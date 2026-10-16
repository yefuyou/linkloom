"""Re-score saved synthetic outputs without spending another Provider request."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

from scripts.adaptive_live import ROOT, OUT, write_new
from scripts.adaptive_evaluation import score
from linkloom.runtime.team_decision_planner import plan_relation_investigation


def report(destination):
    fixtures = ROOT / "tests/fixtures/adaptive_planning"
    gold, public, partitions = {}, {}, {}
    for partition in ("dev", "validation", "heldout"):
        gold.update({row["id"]: row for row in json.loads((fixtures / f"gold_{partition}.json").read_text(encoding="utf8"))["cases"]})
        rows = json.loads((fixtures / f"public_{partition}.json").read_text(encoding="utf8"))["cases"]
        public.update({row["id"]: row for row in rows})
        partitions.update({row["id"]: partition for row in rows})
    results = []
    for path in sorted((OUT / "runs").glob("*/*-raw.json")):
        actual = json.loads(path.read_text(encoding="utf8"))
        identifier = actual["case_id"]
        if identifier not in gold:
            continue
        scored = score(actual, gold[identifier])
        original = actual.get("unified_result") or {}
        events = actual.get("observed_plans") or []
        plan = actual.get("plan")
        rule = plan_relation_investigation(public[identifier]["query"])
        invalid_tools = len(actual.get("invalid_tool_errors") or [])
        try:
            state = json.loads((Path(actual["artifact_path"]) / "runtime-state.json").read_text(encoding="utf8"))
            invalid_tools = max(invalid_tools, sum(row["status"] == "failed" for row in state.get("tool_ledger", [])))
        except (KeyError, OSError, ValueError):
            pass
        results.append({"case_id": identifier, "partition": partitions[identifier], "variant": actual["variant"],
            "trial": actual.get("trial", 1), "status": actual["status"], "score": scored,
            "baseline_rule_recognized": rule is not None,
            "model_planning": bool(events), "planning_rounds": actual.get("planning_rounds", 0),
            "observed_plans": events, "plan": plan, "facts": actual.get("facts", []),
            "reviewer": original.get("reviewer"), "grounding": actual.get("grounding"),
            "invalid_tools": invalid_tools,
            "model_turns": actual.get("model_turns", 0), "elapsed_ms": actual.get("elapsed_ms"),
            "provider_delta": (actual.get("provider_rounds") or {}).get("delta"),
            "cost_verifiable": (actual.get("provider_rounds") or {}).get("after") is not None,
            "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "artifact_relative_path": path.relative_to(ROOT).as_posix(),
            "error": original.get("error") or actual.get("error")})
    canary = OUT / "runs/dev-canary-b1/report.json"
    if canary.exists() and not any(row["case_id"] == "AP-D10" and row["variant"] == "B" for row in results):
        results.append({"case_id": "AP-D10", "partition": "dev", "variant": "B", "trial": 1,
            "status": "CAPTURE_FAILED", "score": {"passed": False, "fact_exact": False,
                "plan_valid": False, "expected_outcome": "answer", "outcome": "capture_error"},
            "baseline_rule_recognized": False, "model_planning": True, "planning_rounds": 1,
            "runtime_turns": 6, "model_turns": 6, "invalid_tools": 0, "elapsed_ms": None,
            "runtime_status": "success_reviewer_accepted", "transport_status": "final_not_preserved",
            "error": "Original Windows artifact copy error; excluded from complete HTTP successes",
            "artifact_relative_path": canary.relative_to(ROOT).as_posix()})
    # Rule-recognized B requests take exactly the same branch/plan/runtime as A.
    # Share that observation rather than paying to fabricate an independent trial.
    # Unrecognized A requests cannot start a model and are observed by offline HTTP.
    offline = []
    for path in (OUT / "offline-a/report.json", OUT / "offline-a-heldout/report.json"):
        if path.exists():
            offline.extend(json.loads(path.read_text(encoding="utf8")))
    paired_a = []
    by_b = {row["case_id"]: row for row in results if row["variant"] == "B" and row["trial"] == 1}
    by_offline = {row["actual"]["case_id"]: row["actual"] for row in offline}
    for identifier, case in public.items():
        b = by_b.get(identifier)
        if plan_relation_investigation(case["query"]) is not None and b:
            paired_a.append({"case_id": identifier, "basis": "shared_identical_rule_execution_with_B",
                             "status": b["status"], "score": b["score"], "elapsed_ms": b["elapsed_ms"]})
        elif identifier in by_offline:
            a = by_offline[identifier]
            paired_a.append({"case_id": identifier, "basis": "offline_HTTP_zero_provider_refusal",
                             "status": a["status"], "score": score(a, gold[identifier]), "elapsed_ms": a["elapsed_ms"]})
    ledger = [json.loads(line) for line in (OUT / "provider.jsonl").read_text(encoding="utf8").splitlines()]
    finished = [row for row in ledger if row["event"] == "request_finished"]
    groups = {}
    for variant in ("A", "B", "C"):
        selected = [row for row in results if row["variant"] == variant]
        groups[variant] = {"tasks": len(selected), "strict_pass": sum(bool(row["score"].get("passed")) for row in selected),
            "status": dict(Counter(row["status"] for row in selected)),
            "model_proposed_tasks": sum(row["model_planning"] for row in selected),
            "additional_completed_tasks": [row["case_id"] for row in selected
                if not row["baseline_rule_recognized"] and row["status"] == "COMPLETED"
                and row["score"].get("fact_exact") and row["score"].get("plan_valid")],
            "planning_rounds": sum(row["planning_rounds"] for row in selected),
            "runtime_turns": sum(row["model_turns"] for row in selected),
            "invalid_tools": sum(row["invalid_tools"] for row in selected),
            "median_elapsed_ms": statistics.median([row["elapsed_ms"] for row in selected if row.get("elapsed_ms") is not None]) if any(row.get("elapsed_ms") is not None for row in selected) else None}
    value = {"schema_version": "adaptive-planning-evidence/v1", "groups": groups, "cases": results,
        "paired_A": paired_a,
        "accounting": {"generation_requests": len([row for row in ledger if row["event"] == "request_reserved"]),
            "known_usage_usd": sum(row.get("actual_cost_usd") or 0 for row in finished),
            "budget_committed_usd": sum(row.get("actual_cost_usd") if row.get("actual_cost_usd") is not None
                else row["reservation_usd"] for row in finished),
            "strict_uncertain": sum(row.get("strict_uncertain", False) for row in finished),
            "first_request_utc": next(row["utc"] for row in ledger if row["event"] == "request_reserved"),
            "last_request_utc": finished[-1]["utc"],
            "ledger_sha256": hashlib.sha256((OUT / "provider.jsonl").read_bytes()).hexdigest()},
        "method": {"invalid_plans_never_model_success": True, "raw_ledgers_remain_local": True,
                   "first_trials_and_browser_repeats_separate": True, "gold_sent_to_provider": False}}
    write_new(destination, value)
    print(json.dumps({"groups": groups, "accounting": value["accounting"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    report(parser.parse_args().output)
