"""M1.2 real-provider Team Decision smoke and its offline safety harness.

The real-provider test in this module is deliberately opt-in.  The focused
offline tests below exercise the gates without constructing an SDK client,
reading a credential, contacting a Provider, or spending tokens.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
import copy
from dataclasses import asdict, dataclass, field
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import logging
import math
import os
import re
from pathlib import Path
import shutil
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen as default_urlopen
from unittest.mock import patch
from uuid import uuid4

import pytest

from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.agents.providers.retry_policy import classify_retryable_failure
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault


MODULE_NAME = "tests.smoke.test_m12_real_provider_team_decision_smoke"

REPO = Path(__file__).resolve().parents[2]
SEED_ROOT = REPO / "docs" / "requirements" / "m1_team_decision_eval_seed"
DATASET_PATH = SEED_ROOT / "dataset.jsonl"
ARTIFACT_ROOT = REPO / ".artifacts" / "m1_2_real_provider_smoke"

MODEL = "gemini-3.8-flash"
OPT_IN_ENV = "LINKLOOM_RUN_REAL_PROVIDER_SMOKE"
AUTHORIZATION_ENV = "LINKLOOM_REAL_PROVIDER_SMOKE_AUTHORIZATION"
MODEL_ENV = "LINKLOOM_GATE_A_MODEL"
MPS_001_RERUN_ENV = "LINKLOOM_M12_MPS_001_RERUN"
REQUIRED_AUTHORIZATION = "AUTHORIZE REAL PROVIDER SMOKE"
SDK_VERSION = "2.22.0"
PER_REQUEST_OUTPUT_CAP = 512
REQUEST_TIMEOUT_SECONDS = 30
AUTOMATIC_RETRY_COUNT = 0
MAX_TRANSPORT_RETRIES_PER_REQUEST = 0
TRANSPORT_RETRY_BASE_DELAY_SECONDS = 0.25
TRANSPORT_ATTEMPTS_FIELD = "__linkloom_transport_attempts"
UNAVAILABLE = "UNAVAILABLE"
COUNT_TOKENS_COUNTER_KIND = "official_developer_api_models.count_tokens"
COUNT_TOKENS_FAILURE_BODY_LIMIT = 4_096
COUNT_TOKENS_FAILURE_GENERIC_MESSAGE = "countTokens request failed"
STATIC_COUNT_TOKENS_STRUCTURAL_RESERVE = 512
_REAL_AUTHORIZATION_SENTINEL = object()

_COUNT_TOKENS_DIAGNOSTIC_KEYS = frozenset(
    {"exception_class", "http_status", "provider_error_code", "failure_kind", "message"}
)
_COUNT_TOKENS_FAILURE_KINDS = frozenset(
    {"SYSTEM_INSTRUCTION_SHAPE_400", "UNCLASSIFIED"}
)
_KNOWN_GEMINI_PROVIDER_ERROR_CODES = frozenset(
    {
        "OK",
        "CANCELLED",
        "UNKNOWN",
        "INVALID_ARGUMENT",
        "DEADLINE_EXCEEDED",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "RESOURCE_EXHAUSTED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "INTERNAL",
        "UNAVAILABLE",
        "DATA_LOSS",
    }
)
# These are intentionally duplicated, immutable identity values rather than
# values discovered from the working tree at run time.  The run must refuse
# to proceed if the checked-in freeze or any synthetic source workspace drifts.
FROZEN_DATASET_SHA256 = (
    "49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F"
)
FROZEN_GOLDEN8_CASE_IDS = (
    "mps-001",
    "aer-002",
    "drm-003",
    "inc-004",
    "ret-005",
    "iti-005",
    "drm-002",
    "aer-005",
)
FROZEN_WORKSPACE_FINGERPRINTS = {
    "agent_evaluation_rollout": "72B1BDA67CC60E53592785633377754ADC5B2FE11B634971B61C6ABF381CF3F8",
    "data_retention_migration": "75DBC6DE5676E5ED47A5B6A9F334BC4FB7FE1796AE74755D6B93905DE4F6583B",
    "deployment_incident": "594B4AED850D08B2EDD1752832A5A4E03C618F3602074EF70AE8A220DD0C59F1",
    "internal_tool_integration": "4A8DBAAEDB1D673D2090A161F5E03E160AFB4286EC0A39E3A0D0752689258A2C",
    "model_provider_selection": "980D255FB33D9A937905797F904BDD47566A81C6E4FBE0A6FD664B8823DD3DA0",
    "retrieval_upgrade": "8EBFFA7FE555ED537AB487CFB8DDE40C8ED1DAF90A6CDF04D7BAEFAAC971F3DB",
}
DEVELOPER_API_ENDPOINT = "generativelanguage.googleapis.com"
DEVELOPER_API_BASE_URL = f"https://{DEVELOPER_API_ENDPOINT}/"


class SmokeBlocked(RuntimeError):
    """A fixed, safe failure that never includes provider payloads or secrets."""


class CountTokensFailure(SmokeBlocked):
    """A terminal count failure carrying only a pre-sanitized diagnostic."""

    def __init__(
        self,
        diagnostic: dict[str, int | str],
        *,
        transport_attempts: int = 1,
    ) -> None:
        super().__init__("COUNT_TOKENS_FAILED")
        self.diagnostic = dict(diagnostic)
        self.transport_attempts = transport_attempts


@dataclass(frozen=True)
class SmokeCase:
    """Only public fields allowed to cross into the real execution path."""

    case_id: str
    workspace_id: str
    user_question: str
    budget_id: str


@dataclass(frozen=True)
class CaseBudget:
    case_id: str
    max_provider_requests: int
    max_model_turns: int
    max_input_tokens: int
    max_output_tokens: int
    max_elapsed_provider_seconds: int
    request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS
    per_request_output_tokens: int = PER_REQUEST_OUTPUT_CAP
    automatic_retry_count: int = AUTOMATIC_RETRY_COUNT
    max_preflight_requests: int | None = None
    max_preflight_counted_input_tokens: int | None = None
    max_elapsed_preflight_seconds: int | None = None
    per_request_input_tokens: int | None = None

    @property
    def preflight_requests_limit(self) -> int:
        return (
            self.max_preflight_requests
            if self.max_preflight_requests is not None
            else self.max_provider_requests
        )

    @property
    def preflight_input_limit(self) -> int:
        return (
            self.max_preflight_counted_input_tokens
            if self.max_preflight_counted_input_tokens is not None
            else self.max_input_tokens
        )

    @property
    def per_request_input_limit(self) -> int:
        return (
            self.per_request_input_tokens
            if self.per_request_input_tokens is not None
            else self.max_input_tokens
        )

    @property
    def preflight_elapsed_limit(self) -> int:
        return (
            self.max_elapsed_preflight_seconds
            if self.max_elapsed_preflight_seconds is not None
            else self.max_elapsed_provider_seconds
        )


@dataclass(frozen=True)
class AggregateBudget:
    max_provider_requests: int
    max_model_turns: int
    max_input_tokens: int
    max_output_tokens: int
    max_elapsed_provider_seconds: int
    max_cost_usd: float
    max_preflight_requests: int | None = None
    max_preflight_counted_input_tokens: int | None = None
    max_elapsed_preflight_seconds: int | None = None

    @property
    def preflight_requests_limit(self) -> int:
        return (
            self.max_preflight_requests
            if self.max_preflight_requests is not None
            else self.max_provider_requests
        )

    @property
    def preflight_input_limit(self) -> int:
        return (
            self.max_preflight_counted_input_tokens
            if self.max_preflight_counted_input_tokens is not None
            else self.max_input_tokens
        )

    @property
    def preflight_elapsed_limit(self) -> int:
        return (
            self.max_elapsed_preflight_seconds
            if self.max_elapsed_preflight_seconds is not None
            else self.max_elapsed_provider_seconds
        )


@dataclass(frozen=True)
class PricingSnapshot:
    provider: str
    snapshot_date: str
    input_usd_per_million: float
    output_usd_per_million: float
    currency: str = "USD"


@dataclass(frozen=True)
class BudgetManifest:
    model_id: str
    pricing: PricingSnapshot
    cases: tuple[CaseBudget, ...]
    aggregate: AggregateBudget
    automatic_retry_count: int = AUTOMATIC_RETRY_COUNT

    def case(self, case_id: str) -> CaseBudget:
        for value in self.cases:
            if value.case_id == case_id:
                return value
        raise SmokeBlocked("budget manifest does not contain the selected case")

    def worst_case_estimated_cost_usd(self) -> float:
        input_cost = (
            Decimal(
                self.aggregate.max_input_tokens
                + self.aggregate.preflight_input_limit
            )
            * Decimal(str(self.pricing.input_usd_per_million))
            / Decimal(1_000_000)
        )
        output_cost = (
            Decimal(self.aggregate.max_output_tokens)
            * Decimal(str(self.pricing.output_usd_per_million))
            / Decimal(1_000_000)
        )
        return float(input_cost + output_cost)

    def validate(self) -> None:
        if self.model_id != MODEL:
            raise SmokeBlocked("budget manifest must use the fixed model")
        if self.pricing != PricingSnapshot(
            provider="Gemini Developer API",
            snapshot_date="2026-09-12",
            input_usd_per_million=0.75,
            output_usd_per_million=3.75,
        ):
            raise SmokeBlocked("pricing snapshot is not the approved immutable snapshot")
        if self.automatic_retry_count != 0:
            raise SmokeBlocked("automatic retry must remain zero")
        if tuple(item.case_id for item in self.cases) != (
            "mps-001",
            "aer-002",
            "iti-005",
        ):
            raise SmokeBlocked("budget manifest case order is not the approved selection")
        expected = {
            "mps-001": (3, 3, 4_000, 1_536, 90),
            "aer-002": (6, 6, 12_000, 3_072, 180),
            "iti-005": (4, 4, 6_000, 2_048, 120),
        }
        for item in self.cases:
            if (
                item.max_provider_requests,
                item.max_model_turns,
                item.max_input_tokens,
                item.max_output_tokens,
                item.max_elapsed_provider_seconds,
            ) != expected[item.case_id]:
                raise SmokeBlocked("case budget does not match the approved envelope")
            if item.request_timeout_seconds != REQUEST_TIMEOUT_SECONDS:
                raise SmokeBlocked("request timeout must remain 30 seconds")
            if item.per_request_output_tokens != PER_REQUEST_OUTPUT_CAP:
                raise SmokeBlocked("per-request output cap must remain 512 tokens")
            if item.automatic_retry_count != 0:
                raise SmokeBlocked("case automatic retry must remain zero")
            if item.preflight_requests_limit != item.max_provider_requests:
                raise SmokeBlocked("countTokens case request envelope changed")
            if item.preflight_input_limit != item.max_input_tokens:
                raise SmokeBlocked("countTokens case input envelope changed")
            if item.preflight_elapsed_limit != item.max_elapsed_provider_seconds:
                raise SmokeBlocked("countTokens case time envelope changed")
        sums = {
            "max_provider_requests": sum(item.max_provider_requests for item in self.cases),
            "max_model_turns": sum(item.max_model_turns for item in self.cases),
            "max_input_tokens": sum(item.max_input_tokens for item in self.cases),
            "max_output_tokens": sum(item.max_output_tokens for item in self.cases),
            "max_elapsed_provider_seconds": sum(
                item.max_elapsed_provider_seconds for item in self.cases
            ),
        }
        aggregate_values = {
            key: getattr(self.aggregate, key)
            for key in sums
        }
        if sums != aggregate_values:
            raise SmokeBlocked("aggregate budget does not equal the case ceilings")
        if self.aggregate.preflight_requests_limit != 13:
            raise SmokeBlocked("aggregate countTokens request limit must remain 13")
        if self.aggregate.preflight_input_limit != 22_000:
            raise SmokeBlocked("aggregate countTokens input limit must remain 22000")
        if self.aggregate.preflight_elapsed_limit != 390:
            raise SmokeBlocked("aggregate countTokens time limit must remain 390 seconds")
        if self.worst_case_estimated_cost_usd() > self.aggregate.max_cost_usd:
            raise SmokeBlocked("worst-case estimated cost exceeds the approved ceiling")


PUBLIC_CASES = (
    SmokeCase(
        case_id="mps-001",
        workspace_id="model_provider_selection",
        user_question="Which model provider was finally approved for the Atlas Lantern pilot?",
        budget_id="m1_2_mps_001",
    ),
    SmokeCase(
        case_id="aer-002",
        workspace_id="agent_evaluation_rollout",
        user_question=(
            "What is the current evaluation rollout boundary, and which reporting, "
            "multilingual, and threshold actions are still open?"
        ),
        budget_id="m1_2_aer_002",
    ),
    SmokeCase(
        case_id="iti-005",
        workspace_id="internal_tool_integration",
        user_question=(
            "Who is assigned to own the export checksum review and export-retention "
            "work, and what are their deadlines?"
        ),
        budget_id="m1_2_iti_005",
    ),
)


BUDGET_MANIFEST = BudgetManifest(
    model_id=MODEL,
    pricing=PricingSnapshot(
        provider="Gemini Developer API",
        snapshot_date="2026-09-12",
        input_usd_per_million=0.75,
        output_usd_per_million=3.75,
    ),
    cases=(
        CaseBudget(
            "mps-001",
            3,
            3,
            4_000,
            1_536,
            90,
            max_preflight_requests=3,
            max_preflight_counted_input_tokens=4_000,
            max_elapsed_preflight_seconds=90,
        ),
        CaseBudget(
            "aer-002",
            6,
            6,
            12_000,
            3_072,
            180,
            max_preflight_requests=6,
            max_preflight_counted_input_tokens=12_000,
            max_elapsed_preflight_seconds=180,
        ),
        CaseBudget(
            "iti-005",
            4,
            4,
            6_000,
            2_048,
            120,
            max_preflight_requests=4,
            max_preflight_counted_input_tokens=6_000,
            max_elapsed_preflight_seconds=120,
        ),
    ),
    aggregate=AggregateBudget(
        max_provider_requests=13,
        max_model_turns=13,
        max_input_tokens=22_000,
        max_output_tokens=6_656,
        max_elapsed_provider_seconds=390,
        max_cost_usd=0.25,
        max_preflight_requests=13,
        max_preflight_counted_input_tokens=22_000,
        max_elapsed_preflight_seconds=390,
    ),
)

MPS_001_RERUN_CASE = PUBLIC_CASES[0]
MPS_001_RERUN_CASE_BUDGET = CaseBudget(
    "mps-001",
    12,
    12,
    160_000,
    16_384,
    90,
    per_request_output_tokens=8_192,
    max_preflight_requests=12,
    max_preflight_counted_input_tokens=160_000,
    max_elapsed_preflight_seconds=90,
    per_request_input_tokens=50_000,
)
MAX_TOTAL_SMOKE_COST = 0.35  # USD; single frozen-case smoke, enforced by the guard.
MPS_001_RERUN_AGGREGATE_BUDGET = AggregateBudget(
    max_provider_requests=12,
    max_model_turns=12,
    max_input_tokens=160_000,
    max_output_tokens=16_384,
    max_elapsed_provider_seconds=90,
    max_cost_usd=MAX_TOTAL_SMOKE_COST,
    max_preflight_requests=12,
    max_preflight_counted_input_tokens=160_000,
    max_elapsed_preflight_seconds=90,
)


@dataclass(frozen=True)
class RealSettings:
    model_id: str
    api_key: str


def load_real_settings(environ: Mapping[str, str] | None = None) -> RealSettings:
    """Read the credential only after the explicit real-smoke opt-in."""

    source = os.environ if environ is None else environ
    if source.get(OPT_IN_ENV) != "1":
        pytest.skip("M1.2 real smoke requires explicit opt-in.")
    if source.get(AUTHORIZATION_ENV) != REQUIRED_AUTHORIZATION:
        raise SmokeBlocked("exact real-provider smoke authorization is required")
    BUDGET_MANIFEST.validate()
    configured_model = source.get(MODEL_ENV, MODEL)
    if configured_model != MODEL:
        raise SmokeBlocked("real smoke requires the fixed model gemini-3.8-flash")
    api_key = source.get("GEMINI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        pytest.skip("M1.2 real smoke requires GEMINI_API_KEY after opt-in.")
    return RealSettings(model_id=MODEL, api_key=api_key)


@dataclass(frozen=True)
class PreflightCount:
    """One exact count result or conservative input reserve for one call."""

    tokens: int
    cost_usd: float | None = None


@dataclass
class CountTokensFallbackState:
    """Share a qualified countTokens unavailability across one G3 run."""

    unavailable: bool = False
    reason: str | None = None
    failure_class: str | None = None


@dataclass
class AggregateUsage:
    provider_requests: int = 0
    model_turns: int = 0
    estimated_input_tokens: int = 0
    preflight_count_requests: int = 0
    preflight_transport_attempts: int = 0
    inference_transport_attempts: int = 0
    preflight_counted_input_tokens: int = 0
    reported_input_tokens: int = 0
    reported_output_tokens: int = 0
    reported_thinking_tokens: int | None = None
    reported_billable_output_tokens: int = 0
    reserved_unknown_output_tokens: int = 0
    provider_elapsed_seconds: float = 0.0
    preflight_elapsed_seconds: float = 0.0
    reported_cost_usd: float | None = None
    reported_count_cost_usd: float | None = None
    preflight_records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def inference_input_tokens(self) -> int:
        """Explicit name for the inference-side reserved input ledger."""

        return self.estimated_input_tokens


def _mapping_or_attr(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _safe_json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return ""


def _safe_count_tokens_error_payload(error: BaseException) -> dict[str, Any]:
    """Read at most one bounded JSON error body without retaining it."""

    try:
        reader = getattr(error, "read", None)
        if not callable(reader):
            return {}
        raw = reader(COUNT_TOKENS_FAILURE_BODY_LIMIT)
    except Exception:
        return {}
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        return {}
    try:
        payload = json.loads(text[:COUNT_TOKENS_FAILURE_BODY_LIMIT])
    except (TypeError, ValueError, UnicodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    detail = payload.get("error", payload)
    return detail if isinstance(detail, dict) else {}


def _safe_http_status(value: Any) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return UNAVAILABLE


def _safe_provider_error_code(value: Any) -> str:
    if isinstance(value, str) and value in _KNOWN_GEMINI_PROVIDER_ERROR_CODES:
        return value
    return UNAVAILABLE


def _sanitize_count_tokens_message(value: Any, *, api_key: str) -> str:
    """Never persist Provider-supplied prose; retain only a fixed safe message."""

    del value, api_key
    return COUNT_TOKENS_FAILURE_GENERIC_MESSAGE


def _count_tokens_failure_kind(
    *,
    http_status: int | str,
    provider_error_code: str,
    message: Any,
) -> str:
    if (
        http_status == 400
        and provider_error_code == "INVALID_ARGUMENT"
        and isinstance(message, str)
        and "generate_content_request.system_instruction" in message.casefold()
    ):
        return "SYSTEM_INSTRUCTION_SHAPE_400"
    return "UNCLASSIFIED"


def _safe_exception_class(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
        return value
    return "Exception"


def count_request_metadata(request: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted shape only: no text, argument values, headers or tool names."""
    contents = request.get("contents", [])
    config = request.get("config", {})
    parts = [p for c in contents if isinstance(c, dict)
             for p in c.get("parts", []) if isinstance(p, dict)]
    return {
        "request_endpoint": f"https://{DEVELOPER_API_ENDPOINT}/v1beta/models/{MODEL}:countTokens",
        "model": MODEL,
        "sdk_version": SDK_VERSION,
        "transport_path": "urllib.request/raw-Developer-API-generateContentRequest",
        "request_shape": {
            "content_count": len(contents),
            "part_count": len(parts),
            "roles": [c.get("role") if c.get("role") in {"user", "model"} else "other"
                      for c in contents if isinstance(c, dict)],
            "has_history": len(contents) > 1,
            "has_tools": bool(config.get("tools")),
            "has_system_instruction": bool(config.get("system_instruction")),
            "has_function_calls": any("function_call" in p or "functionCall" in p for p in parts),
            "has_function_responses": any("function_response" in p or "functionResponse" in p for p in parts),
            "has_thought_metadata": any("thought" in p for p in parts),
            "has_thought_signatures": any("thought_signature" in p or "thoughtSignature" in p for p in parts),
        },
    }


def _persistable_count_tokens_failure_diagnostic(
    diagnostic: Any,
) -> dict[str, int | str]:
    """Validate the exact safe schema again at the artifact persistence edge."""

    fallback = {
        "exception_class": "Exception",
        "http_status": UNAVAILABLE,
        "provider_error_code": UNAVAILABLE,
        "failure_kind": "UNCLASSIFIED",
        "message": COUNT_TOKENS_FAILURE_GENERIC_MESSAGE,
    }
    legacy_keys = _COUNT_TOKENS_DIAGNOSTIC_KEYS - {"failure_kind"}
    if (
        not isinstance(diagnostic, Mapping)
        or frozenset(diagnostic) not in {_COUNT_TOKENS_DIAGNOSTIC_KEYS, legacy_keys}
    ):
        return fallback
    http_status = _safe_http_status(diagnostic["http_status"])
    provider_error_code = _safe_provider_error_code(diagnostic["provider_error_code"])
    failure_kind = diagnostic.get("failure_kind")
    if failure_kind not in _COUNT_TOKENS_FAILURE_KINDS:
        failure_kind = _count_tokens_failure_kind(
            http_status=http_status,
            provider_error_code=provider_error_code,
            message=diagnostic.get("message"),
        )
    return {
        "exception_class": _safe_exception_class(diagnostic["exception_class"]),
        "http_status": http_status,
        "provider_error_code": provider_error_code,
        "failure_kind": failure_kind,
        "message": COUNT_TOKENS_FAILURE_GENERIC_MESSAGE,
    }


