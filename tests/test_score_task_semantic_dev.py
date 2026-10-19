"""Focused tests for the offline M3 semantic Dev scorer.

These fixtures are scripted contract probes only.  They must never be read as
production Agent or provider quality evidence.
"""

from copy import deepcopy
import json
from pathlib import Path

from scripts.score_task_semantic_dev import (
    DATE_EXPECTATIONS,
    score_case,
    score_predictions,
)


ROOT = Path(__file__).resolve().parents[1]
INPUTS = json.loads(
    (ROOT / "tests/fixtures/task_semantic/inputs/dev.json").read_text(encoding="utf-8")
)
GOLD = json.loads(
    (ROOT / "tests/fixtures/task_semantic/gold/dev.json").read_text(encoding="utf-8")
)


def _case(case_id: str):
    return next(case for case in INPUTS["cases"] if case["case_id"] == case_id)


def _gold(case_id: str):
    return next(case for case in GOLD["cases"] if case["case_id"] == case_id)


def _span(source: str, quote: str = "evidence"):
    return {
        "span": {"artifact_id": source, "quote": quote},
        "quote": quote,
    }


def _proposal(predicate: str, subject: str, object_: str, verdict: str,
              sources=(), *, proposal_id: str | None = None):
    evidence = [_span(source) for source in sources]
    return {
        "proposal_id": proposal_id or f"p-{predicate}-{subject}-{object_}",
        "predicate": predicate,
        "subject": subject,
        "object": object_,
        "proposed_verdict": verdict,
        "supporting_evidence": evidence if verdict in {"SUPPORTED", "CONFLICT"} else [],
        "counter_evidence": evidence if verdict in {"REFUTED", "CONFLICT"} else [],
        "missing_evidence": ["missing bridge"] if verdict == "UNKNOWN" else [],
        "reason_codes": ["TEST_ONLY"],
        "changed_fields": ["deadline"] if predicate == "REVISES_PLAN" and verdict == "SUPPORTED" else [],
    }


def _row(case_id: str, proposals, *, status="EXECUTED", validation=None,
         comparisons=None, provider_calls=1):
    return {
        "case_id": case_id,
        "result": {
            "schema": "task-semantic-result/v1",
            "status": status,
            "provider_calls": provider_calls,
            "proposals": proposals,
        },
        "validation": validation if validation is not None else [
            {
                "proposal_id": proposal["proposal_id"],
                "stage": "EVIDENCE_VALID",
                "errors": [],
                "supporting_evidence": proposal.get("supporting_evidence", []),
                "counter_evidence": proposal.get("counter_evidence", []),
            }
            for proposal in proposals
        ],
        "comparisons": comparisons or [],
    }


def test_real_provider_scores_labels_separately_from_quote_and_source_coverage():
    case = _case("causality")
    gold = _gold("causality")
    proposals = [
        _proposal("AFFECTED_BY", "adapt", "change", "SUPPORTED", ["choice.md", "work.md"]),
        _proposal("AFFECTED_BY", "archive", "change", "REFUTED", ["work.md"]),
        _proposal("AFFECTED_BY", "cache", "change", "UNKNOWN"),
    ]
    result = score_case(case, gold, _row("causality", proposals), execution_kind="REAL_PROVIDER")

    assert result["status"] == "SCORED"
    assert result["business_metrics"]["relation_state_accuracy"]["value"] == 1
    assert result["business_metrics"]["relation_positive_precision"]["AFFECTED_BY"]["value"] == 1
    assert result["business_metrics"]["relation_positive_recall"]["AFFECTED_BY"]["value"] == 1
    assert result["business_metrics"]["gold_source_coverage"]["value"] == 1 / 3
    assert result["business_metrics"]["evidence_groundedness"]["value"] == 1 / 3
    assert result["business_metrics"]["quote_authenticity"]["value"] == 1


