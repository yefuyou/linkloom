"""Offline freeze and cost helpers for a clean G3.1 held-out QA subset."""

from __future__ import annotations

import ast
import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


QA_ID_PREFIX = "factconsolidation_sh_6k_no"
HELD_OUT_METHODS = ("Flat Retrieval", "LinkLoom Temporal Memory")
REQUIRED_FINGERPRINTS = (
    "adapter_fingerprint",
    "temporal_memory_fingerprint",
    "retrieval_fingerprint",
    "scoring_fingerprint",
    "prompt_fingerprint",
    "gemini_config_fingerprint",
)


class HeldOutPreflightError(ValueError):
    """A held-out subset could not be frozen without violating its invariants."""


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_clean_contiguous_subset(
    official_qa_ids: Sequence[str],
    previously_used_qa_ids: Sequence[str],
    *,
    start_index: int = 50,
    requested_count: int = 50,
) -> dict[str, Any]:
    """Select a stable official-order interval; never skip/reuse IDs to fill it."""
    if isinstance(start_index, bool) or not isinstance(start_index, int) or start_index < 0:
        raise HeldOutPreflightError("start_index must be a non-negative integer")
    if isinstance(requested_count, bool) or not isinstance(requested_count, int) or requested_count <= 0:
        raise HeldOutPreflightError("requested_count must be a positive integer")
    if len(set(official_qa_ids)) != len(official_qa_ids):
        raise HeldOutPreflightError("official QA IDs contain duplicates")

    available_ids = list(official_qa_ids[start_index:])
    selected_ids = available_ids[:requested_count]
    used = set(previously_used_qa_ids)
    overlap = [qa_id for qa_id in selected_ids if qa_id in used]
    status = "READY" if len(selected_ids) == requested_count and not overlap else "NOT_READY"
    return {
        "status": status,
        "selection_start_index": start_index,
        "requested_count": requested_count,
        "available_count_after_start": len(available_ids),
        "selected_count": len(selected_ids),
        "selected_qa_ids": selected_ids,
        "previously_used_overlap": overlap,
        "remaining_unseen_count_after_start": sum(qa_id not in used for qa_id in available_ids),
        "selection_rule": "contiguous official QA order; fail closed on any prior-use overlap",
    }


