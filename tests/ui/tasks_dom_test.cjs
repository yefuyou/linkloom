/* Native DOM harness: exercise delegated page actions against an offline HTTP double. */
const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/linkloom/ui/static/tasks.js', 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const fingerprint = 'a'.repeat(64);
const page = items => ({items, page: {limit: 20, offset: 0, next_offset: null, has_more: false}});
const reference = (suffix = 'A') => ({source_id: 'source-' + suffix, source_name: suffix + ' 会议',
  source_version_id: 'version-' + suffix, meeting_time: '2026-10-08T02:00:00+00:00',
  line_start: 1, line_end: 2, quote: suffix + ' 原文 <img src=x onerror=alert(1)>', quote_sha256: 'hash-' + suffix, source_sha256: 'source-hash-' + suffix});
function candidate(id, owner = '李明') {
  return {candidate_id: id, candidate_fingerprint: fingerprint, fingerprint, is_current: true, reviewed: false,
    review_status: 'pending', evidence_count: 2, source_summary: [{source_name: '10/08 周会', meeting_time: '2026-10-08T02:00:00+00:00'}],
    candidate: {candidate_id: id, title: '联调报告', owner, deadline: '2026-10-12', deadline_text: '10/12 前',
      confidence: 'LOW', confidence_reason: '负责人需要复核'}};
}
function element() {
  return {innerHTML: '', textContent: '', value: '', title: '', disabled: false, dataset: {}, listeners: {},
    setAttribute(name, value) {this[name] = value;}, addEventListener(name, fn) {this.listeners[name] = fn;},
    querySelector() {return {value: '', focus() {}};}, querySelectorAll() {return [];}, focus() {}};
}
async function harness(options = {}) {
  const elements = Object.fromEntries(['tasks-app', 'workspace', 'identity', 'notice', 'refresh-tasks'].map(key => [key, element()]));
  const h = {elements, calls: [], prompts: [], names: options.names || ['审核甲', '审核乙', '审核丙'],
    pending: [candidate('a'), candidate('b', '王芳')], processed: [], events: [], inject: null, hook: null,
    task: {id: 'task-1', workspace_id: 'w1', title: '联调报告', owner: '李明', deadline: '2026-10-12', deadline_text: '10/12 前', status: 'open'},
    saved: options.saved || ''};
  const storage = Object.prototype.hasOwnProperty.call(options, 'storage') ? options.storage : {
    getItem() {return h.saved;}, setItem(_key, value) {h.saved = value;},
  };
  const document = {body: {dataset: {}}, getElementById(key) {return elements[key] || null;}};
  h.summary = () => ({task: clone(h.task), review_status: 'approved', review_revision: '7', candidate_id: 'a', candidate_fingerprint: fingerprint,
    field_sources: {title: {origin: 'source'}, owner: {origin: 'human'}, deadline: {origin: 'source'}},
    field_source_summary: {owner: {sources: [{source_name: 'A 会议', meeting_time: '2026-10-08T02:00:00+00:00'}]}},
    human_declarations: {same_task: true}, machine_validation: {source: true}});
  const response = (payload, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => clone(payload)});
  const fetcher = async (target, opts = {}) => {
    const url = new URL(target, 'http://local.test');
    const body = opts.body ? JSON.parse(opts.body) : null;
    const call = {path: url.pathname, query: Object.fromEntries(url.searchParams), method: opts.method || 'GET', body};
    h.calls.push(call);
    if (h.hook) {const value = await h.hook(call); if (value) return value;}
    if (h.inject && h.inject.match(call)) {
      const error = h.inject; h.inject = null;
      if (error.network) throw new Error('offline');
      return response({error: {code: error.code, message: 'injected', details: error.details}}, error.status);
    }
    if (url.pathname === '/api/workspaces') return response({workspaces: [{workspace_id: 'w1', name: '工作区一'}, {workspace_id: 'w2', name: '工作区二'}]});
    if (url.pathname === '/api/workspaces/current') return response({workspace: {workspace_id: 'w1', name: '工作区一'}});
    if (url.pathname === '/api/product/task-candidates') return response(page(call.query.workspace_id === 'w2' ? [] : call.query.state === 'processed' ? h.processed : h.pending));
    if (/\/evidence$/.test(url.pathname)) return response({...page([reference('A'), reference('B')]), evidence_basis: 'current_source'});
    if (url.pathname === '/api/product/tasks') return response(page([h.summary()]));
    if (/\/events$/.test(url.pathname)) return response(page(h.events));
    if (/\/merge-preview$/.test(url.pathname)) return response({current_values: {title: h.task.title, owner: h.task.owner, deadline: {deadline: h.task.deadline}},
      candidate_values: {title: '联调报告', owner: '王芳', deadline: {deadline: '2026-10-16'}}, review_revision: '7', preview_token: 'merge-token'});
    if (/\/undo-preview$/.test(url.pathname)) return response({restored_values: {title: '联调报告', owner: '李明', deadline: {deadline: '2026-10-12'}},
      restored_review_status: 'approved', review_revision: '8', preview_token: 'undo-token'});
    if (/\/review$/.test(url.pathname)) {
      const id = url.pathname.split('/')[4];
      const selected = h.pending.find(item => item.candidate_id === id);
      h.pending = h.pending.filter(item => item.candidate_id !== id);
      if (body.action === 'reject') {
        h.processed.push({...selected, review_status: 'rejected', reviewed: true, reason: body.reason, reviewer: body.reviewer});
        return response({review_status: 'rejected'});
      }
      if (body.action === 'merge') {h.task.owner = '王芳'; h.task.deadline = '2026-10-16';}
      if (body.patch) Object.assign(h.task, body.patch);
      return response({...h.summary(), merge_id: body.action === 'merge' ? 'merge-1' : undefined});
    }
    if (/\/undo$/.test(url.pathname)) {h.task.owner = '李明'; h.task.deadline = '2026-10-12'; return response(h.summary());}
    if (/\/status$/.test(url.pathname)) {h.task.status = body.status; return response({task: clone(h.task)});}
    if (/\/tasks\/task-1$/.test(url.pathname)) return response(h.summary());
    throw new Error('Unexpected fetch: ' + target);
  };
  const window = {LINKLOOM_TASKS_NO_AUTO_START: true};
  vm.runInNewContext(source, {window, URLSearchParams, console});
  h.helpers = window.LinkLoomTasks.helpers;
  h.app = window.LinkLoomTasks.createApp({document, fetch: fetcher, storage,
    prompt(message, previous) {h.prompts.push({message, previous}); return h.names.shift() ?? null;},
    location: {search: options.search || ''}, history: {replaceState(_a, _b, value) {h.url = value;}}});
  h.click = (action, data = {}) => h.app.handleAction({dataset: {action, ...data}});
  h.input = (field, value) => h.app.handleInput({dataset: {field}, value});
  h.change = (field, value) => h.app.handleChange({dataset: {field}, value});
  h.check = (field, checked = true) => h.app.handleChange({dataset: {mergeField: field}, checked});
  h.writes = () => h.calls.filter(call => /\/review$|\/undo$|\/status$/.test(call.path));
  await h.app.start();
  return h;
}
const cases = [];
const test = (name, run) => cases.push({name, run});

