from __future__ import annotations

import numpy as np
import pytest

from linkloom.indexing import BM25Index, DirectoryIndex, IndexDocument, VectorIndex


class KeywordEmbedder:
    """Small deterministic embedder used to prove vector lifecycle behavior."""

    dimensions = ("supplier", "rollback", "meeting")

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            [[float(text.casefold().count(term)) for term in self.dimensions] for text in texts],
            dtype=np.float32,
        )


def _document(
    resource_id: str,
    content: str,
    *,
    workspace_id: str = "borealis",
    logical_path: str | None = None,
) -> IndexDocument:
    path = logical_path or f"/projects/borealis/requirements/{resource_id}.md"
    return IndexDocument(
        workspace_id=workspace_id,
        resource_id=resource_id,
        evidence_id=f"evidence:{resource_id}",
        logical_path=path,
        content=content,
        source_ref=f"fixture://{resource_id}",
        metadata={"title": resource_id},
    )


def test_bm25_index_build_upsert_remove_and_scope_before_ranking() -> None:
    index = BM25Index()
    index.build(
        [
            _document("vendor", "supplier shortlist approved"),
            _document("incident", "rollback procedure"),
            _document("foreign", "supplier supplier supplier", workspace_id="other"),
        ]
    )

    assert [hit.resource_id for hit in index.search("supplier", workspace_id="borealis", top_k=5)] == ["vendor"]
    assert index.search("supplier", workspace_id="other", top_k=5)[0].resource_id == "foreign"

    index.upsert(_document("vendor", "contract signed"))
    assert index.search("supplier", workspace_id="borealis", top_k=5) == []
    assert index.search("contract", workspace_id="borealis", top_k=5)[0].resource_id == "vendor"

    index.remove("borealis", "vendor")
    assert index.search("contract", workspace_id="borealis", top_k=5) == []
    assert index.resource_ids("borealis") == {"incident"}

    index.move("borealis", "incident", "/projects/borealis/operations/incident.md")
    incident_doc = index.get_document("borealis", "incident")
    assert incident_doc is not None
    assert incident_doc.logical_path == "/projects/borealis/operations/incident.md"
    assert index.search("rollback", workspace_id="borealis", scope_path="/projects/borealis/requirements") == []
    assert index.search("rollback", workspace_id="borealis", scope_path="/projects/borealis/operations")[0].resource_id == "incident"


def test_bm25_directory_scope_defines_the_ranked_corpus() -> None:
    scoped_documents = [
        _document("alpha", "alpha", logical_path="/scope/alpha.md"),
        _document("beta", "beta", logical_path="/scope/beta.md"),
    ]
    full_index = BM25Index()
    full_index.build(
        [
            *scoped_documents,
            _document("outside-1", "alpha", logical_path="/outside/one.md"),
            _document("outside-2", "alpha", logical_path="/outside/two.md"),
            _document("outside-3", "alpha", logical_path="/outside/three.md"),
        ]
    )
    scoped_baseline = BM25Index()
    scoped_baseline.build(scoped_documents)

    actual = full_index.search("alpha beta", workspace_id="borealis", scope_path="/scope")
    expected = scoped_baseline.search("alpha beta", workspace_id="borealis")

    assert [hit.resource_id for hit in actual] == [hit.resource_id for hit in expected]
    assert [hit.score for hit in actual] == pytest.approx([hit.score for hit in expected])