def build_method_case_plan(qa_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not qa_ids or len(set(qa_ids)) != len(qa_ids):
        raise HeldOutPreflightError("held-out QA IDs must be non-empty and unique")
    return [
        {"sequence": sequence, "qa_id": qa_id, "method": method}
        for sequence, (qa_id, method) in enumerate(
            ((qa_id, method) for qa_id in qa_ids for method in HELD_OUT_METHODS),
            start=1,
        )
    ]


def file_bundle_fingerprint(root: str | Path, relative_paths: Sequence[str]) -> dict[str, Any]:
    root_path = Path(root).resolve()
    entries: list[dict[str, str]] = []
    for relative_path in sorted(set(relative_paths)):
        path = (root_path / relative_path).resolve()
        if root_path not in path.parents or not path.is_file():
            raise HeldOutPreflightError(f"fingerprint input is missing or outside root: {relative_path}")
        entries.append(
            {
                "path": relative_path.replace("\\", "/"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    if not entries:
        raise HeldOutPreflightError("fingerprint bundle must include at least one source file")
    return {"sha256": canonical_sha256(entries), "files": entries}


def python_symbol_fingerprint(
    root: str | Path,
    relative_path: str,
    symbol_names: Sequence[str],
) -> dict[str, Any]:
    """Hash only named Python functions/constants, for separate prompt/scoring IDs."""
    source_path = Path(root).resolve() / relative_path
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    wanted = set(symbol_names)
    found: dict[str, str] = {}
    for node in tree.body:
        name: str | None = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            name = next((target.id for target in targets if isinstance(target, ast.Name)), None)
        if name in wanted:
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                found[name] = segment
    missing = sorted(wanted - found.keys())
    if missing:
        raise HeldOutPreflightError(f"fingerprint symbols missing from {relative_path}: {missing}")
    ordered = [{"name": name, "source": found[name]} for name in symbol_names]
    return {
        "sha256": canonical_sha256(ordered),
        "source": relative_path.replace("\\", "/"),
        "symbols": list(symbol_names),
    }


def estimate_static_cost(
    static_input_bounds: Sequence[int],
    *,
    max_attempts_per_method_case: int = 3,
    per_request_input_cap: int = 10_000,
    per_request_output_cap: int = 512,
    hard_cost_cap_usd: float = 1.00,
    input_usd_per_million: float = 0.75,
    output_usd_per_million: float = 3.75,
) -> dict[str, Any]:
    """Price countTokens + generation inputs conservatively at the static bound."""
    if not static_input_bounds:
        raise HeldOutPreflightError("cost estimate requires at least one method-case")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in static_input_bounds
    ):
        raise HeldOutPreflightError("static input bounds must be non-negative integers")
    if max_attempts_per_method_case <= 0:
        raise HeldOutPreflightError("max_attempts_per_method_case must be positive")
    over_cap = [value for value in static_input_bounds if value > per_request_input_cap]
    if over_cap:
        raise HeldOutPreflightError("a static input bound exceeds the frozen per-request cap")

    one_attempt_cost = sum(
        (
            2 * input_bound * input_usd_per_million
            + per_request_output_cap * output_usd_per_million
        )
        / 1_000_000
        for input_bound in static_input_bounds
    )
    all_attempts_cost = one_attempt_cost * max_attempts_per_method_case
    return {
        "currency": "USD",
        "pricing_snapshot_date": "2026-09-25",
        "method_case_count": len(static_input_bounds),
        "input_bound_min": min(static_input_bounds),
        "input_bound_median": statistics.median(static_input_bounds),
        "input_bound_max": max(static_input_bounds),
        "static_estimator": "UTF-8 byte length of serialized countTokens request + 512-token structural reserve",
        "count_tokens_input_charged_conservatively": True,
        "per_request_input_cap": per_request_input_cap,
        "per_request_output_cap": per_request_output_cap,
        "max_attempts_per_method_case": max_attempts_per_method_case,
        "input_usd_per_million": input_usd_per_million,
        "output_usd_per_million": output_usd_per_million,
        "one_attempt_total_cost_upper_bound_usd": round(one_attempt_cost, 9),
        "all_allowed_attempts_cost_upper_bound_usd": round(all_attempts_cost, 9),
        "hard_cost_cap_usd": hard_cost_cap_usd,
        "one_attempt_within_hard_cap": one_attempt_cost <= hard_cost_cap_usd,
        "all_attempts_within_hard_cap": all_attempts_cost <= hard_cost_cap_usd,
        "dynamic_cost_guard_required": all_attempts_cost > hard_cost_cap_usd,
    }


def build_subset_manifest(
    *,
    dataset_revision: str,
    dataset_sha256: str,
    dataset_split: str,
    dataset_configuration: str,
    case_id: str,
    workspace_id: str,
    qa_ids: Sequence[str],
    question_sha256_by_id: Mapping[str, str],
    fingerprints: Mapping[str, str],
    execution_plan: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not qa_ids or len(set(qa_ids)) != len(qa_ids):
        raise HeldOutPreflightError("manifest QA IDs must be non-empty and unique")
    if set(question_sha256_by_id) != set(qa_ids):
        raise HeldOutPreflightError("question hashes must exactly match held-out QA IDs")
    missing = [key for key in REQUIRED_FINGERPRINTS if not fingerprints.get(key)]
    if missing:
        raise HeldOutPreflightError(f"implementation fingerprints are incomplete: {missing}")

    expected_plan = build_method_case_plan(qa_ids)
    if len(execution_plan) != len(expected_plan):
        raise HeldOutPreflightError("execution plan does not contain exactly two methods per QA")
    for actual, expected in zip(execution_plan, expected_plan, strict=True):
        for field in ("sequence", "qa_id", "method"):
            if actual.get(field) != expected[field]:
                raise HeldOutPreflightError("execution plan differs from frozen QA/method order")

    subset_identity = {
        "dataset_revision": dataset_revision,
        "dataset_sha256": dataset_sha256,
        "dataset_split": dataset_split,
        "dataset_configuration": dataset_configuration,
        "qa_ids": list(qa_ids),
        "question_sha256_by_id": dict(sorted(question_sha256_by_id.items())),
    }
    subset_hash = canonical_sha256(subset_identity)
    manifest = {
        "schema_version": "memoryagentbench-g31-heldout/v1",
        "stage": "G3_1_CLEAN_HELD_OUT_PREFLIGHT",
        "classification": "HELD_OUT_DEVELOPMENT_EVALUATION_CANDIDATE",
        "dataset_revision": dataset_revision,
        "dataset_sha256": dataset_sha256,
        "dataset_split": dataset_split,
        "dataset_configuration": dataset_configuration,
        "selection": "official QA order, starting at zero-based QA index 50; no reuse or gap filling",
        "question_count": len(qa_ids),
        "qa_ids": list(qa_ids),
        "question_sha256_by_id": dict(sorted(question_sha256_by_id.items())),
        "case_id": case_id,
        "workspace_id": workspace_id,
        "gold_columns_read": False,
        "input_columns": ["context", "questions", "metadata.qa_pair_ids", "metadata.source"],
        "methods": list(HELD_OUT_METHODS),
        "method_case_count": len(execution_plan),
        "subset_hash": subset_hash,
        "execution_plan_sha256": canonical_sha256(list(execution_plan)),
        "fingerprints": {key: fingerprints[key] for key in REQUIRED_FINGERPRINTS},
    }
    manifest["subset_manifest_sha256"] = canonical_sha256(manifest)
    return manifest
