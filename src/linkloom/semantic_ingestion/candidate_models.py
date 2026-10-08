"""Non-authoritative contracts for Semantic Ingestion Step 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Literal

from linkloom.agents.model_adapter import ModelUsage
from linkloom.decision_memory.models import decision_slot_key_for
from linkloom.semantic_ingestion.artifacts import EvidenceSpan


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXTRACTION_RESULT_VERSION = "semantic-extraction-result/v2"


def extraction_fingerprint_for(
    *,
    workspace_id: str,
    artifact_version_id: str,
    segment_id: str,
    evidence_ref: str,
    quote_sha256: str,
    config_sha256: str,
    prompt_sha256: str,
) -> str:
    payload = {
        "workspace_id": workspace_id,
        "artifact_version_id": artifact_version_id,
        "segment_id": segment_id,
        "evidence_ref": evidence_ref,
        "quote_sha256": quote_sha256,
        "config_sha256": config_sha256,
        "prompt_sha256": prompt_sha256,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def candidate_subject_key_for(subject: str | None, relation: str | None) -> str | None:
    """Build a stable slot key only when both subject and canonical relation resolve."""
    return decision_slot_key_for(subject, relation)


class ClaimType(StrEnum):
    DECISION = "DECISION"
    FACT = "FACT"
    PROPOSAL = "PROPOSAL"
    QUESTION = "QUESTION"
    REJECTED_OPTION = "REJECTED_OPTION"
    PLAN = "PLAN"
    ACTION = "ACTION"
    OPINION = "OPINION"
    SPECULATION = "SPECULATION"


class RelationResolution(StrEnum):
    CANONICAL_RELATION = "CANONICAL_RELATION"
    NEW_RELATION_CANDIDATE = "NEW_RELATION_CANDIDATE"
    UNRESOLVED_RELATION = "UNRESOLVED_RELATION"


class EntityResolution(StrEnum):
    SOURCE_GROUNDED = "SOURCE_GROUNDED"
    UNRESOLVED = "UNRESOLVED"


class TemporalResolution(StrEnum):
    SOURCE_EVENT_TIME = "SOURCE_EVENT_TIME"
    EXPLICIT = "EXPLICIT"
    UNRESOLVED = "UNRESOLVED"
    NOT_STATED = "NOT_STATED"


class TemporalBasis(StrEnum):
    EXPLICIT = "EXPLICIT"
    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"
    UNRESOLVED = "UNRESOLVED"


class CandidateOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"


class CandidateReasonCode(StrEnum):
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    MALFORMED_EXTRACTION = "MALFORMED_EXTRACTION"
    SEMANTIC_CLASS_UNRESOLVED = "SEMANTIC_CLASS_UNRESOLVED"
    ENTITY_UNRESOLVED = "ENTITY_UNRESOLVED"
    RELATION_UNRESOLVED = "RELATION_UNRESOLVED"
    TEMPORAL_UNRESOLVED = "TEMPORAL_UNRESOLVED"
    CONFLICT_REQUIRES_REVIEW = "CONFLICT_REQUIRES_REVIEW"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
    PROVIDER_REQUEST_BLOCKED = "PROVIDER_REQUEST_BLOCKED"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"


@dataclass(frozen=True, slots=True)
class CandidateConfidence:
    """Provider-reported confidence; deterministic validation gates take priority."""

    claim_type: float
    entity: float
    relation: float
    temporal: float
    overall: float

    def __post_init__(self) -> None:
        for name in ("claim_type", "entity", "relation", "temporal", "overall"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"confidence.{name} must be a number")
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"confidence.{name} must be between 0 and 1")

    def to_dict(self) -> dict[str, float]:
        return {
            "claim_type": self.claim_type,
            "entity": self.entity,
            "relation": self.relation,
            "temporal": self.temporal,
            "overall": self.overall,
        }


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    """Strict, parsed intermediate output before local candidate validation."""

    claim_type: ClaimType
    subject: str | None
    relation_phrase: str | None
    value: str | None
    temporal_status: TemporalResolution
    valid_from_raw: str | None
    valid_to_raw: str | None
    confidence: CandidateConfidence

    def __post_init__(self) -> None:
        if not isinstance(self.claim_type, ClaimType):
            object.__setattr__(self, "claim_type", ClaimType(self.claim_type))
        if not isinstance(self.temporal_status, TemporalResolution):
            object.__setattr__(self, "temporal_status", TemporalResolution(self.temporal_status))
        for name in ("subject", "relation_phrase", "value"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 512
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"{name} must be bounded non-empty text or null")
        for name in ("valid_from_raw", "valid_to_raw"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 40
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"{name} must be a bounded date/time string or null")
        if not isinstance(self.confidence, CandidateConfidence):
            raise ValueError("confidence must be CandidateConfidence")
        if self.temporal_status in {TemporalResolution.UNRESOLVED, TemporalResolution.NOT_STATED} and (
            self.valid_from_raw is not None or self.valid_to_raw is not None
        ):
            raise ValueError("unresolved or unstated temporal status cannot carry valid dates")
        if self.temporal_status is TemporalResolution.SOURCE_EVENT_TIME and (
            self.valid_from_raw is not None or self.valid_to_raw is not None
        ):
            raise ValueError("source-event time must not carry model-provided valid dates")
        if self.temporal_status is TemporalResolution.EXPLICIT and self.valid_from_raw is None:
            raise ValueError("explicit temporal status requires valid_from")

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_type": self.claim_type.value,
            "subject": self.subject,
            "relation_phrase": self.relation_phrase,
            "value": self.value,
            "temporal_status": self.temporal_status.value,
            "valid_from_raw": self.valid_from_raw,
            "valid_to_raw": self.valid_to_raw,
            "confidence": self.confidence.to_dict(),
        }


def claims_sha256_for(claims: tuple[ExtractedClaim, ...]) -> str:
    """Hash the canonical structured claim payload for replay integrity."""
    if not isinstance(claims, tuple) or any(
        not isinstance(claim, ExtractedClaim) for claim in claims
    ):
        raise ValueError("claims must be an immutable tuple of ExtractedClaim")
    encoded = json.dumps(
        [claim.to_dict() for claim in claims],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ExtractionAttempt:
    attempt_number: int
    request_sha256: str
    response_sha256: str | None
    provider_request_id: str | None
    provider_response_id: str | None
    usage: ModelUsage
    latency_ms: float
    failure_code: str | None = None
    outcome: str = "accepted_response"
    budget_reservation_id: str | None = None
    estimated_cost_upper_bound_usd: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or self.attempt_number < 1
        ):
            raise ValueError("attempt_number must be a positive integer")
        if not _SHA256.fullmatch(self.request_sha256):
            raise ValueError("request_sha256 must be a lowercase SHA-256 digest")
        if self.response_sha256 is not None and not _SHA256.fullmatch(self.response_sha256):
            raise ValueError("response_sha256 must be a lowercase SHA-256 digest or null")
        if not isinstance(self.usage, ModelUsage):
            raise ValueError("usage must be ModelUsage")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be non-negative")
        if self.outcome not in {
            "accepted_response",
            "known_provider_failure",
            "unknown_provider_outcome",
            "provider_call_exception",
            "preflight_blocked",
        }:
            raise ValueError("unsupported attempt outcome")
        if self.budget_reservation_id is not None and (
            not isinstance(self.budget_reservation_id, str)
            or not self.budget_reservation_id.strip()
        ):
            raise ValueError("budget_reservation_id must be non-empty text or null")
        if self.estimated_cost_upper_bound_usd is not None and (
            isinstance(self.estimated_cost_upper_bound_usd, bool)
            or not isinstance(self.estimated_cost_upper_bound_usd, (int, float))
            or not math.isfinite(self.estimated_cost_upper_bound_usd)
            or self.estimated_cost_upper_bound_usd < 0
        ):
            raise ValueError("estimated_cost_upper_bound_usd must be non-negative or null")
        if (self.budget_reservation_id is None) != (
            self.estimated_cost_upper_bound_usd is None
        ):
            raise ValueError("budget reservation and cost upper bound must be recorded together")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_number": self.attempt_number,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "provider_request_id": self.provider_request_id,
            "provider_response_id": self.provider_response_id,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "failure_code": self.failure_code,
            "outcome": self.outcome,
            "budget_reservation_id": self.budget_reservation_id,
            "estimated_cost_upper_bound_usd": self.estimated_cost_upper_bound_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionAttempt":
        expected = {
            "attempt_number",
            "request_sha256",
            "response_sha256",
            "provider_request_id",
            "provider_response_id",
            "usage",
            "latency_ms",
            "failure_code",
            "outcome",
            "budget_reservation_id",
            "estimated_cost_upper_bound_usd",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("ExtractionAttempt has missing or unsupported fields")
        return cls(
            attempt_number=data["attempt_number"],
            request_sha256=data["request_sha256"],
            response_sha256=data["response_sha256"],
            provider_request_id=data["provider_request_id"],
            provider_response_id=data["provider_response_id"],
            usage=ModelUsage.from_dict(data["usage"]),  # type: ignore[arg-type]
            latency_ms=data["latency_ms"],
            failure_code=data["failure_code"],
            outcome=data["outcome"],
            budget_reservation_id=data["budget_reservation_id"],
            estimated_cost_upper_bound_usd=data["estimated_cost_upper_bound_usd"],
        )


@dataclass(frozen=True, slots=True)
class ExtractionReceipt:
    provider_id: str
    model_id: str
    extractor_version: str
    schema_version: str
    temporal_policy_version: str
    request_guard_id: str
    workspace_id: str
    artifact_version_id: str
    segment_id: str
    evidence_ref: str
    quote_sha256: str
    relation_catalog_fingerprint: str
    prompt_sha256: str
    schema_sha256: str
    config_sha256: str
    extraction_fingerprint: str
    claims_sha256: str
    attempts: tuple[ExtractionAttempt, ...]
    terminal_reason: CandidateReasonCode | None = None

    def __post_init__(self) -> None:
        for name in (
            "provider_id",
            "model_id",
            "extractor_version",
            "schema_version",
            "temporal_policy_version",
            "request_guard_id",
            "workspace_id",
            "artifact_version_id",
            "segment_id",
            "evidence_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        for name in (
            "prompt_sha256",
            "schema_sha256",
            "config_sha256",
            "extraction_fingerprint",
            "claims_sha256",
            "quote_sha256",
            "relation_catalog_fingerprint",
        ):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not self.attempts:
            raise ValueError("an extraction receipt must record at least one attempt")
        if not isinstance(self.attempts, tuple) or any(
            not isinstance(item, ExtractionAttempt) for item in self.attempts
        ):
            raise ValueError("attempts must be an immutable tuple of ExtractionAttempt")
        if tuple(item.attempt_number for item in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("attempt numbers must be consecutive and ordered")
        if self.extraction_fingerprint != extraction_fingerprint_for(
            workspace_id=self.workspace_id,
            artifact_version_id=self.artifact_version_id,
            segment_id=self.segment_id,
            evidence_ref=self.evidence_ref,
            quote_sha256=self.quote_sha256,
            config_sha256=self.config_sha256,
            prompt_sha256=self.prompt_sha256,
        ):
            raise ValueError("extraction_fingerprint does not match its source/config identity")
        if self.terminal_reason is not None and not isinstance(
            self.terminal_reason, CandidateReasonCode
        ):
            object.__setattr__(self, "terminal_reason", CandidateReasonCode(self.terminal_reason))

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def request_sha256(self) -> str:
        return self.attempts[-1].request_sha256

    @property
    def response_sha256(self) -> str | None:
        return self.attempts[-1].response_sha256

    @property
    def usage(self) -> ModelUsage:
        return self.attempts[-1].usage

    @property
    def latency_ms(self) -> float:
        return sum(attempt.latency_ms for attempt in self.attempts)

    @property
    def estimated_cost_upper_bound_usd(self) -> float:
        return sum(
            attempt.estimated_cost_upper_bound_usd or 0.0
            for attempt in self.attempts
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "extractor_version": self.extractor_version,
            "schema_version": self.schema_version,
            "temporal_policy_version": self.temporal_policy_version,
            "request_guard_id": self.request_guard_id,
            "workspace_id": self.workspace_id,
            "artifact_version_id": self.artifact_version_id,
            "segment_id": self.segment_id,
            "evidence_ref": self.evidence_ref,
            "quote_sha256": self.quote_sha256,
            "relation_catalog_fingerprint": self.relation_catalog_fingerprint,
            "prompt_sha256": self.prompt_sha256,
            "schema_sha256": self.schema_sha256,
            "config_sha256": self.config_sha256,
            "extraction_fingerprint": self.extraction_fingerprint,
            "claims_sha256": self.claims_sha256,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "terminal_reason": self.terminal_reason.value if self.terminal_reason else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionReceipt":
        expected = {
            "provider_id",
            "model_id",
            "extractor_version",
            "schema_version",
            "temporal_policy_version",
            "request_guard_id",
            "workspace_id",
            "artifact_version_id",
            "segment_id",
            "evidence_ref",
            "quote_sha256",
            "relation_catalog_fingerprint",
            "prompt_sha256",
            "schema_sha256",
            "config_sha256",
            "extraction_fingerprint",
            "claims_sha256",
            "attempts",
            "terminal_reason",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("ExtractionReceipt has missing or unsupported fields")
        attempts = data["attempts"]
        if not isinstance(attempts, list):
            raise ValueError("ExtractionReceipt.attempts must be a list")
        raw_reason = data["terminal_reason"]
        return cls(
            provider_id=data["provider_id"],
            model_id=data["model_id"],
            extractor_version=data["extractor_version"],
            schema_version=data["schema_version"],
            temporal_policy_version=data["temporal_policy_version"],
            request_guard_id=data["request_guard_id"],
            workspace_id=data["workspace_id"],
            artifact_version_id=data["artifact_version_id"],
            segment_id=data["segment_id"],
            evidence_ref=data["evidence_ref"],
            quote_sha256=data["quote_sha256"],
            relation_catalog_fingerprint=data["relation_catalog_fingerprint"],
            prompt_sha256=data["prompt_sha256"],
            schema_sha256=data["schema_sha256"],
            config_sha256=data["config_sha256"],
            extraction_fingerprint=data["extraction_fingerprint"],
            claims_sha256=data["claims_sha256"],
            attempts=tuple(ExtractionAttempt.from_dict(item) for item in attempts),  # type: ignore[arg-type]
            terminal_reason=(CandidateReasonCode(raw_reason) if raw_reason is not None else None),
        )


@dataclass(frozen=True, slots=True)
class SemanticExtractionResult:
    claims: tuple[ExtractedClaim, ...]
    receipt: ExtractionReceipt
    failure_code: CandidateReasonCode | None = None
    # Product audit data is deliberately outside the canonical extraction record.
    response_evidence: dict[str, object] | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.claims, tuple) or any(
            not isinstance(claim, ExtractedClaim) for claim in self.claims
        ):
            raise ValueError("claims must be an immutable tuple of ExtractedClaim")
        if not isinstance(self.receipt, ExtractionReceipt):
            raise ValueError("receipt must be ExtractionReceipt")
        if self.failure_code is not None and not isinstance(
            self.failure_code, CandidateReasonCode
        ):
            object.__setattr__(self, "failure_code", CandidateReasonCode(self.failure_code))
        if self.failure_code is not None and self.claims:
            raise ValueError("failed extraction cannot contain claims")
        if self.failure_code != self.receipt.terminal_reason:
            raise ValueError("extraction failure and receipt terminal reason must match")
        if self.receipt.claims_sha256 != claims_sha256_for(self.claims):
            raise ValueError("claims_sha256 does not match the serialized extraction claims")

    def to_dict(self) -> dict[str, object]:
        return {
            "record_version": _EXTRACTION_RESULT_VERSION,
            "claims": [claim.to_dict() for claim in self.claims],
            "receipt": self.receipt.to_dict(),
            "failure_code": self.failure_code.value if self.failure_code else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SemanticExtractionResult":
        if not isinstance(data, dict) or set(data) != {
            "record_version",
            "claims",
            "receipt",
            "failure_code",
        }:
            raise ValueError("SemanticExtractionResult has missing or unsupported fields")
        if data["record_version"] != _EXTRACTION_RESULT_VERSION:
            raise ValueError("unsupported SemanticExtractionResult record version")
        raw_claims = data["claims"]
        if not isinstance(raw_claims, list):
            raise ValueError("SemanticExtractionResult.claims must be a list")
        claims = tuple(_claim_from_dict(item) for item in raw_claims)
        raw_failure = data["failure_code"]
        return cls(
            claims=claims,
            receipt=ExtractionReceipt.from_dict(data["receipt"]),  # type: ignore[arg-type]
            failure_code=(CandidateReasonCode(raw_failure) if raw_failure is not None else None),
        )


def _claim_from_dict(data: object) -> ExtractedClaim:
    expected = {
        "claim_type",
        "subject",
        "relation_phrase",
        "value",
        "temporal_status",
        "valid_from_raw",
        "valid_to_raw",
        "confidence",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("ExtractedClaim has missing or unsupported fields")
    raw_confidence = data["confidence"]
    if not isinstance(raw_confidence, dict) or set(raw_confidence) != {
        "claim_type",
        "entity",
        "relation",
        "temporal",
        "overall",
    }:
        raise ValueError("ExtractedClaim confidence has missing or unsupported fields")
    for key in ("subject", "relation_phrase", "value", "valid_from_raw", "valid_to_raw"):
        if data[key] is not None and not isinstance(data[key], str):
            raise ValueError(f"ExtractedClaim.{key} must be text or null")
    for key in ("claim_type", "temporal_status"):
        if not isinstance(data[key], str):
            raise ValueError(f"ExtractedClaim.{key} must be text")
    for key in ("claim_type", "entity", "relation", "temporal", "overall"):
        if isinstance(raw_confidence[key], bool) or not isinstance(raw_confidence[key], (int, float)):
            raise ValueError(f"ExtractedClaim.confidence.{key} must be numeric")
    return ExtractedClaim(
        claim_type=ClaimType(data["claim_type"]),
        subject=data["subject"],
        relation_phrase=data["relation_phrase"],
        value=data["value"],
        temporal_status=TemporalResolution(data["temporal_status"]),
        valid_from_raw=data["valid_from_raw"],
        valid_to_raw=data["valid_to_raw"],
        confidence=CandidateConfidence(
            claim_type=raw_confidence["claim_type"],
            entity=raw_confidence["entity"],
            relation=raw_confidence["relation"],
            temporal=raw_confidence["temporal"],
            overall=raw_confidence["overall"],
        ),
    )


@dataclass(frozen=True, slots=True)
class CandidateDecisionFact:
    """Source-grounded semantic proposal; never an authoritative DecisionRecord."""

    candidate_id: str
    workspace_id: str
    artifact_id: str
    artifact_version_id: str
    source_episode_id: str
    source_ref: str
    content_sha256: str
    segment_id: str
    message_id: str
    evidence_ref: str
    provenance: EvidenceSpan
    claim_type: ClaimType
    subject: str | None
    subject_key: str | None
    relation: str | None
    relation_phrase: str | None
    value: str | None
    relation_resolution: RelationResolution
    entity_resolution: EntityResolution
    event_time: datetime
    ingestion_time: datetime
    valid_from: date | datetime | None
    valid_to: date | datetime | None
    temporal_resolution: TemporalResolution
    temporal_basis: TemporalBasis
    confidence: CandidateConfidence
    extraction_fingerprint: str
    extraction_receipt: ExtractionReceipt
    review_reasons: tuple[CandidateReasonCode, ...] = ()
    candidate_status: Literal["CANDIDATE"] = "CANDIDATE"
    authority: Literal["NON_AUTHORITATIVE"] = "NON_AUTHORITATIVE"

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "workspace_id",
            "artifact_id",
            "artifact_version_id",
            "source_episode_id",
            "source_ref",
            "segment_id",
            "message_id",
            "evidence_ref",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.provenance, EvidenceSpan):
            raise ValueError("provenance must be an EvidenceSpan")
        if not isinstance(self.extraction_receipt, ExtractionReceipt):
            raise ValueError("extraction_receipt must be an ExtractionReceipt")
        if not isinstance(self.claim_type, ClaimType):
            object.__setattr__(self, "claim_type", ClaimType(self.claim_type))
        if not isinstance(self.relation_resolution, RelationResolution):
            object.__setattr__(
                self,
                "relation_resolution",
                RelationResolution(self.relation_resolution),
            )
        if not isinstance(self.entity_resolution, EntityResolution):
            object.__setattr__(self, "entity_resolution", EntityResolution(self.entity_resolution))
        if not isinstance(self.temporal_resolution, TemporalResolution):
            object.__setattr__(
                self,
                "temporal_resolution",
                TemporalResolution(self.temporal_resolution),
            )
        if not isinstance(self.temporal_basis, TemporalBasis):
            object.__setattr__(self, "temporal_basis", TemporalBasis(self.temporal_basis))
        if not isinstance(self.confidence, CandidateConfidence):
            raise ValueError("confidence must be CandidateConfidence")
        if self.subject is not None and (not isinstance(self.subject, str) or not self.subject.strip()):
            raise ValueError("subject must be non-empty text or null")
        if self.subject_key is not None and (
            not isinstance(self.subject_key, str) or not self.subject_key.strip()
        ):
            raise ValueError("subject_key must be non-empty text or null")
        expected_subject_key = candidate_subject_key_for(
            self.subject if self.entity_resolution is EntityResolution.SOURCE_GROUNDED else None,
            self.relation,
        )
        if self.subject_key != expected_subject_key:
            raise ValueError("subject_key must match the normalized subject/relation slot")
        if self.relation is not None and (not isinstance(self.relation, str) or not self.relation.strip()):
            raise ValueError("relation must be non-empty text or null")
        if self.value is not None and (not isinstance(self.value, str) or not self.value.strip()):
            raise ValueError("value must be non-empty text or null")
        if not isinstance(self.review_reasons, tuple) or any(
            not isinstance(reason, CandidateReasonCode) for reason in self.review_reasons
        ):
            raise ValueError("review_reasons must be a tuple of CandidateReasonCode")
        if self.authority != "NON_AUTHORITATIVE":
            raise ValueError("Semantic Ingestion candidates must remain non-authoritative")
        if self.candidate_status != "CANDIDATE":
            raise ValueError("Semantic Ingestion candidates must remain CANDIDATE")
        if self.temporal_basis is TemporalBasis.EXPLICIT and (
            self.temporal_resolution is not TemporalResolution.EXPLICIT
            or self.valid_from is None
        ):
            raise ValueError("explicit temporal basis requires a resolved explicit valid_from")
        if self.temporal_basis is TemporalBasis.DETERMINISTIC_RULE and (
            self.temporal_resolution is not TemporalResolution.SOURCE_EVENT_TIME
            or self.valid_from is None
        ):
            raise ValueError("deterministic temporal basis requires source event time as valid_from")
        if self.temporal_basis is TemporalBasis.UNRESOLVED and (
            self.valid_from is not None or self.valid_to is not None
        ):
            raise ValueError("unresolved temporal basis cannot carry valid-time bounds")
        if (
            self.provenance.workspace_id,
            self.provenance.artifact_id,
            self.provenance.artifact_version_id,
            self.provenance.source_ref,
            self.provenance.content_hash,
            self.provenance.segment_id,
            self.provenance.message_id,
            self.provenance.evidence_ref,
        ) != (
            self.workspace_id,
            self.artifact_id,
            self.artifact_version_id,
            self.source_ref,
            self.content_sha256,
            self.segment_id,
            self.message_id,
            self.evidence_ref,
        ):
            raise ValueError("candidate identity does not match its provenance")
        if self.extraction_fingerprint != self.extraction_receipt.extraction_fingerprint:
            raise ValueError("candidate extraction fingerprint does not match its receipt")
        for name in ("event_time", "ingestion_time"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        for name in ("valid_from", "valid_to"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, date):
                raise ValueError(f"{name} must be a date, datetime, or null")
            if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CandidateValidationResult:
    outcome: CandidateOutcome
    candidate: CandidateDecisionFact | None
    reason_codes: tuple[CandidateReasonCode, ...]
    extraction_receipt: ExtractionReceipt
    provenance_valid: bool

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, CandidateOutcome):
            object.__setattr__(self, "outcome", CandidateOutcome(self.outcome))
        if self.candidate is not None and not isinstance(self.candidate, CandidateDecisionFact):
            raise ValueError("candidate must be CandidateDecisionFact or null")
        if not isinstance(self.extraction_receipt, ExtractionReceipt):
            raise ValueError("extraction_receipt must be ExtractionReceipt")
        if not isinstance(self.provenance_valid, bool):
            raise ValueError("provenance_valid must be boolean")
        if not isinstance(self.reason_codes, tuple) or any(
            not isinstance(code, CandidateReasonCode) for code in self.reason_codes
        ):
            raise ValueError("reason_codes must be a tuple of CandidateReasonCode")
        if self.outcome is CandidateOutcome.REJECTED and self.candidate is not None:
            raise ValueError("rejected results must not expose an accepted candidate")
        if self.outcome is not CandidateOutcome.REJECTED and self.candidate is None:
            raise ValueError("accepted/unresolved results require a candidate")
        if self.outcome is CandidateOutcome.ACCEPTED and self.reason_codes:
            raise ValueError("accepted results cannot contain unresolved reason codes")
