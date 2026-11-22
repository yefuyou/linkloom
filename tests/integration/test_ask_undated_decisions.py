"""B1-5: deterministic Ask never orders a confirmed, time-unknown decision into CURRENT/AS_OF."""

from __future__ import annotations

import pytest

from linkloom.semantic_ingestion.artifacts import source_episode_id_for
from tests.integration.unified_ask_fixture import (
    ATLAS, HISTORICAL_AS_OF, RELATION, SUBJECT, UnifiedAskFixture, request_json, wait_source,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")


@pytest.fixture
def ask_fixture(tmp_path):
    fixture = UnifiedAskFixture(tmp_path / "unified-ask.sqlite")
    try:
        yield fixture
    finally:
        fixture.stop()


def _confirm_undated(fixture: UnifiedAskFixture, value: str, *, subject: str = SUBJECT) -> str:
    """Seed a reviewed, time-unknown decision (the store contract from #123) on an imported source."""
    status, payload = request_json(fixture.base_url, "/api/sources/import", method="POST", data={
        "name": f"undated-{value}.md",
        "content": f"Meeting notes\nWe agreed to switch {subject} to {value} as supplier. No start date yet.\n"})
    assert status == 202, payload
    source = wait_source(fixture.base_url, payload["source"]["source_id"])
    app = fixture.app
    workspace_id = fixture.workspace_ids[ATLAS]
    (evidence,) = app.repository.spans_for_version(workspace_id, source["source_id"], source["source_version"])
    record, _ = app.store.confirm_undated_decision(
        workspace_id=workspace_id, subject=subject, relation=RELATION, value=value,
        source_episode_id=source_episode_id_for(app._source_artifact(source)),
        source_evidence_refs=(evidence["evidence_ref"],), provenance_run_id="test-review",
        authorization_id="local-reviewer", authorization_reason="Confirmed; effective date unknown")
    return record.undated_decision_id


def _ask(fixture, query, **extra):
    payload = {"workspace_id": fixture.workspace_ids[ATLAS], "subject": SUBJECT, "relation": RELATION,
               "query": query, **extra}
    status, body = request_json(fixture.base_url, "/api/product/ask", method="POST", data=payload)
    assert status == 200, body
    return body["result"]


def test_dated_only_answer_is_unchanged(ask_fixture):
    result = _ask(ask_fixture, "What supplier are we currently using?")
    assert result["status"] == "COMPLETED" and result["conclusions"][0]["value"] == "Wrenwell"
    assert result["evolution"]["undated"] == []
    assert result["execution"] == {"schema_version": "ask-execution/v1", "answer_path": "VERIFIED_STORE_LOOKUP",
                                   "answer_provider_calls": 0, "router_provider_calls": 0,
                                   "router_reported_usage": None, "agent_run_id": None}


def test_undated_replacement_makes_current_needs_verification(ask_fixture):
    undated_id = _confirm_undated(ask_fixture, "Halden")
    current = _ask(ask_fixture, "What supplier are we currently using?")
    assert current["path"] == "DETERMINISTIC"
    assert current["status"] == "INSUFFICIENT"
    assert current["conclusions"] == [], "no value is asserted by import or review order"
    assert "EFFECTIVE_TIME_UNKNOWN" in current["uncertainty"]["reasons"]
    (undated,) = current["evolution"]["undated"]
    assert (undated["decision_id"], undated["value"], undated["valid_from"]) == (undated_id, "Halden", None)
    assert undated["evidence_status"] == "VERIFIED"
    # The dated history and its evidence stay visible next to the undated record.
    assert current["timeline"]["effective"]["value"] == "Wrenwell"
    assert ask_fixture.transports == []

    # A historical date with a verified dated record still answers, with the undated note.
    historical = _ask(ask_fixture, f"What supplier was Launch using on {HISTORICAL_AS_OF}?", as_of=HISTORICAL_AS_OF)
    assert historical["status"] == "COMPLETED" and historical["conclusions"][0]["value"] == "Birchline"
    assert "undated_note" in historical["uncertainty"]


def test_revoked_undated_record_without_replacement_does_not_block(ask_fixture):
    undated_id = _confirm_undated(ask_fixture, "Halden")
    ask_fixture.app.store.revoke_undated_decision(ask_fixture.workspace_ids[ATLAS], undated_id)
    result = _ask(ask_fixture, "What supplier are we currently using?")
    assert result["status"] == "COMPLETED" and result["conclusions"][0]["value"] == "Wrenwell"
    assert [item["memory_state"] for item in result["evolution"]["undated"]] == []


def _router_ask(fixture, query, reply):
    import json
    from linkloom.agents.model_adapter import ModelAction, ModelResponse
    from linkloom.ui.router_provider import RouterProvider
    from linkloom.ui.structured_router import PROMPT
    from linkloom.ui.unified_ask import UnifiedAskService

    requests = []

    class Router:
        def complete(self, request):
            data = json.loads(request.user_input.removeprefix(PROMPT))
            requests.append(data)
            return ModelResponse(action=ModelAction.final(json.dumps(reply(data))))

    fixture.app.repository.select_workspace(fixture.workspace_ids[ATLAS])
    ask = UnifiedAskService(fixture.app, lambda: None, graph_service=False,
                            router_provider=RouterProvider(Router()), slot_embedder=None)
    result, status = ask.ask(dict(query=query, workspace_id=fixture.workspace_ids[ATLAS]))
    assert status == 200, result
    return result, requests


def _route(data, label, **changes):
    candidate = next(item["id"] for item in data["candidates"] if label in item["label"])
    return dict(dict(schema_version="router/v2", decision="ROUTE", intent="CURRENT", domain="DECISION",
                     operation="READ", candidate_ids=[candidate], mentions=[label], time_ref_ids=[],
                     time_role="NONE", reason_code="NONE"), **changes)


def test_router_decision_history_is_deterministic_and_lists_undated_separately(ask_fixture):
    _confirm_undated(ask_fixture, "Halden")
    result, requests = _router_ask(ask_fixture, "How has the Launch supplier changed?",
                                   lambda data: _route(data, "Launch", intent="HISTORY"))
    assert len(requests) == 1 and result["path"] == "DETERMINISTIC"
    assert result["execution"]["answer_provider_calls"] == 0
    assert result["execution"]["router_provider_calls"] == 1
    entries = [(item["kind"], item["value"]) for item in result["conclusions"]]
    assert entries == [("DECISION_HISTORY_ENTRY", "Birchline"), ("DECISION_HISTORY_ENTRY", "Wrenwell"),
                       ("UNDATED_DECISION", "Halden")]
    assert result["uncertainty"]["unknown_fields"] == ["effective_time"]


def test_router_undated_only_slot_answers_time_unknown_instead_of_no_record(ask_fixture):
    _confirm_undated(ask_fixture, "Quill", subject="Orbit")
    result, _ = _router_ask(ask_fixture, "What supplier is Orbit using now?",
                            lambda data: _route(data, "Orbit"))
    assert result["path"] == "DETERMINISTIC" and result["status"] == "INSUFFICIENT", result
    assert result["conclusions"] == []
    assert [item["value"] for item in result["evolution"]["undated"]] == ["Quill"]
    assert "EFFECTIVE_TIME_UNKNOWN" in result["uncertainty"]["reasons"]
