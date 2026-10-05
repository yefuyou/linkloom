from __future__ import annotations

import pytest

from linkloom.retrieval_v2.characterization import (
    classify_failure_categories,
    explain_failure_case,
    percentile,
    recompute_case_metrics,
)


def test_percentile_uses_linear_interpolation_for_small_samples() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 3.85


def test_percentile_rejects_empty_or_out_of_range_inputs() -> None:
    with pytest.raises(ValueError, match="at least one"):
        percentile([], 0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        percentile([1.0], 1.1)


def test_recomputed_metrics_match_hand_calculated_multi_evidence_case() -> None:
    metrics = recompute_case_metrics(
        ranked_source_refs=["noise.md", "a.md", "b.md", "c.md"],
        relevant_source_refs={"a.md", "b.md", "c.md"},
    )

    assert metrics == {
        "recall_at_1": 0.0,
        "recall_at_3": 0.6667,
        "recall_at_5": 1.0,
        "mrr": 0.5,
        "ndcg_at_5": 0.7328,
        "retrieval_failure": 0.0,
        "evidence_availability_at_5": 1.0,
    }


def test_failure_categories_distinguish_ranking_cutoff_from_candidate_miss() -> None:
    official = {
        "current": ["a.md", "noise.md"],
        "bm25": ["a.md", "noise.md"],
        "dense": ["a.md", "noise.md"],
        "hybrid": ["a.md", "noise.md"],
        "directory_hybrid": ["a.md", "noise.md"],
    }
    candidates = {
        "current": ["a.md", "b.md", "noise.md"],
        "bm25": ["a.md", "b.md", "noise.md"],
        "dense": ["a.md", "b.md", "noise.md"],
        "hybrid": ["a.md", "b.md", "noise.md"],
        "directory_hybrid": ["a.md", "b.md", "noise.md"],
    }

    labels = classify_failure_categories(
        relevant_source_refs={"a.md", "b.md"},
        official_rankings=official,
        candidate_rankings=candidates,
    )

    assert labels == ["RANKING_MISS", "MULTI_DOCUMENT_EVIDENCE"]


def test_failure_explanation_uses_observed_top5_and_candidate_ranks() -> None:
    official = {
        "current": ["a.md", "noise.md"],
        "bm25": ["a.md", "noise.md"],
        "dense": ["a.md", "noise.md"],
        "hybrid": ["a.md", "noise.md"],
        "directory_hybrid": ["a.md", "noise.md"],
    }
    candidates = {
        "current": ["a.md", "b.md", "noise.md"],
        "bm25": ["a.md", "b.md", "noise.md"],
        "dense": ["a.md", "b.md", "noise.md"],
        "hybrid": ["a.md", "b.md", "noise.md"],
        "directory_hybrid": ["a.md", "b.md", "noise.md"],
    }

    explanation = explain_failure_case(
        relevant_source_refs={"a.md", "b.md"},
        official_rankings=official,
        candidate_rankings=candidates,
    )

    assert "Top-5" in explanation
    assert "Top-20" in explanation
    assert "ranking cutoff" in explanation
    assert "directory scope loss" in explanation
