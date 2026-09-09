"""Independent deterministic metrics for trajectory evaluation runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .capabilities import CAPABILITY_MATRIX
from .models import CaseResult, TrajectoryCase


REQUIRED_METRIC_NAMES = (
    "argument_contract_accuracy",
    "permission_block_rate",
    "budget_accounting_accuracy",
    "executor_suppression_rate",
    "tool_result_contract_accuracy",
    "not_found_semantic_accuracy",
    "partial_retrieval_recovery_rate",
    "pending_ambiguity_safety_rate",
    "safe_error_exposure_rate",
)


@dataclass(frozen=True)
class MetricDefinition:
    case_ids: frozenset[str]
    assertion_names: frozenset[str]

    def __post_init__(self) -> None:
        if not self.case_ids or not self.assertion_names:
            raise ValueError("metric definitions require cases and assertions")


METRIC_REGISTRY = {
    "argument_contract_accuracy": MetricDefinition(
        case_ids=frozenset({"ARG-01", "ARG-02", "ARG-03"}),
        assertion_names=frozenset(
            {
                "canonical_arguments",
                "denied_resource_access",
                "tool_result_error_code",
            }
        ),
    ),
    "permission_block_rate": MetricDefinition(
        case_ids=frozenset({"PERM-01", "PERM-02", "PERM-03"}),
        assertion_names=frozenset(
            {
                "denied_resource_access",
                "executor_suppressed",
                "tool_result_error_code",
            }
        ),
    ),
    "budget_accounting_accuracy": MetricDefinition(
        case_ids=frozenset({"BUD-01", "BUD-02"}),
        assertion_names=frozenset(
            {
                "budget_used",
                "executor_suppressed",
                "tool_result_error_code",
                "tool_result_status",
            }
        ),
    ),
    "executor_suppression_rate": MetricDefinition(
        case_ids=frozenset(
            {
                "ARG-01",
                "ARG-02",
                "ARG-03",
                "BUD-01",
                "CP-01",
                "CP-03",
                "CP-04",
                "EXEC-02",
                "PERM-01",
                "PERM-02",
                "PERM-03",
                "REP-02",
            }
        ),
        assertion_names=frozenset({"executor_suppressed"}),
    ),
    "tool_result_contract_accuracy": MetricDefinition(
        case_ids=frozenset(
            {
                "ARG-01",
                "ARG-02",
                "ARG-03",
                "BUD-01",
                "BUD-02",
                "CP-01",
                "EXEC-01",
                "EXEC-02",
                "EXEC-03",
                "NF-01",
                "NF-02",
                "OUT-01",
                "OUT-02",
                "OUT-03",
                "PART-01",
                "PART-02",
                "PART-03",
                "PERM-01",
                "PERM-02",
                "PERM-03",
                "REP-02",
            }
        ),
        assertion_names=frozenset(
            {"business_status", "tool_result_error_code", "tool_result_status"}
        ),
    ),
    "not_found_semantic_accuracy": MetricDefinition(
        case_ids=frozenset({"NF-01", "NF-02"}),
        assertion_names=frozenset(
            {"business_status", "termination_status", "tool_result_status"}
        ),
    ),
    "partial_retrieval_recovery_rate": MetricDefinition(
        case_ids=frozenset({"PART-01", "PART-02", "PART-03"}),
        assertion_names=frozenset({"evidence_refs_valid"}),
    ),
    "pending_ambiguity_safety_rate": MetricDefinition(
        case_ids=frozenset({"CP-01", "CP-03", "CP-04"}),
        assertion_names=frozenset(
            {
                "executor_suppressed",
                "ledger_transition",
                "pending_recommendation",
                "tool_result_status",
            }
        ),
    ),
    "safe_error_exposure_rate": MetricDefinition(
        case_ids=frozenset({"EXEC-01", "PART-02"}),
        assertion_names=frozenset({"safe_error_exposure"}),
    ),
}


_FUTURE_METRICS = {
    "routing": "routing.model_driven",
    "model_tool_selection": "tool_selection.model_driven",
    "semantic_repeat_guard": "guard.semantic_repeat",
    "semantic_loop_guard": "guard.semantic_loop",
    "premature_final_guard": "guard.premature_final",
    "full_resume": "checkpoint.full_resume",
    "memory_staleness": "memory.staleness",
    "memory_conflict": "memory.conflict",
    "llm_judge": "llm.judge",
}


def compute_trajectory_metrics(
    cases: Iterable[TrajectoryCase],
    results: Iterable[CaseResult],
) -> dict[str, Any]:
    """Compute separate metrics; intentionally do not create a single score."""

    case_by_id = {case.case_id: case for case in cases}
    result_by_id: dict[str, CaseResult] = {}
    for result in results:
        if result.case_id in result_by_id:
            raise ValueError(f"duplicate result for case_id: {result.case_id}")
        result_by_id[result.case_id] = result

    metrics: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_METRIC_NAMES:
        definition = METRIC_REGISTRY[name]
        case_ids = sorted(
            case_id
            for case_id in definition.case_ids
            if case_id in case_by_id
            and case_id in result_by_id
            and result_by_id[case_id].status != "NOT_IMPLEMENTED"
        )
        contributions: list[bool] = []
        for case_id in case_ids:
            mapped = [
                assertion
                for assertion in result_by_id[case_id].assertions
                if assertion["assertion"] in definition.assertion_names
            ]
            if not mapped:
                raise ValueError(
                    f"metric {name} has no contributing assertion for {case_id}"
                )
            contributions.append(all(assertion["passed"] for assertion in mapped))
        numerator = sum(contributions)
        denominator = len(case_ids)
        metrics[name] = {
            "status": "EVALUATED" if denominator else "NOT_EVALUATED",
            "numerator": numerator,
            "denominator": denominator,
            "value": round(numerator / denominator, 4) if denominator else None,
            "case_ids": case_ids,
        }

    future_metrics: dict[str, dict[str, str]] = {}
    for name, capability_id in _FUTURE_METRICS.items():
        definition = CAPABILITY_MATRIX[capability_id]
        future_metrics[name] = {
            "status": "NOT_EVALUATED" if name == "llm_judge" else "UNSUPPORTED",
            "capability_id": capability_id,
            "reason": definition.missing_reason or definition.boundary,
        }

    return {
        "schema_version": "trajectory-metrics/v1",
        "metrics": metrics,
        "future_metrics": future_metrics,
    }


__all__ = [
    "METRIC_REGISTRY",
    "REQUIRED_METRIC_NAMES",
    "MetricDefinition",
    "compute_trajectory_metrics",
]
