"""Frozen G3.1 fixed-50 execution profile and offline safety calculations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence


QA_ID_PREFIX = "factconsolidation_sh_6k_no"
METHODS = ("Flat Retrieval", "LinkLoom Temporal Memory")
INPUT_USD_PER_MILLION = 0.75
OUTPUT_USD_PER_MILLION = 3.75


@dataclass(frozen=True, slots=True)
class G3RunProfile:
    name: str
    question_count: int
    max_generation_attempts: int
    hard_cost_cap_usd: float
    artifact_prefix: str
    stage: str

    @property
    def method_case_count(self) -> int:
        return self.question_count * len(METHODS)


G3_FIRST10_PROFILE = G3RunProfile(
    name="G3_FIRST_TEN_PILOT",
    question_count=10,
    max_generation_attempts=30,
    hard_cost_cap_usd=0.50,
    artifact_prefix="g3-first10-",
    stage="G3_FIRST_TEN_PILOT",
)

G31_FIXED50_PROFILE = G3RunProfile(
    name="G3_1_FIXED_FIFTY",
    question_count=50,
    max_generation_attempts=300,
    hard_cost_cap_usd=1.00,
    artifact_prefix="g3-first50-",
    stage="G3_1_FIXED_FIFTY",
)


class G31ProfileError(ValueError):
    """An invalid fixed-subset, budget, or recovery state."""


def fixed_qa_ids(question_count: int = 50) -> list[str]:
    if isinstance(question_count, bool) or not isinstance(question_count, int) or question_count < 1:
        raise G31ProfileError("question_count must be a positive integer")
    return [f"{QA_ID_PREFIX}{index}" for index in range(question_count)]


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_subset_manifest(
    *,
    qa_ids: Sequence[str],
    dataset_revision: str,
    dataset_sha256: str,
    split: str,
    configuration: str,
    case_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    expected_ids = fixed_qa_ids(50)
    if list(qa_ids) != expected_ids:
        raise G31ProfileError("subset must be the deterministic official first 50 QA IDs")
    if not all(isinstance(item, str) and item.strip() for item in (
        dataset_revision, dataset_sha256, split, configuration, case_id, workspace_id
    )):
        raise G31ProfileError("dataset and case identity fields must be non-empty strings")
    if len(dataset_sha256) != 64 or any(char not in "0123456789abcdef" for char in dataset_sha256.casefold()):
        raise G31ProfileError("dataset_sha256 must be a 64-character hexadecimal digest")
    return {
        "schema_version": "memoryagentbench-g31-subset/v1",
        "stage": "G3_1_FIXED_FIFTY",
        "dataset_revision": dataset_revision,
        "dataset_sha256": dataset_sha256,
        "dataset_split": split,
        "dataset_configuration": configuration,
        "selection": "official QA order; fixed first 50",
        "question_count": 50,
        "qa_ids": list(expected_ids),
        "includes_original_first_ten": True,
        "case_id": case_id,
        "workspace_id": workspace_id,
    }


def build_execution_order(qa_ids: Sequence[str]) -> list[dict[str, Any]]:
    expected_ids = fixed_qa_ids(50)
    if list(qa_ids) != expected_ids:
        raise G31ProfileError("execution plan must use the frozen first-50 QA order")
    return [
        {"sequence": sequence, "qa_id": qa_id, "method": method}
        for sequence, (qa_id, method) in enumerate(
            ((qa_id, method) for qa_id in expected_ids for method in METHODS),
            start=1,
        )
    ]


def estimate_cost_from_pilot(
    *,
    pilot_generation_requests: int,
    pilot_count_tokens_requests: int,
    pilot_preflight_input_tokens: int,
    pilot_generation_input_tokens: int,
    pilot_output_tokens: int,
    pilot_max_input_tokens: int,
    question_count: int = 50,
    max_attempts_per_method_case: int = 3,
    per_request_input_cap: int = 10_000,
    per_request_output_cap: int = 512,
    hard_cost_cap_usd: float = 1.00,
) -> dict[str, Any]:
    """Estimate costs from observed pilot usage and retain a contractual bound.

    The pilot-anchored upper estimate uses the largest input observed in the
    sealed pilot and the unchanged maximum output cap. Runtime cost guards still
    enforce the hard cap against actual countTokens results before generation.
    """
    integer_values = (
        pilot_generation_requests,
        pilot_count_tokens_requests,
        pilot_preflight_input_tokens,
        pilot_generation_input_tokens,
        pilot_output_tokens,
        pilot_max_input_tokens,
        question_count,
        max_attempts_per_method_case,
        per_request_input_cap,
        per_request_output_cap,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integer_values):
        raise G31ProfileError("usage and token bounds must be non-negative integers")
    if pilot_generation_requests <= 0 or pilot_count_tokens_requests <= 0 or question_count <= 0:
        raise G31ProfileError("pilot and planned request counts must be positive")
    if max_attempts_per_method_case < 1 or pilot_max_input_tokens > per_request_input_cap:
        raise G31ProfileError("pilot token maximum exceeds the frozen per-request cap")
    if isinstance(hard_cost_cap_usd, bool) or not isinstance(hard_cost_cap_usd, (int, float)) or hard_cost_cap_usd <= 0:
        raise G31ProfileError("hard cost cap must be positive")

    method_cases = question_count * len(METHODS)
    pilot_cost = (
        (pilot_preflight_input_tokens + pilot_generation_input_tokens)
        * INPUT_USD_PER_MILLION
        + pilot_output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    expected_base = pilot_cost * method_cases / pilot_generation_requests

    observed_input_attempt_cost = (
        2 * pilot_max_input_tokens * INPUT_USD_PER_MILLION
        + per_request_output_cap * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    conservative_base = method_cases * observed_input_attempt_cost
    retry_attempt_reserve_count = method_cases * (max_attempts_per_method_case - 1)
    retry_reserve = retry_attempt_reserve_count * observed_input_attempt_cost
    pilot_anchored_total = conservative_base + retry_reserve

    contractual_total_attempts = method_cases * max_attempts_per_method_case
    contractual_worst_case = (
        contractual_total_attempts
        * (2 * per_request_input_cap * INPUT_USD_PER_MILLION
           + per_request_output_cap * OUTPUT_USD_PER_MILLION)
        / 1_000_000
    )
    return {
        "currency": "USD",
        "pricing_snapshot_date": "2026-09-25",
        "pilot_source_requests": {
            "generation": pilot_generation_requests,
            "count_tokens": pilot_count_tokens_requests,
        },
        "pilot_observed_usage": {
            "count_tokens_input_tokens": pilot_preflight_input_tokens,
            "generation_input_tokens": pilot_generation_input_tokens,
            "generation_output_tokens": pilot_output_tokens,
            "max_input_tokens_per_request": pilot_max_input_tokens,
        },
        "planned_method_case_executions": method_cases,
        "expected_base_cost_usd": round(expected_base, 9),
        "conservative_base_upper_bound_usd": round(conservative_base, 9),
        "retry_reserve": {
            "additional_attempts": retry_attempt_reserve_count,
            "assumption": "up to two retries per method-case, priced at the largest input observed in the sealed pilot and the unchanged output cap",
            "cost_upper_bound_usd": round(retry_reserve, 9),
        },
        "pilot_anchored_conservative_total_usd": round(pilot_anchored_total, 9),
        "contractual_worst_case_at_request_caps_usd": round(contractual_worst_case, 9),
        "hard_cost_cap_usd": float(hard_cost_cap_usd),
        "dynamic_hard_cap_enforced_before_each_count_tokens_and_generation": True,
        "pilot_anchored_estimate_within_hard_cap": pilot_anchored_total <= hard_cost_cap_usd,
        "contractual_worst_case_exceeds_hard_cap": contractual_worst_case > hard_cost_cap_usd,
    }


def classify_recovery_state(
    execution_plan: Sequence[Mapping[str, Any]],
    journal_events: Sequence[Mapping[str, Any]],
    *,
    provider_output_sequences: set[int],
    sealed_sequences: set[int],
) -> list[dict[str, Any]]:
    """Classify each sequence without making any Provider call or retry decision."""
    expected = {int(item["sequence"]): (str(item["qa_id"]), str(item["method"])) for item in execution_plan}
    sequence_by_identity = {identity: sequence for sequence, identity in expected.items()}
    if set(provider_output_sequences) - expected.keys() or set(sealed_sequences) - expected.keys():
        raise G31ProfileError("recovery artifacts contain an unknown method-case sequence")
    by_sequence: dict[int, list[Mapping[str, Any]]] = {sequence: [] for sequence in expected}
    for event in journal_events:
        sequence = event.get("sequence")
        if not (isinstance(sequence, int) and not isinstance(sequence, bool) and sequence in by_sequence):
            identity = (str(event.get("qa_id", "")), str(event.get("method", "")))
            sequence = sequence_by_identity.get(identity)
        if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence in by_sequence:
            qa_id, method = expected[sequence]
            if event.get("qa_id") not in {None, qa_id} or event.get("method") not in {None, method}:
                raise G31ProfileError("journal identity does not match the frozen execution plan")
            by_sequence[sequence].append(event)

    recovery: list[dict[str, Any]] = []
    for sequence, (qa_id, method) in expected.items():
        events = by_sequence[sequence]
        output_exists = sequence in provider_output_sequences
        sealed = sequence in sealed_sequences
        started = any(item.get("event") == "CASE_METHOD_STARTED" for item in events)
        completed = any(item.get("event") == "CASE_METHOD_COMPLETED" for item in events)
        provider_started = any(item.get("event") == "PROVIDER_ATTEMPT_STARTED" for item in events)
        if sealed:
            state = "SEALED"
        elif output_exists:
            state = (
                "CORRUPT"
                if not started
                else "SCORE_PENDING"
                if completed
                else "OUTPUT_PERSISTED_PENDING_COMPLETION"
            )
        elif completed:
            state = "CORRUPT"
        elif provider_started:
            state = "UNKNOWN_PROVIDER_OUTCOME"
        elif started:
            state = "SAFE_TO_RESUME"
        else:
            state = "UNSTARTED"
        recovery.append({
            "sequence": sequence,
            "qa_id": qa_id,
            "method": method,
            "state": state,
        })
    return recovery
