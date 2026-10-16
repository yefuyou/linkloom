"""Acceptance tests for the offline qualified runtime and Core Gate parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluation_core_gate import parse_junit_result
from scripts.evaluation_qualification import public_projection
from scripts.qualified_runtime import execute_public


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "evaluation_qualification" / "core_gate.json"


def _cases() -> dict[str, dict]:
    return {case["id"]: case for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}


def _public(case_id: str) -> dict:
    case = _cases()[case_id]
    case["gold"]["private_test_marker"] = "__gold_sentinel__"
    projected = public_projection(case)
    assert "__gold_sentinel__" not in json.dumps(projected)
    return projected


@pytest.mark.parametrize(
    ("case_id", "expected_outcome"),
    [("CORE-NORMAL", "complete"), ("CORE-PARTIAL", "partial"), ("CORE-CONFLICT", "partial")],
)
def test_qualified_runtime_observes_source_outcome(case_id: str, expected_outcome: str, tmp_path: Path) -> None:
    result = execute_public(_public(case_id), tmp_path / case_id)

    assert result["outcome"] == expected_outcome
    assert result["bindings_verified"] is True
    assert result["citation_valid"] is True
    assert result["model_gold_access"] is False
    assert result["source_identity"]["index_sha256"]
    assert result["calls"]
    if case_id == "CORE-CONFLICT":
        assert result["reason"] == "conflicting_evidence"
        assert result["final"]["decision"]["value"] is None
        assert len(result["final"]["uncertainty"]["evidence_refs"]) >= 2


def test_qualified_runtime_rejects_invalid_tool_params_from_observed_runtime(tmp_path: Path) -> None:
    result = execute_public(_public("CORE-INVALID-TOOL"), tmp_path / "invalid-tool")

    assert result["outcome"] == "refused"
    assert result["run_status"] == "failed"
    assert result["error"]["code"] == "TOOL_INVALID_ARGUMENTS"
    assert result["error"]["details"]["path"] == "$.limit"
    assert result["executor_search_calls"] == result["executor_read_calls"] == 0
    assert result["provider_requests"] == 2  # one rejected call, then bounded recovery observes its error


def test_final_correction_is_a_new_counted_response(tmp_path: Path) -> None:
    result = execute_public(_public("CORE-FINAL-CORRECTION"), tmp_path / "final-correction")

    assert result["outcome"] == "complete"
    assert result["final_correction"]["invalid_response_count"] == 1
    assert result["final_correction"]["correction_response_count"] == 1
    assert result["model_turns"] >= 4
    assert result["model_gold_access"] is False


def test_cold_resume_reuses_durable_response_and_counts_new_provider_turns(tmp_path: Path) -> None:
    result = execute_public(_public("CORE-COLD-RESUME"), tmp_path / "cold-resume")

    assert result["outcome"] == "complete"
    assert result["cold_resume"]["crashed_after_durable_response"] is True
    assert result["cold_resume"]["new_provider_requests"] >= 1
    assert result["cold_resume"]["response_origin"] == "provider"
    assert result["cold_resume"]["duplicate_executor_calls"] == 0


@pytest.mark.parametrize(
    "case_id",
    ["CORE-PROVIDER-UNCERTAIN", "CORE-WRONG-WORKSPACE", "CORE-SOURCE-CHANGE", "CORE-BUDGET"],
)
def test_fault_boundaries_fail_closed(case_id: str, tmp_path: Path) -> None:
    result = execute_public(_public(case_id), tmp_path / case_id)

    assert result["run_status"] == "failed"
    assert result["outcome"] in {"refused", "partial"}
    assert result["model_gold_access"] is False
    assert result["final"] is None
    if case_id == "CORE-PROVIDER-UNCERTAIN":
        assert result["error"]["details"]["outcome"] == "unknown_provider_outcome"
        assert result["provider_requests"] == 1
        assert result["executor_search_calls"] == result["executor_read_calls"] == 0
    elif case_id == "CORE-WRONG-WORKSPACE":
        assert result["error"]["code"] == "TOOL_INVALID_ARGUMENTS"
        assert "source_context" in result["error"]["details"]["path"]
        assert result["executor_search_calls"] == result["executor_read_calls"] == 0
    elif case_id == "CORE-SOURCE-CHANGE":
        mutation = result["source_mutation"]
        assert mutation["path"] in result["sources"]
        assert mutation["before_sha256"] != mutation["after_sha256"]
        assert result["error"]["code"] == "TOOL_EXECUTION_FAILED"
        assert result["error"]["details"]["failure_diagnostic"]["exception_type"] == "LoaderError"
    elif case_id == "CORE-BUDGET":
        assert result["provider_requests"] == 1
        assert "BUDGET" in result["error"]["code"] or result["error"]["code"] == "MODEL_MAX_STEPS_EXCEEDED"
        assert result["executor_search_calls"] == 1


def test_source_context_and_dependency_bindings_are_observed_not_prefilled(tmp_path: Path) -> None:
    result = execute_public(_public("CORE-NORMAL"), tmp_path / "bindings")

    searches = [call for call in result["calls"] if call["tool"] == "search_notes"]
    assert len(searches) == 2
    assert searches[0]["dependencies"] == {}
    assert searches[1]["dependencies"]
    assert searches[1]["dependencies"] == result["observed_bindings"]["leader"]
    assert result["executor_search_calls"] == len(searches)


def test_unverified_dependency_never_reaches_next_executor(tmp_path):
    public = _public("CORE-NORMAL")
    public["fault"] = "unverified_dependency"
    result = execute_public(public, tmp_path / "dependency")
    assert result["run_status"] == "failed"
    assert result["error"]["code"] == "TOOL_INVALID_ARGUMENTS"
    assert result["executor_search_calls"] == 1
    assert result["executor_read_calls"] >= 1
    assert result["final"] is None
    actions = [r.get("normalized_action") or {} for r in result["model_executions"]]
    assert "Forged Owner" in json.dumps(actions)


def test_d06_f07_goal_mismatch_regressions_are_represented_by_new_runner(tmp_path: Path) -> None:
    for case_id in ("CORE-D06-GOAL-MISMATCH", "CORE-F07-GOAL-MISMATCH"):
        case = _cases()[case_id]
        projected = public_projection(case)
        with pytest.raises(ValueError, match="goal/query"):
            execute_public(projected, tmp_path / case_id)


def test_core_gate_junit_parser_does_not_promote_skipped_or_error_to_pass(tmp_path: Path) -> None:
    skipped = tmp_path / "skipped.xml"
    skipped.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="x" name="y"><skipped /></testcase></testsuite>',
        encoding="utf-8",
    )
    errored = tmp_path / "error.xml"
    errored.write_text(
        '<testsuite tests="1" failures="0" errors="1" skipped="0">'
        '<testcase classname="x" name="y"><error /></testcase></testsuite>',
        encoding="utf-8",
    )

    assert parse_junit_result(skipped)["status"] == "failed"
    assert parse_junit_result(errored)["status"] == "failed"
