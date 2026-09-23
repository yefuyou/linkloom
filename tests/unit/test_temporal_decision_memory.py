from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

import pytest

from linkloom.decision_memory import (
    ActionRecord,
    DecisionMaterializer,
    DecisionRecord,
    DecisionStatus,
    SourceReferenceRegistry,
    TemporalDecisionStore,
)
from linkloom.decision_memory.demo import build_demo_report


_FIXTURE_WORKSPACES = ("borealis", "atlas", "ws-borealis", "ws-atlas")
_FIXTURE_EVIDENCE = (
    "notes/decision.md#L1-L4",
    "notes/final-decision.md#L8-L14",
    "notes/actions.md#L3-L7",
    "notes/actions.md#L1",
    "atlas/final.md#L1-L3",
    "notes/scoped.md#L1",
    "notes/action.md#L1",
)
_FIXTURE_EPISODES = {
    f"episode:{decision_id}"
    for decision_id in (
        "decision-a",
        "decision-b",
        "decision-c",
        "decision-backward",
        "decision-equal",
        "borealis-a",
        "atlas-a",
        "borealis-target",
        "atlas-target",
        "scoped",
        "dec-v1",
        "dec-v2",
        "dec-v3",
    )
}


def _store() -> TemporalDecisionStore:
    registry = SourceReferenceRegistry(
        episodes={workspace_id: _FIXTURE_EPISODES for workspace_id in _FIXTURE_WORKSPACES},
        evidence={workspace_id: _FIXTURE_EVIDENCE for workspace_id in _FIXTURE_WORKSPACES},
    )
    return TemporalDecisionStore(":memory:", source_registry=registry)


def _materialize(
    store: TemporalDecisionStore,
    decision: DecisionRecord,
    *,
    actions: tuple[ActionRecord, ...] = (),
) -> DecisionRecord:
    materializer = DecisionMaterializer(store)
    return materializer.materialize(
        materializer.propose(
            decision,
            actions=actions,
            team_decision_contract_pass=True,
            grounding_pass=True,
        ),
        approved_by="fixture:test",
    )


def _decision(
    decision_id: str,
    value: str,
    valid_from: datetime,
    *,
    workspace_id: str = "borealis",
    subject_key: str = "supplier",
    supersedes_id: str | None = None,
    evidence: tuple[str, ...] = ("notes/decision.md#L1-L4",),
    source_episode_id: str | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        workspace_id=workspace_id,
        subject_key=subject_key,
        value=value,
        status=DecisionStatus.CURRENT,
        valid_from=valid_from,
        valid_to=None,
        supersedes_id=supersedes_id,
        source_episode_id=source_episode_id or f"episode:{decision_id}",
        source_evidence_refs=evidence,
        provenance_run_id=f"run:{decision_id}",
    )


def test_current_historical_as_of_and_supersession_evolution() -> None:
    store = _store()
    first_at = datetime(2026, 5, 12, tzinfo=UTC)
    second_at = datetime(2026, 6, 1, tzinfo=UTC)
    _materialize(store, _decision("decision-a", "Supplier A", first_at))
    _materialize(
        store,
        _decision(
            "decision-b",
            "Supplier B",
            second_at,
            supersedes_id="decision-a",
            evidence=("notes/final-decision.md#L8-L14",),
        ),
    )

    current = store.get_current("borealis", "supplier")
    history = store.get_history("borealis", "supplier")
    historical = store.get_as_of(
        "borealis",
        "supplier",
        datetime(2026, 5, 20, tzinfo=UTC),
    )
    evolution = store.get_evolution("borealis", "supplier")

    assert current is not None and current.decision_id == "decision-b"
    assert current.source_evidence_refs == ("notes/final-decision.md#L8-L14",)
    assert [item.decision_id for item in history] == ["decision-a", "decision-b"]
    assert history[0].status is DecisionStatus.SUPERSEDED
    assert history[0].valid_to == second_at
    assert historical is not None and historical.decision_id == "decision-a"
    assert len(evolution) == 1
    assert evolution[0].previous_decision_id == "decision-a"
    assert evolution[0].current_decision_id == "decision-b"
    assert evolution[0].from_value == "Supplier A"
    assert evolution[0].to_value == "Supplier B"
    assert evolution[0].changed_at == second_at
    assert evolution[0].source_evidence_refs == ("notes/final-decision.md#L8-L14",)