def test_vector_index_exact_cosine_upsert_remove_and_directory_scope() -> None:
    index = VectorIndex(KeywordEmbedder())
    index.build(
        [
            _document("decision", "supplier supplier", logical_path="/projects/borealis/decisions/final.md"),
            _document("meeting", "supplier meeting", logical_path="/projects/borealis/meetings/notes.md"),
            _document("foreign", "supplier supplier supplier", workspace_id="other"),
        ]
    )

    hits = index.search(
        "supplier",
        workspace_id="borealis",
        scope_path="/projects/borealis/decisions",
        top_k=5,
    )
    assert [hit.resource_id for hit in hits] == ["decision"]
    assert hits[0].score == 1.0

    index.upsert(_document("decision", "rollback", logical_path="/projects/borealis/decisions/final.md"))
    assert index.search("supplier", workspace_id="borealis", scope_path="/projects/borealis/decisions") == []
    assert index.search("rollback", workspace_id="borealis")[0].resource_id == "decision"

    index.remove("borealis", "decision")
    assert index.search("rollback", workspace_id="borealis") == []

    index.move("borealis", "meeting", "/projects/borealis/archive/notes.md")
    meeting_doc = index.get_document("borealis", "meeting")
    assert meeting_doc is not None
    assert meeting_doc.logical_path == "/projects/borealis/archive/notes.md"
    assert index.search("meeting", workspace_id="borealis", scope_path="/projects/borealis/meetings") == []
    assert index.search("meeting", workspace_id="borealis", scope_path="/projects/borealis/archive")[0].resource_id == "meeting"


def test_directory_index_tracks_hierarchy_moves_and_dirty_ancestors() -> None:
    index = DirectoryIndex(index_version=3)
    index.upsert(
        _document(
            "vendor",
            "supplier shortlist approved",
            logical_path="/projects/borealis/requirements/vendor.md",
        )
    )
    index.upsert(
        _document(
            "rollout",
            "rollout action",
            logical_path="/projects/borealis/actions/rollout.md",
        )
    )

    requirements = index.get_node("borealis", "/projects/borealis/requirements")
    assert requirements is not None
    assert requirements.contained_documents == ("vendor",)
    assert requirements.dirty_summary is True
    assert requirements.index_version == 3
    assert index.get_parent("borealis", "/projects/borealis/requirements") == "/projects/borealis"
    assert index.get_children("borealis", "/projects/borealis") == (
        "/projects/borealis/actions",
        "/projects/borealis/requirements",
    )
    assert index.get_descendants("borealis", "/") == (
        "/projects",
        "/projects/borealis",
        "/projects/borealis/actions",
        "/projects/borealis/requirements",
    )
    assert index.get_resources_under("borealis", "/projects/borealis") == ("rollout", "vendor")

    index.refresh_summaries("borealis", "/projects/borealis/requirements")
    refreshed = index.get_node("borealis", "/projects/borealis/requirements")
    assert refreshed is not None
    assert refreshed.dirty_summary is False
    assert "vendor.md" in refreshed.abstract
    assert "vendor.md" in refreshed.overview

    index.refresh_summaries("borealis", "/projects/borealis")
    borealis_node = index.get_node("borealis", "/projects/borealis")
    assert borealis_node is not None
    assert borealis_node.dirty_summary is False

    index.refresh_summaries("borealis", "/projects")
    projects_node = index.get_node("borealis", "/projects")
    assert projects_node is not None
    assert projects_node.dirty_summary is False

    index.refresh_summaries("borealis", "/")
    root_node = index.get_node("borealis", "/")
    assert root_node is not None
    assert root_node.dirty_summary is False

    index.move("borealis", "vendor", "/projects/borealis/decisions/supplier.md")
    assert index.get_resources_under("borealis", "/projects/borealis/requirements") == ()
    assert index.get_resources_under("borealis", "/projects/borealis/decisions") == ("vendor",)
    req_after = index.get_node("borealis", "/projects/borealis/requirements")
    dec_after = index.get_node("borealis", "/projects/borealis/decisions")
    borealis_after = index.get_node("borealis", "/projects/borealis")
    projects_after = index.get_node("borealis", "/projects")
    root_after = index.get_node("borealis", "/")
    assert req_after is not None and req_after.dirty_summary is True
    assert dec_after is not None and dec_after.dirty_summary is True
    assert borealis_after is not None and borealis_after.dirty_summary is True
    assert projects_after is not None and projects_after.dirty_summary is True
    assert root_after is not None and root_after.dirty_summary is True

    index.remove("borealis", "vendor")
    assert index.get_resources_under("borealis", "/projects/borealis/decisions") == ()
