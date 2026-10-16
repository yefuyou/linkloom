"""Explicit, independently accounted live entry for the Linkloom B experiment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests/integration")]
OUT = ROOT / "work/adaptive-planning"
LEDGER = OUT / "provider.jsonl"


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def connect():
    from google.genai import types
    from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
    from scripts.adaptive_provider import AdaptiveProvider
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_CREDENTIAL_UNAVAILABLE")
    identity = {"endpoint": "generativelanguage.googleapis.com", "requests": 0}
    def check_endpoint(request):
        if request.url.host != identity["endpoint"]:
            raise RuntimeError("SDK_ENDPOINT_IDENTITY_MISMATCH")
        identity["requests"] += 1
    sdk = build_gemini_sdk_client(key, http_options_kwargs={
        "base_url": "https://generativelanguage.googleapis.com",
        "timeout": 130000, "retry_options": types.HttpRetryOptions(attempts=1),
        "client_args": {"http2": False, "event_hooks": {"request": [check_endpoint]}},
    })
    provider = AdaptiveProvider(sdk, LEDGER, experiment_id="linkloom-b-20261016",
                                max_requests=400, max_usd=15, max_seconds=10800)
    return sdk, provider, identity


def preflight():
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from linkloom.runtime.adaptive_planning import SOURCE_RELATION_PROFILE, validate_source_relation_plan
    from scripts.adaptive_provider import INPUT_TOKEN_BOUND, MAX_OUTPUT_TOKENS, RESERVATION_USD
    sdk, provider, identity = connect()
    packet = {"utc": datetime.now(timezone.utc).isoformat(), "authorization": "User Linkloom B request 2026-10-16",
        "model": "gemini-3.8-flash", "credential_source": "GEMINI_API_KEY" if os.getenv("GEMINI_API_KEY") else "GOOGLE_API_KEY",
        "identity": "Gemini Developer API; vertexai=False; credential principal not exposed by SDK",
        "official_endpoint": identity["endpoint"], "sdk": importlib.metadata.version("google-genai"),
        "sdk_retries": 0, "request_timeout_ms": 130000, "serial_tasks": True,
        "max_requests": 400, "max_usd": 15, "max_seconds": 10800,
        "input_token_upper_bound": INPUT_TOKEN_BOUND, "max_output_tokens_including_thinking": MAX_OUTPUT_TOKENS,
        "request_reservation_usd": RESERVATION_USD, "prices_per_million": {"input": .75, "output": 3.75},
        "price_source": "https://ai.google.dev/gemini-api/docs/pricing.md",
        "model_source": "https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash",
        "ledger": "work/adaptive-planning/provider.jsonl", "no_old_ledgers_read_or_modified": True,
        "permission": "Explicit user authorization; synthetic question only; no real vault",
        "compatible": False}
    write_new(OUT / "preflight-before.json", packet)
    query = "想查清 Willow Bay 的牵头人，以及这个人向谁直接汇报。"
    try:
        with provider.task("PREFLIGHT", 1, "B"):
            raw = GeminiProviderAdapter(provider).propose_task_plan(query, profile=SOURCE_RELATION_PROFILE)
        plan = validate_source_relation_plan(raw, user_query=query, max_steps=12)
        packet.update(compatible=True, endpoint_requests=identity["requests"], plan=plan.to_dict(),
                      plan_valid=True, accounting=provider.summary())
        print(json.dumps({"preflight": "PASS", "plan_subgoals": len(plan.subgoals), "accounting": provider.summary()}))
    except Exception as error:
        packet.update(error_class=type(error).__name__, safe_code=getattr(error, "code", None), accounting=provider.summary())
        print(json.dumps({"preflight": "FAILED", "error_class": type(error).__name__, "accounting": provider.summary()}))
    finally:
        write_new(OUT / "preflight-after.json", packet)
        sdk.close()
    return 0 if packet["compatible"] else 1


def evaluate(args):
    from scripts.adaptive_evaluation import run_case, score
    preflight_record = json.loads((OUT / "preflight-after.json").read_text(encoding="utf-8"))
    if not preflight_record.get("compatible"):
        raise RuntimeError("LIVE_PREFLIGHT_NOT_PASSED")
    if args.partition == "heldout" and not args.final_acceptance:
        raise RuntimeError("HELDOUT_REQUIRES_FINAL_ACCEPTANCE")
    fixture_root = ROOT / "tests/fixtures/adaptive_planning"
    if args.partition == "heldout":
        frozen = json.loads((fixture_root / "freeze_heldout.json").read_text(encoding="utf-8"))
        for name, digest in frozen["files"].items():
            if hashlib.sha256((fixture_root / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("HELDOUT_FREEZE_MISMATCH")
    dataset = fixture_root / f"public_{args.partition}.json"
    public = json.loads(dataset.read_text(encoding="utf-8"))["cases"]
    if args.cases:
        selected = set(args.cases.split(","))
        if not selected <= {case["id"] for case in public}:
            raise RuntimeError("UNKNOWN_CASE")
        public = [case for case in public if case["id"] in selected]
    gold = {case["id"]: case for case in json.loads(
        (fixture_root / f"gold_{args.partition}.json").read_text(encoding="utf-8"))["cases"]}
    run_root = OUT / "runs" / args.run
    run_root.mkdir(parents=True, exist_ok=False)
    sdk, provider, identity = connect()
    fingerprint_files = list((ROOT / "src/linkloom").rglob("*.py")) + [
        ROOT / "scripts/adaptive_evaluation.py", ROOT / "scripts/adaptive_provider.py"]
    report = {"partition": args.partition, "variants": args.variants.split(","),
        "trial": args.trial, "live": True, "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "code_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in fingerprint_files},
        "cases": [], "before": provider.summary()}
    write_new(run_root / "protocol.json", {key: value for key, value in report.items() if key != "cases"})
    try:
        for case in public:
            for variant in report["variants"]:
                if provider.summary()["stopped"] or provider.summary()["status"] == "EXHAUSTED":
                    raise RuntimeError("EXPERIMENT_STOPPED")
                with provider.task(case["id"], args.trial, variant):
                    actual = run_case(case, run_root / "artifacts", provider, variant=variant)
                actual["case_id"] = case["id"]
                actual["trial"] = args.trial
                write_new(run_root / f"{case['id']}-{variant}-raw.json", actual)
                try:
                    actual["score"] = score(actual, gold[case["id"]])
                except Exception as score_error:
                    actual["score"] = {"passed": False, "scoring_error": type(score_error).__name__}
                write_new(run_root / f"{case['id']}-{variant}.json", actual)
                report["cases"].append(actual)
                print(json.dumps({"case": case["id"], "variant": variant, "status": actual.get("status"),
                    "passed": actual["score"].get("passed"), "model_turns": actual.get("model_turns"),
                    "planning_rounds": actual.get("planning_rounds"), "elapsed_ms": actual.get("elapsed_ms"),
                    "requests": provider.summary()["request_count"], "usd": provider.summary()["committed_usd"]},
                    ensure_ascii=False), flush=True)
    except Exception as error:
        report["stopped"] = {"error_class": type(error).__name__, "code": getattr(error, "code", None)}
        print(json.dumps({"stopped": report["stopped"], "accounting": provider.summary()}), flush=True)
    finally:
        report["after"] = provider.summary()
        report["endpoint_requests"] = identity["requests"]
        write_new(run_root / "report.json", report)
        sdk.close()
    return 0 if "stopped" not in report else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--partition", choices=["dev", "validation", "heldout"])
    parser.add_argument("--cases")
    parser.add_argument("--variants", default="B")
    parser.add_argument("--trial", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument("--run")
    parser.add_argument("--final-acceptance", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        raise SystemExit(preflight())
    if not args.partition or not args.run or not all(char.isalnum() or char == "-" for char in args.run):
        parser.error("Evaluation requires --partition and a simple unique --run identity.")
    if not set(args.variants.split(",")) <= {"A", "B", "C"}:
        parser.error("variants must be A, B and/or C")
    raise SystemExit(evaluate(args))
