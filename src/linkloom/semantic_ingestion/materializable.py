"""Small common view over distinct source and Agent candidate payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
import hashlib
import json
from typing import TYPE_CHECKING

from linkloom.decision_memory.models import ActionRecord, decision_slot_key_for
from linkloom.semantic_ingestion.artifacts import RawArtifactSourceType
from linkloom.semantic_ingestion.evidence import VerifiedEvidenceBinding
from linkloom.semantic_ingestion.candidate_models import (
    CandidateDecisionFact,
    CandidateOutcome,
    CandidateValidationResult,
    ClaimType,
    EntityResolution,
    RelationResolution,
    TemporalBasis,
    TemporalResolution,
)

if TYPE_CHECKING:
    from linkloom.agents.memory_candidate import AgentMemoryCandidate


class CandidateSourceKind(StrEnum):
    SOURCE_INGESTION = "SOURCE_INGESTION"
    AGENT_RESULT = "AGENT_RESULT"


@dataclass(frozen=True, slots=True)
class MaterializableDecisionCandidate:
    """Lifecycle-only projection; the persisted source payload stays type-specific."""

    candidate: CandidateDecisionFact | AgentMemoryCandidate
    source_kind: CandidateSourceKind
    candidate_fingerprint: str
    validation_state: CandidateOutcome
    validation_reasons: tuple[str, ...]
    provenance_valid: bool
    claim_type: ClaimType | None
    subject: str | None
    subject_key: str | None
    relation: str | None
    value: str | None
    subject_resolved: bool
    relation_resolved: bool
    temporal_resolved: bool
    valid_from: date | datetime | None
    valid_to: date | datetime | None
    temporal_resolution: TemporalResolution
    temporal_basis: TemporalBasis
    evidence: tuple[VerifiedEvidenceBinding, ...]
    decision_evidence_refs: tuple[str, ...]
    provenance_run_id: str
    identity_fingerprint: str
    review_reasons: tuple[str, ...]

    @classmethod
    def from_source_validation(
        cls,
        result: CandidateValidationResult,
        *,
        candidate_fingerprint: str,
    ) -> "MaterializableDecisionCandidate":
        candidate = result.candidate
        if candidate is None:
            raise ValueError("a materializable view requires a persisted candidate")
        span = candidate.provenance
        binding = VerifiedEvidenceBinding(
            workspace_id=candidate.workspace_id,
            evidence_ref=candidate.evidence_ref,
            source_episode_id=candidate.source_episode_id,
            source_identity=candidate.artifact_id,
            source_version=candidate.artifact_version_id,
            content_hash=candidate.content_sha256,
            source_type=RawArtifactSourceType.TIMESTAMPED_TEXT.value,
            source_locator=candidate.source_ref,
            quote=span.quote,
            char_start=span.char_start,
            char_end=span.char_end,
            line_start=span.line_start,
            line_end=span.line_end,
            event_time=candidate.event_time,
            semantic_span=span,
            verification_kind="SEMANTIC_EVIDENCE_SPAN_V1",
            quote_sha256=span.quote_sha256,
        )
        return cls(
            candidate=candidate,
            source_kind=CandidateSourceKind.SOURCE_INGESTION,
            candidate_fingerprint=candidate_fingerprint,
            validation_state=result.outcome,
            validation_reasons=tuple(code.value for code in result.reason_codes),
            provenance_valid=result.provenance_valid,
            claim_type=candidate.claim_type,
            subject=candidate.subject,
            subject_key=candidate.subject_key,
            relation=candidate.relation,
            value=candidate.value,
            subject_resolved=(candidate.entity_resolution is EntityResolution.SOURCE_GROUNDED),
            relation_resolved=(candidate.relation_resolution is RelationResolution.CANONICAL_RELATION),
            temporal_resolved=(
                candidate.temporal_resolution not in {
                    TemporalResolution.UNRESOLVED,
                    TemporalResolution.NOT_STATED,
                }
                and candidate.valid_from is not None
                and candidate.temporal_basis is not TemporalBasis.UNRESOLVED
            ),
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
            temporal_resolution=candidate.temporal_resolution,
            temporal_basis=candidate.temporal_basis,
            evidence=(binding,),
            decision_evidence_refs=(candidate.evidence_ref,),
            provenance_run_id=candidate.extraction_fingerprint,
            identity_fingerprint=candidate.extraction_fingerprint,
            review_reasons=tuple(reason.value for reason in candidate.review_reasons),
        )

    @classmethod
    def from_agent_candidate(
        cls,
        candidate: AgentMemoryCandidate,
        *,
        candidate_fingerprint: str,
        validation_reasons: tuple[str, ...] | None = None,
    ) -> "MaterializableDecisionCandidate":
        if not _is_agent_candidate(candidate):
            raise TypeError("candidate must be AgentMemoryCandidate")
        evidence = tuple(item.provenance for item in candidate.evidence)
        reasons = (
            validation_reasons
            if validation_reasons is not None
            else tuple(reason.value for reason in candidate.review_reasons)
        )
        subject_resolved = (
            candidate.subject_id is not None
            and candidate.subject_label is not None
            and candidate.subject_resolution.status.value == "RESOLVED_EXISTING_SUBJECT"
        )
        relation_resolved = (
            candidate.relation_resolution.status is RelationResolution.CANONICAL_RELATION
            and candidate.relation_resolution.canonical_relation is not None
        )
        temporal_resolved = (
            candidate.effective_time is not None
            and candidate.temporal_resolution not in {
                TemporalResolution.UNRESOLVED,
                TemporalResolution.NOT_STATED,
            }
            and candidate.temporal_basis is not TemporalBasis.UNRESOLVED
        )
        return cls(
            candidate=candidate,
            source_kind=CandidateSourceKind.AGENT_RESULT,
            candidate_fingerprint=candidate_fingerprint,
            validation_state=(
                CandidateOutcome.UNRESOLVED if reasons else CandidateOutcome.ACCEPTED
            ),
            validation_reasons=tuple(reasons),
            provenance_valid=True,
            claim_type=candidate.claim_type,
            subject=candidate.subject_label,
            subject_key=candidate.subject_key,
            relation=candidate.relation_resolution.canonical_relation,
            value=candidate.decision_value or None,
            subject_resolved=subject_resolved,
            relation_resolved=relation_resolved,
            temporal_resolved=temporal_resolved,
            valid_from=candidate.effective_time,
            valid_to=None,
            temporal_resolution=candidate.temporal_resolution,
            temporal_basis=candidate.temporal_basis,
            evidence=evidence,
            decision_evidence_refs=candidate.decision_evidence_refs,
            provenance_run_id=candidate.provenance_run_id,
            identity_fingerprint=candidate.fingerprint,
            review_reasons=tuple(reason.value for reason in candidate.review_reasons),
        )

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def workspace_id(self) -> str:
        return self.candidate.workspace_id

    @property
    def primary_evidence(self) -> VerifiedEvidenceBinding:
        by_ref = {item.evidence_ref: item for item in self.evidence}
        try:
            return by_ref[self.decision_evidence_refs[0]]
        except (IndexError, KeyError) as error:
            raise ValueError("candidate has no bound decision evidence") from error

    def evidence_by_ref(self) -> dict[str, VerifiedEvidenceBinding]:
        return {item.evidence_ref: item for item in self.evidence}

    @property
    def agent_candidate(self) -> AgentMemoryCandidate | None:
        return self.candidate if _is_agent_candidate(self.candidate) else None

    @property
    def source_candidate(self) -> CandidateDecisionFact | None:
        return self.candidate if isinstance(self.candidate, CandidateDecisionFact) else None

    @property
    def subject_slot_is_canonical(self) -> bool:
        return self.subject_key is not None and self.subject_key == decision_slot_key_for(
            self.subject, self.relation
        )


def action_mapping_for(
    candidate: MaterializableDecisionCandidate,
    *,
    source_decision_id: str,
) -> tuple[tuple[ActionRecord, ...], tuple[dict[str, object], ...]]:
    """Map only fully grounded Agent actions and report every retained proposal."""
    agent = candidate.agent_candidate
    if agent is None:
        return (), ()
    evidence_by_ref = candidate.evidence_by_ref()
    actions: list[ActionRecord] = []
    dispositions: list[dict[str, object]] = []
    for index, proposal in enumerate(agent.actions):
        reasons: list[str] = []
        if not proposal.description_grounded:
            reasons.append("ACTION_DESCRIPTION_UNGROUNDED")
        if proposal.owner is None or not proposal.owner.strip():
            reasons.append("ACTION_OWNER_MISSING")
        elif not proposal.owner_grounded:
            reasons.append("ACTION_OWNER_UNGROUNDED")
        if proposal.deadline is not None and not proposal.deadline_grounded:
            reasons.append("ACTION_DEADLINE_UNGROUNDED")
        if not proposal.evidence_refs or any(ref not in evidence_by_ref for ref in proposal.evidence_refs):
            reasons.append("ACTION_EVIDENCE_UNBOUND")
        if reasons:
            dispositions.append(
                {
                    "proposal_index": index,
                    "status": "RETAINED_CANDIDATE",
                    "action_id": None,
                    "reason_codes": reasons,
                }
            )
            continue
        action_id = "agent_action_v1_" + hashlib.sha256(
            json.dumps(
                [candidate.workspace_id, candidate.candidate_id, candidate.candidate_fingerprint, index],
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        action = ActionRecord(
            action_id=action_id,
            workspace_id=candidate.workspace_id,
            description=proposal.description,
            owner=proposal.owner or "",
            deadline=proposal.deadline,
            status=proposal.status,
            source_decision_id=source_decision_id,
            source_evidence_refs=proposal.evidence_refs,
        )
        actions.append(action)
        dispositions.append(
            {
                "proposal_index": index,
                "status": "MATERIALIZED",
                "action_id": action_id,
                "reason_codes": [],
            }
        )
    return tuple(actions), tuple(dispositions)


def _is_agent_candidate(candidate: object) -> bool:
    from linkloom.agents.memory_candidate import AgentMemoryCandidate

    return isinstance(candidate, AgentMemoryCandidate)
