"""Stable bad-case taxonomy for evaluator reports."""

from __future__ import annotations

from .models import BadCase, BadCaseCategory, BadCaseSeverity


def create_bad_case(
    source_id: str,
    target_id: str,
    category: BadCaseCategory,
    severity: BadCaseSeverity,
    reason: str,
    safe_refs: list[str],
) -> BadCase:
    return BadCase(
        source_id=source_id,
        target_id=target_id,
        category=category.value,
        severity=severity.value,
        reason=reason[:500].replace("\n", " "),
        safe_refs=list(safe_refs),
    )


def classify_link_case(
    source_id: str,
    target_id: str,
    gold_link: bool,
    predicted_link: bool,
    *,
    safe_refs: list[str] | None = None,
) -> BadCase | None:
    if gold_link == predicted_link:
        return None
    category = BadCaseCategory.FALSE_POSITIVE if predicted_link else BadCaseCategory.FALSE_NEGATIVE
    return create_bad_case(
        source_id,
        target_id,
        category,
        BadCaseSeverity.MEDIUM,
        category.value.replace("_", " "),
        safe_refs or [],
    )


def classify_relation_detail(
    source_id: str,
    target_id: str,
    *,
    direction_ok: bool,
    type_ok: bool,
    safe_refs: list[str] | None = None,
) -> list[BadCase]:
    cases: list[BadCase] = []
    if not direction_ok:
        cases.append(create_bad_case(source_id, target_id, BadCaseCategory.DIRECTION_ERROR, BadCaseSeverity.MEDIUM, "relation direction mismatch", safe_refs or []))
    if not type_ok:
        cases.append(create_bad_case(source_id, target_id, BadCaseCategory.TYPE_ERROR, BadCaseSeverity.MEDIUM, "relation type mismatch", safe_refs or []))
    return cases
