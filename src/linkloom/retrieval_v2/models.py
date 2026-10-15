"""Provider-neutral contracts for document retrieval V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from linkloom.context import normalize_logical_path


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    workspace_id: str
    query: str
    top_k: int = 5
    optional_scope_path: str | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.optional_scope_path is not None:
            object.__setattr__(
                self,
                "optional_scope_path",
                normalize_logical_path(self.optional_scope_path),
            )


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    evidence_id: str
    source_ref: str
    logical_path: str
    score: float
    rank: int
    retrieval_channel: str
    resource_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