def test_decision_action_owner_and_evidence_relations() -> None:
    store = _store()
    decision = _decision("decision-a", "Supplier A", datetime(2026, 5, 12, tzinfo=UTC))
    action = ActionRecord(
        action_id="action-rollout",
        workspace_id="borealis",
        description="Prepare the supplier rollout plan.",
        owner="Rina",
        deadline="2026-06-15",
        status="OPEN",
        source_decision_id="decision-a",
        source_evidence_refs=("notes/actions.md#L3-L7",),
    )

    _materialize(store, decision, actions=(action,))

    stored_actions = store.get_actions("borealis", "decision-a")
    assert stored_actions == (action,)
    assert store.get_decision_evidence("borealis", "decision-a") == ("notes/decision.md#L1-L4",)
    assert store.get_action_evidence("borealis", "action-rollout") == ("notes/actions.md#L3-L7",)


def test_store_rejects_two_current_truths_and_invalid_sources() -> None:
    store = _store()
    first_at = datetime(2026, 5, 12, tzinfo=UTC)
    _materialize(store, _decision("decision-a", "Supplier A", first_at))

    with pytest.raises(ValueError, match="must explicitly supersede"):
        _materialize(
            store,
            _decision("decision-b", "Supplier B", datetime(2026, 6, 1, tzinfo=UTC))
        )
    with pytest.raises(ValueError, match="superseded decision does not exist"):
        _materialize(
            store,
            _decision(
                "decision-c",
                "Supplier C",
                datetime(2026, 6, 2, tzinfo=UTC),
                subject_key="legal-review",
                supersedes_id="missing",
            )
        )
    with pytest.raises(ValueError, match="source_evidence_refs"):
        _decision("invalid", "Unknown", first_at, evidence=())

    invalid_action = ActionRecord(
        action_id="invalid-action",
        workspace_id="borealis",
        description="Invalid source relation.",
        owner="Rina",
        deadline=None,
        status="OPEN",
        source_decision_id="other-decision",
        source_evidence_refs=("notes/actions.md#L1",),
    )
    with pytest.raises(ValueError, match="source_decision_id"):
        _materialize(
            store,
            _decision(
                "decision-b",
                "Supplier B",
                datetime(2026, 6, 1, tzinfo=UTC),
                supersedes_id="decision-a",
            ),
            actions=(invalid_action,),
        )


