"""Shared provider accounting for transport tests and explicitly authorized smoke runners."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from linkloom.runtime.errors import ProviderOutcomeUnknownError


MODEL = "gemini-3.8-flash"

INPUT_USD_PER_MILLION_TOKENS = 0.75

OUTPUT_USD_PER_MILLION_TOKENS = 3.75

class SmokeBlocked(RuntimeError):
    """Safe fixed reason for a preflight or real-run stop."""

class UncertainProviderRequest(SmokeBlocked, ProviderOutcomeUnknownError):
    """Keep the smoke stop contract while exposing an exact Runtime category."""

    def __init__(self) -> None:
        ProviderOutcomeUnknownError.__init__(self)

def _read_field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None

def _int_field(value: Any, *names: str) -> int | None:
    result = _read_field(value, *names)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        return None
    return result

def _usage_receipt(response: Any, request_kind: str) -> dict[str, Any]:
    def reported_cost(value: Any) -> float | None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            return None
        return float(value)

    if request_kind == "count_tokens":
        counted = _int_field(response, "total_tokens", "totalTokenCount", "totalTokens")
        provider_cost = reported_cost(_read_field(response, "cost_usd", "costUsd"))
        return {
            "counted_input_tokens": counted,
            "usage_status": "reported" if counted is not None else "unknown",
            "model_version": _read_field(response, "model_version", "modelVersion"),
            "provider_reported_cost_usd": provider_cost,
            "estimated_cost_usd": None,
        }
    usage = _read_field(response, "usage_metadata", "usageMetadata", "usage")
    prompt = _int_field(usage, "prompt_token_count", "promptTokenCount", "input_tokens")
    output = _int_field(usage, "candidates_token_count", "candidatesTokenCount", "output_tokens")
    thoughts = _int_field(usage, "thoughts_token_count", "thoughtsTokenCount", "thinking_tokens")
    cached = _int_field(usage, "cached_content_token_count", "cachedContentTokenCount")
    total = _int_field(usage, "total_token_count", "totalTokenCount", "total_tokens")
    version = _read_field(response, "model_version", "modelVersion") or _read_field(
        usage, "model_version", "modelVersion"
    )
    provider_cost = reported_cost(_read_field(usage, "cost_usd", "costUsd"))
    if provider_cost is None:
        provider_cost = reported_cost(_read_field(response, "cost_usd", "costUsd"))
    reported = prompt is not None and output is not None
    estimated_cost = None
    if prompt is not None and output is not None:
        estimated_cost = (
            prompt * INPUT_USD_PER_MILLION_TOKENS
            + (output + (thoughts or 0)) * OUTPUT_USD_PER_MILLION_TOKENS
        ) / 1_000_000
    return {
        "prompt_tokens": prompt,
        "output_tokens": output,
        "thinking_tokens": thoughts,
        "cached_tokens": cached,
        "total_tokens": total,
        "model_version": version,
        "usage_status": "reported" if reported else "unknown",
        "provider_reported_cost_usd": provider_cost,
        "estimated_cost_usd": estimated_cost,
    }

class AppendOnlyProviderLedger:
    """Exclusive, append-only request ledger with reservation before transport."""

    def __init__(
        self,
        path: Path,
        *,
        batch_id: str,
        run_id: str,
        max_requests: int,
        max_runtime_runs: int,
        max_input_tokens_per_request: int | None,
        case_id: str = "P0_CN2",
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND,
                0o600,
            )
        except FileExistsError:
            raise SmokeBlocked("INDEPENDENT_LEDGER_ALREADY_EXISTS_STOP") from None
        except OSError:
            raise SmokeBlocked("INDEPENDENT_LEDGER_CREATE_FAILED") from None
        self.path = path
        self.batch_id = batch_id
        self.run_id = run_id
        self.case_id = case_id
        self.max_requests = max_requests
        self.max_runtime_runs = max_runtime_runs
        self.max_input_tokens_per_request = max_input_tokens_per_request
        self._request_count = 0
        self._runtime_runs = 0
        self._in_flight = False
        self._halted = False
        self._run_finished = False
        self._lock = threading.Lock()
        self.model_versions: list[str] = []
        self._append_locked({
            "event": "batch_reserved",
            "batch_id": batch_id,
            "run_id": run_id,
            "case_id": self.case_id,
            "variant": "typed-claims-v1",
            "model_id": MODEL,
            "max_total_http_requests": max_requests,
            "max_runtime_runs": max_runtime_runs,
            "input_tokens_per_request_observation_threshold": max_input_tokens_per_request,
            "token_usage_enforcement": "OBSERVE_ONLY",
            "cost_enforcement": "none",
            "utc": datetime.now(timezone.utc).isoformat(),
        })

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def run_finished(self) -> bool:
        return self._run_finished

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    def _append_locked(self, record: dict[str, Any]) -> None:
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        try:
            written = os.write(self._fd, payload)
            if written != len(payload):
                raise OSError("short append")
            os.fsync(self._fd)
        except OSError:
            self._halted = True
            raise SmokeBlocked("LEDGER_DURABILITY_FAILURE_STOP") from None

    @staticmethod
    def _confirmed_client_rejection(error: BaseException) -> int | None:
        """Recognize only an SDK client error carrying a completed HTTP response."""
        try:
            import httpx
            from google.genai.errors import ClientError
        except ImportError:
            return None
        if not isinstance(error, ClientError):
            return None
        status = getattr(error, "code", None)
        response = getattr(error, "response", None)
        if (status in {400, 401, 402, 403, 404, 422, 429}
                and isinstance(response, httpx.Response)
                and response.status_code == status):
            return status
        return None

    def request(
        self,
        operation: Callable[[], Any],
        *,
        request_kind: str,
        model: str,
        payload: Any,
        max_output_tokens: int | None = None,
    ) -> Any:
        if model != MODEL:
            raise SmokeBlocked("MODEL_MISMATCH_NO_FALLBACK")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise SmokeBlocked("REQUEST_PAYLOAD_NOT_HASHABLE") from None
        payload_hash = hashlib.sha256(encoded).hexdigest()
        with self._lock:
            if self._halted:
                raise SmokeBlocked("LEDGER_TERMINAL_STOP")
            if self._runtime_runs != 1:
                self._halted = True
                raise SmokeBlocked("RUNTIME_RUN_RESERVATION_REQUIRED")
            if self._in_flight:
                self._halted = True
                raise SmokeBlocked("CONCURRENT_PROVIDER_REQUEST_BLOCKED")
            if self._request_count >= self.max_requests:
                self._halted = True
                raise SmokeBlocked("TOTAL_PROVIDER_REQUEST_CAP_REACHED")
            sequence = self._request_count + 1
            self._request_count = sequence
            self._in_flight = True
            request_id = f"{self.run_id}-http-{sequence:02d}"
            self._append_locked({
                "event": "request_reserved",
                "batch_id": self.batch_id,
                "run_id": self.run_id,
                "case_id": self.case_id,
                "variant": "typed-claims-v1",
                "request_id": request_id,
                "request_sequence": sequence,
                "request_kind": request_kind,
                "requested_model_id": model,
                "prompt_sha256": payload_hash,
                "serialized_payload_bytes": len(encoded),
                "input_tokens_observation_threshold": self.max_input_tokens_per_request,
                "token_usage_enforcement": "OBSERVE_ONLY",
                "max_output_tokens": max_output_tokens,
                "utc": datetime.now(timezone.utc).isoformat(),
            })

        started = time.monotonic()
        try:
            response = operation()
        except BaseException as error:
            elapsed_ms = round((time.monotonic() - started) * 1000, 3)
            known_status = self._confirmed_client_rejection(error)
            with self._lock:
                self._in_flight = False
                self._halted = True
                self._append_locked({
                    "event": "request_finished",
                    "request_id": request_id,
                    "status": ("KNOWN_PROVIDER_FAILURE_TERMINAL" if known_status is not None
                               else "UNCERTAIN"),
                    "error_class": type(error).__name__,
                    **({"http_status": known_status, "response_received": True}
                       if known_status is not None else {}),
                    "operation_elapsed_ms": elapsed_ms,
                    "usage": None,
                    "utc": datetime.now(timezone.utc).isoformat(),
                })
            if known_status is not None:
                raise
            raise UncertainProviderRequest() from None

        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        try:
            receipt = _usage_receipt(response, request_kind)
        except BaseException as error:
            # The SDK call returned, but its response could not be accounted
            # for. Close the reservation without pretending that Usage exists.
            with self._lock:
                self._in_flight = False
                self._halted = True
                self._append_locked({
                    "event": "request_finished",
                    "request_id": request_id,
                    "status": "USAGE_UNKNOWN_TERMINAL",
                    "error_class": type(error).__name__,
                    "response_received": True,
                    "operation_elapsed_ms": elapsed_ms,
                    "usage": None,
                    "utc": datetime.now(timezone.utc).isoformat(),
                })
            raise SmokeBlocked("RESPONSE_USAGE_PARSE_FAILED_STOP") from None
        version = receipt.get("model_version")
        model_version_unavailable = request_kind == "generate_content" and not isinstance(version, str)
        identity_mismatch = isinstance(version, str) and not (
            version == MODEL or version.startswith(MODEL + "-")
        )
        with self._lock:
            self._in_flight = False
            if isinstance(version, str):
                self.model_versions.append(version)
            status = "SUCCESS_USAGE_REPORTED" if receipt["usage_status"] == "reported" else "USAGE_UNKNOWN_TERMINAL"
            if identity_mismatch:
                status = "MODEL_IDENTITY_MISMATCH_TERMINAL"
            if model_version_unavailable:
                status = "MODEL_VERSION_UNAVAILABLE_TERMINAL"
            if receipt["usage_status"] != "reported" or identity_mismatch or model_version_unavailable:
                self._halted = True
            self._append_locked({
                "event": "request_finished",
                "request_id": request_id,
                "requested_model_id": model,
                "provider_model_version": version,
                "status": status,
                "operation_elapsed_ms": elapsed_ms,
                "usage": receipt,
                "utc": datetime.now(timezone.utc).isoformat(),
            })
        if identity_mismatch:
            raise SmokeBlocked("PROVIDER_MODEL_IDENTITY_MISMATCH_STOP")
        if model_version_unavailable:
            raise SmokeBlocked("PROVIDER_MODEL_VERSION_UNAVAILABLE_STOP")
        if request_kind == "count_tokens" and receipt.get("counted_input_tokens") is None:
            raise SmokeBlocked("COUNT_TOKENS_USAGE_UNKNOWN_STOP")
        return response

    def finish_run(self, *, status: str, usage_summary: dict[str, Any]) -> None:
        with self._lock:
            if self._in_flight:
                self._halted = True
                raise SmokeBlocked("CANNOT_FINISH_RUN_WITH_PROVIDER_REQUEST_IN_FLIGHT")
            self._append_locked({
                "event": "run_finished",
                "batch_id": self.batch_id,
                "run_id": self.run_id,
                "case_id": self.case_id,
                "status": status,
                "provider_model_versions": list(self.model_versions),
                "usage_summary": usage_summary,
                "utc": datetime.now(timezone.utc).isoformat(),
            })
            self._run_finished = True

    def runtime_started(self) -> None:
        with self._lock:
            if self._halted or self._runtime_runs >= self.max_runtime_runs:
                self._halted = True
                raise SmokeBlocked("RUNTIME_RUN_CAP_REACHED")
            self._runtime_runs += 1
            self._append_locked({
                "event": "runtime_started",
                "batch_id": self.batch_id,
                "run_id": self.run_id,
                "case_id": self.case_id,
                "utc": datetime.now(timezone.utc).isoformat(),
            })

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass

def _assert_developer_api_sdk(client: Any) -> None:
    if getattr(client, "vertexai", None) is not False:
        raise SmokeBlocked("SDK_ENDPOINT_IDENTITY_MISMATCH")
    api_client = getattr(client, "_api_client", None)
    options = getattr(api_client, "_http_options", None)
    base_url = options.get("base_url") if isinstance(options, Mapping) else getattr(options, "base_url", None)
    try:
        parsed = urlsplit(base_url) if isinstance(base_url, str) else None
        valid = bool(
            parsed
            and parsed.scheme.casefold() == "https"
            and parsed.netloc.casefold() == "generativelanguage.googleapis.com"
            and parsed.hostname == "generativelanguage.googleapis.com"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise SmokeBlocked("SDK_ENDPOINT_IDENTITY_MISMATCH")

class LedgeredGeminiClient:
    """Usage-observation wrapper directly around the official SDK client."""

    def __init__(self, delegate: Any, ledger: AppendOnlyProviderLedger, *, transport_diagnostics: Any | None = None) -> None:
        self._delegate = delegate
        self._ledger = ledger
        self._transport_diagnostics = transport_diagnostics
        self.provider_calls = 0
        self.preflight_count_requests = 0
        self.count_tokens_preflight = False
        self.automatic_retry_count = 0
        self.last_guard_state = "READY"
        self.generation_usage_records: list[dict[str, Any]] = []
        self.reported_input_tokens = 0
        self.reported_output_tokens = 0
        self.reported_thinking_tokens: int | None = 0
        self.reported_cached_tokens: int | None = 0
        self.reported_billable_output_tokens = 0
        self.reported_cost_usd: float | None = None
        self.reported_count_cost_usd: float | None = None
        self.estimated_cost_usd: float | None = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def assert_developer_api(self) -> None:
        try:
            _assert_developer_api_sdk(self._delegate)
        except SmokeBlocked:
            self.last_guard_state = "SDK_ENDPOINT_IDENTITY_MISMATCH"
            raise

    def count_tokens(self, *, model: str, request: dict[str, Any]) -> Any:
        # The active G38 Runtime never calls countTokens. This method remains
        # only for the old isolated ledger contract tests in this module.
        models = getattr(self._delegate, "models", None)
        operation = getattr(models, "count_tokens", None)
        if not callable(operation):
            operation = getattr(self._delegate, "count_tokens", None)
        if not callable(operation):
            raise SmokeBlocked("COUNT_TOKENS_DISABLED_G38_USAGE_ONLY")
        response = self._ledger.request(
            lambda: operation(model=model, request=request),
            request_kind="count_tokens",
            model=model,
            payload=request,
        )
        self.preflight_count_requests += 1
        return response

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        if model != MODEL:
            self.last_guard_state = "MODEL_MISMATCH_NO_FALLBACK"
            raise SmokeBlocked("MODEL_MISMATCH_NO_FALLBACK")
        if getattr(self._delegate, "_api_client", None) is not None:
            self.assert_developer_api()
        models = getattr(self._delegate, "models", None)
        operation = getattr(models, "generate_content", None)
        if callable(operation):
            send = lambda: operation(model=model, contents=contents, config=config)
        else:
            # Legacy fake delegates remain available to the isolated unit
            # tests; the active G38 runner requires Client.models above.
            operation = getattr(self._delegate, "generate_content", None)
            if not callable(operation):
                raise SmokeBlocked("SDK_GENERATE_CONTENT_SURFACE_UNAVAILABLE")
            send = lambda: operation(model=model, contents=contents, config=config)

        if self._transport_diagnostics is not None and self._transport_diagnostics.enabled:
            from scripts.gemini_transport.product import diagnosed_generation
            original_send = send
            send = lambda: diagnosed_generation(original_send, self._transport_diagnostics, self._ledger)
        payload = {"model": model, "contents": contents, "config": config}
        started = time.monotonic()
        try:
            response = self._ledger.request(
                send,
                request_kind="generate_content",
                model=model,
                payload=payload,
                max_output_tokens=(
                    config.get("max_output_tokens")
                    if isinstance(config, Mapping)
                    else None
                ),
            )
        except SmokeBlocked as error:
            args = getattr(error, "args", ())
            code = args[0] if len(args) == 1 and isinstance(args[0], str) else ""
            self.last_guard_state = (
                code if code.isascii() and code.replace("_", "").isalnum()
                else "PROVIDER_REQUEST_STOP"
            )
            self.provider_calls = self._ledger.request_count
            raise

        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        receipt = _usage_receipt(response, "generate_content")
        complete = receipt.get("usage_status") == "reported"
        prompt = receipt.get("prompt_tokens")
        output = receipt.get("output_tokens")
        thinking = receipt.get("thinking_tokens")
        cached = receipt.get("cached_tokens")
        if prompt is not None:
            self.reported_input_tokens += prompt
        if output is not None:
            self.reported_output_tokens += output
            self.reported_billable_output_tokens += output
        if thinking is not None:
            self.reported_thinking_tokens = (self.reported_thinking_tokens or 0) + thinking
            self.reported_billable_output_tokens += thinking
        if cached is not None:
            self.reported_cached_tokens = (self.reported_cached_tokens or 0) + cached
        provider_cost = receipt.get("provider_reported_cost_usd")
        if provider_cost is not None:
            self.reported_cost_usd = (self.reported_cost_usd or 0.0) + provider_cost
        estimate = receipt.get("estimated_cost_usd")
        if estimate is not None:
            self.estimated_cost_usd = (self.estimated_cost_usd or 0.0) + estimate
        candidates = _read_field(response, "candidates")
        finish_reasons = []
        if isinstance(candidates, (list, tuple)):
            for candidate in candidates:
                reason = _read_field(candidate, "finish_reason", "finishReason")
                finish_reasons.append({"finish_reason": getattr(reason, "value", reason)})
        self.generation_usage_records.append({
            "sequence": self._ledger.request_count,
            "usage_status": "COMPLETE" if complete else "INCOMPLETE",
            "prompt_tokens": prompt,
            "reported_input_tokens": prompt,
            "output_tokens": output,
            "reported_output_tokens": output,
            "thinking_tokens": thinking,
            "reported_thinking_tokens": thinking,
            "cached_tokens": cached,
            "reported_cached_tokens": cached,
            "billable_output_estimate": output + (thinking or 0) if output is not None else None,
            "provider_reported_cost_usd": provider_cost,
            "estimated_cost_usd": estimate,
            "duration_ms": elapsed_ms,
            "response_replay_observation": {"candidates": finish_reasons},
        })
        self.provider_calls = self._ledger.request_count
        return response
