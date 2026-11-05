"""Generic cardinality and comparison contracts; hand-authored synthetic unit records."""
from copy import deepcopy
from datetime import UTC,datetime
import pytest
from linkloom.temporal_graph.models import Node,Edge,GraphSnapshot
from linkloom.temporal_graph.query import graph_query
from tests.unit.test_temporal_graph_query import edge,node,Store


def fixture():
    first=Node('w','meeting','m1','周会');second=Node('w','meeting','m2','评审')
    old=Node('w','decision_record','d1','旧方案');new=Node('w','decision_record','d2','新方案')
    proof=edge(node('person','A'),node('person','B')).evidence
    records=[dict(node_id=first.node_id,kind='meeting',meeting_date='2026-04-01',pending_count=2),
        dict(node_id=second.node_id,kind='meeting',meeting_date='2026-05-01',pending_count=0),
        dict(node_id=old.node_id,kind='decision_record',record_id='d1',subject_key='plan',value='旧',status='SUPERSEDED',valid_from='2026-04-01',valid_to='2026-05-01',evidence=[e.to_dict() for e in proof]),
        dict(node_id=new.node_id,kind='decision_record',record_id='d2',subject_key='plan',value='新',status='ACTIVE',valid_from='2026-05-01',valid_to=None,evidence=[e.to_dict() for e in proof])]
    edges=[Edge(old,first,'decided_in','2026-04-01',None,'interval','event1','approval1',proof),
        Edge(new,second,'decided_in','2026-05-01',None,'interval','event2','approval2',proof),
        Edge(new,old,'supersedes','2026-05-01',None,'interval','event2','approval2',proof)]
    class Records:
        def load_edges(self,workspace,*args):return GraphSnapshot('w',tuple(edges),(),(first,second,old,new),tuple(records))
    return Records(),first,second,old,new,edges,records


def traverse(root,cardinality='list',kind='decided_in',direction='in',selection='all'):
    return dict(id='s',operation='traverse',input={'node':root.node_id},as_of=None,
                edge_type=kind,direction=direction,cardinality=cardinality,selection=selection)


def test_empty_list_is_found_with_zero_and_pending_count_but_single_is_not_found():
    store,first,*_=fixture()
    result=graph_query(store,[traverse(first,kind='assigned_in')],'2026-06-01','w',outputs=[dict(step='s',field='record')])
    assert result['status']=='FOUND' and result['outputs'][0]['values']==[]
    assert result['steps'][0]['pending_count']==2
    result=graph_query(store,[traverse(first,cardinality='single',kind='assigned_in')],'2026-06-01','w',outputs=[dict(step='s',field='node')])
    assert result['status']=='NOT_FOUND'


def test_single_conflicts_list_is_complete_and_order_is_original_line_order():
    store,first,second,old,new,edges,records=fixture();proof=edges[0].evidence
    edges.append(Edge(new,first,'decided_in','2026-05-01',None,'interval','event3','approval3',proof))
    single=graph_query(store,[traverse(first,'single')],'2026-06-01','w',outputs=[dict(step='s',field='record')])
    assert single['status']=='CONFLICT' and len(single['steps'][0]['edges'])==2
    listing=graph_query(store,[traverse(first)],'2026-06-01','w',outputs=[dict(step='s',field='record')])
    assert listing['status']=='FOUND' and len(listing['outputs'][0]['values'])==2
    before=graph_query(store,[traverse(first,'single')],'2026-03-01','w',outputs=[dict(step='s',field='record')])
    assert before['status']=='NO_VALID_RECORD_AT_TIME'


