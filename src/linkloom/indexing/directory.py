"""Directory-aware structured index for hierarchical context navigation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from linkloom.context import normalize_logical_path, parent_logical_path
from linkloom.indexing.models import IndexDocument


@dataclass(frozen=True, slots=True)
class DirectoryNode:
    path: str
    workspace_id: str
    parent_path: str | None
    child_paths: tuple[str, ...]
    contained_documents: tuple[str, ...]
    abstract: str
    overview: str
    content_hash: str
    index_version: int
    dirty_summary: bool = True


class DirectoryIndex:
    def __init__(self, *, index_version: int = 1) -> None:
        self.index_version = index_version
        self._nodes: dict[tuple[str, str], DirectoryNode] = {}
        self._documents: dict[tuple[str, str], IndexDocument] = {}

    def build(self, documents: list[IndexDocument]) -> None:
        self._nodes.clear()
        self._documents.clear()
        for document in documents:
            self.upsert(document)

    def upsert(self, document: IndexDocument) -> None:
        key = (document.workspace_id, document.resource_id)
        previous = self._documents.get(key)
        if previous is not None and previous.logical_path != document.logical_path:
            self._detach(previous)
        self._documents[key] = document
        self._attach(document)

    def remove(self, workspace_id: str, resource_id: str) -> None:
        document = self._documents.pop((workspace_id, resource_id), None)
        if document is not None:
            self._detach(document)

    def move(self, workspace_id: str, resource_id: str, new_path: str) -> None:
        key = (workspace_id, resource_id)
        document = self._documents.get(key)
        if document is None:
            raise KeyError(f"unknown directory-index resource: {workspace_id}/{resource_id}")
        self._detach(document)
        moved = replace(document, logical_path=normalize_logical_path(new_path))
        self._documents[key] = moved
        self._attach(moved)

    def get_node(self, workspace_id: str, path: str) -> DirectoryNode | None:
        return self._nodes.get((workspace_id, normalize_logical_path(path)))

    def list_nodes(self, workspace_id: str | None = None) -> tuple[DirectoryNode, ...]:
        return tuple(
            sorted(
                (
                    node
                    for (node_workspace, _), node in self._nodes.items()
                    if workspace_id is None or node_workspace == workspace_id
                ),
                key=lambda node: (node.workspace_id, node.path),
            )
        )

    def get_parent(self, workspace_id: str, path: str) -> str | None:
        node = self.get_node(workspace_id, path)
        return node.parent_path if node else parent_logical_path(path)

    def get_children(self, workspace_id: str, path: str) -> tuple[str, ...]:
        node = self.get_node(workspace_id, path)
        return node.child_paths if node else ()

    def get_descendants(self, workspace_id: str, path: str) -> tuple[str, ...]:
        normalized = normalize_logical_path(path)
        prefix = f"{normalized.rstrip('/')}/"
        return tuple(
            sorted(
                node_path
                for (node_workspace, node_path) in self._nodes
                if node_workspace == workspace_id
                and node_path != normalized
                and node_path.startswith(prefix)
            )
        )

    def get_ancestors(self, workspace_id: str, path: str) -> tuple[str, ...]:
        del workspace_id
        ancestors: list[str] = []
        current = parent_logical_path(path)
        while current is not None:
            ancestors.append(current)
            current = parent_logical_path(current)
        return tuple(ancestors)

    def get_resources_under(self, workspace_id: str, path: str) -> tuple[str, ...]:
        normalized = normalize_logical_path(path)
        prefix = f"{normalized.rstrip('/')}/"
        return tuple(
            sorted(
                resource_id
                for (document_workspace, resource_id), document in self._documents.items()
                if document_workspace == workspace_id
                and (document.logical_path == normalized or document.logical_path.startswith(prefix))
            )
        )

    def resource_ids(self, workspace_id: str | None = None) -> set[str]:
        return {
            resource_id
            for (document_workspace, resource_id) in self._documents
            if workspace_id is None or document_workspace == workspace_id
        }

    def workspace_ids(self) -> set[str]:
        return {workspace_id for workspace_id, _ in self._documents}

    def get_document(self, workspace_id: str, resource_id: str) -> IndexDocument | None:
        return self._documents.get((workspace_id, resource_id))

    def refresh_summaries(self, workspace_id: str, path: str) -> DirectoryNode:
        normalized = normalize_logical_path(path)
        key = (workspace_id, normalized)
        node = self._nodes.get(key)
        if node is None:
            raise KeyError(f"unknown directory: {workspace_id}{normalized}")
        direct_documents = [
            self._documents[(workspace_id, resource_id)]
            for resource_id in node.contained_documents
        ]
        file_names = [PurePosixPath(document.logical_path).name for document in direct_documents]
        child_names = [PurePosixPath(child).name for child in node.child_paths]
        label = PurePosixPath(normalized).name or "root"
        file_text = ", ".join(file_names) if file_names else "no direct files"
        child_text = ", ".join(child_names) if child_names else "no child directories"
        abstract = f"Directory {label}; files: {file_text}; children: {child_text}."
        overview = (
            f"Directory {normalized} contains {len(file_names)} direct files ({file_text}) "
            f"and {len(child_names)} child directories ({child_text})."
        )
        refreshed = replace(node, abstract=abstract[:300], overview=overview, dirty_summary=False)
        self._nodes[key] = refreshed
        return refreshed

    def _attach(self, document: IndexDocument) -> None:
        parent = parent_logical_path(document.logical_path) or "/"
        self._ensure_chain(document.workspace_id, parent)
        node_key = (document.workspace_id, parent)
        node = self._nodes[node_key]
        documents = tuple(sorted(set(node.contained_documents) | {document.resource_id}))
        self._nodes[node_key] = replace(node, contained_documents=documents, dirty_summary=True)
        self._mark_ancestors_dirty(document.workspace_id, parent)

    def _detach(self, document: IndexDocument) -> None:
        parent = parent_logical_path(document.logical_path) or "/"
        node_key = (document.workspace_id, parent)
        node = self._nodes.get(node_key)
        if node is not None:
            documents = tuple(item for item in node.contained_documents if item != document.resource_id)
            self._nodes[node_key] = replace(node, contained_documents=documents, dirty_summary=True)
        self._mark_ancestors_dirty(document.workspace_id, parent)

    def _ensure_chain(self, workspace_id: str, path: str) -> None:
        normalized = normalize_logical_path(path)
        chain: list[str] = []
        current: str | None = normalized
        while current is not None:
            chain.append(current)
            current = parent_logical_path(current)
        for directory_path in reversed(chain):
            key = (workspace_id, directory_path)
            if key not in self._nodes:
                self._nodes[key] = DirectoryNode(
                    path=directory_path,
                    workspace_id=workspace_id,
                    parent_path=parent_logical_path(directory_path),
                    child_paths=(),
                    contained_documents=(),
                    abstract="",
                    overview="",
                    content_hash=self._hash_node(directory_path, (), ()),
                    index_version=self.index_version,
                    dirty_summary=True,
                )
            parent = parent_logical_path(directory_path)
            if parent is not None:
                parent_key = (workspace_id, parent)
                parent_node = self._nodes[parent_key]
                children = tuple(sorted(set(parent_node.child_paths) | {directory_path}))
                self._nodes[parent_key] = replace(
                    parent_node,
                    child_paths=children,
                    content_hash=self._hash_node(parent, children, parent_node.contained_documents),
                    dirty_summary=True,
                )

    def _mark_ancestors_dirty(self, workspace_id: str, path: str) -> None:
        current: str | None = normalize_logical_path(path)
        while current is not None:
            key = (workspace_id, current)
            node = self._nodes.get(key)
            if node is not None:
                self._nodes[key] = replace(
                    node,
                    content_hash=self._hash_node(current, node.child_paths, node.contained_documents),
                    dirty_summary=True,
                )
            current = parent_logical_path(current)

    @staticmethod
    def _hash_node(path: str, children: tuple[str, ...], documents: tuple[str, ...]) -> str:
        payload = "\n".join((path, *children, *documents))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
