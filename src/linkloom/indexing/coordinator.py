"""Event-driven coordination across manifest and affected context indexes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from linkloom.context import (
    ChangeType,
    ContextManifest,
    ContextResource,
    FileChangeEvent,
    ResourceType,
    normalize_logical_path,
    parent_logical_path,
)
from linkloom.indexing.bm25 import BM25Index
from linkloom.indexing.directory import DirectoryIndex
from linkloom.indexing.models import IndexDocument
from linkloom.indexing.vector import VectorIndex


@dataclass(frozen=True, slots=True)
class SourceDocument:
    workspace_id: str
    resource_id: str
    document_id: str
    logical_path: str
    content: str
    source_timestamp: datetime
    source_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_path", normalize_logical_path(self.logical_path))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


class UpdateStatus(StrEnum):
    APPLIED = "APPLIED"
    NO_OP = "NO_OP"


@dataclass(frozen=True, slots=True)
class IndexUpdateResult:
    status: UpdateStatus
    workspace_id: str
    resource_id: str | None
    affected_indexes: tuple[str, ...]
    reason: str


class IndexUpdateCoordinator:
    def __init__(
        self,
        *,
        manifest: ContextManifest,
        lexical_index: BM25Index,
        vector_index: VectorIndex,
        directory_index: DirectoryIndex,
        index_version: int = 1,
    ) -> None:
        self.manifest = manifest
        self.lexical_index = lexical_index
        self.vector_index = vector_index
        self.directory_index = directory_index
        self.index_version = index_version

    def create(self, source: SourceDocument) -> IndexUpdateResult:
        return self.apply(
            FileChangeEvent(
                event_type=ChangeType.CREATE,
                workspace_id=source.workspace_id,
                logical_path=source.logical_path,
                content_hash=source.content_hash,
                timestamp=source.source_timestamp,
            ),
            source,
        )

    def apply(self, event: FileChangeEvent, source: SourceDocument | None = None) -> IndexUpdateResult:
        if event.event_type is ChangeType.CREATE:
            return self._create(event, self._require_source(event, source))
        if event.event_type is ChangeType.MODIFY:
            return self._modify(event, self._require_source(event, source))
        if event.event_type is ChangeType.DELETE:
            return self._delete(event)
        if event.event_type is ChangeType.MOVE:
            return self._move(event, source)
        raise ValueError(f"unsupported event type: {event.event_type}")

    def index_document(self, source: SourceDocument) -> IndexDocument:
        return IndexDocument(
            workspace_id=source.workspace_id,
            resource_id=source.resource_id,
            evidence_id=f"ctx:{source.workspace_id}:{source.resource_id}",
            logical_path=source.logical_path,
            content=source.content,
            source_ref=source.source_ref,
            metadata={**source.metadata, "content_hash": source.content_hash},
        )

    def _create(self, event: FileChangeEvent, source: SourceDocument) -> IndexUpdateResult:
        existing = self.manifest.get_resource(source.workspace_id, source.resource_id)
        if existing is not None and existing.active:
            if existing.content_hash == source.content_hash and existing.logical_path == source.logical_path:
                return self._no_op(source.workspace_id, source.resource_id, "resource already current")
            return self._modify(event, source)
        indexed_at = datetime.now(UTC)
        resource = ContextResource(
            workspace_id=source.workspace_id,
            resource_id=source.resource_id,
            document_id=source.document_id,
            logical_path=source.logical_path,
            parent_path=parent_logical_path(source.logical_path),
            version=(existing.version + 1) if existing else 1,
            source_timestamp=source.source_timestamp,
            content_hash=source.content_hash,
            source_ref=source.source_ref,
            resource_type=ResourceType.DOCUMENT,
            indexed_at=indexed_at,
            index_version=self.index_version,
            active=True,
        )
        document = self.index_document(source)
        self.lexical_index.upsert(document)
        self.vector_index.upsert(document)
        self.directory_index.upsert(document)
        self.manifest.upsert(resource)
        self._sync_directory_manifest(source.workspace_id, event.timestamp or source.source_timestamp)
        return IndexUpdateResult(
            UpdateStatus.APPLIED,
            source.workspace_id,
            source.resource_id,
            ("lexical", "vector", "directory", "manifest"),
            "resource created",
        )

    def _modify(self, event: FileChangeEvent, source: SourceDocument) -> IndexUpdateResult:
        current = self.manifest.get_resource(source.workspace_id, source.resource_id)
        if current is None or not current.active:
            return self._create(event, source)
        if current.content_hash == source.content_hash and current.logical_path == source.logical_path:
            return self._no_op(source.workspace_id, source.resource_id, "content hash unchanged")
        if current.logical_path != source.logical_path:
            move_event = FileChangeEvent(
                event_type=ChangeType.MOVE,
                workspace_id=source.workspace_id,
                logical_path=source.logical_path,
                old_path=current.logical_path,
                content_hash=source.content_hash,
                timestamp=event.timestamp,
            )
            return self._move(move_event, source)
        document = self.index_document(source)
        self.lexical_index.upsert(document)
        self.vector_index.upsert(document)
        self.directory_index.upsert(document)
        self.manifest.upsert(
            replace(
                current,
                version=current.version + 1,
                source_timestamp=source.source_timestamp,
                content_hash=source.content_hash,
                source_ref=source.source_ref,
                indexed_at=datetime.now(UTC),
                index_version=self.index_version,
            )
        )
        self._sync_directory_manifest(source.workspace_id, event.timestamp or source.source_timestamp)
        return IndexUpdateResult(
            UpdateStatus.APPLIED,
            source.workspace_id,
            source.resource_id,
            ("lexical", "vector", "directory", "manifest"),
            "content hash changed",
        )

    def _delete(self, event: FileChangeEvent) -> IndexUpdateResult:
        current = self.manifest.get_resource_by_path(event.workspace_id, event.logical_path)
        if current is None or not current.active:
            return self._no_op(event.workspace_id, None, "resource already absent")
        self.lexical_index.remove(event.workspace_id, current.resource_id)
        self.vector_index.remove(event.workspace_id, current.resource_id)
        self.directory_index.remove(event.workspace_id, current.resource_id)
        self.manifest.upsert(
            replace(
                current,
                version=current.version + 1,
                indexed_at=datetime.now(UTC),
                active=False,
            )
        )
        self._sync_directory_manifest(event.workspace_id, event.timestamp or datetime.now(UTC))
        return IndexUpdateResult(
            UpdateStatus.APPLIED,
            event.workspace_id,
            current.resource_id,
            ("lexical", "vector", "directory", "manifest"),
            "resource deleted and provenance retained",
        )

    def _move(self, event: FileChangeEvent, source: SourceDocument | None) -> IndexUpdateResult:
        assert event.old_path is not None
        current = self.manifest.get_resource_by_path(event.workspace_id, event.old_path)
        if current is None:
            destination = self.manifest.get_resource_by_path(event.workspace_id, event.logical_path)
            if destination is not None:
                return self._no_op(event.workspace_id, destination.resource_id, "resource already moved")
            raise KeyError(f"MOVE source does not exist: {event.workspace_id}{event.old_path}")
        existing_document = self.lexical_index.get_document(event.workspace_id, current.resource_id)
        if source is None:
            if existing_document is None:
                raise KeyError(f"MOVE source is missing from lexical index: {current.resource_id}")
            source = SourceDocument(
                workspace_id=current.workspace_id,
                resource_id=current.resource_id,
                document_id=current.document_id or current.resource_id,
                logical_path=event.logical_path,
                content=existing_document.content,
                source_timestamp=event.timestamp or current.source_timestamp,
                source_ref=current.source_ref,
                metadata={
                    key: value
                    for key, value in existing_document.metadata.items()
                    if key != "content_hash"
                },
            )
        if source.logical_path != event.logical_path:
            raise ValueError("MOVE source logical_path must equal event logical_path")

        content_changed = source.content_hash != current.content_hash
        indexes_complete = all(
            index.get_document(event.workspace_id, current.resource_id) is not None
            for index in (self.lexical_index, self.vector_index, self.directory_index)
        )
        if content_changed or not indexes_complete:
            document = self.index_document(source)
            self.lexical_index.upsert(document)
            self.vector_index.upsert(document)
            self.directory_index.upsert(document)
        else:
            self.lexical_index.move(event.workspace_id, current.resource_id, event.logical_path)
            self.vector_index.move(event.workspace_id, current.resource_id, event.logical_path)
            self.directory_index.move(event.workspace_id, current.resource_id, event.logical_path)
        self.manifest.upsert(
            replace(
                current,
                logical_path=event.logical_path,
                parent_path=parent_logical_path(event.logical_path),
                version=current.version + 1,
                source_timestamp=source.source_timestamp,
                content_hash=source.content_hash,
                source_ref=source.source_ref,
                indexed_at=datetime.now(UTC),
                index_version=self.index_version,
            )
        )
        self._sync_directory_manifest(event.workspace_id, event.timestamp or source.source_timestamp)
        return IndexUpdateResult(
            UpdateStatus.APPLIED,
            event.workspace_id,
            current.resource_id,
            ("lexical", "vector", "directory", "manifest"),
            "resource moved with identity preserved",
        )

    def _sync_directory_manifest(self, workspace_id: str, timestamp: datetime) -> None:
        indexed_at = datetime.now(UTC)
        for node in self.directory_index.list_nodes(workspace_id):
            resource_id = f"dir:{hashlib.sha256(node.path.encode('utf-8')).hexdigest()[:20]}"
            current = self.manifest.get_resource(workspace_id, resource_id)
            if (
                current is not None
                and current.active
                and current.content_hash == node.content_hash
                and current.index_version == self.index_version
            ):
                continue
            version = 1 if current is None else current.version + int(current.content_hash != node.content_hash)
            self.manifest.upsert(
                ContextResource(
                    workspace_id=workspace_id,
                    resource_id=resource_id,
                    document_id=None,
                    logical_path=node.path,
                    parent_path=node.parent_path,
                    version=version,
                    source_timestamp=timestamp,
                    content_hash=node.content_hash,
                    source_ref=f"contextfs://{workspace_id}{node.path}",
                    resource_type=ResourceType.DIRECTORY,
                    indexed_at=indexed_at,
                    index_version=self.index_version,
                )
            )

    @staticmethod
    def _require_source(event: FileChangeEvent, source: SourceDocument | None) -> SourceDocument:
        if source is None:
            raise ValueError(f"{event.event_type} requires source content")
        if source.workspace_id != event.workspace_id or source.logical_path != event.logical_path:
            raise ValueError("event and source must share workspace_id and logical_path")
        if event.content_hash is not None and event.content_hash != source.content_hash:
            raise ValueError("event content_hash does not match source content")
        return source

    @staticmethod
    def _no_op(workspace_id: str, resource_id: str | None, reason: str) -> IndexUpdateResult:
        return IndexUpdateResult(UpdateStatus.NO_OP, workspace_id, resource_id, (), reason)