def _safe_count_tokens_failure_diagnostic(
    error: BaseException,
    *,
    api_key: str,
) -> dict[str, int | str]:
    """Extract only approved fields; never stringify or retain the exception."""

    payload = _safe_count_tokens_error_payload(error)
    http_status = _safe_http_status(getattr(error, "code", None))
    if http_status == UNAVAILABLE:
        http_status = _safe_http_status(payload.get("code"))
    provider_error_code = _safe_provider_error_code(payload.get("status"))
    return {
        "exception_class": _safe_exception_class(type(error).__name__),
        "http_status": http_status,
        "provider_error_code": provider_error_code,
        "failure_kind": _count_tokens_failure_kind(
            http_status=http_status,
            provider_error_code=provider_error_code,
            message=payload.get("message"),
        ),
        "message": _sanitize_count_tokens_message(
            payload.get("message"), api_key=api_key
        ),
    }


def _exception_http_status(error: BaseException) -> int | str:
    for candidate in (
        getattr(error, "code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        status = _safe_http_status(candidate)
        if status != UNAVAILABLE:
            return status
    return UNAVAILABLE


def _exception_provider_code(error: BaseException) -> str:
    for candidate in (
        getattr(error, "status", None),
        getattr(error, "reason", None),
    ):
        code = _safe_provider_error_code(candidate)
        if code != UNAVAILABLE:
            return code
    return UNAVAILABLE


def _is_retryable_transport_failure(
    error: BaseException,
    *,
    diagnostic: Mapping[str, Any] | None = None,
) -> bool:
    """Compatibility wrapper around the shared provider-neutral retry policy."""
    return classify_retryable_failure(error, diagnostic) is not None


def _contains_vertex_billing_path(value: Any) -> bool:
    text = _safe_json_text(value).casefold()
    return any(
        marker in text
        for marker in (
            "vertexai",
            "aiplatform.googleapis.com",
            "vertex.googleapis.com",
            "vertex billing",
        )
    )


def _assert_gold_free(value: Any, field_name: str = "generation request") -> None:
    """Reject evaluation-only data before either remote request is attempted."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise SmokeBlocked("GENERATION_REQUEST_UNREPRESENTABLE")
            lowered = key.casefold()
            compact = "".join(character for character in lowered if character.isalnum())
            if (
                lowered.startswith("expected_")
                or "gold" in lowered
                or "evaluator" in lowered
                or "evaluation" in lowered
                or "required_claim" in lowered
                or "golden_assert" in lowered
                or "requiredclaim" in compact
            ):
                raise SmokeBlocked("GOLD_OR_EVALUATION_DATA_FORBIDDEN")
            _assert_gold_free(nested, f"{field_name}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_gold_free(nested, f"{field_name}[{index}]")


def _parse_count_tokens_response(response: Any) -> PreflightCount:
    """Normalize only the official count response; malformed data fails closed."""

    tokens = None
    for field_name in ("total_tokens", "totalTokenCount", "totalTokens"):
        tokens = _mapping_or_attr(response, field_name, None)
        if tokens is not None:
            break
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
        raise SmokeBlocked("COUNT_TOKENS_RESPONSE_MALFORMED")
    cost = _mapping_or_attr(response, "cost_usd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "count_cost_usd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "costUsd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "countCostUsd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "billed_cost_usd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "billedCostUsd", None)
    if cost is None:
        usage = _mapping_or_attr(response, "usage_metadata", None)
        if usage is None:
            usage = _mapping_or_attr(response, "usage", None)
        cost = _mapping_or_attr(usage, "cost_usd", None)
    if cost is not None and (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or cost < 0
        or not math.isfinite(float(cost))
    ):
        raise SmokeBlocked("COUNT_TOKENS_RESPONSE_MALFORMED")
    return PreflightCount(tokens=tokens, cost_usd=float(cost) if cost is not None else None)


def _contains_replayed_thought_signature(request: dict[str, Any]) -> bool:
    for content in request.get("contents", []):
        if not isinstance(content, Mapping):
            continue
        for part in content.get("parts", []):
            if isinstance(part, Mapping) and (
                "thought_signature" in part or "thoughtSignature" in part
            ):
                return True
    return False


def _contains_secret(value: Any, secret_detector: Callable[[str], bool] | None) -> bool:
    if secret_detector is None:
        return False
    return secret_detector(_safe_json_text(value))


def _reported_usage(
    response: Any,
) -> tuple[int | None, int | None, int | None, float | None]:
    usage = _mapping_or_attr(response, "usage_metadata", None)
    if usage is None:
        usage = _mapping_or_attr(response, "usage", None)
    if usage is None:
        return None, None, None, None
    input_tokens = _mapping_or_attr(usage, "prompt_token_count", None)
    if input_tokens is None:
        input_tokens = _mapping_or_attr(usage, "input_tokens", None)
    if input_tokens is None:
        input_tokens = _mapping_or_attr(usage, "promptTokenCount", None)
    output_tokens = _mapping_or_attr(usage, "candidates_token_count", None)
    if output_tokens is None:
        output_tokens = _mapping_or_attr(usage, "output_tokens", None)
    if output_tokens is None:
        output_tokens = _mapping_or_attr(usage, "candidatesTokenCount", None)
    # Gemini usage metadata has used both ``thoughts_token_count`` and
    # ``thinking_token_count`` spellings across SDK/API revisions.  Preserve
    # either field when present; absence remains explicitly unavailable.
    thinking_tokens = None
    for field_name in (
        "thoughts_token_count",
        "thinking_token_count",
        "thoughtsTokenCount",
        "thinkingTokenCount",
        "thoughts_tokens",
        "thinking_tokens",
    ):
        thinking_tokens = _mapping_or_attr(usage, field_name, None)
        if thinking_tokens is not None:
            break
    cost = _mapping_or_attr(usage, "cost_usd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "cost_usd", None)
    if cost is None:
        cost = _mapping_or_attr(usage, "costUsd", None)
    if cost is None:
        cost = _mapping_or_attr(response, "costUsd", None)
    for value in (input_tokens, output_tokens, thinking_tokens):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise SmokeBlocked("PROVIDER_USAGE_UNAVAILABLE")
    if cost is not None and (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or cost < 0
        or not math.isfinite(float(cost))
    ):
        raise SmokeBlocked("PROVIDER_USAGE_UNAVAILABLE")
    return (
        input_tokens,
        output_tokens,
        thinking_tokens,
        float(cost) if cost is not None else None,
    )


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    return float(
        Decimal(input_tokens) * Decimal("0.75") / Decimal(1_000_000)
        + Decimal(output_tokens) * Decimal("3.75") / Decimal(1_000_000)
    )


def _combined_cost_usd(
    inference_input_tokens: int,
    preflight_input_tokens: int,
    output_tokens: int,
) -> float:
    return float(
        Decimal(inference_input_tokens + preflight_input_tokens)
        * Decimal("0.75")
        / Decimal(1_000_000)
        + Decimal(output_tokens) * Decimal("3.75") / Decimal(1_000_000)
    )


_REST_CONFIG_FIELDS = {
    "system_instruction": "systemInstruction",
    "tools": "tools",
    "tool_config": "toolConfig",
    "safety_settings": "safetySettings",
    "cached_content": "cachedContent",
    # The adapter uses these SDK-friendly spellings at config top-level; the
    # Developer GenerateContentRequest nests them under generationConfig.
    "response_mime_type": "responseMimeType",
    "response_schema": "responseSchema",
    "candidate_count": "candidateCount",
    "max_output_tokens": "maxOutputTokens",
    "stop_sequences": "stopSequences",
    "temperature": "temperature",
    "top_p": "topP",
    "top_k": "topK",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
    "seed": "seed",
    "thinking_config": "thinkingConfig",
    # This is an SDK transport control, not a token-bearing REST field.
    "automatic_function_calling": None,
}
_REST_GENERATION_FIELDS = {
    key: value
    for key, value in _REST_CONFIG_FIELDS.items()
    if key
    not in {
        "tools",
        "system_instruction",
        "safety_settings",
        "tool_config",
        "cached_content",
        "automatic_function_calling",
    }
}
_REST_REQUEST_CONFIG_FIELDS = {
    "system_instruction",
    "tools",
    "tool_config",
    "safety_settings",
    "cached_content",
    "automatic_function_calling",
}
_REST_NESTED_FIELDS = {
    "function_declarations": "functionDeclarations",
    "functionDeclarations": "functionDeclarations",
    "parameters_json_schema": "parametersJsonSchema",
    "parametersJsonSchema": "parametersJsonSchema",
    "function_call": "functionCall",
    "functionCall": "functionCall",
    "function_response": "functionResponse",
    "functionResponse": "functionResponse",
    "thought_signature": "thoughtSignature",
    "thoughtSignature": "thoughtSignature",
    "function_calling_config": "functionCallingConfig",
    "functionCallingConfig": "functionCallingConfig",
    "include_thoughts": "includeThoughts",
    "includeThoughts": "includeThoughts",
    "thinking_budget": "thinkingBudget",
    "thinkingBudget": "thinkingBudget",
}


def _map_rest_nested(value: Any) -> Any:
    if isinstance(value, dict):
        mapped: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
            rest_key = _REST_NESTED_FIELDS.get(key, key)
            _merge_mapped_field(mapped, rest_key, _map_rest_nested(nested))
        return mapped
    if isinstance(value, list):
        return [_map_rest_nested(item) for item in value]
    return copy.deepcopy(value)


def _merge_mapped_field(mapping: dict[str, Any], key: str, value: Any) -> None:
    """Reject conflicting SDK aliases instead of silently overwriting them."""

    if key in mapping and mapping[key] != value:
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
    mapping[key] = value


def _map_count_config(config: Any) -> dict[str, Any]:
    """Map known SDK config spellings without dropping token-bearing fields."""

    if not isinstance(config, dict):
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
    mapped: dict[str, Any] = {}
    generation: dict[str, Any] = {}
    for key, value in config.items():
        rest_key = _REST_CONFIG_FIELDS.get(key)
        if rest_key is None:
            if key == "automatic_function_calling":
                if value != {"disable": True}:
                    raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
                continue
            raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
        if key not in _REST_REQUEST_CONFIG_FIELDS:
            nested_rest_key = _REST_GENERATION_FIELDS.get(key)
            if nested_rest_key is None:
                raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
            _merge_mapped_field(generation, nested_rest_key, _map_rest_nested(value))
        else:
            rest_value = value
            if key == "system_instruction" and isinstance(value, str):
                # GenerateContentConfig accepts a string and the installed SDK
                # converts it to Content(parts=[Part(text=...)], role="user").
                # A raw REST count request must preserve that same wire shape.
                rest_value = {"parts": [{"text": value}], "role": "user"}
            _merge_mapped_field(mapped, rest_key, _map_rest_nested(rest_value))
    if generation:
        mapped["generationConfig"] = generation
    return mapped


def _static_conservative_input_token_upper_bound(request: dict[str, Any]) -> int:
    """Bound the frozen G3 text-only request plus fixed structural-token reserve.

    This is deliberately restricted to one user text part, one string system
    instruction, and the frozen scalar generation config. For that geometry,
    UTF-8 bytes upper-bound non-empty text token pieces; 512 tokens reserve
    role/content/system boundaries and protocol framing. Unknown/multimodal/
    tool shapes are not estimated. The result must still fit the hard input cap.
    """

    contents = request.get("contents") if isinstance(request, dict) else None
    config = request.get("config") if isinstance(request, dict) else None
    if (
        not isinstance(contents, list)
        or len(contents) != 1
        or not isinstance(contents[0], dict)
        or contents[0].get("role") != "user"
        or not isinstance(contents[0].get("parts"), list)
        or len(contents[0]["parts"]) != 1
        or not isinstance(contents[0]["parts"][0], dict)
        or set(contents[0]["parts"][0]) != {"text"}
        or not isinstance(contents[0]["parts"][0].get("text"), str)
        or not isinstance(config, dict)
        or not isinstance(config.get("system_instruction"), str)
        or set(config)
        - {
            "system_instruction",
            "temperature",
            "max_output_tokens",
            "automatic_function_calling",
        }
    ):
        raise SmokeBlocked("STATIC_COUNT_TOKENS_BOUND_UNSAFE")

    serialized = _count_endpoint_generate_request(request)
    encoded = json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return len(encoded) + STATIC_COUNT_TOKENS_STRUCTURAL_RESERVE


def _matches_count_tokens_system_instruction_shape_failure(
    diagnostic: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(diagnostic, Mapping):
        return False
    return (
        diagnostic.get("http_status") == 400
        and diagnostic.get("provider_error_code") == "INVALID_ARGUMENT"
        and diagnostic.get("failure_kind") == "SYSTEM_INSTRUCTION_SHAPE_400"
    )


def _matches_safe_count_tokens_transport_failure(
    diagnostic: Mapping[str, Any] | None,
) -> bool:
    """Permit only explicit no-response transport failures to use the bound."""
    if not isinstance(diagnostic, Mapping):
        return False
    if diagnostic.get("http_status") not in {None, UNAVAILABLE}:
        return False
    if diagnostic.get("provider_error_code") not in {None, UNAVAILABLE}:
        return False
    normalized = dict(diagnostic)
    for key in ("http_status", "provider_error_code"):
        if normalized.get(key) == UNAVAILABLE:
            normalized[key] = None
    classification = classify_retryable_failure(None, normalized)
    return isinstance(classification, str) and classification.startswith("TRANSPORT_")


def _count_endpoint_generate_request(request: dict[str, Any]) -> dict[str, Any]:
    """Build the documented complete Developer API GenerateContentRequest."""

    if not isinstance(request, dict):
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
    if set(request) != {"model", "contents", "config"}:
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
    model = request.get("model")
    contents = request.get("contents")
    config = request.get("config")
    if model != MODEL or not isinstance(contents, list) or not isinstance(config, dict):
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
    _assert_gold_free(request)
    mapped = {"model": f"models/{model}", "contents": _map_rest_nested(contents)}
    mapped.update(_map_count_config(config))
    _assert_gold_free(mapped)
    try:
        json.dumps(mapped, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, UnicodeError):
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE") from None
    return mapped


def _raw_developer_count_tokens(
    api_key: str,
    *,
    model: str,
    request: dict[str, Any],
    urlopen: Callable[..., Any] = default_urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call only the official Developer API count endpoint with a full request."""

    if not isinstance(api_key, str) or not api_key.strip() or model != MODEL:
        raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
    payload = {"generateContentRequest": _count_endpoint_generate_request(request)}
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    endpoint = (
        "https://"
        + DEVELOPER_API_ENDPOINT
        + "/v1beta/models/"
        + quote(model, safe="")
        + ":countTokens"
    )
    attempts = 0
    while True:
        attempts += 1
        try:
            http_request = UrlRequest(
                endpoint,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            response = urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS)
            reader = getattr(response, "read", None)
            if not callable(reader):
                raise ValueError
            raw = reader()
            if hasattr(response, "close") and callable(response.close):
                response.close()
            parsed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except SmokeBlocked:
            raise
        except Exception as error:
            diagnostic = _safe_count_tokens_failure_diagnostic(error, api_key=api_key)
            if (
                attempts <= MAX_TRANSPORT_RETRIES_PER_REQUEST
                and _is_retryable_transport_failure(error, diagnostic=diagnostic)
            ):
                sleep(TRANSPORT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempts - 1)))
                continue
            # Keep only an approved, bounded diagnostic; never retain the raw error.
            raise CountTokensFailure(
                diagnostic,
                transport_attempts=attempts,
            ) from None
        if not isinstance(parsed, dict):
            raise SmokeBlocked("COUNT_TOKENS_RESPONSE_MALFORMED")
        parsed = dict(parsed)
        parsed[TRANSPORT_ATTEMPTS_FIELD] = attempts
        return parsed


class RecordingDelegate:
    """Offline-only SDK-shaped delegate used to exercise transport guards."""

    def __init__(
        self,
        response: Any | None = None,
        exception: BaseException | None = None,
        *,
        count_response: Any | None = None,
        count_exception: BaseException | None = None,
    ):
        self.response = {"text": "synthetic response"} if response is None else response
        self.exception = exception
        self.count_response = (
            {"total_tokens": 7} if count_response is None else count_response
        )
        self.count_exception = count_exception
        self.calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.vertexai = False
        self.api_endpoint = DEVELOPER_API_BASE_URL
        self.api_surface = "developer_api"

    def count_tokens(self, *, model: str, request: dict[str, Any]) -> Any:
        self.count_calls.append(
            {"model": model, "request": copy.deepcopy(request)}
        )
        if self.count_exception is not None:
            raise self.count_exception
        return self.count_response

    def generate_content(
        self, *, model: str, contents: list[dict[str, Any]], config: dict[str, Any]
    ) -> Any:
        self.calls.append(
            {
                "model": model,
                "contents": copy.deepcopy(contents),
                "config": copy.deepcopy(config),
                "request": {
                    "model": model,
                    "contents": copy.deepcopy(contents),
                    "config": copy.deepcopy(config),
                },
            }
        )
        if self.exception is not None:
            raise self.exception
        return self.response


