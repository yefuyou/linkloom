"""Current, lexical, dense, hybrid, and directory-aware retrievers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import replace
import logging
import math
import re
import time
from typing import TYPE_CHECKING, Protocol

from linkloom.indexing import BM25Index, DirectoryIndex, VectorIndex
from linkloom.indexing.bm25 import tokenize_for_index
from linkloom.indexing.models import IndexSearchResult
from linkloom.retrieval import retrieve_evidence
from linkloom.schemas import NoteDocument

from .models import RetrievedEvidence, RetrievalQuery

if TYPE_CHECKING:
    from linkloom.rerank.base import Reranker

_LOGGER = logging.getLogger(__name__)


class Retriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]: ...


class CurrentRetriever:
    """Document-ranked adapter over the unchanged legacy line retriever."""

    def __init__(self, document_provider: Callable[[str], list[NoteDocument]]) -> None:
        self._document_provider = document_provider

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        documents = self._document_provider(query.workspace_id)
        if query.optional_scope_path:
            scope = query.optional_scope_path.strip("/")
            if scope:
                documents = [
                    document
                    for document in documents
                    if document.relative_path == scope
                    or document.relative_path.startswith(f"{scope}/")
                ]
        line_hits = retrieve_evidence(
            query.query,
            documents,
            max_results=max(query.top_k * 20, query.top_k),
            workspace_id=query.workspace_id,
        )
        results: list[RetrievedEvidence] = []
        seen_paths: set[str] = set()
        for line_hit in line_hits:
            if line_hit.relative_path in seen_paths:
                continue
            seen_paths.add(line_hit.relative_path)
            results.append(
                RetrievedEvidence(
                    evidence_id=line_hit.evidence_id,
                    source_ref=line_hit.relative_path,
                    logical_path=f"/{line_hit.relative_path}",
                    score=1.0 / (len(results) + 1),
                    rank=len(results) + 1,
                    retrieval_channel="current",
                    resource_id=line_hit.relative_path,
                    metadata={
                        "content_sha256": line_hit.content_sha256,
                        "line_start": line_hit.line_start,
                        "line_end": line_hit.line_end,
                        "quote": line_hit.quote,
                        "source_kind": line_hit.source_kind,
                    },
                )
            )
            if len(results) >= query.top_k:
                break
        return results


class BM25Retriever:
    def __init__(self, index: BM25Index) -> None:
        self.index = index
        self.last_search_ms = 0.0

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        started = time.perf_counter()
        hits = self.index.search(
            query.query,
            workspace_id=query.workspace_id,
            top_k=query.top_k,
            scope_path=query.optional_scope_path,
        )
        results = _map_index_hits(hits, "bm25")
        self.last_search_ms = (time.perf_counter() - started) * 1000.0
        return results


class DenseRetriever:
    def __init__(self, index: VectorIndex) -> None:
        self.index = index
        self.last_search_ms = 0.0

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        started = time.perf_counter()
        hits = self.index.search(
            query.query,
            workspace_id=query.workspace_id,
            top_k=query.top_k,
            scope_path=query.optional_scope_path,
        )
        results = _map_index_hits(hits, "dense")
        self.last_search_ms = (time.perf_counter() - started) * 1000.0
        return results


class HybridRetriever:
    """Fuse the top 20 BM25 and dense candidates with fixed RRF k=60."""

    candidate_k = 20
    rrf_k = 60

    def __init__(
        self,
        lexical: Retriever,
        dense: Retriever,
        *,
        reranker: Reranker | None = None,
        candidate_text_provider: Callable[[RetrievedEvidence], str] | None = None,
    ) -> None:
        # The component is configuration-neutral. Product wiring opts into the
        # environment-configured pipeline; legacy experiments remain pure RRF.
        if reranker is None:
            from linkloom.rerank.base import NoopReranker

            reranker = NoopReranker()
        self.lexical = lexical
        self.dense = dense
        self.reranker = reranker
        self._candidate_text_provider = candidate_text_provider
        self.last_stage_timings_ms: dict[str, float] = {}
        self.last_rerank_trace: dict[str, object] = {}

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        candidate_query = replace(query, top_k=self.candidate_k)
        bm25_started = time.perf_counter()
        bm25_hits = self.lexical.retrieve(candidate_query)
        bm25_ms = (time.perf_counter() - bm25_started) * 1000.0
        dense_started = time.perf_counter()
        dense_hits = self.dense.retrieve(candidate_query)
        dense_total_ms = (time.perf_counter() - dense_started) * 1000.0
        vector_index = getattr(self.dense, "index", None)
        embedding_query_ms = float(getattr(vector_index, "last_query_embedding_ms", 0.0))
        embedding_model_load_ms = float(
            getattr(vector_index, "last_query_model_load_ms", 0.0)
        )
        channel_hits = {"bm25": bm25_hits, "dense": dense_hits}
        fusion_started = time.perf_counter()
        fused: dict[str, dict[str, object]] = {}
        for channel, hits in channel_hits.items():
            for channel_rank, hit in enumerate(hits, start=1):
                identity = hit.evidence_id
                row = fused.setdefault(
                    identity,
                    {"hit": hit, "score": 0.0, "channel_ranks": {}, "channel_scores": {}},
                )
                if row["hit"] is not hit and not _same_identity(row["hit"], hit):
                    raise ValueError(
                        "retrieval channels returned conflicting source identities "
                        f"for evidence_id {identity}"
                    )
                row["score"] = float(row["score"]) + 1.0 / (self.rrf_k + channel_rank)
                row["channel_ranks"][channel] = channel_rank  # type: ignore[index]
                row["channel_scores"][channel] = hit.score  # type: ignore[index]

        ordered = sorted(
            fused.values(),
            key=lambda row: (
                -float(row["score"]),
                min(row["channel_ranks"].values()),  # type: ignore[union-attr]
                row["hit"].logical_path,  # type: ignore[union-attr]
                row["hit"].evidence_id,  # type: ignore[union-attr]
            ),
        )
        results: list[RetrievedEvidence] = []
        for rank, row in enumerate(ordered, start=1):
            hit = row["hit"]
            metadata = dict(hit.metadata)  # type: ignore[union-attr]
            metadata.update(
                {
                    "rrf_k": self.rrf_k,
                    "rrf_rank": rank,
                    "channel_ranks": dict(row["channel_ranks"]),
                    "channel_scores": dict(row["channel_scores"]),
                }
            )
            results.append(
                RetrievedEvidence(
                    evidence_id=hit.evidence_id,  # type: ignore[union-attr]
                    source_ref=hit.source_ref,  # type: ignore[union-attr]
                    logical_path=hit.logical_path,  # type: ignore[union-attr]
                    score=float(row["score"]),
                    rank=rank,
                    retrieval_channel="hybrid",
                    resource_id=hit.resource_id,  # type: ignore[union-attr]
                    metadata=metadata,
                )
            )
        fusion_ms = (time.perf_counter() - fusion_started) * 1000.0
        results, rerank_ms = self._rerank(query.query, results)
        self.last_stage_timings_ms = {
            "bm25_ms": bm25_ms,
            "embedding_query_ms": embedding_query_ms,
            # Keep query-time loading separate from the cold document-index
            # model load recorded by RuntimeRetrievalBackend._ensure_indexes.
            "query_embedding_model_load_ms": embedding_model_load_ms,
            "dense_search_ms": max(
                0.0,
                dense_total_ms - embedding_query_ms - embedding_model_load_ms,
            ),
            "fusion_ms": fusion_ms,
            "rerank_ms": rerank_ms,
        }
        return [replace(hit, rank=rank) for rank, hit in
                enumerate(results[: query.top_k], start=1)]

    def _rerank(
        self, query: str, results: list[RetrievedEvidence]
    ) -> tuple[list[RetrievedEvidence], float]:
        head, tail = results[: self.candidate_k], results[self.candidate_k :]
        mode = str(getattr(self.reranker, "mode", "off"))
        self.last_rerank_trace = {
            "mode": mode, "backend": "noop", "status": "disabled",
            "candidate_count": len(head), "attempts": [], "elapsed_ms": 0.0,
        }
        if not getattr(self.reranker, "enabled", True):
            recorded = getattr(self.reranker, "last_trace", None)
            if isinstance(recorded, dict) and recorded:
                self.last_rerank_trace = deepcopy(recorded)
                self.last_rerank_trace.update(candidate_count=len(head), elapsed_ms=0.0)
            return results, 0.0
        if not head or not query.strip():
            self.last_rerank_trace["status"] = "empty_input"
            return results, 0.0
        from linkloom.rerank.base import RerankCandidate, RerankError

        # Providers and injected rerankers never receive the fallback objects:
        # metadata dictionaries remain mutable even in frozen evidence records.
        baseline = head
        started = time.perf_counter()
        try:
            candidates = []
            for hit in deepcopy(head):
                text = (self._candidate_text_provider(hit)
                        if self._candidate_text_provider is not None
                        else hit.metadata.get("content"))
                if not isinstance(text, str) or not text.strip():
                    raise RerankError("candidate_text_unavailable")
                candidates.append(RerankCandidate(evidence=hit, text=text))
            reranked = self.reranker.rerank(query, candidates)
            _validate_reranked(baseline, reranked)
            recorded = getattr(self.reranker, "last_trace", None)
            self.last_rerank_trace = deepcopy(recorded) if isinstance(recorded, dict) else {
                "mode": mode, "backend": type(self.reranker).__name__,
                "status": "ok", "candidate_count": len(head), "attempts": [],
            }
            result = deepcopy(reranked) + tail
        except Exception as error:
            reason = getattr(error, "reason", None)
            if not isinstance(reason, str) or re.fullmatch(r"[a-z][a-z0-9_]*", reason) is None:
                reason = "invalid_result" if isinstance(error, ValueError) else "rerank_failed"
            self.last_rerank_trace.update({
                "status": "fallback", "reason": reason,
                "attempts": [{"backend": type(self.reranker).__name__,
                              "status": "failed", "reason": reason}],
            })
            try:
                _LOGGER.warning("Document reranking degraded to RRF (%s)", reason)
            except Exception:
                pass  # A broken optional logging sink cannot fail retrieval.
            result = baseline + tail
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.last_rerank_trace["elapsed_ms"] = elapsed_ms
        return result, elapsed_ms


class DirectoryAwareHybridRetriever:
    """Optionally pick one directory from L0/L1 metadata before document fusion."""

    def __init__(
        self,
        hybrid: HybridRetriever,
        directories: DirectoryIndex,
        *,
        enabled: bool = False,
    ) -> None:
        self.hybrid = hybrid
        self.directories = directories
        self.enabled = enabled
        self.last_stage_timings_ms: dict[str, float] = {}
        self.last_rerank_trace: dict[str, object] = {}

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        directory_started = time.perf_counter()
        scope = None
        if not self.enabled or query.optional_scope_path is not None:
            directory_ms = (time.perf_counter() - directory_started) * 1000.0
            hits = self.hybrid.retrieve(query)
        else:
            scope = self._select_scope(query)
            directory_ms = (time.perf_counter() - directory_started) * 1000.0
            if scope is None:
                hits = self.hybrid.retrieve(query)
            else:
                hits = self.hybrid.retrieve(replace(query, optional_scope_path=scope))
        self.last_rerank_trace = deepcopy(self.hybrid.last_rerank_trace)
        self.last_stage_timings_ms = {
            **self.hybrid.last_stage_timings_ms,
            "directory_ms": directory_ms,
        }
        if scope is None:
            return hits
        return [
            replace(hit, metadata={**hit.metadata, "directory_scope": scope})
            for hit in hits
        ]

    def _select_scope(self, query: RetrievalQuery) -> str | None:
        query_tokens = set(tokenize_for_index(query.query))
        ranked: list[tuple[int, str]] = []
        for node in self.directories.list_nodes(query.workspace_id):
            if not node.contained_documents:
                continue
            summary_tokens = set(tokenize_for_index(f"{node.path} {node.abstract} {node.overview}"))
            overlap = len(query_tokens & summary_tokens)
            if overlap:
                ranked.append((overlap, node.path))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][1] if ranked else None


class ProgressiveContextLoader:
    """Explicit L0/L1/L2 access so callers choose when original content is loaded."""

    def __init__(self, directories: DirectoryIndex) -> None:
        self.directories = directories

    def load_abstract(self, workspace_id: str, path: str) -> str:
        node = self._require_node(workspace_id, path)
        return node.abstract

    def load_overview(self, workspace_id: str, path: str) -> str:
        node = self._require_node(workspace_id, path)
        return node.overview

    def load_original(self, workspace_id: str, resource_id: str):
        document = self.directories.get_document(workspace_id, resource_id)
        if document is None:
            raise KeyError(f"unknown context resource: {workspace_id}/{resource_id}")
        return document

    def _require_node(self, workspace_id: str, path: str):
        node = self.directories.get_node(workspace_id, path)
        if node is None:
            raise KeyError(f"unknown context directory: {workspace_id}{path}")
        return node


def _map_index_hits(
    hits: Sequence[IndexSearchResult],
    channel: str,
) -> list[RetrievedEvidence]:
    return [
        RetrievedEvidence(
            evidence_id=hit.evidence_id,
            source_ref=hit.source_ref,
            logical_path=hit.logical_path,
            score=hit.score,
            rank=rank,
            retrieval_channel=channel,
            resource_id=hit.resource_id,
            metadata={**hit.metadata, "workspace_id": hit.workspace_id},
        )
        for rank, hit in enumerate(hits, start=1)
    ]


def _same_identity(left: RetrievedEvidence, right: RetrievedEvidence) -> bool:
    """Whether duplicate IDs from channels refer to the same immutable item."""
    identity_fields = (
        "workspace_id",
        "content_hash",
        "content_sha256",
        "line_start",
        "line_end",
        "quote_sha256",
    )
    return (
        left.source_ref == right.source_ref
        and left.logical_path == right.logical_path
        and left.resource_id == right.resource_id
        and all(left.metadata.get(key) == right.metadata.get(key) for key in identity_fields)
    )


def _validate_reranked(
    original: list[RetrievedEvidence], result: list[RetrievedEvidence]
) -> None:
    """Reject incomplete batches and any attempt to replace source evidence."""
    if not isinstance(result, list) or len(result) != len(original):
        raise ValueError("invalid rerank batch")
    expected = {hit.evidence_id: hit for hit in original}
    if any(not isinstance(hit, RetrievedEvidence) for hit in result):
        raise ValueError("invalid rerank evidence")
    if len({hit.evidence_id for hit in result}) != len(expected):
        raise ValueError("duplicate rerank evidence")
    scores = [hit.rerank_score for hit in result]
    if all(score is None for score in scores):
        if [hit.evidence_id for hit in result] != [hit.evidence_id for hit in original]:
            raise ValueError("unscored rerank order")
    elif any(isinstance(score, bool) or not isinstance(score, (int, float))
             or not math.isfinite(score) for score in scores):
        raise ValueError("invalid rerank score")
    for hit in result:
        previous = expected.get(hit.evidence_id)
        if previous is None or (
            not _same_identity(previous, hit)
            or hit.score != previous.score
            or hit.retrieval_channel != previous.retrieval_channel
            or any(key not in hit.metadata or hit.metadata[key] != value
                   for key, value in previous.metadata.items())
        ):
            raise ValueError("rerank source changed")