def test_missing_proposal_is_a_false_negative_for_an_executed_case():
    case = _case("revision")
    gold = _gold("revision")
    proposal = _proposal("SAME_TASK", "new", "old", "SUPPORTED", ["plan.md", "update.md"])
    result = score_case(
        case,
        gold,
        _row("revision", [proposal]),
        execution_kind="REAL_PROVIDER",
    )

    metrics = result["business_metrics"]
    assert metrics["relation_state_accuracy"]["numerator"] == 1
    assert metrics["relation_state_accuracy"]["denominator"] == 2
    assert metrics["relation_positive_recall"]["REVISES_PLAN"]["numerator"] == 0
    assert metrics["relation_positive_recall"]["REVISES_PLAN"]["denominator"] == 1
    assert metrics["counts"]["missing_gold_relations"] == 1


def test_not_run_and_fake_outputs_never_receive_business_scores():
    case = _case("revision")
    gold = _gold("revision")
    not_run = score_case(
        case,
        gold,
        _row("revision", [], status="NOT_RUN", provider_calls=0),
        execution_kind="REAL_PROVIDER",
    )
    fake = score_case(
        case,
        gold,
        _row("revision", [_proposal("SAME_TASK", "new", "old", "SUPPORTED", ["plan.md"])]),
        execution_kind="SCRIPTED_CONTRACT_ONLY",
    )

    assert not_run["status"] == "NOT_EVALUATED"
    assert not_run["business_metrics"] is None
    assert fake["status"] == "SCRIPTED_CONTRACT_ONLY"
    assert fake["business_metrics"] is None
    assert fake["diagnostic_metrics"] is not None


def test_date_expectations_are_explicit_and_require_conditional_math_contract():
    assert DATE_EXPECTATIONS["revision"] == {"status": "COMPUTABLE", "delta_calendar_days": 4}
    assert DATE_EXPECTATIONS["conflict"] == {"status": "BLOCKED", "delta_calendar_days": None}
    assert DATE_EXPECTATIONS["missing-year"] == {
        "status": "NOT_COMPARABLE",
        "delta_calendar_days": None,
    }

    case = _case("revision")
    gold = _gold("revision")
    revision_id = "p-REVISES_PLAN-new-old"
    proposals = [
        _proposal("SAME_TASK", "new", "old", "SUPPORTED", ["plan.md", "update.md"]),
        _proposal("REVISES_PLAN", "new", "old", "SUPPORTED", ["plan.md", "update.md"], proposal_id=revision_id),
    ]
    comparison = {
        "proposal_id": revision_id,
        "status": "COMPUTABLE",
        "delta_calendar_days": 4,
        "conditional_on_model_semantics": True,
        "semantic_status": "NOT_EVALUATED",
        "publishable": False,
    }
    result = score_case(
        case,
        gold,
        _row("revision", proposals, comparisons=[comparison]),
        execution_kind="REAL_PROVIDER",
    )
    assert result["business_metrics"]["conditional_date_correctness"]["value"] == 1

    invalid = deepcopy(comparison)
    invalid["semantic_status"] = "SEMANTIC_ACCEPTED"
    invalid["publishable"] = True
    result = score_case(
        case,
        gold,
        _row("revision", proposals, comparisons=[invalid]),
        execution_kind="REAL_PROVIDER",
    )
    assert result["business_metrics"]["conditional_date_correctness"]["value"] == 0


def test_score_predictions_marks_partial_real_run_without_calling_it_full_dev_score():
    rows = [
        _row(
            "revision",
            [
                _proposal("SAME_TASK", "new", "old", "SUPPORTED", ["plan.md", "update.md"]),
                _proposal("REVISES_PLAN", "new", "old", "SUPPORTED", ["plan.md", "update.md"]),
            ],
        ),
        _row("causality", [], status="NOT_RUN", provider_calls=0),
    ]
    report = score_predictions(
        INPUTS,
        GOLD,
        {
            "schema": "task-semantic-predictions/v1",
            "execution_kind": "REAL_PROVIDER",
            "provider_calls": 1,
            "provider_receipts": [{"status": "RETURNED"}],
            "cases": rows,
        },
    )

    assert report["status"] == "PARTIAL_EXECUTION"
    assert report["coverage"]["executed_real_cases"] == 1
    assert report["coverage"]["not_run_cases"] == 7
    assert report["business_metrics"] is not None
    assert report["business_metrics"]["scope"] == "EXECUTED_REAL_PROVIDER_CASES_ONLY"


