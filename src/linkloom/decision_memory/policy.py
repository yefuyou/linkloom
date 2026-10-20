"""Deterministic write decisions for temporal business memory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import DecisionRecord


class DecisionWriteAction(StrEnum):
    KEEP_CANDIDATE = "KEEP_CANDIDATE"
    NO_OP = "NO_OP"
    ACTIVATE = "ACTIVATE"
    SUPERSEDE = "SUPERSEDE"


@dataclass(frozen=True, slots=True)
class DecisionWriteAssessment:
    action: DecisionWriteAction
    reason: str


@dataclass(frozen=True, slots=True)
class DecisionWriteResult:
    candidate_id: str
    action: DecisionWriteAction
    reason: str
    decision: DecisionRecord | None


class DecisionMemoryWritePolicy:
    """Select a safe, idempotent outcome without mutating the store."""

    def evaluate(
        self,
        *,
        current_value: str | None,
        proposed_value: str,
        workspace_resolved: bool,
        subject_resolved: bool,
        episode_resolved: bool,
        evidence_resolved: bool,
        team_decision_contract_pass: bool,
        grounding_pass: bool,
    ) -> DecisionWriteAssessment:
        if not team_decision_contract_pass or not grounding_pass:
            return DecisionWriteAssessment(
                DecisionWriteAction.KEEP_CANDIDATE,
                "write_gate_not_satisfied",
            )
        if not workspace_resolved:
            return DecisionWriteAssessment(
                DecisionWriteAction.KEEP_CANDIDATE,
                "workspace_unresolved",
            )
        if not subject_resolved:
            return DecisionWriteAssessment(
                DecisionWriteAction.KEEP_CANDIDATE,
                "subject_unresolved",
            )
        if not episode_resolved or not evidence_resolved:
            return DecisionWriteAssessment(
                DecisionWriteAction.KEEP_CANDIDATE,
                "insufficient_evidence",
            )
        if current_value is not None and current_value.strip() == proposed_value.strip():
            return DecisionWriteAssessment(DecisionWriteAction.NO_OP, "same_value")
        if current_value is None:
            return DecisionWriteAssessment(DecisionWriteAction.ACTIVATE, "new_subject")
        return DecisionWriteAssessment(DecisionWriteAction.SUPERSEDE, "value_changed")


__all__ = [
    "DecisionMemoryWritePolicy",
    "DecisionWriteAction",
    "DecisionWriteAssessment",
    "DecisionWriteResult",
]
