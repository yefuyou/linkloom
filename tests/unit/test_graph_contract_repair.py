"""Authorized deterministic repairs, using synthetic records only; no DEV or D3."""
from copy import deepcopy
from hashlib import sha256
import pytest
from linkloom.temporal_graph.models import Node,GraphSnapshot
from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
from linkloom.temporal_graph.planning import GraphPlanningContext,_schema_for_model
from linkloom.temporal_graph.mention_planning import compile_plan,schema
from linkloom.temporal_graph.entity_locator import validate_definition
from linkloom.temporal_graph.query import graph_query
from tests.unit.test_graph_mentions import FixedLocator,mention,raw,membership
from tests.unit.test_meeting_graph_query import fixture,traverse
from tests.unit.test_temporal_graph_query import node,edge,Store


def typed_plan(steps,nodes,outputs=None):
    return MeetingGraphPlan.from_dict(dict(steps=steps,outputs=outputs or [dict(step=steps[-1]['id'],field='node')],as_of='2026-06-01'),nodes=nodes)


@pytest.mark.parametrize('relation,direction',[('owner_of','in'),('supersedes','in'),('supersedes','out'),('derived_from','out'),('decided_in','out'),('reports_to','out'),('alias_of','out'),('same_task','out')])
def test_single_relation_list_normalizes_without_touching_input_or_time(relation,direction,caplog):
    root=node('plan','Artifact');step=traverse(root,kind=relation,direction=direction)
    before=deepcopy(step)
    plan=typed_plan([step],[root])
    assert plan.steps[0]['cardinality']=='single' and plan.steps[0]['selection']=='all'
    assert plan.as_of=='2026-06-01' and plan.steps[0]['as_of'] is None and step==before
    assert 'GRAPH_CONTRACT_NORMALIZED' in caplog.text and 'list->single' in caplog.text


@pytest.mark.parametrize('relation',[ 'owner_of','supersedes'])
def test_normalized_single_value_keeps_both_candidates_and_evidence(relation):
    root=node('plan','Artifact');a=node('person' if relation=='owner_of' else 'plan','One');b=node(a.kind,'Two')
    for edges in [[edge(a,root,relation),edge(b,root,relation)],[edge(b,root,relation),edge(a,root,relation)]]:
        result=graph_query(Store(edges),[traverse(root,kind=relation)],'2026-06-01','w',outputs=[dict(step='s',field='node')])
        assert result['status'] in {'CONFLICT','AMBIGUOUS'} and result['outputs']==[]
        assert {r['source']['label'] for r in result['steps'][0]['edges']}=={'One','Two'}
        assert len(result['steps'][0]['edges'])==2 and all(e['evidence'] for e in result['steps'][0]['edges'])


@pytest.mark.parametrize('relation,direction',[('owner_of','in'),('reports_to','out'),('supersedes','in')])
def test_illegal_latest_rejected_before_any_store_query_or_time_rewrite(relation,direction):
    root=node('plan','Artifact');step=traverse(root,'single',relation,direction,'latest');step['as_of']='2026-04-01'
    with pytest.raises(ValueError,match='LATEST_ONLY_FOR_CURRENT_TASK_ASSIGNMENT'):
        typed_plan([step],[root])
    assert step['selection']=='latest' and step['as_of']=='2026-04-01'


def test_schema_enums_restrict_combinations_before_host_normalization():
    meeting=node('meeting','Digest');ctx=GraphPlanningContext('Digest decisions','w',(meeting,),lambda:True)
    for spec in [schema(ctx),_schema_for_model(ctx,'deepseek-flash')]:
        branch=next(b for b in spec['properties']['steps']['items']['anyOf'] if b['properties']['operation']['enum']==['traverse'])
        options=branch['allOf'][0]['anyOf']
        def accepts(edge_type,direction,cardinality,selection):
            row=dict(edge_type=edge_type,direction=direction,cardinality=cardinality,selection=selection)
            return any(all(row[k] in v['enum'] for k,v in option['properties'].items()) for option in options)
        assert accepts('owner_of','in','single','all')
        assert not accepts('owner_of','in','list','all')
        assert not accepts('supersedes','in','single','latest')
        assert not accepts('assigned_in','out','list','latest')
        assert accepts('assigned_in','out','single','latest')
        assert accepts('assigned_in','out','list','all')


def task_fixture():
    store,first,second,old,new,edges,records=fixture();task=Node('w','task','artifact','Artifact');proof=[p.to_dict() for p in edges[0].evidence]
    common=dict(status='open',confirmation_ref='approval',event_ids=['assignment'],review_event_id='review',evidence=proof)
    records.append(dict(node_id=task.node_id,kind='task',task_id=task.key,title=task.label,status='open',states=[
        dict(common,valid_from='2026-04-01',valid_to='2026-05-01',fields=dict(title=task.label,owner='One')),
        dict(common,valid_from='2026-05-01',valid_to=None,fields=dict(title=task.label,owner='Two'))]))
    edges.append(edge(task,first,'assigned_in',start='2026-04-01'))
    edges.append(edge(task,old,'derived_from',start='2026-04-01'))
    class Tasks:
        def load_edges(self,workspace,*args):
            s=store.load_edges(workspace);return GraphSnapshot(workspace,s.edges,(),s.nodes+(task,),s.records)
    return Tasks(),task,old


