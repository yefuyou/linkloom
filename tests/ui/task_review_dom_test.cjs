const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/linkloom/ui/static/product-task-review.js', 'utf8');
const productSource = fs.readFileSync('src/linkloom/ui/static/product.js', 'utf8');
const calls = [];
let sourceMutationMode = 'none';

function response(payload, ok = true, status = 200) {
  return {ok, status, json: async () => payload};
}

const review = {
  available: true,
  mode: 'LOCAL_HOST_SELF_ASSERTED',
  business_approval: false,
  workspace_id: 'ws-a',
  run_id: 'ui-0123456789ab',
  source_snapshot_hash: 'snapshot-1',
  source_catalog_hash: 'catalog-1',
  revision: 0,
  capability: 'host-capability-issued-by-host',
  steps: [
    {
      step_id: 'identity',
      predicate: 'SAME_TASK',
      status: 'SUPPORTED',
      source_verdict: 'SUPPORTED',
      source_verdict_source: 'RELATION_ASSESSMENT',
      source_proof_status: 'BLOCKED',
      subject: {raw_text: 'Task A original assignment', provenance: {quote: 'Task A exact original'}},
      object: {raw_text: 'Task B original assignment', provenance: {quote: 'Task B exact original'}},
      supporting_evidence: [{quote: 'Model proposal support must not render'}],
      counter_evidence: [{quote: 'Model proposal counter must not render'}],
      source_supporting_evidence: [{quote: 'Canonical source support quote'}],
      source_counter_evidence: [{quote: 'Canonical source counter quote'}],
      source_blockers: ['CANONICAL_SOURCE_DIAGNOSTIC'],
      blockers: [],
    },
    {
      step_id: 'revision',
      predicate: 'REVISES_PLAN',
      status: 'UNKNOWN',
      subject: {raw_text: 'Task A original assignment', provenance: {quote: 'Task A exact original'}},
      object: {raw_text: 'Task B original assignment', provenance: {quote: 'Task B exact original'}},
      supporting_evidence: [],
      counter_evidence: [],
      blockers: ['SOURCE_PROOF_UNKNOWN', 'IDENTITY_NOT_SUPPORTED'],
    },
  ],
  history: [{
    action: 'REVIEW', step_id: 'identity', actor_label: 'previous-local-reviewer',
    note: 'previous local statement', outcome: 'CHECKED', revision: 1,
    run_id: 'old-run-0001', current_run: false,
    quote: 'RelationRead historical quote must never render',
  }, {
    action: 'AUDIT', step_id: 'identity', phase: 'REVIEW', status: 'COMMITTED',
    run_id: 'ui-0123456789ab', current_run: true, sequence: 2,
  }],
};

const target = {
  innerHTML: '',
  isConnected: true,
  listeners: {},
  addEventListener(type, listener) { this.listeners[type] = listener; },
  insertAdjacentHTML(_position, html) { this.innerHTML += html; },
};
const sourceTarget = {
  innerHTML: '', isConnected: true, listeners: {},
  addEventListener(type, listener) { this.listeners[type] = listener; },
  insertAdjacentHTML(_position, html) { this.innerHTML += html; },
};

const context = {
  window: {},
  document: {getElementById(id) { return id === 'ask-result' ? target : null; }},
  fetch: async (path, options = {}) => {
    if (sourceMutationMode === 'post-invalid' && options.method) return response({error: {
      code: 'SOURCE_SNAPSHOT_CHANGED', message: 'The current source changed.'}}, false, 409);
    if (sourceMutationMode === 'get-invalid' && !options.method) return response({available: false,
      mode: 'LOCAL_HOST_SELF_ASSERTED', workspace_id: review.workspace_id, run_id: review.run_id,
      business_approval: false, reason: 'SOURCE_SNAPSHOT_CHANGED', steps: [], history: []});
    if (sourceMutationMode === 'get-disabled-error' && !options.method) return response({error: {
      code: 'LOCAL_TASK_REVIEW_DISABLED', message: 'Local review is disabled.'}}, false, 403);
    if (!options.method && path.includes('ws-disabled')) return response({available: false,
      mode: 'DISABLED', workspace_id: 'ws-disabled', run_id: review.run_id,
      business_approval: false, reason: 'LOCAL_TASK_REVIEW_DISABLED', steps: [], history: []});
    if (!options.method) return response(review);
    calls.push({path, body: JSON.parse(options.body)});
    return response({receipt: {revision: 1}, review: {...review, revision: 1, history: [
      ...review.history,
      {action: 'REVIEW', step_id: 'identity', actor_label: 'local self-asserted actor',
        note: 'I checked the two original records locally.', outcome: 'CHECKED', revision: 1},
    ]}});
  },
  crypto: {randomUUID: () => 'request-review-1'},
  console,
};
vm.runInNewContext(source, context);

