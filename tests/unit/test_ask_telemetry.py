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


def journal(*outcomes, stage="runtime", usage=None):
    rows = []
    for sequence, outcome in enumerate(outcomes, 1):
        row = dict(sequence=sequence, stage=stage, request_sha256="same-hash", outcome=outcome)
        if outcome == "SUCCEEDED":
            row.update(response_sha256="response", usage=dict(
                usage if usage is not None else dict(input_tokens=30, output_tokens=10, total_tokens=40, duration_ms=123)))
        rows.append(row)
    return dict(schema_version=2, requests=len(rows), attempts=rows, failure=None,
                limits=dict(max_seconds=5, request_timeout_seconds=1))


def test_reported_usage_and_price_estimate_are_separate_and_summed_across_scopes():
    journals = {"router/router-1": journal("SUCCEEDED", stage="ask-router"),
                "supervisor/round-0": journal("SUCCEEDED", stage="supervisor")}
    original = deepcopy(journals)
    got = project(router={"source": "llm", "attempted": True, "decision": "ROUTE",
                          "query": "private question"},
                  provider_journals=journals, provider_journals_complete=True, total_duration_ms=180)
    assert got["provider_calls"]["attempted"] == got["provider_calls"]["completed"] == 2
    assert got["usage"]["total_tokens"] == dict(value=80, source="provider_reported", known_value=80,
                                                unreported_requests=0)
    assert got["usage"]["input_tokens"]["value"] == 60 and got["usage"]["duration_ms"]["value"] == 246
    assert got["usage"]["cached_input_tokens"]["value"] is None
    assert got["usage"]["cached_input_tokens"]["source"] == "unknown"
    assert got["cost"]["estimated_usd"]["value"] == pytest.approx(.00016)
    assert got["cost"]["estimated_usd"]["source"] == "estimated"
    assert got["cost"]["provider_reported_usd"]["value"] is None
    assert got["cost"]["provider_reported_usd"]["source"] == "unknown"
    assert got["total_duration_ms"] == 180
    assert "reserved" not in json.dumps(got) and "private question" not in json.dumps(got)
    assert journals == original


def test_two_workers_sum_exactly_without_counting_sub_counts_twice():
    one = dict(input_tokens=100, output_tokens=40, total_tokens=140, cached_input_tokens=30,
               reasoning_tokens=15, provider_reported_cost_usd=0.001)
    two = dict(input_tokens=7, output_tokens=3, total_tokens=10, cached_input_tokens=2,
               reasoning_tokens=1, provider_reported_cost_usd=0.0005)
    got = project(provider_journals={"tasks/a/attempt-0-1": journal("SUCCEEDED", usage=one),
                                     "tasks/b/attempt-0-1": journal("SUCCEEDED", usage=two)},
                  provider_journals_complete=True)
    usage = got["usage"]
    assert [usage[key]["value"] for key in ("input_tokens", "output_tokens", "total_tokens")] == [107, 43, 150]
    assert usage["cached_input_tokens"]["value"] == 32 and usage["reasoning_tokens"]["value"] == 16
    assert usage["total_tokens"]["source"] == "provider_reported"
    assert got["cost"]["provider_reported_usd"]["value"] == pytest.approx(.0015)
    assert got["cost"]["estimated_usd"]["value"] == pytest.approx((107 + 43 * 5) / 1_000_000)


def test_total_is_derived_once_and_tagged_estimated_when_the_provider_sent_none():
    got = project(provider_journals={"provider": journal("SUCCEEDED", usage=dict(input_tokens=13, output_tokens=7))},
                  provider_journals_complete=True)
    assert got["usage"]["total_tokens"]["value"] == 20 and got["usage"]["total_tokens"]["source"] == "estimated"
    assert got["usage"]["input_tokens"]["source"] == "provider_reported"


