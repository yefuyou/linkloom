"""Source-bound SAME_TASK assessments; no task merging or decision authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import ClassVar

from linkloom.semantic_ingestion.artifacts import EvidenceSpan, RawArtifact
from linkloom.semantic_ingestion.task_observation import TaskObservation


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def _strict(data, names):
    if not isinstance(data, dict) or set(data) != set(names):
        raise ValueError("missing or unsupported identity contract fields")
    return dict(data)


@dataclass(frozen=True, slots=True)
class ObservationRef:
    workspace_id: str
    observation_id: str
    artifact_id: str
    artifact_version_id: str
    evidence_ref: str
    content_hash: str
    payload_hash: str

    def __post_init__(self):
        if any(not isinstance(getattr(self, f.name), str) or not getattr(self, f.name).strip()
               for f in fields(self)):
            raise ValueError("empty observation reference")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in (self.content_hash, self.payload_hash)):
            raise ValueError("invalid observation hash")

    @classmethod
    def from_observation(cls, observation: TaskObservation):
        data = observation.to_dict()
        data.pop("recorded_at")
        span = observation.provenance
        return cls(span.workspace_id, observation.observation_id, span.artifact_id, span.artifact_version_id,
                   span.evidence_ref, span.content_hash, canonical_hash(data))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**_strict(data, (f.name for f in fields(cls))))


@dataclass(frozen=True, slots=True)
class EvidenceQuote:
    """Exact subrange of an existing EvidenceSpan; Unicode end-exclusive offsets."""
    span: EvidenceSpan
    char_start: int
    char_end: int
    quote: str
    role: str

    def __post_init__(self):
        if not isinstance(self.span, EvidenceSpan) or self.role not in {
            "TASK_KEY", "PROJECT_SCOPE", "TASK_NAMESPACE", "IDENTITY_ASSERTION",
            "IDENTITY_COUNTER", "UNRESOLVED_REFERENCE", "AMBIGUITY",
            "RELATION_PROPOSAL", "PROJECT_BINDING", "PROJECT_ALIAS", "WORK_LINK", "DIRECT_IMPACT", "CHANGE_CLAIM",
        }:
            raise ValueError("invalid identity evidence role/span")
        if (type(self.char_start) is not int or type(self.char_end) is not int
            or not isinstance(self.quote, str) or not self.quote
            or self.char_start < self.span.char_start or self.char_end > self.span.char_end
            or self.char_end - self.char_start != len(self.quote)
            or self.span.quote[self.char_start - self.span.char_start:self.char_end - self.span.char_start] != self.quote):
            raise ValueError("identity quote does not match its exact source range")

    def verify(self, artifact: RawArtifact) -> bool:
        return self.span.verify(artifact) and artifact.content[self.char_start:self.char_end] == self.quote

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        values = _strict(data, (f.name for f in fields(cls)))
        values["span"] = EvidenceSpan(**values["span"])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class SourceDependency:
    observation: ObservationRef | None
    span: EvidenceSpan
    source_status: str
    readback_ok: bool
    observation_valid: bool

    def __post_init__(self):
        if (self.observation is not None and not isinstance(self.observation, ObservationRef)
            or not isinstance(self.span, EvidenceSpan)
            or self.source_status not in {"CURRENT", "NOT_CURRENT", "MISSING", "INVALID"}
            or type(self.readback_ok) is not bool or type(self.observation_valid) is not bool):
            raise ValueError("invalid source dependency")
        if self.observation is not None and (self.observation.workspace_id, self.observation.artifact_id, self.observation.artifact_version_id,
            self.observation.evidence_ref, self.observation.content_hash) != (
                self.span.workspace_id, self.span.artifact_id, self.span.artifact_version_id,
                self.span.evidence_ref, self.span.content_hash):
            raise ValueError("dependency reference does not match source identity")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        values = _strict(data, (f.name for f in fields(cls)))
        values["observation"] = ObservationRef.from_dict(values["observation"]) if values["observation"] is not None else None
        values["span"] = EvidenceSpan(**values["span"])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ProjectRef:
    workspace_id: str
    name: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in (self.workspace_id, self.name)):
            raise ValueError("invalid scoped project reference")

    def to_dict(self):
        return {"endpoint_type": "PROJECT", **asdict(self)}


@dataclass(frozen=True, slots=True)
class ChangeRef:
    workspace_id: str
    project: str
    before_vendor: str
    after_vendor: str
    evidence: EvidenceQuote
    kind: str = "SOURCE_CLAIM"
    decision_id: str | None = None
    candidate_id: str | None = None
    receipt_id: str | None = None

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in
               (self.workspace_id, self.project, self.before_vendor, self.after_vendor)):
            raise ValueError("invalid change scope")
        if self.kind not in {"SOURCE_CLAIM", "REVIEWED_DECISION"} or not isinstance(self.evidence, EvidenceQuote):
            raise ValueError("invalid change authority/evidence")
        if self.before_vendor == self.after_vendor:
            raise ValueError('a supplier change requires different values')
        refs = (self.decision_id, self.candidate_id, self.receipt_id)
        if self.kind == "SOURCE_CLAIM" and any(v is not None for v in refs):
            raise ValueError("source claim has no approval authority")
        if self.kind == "REVIEWED_DECISION" and any(not isinstance(v, str) or not v.strip() for v in refs):
            raise ValueError("reviewed change requires durable references; these must still be read back")

    def to_dict(self):
        return {"endpoint_type": "CHANGE", **asdict(self)}


def _endpoint(data):
    if data is None:
        return None
    if data.get("endpoint_type") == "PROJECT":
        return ProjectRef(**_strict({k: v for k, v in data.items() if k != "endpoint_type"}, (f.name for f in fields(ProjectRef))))
    if data.get("endpoint_type") == "CHANGE":
        values = _strict({k: v for k, v in data.items() if k != "endpoint_type"}, (f.name for f in fields(ChangeRef)))
        values["evidence"] = EvidenceQuote.from_dict(values["evidence"])
        return ChangeRef(**values)
    return ObservationRef.from_dict(data)


@dataclass(frozen=True, slots=True)
class RelationAssessment:
    subject: ObservationRef | ProjectRef | None
    object: ObservationRef | ProjectRef | ChangeRef | None
    verdict: str
    reason_codes: tuple[str, ...]
    observations: tuple[TaskObservation | None, TaskObservation | None]
    dependencies: tuple[SourceDependency, ...]
    supporting_evidence: tuple[EvidenceQuote, ...] = ()
    counter_evidence: tuple[EvidenceQuote, ...] = ()
    context_evidence: tuple[EvidenceQuote, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    assessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    predicate: str = "SAME_TASK"
    relation_detail: str | None = None
    premise_ids: tuple[str, ...] = ()
    authority_checks: tuple[str, ...] = ()
    assessment_id: str = field(init=False)

    schema_version: ClassVar[str] = "task-relation-assessment/v1"
    rule_version: ClassVar[str] = "same-task-rules/v1"

    def __post_init__(self):
        shapes = {"SAME_TASK": (ObservationRef, ObservationRef), "RELATED_TO": (ObservationRef, ObservationRef),
                  "IN_PROJECT": (ObservationRef, ProjectRef), "PROJECT_ALIAS": (ProjectRef, ProjectRef),
                  "AFFECTED_BY": (ObservationRef, ChangeRef)}
        if self.predicate not in shapes:
            raise ValueError("relation predicate not implemented")
        if self.predicate == 'SAME_TASK' and (self.relation_detail is not None or self.premise_ids or self.authority_checks):
            raise ValueError('P1 assessments do not contain P2 metadata')
        for name in ('premise_ids', 'authority_checks'):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('invalid relation metadata')
            object.__setattr__(self, name, tuple(sorted(set(values))))
        if self.relation_detail is not None and (not isinstance(self.relation_detail, str) or not self.relation_detail.strip()):
            raise ValueError('invalid relation detail')
        if any(ref is not None and not isinstance(ref, kind) for ref, kind in zip((self.subject, self.object), shapes[self.predicate])):
            raise ValueError("invalid typed relation endpoints")
        if self.verdict not in {"SUPPORTED", "REFUTED", "UNKNOWN", "CONFLICT", "NOT_ASSESSABLE"}:
            raise ValueError("unsupported identity verdict")
        if (not isinstance(self.assessed_at, datetime) or self.assessed_at.tzinfo is None
            or self.assessed_at.utcoffset() is None):
            raise ValueError("assessment time must be timezone-aware")
        if not isinstance(self.observations, tuple) or len(self.observations) != 2:
            raise ValueError("identity assessment needs two observation slots")
        for reference, observation in zip((self.subject, self.object), self.observations):
            if isinstance(reference, (ProjectRef, ChangeRef)):
                if observation is not None:
                    raise ValueError("non-observation endpoint cannot contain a task payload")
                continue
            if observation is None:
                if reference is not None or self.verdict != "NOT_ASSESSABLE":
                    raise ValueError("missing observation in assessable relation")
            elif not isinstance(observation, TaskObservation) or reference != ObservationRef.from_observation(observation):
                raise ValueError("assessment observation payload/reference mismatch")
        for name, expected_type in (("dependencies", SourceDependency), ("supporting_evidence", EvidenceQuote),
                                    ("counter_evidence", EvidenceQuote), ("context_evidence", EvidenceQuote)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, expected_type) for v in values):
                raise ValueError("invalid assessment evidence collection")
            unique = {canonical_hash(v.to_dict()): v for v in values}
            object.__setattr__(self, name, tuple(unique[k] for k in sorted(unique)))
        for name in ("reason_codes", "missing_evidence"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError("invalid assessment explanation")
            object.__setattr__(self, name, tuple(sorted(set(values))))
        if not self.reason_codes or (self.verdict == "SUPPORTED" and not self.supporting_evidence):
            raise ValueError("identity verdict requires explanation and supporting proof")
        if self.verdict in {"REFUTED", "CONFLICT"} and not self.counter_evidence:
            raise ValueError("refutation/conflict requires counter evidence")
        if self.verdict == "CONFLICT" and not self.supporting_evidence:
            raise ValueError("conflict requires both sides of the proof")
        if self.verdict != "NOT_ASSESSABLE" and not self.evidence_validity:
            raise ValueError("invalid dependencies in assessable relation")
        identity = self._payload()
        identity.pop("assessed_at")
        identity.pop("observations")  # Their immutable semantic payloads are hashed by the endpoint refs.
        prefix = 'task_assessment_v1_' if self.predicate == 'SAME_TASK' else 'task_assessment_v2_'
        object.__setattr__(self, "assessment_id", prefix + canonical_hash(identity))

    @property
    def evidence_validity(self):
        return self.verdict != "NOT_ASSESSABLE" and bool(self.dependencies) and all(
            d.source_status == "CURRENT" and d.readback_ok and d.observation_valid for d in self.dependencies)

    @property
    def lifecycle(self):
        if self.verdict != "NOT_ASSESSABLE":
            return "ACTIVE"
        return "STALE" if any(d.source_status == "NOT_CURRENT" for d in self.dependencies) else "INVALIDATED"

    def _payload(self):
        payload = {
            "schema_version": self.schema_version, "rule_version": self.rule_version,
            "predicate": self.predicate, "phase": "ASSESSED", "authority": "SUPPORTING_ONLY",
            "subject": self.subject.to_dict() if self.subject else None,
            "object": self.object.to_dict() if self.object else None,
            "verdict": self.verdict, "reason_codes": list(self.reason_codes), "lifecycle": self.lifecycle,
            "observations": [o.to_dict() if o else None for o in self.observations],
            "dependencies": [d.to_dict() for d in self.dependencies],
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "counter_evidence": [e.to_dict() for e in self.counter_evidence],
            "context_evidence": [e.to_dict() for e in self.context_evidence],
            "evidence_validity": self.evidence_validity, "missing_evidence": list(self.missing_evidence),
            "assessed_at": self.assessed_at.isoformat(),
        }
        if self.predicate != "SAME_TASK":
            payload.update(schema_version="task-relation-assessment/v2", rule_version="direct-relations-rules/v1",
                           relation_detail=self.relation_detail, premise_ids=list(self.premise_ids),
                           authority_checks=list(self.authority_checks))
        return payload

    def to_dict(self):
        return {"assessment_id": self.assessment_id, **self._payload()}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("assessment must be an object")
        try:
            result = cls(
                subject=_endpoint(data["subject"]), object=_endpoint(data["object"]),
                verdict=data["verdict"], reason_codes=tuple(data["reason_codes"]),
                observations=tuple(TaskObservation.from_dict(o) if o is not None else None for o in data["observations"]),
                dependencies=tuple(SourceDependency.from_dict(d) for d in data["dependencies"]),
                supporting_evidence=tuple(EvidenceQuote.from_dict(e) for e in data["supporting_evidence"]),
                counter_evidence=tuple(EvidenceQuote.from_dict(e) for e in data["counter_evidence"]),
                context_evidence=tuple(EvidenceQuote.from_dict(e) for e in data["context_evidence"]),
                missing_evidence=tuple(data["missing_evidence"]), assessed_at=datetime.fromisoformat(data["assessed_at"]),
                predicate=data["predicate"], relation_detail=data.get("relation_detail"),
                premise_ids=tuple(data.get("premise_ids", ())), authority_checks=tuple(data.get("authority_checks", ())),
            )
            if result.to_dict() != data:
                raise ValueError("noncanonical or altered identity assessment")
            return result
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("malformed identity assessment") from error
