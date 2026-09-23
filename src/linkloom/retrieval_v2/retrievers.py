"""Current, lexical, dense, hybrid, and directory-aware retrievers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Protocol

from linkloom.indexing import BM25Index, DirectoryIndex, VectorIndex
from linkloom.indexing.bm25 import tokenize_for_index
from linkloom.indexing.models import IndexSearchResult
from linkloom.retrieval import retrieve_evidence
from linkloom.schemas import NoteDocument

from .models import RetrievedEvidence, RetrievalQuery


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

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        hits = self.index.search(
            query.query,
            workspace_id=query.workspace_id,
            top_k=query.top_k,
            scope_path=query.optional_scope_path,
        )
        return _map_index_hits(hits, "bm25")


class DenseRetriever:
    def __init__(self, index: VectorIndex) -> None:
        self.index = index

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        hits = self.index.search(
            query.query,
            workspace_id=query.workspace_id,
            top_k=query.top_k,
            scope_path=query.optional_scope_path,
        )
        return _map_index_hits(hits, "dense")


class HybridRetriever:
    """Fuse the top 20 BM25 and dense candidates with fixed RRF k=60."""

    candidate_k = 20
    rrf_k = 60

    def __init__(self, lexical: Retriever, dense: Retriever) -> None:
        self.lexical = lexical
        self.dense = dense

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        candidate_query = replace(query, top_k=self.candidate_k)
        channel_hits = {
            "bm25": self.lexical.retrieve(candidate_query),
            "dense": self.dense.retrieve(candidate_query),
        }
        fused: dict[str, dict[str, object]] = {}
        for channel, hits in channel_hits.items():
            for channel_rank, hit in enumerate(hits, start=1):
                identity = hit.evidence_id
                row = fused.setdefault(
                    identity,
                    {"hit": hit, "score": 0.0, "channel_ranks": {}, "channel_scores": {}},
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
        for rank, row in enumerate(ordered[: query.top_k], start=1):
            hit = row["hit"]
            metadata = dict(hit.metadata)  # type: ignore[union-attr]
            metadata.update(
                {
                    "rrf_k": self.rrf_k,
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
        return results


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

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
        if not self.enabled or query.optional_scope_path is not None:
            return self.hybrid.retrieve(query)
        scope = self._select_scope(query)
        if scope is None:
            return self.hybrid.retrieve(query)
        hits = self.hybrid.retrieve(replace(query, optional_scope_path=scope))
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
            metadata=dict(hit.metadata),
        )
        for rank, hit in enumerate(hits, start=1)
    ]
