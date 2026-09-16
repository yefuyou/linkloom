from dataclasses import replace

import pytest

from linkloom.experience.context import ExperienceContextBuilder
from linkloom.experience.models import (
    ExperienceApplicability,
    ExperienceProvenance,
    ExperienceQuery,
    ExperienceRecord,
)
from linkloom.experience.retrieval import ExperienceRetriever


from functools import partial
from tests.experience_authority_support import fixture_authority, fixture_candidate, fixture_reviewed
from linkloom.experience.store import ExperienceStore

AUTHORITY = fixture_authority()
_Retriever = ExperienceRetriever
_Builder = ExperienceContextBuilder


@pytest.fixture(autouse=True)
def review_store(tmp_path):
    global STORE, ExperienceRetriever, ExperienceContextBuilder
    STORE = ExperienceStore(tmp_path / "review.jsonl", authority=AUTHORITY)
    ExperienceRetriever = partial(_Retriever, authority=AUTHORITY, store=STORE)
    ExperienceContextBuilder = partial(_Builder, authority=AUTHORITY, store=STORE)


def record(run_suffix: str, *, status: str = "accepted", applicability=None) -> ExperienceRecord:
    value = fixture_candidate(AUTHORITY, run_suffix)
    if status == "candidate":
        return value
    assert applicability is None
    return fixture_reviewed(STORE, AUTHORITY, run_suffix, status)


def matching_query(**overrides: object) -> ExperienceQuery:
    values = {
        "workflow": "team_decision",
        "task_characteristics": ("current_decision", "compared_options"),
        "pattern_types": ("evidence_semantics",),
    }
    values.update(overrides)
    return ExperienceQuery(**values)


def test_matching_query_retrieves_only_accepted_records() -> None:
    accepted = record("a1")
    candidate = record("b2", status="candidate")
    rejected = record("c3", status="rejected")

    selection = ExperienceRetriever((candidate, rejected, accepted)).retrieve(
        matching_query()
    )

    assert selection.records == (accepted,)
    assert selection.total_rendered_chars > 0


@pytest.mark.parametrize(
    "query",
    [
        matching_query(workflow="action_inbox"),
        matching_query(task_characteristics=("owner_lookup",)),
        matching_query(workflow="provider_incident"),
        matching_query(
            task_characteristics=(
                "current_decision",
                "explicit_rejection_evidence_present",
            )
        ),
    ],
)
def test_irrelevant_or_excluded_queries_return_no_experience(
    query: ExperienceQuery,
) -> None:
    assert ExperienceRetriever((record("a1"),)).retrieve(query).records == ()


def test_ranking_is_deterministic_for_registered_template_records() -> None:
    first = record("a1")
    high_a = record("b2")
    high_b = record("c3")

    selection = ExperienceRetriever((high_b, first, high_a)).retrieve(
        matching_query(), top_k=3
    )

    # The single v1 template has equal relevance scores; tie identity ordering is stable.
    expected = tuple(sorted((first, high_a, high_b), key=lambda item: item.experience_id))
    assert selection.records == expected
    assert ExperienceRetriever(tuple(reversed(expected))).retrieve(matching_query()).records == expected


def test_hard_limits_reject_widening_and_whole_records_respect_budget() -> None:
    retriever = ExperienceRetriever((record("a1"),))

    with pytest.raises(ValueError, match="top_k"):
        retriever.retrieve(matching_query(), top_k=6)
    with pytest.raises(ValueError, match="max_chars"):
        retriever.retrieve(matching_query(), max_chars=4001)

    too_small = retriever.retrieve(matching_query(), max_chars=80)
    assert too_small.records == ()
    assert too_small.total_rendered_chars == 0


def test_context_contains_only_model_safe_fields_and_trace_is_separate() -> None:
    accepted = record("a1")
    selection = ExperienceRetriever((accepted,)).retrieve(matching_query())

    context = ExperienceContextBuilder().build(selection)

    assert context.model_text.startswith("Relevant prior experience")
    assert accepted.reusable_lesson in context.model_text
    assert accepted.suggested_strategy in context.model_text
    assert "team_decision" in context.model_text
    assert accepted.experience_id not in context.model_text
    assert accepted.source_run_ids[0] not in context.model_text
    assert accepted.source_evidence_refs[0] not in context.model_text
    assert context.experience_ids == (accepted.experience_id,)
    assert context.provenance == accepted.provenance
    assert len(context.model_text) == selection.total_rendered_chars


def test_growth_is_bounded_by_top_k_and_context_character_limit() -> None:
    records = tuple(record(f"{letter}{index}") for index, letter in enumerate("abcde", 1))
    retriever = ExperienceRetriever(records)

    selection = retriever.retrieve(matching_query(), top_k=3, max_chars=2000)
    context = ExperienceContextBuilder().build(selection)

    assert len(selection.records) == 3
    assert len(context.model_text) <= 2000


def test_context_builder_rejects_tampered_character_accounting() -> None:
    accepted = record("a1")
    selection = ExperienceRetriever((accepted,)).retrieve(matching_query())

    with pytest.raises(ValueError, match="character accounting"):
        ExperienceContextBuilder().build(
            replace(selection, total_rendered_chars=selection.total_rendered_chars + 1)
        )
