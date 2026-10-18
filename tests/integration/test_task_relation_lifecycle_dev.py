"""P4 Dev acceptance: actual side-store writes followed by read-only SQLite reads."""
from __future__ import annotations

from pathlib import Path

from tests.task_relation_lifecycle_support import load_expected, run_dev_lifecycle


def _assessment(read: dict):
    value = read["assessment"]
    assert isinstance(value, dict)
    return value


def _comparison(read: dict):
    value = read["historical_comparison"]
    assert isinstance(value, dict)
    return value


def test_task_relation_lifecycle_dev(tmp_path: Path):
    expected = {item["case_id"]: item for item in load_expected()["cases"]}
    actual = run_dev_lifecycle(tmp_path)
    assert actual["real_sqlite"] is True
    assert actual["provider_calls"] == 0
    assert actual["holdout_executed"] is False

    d04 = actual["cases"]["D04-plus-four"]
    initial_identity = _assessment(d04["initial"]["identity"])
    initial_revision = _assessment(d04["initial"]["revision"])
    initial_comparison = _comparison(d04["initial"]["comparison"])
    assert initial_identity["verdict"] == expected["D04-plus-four"]["identity_verdict"]
    assert initial_revision["verdict"] == expected["D04-plus-four"]["revision_verdict"]
    assert initial_comparison["status"] == expected["D04-plus-four"]["comparison_status"]
    assert initial_comparison["delta_calendar_days"] == expected["D04-plus-four"]["comparison_delta"]
    assert d04["initial"]["comparison"]["current_valid"] is True
    assert d04["initial"]["comparison"]["current_comparison"]["delta_calendar_days"] == 4

    d04_after = d04["after_retraction"]
    assert d04_after["identity"]["persistent_lifecycle"] == expected["D04-plus-four"]["after_identity_retraction"]["identity_lifecycle"]
    assert d04_after["identity"]["current_supported"] is False
    assert d04_after["comparison"]["current_valid"] is False
    assert d04_after["comparison"]["current_comparison"]["status"] == expected["D04-plus-four"]["after_identity_retraction"]["comparison_current_status"]
    assert d04_after["comparison"]["current_comparison"]["delta_calendar_days"] is expected["D04-plus-four"]["after_identity_retraction"]["comparison_current_delta"]
    assert d04_after["comparison"]["historical_comparison"]["delta_calendar_days"] == expected["D04-plus-four"]["after_identity_retraction"]["comparison_historical_delta"]
    assert d04_after["comparison"]["blocking_reasons"]

    d07 = actual["cases"]["D07-unknown"]
    assert _assessment(d07["identity"])["verdict"] == expected["D07-unknown"]["identity_verdict"]
    assert d07["identity"]["current_supported"] is False
    assert _assessment(d07["revision"])["verdict"] == expected["D07-unknown"]["revision_verdict"]
    assert _comparison(d07["comparison"])["status"] == expected["D07-unknown"]["comparison_status"]
    assert _comparison(d07["comparison"])["delta_calendar_days"] is None
    assert any(expected["D07-unknown"]["blocking_reason_contains"] in reason for reason in d07["comparison"]["blocking_reasons"])

    d08 = actual["cases"]["D08-derived-project"]
    project_rows = d08["before_retraction"]["project"]
    task_rows = [row for row in project_rows if "observation_id" in _assessment(row)["subject"]]
    assert len(task_rows) == 1
    assert _assessment(task_rows[0])["subject"].get("observation_id")
    assert all(row["current_supported"] is True for row in project_rows)
    assert d08["before_retraction"]["alias"]["current_supported"] is True
    after_project_rows = d08["after_retraction"]["project"]
    assert after_project_rows == []
    history_ids = {_assessment(row)["assessment_id"] for row in d08["after_retraction"]["history"]}
    derived_id = d08["after_retraction"]["derived"]["assessment"]["assessment_id"]
    assert derived_id in history_ids
    assert d08["after_retraction"]["derived"]["current_supported"] is False
    assert d08["after_retraction"]["derived"]["persistent_lifecycle"] == "ACTIVE"
    assert d08["after_retraction"]["alias"]["persistent_lifecycle"] == expected["D08-derived-project"]["after_alias_retraction"]["alias_lifecycle"]

    redwood = actual["cases"]["REDWOOD-version-retraction"]
    s0 = redwood["s0"]
    assert _assessment(s0["identity"])["verdict"] == expected["REDWOOD-version-retraction"]["s0"]["identity_verdict"]
    assert _assessment(s0["revision"])["verdict"] == expected["REDWOOD-version-retraction"]["s0"]["revision_verdict"]
    assert _comparison(s0["comparison"])["status"] == expected["REDWOOD-version-retraction"]["s0"]["comparison_status"]
    assert _comparison(s0["comparison"])["delta_calendar_days"] == expected["REDWOOD-version-retraction"]["s0"]["comparison_delta"]
    assert s0["identity"]["current_supported"] is True
    assert s0["revision"]["current_supported"] is True
    assert s0["comparison"]["current_valid"] is True
    assert s0["comparison"]["current_comparison"]["status"] == "COMPUTABLE"
    assert s0["comparison"]["current_comparison"]["delta_calendar_days"] == 2

    s1 = redwood["s1"]
    assert s1["old_identity"]["persistent_lifecycle"] == expected["REDWOOD-version-retraction"]["s1"]["old_identity_lifecycle"]
    assert s1["old_identity"]["evidence_state"] == expected["REDWOOD-version-retraction"]["s1"]["old_identity_evidence_state"]
    assert s1["old_revision"]["persistent_lifecycle"] == expected["REDWOOD-version-retraction"]["s1"]["old_revision_lifecycle"]
    assert s1["old_revision"]["evidence_state"] == expected["REDWOOD-version-retraction"]["s1"]["old_revision_evidence_state"]
    assert s1["old_comparison"]["current_valid"] is False
    assert s1["old_comparison"]["historical_comparison"]["delta_calendar_days"] == 2
    assert _assessment(s1["new_identity"])["verdict"] == expected["REDWOOD-version-retraction"]["s1"]["new_identity_verdict"]
    assert _assessment(s1["new_revision"])["verdict"] == expected["REDWOOD-version-retraction"]["s1"]["new_revision_verdict"]
    assert _comparison(s1["new_comparison"])["status"] == expected["REDWOOD-version-retraction"]["s1"]["new_comparison_status"]
    assert _comparison(s1["new_comparison"])["delta_calendar_days"] is None
    assert s1["new_identity"]["current_supported"] is True
    assert s1["new_comparison"]["current_valid"] is False
    assert s1["new_comparison"]["current_comparison"]["status"] == "NOT_APPLICABLE"
    assert s1["new_comparison"]["current_comparison"]["delta_calendar_days"] is None

    s2 = redwood["s2"]
    assert s2["old_identity"]["persistent_lifecycle"] == expected["REDWOOD-version-retraction"]["s2"]["old_identity_lifecycle"]
    assert s2["old_revision"]["persistent_lifecycle"] == expected["REDWOOD-version-retraction"]["s2"]["old_revision_lifecycle"]
    assert s2["old_revision"]["evidence_state"] == expected["REDWOOD-version-retraction"]["s2"]["old_revision_evidence_state"]
    assert s2["new_identity"]["persistent_lifecycle"] == expected["REDWOOD-version-retraction"]["s2"]["new_identity_lifecycle"]
    assert s2["new_identity"]["current_supported"] is True
    assert s2["old_comparison"]["current_valid"] is False
    assert s2["old_comparison"]["historical_comparison"]["delta_calendar_days"] == 2
