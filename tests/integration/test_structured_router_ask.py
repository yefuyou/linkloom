"""Real Ask/SQLite/evidence paths with scripted routing; no external Provider."""
import json
from datetime import UTC, datetime

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
from linkloom.ui.graph_investigation import GraphInvestigationService, product_graph_service
from linkloom.ui.product import ProductApplication
from linkloom.ui.router_provider import RouterProvider
from linkloom.ui.structured_router import PROMPT
from linkloom.ui.unified_ask import UnifiedAskService
from tests.unit.test_task_legacy_adapter import seed, close, preview
from unified_ask_fixture import UnifiedAskFixture, ATLAS


class ScriptedRouter:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
    def complete(self, request):
        self.requests.append(request)
        result = self.reply(json.loads(request.user_input.removeprefix(PROMPT)))
        return ModelResponse(action=ModelAction.final(json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result))


def output(**changes):
    return dict(dict(schema_version='router/v1', decision='ROUTE', intent='CURRENT', domain='TASK', operation='READ',
        candidate_ids=['c0'], mentions=[dict(text='联调报告',start=0,end=4)], time_ref_ids=[], time_role='NONE',reason_code='NONE'), **changes)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE', 'bm25')
    monkeypatch.setenv('LINKLOOM_GRAPH_QUERY', 'on')
    monkeypatch.setenv('LINKLOOM_LLM_ROUTER', 'off')


def test_named_confirmed_task_reaches_existing_graph_and_verified_answer(tmp_path, monkeypatch):
    path = tmp_path/'task.sqlite'
    fixture = seed(path)
    ws = fixture.workspace
    try:
        fixture.service.confirm(ws, fixture.observation.observation_id,
            expected_candidate_fingerprint=preview(fixture)['fingerprint'], reviewer='人工审核人',reason='确认夹具',
            now=datetime(2026,11,1,tzinfo=UTC))
    finally:
        close(fixture)
    app = ProductApplication(database_path=str(path), extractor=None); app.repository.select_workspace(ws)
    try:
        store_factory = product_graph_service(app, lambda: None).store_factory
        def planner(context):
            task = next(n for n in context.nodes if n.kind == 'task')
            plan = MeetingGraphPlan.from_dict(dict(steps=[dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)],
                outputs=[dict(step='task',field='owner')],as_of=None),nodes=context.nodes)
            return type('FakeGraphPlanner', (), {'plan':lambda self, context:dict(status='PLANNED',plan=plan)})()
        worker = GraphInvestigationService(store_factory, planner)
        model = ScriptedRouter(lambda data: output(candidate_ids=[next(c['id'] for c in data['candidates'] if c['kind']=='task')]))
        ask = UnifiedAskService(app, lambda: None, graph_service=worker, router_provider=RouterProvider(model), slot_embedder=None)
        monkeypatch.setattr('linkloom.ui.unified_ask.admission_restriction', lambda _:pytest.fail('legacy wording gate ran'))
        result,status = ask.ask(dict(query='联调报告现在谁负责',workspace_id=ws))
        assert status == 200 and result['router']['domain'] == 'TASK', result
        assert result['router']['decision']=='ROUTE' and result['status']=='COMPLETED', result
        assert '李明' in result['source_investigation']['answer']
        assert result['reviewer']['accepted'] and result['evidence'] and len(model.requests)==1
        reopened,code = ask.inspect(result['agent_run_id'],ws)
        assert code==200 and reopened['router']['domain']=='TASK' and reopened['evidence']==result['evidence']
        with app.repository._connection:
            app.repository._connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=?',(ws,))
        reopened,code = ask.inspect(result['agent_run_id'],ws)
        assert code==409 and not reopened['evidence'] and len(model.requests)==1
    finally:
        app.close()


@pytest.fixture
def decisions(tmp_path):
    demo = UnifiedAskFixture(tmp_path/'decisions.sqlite')
    demo.app.repository.select_workspace(demo.workspace_ids[ATLAS])
    try:
        yield demo
    finally:
        demo.stop()


def test_bad_output_falls_back_once_and_keeps_legacy_reviewed_answer(decisions):
    model = ScriptedRouter(lambda _: 'not JSON')
    ask = UnifiedAskService(decisions.app, lambda:None, graph_service=False, router_provider=RouterProvider(model), slot_embedder=None)
    query = dict(query='What supplier is Launch currently using?',workspace_id=decisions.app._workspace_id())
    off = UnifiedAskService(decisions.app, lambda:None, graph_service=False, slot_embedder=None)
    baseline,code = off.ask(query)
    result,status = ask.ask(query)
    assert status == code == 200 and baseline['status']=='COMPLETED', baseline
    assert result['conclusions'] == baseline['conclusions'] and result['evidence'] == baseline['evidence']
    assert result['router']==dict(source='rule',fallback_reason='ROUTER_SCHEMA_INVALID',attempted=True)
    assert len(model.requests)==1


