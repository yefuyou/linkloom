"""Offline acceptance coverage for the Unified Ask HTTP contract."""

from __future__ import annotations

from time import monotonic, sleep
from typing import Any

import pytest

try:
    from tests.integration.unified_ask_fixture import (
        ATLAS,
        CURRENT_AS_OF,
        HISTORICAL_AS_OF,
        OTHER,
        RELATION,
        SUBJECT,
        UnifiedAskFixture,
        poll_product_ask,
        request_bytes,
        request_json,
    )
except ModuleNotFoundError:
    from unified_ask_fixture import (  # type: ignore[no-redef]
        ATLAS,
        CURRENT_AS_OF,
        HISTORICAL_AS_OF,
        OTHER,
        RELATION,
        SUBJECT,
        UnifiedAskFixture,
        poll_product_ask,
        request_bytes,
        request_json,
    )


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


@pytest.fixture
def ask_fixture(tmp_path):
    fixture = UnifiedAskFixture(tmp_path / "unified-ask.sqlite")
    try:
        yield fixture
    finally:
        fixture.stop()


def _scope(fixture: UnifiedAskFixture, *, as_of: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": fixture.workspace_ids[ATLAS],  # type: ignore[index]
        "subject": SUBJECT,
        "relation": RELATION,
    }
    if as_of is not None:
        payload["as_of"] = as_of
    return payload


def _ask(
    fixture: UnifiedAskFixture,
    query: str,
    *,
    as_of: str | None = None,
    workspace_id: str | None = None,
    allow_external_provider: bool | None = None,
) -> tuple[int, dict[str, Any]]:
    payload = _scope(fixture, as_of=as_of)
    if workspace_id is not None:
        payload["workspace_id"] = workspace_id
    if allow_external_provider is not None:
        payload["allow_external_provider"] = allow_external_provider
    payload["query"] = query
    return request_json(fixture.base_url, "/api/product/ask", method="POST", data=payload)


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    assert isinstance(result, dict), payload
    return result


def _assert_result_contract(result: dict[str, Any]) -> None:
    assert result["path"] in {"DETERMINISTIC", "AGENT", None}
    assert result["status"] in {
        "COMPLETED",
        "INVESTIGATING",
        "NEEDS_SCOPE",
        "FAILED",
        "PROVIDER_UNCERTAIN",
    }
    for field in ("conclusions", "evidence", "receipts", "reviewer", "agent_run_id"):
        assert field in result


def test_current_and_historical_lookup_are_reviewed_and_make_zero_model_requests(ask_fixture):
    status, current_payload = _ask(
        ask_fixture,
        "What supplier are we currently using?",
        as_of=CURRENT_AS_OF,
    )
    current = _result(current_payload)
    assert status == 200
    _assert_result_contract(current)
    assert current["path"] == "DETERMINISTIC"
    assert current["status"] == "COMPLETED"
    assert current["conclusions"][0]["value"] == "Wrenwell"
    assert current["evidence"] and all(item["verified"] for item in current["evidence"])
    assert current["receipts"][0]["authorization_type"] == "HUMAN_APPROVAL"

    status, historical_payload = _ask(
        ask_fixture,
        f"What supplier was Launch using on {HISTORICAL_AS_OF}?",
        as_of=HISTORICAL_AS_OF,
    )
    historical = _result(historical_payload)
    assert status == 200
    _assert_result_contract(historical)
    assert historical["path"] == "DETERMINISTIC"
    assert historical["status"] == "COMPLETED"
    assert historical["conclusions"][0]["value"] == "Birchline"
    assert historical["scope"]["as_of"].startswith(HISTORICAL_AS_OF)
    assert ask_fixture.transports == []


