"""Independent Dev Gold is read after predictions, never passed to the resolver."""
import json
from pathlib import Path

from linkloom.semantic_ingestion.task_relation_models import RelationAssessment
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from tests.task_identity_support import build_case, public_cases


def test_dev_fixed_pair_verdicts_and_source_evidence():
    predictions = {}
    inputs = {}
    for case in public_cases():
        subject, object, artifacts, registry = build_case(case)
        result = assess_same_task(subject, object, artifacts=artifacts, source_registry=registry)
        predictions[case["case_id"]] = result
        inputs[case["case_id"]] = (artifacts, registry)
    # Evaluator-only access starts here, after all system outputs exist.
    gold_file = Path(__file__).parents[1] / "fixtures/task_identity/gold/dev.json"
    gold = json.loads(gold_file.read_text(encoding="utf8"))
    assert set(predictions) == {row["case_id"] for row in gold["cases"]}
    for row in gold["cases"]:
        result = predictions[row["case_id"]]
        assert result.verdict == row["verdict"], row["case_id"]
        assert result.evidence_validity
        artifacts, registry = inputs[row["case_id"]]
        for evidence in (*result.supporting_evidence, *result.counter_evidence, *result.context_evidence):
            artifact = next(a for a in artifacts if a.artifact_version_id == evidence.span.artifact_version_id)
            assert evidence.verify(artifact)
            assert registry.evidence_hash(artifact.workspace_id, evidence.span.evidence_ref) == artifact.content_hash
        for required in row["required_evidence"]:
            # Gold also lists source context (e.g. D07's supplier statement),
            # not just the narrower observation/proof excerpts. Read the pinned
            # complete artifact; do not change Gold to fit the selected spans.
            assert any(a.artifact_id == required["source_id"] and required["quote"] in a.content
                       and any(d.span.artifact_id == a.artifact_id and d.span.content_hash == a.content_hash
                               for d in result.dependencies) for a in artifacts), (row["case_id"], required)
        assert RelationAssessment.from_dict(json.loads(json.dumps(result.to_dict(), ensure_ascii=False))) == result
    original = predictions["D07"]
    assert sorted(o.deadline for o in original.observations) == ["2026-10-14", "2026-10-18"]
    assert next(o for o in original.observations if o.vendor is not None).project is None
    assert all(o.authority == "SUPPORTING_ONLY" and o.relation_status == "UNCONFIRMED" for o in original.observations)


def test_public_input_loader_rejects_gold_leakage():
    case = dict(public_cases()[0], verdict="SUPPORTED")
    import pytest
    with pytest.raises(ValueError, match="labelled"):
        build_case(case)
