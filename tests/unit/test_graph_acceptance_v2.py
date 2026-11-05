from copy import deepcopy
from graph_acceptance_v2 import AcceptanceEvaluator,compile_expected
from tests.unit.test_holdout_acceptance_adapter import sample


def list_sample():
    old,actual=sample();fixture=deepcopy(old.fixture);index=deepcopy(old.index)
    record=deepcopy(actual['result_steps'][0]['records'][0]);record.update(task_id='task-other',title='Second artifact')
    record['current']['fields']['title']='Second artifact';record['states'][0]['fields']['title']='Second artifact'
    task=deepcopy(fixture['confirmed_tasks'][0]);task.update(id='Z',title='Second artifact');fixture['confirmed_tasks'].append(task);index['tasks']['Z']='task-other'
    actual['result_steps'][0]['records'].append(record);actual['structured_outputs'][0]['values'].append(record)
    actual['structured_outputs'] += [dict(step='s',field='owner',values=['Foo','Foo']),dict(step='s',field='deadline',values=['2024-04-08','2024-04-08'])]
    return AcceptanceEvaluator(fixture,index),actual


def test_multi_task_scalar_lists_are_bound_and_complete_without_single_task_constraint():
    scorer,actual=list_sample();rules=compile_expected(dict(status='FOUND',tasks=['Artifact · Foo · 4/8','Second artifact · Foo · 4/8']))
    assert scorer(actual,rules)['correct']
    for field in ['owner','deadline']:
        bad=deepcopy(actual);next(o for o in bad['structured_outputs'] if o['field']==field)['values'].pop()
        assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);next(o for o in bad['structured_outputs'] if o['field']=='owner')['values'][0]='Injected'
    assert not scorer(bad,rules)['correct']


def test_single_value_remains_unique_and_unbound_scalars_rejected():
    scorer,actual=list_sample()
    assert not scorer(actual,compile_expected(dict(status='FOUND',owner='Foo',deadline='4/8')))['correct']
    bad=deepcopy(actual);bad['result_steps'][0]['records']=[]
    assert not scorer(bad,compile_expected(dict(status='FOUND',tasks=['Artifact · Foo · 4/8','Second artifact · Foo · 4/8'])))['correct']


def test_list_scalars_cannot_contradict_selected_historical_snapshot():
    from tests.unit.test_holdout_acceptance_adapter import comparison_sample
    old,comparison,_=comparison_sample();scorer=AcceptanceEvaluator(old.fixture,old.index)
    changed=comparison['structured_outputs'][1]['values'][0]['changed'][0]
    before,after=changed['before'],changed['after']
    actual=dict(status='FOUND',answer='synthetic',relations=[],evidence=['A','B'],structured_outputs=[dict(step='a',field='record',values=[before]),dict(step='b',field='deadline',values=['2024-04-09'])],result_steps=[dict(id='a',status='FOUND',records=[before],evidence=before['current']['evidence']),dict(id='b',status='FOUND',records=[after],evidence=after['current']['evidence'])])
    assert not scorer(actual,compile_expected(dict(status='FOUND',tasks=['Artifact · Foo · 4/8'])))['correct']
