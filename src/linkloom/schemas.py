"""Data contracts and schemas for Linkloom P1 Walking Skeleton."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class NoteRef:
    relative_path: str
    content_sha256: str
    index_schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    relative_path: str
    content_sha256: str
    line_start: int
    line_end: int
    quote: str
    quote_sha256: str
    source_kind: str  # "note_body" | "heading" | "tag" | "wikilink"
    reason: str
    status: str = "verified"  # "verified" | "stale" | "invalid"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NoteDocument:
    relative_path: str
    title: str
    content: str
    headings: list[dict[str, Any]]
    tags: list[str]
    wikilinks: list[str]
    size_bytes: int
    content_sha256: str
    line_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScoreBreakdown:
    kind: str
    value: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidatePair:
    candidate_id: str
    left: dict[str, str]
    right: dict[str, str]
    rank_score: float
    score_breakdown: list[dict[str, Any]]
    evidence: list[str]
    reason: str
    provenance: str = "system_observation"
    status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AskAnswer:
    text: str
    kind: str = "extractive_summary"
    provenance: str = "system_observation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceContext:
    index_sha256: str
    index_schema_version: int = 1
    vault_root_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AskResult:
    request_id: str
    run_id: str
    status: str  # "completed" | "no_evidence" | "failed"
    query: str
    answer: dict[str, Any]
    evidence: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    source: dict[str, Any]
    schema_version: int = 1
    result_type: str = "ask"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectResult:
    request_id: str
    run_id: str
    status: str  # "completed" | "no_candidates" | "failed"
    query: str
    candidates: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    result_type: str = "connect"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WalkingSkeletonRun:
    run_id: str
    mode: str
    started_at: str
    finished_at: str
    source: dict[str, Any]
    result_ref: str
    source_files_changed: bool = False
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
