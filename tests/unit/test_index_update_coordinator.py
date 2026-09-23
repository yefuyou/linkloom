from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from linkloom.context import ChangeType, ContextManifest, FileChangeEvent
from linkloom.indexing import (
    BM25Index,
    DirectoryIndex,
    IndexDocument,
    IndexUpdateCoordinator,
    SourceDocument,
    VectorIndex,
    reconcile,
)


class TrackingEmbedder:
    dimensions = ("supplier", "contract", "rollback", "stable")

    def __init__(self) -> None:
        self.encoded_texts: list[str] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.encoded_texts.extend(texts)
        return np.asarray(
            [[float(text.casefold().count(term)) for term in self.dimensions] for text in texts],
            dtype=np.float32,
        )


def _source(
    resource_id: str,
    content: str,
    *,
    path: str | None = None,
    timestamp: datetime | None = None,
) -> SourceDocument:
    return SourceDocument(
        workspace_id="borealis",
        resource_id=resource_id,
        document_id=resource_id,
        logical_path=path or f"/projects/borealis/requirements/{resource_id}.md",
        content=content,
        source_timestamp=timestamp or datetime(2026, 5, 12, tzinfo=UTC),
        source_ref=f"fixture://{resource_id}",
        metadata={"title": resource_id},
    )


def _coordinator(embedder: TrackingEmbedder | None = None) -> IndexUpdateCoordinator:
    return IndexUpdateCoordinator(
        manifest=ContextManifest(),
        lexical_index=BM25Index(),
        vector_index=VectorIndex(embedder or TrackingEmbedder()),
        directory_index=DirectoryIndex(index_version=7),
        index_version=7,
    )


def test_file_change_lifecycle_updates_only_affected_entries() -> None:
    embedder = TrackingEmbedder()
    coordinator = _coordinator(embedder)
    created = _source("vendor", "supplier shortlist")

    create_result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.CREATE,
            workspace_id="borealis",
            logical_path=created.logical_path,
            content_hash=created.content_hash,
            timestamp=created.source_timestamp,
        ),
        created,
    )
    assert create_result.status == "APPLIED"
    assert create_result.affected_indexes == ("lexical", "vector", "directory", "manifest")
    assert coordinator.lexical_index.search("supplier", workspace_id="borealis")[0].resource_id == "vendor"
    assert coordinator.vector_index.search("supplier", workspace_id="borealis")[0].resource_id == "vendor"
    assert coordinator.directory_index.get_resources_under("borealis", "/projects/borealis") == ("vendor",)
    assert coordinator.manifest.get_resource("borealis", "vendor").version == 1

    calls_before_noop = len(embedder.encoded_texts)
    unchanged_result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.MODIFY,
            workspace_id="borealis",
            logical_path=created.logical_path,
            content_hash=created.content_hash,
        ),
        created,
    )
    assert unchanged_result.status == "NO_OP"
    assert unchanged_result.affected_indexes == ()
    assert len(embedder.encoded_texts) == calls_before_noop

    modified = _source("vendor", "contract signed", timestamp=datetime(2026, 5, 13, tzinfo=UTC))
    changed_result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.MODIFY,
            workspace_id="borealis",
            logical_path=modified.logical_path,
            content_hash=modified.content_hash,
            timestamp=modified.source_timestamp,
        ),
        modified,
    )
    assert changed_result.status == "APPLIED"
    assert coordinator.lexical_index.search("supplier", workspace_id="borealis") == []
    assert coordinator.lexical_index.search("contract", workspace_id="borealis")[0].resource_id == "vendor"
    assert coordinator.vector_index.search("contract", workspace_id="borealis")[0].resource_id == "vendor"
    assert coordinator.manifest.get_resource("borealis", "vendor").version == 2

    moved_path = "/projects/borealis/decisions/supplier.md"
    move_result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.MOVE,
            workspace_id="borealis",
            logical_path=moved_path,
            old_path=modified.logical_path,
            content_hash=modified.content_hash,
        )
    )
    assert move_result.status == "APPLIED"
    assert coordinator.manifest.get_resource("borealis", "vendor").logical_path == moved_path
    assert coordinator.manifest.get_resource("borealis", "vendor").document_id == "vendor"
    assert coordinator.directory_index.get_resources_under("borealis", "/projects/borealis/requirements") == ()
    assert coordinator.directory_index.get_resources_under("borealis", "/projects/borealis/decisions") == ("vendor",)
    vendor_lex = coordinator.lexical_index.get_document("borealis", "vendor")
    vendor_vec = coordinator.vector_index.get_document("borealis", "vendor")
    assert vendor_lex is not None and vendor_lex.logical_path == moved_path
    assert vendor_vec is not None and vendor_vec.logical_path == moved_path
    assert coordinator.lexical_index.search("contract", workspace_id="borealis", scope_path="/projects/borealis/requirements") == []
    assert coordinator.lexical_index.search("contract", workspace_id="borealis", scope_path="/projects/borealis/decisions")[0].resource_id == "vendor"
    assert coordinator.vector_index.search("contract", workspace_id="borealis", scope_path="/projects/borealis/requirements") == []
    assert coordinator.vector_index.search("contract", workspace_id="borealis", scope_path="/projects/borealis/decisions")[0].resource_id == "vendor"

    delete_result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.DELETE,
            workspace_id="borealis",
            logical_path=moved_path,
        )
    )
    assert delete_result.status == "APPLIED"
    assert coordinator.manifest.get_resource("borealis", "vendor").active is False
    assert coordinator.lexical_index.resource_ids("borealis") == set()
    assert coordinator.vector_index.resource_ids("borealis") == set()
    assert coordinator.directory_index.resource_ids("borealis") == set()


