"""Deterministic, evidence-backed reflection with conservative abstention."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .models import ExperienceProvenance, ExperienceRecord, ReflectionInput
from .authority import AuthoritativeEvaluationResult, EvaluationAuthorityError
from .policy import (
    EXPERIENCE_TEMPLATES,
    GENERATION_RULE_EVALUATOR_SCHEMAS,
    REGISTERED_FINDING_CODES,
    validate_candidate_record,
)


Clock = Callable[[], datetime]
_GENERATION_RULE = "explicit_rejection_requires_evidence/v1"
_REQUIRED_TASK_SIGNALS = frozenset(
    {"team_decision", "current_decision", "compared_options"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunReflection:
    """Map a closed evaluator projection to zero or one candidate Experience."""

    def __init__(self, *, authority=None, clock: Clock = _utc_now):
        self._authority = authority
        self._clock = clock

    def reflect(self, result: AuthoritativeEvaluationResult) -> tuple[ExperienceRecord, ...]:
        if self._authority is None or not isinstance(result, AuthoritativeEvaluationResult):
            raise EvaluationAuthorityError("Reflection requires configured authoritative evaluation result")
        result = self._authority.validate_result(result)
        if not result.generation_allowed or result.provenance is None:
            return ()
        evidence = result.projection
        template = EXPERIENCE_TEMPLATES[_GENERATION_RULE]
        if set(evidence.task_characteristics).intersection(template.applicability.exclude_when):
            return ()
        if not _REQUIRED_TASK_SIGNALS.issubset(evidence.task_characteristics):
            return ()
        if any(item.code not in REGISTERED_FINDING_CODES for item in evidence.findings):
            return ()
        if len(evidence.findings) != 1:
            return ()

        finding = evidence.findings[0]
        if not (
            finding.dimension == "semantic"
            and finding.outcome == "FAIL"
            and finding.code == _GENERATION_RULE
        ):
            return ()
        if (
            finding.evaluator_artifact_sha256
            != evidence.evaluator_artifact_sha256
            or evidence.evaluator_artifact_schema_version
            not in GENERATION_RULE_EVALUATOR_SCHEMAS[_GENERATION_RULE]
        ):
            return ()

        prefix = f"{evidence.source_run_id}:"
        refs: list[str] = []
        if not finding.source_evidence_refs:
            return ()
        if any(not ref.startswith(prefix) for ref in finding.source_evidence_refs):
            return ()
        if not set(finding.source_evidence_refs).issubset(
            evidence.available_source_evidence_refs
        ):
            return ()
        for ref in finding.source_evidence_refs:
            if ref not in refs:
                refs.append(ref)

        created_at = self._clock()
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        provenance = result.provenance
        record = ExperienceRecord.create(
            situation=template.situation,
            observed_outcome=template.observed_outcome,
            pattern_type=template.pattern_type,
            reusable_lesson=template.reusable_lesson,
            suggested_strategy=template.suggested_strategy,
            applicability=template.applicability,
            counterexamples_or_limits=template.counterexamples_or_limits,
            source_run_ids=(evidence.source_run_id,),
            source_evidence_refs=tuple(refs),
            confidence=template.confidence,
            created_at=created_at.astimezone(timezone.utc).isoformat(),
            status="candidate",
            generation_rule_id=template.generation_rule_id,
            provenance=(provenance,),
        )
        validate_candidate_record(record, authority=self._authority)
        return (record,)


__all__ = ["RunReflection"]
