"""F v3 + G replan + real Host/Runtime/HTTP; network is explicitly scripted."""
import pytest

from tests.m3d_support import combined_product, start_case, preparation, verify_case
from tests.m3p_support import product_http, cold_read
from tests.integration.test_product_application_flow import _client
from tests.integration.test_m3q_provider_host import loopback_only


@pytest.mark.parametrize('case', ['A', 'B', 'C'])
def test_combined_product_and_provider_free_cold_read(tmp_path, case):
    app, service, transport, stages = combined_product(tmp_path, case)
    try:
        with product_http(app, service) as base:
            ws, result = start_case(base, case)
            prep_root, prep = preparation(tmp_path)
            verify_case(case, result, prep_root, prep)
            assert len(stages[0].requests) == 1 and not stages[2].requests
            count = len(transport.requests)
            code, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
            assert code == 200 and cold['result']['status'] == 'PARTIAL'
            assert cold['result']['task_review']['observations'] == result['task_review']['observations']
            assert count == len(transport.requests)
    finally:
        app.close()


@pytest.mark.parametrize('mutation', ['source_update', 'evidence_revoke', 'workspace'])
def test_combined_replanned_result_never_leaks_stale_evidence(tmp_path, mutation):
    app, service, transport, _ = combined_product(tmp_path, 'C')
    try:
        with product_http(app, service) as base:
            ws, result = start_case(base, 'C')
            verify_case('C', result, *preparation(tmp_path))
            if mutation == 'source_update':
                source, = app.snapshot_investigation_sources(ws)[1]
                code, body = _client(base, '/api/sources/'+source['source_id'], method='PUT',
                    data=dict(name=source['name'], content=source['content']+'\nSynthetic revision.'))
                assert code == 202, body
            elif mutation == 'evidence_revoke':
                with app.repository._lock, app.repository._connection:
                    app.repository._connection.execute(
                        'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                        (ws, result['evidence'][0]['evidence_ref']))
            else:
                other = app.repository.create_workspace('Unrelated Integration D workspace')
                app.repository.select_workspace(other['workspace_id'])
            count = len(transport.requests)
            path = f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}"
            hot_code, hot = _client(base, path)
            cold_code, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
            for code, body in ((hot_code, hot), (cold_code, cold)):
                if mutation == 'workspace':
                    assert code == 409, body
                else:
                    assert code == 200 and body['result']['status'] == 'BLOCKED', body
                    assert not body['result']['evidence'] and not body['result']['trace']
                    assert not body['result'].get('task_review')
            assert count == len(transport.requests)
    finally:
        app.close()
