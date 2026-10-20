"""Pure offline checks for G3.1 fixed-50 analysis and continuation closure."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


REQUIRED_CONTINUATION_FINGERPRINTS = (
    "dataset_revision",
    "subset_hash",
    "temporal_memory_fingerprint",
    "retrieval_fingerprint",
    "prompt_fingerprint",
    "gemini_config_fingerprint",
    "scoring_fingerprint",
)
_TERMINAL_SEMANTIC_STATUSES = {"PASS", "FAIL"}
_KNOWN_SEMANTIC_STATUSES = _TERMINAL_SEMANTIC_STATUSES | {"NOT_EVALUATED"}


class G31ClosureError(ValueError):
    """A fixed-50 analysis, continuation, or merge invariant was violated."""


def calculate_token_estimator_metrics(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare exact countTokens, provider usage, and a static token bound."""
    if not observations:
        raise G31ClosureError("token analysis requires at least one observation")

    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(observations):
        case_id = row.get("case_id", str(index))
        count_tokens = _non_negative_int(row.get("count_tokens"), "count_tokens")
        actual = _non_negative_int(row.get("provider_actual"), "provider_actual")
        estimate = _non_negative_int(row.get("static_estimate"), "static_estimate")
        if actual == 0:
            raise G31ClosureError("provider_actual must be positive for ratio metrics")
        normalized.append(
            {
                "case_id": str(case_id),
                "count_tokens": count_tokens,
                "provider_actual": actual,
                "static_estimate": estimate,
                "margin": estimate - actual,
                "ratio": estimate / actual,
            }
        )

    margins = sorted(row["margin"] for row in normalized)
    ratios = [row["ratio"] for row in normalized]
    exact_matches = sum(
        row["count_tokens"] == row["provider_actual"] for row in normalized
    )
    covered = sum(row["static_estimate"] >= row["provider_actual"] for row in normalized)
    underestimation_ids = [
        row["case_id"]
        for row in normalized
        if row["static_estimate"] < row["provider_actual"]
    ]
    p95_index = max(0, math.ceil(0.95 * len(margins)) - 1)
    return {
        "case_count": len(normalized),
        "count_tokens_exact_matches": exact_matches,
        "count_tokens_exact_match_rate": exact_matches / len(normalized),
        "static_coverage_count": covered,
        "static_coverage_rate": covered / len(normalized),
        "margin_tokens": {
            "min": margins[0],
            "median": statistics.median(margins),
            "p95": margins[p95_index],
            "max": margins[-1],
        },
        "max_overestimate_ratio": max(ratios),
        "underestimation_case_ids": underestimation_ids,
    }


