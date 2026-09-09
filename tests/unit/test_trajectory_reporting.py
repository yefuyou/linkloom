from __future__ import annotations

import json
from pathlib import Path

import pytest

from linkloom.evaluation.trajectory.models import RESULT_SCHEMA_VERSION, CaseResult
from linkloom.evaluation.trajectory.reporting import (
    ARTIFACT_FILENAMES,
    write_trajectory_artifacts,
)


def _result() -> CaseResult:
    return CaseResult(
        schema_version=RESULT_SCHEMA_VERSION,
        case_id="NF-01",
        category="NOT_FOUND",
        status="PASS",
        assertions=[
            {
                "assertion": "business_status",
                "passed": True,
                "expected": "NOT_FOUND",
                "actual": "NOT_FOUND",
            }
        ],
        failed_assertion=None,
        expected=None,
        actual=None,
        primary_failure_attribution=None,
        missing_capability=None,
        judge={"status": "NOT_EVALUATED", "provider": None},
    )


def _payloads() -> dict:
    return {
        "manifest": {
            "schema_version": "trajectory-run-manifest/v1",
            "run_id": "test-run",
            "status": "PASS",
            "counts": {"total": 1, "executable": 1, "future": 0},
        },
        "case_results": [_result()],
        "metrics": {
            "schema_version": "trajectory-metrics/v1",
            "metrics": {
                "not_found_semantic_accuracy": {
                    "status": "EVALUATED",
                    "numerator": 1,
                    "denominator": 1,
                    "value": 1.0,
                    "case_ids": ["NF-01"],
                }
            },
            "future_metrics": {},
        },
        "failure_summary": {
            "schema_version": "trajectory-failure-summary/v1",
            "counts": {"PASS": 1, "FAIL": 0, "NOT_IMPLEMENTED": 0},
            "failures": [],
            "missing_capabilities": {},
        },
        "capability_matrix": {
            "schema_version": "trajectory-capability-matrix/v1",
            "definitions": [],
            "derived": {
                "executable_case_ids": ["NF-01"],
                "future_case_ids": [],
                "executable_case_count": 1,
                "future_case_count": 0,
            },
        },
    }


def test_reporting_writes_exact_six_parseable_artifacts_without_aggregate_score(
    tmp_path: Path,
) -> None:
    output = tmp_path / "trajectory-run"

    written = write_trajectory_artifacts(output, **_payloads())

    assert {path.name for path in output.iterdir()} == set(ARTIFACT_FILENAMES)
    assert {path.name for path in written} == set(ARTIFACT_FILENAMES)
    for name in ARTIFACT_FILENAMES - {"case_results.jsonl", "report.md"}:
        json.loads((output / name).read_text(encoding="utf-8"))
    lines = (output / "case_results.jsonl").read_text(encoding="utf-8").splitlines()
    assert [CaseResult.from_dict(json.loads(line)).case_id for line in lines] == ["NF-01"]
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "aggregate" not in report.casefold()
    assert "raw note" not in report.casefold()


def test_reporting_refuses_unknown_existing_files_instead_of_deleting_them(
    tmp_path: Path,
) -> None:
    output = tmp_path / "trajectory-run"
    output.mkdir()
    unknown = output / "user-file.txt"
    unknown.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected files"):
        write_trajectory_artifacts(output, **_payloads())

    assert unknown.read_text(encoding="utf-8") == "keep"
