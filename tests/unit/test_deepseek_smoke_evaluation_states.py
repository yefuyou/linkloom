"""Offline checks for the DeepSeek smoke evaluator's tri-state outcomes."""

from __future__ import annotations

import json
from pathlib import Path

from tests.smoke.test_deepseek_real_provider_smoke import (
    _classify_smoke_verdict,
    _decision_value_semantically_matches,
    _integration_validation_statuses,
    _memory_and_retrieval_validation,
    _seal_observed,
    _smoke_axis_statuses,
    _team_decision_contract_status,
    evaluate_sealed_case,
)


def _valid_team_decision(evidence_ref: str = "evidence-1") -> dict:
    return {
        "schema_version": "team-decision-result/v1",
        "decision": {
            "value": "Aster A",
            "status": "approved",
            "evidence_refs": [evidence_ref],
        },
        "rationale": [],
        "rejected_alternatives": [],
        "actions": [],
        "unresolved_items": [],
        "uncertainty": {
            "status": "none",
            "statement": None,
            "unknown_fields": [],
            "evidence_refs": [],
        },
        "evidence_refs": [evidence_ref],
    }


def _write_sealed_observed(case_root: Path, observed: dict) -> None:
    _seal_observed(case_root, observed)


def test_provider_dies_before_final_is_not_evaluated(tmp_path: Path):
    observed = {
        "trajectory": [
            {"action": {"kind": "tool", "tool_id": "search_notes"}}
        ],
        "team_decision_result": None,
        "runtime": {"error": {"code": "MODEL_TRANSIENT_FAILURE"}},
        "integration_validation": {},
    }
    _write_sealed_observed(tmp_path, observed)

    evaluation = evaluate_sealed_case(tmp_path)

    assert evaluation["team_decision_contract"] == "NOT_EVALUATED"
    assert evaluation["semantic"] == "NOT_EVALUATED"
    assert evaluation["grounding"] == "NOT_EVALUATED"
    assert evaluation["gold_evaluation"] == "NOT_EVALUATED"
    assert evaluation["evidence_identity"] == "NOT_EVALUATED"
    assert evaluation["evidence_identity_validation"] == "NOT_EVALUATED"
    assert evaluation["memory"] == "NOT_EVALUATED"
    assert evaluation["memory_validation"] == "NOT_EVALUATED"
    assert evaluation["context_assembler"] == "NOT_EVALUATED"


def test_malformed_final_is_contract_failure_without_downstream_failures(
    tmp_path: Path,
):
    observed = {
        "trajectory": [{"action": {"kind": "final"}}],
        "team_decision_result": {"schema_version": "team-decision-result/v1"},
        "runtime": {"error": None},
        "integration_validation": {},
    }
    _write_sealed_observed(tmp_path, observed)

    evaluation = evaluate_sealed_case(tmp_path)

    assert evaluation["team_decision_contract"] == "FAIL"
    assert evaluation["semantic"] == "NOT_EVALUATED"
    assert evaluation["grounding"] == "NOT_EVALUATED"
    assert evaluation["gold_evaluation"] == "NOT_EVALUATED"
    assert evaluation["evidence_identity"] == "NOT_EVALUATED"
    assert evaluation["evidence_identity_validation"] == "NOT_EVALUATED"
    assert evaluation["memory"] == "NOT_EVALUATED"
    assert evaluation["memory_validation"] == "NOT_EVALUATED"
    assert evaluation["context_assembler"] == "NOT_EVALUATED"


def test_valid_final_is_contract_pass(tmp_path: Path):
    observed = {
        "trajectory": [
            {
                "action": {
                    "kind": "final",
                    "final_answer": json.dumps(_valid_team_decision()),
                }
            }
        ],
        "team_decision_result": _valid_team_decision(),
    }

    assert _team_decision_contract_status(observed) == "PASS"


def test_valid_final_evaluator_records_contract_pass(tmp_path: Path):
    observed = {
        "trajectory": [
            {
                "action": {
                    "kind": "final",
                    "final_answer": json.dumps(_valid_team_decision()),
                }
            }
        ],
        "team_decision_result": _valid_team_decision(),
        "visible_evidence_refs": ["evidence-1"],
    }
    _write_sealed_observed(tmp_path, observed)

    evaluation = evaluate_sealed_case(tmp_path)

    assert evaluation["team_decision_contract"] == "PASS"


def test_semantic_evaluator_accepts_contract_compatible_natural_language_value(
    tmp_path: Path,
):
    value = "Aster A was selected as the approved model provider for the Atlas Lantern pilot."
    result = _valid_team_decision()
    result["decision"]["value"] = value
    result["rationale"] = []
    result["uncertainty"]["evidence_refs"] = []
    observed = {
        "trajectory": [
            {
                "action": {
                    "kind": "final",
                    "final_answer": json.dumps(result),
                }
            }
        ],
        "team_decision_result": result,
        "visible_evidence_refs": ["evidence-1"],
        "runtime": {"status": "completed", "error": None},
        "integration_validation": {
            "retrieval": {
                "validation_status": "PASS",
                "evidence_identity_status": "PASS",
            },
            "decision_memory": {"validation_status": "NOT_EVALUATED"},
            "context_assembler": {"validation_status": "PASS"},
        },
    }
    _write_sealed_observed(tmp_path, observed)

    evaluation = evaluate_sealed_case(tmp_path)

    assert evaluation["semantic"] == "PASS"
    assert evaluation["gold_evaluation"] == "PASS"
    assert evaluation["correctness"]["decision_value_comparison"] == (
        "semantic_equivalence_v1"
    )


