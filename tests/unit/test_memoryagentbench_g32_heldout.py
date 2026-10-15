from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from benchmarks.memoryagentbench.g32_heldout import (
    EXPECTED_FROZEN,
    G32_HELDOUT_PROFILE,
    G32IntegrityError,
    build_execution_order,
    can_schedule_base_execution,
    can_schedule_retry,
    heldout_qa_ids,
    request_cost_upper_bound_usd,
    validate_frozen_preflight,
)
from scripts import run_memoryagentbench_g3_pilot as g3_runner


def _preflight_packet() -> dict[str, object]:
    qa_ids = heldout_qa_ids()
    plan = build_execution_order(qa_ids)
    for row in plan:
        row.update(
            {
                "generation_request_sha256": "a" * 64,
                "count_tokens_request_sha256": "b" * 64,
                "context_sha256": "c" * 64,
                "static_input_token_upper_bound": 1_200,
            }
        )
    manifest = {
        "qa_ids": qa_ids,
        "subset_hash": EXPECTED_FROZEN["subset_hash"],
        "fingerprints": {
            key: value for key, value in EXPECTED_FROZEN.items()
            if key.endswith("_fingerprint")
        },
    }
    heldout = {
        "dataset_revision": EXPECTED_FROZEN["dataset_revision"],
        "dataset_sha256": EXPECTED_FROZEN["dataset_sha256"],
        "qa_ids": qa_ids,
        "subset_manifest": manifest,
        "execution_plan": plan,
        "continuation_metadata": {
            "subset_manifest_sha256": EXPECTED_FROZEN["subset_manifest_sha256"],
        },
        "cost_estimate": {"hard_cost_cap_usd": 1.0},
    }
    return {
        "status": "READY",
        "heldout_preflight": heldout,
        "heldout_selection": {"previously_used_overlap": []},
        "gold_isolation": {"gold_columns_read": False},
        "provider_calls": {"generation": 0, "count_tokens": 0, "retries": 0},
    }


def test_g32_plan_is_exactly_interleaved_for_qa50_through_qa99() -> None:
    ids = heldout_qa_ids()
    plan = build_execution_order(ids)

    assert ids[0].endswith("no50")
    assert ids[-1].endswith("no99")
    assert len(plan) == 100
    assert [row["sequence"] for row in plan] == list(range(1, 101))
    assert [(row["qa_id"], row["method"]) for row in plan[:4]] == [
        (ids[0], "Flat Retrieval"),
        (ids[0], "LinkLoom Temporal Memory"),
        (ids[1], "Flat Retrieval"),
        (ids[1], "LinkLoom Temporal Memory"),
    ]
    assert plan[-1]["qa_id"] == ids[-1]
    assert plan[-1]["method"] == "LinkLoom Temporal Memory"


def test_g32_manifest_verification_rejects_order_or_fingerprint_drift() -> None:
    frozen = _preflight_packet()
    recomputed = deepcopy(frozen)
    verified = validate_frozen_preflight(frozen, recomputed)
    assert verified["qa_ids"] == heldout_qa_ids()
    assert len(verified["static_input_bounds"]) == 100

    changed = deepcopy(recomputed)
    changed["heldout_preflight"]["subset_manifest"]["fingerprints"]["prompt_fingerprint"] = "d" * 64
    with pytest.raises(G32IntegrityError, match="manifest differs"):
        validate_frozen_preflight(frozen, changed)

    changed = deepcopy(recomputed)
    changed["heldout_preflight"]["execution_plan"][0]["method"] = "LinkLoom Temporal Memory"
    with pytest.raises(G32IntegrityError, match="not interleaved"):
        validate_frozen_preflight(changed, changed)


