/* Issue #104: pending source claims in Ask and the decision timeline link to the Review candidate. Offline, no model. */
const {strict: assert} = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync("src/linkloom/ui/static/product.js", "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));

const evidence = {source_id: "src-b", source_name: "b.md", source_version: "v-b", evidence_ref: "ref-b",
  quote: "We decided to use Birchline.", line_start: 1, line_end: 1, verified: true};
const approved = {decision_id: "decision-b", subject: "Launch", relation: "uses supplier", value: "Birchline",
  memory_state: "ACTIVE", valid_from: "2026-10-01T10:00:00+00:00", evidence: [evidence]};
const pendingItem = (id, value, extra = {}) => ({candidate_id: id, value, review_status: "PENDING_REVIEW",
  effective_from: "2026-10-05T10:00:00+00:00", authority: "NON_AUTHORITATIVE",
  evidence: {quote: `Someone said ${value}.`, line_start: 3, line_end: 4, source_name: "c.md", verified: true}, ...extra});
const candidateDetail = id => ({candidate_id: id, claim_type: "DECISION", proposed_value: "Cedar", subject: "Launch",
  relation: "uses supplier", status: "PENDING_REVIEW", review_reasons: [], valid_from: "2026-10-05",
  current_unresolved_fields: [], source: {name: "c.md", source_id: "src-c"},
  evidence: {quote: "Someone said Cedar.", line_start: 3, line_end: 4}});

async function setup({timeline, workspaceId = "w", missing = new Set()}) {
  const elements = new Map(), requested = [], writes = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {id, value: "", dataset: {}, innerHTML: "", textContent: "", listeners: {},
      isConnected: true, setAttribute() {}, removeAttribute() {}, hasAttribute() {return false;},
      addEventListener(name, fn) {this.listeners[name] = fn;}, querySelectorAll() {return [];}, querySelector() {return null;}});
    return elements.get(id);
  };
  const nav = ["sources", "decisions", "ask"].map(id => Object.assign(element(id), {dataset: {screen: id}}));
  const response = (payload, status = 200) => ({ok: status < 400, status, json: async () => payload});
  async function fetch(path, options = {}) {
    if (options.method && options.method !== "GET") writes.push({path, method: options.method});
    requested.push(path);
    if (path.startsWith("/api/review/candidates/")) {
      const id = decodeURIComponent(path.split("/").pop());
      return missing.has(id) ? response({error: {code: "CANDIDATE_NOT_FOUND", message: "The candidate is unavailable in this workspace."}}, 404)
        : response({candidate: candidateDetail(id)});
    }
    if (path === "/api/workspaces") return response({workspaces: [{workspace_id: workspaceId, name: "Synthetic"}]});
    if (path === "/api/workspaces/current") return response({workspace: {workspace_id: workspaceId, name: "Synthetic"}});
    if (path === "/api/status") return response({ready: false});
    if (path === "/api/product/ask/config") return response({workspace_id: workspaceId, scopes: [{subject: "Launch", relation: "uses supplier"}],
      investigation_context: {source_count: 1, sources: []}, agent: {available: false}});
    if (path === "/api/product/ask") return response({result: {workspace_id: workspaceId, path: "DETERMINISTIC", status: "COMPLETED",
      query: "Which supplier?", scope: {subject: "Launch", relation: "uses supplier", as_of: "2026-10-10"},
      conclusions: timeline.effective ? [{decision_id: "decision-b", value: "Birchline", effective_at: approved.valid_from}] : [],
      evidence: timeline.effective ? [evidence] : [], timeline}});
    if (path === "/api/decisions/decision-b") return response({decision: approved});
    if (path === "/api/decisions/decision-b/history") return response({history: [approved]});
    if (path === "/api/decisions/recover") return response({recovery: timeline});
    return response({sources: [], candidates: [], decisions: [approved]});
  }
  vm.runInNewContext(code, {document: {documentElement: {lang: "en"}, getElementById: element, querySelectorAll: () => nav},
    fetch, window: {setTimeout() {}, location: {pathname: "/", search: "?lang=en", hash: "#ask"}, history: {replaceState() {}},
      localStorage: {getItem: () => null, setItem() {}}, sessionStorage: {getItem() {return null;}}, addEventListener() {}},
    URLSearchParams, setTimeout, console,
    FormData: class {constructor(form) {this.fields = form.fields || {};} *[Symbol.iterator]() {yield* Object.entries(this.fields);} get(key) {return this.fields[key];}}});
  await tick(); await tick();
  const click = (dataset) => element("page").listeners.click({target: {closest: () => ({dataset, tagName: "BUTTON"})}});
  const ask = async () => {
    await element("ask").listeners.click(); await tick();
    element("ask-form").fields = {query: "Which supplier?", as_of: ""};
    await element("page").listeners.submit({preventDefault() {}, target: element("ask-form")});
    await tick();
    return element("ask-result").innerHTML;
  };
  return {element, click, ask, requested, writes};
}

const base = {status: "FOUND", as_of: "2026-10-10T00:00:00+00:00", effective: approved, history: [approved],
  transitions: [], proposals: [], source_conflicts: []};

