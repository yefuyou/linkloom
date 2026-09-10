from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path

import pytest

from linkloom.evaluation.trajectory.dataset import TrajectoryDataset
from linkloom.evaluation.trajectory.grader import grade_case
from linkloom.evaluation.trajectory.harness import TrajectoryHarness
from linkloom.evaluation.trajectory.models import (
    ATTRIBUTION_SIGNAL_PRIORITY,
    TrajectoryCase,
    TrajectoryObservation,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"


def _cases() -> TrajectoryDataset:
    return TrajectoryDataset.from_path(CASES_PATH)


def _error_codes(observation) -> list[str | None]:
    return [
        result["error"]["code"] if isinstance(result.get("error"), dict) else None
        for result in observation.tool_results
    ]


def test_harness_uses_production_prechecks_and_suppresses_denied_executors() -> None:
    harness = TrajectoryHarness(REPO_ROOT)

    malformed = harness.execute_case(_cases().by_id("ARG-01"))
    permission = harness.execute_case(_cases().by_id("PERM-01"))
    budget = harness.execute_case(_cases().by_id("BUD-01"))

    assert _error_codes(malformed) == ["TOOL_INVALID_ARGUMENTS"]
    assert malformed.execution["executor_invocations"] == []
    assert malformed.execution["suppressed_call_ids"] == ["arg-01-search"]

    assert _error_codes(permission) == ["TOOL_PERMISSION_DENIED"]
    assert permission.execution["denied_accesses"] == ["write_file"]
    assert harness.denied_side_effect_executor_counts == {
        "write_file": 0,
        "read_gold": 0,
        "raw_filesystem": 0,
    }

    assert _error_codes(budget) == ["TOOL_BUDGET_EXCEEDED"]
    assert budget.execution["budget"] == {"used": 0, "remaining": 0}


def test_harness_records_actual_checkpoint_and_duplicate_ledger_histories() -> None:
    harness = TrajectoryHarness(REPO_ROOT)

    pending_failure = harness.execute_case(_cases().by_id("EXEC-02"))
    terminal_failure = harness.execute_case(_cases().by_id("EXEC-03"))
    duplicate = harness.execute_case(_cases().by_id("REP-02"))

    assert _error_codes(pending_failure) == ["TOOL_PENDING_CHECKPOINT_FAILED"]
    assert pending_failure.execution["ledger"]["history"] == [
        {"call_id": "exec-02-read", "states": ["pending"]}
    ]
    assert pending_failure.execution["executor_invocations"] == []

    assert _error_codes(terminal_failure) == ["TOOL_TERMINAL_CHECKPOINT_FAILED"]
    assert terminal_failure.execution["ledger"]["history"] == [
        {"call_id": "exec-03-read", "states": ["pending", "completed"]}
    ]
    assert terminal_failure.termination["status"] == "durability_uncertain"

    assert _error_codes(duplicate) == [None, "TOOL_LEDGER_CONFLICT"]
    assert duplicate.execution["ledger"]["history"] == [
        {"call_id": "rep-02-duplicate", "states": ["pending", "completed"]}
    ]
    assert len(duplicate.execution["executor_invocations"]) == 1
    assert duplicate.execution["signals"]["repeated_call_detected"] is True


def test_harness_continues_partial_branches_and_preserves_actual_evidence() -> None:
    harness = TrajectoryHarness(REPO_ROOT)

    failed_then_valid = harness.execute_case(_cases().by_id("PART-02"))
    invalid_then_valid = harness.execute_case(_cases().by_id("PART-03"))

    assert _error_codes(failed_then_valid) == ["TOOL_EXECUTION_FAILED", None]
    assert _error_codes(invalid_then_valid) == ["TOOL_INVALID_OUTPUT", None]
    assert failed_then_valid.termination == {
        "status": "partial",
        "reason": "one_branch_failed",
        "valid_evidence_preserved": True,
    }
    assert invalid_then_valid.execution["evidence_refs"] == [
        "n/retrieval.md#retrieval-contract"
    ]
    assert len(failed_then_valid.execution["executor_invocations"]) == 2


def test_harness_uses_recovery_advice_without_retry_or_replay() -> None:
    harness = TrajectoryHarness(REPO_ROOT)

    read_pending = harness.execute_case(_cases().by_id("CP-03"))
    effect_pending = harness.execute_case(_cases().by_id("CP-04"))

    assert read_pending.execution["recovery_decisions"][0]["decision"] == "safe_to_retry"
    assert effect_pending.execution["recovery_decisions"][0]["decision"] == (
        "requires_manual_decision"
    )
    assert read_pending.execution["executor_invocations"] == []
    assert effect_pending.execution["executor_invocations"] == []
    assert harness.executor_counts.get("side_effect_stub", 0) == 0


def test_harness_does_not_copy_expected_results_or_termination() -> None:
    original = _cases().by_id("NF-01")
    payload = original.to_dict()
    payload["expected_tool_results"][0]["business_status"] = "FOUND"
    payload["expected_termination"] = {
        "status": "safe_error",
        "reason": "tool_execution_failure",
    }
    poisoned = TrajectoryCase.from_dict(payload)

    observation = TrajectoryHarness(REPO_ROOT).execute_case(poisoned)

    assert observation.tool_results[0]["status"] == "ok"
    assert observation.tool_results[0]["business_status"] == "NOT_FOUND"
    assert observation.termination == {
        "status": "completed",
        "reason": "not_found_reported",
    }


def test_harness_refuses_future_cases_instead_of_executing_them() -> None:
    harness = TrajectoryHarness(REPO_ROOT)

    with pytest.raises(ValueError, match="future capability"):
        harness.execute_case(_cases().by_id("RTE-01"))

    assert harness.executed_case_ids == []


def test_expected_attribution_mutation_does_not_change_observed_attribution() -> None:
    original = _cases().by_id("ARG-01")
    baseline = TrajectoryHarness(REPO_ROOT).execute_case(original).attribution
    payload = original.to_dict()
    payload["expected_attribution"] = {
        "primary": "executor",
        "stage": "tool_execution",
    }
    for assertion in payload["deterministic_assertions"]:
        if assertion["assertion"] == "attribution":
            assertion["expected"] = deepcopy(payload["expected_attribution"])

    mutated = TrajectoryHarness(REPO_ROOT).execute_case(
        TrajectoryCase.from_dict(payload)
    )

    assert mutated.attribution == baseline
    assert "expected_attribution" not in inspect.getsource(
        TrajectoryHarness._derive_attribution
    )


def test_attribution_derivation_has_no_fault_input_or_fault_signal() -> None:
    parameters = inspect.signature(TrajectoryHarness._derive_attribution).parameters
    source = inspect.getsource(TrajectoryHarness._derive_attribution)

    assert set(parameters) == {
        "tool_results",
        "termination",
        "recovery_decisions",
        "trajectory",
    }
    assert "fault_kind" not in source
    assert "fault." not in source
    assert all(
        not signal.startswith("fault.")
        for signal, _attribution in ATTRIBUTION_SIGNAL_PRIORITY
    )


def test_registered_trajectory_event_changes_attribution_with_other_inputs_fixed() -> None:
    tool_results = [
        {
            "status": "error",
            "business_status": None,
            "error": {"code": "TOOL_INVALID_OUTPUT"},
        }
    ]
    termination = {"status": "safe_error", "reason": "invalid_tool_output"}
    identity = TrajectoryHarness._derive_attribution(
        tool_results=deepcopy(tool_results),
        termination=deepcopy(termination),
        recovery_decisions=[],
        trajectory=[
            {
                "event": "output_identity_mismatch",
                "expected_call_id": "call-1",
                "actual_call_id": "other-call",
            }
        ],
    )
    invalid_shape = TrajectoryHarness._derive_attribution(
        tool_results=deepcopy(tool_results),
        termination=deepcopy(termination),
        recovery_decisions=[],
        trajectory=[
            {
                "event": "output_validation_failed",
                "expected_shape": "array",
                "actual_shape": "object",
            }
        ],
    )

    assert identity == {
        "primary": "tool_output_validator",
        "stage": "result_identity_validation",
    }
    assert invalid_shape == {
        "primary": "tool_output_validator",
        "stage": "result_validation",
    }


def test_tool_result_changes_attribution_with_other_inputs_fixed() -> None:
    common = {
        "termination": {"status": "safe_error", "reason": "malformed_arguments"},
        "recovery_decisions": [],
        "trajectory": [
            {
                "event": "tool_requested",
                "call_id": "call-1",
                "tool_id": "search_notes",
            }
        ],
    }
    invalid_arguments = TrajectoryHarness._derive_attribution(
        tool_results=[
            {
                "status": "error",
                "business_status": None,
                "error": {"code": "TOOL_INVALID_ARGUMENTS"},
            }
        ],
        **deepcopy(common),
    )
    permission_denied = TrajectoryHarness._derive_attribution(
        tool_results=[
            {
                "status": "error",
                "business_status": None,
                "error": {"code": "TOOL_PERMISSION_DENIED"},
            }
        ],
        **deepcopy(common),
    )

    assert invalid_arguments != permission_denied
    assert permission_denied == {
        "primary": "policy_enforcer",
        "stage": "authorization",
    }


def test_harness_emits_registered_observed_events_used_for_attribution() -> None:
    harness = TrajectoryHarness(REPO_ROOT)

    invalid_shape = harness.execute_case(_cases().by_id("OUT-01"))
    identity_mismatch = harness.execute_case(_cases().by_id("OUT-03"))
    pending_boundary = harness.execute_case(_cases().by_id("CP-01"))

    assert any(
        event["event"] == "output_validation_failed"
        for event in invalid_shape.trajectory
    )
    assert any(
        event["event"] == "output_identity_mismatch"
        for event in identity_mismatch.trajectory
    )
    assert {
        "event": "checkpoint_saved",
        "record_status": "pending",
    } in pending_boundary.trajectory


def test_attribution_assertion_mismatch_fails_deterministic_grade() -> None:
    case = _cases().by_id("ARG-01")
    observed = TrajectoryHarness(REPO_ROOT).execute_case(case)
    payload = observed.to_dict()
    payload["attribution"] = {
        "primary": "executor",
        "stage": "tool_execution",
    }

    result = grade_case(case, TrajectoryObservation.from_dict(payload))

    assert result.status == "FAIL"
    assert result.failed_assertion == "attribution"
    assert result.expected == case.expected_attribution
    assert result.actual == payload["attribution"]
