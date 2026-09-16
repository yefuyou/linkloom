import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from linkloom.evaluation.experience import EvaluationFindingProjector
from linkloom.experience.models import (
    EvaluatorFinding, ExperienceProvenance, ExperienceRecord, ReflectionInput,
)
from linkloom.experience.policy import EXPERIENCE_TEMPLATES
from linkloom.experience.reflection import RunReflection
from linkloom.experience.store import ExperienceStore
from linkloom.experience.authority import AuthoritativeEvaluationResult
from tests.experience_authority_support import (
    fixture_authority, fixture_result, fixture_candidate, fixture_reviewed, synthetic_evaluator_bundle,
)

RUN = "run_authority_test"
REF = f"{RUN}:ev_real"
RULE = "explicit_rejection_requires_evidence/v1"
SIGNALS = ("team_decision", "current_decision", "compared_options")


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def legacy_input(ref: str = REF) -> ReflectionInput:
    return ReflectionInput(
        "reflection-input/v1", RUN, "a" * 64, "b" * 64,
        "deepseek-posthoc-evaluation/v1", (ref,),
        (EvaluatorFinding("finding.semantic", "semantic", "FAIL", RULE,
                          (ref,), "b" * 64),), SIGNALS,
    )


def forged_record() -> ExperienceRecord:
    ref = f"{RUN}:ev_fabricated"
    template = EXPERIENCE_TEMPLATES[RULE]
    provenance = ExperienceProvenance(
        RUN, "a" * 64, "b" * 64, "complete-evaluation-result/v1",
        ("finding.semantic",), (ref,), (ref,),
    )
    return ExperienceRecord.create(
        situation=template.situation, observed_outcome=template.observed_outcome,
        pattern_type=template.pattern_type, reusable_lesson=template.reusable_lesson,
        suggested_strategy=template.suggested_strategy,
        applicability=template.applicability,
        counterexamples_or_limits=template.counterexamples_or_limits,
        source_run_ids=(RUN,), source_evidence_refs=(ref,), confidence=template.confidence,
        created_at="2026-09-16T03:00:00+00:00", status="candidate",
        generation_rule_id=RULE, provenance=(provenance,),
    )


def test_reviewer_omission_attack_does_not_generate_candidate() -> None:
    observed = encode({"schema_version": "deepseek-real-smoke-observed/v1",
                       "case_id": "synthetic-case", "run_id": RUN,
                       "visible_evidence_refs": ["ev_real"]})
    seal = encode({"schema_version": "deepseek-real-smoke-seal/v1", "sealed": True,
                   "gold_available": False,
                   "observed_summary_sha256": hashlib.sha256(observed).hexdigest()})
    evaluator = encode({"schema_version": "deepseek-posthoc-evaluation/v1",
                        "case_id": "synthetic-case", "gold_accessed": True,
                        "infrastructure": "PASS", "grounding": "N/E",
                        "semantic": "FAIL",
                        "correctness": {"rejected_alternatives": False}})
    try:
        projected = EvaluationFindingProjector().project(
            source_run_id=RUN, observed_summary=observed, observed_seal=seal,
            evaluator_artifact=evaluator,
            findings=({"finding_id": "finding.semantic", "dimension": "semantic",
                       "outcome": "FAIL", "code": RULE, "source_evidence_refs": [REF]},),
            task_characteristics=SIGNALS,
        )
    except ValueError:
        return
    assert RunReflection().reflect(projected) == ()


def test_reviewer_fabricated_ref_and_catalog_are_not_authority() -> None:
    with pytest.raises(ValueError, match="authorit"):
        RunReflection().reflect(legacy_input(f"{RUN}:ev_fabricated"))


