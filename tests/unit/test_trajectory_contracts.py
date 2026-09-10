from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from linkloom.evaluation.trajectory.models import (
    CASE_SCHEMA_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    CaseResult,
    TrajectoryCase,
    TrajectoryObservation,
    canonicalize,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"


def _case_data() -> dict:
    for raw_line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        data = json.loads(raw_line)
        if data["case_id"] == "ARG-01":
            data["case_id"] = "ARG-TEST"
            return data
    raise AssertionError("ARG-01 fixture case is missing")


def _observation_data() -> dict:
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "case_id": "ARG-TEST",
        "trajectory": [
            {
                "event": "tool_requested",
                "call_id": "arg-test-1",
                "tool_id": "search_notes",
            },
            {
                "event": "tool_result",
                "call_id": "arg-test-1",
                "status": "error",
                "error_code": "TOOL_INVALID_ARGUMENTS",
            },
        ],
        "tool_calls": [
            {
                "call_id": "arg-test-1",
                "tool_id": "search_notes",
                "arguments": {"source_context": {}, "limit": 5},
                "sequence": 0,
            }
        ],
        "tool_results": [
            {
                "call_id": "arg-test-1",
                "tool_id": "search_notes",
                "status": "error",
                "value": None,
                "business_status": None,
                "error": {
                    "code": "TOOL_INVALID_ARGUMENTS",
                    "category": "input",
                    "message": "Tool arguments are invalid.",
                    "retryable": False,
                    "safe_to_expose": True,
                },
            }
        ],
        "termination": {"status": "safe_error", "reason": "malformed_arguments"},
        "attribution": {
            "primary": "caller_argument_construction",
            "stage": "argument_validation",
        },
        "execution": {
            "executor_invocations": [],
            "suppressed_call_ids": ["arg-test-1"],
            "budget": {"used": 0, "remaining": 1},
            "ledger": {"history": []},
            "evidence_refs": [],
            "denied_accesses": [],
            "recovery_decisions": [],
            "signals": {},
        },
        "metadata": {
            "run_id": "ignored-random-id",
            "timestamp": "2026-08-24T00:00:00Z",
        },
    }


def _pass_result_data() -> dict:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "case_id": "ARG-TEST",
        "category": "malformed_arguments",
        "status": "PASS",
        "assertions": [
            {
                "assertion": "executor_suppressed",
                "passed": True,
                "expected": True,
                "actual": True,
            }
        ],
        "failed_assertion": None,
        "expected": None,
        "actual": None,
        "primary_failure_attribution": None,
        "missing_capability": None,
        "judge": {"status": "NOT_EVALUATED", "provider": None},
    }


def _fail_result_data() -> dict:
    data = _pass_result_data()
    data.update(
        status="FAIL",
        failed_assertion="executor_suppressed",
        expected=True,
        actual=False,
        primary_failure_attribution="caller_argument_construction",
    )
    data["assertions"][0].update(passed=False, actual=False)
    return data


def test_trajectory_case_round_trips_versioned_json_safe_data() -> None:
    case = TrajectoryCase.from_dict(_case_data())

    assert case.schema_version == CASE_SCHEMA_VERSION
    assert case.case_id == "ARG-TEST"
    assert case.to_dict() == _case_data()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.pop("expected_termination"), "missing required fields"),
        (lambda data: data.update({"surprise": True}), "unknown fields"),
        (lambda data: data.update(category="unknown"), "category"),
        (lambda data: data.update(schema_version="trajectory-case/v0"), "schema_version"),
        (
            lambda data: data["fault_injection"].update(non_finite=math.nan),
            "JSON-safe",
        ),
    ],
)
def test_trajectory_case_rejects_invalid_contract(mutation, message: str) -> None:
    data = _case_data()
    mutation(data)

    with pytest.raises(ValueError, match=message):
        TrajectoryCase.from_dict(data)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../relation_gold.yaml",
        "n/../../private.md",
        "/tmp/private.md",
        r"C:\\Users\\person\\vault\\note.md",
        "tests/eval/relation_gold.yaml",
        "tests/fixtures/relation_vault/note.md",
        "n/not-exposed.md",
    ],
)
def test_trajectory_case_rejects_unsafe_or_unexposed_fixture_paths(
    unsafe_path: str,
) -> None:
    data = _case_data()
    data["synthetic_input"]["fixture_paths"] = [unsafe_path]

    with pytest.raises(ValueError, match="fixture"):
        TrajectoryCase.from_dict(data)


def test_canonicalize_ignores_nondeterministic_metadata_recursively() -> None:
    left = {
        "case_id": "NF-01",
        "run_id": "random-a",
        "timestamp": "2026-08-23T00:00:00Z",
        "tool_calls": [
            {
                "call_id": "nf-01-search",
                "tool_id": "search_notes",
                "arguments": {"query": "ghost"},
                "metadata": {
                    "created_at": "yesterday",
                    "timestamps": [1, 2],
                },
            }
        ],
    }
    right = {
        "case_id": "NF-01",
        "run_id": "random-b",
        "timestamp": "2026-08-24T00:00:00Z",
        "tool_calls": [
            {
                "call_id": "nf-01-search",
                "tool_id": "search_notes",
                "arguments": {"query": "ghost"},
                "metadata": {
                    "created_at": "today",
                    "timestamps": [99],
                },
            }
        ],
    }

    assert canonicalize(left) == canonicalize(right)
    canonical = canonicalize(left)
    assert canonical["case_id"] == "NF-01"
    assert canonical["tool_calls"][0]["call_id"] == "nf-01-search"
    assert canonical["tool_calls"][0]["tool_id"] == "search_notes"


