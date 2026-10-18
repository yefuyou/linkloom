"""Run the M2-P4 Dev lifecycle harness against a fresh synthetic SQLite DB.

Usage from the qualification checkout::

    python scripts/evaluate_task_relation_lifecycle.py --output work/p4-lifecycle-run

The harness uses only the Dev input/expected files and real M1/P1/P2/P3
constructors.  It never opens the historical relation Gold files, invokes a
Provider, or mutates the original databases.
"""
from __future__ import annotations

from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.task_relation_lifecycle_support import (  # noqa: E402
    EXPECTED,
    INPUT,
    load_expected,
    run_dev_lifecycle,
)


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assessment(read: dict) -> dict:
    value = read["assessment"]
    if not isinstance(value, dict):
        raise AssertionError("relation read did not contain an assessment payload")
    return value


def _historical_comparison(read: dict) -> dict:
    value = read["historical_comparison"]
    if not isinstance(value, dict):
        raise AssertionError("comparison read did not retain its historical payload")
    return value


def _record(checks: list[dict], family: str, name: str, passed: bool) -> None:
    checks.append({"family": family, "name": name, "passed": bool(passed)})


def evaluate(output: Path) -> dict:
    output = output.resolve()
    work_root = (ROOT / "work").resolve()
    if not output.is_relative_to(work_root):
        raise ValueError("use a new task-owned output directory under work/")
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    runtime.mkdir()

    actual = run_dev_lifecycle(runtime)
    _write(output / "predictions.json", actual)
    expected = {item["case_id"]: item for item in load_expected()["cases"]}
    cases = actual["cases"]
    atomic: list[dict] = []

    d04_expected = expected["D04-plus-four"]
    d04 = cases["D04-plus-four"]
    d04_initial_identity = _assessment(d04["initial"]["identity"])
    d04_initial_revision = _assessment(d04["initial"]["revision"])
    d04_initial_comparison = _historical_comparison(d04["initial"]["comparison"])
    # The initial revision payload is not returned separately by the compact
    # D04 projection; its verdict is exercised by the P3 object before write
    # and the lifecycle read is represented by the stored identity/comparison.
    _record(atomic, "D04-plus-four", "identity_verdict", d04_initial_identity["verdict"] == d04_expected["identity_verdict"])
    _record(atomic, "D04-plus-four", "comparison_status", d04_initial_comparison["status"] == d04_expected["comparison_status"])
    _record(atomic, "D04-plus-four", "comparison_delta", d04_initial_comparison["delta_calendar_days"] == d04_expected["comparison_delta"])
    d04_after_identity_read = d04["after_retraction"]["identity"]
    d04_after_identity = _assessment(d04_after_identity_read)
    d04_after_comparison = d04["after_retraction"]["comparison"]
    d04_current = d04_after_comparison["current_comparison"]
    _record(atomic, "D04-plus-four", "revision_verdict", d04_initial_revision["verdict"] == d04_expected["revision_verdict"])
    _record(atomic, "D04-plus-four", "after.identity_lifecycle", d04_after_identity_read["persistent_lifecycle"] == d04_expected["after_identity_retraction"]["identity_lifecycle"])
    _record(atomic, "D04-plus-four", "after.identity_current_supported", d04_after_identity_read["current_supported"] is d04_expected["after_identity_retraction"]["identity_current_supported"])
    _record(atomic, "D04-plus-four", "after.comparison_current_valid", d04_after_comparison["current_valid"] is d04_expected["after_identity_retraction"]["comparison_current_valid"])
    _record(atomic, "D04-plus-four", "after.comparison_current_status", d04_current["status"] == d04_expected["after_identity_retraction"]["comparison_current_status"])
    _record(atomic, "D04-plus-four", "after.comparison_current_delta", d04_current["delta_calendar_days"] == d04_expected["after_identity_retraction"]["comparison_current_delta"])
    _record(atomic, "D04-plus-four", "after.comparison_historical_delta", d04_after_comparison["historical_comparison"]["delta_calendar_days"] == d04_expected["after_identity_retraction"]["comparison_historical_delta"])
    _record(atomic, "D04-plus-four", "after.blocking_reasons_present", bool(d04_after_comparison["blocking_reasons"]))

    d07_expected = expected["D07-unknown"]
    d07 = cases["D07-unknown"]
    d07_identity = _assessment(d07["identity"])
    d07_revision = _assessment(d07["revision"])
    d07_comparison = d07["comparison"]
    _record(atomic, "D07-unknown", "identity_verdict", d07_identity["verdict"] == d07_expected["identity_verdict"])
    _record(atomic, "D07-unknown", "revision_verdict", d07_revision["verdict"] == d07_expected["revision_verdict"])
    _record(atomic, "D07-unknown", "comparison_status", _historical_comparison(d07_comparison)["status"] == d07_expected["comparison_status"])
    _record(atomic, "D07-unknown", "comparison_delta", _historical_comparison(d07_comparison)["delta_calendar_days"] == d07_expected["comparison_delta"])
    _record(atomic, "D07-unknown", "current_supported", d07["identity"]["current_supported"] is d07_expected["current_supported"])
    _record(atomic, "D07-unknown", "blocking_reason_contains", any(d07_expected["blocking_reason_contains"] in reason for reason in d07_comparison["blocking_reasons"]))

    d08_expected = expected["D08-derived-project"]
    d08 = cases["D08-derived-project"]
    d08_before_rows = d08["before_retraction"]["project"]
    d08_after = d08["after_retraction"]
    derived_id = _assessment(d08_after["derived"])["assessment_id"]
    _record(atomic, "D08-derived-project", "alias_verdict", _assessment(d08["before_retraction"]["alias"])["verdict"] == d08_expected["alias_verdict"])
    _record(atomic, "D08-derived-project", "derived_project_verdict", next(_assessment(row)["verdict"] for row in d08_before_rows if "observation_id" in _assessment(row)["subject"]) == d08_expected["derived_project_verdict"])
    _record(atomic, "D08-derived-project", "wrong_context_verdict", any(_assessment(row)["verdict"] == d08_expected["wrong_context_verdict"] for row in d08_after["history"]))
    _record(atomic, "D08-derived-project", "project_query_contains_derived", any("observation_id" in _assessment(row)["subject"] for row in d08_before_rows) == d08_expected["project_query_contains_derived"])
    _record(atomic, "D08-derived-project", "project_query_current_supported", all(row["current_supported"] is True for row in d08_before_rows))
    _record(atomic, "D08-derived-project", "after.project_not_published", d08_after["project"] == [])
    _record(atomic, "D08-derived-project", "after.alias_lifecycle", d08_after["alias"]["persistent_lifecycle"] == d08_expected["after_alias_retraction"]["alias_lifecycle"])
    _record(atomic, "D08-derived-project", "after.derived_current_supported", d08_after["derived"]["current_supported"] == d08_expected["after_alias_retraction"]["derived_project_current_supported"])
    _record(atomic, "D08-derived-project", "after.historical_contains_derived", (derived_id in {_assessment(row)["assessment_id"] for row in d08_after["history"]}) == d08_expected["after_alias_retraction"]["historical_relation_contains_derived"])

    redwood_expected = expected["REDWOOD-version-retraction"]
    redwood = cases["REDWOOD-version-retraction"]
    s0 = redwood["s0"]
    _record(atomic, "REDWOOD-version-retraction", "s0.identity_verdict", _assessment(s0["identity"])["verdict"] == redwood_expected["s0"]["identity_verdict"])
    _record(atomic, "REDWOOD-version-retraction", "s0.revision_verdict", _assessment(s0["revision"])["verdict"] == redwood_expected["s0"]["revision_verdict"])
    _record(atomic, "REDWOOD-version-retraction", "s0.comparison_status", _historical_comparison(s0["comparison"])["status"] == redwood_expected["s0"]["comparison_status"])
    _record(atomic, "REDWOOD-version-retraction", "s0.comparison_delta", _historical_comparison(s0["comparison"])["delta_calendar_days"] == redwood_expected["s0"]["comparison_delta"])
    _record(atomic, "REDWOOD-version-retraction", "s0.identity_current_supported", s0["identity"]["current_supported"] is True)
    _record(atomic, "REDWOOD-version-retraction", "s0.revision_current_supported", s0["revision"]["current_supported"] is True)
    _record(atomic, "REDWOOD-version-retraction", "s0.comparison_current_valid", s0["comparison"]["current_valid"] is True)
    _record(atomic, "REDWOOD-version-retraction", "s0.current_comparison_computable", s0["comparison"]["current_comparison"]["status"] == "COMPUTABLE" and s0["comparison"]["current_comparison"]["delta_calendar_days"] == 2)

    s1 = redwood["s1"]
    _record(atomic, "REDWOOD-version-retraction", "s1.old_identity_lifecycle", s1["old_identity"]["persistent_lifecycle"] == redwood_expected["s1"]["old_identity_lifecycle"])
    _record(atomic, "REDWOOD-version-retraction", "s1.old_identity_evidence_state", s1["old_identity"]["evidence_state"] == redwood_expected["s1"]["old_identity_evidence_state"])
    _record(atomic, "REDWOOD-version-retraction", "s1.old_revision_lifecycle", s1["old_revision"]["persistent_lifecycle"] == redwood_expected["s1"]["old_revision_lifecycle"])
    _record(atomic, "REDWOOD-version-retraction", "s1.old_revision_evidence_state", s1["old_revision"]["evidence_state"] == redwood_expected["s1"]["old_revision_evidence_state"])
    _record(atomic, "REDWOOD-version-retraction", "s1.old_comparison_current_valid", s1["old_comparison"]["current_valid"] is redwood_expected["s1"]["old_comparison_current_valid"])
    _record(atomic, "REDWOOD-version-retraction", "s1.old_comparison_historical_delta", s1["old_comparison"]["historical_comparison"]["delta_calendar_days"] == redwood_expected["s1"]["old_comparison_historical_delta"])
    _record(atomic, "REDWOOD-version-retraction", "s1.new_identity_verdict", _assessment(s1["new_identity"])["verdict"] == redwood_expected["s1"]["new_identity_verdict"])
    _record(atomic, "REDWOOD-version-retraction", "s1.new_revision_verdict", _assessment(s1["new_revision"])["verdict"] == redwood_expected["s1"]["new_revision_verdict"])
    _record(atomic, "REDWOOD-version-retraction", "s1.new_comparison_status", _historical_comparison(s1["new_comparison"])["status"] == redwood_expected["s1"]["new_comparison_status"])
    _record(atomic, "REDWOOD-version-retraction", "s1.new_comparison_delta", _historical_comparison(s1["new_comparison"])["delta_calendar_days"] == redwood_expected["s1"]["new_comparison_delta"])
    _record(atomic, "REDWOOD-version-retraction", "s1.new_identity_current_supported", s1["new_identity"]["current_supported"] is True)
    _record(atomic, "REDWOOD-version-retraction", "s1.new_comparison_current_not_applicable", s1["new_comparison"]["current_valid"] is False and s1["new_comparison"]["current_comparison"]["status"] == "NOT_APPLICABLE" and s1["new_comparison"]["current_comparison"]["delta_calendar_days"] is None)

    s2 = redwood["s2"]
    _record(atomic, "REDWOOD-version-retraction", "s2.old_identity_lifecycle", s2["old_identity"]["persistent_lifecycle"] == redwood_expected["s2"]["old_identity_lifecycle"])
    _record(atomic, "REDWOOD-version-retraction", "s2.old_revision_lifecycle", s2["old_revision"]["persistent_lifecycle"] == redwood_expected["s2"]["old_revision_lifecycle"])
    _record(atomic, "REDWOOD-version-retraction", "s2.old_revision_evidence_state", s2["old_revision"]["evidence_state"] == redwood_expected["s2"]["old_revision_evidence_state"])
    _record(atomic, "REDWOOD-version-retraction", "s2.new_identity_lifecycle", s2["new_identity"]["persistent_lifecycle"] == redwood_expected["s2"]["new_identity_lifecycle"])
    _record(atomic, "REDWOOD-version-retraction", "s2.new_identity_current_supported", s2["new_identity"]["current_supported"] is redwood_expected["s2"]["new_identity_current_supported"])
    _record(atomic, "REDWOOD-version-retraction", "s2.old_comparison_current_valid", s2["old_comparison"]["current_valid"] is redwood_expected["s2"]["old_comparison_current_valid"])
    _record(atomic, "REDWOOD-version-retraction", "s2.old_comparison_historical_delta", s2["old_comparison"]["historical_comparison"]["delta_calendar_days"] == redwood_expected["s2"]["old_comparison_historical_delta"])

    checks = {f"{item['family']}.{item['name']}": item["passed"] for item in atomic}
    families = {}
    for item in atomic:
        family = families.setdefault(item["family"], {"passed": 0, "total": 0})
        family["total"] += 1
        family["passed"] += int(item["passed"])
    for family in families.values():
        family["all_passed"] = family["passed"] == family["total"]
    metrics = {
        "schema_version": "task-relation-lifecycle-metrics/v1",
        "split": "dev",
        "checks": checks,
        "atomic_checks": atomic,
        "families": families,
        "passed": sum(checks.values()),
        "total": len(checks),
        "input_sha256": _sha256(INPUT),
        "expected_sha256": _sha256(EXPECTED),
        "provider_calls": actual["provider_calls"],
        "holdout_executed": actual["holdout_executed"],
        "real_sqlite": actual["real_sqlite"],
        "scope": "M2-P4 Dev lifecycle only; actual SQLite reads; no old Gold, Provider, or holdout",
    }
    _write(output / "metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result = evaluate(args.output)
    raise SystemExit(0 if result["passed"] == result["total"] else 1)