class BudgetedGeminiClient:
    """Bounded inference transport with one official count preflight per call."""

    def __init__(
        self,
        delegate: Any,
        *,
        case: SmokeCase,
        case_budget: CaseBudget,
        aggregate: AggregateUsage,
        aggregate_budget: AggregateBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        secret_detector: Callable[[str], bool] | None = None,
        allow_static_count_tokens_fallback: bool = False,
        count_tokens_fallback_state: CountTokensFallbackState | None = None,
        count_tokens_transport_fallback_only: bool = False,
        on_response_accepted: Callable[[Any], None] | None = None,
    ) -> None:
        if not callable(getattr(delegate, "generate_content", None)):
            raise SmokeBlocked("injected Gemini client surface is unavailable")
        if not callable(getattr(delegate, "count_tokens", None)):
            raise SmokeBlocked("official models.count_tokens surface is unavailable")
        if not isinstance(case, SmokeCase) or case.case_id != case_budget.case_id:
            raise SmokeBlocked("case and budget identity do not match")
        if case_budget.automatic_retry_count != 0:
            raise SmokeBlocked("automatic retry must remain zero")
        self.delegate = delegate
        self.case = case
        self.case_budget = case_budget
        self.aggregate = aggregate
        self.aggregate_budget = aggregate_budget or BUDGET_MANIFEST.aggregate
        self.clock = clock
        self.sleep = sleep
        self.secret_detector = secret_detector
        self.allow_static_count_tokens_fallback = allow_static_count_tokens_fallback
        self.count_tokens_transport_fallback_only = count_tokens_transport_fallback_only
        self.on_response_accepted = on_response_accepted
        self.count_tokens_fallback_state = (
            count_tokens_fallback_state or CountTokensFallbackState()
        )
        self.provider_calls = 0
        self.model_turns = 0
        self.estimated_input_tokens = 0
        self.preflight_count_requests = 0
        self.preflight_transport_attempts = 0
        self.inference_transport_attempts = 0
        self.preflight_counted_input_tokens = 0
        self.reported_input_tokens = 0
        self.reported_output_tokens = 0
        self.reported_thinking_tokens: int | None = None
        self.reported_billable_output_tokens = 0
        self.reserved_unknown_output_tokens = 0
        self.provider_elapsed_seconds = 0.0
        self.preflight_elapsed_seconds = 0.0
        self.reported_cost_usd: float | None = None
        self.reported_count_cost_usd: float | None = None
        self.preflight_records: list[dict[str, Any]] = []
        self.automatic_retry_count = 0
        self.last_guard_state = "READY"
        self.provider_response_accepted = False
        self._terminal = False
        self._pending_preflight: dict[str, Any] | None = None
        self._continuation_thinking_token_reserve = 0

    @property
    def inference_input_tokens(self) -> int:
        """Explicit name for the inference-side reserved input ledger."""

        return self.estimated_input_tokens

    def _assert_developer_api(self) -> None:
        assert_developer_api = getattr(self.delegate, "assert_developer_api", None)
        if callable(assert_developer_api):
            try:
                assert_developer_api()
            except SmokeBlocked:
                self.last_guard_state = "BLOCKED_VERTEX_OR_ENDPOINT"
                self._terminal = True
                raise
            return
        vertexai = getattr(self.delegate, "vertexai", None)
        endpoint = getattr(self.delegate, "api_endpoint", None)
        if not _vertexai_is_explicitly_false(self.delegate) or _is_vertex_endpoint(endpoint):
            self.last_guard_state = "BLOCKED_VERTEX_OR_ENDPOINT"
            self._terminal = True
            raise SmokeBlocked("Vertex or non-Developer API configuration is forbidden")
        if vertexai is not False or not _is_exact_developer_base_url(endpoint):
            self.last_guard_state = "BLOCKED_VERTEX_OR_ENDPOINT"
            self._terminal = True
            raise SmokeBlocked("Developer API configuration is unavailable")

    def _build_request(
        self,
        model: str,
        contents: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if model != MODEL:
            raise SmokeBlocked("real smoke requires the fixed model gemini-3.8-flash")
        if not isinstance(contents, list) or not isinstance(config, dict):
            raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
        if config.get("automatic_function_calling") != {"disable": True}:
            raise SmokeBlocked("PROVIDER_SIDE_TOOL_EXECUTION_MUST_BE_DISABLED")
        if "generation_config" in config or "generationConfig" in config:
            raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE")
        outgoing_config = copy.deepcopy(config)
        outgoing_config["max_output_tokens"] = self.case_budget.per_request_output_tokens
        request = {
            "model": model,
            "contents": copy.deepcopy(contents),
            "config": outgoing_config,
        }
        _assert_gold_free(request)
        try:
            json.dumps(request, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, UnicodeError):
            raise SmokeBlocked("COUNT_TOKENS_REQUEST_UNREPRESENTABLE") from None
        return request

    def _terminal_block(self) -> None:
        if self._terminal:
            self.last_guard_state = "BLOCKED_TERMINAL_FIRST_RESULT"
            raise SmokeBlocked("PREFLIGHT_GUARD_TERMINAL")

    def _append_preflight_record(self, record: dict[str, Any]) -> None:
        self.preflight_records.append(record)
        self.aggregate.preflight_records.append(record)

    def _record_preflight_transport_attempts(
        self,
        record: dict[str, Any],
        attempts: Any,
    ) -> None:
        if (
            isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or attempts < 1
            or attempts > MAX_TRANSPORT_RETRIES_PER_REQUEST + 1
        ):
            attempts = 1
        record["transport_attempts"] = attempts
        self.preflight_transport_attempts += attempts
        self.aggregate.preflight_transport_attempts += attempts

    def _preflight_failure(
        self,
        record: dict[str, Any],
        code: str,
    ) -> None:
        record["status"] = "failed"
        record["error_code"] = code
        self.last_guard_state = code
        self._terminal = True
        raise SmokeBlocked(code) from None

    def _static_count_tokens_fallback(
        self,
        request: dict[str, Any],
        record: dict[str, Any],
        *,
        reason: str,
        failure_class: str | None = None,
    ) -> PreflightCount:
        estimate = _static_conservative_input_token_upper_bound(request)
        if estimate > self.case_budget.per_request_input_limit:
            self._preflight_failure(record, "STATIC_COUNT_TOKENS_BOUND_UNSAFE")
        record["status"] = "static_fallback"
        record["count_tokens_status"] = "UNAVAILABLE"
        record["token_estimation_source"] = "STATIC_CONSERVATIVE"
        record["billing_preflight_uncertainty"] = True
        record["estimated_input_token_upper_bound"] = estimate
        record["fallback_reason"] = reason
        if failure_class is not None:
            record["failure_class"] = failure_class
        return PreflightCount(tokens=estimate)

    def _preflight(
        self,
        request: dict[str, Any],
    ) -> PreflightCount:
        self._terminal_block()
        self._assert_developer_api()
        # Do not issue even a counting request after the shared budget is spent.
        output_tokens_reserved = (
            self.aggregate.reported_billable_output_tokens
            + self.aggregate.reserved_unknown_output_tokens
        )
        spent = max(
            self.aggregate.reported_cost_usd or 0.0,
            _combined_cost_usd(
                self.aggregate.estimated_input_tokens,
                self.aggregate.preflight_counted_input_tokens,
                output_tokens_reserved,
            ),
        )
        if spent >= self.aggregate_budget.max_cost_usd:
            self._terminal = True
            self.last_guard_state = "PROJECTED_COST_BUDGET_EXCEEDED"
            raise SmokeBlocked("PROJECTED_COST_BUDGET_EXCEEDED")
        if self.preflight_count_requests >= self.case_budget.preflight_requests_limit:
            self._terminal = True
            self.last_guard_state = "PREFLIGHT_REQUEST_BUDGET_EXCEEDED"
            raise SmokeBlocked("PREFLIGHT_REQUEST_BUDGET_EXCEEDED")
        if (
            self.aggregate.preflight_count_requests
            >= self.aggregate_budget.preflight_requests_limit
        ):
            self._terminal = True
            self.last_guard_state = "AGGREGATE_PREFLIGHT_REQUEST_BUDGET_EXCEEDED"
            raise SmokeBlocked("AGGREGATE_PREFLIGHT_REQUEST_BUDGET_EXCEEDED")
        if self.model_turns >= self.case_budget.max_model_turns:
            self._terminal = True
            self.last_guard_state = "MODEL_TURN_BUDGET_EXCEEDED"
            raise SmokeBlocked("MODEL_TURN_BUDGET_EXCEEDED")
        if self.aggregate.model_turns >= self.aggregate_budget.max_model_turns:
            self._terminal = True
            self.last_guard_state = "AGGREGATE_MODEL_TURN_BUDGET_EXCEEDED"
            raise SmokeBlocked("AGGREGATE_MODEL_TURN_BUDGET_EXCEEDED")
        if self.provider_calls >= self.case_budget.max_provider_requests:
            self._terminal = True
            self.last_guard_state = "PROVIDER_REQUEST_BUDGET_EXCEEDED"
            raise SmokeBlocked("PROVIDER_REQUEST_BUDGET_EXCEEDED")
        if self.aggregate.provider_requests >= self.aggregate_budget.max_provider_requests:
            self._terminal = True
            self.last_guard_state = "AGGREGATE_PROVIDER_REQUEST_BUDGET_EXCEEDED"
            raise SmokeBlocked("AGGREGATE_PROVIDER_REQUEST_BUDGET_EXCEEDED")
        if self.preflight_elapsed_seconds >= self.case_budget.preflight_elapsed_limit:
            self._terminal = True
            self.last_guard_state = "CASE_PREFLIGHT_TIME_EXCEEDED"
            raise SmokeBlocked("CASE_PREFLIGHT_TIME_EXCEEDED")
        if (
            self.aggregate.preflight_elapsed_seconds
            >= self.aggregate_budget.preflight_elapsed_limit
        ):
            self._terminal = True
            self.last_guard_state = "AGGREGATE_PREFLIGHT_TIME_EXCEEDED"
            raise SmokeBlocked("AGGREGATE_PREFLIGHT_TIME_EXCEEDED")
        if self.reported_billable_output_tokens + self.reserved_unknown_output_tokens >= self.case_budget.max_output_tokens:
            self._terminal = True
            self.last_guard_state = "OUTPUT_TOKEN_BUDGET_EXCEEDED"
            raise SmokeBlocked("OUTPUT_TOKEN_BUDGET_EXCEEDED")
        if (
            self.aggregate.reported_billable_output_tokens
            + self.aggregate.reserved_unknown_output_tokens
            >= self.aggregate_budget.max_output_tokens
        ):
            self._terminal = True
            self.last_guard_state = "AGGREGATE_OUTPUT_TOKEN_BUDGET_EXCEEDED"
            raise SmokeBlocked("AGGREGATE_OUTPUT_TOKEN_BUDGET_EXCEEDED")

        record: dict[str, Any] = {
            "sequence": len(self.preflight_records) + 1,
            "request_diagnostic": count_request_metadata(request),
            "model": request["model"],
            "request": copy.deepcopy(request),
            "status": "attempted",
            "counted_input_tokens": UNAVAILABLE,
            "count_tokens_status": "PENDING",
            "token_estimation_source": "PENDING",
            "billing_preflight_uncertainty": False,
            "reported_input_tokens": UNAVAILABLE,
            "reported_output_tokens": UNAVAILABLE,
            "reported_thinking_tokens": UNAVAILABLE,
            "reported_cost_usd": UNAVAILABLE,
            "usage_delta": UNAVAILABLE,
            "elapsed_seconds": UNAVAILABLE,
            "cost_usd": UNAVAILABLE,
            "transport_attempts": 0,
            "inference_transport_attempts": 0,
        }
        self._append_preflight_record(record)
        started = self.clock()
        response: Any = None
        static_fallback = False
        if self.allow_static_count_tokens_fallback and self.count_tokens_fallback_state.unavailable:
            count = self._static_count_tokens_fallback(
                request,
                record,
                reason=self.count_tokens_fallback_state.reason
                or "COUNT_TOKENS_PREVIOUSLY_MARKED_UNAVAILABLE",
                failure_class=self.count_tokens_fallback_state.failure_class,
            )
            static_fallback = True
        else:
            self.preflight_count_requests += 1
            self.aggregate.preflight_count_requests += 1
            try:
                # Use the full GenerateContentRequest envelope; the installed
                # Developer API CountTokensConfig converter rejects these fields.
                response = self.delegate.count_tokens(
                    model=request["model"],
                    request=copy.deepcopy(request),
                )
                self._record_preflight_transport_attempts(
                    record,
                    _mapping_or_attr(response, TRANSPORT_ATTEMPTS_FIELD, 1),
                )
            except SmokeBlocked as error:
                elapsed = max(0.0, self.clock() - started)
                record["elapsed_seconds"] = elapsed
                if isinstance(error, CountTokensFailure):
                    self._record_preflight_transport_attempts(
                        record,
                        error.transport_attempts,
                    )
                    diagnostic = _persistable_count_tokens_failure_diagnostic(
                        error.diagnostic
                    )
                    record["failure_diagnostic"] = diagnostic
                else:
                    diagnostic = None
                    if record["transport_attempts"] == 0:
                        self._record_preflight_transport_attempts(record, 1)
                if (
                    self.allow_static_count_tokens_fallback
                    and not self.count_tokens_transport_fallback_only
                    and (
                    _matches_count_tokens_system_instruction_shape_failure(diagnostic)
                    )
                ):
                    reason = "DETERMINISTIC_SYSTEM_INSTRUCTION_SHAPE_400"
                    self.count_tokens_fallback_state.unavailable = True
                    self.count_tokens_fallback_state.reason = reason
                    self.count_tokens_fallback_state.failure_class = (
                        "HTTP_400_SYSTEM_INSTRUCTION_SCHEMA"
                    )
                    count = self._static_count_tokens_fallback(
                        request,
                        record,
                        reason=reason,
                        failure_class=self.count_tokens_fallback_state.failure_class,
                    )
                    static_fallback = True
                elif (
                    self.allow_static_count_tokens_fallback
                    and _matches_safe_count_tokens_transport_failure(diagnostic)
                ):
                    exception_class = str(diagnostic["exception_class"])
                    failure_class = f"TRANSPORT_{exception_class}"
                    reason = f"{failure_class}_COUNT_TOKENS_UNAVAILABLE"
                    self.count_tokens_fallback_state.unavailable = True
                    self.count_tokens_fallback_state.reason = reason
                    self.count_tokens_fallback_state.failure_class = failure_class
                    count = self._static_count_tokens_fallback(
                        request,
                        record,
                        reason=reason,
                        failure_class=failure_class,
                    )
                    static_fallback = True
                else:
                    if "failure_diagnostic" not in record:
                        record["failure_diagnostic"] = {
                            "exception_class": _safe_exception_class(type(error).__name__),
                            "http_status": UNAVAILABLE,
                            "provider_error_code": UNAVAILABLE,
                            "failure_kind": "UNCLASSIFIED",
                            "message": COUNT_TOKENS_FAILURE_GENERIC_MESSAGE,
                        }
                    self.aggregate.preflight_elapsed_seconds += elapsed
                    self.preflight_elapsed_seconds += elapsed
                    safe_count_errors = {
                        "COUNT_TOKENS_FAILED",
                        "COUNT_TOKENS_REQUEST_UNREPRESENTABLE",
                        "COUNT_TOKENS_RESPONSE_MALFORMED",
                    }
                    error_code = str(error)
                    code = error_code if error_code in safe_count_errors else "COUNT_TOKENS_FAILED"
                    self._preflight_failure(record, code)
            except Exception as error:
                elapsed = max(0.0, self.clock() - started)
                self.preflight_elapsed_seconds += elapsed
                self.aggregate.preflight_elapsed_seconds += elapsed
                record["elapsed_seconds"] = elapsed
                record["failure_diagnostic"] = {
                    "exception_class": _safe_exception_class(type(error).__name__),
                    "http_status": _safe_http_status(getattr(error, "code", None)),
                    "provider_error_code": UNAVAILABLE,
                    "failure_kind": "UNCLASSIFIED",
                    "message": COUNT_TOKENS_FAILURE_GENERIC_MESSAGE,
                }
                self._record_preflight_transport_attempts(record, 1)
                self._preflight_failure(record, "COUNT_TOKENS_FAILED")

        elapsed = max(0.0, self.clock() - started)
        self.preflight_elapsed_seconds += elapsed
        self.aggregate.preflight_elapsed_seconds += elapsed
        record["elapsed_seconds"] = elapsed
        if elapsed > self.case_budget.request_timeout_seconds:
            self._preflight_failure(record, "PREFLIGHT_TIMEOUT_EXCEEDED")
        if self.preflight_elapsed_seconds > self.case_budget.preflight_elapsed_limit:
            self._preflight_failure(record, "CASE_PREFLIGHT_TIME_EXCEEDED")
        if (
            self.aggregate.preflight_elapsed_seconds
            > self.aggregate_budget.preflight_elapsed_limit
        ):
            self._preflight_failure(record, "AGGREGATE_PREFLIGHT_TIME_EXCEEDED")
        if static_fallback:
            raw_counted_input_tokens = count.tokens
            continuation_reserve = (
                self._continuation_thinking_token_reserve
                if _contains_replayed_thought_signature(request)
                else 0
            )
            count = PreflightCount(tokens=raw_counted_input_tokens + continuation_reserve)
            record["raw_counted_input_tokens"] = UNAVAILABLE
            record["continuation_thinking_token_reserve"] = continuation_reserve
            record["estimated_input_token_upper_bound"] = count.tokens
        else:
            try:
                count = _parse_count_tokens_response(response)
            except SmokeBlocked:
                self._preflight_failure(record, "COUNT_TOKENS_RESPONSE_MALFORMED")
            except Exception:
                self._preflight_failure(record, "COUNT_TOKENS_RESPONSE_MALFORMED")

            raw_counted_input_tokens = count.tokens
            continuation_reserve = (
                self._continuation_thinking_token_reserve
                if _contains_replayed_thought_signature(request)
                else 0
            )
            count = PreflightCount(
                tokens=raw_counted_input_tokens + continuation_reserve,
                cost_usd=count.cost_usd,
            )
            record["raw_counted_input_tokens"] = raw_counted_input_tokens
            record["continuation_thinking_token_reserve"] = continuation_reserve
            record["counted_input_tokens"] = count.tokens
            record["count_tokens_status"] = "PASS"
            record["token_estimation_source"] = "PROVIDER_COUNT_TOKENS"
            record["billing_preflight_uncertainty"] = False
        if count.cost_usd is not None:
            record["cost_usd"] = count.cost_usd
        if count.tokens > self.case_budget.per_request_input_limit:
            self._preflight_failure(
                record, "PREFLIGHT_REQUEST_INPUT_TOKEN_BUDGET_EXCEEDED"
            )
        if count.tokens > (
            self.case_budget.preflight_input_limit - self.preflight_counted_input_tokens
        ):
            self._preflight_failure(record, "PREFLIGHT_INPUT_TOKEN_BUDGET_EXCEEDED")
        if count.tokens > (
            self.aggregate_budget.preflight_input_limit
            - self.aggregate.preflight_counted_input_tokens
        ):
            self._preflight_failure(record, "AGGREGATE_PREFLIGHT_INPUT_TOKEN_BUDGET_EXCEEDED")

        current_reported_cost = self.aggregate.reported_cost_usd or 0.0
        aggregate_output_reserved = (
            self.aggregate.reported_billable_output_tokens
            + self.aggregate.reserved_unknown_output_tokens
        )
        projected_cost = _combined_cost_usd(
            self.aggregate.estimated_input_tokens + count.tokens,
            self.aggregate.preflight_counted_input_tokens + count.tokens,
            aggregate_output_reserved
            + min(
                self.case_budget.per_request_output_tokens,
                max(
                    0,
                    self.aggregate_budget.max_output_tokens
                    - aggregate_output_reserved,
                ),
            ),
        )
        if count.cost_usd is not None:
            projected_cost = max(
                projected_cost,
                current_reported_cost + count.cost_usd,
            )
        else:
            projected_cost = max(projected_cost, current_reported_cost)
        # A reported bill above the token estimate must still reserve the next
        # request, rather than comparing already-spent cost alone to the cap.
        next_output_cap = min(
            self.case_budget.per_request_output_tokens,
            max(0, self.aggregate_budget.max_output_tokens - aggregate_output_reserved),
        )
        projected_cost = max(
            projected_cost,
            current_reported_cost + _combined_cost_usd(
                count.tokens, count.tokens, next_output_cap
            ) + (count.cost_usd or 0.0),
        )
        if projected_cost > self.aggregate_budget.max_cost_usd:
            self._preflight_failure(record, "PROJECTED_COST_BUDGET_EXCEEDED")
        record["reserved_output_tokens_upper_bound"] = next_output_cap

        self.estimated_input_tokens += count.tokens
        self.preflight_counted_input_tokens += count.tokens
        self.aggregate.estimated_input_tokens += count.tokens
        self.aggregate.preflight_counted_input_tokens += count.tokens
        if count.cost_usd is not None:
            self.reported_count_cost_usd = (
                count.cost_usd
                if self.reported_count_cost_usd is None
                else self.reported_count_cost_usd + count.cost_usd
            )
            self.aggregate.reported_count_cost_usd = (
                count.cost_usd
                if self.aggregate.reported_count_cost_usd is None
                else self.aggregate.reported_count_cost_usd + count.cost_usd
            )
            self.aggregate.reported_cost_usd = (
                self.aggregate.reported_cost_usd or 0.0
            ) + count.cost_usd
        if static_fallback:
            record["status"] = "static_fallback"
            record["counter_kind"] = "STATIC_CONSERVATIVE"
            self.last_guard_state = "PREFLIGHT_STATIC_FALLBACK_ACCEPTED"
        else:
            record["status"] = "success"
            record["counter_kind"] = COUNT_TOKENS_COUNTER_KIND
            self.last_guard_state = "PREFLIGHT_ACCEPTED"
        self._pending_preflight = {
            "record": record,
            "count": count,
            "output_token_reserve": next_output_cap,
        }
        return count

    def _capture_available_response_usage(
        self,
        response: Any,
        *,
        status: str,
    ) -> None:
        """Retain parseable usage before a post-response time guard stops."""

        if self._pending_preflight is None:
            return
        try:
            input_tokens, output_tokens, thinking_tokens, cost = _reported_usage(
                response
            )
        except SmokeBlocked:
            return
        pending = self._pending_preflight
        record = pending["record"]
        preflight = pending["count"]
        record["inference_status"] = status
        record["reported_input_tokens"] = (
            input_tokens if input_tokens is not None else UNAVAILABLE
        )
        record["reported_output_tokens"] = (
            output_tokens if output_tokens is not None else UNAVAILABLE
        )
        record["reported_thinking_tokens"] = (
            thinking_tokens if thinking_tokens is not None else UNAVAILABLE
        )
        record["reported_cost_usd"] = cost if cost is not None else UNAVAILABLE
        record["usage_status"] = (
            "COMPLETE"
            if input_tokens is not None and output_tokens is not None
            else "INCOMPLETE"
        )
        record["usage_missing_fields"] = [
            name
            for name, value in (
                ("input_tokens", input_tokens),
                ("output_tokens", output_tokens),
            )
            if value is None
        ]
        record["usage_delta"] = (
            input_tokens - preflight.tokens
            if input_tokens is not None
            else UNAVAILABLE
        )
        if input_tokens is not None:
            self.reported_input_tokens += input_tokens
            self.aggregate.reported_input_tokens += input_tokens
        if output_tokens is not None:
            self.reported_output_tokens += output_tokens
            self.aggregate.reported_output_tokens += output_tokens
        if thinking_tokens is not None:
            self.reported_thinking_tokens = (
                thinking_tokens
                if self.reported_thinking_tokens is None
                else self.reported_thinking_tokens + thinking_tokens
            )
            self.aggregate.reported_thinking_tokens = (
                thinking_tokens
                if self.aggregate.reported_thinking_tokens is None
                else self.aggregate.reported_thinking_tokens + thinking_tokens
            )
        self._record_billable_output_usage(output_tokens, thinking_tokens)
        if cost is not None:
            self.reported_cost_usd = (
                cost if self.reported_cost_usd is None else self.reported_cost_usd + cost
            )
            self.aggregate.reported_cost_usd = (
                cost
                if self.aggregate.reported_cost_usd is None
                else self.aggregate.reported_cost_usd + cost
            )
        self._pending_preflight = None

    def _record_billable_output_usage(
        self,
        output_tokens: int | None,
        thinking_tokens: int | None,
    ) -> None:
        if output_tokens is None:
            if thinking_tokens is not None:
                self.reported_billable_output_tokens += thinking_tokens
                self.aggregate.reported_billable_output_tokens += thinking_tokens
            pending_reserve = (
                self._pending_preflight.get("output_token_reserve")
                if self._pending_preflight is not None
                else None
            )
            output_reserve = (
                pending_reserve
                if isinstance(pending_reserve, int) and not isinstance(pending_reserve, bool)
                else self.case_budget.per_request_output_tokens
            )
            self._reserve_unknown_output_tokens(
                max(0, output_reserve - (thinking_tokens or 0))
            )
            return
        billable_output_tokens = output_tokens + (thinking_tokens or 0)
        self.reported_billable_output_tokens += billable_output_tokens
        self.aggregate.reported_billable_output_tokens += billable_output_tokens

    def _reserve_unknown_output_tokens(self, reserve: int | None = None) -> None:
        if self.reserved_unknown_output_tokens:
            return
        reserve = self.case_budget.per_request_output_tokens if reserve is None else reserve
        self.reserved_unknown_output_tokens = reserve
        self.aggregate.reserved_unknown_output_tokens += reserve

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> Any:
        self._terminal_block()
        try:
            request = self._build_request(model, contents, config)
            count = self._preflight(request)
            # Re-check the effective transport immediately before the paired
            # inference call; a mutable SDK surface cannot drift to Vertex or
            # a look-alike endpoint after countTokens succeeds.
            self._assert_developer_api()
        except SmokeBlocked:
            self._terminal = True
            if self.last_guard_state == "READY":
                self.last_guard_state = "BLOCKED_PRE_SEND_GUARD"
            raise
        self.provider_calls += 1
        self.model_turns += 1
        self.aggregate.provider_requests += 1
        self.aggregate.model_turns += 1
        started = self.clock()
        self.last_guard_state = "REQUEST_SENT"
        inference_attempts = 0
        while True:
            inference_attempts += 1
            self.inference_transport_attempts += 1
            self.aggregate.inference_transport_attempts += 1
            if self._pending_preflight is not None:
                self._pending_preflight["record"][
                    "inference_transport_attempts"
                ] = inference_attempts
            try:
                response = self.delegate.generate_content(
                    model=request["model"],
                    contents=copy.deepcopy(request["contents"]),
                    config=copy.deepcopy(request["config"]),
                )
                break
            except Exception as exception:
                if _contains_vertex_billing_path(exception):
                    self.last_guard_state = "VERTEX_BILLING_PATH_OBSERVED"
                    self._terminal = True
                    raise SmokeBlocked("VERTEX_BILLING_PATH_OBSERVED") from None
                if (
                    inference_attempts <= MAX_TRANSPORT_RETRIES_PER_REQUEST
                    and _is_retryable_transport_failure(exception)
                ):
                    self.sleep(
                        TRANSPORT_RETRY_BASE_DELAY_SECONDS
                        * (2 ** (inference_attempts - 1))
                    )
                    continue
                elapsed = max(0.0, self.clock() - started)
                self.provider_elapsed_seconds += elapsed
                self.aggregate.provider_elapsed_seconds += elapsed
                self.last_guard_state = "FIRST_ATTEMPT_FAILED"
                self._terminal = True
                if self._pending_preflight is not None:
                    self._pending_preflight["record"]["inference_status"] = "failed"
                # Do not add exception text to any artifact or diagnostic.
                raise
        self.provider_response_accepted = True
        self.last_guard_state = "RESPONSE_ACCEPTED"
        if self.on_response_accepted is not None:
            # This callback is deliberately outside the transport retry block:
            # local persistence failures must not replay an already accepted response.
            try:
                self.on_response_accepted(response)
            except BaseException:
                self._terminal = True
                raise
        elapsed = max(0.0, self.clock() - started)
        self.provider_elapsed_seconds += elapsed
        self.aggregate.provider_elapsed_seconds += elapsed
        if elapsed > self.case_budget.request_timeout_seconds:
            self._capture_available_response_usage(
                response,
                status="response_received_timeout",
            )
            self.last_guard_state = "REQUEST_TIMEOUT_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("REQUEST_TIMEOUT_EXCEEDED")
        if self.provider_elapsed_seconds > self.case_budget.max_elapsed_provider_seconds:
            self._capture_available_response_usage(
                response,
                status="response_received_case_time_limit",
            )
            self.last_guard_state = "CASE_PROVIDER_TIME_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("CASE_PROVIDER_TIME_EXCEEDED")
        if self.aggregate.provider_elapsed_seconds > self.aggregate_budget.max_elapsed_provider_seconds:
            self._capture_available_response_usage(
                response,
                status="response_received_aggregate_time_limit",
            )
            self.last_guard_state = "AGGREGATE_PROVIDER_TIME_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("AGGREGATE_PROVIDER_TIME_EXCEEDED")
        if _contains_vertex_billing_path(response):
            self.last_guard_state = "VERTEX_BILLING_PATH_OBSERVED"
            self._terminal = True
            raise SmokeBlocked("VERTEX_BILLING_PATH_OBSERVED")
        if _contains_secret(response, self.secret_detector):
            self.last_guard_state = "CREDENTIAL_LEAK_BLOCKED"
            self._terminal = True
            raise SmokeBlocked("CREDENTIAL_LEAK_BLOCKED")
        try:
            input_tokens, output_tokens, thinking_tokens, cost = _reported_usage(response)
        except SmokeBlocked:
            self.last_guard_state = "BLOCKED_PROVIDER_USAGE_UNAVAILABLE"
            self._terminal = True
            raise
        if self._pending_preflight is None:
            self.last_guard_state = "BLOCKED_PROVIDER_USAGE_UNAVAILABLE"
            self._terminal = True
            raise SmokeBlocked("PROVIDER_USAGE_UNAVAILABLE")
        preflight = self._pending_preflight["count"]
        response_usage_complete = input_tokens is not None and output_tokens is not None
        self._capture_available_response_usage(
            response,
            status=(
                "response_received"
                if response_usage_complete
                else "response_received_usage_incomplete"
            ),
        )
        if input_tokens is not None and input_tokens > preflight.tokens:
            self.last_guard_state = "PREFLIGHT_UNDERCOUNT_MISMATCH"
            self._terminal = True
            raise SmokeBlocked("PREFLIGHT_UNDERCOUNT_MISMATCH")
        if self.reported_input_tokens > self.case_budget.max_input_tokens:
            self.last_guard_state = "REPORTED_INPUT_TOKEN_BUDGET_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("REPORTED_INPUT_TOKEN_BUDGET_EXCEEDED")
        if self.reported_billable_output_tokens > self.case_budget.max_output_tokens:
            self.last_guard_state = "REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED")
        if self.aggregate.reported_input_tokens > self.aggregate_budget.max_input_tokens:
            self.last_guard_state = "AGGREGATE_REPORTED_INPUT_TOKEN_BUDGET_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("AGGREGATE_REPORTED_INPUT_TOKEN_BUDGET_EXCEEDED")
        if self.aggregate.reported_billable_output_tokens > self.aggregate_budget.max_output_tokens:
            self.last_guard_state = "AGGREGATE_REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("AGGREGATE_REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED")
        if self.aggregate.reported_cost_usd is not None and self.aggregate.reported_cost_usd > self.aggregate_budget.max_cost_usd:
            self.last_guard_state = "REPORTED_COST_BUDGET_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("REPORTED_COST_BUDGET_EXCEEDED")
        if self.reported_cost_usd is not None and self.reported_cost_usd > self.aggregate_budget.max_cost_usd:
            self.last_guard_state = "REPORTED_COST_BUDGET_EXCEEDED"
            self._terminal = True
            raise SmokeBlocked("REPORTED_COST_BUDGET_EXCEEDED")
        if not response_usage_complete:
            self.last_guard_state = "RESPONSE_ACCEPTED_USAGE_INCOMPLETE"
            self._terminal = True
            return response
        self._continuation_thinking_token_reserve = thinking_tokens or 0
        self.last_guard_state = "RESPONSE_ACCEPTED"
        return response


