"""Test-only first-result runner for two approved DeepSeek business cases."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from tests.smoke import test_deepseek_real_provider_smoke as baseline


REPO = Path(__file__).resolve().parents[2]
SEED_ROOT = REPO / "docs" / "requirements" / "m1_team_decision_eval_seed"
DATASET_PATH = SEED_ROOT / "dataset.jsonl"
ARTIFACT_ROOT = REPO / ".artifacts" / "deepseek_business_evaluation"
MODEL = baseline.MODEL
OPT_IN_ENV = baseline.OPT_IN_ENV
SmokeBlocked = baseline.SmokeBlocked
_TERMINAL_RUNTIME_STATUSES = frozenset(
    {"completed", "failed", "rejected", "stale", "expired"}
)


@dataclass(frozen=True)
class BusinessCase:
    case_id: str
    workspace_id: str
    user_question: str
    workspace_sha256: str
    max_model_turns: int
    max_provider_requests: int

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


CASES = {
    "aer-002": BusinessCase(
        case_id="aer-002",
        workspace_id="agent_evaluation_rollout",
        user_question=(
            "What is the current evaluation rollout boundary, and which reporting, "
            "multilingual, and threshold actions are still open?"
        ),
        workspace_sha256=(
            "72B1BDA67CC60E53592785633377754ADC5B2FE11B634971B61C6ABF381CF3F8"
        ),
        max_model_turns=6,
        max_provider_requests=6,
    ),
    "iti-005": BusinessCase(
        case_id="iti-005",
        workspace_id="internal_tool_integration",
        user_question=(
            "Who is assigned to own the export checksum review and export-retention "
            "work, and what are their deadlines?"
        ),
        workspace_sha256=(
            "4A8DBAAEDB1D673D2090A161F5E03E160AFB4286EC0A39E3A0D0752689258A2C"
        ),
        max_model_turns=4,
        max_provider_requests=4,
    ),
}


def _verify_case_inputs(case: BusinessCase) -> Path:
    dataset_sha = hashlib.sha256(
        DATASET_PATH.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest().upper()
    if dataset_sha != baseline.FROZEN_DATASET_SHA256:
        raise SmokeBlocked("DEEPSEEK_GOLDEN_DATASET_DRIFT")
    workspaces_root = (SEED_ROOT / "workspaces").resolve()
    workspace = (workspaces_root / case.workspace_id).resolve()
    if not workspace.is_relative_to(workspaces_root) or not workspace.is_dir():
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_UNAVAILABLE")
    if baseline._canonical_tree_fingerprint(workspace) != case.workspace_sha256:
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_DRIFT")
    return workspace


def _runtime_error(status: Any, state: Any) -> dict[str, Any] | None:
    error = status.error
    if error is None:
        return state.error.to_dict() if state.error is not None else None
    if hasattr(error, "to_dict"):
        return error.to_dict()
    return error if isinstance(error, dict) else None


def _assert_observed_bounds(
    observed: Mapping[str, Any],
    case: BusinessCase,
) -> None:
    runtime = observed.get("runtime")
    usage = observed.get("usage")
    cost = observed.get("cost")
    guard = observed.get("guard")
    if not all(isinstance(item, Mapping) for item in (runtime, usage, cost, guard)):
        raise SmokeBlocked("DEEPSEEK_BUSINESS_BOUNDARY_MISSING")
    if runtime.get("status") not in _TERMINAL_RUNTIME_STATUSES:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_NONTERMINAL_RESULT")
    provider_requests = runtime.get("provider_requests")
    model_turns = runtime.get("model_turns")
    if (
        isinstance(provider_requests, bool)
        or not isinstance(provider_requests, int)
        or provider_requests < 1
        or provider_requests > case.max_provider_requests
        or isinstance(model_turns, bool)
        or not isinstance(model_turns, int)
        or model_turns < 1
        or model_turns > case.max_model_turns
    ):
        raise SmokeBlocked("DEEPSEEK_BUSINESS_RUN_BUDGET_EXCEEDED")
    if (
        observed.get("semantic_retry_count") != 0
        or usage.get("reported_reasoning_tokens") != 0
        or guard.get("state") != "WITHIN_BUDGET"
    ):
        raise SmokeBlocked("DEEPSEEK_BUSINESS_EXECUTION_MODE_VIOLATION")
    try:
        estimated_cost = Decimal(str(cost.get("estimated_peak_usd")))
        hard_ceiling = Decimal(str(cost.get("hard_ceiling_usd")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_COST_UNAVAILABLE") from error
    if estimated_cost < 0 or estimated_cost > hard_ceiling:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_COST_BUDGET_EXCEEDED")


def _run_case(
    *,
    run_root: Path,
    case: BusinessCase,
    api_key: str,
) -> Path:
    source = _verify_case_inputs(case)
    case_root = run_root / case.case_id
    case_root.mkdir(parents=True)
    vault = case_root / "vault"
    source_before = baseline._file_hashes(source)
    shutil.copytree(source, vault)
    if baseline._file_hashes(vault) != source_before:
        raise SmokeBlocked("DEEPSEEK_WORKSPACE_COPY_MISMATCH")

    index_path = scan_vault(vault, case_root / "scan").index_path
    delegate = baseline.build_official_client(api_key)
    guard = baseline.BudgetedDeepSeekClient(delegate)
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
    request_id = f"deepseek-{case.case_id}-{uuid4().hex}"
    status = runtime.start_multi_agent(
        RunRequest(
            request_id=request_id,
            thread_id=request_id,
            workflow="team_decision",
            query=case.user_question,
            max_steps=case.max_model_turns,
            max_provider_requests=case.max_provider_requests,
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
            result = baseline._read_json(result_path)

    source_after = baseline._file_hashes(source)
    copy_after = baseline._file_hashes(vault)
    if source_after != source_before or copy_after != source_before:
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_MUTATED")
    baseline._scan_for_credential(case_root, api_key)

    artifact_store = ModelArtifactStore(case_root / "checkpoints" / "models")
    observed = {
        "schema_version": "deepseek-business-observed/v1",
        "case_id": case.case_id,
        "workspace_id": case.workspace_id,
        "model": MODEL,
        "thinking": "disabled",
        "structured_json": True,
        "semantic_retry_count": 0,
        "transport_retry_count": guard.transport_retry_count,
        "trajectory": baseline._request_observation_summaries(state, artifact_store),
        "tool_calls": [record.to_dict() for record in state.tool_ledger],
        "visible_evidence_refs": baseline._visible_evidence_refs(state),
        "team_decision_result": baseline._actual_team_decision(result),
        "runtime": {
            "status": status.status,
            "termination": state.termination.to_dict() if state.termination else None,
            "error": _runtime_error(status, state),
            "provider_requests": guard.logical_requests,
            "transport_attempts": guard.transport_attempts,
            "model_turns": len(state.model_executions),
            "max_provider_requests": case.max_provider_requests,
            "max_model_turns": case.max_model_turns,
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
            "hard_ceiling_usd": str(baseline.BUDGET.max_cost_usd),
            "pricing_basis": "official peak rates",
        },
        "guard": {
            "state": guard.last_guard_state,
            "budget": {
                **asdict(baseline.BUDGET),
                "max_cost_usd": str(baseline.BUDGET.max_cost_usd),
            },
        },
        "source_unchanged": source_before == source_after == copy_after,
        "runtime_state": state.to_dict(),
        "result": deepcopy(result),
    }
    _assert_observed_bounds(observed, case)
    baseline._seal_observed(case_root, observed)
    baseline._scan_for_credential(case_root, api_key)
    return case_root


def _assert_provider_failure_state(case: BusinessCase, state: Any) -> None:
    """Require the narrow terminal shape accepted by offline failure sealing."""
    if state.status not in _TERMINAL_RUNTIME_STATUSES or state.status != "failed":
        raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")
    if state.termination is None or state.termination.status != "failed":
        raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")
    records = list(state.model_executions)
    if not records:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")
    failed_record = records[-1]
    provider_error = failed_record.provider_error
    if (
        failed_record.status != "failed"
        or not isinstance(provider_error, dict)
        or provider_error.get("code") != "MODEL_TRANSIENT_FAILURE"
        or provider_error.get("outcome") != "unknown_provider_outcome"
        or failed_record.normalized_action is not None
        or failed_record.normalized_proposal is not None
    ):
        raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")
    expected_tool_calls = 0
    for record in records[:-1]:
        proposal = record.normalized_proposal
        calls = proposal.get("tool_calls") if isinstance(proposal, dict) else None
        if (
            record.status != "tool_results_durable"
            or not isinstance(proposal, dict)
            or proposal.get("kind") != "tool_calls"
            or not isinstance(calls, list)
            or not calls
            or len(record.tool_result_refs) != len(calls)
            or record.provider_error is not None
            or record.run_id != state.run_id
        ):
            raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")
        expected_tool_calls += len(calls)
    if (
        failed_record.run_id != state.run_id
        or state.workflow != "team_decision"
        or len(state.tool_ledger) != expected_tool_calls
        or any(
            ledger_record.status not in {"completed", "failed"}
            for ledger_record in state.tool_ledger
        )
    ):
        raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")
    if case.max_provider_requests < 1 or case.max_model_turns < 1:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_FAILURE_STATE_INVALID")


def _write_failure_run_manifests(
    run_root: Path,
    case: BusinessCase,
    state: Any,
    thread_id: str,
) -> None:
    """Persist a case manifest and a sanitized guard-counter snapshot."""
    durable_records = list(state.model_executions[:-1])
    infrastructure_evidence = {
        "infrastructure_crossed": bool(durable_records),
        "multi_call_proposal_observed": any(
            len(record.normalized_proposal.get("tool_calls", [])) > 1
            for record in durable_records
        ),
        "completed_tool_calls": sum(
            record.status == "completed" for record in state.tool_ledger
        ),
        "terminal_tool_calls": len(state.tool_ledger),
        "next_model_turn_reached": len(state.model_executions) > 1,
    }
    parent_manifest_path = run_root / "run_manifest.json"
    if parent_manifest_path.is_file():
        parent_manifest = baseline._read_json(parent_manifest_path)
    else:
        parent_manifest = {
            "schema_version": "deepseek-business-run/v1",
            "case_ids": [case.case_id],
            "cases": [case.to_public_dict()],
            "model": MODEL,
            "thinking": "disabled",
            "structured_json": True,
            "semantic_retry_count": 0,
            "execution_order": [case.case_id],
        }
    baseline._write_json(parent_manifest_path, parent_manifest)

    case_root = run_root / case.case_id
    baseline._write_json(
        case_root / "run_manifest.json",
        {
            "schema_version": "deepseek-business-case-run/v1",
            "case_id": case.case_id,
            "workspace_id": case.workspace_id,
            "run_id": state.run_id,
            "thread_id": thread_id,
            "model": MODEL,
            "thinking": "disabled",
            "structured_json": True,
            "semantic_retry_count": 0,
            "execution_mode": "semantic_retry_zero_transport_unknown",
            "provider_requests": len(state.model_executions),
            "model_turns": len(state.model_executions),
            "tool_calls": len(state.tool_ledger),
            "terminal_status": state.status,
            "evidence_mode": "offline_finalize_after_provider_failure",
            "gold_access_mode": "frozen_dataset_hash_only",
            "posthoc_evaluation": "not_created",
            "runtime_evidence": infrastructure_evidence,
        },
    )
    baseline._write_json(
        case_root / "guard_counters.json",
        {
            "schema_version": "deepseek-business-guard-counters/v1",
            "source": "durable_runtime_state",
            "case_id": case.case_id,
            "run_id": state.run_id,
            "logical_requests": len(state.model_executions),
            "model_turns": len(state.model_executions),
            "tool_calls": len(state.tool_ledger),
            "transport_attempts": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
            "transport_retry_count": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
            "counter_status": "PARTIAL_DURABLE_EVIDENCE",
            "unknown_reason": (
                "The original in-process Provider guard did not persist its "
                "transport counters before the unknown Provider outcome."
            ),
            "runtime_evidence": infrastructure_evidence,
        },
    )


def finalize_unsealed_provider_failure(
    *,
    run_root: Path,
    case_id: str,
    thread_id: str,
) -> Path:
    """Seal a failed Provider-boundary run using local artifacts only.

    This path is intentionally offline: it never constructs a Provider
    client, reads a credential, semantically loads/evaluates Gold, or writes a
    post-hoc evaluation. It hash-verifies the frozen dataset to detect drift,
    then turns an interrupted reporter path into an auditable evidence package
    while preserving the honest ``unknown`` transport-counter boundary.
    """
    if case_id not in CASES:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_CASE_NOT_ALLOWED")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise SmokeBlocked("DEEPSEEK_BUSINESS_THREAD_ID_INVALID")
    run_root = Path(run_root).resolve()
    case = CASES[case_id]
    case_root = run_root / case_id
    observed_path = case_root / "observed_summary.json"
    if observed_path.exists():
        raise SmokeBlocked("DEEPSEEK_OBSERVED_SUMMARY_ALREADY_EXISTS")
    source = _verify_case_inputs(case)
    source_before = baseline._file_hashes(source)
    vault = case_root / "vault"
    source_after = baseline._file_hashes(source)
    copy_after = baseline._file_hashes(vault)
    if source_after != source_before or copy_after != source_before:
        raise SmokeBlocked("DEEPSEEK_SYNTHETIC_WORKSPACE_MUTATED")
    state = SQLiteCheckpointer(case_root / "checkpoints").get_latest(thread_id)
    if state is None:
        raise SmokeBlocked("DEEPSEEK_RUNTIME_STATE_UNAVAILABLE")
    _assert_provider_failure_state(case, state)
    trace_manifests = list((case_root / "traces").glob("*/manifest.json"))
    if len(trace_manifests) != 1:
        raise SmokeBlocked("DEEPSEEK_TRACE_MANIFEST_UNAVAILABLE")
    trace_manifest = baseline._read_json(trace_manifests[0])
    if (
        trace_manifest.get("run_id") != state.run_id
        or trace_manifest.get("complete") is not False
        or trace_manifest.get("redaction_policy_version") != "trace-redaction-v1"
    ):
        raise SmokeBlocked("DEEPSEEK_TRACE_MANIFEST_INVALID")

    artifact_store = ModelArtifactStore(case_root / "checkpoints" / "models")
    result = None
    if state.result_ref:
        result_path = case_root / "checkpoints" / state.result_ref
        if result_path.is_file():
            result = baseline._read_json(result_path)
    per_turn: list[dict[str, Any]] = []
    reported_input_tokens = 0
    reported_output_tokens = 0
    reported_total_tokens = 0
    reported_cache_hit_tokens = 0
    reported_cache_miss_tokens = 0
    reported_reasoning_tokens = 0
    durable_response_count = 0
    for record in state.model_executions:
        if not record.request_ref or not record.request_sha256:
            raise SmokeBlocked("DEEPSEEK_DURABLE_MODEL_REQUEST_UNAVAILABLE")
        request_payload = artifact_store.read(
            record.request_ref,
            expected_sha256=record.request_sha256,
        )
        raw_request = request_payload.get("model_request")
        if not isinstance(raw_request, dict):
            raise SmokeBlocked("DEEPSEEK_DURABLE_MODEL_REQUEST_UNAVAILABLE")
        request_sha = hashlib.sha256(
            json.dumps(raw_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        usage = record.usage if isinstance(record.usage, dict) else {}
        metadata = (
            record.provider_metadata
            if isinstance(record.provider_metadata, dict)
            else {}
        )
        response_durable = record.provider_error is None
        input_tokens = (usage.get("input_tokens") or 0) if response_durable else 0
        output_tokens = (usage.get("output_tokens") or 0) if response_durable else 0
        total_tokens = (usage.get("total_tokens") or 0) if response_durable else 0
        cache_hit = (
            (metadata.get("prompt_cache_hit_tokens") or 0)
            if response_durable
            else 0
        )
        cache_miss = (
            metadata.get("prompt_cache_miss_tokens") if response_durable else 0
        )
        if cache_miss is None:
            cache_miss = input_tokens - cache_hit
        reasoning_tokens = (
            (metadata.get("reasoning_tokens") or 0) if response_durable else 0
        )
        if response_durable:
            durable_response_count += 1
            reported_input_tokens += input_tokens
            reported_output_tokens += output_tokens
            reported_total_tokens += total_tokens
            reported_cache_hit_tokens += cache_hit
            reported_cache_miss_tokens += cache_miss
            reported_reasoning_tokens += reasoning_tokens
        per_turn.append(
            {
                "logical_request": record.sequence,
                "reported_input_tokens": input_tokens if response_durable else None,
                "reported_output_tokens": output_tokens if response_durable else None,
                "reported_total_tokens": total_tokens if response_durable else None,
                "reported_cache_hit_tokens": cache_hit if response_durable else None,
                "reported_cache_miss_tokens": cache_miss if response_durable else None,
                "reported_reasoning_tokens": reasoning_tokens if response_durable else None,
                "transport_attempts": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
                "request_sha256": request_sha,
                "status": "response_durable" if response_durable else "provider_error",
            }
        )

    estimated_cost = (
        Decimal(reported_cache_hit_tokens)
        * baseline.PEAK_CACHE_HIT_USD_PER_MILLION
        + Decimal(reported_cache_miss_tokens)
        * baseline.PEAK_CACHE_MISS_USD_PER_MILLION
        + Decimal(reported_output_tokens) * baseline.PEAK_OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)
    if durable_response_count and estimated_cost > baseline.BUDGET.max_cost_usd:
        raise SmokeBlocked("DEEPSEEK_REPORTED_COST_BUDGET_EXCEEDED")

    durable_records = list(state.model_executions[:-1])
    runtime_evidence = {
        "infrastructure_crossed": bool(durable_records),
        "multi_call_proposal_observed": any(
            len(record.normalized_proposal.get("tool_calls", [])) > 1
            for record in durable_records
        ),
        "completed_tool_calls": sum(
            record.status == "completed" for record in state.tool_ledger
        ),
        "terminal_tool_calls": len(state.tool_ledger),
        "next_model_turn_reached": len(state.model_executions) > 1,
    }

    _write_failure_run_manifests(run_root, case, state, thread_id)
    observed = {
        "schema_version": "deepseek-business-observed/v1",
        "case_id": case.case_id,
        "workspace_id": case.workspace_id,
        "model": MODEL,
        "thinking": "disabled",
        "structured_json": True,
        "semantic_retry_count": 0,
        "transport_retry_count": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
        "evidence_mode": "offline_finalize_after_provider_failure",
        "trajectory": baseline._request_observation_summaries(state, artifact_store),
        "tool_calls": [record.to_dict() for record in state.tool_ledger],
        "visible_evidence_refs": [],
        "team_decision_result": None,
        "runtime_evidence": runtime_evidence,
        "runtime": {
            "status": state.status,
            "termination": state.termination.to_dict() if state.termination else None,
            "error": state.error.to_dict() if state.error else None,
            "provider_requests": len(state.model_executions),
            "transport_attempts": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
            "model_turns": len(state.model_executions),
            "max_provider_requests": case.max_provider_requests,
            "max_model_turns": case.max_model_turns,
        },
        "usage": {
            "per_turn": per_turn,
            "reported_input_tokens": reported_input_tokens,
            "reported_output_tokens": reported_output_tokens,
            "reported_total_tokens": reported_total_tokens,
            "reported_cache_hit_tokens": reported_cache_hit_tokens,
            "reported_cache_miss_tokens": reported_cache_miss_tokens,
            "reported_reasoning_tokens": reported_reasoning_tokens,
            "conservative_input_tokens": None,
        },
        "cost": {
            "estimated_peak_usd": (
                str(estimated_cost)
                if durable_response_count
                else "UNAVAILABLE_AFTER_PROVIDER_FAILURE"
            ),
            "hard_ceiling_usd": str(baseline.BUDGET.max_cost_usd),
            "pricing_basis": (
                "official peak rates; durable successful response usage only"
                if durable_response_count
                else "official peak rates; no successful usage to price"
            ),
        },
        "guard": {
            "state": "PROVIDER_ERROR_FROM_DURABLE_STATE",
            "logical_requests": len(state.model_executions),
            "transport_attempts": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
            "transport_retry_count": "UNAVAILABLE_AFTER_PROVIDER_FAILURE",
            "counter_provenance": "durable_runtime_state",
            "budget": {
                **asdict(baseline.BUDGET),
                "max_cost_usd": str(baseline.BUDGET.max_cost_usd),
            },
        },
        "source_unchanged": source_before == source_after == copy_after,
        "gold_accessed": False,
        "gold_access_mode": "frozen_dataset_hash_only",
        "gold_semantic_evaluation": False,
        "posthoc_evaluation": "not_created",
        "runtime_state": state.to_dict(),
        "result": deepcopy(result),
    }
    baseline._seal_observed(case_root, observed)
    return case_root


def _load_gold_after_seal(case_root: Path, case_id: str) -> dict[str, Any]:
    if case_id not in CASES:
        raise SmokeBlocked("DEEPSEEK_BUSINESS_CASE_NOT_ALLOWED")
    observed_path = case_root / "observed_summary.json"
    seal_path = case_root / "observed_summary.seal.json"
    if not observed_path.is_file() or not seal_path.is_file():
        raise SmokeBlocked("DEEPSEEK_EVALUATION_REQUIRES_SEALED_OBSERVED_RUN")
    observed = baseline._read_json(observed_path)
    seal = baseline._read_json(seal_path)
    if (
        observed.get("case_id") != case_id
        or seal.get("sealed") is not True
        or seal.get("gold_available") is not False
        or seal.get("observed_summary_sha256")
        != hashlib.sha256(observed_path.read_bytes()).hexdigest()
    ):
        raise SmokeBlocked("DEEPSEEK_EVALUATION_REQUIRES_SEALED_OBSERVED_RUN")
    dataset_sha = hashlib.sha256(
        DATASET_PATH.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest().upper()
    if dataset_sha != baseline.FROZEN_DATASET_SHA256:
        raise SmokeBlocked("DEEPSEEK_GOLDEN_DATASET_DRIFT")
    for line in DATASET_PATH.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if isinstance(record, dict) and record.get("case_id") == case_id:
            return record
    raise SmokeBlocked("DEEPSEEK_GOLD_CASE_UNAVAILABLE")


def _forbidden_claim_hits(actual: object, gold: Mapping[str, Any]) -> list[str]:
    text = json.dumps(actual, ensure_ascii=False, sort_keys=True).casefold()
    return [
        claim
        for claim in gold.get("forbidden_claims", [])
        if isinstance(claim, str) and claim.casefold() in text
    ]


def evaluate_sealed_case(case_root: Path, case_id: str) -> dict[str, Any]:
    gold = _load_gold_after_seal(case_root, case_id)
    observed = baseline._read_json(case_root / "observed_summary.json")
    trajectory = observed.get("trajectory", [])
    has_final = any(
        isinstance(turn, dict)
        and isinstance(turn.get("action"), dict)
        and turn["action"].get("kind") == "final"
        for turn in trajectory
    )
    actual = observed.get("team_decision_result")
    runtime = observed.get("runtime", {})
    infrastructure_pass = has_final and runtime.get("status") == "completed"
    if not infrastructure_pass:
        evaluation = {
            "schema_version": "deepseek-business-posthoc/v1",
            "case_id": case_id,
            "infrastructure": "FAIL",
            "contract": "N/E",
            "grounding": "N/E",
            "decision": {
                "status": "N/E",
                "status_match": None,
                "value_exact_match": None,
            },
            "scope": {
                "status": "N/E",
                "forbidden_claim_hits": [],
            },
            "uncertainty": {
                "status": "N/E",
                "unknown_fields": None,
            },
            "infrastructure_error": deepcopy(runtime.get("error")),
            "gold_accessed": True,
            "gold_dataset_sha256": baseline.FROZEN_DATASET_SHA256,
            "overall": "BLOCKED_INFRASTRUCTURE",
        }
        baseline._write_json(case_root / "posthoc_evaluation.json", evaluation)
        return evaluation
    contract_pass = infrastructure_pass and isinstance(actual, dict)
    visible_refs = set(observed.get("visible_evidence_refs", []))
    claim_refs = baseline._claim_refs(actual) if isinstance(actual, dict) else []
    grounding_pass = bool(claim_refs) and set(claim_refs) <= visible_refs
    actual_decision = actual.get("decision") if isinstance(actual, dict) else None
    expected_decision = gold.get("expected_decision")
    decision_status_match = (
        isinstance(actual_decision, dict)
        and isinstance(expected_decision, dict)
        and actual_decision.get("status") == expected_decision.get("status")
    )
    decision_value_exact_match = (
        isinstance(actual_decision, dict)
        and isinstance(expected_decision, dict)
        and actual_decision.get("value") == expected_decision.get("value")
    )
    uncertainty = actual.get("uncertainty") if isinstance(actual, dict) else None
    evaluation = {
        "schema_version": "deepseek-business-posthoc/v1",
        "case_id": case_id,
        "infrastructure": "PASS" if infrastructure_pass else "FAIL",
        "contract": "PASS" if contract_pass else "FAIL",
        "grounding": "PASS" if grounding_pass else "FAIL",
        "decision": {
            "status_match": decision_status_match,
            "value_exact_match": decision_value_exact_match,
        },
        "scope": {
            "forbidden_claim_hits": _forbidden_claim_hits(actual, gold),
        },
        "uncertainty": {
            "status": uncertainty.get("status") if isinstance(uncertainty, dict) else None,
            "unknown_fields": (
                uncertainty.get("unknown_fields")
                if isinstance(uncertainty, dict)
                else None
            ),
        },
        "gold_accessed": True,
        "gold_dataset_sha256": baseline.FROZEN_DATASET_SHA256,
        "overall": "POSTHOC_REVIEW_REQUIRED",
    }
    baseline._write_json(case_root / "posthoc_evaluation.json", evaluation)
    return evaluation


def _posthoc_evaluate_run(run_root: Path) -> None:
    evaluations = [
        evaluate_sealed_case(run_root / case_id, case_id)
        for case_id in CASES
    ]
    baseline._write_json(
        run_root / "evaluation_summary.json",
        {
            "schema_version": "deepseek-business-evaluation-summary/v1",
            "case_ids": list(CASES),
            "model": MODEL,
            "thinking": "disabled",
            "semantic_retry_count": 0,
            "evaluations": evaluations,
        },
    )


def run_business_evaluation() -> Path:
    settings = baseline.load_real_settings()
    run_root = (ARTIFACT_ROOT / f"run-{uuid4().hex}").resolve()
    run_root.mkdir(parents=True)
    baseline._write_json(
        run_root / "run_manifest.json",
        {
            "schema_version": "deepseek-business-run/v1",
            "case_ids": list(CASES),
            "cases": [case.to_public_dict() for case in CASES.values()],
            "model": MODEL,
            "thinking": "disabled",
            "structured_json": True,
            "semantic_retry_count": 0,
            "execution_order": list(CASES),
        },
    )
    for case in CASES.values():
        _run_case(run_root=run_root, case=case, api_key=settings.api_key)
    _posthoc_evaluate_run(run_root)
    baseline._scan_for_credential(run_root, settings.api_key)
    return run_root


__all__ = [
    "CASES",
    "MODEL",
    "OPT_IN_ENV",
    "SmokeBlocked",
    "evaluate_sealed_case",
    "finalize_unsealed_provider_failure",
    "run_business_evaluation",
]
