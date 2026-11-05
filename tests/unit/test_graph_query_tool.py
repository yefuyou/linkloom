from copy import deepcopy
from dataclasses import replace
from datetime import UTC,datetime
import pytest
from linkloom.tools.graph import register_graph_query
from linkloom.tools.contracts import ToolCall
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.graph import register_graph_query
from linkloom.ui.graph_investigation import _policy,GraphInvestigationService
from linkloom.agents.graph_result import review_graph_result
from linkloom.temporal_graph.models import GraphPlan
from tests.unit.test_temporal_graph_query import Store,node,edge,step


def runtime_case():
    a,b=node('person','A'),node('person','B');store=Store([edge(a,b)])
    rows=[step('s',{'node':a.node_id})];args=dict(workspace='w',as_of='2026-04-01',subgoals=rows,outputs=[dict(step='s',field='node')])
    registry=ToolRegistry();register_graph_query(registry,store,'w',query_time=datetime(2026,4,1,tzinfo=UTC))
    runtime=ToolRuntime(registry); call=ToolCall('call','graph_query',args,run_id='run',task_id='task',agent_id='graph-worker',sequence=1)
    return store,runtime,call


def test_independent_tool_records_identity_and_reviewer_rechecks_current_sources():
    store,runtime,call=runtime_case();result=runtime.execute(call,_policy('graph-worker',('graph_query',),1))
    assert result.status=='ok',result
    record=runtime.ledger.get('call')
    assert review_graph_result(result.value,record,store,run_id='run',task_id='task')['status']=='pass'
    tampered=deepcopy(result.value);tampered['steps'][0]['output_nodes'][0]['label']='invented'
    assert review_graph_result(tampered,record,store,run_id='run',task_id='task')['status']=='fail'
    assert review_graph_result(result.value,record,store,run_id='other',task_id='task')['status']=='fail'
    store.edges=()
    assert review_graph_result(result.value,record,store,run_id='run',task_id='task')['status']=='fail'


def test_tool_rejects_extra_acl_sql_and_missing_host_identity():
    _,runtime,call=runtime_case()
    result=runtime.execute(replace(call,arguments={**call.arguments,'sql':'select * from private'}),_policy('graph-worker',('graph_query',),1))
    assert result.status=='error'
    _,runtime,call=runtime_case()
    result=runtime.execute(replace(call,run_id=None),_policy('graph-worker',('graph_query',),1))
    assert result.status=='error'


def test_worker_uses_actual_tool_and_reviewer_and_minimal_metrics():
    store,_,call=runtime_case()
    plan=GraphPlan.from_dict(dict(steps=call.arguments['subgoals'],outputs=call.arguments['outputs'],as_of=call.arguments['as_of']),nodes=store.load_edges('w').nodes)
    class Planner:
        def plan(self,context):return dict(status='PLANNED',plan=plan)
    service=GraphInvestigationService(lambda ws:store,lambda context:Planner())
    result=service.run('查询上级','w',as_of='2026-04-01')
    assert result['status']=='COMPLETED',result
    assert result['reviewer']['accepted'] and result['source_investigation']['answer']=='B'
    assert {r['name'] for r in service.metrics.snapshot()}=={'graph_planning_attempt','graph_planning_success','graph_query_status'}
    service.external_provider=True
    assert service.run('查询上级','w')['status']=='UNAUTHORIZED'


def test_authorization_change_during_planning_cannot_fall_back():
    from tests.unit.test_temporal_graph_planning import Model,planner,output
    from linkloom.temporal_graph.models import GraphSnapshot
    a,b=node('person','A'),node('person','B');raw=Store([edge(a,b)]);current=[True]
    class ChangingStore:
        def load_edges(self,workspace,as_of=None,edge_types=None):
            return raw.load_edges(workspace) if current[0] else GraphSnapshot(workspace,())
    def factory(ctx):
        args=dict(applicable=True,as_of='',refusal_reasons=[],steps=[dict(id='s',input_node=a.node_id,input_step='',edge_type='reports_to',direction='out',as_of='')],outputs=[dict(step='s',field='node')])
        return planner(Model(args,lambda:current.__setitem__(0,False)))
    service=GraphInvestigationService(lambda ws:ChangingStore(),factory)
    result=service.run('查询上级','w',as_of='2026-04-01')
    assert result['status']=='UNAUTHORIZED'
    assert not any(r['name']=='legacy_fallback' for r in service.metrics.snapshot())


def test_graph_planner_cannot_create_provider_session_after_host_stop():
    from types import SimpleNamespace
    from threading import Lock
    from linkloom.ui.lifecycle import BackgroundTasks
    from linkloom.ui.graph_investigation import product_graph_service
    starts=BackgroundTasks();starts.stop_accepting();created=[]
    investigation=SimpleNamespace(_starts=starts,_provider_sessions=[],_lock=Lock(),
        graph_planner_factory=lambda context:created.append(context))
    app=SimpleNamespace(repository=SimpleNamespace(database_path='unused'))
    service=product_graph_service(app,lambda:investigation)
    with pytest.raises(PermissionError,match='SHUTTING_DOWN'):service.planner_factory(object())
    assert created==[] and investigation._provider_sessions==[]
