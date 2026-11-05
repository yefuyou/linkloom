from copy import deepcopy
from datetime import datetime,UTC
from dataclasses import replace
import pytest
from linkloom.temporal_graph.models import Node,GraphSnapshot,Edge
from linkloom.temporal_graph.planning import GraphPlanningContext,GraphQuestionPlanner
from linkloom.temporal_graph.query import graph_query
from tests.unit.test_temporal_graph_planning import Model,planner
from tests.unit.test_meeting_graph_query import fixture,traverse


def emit(steps,outputs,entities,types,mode='lookup'):
    return dict(applicable=True,as_of='',refusal_reasons=[],steps=steps,outputs=outputs,intent=dict(mode=mode,result_kinds=types,entities=entities))


def entity(node,text=None,mode='exact',others=()):
    return dict(text=text or node.label,kind=node.kind,match=mode,candidates=[node.node_id]+[n.node_id for n in others])


def grounded(raw,context):return planner(Model(raw),grounding_required=True).plan(context)


def test_absent_named_artifact_cannot_be_replaced_by_a_related_existing_task():
    task=Node('w','task','k','Invoice script');context=GraphPlanningContext('Invoice dashboard owner?','w',(task,),lambda:True)
    step=dict(id='state',operation='inspect',input={'node':task.node_id},as_of='')
    raw=emit([step],[dict(step='state',field='owner')],[entity(task,'Invoice dashboard')],['task'])
    result=grounded(raw,context)
    assert result['status']=='PLANNED' and result['missing_entities']==[dict(text='Invoice dashboard',kind='task')]
    assert result['plan'].steps[0]['input']=={'entity':{'text':'Invoice dashboard','kind':'task'}}
    assert task.node_id not in str(result['plan'].to_dict())


def test_root_witness_must_be_literal_and_dates_cannot_be_invented():
    meeting=Node('w','meeting','m','Planning meeting');ctx=GraphPlanningContext('Planning meeting decisions','w',(meeting,),lambda:True)
    raw=emit([traverse(meeting)],[dict(step='s',field='record')],[entity(meeting,'invented')],['decision_record'],'list')
    assert grounded(raw,ctx)['reason']=='ENTITY_WITNESS_NOT_IN_QUESTION'
    raw=emit([dict(id='root',operation='resolve',input={'meeting_date':'2026-04-01'},as_of='')],[dict(step='root',field='record')],[],['meeting'])
    assert grounded(raw,ctx)['reason']=='MEETING_DATE_NOT_IN_QUESTION'


def test_requested_decision_list_rejects_task_outputs():
    meeting=Node('w','meeting','m','Planning meeting');ctx=GraphPlanningContext('Planning meeting decisions','w',(meeting,),lambda:True)
    raw=emit([traverse(meeting,kind='assigned_in')],[dict(step='s',field='record')],[entity(meeting)],['decision_record'],'list')
    assert grounded(raw,ctx)['reason']=='UNREQUESTED_OUTPUT_NODE_TYPE'


def test_owner_collection_infers_target_kinds_and_filters_mixed_business_nodes():
    person=Node('w','person','p','Morgan');task=Node('w','task','k','Report');project=Node('w','project','j','Project')
    ctx=GraphPlanningContext('Morgan work','w',(person,task,project),lambda:True)
    raw=emit([traverse(person,kind='owner_of',direction='out')],[dict(step='s',field='record')],[entity(person)],['task'],'list')
    result=grounded(raw,ctx)
    assert result['status']=='PLANNED',result
    assert result['plan'].steps[0]['target_kind']=='task'


def test_generic_meeting_comparison_completes_both_requested_domains():
    store,first,second,*_=fixture();ctx=GraphPlanningContext(first.label+' vs '+second.label,'w',store.load_edges('w').nodes,lambda:True)
    raw=emit([dict(id='diff',operation='compare',input={'node':first.node_id},other=second.node_id,domain='tasks',as_of='')],[dict(step='diff',field='comparison')],[entity(first),entity(second)],['task','decision_record'],'compare')
    result=grounded(raw,ctx)
    assert result['status']=='PLANNED',result
    assert {s['domain'] for s in result['plan'].steps}=={'decisions','tasks'}
    payload=graph_query(store,list(result['plan'].steps),'2026-06-01','w',outputs=list(result['plan'].outputs))
    assert payload['status']=='FOUND' and len(payload['outputs'])==2


