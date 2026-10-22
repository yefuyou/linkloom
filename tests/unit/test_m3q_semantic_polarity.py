from copy import deepcopy
import pytest
from tests.task_semantic_support import selected_context, response_arguments
from tests.unit.test_task_relation_semantic import run
from linkloom.semantic_ingestion.task_relation_semantic_validation import validate_proposals


@pytest.mark.parametrize('verdict', ['SUPPORTED', 'REFUTED', 'UNKNOWN'])
def test_two_sided_evidence_cannot_be_presented_as_one_sided_verdict(verdict):
    context = selected_context('conflict')
    arguments = response_arguments(context, 'CONFLICT')
    arguments['proposals'][1].update(proposed_verdict=verdict,
        missing_evidence=['missing premise'] if verdict == 'UNKNOWN' else [],
        changed_fields=['deadline'] if verdict == 'SUPPORTED' else [])
    result, _ = run(context, arguments)
    report = validate_proposals(result, context)[1]
    assert report.stage == 'EVIDENCE_INVALID'
    assert 'VERDICT_EVIDENCE_POLARITY_MISMATCH' in report.errors


@pytest.mark.parametrize('verdict', ['SUPPORTED', 'REFUTED', 'CONFLICT', 'UNKNOWN'])
def test_valid_structured_states_remain_distinct_and_unconfirmed(verdict):
    context = selected_context('conflict')
    arguments = response_arguments(context, verdict)
    result, _ = run(context, arguments)
    reports = validate_proposals(result, context)
    assert result.proposals[1].proposed_verdict == verdict
    assert all(r.stage == 'EVIDENCE_VALID' and not r.publishable for r in reports)
