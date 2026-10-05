from __future__ import annotations

import importlib
import hashlib
from types import SimpleNamespace

import pytest


def _guard(response, *, callback=None):
    harness = importlib.import_module(
        "tests.smoke.test_m12_real_provider_team_decision_smoke"
    )
    delegate = harness.RecordingDelegate(response=response)
    aggregate = harness.AggregateUsage()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=aggregate,
        on_response_accepted=callback,
    )
    return harness, delegate, aggregate, guard


def _generate(guard):
    return guard.generate_content(
        model=guard.delegate.api_endpoint and importlib.import_module(
            "tests.smoke.test_m12_real_provider_team_decision_smoke"
        ).MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )


def _runner():
    return importlib.import_module("scripts.run_public_memory_clean_eval")


def test_e2_requalification_uses_new_output_dir_and_preserves_preregistered_assets():
    runner = _runner()
    asset_dir = runner.CLEAN_EVAL_ROOT / "e2_revised_mh_only_20261005"
    qualification_dir = asset_dir / "final_evaluation_v1_20261005"

    assert runner.E2_QUALIFICATION_DIR == qualification_dir
    assert runner.E2_SUBSET_MANIFEST_PATH == asset_dir / "E2_SUBSET_MANIFEST.json"
    assert runner.E2_SCORE_PROTOCOL_PATH == asset_dir / "E2_SCORE_PROTOCOL.json"
    assert runner._e2_qualification_artifact_path(
        qualification_dir, "E2_SUBSET_MANIFEST.json"
    ) == runner.E2_SUBSET_MANIFEST_PATH
    assert runner._e2_qualification_artifact_path(
        qualification_dir, "E2_SCORE_PROTOCOL.json"
    ) == runner.E2_SCORE_PROTOCOL_PATH
    assert runner._e2_qualification_artifact_path(
        qualification_dir, "mh_6k_preflight.json"
    ) == qualification_dir / "mh_6k_preflight.json"
    assert runner.E2_QUALIFICATION_DIR / "mh_6k_preflight.json" != asset_dir / "mh_6k_preflight.json"


def test_authorized_baseline_budget_hash_matches_plan_specific_contract():
    runner = _runner()
    ledger = {"ledger_sha256": "ledger-hash"}

    assert runner._authorization_baseline_budget_hash_matches(
        {"budget": {"formal_budget_ledger_sha256": "ledger-hash"}},
        ledger,
        e2_plan=True,
    ) is True
    assert runner._authorization_baseline_budget_hash_matches(
        {"budget": {"formal_budget_ledger_sha256": "different-hash"}},
        ledger,
        e2_plan=True,
    ) is False
    assert runner._authorization_baseline_budget_hash_matches(
        {"formal_budget_ledger_sha256": "ledger-hash"},
        ledger,
        e2_plan=False,
    ) is True
    assert runner._authorization_baseline_budget_hash_matches(
        {"formal_budget_ledger_sha256": "ledger-hash"},
        ledger,
        e2_plan=True,
    ) is False


def test_zero_request_e2_stop_does_not_consume_frozen_subset():
    runner = _runner()

    assert runner._verify_zero_provider_stopped_e2_run(
        runner.E2_ZERO_PROVIDER_STOPPED_RUN_ID
    ) is True
    subset = runner._read_e2_subset_manifest()

    assert subset["subset_sha256"] == runner._build_e2_subset_manifest_payload()["subset_sha256"]
    assert subset["question_count"] == 92
    assert len(subset["excluded_prior_generation_qa_ids"]) == 8
    assert subset["prior_generation_overlap"] == []


def test_h3_1_reconciliation_reference_requires_matching_scoped_seal_and_passed_evidence(tmp_path):
    runner = _runner()
    actual = (
        runner.CLEAN_EVAL_ROOT / "H3_1_EVIDENCE_RECONCILIATION_20261005.json"
    )
    assert runner._verify_h3_1_reconciliation_artifact(actual) is True

    wrong = tmp_path / "reconciliation.json"
    runner._json_write(
        wrong,
        {
            "run_id": "other-run",
            "verification_status": "PASS",
            "overall_evidence_checks_pass": True,
        },
    )
    runner._seal_file(
        wrong,
        identity="h3-1-evidence-reconciliation:other-run:20261005",
    )
    assert runner._verify_h3_1_reconciliation_artifact(wrong) is False


