"""Reviewer six-blocker regressions; all artifacts are synthetic/offline."""
from dataclasses import replace
import hashlib
import json

import pytest

from linkloom.experience.context import ExperienceContextBuilder, render_records
from linkloom.experience.models import ExperienceRecord, ExperienceSelection, ExperienceReviewDecision
from linkloom.experience.authority import EvaluationAuthority
from linkloom.experience.retrieval import ExperienceRetriever
from linkloom.experience.store import ExperienceStore
from linkloom.experience.reflection import RunReflection
from tests.experience_authority_support import fixture_authority, fixture_candidate, fixture_reviewed, synthetic_evaluator_bundle


def six_reviewed_records(root):
    """Six genuine, independently sealed lessons and durable test reviews."""
    entries = []
    for index in range(6):
        def edit(observed, evaluation):
            original = observed["run_id"]
            run_id = f"run_opaque_{index}"
            observed["run_id"] = observed["result"]["run_id"] = evaluation["run_id"] = run_id
            for observation in observed["supporting_observations"]:
                observation["source_evidence_refs"] = [r.replace(original, run_id) for r in observation["source_evidence_refs"]]
            for finding in evaluation["findings"]:
                finding["source_evidence_refs"] = [r.replace(original, run_id) for r in finding["source_evidence_refs"]]
        synthetic_evaluator_bundle(root / str(index), edit=edit)
        entry = json.loads((root / str(index) / "manifest.json").read_bytes())["results"][0]
        for artifact in entry["artifacts"].values():
            artifact["path"] = str(index) + "/" + artifact["path"]
        entries.append(entry)
    raw = json.dumps({"schema_version": "evaluation-authority-manifest/v1", "results": entries}, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(raw)
    authority = EvaluationAuthority(root / "manifest.json", manifest_sha256=hashlib.sha256(raw).hexdigest())
    store = ExperienceStore(root / "reviews.jsonl", authority=authority)
    accepted = []
    for entry in entries:
        candidate = RunReflection(authority=authority).reflect(authority.resolve(entry["result_id"]))[0]
        store.save(candidate)
        accepted.append(store.record_review(ExperienceReviewDecision(
            "experience-review/v1", candidate.experience_id, "accepted", "reviewer:test",
            "independent_reviewer", "review:offline", "2026-09-16T04:00:00+00:00")))
    return authority, store, tuple(sorted(accepted, key=lambda record: record.experience_id))


@pytest.mark.parametrize("edit", [
    lambda o, e: o["result"].update(status="running"),
    lambda o, e: e["task_characteristics"].append("explicit_rejection_evidence_present"),
    lambda o, e: e["task_characteristics"].append("gold_leakage_detected"),
    lambda o, e: o.update(provider_error="unknown_provider_outcome"),
    lambda o, e: o.update(supporting_observations=[]),
    lambda o, e: e.update(task_characteristics=["team_decision"]),
    lambda o, e: e.update(gold_leakage_detected=True),
    lambda o, e: o.update(runtime_state={"run_id": o["run_id"], "status": "running"}),
    lambda o, e: o["result"].update(error={"code": "MODEL_TRANSIENT_FAILURE", "details": {"outcome": "unknown_provider_outcome"}}),
])
def test_unreliable_or_excluded_basis_never_creates_candidate(tmp_path, edit):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=edit)
    assert RunReflection(authority=authority).reflect(authority.resolve(identity)) == ()


@pytest.mark.parametrize("state", [
    {"grounding": "N/E"}, {"provider_availability": "PARTIAL"},
    {"infrastructure": "FAIL"}, {"semantic": "REVIEW_REQUIRED"},
])
def test_complete_but_unusable_evaluator_disposition_abstains(tmp_path, state):
    authority, identity = synthetic_evaluator_bundle(tmp_path, states=state)
    assert RunReflection(authority=authority).reflect(authority.resolve(identity)) == ()


def test_catalog_membership_without_observation_link_rejects(tmp_path):
    def edit(observed, evaluation):
        observed["visible_evidence_refs"].append("ev_orphan")
        evaluation["findings"][0]["source_evidence_refs"].append("run_authority_a1:ev_orphan")
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=edit)
    with pytest.raises(ValueError, match="observation|link"):
        RunReflection(authority=authority).reflect(authority.resolve(identity))


def test_unobserved_supporting_observation_rejects(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=lambda o, e:
        o["supporting_observations"][0].update(model_observed=False))
    with pytest.raises(ValueError, match="model-observed|link"):
        authority.resolve(identity)


def test_evaluator_link_to_unrelated_observation_rejects(tmp_path):
    authority, identity = synthetic_evaluator_bundle(tmp_path, edit=lambda o, e:
        e["findings"][0].update(supporting_observation_ids=["observation_unrelated"]))
    with pytest.raises(ValueError, match="observation|link"):
        authority.resolve(identity)


def test_direct_accepted_construction_is_not_a_review_transition():
    candidate = fixture_candidate(fixture_authority())
    with pytest.raises(ValueError, match="review|candidate"):
        ExperienceRecord.create(**(candidate.creation_payload() | {"status": "accepted"}))
    with pytest.raises(ValueError, match="review"):
        replace(candidate, status="accepted")


def test_six_record_selection_contract_rejects_hard_top_k_bypass(tmp_path):
    _, _, records = six_reviewed_records(tmp_path)
    with pytest.raises(ValueError, match="top.k|bound"):
        ExperienceSelection(records, len(render_records(records)))


def test_final_builder_bounds_constructor_bypass_and_preserves_rank_order(tmp_path):
    authority, store, ranked = six_reviewed_records(tmp_path)
    bypass = object.__new__(ExperienceSelection)
    object.__setattr__(bypass, "records", ranked)
    object.__setattr__(bypass, "total_rendered_chars", len(render_records(bypass.records)))
    builder = ExperienceContextBuilder(authority=authority, store=store, hard_top_k=5)
    context = builder.build(bypass)
    assert context.experience_ids == tuple(record.experience_id for record in ranked[:5])
    assert context.model_text.count("- Lesson:") == 5
    assert builder.build(bypass) == context
    configured = ExperienceContextBuilder(authority=authority, store=store, hard_top_k=3).build(bypass)
    assert configured.experience_ids == tuple(record.experience_id for record in ranked[:3])


def test_final_serialization_rejects_a_sixth_untracked_lesson(tmp_path):
    authority, store, ranked = six_reviewed_records(tmp_path)
    selection = ExperienceSelection(ranked[:5], len(render_records(ranked[:5])))
    context = ExperienceContextBuilder(authority=authority, store=store).build(selection)
    object.__setattr__(context, "model_text", context.model_text + "\n- Lesson: bypass")
    with pytest.raises(ValueError, match="top.k|bound|lesson"):
        context.to_dict()
