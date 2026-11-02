"""Explicit local-only defaults and recoverable, whole-batch rerank fallback."""
from __future__ import annotations

from collections.abc import Sequence
import copy
from dataclasses import fields, replace
import logging
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from .base import NoopReranker, RerankCandidate, RerankError, Reranker, finite_score, score_candidates

if TYPE_CHECKING:
    from linkloom.retrieval_v2.models import RetrievedEvidence

_LOG = logging.getLogger(__name__)
_WARNED: set[str] = set()
_WARN_LOCK = threading.Lock()


def _warn_once(code: str, message: str, logger: logging.Logger) -> None:
    with _WARN_LOCK:
        if code in _WARNED:
            return
        _WARNED.add(code)
    try:
        logger.warning(message)
    except Exception:
        pass


def _reason(error: Exception) -> str:
    reason = getattr(error, "reason", None)
    if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
        return reason
    return "missing_dependency" if isinstance(error, (ImportError, ModuleNotFoundError)) else "inference_failed"


def _validate_result(candidates: Sequence[RerankCandidate], result: object, backend: str) -> list[RetrievedEvidence]:
    if not isinstance(result, (list, tuple)) or len(result) != len(candidates):
        raise RerankError("invalid_scores")
    originals = {candidate.evidence.evidence_id: candidate.evidence for candidate in candidates}
    if len(originals) != len(candidates):
        raise RerankError("invalid_scores")
    received: dict[str, Any] = {}
    for item in result:
        identity = getattr(item, "evidence_id", None)
        if identity not in originals or identity in received:
            raise RerankError("invalid_scores")
        original = originals[identity]
        for field in fields(original):
            if field.name not in {"rank", "metadata", "rerank_score"} and getattr(item, field.name, object()) != getattr(original, field.name):
                raise RerankError("invalid_scores")
        if not isinstance(getattr(item, "metadata", None), dict):
            raise RerankError("invalid_scores")
        if any(key not in item.metadata or item.metadata[key] != value for key, value in original.metadata.items()):
            raise RerankError("invalid_scores")
        finite_score(getattr(item, "rerank_score", None))
        received[identity] = item
    # Rebuild from trusted original objects. Provider additions cannot replace evidence.
    return score_candidates(candidates, [received[c.evidence.evidence_id].rerank_score for c in candidates], backend=backend)


class RerankPipeline:
    """auto/local never construct or invoke a remote backend, even with a key."""

    def __init__(self, mode: str = "auto", *, local: Reranker | None = None, jev: Reranker | None = None,
                 logger: logging.Logger | None = None) -> None:
        normalized = mode.strip().casefold() if isinstance(mode, str) else ""
        self.mode = normalized if normalized in {"auto", "local", "jev", "off"} else "off"
        self.enabled = self.mode != "off"
        self._invalid_mode = normalized not in {"auto", "local", "jev", "off"}
        self._local, self._jev = local, jev
        self._owns_local = local is not None
        self._logger = logger or _LOG
        self.last_trace: dict[str, Any] = {"mode": self.mode, "backend": "noop", "status": "fallback" if self._invalid_mode else "off" if not self.enabled else "not_attempted", "candidate_count": 0, "attempts": [], "elapsed_ms": 0.0}
        if self._invalid_mode:
            self.last_trace["reason"] = "invalid_mode"
        if self.mode == "jev":
            _warn_once("jev_outbound", "LINKLOOM_RERANK=jev: queries and up to 20 truncated source passages may be sent outside this device to TypeSafe. Only jev mode sends data externally.", self._logger)

    def _backend(self) -> Reranker:
        if self.mode in {"auto", "local"}:
            if self._local is None:
                from .local import get_default_local_reranker
                self._local = get_default_local_reranker()
            return self._local
        if self._jev is None:
            # Credentials are deliberately read only inside explicit jev mode.
            key = os.environ.get("LINKLOOM_JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
            if not key or not key.strip():
                raise RerankError("missing_key")
            from .jev import JevReranker
            self._jev = JevReranker(key, model=os.environ.get("LINKLOOM_JEV_MODEL", "jev-1.14.0"))
        return self._jev

    def rerank(self, query: str, candidates: Sequence[RerankCandidate]) -> list[RetrievedEvidence]:
        started = time.perf_counter()
        baseline = NoopReranker().rerank(query, candidates)
        trace: dict[str, Any] = {"mode": self.mode, "backend": "noop", "status": "off", "candidate_count": len(candidates), "attempts": []}
        try:
            if not self.enabled:
                if self._invalid_mode:
                    trace.update(status="fallback", reason="invalid_mode")
                return baseline
            if not candidates or not query.strip():
                trace["status"] = "empty_input"
                return baseline
            backend_name = "jev" if self.mode == "jev" else "local"
            attempt_started = time.perf_counter()
            try:
                backend = self._backend()
                # Backends receive independent metadata dictionaries, not mutable originals.
                private_candidates = [RerankCandidate(replace(c.evidence, metadata=copy.deepcopy(c.evidence.metadata)), c.text) for c in candidates]
                result = backend.rerank(query, private_candidates)
                ranked = _validate_result(candidates, result, backend_name)
                attempt = {"backend": backend_name, "status": "success", "elapsed_ms": (time.perf_counter() - attempt_started) * 1000}
                detail = getattr(backend, "last_trace", None)
                if isinstance(detail, dict):
                    # Only backend-controlled diagnostic fields are exported, never text or credentials.
                    for key in ("model", "revision", "device", "token_counts", "truncated_count", "first_load_ms", "attempts"):
                        if key in detail:
                            attempt[key] = detail[key]
                trace.update(backend=backend_name, status="success", attempts=[attempt])
                return ranked
            except Exception as error:
                reason = _reason(error)
                trace.update(status="fallback", reason=reason, attempts=[{"backend": backend_name, "status": "failed", "reason": reason, "error_type": type(error).__name__, "elapsed_ms": (time.perf_counter() - attempt_started) * 1000}, {"backend": "noop", "status": "success", "elapsed_ms": 0.0}])
                if self.mode == "local" and reason == "missing_dependency":
                    _warn_once("local_missing_dependency", "Local reranking is unavailable: install linkloom[rerank] to enable it. Retrieval continues in the original RRF order; no data is sent externally.", self._logger)
                return baseline
        finally:
            trace["elapsed_ms"] = max(0.0, (time.perf_counter() - started) * 1000)
            self.last_trace = trace
            try:
                self._logger.info("retrieval_rerank", extra={"rerank_trace": trace})
            except Exception:
                pass

    def close(self) -> None:
        for backend in (self._local if self._owns_local else None, self._jev):
            close = getattr(backend, "close", None)
            if callable(close):
                close()


def build_reranker_from_env() -> RerankPipeline:
    return RerankPipeline(os.environ.get("LINKLOOM_RERANK", "auto"))
