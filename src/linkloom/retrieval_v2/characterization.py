"""Pure helpers for validating and describing frozen retrieval benchmarks."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def percentile(values: Iterable[float], quantile: float) -> float:
    """Return a linear-interpolated percentile using the Type 7 convention."""
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    if any(not math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")

    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(interpolated, 4)


def recompute_case_metrics(
    ranked_source_refs: Sequence[str],
    relevant_source_refs: Iterable[str],
) -> dict[str, float]:
    """Independently recompute the frozen benchmark's binary relevance metrics."""
    ranked = tuple(ranked_source_refs)
    relevant = set(relevant_source_refs)

    def recall_at(k: int) -> float:
        if not relevant:
            return 0.0
        return _round(len(relevant.intersection(ranked[:k])) / len(relevant))

    first_relevant_rank = next(
        (rank for rank, source_ref in enumerate(ranked, start=1) if source_ref in relevant),
        None,
    )
    gains = [1.0 if source_ref in relevant else 0.0 for source_ref in ranked[:5]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_count = min(len(relevant), 5)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    availability = 1.0 if relevant.intersection(ranked[:5]) else 0.0

    return {
        "recall_at_1": recall_at(1),
        "recall_at_3": recall_at(3),
        "recall_at_5": recall_at(5),
        "mrr": _round(1.0 / first_relevant_rank) if first_relevant_rank else 0.0,
        "ndcg_at_5": _round(dcg / idcg) if idcg else 0.0,
        "retrieval_failure": 1.0 - availability,
        "evidence_availability_at_5": availability,
    }


def classify_failure_categories(
    *,
    relevant_source_refs: Iterable[str],
    official_rankings: Mapping[str, Sequence[str]],
    candidate_rankings: Mapping[str, Sequence[str]],
) -> list[str]:
    """Classify only failures evidenced by official top-5 and candidate rankings."""
    relevant = set(relevant_source_refs)
    missing_by_mode = {
        mode: relevant.difference(ranked)
        for mode, ranked in official_rankings.items()
    }
    has_missing = any(missing_by_mode.values())
    if not has_missing:
        return []

    labels: set[str] = set()
    for mode, missing_refs in missing_by_mode.items():
        for source_ref in missing_refs:
            if source_ref in candidate_rankings.get(mode, ()):
                labels.add("RANKING_MISS")
            elif mode == "bm25" and any(
                source_ref in candidate_rankings.get(other, ())
                for other in ("dense", "hybrid", "directory_hybrid")
            ):
                labels.add("LEXICAL_MISS")
            elif mode == "dense" and any(
                source_ref in candidate_rankings.get(other, ())
                for other in ("bm25", "hybrid", "directory_hybrid")
            ):
                labels.add("SEMANTIC_MISS")
            elif mode == "directory_hybrid" and source_ref in candidate_rankings.get(
                "hybrid", ()
            ):
                labels.add("SCOPE_ROUTING_MISS")
            elif not any(source_ref in ranking for ranking in candidate_rankings.values()):
                labels.add("NO_RELEVANT_EVIDENCE")
            else:
                labels.add("OTHER")

    if len(relevant) > 1:
        labels.add("MULTI_DOCUMENT_EVIDENCE")

    order = (
        "LEXICAL_MISS",
        "SEMANTIC_MISS",
        "RANKING_MISS",
        "SCOPE_ROUTING_MISS",
        "TEMPORAL_AMBIGUITY",
        "MULTI_DOCUMENT_EVIDENCE",
        "ENTITY_ALIAS",
        "NO_RELEVANT_EVIDENCE",
        "OTHER",
    )
    return [label for label in order if label in labels]


def explain_failure_case(
    *,
    relevant_source_refs: Iterable[str],
    official_rankings: Mapping[str, Sequence[str]],
    candidate_rankings: Mapping[str, Sequence[str]],
) -> str:
    """Explain a failed Top-5 case using only observed frozen rankings."""
    relevant = set(relevant_source_refs)
    top5_omissions = {
        mode: relevant.difference(ranked)
        for mode, ranked in official_rankings.items()
        if relevant.difference(ranked)
    }
    if not top5_omissions:
        return "No frozen relevant source is missing from the official Top-5 results."

    omitted_summary = ", ".join(
        f"{mode} {len(missing)}/{len(relevant)}"
        for mode, missing in top5_omissions.items()
    )
    candidate_omissions = {
        mode: relevant.difference(ranked)
        for mode, ranked in candidate_rankings.items()
        if relevant.difference(ranked)
    }
    if candidate_omissions:
        candidate_summary = ", ".join(
            f"{mode} {len(missing)}/{len(relevant)}"
            for mode, missing in candidate_omissions.items()
        )
        candidate_clause = (
            f" Top-20 also misses relevant sources in {candidate_summary}, "
            "so candidate recall contributes to those modes."
        )
    else:
        candidate_clause = (
            " All relevant sources are present in every Top-20 candidate list, "
            "so the observed Top-5 misses are ranking cutoffs, not candidate-generation misses."
        )

    hybrid_candidates = set(candidate_rankings.get("hybrid", ()))
    directory_candidates = set(candidate_rankings.get("directory_hybrid", ()))
    scope_losses = relevant.intersection(hybrid_candidates - directory_candidates)
    scope_clause = (
        f" Directory-Hybrid excludes {len(scope_losses)} relevant source(s) found by Hybrid, "
        "indicating a scope-routing loss."
        if scope_losses
        else " No directory scope loss from Hybrid to Directory-Hybrid is present in Top-20."
    )
    return (
        f"Top-5 omits relevant sources in {omitted_summary}."
        f"{candidate_clause}{scope_clause}"
    )


def _round(value: float) -> float:
    return round(float(value), 4)