def test_current_answer_keeps_unreviewed_cedar_proposal_out_of_effective_conclusion(ask_fixture):
    status, payload = _ask(ask_fixture, "Is Cedar the current supplier?", as_of=CURRENT_AS_OF)
    result = _result(payload)
    assert status == 200
    assert result["status"] == "COMPLETED"
    assert result["conclusions"][0]["value"] == "Wrenwell"
    proposals = result["timeline"]["proposals"]
    assert [item["value"] for item in proposals] == ["Cedar"]
    assert proposals[0]["status"] == "PROPOSED_NOT_APPROVED"
    assert "Cedar" not in {item["value"] for item in result["conclusions"]}


def test_unknown_task_and_date_mismatch_request_scope_without_starting_agent(ask_fixture):
    status, payload = _ask(ask_fixture, "Summarize this workspace", as_of=CURRENT_AS_OF)
    result = _result(payload)
    assert status == 200
    assert result["status"] == "NEEDS_SCOPE"
    assert result["error"]["code"] == "TASK_UNCLASSIFIED"

    status, payload = _ask(
        ask_fixture,
        f"What supplier was Launch using on {HISTORICAL_AS_OF}?",
        as_of=CURRENT_AS_OF,
    )
    result = _result(payload)
    assert status == 200
    assert result["status"] == "NEEDS_SCOPE"
    assert result["error"]["code"] == "DATE_SCOPE_REQUIRED"
    assert ask_fixture.transports == []


def test_external_provider_permission_is_denied_by_default_and_offline_mode_is_explicit(ask_fixture):
    service = ask_fixture.configure_agent(external_provider=True)
    status, config = request_json(ask_fixture.base_url, "/api/product/ask/config")
    assert status == 200
    assert config["agent"]["available"] is True
    assert config["agent"]["external_provider"] is True

    status, payload = _ask(
        ask_fixture,
        "Why did Launch change suppliers?",
        as_of=CURRENT_AS_OF,
    )
    result = _result(payload)
    assert status == 403
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "EXTERNAL_PROVIDER_PERMISSION_REQUIRED"
    assert service.config()["external_provider"] is True
    assert ask_fixture.transports == []

    ask_fixture.configure_agent(external_provider=False)
    status, payload = _ask(
        ask_fixture,
        "Why did Launch change suppliers?",
        as_of=CURRENT_AS_OF,
    )
    assert status == 202
    assert _result(payload)["status"] in {"INVESTIGATING", "FAILED", "COMPLETED"}
    assert len(ask_fixture.transports) == 1


def test_investigation_uses_runtime_tools_validator_reviewer_and_keeps_reason_unknown(ask_fixture):
    service = ask_fixture.configure_agent(scenario="normal", external_provider=False)
    status, started_payload = _ask(
        ask_fixture,
        "Why did Launch switch from Birchline to Wrenwell?",
        as_of=CURRENT_AS_OF,
    )
    started = _result(started_payload)
    assert status == 202
    assert started["path"] == "AGENT"
    assert started["status"] == "INVESTIGATING"
    run_id = started["agent_run_id"]
    assert run_id

    status, terminal_payload = poll_product_ask(
        ask_fixture,
        run_id,
        ask_fixture.workspace_ids[ATLAS],  # type: ignore[index]
    )
    result = _result(terminal_payload)
    assert status == 200, terminal_payload
    assert result["status"] == "COMPLETED", result
    assert result["conclusions"][0]["value"] == "Wrenwell"
    assert result["evidence"] and result["receipts"]
    reviewer = result["reviewer"]
    assert reviewer["performed"] is True
    assert reviewer["accepted"] is True
    assert reviewer["decision"] == "evidence_sufficient"
    assert reviewer["review_id"]
    assert reviewer["reviewer_agent_id"]
    assert reviewer["checks"]
    assert reviewer["reason"]
    assert reviewer["created_at"]
    assert result["uncertainty"]["unknown_fields"] == ["replacement_reason"]
    assert result["timeline"]["transitions"][0]["from_value"] == "Birchline"
    assert result["timeline"]["transitions"][0]["reason_status"] == "NOT_SOURCE_VERIFIED"

    snapshot = service.inspect(run_id)
    assert snapshot is not None
    assert snapshot["reviewer"]["decision"] == "evidence_sufficient"
    tools = [item["tool"] for item in snapshot["trace"]]
    assert "search_decision_memory" in tools
    assert "search_notes" in tools
    assert tools.count("read_verified_note") >= 2
    assert ask_fixture.transports[0].final_sent is True


