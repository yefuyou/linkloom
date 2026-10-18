"""Scoring and no-Gold-input boundary for the new synthetic discovery corpus."""
import builtins
from pathlib import Path

from scripts.evaluate_task_candidates import score
from tests.task_candidate_support import public_cases, run_case, version_trials, five_line_safety_trial


def test_predictions_do_not_read_gold_and_are_repeatable(monkeypatch):
    original_open, original_path_open = builtins.open, Path.open

    def check(path):
        if "gold" in str(path).replace("\\", "/").split("/"):
            raise AssertionError("Gold must not enter candidate discovery")

    def safe_open(path, *args, **kwargs):
        check(path)
        return original_open(path, *args, **kwargs)

    def safe_path_open(path, *args, **kwargs):
        check(path)
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", safe_open)
    monkeypatch.setattr(Path, "open", safe_path_open)
    cases = public_cases()
    first = [run_case(case) for case in cases]
    assert first == [run_case(case) for case in cases]
    assert len(first) == 12
    assert all(len(row["discovery"]) <= 5 for row in first)


def test_scorer_measures_endpoint_recall_noise_and_explicit_omissions():
    predictions = [dict(case_id="unit", baseline=[], discovery=[dict(target="O:a"), dict(target="O:noise")])]
    gold = {"cases": {"unit": dict(targets=["O:a", "O:b"], explicit_references=["O:b"])}}
    metrics, failures = score(predictions, gold, 5)
    assert metrics["discovery"]["recall_at_k"] == .5
    assert metrics["discovery"]["noise_rate"] == .5
    assert metrics["discovery"]["counts"]["explicit_missed"] == 1
    assert failures[-1]["missed"] == ["O:b"]


def test_synthetic_cross_project_and_unconnected_cases_have_no_discovery_pollution():
    cases = {c["case_id"]: c for c in public_cases()}
    assert run_case(cases["C05"])["discovery"] == []
    assert len(run_case(cases["C05"])["baseline"]) == 1
    assert run_case(cases["C06"])["discovery"] == []
    # Same-project unrelated work is recorded noise, not falsely called a relation.
    weak = run_case(cases["C12"])["discovery"]
    assert len(weak) == 1 and "PROJECT_COOCCURRENCE_ONLY" in weak[0]["candidate"]["uncertainty"]


def test_source_updates_expire_task_alias_and_change_candidates():
    trials = version_trials()
    assert len(trials) == 3
    assert all(t["valid_before"] and not t["valid_after"] and t["old_version"] != t["new_version"] for t in trials)


def test_original_five_lines_do_not_establish_revision_or_delay():
    result = five_line_safety_trial()
    assert len(result["observations"]) == 2
    assert result["observations"][1]["project"] is None
    assert result["same_task"] == "UNKNOWN"
    assert result["revision"] == "UNKNOWN"
    assert result["comparison_status"] == "BLOCKED"
    assert result["delta_calendar_days"] is None
    assert not any(c["predicate_hint"] == "REVISES_PLAN" for c in result["discovery"]["candidates"])
