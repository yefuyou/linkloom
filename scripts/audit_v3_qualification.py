"""Read-only disposition of V3's exposed, unqualified evaluation records."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CATEGORIES = {
    "1": "legal input, demonstrated production failure",
    "2": "input violates TaskPlan/Product admission",
    "3": "runner did not execute the target action",
    "4": "observation insufficient",
    "5": "Gold and target disagree",
    "6": "missing target execution evidence",
}


def audit(root=ROOT):
    from scripts.evaluation_qualification import source_facts
    root = Path(root)
    folder = root / "docs/requirements/real-agent-contract-v3"
    evidence = json.loads((folder / "DATA_EVIDENCE.json").read_text(encoding="utf-8"))
    dispositions = {
        "D01": (["3", "5"], "Repeated malformed Final was scripted, not a successful correction. Gold business_status=complete despite rejected Final; only owner passage was read."),
        "D02": ([], "Observed valid completion, two facts and Reviewer. Historical pass retained."),
        "D03": (["3"], "UNVERIFIED_ABSTENTION identifies unsearched leader subgoal. Controller finalized after owner read; absence was not investigated."),
        "D04": (["2", "3"], "Single-owner question has no admitted multi-hop plan. Partial/conflict without validated subgoal coverage was correctly rejected (UNVERIFIED_ABSTENTION)."),
        "D05": (["3"], "One paragraph was read; Final claimed only owner. Gold requires two relations across separate passages. Runtime accepted only the grounded fact."),
        "D06": (["2", "3", "6"], "TaskPlan.goal differs from query. Runtime tool boundary was never reached; identity/dependency/executor guards were not executed."),
        "D07": (["3"], "Controller issued one read for a multi-passage file then Final. Second relation never became verified context."),
        "D08": (["4", "5"], "Context byte limit was not wired to Runtime; count limit is not byte accounting. Dropped-noise guard uses read-path absence, not ContextAssembler audit."),
        "D09": ([], "Final qualified evidence records actual MODEL_MAX_STEPS_EXCEEDED, Search+Read and no Final. Earlier provisional concerns are superseded by this saved trace."),
        "D10": (["4"], "Runtime unknown-outcome and one model invocation observed. Fake adapter has no paid Provider reservation ledger; append-only/cost guard is unobserved."),
        "F01": (["3", "4", "6"], "post_fix_provider_input_only has no scripted tools or foreign-workspace action. Empty malformed Finals cannot prove workspace isolation."),
        "F02": (["3", "4", "6"], "No source update or stale read was executed; repeated malformed Final is not freshness evidence."),
        "F03": (["3", "4", "6"], "No Search/Read or scoped Final; no observations for scope guards."),
        "F04": (["3", "4", "6"], "Unscripted mode generated empty Final, not the specified unknown-field/schema-boundary stimulus."),
        "F05": (["3", "4", "6"], "No owner read or missing-hop search. Partial target never reached."),
        "F06": (["2", "3", "4", "5", "6"], "No opposing reads. The registry-names-X-as-owner phrasing yields no supported typed facts in the existing claim grammar. Gold also mandates a context-only evidence path without exact fact spans. No authority inference was tested."),
        "F07": (["2", "3", "6"], "TaskPlan.goal differs from query; alternate/dependency/source-context boundary not reached."),
        "F08": (["3", "4", "6"], "No distinct reads or real context-byte selection occurred; context target unobserved."),
        "F09": (["3", "6"], "Unscripted empty Final fails format before step exhaustion; MODEL_SCHEMA_CORRECTION_EXHAUSTED is not a budget-boundary witness."),
        "F10": (["2", "3", "6"], "TaskPlan.goal differs from query; provider_outcome=not_scripted never injects uncertain response. No unknown-outcome target evidence."),
    }
    cases = []
    for partition in ("dev", "final"):
        public = json.loads((root / f"tests/fixtures/real_agent_contract_v3/{partition}.json").read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in public["cases"]}
        for row in evidence[partition]["cases"]:
            identifier = row["id"]; case = by_id[identifier]
            categories, reason = dispositions[identifier.removeprefix("RAC3-")]
            result = row["results"]
            error = result.get("error") or result.get("payload", {}).get("error")
            guards = row["score"].get("guards", {})
            cases.append({"id": identifier, "categories": categories, "disposition": reason,
                "production_failure_demonstrated": False, "production_failure_note": "No attributable production regression established by this case; lack of proof is not a pass.",
                "historical_score": row["score"].get("qualification"), "historical_passed": row["score"]["passed"],
                "error": error, "actual": row["score"].get("actual"),
                "input_signals": {"goal_matches_query": case["input"].get("task_plan", {}).get("goal", case["input"]["query"]) == case["input"]["query"],
                                  "stimulus_mode": case["stimulus"].get("mode"),
                                  "scripted_tool_calls": len(case["stimulus"].get("tool_calls", [])),
                                  "parsed_source_facts": source_facts(case["input"]["documents"])},
                "saved_trace": row.get("trace"), "saved_verified_reads": row.get("verified_reads"),
                "unobserved_required_guards": [g for g in case["gold"]["required_guards"] if guards.get(g) == "not_observed" or g not in guards],
                "data_pointer": f"DATA_EVIDENCE.json#/{partition}/cases/{identifier}",
                "old_case_sha256": hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
    protected = list(folder.glob("*")) + list((root / "tests/fixtures/real_agent_contract_v3").glob("*")) + [root / "scripts/contract_v3_cases.py"]
    return {"schema_version": "v3-qualification-disposition/v1", "categories": CATEGORIES,
            "old_final_identity": "EXPOSED; not blind; not rerun by this audit", "cases": cases,
            "old_artifact_sha256": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()},
            "old_scores_modified": False, "model_calls": 0,
            "limits": "Saved traces cannot establish unexecuted product behavior. Category 1 requires a legal, target-reaching reproducible failure; none demonstrated here."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("20 exposed V3 cases dispositioned; no model or old-file writes")


if __name__ == "__main__":
    main()
