"""The answer-first assets use unchanged, validated task reads."""
from urllib.request import urlopen

import pytest

from tests.integration.test_product_application_flow import _client
from tests.m3p_support import http_ask, product_case, product_http
from tests.task_answer_browser_server import conflict_product


@pytest.mark.parametrize('case', ['partial', 'conflict', 'model-conflict', 'unknown'])
def test_task_answer_assets_and_existing_result_contract(tmp_path, case):
    app, service, _, stages, workspace = (conflict_product(tmp_path) if case == 'model-conflict'
        else product_case(tmp_path, conflict=case == 'conflict', missing=case == 'unknown'))
    try:
        with product_http(app, service) as base:
            result = http_ask(base, workspace)
            counts = [len(stage.requests) for stage in stages]
            with urlopen(base + '/') as response:
                html = response.read().decode('utf8')
                assert html.index('/product-task-result.js') < html.index('/product.js')
            with urlopen(base + '/product-task-result.js') as response:
                assert response.status == 200
                assert 'javascript' in response.headers['Content-Type']
                assert "script-src 'self'" in response.headers['Content-Security-Policy']
                assert 'renderTaskAnswer' in response.read().decode('utf8')
            code, saved = _client(base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
            assert code == 200
            assert saved['result']['task_review'] == result['task_review']
            assert [len(stage.requests) for stage in stages] == counts
            assert not result['conclusions']
            if case == 'unknown':
                assert result['status'] == 'UNKNOWN'
                assert not result['task_review']['observations']
            elif case == 'conflict':
                # The existing scripted fixture reports UNKNOWN with a conflict
                # gap; the UI must not upgrade that to a CONFLICT verdict.
                assert result['task_review']['proposals'][0]['proposed_verdict'] == 'UNKNOWN'
                assert '日期存在冲突' in result['task_review']['proposals'][0]['missing_evidence']
                assert result['status'] == 'PARTIAL'  # Proposal conflict is not a new global status.
            elif case == 'model-conflict':
                proposal = result['task_review']['proposals'][0]
                assert proposal['proposed_verdict'] == 'CONFLICT'
                assert proposal['evidence_status'] == 'EVIDENCE_VALID'
                assert proposal['counter_evidence'] and not proposal['publishable']
                assert result['status'] == 'PARTIAL'
            else:
                assert result['status'] == 'PARTIAL'
                assert len(result['task_review']['observations']) == 2
    finally:
        app.close()
