"""Synthetic evaluator-owned artifacts. Digest is pinned, not recomputed on load."""
from datetime import datetime, timezone
from pathlib import Path

from linkloom.experience.authority import EvaluationAuthority
from linkloom.experience.reflection import RunReflection

ROOT = Path(__file__).parent / "fixtures" / "experience_reflection_v1" / "authority"
MANIFEST_SHA256 = "e58cd2e2b2c7994c8e6ce97ca869c3b317eb2ca312a1974efef05a06554d2c01"


def fixture_authority():
    return EvaluationAuthority(ROOT / "manifest.json", manifest_sha256=MANIFEST_SHA256)


def fixture_result(authority, suffix="a1"):
    import json
    entries = json.loads((ROOT / "manifest.json").read_bytes())["results"]
    result_id = next(e["result_id"] for e in entries if e["run_id"] == "run_authority_" + suffix)
    return authority.resolve(result_id)


def fixture_candidate(authority, suffix="a1", created_at="2026-09-16T03:00:00+00:00"):
    return RunReflection(authority=authority, clock=lambda: datetime.fromisoformat(created_at)).reflect(
        fixture_result(authority, suffix)
    )[0]


def fixture_reviewed(store, authority, suffix="a1", decision="accepted"):
    """Only an explicit durable test review grants terminal lifecycle status."""
    from linkloom.experience.models import ExperienceReviewDecision
    candidate = fixture_candidate(authority, suffix)
    existing = store.get(candidate.experience_id)
    if existing is not None and existing.status != "candidate":
        return existing
    store.save(candidate)
    return store.record_review(ExperienceReviewDecision(
        "experience-review/v1", candidate.experience_id, decision,
        "reviewer:test", "independent_reviewer", "review:offline",
        "2026-09-16T04:00:00+00:00",
    ))


def synthetic_evaluator_bundle(root, *, states=None, edit=None):
    """Trusted test producer emits all dimensions/companions, then seals files."""
    import hashlib
    import json
    observed = json.loads((ROOT / "a1.observed.json").read_bytes())
    evaluation = json.loads((ROOT / "a1.evaluation.json").read_bytes())
    evaluation["dimensions"].update(states or {})
    evaluation["findings"] = [f for f in evaluation["findings"]
                              if evaluation["dimensions"][f["dimension"]] == f["outcome"]]
    for dimension, state in evaluation["dimensions"].items():
        if state != "PASS" and not any(f["dimension"] == dimension for f in evaluation["findings"]):
            evaluation["findings"].append({
                "finding_id": "finding." + dimension, "dimension": dimension,
                "outcome": "N/E" if state == "BLOCKED" else state,
                "code": "semantic_mismatch_cause_unresolved/v1", "source_evidence_refs": [],
                "supporting_observation_ids": [],
            })
    if edit:
        edit(observed, evaluation)
    root.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        (root / name).write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()
    observed_hash = write("observed.json", observed)
    observed_seal_hash = write("observed-seal.json", {
        "schema_version": "deepseek-real-smoke-seal/v1", "sealed": True,
        "gold_available": False, "observed_summary_sha256": observed_hash,
    })
    evaluation["observed_summary_sha256"] = observed_hash
    evaluation["observed_seal_sha256"] = observed_seal_hash
    evaluation_hash = write("evaluation.json", evaluation)
    evaluation_seal_hash = write("evaluation-seal.json", {
        "schema_version": "complete-evaluation-seal/v1", "sealed": True,
        "run_id": evaluation["run_id"], "evaluation_sha256": evaluation_hash,
    })
    result_id = "evaluation_" + evaluation_hash
    artifacts = {name: {"path": path, "sha256": digest} for name, path, digest in (
        ("observed", "observed.json", observed_hash),
        ("observed_seal", "observed-seal.json", observed_seal_hash),
        ("evaluation", "evaluation.json", evaluation_hash),
        ("evaluation_seal", "evaluation-seal.json", evaluation_seal_hash),
    )}
    manifest_hash = write("manifest.json", {"schema_version": "evaluation-authority-manifest/v1",
        "results": [{"result_id": result_id, "run_id": observed["run_id"], "artifacts": artifacts}]})
    return EvaluationAuthority(root / "manifest.json", manifest_sha256=manifest_hash), result_id
