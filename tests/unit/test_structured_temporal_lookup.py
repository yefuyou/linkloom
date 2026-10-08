from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

import pytest

from linkloom.decision_memory import (
    DecisionMaterializer,
    DecisionRecord,
    DecisionStatus,
    SourceReferenceRegistry,
    TemporalDecisionStore,
)
import linkloom.decision_memory.store as decision_store_module
from linkloom.decision_memory.tool import DecisionMemorySearchTool


_WORKSPACE = "structured-lookup"
_FIRST = datetime(2026, 1, 1, tzinfo=UTC)
_SECOND = datetime(2026, 2, 1, tzinfo=UTC)
_EVIDENCE = ("facts/structured.md#L1",)


def _store() -> TemporalDecisionStore:
    return TemporalDecisionStore(
        ":memory:",
        source_registry=SourceReferenceRegistry(
            episodes={
                _WORKSPACE: {
                    "episode:one",
                    "episode:two",
                    "episode:three",
                    "episode:legacy",
                }
            },
            evidence={_WORKSPACE: set(_EVIDENCE)},
        ),
    )


def _write(
    store: TemporalDecisionStore,
    decision_id: str,
    subject: str,
    relation: str,
    value: str,
    *,
    valid_from: datetime = _FIRST,
    supersedes_id: str | None = None,
    subject_key: str | None = None,
) -> DecisionRecord:
    record = DecisionRecord(
        decision_id=decision_id,
        workspace_id=_WORKSPACE,
        subject_key=subject_key or f"{subject} {relation}",
        value=value,
        status=DecisionStatus.CURRENT,
        valid_from=valid_from,
        valid_to=None,
        supersedes_id=supersedes_id,
        source_episode_id=f"episode:{decision_id.removeprefix('decision-')}",
        source_evidence_refs=_EVIDENCE,
        provenance_run_id="run:structured-lookup-test",
        subject=subject,
        relation=relation,
    )
    materializer = DecisionMaterializer(store)
    return materializer.materialize(
        materializer.propose(
            record,
            team_decision_contract_pass=True,
            grounding_pass=True,
        ),
        approved_by="fixture:structured-lookup-test",
    )


def test_structured_lookup_resolves_single_relation() -> None:
    store = _store()
    _write(store, "decision-one", "Northstar City", "administrative center of", "Region A")

    result = store.lookup(_WORKSPACE, "Northstar City", "administrative center of")

    assert result.status == "FOUND"
    assert [record.value for record in result.records] == ["Region A"]


def test_structured_lookup_selects_each_exact_relation_for_one_subject() -> None:
    store = _store()
    _write(store, "decision-one", "Exampleland", "capital of", "Northstar")
    _write(
        store,
        "decision-two",
        "Exampleland",
        "name of the current head of state in",
        "Avery Example",
    )

    capital = store.lookup(_WORKSPACE, "Exampleland", "capital of")
    head_of_state = store.lookup(
        _WORKSPACE,
        "Exampleland",
        "name of the current head of state in",
    )

    assert [record.value for record in capital.records] == ["Northstar"]
    assert [record.value for record in head_of_state.records] == ["Avery Example"]


def test_unknown_relation_is_an_explicit_miss_without_cross_relation_fallback() -> None:
    store = _store()
    _write(store, "decision-one", "Exampleland", "capital of", "Northstar")
    _write(
        store,
        "decision-two",
        "Exampleland",
        "name of the current head of state in",
        "Avery Example",
    )

    result = store.lookup(_WORKSPACE, "Exampleland", "chief of state")

    assert result.status == "RELATION_NOT_FOUND"
    assert result.records == ()


def test_structured_lookup_normalizes_case_whitespace_and_terminal_punctuation() -> None:
    store = _store()
    _write(
        store,
        "decision-one",
        "Exampleland",
        "name of the current head of state in",
        "Avery Example",
    )

    result = store.lookup(
        _WORKSPACE,
        "  EXAMPLELAND!!!  ",
        " NAME   OF THE CURRENT HEAD OF STATE IN? ",
    )

    assert result.status == "FOUND"
    assert [record.value for record in result.records] == ["Avery Example"]


def test_structured_record_requires_both_nonempty_components() -> None:
    with pytest.raises(ValueError, match="subject and relation must be provided together"):
        DecisionRecord(
            decision_id="decision-partial",
            workspace_id=_WORKSPACE,
            subject_key="Exampleland capital of",
            value="Northstar",
            status=DecisionStatus.CURRENT,
            valid_from=_FIRST,
            valid_to=None,
            supersedes_id=None,
            source_episode_id="episode:legacy",
            source_evidence_refs=_EVIDENCE,
            provenance_run_id="run:partial",
            subject="Exampleland",
        )

    with pytest.raises(ValueError, match="relation must not be empty after normalization"):
        _write(
            store=_store(),
            decision_id="decision-punctuation",
            subject="Exampleland",
            relation="!!!",
            value="Northstar",
        )


