from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.observability.ask_telemetry import build_ask_telemetry
from linkloom.runtime.models import AttemptRecord, ModelExecutionRecord, ToolExecutionRecord
from tests.unit.test_m3q_provider_budget import request, session
from tests.unit.test_runtime_checkpoint import _sample_state


def project(**options):
    return build_ask_telemetry(run_id="ask-1", workspace_id="ws-1", status="COMPLETE", **options)


def journal(*outcomes, stage="runtime"):
    rows = []
    for sequence, outcome in enumerate(outcomes, 1):
        row = dict(sequence=sequence, stage=stage, request_sha256="same-hash",
                   input_reserved=150, output_reserved=100, cost_reserved_usd=0.01,
                   outcome=outcome)
        if outcome == "SUCCEEDED":
            row.update(response_sha256="response", usage=dict(
                input_tokens=30, output_tokens=10, total_tokens=40, duration_ms=123))
        rows.append(row)
    return dict(requests_reserved=len(rows), input_tokens_reserved=150 * len(rows),
                output_tokens_reserved=100 * len(rows), cost_reserved_usd=.01 * len(rows),
                actual_cost_usd=None, attempts=rows,
                limits=dict(input_usd_per_million=1.0, output_usd_per_million=5.0))


def test_reported_usage_price_estimate_and_budget_are_independent():
    budgets = {"router/router-1": journal("SUCCEEDED", stage="ask-router"),
               "supervisor/round-0": journal("SUCCEEDED", stage="supervisor")}
    original = deepcopy(budgets)
    got = project(router={"source": "llm", "attempted": True, "decision": "ROUTE",
                          "query": "private question"},
                  provider_journals=budgets, provider_journals_complete=True, total_duration_ms=180)
    assert got["provider_calls"]["attempted"] == got["provider_calls"]["completed"] == 2
    assert got["usage"]["reported_total_tokens"] == 80
    assert got["usage"]["reserved_input_upper_bound"] == 300
    assert got["cost"]["estimated_actual_usage_usd"] == pytest.approx(.00016)
    assert got["cost"]["reserved_upper_bound_usd"] == .02
    assert got["cost"]["actual_billed_usd"] is None
    assert got["total_duration_ms"] == 180
    assert "private question" not in json.dumps(got)
    assert budgets == original


def test_unknown_provider_outcome_or_pending_receipt_never_reports_zero_usage():
    budget = journal("SUCCEEDED", "UNCERTAIN", "PENDING")
    # A timeout can also return a durable error envelope with a partial usage field.
    budget["attempts"][1].update(response_sha256="error-envelope", usage={"input_tokens": 4})
    got = project(provider_journals={"tasks/t1/attempt-0-1": budget},
                  provider_journals_complete=True)
    assert got["provider_calls"]["attempted"] is None
    assert got["provider_calls"]["pending"] == 1
    assert got["provider_calls"]["observed_attempted"] == 2
    assert got["usage"]["reported_input_tokens"] is None
    assert got["cost"]["estimated_actual_usage_usd"] is None
    assert got["cost"]["known_estimated_usage_usd"] == pytest.approx(.00008)
    assert got["cost"]["unresolved_reserved_upper_bound_usd"] == .02
    assert got["cost"]["billing_status"] == "PARTIAL"
    assert all(node["reported_usage"]["input_tokens"] is None for node in got["nodes"][1:])
    assert got["total_duration_ms"] is None


def test_missing_router_journal_differs_from_confirmed_local_only_run():
    missing = project(provider_journals={"router/router-1": None})
    assert missing["provider_calls"]["attempted"] is None
    assert missing["usage"]["reported_total_tokens"] is None
    assert missing["cost"]["reserved_upper_bound_usd"] is None
    assert missing["cost"]["billing_status"] == "UNKNOWN"
    assert missing["eval_case_id"] is None

    local = project(provider_journals={}, provider_journals_complete=True)
    assert local["provider_calls"]["attempted"] == 0
    assert local["usage"]["reported_total_tokens"] == 0
    assert local["cost"]["reserved_upper_bound_usd"] == 0
    assert local["nodes"] == []


