from __future__ import annotations

import pytest

from benchmarks.memoryagentbench.g31_closure import (
    G31ClosureError,
    assess_fixed50_continuation,
    calculate_token_estimator_metrics,
    merge_fixed50_outcomes,
)


FINGERPRINTS = {
    "dataset_revision": "revision-1",
    "subset_hash": "a" * 64,
    "temporal_memory_fingerprint": "b" * 64,
    "retrieval_fingerprint": "c" * 64,
    "prompt_fingerprint": "d" * 64,
    "gemini_config_fingerprint": "e" * 64,
    "scoring_fingerprint": "f" * 64,
}


def _plan() -> list[dict[str, object]]:
    return [
        {"sequence": 1, "qa_id": "no0", "method": "Flat Retrieval"},
        {"sequence": 2, "qa_id": "no0", "method": "LinkLoom Temporal Memory"},
        {"sequence": 3, "qa_id": "no1", "method": "Flat Retrieval"},
    ]


def test_token_estimator_metrics_include_match_margin_and_underestimate_metrics() -> None:
    metrics = calculate_token_estimator_metrics(
        [
            {"case_id": "a", "count_tokens": 10, "provider_actual": 10, "static_estimate": 30},
            {"case_id": "b", "count_tokens": 12, "provider_actual": 12, "static_estimate": 40},
            {"case_id": "c", "count_tokens": 11, "provider_actual": 12, "static_estimate": 11},
        ]
    )

    assert metrics["case_count"] == 3
    assert metrics["count_tokens_exact_matches"] == 2
    assert metrics["static_coverage_count"] == 2
    assert metrics["underestimation_case_ids"] == ["c"]
    assert metrics["margin_tokens"]["min"] == -1
    assert metrics["margin_tokens"]["median"] == 20
    assert metrics["margin_tokens"]["p95"] == 28
    assert metrics["max_overestimate_ratio"] == pytest.approx(40 / 12)


def test_continuation_counts_not_evaluated_as_pending_and_reports_count_mismatch() -> None:
    assessment = assess_fixed50_continuation(
        _plan(),
        [
            {"sequence": 1, "question_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
            {"sequence": 2, "qa_id": "no0", "method": "LinkLoom Temporal Memory", "semantic_status": "NOT_EVALUATED"},
        ],
        requested_case_count=1,
        original_fingerprints=FINGERPRINTS,
    )

    assert assessment["status"] == "NOT_READY"
    assert assessment["pending_case_count"] == 2
    assert assessment["pending_cases"] == _plan()[1:]
    assert "PENDING_CASE_COUNT_MISMATCH" in assessment["blockers"]


def test_merge_keeps_not_evaluated_attempt_out_of_final_exactly_once_results() -> None:
    merged = merge_fixed50_outcomes(
        _plan()[:2],
        {
            "run_id": "original",
            "sealed": True,
            "fingerprints": FINGERPRINTS,
            "results": [
                {"sequence": 1, "qa_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
                {"sequence": 2, "qa_id": "no0", "method": "LinkLoom Temporal Memory", "semantic_status": "NOT_EVALUATED"},
            ],
        },
        {
            "run_id": "continuation",
            "sealed": True,
            "fingerprints": FINGERPRINTS,
            "results": [
                {"sequence": 2, "qa_id": "no0", "method": "LinkLoom Temporal Memory", "semantic_status": "PASS"},
            ],
        },
    )

    assert merged["verdict"] == "MERGE_VALID"
    assert [(row["qa_id"], row["method"]) for row in merged["results"]] == [
        ("no0", "Flat Retrieval"),
        ("no0", "LinkLoom Temporal Memory"),
    ]
    assert merged["results"][1]["source_run_id"] == "continuation"


@pytest.mark.parametrize(
    ("continuation_results", "message"),
    [
        (
            [
                {"sequence": 1, "qa_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
                {"sequence": 2, "qa_id": "no0", "method": "LinkLoom Temporal Memory", "semantic_status": "PASS"},
            ],
            "duplicate",
        ),
        ([], "missing"),
    ],
)
def test_merge_rejects_duplicate_or_missing_terminal_outcome(continuation_results, message) -> None:
    with pytest.raises(G31ClosureError, match=message):
        merge_fixed50_outcomes(
            _plan()[:2],
            {
                "run_id": "original",
                "sealed": True,
                "fingerprints": FINGERPRINTS,
                "results": [
                    {"sequence": 1, "qa_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
                    {"sequence": 2, "qa_id": "no0", "method": "LinkLoom Temporal Memory", "semantic_status": "NOT_EVALUATED"},
                ],
            },
            {
                "run_id": "continuation",
                "sealed": True,
                "fingerprints": FINGERPRINTS,
                "results": continuation_results,
            },
        )


def test_merge_rejects_fingerprint_mismatch_or_unsealed_input() -> None:
    bad_fingerprints = {**FINGERPRINTS, "retrieval_fingerprint": "x" * 64}
    with pytest.raises(G31ClosureError, match="fingerprint"):
        merge_fixed50_outcomes(
            _plan()[:1],
            {"run_id": "original", "sealed": True, "fingerprints": FINGERPRINTS, "results": [
                {"sequence": 1, "qa_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
            ]},
            {"run_id": "continuation", "sealed": True, "fingerprints": bad_fingerprints, "results": []},
        )

    with pytest.raises(G31ClosureError, match="sealed"):
        merge_fixed50_outcomes(
            _plan()[:1],
            {"run_id": "original", "sealed": True, "fingerprints": FINGERPRINTS, "results": [
                {"sequence": 1, "qa_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
            ]},
            {"run_id": "continuation", "sealed": False, "fingerprints": FINGERPRINTS, "results": []},
        )


def test_merge_rejects_sequence_drift_against_frozen_manifest() -> None:
    with pytest.raises(G31ClosureError, match="sequence"):
        merge_fixed50_outcomes(
            _plan()[:1],
            {
                "run_id": "original",
                "sealed": True,
                "fingerprints": FINGERPRINTS,
                "results": [
                    {"sequence": 3, "qa_id": "no0", "method": "Flat Retrieval", "semantic_status": "PASS"},
                ],
            },
            {"run_id": "continuation", "sealed": True, "fingerprints": FINGERPRINTS, "results": []},
        )