def test_real_provider_requires_a_call_and_invalid_output_cannot_smuggle_proposals():
    case = _case("revision")
    gold = _gold("revision")
    proposal = _proposal("SAME_TASK", "new", "old", "SUPPORTED", ["plan.md", "update.md"])
    no_call = score_case(
        case,
        gold,
        _row("revision", [proposal], provider_calls=0),
        execution_kind="REAL_PROVIDER",
    )
    malformed = score_case(
        case,
        gold,
        _row("revision", [proposal], status="OUTPUT_INVALID", provider_calls=1),
        execution_kind="REAL_PROVIDER",
    )

    assert no_call["status"] == "NOT_EVALUATED"
    assert no_call["business_metrics"] is None
    assert malformed["status"] == "SCORED"
    assert malformed["invalid_output"] is True
    assert malformed["business_metrics"]["counts"]["predicted_proposals"] == 0
    assert malformed["business_metrics"]["relation_state_accuracy"]["numerator"] == 0


def test_attempted_only_receipts_do_not_attest_real_executed_cases():
    rows = [_row("revision", [_proposal("SAME_TASK", "new", "old", "SUPPORTED", ["plan.md"])])]
    report = score_predictions(
        INPUTS,
        GOLD,
        {
            "schema": "task-semantic-predictions/v1",
            "execution_kind": "REAL_PROVIDER",
            "provider_calls": 1,
            "provider_receipts": [{"status": "ATTEMPTED"}],
            "cases": rows,
        },
    )

    assert report["status"] == "NOT_EVALUATED"
    assert report["coverage"]["execution_receipts_attested"] is False
    assert report["business_metrics"] is None


def _attested_prediction(rows):
    calls=sum(row['result']['provider_calls'] for row in rows)
    return dict(schema='task-semantic-predictions/v1',execution_kind='REAL_PROVIDER',
                provider_calls=calls,provider_receipts=[dict(status='RETURNED') for _ in range(calls)],cases=rows)


def test_aggregate_keeps_each_expected_state():
    proposals=[_proposal('AFFECTED_BY','adapt','change','SUPPORTED',['choice.md','work.md']),
               _proposal('AFFECTED_BY','archive','change','REFUTED',['choice.md','work.md']),
               _proposal('AFFECTED_BY','cache','change','UNKNOWN')]
    report=score_predictions(INPUTS,GOLD,_attested_prediction([_row('causality',proposals)]))
    states=report['business_metrics']['relation_state_accuracy']['by_expected_state']
    assert states['SUPPORTED']['numerator']==states['REFUTED']['numerator']==states['UNKNOWN']['numerator']==1
    assert states['CONFLICT']['denominator']==0 and states['CONFLICT']['value'] is None


def test_duplicate_or_unexpected_cases_are_never_silently_scored():
    row=_row('revision',[])
    for rows,reason in (([row,deepcopy(row)],'DUPLICATE_CASE_IDS'),
                        ([row,_row('unknown-case',[])],'UNEXPECTED_CASE_IDS')):
        report=score_predictions(INPUTS,GOLD,_attested_prediction(rows))
        assert report['status']=='NOT_EVALUATED' and report['business_metrics'] is None
        assert report['reason']==reason


def test_invalid_receipts_or_call_count_mismatch_do_not_score():
    prediction=_attested_prediction([_row('revision',[])])
    for receipts in (['malformed'],[],[dict(status='RETURNED'),dict(status='RETURNED')]):
        altered=deepcopy(prediction)
        altered['provider_receipts']=receipts
        assert score_predictions(INPUTS,GOLD,altered)['business_metrics'] is None
    altered=deepcopy(prediction)
    altered['cases'][0]['result']['provider_calls']=0
    assert score_predictions(INPUTS,GOLD,altered)['business_metrics'] is None


def test_revoked_evidence_can_be_exact_without_receiving_groundedness_credit():
    proposal=_proposal('SAME_TASK','new','old','SUPPORTED',['plan.md','update.md'])
    row=_row('revision',[proposal])
    row['validation'][0].update(stage='EVIDENCE_INVALID',errors=['OBSERVATION_REVOKED'])
    score=score_case(_case('revision'),_gold('revision'),row,execution_kind='REAL_PROVIDER')
    assert score['business_metrics']['quote_authenticity']['value']==1
    assert score['business_metrics']['evidence_groundedness']['numerator']==0
