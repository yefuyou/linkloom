"""Build non-authoritative temporal-memory candidates from grounded Agent results.

This is an application boundary only. It validates a completed TeamDecisionResult
against the source evidence captured for one run and returns an immutable
AgentMemoryCandidate. It deliberately has no store, policy, or materializer
dependency; persistence and authority transitions remain owned by Semantic
Ingestion and Temporal Decision Memory.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType

from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.decision_memory.models import decision_slot_key_for
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import (
    RawArtifact,
)
from linkloom.semantic_ingestion.evidence import VerifiedEvidenceBinding
from linkloom.semantic_ingestion.candidate_models import (
    ClaimType,
    RelationResolution,
    TemporalBasis,
    TemporalResolution,
)
from linkloom.semantic_ingestion.relations import (
    FrozenRelationResolver,
    RelationResolutionResult,
)
from linkloom.semantic_ingestion.timestamped_text import TimestampedTextSegment
from linkloom.semantic_ingestion.validation import (
    parse_explicit_valid_time,
    source_event_time_is_effective,
)


_AGENT_CANDIDATE_PREFIX = "agent_memory_candidate_v1_"
_ISO_TIME = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2}))?"
)
_EXPLICIT_TIME_INTENT = re.compile(
    r"\b(?:effective(?:\s+date)?(?:\s+(?:from|on))?|"
    r"valid\s+(?:from|on)|starting\s+(?:on|from)|"
    r"starts?\s+(?:on|from)|takes?\s+effect\s+(?:on|from))\b",
    re.IGNORECASE,
)
_EXPLICIT_TIME_VALUE = re.compile(
    r"\b(?:effective(?:\s+date)?(?:\s+(?:from|on))?|"
    r"valid\s+(?:from|on)|starting\s+(?:on|from)|"
    r"starts?\s+(?:on|from)|takes?\s+effect\s+(?:on|from))"
    rf"\s*[:=]?\s*(?P<value>{_ISO_TIME})",
    re.IGNORECASE,
)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_phrase(value: str) -> str:
    return " ".join(value.casefold().split()).strip(" .!?\t\r\n")


def _contains_phrase(phrase: str | None, source_text: str) -> bool:
    if not phrase:
        return False
    return _normalize_phrase(phrase) in _normalize_phrase(source_text)


def _temporal_identity(value: date | datetime) -> tuple[str, str]:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective timestamps must include a timezone")
        return ("datetime", value.astimezone(UTC).isoformat())
    return ("date", value.isoformat())


class AgentMemoryBuildStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"


class AgentMemoryReason(StrEnum):
    NON_DECISION_RESULT = "NON_DECISION_RESULT"
    NON_DECISION_CLAIM = "NON_DECISION_CLAIM"
    EVIDENCE_REF_UNBOUND = "EVIDENCE_REF_UNBOUND"
    SOURCE_PROVENANCE_INVALID = "SOURCE_PROVENANCE_INVALID"
    SOURCE_NOT_REGISTERED = "SOURCE_NOT_REGISTERED"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    RUN_EVIDENCE_SCOPE_INVALID = "RUN_EVIDENCE_SCOPE_INVALID"
    DECISION_EVIDENCE_SCOPE_INVALID = "DECISION_EVIDENCE_SCOPE_INVALID"
    DECISION_VALUE_UNGROUNDED = "DECISION_VALUE_UNGROUNDED"
    CLAIM_TYPE_UNRESOLVED = "CLAIM_TYPE_UNRESOLVED"
    SUBJECT_UNRESOLVED = "SUBJECT_UNRESOLVED"
    SUBJECT_NEW_REQUIRES_REVIEW = "SUBJECT_NEW_REQUIRES_REVIEW"
    SUBJECT_AMBIGUOUS = "SUBJECT_AMBIGUOUS"
    RELATION_UNRESOLVED = "RELATION_UNRESOLVED"
    TEMPORAL_UNRESOLVED = "TEMPORAL_UNRESOLVED"
    AGENT_RESULT_REQUIRES_REVIEW = "AGENT_RESULT_REQUIRES_REVIEW"
    ACTION_DESCRIPTION_UNGROUNDED = "ACTION_DESCRIPTION_UNGROUNDED"
    ACTION_OWNER_MISSING = "ACTION_OWNER_MISSING"
    ACTION_OWNER_UNGROUNDED = "ACTION_OWNER_UNGROUNDED"
    ACTION_DEADLINE_UNGROUNDED = "ACTION_DEADLINE_UNGROUNDED"


class SubjectResolutionStatus(StrEnum):
    RESOLVED_EXISTING_SUBJECT = "RESOLVED_EXISTING_SUBJECT"
    NEW_SUBJECT_CANDIDATE = "NEW_SUBJECT_CANDIDATE"
    AMBIGUOUS_SUBJECT = "AMBIGUOUS_SUBJECT"
    UNRESOLVED_SUBJECT = "UNRESOLVED_SUBJECT"


@dataclass(frozen=True, slots=True)
class WorkspaceSubject:
    """One explicitly registered subject and its human-confirmed aliases."""

    subject_id: str
    display_label: str
    confirmed_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_id", _required_text(self.subject_id, "subject_id"))
        object.__setattr__(self, "display_label", _required_text(self.display_label, "display_label"))
        if not isinstance(self.confirmed_aliases, tuple):
            raise ValueError("confirmed_aliases must be a tuple")
        aliases = tuple(_required_text(value, "confirmed_alias") for value in self.confirmed_aliases)
        if len({_normalize_phrase(value) for value in aliases}) != len(aliases):
            raise ValueError("confirmed aliases must be unique for a subject")
        object.__setattr__(self, "confirmed_aliases", aliases)

    @classmethod
    def create(
        cls,
        workspace_id: str,
        display_label: str,
        *,
        confirmed_aliases: tuple[str, ...] = (),
    ) -> "WorkspaceSubject":
        """Create an opaque, deterministic identity from a registry label."""
        workspace = _required_text(workspace_id, "workspace_id")
        label = _required_text(display_label, "display_label")
        normalized = _normalize_phrase(label)
        if not normalized:
            raise ValueError("display_label must not be empty after normalization")
        subject_id = "subject_v1_" + _sha256_json([workspace, normalized])
        return cls(subject_id, label, confirmed_aliases)


@dataclass(frozen=True, slots=True)
class SubjectResolution:
    status: SubjectResolutionStatus
    proposed_phrase: str | None
    subject_id: str | None = None
    display_label: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class WorkspaceSubjectRegistry:
    """Immutable workspace-local subject snapshot; it never learns aliases."""

    workspace_id: str
    fingerprint: str
    _subjects: Mapping[str, WorkspaceSubject]
    _aliases: Mapping[str, tuple[str, ...]]

    def __init__(self, workspace_id: str, subjects: Iterable[WorkspaceSubject] = ()) -> None:
        workspace = _required_text(workspace_id, "workspace_id")
        by_id: dict[str, WorkspaceSubject] = {}
        aliases: dict[str, set[str]] = defaultdict(set)
        for subject in subjects:
            if not isinstance(subject, WorkspaceSubject):
                raise TypeError("subjects must contain WorkspaceSubject values")
            existing = by_id.get(subject.subject_id)
            if existing is not None and existing != subject:
                raise ValueError("a subject ID cannot identify multiple registry entries")
            by_id[subject.subject_id] = subject
            for phrase in (subject.display_label, *subject.confirmed_aliases):
                normalized = _normalize_phrase(phrase)
                if normalized:
                    aliases[normalized].add(subject.subject_id)
        frozen_subjects = MappingProxyType(dict(sorted(by_id.items())))
        frozen_aliases = MappingProxyType(
            {key: tuple(sorted(values)) for key, values in sorted(aliases.items())}
        )
        fingerprint = _sha256_json(
            {
                "workspace_id": workspace,
                "subjects": [
                    {
                        "subject_id": subject.subject_id,
                        "display_label": subject.display_label,
                        "confirmed_aliases": sorted(
                            (_normalize_phrase(value) for value in subject.confirmed_aliases)
                        ),
                    }
                    for subject in frozen_subjects.values()
                ],
            }
        )
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "_subjects", frozen_subjects)
        object.__setattr__(self, "_aliases", frozen_aliases)
        object.__setattr__(self, "fingerprint", fingerprint)

    def resolve(self, phrase: str | None) -> SubjectResolution:
        if phrase is None or not isinstance(phrase, str) or not _normalize_phrase(phrase):
            return SubjectResolution(SubjectResolutionStatus.UNRESOLVED_SUBJECT, phrase)
        matches = self._aliases.get(_normalize_phrase(phrase), ())
        if not matches:
            return SubjectResolution(SubjectResolutionStatus.NEW_SUBJECT_CANDIDATE, phrase)
        if len(matches) > 1:
            return SubjectResolution(SubjectResolutionStatus.AMBIGUOUS_SUBJECT, phrase)
        subject = self._subjects[matches[0]]
        return SubjectResolution(
            SubjectResolutionStatus.RESOLVED_EXISTING_SUBJECT,
            phrase,
            subject.subject_id,
            subject.display_label,
        )

    @property
    def subjects(self) -> tuple[WorkspaceSubject, ...]:
        """Return the immutable subject snapshot used by a candidate build."""
        return tuple(self._subjects.values())


@dataclass(frozen=True, slots=True)
class AgentRunEvidence:
    """One run-visible evidence binding from any verified local source."""

    binding: VerifiedEvidenceBinding
    artifact: RawArtifact | None = None
    segment: TimestampedTextSegment | None = None
    observed_content: str | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        source: RawArtifact | VerifiedEvidenceBinding,
        segment: TimestampedTextSegment | None = None,
        *,
        observed_content: str | None = None,
    ) -> None:
        if isinstance(source, RawArtifact):
            if not isinstance(segment, TimestampedTextSegment):
                raise TypeError("Semantic Ingestion evidence requires TimestampedTextSegment")
            binding = VerifiedEvidenceBinding.from_semantic_segment(source, segment)
            object.__setattr__(self, "artifact", source)
            object.__setattr__(self, "segment", segment)
            object.__setattr__(self, "observed_content", source.content)
        elif isinstance(source, VerifiedEvidenceBinding) and segment is None:
            binding = source
            object.__setattr__(self, "artifact", None)
            object.__setattr__(self, "segment", None)
            if observed_content is not None and not isinstance(observed_content, str):
                raise TypeError("observed_content must be text or null")
            object.__setattr__(self, "observed_content", observed_content)
        else:
            raise TypeError("source must be RawArtifact with a segment or VerifiedEvidenceBinding")
        object.__setattr__(self, "binding", binding)

    @property
    def evidence_ref(self) -> str:
        return self.binding.evidence_ref

    @property
    def source_episode_id(self) -> str:
        return self.binding.source_episode_id


@dataclass(frozen=True, slots=True, init=False)
class AgentRunEvidenceCatalog:
    """Immutable catalog of exact source spans observed during one Agent run."""

    run_id: str
    _evidence: Mapping[str, AgentRunEvidence]

    def __init__(self, run_id: str, evidence: Iterable[AgentRunEvidence]) -> None:
        selected_run_id = _required_text(run_id, "run_id")
        by_ref: dict[str, AgentRunEvidence] = {}
        for item in evidence:
            if not isinstance(item, AgentRunEvidence):
                raise TypeError("evidence must contain AgentRunEvidence values")
            previous = by_ref.get(item.evidence_ref)
            if previous is not None and previous != item:
                raise ValueError("one evidence reference cannot identify multiple source spans")
            by_ref[item.evidence_ref] = item
        object.__setattr__(self, "run_id", selected_run_id)
        object.__setattr__(self, "_evidence", MappingProxyType(dict(by_ref)))
        if any(
            item.binding.run_id is not None and item.binding.run_id != selected_run_id
            for item in by_ref.values()
        ):
            raise ValueError("run-scoped evidence binding does not match catalog run_id")

    def get(self, evidence_ref: str) -> AgentRunEvidence | None:
        return self._evidence.get(evidence_ref)


@dataclass(frozen=True, slots=True)
class AgentDecisionClaimProposal:
    """Untrusted semantic slot proposal; every phrase is checked against source evidence."""

    claim_type: ClaimType
    subject_phrase: str | None
    relation_phrase: str | None
    evidence_refs: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.claim_type, ClaimType):
            object.__setattr__(self, "claim_type", ClaimType(self.claim_type))
        for name in ("subject_phrase", "relation_phrase"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_text(value, name))
        if self.evidence_refs is not None:
            if not isinstance(self.evidence_refs, tuple) or any(
                not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs
            ):
                raise ValueError("evidence_refs must be a tuple of non-empty refs or null")
            if len(set(self.evidence_refs)) != len(self.evidence_refs):
                raise ValueError("evidence_refs must not contain duplicates")


@dataclass(frozen=True, slots=True)
class AgentMemoryResolutionContext:
    workspace_id: str
    subject_registry: WorkspaceSubjectRegistry
    claim_proposal: AgentDecisionClaimProposal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _required_text(self.workspace_id, "workspace_id"))
        if not isinstance(self.subject_registry, WorkspaceSubjectRegistry):
            raise TypeError("subject_registry must be WorkspaceSubjectRegistry")
        if self.subject_registry.workspace_id != self.workspace_id:
            raise ValueError("subject registry workspace does not match resolution context")
        if self.claim_proposal is not None and not isinstance(
            self.claim_proposal, AgentDecisionClaimProposal
        ):
            raise TypeError("claim_proposal must be AgentDecisionClaimProposal or null")


@dataclass(frozen=True, slots=True)
class AgentEvidenceBinding:
    provenance: VerifiedEvidenceBinding
    claim_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, VerifiedEvidenceBinding):
            raise TypeError("provenance must be VerifiedEvidenceBinding")

    @property
    def evidence_ref(self) -> str:
        return self.provenance.evidence_ref

    @property
    def source_episode_id(self) -> str:
        return self.provenance.source_episode_id

    @property
    def artifact_id(self) -> str:
        return self.provenance.source_identity

    @property
    def artifact_version_id(self) -> str:
        return self.provenance.source_version

    @property
    def source_ref(self) -> str:
        return self.provenance.source_locator

    @property
    def content_sha256(self) -> str:
        return self.provenance.content_hash

    @property
    def source_speaker(self) -> str | None:
        return self.provenance.source_speaker

    @property
    def event_time(self) -> datetime | None:
        return self.provenance.event_time


@dataclass(frozen=True, slots=True)
class AgentActionCandidate:
    """Preserved action proposal; deliberately not an ActionRecord."""

    description: str
    owner: str | None
    deadline: str | None
    status: str
    evidence_refs: tuple[str, ...]
    description_grounded: bool
    owner_grounded: bool
    deadline_grounded: bool
    requires_review: bool


@dataclass(frozen=True, slots=True)
class AgentMemoryCandidate:
    """Aggregate, non-authoritative candidate envelope for one Agent result."""

    schema_version: str
    candidate_id: str
    fingerprint: str
    workspace_id: str
    provenance_run_id: str
    team_decision_result_sha256: str
    team_decision_result_json: str
    agent_result_status: str
    claim_type: ClaimType | None
    decision_value: str
    decision_evidence_refs: tuple[str, ...]
    evidence: tuple[AgentEvidenceBinding, ...]
    source_episode_ids: tuple[str, ...]
    subject_resolution: SubjectResolution
    subject_id: str | None
    subject_label: str | None
    subject_key: str | None
    relation_resolution: RelationResolutionResult
    relation_catalog_fingerprint: str
    subject_registry_fingerprint: str
    subject_registry_snapshot: tuple[WorkspaceSubject, ...]
    effective_time: date | datetime | None
    temporal_resolution: TemporalResolution
    temporal_basis: TemporalBasis
    actions: tuple[AgentActionCandidate, ...]
    review_reasons: tuple[AgentMemoryReason, ...]
    authorization_status: str = "NOT_AUTHORIZED"
    authority: str = "NON_AUTHORITATIVE"
    candidate_status: str = "CANDIDATE"

    def __post_init__(self) -> None:
        if self.schema_version != "agent-memory-candidate/v1":
            raise ValueError("unsupported AgentMemoryCandidate schema version")
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.provenance_run_id, "provenance_run_id")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("fingerprint must be a lowercase SHA-256 digest")
        if self.candidate_id != f"{_AGENT_CANDIDATE_PREFIX}{self.fingerprint}":
            raise ValueError("candidate ID must be derived from its stable fingerprint")
        if not re.fullmatch(r"[0-9a-f]{64}", self.team_decision_result_sha256):
            raise ValueError("TeamDecisionResult hash must be a lowercase SHA-256 digest")
        if hashlib.sha256(self.team_decision_result_json.encode("utf-8")).hexdigest() != self.team_decision_result_sha256:
            raise ValueError("TeamDecisionResult snapshot does not match its hash")
        if self.authority != "NON_AUTHORITATIVE" or self.authorization_status != "NOT_AUTHORIZED":
            raise ValueError("AgentMemoryCandidate cannot carry memory authority")
        if self.candidate_status != "CANDIDATE":
            raise ValueError("AgentMemoryCandidate must remain a candidate")
        if not isinstance(self.evidence, tuple) or not self.evidence or any(
            not isinstance(item, AgentEvidenceBinding) for item in self.evidence
        ):
            raise ValueError("evidence must be a non-empty tuple of AgentEvidenceBinding values")
        evidence_refs = tuple(item.evidence_ref for item in self.evidence)
        if len(set(evidence_refs)) != len(evidence_refs):
            raise ValueError("candidate evidence refs must be unique")
        if not set(self.decision_evidence_refs).issubset(evidence_refs):
            raise ValueError("decision evidence refs must be bound in candidate provenance")
        expected_episodes = tuple(dict.fromkeys(item.source_episode_id for item in self.evidence))
        if self.source_episode_ids != expected_episodes:
            raise ValueError("source_episode_ids must match bound candidate evidence")
        if self.claim_type is not None and not isinstance(self.claim_type, ClaimType):
            object.__setattr__(self, "claim_type", ClaimType(self.claim_type))
        if not isinstance(self.subject_resolution, SubjectResolution):
            raise TypeError("subject_resolution must be SubjectResolution")
        if not isinstance(self.subject_registry_fingerprint, str):
            raise ValueError("subject_registry_fingerprint must be text")
        if not isinstance(self.subject_registry_snapshot, tuple) or any(
            not isinstance(item, WorkspaceSubject) for item in self.subject_registry_snapshot
        ):
            raise ValueError("subject_registry_snapshot must be a tuple of WorkspaceSubject values")
        if self.subject_registry_fingerprint:
            snapshot = WorkspaceSubjectRegistry(self.workspace_id, self.subject_registry_snapshot)
            if snapshot.fingerprint != self.subject_registry_fingerprint:
                raise ValueError("subject registry snapshot does not match its fingerprint")
        elif self.subject_registry_snapshot:
            raise ValueError("unfingerprinted subject registry cannot contain subjects")
        if not isinstance(self.relation_resolution, RelationResolutionResult):
            raise TypeError("relation_resolution must be RelationResolutionResult")
        if not isinstance(self.review_reasons, tuple) or any(
            not isinstance(reason, AgentMemoryReason) for reason in self.review_reasons
        ):
            raise ValueError("review_reasons must be AgentMemoryReason values")
        if not isinstance(self.actions, tuple) or any(
            not isinstance(action, AgentActionCandidate) for action in self.actions
        ):
            raise ValueError("actions must be AgentActionCandidate values")
        if self.temporal_resolution is TemporalResolution.UNRESOLVED:
            if self.effective_time is not None or self.temporal_basis is not TemporalBasis.UNRESOLVED:
                raise ValueError("unresolved effective time cannot carry valid-time values")
        elif self.effective_time is None:
            raise ValueError("resolved effective time requires a source-derived value")
        elif isinstance(self.effective_time, datetime) and (
            self.effective_time.tzinfo is None or self.effective_time.utcoffset() is None
        ):
            raise ValueError("effective timestamps must be timezone-aware")
        if (self.subject_id is None) != (self.subject_label is None):
            raise ValueError("canonical subject ID and display label must resolve together")
        if self.subject_id is not None:
            canonical = next(
                (item for item in self.subject_registry_snapshot if item.subject_id == self.subject_id),
                None,
            )
            if canonical is None or canonical.display_label != self.subject_label:
                raise ValueError("resolved subject must match the captured workspace subject registry")
        if self.subject_key is not None and (self.subject_label is None or self.relation_resolution.canonical_relation is None):
            raise ValueError("subject key requires resolved subject and canonical relation")
        if self.subject_key is not None and self.subject_key != decision_slot_key_for(
            self.subject_label, self.relation_resolution.canonical_relation
        ):
            raise ValueError("subject key does not match existing DecisionRecord slot contract")


@dataclass(frozen=True, slots=True)
class AgentMemoryBuildResult:
    status: AgentMemoryBuildStatus
    candidate: AgentMemoryCandidate | None
    reason_codes: tuple[AgentMemoryReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentMemoryBuildStatus):
            object.__setattr__(self, "status", AgentMemoryBuildStatus(self.status))
        if self.status is AgentMemoryBuildStatus.BLOCKED and self.candidate is not None:
            raise ValueError("blocked construction must not return a candidate")
        if self.status is not AgentMemoryBuildStatus.BLOCKED and self.candidate is None:
            raise ValueError("candidate and unresolved outcomes require a candidate envelope")
        if not isinstance(self.reason_codes, tuple) or any(
            not isinstance(reason, AgentMemoryReason) for reason in self.reason_codes
        ):
            raise ValueError("reason_codes must be AgentMemoryReason values")


class WorkspaceMismatchError(PermissionError):
    """A candidate build attempted to cross the authorized workspace boundary."""


class MemoryCandidateBuilder:
    """Validate Agent claims and source provenance without persisting or authorizing."""

    def build(
        self,
        result: TeamDecisionResult,
        *,
        workspace_id: str,
        run_id: str,
        evidence_catalog: AgentRunEvidenceCatalog,
        source_registry: SourceReferenceRegistry,
        relation_resolver: FrozenRelationResolver,
        resolution_context: AgentMemoryResolutionContext | None = None,
    ) -> AgentMemoryBuildResult:
        if not isinstance(result, TeamDecisionResult):
            raise TypeError("result must be a validated TeamDecisionResult")
        workspace = _required_text(workspace_id, "workspace_id")
        run = _required_text(run_id, "run_id")
        if not isinstance(evidence_catalog, AgentRunEvidenceCatalog):
            raise TypeError("evidence_catalog must be AgentRunEvidenceCatalog")
        if not isinstance(source_registry, SourceReferenceRegistry):
            raise TypeError("source_registry must be SourceReferenceRegistry")
        if not isinstance(relation_resolver, FrozenRelationResolver):
            raise TypeError("relation_resolver must be FrozenRelationResolver")
        if evidence_catalog.run_id != run:
            return AgentMemoryBuildResult(
                AgentMemoryBuildStatus.BLOCKED,
                None,
                (AgentMemoryReason.RUN_EVIDENCE_SCOPE_INVALID,),
            )
        if resolution_context is not None:
            if not isinstance(resolution_context, AgentMemoryResolutionContext):
                raise TypeError("resolution_context must be AgentMemoryResolutionContext or null")
            if resolution_context.workspace_id != workspace:
                raise WorkspaceMismatchError("resolution context belongs to another workspace")

        if result.decision["status"] in {"insufficient_evidence", "not_found"} or not result.decision[
            "value"
        ]:
            return AgentMemoryBuildResult(
                AgentMemoryBuildStatus.BLOCKED,
                None,
                (AgentMemoryReason.NON_DECISION_RESULT,),
            )

        proposal = resolution_context.claim_proposal if resolution_context is not None else None
        if proposal is not None and proposal.claim_type not in {ClaimType.DECISION, ClaimType.FACT}:
            return AgentMemoryBuildResult(
                AgentMemoryBuildStatus.BLOCKED,
                None,
                (AgentMemoryReason.NON_DECISION_CLAIM,),
            )

        bindings_result = self._bind_evidence(
            result,
            workspace_id=workspace,
            evidence_catalog=evidence_catalog,
            source_registry=source_registry,
        )
        if isinstance(bindings_result, AgentMemoryReason):
            return AgentMemoryBuildResult(
                AgentMemoryBuildStatus.BLOCKED,
                None,
                (bindings_result,),
            )
        bindings = bindings_result
        evidence_by_ref = {item.evidence_ref: item for item in bindings}

        decision_refs = tuple(result.decision["evidence_refs"])
        proposal_refs = (
            proposal.evidence_refs
            if proposal is not None and proposal.evidence_refs is not None
            else decision_refs
        )
        if any(ref not in decision_refs or ref not in evidence_by_ref for ref in proposal_refs):
            return AgentMemoryBuildResult(
                AgentMemoryBuildStatus.BLOCKED,
                None,
                (AgentMemoryReason.DECISION_EVIDENCE_SCOPE_INVALID,),
            )
        proposal_evidence = tuple(evidence_by_ref[ref] for ref in proposal_refs)

        reasons: list[AgentMemoryReason] = []
        if not any(
            _contains_phrase(result.decision["value"], item.binding.quote)
            for item in (evidence_by_ref[ref] for ref in decision_refs)
        ):
            reasons.append(AgentMemoryReason.DECISION_VALUE_UNGROUNDED)
        claim_type = proposal.claim_type if proposal is not None else None
        subject_phrase = proposal.subject_phrase if proposal is not None else None
        relation_phrase = proposal.relation_phrase if proposal is not None else None
        if claim_type is None:
            reasons.append(AgentMemoryReason.CLAIM_TYPE_UNRESOLVED)

        subject_resolution = self._resolve_subject(
            subject_phrase,
            proposal_evidence,
            resolution_context,
        )
        if subject_resolution.status is SubjectResolutionStatus.NEW_SUBJECT_CANDIDATE:
            reasons.append(AgentMemoryReason.SUBJECT_NEW_REQUIRES_REVIEW)
        elif subject_resolution.status is SubjectResolutionStatus.AMBIGUOUS_SUBJECT:
            reasons.append(AgentMemoryReason.SUBJECT_AMBIGUOUS)
        elif subject_resolution.status is SubjectResolutionStatus.UNRESOLVED_SUBJECT:
            reasons.append(AgentMemoryReason.SUBJECT_UNRESOLVED)

        relation_resolution = self._resolve_relation(
            relation_phrase,
            proposal_evidence,
            relation_resolver,
        )
        if relation_resolution.status is not RelationResolution.CANONICAL_RELATION:
            reasons.append(AgentMemoryReason.RELATION_UNRESOLVED)

        effective_time, temporal_resolution, temporal_basis = self._resolve_effective_time(
            tuple(evidence_by_ref[ref] for ref in decision_refs)
        )
        if temporal_resolution is TemporalResolution.UNRESOLVED:
            reasons.append(AgentMemoryReason.TEMPORAL_UNRESOLVED)

        if result.decision["status"] != "approved" or result.uncertainty["status"] != "none":
            reasons.append(AgentMemoryReason.AGENT_RESULT_REQUIRES_REVIEW)

        roles = self._evidence_roles(result)
        bindings = tuple(
            AgentEvidenceBinding(
                provenance=item.binding,
                claim_roles=tuple(roles[item.evidence_ref]),
            )
            for item in (evidence_catalog.get(ref) for ref in result.evidence_refs)
            if item is not None
        )
        actions = self._map_actions(result, evidence_by_ref, reasons)
        subject_id = (
            subject_resolution.subject_id
            if subject_resolution.status is SubjectResolutionStatus.RESOLVED_EXISTING_SUBJECT
            else None
        )
        subject_label = (
            subject_resolution.display_label
            if subject_resolution.status is SubjectResolutionStatus.RESOLVED_EXISTING_SUBJECT
            else None
        )
        canonical_relation = relation_resolution.canonical_relation
        subject_key = (
            decision_slot_key_for(subject_label, canonical_relation)
            if subject_label is not None
            and relation_resolution.status is RelationResolution.CANONICAL_RELATION
            else None
        )

        result_json = _canonical_json(result.to_dict())
        result_digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        registry_fingerprint = (
            resolution_context.subject_registry.fingerprint
            if resolution_context is not None
            else ""
        )
        subject_registry_snapshot = (
            resolution_context.subject_registry.subjects
            if resolution_context is not None
            else ()
        )
        fingerprint_payload = {
            "schema_version": "agent-memory-candidate/v1",
            "workspace_id": workspace,
            "run_id": run,
            "team_decision_result_sha256": result_digest,
            "source_evidence": [
                {
                    "evidence_ref": item.evidence_ref,
                    "source_episode_id": item.source_episode_id,
                    "artifact_id": item.artifact_id,
                    "artifact_version_id": item.artifact_version_id,
                    "source_ref": item.source_ref,
                    "content_sha256": item.content_sha256,
                    "segment_id": item.provenance.segment_id,
                    "quote_sha256": item.provenance.quote_sha256,
                    "char_start": item.provenance.char_start,
                    "char_end": item.provenance.char_end,
                    "event_time": item.event_time.isoformat() if item.event_time else None,
                }
                for item in bindings
            ],
            "claim_proposal": (
                {
                    "claim_type": claim_type.value,
                    "subject_phrase": subject_phrase,
                    "relation_phrase": relation_phrase,
                    "evidence_refs": proposal_refs,
                }
                if claim_type is not None
                else None
            ),
            "subject_registry_fingerprint": registry_fingerprint,
            "subject_resolution": {
                "status": subject_resolution.status.value,
                "subject_id": subject_id,
                "display_label": subject_label,
                "proposed_phrase": subject_resolution.proposed_phrase,
            },
            "relation_catalog_fingerprint": relation_resolver.fingerprint,
            "relation_resolution": {
                "status": relation_resolution.status.value,
                "canonical_relation": canonical_relation,
                "source_phrase": relation_resolution.source_phrase,
            },
            "effective_time": {
                "value": effective_time.isoformat() if effective_time is not None else None,
                "resolution": temporal_resolution.value,
                "basis": temporal_basis.value,
            },
        }
        fingerprint = _sha256_json(fingerprint_payload)
        candidate = AgentMemoryCandidate(
            schema_version="agent-memory-candidate/v1",
            candidate_id=f"{_AGENT_CANDIDATE_PREFIX}{fingerprint}",
            fingerprint=fingerprint,
            workspace_id=workspace,
            provenance_run_id=run,
            team_decision_result_sha256=result_digest,
            team_decision_result_json=result_json,
            agent_result_status=result.decision["status"],
            claim_type=claim_type,
            decision_value=result.decision["value"],
            decision_evidence_refs=decision_refs,
            evidence=bindings,
            source_episode_ids=tuple(dict.fromkeys(item.source_episode_id for item in bindings)),
            subject_resolution=subject_resolution,
            subject_id=subject_id,
            subject_label=subject_label,
            subject_key=subject_key,
            relation_resolution=relation_resolution,
            relation_catalog_fingerprint=relation_resolver.fingerprint,
            subject_registry_fingerprint=registry_fingerprint,
            subject_registry_snapshot=subject_registry_snapshot,
            effective_time=effective_time,
            temporal_resolution=temporal_resolution,
            temporal_basis=temporal_basis,
            actions=actions,
            review_reasons=tuple(dict.fromkeys(reasons)),
        )
        return AgentMemoryBuildResult(
            AgentMemoryBuildStatus.UNRESOLVED if candidate.review_reasons else AgentMemoryBuildStatus.CANDIDATE,
            candidate,
            candidate.review_reasons,
        )

    @staticmethod
    def _bind_evidence(
        result: TeamDecisionResult,
        *,
        workspace_id: str,
        evidence_catalog: AgentRunEvidenceCatalog,
        source_registry: SourceReferenceRegistry,
    ) -> tuple[AgentRunEvidence, ...] | AgentMemoryReason:
        bound: list[AgentRunEvidence] = []
        for evidence_ref in result.evidence_refs:
            item = evidence_catalog.get(evidence_ref)
            if item is None:
                return AgentMemoryReason.EVIDENCE_REF_UNBOUND
            binding = item.binding
            if binding.workspace_id != workspace_id:
                raise WorkspaceMismatchError("source evidence belongs to another workspace")
            if binding.run_id is not None and binding.run_id != evidence_catalog.run_id:
                return AgentMemoryReason.RUN_EVIDENCE_SCOPE_INVALID
            if item.artifact is not None:
                if (
                    item.segment is None
                    or binding.semantic_span != item.segment.evidence_span
                    or not binding.verify(item.artifact)
                ):
                    return AgentMemoryReason.SOURCE_PROVENANCE_INVALID
            elif (
                item.segment is not None
                or item.observed_content is None
                or not binding.verify_source(
                    item.observed_content,
                    workspace_id=workspace_id,
                    source_identity=binding.source_identity,
                    source_version=binding.source_version,
                    source_locator=binding.source_locator,
                )
            ):
                return AgentMemoryReason.SOURCE_PROVENANCE_INVALID
            source_episode_id = item.source_episode_id
            if (
                not source_registry.has_episode(workspace_id, source_episode_id)
                or not source_registry.has_evidence(workspace_id, evidence_ref)
                or source_registry.evidence_hash(workspace_id, evidence_ref) != binding.content_hash
            ):
                return AgentMemoryReason.SOURCE_NOT_REGISTERED
            if source_registry.evidence_version(workspace_id, evidence_ref) != binding.source_version:
                return AgentMemoryReason.SOURCE_VERSION_MISMATCH
            bound.append(item)
        if not bound:
            return AgentMemoryReason.EVIDENCE_REF_UNBOUND
        return tuple(bound)

    @staticmethod
    def _resolve_subject(
        phrase: str | None,
        evidence: tuple[AgentRunEvidence, ...],
        context: AgentMemoryResolutionContext | None,
    ) -> SubjectResolution:
        if context is None or phrase is None or not any(
            _contains_phrase(phrase, item.binding.quote) for item in evidence
        ):
            return SubjectResolution(SubjectResolutionStatus.UNRESOLVED_SUBJECT, phrase)
        return context.subject_registry.resolve(phrase)

    @staticmethod
    def _resolve_relation(
        phrase: str | None,
        evidence: tuple[AgentRunEvidence, ...],
        resolver: FrozenRelationResolver,
    ) -> RelationResolutionResult:
        if phrase is None or not any(_contains_phrase(phrase, item.binding.quote) for item in evidence):
            return RelationResolutionResult(
                RelationResolution.UNRESOLVED_RELATION,
                canonical_relation=None,
                source_phrase=phrase,
            )
        return resolver.resolve(phrase)

    @staticmethod
    def _resolve_effective_time(
        evidence: tuple[AgentRunEvidence, ...],
    ) -> tuple[date | datetime | None, TemporalResolution, TemporalBasis]:
        explicit_values: list[date | datetime] = []
        malformed_explicit = False
        for item in evidence:
            quote = item.binding.quote
            has_intent = _EXPLICIT_TIME_INTENT.search(quote) is not None
            matches = tuple(_EXPLICIT_TIME_VALUE.finditer(quote))
            if has_intent and not matches:
                malformed_explicit = True
                continue
            for match in matches:
                try:
                    parsed = parse_explicit_valid_time(match.group("value"))
                except (TypeError, ValueError, OverflowError):
                    parsed = None
                if parsed is None:
                    malformed_explicit = True
                else:
                    explicit_values.append(parsed)

        if malformed_explicit:
            return None, TemporalResolution.UNRESOLVED, TemporalBasis.UNRESOLVED
        if explicit_values:
            unique = {_temporal_identity(value): value for value in explicit_values}
            if len(unique) == 1:
                return next(iter(unique.values())), TemporalResolution.EXPLICIT, TemporalBasis.EXPLICIT
            return None, TemporalResolution.UNRESOLVED, TemporalBasis.UNRESOLVED

        event_times = {
            item.binding.event_time.isoformat(): item.binding.event_time
            for item in evidence
            if item.binding.event_time is not None
            and source_event_time_is_effective(item.binding.quote)
        }
        if len(event_times) == 1:
            return next(iter(event_times.values())), TemporalResolution.SOURCE_EVENT_TIME, TemporalBasis.DETERMINISTIC_RULE
        return None, TemporalResolution.UNRESOLVED, TemporalBasis.UNRESOLVED

    @staticmethod
    def _evidence_roles(result: TeamDecisionResult) -> dict[str, list[str]]:
        roles: dict[str, list[str]] = defaultdict(list)

        def add(refs: Iterable[str], role: str) -> None:
            for ref in refs:
                if role not in roles[ref]:
                    roles[ref].append(role)

        add(result.decision["evidence_refs"], "decision")
        for index, item in enumerate(result.rationale):
            add(item["evidence_refs"], f"rationale:{index}")
        for index, item in enumerate(result.rejected_alternatives):
            add(item["evidence_refs"], f"rejected_alternative:{index}")
        for index, item in enumerate(result.actions):
            add(item["evidence_refs"], f"action:{index}")
        for index, item in enumerate(result.unresolved_items):
            add(item["evidence_refs"], f"unresolved_item:{index}")
        add(result.uncertainty["evidence_refs"], "uncertainty")
        return roles

    @staticmethod
    def _map_actions(
        result: TeamDecisionResult,
        evidence_by_ref: Mapping[str, AgentRunEvidence],
        reasons: list[AgentMemoryReason],
    ) -> tuple[AgentActionCandidate, ...]:
        mapped: list[AgentActionCandidate] = []
        source_by_ref = {ref: binding for ref, binding in evidence_by_ref.items()}
        # Use the verified source quote and its speaker, never an Agent paraphrase.
        for action in result.actions:
            refs = tuple(action["evidence_refs"])
            records = tuple(source_by_ref[ref] for ref in refs if ref in source_by_ref)
            quotes = tuple(item.binding.quote for item in records)
            description_grounded = any(
                _contains_phrase(action["description"], quote) for quote in quotes
            )
            owner = action["owner"]
            owner_grounded = owner is not None and any(
                _contains_phrase(owner, item.binding.quote)
                or (
                    item.binding.source_speaker is not None
                    and _normalize_phrase(owner) == _normalize_phrase(item.binding.source_speaker)
                )
                for item in records
            )
            deadline = action["deadline"]
            deadline_grounded = deadline is not None and any(
                deadline in quote for quote in quotes
            )
            action_reasons: list[AgentMemoryReason] = []
            if not description_grounded:
                action_reasons.append(AgentMemoryReason.ACTION_DESCRIPTION_UNGROUNDED)
            if owner is None:
                action_reasons.append(AgentMemoryReason.ACTION_OWNER_MISSING)
            elif not owner_grounded:
                action_reasons.append(AgentMemoryReason.ACTION_OWNER_UNGROUNDED)
            if deadline is not None and not deadline_grounded:
                action_reasons.append(AgentMemoryReason.ACTION_DEADLINE_UNGROUNDED)
            for reason in action_reasons:
                if reason not in reasons:
                    reasons.append(reason)
            mapped.append(
                AgentActionCandidate(
                    description=action["description"],
                    owner=owner,
                    deadline=deadline,
                    status=action["status"],
                    evidence_refs=refs,
                    description_grounded=description_grounded,
                    owner_grounded=owner_grounded,
                    deadline_grounded=deadline_grounded,
                    requires_review=bool(action_reasons),
                )
            )
        return tuple(mapped)
