"""Optional TypeSafe Jev reranking (experimental; not verified with the real API).

实验性，未经真实 API 验证；仅显式外发模式启用，并限制调用费用。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
import json
import logging
import math
import queue
import threading
from typing import TYPE_CHECKING, Any

from .base import RerankCandidate, RerankError, score_candidates
from .pipeline import _warn_once

if TYPE_CHECKING:
    from linkloom.retrieval_v2.models import RetrievedEvidence

_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_PRICE = Decimal("0.042") / Decimal(1_000_000)
# Reserve a full request context for each of at most 20 scored questions.
# This also covers providers that account shared state once per question.
_MAX_REQUEST_INPUT_TOKENS = 65_536 * 20
_PRICED_MODELS = {"jev-1.14.0", "jev-1.13.0"}


class JevReranker:
    backend = "jev"
    enabled = True

    def __init__(self, api_key: str, *, model: str = "jev-1.14.0", timeout_s: float = 3.0,
                 budget_usd: float | Decimal | None = None,
                 prepare: Callable[[str, Sequence[RerankCandidate]], tuple[str, list[str]]] | None = None,
                 transport: Callable[..., dict[str, Any]] | None = None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise RerankError("missing_key")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        self._key, self.model, self.timeout_s = api_key.strip(), model, float(timeout_s)
        self._prepare, self._transport = prepare, transport
        self._budget = None if budget_usd is None else Decimal(str(budget_usd))
        if self._budget is not None and (not self._budget.is_finite() or self._budget <= 0):
            raise ValueError("budget_usd must be positive and finite")
        self._lock = threading.Lock()
        self._request_count = self._input_tokens = 0
        self._cost = self._reserved = Decimal(0)
        self._uncertain = False
        self._inflight_done: threading.Event | None = None
        self._cancel_active: threading.Event | None = None
        self.last_usage: dict[str, Any] = {}
        self.last_trace: dict[str, Any] = {}
        _warn_once("jev_outbound", "LINKLOOM_RERANK=jev: queries and up to 20 truncated source passages may be sent outside this device to TypeSafe. Only jev mode sends data externally.", logging.getLogger(__name__))

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def cost_usd(self) -> float:
        return float(self._cost)

    def accounting(self) -> dict[str, Any]:
        with self._lock:
            return {"request_count": self._request_count, "input_tokens": self._input_tokens,
                    "cost_usd": float(self._cost), "reserved_cost_usd": float(self._reserved),
                    "upper_bound_cost_usd": float(self._cost + self._reserved),
                    "accounting_uncertain": self._uncertain,
                    "input_usd_per_million_tokens": 0.042,
                    "budget_usd": None if self._budget is None else float(self._budget),
                    "cost_basis": "API input_tokens at the published versioned-model price; failed requests retain a conservative context-ceiling reservation."}

    def _reserve(self) -> Decimal:
        amount = _MAX_REQUEST_INPUT_TOKENS * _PRICE
        with self._lock:
            if self._budget is not None:
                if self.model not in _PRICED_MODELS:
                    raise RerankError("unpriced_model")
                if self._uncertain:
                    raise RerankError("accounting_uncertain")
                if self._cost + self._reserved + amount > self._budget:
                    raise RerankError("budget_exhausted")
            self._reserved += amount
        return amount

    def _settle(self, payload: dict[str, Any], reserved: Decimal) -> None:
        usage = payload.get("usage")
        count = usage.get("input_tokens") if isinstance(usage, dict) else None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > _MAX_REQUEST_INPUT_TOKENS:
            with self._lock:
                self._uncertain = True
            self.last_usage = {"status": "unavailable", "reserved_cost_usd": float(reserved)}
            if self._budget is not None:
                raise RerankError("usage_unavailable")
            return
        cost = count * _PRICE
        with self._lock:
            self._reserved -= reserved
            self._input_tokens += count
            self._cost += cost
        self.last_usage = {"status": "reported", "input_tokens": count, "cost_usd": float(cost)}

    def _request(self, payload: dict[str, Any], reserved: Decimal) -> dict[str, Any]:
        result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        started = threading.Event()
        cancelled = threading.Event()
        finished = threading.Event()
        with self._lock:
            if self._inflight_done is not None and not self._inflight_done.is_set():
                self._reserved -= reserved
                raise RerankError("request_in_progress")
            self._inflight_done = finished
            self._cancel_active = cancelled
        client_holder: list[Any] = []

        def send() -> None:
            ok = False
            response: Any = None
            try:
                if self._transport is None:
                    try:
                        import httpx
                    except ImportError as error:
                        raise RerankError("missing_dependency") from error
                    client = httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=min(1.0, self.timeout_s)), follow_redirects=False)
                    client_holder.append(client)
                if cancelled.is_set():
                    raise RerankError("request_timeout")
                with self._lock:
                    self._request_count += 1
                started.set()
                headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
                if self._transport is not None:
                    response = self._transport(payload, headers=headers, timeout=self.timeout_s)
                else:
                    try:
                        with client:
                            raw = client.post(_ENDPOINT, json=payload, headers=headers)
                            if raw.status_code != 200:
                                raise RerankError(f"http_{raw.status_code}")
                            if len(raw.content) > 1_048_576:
                                raise RerankError("invalid_response")
                            response = raw.json()
                    except httpx.TimeoutException as error:
                        raise RerankError("request_timeout") from error
                    except httpx.HTTPError as error:
                        raise RerankError("request_failed") from error
                if not isinstance(response, dict):
                    raise RerankError("invalid_response")
                ok = True
            except Exception as error:
                response = error
            finally:
                finished.set()
            result.put((ok, response))

        thread = threading.Thread(target=send, name="linkloom-jev-request", daemon=True)
        thread.start()
        try:
            ok, response = result.get(timeout=self.timeout_s)
        except queue.Empty as error:
            cancelled.set()
            def close_clients() -> None:
                for client in client_holder:
                    try:
                        client.close()
                    except Exception:
                        pass
            threading.Thread(target=close_clients, name="linkloom-jev-cancel", daemon=True).start()
            with self._lock:
                self._uncertain = True
            raise RerankError("request_timeout") from error
        if not ok:
            if not started.is_set():
                with self._lock:
                    self._reserved -= reserved
            else:
                with self._lock:
                    self._uncertain = True
            if isinstance(response, RerankError):
                raise response
            raise RerankError("request_failed") from response
        return response

    def rerank(self, query: str, candidates: Sequence[RerankCandidate]) -> list[RetrievedEvidence]:
        if not candidates or not query.strip():
            return [candidate.evidence for candidate in candidates]
        if len(candidates) > 20:
            raise RerankError("candidate_limit")
        prepare = self._prepare
        if prepare is None:
            from .local import prepare_jev_inputs
            prepare = prepare_jev_inputs
        prepared_query, texts = prepare(query, candidates)
        if not isinstance(prepared_query, str) or not prepared_query.strip() or len(texts) != len(candidates) or any(not isinstance(text, str) for text in texts):
            raise RerankError("input_budget_unavailable")
        labels = [f"c{index:02d}" for index in range(len(candidates))]
        payload = {"model": self.model, "state": {"query": prepared_query, "candidates": dict(zip(labels, texts))},
                   "questions": {label: {"type": "noul", "instructions": f"Does candidate {label} directly help answer the query? Consider only {label}, including its project and temporal scope.",
                    "criteria": {"true": "The passage supplies evidence needed to answer the specific query.", "false": "It is only topically similar or contradicts the requested project/time scope."}} for label in labels}}
        # It also bounds accidental request growth before the network call.
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 131_072:
            raise RerankError("input_budget_unavailable")
        reserved = self._reserve()
        response = self._request(payload, reserved)
        self._settle(response, reserved)
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(labels):
            raise RerankError("invalid_response")
        scores = []
        for label in labels:
            answer = answers[label]
            if not isinstance(answer, dict) or answer.get("type") != "noul":
                raise RerankError("invalid_response")
            value = answer.get("noul")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise RerankError("invalid_response")
            scores.append(float(value))
        actual_model = response.get("model")
        if not isinstance(actual_model, str) or not actual_model.startswith("jev-"):
            raise RerankError("invalid_response")
        self.last_trace = {"model": actual_model, "device": "remote", "request_count": self.request_count}
        return score_candidates(candidates, scores, backend="jev")

    def close(self) -> None:
        if self._cancel_active is not None:
            self._cancel_active.set()
