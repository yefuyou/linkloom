"""Verified-note adapter that makes Retrieval V2 usable by Runtime V2."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from linkloom.evidence_identity import document_identity_id
from linkloom.indexing import (
    BM25Index,
    DirectoryIndex,
    EmbeddingProvider,
    IndexUpdateCoordinator,
    IndexDocument,
    SentenceTransformerEmbedder,
    VectorIndex,
)
from linkloom.retrieval import retrieve_evidence
from linkloom.schemas import NoteDocument

from .models import RetrievalQuery
from .retrievers import (
    BM25Retriever,
    DenseRetriever,
    DirectoryAwareHybridRetriever,
    HybridRetriever,
    Retriever,
)


class RetrievalMode(StrEnum):
    CURRENT = "current"
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"
    DIRECTORY_HYBRID = "directory_hybrid"

    @classmethod
    def parse(cls, value: str | RetrievalMode) -> RetrievalMode:
        if isinstance(value, cls):
            return value
        try:
            return cls(value.strip().casefold())
        except (AttributeError, ValueError) as error:
            raise ValueError(
                f"retrieval mode must be one of: {', '.join(item.value for item in cls)}"
            ) from error


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    retrieval_mode: str
    scope_path: str | None
    retrieval_latency_ms: float
    candidate_count: int
    top_k: int
    index_version: int
    stage_timings_ms: dict[str, float]


@dataclass(frozen=True, slots=True)
class RuntimeRetrievalResult:
    evidence: list[dict[str, Any]]
    observation: RetrievalObservation


_DEFAULT_RUNTIME_EMBEDDER = SentenceTransformerEmbedder()


class RuntimeRetrievalBackend:
    """Rank verified documents with V2, then expose exact source evidence lines."""

    def __init__(
        self,
        *,
        workspace_id: str,
        document_provider: Callable[[], list[NoteDocument]],
        mode: str | RetrievalMode = RetrievalMode.HYBRID,
        embedder: EmbeddingProvider | None = None,
        index_update_coordinator: IndexUpdateCoordinator | None = None,
        index_version: int = 2,
    ) -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if not callable(document_provider):
            raise ValueError("document_provider must be callable")
        if index_version <= 0:
            raise ValueError("index_version must be positive")
        self.workspace_id = workspace_id
        self.document_provider = document_provider
        self.mode = RetrievalMode.parse(mode)
        if index_update_coordinator is not None and not isinstance(
            index_update_coordinator,
            IndexUpdateCoordinator,
        ):
            raise ValueError("index_update_coordinator must be an IndexUpdateCoordinator or None")
        self._index_update_coordinator = index_update_coordinator
        self.index_version = (
            index_update_coordinator.index_version
            if index_update_coordinator is not None
            else index_version
        )
        self._embedder = embedder or _DEFAULT_RUNTIME_EMBEDDER
        self._fingerprint: tuple[tuple[str, str], ...] | None = None
        self._retrievers: dict[RetrievalMode, Retriever] = {}
        self._documents_by_id: dict[str, NoteDocument] = {}
        self._indexed_documents: dict[str, IndexDocument] = {}
        self._lexical_index: BM25Index | None = None
        self._vector_index: VectorIndex | None = None
        self._directory_index: DirectoryIndex | None = None

    def search(
        self,
        query: str,
        *,
        top_k: int,
        scope_path: str | None = None,
    ) -> RuntimeRetrievalResult:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        started = time.perf_counter()
        stage_timings = {
            "document_scan_ms": 0.0,
            "index_setup_other_ms": 0.0,
            "bm25_index_build_ms": 0.0,
            "directory_index_build_ms": 0.0,
            "directory_ms": 0.0,
            "bm25_ms": 0.0,
            "embedding_model_load_ms": 0.0,
            "document_embedding_ms": 0.0,
            "vector_normalization_ms": 0.0,
            "embedding_query_ms": 0.0,
            "query_embedding_model_load_ms": 0.0,
            "dense_search_ms": 0.0,
            "fusion_ms": 0.0,
            "retriever_other_ms": 0.0,
            "materialization_ms": 0.0,
        }
        scan_started = time.perf_counter()
        documents = self.document_provider()
        stage_timings["document_scan_ms"] = (time.perf_counter() - scan_started) * 1000.0
        index_setup_started = time.perf_counter()
        index_setup_cpu_started = time.process_time()
        index_timings = self._ensure_indexes(
            documents,
            caller_started=index_setup_started,
        )
        index_setup_call_returned = time.perf_counter()
        index_setup_call_ms = (
            index_setup_call_returned - index_setup_started
        ) * 1000.0
        index_setup_cpu_ms = (time.process_time() - index_setup_cpu_started) * 1000.0
        index_setup_body_ms = float(index_timings.get("index_setup_total_ms", 0.0))
        stage_timings.update(index_timings)
        stage_timings["index_setup_merge_ms"] = (
            time.perf_counter() - index_setup_call_returned
        ) * 1000.0
        stage_timings["index_setup_return_gap_ms"] = max(
            0.0,
            index_setup_call_ms - index_setup_body_ms,
        )
        stage_timings["index_setup_cpu_ms"] = index_setup_cpu_ms
        stage_timings["index_setup_total_ms"] = (
            index_setup_call_ms + stage_timings["index_setup_merge_ms"]
        )

        if self.mode is RetrievalMode.CURRENT:
            materialization_started = time.perf_counter()
            scoped_documents = _scope_documents(documents, scope_path)
            line_hits = retrieve_evidence(
                query,
                scoped_documents,
                max_results=top_k,
                workspace_id=self.workspace_id,
            )
            evidence = [
                self._decorate_evidence(
                    item.to_dict(),
                    document_rank=index + 1,
                    document_score=1.0 / (index + 1),
                    retrieval_channel="current",
                    scope_path=scope_path,
                )
                for index, item in enumerate(line_hits)
            ]
            candidate_count = len({item.relative_path for item in line_hits})
            effective_scope = scope_path
            stage_timings["materialization_ms"] = (
                time.perf_counter() - materialization_started
            ) * 1000.0
        else:
            candidate_limit = max(top_k, HybridRetriever.candidate_k)
            retriever = self._retrievers[self.mode]
            retriever_started = time.perf_counter()
            hits = retriever.retrieve(
                RetrievalQuery(
                    workspace_id=self.workspace_id,
                    query=query,
                    top_k=candidate_limit,
                    optional_scope_path=scope_path,
                )
            )
            retriever_timings = _retriever_stage_timings(retriever)
            stage_timings.update(retriever_timings)
            retriever_total_ms = (time.perf_counter() - retriever_started) * 1000.0
            stage_timings["retriever_total_ms"] = retriever_total_ms
            stage_timings["retriever_other_ms"] = max(
                0.0,
                retriever_total_ms - sum(retriever_timings.values()),
            )
            candidate_count = len(hits)
            effective_scope = scope_path
            if effective_scope is None and hits:
                directory_scope = hits[0].metadata.get("directory_scope")
                if isinstance(directory_scope, str):
                    effective_scope = directory_scope
            evidence = []
            materialization_started = time.perf_counter()
            for hit in hits:
                document = self._documents_by_id.get(hit.resource_id)
                if document is None:
                    continue
                if self._index_update_coordinator is not None:
                    manifest_resource = self._index_update_coordinator.manifest.get_resource(
                        self.workspace_id,
                        hit.resource_id,
                    )
                    if (
                        manifest_resource is None
                        or not manifest_resource.active
                        or manifest_resource.content_hash != document.content_sha256
                    ):
                        # A fresh source scan does not make stale index entries
                        # safe to expose. Wait for the event coordinator or
                        # reconciliation to advance the manifest and indexes.
                        continue
                line_hits = retrieve_evidence(
                    query,
                    [document],
                    max_results=1,
                    workspace_id=self.workspace_id,
                )
                if not line_hits:
                    continue
                evidence.append(
                    self._decorate_evidence(
                        line_hits[0].to_dict(),
                        document_rank=hit.rank,
                        document_score=hit.score,
                        retrieval_channel=hit.retrieval_channel,
                        scope_path=effective_scope,
                        logical_path=hit.logical_path,
                    )
                )
                if len(evidence) >= top_k:
                    break
            stage_timings["materialization_ms"] = (
                time.perf_counter() - materialization_started
            ) * 1000.0

        total_ms = (time.perf_counter() - started) * 1000.0
        diagnostic_totals = {
            "index_setup_total_ms",
            "index_setup_internal_total_ms",
            "index_setup_measured_components_ms",
            "index_setup_cpu_ms",
            "retriever_total_ms",
            "total_retrieval_ms",
        }
        stage_timings["other_ms"] = max(
            0.0,
            total_ms
            - sum(
                value
                for key, value in stage_timings.items()
                if key not in diagnostic_totals
            ),
        )
        stage_timings["total_retrieval_ms"] = total_ms
        observation = RetrievalObservation(
            retrieval_mode=self.mode.value,
            scope_path=effective_scope,
            retrieval_latency_ms=total_ms,
            candidate_count=candidate_count,
            top_k=top_k,
            index_version=self.index_version,
            stage_timings_ms=stage_timings,
        )
        return RuntimeRetrievalResult(evidence=evidence, observation=observation)

    def _ensure_indexes(
        self,
        documents: list[NoteDocument],
        *,
        caller_started: float | None = None,
    ) -> dict[str, float]:
        started = time.perf_counter()
        entry_gap_ms = (
            max(0.0, (started - caller_started) * 1000.0)
            if caller_started is not None
            else 0.0
        )
        timings = {
            "index_setup_other_ms": 0.0,
            "index_setup_entry_gap_ms": entry_gap_ms,
            "bm25_index_build_ms": 0.0,
            "directory_index_build_ms": 0.0,
            "embedding_model_load_ms": 0.0,
            "document_embedding_ms": 0.0,
            "vector_normalization_ms": 0.0,
        }
        fingerprint = tuple(
            sorted((document.relative_path, document.content_sha256) for document in documents)
        )
        if self._index_update_coordinator is not None and self.mode is not RetrievalMode.CURRENT:
            # The event coordinator owns index mutation in this mode. The
            # runtime adapter refreshes its verified source-document map, but
            # never silently rebuilds or repairs the shared indexes; missed
            # events are handled by the explicit reconciliation job.
            self._documents_by_id = {
                document.relative_path: document for document in documents
            }
            attach_started = time.perf_counter()
            self._attach_coordinated_indexes()
            self._fingerprint = fingerprint
            timings["index_setup_other_ms"] = (
                time.perf_counter() - attach_started
            ) * 1000.0
            timings["index_setup_total_ms"] = (
                entry_gap_ms + timings["index_setup_other_ms"]
            )
            return timings
        if fingerprint == self._fingerprint:
            timings["index_setup_total_ms"] = entry_gap_ms + (
                time.perf_counter() - started
            ) * 1000.0
            return timings

        index_documents = {
            document.relative_path: IndexDocument(
                workspace_id=self.workspace_id,
                resource_id=document.relative_path,
                evidence_id=document_identity_id(
                    workspace_id=self.workspace_id,
                    resource_id=document.relative_path,
                    document_id=document.relative_path,
                    logical_path=f"/{document.relative_path}",
                    source_ref=document.relative_path,
                    content_hash=document.content_sha256,
                ),
                logical_path=f"/{document.relative_path}",
                content=f"{document.title}\n{document.content}",
                source_ref=document.relative_path,
                metadata={
                    "title": document.title,
                    "content_sha256": document.content_sha256,
                },
            )
            for document in documents
        }
        self._documents_by_id = {
            document.relative_path: document for document in documents
        }

        if self.mode is RetrievalMode.CURRENT:
            self._indexed_documents = index_documents
            self._fingerprint = fingerprint
            timings["index_setup_other_ms"] = (time.perf_counter() - started) * 1000.0
            timings["index_setup_total_ms"] = (
                entry_gap_ms + timings["index_setup_other_ms"]
            )
            return timings

        needs_lexical = self.mode in {
            RetrievalMode.BM25,
            RetrievalMode.HYBRID,
            RetrievalMode.DIRECTORY_HYBRID,
        }
        needs_dense = self.mode in {
            RetrievalMode.DENSE,
            RetrievalMode.HYBRID,
            RetrievalMode.DIRECTORY_HYBRID,
        }
        needs_directory = self.mode is RetrievalMode.DIRECTORY_HYBRID
        if self._fingerprint is None:
            if needs_lexical:
                self._lexical_index = BM25Index()
                index_started = time.perf_counter()
                self._lexical_index.build(index_documents.values())
                timings["bm25_index_build_ms"] += (
                    time.perf_counter() - index_started
                ) * 1000.0
            if needs_dense:
                self._vector_index = VectorIndex(self._embedder)
                self._vector_index.build(index_documents.values())
                timings["embedding_model_load_ms"] += self._vector_index.last_model_load_ms
                timings["document_embedding_ms"] += self._vector_index.last_document_embedding_ms
                timings["vector_normalization_ms"] += self._vector_index.last_normalization_ms
            if needs_directory:
                self._directory_index = DirectoryIndex(index_version=self.index_version)
                index_started = time.perf_counter()
                self._directory_index.build(list(index_documents.values()))
                timings["directory_index_build_ms"] += (
                    time.perf_counter() - index_started
                ) * 1000.0
        else:
            changed_ids = {
                resource_id
                for resource_id, document in index_documents.items()
                if (
                    resource_id not in self._indexed_documents
                    or self._indexed_documents[resource_id].content != document.content
                    or self._indexed_documents[resource_id].logical_path != document.logical_path
                )
            }
            removed_ids = self._indexed_documents.keys() - index_documents.keys()
            for resource_id in removed_ids:
                if self._lexical_index is not None:
                    index_started = time.perf_counter()
                    self._lexical_index.remove(self.workspace_id, resource_id)
                    timings["bm25_index_build_ms"] += (
                        time.perf_counter() - index_started
                    ) * 1000.0
                if self._vector_index is not None:
                    self._vector_index.remove(self.workspace_id, resource_id)
                if self._directory_index is not None:
                    index_started = time.perf_counter()
                    self._directory_index.remove(self.workspace_id, resource_id)
                    timings["directory_index_build_ms"] += (
                        time.perf_counter() - index_started
                    ) * 1000.0
            for resource_id in sorted(changed_ids):
                document = index_documents[resource_id]
                if self._lexical_index is not None:
                    index_started = time.perf_counter()
                    self._lexical_index.upsert(document)
                    timings["bm25_index_build_ms"] += (
                        time.perf_counter() - index_started
                    ) * 1000.0
                if self._vector_index is not None:
                    self._vector_index.upsert(document)
                    timings["embedding_model_load_ms"] += self._vector_index.last_model_load_ms
                    timings["document_embedding_ms"] += self._vector_index.last_document_embedding_ms
                    timings["vector_normalization_ms"] += self._vector_index.last_normalization_ms
                if self._directory_index is not None:
                    index_started = time.perf_counter()
                    self._directory_index.upsert(document)
                    timings["directory_index_build_ms"] += (
                        time.perf_counter() - index_started
                    ) * 1000.0

        if self._directory_index is not None:
            for node in self._directory_index.list_nodes(self.workspace_id):
                if node.dirty_summary:
                    index_started = time.perf_counter()
                    self._directory_index.refresh_summaries(self.workspace_id, node.path)
                    timings["directory_index_build_ms"] += (
                        time.perf_counter() - index_started
                    ) * 1000.0

        if not self._retrievers:
            if self.mode is RetrievalMode.BM25:
                assert self._lexical_index is not None
                self._retrievers[RetrievalMode.BM25] = BM25Retriever(self._lexical_index)
            elif self.mode is RetrievalMode.DENSE:
                assert self._vector_index is not None
                self._retrievers[RetrievalMode.DENSE] = DenseRetriever(self._vector_index)
            else:
                assert self._lexical_index is not None
                assert self._vector_index is not None
                hybrid = HybridRetriever(
                    BM25Retriever(self._lexical_index),
                    DenseRetriever(self._vector_index),
                )
                if self.mode is RetrievalMode.HYBRID:
                    self._retrievers[RetrievalMode.HYBRID] = hybrid
                else:
                    assert self._directory_index is not None
                    self._retrievers[RetrievalMode.DIRECTORY_HYBRID] = (
                        DirectoryAwareHybridRetriever(
                            hybrid,
                            self._directory_index,
                            enabled=True,
                        )
                    )

        self._indexed_documents = index_documents
        self._fingerprint = fingerprint
        measured = sum(
            timings[name]
            for name in (
                "bm25_index_build_ms",
                "directory_index_build_ms",
                "embedding_model_load_ms",
                "document_embedding_ms",
                "vector_normalization_ms",
            )
        )
        body_ms = (time.perf_counter() - started) * 1000.0
        timings["index_setup_measured_components_ms"] = measured
        timings["index_setup_internal_total_ms"] = body_ms
        timings["index_setup_other_ms"] = max(
            0.0,
            body_ms - measured,
        )
        timings["index_setup_total_ms"] = entry_gap_ms + body_ms
        return timings

    def _attach_coordinated_indexes(self) -> None:
        if self._retrievers:
            return
        coordinator = self._index_update_coordinator
        if coordinator is None:
            return
        if self.mode is RetrievalMode.BM25:
            self._retrievers[RetrievalMode.BM25] = BM25Retriever(
                coordinator.lexical_index
            )
            return
        if self.mode is RetrievalMode.DENSE:
            self._retrievers[RetrievalMode.DENSE] = DenseRetriever(
                coordinator.vector_index
            )
            return
        hybrid = HybridRetriever(
            BM25Retriever(coordinator.lexical_index),
            DenseRetriever(coordinator.vector_index),
        )
        if self.mode is RetrievalMode.HYBRID:
            self._retrievers[RetrievalMode.HYBRID] = hybrid
            return
        self._retrievers[RetrievalMode.DIRECTORY_HYBRID] = (
            DirectoryAwareHybridRetriever(
                hybrid,
                coordinator.directory_index,
                enabled=True,
            )
        )

    def _decorate_evidence(
        self,
        value: dict[str, Any],
        *,
        document_rank: int,
        document_score: float,
        retrieval_channel: str,
        scope_path: str | None,
        logical_path: str | None = None,
    ) -> dict[str, Any]:
        return {
            **value,
            "logical_path": logical_path or f"/{value['relative_path']}",
            "rank": document_rank,
            "score": document_score,
            "retrieval_channel": retrieval_channel,
            "retrieval_mode": self.mode.value,
            "scope_path": scope_path,
            "index_version": self.index_version,
            "source_ref": value.get("source_ref") or value["relative_path"],
            "workspace_id": self.workspace_id,
        }


def _scope_documents(
    documents: list[NoteDocument],
    scope_path: str | None,
) -> list[NoteDocument]:
    if scope_path is None:
        return documents
    normalized = scope_path.strip("/")
    if not normalized:
        return documents
    return [
        document
        for document in documents
        if document.relative_path == normalized
        or document.relative_path.startswith(f"{normalized}/")
    ]


def _retriever_stage_timings(retriever: Retriever) -> dict[str, float]:
    recorded = getattr(retriever, "last_stage_timings_ms", None)
    if isinstance(recorded, dict):
        return {key: float(value) for key, value in recorded.items()}
    if isinstance(retriever, BM25Retriever):
        return {"bm25_ms": retriever.last_search_ms}
    if isinstance(retriever, DenseRetriever):
        index = retriever.index
        return {
            "embedding_query_ms": float(index.last_query_embedding_ms),
            "query_embedding_model_load_ms": float(index.last_query_model_load_ms),
            "dense_search_ms": float(index.last_dense_search_ms),
        }
    return {}
