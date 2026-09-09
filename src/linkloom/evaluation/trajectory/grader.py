"""Deterministic grading for normalized synthetic trajectory observations."""

from __future__ import annotations

from typing import Any, Callable

from .capabilities import assess_case_capabilities
from .fixture import validate_evidence_reference
from .models import (
    ASSERTION_REGISTRY,
    RESULT_SCHEMA_VERSION,
    CaseResult,
    TrajectoryCase,
    TrajectoryObservation,
    canonicalize,
)


AssertionHandler = Callable[[TrajectoryObservation, Any], Any]
_JUDGE_NOT_EVALUATED = {"status": "NOT_EVALUATED", "provider": None}


def _shape_like_expected(expected: Any, values: list[Any]) -> Any:
    if isinstance(expected, list):
        return values
    if len(values) == 1:
        return values[0]
    return values


def _route(observation: TrajectoryObservation, expected: Any) -> Any:
    routes = [event["route"] for event in observation.trajectory if "route" in event]
    return _shape_like_expected(expected, routes)


def _observation_present(
    observation: TrajectoryObservation, expected: Any
) -> bool:
    return True


def _case_id(observation: TrajectoryObservation, expected: Any) -> str:
    return observation.case_id


def _tool_ids(observation: TrajectoryObservation, expected: Any) -> Any:
    values = [call.get("tool_id") for call in observation.tool_calls]
    return _shape_like_expected(expected, values)


def _canonical_arguments(observation: TrajectoryObservation, expected: Any) -> Any:
    values = [canonicalize(call.get("arguments", {})) for call in observation.tool_calls]
    return _shape_like_expected(expected, values)


def _tool_sequence(observation: TrajectoryObservation, expected: Any) -> Any:
    return _tool_ids(observation, expected)


def _executor_suppressed(observation: TrajectoryObservation, expected: Any) -> bool:
    execution = observation.execution
    if execution["suppressed_call_ids"]:
        return True
    return len(execution["executor_invocations"]) < len(observation.tool_calls)


def _executor_count(observation: TrajectoryObservation, expected: Any) -> int:
    return len(observation.execution["executor_invocations"])


def _budget_used(observation: TrajectoryObservation, expected: Any) -> int:
    return observation.execution["budget"]["used"]


def _tool_result_status(observation: TrajectoryObservation, expected: Any) -> Any:
    values = [result.get("status") for result in observation.tool_results]
    return _shape_like_expected(expected, values)


def _tool_result_error_code(observation: TrajectoryObservation, expected: Any) -> Any:
    values = []
    for result in observation.tool_results:
        error = result.get("error")
        values.append(error.get("code") if isinstance(error, dict) else None)
    return _shape_like_expected(expected, values)


def _business_status(observation: TrajectoryObservation, expected: Any) -> Any:
    values = [result.get("business_status") for result in observation.tool_results]
    return _shape_like_expected(expected, values)


def _termination_status(observation: TrajectoryObservation, expected: Any) -> Any:
    return observation.termination.get("status")


def _ledger_transition(observation: TrajectoryObservation, expected: Any) -> Any:
    histories = observation.execution["ledger"]["history"]
    state_sequences: list[Any] = []
    for history in histories:
        if not isinstance(history, dict):
            state_sequences.append(None)
            continue
        states = history.get("states", [])
        if isinstance(states, list) and len(states) == 1 and not isinstance(expected, list):
            state_sequences.append(states[0])
        else:
            state_sequences.append(states)
    if len(state_sequences) == 1:
        return state_sequences[0]
    return state_sequences


def _repeated_call_detected(observation: TrajectoryObservation, expected: Any) -> Any:
    return observation.execution["signals"].get("repeated_call_detected", False)


def _loop_condition(observation: TrajectoryObservation, expected: Any) -> Any:
    return observation.execution["signals"].get("loop_condition")


def _premature_termination_blocked(
    observation: TrajectoryObservation, expected: Any
) -> Any:
    return observation.execution["signals"].get(
        "premature_termination_blocked", False
    )


def _pending_recommendation(observation: TrajectoryObservation, expected: Any) -> Any:
    decisions = observation.execution["recovery_decisions"]
    if not decisions:
        return None
    decision = decisions[-1]
    if not isinstance(decision, dict):
        return None
    return decision.get("recommendation", decision.get("decision"))


def _evidence_refs_valid(observation: TrajectoryObservation, expected: Any) -> bool:
    refs = observation.execution["evidence_refs"]
    if not refs:
        return False
    try:
        for ref in refs:
            validate_evidence_reference(ref)
    except ValueError:
        return False
    return True


def _denied_resource_access(observation: TrajectoryObservation, expected: Any) -> bool:
    return bool(observation.execution["denied_accesses"])


def _safe_error_exposure(observation: TrajectoryObservation, expected: Any) -> bool:
    errors = [
        result.get("error")
        for result in observation.tool_results
        if isinstance(result.get("error"), dict)
    ]
    return bool(errors) and all(error.get("safe_to_expose") is True for error in errors)


def _stale_memory_ignored(observation: TrajectoryObservation, expected: Any) -> Any:
    return observation.execution["signals"].get("stale_memory_ignored", False)