@contextmanager
def _disable_ambient_credential_fallback():
    """Patch the SDK's ambient key lookup while constructing the client."""

    try:
        from google.genai import _api_client
    except Exception:
        raise SmokeBlocked("Gemini SDK ambient credential guard is unavailable") from None
    getter = getattr(_api_client, "get_env_api_key", None)
    if not callable(getter):
        raise SmokeBlocked("Gemini SDK ambient credential guard is unavailable")
    with patch.object(_api_client, "get_env_api_key", return_value=None):
        yield


@contextmanager
def _quiet_sdk():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _nested_client_value(client: Any, names: tuple[str, ...]) -> Any:
    for owner in (client, getattr(client, "_api_client", None)):
        if owner is None:
            continue
        for name in names:
            value = getattr(owner, name, None)
            if value is not None:
                return value
    return None


def _is_vertex_endpoint(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.casefold()
    return any(
        marker in text
        for marker in (
            "aiplatform.googleapis.com",
            "vertex.googleapis.com",
            "vertexai",
            "vertex billing",
        )
    )


def _is_exact_developer_base_url(value: Any) -> bool:
    """Accept only the official HTTPS Developer API authority."""

    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlsplit(value)
        # Accessing ``port`` can itself raise for malformed URL text.
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() == DEVELOPER_API_ENDPOINT
        and parsed.hostname == DEVELOPER_API_ENDPOINT
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _effective_sdk_base_url(client: Any) -> Any:
    """Read the installed SDK transport's effective HTTP base URL only."""

    try:
        api_client = getattr(client, "_api_client", None)
        http_options = getattr(api_client, "_http_options", None)
        if isinstance(http_options, Mapping):
            return http_options.get("base_url")
        return getattr(http_options, "base_url", None)
    except Exception:
        return None


def _vertexai_is_explicitly_false(client: Any) -> bool:
    """Require every exposed SDK vertex flag to be present and exactly False."""

    flags: list[Any] = []
    vertexai_seen = False
    try:
        owners = (client, getattr(client, "_api_client", None))
        for owner in owners:
            if owner is None:
                continue
            for name in ("vertexai", "use_vertexai"):
                value = getattr(owner, name, None)
                if value is not None:
                    if name == "vertexai":
                        vertexai_seen = True
                    flags.append(value)
    except Exception:
        return False
    return vertexai_seen and bool(flags) and all(value is False for value in flags)


class DeveloperApiModelsSurface:
    """Expose inference plus a complete official Developer count surface."""

    def __init__(
        self,
        client: Any,
        *,
        api_key: str | None = None,
        count_tokens_transport: Callable[..., Any] | None = None,
        urlopen: Callable[..., Any] = default_urlopen,
    ):
        models = getattr(client, "models", None)
        if not callable(getattr(models, "generate_content", None)):
            raise SmokeBlocked("Gemini SDK Client.models.generate_content is unavailable")
        if count_tokens_transport is None and not callable(
            getattr(models, "count_tokens", None)
        ):
            raise SmokeBlocked("Gemini SDK Client.models.count_tokens is unavailable")
        effective_base_url = _effective_sdk_base_url(client)
        if not _vertexai_is_explicitly_false(client):
            raise SmokeBlocked("Vertex billing path is forbidden for this smoke")
        if not _is_exact_developer_base_url(effective_base_url):
            raise SmokeBlocked("effective Developer API base URL is unavailable or forbidden")
        self._client = client
        self.models = models
        # These are explicit constructor facts, retained as a pre-send gate.
        self.vertexai = False
        self.api_endpoint = effective_base_url
        self._effective_base_url = effective_base_url
        self.api_surface = "developer_api"
        if count_tokens_transport is not None:
            self._count_tokens_transport = count_tokens_transport
        elif isinstance(api_key, str) and api_key.strip():
            self._count_tokens_transport = lambda **kwargs: _raw_developer_count_tokens(
                api_key,
                urlopen=urlopen,
                **kwargs,
            )
        else:
            raise SmokeBlocked("official Developer countTokens transport is unavailable")

    def assert_developer_api(self) -> None:
        if (
            self.vertexai is not False
            or self.api_surface != "developer_api"
            or not _vertexai_is_explicitly_false(self._client)
        ):
            raise SmokeBlocked("Vertex or non-Developer API configuration is forbidden")
        effective_base_url = _effective_sdk_base_url(self._client)
        if not _is_exact_developer_base_url(effective_base_url):
            raise SmokeBlocked("effective Developer API base URL is unavailable or forbidden")
        if effective_base_url != self._effective_base_url:
            raise SmokeBlocked("effective Developer API base URL changed")

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.assert_developer_api()
        return self.models.generate_content(model=model, contents=contents, config=config)

    def count_tokens(self, *, model: str, request: dict[str, Any]) -> Any:
        self.assert_developer_api()
        if model != MODEL:
            raise SmokeBlocked("real smoke requires the fixed model gemini-3.8-flash")
        try:
            return self._count_tokens_transport(
                model=model,
                request=copy.deepcopy(request),
            )
        except SmokeBlocked:
            raise
        except Exception:
            raise SmokeBlocked("COUNT_TOKENS_FAILED") from None

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def build_official_client(
    api_key: str,
    *,
    genai_module: Any | None = None,
    types_module: Any | None = None,
    ambient_fallback_patch: Any = "__default__",
    count_tokens_transport: Callable[..., Any] | None = None,
    urlopen: Callable[..., Any] = default_urlopen,
    _authorization_sentinel: Any = None,
) -> Any:
    """Construct the Developer API client only in the explicitly enabled path.

    ``genai_module``/``types_module`` and ``ambient_fallback_patch`` are
    dependency seams for offline tests.  The real call imports the official
    SDK lazily and always sets ``vertexai=False``.
    """

    if not isinstance(api_key, str) or not api_key.strip():
        raise SmokeBlocked("Gemini Developer API key is unavailable")
    if genai_module is None and _authorization_sentinel is not _REAL_AUTHORIZATION_SENTINEL:
        raise SmokeBlocked("exact real-provider smoke authorization is required")
    if genai_module is None or types_module is None:
        try:
            if importlib.metadata.version("google-genai") != SDK_VERSION:
                raise SmokeBlocked("Gemini SDK version is not the pinned smoke version")
            from google import genai as genai_module  # type: ignore[no-redef]
            from google.genai import types as types_module  # type: ignore[no-redef]
        except SmokeBlocked:
            raise
        except Exception:
            raise SmokeBlocked("Gemini Developer API SDK is unavailable") from None
    try:
        retry_options = types_module.HttpRetryOptions(attempts=1)
        http_options = types_module.HttpOptions(
            timeout=REQUEST_TIMEOUT_SECONDS * 1000,
            retry_options=retry_options,
        )
        factory_context = (
            _disable_ambient_credential_fallback()
            if ambient_fallback_patch == "__default__"
            else (ambient_fallback_patch or nullcontext())
        )
        with _quiet_sdk(), factory_context:
            client = genai_module.Client(
                api_key=api_key,
                vertexai=False,
                http_options=http_options,
            )
            return DeveloperApiModelsSurface(
                client,
                api_key=api_key,
                count_tokens_transport=count_tokens_transport,
                urlopen=urlopen,
            )
    except SmokeBlocked:
        raise
    except Exception:
        raise SmokeBlocked("Gemini Developer API client construction failed safely") from None


# Kept as a descriptive alias for callers familiar with the earlier smoke.
official_client = build_official_client


def _workspace_for_case(case: SmokeCase) -> Path:
    """Resolve only one of the six checked-in synthetic workspaces."""

    if case.workspace_id not in FROZEN_WORKSPACE_FINGERPRINTS:
        raise SmokeBlocked("selected workspace is not in the frozen synthetic corpus")
    workspaces = (SEED_ROOT / "workspaces").resolve()
    source = (workspaces / case.workspace_id).resolve()
    if not source.is_relative_to(workspaces) or not source.is_dir():
        raise SmokeBlocked("selected synthetic workspace is unavailable")
    return source


def _canonical_tree_fingerprint(root: Path) -> str:
    """Hash relative paths and LF-normalized synthetic file bytes."""

    if not root.is_dir():
        raise SmokeBlocked("frozen synthetic workspace is unavailable")
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest().upper()


def verify_frozen_inputs() -> None:
    """Fail closed if the Golden freeze or any synthetic source has drifted."""

    try:
        dataset_sha = hashlib.sha256(
            DATASET_PATH.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest().upper()
    except OSError:
        raise SmokeBlocked("frozen evaluator dataset is unavailable") from None
    if dataset_sha != FROZEN_DATASET_SHA256:
        raise SmokeBlocked("frozen evaluator dataset fingerprint changed")
    manifest_path = SEED_ROOT / "golden8_manifest.json"
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema_version") != "golden8-freeze/v1"
        or manifest.get("parent_dataset_sha256") != FROZEN_DATASET_SHA256
        or tuple(manifest.get("case_ids", ())) != FROZEN_GOLDEN8_CASE_IDS
    ):
        raise SmokeBlocked("Golden8 freeze fingerprint changed")
    workspace_root = (SEED_ROOT / "workspaces").resolve()
    for workspace_id, expected in FROZEN_WORKSPACE_FINGERPRINTS.items():
        workspace = (workspace_root / workspace_id).resolve()
        if not workspace.is_relative_to(workspace_root):
            raise SmokeBlocked("synthetic workspace path escaped the seed root")
        if _canonical_tree_fingerprint(workspace) != expected:
            raise SmokeBlocked("synthetic workspace fingerprint changed")


def _file_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise SmokeBlocked("smoke source copy is unavailable")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise SmokeBlocked("smoke artifact is unavailable or malformed") from None
    if not isinstance(payload, dict):
        raise SmokeBlocked("smoke artifact is not a JSON object")
    return payload


def _secret_scan(root: Path, secret: str) -> bool:
    """Return whether a credential appears in any generated artifact."""

    if not isinstance(secret, str) or not secret:
        return False
    marker = secret.encode("utf-8")
    for path in root.rglob("*"):
        if path.is_file() and marker in path.read_bytes():
            return True
    return False


def _secret_scan_with_detector(
    root: Path, detector: Callable[[str], bool] | None
) -> bool:
    """Scan generated bytes without ever returning or logging the secret."""

    if detector is None:
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            raise SmokeBlocked("generated artifact could not be secret-scanned") from None
        if detector(text):
            return True
    return False


def _optional_sum(records: list[Any], field_name: str) -> int | None:
    values = [
        record.usage.get(field_name)
        for record in records
        if isinstance(getattr(record, "usage", None), dict)
    ]
    if not values or any(value is None for value in values):
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        return None
    return sum(values)


def _model_records_summary(records: list[Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for record in records:
        action = record.normalized_action if isinstance(record.normalized_action, dict) else None
        values.append(
            {
                "sequence": record.sequence,
                "turn_id": record.turn_id,
                "status": record.status,
                "normalized_action": copy.deepcopy(action),
                "usage": copy.deepcopy(record.usage),
                "provider_request_id": record.provider_request_id,
                "provider_response_id": record.provider_response_id,
                "finish_reason": record.finish_reason,
                "provider_metadata": copy.deepcopy(record.provider_metadata),
                "provider_error": copy.deepcopy(record.provider_error),
                "response_origin": record.response_origin,
            }
        )
    return values


def _tool_records_summary(records: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": record.sequence,
            "call_id": record.call_id,
            "tool_id": record.tool_id,
            "arguments": copy.deepcopy(record.arguments),
            "status": record.status,
            "result": copy.deepcopy(record.result),
            "error": copy.deepcopy(record.error),
        }
        for record in records
    ]


def _termination_summary(state: Any) -> dict[str, Any] | None:
    termination = getattr(state, "termination", None)
    return termination.to_dict() if termination is not None else None


def _is_hard_guard_stop(state: str) -> bool:
    return (
        state.startswith("BLOCKED_")
        or state.startswith("COUNT_TOKENS")
        or state.startswith("PREFLIGHT")
        or state.endswith("_EXCEEDED")
        or "BUDGET_EXCEEDED" in state
        or state in {"CREDENTIAL_LEAK_BLOCKED", "VERTEX_BILLING_PATH_OBSERVED", "FIRST_ATTEMPT_FAILED"}
    )


def _build_observed_summary(
    case: SmokeCase,
    case_budget: CaseBudget,
    status: Any,
    state: Any,
    result: dict[str, Any] | None,
    guard: BudgetedGeminiClient,
    source_before: dict[str, str],
    source_after: dict[str, str],
    copy_before: dict[str, str],
    copy_after: dict[str, str],
) -> dict[str, Any]:
    model_records = list(getattr(state, "model_executions", []))
    tool_records = list(getattr(state, "tool_ledger", []))
    final_actions = [
        record.normalized_action
        for record in model_records
        if isinstance(record.normalized_action, dict)
        and record.normalized_action.get("kind") == "final"
    ]
    visible_refs: list[str] = []
    for record in tool_records:
        raw_result = record.result if isinstance(record.result, dict) else {}
        value = raw_result.get("value")
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                ref = item.get("evidence_id")
                if isinstance(ref, str) and ref and ref not in visible_refs:
                    visible_refs.append(ref)
    input_tokens = _optional_sum(model_records, "input_tokens")
    output_tokens = _optional_sum(model_records, "output_tokens")
    total_tokens = _optional_sum(model_records, "total_tokens")
    per_case_estimated_cost = UNAVAILABLE
    if input_tokens is not None and output_tokens is not None:
        thinking_tokens = guard.reported_thinking_tokens or 0
        per_case_estimated_cost = _combined_cost_usd(
            guard.estimated_input_tokens,
            guard.preflight_counted_input_tokens,
            output_tokens + thinking_tokens,
        )
    runtime_status = getattr(status, "status", None) or getattr(state, "status", UNAVAILABLE)
    return {
        "schema_version": "m1-2-observed-case/v1",
        "requested": {
            "case_id": case.case_id,
            "workspace_id": case.workspace_id,
            "user_question": case.user_question,
            "workflow": "team_decision",
            "model": MODEL,
        },
        "outcome": {
            "requested_outcome": UNAVAILABLE,
            "decision_correctness": UNAVAILABLE,
            "action_correctness": UNAVAILABLE,
            "uncertainty_correctness": UNAVAILABLE,
        },
        "grounding": {
            "material_claim_evidence": UNAVAILABLE,
            "unsupported_claim_count": UNAVAILABLE,
            "invisible_or_fabricated_ref_rejected": UNAVAILABLE,
            "visible_evidence_refs": visible_refs,
        },
        "trajectory": {
            "model_records": _model_records_summary(model_records),
            "tool_calls": _tool_records_summary(tool_records),
            "final_actions": copy.deepcopy(final_actions),
            "chosen_by": "model",
        },
        "runtime": {
            "status": runtime_status,
            "result_ref": getattr(state, "result_ref", None),
            "provider_requests": guard.provider_calls,
            "model_turns": len(model_records),
            "tool_calls": len(tool_records),
            "inference_input_tokens": guard.inference_input_tokens,
            "preflight_count_requests": guard.preflight_count_requests,
            "preflight_transport_attempts": guard.preflight_transport_attempts,
            "inference_transport_attempts": guard.inference_transport_attempts,
            "preflight_counted_input_tokens": guard.preflight_counted_input_tokens,
            "preflight_elapsed_seconds": guard.preflight_elapsed_seconds,
            "preflight_records": copy.deepcopy(guard.preflight_records),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "billable_output_tokens": guard.reported_billable_output_tokens,
            "provider_elapsed_seconds": guard.provider_elapsed_seconds,
            "termination": _termination_summary(state),
            "normalized_error": getattr(status, "error", None)
            or (state.error.to_dict() if getattr(state, "error", None) else None),
            "thinking_tokens": (
                guard.reported_thinking_tokens
                if guard.reported_thinking_tokens is not None
                else UNAVAILABLE
            ),
            "automatic_retry_count": 0,
        },
        "cost": {
            "estimated_cost_usd": per_case_estimated_cost,
            "estimated_cost_snapshot": "2026-09-12",
            "actual_cost_usd": (
                (guard.reported_cost_usd or 0.0)
                + (guard.reported_count_cost_usd or 0.0)
                if guard.reported_cost_usd is not None
                or guard.reported_count_cost_usd is not None
                else UNAVAILABLE
            ),
        },
        "guard": {
            "state": guard.last_guard_state,
            "automatic_retry_count": guard.automatic_retry_count,
            "max_provider_requests": case_budget.max_provider_requests,
            "max_model_turns": case_budget.max_model_turns,
            "max_preflight_requests": case_budget.preflight_requests_limit,
            "max_per_request_input_tokens": case_budget.per_request_input_limit,
            "max_input_tokens": case_budget.max_input_tokens,
            "max_preflight_counted_input_tokens": case_budget.preflight_input_limit,
            "max_output_tokens": case_budget.max_output_tokens,
            "per_request_output_cap": case_budget.per_request_output_tokens,
            "request_timeout_seconds": case_budget.request_timeout_seconds,
            "max_elapsed_preflight_seconds": case_budget.preflight_elapsed_limit,
            "estimated_input_tokens": guard.estimated_input_tokens,
            "inference_input_tokens": guard.inference_input_tokens,
            "preflight_count_requests": guard.preflight_count_requests,
            "preflight_transport_attempts": guard.preflight_transport_attempts,
            "inference_transport_attempts": guard.inference_transport_attempts,
            "preflight_counted_input_tokens": guard.preflight_counted_input_tokens,
            "preflight_elapsed_seconds": guard.preflight_elapsed_seconds,
            "preflight_records": copy.deepcopy(guard.preflight_records),
            "reported_input_tokens": guard.reported_input_tokens,
            "reported_output_tokens": guard.reported_output_tokens,
            "reported_thinking_tokens": guard.reported_thinking_tokens
            if guard.reported_thinking_tokens is not None
            else UNAVAILABLE,
            "reported_count_cost_usd": guard.reported_count_cost_usd
            if guard.reported_count_cost_usd is not None
            else UNAVAILABLE,
            "reported_billable_output_tokens": guard.reported_billable_output_tokens,
        },
        "source_hashes": {
            "source_before": source_before,
            "source_after": source_after,
            "copy_before": copy_before,
            "copy_after": copy_after,
            "unchanged": source_before == source_after and copy_before == copy_after,
        },
        "runtime_state": state.to_dict(),
        "result": copy.deepcopy(result),
    }


def _seal_observed_case(case_root: Path, observed: dict[str, Any]) -> Path:
    """Persist a Gold-free observed summary and a content-addressed seal."""

    summary_path = case_root / "observed_summary.json"
    _write_json(summary_path, observed)
    summary_sha = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    seal_path = case_root / "observed_summary.seal.json"
    _write_json(
        seal_path,
        {
            "schema_version": "m1-2-observed-seal/v1",
            "sealed": True,
            "gold_available": False,
            "observed_summary_sha256": summary_sha,
        },
    )
    return seal_path


def _safe_termination_code(value: Any) -> str:
    """Keep early-stop artifacts to fixed non-sensitive guard identifiers."""

    if not isinstance(value, str) or not value:
        return "EARLY_TERMINATION"
    if any(not (character.isalnum() or character == "_") for character in value):
        return "EARLY_TERMINATION"
    if value.startswith(
        (
            "BLOCKED_",
            "COUNT_TOKENS",
            "PREFLIGHT",
            "AGGREGATE_",
            "CASE_",
            "REQUEST_",
            "REPORTED_",
            "VERTEX_",
            "CREDENTIAL_",
            "FIRST_ATTEMPT_",
            "M1_2_",
        )
    ):
        return value
    return "EARLY_TERMINATION"


def _write_evaluator_unavailable(
    case_root: Path,
    *,
    case_id: str,
    reason: str,
) -> Path:
    """Record why a sealed case could not reach the post-run evaluator."""

    seal_path = case_root / "observed_summary.seal.json"
    if not seal_path.is_file():
        raise SmokeBlocked("EVALUATOR_UNAVAILABLE_REQUIRES_SEALED_CASE")
    safe_reason = _safe_termination_code(reason)
    report = {
        "schema_version": "m1-2-post-run-evaluation-unavailable/v1",
        "case_id": case_id,
        "evaluated_after_seal": False,
        "status": "unavailable",
        "reason": safe_reason,
        "gold_loaded": False,
    }
    path = case_root / "post_run_evaluation.unavailable.json"
    _write_json(path, report)
    return path


def _write_early_termination_artifacts(
    run_root: Path,
    *,
    case_roots: list[Path],
    aggregate: AggregateUsage,
    error_code: str,
) -> Path:
    """Seal aggregate accounting when a run stops before post-run evaluation."""

    safe_error_code = _safe_termination_code(error_code)
    evaluations: list[dict[str, Any]] = []
    case_ids: list[str] = []
    unique_roots: list[Path] = []
    for case_root in case_roots:
        resolved = case_root.resolve()
        if resolved in unique_roots:
            continue
        unique_roots.append(resolved)
        observed_path = resolved / "observed_summary.json"
        if not observed_path.is_file():
            continue
        observed = _read_json(observed_path)
        requested = observed.get("requested")
        case_id = requested.get("case_id") if isinstance(requested, dict) else None
        if not isinstance(case_id, str) or not case_id:
            continue
        case_ids.append(case_id)
        unavailable_path = resolved / "post_run_evaluation.unavailable.json"
        evaluation_path = resolved / "post_run_evaluation.json"
        if evaluation_path.is_file():
            evaluations.append(
                {
                    "case_id": case_id,
                    "status": "completed",
                }
            )
            continue
        if not unavailable_path.is_file():
            _write_evaluator_unavailable(
                resolved,
                case_id=case_id,
                reason=safe_error_code,
            )
        evaluations.append(
            {
                "case_id": case_id,
                "status": "unavailable",
                "reason": safe_error_code,
            }
        )

    summary = {
        "schema_version": "m1-2-aggregate-summary/v1",
        "model": MODEL,
        "case_ids": case_ids,
        "provider_requests": aggregate.provider_requests,
        "model_turns": aggregate.model_turns,
        "preflight_count_requests": aggregate.preflight_count_requests,
        "estimated_input_tokens": aggregate.estimated_input_tokens,
        "inference_input_tokens": aggregate.inference_input_tokens,
        "preflight_counted_input_tokens": aggregate.preflight_counted_input_tokens,
        "reported_input_tokens": aggregate.reported_input_tokens,
        "reported_output_tokens": aggregate.reported_output_tokens,
        "reported_thinking_tokens": aggregate.reported_thinking_tokens
        if aggregate.reported_thinking_tokens is not None
        else UNAVAILABLE,
        "reported_billable_output_tokens": aggregate.reported_billable_output_tokens,
        "provider_elapsed_seconds": aggregate.provider_elapsed_seconds,
        "preflight_elapsed_seconds": aggregate.preflight_elapsed_seconds,
        "preflight_records": copy.deepcopy(aggregate.preflight_records),
        "estimated_worst_case_cost_usd": BUDGET_MANIFEST.worst_case_estimated_cost_usd(),
        "reported_count_cost_usd": aggregate.reported_count_cost_usd
        if aggregate.reported_count_cost_usd is not None
        else UNAVAILABLE,
        "actual_cost_usd": aggregate.reported_cost_usd
        if aggregate.reported_cost_usd is not None
        else UNAVAILABLE,
        "pricing_snapshot": asdict(BUDGET_MANIFEST.pricing),
        "automatic_retry_count": 0,
        "evaluations": evaluations,
        "acceptance_boundary": "not accepted after early termination",
        "termination": {
            "status": "failed",
            "error_code": safe_error_code,
            "case_ids": case_ids,
        },
    }
    summary_path = run_root / "aggregate_summary.json"
    _write_json(summary_path, summary)
    _write_json(
        run_root / "aggregate_summary.seal.json",
        {
            "schema_version": "m1-2-aggregate-summary-seal/v1",
            "sealed": True,
            "aggregate_summary_sha256": hashlib.sha256(
                summary_path.read_bytes()
            ).hexdigest(),
        },
    )
    return summary_path


def execute_real_case(
    *,
    case: SmokeCase,
    sdk_client: Any,
    run_root: Path,
    aggregate: AggregateUsage,
    case_budget: CaseBudget | None = None,
    aggregate_budget: AggregateBudget | None = None,
    secret_detector: Callable[[str], bool] | None = None,
) -> Path:
    """Execute one public case through the existing Team Decision runtime.

    This executor intentionally knows nothing about the frozen evaluation
    labels.  It only copies a synthetic workspace, injects the adapter, and
    records the first Runtime result for a later evaluator.
    """

    verify_frozen_inputs()
    if case not in PUBLIC_CASES:
        raise SmokeBlocked("case is not in the approved real-smoke selection")
    BUDGET_MANIFEST.validate()
    approved_case_budget = BUDGET_MANIFEST.case(case.case_id)
    if case_budget is None:
        case_budget = approved_case_budget
    elif case != MPS_001_RERUN_CASE or case_budget != MPS_001_RERUN_CASE_BUDGET:
        raise SmokeBlocked("only the fixed mps-001 rerun budget may override a case")
    if aggregate_budget is None:
        aggregate_budget = BUDGET_MANIFEST.aggregate
    elif (
        case != MPS_001_RERUN_CASE
        or case_budget != MPS_001_RERUN_CASE_BUDGET
        or aggregate_budget != MPS_001_RERUN_AGGREGATE_BUDGET
    ):
        raise SmokeBlocked("only the fixed mps-001 rerun aggregate budget may override")
    source = _workspace_for_case(case)
    case_root = (run_root / case.case_id).resolve()
    if case_root.exists():
        raise SmokeBlocked("smoke case artifact directory already exists")
    case_root.mkdir(parents=True)
    vault = case_root / "vault"
    source_before = _file_hashes(source)
    shutil.copytree(source, vault)
    copy_before = _file_hashes(vault)
    if source_before != copy_before:
        raise SmokeBlocked("synthetic workspace copy hash mismatch")
    index_path = scan_vault(vault, case_root / "scan").index_path
    guarded_client = BudgetedGeminiClient(
        sdk_client,
        case=case,
        case_budget=case_budget,
        aggregate=aggregate,
        aggregate_budget=aggregate_budget,
        secret_detector=secret_detector,
    )
    adapter = GeminiProviderAdapter(guarded_client, model_id=MODEL)
    runtime = RuntimeEngine(
        vault_root=vault,
        index_path=index_path,
        checkpoint_dir=case_root / "checkpoints",
        trace_dir=case_root / "traces",
        memory_root=case_root / "memory",
        model=adapter,
    )
    request_id = f"m12-{case.case_id}-{uuid4().hex}"
    from tests.smoke.m12_memory_fixture import smoke_memory_binding
    memory_binding = (
        smoke_memory_binding(vault, index_path, case_root / "temporal_decisions.sqlite")
        if case.case_id == "mps-001" else nullcontext()
    )
    with memory_binding:
        status = runtime.start_multi_agent(RunRequest(
            request_id=request_id,
            thread_id=request_id,
            workflow="team_decision",
            query=case.user_question,
            max_steps=case_budget.max_model_turns,
            max_provider_requests=case_budget.max_provider_requests,
            dry_run=True,
        ))
    state = runtime.checkpointer.get_latest(status.thread_id)
    if state is None:
        raise SmokeBlocked("Team Decision runtime did not persist state")
    result = None
    if state.result_ref:
        result_path = case_root / "checkpoints" / state.result_ref
        if result_path.exists():
            result = _read_json(result_path)
    source_after = _file_hashes(source)
    copy_after = _file_hashes(vault)
    if source_before != source_after or copy_before != copy_after:
        raise SmokeBlocked("synthetic workspace changed during smoke")
    # Trace/checkpoint/model artifacts are scanned before any observed summary
    # or seal is written, so a leaked credential cannot enter a summary.
    if _secret_scan_with_detector(case_root, secret_detector):
        raise SmokeBlocked("CREDENTIAL_LEAK_BLOCKED")
    observed = _build_observed_summary(
        case,
        case_budget,
        status,
        state,
        result,
        guarded_client,
        source_before,
        source_after,
        copy_before,
        copy_after,
    )
    _seal_observed_case(case_root, observed)
    if _is_hard_guard_stop(guarded_client.last_guard_state):
        # The first failed result is sealed before the top-level runner stops;
        # no later case may make another Provider request after a hard guard.
        _write_evaluator_unavailable(
            case_root,
            case_id=case.case_id,
            reason=guarded_client.last_guard_state,
        )
        raise SmokeBlocked("M1_2_HARD_STOP_AFTER_SEALED_RESULT")
    return case_root


def _load_gold_case(case_id: str) -> dict[str, Any]:
    """Load frozen evaluation fields for the post-seal evaluator only."""

    try:
        lines = DATASET_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise SmokeBlocked("frozen evaluator dataset is unavailable") from None
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("case_id") == case_id:
            return record
    raise SmokeBlocked("frozen evaluator case is unavailable")


def _actual_team_decision(observed: dict[str, Any]) -> dict[str, Any] | None:
    result = observed.get("result")
    if not isinstance(result, dict):
        return None
    nested = result.get("result")
    if not isinstance(nested, dict):
        return None
    value = nested.get("team_decision")
    return value if isinstance(value, dict) else None


def _claim_refs(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    if isinstance(payload.get("evidence_refs"), list):
        refs.extend(ref for ref in payload["evidence_refs"] if isinstance(ref, str))
    return refs


def _actual_claim_refs(payload: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for value in payload.values():
        if isinstance(value, dict):
            refs.update(ref for ref in value.get("evidence_refs", []) if isinstance(ref, str))
            for nested in value.values():
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, dict):
                            refs.update(
                                ref
                                for ref in item.get("evidence_refs", [])
                                if isinstance(ref, str)
                            )
    return refs


def _items_match(actual: Any, expected: Any) -> bool | None:
    """Compare structured action/rejection projections without judging prose."""

    if not isinstance(actual, list) or not isinstance(expected, list):
        return None
    if not expected:
        return len(actual) == 0
    if len(actual) != len(expected):
        return False
    actual_projection = {
        json.dumps(
            {
                key: item.get(key)
                for key in ("description", "owner", "deadline", "status", "evidence_refs", "alternative", "reason")
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for item in actual
        if isinstance(item, dict)
    }
    expected_projection = {
        json.dumps(
            {
                key: item.get(key)
                for key in ("description", "owner", "deadline", "status", "evidence_refs", "alternative", "reason")
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for item in expected
        if isinstance(item, dict)
    }
    return actual_projection == expected_projection


def _trajectory_metrics(observed: dict[str, Any], frozen: dict[str, Any]) -> dict[str, str]:
    calls = observed.get("trajectory", {}).get("tool_calls", [])
    if not isinstance(calls, list):
        calls = []
    model_records = observed.get("trajectory", {}).get("model_records", [])
    if not isinstance(model_records, list):
        model_records = []
    tool_names = [
        item.get("tool_id")
        for item in calls
        if isinstance(item, dict) and isinstance(item.get("tool_id"), str)
    ]
    event_names: list[str] = []
    for item in model_records:
        action = item.get("normalized_action") if isinstance(item, dict) else None
        if not isinstance(action, dict):
            continue
        if action.get("kind") == "tool_call":
            tool_call = action.get("tool_call")
            event_names.append(
                tool_call.get("tool_id")
                if isinstance(tool_call, dict) and isinstance(tool_call.get("tool_id"), str)
                else "tool_call"
            )
        elif action.get("kind") == "final":
            event_names.append("final")
    constraints = frozen.get("trajectory_constraints")
    if not isinstance(constraints, dict):
        return {"trajectory_contract": UNAVAILABLE}
    max_calls = constraints.get("max_tool_calls")
    call_count_ok = isinstance(max_calls, int) and len(calls) <= max_calls
    final_index = event_names.index("final") if "final" in event_names else None
    final_after_evidence = final_index is not None and bool(tool_names[:final_index])
    unique_calls = {
        json.dumps(
            {"tool_id": item.get("tool_id"), "arguments": item.get("arguments")},
            ensure_ascii=False,
            sort_keys=True,
        )
        for item in calls
        if isinstance(item, dict)
    }
    return {
        "tool_call_limit": "PASS" if call_count_ok else "FAIL",
        "final_after_evidence": "PASS" if final_after_evidence else "FAIL",
        "repeated_calls": "PASS" if len(unique_calls) == len(calls) else "FAIL",
        "unnecessary_tool_calls": "PASS" if len(unique_calls) == len(calls) else "FAIL",
        "premature_final": "PASS" if final_after_evidence else "FAIL",
        "invalid_ordering": (
            "PASS"
            if (
                all(name != "read_verified_note" for name in event_names[: event_names.index("search_notes")])
                if "search_notes" in event_names
                else "read_verified_note" not in event_names
            )
            and final_after_evidence
            else "FAIL"
        ),
        "required_tool_missing": (
            "PASS" if "search_notes" in tool_names else UNAVAILABLE
        ),
    }


def _persist_post_run_evaluation(
    case_root: Path,
    observed: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    _write_json(case_root / "post_run_evaluation.json", evaluation)
    summary = {
        **observed,
        "evaluation": evaluation,
        "sealed": True,
    }
    _write_json(case_root / "case_summary.json", summary)
    _write_json(
        case_root / "case_summary.seal.json",
        {
            "schema_version": "m1-2-case-summary-seal/v1",
            "sealed": True,
            "case_summary_sha256": hashlib.sha256(
                (case_root / "case_summary.json").read_bytes()
            ).hexdigest(),
        },
    )
    return evaluation


def evaluate_sealed_case(case_root: Path) -> dict[str, Any]:
    """Evaluate a sealed first result; this is the only Gold-aware function."""

    seal_path = case_root / "observed_summary.seal.json"
    observed_path = case_root / "observed_summary.json"
    if not seal_path.is_file() or not observed_path.is_file():
        raise SmokeBlocked("EVALUATION_REQUIRES_SEALED_RUN")
    seal = _read_json(seal_path)
    if seal.get("sealed") is not True or seal.get("gold_available") is not False:
        raise SmokeBlocked("EVALUATION_REQUIRES_SEALED_RUN")
    if hashlib.sha256(observed_path.read_bytes()).hexdigest() != seal.get(
        "observed_summary_sha256"
    ):
        raise SmokeBlocked("EVALUATION_REQUIRES_SEALED_RUN")
    observed = _read_json(observed_path)
    requested = observed.get("requested")
    if not isinstance(requested, dict) or not isinstance(requested.get("case_id"), str):
        raise SmokeBlocked("sealed smoke request identity is unavailable")
    actual = _actual_team_decision(observed)
    runtime = observed.get("runtime", {})
    runtime_status = runtime.get("status") if isinstance(runtime, dict) else None
    normalized_error = (
        runtime.get("normalized_error") if isinstance(runtime, dict) else None
    )
    error_code = (
        normalized_error.get("code")
        if isinstance(normalized_error, dict)
        else None
    )
    final_actions = observed.get("trajectory", {}).get("final_actions", [])
    has_final_attempt = isinstance(final_actions, list) and bool(final_actions)
    contract_failure = error_code == "TEAM_DECISION_CONTRACT_ERROR"
    if actual is None and not has_final_attempt and not contract_failure:
        not_evaluated = "NOT_EVALUATED"
        metrics = {
            "outcome": {
                "requested_outcome": not_evaluated,
                "decision_status": not_evaluated,
                "decision_value": not_evaluated,
                "action_correctness": not_evaluated,
                "rejected_alternative_correctness": not_evaluated,
                "unresolved_item_correctness": not_evaluated,
                "uncertainty_correctness": not_evaluated,
                "task_success": not_evaluated,
            },
            "grounding": {
                "material_claim_evidence": not_evaluated,
                "unsupported_claim_count": not_evaluated,
                "invisible_or_fabricated_ref_rejected": not_evaluated,
            },
            "trajectory": {
                "final_after_evidence": not_evaluated,
                "invalid_ordering": not_evaluated,
                "premature_final": not_evaluated,
                "repeated_calls": not_evaluated,
                "required_tool_missing": not_evaluated,
                "tool_call_limit": not_evaluated,
                "unnecessary_tool_calls": not_evaluated,
            },
            "runtime": {
                "status": runtime_status or UNAVAILABLE,
                "provider_requests": runtime.get("provider_requests", UNAVAILABLE),
                "model_turns": runtime.get("model_turns", UNAVAILABLE),
                "tool_calls": runtime.get("tool_calls", UNAVAILABLE),
                "inference_input_tokens": runtime.get(
                    "inference_input_tokens", UNAVAILABLE
                ),
                "preflight_count_requests": runtime.get(
                    "preflight_count_requests", UNAVAILABLE
                ),
                "preflight_counted_input_tokens": runtime.get(
                    "preflight_counted_input_tokens", UNAVAILABLE
                ),
                "preflight_elapsed_seconds": runtime.get(
                    "preflight_elapsed_seconds", UNAVAILABLE
                ),
                "preflight_records": runtime.get("preflight_records", UNAVAILABLE),
                "input_tokens": runtime.get("input_tokens", UNAVAILABLE),
                "output_tokens": runtime.get("output_tokens", UNAVAILABLE),
                "total_tokens": runtime.get("total_tokens", UNAVAILABLE),
                "termination": runtime.get("termination", UNAVAILABLE),
                "normalized_error": runtime.get("normalized_error", UNAVAILABLE),
                "thinking_tokens": runtime.get("thinking_tokens", UNAVAILABLE),
                "automatic_retry_count": runtime.get("automatic_retry_count", 0),
            },
            "cost": {
                "estimated_cost_usd": observed.get("cost", {}).get(
                    "estimated_cost_usd", UNAVAILABLE
                ),
                "actual_cost_usd": observed.get("cost", {}).get(
                    "actual_cost_usd", UNAVAILABLE
                ),
                "pricing_snapshot": "2026-09-12",
            },
        }
        evaluation = {
            "schema_version": "m1-2-post-run-evaluation/v1",
            "case_id": requested["case_id"],
            "evaluated_after_seal": True,
            "status": not_evaluated,
            "reason": "INFRASTRUCTURE/PREFLIGHT BLOCKED",
            "gold_source": "NOT_ACCESSED",
            "metrics": metrics,
        }
        return _persist_post_run_evaluation(case_root, observed, evaluation)

    frozen = _load_gold_case(requested["case_id"])
    actual_decision = actual.get("decision") if isinstance(actual, dict) else None
    expected_decision = frozen.get("expected_decision")
    status_ok = (
        isinstance(actual_decision, dict)
        and isinstance(expected_decision, dict)
        and actual_decision.get("status") == expected_decision.get("status")
    )
    value_ok = (
        isinstance(actual_decision, dict)
        and isinstance(expected_decision, dict)
        and actual_decision.get("value") == expected_decision.get("value")
    )
    visible_refs = set(observed.get("grounding", {}).get("visible_evidence_refs", []))
    actual_refs = _actual_claim_refs(actual) if isinstance(actual, dict) else set()
    grounded = actual is not None and bool(actual_refs) and actual_refs <= visible_refs
    # A Team Decision contract rejection is the expected safety outcome when
    # the model proposes an invisible/fabricated reference.  It is evaluated
    # as a grounding PASS only when no untrusted decision payload escaped.
    fabricated_ref_rejected = (
        runtime_status == "failed"
        and error_code == "TEAM_DECISION_CONTRACT_ERROR"
        and actual is None
    )
    expected_outcome = frozen.get("expected_outcome")
    expected_actions = frozen.get("expected_action_items")
    expected_unresolved = frozen.get("expected_unresolved_items")
    expected_rejected = frozen.get("expected_rejected_alternatives")
    actual_actions = actual.get("actions") if isinstance(actual, dict) else None
    actual_unresolved = actual.get("unresolved_items") if isinstance(actual, dict) else None
    actual_rejected = actual.get("rejected_alternatives") if isinstance(actual, dict) else None
    action_match = _items_match(actual_actions, expected_actions)
    unresolved_match = _items_match(actual_unresolved, expected_unresolved)
    rejected_match = _items_match(actual_rejected, expected_rejected)
    expected_uncertainty = frozen.get("insufficient_evidence_behavior", {})
    uncertainty = actual.get("uncertainty") if isinstance(actual, dict) else None
    uncertainty_required = (
        isinstance(expected_uncertainty, dict)
        and expected_uncertainty.get("must_state_uncertainty") is True
    )
    uncertainty_match = (
        isinstance(uncertainty, dict)
        and isinstance(uncertainty.get("status"), str)
        and (not uncertainty_required or uncertainty.get("status") != "none")
    )
    requested_outcome_match = (
        (expected_outcome == "success" and status_ok)
        or (
            expected_outcome == "partial_with_uncertainty"
            and status_ok
            and uncertainty_match
        )
        or (
            expected_outcome == "insufficient_evidence"
            and isinstance(actual_decision, dict)
            and actual_decision.get("status") == "insufficient_evidence"
        )
    )
    metrics = {
        "outcome": {
            "requested_outcome": "PASS" if requested_outcome_match else "FAIL",
            "decision_status": "PASS" if status_ok else "FAIL",
            "decision_value": "PASS" if value_ok else "FAIL",
            "action_correctness": (
                "PASS" if action_match is True else "FAIL" if action_match is False else UNAVAILABLE
            ),
            "rejected_alternative_correctness": (
                "PASS" if rejected_match is True else "FAIL" if rejected_match is False else UNAVAILABLE
            ),
            "unresolved_item_correctness": (
                "PASS" if unresolved_match is True else "FAIL" if unresolved_match is False else UNAVAILABLE
            ),
            "uncertainty_correctness": "PASS" if uncertainty_match else "FAIL",
            "task_success": "PASS" if requested_outcome_match and grounded else "FAIL",
        },
        "grounding": {
            "material_claim_evidence": "PASS" if grounded else "FAIL",
            "unsupported_claim_count": 0 if grounded else (len(actual_refs - visible_refs) if actual else UNAVAILABLE),
            "invisible_or_fabricated_ref_rejected": (
                "PASS" if (runtime_status == "completed" and grounded) or fabricated_ref_rejected else "FAIL"
            ),
        },
        "trajectory": _trajectory_metrics(observed, frozen),
        "runtime": {
            "status": runtime_status or UNAVAILABLE,
            "provider_requests": observed.get("runtime", {}).get("provider_requests", UNAVAILABLE),
            "model_turns": observed.get("runtime", {}).get("model_turns", UNAVAILABLE),
            "tool_calls": observed.get("runtime", {}).get("tool_calls", UNAVAILABLE),
            "inference_input_tokens": observed.get("runtime", {}).get("inference_input_tokens", UNAVAILABLE),
            "preflight_count_requests": observed.get("runtime", {}).get("preflight_count_requests", UNAVAILABLE),
            "preflight_counted_input_tokens": observed.get("runtime", {}).get("preflight_counted_input_tokens", UNAVAILABLE),
            "preflight_elapsed_seconds": observed.get("runtime", {}).get("preflight_elapsed_seconds", UNAVAILABLE),
            "preflight_records": observed.get("runtime", {}).get("preflight_records", UNAVAILABLE),
            "input_tokens": observed.get("runtime", {}).get("input_tokens", UNAVAILABLE),
            "output_tokens": observed.get("runtime", {}).get("output_tokens", UNAVAILABLE),
            "total_tokens": observed.get("runtime", {}).get("total_tokens", UNAVAILABLE),
            "termination": observed.get("runtime", {}).get("termination", UNAVAILABLE),
            "normalized_error": observed.get("runtime", {}).get("normalized_error", UNAVAILABLE),
            "thinking_tokens": observed.get("runtime", {}).get("thinking_tokens", UNAVAILABLE),
            "automatic_retry_count": observed.get("runtime", {}).get("automatic_retry_count", 0),
        },
        "cost": {
            "estimated_cost_usd": observed.get("cost", {}).get("estimated_cost_usd", UNAVAILABLE),
            "actual_cost_usd": observed.get("cost", {}).get("actual_cost_usd", UNAVAILABLE),
            "pricing_snapshot": "2026-09-12",
        },
    }
    evaluation = {
        "schema_version": "m1-2-post-run-evaluation/v1",
        "case_id": requested["case_id"],
        "evaluated_after_seal": True,
        "status": "EVALUATED",
        "reason": None,
        "gold_source": "dataset.jsonl",
        "metrics": metrics,
    }
    return _persist_post_run_evaluation(case_root, observed, evaluation)


def run_real_provider_smoke(settings: RealSettings | None = None) -> Path:
    """Run the three selected cases once and return the private artifact root."""

    # Always re-enter the opt-in and exact authorization guard.  A caller
    # supplied RealSettings object can never bypass that boundary.
    authorized_settings = load_real_settings()
    settings = authorized_settings
    verify_frozen_inputs()
    BUDGET_MANIFEST.validate()
    run_root = (ARTIFACT_ROOT / f"run-{uuid4().hex}").resolve()
    run_root.mkdir(parents=True)
    _write_json(
        run_root / "run_manifest.json",
        {
            "schema_version": "m1-2-run-manifest/v1",
            "model": MODEL,
            "sdk_version": SDK_VERSION,
            "authorization_guard": "PASS",
            "freeze_dataset_sha256": FROZEN_DATASET_SHA256,
            "freeze_golden8_case_ids": list(FROZEN_GOLDEN8_CASE_IDS),
            "freeze_workspace_fingerprints": FROZEN_WORKSPACE_FINGERPRINTS,
            "case_ids": [case.case_id for case in PUBLIC_CASES],
            "budget_manifest": asdict(BUDGET_MANIFEST),
            "gold_available_to_executor": False,
            "automatic_retry_count": 0,
        },
    )
    aggregate = AggregateUsage()
    secret_detector = lambda text: settings.api_key in text
    sdk_client = build_official_client(
        settings.api_key,
        _authorization_sentinel=_REAL_AUTHORIZATION_SENTINEL,
    )
    case_roots: list[Path] = []
    evaluations: list[dict[str, Any]] = []
    try:
        for case in PUBLIC_CASES:
            case_roots.append(
                execute_real_case(
                    case=case,
                    sdk_client=sdk_client,
                    run_root=run_root,
                    aggregate=aggregate,
                    secret_detector=secret_detector,
                )
            )
        # Gold is intentionally loaded only after every Provider execution has
        # stopped and every observed case has its independent seal.
        evaluations = [evaluate_sealed_case(case_root) for case_root in case_roots]
        if aggregate.provider_requests > BUDGET_MANIFEST.aggregate.max_provider_requests:
            raise SmokeBlocked("AGGREGATE_PROVIDER_REQUEST_BUDGET_EXCEEDED")
        if aggregate.model_turns > BUDGET_MANIFEST.aggregate.max_model_turns:
            raise SmokeBlocked("AGGREGATE_MODEL_TURN_BUDGET_EXCEEDED")
        if (
            aggregate.preflight_count_requests
            > BUDGET_MANIFEST.aggregate.preflight_requests_limit
        ):
            raise SmokeBlocked("AGGREGATE_PREFLIGHT_REQUEST_BUDGET_EXCEEDED")
        if aggregate.estimated_input_tokens > BUDGET_MANIFEST.aggregate.max_input_tokens:
            raise SmokeBlocked("AGGREGATE_INPUT_TOKEN_BUDGET_EXCEEDED")
        if (
            aggregate.preflight_counted_input_tokens
            > BUDGET_MANIFEST.aggregate.preflight_input_limit
        ):
            raise SmokeBlocked("AGGREGATE_PREFLIGHT_INPUT_TOKEN_BUDGET_EXCEEDED")
        if aggregate.reported_output_tokens > BUDGET_MANIFEST.aggregate.max_output_tokens:
            raise SmokeBlocked("AGGREGATE_OUTPUT_TOKEN_BUDGET_EXCEEDED")
        if aggregate.provider_elapsed_seconds > BUDGET_MANIFEST.aggregate.max_elapsed_provider_seconds:
            raise SmokeBlocked("AGGREGATE_PROVIDER_TIME_EXCEEDED")
        if (
            aggregate.preflight_elapsed_seconds
            > BUDGET_MANIFEST.aggregate.preflight_elapsed_limit
        ):
            raise SmokeBlocked("AGGREGATE_PREFLIGHT_TIME_EXCEEDED")
        aggregate_summary = {
            "schema_version": "m1-2-aggregate-summary/v1",
            "model": MODEL,
            "case_ids": [case.case_id for case in PUBLIC_CASES],
            "provider_requests": aggregate.provider_requests,
            "model_turns": aggregate.model_turns,
            "preflight_count_requests": aggregate.preflight_count_requests,
            "estimated_input_tokens": aggregate.estimated_input_tokens,
            "inference_input_tokens": aggregate.inference_input_tokens,
            "preflight_counted_input_tokens": aggregate.preflight_counted_input_tokens,
            "reported_input_tokens": aggregate.reported_input_tokens,
            "reported_output_tokens": aggregate.reported_output_tokens,
            "reported_thinking_tokens": aggregate.reported_thinking_tokens
            if aggregate.reported_thinking_tokens is not None
            else UNAVAILABLE,
            "reported_billable_output_tokens": aggregate.reported_billable_output_tokens,
            "provider_elapsed_seconds": aggregate.provider_elapsed_seconds,
            "preflight_elapsed_seconds": aggregate.preflight_elapsed_seconds,
            "preflight_records": copy.deepcopy(aggregate.preflight_records),
            "estimated_worst_case_cost_usd": BUDGET_MANIFEST.worst_case_estimated_cost_usd(),
            "reported_count_cost_usd": (
                aggregate.reported_count_cost_usd
                if aggregate.reported_count_cost_usd is not None
                else UNAVAILABLE
            ),
            "actual_cost_usd": (
                aggregate.reported_cost_usd
                if aggregate.reported_cost_usd is not None
                else UNAVAILABLE
            ),
            "pricing_snapshot": asdict(BUDGET_MANIFEST.pricing),
            "automatic_retry_count": 0,
            "evaluations": evaluations,
            "acceptance_boundary": "Real-provider smoke accepted for selected cases",
        }
        if _secret_scan(run_root, settings.api_key):
            raise SmokeBlocked("CREDENTIAL_LEAK_BLOCKED")
        aggregate_summary["secret_scan"] = "PASS"
        _write_json(run_root / "aggregate_summary.json", aggregate_summary)
        if _secret_scan(run_root, settings.api_key):
            raise SmokeBlocked("CREDENTIAL_LEAK_BLOCKED")
        return run_root
    except SmokeBlocked as error:
        # Preserve sealed first-result evidence and aggregate accounting even
        # when a hard guard stops before all selected cases are evaluated.
        discovered_case_roots = list(case_roots)
        for candidate in sorted(run_root.iterdir()):
            if not candidate.is_dir():
                continue
            if not (candidate / "observed_summary.seal.json").is_file():
                continue
            if candidate not in discovered_case_roots:
                discovered_case_roots.append(candidate)
        _write_early_termination_artifacts(
            run_root,
            case_roots=discovered_case_roots,
            aggregate=aggregate,
            error_code=str(error),
        )
        raise
    finally:
        close = getattr(sdk_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def run_mps_001_rerun(
    settings: RealSettings | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Run only mps-001 under its bounded multi-turn Agent-loop budget."""

    source = os.environ if environ is None else environ
    if source.get(MPS_001_RERUN_ENV) != "1":
        raise SmokeBlocked("explicit mps-001 rerun mode is required")
    # Re-enter the normal opt-in/authorization guard; a supplied settings
    # object must never bypass credential safety.
    settings = load_real_settings(source)
    verify_frozen_inputs()
    BUDGET_MANIFEST.validate()
    run_root = (ARTIFACT_ROOT / f"mps-001-rerun-{uuid4().hex}").resolve()
    run_root.mkdir(parents=True)
    _write_json(
        run_root / "run_manifest.json",
        {
            "schema_version": "m1-2-mps-001-rerun/v1",
            "model": MODEL,
            "case_ids": [MPS_001_RERUN_CASE.case_id],
            "rerun_mode": "single-case-bounded-agent-loop",
            "case_budget": asdict(MPS_001_RERUN_CASE_BUDGET),
            "aggregate_budget": asdict(MPS_001_RERUN_AGGREGATE_BUDGET),
            "gold_available_to_executor": False,
            "automatic_retry_count": 0,
            "max_transport_retries_per_request": MAX_TRANSPORT_RETRIES_PER_REQUEST,
        },
    )
    aggregate = AggregateUsage()
    secret_detector = lambda text: settings.api_key in text
    sdk_client = build_official_client(
        settings.api_key,
        _authorization_sentinel=_REAL_AUTHORIZATION_SENTINEL,
    )
    try:
        execute_real_case(
            case=MPS_001_RERUN_CASE,
            sdk_client=sdk_client,
            run_root=run_root,
            aggregate=aggregate,
            case_budget=MPS_001_RERUN_CASE_BUDGET,
            aggregate_budget=MPS_001_RERUN_AGGREGATE_BUDGET,
            secret_detector=secret_detector,
        )
        # No later case is allowed after this bounded mps-001 Agent loop.
        return run_root
    finally:
        close = getattr(sdk_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass



def _harness():
    return importlib.import_module(MODULE_NAME)


def test_offline_default_skip_never_reads_secret_or_late_environment():
    harness = _harness()

    class OptInOnly(dict):
        def get(self, name, default=None):
            if name != harness.OPT_IN_ENV:
                raise AssertionError(f"environment read before opt-in: {name}")
            return None

    with pytest.raises(pytest.skip.Exception):
        harness.load_real_settings(OptInOnly())


def test_offline_model_configuration_is_fixed_to_approved_model():
    harness = _harness()

    with pytest.raises(harness.SmokeBlocked, match="fixed model"):
        harness.load_real_settings(
            {
                harness.OPT_IN_ENV: "1",
                harness.AUTHORIZATION_ENV: harness.REQUIRED_AUTHORIZATION,
                harness.MODEL_ENV: "gemini-not-approved",
            }
        )


def test_offline_opt_in_without_exact_authorization_never_reads_secret():
    harness = _harness()

    class WrongAuthorization(dict):
        def get(self, name, default=None):
            if name == "GEMINI_API_KEY":
                raise AssertionError("credential read before exact authorization")
            return super().get(name, default)

    with pytest.raises(harness.SmokeBlocked, match="authorization"):
        harness.load_real_settings(
            WrongAuthorization(
                {
                    harness.OPT_IN_ENV: "1",
                    harness.AUTHORIZATION_ENV: "not-the-approved-phrase",
                }
            )
        )


def test_offline_public_case_registry_contains_only_execution_fields():
    harness = _harness()

    assert tuple(case.case_id for case in harness.PUBLIC_CASES) == (
        "mps-001",
        "aer-002",
        "iti-005",
    )
    for case in harness.PUBLIC_CASES:
        assert set(case.__dict__) == {
            "case_id",
            "workspace_id",
            "user_question",
            "budget_id",
        }
        assert not any("expected" in key.lower() for key in case.__dict__)


def test_offline_pricing_snapshot_matches_the_approved_ceiling():
    harness = _harness()

    manifest = harness.BUDGET_MANIFEST
    assert manifest.model_id == "gemini-3.8-flash"
    assert manifest.pricing.snapshot_date == "2026-09-12"
    assert manifest.pricing.input_usd_per_million == 0.75
    assert manifest.pricing.output_usd_per_million == 3.75
    assert manifest.worst_case_estimated_cost_usd() == pytest.approx(0.05796)
    assert manifest.worst_case_estimated_cost_usd() <= manifest.aggregate.max_cost_usd


def test_offline_pre_send_input_guard_blocks_without_delegate_call():
    harness = _harness()
    delegate = harness.RecordingDelegate(count_response={"total_tokens": 5_000})
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(
        harness.SmokeBlocked,
        match="PREFLIGHT_REQUEST_INPUT_TOKEN_BUDGET_EXCEEDED",
    ):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "x" * 5000}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    assert delegate.calls == []
    assert len(delegate.count_calls) == 1
    assert guard.provider_calls == 0


def test_offline_guard_forces_512_output_cap_and_never_retries():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        response={
            "text": "synthetic final",
            "usage_metadata": {
                "prompt_token_count": 7,
                "candidates_token_count": 5,
                "total_token_count": 12,
                "thoughts_token_count": 2,
            },
        }
    )
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    guard.generate_content(
        model=harness.MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={
            "max_output_tokens": 9999,
            "automatic_function_calling": {"disable": True},
        },
    )

    assert len(delegate.calls) == 1
    assert delegate.calls[0]["config"]["max_output_tokens"] == 512
    assert guard.automatic_retry_count == 0
    assert guard.reported_thinking_tokens == 2
    assert guard.reported_billable_output_tokens == 7


def test_offline_case_and_aggregate_elapsed_guards_are_distinct_and_terminal():
    harness = _harness()

    class Clock:
        def __init__(self, elapsed):
            self.values = iter((0.0, 0.0, 0.0, elapsed))

        def __call__(self):
            return next(self.values)

    case_delegate = harness.RecordingDelegate()
    case_budget = harness.CaseBudget("mps-001", 3, 3, 4_000, 1_536, 1, 30, 512, 0)
    case_guard = harness.BudgetedGeminiClient(
        case_delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=case_budget,
        aggregate=harness.AggregateUsage(),
        clock=Clock(2.0),
    )
    with pytest.raises(harness.SmokeBlocked, match="CASE_PROVIDER_TIME_EXCEEDED"):
        case_guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(case_delegate.calls) == 1

    aggregate_delegate = harness.RecordingDelegate()
    aggregate = harness.AggregateUsage()
    aggregate_guard = harness.BudgetedGeminiClient(
        aggregate_delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.CaseBudget("mps-001", 3, 3, 4_000, 1_536, 500, 500, 512, 0),
        aggregate=aggregate,
        clock=Clock(391.0),
    )
    with pytest.raises(harness.SmokeBlocked, match="AGGREGATE_PROVIDER_TIME_EXCEEDED"):
        aggregate_guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(aggregate_delegate.calls) == 1


def test_offline_thinking_tokens_count_toward_case_output_budget():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        response={
            "usage_metadata": {
                "prompt_token_count": 7,
                "candidates_token_count": 5,
                "thoughts_token_count": 2,
            }
        }
    )
    case_budget = harness.CaseBudget("mps-001", 3, 3, 4_000, 6, 90, 30, 512, 0)
    aggregate = harness.AggregateUsage()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=case_budget,
        aggregate=aggregate,
    )
    with pytest.raises(harness.SmokeBlocked, match="REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert guard.reported_thinking_tokens == 2
    assert guard.reported_billable_output_tokens == 7
    assert aggregate.reported_thinking_tokens == 2
    assert aggregate.reported_billable_output_tokens == 7
    assert len(delegate.calls) == 1


def test_offline_projected_cost_guard_blocks_before_delegate_call():
    harness = _harness()
    delegate = harness.RecordingDelegate()
    tiny_aggregate = harness.AggregateBudget(
        max_provider_requests=13,
        max_model_turns=13,
        max_input_tokens=22_000,
        max_output_tokens=6_656,
        max_elapsed_provider_seconds=390,
        max_cost_usd=0.000001,
    )
    manifest = harness.BudgetManifest(
        model_id=harness.MODEL,
        pricing=harness.BUDGET_MANIFEST.pricing,
        cases=harness.BUDGET_MANIFEST.cases,
        aggregate=tiny_aggregate,
    )
    with patch.object(harness, "BUDGET_MANIFEST", manifest):
        guard = harness.BudgetedGeminiClient(
            delegate,
            case=harness.PUBLIC_CASES[0],
            case_budget=manifest.case("mps-001"),
            aggregate=harness.AggregateUsage(),
        )
        with pytest.raises(harness.SmokeBlocked, match="PROJECTED_COST_BUDGET_EXCEEDED"):
            guard.generate_content(
                model=harness.MODEL,
                contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
                config={"automatic_function_calling": {"disable": True}},
            )
    assert delegate.calls == []


def test_offline_exhausted_smoke_cost_blocks_count_and_generation():
    harness = _harness()
    delegate = harness.RecordingDelegate()
    aggregate = harness.AggregateUsage()
    aggregate.reported_cost_usd = harness.MAX_TOTAL_SMOKE_COST
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=harness.MPS_001_RERUN_CASE_BUDGET,
        aggregate=aggregate,
        aggregate_budget=harness.MPS_001_RERUN_AGGREGATE_BUDGET,
    )
    with patch.object(delegate, "count_tokens") as count:
        with pytest.raises(harness.SmokeBlocked, match="PROJECTED_COST_BUDGET_EXCEEDED"):
            guard.generate_content(
                model=harness.MODEL,
                contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
                config={"automatic_function_calling": {"disable": True}},
            )
        count.assert_not_called()
    assert delegate.calls == []


def test_offline_client_constructor_requires_developer_api_and_disables_vertex():
    harness = _harness()
    seen = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            seen["http_options"] = kwargs

    class FakeTypes:
        class HttpRetryOptions:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        HttpOptions = FakeOptions

    class FakeModels:
        def __init__(self):
            self.calls = []
            self.count_calls = []

        def count_tokens(self, **kwargs):
            self.count_calls.append(kwargs)
            return {"total_tokens": 7}

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "text": "synthetic",
                "usage_metadata": {"prompt_token_count": 7, "candidates_token_count": 1},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.models = FakeModels()
            self.vertexai = kwargs["vertexai"]
            self.api_endpoint = harness.DEVELOPER_API_BASE_URL

            class ApiClient:
                class HttpOptions:
                    base_url = harness.DEVELOPER_API_BASE_URL

                _http_options = HttpOptions()

            self._api_client = ApiClient()

    class FakeGenai:
        class Client:
            def __new__(cls, **kwargs):
                return FakeClient(**kwargs)

    client = harness.build_official_client(
        "synthetic-key",
        genai_module=FakeGenai,
        types_module=FakeTypes,
        ambient_fallback_patch=None,
        count_tokens_transport=lambda **kwargs: {"total_tokens": 7},
    )

    assert client is not None
    assert seen["client"]["api_key"] == "synthetic-key"
    assert seen["client"]["vertexai"] is False
    assert seen["http_options"]["timeout"] == 30000
    assert seen["http_options"]["retry_options"].attempts == 1
    client.generate_content(
        model=harness.MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )
    assert len(client.models.calls) == 1


def test_offline_client_without_count_tokens_surface_blocks_before_use():
    harness = _harness()
    constructed = []

    class FakeGenai:
        class Client:
            def __new__(cls, **kwargs):
                constructed.append(kwargs)
                class FakeClient:
                    vertexai = False
                    api_endpoint = harness.DEVELOPER_API_BASE_URL

                    class _api_client:
                        class _http_options:
                            base_url = harness.DEVELOPER_API_BASE_URL

                    class models:
                        def generate_content(self, **kwargs):
                            return {"text": "must not be reached"}

                return FakeClient()

    class FakeTypes:
        class HttpRetryOptions:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class HttpOptions:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

    with pytest.raises(harness.SmokeBlocked, match="count_tokens"):
        harness.build_official_client(
            "synthetic-key",
            genai_module=FakeGenai,
            types_module=FakeTypes,
            ambient_fallback_patch=None,
        )
    assert len(constructed) == 1


def test_offline_delegate_failure_stops_without_transport_retry():
    harness = _harness()
    delegate = harness.RecordingDelegate(exception=TimeoutError("synthetic timeout"))
    delays = []
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
        sleep=delays.append,
    )
    with pytest.raises(TimeoutError):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(delegate.calls) == 1
    assert guard.provider_calls == 1
    assert guard.inference_transport_attempts == 1
    assert guard.automatic_retry_count == 0
    assert delays == []


def test_offline_provider_side_tool_execution_is_rejected_before_send():
    harness = _harness()
    delegate = harness.RecordingDelegate()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )
    with pytest.raises(harness.SmokeBlocked, match="PROVIDER_SIDE_TOOL_EXECUTION"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={},
        )
    assert delegate.calls == []


def test_offline_vertex_configuration_is_rejected_before_delegate_call():
    harness = _harness()
    delegate = harness.RecordingDelegate()
    delegate.vertexai = True
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )
    with pytest.raises(harness.SmokeBlocked, match="Vertex"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert delegate.calls == []
    assert guard.provider_calls == 0


def test_offline_vertex_billing_path_is_terminal():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        response={"text": "synthetic", "endpoint": "aiplatform.googleapis.com"}
    )
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )
    with pytest.raises(harness.SmokeBlocked, match="VERTEX_BILLING_PATH_OBSERVED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(delegate.calls) == 1


def test_offline_executor_source_has_no_evaluator_or_gold_dependency():
    import inspect

    harness = _harness()
    source = inspect.getsource(harness.execute_real_case).casefold()
    assert "expected_" not in source
    assert "gold" not in source
    assert "evaluate_sealed_case" not in source


def test_offline_runner_reenters_authorization_even_with_supplied_settings():
    harness = _harness()
    with pytest.raises(pytest.skip.Exception):
        harness.run_real_provider_smoke(
            harness.RealSettings(harness.MODEL, "synthetic-key")
        )


def test_offline_freeze_and_workspace_fingerprints_are_required_before_run():
    harness = _harness()
    harness.verify_frozen_inputs()
    with patch.object(harness, "FROZEN_DATASET_SHA256", "0" * 64):
        with pytest.raises(harness.SmokeBlocked, match="fingerprint"):
            harness.verify_frozen_inputs()


def test_offline_evaluator_requires_a_sealed_observed_result():
    harness = _harness()
    case_root = harness.REPO / ".tmp" / f"m12-unsealed-{harness.uuid4().hex}"
    case_root.mkdir(parents=True)
    with pytest.raises(harness.SmokeBlocked, match="EVALUATION_REQUIRES_SEALED_RUN"):
        harness.evaluate_sealed_case(case_root)
    case_root.rmdir()


def test_offline_preflight_blocked_is_not_semantically_evaluated():
    harness = _harness()
    case_root = harness.REPO / ".tmp" / f"m12-not-evaluated-{harness.uuid4().hex}"
    case_root.mkdir(parents=True)
    try:
        observed = {
            "requested": {"case_id": "mps-001"},
            "trajectory": {
                "model_records": [],
                "tool_calls": [],
                "final_actions": [],
            },
            "runtime": {
                "status": "failed",
                "provider_requests": 0,
                "model_turns": 1,
                "tool_calls": 0,
                "normalized_error": {"code": "MODEL_TRANSIENT_FAILURE"},
            },
            "grounding": {"visible_evidence_refs": []},
            "cost": {},
            "result": None,
        }
        observed_path = case_root / "observed_summary.json"
        harness._write_json(observed_path, observed)
        harness._write_json(
            case_root / "observed_summary.seal.json",
            {
                "sealed": True,
                "gold_available": False,
                "observed_summary_sha256": hashlib.sha256(
                    observed_path.read_bytes()
                ).hexdigest(),
            },
        )

        with patch.object(
            harness,
            "_load_gold_case",
            side_effect=AssertionError("Gold must not be loaded for an unevaluated run"),
        ):
            evaluation = harness.evaluate_sealed_case(case_root)

        assert evaluation["status"] == "NOT_EVALUATED"
        assert evaluation["reason"] == "INFRASTRUCTURE/PREFLIGHT BLOCKED"
        assert evaluation["gold_source"] == "NOT_ACCESSED"
        assert set(evaluation["metrics"]["outcome"].values()) == {"NOT_EVALUATED"}
        assert set(evaluation["metrics"]["grounding"].values()) == {
            "NOT_EVALUATED"
        }
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_offline_count_tokens_failure_blocks_matching_inference_and_retry():
    """A countTokens failure is terminal and cannot fall back to inference."""

    harness = _harness()

    class CountFailureDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = []
            self.inference_calls = []

        def count_tokens(self, **kwargs):
            self.count_calls.append(copy.deepcopy(kwargs))
            raise TimeoutError("synthetic count timeout")

        def generate_content(self, **kwargs):
            self.inference_calls.append(copy.deepcopy(kwargs))
            return {"text": "must not be reached"}

    delegate = CountFailureDelegate()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    assert len(delegate.count_calls) == 1
    assert delegate.inference_calls == []
    assert guard.preflight_count_requests == 1
    assert guard.provider_calls == 0


def test_offline_count_tokens_and_inference_receive_the_same_complete_request():
    """System/tool/generation inputs must be present in both request records."""

    harness = _harness()

    class CompleteRequestDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = []
            self.inference_calls = []

        def count_tokens(self, **kwargs):
            self.count_calls.append(copy.deepcopy(kwargs))
            return {"totalTokens": 11}

        def generate_content(self, **kwargs):
            self.inference_calls.append(copy.deepcopy(kwargs))
            self.inference_calls[-1]["request"] = {
                "model": kwargs["model"],
                "contents": copy.deepcopy(kwargs["contents"]),
                "config": copy.deepcopy(kwargs["config"]),
            }
            return {
                "text": "synthetic final",
                "usage_metadata": {
                    "prompt_token_count": 11,
                    "candidates_token_count": 3,
                },
            }

    delegate = CompleteRequestDelegate()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )
    contents = [{"role": "user", "parts": [{"text": "synthetic"}]}]
    config = {
        "system_instruction": {"parts": [{"text": "be concise"}]},
        "tools": [{"function_declarations": [{"name": "search_notes"}]}],
        "safety_settings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT"}],
        "temperature": 0.2,
        "automatic_function_calling": {"disable": True},
    }

    guard.generate_content(model=harness.MODEL, contents=contents, config=config)

    assert len(delegate.count_calls) == 1
    assert len(delegate.inference_calls) == 1
    count_request = delegate.count_calls[0]["request"]
    inference_request = delegate.inference_calls[0]["request"]
    assert count_request == inference_request
    assert count_request["config"]["max_output_tokens"] == 512
    assert "system_instruction" in count_request["config"]
    assert "tools" in count_request["config"]
    assert "safety_settings" in count_request["config"]
    assert guard.preflight_records[0]["usage_delta"] == 0


def test_offline_count_and_inference_ledgers_are_separate_and_record_delta():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_response={"total_tokens": 13},
        response={
            "usage_metadata": {
                "prompt_token_count": 12,
                "candidates_token_count": 4,
                "thoughts_token_count": 2,
            }
        },
    )
    aggregate = harness.AggregateUsage()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=aggregate,
    )

    guard.generate_content(
        model=harness.MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )

    assert guard.preflight_count_requests == 1
    assert guard.provider_calls == 1
    assert guard.preflight_counted_input_tokens == 13
    assert aggregate.preflight_counted_input_tokens == 13
    assert guard.estimated_input_tokens == 13
    assert guard.reported_input_tokens == 12
    assert guard.preflight_records[0]["usage_delta"] == -1
    assert guard.reported_billable_output_tokens == 6
    assert guard.preflight_elapsed_seconds >= 0


def test_offline_count_specific_cost_is_added_to_actual_cost_telemetry():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_response={"total_tokens": 7, "cost_usd": 0.01},
        response={
            "usage_metadata": {
                "prompt_token_count": 7,
                "candidates_token_count": 1,
                "cost_usd": 0.02,
            }
        },
    )
    aggregate = harness.AggregateUsage()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=aggregate,
    )
    guard.generate_content(
        model=harness.MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config={"automatic_function_calling": {"disable": True}},
    )
    assert guard.reported_count_cost_usd == pytest.approx(0.01)
    assert guard.reported_cost_usd == pytest.approx(0.02)
    assert aggregate.reported_count_cost_usd == pytest.approx(0.01)
    assert aggregate.reported_cost_usd == pytest.approx(0.03)


def test_offline_malformed_count_tokens_blocks_before_inference():
    harness = _harness()
    delegate = harness.RecordingDelegate(count_response={"totalTokens": "not-an-int"})
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert delegate.calls == []
    assert guard.preflight_count_requests == 1


def test_offline_count_tokens_undercount_terminates_future_requests():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_response={"total_tokens": 7},
        response={"usage_metadata": {"prompt_token_count": 8, "candidates_token_count": 1}},
    )
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="PREFLIGHT.*MISMATCH"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(delegate.calls) == 1
    with pytest.raises(harness.SmokeBlocked, match="terminal|PREFLIGHT"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "second"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(delegate.count_calls) == 1
    assert len(delegate.calls) == 1


def test_offline_continuation_preflight_reserves_previous_thinking_tokens():
    harness = _harness()

    class SequencedContinuationDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = []
            self.calls = []
            self.counts = [7, 11]
            self.responses = [
                {
                    "usage_metadata": {
                        "prompt_token_count": 7,
                        "candidates_token_count": 2,
                        "thoughts_token_count": 3,
                    }
                },
                {
                    "usage_metadata": {
                        "prompt_token_count": 14,
                        "candidates_token_count": 1,
                        "thoughts_token_count": 2,
                    }
                },
            ]

        def count_tokens(self, **kwargs):
            self.count_calls.append(copy.deepcopy(kwargs))
            return {"total_tokens": self.counts[len(self.count_calls) - 1]}

        def generate_content(self, **kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            return self.responses[len(self.calls) - 1]

    delegate = SequencedContinuationDelegate()
    aggregate = harness.AggregateUsage()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=aggregate,
    )
    config = {"automatic_function_calling": {"disable": True}}

    guard.generate_content(
        model=harness.MODEL,
        contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
        config=config,
    )
    guard.generate_content(
        model=harness.MODEL,
        contents=[
            {"role": "user", "parts": [{"text": "synthetic"}]},
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "synthetic-call",
                            "name": "search_notes",
                            "args": {"query": "synthetic"},
                        },
                        "thought_signature": "c2ln",
                    }
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "synthetic-call",
                            "name": "search_notes",
                            "response": {"status": "ok"},
                        }
                    }
                ],
            },
        ],
        config=config,
    )

    second = guard.preflight_records[1]
    assert second["raw_counted_input_tokens"] == 11
    assert second["continuation_thinking_token_reserve"] == 3
    assert second["counted_input_tokens"] == 14
    assert second["usage_delta"] == 0
    assert guard.preflight_counted_input_tokens == 21
    assert aggregate.preflight_counted_input_tokens == 21
    assert len(delegate.calls) == 2


def test_offline_gold_fields_are_rejected_before_count_tokens():
    harness = _harness()
    delegate = harness.RecordingDelegate(count_response={"total_tokens": 7})
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="(?i)gold|expected|evaluator"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[
                {
                    "role": "user",
                    "parts": [{"text": "synthetic"}],
                    "expected_answer": "must not cross boundary",
                }
            ],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert delegate.count_calls == []
    assert delegate.calls == []


def test_offline_count_tokens_surface_is_required_without_local_fallback():
    harness = _harness()

    class GenerateOnlyDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def generate_content(self, **kwargs):
            return {"text": "must not be reached"}

    with pytest.raises(harness.SmokeBlocked, match="count_tokens"):
        harness.BudgetedGeminiClient(
            GenerateOnlyDelegate(),
            case=harness.PUBLIC_CASES[0],
            case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
            aggregate=harness.AggregateUsage(),
        )


def test_offline_vertex_is_rejected_before_count_tokens_or_inference():
    harness = _harness()
    delegate = harness.RecordingDelegate(count_response={"total_tokens": 7})
    delegate.vertexai = True
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="Vertex"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert delegate.count_calls == []
    assert delegate.calls == []


def test_offline_approved_cost_reserves_both_input_ledgers():
    harness = _harness()
    assert harness.BUDGET_MANIFEST.worst_case_estimated_cost_usd() == pytest.approx(
        0.05796
    )


def test_offline_raw_count_endpoint_uses_complete_request_and_developer_header():
    harness = _harness()
    seen = {}

    class FakeResponse:
        def read(self):
            return b'{"totalTokens": 19}'

        def close(self):
            seen["closed"] = True

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return FakeResponse()

    response = harness._raw_developer_count_tokens(
        "synthetic-key",
        model=harness.MODEL,
        request={
            "model": harness.MODEL,
            "contents": [
                {"role": "user", "parts": [{"text": "synthetic"}]},
                {
                    "role": "model",
                    "parts": [
                        {
                            "function_call": {
                                "id": "synthetic-call",
                                "name": "search_notes",
                                "args": {"query": "synthetic"},
                            },
                            "thought_signature": "c2ln",
                        }
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": "synthetic-call",
                                "name": "search_notes",
                                "response": {"status": "ok"},
                            }
                        }
                    ],
                },
            ],
            "config": {
                "system_instruction": {"parts": [{"text": "be concise"}]},
                "tools": [{"function_declarations": [{"name": "search_notes"}]}],
                "safety_settings": [{"category": "HARM_CATEGORY_UNSPECIFIED"}],
                "max_output_tokens": 512,
                "temperature": 0.2,
                "automatic_function_calling": {"disable": True},
            },
        },
        urlopen=fake_urlopen,
    )

    assert response["totalTokens"] == 19
    assert response[harness.TRANSPORT_ATTEMPTS_FIELD] == 1
    assert seen["url"].endswith("/v1beta/models/gemini-3.8-flash:countTokens")
    assert seen["headers"]["X-goog-api-key"] == "synthetic-key"
    request = seen["body"]["generateContentRequest"]
    assert harness.MODEL == "gemini-3.8-flash"
    assert request["model"] == "models/gemini-3.8-flash"
    assert request["model"].removeprefix("models/") == harness.MODEL
    assert request["contents"][0]["parts"][0]["text"] == "synthetic"
    assert request["contents"][1]["parts"][0] == {
        "functionCall": {
            "id": "synthetic-call",
            "name": "search_notes",
            "args": {"query": "synthetic"},
        },
        "thoughtSignature": "c2ln",
    }
    assert request["contents"][2]["parts"][0]["functionResponse"] == {
        "id": "synthetic-call",
        "name": "search_notes",
        "response": {"status": "ok"},
    }
    assert request["contents"][2]["role"] == "user"
    assert request["systemInstruction"]["parts"][0]["text"] == "be concise"
    assert request["tools"][0]["functionDeclarations"][0]["name"] == "search_notes"
    assert request["safetySettings"][0]["category"] == "HARM_CATEGORY_UNSPECIFIED"
    assert request["generationConfig"]["maxOutputTokens"] == 512
    assert request["generationConfig"]["temperature"] == 0.2
    assert "automaticFunctionCalling" not in request
    assert seen["timeout"] == harness.REQUEST_TIMEOUT_SECONDS
    assert seen["closed"] is True


def test_offline_count_transport_failure_is_terminal_without_retry():
    harness = _harness()
    calls = []
    delays = []

    def transient_failure(request, timeout):
        calls.append(
            (
                request.full_url,
                request.data,
                tuple(sorted(request.header_items())),
                timeout,
            )
        )
        raise URLError("synthetic transient")

    request = {
        "model": harness.MODEL,
        "contents": [{"role": "user", "parts": [{"text": "synthetic"}]}],
        "config": {"automatic_function_calling": {"disable": True}},
    }
    with pytest.raises(harness.CountTokensFailure) as transient_failure_result:
        harness._raw_developer_count_tokens(
            "synthetic-key",
            model=harness.MODEL,
            request=request,
            urlopen=transient_failure,
            sleep=delays.append,
        )

    assert transient_failure_result.value.transport_attempts == 1
    assert len(calls) == 1
    assert delays == []

    deterministic_calls = []

    def invalid_argument(request, timeout):
        deterministic_calls.append((request.full_url, request.data, timeout))
        payload = json.dumps(
            {
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": "synthetic invalid request",
                }
            }
        ).encode("utf-8")
        raise HTTPError(request.full_url, 400, "Bad Request", None, BytesIO(payload))

    with pytest.raises(harness.CountTokensFailure) as failure:
        harness._raw_developer_count_tokens(
            "synthetic-key",
            model=harness.MODEL,
            request=request,
            urlopen=invalid_argument,
            sleep=lambda _delay: pytest.fail("deterministic errors must not retry"),
        )
    assert failure.value.transport_attempts == 1
    assert len(deterministic_calls) == 1


def test_offline_inference_transport_error_is_terminal_without_retry():
    harness = _harness()
    delays = []

    class TransientDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.inference_calls = []

        def count_tokens(self, **_kwargs):
            return {"totalTokens": 7}

        def generate_content(self, **kwargs):
            self.inference_calls.append(copy.deepcopy(kwargs))
            if len(self.inference_calls) == 1:
                raise URLError("synthetic transient")
            return {
                "usage_metadata": {
                    "prompt_token_count": 7,
                    "candidates_token_count": 1,
                }
            }

    delegate = TransientDelegate()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=harness.MPS_001_RERUN_CASE_BUDGET,
        aggregate=harness.AggregateUsage(),
        sleep=delays.append,
    )
    with pytest.raises(URLError):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    assert len(delegate.inference_calls) == 1
    assert guard.provider_calls == 1
    assert guard.model_turns == 1
    assert guard.inference_transport_attempts == 1
    assert guard.automatic_retry_count == 0
    assert delays == []


def test_offline_raw_http_count_failure_drops_untrusted_error_body_and_blocks_inference():
    """Unsafe Provider error text never crosses into a persisted preflight record."""

    harness = _harness()
    synthetic_key = "synthetic-key-must-not-persist"

    def fake_urlopen(_request, timeout):
        assert timeout == harness.REQUEST_TIMEOUT_SECONDS
        payload = json.dumps(
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": (
                        "expected_answer=gold; "
                        f"https://example.invalid/countTokens?key={synthetic_key}; "
                        "Authorization: Bearer synthetic-token"
                    ),
                }
            }
        ).encode("utf-8")
        raise HTTPError(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{harness.MODEL}:countTokens?key={synthetic_key}",
            429,
            "Too Many Requests",
            {"Authorization": "Bearer synthetic-token"},
            BytesIO(payload),
        )

    class RawHttpFailureDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = []
            self.inference_calls = []

        def count_tokens(self, *, model, request):
            self.count_calls.append(copy.deepcopy({"model": model, "request": request}))
            return harness._raw_developer_count_tokens(
                synthetic_key,
                model=model,
                request=request,
                urlopen=fake_urlopen,
            )

        def generate_content(self, **kwargs):
            self.inference_calls.append(copy.deepcopy(kwargs))
            return {"text": "must not be reached"}

    delegate = RawHttpFailureDelegate()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS_FAILED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    record = guard.preflight_records[0]
    assert record["failure_diagnostic"] == {
        "exception_class": "HTTPError",
        "http_status": 429,
        "provider_error_code": "RESOURCE_EXHAUSTED",
        "failure_kind": "UNCLASSIFIED",
        "message": harness.COUNT_TOKENS_FAILURE_GENERIC_MESSAGE,
    }
    assert set(record["failure_diagnostic"]) == {
        "exception_class",
        "http_status",
        "provider_error_code",
        "failure_kind",
        "message",
    }
    persisted = json.dumps(record, sort_keys=True)
    assert synthetic_key not in persisted
    assert "Authorization" not in persisted
    assert "countTokens?key=" not in persisted
    assert "expected_answer" not in persisted
    assert "gold" not in persisted
    assert len(delegate.count_calls) == 1
    assert delegate.inference_calls == []
    assert guard.preflight_count_requests == 1
    assert guard.provider_calls == 0


def test_offline_raw_http_count_failure_drops_untrusted_provider_error_code():
    """Hostile JSON ``error.status`` cannot escape through diagnostic metadata."""

    harness = _harness()
    synthetic_key = "synthetic-key-must-not-persist"

    def fake_urlopen(_request, timeout):
        assert timeout == harness.REQUEST_TIMEOUT_SECONDS
        payload = json.dumps(
            {
                "error": {
                    "code": 429,
                    "status": "expectedAnswer.GOLDRESULT",
                    "message": "private synthetic prompt/context marker",
                }
            }
        ).encode("utf-8")
        raise HTTPError(
            f"https://example.invalid/countTokens?key={synthetic_key}",
            429,
            "Too Many Requests",
            None,
            BytesIO(payload),
        )

    class RawHttpFailureDelegate:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self):
            self.count_calls = []
            self.inference_calls = []

        def count_tokens(self, *, model, request):
            self.count_calls.append(copy.deepcopy({"model": model, "request": request}))
            return harness._raw_developer_count_tokens(
                synthetic_key,
                model=model,
                request=request,
                urlopen=fake_urlopen,
            )

        def generate_content(self, **kwargs):
            self.inference_calls.append(copy.deepcopy(kwargs))
            return {"text": "must not be reached"}

    delegate = RawHttpFailureDelegate()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS_FAILED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    record = guard.preflight_records[0]
    assert record["failure_diagnostic"]["provider_error_code"] == harness.UNAVAILABLE
    assert record["failure_diagnostic"]["message"] == harness.COUNT_TOKENS_FAILURE_GENERIC_MESSAGE
    persisted = json.dumps(record, sort_keys=True)
    assert synthetic_key not in persisted
    assert "private synthetic prompt/context marker" not in persisted
    assert "expectedAnswer" not in persisted
    assert "GOLDRESULT" not in persisted
    assert delegate.inference_calls == []
    assert guard.preflight_count_requests == 1
    assert guard.provider_calls == 0


def test_offline_mps_001_rerun_uses_bounded_twelve_turn_single_case_budget():
    """The rerun stays on mps-001 with the approved bounded Agent-loop budget."""

    harness = _harness()
    artifact_root = harness.REPO / ".tmp" / f"m12-mps-rerun-{harness.uuid4().hex}"
    selected_case_ids = []
    events = []

    class FakeTransport:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def count_tokens(self, **_kwargs):
            events.append("count")
            return {"totalTokens": 7}

        def generate_content(self, **kwargs):
            events.append("inference")
            assert kwargs["config"]["max_output_tokens"] == 8_192
            return {
                "usage_metadata": {
                    "prompt_token_count": 7,
                    "candidates_token_count": 1,
                }
            }

        def close(self):
            events.append("close")

    def fake_execute_real_case(
        *, case, sdk_client, run_root, aggregate, case_budget, aggregate_budget, **_kwargs
    ):
        selected_case_ids.append(case.case_id)
        assert case.case_id == "mps-001"
        assert case_budget.max_preflight_requests == 12
        assert case_budget.max_provider_requests == 12
        assert case_budget.max_model_turns == 12
        assert case_budget.per_request_input_limit == 50_000
        assert case_budget.max_input_tokens == 160_000
        assert case_budget.max_preflight_counted_input_tokens == 160_000
        assert case_budget.per_request_output_tokens == 8_192
        assert case_budget.max_output_tokens == 16_384
        assert case_budget.automatic_retry_count == 0
        assert aggregate_budget == harness.MPS_001_RERUN_AGGREGATE_BUDGET
        guard = harness.BudgetedGeminiClient(
            sdk_client,
            case=case,
            case_budget=case_budget,
            aggregate=aggregate,
        )
        for index in range(12):
            guard.generate_content(
                model=harness.MODEL,
                contents=[
                    {"role": "user", "parts": [{"text": f"synthetic-{index}"}]}
                ],
                config={"automatic_function_calling": {"disable": True}},
            )
        with pytest.raises(harness.SmokeBlocked, match="PREFLIGHT|terminal"):
            guard.generate_content(
                model=harness.MODEL,
                    contents=[{"role": "user", "parts": [{"text": "thirteenth"}]}],
                config={"automatic_function_calling": {"disable": True}},
            )
        assert guard.preflight_count_requests == 12
        assert guard.provider_calls == 12
        case_root = run_root / case.case_id
        case_root.mkdir(parents=True)
        return case_root

    try:
        with patch.object(harness, "ARTIFACT_ROOT", artifact_root):
            with patch.object(harness, "verify_frozen_inputs"):
                with patch.object(
                    harness,
                    "load_real_settings",
                    return_value=harness.RealSettings(harness.MODEL, "synthetic-key"),
                ):
                    with patch.object(
                        harness, "build_official_client", return_value=FakeTransport()
                    ):
                        with patch.object(
                            harness,
                            "execute_real_case",
                            side_effect=fake_execute_real_case,
                        ):
                            run_root = harness.run_mps_001_rerun(
                                environ={harness.MPS_001_RERUN_ENV: "1"}
                            )
        assert run_root.is_dir()
        assert selected_case_ids == ["mps-001"]
        assert events == ["count", "inference"] * 12 + ["close"]
    finally:
        shutil.rmtree(artifact_root, ignore_errors=True)


def test_offline_mps_001_rerun_enforces_request_and_cumulative_input_caps():
    harness = _harness()

    class CountSequenceTransport:
        vertexai = False
        api_endpoint = harness.DEVELOPER_API_BASE_URL

        def __init__(self, counts):
            self.counts = iter(counts)
            self.inference_calls = 0

        def count_tokens(self, **_kwargs):
            return {"totalTokens": next(self.counts)}

        def generate_content(self, **_kwargs):
            self.inference_calls += 1
            return {
                "usage_metadata": {
                    "prompt_token_count": 1,
                    "candidates_token_count": 1,
                }
            }

    def generate(guard, label):
        return guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": label}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    request_transport = CountSequenceTransport([50_001])
    request_guard = harness.BudgetedGeminiClient(
        request_transport,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=harness.MPS_001_RERUN_CASE_BUDGET,
        aggregate=harness.AggregateUsage(),
        aggregate_budget=harness.MPS_001_RERUN_AGGREGATE_BUDGET,
    )
    with pytest.raises(
        harness.SmokeBlocked,
        match="PREFLIGHT_REQUEST_INPUT_TOKEN_BUDGET_EXCEEDED",
    ):
        generate(request_guard, "over per-request cap")
    assert request_guard.preflight_count_requests == 1
    assert request_transport.inference_calls == 0

    cumulative_transport = CountSequenceTransport([50_000, 50_000, 50_000, 10_001])
    cumulative_guard = harness.BudgetedGeminiClient(
        cumulative_transport,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=harness.MPS_001_RERUN_CASE_BUDGET,
        aggregate=harness.AggregateUsage(),
        aggregate_budget=harness.MPS_001_RERUN_AGGREGATE_BUDGET,
    )
    generate(cumulative_guard, "first request")
    generate(cumulative_guard, "second request")
    generate(cumulative_guard, "third request")
    with pytest.raises(
        harness.SmokeBlocked,
        match="PREFLIGHT_INPUT_TOKEN_BUDGET_EXCEEDED",
    ):
        generate(cumulative_guard, "fourth request cumulative overflow")
    assert cumulative_guard.preflight_count_requests == 4
    assert cumulative_guard.preflight_counted_input_tokens == 150_000
    assert cumulative_transport.inference_calls == 3


def test_offline_raw_count_endpoint_blocks_unrepresentable_generation_config():
    harness = _harness()
    calls = []

    def fake_urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("remote count endpoint must not be called")

    with pytest.raises(harness.SmokeBlocked, match="UNREPRESENTABLE"):
        harness._raw_developer_count_tokens(
            "synthetic-key",
            model=harness.MODEL,
            request={
                "model": harness.MODEL,
                "contents": [{"role": "user", "parts": [{"text": "synthetic"}]}],
                "config": {
                    "automatic_function_calling": {"disable": True},
                    "unsupported_generation_field": "must block",
                },
            },
            urlopen=fake_urlopen,
        )
    assert calls == []


def test_offline_surface_uses_sdk_shape_with_explicit_developer_transport_metadata():
    harness = _harness()
    calls = {"count": 0, "inference": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            calls["inference"] += 1
            return {
                "usage_metadata": {
                    "prompt_token_count": 7,
                    "candidates_token_count": 1,
                }
            }

    class FakeClient:
        models = FakeModels()
        vertexai = False
        _api_client = type(
            "ApiClient",
            (),
            {
                "vertexai": False,
                "_http_options": type(
                    "HttpOptions",
                    (),
                    {"base_url": harness.DEVELOPER_API_BASE_URL},
                )(),
            },
        )()

    def fake_count_tokens_transport(**_kwargs):
        calls["count"] += 1
        return {"total_tokens": 7}

    surface = harness.DeveloperApiModelsSurface(
        FakeClient(),
        count_tokens_transport=fake_count_tokens_transport,
    )
    assert surface.count_tokens(
        model=harness.MODEL,
        request={"model": harness.MODEL, "contents": [], "config": {}},
    ) == {"total_tokens": 7}
    surface.generate_content(model=harness.MODEL, contents=[], config={})
    assert calls == {"count": 1, "inference": 1}


def test_offline_raw_count_rejects_alias_conflicts_and_sdk_rejected_fields():
    harness = _harness()
    calls = []

    def fake_urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("remote count endpoint must not be called")

    bad_configs = (
        {
            "automatic_function_calling": {"disable": True},
            "max_output_tokens": 512,
            "maxOutputTokens": 256,
        },
        {
            "automatic_function_calling": {"disable": True},
            "temperature": 0.1,
            "generation_config": {"temperature": 0.2},
        },
        {
            "automatic_function_calling": {"disable": True},
            "routing_config": {"autoRoutingMode": {"modelRoutingPreference": "LOW"}},
        },
    )
    for config in bad_configs:
        with pytest.raises(harness.SmokeBlocked, match="UNREPRESENTABLE"):
            harness._raw_developer_count_tokens(
                "synthetic-key",
                model=harness.MODEL,
                request={
                    "model": harness.MODEL,
                    "contents": [{"role": "user", "parts": [{"text": "synthetic"}]}],
                    "config": config,
                },
                urlopen=fake_urlopen,
            )
    assert calls == []


def test_offline_post_response_output_budget_is_terminal_before_next_preflight():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_response={"total_tokens": 7},
        response={
            "usage_metadata": {
                "prompt_token_count": 7,
                "candidates_token_count": 1_537,
            }
        },
    )
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=harness.AggregateUsage(),
    )

    with pytest.raises(harness.SmokeBlocked, match="REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert guard._terminal is True

    with pytest.raises(harness.SmokeBlocked, match="terminal|PREFLIGHT"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "second"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(delegate.count_calls) == 1
    assert len(delegate.calls) == 1


def test_offline_undercount_records_all_available_usage_before_terminal_stop():
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_response={"total_tokens": 7},
        response={
            "usage_metadata": {
                "prompt_token_count": 8,
                "candidates_token_count": 4,
                "thoughts_token_count": 2,
                "cost_usd": 0.02,
            }
        },
    )
    aggregate = harness.AggregateUsage()
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.PUBLIC_CASES[0],
        case_budget=harness.BUDGET_MANIFEST.case("mps-001"),
        aggregate=aggregate,
    )

    with pytest.raises(harness.SmokeBlocked, match="PREFLIGHT_UNDERCOUNT_MISMATCH"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )

    record = guard.preflight_records[0]
    assert record["reported_input_tokens"] == 8
    assert record["reported_output_tokens"] == 4
    assert record["reported_thinking_tokens"] == 2
    assert record["reported_cost_usd"] == pytest.approx(0.02)
    assert record["usage_delta"] == 1
    assert guard.reported_thinking_tokens == 2
    assert guard.reported_billable_output_tokens == 6
    assert guard.reported_cost_usd == pytest.approx(0.02)
    assert aggregate.reported_thinking_tokens == 2
    assert aggregate.reported_billable_output_tokens == 6
    assert aggregate.reported_cost_usd == pytest.approx(0.02)

    with pytest.raises(harness.SmokeBlocked, match="terminal|PREFLIGHT"):
        guard.generate_content(
            model=harness.MODEL,
            contents=[{"role": "user", "parts": [{"text": "second"}]}],
            config={"automatic_function_calling": {"disable": True}},
        )
    assert len(delegate.count_calls) == 1
    assert len(delegate.calls) == 1


def test_offline_hard_stop_preserves_sealed_case_and_aggregate_stop_evidence():
    harness = _harness()
    run_root = harness.REPO / ".tmp" / f"m12-hard-stop-{harness.uuid4().hex}"
    case_root = run_root / "mps-001"
    case_root.mkdir(parents=True)
    try:
        observed = {
            "schema_version": "m1-2-observed-case/v1",
            "requested": {"case_id": "mps-001"},
            "runtime": {
                "status": "failed",
                "provider_requests": 0,
                "model_turns": 0,
                "termination": {"code": "COUNT_TOKENS_FAILED"},
            },
            "result": None,
        }
        harness._seal_observed_case(case_root, observed)
        harness._write_evaluator_unavailable(
            case_root,
            case_id="mps-001",
            reason="COUNT_TOKENS_FAILED",
        )
        aggregate = harness.AggregateUsage()
        harness._write_early_termination_artifacts(
            run_root,
            case_roots=[case_root],
            aggregate=aggregate,
            error_code="COUNT_TOKENS_FAILED",
        )

        assert (case_root / "observed_summary.seal.json").is_file()
        unavailable = json.loads(
            (case_root / "post_run_evaluation.unavailable.json").read_text(encoding="utf-8")
        )
        assert unavailable["evaluated_after_seal"] is False
        assert unavailable["status"] == "unavailable"
        aggregate_summary = json.loads(
            (run_root / "aggregate_summary.json").read_text(encoding="utf-8")
        )
        assert aggregate_summary["termination"]["status"] == "failed"
        assert aggregate_summary["termination"]["error_code"] == "COUNT_TOKENS_FAILED"
        assert aggregate_summary["termination"]["case_ids"] == ["mps-001"]
        assert (run_root / "aggregate_summary.seal.json").is_file()
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_offline_run_hard_stop_writes_aggregate_stop_accounting_without_provider():
    harness = _harness()
    artifact_root = harness.REPO / ".tmp" / f"m12-run-hard-stop-{harness.uuid4().hex}"
    seen_run_roots = []

    class FakeClient:
        def close(self):
            return None

    def fake_execute_real_case(*, case, run_root, **kwargs):
        seen_run_roots.append(run_root)
        case_root = run_root / case.case_id
        case_root.mkdir(parents=True)
        harness._seal_observed_case(
            case_root,
            {
                "schema_version": "m1-2-observed-case/v1",
                "requested": {"case_id": case.case_id},
                "runtime": {
                    "status": "failed",
                    "termination": {"code": "COUNT_TOKENS_FAILED"},
                },
                "result": None,
            },
        )
        raise harness.SmokeBlocked("M1_2_HARD_STOP_AFTER_SEALED_RESULT")

    with patch.object(harness, "ARTIFACT_ROOT", artifact_root):
        with patch.object(
            harness,
            "load_real_settings",
            return_value=harness.RealSettings(harness.MODEL, "synthetic-key"),
        ):
            with patch.object(harness, "verify_frozen_inputs"):
                with patch.object(
                    harness,
                    "build_official_client",
                    return_value=FakeClient(),
                ):
                    with patch.object(
                        harness,
                        "execute_real_case",
                        side_effect=fake_execute_real_case,
                    ):
                        with pytest.raises(
                            harness.SmokeBlocked,
                            match="M1_2_HARD_STOP_AFTER_SEALED_RESULT",
                        ):
                            harness.run_real_provider_smoke()

    assert len(seen_run_roots) == 1
    run_root = seen_run_roots[0]
    try:
        case_root = run_root / "mps-001"
        assert (case_root / "observed_summary.seal.json").is_file()
        assert (case_root / "post_run_evaluation.unavailable.json").is_file()
        summary = json.loads(
            (run_root / "aggregate_summary.json").read_text(encoding="utf-8")
        )
        assert summary["termination"]["status"] == "failed"
        assert summary["termination"]["error_code"] == "M1_2_HARD_STOP_AFTER_SEALED_RESULT"
        assert summary["case_ids"] == ["mps-001"]
        assert (run_root / "aggregate_summary.seal.json").is_file()
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_real_provider_team_decision_smoke_is_opt_in():
    """This test is skipped by default and is never run in offline verification."""

    if os.environ.get(MPS_001_RERUN_ENV) == "1":
        pytest.skip("mps-001 rerun mode excludes the three-case smoke")
    settings = _harness().load_real_settings()
    _harness().run_real_provider_smoke(settings)


def test_real_provider_mps_001_rerun_is_opt_in():
    """The explicit rerun target can never select the other two cases."""

    if os.environ.get(MPS_001_RERUN_ENV) != "1":
        pytest.skip("mps-001 rerun mode is not enabled")
    _harness().run_mps_001_rerun()
