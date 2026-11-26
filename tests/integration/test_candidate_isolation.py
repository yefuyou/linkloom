"""A single unreadable candidate is isolated; it never blocks the rest of the Workspace."""
from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from linkloom.semantic_ingestion.materialization import (
    CandidateIdentityMismatchError,
    CandidateIntegrityError,
    CandidateRecordError,
    CandidateVersionUnsupportedError,
    MaterializationBlockedError,
    _candidate_from_row,
)
from linkloom.ui.product import _ISOLATED
from linkloom.ui.router_context import build_context
from linkloom.ui.unified_ask import UnifiedAskService
from tests.integration.test_pending_source_conflicts import _candidate_id, _review
from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)

from tests.unit.test_agent_memory_candidate import _build, _environment

AS_OF = "2026-10-20"


def _claim_for(subject, value, **kwargs):
    return {**_claim(value, **kwargs), "subject": subject}


@pytest.fixture
def product(tmp_path):
    started = {}

    def start(claims):
        started["parts"] = _start_product_server(tmp_path, claims)
        base, _, _, app = started["parts"]
        _client(base, "/api/workspaces", method="POST", data={"name": "Launch"})
        return base, app

    yield start
    if started:
        _, srv, worker, app = started["parts"]
        _stop_product_server(srv, worker, app)


def _rows(app, **kwargs):
    return app.store.list_semantic_candidate_rows(app._workspace_id(), **kwargs)


def _corrupt(app, candidate_id):
    """Break one stored payload in a temporary database (the immutability trigger is dropped there only)."""
    connection = app.store._connection
    connection.execute("DROP TRIGGER IF EXISTS semantic_candidate_payload_immutable")
    connection.execute(
        "UPDATE semantic_candidate SET payload_json = replace(payload_json, ?, ?) WHERE candidate_id = ?",
        ('"record_version"', '"record_version "', candidate_id))
    connection.commit()


def _corrupt_validation_reasons(app, candidate_id, reasons):
    connection = app.store._connection
    connection.execute("DROP TRIGGER semantic_candidate_payload_immutable")
    connection.execute(
        "UPDATE semantic_candidate SET validation_reasons_json = ? WHERE candidate_id = ?",
        (reasons, candidate_id),
    )
    connection.commit()


def _service(app):
    return UnifiedAskService(app, lambda: None, graph_service=False, router_provider=None)


def _import(base, name, day, speaker, value, *, subject, verb="We decided to use"):
    _, imported = _client(base, "/api/sources/import", method="POST", data={
        "name": name,
        "content": f"[2026-10-{day}T10:00:00Z] {speaker}: {subject}: {verb} {value} as the supplier for launch.",
    })
    _wait_source(base, imported["source"]["source_id"])


def _decide(base, name, day, speaker, subject_value, subject):
    _import(base, name, day, speaker, subject_value, subject=subject)
    candidate_id = _candidate_id(base, subject_value)
    _review(base, candidate_id, "approve")
    return candidate_id


def _two_slots(product):
    base, app = product([_claim_for("Launch", "Birchline"), _claim_for("Apollo", "Cedar")])
    bad = _decide(base, "a.md", "01", "Alice", "Birchline", "Launch")
    good = _decide(base, "b.md", "02", "Bob", "Cedar", "Apollo")
    return base, app, bad, good


def test_good_decision_survives_corrupt_decision(product):
    base, app, bad, good = _two_slots(product)
    workspace = app._workspace_id()
    _corrupt(app, bad)
    slots = _service(app)._trusted_slots(workspace)
    assert [slot["subject"] for slot in slots] == ["apollo"] or [slot["subject"] for slot in slots] == ["Apollo"]
    recovered = app.recover_decision("Apollo", "uses supplier", AS_OF)
    assert recovered["status"] == "FOUND" and recovered["effective"]["value"] == "Cedar"
    assert recovered["unverifiable"] is False and recovered["isolated_candidates"] == []


@pytest.mark.parametrize("corruption", [_corrupt, pytest.param(
    lambda app, candidate_id: _corrupt_validation_reasons(app, candidate_id, '{'),
    id="validation-reasons",
)])
def test_corrupt_latest_decision_never_falls_back_to_earlier_value(product, corruption):
    base, app = product([_claim_for("Launch", "Birchline"), _claim_for("Launch", "Cedar")])
    _decide(base, "a.md", "01", "Alice", "Birchline", "Launch")
    latest = _decide(base, "b.md", "05", "Bob", "Cedar", "Launch")
    workspace = app._workspace_id()
    assert app.recover_decision("Launch", "uses supplier", AS_OF)["effective"]["value"] == "Cedar"
    corruption(app, latest)
    recovered = app.recover_decision("Launch", "uses supplier", AS_OF)
    assert recovered["status"] == "AUTHORIZATION_OR_SOURCE_UNVERIFIED"
    assert recovered["unverifiable"] is True
    assert recovered["effective"] is None and recovered["conflicting"] == []
    assert recovered["history"] == [] and recovered["transitions"] == []
    assert "Birchline" not in json.dumps(recovered) and "Cedar" not in json.dumps(recovered)
    assert _service(app)._trusted_slots(workspace) == []


