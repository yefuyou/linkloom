/* Real base INT8 + existing hybrid retrieval. Requires prepared local models and Playwright. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawnHostProcess, stopHostProcess} = require('./browser_host.cjs');
const {chromium} = require('playwright');

(async () => {
  const checkout = path.resolve(__dirname, '../..');
  const root = path.resolve(process.argv[2] || `output/playwright/same-task-${Date.now()}`);
  await fs.mkdir(root, {recursive: true});
  const child = spawnHostProcess(process.env.LINKLOOM_TEST_PYTHON || 'python',
    ['-m', 'tests.same_task_hint_browser_server', '--root', path.join(root, 'fixture')],
    {cwd: checkout, env: {...process.env, PYTHONPATH: [path.join(checkout, 'src'), checkout].join(path.delimiter),
      LINKLOOM_SAME_TASK_HINT: 'on', HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1',
      PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', OPENAI_API_KEY: '', GEMINI_API_KEY: '', GOOGLE_API_KEY: '', DEEPSEEK_API_KEY: ''},
     stdio: ['ignore', 'pipe', 'pipe']});
  let browser, stderr = '';
  child.stderr.on('data', chunk => {stderr += chunk.toString();});
  try {
    const fixture = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('local model fixture timeout: ' + stderr)), 120000);
      child.stdout.on('data', chunk => {
        output += chunk.toString();
        const line = output.split(/\r?\n/).find(value => value.startsWith('{') && value.endsWith('}'));
        if (line) {clearTimeout(timer); resolve(JSON.parse(line));}
      });
      child.on('error', error => {clearTimeout(timer); reject(error);});
      child.on('exit', code => {clearTimeout(timer); reject(new Error('fixture exited ' + code + ': ' + stderr));});
    });
    browser = await chromium.launch({headless: true, ...(process.env.LINKLOOM_CHROMIUM_EXECUTABLE ? {executablePath: process.env.LINKLOOM_CHROMIUM_EXECUTABLE} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {if (request.method() === 'POST' && /\/review$|\/undo$/.test(new URL(request.url()).pathname)) writes.push(request.url());});
    await page.goto(fixture.url + '/tasks?workspace_id=' + encodeURIComponent(fixture.workspace_id));
    const card = page.getByTestId('candidate-' + fixture.candidate_id);
    await card.waitFor();
    let sawPreparing = false;
    const deadline = Date.now() + 180000;
    while (true) {
      // Finish each real HTTP read before refreshing; do not abort local inference.
      await page.waitForFunction(() => {
        const app = window.linkloomTasksApp;
        return app && app.state.candidates.items.length && app.state.candidates.items.every(item => Array.isArray(item.sameTaskHints));
      }, undefined, {timeout: 60000});
      if (await card.getByTestId('same-task-hint').count()) break;
      if (Date.now() > deadline) throw new Error('background preloading did not produce hints: ' + stderr);
      sawPreparing ||= await card.getByTestId('same-task-analyzing').count() > 0;
      await page.waitForTimeout(1500);
      await page.reload();
      await card.waitFor();
    }
    console.log('Product startup background preparation observed: ' + sawPreparing);
    const hints = card.getByTestId('same-task-hint');
    assert.ok(await hints.count() <= 3);
    const visible = await hints.allTextContents();
    assert.ok(visible.some(text => text.includes('可能是同一件事：联调报告')), visible);
    assert.ok(!visible.some(text => text.includes('联调周报')), visible);
    assert.ok(visible.some(text => text.includes('本地重排分数')), visible);
    const report = hints.filter({hasText: '可能是同一件事：联调报告'});
    await report.getByText('两边原文', {exact: true}).click();
    await report.getByRole('heading', {name: '本次候选原文'}).waitFor();
    assert.match(await report.innerText(), /联调那份报告/);
    assert.match(await report.innerText(), /由李明负责联调报告/);
    await page.screenshot({path: path.join(root, 'hints.png'), fullPage: true});
    await report.getByTestId('choose-hinted-task').click();
    await page.waitForFunction(() => {const form = window.linkloomTasksApp.state.form; return form && !form.previewPending && form.preview;});
    await page.getByTestId('merge-diff').waitFor();
    assert.equal(writes.length, 0);
    assert.equal(await page.locator('[data-field="mergeTarget"]').inputValue(), fixture.report_id);
    await page.screenshot({path: path.join(root, 'preview.png'), fullPage: true});
    assert.deepEqual(errors, []);
    console.log('PASS real base INT8: report paraphrase suggested, weekly report excluded, two quotes visible, choose opens preview without merge');
  } finally {
    if (browser) await browser.close();
    await stopHostProcess(child);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
