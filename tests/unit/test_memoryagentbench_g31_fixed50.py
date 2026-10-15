from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.memoryagentbench.g31_fixed50 import (
    G31ProfileError,
    build_execution_order,
    build_subset_manifest,
    canonical_sha256,
    classify_recovery_state,
    estimate_cost_from_pilot,
    fixed_qa_ids,
)
from scripts import run_memoryagentbench_g3_pilot as g3_runner


def _execution_plan() -> list[dict[str, object]]:
    return build_execution_order(fixed_qa_ids())


def test_fixed50_subset_is_exact_first50_and_manifest_hash_is_stable() -> None:
    qa_ids = fixed_qa_ids()
    manifest = build_subset_manifest(
        qa_ids=qa_ids,
        dataset_revision="7ea066982b140a19337e17e60d45d4076e042faf",
        dataset_sha256="24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45",
        split="Conflict_Resolution",
        configuration="FactConsolidation single-hop / 6k",
        case_id="factconsolidation_sh_6k:fixture",
        workspace_id="memoryagentbench:fixture",
    )

    assert qa_ids[:10] == [f"factconsolidation_sh_6k_no{index}" for index in range(10)]
    assert manifest["qa_ids"] == qa_ids
    assert manifest["question_count"] == 50
    assert manifest["includes_original_first_ten"] is True
    assert canonical_sha256(manifest) == canonical_sha256(dict(manifest))


def test_fixed50_rejects_reordered_or_cherry_picked_qa() -> None:
    qa_ids = fixed_qa_ids()

    with pytest.raises(G31ProfileError, match="deterministic official first 50"):
        build_subset_manifest(
            qa_ids=[*qa_ids[1:], qa_ids[0]],
            dataset_revision="revision",
            dataset_sha256="a" * 64,
            split="Conflict_Resolution",
            configuration="single-hop / 6k",
            case_id="case",
            workspace_id="workspace",
        )


def test_fixed50_plan_interleaves_flat_then_temporal_for_all_50_questions() -> None:
    plan = _execution_plan()

    assert len(plan) == 100
    assert [item["sequence"] for item in plan] == list(range(1, 101))
    assert [(item["qa_id"], item["method"]) for item in plan[:6]] == [
        ("factconsolidation_sh_6k_no0", "Flat Retrieval"),
        ("factconsolidation_sh_6k_no0", "LinkLoom Temporal Memory"),
        ("factconsolidation_sh_6k_no1", "Flat Retrieval"),
        ("factconsolidation_sh_6k_no1", "LinkLoom Temporal Memory"),
        ("factconsolidation_sh_6k_no2", "Flat Retrieval"),
        ("factconsolidation_sh_6k_no2", "LinkLoom Temporal Memory"),
    ]
    assert plan[-1]["qa_id"] == "factconsolidation_sh_6k_no49"


def test_cost_estimate_uses_sealed_pilot_and_keeps_one_dollar_cap() -> None:
    estimate = estimate_cost_from_pilot(
        pilot_generation_requests=20,
        pilot_count_tokens_requests=20,
        pilot_preflight_input_tokens=3603,
        pilot_generation_input_tokens=3603,
        pilot_output_tokens=56,
        pilot_max_input_tokens=278,
    )

    assert estimate["expected_base_cost_usd"] == pytest.approx(0.0280725)
    assert estimate["conservative_base_upper_bound_usd"] == pytest.approx(0.2337)
    assert estimate["retry_reserve"]["additional_attempts"] == 200
    assert estimate["retry_reserve"]["cost_upper_bound_usd"] == pytest.approx(0.4674)
    assert estimate["pilot_anchored_conservative_total_usd"] == pytest.approx(0.7011)
    assert estimate["hard_cost_cap_usd"] == 1.00
    assert estimate["pilot_anchored_estimate_within_hard_cap"] is True
    assert estimate["contractual_worst_case_exceeds_hard_cap"] is True
    assert estimate["dynamic_hard_cap_enforced_before_each_count_tokens_and_generation"] is True


