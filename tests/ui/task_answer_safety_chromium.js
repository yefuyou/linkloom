// Run after task_answer_chromium.js on the dedicated public partial fixture.
// This updates only that disposable synthetic database via the normal API.
async (page, outputDirectory = 'output/playwright') => {
  const check = (value, message) => {if (!value) throw new Error(message);};
  const base = new URL(page.url()).origin;
  check(new URL(base).hostname === '127.0.0.1', 'Synthetic local host required');
  const current = await (await page.request.get(base + '/api/workspaces/current')).json();
  const workspace = current.workspace.workspace_id;
  const original = await page.locator('#ask-result').innerText();
  check(original.includes('林枫') && original.includes('2029-06-20'), 'Run public partial acceptance first');
  await page.locator('[data-action="inspect-source"]').first().click();
  await page.locator('#update-content').waitFor();
  check((await page.locator('#update-content').inputValue()).includes('完成日志迁移'), 'Source button opened the wrong record');
  await page.screenshot({path: `${outputDirectory}/after-original-source.png`});
  await page.getByRole('button', {name: 'Ask', exact: true}).click();
  const read = page.waitForResponse(r => new URL(r.url()).pathname.startsWith('/api/product/ask/runs/'));
  await page.locator('[data-action="reopen-task"]').click();
  const valid = (await (await read).json()).result;
  check(valid.status === 'PARTIAL', 'Saved result did not revalidate');
  await page.locator('.task-answer').waitFor();
  const runId = valid.agent_run_id;
  await page.reload();
  await page.getByRole('heading', {name: 'Workspace sources', exact: true}).waitFor();
  await page.getByRole('button', {name: 'Ask', exact: true}).click();
  await page.locator('#ask-result').waitFor({state: 'attached'});
  check(!((await page.locator('#ask-result').innerText()).includes('林枫')), 'Reload showed cached source text');
  await page.locator('[data-action="reopen-task"]').click();
  await page.locator('.task-answer').waitFor();
  const sources = (await (await page.request.get(base + '/api/sources')).json()).sources;
  const detail = (await (await page.request.get(base + '/api/sources/' + sources[0].source_id)).json()).source;
  const update = await page.request.put(base + '/api/sources/' + detail.source_id, {data: {
    name: detail.name, content: detail.content + '\n[2029-06-06T09:00:00Z] 林枫: 合成安全验收：本来源已更新。',
  }});
  check(update.status() === 202, 'Synthetic source update failed');
  const blockedRead = page.waitForResponse(r => new URL(r.url()).pathname.includes('/api/product/ask/runs/'));
  await page.locator('[data-action="reopen-task"]').click();
  const blocked = (await (await blockedRead).json()).result;
  check(blocked.status === 'BLOCKED' && blocked.evidence.length === 0 && !blocked.task_review, 'Server released stale evidence');
  await page.getByRole('heading', {name: '任务调查已停止'}).waitFor();
  const stopped = await page.locator('#ask-result').innerText();
  check(stopped.includes('旧任务详情已隐藏') && !/林枫|2029-06-20|2029-06-24/.test(stopped), 'Blocked view retained old data');
  await page.locator('#ask-result').evaluate(el => el.scrollIntoView({block: 'start'}));
  await page.screenshot({path: `${outputDirectory}/after-blocked.png`});
  const created = await page.request.post(base + '/api/workspaces', {data: {name: 'Public synthetic isolation'}});
  check(created.status() === 201, 'Could not create synthetic isolation workspace');
  const other = (await created.json()).workspace.workspace_id;
  await page.reload();
  await page.getByRole('heading', {name: 'Workspace sources', exact: true}).waitFor();
  await page.locator('#workspace-picker').selectOption(other);
  await page.getByRole('heading', {name: 'Workspace sources', exact: true}).waitFor();
  await page.getByRole('button', {name: 'Ask', exact: true}).click();
  await page.locator('#ask-form').waitFor();
  check(await page.locator('[data-action="reopen-task"]').count() === 0, 'Cross-workspace result pointer leaked');
  const denied = await page.request.get(base + `/api/product/ask/runs/${runId}?workspace_id=${other}`);
  const denial = await denied.json();
  check([403, 404, 409].includes(denied.status()) ||
    (denial.result?.status === 'BLOCKED' && !denial.result.task_review && !denial.result.evidence?.length),
    'Other workspace could read the original run');
  check(!(await page.locator('#page').innerText()).includes('林枫'), 'Other workspace shows source data');
  await page.screenshot({path: `${outputDirectory}/after-workspace-isolation.png`});
  return {sourceOpened: true, savedRead: valid.status, updatedRead: blocked.status,
    error: blocked.error.code, otherWorkspaceRead: denied.status(), workspace};
}
