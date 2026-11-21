"""Single-file, offline, read-only HTML renderer for a trajectory diff."""

from __future__ import annotations

import json
from typing import Any, Mapping


def _embed(data: Mapping[str, Any]) -> str:
    """JSON safe to place inside a <script> element (no tag, entity or line-separator breakout)."""
    raw = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True)
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_html(diff: Mapping[str, Any]) -> str:
    return _TEMPLATE.replace("__DATA__", _embed(diff))


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LinkLoom Trajectory Diff</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2430;--mut:#667085;--line:#e3e6eb;--a:#2563eb;--b:#d97706;--ok:#15803d;--bad:#b91c1c;--na:#8a919e}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:18px 28px;background:#111827;color:#fff}header h1{margin:0;font-size:20px}header p{margin:4px 0 0;color:#cbd2dc;font-size:13px}
nav{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid var(--line);padding:8px 28px;display:flex;gap:6px;flex-wrap:wrap}
button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:6px;padding:4px 12px;cursor:pointer}
button.on{background:#111827;color:#fff;border-color:#111827}
main{padding:20px 28px;max-width:1200px;margin:auto}section{display:none}section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.card h2{margin:0 0 8px;font-size:16px}.card h3{margin:12px 0 4px;font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600}
.A{color:var(--a)}.B{color:var(--b)}.na{color:var(--na);font-style:italic}.bad{color:var(--bad)}.good{color:var(--ok)}
code,.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;word-break:break-all}
.row{border:1px solid var(--line);border-radius:6px;margin:4px 0;background:#fff}.row>.head{display:flex;gap:10px;padding:6px 10px;cursor:pointer;align-items:center}
.row>.body{display:none;padding:8px 12px;border-top:1px solid var(--line);background:#fafbfc}.row.open>.body{display:block}
.tag{font-size:11px;padding:1px 7px;border-radius:10px;background:#eef1f5}.diff{background:#fff7e6}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}.cols.one{grid-template-columns:1fr}
.note{color:var(--mut);font-size:12px}.warn{background:#fff4f4;border:1px solid #f3c6c6;border-radius:6px;padding:6px 10px;margin:4px 0}
pre{margin:0;white-space:pre-wrap;word-break:break-word}
</style></head><body>
<header><h1>LinkLoom Trajectory Diff</h1>
<p>Read-only comparison of two persisted runs. Values come from recorded Trace, Checkpoint and Recovery data; anything not recorded is shown as N/A with the reason. Similarity is never reported as accuracy.</p></header>
<nav id="nav"></nav><main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const R = D.runs, CL = ["A", "B"];
function el(tag, cls, text, kids) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  (kids || []).forEach(k => e.appendChild(k));
  return e;
}
function mv(m) {
  if (!m) return el("span", "na", "N/A");
  if (m.value === null || m.value === undefined) { const s = el("span", "na", "N/A"); s.title = m.na || ""; return s; }
  return el("span", "", typeof m.value === "object" ? JSON.stringify(m.value) : m.value);
}
function mvText(m) { return (m && m.value !== null && m.value !== undefined) ? (typeof m.value === "object" ? JSON.stringify(m.value) : String(m.value)) : "N/A"; }
function card(title, kids) { return el("div", "card", null, [el("h2", null, title)].concat(kids)); }
function table(head, rows) {
  const t = el("table"); t.appendChild(el("tr", null, null, head.map(h => el("th", null, h))));
  rows.forEach(r => { const tr = el("tr", r.cls || ""); r.cells.forEach(c => tr.appendChild(el("td", null, null, [c instanceof Node ? c : document.createTextNode(String(c))]))); t.appendChild(tr); });
  return t;
}
function expandable(headParts, bodyObj, extraCls) {
  const row = el("div", "row " + (extraCls || ""));
  const head = el("div", "head", null, headParts);
  const body = el("div", "body", null, [el("pre", "mono", JSON.stringify(bodyObj, null, 2))]);
  head.addEventListener("click", () => row.classList.toggle("open"));
  row.appendChild(head); row.appendChild(body); return row;
}
const labelOf = i => el("b", CL[i], "Run " + CL[i]);

function overview() {
  const keys = [["l2.final_status", "Final status"], ["l3.provider_requests", "Provider requests"], ["l3.tool_calls", "Tool calls"],
    ["l3.failures", "Failures"], ["l3.wall_time_ms", "Wall time (ms)"], ["l3.summed_duration_ms", "Summed event duration (ms)"],
    ["l3.input_tokens", "Input tokens"], ["l3.output_tokens", "Output tokens"], ["l3.estimated_cost_usd", "Estimated model cost (USD)"],
    ["l3.reserved_budget", "Budget reservation (limits, not cost)"], ["l3.handoffs", "Agent handoffs"], ["l3.checkpoints_saved", "Checkpoints saved"],
    ["l3.interrupts", "Interrupts"], ["l3.resumes", "Resumes"], ["l3.max_concurrency", "Peak concurrency"],
    ["l1.policy_rejections", "Policy rejections recorded"], ["l1.tool_failures", "Tool failures"], ["l1.record_complete", "Manifest complete"],
    ["l1.event_log_consistent", "Event log consistent"], ["l1.source_index_matches", "Source index matches manifest"],
    ["l1.artifact_present", "Result artifact present"], ["l1.uncertain_items", "Uncertain (unverified) items"], ["l1.safe_stop", "Safe stop"]];
  const m = {}; D.metrics.forEach(x => m[x.key] = x);
  const only = el("label", null, null, []); const cb = el("input"); cb.type = "checkbox"; only.appendChild(cb); only.appendChild(document.createTextNode(" show only differences"));
  const holder = el("div");
  function draw() {
    holder.textContent = "";
    const rows = [];
    keys.forEach(([k, name]) => {
      const x = m[k]; if (!x) return; if (cb.checked && x.same) return;
      rows.push({ cls: x.same ? "" : "diff", cells: [name, mv(x.a), mv(x.b), x.same ? "same" : "differs"] });
    });
    holder.appendChild(table(["Metric", "Run A", "Run B", ""], rows));
  }
  cb.addEventListener("change", draw); draw();
  const prov = table(["", "Run A", "Run B"], [
    { cells: ["run_id", R[0].run_id, R[1].run_id] },
    { cells: ["execution mode", R[0].provenance.execution_mode, R[1].provenance.execution_mode] },
    { cells: ["mock model", String(R[0].provenance.is_mock), String(R[1].provenance.is_mock)] }]);
  return [card("Run A / Run B overview", [prov, el("p", "note", "Hover an N/A for the reason. Zero recorded rejections does not prove that security checks passed."), only, holder])];
}

function timeline() {
  const view = { mode: "both" };
  const wrap = el("div");
  const bar = el("div"); const out = el("div");
  [["both", "Side by side"], ["0", "Run A"], ["1", "Run B"]].forEach(([v, t]) => {
    const b = el("button", v === view.mode ? "on" : "", t);
    b.addEventListener("click", () => { view.mode = v; Array.from(bar.children).forEach(c => c.classList.toggle("on", c === b)); draw(); });
    bar.appendChild(b);
  });
  function col(i) {
    const run = R[i]; const c = el("div", null, null, [el("h3", null, "Run " + CL[i] + " (ordered by seq; trace has no wall-clock for most events)")]);
    const calls = {}; run.models.forEach(mod => { if (mod.tool_id) calls[mod.sequence] = mod; });
    run.timeline.forEach(ev => {
      const parts = [el("span", "mono", "#" + ev.seq), el("b", CL[i], ev.event_type), el("span", "tag", ev.actor), el("span", "tag " + (ev.status === "failed" ? "bad" : ""), ev.status)];
      if (ev.call_id) parts.push(el("span", "mono", ev.call_id));
      if (ev.attributes && ev.attributes.tool) parts.push(el("span", "tag", ev.attributes.tool));
      c.appendChild(expandable(parts, ev));
    });
    c.appendChild(el("h3", null, "Model turns (from checkpoint, not positioned in trace)"));
    if (!run.models.length) c.appendChild(el("p", "na", "N/A: no checkpoint loaded"));
    run.models.forEach(mod => c.appendChild(expandable([el("span", "mono", "turn " + mod.sequence), el("b", CL[i], mod.action_kind || "?"), el("span", "tag", mod.status), el("span", "mono", mod.tool_id || "")], mod)));
    return c;
  }
  function draw() {
    out.textContent = "";
    const idx = view.mode === "both" ? [0, 1] : [Number(view.mode)];
    const g = el("div", "cols" + (idx.length === 1 ? " one" : "")); idx.forEach(i => g.appendChild(col(i))); out.appendChild(g);
  }
  draw(); wrap.appendChild(bar); wrap.appendChild(out);
  return [card("Execution timeline", [el("p", "note", "Click a row to expand its recorded details."), wrap])];
}

function evidence() {
  const E = D.evidence, out = [];
  const filt = { v: "all" }; const bar = el("div"); const holder = el("div");
  const sets = { shared: E.shared, A: E.only_a, B: E.only_b };
  const all = [].concat(E.shared, E.only_a, E.only_b);
  function ident(eid) {
    for (const r of R) { const x = r.l2.identities[eid]; if (x) return x; }
    return { evidence_id: eid };
  }
  function draw() {
    holder.textContent = "";
    const groups = filt.v === "all" ? ["shared", "A", "B"] : [filt.v];
    groups.forEach(g => {
      holder.appendChild(el("h3", null, (g === "shared" ? "Cited by both" : "Cited only by Run " + g) + " (" + sets[g].length + ")"));
      if (!sets[g].length) holder.appendChild(el("p", "na", "none"));
      sets[g].forEach(eid => { const id = ident(eid); holder.appendChild(expandable([el("span", g === "B" ? "B" : g === "A" ? "A" : "", g), el("span", "mono", eid.slice(0, 22) + "..."), el("span", "tag", id.source_ref || "source N/A"), el("span", "tag", id.status || "status N/A")], id)); });
    });
  }
  [["all", "All"], ["shared", "Shared"], ["A", "Only A"], ["B", "Only B"]].forEach(([v, t]) => {
    const b = el("button", v === "all" ? "on" : "", t);
    b.addEventListener("click", () => { filt.v = v; Array.from(bar.children).forEach(c => c.classList.toggle("on", c === b)); draw(); }); bar.appendChild(b);
  });
  draw();
  out.push(card("Citation difference", [E.comparable ? el("p", "note", "Evidence ids share one workspace_id, so they are comparable.") : el("p", "warn", E.reason), bar, holder]));
  const rows = [];
  [["Final status", r => mvText(r.l2.final_status)], ["Review decision", r => mvText(r.l2.review_decision)],
   ["Cited evidence", r => r.l2.cited.length], ["Verified among cited", r => r.l2.verified_cited.length],
   ["Evidence gaps (cited but never seen in a tool result)", r => r.l2.gaps.length],
   ["Retrieved but not cited", r => r.l2.retrieved_not_cited.length],
   ["Gold recall", r => mvText(r.l2.gold_recall)], ["Rubric accuracy", r => mvText(r.l2.rubric_accuracy)]]
    .forEach(([n, f]) => rows.push({ cells: [n, f(R[0]), f(R[1])] }));
  out.push(card("Outcome and gaps", [table(["", "Run A", "Run B"], rows),
    el("p", "note", "Gold Recall and Rubric Accuracy are N/A: " + (R[0].l2.gold_recall.na || "") + ". Citation overlap is not correctness. Final answer text is deliberately not compared.")]));
  return out;
}

function recovery() {
  return CL.map((c, i) => {
    const rc = R[i].recovery;
    if (!rc) return card("Run " + c, [el("p", "na", "N/A: no checkpoint loaded")]);
    const d = rc.decision;
    const blocked = d.decision === "requires_verification" || d.decision === "requires_manual_decision";
    return card("Run " + c + " fault and recovery state", [
      el("p", null, null, [el("b", blocked ? "bad" : "", d.decision), document.createTextNode(" / " + d.reason_code + ": " + d.message)]),
      el("p", "note", "Recommendation only, from decide_model_resume(); the viewer never resumes, retries or invokes anything."),
      el("h3", null, "Termination"), el("p", null, rc.termination ? rc.termination.status + " (" + rc.termination.reason_code + ")" : "N/A"),
      el("h3", null, "Model execution records"),
      table(["#", "status", "note"], rc.model_executions.map(x => ({ cells: [x.sequence, x.status, x.note], cls: /unverified|failed/.test(x.note) ? "diff" : "" }))),
      el("h3", null, "Tool ledger"),
      table(["call", "tool", "status", "note"], rc.tool_ledger.map(x => ({ cells: [x.call_id, x.tool_id, x.status, x.note] }))),
      el("h3", null, "Checkpoints"),
      table(["id", "step", "status", "current step"], rc.checkpoints.map(x => ({ cells: [x.checkpoint_id, x.step_seq, x.status, x.current_step || ""] })))]);
  });
}

function provenance() {
  return CL.map((c, i) => {
    const p = R[i].provenance;
    const nas = [];
    ["l1", "l2", "l3"].forEach(L => Object.entries(R[i][L]).forEach(([k, v]) => { if (v && v.na) nas.push({ cells: [L + "." + k, v.na] }); }));
    return card("Run " + c + " provenance", [
      table(["field", "value"], [["run_id", R[i].run_id], ["thread_id", p.thread_id], ["execution mode", p.execution_mode], ["data source", p.data_source],
        ["mock model", String(p.is_mock)], ["events read", p.event_count], ["manifest complete", String(p.manifest_complete)], ["checkpoint loaded", String(p.checkpoint_loaded)]].map(c2 => ({ cells: c2 }))),
      el("h3", null, "Diagnostics"),
      p.diagnostics.length ? el("div", null, null, p.diagnostics.map(t => el("div", "warn", t))) : el("p", "good", "none"),
      el("h3", null, "Metrics not computed, and why"), nas.length ? table(["metric", "reason"], nas) : el("p", null, "none")]);
  });
}

const TABS = [["Overview", overview], ["Execution Timeline", timeline], ["Evidence & Outcome", evidence], ["Fault & Recovery", recovery], ["Data Provenance", provenance]];
const nav = document.getElementById("nav"), main = document.getElementById("main");
TABS.forEach(([name, fn], n) => {
  const sec = el("section"); fn().forEach(x => sec.appendChild(x)); main.appendChild(sec);
  const b = el("button", n === 0 ? "on" : "", name);
  if (n === 0) sec.classList.add("on");
  b.addEventListener("click", () => { Array.from(nav.children).forEach((x, j) => { x.classList.toggle("on", j === n); main.children[j].classList.toggle("on", j === n); }); });
  nav.appendChild(b);
});
</script></body></html>
"""