test('first actual write asks identity once; later status uses actor; identity change applies', async () => {
  const h = await harness();
  assert.equal(h.prompts.length, 0);
  await h.click('candidate-create', {index: '0'});
  assert.equal(h.app.state.form.reason, '按原文确认，未修改');
  assert.doesNotMatch(h.elements['tasks-app'].innerHTML, /data-field="reviewer"|data-field="actor"/);
  await h.click('confirm-review');
  assert.equal(h.prompts.length, 1);
  assert.equal(h.prompts[0].message, '你的名字');
  assert.equal(h.saved, '审核甲');
  assert.equal(h.writes()[0].body.reviewer, '审核甲');
  assert.equal(h.writes()[0].body.reason, '按原文确认，未修改');
  assert.equal(h.writes()[0].body.patch, undefined);
  assert.match(h.elements.identity.textContent, /以 审核甲 身份操作 · 更改/);
  await h.click('status-open');
  assert.equal(h.app.state.form.reason, '');
  await h.change('status', 'done'); h.input('reason', '已经完成');
  await h.click('status-submit');
  assert.equal(h.prompts.length, 1);
  assert.equal(h.writes()[1].body.actor, '审核甲');
  h.elements.identity.onclick();
  await h.click('candidate-reject', {index: '0'}); h.input('reason', '不列入正式待办');
  await h.click('confirm-review');
  assert.equal(h.writes()[2].body.reviewer, '审核乙');
  assert.equal(h.saved, '审核乙');
});