def test_comparison_reverses_dates_and_requires_confirmed_alignment_chain():
    store,first,second,old,new,edges,records=fixture()
    step=dict(id='diff',operation='compare',input={'node':second.node_id},other=first.node_id,domain='decisions',as_of=None)
    result=graph_query(store,[step],None,'w',outputs=[dict(step='diff',field='comparison')])
    diff=result['outputs'][0]['values'][0]
    assert result['status']=='FOUND' and diff['order_swapped']
    assert len(diff['changed'])==1 and len(diff['changed'][0]['supersedes_chain'])==1
    assert diff['pending']['earlier']==2
    edges[:]=[e for e in edges if e.edge_type!='supersedes']
    result=graph_query(store,[step],None,'w',outputs=[dict(step='diff',field='comparison')])
    assert result['outputs'][0]['values'][0]['changed']==[]
    assert result['outputs'][0]['values'][0]['unaligned'][0]['reason']=='CONFIRMED_SUPERSEDES_CHAIN_REQUIRED'


def test_actual_tool_and_reviewer_render_a_full_list_without_json_in_product_answer():
    from linkloom.ui.graph_investigation import GraphInvestigationService
    from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
    store,first,second,old,new,edges,records=fixture()
    plan=MeetingGraphPlan.from_dict(dict(steps=[traverse(first)],outputs=[dict(step='s',field='record')],as_of='2026-06-01'),
        nodes=store.load_edges('w').nodes)
    class Planner:
        def plan(self,context):return dict(status='PLANNED',plan=plan)
    worker=GraphInvestigationService(lambda ws:store,lambda context:Planner(),query_clock=lambda:datetime(2026,6,1,tzinfo=UTC))
    result=worker.run('周会拍板了什么','w')
    assert result['status']=='COMPLETED' and result['reviewer']['accepted'],result
    assert '旧' in result['source_investigation']['answer']
    assert '2 条待审' in result['source_investigation']['answer']
    assert 'node_id' not in result['source_investigation']['answer']
    assert worker.inspect(result['run_id'],'w')['status']=='COMPLETED'


def test_inspect_task_fields_obeys_as_of_and_outputs_scalar_deadline():
    store,first,second,old,new,edges,records=fixture()
    task=Node('w','task','task1','报告')
    proof=edges[0].evidence
    state=dict(valid_from='2026-04-01',valid_to=None,fields=dict(title='报告',owner='Ada',deadline=dict(deadline='2026-04-15',deadline_text='4/15')),
        status='open',confirmation_ref='approval-task',event_ids=['created'],review_event_id='review-task',evidence=[e.to_dict() for e in proof])
    records.append(dict(node_id=task.node_id,kind='task',task_id='task1',title='报告',states=[state],status='open'))
    edges.append(Edge(task,first,'assigned_in','2026-04-01',None,'interval','created','approval-task',proof))
    step=dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)
    result=graph_query(store,[step],'2026-04-02','w',outputs=[dict(step='task',field='owner'),dict(step='task',field='deadline')])
    assert result['status']=='FOUND'
    assert [r['values'] for r in result['outputs']]==[['Ada'],['2026-04-15']]
    before=graph_query(store,[step],'2026-03-01','w',outputs=[dict(step='task',field='owner')])
    assert before['status']=='NO_VALID_RECORD_AT_TIME'


def test_historical_inspection_and_derived_task_list_never_return_future_owner_deadline_or_evidence():
    from linkloom.agents.graph_result import render_meeting_outputs
    store,first,second,old,new,edges,records=fixture();task=Node('w','task','task-history','Task')
    proof=edges[0].evidence
    common=dict(status='open',confirmation_ref='approved-task',event_ids=['create'],review_event_id='review',evidence=[e.to_dict() for e in proof])
    states=[dict(common,valid_from='2026-04-01',valid_to='2026-05-01',fields=dict(title='Task',owner='Ada',deadline=dict(deadline='2026-04-20'))),
        dict(common,valid_from='2026-05-01',valid_to=None,fields=dict(title='Task',owner='Bo',deadline=dict(deadline='2026-05-20')))]
    records.append(dict(node_id=task.node_id,kind='task',task_id=task.key,title='Task',states=states,status='open'))
    edges.append(Edge(task,old,'derived_from','2026-04-01',None,'interval','created','approved-task',proof))
    inspect=dict(id='history',operation='inspect',input={'node':task.node_id},as_of=None)
    result=graph_query(store,[inspect],'2026-04-02','w',outputs=[dict(step='history',field='history')])
    assert len(result['outputs'][0]['values'][0])==1
    listing=traverse(old,kind='derived_from',direction='in')
    result=graph_query(store,[listing],'2026-04-02','w',outputs=[dict(step='s',field='record')])
    answer=render_meeting_outputs(result)
    assert 'Ada' in answer and '2026-04-20' in answer
    assert 'Bo' not in answer and '2026-05-20' not in answer
    assert len(result['outputs'][0]['values'][0]['states'])==1