@pytest.mark.parametrize("corruption", [_corrupt, pytest.param(
    lambda app, candidate_id: _corrupt_validation_reasons(app, candidate_id, '{}'),
    id="validation-reasons",
)])
def test_query_on_corrupt_slot_reports_unverified_not_missing(product, corruption):
    base, app, bad, _ = _two_slots(product)
    workspace = app._workspace_id()
    corruption(app, bad)
    service = _service(app)
    slots = service._trusted_slots(workspace)
    assert [item["subject"].casefold() for item in service.unverified_slots[workspace]] == ["launch"]
    blocked = service._router_blocked_entities(workspace, slots)
    assert ("Launch".casefold(), "DECISION_UNVERIFIED") in {(n.casefold(), r) for n, r in blocked}
    context = build_context(
        "What supplier does Launch use?", workspace, slots, app.relation_resolver,
        view=lambda s, r, a: app.recover_decision(s, r, a, _workspace_id=workspace),
        blocked_entities=blocked)
    assert not any("Birchline" in json.dumps(vars(candidate), default=str)
                   for candidate in context.candidates.values())
    assert any(fact[1] == "DECISION_UNVERIFIED" for fact in context.blocked_entities)


@pytest.mark.parametrize("corruption", [_corrupt, pytest.param(
    lambda app, candidate_id: _corrupt_validation_reasons(app, candidate_id, '[1]'),
    id="validation-reasons",
)])
def test_good_supporting_only_survives_corrupt_one(product, corruption):
    base, app = product([_claim_for("Launch", "Birchline", claim_type="PROPOSAL"),
                         _claim_for("Launch", "Cedar", claim_type="PROPOSAL")])
    _import(base, "a.md", "01", "Alice", "Birchline", subject="Launch", verb="We propose to use")
    _import(base, "b.md", "02", "Bob", "Cedar", subject="Launch", verb="We propose to use")
    rows = _rows(app, workflow_state="SUPPORTING_ONLY")
    assert len(rows) == 2
    values = {row["candidate_id"]: json.loads(row["payload_json"])["candidate"]["value"] for row in rows}
    bad = next(key for key, value in values.items() if value == "Birchline")
    corruption(app, bad)
    recovered = app.recover_decision("Launch", "uses supplier", AS_OF)
    assert [item["value"] for item in recovered["proposals"]] == ["Cedar"]
    assert [item["candidate_id"] for item in recovered["isolated_candidates"]] == [bad]
    assert recovered["isolated_candidates"][0]["role"] == "SUPPORTING_ONLY"
    assert recovered["unverifiable"] is False
    assert "Birchline" not in json.dumps({k: v for k, v in recovered.items() if k != "isolated_candidates"})


def test_many_corrupt_candidates_do_not_break_context_build(product):
    base, app = product([_claim_for("Launch", "Birchline"), _claim_for("Apollo", "Cedar"),
                         _claim_for("Apollo", "Dune", claim_type="PROPOSAL"),
                         _claim_for("Orion", "Elm")])
    bad_decision = _decide(base, "a.md", "01", "Alice", "Birchline", "Launch")
    _decide(base, "b.md", "02", "Bob", "Cedar", "Apollo")
    _import(base, "c.md", "03", "Carol", "Dune", subject="Apollo", verb="We propose to use")
    _import(base, "d.md", "04", "Dan", "Elm", subject="Orion")
    workspace = app._workspace_id()
    broken = [bad_decision] + [row["candidate_id"] for row in _rows(app)
                               if "Dune" in row["payload_json"] or "Elm" in row["payload_json"]]
    assert len(broken) == 3
    for candidate_id in broken:
        _corrupt(app, candidate_id)
    service = _service(app)
    slots = service._trusted_slots(workspace)
    assert [slot["subject"].casefold() for slot in slots] == ["apollo"]
    blocked = service._router_blocked_entities(workspace, slots)
    context = build_context(
        "Who is the supplier?", workspace, slots, app.relation_resolver,
        view=lambda s, r, a: app.recover_decision(s, r, a, _workspace_id=workspace),
        blocked_entities=blocked)
    assert context.workspace == workspace
    apollo = app.recover_decision("Apollo", "uses supplier", AS_OF)
    assert apollo["status"] == "FOUND" and apollo["proposals"] == []
    assert {item["candidate_id"] for item in apollo["isolated_candidates"]} == set(broken[1:])