test('saved identity bypasses prompt and cancelled/blank identity never submits', async () => {
  const h = await harness({saved: '已有身份'});
  await h.click('candidate-create', {index: '0'}); await h.click('confirm-review');
  assert.equal(h.prompts.length, 0); assert.equal(h.writes()[0].body.reviewer, '已有身份');
  for (const name of [null, '   ']) {
    const blank = await harness({names: [name]});
    await blank.click('candidate-create', {index: '0'}); await blank.click('confirm-review');
    assert.equal(blank.writes().length, 0);
  }
});

test('storage read failure, write failure and missing storage re-ask before every write', async () => {
  for (const storage of [
    {getItem() {throw new Error('privacy');}, setItem() {}},
    {getItem() {return '';}, setItem() {throw new Error('quota');}}, null,
  ]) {
    const h = await harness({storage});
    await h.click('candidate-create', {index: '0'}); await h.click('confirm-review');
    await h.click('status-open'); await h.change('status', 'done'); h.input('reason', '人工标记完成'); await h.click('status-submit');
    assert.equal(h.prompts.length, 2); assert.equal(h.writes()[0].body.reviewer, '审核甲');
    assert.equal(h.writes()[1].body.actor, '审核乙'); assert.equal(h.app.state.storageAvailable, false);
  }
});

test('confirm patch reasons track only changed fields and preserve editable reasons', async () => {
  const h = await harness(); await h.click('candidate-create', {index: '0'});
  h.input('owner', '王芳'); assert.equal(h.app.state.form.reason, '人工修正：负责人');
  h.input('deadline', '2026-10-16'); assert.equal(h.app.state.form.reason, '人工修正：负责人、截止时间');
  h.input('reason', '人工核对了新的执行安排'); h.input('title', '联调验收报告');
  assert.equal(h.app.state.form.reason, '人工核对了新的执行安排');
  await h.click('confirm-review');
  assert.deepEqual(h.writes()[0].body.patch, {title: '联调验收报告', owner: '王芳', deadline: '2026-10-16', deadline_text: '10/12 前'});
  assert.equal(h.writes()[0].body.reason, '人工核对了新的执行安排');
});

test('merge is explicit, requires preview, invalidates preview on field/target change, sends token', async () => {
  const h = await harness(); await h.click('candidate-merge', {index: '1'});
  assert.equal(h.app.state.form.target, ''); assert.deepEqual(Array.from(h.app.state.form.applyFields), []);
  assert.equal(h.app.state.form.preview, null); assert.equal(h.prompts.length, 0);
  await h.change('mergeTarget', 'task-1'); await h.check('owner'); await h.check('deadline');
  assert.equal(h.app.state.form.reason, '合并入已有任务：负责人、截止时间');
  await h.click('merge-preview'); assert.equal(h.prompts.length, 0);
  assert.match(h.elements['tasks-app'].innerHTML, /李明/); assert.match(h.elements['tasks-app'].innerHTML, /王芳/);
  await h.check('owner', false); assert.equal(h.app.state.form.preview, null);
  await h.check('owner'); await h.click('merge-preview');
  await h.change('mergeTarget', 'task-1'); assert.equal(h.app.state.form.preview, null);
  await h.click('merge-preview'); h.input('reason', '我核实两次会议说的是同一任务');
  await h.click('merge-confirm');
  const merged = h.writes()[0].body;
  assert.equal(merged.action, 'merge'); assert.deepEqual(merged.apply_fields, ['owner', 'deadline']);
  assert.equal(merged.expected_preview_token, 'merge-token'); assert.equal(merged.expected_review_revision, '7');
  assert.equal(merged.reviewer, '审核甲'); assert.equal(merged.reason, '我核实两次会议说的是同一任务');
  assert.match(h.url, /merge=merge-1/);
});