def test_move_with_simultaneous_content_change_updates_all_indexes() -> None:
    coordinator = _coordinator()
    initial = _source("vendor", "supplier shortlist", path="/projects/borealis/requirements/vendor.md")
    coordinator.create(initial)

    moved_modified = _source(
        "vendor",
        "contract approved",
        path="/projects/borealis/decisions/supplier.md",
        timestamp=datetime(2026, 5, 14, tzinfo=UTC),
    )
    move_result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.MOVE,
            workspace_id="borealis",
            logical_path=moved_modified.logical_path,
            old_path=initial.logical_path,
            content_hash=moved_modified.content_hash,
            timestamp=moved_modified.source_timestamp,
        ),
        moved_modified,
    )
    assert move_result.status == "APPLIED"
    manifest_entry = coordinator.manifest.get_resource("borealis", "vendor")
    assert manifest_entry is not None
    assert manifest_entry.version == 2
    assert manifest_entry.content_hash == moved_modified.content_hash
    assert manifest_entry.logical_path == moved_modified.logical_path
    assert coordinator.lexical_index.search("supplier", workspace_id="borealis") == []
    assert coordinator.lexical_index.search("contract", workspace_id="borealis", scope_path="/projects/borealis/decisions")[0].resource_id == "vendor"
    assert coordinator.vector_index.search("contract", workspace_id="borealis", scope_path="/projects/borealis/decisions")[0].resource_id == "vendor"
    assert coordinator.directory_index.get_resources_under("borealis", "/projects/borealis/requirements") == ()
    assert coordinator.directory_index.get_resources_under("borealis", "/projects/borealis/decisions") == ("vendor",)


