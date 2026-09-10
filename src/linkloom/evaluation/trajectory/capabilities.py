"""Central capability gating for trajectory evaluation cases.

This module describes what the independent evaluator can assess today. It
does not add capabilities to the production Agent runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Iterable

from .models import TrajectoryCase


SUPPORTED = "SUPPORTED"
FUTURE = "FUTURE"
CAPABILITY_STATUSES = frozenset({SUPPORTED, FUTURE})


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    status: str
    evidence: str
    boundary: str
    missing_reason: str | None
    adapter: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not self.capability_id.strip():
            raise ValueError("capability_id must be non-empty text")
        if self.status not in CAPABILITY_STATUSES:
            raise ValueError(f"status must be one of {sorted(CAPABILITY_STATUSES)}")
        for field_name, value in (("evidence", self.evidence), ("boundary", self.boundary)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty text")
        if self.status == FUTURE:
            if not isinstance(self.missing_reason, str) or not self.missing_reason.strip():
                raise ValueError("future capability must have a missing_reason")
            if self.adapter is not None:
                raise ValueError("future capability cannot expose an adapter")
        elif self.missing_reason is not None:
            raise ValueError("supported capability cannot have a missing_reason")
        if self.adapter is not None and (
            not isinstance(self.adapter, str) or not self.adapter.strip()
        ):
            raise ValueError("adapter must be non-empty text or null")

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _supported(
    capability_id: str,
    *,
    evidence: str,
    boundary: str,
    adapter: str,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=capability_id,
        status=SUPPORTED,
        evidence=evidence,
        boundary=boundary,
        missing_reason=None,
        adapter=adapter,
    )


def _future(
    capability_id: str,
    *,
    evidence: str,
    boundary: str,
    missing_reason: str,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=capability_id,
        status=FUTURE,
        evidence=evidence,
        boundary=boundary,
        missing_reason=missing_reason,
        adapter=None,
    )


_CAPABILITY_MATRIX = {
    "validation.arguments": _supported(
        "validation.arguments",
        evidence="ToolRuntime validates registered input schemas before executor dispatch.",
        boundary="Argument contract and executor suppression only; no model repair.",
        adapter="deterministic_grader.argument_contract",
    ),
    "policy.permissions": _supported(
        "policy.permissions",
        evidence="ToolPolicyEnforcer denies write_file, read_gold, and raw_filesystem.",
        boundary="Static read-only policy outcomes; no external authorization service.",
        adapter="deterministic_grader.permission_contract",
    ),
    "budget.max_calls": _supported(
        "budget.max_calls",
        evidence="ToolPolicyEnforcer exposes deterministic call_count and max_calls behavior.",
        boundary="Per-enforcer tool-call accounting only; no token or monetary budget.",
        adapter="deterministic_grader.budget_contract",
    ),
    "execution.failure_normalization": _supported(
        "execution.failure_normalization",
        evidence="ToolRuntime normalizes executor and checkpoint failures into ToolResult errors.",
        boundary="Single-call failure envelopes and ledger history; no automatic retry.",
        adapter="deterministic_grader.execution_failure_contract",
    ),
    "output.schema_validation": _supported(
        "output.schema_validation",
        evidence="ToolRuntime validates executor output against registered output schemas.",
        boundary="Schema and ToolResult identity normalization only; no semantic judge.",
        adapter="deterministic_grader.output_contract",
    ),
    "retrieval.not_found_semantics": _supported(
        "retrieval.not_found_semantics",
        evidence="ToolResult separates status=ok from business_status=NOT_FOUND.",
        boundary="Synthetic retrieval business status only; no live vault retrieval.",
        adapter="deterministic_grader.not_found_contract",
    ),
    "retrieval.partial_recovery": _supported(
        "retrieval.partial_recovery",
        evidence="Normalized observations can preserve valid evidence beside failed branches.",
        boundary="Observed partial-result contracts only; no runtime retry or orchestration rewrite.",
        adapter="deterministic_grader.partial_retrieval_contract",
    ),
    "ledger.duplicate_call_id": _supported(
        "ledger.duplicate_call_id",
        evidence="ToolExecutionLedger rejects duplicate call_id before a second executor run.",
        boundary="Ledger identity conflict only; no semantic repeat detection.",
        adapter="deterministic_grader.ledger_conflict_contract",
    ),
    "checkpoint.pending_safety": _supported(
        "checkpoint.pending_safety",
        evidence="PendingRecoveryDecision classifies read-only and unknown-side-effect records.",
        boundary="Recommendation and pending representation only; no resume or replay.",
        adapter="deterministic_grader.pending_safety_contract",
    ),
    "routing.model_driven": _future(
        "routing.model_driven",
        evidence="No accepted model-driven routing evaluation adapter exists.",
        boundary="Routing remains outside deterministic runtime-contract evaluation.",
        missing_reason="model-driven routing is not a formal current capability",
    ),
    "tool_selection.model_driven": _future(
        "tool_selection.model_driven",
        evidence="No accepted model-driven tool-selection evaluation adapter exists.",
        boundary="Tool selection quality is not inferred from expected labels.",
        missing_reason="model-driven tool selection is not a formal current capability",
    ),
    "guard.semantic_repeat": _future(
        "guard.semantic_repeat",
        evidence="The current ledger checks call identity, not semantic argument equivalence.",
        boundary="Duplicate call_id is supported; semantic repetition is not.",
        missing_reason="semantic repeated-call guard is not implemented",
    ),
    "guard.semantic_loop": _future(
        "guard.semantic_loop",
        evidence="No production semantic loop guard emits an accepted loop signal.",
        boundary="The grader can inspect a supplied signal but cannot create runtime behavior.",
        missing_reason="semantic loop detection is not implemented",
    ),
    "guard.premature_final": _future(
        "guard.premature_final",
        evidence="No accepted runtime guard blocks evidence-free final answers.",
        boundary="The grader can inspect an observed guard signal only.",
        missing_reason="premature-final guard is not implemented",
    ),
    "checkpoint.full_resume": _future(
        "checkpoint.full_resume",
        evidence="Current recovery code recommends a disposition and never resumes execution.",
        boundary="No retry, replay, deduplication, or exactly-once claim.",
        missing_reason="full checkpoint resume and recovery is not implemented",
    ),
    "memory.staleness": _future(
        "memory.staleness",
        evidence="No accepted runtime memory freshness detector exists.",
        boundary="Staleness signals may be graded only after a future producer exists.",
        missing_reason="memory staleness detection is not implemented",
    ),
    "memory.conflict": _future(
        "memory.conflict",
        evidence="No accepted runtime conflicting-memory detector exists.",
        boundary="Conflict signals may be graded only after a future producer exists.",
        missing_reason="memory conflict detection is not implemented",
    ),
    "llm.judge": _future(
        "llm.judge",
        evidence="Judge rubrics are dataset fields and no provider adapter is present.",
        boundary="Judge remains NOT_EVALUATED and cannot override deterministic grading.",
        missing_reason="LLM judge execution is intentionally not implemented",
    ),
}

CAPABILITY_MATRIX = MappingProxyType(_CAPABILITY_MATRIX)


@dataclass(frozen=True)
class CaseCapabilitySupport:
    executable: bool
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    missing_reasons: tuple[str, ...]


def assess_case_capabilities(case: TrajectoryCase) -> CaseCapabilitySupport:
    if not isinstance(case, TrajectoryCase):
        raise TypeError("case must be a TrajectoryCase")
    unknown = sorted(set(case.required_capabilities) - set(CAPABILITY_MATRIX))
    if unknown:
        raise ValueError(f"unknown trajectory capabilities: {unknown}")
    missing = tuple(
        capability_id
        for capability_id in case.required_capabilities
        if CAPABILITY_MATRIX[capability_id].status == FUTURE
    )
    reasons = tuple(
        CAPABILITY_MATRIX[capability_id].missing_reason or ""
        for capability_id in missing
    )
    return CaseCapabilitySupport(
        executable=not missing,
        required_capabilities=tuple(case.required_capabilities),
        missing_capabilities=missing,
        missing_reasons=reasons,
    )


def partition_case_ids(
    cases: Iterable[TrajectoryCase],
) -> tuple[set[str], set[str]]:
    executable: set[str] = set()
    future: set[str] = set()
    for case in cases:
        support = assess_case_capabilities(case)
        target = executable if support.executable else future
        target.add(case.case_id)
    return executable, future


def capability_matrix_payload() -> list[dict[str, str | None]]:
    return [
        CAPABILITY_MATRIX[capability_id].to_dict()
        for capability_id in sorted(CAPABILITY_MATRIX)
    ]


__all__ = [
    "CAPABILITY_MATRIX",
    "CAPABILITY_STATUSES",
    "FUTURE",
    "SUPPORTED",
    "CapabilityDefinition",
    "CaseCapabilitySupport",
    "assess_case_capabilities",
    "capability_matrix_payload",
    "partition_case_ids",
]
