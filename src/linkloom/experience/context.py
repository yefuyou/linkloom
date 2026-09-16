"""Explicit, bounded model context rendering for accepted Experience."""

from __future__ import annotations

from .models import (
    ExperienceContextSection,
    ExperienceRecord,
    ExperienceSelection,
)
from .policy import validate_experience_record


CONTEXT_HEADING = "Relevant prior experience"


def render_record(record: ExperienceRecord) -> str:
    applicability = record.applicability
    workflows = ", ".join(applicability.workflows)
    characteristics = ", ".join(applicability.task_characteristics)
    exclusions = ", ".join(applicability.exclude_when) or "none"
    return (
        f"- Lesson: {record.reusable_lesson}\n"
        f"  Strategy: {record.suggested_strategy}\n"
        "  Applicability: "
        f"workflows={workflows}; characteristics={characteristics}; "
        f"exclude when={exclusions}"
    )


def render_records(records: tuple[ExperienceRecord, ...]) -> str:
    if not records:
        return ""
    return CONTEXT_HEADING + "\n" + "\n".join(render_record(item) for item in records)


class ExperienceContextBuilder:
    def __init__(self, *, authority=None, store=None, hard_top_k: int = 5, max_chars: int = 4000):
        if isinstance(hard_top_k, bool) or not isinstance(hard_top_k, int) or not 0 <= hard_top_k <= 5:
            raise ValueError("context hard top-k must be between 0 and 5")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 0 <= max_chars <= 4000:
            raise ValueError("context max_chars must be between 0 and 4000")
        self._authority = authority
        self._store = store
        self._hard_top_k = hard_top_k
        self._max_chars = max_chars

    def build(self, selection: ExperienceSelection) -> ExperienceContextSection:
        if not isinstance(selection, ExperienceSelection):
            raise ValueError("selection must be ExperienceSelection")
        # Recheck untrusted upstream accounting, then enforce final whole-record
        # bounds even if a caller bypassed ExperienceSelection's constructor.
        raw_length = (len(CONTEXT_HEADING) + 1 + sum(len(render_record(r)) for r in selection.records)
                      + len(selection.records) - 1) if selection.records else 0
        if raw_length != selection.total_rendered_chars:
            raise ValueError("selection character accounting is invalid")
        bounded = []
        seen = set()
        for record in selection.records:
            if len(bounded) >= self._hard_top_k:
                break
            if record.experience_id in seen:
                continue
            seen.add(record.experience_id)
            if record.status != "accepted":
                raise ValueError("context requires accepted Experience")
            validate_experience_record(record, authority=self._authority)
            from .store import ExperienceStore
            if not isinstance(self._store, ExperienceStore):
                raise ValueError("context requires persisted approval store")
            self._store.validate_reviewed_record(record)
            if len(render_records(tuple(bounded) + (record,))) <= self._max_chars:
                bounded.append(record)
        records = tuple(bounded)
        model_text = render_records(records)
        return ExperienceContextSection(
            model_text=model_text,
            experience_ids=tuple(item.experience_id for item in records),
            provenance=tuple(
                provenance
                for item in records
                for provenance in item.provenance
            ),
        )


__all__ = [
    "CONTEXT_HEADING",
    "ExperienceContextBuilder",
    "render_record",
    "render_records",
]
