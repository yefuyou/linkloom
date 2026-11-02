"""Small, dependency-free contracts shared by reranking backends."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import math
from numbers import Real
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from linkloom.retrieval_v2.models import RetrievedEvidence


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    evidence: RetrievedEvidence
    text: str


class Reranker(Protocol):
    def rerank(self, query: str, candidates: Sequence[RerankCandidate]) -> list[RetrievedEvidence]: ...


class RerankError(Exception):
    """A recoverable error whose reason is a safe, non-content-bearing code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class NoopReranker:
    enabled = False
    backend = "noop"

    def rerank(self, query: str, candidates: Sequence[RerankCandidate]) -> list[RetrievedEvidence]:
        return [candidate.evidence for candidate in candidates]


def finite_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise RerankError("invalid_scores")
    return float(value)


def score_candidates(
    candidates: Sequence[RerankCandidate], scores: Sequence[float], *, backend: str = "local"
) -> list[RetrievedEvidence]:
    """Create scored copies without replacing the retrieval score or identities."""
    if len(candidates) != len(scores):
        raise RerankError("invalid_scores")
    ids = [candidate.evidence.evidence_id for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise RerankError("invalid_scores")
    scored = [(candidate, finite_score(score), index) for index, (candidate, score) in enumerate(zip(candidates, scores))]
    scored.sort(key=lambda row: (-row[1], row[0].evidence.rank, row[2]))
    return [
        replace(
            candidate.evidence,
            rank=rank,
            rerank_score=score,
            metadata={**candidate.evidence.metadata, "rrf_rank": candidate.evidence.rank, "rerank_backend": backend},
        )
        for rank, (candidate, score, _) in enumerate(scored, start=1)
    ]
