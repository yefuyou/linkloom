"""Fail-closed, explicitly approved writes into temporal decision memory."""

from __future__ import annotations

from linkloom.decision_memory.models import ActionRecord, DecisionCandidate, DecisionRecord
from linkloom.decision_memory.store import TemporalDecisionStore


class DecisionMaterializer:
    def __init__(self, store: TemporalDecisionStore) -> None:
        self.store = store

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
        return DecisionCandidate(
            decision=decision,
            actions=actions,
            team_decision_contract_pass=True,
            grounding_pass=True,
        )

    def materialize(self, candidate: DecisionCandidate, *, approved_by: str) -> DecisionRecord:
        return self.store._materialize_candidate(candidate, approved_by=approved_by)
