"""Approved historical compatibility: real sealed evidence, zero Provider calls."""
import json
import hashlib
import io
import os
from dataclasses import replace
from pathlib import Path

import pytest

from linkloom.experience.authority import HistoricalReviewAuthority
from linkloom.experience.context import ExperienceContextBuilder
from linkloom.experience.models import ExperienceQuery, ExperienceReviewDecision
from linkloom.experience.reflection import RunReflection
from linkloom.experience.retrieval import ExperienceRetriever
from linkloom.experience.store import ExperienceStore


PROJECT = Path(__file__).parents[2]
ROOT = PROJECT / "tests/fixtures/experience_reflection_v1/historical_review"
MANIFEST_PIN = "70b8c3a4091e2ea350093edc1d1c60fe5e517c801739ae64730f9933148ad0d0"
RUNS = ("run_p4_961cf695f6f8", "run_p4_dd10d1b9eee7", "run_p4_b63d23ad517d")


def authority():
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    if any(not (PROJECT / source["path"]).exists()
           for entry in manifest["results"] for source in entry["sources"].values()):
        pytest.skip("unchanged local sealed historical sources are unavailable")
    return HistoricalReviewAuthority(ROOT / "manifest.json", manifest_sha256=MANIFEST_PIN, artifact_root=PROJECT)


def result(auth, run_id):
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    return auth.resolve(next(entry["result_id"] for entry in manifest["results"] if entry["run_id"] == run_id))


def mutated_receipt_authority(root, edit):
    authority()  # Skip when the immutable local source bundle is unavailable.
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    for index, entry in enumerate(manifest["results"]):
        receipt = json.loads((ROOT / entry["receipt"]["path"]).read_bytes())
        if index == 0:
            edit(receipt)
        raw = json.dumps(receipt, sort_keys=True).encode()
        (root / entry["receipt"]["path"]).write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        entry["receipt"]["sha256"] = digest
        entry["result_id"] = "historicalreview_" + digest
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(raw)
    auth = HistoricalReviewAuthority(root / "manifest.json", manifest_sha256=hashlib.sha256(raw).hexdigest(), artifact_root=PROJECT)
    return auth, manifest["results"][0]["result_id"]


@pytest.mark.parametrize("run_id,count", tuple(zip(RUNS, (1, 0, 0))))
def test_original_reviewed_disposition_is_preserved(run_id, count):
    auth = authority()
    evidence = result(auth, run_id)
    records = RunReflection(authority=auth).reflect(evidence)
    assert len(records) == count
    if count:
        assert records[0].status == "candidate"
        assert records[0].source_run_ids == (run_id,)
        assert set(records[0].source_evidence_refs) == {f"{run_id}:ev_p1_0016", f"{run_id}:ev_p1_0018"}
        assert dict(evidence.dimensions) == {"infrastructure": "PASS", "grounding": "PASS", "semantic": "FAIL"}
        assert "provider_availability" not in dict(evidence.dimensions)
        assert "contract" not in dict(evidence.dimensions)


def test_historical_candidate_requires_separate_persisted_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: pytest.fail("Provider/network forbidden"))
    original_open = io.open
    accesses = []
    def guarded_open(file, *args, **kwargs):
        try:
            path = os.fspath(file).casefold()
        except TypeError:
            return original_open(file, *args, **kwargs)
        accesses.append(path)
        if any(token in path for token in ("golden8", "gold.json", "relation_pairs")):
            pytest.fail("Gold loader/file access forbidden")
        return original_open(file, *args, **kwargs)
    monkeypatch.setattr(io, "open", guarded_open)
    auth = authority()
    evidence = result(auth, RUNS[0])
    candidate = RunReflection(authority=auth).reflect(evidence)[0]
    store = ExperienceStore(tmp_path / "review.jsonl", authority=auth)
    store.save(candidate)
    query = ExperienceQuery("team_decision", ("current_decision", "compared_options"))
    assert ExperienceRetriever(store.list(), authority=auth, store=store).retrieve(query).records == ()
    accepted = store.record_review(ExperienceReviewDecision(
        "experience-review/v1", candidate.experience_id, "accepted", "reviewer:test",
        "independent_reviewer", "review:offline", "2026-09-16T12:00:00+00:00"))
    selection = ExperienceRetriever(store.list(), authority=auth, store=store).retrieve(query)
    context = ExperienceContextBuilder(authority=auth, store=store).build(selection)
    assert selection.records == (accepted,)
    assert context.experience_ids == (candidate.experience_id,)
    assert context.model_text.count("- Lesson:") == 1
    assert RUNS[0] not in context.model_text
    assert "mps-001" not in context.model_text
    assert ExperienceStore(store.log_path, authority=auth).get(candidate.experience_id) == accepted
    assert accesses
    assert set(accepted.to_dict()).isdisjoint({"case_id", "expected_answer", "gold"})
    assert all(".artifacts" not in str(value) for value in accepted.to_dict().values())


def test_historical_receipt_from_another_issuer_is_not_authority():
    auth = authority()
    issued = result(auth, RUNS[0])
    with pytest.raises(ValueError, match="authority|issuer"):
        RunReflection(authority=auth).reflect(replace(issued, _issuer=object()))


@pytest.mark.parametrize("edit", [
    lambda receipt: receipt["task_characteristics"].append("explicit_rejection_evidence_present"),
    lambda receipt: receipt.update(gold_leakage_detected=True),
    lambda receipt: receipt["task_characteristics"].append("gold_leakage_detected"),
])
def test_historical_exclusion_or_gold_signal_abstains(tmp_path, edit):
    auth, identity = mutated_receipt_authority(tmp_path, edit)
    assert RunReflection(authority=auth).reflect(auth.resolve(identity)) == ()


@pytest.mark.parametrize("edit", [
    lambda receipt: receipt["finding"].update(source_evidence_refs=[f"{RUNS[0]}:ev_p1_0002"]),
    lambda receipt: receipt["finding"].update(source_evidence_refs=[f"{RUNS[0]}:ev_unobserved"]),
    lambda receipt: receipt["finding"].update(source_evidence_refs=[f"{RUNS[1]}:ev_p1_0016"]),
    lambda receipt: receipt["source_disposition"].update(contract="PASS"),
    lambda receipt: receipt["source_disposition"].update(semantic="PASS"),
    lambda receipt: receipt.update(source_evaluator_sha256="f" * 64),
    lambda receipt: receipt.update(source_run_id="run_mps-001"),
    lambda receipt: receipt.update(source_run_id="run_safe:/private/gold.json"),
])
def test_historical_repin_cannot_invent_linkage_disposition_or_source(tmp_path, edit):
    auth, identity = mutated_receipt_authority(tmp_path, edit)
    with pytest.raises(ValueError):
        auth.resolve(identity)


def test_stale_receipt_rejects_without_touching_original_sources(tmp_path):
    auth, identity = mutated_receipt_authority(tmp_path, lambda receipt: None)
    issued = auth.resolve(identity)
    (tmp_path / "r1.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="identity|stale"):
        RunReflection(authority=auth).reflect(issued)
