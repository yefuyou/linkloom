const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const script = fs.readFileSync('src/linkloom/ui/static/product.js', 'utf8');

async function setup({ready = false, unresolved = ['relation', 'valid_from'], locale = 'en', relation = 'uses vendor'} = {}) {
  const elements = new Map(), posts = [];
  const source = {source_id: 'source-1', name: 'meeting.md', source_version: 'version-1',
    content: '[2026-10-01T10:00:00Z] Alice: We decided to use Birchline.',
    ingestion_status: ready ? 'COMPLETE' : 'FAILED', candidate_count: ready ? 1 : 0,
    review_required_count: ready ? 1 : 0, segment_summary: {total: 0, processed: 0, failed: 0, retryable: 0},
    error: ready ? null : 'PROVIDER_UNAVAILABLE: Configure a semantic Provider.', segments: [], evidence: []};
  const candidate = {candidate_id: 'candidate-1', source_id: 'source-1', source: {name: 'meeting.md'},
    proposed_value: 'Birchline', subject: 'Launch', relation: unresolved.includes('relation') ? null : relation,
    relation_phrase: '<unmapped supplier>', claim_type: 'DECISION', status: 'PENDING_REVIEW',
    current_unresolved_fields: unresolved, review_reasons: ['RELATION_UNRESOLVED'],
    evidence: {quote: 'We decided to use Birchline.', line_start: 1, line_end: 1}};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: '', textContent: '', value: '', dataset: {}, listeners: {},
      attributes: new Map([['id', id]]),
      addEventListener(type, fn) {this.listeners[type] = fn;},
      setAttribute(name, value) {this.attributes.set(String(name).toLowerCase(), String(value));},
      removeAttribute(name) {this.attributes.delete(String(name).toLowerCase());},
      hasAttribute(name) {return this.attributes.has(String(name).toLowerCase());},
      checkValidity() {return Boolean(this.value);}, reportValidity() {this.reported = true;},
      querySelector() {return {disabled: /data-review-action="approve" disabled/.test(element('page').innerHTML)};}});
    return elements.get(id);
  }
  const nav = ['sources', 'review'].map(screen => Object.assign(element(screen), {dataset: {screen}}));
  async function fetch(path, options = {}) {
    if (options.method) posts.push({path, data: JSON.parse(options.body)});
    const payload = path === '/api/status' ? {ready, message: ready ? 'Provider ready.' : 'No provider. --allow-external-provider'}
      : path === '/api/workspaces' ? {workspaces: [{workspace_id: 'a', name: 'Atlas'}]}
      : path === '/api/workspaces/current' ? {workspace: {workspace_id: 'a', name: 'Atlas'}}
      : path === '/api/sources' ? {sources: []}
      : path.startsWith('/api/sources/') ? {source}
      : path === '/api/review/attention' ? {decisions: []}
      : path === '/api/review/candidates' ? {candidates: [], supporting_proposals: []}
      : {candidate};
    return {ok: true, status: 200, json: async () => payload};
  }
  vm.runInNewContext(script, {document: {documentElement: {lang: locale}, getElementById: element,
    querySelectorAll: () => nav}, fetch, console, setTimeout: fn => fn(), window: {setTimeout() {}},
    FormData: class {constructor(form) {this.fields = form.fields;} *[Symbol.iterator]() {yield* Object.entries(this.fields || {});}}});
  await tick(); await tick();
  const click = async (action, id) => {
    await element('page').listeners.click({target: {closest: () => ({dataset: {action, id}})}});
    await tick();
  };
  const submit = async (id, action, fields) => {
    const form = element(id); form.fields = fields; form.dataset.id = 'candidate-1';
    await element('page').listeners.submit({preventDefault() {}, target: form, submitter: {dataset: {reviewAction: action}}});
  };
  return {element, posts, click, submit};
}

