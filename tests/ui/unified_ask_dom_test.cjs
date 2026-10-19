const { strict: assert } = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("src/linkloom/ui/static/product.js", "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const result = value => ({result: {path: "DETERMINISTIC", status: "COMPLETED", query: "current", workspace_id: "a",
  scope: {subject: "Launch", relation: "uses supplier", as_of: "2026-10-10T00:00:00+00:00"}, route: {reason: "lookup"},
  timeline: {effective: {decision_id: "decision-1", value}}, conclusions: [{value}], evidence: [], receipts: [], reviewer: {performed: false}, uncertainty: {unknown_fields: []}}});

function sourceResult(complete = true) {
  const payload = result("");
  Object.assign(payload.result, {path: "SOURCE_RELATION_INVESTIGATION", status: complete ? "COMPLETED" : "INSUFFICIENT",
    reviewer: {accepted: true}, conclusions: [], timeline: null,
    uncertainty: {status: complete ? "none" : "conflicting_evidence", unknown_fields: complete ? [] : ["leader"]},
    source_investigation: {answer: complete ? "Verified Leader" : null,
      facts: [{subject: "project", predicate: "owner_of_project", value: "Known Owner"}],
      plan: {subgoals: [{relation_path: ["owner_of_project"]}, {relation_path: ["reports_to"]}]}, trace: []}});
  return payload;
}

async function setup() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: "", textContent: "", value: "", dataset: {}, listeners: {},
      addEventListener(type, fn) {this.listeners[type] = fn;}, setAttribute() {}, removeAttribute() {}});
    return elements.get(id);
  }
  const nav = ["sources", "ask"].map(screen => Object.assign(element(screen), {dataset: {screen}}));
  const pending = [];
  const submittedBodies = [];
  const polls = [];
  const feedbackPosts = [];
  let current = "a";
  const workspaces = [{workspace_id: "a", name: "Atlas"}, {workspace_id: "b", name: "Other"}];
  const response = payload => ({ok: true, status: 200, json: async () => payload});
  async function fetch(path, options = {}) {
    if (path.startsWith("/api/product/feedback/")) return await new Promise(resolve => feedbackPosts.push(payload => resolve(response(payload))));
    if (path.startsWith("/api/product/ask/runs/")) return await new Promise(resolve => polls.push(payload => resolve(response(payload))));
    if (path === "/api/product/ask") {
      submittedBodies.push(JSON.parse(options.body));
      return await new Promise(resolve => pending.push(payload => resolve(response(payload))));
    }
    if (path === "/api/workspaces/current" && options.method === "POST") current = JSON.parse(options.body).workspace_id;
    const payload = path === "/api/workspaces" ? {workspaces}
      : path === "/api/workspaces/current" ? {workspace: workspaces.find(w => w.workspace_id === current)}
      : path === "/api/status" ? {ready: false}
      : path === "/api/sources" ? {sources: []}
      : path === "/api/product/ask/config" ? {workspace_id: current, scopes: [{subject: "Launch", relation: "uses supplier"}], agent: {available: false}, investigation_context: {snapshot_hash: "a".repeat(64)}}
      : {candidates: []};
    return response(payload);
  }
  vm.runInNewContext(source, {document: {documentElement: {lang: "en"},
    getElementById: element, querySelectorAll: () => nav}, fetch,
    FormData: class {constructor(form) {this.form = form;} *[Symbol.iterator]() {yield* Object.entries(this.form.fields);} get(key) {return this.form.fields[key];}},
    window: {setTimeout() {}}, setTimeout, console});
  await tick(); await tick();
  await element("ask").listeners.click(); await tick();
  assert.match(element("page").innerHTML, /id="ask-slot"/);
  assert.match(element("page").innerHTML, /id="ask-domain"/);
  assert.doesNotMatch(element("page").innerHTML, /id="ask-slot" required/);
  assert.match(element("page").innerHTML, /Discover automatically from my question/);
  element("ask-slot").value = "0";
  element("ask-form").fields = {query: "What is current?", as_of: ""};
  const submit = () => element("page").listeners.submit({preventDefault() {}, target: element("ask-form")});
  return {element, pending, polls, submit, submittedBodies, feedbackPosts};
}

