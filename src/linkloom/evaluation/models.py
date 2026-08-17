"""Typed contracts for the formal, read-only evaluation boundary."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Dict, List


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_TERMS = ("gold", "expected", "label", "ground_truth", "answer_key")


def _ensure_json(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-safe") from exc


def _ensure_no_forbidden(value: str, name: str) -> None:
    lowered = value.casefold()
    for term in _FORBIDDEN_TERMS:
        if term in lowered:
            raise ValueError(f"Forbidden keyword '{term}' in {name}")


def _ensure_relative_path(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or PureWindowsPath(value).is_absolute() or value.startswith("/"):
        raise ValueError(f"{name} must be a non-empty relative path")
    if any(part == ".." for part in value.replace("\\", "/").split("/")):
        raise ValueError(f"{name} must not traverse outside the dataset root")


def _ensure_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _ensure_finite(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


class PredictionKind(str, Enum):
    TOPIC = "topic"
    RELATION = "relation"


class BadCaseCategory(str, Enum):
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    DIRECTION_ERROR = "direction_error"
    TYPE_ERROR = "type_error"
    MALFORMED_OUTPUT = "malformed_output"
    EVIDENCE_INVALID = "evidence_invalid"
    PROVIDER_ERROR = "provider_error"
    SAFETY_VIOLATION = "safety_violation"


class BadCaseSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class InferenceDocument:
    path: str
    content_sha256: str
    content: str

    def __post_init__(self) -> None:
        _ensure_relative_path(self.path, "InferenceDocument.path")
        _ensure_no_forbidden(self.path, "InferenceDocument.path")
        _ensure_sha(self.content_sha256, "InferenceDocument.content_sha256")
        if not isinstance(self.content, str):
            raise ValueError("InferenceDocument.content must be text")
        _ensure_json(self.content, "InferenceDocument.content")


@dataclass(frozen=True)
class InferencePair:
    source_id: str
    target_id: str

    def __post_init__(self) -> None:
        for name, value in (("source_id", self.source_id), ("target_id", self.target_id)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"InferencePair.{name} must be non-empty")
            _ensure_relative_path(value, f"InferencePair.{name}")
            _ensure_no_forbidden(value, f"InferencePair.{name}")
        if self.source_id == self.target_id:
            raise ValueError("InferencePair cannot contain the same document twice")


@dataclass(frozen=True)
class EvidenceRef:
    note_path: str
    quote_sha256: str
    valid: bool | None = None

    def __post_init__(self) -> None:
        _ensure_relative_path(self.note_path, "EvidenceRef.note_path")
        _ensure_sha(self.quote_sha256, "EvidenceRef.quote_sha256")


@dataclass(frozen=True)
class PredictionRecord:
    source_id: str
    target_id: str
    relation_type: str | None
    direction: str | None
    evidence_refs: List[str]
    kind: PredictionKind = PredictionKind.RELATION
    should_link: bool | None = None
    confidence: float | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (("source_id", self.source_id), ("target_id", self.target_id)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"PredictionRecord.{name} must be non-empty")
            _ensure_no_forbidden(value, f"PredictionRecord.{name}")
        if self.relation_type is not None:
            _ensure_no_forbidden(self.relation_type, "PredictionRecord.relation_type")
        try:
            object.__setattr__(self, "kind", self.kind if isinstance(self.kind, PredictionKind) else PredictionKind(self.kind))
        except (TypeError, ValueError) as exc:
            raise ValueError("PredictionRecord.kind is invalid") from exc
        if not isinstance(self.evidence_refs, list):
            raise ValueError("PredictionRecord.evidence_refs must be a list")
        for ref in self.evidence_refs:
            if not isinstance(ref, str) or not ref or len(ref) > 200 or "\n" in ref or "\r" in ref:
                raise ValueError("evidence_refs should contain short refs/quote hashes, not raw bodies.")
        if self.confidence is not None:
            _ensure_finite(self.confidence, "PredictionRecord.confidence")
            if not 0 <= self.confidence <= 1:
                raise ValueError("PredictionRecord.confidence must be between 0 and 1")
        if not isinstance(self.metadata, dict):
            raise ValueError("PredictionRecord.metadata must be an object")
        for key in self.metadata:
            _ensure_no_forbidden(str(key), "PredictionRecord.metadata")
        _ensure_json(self.metadata, "PredictionRecord.metadata")


@dataclass(frozen=True)
class DatasetManifest:
    dataset_version: str
    document_count: int
    pair_count: int
    fixture_sha256: str
    gold_sha256: str
    frozen: bool = True

    def __post_init__(self) -> None:
        if not self.dataset_version:
            raise ValueError("dataset_version is required")
        if self.document_count < 0 or self.pair_count < 0:
            raise ValueError("dataset counts must be non-negative")
        # Keep construction permissive for comparison manifests; the loader
        # and assert_frozen enforce digest shape and exact equality.
        if not isinstance(self.fixture_sha256, str) or not isinstance(self.gold_sha256, str):
            raise ValueError("dataset digests must be strings")


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("MetricResult.name is required")
        _ensure_finite(self.value, "MetricResult.value")
        _ensure_json(self.metadata, "MetricResult.metadata")


@dataclass(frozen=True)
class BadCase:
    source_id: str
    target_id: str
    category: str
    severity: str
    reason: str
    safe_refs: List[str]

    def __post_init__(self) -> None:
        if not self.category or not self.severity or not self.reason:
            raise ValueError("BadCase category, severity, and reason are required")
        if len(self.reason) > 500 or "\n" in self.reason:
            raise ValueError("BadCase.reason must be a short single-line summary")
        if not isinstance(self.safe_refs, list) or any(not isinstance(ref, str) or len(ref) > 256 for ref in self.safe_refs):
            raise ValueError("BadCase.safe_refs must contain short safe refs")


@dataclass(frozen=True)
class TraceQuality:
    sequence_count: int
    redaction_count: int
    contiguous: bool = True
    raw_content_events: int = 0


@dataclass(frozen=True)
class SafetyReport:
    is_safe: bool
    violations: List[str]

    def __post_init__(self) -> None:
        if any(not isinstance(item, str) or len(item) > 200 for item in self.violations):
            raise ValueError("SafetyReport violations must be short codes")


@dataclass(frozen=True)
class EvaluationRunManifest:
    dataset_version: str
    run_timestamp: str
    metrics: List[MetricResult]
    safety_report: SafetyReport
    run_id: str = ""
    provider: str = "mock"
    scenario: str = ""
    baseline_eligible: bool = False


@dataclass(frozen=True)
class GoldNote:
    note_path: str
    expected_topic_ids: List[str]
    note_type: str
    should_link_to_agent_evaluation: bool
    evidence: List[str]
    difficulty: str
    rationale: str


@dataclass(frozen=True)
class GoldPair:
    source: str
    target: str
    relation_type: str
    evidence: Dict[str, List[str]]
    rationale: str
    should_link: bool = True