def test_fixed50_recovery_classifies_sealed_pending_safe_unknown_and_unstarted() -> None:
    plan = _execution_plan()[:6]
    events = [
        {"event": "CASE_METHOD_STARTED", "sequence": 2, "qa_id": plan[1]["qa_id"], "method": plan[1]["method"]},
        {"event": "CASE_METHOD_STARTED", "sequence": 3, "qa_id": plan[2]["qa_id"], "method": plan[2]["method"]},
        {"event": "CASE_METHOD_COMPLETED", "sequence": 3, "qa_id": plan[2]["qa_id"], "method": plan[2]["method"]},
        {"event": "PROVIDER_ATTEMPT_STARTED", "qa_id": plan[3]["qa_id"], "method": plan[3]["method"], "operation": "generateContent"},
        {"event": "CASE_METHOD_STARTED", "sequence": 5, "qa_id": plan[4]["qa_id"], "method": plan[4]["method"]},
    ]

    classified = classify_recovery_state(
        plan,
        events,
        provider_output_sequences={3, 5},
        sealed_sequences={1},
    )

    assert [item["state"] for item in classified] == [
        "SEALED",
        "SAFE_TO_RESUME",
        "SCORE_PENDING",
        "UNKNOWN_PROVIDER_OUTCOME",
        "OUTPUT_PERSISTED_PENDING_COMPLETION",
        "UNSTARTED",
    ]


def test_recovery_fails_closed_on_output_identity_mismatch() -> None:
    plan = _execution_plan()[:1]

    with pytest.raises(G31ProfileError, match="journal identity"):
        classify_recovery_state(
            plan,
            [{"event": "CASE_METHOD_STARTED", "sequence": 1, "qa_id": "wrong", "method": "Flat Retrieval"}],
            provider_output_sequences=set(),
            sealed_sequences=set(),
        )


def test_recovery_fails_closed_when_completed_case_lost_its_output() -> None:
    plan = _execution_plan()[:1]

    state = classify_recovery_state(
        plan,
        [{"event": "CASE_METHOD_COMPLETED", "sequence": 1, "qa_id": plan[0]["qa_id"], "method": plan[0]["method"]}],
        provider_output_sequences=set(),
        sealed_sequences=set(),
    )

    assert state[0]["state"] == "CORRUPT"


