/* Explicitly synthetic HTTP fixture for browser UI behavior; no extractor or model runs.
   Real product ingestion is verified separately by the L5-2 A/B acceptance.
   Run with the repository's Playwright/Chromium environment:
   node tests/ui/task_field_bindings_browser.cjs [fresh-output-directory]
*/
const {strict: assert} = require('node:assert');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');

async function main() {
  const {chromium} = require('playwright');
  const output = path.resolve(process.argv[2] || `output/playwright/task-fields-${Date.now()}`);
  await fs.mkdir(path.dirname(output), {recursive: true});
  await fs.mkdir(output, {recursive: false});
  const report = {status: 'FAIL', synthetic: true,
    scope: 'Real browser with explicit synthetic HTTP responses; not extraction or backend acceptance.',
    model_requests: 0, screenshots: [], writes: [], page_errors: [], server_errors: []};
  const original = '联调报告改由王芳负责，延期到 10/16。';
  const fingerprint = 'f'.repeat(64);
  const workspace = {workspace_id: 'ui-fields', name: '字段显示离线夹具'};
  const evidence = {source_id: 'source-b', source_version_id: 'version-b', source_name: 'B 会议',
    meeting_time: '2026-10-10T02:00:00+00:00', message_id: 'original-message-b',
    line_start: 1, line_end: 1, char_start: 0, char_end: original.length,
    quote: original, quote_sha256: 'q'.repeat(64), source_sha256: 's'.repeat(64)};
  const candidate = {candidate_id: 'candidate-b', workspace_id: workspace.workspace_id,
    title: original, title_needs_edit: true, owner: '王芳', deadline: null, deadline_text: '10/16'};
  let pending = true, task = null;
  const pageOf = items => ({items, page: {limit: 20, offset: 0, next_offset: null, has_more: false}});
  const summary = () => ({task, review_status: 'approved', review_revision: '1',
    source_summary: [{source_name: evidence.source_name, meeting_time: evidence.meeting_time}],
    field_sources: {title: {origin: 'human'}, owner: {origin: 'source'}, deadline: {origin: 'source'}}});
  const sendJson = (response, value, status = 200) => {
    response.writeHead(status, {'Content-Type': 'application/json; charset=utf-8'});
    response.end(JSON.stringify(value));
  };
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://127.0.0.1');
      let body = null;
      if (request.method === 'POST') {
        let bytes = '';
        for await (const chunk of request) bytes += chunk;
        body = JSON.parse(bytes);
      }
      if (url.pathname === '/api/workspaces') return sendJson(response, {workspaces: [workspace]});
      if (url.pathname === '/api/workspaces/current') return sendJson(response, {workspace});
      if (url.pathname === '/api/product/task-candidates') return sendJson(response, pageOf(pending ? [{
        candidate_id: candidate.candidate_id, candidate_fingerprint: fingerprint, candidate,
        is_current: true, reviewed: false, review_status: null,
        source_summary: [{source_name: evidence.source_name, meeting_time: evidence.meeting_time}], evidence_count: 1,
      }] : []));
      if (url.pathname === '/api/product/task-candidates/candidate-b/evidence') {
        assert.equal(url.searchParams.get('candidate_fingerprint'), fingerprint);
        return sendJson(response, {...pageOf([evidence]), evidence_basis: 'current_source'});
      }
      if (url.pathname === '/api/product/task-candidates/candidate-b/review' && request.method === 'POST') {
        assert.equal(pending, true);
        assert.equal(url.searchParams.get('workspace_id'), workspace.workspace_id);
        assert.equal(body.action, 'create');
        assert.equal(body.expected_candidate_fingerprint, fingerprint);
        assert.equal(body.reviewer, '页面审核人');
        assert.equal(body.reason, '人工修正：任务描述');
        assert.deepEqual(body.patch, {title: '联调报告'});
        report.writes.push({path: url.pathname, body});
        task = {id: 'task-1', workspace_id: workspace.workspace_id, title: body.patch.title,
          owner: candidate.owner, deadline: null, deadline_text: candidate.deadline_text, status: 'open'};
        pending = false;
        return sendJson(response, summary());
      }
      if (url.pathname === '/api/product/tasks') return sendJson(response, pageOf(task ? [summary()] : []));
      if (url.pathname === '/api/product/tasks/task-1') return sendJson(response, summary());
      if (url.pathname === '/api/product/tasks/task-1/events') return sendJson(response, pageOf(task ? [{
        id: 'created-1', event_type: 'created', new_value: task, actor: '页面审核人',
        reason: '人工修正：任务描述', created_at: '2026-11-01T14:00:00+00:00',
        meeting_time: evidence.meeting_time, before_evidence: [], after_evidence: [evidence],
        evidence_role: 'context', after_evidence_role: 'context',
      }] : []));
      const asset = {'/tasks': 'tasks.html', '/tasks.js': 'tasks.js', '/tasks.css': 'tasks.css'}[url.pathname];
      if (asset) {
        response.writeHead(200, {'Content-Type': asset.endsWith('.html') ? 'text/html; charset=utf-8' :
          asset.endsWith('.js') ? 'text/javascript; charset=utf-8' : 'text/css; charset=utf-8'});
        response.end(await fs.readFile(path.join(__dirname, '../../src/linkloom/ui/static', asset)));
        return;
      }
      sendJson(response, {error: 'Unknown fixture route'}, 404);
    } catch (error) {
      report.server_errors.push(error.stack);
      if (!response.headersSent) sendJson(response, {error: 'Synthetic fixture assertion failed'}, 500);
      else response.end();
    }
  });
  await new Promise((resolve, reject) => {server.once('error', reject); server.listen(0, '127.0.0.1', resolve);});
  let browser;
  try {
    browser = await chromium.launch({headless: true, ...(process.env.LINKLOOM_CHROMIUM_EXECUTABLE ?
      {executablePath: process.env.LINKLOOM_CHROMIUM_EXECUTABLE} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', error => report.page_errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('linkloom.tasks.reviewer', '页面审核人'));
    await page.goto(`http://127.0.0.1:${server.address().port}/tasks`);
    const card = page.getByTestId('candidate-candidate-b');
    await card.getByTestId('title-needs-edit').waitFor();
    assert.equal(await card.getByTestId('title-needs-edit').innerText(), '标题待整理');
    assert.ok((await card.innerText()).includes('截止 10/16'));
    assert.ok(!(await card.innerText()).includes('2026-10-16'));
    assert.equal(await card.locator('.card-title').innerText(), original);
    const capture = async name => {
      await page.locator('#tasks-app[aria-busy="false"]').waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({path: path.join(output, name + '.png')});
      report.screenshots.push({path: name + '.png', viewport: page.viewportSize()});
    };
    await capture('desktop-flagged-candidate');
    await page.setViewportSize({width: 390, height: 844});
    await capture('mobile-flagged-candidate');
    await card.getByTestId('candidate-create').click();
    const title = page.getByTestId('confirm-title');
    assert.equal(await title.inputValue(), original);
    assert.equal(await page.getByTestId('confirm-deadline').inputValue(), '');
    assert.equal(await page.getByTestId('confirm-deadline_text').inputValue(), '10/16');
    await title.fill('联调报告');
    await capture('mobile-edit-title');
    await page.getByTestId('confirm-review').click();
    await page.getByTestId('view-task').waitFor();
    assert.equal(await page.getByTestId('task-detail-panel').count(), 0);
    assert.equal(report.writes.length, 1);
    await page.getByTestId('view-task').click();
    await page.getByTestId('task-detail-panel').waitFor();
    assert.equal(await page.getByTestId('task-deadline').innerText(), '截止 10/16');
    assert.equal(await page.locator('[data-testid="task-detail-panel"] > .card-title').innerText(), '联调报告');
    await page.getByTestId('task-event').locator('details.evidence > summary').click();
    assert.equal(await page.getByTestId('task-event').locator('blockquote').innerText(), original);
    await capture('mobile-confirmed-task');
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.server_errors, []);
    report.status = 'PASS';
    console.log('PASS: month/day display, title warning, manual title patch and unchanged source quote (synthetic UI HTTP fixture)');
  } catch (error) {
    report.error = error.stack;
    throw error;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
    await fs.writeFile(path.join(output, 'BROWSER_RESULT.json'), JSON.stringify(report, null, 2));
  }
}

if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