@pytest.mark.parametrize('cardinality,selection',[('single','latest'),('list','all')])
def test_model_cannot_hide_owner_conflicts_or_blockers_by_cardinality_or_latest(cardinality,selection):
    store,first,second,old,new,edges,records=fixture()
    resource=node('project','Resource');person=node('person','Bo');proof=edges[0].evidence
    edges.append(Edge(person,resource,'owner_of','2026-04-01',None,'interval','negative','approval-negative',proof,True))
    edges.append(Edge(person,resource,'owner_of','2026-05-01',None,'interval','positive','approval-positive',proof))
    step=traverse(resource,kind='owner_of',direction='in',cardinality=cardinality,selection=selection)
    if selection=='latest':
        with pytest.raises(ValueError,match='LATEST_ONLY'):
            graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='s',field='node')])
    else:
        # Authorized contract correction: list is normalized, never a way to
        # suppress either the negative evidence or single-value conflict gate.
        normalized=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='s',field='node')])
        assert normalized['status']=='CONFLICT' and len(normalized['steps'][0]['edges'])==2
        assert normalized['outputs']==[]
    step=traverse(resource,kind='owner_of',direction='in',cardinality='single',selection='all')
    result=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='s',field='node')])
    assert result['status']=='CONFLICT'


def test_comparison_never_cites_unrelated_future_record_and_date_precision_cannot_pick_midnight():
    from dataclasses import replace
    store,first,second,old,new,edges,records=fixture()
    future=node('decision_record','future')
    witness=replace(edges[0].evidence[0],evidence_id='future-proof')
    records.append(dict(node_id=future.node_id,kind='decision_record',record_id='future',subject_key='future',value='future',
        valid_from='2027-01-01',valid_to=None,status='ACTIVE',evidence=[witness.to_dict()]))
    step=dict(id='diff',operation='compare',input={'node':first.node_id},other=second.node_id,domain='decisions',as_of=None)
    result=graph_query(store,[step],None,'w',outputs=[dict(step='diff',field='comparison')])
    assert all(e['evidence_id']!='future-proof' for e in result['steps'][0]['evidence'])
    records[2]['valid_to']='2026-04-01T12:00:00+00:00'
    inspect=dict(id='decision',operation='inspect',input={'node':old.node_id},as_of=None)
    result=graph_query(store,[inspect],'2026-04-01','w',outputs=[dict(step='decision',field='record')])
    assert result['status']=='CONFLICT'


def test_comparison_respects_explicit_time_and_full_same_day_meeting_order():
    store,first,second,old,new,edges,records=fixture()
    step=dict(id='diff',operation='compare',input={'node':second.node_id},other=first.node_id,domain='decisions',as_of=None)
    result=graph_query(store,[step],'2026-04-15','w',outputs=[dict(step='diff',field='comparison')])
    assert result['status']=='NO_VALID_RECORD_AT_TIME'
    records[0].update(meeting_date='2026-04-01',meeting_time='2026-04-01T09:00:00+00:00')
    records[1].update(meeting_date='2026-04-01',meeting_time='2026-04-01T12:00:00+00:00')
    result=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='diff',field='comparison')])
    assert result['outputs'][0]['values'][0]['order_swapped']
    records[0]['meeting_time']=None
    result=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='diff',field='comparison')])
    assert result['status']=='CONFLICT'


