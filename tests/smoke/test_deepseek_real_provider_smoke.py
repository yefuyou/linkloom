"""Opt-in, single-case DeepSeek real-provider smoke and offline guard tests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable
import unicodedata
from urllib.error import URLError
from uuid import uuid4

import pytest

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.providers.deepseek_api import build_deepseek_request
from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.errors import RuntimeModelError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault


REPO = Path(__file__).resolve().parents[2]
SEED_ROOT = REPO / "docs" / "requirements" / "m1_team_decision_eval_seed"
DATASET_PATH = SEED_ROOT / "dataset.jsonl"
ARTIFACT_ROOT = REPO / ".artifacts" / "deepseek_real_provider_smoke"
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
OPT_IN_ENV = "LINKLOOM_RUN_DEEPSEEK_REAL_SMOKE"
API_KEY_ENV = "DEEPSEEK_API_KEY"
MAX_MODEL_TURNS = 8
MAX_PROVIDER_REQUESTS = 8
MAX_TRANSPORT_RETRIES_PER_REQUEST = 1
TRANSPORT_RETRY_BASE_SECONDS = 0.25
REQUEST_TIMEOUT_SECONDS = 45
PER_REQUEST_OUTPUT_TOKENS = 2_048
MAX_CUMULATIVE_OUTPUT_TOKENS = MAX_PROVIDER_REQUESTS * PER_REQUEST_OUTPUT_TOKENS
MAX_CONSERVATIVE_INPUT_TOKENS = 100_000
REQUEST_TEMPLATE_RESERVE_TOKENS = 8_192
MAX_COST_USD = Decimal("0.10")
PEAK_CACHE_HIT_USD_PER_MILLION = Decimal("0.006")
PEAK_CACHE_MISS_USD_PER_MILLION = Decimal("0.30")
PEAK_OUTPUT_USD_PER_MILLION = Decimal("1.20")
FROZEN_DATASET_SHA256 = (
    "49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F"
)
FROZEN_WORKSPACE_SHA256 = (
    "980D255FB33D9A937905797F904BDD47566A81C6E4FBE0A6FD664B8823DD3DA0"
)
CASE_ID = "mps-001"
WORKSPACE_ID = "model_provider_selection"
USER_QUESTION = "Which model provider was finally approved for the Atlas Lantern pilot?"

# M1.2 acceptance requires observing the Decision Memory path before an
# integration PASS can be assigned.  The model is not forced to call the tool;
# an uncalled tool remains a visible coverage gap.
REQUIRED_LIVE_DECISION_MEMORY_COVERAGE = True


class SmokeBlocked(RuntimeError):
    """Safe terminal smoke failure without provider payload or credentials."""


@dataclass(frozen=True)
class RealSettings:
    api_key: str


@dataclass(frozen=True)
class DeepSeekSmokeBudget:
    max_provider_requests: int = MAX_PROVIDER_REQUESTS
    max_model_turns: int = MAX_MODEL_TURNS
    max_conservative_input_tokens: int = MAX_CONSERVATIVE_INPUT_TOKENS
    max_output_tokens: int = MAX_CUMULATIVE_OUTPUT_TOKENS
    per_request_output_tokens: int = PER_REQUEST_OUTPUT_TOKENS
    max_cost_usd: Decimal = MAX_COST_USD

    def worst_case_cost_usd(self) -> Decimal:
        transport_attempt_multiplier = Decimal(
            MAX_TRANSPORT_RETRIES_PER_REQUEST + 1
        )
        return (
            Decimal(self.max_conservative_input_tokens)
            * PEAK_CACHE_MISS_USD_PER_MILLION
            + Decimal(self.max_output_tokens) * PEAK_OUTPUT_USD_PER_MILLION
        ) * transport_attempt_multiplier / Decimal(1_000_000)

    def validate(self) -> None:
        if self.max_provider_requests != MAX_PROVIDER_REQUESTS:
            raise SmokeBlocked("DEEPSEEK_PROVIDER_REQUEST_BUDGET_INVALID")
        if self.max_model_turns != MAX_MODEL_TURNS:
            raise SmokeBlocked("DEEPSEEK_MODEL_TURN_BUDGET_INVALID")
        if self.per_request_output_tokens != PER_REQUEST_OUTPUT_TOKENS:
            raise SmokeBlocked("DEEPSEEK_OUTPUT_CAP_INVALID")
        if self.max_output_tokens != (
            self.max_provider_requests * self.per_request_output_tokens
        ):
            raise SmokeBlocked("DEEPSEEK_OUTPUT_BUDGET_INVALID")
        if self.worst_case_cost_usd() > self.max_cost_usd:
            raise SmokeBlocked("DEEPSEEK_WORST_CASE_COST_EXCEEDED")


BUDGET = DeepSeekSmokeBudget()


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    try:
        return getattr(value, key, default)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return default


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest().upper()


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_frozen_inputs() -> Path:
    dataset_sha = hashlib.sha256(
        DATASET_PATH.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest().upper()
    if dataset_sha != FROZEN_DATASET_SHA256:
        raise SmokeBlocked("DEEPSEEK_GOLDEN_DATASET_DRIFT")
    workspace_root = (SEED_ROOT / "workspaces").resolve()
    workspace = (workspace_root / WORKSPACE_ID).resolve()
    if not workspace.is_relative_to(workspace_root) or not workspace.is_dir():
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_UNAVAILABLE")
    if _canonical_tree_fingerprint(workspace) != FROZEN_WORKSPACE_SHA256:
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_DRIFT")
    return workspace


def load_real_settings(environ: Mapping[str, str] | None = None) -> RealSettings:
    """Read the credential only after the exact opt-in is enabled."""

    source = os.environ if environ is None else environ
    if source.get(OPT_IN_ENV) != "1":
        raise SmokeBlocked("DEEPSEEK_REAL_SMOKE_NOT_OPTED_IN")
    api_key = source.get(API_KEY_ENV)
    if not isinstance(api_key, str) or not api_key.strip():
        raise SmokeBlocked("DEEPSEEK_API_KEY_UNAVAILABLE")
    return RealSettings(api_key=api_key)


class OpenAICompatibleDeepSeekClient:
    """Translate the adapter seam to the installed OpenAI-compatible SDK."""

    def __init__(self, sdk_client: Any) -> None:
        self.sdk_client = sdk_client
        from tests.smoke.transport_observability import TransportObserver

        self.transport_observer = TransportObserver(sdk_client)

    def create_chat_completion(self, **request):
        payload = deepcopy(request)
        thinking = payload.pop("thinking", None)
        if thinking != {"type": "disabled"}:
            raise SmokeBlocked("DEEPSEEK_THINKING_NOT_DISABLED")
        return self.sdk_client.chat.completions.create(
            **payload,
            extra_body={"thinking": thinking},
        )


def build_official_client(api_key: str) -> OpenAICompatibleDeepSeekClient:
    try:
        from openai import OpenAI

        sdk_client = OpenAI(
            api_key=api_key,
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
        return OpenAICompatibleDeepSeekClient(sdk_client)
    except Exception:
        raise SmokeBlocked("DEEPSEEK_CLIENT_CONSTRUCTION_FAILED") from None


def _status_code(exception: BaseException) -> int | None:
    value = _read(exception, "status_code", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_transient_transport_failure(exception: BaseException) -> bool:
    status = _status_code(exception)
    if status in {400, 401, 402, 403, 404, 422}:
        return False
    if status in {408, 429, 500, 502, 503, 504}:
        return True
    if isinstance(exception, (TimeoutError, URLError, ConnectionError)):
        return True
    name = type(exception).__name__.lower()
    return any(
        marker in name
        for marker in (
            "connection",
            "timeout",
            "ratelimit",
            "internalserver",
            "serviceunavailable",
        )
    )


class BudgetedDeepSeekClient:
    """Test-only request, transport-retry, usage, and cost envelope."""

    def __init__(
        self,
        delegate: Any,
        *,
        budget: DeepSeekSmokeBudget = BUDGET,
        max_transport_retries: int = MAX_TRANSPORT_RETRIES_PER_REQUEST,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        budget.validate()
        if (
            isinstance(max_transport_retries, bool)
            or not isinstance(max_transport_retries, int)
            or max_transport_retries < 0
        ):
            raise ValueError("max_transport_retries must be a non-negative integer")
        self.delegate = delegate
        self.budget = budget
        self.max_transport_retries = max_transport_retries
        self.sleep = sleep
        self.logical_requests = 0
        self.transport_attempts = 0
        self.transport_retry_count = 0
        self.conservative_input_tokens = 0
        self.reported_input_tokens = 0
        self.reported_output_tokens = 0
        self.reported_total_tokens = 0
        self.reported_cache_hit_tokens = 0
        self.reported_cache_miss_tokens = 0
        self.reported_reasoning_tokens = 0
        self.estimated_actual_cost_usd = Decimal("0")
        self.provider_turn_ms = 0.0
        self.turns: list[dict[str, Any]] = []
        self.last_guard_state = "READY"

    def _fail(self, state: str) -> None:
        self.last_guard_state = state
        raise SmokeBlocked(state)

    def create_chat_completion(self, **request):
        if self.logical_requests >= self.budget.max_provider_requests:
            self._fail("DEEPSEEK_PROVIDER_REQUEST_BUDGET_EXCEEDED")
        payload = deepcopy(request)
        if payload.get("model") != MODEL:
            self._fail("DEEPSEEK_MODEL_ID_MISMATCH")
        if payload.get("thinking") != {"type": "disabled"}:
            self._fail("DEEPSEEK_THINKING_NOT_DISABLED")
        if payload.get("response_format") != {"type": "json_object"}:
            self._fail("DEEPSEEK_JSON_OUTPUT_NOT_ENABLED")
        payload["max_tokens"] = self.budget.per_request_output_tokens
        conservative_tokens = (
            len(_canonical_json_bytes(payload)) + REQUEST_TEMPLATE_RESERVE_TOKENS
        )
        if (
            self.conservative_input_tokens + conservative_tokens
            > self.budget.max_conservative_input_tokens
        ):
            self._fail("DEEPSEEK_CONSERVATIVE_INPUT_BUDGET_EXCEEDED")
        self.logical_requests += 1
        self.conservative_input_tokens += conservative_tokens
        canonical_request_sha = hashlib.sha256(
            _canonical_json_bytes(payload)
        ).hexdigest()

        response = None
        attempts_for_turn = 0
        for retry_index in range(self.max_transport_retries + 1):
            attempts_for_turn += 1
            self.transport_attempts += 1
            provider_started = time.perf_counter()
            try:
                response = self.delegate.create_chat_completion(**deepcopy(payload))
            except Exception as exception:
                elapsed_ms = (time.perf_counter() - provider_started) * 1000.0
                self.provider_turn_ms += elapsed_ms
                if (
                    retry_index >= self.max_transport_retries
                    or not _is_transient_transport_failure(exception)
                ):
                    self.turns.append(
                        {
                            "logical_request": self.logical_requests,
                            "conservative_input_tokens": conservative_tokens,
                            "transport_attempts": attempts_for_turn,
                            "request_sha256": canonical_request_sha,
                            "status": "provider_error",
                            "elapsed_ms": round(elapsed_ms, 3),
                        }
                    )
                    self.last_guard_state = "PROVIDER_ERROR"
                    raise
                self.transport_retry_count += 1
                self.sleep(TRANSPORT_RETRY_BASE_SECONDS * (2**retry_index))
            else:
                elapsed_ms = (time.perf_counter() - provider_started) * 1000.0
                self.provider_turn_ms += elapsed_ms
                break
        if response is None:
            self._fail("DEEPSEEK_EMPTY_TRANSPORT_RESPONSE")

        usage = _read(response, "usage", None)
        prompt_tokens = _read(usage, "prompt_tokens", 0)
        completion_tokens = _read(usage, "completion_tokens", 0)
        total_tokens = _read(usage, "total_tokens", 0)
        cache_hit = _read(usage, "prompt_cache_hit_tokens", 0) or 0
        cache_miss = _read(usage, "prompt_cache_miss_tokens", None)
        if cache_miss is None:
            cache_miss = prompt_tokens - cache_hit
        completion_details = _read(usage, "completion_tokens_details", None)
        reasoning_tokens = _read(completion_details, "reasoning_tokens", 0) or 0
        counts = (
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cache_hit,
            cache_miss,
            reasoning_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            self._fail("DEEPSEEK_REPORTED_USAGE_INVALID")
        if cache_hit + cache_miss > prompt_tokens:
            self._fail("DEEPSEEK_REPORTED_CACHE_USAGE_INVALID")
        self.reported_input_tokens += prompt_tokens
        self.reported_output_tokens += completion_tokens
        self.reported_total_tokens += total_tokens
        self.reported_cache_hit_tokens += cache_hit
        self.reported_cache_miss_tokens += cache_miss
        self.reported_reasoning_tokens += reasoning_tokens
        self.estimated_actual_cost_usd = (
            Decimal(self.reported_cache_hit_tokens)
            * PEAK_CACHE_HIT_USD_PER_MILLION
            + Decimal(self.reported_cache_miss_tokens)
            * PEAK_CACHE_MISS_USD_PER_MILLION
            + Decimal(self.reported_output_tokens) * PEAK_OUTPUT_USD_PER_MILLION
        ) / Decimal(1_000_000)
        self.turns.append(
            {
                "logical_request": self.logical_requests,
                "conservative_input_tokens": conservative_tokens,
                "reported_input_tokens": prompt_tokens,
                "reported_output_tokens": completion_tokens,
                "reported_total_tokens": total_tokens,
                "reported_cache_hit_tokens": cache_hit,
                "reported_cache_miss_tokens": cache_miss,
                "reported_reasoning_tokens": reasoning_tokens,
                "transport_attempts": attempts_for_turn,
                "request_sha256": canonical_request_sha,
                "status": "response_received",
                "elapsed_ms": round(elapsed_ms, 3),
            }
        )
        if self.reported_input_tokens > self.budget.max_conservative_input_tokens:
            self._fail("DEEPSEEK_REPORTED_INPUT_BUDGET_EXCEEDED")
        if self.reported_output_tokens > self.budget.max_output_tokens:
            self._fail("DEEPSEEK_REPORTED_OUTPUT_BUDGET_EXCEEDED")
        if self.estimated_actual_cost_usd > self.budget.max_cost_usd:
            self._fail("DEEPSEEK_REPORTED_COST_BUDGET_EXCEEDED")
        self.last_guard_state = "WITHIN_BUDGET"
        return response


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SmokeBlocked("DEEPSEEK_ARTIFACT_NOT_OBJECT")
    return value


def _finalize_harness_timings(
    *,
    started_at: float,
    phase_ms: Mapping[str, float],
    provider_turn_ms: float,
    finished_at: float | None = None,
) -> dict[str, Any]:
    """Build non-overlapping harness timings and mark Runtime/provider overlap."""

    phase_names = (
        "fixture_setup_ms",
        "context_index_setup_ms",
        "sqlite_memory_setup_ms",
        "artifact_init_ms",
        "runtime_total_ms",
        "integration_validation_ms",
        "posthoc_evaluation_ms",
        "artifact_sealing_ms",
    )
    phases = {
        name: round(max(0.0, float(phase_ms.get(name, 0.0))), 3)
        for name in phase_names
    }
    total_end = time.perf_counter() if finished_at is None else finished_at
    total_ms = max(0.0, (total_end - started_at) * 1000.0)
    accounted_ms = sum(phases.values())
    result: dict[str, Any] = {
        **phases,
        "provider_turn_ms": round(max(0.0, float(provider_turn_ms)), 3),
        "total_smoke_ms": round(total_ms, 3),
        "unaccounted_ms": round(max(0.0, total_ms - accounted_ms), 3),
        "overlap": {"provider_turn_ms": "inside_runtime_total_ms"},
        "accounted_phase_sum_ms": round(accounted_ms, 3),
        "measurement_notes": {
            "provider_turn_ms": "Nested within runtime_total_ms; excluded from accounted_phase_sum_ms.",
            "artifact_sealing_ms": "Observed summary and smoke verdict seals; timing artifact self-seal excluded.",
            "total_smoke_ms": "Measured through post-run credential scan; excludes harness_timing.json serialization and seal.",
        },
    }
    return result


def _seal_harness_timing(case_root: Path, timing: dict[str, Any]) -> None:
    timing_path = case_root / "harness_timing.json"
    _write_json(timing_path, timing)
    _write_json(
        case_root / "harness_timing.seal.json",
        {
            "schema_version": "deepseek-smoke-timing-seal/v1",
            "sealed": True,
            "harness_timing_sha256": hashlib.sha256(
                timing_path.read_bytes()
            ).hexdigest(),
        },
    )


def _runtime_trace_summaries(trace_dir: Path, run_id: str) -> list[dict[str, Any]]:
    events_path = trace_dir / run_id / "events.jsonl"
    if not events_path.is_file():
        return []
    summaries: list[dict[str, Any]] = []
    relevant = {
        "retrieval.completed",
        "decision_memory.completed",
        "context.assembled",
        "provider.failed",
    }
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(event, dict) and event.get("event_type") in relevant:
            summaries.append(
                {
                    "event_type": event.get("event_type"),
                    "status": event.get("status"),
                    "attributes": event.get("attributes", {}),
                    "error": event.get("error"),
                }
            )
    return summaries


def _memory_and_retrieval_validation(
    *,
    memory_binding: Any,
    tool_calls: list[dict[str, Any]],
    trace_summaries: list[dict[str, Any]],
    database_path: Path,
) -> dict[str, Any]:
    adapter = memory_binding.adapter
    memory_tool_calls = [
        call for call in tool_calls if call.get("tool_id") == "search_decision_memory"
    ]
    retrieved = list(adapter._latest_retrieved_evidence)
    evidence_ids = [item.evidence_id for item in retrieved]
    readback: list[dict[str, Any]] = []
    for evidence in retrieved:
        source = adapter._evidence_by_id.get(evidence.evidence_id, {})
        try:
            resolved = adapter._read_verified_note(evidence.evidence_id)
        except Exception as error:
            # Preserve a terminal identity-validation failure without copying
            # source content or exception messages into the artifact.
            readback.append(
                {
                    "evidence_id": evidence.evidence_id,
                    "source_ref": evidence.source_ref,
                    "logical_path": evidence.logical_path,
                    "resource_id": evidence.resource_id,
                    "content_sha256": source.get("content_sha256"),
                    "readback_path": None,
                    "readback_content_sha256": None,
                    "identity_preserved": False,
                    "error_type": type(error).__name__,
                }
            )
            continue
        readback.append(
            {
                "evidence_id": evidence.evidence_id,
                "source_ref": evidence.source_ref,
                "logical_path": evidence.logical_path,
                "resource_id": evidence.resource_id,
                "content_sha256": source.get("content_sha256"),
                "readback_path": resolved.get("relative_path"),
                "readback_content_sha256": resolved.get("content_sha256"),
                "identity_preserved": (
                    source.get("evidence_id") == evidence.evidence_id
                    and source.get("relative_path") == resolved.get("relative_path")
                    and source.get("content_sha256") == resolved.get("content_sha256")
                    and source.get("source_ref", source.get("relative_path"))
                    == evidence.source_ref
                ),
            }
        )
    contexts = [
        event.get("attributes", {})
        for event in trace_summaries
        if event.get("event_type") == "context.assembled"
    ]
    memory_context_items = [
        item
        for context in contexts
        for item in context.get("selected_items", [])
        if isinstance(item, dict) and item.get("source_type") == "decision_memory"
    ]
    memory_records = list(adapter._latest_decision_records)
    successful_memory_calls = [
        call
        for call in memory_tool_calls
        if call.get("status") == "completed"
        and isinstance(call.get("result"), dict)
        and call["result"].get("status") == "ok"
        and isinstance(call["result"].get("value"), list)
        and bool(call["result"]["value"])
    ]
    terminal_memory_calls = [
        call
        for call in memory_tool_calls
        if call.get("status") in {"completed", "failed"}
    ]
    hybrid_event_count = sum(
        1
        for event in trace_summaries
        if event.get("event_type") == "retrieval.completed"
        and event.get("attributes", {}).get("retrieval_mode") == "hybrid"
    )
    workspace_matches = all(
        item.metadata.get("workspace_id") == adapter.workspace_id
        for item in retrieved
    ) and all(record.workspace_id == adapter.workspace_id for record in memory_records)

    # A validation result is meaningful only after its corresponding pipeline
    # stage has run.  Keep the raw observations above for diagnostics, but do
    # not turn empty collections into vacuous PASS values.
    memory_validation_status = (
        "NOT_EVALUATED"
        if not terminal_memory_calls
        else (
            "PASS"
            if (
                successful_memory_calls
                and adapter.workspace_id == adapter.reader.vault_root_fingerprint
                and memory_records
            )
            else "FAIL"
        )
    )
    retrieval_validation_status = (
        "NOT_EVALUATED"
        if hybrid_event_count == 0
        else (
            "PASS"
            if retrieved and len(evidence_ids) == len(set(evidence_ids))
            else "FAIL"
        )
    )
    evidence_identity_status = (
        "NOT_EVALUATED"
        if not retrieved
        else (
            "PASS"
            if all(item["identity_preserved"] for item in readback)
            else "FAIL"
        )
    )
    context_assembler_status = (
        "NOT_EVALUATED"
        if not contexts
        else (
            "PASS"
            if all(
                context.get("workspace_id") == adapter.workspace_id
                for context in contexts
            )
            and workspace_matches
            else "FAIL"
        )
    )
    return {
        "decision_memory": {
            "tool_invocation_count": len(memory_tool_calls),
            "sqlite_path": database_path.name,
            "sqlite_file_exists": database_path.is_file(),
            "authorized_workspace_identity_matches": (
                adapter.workspace_id == adapter.reader.vault_root_fingerprint
            ),
            "returned_decision_ids": [record.decision_id for record in memory_records],
            "successful_nonempty_tool_result_count": len(successful_memory_calls),
            "context_selected_decision_memory_items": memory_context_items,
            "memory_context_actually_used": bool(
                successful_memory_calls and memory_context_items
            ),
            "validation_status": memory_validation_status,
        },
        "retrieval": {
            "mode": adapter._retrieval_backend.mode.value,
            "hybrid_execution_count": hybrid_event_count,
            "hybrid_executed": hybrid_event_count > 0,
            "selected_evidence_ids": evidence_ids,
            "evidence_ids_unique": (
                len(evidence_ids) == len(set(evidence_ids)) if retrieved else None
            ),
            "evidence_workspace_matches": workspace_matches if retrieved else None,
            "retrieve_read_source_identity_preserved": (
                all(item["identity_preserved"] for item in readback)
                if retrieved
                else None
            ),
            "validation_status": retrieval_validation_status,
            "evidence_identity_status": evidence_identity_status,
            "source_readback": readback,
        },
        "context_assembler": {
            "selection_events": contexts,
            "selected_items": [
                item for context in contexts for item in context.get("selected_items", [])
            ],
            "dropped_items": [
                item for context in contexts for item in context.get("dropped_items", [])
            ],
            "estimated_tokens": [
                context.get("estimated_tokens") for context in contexts
            ],
            "no_cross_workspace_exposure": (
                all(
                    context.get("workspace_id") == adapter.workspace_id
                    for context in contexts
                )
                and workspace_matches
                if contexts
                else None
            ),
            "validation_status": context_assembler_status,
        },
    }


def _normalize_decision_text(value: object) -> str:
    """Normalize prose only for provider-independent semantic comparison."""

    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


_DECISION_POSITIVE_CUES = (
    r"\bapproved\b",
    r"\bselected\b",
    r"\badopted\b",
    r"\bchosen\b",
    r"\bpicked\b",
    r"\bremains\b",
)
_DECISION_NEGATIVE_CUES = (
    r"\bnot\b",
    r"\bnever\b",
    r"\brejected\b",
    r"\bruled\s+out\b",
    r"\binstead\s+of\b",
    r"\brather\s+than\b",
    r"\beither\b",
    r"\bor\b",
    r"\bmaybe\b",
    r"\bpossibly\b",
    r"\bmight\b",
    r"\bcould\b",
    r"\buncertain\b",
    r"\bconsidered\b",
    r"\bproposed\b",
    r"\bcandidate\b",
    r"\bbut\b",
    r"\bhowever\b",
    r"\balthough\b",
    r"\bversus\b",
    r"\bvs\b",
    r"\byet\b",
)


def _decision_value_semantically_matches(actual: object, expected: object) -> bool:
    """Compare a decision value conservatively without case-specific rules.

    The TeamDecision contract permits a non-empty string, and existing
    production-path tests intentionally use a sentence around an atomic
    decision.  Exact normalized equality is always safe.  A longer statement
    is accepted only when it contains the expected value as a bounded span,
    has an affirmative decision cue nearby, and has no negation, contrast, or
    uncertainty cue in that local clause.  Otherwise the result is FAIL rather
    than an optimistic substring match.
    """

    actual_text = _normalize_decision_text(actual)
    expected_text = _normalize_decision_text(expected)
    if not actual_text or not expected_text:
        return False
    if actual_text == expected_text:
        return True

    match = re.search(
        rf"(?<!\w){re.escape(expected_text)}(?!\w)",
        actual_text,
    )
    if match is None:
        return False
    start = max(0, match.start() - 96)
    end = min(len(actual_text), match.end() + 96)
    local_clause = actual_text[start:end]
    surrounding_clause = (
        actual_text[start : match.start()] + " " + actual_text[match.end() : end]
    )
    if any(
        re.search(pattern, surrounding_clause) for pattern in _DECISION_NEGATIVE_CUES
    ):
        return False
    return any(
        re.search(pattern, local_clause) for pattern in _DECISION_POSITIVE_CUES
    )


def _provider_axis_statuses(state: Any) -> dict[str, str]:
    """Separate transport failure from response failure for the smoke axes."""

    executions = getattr(state, "model_executions", None)
    if not isinstance(executions, list) or not executions:
        return {
            "provider_transport": "NOT_EVALUATED",
            "provider_response": "NOT_EVALUATED",
        }
    provider_errors = [
        record.provider_error
        for record in executions
        if isinstance(getattr(record, "provider_error", None), dict)
    ]
    successful_response = any(
        getattr(record, "provider_error", None) is None
        and getattr(record, "status", None)
        in {"response_received", "response_durable", "completed"}
        for record in executions
    )
    transport_error = any(
        isinstance(error.get("details"), dict)
        and error["details"].get("exception_type")
        for error in provider_errors
    )
    response_error = any(
        isinstance(error.get("details"), dict)
        and any(
            error["details"].get(name) is not None
            for name in ("finish_reason", "response_status", "provider_error_code")
        )
        for error in provider_errors
    )
    return {
        "provider_transport": (
            "FAIL" if transport_error else "PASS" if successful_response else "NOT_EVALUATED"
        ),
        "provider_response": (
            "FAIL" if response_error else "PASS" if successful_response else "NOT_EVALUATED"
        ),
    }


def _smoke_axis_statuses(
    *,
    state: Any,
    evaluation: Mapping[str, Any],
    integration: Mapping[str, Any],
) -> dict[str, str]:
    """Return independently inspectable statuses for every smoke dimension."""

    def status(value: object) -> str:
        return value if value in _EVALUATION_STATES else "NOT_EVALUATED"

    provider = _provider_axis_statuses(state)
    memory = integration.get("decision_memory")
    retrieval = integration.get("retrieval")
    assembler = integration.get("context_assembler")
    security_value = (
        assembler.get("no_cross_workspace_exposure")
        if isinstance(assembler, Mapping)
        else None
    )
    security = (
        "PASS"
        if security_value is True
        else "FAIL"
        if security_value is False
        else "NOT_EVALUATED"
    )
    runtime_status = getattr(state, "status", None)
    return {
        **provider,
        "runtime": "PASS" if runtime_status == "completed" else (
            "FAIL" if runtime_status is not None else "NOT_EVALUATED"
        ),
        "retrieval": status(
            retrieval.get("validation_status") if isinstance(retrieval, Mapping) else None
        ),
        "decision_memory": status(
            memory.get("validation_status") if isinstance(memory, Mapping) else None
        ),
        "context_assembly": status(
            assembler.get("validation_status") if isinstance(assembler, Mapping) else None
        ),
        "contract": status(evaluation.get("team_decision_contract")),
        "grounding": status(evaluation.get("grounding")),
        "semantic": status(evaluation.get("semantic")),
        "security": security,
    }


def _smoke_coverage_gaps(axes: Mapping[str, str]) -> list[str]:
    return [key for key, value in axes.items() if value == "NOT_EVALUATED"]


def _smoke_failure_axes(axes: Mapping[str, str]) -> list[str]:
    return [key for key, value in axes.items() if value == "FAIL"]


def _classify_smoke_verdict(
    *,
    state: Any,
    evaluation: dict[str, Any],
    integration: dict[str, Any],
) -> str:
    axes = _smoke_axis_statuses(
        state=state,
        evaluation=evaluation,
        integration=integration,
    )
    if axes["provider_transport"] == "FAIL":
        return "PROVIDER_TRANSPORT_FAIL"
    if axes["provider_response"] == "FAIL":
        return "PROVIDER_RESPONSE_FAIL"
    if axes["contract"] == "FAIL" or axes["grounding"] == "FAIL":
        return "CONTRACT_OR_GROUNDING_FAIL"
    if axes["runtime"] == "FAIL":
        return "INTEGRATION_FAIL"
    if axes["semantic"] == "FAIL":
        return "SEMANTIC_FAIL"
    if (
        axes["retrieval"] == "FAIL"
        or axes["context_assembly"] == "FAIL"
        or axes["security"] == "FAIL"
    ):
        return "INTEGRATION_FAIL"
    if axes["decision_memory"] == "FAIL":
        return "INTEGRATION_FAIL"
    if (
        REQUIRED_LIVE_DECISION_MEMORY_COVERAGE
        and axes["decision_memory"] == "NOT_EVALUATED"
    ):
        return "INTEGRATION_FAIL"
    return "REAL_PROVIDER_INTEGRATION_SMOKE_PASS"


def _seal_smoke_verdict(case_root: Path, verdict: dict[str, Any]) -> None:
    verdict_path = case_root / "smoke_verdict.json"
    _write_json(verdict_path, verdict)
    _write_json(
        case_root / "smoke_verdict.seal.json",
        {
            "schema_version": "deepseek-smoke-verdict-seal/v1",
            "sealed": True,
            "smoke_verdict_sha256": hashlib.sha256(
                verdict_path.read_bytes()
            ).hexdigest(),
        },
    )


def _visible_evidence_refs(state: Any) -> list[str]:
    refs: list[str] = []
    for record in state.tool_ledger:
        raw_result = record.result if isinstance(record.result, dict) else {}
        value = raw_result.get("value")
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                evidence_id = item.get("evidence_id")
                if (
                    isinstance(evidence_id, str)
                    and evidence_id
                    and evidence_id not in refs
                ):
                    refs.append(evidence_id)
    return refs


def _request_observation_summaries(state: Any, artifact_store: ModelArtifactStore):
    summaries: list[dict[str, Any]] = []
    for record in state.model_executions:
        request_artifact = artifact_store.read(record.request_ref)
        model_request = request_artifact.get("model_request")
        if not isinstance(model_request, dict):
            raise SmokeBlocked("DEEPSEEK_DURABLE_MODEL_REQUEST_UNAVAILABLE")
        observation = model_request.get("observation")
        evidence_context = model_request.get("evidence_context", [])
        usage = record.usage if isinstance(record.usage, dict) else {}
        summaries.append(
            {
                "sequence": record.sequence,
                "turn_id": record.turn_id,
                "status": record.status,
                "observation": (
                    {
                        "call_id": observation.get("call_id"),
                        "tool_id": observation.get("tool_id"),
                        "status": observation.get("status"),
                    }
                    if isinstance(observation, dict)
                    else None
                ),
                "visible_evidence_refs": [
                    item.get("evidence_id")
                    for result in evidence_context
                    if isinstance(result, dict)
                    for item in (
                        result.get("value")
                        if isinstance(result.get("value"), list)
                        else [result.get("value")]
                    )
                    if isinstance(item, dict)
                    and isinstance(item.get("evidence_id"), str)
                ],
                "action": deepcopy(record.normalized_action),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "finish_reason": record.finish_reason,
            }
        )
    return summaries


def _actual_team_decision(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    nested = result.get("result")
    if not isinstance(nested, dict):
        return None
    team_decision = nested.get("team_decision")
    return team_decision if isinstance(team_decision, dict) else None


def _claim_refs(value: dict[str, Any]) -> list[str]:
    refs: list[str] = []

    def append(raw: Any) -> None:
        if isinstance(raw, list):
            for ref in raw:
                if isinstance(ref, str) and ref not in refs:
                    refs.append(ref)

    decision = value.get("decision")
    if isinstance(decision, dict):
        append(decision.get("evidence_refs"))
    for key in (
        "rationale",
        "rejected_alternatives",
        "actions",
        "unresolved_items",
    ):
        items = value.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    append(item.get("evidence_refs"))
    uncertainty = value.get("uncertainty")
    if isinstance(uncertainty, dict):
        append(uncertainty.get("evidence_refs"))
    return refs


def _seal_observed(case_root: Path, observed: dict[str, Any]) -> Path:
    observed_path = case_root / "observed_summary.json"
    _write_json(observed_path, observed)
    seal = {
        "schema_version": "deepseek-real-smoke-seal/v1",
        "sealed": True,
        "gold_available": False,
        "observed_summary_sha256": hashlib.sha256(observed_path.read_bytes()).hexdigest(),
    }
    seal_path = case_root / "observed_summary.seal.json"
    _write_json(seal_path, seal)
    return seal_path


def _load_gold_after_seal(case_root: Path) -> dict[str, Any]:
    observed_path = case_root / "observed_summary.json"
    seal = _read_json(case_root / "observed_summary.seal.json")
    if (
        seal.get("sealed") is not True
        or seal.get("gold_available") is not False
        or seal.get("observed_summary_sha256")
        != hashlib.sha256(observed_path.read_bytes()).hexdigest()
    ):
        raise SmokeBlocked("DEEPSEEK_EVALUATION_REQUIRES_SEALED_OBSERVED_RUN")
    for line in DATASET_PATH.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if isinstance(record, dict) and record.get("case_id") == CASE_ID:
            return record
    raise SmokeBlocked("DEEPSEEK_GOLD_CASE_UNAVAILABLE")


_EVALUATION_STATES = frozenset({"PASS", "FAIL", "NOT_EVALUATED"})


def _has_final_action(observed: Mapping[str, Any]) -> bool:
    return any(
        isinstance(turn, Mapping)
        and isinstance(turn.get("action"), Mapping)
        and turn["action"].get("kind") == "final"
        for turn in observed.get("trajectory", [])
        if isinstance(turn, Mapping)
    )


def _normalized_final_result(
    observed: Mapping[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    """Return the normalized Final payload, with a legacy-result fallback."""

    final_action: Mapping[str, Any] | None = None
    for turn in reversed(observed.get("trajectory", [])):
        if not isinstance(turn, Mapping):
            continue
        action = turn.get("action")
        if isinstance(action, Mapping) and action.get("kind") == "final":
            final_action = action
            break
    if final_action is None:
        return False, None

    from linkloom.agents.team_decision import TeamDecisionResult

    # A present Final payload is authoritative.  A materialized result is only
    # a compatibility fallback for older artifacts whose action omitted it.
    if "final_answer" in final_action:
        try:
            return True, TeamDecisionResult.from_json(final_action["final_answer"]).to_dict()
        except (RuntimeModelError, TypeError, ValueError):
            return True, None

    actual = observed.get("team_decision_result")
    if isinstance(actual, Mapping) and isinstance(actual.get("team_decision"), Mapping):
        actual = actual["team_decision"]
    if not isinstance(actual, Mapping):
        return True, None
    try:
        return True, TeamDecisionResult.from_dict(actual).to_dict()
    except (RuntimeModelError, TypeError, ValueError):
        return True, None


def _team_decision_contract_status(observed: Mapping[str, Any]) -> str:
    """Return the contract state without treating an absent Final as failure."""

    has_final, actual = _normalized_final_result(observed)
    if not has_final:
        return "NOT_EVALUATED"
    return "PASS" if actual is not None else "FAIL"


def _integration_validation_statuses(
    integration: Any,
) -> dict[str, str]:
    """Map only executed integration stages to PASS/FAIL; otherwise N/E."""

    if not isinstance(integration, Mapping):
        return {
            "memory": "NOT_EVALUATED",
            "retrieval": "NOT_EVALUATED",
            "evidence_identity": "NOT_EVALUATED",
            "context_assembler": "NOT_EVALUATED",
        }
    memory = integration.get("decision_memory")
    retrieval = integration.get("retrieval")
    assembler = integration.get("context_assembler")
    statuses = {
        "memory": _read(memory, "validation_status", "NOT_EVALUATED"),
        "retrieval": _read(retrieval, "validation_status", "NOT_EVALUATED"),
        "evidence_identity": _read(
            retrieval, "evidence_identity_status", "NOT_EVALUATED"
        ),
        "context_assembler": _read(
            assembler, "validation_status", "NOT_EVALUATED"
        ),
    }
    return {
        key: value if value in _EVALUATION_STATES else "NOT_EVALUATED"
        for key, value in statuses.items()
    }


def evaluate_sealed_case(case_root: Path) -> dict[str, Any]:
    observed = _read_json(case_root / "observed_summary.json")
    has_final, normalized_final = _normalized_final_result(observed)
    contract_status = _team_decision_contract_status(observed)
    integration_status = _integration_validation_statuses(
        observed.get("integration_validation")
    )
    actual = normalized_final
    runtime_error = observed.get("runtime", {}).get("error")
    if not has_final or contract_status != "PASS":
        evaluation = {
            "schema_version": "deepseek-posthoc-evaluation/v1",
            "case_id": CASE_ID,
            "infrastructure": "BLOCKED" if not has_final else "PASS",
            "team_decision_contract": contract_status,
            "semantic": "NOT_EVALUATED",
            "grounding": "NOT_EVALUATED",
            "gold_evaluation": "NOT_EVALUATED",
            "evidence_identity": integration_status["evidence_identity"],
            "evidence_identity_validation": integration_status["evidence_identity"],
            "memory": integration_status["memory"],
            "memory_validation": integration_status["memory"],
            "context_assembler": integration_status["context_assembler"],
            "integration_validation": integration_status,
            "gold_accessed": False,
            "termination_error_code": (
                runtime_error.get("code") if isinstance(runtime_error, dict) else None
            ),
        }
        _write_json(case_root / "posthoc_evaluation.json", evaluation)
        return evaluation

    gold = _load_gold_after_seal(case_root)
    expected_decision = gold.get("expected_decision")
    actual_decision = actual.get("decision") if isinstance(actual, dict) else None
    visible_refs = set(observed.get("visible_evidence_refs", []))
    actual_refs = _claim_refs(actual) if isinstance(actual, dict) else []
    grounding = bool(actual_refs) and set(actual_refs) <= visible_refs
    decision_status_match = (
        isinstance(actual_decision, dict)
        and isinstance(expected_decision, dict)
        and actual_decision.get("status") == expected_decision.get("status")
    )
    decision_value_match = (
        isinstance(actual_decision, dict)
        and isinstance(expected_decision, dict)
        and _decision_value_semantically_matches(
            actual_decision.get("value"), expected_decision.get("value")
        )
    )
    actions_match = (
        isinstance(actual, dict)
        and actual.get("actions") == gold.get("expected_action_items")
    )
    rejected_match = (
        isinstance(actual, dict)
        and actual.get("rejected_alternatives")
        == gold.get("expected_rejected_alternatives")
    )
    unresolved_match = (
        isinstance(actual, dict)
        and actual.get("unresolved_items") == gold.get("expected_unresolved_items")
    )
    semantic_pass = (
        decision_status_match
        and decision_value_match
        and actions_match
        and rejected_match
        and unresolved_match
    )
    evaluation = {
        "schema_version": "deepseek-posthoc-evaluation/v1",
        "case_id": CASE_ID,
        "infrastructure": "PASS",
        "team_decision_contract": "PASS",
        "semantic": "PASS" if semantic_pass else "FAIL",
        "grounding": "PASS" if grounding else "FAIL",
        "gold_evaluation": "PASS" if semantic_pass else "FAIL",
        "evidence_identity": integration_status["evidence_identity"],
        "evidence_identity_validation": integration_status["evidence_identity"],
        "memory": integration_status["memory"],
        "memory_validation": integration_status["memory"],
        "context_assembler": integration_status["context_assembler"],
        "integration_validation": integration_status,
        "gold_accessed": True,
        "gold_dataset_sha256": FROZEN_DATASET_SHA256,
        "correctness": {
            "decision_status": decision_status_match,
            "decision_value": decision_value_match,
            "decision_value_comparison": "semantic_equivalence_v1",
            "actions": actions_match,
            "rejected_alternatives": rejected_match,
            "unresolved_items": unresolved_match,
        },
    }
    _write_json(case_root / "posthoc_evaluation.json", evaluation)
    return evaluation


def _scan_for_credential(root: Path, api_key: str) -> None:
    needle = api_key.encode("utf-8")
    for path in root.rglob("*"):
        if path.is_file() and needle in path.read_bytes():
            raise SmokeBlocked("DEEPSEEK_CREDENTIAL_PERSISTED")


def run_real_deepseek_smoke(settings: RealSettings | None = None) -> Path:
    """Run only mps-001 once through the production Team Decision Runtime."""

    total_started_at = time.perf_counter()
    phase_ms: dict[str, float] = {}
    fixture_started_at = time.perf_counter()
    authorized = load_real_settings()
    settings = authorized
    BUDGET.validate()
    source = verify_frozen_inputs()
    run_root = (ARTIFACT_ROOT / f"mps-001-{uuid4().hex}").resolve()
    case_root = run_root / CASE_ID
    case_root.mkdir(parents=True)
    vault = case_root / "vault"
    source_before = _file_hashes(source)
    shutil.copytree(source, vault)
    if _file_hashes(vault) != source_before:
        raise SmokeBlocked("DEEPSEEK_WORKSPACE_COPY_MISMATCH")
    phase_ms["fixture_setup_ms"] = (time.perf_counter() - fixture_started_at) * 1000.0

    context_started_at = time.perf_counter()
    index_path = scan_vault(vault, case_root / "scan").index_path
    phase_ms["context_index_setup_ms"] = (time.perf_counter() - context_started_at) * 1000.0

    artifact_started_at = time.perf_counter()
    delegate = build_official_client(settings.api_key)
    guard = BudgetedDeepSeekClient(delegate, max_transport_retries=0)
    adapter = DeepSeekProviderAdapter(
        guard,
        model_id=MODEL,
        json_output=True,
    )
    artifact_store = ModelArtifactStore(case_root / "checkpoints" / "models")
    runtime = RuntimeEngine(
        vault_root=vault,
        index_path=index_path,
        checkpoint_dir=case_root / "checkpoints",
        trace_dir=case_root / "traces",
        memory_root=case_root / "memory",
        model=adapter,
    )
    phase_ms["artifact_init_ms"] = (time.perf_counter() - artifact_started_at) * 1000.0

    request_id = f"deepseek-{CASE_ID}-{uuid4().hex}"
    from tests.smoke.m12_memory_fixture import smoke_memory_binding

    sqlite_started_at = time.perf_counter()
    with smoke_memory_binding(
        vault,
        index_path,
        case_root / "temporal_decisions.sqlite",
    ) as memory_binding:
        phase_ms["sqlite_memory_setup_ms"] = (
            time.perf_counter() - sqlite_started_at
        ) * 1000.0

        runtime_started_at = time.perf_counter()
        status = runtime.start_multi_agent(
            RunRequest(
                request_id=request_id,
                thread_id=request_id,
                workflow="team_decision",
                query=USER_QUESTION,
                max_steps=BUDGET.max_model_turns,
                max_provider_requests=BUDGET.max_provider_requests,
                dry_run=True,
            )
        )
        phase_ms["runtime_total_ms"] = (
            time.perf_counter() - runtime_started_at
        ) * 1000.0
        state = runtime.checkpointer.get_latest(status.thread_id)
        if state is None:
            raise SmokeBlocked("DEEPSEEK_RUNTIME_STATE_UNAVAILABLE")
        result = None
        if state.result_ref:
            result_path = case_root / "checkpoints" / state.result_ref
            if result_path.is_file():
                result = _read_json(result_path)

        tool_calls = [record.to_dict() for record in state.tool_ledger]
        trace_summaries = _runtime_trace_summaries(
            case_root / "traces",
            state.run_id,
        )
        integration_started_at = time.perf_counter()
        integration = _memory_and_retrieval_validation(
            memory_binding=memory_binding,
            tool_calls=tool_calls,
            trace_summaries=trace_summaries,
            database_path=case_root / "temporal_decisions.sqlite",
        )
        phase_ms["integration_validation_ms"] = (
            time.perf_counter() - integration_started_at
        ) * 1000.0

    source_after = _file_hashes(source)
    copy_after = _file_hashes(vault)
    if source_after != source_before or copy_after != source_before:
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_MUTATED")
    _scan_for_credential(case_root, settings.api_key)
    runtime_error = status.error
    if runtime_error is None:
        runtime_error = state.error.to_dict() if state.error is not None else None
    elif hasattr(runtime_error, "to_dict"):
        runtime_error = runtime_error.to_dict()
    trajectory = _request_observation_summaries(state, artifact_store)
    model_observed_evidence_refs = list(
        dict.fromkeys(
            ref
            for turn in trajectory
            for ref in turn.get("visible_evidence_refs", [])
            if isinstance(ref, str)
        )
    )
    observed = {
        "schema_version": "deepseek-real-smoke-observed/v1",
        "case_id": CASE_ID,
        "workspace_id": WORKSPACE_ID,
        "model": MODEL,
        "thinking": "disabled",
        "semantic_retry_count": 0,
        "transport_retry_count": guard.transport_retry_count,
        "transport_diagnostics": deepcopy(guard.delegate.transport_observer.records)
        if isinstance(guard.delegate, OpenAICompatibleDeepSeekClient) else [],
        "trajectory": trajectory,
        "tool_calls": tool_calls,
        "visible_evidence_refs": model_observed_evidence_refs,
        "team_decision_result": _actual_team_decision(result),
        "runtime": {
            "status": status.status,
            "termination": state.termination.to_dict() if state.termination else None,
            "error": runtime_error,
            "provider_requests": guard.logical_requests,
            "transport_attempts": guard.transport_attempts,
            "model_turns": len(state.model_executions),
        },
        "usage": {
            "per_turn": deepcopy(guard.turns),
            "reported_input_tokens": guard.reported_input_tokens,
            "reported_output_tokens": guard.reported_output_tokens,
            "reported_total_tokens": guard.reported_total_tokens,
            "reported_cache_hit_tokens": guard.reported_cache_hit_tokens,
            "reported_cache_miss_tokens": guard.reported_cache_miss_tokens,
            "reported_reasoning_tokens": guard.reported_reasoning_tokens,
            "conservative_input_tokens": guard.conservative_input_tokens,
        },
        "cost": {
            "estimated_peak_usd": str(guard.estimated_actual_cost_usd),
            "static_worst_case_usd": str(BUDGET.worst_case_cost_usd()),
            "hard_ceiling_usd": str(BUDGET.max_cost_usd),
            "pricing": {
                "cache_hit_usd_per_million": str(
                    PEAK_CACHE_HIT_USD_PER_MILLION
                ),
                "cache_miss_usd_per_million": str(
                    PEAK_CACHE_MISS_USD_PER_MILLION
                ),
                "output_usd_per_million": str(PEAK_OUTPUT_USD_PER_MILLION),
                "basis": "official peak rates",
            },
        },
        "guard": {
            "state": guard.last_guard_state,
            "budget": {
                **asdict(BUDGET),
                "max_cost_usd": str(BUDGET.max_cost_usd),
            },
        },
        "source_unchanged": source_before == source_after == copy_after,
        "runtime_state": state.to_dict(),
        "result": deepcopy(result),
        "integration_validation": integration,
        "trace_summaries": trace_summaries,
        "provider_failure_diagnostics": [
            {
                "high_level_outcome": record.provider_error.get("code"),
                **deepcopy(record.provider_error.get("details", {})),
            }
            for record in state.model_executions
            if isinstance(record.provider_error, dict)
        ],
    }
    seal_started_at = time.perf_counter()
    _seal_observed(case_root, observed)
    observed_sealing_ms = (
        time.perf_counter() - seal_started_at
    ) * 1000.0

    posthoc_started_at = time.perf_counter()
    evaluation = evaluate_sealed_case(case_root)
    phase_ms["posthoc_evaluation_ms"] = (
        time.perf_counter() - posthoc_started_at
    ) * 1000.0
    _scan_for_credential(case_root, settings.api_key)
    verdict = _classify_smoke_verdict(
        state=state,
        evaluation=evaluation,
        integration=integration,
    )
    axis_statuses = _smoke_axis_statuses(
        state=state,
        evaluation=evaluation,
        integration=integration,
    )
    coverage_gaps = _smoke_coverage_gaps(axis_statuses)
    failure_axes = _smoke_failure_axes(axis_statuses)
    overall_reason = None
    if (
        REQUIRED_LIVE_DECISION_MEMORY_COVERAGE
        and axis_statuses["decision_memory"] == "NOT_EVALUATED"
    ):
        overall_reason = "required_decision_memory_coverage_not_evaluated"
    elif failure_axes:
        overall_reason = "failed_axes_present"
    verdict_sealing_started_at = time.perf_counter()
    team_decision_contract_status = evaluation.get(
        "team_decision_contract", "NOT_EVALUATED"
    )
    if team_decision_contract_status not in _EVALUATION_STATES:
        team_decision_contract_status = "NOT_EVALUATED"
    _seal_smoke_verdict(
        case_root,
        {
            "case_id": CASE_ID,
            "provider": "DeepSeek",
            "model": MODEL,
            "verdict": verdict,
            "semantic": evaluation.get("semantic"),
            "team_decision_contract": team_decision_contract_status,
            "grounding": evaluation.get("grounding"),
            "gold_evaluation": evaluation.get("gold_evaluation"),
            "evidence_identity": evaluation.get("evidence_identity"),
            "evidence_identity_validation": evaluation.get(
                "evidence_identity_validation"
            ),
            "memory": evaluation.get("memory"),
            "memory_validation": evaluation.get("memory_validation"),
            "context_assembler": evaluation.get("context_assembler"),
            "axes": axis_statuses,
            "failure_axes": failure_axes,
            "coverage_gaps": coverage_gaps,
            "overall_reason": overall_reason,
            "provider_requests": guard.logical_requests,
            "transport_retries": guard.transport_retry_count,
        },
    )
    phase_ms["artifact_sealing_ms"] = observed_sealing_ms + (
        time.perf_counter() - verdict_sealing_started_at
    ) * 1000.0
    timing = _finalize_harness_timings(
        started_at=total_started_at,
        phase_ms=phase_ms,
        provider_turn_ms=guard.provider_turn_ms,
    )
    _seal_harness_timing(case_root, timing)
    return run_root


def finalize_existing_deepseek_run(run_root: Path, thread_id: str) -> Path:
    """Seal an already-finished run after a test-only reporter failure.

    This recovery path reads only durable local artifacts and cannot construct
    a Provider client or issue a network request.
    """

    run_root = Path(run_root).resolve()
    case_root = run_root / CASE_ID
    if (case_root / "observed_summary.json").exists():
        raise SmokeBlocked("DEEPSEEK_OBSERVED_SUMMARY_ALREADY_EXISTS")
    source = verify_frozen_inputs()
    source_hashes = _file_hashes(source)
    vault_hashes = _file_hashes(case_root / "vault")
    state = SQLiteCheckpointer(case_root / "checkpoints").get_latest(thread_id)
    if state is None:
        raise SmokeBlocked("DEEPSEEK_RUNTIME_STATE_UNAVAILABLE")
    artifact_store = ModelArtifactStore(case_root / "checkpoints" / "models")
    result = None
    if state.result_ref:
        result_path = case_root / "checkpoints" / state.result_ref
        if result_path.is_file():
            result = _read_json(result_path)

    per_turn: list[dict[str, Any]] = []
    reported_input_tokens = 0
    reported_output_tokens = 0
    reported_total_tokens = 0
    reported_cache_hit_tokens = 0
    reported_cache_miss_tokens = 0
    reported_reasoning_tokens = 0
    conservative_input_tokens = 0
    for record in state.model_executions:
        request_artifact = artifact_store.read(record.request_ref)
        raw_model_request = request_artifact.get("model_request")
        if not isinstance(raw_model_request, dict):
            raise SmokeBlocked("DEEPSEEK_DURABLE_MODEL_REQUEST_UNAVAILABLE")
        model_request = ModelTurnRequest.from_dict(raw_model_request)
        payload = build_deepseek_request(
            model_request,
            configured_model_id=MODEL,
            json_output=True,
        )
        payload["max_tokens"] = BUDGET.per_request_output_tokens
        conservative = len(_canonical_json_bytes(payload)) + REQUEST_TEMPLATE_RESERVE_TOKENS
        conservative_input_tokens += conservative
        usage = record.usage if isinstance(record.usage, dict) else {}
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        total_tokens = usage.get("total_tokens") or 0
        metadata = (
            record.provider_metadata
            if isinstance(record.provider_metadata, dict)
            else {}
        )
        cache_hit = metadata.get("prompt_cache_hit_tokens") or 0
        cache_miss = metadata.get("prompt_cache_miss_tokens")
        if cache_miss is None:
            cache_miss = input_tokens - cache_hit
        reasoning_tokens = metadata.get("reasoning_tokens") or 0
        reported_input_tokens += input_tokens
        reported_output_tokens += output_tokens
        reported_total_tokens += total_tokens
        reported_cache_hit_tokens += cache_hit
        reported_cache_miss_tokens += cache_miss
        reported_reasoning_tokens += reasoning_tokens
        per_turn.append(
            {
                "logical_request": record.sequence,
                "conservative_input_tokens": conservative,
                "reported_input_tokens": input_tokens,
                "reported_output_tokens": output_tokens,
                "reported_total_tokens": total_tokens,
                "reported_cache_hit_tokens": cache_hit,
                "reported_cache_miss_tokens": cache_miss,
                "reported_reasoning_tokens": reasoning_tokens,
                "transport_attempts": "UNAVAILABLE_AFTER_REPORTER_FAILURE",
                "request_sha256": hashlib.sha256(
                    _canonical_json_bytes(payload)
                ).hexdigest(),
                "status": "response_durable",
            }
        )
    estimated_cost = (
        Decimal(reported_cache_hit_tokens) * PEAK_CACHE_HIT_USD_PER_MILLION
        + Decimal(reported_cache_miss_tokens) * PEAK_CACHE_MISS_USD_PER_MILLION
        + Decimal(reported_output_tokens) * PEAK_OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)
    if conservative_input_tokens > BUDGET.max_conservative_input_tokens:
        raise SmokeBlocked("DEEPSEEK_CONSERVATIVE_INPUT_BUDGET_EXCEEDED")
    if reported_output_tokens > BUDGET.max_output_tokens:
        raise SmokeBlocked("DEEPSEEK_REPORTED_OUTPUT_BUDGET_EXCEEDED")
    if estimated_cost > BUDGET.max_cost_usd:
        raise SmokeBlocked("DEEPSEEK_REPORTED_COST_BUDGET_EXCEEDED")

    observed = {
        "schema_version": "deepseek-real-smoke-observed/v1",
        "case_id": CASE_ID,
        "workspace_id": WORKSPACE_ID,
        "model": MODEL,
        "thinking": "disabled",
        "semantic_retry_count": 0,
        "transport_retry_count": "UNAVAILABLE_AFTER_REPORTER_FAILURE",
        "trajectory": _request_observation_summaries(state, artifact_store),
        "tool_calls": [record.to_dict() for record in state.tool_ledger],
        "visible_evidence_refs": _visible_evidence_refs(state),
        "team_decision_result": _actual_team_decision(result),
        "runtime": {
            "status": state.status,
            "termination": state.termination.to_dict() if state.termination else None,
            "error": state.error.to_dict() if state.error else None,
            "provider_requests": len(state.model_executions),
            "transport_attempts": "UNAVAILABLE_AFTER_REPORTER_FAILURE",
            "model_turns": len(state.model_executions),
        },
        "usage": {
            "per_turn": per_turn,
            "reported_input_tokens": reported_input_tokens,
            "reported_output_tokens": reported_output_tokens,
            "reported_total_tokens": reported_total_tokens,
            "reported_cache_hit_tokens": reported_cache_hit_tokens,
            "reported_cache_miss_tokens": reported_cache_miss_tokens,
            "reported_reasoning_tokens": reported_reasoning_tokens,
            "conservative_input_tokens": conservative_input_tokens,
        },
        "cost": {
            "estimated_peak_usd": str(estimated_cost),
            "static_worst_case_usd": str(BUDGET.worst_case_cost_usd()),
            "hard_ceiling_usd": str(BUDGET.max_cost_usd),
            "pricing": {
                "cache_hit_usd_per_million": str(
                    PEAK_CACHE_HIT_USD_PER_MILLION
                ),
                "cache_miss_usd_per_million": str(
                    PEAK_CACHE_MISS_USD_PER_MILLION
                ),
                "output_usd_per_million": str(PEAK_OUTPUT_USD_PER_MILLION),
                "basis": "official peak rates",
            },
        },
        "guard": {
            "state": "WITHIN_BUDGET_FROM_DURABLE_USAGE",
            "budget": {
                **asdict(BUDGET),
                "max_cost_usd": str(BUDGET.max_cost_usd),
            },
            "credential_scan": "PASSED_BEFORE_REPORTER_FAILURE",
        },
        "source_unchanged": source_hashes == vault_hashes,
        "runtime_state": state.to_dict(),
        "result": deepcopy(result),
        "recovery": {
            "mode": "offline_finalize_after_reporter_failure",
            "provider_requests_added": 0,
        },
    }
    _seal_observed(case_root, observed)
    evaluate_sealed_case(case_root)
    return run_root


def test_offline_budget_worst_case_is_below_hard_cost_ceiling():
    BUDGET.validate()

    assert BUDGET.worst_case_cost_usd() == Decimal("0.0993216")
    assert BUDGET.worst_case_cost_usd() < MAX_COST_USD


def test_offline_credential_is_not_read_before_opt_in():
    class GuardedEnvironment(dict):
        def get(self, name, default=None):
            if name == API_KEY_ENV:
                raise AssertionError("credential read before opt-in")
            return super().get(name, default)

    with pytest.raises(SmokeBlocked, match="NOT_OPTED_IN"):
        load_real_settings(GuardedEnvironment())


def test_offline_openai_wrapper_moves_thinking_to_extra_body():
    calls: list[dict[str, Any]] = []

    class Completions:
        def create(self, **request):
            calls.append(request)
            return {"ok": True}

    sdk = type("Sdk", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    client = OpenAICompatibleDeepSeekClient(sdk)

    client.create_chat_completion(
        model=MODEL,
        messages=[{"role": "user", "content": "json"}],
        thinking={"type": "disabled"},
    )

    assert calls == [
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "json"}],
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


def test_offline_installed_sdk_serializes_exact_deepseek_wire_shape():
    import httpx
    from openai import OpenAI

    captured_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "offline-sdk-response",
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_offline_sdk",
                                    "type": "function",
                                    "function": {
                                        "name": "search_notes",
                                        "arguments": '{"query":"offline"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = OpenAI(
        api_key="offline-placeholder",
        base_url=BASE_URL,
        http_client=http_client,
        max_retries=0,
    )
    client = OpenAICompatibleDeepSeekClient(sdk)

    response = client.create_chat_completion(**_guard_request())

    assert response.choices[0].message.tool_calls[0].id == "call_offline_sdk"
    assert len(captured_bodies) == 1
    assert captured_bodies[0]["model"] == MODEL
    assert captured_bodies[0]["messages"] == _guard_request()["messages"]
    assert captured_bodies[0]["thinking"] == {"type": "disabled"}
    assert captured_bodies[0]["response_format"] == {"type": "json_object"}


def _usage_response() -> dict[str, Any]:
    return {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_cache_hit_tokens": 2,
            "prompt_cache_miss_tokens": 8,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    }


def _guard_request() -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Use tools."},
        ],
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "tools": [],
    }


def test_offline_guard_forces_output_cap_and_records_reported_usage():
    class Delegate:
        def __init__(self):
            self.calls = []

        def create_chat_completion(self, **request):
            self.calls.append(request)
            return _usage_response()

    delegate = Delegate()
    guard = BudgetedDeepSeekClient(delegate)

    guard.create_chat_completion(**_guard_request())

    assert len(delegate.calls) == 1
    assert delegate.calls[0]["max_tokens"] == PER_REQUEST_OUTPUT_TOKENS
    assert guard.logical_requests == 1
    assert guard.transport_attempts == 1
    assert guard.reported_input_tokens == 10
    assert guard.reported_output_tokens == 5
    assert guard.reported_reasoning_tokens == 0
    assert guard.estimated_actual_cost_usd == Decimal("0.000008412")


def test_offline_transient_retry_resends_the_exact_same_request_once():
    class Delegate:
        def __init__(self):
            self.calls = []

        def create_chat_completion(self, **request):
            self.calls.append(request)
            if len(self.calls) == 1:
                raise URLError("offline transient")
            return _usage_response()

    delegate = Delegate()
    delays: list[float] = []
    guard = BudgetedDeepSeekClient(delegate, sleep=delays.append)

    guard.create_chat_completion(**_guard_request())

    assert len(delegate.calls) == 2
    assert delegate.calls[0] == delegate.calls[1]
    assert guard.logical_requests == 1
    assert guard.transport_attempts == 2
    assert guard.transport_retry_count == 1
    assert delays == [TRANSPORT_RETRY_BASE_SECONDS]


def test_offline_live_mode_can_disable_harness_transport_retries():
    class Delegate:
        def __init__(self):
            self.calls = []

        def create_chat_completion(self, **request):
            self.calls.append(request)
            raise URLError("offline transient")

    delegate = Delegate()
    guard = BudgetedDeepSeekClient(
        delegate,
        max_transport_retries=0,
        sleep=lambda delay: pytest.fail("retry sleep must not run"),
    )

    with pytest.raises(URLError):
        guard.create_chat_completion(**_guard_request())

    assert len(delegate.calls) == 1
    assert guard.transport_attempts == 1
    assert guard.transport_retry_count == 0
    assert guard.turns[0]["status"] == "provider_error"
    assert guard.turns[0]["transport_attempts"] == 1
    assert guard.turns[0]["elapsed_ms"] >= 0


def test_offline_harness_timings_mark_provider_time_as_nested_and_nonnegative():
    started = time.perf_counter()
    timing = _finalize_harness_timings(
        started_at=started,
        finished_at=started + 0.25,
        phase_ms={
            "fixture_setup_ms": 20.0,
            "context_index_setup_ms": 30.0,
            "sqlite_memory_setup_ms": 10.0,
            "artifact_init_ms": 5.0,
            "runtime_total_ms": 100.0,
            "posthoc_evaluation_ms": 20.0,
            "artifact_sealing_ms": 10.0,
        },
        provider_turn_ms=60.0,
    )

    assert timing["total_smoke_ms"] == pytest.approx(250.0)
    assert timing["provider_turn_ms"] == pytest.approx(60.0)
    assert timing["unaccounted_ms"] == pytest.approx(55.0)
    assert timing["overlap"]["provider_turn_ms"] == "inside_runtime_total_ms"
    assert all(
        timing[key] >= 0
        for key in (
            "fixture_setup_ms",
            "context_index_setup_ms",
            "sqlite_memory_setup_ms",
            "artifact_init_ms",
            "runtime_total_ms",
            "provider_turn_ms",
            "posthoc_evaluation_ms",
            "artifact_sealing_ms",
            "total_smoke_ms",
            "unaccounted_ms",
        )
    )


def test_offline_runner_binds_seeded_sqlite_memory_and_never_retries(
    monkeypatch,
):
    from contextlib import contextmanager

    from tests.smoke import m12_memory_fixture

    offline_root = Path("work") / f"deepseek-smoke-offline-{uuid4().hex}"
    delegate_calls: list[dict[str, Any]] = []
    bindings: list[Any] = []
    guards: list[BudgetedDeepSeekClient] = []

    class Delegate:
        def create_chat_completion(self, **request):
            delegate_calls.append(request)
            raise URLError(TimeoutError("offline deterministic timeout"))

    delegate = Delegate()
    monkeypatch.setitem(globals(), "ARTIFACT_ROOT", offline_root / "artifacts")
    monkeypatch.setitem(
        globals(), "load_real_settings", lambda: RealSettings("offline-placeholder-key")
    )
    monkeypatch.setitem(globals(), "build_official_client", lambda api_key: delegate)
    original_guard = BudgetedDeepSeekClient

    def capture_guard(*args, **kwargs):
        guard = original_guard(*args, **kwargs)
        guards.append(guard)
        return guard

    monkeypatch.setitem(globals(), "BudgetedDeepSeekClient", capture_guard)
    original_binding = m12_memory_fixture.smoke_memory_binding

    @contextmanager
    def capture_binding(*args, **kwargs):
        with original_binding(*args, **kwargs) as binding:
            bindings.append(binding)
            assert binding.adapter._decision_memory_store.get_decision(
                binding.adapter.workspace_id,
                "mps-001-current",
            ) is not None
            yield binding

    monkeypatch.setattr(
        m12_memory_fixture,
        "smoke_memory_binding",
        capture_binding,
    )

    run_root = run_real_deepseek_smoke()
    case_root = run_root / CASE_ID
    observed = _read_json(case_root / "observed_summary.json")
    timing = _read_json(case_root / "harness_timing.json")
    verdict = _read_json(case_root / "smoke_verdict.json")

    assert len(delegate_calls) == 1
    assert guards[0].max_transport_retries == 0
    assert guards[0].transport_retry_count == 0
    assert len(bindings) == 1 and bindings[0].constructions == 1
    assert (case_root / "temporal_decisions.sqlite").is_file()
    assert observed["provider_failure_diagnostics"][0]["low_level_failure"] == "TRANSPORT_TIMEOUT"
    assert verdict["verdict"] == "PROVIDER_TRANSPORT_FAIL"
    assert timing["provider_turn_ms"] >= 0
    assert timing["unaccounted_ms"] >= 0
    assert (case_root / "observed_summary.seal.json").is_file()
    assert (case_root / "harness_timing.seal.json").is_file()
    assert (case_root / "smoke_verdict.seal.json").is_file()


def test_offline_deterministic_400_is_not_retried():
    class BadRequest(Exception):
        status_code = 400

    class Delegate:
        def __init__(self):
            self.calls = []

        def create_chat_completion(self, **request):
            self.calls.append(request)
            raise BadRequest("offline deterministic")

    delegate = Delegate()
    guard = BudgetedDeepSeekClient(delegate, sleep=lambda delay: None)

    with pytest.raises(BadRequest):
        guard.create_chat_completion(**_guard_request())

    assert len(delegate.calls) == 1
    assert guard.transport_retry_count == 0


def test_offline_input_budget_blocks_before_delegate_call():
    class Delegate:
        def __init__(self):
            self.calls = []

        def create_chat_completion(self, **request):
            self.calls.append(request)
            return _usage_response()

    delegate = Delegate()
    guard = BudgetedDeepSeekClient(delegate)
    request = _guard_request()
    request["messages"][1]["content"] = "x" * MAX_CONSERVATIVE_INPUT_TOKENS

    with pytest.raises(SmokeBlocked, match="INPUT_BUDGET_EXCEEDED"):
        guard.create_chat_completion(**request)

    assert delegate.calls == []
    assert guard.logical_requests == 0


def test_offline_trajectory_summary_reads_usage_from_durable_record_shape():
    class Store:
        def read(self, request_ref):
            assert request_ref == "model/request.json"
            return {
                "model_request": {
                    "observation": {
                        "call_id": "call_summary",
                        "tool_id": "search_notes",
                        "status": "ok",
                    },
                    "evidence_context": [],
                }
            }

    record = type(
        "Record",
        (),
        {
            "request_ref": "model/request.json",
            "sequence": 2,
            "turn_id": "run:turn:2",
            "status": "completed",
            "normalized_action": {"kind": "final", "final_answer": "{}"},
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
            "finish_reason": "stop",
        },
    )()
    state = type("State", (), {"model_executions": [record]})()

    summary = _request_observation_summaries(state, Store())

    assert summary[0]["observation"] == {
        "call_id": "call_summary",
        "tool_id": "search_notes",
        "status": "ok",
    }
    assert summary[0]["input_tokens"] == 11
    assert summary[0]["output_tokens"] == 7
    assert summary[0]["total_tokens"] == 18


@pytest.mark.skipif(
    os.environ.get(OPT_IN_ENV) != "1",
    reason="DeepSeek real-provider smoke requires exact opt-in",
)
def test_real_deepseek_mps_001_smoke():
    run_root = run_real_deepseek_smoke()

    assert (run_root / CASE_ID / "observed_summary.seal.json").is_file()
    evaluation = _read_json(run_root / CASE_ID / "posthoc_evaluation.json")
    assert evaluation["infrastructure"] == "PASS"


__all__ = [
    "BUDGET",
    "BudgetedDeepSeekClient",
    "DeepSeekSmokeBudget",
    "OpenAICompatibleDeepSeekClient",
    "SmokeBlocked",
    "evaluate_sealed_case",
    "finalize_existing_deepseek_run",
    "load_real_settings",
    "run_real_deepseek_smoke",
]
