"""Ordinary HTTP Ask traverses a graph worker; no live provider or held-out data."""
import pytest
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.temporal_graph.planning import GraphQuestionPlanner
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.ui.graph_investigation import GraphInvestigationService
from tests.graph_eval_support import confirm_candidates
from tests.unit.test_temporal_graph_projection import fixture_public
from tests.unit.test_temporal_graph_planning import Model
from unified_investigation_fixture import UnifiedInvestigationFixture,RELATIONS,TWO
from unified_ask_fixture import request_json,poll_product_ask

@pytest.fixture
def graph_http(tmp_path,monkeypatch):
    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE','bm25')
    demo=UnifiedInvestigationFixture(tmp_path/'app.sqlite');demo.configure_agent()
    public=fixture_public();confirmed=confirm_candidates(public,demo.app.repository.database_path)
    workspace=confirmed['workspaces'][('DEV','original')];demo.app.repository.select_workspace(workspace)
    store=SQLiteGraphStore(demo.app.repository.database_path,authorized_workspace=workspace,
        authorize_source=lambda ws,s,v:demo.app._workspace_id()==ws)
    models=[]
    def factory(context):
        project=next(n for n in context.nodes if n.kind=='project')
        raw=dict(applicable=True,as_of='',refusal_reasons=[],steps=[
          dict(id='owner',input_node=project.node_id,input_step='',edge_type='owner_of',direction='in',as_of=''),
          dict(id='leader',input_node='',input_step='owner',edge_type='reports_to',direction='out',as_of='')],
          outputs=[dict(step='leader',field='node')])
        model=Model(raw);models.append(model)
        return GraphQuestionPlanner(model,model_id='fake',guard_id='http',request_guard=lambda r:
          ProviderRequestAuthorization.approve(r,guard_id='http',reservation_id='local',estimated_cost_upper_bound_usd=0))
    worker=GraphInvestigationService(lambda ws:store,factory)
    demo.server.unified_ask.graph_service=worker
    try:yield demo,workspace,worker,models
    finally:demo.stop()


def test_ordinary_ask_new_wording_reaches_model_graph_before_old_question_grammar(graph_http,monkeypatch):
    demo,workspace,worker,models=graph_http
    monkeypatch.setattr('linkloom.ui.unified_ask.plan_relation_investigation',lambda q:(_ for _ in ()).throw(AssertionError('old grammar called')))
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(
        query='联调这摊活找谁接、这位同事直接向哪位汇报？',workspace_id=workspace,as_of='2026-04-01'))
    result=body['result']
    assert status==200 and result['status']=='COMPLETED',body
    assert result['decided_by']=='graph' and result['source_investigation']['answer']=='bo'
    assert result['reviewer']['accepted'] and len(result['evidence'])==2
    assert len(models)==1 and len(models[0].calls)==1
    assert {f['step_id'] for f in result['source_investigation']['facts']}=={'owner','leader'}


def test_external_permission_and_explicit_scope_never_bypass_existing_admission(graph_http):
    demo,workspace,worker,models=graph_http
    worker.external_provider=True
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(query='找负责人',workspace_id=workspace))
    assert status==403 and models==[]
    worker.external_provider=False
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(query='找负责人',workspace_id='other'))
    assert status==409 and models==[]


def test_planning_failure_falls_back_but_query_unknown_never_does(graph_http):
    demo,workspace,worker,models=graph_http
    class Failed:
        def plan(self,context):return dict(status='OUTPUT_INVALID',plan=None)
    worker.planner_factory=lambda context:Failed()
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(query='无法识别的问法',workspace_id=workspace))
    assert len([r for r in worker.metrics.snapshot() if r['name']=='legacy_fallback'])==1
    worker.planner_factory=lambda context:GraphQuestionPlanner(Model(dict(applicable=True,as_of='',refusal_reasons=[],steps=[
      dict(id='s',input_node=next(n for n in context.nodes if n.kind=='project').node_id,input_step='',edge_type='owner_of',direction='in',as_of='')],
      outputs=[dict(step='s',field='node')])),model_id='fake',guard_id='local',request_guard=lambda r:
      ProviderRequestAuthorization.approve(r,guard_id='local',reservation_id='local',estimated_cost_upper_bound_usd=0))
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(query='负责人',workspace_id=workspace,as_of='2026-05-01'))
    assert status==200 and body['result']['graph_status']=='UNKNOWN'
    fallback=next(r for r in worker.metrics.snapshot() if r['name']=='legacy_fallback')
    assert fallback['count']==1


