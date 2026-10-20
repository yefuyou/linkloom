from datetime import datetime, timezone

import pytest

from linkloom.experience.models import EvaluatorFinding, ReflectionInput
from linkloom.experience.reflection import RunReflection


RUN_ID = "run_p4_961cf695f6f8"
REFS = (f"{RUN_ID}:ev_p1_0016", f"{RUN_ID}:ev_p1_0018")


def finding(
    *,
    dimension: str = "semantic",
    outcome: str = "FAIL",
    code: str = "explicit_rejection_requires_evidence/v1",
    refs: tuple[str, ...] = REFS,
    finding_id: str = "finding.rejection_semantics",
    evaluator_sha256: str = "b" * 64,
) -> EvaluatorFinding:
    return EvaluatorFinding(
        finding_id=finding_id,
        dimension=dimension,
        outcome=outcome,
        code=code,
        source_evidence_refs=refs,
        evaluator_artifact_sha256=evaluator_sha256,
    )


def reflection_input(
    *findings: EvaluatorFinding,
    task_characteristics: tuple[str, ...] = (
        "team_decision",
        "current_decision",
        "compared_options",
    ),
) -> ReflectionInput:
    return ReflectionInput(
        schema_version="reflection-input/v1",
        source_run_id=RUN_ID,
        observed_summary_sha256="a" * 64,
        evaluator_artifact_sha256="b" * 64,
        evaluator_artifact_schema_version="deepseek-posthoc-evaluation/v1",
        available_source_evidence_refs=REFS,
        findings=findings or (finding(),),
        task_characteristics=task_characteristics,
    )


def fixed_clock() -> datetime:
    return datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)


from tests.experience_authority_support import fixture_authority, fixture_candidate, fixture_result

AUTHORITY = fixture_authority()


def test_supported_causal_finding_produces_one_candidate():
    result = fixture_result(AUTHORITY)
    record = RunReflection(authority=AUTHORITY, clock=fixed_clock).reflect(result)[0]
    assert record.status == "candidate"
    assert record.provenance == (result.provenance,)
    assert record.source_evidence_refs == result.provenance.source_evidence_refs


@pytest.mark.parametrize("unsupported", [
    finding(dimension="provider_availability", outcome="PARTIAL", code="provider_transient_before_final/v1"),
    finding(outcome="N/E"),
    finding(outcome="REVIEW_REQUIRED", code="semantic_mismatch_cause_unresolved/v1"),
    finding(code="unknown_finding/v1"), finding(refs=()),
    finding(refs=("run_other:ev_p1_0016",)),
])
def test_unsupported_or_incomplete_findings_abstain(unsupported):
    with pytest.raises(ValueError, match="authorit"):
        RunReflection(authority=AUTHORITY).reflect(reflection_input(unsupported))


def test_ambiguous_semantic_attribution_abstains():
    with pytest.raises(ValueError, match="authorit"):
        RunReflection(authority=AUTHORITY).reflect(reflection_input(finding(), finding(
            finding_id="finding.other", outcome="REVIEW_REQUIRED",
            code="semantic_mismatch_cause_unresolved/v1")))


@pytest.mark.parametrize("incomplete", [
    finding(finding_id="finding.provider", dimension="provider_availability",
            outcome="PARTIAL", code="provider_transient_before_final/v1", refs=()),
    finding(finding_id="finding.grounding", dimension="grounding", outcome="N/E",
            code="semantic_mismatch_cause_unresolved/v1", refs=()),
])
def test_supported_semantic_finding_mixed_with_incomplete_layer_abstains(incomplete):
    with pytest.raises(ValueError, match="authorit"):
        RunReflection(authority=AUTHORITY).reflect(reflection_input(finding(), incomplete))


def test_same_run_prefix_does_not_make_an_orphan_evidence_ref_valid():
    with pytest.raises(ValueError, match="authorit"):
        RunReflection(authority=AUTHORITY).reflect(reflection_input(
            finding(refs=(f"{RUN_ID}:ev_not_an_observation",))))


def test_mixed_evaluator_artifact_hashes_abstain():
    with pytest.raises(ValueError, match="authorit"):
        RunReflection(authority=AUTHORITY).reflect(reflection_input(
            finding(), finding(finding_id="finding.second", evaluator_sha256="c" * 64)))


def test_rule_requires_applicable_task_characteristics():
    with pytest.raises(ValueError, match="authorit"):
        RunReflection(authority=AUTHORITY).reflect(reflection_input(
            finding(), task_characteristics=("team_decision", "owner_lookup")))


def test_output_uses_only_registered_template_not_source_identifiers():
    record = fixture_candidate(AUTHORITY)
    semantic_text = " ".join((record.situation, record.observed_outcome,
        record.reusable_lesson, record.suggested_strategy, *record.counterexamples_or_limits))
    assert record.source_run_ids[0] not in semantic_text
    assert record.provenance[0].finding_ids[0] not in semantic_text


def test_identity_is_deterministic_across_clock_values():
    first = fixture_candidate(AUTHORITY)
    later = fixture_candidate(AUTHORITY, created_at="2026-09-17T03:00:00+00:00")
    assert first.created_at != later.created_at
    assert first.experience_id == later.experience_id


def test_reflection_resolves_only_local_authority_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("reflection attempted network access")
    monkeypatch.setattr("socket.create_connection", forbidden)
    assert len(RunReflection(authority=AUTHORITY).reflect(fixture_result(AUTHORITY))) == 1