def test_candidate_requires_contract_grounding_and_explicit_approval() -> None:
    store = _store()
    materializer = DecisionMaterializer(store)
    decision = _decision("decision-a", "Supplier A", datetime(2026, 5, 12, tzinfo=UTC))

    with pytest.raises(ValueError, match="TeamDecision Contract PASS"):
        materializer.propose(
            decision,
            team_decision_contract_pass=False,
            grounding_pass=True,
        )
    with pytest.raises(ValueError, match="Grounding PASS"):
        materializer.propose(
            decision,
            team_decision_contract_pass=True,
            grounding_pass=False,
        )

    candidate = materializer.propose(
        decision,
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    with pytest.raises(PermissionError, match="explicit approval"):
        materializer.materialize(candidate, approved_by="")

    stored = materializer.materialize(candidate, approved_by="fixture:test")
    assert stored == decision
    assert store.get_current("borealis", "supplier") == decision


def test_workspace_scope_precedes_search_and_required_sqlite_indexes_exist() -> None:
    store = _store()
    at = datetime(2026, 5, 12, tzinfo=UTC)
    _materialize(store, _decision("borealis-a", "Supplier A", at))
    _materialize(
        store,
        _decision(
            "atlas-a",
            "Supplier Atlas",
            at,
            workspace_id="atlas",
            evidence=("atlas/final.md#L1-L3",),
        ),
    )

    assert [item.decision_id for item in store.search("borealis", "Supplier", limit=10)] == [
        "borealis-a"
    ]
    assert [item.decision_id for item in store.search("atlas", "Supplier", limit=10)] == [
        "atlas-a"
    ]
    indexes = store.schema_indexes()
    expected_indexes = {
        "idx_decision_workspace_subject": ("workspace_id", "subject_key"),
        "idx_decision_workspace_subject_validity": (
            "workspace_id",
            "subject_key",
            "valid_from",
            "valid_to",
        ),
        "idx_decision_source_episode": ("source_episode_id",),
        "ux_decision_current_subject": ("workspace_id", "subject_key"),
    }
    for name, columns in expected_indexes.items():
        assert indexes[name] == columns


def test_required_temporal_demo_answers_current_history_and_evolution() -> None:
    report = build_demo_report()

    assert report["current_question"]["answer"]["value"] == "Supplier B"
    assert report["historical_question"]["answer"]["value"] == "Supplier A"
    evolution = report["evolution_question"]["answer"]
    assert evolution["from_value"] == "Supplier A"
    assert evolution["to_value"] == "Supplier B"
    assert evolution["source_evidence_refs"] == ["decisions/supplier-decision.md#L8-L15"]
    relations = report["relations"]
    assert relations["decision_to_previous_decision"] == "supplier-a"
    assert relations["decision_to_action_to_owner"][0]["owner"] == "Rina"


def test_as_of_boundary_conditions_and_tz_enforcement() -> None:
    store = _store()
    t_a = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)
    t_b = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    _materialize(store, _decision("decision-a", "Supplier A", t_a))
    _materialize(store, _decision("decision-b", "Supplier B", t_b, supersedes_id="decision-a"))

    # 1. Before decision-a began: must return None
    assert store.get_as_of("borealis", "supplier", datetime(2026, 5, 11, 23, 59, 59, tzinfo=UTC)) is None

    # 2. Exact start of decision-a: must return decision-a
    at_a = store.get_as_of("borealis", "supplier", t_a)
    assert at_a is not None and at_a.decision_id == "decision-a"

    # 3. Transition point [valid_from, valid_to): at t_b, decision-a is expired (valid_to > ? is false), decision-b is active
    at_b = store.get_as_of("borealis", "supplier", t_b)
    assert at_b is not None and at_b.decision_id == "decision-b"

    # 4. Far future: returns current truth
    at_future = store.get_as_of("borealis", "supplier", datetime(2026, 12, 31, 0, 0, 0, tzinfo=UTC))
    assert at_future is not None and at_future.decision_id == "decision-b"

    # 5. Timezone-naive datetime must fail-fast with ValueError
    with pytest.raises(ValueError, match="timezone-aware"):
        store.get_as_of("borealis", "supplier", datetime(2026, 5, 20))

    with pytest.raises(ValueError, match="timezone-aware"):
        store.search("borealis", "Supplier", as_of=datetime(2026, 5, 20))


