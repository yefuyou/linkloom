"""Serial V2 experiment orchestration; workers never receive Gold paths."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

from scripts.self_improvement.run import ROOT, child
sys.path[:0] = [str(ROOT/"src"),str(ROOT/"tests/integration")]
from scripts.self_improvement.contracts import BASELINE, failure_signals
from scripts.self_improvement.v2_contracts import (V1_ROUTING, NARROW_FIRST, sha, write,
    parse_candidate, key, validate_feedback, claim_final, admission)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def regression_applies(report,candidate):
    tested = parse_candidate(report["candidate"]["config"])
    return key(tested) == key(candidate) or (
        candidate.read_mode == "all" and candidate.limit == BASELINE.limit and
        tested.query_mode == candidate.query_mode)


def compare(before, after):
    old = {row["id"]: row for row in before["cases"]}
    regressions = [row["id"] for row in after["cases"] if old[row["id"]]["passed"] and not row["passed"]]
    for row in after["cases"]:
        previous = old[row["id"]]
        if any(previous.get(field) is True and row.get(field) is not True
               for field in ("conflict_correct","upstream_final_preserved")) and row["id"] not in regressions:
            regressions.append(row["id"])
    a, b = before["metrics"], after["metrics"]
    costs = all(b[field] <= a[field]*1.1 for field in ("calls", "context_total_bytes"))
    improved = b["correct"] > a["correct"] or b["calls"] < a["calls"]*.9 or b["context_total_bytes"] < a["context_total_bytes"]*.9
    return {"pass": not regressions and not b["safety_violations"] and costs,
        "improved": improved, "regressions": regressions,
        "paired": [{"id": row["id"], **{field: row[field]-old[row["id"]][field]
                    for field in ("calls", "reads", "context_total_bytes", "elapsed_ms")}} for row in after["cases"]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", default="v2", choices=["v2"])
    parser.add_argument("--dataset", type=Path, default=ROOT/"tests/fixtures/self_improvement_v2")
    parser.add_argument("--qualification",type=Path,default=ROOT/"docs/requirements/self-improvement-hardening/DATASET_QUALIFICATION.json")
    parser.add_argument("--scoring-protocol",type=Path,default=ROOT/"docs/requirements/self-improvement-hardening/SCORING_PROTOCOL.json")
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--max-candidates", type=int, default=3, choices=range(1,4))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--skip-regression", action="store_true", help="Leave engineering gate incomplete")
    parser.add_argument("--development-only", action="store_true")
    parser.add_argument("--screen-only", action="store_true")
    args = parser.parse_args(argv)
    data = args.dataset.resolve()
    freeze = load(data/"freeze.json")
    qualification = load(args.qualification)
    protocol = load(args.scoring_protocol)
    if protocol["scorer_sha256"] != sha(ROOT/"scripts/self_improvement/v2_scorer.py") or protocol["freeze_sha256"] != sha(data/"freeze.json"):
        raise ValueError("Scoring protocol changed after preregistration")
    if qualification["freeze_sha256"] != sha(data/"freeze.json"):
        raise ValueError("Dataset qualification identity mismatch")
    for name, digest in freeze["files"].items():
        if sha(data/name) != digest:
            raise ValueError("Frozen input changed: "+name)
    out = args.experiment or ROOT/"work"/("self-improvement-v2-"+uuid4().hex[:12])
    out.mkdir(parents=True, exist_ok=True)
    identity = {"experiment_id": out.name, "freeze_sha256": sha(data/"freeze.json"), "protocol": "v2","scoring_protocol_sha256":sha(args.scoring_protocol)}
    if (out/"IDENTITY.json").exists():
        if load(out/"IDENTITY.json") != identity:
            raise ValueError("Resume identity mismatch")
    else:
        write(out/"IDENTITY.json", identity)
    generation_budget = {"maximum_generated_candidates":args.max_candidates}
    budget_path = out/"GENERATION_BUDGET.json"
    if budget_path.exists():
        if load(budget_path) != generation_budget: raise ValueError("Candidate budget changed on resume")
    else: write(budget_path,generation_budget)
    closed = (out/"CANDIDATE_MANIFEST.json").exists() or (out/"FINAL_CLAIM.json").exists()
    published_exposure = ROOT/"docs/requirements/self-improvement-hardening/FINAL_EXPOSURE.json"
    if published_exposure.exists() and not (args.development_only or args.screen_only):
        exposure = load(published_exposure)
        if exposure["freeze_sha256"] == identity["freeze_sha256"] and not (
                closed and (out/"REPORT.json").exists() and sha(out/"REPORT.json") == exposure["report_sha256"]):
            raise ValueError("Committed Final is exposed; use --screen-only or a newly qualified freeze")
    prior_final = ROOT/"work"/("V2_FINAL_"+identity["freeze_sha256"]+".json")
    if prior_final.exists() and not (args.development_only or args.screen_only):
        prior = load(prior_final)
        if prior["experiment_id"] != out.name or prior.get("experiment_root") != str(out.resolve()):
            raise ValueError("Frozen Final already exposed; only DEV/Validation diagnostics remain available")
    def evaluate(candidate, split):
        folder = out/candidate.id/split
        folder.mkdir(parents=True, exist_ok=True)
        config = folder/"candidate.json"
        if not config.exists(): write(config, candidate.to_dict())
        elif load(config) != candidate.to_dict(): raise ValueError("Candidate identity reuse")
        raw, scored = folder/"raw.json", folder/"scored-v3.json"
        if not raw.exists():
            if split == "final":
                if (folder/"FINAL_ATTEMPT.json").exists():
                    raise RuntimeError("FINAL_INCOMPLETE: previous attempt has no sealed raw output; automatic replay prohibited")
                write(folder/"FINAL_ATTEMPT.json",{**identity,"candidate":candidate.id})
            child("scripts.self_improvement.v2_worker", ["--public",data/(split+"_public.json"),"--candidate",config,"--output",raw], folder/"worker.log", timeout=1200)
        raw_value = load(raw)
        if raw_value["candidate"] != candidate.to_dict() or raw_value["partition"] != split:
            raise ValueError("Raw result candidate or partition mismatch")
        seal = {**identity,"raw_sha256":sha(raw),"public_sha256":sha(data/(split+"_public.json")),"candidate":candidate.to_dict()}
        seal_path = folder/"RAW_SEAL.json"
        if seal_path.exists():
            if load(seal_path) != seal: raise ValueError("Raw evidence chain changed")
        else: write(seal_path,seal)
        if not scored.exists():
            child("scripts.self_improvement.v2_scorer", ["--raw",raw,"--gold",data/(split+"_gold.json"),"--catalog",data/"catalog.json","--output",scored],folder/"scorer.log")
        score_seal = {"raw_sha256":sha(raw),"score_sha256":sha(scored),"scorer_sha256":sha(ROOT/"scripts/self_improvement/v2_scorer.py")}
        score_seal_path = folder/"SCORE_V3_SEAL.json"
        score_value = load(scored)
        if score_value["candidate"] != candidate.to_dict() or score_value["partition"] != split or not isinstance(score_value.get("cases"),list):
            raise ValueError("Scored result identity or schema mismatch")
        if score_seal_path.exists():
            if load(score_seal_path) != score_seal: raise ValueError("Score evidence chain changed")
        else: write(score_seal_path,score_seal)
        return load(scored)
    candidates = [BASELINE, V1_ROUTING, NARROW_FIRST]
    if closed:
        manifest = load(out/"CANDIDATE_MANIFEST.json")
        if manifest["identity"] != identity: raise ValueError("Closed candidate manifest identity mismatch")
        if (out/"FINAL_CLAIM.json").exists() and load(out/"FINAL_CLAIM.json")["manifest_sha256"] != sha(out/"CANDIDATE_MANIFEST.json"):
            raise ValueError("Sealed candidate manifest changed after Final")
        candidates = [parse_candidate(c) for c in manifest["candidates"]]
    results = {c.id:{"dev":evaluate(c,"dev")} for c in candidates}
    feedback = {"partition":"development", **{k:identity[k] for k in ("experiment_id","freeze_sha256")},
        "signals": failure_signals(load(out/"baseline/dev/raw.json")["cases"]),
        "tested_configs":[c.to_dict() for c in candidates]}
    validate_feedback(feedback, experiment_id=out.name, freeze_sha256=identity["freeze_sha256"])
    if not (out/"DEV_FEEDBACK.json").exists(): write(out/"DEV_FEEDBACK.json",feedback)
    from scripts.self_improvement.generator import propose
    finite = [] if closed else propose({"signals":feedback["signals"],"tested_configs":feedback["tested_configs"]})[:args.max_candidates]
    seen = {key(c) for c in candidates}
    for value in finite:
        candidate = parse_candidate(value)
        if key(candidate) in seen: continue
        seen.add(key(candidate)); candidates.append(candidate)
        results[candidate.id] = {"dev":evaluate(candidate,"dev")}
    feedback["tested_configs"] = [c.to_dict() for c in candidates]
    feedback["signals"] = [{**signal,"strategy_id":c.id} for c in candidates
        for signal in failure_signals(load(out/c.id/"dev/raw.json")["cases"])]
    if not (out/"DEV_MODEL_FEEDBACK.json").exists(): write(out/"DEV_MODEL_FEEDBACK.json",feedback)
    if args.live and not closed:
        from scripts.self_improvement.v2_live import proposals, prepare_prelive
        prepare_prelive(out,identity)
        proposed = proposals(out, identity, feedback, args.max_candidates-(len(candidates)-3))
        for proposal in proposed:
            candidate = parse_candidate(proposal)
            if key(candidate) in seen: continue
            if candidate.id in {c.id for c in candidates}:
                rejection = out/("REJECTED_ID_COLLISION_"+candidate.id+".json")
                if not rejection.exists(): write(rejection,{"candidate":candidate.to_dict(),"reason":"existing candidate id cannot be reused"})
                continue
            seen.add(key(candidate)); candidates.append(candidate)
            results[candidate.id] = {"dev":evaluate(candidate,"dev")}
    if args.development_only:
        print(out); return
    for c in candidates:
        for split in ("validation","stress"):
            results[c.id][split] = evaluate(c,split)
    if args.screen_only:
        print(out); return
    selected = [c for c in candidates[1:] if all(compare(results[BASELINE.id][s],results[c.id][s])["pass"] for s in ("dev","validation","stress"))]
    # Fixed controls are preregistered diagnostics; no proposal or repair follows Final.
    claim = out/"FINAL_CLAIM.json"
    final_ids = [c.id for c in candidates]
    candidate_manifest = {"identity":identity,"candidates":[c.to_dict() for c in candidates],"total_generated_limit":args.max_candidates}
    if not (out/"CANDIDATE_MANIFEST.json").exists(): write(out/"CANDIDATE_MANIFEST.json",candidate_manifest)
    elif load(out/"CANDIDATE_MANIFEST.json") != candidate_manifest: raise ValueError("Candidate manifest changed")
    global_claim = ROOT/"work"/("V2_FINAL_"+identity["freeze_sha256"]+".json")
    if global_claim.exists():
        if (load(global_claim)["experiment_id"] != out.name or
                load(global_claim).get("experiment_root") != str(out.resolve())):
            raise ValueError("This frozen Final has already been exposed by another experiment")
    else:
        write(global_claim,{**identity,"experiment_root":str(out.resolve()),"candidates":final_ids,"manifest_sha256":sha(out/"CANDIDATE_MANIFEST.json")})
    if load(global_claim)["manifest_sha256"] != sha(out/"CANDIDATE_MANIFEST.json"):
        raise ValueError("Global Final manifest mismatch")
    if not claim.exists(): claim_final(out,out.name,identity["freeze_sha256"],final_ids,sha(out/"CANDIDATE_MANIFEST.json"))
    elif load(claim)["candidates"] != final_ids: raise ValueError("Final selection changed")
    for c in candidates: results[c.id]["final"] = evaluate(c,"final")
    regression = None
    regression_candidate = None
    if not args.skip_regression:
        from scripts.self_improvement.v2_regression import run_regression
        target = selected[0] if selected else V1_ROUTING
        regression_candidate = target.id
        regression_path = out/"regression/report.json"
        complete = out/"complete-source-regression/report.json"
        prelive = out/"prelive-regression/report.json"
        if complete.exists() and regression_applies(load(complete),target):
            regression_path = complete
        elif prelive.exists() and regression_applies(load(prelive),target):
            regression_path = prelive
        if regression_path.exists():
            regression_report = load(regression_path)
            if regression_report["base_commit"] != "92c0fd282f8c33dfe29471aa70b9d7cfbf6e1d75": raise ValueError("Old regression identity")
            if not regression_applies(regression_report,target): raise ValueError("Regression candidate policy mismatch")
            regression = str(regression_path)
        else:
            regression = str(run_regression(ROOT,candidate_config=target.to_dict(), output_root=out/"regression",run_full_collect=True))
    decisions = {}
    for c in candidates[1:]:
        comparisons = {s:compare(results[BASELINE.id][s],results[c.id][s]) for s in ("dev","validation","stress","final")}
        gates = {"validation_pass":all(comparisons[s]["pass"] for s in ("dev","validation","stress")),
            "final_pass":comparisons["final"]["pass"],"improved":comparisons["validation"]["improved"] and comparisons["final"]["improved"],
            "core_complete":False,"protected_violations":sum(v["metrics"]["safety_violations"] for v in results[c.id].values()),
            "negative_regressions":[case for value in comparisons.values() for case in value["regressions"]],"live_qualified":False}
        gates["dataset_qualified"] = qualification["qualified"] is True
        live_report = out/"REAL_TRANSFER_REPORT.json"
        if live_report.exists() and c.id == V1_ROUTING.id:
            from scripts.self_improvement.v2_live import verify_transfer_report
            verify_transfer_report(out,identity)
            gates["live_qualified"] = False  # V1 hard read policy is not enforced in real Runtime.
        if regression and regression_candidate == c.id:
            regression_report = load(regression)
            gates["core_complete"] = regression_report["core_complete"]
            gates["core_new_failures"] = regression_report["core_new_failures"]
        decisions[c.id] = {"gates":gates,"decision":admission(gates),"comparisons":comparisons}
    rank = {"REJECTED":0,"EXPERIMENTAL_ACCEPTED":1,"INTEGRATION_CANDIDATE":2}
    engineering = max((v["decision"]["level"] for v in decisions.values()),key=rank.get,default="REJECTED")
    report = {**identity,"results":results,"decisions":decisions,"regression":regression,"production_activation":False,"engineering_decision":engineering,
        "regression_scope":"Host query factory seam. Evidence also applies to same-query candidates whose read_mode=all and limit=baseline; controller policy changes are assessed offline separately."}
    if not (out/"REPORT.json").exists(): write(out/"REPORT.json",report)
    elif load(out/"REPORT.json") != report: raise ValueError("Sealed report differs on resume")
    print(out)


if __name__ == "__main__": main()
