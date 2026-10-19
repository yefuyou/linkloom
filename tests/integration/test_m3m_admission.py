import pytest

from tests.m3m_support import CASES, matrix
from tests.m3m_support import context_for
from tests.m3i_support import product_case, finish


@pytest.fixture(scope='module')
def observed(tmp_path_factory):
    return {row['id']: row for row in matrix(tmp_path_factory.mktemp('m'))}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_frozen_multilingual_route_contract(case, observed):
    row = observed[case['id']]
    assert row['observed'] == case['expected'], row
    assert row['question_preserved'], row
    assert row['classifier_model_calls'] == 0
    if case['expected'] in {'CLARIFY', 'CAPABILITY', 'DETERMINISTIC'}:
        assert row['start_count'] == 0, row


@pytest.mark.parametrize('case_id', ['implicit_zh', 'time_mix', 'task_owner_mislead'])
def test_scoped_paraphrase_uses_existing_chain_without_classifier(tmp_path, case_id):
    app, service, ask, stages, workspace = product_case(tmp_path)
    query = next(c['query'] for c in CASES if c['id'] == case_id)
    try:
        result, status = ask.ask(dict(query=query, workspace_id=workspace,
            investigation_context=context_for(app, workspace, 'tasks')))
        assert status == 202, result
        result = finish(ask, workspace, result)
        assert result['status'] == 'PARTIAL' and result['reviewer']['accepted'], result
        assert result['preparation']['question'] == query
        assert result['preparation']['planning'][0]['plan']['goal'] == query
        assert [len(s.requests) for s in stages] == [2, 1, 1]
        assert result['metrics']['model_turns'] == 4
        assert result['route']['admission']['origin'] == 'USER_SCOPED'
        assert result['working_context_audit']['recovery']['status'] == 'complete'
        assert all(e['source_version'] and e['source_id'] for e in result['evidence'])
        assert not result['conclusions']
    finally:
        app.close()


def test_g_refusal_keeps_original_goal_and_never_starts_runtime(tmp_path):
    from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
    from linkloom.tools.contracts import ToolCall
    app, service, ask, stages, workspace = product_case(tmp_path)
    query = '请从这些原话证明一项还没有确认的公司正式授权。'
    def refuse(request):
        stages[1].requests.append(request.to_dict())
        return ModelResponse(action=ModelAction.tool(ToolCall('refuse', request.available_tools[0].tool_id,
            dict(steps=[], uncertainties=['正式授权不属于任务候选调查能力。'],
                 refusal_reasons=['UNSUPPORTED_AUTHORITY_REQUEST']))),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30))
    stages[1].complete = refuse
    try:
        result, _ = ask.ask(dict(query=query, workspace_id=workspace,
            investigation_context=context_for(app, workspace, 'tasks')))
        assert result['status'] == 'BLOCKED' and not service._jobs, result
        assert result['preparation']['question'] == query
        assert result['preparation']['planning'][0]['status'] == 'REFUSED'
        assert not stages[2].requests and not result['conclusions']
    finally:
        app.close()


def test_http_scope_choice_binds_host_snapshot_and_preserves_limits(tmp_path):
    from threading import Thread
    from time import monotonic, sleep
    from tests.integration.test_product_application_flow import _UnusedRunBackend, _client
    from linkloom.ui.server import create_http_server
    app, service, ask, stages, workspace = product_case(tmp_path)
    server = create_http_server(_UnusedRunBackend(), product_app=app, investigation_service=service, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        status, config = _client(base, '/api/product/ask/config')
        context = dict(domain='tasks', snapshot_hash=config['investigation_context']['snapshot_hash'])
        payload = dict(query='后面那条还能接上吗？', workspace_id=workspace,
            investigation_context=context, max_requests=9999, provider='arbitrary', tools=['write'])
        status, body = _client(base, '/api/product/ask', method='POST', data=payload)
        assert status == 202, body
        assert body['result']['route']['admission']['context'] == context
        # F and G run synchronously before the existing Runtime starts.
        assert [len(s.requests) for s in stages][:2] == [2, 1]
        assert service.task_models.planning_limits.max_provider_calls == 3
        run_id, result = body['result']['agent_run_id'], body['result']
        deadline = monotonic() + 20
        while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
            sleep(.04)
            _, polled = _client(base, f'/api/product/ask/runs/{run_id}?workspace_id={workspace}')
            result = polled['result']
        assert result['status'] == 'PARTIAL' and result['reviewer']['accepted'], result
        source = app.snapshot_investigation_sources(workspace)[1][0]
        app.repository.delete_source(workspace, source['source_id'])
        status, stale = _client(base, '/api/product/ask', method='POST', data=payload)
        assert status == 409 and stale['result']['error']['code'] == 'INVESTIGATION_CONTEXT_CHANGED'
        assert [len(s.requests) for s in stages][:2] == [2, 1]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        app.close()


def test_scoped_language_cannot_expand_frozen_live_canary(tmp_path, monkeypatch):
    from tests.m3l_support import offline_product
    p = offline_product(tmp_path, monkeypatch)
    workspace = p.host._workspaces['C1']
    try:
        result, _ = p.ask.ask(dict(query='后来那条还算接着前面的做吗？', workspace_id=workspace,
            investigation_context=context_for(p.app, workspace, 'tasks'), allow_external_provider=True))
        assert result['status'] == 'BLOCKED', result
        assert not p.sdk.calls and not p.service._jobs
        assert p.host.ledger.snapshot()['request_count'] == 0
    finally:
        p.app.close()