def _journal_for_response(response, *, blocked=False, reason=None):
    runner = _runner()
    usage = runner._response_usage_evidence(response)
    request_hash = hashlib.sha256(b"fake-request").hexdigest()
    response_hash = hashlib.sha256(str(response.get("text", "")).encode()).hexdigest()
    base = {
        "run_id": "fake-run",
        "sequence": 1,
        "qa_id": "qa-1",
        "method": runner.METHODS[0],
        "attempt_no": 1,
        "attempt_id": "attempt-1",
        "generation_request_sha256": request_hash,
    }
    usage_fields = {
        key: usage[key]
        for key in (
            "usage_status",
            "usage_present",
            "usage_metadata",
            "usage_missing_fields",
            "usage_sources",
            "provider_usage_fields",
            "usage_invalid_fields",
            "usage_access_failures",
            "billing_uncertainty",
            "billing_basis",
        )
    }
    received = {
        **base,
        "event": "RESPONSE_RECEIVED",
        "request_sha256": request_hash,
        "response_sha256": response_hash,
        "provider_response_accepted": True,
        "model": runner.MODEL,
        **usage_fields,
    }
    persisted = {
        **base,
        "event": "RESPONSE_EVIDENCE_PERSISTED",
        "request_sha256": request_hash,
        "response_sha256": response_hash,
        "evidence_sha256": hashlib.sha256(b"fake-evidence").hexdigest(),
    }
    validated = {
        **received,
        "event": "USAGE_VALIDATED",
    }
    completion = {
        **base,
        "event": "PROVIDER_ATTEMPT_COMPLETED",
        "provider_outcome": "LOCAL_HARNESS_BLOCK" if blocked else "PROVIDER_COMPLETE",
        "provider_response_accepted": True,
        "generation_request_sha256": request_hash,
        "response_sha256": response_hash,
        "provider_input_tokens": usage["usage_metadata"]["input_tokens"],
        "provider_output_tokens": usage["usage_metadata"]["output_tokens"],
        "provider_thinking_tokens": usage["usage_metadata"]["thinking_tokens"],
        "usage_status": usage["usage_status"],
        "usage_missing_fields": usage["usage_missing_fields"],
        "billing_uncertainty": usage["billing_uncertainty"],
        "billing_basis": usage["billing_basis"],
    }
    events = [
        {**base, "event": "CASE_METHOD_STARTED"},
        {**base, "event": "PROVIDER_ATTEMPT_STARTED"},
        received,
        persisted,
        validated,
        completion,
        {
            **base,
            "event": "CASE_METHOD_BLOCKED" if blocked else "CASE_METHOD_COMPLETED",
            **({"reason_code": reason} if blocked else {}),
        },
    ]
    return runner, events, usage, request_hash, response_hash


def test_missing_output_usage_returns_accepted_response_with_reserved_cost():
    accepted = []
    harness, delegate, aggregate, guard = _guard(
        {
            "text": "synthetic accepted answer",
            "usage_metadata": {
                "prompt_token_count": 7,
                "thoughts_token_count": 157,
            },
        },
        callback=accepted.append,
    )

    response = _generate(guard)

    assert response["text"] == "synthetic accepted answer"
    assert accepted == [response]
    assert guard.provider_response_accepted is True
    assert guard.preflight_records[0]["reported_output_tokens"] == harness.UNAVAILABLE
    assert guard.preflight_records[0]["reported_thinking_tokens"] == 157
    assert guard.reserved_unknown_output_tokens == 512 - 157
    assert guard.reported_billable_output_tokens == 157
    assert aggregate.reserved_unknown_output_tokens + aggregate.reported_billable_output_tokens == 512
    assert guard.last_guard_state == "RESPONSE_ACCEPTED_USAGE_INCOMPLETE"
    assert guard._terminal is True
    assert len(delegate.calls) == 1

    with pytest.raises(harness.SmokeBlocked, match="terminal|PREFLIGHT"):
        _generate(guard)
    assert len(delegate.calls) == 1
    assert harness._combined_cost_usd(
        aggregate.estimated_input_tokens,
        aggregate.preflight_counted_input_tokens,
        aggregate.reported_billable_output_tokens + aggregate.reserved_unknown_output_tokens,
    ) >= harness._combined_cost_usd(7, 7, 512)