def test_g32_manifest_verification_fails_closed_on_gold_or_provider_activity() -> None:
    frozen = _preflight_packet()
    changed = deepcopy(frozen)
    changed["gold_isolation"]["gold_columns_read"] = True
    with pytest.raises(G32IntegrityError, match="Gold-isolation"):
        validate_frozen_preflight(changed, changed)

    changed = deepcopy(frozen)
    changed["provider_calls"]["generation"] = 1
    with pytest.raises(G32IntegrityError, match="Provider calls"):
        validate_frozen_preflight(changed, changed)


def test_retry_gate_reserves_all_not_yet_started_base_executions() -> None:
    retry = request_cost_upper_bound_usd(1_400)
    remaining_bases = 40 * request_cost_upper_bound_usd(1_200)

    assert can_schedule_retry(
        spent_or_reserved_cost_usd=0.10,
        retry_worst_case_cost_usd=retry,
        remaining_base_execution_cost_usd=remaining_bases,
        hard_cap_usd=1.00,
    )
    assert not can_schedule_retry(
        spent_or_reserved_cost_usd=0.90,
        retry_worst_case_cost_usd=retry,
        remaining_base_execution_cost_usd=remaining_bases,
        hard_cap_usd=1.00,
    )
    assert not can_schedule_retry(
        spent_or_reserved_cost_usd=0.0,
        retry_worst_case_cost_usd=0.2,
        remaining_base_execution_cost_usd=0.8,
        hard_cap_usd=1.0,
    )


def test_retry_cost_bound_charges_preflight_and_generation_input() -> None:
    assert request_cost_upper_bound_usd(1_000) == pytest.approx(0.00342)
    with pytest.raises(ValueError, match="non-negative"):
        request_cost_upper_bound_usd(-1)


def test_budget_for_attempt_can_reserve_future_base_workload() -> None:
    _, _, aggregate_budget = g3_runner._budget_for_attempt(
        "g32-case",
        max_total_cost_usd=0.75,
    )

    assert aggregate_budget.max_cost_usd == pytest.approx(0.75)


def test_base_cost_gate_reserves_current_and_all_remaining_base_executions() -> None:
    assert can_schedule_base_execution(
        spent_or_reserved_cost_usd=0.0,
        current_and_remaining_base_cost_usd=0.4008375,
        hard_cap_usd=1.0,
    )
    assert not can_schedule_base_execution(
        spent_or_reserved_cost_usd=0.60,
        current_and_remaining_base_cost_usd=0.4008375,
        hard_cap_usd=1.0,
    )


def test_g32_summary_uses_the_explicit_preflight_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = SimpleNamespace(
        provider_requests=0,
        preflight_count_requests=0,
        inference_transport_attempts=0,
        preflight_counted_input_tokens=0,
        reported_input_tokens=0,
        reported_output_tokens=0,
        reported_billable_output_tokens=0,
        estimated_input_tokens=0,
        reported_cost_usd=None,
        preflight_records=[],
    )
    monkeypatch.setattr(g3_runner, "ACTIVE_PROFILE", G32_HELDOUT_PROFILE)

    summary = g3_runner._summarize(
        [],
        aggregate,
        qualification={"offline": True},
        retry_count=0,
        execution_status="COMPLETE",
        preflight_snapshot={
            "cost_estimate": {
                "all_allowed_attempts_cost_upper_bound_usd": 0.42,
            },
        },
    )

    assert summary["all_allowed_attempts_static_cost_upper_bound_usd"] == 0.42


def test_retry_runner_honors_global_cost_gate_before_another_attempt() -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def attempt(number: int) -> dict[str, object]:
        attempts.append(number)
        return {
            "provider_outcome": "PROVIDER_ERROR",
            "failure": {"low_level_failure_class": "TRANSPORT_CONNECT"},
        }

    result = g3_runner.run_bounded_generation_attempts(
        attempt,
        sleep=sleeps.append,
        random_sample=lambda: 0.0,
        on_retry=lambda *_args: False,
    )

    assert attempts == [1]
    assert sleeps == []
    assert len(result) == 1
    assert result[0]["retry_blocked_by_global_cost_guard"] is True
