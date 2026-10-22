"""Reproduce and verify pending revalidation against real synthetic Product HTTP."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests.integration.test_product_application_flow import _client
from tests.m3d_support import combined_product, start_case, preparation, verify_case
from tests.m3p_support import product_http


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--campaign', required=True)
    args = parser.parse_args()
    os.environ.update(GEMINI_API_KEY='', DEEPSEEK_API_KEY='')
    doc = Path('docs/requirements/m3d-answer-first-integration/evidence')
    report = dict(paid_requests=0, cases=[], page_errors=[])
    output = doc / ('pending-browser-' + args.campaign + '.json')
    assert not output.exists()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            report['browser_version'] = browser.version
            try:
                for case in ('A', 'C'):
                    root = Path('work') / ('af-pending-' + args.campaign) / case
                    root.mkdir(parents=True, exist_ok=False)
                    app, service, transport, _ = combined_product(root, case)
                    row = dict(case=case, mutation='source_update' if case == 'A' else 'evidence_revoke')
                    report['cases'].append(row)
                    try:
                        with product_http(app, service) as base:
                            ws, result = start_case(base, case)
                            verify_case(case, result, *preparation(root))
                            run_id = result['agent_run_id']
                            page = browser.new_page(viewport=dict(width=1440, height=1000))
                            page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
                            page.goto(base)
                            page.locator('#source-create').wait_for()
                            page.evaluate('([k,v])=>sessionStorage.setItem(k,v)', [f'linkloom:last-task:{ws}', run_id])
                            page.locator('[data-screen="ask"]').click()
                            page.locator('[data-action="reopen-task"]').click()
                            page.locator('.task-answer').wait_for()
                            old = page.locator('#ask-result').inner_text()
                            assert result['task_review']['observations'][0]['description'] in old
                            if case == 'A':
                                source, = app.snapshot_investigation_sources(ws)[1]
                                code, body = _client(base, '/api/sources/'+source['source_id'], method='PUT',
                                    data=dict(name=source['name'], content=source['content']+'\nPending verification update.'))
                                assert code == 202, body
                            else:
                                with app.repository._lock, app.repository._connection:
                                    app.repository._connection.execute(
                                        'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                                        (ws, result['evidence'][0]['evidence_ref']))
                            held = []
                            page.route('**/api/product/ask/runs/**', lambda route: held.append(route))
                            page.locator('[data-action="reopen-task"]').click()
                            page.wait_for_timeout(100)
                            assert len(held) == 1
                            row['pending_text'] = page.locator('#ask-result').inner_text()
                            page.screenshot(path=str(doc / ('pending-' + args.campaign + '-' + case + '.png')))
                            assert not row['pending_text'], 'Previous task evidence remains visible while revalidation HTTP is pending'
                            page.locator('#ask-language').select_option('en')
                            assert not page.locator('#ask-result').inner_text(), 'Language change restored stale evidence'
                            page.locator('#ask-language').select_option('zh')
                            assert not page.locator('#ask-result').inner_text()
                            response = held[0].fetch()
                            row['http'] = dict(status=response.status, body=response.json())
                            held[0].fulfill(response=response)
                            page.get_by_role('heading', name='任务调查已停止').wait_for()
                            blocked = row['http']['body']['result']
                            assert response.status == 200 and blocked['status'] == 'BLOCKED'
                            assert not blocked['evidence'] and not blocked.get('task_review')
                            assert result['task_review']['observations'][0]['description'] not in page.locator('#ask-result').inner_text()
                            page.locator('#ask-result').scroll_into_view_if_needed()
                            page.screenshot(path=str(doc / ('pending-' + args.campaign + '-' + case + '-blocked.png')))
                            row.update(passed=True, scripted_calls=len(transport.requests))
                            page.close()
                    finally:
                        app.close()
                assert not report['page_errors']
                report['passed'] = True
            finally:
                browser.close()
    finally:
        report['product_js_sha256'] = sha256(Path('src/linkloom/ui/static/product.js').read_bytes()).hexdigest()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


if __name__ == '__main__':
    main()
