"""Explicit paid V2 stage; immutable proposals and serial actual Runtime trials."""
import json
from pathlib import Path

from scripts.self_improvement.v2_contracts import write, parse_candidate, sha, BASE_COMMIT, validate_feedback
from scripts.self_improvement.v2_provider import V2Provider, MODEL


def verify_dataset(dataset, identity):
    if sha(dataset/"freeze.json") != identity["freeze_sha256"]:
        raise ValueError("Live dataset freeze mismatch")
    freeze = json.loads((dataset/"freeze.json").read_text(encoding="utf-8"))
    for name,digest in freeze["files"].items():
        if sha(dataset/name) != digest: raise ValueError("Live frozen input changed")


def verify_transfer_report(out,identity):
    report = out/"REAL_TRANSFER_REPORT.json"
    seal = json.loads((out/"REAL_TRANSFER_REPORT_SEAL.json").read_text(encoding="utf-8"))
    value = json.loads(report.read_text(encoding="utf-8"))
    if seal != {"identity":identity,"sha256":sha(report)} or value["identity"] != identity or value["live_qualified"] is not False:
        raise ValueError("Real transfer report chain mismatch")
    return value


def verify_prelive(out, identity):
    marker = json.loads((out/"PRELIVE_REGRESSION.json").read_text(encoding="utf-8"))
    report = Path(marker["report"])
    if (marker["experiment_id"] != identity["experiment_id"] or
            marker["freeze_sha256"] != identity["freeze_sha256"] or sha(report) != marker["report_sha256"]):
        raise ValueError("Prelive regression identity mismatch")
    result = json.loads(report.read_text(encoding="utf-8"))
    if result["base_commit"] != BASE_COMMIT or not result.get("stages"):
        raise ValueError("Missing actual paired base regression evidence")
    report.resolve().relative_to(out.resolve())
    core = result["stages"].get("Core Regression",{})
    if not core.get("pairs") or any(core.get(mode,{}).get("timed_out",True) for mode in ("baseline","candidate")):
        raise ValueError("Actual paired Core execution incomplete before paid diagnostics")


