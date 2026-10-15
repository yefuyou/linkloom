"""Fail-closed, explicitly approved writes into temporal decision memory."""

from __future__ import annotations

from linkloom.decision_memory.models import ActionRecord, DecisionCandidate, DecisionRecord
from linkloom.decision_memory.policy import (
    DecisionMemoryWritePolicy,
    DecisionWriteAction,
    DecisionWriteResult,
)
from linkloom.decision_memory.store import TemporalDecisionStore


class DecisionMaterializer:
    def __init__(
        self,
        store: TemporalDecisionStore,
        *,
        policy: DecisionMemoryWritePolicy | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or DecisionMemoryWritePolicy()

    def propose(
        self,
        decision: DecisionRecord,
        *,
        actions: tuple[ActionRecord, ...] = (),
        team_decision_contract_pass: bool,
        grounding_pass: bool,
    ) -> DecisionCandidate:
        if not team_decision_contract_pass:
            raise ValueError("TeamDecision Contract PASS is required for a memory candidate")
        if not grounding_pass:
            raise ValueError("Grounding PASS is required for a memory candidate")
        candidate = DecisionCandidate(
            decision=decision,
            actions=actions,
            team_decision_contract_pass=True,
            grounding_pass=True,
        )
        self.store._register_candidate(candidate)
        return candidate

    def materialize(self, candidate: DecisionCandidate, *, approved_by: str) -> DecisionRecord:
        if not approved_by.strip():
            raise PermissionError("explicit approval is required to materialize decision memory")
        result = self.materialize_with_policy(candidate, approved_by=approved_by)
        if result.action is DecisionWriteAction.KEEP_CANDIDATE:
            # Preserve precise provenance validation errors for callers of the
            # original API while still routing every successful write through
            # the deterministic policy.
            self.store._source_registry.validate_decision(candidate.decision)
            if result.reason == "supersession_not_chronological":
                raise ValueError("superseding decision must start after the current decision")
            if result.reason == "supersession_target_unresolved":
                raise ValueError("superseded decision does not exist for this workspace and subject")
            raise ValueError(f"decision candidate retained: {result.reason}")
        if result.decision is None:
            raise RuntimeError("materialization policy returned no decision for a write outcome")
        return result.decision

    def materialize_with_policy(
        self,
        candidate: DecisionCandidate,
        *,
        approved_by: str,
    ) -> DecisionWriteResult:
        return self.store._materialize_candidate_with_policy(
            candidate,
            approved_by=approved_by,
            policy=self.policy,
        )
