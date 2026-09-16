"""Deterministic structured retrieval for accepted Experience records."""

from __future__ import annotations

from collections.abc import Iterable

from .context import render_records
from .models import ExperienceQuery, ExperienceRecord, ExperienceSelection
from .policy import validate_experience_record


DEFAULT_TOP_K = 3
DEFAULT_MAX_CHARS = 2000
HARD_MAX_TOP_K = 5
HARD_MAX_CHARS = 4000


class ExperienceRetriever:
    def __init__(self, records: Iterable[ExperienceRecord], *, authority=None, store=None):
        values = tuple(records)
        if any(not isinstance(item, ExperienceRecord) for item in values):
            raise ValueError("records must contain ExperienceRecord values")
        self._records = values
        self._authority = authority
        self._store = store

    def retrieve(
        self,
        query: ExperienceQuery,
        *,
        top_k: int = DEFAULT_TOP_K,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> ExperienceSelection:
        if not isinstance(query, ExperienceQuery):
            raise ValueError("query must be ExperienceQuery")
        if (
            not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or top_k < 0
            or top_k > HARD_MAX_TOP_K
        ):
            raise ValueError(f"top_k must be between 0 and {HARD_MAX_TOP_K}")
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars < 0
            or max_chars > HARD_MAX_CHARS
        ):
            raise ValueError(
                f"max_chars must be between 0 and {HARD_MAX_CHARS}"
            )

        query_signals = set(query.task_characteristics)
        scored: list[tuple[int, ExperienceRecord]] = []
        for record in self._records:
            if record.status != "accepted":
                continue
            if self._authority is None:
                raise ValueError("retrieval requires configured evaluation authority")
            validate_experience_record(record, authority=self._authority)
            from .store import ExperienceStore
            if not isinstance(self._store, ExperienceStore):
                raise ValueError("retrieval requires persisted approval store")
            self._store.validate_reviewed_record(record)
            applicability = record.applicability
            if query.workflow not in applicability.workflows:
                continue
            if query_signals.intersection(applicability.exclude_when):
                continue
            overlap = len(query_signals.intersection(applicability.task_characteristics))
            if overlap == 0:
                continue
            if query.pattern_types and record.pattern_type not in query.pattern_types:
                continue
            pattern_score = 2 if record.pattern_type in query.pattern_types else 0
            scored.append((overlap + pattern_score, record))

        ranked = sorted(
            scored,
            key=lambda item: (-item[0], item[1].experience_id),
        )
        selected: list[ExperienceRecord] = []
        for _, record in ranked:
            if len(selected) >= top_k:
                break
            proposed = tuple((*selected, record))
            if len(render_records(proposed)) > max_chars:
                continue
            selected.append(record)
        records = tuple(selected)
        return ExperienceSelection(
            records=records,
            total_rendered_chars=len(render_records(records)),
        )


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_TOP_K",
    "HARD_MAX_CHARS",
    "HARD_MAX_TOP_K",
    "ExperienceRetriever",
]
