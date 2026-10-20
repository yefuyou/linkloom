"""Policy, review, and authoritative materialization for validated candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from enum import StrEnum
import hashlib
import json
from typing import TYPE_CHECKING

from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.models import (
    DecisionMemoryState,
    DecisionRecord,
    DecisionStatus,
    decision_slot_key_for,
    normalize_temporal_component,
)
from linkloom.decision_memory.policy import DecisionWriteAction
from linkloom.semantic_ingestion.candidate_models import (
    CandidateDecisionFact,
    CandidateOutcome,
    CandidateReasonCode,
    CandidateValidationResult,
    ClaimType,
    EntityResolution,
    RelationResolution,
    TemporalBasis,
    TemporalResolution,
)
from linkloom.semantic_ingestion.candidate_persistence import (
    deserialize_candidate as deserialize_source_candidate,
    serialize_candidate as serialize_source_candidate,
)
from linkloom.semantic_ingestion.materializable import (
    CandidateSourceKind,
    MaterializableDecisionCandidate,
    action_mapping_for,
)

if TYPE_CHECKING:
    from linkloom.decision_memory.store import TemporalDecisionStore
    from linkloom.agents.memory_candidate import AgentMemoryCandidate


class MaterializationOutcome(StrEnum):
    AUTO_ALLOW = "AUTO_ALLOW"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"
    REJECT = "REJECT"


class CandidateWorkflowState(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    POLICY_AUTHORIZED = "POLICY_AUTHORIZED"
    MATERIALIZED = "MATERIALIZED"
    DUPLICATE = "DUPLICATE"
    SUPPORTING_ONLY = "SUPPORTING_ONLY"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    profile_id: str
    version: str
    thresholds: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.version.strip():
            raise ValueError("calibration profile identity is required")
        names = {name for name, _ in self.thresholds}
        if names != {"claim_type", "entity", "relation", "temporal", "overall"}:
            raise ValueError("calibration profile must define each confidence field")
        if len(names) != len(self.thresholds):
            raise ValueError("calibration profile confidence fields must be unique")
        for name, threshold in self.thresholds:
            if not 0.0 <= threshold <= 1.0:
                raise ValueError(f"calibration threshold {name} must be between 0 and 1")

    @property
    def fingerprint(self) -> str:
        payload = {
            "profile_id": self.profile_id,
            "version": self.version,
            "thresholds": dict(sorted(self.thresholds)),
        }
        return _sha256_json(payload)


@dataclass(frozen=True, slots=True)
class MaterializationPolicyDecision:
    outcome: MaterializationOutcome
    reason_codes: tuple[str, ...]
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    risk_level: str
    authorization_mode: str | None
    materializable_after_review: bool


@dataclass(frozen=True, slots=True)
class CandidateReviewResolution:
    """Human adjudication overlay; the captured extraction payload stays immutable."""

    subject: str | None = None
    relation: str | None = None
    valid_from: date | datetime | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("subject", "relation"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 512
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"review resolution {name} must be bounded non-empty text")
        if self.subject_id is not None and (
            not isinstance(self.subject_id, str)
            or not self.subject_id.strip()
            or len(self.subject_id) > 512
            or any(ord(character) < 32 for character in self.subject_id)
        ):
            raise ValueError("review resolution subject_id must be bounded non-empty text")
        if self.subject_id is not None and self.subject is None:
            raise ValueError("review resolution subject_id requires a subject label")
        if self.subject is None and self.relation is None and self.valid_from is None:
            raise ValueError("review resolution must resolve at least one candidate field")
        if self.valid_from is not None and not isinstance(self.valid_from, date):
            raise ValueError("review resolution valid_from must be a date or datetime")
        if isinstance(self.valid_from, datetime) and (
            self.valid_from.tzinfo is None or self.valid_from.utcoffset() is None
        ):
            raise ValueError("review resolution valid_from timestamp must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        valid_from: dict[str, str] | None
        if self.valid_from is None:
            valid_from = None
        elif isinstance(self.valid_from, datetime):
            valid_from = {"kind": "datetime", "value": self.valid_from.isoformat()}
        else:
            valid_from = {"kind": "date", "value": self.valid_from.isoformat()}
        value: dict[str, object] = {
            "subject": self.subject,
            "relation": self.relation,
            "valid_from": valid_from,
        }
        if self.subject_id is not None:
            value["subject_id"] = self.subject_id
        return value

    @classmethod
    def from_dict(cls, value: object) -> CandidateReviewResolution | None:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) not in (
            {"subject", "relation", "valid_from"},
            {"subject", "relation", "valid_from", "subject_id"},
        ):
            raise ValueError("stored candidate review resolution is malformed")
        raw_valid_from = value["valid_from"]
        valid_from: date | datetime | None = None
        if raw_valid_from is not None:
            if (
                not isinstance(raw_valid_from, dict)
                or set(raw_valid_from) != {"kind", "value"}
                or not isinstance(raw_valid_from["value"], str)
            ):
                raise ValueError("stored review valid_from is malformed")
            if raw_valid_from["kind"] == "datetime":
                valid_from = datetime.fromisoformat(raw_valid_from["value"])
            elif raw_valid_from["kind"] == "date":
                valid_from = date.fromisoformat(raw_valid_from["value"])
            else:
                raise ValueError("stored review valid_from kind is unsupported")
        return cls(
            subject=value["subject"],
            relation=value["relation"],
            valid_from=valid_from,
            subject_id=value.get("subject_id"),
        )

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class MaterializationReceipt:
    receipt_id: str
    workspace_id: str
    candidate_id: str
    candidate_fingerprint: str
    authorization_type: str
    authorization_id: str
    policy_id: str | None
    policy_version: str | None
    policy_fingerprint: str | None
    authorization_reason: str
    authorized_at: datetime
    decision_id: str
    supersedes_id: str | None
    correction_of_candidate_id: str | None
    correction_of_decision_id: str | None
    effective_at: datetime
    materialized_at: datetime
    result_state: str
    materialization_key: str
    review_resolution_fingerprint: str | None
    receipt_fingerprint: str
    candidate_source_kind: CandidateSourceKind = CandidateSourceKind.SOURCE_INGESTION
    action_dispositions: tuple["ActionMaterializationDisposition", ...] = ()


@dataclass(frozen=True, slots=True)
class ActionMaterializationDisposition:
    proposal_index: int
    status: str
    action_id: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterializationAssessment:
    candidate_id: str
    candidate_fingerprint: str
    policy_decision: MaterializationPolicyDecision
    workflow_state: CandidateWorkflowState
    receipt: MaterializationReceipt | None = None
    candidate_source_kind: CandidateSourceKind = CandidateSourceKind.SOURCE_INGESTION


class MaterializationBlockedError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MaterializationPolicy:
    """Deterministic, default-review policy for candidate promotion."""

    policy_id = "semantic-materialization"
    policy_version = "v1"

    def __init__(self, calibration: CalibrationProfile | None = None) -> None:
        self.calibration = calibration
        self.fingerprint = _sha256_json(
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "calibration_fingerprint": calibration.fingerprint if calibration else None,
            }
        )

    def evaluate(
        self,
        candidate: MaterializableDecisionCandidate | None,
        *,
        expected_workspace_id: str,
        source_verified: bool,
        existing: DecisionRecord | None,
        now: datetime,
        write_policy: DecisionMaterializer,
    ) -> MaterializationPolicyDecision:
        if candidate is None:
            return self._decision(
                MaterializationOutcome.REJECT,
                ("CANDIDATE_INVALID",),
                "HIGH",
                None,
                False,
            )
        if candidate.workspace_id != expected_workspace_id:
            return self._decision(
                MaterializationOutcome.REJECT,
                ("WORKSPACE_MISMATCH",),
                "HIGH",
                None,
                False,
            )
        if not source_verified or not candidate.provenance_valid:
            return self._decision(
                MaterializationOutcome.REJECT,
                ("PROVENANCE_INVALID",),
                "HIGH",
                None,
                False,
            )
        if candidate.claim_type not in {ClaimType.DECISION, ClaimType.FACT}:
            if (
                candidate.source_kind is CandidateSourceKind.AGENT_RESULT
                and candidate.claim_type is None
            ):
                return self._decision(
                    MaterializationOutcome.REQUIRE_REVIEW,
                    ("SEMANTIC_CLASS_UNRESOLVED",),
                    "HIGH",
                    "HUMAN_APPROVAL",
                    False,
                )
            return self._decision(
                MaterializationOutcome.REJECT,
                ("SEMANTIC_CLASS_NOT_AUTHORITATIVE",),
                "LOW",
                None,
                False,
            )

        structural_reasons = self._structural_reasons(candidate)
        if structural_reasons:
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                tuple(structural_reasons),
                "HIGH",
                "HUMAN_APPROVAL",
                False,
            )

        integrity = write_policy.policy.evaluate(
            current_value=existing.value if existing is not None else None,
            proposed_value=candidate.value or "",
            workspace_resolved=True,
            subject_resolved=bool(candidate.subject and candidate.subject_key),
            episode_resolved=True,
            evidence_resolved=True,
            team_decision_contract_pass=True,
            grounding_pass=True,
        )
        if integrity.action is DecisionWriteAction.KEEP_CANDIDATE:
            return self._decision(
                MaterializationOutcome.REJECT,
                ("CANDIDATE_INVALID", integrity.reason.upper()),
                "HIGH",
                None,
                False,
            )

        non_conflict_validation_reasons = tuple(
            code
            for code in candidate.validation_reasons
            if code != CandidateReasonCode.CONFLICT_REQUIRES_REVIEW.value
        )
        if (
            candidate.source_kind is CandidateSourceKind.SOURCE_INGESTION
            and non_conflict_validation_reasons
        ):
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                non_conflict_validation_reasons,
                "HIGH",
                "HUMAN_APPROVAL",
                False,
            )

        if existing is not None and _same_value(existing.value, candidate.value or ""):
            if candidate.source_kind is CandidateSourceKind.AGENT_RESULT:
                return self._decision(
                    MaterializationOutcome.REQUIRE_REVIEW,
                    ("DUPLICATE", "AGENT_RESULT_REQUIRES_HUMAN_APPROVAL"),
                    "LOW",
                    "HUMAN_APPROVAL",
                    True,
                )
            if self.calibration is None:
                return self._decision(
                    MaterializationOutcome.REQUIRE_REVIEW,
                    ("DUPLICATE", "CALIBRATION_NOT_AVAILABLE"),
                    "LOW",
                    "HUMAN_APPROVAL",
                    True,
                )
            return self._decision(
                MaterializationOutcome.AUTO_ALLOW,
                ("DUPLICATE",),
                "LOW",
                "POLICY_AUTHORIZATION",
                True,
            )

        risk_reasons: list[str] = []
        if existing is not None:
            if _explicit_replacement(
                _decision_evidence_text(candidate), candidate.value or "", existing.value
            ):
                risk_reasons.append("SUPERSESSION_REQUIRES_REVIEW")
            else:
                risk_reasons.append("CONFLICT_REQUIRES_REVIEW")
        if candidate.valid_from is not None and _as_utc(candidate.valid_from) > now.astimezone(UTC):
            risk_reasons.append("FUTURE_EFFECTIVE_REQUIRES_REVIEW")
        if CandidateReasonCode.CONFLICT_REQUIRES_REVIEW.value in candidate.review_reasons:
            risk_reasons.append("CONFLICT_REQUIRES_REVIEW")
        if risk_reasons:
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                tuple(dict.fromkeys(risk_reasons)),
                "HIGH",
                "HUMAN_APPROVAL",
                True,
            )

        # TeamDecisionResult has no calibrated claim-confidence vector. Its
        # approved status therefore remains evidence about the Agent run, not a
        # policy authorization for durable memory.
        if candidate.source_kind is CandidateSourceKind.AGENT_RESULT:
            if "DECISION_VALUE_UNGROUNDED" in candidate.validation_reasons:
                return self._decision(
                    MaterializationOutcome.REQUIRE_REVIEW,
                    ("DECISION_VALUE_UNGROUNDED",),
                    "HIGH",
                    "HUMAN_APPROVAL",
                    False,
                )
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                tuple(
                    dict.fromkeys(
                        (*non_conflict_validation_reasons, "AGENT_RESULT_REQUIRES_HUMAN_APPROVAL")
                    )
                ),
                "LOW",
                "HUMAN_APPROVAL",
                True,
            )
        if non_conflict_validation_reasons:
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                non_conflict_validation_reasons,
                "HIGH",
                "HUMAN_APPROVAL",
                False,
            )

        if self.calibration is None:
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                ("CALIBRATION_NOT_AVAILABLE",),
                "LOW",
                "HUMAN_APPROVAL",
                True,
            )
        thresholds = dict(self.calibration.thresholds)
        source_candidate = candidate.source_candidate
        if source_candidate is None:
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                ("CALIBRATED_CONFIDENCE_UNAVAILABLE",),
                "LOW",
                "HUMAN_APPROVAL",
                True,
            )
        scores = source_candidate.confidence.to_dict()
        below = tuple(
            f"CONFIDENCE_BELOW_THRESHOLD_{name.upper()}"
            for name, threshold in sorted(thresholds.items())
            if scores[name] < threshold
        )
        if below:
            return self._decision(
                MaterializationOutcome.REQUIRE_REVIEW,
                below,
                "MEDIUM",
                "HUMAN_APPROVAL",
                True,
            )
        return self._decision(
            MaterializationOutcome.AUTO_ALLOW,
            ("LOW_RISK_CALIBRATED_CANDIDATE",),
            "LOW",
            "POLICY_AUTHORIZATION",
            True,
        )

    def _decision(
        self,
        outcome: MaterializationOutcome,
        reason_codes: tuple[str, ...],
        risk_level: str,
        authorization_mode: str | None,
        materializable_after_review: bool,
    ) -> MaterializationPolicyDecision:
        return MaterializationPolicyDecision(
            outcome=outcome,
            reason_codes=reason_codes,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            policy_fingerprint=self.fingerprint,
            risk_level=risk_level,
            authorization_mode=authorization_mode,
            materializable_after_review=materializable_after_review,
        )

    @staticmethod
    def _structural_reasons(candidate: MaterializableDecisionCandidate) -> tuple[str, ...]:
        reasons: list[str] = []
        if not candidate.subject_resolved or candidate.subject is None or candidate.subject_key is None:
            reasons.append("ENTITY_UNRESOLVED")
        if not candidate.relation_resolved:
            reasons.append("RELATION_UNRESOLVED")
        if candidate.value is None:
            reasons.append("VALUE_UNRESOLVED")
        if not candidate.temporal_resolved or candidate.valid_from is None:
            reasons.append("TEMPORAL_UNRESOLVED")
        if not candidate.subject_slot_is_canonical:
            reasons.append("SUBJECT_SLOT_INVALID")
        return tuple(dict.fromkeys(reasons))


class SemanticDecisionMaterializer:
    """Durable candidate workflow and atomic DecisionRecord materializer."""

    def __init__(
        self,
        store: TemporalDecisionStore,
        *,
        policy: MaterializationPolicy | None = None,
        decision_materializer: DecisionMaterializer | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or MaterializationPolicy()
        self.decision_materializer = decision_materializer or DecisionMaterializer(store)
        if self.decision_materializer.store is not store:
            raise ValueError("DecisionMaterializer must use the same TemporalDecisionStore")

    def capture(
        self,
        result: CandidateValidationResult | AgentMemoryCandidate,
        *,
        expected_workspace_id: str,
        now: datetime | None = None,
    ) -> MaterializationAssessment:
        from linkloom.agents.memory_candidate import AgentMemoryCandidate
        from linkloom.agents.memory_candidate_persistence import (
            serialize_candidate as serialize_agent_candidate,
        )

        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        if isinstance(result, CandidateValidationResult):
            candidate = result.candidate
            if candidate is None:
                raise MaterializationBlockedError(
                    "CANDIDATE_INVALID", "validation produced no candidate"
                )
            source_kind = CandidateSourceKind.SOURCE_INGESTION
            payload_json, fingerprint = serialize_source_candidate(candidate)
            extraction_fingerprint = candidate.extraction_fingerprint
            validation_state = result.outcome.value
            validation_reasons = tuple(code.value for code in result.reason_codes)
            created_at = candidate.ingestion_time
            view = MaterializableDecisionCandidate.from_source_validation(
                result, candidate_fingerprint=fingerprint
            )
        elif isinstance(result, AgentMemoryCandidate):
            candidate = result
            source_kind = CandidateSourceKind.AGENT_RESULT
            payload_json, fingerprint = serialize_agent_candidate(candidate)
            extraction_fingerprint = candidate.fingerprint
            validation_reasons = tuple(reason.value for reason in candidate.review_reasons)
            validation_state = (
                CandidateOutcome.UNRESOLVED.value
                if validation_reasons
                else CandidateOutcome.ACCEPTED.value
            )
            created_at = captured_at
            view = MaterializableDecisionCandidate.from_agent_candidate(
                candidate,
                candidate_fingerprint=fingerprint,
                validation_reasons=validation_reasons,
            )
        else:
            raise TypeError(
                "result must be CandidateValidationResult or AgentMemoryCandidate"
            )
        if candidate.workspace_id != expected_workspace_id:
            raise MaterializationBlockedError(
                "WORKSPACE_MISMATCH", "candidate belongs to another workspace"
            )
        candidate_fingerprint = fingerprint
        source_verified = self._source_verified(view)
        primary = view.primary_evidence
        created = self.store.save_semantic_candidate(
            candidate_id=candidate.candidate_id,
            workspace_id=candidate.workspace_id,
            artifact_id=primary.artifact_id,
            artifact_version_id=primary.artifact_version_id,
            content_sha256=primary.content_sha256,
            extraction_fingerprint=extraction_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            payload_version=(
                "candidate-decision-fact/v1"
                if source_kind is CandidateSourceKind.SOURCE_INGESTION
                else str(json.loads(payload_json)["record_version"])
            ),
            candidate_source_kind=source_kind.value,
            validation_state=validation_state,
            validation_reasons=validation_reasons,
            payload_json=payload_json,
            created_at=created_at,
            observed_at=captured_at,
        )

        if (
            source_kind is CandidateSourceKind.SOURCE_INGESTION
            and candidate.claim_type not in {ClaimType.DECISION, ClaimType.FACT}
        ):
            state = CandidateWorkflowState.SUPPORTING_ONLY
            decision = self._evaluate(view, expected_workspace_id, source_verified, None, captured_at)
            if created:
                self.store.transition_semantic_candidate(
                    workspace_id=candidate.workspace_id,
                    candidate_id=candidate.candidate_id,
                    candidate_fingerprint=candidate_fingerprint,
                    expected_states=(CandidateWorkflowState.PENDING_REVIEW.value,),
                    new_state=state.value,
                    event_type="POLICY_DECISION",
                    actor_type="POLICY",
                    actor_id=decision.policy_id,
                    event_at=captured_at,
                    reason_code=(decision.reason_codes[0] if decision.reason_codes else None),
                    reason="candidate retained as supporting evidence under materialization policy",
                    metadata=_policy_event_metadata(decision),
                )
            persisted = self.store.get_semantic_candidate_row(
                candidate.workspace_id, candidate.candidate_id
            )
            assert persisted is not None
            receipt_row = self.store.get_semantic_receipt_row(
                candidate.workspace_id, candidate.candidate_id
            )
            return MaterializationAssessment(
                candidate.candidate_id,
                candidate_fingerprint,
                decision,
                CandidateWorkflowState(str(persisted["workflow_state"])),
                _receipt_from_row(receipt_row) if receipt_row is not None else None,
                source_kind,
            )

        existing = self._existing_at_candidate_time(view)
        decision = self._evaluate(
            view,
            expected_workspace_id,
            source_verified,
            existing,
            captured_at,
        )
        persisted = self.store.get_semantic_candidate_row(
            candidate.workspace_id, candidate.candidate_id
        )
        assert persisted is not None
        persisted_state = CandidateWorkflowState(str(persisted["workflow_state"]))
        if not created:
            receipt_row = self.store.get_semantic_receipt_row(
                candidate.workspace_id, candidate.candidate_id
            )
            return MaterializationAssessment(
                candidate.candidate_id,
                candidate_fingerprint,
                decision,
                persisted_state,
                _receipt_from_row(receipt_row) if receipt_row is not None else None,
                source_kind,
            )
        if not source_verified or decision.outcome is MaterializationOutcome.REJECT:
            reason_code = (
                decision.reason_codes[0]
                if decision.reason_codes
                else "MATERIALIZATION_REJECTED"
            )
            self.store.transition_semantic_candidate(
                workspace_id=candidate.workspace_id,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate_fingerprint,
                expected_states=(CandidateWorkflowState.PENDING_REVIEW.value,),
                new_state=CandidateWorkflowState.REJECTED.value,
                event_type="CANDIDATE_REJECTED",
                actor_type="SYSTEM",
                actor_id="semantic-materialization-policy",
                event_at=captured_at,
                reason_code=reason_code,
                reason="candidate failed deterministic provenance or materialization policy",
                metadata=_policy_event_metadata(decision),
            )
            return MaterializationAssessment(
                candidate.candidate_id,
                candidate_fingerprint,
                decision,
                CandidateWorkflowState.REJECTED,
                candidate_source_kind=source_kind,
            )
        state = (
            CandidateWorkflowState.CONFLICT
            if "CONFLICT_REQUIRES_REVIEW" in decision.reason_codes
            else CandidateWorkflowState.PENDING_REVIEW
        )
        self.store.transition_semantic_candidate(
            workspace_id=candidate.workspace_id,
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            expected_states=(CandidateWorkflowState.PENDING_REVIEW.value,),
            new_state=state.value,
            event_type="POLICY_DECISION",
            actor_type="POLICY",
            actor_id=decision.policy_id,
            event_at=captured_at,
            reason_code=(decision.reason_codes[0] if decision.reason_codes else None),
            reason="candidate disposition recorded by deterministic materialization policy",
            metadata=_policy_event_metadata(decision),
        )
        return MaterializationAssessment(
            candidate.candidate_id,
            candidate_fingerprint,
            decision,
            state,
            candidate_source_kind=source_kind,
        )

    def get_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
    ) -> CandidateDecisionFact | AgentMemoryCandidate | None:
        row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
        return _candidate_from_row(row) if row is not None else None

    def list_pending(
        self,
        workspace_id: str,
    ) -> tuple[CandidateDecisionFact | AgentMemoryCandidate, ...]:
        rows = self.store.list_semantic_candidate_rows(workspace_id)
        return tuple(
            _candidate_from_row(row)
            for row in rows
            if str(row["workflow_state"])
            in {
                CandidateWorkflowState.PENDING_REVIEW.value,
                CandidateWorkflowState.CONFLICT.value,
            }
        )

    def candidate_fingerprint(self, workspace_id: str, candidate_id: str) -> str | None:
        row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
        return str(row["candidate_fingerprint"]) if row is not None else None

    def approve(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        reviewer_id: str,
        expected_candidate_fingerprint: str,
        reason: str,
        resolution: CandidateReviewResolution | None = None,
        now: datetime | None = None,
    ) -> MaterializationAssessment:
        reviewer_id = _required_text(reviewer_id, "reviewer_id")
        reason = _required_text(reason, "review reason")
        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        candidate, fingerprint = self._load_candidate(workspace_id, candidate_id)
        if fingerprint != expected_candidate_fingerprint:
            raise MaterializationBlockedError("APPROVAL_STALE", "approval fingerprint does not match")
        resolved_candidate = _apply_view_review_resolution(candidate, resolution)
        source_verified = self._source_verified(candidate)
        existing = self._existing_at_candidate_time(resolved_candidate)
        decision = self._evaluate(
            resolved_candidate,
            workspace_id,
            source_verified,
            existing,
            captured_at,
        )
        if decision.outcome is MaterializationOutcome.REJECT or not decision.materializable_after_review:
            raise MaterializationBlockedError(
                decision.reason_codes[0] if decision.reason_codes else "MATERIALIZATION_REJECTED",
                "candidate is not eligible for approval-based materialization",
            )
        changed = self.store.transition_semantic_candidate(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            candidate_fingerprint=fingerprint,
            expected_states=(
                CandidateWorkflowState.PENDING_REVIEW.value,
                CandidateWorkflowState.CONFLICT.value,
            ),
            new_state=CandidateWorkflowState.APPROVED.value,
            event_type="HUMAN_APPROVAL",
            actor_type="HUMAN",
            actor_id=reviewer_id,
            event_at=captured_at,
            reason_code=(decision.reason_codes[0] if decision.reason_codes else None),
            reason=reason,
            metadata={
                "policy_id": decision.policy_id,
                "policy_version": decision.policy_version,
                "policy_fingerprint": decision.policy_fingerprint,
                "risk_level": decision.risk_level,
                "review_resolution": resolution.to_dict() if resolution is not None else None,
                "review_resolution_fingerprint": (
                    resolution.fingerprint if resolution is not None else None
                ),
            },
        )
        if not changed:
            row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
            if row is None:
                raise MaterializationBlockedError("WORKSPACE_MISMATCH", "candidate is unavailable")
            if str(row["workflow_state"]) == CandidateWorkflowState.APPROVED.value:
                return MaterializationAssessment(
                    candidate_id,
                    fingerprint,
                    decision,
                    CandidateWorkflowState.APPROVED,
                    candidate_source_kind=candidate.source_kind,
                )
            raise MaterializationBlockedError("APPROVAL_STALE", "candidate is no longer reviewable")
        return MaterializationAssessment(
            candidate_id,
            fingerprint,
            decision,
            CandidateWorkflowState.APPROVED,
            candidate_source_kind=candidate.source_kind,
        )

    def reject(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        reviewer_id: str,
        expected_candidate_fingerprint: str,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        reviewer_id = _required_text(reviewer_id, "reviewer_id")
        reason = _required_text(reason, "review reason")
        row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
        if row is None:
            raise MaterializationBlockedError("WORKSPACE_MISMATCH", "candidate is unavailable")
        fingerprint = str(row["candidate_fingerprint"])
        if fingerprint != expected_candidate_fingerprint:
            raise MaterializationBlockedError("APPROVAL_STALE", "review fingerprint does not match")
        if not self.store.transition_semantic_candidate(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            candidate_fingerprint=fingerprint,
            expected_states=(
                CandidateWorkflowState.PENDING_REVIEW.value,
                CandidateWorkflowState.CONFLICT.value,
            ),
            new_state=CandidateWorkflowState.REJECTED.value,
            event_type="HUMAN_REJECTION",
            actor_type="HUMAN",
            actor_id=reviewer_id,
            event_at=(now or datetime.now(UTC)).astimezone(UTC),
            reason_code="POLICY_REJECTED",
            reason=reason,
        ):
            raise MaterializationBlockedError("APPROVAL_STALE", "candidate is no longer reviewable")

    def authorize_by_policy(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        expected_candidate_fingerprint: str,
        now: datetime | None = None,
    ) -> MaterializationAssessment:
        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        candidate, fingerprint = self._load_candidate(workspace_id, candidate_id)
        if fingerprint != expected_candidate_fingerprint:
            raise MaterializationBlockedError("APPROVAL_STALE", "policy authorization is stale")
        decision = self._evaluate(
            candidate,
            workspace_id,
            self._source_verified(candidate),
            self._existing_at_candidate_time(candidate),
            captured_at,
        )
        if decision.outcome is not MaterializationOutcome.AUTO_ALLOW:
            raise MaterializationBlockedError(
                decision.reason_codes[0] if decision.reason_codes else "REVIEW_REQUIRED",
                "versioned policy did not authorize this candidate",
            )
        changed = self.store.transition_semantic_candidate(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            candidate_fingerprint=fingerprint,
            expected_states=(CandidateWorkflowState.PENDING_REVIEW.value,),
            new_state=CandidateWorkflowState.POLICY_AUTHORIZED.value,
            event_type="POLICY_AUTHORIZATION",
            actor_type="POLICY",
            actor_id=decision.policy_id,
            event_at=captured_at,
            reason_code=decision.reason_codes[0] if decision.reason_codes else None,
            reason="versioned deterministic materialization policy authorized candidate",
            metadata={
                "policy_id": decision.policy_id,
                "policy_version": decision.policy_version,
                "policy_fingerprint": decision.policy_fingerprint,
            },
        )
        if not changed:
            raise MaterializationBlockedError("APPROVAL_STALE", "candidate is no longer policy-authorizable")
        return MaterializationAssessment(
            candidate_id,
            fingerprint,
            decision,
            CandidateWorkflowState.POLICY_AUTHORIZED,
            candidate_source_kind=candidate.source_kind,
        )

    def materialize(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        now: datetime | None = None,
        correction_of_candidate_id: str | None = None,
    ) -> MaterializationAssessment:
        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        candidate, fingerprint = self._load_candidate(workspace_id, candidate_id)
        row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
        assert row is not None
        workflow_state = CandidateWorkflowState(str(row["workflow_state"]))
        receipt_row = self.store.get_semantic_receipt_row(workspace_id, candidate_id)
        auth = None
        review_resolution = None
        authorization_state = workflow_state
        if receipt_row is not None and workflow_state in {
            CandidateWorkflowState.MATERIALIZED,
            CandidateWorkflowState.DUPLICATE,
        }:
            authorization_state = (
                CandidateWorkflowState.POLICY_AUTHORIZED
                if str(receipt_row["authorization_type"]) == "POLICY_AUTHORIZATION"
                else CandidateWorkflowState.APPROVED
            )
        if authorization_state in {
            CandidateWorkflowState.APPROVED,
            CandidateWorkflowState.POLICY_AUTHORIZED,
        }:
            auth = self._authorization_from_events(workspace_id, candidate_id, authorization_state)
            review_resolution = CandidateReviewResolution.from_dict(
                auth.get("review_resolution")
            )
        resolved_candidate = _apply_view_review_resolution(candidate, review_resolution)
        decision_policy = self._evaluate(
            resolved_candidate,
            workspace_id,
            self._source_verified(candidate),
            self._existing_at_candidate_time(resolved_candidate),
            captured_at,
        )
        if receipt_row is not None:
            return MaterializationAssessment(
                candidate_id,
                fingerprint,
                decision_policy,
                workflow_state,
                _receipt_from_row(receipt_row),
                candidate.source_kind,
            )
        if workflow_state not in {
            CandidateWorkflowState.APPROVED,
            CandidateWorkflowState.POLICY_AUTHORIZED,
        }:
            raise MaterializationBlockedError("APPROVAL_MISSING", "candidate has no durable authorization")
        if not self._source_verified(candidate):
            self.store.transition_semantic_candidate(
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                candidate_fingerprint=fingerprint,
                expected_states=(workflow_state.value,),
                new_state=CandidateWorkflowState.STALE.value,
                event_type="SOURCE_CHANGED_BEFORE_MATERIALIZATION",
                actor_type="SYSTEM",
                actor_id="semantic-materializer",
                event_at=captured_at,
                reason_code="PROVENANCE_INVALID",
                reason="registered source version no longer matches candidate evidence",
            )
            raise MaterializationBlockedError("PROVENANCE_INVALID", "source version changed before write")
        if decision_policy.outcome is MaterializationOutcome.REJECT or not decision_policy.materializable_after_review:
            raise MaterializationBlockedError(
                decision_policy.reason_codes[0] if decision_policy.reason_codes else "MATERIALIZATION_REJECTED",
                "candidate no longer satisfies materialization policy",
            )

        prior = self._existing_at_candidate_time(resolved_candidate)
        if prior is not None and _same_value(prior.value, resolved_candidate.value or ""):
            decision = prior
        else:
            decision = self._decision_record(resolved_candidate, prior)
        assert auth is not None
        correction_decision_id: str | None = None
        if correction_of_candidate_id is not None:
            correction_candidate = self.store.get_semantic_candidate_row(
                workspace_id, correction_of_candidate_id
            )
            correction_row = self.store.get_semantic_receipt_row(
                workspace_id, correction_of_candidate_id
            )
            correction_event = any(
                event["event_type"] == "CORRECTS_EXTRACTION"
                and event["candidate_fingerprint"] == fingerprint
                and json.loads(str(event["metadata_json"])).get("incorrect_candidate_id")
                == correction_of_candidate_id
                for event in self.store.get_semantic_candidate_events(workspace_id, candidate_id)
            )
            if correction_candidate is None or correction_row is None or not correction_event:
                raise MaterializationBlockedError(
                    "CORRECTION_TARGET_INVALID",
                    "correction target must be an existing, materialized candidate in the same workspace and be recorded by the extraction-correction workflow",
                )
            correction_decision_id = str(correction_row["decision_id"])
        materialization_key = _materialization_key(resolved_candidate)
        _mapped_actions, action_dispositions = action_mapping_for(
            resolved_candidate,
            source_decision_id=decision.decision_id,
        )
        receipt_id = "semantic_receipt_v1_" + _sha256_json(
            [workspace_id, candidate_id, fingerprint, candidate.source_kind.value, materialization_key]
        )
        receipt_fingerprint = _sha256_json(
            [
                receipt_id,
                workspace_id,
                candidate_id,
                fingerprint,
                auth["authorization_type"],
                auth["authorization_id"],
                decision.decision_id,
                decision.supersedes_id,
                correction_of_candidate_id,
                correction_decision_id,
                _to_iso(resolved_candidate.valid_from),
                materialization_key,
                review_resolution.fingerprint if review_resolution is not None else None,
                candidate.source_kind.value,
                list(action_dispositions),
            ]
        )
        receipt = self.store.materialize_semantic_candidate(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            candidate_fingerprint=fingerprint,
            candidate_source_kind=candidate.source_kind.value,
            decision=decision,
            materialization_key=materialization_key,
            receipt_id=receipt_id,
            receipt_fingerprint=receipt_fingerprint,
            authorization_type=str(auth["authorization_type"]),
            authorization_id=str(auth["authorization_id"]),
            policy_id=decision_policy.policy_id if auth["authorization_type"] == "POLICY_AUTHORIZATION" else None,
            policy_version=decision_policy.policy_version if auth["authorization_type"] == "POLICY_AUTHORIZATION" else None,
            policy_fingerprint=decision_policy.policy_fingerprint if auth["authorization_type"] == "POLICY_AUTHORIZATION" else None,
            authorization_reason=str(auth["authorization_reason"]),
            authorized_at=auth["authorized_at"],
            materialized_at=captured_at,
            effective_at=_as_utc(resolved_candidate.valid_from),
            correction_of_candidate_id=correction_of_candidate_id,
            correction_of_decision_id=correction_decision_id,
            review_resolution_fingerprint=(
                review_resolution.fingerprint if review_resolution is not None else None
            ),
            action_dispositions=action_dispositions,
        )
        updated_row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
        assert updated_row is not None
        return MaterializationAssessment(
            candidate_id,
            fingerprint,
            decision_policy,
            CandidateWorkflowState(str(updated_row["workflow_state"])),
            _receipt_from_row(receipt),
            candidate.source_kind,
        )

    def record_extraction_correction(
        self,
        workspace_id: str,
        incorrect_candidate_id: str,
        corrected_candidate_id: str,
        *,
        reviewer_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        for selected_id in (incorrect_candidate_id, corrected_candidate_id):
            row = self.store.get_semantic_candidate_row(workspace_id, selected_id)
            if row is not None and str(row["candidate_source_kind"]) != CandidateSourceKind.SOURCE_INGESTION.value:
                raise MaterializationBlockedError(
                    "CORRECTION_KIND_UNSUPPORTED",
                    "extraction correction is defined for source-ingestion candidates",
                )
        self.store.record_extraction_correction(
            workspace_id=workspace_id,
            incorrect_candidate_id=incorrect_candidate_id,
            corrected_candidate_id=corrected_candidate_id,
            reviewer_id=_required_text(reviewer_id, "reviewer_id"),
            reason=_required_text(reason, "correction reason"),
            event_at=(now or datetime.now(UTC)).astimezone(UTC),
        )

    def _load_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
    ) -> tuple[MaterializableDecisionCandidate, str]:
        row = self.store.get_semantic_candidate_row(workspace_id, candidate_id)
        if row is None:
            raise MaterializationBlockedError("WORKSPACE_MISMATCH", "candidate is unavailable in this workspace")
        candidate, view, encoded, actual_fingerprint = _candidate_view_from_row(row)
        if (
            actual_fingerprint != str(row["candidate_fingerprint"])
            or encoded != str(row["payload_json"])
        ):
            raise MaterializationBlockedError("CANDIDATE_INVALID", "persisted candidate integrity check failed")
        return view, actual_fingerprint

    def _source_verified(self, candidate: MaterializableDecisionCandidate) -> bool:
        registry = self.store.source_registry
        if not candidate.decision_evidence_refs or any(
            ref not in candidate.evidence_by_ref() for ref in candidate.decision_evidence_refs
        ):
            return False
        for binding in candidate.evidence:
            if (
                binding.workspace_id != candidate.workspace_id
                or not registry.has_episode(candidate.workspace_id, binding.source_episode_id)
                or not registry.has_evidence(candidate.workspace_id, binding.evidence_ref)
                or registry.evidence_hash(candidate.workspace_id, binding.evidence_ref)
                != binding.content_hash
                or registry.evidence_version(candidate.workspace_id, binding.evidence_ref)
                != binding.source_version
            ):
                return False
        return True

    def _existing_at_candidate_time(
        self,
        candidate: MaterializableDecisionCandidate,
    ) -> DecisionRecord | None:
        if (
            candidate.subject is None
            or candidate.relation is None
            or candidate.subject_key is None
            or candidate.valid_from is None
            or not candidate.subject_resolved
            or not candidate.relation_resolved
        ):
            return None
        return self.store.get_as_of(
            candidate.workspace_id,
            candidate.subject_key,
            _as_utc(candidate.valid_from),
        )

    def _evaluate(
        self,
        candidate: MaterializableDecisionCandidate,
        workspace_id: str,
        source_verified: bool,
        existing: DecisionRecord | None,
        now: datetime,
    ) -> MaterializationPolicyDecision:
        return self.policy.evaluate(
            candidate,
            expected_workspace_id=workspace_id,
            source_verified=source_verified,
            existing=existing,
            now=now,
            write_policy=self.decision_materializer,
        )

    def _decision_record(
        self,
        candidate: MaterializableDecisionCandidate,
        prior: DecisionRecord | None,
    ) -> DecisionRecord:
        if (
            candidate.subject is None
            or candidate.subject_key is None
            or candidate.relation is None
            or candidate.value is None
            or candidate.valid_from is None
        ):
            raise MaterializationBlockedError("CANDIDATE_INVALID", "authoritative fields are unresolved")
        canonical_slot = decision_slot_key_for(candidate.subject, candidate.relation)
        if canonical_slot is None or canonical_slot != candidate.subject_key:
            raise MaterializationBlockedError("CANDIDATE_INVALID", "candidate decision slot is not canonical")
        if candidate.source_kind is CandidateSourceKind.SOURCE_INGESTION:
            source_candidate = candidate.source_candidate
            assert source_candidate is not None
            decision_id = "decision_semantic_v1_" + _sha256_json(
                [candidate.workspace_id, candidate.candidate_id, source_candidate.extraction_fingerprint]
            )
        else:
            decision_id = "decision_agent_v1_" + _sha256_json(
                [
                    candidate.workspace_id,
                    candidate.candidate_id,
                    candidate.identity_fingerprint,
                    candidate.candidate_fingerprint,
                ]
            )
        valid_to = _as_utc(candidate.valid_to) if candidate.valid_to is not None else None
        primary = candidate.primary_evidence
        return DecisionRecord(
            decision_id=decision_id,
            workspace_id=candidate.workspace_id,
            subject_key=canonical_slot,
            subject=candidate.subject,
            relation=candidate.relation,
            value=candidate.value,
            status=DecisionStatus.CURRENT,
            memory_state=DecisionMemoryState.ACTIVE,
            valid_from=_as_utc(candidate.valid_from),
            valid_to=valid_to,
            supersedes_id=prior.decision_id if prior is not None else None,
            source_episode_id=primary.source_episode_id,
            source_evidence_refs=candidate.decision_evidence_refs,
            provenance_run_id=candidate.provenance_run_id,
        )

    def _authorization_from_events(
        self,
        workspace_id: str,
        candidate_id: str,
        state: CandidateWorkflowState,
    ) -> dict[str, object]:
        events = self.store.get_semantic_candidate_events(workspace_id, candidate_id)
        wanted = "POLICY_AUTHORIZATION" if state is CandidateWorkflowState.POLICY_AUTHORIZED else "HUMAN_APPROVAL"
        for event in reversed(events):
            if str(event["event_type"]) == wanted:
                if str(event["candidate_fingerprint"]) != self.candidate_fingerprint(
                    workspace_id, candidate_id
                ):
                    raise MaterializationBlockedError(
                        "APPROVAL_STALE", "authorization event is bound to another candidate"
                    )
                metadata = json.loads(str(event["metadata_json"]))
                if not isinstance(metadata, dict):
                    raise MaterializationBlockedError(
                        "AUTHORIZATION_EVENT_INVALID", "authorization metadata is malformed"
                    )
                return {
                    "authorization_type": wanted,
                    "authorization_id": str(event["actor_id"]),
                    "authorization_reason": str(event["reason"]),
                    "authorized_at": datetime.fromisoformat(str(event["event_at"])),
                    **metadata,
                }
        raise MaterializationBlockedError("APPROVAL_MISSING", "authorization audit event is missing")


def _apply_review_resolution(
    validation: CandidateValidationResult,
    resolution: CandidateReviewResolution | None,
) -> CandidateValidationResult:
    if resolution is None:
        return validation
    candidate = validation.candidate
    if candidate is None:
        raise MaterializationBlockedError(
            "CANDIDATE_INVALID", "rejected extraction cannot be resolved"
        )
    if (
        resolution.subject is not None
        and candidate.entity_resolution is not EntityResolution.UNRESOLVED
    ):
        raise MaterializationBlockedError(
            "REVIEW_SUBJECT_ALREADY_RESOLVED",
            "human review can only supply an unresolved subject",
        )
    if (
        resolution.valid_from is not None
        and candidate.temporal_resolution
        not in {TemporalResolution.UNRESOLVED, TemporalResolution.NOT_STATED}
    ):
        raise MaterializationBlockedError(
            "REVIEW_TIME_ALREADY_RESOLVED",
            "human review can only supply an unresolved or unstated valid time",
        )
    if resolution.relation is not None and (
        candidate.relation_resolution is not RelationResolution.CANONICAL_RELATION
        or resolution.relation != candidate.relation
    ):
        raise MaterializationBlockedError(
            "RELATION_UNRESOLVED", "human review cannot add or rewrite the canonical relation"
        )

    subject = resolution.subject or candidate.subject
    relation = resolution.relation or candidate.relation
    entity_resolution = (
        EntityResolution.SOURCE_GROUNDED
        if resolution.subject is not None
        else candidate.entity_resolution
    )
    relation_resolution = candidate.relation_resolution
    valid_from = resolution.valid_from or candidate.valid_from
    temporal_resolution = (
        TemporalResolution.EXPLICIT
        if resolution.valid_from is not None
        else candidate.temporal_resolution
    )
    temporal_basis = (
        TemporalBasis.EXPLICIT
        if resolution.valid_from is not None
        else candidate.temporal_basis
    )
    slot_key = (
        decision_slot_key_for(subject, relation)
        if entity_resolution is EntityResolution.SOURCE_GROUNDED
        else None
    )
    resolved_codes = set()
    if resolution.subject is not None:
        resolved_codes.add(CandidateReasonCode.ENTITY_UNRESOLVED)
    if resolution.relation is not None:
        resolved_codes.add(CandidateReasonCode.RELATION_UNRESOLVED)
    if resolution.valid_from is not None:
        resolved_codes.add(CandidateReasonCode.TEMPORAL_UNRESOLVED)
    resolved_candidate = replace(
        candidate,
        subject=subject,
        relation=relation,
        subject_key=slot_key,
        entity_resolution=entity_resolution,
        relation_resolution=relation_resolution,
        valid_from=valid_from,
        temporal_resolution=temporal_resolution,
        temporal_basis=temporal_basis,
        review_reasons=tuple(code for code in candidate.review_reasons if code not in resolved_codes),
    )
    reason_codes = tuple(code for code in validation.reason_codes if code not in resolved_codes)
    return CandidateValidationResult(
        outcome=CandidateOutcome.ACCEPTED if not reason_codes else CandidateOutcome.UNRESOLVED,
        candidate=resolved_candidate,
        reason_codes=reason_codes,
        extraction_receipt=validation.extraction_receipt,
        provenance_valid=validation.provenance_valid,
    )


def _candidate_from_row(
    row: dict[str, object],
) -> CandidateDecisionFact | AgentMemoryCandidate:
    candidate, _view, encoded, fingerprint = _candidate_view_from_row(row)
    if (
        encoded != str(row["payload_json"])
        or fingerprint != str(row["candidate_fingerprint"])
        or candidate.candidate_id != str(row["candidate_id"])
        or candidate.workspace_id != str(row["workspace_id"])
    ):
        raise MaterializationBlockedError(
            "CANDIDATE_INVALID", "persisted candidate integrity check failed"
        )
    return candidate


def _candidate_view_from_row(
    row: dict[str, object],
) -> tuple[
    CandidateDecisionFact | AgentMemoryCandidate,
    MaterializableDecisionCandidate,
    str,
    str,
]:
    try:
        source_kind = CandidateSourceKind(str(row["candidate_source_kind"]))
    except (KeyError, ValueError) as error:
        raise MaterializationBlockedError(
            "CANDIDATE_INVALID", "persisted candidate source kind is unsupported"
        ) from error
    payload_json = str(row["payload_json"])
    if source_kind is CandidateSourceKind.SOURCE_INGESTION:
        if str(row["payload_version"]) != "candidate-decision-fact/v1":
            raise MaterializationBlockedError(
                "CANDIDATE_INVALID", "source-ingestion candidate payload version is unsupported"
            )
        candidate = deserialize_source_candidate(payload_json)
        encoded, fingerprint = serialize_source_candidate(candidate)
        raw_reasons = json.loads(str(row["validation_reasons_json"]))
        if not isinstance(raw_reasons, list) or any(not isinstance(item, str) for item in raw_reasons):
            raise MaterializationBlockedError("CANDIDATE_INVALID", "candidate validation reasons are malformed")
        try:
            result = CandidateValidationResult(
                outcome=CandidateOutcome(str(row["validation_state"])),
                candidate=candidate,
                reason_codes=tuple(CandidateReasonCode(item) for item in raw_reasons),
                extraction_receipt=candidate.extraction_receipt,
                provenance_valid=True,
            )
        except (ValueError, KeyError) as error:
            raise MaterializationBlockedError(
                "CANDIDATE_INVALID", "persisted source candidate validation state is malformed"
            ) from error
        view = MaterializableDecisionCandidate.from_source_validation(
            result, candidate_fingerprint=fingerprint
        )
    else:
        if str(row["payload_version"]) not in {
            "agent-memory-candidate/v1",
            "agent-memory-candidate/v2",
        }:
            raise MaterializationBlockedError(
                "CANDIDATE_INVALID", "Agent candidate payload version is unsupported"
            )
        from linkloom.agents.memory_candidate_persistence import (
            deserialize_candidate as deserialize_agent_candidate,
            serialize_candidate as serialize_agent_candidate,
        )

        candidate = deserialize_agent_candidate(payload_json)
        payload_record_version = json.loads(payload_json).get("record_version")
        if str(row["payload_version"]) != payload_record_version:
            raise MaterializationBlockedError(
                "CANDIDATE_INVALID", "Agent candidate payload version does not match its row"
            )
        if payload_record_version == "agent-memory-candidate/v1":
            # Keep historical row identity intact while the persistence reader
            # adapts its evidence into VerifiedEvidenceBinding. All new writes
            # use v2; v1 serialization is intentionally read-only.
            raw_payload = json.loads(payload_json)
            encoded = json.dumps(
                raw_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if encoded != payload_json:
                raise MaterializationBlockedError(
                    "CANDIDATE_INVALID", "persisted legacy candidate is not canonical JSON"
                )
            fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        else:
            encoded, fingerprint = serialize_agent_candidate(candidate)
        raw_reasons = json.loads(str(row["validation_reasons_json"]))
        if not isinstance(raw_reasons, list) or any(not isinstance(item, str) for item in raw_reasons):
            raise MaterializationBlockedError("CANDIDATE_INVALID", "candidate validation reasons are malformed")
        try:
            outcome = CandidateOutcome(str(row["validation_state"]))
        except ValueError as error:
            raise MaterializationBlockedError(
                "CANDIDATE_INVALID", "persisted Agent candidate validation state is malformed"
            ) from error
        expected_state = CandidateOutcome.UNRESOLVED if raw_reasons else CandidateOutcome.ACCEPTED
        if outcome is not expected_state:
            raise MaterializationBlockedError(
                "CANDIDATE_INVALID", "Agent candidate validation state does not match its reasons"
            )
        view = MaterializableDecisionCandidate.from_agent_candidate(
            candidate,
            candidate_fingerprint=fingerprint,
            validation_reasons=tuple(raw_reasons),
        )
    return candidate, view, encoded, fingerprint


def _apply_view_review_resolution(
    candidate: MaterializableDecisionCandidate,
    resolution: CandidateReviewResolution | None,
) -> MaterializableDecisionCandidate:
    if resolution is None:
        return candidate
    if candidate.source_kind is CandidateSourceKind.SOURCE_INGESTION:
        if resolution.subject_id is not None:
            raise MaterializationBlockedError(
                "REVIEW_SUBJECT_ID_UNSUPPORTED",
                "source-ingestion review does not use Agent workspace subject IDs",
            )
        source_candidate = candidate.source_candidate
        assert source_candidate is not None
        result = CandidateValidationResult(
            outcome=candidate.validation_state,
            candidate=source_candidate,
            reason_codes=tuple(CandidateReasonCode(item) for item in candidate.validation_reasons),
            extraction_receipt=source_candidate.extraction_receipt,
            provenance_valid=candidate.provenance_valid,
        )
        resolved = _apply_review_resolution(result, resolution)
        return MaterializableDecisionCandidate.from_source_validation(
            resolved,
            candidate_fingerprint=candidate.candidate_fingerprint,
        )

    agent = candidate.agent_candidate
    assert agent is not None
    from linkloom.agents.memory_candidate import AgentMemoryReason
    subject = candidate.subject
    subject_key = candidate.subject_key
    subject_resolved = candidate.subject_resolved
    if resolution.subject is not None:
        if subject_resolved:
            raise MaterializationBlockedError(
                "REVIEW_SUBJECT_ALREADY_RESOLVED", "human review cannot rewrite a resolved subject"
            )
        if resolution.subject_id is None:
            raise MaterializationBlockedError(
                "SUBJECT_IDENTITY_UNRESOLVED",
                "Agent subject review must select an existing workspace subject identity",
            )
        registered = next(
            (
                item
                for item in agent.subject_registry_snapshot
                if item.subject_id == resolution.subject_id
            ),
            None,
        )
        if registered is None or registered.display_label != resolution.subject:
            raise MaterializationBlockedError(
                "SUBJECT_IDENTITY_INVALID",
                "review subject must match a canonical subject in the captured workspace registry",
            )
        subject = registered.display_label
        subject_resolved = True
        subject_key = decision_slot_key_for(subject, candidate.relation)
    elif resolution.subject_id is not None:
        raise MaterializationBlockedError(
            "SUBJECT_IDENTITY_INVALID", "subject identity resolution is incomplete"
        )

    if resolution.relation is not None and (
        not candidate.relation_resolved or resolution.relation != candidate.relation
    ):
        raise MaterializationBlockedError(
            "RELATION_UNRESOLVED", "human review cannot add or rewrite an Agent canonical relation"
        )
    if resolution.valid_from is not None and candidate.temporal_resolved:
        raise MaterializationBlockedError(
            "REVIEW_TIME_ALREADY_RESOLVED", "human review cannot rewrite a resolved effective time"
        )

    valid_from = resolution.valid_from or candidate.valid_from
    temporal_resolution = (
        TemporalResolution.EXPLICIT
        if resolution.valid_from is not None
        else candidate.temporal_resolution
    )
    temporal_basis = (
        TemporalBasis.EXPLICIT
        if resolution.valid_from is not None
        else candidate.temporal_basis
    )
    resolved_reasons = set()
    if resolution.subject is not None:
        resolved_reasons.update(
            {
                AgentMemoryReason.SUBJECT_NEW_REQUIRES_REVIEW.value,
                AgentMemoryReason.SUBJECT_AMBIGUOUS.value,
                AgentMemoryReason.SUBJECT_UNRESOLVED.value,
            }
        )
    if resolution.valid_from is not None:
        resolved_reasons.add(AgentMemoryReason.TEMPORAL_UNRESOLVED.value)
    remaining_validation = tuple(
        reason for reason in candidate.validation_reasons if reason not in resolved_reasons
    )
    remaining_review = tuple(
        reason for reason in candidate.review_reasons if reason not in resolved_reasons
    )
    return replace(
        candidate,
        validation_state=(
            CandidateOutcome.UNRESOLVED if remaining_validation else CandidateOutcome.ACCEPTED
        ),
        validation_reasons=remaining_validation,
        subject=subject,
        subject_key=subject_key,
        subject_resolved=subject_resolved,
        temporal_resolved=(valid_from is not None),
        valid_from=valid_from,
        temporal_resolution=temporal_resolution,
        temporal_basis=temporal_basis,
        review_reasons=remaining_review,
    )


def _policy_event_metadata(decision: MaterializationPolicyDecision) -> dict[str, object]:
    return {
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "policy_fingerprint": decision.policy_fingerprint,
        "outcome": decision.outcome.value,
        "risk_level": decision.risk_level,
        "authorization_mode": decision.authorization_mode,
        "materializable_after_review": decision.materializable_after_review,
        "reason_codes": list(decision.reason_codes),
    }


def _receipt_from_row(row: dict[str, object]) -> MaterializationReceipt:
    raw_dispositions = json.loads(str(row["action_dispositions_json"]))
    if not isinstance(raw_dispositions, list):
        raise MaterializationBlockedError("RECEIPT_INVALID", "stored action mapping is malformed")
    dispositions: list[ActionMaterializationDisposition] = []
    for value in raw_dispositions:
        if not isinstance(value, dict) or set(value) != {
            "proposal_index",
            "status",
            "action_id",
            "reason_codes",
        }:
            raise MaterializationBlockedError("RECEIPT_INVALID", "stored action mapping is malformed")
        if not isinstance(value["proposal_index"], int) or isinstance(value["proposal_index"], bool):
            raise MaterializationBlockedError("RECEIPT_INVALID", "stored action index is malformed")
        if not isinstance(value["reason_codes"], list) or any(
            not isinstance(item, str) for item in value["reason_codes"]
        ):
            raise MaterializationBlockedError("RECEIPT_INVALID", "stored action reasons are malformed")
        dispositions.append(
            ActionMaterializationDisposition(
                proposal_index=value["proposal_index"],
                status=str(value["status"]),
                action_id=(str(value["action_id"]) if value["action_id"] is not None else None),
                reason_codes=tuple(value["reason_codes"]),
            )
        )
    return MaterializationReceipt(
        receipt_id=str(row["receipt_id"]),
        workspace_id=str(row["workspace_id"]),
        candidate_id=str(row["candidate_id"]),
        candidate_fingerprint=str(row["candidate_fingerprint"]),
        authorization_type=str(row["authorization_type"]),
        authorization_id=str(row["authorization_id"]),
        policy_id=str(row["policy_id"]) if row["policy_id"] is not None else None,
        policy_version=str(row["policy_version"]) if row["policy_version"] is not None else None,
        policy_fingerprint=str(row["policy_fingerprint"]) if row["policy_fingerprint"] is not None else None,
        authorization_reason=str(row["authorization_reason"]),
        authorized_at=datetime.fromisoformat(str(row["authorized_at"])).astimezone(UTC),
        decision_id=str(row["decision_id"]),
        supersedes_id=str(row["supersedes_id"]) if row["supersedes_id"] is not None else None,
        correction_of_candidate_id=str(row["correction_of_candidate_id"])
        if row["correction_of_candidate_id"] is not None
        else None,
        correction_of_decision_id=str(row["correction_of_decision_id"])
        if row["correction_of_decision_id"] is not None
        else None,
        effective_at=datetime.fromisoformat(str(row["effective_at"])).astimezone(UTC),
        materialized_at=datetime.fromisoformat(str(row["materialized_at"])).astimezone(UTC),
        result_state=str(row["result_state"]),
        materialization_key=str(row["materialization_key"]),
        review_resolution_fingerprint=(
            str(row["review_resolution_fingerprint"])
            if row["review_resolution_fingerprint"] is not None
            else None
        ),
        receipt_fingerprint=str(row["receipt_fingerprint"]),
        candidate_source_kind=CandidateSourceKind(str(row["candidate_source_kind"])),
        action_dispositions=tuple(dispositions),
    )


def _materialization_key(candidate: MaterializableDecisionCandidate) -> str:
    common = [
        candidate.workspace_id,
        candidate.subject_key,
        normalize_temporal_component(candidate.value or ""),
        _to_iso(candidate.valid_from),
    ]
    if candidate.source_kind is CandidateSourceKind.SOURCE_INGESTION:
        primary = candidate.primary_evidence
        return _sha256_json(
            [
                *common,
                primary.artifact_version_id,
                primary.evidence_ref,
                primary.content_sha256,
            ]
        )
    evidence_identity = [
        [item.artifact_version_id, item.evidence_ref, item.content_sha256]
        for item in (
            candidate.evidence_by_ref()[reference]
            for reference in candidate.decision_evidence_refs
        )
    ]
    return _sha256_json([*common, CandidateSourceKind.AGENT_RESULT.value, evidence_identity])


def _decision_evidence_text(candidate: MaterializableDecisionCandidate) -> str:
    by_ref = candidate.evidence_by_ref()
    return "\n".join(
        by_ref[reference].quote
        for reference in candidate.decision_evidence_refs
        if reference in by_ref
    )


def _to_iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _as_utc(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("valid-time timestamp must be timezone-aware")
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


def _same_value(left: str, right: str) -> bool:
    return normalize_temporal_component(left) == normalize_temporal_component(right)


def _explicit_replacement(quote: str, new_value: str, old_value: str) -> bool:
    normalized_quote = normalize_temporal_component(quote)
    new = normalize_temporal_component(new_value)
    old = normalize_temporal_component(old_value)
    phrases = (
        f"{new} replaces {old}",
        f"{new} replaced {old}",
        f"{new} supersedes {old}",
        f"{new} superseded {old}",
        f"{old} was replaced by {new}",
        f"{old} is replaced by {new}",
        f"{old} was superseded by {new}",
        f"{old} is superseded by {new}",
    )
    return any(phrase in normalized_quote for phrase in phrases)


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
