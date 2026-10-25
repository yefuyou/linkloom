// Run with playwright-cli run-code --filename=tests/ui/task_answer_chromium.js
// Start tests.task_answer_browser_server first and navigate to its loopback URL.
async (page, outputDirectory = 'output/playwright') => {
  const check = (condition, message) => {if (!condition) throw new Error(message);};
  const base = new URL(page.url()).origin;
  check(new URL(base).hostname === '127.0.0.1', 'Synthetic local host required');
  await page.getByRole('button', {name: 'Ask', exact: true}).click();
  await page.locator('#ask-query').fill('请调查这几次会议里交付任务如何延续，截止日期的变动是否有可靠依据？');
  await page.locator('#ask-domain').selectOption('tasks');
  await page.locator('#ask-submit-button').click();
  await page.locator('.task-answer').waitFor();
  const result = page.locator('#ask-result');
  const text = await result.innerText();
  const config = await (await page.request.get(base + '/api/product/ask/config')).json();
  const kind = text.includes('AI 提案：证据冲突') ? 'model-conflict'
    : config.investigation_context.source_count === 3 ? 'conflict-gap'
    : text.includes('资料不足') ? 'unknown'
    : text.includes('日历日差值') ? 'p4-comparison' : 'partial';
  check(text.includes('任务关系与生效计划：待确认'), 'Formal status must remain unconfirmed');
  check(!text.includes('SUPPORTED') && !text.includes('EVIDENCE_VALID'), 'Technical codes leaked into collapsed view');
  check(!/已确认延期|已批准/.test(text), 'False business approval');
  check(await page.locator('.task-investigation-details').getAttribute('open') === null, 'Details should start collapsed');
  check(await page.locator('body').evaluate(el => el.scrollWidth <= innerWidth), 'Horizontal overflow');
  await result.evaluate(el => el.scrollIntoView({block: 'start'}));
  await page.screenshot({path: `${outputDirectory}/after-${kind}-desktop.png`});
  if (kind === 'partial') {
    check(text.includes('AI 提案：支持该关系'), 'Supported model proposal needs an unconfirmed label');
    const cards = page.locator('[data-task-observation]');
    check(await cards.count() === 2, 'Expected both source records');
    check((await cards.nth(1).innerText()).includes('尚未确定'), 'Unresolved date must remain unknown');
    await cards.first().locator('.ask-source-details summary').click();
    check((await cards.first().innerText()).includes('TASK_DEADLINE'), 'Exact field role unavailable');
    await cards.first().scrollIntoViewIfNeeded();
    await page.screenshot({path: `${outputDirectory}/after-field-evidence.png`});
    await cards.first().locator('.ask-source-details summary').click();
    await page.locator('#ask-language').selectOption('en');
    check((await result.innerText()).includes('Task relationships and effective plan: unconfirmed'), 'English labels missing');
    await page.locator('#ask-language').selectOption('zh');
    await page.setViewportSize({width: 390, height: 844});
    await result.evaluate(el => el.scrollIntoView({block: 'start'}));
    check(await page.locator('body').evaluate(el => el.scrollWidth <= innerWidth), 'Mobile overflow');
    await page.screenshot({path: `${outputDirectory}/after-mobile.png`});
    await page.setViewportSize({width: 1440, height: 1000});
  } else if (kind === 'unknown') {
    check(text.includes('本次未检索到可核验的任务记录'), 'Unknown incorrectly establishes absence');
  } else if (kind === 'model-conflict') {
    check(text.includes('AI 提案：证据冲突') && text.includes('日期变动与未批准调整的原文需要独立复核'), 'Model conflict attribution was lost');
  } else if (kind === 'conflict-gap') {
    check(text.includes('AI 提案：证据不足') && text.includes('日期存在冲突'), 'Existing UNKNOWN conflict gap was lost');
  } else {
    check(text.includes('日历日差值') && text.includes('当前生效计划待确认'), 'P4 comparison currentness lost');
  }
  return {kind, visibleText: text, sourceRecords: await page.locator('[data-task-observation]').count()};
}
