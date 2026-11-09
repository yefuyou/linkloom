"""Run the README GIF route three times; screenshots only, no paid models.

Run from the checkout root: python scripts/dryrun.py
Requires Python Playwright and a local Chrome installation. Graph demo seeds
use the ordinary source import and approval APIs; all responses are offline.
"""
import sys, os, json, time, traceback
from pathlib import Path
CHECKOUT = Path(__file__).resolve().parents[1]
ROOT = Path.cwd() / 'work' / 'gif-dryrun'
sys.path[:0] = [str(CHECKOUT / 'src'), str(CHECKOUT), str(CHECKOUT / 'tests/integration')]
os.environ['LINKLOOM_RETRIEVAL_MODE'] = 'bm25'
os.environ['LINKLOOM_LLM_ROUTER'] = 'off'
from graph_evidence_jump_fixture import build_graph_demo, GRAPH_QUERY, GRAPH_AS_OF
from playwright.sync_api import sync_playwright

MEETING = '[2026-10-01T10:00:00Z] Speaker-A: We decided to use Birchline as the supplier for Launch.'
QUERY = GRAPH_QUERY

def host(directory):
    return build_graph_demo(directory / 'demo.sqlite')

def run(page, demo, directory, report):
    page.set_default_timeout(12000)
    page.on('pageerror', lambda error: report.setdefault('page_errors', []).append(str(error)))
    def capture(name):
        page.screenshot(path=str(directory / (name + '.png')), full_page=True)
        report['steps'].append(name)
    page.goto(demo.base_url + '/#meetings')
    page.locator('#interface-language').select_option('zh')
    page.locator('#source-name').fill('README-demo-meeting.txt')
    page.locator('#source-content').fill(MEETING)
    capture('01-import')
    page.locator('#source-create button[type=submit]').click()
    page.wait_for_timeout(800)
    capture('02-imported')
    page.locator('[data-screen="sources"]').click()
    page.locator('[data-action="back-review"]').first.click()
    page.locator('[data-action="inspect-candidate"]').first.click()
    page.locator('#candidate-review').wait_for()
    capture('03-candidate')
    page.locator('#reviewer-id').fill('演示审核员')
    page.locator('#review-reason').fill('合成会议原文明确决定采用 Birchline。')
    page.locator('[data-review-action="approve"]').click()
    page.wait_for_timeout(500)
    capture('04-approved')
    # Verify actual persistence rather than inferring approval from a click.
    decisions = page.request.get(demo.base_url + '/api/decisions').json()
    report['approval_response'] = decisions
    assert any(d['value'] == 'Birchline' and d['memory_state'] == 'ACTIVE' for d in decisions['decisions']), decisions
    page.goto(demo.base_url + '/#ask')
    page.locator('#ask-query').fill(QUERY)
    page.locator('#ask-advanced > summary').click()
    page.locator('#ask-date').fill(GRAPH_AS_OF)
    capture('05-question')
    with page.expect_response(lambda response: '/api/product/ask' in response.url and response.request.method == 'POST') as response:
        page.locator('#ask-submit-button').click()
    result = response.value.json()['result']
    deadline = time.monotonic() + 20
    while result['status'] == 'INVESTIGATING' and time.monotonic() < deadline:
        page.wait_for_timeout(250)
        result = page.request.get(demo.base_url + '/api/product/ask/runs/' + result['agent_run_id'] + '?workspace_id=' + result['workspace_id']).json()['result']
    report['graph_result'] = result
    page.wait_for_timeout(700)
    capture('06-graph-result')
    assert result['status'] == 'COMPLETED', result
    assert result['decided_by'] == 'graph', result
    assert result['source_investigation']['answer'] == '演示主管B', result
    assert len(result['evidence']) == 2 and result['reviewer']['accepted'], result
    page.locator('[data-action="inspect-evidence"]').first.wait_for()
    capture('06-answer-citations')
    page.locator('[data-action="inspect-evidence"]').first.click()
    mark = page.locator('dialog[open] mark')
    mark.wait_for(state='visible')
    assert mark.inner_text() == result['evidence'][0]['quote']
    assert mark.evaluate('(el) => el === document.activeElement')
    report['highlight'] = mark.inner_text()
    capture('07-original-highlight')

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    session = ROOT / ('session-' + time.strftime('%Y%m%d-%H%M%S'))
    session.mkdir()
    reports = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True, timeout=15000)
        for i in range(1, 4):
            directory = session / f'run-{i}'
            directory.mkdir()
            report = dict(run=i, status='FAIL', steps=[], external_model_requests=0, cost_usd=0)
            demo = None
            context = browser.new_context(viewport=dict(width=1280, height=720), reduced_motion='reduce')
            page = context.new_page()
            start = time.monotonic()
            try:
                demo = host(directory)
                run(page, demo, directory, report)
                report['status'] = 'PASS'
            except Exception as error:
                report['error'] = str(error)
                report['traceback'] = traceback.format_exc()
                page.screenshot(path=str(directory / 'failure.png'), full_page=True)
            finally:
                report['seconds'] = round(time.monotonic() - start, 2)
                context.close()
                if demo: demo.stop()
                (directory / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                reports.append(report)
                print(json.dumps(dict(run=i, status=report['status'], steps=report['steps'], error=report.get('error', '')[:450]), ensure_ascii=False), flush=True)
        browser.close()
    (session / 'results.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(session), flush=True)

if __name__ == '__main__': main()
