"""Gold-safety and registered-template policy for Experience v1."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from .models import ExperienceApplicability, ExperienceRecord


class ExperiencePolicyViolation(ValueError):
    """Raised when Experience data crosses an approved safety boundary."""


REGISTERED_FINDING_CODES = frozenset(
    {
        "explicit_rejection_requires_evidence/v1",
        "provider_transient_before_final/v1",
        "semantic_mismatch_cause_unresolved/v1",
    }
)
REGISTERED_EVALUATOR_ARTIFACT_SCHEMAS = frozenset(
    {
        "deepseek-posthoc-evaluation/v1",
        "deepseek-business-posthoc/v1",
        "deepseek-business-case-run/v1",
    }
)
GENERATION_RULE_EVALUATOR_SCHEMAS = {
    "explicit_rejection_requires_evidence/v1": frozenset(
        {"complete-evaluation-result/v1", "historical-review-receipt/v1"}
    )
}

_FORBIDDEN_KEY_PARTS = (
    "answer_key",
    "benchmark_id",
    "case_id",
    "expected",
    "gold",
    "ground_truth",
    "selected_entity",
    "target_answer",
    "workspace_id",
)
_BENCHMARK_ID_RE = re.compile(r"\b(?:mps|aer|iti)-[0-9]+\b", re.IGNORECASE)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# Source identifiers are opaque references, not containers for case names,
# paths, or evaluator hints. Keep this grammar local to the policy boundary so
# callers can validate identifiers before constructing the looser historical
# model contracts.
_RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_EVIDENCE_REF_RE = re.compile(
    r"^(?P<run_id>run_[A-Za-z0-9][A-Za-z0-9_.-]{0,127}):"
    r"(?P<evidence_id>ev_[A-Za-z0-9][A-Za-z0-9_.-]{0,127})$"
)
_EMBEDDED_BENCHMARK_ID_RE = re.compile(
    r"(?:mps|aer|iti)-[0-9]+", re.IGNORECASE
)
_SENSITIVE_HINT_RE = re.compile(
    r"(?:expected|ground[_ -]?truth|answer[_ -]?key|gold(?:en)?)",
    re.IGNORECASE,
)
_EMBEDDED_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|://|:[\\/]|"
    r"(?<![A-Za-z0-9_])/(?:[^\s/]+/)*[^\s/]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExperienceTemplate:
    generation_rule_id: str
    situation: str
    observed_outcome: str
    pattern_type: str
    reusable_lesson: str
    suggested_strategy: str
    applicability: ExperienceApplicability
    counterexamples_or_limits: tuple[str, ...]
    confidence: str


EXPERIENCE_TEMPLATES = {
    "explicit_rejection_requires_evidence/v1": ExperienceTemplate(
        generation_rule_id="explicit_rejection_requires_evidence/v1",
        situation=(
            "A decision task uses a corpus containing compared, non-selected, "
            "or superseded alternatives alongside a current decision."
        ),
        observed_outcome=(
            "The response applied explicit rejection semantics even though "
            "the cited evidence established comparison, non-selection, or "
            "supersession only."
        ),
        pattern_type="evidence_semantics",
        reusable_lesson=(
            "Comparison, non-selection, or supersession does not by itself "
            "prove explicit rejection."
        ),
        suggested_strategy=(
            "Populate rejected semantics only when the cited evidence "
            "explicitly establishes rejection; otherwise omit the claim or "
            "preserve uncertainty."
        ),
        applicability=ExperienceApplicability(
            workflows=("team_decision",),
            task_characteristics=("current_decision", "compared_options"),
            exclude_when=("explicit_rejection_evidence_present",),
        ),
        counterexamples_or_limits=(
            "Explicit authoritative rejection evidence permits rejected semantics.",
        ),
        confidence="high",
    )
}


def _validate_key(key: Any) -> None:
    if not isinstance(key, str) or not key:
        raise ExperiencePolicyViolation("payload keys must be non-empty text")
    normalized = key.casefold().replace("-", "_")
    if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
        raise ExperiencePolicyViolation(f"forbidden evaluation field: {key}")


def _validate_text(value: str) -> None:
    if len(value) > 4000:
        raise ExperiencePolicyViolation("Experience text exceeds the hard limit")
    if _EMBEDDED_PATH_RE.search(value) or value.startswith("/"):
        raise ExperiencePolicyViolation("absolute paths are forbidden")
    if _EMBEDDED_BENCHMARK_ID_RE.search(value) or _BENCHMARK_ID_RE.search(value):
        raise ExperiencePolicyViolation("benchmark identifiers are forbidden")
    if _SENSITIVE_HINT_RE.search(value):
        raise ExperiencePolicyViolation("evaluation hints are forbidden")


def validate_source_identifiers(
    run_id: str, refs: tuple[str, ...] | list[str]
) -> None:
    """Validate a run ID and its fully qualified opaque evidence references.

    A source reference must have the exact form ``run_<opaque>:ev_<opaque>``;
    allowing a free-form opaque string here would let benchmark IDs, paths, or
    expected/Gold hints hide inside provenance. The helper intentionally
    returns ``None`` on success so model and authority boundaries can call it
    without changing their existing contracts.
    """
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ExperiencePolicyViolation(
            "run_id must be a full opaque run identifier"
        )
    if (
        _EMBEDDED_BENCHMARK_ID_RE.search(run_id)
        or _SENSITIVE_HINT_RE.search(run_id)
        or _EMBEDDED_PATH_RE.search(run_id)
    ):
        raise ExperiencePolicyViolation(
            "run_id contains a forbidden identifier hint"
        )
    if not isinstance(refs, (tuple, list)):
        raise ExperiencePolicyViolation("evidence refs must be an array")
    for ref in refs:
        if not isinstance(ref, str):
            raise ExperiencePolicyViolation("evidence refs must be text")
        match = _EVIDENCE_REF_RE.fullmatch(ref)
        if match is None or match.group("run_id") != run_id:
            raise ExperiencePolicyViolation(
                "evidence refs must be fully qualified opaque identifiers"
            )
        evidence_id = match.group("evidence_id")
        if (
            _EMBEDDED_BENCHMARK_ID_RE.search(evidence_id)
            or _SENSITIVE_HINT_RE.search(evidence_id)
            or _EMBEDDED_PATH_RE.search(evidence_id)
        ):
            raise ExperiencePolicyViolation(
                "evidence ref contains a forbidden identifier hint"
            )


def validate_gold_safe_payload(value: Any) -> None:
    """Reject answer-bearing, path-bearing, or non-JSON Experience data."""

    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ExperiencePolicyViolation("non-finite numbers are not JSON-safe")
        return
    if isinstance(value, str):
        _validate_text(value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_key(key)
            validate_gold_safe_payload(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_gold_safe_payload(item)
        return
    raise ExperiencePolicyViolation("payload must be JSON-safe")


def validate_candidate_shape(record: ExperienceRecord) -> None:
    """Check closed/template shape only; this is not provenance authorization."""
    if not isinstance(record, ExperienceRecord):
        raise ExperiencePolicyViolation("record must be an ExperienceRecord")
    try:
        validated = ExperienceRecord.from_dict(record.to_dict())
    except ValueError as exc:
        raise ExperiencePolicyViolation(
            "record provenance or identity is not closed"
        ) from exc
    if validated != record:
        raise ExperiencePolicyViolation("record failed closed-contract replay")
    if record.status != "candidate":
        raise ExperiencePolicyViolation("new Experience must remain candidate")
    template = EXPERIENCE_TEMPLATES.get(record.generation_rule_id)
    if template is None:
        raise ExperiencePolicyViolation("generation rule is not registered")
    allowed_schemas = GENERATION_RULE_EVALUATOR_SCHEMAS[
        record.generation_rule_id
    ]
    if any(
        item.evaluator_artifact_schema_version not in allowed_schemas
        for item in record.provenance
    ):
        raise ExperiencePolicyViolation(
            "evaluator artifact schema is not valid for the generation rule"
        )
    expected = {
        "situation": template.situation,
        "observed_outcome": template.observed_outcome,
        "pattern_type": template.pattern_type,
        "reusable_lesson": template.reusable_lesson,
        "suggested_strategy": template.suggested_strategy,
        "applicability": template.applicability,
        "counterexamples_or_limits": template.counterexamples_or_limits,
        "confidence": template.confidence,
    }
    for field_name, expected_value in expected.items():
        if getattr(record, field_name) != expected_value:
            raise ExperiencePolicyViolation(
                f"{field_name} must equal the registered template"
            )
    for provenance in record.provenance:
        validate_source_identifiers(
            provenance.source_run_id, provenance.source_evidence_refs
        )
        validate_source_identifiers(
            provenance.source_run_id,
            provenance.available_source_evidence_refs,
        )
    validate_gold_safe_payload(record.to_dict())


def validate_candidate_record(record: ExperienceRecord, *, authority=None) -> None:
    if authority is None:
        raise ExperiencePolicyViolation("candidate requires configured evaluation authority")
    validate_candidate_shape(record)
    authority.validate_record(record)


def validate_experience_record(record: ExperienceRecord, *, authority=None) -> None:
    """Revalidate immutable record content/authority without promoting its status."""
    if not isinstance(record, ExperienceRecord):
        raise ExperiencePolicyViolation("record must be an ExperienceRecord")
    # Inspect the original lifecycle payload before normalizing its status so
    # any persisted review-transition metadata is covered by Gold-safe policy.
    validate_gold_safe_payload(record.to_dict())
    # Validate original lifecycle shape before normalizing status for candidate policy.
    ExperienceRecord.from_dict(record.to_dict())
    validate_candidate_record(replace(record, status="candidate", review_transition=None), authority=authority)


__all__ = [
    "EXPERIENCE_TEMPLATES",
    "GENERATION_RULE_EVALUATOR_SCHEMAS",
    "REGISTERED_EVALUATOR_ARTIFACT_SCHEMAS",
    "REGISTERED_FINDING_CODES",
    "ExperiencePolicyViolation",
    "ExperienceTemplate",
    "validate_candidate_record",
    "validate_candidate_shape",
    "validate_experience_record",
    "validate_gold_safe_payload",
    "validate_source_identifiers",
]
