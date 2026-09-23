"""Contracts for LinkLoom's temporal decision relation index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class DecisionStatus(StrEnum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _evidence_refs(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one source evidence reference")
    return normalized


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    workspace_id: str
    subject_key: str
    value: str
    status: DecisionStatus
    valid_from: datetime
    valid_to: datetime | None
    supersedes_id: str | None
    source_episode_id: str
    source_evidence_refs: tuple[str, ...]
    provenance_run_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "decision_id",
            "workspace_id",
            "subject_key",
            "value",
            "source_episode_id",
            "provenance_run_id",
        ):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(self, "valid_from", _utc(self.valid_from, "valid_from"))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _utc(self.valid_to, "valid_to"))
        if self.status is DecisionStatus.CURRENT and self.valid_to is not None:
            raise ValueError("a current decision cannot have valid_to")
        if self.status is DecisionStatus.SUPERSEDED and self.valid_to is None:
            raise ValueError("a superseded decision requires valid_to")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        if self.supersedes_id is not None:
            object.__setattr__(
                self,
                "supersedes_id",
                _required_text(self.supersedes_id, "supersedes_id"),
            )
        object.__setattr__(
            self,
            "source_evidence_refs",
            _evidence_refs(self.source_evidence_refs, "source_evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: str
    workspace_id: str
    description: str
    owner: str
    deadline: str | None
    status: str
    source_decision_id: str
    source_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "action_id",
            "workspace_id",
            "description",
            "owner",
            "status",
            "source_decision_id",
        ):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        if self.deadline is not None:
            object.__setattr__(self, "deadline", _required_text(self.deadline, "deadline"))
        object.__setattr__(
            self,
            "source_evidence_refs",
            _evidence_refs(self.source_evidence_refs, "source_evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class DecisionEvolution:
    previous_decision_id: str
    current_decision_id: str
    workspace_id: str
    subject_key: str
    from_value: str
    to_value: str
    changed_at: datetime
    source_evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionCandidate:
    decision: DecisionRecord
    actions: tuple[ActionRecord, ...]
    team_decision_contract_pass: bool
    grounding_pass: bool
