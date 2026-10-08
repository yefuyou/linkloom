"""Explicit review flow shared by semantic-ingestion smoke runs and tests."""

from datetime import datetime
import re

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import (
    CandidateOutcome,
    ClaimType,
    EntityResolution,
    RelationResolution,
    TemporalResolution,
)
from linkloom.semantic_ingestion.materialization import (
    CandidateReviewResolution,
    MaterializationAssessment,
    MaterializationBlockedError,
    SemanticDecisionMaterializer,
)


def synthetic_review_eligibility_gate(
    validation: object,
    quote: str,
    *,
    source_artifact: RawArtifact,
    reviewed_event_time: datetime | None = None,
) -> tuple[bool, str]:
    """Check whether a synthetic test reviewer may review a source decision.

    This gate only determines eligibility for an explicit, fingerprint-bound
    review. It does not approve or materialize a candidate.
    """

    candidate = getattr(validation, "candidate", None)
    if candidate is None:
        return False, "NO_CANDIDATE"
    provenance = getattr(candidate, "provenance", None)
    if (
        not getattr(validation, "provenance_valid", False)
        or provenance is None
        or not provenance.verify(source_artifact)
    ):
        return False, "PROVENANCE_NOT_VERIFIED"
    if candidate.claim_type is not ClaimType.DECISION:
        return False, "NON_DECISION_SEMANTIC_CLASS"
    if validation.outcome not in {CandidateOutcome.ACCEPTED, CandidateOutcome.UNRESOLVED}:
        return False, "VALIDATION_NOT_REVIEWABLE"

    temporal_resolved = (
        candidate.temporal_resolution in {TemporalResolution.SOURCE_EVENT_TIME, TemporalResolution.EXPLICIT}
        and candidate.valid_from is not None
    )
    source_event_time_reviewable = (
        reviewed_event_time is not None
        and reviewed_event_time == candidate.event_time
        and candidate.temporal_resolution in {TemporalResolution.UNRESOLVED, TemporalResolution.NOT_STATED}
        and candidate.valid_from is None
    )
    allowed_reasons = {"CONFLICT_REQUIRES_REVIEW"}
    if source_event_time_reviewable:
        allowed_reasons.add("TEMPORAL_UNRESOLVED")
    validation_reasons = {
        getattr(reason, "value", reason) for reason in validation.reason_codes
    }
    if validation_reasons - allowed_reasons:
        return False, "UNRESOLVED_VALIDATION_REASON"

    if (
        candidate.subject is None
        or candidate.subject_key is None
        or candidate.relation is None
        or candidate.value is None
        or candidate.relation_resolution is not RelationResolution.CANONICAL_RELATION
        or candidate.entity_resolution is not EntityResolution.SOURCE_GROUNDED
        or not (temporal_resolved or source_event_time_reviewable)
    ):
        return False, "REQUIRED_FIELD_UNRESOLVED"

    folded = " ".join(quote.casefold().split())
    if re.search(
        r"\b(?:only a proposal|no decision has changed|i (?:propose|suggest)|"
        r"we (?:should|could|might) consider|could we compare|we are considering)\b",
        folded,
    ):
        return False, "SOURCE_IS_NON_AUTHORITATIVE"
    if re.search(
        r"\b(?:according to|(?:a|the) prior report|the report|historical record|"
        r"prior record|the log|the history)\b.{0,80}\b(?:says|said|reports|reported|"
        r"notes|noted|shows|showed|records|recorded)\b",
        folded,
    ):
        return False, "HISTORICAL_SOURCE_REPORT_ONLY"

    explicit_team_choice = re.search(
        r"\b(?:we|our team|the team)\b.{0,120}\b(?:decid\w*|approv\w*|"
        r"adopt\w*|select\w*|choos\w*|switch\w*|replac\w*|supersed\w*|"
        r"cancel\w*|commit\w*)\b",
        folded,
    )
    explicit_transition = re.search(
        r"\b(?:replac\w*|supersed\w*|switch\w*|adopt\w*|approv\w*)\b",
        folded,
    )
    if not (explicit_team_choice or explicit_transition):
        return False, "NO_EXPLICIT_DECISION_CUE"
    if source_event_time_reviewable:
        return True, "EXPLICIT_SOURCE_DECISION_REVIEWABLE_WITH_TEST_TIME_REVIEW"
    return True, "EXPLICIT_SOURCE_DECISION_REVIEWABLE"


def authorize_review_and_materialize(
    service: SemanticDecisionMaterializer,
    workspace_id: str,
    candidate_id: str,
    *,
    reviewer_id: str,
    expected_candidate_fingerprint: str,
    reason: str,
    resolution: CandidateReviewResolution | None,
    now: datetime,
) -> tuple[MaterializationAssessment, MaterializationAssessment]:
    """Apply a fingerprint-bound review before checking post-review eligibility.

    The caller must supply the review identity, current candidate fingerprint,
    reason, and any field resolution. This helper never approves a candidate
    based on its pre-review eligibility and never changes a candidate itself.
    """

    approved = service.approve(
        workspace_id,
        candidate_id,
        reviewer_id=reviewer_id,
        expected_candidate_fingerprint=expected_candidate_fingerprint,
        reason=reason,
        resolution=resolution,
        now=now,
    )
    if not approved.policy_decision.materializable_after_review:
        code = (
            approved.policy_decision.reason_codes[0]
            if approved.policy_decision.reason_codes
            else "MATERIALIZATION_REJECTED"
        )
        raise MaterializationBlockedError(
            code,
            "review resolution did not make the candidate eligible for materialization",
        )

    materialized = service.materialize(workspace_id, candidate_id, now=now)
    if materialized.receipt is None:
        code = (
            materialized.policy_decision.reason_codes[0]
            if materialized.policy_decision.reason_codes
            else "MATERIALIZATION_RECEIPT_MISSING"
        )
        raise MaterializationBlockedError(
            code,
            "authorized candidate did not produce a materialization receipt",
        )
    return approved, materialized
