from copy import deepcopy
import json

import pytest

from evals.m3_task_impact.fixtures import load_inputs
from evals.m3_task_impact.runner import execute, scripted_d01, run_dev
from evals.m3_task_impact.scorer import load_dev, score_run


def test_default_does_not_load_gold_or_claim_execution():
    record = execute(load_inputs()[0],None)
    assert record['status']=='NOT_RUN'
    assert record['execution']['target_invocations']==0
    assert record['execution']['tool_receipts']==[]
    assert record['execution']['cost_usd'] is None
    case,gold = load_dev()[0][0],load_dev()[1][0]
    result = score_run(case,gold,record)
    assert result['status']=='NOT_EVALUATED'
    assert result['metrics'] is None


def test_scripted_target_really_reads_sources_and_scores_separately():
    case = load_inputs()[0]
    record = execute(case,scripted_d01,adapter_kind='SCRIPTED')
    assert record['status']=='EXECUTED'
    assert record['execution']['target_invocations']==1
    assert [e['tool'] for e in record['execution']['tool_receipts']]==['list_sources','read_source']
    assert record['execution']['model_calls']==0
    gold = load_dev()[1][0]
    assert score_run(case,gold,record)['metrics']['task_accuracy']['value']==1


def test_agent_request_and_tools_only_expose_public_material():
    seen = []
    def adapter(request,tools):
        assert set(request)=={'case_id','workspace_id','snapshot','question'}
        assert 'gold' not in json.dumps(request).lower()
        sources = tools.list_sources()
        seen.append(tools.read_source(sources[0]['doc_id']))
        with pytest.raises(KeyError):
            tools.read_source('../../gold/dev.json')
        return dict(tasks=[],relations=[],revisions=[],answer_status='ABSTAIN',
                    abstentions=['UNTESTED'],answer_evidence=[])
    execute(load_inputs()[0],adapter,adapter_kind='TEST')
    assert 'content' in seen[0] and 'evidence' in seen[0]


def test_correct_answer_without_source_execution_does_not_count_as_run():
    case = load_inputs()[0]
    valid = execute(case,scripted_d01,adapter_kind='SCRIPTED')
    def no_action(request,tools):
        return valid['answer']
    record = execute(case,no_action,adapter_kind='TEST')
    result = score_run(case,load_dev()[1][0],record)
    assert result['status']=='NOT_EVALUATED'
    assert result['failure_stage']=='execution_contract'


def test_input_or_execution_receipt_tampering_is_not_evaluated():
    case = load_inputs()[0]
    record = execute(case,scripted_d01,adapter_kind='SCRIPTED')
    record['input_sha256'] = 'fake'
    assert score_run(case,load_dev()[1][0],record)['status']=='NOT_EVALUATED'
    record = execute(case,scripted_d01,adapter_kind='SCRIPTED')
    record['execution']['tool_receipts'][1]['result_sha256']='fake'
    assert score_run(case,load_dev()[1][0],record)['status']=='NOT_EVALUATED'


def test_executor_error_preserves_partial_trace_and_separate_failure_stage():
    def broken(request,tools):
        tools.list_sources()
        tools.read_source('a')
        raise RuntimeError('scripted test exception')
    case = load_inputs()[0]
    record = execute(case,broken,adapter_kind='TEST')
    assert record['status']=='RUN_ERROR'
    assert len(record['execution']['tool_receipts'])==2
    result = score_run(case,load_dev()[1][0],record)
    assert result['status']=='RUN_ERROR'
    assert result['metrics'] is None
    assert result['failure_stage']=='agent_execution'


def test_no_holdout_execution_and_unknown_ids_are_rejected():
    with pytest.raises(ValueError):
        run_dev(['H01'],'SCRIPTED')
    with pytest.raises(ValueError):
        run_dev(['typo'],'UNAVAILABLE')


def test_malformed_execution_is_observable_instead_of_crashing_score():
    case,gold = load_dev()[0][0],load_dev()[1][0]
    result = score_run(case,gold,dict(execution=[]))
    assert result['status']=='CONTRACT_ERROR'
    assert result['failure_stage']=='execution_contract'
