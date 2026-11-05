from copy import deepcopy
from datetime import datetime,UTC
import pytest
from linkloom.temporal_graph.models import Node,GraphSnapshot
from linkloom.temporal_graph.planning import GraphPlanningContext
from linkloom.temporal_graph.query import graph_query
from linkloom.temporal_graph.meeting_query import nearest_previous_meeting
from tests.unit.test_graph_grounded_contracts import emit,entity,grounded
from tests.unit.test_meeting_graph_query import fixture,traverse


def test_dated_meeting_shorthand_matches_structural_date_and_name_fragment():
    meeting=Node('w','meeting','m','2031/3/7 Strategic planning')
    ctx=GraphPlanningContext('2031/3/7 planning decisions','w',(meeting,),lambda:True,node_metadata=({'node_id':meeting.node_id,'meeting_date':'2031-03-07'},))
    raw=emit([traverse(meeting)],[dict(step='s',field='record')],[entity(meeting,'2031/3/7 planning')],['decision_record'],'list')
    result=grounded(raw,ctx)
    assert result['status']=='PLANNED' and not result.get('missing_entities'),result
    wrong=deepcopy(raw);wrong['intent']['entities'][0]['text']='2031/3/7 review'
    wrong_ctx=GraphPlanningContext('2031/3/7 review decisions','w',ctx.nodes,lambda:True,node_metadata=ctx.node_metadata)
    assert grounded(wrong,wrong_ctx)['missing_entities']


def test_broad_unresolved_reference_is_not_reported_as_absence():
    task=Node('w','task','t','Report');ctx=GraphPlanningContext('that other thing owner','w',(task,),lambda:True)
    raw=emit([dict(id='read',operation='inspect',input={'node':task.node_id},as_of='')],[dict(step='read',field='record')],[entity(task,'that other thing','broad')],['task'])
    assert grounded(raw,ctx)['reason']=='ROOT_REFERENCE_UNRESOLVED'


def test_type_contract_removes_unrequested_branch_and_keeps_requested_query():
    store,first,*_=fixture();ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True)
    decisions=traverse(first);tasks=traverse(first,kind='assigned_in');tasks['id']='tasks'
    raw=emit([decisions,tasks],[dict(step='s',field='record'),dict(step='tasks',field='record')],[entity(first)],['decision_record'],'list')
    result=grounded(raw,ctx);assert result['status']=='PLANNED',result
    assert [step['id'] for step in result['plan'].steps]==['s']
    assert [o['step'] for o in result['plan'].outputs]==['s']


def test_previous_reference_is_explicit_workspace_rule_rechecked_by_tool():
    store,first,second,old,new,edges,records=fixture()
    metadata=tuple(dict(r) for r in records if r['kind']=='meeting')
    ctx=GraphPlanningContext(second.label+' changed arrangements','w',store.load_edges('w').nodes,lambda:True,node_metadata=metadata)
    baseline=dict(text=second.label,kind='meeting',match='previous',candidates=[first.node_id])
    raw=emit([dict(id='diff',operation='compare',input={'node':second.node_id},other=first.node_id,domain='decisions',as_of='')],[dict(step='diff',field='comparison')],[entity(second),baseline],['decision_record'],'compare')
    result=grounded(raw,ctx);assert result['status']=='PLANNED',result
    step=result['plan'].steps[0];assert step['previous_anchor']==second.node_id and step['baseline_scope']=='workspace'
    first_payload=graph_query(store,list(result['plan'].steps),'2026-06-01','w',outputs=list(result['plan'].outputs))
    assert first_payload['status']=='FOUND'
    middle=Node('w','meeting','middle','Other workspace meeting')
    records.append(dict(node_id=middle.node_id,kind='meeting',meeting_date='2026-04-15',pending_count=0,evidence=[]))
    class Changed:
        def load_edges(self,workspace,*args):
            saved=store.load_edges(workspace);return GraphSnapshot(workspace,saved.edges,(),saved.nodes+(middle,),tuple(records))
    changed=graph_query(Changed(),list(result['plan'].steps),'2026-06-01','w',outputs=list(result['plan'].outputs))
    assert changed['status']=='CONFLICT' and changed['steps'][0]['reason_code']=='PREVIOUS_MEETING_CHANGED'
    assert changed['outputs']==[]


def test_previous_policy_does_not_infer_series_and_never_breaks_time_ties():
    records={'anchor':dict(kind='meeting',meeting_date='2031-03-10'),
        'same-series':dict(kind='meeting',meeting_date='2031-03-01',series='one'),
        'interleaved':dict(kind='meeting',meeting_date='2031-03-08',series='two')}
    result=nearest_previous_meeting(records,'anchor')
    assert result['candidates']==['interleaved']  # Explicit workspace scope, never similarity-based same-series inference.
    records['tie']=dict(kind='meeting',meeting_date='2031-03-08')
    assert nearest_previous_meeting(records,'anchor')['status']=='AMBIGUOUS'
    records['same-day']=dict(kind='meeting',meeting_date='2031-03-10')
    assert nearest_previous_meeting(records,'anchor')['status']=='AMBIGUOUS'