def derive_pending_method_cases(
    expected_order: Sequence[Mapping[str, Any]],
    scored_results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return method-cases without a terminal score, in frozen manifest order.

    A sealed ``NOT_EVALUATED`` record remains an immutable attempt artifact but
    does not count as a completed benchmark outcome.
    """
    by_identity = _expected_by_identity(expected_order)
    seen: set[tuple[str, str]] = set()
    terminal: set[tuple[str, str]] = set()
    for row in scored_results:
        identity = _row_identity(row)
        if identity not in by_identity:
            raise G31ClosureError("result contains an unknown method-case identity")
        _validate_expected_sequence(row, by_identity[identity])
        if identity in seen:
            raise G31ClosureError("duplicate method-case record within a run")
        seen.add(identity)
        status = row.get("semantic_status")
        if status not in _KNOWN_SEMANTIC_STATUSES:
            raise G31ClosureError("result has an unknown semantic status")
        if status in _TERMINAL_SEMANTIC_STATUSES:
            terminal.add(identity)
    return [
        dict(item)
        for item in expected_order
        if (str(item["qa_id"]), str(item["method"])) not in terminal
    ]


def assess_fixed50_continuation(
    expected_order: Sequence[Mapping[str, Any]],
    original_results: Sequence[Mapping[str, Any]],
    *,
    requested_case_count: int,
    original_fingerprints: Mapping[str, Any],
) -> dict[str, Any]:
    """Check continuation scope and whether source fingerprints are available."""
    if (
        isinstance(requested_case_count, bool)
        or not isinstance(requested_case_count, int)
        or requested_case_count < 0
    ):
        raise G31ClosureError("requested_case_count must be a non-negative integer")
    pending = derive_pending_method_cases(expected_order, original_results)
    missing_fingerprints = [
        field
        for field in REQUIRED_CONTINUATION_FINGERPRINTS
        if not isinstance(original_fingerprints.get(field), str)
        or not original_fingerprints[field].strip()
    ]
    blockers: list[str] = []
    if len(pending) != requested_case_count:
        blockers.append("PENDING_CASE_COUNT_MISMATCH")
    if missing_fingerprints:
        blockers.append("ORIGINAL_IMPLEMENTATION_FINGERPRINTS_MISSING")
    return {
        "status": "READY" if not blockers else "NOT_READY",
        "requested_case_count": requested_case_count,
        "pending_case_count": len(pending),
        "pending_cases": pending,
        "missing_fingerprints": missing_fingerprints,
        "blockers": blockers,
    }


def merge_fixed50_outcomes(
    expected_order: Sequence[Mapping[str, Any]],
    original_run: Mapping[str, Any],
    continuation_run: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and merge terminal outcomes from two sealed offline artifacts."""
    expected = _expected_by_identity(expected_order)
    _require_sealed_run(original_run, "original")
    _require_sealed_run(continuation_run, "continuation")
    original_id = original_run.get("run_id")
    continuation_id = continuation_run.get("run_id")
    if not isinstance(original_id, str) or not original_id:
        raise G31ClosureError("MERGE_INVALID: original run_id is missing")
    if not isinstance(continuation_id, str) or not continuation_id:
        raise G31ClosureError("MERGE_INVALID: continuation run_id is missing")
    if original_id == continuation_id:
        raise G31ClosureError("MERGE_INVALID: run IDs must be distinct")

    original_fingerprints = _require_fingerprints(original_run)
    continuation_fingerprints = _require_fingerprints(continuation_run)
    if original_fingerprints != continuation_fingerprints:
        raise G31ClosureError("MERGE_INVALID: fingerprint mismatch")

    outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    for run in (original_run, continuation_run):
        run_id = str(run["run_id"])
        results = run.get("results")
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            raise G31ClosureError("MERGE_INVALID: results must be a sequence")
        seen_in_run: set[tuple[str, str]] = set()
        for row in results:
            if not isinstance(row, Mapping):
                raise G31ClosureError("MERGE_INVALID: result row is malformed")
            identity = _row_identity(row)
            if identity not in expected:
                raise G31ClosureError("MERGE_INVALID: unknown method-case identity")
            _validate_expected_sequence(row, expected[identity], merge=True)
            if identity in seen_in_run:
                raise G31ClosureError("MERGE_INVALID: duplicate method-case within run")
            seen_in_run.add(identity)
            status = row.get("semantic_status")
            if status not in _KNOWN_SEMANTIC_STATUSES:
                raise G31ClosureError("MERGE_INVALID: unknown semantic status")
            if status == "NOT_EVALUATED":
                continue
            if identity in outcomes:
                raise G31ClosureError("MERGE_INVALID: duplicate terminal method-case")
            outcomes[identity] = {**dict(row), "source_run_id": run_id}

    missing = [identity for identity in expected if identity not in outcomes]
    if missing:
        raise G31ClosureError(
            f"MERGE_INVALID: missing {len(missing)} terminal method-case outcome(s)"
        )
    ordered_results = [
        outcomes[identity]
        for identity in expected
    ]
    return {
        "verdict": "MERGE_VALID",
        "expected_method_case_count": len(expected),
        "terminal_outcome_count": len(ordered_results),
        "results": ordered_results,
        "fingerprints": original_fingerprints,
    }


def _expected_by_identity(
    expected_order: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not expected_order:
        raise G31ClosureError("expected method-case order is empty")
    by_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in expected_order:
        identity = _row_identity(row)
        if identity in by_identity:
            raise G31ClosureError("expected method-case order contains a duplicate")
        by_identity[identity] = row
    return by_identity


def _row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    qa_id = row.get("qa_id")
    question_id = row.get("question_id")
    if qa_id is not None and question_id is not None and qa_id != question_id:
        raise G31ClosureError("method-case identity fields disagree")
    qa_id = qa_id if qa_id is not None else question_id
    method = row.get("method")
    if not isinstance(qa_id, str) or not qa_id or not isinstance(method, str) or not method:
        raise G31ClosureError("method-case identity is incomplete")
    return qa_id, method


def _validate_expected_sequence(
    result: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    merge: bool = False,
) -> None:
    actual_sequence = result.get("sequence")
    expected_sequence = expected.get("sequence")
    if actual_sequence is not None and actual_sequence != expected_sequence:
        prefix = "MERGE_INVALID: " if merge else ""
        raise G31ClosureError(f"{prefix}method-case sequence does not match manifest")


def _require_sealed_run(run: Mapping[str, Any], label: str) -> None:
    if run.get("sealed") is not True:
        raise G31ClosureError(f"MERGE_INVALID: {label} run is not sealed")


def _require_fingerprints(run: Mapping[str, Any]) -> dict[str, str]:
    raw = run.get("fingerprints")
    if not isinstance(raw, Mapping):
        raise G31ClosureError("MERGE_INVALID: fingerprints are missing")
    fingerprints: dict[str, str] = {}
    for field in REQUIRED_CONTINUATION_FINGERPRINTS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise G31ClosureError(f"MERGE_INVALID: fingerprint {field} is missing")
        fingerprints[field] = value
    return fingerprints


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise G31ClosureError(f"{field} must be a non-negative integer")
    return value
