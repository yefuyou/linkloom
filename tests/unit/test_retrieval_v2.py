from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from linkloom.context import ContextManifest
from linkloom.indexing import (
    BM25Index,
    DirectoryIndex,
    IndexDocument,
    IndexUpdateCoordinator,
    SourceDocument,
    VectorIndex,
)
from linkloom.retrieval_v2 import (
    BM25Retriever,
    CurrentRetriever,
    DenseRetriever,
    DirectoryAwareHybridRetriever,
    HybridRetriever,
    ProgressiveContextLoader,
    RetrievedEvidence,
    RetrievalQuery,
)
from linkloom.schemas import NoteDocument


class TermEmbedder:
    terms = ("semantic", "contract", "rollback")

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            [[float(text.casefold().count(term)) for term in self.terms] for text in texts],
            dtype=np.float32,
        )


def _document(
    resource_id: str,
    content: str,
    *,
    workspace_id: str = "alpha",
    logical_path: str | None = None,
) -> IndexDocument:
    path = logical_path or f"/workspaces/{workspace_id}/decisions/{resource_id}.md"
    return IndexDocument(
        workspace_id=workspace_id,
        resource_id=resource_id,
        evidence_id=f"evidence:{resource_id}",
        logical_path=path,
        content=content,
        source_ref=f"workspaces/{workspace_id}/{resource_id}.md",
        metadata={"content": content, "title": resource_id},
    )


def test_bm25_and_dense_retrievers_preserve_workspace_and_directory_scope() -> None:
    documents = [
        _document("approved", "contract approved semantic"),
        _document("incident", "rollback required", logical_path="/workspaces/alpha/incidents/incident.md"),
        _document("foreign", "contract contract", workspace_id="beta"),
    ]
    lexical = BM25Index()
    lexical.build(documents)
    vector = VectorIndex(TermEmbedder())
    vector.build(documents)

    query = RetrievalQuery(
        workspace_id="alpha",
        query="contract",
        top_k=5,
        optional_scope_path="/workspaces/alpha/decisions",
    )
    lexical_hits = BM25Retriever(lexical).retrieve(query)
    dense_hits = DenseRetriever(vector).retrieve(query)

    assert [hit.source_ref for hit in lexical_hits] == ["workspaces/alpha/approved.md"]
    assert [hit.source_ref for hit in dense_hits] == ["workspaces/alpha/approved.md"]
    assert lexical_hits[0].retrieval_channel == "bm25"
    assert dense_hits[0].retrieval_channel == "dense"
    assert lexical_hits[0].rank == dense_hits[0].rank == 1


def test_hybrid_retriever_uses_rrf_60_deduplicates_and_is_deterministic() -> None:
    documents = [
        _document("lexical", "contract contract semantic"),
        _document("dense", "semantic semantic contract"),
        _document("shared", "contract semantic"),
    ]
    lexical = BM25Index()
    lexical.build(documents)
    vector = VectorIndex(TermEmbedder())
    vector.build(documents)
    retriever = HybridRetriever(BM25Retriever(lexical), DenseRetriever(vector))

    first = retriever.retrieve(RetrievalQuery(workspace_id="alpha", query="contract semantic", top_k=3))
    second = retriever.retrieve(RetrievalQuery(workspace_id="alpha", query="contract semantic", top_k=3))

    assert first == second
    assert len({hit.evidence_id for hit in first}) == len(first) == 3
    assert all(hit.retrieval_channel == "hybrid" for hit in first)
    assert all(hit.metadata["rrf_k"] == 60 for hit in first)
    assert [hit.rank for hit in first] == [1, 2, 3]


def test_directory_routing_can_be_disabled_or_limit_hybrid_search_to_selected_scope() -> None:
    decisions = _document(
        "decision",
        "supplier contract approved",
        logical_path="/workspaces/alpha/decisions/final.md",
    )
    incidents = _document(
        "incident",
        "supplier rollback incident",
        logical_path="/workspaces/alpha/incidents/outage.md",
    )
    lexical = BM25Index()
    lexical.build([decisions, incidents])
    vector = VectorIndex(TermEmbedder())
    vector.build([decisions, incidents])
    directories = DirectoryIndex()
    directories.build([decisions, incidents])
    directories.refresh_summaries("alpha", "/workspaces/alpha/decisions")
    directories.refresh_summaries("alpha", "/workspaces/alpha/incidents")
    hybrid = HybridRetriever(BM25Retriever(lexical), DenseRetriever(vector))

    enabled = DirectoryAwareHybridRetriever(hybrid, directories, enabled=True)
    disabled = DirectoryAwareHybridRetriever(hybrid, directories, enabled=False)
    query = RetrievalQuery(workspace_id="alpha", query="decisions final supplier", top_k=5)

    enabled_hits = enabled.retrieve(query)
    disabled_hits = disabled.retrieve(query)

    assert [hit.resource_id for hit in enabled_hits] == ["decision"]
    assert enabled_hits[0].metadata["directory_scope"] == "/workspaces/alpha/decisions"
    assert {hit.resource_id for hit in disabled_hits} == {"decision", "incident"}


