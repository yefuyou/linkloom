"""Subject diversity must survive duplicate high-scoring facts."""
from dataclasses import replace
from datetime import UTC, datetime

from test_fact_memory_evidence import _fact
from linkloom.fact_memory.ask import relevant_records


def record(candidate, subject, text, month=3):
    return replace(_fact(candidate, text, text, month, text),
                   subject_id=subject, subject_label=subject)


def test_duplicate_records_do_not_exhaust_the_subject_budget():
    records = (
        record("a1", "a", "orchid fertilizer"),
        record("a2", "a", "orchid fertilizer"),
        record("a3", "a", "orchid fertilizer"),
        record("b", "b", "orchid"),
        record("c", "c", "fertilizer"),
    )
    selected = relevant_records("orchid fertilizer", records, datetime(2026, 4, 1, tzinfo=UTC))
    assert {r.subject_id for r, score in selected} == {"a", "b", "c"}
    assert {r.candidate_id for r, score in selected} == {"a1", "a2", "a3", "b", "c"}


def test_subject_diversity_does_not_admit_future_or_unrelated_records():
    records = (
        record("a", "a", "orchid fertilizer"),
        record("b", "b", "orchid"),
        record("c", "c", "fertilizer"),
        record("unrelated", "d", "bicycle wheel"),
        record("future", "e", "orchid fertilizer", month=5),
        record("unrelated_same_subject", "a", "bicycle wheel"),
    )
    selected = relevant_records("orchid fertilizer", records, datetime(2026, 4, 1, tzinfo=UTC))
    assert {r.candidate_id for r, score in selected} == {"a", "b", "c"}


def test_subject_budget_still_limits_relevant_subjects_to_three():
    records = tuple(record(str(i), str(i), "orchid fertilizer") for i in range(4))
    selected = relevant_records("orchid fertilizer", records, datetime(2026, 4, 1, tzinfo=UTC))
    assert len({r.subject_id for r, score in selected}) == 3
