"""Shared contracts for lexical, vector, and directory indexes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from linkloom.context import normalize_logical_path


@dataclass(frozen=True, slots=True)
class IndexDocument:
    workspace_id: str
    resource_id: str
    evidence_id: str
    logical_path: str
    content: str
    source_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_path", normalize_logical_path(self.logical_path))


@dataclass(frozen=True, slots=True)
class IndexSearchResult:
    workspace_id: str
    resource_id: str
    evidence_id: str
    logical_path: str
    source_ref: str
    score: float
    metadata: dict[str, Any]
