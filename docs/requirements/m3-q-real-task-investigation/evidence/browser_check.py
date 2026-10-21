import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests.m3p_support import QUESTION

root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('work/browser-m3q')
base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:7383/'
errors, responses = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
    page = browser.new_page(viewport=dict(width=1440, height=1000), device_scale_factor=1)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('response', lambda response: responses.append(dict(url=response.url, status=response.status))
        if '/api/product/ask' in response.url else None)
    page.goto('http://127.0.0.1:8774/')
    page.locator('#workspace-create, #source-create').wait_for()
    if page.locator('#workspace-create').is_visible():
        page.locator('#workspace-name').fill('Unconfigured task Provider')
        page.locator('#workspace-create button').click()
        page.locator('#source-create').wait_for()
    page.locator('[data-screen="ask"]').click()
    page.locator('#ask-query').wait_for()
    page.locator('#ask-domain').select_option('tasks')
    assert 'unavailable' in page.locator('#task-provider-status').inner_text().lower()
    assert '--task-max-requests' in page.locator('#task-provider-status').inner_text()
    page.screenshot(path=str(root / 'browser-unconfigured.png'), full_page=True)

    page.goto(base)
    page.locator('#source-create').wait_for()
    page.locator('[data-screen="ask"]').click()
    page.locator('#ask-query').wait_for()
    page.locator('#ask-query').fill(QUESTION)
    page.locator('#ask-domain').select_option('tasks')
    page.locator('#task-provider-status details summary').click()
    assert page.locator('#ask-external').is_checked() is False
    assert 'public-meeting-1.txt' in page.locator('#task-provider-status').inner_text()
    page.screenshot(path=str(root / 'browser-scope.png'), full_page=True)
    page.locator('#ask-submit-button').click()
    page.locator('#ask-result').get_by_role('heading', name='这次调查需要你的授权').wait_for()
    page.screenshot(path=str(root / 'browser-permission-denied.png'), full_page=True)
    page.locator('#ask-external').check()
    page.locator('#ask-submit-button').click()
    page.locator('#ask-result').get_by_role('heading', name='正在调查相关来源').wait_for(timeout=30000)
    page.screenshot(path=str(root / 'browser-running.png'), full_page=True)
    page.locator('[data-task-proposal-verdict]').first.wait_for(timeout=30000)
    assert page.locator('[data-task-proposal-verdict]').count() == 2
    assert 'UNKNOWN' in page.locator('#ask-result').inner_text()
    page.locator('#ask-result details').first.locator('summary').click()
    page.screenshot(path=str(root / 'browser-results.png'), full_page=True)
    text_before = page.locator('#ask-result').inner_text()
    page.locator('#ask-result [data-action="inspect-source"]').first.click()
    page.get_by_role('heading', name='public-meeting-1.txt').wait_for()
    page.screenshot(path=str(root / 'browser-source.png'), full_page=True)
    assert '2029-06-20' in page.locator('#page').inner_text()
    page.reload()
    page.locator('#source-create').wait_for()
    page.locator('[data-screen="ask"]').click()
    page.locator('[data-action="reopen-task"]').wait_for()
    page.locator('[data-action="reopen-task"]').click()
    page.locator('[data-task-proposal-verdict]').first.wait_for(timeout=30000)
    page.screenshot(path=str(root / 'browser-refresh-revalidated.png'), full_page=True)
    assert page.locator('[data-task-proposal-verdict]').count() == 2
    assert not errors, errors
    (root / 'browser.json').write_text(json.dumps(dict(mode='OFFLINE', browser=browser.version,
        errors=errors, responses=responses, result_text=text_before,
        refreshed_text=page.locator('#ask-result').inner_text()), ensure_ascii=False, indent=2), encoding='utf8')
    browser.close()
print('PASS: real Chromium ordinary UI, source permission, results, source open and refresh revalidation.')
