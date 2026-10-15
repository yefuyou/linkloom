"""Deterministic Experience checks and authoritative evaluation projection."""

from __future__ import annotations

from dataclasses import dataclass, replace

from linkloom.experience.authority import AuthoritativeEvaluationResult, EvaluationAuthority, EvaluationAuthorityError
from linkloom.experience.context import render_records
from linkloom.experience.models import (
    ExperienceContextSection, ExperienceQuery, ExperienceRecord,
    ExperienceSelection,
)
from linkloom.experience.policy import (
    EXPERIENCE_TEMPLATES, ExperiencePolicyViolation, validate_candidate_shape,
    validate_gold_safe_payload,
)


class EvaluationFindingProjector:
    """Only project a result already issued by the configured authority."""

    def project(self, result=None, *, authority=None, **untrusted):
        if untrusted or authority is None or not isinstance(result, AuthoritativeEvaluationResult):
            raise EvaluationAuthorityError("ad-hoc findings are not an authoritative evaluation result")
        return authority.validate_result(result)


@dataclass(frozen=True)
class ExperienceDimensionResult:
    dimension: str
    status: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL"}:
            raise ValueError("dimension status must be PASS or FAIL")
        if not self.evidence:
            raise ValueError("dimension evidence must not be empty")


@dataclass(frozen=True)
class ExperienceEvaluationReport:
    schema_version: str
    dimensions: tuple[ExperienceDimensionResult, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "experience-evaluation/v1":
            raise ValueError("invalid Experience evaluation schema_version")
        names = tuple(item.dimension for item in self.dimensions)
        if len(names) != len(set(names)):
            raise ValueError("Experience evaluation dimensions must be unique")

    def statuses(self) -> dict[str, str]:
        return {item.dimension: item.status for item in self.dimensions}


def _result(
    dimension: str, failures: list[str], success: str
) -> ExperienceDimensionResult:
    return ExperienceDimensionResult(
        dimension=dimension,
        status="FAIL" if failures else "PASS",
        evidence=tuple(failures or [success]),
    )


def evaluate_reflection_correctness(
    record: ExperienceRecord, reflection_input: AuthoritativeEvaluationResult,
    *, authority: EvaluationAuthority | None = None,
) -> ExperienceDimensionResult:
    failures: list[str] = []
    try:
        if authority is None:
            raise EvaluationAuthorityError("missing configured authority")
        result = authority.validate_result(reflection_input)
    except (EvaluationAuthorityError, OSError, ValueError):
        return _result("reflection_correctness", ["non_authoritative_evaluation_result"], "")
    reflection_input = result.projection
    if not result.generation_allowed:
        failures.append("complete_evaluator_blocks_generation")
    template = EXPERIENCE_TEMPLATES.get(record.generation_rule_id)
    if template is None:
        failures.append("unsupported_generation_rule")
    else:
        try:
            validate_candidate_shape(replace(record, status="candidate", review_transition=None))
        except (ExperiencePolicyViolation, ValueError):
            failures.append("registered_template_mismatch")
    matching = tuple(
        item
        for item in reflection_input.findings
        if item.code == record.generation_rule_id
        and item.dimension == "semantic"
        and item.outcome == "FAIL"
    )
    if not matching:
        failures.append("causal_finding_missing")
    if len(reflection_input.findings) != 1:
        failures.append("ambiguous_or_mixed_findings")
    required_signals = set(record.applicability.task_characteristics)
    if not required_signals.issubset(reflection_input.task_characteristics):
        failures.append("applicability_not_supported")
    return _result(
        "reflection_correctness", failures, "registered_causal_rule_supported"
    )


def evaluate_gold_leakage(record: ExperienceRecord) -> ExperienceDimensionResult:
    failures: list[str] = []
    try:
        validate_gold_safe_payload(record.to_dict())
    except ExperiencePolicyViolation:
        failures.append("gold_safe_policy_violation")
    return _result("gold_leakage", failures, "closed_payload_is_gold_safe")


def evaluate_generalizability(
    record: ExperienceRecord,
) -> ExperienceDimensionResult:
    failures: list[str] = []
    if not record.applicability.workflows:
        failures.append("missing_workflow_applicability")
    if not record.applicability.task_characteristics:
        failures.append("missing_task_characteristics")
    if not record.counterexamples_or_limits:
        failures.append("missing_counterexample_or_limit")
    return _result(
        "generalizability", failures, "applicability_and_limits_are_explicit"
    )


def evaluate_retrieval_relevance(
    record: ExperienceRecord,
    query: ExperienceQuery,
    selection: ExperienceSelection,
) -> ExperienceDimensionResult:
    failures: list[str] = []
    signals = set(query.task_characteristics)
    applicable = (
        record.status == "accepted"
        and query.workflow in record.applicability.workflows
        and bool(signals.intersection(record.applicability.task_characteristics))
        and not signals.intersection(record.applicability.exclude_when)
        and (
            not query.pattern_types or record.pattern_type in query.pattern_types
        )
    )
    selected_ids = {item.experience_id for item in selection.records}
    if applicable and record.experience_id not in selected_ids:
        failures.append("relevant_record_not_selected")
    if not applicable and record.experience_id in selected_ids:
        failures.append("irrelevant_record_selected")
    return _result(
        "retrieval_relevance", failures, "selection_matches_structured_applicability"
    )


def evaluate_provenance(
    record: ExperienceRecord, reflection_input: AuthoritativeEvaluationResult,
    *, authority: EvaluationAuthority | None = None,
) -> ExperienceDimensionResult:
    failures: list[str] = []
    if not record.provenance:
        failures.append("missing_provenance")
    else:
        try:
            if authority is None:
                raise EvaluationAuthorityError("missing configured authority")
            result = authority.validate_result(reflection_input)
            authority.validate_record(record)
            if record.provenance != (result.provenance,):
                raise EvaluationAuthorityError("evaluation result identity mismatch")
        except (EvaluationAuthorityError, OSError, ValueError):
            failures.append("non_authoritative_provenance")
    return _result("provenance", failures, "provenance_resolves_to_sealed_authority")


def evaluate_bounded_context(
    selection: ExperienceSelection,
    context: ExperienceContextSection,
    *,
    max_chars: int,
) -> ExperienceDimensionResult:
    failures: list[str] = []
    if max_chars < 0 or max_chars > 4000:
        failures.append("invalid_context_budget")
    if len(selection.records) > 5:
        failures.append("top_k_hard_limit_exceeded")
    if len(context.model_text) > max_chars:
        failures.append("context_budget_exceeded")
    if context.model_text != render_records(selection.records):
        failures.append("context_render_mismatch")
    if selection.total_rendered_chars != len(context.model_text):
        failures.append("character_accounting_mismatch")
    expected_ids = tuple(item.experience_id for item in selection.records)
    if context.experience_ids != expected_ids:
        failures.append("trace_identity_mismatch")
    return _result(
        "bounded_context", failures, "hard_limits_and_rendering_are_consistent"
    )


def evaluate_experience(
    *,
    record: ExperienceRecord,
    reflection_input: AuthoritativeEvaluationResult,
    authority: EvaluationAuthority,
    query: ExperienceQuery,
    selection: ExperienceSelection,
    context: ExperienceContextSection,
    max_chars: int,
) -> ExperienceEvaluationReport:
    return ExperienceEvaluationReport(
        schema_version="experience-evaluation/v1",
        dimensions=(
            evaluate_reflection_correctness(record, reflection_input, authority=authority),
            evaluate_gold_leakage(record),
            evaluate_generalizability(record),
            evaluate_retrieval_relevance(record, query, selection),
            evaluate_provenance(record, reflection_input, authority=authority),
            evaluate_bounded_context(
                selection, context, max_chars=max_chars
            ),
        ),
    )


__all__ = [
    "EvaluationFindingProjector",
    "ExperienceDimensionResult",
    "ExperienceEvaluationReport",
    "evaluate_bounded_context",
    "evaluate_experience",
    "evaluate_generalizability",
    "evaluate_gold_leakage",
    "evaluate_provenance",
    "evaluate_reflection_correctness",
    "evaluate_retrieval_relevance",
]
