from __future__ import annotations

from datetime import UTC, datetime

import pytest

from linkloom.decision_memory.models import DecisionMemoryState, DecisionStatus
from linkloom.decision_memory.policy import (
    DecisionMemoryWritePolicy,
    DecisionWriteAction,
)
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.models import DecisionRecord


def _decision(
    decision_id: str,
    value: str,
    *,
    supersedes_id: str | None = None,
    valid_from: datetime = datetime(2026, 5, 1, tzinfo=UTC),
    evidence: tuple[str, ...] = ("notes/decision.md#L1",),
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        workspace_id="borealis",
        subject_key="supplier",
        value=value,
        status=DecisionStatus.CURRENT,
        valid_from=valid_from,
        valid_to=None,
        supersedes_id=supersedes_id,
        source_episode_id=f"episode:{decision_id}",
        source_evidence_refs=evidence,
        provenance_run_id=f"run:{decision_id}",
    )


def _registry() -> SourceReferenceRegistry:
    return SourceReferenceRegistry(
        episodes={"borealis": {"episode:decision-a", "episode:decision-b", "episode:decision-c"}},
        evidence={"borealis": {"notes/decision.md#L1"}},
    )


def test_write_policy_keeps_candidate_when_workspace_or_evidence_is_unresolved() -> None:
    policy = DecisionMemoryWritePolicy()

    result = policy.evaluate(
        current_value="Supplier A",
        proposed_value="Supplier B",
        workspace_resolved=True,
        subject_resolved=True,
        episode_resolved=True,
        evidence_resolved=False,
        team_decision_contract_pass=True,
        grounding_pass=True,
    )

    assert result.action is DecisionWriteAction.KEEP_CANDIDATE
    assert result.reason == "insufficient_evidence"


def test_write_policy_is_idempotent_for_same_value_and_supersedes_changed_value() -> None:
    policy = DecisionMemoryWritePolicy()
    common = {
        "workspace_resolved": True,
        "subject_resolved": True,
        "episode_resolved": True,
        "evidence_resolved": True,
        "team_decision_contract_pass": True,
        "grounding_pass": True,
    }

    same = policy.evaluate(current_value="Supplier A", proposed_value="Supplier A", **common)
    changed = policy.evaluate(current_value="Supplier A", proposed_value="Supplier B", **common)
    new = policy.evaluate(current_value=None, proposed_value="Supplier A", **common)

    assert same.action is DecisionWriteAction.NO_OP
    assert changed.action is DecisionWriteAction.SUPERSEDE
    assert new.action is DecisionWriteAction.ACTIVATE