def test_missing_usage_object_keeps_receipt_and_reserves_full_output_cap():
    accepted = []
    harness, delegate, aggregate, guard = _guard(
        {"text": "synthetic accepted answer"},
        callback=accepted.append,
    )

    response = _generate(guard)

    assert response["text"] == "synthetic accepted answer"
    assert accepted == [response]
    record = guard.preflight_records[0]
    assert record["reported_input_tokens"] == harness.UNAVAILABLE
    assert record["reported_output_tokens"] == harness.UNAVAILABLE
    assert aggregate.reported_input_tokens == 0
    assert aggregate.reported_output_tokens == 0
    assert aggregate.reserved_unknown_output_tokens == 512
    assert aggregate.reported_billable_output_tokens == 0
    assert guard.last_guard_state == "RESPONSE_ACCEPTED_USAGE_INCOMPLETE"
    assert guard._terminal is True
    assert len(delegate.calls) == 1


def test_accepted_response_callback_failure_terminates_client_without_replay():
    harness, delegate, _aggregate, guard = _guard(
        {"text": "synthetic accepted answer"},
        callback=lambda _response: (_ for _ in ()).throw(OSError("local journal unavailable")),
    )

    with pytest.raises(OSError, match="local journal unavailable"):
        _generate(guard)

    assert guard._terminal is True
    assert len(delegate.calls) == 1
    assert len(delegate.count_calls) == 1
    with pytest.raises(harness.SmokeBlocked, match="terminal|PREFLIGHT"):
        _generate(guard)
    assert len(delegate.calls) == 1


def test_usage_normalization_preserves_unknown_fields_and_their_sources():
    runner = _runner()
    complete = runner._response_usage_evidence(
        {"usage_metadata": {"prompt_token_count": 10, "candidates_token_count": 4, "thoughts_token_count": 2}}
    )
    thinking_only = runner._response_usage_evidence(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(prompt_token_count=10, thoughts_token_count=7)
        )
    )
    missing = runner._response_usage_evidence({"text": "answer"})

    assert complete["usage_status"] == "COMPLETE"
    assert complete["usage_metadata"] == {"input_tokens": 10, "output_tokens": 4, "thinking_tokens": 2}
    assert complete["usage_sources"]["output_tokens"] == "candidates_token_count"
    assert thinking_only["usage_status"] == "INCOMPLETE"
    assert thinking_only["usage_metadata"] == {"input_tokens": 10, "output_tokens": None, "thinking_tokens": 7}
    assert thinking_only["usage_missing_fields"] == ["output_tokens"]
    assert thinking_only["billing_uncertainty"] is True
    assert missing["usage_status"] == "INCOMPLETE"
    assert missing["usage_metadata"] == {"input_tokens": None, "output_tokens": None, "thinking_tokens": None}
    assert missing["provider_usage_object_present"] is False


@pytest.mark.parametrize(
    "response",
    [
        {"text": "complete", "usage_metadata": {"prompt_token_count": 3, "candidates_token_count": 5}},
        {"text": "incomplete", "usage_metadata": {"prompt_token_count": 3, "thoughts_token_count": 4}},
        {"text": "missing usage"},
    ],
)
def test_response_receipt_precedes_usage_validation_and_incomplete_receipt_stays_valid(response):
    runner, events, usage, request_hash, response_hash = _journal_for_response(response)

    summary = runner._validate_attempt_journal_events(
        events,
        expected_case_count=1,
        scoring_required=False,
    )

    assert [event["event"] for event in events].index("RESPONSE_RECEIVED") < [
        event["event"] for event in events
    ].index("USAGE_VALIDATED")
    assert events[2]["response_sha256"] == response_hash
    assert events[2]["request_sha256"] == request_hash
    assert summary["integrity_valid"] is True
    assert summary["provider_response_acceptances"] == 1
    assert events[5]["provider_outcome"] == "PROVIDER_COMPLETE"
    if usage["usage_status"] == "INCOMPLETE":
        assert events[2]["usage_metadata"]["output_tokens"] is None
        assert events[5]["billing_basis"] == "RESERVED_ATTEMPT_UPPER_BOUND"


