// B1-1 Text Source: optional meeting date, duplicate choice, request_id reuse, revision conflict.
const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const script = fs.readFileSync('src/linkloom/ui/static/product.js', 'utf8');

async function setup() {
  const elements = new Map(), calls = [];
  const existing = {source_id: 'source-1', name: 'Weekly sync', meeting_date: null, created_at: '2026-11-19T10:00:00Z'};
  const source = {source_id: 'source-2', name: 'Weekly sync copy', source_version: 'version-2', revision: 2,
    meeting_date: '2026-11-18', meeting_date_basis: 'USER_SELECTED', content: 'Decided: use Wrenwell.\n',
    content_sha256: 'hash', ingestion_status: 'COMPLETE', candidate_count: 0, review_required_count: 0,
    segment_summary: {total: 1, processed: 1, failed: 0, retryable: 0}, error: null, segments: [], evidence: []};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: '', textContent: '', value: '', dataset: {}, listeners: {},
      attributes: new Map(), addEventListener(type, fn) {this.listeners[type] = fn;},
      setAttribute() {}, removeAttribute() {}, hasAttribute() {return false;}, focus() {}, scrollIntoView() {}});
    return elements.get(id);
  }
  const nav = ['sources', 'review'].map(screen => Object.assign(element(screen), {dataset: {screen}}));
  async function fetch(path, options = {}) {
    const data = options.body ? JSON.parse(options.body) : null;
    if (options.method) calls.push({method: options.method, path, data});
    let status = 200, payload;
    if (path === '/api/sources/import') {
      status = data.allow_duplicate ? 202 : 409;
      payload = data.allow_duplicate ? {source}
        : {error: {code: 'SOURCE_DUPLICATE', message: 'duplicate', details: {duplicates: [existing]}}};
    } else if (options.method === 'PUT') {
      status = 409;
      payload = {error: {code: 'VERSION_CONFLICT', message: 'conflict', details: {current_revision: 3}}};
    } else payload = path === '/api/status' ? {ready: true, message: 'ready'}
      : path === '/api/workspaces' ? {workspaces: [{workspace_id: 'a', name: 'Atlas'}]}
      : path === '/api/workspaces/current' ? {workspace: {workspace_id: 'a', name: 'Atlas'}}
      : path === '/api/sources' ? {sources: []}
      : path.startsWith('/api/sources/') ? {source}
      : path === '/api/review/attention' ? {decisions: []}
      : path === '/api/review/candidates' ? {candidates: [], supporting_proposals: []}
      : {items: [], page: {has_more: false}};
    return {ok: status < 400, status, json: async () => payload};
  }
  vm.runInNewContext(script, {document: {documentElement: {lang: 'en'}, getElementById: element,
    querySelectorAll: () => nav}, fetch, console, setTimeout: fn => fn(), window: {setTimeout() {}},
    crypto: {randomUUID: () => '00000000-0000-4000-8000-000000000001'},
    FormData: class {constructor(form) {this.fields = form.fields;} *[Symbol.iterator]() {yield* Object.entries(this.fields || {});}}});
  await tick(); await tick();
  const click = async dataset => {
    await element('page').listeners.click({target: {closest: () => ({dataset})}});
    await tick();
  };
  const submit = async (id, fields, dataset = {}) => {
    const form = element(id); form.fields = fields; Object.assign(form.dataset, dataset);
    await element('page').listeners.submit({preventDefault() {}, target: form, submitter: {dataset: {}}});
    await tick();
  };
  return {element, calls, click, submit};
}

(async () => {
  const ui = await setup();
  const html = ui.element('page').innerHTML;
  assert.match(html, /Meeting date \(optional\)/);
  assert.match(html, /data-action="meeting-date-today"/);
  assert.match(html, /data-action="meeting-date-clear"/);
  assert.match(html, /Plain notes or Markdown work as-is/);
  assert.match(html, /never fills it in for you/);
  assert.equal(ui.calls.length, 0, 'opening the page does not import');

  // "Fill today" is an explicit user action that records its basis; Clear removes both.
  await ui.click({action: 'meeting-date-today', target: 'source'});
  assert.match(ui.element('source-meeting-date').value, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(ui.element('source-meeting-date-basis').value, 'USER_TODAY');
  await ui.click({action: 'meeting-date-clear', target: 'source'});
  assert.equal(ui.element('source-meeting-date').value, '');
  assert.equal(ui.element('source-meeting-date-basis').value, '');

  // Duplicate text prompts with both choices and keeps the draft on screen.
  const pageBefore = ui.element('page').innerHTML;
  await ui.submit('source-create', {name: 'Weekly sync', content: 'Decided: use Wrenwell.\n', input_kind: 'paste',
    meeting_date: '', meeting_date_basis: ''});
  const first = ui.calls.at(-1);
  assert.equal(first.path, '/api/sources/import');
  assert.equal(first.data.request_id, 'import-00000000-0000-4000-8000-000000000001');
  assert.equal('meeting_date' in first.data, false, 'an empty date is not sent or defaulted');
  assert.equal(ui.element('page').innerHTML, pageBefore, 'the import form is not wiped');
  const prompt = ui.element('source-create-status').innerHTML;
  assert.match(prompt, /View existing meeting/);
  assert.match(prompt, /data-action="inspect-source" data-id="source-1"/);
  assert.match(prompt, /Import as a separate meeting/);

  await ui.click({action: 'import-duplicate'});
  const second = ui.calls.at(-1);
  assert.equal(second.data.allow_duplicate, true);
  assert.equal(second.data.request_id, first.data.request_id, 'the duplicate prompt did not consume the request id');
  assert.match(ui.element('page').innerHTML, /Meeting date: 2026-11-18 · chosen by you/);
  assert.match(ui.element('page').innerHTML, /name="expected_revision" value="2"/);

  // A stale edit keeps the user's text and explains the conflict.
  const detail = ui.element('page').innerHTML;
  await ui.submit('source-update', {name: 'Renamed', content: 'Decided: use Wrenwell.\n', meeting_date: '',
    meeting_date_basis: '', expected_revision: '2'}, {id: 'source-2'});
  const put = ui.calls.at(-1);
  assert.equal(put.method, 'PUT');
  assert.equal(put.data.expected_revision, 2);
  assert.equal(put.data.meeting_date, null, 'clearing the date is sent as an explicit null');
  assert.equal(ui.element('page').innerHTML, detail, 'unsaved edits stay in the form');
  assert.match(ui.element('source-update-status').innerHTML, /changed elsewhere/);
  console.log('text source import DOM test passed');
})().catch(error => { console.error(error); process.exit(1); });
