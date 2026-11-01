/* Keyless Chromium acceptance using the existing public synthetic product hosts.
   NODE_PATH must include Playwright; LINKLOOM_CHROMIUM_EXECUTABLE is optional.
   Run from the checkout root: node tests/ui/product_copy_browser.cjs [new-output-dir]
   --baseline uses master static files exported by the caller, without changing Git.
*/
const {strict: assert} = require('node:assert');
const {spawnHostProcess, stopHostProcess} = require('./browser_host.cjs');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(process.argv[2] || `work/copy-browser-${Date.now()}`);
const baseline = process.argv.includes('--baseline');
const selectedCases = process.argv.find(value => value.startsWith('--cases='))?.slice(8).split(',');
const report = {synthetic: true, external_provider_calls: 0, browser: '', steps: [], console_errors: [], http: []};
let browser, context;
async function host(args, directory) {
  const child = spawnHostProcess(process.env.LINKLOOM_TEST_PYTHON || 'python', args, {
    env: {...process.env, PYTHONPATH: ['src','.'].join(path.delimiter), PYTHONUNBUFFERED: '1', LINKLOOM_RETRIEVAL_MODE: 'bm25', GEMINI_API_KEY: '', DEEPSEEK_API_KEY: ''},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let url;
  try { url = await new Promise((resolve, reject) => {
    let output = '', errors = '';
    const timer = setTimeout(() => reject(new Error('Synthetic host startup timeout: '+errors)), 60000);
    child.stderr.on('data', d => errors += d);
    child.stdout.on('data', d => {
      output += d;
      const found = output.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (found) {clearTimeout(timer); resolve(found[0]);}
    });
    child.on('exit', code => {clearTimeout(timer); reject(new Error(`Host exited ${code}: ${errors}`));});
  });
  } catch (error) { await stopHostProcess(child); throw error; }
  return {url, stop: () => stopHostProcess(child)};
}
async function pageFor(url) {
  const page = await context.newPage();
  await page.setViewportSize({width:1440,height:1000});
  page.on('pageerror', e => report.console_errors.push(e.message));
  page.on('response', response => {
    if (new URL(response.url()).pathname.startsWith('/api/')) report.http.push({path:new URL(response.url()).pathname,status:response.status()});
  });
  await page.goto(url+'/?lang=en#meetings');
  await page.locator('#source-create, #workspace-create').waitFor();
  await page.evaluate(() => localStorage.setItem('linkloom.tasks.reviewer', 'public-browser-reviewer'));
  return page;
}
async function capture(page, name, description) {
  assert.ok(await page.locator('body').evaluate(el => el.scrollWidth <= innerWidth), name+' horizontal overflow');
  const file=name+'.png';
  await page.screenshot({path:path.join(root,file),fullPage:true});
  await fs.writeFile(path.join(root,name+'.txt'), await page.locator('body').innerText());
  report.steps.push({name, description, screenshot:file,status:'PASS'});
}
async function taskCase(kind) {
  const directory=path.join(root,'db-'+kind);
  const args=['-m','tests.task_answer_browser_server','--case',kind,'--root',directory,'--port','0'];
  if (baseline) args.push('--static-root',path.resolve('work/copy-baseline-static'));
  const server=await host(args); let page;
  try {
    page=await pageFor(server.url);
    if (kind==='partial') await capture(page,'01-sources-en','Sources list and local import setup');
    if (!baseline && kind==='partial') {
      await page.locator('#interface-language').selectOption('zh');
      await page.getByRole('heading',{name:'会议',exact:true}).waitFor();
      await page.reload();
      await page.getByRole('heading',{name:'会议',exact:true}).waitFor();
      assert.equal(await page.locator('#interface-language').inputValue(), 'zh');
      await capture(page,'02-sources-zh','Chinese source labels and unchanged source names');
      await page.locator('#new-workspace').click();
      await page.locator('#workspace-name').fill('Sources — 保留我的名称');
      await page.locator('#interface-language').selectOption('en');
      assert.equal(await page.locator('#workspace-name').inputValue(),'Sources — 保留我的名称');
      await page.locator('#interface-language').selectOption('zh');
      assert.equal(await page.locator('#workspace-name').inputValue(),'Sources — 保留我的名称');
      await capture(page,'03-workspace-form','Language changes preserve the open workspace form and user text');
      await page.locator('[data-action="back-sources"]').click();
      await page.locator('#interface-language').selectOption('en');
    }
    await page.locator('[data-screen="ask"]').click();
    await page.locator('#ask-query').fill('请调查这几次会议里交付任务如何延续，截止日期的变动是否有可靠依据？');
    if (!(await page.locator('#ask-advanced').evaluate(el => el.open))) await page.locator('#ask-advanced > summary').click();
    await page.locator('#ask-domain').selectOption('tasks');
    await page.locator('#ask-submit-button').click();
    await page.locator('.task-answer').waitFor({timeout:90000});
    let text=await page.locator('#ask-result').innerText();
    if (!baseline) {
      assert.ok(text.includes('任务状态：待确认'));
      assert.ok(!text.includes('EVIDENCE_VALID')&&!text.includes('MODEL_PROPOSED'));
      assert.ok(!/已批准|已确认延期/.test(text));
      if(kind==='partial') {
        assert.ok(text.includes('找到 2 条任务记录')&&text.includes('部分结果')&&text.includes('离线演示'));
        assert.ok(text.includes('模型建议 · 待确认'));
        assert.equal(await page.locator('[data-task-observation]').count(),2);
      } else if(kind==='unknown') {
        assert.ok(text.includes('资料不足')&&text.includes('暂未找到可核验'));
        assert.ok(!text.includes('任务不存在'));
      } else if(kind==='model-conflict') assert.ok(text.includes('模型判断：证据冲突'));
      else if(kind==='conflict') {
        assert.ok(text.includes('模型判断：待确认'));
        await page.locator('.task-investigation-details > summary').click();
        assert.ok((await page.locator('#ask-result').innerText()).includes('日期存在冲突'));
        await page.locator('.task-investigation-details > summary').click();
      }
      else if(kind==='p4') assert.ok(text.includes('日历日差值')&&text.includes('当前生效计划：待确认'));
    }
    await capture(page,`04-task-${kind}-zh`,'Task answer, state, source records and unresolved checks');
    if(kind==='partial') {
      await page.locator('[data-task-observation] .ask-source-details summary').first().click();
      assert.ok((await page.locator('[data-task-observation] .ask-source-details').first().innerText()).includes('TASK_DEADLINE'));
      await capture(page,'05-field-evidence','Expanded exact quote, field roles and source version');
      await page.locator('[data-task-observation] .ask-source-details summary').first().click();
      await page.locator('#ask-language').selectOption('en');
      text=await page.locator('#ask-result').innerText();
      assert.ok(text.includes(baseline?'Unconfirmed. This investigation does not grant approval.':'Task status: unconfirmed'));
      await capture(page,'06-task-partial-en','English task result with unapproved model suggestions');
      await page.locator('#ask-language').selectOption('zh');
      await page.setViewportSize({width:390,height:844});
      await capture(page,'07-task-mobile-zh','390px task result and source cards');
      await page.setViewportSize({width:1440,height:1000});
      if (!baseline) {
        await page.locator('[data-action="inspect-source"]').first().click();
        await page.locator('#update-content').waitFor();
        await page.locator('#interface-language').selectOption('zh');
        assert.ok(await page.locator('#source-update').count());
        assert.ok((await page.locator('#update-content').inputValue()).includes('完成日志迁移'));
        await capture(page,'08-original-source','Open source and switch interface language without returning to candidate view');
        await page.locator('[data-screen="ask"]').click();
        await page.locator('[data-action="reopen-task"]').click();
        await page.locator('.task-answer').waitFor({timeout:90000});
        const sources=(await (await page.request.get(server.url+'/api/sources')).json()).sources;
        const source=(await (await page.request.get(server.url+'/api/sources/'+sources[0].source_id)).json()).source;
        await page.request.put(server.url+'/api/sources/'+source.source_id,{data:{name:source.name,content:source.content+'\n[2029-06-06T09:00:00Z] 林枫: 公开合成来源已更新。'}});
        await page.locator('[data-action="reopen-task"]').click();
        await page.getByRole('heading',{name:'任务调查已停止'}).waitFor();
        const blocked=await page.locator('#ask-result').innerText();
        assert.ok(blocked.includes('旧任务详情已隐藏')&&!/林枫|2029-06-20|2029-06-24/.test(blocked));
        await capture(page,'09-stale-blocked','Reopening updated-source results hides old evidence');
        await page.locator('#new-workspace').click();
        await page.locator('#workspace-name').fill('Public isolation workspace');
        await page.locator('#workspace-create button[type="submit"]').click();
        await page.locator('#source-create').waitFor();
        await page.locator('[data-screen="ask"]').click();
        assert.equal(await page.locator('[data-action="reopen-task"]').count(),0);
        assert.ok(!(await page.locator('#page').innerText()).includes('林枫'));
        await capture(page,'10-workspace-isolation','New workspace has no previous task pointer or evidence');
      }
    }
  } finally {if(page)await page.close();await server.stop();}
}
async function openReview(page) {
  await page.locator('[data-screen="sources"]').click();
  await page.locator('button[data-action="back-review"]').click();
}
async function workflow() {
  const server=await host(['scripts/demo_stale_revalidation.py','--directory',path.join(root,'workflow-db')]);let page;
  try {
    page=await pageFor(server.url);
    await openReview(page);
    await page.locator('[data-action="revalidate-decision"]').waitFor();
    await capture(page,'11-review-attention','Pending candidates and immediate source-changed warning');
    await page.locator('[data-action="revalidate-decision"]').click();
    await page.locator('[data-action="revalidate-candidate"]').waitFor();
    await capture(page,'12-source-comparison','Original approval, current source and separate new candidate');
    await page.locator('[data-action="revalidate-candidate"]').click();
    await page.locator('#reviewer-id').fill('public-browser-reviewer');
    await page.locator('#review-reason').fill('Reviewed exact current synthetic evidence');
    await page.locator('#interface-language').selectOption('zh');
    assert.equal(await page.locator('#reviewer-id').inputValue(),'public-browser-reviewer');
    await capture(page,'13-review-zh','Chinese review and explicit approval of the new candidate');
    await page.locator('[data-review-action="approve"]').click();
    await page.locator('#timeline-as-of').waitFor();
    assert.ok((await page.locator('#page').innerText()).includes('独立人工批准复核于'));
    assert.ok(!(await page.locator('#page').innerText()).includes('状态暂不可用'));
    await capture(page,'14-decision-history-zh','Approved result, dated history and retained original approval');
    await page.locator('#timeline-date').fill('2026-10-02');
    await page.locator('#timeline-as-of button[type="submit"]').click();
    await page.getByText(/查询日期：\s*2026-10-02/).waitFor();
    const historicalRead=page.waitForResponse(response => new URL(response.url()).pathname==='/api/decisions/recover');
    await page.locator('#interface-language').selectOption('en');
    const historicalPayload=await (await historicalRead).json();
    assert.ok(historicalPayload.recovery.as_of.startsWith('2026-10-02'));
    await page.getByText(/Selected date:\s*2026-10-02/).waitFor();
    assert.equal(await page.locator('#timeline-date').inputValue(),'2026-10-02');
    assert.ok((await page.locator('#page').innerText()).includes('Selected date: 2026-10-02'));
    assert.ok(!(await page.locator('#page').innerText()).includes(' → 持续有效'));
    await capture(page,'14-historical-language','Language switch preserves the queried historical date and result scope');
    await page.locator('#timeline-date').fill('');
    await page.locator('#timeline-as-of button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('[data-action="ask-decision"]')?.dataset.asOf === '');
    assert.equal(await page.locator('[data-action="ask-decision"]').getAttribute('data-relation'), 'uses supplier');
    await page.locator('[data-action="ask-decision"]').click();
    await reviewedAsk(page);
  } finally {if(page)await page.close();await server.stop();}
}
async function reviewedAsk(page) {
  try {
    await page.locator('#ask-query').fill('What supplier are we currently using?');
    await page.locator('#ask-submit-button').click();
    await page.waitForFunction(() => document.querySelector('#ask-result')?.textContent.includes('Wrenwell'), undefined, {timeout:30000});
    assert.ok((await page.locator('#ask-result').innerText()).includes('Wrenwell'));
    await capture(page,'15-ask-reviewed','Reviewed supplier lookup with evidence');
    await page.evaluate(() => localStorage.setItem('linkloom.tasks.reviewer', 'public-browser-reviewer'));
    await page.locator('#feedback-panel > summary').click();
    await page.locator('#feedback-term').fill('供方');
    await page.locator('#feedback-relation').selectOption('uses supplier');
    await page.locator('#feedback-form button[type="submit"]').click();
    await page.locator('[data-action="feedback-approve"]').waitFor();
    assert.ok(await page.locator('#feedback-panel').isVisible());
    assert.equal(await page.locator('#feedback-panel').evaluate(element => element.open), true);
    assert.equal(await page.locator('[data-action="feedback-revoke"]').count(), 0, 'submission must remain pending');
    assert.ok((await page.locator('#feedback-identity').innerText()).includes('public-browser-reviewer'));
    await page.locator('[data-action="feedback-reject"]').click();
    await page.getByText('已拒绝 / Rejected').waitFor();
    await page.locator('#ask-submit-button').click();
    await page.waitForFunction(() => document.querySelector('#ask-result')?.textContent.includes('Wrenwell'), undefined, {timeout:30000});
    assert.ok((await page.locator('#ask-result').innerText()).includes('Wrenwell'));
    await capture(page,'15-terminology-lifecycle','Correction reuses local identity, stays pending, requires a separate explicit review and preserves the question');
    await page.setViewportSize({width:390,height:844});
    await capture(page,'16-ask-mobile','390px reviewed answer and composer');
    await page.setViewportSize({width:1440,height:1000});
    await openReview(page);
    await page.locator('#interface-language').selectOption('zh');
    await capture(page,'17-review-complete-zh','No stale decision remains after separate approval');
  } catch (error) {
    await fs.writeFile(path.join(root,'ask-failure.txt'), await page.locator('body').innerText());
    await page.screenshot({path:path.join(root,'ask-failure.png'),fullPage:true});
    throw error;
  }
}
async function resumeWorkflow() {
  const database = process.argv.find(value => value.startsWith('--resume-workflow-db='))?.slice(21);
  assert.ok(database, 'Pass the already-approved synthetic workflow database to resume');
  // Restore the exact frozen relation resolver used to seed this synthetic database.
  // The default CLI resolver intentionally rejects relations outside its own catalogue.
  const resumeCode = [
    'import json,sys', 'from pathlib import Path',
    'from linkloom.ui.product import ProductApplication',
    'from linkloom.ui.server import create_http_server',
    'from linkloom.semantic_ingestion.relations import FrozenRelationResolver',
    'from tests.integration.test_product_application_flow import _UnusedRunBackend',
    'app=ProductApplication(database_path=Path(sys.argv[1]), extractor=None, relation_resolver=FrozenRelationResolver(("uses supplier",), schema_version="product-test-relations/v1"))',
    'server=create_http_server(_UnusedRunBackend(), product_app=app, port=0)',
    'print(json.dumps({"url":f"http://127.0.0.1:{server.server_port}"}),flush=True)',
    'server.serve_forever()'
  ].join('; ');
  const server = await host(['-u','-c',resumeCode,path.resolve(database)]); let page;
  try {
    page = await pageFor(server.url);
    await page.locator('[data-screen="ask"]').click();
    await page.locator('#ask-form').waitFor();
    await page.locator('#ask-advanced > summary').click();
    const config = await (await page.request.get(server.url+'/api/product/ask/config')).json();
    const selected = config.scopes.findIndex(slot => slot.relation === 'uses supplier');
    assert.ok(selected >= 0);
    await page.locator('#ask-slot').selectOption(String(selected));
    await reviewedAsk(page);
  } finally {if(page)await page.close();await server.stop();}
}
async function freshAndReplay() {
  const server=await host(['-m','linkloom.ui','--database',path.join(root,'fresh.sqlite'),'--port','0']);let page;
  try {
    page=await pageFor(server.url);
    await capture(page,'18-empty-workspace','Fresh application requests a workspace');
    await page.locator('#interface-language').selectOption('zh');
    await page.locator('#workspace-name').fill('公开空白工作区');
    await page.locator('#workspace-create button[type="submit"]').click();
    await page.locator('#source-create').waitFor();
    await capture(page,'19-empty-sources-zh','Chinese source empty state and save-only import');
    await openReview(page);
    await page.getByRole('heading',{name:'暂无待审核候选'}).waitFor();
    await capture(page,'20-empty-review-zh','Empty review queue, supporting proposals and attention state');
    await page.locator('[data-screen="decisions"]').click();
    await page.getByRole('heading',{name:'工作区暂无决策'}).waitFor();
    await capture(page,'21-empty-decisions-zh','Empty Decision Memory has a clear next step');
    await page.locator('[data-screen="ask"]').click();
    await page.locator('#ask-query').fill('调查会议中的交付任务');
    if (!(await page.locator('#ask-advanced').evaluate(el => el.open))) await page.locator('#ask-advanced > summary').click();
    await page.locator('#ask-domain').selectOption('tasks');
    await page.locator('#ask-submit-button').click();
    await page.getByRole('heading',{name:'请先添加来源'}).waitFor();
    await capture(page,'21-empty-investigation','Empty-source investigation gives an actionable prerequisite');
    await page.locator('[data-screen="sources"]').click();
    await page.locator('#source-name').fill('public-task-source.txt');
    await page.locator('#source-content').fill('[2029-06-01T09:00:00Z] 林枫: 完成日志迁移。');
    await page.locator('#source-create button[type="submit"]').click();
    await page.locator('#source-update').waitFor();
    await capture(page,'22-saved-source-zh','Source saves locally while extraction remains unavailable');
    await page.locator('[data-screen="ask"]').click();
    await page.locator('#ask-query').fill('调查会议中的交付任务');
    if (!(await page.locator('#ask-advanced').evaluate(el => el.open))) await page.locator('#ask-advanced > summary').click();
    await page.locator('#ask-domain').selectOption('tasks');
    await page.locator('#ask-submit-button').click();
    await page.getByRole('heading',{name:'当前无法使用任务调查'}).waitFor();
    await capture(page,'22-task-unavailable','Immediate unavailable reason with setup instructions expandable');
  } finally {if(page)await page.close();await server.stop();}
}
(async()=>{
  await fs.mkdir(root,{recursive:false});
  browser=await chromium.launch({headless:true,...(process.env.LINKLOOM_CHROMIUM_EXECUTABLE?{executablePath:process.env.LINKLOOM_CHROMIUM_EXECUTABLE}: {})});
  report.browser=browser.version();
  context=await browser.newContext();
  try {
    for (const kind of baseline ? ['partial'] : ['partial','unknown','conflict','model-conflict','p4'].filter(kind => !selectedCases || selectedCases.includes(kind))) await taskCase(kind);
    if(!baseline) {if (!selectedCases || selectedCases.includes('workflow')) await workflow(); if (selectedCases?.includes('workflow-resume')) await resumeWorkflow(); if (!selectedCases || selectedCases.includes('fresh')) await freshAndReplay();}
    assert.deepEqual(report.console_errors,[]);
    report.status='PASS';
  } catch(error) {report.status='FAIL';report.error=error.stack;throw error;}
  finally {await fs.writeFile(path.join(root,'BROWSER_RESULT.json'),JSON.stringify(report,null,2));await browser.close();}
  console.log(`PASS: ${report.steps.length} captured browser steps; Chromium ${report.browser}; no external Provider`);
})().catch(e=>{console.error(e);process.exitCode=1});
