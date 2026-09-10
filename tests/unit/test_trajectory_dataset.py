from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import pytest

from linkloom.evaluation.trajectory.dataset import TrajectoryDataset, load_cases_jsonl
from linkloom.evaluation.trajectory.capabilities import partition_case_ids


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"

EXPECTED_CATEGORY_COUNTS = {
    "routing": 4,
    "tool_selection": 3,
    "malformed_arguments": 3,
    "permission_denied": 3,
    "budget_exhausted": 2,
    "tool_execution_failure": 3,
    "invalid_output": 3,
    "NOT_FOUND": 2,
    "partial_retrieval_failure": 3,
    "repeated_tool_call": 3,
    "loop": 2,
    "premature_termination": 2,
    "checkpoint_pending_ambiguity": 4,
    "stale_memory": 2,
    "conflicting_memory": 2,
}

EXECUTABLE_CASE_IDS = {
    *(f"ARG-{number:02}" for number in range(1, 4)),
    *(f"PERM-{number:02}" for number in range(1, 4)),
    *(f"BUD-{number:02}" for number in range(1, 3)),
    *(f"EXEC-{number:02}" for number in range(1, 4)),
    *(f"OUT-{number:02}" for number in range(1, 4)),
    *(f"NF-{number:02}" for number in range(1, 3)),
    *(f"PART-{number:02}" for number in range(1, 4)),
    "REP-02",
    "CP-01",
    "CP-03",
    "CP-04",
}

def test_dataset_loads_exact_41_unique_cases_and_15_categories() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)

    assert len(dataset.cases) == 41
    assert len({case.case_id for case in dataset.cases}) == 41
    assert Counter(case.category for case in dataset.cases) == EXPECTED_CATEGORY_COUNTS


def test_each_case_has_meaningful_expected_contract_data() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)

    for case in dataset.cases:
        assert case.synthetic_input["request"].strip()
        assert case.synthetic_input["fixture_paths"]
        assert case.expected_trajectory
        assert all(event.get("event") for event in case.expected_trajectory)
        assert case.expected_termination.get("status")
        assert case.expected_attribution.get("primary")
        assert case.deterministic_assertions
        assert all(item.get("assertion") for item in case.deterministic_assertions)
        assert case.optional_judge_rubric["status"] == "NOT_EVALUATED"
        assert case.optional_judge_rubric["provider"] is None
        assert case.optional_judge_rubric["criteria"]
        assert case.required_capabilities


def test_required_capabilities_encode_23_current_and_18_future_cases() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)
    actual_executable, actual_future = partition_case_ids(dataset.cases)

    assert actual_executable == EXECUTABLE_CASE_IDS
    assert len(actual_executable) == 23
    assert len(actual_future) == 18
    assert actual_executable.isdisjoint(actual_future)
    assert actual_executable | actual_future == {
        case.case_id for case in dataset.cases
    }


def test_every_executable_case_has_one_attribution_assertion_matching_expected() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)
    executable, _future = partition_case_ids(dataset.cases)

    for case in dataset.cases:
        assertions = [
            assertion
            for assertion in case.deterministic_assertions
            if assertion["assertion"] == "attribution"
        ]
        if case.case_id in executable:
            assert assertions == [
                {"assertion": "attribution", "expected": case.expected_attribution}
            ]
        else:
            assert assertions == []


