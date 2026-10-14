"""One-case Gemini 3.8 Typed Claims acceptance entry for frozen P14-CN2.

The real test is opt-in and separately authorized.  Its offline companion
checks only frozen inputs, the production RunRequest boundary and the
independent fail-closed accounting wrapper; it never reads a credential or
contacts Gemini.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import copy
from dataclasses import replace
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from linkloom.agents.providers.gemini_api import (
    GEMINI_38_FLASH_MODEL_ID,
    GeminiProviderAdapter,
    build_gemini_sdk_client,
)
from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.errors import ProviderOutcomeUnknownError
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault


REPO = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO / "work" / "GEMINI38_CN2_USAGE_ONLY_PROTOCOL.json"
PARENT_PROTOCOL_PATH = REPO / "work" / "phase14_gemini" / "PHASE14_PROTOCOL.json"
LEDGER_PATH = REPO / "work" / "GEMINI38_CN2_USAGE_ONLY_PROVIDER_LEDGER.jsonl"
ARTIFACT_ROOT = REPO / ".artifacts" / "gemini38_p14_cn2_typed"
G38_002_PROFILE = "G38-TYPED-CN2-002"
G38_004_PROFILE = "G38-TYPED-CN2-004"
G38_005_PROFILE = "G38-TYPED-CN2-005"
RUN_PROFILE_ENV = "LINKLOOM_G38_RUN_PROFILE"
G38_004_PROTOCOL_PATH = REPO / "work" / "GEMINI38_CN2_004_PROTOCOL.json"
G38_004_PROTOCOL_SHA256 = "a655993ff1b51dee36d3fe02b7b2f77ced75b73ca06fe98aa2caf9055a3570e8"
G38_004_LEDGER_PATH = REPO / "work" / "GEMINI38_CN2_004_PROVIDER_LEDGER.jsonl"
G38_004_ARTIFACT_RELATIVE_PATH = ".artifacts/gemini38_p14_cn2_typed/G38-TYPED-CN2-004"
G38_004_BATCH_ID = "gemini38-cn2-typed-20261011-g38-004"
G38_004_AUTHORIZATION_ENV = "LINKLOOM_G38_004_AUTHORIZATION"
G38_004_REQUIRED_AUTHORIZATION = "AUTHORIZE G38-TYPED-CN2-004 GEMINI 3.8"
G38_005_PROTOCOL_PATH = REPO / "work" / "GEMINI38_CN2_005_PROTOCOL.json"
G38_005_PROTOCOL_SHA256 = "50f806d9d10c81becb0ec631d7181e70a6bb29206d2a3eed288ebbc4487160c7"
G38_005_LEDGER_PATH = REPO / "work" / "GEMINI38_CN2_005_PROVIDER_LEDGER.jsonl"
G38_005_ARTIFACT_RELATIVE_PATH = ".artifacts/gemini38_p14_cn2_typed/G38-TYPED-CN2-005"
G38_005_BATCH_ID = "gemini38-cn2-typed-20261011-g38-005"
G38_005_AUTHORIZATION_ENV = "LINKLOOM_G38_005_AUTHORIZATION"
G38_005_REQUIRED_AUTHORIZATION = "AUTHORIZE G38-TYPED-CN2-005 GEMINI 3.8"
PARENT_PROTOCOL_SHA256 = "6aa5a84e910895838ed69d174c652a4d22fec7ed91e2c9e0bd08f8b678e61266"
PROTOCOL_SHA256 = "2b731e6984639c1464a8ed1226ab6d42ff31bd1e10c9af2d9d98886dc900a8e2"
MODEL = "gemini-3.8-flash"
MODEL_ENV = "LINKLOOM_GATE_A_MODEL"
FINAL_PROTOCOL = "claim-proposal/v1"
# New usage-only G38 runs leave per-response generation length to the Provider.
USAGE_ONLY_PROVIDER_DEFAULT_OUTPUT = True
INPUT_USD_PER_MILLION_TOKENS = 0.75
OUTPUT_USD_PER_MILLION_TOKENS = 3.75
PHASE9_GOLD_PATH = REPO / "work" / "phase9_gemini" / "PHASE9_GOLD.json"
SCORER_PATHS = (
    "scripts/score_phase11_gemini_eval.py",
    "scripts/score_phase13_gemini_eval.py",
)
OPT_IN_ENV = "LINKLOOM_RUN_P14_CN2_TYPED_GEMINI38"
AUTHORIZATION_ENV = "LINKLOOM_P14_CN2_TYPED_GEMINI38_AUTHORIZATION"
REQUIRED_AUTHORIZATION = "AUTHORIZE P14 CN2 TYPED GEMINI 3.8"


class SmokeBlocked(RuntimeError):
    """Safe fixed reason for a preflight or real-run stop."""


class UncertainProviderRequest(SmokeBlocked, ProviderOutcomeUnknownError):
    """Keep the smoke stop contract while exposing an exact Runtime category."""

    def __init__(self) -> None:
        ProviderOutcomeUnknownError.__init__(self)


_STARTUP_STAGES = {
    "FROZEN_INPUT_AUTHORIZATION_VALIDATION",
    "RETRIEVAL_MODEL_WARMUP",
    "SDK_CLIENT_INITIALIZATION",
    "LEDGER_CREATION_RECOVERY",
    "RUNTIME_INITIALIZATION",
    "RUNTIME_EXECUTION",
    "POST_RUN_SCORING",
}
_STAGE_ERROR_CODES = {
    "FROZEN_INPUT_AUTHORIZATION_VALIDATION": {
        "FROZEN_PROTOCOL_UNAVAILABLE", "P14_PROTOCOL_FINGERPRINT_MISMATCH",
        "NEW_PROTOCOL_FINGERPRINT_MISMATCH", "PHASE15_PROTOCOL_SCHEMA_MISMATCH",
        "PARENT_PROTOCOL_REFERENCE_MISMATCH", "MODEL_NOT_PINNED_TO_GEMINI_38_FLASH",
        "PROVIDER_OR_COMPARISON_POLICY_CHANGED", "TYPED_FINAL_PROTOCOL_NOT_PINNED",
        "TOKEN_ESTIMATION_MUST_NOT_BLOCK", "COST_MUST_BE_OBSERVATION_ONLY",
        "PROVIDER_AUTHORIZATION_GATE_MISSING", "WORKTREE_BASELINE_MISMATCH",
        "IMPLEMENTATION_FINGERPRINT_MISMATCH", "CASE_NOT_PINNED_TO_P14_CN2",
        "RUN_ID_NOT_PINNED", "BATCH_ID_NOT_PINNED", "INDEPENDENT_LEDGER_PATH_NOT_PINNED",
        "ARTIFACT_PATH_NOT_PINNED", "G38_RUN_PROFILE_NOT_PINNED",
        "EXACT_G38_004_AUTHORIZATION_REQUIRED",
        "EXACT_G38_005_AUTHORIZATION_REQUIRED",
        "FROZEN_QUESTION_FINGERPRINT_MISMATCH", "P14_CASE_OR_WORKSPACE_REFERENCE_MISMATCH",
        "FROZEN_WORKSPACE_SNAPSHOT_MISMATCH", "HISTORICAL_LEDGER_FINGERPRINT_MISMATCH",
        "PHASE14_HISTORICAL_ARTIFACT_FINGERPRINT_MISMATCH", "EXECUTION_LIMITS_NOT_PINNED",
        "REQUIRED_EVIDENCE_READS_NOT_PINNED", "FROZEN_GOLD_SCORER_FINGERPRINT_MISMATCH",
        "EXACT_P14_CN2_AUTHORIZATION_REQUIRED", "MODEL_ENV_MUST_EXPLICITLY_EQUAL_GEMINI_38_FLASH",
        "REAL_RUN_OPT_IN_REQUIRED",
    },
    "RETRIEVAL_MODEL_WARMUP": {"LOCAL_RETRIEVAL_MODEL_UNAVAILABLE_BEFORE_PROVIDER"},
    "SDK_CLIENT_INITIALIZATION": {
        "GEMINI_CREDENTIAL_UNAVAILABLE", "MODEL_MISMATCH_NO_FALLBACK",
        "SDK_CLIENT_INITIALIZATION_FAILED", "SDK_ENDPOINT_IDENTITY_MISMATCH",
    },
    "LEDGER_CREATION_RECOVERY": {
        "INDEPENDENT_LEDGER_ALREADY_EXISTS_STOP", "INDEPENDENT_LEDGER_CREATE_FAILED",
        "RUN_ARTIFACTS_ALREADY_EXIST_STOP",
    },
    "RUNTIME_INITIALIZATION": {
        "FROZEN_WORKSPACE_COPY_MISMATCH", "TYPED_FINAL_NOT_ACTIVE_AT_RUNTIME_BOUNDARY",
        "RUNTIME_INITIALIZATION_FAILED",
    },
    "RUNTIME_EXECUTION": {
        "FROZEN_WORKSPACE_CHANGED_DURING_RUN", "RUNTIME_STATE_NOT_PERSISTED",
        "CREDENTIAL_LEAK_BLOCKED", "RUNTIME_EXECUTION_FAILED", "RUNTIME_DID_NOT_COMPLETE",
        "REVIEWER_DID_NOT_ACCEPT", "PROVIDER_LEDGER_TERMINAL",
    },
    "POST_RUN_SCORING": {"GOLD_SCORING_FAILED"},
}


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def _safe_startup_error_code(stage: str, error: BaseException) -> str:
    if stage not in _STARTUP_STAGES or not isinstance(error, SmokeBlocked):
        return "UNKNOWN_STARTUP_ERROR"
    args = getattr(error, "args", ())
    code = args[0] if len(args) == 1 else None
    return code if isinstance(code, str) and code in _STAGE_ERROR_CODES.get(stage, set()) else "UNKNOWN_STARTUP_ERROR"


def _write_startup_diagnostic(
    root: Path, *, run_id: str, stage: str, error: BaseException, provider_requests: int
) -> Path:
    """Persist only allowlisted startup identity and error codes."""
    safe_run_id = run_id if isinstance(run_id, str) and run_id and all(ch.isalnum() or ch in "-_" for ch in run_id) else "UNKNOWN_RUN"
    safe_stage = stage if stage in _STARTUP_STAGES else "UNKNOWN_STAGE"
    request_count = provider_requests if isinstance(provider_requests, int) and not isinstance(provider_requests, bool) and provider_requests >= 0 else 0
    value = {
        "run_id": safe_run_id,
        "stage": safe_stage,
        "error_code": _safe_startup_error_code(safe_stage, error),
        "terminal_status": "BLOCKED_BEFORE_PROVIDER" if request_count == 0 else "FAILED_AFTER_PROVIDER",
        "provider_requests": request_count,
    }
    target_root = root / "startup_diagnostics"
    target_root.mkdir(parents=True, exist_ok=True)
    for sequence in range(1, 1000):
        path = target_root / f"{safe_run_id}-startup-{sequence:03d}.json"
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError:
            raise SmokeBlocked("STARTUP_DIAGNOSTIC_PERSIST_FAILED") from None
        try:
            payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            if os.write(fd, payload) != len(payload):
                raise OSError("short diagnostic write")
            os.fsync(fd)
        except OSError:
            raise SmokeBlocked("STARTUP_DIAGNOSTIC_PERSIST_FAILED") from None
        finally:
            os.close(fd)
        return path
    raise SmokeBlocked("STARTUP_DIAGNOSTIC_PATH_EXHAUSTED")


def _harness():
    return importlib.import_module("tests.smoke.test_m12_real_provider_team_decision_smoke")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_profile_config(profile: str) -> dict[str, Any]:
    if profile == G38_002_PROFILE:
        return {
            "profile": profile,
            "protocol_path": PROTOCOL_PATH,
            "protocol_sha256": PROTOCOL_SHA256,
            "batch_id": "gemini38-cn2-usage-only-20261011-a",
            "run_id": G38_002_PROFILE,
            "ledger_path": LEDGER_PATH,
            "legacy_opt_in_required": True,
            "artifact_relative_path": f".artifacts/gemini38_p14_cn2_typed/{G38_002_PROFILE}",
        }
    if profile == G38_004_PROFILE:
        return {
            "profile": profile,
            "protocol_path": G38_004_PROTOCOL_PATH,
            "protocol_sha256": G38_004_PROTOCOL_SHA256,
            "batch_id": G38_004_BATCH_ID,
            "run_id": G38_004_PROFILE,
            "ledger_path": G38_004_LEDGER_PATH,
            "legacy_opt_in_required": False,
            "artifact_relative_path": G38_004_ARTIFACT_RELATIVE_PATH,
            "authorization_environment_variable": G38_004_AUTHORIZATION_ENV,
            "required_authorization": G38_004_REQUIRED_AUTHORIZATION,
        }
    if profile == G38_005_PROFILE:
        return {
            "profile": profile,
            "protocol_path": G38_005_PROTOCOL_PATH,
            "protocol_sha256": G38_005_PROTOCOL_SHA256,
            "batch_id": G38_005_BATCH_ID,
            "run_id": G38_005_PROFILE,
            "ledger_path": G38_005_LEDGER_PATH,
            "legacy_opt_in_required": False,
            "artifact_relative_path": G38_005_ARTIFACT_RELATIVE_PATH,
            "authorization_environment_variable": G38_005_AUTHORIZATION_ENV,
            "required_authorization": G38_005_REQUIRED_AUTHORIZATION,
        }
    raise SmokeBlocked("G38_RUN_PROFILE_NOT_PINNED")


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _read_protocol(profile: str = G38_002_PROFILE) -> tuple[dict[str, Any], dict[str, Any]]:
    profile_config = _run_profile_config(profile)
    protocol_path = profile_config["protocol_path"]
    try:
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        parent = json.loads(PARENT_PROTOCOL_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SmokeBlocked("FROZEN_PROTOCOL_UNAVAILABLE") from None
    if _sha256(PARENT_PROTOCOL_PATH) != PARENT_PROTOCOL_SHA256:
        raise SmokeBlocked("P14_PROTOCOL_FINGERPRINT_MISMATCH")
    if _sha256(protocol_path) != profile_config["protocol_sha256"]:
        raise SmokeBlocked("NEW_PROTOCOL_FINGERPRINT_MISMATCH")
    if protocol.get("schema_version") != "gemini38-typed-cn2-protocol/v1":
        raise SmokeBlocked("PHASE15_PROTOCOL_SCHEMA_MISMATCH")
    if protocol.get("batch_id") != profile_config["batch_id"]:
        raise SmokeBlocked("BATCH_ID_NOT_PINNED")
    if protocol.get("parent_protocol", {}).get("sha256") != PARENT_PROTOCOL_SHA256:
        raise SmokeBlocked("PARENT_PROTOCOL_REFERENCE_MISMATCH")
    if protocol.get("provider", {}).get("model_id") != MODEL:
        raise SmokeBlocked("MODEL_NOT_PINNED_TO_GEMINI_38_FLASH")
    if (
        protocol.get("provider", {}).get("fallback") != "forbidden"
        or protocol.get("provider", {}).get("sdk_version") != "2.22.0"
        or protocol.get("parent_protocol", {}).get("strict_comparability") is not False
        or protocol.get("comparison_and_acceptance", {}).get("strict_phase14_comparison") is not False
    ):
        raise SmokeBlocked("PROVIDER_OR_COMPARISON_POLICY_CHANGED")
    if protocol.get("provider", {}).get("final_protocol") != FINAL_PROTOCOL:
        raise SmokeBlocked("TYPED_FINAL_PROTOCOL_NOT_PINNED")
    if (
        protocol.get("provider", {}).get("count_tokens_preflight") is not False
        or protocol.get("token_accounting", {}).get("mode") != "provider_usage_only"
        or protocol.get("token_accounting", {}).get("count_tokens_preflight") is not False
        or protocol.get("token_accounting", {}).get("enforcement")
        != "provider_reported_usage_only"
        or protocol.get("token_accounting", {}).get("estimate_mismatch_stops_runtime") is not False
    ):
        raise SmokeBlocked("TOKEN_ESTIMATION_MUST_NOT_BLOCK")
    cost_observation = protocol.get("cost_observation", {})
    if (
        "cost_guard" in protocol
        or cost_observation.get("enforcement") != "none"
        or cost_observation.get("record_usage_and_estimate") is not True
        or cost_observation.get("input_usd_per_million_tokens") != INPUT_USD_PER_MILLION_TOKENS
        or cost_observation.get("output_usd_per_million_tokens_including_thinking") != OUTPUT_USD_PER_MILLION_TOKENS
    ):
        raise SmokeBlocked("COST_MUST_BE_OBSERVATION_ONLY")
    if (
        protocol.get("authorization_status") != "PREPARED_WAITING_FOR_NEW_EXPLICIT_USER_AUTHORIZATION"
        or protocol.get("provider_calls_require_new_explicit_user_authorization") is not True
    ):
        raise SmokeBlocked("PROVIDER_AUTHORIZATION_GATE_MISSING")
    if protocol.get("head") != "bf3aef2fa423968006b337eb4b4cd68ce7b3b2e7":
        raise SmokeBlocked("WORKTREE_BASELINE_MISMATCH")
    for relative, expected in protocol.get("implementation_fingerprint", {}).items():
        path = REPO / relative
        if not path.is_file() or _sha256(path) != expected:
            raise SmokeBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH")
    if protocol.get("case", {}).get("case_id") != "P0_CN2":
        raise SmokeBlocked("CASE_NOT_PINNED_TO_P14_CN2")
    if protocol.get("case", {}).get("run_id") != profile_config["run_id"]:
        raise SmokeBlocked("RUN_ID_NOT_PINNED")
    if protocol.get("independent_accounting", {}).get("ledger_path") != str(
        profile_config["ledger_path"].relative_to(REPO).as_posix()
    ):
        raise SmokeBlocked("INDEPENDENT_LEDGER_PATH_NOT_PINNED")
    if profile in {G38_004_PROFILE, G38_005_PROFILE}:
        if protocol.get("independent_accounting", {}).get("artifact_path") != profile_config[
            "artifact_relative_path"
        ]:
            raise SmokeBlocked("ARTIFACT_PATH_NOT_PINNED")
        if protocol.get("authorization_binding") != {
            "environment_variable": profile_config["authorization_environment_variable"],
            "exact_value": profile_config["required_authorization"],
            "run_id": profile_config["run_id"],
            "runtime_runs": 1,
        }:
            raise SmokeBlocked("PROVIDER_AUTHORIZATION_GATE_MISSING")

    case = protocol["case"]
    question = case.get("question")
    if not isinstance(question, str) or hashlib.sha256(question.encode("utf-8")).hexdigest() != case.get("question_sha256"):
        raise SmokeBlocked("FROZEN_QUESTION_FINGERPRINT_MISMATCH")
    parent_case = parent.get("fingerprint", {}).get("cases", {}).get("P0_CN2", {})
    if (
        parent_case.get("question_sha256") != case.get("question_sha256")
        or parent_case.get("source") != case.get("workspace_snapshot")
        or parent_case.get("source_sha256") != case.get("workspace_files_sha256")
    ):
        raise SmokeBlocked("P14_CASE_OR_WORKSPACE_REFERENCE_MISMATCH")

    source = (REPO / case["workspace_snapshot"]).resolve()
    if not source.is_dir() or _file_hashes(source) != case.get("workspace_files_sha256"):
        raise SmokeBlocked("FROZEN_WORKSPACE_SNAPSHOT_MISMATCH")
    for relative, expected in parent.get("fingerprint", {}).get("historical_ledger_sha256", {}).items():
        historical_path = REPO / relative
        if not historical_path.is_file() or _sha256(historical_path) != expected:
            raise SmokeBlocked("HISTORICAL_LEDGER_FINGERPRINT_MISMATCH")
    for relative, expected in protocol.get("phase14_historical_artifact_sha256", {}).items():
        historical_path = REPO / relative
        if not historical_path.is_file() or _sha256(historical_path) != expected:
            raise SmokeBlocked("PHASE14_HISTORICAL_ARTIFACT_FINGERPRINT_MISMATCH")

    limits = protocol.get("execution_limits", {})
    if (
        limits.get("runtime_runs") != 1
        or limits.get("runtime_steps") != 18
        or limits.get("generation_requests") != 18
        or limits.get("count_tokens_requests") != 0
        or limits.get("total_http_requests_including_preflight") != 18
        or limits.get("request_timeout_seconds") != 130
        or limits.get("automatic_retries") != 0
        or limits.get("concurrency") != 1
        or limits.get("outer_process_timeout_seconds") != 1900
        or protocol.get("provider", {}).get("timeout_seconds_per_request") != 130
    ):
        raise SmokeBlocked("EXECUTION_LIMITS_NOT_PINNED")
    token_limit_fields = (
        "input_tokens_per_generation_request",
        "aggregate_generation_input_tokens",
        "max_output_tokens_per_request",
        "aggregate_output_tokens_including_thinking",
    )
    if profile == G38_002_PROFILE:
        if (
            limits.get("input_tokens_per_generation_request") != 12000
            or limits.get("aggregate_generation_input_tokens") != 64000
            or limits.get("aggregate_counted_input_tokens") != 0
            or limits.get("max_output_tokens_per_request") != 512
            or limits.get("aggregate_output_tokens_including_thinking") != 4096
        ):
            raise SmokeBlocked("EXECUTION_LIMITS_NOT_PINNED")
    elif (
        any(limits.get(field) is not None for field in token_limit_fields)
        or limits.get("aggregate_counted_input_tokens") != 0
    ):
        raise SmokeBlocked("EXECUTION_LIMITS_NOT_PINNED")
    if case.get("required_verified_read_suffixes") != ["owner.md", "leader.md"]:
        raise SmokeBlocked("REQUIRED_EVIDENCE_READS_NOT_PINNED")
    frozen_scorers = parent.get("fingerprint", {}).get("phase14_current_files_sha256", {})
    for relative in SCORER_PATHS:
        path = REPO / relative
        if not path.is_file() or _sha256(path) != frozen_scorers.get(relative):
            raise SmokeBlocked("FROZEN_GOLD_SCORER_FINGERPRINT_MISMATCH")
    return protocol, parent


def _new_run_request(protocol: Mapping[str, Any]) -> RunRequest:
    case = protocol["case"]
    return RunRequest(
        request_id=case["run_id"],
        thread_id=case["run_id"],
        workflow="team_decision",
        query=case["question"],
        max_steps=protocol["execution_limits"]["runtime_steps"],
        max_provider_requests=protocol["execution_limits"]["generation_requests"],
        planning_mode="template",
        dry_run=True,
        final_protocol=FINAL_PROTOCOL,
    )


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


def _score_gold_after_provider_stop(
    ledger: AppendOnlyProviderLedger,
    observed: dict[str, Any],
    gold_path: Path,
    expected_gold_sha256: str,
) -> dict[str, Any]:
    """Read frozen Gold only after the provider ledger records a stopped run."""

    if not ledger.run_finished or ledger.in_flight:
        raise SmokeBlocked("GOLD_SCORING_REQUIRES_FINISHED_RUN")
    if not gold_path.is_file() or _sha256(gold_path) != expected_gold_sha256:
        raise SmokeBlocked("FROZEN_GOLD_FINGERPRINT_MISMATCH")

    parent = json.loads(PARENT_PROTOCOL_PATH.read_text(encoding="utf-8"))
    frozen = parent.get("fingerprint", {}).get("phase14_current_files_sha256", {})
    for relative in SCORER_PATHS:
        path = REPO / relative
        if not path.is_file() or _sha256(path) != frozen.get(relative):
            raise SmokeBlocked("FROZEN_GOLD_SCORER_FINGERPRINT_MISMATCH")

    # The historical scorer module is imported and Gold is opened only here,
    # after the runtime and all provider requests have stopped.
    scorer = importlib.import_module("scripts.score_phase13_gemini_eval")
    gold_document = json.loads(gold_path.read_text(encoding="utf-8"))
    gold_case = dict(gold_document["P0_CN2"])
    gold_case["required_source_suffixes"] = list(scorer.REQUIRED_READS["P0_CN2"])
    return scorer._score_frozen_shape(observed, gold_case)


def _attach_gold_outcome(
    summary: dict[str, Any],
    *,
    actual_models: list[str],
    runtime_status: str | None,
    decision_root: dict[str, Any] | None,
    reviewer_decision: str | None,
    score: dict[str, Any] | None,
) -> dict[str, Any]:
    """Record model identity and keep an unscorable runtime failure distinct from a wrong answer."""
    actual_model_id_confirmed = bool(actual_models) and all(
        version == MODEL or version.startswith(MODEL + "-")
        for version in actual_models
    )
    decision = (
        decision_root.get("decision")
        if isinstance(decision_root, dict)
        and isinstance(decision_root.get("decision"), dict)
        else None
    )
    frozen_label = score.get("label") if isinstance(score, dict) else None
    scorable = frozen_label not in (None, "MODEL_ERROR", "NOT_EVALUATED")
    reason = None if scorable else "NO_SCORABLE_FINAL" if score is not None else "SCORER_NOT_RUN"
    retrieval_ok = bool(
        isinstance(score, dict) and score.get("required_verified_reads_present")
    )
    schema_ok = (
        runtime_status == "completed"
        and isinstance(decision_root, dict)
        and decision_root.get("schema_version") == "team-decision-result/v1"
    )
    reviewer_accepted = reviewer_decision == "evidence_sufficient"
    evidence_ok = (
        reviewer_accepted
        and isinstance(decision, dict)
        and decision.get("status") == "approved"
    )
    summary.update({
        "actual_model_id_confirmed": actual_model_id_confirmed,
        "retrieval_and_required_evidence_reads_succeeded": retrieval_ok,
        "typed_claim_schema_passed": schema_ok,
        "evidence_validation_passed": evidence_ok,
        "reviewer_accepted": reviewer_accepted,
        "frozen_gold_scorer_label": frozen_label,
        "independent_gold_label": frozen_label if scorable else "NOT_EVALUATED",
        "gold_scorer_invoked": score is not None,
        "gold_accuracy_scored": scorable,
        "gold_scoring_status": "SCORED" if scorable else "NOT_EVALUATED",
        "gold_not_evaluated_reason": reason,
        "gold_read_by_runtime": False,
        "task_success": bool(
            actual_model_id_confirmed
            and retrieval_ok
            and schema_ok
            and evidence_ok
            and frozen_label == "ANSWERED_CORRECT"
        ),
    })
    return summary


def _runtime_failure_type(run_status: Any, state: Any, exception: BaseException | None = None) -> str | None:
    for owner in (run_status, state):
        error = getattr(owner, "error", None) if owner is not None else None
        if callable(getattr(error, "to_dict", None)):
            error = error.to_dict()
        code = error.get("code") if isinstance(error, dict) else getattr(error, "code", None)
        if isinstance(code, str) and code and all(ch.isalnum() or ch == "_" for ch in code):
            return code[:96]
    if exception is not None:
        name = type(exception).__name__
        if name and all(ch.isalnum() or ch == "_" for ch in name):
            return name[:96]
    return None


def _provider_output_diagnostics(generation_usage_records: Any) -> dict[str, Any]:
    finish_reasons: list[dict[str, Any]] = []
    if isinstance(generation_usage_records, list):
        for record in generation_usage_records:
            if not isinstance(record, Mapping):
                continue
            observation = record.get("response_replay_observation")
            if not isinstance(observation, Mapping):
                continue
            candidates = observation.get("candidates")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                reason = candidate.get("finish_reason")
                if not isinstance(reason, str) or not reason.strip():
                    continue
                normalized = reason.rsplit(".", 1)[-1].strip().upper()
                finish_reasons.append({
                    "sequence": record.get("sequence"),
                    "finish_reason": normalized,
                })
    return {
        "provider_finish_reasons": finish_reasons,
        "provider_output_truncated_by_max_tokens": any(
            item["finish_reason"] == "MAX_TOKENS" for item in finish_reasons
        ),
    }


def _provider_usage_complete(guarded_client: Any) -> bool:
    records = getattr(guarded_client, "generation_usage_records", [])
    return bool(records) and all(
        isinstance(record, Mapping) and record.get("usage_status") == "COMPLETE"
        for record in records
    )


def _reported_usage_total(guarded_client: Any, field_name: str) -> int | None:
    value = getattr(guarded_client, field_name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    if _provider_usage_complete(guarded_client) or value > 0:
        return value
    records = getattr(guarded_client, "generation_usage_records", [])
    if any(
        isinstance(record, Mapping)
        and isinstance(record.get(field_name), int)
        and not isinstance(record.get(field_name), bool)
        and record[field_name] >= 0
        for record in records
    ):
        return value
    return None


def _conservative_cost_observation(guarded_client: Any) -> float | None:
    if not _provider_usage_complete(guarded_client):
        return None
    harness = _harness()
    input_tokens = (
        guarded_client.estimated_input_tokens
        if guarded_client.count_tokens_preflight
        else guarded_client.reported_input_tokens
    )
    return harness._combined_cost_usd(
        input_tokens,
        guarded_client.preflight_counted_input_tokens,
        guarded_client.reported_billable_output_tokens
        + guarded_client.reserved_unknown_output_tokens,
    )


def _write_failure_terminal_summary(
    case_root: Path,
    *,
    run_id: str,
    case_id: str,
    ledger: AppendOnlyProviderLedger,
    guarded_client: Any | None,
    runtime_status: str | None,
    failure_type: str | None,
    state: Any = None,
    score: dict[str, Any] | None = None,
) -> Path:
    """Persist a safe summary even when Runtime exits before normal scoring."""
    status = runtime_status or getattr(state, "status", None) or "INTERRUPTED_OR_BLOCKED"
    summary = {
        "run_id": run_id,
        "case_id": case_id,
        "requested_model_id": MODEL,
        "provider_model_versions": list(ledger.model_versions),
        "final_protocol": FINAL_PROTOCOL,
        "runtime_status": status,
        "runtime_failure_type": failure_type,
        "guard_stop_reason": (
            guarded_client.last_guard_state if guarded_client is not None else "NOT_INITIALIZED"
        ),
        "token_usage_enforcement": "OBSERVE_ONLY",
        "provider_usage_completeness": (
            "COMPLETE"
            if guarded_client is not None and _provider_usage_complete(guarded_client)
            else "INCOMPLETE_OR_UNAVAILABLE"
        ),
        "provider_http_requests_including_count_tokens": ledger.request_count,
        "provider_usage_terminal_stop": ledger.halted,
        "provider_requests": guarded_client.provider_calls if guarded_client is not None else 0,
        "count_tokens_requests": (
            guarded_client.preflight_count_requests if guarded_client is not None else 0
        ),
        "count_tokens_preflight_enabled": (
            guarded_client.count_tokens_preflight if guarded_client is not None else False
        ),
        "input_tokens_reported": (
            _reported_usage_total(guarded_client, "reported_input_tokens")
            if guarded_client is not None else None
        ),
        "output_tokens_reported": (
            _reported_usage_total(guarded_client, "reported_output_tokens")
            if guarded_client is not None else None
        ),
        "thinking_tokens_reported": (
            _reported_usage_total(guarded_client, "reported_thinking_tokens")
            if guarded_client is not None else None
        ),
        "cached_tokens_reported": (
            _reported_usage_total(guarded_client, "reported_cached_tokens")
            if guarded_client is not None else None
        ),
        "cost_usd_reported": (
            guarded_client.reported_cost_usd if guarded_client is not None else None
        ),
        "generation_usage_records": (
            copy.deepcopy(guarded_client.generation_usage_records)
            if guarded_client is not None else []
        ),
        **_provider_output_diagnostics(
            guarded_client.generation_usage_records if guarded_client is not None else []
        ),
        "tool_ledger_count": len(getattr(state, "tool_ledger", [])) if state is not None else 0,
        "gold_read_by_runtime": False,
        "independent_gold_scorer": "frozen-phase14-phase13-phase11-shape",
    }
    _attach_gold_outcome(
        summary,
        actual_models=list(ledger.model_versions),
        runtime_status=runtime_status,
        decision_root=None,
        reviewer_decision=None,
        score=score,
    )
    if score is None:
        summary["independent_gold_label"] = "NOT_EVALUATED"
        summary["gold_scoring_status"] = "NOT_EVALUATED"
        summary["gold_accuracy_scored"] = False
        summary["gold_not_evaluated_reason"] = "RUN_DID_NOT_REACH_SCORER"
    path = case_root / "typed_final_runtime_summary.json"
    _harness()._write_json(path, summary)
    return path


def _preflight_local_retrieval_model() -> None:
    """Warm the production embedder in offline-only mode before any Gemini call."""

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        backend = importlib.import_module("linkloom.retrieval_v2.runtime_backend")
        encoded = backend._DEFAULT_RUNTIME_EMBEDDER.encode(["Linkloom retrieval readiness check"])
        import numpy as np

        vector = np.asarray(encoded)
        if vector.ndim != 2 or vector.shape[0] != 1 or vector.shape[1] == 0:
            raise ValueError
        if not np.isfinite(vector).all() or not np.any(vector):
            raise ValueError
    except Exception:
        raise SmokeBlocked("LOCAL_RETRIEVAL_MODEL_UNAVAILABLE_BEFORE_PROVIDER") from None


def _frozen_score_input(observed_summary: dict[str, Any], run_result: dict[str, Any] | None,
                        state: Any, run_status: Any, provider_requests: int,
                        conservative_cost_usd: float) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    trajectory = observed_summary.get("trajectory", {})
    for raw in trajectory.get("tool_calls", []):
        arguments = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        raw_result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        value = raw_result.get("value")
        values = value if isinstance(value, list) else [value]
        candidates = [
            {
                key: row[key]
                for key in ("relative_path", "evidence_id")
                if isinstance(row, dict) and key in row
            }
            for row in values
            if isinstance(row, dict)
        ]
        actions.append({
            "tool_id": raw.get("tool_id"),
            "status": raw.get("status"),
            "query": arguments.get("query"),
            "candidates_or_read": candidates,
            "error": raw.get("error"),
        })
    result_root = run_result.get("result") if isinstance(run_result, dict) else None
    if isinstance(result_root, dict) and isinstance(result_root.get("team_decision"), dict):
        decision = result_root["team_decision"]
    else:
        decision = result_root if isinstance(result_root, dict) else None
    state_error = getattr(state, "error", None)
    if state_error is not None and callable(getattr(state_error, "to_dict", None)):
        state_error = state_error.to_dict()
    return {
        "run_id": getattr(run_status, "run_id", None),
        "case_id": "P0_CN2",
        "variant": "typed-claims-v1",
        "group_id": "gemini38-p14-cn2-typed",
        "status": getattr(run_status, "status", None) or getattr(state, "status", None),
        "error": state_error,
        "decision": decision,
        "tool_actions": actions,
        "provider_requests": provider_requests,
        "tool_action_count": len(actions),
        "model_turns": len(getattr(state, "model_executions", [])),
        "conservative_usd": conservative_cost_usd,
    }


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

    def __init__(self, delegate: Any, ledger: AppendOnlyProviderLedger) -> None:
        self._delegate = delegate
        self._ledger = ledger
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


def _new_usage_only_guard(harness: Any, delegate: Any, **kwargs: Any) -> Any:
    """Reuse the M1.2 request/time guard while making its token budgets observational."""

    if kwargs.get("count_tokens_preflight") is not False:
        raise SmokeBlocked("G38_USAGE_ONLY_MUST_NOT_ENABLE_COUNT_TOKENS")

    class ProviderUsageOnlyGuard(harness.BudgetedGeminiClient):
        def _terminal_block(self) -> None:
            if self._terminal:
                reason = (
                    self.last_guard_state
                    if isinstance(self.last_guard_state, str) and self.last_guard_state
                    else "G38_USAGE_ONLY_GUARD_TERMINAL"
                )
                raise harness.SmokeBlocked(reason)

        def _record_billable_output_usage(
            self,
            output_tokens: int | None,
            thinking_tokens: int | None,
        ) -> None:
            if output_tokens is not None:
                super()._record_billable_output_usage(output_tokens, thinking_tokens)
                return
            # Usage is incomplete, so account only for tokens the Provider did
            # report. Do not turn the legacy 512 cap into fictitious usage.
            if thinking_tokens is not None:
                self.reported_billable_output_tokens += thinking_tokens
                self.aggregate.reported_billable_output_tokens += thinking_tokens

        def generate_content(self, *, model: str, contents: list[dict[str, Any]],
                            config: dict[str, Any]) -> Any:
            original_case_budget = self.case_budget
            original_aggregate_budget = self.aggregate_budget
            # Infinity is a transient sentinel for disabled token enforcement,
            # not a replacement cap. Restore the frozen values before Runtime
            # summaries or ledgers can observe these temporary comparisons.
            self.case_budget = replace(
                original_case_budget,
                max_input_tokens=math.inf,
                max_output_tokens=math.inf,
                per_request_input_tokens=math.inf,
            )
            self.aggregate_budget = replace(
                original_aggregate_budget,
                max_input_tokens=math.inf,
                max_output_tokens=math.inf,
            )
            try:
                response = super().generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            finally:
                self.case_budget = original_case_budget
                self.aggregate_budget = original_aggregate_budget
            if self.last_guard_state == "RESPONSE_ACCEPTED_USAGE_INCOMPLETE":
                self.last_guard_state = "PROVIDER_USAGE_INCOMPLETE_TERMINAL"
            return response

    return ProviderUsageOnlyGuard(delegate, **kwargs)



def _team_decision_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    root = result.get("result") if isinstance(result, dict) else None
    if isinstance(root, dict) and isinstance(root.get("team_decision"), dict):
        return root["team_decision"]
    return root if isinstance(root, dict) else None


def _tool_action_candidates(record: Any) -> list[dict[str, Any]]:
    raw_result = record.result if isinstance(record.result, dict) else {}
    raw_value = raw_result.get("value")
    values = raw_value if isinstance(raw_value, list) else [raw_value]
    return [
        {key: row[key] for key in ("relative_path", "evidence_id") if key in row}
        for row in values
        if isinstance(row, dict)
    ]


def _run_real_case(
    *,
    _ledger_path: Path | None = None,
    _artifact_root: Path | None = None,
    _http_transport: Any | None = None,
    _profile: str | None = None,
) -> Path:
    """Run frozen P14 through the production SDK, Adapter, Runtime and scorer.

    Offline tests may substitute only the SDK's HTTP transport and output
    locations. The question, workspace, RunRequest, product execution chain,
    Ledger rules and post-run Gold scorer remain unchanged.
    """
    stage = "FROZEN_INPUT_AUTHORIZATION_VALIDATION"
    protocol: dict[str, Any] | None = None
    parent: dict[str, Any] | None = None
    selected_profile = (
        _profile if _profile is not None
        else os.environ.get(RUN_PROFILE_ENV, G38_002_PROFILE)
    )
    try:
        profile_config = _run_profile_config(selected_profile)
    except SmokeBlocked:
        profile_config = None
    run_id = profile_config["run_id"] if profile_config is not None else "UNKNOWN_RUN"
    ledger: AppendOnlyProviderLedger | None = None
    sdk_client: Any | None = None
    client: LedgeredGeminiClient | None = None
    run_root: Path | None = None
    case_root: Path | None = None
    run_status = None
    state = None
    score: dict[str, Any] | None = None
    root = _artifact_root or ARTIFACT_ROOT
    ledger_path = _ledger_path or (
        profile_config["ledger_path"] if profile_config is not None else LEDGER_PATH
    )
    try:
        if profile_config is None:
            raise SmokeBlocked("G38_RUN_PROFILE_NOT_PINNED")
        protocol, parent = _read_protocol(selected_profile)
        case = protocol["case"]
        limits = protocol["execution_limits"]
        run_id = case["run_id"]
        if profile_config["legacy_opt_in_required"]:
            if os.environ.get(OPT_IN_ENV) != "1":
                raise SmokeBlocked("REAL_RUN_OPT_IN_REQUIRED")
            if os.environ.get(AUTHORIZATION_ENV) != REQUIRED_AUTHORIZATION:
                raise SmokeBlocked("EXACT_P14_CN2_AUTHORIZATION_REQUIRED")
        elif os.environ.get(profile_config["authorization_environment_variable"]) != profile_config[
            "required_authorization"
        ]:
            raise SmokeBlocked(
                "EXACT_G38_005_AUTHORIZATION_REQUIRED"
                if selected_profile == G38_005_PROFILE
                else "EXACT_G38_004_AUTHORIZATION_REQUIRED"
            )
        if os.environ.get(MODEL_ENV) != MODEL:
            raise SmokeBlocked("MODEL_ENV_MUST_EXPLICITLY_EQUAL_GEMINI_38_FLASH")
        if limits["generation_requests"] != limits["total_http_requests_including_preflight"]:
            raise SmokeBlocked("EXECUTION_LIMITS_NOT_PINNED")
        run_root = (
            REPO / protocol["independent_accounting"]["artifact_path"]
            if _artifact_root is None and selected_profile in {G38_004_PROFILE, G38_005_PROFILE}
            else root / run_id
        )
        stage = "LEDGER_CREATION_RECOVERY"
        if ledger_path.exists():
            raise SmokeBlocked("INDEPENDENT_LEDGER_ALREADY_EXISTS_STOP")
        if run_root.exists():
            raise SmokeBlocked("RUN_ARTIFACTS_ALREADY_EXIST_STOP")

        stage = "RETRIEVAL_MODEL_WARMUP"
        _preflight_local_retrieval_model()

        stage = "SDK_CLIENT_INITIALIZATION"
        api_key = os.environ.get("GEMINI_API_KEY")
        if not isinstance(api_key, str) or not api_key.strip():
            raise SmokeBlocked("GEMINI_CREDENTIAL_UNAVAILABLE")
        if os.environ.get(MODEL_ENV) != MODEL:
            raise SmokeBlocked("MODEL_MISMATCH_NO_FALLBACK")
        try:
            from google.genai import types as genai_types

            http_options: dict[str, Any] = {
                "timeout": int(limits["request_timeout_seconds"] * 1000),
                "retry_options": genai_types.HttpRetryOptions(attempts=1),
            }
            if _http_transport is not None:
                http_options["client_args"] = {"transport": _http_transport}
            sdk_client = build_gemini_sdk_client(
                api_key,
                http_options_kwargs=http_options,
            )
            _assert_developer_api_sdk(sdk_client)
        except SmokeBlocked:
            raise
        except Exception:
            raise SmokeBlocked("SDK_CLIENT_INITIALIZATION_FAILED") from None

        stage = "LEDGER_CREATION_RECOVERY"
        ledger = AppendOnlyProviderLedger(
            ledger_path,
            batch_id=protocol["batch_id"],
            run_id=run_id,
            max_requests=limits["total_http_requests_including_preflight"],
            max_runtime_runs=limits["runtime_runs"],
            max_input_tokens_per_request=None,
        )

        case_root = run_root / case["case_id"]
        stage = "RUNTIME_INITIALIZATION"
        try:
            source = (REPO / case["workspace_snapshot"]).resolve()
            source_before = _file_hashes(source)
            run_root.mkdir(parents=True, exist_ok=False)
            case_root.mkdir(parents=True, exist_ok=False)
            vault = case_root / "vault"
            shutil.copytree(source, vault)
            copy_before = _file_hashes(vault)
            if copy_before != case["workspace_files_sha256"]:
                raise SmokeBlocked("FROZEN_WORKSPACE_COPY_MISMATCH")
            index_path = scan_vault(vault, case_root / "scan").index_path
            client = LedgeredGeminiClient(sdk_client, ledger)
            client.assert_developer_api()
            adapter = GeminiProviderAdapter(client, model_id=MODEL)
            runtime = RuntimeEngine(
                vault_root=vault,
                index_path=index_path,
                checkpoint_dir=case_root / "checkpoints",
                trace_dir=case_root / "traces",
                memory_root=case_root / "memory",
                model=adapter,
            )
            run_request = _new_run_request(protocol)
            if run_request.final_protocol != FINAL_PROTOCOL:
                raise SmokeBlocked("TYPED_FINAL_NOT_ACTIVE_AT_RUNTIME_BOUNDARY")
            _write_json(run_root / "run_manifest.json", {
                "schema_version": "gemini38-typed-cn2-run/v1",
                "batch_id": protocol["batch_id"],
                "run_id": run_id,
                "case_id": case["case_id"],
                "protocol_sha256": _sha256(profile_config["protocol_path"]),
                "runner_sha256": _sha256(Path(__file__)),
                "requested_model_id": MODEL,
                "sdk_version": protocol["provider"]["sdk_version"],
                "request_execution": "official_sdk_usage_only",
                "count_tokens_preflight_enabled": False,
                "max_runtime_runs": limits["runtime_runs"],
                "max_provider_requests": limits["generation_requests"],
                "max_runtime_steps": limits["runtime_steps"],
                "request_timeout_seconds": limits["request_timeout_seconds"],
                "concurrency": limits["concurrency"],
                "automatic_retries": limits["automatic_retries"],
                "outer_process_timeout_seconds": limits["outer_process_timeout_seconds"],
                "provider_default_output_limit": True,
                "final_protocol": run_request.final_protocol,
                "workspace_files_sha256": copy_before,
                "cost_enforcement": "none",
                "cost_observation": protocol["cost_observation"],
                "gold_available_to_runtime": False,
            })
        except SmokeBlocked:
            raise
        except Exception:
            raise SmokeBlocked("RUNTIME_INITIALIZATION_FAILED") from None

        stage = "RUNTIME_EXECUTION"
        ledger.runtime_started()
        try:
            run_status = runtime.start_multi_agent(run_request)
        except Exception:
            raise SmokeBlocked("RUNTIME_EXECUTION_FAILED") from None
        state = runtime.checkpointer.get_latest(run_status.thread_id)
        if state is None:
            raise SmokeBlocked("RUNTIME_STATE_NOT_PERSISTED")
        result = None
        if state.result_ref:
            result_path = case_root / "checkpoints" / state.result_ref
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))

        source_after = _file_hashes(source)
        copy_after = _file_hashes(vault)
        if source_after != source_before or copy_after != copy_before:
            raise SmokeBlocked("FROZEN_WORKSPACE_CHANGED_DURING_RUN")
        key_bytes = api_key.encode("utf-8")
        if any(key_bytes in path.read_bytes() for path in case_root.rglob("*") if path.is_file()):
            raise SmokeBlocked("CREDENTIAL_LEAK_BLOCKED")

        tool_actions = [
            {
                "tool_id": record.tool_id,
                "status": getattr(record.status, "value", record.status),
                "query": record.arguments.get("query") if isinstance(record.arguments, dict) else None,
                "arguments": copy.deepcopy(record.arguments),
                "result": copy.deepcopy(record.result),
                "error": copy.deepcopy(record.error),
                "candidates_or_read": _tool_action_candidates(record),
            }
            for record in state.tool_ledger
        ]
        decision_root = _team_decision_result(result)
        decision = decision_root.get("decision") if isinstance(decision_root, dict) else None
        review = result.get("review") if isinstance(result, dict) else None
        reviewer_decision = review.get("decision") if isinstance(review, dict) else None
        runtime_status = getattr(run_status, "status", None) or getattr(state, "status", None)
        error_value = getattr(state, "error", None)
        if callable(getattr(error_value, "to_dict", None)):
            error_value = error_value.to_dict()
        provider_usage = [
            {
                "status": "SUCCESS_USAGE_REPORTED" if item.get("usage_status") == "COMPLETE" else "USAGE_UNKNOWN_TERMINAL",
                "duration_ms": item.get("duration_ms"),
                "usage": {
                    "prompt_tokens": item.get("prompt_tokens"),
                    "billable_output_estimate": item.get("billable_output_estimate"),
                },
            }
            for item in client.generation_usage_records
        ]
        estimate = client.estimated_cost_usd
        observed = {
            "run_id": run_id,
            "case_id": "P0_CN2",
            "variant": "typed-claims-v1",
            "group_id": "gemini38-p14-cn2-typed",
            "status": runtime_status,
            "error": error_value,
            "decision": decision_root,
            "tool_actions": tool_actions,
            "tool_action_count": len(tool_actions),
            "model_turns": len(getattr(state, "model_executions", [])),
            "provider_requests": ledger.request_count,
            "provider_usage": provider_usage,
            "conservative_usd": estimate,
            "trajectory": {"tool_calls": tool_actions},
            "runtime": {
                "provider_requests": ledger.request_count,
                "runtime_steps": len(getattr(state, "model_executions", [])),
                "tool_calls": len(tool_actions),
                "input_tokens": client.reported_input_tokens if client.generation_usage_records else None,
                "output_tokens": client.reported_output_tokens if client.generation_usage_records else None,
                "thinking_tokens": client.reported_thinking_tokens,
                "cached_tokens": client.reported_cached_tokens,
                "usage_records": copy.deepcopy(client.generation_usage_records),
            },
            "cost": {
                "estimated_cost_usd": estimate,
                "provider_reported_cost_usd": client.reported_cost_usd,
                "enforcement": "none",
            },
            "gold_read_by_runtime": False,
        }
        _write_json(case_root / "observed_summary.json", observed)
        summary_sha = _sha256(case_root / "observed_summary.json")
        _write_json(case_root / "observed_summary.seal.json", {
            "schema_version": "gemini38-observed-seal/v1",
            "sealed": True,
            "gold_available": False,
            "observed_summary_sha256": summary_sha,
        })

        summary: dict[str, Any] = {
            "run_id": run_id,
            "case_id": "P0_CN2",
            "requested_model_id": MODEL,
            "provider_model_versions": list(ledger.model_versions),
            "final_protocol": run_request.final_protocol,
            "runtime_status": runtime_status,
            "runtime_failure_type": _runtime_failure_type(run_status, state),
            "provider_requests": ledger.request_count,
            "count_tokens_requests": 0,
            "runtime_steps": len(getattr(state, "model_executions", [])),
            "tool_action_count": len(tool_actions),
            "typed_result_schema": decision_root.get("schema_version") if isinstance(decision_root, dict) else None,
            "typed_final_schema_passed": isinstance(decision_root, dict) and decision_root.get("schema_version") == "team-decision-result/v1",
            "decision_status": decision.get("status") if isinstance(decision, dict) else None,
            "reviewer_decision": reviewer_decision,
            "reviewer_accepted": reviewer_decision == "evidence_sufficient",
            "input_tokens_reported": client.reported_input_tokens if client.generation_usage_records else None,
            "output_tokens_reported": client.reported_output_tokens if client.generation_usage_records else None,
            "thinking_tokens_reported": client.reported_thinking_tokens,
            "cached_tokens_reported": client.reported_cached_tokens,
            "provider_reported_cost_usd": client.reported_cost_usd,
            "estimated_cost_usd": estimate,
            "provider_usage_records": copy.deepcopy(client.generation_usage_records),
            "provider_finish_reasons": _provider_output_diagnostics(client.generation_usage_records)["provider_finish_reasons"],
            "gold_read_by_runtime": False,
            "gold_accuracy_scored": False,
        }

        stage = "POST_RUN_SCORING"
        ledger.finish_run(
            status="COMPLETED" if runtime_status == "completed" else "RUNTIME_FAILED",
            usage_summary={
                "provider_requests": ledger.request_count,
                "count_tokens_requests": 0,
                "input_tokens": summary["input_tokens_reported"],
                "output_tokens": summary["output_tokens_reported"],
                "thinking_tokens": summary["thinking_tokens_reported"],
                "cached_tokens": summary["cached_tokens_reported"],
                "provider_reported_cost_usd": client.reported_cost_usd,
                "estimated_cost_usd": estimate,
                "token_usage_enforcement": "OBSERVE_ONLY",
                "cost_enforcement": "none",
            },
        )
        score_input = _frozen_score_input(
            observed, result, state, run_status, ledger.request_count, estimate or 0.0
        )
        try:
            score = _score_gold_after_provider_stop(
                ledger,
                score_input,
                PHASE9_GOLD_PATH,
                parent["fingerprint"]["gold_sha256_unread_by_runner"]["phase9"],
            )
        except BaseException:
            raise SmokeBlocked("GOLD_SCORING_FAILED") from None
        _attach_gold_outcome(
            summary,
            actual_models=list(ledger.model_versions),
            runtime_status=runtime_status,
            decision_root=decision_root,
            reviewer_decision=reviewer_decision,
            score=score,
        )
        summary["independent_gold_score"] = score
        _write_json(case_root / "typed_final_runtime_summary.json", summary)

        if runtime_status != "completed":
            raise SmokeBlocked("RUNTIME_DID_NOT_COMPLETE")
        if reviewer_decision != "evidence_sufficient" or not isinstance(decision, dict) or decision.get("status") != "approved":
            raise SmokeBlocked("REVIEWER_DID_NOT_ACCEPT")
        if ledger.halted:
            raise SmokeBlocked("PROVIDER_LEDGER_TERMINAL")
        if score.get("label") != "ANSWERED_CORRECT":
            raise SmokeBlocked("GOLD_SCORING_FAILED")
        return case_root
    except BaseException as error:
        if ledger is not None and not ledger.run_finished:
            try:
                ledger.finish_run(
                    status="STOPPED_OR_UNCERTAIN",
                    usage_summary={
                        "provider_requests": ledger.request_count,
                        "count_tokens_requests": 0,
                        "provider_usage_completeness": (
                            "COMPLETE" if client is not None and _provider_usage_complete(client)
                            else "INCOMPLETE_OR_UNAVAILABLE"
                        ),
                    },
                )
            except BaseException:
                pass
        if case_root is not None and case_root.is_dir() and not (case_root / "typed_final_runtime_summary.json").exists():
            try:
                _write_json(case_root / "typed_final_runtime_summary.json", {
                    "run_id": run_id,
                    "case_id": "P0_CN2",
                    "runtime_status": getattr(run_status, "status", None) or getattr(state, "status", None) or "FAILED",
                    "runtime_failure_type": _runtime_failure_type(run_status, state, error),
                    "provider_requests": ledger.request_count if ledger is not None else 0,
                    "count_tokens_requests": 0,
                    "typed_final_schema_passed": False,
                    "reviewer_accepted": False,
                    "independent_gold_label": "NOT_EVALUATED",
                    "gold_scoring_status": "NOT_EVALUATED",
                    "gold_not_evaluated_reason": "SCORER_NOT_RUN",
                    "gold_read_by_runtime": False,
                })
            except BaseException:
                pass
        try:
            _write_startup_diagnostic(
                root,
                run_id=run_id,
                stage=stage,
                error=error,
                provider_requests=ledger.request_count if ledger is not None else 0,
            )
        except BaseException:
            pass
        raise
    finally:
        if ledger is not None:
            ledger.close()
        close = getattr(sdk_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def test_p14_cn2_typed_gemini38_offline_preflight():
    protocol, _parent = _read_protocol()
    request = _new_run_request(protocol)
    assert request.final_protocol == "claim-proposal/v1"
    assert request.workflow == "team_decision"
    assert request.max_steps == 18
    assert request.max_provider_requests == 18
    assert request.query == protocol["case"]["question"]
    assert GEMINI_38_FLASH_MODEL_ID == MODEL
    assert protocol["provider"]["temperature"] == "omitted"
    assert protocol["provider"]["thinking_level"] == "provider_default"
    assert USAGE_ONLY_PROVIDER_DEFAULT_OUTPUT is True
    assert protocol["case"]["gold_available_to_runtime"] is False


def test_new_protocol_observes_cost_without_any_usd_gate():
    protocol, _parent = _read_protocol()
    assert "cost_guard" not in protocol
    assert protocol["cost_observation"]["enforcement"] == "none"
    assert protocol["cost_observation"]["record_usage_and_estimate"] is True
    limits = protocol["execution_limits"]
    assert limits["count_tokens_requests"] == 0
    assert limits["total_http_requests_including_preflight"] == limits["generation_requests"]
    assert limits["aggregate_counted_input_tokens"] == 0
    assert protocol["token_accounting"]["mode"] == "provider_usage_only"
    assert protocol["case"]["run_id"] == "G38-TYPED-CN2-002"
    assert LEDGER_PATH.name == "GEMINI38_CN2_USAGE_ONLY_PROVIDER_LEDGER.jsonl"
    assert (ARTIFACT_ROOT / "G38-TYPED-CN2-001").is_dir()
    assert (ARTIFACT_ROOT / protocol["case"]["run_id"]).is_dir()
    assert LEDGER_PATH.is_file()
    ledger_rows = [
        json.loads(line) for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    ]
    run_rows = [row for row in ledger_rows if row.get("run_id") == protocol["case"]["run_id"]]
    assert sum(row.get("event") == "request_reserved" for row in run_rows) == 5
    generation_reservations = [
        row for row in run_rows if row.get("event") == "request_reserved"
    ]
    assert all(row["max_output_tokens"] == 512 for row in generation_reservations)
    assert not any(row.get("request_kind") == "count_tokens" for row in run_rows)
    assert run_rows[-1]["event"] == "run_finished"


def test_gemini38_usage_only_guard_skips_count_tokens_and_accepts_tool_response():
    harness = _harness()

    class Delegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = 0
            self.generate_calls = []
            self.response = {
                "model_version": MODEL,
                "usage_metadata": {
                    "prompt_token_count": 4364,
                    "candidates_token_count": 176,
                    "thoughts_token_count": 107,
                },
                "function_calls": [{
                    "id": "call-real-third-turn-replay",
                    "name": "search_notes",
                    "args": {
                        "query": "verified dependency entity",
                        "source_context": {"dependency_entities": {"owner": "verified"}},
                        "limit": 5,
                    },
                }],
            }

        def count_tokens(self, **_kwargs):
            self.count_calls += 1
            raise AssertionError("usage-only mode must not call countTokens")

        def generate_content(self, **kwargs):
            self.generate_calls.append(copy.deepcopy(kwargs))
            return self.response

    delegate = Delegate()
    aggregate = harness.AggregateUsage()
    guard = _new_usage_only_guard(
        harness,
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=replace(
            harness.BUDGET_MANIFEST.case("mps-001"),
            max_provider_requests=18,
            max_model_turns=18,
            max_input_tokens=64000,
            max_output_tokens=4096,
            max_elapsed_provider_seconds=1900,
            request_timeout_seconds=130,
            per_request_input_tokens=12000,
        ),
        aggregate=aggregate,
        count_tokens_preflight=False,
    )
    contents = [
        {"role": "user", "parts": [{"text": "frozen goal and tool history"}]},
        {"role": "model", "parts": [{
            "function_call": {"name": "search_notes"},
            "thought_signature": "first-signature-redacted",
        }]},
        {"role": "user", "parts": [{"function_response": {"name": "search_notes"}}]},
        {"role": "model", "parts": [{
            "function_call": {"name": "read_verified_note"},
            "thought_signature": "second-signature-redacted",
        }]},
        {"role": "user", "parts": [{"function_response": {"name": "read_verified_note"}}]},
    ]

    response = guard.generate_content(
        model=MODEL,
        contents=contents,
        config={"automatic_function_calling": {"disable": True}},
    )

    assert delegate.count_calls == 0
    assert len(delegate.generate_calls) == 1
    assert response["function_calls"][0]["name"] == "search_notes"
    assert guard.provider_calls == 1
    assert guard.preflight_count_requests == 0
    assert guard.reported_input_tokens == 4364
    assert guard.reported_output_tokens == 176
    assert guard.reported_thinking_tokens == 107


def test_historical_g38_third_turn_payload_and_tool_call_pass_usage_only_guard():
    harness = _harness()
    artifact_dir = (
        REPO
        / ".artifacts"
        / "gemini38_p14_cn2_typed"
        / "G38-TYPED-CN2-001"
        / "P0_CN2"
    )
    summary_path = artifact_dir / "observed_summary.json"
    seal_path = artifact_dir / "observed_summary.seal.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(summary_path.read_bytes()).hexdigest() == seal[
        "observed_summary_sha256"
    ]

    record = summary["runtime"]["preflight_records"][2]
    request = record["request"]
    captured_call = record["response_replay_observation"]["candidates"][0][
        "function_calls"
    ][0]
    assert captured_call["arguments_replayable"] is True

    class HistoricalResponseDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.generate_calls = []
            self.response = {
                "model_version": MODEL,
                "usage_metadata": {
                    "prompt_token_count": record["reported_input_tokens"],
                    "candidates_token_count": record["reported_output_tokens"],
                    "thoughts_token_count": record["reported_thinking_tokens"],
                },
                "function_calls": [{
                    "name": captured_call["name"],
                    "args": captured_call["arguments"],
                }],
            }

        def generate_content(self, **kwargs):
            self.generate_calls.append(copy.deepcopy(kwargs))
            return self.response

    delegate = HistoricalResponseDelegate()
    aggregate = harness.AggregateUsage()
    guard = _new_usage_only_guard(
        harness,
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=replace(
            harness.BUDGET_MANIFEST.case("mps-001"),
            max_provider_requests=18,
            max_model_turns=18,
            max_input_tokens=64000,
            max_output_tokens=4096,
            max_elapsed_provider_seconds=1900,
            request_timeout_seconds=130,
            per_request_input_tokens=12000,
        ),
        aggregate=aggregate,
        count_tokens_preflight=False,
    )

    response = guard.generate_content(
        model=request["model"],
        contents=request["contents"],
        config=request["config"],
    )

    assert not hasattr(delegate, "count_tokens")
    assert len(delegate.generate_calls) == 1
    assert harness._contains_replayed_thought_signature(delegate.generate_calls[0])
    assert response is delegate.response
    assert response["function_calls"][0]["name"] == captured_call["name"]
    assert response["function_calls"][0]["args"] is captured_call["arguments"]
    assert guard.provider_calls == 1
    assert guard.preflight_count_requests == 0
    assert guard.reported_input_tokens == 4364
    assert guard.reported_output_tokens == 176
    assert guard.reported_thinking_tokens == 107
    assert guard.generation_usage_records[0]["usage_delta"] == "NOT_APPLICABLE_USAGE_ONLY"


def test_g38_usage_only_keeps_provider_output_default_and_classifies_max_tokens(tmp_path: Path):
    harness = _harness()

    class Delegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.generate_calls = []
            self.response = {
                "model_version": MODEL,
                "usage_metadata": {
                    "prompt_token_count": 120,
                    "candidates_token_count": 600,
                    "thoughts_token_count": 488,
                },
                "candidates": [{
                    "finish_reason": "MAX_TOKENS",
                    "content": {"parts": [{"text": '{"schema_version":"claim-proposal/v1","claims":'}]},
                }],
            }

        def generate_content(self, **kwargs):
            self.generate_calls.append(copy.deepcopy(kwargs))
            return self.response

        def assert_developer_api(self):
            return None

    delegate = Delegate()
    aggregate = harness.AggregateUsage()
    ledger = AppendOnlyProviderLedger(
        tmp_path / "max-tokens-ledger.jsonl",
        batch_id="offline-max-tokens-diagnosis",
        run_id="offline-max-tokens-diagnosis-run",
        max_requests=1,
        max_runtime_runs=1,
        max_input_tokens_per_request=8192,
    )
    ledger.runtime_started()
    ledgered_delegate = LedgeredGeminiClient(
        delegate,
        ledger,
        provider_default_output=USAGE_ONLY_PROVIDER_DEFAULT_OUTPUT,
    )
    guard = _new_usage_only_guard(
        harness,
        ledgered_delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=replace(
            harness.BUDGET_MANIFEST.case("mps-001"),
            max_provider_requests=18,
            max_model_turns=18,
            max_input_tokens=64000,
            max_output_tokens=4096,
            max_elapsed_provider_seconds=1900,
            request_timeout_seconds=130,
            per_request_input_tokens=12000,
        ),
        aggregate=aggregate,
        count_tokens_preflight=False,
    )
    response = guard.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic typed claim request"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )

    assert response is delegate.response
    assert "max_output_tokens" not in delegate.generate_calls[0]["config"]
    reservation = next(row for row in map(
        json.loads,
        (tmp_path / "max-tokens-ledger.jsonl").read_text(encoding="utf-8").splitlines(),
    ) if row.get("event") == "request_reserved")
    assert reservation["max_output_tokens"] is None
    assert guard.reported_output_tokens == 600
    assert guard.reported_thinking_tokens == 488
    assert guard.reported_billable_output_tokens == 1088
    assert guard.last_guard_state == "RESPONSE_ACCEPTED"
    assert guard.generation_usage_records[0]["response_replay_observation"]["candidates"][0]["finish_reason"] == "MAX_TOKENS"

    diagnostics = _provider_output_diagnostics(guard.generation_usage_records)
    assert diagnostics["provider_finish_reasons"] == [
        {"sequence": 1, "finish_reason": "MAX_TOKENS"}
    ]
    assert diagnostics["provider_output_truncated_by_max_tokens"] is True

    real_run_root = ARTIFACT_ROOT / "G38-TYPED-CN2-002" / "P0_CN2"
    observed_path = real_run_root / "observed_summary.json"
    observed_seal = json.loads(
        (real_run_root / "observed_summary.seal.json").read_text(encoding="utf-8")
    )
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(observed_path.read_bytes()).hexdigest() == observed_seal[
        "observed_summary_sha256"
    ]
    actual_provider_diagnostics = _provider_output_diagnostics(
        observed["runtime"]["generation_usage_records"]
    )
    assert actual_provider_diagnostics["provider_finish_reasons"][-1] == {
        "sequence": 5,
        "finish_reason": "MAX_TOKENS",
    }
    assert actual_provider_diagnostics["provider_output_truncated_by_max_tokens"] is True

    ledger.finish_run(status="RUNTIME_FAILED", usage_summary={"output_tokens": 20})
    try:
        summary_path = _write_failure_terminal_summary(
            tmp_path / "max-tokens-case",
            run_id="offline-max-tokens-diagnosis-run",
            case_id="P0_CN2",
            ledger=ledger,
            guarded_client=guard,
            runtime_status="failed",
            failure_type="TEAM_DECISION_CONTRACT_ERROR",
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["provider_output_truncated_by_max_tokens"] is True
        assert summary["provider_finish_reasons"] == diagnostics["provider_finish_reasons"]
        assert summary["independent_gold_label"] == "NOT_EVALUATED"
    finally:
        ledger.close()


def test_failed_smoke_summary_marks_gold_not_evaluated_without_model_identity():
    summary = _attach_gold_outcome(
        {"run_id": "offline-failed-run", "runtime_status": "failed"},
        actual_models=[],
        runtime_status="failed",
        decision_root=None,
        reviewer_decision=None,
        score={"label": "MODEL_ERROR", "required_verified_reads_present": False},
    )

    assert summary["actual_model_id_confirmed"] is False
    assert summary["frozen_gold_scorer_label"] == "MODEL_ERROR"
    assert summary["independent_gold_label"] == "NOT_EVALUATED"
    assert summary["gold_accuracy_scored"] is False
    assert summary["gold_not_evaluated_reason"] == "NO_SCORABLE_FINAL"


def test_usage_only_guard_observes_usage_without_old_token_budget_stops():
    harness = _harness()

    class Delegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = 0
            self.generate_calls = 0

        def count_tokens(self, **_kwargs):
            self.count_calls += 1
            raise AssertionError("countTokens is disabled")

        def generate_content(self, **_kwargs):
            self.generate_calls += 1
            return {
                "model_version": MODEL,
                "usage_metadata": {
                    "prompt_token_count": 64001,
                    "candidates_token_count": 5000,
                    "thoughts_token_count": 1200,
                    "cached_content_token_count": 64,
                },
                "function_calls": [{"id": "call-over-limit", "name": "search_notes", "args": {"query": "x", "limit": 1}}],
            }

    delegate = Delegate()
    aggregate = harness.AggregateUsage()
    guard = _new_usage_only_guard(
        harness,
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=replace(
            harness.BUDGET_MANIFEST.case("mps-001"),
            max_provider_requests=18,
            max_model_turns=18,
            max_input_tokens=64000,
            max_output_tokens=4096,
            max_elapsed_provider_seconds=1900,
            request_timeout_seconds=130,
            per_request_input_tokens=4300,
        ),
        aggregate=aggregate,
        count_tokens_preflight=False,
    )

    response = guard.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )

    assert delegate.count_calls == 0
    assert delegate.generate_calls == 1
    assert response["function_calls"][0]["id"] == "call-over-limit"
    assert guard.last_guard_state == "RESPONSE_ACCEPTED"
    assert guard.reported_input_tokens == 64001
    assert guard.reported_output_tokens == 5000
    assert guard.reported_thinking_tokens == 1200
    assert guard.reported_billable_output_tokens == 6200
    assert guard.reserved_unknown_output_tokens == 0
    assert guard.reported_cached_tokens == 64
    assert aggregate.reported_input_tokens == 64001
    assert aggregate.reported_billable_output_tokens == 6200
    assert aggregate.reported_cached_tokens == 64
    assert guard.generation_usage_records[0]["reported_cached_tokens"] == 64


def test_usage_only_missing_usage_has_exact_terminal_reason_and_no_fake_tokens(tmp_path: Path):
    harness = _harness()

    class Delegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.generate_calls = 0
            self.response = {
                "model_version": MODEL,
                "usage_metadata": {"prompt_token_count": 3000},
                "text": "{}",
                "finish_reason": "STOP",
            }

        def generate_content(self, **_kwargs):
            self.generate_calls += 1
            return self.response

        def assert_developer_api(self):
            return None

    delegate = Delegate()
    ledger = AppendOnlyProviderLedger(
        tmp_path / "unknown-usage-ledger.jsonl",
        batch_id="offline-unknown-usage",
        run_id="offline-unknown-usage-run",
        max_requests=2,
        max_runtime_runs=1,
        max_input_tokens_per_request=None,
    )
    ledger.runtime_started()
    guard = _new_usage_only_guard(
        harness,
        LedgeredGeminiClient(
            delegate,
            ledger,
            provider_default_output=USAGE_ONLY_PROVIDER_DEFAULT_OUTPUT,
        ),
        case=harness.PUBLIC_CASES[0],
        case_budget=replace(
            harness.BUDGET_MANIFEST.case("mps-001"),
            max_provider_requests=18,
            max_model_turns=18,
            max_input_tokens=64000,
            max_output_tokens=4096,
            max_elapsed_provider_seconds=1900,
            request_timeout_seconds=130,
            per_request_input_tokens=12000,
        ),
        aggregate=harness.AggregateUsage(),
        count_tokens_preflight=False,
    )

    response = guard.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )

    assert response is delegate.response
    assert delegate.generate_calls == 1
    assert guard.last_guard_state == "PROVIDER_USAGE_INCOMPLETE_TERMINAL"
    assert guard.generation_usage_records[0]["usage_status"] == "INCOMPLETE"
    assert guard.generation_usage_records[0]["reported_output_tokens"] == "UNAVAILABLE"
    assert guard.reported_output_tokens == 0
    assert guard.reserved_unknown_output_tokens == 0
    assert _conservative_cost_observation(guard) is None
    assert _reported_usage_total(guard, "reported_output_tokens") is None
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "unknown-usage-ledger.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    finished_request = next(
        row for row in ledger_rows if row.get("event") == "request_finished"
    )
    assert finished_request["status"] == "USAGE_UNKNOWN_TERMINAL"
    assert finished_request["usage"]["output_tokens"] is None
    assert finished_request["usage"]["estimated_cost_usd"] is None
    assert ledger.halted is True
    with pytest.raises(
        harness.SmokeBlocked, match="PROVIDER_USAGE_INCOMPLETE_TERMINAL"
    ):
        guard.generate_content(
            model=MODEL,
            contents=[{"role": "user", "parts": [{"text": "second"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert delegate.generate_calls == 1
    ledger.finish_run(status=guard.last_guard_state, usage_summary={
        "provider_usage_completeness": "INCOMPLETE_OR_UNAVAILABLE",
        "output_tokens": _reported_usage_total(guard, "reported_output_tokens"),
    })
    assert json.loads(
        (tmp_path / "unknown-usage-ledger.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["status"] == "PROVIDER_USAGE_INCOMPLETE_TERMINAL"
    ledger.close()


def test_usage_only_typed_final_survives_legacy_input_and_output_thresholds(tmp_path: Path):
    harness = _harness()
    final_json = json.dumps({
        "schema_version": "claim-proposal/v1",
        "claims": [{
            "claim_type": "DECISION",
            "subject": "synthetic project",
            "relation": "owner",
            "value": "synthetic person",
            "valid_from": None,
            "valid_to": None,
            "evidence_refs": ["ev_synthetic_1"],
        }],
    })

    class Delegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.calls = []
            self.response = SimpleNamespace(
                text=final_json,
                model_version=MODEL,
                finish_reason="STOP",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=64001,
                    candidates_token_count=5000,
                    thoughts_token_count=1200,
                ),
            )

        def generate_content(self, **kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            return self.response

        def assert_developer_api(self):
            return None

    delegate = Delegate()
    ledger = AppendOnlyProviderLedger(
        tmp_path / "typed-final-provider-ledger.jsonl",
        batch_id="offline-typed-final",
        run_id="offline-typed-final-run",
        max_requests=1,
        max_runtime_runs=1,
        max_input_tokens_per_request=None,
    )
    ledger.runtime_started()
    guarded = _new_usage_only_guard(
        harness,
        LedgeredGeminiClient(
            delegate,
            ledger,
            provider_default_output=USAGE_ONLY_PROVIDER_DEFAULT_OUTPUT,
        ),
        case=harness.PUBLIC_CASES[0],
        case_budget=replace(
            harness.BUDGET_MANIFEST.case("mps-001"),
            max_provider_requests=18,
            max_model_turns=18,
            max_input_tokens=64000,
            max_output_tokens=4096,
            max_elapsed_provider_seconds=1900,
            request_timeout_seconds=130,
            per_request_input_tokens=12000,
        ),
        aggregate=harness.AggregateUsage(),
        count_tokens_preflight=False,
    )
    adapter = GeminiProviderAdapter(guarded, model_id=MODEL)
    try:
        result = adapter.complete(ModelTurnRequest(
            run_id="offline-g38-typed-final",
            turn_id="offline-g38-typed-final:turn:1",
            task_id="offline-g38-typed-task",
            agent_id="decision_agent",
            sequence=1,
            user_input="Return claims supported by the already verified evidence.",
            observation=None,
            available_tools=[],
            model_id=MODEL,
        ))
        ledger.finish_run(status="OFFLINE_COMPLETE", usage_summary={
            "input_tokens": guarded.reported_input_tokens,
            "output_tokens": guarded.reported_output_tokens,
            "thinking_tokens": guarded.reported_thinking_tokens,
        })
    finally:
        ledger.close()

    assert result.error is None
    assert result.proposal is not None
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 64001
    assert result.usage.output_tokens == 5000
    assert guarded.reported_thinking_tokens == 1200
    assert guarded.last_guard_state == "RESPONSE_ACCEPTED"
    assert len(delegate.calls) == 1
    assert "max_output_tokens" not in delegate.calls[0]["config"]
    assert guarded.case_budget.max_provider_requests == 18
    assert guarded.case_budget.max_model_turns == 18
    assert guarded.case_budget.request_timeout_seconds == 130
    assert guarded.case_budget.automatic_retry_count == 0
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "typed-final-provider-ledger.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    reservation = next(row for row in ledger_rows if row.get("event") == "request_reserved")
    assert reservation["max_output_tokens"] is None
    assert reservation["token_usage_enforcement"] == "OBSERVE_ONLY"
    assert ledger.halted is False


def test_failure_terminal_summary_is_written_without_gold_and_keeps_usage(tmp_path: Path):
    ledger = AppendOnlyProviderLedger(
        tmp_path / "failure-ledger.jsonl",
        batch_id="offline-failure-summary",
        run_id="offline-failure-summary-run",
        max_requests=2,
        max_runtime_runs=1,
        max_input_tokens_per_request=8192,
    )
    ledger.runtime_started()
    ledger.finish_run(status="STOPPED_OR_UNCERTAIN", usage_summary={"input_tokens": 4364})

    class Usage:
        last_guard_state = "USAGE_ONLY_RESPONSE_RECEIVED"
        provider_calls = 1
        preflight_count_requests = 0
        count_tokens_preflight = False
        reported_input_tokens = 4364
        reported_output_tokens = 176
        reported_thinking_tokens = 107
        reported_cached_tokens = 64
        reported_cost_usd = None
        generation_usage_records = [{"reported_input_tokens": 4364, "reported_cached_tokens": 64}]

    try:
        path = _write_failure_terminal_summary(
            tmp_path / "case",
            run_id="offline-failure-summary-run",
            case_id="P0_CN2",
            ledger=ledger,
            guarded_client=Usage(),
            runtime_status="failed",
            failure_type="MODEL_LOCAL_GUARD_FAILURE",
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["runtime_failure_type"] == "MODEL_LOCAL_GUARD_FAILURE"
        assert data["input_tokens_reported"] == 4364
        assert data["cached_tokens_reported"] == 64
        assert data["count_tokens_requests"] == 0
        assert data["independent_gold_label"] == "NOT_EVALUATED"
        assert data["gold_accuracy_scored"] is False
        assert data["gold_read_by_runtime"] is False
        assert "required_entities" not in path.read_text(encoding="utf-8")
    finally:
        ledger.close()


def test_frozen_gold_scorer_requires_finished_run_and_scores_postrun_only(tmp_path: Path):
    ledger = AppendOnlyProviderLedger(
        tmp_path / "ledger.jsonl",
        batch_id="postrun-score",
        run_id="postrun-score-run",
        max_requests=2,
        max_runtime_runs=1,
        max_input_tokens_per_request=64,
    )
    gold_path = tmp_path / "synthetic-gold.json"
    gold_path.write_text(json.dumps({
        "P0_CN2": {
            "answerable": True,
            "required_entities": ["Mira Chen", "Cobalt B"],
        }
    }), encoding="utf-8")
    expected_hash = _sha256(gold_path)
    observed = {
        "run_id": "postrun-score-run",
        "case_id": "P0_CN2",
        "variant": "typed-claims-v1",
        "group_id": "offline-test",
        "status": "completed",
        "decision": {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "status": "approved",
                "value": "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B.",
            },
        },
        "tool_actions": [
            {
                "tool_id": "read_verified_note",
                "status": "completed",
                "candidates_or_read": [{"relative_path": "phase5_cn/owner.md"}],
            },
            {
                "tool_id": "read_verified_note",
                "status": "completed",
                "candidates_or_read": [{"relative_path": "phase5_cn/leader.md"}],
            },
        ],
        "provider_requests": 3,
        "tool_action_count": 4,
        "model_turns": 3,
        "conservative_usd": 0.001,
    }
    try:
        with pytest.raises(SmokeBlocked, match="GOLD_SCORING_REQUIRES_FINISHED_RUN"):
            _score_gold_after_provider_stop(ledger, observed, gold_path, expected_hash)
        ledger.finish_run(status="COMPLETED", usage_summary={"provider_requests": 3})
        scored = _score_gold_after_provider_stop(ledger, observed, gold_path, expected_hash)
        assert scored["label"] == "ANSWERED_CORRECT"
        assert scored["required_verified_reads_present"] is True
    finally:
        ledger.close()


class _DeterministicTestEmbedder:
    def encode(self, texts: list[str]):
        import numpy as np

        return np.ones((len(texts), 4), dtype=np.float32)


def _run_phase14_typed_fake_runtime(tmp_path: Path, monkeypatch, *, wrong_owner: bool):
    replay = importlib.import_module(
        "tests.integration.test_team_decision_final_contract_communication"
    )
    runtime_backend = importlib.import_module("linkloom.retrieval_v2.runtime_backend")
    monkeypatch.setattr(
        runtime_backend, "_DEFAULT_RUNTIME_EMBEDDER", _DeterministicTestEmbedder()
    )
    observed, vault, index_path = replay._phase14_inputs("P14-CN2")
    proposal = replay._phase14_cn2_claim_proposal(observed)
    if wrong_owner:
        proposal["claims"][0]["object"] = "赵强"
    final_raw = json.dumps(proposal, ensure_ascii=False)
    actions = []
    search_number = 0
    for action_index, trace_action in enumerate(observed["tool_actions"]):
        if trace_action["tool_id"] == "search_notes":
            query = trace_action["query"]
            subgoal_number = search_number
            search_number += 1

            def search(request, *, query=query, index=action_index, subgoal_number=subgoal_number):
                request_data = json.loads(
                    request.user_input.split("team_decision_request=", 1)[1]
                )
                source_context = dict(request_data["source_context"])
                source_context.update({
                    "subgoal_id": "owner" if subgoal_number == 0 else "leader",
                    "query_variant": "primary",
                    "dependency_entities": {} if subgoal_number == 0 else {"owner": "李明"},
                })
                return replay.ModelAction.tool(replay.ToolCall(
                    call_id=f"phase15-search-{index}",
                    tool_id="search_notes",
                    arguments={
                        "query": query,
                        "source_context": source_context,
                        "limit": 5,
                    },
                ))

            actions.append(search)
        elif trace_action["tool_id"] == "read_verified_note":
            suffix = trace_action["candidates_or_read"][0]["relative_path"]

            def read(request, *, suffix=suffix, index=action_index):
                assert request.observation is not None
                evidence = next(
                    item for item in request.observation.value
                    if item["relative_path"].endswith(suffix)
                )
                return replay.ModelAction.tool(replay.ToolCall(
                    call_id=f"phase15-read-{index}",
                    tool_id="read_verified_note",
                    arguments={"note_ref": evidence["evidence_id"]},
                ))

            actions.append(read)
    actions.append(lambda _request: replay.ModelAction.final(final_raw))
    model = replay.FakeModelAdapter(actions)
    engine = replay._engine(tmp_path, model, vault_root=vault, index_path=index_path)
    status = engine.start_multi_agent(replay.RunRequest(
        request_id=f"phase14-typed-fake-{wrong_owner}",
        thread_id=f"phase14-typed-fake-{wrong_owner}",
        workflow="team_decision",
        query="谁负责青岚计划？该负责人的直属领导是谁？",
        max_steps=8,
        max_provider_requests=8,
        dry_run=True,
        final_protocol="claim-proposal/v1",
    ))
    state = engine.checkpointer.get_latest(status.thread_id)
    result_path = engine.checkpoint_dir / "results" / status.run_id / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return status, state, result, model


def test_phase14_typed_fake_runtime_completes_and_rejects_bad_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    accepted_status, accepted_state, accepted_result, accepted_model = (
        _run_phase14_typed_fake_runtime(tmp_path / "accepted", monkeypatch, wrong_owner=False)
    )
    assert accepted_status.status == accepted_state.status == "completed", accepted_status.error
    assert accepted_result["review"]["decision"] == "evidence_sufficient"
    assert accepted_model.call_count <= 8
    observed_summary = {
        "trajectory": {
            "tool_calls": [
                {
                    "tool_id": item.tool_id,
                    "status": item.status,
                    "arguments": item.arguments,
                    "result": item.result,
                    "error": item.error,
                }
                for item in accepted_state.tool_ledger
            ]
        }
    }
    score_input = _frozen_score_input(
        observed_summary,
        accepted_result,
        accepted_state,
        accepted_status,
        provider_requests=0,
        conservative_cost_usd=0.0,
    )
    gold_path = tmp_path / "scripted-gold.json"
    gold_path.write_text(json.dumps({
        "P0_CN2": {
            "answerable": True,
            "required_entities": ["青岚计划", "李明", "王芳"],
        }
    }, ensure_ascii=False), encoding="utf-8")
    ledger = AppendOnlyProviderLedger(
        tmp_path / "scripted-run-ledger.jsonl",
        batch_id="scripted-runtime-postrun-score",
        run_id="scripted-runtime-postrun-score",
        max_requests=1,
        max_runtime_runs=1,
        max_input_tokens_per_request=64,
    )
    try:
        ledger.finish_run(status="COMPLETED", usage_summary={"provider_requests": 0})
        scripted_score = _score_gold_after_provider_stop(
            ledger, score_input, gold_path, _sha256(gold_path)
        )
        assert scripted_score["label"] == "ANSWERED_CORRECT"
        assert scripted_score["required_verified_reads_present"] is True
    finally:
        ledger.close()

    rejected_status, rejected_state, rejected_result, rejected_model = (
        _run_phase14_typed_fake_runtime(tmp_path / "rejected", monkeypatch, wrong_owner=True)
    )
    assert rejected_status.status == rejected_state.status == "failed"
    assert rejected_state.error.code == "TEAM_DECISION_CONTRACT_ERROR"
    assert rejected_result["result"] is None
    assert rejected_model.call_count <= 8


def test_independent_ledger_is_append_only_capped_and_marks_unknown_as_terminal(tmp_path: Path):
    class Delegate:
        vertexai = False
        api_endpoint = "https://generativelanguage.googleapis.com/"

        def __init__(self) -> None:
            self.calls = 0

        def assert_developer_api(self) -> None:
            return None

        def count_tokens(self, **_kwargs: Any) -> Any:
            self.calls += 1
            return {"total_tokens": 7}

        def generate_content(self, **_kwargs: Any) -> Any:
            self.calls += 1
            raise TimeoutError("private transport details must not be logged")

    path = tmp_path / "ledger.jsonl"
    ledger = AppendOnlyProviderLedger(
        path,
        batch_id="offline-test",
        run_id="offline-test-run",
        max_requests=2,
        max_runtime_runs=1,
        max_input_tokens_per_request=64,
    )
    delegate = Delegate()
    client = LedgeredGeminiClient(delegate, ledger)
    try:
        ledger.runtime_started()
        client.count_tokens(model=MODEL, request={"contents": [{"text": "synthetic prompt"}]})
        with pytest.raises(UncertainProviderRequest, match="UNCERTAIN_STOP"):
            client.generate_content(model=MODEL, contents=[{"text": "synthetic prompt"}], config={"max_output_tokens": 8})
        with pytest.raises(SmokeBlocked, match="TERMINAL_STOP"):
            client.count_tokens(model=MODEL, request={"contents": []})
    finally:
        ledger.close()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in records].count("request_reserved") == 2
    assert records[-1]["status"] == "UNCERTAIN"
    assert all("synthetic prompt" not in line for line in path.read_text(encoding="utf-8").splitlines())
    assert all("private transport details" not in line for line in path.read_text(encoding="utf-8").splitlines())
    assert delegate.calls == 2


def test_independent_ledger_caps_count_and_generation_requests_before_transport(tmp_path: Path):
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        def count_tokens(self, **_kwargs: Any) -> Any:
            self.calls += 1
            return {"total_tokens": 7}

        def generate_content(self, **_kwargs: Any) -> Any:
            self.calls += 1
            return {
                "model_version": MODEL,
                "usage_metadata": {
                    "prompt_token_count": 7,
                    "candidates_token_count": 1,
                    "thoughts_token_count": 0,
                    "cost_usd": 0.00001,
                },
            }

    path = tmp_path / "capped-ledger.jsonl"
    ledger = AppendOnlyProviderLedger(
        path,
        batch_id="offline-cap-test",
        run_id="offline-cap-run",
        max_requests=2,
        max_runtime_runs=1,
        max_input_tokens_per_request=64,
    )
    delegate = Delegate()
    client = LedgeredGeminiClient(delegate, ledger)
    try:
        ledger.runtime_started()
        client.count_tokens(model=MODEL, request={"contents": []})
        client.generate_content(model=MODEL, contents=[], config={"max_output_tokens": 8})
        with pytest.raises(SmokeBlocked, match="REQUEST_CAP_REACHED"):
            client.count_tokens(model=MODEL, request={"contents": []})
        with pytest.raises(SmokeBlocked, match="RUNTIME_RUN_CAP_REACHED"):
            ledger.runtime_started()
    finally:
        ledger.close()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in records].count("request_reserved") == 2
    assert [row["event"] for row in records].count("runtime_started") == 1
    generated = next(
        row for row in records
        if row["event"] == "request_finished" and row["usage"].get("output_tokens") == 1
    )
    assert generated["usage"]["provider_reported_cost_usd"] == 0.00001
    assert generated["usage"]["estimated_cost_usd"] > 0
    assert all("local_stop_usd" not in row and "reserved_worst_case_cost_usd" not in row for row in records)
    assert delegate.calls == 2


def test_real_p14_cn2_typed_gemini38_is_separately_opt_in_and_authorized():
    profile = os.environ.get(RUN_PROFILE_ENV, G38_002_PROFILE)
    if profile == G38_002_PROFILE and os.environ.get(OPT_IN_ENV) != "1":
        pytest.skip("P14-CN2 Gemini 3.8 real acceptance is opt-in")
    if profile in {G38_004_PROFILE, G38_005_PROFILE}:
        profile_config = _run_profile_config(profile)
        if os.environ.get(profile_config["authorization_environment_variable"]) != profile_config[
            "required_authorization"
        ]:
            pytest.skip(f"{profile} requires its exact run-scoped authorization")
    _run_real_case(_profile=profile)



def test_g38_ledgered_sdk_boundary_preserves_provider_output_default(tmp_path: Path):
    calls = []

    class FakeSdkClient:
        vertexai = False
        _api_client = SimpleNamespace(
            _http_options={"base_url": "https://generativelanguage.googleapis.com/"}
        )

        def __init__(self):
            self.models = SimpleNamespace(generate_content=self.generate_at_sdk_boundary)

        def assert_developer_api(self):
            return None

        def generate_at_sdk_boundary(self, **kwargs):
            calls.append(copy.deepcopy(kwargs))
            return {
                "model_version": MODEL,
                "usage_metadata": {
                    "prompt_token_count": 12,
                    "candidates_token_count": 34,
                    "thoughts_token_count": 5,
                    "cached_content_token_count": 2,
                },
            }

    ledger = AppendOnlyProviderLedger(
        tmp_path / "g38-sdk-boundary.jsonl",
        batch_id="offline-sdk-boundary",
        run_id="offline-sdk-boundary",
        max_requests=1,
        max_runtime_runs=1,
        max_input_tokens_per_request=None,
    )
    ledger.runtime_started()
    client = LedgeredGeminiClient(FakeSdkClient(), ledger)
    try:
        client.generate_content(
            model=MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
        ledger.finish_run(status="OFFLINE_COMPLETE", usage_summary={})
    finally:
        ledger.close()

    assert len(calls) == 1
    assert "max_output_tokens" not in calls[0]["config"]
    rows = [
        json.loads(line)
        for line in (tmp_path / "g38-sdk-boundary.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    request = next(row for row in rows if row.get("event") == "request_reserved")
    assert request["max_output_tokens"] is None


def test_startup_block_diagnostic_is_allowlisted_and_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    path = _write_startup_diagnostic(
        tmp_path,
        run_id="offline-startup-block",
        stage="SDK_CLIENT_INITIALIZATION",
        error=SmokeBlocked("SDK_ENDPOINT_IDENTITY_MISMATCH"),
        provider_requests=0,
    )
    diagnostic = json.loads(path.read_text(encoding="utf-8"))
    assert diagnostic == {
        "run_id": "offline-startup-block",
        "stage": "SDK_CLIENT_INITIALIZATION",
        "error_code": "SDK_ENDPOINT_IDENTITY_MISMATCH",
        "terminal_status": "BLOCKED_BEFORE_PROVIDER",
        "provider_requests": 0,
    }
    assert "secret" not in path.read_text(encoding="utf-8").casefold()
    unknown_path = _write_startup_diagnostic(
        tmp_path,
        run_id="offline-unknown-startup",
        stage="UNRECOGNIZED_STAGE",
        error=RuntimeError("secret raw error"),
        provider_requests=0,
    )
    unknown = json.loads(unknown_path.read_text(encoding="utf-8"))
    assert unknown["stage"] == "UNKNOWN_STAGE"
    assert unknown["error_code"] == "UNKNOWN_STARTUP_ERROR"
    assert "secret raw error" not in unknown_path.read_text(encoding="utf-8")

    module = importlib.import_module(__name__)
    monkeypatch.setenv(OPT_IN_ENV, "1")
    monkeypatch.setenv(AUTHORIZATION_ENV, REQUIRED_AUTHORIZATION)
    monkeypatch.setenv(MODEL_ENV, MODEL)
    monkeypatch.setenv("GEMINI_API_KEY", "offline-synthetic-key")
    monkeypatch.setattr(module, "_preflight_local_retrieval_model", lambda: None)
    monkeypatch.setattr(
        module,
        "build_gemini_sdk_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private sdk detail")),
    )
    with pytest.raises(SmokeBlocked, match="SDK_CLIENT_INITIALIZATION_FAILED"):
        _run_real_case(
            _ledger_path=tmp_path / "sdk-block-ledger.jsonl",
            _artifact_root=tmp_path / "sdk-block-artifacts",
        )
    runtime_diagnostic = next(
        (tmp_path / "sdk-block-artifacts" / "startup_diagnostics").glob("*.json")
    )
    recorded = json.loads(runtime_diagnostic.read_text(encoding="utf-8"))
    assert recorded["stage"] == "SDK_CLIENT_INITIALIZATION"
    assert recorded["error_code"] == "SDK_CLIENT_INITIALIZATION_FAILED"
    assert recorded["provider_requests"] == 0
    assert "private sdk detail" not in runtime_diagnostic.read_text(encoding="utf-8")


def test_g38_same_entrypoint_fake_transport_reaches_reviewer_and_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import httpx
    monkeypatch.setenv(OPT_IN_ENV, "1")
    monkeypatch.setenv(AUTHORIZATION_ENV, REQUIRED_AUTHORIZATION)
    monkeypatch.setenv(MODEL_ENV, MODEL)
    monkeypatch.setenv("GEMINI_API_KEY", "offline-synthetic-key")
    module = importlib.import_module(__name__)
    real_builder = module.build_gemini_sdk_client
    sdk_options: list[dict[str, Any]] = []

    def build_client(api_key: str, *, http_options_kwargs: dict[str, Any]):
        retry = http_options_kwargs["retry_options"]
        sdk_options.append({
            "timeout": http_options_kwargs["timeout"],
            "attempts": retry.attempts,
        })
        return real_builder(api_key, http_options_kwargs=http_options_kwargs)

    monkeypatch.setattr(module, "build_gemini_sdk_client", build_client)
    monkeypatch.setattr(
        module,
        "_new_usage_only_guard",
        lambda *_args, **_kwargs: pytest.fail("legacy M1.2 guard was reached"),
    )

    observed = json.loads(
        (REPO / "work" / "phase14_gemini" / "runs" / "P14-CN2" / "observed.json")
        .read_text(encoding="utf-8")
    )
    queries = [
        action["query"]
        for action in observed["tool_actions"]
        if action["tool_id"] == "search_notes"
    ]
    assert len(queries) == 2
    requests: list[dict[str, Any]] = []
    scorer_called_after_ledger_stop: list[bool] = []

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from walk(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from walk(nested)

    def function_responses(body: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            part["functionResponse"]
            for part in walk(body)
            if isinstance(part.get("functionResponse"), dict)
        ]

    def evidence_for(body: dict[str, Any], *, name: str, suffix: str) -> dict[str, Any]:
        for function_response in function_responses(body):
            if function_response.get("name") != name:
                continue
            response = function_response.get("response")
            for item in walk(response):
                path = item.get("relative_path")
                if isinstance(path, str) and path.endswith(suffix):
                    return item
        raise AssertionError(f"synthetic tool history omitted {suffix}")

    def source_context(subgoal_id: str) -> dict[str, Any]:
        if subgoal_id == "owner":
            return {"subgoal_id": "owner", "query_variant": "primary"}
        return {
            "subgoal_id": "leader",
            "query_variant": "primary",
            "dependency_entities": {"owner": "李明"},
        }

    def response(*, parts: list[dict[str, Any]], turn: int) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "modelVersion": MODEL,
                "responseId": f"fake-response-{turn}",
                "usageMetadata": {
                    "promptTokenCount": 100 + turn,
                    "candidatesTokenCount": 20 + turn,
                    "thoughtsTokenCount": 5,
                    "cachedContentTokenCount": 2,
                },
                "candidates": [{
                    "content": {"role": "model", "parts": parts},
                    "finishReason": "STOP",
                }],
            },
            request=httpx.Request("POST", "https://generativelanguage.googleapis.com/fake"),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.url.path.endswith("models/gemini-3.8-flash:generateContent")
        body = json.loads(request.content)
        assert body.get("model") in (None, MODEL)
        generation_config = body.get("generationConfig", {})
        assert "maxOutputTokens" not in generation_config
        requests.append(body)
        turn = len(requests)
        if turn == 1:
            args = {
                "query": queries[0],
                "source_context": source_context("owner"),
                "limit": 5,
            }
            name = "search_notes"
        elif turn == 2:
            owner = evidence_for(body, name="search_notes", suffix="owner.md")
            args = {"note_ref": owner["evidence_id"]}
            name = "read_verified_note"
        elif turn == 3:
            args = {
                "query": queries[1],
                "source_context": source_context("leader"),
                "limit": 5,
            }
            name = "search_notes"
        elif turn == 4:
            leader = evidence_for(body, name="search_notes", suffix="leader.md")
            args = {"note_ref": leader["evidence_id"]}
            name = "read_verified_note"
        elif turn == 5:
            owner = evidence_for(body, name="read_verified_note", suffix="owner.md")
            leader = evidence_for(body, name="read_verified_note", suffix="leader.md")
            final = {
                "schema_version": FINAL_PROTOCOL,
                "claims": [
                    {
                        "subject": "青岚计划",
                        "predicate": "owner_of_project",
                        "object": "李明",
                        "evidence_ids": [owner["evidence_id"]],
                        "quote": "截至 2025-03-01，李明是青岚计划的负责人。",
                        "valid_on": "2025-03-01",
                        "temporal_kind": "snapshot",
                    },
                    {
                        "subject": "李明",
                        "predicate": "reports_to",
                        "object": "王芳",
                        "evidence_ids": [leader["evidence_id"]],
                        "quote": "李明的直属领导是王芳。",
                        "valid_on": None,
                        "temporal_kind": None,
                    },
                ],
            }
            return response(
                parts=[{"text": json.dumps(final, ensure_ascii=False)}],
                turn=turn,
            )
        else:
            raise AssertionError("Runtime exceeded frozen scripted response sequence")
        return response(
            parts=[{
                "functionCall": {"id": f"fake-call-{turn}", "name": name, "args": args},
                "thoughtSignature": "c2ln",
            }],
            turn=turn,
        )

    real_score = _score_gold_after_provider_stop

    def score_after_stop(ledger, *args, **kwargs):
        scorer_called_after_ledger_stop.append(ledger.run_finished and not ledger.in_flight)
        return real_score(ledger, *args, **kwargs)

    monkeypatch.setattr(
        module,
        "_score_gold_after_provider_stop",
        score_after_stop,
    )
    transport = httpx.MockTransport(handler)
    case_root = _run_real_case(
        _ledger_path=tmp_path / "independent" / "fake-ledger.jsonl",
        # Keep the artifact path below the Windows limit for durable tool results.
        _artifact_root=tmp_path.parent / f"a{tmp_path.name[-1]}",
        _http_transport=transport,
    )

    summary = json.loads((case_root / "typed_final_runtime_summary.json").read_text(encoding="utf-8"))
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "independent" / "fake-ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    manifest = json.loads((case_root.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert summary["runtime_status"] == "completed"
    assert summary["provider_model_versions"] == [MODEL] * 5
    assert summary["final_protocol"] == FINAL_PROTOCOL
    assert summary["typed_final_schema_passed"] is True
    assert summary["reviewer_accepted"] is True
    assert summary["independent_gold_score"]["label"] == "ANSWERED_CORRECT"
    assert summary["count_tokens_requests"] == 0
    assert summary["provider_requests"] == len(requests) == 5
    assert summary["thinking_tokens_reported"] == 25
    assert summary["cached_tokens_reported"] == 10
    assert summary["estimated_cost_usd"] > 0
    assert len(scorer_called_after_ledger_stop) == 1 and scorer_called_after_ledger_stop[0]
    assert manifest["request_timeout_seconds"] == 130
    assert manifest["automatic_retries"] == 0
    assert manifest["concurrency"] == 1
    assert manifest["max_runtime_steps"] == 18
    assert manifest["max_provider_requests"] == 18
    assert sdk_options == [{"timeout": 130000, "attempts": 1}]
    assert all("maxOutputTokens" not in body.get("generationConfig", {}) for body in requests)
    assert any(
        "thoughtSignature" in part
        for body in requests[1:]
        for content in body.get("contents", [])
        for part in content.get("parts", [])
    )
    assert sum(row.get("event") == "runtime_started" for row in ledger_rows) == 1
    assert sum(row.get("event") == "request_reserved" for row in ledger_rows) == 5
    assert all(row.get("request_kind") == "generate_content" for row in ledger_rows if row.get("event") == "request_reserved")
    assert ledger_rows[-1]["event"] == "run_finished"
    model_visible = json.dumps(requests, ensure_ascii=False)
    assert "owner_of_project: subject=project, object=person" in model_visible
    assert "reports_to: subject=person, object=person" in model_visible
    assert "x-linkloom-relation-definitions" in model_visible
    assert "required_entities" not in model_visible
    assert "required_source_suffixes" not in model_visible
    assert "answerable" not in model_visible.casefold()