assert.equal(typeof context.window.mountTaskReview, 'function', 'module exposes the narrow post-render hook');
assert.match(productSource, /invalidateResult:\s*\(\)\s*=>\s*invalidateAsk\(\)/);
assert.match(productSource, /const reviewLanguage = locale;/, 'review panel follows the answer locale');
assert.match(productSource, /askLocale\(result\.query\) === reviewLanguage/, 'stale checks use the same answer locale');

(async () => {
  await context.window.mountTaskReview({
    target,
    result: {agent_run_id: review.run_id, workspace_id: review.workspace_id},
    workspaceId: review.workspace_id,
    runId: review.run_id,
    language: 'en',
    isCurrent: () => true,
  });

  assert.match(target.innerHTML, /id="task-review-panel"/);
  assert.match(target.innerHTML, /Local review|人工核验/);
  assert.match(target.innerHTML, /Task A original assignment/);
  assert.match(target.innerHTML, /Task B original assignment/);
  assert.match(target.innerHTML, /来源合同核验|Source contract verdict/);
  assert.match(target.innerHTML, /SUPPORTED/);
  assert.match(target.innerHTML, /RELATION_ASSESSMENT/);
  assert.match(target.innerHTML, /持久化关系证明|Persistent relation proof/);
  assert.match(target.innerHTML, /CURRENT_CONFIRMED|BLOCKED/);
  assert.match(target.innerHTML, /Canonical source support quote/);
  assert.match(target.innerHTML, /Canonical source counter quote/);
  assert.match(target.innerHTML, /CANONICAL_SOURCE_DIAGNOSTIC/);
  assert.doesNotMatch(target.innerHTML, /Model proposal support must not render/);
  assert.doesNotMatch(target.innerHTML, /Model proposal counter must not render/);
  assert.match(target.innerHTML, /SOURCE_PROOF_UNKNOWN/);
  assert.match(target.innerHTML, /previous-local-reviewer/);
  assert.match(target.innerHTML, /old-run-0001/);
  assert.match(target.innerHTML, /related history/);
  assert.match(target.innerHTML, /ui-0123456789ab/);
  assert.match(target.innerHTML, /current run/);
  assert.doesNotMatch(target.innerHTML, /RelationRead historical quote must never render/);
  assert.match(target.innerHTML, /Business approval.*Unconfirmed/i);
  assert.match(target.innerHTML, /data-task-review-action="WITHDRAW"/);
  assert.match(target.innerHTML, /data-task-review-action="CONTINUE"/);
  assert.match(target.innerHTML, /data-task-review-actor/);
  assert.match(target.innerHTML, /data-task-review-note/);
  assert.match(target.innerHTML, /data-task-review-outcome/);
  assert.match(target.innerHTML, /CHECKED[^<]*disabled|option[^>]+disabled[^>]*>[^<]*CHECKED/i);

  const zhTarget = {
    innerHTML: '', isConnected: true, listeners: {},
    addEventListener(type, listener) { this.listeners[type] = listener; },
    insertAdjacentHTML(_position, html) { this.innerHTML += html; },
  };
  await context.window.mountTaskReview({target: zhTarget, workspaceId: review.workspace_id,
    runId: review.run_id, language: 'zh', isCurrent: () => true});
  assert.match(zhTarget.innerHTML, /人工核验/);
  assert.match(zhTarget.innerHTML, /业务批准/);

  const form = {
    dataset: {taskReviewForm: '', stepId: 'identity'},
    fields: {
      actor: {value: 'local self-asserted actor'},
      note: {value: 'I checked the two original records locally.'},
      outcome: {value: 'CHECKED'},
    },
    querySelector(selector) {
      if (selector.includes('actor')) return this.fields.actor;
      if (selector.includes('note')) return this.fields.note;
      if (selector.includes('outcome')) return this.fields.outcome;
      return null;
    },
  };
  await target.listeners.submit({
    target: form,
    submitter: {dataset: {taskReviewAction: 'REVIEW'}},
    preventDefault() {},
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/api/product/task-review');
  assert.deepEqual(calls[0].body, {
    workspace_id: 'ws-a', run_id: 'ui-0123456789ab', step_id: 'identity',
    action: 'REVIEW', request_id: 'request-review-1', capability: 'host-capability-issued-by-host',
    expected_revision: 0, actor_label: 'local self-asserted actor',
    note: 'I checked the two original records locally.', outcome: 'CHECKED',
  });
  assert.match(target.innerHTML, /local self-asserted actor/);
  assert.match(target.innerHTML, /I checked the two original records locally/);

  const disabledTarget = {
    innerHTML: '<article data-task-answer>Existing read-only task answer</article>',
    isConnected: true, listeners: {}, panel: null,
    addEventListener(type, listener) { this.listeners[type] = listener; },
    querySelector(selector) { return selector === '#task-review-panel' ? this.panel : null; },
    insertAdjacentHTML(_position, html) {
      const owner = this;
      this.innerHTML += html;
      this.panel = {};
      Object.defineProperty(this.panel, 'outerHTML', {
        get() { return '<section id="task-review-panel"></section>'; },
        set(value) {
          const answer = owner.innerHTML.match(/<article data-task-answer>[\s\S]*?<\/article>/)?.[0] || '';
          owner.innerHTML = answer + value;
        },
      });
    },
  };
  await context.window.mountTaskReview({target: disabledTarget, workspaceId: 'ws-disabled', runId: review.run_id,
    language: 'en', isCurrent: () => true});
  assert.match(disabledTarget.innerHTML, /--allow-local-task-review/);
  assert.match(disabledTarget.innerHTML, /Existing read-only task answer/, 'Host-disabled review must preserve the read-only task answer');
  assert.doesNotMatch(disabledTarget.innerHTML, /data-task-review-action="REVIEW"/);

  disabledTarget.innerHTML = '<article data-task-answer>Existing answer after disabled error</article>';
  disabledTarget.panel = null;
  sourceMutationMode = 'get-disabled-error';
  await context.window.mountTaskReview({target: disabledTarget, workspaceId: 'ws-disabled', runId: review.run_id,
    language: 'en', isCurrent: () => true});
  assert.match(disabledTarget.innerHTML, /Existing answer after disabled error/, 'Host-disabled fetch errors must preserve the read-only task answer');
  assert.match(disabledTarget.innerHTML, /--allow-local-task-review/);
  sourceMutationMode = 'none';

  // A source-invalidated or rejected follow-up must not leave old source proof
  // visible in the review panel while the fresh GET is pending.
  target.innerHTML = '<div class="task-review-evidence">Current support quote</div>';
  const staleCurrent = () => false;
  await context.window.mountTaskReview({
    target,
    result: {agent_run_id: review.run_id, workspace_id: review.workspace_id},
    workspaceId: review.workspace_id,
    runId: review.run_id,
    language: 'zh',
    isCurrent: staleCurrent,
  });
  assert.doesNotMatch(target.innerHTML, /Current support quote/);
  assert.doesNotMatch(target.innerHTML, /Canonical source counter quote/);

  let askResult = {quote: 'Old task answer quote'};
  let invalidated = 0;
  const sourceContext = {
    target: sourceTarget, workspaceId: review.workspace_id, runId: review.run_id,
    language: 'en', isCurrent: () => true,
    invalidateResult: () => { invalidated += 1; askResult = null; sourceTarget.innerHTML = ''; },
  };
  await context.window.mountTaskReview(sourceContext);
  sourceTarget.innerHTML = `Old task answer quote${sourceTarget.innerHTML}`;
  sourceMutationMode = 'post-invalid';
  await sourceTarget.listeners.submit({target: form, submitter: {dataset: {taskReviewAction: 'CONTINUE'}}, preventDefault() {}});
  assert.equal(invalidated, 1, 'source rejection invalidates the parent task result state');
  assert.equal(askResult, null);
  assert.doesNotMatch(sourceTarget.innerHTML, /Old task answer quote/);
  assert.match(sourceTarget.innerHTML, /source|来源|失效/i);
  if (askResult) sourceTarget.innerHTML += askResult.quote;
  assert.doesNotMatch(sourceTarget.innerHTML, /Old task answer quote/, 'language changes cannot resurrect cleared state');

  sourceMutationMode = 'get-invalid';
  await context.window.mountTaskReview({...sourceContext, language: 'zh'});
  assert.doesNotMatch(sourceTarget.innerHTML, /Old task answer quote/);
  assert.match(sourceTarget.innerHTML, /来源|失效/);
  assert.doesNotMatch(sourceTarget.innerHTML, /--allow-local-task-review/, 'source failure is not mislabelled as Host opt-in failure');

  console.log('PASS: task review renders bounded evidence, self-asserted local forms, history and safe invalidation');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