def test_current_lookup_preserves_supersession_and_as_of_history() -> None:
    store = _store()
    _write(store, "decision-one", "Exampleland", "head of state", "Avery Example")
    _write(
        store,
        "decision-two",
        "Exampleland",
        "head of state",
        "Blake Example",
        valid_from=_SECOND,
        supersedes_id="decision-one",
    )

    current = store.lookup(_WORKSPACE, "Exampleland", "head of state")
    historical = store.lookup(
        _WORKSPACE,
        "Exampleland",
        "head of state",
        as_of=datetime(2026, 1, 15, tzinfo=UTC),
    )

    assert [record.value for record in current.records] == ["Blake Example"]
    assert [record.value for record in historical.records] == ["Avery Example"]


def test_lookup_distinguishes_subject_relation_and_temporal_misses() -> None:
    store = _store()
    _write(store, "decision-one", "Exampleland", "capital of", "Northstar")

    subject_miss = store.lookup(_WORKSPACE, "Otherland", "capital of")
    relation_miss = store.lookup(_WORKSPACE, "Exampleland", "official language")
    time_miss = store.lookup(
        _WORKSPACE,
        "Exampleland",
        "capital of",
        as_of=datetime(2025, 12, 31, tzinfo=UTC),
    )

    assert subject_miss.status == "SUBJECT_NOT_FOUND"
    assert relation_miss.status == "RELATION_NOT_FOUND"
    assert time_miss.status == "NO_VALID_RECORD_AT_TIME"


def test_lookup_rejects_naive_as_of_even_when_subject_is_missing() -> None:
    store = _store()

    with pytest.raises(ValueError, match="timezone-aware"):
        store.lookup(_WORKSPACE, "Missingland", "capital of", as_of=datetime(2026, 1, 1))


def test_authorized_memory_tool_exposes_structured_lookup_without_changing_search() -> None:
    store = _store()
    _write(store, "decision-one", "Exampleland", "capital of", "Northstar")
    tool = DecisionMemorySearchTool(store, authorized_workspace_id=_WORKSPACE)

    result = tool.lookup(
        workspace=_WORKSPACE,
        subject="Exampleland",
        relation="capital of",
    )

    assert result.status == "FOUND"
    assert [record.value for record in result.records] == ["Northstar"]
    with pytest.raises(PermissionError, match="workspace is not authorized"):
        tool.lookup(
            workspace="other-workspace",
            subject="Exampleland",
            relation="capital of",
        )


def test_legacy_lexical_search_remains_available_and_unchanged() -> None:
    store = _store()
    _write(store, "decision-one", "Exampleland", "capital of", "Northstar")

    records = store.search(_WORKSPACE, "Exampleland capital of")

    assert [record.value for record in records] == ["Northstar"]


def test_legacy_unstructured_records_are_not_inferred_by_splitting_subject_key() -> None:
    store = _store()
    materializer = DecisionMaterializer(store)
    record = DecisionRecord(
        decision_id="decision-legacy",
        workspace_id=_WORKSPACE,
        subject_key="Exampleland capital of",
        value="Northstar",
        status=DecisionStatus.CURRENT,
        valid_from=_FIRST,
        valid_to=None,
        supersedes_id=None,
        source_episode_id="episode:legacy",
        source_evidence_refs=_EVIDENCE,
        provenance_run_id="run:legacy",
    )
    materializer.materialize(
        materializer.propose(
            record,
            team_decision_contract_pass=True,
            grounding_pass=True,
        ),
        approved_by="fixture:legacy",
    )

    structured = store.lookup(_WORKSPACE, "Exampleland", "capital of")

    assert structured.status == "SUBJECT_NOT_FOUND"
    assert structured.records == ()
    assert [item.value for item in store.search(_WORKSPACE, "Exampleland capital of")] == [
        "Northstar"
    ]


def test_store_adds_structured_columns_to_an_existing_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_connection = sqlite3.connect(":memory:")
    legacy_connection.execute(
        """
        CREATE TABLE decision (
            decision_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            subject_key TEXT NOT NULL,
            value TEXT NOT NULL,
            status TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            supersedes_id TEXT,
            source_episode_id TEXT NOT NULL,
            provenance_run_id TEXT NOT NULL
        )
        """
    )
    monkeypatch.setattr(
        decision_store_module.sqlite3,
        "connect",
        lambda _path: legacy_connection,
    )

    store = TemporalDecisionStore(":memory:")
    columns = {
        str(row["name"])
        for row in store._connection.execute('PRAGMA table_info("decision")')
    }

    assert {"subject", "relation", "subject_normalized", "relation_normalized"} <= columns
    assert store.lookup(_WORKSPACE, "Exampleland", "capital of").status == "SUBJECT_NOT_FOUND"
    store.close()
