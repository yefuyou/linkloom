from __future__ import annotations

from pathlib import Path

from linkloom.evaluation.trajectory.capabilities import assess_case_capabilities
from linkloom.evaluation.trajectory.dataset import TrajectoryDataset
from linkloom.evaluation.trajectory.grader import grade_case
from linkloom.evaluation.trajectory.metrics import (
    METRIC_REGISTRY,
    REQUIRED_METRIC_NAMES,
    compute_trajectory_metrics,
)
from linkloom.evaluation.trajectory.models import RESULT_SCHEMA_VERSION, CaseResult


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"


def _passing_results(dataset: TrajectoryDataset) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in dataset.cases:
        support = assess_case_capabilities(case)
        if not support.executable:
            results.append(grade_case(case, None))
            continue
        results.append(
            CaseResult(
                schema_version=RESULT_SCHEMA_VERSION,
                case_id=case.case_id,
                category=case.category,
                status="PASS",
                assertions=[
                    {
                        "assertion": assertion["assertion"],
                        "passed": True,
                        "expected": assertion["expected"],
                        "actual": assertion["expected"],
                    }
                    for assertion in case.deterministic_assertions
                ],
                failed_assertion=None,
                expected=None,
                actual=None,
                primary_failure_attribution=None,
                missing_capability=None,
                judge={"status": "NOT_EVALUATED", "provider": None},
            )
        )
    return results


def _fail_assertion(
    results: list[CaseResult], case_id: str, assertion_name: str
) -> list[CaseResult]:
    changed = list(results)
    index = next(
        result_index
        for result_index, result in enumerate(changed)
        if result.case_id == case_id
    )
    original = changed[index]
    failed = next(
        assertion
        for assertion in original.assertions
        if assertion["assertion"] == assertion_name
    )
    changed[index] = CaseResult(
        schema_version=original.schema_version,
        case_id=original.case_id,
        category=original.category,
        status="FAIL",
        assertions=[
            {
                **assertion,
                "passed": False
                if assertion["assertion"] == assertion_name
                else assertion["passed"],
                "actual": {"corrupted": assertion_name}
                if assertion["assertion"] == assertion_name
                else assertion["actual"],
            }
            for assertion in original.assertions
        ],
        failed_assertion=assertion_name,
        expected=failed["expected"],
        actual={"corrupted": assertion_name},
        primary_failure_attribution="caller_argument_construction",
        missing_capability=None,
        judge=original.judge,
    )
    return changed


def test_metrics_report_nine_independent_evaluated_values_without_aggregate() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)

    payload = compute_trajectory_metrics(dataset.cases, _passing_results(dataset))

    assert set(payload["metrics"]) == set(REQUIRED_METRIC_NAMES)
    assert "score" not in payload
    assert "aggregate" not in payload
    for metric in payload["metrics"].values():
        assert metric["status"] == "EVALUATED"
        assert metric["denominator"] > 0
        assert metric["numerator"] == metric["denominator"]
        assert metric["value"] == 1.0
        assert metric["case_ids"] == sorted(metric["case_ids"])

    assert set(METRIC_REGISTRY) == set(REQUIRED_METRIC_NAMES)
    assert all(definition.case_ids for definition in METRIC_REGISTRY.values())
    assert all(
        definition.assertion_names for definition in METRIC_REGISTRY.values()
    )


def test_mapped_argument_failure_does_not_cross_contaminate_other_metrics() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)
    baseline = compute_trajectory_metrics(
        dataset.cases, _passing_results(dataset)
    )["metrics"]
    corrupted = compute_trajectory_metrics(
        dataset.cases,
        _fail_assertion(
            _passing_results(dataset), "ARG-02", "canonical_arguments"
        ),
    )["metrics"]

    assert corrupted["argument_contract_accuracy"] == {
        **baseline["argument_contract_accuracy"],
        "numerator": 2,
        "value": 0.6667,
    }
    assert corrupted["executor_suppression_rate"] == baseline[
        "executor_suppression_rate"
    ]
    assert corrupted["tool_result_contract_accuracy"] == baseline[
        "tool_result_contract_accuracy"
    ]
    assert {
        name: metric
        for name, metric in corrupted.items()
        if name != "argument_contract_accuracy"
    } == {
        name: metric
        for name, metric in baseline.items()
        if name != "argument_contract_accuracy"
    }


def test_non_mapped_failure_leaves_target_metric_unchanged() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)
    baseline = compute_trajectory_metrics(
        dataset.cases, _passing_results(dataset)
    )["metrics"]
    corrupted = compute_trajectory_metrics(
        dataset.cases,
        _fail_assertion(
            _passing_results(dataset), "ARG-02", "executor_suppressed"
        ),
    )["metrics"]

    assert corrupted["argument_contract_accuracy"] == baseline[
        "argument_contract_accuracy"
    ]
    assert corrupted["executor_suppression_rate"] == {
        **baseline["executor_suppression_rate"],
        "numerator": baseline["executor_suppression_rate"]["numerator"] - 1,
        "value": round(
            (baseline["executor_suppression_rate"]["numerator"] - 1)
            / baseline["executor_suppression_rate"]["denominator"],
            4,
        ),
    }


def test_metric_uses_assertions_even_when_case_status_is_fail() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)
    results = _fail_assertion(
        _passing_results(dataset), "ARG-02", "executor_suppressed"
    )

    metrics = compute_trajectory_metrics(dataset.cases, results)["metrics"]

    assert metrics["argument_contract_accuracy"]["value"] == 1.0
    assert metrics["tool_result_contract_accuracy"]["value"] == 1.0


def test_future_metrics_name_capability_and_reason_without_scoring_cases() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)

    future = compute_trajectory_metrics(dataset.cases, _passing_results(dataset))[
        "future_metrics"
    ]

    assert set(future) == {
        "routing",
        "model_tool_selection",
        "semantic_repeat_guard",
        "semantic_loop_guard",
        "premature_final_guard",
        "full_resume",
        "memory_staleness",
        "memory_conflict",
        "llm_judge",
    }
    assert future["llm_judge"]["status"] == "NOT_EVALUATED"
    assert future["llm_judge"]["capability_id"] == "llm.judge"
    assert all(item["reason"].strip() for item in future.values())
    assert all(
        item["status"] in {"UNSUPPORTED", "NOT_EVALUATED"}
        for item in future.values()
    )
