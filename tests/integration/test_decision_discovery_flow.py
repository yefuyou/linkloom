import pytest

from decision_discovery_fixture import DecisionDiscoveryFixture
from unified_ask_fixture import ATLAS, OTHER, poll_product_ask, request_json
from unified_ask_fixture import claim


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    fixture = DecisionDiscoveryFixture(tmp_path / "discovery.sqlite")
    yield fixture
    fixture.stop()


def ask(demo, query, **kwargs):
    return request_json(demo.base_url, "/api/product/ask", method="POST", data={
        "query": query, "workspace_id": demo.app._workspace_id(), **kwargs})


def test_current_and_exact_historical_question_discover_without_model_calls(demo):
    demo.configure_agent(external_provider=True)
    for query, value, date in (
        ("我们现在采用哪家供应商？", "Wrenwell", None),
        ("2026年10月6日这个项目用的是哪家供应商？", "Birchline", "2026-10-06"),
    ):
        status, payload = ask(demo, query)
        result = payload["result"]
        assert status == 200 and result["status"] == "COMPLETED"
        assert result["discovery"]["status"] == "MATCHED"
        assert result["scope"]["subject"] == "Launch"
        assert result["scope"]["relation"] == "uses supplier"
        assert result["path"] == "DETERMINISTIC"
        assert result["conclusions"][0]["value"] == value
        assert result["receipts"] and all(item["verified"] for item in result["evidence"])
        if date:
            assert result["scope"]["as_of"].startswith(date)
    assert demo.transports == []


def test_ambiguous_question_can_resume_with_a_trusted_candidate(demo):
    demo.select_workspace(OTHER)
    query = "我们现在采用哪家供应商？"
    status, payload = ask(demo, query)
    result = payload["result"]
    assert status == 200 and result["status"] == "NEEDS_SCOPE"
    assert result["discovery"]["status"] == "AMBIGUOUS"
    assert {item["subject"] for item in result["discovery"]["candidates"]} == {"Support", "Expansion"}
    status, payload = ask(demo, query, subject="Support", relation="uses supplier")
    assert payload["result"]["discovery"]["status"] == "EXPLICIT"
    assert payload["result"]["conclusions"][0]["value"] == "Riverton"
    assert demo.transports == []


def test_no_match_and_proposal_only_subject_never_create_authoritative_scope(demo):
    demo.select_workspace(OTHER)
    for query in ("What is the weather?", "Research Mallow", "Which supplier does Ghost use?"):
        status, payload = ask(demo, query)
        assert status == 200
        assert payload["result"]["discovery"]["status"] == "NO_MATCH"
        assert payload["result"]["conclusions"] == []
    _, config = request_json(demo.base_url, "/api/product/ask/config")
    assert all(slot["subject"] != "Research" for slot in config["scopes"])


def test_relative_date_clarification_and_explicit_resolution(demo):
    query = "上个月这个项目用的是哪家供应商？"
    _, payload = ask(demo, query)
    assert payload["result"]["error"]["code"] == "DATE_SCOPE_REQUIRED"
    _, payload = ask(demo, query, as_of="2026-09-15")
    assert payload["result"]["scope"]["as_of"].startswith("2026-09-15")
    assert payload["result"]["conclusions"] == []  # Decisions did not yet exist.
    for interval in ("What supplier did Launch use 2026-10-01 to now?",
                     "What supplier did Launch use 2026-10-01 and now?",
                     "2026-10-01至今采用哪家供应商？"):
        _, payload = ask(demo, interval)
        assert payload["result"]["error"]["code"] == "DATE_SCOPE_REQUIRED"
        assert payload["result"]["conclusions"] == []


def test_approved_future_only_slot_remains_compatible_with_explicit_as_of(demo):
    future = claim("Seabrook")
    future["subject"] = "Future"
    demo.app.extractor._provider._claims.append(future)
    demo._import_and_review(name="future-supplier.md",
        content="[2030-01-01T10:00:00Z] Alice: We decided to use Seabrook as the supplier for Future.",
        value="Seabrook", approve=True)
    for explicit in ({}, {"subject": "Future", "relation": "uses supplier"}):
        _, payload = ask(demo, "What supplier is Future using on 2030-01-02?", **explicit)
        assert payload["result"]["conclusions"][0]["value"] == "Seabrook"
    _, payload = ask(demo, "What supplier is Future currently using?")
    assert payload["result"]["conclusions"] == []
    assert demo.transports == []


@pytest.mark.parametrize("scenario", ["normal", "uncertain"])
def test_automatic_history_discovery_uses_existing_agent_and_trust_gates(demo, scenario):
    demo.configure_agent(scenario=scenario, external_provider=True)
    query = "为什么我们从 Birchline 换成了 Wrenwell？"
    status, denied = ask(demo, query)
    assert status == 403 and denied["result"]["error"]["code"] == "EXTERNAL_PROVIDER_PERMISSION_REQUIRED"
    assert demo.transports == []
    status, started = ask(demo, query, allow_external_provider=True)
    assert status == 202
    run_id = started["result"]["agent_run_id"]
    _, terminal = poll_product_ask(demo, run_id, demo.workspace_ids[ATLAS])
    result = terminal["result"]
    assert result["discovery"]["status"] == "MATCHED"
    if scenario == "normal":
        assert result["status"] == "COMPLETED"
        assert result["reviewer"]["accepted"] is True
        assert result["uncertainty"]["unknown_fields"] == ["replacement_reason"]
        assert "Cedar" not in {item["value"] for item in result["conclusions"]}
    else:
        assert result["status"] == "PROVIDER_UNCERTAIN"
        assert result["conclusions"] == result["evidence"] == result["receipts"] == []
    demo.select_workspace(OTHER)
    status, payload = request_json(demo.base_url,
        f"/api/product/ask/runs/{run_id}?workspace_id={demo.workspace_ids[OTHER]}")
    assert status == 409 and payload["error"]["code"] == "WORKSPACE_MISMATCH"
    _, payload = ask(demo, "Why Birchline changed to Wrenwell?")
    assert payload["result"]["discovery"]["status"] == "NO_MATCH"