def test_investigation_without_as_of_uses_observed_current_scope(ask_fixture):
    ask_fixture.configure_agent(scenario="normal", external_provider=False)
    status, started_payload = _ask(
        ask_fixture,
        "Why is Launch using Wrenwell?",
    )
    assert status == 202
    run_id = _result(started_payload)["agent_run_id"]
    status, terminal_payload = poll_product_ask(
        ask_fixture, run_id, ask_fixture.workspace_ids[ATLAS],  # type: ignore[index]
    )
    result = _result(terminal_payload)
    assert status == 200, terminal_payload
    assert result["status"] == "COMPLETED", result
    assert result["scope"]["as_of"] == result["timeline"]["as_of"]
    assert result["conclusions"][0]["value"] == "Wrenwell"
    assert result["uncertainty"]["unknown_fields"] == ["replacement_reason"]
    assert result["reviewer"]["decision"] == "evidence_sufficient"


def test_historical_investigation_uses_observed_state_without_fake_predecessor(ask_fixture):
    ask_fixture.configure_agent(scenario="normal", external_provider=False)
    status, started_payload = _ask(
        ask_fixture,
        f"Why was Launch using Birchline on {HISTORICAL_AS_OF}?",
        as_of=HISTORICAL_AS_OF,
    )
    assert status == 202
    run_id = _result(started_payload)["agent_run_id"]
    status, terminal_payload = poll_product_ask(
        ask_fixture, run_id, ask_fixture.workspace_ids[ATLAS],  # type: ignore[index]
    )
    result = _result(terminal_payload)
    assert status == 200, terminal_payload
    assert result["status"] == "COMPLETED", result
    assert result["conclusions"][0]["value"] == "Birchline"
    assert result["scope"]["as_of"].startswith(HISTORICAL_AS_OF)
    assert result["timeline"]["transitions"] == []
    assert result["uncertainty"]["unknown_fields"] == []
    assert result["reviewer"]["decision"] == "evidence_sufficient"
    tools = [item["tool"] for item in ask_fixture.service.inspect(run_id)["trace"]]
    assert tools.count("read_verified_note") == 1


def test_unconfigured_agent_returns_safe_configuration_failure(ask_fixture):
    ask_fixture.configure_agent(configured=False, external_provider=False)
    status, payload = _ask(
        ask_fixture,
        "Why did Launch change suppliers?",
        as_of=CURRENT_AS_OF,
    )
    result = _result(payload)
    assert status == 503
    assert result["path"] == "AGENT"
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "AGENT_NOT_CONFIGURED"
    assert result["conclusions"] == []
    assert result["receipts"] == []


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [("uncertain", "PROVIDER_UNCERTAIN"), ("fail", "FAILED")],
)
def test_provider_failure_never_exposes_a_final(
    ask_fixture,
    scenario: str,
    expected_status: str,
):
    # The browser demo uses scenario="normal" and selects the outcome from
    # these query phrases, so exercise that same deterministic seam here.
    ask_fixture.configure_agent(scenario="normal", external_provider=False)
    status, started_payload = _ask(
        ask_fixture,
        f"Why did Launch change suppliers? {scenario} provider",
        as_of=CURRENT_AS_OF,
    )
    started = _result(started_payload)
    assert status == 202
    run_id = started["agent_run_id"]
    status, terminal_payload = poll_product_ask(
        ask_fixture,
        run_id,
        ask_fixture.workspace_ids[ATLAS],  # type: ignore[index]
    )
    result = _result(terminal_payload)
    # Provider failures are surfaced through the HTTP boundary as the
    # provider-specific 5xx mapping (currently 502 for a known SDK failure,
    # 503 for an uncertain provider result).  The observable safety contract
    # is the terminal result/status and the absence of a final answer.
    assert 500 <= status <= 503
    assert result["status"] == expected_status
    assert result["conclusions"] == []
    assert result["evidence"] == []
    assert result["receipts"] == []
    assert result["reviewer"]["performed"] is False
    assert result["reviewer"]["accepted"] is False
    assert result["reviewer"]["decision"] is None
    assert ask_fixture.transports[0].final_sent is False


