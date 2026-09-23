"""Core contracts for LinkLoom's logical context filesystem."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath


class ResourceType(StrEnum):
    DIRECTORY = "directory"
    DOCUMENT = "document"


class ChangeType(StrEnum):
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    MOVE = "MOVE"


def normalize_logical_path(path: str) -> str:
    """Return a canonical absolute POSIX path without allowing traversal."""
    candidate = path.replace("\\", "/").strip()
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    parts = [part for part in candidate.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("logical paths must not contain '..'")
    return "/" + "/".join(parts) if parts else "/"


def parent_logical_path(path: str) -> str | None:
    normalized = normalize_logical_path(path)
    if normalized == "/":
        return None
    parent = str(PurePosixPath(normalized).parent)
    return parent if parent != "." else "/"


@dataclass(frozen=True, slots=True)
class ContextResource:
    workspace_id: str
    resource_id: str
    document_id: str | None
    logical_path: str
    parent_path: str | None
    version: int
    source_timestamp: datetime
    content_hash: str
    source_ref: str
    resource_type: ResourceType
    indexed_at: datetime
    index_version: int
    active: bool = True

    def __post_init__(self) -> None:
        normalized = normalize_logical_path(self.logical_path)
        object.__setattr__(self, "logical_path", normalized)
        object.__setattr__(self, "parent_path", parent_logical_path(normalized))
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.resource_id.strip():
            raise ValueError("resource_id is required")
        if self.version < 1 or self.index_version < 1:
            raise ValueError("versions must be positive")

    @classmethod
    def directory(
        cls,
        *,
        workspace_id: str,
        resource_id: str,
        logical_path: str,
        source_timestamp: datetime | None = None,
        index_version: int = 1,
    ) -> ContextResource:
        timestamp = source_timestamp or datetime.now(UTC)
        normalized = normalize_logical_path(logical_path)
        return cls(
            workspace_id=workspace_id,
            resource_id=resource_id,
            document_id=None,
            logical_path=normalized,
            parent_path=parent_logical_path(normalized),
            version=1,
            source_timestamp=timestamp,
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            source_ref=f"contextfs://{workspace_id}{normalized}",
            resource_type=ResourceType.DIRECTORY,
            indexed_at=timestamp,
            index_version=index_version,
        )

    def with_workspace(self, workspace_id: str) -> ContextResource:
        return replace(self, workspace_id=workspace_id)


@dataclass(frozen=True, slots=True)
class SourceInventoryEntry:
    workspace_id: str
    resource_id: str
    logical_path: str
    content_hash: str
    source_timestamp: datetime
    source_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_path", normalize_logical_path(self.logical_path))


@dataclass(frozen=True, slots=True)
class ManifestChange:
    change_type: ChangeType
    workspace_id: str
    resource_id: str
    logical_path: str
    old_path: str | None = None


@dataclass(frozen=True, slots=True)
class FileChangeEvent:
    event_type: ChangeType
    workspace_id: str
    logical_path: str
    old_path: str | None = None
    content_hash: str | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_path", normalize_logical_path(self.logical_path))
        if self.old_path is not None:
            object.__setattr__(self, "old_path", normalize_logical_path(self.old_path))
        if self.event_type is ChangeType.MOVE and self.old_path is None:
            raise ValueError("MOVE events require old_path")