def test_observation_and_case_result_are_normalized_json_contracts() -> None:
    observation = TrajectoryObservation.from_dict(_observation_data())
    result = CaseResult.from_dict(_pass_result_data())

    assert observation.case_id == result.case_id
    assert result.status == "PASS"
    assert result.judge == {"status": "NOT_EVALUATED", "provider": None}


def test_observation_rejects_undocumented_execution_fields() -> None:
    data = _observation_data()
    data["execution"]["arbitrary_runtime_blob"] = {}

    with pytest.raises(ValueError, match="unknown fields"):
        TrajectoryObservation.from_dict(data)


def test_observation_metadata_requires_non_empty_run_id() -> None:
    missing = _observation_data()
    missing["metadata"].pop("run_id")
    with pytest.raises(ValueError, match="metadata.*run_id"):
        TrajectoryObservation.from_dict(missing)

    empty = _observation_data()
    empty["metadata"]["run_id"] = ""
    with pytest.raises(ValueError, match="metadata.run_id"):
        TrajectoryObservation.from_dict(empty)


def test_observation_metadata_allows_only_explicit_optional_nondeterministic_fields() -> None:
    data = _observation_data()
    data["metadata"] = {"run_id": "required-run"}

    observation = TrajectoryObservation.from_dict(data)

    assert observation.metadata == {"run_id": "required-run"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["policy_budget"]["allowed_tools"].__setitem__(
            0, "unknown_tool"
        ),
        lambda data: data["policy_budget"]["denied_tools"].__setitem__(
            0, "unknown_tool"
        ),
        lambda data: data["expected_tool_calls"][0].update(
            tool_id="unknown_tool"
        ),
        lambda data: data["expected_tool_results"][0].update(
            tool_id="unknown_tool"
        ),
        lambda data: data["expected_trajectory"][0].update(
            tool_id="unknown_tool"
        ),
    ],
)
def test_case_rejects_unknown_tool_ids_at_every_applicable_boundary(mutation) -> None:
    data = _case_data()
    mutation(data)

    with pytest.raises(ValueError, match="tool"):
        TrajectoryCase.from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["tool_calls"][0].update(tool_id="unknown_tool"),
        lambda data: data["tool_results"][0].update(tool_id="unknown_tool"),
        lambda data: data["trajectory"][0].update(tool_id="unknown_tool"),
        lambda data: data["execution"]["executor_invocations"].append(
            {"call_id": "unknown-invocation", "tool_id": "unknown_tool"}
        ),
        lambda data: data["execution"]["recovery_decisions"].append(
            {
                "call_id": "unknown-recovery",
                "tool_id": "unknown_tool",
                "decision": "safe_to_retry",
                "reason_code": "SYNTHETIC_READ_ONLY",
                "message": "Synthetic recovery decision.",
            }
        ),
    ],
)
def test_observation_rejects_unknown_tool_ids_at_every_applicable_boundary(
    mutation,
) -> None:
    data = _observation_data()
    mutation(data)

    with pytest.raises(ValueError, match="tool"):
        TrajectoryObservation.from_dict(data)


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("ok", "TOOL_INVALID_ARGUMENTS"),
        ("error", None),
    ],
)
def test_tool_result_event_rejects_invalid_status_error_code_combinations(
    status: str,
    error_code: str | None,
) -> None:
    data = _observation_data()
    data["trajectory"][1] = {
        "event": "tool_result",
        "call_id": "arg-test-1",
        "status": status,
        "error_code": error_code,
    }

    with pytest.raises(ValueError, match="tool_result.*error_code"):
        TrajectoryObservation.from_dict(data)


def test_tool_result_event_accepts_valid_status_error_code_combinations() -> None:
    error_data = _observation_data()
    TrajectoryObservation.from_dict(error_data)

    ok_data = _observation_data()
    ok_data["trajectory"][1] = {
        "event": "tool_result",
        "call_id": "arg-test-1",
        "status": "ok",
        "error_code": None,
    }

    TrajectoryObservation.from_dict(ok_data)


