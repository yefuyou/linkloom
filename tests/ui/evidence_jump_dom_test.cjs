const {strict: assert} = require('node:assert');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

// Event-driven DOM harness: parse the rendered markup, click its real buttons,
// and record focus/scroll calls. No production functions are exported for tests.
const decode = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
class Element {
  constructor(tag = 'div', attrs = {}) { this.tagName = tag.toUpperCase(); this.attrs = {...attrs}; this.children = []; this.listeners = {}; this.value = attrs.value || ''; }
  get id() { return this.attrs.id; }
  get dataset() { return Object.fromEntries(Object.entries(this.attrs).filter(([k]) => k.startsWith('data-')).map(([k,v]) => [k.slice(5).replace(/-([a-z])/g, (_,c) => c.toUpperCase()), v])); }
  get isConnected() { return this.tagName === 'BODY' || Boolean(this.parent?.isConnected); }
  get disabled() { return Object.hasOwn(this.attrs, 'disabled'); }
  get innerHTML() { return this.children.map(c => typeof c === 'string' ? c : c.outerHTML).join(''); }
  get outerHTML() { return `<${this.tagName.toLowerCase()}${Object.entries(this.attrs).map(([k,v]) => ` ${k}="${String(v).replace(/"/g, '&quot;')}"`).join('')}>${this.innerHTML}</${this.tagName.toLowerCase()}>`; }
  set innerHTML(html) {
    for (const child of this.children) if (typeof child !== 'string') child.parent = null;
    this.children = []; const stack = [this];
    for (const token of String(html).match(/<[^>]*>|[^<]+/g) || []) {
      if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      if (!token.startsWith('<')) { stack.at(-1).children.push(token); continue; }
      const tag = /^<([\w-]+)/.exec(token)?.[1]; if (!tag) continue;
      const attrs = {};
      for (const match of token.slice(tag.length + 1, -1).matchAll(/([^\s=/>]+)(?:="([^"]*)"|='([^']*)'|=([^\s>]+))?/g)) attrs[match[1]] = decode(match[2] ?? match[3] ?? match[4] ?? '');
      const child = new Element(tag, attrs); stack.at(-1).appendChild(child);
      if (!['input','br','hr','img','meta','link'].includes(tag) && !token.endsWith('/>')) stack.push(child);
    }
  }
  get textContent() { return this.children.map(c => typeof c === 'string' ? decode(c) : c.textContent).join(''); }
  set textContent(text) { this.innerHTML = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
  appendChild(node) { node.parent = this; this.children.push(node); return node; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; }
  setAttribute(k,v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return Object.hasOwn(this.attrs,k); }
  matches(selector) {
    if (selector.includes(' ')) selector = selector.split(' ').at(-1);
    const attr = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(selector);
    if (attr) return this.hasAttribute(attr[1]) && (attr[2] === undefined || this.attrs[attr[1]] === attr[2]);
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    if (selector.startsWith('.')) return (this.attrs.class || '').split(' ').includes(selector.slice(1));
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(c => typeof c === 'string' ? [] : [...(c.matches(selector) ? [c] : []), ...c.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  async dispatch(type, event = {}) {
    event.target ||= this; event.preventDefault ||= () => {}; event.type = type;
    if (this['on' + type]) await this['on' + type](event);
    for (const callback of this.listeners[type] || []) await callback(event);
    if (type === 'click' && this.parent) await this.parent.dispatch(type, event);
  }
  click() { return this.dispatch('click'); }
  focus(options) { focused = this; this.focusOptions = options; }
  scrollIntoView(options) { this.scrolled = options; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  checkValidity() { return true; }
}
let focused;
const tick = () => new Promise(resolve => setImmediate(resolve));
const quote = '采用青竹😀 <script>alert(1)</script>';
const text = `[2026-10-08T10:00:00Z] A: ${quote}\r\n[2026-10-08T10:01:00Z] B: ${quote}`;
const offset = Array.from(`[2026-10-08T10:00:00Z] A: ${quote}\r\n[2026-10-08T10:01:00Z] B: `).length;
const runtimeStart = Array.from(`[2026-10-08T10:00:00Z] A: ${quote}\r\n`).length;
const runtimeEvidence = {evidence_ref: 'ev_v2_existing_line_2', source_id: 'src-1', source_version: 'version-1', source_name: 'meeting.txt', quote: `[2026-10-08T10:01:00Z] B: ${quote}`, line_start: 2, line_end: 2};
const evidence = {evidence_ref: 'existing-ref-2', source_id: 'src-1', source_version: 'version-1', source_name: 'meeting.txt', quote, line_start: 2, line_end: 2};
function source() { return {workspace_id: 'a', source_id: 'src-1', name: 'meeting.txt', active: true, source_version: 'version-1', content_sha256: 'hash', content: text,
  evidence_jump: {validated: true, workspace_id: 'a', source_id: 'src-1', source_version: 'version-1', content_sha256: 'hash', spans: [
    {...evidence, evidence_ref: 'existing-ref-1', char_start: 24, char_end: 24 + Array.from(quote).length},
    {...evidence, char_start: offset, char_end: offset + Array.from(quote).length},
    {...runtimeEvidence, char_start: runtimeStart, char_end: Array.from(text).length}]}}; }
const candidate = {candidate_id: 'candidate-1', source_id: 'src-1', source: {name: 'meeting.txt'}, evidence, claim_type: 'DECISION', status: 'PENDING_REVIEW', subject: 'Launch', relation: 'uses supplier', proposed_value: '青竹', valid_from: '2026-10-08', current_unresolved_fields: [], review_reasons: []};
function result(task = false) { return {workspace_id: 'a', path: task ? 'TASK_INVESTIGATION' : 'SOURCE_RELATION_INVESTIGATION', status: task ? 'PARTIAL' : 'COMPLETED', query: 'What changed?',
  reviewer: {accepted: true}, scope: {subject: 'Launch', relation: 'uses supplier'}, evidence: [task ? evidence : runtimeEvidence], uncertainty: {unknown_fields: []}, preparation: {execution_kind: 'FAKE'},
  source_investigation: task ? null : {answer: '青竹', facts: [], conflicts: {}, trace: [{quote: 'OLD_TRACE_QUOTE'}], plan: {subgoals: []}},
  task_review: task ? {observations: [{observation_id: 'observation-2', raw_text: 'OLD_PROPOSAL_QUOTE', description: 'Ship', owner: 'B', provenance: {...evidence, artifact_version_id: 'version-1'}}], proposals: [{predicate: 'SAME_TASK', evidence_status: 'EVIDENCE_VALID', proposed_verdict: 'SUPPORTED', subject: {observation_id: 'observation-2'}, object: {observation_id: 'observation-2'}, supporting_evidence: [{quote: 'OLD_SUPPORT_QUOTE'}], counter_evidence: [{quote: 'OLD_COUNTER_QUOTE'}]}], relations: [{quote: 'OLD_RELATION_QUOTE'}], steps: [], next_actions: []} : null}; }

async function setup(entry = 'candidate') {
  const body = new Element('body');
  body.innerHTML = '<select id="workspace-picker"></select><select id="interface-language"></select><button id="new-workspace"></button><span id="status-live"></span><span id="review-count"></span><span id="provider-state"></span><div id="page"></div><nav><button data-screen="sources"></button><button data-screen="review"></button><button data-screen="ask"></button></nav>';
  const doc = {body, documentElement: {lang: 'en'}, getElementById: id => body.querySelector('#' + id), querySelectorAll: selector => body.querySelectorAll(selector), createElement: tag => new Element(tag)};
  let workspace = 'a', sourceRead = async () => ({source: source()});
  const calls = [], events = {};
  async function fetch(path, options = {}) {
    calls.push({path, ...options});
    if (path === '/api/sources/src-1') { const payload = await sourceRead(); return {ok: !payload.error, status: payload.status || 200, json: async () => payload}; }
    if (path === '/api/workspaces/current' && options.method) workspace = JSON.parse(options.body).workspace_id;
    const payload = path === '/api/workspaces' ? {workspaces: [{workspace_id: 'a', name: 'Atlas'}, {workspace_id: 'b', name: 'Other'}]}
      : path === '/api/workspaces/current' ? {workspace: {workspace_id: workspace, name: 'Atlas'}}
      : path === '/api/status' ? {ready: false}
      : path === '/api/review/candidates' ? {candidates: [candidate]}
      : path === '/api/review/candidates/candidate-1' ? {candidate}
      : path === '/api/review/attention' ? {decisions: []}
      : path === '/api/sources' ? {sources: []}
      : path === '/api/product/ask/config' ? {workspace_id: workspace, scopes: [], agent: {available: false}}
      : path === '/api/product/ask' ? {result: result(entry === 'task')}
      : {};
    return {ok: true, status: 200, json: async () => payload};
  }
  const window = {location: {hash: entry === 'candidate' ? '#review' : '#ask'}, addEventListener: (name, fn) => events[name] = fn, setTimeout() {}};
  class FormData {
    constructor(form) { this.form = form; }
    *[Symbol.iterator]() { for (const input of this.form.querySelectorAll('input').concat(this.form.querySelectorAll('textarea'), this.form.querySelectorAll('select'))) if (input.attrs.name) yield [input.attrs.name, input.value]; }
    get(key) { return [...this].find(([k]) => k === key)?.[1] || ''; }
  }
  vm.runInNewContext(fs.readFileSync('src/linkloom/ui/static/product-task-result.js', 'utf8') + '\n' + fs.readFileSync('src/linkloom/ui/static/product.js', 'utf8'), {document: doc, window, fetch, FormData, setTimeout, console});
  await tick(); await tick();
  const page = doc.getElementById('page');
  if (entry === 'candidate') await page.querySelector('[data-action="inspect-candidate"]').click();
  else {
    const form = doc.getElementById('ask-form'); doc.getElementById('ask-query').value = 'What changed?';
    await page.dispatch('submit', {target: form});
  }
  return {doc, page, calls, events, button: () => page.querySelector('[data-action="inspect-evidence"]'), dialog: () => body.querySelector('dialog'), setSource: fn => sourceRead = fn};
}

for (const entry of ['candidate', 'ordinary', 'task']) test(`${entry}: click existing evidence, highlight only the second identical message, preserve form`, async () => {
  const ui = await setup(entry), button = ui.button(); assert.ok(button, ui.page.innerHTML);
  const form = ui.doc.getElementById('candidate-review');
  if (form) { ui.doc.getElementById('reviewer-id').value = 'Reviewer'; ui.doc.getElementById('review-reason').value = 'Checked original'; }
  const formBefore = form?.outerHTML;
  const expected = entry === 'ordinary' ? runtimeEvidence : evidence;
  await button.click();
  const dialog = ui.dialog(), mark = dialog.querySelector('mark');
  assert.equal(dialog.querySelectorAll('mark').length, 1);
  assert.equal(mark.dataset.evidenceRef, expected.evidence_ref);
  assert.equal(mark.textContent, expected.quote);
  assert.equal(dialog.querySelector('pre').textContent, text);
  assert.equal(dialog.querySelector('pre').innerHTML.split('<mark')[0], Array.from(text).slice(0, entry === 'ordinary' ? runtimeStart : offset).join('').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'));
  assert.equal(dialog.querySelector('script'), null);
  assert.equal(focused, mark); assert.equal(mark.scrolled.block, 'center'); assert.equal(mark.focusOptions.preventScroll, true);
  assert.equal(form?.outerHTML, formBefore);
  await dialog.querySelector('[data-close-evidence]').click();
  assert.equal(ui.dialog(), null); assert.equal(focused, button);
  await button.click(); assert.equal(ui.calls.filter(c => c.path === '/api/sources/src-1').length, 2);
  await ui.dialog().querySelector('[data-close-evidence]').click();
  if (form) {
    await ui.page.dispatch('submit', {target: form, submitter: form.querySelector('[data-review-action="approve"]')});
    const sent = ui.calls.find(c => c.path.endsWith('/approve'));
    assert.deepEqual(JSON.parse(sent.body), {reviewer_id: 'Reviewer', reason: 'Checked original'});
    assert.equal(sent.method, 'POST');
  }
});

for (const entry of ['candidate', 'ordinary', 'task']) for (const kind of ['permission', 'removed', 'version', 'registry', 'unicode-offset', 'duplicate-ref']) test(`${entry} fails closed: ${kind}`, async () => {
  const ui = await setup(entry); await ui.button().click(); assert.ok(ui.dialog().querySelector('mark'));
  ui.setSource(async () => {
    if (kind === 'permission' || kind === 'removed') return {status: kind === 'permission' ? 403 : 404, error: {code: 'UNAVAILABLE', message: quote}};
    const value = source();
    if (kind === 'version') value.source_version = 'new-version';
    if (kind === 'registry') value.evidence_jump.validated = false;
    const target = value.evidence_jump.spans.find(span => span.evidence_ref === (entry === 'ordinary' ? runtimeEvidence : evidence).evidence_ref);
    if (kind === 'unicode-offset') target.char_start += 1;
    if (kind === 'duplicate-ref') value.evidence_jump.spans.push({...target});
    return {source: value};
  });
  await ui.events.focus(); await tick();
  assert.equal(ui.dialog().querySelector('mark'), null);
  assert.doesNotMatch(ui.dialog().textContent, /alert\(1\)/);
  assert.doesNotMatch(ui.page.textContent, /alert\(1\)|OLD_(TRACE|PROPOSAL|SUPPORT|COUNTER|RELATION)_QUOTE/);
});

test('clear pending content and ignore a late response after workspace changes', async () => {
  const ui = await setup(); let finish;
  ui.setSource(() => new Promise(resolve => finish = resolve));
  const opening = ui.button().click(); await tick();
  const oldDialog = ui.dialog(); assert.equal(oldDialog.querySelector('mark'), null);
  const picker = ui.doc.getElementById('workspace-picker'); picker.value = 'b'; await picker.dispatch('change');
  finish({source: source()}); await opening;
  assert.equal(ui.dialog(), null); assert.equal(oldDialog.textContent, '');
});

test('a later source open wins and a close cannot be undone by a late response', async () => {
  const ui = await setup('ordinary'); let finish;
  ui.setSource(() => new Promise(resolve => finish = resolve));
  const first = ui.button().click(); await tick();
  const firstDialog = ui.dialog();
  ui.setSource(async () => ({source: source()})); await ui.button().click();
  const currentDialog = ui.dialog(); finish({source: source()}); await first;
  assert.notEqual(currentDialog, firstDialog); assert.equal(ui.dialog(), currentDialog); assert.equal(firstDialog.textContent, '');
  await currentDialog.querySelector('[data-close-evidence]').click();
  ui.setSource(() => new Promise(resolve => finish = resolve));
  const pending = ui.button().click(); await tick();
  await ui.dialog().querySelector('[data-close-evidence]').click(); finish({source: source()}); await pending;
  assert.equal(ui.dialog(), null);
});