def test_decision_history_survives_supersession_and_respects_as_of():
    store,first,second,old,new,*_=fixture();ctx=GraphPlanningContext(old.label+' history','w',store.load_edges('w').nodes,lambda:True)
    raw=emit([dict(id='state',operation='inspect',input={'node':old.node_id},as_of='')],[dict(step='state',field='record')],[entity(old)],['decision_record'],'history')
    result=grounded(raw,ctx);assert result['status']=='PLANNED',result
    assert result['plan'].steps[0]['operation']=='inspect_history'
    payload=graph_query(store,list(result['plan'].steps),'2026-06-01','w',outputs=list(result['plan'].outputs))
    assert payload['status']=='FOUND' and {r['record_id'] for r in payload['steps'][0]['records']}=={old.key,new.key}
    earlier=graph_query(store,list(result['plan'].steps),'2026-04-02','w',outputs=list(result['plan'].outputs))
    assert earlier['status']=='FOUND' and [r['record_id'] for r in earlier['steps'][0]['records']]==[old.key]
    assert earlier['steps'][0]['records'][0]['status']=='ACTIVE'


def task_fixture():
    store,first,second,old,new,edges,records=fixture();task=Node('w','task','t','Artifact');proof=edges[0].evidence
    def state(when,end,owner):return dict(valid_from=when,valid_to=end,fields=dict(title='Artifact',owner=owner,deadline={'deadline':'2026-06-08'}),status='open',confirmation_ref='review-'+owner,event_ids=['event-'+owner],review_event_id='review-'+owner,evidence=[e.to_dict() for e in proof])
    states=[state('2026-04-01','2026-05-01','Morgan'),state('2026-05-01',None,'Taylor')]
    records.append(dict(node_id=task.node_id,kind='task',task_id=task.key,title=task.label,states=states,status='open'))
    edges.append(Edge(task,first,'assigned_in','2026-04-01',None,'interval','created','review-Morgan',proof))
    edges.append(Edge(task,second,'assigned_in','2026-05-01',None,'interval','changed','review-Taylor',proof))
    return store,first,second,task,edges,records


def test_membership_inspection_inherits_meeting_assignment_not_current_fields():
    store,first,second,task,*_=task_fixture();steps=[traverse(first,kind='assigned_in'),dict(id='fields',operation='inspect',input={'step':'s'},as_of=None)]
    outputs=[dict(step='s',field='record'),dict(step='fields',field='owner')]
    payload=graph_query(store,steps,'2026-06-01','w',outputs=outputs)
    assert payload['status']=='FOUND' and payload['outputs'][1]['values']==['Morgan']
    assert payload['steps'][1]['records'][0]['meeting_context_date']=='2026-04-01'
    steps[1]['as_of']='2026-06-01'
    explicit=graph_query(store,steps,'2026-06-01','w',outputs=outputs)
    assert explicit['outputs'][1]['values']==['Taylor']


@pytest.mark.parametrize('step_time',[False,True])
def test_broad_root_keeps_downstream_operation_when_time_leaves_one_candidate(step_time):
    store,first,second,task,edges,records=task_fixture();other=Node('w','task','t2','Artifact other');record=deepcopy(records[-1]);record.update(node_id=other.node_id,task_id=other.key,title=other.label);record['states']=record['states'][1:]
    for state in record['states']:state['fields']['title']=other.label
    records.append(record);edges.append(Edge(other,second,'assigned_in','2026-05-01',None,'interval','created2','review-Taylor',edges[0].evidence))
    ctx=GraphPlanningContext('Artifact work owner','w',store.load_edges('w').nodes,lambda:True,as_of=None if step_time else '2026-04-02')
    raw=emit([dict(id='fields',operation='inspect',input={'node':task.node_id},as_of='2026-04-02' if step_time else '')],[dict(step='fields',field='owner')],[entity(task,'Artifact','broad',(other,))],['task'])
    result=grounded(raw,ctx);assert result['status']=='PLANNED',result
    assert result['plan'].steps[-1]['input'].get('step')=='grounded_root_0_0'
    payload=graph_query(store,list(result['plan'].steps),result['plan'].as_of,'w',outputs=list(result['plan'].outputs),now=datetime(2026,6,1,tzinfo=UTC))
    assert payload['status']=='FOUND' and next(o for o in payload['outputs'] if o['field']=='owner')['values']==['Morgan']


