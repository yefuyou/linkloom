from threading import Thread
from time import monotonic, sleep

from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend, _client
from tests.m3i_support import product_case, QUESTION
from tests.integration.unified_ask_fixture import UnifiedAskFixture, ATLAS, SUBJECT, RELATION, CURRENT_AS_OF
from linkloom.ui.task_investigation import TaskInvestigationModels


def test_existing_product_http_route_uses_the_real_task_chain(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    server = create_http_server(_UnusedRunBackend(), product_app=app, investigation_service=service, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        status, payload = _client(base, '/api/product/ask', method='POST', data=dict(query=QUESTION, workspace_id=workspace))
        assert status == 202, payload
        result = payload['result']
        deadline = monotonic() + 20
        while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
            sleep(.04)
            status, payload = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
            result = payload['result']
        assert status == 200 and result['status'] == 'PARTIAL', result
        assert result['reviewer']['accepted'] and len(result['evidence']) == 2
        # The browser launch mode uses this same service. A second question
        # must start a new bounded session rather than exhaust the fixture.
        status, payload = _client(base, '/api/product/ask', method='POST', data=dict(query=QUESTION, workspace_id=workspace))
        assert status == 202, payload
        deadline = monotonic() + 20
        second = payload['result']
        while second['status'] == 'INVESTIGATING' and monotonic() < deadline:
            sleep(.04)
            status, payload = _client(base, f"/api/product/ask/runs/{second['agent_run_id']}?workspace_id={workspace}")
            second = payload['result']
        assert second['status'] == 'PARTIAL' and second['agent_run_id'] != result['agent_run_id'], second
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_existing_deterministic_lookup_skips_all_task_stage_adapters(tmp_path):
    fixture = UnifiedAskFixture(tmp_path / 'fast-path.sqlite')
    class Forbidden:
        def complete(self, request):
            raise AssertionError('Deterministic lookup must not call any F/G/semantic stage.')
    try:
        service = fixture.configure_agent()
        service.task_models = TaskInvestigationModels(Forbidden(), Forbidden(), Forbidden())
        status, payload = _client(fixture.base_url, '/api/product/ask', method='POST', data=dict(
            query='What supplier are we currently using?', workspace_id=fixture.workspace_ids[ATLAS],
            subject=SUBJECT, relation=RELATION, as_of=CURRENT_AS_OF))
        result = payload['result']
        assert status == 200 and result['path'] == 'DETERMINISTIC' and result['status'] == 'COMPLETED'
        assert result['conclusions'][0]['value'] == 'Wrenwell'
        assert fixture.transports == [] and not service._jobs
    finally:
        fixture.stop()