(async () => {
  // Scenario A: approved B stays the formal answer; pending C is separate and links to Review.
  const a = await setup({timeline: {...base, pending_conflicts: [pendingItem("cand-c", "Cedar")]}});
  const askA = await a.ask();
  assert.match(askA, /ask-answer-value">Birchline/, "approved decision remains the formal answer");
  const sectionA = askA.split('class="pending-source-conflicts"')[1];
  assert.ok(sectionA, "pending section rendered");
  assert.match(sectionA, /differ from the current approved decision/);
  assert.match(sectionA, /Cedar/); assert.match(sectionA, /c\.md/); assert.match(sectionA, /Someone said Cedar\./);
  assert.match(sectionA, /3.4/); assert.match(sectionA, /2026-10-05/);
  assert.match(sectionA, /Go to Review to verify/);
  assert.match(sectionA, /data-action="inspect-candidate" data-id="cand-c"/);
  assert.doesNotMatch(askA, /formal decisions conflict|两条正式决策冲突|assigned to/i);
  await a.click({action: "inspect-candidate", id: "cand-c"});
  assert.deepEqual(a.requested.filter(p => p.startsWith("/api/review/candidates/")), ["/api/review/candidates/cand-c"]);
  assert.match(a.element("page").innerHTML, /Cedar/);
  assert.match(a.element("page").innerHTML, /Back to Review/);
  assert.deepEqual(a.writes.map(w => w.path), ["/api/product/ask"], "only the read-only Ask query is posted; Review navigation never writes");

  // Scenario A from the timeline.
  await a.click({action: "ask-timeline", id: "decision-b", asOf: "2026-10-10"});
  const timeline = a.element("page").innerHTML;
  assert.match(timeline, /pending-source-conflicts/);
  assert.match(timeline, /data-action="inspect-candidate" data-id="cand-c"/);
  assert.ok(timeline.indexOf("pending-source-conflicts") < timeline.indexOf("Decision timeline"));
  await a.click({action: "inspect-candidate", id: "cand-c"});
  assert.match(a.element("page").innerHTML, /Someone said Cedar\./);
  assert.ok(a.writes.every(w => ["/api/product/ask", "/api/decisions/recover"].includes(w.path)), "timeline and Review navigation only read");

  // Scenario B: no formal decision; both sides and review entries, never "formal decisions conflict".
  const b = await setup({timeline: {...base, status: "SOURCE_CONFLICT_UNREVIEWED", effective: null, history: [],
    pending_conflicts: [pendingItem("cand-b", "Birchline"), pendingItem("cand-c", "Cedar", {review_status: "CONFLICT"})]}});
  const askB = await b.ask();
  assert.match(askB, /Pending sources disagree; there is no formal decision yet/);
  assert.doesNotMatch(askB, /ask-answer-value/);
  assert.equal((askB.match(/data-action="inspect-candidate"/g) || []).length, 2);
  assert.match(askB, /Birchline/); assert.match(askB, /Cedar/);
  assert.doesNotMatch(askB, /formal decisions conflict|两条正式决策冲突/);
  const zh = await setup({timeline: {...base, status: "SOURCE_CONFLICT_UNREVIEWED", effective: null, history: [],
    pending_conflicts: [pendingItem("cand-b", "Birchline"), pendingItem("cand-c", "Cedar")]}});
  zh.element("interface-language").value = "zh";
  await zh.element("interface-language").listeners.change({target: {value: "zh"}});
  await tick();
  assert.match(await zh.ask(), /待审核来源存在不同说法，当前无正式决定/);

  // Scenario C and negatives arrive as an empty list: nothing is rendered.
  const c = await setup({timeline: {...base, pending_conflicts: []}});
  const askC = await c.ask();
  assert.doesNotMatch(askC, /pending-source-conflicts|inspect-candidate/);
  assert.match(askC, /ask-answer-value">Birchline/);

  // Stale/deleted/version-changed candidates never get a link; a stale click fails inside the workspace.
  const stale = await setup({timeline: {...base, pending_conflicts: [
    pendingItem("cand-reviewed", "Cedar", {review_status: "REJECTED"}), pendingItem("", "Dune"), pendingItem(null, "Elm"),
    pendingItem("cand-stale", "Fern", {review_status: "STALE"}), pendingItem("cand-gone", "Gale")]}, missing: new Set(["cand-gone"])});
  const askStale = await stale.ask();
  assert.equal((askStale.match(/data-action="inspect-candidate"/g) || []).length, 1, "only the reviewable candidate with an id links");
  assert.match(askStale, /data-id="cand-gone"/);
  assert.doesNotMatch(askStale, /data-id="cand-reviewed"|data-id="cand-stale"/);
  await stale.click({action: "inspect-candidate", id: "cand-gone"});
  assert.doesNotMatch(stale.element("page").innerHTML, /Someone said Gale\.[\s\S]*Back to Review/, "missing candidate must not open another record");
  assert.match(stale.element("page").innerHTML, /unavailable|CANDIDATE_NOT_FOUND/);

  // HTML in every untrusted field is escaped, in Ask and in the timeline.
  const xss = "<img src=x onerror=alert(1)>";
  const evil = pendingItem("cand-\"x", xss, {evidence: {quote: xss, line_start: 1, line_end: 1, source_name: xss}});
  const escaped = await setup({timeline: {...base, pending_conflicts: [evil]}});
  const askEscaped = await escaped.ask();
  assert.doesNotMatch(askEscaped, /<img src=x/);
  assert.match(askEscaped, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(askEscaped, /data-id="cand-"x"/);
  await escaped.click({action: "ask-timeline", id: "decision-b", asOf: ""});
  assert.doesNotMatch(escaped.element("page").innerHTML, /<img src=x/);
  console.log("PASS: pending source claims in Ask and timeline, Review navigation, no link for stale candidates, escaping");
})().catch(error => {console.error(error); process.exitCode = 1;});
