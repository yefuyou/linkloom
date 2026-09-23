from __future__ import annotations

from datetime import UTC, datetime

from linkloom.context import ContextManifest, ContextResource, ResourceType, SourceInventoryEntry


def _resource(
    *,
    resource_id: str = "doc-1",
    logical_path: str = "/projects/borealis/requirements/vendor.md",
    content_hash: str = "sha-v1",
) -> ContextResource:
    return ContextResource(
        workspace_id="borealis",
        resource_id=resource_id,
        document_id=resource_id,
        logical_path=logical_path,
        parent_path=logical_path.rsplit("/", 1)[0],
        version=1,
        source_timestamp=datetime(2026, 5, 12, tzinfo=UTC),
        content_hash=content_hash,
        source_ref=f"fixture://{resource_id}",
        resource_type=ResourceType.DOCUMENT,
        indexed_at=datetime(2026, 5, 12, 1, tzinfo=UTC),
        index_version=1,
    )


def test_manifest_get_children_and_descendants_are_workspace_scoped() -> None:
    manifest = ContextManifest()
    manifest.upsert(_resource(resource_id="doc-a"))
    manifest.upsert(
        _resource(
            resource_id="doc-b",
            logical_path="/projects/borealis/requirements/archive/vendor-v1.md",
        )
    )
    manifest.upsert(
        ContextResource.directory(
            workspace_id="borealis",
            resource_id="dir-archive",
            logical_path="/projects/borealis/requirements/archive",
            source_timestamp=datetime(2026, 5, 12, tzinfo=UTC),
        )
    )
    manifest.upsert(
        _resource(
            resource_id="foreign",
            logical_path="/projects/borealis/requirements/foreign.md",
        ).with_workspace("other")
    )

    assert manifest.get_resource("borealis", "doc-a") == _resource(resource_id="doc-a")
    assert [item.resource_id for item in manifest.list_children("borealis", "/projects/borealis/requirements")] == [
        "dir-archive",
        "doc-a",
    ]
    assert [item.resource_id for item in manifest.list_descendants("borealis", "/projects/borealis/requirements")] == [
        "dir-archive",
        "doc-b",
        "doc-a",
    ]


def test_find_changed_resources_distinguishes_create_modify_delete_and_unchanged() -> None:
    manifest = ContextManifest([_resource(resource_id="changed"), _resource(resource_id="deleted")])
    inventory = [
        SourceInventoryEntry(
            workspace_id="borealis",
            resource_id="changed",
            logical_path="/projects/borealis/requirements/vendor.md",
            content_hash="sha-v2",
            source_timestamp=datetime(2026, 5, 13, tzinfo=UTC),
            source_ref="fixture://changed",
        ),
        SourceInventoryEntry(
            workspace_id="borealis",
            resource_id="created",
            logical_path="/projects/borealis/requirements/new.md",
            content_hash="sha-new",
            source_timestamp=datetime(2026, 5, 13, tzinfo=UTC),
            source_ref="fixture://created",
        ),
    ]

    changes = manifest.find_changed_resources(inventory, workspace_id="borealis")

    assert [(change.change_type.value, change.resource_id) for change in changes] == [
        ("CREATE", "created"),
        ("MODIFY", "changed"),
        ("DELETE", "deleted"),
    ]
