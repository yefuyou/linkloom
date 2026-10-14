const { strict: assert } = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("src/linkloom/ui/static/product.js", "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const result = value => ({result: {path: "DETERMINISTIC", status: "COMPLETED", query: "current", workspace_id: "a",
  scope: {subject: "Launch", relation: "uses supplier", as_of: "2026-10-10T00:00:00+00:00"}, route: {reason: "lookup"},
  conclusions: [{value}], evidence: [], receipts: [], reviewer: {performed: false}, uncertainty: {unknown_fields: []}}});

async function setup() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: "", textContent: "", value: "", dataset: {}, listeners: {},
      addEventListener(type, fn) {this.listeners[type] = fn;}, setAttribute() {}, removeAttribute() {}});
    return elements.get(id);
  }
  const nav = ["sources", "ask"].map(screen => Object.assign(element(screen), {dataset: {screen}}));
  const pending = [];
  const polls = [];
  let current = "a";
  const workspaces = [{workspace_id: "a", name: "Atlas"}, {workspace_id: "b", name: "Other"}];
  const response = payload => ({ok: true, status: 200, json: async () => payload});
  async function fetch(path, options = {}) {
    if (path.startsWith("/api/product/ask/runs/")) return await new Promise(resolve => polls.push(payload => resolve(response(payload))));
    if (path === "/api/product/ask") return await new Promise(resolve => pending.push(payload => resolve(response(payload))));
    if (path === "/api/workspaces/current" && options.method === "POST") current = JSON.parse(options.body).workspace_id;
    const payload = path === "/api/workspaces" ? {workspaces}
      : path === "/api/workspaces/current" ? {workspace: workspaces.find(w => w.workspace_id === current)}
      : path === "/api/status" ? {ready: false}
      : path === "/api/sources" ? {sources: []}
      : path === "/api/product/ask/config" ? {workspace_id: current, scopes: [{subject: "Launch", relation: "uses supplier"}], agent: {available: false}}
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
  element("ask-slot").value = "0";
  const submit = () => element("page").listeners.submit({preventDefault() {}, target: {id: "ask-form", fields: {query: "What is current?", as_of: ""}}});
  return {element, pending, polls, submit};
}

(async () => {
  for (const field of ["ask-date", "ask-query", "ask-slot"]) {
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
  for (const payload of [result("OBSOLETE_AGENT_FINAL"), {error: {code: "OLD_POLL_ERROR", message: "OLD_POLL_ERROR"}}]) {
    const {element: ui, pending: posts, polls, submit: start} = await setup();
    const investigation = start(); await tick();
    const accepted = result(""); accepted.result.path = "AGENT"; accepted.result.status = "INVESTIGATING"; accepted.result.agent_run_id = "run-1";
    posts[0](accepted); await investigation; await tick();
    await ui("page").listeners.input({target: ui("ask-date")});
    polls[0](payload); await tick(); await tick();
    assert.doesNotMatch(ui("ask-result").innerHTML, /OBSOLETE_AGENT_FINAL|OLD_POLL_ERROR/);
  }
  console.log("PASS: scope/date/query invalidation, latest question wins, workspace response isolation");
})().catch(error => {console.error(error); process.exitCode = 1;});