def test_pending_proposal_context_is_ledgered_not_a_node_and_is_revalidated(tmp_path,monkeypatch):
    from tests.meeting_graph_eval_support import build_meetings,task_candidate_source
    from linkloom.temporal_graph.store import SQLiteGraphStore
    from linkloom.ui.graph_investigation import GraphInvestigationService
    fixture_data=dict(workspace='synthetic',topic='Synthetic release',meetings=[dict(id='M',name='Planning meeting',date='2026-04-01',lines=['1 Ship on 4/8.','2 Someone proposed a color panel, not scheduled.'])],confirmed_decisions=[dict(id='E',meeting='M',evidence='M#1',valid_from='2026-04-01')],confirmed_tasks=[],unconfirmed=[dict(evidence='M#2')])
    database=tmp_path/'confirmed.sqlite';index=build_meetings(fixture_data,database,recorded_at='2026-06-01T00:00:00+00:00');workspace=index['workspace_id']
    authorized=[True]
    store=SQLiteGraphStore(database,authorized_workspace=workspace,authorize_source=lambda *args:authorized[0],task_candidate_source=task_candidate_source())
    nodes=store.load_edges(workspace).nodes
    assert all(n.kind!='task' for n in nodes)
    pending=store.unconfirmed_context(workspace,'color panel')
    assert pending['count']==1 and pending['evidence']
    raw=emit([dict(id='root',operation='resolve',input={'candidates':[]},as_of='')],[dict(step='root',field='record')],[dict(text='color panel',kind='task',match='exact',candidates=[])],['task'])
    worker=GraphInvestigationService(lambda ws:store,lambda ctx:planner(Model(raw),grounding_required=True),query_clock=lambda:datetime(2026,6,1,tzinfo=UTC))
    result=worker.run('color panel owner?',workspace)
    assert result.get('graph_status')=='NOT_FOUND' and result['source_investigation']['facts']==[],result
    assert '未找到已确认的任务' in result['source_investigation']['answer'] and '仅为提议、未确认' in result['source_investigation']['answer']
    row=result['source_investigation']['graph_query']['steps'][0]
    assert row['unconfirmed_context']['count']==1 and row['unconfirmed_context']['candidate_refs'] and row['evidence']
    assert worker.inspect(result['run_id'],workspace)['status']=='INSUFFICIENT'
    authorized[0]=False
    assert worker.inspect(result['run_id'],workspace)['status']=='BLOCKED'
    authorized[0]=True
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with sqlite3.connect(database) as db:db.execute("UPDATE semantic_candidate SET candidate_fingerprint='tampered' WHERE workflow_state='SUPPORTING_ONLY'")
    # The production DB forbids mutation. Inject a corrupt decoded fingerprint
    # to verify the independent reader rejects it without weakening the trigger.
    import linkloom.semantic_ingestion.materialization as materialization
    original=materialization._candidate_view_from_row
    def corrupt(row):
        candidate,view,encoded,fingerprint=original(row)
        return candidate,view,encoded,'forged'
    monkeypatch.setattr(materialization,'_candidate_view_from_row',corrupt)
    assert store.unconfirmed_context(workspace,'color panel')['count']==0


@pytest.mark.parametrize('question,literal',[('2026/4/1 meeting','4/1'),('11/20 meeting','1/20'),('2026年4月1日会议','4月1日')])
def test_date_witness_cannot_drop_year_or_truncate_another_date(question,literal):
    meeting=Node('w','meeting','m','Planning');ctx=GraphPlanningContext(question,'w',(meeting,),lambda:True)
    raw=emit([dict(id='root',operation='resolve',input={'meeting_date':literal},as_of='')],[dict(step='root',field='record')],[],['meeting'])
    assert grounded(raw,ctx)['reason']=='MEETING_DATE_NOT_IN_QUESTION'


def test_partial_meeting_name_cannot_silently_choose_one_matching_meeting():
    first=Node('w','meeting','a','Planning north');second=Node('w','meeting','b','Planning south')
    ctx=GraphPlanningContext('Planning records','w',(first,second),lambda:True)
    raw=emit([dict(id='read',operation='inspect',input={'node':first.node_id},as_of='')],[dict(step='read',field='record')],[entity(first,'Planning')],['meeting'])
    result=grounded(raw,ctx);assert result['status']=='PLANNED',result
    assert set(result['plan'].steps[0]['input']['candidates'])=={first.node_id,second.node_id}
    assert result['plan'].steps[1]['input']=={'step':result['plan'].steps[0]['id']}
