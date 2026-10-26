const {strict: assert} = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync("src/linkloom/ui/static/product.js", "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const candidate = {candidate_id: "new", claim_type: "DECISION", proposed_value: "Wrenwell", subject: "Launch",
  relation: "uses supplier", status: "PENDING_REVIEW", review_reasons: [], valid_from: "2026-10-07",
  current_unresolved_fields: [], source: {name: "current.md"},
  evidence: {quote: "Current source", line_start: 1, line_end: 1}};
const view = {workspace_id: "a", original: {decision_id: "old", subject: "Launch", relation: "uses supplier",
  value: "Birchline", memory_state: "STALE", valid_from: "2026-10-01", evidence: [{quote: "<script>unsafe</script>"}]},
  original_receipts: [], current_sources: [], candidates: [candidate], reasons: [{code: "SOURCE_REMOVED"}], status: "UNRESOLVED", review_token: "snapshot"};

async function setup() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: "", value: "", textContent: "", dataset: {}, listeners: {},
      addEventListener(name, fn) {this.listeners[name] = fn;}, setAttribute() {}, removeAttribute() {}});
    return elements.get(id);
  };
  const nav = ["sources", "review", "decisions", "ask"].map(id => Object.assign(element(id), {dataset: {screen: id}}));
  const workspaces = [{workspace_id: "a", name: "Atlas"}, {workspace_id: "b", name: "Other"}];
  let workspace = "a";
  let deferCandidate = false;
  let pending;
  let currentView = structuredClone(view);
  const response = payload => ({ok: true, status: 200, json: async () => payload});
  async function fetch(path, options = {}) {
    if (path === "/api/review/candidates/new") {
      if (deferCandidate) return await new Promise(resolve => pending = () => resolve(response({candidate})));
      return response({candidate});
    }
    if (path === "/api/review/revalidation/old") return response({revalidation: currentView});
    if (path === "/api/workspaces/current" && options.method === "POST") workspace = JSON.parse(options.body).workspace_id;
    return response(path === "/api/workspaces" ? {workspaces}
      : path === "/api/workspaces/current" ? {workspace: workspaces.find(item => item.workspace_id === workspace)}
      : path === "/api/status" ? {ready: false}
      : {sources: [], candidates: [], decisions: []});
  }
  vm.runInNewContext(code, {document: {documentElement: {lang: "en"}, getElementById: element, querySelectorAll: () => nav},
    fetch, window: {setTimeout() {}}, setTimeout, console});
  await tick(); await tick();
  const click = (action, id) => element("page").listeners.click({target: {closest: () => ({dataset: {action, id}})}});
  return {element, click, setView: next => currentView = next, defer: () => deferCandidate = true, resolve: () => pending()};
}

(async () => {
  const ui = await setup();
  await ui.click("revalidate-decision", "old");
  assert.match(ui.element("page").innerHTML, /Original decision: Birchline/);
  assert.match(ui.element("page").innerHTML, /Keep unresolved/);
  assert.match(ui.element("page").innerHTML, /&lt;script&gt;unsafe/);
  assert.doesNotMatch(ui.element("page").innerHTML, /<script>unsafe/);
  await ui.click("revalidate-candidate", "new");
  assert.match(ui.element("page").innerHTML, /New approval for a stale decision/);
  assert.match(ui.element("page").innerHTML, /Original: Birchline/);
  assert.match(ui.element("page").innerHTML, /Keep unresolved \/ back to comparison/);

  const resolved = structuredClone(view);
  Object.assign(resolved, {status: "RESOLVED", candidates: [], completion: {decision_id: "new", authorized_at: "2026-10-21"}});
  ui.setView(resolved);
  await ui.click("revalidate-decision", "old");
  assert.match(ui.element("page").innerHTML, /This revalidation is complete/);
  assert.doesNotMatch(ui.element("page").innerHTML, /stays unresolved/);

  const racing = await setup();
  await racing.click("revalidate-decision", "old");
  racing.defer();
  const inspection = racing.click("revalidate-candidate", "new");
  await tick();
  racing.element("workspace-picker").value = "b";
  await racing.element("workspace-picker").listeners.change();
  racing.resolve(); await inspection;
  assert.doesNotMatch(racing.element("page").innerHTML, /Original: Birchline|New approval for a stale decision/);
  const localeRace = await setup();
  await localeRace.click("revalidate-decision", "old");
  await localeRace.click("revalidate-candidate", "new");
  localeRace.element("reviewer-id").value = "OLD_WORKSPACE_REVIEWER";
  localeRace.element("page").querySelectorAll = () => [{id: "reviewer-id", value: "OLD_WORKSPACE_REVIEWER"}];
  localeRace.defer();
  const languageChange = localeRace.element("interface-language").listeners.change({target: {value: "zh"}});
  await tick();
  localeRace.element("workspace-picker").value = "b";
  await localeRace.element("workspace-picker").listeners.change();
  localeRace.element("reviewer-id").value = "NEW_WORKSPACE_VALUE";
  localeRace.resolve(); await languageChange;
  assert.equal(localeRace.element("reviewer-id").value, "NEW_WORKSPACE_VALUE",
    "late language refresh must not restore old workspace form values");
  assert.doesNotMatch(localeRace.element("page").innerHTML, /Original: Birchline|OLD_WORKSPACE_REVIEWER/);
  console.log("PASS: stale comparison, separate approval, escaped evidence, resolved copy, workspace race isolation");
})().catch(error => {console.error(error); process.exitCode = 1;});
