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
import shutil
import time
from typing import Any, Callable
from urllib.error import URLError
from uuid import uuid4

import pytest

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.providers.deepseek_api import build_deepseek_request
from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.checkpoint import SQLiteCheckpointer
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        budget.validate()
        self.delegate = delegate
        self.budget = budget
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
        for retry_index in range(MAX_TRANSPORT_RETRIES_PER_REQUEST + 1):
            attempts_for_turn += 1
            self.transport_attempts += 1
            try:
                response = self.delegate.create_chat_completion(**deepcopy(payload))
                break
            except Exception as exception:
                if (
                    retry_index >= MAX_TRANSPORT_RETRIES_PER_REQUEST
                    or not _is_transient_transport_failure(exception)
                ):
                    self.turns.append(
                        {
                            "logical_request": self.logical_requests,
                            "conservative_input_tokens": conservative_tokens,
                            "transport_attempts": attempts_for_turn,
                            "request_sha256": canonical_request_sha,
                            "status": "provider_error",
                        }
                    )
                    self.last_guard_state = "PROVIDER_ERROR"
                    raise
                self.transport_retry_count += 1
                self.sleep(TRANSPORT_RETRY_BASE_SECONDS * (2**retry_index))
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


def evaluate_sealed_case(case_root: Path) -> dict[str, Any]:
    observed = _read_json(case_root / "observed_summary.json")
    has_final = any(
        isinstance(turn.get("action"), dict)
        and turn["action"].get("kind") == "final"
        for turn in observed.get("trajectory", [])
        if isinstance(turn, dict)
    )
    actual = observed.get("team_decision_result")
    runtime_error = observed.get("runtime", {}).get("error")
    if not has_final:
        evaluation = {
            "schema_version": "deepseek-posthoc-evaluation/v1",
            "case_id": CASE_ID,
            "infrastructure": "BLOCKED",
            "semantic": "NOT_EVALUATED",
            "grounding": "NOT_EVALUATED",
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
        and actual_decision.get("value") == expected_decision.get("value")
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
        "semantic": "PASS" if semantic_pass else "FAIL",
        "grounding": "PASS" if grounding else "FAIL",
        "gold_accessed": True,
        "gold_dataset_sha256": FROZEN_DATASET_SHA256,
        "correctness": {
            "decision_status": decision_status_match,
            "decision_value": decision_value_match,
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
    index_path = scan_vault(vault, case_root / "scan").index_path
    delegate = build_official_client(settings.api_key)
    guard = BudgetedDeepSeekClient(delegate)
    adapter = DeepSeekProviderAdapter(
        guard,
        model_id=MODEL,
        json_output=True,
    )
    runtime = RuntimeEngine(
        vault_root=vault,
        index_path=index_path,
        checkpoint_dir=case_root / "checkpoints",
        trace_dir=case_root / "traces",
        memory_root=case_root / "memory",
        model=adapter,
    )
    request_id = f"deepseek-{CASE_ID}-{uuid4().hex}"
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
    state = runtime.checkpointer.get_latest(status.thread_id)
    if state is None:
        raise SmokeBlocked("DEEPSEEK_RUNTIME_STATE_UNAVAILABLE")
    result = None
    if state.result_ref:
        result_path = case_root / "checkpoints" / state.result_ref
        if result_path.is_file():
            result = _read_json(result_path)
    source_after = _file_hashes(source)
    copy_after = _file_hashes(vault)
    if source_after != source_before or copy_after != source_before:
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_MUTATED")
    _scan_for_credential(case_root, settings.api_key)
    artifact_store = ModelArtifactStore(case_root / "checkpoints" / "models")
    runtime_error = status.error
    if runtime_error is None:
        runtime_error = state.error.to_dict() if state.error is not None else None
    elif hasattr(runtime_error, "to_dict"):
        runtime_error = runtime_error.to_dict()
    observed = {
        "schema_version": "deepseek-real-smoke-observed/v1",
        "case_id": CASE_ID,
        "workspace_id": WORKSPACE_ID,
        "model": MODEL,
        "thinking": "disabled",
        "semantic_retry_count": 0,
        "transport_retry_count": guard.transport_retry_count,
        "trajectory": _request_observation_summaries(state, artifact_store),
        "tool_calls": [record.to_dict() for record in state.tool_ledger],
        "visible_evidence_refs": _visible_evidence_refs(state),
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
    }
    _seal_observed(case_root, observed)
    evaluate_sealed_case(case_root)
    _scan_for_credential(case_root, settings.api_key)
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