def test_usage_guard_block_keeps_accepted_receipt_and_is_not_a_provider_error():
    runner, events, usage, _request_hash, response_hash = _journal_for_response(
        {"text": "synthetic", "usage_metadata": {"prompt_token_count": 3, "thoughts_token_count": 4}},
        blocked=True,
        reason="CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
    )
    summary = runner._validate_attempt_journal_events(
        events,
        expected_case_count=1,
        scoring_required=False,
    )

    assert summary["integrity_valid"] is True
    assert summary["terminal_method_cases"] == 1
    assert events[2]["response_sha256"] == response_hash
    assert events[5]["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert events[5]["provider_response_accepted"] is True
    assert events[6]["event"] == "CASE_METHOD_BLOCKED"
    assert usage["usage_status"] == "INCOMPLETE"


def test_conservative_attempt_reservation_and_case_local_allowlist_fail_closed():
    runner = _runner()
    safe_attempt = {
        "attempt_cost_upper_bound_reserved_usd": 0.04,
        "static_input_token_upper_bound": 400,
        "projected_cell_cost_upper_bound_usd": 2.0,
        "projected_global_cost_upper_bound_usd": 8.0,
    }
    safe_plan = {
        "one_attempt_cost_upper_bound_usd": 0.04,
        "static_input_token_upper_bound": 400,
    }
    assert runner._diagnostic_cost_reservation_is_safe(
        safe_attempt,
        safe_plan,
        run_hard_cost_cap=10.0,
        global_hard_cost_cap=30.0,
    )
    unsafe_attempt = {**safe_attempt, "projected_global_cost_upper_bound_usd": 30.01}
    assert not runner._diagnostic_cost_reservation_is_safe(
        unsafe_attempt,
        safe_plan,
        run_hard_cost_cap=10.0,
        global_hard_cost_cap=30.0,
    )

    response_evidence = {
        "provider_response_accepted": True,
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "usage_status": "INCOMPLETE",
        "usage_metadata": {"input_tokens": 9, "output_tokens": None, "thinking_tokens": 3},
        "usage_present": True,
        "billing_uncertainty": True,
        "billing_basis": "RESERVED_ATTEMPT_UPPER_BOUND",
    }
    assert runner._formal_case_local_block_is_fatal(
        "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
        response_evidence=response_evidence,
        provider_response_accepted=True,
        journal_integrity_valid=True,
        cost_reservation_safe=True,
    ) is False
    assert runner._formal_case_local_block_is_fatal(
        "CLEAN_EVAL_BLOCK_UNKNOWN_GUARD",
        response_evidence=response_evidence,
        provider_response_accepted=True,
        journal_integrity_valid=True,
        cost_reservation_safe=True,
    ) is True


def test_e2_score_gate_counts_fixed_denominator_unpaired_and_unknown_guard_stops_without_gold(monkeypatch):
    runner = _runner()
    qa_ids = [f"qa-{index}" for index in range(runner.E2_SUBSET_QA_COUNT)]
    outputs = [
        {"qa_id": qa_id, "method": method, "provider_outcome": "PROVIDER_COMPLETE", "score": True}
        for qa_id in qa_ids
        for method in runner.METHODS
    ]
    outputs[0] = {
        **outputs[0],
        "usage_status": "INCOMPLETE",
        "provider_output_tokens": None,
        "response_sha256": "c" * 64,
        "reserved_cost_upper_bound_usd": 0.04,
    }
    outputs[2] = {
        **outputs[2],
        "provider_outcome": "LOCAL_HARNESS_BLOCK",
        "reason_code": "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
        "provider_response_accepted": True,
        "safe_error": {
            "provider_outcome": "LOCAL_HARNESS_BLOCK",
            "reason_code": "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
        },
        "score": None,
    }
    monkeypatch.setattr(
        runner,
        "_load_gold_answers_after_seal",
        lambda *_args, **_kwargs: pytest.fail("Gold must remain locked while checking eligibility"),
    )

    gate = runner._score_protocol_eligibility(outputs, qa_ids)

    assert gate["eligible_for_gold_unlock"] is True
    assert gate["planned_questions"] == 92
    assert gate["not_evaluated_method_cases"] == 1
    assert gate["paired_questions"] == 91
    assert gate["case_local_block_reason_codes"] == {
        "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED": 1
    }
    assert outputs[0]["provider_output_tokens"] is None
    assert runner._score_outcome_metrics(
        [
            {"score": True, "provider_outcome": "PROVIDER_COMPLETE"},
            {"score": None, "provider_outcome": "LOCAL_HARNESS_BLOCK"},
        ],
        planned_denominator=92,
    )["not_evaluated"] == 91

    unknown_block = list(outputs)
    unknown_block[2] = {**unknown_block[2], "reason_code": "CLEAN_EVAL_BLOCK_UNKNOWN_GUARD"}
    assert runner._score_protocol_eligibility(unknown_block, qa_ids)["eligible_for_gold_unlock"] is False

    provider_failure = [
        {**row, "safe_error": None} if row.get("provider_outcome") == "PROVIDER_COMPLETE" else row
        for row in outputs
    ]
    provider_failure[1] = {
        **provider_failure[1],
        "provider_outcome": "PROVIDER_ERROR",
        "provider_response_accepted": False,
        "reason_code": "PROVIDER_ERROR_TIMEOUT",
        "safe_error": {
            "provider_outcome": "PROVIDER_ERROR",
            "reason_code": "PROVIDER_ERROR_TIMEOUT",
        },
        "score": None,
    }
    assert runner._score_protocol_eligibility(provider_failure, qa_ids)["eligible_for_gold_unlock"] is True
    provider_failure[1] = {**provider_failure[1], "provider_response_accepted": True}
    invalid_failure_gate = runner._score_protocol_eligibility(provider_failure, qa_ids)
    assert invalid_failure_gate["eligible_for_gold_unlock"] is False
    assert "ACCEPTED_RESPONSE_MISCLASSIFIED_AS_PROVIDER_ERROR" in invalid_failure_gate["errors"]


def test_partial_finalizer_lifecycle_remains_integrity_valid_but_not_protocol_complete():
    runner, events, _usage, _request_hash, _response_hash = _journal_for_response(
        {"text": "synthetic", "usage_metadata": {"prompt_token_count": 3, "candidates_token_count": 5}}
    )

    partial = runner._validate_attempt_journal_events(
        events,
        expected_case_count=2,
        scoring_required=False,
    )

    assert partial["integrity_valid"] is True
    assert partial["method_case_lifecycles_closed"] is True
    assert partial["terminal_method_cases"] == 1
    assert partial["protocol_complete"] is False


def test_fake_provider_e2e_persists_incomplete_receipt_before_usage_validation(tmp_path):
    runner = _runner()
    response = {
        "text": "synthetic accepted answer",
        "usage_metadata": {"prompt_token_count": 3, "thoughts_token_count": 37},
    }
    request_hash = hashlib.sha256(b"fake-e2e-request").hexdigest()
    accepted_evidence = []
    receipt_callback = []

    def persist_accepted(fake_response):
        evidence = runner._persist_accepted_response_evidence(
            tmp_path / "accepted_response_evidence.jsonl",
            run_id="fake-e2e",
            sequence=1,
            logical_case_id="qa-1",
            method=runner.METHODS[0],
            attempt_no=1,
            logical_attempt_id="logical-1",
            model=runner.MODEL,
            request_hash=request_hash,
            response=fake_response,
            on_response_received=receipt_callback.append,
        )
        accepted_evidence.append(evidence)

    harness, delegate, aggregate, guard = _guard(response, callback=persist_accepted)
    returned = _generate(guard)
    persisted = runner._read_accepted_response_evidence(tmp_path / "accepted_response_evidence.jsonl")
    runner, events, usage, _request_hash, _response_hash = _journal_for_response(response)
    lifecycle = runner._validate_attempt_journal_events(
        events,
        expected_case_count=1,
        scoring_required=False,
    )

    assert returned["text"] == "synthetic accepted answer"
    assert len(delegate.count_calls) == 1
    assert len(delegate.calls) == 1
    assert aggregate.reserved_unknown_output_tokens == 512 - 37
    assert receipt_callback[0]["usage_status"] == "INCOMPLETE"
    assert receipt_callback[0]["usage_metadata"]["output_tokens"] is None
    assert accepted_evidence[0]["response_sha256"] == persisted["rows"][0]["response_sha256"]
    assert persisted["integrity_valid"] is True
    assert persisted["rows"][0]["billing_basis"] == "RESERVED_ATTEMPT_UPPER_BOUND"
    assert lifecycle["integrity_valid"] is True
    assert lifecycle["provider_response_acceptances"] == 1
    assert usage["usage_status"] == "INCOMPLETE"
    assert lifecycle["protocol_complete"] is True


def test_fake_e2_score_run_reports_not_evaluated_without_changing_accuracy_denominator(tmp_path, monkeypatch):
    runner = _runner()
    qa_ids = [f"qa-{index}" for index in range(runner.E2_SUBSET_QA_COUNT)]
    case = SimpleNamespace(
        questions=tuple(SimpleNamespace(question_id=qa_id) for qa_id in qa_ids),
        facts=(),
    )
    plan_rows = []
    outputs = []
    for index, qa_id in enumerate(qa_ids):
        for method in runner.METHODS:
            sequence = len(plan_rows) + 1
            plan_rows.append(
                {
                    "sequence": sequence,
                    "qa_id": qa_id,
                    "method": method,
                    "context_evidence_ids_selected": ["evidence-1"],
                    "retrieval_evidence_ids": ["evidence-1"],
                }
            )
            blocked = index == 0 and method == runner.METHODS[0]
            usage_incomplete = index == 1 and method == runner.METHODS[1]
            outputs.append({
                    "sequence": sequence,
                    "qa_id": qa_id,
                    "method": method,
                    "provider_outcome": "LOCAL_HARNESS_BLOCK" if blocked else "PROVIDER_COMPLETE",
                    "reason_code": (
                        "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"
                        if blocked
                        else None
                    ),
                    "provider_response_accepted": not blocked,
                    **(
                        {
                            "safe_error": {
                                "provider_outcome": "LOCAL_HARNESS_BLOCK",
                                "reason_code": "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
                            }
                        }
                        if blocked
                        else {}
                    ),
                    "response_text": "synthetic correct answer",
                    "provider_attempts": 1,
                    "selected_source_evidence_ids": ["evidence-1"],
                    "context_tokens_estimated": 8,
                    "memory_lookup_status": "NOT_APPLICABLE" if method == runner.METHODS[0] else "MISS",
                    **(
                        {
                            "usage_status": "INCOMPLETE",
                            "provider_output_tokens": None,
                            "billing_uncertainty": True,
                            "reserved_cost_upper_bound_usd": 0.04,
                        }
                        if usage_incomplete
                        else {}
                    ),
                })

    provider_outputs_path = tmp_path / "provider_outputs.json"
    runner._json_write(provider_outputs_path, {"provider_outputs": outputs, "gold_values_read": False})
    runner._seal_file(
        provider_outputs_path,
        identity=f"provider-outputs:{runner.CELLS['mh_6k'].source}",
    )
    fake_answers = {qa_id: ["synthetic correct answer"] for qa_id in qa_ids}
    gold_calls = []
    monkeypatch.setattr(
        runner,
        "_load_gold_answers_after_seal",
        lambda source, requested, **_kwargs: gold_calls.append((source, requested)) or fake_answers,
    )
    fingerprints = runner.implementation_fingerprints()

    report, _summary = runner._score_sealed_run(
        root=tmp_path,
        cell_key="mh_6k",
        case=case,
        preflight={
            "execution_plan": plan_rows,
            "dataset_revision": "fake-revision",
            "subset_sha256": "a" * 64,
            "source_subset_sha256": "b" * 64,
            "freeze_manifest_sha256": "c" * 64,
            "runtime_execution_plan_sha256": "d" * 64,
        },
        provider_outputs=outputs,
        provider_outputs_path=provider_outputs_path,
        fingerprints=fingerprints,
    )

    assert len(gold_calls) == 1
    assert report["method_metrics"][runner.METHODS[0]]["planned"] == 92
    assert report["method_metrics"][runner.METHODS[0]]["correct"] == 91
    assert report["method_metrics"][runner.METHODS[0]]["not_evaluated"] == 1
    assert report["method_metrics"][runner.METHODS[1]]["correct"] == 92
    assert report["paired_complete"]["question_count"] == 91
    assert len(report["per_method_question_results"]) == 184
    assert report["score_unlock_gate"]["eligible_for_gold_unlock"] is True
    assert outputs[3]["usage_status"] == "INCOMPLETE"
    assert outputs[3]["provider_output_tokens"] is None


def test_evaluation_guard_policy_separates_identity_scoreability_cost_and_observability():
    runner = _runner()
    decide = runner._evaluation_guard_decision
    base = {
        "phase": "POST_RESPONSE",
        "provider_response_accepted": True,
        "response_valid": True,
        "evidence_valid": True,
        "cost_upper_bound_valid": True,
        "dataset_identity_valid": True,
        "scoring_identity_valid": True,
    }

    assert decide("EVALUATION_CRITICAL", **base) == "RUN_HARD_STOP"
    assert decide(
        "SCOREABILITY_CRITICAL", **{**base, "response_valid": False}
    ) == "CASE_NOT_EVALUATED"
    assert decide(
        "SCOREABILITY_CRITICAL", **{**base, "evidence_valid": False}
    ) == "RUN_HARD_STOP"
    assert decide("COST_TELEMETRY_DEGRADED", **base) == "CONTINUE_WITH_DEGRADED_TELEMETRY"
    assert decide(
        "COST_TELEMETRY_DEGRADED", **{**base, "response_valid": False}
    ) == "CASE_NOT_EVALUATED"
    assert decide(
        "COST_TELEMETRY_DEGRADED", **{**base, "cost_upper_bound_valid": False}
    ) == "RUN_HARD_STOP"
    assert decide("OBSERVABILITY_FORMAT_ONLY", **base) == "WARNING_ONLY"
    assert decide(
        "OBSERVABILITY_FORMAT_ONLY", **{**base, "dataset_identity_valid": False}
    ) == "RUN_HARD_STOP"
    assert runner._guard_criticality_level("E2_PRE_RUN_SUBSET_PLAN_MISMATCH") == "EVALUATION_CRITICAL"
    assert runner._guard_criticality_level("IMPLEMENTATION_FINGERPRINT_MISMATCH") == "EVALUATION_CRITICAL"
    assert runner._guard_criticality_level("DIAGNOSTIC_GOLD_ACCESS_VIOLATION") == "EVALUATION_CRITICAL"
    assert runner._guard_criticality_level("RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED") == "SCOREABILITY_CRITICAL"
    assert runner._guard_criticality_level("OPTIONAL_LATENCY_MISSING") == "COST_TELEMETRY_DEGRADED"
    assert runner._guard_criticality_level("DEBUG_TRACEBACK_MISSING") == "OBSERVABILITY_FORMAT_ONLY"
    assert decide(
        "COST_TELEMETRY_DEGRADED", **{**base, "cost_upper_bound_valid": False}
    ) == "RUN_HARD_STOP"


def test_evaluation_guard_criticality_inventory_covers_every_live_guard():
    from scripts.audit_public_memory_clean_eval_guards import build_guard_inventory

    inventory = build_guard_inventory()

    assert inventory["status"] == "PASS"
    assert inventory["production_reachable_throw_sites"] > 0
    assert inventory["unclassified_criticality_sites"] == 0
    assert all(
        row["criticality_level"] in {
            "EVALUATION_CRITICAL",
            "SCOREABILITY_CRITICAL",
            "COST_TELEMETRY_DEGRADED",
            "OBSERVABILITY_FORMAT_ONLY",
        }
        for row in inventory["sites"]
    )


def test_final_coverage_gate_uses_preregistered_95_percent_threshold():
    runner = _runner()
    qa_ids = [f"qa-{index}" for index in range(runner.E2_SUBSET_QA_COUNT)]
    outputs = [
        {
            "qa_id": qa_id,
            "method": method,
            "provider_outcome": "PROVIDER_COMPLETE",
            "score": True,
        }
        for qa_id in qa_ids
        for method in runner.METHODS
    ]
    outputs[0] = {
        **outputs[0],
        "provider_outcome": "LOCAL_HARNESS_BLOCK",
        "reason_code": "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
        "provider_response_accepted": True,
        "safe_error": {
            "provider_outcome": "LOCAL_HARNESS_BLOCK",
            "reason_code": "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
        },
        "score": None,
    }

    gate = runner._score_protocol_eligibility(outputs, qa_ids)

    assert runner.FINAL_EVALUATION_MIN_SCORED_COVERAGE == 0.95
    assert runner.FINAL_EVALUATION_MIN_PAIRED_COVERAGE == 0.95
    assert gate["minimum_scored_coverage"] == 0.95
    assert gate["minimum_paired_coverage"] == 0.95
    assert gate["eligible_for_gold_unlock"] is True
    assert gate["scored_coverage_by_method"][runner.METHODS[0]] >= 0.95
    assert gate["paired_coverage"] >= 0.95