def test_sqlite_unique_index_and_foreign_key_enforcement() -> None:
    store = _store()
    _materialize(store, _decision("decision-a", "Supplier A", datetime(2026, 5, 12, tzinfo=UTC)))

    # Direct SQLite raw INSERT of duplicate current decision (valid_to is NULL) must trigger ux_decision_current_subject
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        store._connection.execute(
            """
            INSERT INTO decision (
                decision_id, workspace_id, subject_key, value, status,
                valid_from, valid_to, supersedes_id, source_episode_id,
                provenance_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "raw-conflict",
                "borealis",
                "supplier",
                "Raw Conflict",
                "CURRENT",
                "2026-05-15T00:00:00.000000+00:00",
                None,
                None,
                "episode:raw",
                "run:raw",
            ),
        )

    # Direct SQLite raw INSERT of action with non-existent source_decision_id must trigger FOREIGN KEY violation
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        store._connection.execute(
            """
            INSERT INTO action (
                action_id, workspace_id, description, owner, deadline,
                status, source_decision_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("action-orphan", "borealis", "Orphan Action", "Rina", None, "OPEN", "nonexistent-decision"),
        )


def test_invalid_action_workspace_and_supersession_ordering() -> None:
    store = _store()
    t_first = datetime(2026, 5, 12, tzinfo=UTC)
    _materialize(store, _decision("decision-a", "Supplier A", t_first))

    # Action workspace_id mismatch with decision workspace_id
    mismatched_action = ActionRecord(
        action_id="action-mismatch",
        workspace_id="atlas",  # decision is in borealis
        description="Mismatched workspace.",
        owner="Rina",
        deadline=None,
        status="OPEN",
        source_decision_id="decision-b",
        source_evidence_refs=("notes/action.md#L1",),
    )
    with pytest.raises(ValueError, match="action workspace_id must match its source decision"):
        _materialize(
            store,
            _decision("decision-b", "Supplier B", datetime(2026, 6, 1, tzinfo=UTC), supersedes_id="decision-a"),
            actions=(mismatched_action,),
        )

    # Superseding decision with valid_from <= current.valid_from (backward timestamp)
    with pytest.raises(ValueError, match="superseding decision must start after the current decision"):
        _materialize(
            store,
            _decision("decision-backward", "Supplier B", datetime(2026, 5, 1, tzinfo=UTC), supersedes_id="decision-a")
        )

    # Superseding decision with valid_from == current.valid_from (equal timestamp)
    with pytest.raises(ValueError, match="superseding decision must start after the current decision"):
        _materialize(
            store,
            _decision("decision-equal", "Supplier B", t_first, supersedes_id="decision-a")
        )

    # Materialization approval string consisting solely of whitespace
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(
        _decision("decision-c", "Supplier C", datetime(2026, 6, 1, tzinfo=UTC), supersedes_id="decision-a"),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    with pytest.raises(PermissionError, match="explicit approval is required"):
        materializer.materialize(candidate, approved_by="   ")


def test_comprehensive_workspace_isolation() -> None:
    store = _store()
    t = datetime(2026, 5, 12, tzinfo=UTC)
    # Identical subject_key across two workspaces
    _materialize(store, _decision("borealis-target", "Val Borealis", t, workspace_id="ws-borealis", subject_key="k1"))
    _materialize(store, _decision("atlas-target", "Val Atlas", t, workspace_id="ws-atlas", subject_key="k1"))

    # get_current isolation
    assert store.get_current("ws-borealis", "k1") is not None
    assert store.get_current("ws-borealis", "k1").decision_id == "borealis-target"
    assert store.get_current("ws-atlas", "k1") is not None
    assert store.get_current("ws-atlas", "k1").decision_id == "atlas-target"
    assert store.get_current("ws-other", "k1") is None

    # get_history isolation
    assert [d.decision_id for d in store.get_history("ws-borealis", "k1")] == ["borealis-target"]
    assert [d.decision_id for d in store.get_history("ws-atlas", "k1")] == ["atlas-target"]
    assert store.get_history("ws-other", "k1") == ()

    # get_as_of isolation
    as_of_borealis = store.get_as_of("ws-borealis", "k1", t)
    assert as_of_borealis is not None and as_of_borealis.decision_id == "borealis-target"
    as_of_atlas = store.get_as_of("ws-atlas", "k1", t)
    assert as_of_atlas is not None and as_of_atlas.decision_id == "atlas-target"
    assert store.get_as_of("ws-other", "k1", t) is None

    # get_evolution isolation
    assert store.get_evolution("ws-borealis", "k1") == ()
    assert store.get_evolution("ws-atlas", "k1") == ()


def test_chained_supersession_three_generations() -> None:
    store = _store()
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    t3 = datetime(2026, 3, 1, tzinfo=UTC)

    _materialize(store, _decision("dec-v1", "Version 1", t1))
    _materialize(store, _decision("dec-v2", "Version 2", t2, supersedes_id="dec-v1"))
    _materialize(store, _decision("dec-v3", "Version 3", t3, supersedes_id="dec-v2"))

    # Exactly one current truth
    current = store.get_current("borealis", "supplier")
    assert current is not None and current.decision_id == "dec-v3"
    assert current.status is DecisionStatus.CURRENT
    assert current.valid_to is None

    # History chronological integrity
    history = store.get_history("borealis", "supplier")
    assert [d.decision_id for d in history] == ["dec-v1", "dec-v2", "dec-v3"]
    assert history[0].status is DecisionStatus.SUPERSEDED and history[0].valid_to == t2
    assert history[1].status is DecisionStatus.SUPERSEDED and history[1].valid_to == t3
    assert history[2].status is DecisionStatus.CURRENT and history[2].valid_to is None

    # Evolution transitions chain
    evolution = store.get_evolution("borealis", "supplier")
    assert len(evolution) == 2
    assert (evolution[0].previous_decision_id, evolution[0].current_decision_id) == ("dec-v1", "dec-v2")
    assert (evolution[1].previous_decision_id, evolution[1].current_decision_id) == ("dec-v2", "dec-v3")


def test_store_does_not_expose_an_ungated_decision_write() -> None:
    store = TemporalDecisionStore(":memory:")

    assert not hasattr(store, "add_decision")


def test_materialization_rejects_unknown_episode_and_evidence_fail_closed() -> None:
    store = TemporalDecisionStore(":memory:")
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(
        _decision(
            "unknown-source",
            "Supplier A",
            datetime(2026, 5, 12, tzinfo=UTC),
            source_episode_id="episode:not-registered",
            evidence=("fabricated/ref#L1",),
        ),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )

    with pytest.raises(ValueError, match="unknown source_episode_id"):
        materializer.materialize(candidate, approved_by="fixture:test")

    known_episode_registry = SourceReferenceRegistry(
        episodes={"borealis": {"episode:known"}},
        evidence={"borealis": {"notes/known.md#L1"}},
    )
    known_episode_store = TemporalDecisionStore(
        ":memory:",
        source_registry=known_episode_registry,
    )
    unknown_evidence_candidate = DecisionMaterializer(known_episode_store).propose(
        _decision(
            "unknown-evidence",
            "Supplier A",
            datetime(2026, 5, 12, tzinfo=UTC),
            source_episode_id="episode:known",
            evidence=("fabricated/ref#L1",),
        ),
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    with pytest.raises(ValueError, match="unknown source evidence reference"):
        DecisionMaterializer(known_episode_store).materialize(
            unknown_evidence_candidate,
            approved_by="fixture:test",
        )


def test_id_based_reads_require_workspace_scope() -> None:
    store = TemporalDecisionStore(":memory:")
    at = datetime(2026, 5, 12, tzinfo=UTC)
    # Seed only through the approved path; the registry is intentionally explicit.
    registry = SourceReferenceRegistry(
        episodes={"borealis": {"episode:scoped"}},
        evidence={"borealis": {"notes/scoped.md#L1", "notes/scoped-action.md#L1"}},
    )
    scoped_store = TemporalDecisionStore(":memory:", source_registry=registry)
    materializer = DecisionMaterializer(scoped_store)
    action = ActionRecord(
        action_id="scoped-action",
        workspace_id="borealis",
        description="Scoped action",
        owner="Rina",
        deadline=None,
        status="OPEN",
        source_decision_id="scoped",
        source_evidence_refs=("notes/scoped-action.md#L1",),
    )
    materializer.materialize(
        materializer.propose(
            _decision("scoped", "Supplier A", at, evidence=("notes/scoped.md#L1",)),
            actions=(action,),
            team_decision_contract_pass=True,
            grounding_pass=True,
        ),
        approved_by="fixture:test",
    )

    assert scoped_store.get_decision("other", "scoped") is None
    assert scoped_store.get_actions("other", "scoped") == ()
    assert scoped_store.get_decision_evidence("other", "scoped") == ()
    assert scoped_store.get_action_evidence("other", "scoped-action") == ()