def test_meeting_profile_preserves_time_selectors_and_relation_query_bound():
    from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
    from linkloom.temporal_graph.models import GraphLimits
    store,first,second,old,new,edges,records=fixture()
    result=graph_query(store,[traverse(new,'single',kind='supersedes',direction='out')],'2026-06-01','w',outputs=[dict(step='s',field='valid_from')])
    assert result['outputs'][0]['values']==['2026-05-01']
    rows=[]
    for i in range(9):
        row=traverse(first);row['id']='s'+str(i);row['as_of']='2026-06-'+str(i+1).zfill(2);rows.append(row)
    with pytest.raises(ValueError,match='RELATION_QUERY_LIMIT'):
        MeetingGraphPlan.from_dict(dict(steps=rows,outputs=[dict(step='s0',field='record')],as_of=None),nodes=store.load_edges('w').nodes)
    rows=[traverse(first),dict(traverse(first),id='duplicate')]
    with pytest.raises(ValueError,match='DUPLICATE_STEP'):
        MeetingGraphPlan.from_dict(dict(steps=rows,outputs=[dict(step='s',field='record')],as_of=None),nodes=store.load_edges('w').nodes)


def test_historical_task_record_hides_future_source_replacement_and_proof():
    from linkloom.temporal_graph.meeting_query import _selected,_proof
    future_proof={'evidence_id':'future'}
    state=dict(valid_from='2026-04-01',valid_to=None,status='open',fields={'owner':'Ada'},evidence=[])
    task=dict(kind='task',states=[state],source_decision=dict(value='old',valid_to='2026-07-01',
        replacement=dict(value='Future new plan',valid_from='2026-07-01'),evidence=[],replacement_evidence=[future_proof]))
    earlier=_selected(task,'2026-06-01')
    assert earlier['source_decision']['replacement'] is None and 'replacement_evidence' not in earlier['source_decision']
    assert _proof(earlier)==[]
    later=_selected(task,'2026-08-01')
    assert later['source_decision']['replacement']['value']=='Future new plan'
    assert future_proof in _proof(later)


def test_explicit_root_candidates_are_returned_without_choosing_and_empty_root_is_not_found():
    store,first,second,*_=fixture()
    row=dict(id='root',operation='resolve',input={'candidates':[first.node_id,second.node_id]},as_of=None)
    result=graph_query(store,[row],'2026-06-01','w',outputs=[dict(step='root',field='record')])
    assert result['status']=='AMBIGUOUS' and len(result['steps'][0]['records'])==2
    row['input']={'candidates':[]}
    result=graph_query(store,[row],'2026-06-01','w',outputs=[dict(step='root',field='record')])
    assert result['status']=='NOT_FOUND' and result['outputs']==[]


def test_resolve_cannot_select_one_of_known_same_label_identities():
    store,first,second,old,new,edges,records=fixture()
    a=Node('w','task','a','同名任务');b=Node('w','task','b','同名任务');proof=edges[0].evidence
    for task in (a,b):
        edges.append(Edge(task,first,'assigned_in','2026-04-01',None,'interval','event'+task.key,'approval'+task.key,proof))
    row=dict(id='root',operation='resolve',input={'candidates':[a.node_id]},as_of=None)
    with pytest.raises(ValueError,match='INCOMPLETE_AMBIGUOUS_ROOT'):
        graph_query(store,[row],'2026-06-01','w',outputs=[dict(step='root',field='record')])


def test_resolve_cannot_drop_time_conflict_to_select_another_candidate():
    store,first,second,old,new,edges,records=fixture()
    a=Node('w','task','a','任务A');b=Node('w','task','b','任务B');proof=edges[0].evidence
    common=dict(status='open',confirmation_ref='approved',event_ids=['create'],review_event_id='review',evidence=[e.to_dict() for e in proof])
    for task in (a,b):
        edges.append(Edge(task,first,'assigned_in','2026-04-01',None,'interval','event'+task.key,'approval'+task.key,proof))
        states=[dict(common,valid_from='2026-04-01',valid_to=None,fields=dict(title=task.label,owner='Ada'))]
        if task==a:
            states[0]['valid_to']='2026-04-01T12:00:00+00:00'
            states.append(dict(common,valid_from='2026-04-01T12:00:00+00:00',valid_to=None,fields=dict(title=task.label,owner='Bo')))
        records.append(dict(node_id=task.node_id,kind='task',task_id=task.key,title=task.label,states=states,status='open'))
    row=dict(id='root',operation='resolve',input={'candidates':[a.node_id,b.node_id]},as_of=None)
    result=graph_query(store,[row],'2026-04-01','w',outputs=[dict(step='root',field='record')])
    assert result['status']=='CONFLICT' and result['outputs']==[]
    assert {n['node_id'] for n in result['steps'][0]['output_nodes']}=={a.node_id,b.node_id}
    assert result['steps'][0]['evidence']