def test_store_direct_fabricated_provenance_is_rejected(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.jsonl", authority=fixture_authority())
    with pytest.raises(ValueError, match="authorit"):
        store.save(forged_record())
    assert not store.log_path.exists()


def test_complete_supporting_dimensions_pass_can_generate_candidate() -> None:
    authority = fixture_authority()
    result = fixture_result(authority)
    record = RunReflection(authority=authority).reflect(result)[0]
    assert record.status == "candidate"
    assert record.provenance == (result.provenance,)


@pytest.mark.parametrize("dimension", ["grounding", "contract"])
def test_ne_dimension_without_caller_companion_cannot_be_omitted(tmp_path, dimension):
    authority, identity = synthetic_evaluator_bundle(tmp_path, states={dimension: "N/E"})
    # No request findings or companion are supplied: the sealed evaluator owns them.
    assert RunReflection(authority=authority).reflect(authority.resolve(identity)) == ()


@pytest.mark.parametrize("state", ["FAIL", "N/E", "BLOCKED", "PARTIAL"])
def test_complete_nonpass_supporting_dimension_blocks_generation(tmp_path, state):
    authority, identity = synthetic_evaluator_bundle(tmp_path, states={"grounding": state})
    assert RunReflection(authority=authority).reflect(authority.resolve(identity)) == ()


def test_missing_required_dimension_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(
        tmp_path, edit=lambda o, e: e["dimensions"].pop("contract"))
    with pytest.raises(ValueError, match="required evaluation dimension"):
        authority.resolve(identity)


@pytest.mark.parametrize("mutation", ["catalog_type", "dimension_type", "signals_type", "source_digest"])
def test_complete_artifact_rejects_malformed_closed_field_types(tmp_path, mutation):
    def edit(observed, evaluation):
        if mutation == "catalog_type":
            observed["visible_evidence_refs"] = [[]]
        elif mutation == "dimension_type":
            evaluation["dimensions"]["contract"] = []
        elif mutation == "signals_type":
            evaluation["task_characteristics"] = dict.fromkeys(SIGNALS, "caller assertion")
        else:
            evaluation["source_evaluator_sha256"] = "unidentified evaluator"
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=edit)
    with pytest.raises(ValueError):
        authority.resolve(identity)


@pytest.mark.parametrize("dimension", ["grounding", "contract"])
def test_omitted_blocking_companion_in_sealed_result_is_rejected(tmp_path, dimension):
    authority, identity = synthetic_evaluator_bundle(
        tmp_path, states={dimension: "N/E"},
        edit=lambda o, e: e.update(findings=[f for f in e["findings"] if f["dimension"] == "semantic"]))
    with pytest.raises(ValueError, match="omitted blocking dimension"):
        authority.resolve(identity)


def test_uncompleted_evaluator_result_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=lambda o, e: e.update(completed=False))
    with pytest.raises(ValueError, match="complete evaluator"):
        authority.resolve(identity)


def test_caller_cannot_supply_findings_even_with_configured_authority():
    with pytest.raises(ValueError, match="authorit"):
        EvaluationFindingProjector().project(findings=legacy_input().findings, authority=fixture_authority())


def test_receipt_from_different_bootstrap_authority_is_rejected():
    authority = fixture_authority()
    with pytest.raises(ValueError, match="authority"):
        RunReflection(authority=authority).reflect(fixture_result(fixture_authority()))


def test_caller_cannot_omit_dimension_by_rewriting_issued_receipt(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, states={"grounding": "N/E"})
    real = authority.resolve(identity)
    fake = replace(real, dimensions=tuple((d, "PASS" if d == "grounding" else s) for d, s in real.dimensions),
        projection=replace(real.projection, findings=tuple(f for f in real.projection.findings if f.dimension == "semantic")))
    with pytest.raises(ValueError, match="caller-rewritten"):
        RunReflection(authority=authority).reflect(fake)


def test_decoy_outside_sealed_evidence_collection_is_not_resolvable(tmp_path):
    def edit(observed, evaluation):
        observed["untrusted_note"] = "ev_decoy"
        evaluation["findings"][0]["source_evidence_refs"] = ["run_authority_a1:ev_decoy"]
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=edit)
    with pytest.raises(ValueError, match="authoritative sealed run"):
        authority.resolve(identity)


def test_canonical_cross_run_provenance_cannot_be_saved_or_accepted(tmp_path):
    from linkloom.experience.models import ExperienceReviewDecision
    authority = fixture_authority()
    real = fixture_candidate(authority)
    wrong = replace(real.provenance[0], source_run_id="run_authority_b2",
        source_evidence_refs=("run_authority_b2:ev_real",),
        available_source_evidence_refs=("run_authority_b2:ev_real",))
    forged = ExperienceRecord.create(**(real.creation_payload() | {
        "source_run_ids": ("run_authority_b2",), "source_evidence_refs": wrong.source_evidence_refs,
        "provenance": (wrong,)}))
    store = ExperienceStore(tmp_path / "experience.jsonl", authority=authority)
    with pytest.raises(ValueError, match="authoritative sealed evidence"):
        store.save(forged)
    assert not store.log_path.exists()
    # Even bypassing save's public API cannot make a review promote fabricated provenance.
    store._records[forged.experience_id] = forged
    decision = ExperienceReviewDecision("experience-review/v1", forged.experience_id,
        "accepted", "reviewer:test", "independent_reviewer", "review:test", "2026-09-16T03:00:00+00:00")
    with pytest.raises(ValueError, match="authoritative sealed evidence"):
        store.record_review(decision)
    assert store._records[forged.experience_id].status == "candidate"
    with pytest.raises(ValueError, match="authoritative sealed evidence"):
        store.get(forged.experience_id)
    assert not store.log_path.exists()


