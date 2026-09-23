"""In-memory source-of-truth manifest for logical context resources."""

from __future__ import annotations

from collections.abc import Iterable

from linkloom.context.models import (
    ChangeType,
    ContextResource,
    ManifestChange,
    ResourceType,
    SourceInventoryEntry,
    normalize_logical_path,
)


class ContextManifest:
    def __init__(self, resources: Iterable[ContextResource] = ()) -> None:
        self._resources: dict[tuple[str, str], ContextResource] = {}
        for resource in resources:
            self.upsert(resource)

    def upsert(self, resource: ContextResource) -> None:
        self._resources[(resource.workspace_id, resource.resource_id)] = resource

    def get_resource(self, workspace_id: str, resource_id: str) -> ContextResource | None:
        return self._resources.get((workspace_id, resource_id))

    def get_resource_by_path(self, workspace_id: str, logical_path: str) -> ContextResource | None:
        normalized = normalize_logical_path(logical_path)
        matches = [
            resource
            for resource in self._resources.values()
            if resource.workspace_id == workspace_id
            and resource.logical_path == normalized
            and resource.active
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.resource_id)[0]

    def list_resources(self, workspace_id: str | None = None, *, active_only: bool = True) -> list[ContextResource]:
        resources = [
            resource
            for resource in self._resources.values()
            if (workspace_id is None or resource.workspace_id == workspace_id)
            and (resource.active or not active_only)
        ]
        return sorted(resources, key=lambda item: (item.workspace_id, item.logical_path, item.resource_id))

    def list_children(self, workspace_id: str, parent_path: str) -> list[ContextResource]:
        normalized = normalize_logical_path(parent_path)
        return sorted(
            [
                resource
                for resource in self._resources.values()
                if resource.workspace_id == workspace_id
                and resource.active
                and resource.parent_path == normalized
            ],
            key=lambda item: (item.logical_path, item.resource_id),
        )

    def list_descendants(self, workspace_id: str, parent_path: str) -> list[ContextResource]:
        normalized = normalize_logical_path(parent_path)
        prefix = "/" if normalized == "/" else f"{normalized}/"
        return sorted(
            [
                resource
                for resource in self._resources.values()
                if resource.workspace_id == workspace_id
                and resource.active
                and resource.logical_path.startswith(prefix)
                and resource.logical_path != normalized
            ],
            key=lambda item: (item.logical_path, item.resource_id),
        )

    def find_changed_resources(
        self,
        source_inventory: Iterable[SourceInventoryEntry],
        *,
        workspace_id: str,
    ) -> list[ManifestChange]:
        inventory = {
            entry.resource_id: entry
            for entry in source_inventory
            if entry.workspace_id == workspace_id
        }
        active_documents = {
            resource.resource_id: resource
            for resource in self.list_resources(workspace_id)
            if resource.resource_type is ResourceType.DOCUMENT
        }
        changes: list[ManifestChange] = []

        for resource_id, entry in inventory.items():
            current = active_documents.get(resource_id)
            if current is None:
                changes.append(
                    ManifestChange(ChangeType.CREATE, workspace_id, resource_id, entry.logical_path)
                )
            elif current.logical_path != entry.logical_path:
                changes.append(
                    ManifestChange(
                        ChangeType.MOVE,
                        workspace_id,
                        resource_id,
                        entry.logical_path,
                        old_path=current.logical_path,
                    )
                )
            elif current.content_hash != entry.content_hash:
                changes.append(
                    ManifestChange(ChangeType.MODIFY, workspace_id, resource_id, entry.logical_path)
                )

        for resource_id, current in active_documents.items():
            if resource_id not in inventory:
                changes.append(
                    ManifestChange(ChangeType.DELETE, workspace_id, resource_id, current.logical_path)
                )

        order = {ChangeType.CREATE: 0, ChangeType.MOVE: 1, ChangeType.MODIFY: 2, ChangeType.DELETE: 3}
        return sorted(changes, key=lambda item: (order[item.change_type], item.resource_id))
