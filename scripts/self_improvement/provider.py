"""Optional serial Gemini proposals, behind the existing durable Provider Ledger."""
import json
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

MODEL = "gemini-3.8-flash"
RESERVE_USD = .20  # >=65536 input +4096 output at $1.50/$7.50 per million


def reconcile_completed_batches(root):
    """Append an explicit recovery terminal after process exit; never erase events.

    The OS lease proves no lab sender is active. Every request must already
    have a finished reservation and an outcome diagnosis, or accounting stops.
    Costs and uncertain outcomes are preserved, including failed connections.
    """
    from scripts.reliability_probe import live_execution_lock
    with live_execution_lock(root):
        accounting(root)
        for path in root.glob("provider/*/provider_ledger.jsonl"):
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if any(row["event"] == "run_finished" for row in rows):
                continue
            outcome = json.loads(path.with_name("outcome.json").read_text(encoding="utf-8"))
            terminal = {"event": "run_finished", "status": "reconciled_after_process_exit",
                        "recovery": True, "original_requests_replayed": 0,
                        "strict_uncertain": outcome["strict_uncertain"],
                        "utc": datetime.now(timezone.utc).isoformat()}
            fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
            try:
                payload = (json.dumps(terminal) + "\n").encode()
                if os.write(fd, payload) != len(payload):
                    raise RuntimeError("Reconciliation append incomplete")
                os.fsync(fd)
            finally:
                os.close(fd)


def accounting(root):
    requests, strict, estimated = 0, 0, 0.
    missing_usage, versions, incomplete_batches = 0, set(), 0
    for path in root.glob("provider/*/provider_ledger.jsonl"):
        pending = set()
        finished = False
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["event"] == "request_reserved":
                pending.add(row["request_id"])
                requests += 1
            elif row["event"] == "request_finished":
                pending.discard(row["request_id"])
                usage = row.get("usage") or {}
                cost = usage.get("estimated_cost_usd")
                if isinstance(cost, (int, float)):
                    estimated += cost
                else:
                    missing_usage += 1
                if row.get("provider_model_version"):
                    versions.add(row["provider_model_version"])
            elif row["event"] == "run_finished":
                finished = True
        if pending:
            raise RuntimeError("UNCLOSED_PROVIDER_RESERVATION")
        incomplete_batches += int(not finished)
        outcome = path.with_name("outcome.json")
        if not outcome.exists():
            if path.stat().st_size:
                raise RuntimeError("MISSING_PROVIDER_DIAGNOSIS")
        else:
            strict += json.loads(outcome.read_text(encoding="utf-8"))["strict_uncertain"]
    return {"requests": requests, "reserved_usd": round(requests * RESERVE_USD, 4),
            "usage_estimated_usd": round(estimated, 8), "missing_usage_requests": missing_usage,
            "observed_model_versions": sorted(versions),
            "incomplete_batches": incomplete_batches,
            "strict_uncertain": strict, "max_requests": 100, "max_usd": 5,
            "sdk_retries": 0, "model": MODEL}


def generate(prompt, root):
    from google.genai import types
    from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
    from tests.smoke.test_p14_cn2_typed_gemini38_smoke import AppendOnlyProviderLedger
    from scripts.reliability_probe import live_execution_lock
    from scripts.recovery_live import classify_exception

    payload = {"model": MODEL, "contents": prompt, "config": {
        "response_mime_type": "application/json", "max_output_tokens": 4096,
        "temperature": 0}}
    if len(json.dumps(payload).encode()) > 32768:
        raise ValueError("PROVIDER_INPUT_BOUND")
    with live_execution_lock(root):
        prior = accounting(root)
        if prior["incomplete_batches"]:
            raise RuntimeError("INCOMPLETE_PROVIDER_BATCH")
        if prior["strict_uncertain"] >= 2 or prior["requests"] >= 100:
            raise RuntimeError("PROVIDER_STOP_LIMIT")
        if prior["reserved_usd"] + RESERVE_USD > 5:
            raise RuntimeError("PROVIDER_COST_LIMIT")
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("PROVIDER_CREDENTIAL_UNAVAILABLE")
        sdk = build_gemini_sdk_client(key, http_options_kwargs={
            "timeout": 45000, "retry_options": types.HttpRetryOptions(attempts=1)})
        call_id = uuid4().hex
        folder = root / "provider" / call_id
        ledger = AppendOnlyProviderLedger(folder / "provider_ledger.jsonl", batch_id="lab",
            run_id=call_id, max_requests=1, max_runtime_runs=1,
            max_input_tokens_per_request=65536, case_id="strategy_proposal")
        failure = None
        completed = False

        def send():
            nonlocal failure
            try:
                return sdk.models.generate_content(**payload)
            except Exception as error:
                failure = classify_exception(error)
                raise

        ledger.runtime_started()
        try:
            response = ledger.request(send, request_kind="generate_content", model=MODEL,
                                      payload=payload, max_output_tokens=4096)
            text = response.text
            (folder / "response.json").write_text(text or "null", encoding="utf-8")
            (folder / "outcome.json").write_text(json.dumps({"strict_uncertain": 0,
                "response_received": True, "model_version": response.model_version}), encoding="utf-8")
            completed = True
            return json.loads(text)
        finally:
            if not (folder / "outcome.json").exists():
                (folder / "outcome.json").write_text(json.dumps({
                    "strict_uncertain": int(failure is None or failure["classification"] == "provider_outcome_uncertain"),
                    "classification": failure}), encoding="utf-8")
            try:
                if not ledger.in_flight:
                    ledger.finish_run(status="completed" if completed else "failed", usage_summary={})
            finally:
                ledger.close()
                sdk.close()