def _memory_conflict_detected(observation: TrajectoryObservation, expected: Any) -> Any:
    return observation.execution["signals"].get("memory_conflict_detected", False)


def _attribution(observation: TrajectoryObservation, expected: Any) -> Any:
    return observation.attribution


DETERMINISTIC_ASSERTION_HANDLERS: dict[str, AssertionHandler] = {
    "observation_present": _observation_present,
    "case_id": _case_id,
    "route": _route,
    "tool_ids": _tool_ids,
    "canonical_arguments": _canonical_arguments,
    "tool_sequence": _tool_sequence,
    "executor_suppressed": _executor_suppressed,
    "executor_count": _executor_count,
    "budget_used": _budget_used,
    "tool_result_status": _tool_result_status,
    "tool_result_error_code": _tool_result_error_code,
    "business_status": _business_status,
    "termination_status": _termination_status,
    "ledger_transition": _ledger_transition,
    "repeated_call_detected": _repeated_call_detected,
    "loop_condition": _loop_condition,
    "premature_termination_blocked": _premature_termination_blocked,
    "pending_recommendation": _pending_recommendation,
    "evidence_refs_valid": _evidence_refs_valid,
    "denied_resource_access": _denied_resource_access,
    "safe_error_exposure": _safe_error_exposure,
    "stale_memory_ignored": _stale_memory_ignored,
    "memory_conflict_detected": _memory_conflict_detected,
    "attribution": _attribution,
}

if set(DETERMINISTIC_ASSERTION_HANDLERS) != set(ASSERTION_REGISTRY):
    raise RuntimeError("deterministic assertion handlers drifted from schema registry")


def _result(
    case: TrajectoryCase,
    *,
    status: str,
    assertions: list[dict[str, Any]],
    failed_assertion: str | None,
    expected: Any,
    actual: Any,
    primary_failure_attribution: str | None,
    missing_capability: str | None,
) -> CaseResult:
    return CaseResult(
        schema_version=RESULT_SCHEMA_VERSION,
        case_id=case.case_id,
        category=case.category,
        status=status,
        assertions=assertions,
        failed_assertion=failed_assertion,
        expected=expected,
        actual=actual,
        primary_failure_attribution=primary_failure_attribution,
        missing_capability=missing_capability,
        judge=dict(_JUDGE_NOT_EVALUATED),
    )


def grade_case(
    case: TrajectoryCase,
    observation: TrajectoryObservation | None,
) -> CaseResult:
    """Grade one case without executing a tool, provider, model, or judge."""
    if not isinstance(case, TrajectoryCase):
        raise TypeError("case must be a TrajectoryCase")
    support = assess_case_capabilities(case)
    if not support.executable:
        return _result(
            case,
            status="NOT_IMPLEMENTED",
            assertions=[],
            failed_assertion=None,
            expected=None,
            actual=None,
            primary_failure_attribution="capability_gap",
            missing_capability=",".join(support.missing_capabilities),
        )

    if not isinstance(observation, TrajectoryObservation):
        return _result(
            case,
            status="FAIL",
            assertions=[
                {
                    "assertion": "observation_present",
                    "passed": False,
                    "expected": True,
                    "actual": False,
                }
            ],
            failed_assertion="observation_present",
            expected=True,
            actual=False,
            primary_failure_attribution="evaluation_input",
            missing_capability=None,
        )
    if observation.case_id != case.case_id:
        return _result(
            case,
            status="FAIL",
            assertions=[
                {
                    "assertion": "case_id",
                    "passed": False,
                    "expected": case.case_id,
                    "actual": observation.case_id,
                }
            ],
            failed_assertion="case_id",
            expected=case.case_id,
            actual=observation.case_id,
            primary_failure_attribution="evaluation_input",
            missing_capability=None,
        )

    assertion_results: list[dict[str, Any]] = []
    for assertion in case.deterministic_assertions:
        name = assertion["assertion"]
        handler = DETERMINISTIC_ASSERTION_HANDLERS.get(name)
        if handler is None:
            actual: Any = {"grader_error": "unregistered_assertion"}
        else:
            actual = handler(observation, assertion["expected"])
        expected = canonicalize(assertion["expected"])
        canonical_actual = canonicalize(actual)
        assertion_results.append(
            {
                "assertion": name,
                "passed": canonical_actual == expected,
                "expected": expected,
                "actual": canonical_actual,
            }
        )

    failed = next(
        (assertion for assertion in assertion_results if not assertion["passed"]),
        None,
    )
    if failed is None:
        return _result(
            case,
            status="PASS",
            assertions=assertion_results,
            failed_assertion=None,
            expected=None,
            actual=None,
            primary_failure_attribution=None,
            missing_capability=None,
        )
    return _result(
        case,
        status="FAIL",
        assertions=assertion_results,
        failed_assertion=failed["assertion"],
        expected=failed["expected"],
        actual=failed["actual"],
        primary_failure_attribution=case.expected_attribution["primary"],
        missing_capability=None,
    )


__all__ = ["DETERMINISTIC_ASSERTION_HANDLERS", "grade_case"]
