from dataclasses import replace
import pytest

from linkloom.evaluation.experience import (
    evaluate_bounded_context,
    evaluate_experience,
    evaluate_generalizability,
    evaluate_gold_leakage,
    evaluate_provenance,
    evaluate_reflection_correctness,
    evaluate_retrieval_relevance,
)
from linkloom.experience.context import ExperienceContextBuilder
from linkloom.experience.models import (
    EvaluatorFinding,
    ExperienceProvenance,
    ExperienceQuery,
    ExperienceRecord,
    ExperienceSelection,
    ReflectionInput,
)
from linkloom.experience.policy import EXPERIENCE_TEMPLATES
from linkloom.experience.retrieval import ExperienceRetriever


RUN_ID = "run_p4_961cf695f6f8"
RULE_ID = "explicit_rejection_requires_evidence/v1"
REFS = (f"{RUN_ID}:ev_p1_0016", f"{RUN_ID}:ev_p1_0018")


from functools import partial
from tests.experience_authority_support import fixture_authority, fixture_candidate, fixture_result, fixture_reviewed
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


def evidence():
    return fixture_result(AUTHORITY)


def accepted_record():
    return fixture_reviewed(STORE, AUTHORITY)


def query() -> ExperienceQuery:
    return ExperienceQuery(
        workflow="team_decision",
        task_characteristics=("current_decision", "compared_options"),
        pattern_types=("evidence_semantics",),
    )


def selection_and_context() -> tuple[ExperienceSelection, object]:
    selection = ExperienceRetriever((accepted_record(),)).retrieve(query())
    return selection, ExperienceContextBuilder().build(selection)


def test_experience_report_keeps_six_dimensions_separate() -> None:
    record = accepted_record()
    selection, context = selection_and_context()

    report = evaluate_experience(
        record=record,
        reflection_input=evidence(),
        authority=AUTHORITY,
        query=query(),
        selection=selection,
        context=context,
        max_chars=2000,
    )

    assert report.schema_version == "experience-evaluation/v1"
    assert report.statuses() == {
        "reflection_correctness": "PASS",
        "gold_leakage": "PASS",
        "generalizability": "PASS",
        "retrieval_relevance": "PASS",
        "provenance": "PASS",
        "bounded_context": "PASS",
    }
    assert not hasattr(report, "overall")
    assert not hasattr(report, "score")


def test_unsupported_template_fails_correctness_dimension() -> None:
    record = accepted_record()
    object.__setattr__(record, "generation_rule_id", "unknown_rule/v1")

    result = evaluate_reflection_correctness(record, evidence(), authority=AUTHORITY)

    assert result.status == "FAIL"
    assert "unsupported_generation_rule" in result.evidence


def test_case_specific_identifier_fails_leakage_dimension() -> None:
    record = accepted_record()
    object.__setattr__(record, "reusable_lesson", "mps-001 maps to a target")

    result = evaluate_gold_leakage(record)

    assert result.status == "FAIL"
    assert "gold_safe_policy_violation" in result.evidence


def test_missing_limits_fail_generalizability_dimension() -> None:
    record = accepted_record()
    object.__setattr__(record, "counterexamples_or_limits", ())

    result = evaluate_generalizability(record)

    assert result.status == "FAIL"
    assert "missing_counterexample_or_limit" in result.evidence


def test_missing_provenance_fails_only_provenance_check() -> None:
    record = accepted_record()
    object.__setattr__(record, "provenance", ())

    result = evaluate_provenance(record, evidence(), authority=AUTHORITY)

    assert result.status == "FAIL"
    assert "missing_provenance" in result.evidence


def test_provenance_report_cannot_trust_fabricated_projection_catalog() -> None:
    real = accepted_record()
    ref = "run_authority_a1:ev_fabricated"
    fake_provenance = replace(real.provenance[0], source_evidence_refs=(ref,),
                              available_source_evidence_refs=(ref,))
    fake_record = ExperienceRecord.create(**(real.creation_payload() | {
        "status": "candidate", "review_transition": None,
        "source_evidence_refs": (ref,), "provenance": (fake_provenance,)}))
    projection = evidence().projection
    fake_projection = replace(projection, available_source_evidence_refs=(ref,),
        findings=(replace(projection.findings[0], source_evidence_refs=(ref,)),))
    assert evaluate_provenance(fake_record, fake_projection).status == "FAIL"
    assert evaluate_provenance(fake_record, evidence(), authority=AUTHORITY).status == "FAIL"


def test_missing_relevant_selection_fails_relevance_dimension() -> None:
    empty = ExperienceSelection(records=(), total_rendered_chars=0)

    result = evaluate_retrieval_relevance(accepted_record(), query(), empty)

    assert result.status == "FAIL"
    assert "relevant_record_not_selected" in result.evidence


def test_context_overflow_or_tampering_fails_bounded_dimension() -> None:
    selection, context = selection_and_context()
    too_small = evaluate_bounded_context(selection, context, max_chars=10)
    leaked = replace(
        context,
        model_text=context.model_text + accepted_record().experience_id,
    )
    tampered = evaluate_bounded_context(selection, leaked, max_chars=4000)

    assert too_small.status == "FAIL"
    assert "context_budget_exceeded" in too_small.evidence
    assert tampered.status == "FAIL"
    assert "context_render_mismatch" in tampered.evidence
