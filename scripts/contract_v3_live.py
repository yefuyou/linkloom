"""Serial V3 product verification; each invocation has an exclusive trial record."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def verify_pair_identity(case, case_sha256, planning, trials_root):
    """Bind a new Contract phase to its already-recorded baseline inputs."""
    candidates = []
    for path in trials_root.glob("*/attempt.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("phase") == "A" and record.get("case") == case["id"]:
            candidates.append((path, record))
    if len(candidates) != 1:
        raise RuntimeError("PAIR_REQUIRES_ONE_BASELINE")
    path, record = candidates[0]
    if (record.get("case_sha256") != case_sha256
            or record.get("planning") != planning):
        raise RuntimeError("PAIR_INPUT_IDENTITY_MISMATCH")
    if not (path.parent / "result.json").is_file():
        raise RuntimeError("PAIR_BASELINE_NOT_TERMINAL")
    return {"baseline_attempt": str(path),
            "baseline_attempt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "case_sha256": case_sha256, "planning": planning}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", type=Path, default=ROOT)
    parser.add_argument("--case", type=Path, required=True, help="Public synthetic case; never Gold")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--phase", choices=["A", "B"], required=True)
    parser.add_argument("--planning", choices=["rules", "model"], default="rules")
    parser.add_argument("--provider-permission", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if not args.provider_permission:
        parser.error("Explicit --provider-permission is required")
    if not args.trial_id.replace("-", "").isalnum():
        parser.error("Simple trial identity required")
    code = args.code_root.resolve()
    if not args.preflight:
        receipt = ROOT / "work/v3-live/trials/preflight-01"
        confirmed = json.loads((receipt / "result.json").read_text(encoding="utf-8"))
        accounting = json.loads((receipt / "accounting.json").read_text(encoding="utf-8"))
        if not confirmed.get("compatible") or accounting["provider"]["actual_model_version"] != "gemini-3.8-flash":
            raise RuntimeError("COMPATIBILITY_PREFLIGHT_REQUIRED")
        if args.trial_id != "canary-01":
            canary = json.loads((ROOT / "work/v3-live/trials/canary-01/result.json").read_text(encoding="utf-8"))
            if not (canary.get("grounding", {}).get("valid")
                    and canary.get("unified_result", {}).get("reviewer", {}).get("accepted")):
                raise RuntimeError("CANARY_NOT_ACCEPTED")
    sys.path[:0] = [str(code / "src"), str(code), str(code / "tests/integration")]
    os.environ["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
    module_spec = importlib.util.spec_from_file_location("v3_paid_boundary", ROOT / "scripts/contract_v3_provider.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    from scripts import adaptive_evaluation as evaluation
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from linkloom.runtime.adaptive_planning import SOURCE_RELATION_PROFILE, validate_source_relation_plan

    case = json.loads(args.case.read_text(encoding="utf-8"))
    if set(case) - {"id", "query", "documents", "snapshot", "snapshot_sha256", "source_names", "workspace_id"}:
        raise ValueError("Public case contains unsupported or oracle fields")
    snapshot = None
    if "snapshot" in case:
        snapshot = (ROOT / case["snapshot"]).resolve()
        snapshot.relative_to((ROOT / "work/v3-live/snapshots").resolve())
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != case["snapshot_sha256"]:
            raise ValueError("Synthetic snapshot identity mismatch")
    case_sha256 = hashlib.sha256(args.case.read_bytes()).hexdigest()
    pair_identity = None
    if args.phase == "B" and not args.preflight and args.trial_id != "canary-01":
        pair_identity = verify_pair_identity(case, case_sha256, args.planning, ROOT / "work/v3-live/trials")
    output = ROOT / "work/v3-live/trials" / args.trial_id
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "attempt.json", {
        "case": case["id"], "phase": args.phase, "model": module.MODEL,
        "code_root": str(code), "planning": args.planning,
        "case_sha256": case_sha256, "pair_identity": pair_identity,
        "code_sha256": {p.relative_to(code).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (code / "src/linkloom").rglob("*.py")},
    })
    sdk, provider, endpoint = module.connect(provider_permission=True)
    if args.trial_id == "canary-01":
        original_generate = provider.generate_content

        def canary_generate(*arguments, **keywords):
            if provider.summary()["request_count"] >= 12:
                raise RuntimeError("CANARY_REQUEST_LIMIT")
            return original_generate(*arguments, **keywords)

        provider.generate_content = canary_generate
    result = None
    try:
        with provider.task(case["id"], 1, args.phase):
            if args.preflight:
                proposal = GeminiProviderAdapter(provider).propose_task_plan(case["query"], profile=SOURCE_RELATION_PROFILE)
                plan = validate_source_relation_plan(proposal, user_query=case["query"], max_steps=12)
                if provider.summary()["actual_model_version"] != module.MODEL:
                    raise RuntimeError("ACTUAL_MODEL_VERSION_NOT_CONFIRMED")
                result = {"compatible": True, "plan": plan.to_dict(), "model": module.MODEL}
            else:
                # Clone the same pre-seeded synthetic database for both Contracts.
                # No changes to historical harness behavior or production classes.
                if snapshot is not None:
                    import linkloom.ui.product as product
                    original_app = product.ProductApplication

                    def cloned_app(*, database_path, **kwargs):
                        shutil.copyfile(snapshot, database_path)
                        return original_app(database_path=database_path, **kwargs)

                    product.ProductApplication = cloned_app
                    evaluation._seed_sources = lambda app, public, root: (case["workspace_id"], case["source_names"])
                evaluation.POLL_INTERVAL_SECONDS = .2
                result = evaluation.run_case(case, output / "artifacts", provider,
                                             variant="B" if args.planning == "model" else "A")
        write_new(output / "result.json", result)
    except Exception as error:
        write_new(output / "failure.json", {"type": type(error).__name__, "code": getattr(error, "code", None)})
        raise
    finally:
        write_new(output / "accounting.json", {"provider": provider.summary(), "endpoint": endpoint})
        sdk.close()
    print(json.dumps({"trial": args.trial_id, "status": result.get("status", "PREFLIGHT"),
                      "requests": provider.summary()["request_count"]}))


if __name__ == "__main__":
    main()
