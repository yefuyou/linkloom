"""Frozen G3.2 held-out profile and global cost-aware retry gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .g31_fixed50 import G3RunProfile


QA_ID_PREFIX = "factconsolidation_sh_6k_no"
QA_START_INDEX = 50
QUESTION_COUNT = 50
METHODS = ("Flat Retrieval", "LinkLoom Temporal Memory")
HARD_COST_CAP_USD = 1.00
INPUT_USD_PER_MILLION = 0.75
OUTPUT_USD_PER_MILLION = 3.75
OUTPUT_TOKEN_CAP = 512
G32_HELDOUT_PROFILE = G3RunProfile(
    name="G3_2_HELDOUT_QA50_QA99",
    question_count=QUESTION_COUNT,
    max_generation_attempts=QUESTION_COUNT * len(METHODS) * 3,
    hard_cost_cap_usd=HARD_COST_CAP_USD,
    artifact_prefix="g32-heldout50-",
    stage="G3_2_HELDOUT_FIXED_FIFTY",
)

EXPECTED_FROZEN = {
    "dataset_revision": "7ea066982b140a19337e17e60d45d4076e042faf",
    "dataset_sha256": "24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45",
    "subset_hash": "383bb65d206e1d1b5d8571cf0650f678e30b702ee931d95d31afd54c94b8a2b9",
    "subset_manifest_sha256": "2adb826dd4eff3daa92cd5d61dee24bb90f5237f1792cef55c1739c59c7e33c3",
    "adapter_fingerprint": "65c9ce6acbca1d98fe16d3b793c032c0ef5ecdbc7662775872305b6bbf578074",
    "temporal_memory_fingerprint": "1221a8bbde951484947a563f175b78f16a55a75489fbee8629032de85ef6882c",
    "retrieval_fingerprint": "a8b2210329d0811e8614acb51e92477a060e9b84bf6090fdaba3e18914b7fde3",
    "scoring_fingerprint": "adc6a2a986ea240148680f5dc0a01308cd4c48dcf56739d1e233c30e4974fab2",
    "prompt_fingerprint": "7b816f44a1c60e5ed15c66a905d69f3b4ff5fc40af6dd5e17855eb225e174468",
    "gemini_config_fingerprint": "79c45d100d75451ac1d88260ca4ce580d4917c7ebe7dbc711ec28676ceeb914a",
}


class G32IntegrityError(ValueError):
    """The frozen G3.2 held-out packet no longer matches its approved values."""


def heldout_qa_ids() -> list[str]:
    return [
        f"{QA_ID_PREFIX}{index}"
        for index in range(QA_START_INDEX, QA_START_INDEX + QUESTION_COUNT)
    ]


def build_execution_order(qa_ids: Sequence[str]) -> list[dict[str, Any]]:
    expected = heldout_qa_ids()
    if list(qa_ids) != expected:
        raise G32IntegrityError("held-out execution must be exactly QA50–QA99 in order")
    return [
        {"sequence": sequence, "qa_id": qa_id, "method": method}
        for sequence, (qa_id, method) in enumerate(
            ((qa_id, method) for qa_id in expected for method in METHODS),
            start=1,
        )
    ]


def validate_frozen_preflight(
    frozen: Mapping[str, Any],
    recomputed: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless the on-disk packet equals a fresh Gold-blind build."""
    if frozen.get("status") != "READY" or recomputed.get("status") != "READY":
        raise G32IntegrityError("held-out preflight is not READY")
    frozen_heldout = frozen.get("heldout_preflight")
    actual_heldout = recomputed.get("heldout_preflight")
    if not isinstance(frozen_heldout, Mapping) or not isinstance(actual_heldout, Mapping):
        raise G32IntegrityError("held-out preflight payload is missing")
    frozen_manifest = frozen_heldout.get("subset_manifest")
    actual_manifest = actual_heldout.get("subset_manifest")
    if not isinstance(frozen_manifest, Mapping) or not isinstance(actual_manifest, Mapping):
        raise G32IntegrityError("held-out subset manifest is missing")
    if dict(frozen_manifest) != dict(actual_manifest):
        raise G32IntegrityError("fresh held-out subset manifest differs from frozen manifest")

    expected_values = dict(EXPECTED_FROZEN)
    for field in ("dataset_revision", "dataset_sha256"):
        if frozen_heldout.get(field) != expected_values[field]:
            raise G32IntegrityError(f"frozen {field} mismatch")
    if frozen_manifest.get("subset_hash") != expected_values["subset_hash"]:
        raise G32IntegrityError("frozen subset_hash mismatch")
    for key, expected_value in EXPECTED_FROZEN.items():
        if key.endswith("_fingerprint") and frozen_manifest.get("fingerprints", {}).get(key) != expected_value:
            raise G32IntegrityError(f"frozen {key} mismatch")

    qa_ids = frozen_manifest.get("qa_ids")
    if qa_ids != heldout_qa_ids() or frozen_heldout.get("qa_ids") != heldout_qa_ids():
        raise G32IntegrityError("held-out QA IDs are not exactly QA50–QA99")
    gold_isolation = frozen.get("gold_isolation")
    if not isinstance(gold_isolation, Mapping) or gold_isolation.get("gold_columns_read") is not False:
        raise G32IntegrityError("Gold-isolation assertion failed")
    provider_calls = frozen.get("provider_calls")
    if provider_calls != {"generation": 0, "count_tokens": 0, "retries": 0}:
        raise G32IntegrityError("qualification artifact records Provider calls")
    selection = frozen.get("heldout_selection")
    if not isinstance(selection, Mapping) or selection.get("previously_used_overlap") != []:
        raise G32IntegrityError("held-out QA IDs overlap prior use")

    plan = frozen_heldout.get("execution_plan")
    expected_plan = build_execution_order(heldout_qa_ids())
    if not isinstance(plan, list) or len(plan) != len(expected_plan):
        raise G32IntegrityError("held-out execution plan is incomplete")
    for row, expected in zip(plan, expected_plan, strict=True):
        if any(row.get(key) != expected[key] for key in ("sequence", "qa_id", "method")):
            raise G32IntegrityError("held-out execution plan is not interleaved and ordered")
        if not all(
            isinstance(row.get(key), str) and len(row[key]) == 64
            for key in ("generation_request_sha256", "count_tokens_request_sha256", "context_sha256")
        ):
            raise G32IntegrityError("held-out request/context hashes are incomplete")
    continuation = frozen_heldout.get("continuation_metadata")
    if not isinstance(continuation, Mapping) or continuation.get("subset_manifest_sha256") != expected_values["subset_manifest_sha256"]:
        raise G32IntegrityError("continuation metadata does not bind the frozen manifest")

    return {
        "subset_manifest": dict(frozen_manifest),
        "execution_plan": [dict(row) for row in plan],
        "fingerprints": dict(frozen_manifest["fingerprints"]),
        "dataset_revision": frozen_heldout["dataset_revision"],
        "dataset_sha256": frozen_heldout["dataset_sha256"],
        "subset_manifest_sha256": frozen_heldout["continuation_metadata"]["subset_manifest_sha256"],
        "qa_ids": list(qa_ids),
        "static_input_bounds": [row["static_input_token_upper_bound"] for row in plan],
        "cost_estimate": dict(frozen_heldout["cost_estimate"]),
    }


