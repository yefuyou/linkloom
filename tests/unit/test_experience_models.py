from dataclasses import replace

import pytest

from linkloom.experience.models import (
    EvaluatorFinding,
    ExperienceApplicability,
    ExperienceContextSection,
    ExperienceProvenance,
    ExperienceQuery,
    ExperienceRecord,
    ExperienceReviewDecision,
    ExperienceReviewTransition,
    ExperienceSelection,
    ReflectionInput,
)


RUN_ID = "run_p4_961cf695f6f8"
OBSERVED_SHA = "a" * 64
EVALUATOR_SHA = "b" * 64
EVIDENCE_REFS = (
    f"{RUN_ID}:ev_p1_0016",
    f"{RUN_ID}:ev_p1_0018",
)


def finding(**overrides: object) -> EvaluatorFinding:
    values = {
        "finding_id": "finding.rejection_semantics",
        "dimension": "semantic",
        "outcome": "FAIL",
        "code": "explicit_rejection_requires_evidence/v1",
        "source_evidence_refs": EVIDENCE_REFS,
        "evaluator_artifact_sha256": EVALUATOR_SHA,
    }
    values.update(overrides)
    return EvaluatorFinding(**values)


def provenance() -> ExperienceProvenance:
    return ExperienceProvenance(
        source_run_id=RUN_ID,
        observed_summary_sha256=OBSERVED_SHA,
        evaluator_artifact_sha256=EVALUATOR_SHA,
        evaluator_artifact_schema_version="deepseek-posthoc-evaluation/v1",
        finding_ids=("finding.rejection_semantics",),
        source_evidence_refs=EVIDENCE_REFS,
        available_source_evidence_refs=EVIDENCE_REFS,
    )


def candidate(*, created_at: str = "2026-09-16T01:00:00+00:00") -> ExperienceRecord:
    return ExperienceRecord.create(
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
        source_run_ids=(RUN_ID,),
        source_evidence_refs=EVIDENCE_REFS,
        confidence="high",
        created_at=created_at,
        status="candidate",
        generation_rule_id="explicit_rejection_requires_evidence/v1",
        provenance=(provenance(),),
    )


def reviewed_shape(record: ExperienceRecord) -> ExperienceRecord:
    # Model contract example only; retrieval separately requires durable replay.
    decision = ExperienceReviewDecision("experience-review/v1", record.experience_id,
        "accepted", "reviewer:test", "independent_reviewer", "review:offline",
        "2026-09-16T02:00:00+00:00")
    return replace(record, status="accepted", review_transition=ExperienceReviewTransition(
        record.experience_id, "c" * 64, decision, "d" * 64, "e" * 64))


def test_reflection_input_is_closed_and_json_round_trips() -> None:
    value = ReflectionInput(
        schema_version="reflection-input/v1",
        source_run_id=RUN_ID,
        observed_summary_sha256=OBSERVED_SHA,
        evaluator_artifact_sha256=EVALUATOR_SHA,
        evaluator_artifact_schema_version="deepseek-posthoc-evaluation/v1",
        available_source_evidence_refs=EVIDENCE_REFS,
        findings=(finding(),),
        task_characteristics=("team_decision", "current_decision"),
    )

    assert ReflectionInput.from_dict(value.to_dict()) == value

    leaked = value.to_dict() | {"expected_answer": "forbidden"}
    with pytest.raises(ValueError, match="unknown fields"):
        ReflectionInput.from_dict(leaked)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"dimension": "planning"}, "dimension"),
        ({"outcome": "UNKNOWN"}, "outcome"),
        ({"evaluator_artifact_sha256": "not-a-hash"}, "SHA-256"),
        ({"source_evidence_refs": (EVIDENCE_REFS[0],) * 2}, "unique"),
        ({"source_evidence_refs": ("C:\\vault\\note.md",)}, "opaque"),
    ],
)
def test_evaluator_finding_rejects_invalid_contracts(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        finding(**overrides)


def test_experience_record_identity_excludes_time_and_review_status() -> None:
    first = candidate(created_at="2026-09-16T01:00:00+00:00")
    later = candidate(created_at="2026-09-17T01:00:00+00:00")
    accepted = reviewed_shape(first)

    assert first.experience_id == later.experience_id == accepted.experience_id
    assert ExperienceRecord.from_dict(first.to_dict()) == first

    changed = ExperienceRecord.create(
        **{
            **first.creation_payload(),
            "reusable_lesson": first.reusable_lesson + " Extra claim.",
            "created_at": first.created_at,
            "status": "candidate",
        }
    )
    assert changed.experience_id != first.experience_id


def test_experience_record_rejects_identity_tampering_and_bad_provenance() -> None:
    record = candidate()
    with pytest.raises(ValueError, match="identity"):
        replace(record, experience_id="f" * 64)
    with pytest.raises(ValueError, match="provenance"):
        replace(record, provenance=())
    with pytest.raises(ValueError, match="source_run_ids"):
        replace(record, source_run_ids=("run_other",))


def test_applicability_review_query_selection_and_context_are_closed() -> None:
    record = candidate()
    decision = ExperienceReviewDecision(
        schema_version="experience-review/v1",
        experience_id=record.experience_id,
        decision="accepted",
        actor="reviewer:gate4",
        source="independent_reviewer",
        evidence_ref="review:gate4:approve",
        reviewed_at="2026-09-16T02:00:00+00:00",
    )
    query = ExperienceQuery(
        workflow="team_decision",
        task_characteristics=("current_decision", "compared_options"),
    )
    selection = ExperienceSelection(
        records=(reviewed_shape(record),),
        total_rendered_chars=120,
    )
    context = ExperienceContextSection(
        model_text="Relevant prior experience\n- Lesson: example",
        experience_ids=(record.experience_id,),
        provenance=(provenance(),),
    )

    assert ExperienceReviewDecision.from_dict(decision.to_dict()) == decision
    assert ExperienceQuery.from_dict(query.to_dict()) == query
    assert ExperienceSelection.from_dict(selection.to_dict()) == selection
    assert ExperienceContextSection.from_dict(context.to_dict()) == context

    with pytest.raises(ValueError, match="candidate or rejected"):
        ExperienceSelection(records=(record,), total_rendered_chars=120)
    with pytest.raises(ValueError, match="accepted or rejected"):
        replace(decision, decision="candidate")
    with pytest.raises(ValueError, match="source"):
        replace(decision, source="reflection")


def test_contracts_reject_wrong_types_empty_values_and_non_json_data() -> None:
    with pytest.raises(ValueError, match="task_characteristics"):
        ReflectionInput(
            "reflection-input/v1",
            RUN_ID,
            OBSERVED_SHA,
            EVALUATOR_SHA,
            "deepseek-posthoc-evaluation/v1",
            EVIDENCE_REFS,
            (finding(),),
            (),
        )
    with pytest.raises(ValueError, match="workflows"):
        ExperienceApplicability((), ("current_decision",), ())
    with pytest.raises(ValueError, match="workflow"):
        ExperienceQuery("", ("current_decision",))
    with pytest.raises(ValueError, match="JSON-safe"):
        ExperienceContextSection(
            model_text={"bad": object()},  # type: ignore[arg-type]
            experience_ids=("a" * 64,),
            provenance=(provenance(),),
        )
