/* Offline formal-task browser smoke; all mutations go through the visible UI.
   Reuse runTaskWorkflow(page, fixture) when recording a future demo GIF.
   Requirements: Playwright available on NODE_PATH; a compatible Node version.
   LINKLOOM_TEST_PYTHON / LINKLOOM_CHROMIUM_EXECUTABLE are optional overrides.
   Run: node tests/ui/tasks_browser.cjs [fresh-output-directory]
*/
const {strict: assert} = require('node:assert');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');

async function startHost(directory) {
  const checkout = path.resolve(__dirname, '../..');
  const child = spawn(process.env.LINKLOOM_TEST_PYTHON || 'python',
    ['-m', 'tests.task_mvp_browser_server', '--root', directory, '--port', '0'], {
      cwd: checkout,
      env: {...process.env, PYTHONPATH: [path.join(checkout, 'src'), checkout].join(path.delimiter),
        PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', GEMINI_API_KEY: '', DEEPSEEK_API_KEY: ''},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  let stderr = '';
  child.stderr.on('data', value => { stderr += value.toString('utf8'); });
  const info = await new Promise((resolve, reject) => {
    let stdout = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Task fixture timeout: ' + stderr)); }, 45000);
    child.stdout.on('data', value => {
      stdout += value.toString('utf8');
      const line = stdout.split(/\r?\n/).find(value => value.startsWith('{') && value.endsWith('}'));
      if (line) {clearTimeout(timer); resolve(JSON.parse(line));}
    });
    child.on('error', error => {clearTimeout(timer); reject(error);});
    child.on('exit', code => {clearTimeout(timer); reject(new Error(`Task fixture exited ${code}: ${stderr}`));});
  });
  return {...info, stop: async () => {
    if (child.exitCode !== null) return;
    await new Promise(resolve => {child.once('exit', resolve); child.kill();});
  }};
}

async function requestJson(page, fixture, suffix) {
  const response = await page.request.get(fixture.url + '/api/product' + suffix +
    (suffix.includes('?') ? '&' : '?') + new URLSearchParams({workspace_id: fixture.workspace_id}));
  assert.equal(response.status(), 200);
  return response.json();
}

async function runTaskWorkflow(page, fixture, capture = async () => {}) {
  const posts = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/product/')) {
      posts.push({path: new URL(request.url()).pathname, body: request.postDataJSON()});
    }
  });
  let identityPrompts = 0;
  page.on('dialog', async dialog => {
    assert.equal(dialog.type(), 'prompt');
    assert.match(dialog.message(), /你的名字/);
    identityPrompts += 1;
    await dialog.accept('页面审核人');
  });
  await page.goto(fixture.url + '/tasks');
  await page.getByTestId('candidate-' + fixture.candidate_a).waitFor();
  await page.locator('#identity').focus();
  assert.equal(await page.locator('#identity').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement === document.body), false);
  await capture('01-candidates');
  await page.getByTestId('candidate-' + fixture.candidate_a).getByTestId('candidate-create').click();
  await page.getByTestId('review-reason').waitFor();
  assert.equal(await page.getByTestId('review-reason').inputValue(), '按原文确认，未修改');
  await page.getByTestId('confirm-review').click();
  await page.getByText('以 页面审核人 身份操作', {exact: false}).waitFor();
  await page.getByTestId('task-detail-panel').waitFor();
  await page.locator('#tasks-app[aria-busy="false"]').waitFor();
  const createdTasks = await requestJson(page, fixture, '/tasks');
  assert.equal(createdTasks.items.length, 1);
  const taskId = createdTasks.items[0].task.id;
  assert.equal(createdTasks.items[0].task.owner, '李明');
  await capture('02-created');

  await page.locator('[data-action="tab-candidates"]').click();
  await page.getByTestId('candidate-' + fixture.candidate_b).getByTestId('candidate-merge').click();
  await page.getByTestId('merge-target').selectOption(taskId);
  await page.getByTestId('merge-field-owner').check();
  await page.getByTestId('merge-field-deadline').check();
  await page.getByTestId('merge-preview').click();
  await page.getByTestId('merge-diff').waitFor();
  await page.locator('#tasks-app[aria-busy="false"]').waitFor();
  const previewText = await page.locator('#tasks-app').innerText();
  for (const value of ['李明', '王芳', '2026-10-12', '2026-10-16']) assert.ok(previewText.includes(value), value);
  assert.match(await page.getByTestId('review-reason').inputValue(), /^合并入已有任务：.*负责人.*截止时间/);
  await capture('03-merge-preview');
  await page.getByTestId('merge-confirm').click();
  await page.getByTestId('task-detail-panel').waitFor();
  await page.locator('#tasks-app[aria-busy="false"]').waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="task-owner"]').textContent.includes('王芳'));
  const merged = await requestJson(page, fixture, '/tasks/' + taskId);
  assert.equal(merged.task.owner, '王芳');
  assert.equal(merged.task.deadline, '2026-10-16');
  const history = await requestJson(page, fixture, '/tasks/' + taskId + '/events');
  assert.deepEqual(history.items.map(event => event.event_type), ['created', 'owner_changed', 'deadline_changed']);
  assert.ok(history.items[0].after_evidence.some(e => e.quote === fixture.quote_a && e.line_start === 1));
  for (const event of history.items.slice(1)) {
    assert.ok(event.before_evidence.some(e => e.quote === fixture.quote_a && e.line_start === 1));
    assert.ok(event.after_evidence.some(e => e.quote === fixture.quote_b && e.line_start === 1));
    assert.equal(event.meeting_time, '2026-10-10T02:00:00+00:00');
    assert.equal(event.actor, '页面审核人');
  }
  for (const summary of await page.locator('[data-testid="task-event"] > details.evidence > summary').all()) await summary.click();
  assert.ok(await page.getByText(fixture.quote_a, {exact: true}).first().isVisible());
  assert.ok(await page.getByText(fixture.quote_b, {exact: true}).first().isVisible());
  assert.match(await page.locator('[data-testid="task-event"] .quote').first().innerText(), /行 1/);
  await capture('04-merged-history');

  await page.getByTestId('undo-preview').first().click();
  await page.getByTestId('undo-confirm').waitFor();
  assert.equal(await page.getByTestId('review-reason').inputValue(), '');
  await page.getByTestId('review-reason').fill('人工复核后撤销这次合并');
  await capture('05-undo-preview');
  await page.getByTestId('undo-confirm').click();
  await page.getByTestId('task-detail-panel').waitFor();
  await page.locator('#tasks-app[aria-busy="false"]').waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="task-owner"]').textContent.includes('李明'));
  const restored = await requestJson(page, fixture, '/tasks/' + taskId);
  assert.equal(restored.task.owner, '李明');
  assert.equal(restored.task.deadline, '2026-10-12');
  const pending = await requestJson(page, fixture, '/task-candidates');
  assert.ok(pending.items.some(value => value.candidate_id === fixture.candidate_b));
  const retained = await requestJson(page, fixture, '/tasks/' + taskId + '/events');
  assert.ok(retained.items.length > history.items.length);
  assert.deepEqual(retained.items.slice(0, 3), history.items);
  assert.equal(identityPrompts, 1);
  const writes = posts.filter(post => /\/review$|\/undo$|\/status$/.test(post.path));
  assert.equal(writes.length, 3);
  assert.ok(writes.every(post => post.body.reviewer === '页面审核人' || post.body.actor === '页面审核人'));
  assert.deepEqual(writes.map(post => post.body.action || 'undo'), ['create', 'merge', 'undo']);
  await capture('06-restored');
  return {task_id: taskId, identity_prompts: identityPrompts, writes, retained_events: retained.items.length};
}