def test_host_denies_foreign_workspace_before_candidate_or_model(decisions):
    model = ScriptedRouter(lambda _:pytest.fail('foreign query reached provider'))
    ask = UnifiedAskService(decisions.app, lambda:None, graph_service=False, router_provider=RouterProvider(model), slot_embedder=None)
    result,status=ask.ask(dict(query='What supplier is Launch currently using?',workspace_id='foreign'))
    assert status==409 and result['router']['reason_code']=='WORKSPACE_DENIED' and result['router']['reason_class']=='SAFETY'
    assert not result['evidence'] and not model.requests


def test_write_reason_is_terminal_and_not_reinterpreted_by_legacy(decisions,monkeypatch):
    model = ScriptedRouter(lambda _:output(decision='REFUSE',intent='OTHER',domain='NONE',operation='WRITE_REQUEST',
        candidate_ids=[],mentions=[],reason_code='WRITE_NOT_ALLOWED'))
    ask = UnifiedAskService(decisions.app, lambda:None, graph_service=False, router_provider=RouterProvider(model), slot_embedder=None)
    monkeypatch.setattr('linkloom.ui.unified_ask.admission_restriction', lambda _:pytest.fail('safety refusal retried'))
    result,status = ask.ask(dict(query='请修改联调报告的负责人',workspace_id=decisions.app._workspace_id()))
    assert status==200 and result['router']['decision']=='REFUSE' and result['error']['code']=='WRITE_NOT_ALLOWED'
    assert result['router']['reason_class']=='SAFETY' and not result['conclusions']


def test_source_snapshot_change_after_model_cannot_fall_back(decisions):
    ws=decisions.app._workspace_id()
    def reply(_):
        with decisions.app.repository._connection:
            decisions.app.repository._connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=?',(ws,))
        return 'malformed'
    model=ScriptedRouter(reply)
    ask=UnifiedAskService(decisions.app,lambda:None,graph_service=False,router_provider=RouterProvider(model),slot_embedder=None)
    result,status=ask.ask(dict(query='What supplier is Launch currently using?',workspace_id=ws))
    assert status==409 and result['router']['reason_code']=='EVIDENCE_INVALID' and not result['evidence']
    assert len(model.requests)==1


def test_no_existing_executor_returns_unsupported_without_guessing(decisions):
    model=ScriptedRouter(lambda _:output(intent='LIST',candidate_ids=[],mentions=[]))
    ask=UnifiedAskService(decisions.app,lambda:None,graph_service=False,router_provider=RouterProvider(model),slot_embedder=None)
    result,status=ask.ask(dict(query='联调报告现在谁负责',workspace_id=decisions.app._workspace_id()))
    assert status==200 and result['router']['reason_code']=='UNSUPPORTED_QUERY' and not result['evidence']


def test_router_external_transport_requires_this_question_consent(decisions):
    owner=decisions.configure_agent()
    model=ScriptedRouter(lambda _:pytest.fail('unconsented provider call'))
    ask=UnifiedAskService(decisions.app,lambda:owner,graph_service=False,
                          router_provider=RouterProvider(model,external_provider=True),slot_embedder=None)
    result,status=ask.ask(dict(query='What supplier is Launch currently using?',workspace_id=decisions.app._workspace_id()))
    assert status==200 and result['router']['fallback_reason']=='PROVIDER_PERMISSION_REQUIRED'
    assert result['status']=='COMPLETED' and not model.requests



def test_authorized_router_has_one_bounded_send_and_verified_decision(decisions):
    owner=decisions.configure_agent()
    def reply(data):
        supplier=next(c for c in data['candidates'] if 'uses supplier' in c['label'])
        start=data['question'].index('supplier')
        return output(domain='DECISION',candidate_ids=[supplier['id']],
                      mentions=[dict(text='supplier',start=start,end=start+8)])
    model=ScriptedRouter(reply)
    ask=UnifiedAskService(decisions.app,lambda:owner,graph_service=False,
                          router_provider=RouterProvider(model,external_provider=True),slot_embedder=None)
    result,status=ask.ask(dict(query='What supplier is Launch currently using?',
                              workspace_id=decisions.app._workspace_id(),allow_external_provider=True))
    assert status==200 and result['status']=='COMPLETED' and result['router']['domain']=='DECISION', result
    assert result['conclusions'] and result['evidence'] and result['receipts'] and len(model.requests)==1
    session=owner._provider_sessions[-1]
    assert session.requests==1 and session.limits.max_requests==1 and session.failure is None
    assert session.attempts[0]['stage']=='ask-router' and session.attempts[0]['outcome']=='SUCCEEDED'