def test_restored_worker_keeps_only_the_original_attempt_charges():
    trace = dict(run_id="ask-1", events=[
        dict(event="supervisor_decision", round=0, action="delegate", tasks=["t1"]),
        dict(event="worker_finished", task="t1", attempt=1, status="ACCEPTED"),
    ], tasks=[dict(id="t1", status="ACCEPTED", attempt=1, round=0, evidence_keys=["visible", "revoked"],
                   result_ref="tasks/t1/attempt-0-1/row.json")])
    journals = {"tasks/t1/attempt-0-1": journal("SUCCEEDED")}
    initial = project(orchestration_trace=trace, provider_journals=journals, provider_journals_complete=True)
    restored = project(orchestration_trace=trace, provider_journals=journals, provider_journals_complete=True,
                       branch_states=[{"branch": "t1", "state": "REUSED", "reason": "VERIFIED"}],
                       authorized_evidence_refs={"visible"})
    assert restored["provider_calls"] == initial["provider_calls"]
    assert restored["usage"] == initial["usage"]
    assert restored["cost"] == initial["cost"]
    node = next(node for node in restored["nodes"] if node.get("event") == "checkpoint_state")
    assert node["recovery_state"] == "REUSED"
    assert node["evidence_refs"] == ["visible"]
    assert initial["nodes"][-2]["evidence_refs"] is None
    with pytest.raises(ValueError, match="another run"):
        project(orchestration_trace={**trace, "run_id": "unrelated"})


def test_tool_refs_follow_the_actual_ledger_and_runtime_turns_do_not_rebill():
    state = _sample_state()
    quote = "private source quote"
    evidence = dict(evidence_id="ev1", relative_path="private-note.md", content_sha256="c" * 64,
                    line_start=1, line_end=1, quote=quote, status="verified",
                    quote_sha256=hashlib.sha256(quote.encode()).hexdigest())
    tool = ToolExecutionRecord("call1", state.run_id, "task1", "agent1", "read_verified_note", 1,
                               "completed", arguments={"query": "private arguments"},
                               result={"tool_id": "read_verified_note", "status": "ok", "value": evidence})
    retry = AttemptRecord("step-attempt", "retrieve_context", 2, "hash", "idem",
                          status="failed", retry_index=1, error_code="TOOL_READ_FAILED")
    turn = ModelExecutionRecord(state.run_id, "turn1", "task1", "agent1", 1, "completed",
                                usage=journal("SUCCEEDED")["attempts"][0]["usage"])
    records = replace(state, tool_ledger=[tool], attempts=[retry], model_executions=[turn])
    scope = "tasks/t1/attempt-0-1"
    got = project(runtime_states={scope: records}, authorized_evidence_refs={"ev1"},
                  provider_journals={scope: journal("SUCCEEDED")}, provider_journals_complete=True)
    assert got["provider_calls"]["attempted"] == 1
    assert got["nodes"][0]["retry_index"] == 1
    assert got["nodes"][1]["turn_id"] == "turn1"
    assert got["nodes"][2]["evidence_refs"] == ["ev1"]
    assert "TOOL_READ_FAILED" in got["failure_codes"]
    assert "private" not in json.dumps(got)


def test_reads_the_real_budget_writer_contract_without_rewriting_receipts(tmp_path):
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(
        action=ModelAction.final("fixture"), usage=ModelUsage(input_tokens=13, output_tokens=7),
    ))
    bound = session(tmp_path, adapter)
    try:
        bound.complete(request())
    finally:
        bound.session.close()

    raw = (tmp_path / "budget.json").read_bytes()
    got = project(provider_journals={"provider": json.loads(raw)}, provider_journals_complete=True)
    assert got["usage"]["reported_input_tokens"] == 13
    assert got["usage"]["reported_total_tokens"] is None  # The writer never received a total.
    assert got["cost"]["estimated_actual_usage_usd"] == pytest.approx(.000048)
    assert got["cost"]["reserved_upper_bound_usd"] == .1
    assert (tmp_path / "budget.json").read_bytes() == raw
