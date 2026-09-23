"""Scheduled reconciliation for source/manifest/index eventual consistency."""

from __future__ import annotations

from dataclasses import dataclass

from linkloom.context import ChangeType, FileChangeEvent, ResourceType
from linkloom.indexing.coordinator import IndexUpdateCoordinator, SourceDocument


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    issue_type: str
    workspace_id: str
    resource_id: str
    detail: str
    repaired: bool = True


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    issues: tuple[ReconciliationIssue, ...]

    @property
    def detected_issue_types(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(issue.issue_type for issue in self.issues))

    @property
    def repaired_count(self) -> int:
        return sum(issue.repaired for issue in self.issues)


def reconcile(
    source_inventory: list[SourceDocument],
    coordinator: IndexUpdateCoordinator,
) -> ReconciliationReport:
    """Detect and repair missed source events plus stale/orphan index entries."""
    issues: list[ReconciliationIssue] = []
    sources = {(source.workspace_id, source.resource_id): source for source in source_inventory}
    workspaces = {source.workspace_id for source in source_inventory}
    workspaces.update(
        resource.workspace_id
        for resource in coordinator.manifest.list_resources(active_only=False)
        if resource.resource_type is ResourceType.DOCUMENT
    )
    workspaces.update(coordinator.lexical_index.workspace_ids())
    workspaces.update(coordinator.vector_index.workspace_ids())
    workspaces.update(coordinator.directory_index.workspace_ids())

    for key, source in sorted(sources.items()):
        current = coordinator.manifest.get_resource(*key)
        if current is None or not current.active:
            issues.append(_issue("missing_manifest", source, "source resource has no active manifest row"))
            coordinator.create(source)
            continue
        if current.logical_path != source.logical_path:
            issues.append(_issue("stale_directory_entry", source, f"manifest path was {current.logical_path}"))
            coordinator.apply(
                FileChangeEvent(
                    event_type=ChangeType.MOVE,
                    workspace_id=source.workspace_id,
                    logical_path=source.logical_path,
                    old_path=current.logical_path,
                    content_hash=source.content_hash,
                    timestamp=source.source_timestamp,
                ),
                source,
            )
        elif current.content_hash != source.content_hash:
            issues.append(_issue("hash_drift", source, "source hash differs from manifest"))
            coordinator.apply(
                FileChangeEvent(
                    event_type=ChangeType.MODIFY,
                    workspace_id=source.workspace_id,
                    logical_path=source.logical_path,
                    content_hash=source.content_hash,
                    timestamp=source.source_timestamp,
                ),
                source,
            )

    for resource in coordinator.manifest.list_resources(active_only=True):
        key = (resource.workspace_id, resource.resource_id)
        if resource.resource_type is not ResourceType.DOCUMENT or key in sources:
            continue
        issues.append(
            ReconciliationIssue(
                "missing_source",
                resource.workspace_id,
                resource.resource_id,
                "active manifest document is absent from source inventory",
            )
        )
        coordinator.apply(
            FileChangeEvent(
                event_type=ChangeType.DELETE,
                workspace_id=resource.workspace_id,
                logical_path=resource.logical_path,
            )
        )

    for workspace_id in sorted(workspaces):
        expected = {
            resource_id: source
            for (source_workspace, resource_id), source in sources.items()
            if source_workspace == workspace_id
        }
        expected_ids = set(expected)
        _repair_flat_index(
            "lexical",
            workspace_id,
            expected,
            expected_ids,
            coordinator,
            issues,
        )
        _repair_flat_index(
            "vector",
            workspace_id,
            expected,
            expected_ids,
            coordinator,
            issues,
        )
        actual_directory_ids = coordinator.directory_index.resource_ids(workspace_id)
        for resource_id in sorted(actual_directory_ids - expected_ids):
            issues.append(
                ReconciliationIssue(
                    "stale_directory_entry",
                    workspace_id,
                    resource_id,
                    "directory entry has no source resource",
                )
            )
            coordinator.directory_index.remove(workspace_id, resource_id)
        for resource_id, source in sorted(expected.items()):
            current = coordinator.directory_index.get_document(workspace_id, resource_id)
            if current is None or current.logical_path != source.logical_path:
                issues.append(
                    ReconciliationIssue(
                        "stale_directory_entry",
                        workspace_id,
                        resource_id,
                        "directory entry is missing or points at a stale path",
                    )
                )
                coordinator.directory_index.upsert(coordinator.index_document(source))

    return ReconciliationReport(tuple(issues))


def _repair_flat_index(
    index_name: str,
    workspace_id: str,
    expected: dict[str, SourceDocument],
    expected_ids: set[str],
    coordinator: IndexUpdateCoordinator,
    issues: list[ReconciliationIssue],
) -> None:
    index = coordinator.lexical_index if index_name == "lexical" else coordinator.vector_index
    actual_ids = index.resource_ids(workspace_id)
    for resource_id in sorted(actual_ids - expected_ids):
        issues.append(
            ReconciliationIssue(
                f"orphan_{index_name}_entry",
                workspace_id,
                resource_id,
                f"{index_name} entry has no source resource",
            )
        )
        index.remove(workspace_id, resource_id)
    for resource_id, source in sorted(expected.items()):
        current = index.get_document(workspace_id, resource_id)
        if current is None:
            issues.append(
                ReconciliationIssue(
                    f"missing_{index_name}_entry",
                    workspace_id,
                    resource_id,
                    f"source resource is absent from {index_name} index",
                )
            )
            index.upsert(coordinator.index_document(source))
        elif current.logical_path != source.logical_path or current.metadata.get("content_hash") != source.content_hash:
            issues.append(
                ReconciliationIssue(
                    f"stale_{index_name}_entry",
                    workspace_id,
                    resource_id,
                    f"{index_name} entry differs from source",
                )
            )
            index.upsert(coordinator.index_document(source))


def _issue(issue_type: str, source: SourceDocument, detail: str) -> ReconciliationIssue:
    return ReconciliationIssue(issue_type, source.workspace_id, source.resource_id, detail)