def test_confirmed_person_without_separate_record_can_resolve_into_a_task_list_with_evidence():
    store,first,second,old,new,edges,records=fixture()
    person=Node('w','person','ada','Ada');task=Node('w','task','task-person','报告');proof=edges[0].evidence
    state=dict(valid_from='2026-04-01',valid_to=None,status='open',fields=dict(title='报告',owner='Ada',deadline={'deadline':'2026-04-20'}),evidence=[e.to_dict() for e in proof])
    records.append(dict(node_id=task.node_id,kind='task',task_id=task.key,title=task.label,states=[state],status='open'))
    edges.append(Edge(person,task,'owner_of','2026-04-01',None,'interval','owner-event','owner-approval',proof))
    resolve=dict(id='person',operation='resolve',input={'candidates':[person.node_id]},as_of=None)
    find=dict(id='tasks',operation='traverse',input={'step':'person'},edge_type='owner_of',direction='out',cardinality='list',selection='all',as_of=None)
    result=graph_query(store,[resolve,find],'2026-04-02','w',outputs=[dict(step='tasks',field='record')])
    assert result['status']=='FOUND' and result['outputs'][0]['values'][0]['task_id']==task.key
    assert result['steps'][0]['evidence'] and result['steps'][0]['edges']==[]
    before=graph_query(store,[resolve],'2026-03-01','w',outputs=[dict(step='person',field='node')])
    assert before['status']=='NO_VALID_RECORD_AT_TIME'


def test_inspection_validity_selectors_return_confirmed_record_fields():
    store,first,second,old,new,edges,records=fixture()
    step=dict(id='decision',operation='inspect',input={'node':new.node_id},as_of=None)
    result=graph_query(store,[step],'2026-06-01','w',outputs=[dict(step='decision',field='valid_from'),dict(step='decision',field='valid_to')])
    assert [o['values'] for o in result['outputs']]==[['2026-05-01'],[None]]


def test_conflicting_manual_owner_is_labeled_as_human_context_not_raw_source_fact():
    from linkloom.agents.graph_result import project_meeting_result
    store,first,second,old,new,edges,records=fixture();task=Node('w','task','manual-time','报告');proof=edges[0].evidence
    common=dict(status='open',review_event_id='review',confirmation_ref='approved',event_ids=['event'],evidence=[e.to_dict() for e in proof])
    states=[dict(common,valid_from='2026-04-01',valid_to='2026-04-01T12:00:00+00:00',fields=dict(title='报告',owner='Ada'),human_context_fields=[]),
        dict(common,valid_from='2026-04-01T12:00:00+00:00',valid_to=None,fields=dict(title='报告',owner='Manual Bo'),human_context_fields=['owner'])]
    records.append(dict(node_id=task.node_id,kind='task',task_id=task.key,title=task.label,states=states,status='open'))
    edges.append(Edge(task,first,'assigned_in','2026-04-01',None,'interval','event','approved',proof))
    step=dict(id='owner',operation='inspect',input={'node':task.node_id},as_of=None)
    result=graph_query(store,[step],'2026-04-01','w',outputs=[dict(step='owner',field='owner')])
    view,evidence=project_meeting_result(result)
    assert result['status']=='CONFLICT' and view['evidence_level']=='HUMAN_REVIEW_WITH_SOURCE_CONTEXT'
    assert 'Manual Bo' in view['answer'] and '人工修改；原文仅作上下文' in view['answer'] and evidence