def test_materializer_persists_candidate_and_policy_outcomes_without_duplicate_truths() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry())
    materializer = DecisionMaterializer(store)
    first_candidate = materializer.propose(
        _decision("decision-a", "Supplier A"),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    first_result = materializer.materialize_with_policy(first_candidate, approved_by="reviewer:test")

    assert first_result.action is DecisionWriteAction.ACTIVATE
    assert first_result.decision is not None
    assert first_candidate.candidate_id
    assert first_candidate.workspace_id == "borealis"
    assert first_candidate.subject_key == "supplier"
    assert first_candidate.proposed_value == "Supplier A"
    assert first_candidate.source_evidence_refs == ("notes/decision.md#L1",)
    assert first_candidate.provenance_run_id == "run:decision-a"
    assert first_candidate.source_run_id == "run:decision-a"
    assert first_candidate.created_at.tzinfo is not None
    assert store.get_candidate_state(first_candidate.candidate_id) is DecisionMemoryState.ACTIVE

    duplicate_candidate = materializer.propose(
        _decision("decision-b", "Supplier A", valid_from=datetime(2026, 6, 1, tzinfo=UTC)),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    duplicate = materializer.materialize_with_policy(duplicate_candidate, approved_by="reviewer:test")
    assert duplicate.action is DecisionWriteAction.NO_OP
    assert duplicate.decision == first_result.decision
    assert len(store.get_history("borealis", "supplier")) == 1

    changed_candidate = materializer.propose(
        _decision("decision-c", "Supplier B", valid_from=datetime(2026, 7, 1, tzinfo=UTC)),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    changed = materializer.materialize_with_policy(changed_candidate, approved_by="reviewer:test")
    assert changed.action is DecisionWriteAction.SUPERSEDE
    assert changed.decision is not None and changed.decision.supersedes_id == "decision-a"
    assert store.get_current("borealis", "supplier").decision_id == "decision-c"
    assert [record.decision_id for record in store.get_history("borealis", "supplier")] == [
        "decision-a",
        "decision-c",
    ]
    assert store.get_candidate_state(first_candidate.candidate_id) is DecisionMemoryState.SUPERSEDED
    assert store.get_candidate_state(changed_candidate.candidate_id) is DecisionMemoryState.ACTIVE


def test_public_materialize_entry_point_cannot_bypass_no_op_policy() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry())
    materializer = DecisionMaterializer(store)
    first = materializer.propose(
        _decision("decision-a", "Supplier A"),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    stored_first = materializer.materialize(first, approved_by="reviewer:test")
    duplicate = materializer.propose(
        _decision("decision-b", "Supplier A", valid_from=datetime(2026, 6, 1, tzinfo=UTC)),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )

    stored_duplicate = materializer.materialize(duplicate, approved_by="reviewer:test")

    assert stored_duplicate == stored_first
    assert [item.decision_id for item in store.get_history("borealis", "supplier")] == [
        "decision-a"
    ]


def test_unresolved_source_keeps_candidate_pending_for_later_revalidation() -> None:
    registry = SourceReferenceRegistry(episodes={"borealis": {"episode:decision-a"}})
    store = TemporalDecisionStore(":memory:", source_registry=registry)
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(
        _decision("decision-a", "Supplier A"),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )

    result = materializer.materialize_with_policy(candidate, approved_by="reviewer:test")

    assert result.action is DecisionWriteAction.KEEP_CANDIDATE
    assert result.decision is None
    assert store.get_candidate_state(candidate.candidate_id) is DecisionMemoryState.CANDIDATE


def test_source_hash_change_after_candidate_creation_blocks_materialization() -> None:
    registry = SourceReferenceRegistry(
        episodes={"borealis": {"episode:decision-a"}},
        evidence={"borealis": {"notes/decision.md#L1"}},
        evidence_hashes={"borealis": {"notes/decision.md#L1": "a" * 64}},
    )
    store = TemporalDecisionStore(":memory:", source_registry=registry)
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(
        _decision("decision-a", "Supplier A"),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    registry.register_evidence(
        "borealis", "notes/decision.md#L1", content_hash="c" * 64
    )

    with pytest.raises(ValueError, match="source_changed_before_materialization"):
        materializer.materialize(candidate, approved_by="reviewer:test")

    assert store.get_candidate_state(candidate.candidate_id) is DecisionMemoryState.INVALIDATED
    assert store.get_current("borealis", "supplier") is None


@pytest.mark.parametrize(
    ("refs", "invalidator", "expected_state"),
    (
        (("notes/decision.md#L1",), "changed_hash", DecisionMemoryState.INVALIDATED),
        (
            ("notes/decision.md#L1", "notes/backup.md#L2"),
            "changed_hash",
            DecisionMemoryState.NEEDS_REVALIDATION,
        ),
        (("notes/decision.md#L1",), "reconciled_missing", DecisionMemoryState.STALE),
    ),
)
def test_terminal_candidate_requires_a_new_candidate_after_source_repair(
    refs: tuple[str, ...],
    invalidator: str,
    expected_state: DecisionMemoryState,
) -> None:
    hashes = {ref: ("a" if index == 0 else "b") * 64 for index, ref in enumerate(refs)}
    registry = SourceReferenceRegistry(
        episodes={"borealis": {"episode:decision-a"}},
        evidence={"borealis": set(refs)},
        evidence_hashes={"borealis": hashes},
    )
    store = TemporalDecisionStore(":memory:", source_registry=registry)
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(
        _decision("decision-a", "Supplier A", evidence=refs),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )

    if invalidator == "reconciled_missing":
        store.reconcile({"borealis": {}})
    else:
        registry.register_evidence("borealis", refs[0], content_hash="c" * 64)
        blocked = materializer.materialize_with_policy(candidate, approved_by="reviewer:test")
        assert blocked.action is DecisionWriteAction.KEEP_CANDIDATE
        assert blocked.reason == "source_changed_before_materialization"
    assert store.get_candidate_state(candidate.candidate_id) is expected_state

    # Even if the old hash becomes available again, the same persisted candidate
    # cannot silently regain write eligibility.
    registry.register_evidence("borealis", refs[0], content_hash=hashes[refs[0]])
    retry = materializer.materialize_with_policy(candidate, approved_by="reviewer:test")

    assert retry.action is DecisionWriteAction.KEEP_CANDIDATE
    assert retry.reason == "candidate_requires_revalidation"
    assert store.get_candidate_state(candidate.candidate_id) is expected_state
    assert store.get_current("borealis", "supplier") is None

    # A fresh candidate captures the repaired source snapshot and is the explicit
    # revalidation boundary.
    fresh_candidate = materializer.propose(
        _decision("decision-a", "Supplier A", evidence=refs),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    accepted = materializer.materialize_with_policy(
        fresh_candidate,
        approved_by="reviewer:test",
    )
    assert accepted.action is DecisionWriteAction.ACTIVATE
    assert store.get_candidate_state(fresh_candidate.candidate_id) is DecisionMemoryState.ACTIVE
