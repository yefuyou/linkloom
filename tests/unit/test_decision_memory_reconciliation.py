from __future__ import annotations

from datetime import UTC, datetime

from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.models import DecisionMemoryState, DecisionRecord, DecisionStatus
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore


_AT = datetime(2026, 5, 1, tzinfo=UTC)
_REF_A = "notes/decision.md#L1"
_REF_B = "notes/backup.md#L2"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _registry(*, two_refs: bool = False) -> SourceReferenceRegistry:
    evidence = {_REF_A, _REF_B} if two_refs else {_REF_A}
    hashes = {_REF_A: _HASH_A}
    if two_refs:
        hashes[_REF_B] = _HASH_B
    return SourceReferenceRegistry(
        episodes={"borealis": {"episode:decision-a"}},
        evidence={"borealis": evidence},
        evidence_hashes={"borealis": hashes},
    )


def _decision(*, refs: tuple[str, ...] = (_REF_A,)) -> DecisionRecord:
    return DecisionRecord(
        decision_id="decision-a",
        workspace_id="borealis",
        subject_key="supplier",
        value="Supplier A",
        status=DecisionStatus.CURRENT,
        valid_from=_AT,
        valid_to=None,
        supersedes_id=None,
        source_episode_id="episode:decision-a",
        source_evidence_refs=refs,
        provenance_run_id="run:decision-a",
    )


def _materialize(store: TemporalDecisionStore, decision: DecisionRecord) -> None:
    materializer = DecisionMaterializer(store)
    materializer.materialize(
        materializer.propose(
            decision,
            team_decision_contract_pass=True,
            grounding_pass=True,
        ),
        approved_by="reviewer:test",
    )


def test_reconcile_marks_deleted_sole_source_stale_and_hides_current_truth() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry())
    _materialize(store, _decision())

    report = store.reconcile({"borealis": {}})

    stored = store.get_decision("borealis", "decision-a")
    assert stored is not None and stored.memory_state is DecisionMemoryState.STALE
    assert store.get_current("borealis", "supplier") is None
    assert "missing_source_reference" in {issue.code for issue in report.issues}


def test_reconcile_restores_current_truth_when_source_is_revalidated() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry())
    _materialize(store, _decision())
    store.reconcile({"borealis": {}})

    report = store.reconcile({"borealis": {_REF_A: _HASH_A}})

    stored = store.get_decision("borealis", "decision-a")
    assert stored is not None and stored.memory_state is DecisionMemoryState.ACTIVE
    assert store.get_current("borealis", "supplier") == stored
    assert report.state_transitions[0].to_state == DecisionMemoryState.ACTIVE.value


def test_reconcile_marks_sole_changed_source_stale() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry())
    _materialize(store, _decision())

    report = store.reconcile({"borealis": {_REF_A: "c" * 64}})

    stored = store.get_decision("borealis", "decision-a")
    assert stored is not None and stored.memory_state is DecisionMemoryState.STALE
    assert store.get_current("borealis", "supplier") is None
    assert "changed_source_reference" in {issue.code for issue in report.issues}


def test_reconcile_marks_alternate_evidence_needs_revalidation() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry(two_refs=True))
    _materialize(store, _decision(refs=(_REF_A, _REF_B)))

    report = store.reconcile(
        {"borealis": {_REF_A: _HASH_A, _REF_B: "c" * 64}}
    )

    stored = store.get_decision("borealis", "decision-a")
    assert stored is not None and stored.memory_state is DecisionMemoryState.NEEDS_REVALIDATION
    assert store.get_current("borealis", "supplier") is None
    assert "changed_source_reference" in {issue.code for issue in report.issues}


