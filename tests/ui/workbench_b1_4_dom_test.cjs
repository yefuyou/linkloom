/* B1-4 Workbench progressive enhancement DOM regression test.
 * Verifies 3-view hierarchy bar, side-by-side review split, undated-decisions zone,
 * truthful Ask Trace drawer, and global settings drawer with Glossary API wiring.
 */
const { strict: assert } = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const tick = () => new Promise((resolve) => setTimeout(resolve, 15));

function makeEl(id, extra = {}) {
  const listeners = {};
  const attrs = {};
  const classes = new Set();
  const children = [];
  let _innerHTML = "";
  const el = {
    id,
    value: "",
    hidden: false,
    open: false,
    disabled: false,
    dataset: {},
    style: {},
    selectedOptions: [],
    isConnected: true,
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      contains: (c) => classes.has(c),
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k] ?? null; },
    removeAttribute(k) { delete attrs[k]; },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    dispatch(type, event = {}) { (listeners[type] || []).forEach((fn) => fn(event)); },
    appendChild(child) { children.push(child); return child; },
    insertBefore(child) { children.push(child); return child; },
    remove() {},
    focus() {},
    click() {},
    showModal() { this.open = true; },
    close() { this.open = false; this.dispatch("close"); },
    get children() { return children; },
    get innerHTML() { return _innerHTML; },
    set innerHTML(v) { _innerHTML = String(v); },
    querySelector(sel) {
      if (sel === "#candidate-review" && _innerHTML.includes('id="candidate-review"')) return makeEl("candidate-review");
      if (sel === ".detail-layout" && _innerHTML.includes('class="detail-layout"')) {
        return {
          dataset: {},
          classList: el.classList,
          querySelector(sub) {
            if (sub === "[data-evidence-source]") {
              return {
                dataset: { evidenceSource: "src-1" },
                querySelector: () => ({ textContent: "We decided on Birchline." }),
                appendChild(node) { children.push(node); },
              };
            }
            return null;
          },
        };
      }
      if (sel === "#decisions-date-filter" && _innerHTML.includes('id="decisions-date-filter"')) return makeEl("decisions-date-filter");
      if (sel === ".undated-zone") return children.find((c) => c.className?.includes("undated-zone")) || null;
      if (sel === "#ask-result" && _innerHTML.includes('id="ask-result"')) {
        return {
          querySelector(sub) {
            if (sub === ".ask-answer") {
              return {
                querySelector(s2) {
                  if (s2 === ".trace-drawer") return children.find((c) => c.className?.includes("trace-drawer")) || null;
                  if (s2 === ".ask-technical pre") {
                    return {
                      textContent: JSON.stringify({
                        status: "COMPLETED",
                        path: "DETERMINISTIC",
                        scope: { subject: "Launch", relation: "uses supplier" },
                      }),
                    };
                  }
                  return null;
                },
                appendChild(node) { children.push(node); },
                insertBefore(node) { children.push(node); },
              };
            }
            return null;
          },
        };
      }
      return null;
    },
    ...extra,
  };
  return el;
}

