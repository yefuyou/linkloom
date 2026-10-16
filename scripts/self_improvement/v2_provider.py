"""New V2 budget over the unchanged append-only Provider Ledger; no retries.

The provider keeps V2 admission and accounting around the existing append-only
ledger.  It does not change the ledger's transport semantics.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from scripts.self_improvement.v2_contracts import write

MODEL = "gemini-3.8-flash"
INPUT_RATE, OUTPUT_RATE = .75 / 1_000_000, 3.75 / 1_000_000
OUTPUT_BOUND, INPUT_BYTES = 4096, 98304
PROTOCOL = "self-improvement-v2"
PROTOCOL_OVERHEAD_BYTES = 32768
MAX_GENERATION_REQUESTS = 120
MAX_USD = 6.0
MAX_SECONDS = 10800
CANARY_REQUESTS = 12
MAX_STRICT_UNCERTAIN = 2
SDK_RETRIES = 0
PERMISSION_MARKER = "explicit_user_v2_authorization"


def _nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonnegative_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)) and value >= 0)


def _new_batch(*, experiment_id, freeze_sha256):
    return {
        "protocol": PROTOCOL,
        "batch_id": "v2-" + uuid4().hex,
        "experiment_id": experiment_id,
        "freeze_sha256": freeze_sha256,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "max_generation_requests": MAX_GENERATION_REQUESTS,
        "max_usd": MAX_USD,
        "max_seconds": MAX_SECONDS,
        "canary_request_limit": CANARY_REQUESTS,
        "synthetic_only": True,
        "permission": PERMISSION_MARKER,
        "sdk_retries": SDK_RETRIES,
    }


def _validate_batch(batch, *, experiment_id, freeze_sha256):
    """Validate the durable identity and immutable V2 policy fields."""
    if not isinstance(batch, dict):
        raise ValueError("V2_BATCH_IDENTITY_INVALID")
    identity_fields = {"protocol", "batch_id", "experiment_id", "freeze_sha256", "started_utc"}
    if not identity_fields <= set(batch):
        raise ValueError("V2_BATCH_OR_FREEZE_IDENTITY")
    if (batch["protocol"] != PROTOCOL
            or batch["experiment_id"] != experiment_id
            or batch["freeze_sha256"] != freeze_sha256
            or not isinstance(batch["batch_id"], str) or not batch["batch_id"]
            or not isinstance(batch["started_utc"], str) or not batch["started_utc"]):
        raise ValueError("V2_BATCH_OR_FREEZE_IDENTITY")
    required = {
        "protocol", "batch_id", "experiment_id", "freeze_sha256", "started_utc",
        "max_generation_requests", "max_usd", "max_seconds", "canary_request_limit",
        "synthetic_only", "permission", "sdk_retries",
    }
    if set(batch) != required:
        raise ValueError("V2_BATCH_IDENTITY_INVALID")
    immutable = {
        "max_generation_requests": MAX_GENERATION_REQUESTS,
        "max_usd": MAX_USD,
        "max_seconds": MAX_SECONDS,
        "canary_request_limit": CANARY_REQUESTS,
        "synthetic_only": True,
        "permission": PERMISSION_MARKER,
        "sdk_retries": SDK_RETRIES,
    }
    for field, expected in immutable.items():
        value = batch[field]
        if field in {"max_generation_requests", "max_seconds", "canary_request_limit", "sdk_retries"}:
            valid_type = isinstance(value, int) and not isinstance(value, bool)
        elif field == "max_usd":
            valid_type = _nonnegative_number(value)
        elif field == "synthetic_only":
            valid_type = isinstance(value, bool)
        elif field == "permission":
            valid_type = isinstance(value, str)
        else:
            valid_type = True
        if not valid_type or value != expected:
            raise ValueError("V2_BATCH_LIMITS_IMMUTABLE")
    try:
        datetime.fromisoformat(batch["started_utc"])
    except (TypeError, ValueError):
        raise ValueError("V2_BATCH_IDENTITY_INVALID") from None
    return batch


def _read_batch(path, *, experiment_id, freeze_sha256):
    try:
        batch = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise ValueError("V2_BATCH_IDENTITY_INVALID") from None
    return _validate_batch(batch, experiment_id=experiment_id, freeze_sha256=freeze_sha256)


def _reservation_cost(serialized_payload_bytes):
    if not _nonnegative_int(serialized_payload_bytes):
        raise RuntimeError("V2_LEDGER_RESERVATION_INVALID")
    return ((serialized_payload_bytes + PROTOCOL_OVERHEAD_BYTES) * INPUT_RATE
            + OUTPUT_BOUND * OUTPUT_RATE)


def _known_usage_cost(usage):
    """Return a reported cost, or ``None`` when the receipt is incomplete.

    Gemini normally omits ``thinking_tokens`` for a request that has no
    thinking-token usage.  That optional field therefore cannot be the test
    for a complete receipt.  When it is absent, ``total_tokens`` is required
    so a receipt with unreported thinking tokens cannot be undercounted.
    """
    if not isinstance(usage, dict):
        return None
    cost = usage.get("estimated_cost_usd")
    if not _nonnegative_number(cost):
        return None
    status = usage.get("usage_status")
    if status == "reported":
        prompt = usage.get("prompt_tokens")
        output = usage.get("output_tokens")
        thoughts = usage.get("thinking_tokens")
        total = usage.get("total_tokens")
        if not _nonnegative_int(prompt) or not _nonnegative_int(output):
            return None
        if thoughts is None:
            if not _nonnegative_int(total) or total < prompt + output:
                return None
            # If total includes hidden thinking tokens, charge the complete
            # provider-reported output side instead of the receipt's
            # thinking_tokens=None estimate.
            output_billable = max(total - prompt, output)
        else:
            if not _nonnegative_int(thoughts):
                return None
            expected_total = prompt + output + thoughts
            if total is not None and (
                    not _nonnegative_int(total) or total < expected_total):
                return None
            output_billable = output + thoughts
            if total is not None:
                output_billable = max(output_billable, total - prompt)
        derived = prompt * INPUT_RATE + output_billable * OUTPUT_RATE
        return max(float(cost), derived)
    # Preserve compatibility with the original V2 ledger shape, which had no
    # usage_status marker and used thinking_tokens as its completeness marker.
    if status is None and _nonnegative_int(usage.get("thinking_tokens")):
        return float(cost)
    return None


def reserve(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False).encode()
    if len(encoded) > INPUT_BYTES or payload.get("model") != MODEL:
        raise ValueError("V2_PROVIDER_INPUT_OR_MODEL_BOUND")
    # One token per UTF-8 byte plus 32KiB of protocol overhead is conservative.
    return _reservation_cost(len(encoded))


def accounting(root, batch_id):
    requests, known, unknown, strict, integrity_failures = 0, 0., 0., 0, 0
    blocked, versions, request_ids = set(), set(), set()
    for path in Path(root).glob("calls/*/provider_ledger.jsonl"):
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError, TypeError):
            raise RuntimeError("V2_LEDGER_INVALID") from None
        if not rows or rows[0].get("batch_id") != batch_id:
            raise ValueError("V2_OLD_LEDGER_IDENTITY")
        identity_rows = [row for row in rows if "batch_id" in row]
        if any(row.get("batch_id") != batch_id for row in identity_rows):
            raise ValueError("V2_OLD_LEDGER_IDENTITY")
        reserved, completed = {}, {}
        run_finished = 0
        for row in rows:
            event = row.get("event")
            if event == "request_reserved":
                request_id = row.get("request_id")
                if (not isinstance(request_id, str) or not request_id or request_id in reserved
                        or request_id in request_ids):
                    raise RuntimeError("V2_LEDGER_DUPLICATE_REQUEST")
                reserved[request_id] = row
                request_ids.add(request_id)
            elif event == "request_finished":
                request_id = row.get("request_id")
                if not isinstance(request_id, str) or not request_id or request_id in completed:
                    raise RuntimeError("V2_LEDGER_DUPLICATE_REQUEST")
                completed[request_id] = row
            elif event == "run_finished":
                run_finished += 1
        if set(reserved) != set(completed) or run_finished != 1:
            raise RuntimeError("V2_UNCLOSED_REQUEST_OR_BATCH")
        outcome_path = path.with_name("outcome.json")
        if not outcome_path.exists():
            raise RuntimeError("V2_MISSING_PROVIDER_DIAGNOSIS")
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise RuntimeError("V2_PROVIDER_DIAGNOSIS_INVALID") from None
        strict_uncertain = outcome.get("strict_uncertain")
        if not (isinstance(strict_uncertain, (bool, int)) and int(strict_uncertain) in {0, 1}):
            raise RuntimeError("V2_PROVIDER_DIAGNOSIS_INVALID")
        strict += int(strict_uncertain)
        case_id = outcome.get("case_id")
        if (outcome.get("response_received") is True
                and outcome.get("response_accepted") is False):
            integrity_failures += 1
        if strict_uncertain or outcome.get("ledger_halted"):
            if not isinstance(case_id, str) or not case_id:
                raise RuntimeError("V2_PROVIDER_DIAGNOSIS_INVALID")
            blocked.add(case_id)
        for request_id, reservation in reserved.items():
            requests += 1
            receipt = completed[request_id].get("usage") or {}
            cost = _known_usage_cost(receipt)
            if cost is not None:
                known += cost
            else:
                unknown += _reservation_cost(reservation.get("serialized_payload_bytes"))
            if completed[request_id].get("provider_model_version"):
                versions.add(completed[request_id]["provider_model_version"])
    return {"generation_requests": requests, "known_usage_estimated_usd": known,
            "missing_usage_reserves_usd": unknown, "cost_upper_bound_usd": known + unknown,
            "strict_uncertain": strict, "blocked_cases": sorted(blocked),
            "observed_model_versions": sorted(versions), "sdk_retries": 0,
            "response_integrity_failures": integrity_failures,
            "request_ids": sorted(request_ids)}


class V2Provider:
    def __init__(self, root, *, experiment_id, freeze_sha256, permission, sdk=None):
        if permission is not True:
            raise ValueError("V2_EXPLICIT_PROVIDER_PERMISSION_REQUIRED")
        self.root, self.experiment_id = Path(root), experiment_id
        self.freeze_sha256 = freeze_sha256
        self.root.mkdir(parents=True, exist_ok=True)
        identity = self.root / "BATCH.json"
        if not identity.exists():
            write(identity, _new_batch(experiment_id=experiment_id, freeze_sha256=freeze_sha256))
        self.batch = _read_batch(identity, experiment_id=experiment_id, freeze_sha256=freeze_sha256)
        if sdk is None:
            from google.genai import types
            from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise RuntimeError("V2_PROVIDER_CREDENTIAL_UNAVAILABLE")
            sdk = build_gemini_sdk_client(key, http_options_kwargs={"timeout": 45000,
                "retry_options": types.HttpRetryOptions(attempts=1)})
        self.sdk = sdk
        self.case_id, self.purpose = "compatibility", "compatibility"
        self.usage_records = []

    def _refresh_batch(self):
        self.batch = _read_batch(self.root / "BATCH.json", experiment_id=self.experiment_id,
                                 freeze_sha256=self.freeze_sha256)
        return self.batch

    def summary(self):
        self._refresh_batch()
        return accounting(self.root, self.batch["batch_id"])

    def _canary_pass_valid(self, actual_request_ids=None):
        """Verify the durable, scored evidence that unlocks post-canary sends.

        ``CANARY_PASS.json`` is an assertion produced by the live harness, so
        merely finding the marker is insufficient.  Every referenced artifact
        must remain inside this experiment, retain its recorded digest, and
        preserve the one-case/one-candidate successful score chain.
        """
        marker_path = self.root / "CANARY_PASS.json"
        experiment_root = self.root.parent.resolve()
        if actual_request_ids is None:
            actual_request_ids = accounting(self.root, self.batch["batch_id"])["request_ids"]

        def resolve_artifact(value):
            if not isinstance(value, str) or not value:
                return None
            path = Path(value)
            if not path.is_absolute():
                path = experiment_root / path
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(experiment_root)
            except (OSError, RuntimeError, ValueError):
                return None
            return resolved if resolved.is_file() else None

        def digest(path):
            try:
                return hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return None

        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        required = {"experiment_id", "freeze_sha256", "batch_id", "raw_path", "raw_sha256",
                    "score_path", "score_sha256", "request_count", "request_ids"}
        if not isinstance(marker, dict) or not required <= set(marker):
            return False
        if (marker["experiment_id"] != self.experiment_id
                or marker["freeze_sha256"] != self.freeze_sha256
                or marker["batch_id"] != self.batch["batch_id"]):
            return False
        request_count = marker["request_count"]
        if (not isinstance(request_count, int) or isinstance(request_count, bool)
                or request_count < 1 or request_count > CANARY_REQUESTS):
            return False
        marker_request_ids = marker["request_ids"]
        if (not isinstance(marker_request_ids, list)
                or len(marker_request_ids) != request_count
                or not all(isinstance(value, str) and value for value in marker_request_ids)
                or len(set(marker_request_ids)) != len(marker_request_ids)
                or not set(marker_request_ids) <= set(actual_request_ids)):
            return False
        raw_path = resolve_artifact(marker["raw_path"])
        score_path = resolve_artifact(marker["score_path"])
        if raw_path is None or score_path is None:
            return False
        if (not raw_path.name.endswith(".envelope.json")
                or not score_path.name.endswith(".scored.json")
                or digest(raw_path) != marker["raw_sha256"]
                or digest(score_path) != marker["score_sha256"]):
            return False
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            scored = json.loads(score_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(raw, dict) or not isinstance(scored, dict):
            return False
        raw_candidate = raw.get("candidate")
        raw_cases = raw.get("cases")
        if (not isinstance(raw_candidate, dict) or not isinstance(raw_cases, list)
                or len(raw_cases) != 1):
            return False
        candidate_id = raw_candidate.get("id")
        raw_case = raw_cases[0]
        case_id = raw_case.get("id") if isinstance(raw_case, dict) else None
        real_provider_requests = (
            raw_case.get("real_provider_requests") if isinstance(raw_case, dict) else None
        )
        if (not isinstance(candidate_id, str) or not candidate_id
                or not isinstance(case_id, str) or not case_id
                or raw_case.get("candidate_id") != candidate_id
                or candidate_id.casefold() not in raw_path.name.casefold()
                or case_id.casefold() not in raw_path.name.casefold()
                or (real_provider_requests is not None
                    and (not isinstance(real_provider_requests, int)
                         or isinstance(real_provider_requests, bool)
                         or real_provider_requests < 0
                         or real_provider_requests > request_count))):
            return False
        scored_candidate = scored.get("candidate")
        scored_cases = scored.get("cases")
        metrics = scored.get("metrics")
        if (scored_candidate != raw_candidate or not isinstance(scored_cases, list)
                or len(scored_cases) != 1 or not isinstance(metrics, dict)):
            return False
        scored_case = scored_cases[0]
        if (not isinstance(scored_case, dict) or scored_case.get("id") != case_id
                or scored_case.get("passed") is not True
                or scored_case.get("safety_violation") is not False
                or metrics.get("safety_violations") != 0
                or candidate_id.casefold() not in score_path.name.casefold()
                or case_id.casefold() not in score_path.name.casefold()):
            return False
        if raw.get("partition") is not None and scored.get("partition") != raw.get("partition"):
            return False
        return True

    def generate_content(self, *, model, contents, config):
        from scripts.reliability_probe import live_execution_lock
        from scripts.recovery_live import classify_exception
        from tests.smoke.test_p14_cn2_typed_gemini38_smoke import AppendOnlyProviderLedger, _usage_receipt
        config = {**config, "max_output_tokens": OUTPUT_BOUND, "automatic_function_calling": {"disable": True}}
        payload = {"model": model, "contents": contents, "config": config}
        reservation = reserve(payload)
        with live_execution_lock(self.root):
            self._refresh_batch()
            prior = self.summary()
            elapsed = time.time() - datetime.fromisoformat(self.batch["started_utc"]).timestamp()
            if prior["response_integrity_failures"]:
                raise RuntimeError("V2_PROVIDER_RESPONSE_INTEGRITY_STOP")
            if prior["strict_uncertain"] >= MAX_STRICT_UNCERTAIN or self.case_id in prior["blocked_cases"]:
                raise RuntimeError("V2_UNKNOWN_OUTCOME_NO_REPLAY")
            if (prior["generation_requests"] >= self.batch["max_generation_requests"]
                    or elapsed > self.batch["max_seconds"] - 45):
                raise RuntimeError("V2_PROVIDER_REQUEST_OR_TIME_LIMIT")
            canary_marker_exists = (self.root / "CANARY_PASS.json").exists()
            canary_marker_valid = (
                self._canary_pass_valid(prior["request_ids"])
                if canary_marker_exists else False
            )
            if canary_marker_exists and not canary_marker_valid:
                raise RuntimeError("V2_CANARY_MARKER_INVALID")
            if (prior["generation_requests"] >= self.batch["canary_request_limit"]
                    and not canary_marker_valid):
                raise RuntimeError("V2_CANARY_REQUEST_CAP")
            if prior["cost_upper_bound_usd"] + reservation > self.batch["max_usd"]:
                raise RuntimeError("V2_PROVIDER_COST_LIMIT")
            folder = self.root / "calls" / uuid4().hex
            ledger = AppendOnlyProviderLedger(folder / "provider_ledger.jsonl", batch_id=self.batch["batch_id"],
                run_id=folder.name, max_requests=1, max_runtime_runs=1,
                max_input_tokens_per_request=INPUT_BYTES + 32768, case_id=self.case_id)
            ledger.runtime_started()
            failure, received, accepted = None, False, False

            def send():
                nonlocal failure, received
                try:
                    response = self.sdk.models.generate_content(**payload)
                    received = True
                    return response
                except BaseException as error:
                    failure = classify_exception(error)
                    raise

            try:
                write(folder / "payload.json", payload)
                response = ledger.request(send, request_kind="generate_content", model=MODEL,
                                          payload=payload, max_output_tokens=OUTPUT_BOUND)
                accepted = True
                usage = _usage_receipt(response, "generate_content")
                self.usage_records.append({"case_id": self.case_id, "purpose": self.purpose, **usage})
                if usage.get("estimated_cost_usd", 0) and usage["estimated_cost_usd"] > reservation:
                    raise RuntimeError("V2_COST_RESERVATION_BOUND_EXCEEDED")
                if hasattr(response, "model_dump"):
                    write(folder / "response.json", response.model_dump(mode="json"))
                return response
            finally:
                outcome = {"case_id": self.case_id, "experiment_id": self.experiment_id,
                    "purpose": self.purpose, "response_received": received,
                    "response_accepted": accepted, "ledger_halted": ledger.halted,
                    "classification": failure, "reserved_usd": reservation,
                    "strict_uncertain": bool(not received and (failure is None or
                        failure["classification"] == "provider_outcome_uncertain"))}
                write(folder / "outcome.json", outcome)
                try:
                    if not ledger.in_flight:
                        ledger.finish_run(status="completed" if received else "failed", usage_summary={})
                finally:
                    ledger.close()

    def generate_json(self, prompt, *, case_id, purpose):
        self.case_id, self.purpose = case_id, purpose
        response = self.generate_content(model=MODEL, contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0})
        return json.loads(response.text)

    def close(self):
        close = getattr(self.sdk, "close", None)
        if callable(close):
            close()
