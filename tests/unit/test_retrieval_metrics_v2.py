from __future__ import annotations

from linkloom.retrieval_v2 import RetrievalCaseResult, evaluate_retrieval, retrieval_case_metrics


def test_retrieval_case_metrics_cover_all_required_measures() -> None:
    metrics = retrieval_case_metrics(
        ranked_source_refs=["a.md", "noise.md", "b.md", "other.md", "c.md"],
        relevant_source_refs={"a.md", "b.md", "c.md"},
    )

    assert metrics == {
        "recall_at_1": 0.3333,
        "recall_at_3": 0.6667,
        "recall_at_5": 1.0,
        "mrr": 1.0,
        "ndcg_at_5": 0.8855,
        "retrieval_failure": 0.0,
        "evidence_availability_at_5": 1.0,
    }


def test_retrieval_aggregate_averages_cases_and_preserves_case_rows() -> None:
    report = evaluate_retrieval(
        [
            RetrievalCaseResult(
                case_id="hit",
                ranked_source_refs=("a.md",),
                relevant_source_refs=frozenset({"a.md"}),
                latency_ms=2.0,
            ),
            RetrievalCaseResult(
                case_id="miss",
                ranked_source_refs=("noise.md",),
                relevant_source_refs=frozenset({"b.md"}),
                latency_ms=4.0,
            ),
        ]
    )

    assert report.metrics["recall_at_1"] == 0.5
    assert report.metrics["retrieval_failure_rate"] == 0.5
    assert report.metrics["evidence_availability_at_5"] == 0.5
    assert report.metrics["mean_latency_ms"] == 3.0
    assert [row.case_id for row in report.cases] == ["hit", "miss"]