test('reject, undo and status have no default reason and require handwritten text', async () => {
  const h = await harness(); await h.click('candidate-reject', {index: '0'});
  assert.equal(h.app.state.form.reason, ''); await h.click('confirm-review'); assert.equal(h.writes().length, 0);
  assert.equal(h.prompts.length, 0); assert.match(h.elements.notice.innerHTML, /REASON_REQUIRED/);
  h.input('reason', '不接受该候选'); await h.click('confirm-review'); assert.equal(h.writes()[0].body.action, 'reject');
  await h.app.openTask('task-1'); await h.click('undo-preview', {merge: 'merge-1'});
  assert.equal(h.app.state.form.reason, ''); await h.click('undo-confirm'); assert.equal(h.writes().length, 1);
  h.input('reason', '人工确认撤销错误关联'); await h.click('undo-confirm');
  assert.equal(h.writes()[1].body.expected_preview_token, 'undo-token');
  await h.click('status-open'); assert.equal(h.app.state.form.reason, '');
  await h.change('status', 'blocked'); await h.click('status-submit'); assert.equal(h.writes().length, 2);
  h.input('reason', '等待上游数据'); await h.click('status-submit'); assert.equal(h.writes()[2].body.actor, '审核甲');
  await h.change('candidateState', 'processed'); assert.match(h.elements['tasks-app'].innerHTML, /已拒绝/);
  assert.match(h.elements['tasks-app'].innerHTML, /不接受该候选/);
});

test('all stale/conflict codes clear old form and never retry mutations', async () => {
  for (const code of ['CANDIDATE_CHANGED', 'REVIEW_CHANGED', 'PREVIEW_STALE', 'ALREADY_REVIEWED', 'SOURCE_INVALID', 'REVIEW_CONFLICT']) {
    const h = await harness(); await h.click('candidate-create', {index: '0'});
    h.inject = {match: call => /\/review$/.test(call.path), code, status: 409};
    await h.click('confirm-review'); assert.equal(h.writes().length, 1); assert.equal(h.app.state.form, null);
    assert.match(h.elements.notice.innerHTML, new RegExp(code));
  }
});

test('UNDO_BLOCKED shows ordered merge versus later manual operation without offering force undo', async () => {
  for (const operation of ['merge', 'manual_status']) {
    const h = await harness(); await h.app.openTask('task-1');
    h.inject = {match: call => /\/undo-preview$/.test(call.path), code: 'UNDO_BLOCKED', status: 409,
      details: {blocking_count: 1, first_blocking_operation: {operation}}};
    await h.click('undo-preview', {merge: 'merge-1'});
    assert.equal(h.app.state.form, null); assert.match(h.elements.notice.innerHTML, /UNDO_BLOCKED/);
    assert.match(h.elements.notice.innerHTML, operation === 'merge' ? /相反顺序/ : /保留后续变更/);
    assert.equal(h.writes().length, 0);
  }
});

test('400, 404, 503 and network errors are visible and no mutation is auto-retried', async () => {
  for (const failure of [{code: 'INVALID_REQUEST', status: 400}, {code: 'RESOURCE_NOT_FOUND', status: 404},
    {code: 'TASK_BUSY', status: 503}, {network: true, code: 'NETWORK_ERROR', status: 0}]) {
    const h = await harness(); await h.click('candidate-create', {index: '0'});
    h.inject = {...failure, match: call => /\/review$/.test(call.path)};
    await h.click('confirm-review'); assert.equal(h.writes().length, 1);
    assert.match(h.elements.notice.innerHTML, new RegExp(failure.code));
  }
});

