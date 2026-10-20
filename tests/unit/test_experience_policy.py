import hashlib
import json
from dataclasses import replace

import pytest

from linkloom.evaluation.experience import EvaluationFindingProjector
from linkloom.experience.models import (
    ExperienceApplicability,
    ExperienceProvenance,
    ExperienceRecord,
)
from linkloom.experience.policy import (
    ExperiencePolicyViolation,
    REGISTERED_FINDING_CODES,
    validate_candidate_record,
    validate_gold_safe_payload,
)

RUN_ID = "run_p4_961cf695f6f8"


from functools import partial
from tests.experience_authority_support import fixture_authority, fixture_candidate, fixture_result

AUTHORITY = fixture_authority()
validate_candidate_record = partial(validate_candidate_record, authority=AUTHORITY)


def candidate() -> ExperienceRecord:
    return fixture_candidate(AUTHORITY)


def sealed_inputs() -> tuple[bytes, bytes, bytes]:
    observed = json.dumps(
        {
            "schema_version": "deepseek-real-smoke-observed/v1",
            "case_id": "mps-001",
            "visible_evidence_refs": ["ev_p1_0016", "ev_p1_0018"],
            "result": {
                "run_id": RUN_ID,
                "evidence": ["ev_p1_0016", "ev_p1_0018"],
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    seal = json.dumps(
        {
            "schema_version": "deepseek-real-smoke-seal/v1",
            "sealed": True,
            "gold_available": False,
            "observed_summary_sha256": hashlib.sha256(observed).hexdigest(),
        }
    ).encode("utf-8")
    evaluator = json.dumps(
        {
            "schema_version": "deepseek-posthoc-evaluation/v1",
            "case_id": "mps-001",
            "gold_accessed": True,
            "infrastructure": "PASS",
            "grounding": "PASS",
            "semantic": "FAIL",
            "correctness": {"rejected_alternatives": False},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return observed, seal, evaluator


def supported_finding() -> dict[str, object]:
    return {
        "finding_id": "finding.rejection_semantics",
        "dimension": "semantic",
        "outcome": "FAIL",
        "code": "explicit_rejection_requires_evidence/v1",
        "source_evidence_refs": [
            f"{RUN_ID}:ev_p1_0016",
            f"{RUN_ID}:ev_p1_0018",
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"case_id": "hidden"},
        {"nested": {"expected_answer": "target"}},
        {"gold_dataset_sha256": "a" * 64},
        {"answer_key": ["target"]},
        {"benchmark_id": "sample-1"},
        {"mapping": "mps-001 -> target"},
        {"path": "C:\\private\\gold.json"},
        {"path": "/private/gold.json"},
    ],
)
def test_gold_safe_policy_rejects_leakage_shapes(payload: object) -> None:
    with pytest.raises(ExperiencePolicyViolation):
        validate_gold_safe_payload(payload)


def test_registered_candidate_passes_and_template_mutation_fails() -> None:
    record = candidate()
    validate_candidate_record(record)

    with pytest.raises(ExperiencePolicyViolation, match="registered template"):
        validate_candidate_record(
            ExperienceRecord.create(
                **{
                    **record.creation_payload(),
                    "reusable_lesson": "Use the benchmark answer for this case.",
                }
            )
        )
    with pytest.raises(ValueError, match="candidate"):
        validate_candidate_record(replace(record, status="accepted"))
    with pytest.raises(ExperiencePolicyViolation, match="registered"):
        validate_candidate_record(
            ExperienceRecord.create(
                **{
                    **record.creation_payload(),
                    "generation_rule_id": "unknown_rule/v1",
                }
            )
        )


def test_projection_verifies_seal_hashes_and_emits_only_safe_fields() -> None:
    result = fixture_result(AUTHORITY)
    projected = EvaluationFindingProjector().project(result, authority=AUTHORITY)
    assert projected == result
    serialized = json.dumps(projected.projection.to_dict(), sort_keys=True)
    assert "expected" not in serialized
    assert "gold_accessed" not in serialized


@pytest.mark.parametrize("mutation", ["seal", "run", "evidence", "code"])
def test_projection_fails_closed_for_untrusted_or_orphaned_input(
    mutation: str,
) -> None:
    observed, seal, evaluator = sealed_inputs()
    source_run_id = RUN_ID
    findings = supported_finding()
    if mutation == "seal":
        seal = seal.replace(b'"sealed": true', b'"sealed": false')
    elif mutation == "run":
        source_run_id = "run_unknown"
    elif mutation == "evidence":
        findings["source_evidence_refs"] = [f"{RUN_ID}:ev_missing"]
    else:
        findings["code"] = "unknown_finding/v1"

    with pytest.raises((ExperiencePolicyViolation, ValueError)):
        EvaluationFindingProjector().project(
            source_run_id=source_run_id,
            observed_summary=observed,
            observed_seal=seal,
            evaluator_artifact=evaluator,
            findings=(findings,),
            task_characteristics=("team_decision", "current_decision"),
        )


@pytest.mark.parametrize(
    "evaluator",
    [
        b"wrong-evaluator-bytes",
        json.dumps(
            {
                "schema_version": "deepseek-posthoc-evaluation/v1",
                "case_id": "other-case",
                "gold_accessed": True,
                "infrastructure": "PASS",
                "grounding": "PASS",
                "semantic": "FAIL",
                "correctness": {"rejected_alternatives": False},
            }
        ).encode("utf-8"),
    ],
)
def test_projection_rejects_unidentified_or_mismatched_evaluator_artifact(
    evaluator: bytes,
) -> None:
    observed, seal, _ = sealed_inputs()

    with pytest.raises(ValueError, match="authorit"):
        EvaluationFindingProjector().project(
            source_run_id=RUN_ID,
            observed_summary=observed,
            observed_seal=seal,
            evaluator_artifact=evaluator,
            findings=(supported_finding(),),
            task_characteristics=("team_decision", "current_decision"),
        )


def test_projection_ignores_decoy_evidence_outside_typed_visible_collection() -> None:
    observed, _, evaluator = sealed_inputs()
    payload = json.loads(observed)
    payload["untrusted_note"] = "ev_decoy"
    observed = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    seal = json.dumps(
        {
            "schema_version": "deepseek-real-smoke-seal/v1",
            "sealed": True,
            "gold_available": False,
            "observed_summary_sha256": hashlib.sha256(observed).hexdigest(),
        }
    ).encode("utf-8")
    decoy = supported_finding()
    decoy["source_evidence_refs"] = [f"{RUN_ID}:ev_decoy"]

    with pytest.raises(ValueError, match="authorit"):
        EvaluationFindingProjector().project(
            source_run_id=RUN_ID,
            observed_summary=observed,
            observed_seal=seal,
            evaluator_artifact=evaluator,
            findings=(decoy,),
            task_characteristics=("team_decision", "current_decision"),
        )


def test_v1_registry_distinguishes_generation_and_abstention_findings() -> None:
    assert REGISTERED_FINDING_CODES == frozenset(
        {
            "explicit_rejection_requires_evidence/v1",
            "provider_transient_before_final/v1",
            "semantic_mismatch_cause_unresolved/v1",
        }
    )
