from __future__ import annotations

from pathlib import Path

from linkloom.evaluation.trajectory.capabilities import (
    CAPABILITY_MATRIX,
    FUTURE,
    SUPPORTED,
    assess_case_capabilities,
    partition_case_ids,
)
from linkloom.evaluation.trajectory.dataset import TrajectoryDataset


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"

EXPECTED_SUPPORTED_CAPABILITIES = {
    "validation.arguments",
    "policy.permissions",
    "budget.max_calls",
    "execution.failure_normalization",
    "output.schema_validation",
    "retrieval.not_found_semantics",
    "retrieval.partial_recovery",
    "ledger.duplicate_call_id",
    "checkpoint.pending_safety",
}

EXPECTED_FUTURE_CAPABILITIES = {
    "routing.model_driven",
    "tool_selection.model_driven",
    "guard.semantic_repeat",
    "guard.semantic_loop",
    "guard.premature_final",
    "checkpoint.full_resume",
    "memory.staleness",
    "memory.conflict",
    "llm.judge",
}


def test_capability_matrix_has_explicit_evidence_boundaries_and_missing_reasons() -> None:
    supported = {
        capability_id
        for capability_id, definition in CAPABILITY_MATRIX.items()
        if definition.status == SUPPORTED
    }
    future = {
        capability_id
        for capability_id, definition in CAPABILITY_MATRIX.items()
        if definition.status == FUTURE
    }

    assert supported == EXPECTED_SUPPORTED_CAPABILITIES
    assert future == EXPECTED_FUTURE_CAPABILITIES
    assert supported.isdisjoint(future)

    for definition in CAPABILITY_MATRIX.values():
        assert definition.evidence.strip()
        assert definition.boundary.strip()
        if definition.status == FUTURE:
            assert definition.missing_reason is not None
            assert definition.missing_reason.strip()
        else:
            assert definition.missing_reason is None


def test_matrix_derives_23_executable_and_18_future_cases_without_overlap() -> None:
    dataset = TrajectoryDataset.from_path(CASES_PATH)

    executable, future = partition_case_ids(dataset.cases)

    assert len(executable) == 23
    assert len(future) == 18
    assert executable.isdisjoint(future)
    assert executable | future == {case.case_id for case in dataset.cases}


def test_future_case_reports_each_missing_capability_reason() -> None:
    case = TrajectoryDataset.from_path(CASES_PATH).by_id("RTE-01")

    support = assess_case_capabilities(case)

    assert support.executable is False
    assert support.missing_capabilities == ("routing.model_driven",)
    assert support.missing_reasons[0].strip()


def test_llm_judge_is_explicitly_future_and_has_no_callable_adapter() -> None:
    judge = CAPABILITY_MATRIX["llm.judge"]

    assert judge.status == FUTURE
    assert judge.missing_reason
    assert judge.adapter is None