@pytest.mark.parametrize('via_traversal',[False,True])
def test_task_history_compiles_to_existing_projection_and_excludes_future(via_traversal,caplog):
    store,task,decision=task_fixture();target=decision if via_traversal else task
    store.mention_locator=FixedLocator([target]);ctx=GraphPlanningContext(target.label+' history','w',store.load_edges('w').nodes,lambda:True)
    steps=[]
    if via_traversal:steps.append(dict(id='tasks',operation='traverse',input={'mention':'m'},as_of='',edge_type='derived_from',direction='in',cardinality='list',selection='all'))
    steps.append(dict(id='history',operation='inspect_history',input={'step':'tasks'} if via_traversal else {'mention':'m'},as_of=''))
    request=raw([mention(target.label,target.kind)],steps,[dict(step='history',field='history')],'history',['task'])
    plan=compile_plan(request,ctx)
    assert next(s for s in plan.steps if s['id']=='history')['operation']=='inspect'
    result=graph_query(store,list(plan.steps),'2026-04-15','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND' and result['steps'][-1]['evidence']
    histories=[r for o in result['outputs'] if o['field']=='history' for states in o['values'] for r in states]
    assert len(histories)==1 and histories[0]['fields']['owner']=='One'
    assert all(r['fields']['owner']!='Two' for r in histories)
    assert 'inspect_history->inspect+history' in caplog.text


def test_unsupported_history_type_errors_and_redundant_task_history_is_coalesced():
    person=node('person','One')
    with pytest.raises(ValueError,match='HISTORY_REQUIRES_TASK_OR_DECISION.*use inspect'):
        typed_plan([dict(id='h',operation='inspect_history',input={'node':person.node_id},as_of=None)],[person])
    task=node('task','Artifact');steps=[dict(id='current',operation='inspect',input={'node':task.node_id},as_of=None),dict(id='h',operation='inspect_history',input={'node':task.node_id},as_of=None)]
    plan=typed_plan(steps,[task],[dict(step='h',field='history')])
    assert len(plan.steps)==1 and plan.steps[0]['id']=='current'
    assert {o['step'] for o in plan.outputs}=={'current'}
    assert {o['field'] for o in plan.outputs}=={'record','history'}


@pytest.mark.parametrize('question,text',[
    ('2031/3/7 planning decisions','3/7'),('11/20 planning decisions','1/20'),
    ('2031年3月7日 planning decisions','3月7日'),('2031.3.7 planning decisions','3.7'),
    ('2031/3/7 planning decisions','2031/3'),('11/20 planning decisions','11/2'),
    ('2031/3/7 planning decisions','3/7 planning')])
def test_partial_date_mentions_rejected_in_compile_and_direct_tool_proof(question,text):
    root=node('meeting','Digest');ctx=GraphPlanningContext(question,'w',(root,),lambda:True)
    with pytest.raises(ValueError,match='DATE_MENTION_TOKEN_TRUNCATED'):
        compile_plan(membership(text),ctx)
    start=question.index(text);definition=dict(text=text,kind='meeting',reference='direct',temporal_mode='current',source=dict(question=question,start=start,end=start+len(text),sha256=sha256(question.encode()).hexdigest()))
    with pytest.raises(ValueError,match='DATE_MENTION_TOKEN_TRUNCATED'):validate_definition(definition)


def test_date_input_kind_is_only_a_meeting_reference_and_full_year_survives_time_gate():
    store,first,*rest=fixture();records=rest[-1];records[0]['meeting_date']='2031-03-07'
    store.mention_locator=FixedLocator([first]);question='2031/3/7 decisions';ctx=GraphPlanningContext(question,'w',store.load_edges('w').nodes,lambda:True)
    request=membership('2031/3/7');request['mentions'][0]['kind']='date'
    plan=compile_plan(request,ctx)
    assert plan.steps[0]['input']['entity_mention']['kind']=='meeting'
    assert plan.steps[0]['input']['entity_mention']['text']=='2031/3/7'
    with pytest.raises(ValueError,match='LOCATOR_RETURNED_INELIGIBLE_NODE'):
        graph_query(store,list(plan.steps),'2030-12-01','w',outputs=list(plan.outputs))
    for short in ['3/7','1/20']:
        q='2031/3/7 decisions' if short=='3/7' else '11/20 decisions';bad=membership(short);bad['mentions'][0]['kind']='date'
        with pytest.raises(ValueError,match='DATE_MENTION_TOKEN_TRUNCATED'):
            compile_plan(bad,GraphPlanningContext(q,'w',ctx.nodes,lambda:True))


def test_task_history_inference_honours_confirmed_target_kind_on_polymorphic_edge():
    store,task,decision=task_fixture();owner=node('person','One');snapshot=store.load_edges('w')
    class Owned:
        def load_edges(self,workspace,*args):
            return GraphSnapshot('w',snapshot.edges+(edge(owner,task,'owner_of',end='2026-05-01'),),(),snapshot.nodes+(owner,),snapshot.records)
    steps=[dict(traverse(owner,kind='owner_of',direction='out'),target_kind='task'),dict(id='h',operation='inspect_history',input={'step':'s'},as_of=None)]
    plan=typed_plan(steps,Owned().load_edges('w').nodes,[dict(step='h',field='history')])
    assert plan.steps[-1]['operation']=='inspect'
    result=graph_query(Owned(),list(plan.steps),'2026-04-15','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND' and result['steps'][-1]['evidence']
    assert next(o for o in result['outputs'] if o['field']=='history')['values'][0][0]['fields']['owner']=='One'