def test_valid_raw_final_is_contract_pass_even_without_materialized_result(
    tmp_path: Path,
):
    observed = {
        "trajectory": [
            {
                "action": {
                    "kind": "final",
                    "final_answer": json.dumps(_valid_team_decision()),
                }
            }
        ],
        "team_decision_result": None,
        "visible_evidence_refs": [],
    }
    _write_sealed_observed(tmp_path, observed)

    evaluation = evaluate_sealed_case(tmp_path)

    assert evaluation["team_decision_contract"] == "PASS"
    assert evaluation["grounding"] == "FAIL"


def test_empty_integration_stages_are_not_evaluated_and_not_vacuous_pass():
    statuses = _integration_validation_statuses(
        {"decision_memory": {}, "retrieval": {}, "context_assembler": {}}
    )
    assert statuses == {
        "memory": "NOT_EVALUATED",
        "retrieval": "NOT_EVALUATED",
        "evidence_identity": "NOT_EVALUATED",
        "context_assembler": "NOT_EVALUATED",
    }


def test_empty_retrieval_memory_and_assembler_have_no_vacuous_booleans(tmp_path):
    from types import SimpleNamespace

    adapter = SimpleNamespace(
        _latest_retrieved_evidence=[],
        _latest_decision_records=[],
        _retrieval_backend=SimpleNamespace(mode=SimpleNamespace(value="hybrid")),
        workspace_id="workspace-a",
        reader=SimpleNamespace(vault_root_fingerprint="workspace-a"),
    )
    binding = SimpleNamespace(adapter=adapter)

    result = _memory_and_retrieval_validation(
        memory_binding=binding,
        tool_calls=[],
        trace_summaries=[],
        database_path=tmp_path / "missing.sqlite",
    )

    assert result["decision_memory"]["validation_status"] == "NOT_EVALUATED"
    assert result["retrieval"]["validation_status"] == "NOT_EVALUATED"
    assert result["retrieval"]["evidence_identity_status"] == "NOT_EVALUATED"
    assert result["retrieval"]["evidence_ids_unique"] is None
    assert result["retrieval"]["evidence_workspace_matches"] is None
    assert result["retrieval"]["retrieve_read_source_identity_preserved"] is None
    assert result["context_assembler"]["validation_status"] == "NOT_EVALUATED"
    assert result["context_assembler"]["no_cross_workspace_exposure"] is None


def test_malformed_final_classifies_as_contract_failure_before_runtime_failure():
    state = type("State", (), {"model_executions": [], "status": "failed"})()

    verdict = _classify_smoke_verdict(
        state=state,
        evaluation={
            "team_decision_contract": "FAIL",
            "grounding": "NOT_EVALUATED",
        },
        integration={},
    )

    assert verdict == "CONTRACT_OR_GROUNDING_FAIL"


def test_semantic_value_accepts_equivalent_decision_statement():
    assert _decision_value_semantically_matches(
        "Aster A was selected as the approved model provider for the Atlas Lantern pilot.",
        "Aster A",
    )


def test_semantic_value_rejects_a_different_selected_provider():
    assert not _decision_value_semantically_matches(
        "Borealis B was selected as the approved model provider for the pilot.",
        "Aster A",
    )


def test_semantic_value_rejects_ambiguous_or_negated_mentions():
    assert not _decision_value_semantically_matches(
        "Aster A or Borealis B may be approved.",
        "Aster A",
    )
    assert not _decision_value_semantically_matches(
        "Aster A was considered, but Borealis B was approved.",
        "Aster A",
    )
    assert not _decision_value_semantically_matches(
        "Aster A was selected, but the approval remained unresolved.",
        "Aster A",
    )


def test_smoke_axes_keep_memory_coverage_gap_distinct_from_failure():
    state = type(
        "State",
        (),
        {
            "model_executions": [
                type("Execution", (), {"provider_error": None, "status": "response_received"})()
            ],
            "status": "completed",
        },
    )()
    integration = {
        "decision_memory": {
            "validation_status": "NOT_EVALUATED",
            "tool_invocation_count": 0,
        },
        "retrieval": {
            "validation_status": "PASS",
            "evidence_identity_status": "PASS",
            "mode": "hybrid",
            "hybrid_executed": True,
            "evidence_ids_unique": True,
            "evidence_workspace_matches": True,
            "retrieve_read_source_identity_preserved": True,
        },
        "context_assembler": {
            "validation_status": "PASS",
            "no_cross_workspace_exposure": True,
            "selection_events": [{"selected_count": 1}],
        },
    }
    evaluation = {
        "team_decision_contract": "PASS",
        "grounding": "PASS",
        "semantic": "PASS",
        "evidence_identity": "PASS",
        "context_assembler": "PASS",
        "memory": "NOT_EVALUATED",
    }

    axes = _smoke_axis_statuses(state=state, evaluation=evaluation, integration=integration)

    assert axes["decision_memory"] == "NOT_EVALUATED"
    assert axes["security"] == "PASS"
    assert axes["semantic"] == "PASS"
    assert (
        _classify_smoke_verdict(
            state=state,
            evaluation=evaluation,
            integration=integration,
        )
        == "INTEGRATION_FAIL"
    )
