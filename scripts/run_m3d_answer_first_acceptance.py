"""Fresh synthetic Product HTTP/Chromium evidence; no network Provider transport.

Run from this checkout with PYTHONPATH=src;.;tests/integration and PYTHONUTF8=1.
Requires the existing Python Playwright installation and local Chrome channel.
Each campaign requires new output/root paths; historical receipts are preserved.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

from playwright.sync_api import sync_playwright

from linkloom.ui.investigation import ProductInvestigationService
from tests.integration.test_product_application_flow import _client
from tests.m3d_support import CASES, combined_product, preparation, verify_case
from tests.m3p_support import QUESTION, cold_read, product_case, product_http, revision_product
from tests.task_answer_browser_server import conflict_product

DOC = Path('docs/requirements/m3d-answer-first-integration/evidence')
SHOTS = Path('output/playwright/m3d-answer-first')
ROOT = Path('work/af-browser')


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def request(base, path, method='GET', data=None):
    code, body = _client(base, path, method=method, data=data)
    assert code < 400, (code, body)
    return body


def sources_exact(base, result):
    checked = []
    for observation in result['task_review']['observations']:
        span = observation['provenance']
        evidence = next(e for e in result['evidence'] if e['evidence_ref'] == span['evidence_ref'])
        source = request(base, '/api/sources/' + evidence['source_id'])['source']
        content = source['content']
        assert source['source_version'] == span['artifact_version_id'] == evidence['source_version']
        assert source['content_sha256'] == span['content_hash'] == sha256(content.encode('utf8')).hexdigest()
        assert content[span['char_start']:span['char_end']] == span['quote']
        assert sha256(span['quote'].encode('utf8')).hexdigest() == span['quote_sha256']
        for field in observation['field_evidence']:
            assert content[field['char_start']:field['char_end']] == field['quote'], field
        checked.append(dict(observation_id=observation['observation_id'], source=source,
                            provenance=span, field_evidence=observation['field_evidence']))
    return checked


def screenshot(page, name):
    page.locator('#ask-result').scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS / (name + '.png')))


def open_ask(page, base):
    page.goto(base, wait_until='domcontentloaded')
    page.locator('#source-create').wait_for()
    page.locator('[data-screen="ask"]').click()
    page.locator('#ask-form').wait_for()


def observe(page, report):
    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
    page.on('console', lambda message: report['console'].append(
        dict(type=message.type, text=message.text)))

    def response_received(response):
        path = response.url.split('/api/', 1)
        if len(path) != 2:
            return
        body = response.json()
        # Intermediate polling carries no conclusion; keep only its count.
        if body.get('result', {}).get('status') == 'INVESTIGATING' and response.request.method == 'GET':
            report['intermediate_polls'] += 1
            return
        report['http'].append(dict(path='/api/' + path[1], method=response.request.method,
                                   status=response.status, body=body))
    page.on('response', response_received)


def reopen(page):
    with page.expect_response(lambda r: '/api/product/ask/runs/' in r.url) as response:
        page.locator('[data-action="reopen-task"]').click()
    body = response.value.json()
    if body.get('result', {}).get('status') == 'BLOCKED':
        page.get_by_role('heading', name='任务调查已停止').wait_for()
    else:
        page.locator('.task-answer').wait_for()
    return response.value.status, body


def assert_blocked(page, body, old):
    result = body['result']
    assert result['status'] == 'BLOCKED' and not result['evidence'] and not result.get('task_review')
    page.locator('#ask-result summary').click()
    visible = page.locator('#ask-result').inner_text()
    assert '旧任务详情已隐藏' in visible
    for observation in old['task_review']['observations']:
        assert observation['description'] not in visible
        assert observation['provenance']['quote'] not in visible
    assert '模型倾向支持' not in visible


@contextmanager
def cold_host(app):
    # No model factory, task models, external Provider or transport is configured.
    with product_http(app, ProductInvestigationService(app)) as base:
        yield base


def check_cold_browser(browser, app, run_id, ws, label, report):
    with cold_host(app) as base:
        page = browser.new_page(viewport=dict(width=1440, height=1000))
        observe(page, report)
        open_ask(page, base)
        config = request(base, '/api/product/ask/config')
        assert not config['agent']['available']
        page.evaluate('([k,v])=>sessionStorage.setItem(k,v)', [f'linkloom:last-task:{ws}', run_id])
        page.reload(wait_until='domcontentloaded')
        page.locator('#source-create').wait_for()
        page.locator('[data-screen="ask"]').click()
        assert not page.locator('.task-answer').count()
        code, body = reopen(page)
        assert code == 200 and body['result']['status'] == 'PARTIAL'
        screenshot(page, label + '-provider-free-cold')
        page.close()


def campaign(browser, label):
    root = ROOT / label
    root.mkdir(parents=True, exist_ok=False)
    combined = label in CASES
    if combined:
        app, service, transport, stages = combined_product(root, label)
    elif label == 'model-conflict':
        app, service, _, stages, _ = conflict_product(root)
    elif label == 'p4':
        app, service, stages, *_ = revision_product(root)
    else:
        app, service, _, stages, _ = product_case(root, missing=label == 'unknown', conflict=label == 'conflict-gap')
    report = dict(label=label, mode='REAL_HTTP_CHROMIUM_SCRIPTED_TRANSPORT', paid_requests=0,
                  http=[], console=[], page_errors=[], intermediate_polls=0)
    try:
        with product_http(app, service) as base:
            if combined:
                ws = request(base, '/api/workspaces', 'POST', dict(name='Answer-first Integration ' + label))['workspace']['workspace_id']
                request(base, '/api/workspaces/current', 'POST', dict(workspace_id=ws))
                request(base, '/api/sources/import', 'POST', dict(name='combined-' + label + '.txt', content=CASES[label]['source']))
            else:
                ws = request(base, '/api/workspaces/current')['workspace']['workspace_id']
            page = browser.new_page(viewport=dict(width=1440, height=1000))
            observe(page, report)
            open_ask(page, base)
            page.locator('#ask-query').fill(CASES[label]['question'] if combined else QUESTION)
            page.locator('#ask-domain').select_option('tasks')
            if combined:
                # Exercise the real permission boundary before using the scripted transport.
                with page.expect_response(lambda r: r.url.endswith('/api/product/ask')) as denied:
                    page.locator('#ask-submit-button').click()
                assert denied.value.status == 403 and not transport.requests
                page.locator('#ask-external').check()
            page.locator('#ask-submit-button').click()
            page.locator('.task-answer').wait_for(timeout=60000)
            run_id = page.evaluate('(ws)=>sessionStorage.getItem(`linkloom:last-task:${ws}`)', ws)
            result = request(base, f'/api/product/ask/runs/{run_id}?workspace_id={ws}')['result']
            report.update(result=result, source_checks=sources_exact(base, result))
            visible = page.locator('#ask-result').inner_text()
            assert '尚未确认。本次调查不授予业务批准' in visible
            assert 'SUPPORTED' not in visible and 'EVIDENCE_VALID' not in visible
            assert not page.locator('.task-investigation-details').get_attribute('open')
            assert page.locator('body').evaluate('(e)=>e.scrollWidth<=innerWidth')
            screenshot(page, label + '-desktop')
            if combined:
                prep_root, prep = preparation(root)
                verify_case(label, result, prep_root, prep)
                report['preparation'] = prep
                report['audited_stages'] = {p.name: json.loads(p.read_text(encoding='utf8'))
                                            for p in prep_root.glob('*.json') if p.name != 'preparation.json'}
                assert stages[0].requests and not stages[2].requests
                if label == 'A':
                    assert '乔雨' in visible and '2037-08-19' in visible
                else:
                    card = page.locator('[data-task-observation]').inner_text()
                    assert card.count('尚未确定') >= 2
            elif label == 'unknown':
                assert result['status'] == 'UNKNOWN' and '不能据此断言任务不存在' in visible
            elif label == 'model-conflict':
                assert '模型报告证据冲突' in visible and '模型倾向支持' not in visible
                assert result['task_review']['proposals'][0]['evidence_status'] == 'EVIDENCE_VALID'
            elif label == 'conflict-gap':
                assert '模型无法判断' in visible and '日期存在冲突' in visible
            elif label == 'p4':
                assert '日历日差值' in visible and '不能确认当前正式生效的计划' in visible
            else:
                assert '模型倾向支持，尚未确认' in visible
            if label in ('A', 'partial'):
                card = page.locator('[data-task-observation]').first
                card.locator('.ask-source-details summary').click()
                assert 'TASK_DEADLINE' in card.inner_text()
                screenshot(page, label + '-expanded-evidence')
                card.locator('.ask-source-details summary').click()
                page.locator('#ask-language').select_option('en')
                assert 'Unconfirmed. This investigation does not grant approval.' in page.locator('#ask-result').inner_text()
                page.locator('#ask-language').select_option('zh')
                page.set_viewport_size(dict(width=390, height=844))
                assert page.locator('body').evaluate('(e)=>e.scrollWidth<=innerWidth')
                screenshot(page, label + '-mobile')
                page.set_viewport_size(dict(width=1440, height=1000))
                page.locator('[data-action="inspect-source"]').first.click()
                page.locator('#update-content').wait_for()
                assert page.locator('#update-content').input_value() == report['source_checks'][0]['source']['content']
                page.screenshot(path=str(SHOTS / (label + '-original-source.png')))
                page.locator('[data-screen="ask"]').click()
                assert reopen(page)[1]['result']['status'] == 'PARTIAL'
            if label == 'C':
                calls_before = len(transport.requests)
                journal, = (root / 'work/investigations').glob('*/provider/budget.json')
                journal_before = sha256(journal.read_bytes()).hexdigest()
                check_cold_browser(browser, app, run_id, ws, label, report)
                assert calls_before == len(transport.requests)
                assert journal_before == sha256(journal.read_bytes()).hexdigest()
                report['cold_provider_journal_unchanged'] = True
            if label in ('A', 'B', 'C'):
                count = len(transport.requests)
                code, cold = cold_read(app.repository.database_path, run_id, ws)
                assert code == 200 and cold['result']['task_review'] == result['task_review']
                assert count == len(transport.requests)
                report['provider_free_read'] = dict(status=code, result=cold['result'], scripted_calls_unchanged=count)
            if label in ('A', 'C'):
                page.reload(wait_until='domcontentloaded')
                page.locator('#source-create').wait_for()
                page.locator('[data-screen="ask"]').click()
                assert not page.locator('.task-answer').count()
                assert result['task_review']['observations'][0]['description'] not in page.locator('#ask-result').inner_text()
                assert reopen(page)[1]['result']['status'] == 'PARTIAL'
                if label == 'A':
                    source = report['source_checks'][0]['source']
                    update = request(base, '/api/sources/' + source['source_id'], 'PUT',
                                     dict(name=source['name'], content=source['content'] + '\nSynthetic source update.'))
                    report['source_update'] = update
                else:
                    with app.repository._lock, app.repository._connection:
                        app.repository._connection.execute(
                            'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                            (ws, result['evidence'][0]['evidence_ref']))
                code, body = reopen(page)
                assert code == 200
                assert_blocked(page, body, result)
                screenshot(page, label + '-blocked')
                report['blocked_read'] = body
                cold_code, cold = cold_read(app.repository.database_path, run_id, ws)
                assert cold_code == 200 and cold['result']['status'] == 'BLOCKED'
                assert not cold['result']['evidence'] and not cold['result'].get('task_review')
                report['blocked_cold_read'] = cold
                other = request(base, '/api/workspaces', 'POST', dict(name='Unrelated synthetic workspace'))['workspace']['workspace_id']
                page.reload(wait_until='domcontentloaded')
                page.locator('#workspace-picker').wait_for()
                page.locator('#workspace-picker').select_option(other)
                page.locator('#source-create').wait_for()
                page.locator('[data-screen="ask"]').click()
                assert page.locator('[data-action="reopen-task"]').count() == 0
                code, body = _client(base, f'/api/product/ask/runs/{run_id}?workspace_id={ws}')
                assert code == 409, body
                report['workspace_denial'] = dict(status=code, body=body)
                assert result['task_review']['observations'][0]['description'] not in page.locator('#page').inner_text()
                screenshot(page, label + '-workspace-isolation')
            assert not report['page_errors'], report['page_errors']
            page.close()
            report['passed'] = True
            print('PASS', label, result['status'], flush=True)
    finally:
        app.close()
        save(DOC / ('browser-' + label + '.json'), report)


def main():
    os.environ.update(GEMINI_API_KEY='', DEEPSEEK_API_KEY='', PYTHONUTF8='1')
    SHOTS.mkdir(parents=True, exist_ok=True)
    summary = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   base='33390e2eeaad71d215e279b2f724cae672ea4f80', paid_requests=0)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        summary['browser_version'] = browser.version
        try:
            for label in ('A', 'B', 'C', 'partial', 'unknown', 'model-conflict', 'conflict-gap', 'p4'):
                campaign(browser, label)
        finally:
            browser.close()
    summary['passed_cases'] = 8
    save(DOC / 'chromium-summary.json', summary)


if __name__ == '__main__':
    main()
