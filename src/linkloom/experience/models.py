"""Closed, provider-neutral contracts for reviewed Agent experience."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, ClassVar


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*/v[1-9][0-9]*$")
_RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SIGNAL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

REFLECTION_INPUT_SCHEMA_VERSION = "reflection-input/v1"
EXPERIENCE_RECORD_SCHEMA_VERSION = "experience-record/v1"
EXPERIENCE_REVIEW_SCHEMA_VERSION = "experience-review/v1"

FINDING_DIMENSIONS = frozenset(
    {
        "runtime",
        "contract",
        "grounding",
        "semantic",
        "infrastructure",
        "provider_availability",
    }
)
FINDING_OUTCOMES = frozenset(
    {"PASS", "FAIL", "PARTIAL", "N/E", "REVIEW_REQUIRED"}
)
EXPERIENCE_STATUSES = frozenset({"candidate", "accepted", "rejected"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
REVIEW_DECISIONS = frozenset({"accepted", "rejected"})
REVIEW_SOURCES = frozenset(
    {"human", "independent_reviewer", "deterministic_validator"}
)


def _ensure_json_safe(value: Any, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-safe") from exc


def _require_exact_fields(
    data: dict[str, Any], fields: frozenset[str], field_name: str
) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{field_name} must be an object")
    missing = fields - set(data)
    unknown = set(data) - fields
    if missing:
        raise ValueError(f"{field_name} missing required fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{field_name} has unknown fields: {sorted(unknown)}")


def _require_text(value: Any, field_name: str, *, max_length: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    if value != value.strip() or len(value) > max_length:
        raise ValueError(f"{field_name} must be normalized bounded text")
    return value


def _require_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_run_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an opaque run identifier")
    from .policy import validate_source_identifiers
    validate_source_identifiers(value, ())
    return value


def _require_opaque_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an opaque safe identifier")
    from .policy import validate_gold_safe_payload
    validate_gold_safe_payload(value)
    return value


def _require_rule_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _RULE_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a versioned rule identifier")
    return value


def _require_timestamp(value: Any, field_name: str) -> str:
    _require_text(value, field_name, max_length=64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _require_text_tuple(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    signal: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be an immutable tuple")
    if not value and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    for index, item in enumerate(value):
        if signal:
            if not isinstance(item, str) or not _SIGNAL_RE.fullmatch(item):
                raise ValueError(f"{field_name}[{index}] must be a stable signal")
        else:
            _require_opaque_id(item, f"{field_name}[{index}]")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must contain unique values")
    return value


def _as_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    return tuple(value)


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluatorFinding:
    finding_id: str
    dimension: str
    outcome: str
    code: str
    source_evidence_refs: tuple[str, ...]
    evaluator_artifact_sha256: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "finding_id",
            "dimension",
            "outcome",
            "code",
            "source_evidence_refs",
            "evaluator_artifact_sha256",
        }
    )

    def __post_init__(self) -> None:
        _require_opaque_id(self.finding_id, "finding_id")
        if self.dimension not in FINDING_DIMENSIONS:
            raise ValueError("dimension is not registered")
        if self.outcome not in FINDING_OUTCOMES:
            raise ValueError("outcome is not registered")
        _require_rule_id(self.code, "code")
        _require_text_tuple(
            self.source_evidence_refs,
            "source_evidence_refs",
            allow_empty=True,
        )
        _require_digest(
            self.evaluator_artifact_sha256, "evaluator_artifact_sha256"
        )
        _ensure_json_safe(asdict(self), "EvaluatorFinding")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluatorFinding":
        _require_exact_fields(data, cls._FIELDS, "EvaluatorFinding")
        values = deepcopy(data)
        values["source_evidence_refs"] = _as_tuple(
            values["source_evidence_refs"], "source_evidence_refs"
        )
        return cls(**values)


@dataclass(frozen=True)
class ReflectionInput:
    schema_version: str
    source_run_id: str
    observed_summary_sha256: str
    evaluator_artifact_sha256: str
    evaluator_artifact_schema_version: str
    available_source_evidence_refs: tuple[str, ...]
    findings: tuple[EvaluatorFinding, ...]
    task_characteristics: tuple[str, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "source_run_id",
            "observed_summary_sha256",
            "evaluator_artifact_sha256",
            "evaluator_artifact_schema_version",
            "available_source_evidence_refs",
            "findings",
            "task_characteristics",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_version != REFLECTION_INPUT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {REFLECTION_INPUT_SCHEMA_VERSION}"
            )
        _require_run_id(self.source_run_id, "source_run_id")
        _require_digest(self.observed_summary_sha256, "observed_summary_sha256")
        _require_digest(
            self.evaluator_artifact_sha256, "evaluator_artifact_sha256"
        )
        _require_rule_id(
            self.evaluator_artifact_schema_version,
            "evaluator_artifact_schema_version",
        )
        _require_text_tuple(
            self.available_source_evidence_refs,
            "available_source_evidence_refs",
            allow_empty=True,
        )
        prefix = f"{self.source_run_id}:"
        from .policy import validate_source_identifiers
        validate_source_identifiers(self.source_run_id, self.available_source_evidence_refs)
        if any(
            not ref.startswith(prefix)
            for ref in self.available_source_evidence_refs
        ):
            raise ValueError(
                "available_source_evidence_refs must belong to source_run_id"
            )
        if not isinstance(self.findings, tuple):
            raise ValueError("findings must be an immutable tuple")
        if any(not isinstance(item, EvaluatorFinding) for item in self.findings):
            raise ValueError("findings must contain EvaluatorFinding values")
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise ValueError("findings must have unique finding_id values")
        _require_text_tuple(
            self.task_characteristics, "task_characteristics", signal=True
        )
        _ensure_json_safe(asdict(self), "ReflectionInput")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReflectionInput":
        _require_exact_fields(data, cls._FIELDS, "ReflectionInput")
        values = deepcopy(data)
        raw_findings = _as_tuple(values["findings"], "findings")
        values["findings"] = tuple(
            item
            if isinstance(item, EvaluatorFinding)
            else EvaluatorFinding.from_dict(item)
            for item in raw_findings
        )
        values["available_source_evidence_refs"] = _as_tuple(
            values["available_source_evidence_refs"],
            "available_source_evidence_refs",
        )
        values["task_characteristics"] = _as_tuple(
            values["task_characteristics"], "task_characteristics"
        )
        return cls(**values)


@dataclass(frozen=True)
class ExperienceApplicability:
    workflows: tuple[str, ...]
    task_characteristics: tuple[str, ...]
    exclude_when: tuple[str, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"workflows", "task_characteristics", "exclude_when"}
    )

    def __post_init__(self) -> None:
        _require_text_tuple(self.workflows, "workflows", signal=True)
        _require_text_tuple(
            self.task_characteristics, "task_characteristics", signal=True
        )
        _require_text_tuple(
            self.exclude_when, "exclude_when", allow_empty=True, signal=True
        )
        _ensure_json_safe(asdict(self), "ExperienceApplicability")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceApplicability":
        _require_exact_fields(data, cls._FIELDS, "ExperienceApplicability")
        return cls(
            workflows=_as_tuple(data["workflows"], "workflows"),
            task_characteristics=_as_tuple(
                data["task_characteristics"], "task_characteristics"
            ),
            exclude_when=_as_tuple(data["exclude_when"], "exclude_when"),
        )


@dataclass(frozen=True)
class ExperienceProvenance:
    source_run_id: str
    observed_summary_sha256: str
    evaluator_artifact_sha256: str
    evaluator_artifact_schema_version: str
    finding_ids: tuple[str, ...]
    source_evidence_refs: tuple[str, ...]
    available_source_evidence_refs: tuple[str, ...]
    evaluation_result_id: str = ""
    observed_seal_sha256: str = ""
    source_identity: str = ""

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "source_run_id",
            "observed_summary_sha256",
            "evaluator_artifact_sha256",
            "evaluator_artifact_schema_version",
            "finding_ids",
            "source_evidence_refs",
            "available_source_evidence_refs",
            "evaluation_result_id",
            "observed_seal_sha256",
            "source_identity",
        }
    )

    def __post_init__(self) -> None:
        _require_run_id(self.source_run_id, "source_run_id")
        _require_digest(self.observed_summary_sha256, "observed_summary_sha256")
        _require_digest(
            self.evaluator_artifact_sha256, "evaluator_artifact_sha256"
        )
        _require_rule_id(
            self.evaluator_artifact_schema_version,
            "evaluator_artifact_schema_version",
        )
        _require_text_tuple(self.finding_ids, "finding_ids")
        _require_text_tuple(self.source_evidence_refs, "source_evidence_refs")
        _require_text_tuple(
            self.available_source_evidence_refs,
            "available_source_evidence_refs",
            allow_empty=True,
        )
        prefix = f"{self.source_run_id}:"
        all_refs = (
            self.source_evidence_refs + self.available_source_evidence_refs
        )
        from .policy import validate_source_identifiers
        validate_source_identifiers(self.source_run_id, all_refs)
        if any(not ref.startswith(prefix) for ref in all_refs):
            raise ValueError("evidence refs must belong to source_run_id")
        if not set(self.source_evidence_refs).issubset(
            self.available_source_evidence_refs
        ):
            raise ValueError(
                "source_evidence_refs must resolve in available source evidence"
            )
        _ensure_json_safe(asdict(self), "ExperienceProvenance")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceProvenance":
        _require_exact_fields(data, cls._FIELDS, "ExperienceProvenance")
        values = deepcopy(data)
        values["finding_ids"] = _as_tuple(values["finding_ids"], "finding_ids")
        values["source_evidence_refs"] = _as_tuple(
            values["source_evidence_refs"], "source_evidence_refs"
        )
        values["available_source_evidence_refs"] = _as_tuple(
            values["available_source_evidence_refs"],
            "available_source_evidence_refs",
        )
        return cls(**values)


@dataclass(frozen=True)
class ExperienceRecord:
    experience_id: str
    schema_version: str
    situation: str
    observed_outcome: str
    pattern_type: str
    reusable_lesson: str
    suggested_strategy: str
    applicability: ExperienceApplicability
    counterexamples_or_limits: tuple[str, ...]
    source_run_ids: tuple[str, ...]
    source_evidence_refs: tuple[str, ...]
    confidence: str
    created_at: str
    status: str
    generation_rule_id: str
    provenance: tuple[ExperienceProvenance, ...]
    review_transition: ExperienceReviewTransition | None = None

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "experience_id",
            "schema_version",
            "situation",
            "observed_outcome",
            "pattern_type",
            "reusable_lesson",
            "suggested_strategy",
            "applicability",
            "counterexamples_or_limits",
            "source_run_ids",
            "source_evidence_refs",
            "confidence",
            "created_at",
            "status",
            "generation_rule_id",
            "provenance",
            "review_transition",
        }
    )

    def __post_init__(self) -> None:
        _require_digest(self.experience_id, "experience_id")
        if self.schema_version != EXPERIENCE_RECORD_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {EXPERIENCE_RECORD_SCHEMA_VERSION}"
            )
        for name in (
            "situation",
            "observed_outcome",
            "reusable_lesson",
            "suggested_strategy",
        ):
            _require_text(getattr(self, name), name)
        if not isinstance(self.pattern_type, str) or not _SIGNAL_RE.fullmatch(
            self.pattern_type
        ):
            raise ValueError("pattern_type must be a stable signal")
        if not isinstance(self.applicability, ExperienceApplicability):
            raise ValueError("applicability must be ExperienceApplicability")
        if not isinstance(self.counterexamples_or_limits, tuple) or not self.counterexamples_or_limits:
            raise ValueError("counterexamples_or_limits must be a non-empty tuple")
        for index, item in enumerate(self.counterexamples_or_limits):
            _require_text(item, f"counterexamples_or_limits[{index}]")
        _require_text_tuple(self.source_run_ids, "source_run_ids")
        for index, run_id in enumerate(self.source_run_ids):
            _require_run_id(run_id, f"source_run_ids[{index}]")
        _require_text_tuple(self.source_evidence_refs, "source_evidence_refs")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError("confidence must be low, medium, or high")
        _require_timestamp(self.created_at, "created_at")
        if self.status not in EXPERIENCE_STATUSES:
            raise ValueError("status must be candidate, accepted, or rejected")
        if self.status == "candidate":
            if self.review_transition is not None:
                raise ValueError("candidate cannot carry a review transition")
        else:
            if not isinstance(self.review_transition, ExperienceReviewTransition):
                raise ValueError("accepted/rejected candidate requires explicit persisted review transition")
            transition = self.review_transition
            if (transition.candidate_experience_id != self.experience_id
                    or transition.review_decision.experience_id != self.experience_id
                    or transition.review_decision.decision != self.status):
                raise ValueError("review transition must match original candidate identity and status")
        _require_rule_id(self.generation_rule_id, "generation_rule_id")
        if not isinstance(self.provenance, tuple) or not self.provenance:
            raise ValueError("provenance must be a non-empty tuple")
        if any(not isinstance(item, ExperienceProvenance) for item in self.provenance):
            raise ValueError("provenance must contain ExperienceProvenance values")
        provenance_runs = tuple(item.source_run_id for item in self.provenance)
        if provenance_runs != self.source_run_ids:
            raise ValueError("source_run_ids must match provenance")
        provenance_refs = tuple(
            ref for item in self.provenance for ref in item.source_evidence_refs
        )
        if provenance_refs != self.source_evidence_refs:
            raise ValueError("source_evidence_refs must match provenance")
        if self.experience_id != _canonical_hash(self.identity_payload()):
            raise ValueError("experience_id does not match canonical identity")
        _ensure_json_safe(asdict(self), "ExperienceRecord")

    def identity_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        for name in ("experience_id", "created_at", "status", "review_transition"):
            payload.pop(name)
        return payload

    def creation_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("experience_id")
        payload.pop("schema_version")
        payload["applicability"] = self.applicability
        payload["provenance"] = self.provenance
        return payload

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def create(
        cls,
        *,
        situation: str,
        observed_outcome: str,
        pattern_type: str,
        reusable_lesson: str,
        suggested_strategy: str,
        applicability: ExperienceApplicability,
        counterexamples_or_limits: tuple[str, ...],
        source_run_ids: tuple[str, ...],
        source_evidence_refs: tuple[str, ...],
        confidence: str,
        created_at: str,
        status: str,
        generation_rule_id: str,
        provenance: tuple[ExperienceProvenance, ...],
        review_transition: ExperienceReviewTransition | None = None,
    ) -> "ExperienceRecord":
        if status != "candidate" or review_transition is not None:
            raise ValueError("ExperienceRecord.create only creates candidate; use persisted review transition")
        prototype = {
            "schema_version": EXPERIENCE_RECORD_SCHEMA_VERSION,
            "situation": situation,
            "observed_outcome": observed_outcome,
            "pattern_type": pattern_type,
            "reusable_lesson": reusable_lesson,
            "suggested_strategy": suggested_strategy,
            "applicability": applicability.to_dict(),
            "counterexamples_or_limits": counterexamples_or_limits,
            "source_run_ids": source_run_ids,
            "source_evidence_refs": source_evidence_refs,
            "confidence": confidence,
            "generation_rule_id": generation_rule_id,
            "provenance": tuple(item.to_dict() for item in provenance),
        }
        return cls(
            experience_id=_canonical_hash(prototype),
            schema_version=EXPERIENCE_RECORD_SCHEMA_VERSION,
            situation=situation,
            observed_outcome=observed_outcome,
            pattern_type=pattern_type,
            reusable_lesson=reusable_lesson,
            suggested_strategy=suggested_strategy,
            applicability=applicability,
            counterexamples_or_limits=counterexamples_or_limits,
            source_run_ids=source_run_ids,
            source_evidence_refs=source_evidence_refs,
            confidence=confidence,
            created_at=created_at,
            status=status,
            generation_rule_id=generation_rule_id,
            provenance=provenance,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceRecord":
        _require_exact_fields(data, cls._FIELDS, "ExperienceRecord")
        values = deepcopy(data)
        values["applicability"] = ExperienceApplicability.from_dict(
            values["applicability"]
        )
        for name in (
            "counterexamples_or_limits",
            "source_run_ids",
            "source_evidence_refs",
        ):
            values[name] = _as_tuple(values[name], name)
        values["provenance"] = tuple(
            ExperienceProvenance.from_dict(item)
            for item in _as_tuple(values["provenance"], "provenance")
        )
        if values["review_transition"] is not None:
            values["review_transition"] = ExperienceReviewTransition.from_dict(values["review_transition"])
        return cls(**values)


@dataclass(frozen=True)
class ExperienceReviewDecision:
    schema_version: str
    experience_id: str
    decision: str
    actor: str
    source: str
    evidence_ref: str
    reviewed_at: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "experience_id",
            "decision",
            "actor",
            "source",
            "evidence_ref",
            "reviewed_at",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_version != EXPERIENCE_REVIEW_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {EXPERIENCE_REVIEW_SCHEMA_VERSION}"
            )
        _require_digest(self.experience_id, "experience_id")
        if self.decision not in REVIEW_DECISIONS:
            raise ValueError("decision must be accepted or rejected")
        _require_opaque_id(self.actor, "actor")
        if self.source not in REVIEW_SOURCES:
            raise ValueError("source is not an allowed explicit review source")
        _require_opaque_id(self.evidence_ref, "evidence_ref")
        _require_timestamp(self.reviewed_at, "reviewed_at")
        _ensure_json_safe(asdict(self), "ExperienceReviewDecision")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceReviewDecision":
        _require_exact_fields(data, cls._FIELDS, "ExperienceReviewDecision")
        return cls(**deepcopy(data))


@dataclass(frozen=True)
class ExperienceReviewTransition:
    """Audited lifecycle metadata; Store replay, not this shape, grants trust."""

    candidate_experience_id: str
    candidate_event_id: str
    review_decision: ExperienceReviewDecision
    review_event_id: str
    store_identity_sha256: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset({
        "candidate_experience_id", "candidate_event_id", "review_decision",
        "review_event_id", "store_identity_sha256",
    })

    def __post_init__(self) -> None:
        for name in ("candidate_experience_id", "candidate_event_id", "review_event_id", "store_identity_sha256"):
            _require_digest(getattr(self, name), name)
        if not isinstance(self.review_decision, ExperienceReviewDecision):
            raise ValueError("review_decision must be an explicit review decision")
        if self.candidate_experience_id != self.review_decision.experience_id:
            raise ValueError("review decision must bind original candidate identity")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceReviewTransition":
        _require_exact_fields(data, cls._FIELDS, "ExperienceReviewTransition")
        values = deepcopy(data)
        values["review_decision"] = ExperienceReviewDecision.from_dict(values["review_decision"])
        return cls(**values)


@dataclass(frozen=True)
class ExperienceQuery:
    workflow: str
    task_characteristics: tuple[str, ...]
    pattern_types: tuple[str, ...] = ()

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"workflow", "task_characteristics", "pattern_types"}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.workflow, str) or not _SIGNAL_RE.fullmatch(
            self.workflow
        ):
            raise ValueError("workflow must be a stable signal")
        _require_text_tuple(
            self.task_characteristics, "task_characteristics", signal=True
        )
        _require_text_tuple(
            self.pattern_types, "pattern_types", allow_empty=True, signal=True
        )
        _ensure_json_safe(asdict(self), "ExperienceQuery")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceQuery":
        _require_exact_fields(data, cls._FIELDS, "ExperienceQuery")
        return cls(
            workflow=data["workflow"],
            task_characteristics=_as_tuple(
                data["task_characteristics"], "task_characteristics"
            ),
            pattern_types=_as_tuple(data["pattern_types"], "pattern_types"),
        )


@dataclass(frozen=True)
class ExperienceSelection:
    records: tuple[ExperienceRecord, ...]
    total_rendered_chars: int

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"records", "total_rendered_chars"}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple):
            raise ValueError("records must be an immutable tuple")
        if len(self.records) > 5:
            raise ValueError("selection exceeds hard top-k bound of 5")
        if any(not isinstance(item, ExperienceRecord) for item in self.records):
            raise ValueError("records must contain ExperienceRecord values")
        if any(item.status != "accepted" for item in self.records):
            raise ValueError("candidate or rejected records cannot be selected")
        if len({item.experience_id for item in self.records}) != len(self.records):
            raise ValueError("records must have unique experience_id values")
        if (
            not isinstance(self.total_rendered_chars, int)
            or isinstance(self.total_rendered_chars, bool)
            or self.total_rendered_chars < 0
        ):
            raise ValueError("total_rendered_chars must be a non-negative integer")
        _ensure_json_safe(asdict(self), "ExperienceSelection")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceSelection":
        _require_exact_fields(data, cls._FIELDS, "ExperienceSelection")
        records = tuple(
            ExperienceRecord.from_dict(item)
            for item in _as_tuple(data["records"], "records")
        )
        return cls(records=records, total_rendered_chars=data["total_rendered_chars"])


@dataclass(frozen=True)
class ExperienceContextSection:
    model_text: str
    experience_ids: tuple[str, ...]
    provenance: tuple[ExperienceProvenance, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"model_text", "experience_ids", "provenance"}
    )

    def __post_init__(self) -> None:
        _ensure_json_safe(asdict(self), "ExperienceContextSection")
        _require_text_tuple(
            self.experience_ids, "experience_ids", allow_empty=True
        )
        if len(self.experience_ids) > 5:
            raise ValueError("context exceeds hard top-k bound of 5")
        for index, experience_id in enumerate(self.experience_ids):
            _require_digest(experience_id, f"experience_ids[{index}]")
        if not isinstance(self.provenance, tuple):
            raise ValueError("provenance must be an immutable tuple")
        if any(not isinstance(item, ExperienceProvenance) for item in self.provenance):
            raise ValueError("provenance must contain ExperienceProvenance values")
        if not self.experience_ids:
            if self.model_text != "" or self.provenance:
                raise ValueError("empty context must have no text or provenance")
        else:
            _require_text(self.model_text, "model_text", max_length=4000)
            if (not self.model_text.startswith("Relevant prior experience\n")
                    or self.model_text.count("- Lesson:") != len(self.experience_ids)):
                raise ValueError("context lesson count violates final top-k/identity bound")

    def to_dict(self) -> dict[str, Any]:
        # Final offline serialization cannot trust an object whose frozen
        # constructor was bypassed. No production Provider injection exists.
        self.__post_init__()
        return deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceContextSection":
        _require_exact_fields(data, cls._FIELDS, "ExperienceContextSection")
        return cls(
            model_text=data["model_text"],
            experience_ids=_as_tuple(data["experience_ids"], "experience_ids"),
            provenance=tuple(
                ExperienceProvenance.from_dict(item)
                for item in _as_tuple(data["provenance"], "provenance")
            ),
        )


__all__ = [
    "CONFIDENCE_LEVELS",
    "EXPERIENCE_RECORD_SCHEMA_VERSION",
    "EXPERIENCE_REVIEW_SCHEMA_VERSION",
    "EXPERIENCE_STATUSES",
    "FINDING_DIMENSIONS",
    "FINDING_OUTCOMES",
    "REFLECTION_INPUT_SCHEMA_VERSION",
    "REVIEW_DECISIONS",
    "REVIEW_SOURCES",
    "EvaluatorFinding",
    "ExperienceApplicability",
    "ExperienceContextSection",
    "ExperienceProvenance",
    "ExperienceQuery",
    "ExperienceRecord",
    "ExperienceReviewDecision",
    "ExperienceReviewTransition",
    "ExperienceSelection",
    "ReflectionInput",
]