(async () => {
  for (const ready of [false, true]) {
    const ui = await setup({ready});
    const html = ui.element('page').innerHTML;
    assert.match(html, ready ? />Import and extract</ : />Save source</);
    assert.match(html, ready ? /Importing sends source text/ : /Save sources locally/);
    assert.equal(ui.posts.length, 0, 'opening a page must not import or extract');
    assert.match(html, /<h1>Sources<\/h1>/);
    assert.doesNotMatch(html, /class="eyebrow"|class="lede"/);
    if (ready) assert.match(html, /external AI provider and may incur model costs/);
    if (!ready) {
      await ui.submit('source-create', undefined, {name: 'meeting.md', content: 'timestamped text'});
      await tick();
      assert.match(ui.element('page').innerHTML, /Source saved locally/);
      assert.match(ui.element('page').innerHTML, /FAILED/);
      assert.match(ui.element('page').innerHTML, /PROVIDER_UNAVAILABLE/);
      assert.deepEqual(ui.posts.map(p => p.path), ['/api/sources/import']);
    }
  }
  const zh = await setup({ready: true, locale: 'zh'});
  assert.match(zh.element('page').innerHTML, /<h1>来源<\/h1>/);
  assert.match(zh.element('page').innerHTML, /导入并提取/);
  assert.match(zh.element('page').innerHTML, /外部 AI.*模型费用/);
  await zh.click('inspect-candidate', 'candidate-1');
  assert.match(zh.element('page').innerHTML, /关系待确认 · 暂无法批准/);
  assert.match(zh.element('page').innerHTML, /data-review-action="approve" disabled/);
  for (const relation of ['constructor', '__proto__', 'custom relationship', 'uses vendor']) {
    const custom = await setup({ready: true, unresolved: [], relation});
    await custom.click('inspect-candidate', 'candidate-1');
    assert.match(custom.element('page').innerHTML, /id="candidate-review"/);
    assert.ok(custom.element('page').innerHTML.includes(relation === 'uses vendor' ? 'Supplier' : relation));
    assert.doesNotMatch(custom.element('page').innerHTML, /This page could not load/);
  }
  const ui = await setup({ready: true});
  await ui.click('inspect-candidate', 'candidate-1');
  let html = ui.element('page').innerHTML;
  assert.doesNotMatch(html, /id="review-relation"|name="relation"/);
  assert.match(html, /Relationship unresolved · approval blocked/);
  assert.match(html, /data-review-action="approve" disabled/);
  assert.match(html, /data-review-action="reject" formnovalidate/);
  assert.match(html, /&lt;unmapped supplier&gt;/);
  assert.match(html, /import the original source again/);
  await ui.submit('candidate-review', undefined, {});
  assert.equal(ui.posts.length, 0, 'Enter cannot approve an unresolved relation');
  await ui.submit('candidate-review', 'reject', {reviewer_id: '', reason: ''});
  assert.equal(ui.posts.length, 0, 'rejection still requires reviewer identity');
  ui.element('reviewer-id').value = 'human';
  ui.element('review-reason').value = 'Cannot confirm relation';
  await ui.submit('candidate-review', 'reject', {reviewer_id: 'human', reason: 'Cannot confirm relation'});
  assert.equal(ui.posts[0].path, '/api/review/candidates/candidate-1/reject');
  assert.equal(ui.posts[0].data.relation, undefined);
  await ui.click('inspect-candidate', 'candidate-1');
  await ui.click('inspect-source', 'source-1');
  assert.match(ui.element('page').innerHTML, /id="update-content"/);
  assert.match(ui.element('page').innerHTML, /We decided to use Birchline/);
  const resolved = await setup({ready: true, unresolved: ['subject', 'valid_from']});
  await resolved.click('inspect-candidate', 'candidate-1');
  html = resolved.element('page').innerHTML;
  assert.match(html, /id="review-subject"/);
  assert.match(html, /id="review-valid-from"/);
  assert.doesNotMatch(html, /data-review-action="approve" disabled/);
  await resolved.submit('candidate-review', 'approve', {subject: 'Launch', valid_from: '2026-10-01', reviewer_id: 'human', reason: 'Source supported'});
  assert.equal(resolved.posts[0].path, '/api/review/candidates/candidate-1/approve');
  assert.equal(resolved.posts[0].data.relation, undefined);
  console.log('PASS: provider-aware import, saved-vs-failed status, relation guidance/escaping, blocked approval, reviewer-bound rejection, source navigation, subject/time review');
})().catch(error => {console.error(error); process.exitCode = 1;});
