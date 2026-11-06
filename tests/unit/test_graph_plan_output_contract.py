"""Offline contract regressions with synthetic objects, never evaluation prompts."""
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from linkloom.agents.graph_result import project_meeting_result
from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
from linkloom.temporal_graph.mention_planning import compile_plan
from linkloom.temporal_graph.planning import GraphPlanningContext
from linkloom.temporal_graph.query import graph_query
from linkloom.ui.graph_investigation import GraphInvestigationService
from tests.unit.test_graph_contract_repair import task_fixture
from tests.unit.test_graph_mentions import FixedLocator, mention, raw
from tests.unit.test_meeting_graph_query import fixture
from tests.unit.test_temporal_graph_query import node


def inspection(key, operation='inspect', source=None, when=None):
    return dict(id=key, operation=operation, input=source, as_of=when)


@pytest.mark.parametrize('reverse', [False, True])
def test_duplicate_inspections_remap_outputs_and_dependent_inputs(reverse):
    task=node('task','Release checklist')
    steps=[inspection('a',source={'node':task.node_id}),
           inspection('b','inspect_history',{'node':task.node_id})]
    if reverse:steps.reverse()
    steps.append(inspection('child',source={'step':steps[1]['id']}))
    outputs=[dict(step='a',field='owner'),dict(step='b',field='history'),dict(step='child',field='record')]
    data=dict(steps=steps,outputs=outputs,as_of='2028-02-03');before=deepcopy(data)
    plan=MeetingGraphPlan.from_dict(data,nodes=[task])
    survivor=steps[0]['id']
    assert len(plan.steps)==2
    assert plan.steps[1]['input']=={'step':survivor}
    assert {(o['step'],o['field']) for o in plan.outputs}>={(survivor,'owner'),(survivor,'history'),('child','record')}
    assert data==before


def test_different_times_never_merge_and_duplicate_ids_still_fail():
    task=node('task','Release checklist')
    steps=[inspection('a',source={'node':task.node_id},when='2028-02-03'),
           inspection('b','inspect_history',{'node':task.node_id},'2028-02-04')]
    plan=MeetingGraphPlan.from_dict(dict(steps=steps,outputs=[dict(step='b',field='history')],as_of=None),nodes=[task])
    assert len(plan.steps)==2
    steps[1]['id']='a'
    with pytest.raises(ValueError,match='INVALID_STEP_ID'):
        MeetingGraphPlan.from_dict(dict(steps=steps,outputs=[dict(step='a',field='history')],as_of=None),nodes=[task])


@pytest.mark.parametrize('constraint', ['plan','step','host'])
def test_date_declaration_consumed_only_by_matching_time_constraint(constraint):
    task=node('task','Inventory audit');question='Inventory audit owner on 2028/2/3'
    ctx=GraphPlanningContext(question,'w',(task,),lambda:True,as_of='2028-02-03' if constraint=='host' else None)
    request=raw([mention(task.label,'task'),mention('2028/2/3','date',key='date')],
                [inspection('check',source={'mention':'m'},when='2028-02-03' if constraint=='step' else '')],
                [dict(step='check',field='owner')],mode='lookup',kinds=['task'])
    request['as_of']='2028-02-03' if constraint in {'plan','host'} else ''
    plan=compile_plan(request,ctx)
    assert not any(s.get('input',{}).get('entity_mention',{}).get('kind')=='meeting' for s in plan.steps)
    bad=deepcopy(request);bad['mentions'][1]['mention']='2/3'
    with pytest.raises(ValueError,match='DATE_MENTION_TOKEN_TRUNCATED'):compile_plan(bad,ctx)
    if constraint!='host':
        bad=deepcopy(request);bad['as_of']='2028-02-04';bad['steps'][0]['as_of']='2028-02-04'
        with pytest.raises(ValueError,match='UNUSED_MENTION'):compile_plan(bad,ctx)
    bad=deepcopy(request);bad['mentions'].append(mention('owner','person',key='unused'))
    with pytest.raises(ValueError,match='UNUSED_MENTION'):compile_plan(bad,ctx)