def test_cases_match_current_tool_runtime_call_and_result_contracts() -> None:
    cases = {case.case_id: case for case in TrajectoryDataset.from_path(CASES_PATH).cases}

    for case in cases.values():
        for call in case.expected_tool_calls:
            arguments = call["arguments"]
            if call["tool_id"] == "search_notes":
                if case.case_id == "ARG-01":
                    assert set(arguments) == {"source_context", "limit"}
                else:
                    assert set(arguments) == {"query", "source_context", "limit"}
                assert arguments["source_context"] == {}
            if call["tool_id"] == "read_verified_note":
                assert "note_path" not in arguments
                assert "note_ref" in arguments

    exact_error_codes = {
        "ARG-01": ["TOOL_INVALID_ARGUMENTS"],
        "ARG-02": ["TOOL_INVALID_ARGUMENTS"],
        "ARG-03": ["TOOL_INVALID_ARGUMENTS"],
        "PERM-01": ["TOOL_PERMISSION_DENIED"],
        "PERM-02": ["TOOL_PERMISSION_DENIED"],
        "PERM-03": ["TOOL_PERMISSION_DENIED"],
        "BUD-01": ["TOOL_BUDGET_EXCEEDED"],
        "BUD-02": [None, "TOOL_BUDGET_EXCEEDED"],
        "EXEC-01": ["TOOL_EXECUTION_FAILED"],
        "EXEC-02": ["TOOL_PENDING_CHECKPOINT_FAILED"],
        "EXEC-03": ["TOOL_TERMINAL_CHECKPOINT_FAILED"],
        "OUT-01": ["TOOL_INVALID_OUTPUT"],
        "OUT-02": ["TOOL_INVALID_OUTPUT"],
        "OUT-03": ["TOOL_INVALID_OUTPUT"],
        "REP-02": [None, "TOOL_LEDGER_CONFLICT"],
    }
    for case_id, expected_codes in exact_error_codes.items():
        actual_codes = [
            None if result.get("error") is None else result["error"]["code"]
            for result in cases[case_id].expected_tool_results
        ]
        assert actual_codes == expected_codes

    assert cases["EXEC-03"].expected_tool_results[0]["status"] == "error"
    assert cases["EXEC-03"].expected_termination["status"] == "durability_uncertain"
    assert cases["NF-01"].expected_tool_results[0]["status"] == "ok"
    assert cases["NF-01"].expected_tool_results[0]["business_status"] == "NOT_FOUND"


def test_cases_preserve_key_failure_semantics() -> None:
    cases = {case.case_id: case for case in TrajectoryDataset.from_path(CASES_PATH).cases}

    assert cases["ARG-01"].expected_trajectory[-1]["executor_called"] is False
    assert cases["PERM-03"].expected_tool_calls[0]["tool_id"] == "raw_filesystem"
    assert cases["NF-01"].expected_tool_results[0]["business_status"] == "NOT_FOUND"
    assert cases["NF-01"].expected_tool_results[0]["status"] == "ok"
    assert cases["PART-02"].expected_termination["status"] == "partial"
    assert cases["PART-02"].expected_termination["valid_evidence_preserved"] is True
    assert cases["REP-02"].expected_attribution["primary"] == "ledger_conflict"
    assert cases["CP-03"].expected_termination["recommendation"] == "safe_to_retry"
    assert (
        cases["CP-04"].expected_termination["recommendation"]
        == "requires_manual_decision"
    )


def test_loader_rejects_duplicate_case_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    line = CASES_PATH.read_text(encoding="utf-8").splitlines()[0]
    original_read_text = Path.read_text

    def duplicate_cases(self: Path, *args, **kwargs) -> str:
        if self.resolve() == CASES_PATH.resolve():
            return f"{line}\n{line}\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", duplicate_cases)

    with pytest.raises(ValueError, match="duplicate case_id"):
        load_cases_jsonl(CASES_PATH)


def test_loader_reports_invalid_json_line(monkeypatch: pytest.MonkeyPatch) -> None:
    original_read_text = Path.read_text

    def invalid_cases(self: Path, *args, **kwargs) -> str:
        if self.resolve() == CASES_PATH.resolve():
            return '{"case_id": "broken"\n'
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", invalid_cases)

    with pytest.raises(ValueError, match="line 1"):
        load_cases_jsonl(CASES_PATH)


def test_case_file_is_jsonl_and_contains_no_runtime_support_flag() -> None:
    lines = CASES_PATH.read_text(encoding="utf-8").splitlines()

    assert lines
    for line in lines:
        payload = json.loads(line)
        assert "executable" not in payload
        assert "future" not in payload
        assert "supported" not in payload
