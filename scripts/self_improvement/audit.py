"""Reassess sealed observations after review without new execution or generation."""
import argparse
import copy
import json
import re
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/integration"), str(ROOT)]
from scripts.self_improvement.run import compare, case_deltas, digest, verify_fixtures, write, DATA
from scripts.self_improvement.contracts import Candidate, failure_signals, targeted_reductions
from scripts.self_improvement.scorer import score
from scripts.self_improvement.provider import accounting


def regression_summary(path):
    if not path.exists():
        return {"passed": False, "available": False, "all_tests_executed": False,
                "reason": "regression_xml_unavailable"}
    suites = ET.parse(path).getroot()
    result = {name: sum(int(suite.get(name, 0)) for suite in suites.iter("testsuite"))
              for name in ("tests", "errors", "failures", "skipped")}
    result["passed"] = result["errors"] == 0 and result["failures"] == 0
    result["xml_sha256"] = digest(path)
    result["failed_tests"] = [case.get("classname", "") + "." + case.get("name", "")
                              for case in suites.iter("testcase") if case.find("failure") is not None
                              or case.find("error") is not None]
    return result


def audit(run):
    run = run.resolve()
    if not run.is_relative_to((ROOT / "work/self-improvement-lab").resolve()):
        raise ValueError("Not a local experiment")
    verify_fixtures()
    report = copy.deepcopy(json.loads((run / "report.json").read_text(encoding="utf-8")))
    protocol = json.loads((run / "protocol.json").read_text(encoding="utf-8"))
    if any(digest(ROOT / name) != sha for name, sha in protocol["production_fingerprints"].items()):
        raise ValueError("Production source changed since execution")

    def rescore(folder, split):
        raw = folder / "raw.json"
        seal = json.loads((folder / "seal.json").read_text(encoding="utf-8"))
        if digest(raw) != seal["raw_sha256"] or digest(folder / "candidate.json") != seal["candidate_sha256"]:
            raise ValueError("Original execution seal changed")
        result = score(json.loads(raw.read_text(encoding="utf-8")),
                       json.loads((DATA / f"{split}_gold.json").read_text(encoding="utf-8")))
        write(folder / "review_score.json", result)
        write(folder / "review_score_seal.json", {"raw_sha256": digest(raw),
              "candidate_sha256": digest(folder / "candidate.json"),
              "gold_sha256": digest(DATA / f"{split}_gold.json"),
              "scorer_sha256": digest(Path(__file__).with_name("scorer.py")),
              "review_score_sha256": digest(folder / "review_score.json")})
        return result

    before = rescore(run / "baseline/dev", "dev")
    report["before"] = before
    validation_before = (rescore(run / "baseline/validation", "validation")
                         if (run / "baseline/validation/raw.json").exists() else None)
    signals = failure_signals(json.loads((run / "baseline/dev/raw.json").read_text(encoding="utf-8"))["cases"])
    for trial in report["trials"]:
        folder = run / f"round-{trial['round']}" / trial["candidate"]["id"]
        if not (folder / "dev/raw.json").exists():
            trial.update(eligible=False, review_gate="execution_failed_no_observations")
            continue
        after = rescore(folder / "dev", "dev")
        trial["before"] = before
        trial["after"] = after
        trial["per_case_deltas"] = case_deltas(before, after)
        allowed, trial["development_gate"] = compare(before, after)
        raw = json.loads((folder / "dev/raw.json").read_text(encoding="utf-8"))
        reductions = targeted_reductions(signals, raw["cases"], Candidate.parse(trial["candidate"]))
        trial["targeted_failure_reductions"] = reductions
        if allowed and not reductions:
            allowed, trial["development_gate"] = False, "no_targeted_failure_gain"
        if trial.get("validation"):
            validation = rescore(folder / "validation", "validation")
            validation_allowed, reason = compare(validation_before, validation)
            trial["validation"] = {"before": validation_before, "after": validation, "gate": reason}
            allowed &= validation_allowed
        else:
            allowed = False  # audit never invents a missing independent validation
        trial["eligible"] = allowed
    final_folder = Path(report["final_baseline_cache"]) / "baseline/final" if report.get("final_baseline_cache") else run / "baseline/final"
    final_before = rescore(final_folder, "final") if (final_folder / "raw.json").exists() else None
    final_after = rescore(run / "winner/final", "final") if report["final"]["after"] else None
    final_allowed, reason = compare(final_before, final_after) if final_before and final_after else (False, "no_finalist_or_observations")
    candidate_path = run / "winner/final/candidate.json"
    if final_allowed and json.loads(candidate_path.read_text(encoding="utf-8"))["id"] not in {
            trial["candidate"]["id"] for trial in report["trials"] if trial["eligible"]}:
        final_allowed, reason = False, "not_development_and_validation_eligible"
    report["final"] = {"before": final_before, "after": final_after, "gate": reason}
    regression = run / "regression-review.xml"
    if not regression.exists():
        regression = run / "regression.xml"
    report["regression"] = regression_summary(regression)
    followup = run / "regression-review.log"
    if followup.exists() and not (run / "regression-review.xml").exists():
        progress = re.findall(r"\[\s*(\d+)%\]", followup.read_text(encoding="utf-8"))
        report["regression"]["all_tests_executed"] = False
        report["regression"]["followup"] = {
            "status": "followup_without_completed_report",
            "last_reported_progress": progress[-1] + "%" if progress else None,
            "log_sha256": digest(followup),
            "reason": "no complete followup XML; no acceptance possible"}
    report["focused_checks"] = {path.name: regression_summary(path) for path in (
        run / "focused-review.xml", run / "provider-review.xml", run / "gates-review.xml") if path.exists()}
    report["selected"] = (json.loads((run / "winner/final/candidate.json").read_text(encoding="utf-8"))
                          if final_allowed and report["regression"]["passed"] else None)
    report["rejection_stage"] = None if report["selected"] else "full_regression" if final_allowed else "evaluation"
    report["conclusion"] = "A" if report["selected"] else "B"
    report["provider"] = accounting(ROOT / "work/self-improvement-lab")
    report["review"] = {"new_model_requests": 0, "new_runtime_executions": 0,
                        "new_candidates": 0, "purpose": "stronger per-case audit of unchanged sealed observations",
                        "original_report_sha256": digest(run / "report.json")}
    write(run / "report.review.json", report)
    print(json.dumps({"conclusion": report["conclusion"], "regression": report["regression"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    audit(parser.parse_args().run)
