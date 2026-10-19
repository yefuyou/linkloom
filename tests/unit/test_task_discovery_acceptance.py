"""Scoring guards: missed work and false task merges must remain visible."""
import json

from tests import task_discovery_acceptance as diagnostic


def test_not_run_cannot_report_perfect_recall(tmp_path):
    predictions = tmp_path / "not-run.json"
    predictions.write_text(json.dumps({"execution": "NOT_RUN", "cases": []}), encoding="utf-8")
    metrics = tmp_path / "metrics.json"
    diagnostic.score(predictions, metrics)
    report = diagnostic.read(metrics)
    assert report["expected"] == 5 and report["task_recall"] == 0
    assert report["task_precision"] is None and report["field_accuracy_on_matched_tasks"] is None
    assert len(report["failures"]) == 3


def test_recorded_real_diagnostic_is_reproducibly_scored_without_calls(tmp_path):
    predictions = diagnostic.ROOT / "docs/requirements/m3-task-discovery/evidence/live/predictions.json"
    if not predictions.exists():
        import pytest
        pytest.skip("No recorded diagnostic; never performs a live call")
    metrics = tmp_path / "metrics.json"
    diagnostic.score(predictions, metrics)
    report = diagnostic.read(metrics)
    assert report["matched"] == 4 and report["predicted"] == report["expected"] == 5
    assert report["task_recall"] == report["task_precision"] == .8
    assert report["valid_evidence"] == report["evidence_count"] == 5
    assert any(f["reason"] == "MISSED_TASK" for f in report["failures"])


def test_false_merge_of_same_owner_and_date_is_not_credited_as_one_correct_task(tmp_path):
    predictions = diagnostic.ROOT / "docs/requirements/m3-task-discovery/evidence/live/predictions.json"
    if not predictions.exists():
        import pytest
        pytest.skip("No recorded diagnostic; never performs a live call")
    data = diagnostic.read(predictions)
    result = next(row["result"] for row in data["cases"] if row["case_id"] == "same-project-distinct-tasks")
    first, second, third = result["groups"]
    first["observation_ids"] += second["observation_ids"]
    result["groups"] = [first, third]
    merged = tmp_path / "false-merge.json"
    merged.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    metrics = tmp_path / "metrics.json"
    diagnostic.score(merged, metrics)
    report = diagnostic.read(metrics)
    case = next(row for row in report["cases"] if row["case_id"] == "same-project-distinct-tasks")
    assert case["matched"] == 1 and case["predicted"] == 2
    assert sum(f["reason"] == "MISSED_TASK" for f in report["failures"] if f["case_id"] == case["case_id"]) == 2