def test_fixed50_resume_recovers_persisted_output_without_replaying_provider(
    tmp_path: Path,
) -> None:
    root = tmp_path / "g3-first50-resume-fixture"
    root.mkdir()
    run_id = "resume-fixture"
    qa_ids = fixed_qa_ids()
    subset = build_subset_manifest(
        qa_ids=qa_ids,
        dataset_revision="revision",
        dataset_sha256="a" * 64,
        split="Conflict_Resolution",
        configuration="single-hop / 6k",
        case_id="case",
        workspace_id="workspace",
    )
    subset_path = root / "subset_manifest.json"
    g3_runner._write_json(subset_path, subset)
    g3_runner._write_json(root / "subset_manifest.seal.json", {
        "sealed": True,
        "sha256": hashlib.sha256(subset_path.read_bytes()).hexdigest(),
    })
    execution_plan = [
        {**item, "request_sha256": f"request-{item['sequence']}"}
        for item in build_execution_order(qa_ids)
    ]
    system_instruction = g3_runner.build_generation_request("fixture")["config"]["system_instruction"]
    run_manifest = {
        "run_id": run_id,
        "stage": g3_runner.G31_FIXED50_PROFILE.stage,
        "execution_profile": g3_runner.G31_FIXED50_PROFILE.name,
        "dataset_sha256": "a" * 64,
        "dataset_revision": "revision",
        "subset_manifest_sha256": canonical_sha256(subset),
        "execution_order": execution_plan,
        "planned_method_case_executions": 100,
        "provider": "Gemini Developer API",
        "model": "gemini-3.8-flash",
        "reader_configuration": {
            "sdk_version": g3_runner.gemini.SDK_VERSION,
            "system_instruction_sha256": hashlib.sha256(str(system_instruction).encode()).hexdigest(),
            "temperature": 0.0,
            "max_output_tokens": g3_runner.PER_REQUEST_OUTPUT_TOKEN_CAP,
            "context_budget_tokens": 3_000,
            "automatic_function_calling": "disabled",
            "per_request_input_token_cap": g3_runner.PER_REQUEST_INPUT_TOKEN_CAP,
        },
        "hard_cost_cap_usd": 1.00,
        "max_total_generation_attempts": 300,
        "sdk_retries": 0,
    }
    g3_runner._write_json(root / "run_manifest.json", run_manifest)
    first = execution_plan[0]
    journal = g3_runner.DurableAttemptJournal(root / "provider_attempt_journal.jsonl", run_id=run_id)
    journal.case_method_event(
        "CASE_METHOD_STARTED",
        sequence=1,
        qa_id=first["qa_id"],
        method=first["method"],
        request_sha256=first["request_sha256"],
        gold_loaded=False,
    )
    journal.provider_attempt_started(
        qa_id=first["qa_id"],
        method=first["method"],
        operation="generateContent",
        logical_turn=1,
        attempt_number=1,
        retry_level=0,
    )
    journal.provider_attempt_completed(
        qa_id=first["qa_id"],
        method=first["method"],
        operation="generateContent",
        logical_turn=1,
        attempt_number=1,
        retry_level=0,
        status="PROVIDER_ERROR",
    )
    result = {
        "sequence": 1,
        "question_id": first["qa_id"],
        "method": first["method"],
        "request_sha256": first["request_sha256"],
        "provider_outcome": "PROVIDER_ERROR",
        "generation_attempt_count": 1,
        "generation_attempts": [{"generation_latency_ms": 10.0}],
        "retry_count": 0,
        "count_tokens_attempts": 1,
        "count_tokens_preflight_records": [{
            "status": "success",
            "token_estimation_source": "PROVIDER_COUNT_TOKENS",
            "counted_input_tokens": 7,
            "elapsed_seconds": 0.01,
            "transport_attempts": 1,
        }],
        "gold_read": False,
        "answer": None,
    }
    output_paths = g3_runner._method_case_paths(
        root,
        sequence=1,
        qa_id=str(first["qa_id"]),
        method=str(first["method"]),
    )
    g3_runner._write_json(output_paths["output"], {
        "schema_version": "memoryagentbench-g3-provider-output/v1",
        "gold_loaded": False,
        "results": [result],
    })
    # Simulate process death after the durable output, before either case event/seal.
    profile = g3_runner.ACTIVE_PROFILE
    try:
        g3_runner._activate_profile(g3_runner.G31_FIXED50_PROFILE)
        outputs, scored, recovery, fallback = g3_runner._load_fixed50_resume_state(
            root,
            run_id=run_id,
            dataset_path=tmp_path / "unused.parquet",
            preflight_snapshot={
                "dataset_sha256": "a" * 64,
                "dataset_revision": "revision",
                "subset_manifest_sha256": canonical_sha256(subset),
                "subset_manifest": subset,
            },
            execution_plan=execution_plan,
            prepared=[SimpleNamespace(context_bundle=SimpleNamespace(rendered_text="fixture"))],
            case=SimpleNamespace(),
            journal=journal,
        )
    finally:
        g3_runner._activate_profile(profile)

    assert len(outputs) == 1
    assert len(scored) == 1
    assert scored[0]["semantic_status"] == "NOT_EVALUATED"
    assert scored[0]["gold_read"] is False
    assert recovery[0]["state"] == "SEALED"
    assert fallback is False
    assert g3_runner.verify_frozen_output_artifact(
        output_paths["output"], output_paths["output_seal"]
    )
    assert output_paths["case_seal"].is_file()
    assert [item["event"] for item in journal.read_events()][-3:] == [
        "CASE_METHOD_COMPLETED",
        "CASE_METHOD_SCORED",
        "CASE_METHOD_SEALED",
    ]