def test_decision_history_never_silently_truncates_beyond_hop_limit():
    from linkloom.temporal_graph.models import Edge,GraphLimits
    store,first,second,old,new,edges,records=fixture();third=Node('w','decision_record','third','Third decision')
    record=deepcopy(records[-1]);record.update(node_id=third.node_id,record_id=third.key,valid_from='2026-06-01',value='Third')
    records.append(record);edges.append(Edge(third,new,'supersedes','2026-06-01',None,'interval','new-event','reviewed',edges[0].evidence))
    step=dict(id='history',operation='inspect_history',input={'node':old.node_id},as_of=None)
    result=graph_query(store,[step],'2026-07-01','w',outputs=[dict(step='history',field='record')],limits=GraphLimits(max_hops=1))
    assert result['status']=='LIMIT_EXCEEDED' and not result['outputs']


def test_previous_selection_is_revalidated_through_actual_receipt_reviewer():
    from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
    from linkloom.ui.graph_investigation import GraphInvestigationService
    store,first,second,old,new,edges,records=fixture();extra=[]
    class Dynamic:
        def load_edges(self,workspace,*args):
            saved=store.load_edges(workspace);return GraphSnapshot(workspace,saved.edges,(),saved.nodes+tuple(extra),tuple(records))
    dynamic=Dynamic()
    step=dict(id='diff',operation='compare',input={'node':second.node_id},other=first.node_id,domain='decisions',as_of=None,previous_anchor=second.node_id,baseline_scope='workspace')
    plan=MeetingGraphPlan.from_dict(dict(steps=[step],outputs=[dict(step='diff',field='comparison')],as_of='2026-06-01'),nodes=dynamic.load_edges('w').nodes)
    class Planner:
        def plan(self,context):return dict(status='PLANNED',plan=plan)
    worker=GraphInvestigationService(lambda ws:dynamic,lambda ctx:Planner(),query_clock=lambda:datetime(2026,6,1,tzinfo=UTC))
    result=worker.run('Changes in the selected workspace','w');assert result['status']=='COMPLETED',result
    middle=Node('w','meeting','middle','Intervening meeting');extra.append(middle)
    records.append(dict(node_id=middle.node_id,kind='meeting',meeting_date='2026-04-15',pending_count=0,evidence=[]))
    assert worker.inspect(result['run_id'],'w')['status']=='BLOCKED'


def test_task_only_comparison_request_still_returns_both_comparison_domains():
    store,first,second,*_=fixture();ctx=GraphPlanningContext(first.label+' vs '+second.label,'w',store.load_edges('w').nodes,lambda:True)
    raw=emit([dict(id='diff',operation='compare',input={'node':first.node_id},other=second.node_id,domain='tasks',as_of='')],[dict(step='diff',field='comparison')],[entity(first),entity(second)],['task'],'compare')
    result=grounded(raw,ctx);assert result['status']=='PLANNED',result
    assert {s['domain'] for s in result['plan'].steps}=={'tasks','decisions'}


@pytest.mark.parametrize('timed',['2031-03-08T00:00:00+00:00','2031-03-08T18:00:00+00:00'])
def test_previous_order_does_not_assume_undated_time_is_midnight(timed):
    records={'anchor':dict(kind='meeting',meeting_date='2031-03-10'),
        'date-only':dict(kind='meeting',meeting_date='2031-03-08'),
        'timed':dict(kind='meeting',meeting_date='2031-03-08',meeting_time=timed)}
    result=nearest_previous_meeting(records,'anchor')
    assert result['status']=='AMBIGUOUS' and result['candidates']==['date-only','timed']
    records['later']=dict(kind='meeting',meeting_date='2031-03-09',meeting_time='2031-03-09T00:00:00+00:00')
    assert nearest_previous_meeting(records,'anchor')['candidates']==['later']


@pytest.mark.parametrize('previous',[True,False])
def test_comparison_time_gate_precedes_ambiguous_baseline_evidence(previous):
    store,first,second,old,new,edges,records=fixture();tie=Node('w','meeting','tie','Same day')
    records[0]['evidence']=[e.to_dict() for e in edges[0].evidence]
    records.append(dict(node_id=tie.node_id,kind='meeting',meeting_date=records[0]['meeting_date'],pending_count=0,evidence=records[0]['evidence']))
    class WithTie:
        def load_edges(self,workspace,*args):
            saved=store.load_edges(workspace);return GraphSnapshot(workspace,saved.edges,(),saved.nodes+(tie,),tuple(records))
    step=dict(id='diff',operation='compare',input={'node':second.node_id},other=first.node_id,domain='decisions',as_of=None)
    if previous:step.update(previous_anchor=second.node_id,baseline_scope='workspace')
    result=graph_query(WithTie(),[step],'2026-03-01','w',outputs=[dict(step='diff',field='comparison')])
    assert result['status']=='NO_VALID_RECORD_AT_TIME' and not result['outputs']
    row=result['steps'][0]
    assert row['evidence']==[] and row['output_nodes']==[] and 'baseline_verification' not in row