def test_task_history_result_type_is_repaired_without_changing_other_output_types():
    store,task,_=task_fixture();store.mention_locator=FixedLocator([task])
    ctx=GraphPlanningContext(task.label+' revisions','w',store.load_edges('w').nodes,lambda:True)
    request=raw([mention(task.label,'task')],[inspection('h','inspect_history',{'mention':'m'},'')],
                [dict(step='h',field='history')],mode='history',kinds=['decision_record'])
    plan=compile_plan(request,ctx)
    payload=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    projected,_=project_meeting_result(payload)
    assert payload['status']=='FOUND' and 'One' in projected['answer'] and 'Two' in projected['answer']
    assert all(r['kind']=='task' for r in payload['steps'][-1]['records'])
    bad=deepcopy(request);bad['intent']['result_kinds']=['meeting']
    with pytest.raises(ValueError,match='UNREQUESTED_OUTPUT_NODE_TYPE'):compile_plan(bad,ctx)


def test_project_history_resolves_literal_decision_subject_and_keeps_evidence_gates():
    store,_,_,old,new,_,_=fixture();store.mention_locator=FixedLocator([new])
    ctx=GraphPlanningContext('Warehouse strategy revisions','w',store.load_edges('w').nodes,lambda:True)
    request=raw([mention('Warehouse strategy','project')],
                [inspection('h','inspect_history',{'mention':'m'},'')],
                [dict(step='h',field='history')],kinds=['decision_record'])
    before=deepcopy(request);plan=compile_plan(request,ctx)
    definition=plan.steps[0]['input']['entity_mention']
    assert definition['text']=='Warehouse strategy' and definition['kind']=='decision_record'
    assert definition['temporal_mode']=='history' and request==before
    result=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND'
    assert [r['record_id'] for r in result['outputs'][0]['values'][0]]==[old.key,new.key]
    assert result['outputs'][0]['evidence_ids'] and result['steps'][-1]['edges']
    request['intent']['result_kinds']=['person']
    with pytest.raises(ValueError,match='PROJECT_HISTORY_TYPE_NOT_CONVERTIBLE'):compile_plan(request,ctx)


def test_decision_history_survives_tool_review_projection_and_time_scope():
    store,_,_,old,new,_,_=fixture();store.mention_locator=FixedLocator([old])
    request=raw([mention('Strategy','decision_record')],
                [inspection('h','inspect_history',{'mention':'m'},'')],
                [dict(step='h',field='history')],mode='history',kinds=['decision_record'])
    class Planner:
        def plan(self,ctx):return dict(status='PLANNED',plan=compile_plan(request,ctx))
    worker=GraphInvestigationService(lambda ws:store,lambda ctx:Planner(),query_clock=lambda:datetime(2026,6,1,tzinfo=UTC))
    result=worker.run('Strategy revisions','w')
    assert result['graph_status']=='FOUND' and result['reviewer']['accepted']
    answer=result['source_investigation']['answer']
    assert '2026-04-01：旧' in answer and '2026-05-01：新' in answer
    assert result['evidence'] and worker.inspect(result['run_id'],'w')==result
    early=worker.run('Strategy revisions','w',as_of='2026-04-15')
    assert '新' not in early['source_investigation']['answer']


def test_found_without_selected_content_is_explicit_not_found_but_empty_list_still_renders():
    store,_,_,_,new,_,_=fixture()
    step=inspection('check',source={'node':new.node_id})
    payload=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='check',field='owner')])
    assert payload['status']=='NOT_FOUND' and payload['reason_code']=='NO_SELECTED_CONTENT'
    projected,_=project_meeting_result(payload)
    assert projected['answer'].strip() and '未找到' in projected['answer']