def test_future_result_requires_a_missing_capability() -> None:
    with pytest.raises(ValueError, match="missing_capability"):
        CaseResult.from_dict(
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "case_id": "RTE-01",
                "category": "routing",
                "status": "NOT_IMPLEMENTED",
                "assertions": [],
                "failed_assertion": None,
                "expected": None,
                "actual": None,
                "primary_failure_attribution": "capability_gap",
                "missing_capability": None,
                "judge": {"status": "NOT_EVALUATED", "provider": None},
            }
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["synthetic_input"].update({"fixture_path": []}),
            "synthetic_input.*unknown fields",
        ),
        (
            lambda data: data["fault_injection"].update({"tool_id": "search_notes"}),
            "fault_injection.*unknown fields",
        ),
        (
            lambda data: data.update(
                fault_injection={"kind": "missing_required_argument"}
            ),
            "fault_injection.*missing required fields",
        ),
        (
            lambda data: data.update(fault_injection={"kind": "unknown_fault"}),
            "fault_injection.kind",
        ),
        (
            lambda data: data["expected_tool_calls"][0].pop("sequence"),
            "ToolCall.*missing required fields",
        ),
        (
            lambda data: data["expected_tool_calls"][0].update(sequence=True),
            "sequence",
        ),
        (
            lambda data: data["expected_tool_results"][0].update(error=None),
            "status=error.*error",
        ),
        (
            lambda data: data["expected_tool_results"][0]["error"].update(
                {"debug": "leak"}
            ),
            "ToolError.*unknown fields",
        ),
        (
            lambda data: data.update(
                expected_termination={"status": "unknown", "reason": "none"}
            ),
            "termination.status",
        ),
        (
            lambda data: data["expected_attribution"].update(stage="authorization"),
            "attribution pair",
        ),
        (
            lambda data: data["deterministic_assertions"][0].update(
                assertion="unknown_assertion"
            ),
            "assertion",
        ),
        (
            lambda data: data["expected_trajectory"][0].update(event="unknown_event"),
            "event",
        ),
        (
            lambda data: data["policy_budget"]["denied_tools"].append(
                "search_notes"
            ),
            "allowed_tools.*denied_tools",
        ),
        (
            lambda data: data["expected_trajectory"][0].update(
                event="route_selected", route="not_registered"
            ),
            "route",
        ),
        (
            lambda data: data["expected_tool_results"][0]["error"].update(
                category="unknown"
            ),
            "error.category",
        ),
        (
            lambda data: data["expected_tool_results"][0].update(value={}),
            "status=error.*null value",
        ),
        (
            lambda data: data["expected_termination"].update(extra=True),
            "expected_termination.*unknown fields",
        ),
        (
            lambda data: data["expected_attribution"].update(extra=True),
            "expected_attribution.*unknown fields",
        ),
        (
            lambda data: data["deterministic_assertions"][0].update(
                expected="true"
            ),
            "expected.*boolean",
        ),
    ],
)
def test_case_rejects_reviewer_nested_schema_probes(mutation, message: str) -> None:
    data = _case_data()
    mutation(data)

    with pytest.raises(ValueError, match=message):
        TrajectoryCase.from_dict(data)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["trajectory"][0].update({"unexpected": True}),
            "trajectory.*unknown fields",
        ),
        (
            lambda data: data["tool_calls"][0].update({"run_id": "not-metadata"}),
            "ToolCall.*unknown fields",
        ),
        (
            lambda data: data["tool_results"][0].update(error=None),
            "status=error.*error",
        ),
        (
            lambda data: data.update(
                termination={"status": "unknown", "reason": "none"}
            ),
            "termination.status",
        ),
        (
            lambda data: data.update(
                attribution={"primary": "executor", "stage": "authorization"}
            ),
            "attribution pair",
        ),
        (
            lambda data: data["execution"]["executor_invocations"].append(
                {"call_id": "x", "tool_id": "search_notes", "extra": True}
            ),
            "executor_invocations.*unknown fields",
        ),
        (
            lambda data: data["execution"]["signals"].update({"mystery": True}),
            "signals.*unknown fields",
        ),
        (
            lambda data: data["metadata"].update({"vault_path": "private"}),
            "metadata.*unknown fields",
        ),
    ],
)
def test_observation_reuses_closed_nested_contracts(mutation, message: str) -> None:
    data = _observation_data()
    mutation(data)

    with pytest.raises(ValueError, match=message):
        TrajectoryObservation.from_dict(data)


@pytest.mark.parametrize(
    ("factory", "mutation", "message"),
    [
        (
            _pass_result_data,
            lambda data: data["assertions"][0].update(
                assertion="unknown_assertion"
            ),
            "assertion",
        ),
        (
            _pass_result_data,
            lambda data: data["assertions"][0].update(actual=False),
            "passed result must match",
        ),
        (
            _pass_result_data,
            lambda data: data["assertions"][0].update(passed=False),
            "PASS requires",
        ),
        (
            _fail_result_data,
            lambda data: data.update(failed_assertion="tool_result_status"),
            "failed_assertion",
        ),
        (
            _fail_result_data,
            lambda data: data.update(expected=False),
            "expected must match",
        ),
        (
            _fail_result_data,
            lambda data: data.update(primary_failure_attribution="invented_stage"),
            "primary_failure_attribution",
        ),
        (
            _pass_result_data,
            lambda data: data["judge"].update(criteria=[]),
            "judge.*unknown fields",
        ),
    ],
)
def test_case_result_rejects_closed_contract_violations(
    factory, mutation, message: str
) -> None:
    data = factory()
    mutation(data)

    with pytest.raises(ValueError, match=message):
        CaseResult.from_dict(data)