test('evidence, LOW reason, provenance and bounded history are rendered as escaped text', async () => {
  const h = await harness();
  assert.match(h.elements['tasks-app'].innerHTML, /LOW · 低置信度/); assert.match(h.elements['tasks-app'].innerHTML, /负责人需要复核/);
  await h.click('candidate-evidence', {index: '0'});
  let html = h.elements['tasks-app'].innerHTML;
  assert.match(html, /A 原文 &lt;img/); assert.match(html, /B 原文 &lt;img/); assert.match(html, /行 1–2/);
  assert.doesNotMatch(html, /<img src=x/); assert.match(html, /来源版本：version-A/); assert.match(html, /hash-A/);
  h.events = [{id: 'e1', event_type: 'owner_changed', old_value: '李明', new_value: '王芳',
    meeting_time: '2026-10-10T02:00:00+00:00', created_at: '2026-10-20T09:00:00+00:00', actor: '审核人只在历史', reason: '人工改正',
    operation: 'merge', operation_id: 'merge-1', evidence_role: 'context', before_evidence_role: 'source', after_evidence_role: 'context',
    before_evidence: [reference('A')], after_evidence: [reference('B')], before_evidence_count: 3, after_evidence_count: 4, evidence_truncated: true}];
  await h.app.openTask('task-1'); html = h.elements['tasks-app'].innerHTML;
  assert.match(html, /人工修改/); assert.match(html, /审核人只在历史/); assert.match(html, /背景原文/);
  assert.match(html, /共有 7 段/); assert.match(html, /最多展示 5 段/); assert.match(html, /会议时间：2026-10-10/);
  assert.match(html, /操作时间：2026-10-20/); assert.match(html, /机器核验/); assert.match(html, /人工声明/);
});

test('owner filters and all list pagination are sent to the API, no browser-side full-list slicing', async () => {
  const h = await harness(); await h.click('tab-tasks'); await h.change('ownerMode', 'unassigned'); await h.click('filter-tasks');
  assert.equal(h.calls.at(-1).query.owner, '');
  await h.change('ownerMode', 'named'); h.input('ownerFilter', '王芳'); await h.change('statusFilter', 'blocked'); await h.click('filter-tasks');
  assert.equal(h.calls.at(-1).query.owner, '王芳'); assert.equal(h.calls.at(-1).query.status, 'blocked');
  await h.click('task-page', {offset: '20'}); assert.equal(h.calls.at(-1).query.offset, '20'); assert.equal(h.calls.at(-1).query.limit, '20');
  await h.click('tab-candidates'); await h.click('candidate-page', {offset: '40'}); assert.equal(h.calls.at(-1).query.offset, '40');
  await h.app.openTask('task-1'); await h.click('event-page', {offset: '20'}); assert.equal(h.calls.at(-1).query.offset, '20');
});

test('workspace switch clears old evidence/forms and ignores late earlier-workspace responses', async () => {
  const h = await harness(); let resolveOld;
  h.hook = call => call.path === '/api/product/task-candidates' && call.query.workspace_id === 'w1' ?
    new Promise(resolve => {resolveOld = () => resolve({ok: true, status: 200, json: async () => page([candidate('old-secret')])});}) : null;
  const old = h.app.loadCandidates();
  await new Promise(resolve => setImmediate(resolve));
  await h.app.changeWorkspace('w2'); resolveOld(); await old;
  assert.equal(h.app.state.workspace.workspace_id, 'w2'); assert.equal(h.app.state.candidates.items.length, 0);
  assert.equal(h.app.state.form, null); assert.equal(h.app.state.detail, null); assert.deepEqual(Object.keys(h.app.state.evidence), []);
  assert.doesNotMatch(h.elements['tasks-app'].innerHTML, /old-secret/);
});

