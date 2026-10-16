"""Export processed metrics and witnesses, never Provider payloads or Gold."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from scripts.self_improvement.v2_contracts import BASE_COMMIT,sha,write
from scripts.self_improvement.v2_scorer import aggregate


def load(path): return json.loads(path.read_text(encoding="utf-8"))


def real_pairs(experiment,real):
    if not real or not all(name in real["reports"] for name in ("baseline","v1-routing")): return None
    reports = real["reports"]
    ids = set(row["id"] for row in reports["baseline"]["cases"]) & set(row["id"] for row in reports["v1-routing"]["cases"])
    result = {"paired_ids":sorted(ids),"expected_pairs":6,"complete_pairs":len(ids),"not_executed_is_not_failure":True,"strategies":{}}
    for candidate in ("baseline","v1-routing"):
        rows = [row for row in reports[candidate]["cases"] if row["id"] in ids]
        usage = defaultdict(float)
        per_case = defaultdict(lambda:defaultdict(float))
        for outcome_path in (experiment/"provider/calls").glob("*/outcome.json"):
            outcome = load(outcome_path)
            if outcome["case_id"] not in ids or outcome["purpose"] != "real_agent_ab/"+candidate: continue
            ledger = [json.loads(line) for line in outcome_path.with_name("provider_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
            for receipt in (r for r in ledger if r["event"] == "request_finished"):
                fields = {**(receipt.get("usage") or {}),"provider_elapsed_ms":receipt.get("operation_elapsed_ms")}
                for field in ("prompt_tokens","output_tokens","thinking_tokens","total_tokens","estimated_cost_usd","provider_elapsed_ms"):
                    if isinstance(fields.get(field),(int,float)):
                        usage[field] += fields[field]; per_case[outcome["case_id"]][field] += fields[field]
        result["strategies"][candidate] = {"metrics":aggregate(rows),"usage_reported":dict(usage),"usage_by_case":dict(per_case)}
    from scripts.self_improvement.v2_run import compare
    result["comparison"] = compare({"cases":[r for r in reports["baseline"]["cases"] if r["id"] in ids],
        "metrics":result["strategies"]["baseline"]["metrics"]},
        {"cases":[r for r in reports["v1-routing"]["cases"] if r["id"] in ids],"metrics":result["strategies"]["v1-routing"]["metrics"]})
    return result


def export(experiment,output):
    root = Path(__file__).resolve().parents[2]
    experiment = experiment.resolve()
    report = load(experiment/"REPORT.json")
    catalog = {row["id"]:row for row in load(root/"tests/fixtures/self_improvement_v2/catalog.json")["cases"]}
    tags = {}
    for candidate,partitions in report["results"].items():
        groups = defaultdict(list)
        for partition,value in partitions.items():
            for row in value["cases"]:
                for tag in catalog[row["id"]]["tags"]: groups[(partition,tag)].append(row)
        tags[candidate] = {partition:{tag:aggregate(rows) for (part,tag),rows in groups.items() if part == partition}
            for partition in partitions}
    witnesses = []
    requests = [("baseline","dev","v2-dev-001","extra reads exhaust the unchanged coordinator budget"),
        ("v1-routing","dev","v2-dev-011","Complete verified evidence is read, but controller routing/consumption ends in WRONG_ABSTENTION"),
        ("v1-routing","dev","v2-dev-012","Target evidence enters search candidates but only half is verified-read"),
        ("narrow-first","dev","v2-dev-024","first-hit reading misses a conflicting source"),
        ("baseline","dev","v2-dev-007","ranking interference leaves the last hop unresolved"),
        ("baseline","stress","v2-stress-001","source revision is rejected by the original loader"),
        ("baseline","dev","v2-dev-031","planner unsupported is not retrieval failure")]
    for candidate,partition,case_id,description in requests:
        raw_path = experiment/candidate/partition/"raw.json"
        raw = next(row for row in load(raw_path)["cases"] if row["id"] == case_id)
        scored = next(row for row in report["results"][candidate][partition]["cases"] if row["id"] == case_id)
        witnesses.append({"description":description,"candidate":candidate,"case_id":case_id,
            "raw_path":str(raw_path.relative_to(root)),"raw_sha256":sha(raw_path),"score":scored,
            "actual_values":raw["values"],"error":raw["error"],
            "call_witnesses":[{k:call.get(k) for k in ("sequence","tool","status","query","source","evidence_id")} for call in raw["calls"]]})
    real_failure = experiment/"real/v2-dev-014-baseline.json"
    if real_failure.exists():
        raw = load(real_failure)
        witnesses.append({"description":"Real same-file task reaches verified reads but fails the final product contract",
            "candidate":"baseline","case_id":raw["id"],"raw_path":str(real_failure.relative_to(root)),
            "raw_sha256":sha(real_failure),"error":raw["error"],"calls":len(raw["calls"]),"reads":raw["verified_read_count"],
            "score":load(real_failure.with_suffix(".scored.json"))["cases"][0]})
    regression = load(Path(report["regression"])) if report.get("regression") else None
    regression_summary = None
    if regression:
        regression_summary = {"base_commit":regression["base_commit"],"archive":regression["archive"],
            "candidate":regression["candidate"],"core_complete":regression["core_complete"],
            "core_new_failures":regression["core_new_failures"],"full_repo_green":False,
            "stages":{name:{"summary":value.get("summary"),"missing_scripts":value.get("missing_scripts"),
                "nodes":[{k:row.get(k) for k in ("nodeid","baseline_status","candidate_status","classification","transition")} for row in value.get("pairs",[])]}
                for name,value in regression["stages"].items()}}
    real_path = experiment/"REAL_TRANSFER_REPORT.json"
    real = load(real_path) if real_path.exists() else None
    provider = real.get("provider") if real else None
    if provider is None and (experiment/"provider/BATCH.json").exists():
        from scripts.self_improvement.v2_provider import accounting
        provider = accounting(experiment/"provider",load(experiment/"provider/BATCH.json")["batch_id"])
    implementation = {str(path.relative_to(root)):__import__('hashlib').sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        for path in sorted((root/"scripts/self_improvement").glob("v2_*.py"))}
    chain = set()
    for pattern in ("*.json","*/*/RAW_SEAL.json","*/*/SCORE_V3_SEAL.json","*/*/raw.json","*/*/scored-v3.json",
                    "provider/BATCH.json","provider/CANARY_PASS.json","provider/calls/*/provider_ledger.jsonl",
                    "provider/calls/*/outcome.json","provider/calls/*/payload.json","provider/calls/*/response.json",
                    "complete-source-regression/report.json"):
        chain.update(experiment.glob(pattern))
    chain = {str(path.relative_to(root)):sha(path) for path in sorted(chain)
             if path.is_file() and not path.name.startswith("METRICS")}
    write(output,{"schema":"linkloom-self-improvement-v2-delivery/v1","base_commit":BASE_COMMIT,
        "experiment":report,"implementation_sha256_lf":implementation,"artifact_sha256":chain,"by_tag":tags,
        "qualification":load(root/"docs/requirements/self-improvement-hardening/DATASET_QUALIFICATION.json"),
        "failure_stories":witnesses,"regression":regression_summary,"real_transfer":real,"real_paired_analysis":real_pairs(experiment,real),"provider":provider,
        "real_stop":load(experiment/"LIVE_STOP.json") if (experiment/"LIVE_STOP.json").exists() else None,
        "provider_batch":load(experiment/"provider/BATCH.json") if (experiment/"provider/BATCH.json").exists() else None,
        "report_sha256":sha(experiment/"REPORT.json"),"full_repository_green":False,
        "raw_provider_payloads_committed":False,"production_activation":False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    export(args.experiment,args.output)