def test_reconcile_detects_and_repairs_every_required_drift_class() -> None:
    coordinator = _coordinator()
    changed_v1 = _source("changed", "supplier shortlist")
    deleted = _source("deleted", "rollback plan")
    stable = _source("stable", "stable decision")
    missing_lex = _source("missing-lex", "supplier evaluation")
    missing_vec = _source("missing-vec", "rollback strategy")
    stale_lex = _source("stale-lex", "contract agreement")
    stale_vec = _source("stale-vec", "contract protocol")
    moved_src = _source("moved-src", "stable notes", path="/projects/borealis/decisions/moved.md")
    moved_src_initial = _source("moved-src", "stable notes", path="/projects/borealis/requirements/moved.md")

    for document in (
        changed_v1,
        deleted,
        stable,
        missing_lex,
        missing_vec,
        stale_lex,
        stale_vec,
        moved_src_initial,
    ):
        coordinator.create(document)

    changed_v2 = _source("changed", "contract signed", timestamp=datetime(2026, 5, 13, tzinfo=UTC))
    created = _source("created", "supplier approved")

    lexical_orphan = IndexDocument(
        workspace_id="borealis",
        resource_id="lexical-orphan",
        evidence_id="orphan:lexical",
        logical_path="/projects/borealis/orphans/lexical.md",
        content="orphan lexical",
        source_ref="fixture://orphan-lexical",
    )
    vector_orphan = IndexDocument(
        workspace_id="borealis",
        resource_id="vector-orphan",
        evidence_id="orphan:vector",
        logical_path="/projects/borealis/orphans/vector.md",
        content="orphan vector",
        source_ref="fixture://orphan-vector",
    )
    coordinator.lexical_index.upsert(lexical_orphan)
    coordinator.vector_index.upsert(vector_orphan)
    coordinator.directory_index.move("borealis", "stable", "/projects/borealis/wrong/stable.md")

    coordinator.lexical_index.remove("borealis", "missing-lex")
    coordinator.vector_index.remove("borealis", "missing-vec")

    coordinator.lexical_index.upsert(
        IndexDocument(
            workspace_id="borealis",
            resource_id="stale-lex",
            evidence_id="evidence:stale-lex",
            logical_path=stale_lex.logical_path,
            content="stale lexical content",
            source_ref=stale_lex.source_ref,
            metadata={"content_hash": "stale-hash"},
        )
    )
    coordinator.vector_index.upsert(
        IndexDocument(
            workspace_id="borealis",
            resource_id="stale-vec",
            evidence_id="evidence:stale-vec",
            logical_path=stale_vec.logical_path,
            content="stale vector content",
            source_ref=stale_vec.source_ref,
            metadata={"content_hash": "stale-hash"},
        )
    )

    expected_sources = [
        changed_v2,
        stable,
        created,
        missing_lex,
        missing_vec,
        stale_lex,
        stale_vec,
        moved_src,
    ]
    report = reconcile(expected_sources, coordinator)

    assert set(report.detected_issue_types) >= {
        "missing_source",
        "missing_manifest",
        "hash_drift",
        "orphan_lexical_entry",
        "orphan_vector_entry",
        "stale_directory_entry",
        "missing_lexical_entry",
        "missing_vector_entry",
        "stale_lexical_entry",
        "stale_vector_entry",
    }
    assert report.repaired_count == len(report.issues)
    created_res = coordinator.manifest.get_resource("borealis", "created")
    deleted_res = coordinator.manifest.get_resource("borealis", "deleted")
    changed_res = coordinator.manifest.get_resource("borealis", "changed")
    moved_res = coordinator.manifest.get_resource("borealis", "moved-src")
    assert created_res is not None and created_res.active is True
    assert deleted_res is not None and deleted_res.active is False
    assert changed_res is not None and changed_res.content_hash == changed_v2.content_hash
    assert moved_res is not None and moved_res.logical_path == moved_src.logical_path

    expected_ids = {"changed", "created", "stable", "missing-lex", "missing-vec", "stale-lex", "stale-vec", "moved-src"}
    assert coordinator.lexical_index.resource_ids("borealis") == expected_ids
    assert coordinator.vector_index.resource_ids("borealis") == expected_ids
    assert coordinator.directory_index.resource_ids("borealis") == expected_ids

    repaired_stable_dir = coordinator.directory_index.get_document("borealis", "stable")
    repaired_moved_dir = coordinator.directory_index.get_document("borealis", "moved-src")
    assert repaired_stable_dir is not None and repaired_stable_dir.logical_path == stable.logical_path
    assert repaired_moved_dir is not None and repaired_moved_dir.logical_path == moved_src.logical_path

    repaired_lex_missing = coordinator.lexical_index.get_document("borealis", "missing-lex")
    repaired_vec_missing = coordinator.vector_index.get_document("borealis", "missing-vec")
    assert repaired_lex_missing is not None and repaired_lex_missing.content == missing_lex.content
    assert repaired_vec_missing is not None and repaired_vec_missing.content == missing_vec.content

    repaired_lex_stale = coordinator.lexical_index.get_document("borealis", "stale-lex")
    repaired_vec_stale = coordinator.vector_index.get_document("borealis", "stale-vec")
    assert repaired_lex_stale is not None and repaired_lex_stale.metadata["content_hash"] == stale_lex.content_hash
    assert repaired_vec_stale is not None and repaired_vec_stale.metadata["content_hash"] == stale_vec.content_hash

    assert coordinator.lexical_index.search("contract", workspace_id="borealis")[0].resource_id == "changed"


def test_reconcile_repairs_orphans_in_an_index_only_workspace() -> None:
    coordinator = _coordinator()
    orphan = IndexDocument(
        workspace_id="index-only",
        resource_id="orphan",
        evidence_id="orphan:evidence",
        logical_path="/orphan.md",
        content="orphan supplier content",
        source_ref="fixture://orphan",
    )
    coordinator.lexical_index.upsert(orphan)
    coordinator.vector_index.upsert(orphan)
    coordinator.directory_index.upsert(orphan)

    report = reconcile([], coordinator)

    assert set(report.detected_issue_types) == {
        "orphan_lexical_entry",
        "orphan_vector_entry",
        "stale_directory_entry",
    }
    assert coordinator.lexical_index.resource_ids("index-only") == set()
    assert coordinator.vector_index.resource_ids("index-only") == set()
    assert coordinator.directory_index.resource_ids("index-only") == set()


def test_reconcile_repairs_path_drift_when_an_index_entry_is_also_missing() -> None:
    coordinator = _coordinator()
    initial = _source("combined", "supplier decision", path="/projects/borealis/old.md")
    coordinator.create(initial)
    coordinator.lexical_index.remove("borealis", "combined")
    moved = _source("combined", "supplier decision", path="/projects/borealis/new.md")

    report = reconcile([moved], coordinator)

    assert "stale_directory_entry" in report.detected_issue_types
    manifest_entry = coordinator.manifest.get_resource("borealis", "combined")
    assert manifest_entry is not None and manifest_entry.logical_path == moved.logical_path
    for index in (
        coordinator.lexical_index,
        coordinator.vector_index,
        coordinator.directory_index,
    ):
        document = index.get_document("borealis", "combined")
        assert document is not None and document.logical_path == moved.logical_path
