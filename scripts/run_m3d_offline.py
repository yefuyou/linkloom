"""Integration D offline regression, full Product traces and real Chromium."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread
from uuid import uuid4

sys.path[:0] = ['src', '.', 'scripts']
from audit_m3d_budget import DOC, read, save, digest
from tests.m3d_support import CASES, combined_product, start_case, preparation, verify_case
from tests.m3p_support import product_http, cold_read


def regression():
    assert not (DOC/'evidence/regression.xml').exists(), 'REGRESSION_RECEIPT_EXISTS; use a new campaign output, never overwrite'
    workflow = Path('.github/workflows/offline-pr.yml').read_text(encoding='utf8')
    block = workflow.split('name: Observation, planning, proof, context, Runtime and HTTP contracts', 1)[1].split('- name: Explicit offline product entry', 1)[0]
    files = re.findall(r'tests/[^\s]+\.py', block)
    files += ['tests/unit/test_task_field_binding.py', 'tests/unit/test_frozen_rehearsal_ledger.py',
              'tests/unit/test_m3s1_planning_context.py', 'tests/integration/test_m3s1_ambiguous_product.py',
              'tests/unit/test_task_recall_protocol.py', 'tests/integration/test_m3d_combined.py']
    files = list(dict.fromkeys(files))
    save(DOC/'evidence/regression-selection.json', files)
    env = dict(os.environ, PYTHONPATH='src;.;tests/integration', PYTHONIOENCODING='utf8',
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', LINKLOOM_RETRIEVAL_MODE='bm25', GEMINI_API_KEY='', DEEPSEEK_API_KEY='')
    # Short, unique bases avoid MAX_PATH and never remove a previous run.
    temp = 'work/d-r-'+uuid4().hex[:6]
    return subprocess.call([sys.executable, '-m', 'pytest', '-q', '--tb=short', *files,
        '--basetemp='+temp, '--junitxml='+str(DOC/'evidence/regression.xml')], env=env)


def traces():
    for case in CASES:
        root = Path('work/m3d-offline')/case
        assert not root.exists(), 'TRACE_RUN_ALREADY_EXISTS'
        root.mkdir(parents=True)
        app, service, transport, stages = combined_product(root, case)
        try:
            with product_http(app, service) as base:
                ws, result = start_case(base, case)
                prep_root, prep = preparation(root)
                verify_case(case, result, prep_root, prep)
                count = len(transport.requests)
                code, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
                assert code == 200 and cold['result']['task_review'] == result['task_review']
                assert count == len(transport.requests)
                report = dict(mode='OFFLINE_SCRIPTED_TRANSPORT; REAL_HTTP_HOST_RUNTIME_REVIEWER',
                    case=case, input=CASES[case], run_id=result['agent_run_id'], workspace_id=ws,
                    result=result, preparation=prep, provider_free_cold=dict(code=code, body=cold),
                    provider_calls_actual=0, scripted_adapter_calls=count,
                    audited_stages={p.name:read(p) for p in prep_root.glob('*.json') if p.name!='preparation.json'})
                save(DOC/f'evidence/offline-{case}.json', report)
                print('OFFLINE', case, result['status'], 'planning', len(stages[1].requests), flush=True)
        finally:
            app.close()


def browser():
    from playwright.sync_api import sync_playwright
    from linkloom.ui.product import ProductApplication
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.ui.server import create_http_server
    from tests.integration.test_product_application_flow import _UnusedRunBackend
    root = Path('work/m3d-offline/C')
    receipt = read(DOC/'evidence/offline-C.json')
    run, ws = receipt['run_id'], receipt['workspace_id']
    journal, = (root/'work/investigations').glob('*/provider/budget.json')
    before = digest(journal)
    app = ProductApplication(database_path=root/'product.sqlite', extractor=None)
    server = create_http_server(_UnusedRunBackend(), product_app=app,
        investigation_service=ProductInvestigationService(app), host='127.0.0.1', port=0)
    Thread(target=server.serve_forever, daemon=True).start()
    report = dict(mode='REAL_CHROMIUM; OFFLINE_SAVED_RESULT; NO_PROVIDER_CONFIGURED',
        run_id=run, workspace_id=ws, actual_provider_calls=0)
    screenshots = Path('output/playwright/m3d-offline')
    screenshots.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            page = browser.new_page(viewport=dict(width=1440, height=1100))
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='domcontentloaded')
            page.locator('#source-create').wait_for()
            page.evaluate('([key,value])=>sessionStorage.setItem(key,value)', [f'linkloom:last-task:{ws}', run])
            page.locator('[data-screen="ask"]').click()
            page.locator('[data-action="reopen-task"]').click()
            page.locator('#ask-result').get_by_role('heading', name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            summaries = page.locator('#ask-result summary').filter(has_text='展开原始消息与字段证据')
            for i in range(summaries.count()):
                summaries.nth(i).click()
            report['fields_text'] = page.locator('#ask-result').inner_text()
            assert 'UNKNOWN' in report['fields_text'] and '缓存校核' in report['fields_text']
            page.screenshot(path=str(screenshots/'fields.png'), full_page=True)
            page.locator('#ask-result [data-action="inspect-source"]').first.click()
            page.locator('#source-update').wait_for()
            assert page.locator('#update-content').input_value() == CASES['C']['source']
            report['source_opened_exact'] = True
            page.screenshot(path=str(screenshots/'source.png'), full_page=True)
            page.reload(wait_until='domcontentloaded')
            page.locator('#source-create').wait_for()
            page.locator('[data-screen="ask"]').click()
            page.locator('[data-action="reopen-task"]').click()
            page.locator('#ask-result').get_by_role('heading', name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            report.update(browser=browser.version, browser_errors=errors, refresh_verified=True,
                journal_sha256_before=before, journal_sha256_after=digest(journal))
            assert not errors and before == digest(journal)
            browser.close()
    finally:
        server.shutdown(); server.server_close(); app.close()
        save(DOC/'evidence/offline-browser.json', report)
    print('REAL_CHROMIUM_OFFLINE_C_FIELDS_SOURCE_REFRESH_PASS', flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8')
    mode = sys.argv[1]
    if mode == 'regression':
        raise SystemExit(regression())
    os.environ.update(GEMINI_API_KEY='', DEEPSEEK_API_KEY='', PYTHONIOENCODING='utf8',
                      PYTHONPATH='src;.;tests/integration')
    if mode == 'traces':
        traces()
    elif mode == 'browser':
        browser()
    else:
        raise SystemExit('Choose regression, traces or browser')
