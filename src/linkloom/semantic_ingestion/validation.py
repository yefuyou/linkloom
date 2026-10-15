"""Local, provider-independent validation and provenance binding."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
import hashlib
import json
import re

from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.candidate_models import (
    CandidateDecisionFact,
    CandidateOutcome,
    CandidateReasonCode,
    CandidateValidationResult,
    EntityResolution,
    ExtractedClaim,
    RelationResolution,
    SemanticExtractionResult,
    TemporalBasis,
    TemporalResolution,
    candidate_subject_key_for,
    extraction_fingerprint_for,
)
from linkloom.semantic_ingestion.extraction import SemanticExtractionContext
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import TimestampedTextSegment


_DATE_ONLY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SOURCE_EVENT_EFFECTIVE_CUE = re.compile(
    r"\b(?:we\s+(?:decided\s+to|decided\s+that|selected|chose|will\s+use|use)"
    r"|replaces?|replace)\b",
    re.IGNORECASE,
)


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_mention(value: str) -> str:
    return " ".join(value.casefold().split())


def _is_grounded_mention(value: str | None, quote: str) -> bool:
    if value is None:
        return False
    normalized_value = _normalized_mention(value)
    normalized_quote = _normalized_mention(quote)
    return bool(normalized_value) and normalized_value in normalized_quote


def _parse_valid_time(value: str | None) -> date | datetime | None:
    if value is None:
        return None
    if _DATE_ONLY.fullmatch(value):
        return date.fromisoformat(value)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("valid-time timestamp must include an explicit timezone")
    return parsed.astimezone(UTC)


def parse_explicit_valid_time(value: str | None) -> date | datetime | None:
    """Parse the explicit ISO valid-time forms accepted by Semantic Ingestion."""
    return _parse_valid_time(value)


def source_event_time_is_effective(text: str) -> bool:
    """Apply the existing narrow source-event-time cue to exact source text."""
    return isinstance(text, str) and _SOURCE_EVENT_EFFECTIVE_CUE.search(text) is not None


def _as_utc_midnight(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


class CandidateValidator:
    """Validate a structured claim, then bind provenance from Step 1 locally."""

    def validate(
        self,
        extraction: SemanticExtractionResult,
        segment: TimestampedTextSegment,
        artifact: RawArtifact,
        context: SemanticExtractionContext,
    ) -> tuple[CandidateValidationResult, ...]:
        if not isinstance(extraction, SemanticExtractionResult):
            raise TypeError("extraction must be SemanticExtractionResult")
        if not isinstance(segment, TimestampedTextSegment):
            raise TypeError("segment must be a Step 1 TimestampedTextSegment")
        if not isinstance(artifact, RawArtifact):
            raise TypeError("artifact must be RawArtifact")
        if not isinstance(context, SemanticExtractionContext):
            raise TypeError("context must be SemanticExtractionContext")

        if extraction.failure_code is not None:
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(extraction.failure_code,),
                    extraction_receipt=extraction.receipt,
                    provenance_valid=False,
                ),
            )

        if artifact.workspace_id != context.expected_workspace_id:
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(CandidateReasonCode.WORKSPACE_MISMATCH,),
                    extraction_receipt=extraction.receipt,
                    provenance_valid=False,
                ),
            )

        receipt = extraction.receipt
        if receipt.workspace_id != artifact.workspace_id:
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(CandidateReasonCode.WORKSPACE_MISMATCH,),
                    extraction_receipt=receipt,
                    provenance_valid=False,
                ),
            )
        if (
            receipt.artifact_version_id != artifact.artifact_version_id
            or receipt.segment_id != segment.segment_id
            or receipt.evidence_ref != segment.evidence_span.evidence_ref
            or receipt.quote_sha256 != segment.evidence_span.quote_sha256
        ):
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(CandidateReasonCode.PROVENANCE_INVALID,),
                    extraction_receipt=receipt,
                    provenance_valid=False,
                ),
            )
        if receipt.relation_catalog_fingerprint != context.relation_resolver.fingerprint:
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(CandidateReasonCode.VALIDATION_REJECTED,),
                    extraction_receipt=receipt,
                    provenance_valid=False,
                ),
            )
        expected_fingerprint = extraction_fingerprint_for(
            workspace_id=receipt.workspace_id,
            artifact_version_id=receipt.artifact_version_id,
            segment_id=receipt.segment_id,
            evidence_ref=receipt.evidence_ref,
            quote_sha256=receipt.quote_sha256,
            config_sha256=receipt.config_sha256,
            prompt_sha256=receipt.prompt_sha256,
        )
        if expected_fingerprint != receipt.extraction_fingerprint:
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(CandidateReasonCode.VALIDATION_REJECTED,),
                    extraction_receipt=receipt,
                    provenance_valid=False,
                ),
            )

        if (
            segment.body_text != segment.evidence_span.quote
            or segment.message_id != segment.evidence_span.message_id
            or segment.segment_id != segment.evidence_span.segment_id
            or not segment.evidence_span.verify(artifact)
        ):
            return (
                CandidateValidationResult(
                    outcome=CandidateOutcome.REJECTED,
                    candidate=None,
                    reason_codes=(CandidateReasonCode.PROVENANCE_INVALID,),
                    extraction_receipt=extraction.receipt,
                    provenance_valid=False,
                ),
            )

        return tuple(
            self._validate_claim(claim, extraction, segment, artifact, context.relation_resolver)
            for claim in extraction.claims
        )

    def flag_conflicts(
        self,
        results: tuple[CandidateValidationResult, ...],
    ) -> tuple[CandidateValidationResult, ...]:
        """Mark contradictory same-slot claims for review without choosing a winner."""
        if not isinstance(results, tuple) or any(
            not isinstance(result, CandidateValidationResult) for result in results
        ):
            raise ValueError("results must be a tuple of CandidateValidationResult")

        conflict_indices: set[int] = set()
        for left_index, left_result in enumerate(results):
            left = left_result.candidate
            if not self._is_conflict_eligible(left):
                continue
            for right_index in range(left_index + 1, len(results)):
                right_result = results[right_index]
                right = right_result.candidate
                if not self._is_conflict_eligible(right):
                    continue
                if (
                    left.workspace_id != right.workspace_id
                    or _normalized_mention(left.subject or "")
                    != _normalized_mention(right.subject or "")
                    or left.relation != right.relation
                    or _normalized_mention(left.value or "")
                    == _normalized_mention(right.value or "")
                ):
                    continue
                if self._explicit_replacement(left, right.value or "") or self._explicit_replacement(
                    right,
                    left.value or "",
                ):
                    continue
                conflict_indices.update((left_index, right_index))

        if not conflict_indices:
            return results

        reviewed: list[CandidateValidationResult] = []
        for index, result in enumerate(results):
            if index not in conflict_indices or result.candidate is None:
                reviewed.append(result)
                continue
            reason = CandidateReasonCode.CONFLICT_REQUIRES_REVIEW
            candidate = replace(
                result.candidate,
                review_reasons=tuple(dict.fromkeys((*result.candidate.review_reasons, reason))),
            )
            reason_codes = tuple(dict.fromkeys((*result.reason_codes, reason)))
            reviewed.append(
                replace(
                    result,
                    outcome=CandidateOutcome.UNRESOLVED,
                    candidate=candidate,
                    reason_codes=reason_codes,
                )
            )
        return tuple(reviewed)

    @staticmethod
    def _is_conflict_eligible(candidate: CandidateDecisionFact | None) -> bool:
        return bool(
            candidate is not None
            and candidate.claim_type.value in {"DECISION", "FACT"}
            and candidate.subject
            and candidate.relation
            and candidate.relation_resolution is RelationResolution.CANONICAL_RELATION
            and candidate.value
            and candidate.entity_resolution is EntityResolution.SOURCE_GROUNDED
        )

    @staticmethod
    def _explicit_replacement(candidate: CandidateDecisionFact, prior_value: str) -> bool:
        quote = _normalized_mention(candidate.provenance.quote)
        current_value = _normalized_mention(candidate.value or "")
        previous_value = _normalized_mention(prior_value)
        affirmative_phrases = (
            f"{current_value} replaces {previous_value}",
            f"{current_value} replaced {previous_value}",
            f"{current_value} supersedes {previous_value}",
            f"{current_value} superseded {previous_value}",
            f"{previous_value} was replaced by {current_value}",
            f"{previous_value} is replaced by {current_value}",
            f"{previous_value} was superseded by {current_value}",
            f"{previous_value} is superseded by {current_value}",
        )
        return any(phrase in quote for phrase in affirmative_phrases)

    def _validate_claim(
        self,
        claim: ExtractedClaim,
        extraction: SemanticExtractionResult,
        segment: TimestampedTextSegment,
        artifact: RawArtifact,
        relation_resolver: FrozenRelationResolver,
    ) -> CandidateValidationResult:
        reason_codes: list[CandidateReasonCode] = []
        if claim.subject is None or claim.value is None or not (
            _is_grounded_mention(claim.subject, segment.body_text)
            and _is_grounded_mention(claim.value, segment.body_text)
        ):
            reason_codes.append(CandidateReasonCode.ENTITY_UNRESOLVED)
            entity_resolution = EntityResolution.UNRESOLVED
        else:
            entity_resolution = EntityResolution.SOURCE_GROUNDED

        relation_result = relation_resolver.resolve(claim.relation_phrase)
        relation_resolution = relation_result.status
        if (
            relation_resolution is RelationResolution.NEW_RELATION_CANDIDATE
            and not _is_grounded_mention(claim.relation_phrase, segment.body_text)
        ):
            relation_resolution = RelationResolution.UNRESOLVED_RELATION
        if relation_resolution is not RelationResolution.CANONICAL_RELATION:
            reason_codes.append(CandidateReasonCode.RELATION_UNRESOLVED)

        valid_from: date | datetime | None = None
        valid_to: date | datetime | None = None
        temporal_resolution = claim.temporal_status
        temporal_basis = TemporalBasis.UNRESOLVED
        if claim.temporal_status is TemporalResolution.SOURCE_EVENT_TIME:
            if (
                claim.claim_type.value == "DECISION"
                and _SOURCE_EVENT_EFFECTIVE_CUE.search(segment.body_text)
            ):
                valid_from = segment.event_time
                temporal_basis = TemporalBasis.DETERMINISTIC_RULE
            else:
                temporal_resolution = TemporalResolution.UNRESOLVED
                reason_codes.append(CandidateReasonCode.TEMPORAL_UNRESOLVED)
        elif claim.temporal_status is TemporalResolution.EXPLICIT:
            try:
                if claim.valid_from_raw not in segment.body_text:
                    raise ValueError("valid_from is not present in the source span")
                valid_from = _parse_valid_time(claim.valid_from_raw)
                if claim.valid_to_raw is not None:
                    if claim.valid_to_raw not in segment.body_text:
                        raise ValueError("valid_to is not present in the source span")
                valid_to = _parse_valid_time(claim.valid_to_raw)
                if valid_from is None:
                    raise ValueError("valid_from is required")
                if valid_to is not None and _as_utc_midnight(valid_to) <= _as_utc_midnight(valid_from):
                    raise ValueError("valid_to must follow valid_from")
                temporal_basis = TemporalBasis.EXPLICIT
            except (TypeError, ValueError, OverflowError):
                valid_from = None
                valid_to = None
                temporal_resolution = TemporalResolution.UNRESOLVED
                reason_codes.append(CandidateReasonCode.TEMPORAL_UNRESOLVED)
        elif claim.temporal_status is TemporalResolution.UNRESOLVED:
            reason_codes.append(CandidateReasonCode.TEMPORAL_UNRESOLVED)

        source_episode_id = source_episode_id_for(artifact)
        candidate_payload = {
            "workspace_id": artifact.workspace_id,
            "artifact_id": artifact.artifact_id,
            "artifact_version_id": artifact.artifact_version_id,
            "source_episode_id": source_episode_id,
            "evidence_ref": segment.evidence_span.evidence_ref,
            "claim_type": claim.claim_type.value,
            "subject": _normalized_mention(claim.subject) if claim.subject else None,
            "subject_key": candidate_subject_key_for(
                claim.subject if entity_resolution is EntityResolution.SOURCE_GROUNDED else None,
                relation_result.canonical_relation,
            ),
            "relation": relation_result.canonical_relation,
            "relation_phrase": claim.relation_phrase,
            "value": _normalized_mention(claim.value) if claim.value else None,
            "temporal_resolution": temporal_resolution.value,
            "temporal_basis": temporal_basis.value,
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "extraction_fingerprint": extraction.receipt.extraction_fingerprint,
        }
        candidate_id = f"candidate_v1_{_sha256_json(candidate_payload)}"
        candidate = CandidateDecisionFact(
            candidate_id=candidate_id,
            workspace_id=artifact.workspace_id,
            artifact_id=artifact.artifact_id,
            artifact_version_id=artifact.artifact_version_id,
            source_episode_id=source_episode_id,
            source_ref=artifact.source_ref,
            content_sha256=artifact.content_hash,
            segment_id=segment.segment_id,
            message_id=segment.message_id,
            evidence_ref=segment.evidence_span.evidence_ref,
            provenance=segment.evidence_span,
            claim_type=claim.claim_type,
            subject=claim.subject,
            subject_key=candidate_subject_key_for(
                claim.subject if entity_resolution is EntityResolution.SOURCE_GROUNDED else None,
                relation_result.canonical_relation,
            ),
            relation=relation_result.canonical_relation,
            relation_phrase=claim.relation_phrase,
            value=claim.value,
            relation_resolution=relation_resolution,
            entity_resolution=entity_resolution,
            event_time=segment.event_time,
            ingestion_time=artifact.ingestion_time,
            valid_from=valid_from,
            valid_to=valid_to,
            temporal_resolution=temporal_resolution,
            temporal_basis=temporal_basis,
            confidence=claim.confidence,
            extraction_fingerprint=extraction.receipt.extraction_fingerprint,
            extraction_receipt=extraction.receipt,
            review_reasons=tuple(reason_codes),
        )
        return CandidateValidationResult(
            outcome=CandidateOutcome.UNRESOLVED if reason_codes else CandidateOutcome.ACCEPTED,
            candidate=candidate,
            reason_codes=tuple(reason_codes),
            extraction_receipt=extraction.receipt,
            provenance_valid=True,
        )