(async () => {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, makeEl(id));
    return elements.get(id);
  };

  const wsPicker = getEl("workspace-picker");
  wsPicker.value = "ws-alpha";
  wsPicker.selectedOptions = [{ textContent: "Alpha Team" }];
  const reviewCount = getEl("review-count");
  reviewCount.textContent = "3";
  const providerState = getEl("provider-state");
  providerState.textContent = "AI extraction is off";
  providerState.dataset = { ready: "false" };

  const location = { hash: "#meetings" };
  const windowListeners = {};
  const calls = [];
  const fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null });
    if (url === "/api/decisions/undated") {
      return {
        ok: true,
        json: async () => ({
          undated: [{
            decision_id: "udec_1",
            decision_ref: "dref_1",
            subject: "银杏项目",
            relation: "uses supplier",
            value: "Wrenwell",
            memory_state: "CONFIRMED_UNDATED",
            evidence_status: "VERIFIED",
            evidence: [{ quote: "启用时间待定", source_name: "b.md", line_start: 2, line_end: 2 }],
          }],
        }),
      };
    }
    if (url.startsWith("/api/sources/")) {
      return {
        ok: true,
        json: async () => ({ source: { source_id: "src-1", content: "Line 1\nWe decided on Birchline.\nLine 3" } }),
      };
    }
    if (url.startsWith("/api/product/feedback?")) {
      return {
        ok: true,
        json: async () => ({
          entries: [{ id: "fb-1", term: "供方", relation: "uses supplier", status: "pending", actor: "Alice" }],
        }),
      };
    }
    if (url.startsWith("/api/product/feedback/")) {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  let observerCb = null;
  class MockObserver {
    constructor(cb) { observerCb = cb; }
    observe() {}
  }

  const page = getEl("page");
  const contextBar = getEl("workbench-context-bar");
  const settingsDialog = getEl("settings-drawer");
  const settingsBody = getEl("settings-drawer-body");
  const settingsTrigger = getEl("settings-trigger");

  const windowObj = {
    location,
    localStorage: { getItem: () => "Alice", setItem() {} },
    addEventListener: (t, fn) => { (windowListeners[t] ||= []).push(fn); },
  };
  vm.runInNewContext(fs.readFileSync("src/linkloom/ui/static/product-workbench.js", "utf8"), {
    document: {
      documentElement: { lang: "zh" },
      activeElement: settingsTrigger,
      getElementById: (id) => getEl(id),
      createElement: (tag) => makeEl(`created-${tag}`),
    },
    window: windowObj,
    MutationObserver: MockObserver,
    fetch,
    setTimeout,
    queueMicrotask,
    FormData: class { constructor(f) { this.fields = f.fields || {}; } *[Symbol.iterator]() { yield* Object.entries(this.fields); } },
  });

  await tick();
  assert.match(contextBar.innerHTML, /① 会议与审核/);
  assert.match(contextBar.innerHTML, /Alpha Team/);
  assert.match(contextBar.innerHTML, /3/);

  // 1. Side-by-side review split
  page.innerHTML = '<div class="detail-layout"><div data-evidence-source="src-1"></div><form id="candidate-review"></form></div>';
  observerCb();
  await tick();
  assert.ok(calls.some((c) => c.url === "/api/sources/src-1"), "should fetch source transcript for side-by-side reader");

  // 2. Decisions undated zone
  location.hash = "#decisions";
  page.innerHTML = '<form id="decisions-date-filter"></form>';
  (windowListeners.hashchange || []).forEach((fn) => fn());
  await tick();
  assert.match(contextBar.innerHTML, /② 决议与任务演变/);
  const undated = page.children.find((c) => c.className === "panel undated-zone");
  assert.ok(undated, "should render undated-decisions zone");
  assert.match(undated.innerHTML, /时间未知 \/ 待核验区域/);
  assert.ok(calls.some((c) => c.url === "/api/decisions/undated"), "should call GET /api/decisions/undated");

  // 3. Ask execution & trace drawer (direct data flow via onLinkLoomAskResult, no DOM JSON.parse)
  location.hash = "#ask";
  page.innerHTML = '<div id="ask-result"><section class="panel ask-answer"><details class="ask-technical"><pre></pre></details></section></div>';
  (windowListeners.hashchange || []).forEach((fn) => fn());
  await tick();
  windowObj.onLinkLoomAskResult({
    status: "COMPLETED",
    path: "DETERMINISTIC",
    scope: { subject: "Launch", relation: "uses supplier" },
    execution: {
      schema_version: "ask-execution/v1",
      answer_path: "VERIFIED_STORE_LOOKUP",
      answer_provider_calls: 0,
      router_provider_calls: 1,
      router_reported_usage: null,
      agent_run_id: null,
    },
  }, page.querySelector("#ask-result"));
  const trace = page.children.find((c) => c.className === "trace-drawer");
  assert.ok(trace, "should render trace drawer");
  assert.match(trace.innerHTML, /Router: 1/);
  assert.match(trace.innerHTML, /未报告/);
  assert.doesNotMatch(trace.innerHTML, /0 模型调用/, "must not report 0 model calls when router_provider_calls is 1");

  // 4. Settings drawer & Glossary
  settingsTrigger.dispatch("click");
  await tick();
  assert.equal(settingsDialog.open, true);
  assert.match(settingsBody.innerHTML, /Alpha Team/);
  assert.match(settingsBody.innerHTML, /工作区术语表/);

  console.log("PASS: B1-4 workbench 3-view bar, side-by-side review, undated zone, Ask trace drawer, and settings drawer");
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