def test_manually_reconstructed_authoritative_result_is_rejected():
    authority = fixture_authority()
    real = fixture_result(authority)
    fake = replace(real, projection=legacy_input(), _issuer=object())
    with pytest.raises(ValueError, match="authority"):
        RunReflection(authority=authority).reflect(fake)


def test_evaluator_run_identity_mismatch_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=lambda o, e: e.update(run_id="run_other"))
    with pytest.raises(ValueError, match="identity mismatch"):
        authority.resolve(identity)


def test_all_dimensions_pass_abstains_without_a_semantic_failure(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, states={"semantic": "PASS"})
    assert RunReflection(authority=authority).reflect(authority.resolve(identity)) == ()


def test_real_ref_with_wrong_run_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=lambda o, e:
        e["findings"][0].update(source_evidence_refs=["run_other:ev_real"]))
    with pytest.raises(ValueError, match="authoritative sealed run"):
        authority.resolve(identity)


def test_fabricated_ref_not_in_sealed_catalog_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=lambda o, e:
        e["findings"][0].update(source_evidence_refs=["run_authority_a1:ev_fabricated"]))
    with pytest.raises(ValueError, match="authoritative sealed run"):
        authority.resolve(identity)


@pytest.mark.parametrize("field,value", [
    ("source_run_id", "run_other"),
    ("observed_summary_sha256", "c" * 64),
    ("observed_seal_sha256", "c" * 64),
    ("source_identity", "c" * 64),
    ("evaluation_result_id", "evaluation_" + "c" * 64),
])
def test_real_ref_caller_rewritten_metadata_is_rejected(tmp_path, field, value):
    authority = fixture_authority()
    record = fixture_candidate(authority)
    object.__setattr__(record.provenance[0], field, value)
    with pytest.raises(ValueError):
        ExperienceStore(tmp_path / "experience.jsonl", authority=authority).save(record)


def test_fabricated_catalog_with_real_receipt_is_rejected(tmp_path):
    authority = fixture_authority()
    real = fixture_candidate(authority)
    ref = "run_authority_a1:ev_fabricated"
    provenance = replace(real.provenance[0], source_evidence_refs=(ref,), available_source_evidence_refs=(ref,))
    forged = ExperienceRecord.create(**(real.creation_payload() | {
        "source_evidence_refs": (ref,), "provenance": (provenance,)}))
    with pytest.raises(ValueError, match="authoritative sealed evidence"):
        ExperienceStore(tmp_path / "experience.jsonl", authority=authority).save(forged)


def test_catalog_changed_after_authority_bootstrap_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path)
    result = authority.resolve(identity)
    payload = json.loads((tmp_path / "observed.json").read_bytes())
    payload["visible_evidence_refs"].append("ev_fabricated")
    (tmp_path / "observed.json").write_bytes(encode(payload))
    with pytest.raises(ValueError, match="identity mismatch|stale"):
        RunReflection(authority=authority).reflect(result)


def test_catalog_present_but_run_seal_mismatch_is_rejected(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path)
    seal = json.loads((tmp_path / "observed-seal.json").read_bytes())
    seal["observed_summary_sha256"] = "c" * 64
    (tmp_path / "observed-seal.json").write_bytes(encode(seal))
    with pytest.raises(ValueError, match="identity mismatch"):
        authority.resolve(identity)


def test_candidate_review_cannot_bypass_stale_provenance(tmp_path):
    from linkloom.experience.models import ExperienceReviewDecision
    authority, identity = synthetic_evaluator_bundle(tmp_path / "authority")
    record = RunReflection(authority=authority).reflect(authority.resolve(identity))[0]
    store = ExperienceStore(tmp_path / "experience.jsonl", authority=authority)
    store.save(record)
    (tmp_path / "authority" / "observed.json").write_bytes(b"{}")
    decision = ExperienceReviewDecision("experience-review/v1", record.experience_id,
        "accepted", "reviewer:test", "independent_reviewer", "review:test", "2026-09-16T03:00:00+00:00")
    with pytest.raises(ValueError, match="identity mismatch"):
        store.record_review(decision)
    assert store._records[record.experience_id].status == "candidate"
    with pytest.raises(ValueError, match="identity mismatch"):
        store.get(record.experience_id)
    with pytest.raises(ValueError):
        ExperienceStore(store.log_path, authority=authority)


