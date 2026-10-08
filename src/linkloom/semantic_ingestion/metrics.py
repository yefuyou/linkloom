"""Small synthetic-fixture metrics for Step 2 acceptance checks."""

from __future__ import annotations

from dataclasses import dataclass

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import (
    CandidateOutcome,
    CandidateReasonCode,
    CandidateValidationResult,
    ClaimType,
    RelationResolution,
    TemporalResolution,
)


@dataclass(frozen=True, slots=True)
class SemanticEvaluationLabel:
    claim_type: ClaimType | None = None
    subject: str | None = None
    relation_resolution: RelationResolution | None = None
    temporal_resolution: TemporalResolution | None = None
    malformed_output: bool = False


@dataclass(frozen=True, slots=True)
class SemanticEvaluationCase:
    result: CandidateValidationResult | None
    label: SemanticEvaluationLabel
    artifact: RawArtifact


@dataclass(frozen=True, slots=True)
class SemanticEvaluationMetrics:
    case_count: int
    candidate_count: int
    semantic_class_denominator: int
    subject_denominator: int
    relation_resolution_denominator: int
    temporal_resolution_denominator: int
    provenance_denominator: int
    malformed_output_denominator: int
    false_authoritative_denominator: int
    semantic_class_accuracy: float | None
    subject_accuracy: float | None
    relation_resolution_accuracy: float | None
    temporal_resolution_accuracy: float | None
    provenance_validity_rate: float | None
    malformed_output_rejection_rate: float | None
    false_authoritative_rate: float | None


def _ratio(correct: int, total: int) -> float | None:
    return correct / total if total else None


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def evaluate_semantic_extraction(
    cases: list[SemanticEvaluationCase] | tuple[SemanticEvaluationCase, ...],
) -> SemanticEvaluationMetrics:
    """Compare accepted/unresolved outputs to small human-labeled fixtures.

    The false-authoritative metric counts any candidate that escaped the
    non-authoritative type boundary. Step 2 has no materialization operation.
    """
    if not isinstance(cases, (list, tuple)):
        raise ValueError("cases must be a list or tuple")
    if any(not isinstance(case, SemanticEvaluationCase) for case in cases):
        raise ValueError("cases must contain SemanticEvaluationCase values")

    class_total = class_correct = 0
    subject_total = subject_correct = 0
    relation_total = relation_correct = 0
    temporal_total = temporal_correct = 0
    provenance_total = provenance_correct = 0
    malformed_total = malformed_rejected = 0
    candidate_count = 0
    false_authoritative = 0

    for case in cases:
        result = case.result
        candidate = result.candidate if result is not None else None
        label = case.label
        if label.claim_type is not None:
            class_total += 1
            class_correct += int(candidate is not None and candidate.claim_type is label.claim_type)
        if label.subject is not None:
            subject_total += 1
            subject_correct += int(
                candidate is not None
                and candidate.subject is not None
                and _normalized(candidate.subject) == _normalized(label.subject)
            )
        if label.relation_resolution is not None:
            relation_total += 1
            relation_correct += int(
                candidate is not None
                and candidate.relation_resolution is label.relation_resolution
            )
        if label.temporal_resolution is not None:
            temporal_total += 1
            temporal_correct += int(
                candidate is not None
                and candidate.temporal_resolution is label.temporal_resolution
            )
        if candidate is not None:
            candidate_count += 1
            provenance_total += 1
            provenance_correct += int(
                result is not None
                and result.provenance_valid
                and candidate.provenance.verify(case.artifact)
            )
            false_authoritative += int(candidate.authority != "NON_AUTHORITATIVE")
        if label.malformed_output:
            malformed_total += 1
            malformed_rejected += int(
                result is not None
                and result.outcome is CandidateOutcome.REJECTED
                and CandidateReasonCode.MALFORMED_EXTRACTION in result.reason_codes
            )

    return SemanticEvaluationMetrics(
        case_count=len(cases),
        candidate_count=candidate_count,
        semantic_class_denominator=class_total,
        subject_denominator=subject_total,
        relation_resolution_denominator=relation_total,
        temporal_resolution_denominator=temporal_total,
        provenance_denominator=provenance_total,
        malformed_output_denominator=malformed_total,
        false_authoritative_denominator=candidate_count,
        semantic_class_accuracy=_ratio(class_correct, class_total),
        subject_accuracy=_ratio(subject_correct, subject_total),
        relation_resolution_accuracy=_ratio(relation_correct, relation_total),
        temporal_resolution_accuracy=_ratio(temporal_correct, temporal_total),
        provenance_validity_rate=_ratio(provenance_correct, provenance_total),
        malformed_output_rejection_rate=_ratio(malformed_rejected, malformed_total),
        false_authoritative_rate=_ratio(false_authoritative, candidate_count),
    )