async function main() {
  const {chromium} = require('playwright');
  const root = path.resolve(process.argv[2] || `output/playwright/tasks-${Date.now()}`);
  await fs.mkdir(path.dirname(root), {recursive: true});
  await fs.mkdir(root, {recursive: false});
  const fixture = await startHost(path.join(root, 'fixture'));
  let browser;
  const report = {synthetic: true, steps: [], console_errors: [], status: 'FAIL'};
  try {
    browser = await chromium.launch({headless: true, ...(process.env.LINKLOOM_CHROMIUM_EXECUTABLE ?
      {executablePath: process.env.LINKLOOM_CHROMIUM_EXECUTABLE} : {})});
    report.browser = browser.version();
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', error => report.console_errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') report.console_errors.push(message.text());});
    const capture = async name => {
      await page.screenshot({path: path.join(root, name + '.png'), fullPage: true});
      const overflow = await page.locator('body').evaluate(el => el.scrollWidth > innerWidth);
      if (overflow) {
        report.overflow = await page.locator('body').evaluate(() => Array.from(document.querySelectorAll('*')).filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => ({tag: el.tagName, class: el.className, text: el.textContent.slice(0, 150), right: el.getBoundingClientRect().right})).slice(0, 30));
      }
      assert.equal(overflow, false, name + ': horizontal overflow');
      report.steps.push(name);
    };
    Object.assign(report, await runTaskWorkflow(page, fixture, capture));
    await page.setViewportSize({width: 390, height: 844});
    await capture('07-mobile-detail');
    await page.locator('[data-action="tab-candidates"]').click();
    await page.getByTestId('candidate-' + fixture.candidate_b).waitFor();
    await capture('08-mobile-candidates');
    assert.deepEqual(report.console_errors, []);
    report.status = 'PASS';
    console.log(`PASS: formal task create → merge → undo; ${report.steps.length} desktop/mobile captures; Chromium ${report.browser}`);
  } catch (error) {
    report.error = error.stack;
    throw error;
  } finally {
    await fs.writeFile(path.join(root, 'BROWSER_RESULT.json'), JSON.stringify(report, null, 2));
    if (browser) await browser.close();
    await fixture.stop();
  }
}

module.exports = {runTaskWorkflow, startHost};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
