"""Run bounded strategy search, independent scoring, one heldout, one regression.

Example: python scripts/self_improvement/run.py --budget 6 --live-reflection
Default mode is fully offline; production policy is never activated or edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.self_improvement.contracts import BASELINE, Candidate, failure_signals, promotion_gate, targeted_reductions

DATA = ROOT / "tests/fixtures/self_improvement"
FROZEN_MANIFEST_SHA256 = "3d0c57ed3e5521a48feca920d609bc978d3047bb5e46d4958d7e6fcdb4ac5857"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)


def child(module, arguments, log, *, timeout=240, provider=False, environment=None):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT),
           str(ROOT / "tests/integration")]), "LINKLOOM_RETRIEVAL_MODE": "bm25", "PYTHONUTF8": "1"}
    if not provider:
        for key in list(env):
            if key.endswith("API_KEY") or key.startswith("LINKLOOM_RUN_"):
                env.pop(key)
    env.update(environment or {})
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run([sys.executable, "-m", module, *map(str, arguments)], cwd=ROOT,
                                   env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(f"Child failed: {module}; inspect {log.name}")


def verify_fixtures():
    if digest(DATA / "freeze.json") != FROZEN_MANIFEST_SHA256:
        raise ValueError("Frozen split manifest changed")
    manifest = json.loads((DATA / "freeze.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if digest(DATA / name) != expected:
            raise ValueError("Frozen fixture mismatch: " + name)
    return manifest


def evaluate(candidate, split, out):
    out.mkdir(parents=True)
    config = out / "candidate.json"
    write(config, candidate.to_dict())
    raw, scored = out / "raw.json", out / "score.json"
    child("scripts.self_improvement.worker", ["--public", DATA / f"{split}_public.json",
          "--candidate", config, "--output", raw], out / "runtime.log")
    # Only after execution is complete is the Gold-bearing scorer started.
    seal = {"candidate_sha256": digest(config), "raw_sha256": digest(raw)}
    write(out / "seal.json", seal)
    child("scripts.self_improvement.scorer", ["--raw", raw, "--gold", DATA / f"{split}_gold.json",
          "--output", scored], out / "scorer.log")
    if seal["raw_sha256"] != digest(raw) or seal["candidate_sha256"] != digest(config):
        raise RuntimeError("Experiment changed while being scored")
    write(out / "score_seal.json", {"score_sha256": digest(scored), "gold_sha256": digest(DATA / f"{split}_gold.json"),
          "scorer_sha256": digest(Path(__file__).with_name("scorer.py")), "execution_complete": True})
    return json.loads(scored.read_text(encoding="utf-8")), json.loads(raw.read_text(encoding="utf-8"))


def compare(before, after):
    allowed, reason = promotion_gate(before["metrics"], after["metrics"])
    prior = {row["id"]: row for row in before["cases"]}
    if any(prior[row["id"]]["pass"] and not row["pass"] for row in after["cases"]):
        return False, "per_case_regression"
    for row in after["cases"]:
        previous = prior[row["id"]]
        for field in ("citation_valid", "claim_supported", "verified_read_dependency",
                      "bindings_verified", "workspace_valid", "outcome_match", "reason_match"):
            if previous.get(field) is True and row.get(field) is False:
                return False, "per_case_" + field + "_regression"
        if row.get("safety_violations", 0):
            return False, "per_case_safety_veto"
        if previous.get("run_status") == "completed" and row.get("run_status") != "completed":
            return False, "per_case_runtime_regression"
        for field in ("outcome", "reason"):
            if previous.get(field) != row.get(field) and not row.get(field + "_match"):
                return False, "per_case_unverified_" + field + "_change"
    return allowed, reason


def case_deltas(before, after):
    prior = {row["id"]: row for row in before["cases"]}
    fields = ("calls", "model_calls", "context_bytes", "context_total_bytes", "pass",
              "outcome", "reason", "claim_supported", "citation_valid", "bindings_verified",
              "workspace_valid", "safety_violations", "run_status")
    return [{"id": row["id"], "fields": {field: {"before": prior[row["id"]].get(field),
             "after": row.get(field)} for field in fields}} for row in after["cases"]]


def config_key(candidate):
    return candidate.query_mode, candidate.read_mode, candidate.limit


def experiment(args):
    fixtures = verify_fixtures()
    out = ROOT / "work/self-improvement-lab" / ("run-" + uuid4().hex[:12])
    out.mkdir(parents=True)
    protected = {str(path.relative_to(ROOT)): digest(path) for path in (ROOT / "src").rglob("*.py")}
    write(out / "protocol.json", {"base": "cd668f36300e5bfae7e8c654a54359dc40627d33",
          "budget": args.budget, "rounds": args.rounds, "fixtures": fixtures,
          "production_fingerprints": protected, "thresholds": {"max_cost_ratio": 1.10,
          "min_efficiency_gain": .10, "safety_violations": 0}, "no_production_activation": True})
    print("Experiment:", out, flush=True)
    before, raw = evaluate(BASELINE, "dev", out / "baseline/dev")
    report = {"schema_version": "self-improvement-experiment/v1", "before": before,
              "trials": [], "regression": None, "final": None, "selected": None,
              "generator_gold_access": False, "author_visible_synthetic_gold": True}
    baseline_validation = None
    survivors, seen, ids = [], {config_key(BASELINE)}, {BASELINE.id}
    signals = failure_signals(raw["cases"])
    for round_index in range(args.rounds):
        if len(report["trials"]) >= args.budget:
            break
        folder = out / f"round-{round_index + 1}"
        folder.mkdir()
        feedback = {"signals": signals, "tested_configs": [list(key) for key in sorted(seen)]}
        write(folder / "feedback.json", feedback)
        command = ["--feedback", folder / "feedback.json", "--output", folder / "proposals.json"]
        live = args.live_reflection and round_index == 1
        if live:
            command += ["--provider-root", ROOT / "work/self-improvement-lab"]
        try:
            child("scripts.self_improvement.generator", command, folder / "generator.log",
                  timeout=120, provider=live)
        except RuntimeError as error:
            report["generator_failure"] = str(error)
            break  # preserve completed offline trials and finish acceptance
        proposals = json.loads((folder / "proposals.json").read_text(encoding="utf-8"))
        new_count, round_gain = 0, False
        for value in proposals["candidates"]:
            candidate = Candidate.parse(value)
            if config_key(candidate) in seen or candidate.id in ids:
                write(folder / ("duplicate-" + uuid4().hex[:8] + ".json"),
                      {"candidate": value, "rejected": "duplicate_identity_or_config"})
                continue
            if len(report["trials"]) >= args.budget:
                break
            seen.add(config_key(candidate)); ids.add(candidate.id)
            new_count += 1
            trial = {"candidate": candidate.to_dict(), "generator": proposals["generator"],
                     "round": round_index + 1, "before": before}
            try:
                after, candidate_raw = evaluate(candidate, "dev", folder / candidate.id / "dev")
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                trial.update(eligible=False, development_gate="execution_failed", error=type(error).__name__)
                report["trials"].append(trial)
                continue
            trial["after"] = after
            trial["per_case_deltas"] = case_deltas(before, after)
            allowed, reason = compare(before, after)
            reductions = targeted_reductions(signals, candidate_raw["cases"], candidate)
            trial["targeted_failure_reductions"] = reductions
            if allowed and not reductions:
                allowed, reason = False, "no_targeted_failure_gain"
            dev_gain = allowed
            round_gain |= dev_gain
            trial["development_gate"] = reason
            if allowed and not args.development_only:
                if baseline_validation is None:
                    baseline_validation, _ = evaluate(BASELINE, "validation", out / "baseline/validation")
                try:
                    validation, _ = evaluate(candidate, "validation", folder / candidate.id / "validation")
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    trial.update(eligible=False, validation_gate="execution_failed", error=type(error).__name__)
                    report["trials"].append(trial)
                    continue
                allowed, reason = compare(baseline_validation, validation)
                trial["validation"] = {"before": baseline_validation, "after": validation, "gate": reason}
                if allowed:
                    survivors.append((candidate, validation["metrics"]))
            trial["eligible"] = allowed
            report["trials"].append(trial)
            print(candidate.id, trial["development_gate"], trial.get("validation", {}).get("gate", ""),
                  after["metrics"], flush=True)
            write(out / "progress.json" if not (out / "progress.json").exists() else
                  folder / f"{candidate.id}-trial.json", trial)
        # No new measured gain means there is no evidence to spend another round.
        if not new_count or not round_gain or args.development_only:
            break
        # A final Gemini reflection is permitted only after offline screening.
        if round_index >= 1 or not args.live_reflection:
            break
    if not args.development_only:
        winner = max(survivors, key=lambda pair: (pair[1]["accuracy"], -pair[1]["calls"],
                     -pair[1]["context_bytes"]))[0] if survivors else None
        # Final is read once, after all generation/selection has ended.
        write(out / "FINAL_STARTED.json", {"candidate": winner.id if winner else None})
        regression_candidate = out / "baseline/dev/candidate.json"
        try:
            baseline_final, _ = evaluate(BASELINE, "final", out / "baseline/final")
            report["final"] = {"before": baseline_final, "after": None, "gate": "no_finalist"}
            if winner:
                final, _ = evaluate(winner, "final", out / "winner/final")
                allowed, reason = compare(baseline_final, final)
                report["final"].update(after=final, gate=reason)
                if allowed:
                    regression_candidate = out / "winner/final/candidate.json"
                    report["selected"] = winner.to_dict()
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
            report["final"] = {"before": None, "after": None, "gate": "execution_failed",
                               "error": type(error).__name__}
            report["selected"] = None
        print("Final gate:", report["final"]["gate"], flush=True)
        # Exactly one full regression at the end; never after individual proposals.
        try:
            child("pytest", ["tests", "-q", "--continue-on-collection-errors",
                  "--basetemp=" + str(ROOT / "work" / ("si-t-" + uuid4().hex[:8])),
                  "--junitxml=" + str(out / "regression.xml")],
                  out / "regression.log", timeout=1800, environment={"LINKLOOM_LAB_CANDIDATE":
                  str(regression_candidate)})
            report["regression"] = {"passed": True, "xml_sha256": digest(out / "regression.xml")}
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            report["regression"] = {"passed": False, "error": type(error).__name__}
            report["selected"] = None
            report["rejection_stage"] = "full_regression"
    report["production_unchanged"] = all(digest(ROOT / name) == sha for name, sha in protected.items())
    if not report["production_unchanged"]:
        raise RuntimeError("Production source changed during experiment")
    verify_fixtures()
    from scripts.self_improvement.provider import accounting
    report["provider"] = accounting(ROOT / "work/self-improvement-lab")
    report["conclusion"] = "A" if report["selected"] else "B"
    if args.development_only:
        report["conclusion"] = "development_only_incomplete"
    write(out / "report.json", report)
    print(json.dumps({"report": str(out / "report.json"), "conclusion": report["conclusion"],
                      "provider": report["provider"]}), flush=True)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=int, default=3, help="total offline candidate evaluations, 1..18")
    parser.add_argument("--rounds", type=int, default=2, help="maximum proposal rounds, 1..6")
    parser.add_argument("--live-reflection", action="store_true", help="final Gemini proposal after offline screening")
    parser.add_argument("--development-only", action="store_true", help="debug DEV only; no final or full regression")
    args = parser.parse_args()
    if not 1 <= args.budget <= 18 or not 1 <= args.rounds <= 6:
        parser.error("budget must be 1..18 and rounds 1..6")
    if args.live_reflection and (args.budget < 4 or args.rounds < 2):
        parser.error("live reflection requires budget >=4 and rounds >=2")
    experiment(args)


if __name__ == "__main__":
    if "--protocol=v2" in sys.argv or ("--protocol" in sys.argv and
            sys.argv.index("--protocol") + 1 < len(sys.argv) and sys.argv[sys.argv.index("--protocol") + 1] == "v2"):
        from scripts.self_improvement.v2_run import main as v2_main
        v2_main()
    else:
        main()