test('busy state blocks duplicate submit and workspace switching until result known', async () => {
  const h = await harness(); await h.click('candidate-create', {index: '0'}); let release;
  h.hook = call => /\/review$/.test(call.path) ? new Promise(resolve => {release = () => resolve({ok: true, status: 201, json: async () => h.summary()});}) : null;
  const writing = h.click('confirm-review'); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.app.state.busy, true); assert.equal(h.elements.workspace.disabled, true);
  await h.click('confirm-review'); await h.app.changeWorkspace('w2'); assert.equal(h.writes().length, 1);
  assert.equal(h.app.state.workspace.workspace_id, 'w1'); release(); await writing; assert.equal(h.app.state.busy, false);
});

test('undo 400 retains its valid preview and manual reason without crashing', async () => {
  const h = await harness(); await h.app.openTask('task-1'); await h.click('undo-preview', {merge: 'merge-1'});
  h.input('reason', '人工撤销错误关联');
  h.inject = {match: call => /\/undo$/.test(call.path), code: 'INVALID_REQUEST', status: 400};
  await h.click('undo-confirm');
  assert.equal(h.app.state.form.kind, 'undo'); assert.equal(h.app.state.form.preview.preview_token, 'undo-token');
  assert.equal(h.app.state.form.reason, '人工撤销错误关联'); assert.match(h.elements.notice.innerHTML, /INVALID_REQUEST/);
  assert.match(h.elements['tasks-app'].innerHTML, /undo-confirm/); assert.equal(h.writes().length, 1);
});

test('zero-field merge keeps its receipt undo entry and detail URL survives reload', async () => {
  const h = await harness(); await h.click('candidate-merge', {index: '1'}); await h.change('mergeTarget', 'task-1');
  await h.click('merge-preview'); await h.click('merge-confirm');
  assert.deepEqual(h.writes()[0].body.apply_fields, []); assert.match(h.url, /merge=merge-1/);
  assert.match(h.elements['tasks-app'].innerHTML, /receipt-undo-preview/);
  await h.click('undo-preview', {merge: 'merge-1'}); assert.equal(h.app.state.form.kind, 'undo');
  const reloaded = await harness({search: h.url.substring(h.url.indexOf('?'))});
  assert.equal(reloaded.app.state.receipt.merge_id, 'merge-1');
  assert.match(reloaded.elements['tasks-app'].innerHTML, /receipt-undo-preview/);
});

test('historical restoration proof never overrides the current needs-review state', async () => {
  for (const historical of ['needs_review', 'approved']) {
    const h = await harness(), original = h.summary;
    h.summary = () => ({...original(), review_status: 'needs_review', machine_validation: null,
      human_declarations: {withdraws_merge: 'merge-1'}, merge_machine_validation: {restored_review_status: historical}});
    await h.app.openTask('task-1');
    const html = h.elements['tasks-app'].innerHTML;
    assert.match(html, /撤销时的历史核验/); assert.match(html, /当前有效性以本页审核状态为准/);
    assert.match(html, /<span class="badge warn">待复核<\/span>/);
    if (historical === 'approved') assert.match(html, /当时恢复的来源绑定已通过核验/);
    else {
      assert.match(html, /当时恢复来源未通过核验，任务被标为待复核/);
      assert.doesNotMatch(html, /当时恢复的来源绑定已通过核验/);
    }
  }
});

test('successful mutation followed by failed reads preserves receipt and never offers resubmit', async () => {
  const h = await harness(); await h.click('candidate-merge', {index: '1'}); await h.change('mergeTarget', 'task-1');
  await h.click('merge-preview');
  h.hook = call => call.path === '/api/product/task-candidates' && h.writes().length > 0 ?
    {ok: false, status: 503, json: async () => ({error: {code: 'TASK_BUSY'}})} : null;
  await h.click('merge-confirm');
  assert.equal(h.writes().length, 1); assert.equal(h.app.state.form, null);
  assert.match(h.url, /merge=merge-1/); assert.match(h.elements.notice.innerHTML, /操作已完成，但页面刷新未完成/);
});

(async () => {
  for (const {name, run} of cases) {await run(); console.log('PASS ' + name);}
  console.log(`PASS: ${cases.length} formal-task DOM scenarios`);
})().catch(error => {console.error(error); process.exitCode = 1;});