(async () => {
  const scopedUi = await setup();
  scopedUi.element("ask-slot").value = "";
  scopedUi.element("ask-form").fields = {query: "后面那条还能接上吗？", as_of: "", investigation_domain: "tasks"};
  const scopedOperation = scopedUi.submit(); await tick();
  assert.deepEqual(scopedUi.submittedBodies[0].investigation_context, {domain: "tasks", snapshot_hash: "a".repeat(64)});
  assert.equal(scopedUi.submittedBodies[0].query, "后面那条还能接上吗？");
  const needsDomain = result("");
  Object.assign(needsDomain.result, {status: "NEEDS_SCOPE", path: null, conclusions: [],
    error: {code: "INVESTIGATION_DOMAIN_REQUIRED"}, discovery: {status: "NO_MATCH"}});
  scopedUi.pending[0](needsDomain); await scopedOperation;
  assert.match(scopedUi.element("ask-result").innerHTML, /Choose an investigation scope/);
  for (const field of ["ask-date", "ask-query", "ask-slot", "ask-domain"]) {
    const {element, pending, submit} = await setup();
    const operation = submit(); await tick();
    await element("page").listeners.input({target: element(field)});
    pending.shift()(result("OBSOLETE_ANSWER")); await operation;
    assert.doesNotMatch(element("ask-result").innerHTML, /OBSOLETE_ANSWER/);
  }
  const {element, pending, submit} = await setup();
  const old = submit(); await tick();
  const latest = submit(); await tick();
  pending[1](result("NEW_ANSWER")); await latest;
  pending[0](result("OLD_ANSWER")); await old;
  assert.match(element("ask-result").innerHTML, /NEW_ANSWER/);
  assert.doesNotMatch(element("ask-result").innerHTML, /OLD_ANSWER/);
  const {element: ws, pending: requests, submit: ask} = await setup();
  const running = ask(); await tick();
  ws("workspace-picker").value = "b";
  await ws("workspace-picker").listeners.change();
  requests[0](result("OLD_WORKSPACE_ANSWER")); await running;
  assert.doesNotMatch(ws("page").innerHTML, /OLD_WORKSPACE_ANSWER/);
  assert.doesNotMatch(ws("ask-result").innerHTML, /OLD_WORKSPACE_ANSWER/);
  for (const payload of [result("OBSOLETE_AGENT_FINAL"), sourceResult(), {error: {code: "OLD_POLL_ERROR", message: "OLD_POLL_ERROR"}}]) {
    const {element: ui, pending: posts, polls, submit: start} = await setup();
    const investigation = start(); await tick();
    const accepted = result(""); accepted.result.path = "AGENT"; accepted.result.status = "INVESTIGATING"; accepted.result.agent_run_id = "run-1";
    posts[0](accepted); await investigation; await tick();
    await ui("page").listeners.input({target: ui("ask-date")});
    polls[0](payload); await tick(); await tick();
    assert.doesNotMatch(ui("ask-result").innerHTML, /OBSOLETE_AGENT_FINAL|OLD_POLL_ERROR/);
  }
  const answerFirst = await setup();
  const currentAnswer = answerFirst.submit(); await tick();
  const trusted = result("Wrenwell");
  trusted.result.query = "我们现在采用哪家供应商？";
  trusted.result.evidence = [{source_name: "meeting.md", source_id: "source-1", source_version: "VERSION_LONG", evidence_ref: "REF_LONG", quote: "We decided to use Wrenwell.", line_start: 1, line_end: 1}];
  trusted.result.receipts = [{receipt_id: "RECEIPT_LONG", authorization_type: "HUMAN_APPROVAL"}];
  trusted.result.uncertainty = {unknown_fields: ["replacement_reason"]};
  answerFirst.pending[0](trusted); await currentAnswer;
  const html = answerFirst.element("ask-result").innerHTML;
  assert.ok(html.indexOf("Wrenwell") < html.indexOf("RECEIPT_LONG"));
  assert.match(html, /已核实的正式决策/);
  assert.match(html, /We decided to use Wrenwell\./);
  assert.match(html, /<details[^>]*class="ask-technical"/);
  assert.doesNotMatch(html.slice(0, html.indexOf('class="ask-evidence"')), /RECEIPT_LONG|VERSION_LONG|REF_LONG/);
  for (const kind of ["unknown-reason", "empty", "uncertain", "unverified-agent", "escaping"]) {
    const ui = await setup(); const request = ui.submit(); await tick();
    const payload = result(kind === "empty" ? "" : "Wrenwell");
    if (kind === "unknown-reason") {
      payload.result.path = "AGENT"; payload.result.query = "为什么换供应商？";
      payload.result.reviewer = {performed: true, accepted: true, decision: "evidence_sufficient"};
      payload.result.uncertainty = {unknown_fields: ["replacement_reason"]};
      payload.result.timeline.transitions = [{from_value: "Birchline", to_value: "Wrenwell", effective_at: "2026-10-07T10:00:00Z"}];
    } else if (kind === "empty") {
      payload.result.conclusions = []; payload.result.timeline.effective = null;
    } else if (["uncertain", "unverified-agent"].includes(kind)) {
      payload.result.path = "AGENT"; payload.result.status = kind === "uncertain" ? "PROVIDER_UNCERTAIN" : "COMPLETED";
      payload.result.conclusions = [{value: "UNVERIFIED_DRAFT"}];
      payload.result.rationale = [{statement: "UNVERIFIED_DRAFT"}];
    } else {
      payload.result.conclusions[0].value = '<img src=x onerror="alert(1)">';
      payload.result.evidence = [{source_name: '<script>alert(1)</script>', quote: '<img src=x onerror="alert(1)">'}];
    }
    ui.pending[0](payload); await request;
    const output = ui.element("ask-result").innerHTML;
    const core = output.slice(0, output.indexOf('<details class="ask-technical"'));
    if (kind === "unknown-reason") assert.match(core, /更换事实已核实，原因尚无可信证据/);
    if (kind === "empty") { assert.match(core, /No verifiable formal decision/); assert.doesNotMatch(core, /Verified formal decision/); }
    if (["uncertain", "unverified-agent"].includes(kind)) { assert.doesNotMatch(output, /UNVERIFIED_DRAFT/); assert.doesNotMatch(core, /Verified formal decision/); }
    if (kind === "escaping") { assert.doesNotMatch(output, /<img |<script>/); assert.match(output, /&lt;img /); }
  }
  const automatic = await setup();
  for (const status of ["PARTIAL", "UNKNOWN", "BLOCKED"]) {
    const taskUi = await setup(); const request = taskUi.submit(); await tick();
    const payload = result("");
    Object.assign(payload.result, {path: "TASK_INVESTIGATION", status, query: "调查任务如何延续", conclusions: [],
      reviewer: {accepted: true}, preparation: {gaps: ["BOUNDED_DISCOVERY_CANNOT_ESTABLISH_MISSING_ENTITY_ABSENCE"], planning: [{plan: {steps: [{question: '<script>unsafe</script>'}]}}]},
      evidence: [{source_name: "原始会议", quote: "原始会议摘录", source_version: "v1"}]});
    taskUi.pending[0](payload); await request;
    const output = taskUi.element("ask-result").innerHTML;
    assert.match(output, status === "BLOCKED" ? /任务调查已停止/ : /任务调查：仍需确认/);
    assert.doesNotMatch(output, /<script>unsafe/);
    if (status !== "BLOCKED") { assert.match(output, /原始会议摘录/); assert.match(output, /真实模型业务验收尚未评估/); assert.match(output, /不能证明任务不存在/); }
  }
  automatic.element("ask-slot").value = "";
  automatic.element("ask-external").checked = true;
  const firstQuestion = automatic.submit(); await tick();
  assert.equal(automatic.submittedBodies[0].subject, undefined);
  assert.equal(automatic.submittedBodies[0].allow_external_provider, true);
  assert.equal(automatic.element("ask-external").checked, false);
  const ambiguous = result(""); ambiguous.result.status = "NEEDS_SCOPE";
  ambiguous.result.discovery = {status: "AMBIGUOUS", candidate_count: 2, candidates: [
    {subject: "Launch", relation: "uses supplier", current_value: "B"},
    {subject: "Support", relation: "uses supplier", current_value: "C"},
  ]};
  automatic.pending[0](ambiguous); await firstQuestion;
  assert.match(automatic.element("ask-result").innerHTML, /choose-ask-scope/);
  const continuation = automatic.element("page").listeners.click({target: {closest: () => ({dataset: {action: "choose-ask-scope", index: "0"}})}});
  await tick();
  assert.equal(automatic.submittedBodies[1].query, automatic.submittedBodies[0].query);
  assert.equal(automatic.submittedBodies[1].subject, "Launch");
  assert.equal(automatic.submittedBodies[1].allow_external_provider, false);
  const needsDate = result(""); needsDate.result.status = "NEEDS_SCOPE";
  needsDate.result.error = {code: "DATE_SCOPE_REQUIRED", message: "Choose a date"};
  automatic.pending[1](needsDate); await continuation;
  automatic.element("ask-form").fields.as_of = "2026-10-06";
  await automatic.element("page").listeners.input({target: automatic.element("ask-date")});
  const dated = automatic.submit(); await tick();
  assert.equal(automatic.submittedBodies[2].subject, "Launch");
  assert.equal(automatic.submittedBodies[2].query, automatic.submittedBodies[0].query);
  automatic.pending[2](result("B")); await dated;
  assert.match(automatic.element("ask-result").innerHTML, /0 model calls/);
  automatic.element("ask-form").fields.query = "Which decision now?";
  await automatic.element("page").listeners.input({target: automatic.element("ask-query")});
  const nextQuestion = automatic.submit(); await tick();
  assert.equal(automatic.submittedBodies[3].subject, undefined);
  automatic.pending[3](result("NEXT")); await nextQuestion;
  console.log("PASS: scope/date/query invalidation, latest question wins, workspace response isolation");
  console.log("PASS: automatic scope, candidate/date continuation retains question, new query rediscovers, per-request permission resets");
  const feedbackUi = await setup();
  const feedbackForm = feedbackUi.element("feedback-form");
  feedbackForm.fields = {term: "供方", relation: "uses supplier", actor: "human"};
  feedbackUi.element("feedback-status").isConnected = true;
  const originalQuestion = feedbackUi.element("ask-form").fields.query;
  const mutation = feedbackUi.element("page").listeners.submit({preventDefault() {}, target: feedbackForm});
  await tick();
  assert.equal(feedbackUi.element("ask-submit-button").disabled, true);
  await feedbackUi.submit();
  assert.equal(feedbackUi.submittedBodies.length, 0);
  feedbackUi.feedbackPosts[0]({entry: {status: "pending"}}); await mutation;
  assert.equal(feedbackUi.element("ask-submit-button").disabled, false);
  assert.equal(feedbackUi.element("ask-form").fields.query, originalQuestion);
  const afterFeedback = feedbackUi.submit(); await tick();
  feedbackUi.pending[0](result("AFTER_FEEDBACK")); await afterFeedback;
  assert.match(feedbackUi.element("ask-result").innerHTML, /AFTER_FEEDBACK/);
  console.log("PASS: feedback mutation blocks racing Ask, preserves query and resumes after save");
  for (const complete of [true, false]) {
    const ui = await setup();
    const operation = ui.submit(); await tick();
    ui.pending[0](sourceResult(complete)); await operation;
    const html = ui.element("ask-result").innerHTML;
    assert.match(html, /Source-supported relationship/);
    assert.doesNotMatch(html, /Verified formal decision|Recorded authorization/);
    assert.match(html, complete ? /Verified Leader/ : /Conflicting sources; no unique answer/);
    assert.match(html, /Known Owner/);
  }
  const uncertainUi = await setup();
  const unknownOperation = uncertainUi.submit(); await tick();
  const unknownPayload = result("");
  Object.assign(unknownPayload.result, {status: "PROVIDER_UNCERTAIN", path: "AGENT", conclusions: [], timeline: null});
  uncertainUi.pending[0](unknownPayload);
  await unknownOperation;
  assert.match(uncertainUi.element("ask-result").innerHTML, /outcome uncertain; no verified conclusion/);
  assert.doesNotMatch(uncertainUi.element("ask-result").innerHTML, /Verified Leader|Known Owner/);
  const rejectedUi = await setup();
  const operation = rejectedUi.submit(); await tick();
  const rejected = sourceResult(); rejected.result.reviewer.accepted = false;
  rejectedUi.pending[0](rejected); await operation;
  assert.doesNotMatch(rejectedUi.element("ask-result").innerHTML, /Verified Leader|Known Owner/);
  console.log("PASS: source trust label, partial/conflict prefix, rejected Final hidden and stale source poll ignored");
})().catch(error => {console.error(error); process.exitCode = 1;});