@pytest.mark.parametrize("corruption", [_corrupt, pytest.param(
    lambda app, candidate_id: _corrupt_validation_reasons(app, candidate_id, '{'),
    id="validation-reasons",
)])
def test_other_workspace_is_never_read_or_reported(product, corruption):
    base, app, bad, _ = _two_slots(product)
    first = app._workspace_id()
    corruption(app, bad)
    assert app.materializer.get_candidate("not-a-workspace", bad) is None
    _client(base, "/api/workspaces", method="POST", data={"name": "Other"})
    second = app._workspace_id()
    assert second != first
    recovered = app.recover_decision("Launch", "uses supplier", AS_OF)
    assert recovered["isolated_candidates"] == [] and recovered["unverifiable"] is False
    assert recovered["history"] == [] and recovered["proposals"] == []
    assert app.materializer.get_candidate(second, bad) is None
    assert _service(app)._trusted_slots(second) == []
    # Pinning the first workspace explicitly still sees its own isolation.
    pinned = app.recover_decision("Launch", "uses supplier", AS_OF, _workspace_id=first)
    assert pinned["unverifiable"] is True and pinned["workspace_id"] == first


def test_database_error_is_not_swallowed_as_candidate_failure(product, monkeypatch):
    base, app, _, _ = _two_slots(product)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app.store, "get_semantic_candidate_row", broken)
    with pytest.raises(sqlite3.OperationalError):
        app.recover_decision("Launch", "uses supplier", AS_OF)
    assert not isinstance(sqlite3.OperationalError("x"), CandidateRecordError)
    assert not app.candidate_isolation_diagnostics


@pytest.mark.parametrize("error", [RuntimeError("bug"), ValueError("bug"), PermissionError("denied")])
def test_unrelated_errors_propagate(product, monkeypatch, error):
    base, app, _, _ = _two_slots(product)

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(app.materializer, "get_candidate", broken)
    with pytest.raises(type(error)):
        app.recover_decision("Launch", "uses supplier", AS_OF)


def test_isolation_leaves_diagnostics_with_candidate_id_and_reason(product, caplog):
    base, app, bad, _ = _two_slots(product)
    _corrupt(app, bad)
    with caplog.at_level(logging.WARNING, logger="linkloom.ui.product"):
        recovered = app.recover_decision("Launch", "uses supplier", AS_OF)
    [entry] = recovered["isolated_candidates"]
    assert entry == {"candidate_id": bad, "workspace_id": app._workspace_id(), "role": "DECISION",
                     "reason_code": "CANDIDATE_INVALID", "error_type": entry["error_type"]}
    assert issubclass(CandidateRecordError, MaterializationBlockedError)
    assert entry["error_type"] in {"CandidateIntegrityError", "CandidateVersionUnsupportedError",
                                   "CandidateIdentityMismatchError"}
    assert any(bad in record.getMessage() and "CANDIDATE_INVALID" in record.getMessage()
               for record in caplog.records)
    assert app.candidate_isolation_diagnostics[-1]["candidate_id"] == bad


def test_failure_categories_raise_typed_errors(product):
    base, app, bad, _ = _two_slots(product)
    row = dict(app.store.get_semantic_candidate_row(app._workspace_id(), bad))
    assert _candidate_from_row(row).candidate_id == bad
    cases = [
        ({"candidate_fingerprint": "0" * 64}, CandidateIntegrityError),
        ({"payload_json": row["payload_json"] + " "}, CandidateIntegrityError),
        ({"candidate_id": "other"}, CandidateIdentityMismatchError),
        ({"workspace_id": "ws-other"}, CandidateIdentityMismatchError),
        ({"payload_version": "candidate-decision-fact/v99"}, CandidateVersionUnsupportedError),
        ({"candidate_source_kind": "UNKNOWN"}, CandidateVersionUnsupportedError),
    ]
    for override, expected in cases:
        with pytest.raises(expected) as caught:
            _candidate_from_row({**row, **override})
        assert caught.value.reason_code == "CANDIDATE_INVALID"
        assert isinstance(caught.value, MaterializationBlockedError)

@pytest.mark.parametrize("reasons", ["{", "{}", "[1]"], ids=["invalid-json", "object", "non-string"])
@pytest.mark.parametrize("source_kind", ["SOURCE_INGESTION", "AGENT_RESULT"])
def test_malformed_validation_reasons_are_typed_and_isolated(product, source_kind, reasons):
    if source_kind == "SOURCE_INGESTION":
        _, app, candidate_id, _ = _two_slots(product)
        workspace = app._workspace_id()
    else:
        _, app = product([])
        candidate = _build(_environment()).candidate
        assert candidate is not None
        workspace = candidate.workspace_id
        candidate_id = candidate.candidate_id
        app.materializer.capture(candidate, expected_workspace_id=workspace)

    row = app.store.get_semantic_candidate_row(workspace, candidate_id)
    assert row["candidate_source_kind"] == source_kind
    assert app.materializer.get_candidate(workspace, candidate_id).candidate_id == candidate_id
    _corrupt_validation_reasons(app, candidate_id, reasons)

    with pytest.raises(CandidateIntegrityError, match="candidate validation reasons are malformed"):
        app.materializer.get_candidate(workspace, candidate_id)

    isolated = []
    assert app._get_candidate_isolated(workspace, candidate_id, "DECISION", isolated) is _ISOLATED
    assert isolated == [{
        "candidate_id": candidate_id,
        "workspace_id": workspace,
        "role": "DECISION",
        "reason_code": "CANDIDATE_INVALID",
        "error_type": "CandidateIntegrityError",
    }]