def request_cost_upper_bound_usd(static_input_bound: int) -> float:
    if isinstance(static_input_bound, bool) or not isinstance(static_input_bound, int) or static_input_bound < 0:
        raise ValueError("static input bound must be a non-negative integer")
    return (
        2 * static_input_bound * INPUT_USD_PER_MILLION
        + OUTPUT_TOKEN_CAP * OUTPUT_USD_PER_MILLION
    ) / 1_000_000


def count_tokens_request_upper_bound(generation_request: Mapping[str, Any]) -> tuple[str, str, int]:
    """Use the already-qualified G3 countTokens body shape and byte bound."""
    config = generation_request["config"]
    body = {
        "generateContentRequest": {
            "model": f"models/{generation_request['model']}",
            "contents": generation_request["contents"],
            "systemInstruction": {"parts": [{"text": config["system_instruction"]}]},
            "generationConfig": {
                "temperature": config["temperature"],
                "maxOutputTokens": config["max_output_tokens"],
            },
        }
    }
    import hashlib
    import json

    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    generation_hash = hashlib.sha256(
        json.dumps(generation_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    count_tokens_hash = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return generation_hash, count_tokens_hash, len(serialized.encode("utf-8")) + 512


def can_schedule_retry(
    *,
    spent_or_reserved_cost_usd: float,
    retry_worst_case_cost_usd: float,
    remaining_base_execution_cost_usd: float,
    hard_cap_usd: float = HARD_COST_CAP_USD,
) -> bool:
    values = (
        spent_or_reserved_cost_usd,
        retry_worst_case_cost_usd,
        remaining_base_execution_cost_usd,
        hard_cap_usd,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("cost gate values must be numeric")
    if any(value < 0 for value in values) or hard_cap_usd <= 0:
        raise ValueError("cost gate values must be non-negative and cap must be positive")
    return (
        spent_or_reserved_cost_usd
        + retry_worst_case_cost_usd
        + remaining_base_execution_cost_usd
        < hard_cap_usd
    )


def can_schedule_base_execution(
    *,
    spent_or_reserved_cost_usd: float,
    current_and_remaining_base_cost_usd: float,
    hard_cap_usd: float = HARD_COST_CAP_USD,
) -> bool:
    """Reserve the current base attempt and every later base attempt before starting."""
    values = (
        spent_or_reserved_cost_usd,
        current_and_remaining_base_cost_usd,
        hard_cap_usd,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("cost gate values must be numeric")
    if any(value < 0 for value in values) or hard_cap_usd <= 0:
        raise ValueError("cost gate values must be non-negative and cap must be positive")
    return spent_or_reserved_cost_usd + current_and_remaining_base_cost_usd < hard_cap_usd
