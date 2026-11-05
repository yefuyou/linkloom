from dataclasses import replace
import pytest
from linkloom.temporal_graph.models import Node,GraphLimits
from linkloom.temporal_graph.planning import GraphPlanningContext
from linkloom.temporal_graph.query import graph_query
from tests.unit.test_temporal_graph_planning import Model,planner
from tests.unit.test_meeting_graph_query import fixture


def task_plan(fields):
    task=Node('w','task','arbitrary','Arbitrary artifact')
    context=GraphPlanningContext('Read the confirmed state','w',(task,),lambda:True,as_of='2026-04-09')
    raw=dict(applicable=True,as_of='',refusal_reasons=[],steps=[dict(id='state',operation='inspect',input={'node':task.node_id},as_of='')],outputs=[dict(step='state',field=f) for f in fields])
    return context,raw


def test_scalar_task_snapshot_context_has_no_new_entity_hop_or_time_change():
    context,raw=task_plan(['owner']);result=planner(Model(raw)).plan(context)
    assert result['status']=='PLANNED'
    value=result['plan']
    assert len(value.steps)==1 and value.steps[0]['input']==raw['steps'][0]['input']
    assert value.as_of==context.as_of
    assert [o['field'] for o in value.outputs]==['owner','record','history']
    context,raw=task_plan(['owner','record','history'])
    assert len(planner(Model(raw)).plan(context)['plan'].outputs)==3


def test_context_never_repairs_invalid_plan_or_weakens_output_limit():
    context,raw=task_plan(['owner']);raw['steps'][0]['input']['node']='unknown'
    result=planner(Model(raw)).plan(context)
    assert result['status']=='OUTPUT_INVALID' and result['plan'] is None
    context,raw=task_plan(['owner','deadline'])
    context=replace(context,limits=GraphLimits(max_subgoals=1))
    assert planner(Model(raw)).plan(context)['reason']=='OUTPUT_LIMIT'


def test_replacement_chain_proofs_do_not_masquerade_as_complete_records():
    store,first,second,old,new,edges,records=fixture()
    step=dict(id='diff',operation='compare',input={'node':second.node_id},other=first.node_id,domain='decisions',as_of=None)
    result=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='diff',field='comparison')])
    diff=result['outputs'][0]['values'][0]
    assert diff['earlier_meeting_label']==first.label and diff['later_meeting_label']==second.label
    proof=diff['changed'][0]['supersedes_chain'][0]
    assert proof['schema_version']=='graph-edge-proof/v1'
    assert proof['source_ref']['node_type']=='decision_record' and proof['source_ref']['key']==new.key
    assert proof['target_ref']['key']==old.key
    assert 'kind' not in proof['source_ref'] and 'value' not in proof['source_ref']
    original=next(e for e in edges if e.edge_type=='supersedes').to_dict()
    for field in ('edge_id','edge_type','valid_from','valid_to','evidence','confirmation_ref','blocker'):
        assert proof[field]==original[field]
    assert original['source']['kind']=='decision_record'
    def walk(value):
        if isinstance(value,dict):
            yield value
            for v in value.values():yield from walk(v)
        elif isinstance(value,list):
            for v in value:yield from walk(v)
    assert all('record_id' in value and 'value' in value for value in walk(diff) if value.get('kind')=='decision_record')
