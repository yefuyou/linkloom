(() => {
  const state = {
    screen: "sources",
    workspaces: [],
    workspace: null,
    provider: null,
    sourceId: null,
    candidateId: null,
    decisionId: null,
    agentViewToken: 0,
    language: document.documentElement.lang || "en",
  };
  const page = document.getElementById("page");
  const live = document.getElementById("status-live");
  const workspacePicker = document.getElementById("workspace-picker");

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      });
    } catch {
      throw new Error("LinkLoom could not reach its local application. Check that the server is still running.");
    }
    let payload;
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload?.error?.message || "The operation could not be completed.");
      error.code = payload?.error?.code || "REQUEST_FAILED";
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function send(method, path, data) {
    return request(path, { method, body: JSON.stringify(data || {}) });
  }

  function announce(message) { live.textContent = message; }

  function badge(value) {
    const status = String(value || "UNKNOWN").toUpperCase();
    const kind = ["FAILED", "INVALIDATED"].includes(status) ? "error"
      : ["STALE", "NEEDS_REVALIDATION", "INGESTING", "PENDING_REVIEW", "CONFLICT", "PARTIAL"].includes(status) ? "warning"
        : ["SUPERSEDED", "DELETED", "SUPPORTING_ONLY"].includes(status) ? "neutral" : "";
    return `<span class="badge ${kind}">${escapeHtml(status.replaceAll("_", " "))}</span>`;
  }

  function heading(kicker, title, description, action = "") {
    return `<header class="page-heading"><div><p class="eyebrow">${escapeHtml(kicker)}</p><h1>${escapeHtml(title)}</h1><p class="lede">${escapeHtml(description)}</p></div>${action}</header>`;
  }

  function renderError(error, retry = "") {
    page.innerHTML = `${heading("LinkLoom", "This page could not load", "Your source notes and saved decisions were left as they were.")}
      <section class="error-box" role="alert"><strong>${escapeHtml(error.message || "The operation failed.")}</strong>
      ${error.code ? `<p>Code: ${escapeHtml(error.code)}</p>` : ""}${retry}</section>`;
    announce(error.message || "The operation failed.");
  }

  function updateWorkspacePicker() {
    workspacePicker.innerHTML = `<option value="">Select workspace</option>${state.workspaces.map((workspace) =>
      `<option value="${escapeHtml(workspace.workspace_id)}" ${workspace.workspace_id === state.workspace?.workspace_id ? "selected" : ""}>${escapeHtml(workspace.name)}</option>`
    ).join("")}`;
    workspacePicker.disabled = state.workspaces.length === 0;
    document.getElementById("provider-state").textContent = state.provider?.message || "Provider status unavailable";
    document.getElementById("provider-state").dataset.ready = String(Boolean(state.provider?.ready));
  }

  function setNavigation(screen) {
    state.screen = screen;
    document.querySelectorAll(".primary-nav [data-screen]").forEach((button) => {
      if (button.dataset.screen === screen) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
  }

  async function refreshWorkspaceState() {
    const [workspaces, current, provider] = await Promise.all([
      request("/api/workspaces"), request("/api/workspaces/current"), request("/api/status"),
    ]);
    state.workspaces = workspaces.workspaces || [];
    state.workspace = current.workspace;
    state.provider = provider;
    updateWorkspacePicker();
    const review = await request("/api/review/candidates").catch(() => ({ candidates: [] }));
    document.getElementById("review-count").textContent = review.candidates?.length ? String(review.candidates.length) : "";
  }

  async function showNoWorkspace() {
    page.innerHTML = `${heading("Start here", "Create a workspace", "Give this project a name. Your imported source artifacts and decision history stay scoped to this workspace.")}
      <form id="workspace-create" class="panel form-stack" aria-label="Create workspace">
        <div class="field"><label for="workspace-name">Workspace name</label><input id="workspace-name" name="name" maxlength="120" required placeholder="Product launch"></div>
        <button class="button" type="submit">Create workspace</button>
      </form>`;
  }

  async function loadScreen(screen = state.screen) {
    state.agentViewToken += 1;
    setNavigation(screen);
    state.sourceId = null;
    state.candidateId = null;
    state.decisionId = null;
    if (!state.workspace) { await showNoWorkspace(); return; }
    page.innerHTML = `<section class="state-card"><p class="loading">Loading ${escapeHtml(screen)}…</p></section>`;
    try {
      if (screen === "sources") await loadSources();
      else if (screen === "review") await loadReview();
      else if (screen === "decisions") await loadDecisions();
      else await showAsk();
    } catch (error) {
      renderError(error, `<p><button class="button quiet" data-action="reload">Try again</button></p>`);
    }
  }

  async function loadSources() {
    const { sources } = await request("/api/sources");
    const list = sources.length ? sources.map((source) => `
      <article class="card" data-source-card="${escapeHtml(source.source_id)}">
        <div class="card-head"><div><h3>${escapeHtml(source.name)}</h3><p class="status-line">Version captured ${escapeHtml(source.updated_at)}</p></div>${badge(source.ingestion_status)}</div>
        <div class="metadata"><span>Candidates <strong>${source.candidate_count}</strong></span><span>Review required <strong>${source.review_required_count}</strong></span><span>${source.segment_summary.total} segments · ${source.segment_summary.processed} processed · ${source.segment_summary.failed} extraction failed</span><span>Version <strong>${escapeHtml(source.source_version.slice(0, 18))}…</strong></span></div>
        ${source.error ? `<div class="error-box"><strong>${escapeHtml(source.error)}</strong></div>` : ""}
        <div class="actions"><button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(source.source_id)}">Inspect source</button>${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">Retry failed segment</button>` : ""}</div>
      </article>`).join("") : `<section class="empty"><h2>No sources yet</h2><p>Add a timestamped meeting or decision artifact below. Source text is kept as an immutable version; this does not edit files in your workspace.</p></section>`;
    page.innerHTML = `${heading("Sources", "Bring the project record together", "Import timestamped meeting notes. LinkLoom keeps exact source versions and shows when semantic extraction needs review.")}
      <div class="split"><section><div class="section-title"><h2>Workspace sources</h2><span class="muted">${sources.length} active</span></div>${list}</section>
      <section class="panel"><h2>Add a source</h2><p class="hint">Use one message per line: <code>[2026-10-08T10:00:00Z] Speaker: what was said</code>. LinkLoom will show INGESTING, then the candidate and review counts.</p>
        ${state.provider?.ready ? `<p class="hint">${escapeHtml(state.provider.message)} Source text is sent to this configured provider when you submit.</p>` : `<div class="error-box"><strong>Semantic provider is not enabled.</strong><p>${escapeHtml(state.provider?.message || "Configure a provider before importing.")}</p></div>`}
        <form id="source-create" class="form-stack"><div class="field"><label for="source-file">Choose a Markdown or text artifact</label><input id="source-file" type="file" accept=".md,.txt,text/markdown,text/plain"><span class="hint">Files are read in this browser and submitted to the local LinkLoom server.</span></div>
        <div class="field"><label for="source-name">Artifact name</label><input id="source-name" name="name" maxlength="180" required placeholder="meeting-2026-10-08.md"></div>
        <div class="field"><label for="source-content">Timestamped source text</label><textarea class="source-input" id="source-content" name="content" required></textarea></div>
        <button class="button" type="submit">Import and extract</button></form></section></div>`;
  }

  async function inspectSource(sourceId) {
    const { source } = await request(`/api/sources/${encodeURIComponent(sourceId)}`);
    state.sourceId = sourceId;
    const evidence = source.evidence.length ? source.evidence.map((item) => `<div class="card"><div class="metadata"><span>Lines ${item.line_start}–${item.line_end}</span><span>${badge("VERIFIED")}</span></div><blockquote class="quote">${escapeHtml(item.quote)}</blockquote></div>`).join("") : `<p class="empty">No timestamped evidence spans were parsed.</p>`;
    page.innerHTML = `${heading("Sources", source.name, `Current version ${source.source_version}. Source changes create a new version; prior evidence remains available.`)}
      <div class="detail-layout"><section class="panel"><div class="card-head"><h2>Ingestion</h2>${badge(source.ingestion_status)}</div><p>${source.candidate_count} candidates · ${source.review_required_count} need review · ${source.segment_summary.total} segments · ${source.segment_summary.processed} processed · ${source.segment_summary.failed} extraction failed</p>${source.error ? `<div class="error-box" role="alert">${escapeHtml(source.error)}</div>` : ""}${source.segments.map((segment) => `<p class="metadata">Segment ${segment.ordinal + 1}: ${escapeHtml(segment.state)} · ${segment.attempt_count} attempt(s)</p>`).join("")}${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">Retry failed segment</button>` : ""}
        <h3>Exact evidence spans</h3>${evidence}<details><summary>Source version details</summary><p class="metadata">Version: ${escapeHtml(source.source_version)} · Content SHA-256: ${escapeHtml(source.content_sha256)}</p></details></section>
      <section class="panel"><h2>Update source</h2><p class="hint">Submitting changed text creates a new immutable source version and runs reconciliation.</p>
        <form id="source-update" class="form-stack" data-id="${escapeHtml(sourceId)}"><div class="field"><label for="update-name">Artifact name</label><input id="update-name" name="name" value="${escapeHtml(source.name)}" required></div>
        <div class="field"><label for="update-content">Timestamped source text</label><textarea class="source-input" id="update-content" name="content" required>${escapeHtml(source.content)}</textarea></div>
        <button class="button" type="submit">Save new source version</button><button class="button danger" type="button" data-action="delete-source" data-id="${escapeHtml(sourceId)}">Remove from workspace</button></form></section></div>
      <p><button class="button quiet" data-action="back-sources">Back to sources</button></p>`;
    if (source.ingestion_status === "INGESTING") pollSource(sourceId, true);
  }

  async function pollSource(sourceId, refreshScreen = false) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const { source } = await request(`/api/sources/${encodeURIComponent(sourceId)}`);
      if (source.ingestion_status !== "INGESTING") {
        announce(`Source ingestion ${source.ingestion_status.toLowerCase()}. ${source.review_required_count} candidates need review.`);
        await refreshWorkspaceState();
        if (refreshScreen && state.screen === "sources" && state.sourceId === sourceId) await inspectSource(sourceId);
        else if (state.screen === "sources") await loadSources();
        return;
      }
    }
  }

  async function loadReview() {
    const [{ candidates, supporting_proposals: proposals = [] }, attention] = await Promise.all([
      request("/api/review/candidates"), request("/api/review/attention"),
    ]);
    document.getElementById("review-count").textContent = candidates.length ? String(candidates.length) : "";
    const items = candidates.length ? candidates.map((candidate) => `
      <article class="card"><div class="card-head"><div><p class="eyebrow">${escapeHtml(candidate.claim_type)}</p><h3>${escapeHtml(candidate.proposed_value || "Needs review")}</h3></div>${badge(candidate.status)}</div>
        <div class="metadata"><span>Subject <strong>${escapeHtml(candidate.subject || "Needs review")}</strong></span><span>Relation <strong>${escapeHtml(candidate.relation || "Needs review")}</strong></span><span>Source <strong>${escapeHtml(candidate.source_name)}</strong></span><span>Effective <strong>${escapeHtml(candidate.valid_from || "Not resolved")}</strong></span></div>
        <p class="hint">${escapeHtml(candidate.review_reasons.join(" · ") || "Human review required")}</p><button class="button quiet" data-action="inspect-candidate" data-id="${escapeHtml(candidate.candidate_id)}">Review candidate</button></article>`).join("") : `<section class="empty"><h2>No candidates need review</h2><p>New source-grounded decisions will appear here when the materialization policy requires human authorization.</p></section>`;
    const proposalItems = proposals.length ? proposals.map((candidate) => `
      <article class="card"><div class="card-head"><div><p class="eyebrow">PROPOSAL</p><h3>${escapeHtml(candidate.subject || candidate.proposed_value || "Unresolved proposal")}</h3></div>${badge("SUPPORTING_ONLY")}</div>
        <p class="decision-value">${escapeHtml(candidate.proposed_value || "Value not resolved")}</p><p class="metadata">${escapeHtml(candidate.source_name)} · lines ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="hint">Supporting evidence only. This proposal is not approved and is not part of Decision Memory.</p></article>`).join("") : `<p class="muted">No proposals are currently held as supporting evidence.</p>`;
    const attentionCards = attention.decisions.length ? attention.decisions.map((decision) => `<article class="card"><div class="card-head"><div><h3>${escapeHtml(decision.subject)} · ${escapeHtml(decision.relation)}</h3><p>${escapeHtml(decision.value)}</p></div>${badge(decision.memory_state)}</div><p class="hint">Source evidence changed or is unavailable. Revalidate this decision against a current source before relying on it.</p><button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(decision.decision_id)}">Inspect decision</button></article>`).join("") : `<p class="muted">No Decision Memory records currently need revalidation.</p>`;
    page.innerHTML = `${heading("Review", "Decide what becomes authoritative", "Inspect the exact source passage, resolve only fields you can support, then approve or reject through the recorded human review lifecycle.")}
      <div class="section-title"><h2>Pending candidates</h2><span class="muted">${candidates.length}</span></div>${items}
      <div class="section-title"><h2>Supporting proposals</h2><span class="muted">${proposals.length} · not authoritative</span></div>${proposalItems}
      <div class="section-title"><h2>Decision records needing attention</h2></div>${attentionCards}`;
  }

  async function inspectCandidate(candidateId) {
    const { candidate } = await request(`/api/review/candidates/${encodeURIComponent(candidateId)}`);
    state.candidateId = candidateId;
    const unresolved = candidate.current_unresolved_fields;
    const resolutionFields = unresolved.length ? `<section class="panel"><h3>Resolve fields from the evidence</h3><p class="hint">Leave uncertain fields empty. Effective time never defaults to today.</p>
      ${unresolved.includes("subject") ? `<div class="field"><label for="review-subject">Subject</label><input id="review-subject" name="subject" value="${escapeHtml(candidate.subject || "")}" required></div>` : ""}
      ${unresolved.includes("relation") ? `<p class="hint">Extracted relation phrase: ${escapeHtml(candidate.relation_phrase || "Not present")}</p><div class="field"><label for="review-relation">Canonical relation</label><input id="review-relation" name="relation" required></div>` : ""}
      ${unresolved.includes("valid_from") ? `<div class="field"><label for="review-valid-from">Effective date or timestamp</label><input id="review-valid-from" name="valid_from" placeholder="YYYY-MM-DD or ISO 8601 with timezone" required></div>` : ""}</section>` : `<p class="hint">Subject, relation, and effective time are resolved from the source.</p>`;
    page.innerHTML = `${heading("Review candidate", candidate.proposed_value || "Needs review", `${candidate.claim_type} · ${candidate.status}`)}
      <div class="detail-layout"><section class="panel"><div class="metadata"><span>Subject <strong>${escapeHtml(candidate.subject || "Needs review")}</strong></span><span>Relation <strong>${escapeHtml(candidate.relation || "Needs review")}</strong></span><span>Valid from <strong>${escapeHtml(candidate.valid_from || "Not resolved")}</strong></span></div>
        <p class="section-title"><strong>Review reasons</strong></p><p>${escapeHtml(candidate.review_reasons.join(" · ") || "Human authorization required")}</p>
        <h3>Exact supporting evidence</h3><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · lines ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <details><summary>Source version and provenance</summary><p>Version: ${escapeHtml(candidate.evidence.source_version)}</p><p>Evidence ref: ${escapeHtml(candidate.evidence.evidence_ref)}</p></details></section>
      <form id="candidate-review" class="panel form-stack" data-id="${escapeHtml(candidateId)}">${resolutionFields}
        <div class="field"><label for="reviewer-id">Reviewer</label><input id="reviewer-id" name="reviewer_id" required maxlength="120" placeholder="Your name"></div>
        <div class="field"><label for="review-reason">Review reason</label><textarea id="review-reason" name="reason" required maxlength="1000" placeholder="Why is this evidence sufficient?"></textarea></div>
        <div class="actions"><button class="button" type="submit" data-review-action="approve">Approve and materialize</button><button class="button danger" type="submit" data-review-action="reject" formnovalidate>Reject</button></div></form></div>
      <p><button class="button quiet" data-action="back-review">Back to Review</button></p>`;
  }

  async function loadDecisions() {
    const { decisions } = await request("/api/decisions");
    const cards = decisions.length ? decisions.map((decision) => `
      <article class="card"><div class="card-head"><div><p class="eyebrow">${escapeHtml(decision.relation)}</p><h3>${escapeHtml(decision.subject)}</h3></div>${badge(decision.memory_state)}</div>
        <p class="decision-value">${escapeHtml(decision.value)}</p><div class="metadata"><span>Valid from <strong>${escapeHtml(decision.valid_from)}</strong></span><span>Source <strong>${escapeHtml(decision.evidence[0]?.source_name || "Unavailable")}</strong></span></div>
        <button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(decision.decision_id)}">View history and evidence</button></article>`).join("") : `<section class="empty"><h2>No decisions in this workspace</h2><p>Import a source, review a candidate, and approve it before it appears here.</p></section>`;
    page.innerHTML = `${heading("Decision Memory", "What the team has decided", "Current records retain their effective dates, source evidence, predecessor links, and revalidation state.")}
      <div class="section-title"><h2>Current records</h2><span class="muted">${decisions.length}</span></div><div class="grid">${cards}</div>`;
  }

  async function inspectDecision(decisionId, asOf = "") {
    const viewToken = ++state.agentViewToken;
    const detail = await request(`/api/decisions/${encodeURIComponent(decisionId)}`);
    const decision = detail.decision;
    const { recovery } = await send("POST", "/api/decisions/recover", {
      subject: decision.subject, relation: decision.relation, as_of: asOf || null,
    });
    if (viewToken !== state.agentViewToken) return;
    state.decisionId = decisionId;
    const evidenceRows = (evidence) => (evidence || []).map((item) => `<div class="metadata">
      Source: ${escapeHtml(item.source_name || "Unknown")} · lines ${escapeHtml(item.line_start || "?")}–${escapeHtml(item.line_end || "?")}
      · Version ${escapeHtml(item.source_version || "Unavailable")}
      · Evidence ${escapeHtml(item.evidence_ref || "Unavailable")}</div>
      <blockquote class="quote">${escapeHtml(item.quote || "Verified quote unavailable")}</blockquote>
      ${item.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(item.source_id)}">Inspect source</button>` : ""}`).join("");
    const timeline = recovery.history.map((item) => `<article class="timeline-item"><div class="card-head"><strong>${escapeHtml(item.value)}</strong>${badge(item.status)}${badge(item.memory_state)}</div>
      <p class="metadata">Decision ${escapeHtml(item.decision_id)} · ${escapeHtml(item.valid_from)}${item.valid_to ? ` → ${escapeHtml(item.valid_to)}` : " → open-ended"}</p>
      <p class="metadata">Review receipt: ${escapeHtml(item.authorization?.receipt_id || "Unavailable")}
      · Authorized ${escapeHtml(item.authorization?.authorized_at || "Unknown")}</p>${evidenceRows(item.evidence)}</article>`).join("");
    const transitions = recovery.transitions.map((item) => `<p>${escapeHtml(item.from_value)} → ${escapeHtml(item.to_value)} on ${escapeHtml(item.effective_at)}.
      Replacement reason: ${item.reason_status === "NOT_SOURCE_VERIFIED" ? "Cannot confirm from verified sources" : escapeHtml(item.reason_status)}.</p>`).join("");
    const proposals = recovery.proposals.map((item) => `<article class="timeline-item"><strong>${escapeHtml(item.value)}</strong> ${badge(item.status)}
      <p class="hint">Proposal only; no formal review receipt.</p>${evidenceRows([item.evidence])}</article>`).join("");
    page.innerHTML = `${heading("Decision Memory", `${decision.subject} · ${decision.relation}`, "Reviewed decision timeline and source evidence")}
      <section class="panel"><form id="timeline-as-of" class="form-stack" data-id="${escapeHtml(decisionId)}"><div class="field"><label for="timeline-date">Decision valid on</label>
        <input type="date" id="timeline-date" name="as_of" value="${escapeHtml(asOf)}"></div><button class="button" type="submit">Show reviewed state</button></form>
        <p class="hint">Historical validity uses currently recorded review decisions; this is not a snapshot of what was known at that time.</p>
        <p>Selected date: ${escapeHtml(recovery.as_of.slice(0, 10))} · ${escapeHtml(recovery.status)}</p>
        <h2>${recovery.effective ? escapeHtml(recovery.effective.value) : "No verified effective decision for this date"}</h2></section>
      <section class="panel"><h2>Decision timeline</h2><div class="timeline">${timeline || "No reviewed decision was effective by this date."}</div>
        <h3>Formal replacements</h3>${transitions || "No verified replacement by this date."}
        <h3>Unapproved proposals</h3>${proposals || "No unapproved proposal recorded by this date."}</section>
      <section class="panel"><h2>Agent investigation</h2><p class="hint">This read-only investigation cannot approve or change the reviewed timeline above.</p>
        <form id="timeline-agent" class="form-stack" data-subject="${escapeHtml(decision.subject)}"
          data-relation="${escapeHtml(decision.relation)}" data-as-of="${escapeHtml(recovery.as_of)}">
          <div class="field"><label for="timeline-agent-query">Question</label>
            <textarea id="timeline-agent-query" name="query" required maxlength="4000">What was the reviewed ${escapeHtml(decision.relation)} for ${escapeHtml(decision.subject)} on this date, and which sources support it?</textarea></div>
          <button class="button" type="submit">Investigate with Agent</button></form>
        <div id="timeline-agent-result" aria-live="polite"></div></section>
      <p><button class="button quiet" data-action="back-decisions">Back to Decision Memory</button></p>`;
  }

  function renderTemporalAgent(snapshot, viewToken) {
    if (viewToken !== state.agentViewToken) return;
    const target = document.getElementById("timeline-agent-result");
    if (!target) return;
    if (snapshot.kind === "running") {
      target.innerHTML = `<p class="loading">Investigating reviewed records and source evidence…</p>`;
      return;
    }
    const sources = (snapshot.evidence || []).map((item) => `<div class="metadata">${escapeHtml(item.label)}</div>
      <blockquote class="quote">${escapeHtml(item.source?.quote || "Verified excerpt unavailable")}</blockquote>`).join("");
    if (snapshot.kind === "error") {
      target.innerHTML = `<div class="error-box" role="alert"><p>${escapeHtml(snapshot.error?.message || "The investigation stopped before final verification.")}</p>
        <p>Final Reviewer did not accept a conclusion. ${escapeHtml(snapshot.error?.code || "RUNTIME_ERROR")}</p></div>
        ${sources ? `<h3>Sources read before interruption (not a verified final answer)</h3>${sources}` : ""}`;
      return;
    }
    if (snapshot.kind === "success" || snapshot.kind === "insufficient") {
      target.innerHTML = `<p><strong>${escapeHtml(snapshot.kind === "success" ? "Verified Agent conclusion" : "Evidence is insufficient")}</strong></p>
        <p>${escapeHtml(snapshot.decision?.value || snapshot.uncertainty?.statement || "No supported final fact could be confirmed.")}</p>
        ${sources ? `<h3>Sources</h3>${sources}` : ""}`;
      return;
    }
    target.innerHTML = `<div class="error-box" role="alert">The Agent returned an unsupported run state.</div>`;
  }

  async function pollTemporalAgent(runId, viewToken) {
    if (viewToken !== state.agentViewToken) return;
    try {
      const response = await fetch(`/api/agent/decisions/runs/${encodeURIComponent(runId)}`);
      const snapshot = await response.json();
      if (viewToken !== state.agentViewToken) return;
      if (!response.ok && snapshot.kind !== "error") throw new Error("Agent run status is unavailable.");
      renderTemporalAgent(snapshot, viewToken);
      if (snapshot.kind === "running") {
        window.setTimeout(() => pollTemporalAgent(runId, viewToken), 800);
      }
    } catch {
      if (viewToken === state.agentViewToken) {
        const target = document.getElementById("timeline-agent-result");
        if (target) target.innerHTML = `<div class="error-box" role="alert">Agent run status is unavailable. The reviewed timeline above is unchanged.</div>`;
      }
    }
  }

  async function showAsk() {
    page.innerHTML = `${heading("Ask", "Recover a decision with its evidence", "Search approved Team Decision Memory. LinkLoom shows the effective date, prior value, and source passage when those records exist.")}
      <section class="panel"><form id="ask-form" class="form-stack"><div class="field"><label for="ask-query">Question</label><textarea id="ask-query" name="query" required placeholder="What supplier are we currently using?"></textarea></div>
      <div class="field"><label for="ask-date">Optional: decision valid on this date</label><input type="date" id="ask-date" name="as_of"><span class="hint">Leave blank for the current decision.</span></div><button class="button" type="submit">Search Decision Memory</button></form></section><div id="ask-result"></div>`;
  }

  function renderAskResult(result) {
    const target = document.getElementById("ask-result");
    if (result.status !== "FOUND" || !result.answer) {
      target.innerHTML = `<section class="empty"><h2>No matching decision found</h2><p>${escapeHtml(result.message || "Import and approve a source-grounded decision before asking about it.")}</p></section>`;
      return;
    }
    const answer = result.answer;
    target.innerHTML = `<section class="panel"><p class="eyebrow">${result.as_of ? `Historical decision · ${escapeHtml(result.as_of.slice(0, 10))}` : "Current decision"}</p><h2>${escapeHtml(answer.value)}</h2>
      <p>${escapeHtml(answer.subject)} · ${escapeHtml(answer.relation)}</p><p class="metadata">Effective ${escapeHtml(answer.valid_from)}${answer.valid_to ? ` through ${escapeHtml(answer.valid_to)}` : ""} ${badge(answer.memory_state)}</p>
      ${answer.replaced ? `<p><strong>Replaced:</strong> ${escapeHtml(answer.replaced.value)}</p>` : ""}
      ${(answer.evidence || []).map((item) => `<h3>Evidence · ${escapeHtml(item.source_name)}</h3><p class="metadata">${item.line_start ? `Lines ${item.line_start}–${item.line_end}` : "Source location unavailable"}</p><blockquote class="quote">${escapeHtml(item.quote || "Verified quote unavailable")}</blockquote>`).join("")}
      <button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(answer.decision_id)}">View decision history</button></section>`;
  }

  async function createWorkspace(name) {
    await send("POST", "/api/workspaces", { name });
    await refreshWorkspaceState();
    await loadScreen("sources");
  }

  document.querySelectorAll(".primary-nav [data-screen]").forEach((button) => {
    button.addEventListener("click", () => loadScreen(button.dataset.screen));
  });

  workspacePicker.addEventListener("change", async () => {
    if (!workspacePicker.value) return;
    try {
      const result = await send("POST", "/api/workspaces/current", { workspace_id: workspacePicker.value });
      state.workspace = result.workspace;
      await refreshWorkspaceState();
      await loadScreen("sources");
    } catch (error) { renderError(error); }
  });

  document.getElementById("new-workspace").addEventListener("click", () => {
    page.innerHTML = `${heading("Workspace", "Create a workspace", "Each workspace has its own source artifacts, review queue, and temporal decisions.")}
      <form id="workspace-create" class="panel form-stack"><div class="field"><label for="workspace-name">Workspace name</label><input id="workspace-name" name="name" required maxlength="120"></div><button class="button" type="submit">Create workspace</button><button class="button quiet" type="button" data-action="back-sources">Cancel</button></form>`;
  });

  page.addEventListener("change", async (event) => {
    if (event.target?.id !== "source-file") return;
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 256 * 1024) {
      announce("Source files must be 256 KB or smaller.");
      event.target.value = "";
      return;
    }
    try {
      document.getElementById("source-name").value = file.name;
      document.getElementById("source-content").value = await file.text();
      announce(`Loaded ${file.name} locally. Review the text before importing.`);
    } catch {
      announce("LinkLoom could not read this file. Paste the timestamped text instead.");
    }
  });

  page.addEventListener("submit", async (event) => {
    const form = event.target;
    event.preventDefault();
    try {
      if (form.id === "workspace-create") {
        await createWorkspace(new FormData(form).get("name"));
      } else if (form.id === "source-create") {
        const result = await send("POST", "/api/sources/import", Object.fromEntries(new FormData(form)));
        state.sourceId = result.source.source_id;
        announce("Source ingestion started.");
        await inspectSource(result.source.source_id);
        pollSource(result.source.source_id, true);
      } else if (form.id === "source-update") {
        const result = await send("PUT", `/api/sources/${encodeURIComponent(form.dataset.id)}`, Object.fromEntries(new FormData(form)));
        announce(result.changed ? "A new source version is ingesting." : "No source text changes. Existing decision evidence remains unchanged.");
        await inspectSource(result.source.source_id);
        if (result.changed) pollSource(result.source.source_id, true);
      } else if (form.id === "candidate-review") {
        const data = Object.fromEntries(new FormData(form));
        const action = event.submitter?.dataset.reviewAction || "approve";
        if (action === "reject") {
          for (const id of ["reviewer-id", "review-reason"]) {
            const field = document.getElementById(id);
            if (!field.checkValidity()) { field.reportValidity(); return; }
          }
        }
        const result = await send("POST", `/api/review/candidates/${encodeURIComponent(form.dataset.id)}/${action}`, data);
        announce(action === "approve" ? "Candidate approved and materialized." : "Candidate rejected.");
        await refreshWorkspaceState();
        await loadReview();
        if (action === "approve" && result.decision_id) await inspectDecision(result.decision_id);
      } else if (form.id === "ask-form") {
        const resultTarget = document.getElementById("ask-result");
        resultTarget.innerHTML = `<section class="state-card"><p class="loading">Searching approved decisions…</p></section>`;
        renderAskResult((await send("POST", "/api/ask", Object.fromEntries(new FormData(form)))).answer);
      } else if (form.id === "timeline-as-of") {
        await inspectDecision(form.dataset.id, String(new FormData(form).get("as_of") || ""));
      } else if (form.id === "timeline-agent") {
        const viewToken = state.agentViewToken;
        const target = document.getElementById("timeline-agent-result");
        target.innerHTML = `<p class="loading">Starting read-only investigation…</p>`;
        try {
          const started = await send("POST", "/api/agent/decisions/ask", {
            query: String(new FormData(form).get("query") || ""),
            workspace_id: state.workspace.workspace_id,
            subject: form.dataset.subject, relation: form.dataset.relation,
            as_of: form.dataset.asOf,
          });
          if (viewToken === state.agentViewToken) pollTemporalAgent(started.run.id, viewToken);
        } catch (error) {
          if (viewToken === state.agentViewToken) {
            target.innerHTML = `<div class="error-box" role="alert">${escapeHtml(error.message || "The Agent could not start.")}</div>`;
          }
        }
      }
    } catch (error) { renderError(error, `<p><button class="button quiet" data-action="reload">Refresh</button></p>`); }
  });

  page.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const { action, id } = button.dataset;
    try {
      if (action === "inspect-source") await inspectSource(id);
      else if (action === "inspect-candidate") await inspectCandidate(id);
      else if (action === "inspect-decision") await inspectDecision(id);
      else if (action === "back-sources") await loadScreen("sources");
      else if (action === "back-review") await loadScreen("review");
      else if (action === "back-decisions") await loadScreen("decisions");
      else if (action === "reload") await loadScreen();
      else if (action === "retry-source") {
        await send("POST", `/api/sources/${encodeURIComponent(id)}/retry`, {});
        announce("Failed segment recovery started.");
        await loadSources();
        pollSource(id);
      } else if (action === "delete-source") {
        if (!window.confirm("Remove this source from the workspace? Its captured versions remain available for provenance.")) return;
        await request(`/api/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
        announce("Source removed; affected decisions were reconciled.");
        await loadSources();
      }
    } catch (error) { renderError(error); }
  });

  async function bootstrap() {
    try {
      await refreshWorkspaceState();
      if (state.workspace) await loadScreen("sources");
      else await showNoWorkspace();
    } catch (error) { renderError(error); }
  }
  bootstrap();
})();