def test_progressive_context_loader_exposes_l0_then_l1_then_selected_l2() -> None:
    document = _document(
        "decision",
        "The source evidence says the contract is approved.",
        logical_path="/workspaces/alpha/decisions/final.md",
    )
    directories = DirectoryIndex()
    directories.build([document])
    directories.refresh_summaries("alpha", "/workspaces/alpha/decisions")
    loader = ProgressiveContextLoader(directories)

    abstract = loader.load_abstract("alpha", "/workspaces/alpha/decisions")
    overview = loader.load_overview("alpha", "/workspaces/alpha/decisions")
    original = loader.load_original("alpha", "decision")

    assert "final.md" in abstract
    assert "contains" in overview
    assert original.content == document.content
    assert replace(original, content="changed").content == "changed"


def test_current_retriever_treats_root_scope_as_the_whole_workspace() -> None:
    note = NoteDocument(
        relative_path="decisions/final.md",
        title="Final",
        content="# Final\nThe supplier contract is approved.",
        headings=[{"level": 1, "text": "Final", "line": 1}],
        tags=[],
        wikilinks=[],
        size_bytes=43,
        content_sha256="fixture-sha",
        line_count=2,
    )
    retriever = CurrentRetriever(lambda workspace_id: [note] if workspace_id == "alpha" else [])

    hits = retriever.retrieve(
        RetrievalQuery(
            workspace_id="alpha",
            query="supplier contract",
            top_k=5,
            optional_scope_path="/",
        )
    )

    assert [hit.source_ref for hit in hits] == ["decisions/final.md"]


def test_indexed_source_versions_get_distinct_bm25_and_dense_identity() -> None:
    coordinator = IndexUpdateCoordinator(
        manifest=ContextManifest(),
        lexical_index=BM25Index(),
        vector_index=VectorIndex(TermEmbedder()),
        directory_index=DirectoryIndex(),
    )
    original = SourceDocument(
        workspace_id="alpha",
        resource_id="decision-1",
        document_id="decision-1",
        logical_path="/decisions/final.md",
        content="semantic contract approved",
        source_timestamp=datetime(2026, 9, 24, tzinfo=UTC),
        source_ref="fixture://alpha/decisions/final.md",
    )
    version_one = coordinator.index_document(original)
    version_two = coordinator.index_document(
        replace(original, content="semantic contract rejected")
    )
    assert version_one.evidence_id != version_two.evidence_id

    coordinator.lexical_index.build([version_one])
    coordinator.vector_index.build([version_one])
    query = RetrievalQuery(workspace_id="alpha", query="semantic contract", top_k=1)
    lexical = BM25Retriever(coordinator.lexical_index).retrieve(query)
    dense = DenseRetriever(coordinator.vector_index).retrieve(query)

    assert lexical[0].evidence_id == dense[0].evidence_id == version_one.evidence_id


def test_rrf_only_deduplicates_the_same_immutable_evidence_identity() -> None:
    class FixedRetriever:
        def __init__(self, hit: RetrievedEvidence) -> None:
            self.hit = hit

        def retrieve(self, query: RetrievalQuery) -> list[RetrievedEvidence]:
            return [self.hit]

    same_lexical = RetrievedEvidence(
        evidence_id="ev_v2_shared",
        source_ref="decisions/final.md",
        logical_path="/decisions/final.md",
        score=1.0,
        rank=1,
        retrieval_channel="bm25",
        resource_id="decision-1",
        metadata={"workspace_id": "alpha", "content_sha256": "a" * 64},
    )
    same_dense = replace(same_lexical, retrieval_channel="dense", score=0.9)
    fused = HybridRetriever(FixedRetriever(same_lexical), FixedRetriever(same_dense)).retrieve(
        RetrievalQuery(workspace_id="alpha", query="decision", top_k=5)
    )
    assert len(fused) == 1
    assert fused[0].evidence_id == "ev_v2_shared"

    conflicting = replace(
        same_dense,
        source_ref="decisions/other.md",
        logical_path="/decisions/other.md",
        resource_id="decision-2",
        metadata={"workspace_id": "alpha", "content_sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="conflicting source identities"):
        HybridRetriever(FixedRetriever(same_lexical), FixedRetriever(conflicting)).retrieve(
            RetrievalQuery(workspace_id="alpha", query="decision", top_k=5)
        )