def prepare_prelive(out, identity):
    if (out/"PRELIVE_REGRESSION.json").exists():
        verify_prelive(out,identity); return
    from scripts.self_improvement.v2_regression import run_regression
    from scripts.self_improvement.v2_contracts import V1_ROUTING
    root = Path(__file__).resolve().parents[2]
    path = out/"prelive-regression/report.json"
    if not path.exists():
        path = run_regression(root,candidate_config=V1_ROUTING.to_dict(),output_root=out/"prelive-regression",run_full_collect=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["base_commit"] != BASE_COMMIT or value["candidate"]["config"]["query_mode"] != V1_ROUTING.query_mode:
        raise ValueError("Prelive regression candidate identity mismatch")
    write(out/"PRELIVE_REGRESSION.json",{**identity,"report":str(path.resolve()),"report_sha256":sha(path),
        "scope":"Host query seam only; controller read_mode and limit are not exercised by the regression plugin"})
    verify_prelive(out,identity)


def proposals(out, identity, feedback, maximum):
    path = out/"GEMINI_PROPOSALS.json"
    if (out/"FINAL_CLAIM.json").exists() or (out/"CANDIDATE_MANIFEST.json").exists():
        raise RuntimeError("Final exposed: candidate generation is permanently closed")
    validate_feedback(feedback,experiment_id=identity["experiment_id"],freeze_sha256=identity["freeze_sha256"])
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["identity"] != identity or value["maximum"] != maximum or len(value["candidates"]) > maximum:
            raise ValueError("Cached proposal identity or budget mismatch")
        verify_prelive(out,identity)
        return [parse_candidate(c).to_dict() for c in value["candidates"]]
    if maximum == 0: return []
    if maximum not in {1,2,3}: raise ValueError("Invalid total candidate allowance")
    verify_prelive(out,identity)
    if (out/"PROPOSAL_REJECTED.json").exists(): return []
    write(out/"PROPOSAL_ATTEMPT.json",{"experiment_id":identity["experiment_id"],"maximum":maximum})
    provider = V2Provider(out/"provider", experiment_id=identity["experiment_id"],
        freeze_sha256=identity["freeze_sha256"], permission=True)
    try:
        compatibility = provider.generate_json('Return exactly {"compatible":true}.',
            case_id="compatibility",purpose="compatibility")
        if compatibility != {"compatible":True}: raise RuntimeError("Compatibility canary failed")
        prompt = ("Propose at most "+str(maximum)+" bounded retrieval configurations using DEV symptoms. "
            "Return JSON {candidates:[...]}. Each candidate has exactly id (lowercase slug), change "
            "(concrete description), query_mode (literal or relation_terms), read_mode "
            "(all, relation_candidates or first_hit), limit (2,4,6). Prefer preserve every conflict "
            "source. No source edits, policy edits or code. Avoid already tested configurations. "
            "These signals contain no answers; missing evidence is a symptom, not proven recall loss.\n"
            + json.dumps(feedback,ensure_ascii=False))
        response = provider.generate_json(prompt,case_id="proposal-round-1",purpose="dev_proposal")
        if set(response) != {"candidates"} or not isinstance(response["candidates"],list) or len(response["candidates"]) > maximum:
            raise ValueError("Invalid proposal envelope")
        candidates = [parse_candidate(value).to_dict() for value in response["candidates"]]
        write(path,{"candidates":candidates,"identity":identity,"maximum":maximum,"model":MODEL,"provider":provider.summary()})
        return candidates
    except Exception as error:
        write(out/"PROPOSAL_REJECTED.json",{"error_type":type(error).__name__,"reason":str(error),
            "candidates":[],"provider":provider.summary(),"automatic_retry":False})
        return []
    finally:
        write(out/("PROVIDER_PROPOSAL_SUMMARY-"+__import__('uuid').uuid4().hex[:6]+".json"),provider.summary())
        provider.close()


def transfer(out, identity, cases, candidates, dataset):
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from scripts.self_improvement.v2_worker import execute
    from scripts.self_improvement.run import child
    provider = V2Provider(out/"provider",experiment_id=identity["experiment_id"],
        freeze_sha256=identity["freeze_sha256"],permission=True)
    rows = []
    try:
        for case in cases:
            for candidate in candidates:
                if case["id"] in provider.summary()["blocked_cases"]:
                    raise RuntimeError("Uncertain or halted case cannot be continued with another strategy")
                trial_id = case["id"]+"-"+candidate.id
                path = out/"real"/(trial_id+".json")
                if path.exists():
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row["id"] != case["id"] or row["candidate_id"] != candidate.id:
                        raise ValueError("Real trial identity mismatch")
                else:
                    if path.with_suffix(".attempt.json").exists():
                        raise RuntimeError("LIVE_INCOMPLETE: prior trial has no sealed row; paid replay prohibited")
                    write(path.with_suffix(".attempt.json"),{**identity,"case_id":case["id"],"candidate":candidate.to_dict()})
                    provider.case_id, provider.purpose = case["id"],"real_agent_ab/"+candidate.id
                    before = provider.summary()["generation_requests"]
                    row = execute(case,candidate,model=GeminiProviderAdapter(provider,model_id=MODEL),real=True)
                    row["real_provider_requests"] = provider.summary()["generation_requests"]-before
                    diagnoses = [json.loads(p.read_text(encoding="utf-8")) for p in (out/"provider/calls").glob("*/outcome.json")]
                    diagnoses = [d for d in diagnoses if d["case_id"] == case["id"] and d["purpose"] == provider.purpose]
                    row["provider_diagnoses"] = diagnoses
                    row["provider_classification"] = ("PROVIDER_OUTCOME_UNCERTAIN" if any(d["strict_uncertain"] for d in diagnoses)
                        else "PROVIDER_KNOWN_FAILURE" if any(d.get("classification") for d in diagnoses)
                        else "RESPONSE_INTEGRITY_FAILURE" if any(d["response_received"] and not d["response_accepted"] for d in diagnoses)
                        else "NO_PROVIDER_FAILURE")
                    write(path,row)
                rows.append(row)
                raw = path.with_suffix(".envelope.json")
                scored = path.with_suffix(".scored.json")
                envelope = {"partition":"dev","candidate":candidate.to_dict(),"cases":[row]}
                if not raw.exists(): write(raw,envelope)
                elif json.loads(raw.read_text(encoding="utf-8")) != envelope: raise ValueError("Real raw chain changed")
                if not scored.exists():
                    child("scripts.self_improvement.v2_scorer",["--raw",raw,"--gold",dataset/"dev_gold.json",
                        "--catalog",dataset/"catalog.json","--output",scored,"--subset"],path.with_suffix(".score.log"))
                seal = {"raw_sha256":sha(raw),"score_sha256":sha(scored),"scorer_sha256":sha(Path(__file__).with_name("v2_scorer.py"))}
                seal_path = path.with_suffix(".seal.json")
                if seal_path.exists():
                    if json.loads(seal_path.read_text(encoding="utf-8")) != seal: raise ValueError("Real score chain changed")
                else: write(seal_path,seal)
                if not (out/"provider/CANARY_PASS.json").exists():
                    score = json.loads(scored.read_text(encoding="utf-8"))
                    if not score["cases"][0]["passed"] or score["metrics"]["safety_violations"]:
                        raise RuntimeError("Real Runtime canary failed; stop paid stage")
                    write(out/"provider/CANARY_PASS.json",{**identity,"batch_id":provider.batch["batch_id"],
                        "raw_path":str(raw.resolve()),"raw_sha256":sha(raw),"score_path":str(scored.resolve()),
                        "score_sha256":sha(scored),"request_count":provider.summary()["generation_requests"],
                        "request_ids":[value["request_id"] for ledger in (out/"provider/calls").glob("*/provider_ledger.jsonl")
                            for value in map(json.loads,ledger.read_text(encoding="utf-8").splitlines()) if value["event"] == "request_reserved"]})
        return rows
    finally:
        provider.close()


def main():
    import argparse
    import os
    import sys
    from scripts.self_improvement.v2_contracts import BASELINE, V1_ROUTING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment",type=Path,required=True)
    parser.add_argument("--dataset",type=Path,required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root/"src"),str(root/"tests/integration")]
    os.environ["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
    identity = json.loads((args.experiment/"IDENTITY.json").read_text(encoding="utf-8"))
    verify_dataset(args.dataset,identity)
    report_path = args.experiment/"REAL_TRANSFER_REPORT.json"
    if report_path.exists():
        verify_transfer_report(args.experiment,identity)
        return
    if (args.experiment/"LIVE_STOP.json").exists():
        raise RuntimeError("Previous live stage is terminal; automatic paid retry prohibited")
    feedback_path = args.experiment/"DEV_MODEL_FEEDBACK.json"
    if not feedback_path.exists(): feedback_path = args.experiment/"DEV_FEEDBACK.json"
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    prepare_prelive(args.experiment,identity)
    budget_path = args.experiment/"GENERATION_BUDGET.json"
    total = json.loads(budget_path.read_text(encoding="utf-8"))["maximum_generated_candidates"] if budget_path.exists() else 3
    maximum = total-max(0,len(feedback["tested_configs"])-3)
    proposals(args.experiment,identity,feedback,maximum)
    if (args.experiment/"PROPOSAL_REJECTED.json").exists():
        raise RuntimeError("Compatibility/proposal stage failed; paid stage stopped")
    public = json.loads((args.dataset/"dev_public.json").read_text(encoding="utf-8"))
    # Preregistered normal/ranking/same-file/conflict/missing/language trials.
    ids = ["v2-dev-003","v2-dev-018","v2-dev-014","v2-dev-024","v2-dev-021","v2-dev-011"]
    cases = {case["id"]:case for case in public["cases"]}
    try:
        transfer(args.experiment,identity,[cases[id] for id in ids],[BASELINE,V1_ROUTING],args.dataset)
    except Exception as error:
        write(args.experiment/"LIVE_STOP.json",{"error_type":type(error).__name__,"reason":str(error),"automatic_retry":False})
    finally:
        from scripts.self_improvement.v2_provider import accounting
        from scripts.self_improvement.v2_scorer import aggregate
        from scripts.self_improvement.v2_run import compare
        by_candidate = {}
        for scored in (args.experiment/"real").glob("*.scored.json"):
            value = json.loads(scored.read_text(encoding="utf-8"))
            trial = json.loads(scored.with_name(scored.name.replace(".scored.json",".json")).read_text(encoding="utf-8"))
            value["cases"][0]["provider_classification"] = trial.get("provider_classification","NO_PROVIDER_FAILURE")
            by_candidate.setdefault(value["candidate"]["id"],[]).extend(value["cases"])
        reports = {name:{"cases":sorted(rows,key=lambda row:row["id"]),"metrics":aggregate(rows)} for name,rows in by_candidate.items()}
        paired = None
        if all(name in reports for name in ("baseline","v1-routing")) and all(len(reports[name]["cases"]) == 6 for name in ("baseline","v1-routing")):
            paired = compare(reports["baseline"],reports["v1-routing"])
        batch = json.loads((args.experiment/"provider/BATCH.json").read_text(encoding="utf-8"))
        usage = accounting(args.experiment/"provider",batch["batch_id"])
        receipts = [row.get("usage") or {} for ledger in (args.experiment/"provider/calls").glob("*/provider_ledger.jsonl")
            for row in map(json.loads,ledger.read_text(encoding="utf-8").splitlines()) if row["event"] == "request_finished"]
        usage["reported_tokens"] = {field:sum(row[field] for row in receipts if isinstance(row.get(field),int))
            for field in ("prompt_tokens","output_tokens","thinking_tokens","total_tokens")}
        usage["missing_token_fields"] = {field:sum(not isinstance(row.get(field),int) for row in receipts)
            for field in ("prompt_tokens","output_tokens","thinking_tokens","total_tokens")}
        write(args.experiment/"REAL_TRANSFER_REPORT.json",{"identity":identity,"reports":reports,"paired":paired,"provider":usage,
            "live_qualified":False,
            "qualification_reason":"V1 controller read filtering is not enforced in real Runtime; A/B measures host query plus voluntary advice only",
            "transfer_boundary":"host query plus voluntary reading advice; Fake hard filtering does not transfer"})
        write(args.experiment/"REAL_TRANSFER_REPORT_SEAL.json",{"identity":identity,"sha256":sha(args.experiment/"REAL_TRANSFER_REPORT.json")})


if __name__ == "__main__": main()