def test_unknown_provider_outcome_or_pending_receipt_never_reports_zero_usage():
    budget = journal("SUCCEEDED", "UNCERTAIN", "PENDING")
    # A timeout can also return a durable error envelope with a partial usage field.
    budget["attempts"][1].update(response_sha256="error-envelope", usage={"input_tokens": 4})
    got = project(provider_journals={"tasks/t1/attempt-0-1": budget},
                  provider_journals_complete=True)
    assert got["provider_calls"]["attempted"] is None
    assert got["provider_calls"]["pending"] == 1
    assert got["provider_calls"]["observed_attempted"] == 2
    assert got["usage"]["input_tokens"]["value"] is None
    assert got["usage"]["input_tokens"]["known_value"] == 30
    assert got["usage"]["input_tokens"]["unreported_requests"] == 2
    assert got["cost"]["estimated_usd"]["value"] is None
    assert got["cost"]["estimated_usd"]["known_value"] == pytest.approx(.00008)
    assert all(node["usage"]["input_tokens"]["value"] is None for node in got["nodes"][1:])
    assert got["total_duration_ms"] is None


def test_provider_without_usage_is_unknown_not_an_error():
    got = project(provider_journals={"provider": journal("SUCCEEDED", usage={})}, provider_journals_complete=True)
    assert got["provider_calls"]["completed"] == 1
    assert got["usage"]["total_tokens"]["value"] is None and got["usage"]["total_tokens"]["source"] == "unknown"
    assert got["cost"]["estimated_usd"] == dict(value=None, source="unknown", known_value=None, unreported_requests=1)


def test_missing_router_journal_differs_from_confirmed_local_only_run():
    missing = project(provider_journals={"router/router-1": None})
    assert missing["provider_calls"]["attempted"] is None
    assert missing["usage"]["total_tokens"]["value"] is None
    assert missing["cost"]["estimated_usd"]["source"] == "unknown"
    assert missing["eval_case_id"] is None

    local = project(provider_journals={}, provider_journals_complete=True)
    assert local["provider_calls"]["attempted"] == 0
    assert local["usage"]["total_tokens"]["value"] == 0
    assert local["cost"]["estimated_usd"]["value"] == 0
    assert local["nodes"] == []


def test_legacy_budget_journal_reservations_are_never_usage():
    legacy = dict(requests_reserved=1, input_tokens_reserved=150, output_tokens_reserved=100,
                  cost_reserved_usd=.01, attempts=[dict(sequence=1, stage="runtime", request_sha256="h",
                  input_reserved=150, output_reserved=100, cost_reserved_usd=.01, outcome="SUCCEEDED")])
    got = project(provider_journals={"provider": legacy}, provider_journals_complete=True)
    assert got["provider_calls"]["completed"] == 1
    assert got["usage"]["input_tokens"]["value"] is None and got["usage"]["input_tokens"]["source"] == "unknown"
    assert got["cost"]["estimated_usd"]["value"] is None
    assert got["nodes"][0]["journal_format"] == "legacy"
    assert "150" not in json.dumps(got["usage"]) and "reserved" not in json.dumps(got)


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


def test_reads_the_real_journal_writer_contract_without_rewriting_receipts(tmp_path):
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(
        action=ModelAction.final("fixture"),
        usage=ModelUsage(input_tokens=13, output_tokens=7, cached_input_tokens=5, provider_reported_cost_usd=0.002),
    ))
    bound = session(tmp_path, adapter)
    try:
        bound.complete(request())
    finally:
        bound.session.close()

    raw = (tmp_path / "provider_journal.json").read_bytes()
    row = json.loads(raw)["attempts"][0]
    assert row["estimated_cost_usd"] == pytest.approx(.000048) and row["provider_reported_cost_usd"] == .002
    assert row["duration_ms"] is not None
    got = project(provider_journals={"provider": json.loads(raw)}, provider_journals_complete=True)
    assert got["usage"]["input_tokens"]["value"] == 13
    assert got["usage"]["total_tokens"] == dict(value=20, source="estimated", known_value=20, unreported_requests=0)
    assert got["usage"]["cached_input_tokens"]["value"] == 5
    assert got["cost"]["estimated_usd"]["value"] == pytest.approx(.000048)
    assert got["cost"]["provider_reported_usd"]["value"] == .002
    assert (tmp_path / "provider_journal.json").read_bytes() == raw
