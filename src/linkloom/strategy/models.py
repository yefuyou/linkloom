"""Closed immutable Strategy contracts, independent of Experience lifecycle."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

from linkloom.experience.models import ExperienceApplicability


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_digest(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("identity must be a SHA-256 digest")


def require_timestamp(value: Any) -> None:
    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")


def require_fields(cls: type, value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}:
        raise ValueError(f"{cls.__name__} requires exact closed fields")
    canonical_json(value)
    return deepcopy(value)


@dataclass(frozen=True)
class ExperienceApprovalReference:
    experience_id: str
    candidate_event_id: str
    review_event_id: str
    review_decision_sha256: str
    store_identity_sha256: str

    def __post_init__(self):
        for value in asdict(self).values():
            require_digest(value)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**require_fields(cls, value))


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_id: str
    schema_version: str
    generation_rule_id: str
    title: str
    situation: str
    behavioral_rule: str
    applicability: ExperienceApplicability
    exclusions: tuple[str, ...]
    source_experience_ids: tuple[str, ...]
    source_review_receipts: tuple[ExperienceApprovalReference, ...]
    expected_benefit: str
    known_risks: tuple[str, ...]
    evaluation_requirements: tuple[str, ...]
    status: str
    created_at: str
    review_transition: StrategyReviewTransition | None = None

    def __post_init__(self):
        require_digest(self.strategy_id)
        require_timestamp(self.created_at)
        if self.schema_version != "strategy-candidate/v1":
            raise ValueError("invalid Strategy schema")
        if self.status not in {"candidate", "accepted", "rejected"}:
            raise ValueError("invalid Strategy lifecycle")
        if self.status == "candidate":
            if self.review_transition is not None:
                raise ValueError("candidate cannot have a review transition")
        elif (type(self.review_transition) is not StrategyReviewTransition
              or self.review_transition.receipt.strategy_id != self.strategy_id
              or self.review_transition.receipt.reviewer_decision.decision != self.status):
            raise ValueError("terminal state requires an original-candidate explicit review transition")
        for name in ("exclusions", "source_experience_ids", "source_review_receipts", "known_risks", "evaluation_requirements"):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError("collections must be immutable tuples")
        if not self.source_review_receipts or any(type(r) is not ExperienceApprovalReference for r in self.source_review_receipts):
            raise ValueError("source review receipts are required")
        if self.source_experience_ids != tuple(r.experience_id for r in self.source_review_receipts):
            raise ValueError("source IDs must exactly match receipt IDs")
        if len(set(self.source_experience_ids)) != len(self.source_experience_ids):
            raise ValueError("source identities must be unique")
        if self.strategy_id != canonical_hash(self.identity_payload()):
            raise ValueError("immutable Strategy identity mismatch")
        from .policy import validate_shape
        validate_shape(self)

    def identity_payload(self):
        return {k: v for k, v in asdict(self).items() if k not in {"strategy_id", "created_at", "status", "review_transition"}}

    def to_dict(self):
        return asdict(self)

    @classmethod
    def create(cls, *, source_review_receipts, created_at):
        from .policy import STRATEGY_TEMPLATE
        values = dict(STRATEGY_TEMPLATE)
        values.update(schema_version="strategy-candidate/v1", source_review_receipts=tuple(source_review_receipts),
                      source_experience_ids=tuple(r.experience_id for r in source_review_receipts),
                      status="candidate", created_at=created_at, review_transition=None)
        immutable = {k: v.to_dict() if isinstance(v, ExperienceApplicability) else
                     [r.to_dict() for r in v] if k == "source_review_receipts" else v
                     for k, v in values.items() if k not in {"created_at", "status", "review_transition"}}
        return cls(strategy_id=canonical_hash(immutable), **values)

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        values["applicability"] = ExperienceApplicability.from_dict(values["applicability"])
        values["source_review_receipts"] = tuple(ExperienceApprovalReference.from_dict(r) for r in values["source_review_receipts"])
        for name in ("exclusions", "source_experience_ids", "known_risks", "evaluation_requirements"):
            if not isinstance(values[name], (list, tuple)):
                raise ValueError("invalid Strategy array")
            values[name] = tuple(values[name])
        if values["review_transition"] is not None:
            values["review_transition"] = StrategyReviewTransition.from_dict(values["review_transition"])
        return cls(**values)


def evidence_reason(outcome):
    reasons = {"PASS": "sealed_observation/v1", "FAIL": "sealed_observation/v1",
               "N/E": "oracle_not_evaluated/v1", "REVIEW_REQUIRED": "oracle_review_required/v1",
               "N/A": "tools_not_applicable/v1"}
    if outcome not in reasons:
        raise ValueError("invalid evidence outcome")
    return reasons[outcome]


@dataclass(frozen=True)
class MetricEvidenceReference:
    case_key: str
    dimension: str
    artifact_sha256: str
    observation_sha256: str
    reason: str
    schema_version: str = "strategy-metric-evidence/v1"

    def __post_init__(self):
        from .policy import DIMENSIONS
        for value in (self.case_key, self.artifact_sha256, self.observation_sha256):
            require_digest(value)
        if (self.schema_version != "strategy-metric-evidence/v1" or self.dimension not in (*DIMENSIONS, "context_cost")
                or self.reason not in {evidence_reason(s) for s in ("PASS", "FAIL", "N/E", "REVIEW_REQUIRED", "N/A")}):
            raise ValueError("invalid closed evidence locator/reason")

    @classmethod
    def create(cls, observation, artifact_sha256, dimension, outcome):
        return cls(observation["request_received"]["case_key"], dimension, artifact_sha256,
                   canonical_hash(observation), evidence_reason(outcome))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**require_fields(cls, value))


def validate_evidence(reference, case_key, dimension, outcome):
    if (type(reference) is not MetricEvidenceReference or reference.case_key != case_key
            or reference.dimension != dimension or reference.reason != evidence_reason(outcome)):
        raise ValueError("metric evidence does not bind its exact case/dimension/outcome")


@dataclass(frozen=True)
class ContextCostDelta:
    case_key: str
    baseline_chars: int
    candidate_chars: int
    char_delta: int
    baseline_bytes: int
    candidate_bytes: int
    byte_delta: int
    baseline_evidence: MetricEvidenceReference
    candidate_evidence: MetricEvidenceReference
    token_delta: None = None
    cost_usd_delta: None = None
    schema_version: str = "strategy-context-cost/v1"

    def __post_init__(self):
        require_digest(self.case_key)
        counts = (self.baseline_chars, self.candidate_chars, self.char_delta, self.baseline_bytes, self.candidate_bytes, self.byte_delta)
        if any(type(n) is not int for n in counts) or any(n < 0 for n in (self.baseline_chars, self.candidate_chars, self.baseline_bytes, self.candidate_bytes)):
            raise ValueError("context cost requires actual integer measurements")
        if self.char_delta != self.candidate_chars - self.baseline_chars or self.byte_delta != self.candidate_bytes - self.baseline_bytes:
            raise ValueError("incorrect context cost delta")
        if self.schema_version != "strategy-context-cost/v1" or self.token_delta is not None or self.cost_usd_delta is not None:
            raise ValueError("unavailable token/currency cost must not be invented")
        for reference in (self.baseline_evidence, self.candidate_evidence):
            validate_evidence(reference, self.case_key, "context_cost", "PASS")

    @classmethod
    def create(cls, baseline, candidate, baseline_sha256, candidate_sha256):
        b, c = (o["request_received"]["strategy_context"] for o in (baseline, candidate))
        return cls(baseline["request_received"]["case_key"], len(b), len(c), len(c) - len(b),
                   len(b.encode("utf-8")), len(c.encode("utf-8")), len(c.encode("utf-8")) - len(b.encode("utf-8")),
                   MetricEvidenceReference.create(baseline, baseline_sha256, "context_cost", "PASS"),
                   MetricEvidenceReference.create(candidate, candidate_sha256, "context_cost", "PASS"))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        for name in ("baseline_evidence", "candidate_evidence"):
            values[name] = MetricEvidenceReference.from_dict(values[name])
        return cls(**values)


@dataclass(frozen=True)
class MetricDelta:
    case_key: str
    dimension: str
    baseline: str
    candidate: str
    delta: int | None
    severity: str | None
    baseline_evidence: MetricEvidenceReference | None = None
    candidate_evidence: MetricEvidenceReference | None = None

    def __post_init__(self):
        from .policy import DIMENSIONS
        require_digest(self.case_key)
        if self.dimension not in DIMENSIONS or self.baseline not in {"PASS", "FAIL", "N/E", "REVIEW_REQUIRED", "N/A"} or self.candidate not in {"PASS", "FAIL", "N/E", "REVIEW_REQUIRED", "N/A"}:
            raise ValueError("invalid closed comparison dimension/outcome")
        measured = self.baseline in {"PASS", "FAIL"} and self.candidate in {"PASS", "FAIL"}
        expected = int(self.candidate == "PASS") - int(self.baseline == "PASS") if measured else None
        if self.delta != expected or (self.delta is not None and type(self.delta) is not int):
            raise ValueError("incorrect metric delta")
        if self.severity != ("P1" if expected == -1 else None):
            raise ValueError("regression severity must be explicit")
        if "N/A" in (self.baseline, self.candidate) and self.dimension != "retrieval_tools":
            raise ValueError("only predeclared tool inapplicability permits N/A")
        validate_evidence(self.baseline_evidence, self.case_key, self.dimension, self.baseline)
        validate_evidence(self.candidate_evidence, self.case_key, self.dimension, self.candidate)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        for name in ("baseline_evidence", "candidate_evidence"):
            values[name] = MetricEvidenceReference.from_dict(values[name])
        return cls(**values)


@dataclass(frozen=True)
class ComparisonSummary:
    result_id: str
    schema_version: str
    strategy_id: str
    suite_sha256: str
    condition_sha256: str
    policy_version: str
    baseline_sha256: str
    candidate_sha256: str
    context_sha256: str
    proof_kind: str
    rows: tuple[MetricDelta, ...]
    context_chars: int
    context_bytes: int
    recommendation: str
    context_costs: tuple[ContextCostDelta, ...]
    oracle_outcome_sha256: str

    def __post_init__(self):
        from .policy import DIMENSIONS, POLICY_VERSION, recommendation
        for name in ("result_id", "strategy_id", "suite_sha256", "condition_sha256", "baseline_sha256", "candidate_sha256", "context_sha256"):
            require_digest(getattr(self, name))
        require_digest(self.oracle_outcome_sha256)
        if self.schema_version != "strategy-comparison/v1" or self.policy_version != POLICY_VERSION:
            raise ValueError("invalid comparison schema/policy")
        if self.proof_kind not in {"responsive_fake", "scripted_replay"}:
            raise ValueError("unknown offline proof kind")
        if not isinstance(self.rows, tuple) or not self.rows or any(type(r) is not MetricDelta for r in self.rows):
            raise ValueError("complete typed per-case metrics required")
        for key in {row.case_key for row in self.rows}:
            rows = tuple(r for r in self.rows if r.case_key == key)
            if len(rows) != len(DIMENSIONS) or {r.dimension for r in rows} != set(DIMENSIONS):
                raise ValueError("missing or duplicate comparison dimension")
        if any(type(n) is not int or n < 0 for n in (self.context_chars, self.context_bytes)):
            raise ValueError("context proxy must be a nonnegative measured integer")
        if (not isinstance(self.context_costs, tuple) or any(type(r) is not ContextCostDelta for r in self.context_costs)
                or len(self.context_costs) != len({r.case_key for r in self.rows})
                or {r.case_key for r in self.context_costs} != {r.case_key for r in self.rows}):
            raise ValueError("complete unique per-case context costs required")
        if any(r.candidate_chars != self.context_chars or r.candidate_bytes != self.context_bytes for r in self.context_costs):
            raise ValueError("context aggregate differs from per-case measurements")
        if self.recommendation != recommendation(self.rows, self.context_chars, self.context_bytes, self.proof_kind):
            raise ValueError("recommendation inconsistent with complete metrics")
        if self.result_id != canonical_hash({k: v for k, v in self.to_dict().items() if k != "result_id"}):
            raise ValueError("comparison identity mismatch")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def create(cls, **values):
        payload = dict(values, rows=[r.to_dict() for r in values["rows"]], context_costs=[r.to_dict() for r in values["context_costs"]])
        return cls(result_id=canonical_hash(payload), **values)

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        values["rows"] = tuple(MetricDelta.from_dict(row) for row in values["rows"])
        values["context_costs"] = tuple(ContextCostDelta.from_dict(row) for row in values["context_costs"])
        return cls(**values)


@dataclass(frozen=True)
class HumanStrategyDecision:
    schema_version: str
    strategy_id: str
    comparison_id: str
    decision: str
    actor: str
    source: str
    reviewed_at: str
    rationale: str

    def __post_init__(self):
        require_digest(self.strategy_id)
        require_digest(self.comparison_id)
        require_timestamp(self.reviewed_at)
        if self.schema_version != "strategy-human-decision/v1" or self.decision not in {"accepted", "rejected"} or self.source != "human":
            raise ValueError("explicit human Strategy decision required")
        if not isinstance(self.actor, str) or not re.fullmatch(r"human:[A-Za-z0-9_-]{1,64}", self.actor) or any(
            role in self.actor.casefold() for role in ("reviewer", "validator", "model", "tool", "agent")
        ):
            raise ValueError("model/reviewer/validator identity is not a human review source")
        if self.rationale not in {"Reviewed the complete offline comparison and its limits.",
                                  "Insufficient evidence or regression prevents promotion."}:
            raise ValueError("v1 rationale must be a generic bounded decision reason, not raw answer prose")
        from linkloom.experience.policy import validate_gold_safe_payload
        validate_gold_safe_payload(asdict(self))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**require_fields(cls, value))


@dataclass(frozen=True)
class StrategyReviewReceipt:
    strategy_id: str
    candidate_event_id: str
    store_identity_sha256: str
    evaluator_result: ComparisonSummary
    reviewer_decision: HumanStrategyDecision
    schema_version: str = "strategy-review-receipt/v1"

    def __post_init__(self):
        if self.schema_version != "strategy-review-receipt/v1":
            raise ValueError("invalid Strategy review receipt schema")
        for value in (self.strategy_id, self.candidate_event_id, self.store_identity_sha256):
            require_digest(value)
        if type(self.evaluator_result) is not ComparisonSummary or type(self.reviewer_decision) is not HumanStrategyDecision:
            raise ValueError("complete evaluator and explicit human decision required")
        if (self.evaluator_result.strategy_id != self.strategy_id or self.reviewer_decision.strategy_id != self.strategy_id
                or self.reviewer_decision.comparison_id != self.evaluator_result.result_id):
            raise ValueError("review receipt binds a different strategy/comparison")
        if self.reviewer_decision.decision == "accepted" and self.evaluator_result.recommendation != "recommend_accept":
            raise ValueError("unsafe or insufficient recommendation cannot become accepted")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        values["evaluator_result"] = ComparisonSummary.from_dict(values["evaluator_result"])
        values["reviewer_decision"] = HumanStrategyDecision.from_dict(values["reviewer_decision"])
        return cls(**values)


@dataclass(frozen=True)
class StrategyReviewTransition:
    receipt: StrategyReviewReceipt
    review_event_id: str

    def __post_init__(self):
        require_digest(self.review_event_id)
        if type(self.receipt) is not StrategyReviewReceipt:
            raise ValueError("explicit typed review receipt required")

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        values["receipt"] = StrategyReviewReceipt.from_dict(values["receipt"])
        return cls(**values)
