from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from linkloom.evaluation.trajectory.dataset import TrajectoryDataset
from linkloom.evaluation.trajectory.grader import (
    DETERMINISTIC_ASSERTION_HANDLERS,
    grade_case,
)
from linkloom.evaluation.trajectory.models import (
    ASSERTION_REGISTRY,
    OBSERVATION_SCHEMA_VERSION,
    TrajectoryCase,
    TrajectoryObservation,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"


def _dataset() -> TrajectoryDataset:
    return TrajectoryDataset.from_path(CASES_PATH)


def _observation(
    case: TrajectoryCase,
    *,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    executor_invocations: list[dict] | None = None,
    suppressed_call_ids: list[str] | None = None,
) -> TrajectoryObservation:
    return TrajectoryObservation.from_dict(
        {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "case_id": case.case_id,
            "trajectory": deepcopy(case.expected_trajectory),
            "tool_calls": deepcopy(
                case.expected_tool_calls if tool_calls is None else tool_calls
            ),
            "tool_results": deepcopy(
                case.expected_tool_results if tool_results is None else tool_results
            ),
            "termination": deepcopy(case.expected_termination),
            "attribution": deepcopy(case.expected_attribution),
            "execution": {
                "executor_invocations": deepcopy(executor_invocations or []),
                "suppressed_call_ids": deepcopy(suppressed_call_ids or []),
                "budget": {"used": len(executor_invocations or []), "remaining": 0},
                "ledger": {"history": []},
                "evidence_refs": [],
                "denied_accesses": [],
                "recovery_decisions": [],
                "signals": {},
            },
            "metadata": {
                "run_id": "random-run-a",
                "timestamp": "2026-08-24T00:00:00Z",
            },
        }
    )


def test_grader_passes_expected_not_found_observation_without_treating_it_as_error() -> None:
    case = _dataset().by_id("NF-01")
    observation = _observation(
        case,
        executor_invocations=[
            {"call_id": "nf-01-search", "tool_id": "search_notes"}
        ],
    )

    result = grade_case(case, observation)

    assert result.status == "PASS"
    assert result.category == "NOT_FOUND"
    assert result.failed_assertion is None
    assert result.primary_failure_attribution is None
    assert all(assertion["passed"] for assertion in result.assertions)
    assert result.judge == {"status": "NOT_EVALUATED", "provider": None}


def test_grader_reports_corruption_with_expected_actual_and_attribution() -> None:
    case = _dataset().by_id("NF-01")
    corrupted_results = deepcopy(case.expected_tool_results)
    corrupted_results[0].update(
        status="error",
        value=None,
        business_status=None,
        error={
            "code": "TOOL_EXECUTION_FAILED",
            "category": "runtime",
            "message": "Tool execution failed.",
            "retryable": False,
            "safe_to_expose": True,
        },
    )
    observation = _observation(
        case,
        tool_results=corrupted_results,
        executor_invocations=[
            {"call_id": "nf-01-search", "tool_id": "search_notes"}
        ],
    )

    result = grade_case(case, observation)

    assert result.status == "FAIL"
    assert result.failed_assertion == "tool_result_status"
    assert result.expected == ["ok"]
    assert result.actual == ["error"]
    assert result.primary_failure_attribution == "retrieval_business_semantics"
    assert any(not assertion["passed"] for assertion in result.assertions)


def test_future_case_is_not_implemented_with_missing_capability_and_no_judge() -> None:
    case = _dataset().by_id("RTE-01")

    result = grade_case(case, None)

    assert result.status == "NOT_IMPLEMENTED"
    assert result.missing_capability == "routing.model_driven"
    assert result.primary_failure_attribution == "capability_gap"
    assert result.assertions == []
    assert result.judge == {"status": "NOT_EVALUATED", "provider": None}


def test_gating_derives_18_not_implemented_and_leaves_23_executable_cases() -> None:
    results = [grade_case(case, None) for case in _dataset().cases]
    not_implemented = [result for result in results if result.status == "NOT_IMPLEMENTED"]
    executable_without_observation = [result for result in results if result.status == "FAIL"]

    assert len(not_implemented) == 18
    assert len(executable_without_observation) == 23
    assert all(result.missing_capability for result in not_implemented)
    assert all(result.judge["status"] == "NOT_EVALUATED" for result in results)


def test_all_dataset_assertion_kinds_have_registered_deterministic_handlers() -> None:
    assertion_kinds = {
        assertion["assertion"]
        for case in _dataset().cases
        for assertion in case.deterministic_assertions
    }

    assert assertion_kinds <= set(DETERMINISTIC_ASSERTION_HANDLERS)
    assert set(DETERMINISTIC_ASSERTION_HANDLERS) == set(ASSERTION_REGISTRY)


def test_nested_timestamps_and_run_ids_do_not_change_canonical_argument_grade() -> None:
    case = _dataset().by_id("ARG-02")
    calls = deepcopy(case.expected_tool_calls)
    calls[0]["arguments"]["run_id"] = "nested-random-run"
    calls[0]["arguments"]["timestamp"] = "nested-random-time"
    observation = _observation(
        case,
        tool_calls=calls,
        suppressed_call_ids=["arg-02-search"],
    )

    result = grade_case(case, observation)

    assert result.status == "PASS"