def test_reconcile_invalidates_historical_decisions_with_missing_sources() -> None:
    registry = SourceReferenceRegistry(
        episodes={"borealis": {"episode:decision-a", "episode:decision-b"}},
        evidence={"borealis": {_REF_A, _REF_B}},
        evidence_hashes={"borealis": {_REF_A: _HASH_A, _REF_B: _HASH_B}},
    )
    store = TemporalDecisionStore(":memory:", source_registry=registry)
    materializer = DecisionMaterializer(store)

    first = DecisionRecord(
        decision_id="decision-a",
        workspace_id="borealis",
        subject_key="supplier",
        value="Supplier A",
        status=DecisionStatus.CURRENT,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=None,
        supersedes_id=None,
        source_episode_id="episode:decision-a",
        source_evidence_refs=(_REF_A,),
        provenance_run_id="run:decision-a",
    )
    second = DecisionRecord(
        decision_id="decision-b",
        workspace_id="borealis",
        subject_key="supplier",
        value="Supplier B",
        status=DecisionStatus.CURRENT,
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_to=None,
        supersedes_id=None,
        source_episode_id="episode:decision-b",
        source_evidence_refs=(_REF_B,),
        provenance_run_id="run:decision-b",
    )
    for decision in (first, second):
        candidate = materializer.propose(
            decision,
            team_decision_contract_pass=True,
            grounding_pass=True,
        )
        materializer.materialize(candidate, approved_by="reviewer:test")

    report = store.reconcile({"borealis": {_REF_B: _HASH_B}})

    historical = store.get_decision("borealis", "decision-a")
    current = store.get_decision("borealis", "decision-b")
    assert historical is not None and historical.memory_state is DecisionMemoryState.STALE
    assert current is not None and current.memory_state is DecisionMemoryState.ACTIVE
    assert store.get_as_of("borealis", "supplier", datetime(2026, 5, 15, tzinfo=UTC)) is None
    assert store.get_as_of("borealis", "supplier", datetime(2026, 6, 15, tzinfo=UTC)) == current
    assert any(
        issue.code == "missing_source_reference" and issue.resource_id == "decision-a"
        for issue in report.issues
    )

    repair_report = store.reconcile(
        {"borealis": {_REF_A: _HASH_A, _REF_B: _HASH_B}}
    )
    repaired_historical = store.get_decision("borealis", "decision-a")
    assert repaired_historical is not None
    assert repaired_historical.memory_state is DecisionMemoryState.SUPERSEDED
    assert store.get_as_of(
        "borealis", "supplier", datetime(2026, 5, 15, tzinfo=UTC)
    ) == repaired_historical
    assert any(
        transition.entity_id == "decision-a"
        and transition.from_state == DecisionMemoryState.STALE.value
        and transition.to_state == DecisionMemoryState.SUPERSEDED.value
        for transition in repair_report.state_transitions
    )


def test_reconcile_detects_duplicate_current_broken_chain_and_orphan_action() -> None:
    store = TemporalDecisionStore(":memory:", source_registry=_registry())
    store._connection.execute("DROP INDEX ux_decision_current_subject")
    store._connection.execute("PRAGMA foreign_keys = OFF")
    at = "2026-05-01T00:00:00.000000+00:00"
    for decision_id, value, supersedes in (
        ("raw-a", "Supplier A", None),
        ("raw-b", "Supplier B", "missing-parent"),
    ):
        store._connection.execute(
            """
            INSERT INTO decision (
                decision_id, workspace_id, subject_key, value, status, memory_state,
                valid_from, valid_to, supersedes_id, source_episode_id, provenance_run_id
            ) VALUES (?, 'borealis', 'supplier', ?, 'CURRENT', 'ACTIVE', ?, NULL, ?, ?, ?)
            """,
            (decision_id, value, at, supersedes, f"episode:{decision_id}", f"run:{decision_id}"),
        )
        store._connection.execute(
            "INSERT INTO decision_evidence (decision_id, ordinal, evidence_ref) VALUES (?, 0, ?)",
            (decision_id, _REF_A),
        )
    store._connection.execute(
        """
        INSERT INTO action (
            action_id, workspace_id, description, owner, deadline, status, source_decision_id
        ) VALUES ('orphan-action', 'borealis', 'Orphan', 'Rina', NULL, 'OPEN', 'absent-decision')
        """
    )

    report = store.reconcile({"borealis": {_REF_A: _HASH_A}})

    codes = {issue.code for issue in report.issues}
    assert "duplicate_current_truth" in codes
    assert "broken_supersession_chain" in codes
    assert "orphan_action" in codes