@pytest.mark.parametrize("scenario", ["forged_receipt", "wrong_evidence"])
def test_forged_receipt_or_wrong_evidence_is_rejected_and_run_scope_is_workspace_bound(
    ask_fixture, scenario: str,
):
    ask_fixture.configure_agent(scenario=scenario, external_provider=False)
    status, started_payload = _ask(
        ask_fixture,
        f"Why did Launch change suppliers? {scenario}",
        as_of=CURRENT_AS_OF,
    )
    assert status == 202
    run_id = _result(started_payload)["agent_run_id"]
    status, terminal_payload = poll_product_ask(
        ask_fixture,
        run_id,
        ask_fixture.workspace_ids[ATLAS],  # type: ignore[index]
    )
    result = _result(terminal_payload)
    assert status == 500
    assert result["status"] == "FAILED"
    assert result["conclusions"] == []
    assert result["receipts"] == []
    assert ask_fixture.transports[0].final_sent is True

    ask_fixture.select_workspace(OTHER)
    status, payload = request_json(
        ask_fixture.base_url,
        f"/api/product/ask/runs/{run_id}?workspace_id={ask_fixture.workspace_ids[ATLAS]}",  # type: ignore[index]
    )
    assert status == 409
    assert payload["error"]["code"] == "WORKSPACE_MISMATCH"


def test_legacy_agent_endpoint_is_explicit_while_product_ask_requires_provider_permission(ask_fixture):
    """The old Agent endpoint keeps its explicit-start contract for offline callers."""

    ask_fixture.configure_agent(scenario="normal", external_provider=True)
    status, denied = _ask(
        ask_fixture,
        "Why did Launch change suppliers?",
        as_of=CURRENT_AS_OF,
    )
    assert status == 403
    assert _result(denied)["error"]["code"] == "EXTERNAL_PROVIDER_PERMISSION_REQUIRED"
    assert ask_fixture.transports == []

    status, started = request_json(
        ask_fixture.base_url,
        "/api/agent/decisions/ask",
        method="POST",
        data={
            "query": "Why did Launch change suppliers?",
            "workspace_id": ask_fixture.workspace_ids[ATLAS],  # type: ignore[index]
            "subject": SUBJECT,
            "relation": RELATION,
            "as_of": CURRENT_AS_OF,
        },
    )
    assert status == 202
    assert started["kind"] == "running"
    legacy_run_id = started["run"]["id"]

    deadline = monotonic() + 30
    snapshot = started
    while snapshot.get("kind") == "running" and monotonic() < deadline:
        sleep(0.02)
        status, snapshot = request_json(
            ask_fixture.base_url,
            f"/api/agent/decisions/runs/{legacy_run_id}",
        )
    assert status == 200, snapshot
    assert snapshot["kind"] in {"success", "insufficient"}
    assert len(ask_fixture.transports) == 1


def test_legacy_api_and_legacy_page_keep_their_existing_semantics(ask_fixture):
    status, body = request_bytes(ask_fixture.base_url, "/legacy-ask")
    assert status == 200
    assert b"LinkLoom" in body

    status, payload = request_json(
        ask_fixture.base_url,
        "/api/ask",
        method="POST",
        data={"query": "What supplier are we currently using?"},
    )
    assert status == 200
    assert payload["answer"]["status"] == "FOUND"
    assert payload["answer"]["answer"]["value"] == "Wrenwell"
