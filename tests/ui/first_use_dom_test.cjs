const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const script = fs.readFileSync('src/linkloom/ui/static/product.js', 'utf8');

async function setup({ready = false, unresolved = ['relation', 'valid_from']} = {}) {
  const elements = new Map(), posts = [];
  const source = {source_id: 'source-1', name: 'meeting.md', source_version: 'version-1',
    content: '[2026-10-01T10:00:00Z] Alice: We decided to use Birchline.',
    ingestion_status: ready ? 'COMPLETE' : 'FAILED', candidate_count: ready ? 1 : 0,
    review_required_count: ready ? 1 : 0, segment_summary: {total: 0, processed: 0, failed: 0, retryable: 0},
    error: ready ? null : 'PROVIDER_UNAVAILABLE: Configure a semantic Provider.', segments: [], evidence: []};
  const candidate = {candidate_id: 'candidate-1', source_id: 'source-1', source: {name: 'meeting.md'},
    proposed_value: 'Birchline', subject: 'Launch', relation: unresolved.includes('relation') ? null : 'uses vendor',
    relation_phrase: '<unmapped supplier>', claim_type: 'DECISION', status: 'PENDING_REVIEW',
    current_unresolved_fields: unresolved, review_reasons: ['RELATION_UNRESOLVED'],
    evidence: {quote: 'We decided to use Birchline.', line_start: 1, line_end: 1}};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: '', textContent: '', value: '', dataset: {}, listeners: {},
      addEventListener(type, fn) {this.listeners[type] = fn;}, setAttribute() {}, removeAttribute() {},
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
  vm.runInNewContext(script, {document: {documentElement: {lang: 'en'}, getElementById: element,
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
    assert.match(html, ready ? />Import and extract</ : />Save source for later extraction</);
    assert.match(html, ready ? /Source text is sent/ : /Source saving is available; semantic extraction is not/);
    assert.equal(ui.posts.length, 0, 'opening a page must not import or extract');
    if (!ready) {
      await ui.submit('source-create', undefined, {name: 'meeting.md', content: 'timestamped text'});
      await tick();
      assert.match(ui.element('page').innerHTML, /Source saved\. Semantic extraction is unavailable/);
      assert.match(ui.element('page').innerHTML, /FAILED/);
      assert.match(ui.element('page').innerHTML, /PROVIDER_UNAVAILABLE/);
      assert.deepEqual(ui.posts.map(p => p.path), ['/api/sources/import']);
    }
  }
  const ui = await setup({ready: true});
  await ui.click('inspect-candidate', 'candidate-1');
  let html = ui.element('page').innerHTML;
  assert.doesNotMatch(html, /id="review-relation"|name="relation"/);
  assert.match(html, /Canonical relation cannot be corrected/);
  assert.match(html, /data-review-action="approve" disabled/);
  assert.match(html, /data-review-action="reject" formnovalidate/);
  assert.match(html, /&lt;unmapped supplier&gt;/);
  assert.match(html, /import the original artifact again/);
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
