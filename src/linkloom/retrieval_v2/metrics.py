"""Frozen retrieval metrics for the Interview Readiness benchmark."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _round(value: float) -> float:
    return round(float(value), 4)


@dataclass(frozen=True, slots=True)
class RetrievalCaseResult:
    case_id: str
    ranked_source_refs: tuple[str, ...]
    relevant_source_refs: frozenset[str]
    latency_ms: float


@dataclass(frozen=True, slots=True)
class RetrievalCaseEvaluation:
    case_id: str
    metrics: dict[str, float]
    ranked_source_refs: tuple[str, ...]
    relevant_source_refs: frozenset[str]
    latency_ms: float


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    metrics: dict[str, float]
    cases: tuple[RetrievalCaseEvaluation, ...]


def retrieval_case_metrics(
    ranked_source_refs: list[str] | tuple[str, ...],
    relevant_source_refs: set[str] | frozenset[str],
) -> dict[str, float]:
    relevant = set(relevant_source_refs)

    def recall_at(k: int) -> float:
        if not relevant:
            return 0.0
        return _round(len(relevant.intersection(ranked_source_refs[:k])) / len(relevant))

    reciprocal_rank = 0.0
    for rank, source_ref in enumerate(ranked_source_refs, start=1):
        if source_ref in relevant:
            reciprocal_rank = 1.0 / rank
            break

    gains = [1.0 if source_ref in relevant else 0.0 for source_ref in ranked_source_refs[:5]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_count = min(len(relevant), 5)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    available = 1.0 if relevant.intersection(ranked_source_refs[:5]) else 0.0
    return {
        "recall_at_1": recall_at(1),
        "recall_at_3": recall_at(3),
        "recall_at_5": recall_at(5),
        "mrr": _round(reciprocal_rank),
        "ndcg_at_5": _round(dcg / idcg) if idcg else 0.0,
        "retrieval_failure": 1.0 - available,
        "evidence_availability_at_5": available,
    }


def evaluate_retrieval(results: list[RetrievalCaseResult]) -> RetrievalEvaluationReport:
    rows = tuple(
        RetrievalCaseEvaluation(
            case_id=result.case_id,
            metrics=retrieval_case_metrics(result.ranked_source_refs, result.relevant_source_refs),
            ranked_source_refs=result.ranked_source_refs,
            relevant_source_refs=result.relevant_source_refs,
            latency_ms=result.latency_ms,
        )
        for result in results
    )
    if not rows:
        return RetrievalEvaluationReport(
            metrics={
                "recall_at_1": 0.0,
                "recall_at_3": 0.0,
                "recall_at_5": 0.0,
                "mrr": 0.0,
                "ndcg_at_5": 0.0,
                "retrieval_failure_rate": 0.0,
                "evidence_availability_at_5": 0.0,
                "mean_latency_ms": 0.0,
            },
            cases=(),
        )
    metric_names = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "ndcg_at_5")
    aggregate = {
        name: _round(sum(row.metrics[name] for row in rows) / len(rows))
        for name in metric_names
    }
    aggregate.update(
        {
            "retrieval_failure_rate": _round(
                sum(row.metrics["retrieval_failure"] for row in rows) / len(rows)
            ),
            "evidence_availability_at_5": _round(
                sum(row.metrics["evidence_availability_at_5"] for row in rows) / len(rows)
            ),
            "mean_latency_ms": _round(sum(row.latency_ms for row in rows) / len(rows)),
        }
    )
    return RetrievalEvaluationReport(metrics=aggregate, cases=rows)