def test_reopening_graph_answer_rechecks_source_and_never_reuses_invalidated_answer(graph_http):
    demo,workspace,worker,models=graph_http
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(query='负责人及其上级',workspace_id=workspace,as_of='2026-04-01'))
    assert status==200 and body['result']['status']=='COMPLETED'
    run_id=body['result']['agent_run_id']
    reopened,status=demo.server.unified_ask.inspect(run_id,workspace)
    assert status==200 and reopened['source_investigation']['answer']=='bo'
    with demo.app.repository._connection:
        demo.app.repository._connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=?',(workspace,))
    reopened,status=demo.server.unified_ask.inspect(run_id,workspace)
    assert status==409 and reopened.get('source_investigation') is None
    assert len(models)==1


def test_legacy_fallback_inherits_graph_provider_request_budget(graph_http,tmp_path):
    from linkloom.ui.graph_investigation import _GRAPH_PROVIDER,current_graph_provider_session
    from linkloom.ui.task_provider import TaskProviderSession,TaskProviderLimits
    from linkloom.agents.model_adapter import ModelAction,ModelResponse,ModelTurnRequest,ModelGenerationOptions
    demo,workspace,worker,models=graph_http
    service=demo.server.unified_ask.investigation()
    limits=TaskProviderLimits(1,100000,10000,1,30,5,.001)
    session=TaskProviderSession(tmp_path/'shared-provider',limits,lambda:None)
    with service._lock:service._provider_sessions.append(session)
    class Inner:
        calls=0
        def complete(self,request):self.calls+=1;return ModelResponse(action=ModelAction.final('invalid decomposition'))
    inner=Inner()
    class FailingPlanner:
        def plan(self,context):
            _GRAPH_PROVIDER.set(session)
            request=ModelTurnRequest(run_id='g',turn_id='g',task_id='g',agent_id='g',sequence=1,
                user_input='public graph context',observation=None,available_tools=[],
                generation_options=ModelGenerationOptions(max_output_tokens=16))
            session.bind(inner,'graph-planning').complete(request)
            return dict(status='OUTPUT_INVALID',plan=None)
    worker.planner_factory=lambda context:FailingPlanner()
    question='Who is the direct leader of the owner of Bay?'
    status,body=request_json(demo.base_url,'/api/product/ask',method='POST',data=dict(query=question,workspace_id=workspace))
    assert status==202,body
    _,polled=poll_product_ask(demo,body['result']['agent_run_id'],workspace)
    result=polled['result']
    assert result['status']!='COMPLETED'
    assert session.requests==1 and inner.calls==1
    assert session.failure=='TASK_PROVIDER_BUDGET_EXHAUSTED'
    assert demo.relation_models[-1].call_count==0
    assert current_graph_provider_session() is None



def test_intent_review_mismatch_uses_legacy_fallback_without_publishing_graph(graph_http):
    from tests.unit.test_temporal_graph_planning import ReviewedModel
    demo,workspace,worker,models=graph_http;original=worker.planner_factory
    def factory(context):
        planner=original(context)
        planner.session.adapter=ReviewedModel(planner.session.adapter.output,dict(aligned=False,reason='MISALIGNED_ROOT'))
        planner.alignment_review=True
        return planner
    worker.planner_factory=factory
    result=worker.run('联调负责人向谁汇报？',workspace,as_of='2026-04-01')
    assert result==dict(status='FALLBACK',reason='GRAPH_PLAN_INTENT_REJECTED')
    counters=worker.metrics.snapshot()
    assert next(r for r in counters if r['name']=='legacy_fallback')['reason']=='GRAPH_PLAN_INTENT_REJECTED'
    assert not any(r['name']=='graph_planning_success' for r in counters)



def test_explicit_model_refusal_cannot_spoof_intent_failure_to_enter_legacy(graph_http):
    demo,workspace,worker,models=graph_http;original=worker.planner_factory
    def factory(context):
        planner=original(context)
        planner.session.adapter=Model(dict(applicable=False,steps=[],outputs=[],as_of='',refusal_reasons=['PLAN_INTENT_NOT_ALIGNED:MISALIGNED_ROOT']))
        planner.alignment_review=True
        return planner
    worker.planner_factory=factory
    result=worker.run('明确拒绝的请求',workspace,as_of='2026-04-01')
    assert result==dict(status='REFUSED',reason='GRAPH_PLANNER_REFUSED')
    assert not any(r['name'] in {'legacy_fallback','graph_planning_success'} for r in worker.metrics.snapshot())