@pytest.mark.parametrize("field", ["reusable_lesson", "applicability"])
def test_retrieval_revalidates_record_shape_not_only_real_receipt(tmp_path, field):
    from linkloom.experience.models import ExperienceQuery, ExperienceApplicability
    from linkloom.experience.retrieval import ExperienceRetriever
    authority = fixture_authority()
    store = ExperienceStore(tmp_path / "review.jsonl", authority=authority)
    real = fixture_reviewed(store, authority)
    value = ("caller fabricated lesson" if field == "reusable_lesson" else
             ExperienceApplicability(("team_decision",), ("current_decision",), ()))
    forged = ExperienceRecord.create(**(real.creation_payload() | {
        "status": "candidate", "review_transition": None, field: value}))
    object.__setattr__(forged, "status", "accepted")
    object.__setattr__(forged, "review_transition", replace(real.review_transition,
        candidate_experience_id=forged.experience_id,
        review_decision=replace(real.review_transition.review_decision, experience_id=forged.experience_id)))
    # Canonical identity and real receipt are valid; template text is not.
    with pytest.raises(ValueError, match="registered template"):
        ExperienceRetriever((forged,), authority=authority, store=store).retrieve(
            ExperienceQuery("team_decision", ("current_decision",)))


@pytest.mark.parametrize("accessor", ["get", "list", "provenance"])
def test_store_reads_cannot_return_stale_authoritative_provenance(tmp_path, accessor):
    authority, identity = synthetic_evaluator_bundle(tmp_path / "authority")
    record = RunReflection(authority=authority).reflect(authority.resolve(identity))[0]
    store = ExperienceStore(tmp_path / "experience.jsonl", authority=authority)
    store.save(record)
    (tmp_path / "authority" / "observed.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="identity mismatch"):
        if accessor == "list":
            store.list()
        else:
            getattr(store, accessor)(record.experience_id)


def test_context_cannot_render_direct_caller_forged_selection(tmp_path):
    from linkloom.experience.models import ExperienceSelection
    from linkloom.experience.context import ExperienceContextBuilder, render_records
    authority = fixture_authority()
    store = ExperienceStore(tmp_path / "review.jsonl", authority=authority)
    real = fixture_reviewed(store, authority)
    forged = ExperienceRecord.create(**(real.creation_payload() | {
        "status": "candidate", "review_transition": None, "reusable_lesson": "caller fabricated lesson"}))
    object.__setattr__(forged, "status", "accepted")
    object.__setattr__(forged, "review_transition", replace(real.review_transition,
        candidate_experience_id=forged.experience_id,
        review_decision=replace(real.review_transition.review_decision, experience_id=forged.experience_id)))
    selection = ExperienceSelection((forged,), len(render_records((forged,))))
    with pytest.raises(ValueError):
        ExperienceContextBuilder().build(selection)
    with pytest.raises(ValueError, match="registered template"):
        ExperienceContextBuilder(authority=authority, store=store).build(selection)


def test_context_rechecks_sealed_authority_after_retrieval(tmp_path):
    from linkloom.experience.models import ExperienceQuery
    from linkloom.experience.retrieval import ExperienceRetriever
    from linkloom.experience.context import ExperienceContextBuilder
    authority, identity = synthetic_evaluator_bundle(tmp_path)
    from linkloom.experience.models import ExperienceReviewDecision
    store = ExperienceStore(tmp_path / "review.jsonl", authority=authority)
    candidate = store.save(RunReflection(authority=authority).reflect(authority.resolve(identity))[0])
    record = store.record_review(ExperienceReviewDecision("experience-review/v1", candidate.experience_id,
        "accepted", "reviewer:test", "independent_reviewer", "review:offline", "2026-09-16T04:00:00+00:00"))
    selection = ExperienceRetriever((record,), authority=authority, store=store).retrieve(
        ExperienceQuery("team_decision", ("current_decision",)))
    assert selection.records == (record,)
    (tmp_path / "observed.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="identity mismatch"):
        ExperienceContextBuilder(authority=authority, store=store).build(selection)
