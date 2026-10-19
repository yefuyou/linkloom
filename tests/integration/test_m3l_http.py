from threading import Thread
from time import monotonic, sleep

from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend, _client
from tests.m3l_support import offline_product


def test_normal_http_cannot_change_canary_provider_or_budget(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch)
    server = create_http_server(_UnusedRunBackend(), product_app=p.app,
                               investigation_service=p.service, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    workspace = p.host._workspaces['C1']
    try:
        status, payload = _client(base, '/api/product/ask', method='POST', data=dict(
            query=p.spec.question, workspace_id=workspace, provider_id='arbitrary',
            adapter='network', model_id='other-model', max_requests=999, max_cost_usd=999))
        assert status == 202, payload
        result = payload['result']
        deadline = monotonic() + 60
        while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
            sleep(.04)
            status, payload = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
            result = payload['result']
        assert result['status'] == 'PARTIAL' and result['reviewer']['accepted'], result
        assert {call['model'] for call in p.sdk.calls} == {p.spec.model_id}
        assert p.host.ledger.snapshot()['max_requests'] == 34
        assert p.host.ledger.snapshot()['max_total_cost_usd'] == 3
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        p.app.close()
