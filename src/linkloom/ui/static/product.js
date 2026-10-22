(() => {
  const state = {
    screen: "sources",
    workspaces: [],
    workspace: null,
    provider: null,
    sourceId: null,
    candidateId: null,
    revalidation: null,
    decisionId: null,
    agentViewToken: 0,
    askScopes: [],
    askScope: null,
    askCandidates: [],
    askClarification: null,
    askLanguage: "auto",
    askResult: null,
    askAgent: null,
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
    const viewToken = ++state.agentViewToken;
    setNavigation(screen);
    state.sourceId = null;
    state.candidateId = null;
    state.revalidation = null;
    state.decisionId = null;
    if (!state.workspace) { await showNoWorkspace(); return; }
    page.innerHTML = `<section class="state-card"><p class="loading">Loading ${escapeHtml(screen)}…</p></section>`;
    try {
      if (screen === "sources") await loadSources();
      else if (screen === "review") await loadReview();
      else if (screen === "decisions") await loadDecisions();
      else await showAsk();
    } catch (error) {
      if (viewToken === state.agentViewToken) renderError(error, `<p><button class="button quiet" data-action="reload">Try again</button></p>`);
    }
  }

  async function loadSources() {
    const { sources } = await request("/api/sources");
    const providerReady = Boolean(state.provider?.ready);
    const list = sources.length ? sources.map((source) => `
      <article class="card" data-source-card="${escapeHtml(source.source_id)}">
        <div class="card-head"><div><h3>${escapeHtml(source.name)}</h3><p class="status-line">Version captured ${escapeHtml(source.updated_at)}</p></div>${badge(source.ingestion_status)}</div>
        <div class="metadata"><span>Candidates <strong>${source.candidate_count}</strong></span><span>Review required <strong>${source.review_required_count}</strong></span><span>${source.segment_summary.total} segments · ${source.segment_summary.processed} processed · ${source.segment_summary.failed} extraction failed</span><span>Version <strong>${escapeHtml(source.source_version.slice(0, 18))}…</strong></span></div>
        ${source.error ? `<div class="error-box"><strong>${escapeHtml(source.error)}</strong></div>` : ""}
        <div class="actions"><button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(source.source_id)}">Inspect source</button>${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">Retry failed segment</button>` : ""}</div>
      </article>`).join("") : `<section class="empty"><h2>No sources yet</h2><p>Add a timestamped meeting or decision artifact below. Source text is kept as an immutable version; this does not edit files in your workspace.</p></section>`;
    page.innerHTML = `${heading("Sources", "Bring the project record together", "Import timestamped meeting notes. LinkLoom keeps exact source versions and shows when semantic extraction needs review.")}
      <div class="split"><section><div class="section-title"><h2>Workspace sources</h2><span class="muted">${sources.length} active</span></div>${list}</section>
      <section class="panel"><h2>Add a source</h2><p class="hint">Use one message per line: <code>[2026-10-08T10:00:00Z] Speaker: what was said</code>. ${providerReady ? "LinkLoom will show INGESTING, then the candidate and review counts." : "The source is saved as an immutable version now. Saved sources can be investigated in Ask after configuring task stages and authorizing that question."}</p>
        ${providerReady ? `<p class="hint">${escapeHtml(state.provider.message)} Source text is sent to this configured provider when you submit.</p>` : `<div class="error-box"><strong>Source saving is available; semantic extraction is not.</strong><p>${escapeHtml(state.provider?.message || "Configure a provider before extracting source text.")} You can save the source now and extract it later after enabling a provider.</p></div>`}
        <form id="source-create" class="form-stack"><div class="field"><label for="source-file">Choose a Markdown or text artifact</label><input id="source-file" type="file" accept=".md,.txt,text/markdown,text/plain"><span class="hint">Files are read in this browser and submitted to the local LinkLoom server.</span></div>
        <div class="field"><label for="source-name">Artifact name</label><input id="source-name" name="name" maxlength="180" required placeholder="meeting-2026-10-08.md"></div>
        <div class="field"><label for="source-content">Timestamped source text</label><textarea class="source-input" id="source-content" name="content" required></textarea></div>
        <button class="button" type="submit">${providerReady ? "Import and extract" : "Save source for later extraction"}</button></form></section></div>`;
  }

  async function inspectSource(sourceId) {
    const { source } = await request(`/api/sources/${encodeURIComponent(sourceId)}`);
    const providerReady = Boolean(state.provider?.ready);
    state.sourceId = sourceId;
    const extractionUnavailable = source.error?.startsWith("PROVIDER_UNAVAILABLE:");
    const evidence = source.evidence.length ? source.evidence.map((item) => `<div class="card"><div class="metadata"><span>Lines ${item.line_start}–${item.line_end}</span><span>${badge("VERIFIED")}</span></div><blockquote class="quote">${escapeHtml(item.quote)}</blockquote></div>`).join("") : `<p class="empty">No timestamped evidence spans were parsed.</p>`;
    page.innerHTML = `${heading("Sources", source.name, `Current version ${source.source_version}. Source changes create a new version; prior evidence remains available.`)}
      <div class="detail-layout"><section class="panel"><div class="card-head"><h2>Ingestion</h2>${badge(source.ingestion_status)}</div>${extractionUnavailable ? `<p class="review-guidance"><strong>Source saved. Semantic extraction is unavailable.</strong> The exact source text remains stored locally. Enable a provider, then return to Sources and import the original artifact again for extraction.</p>` : ""}<p>${source.candidate_count} candidates · ${source.review_required_count} need review · ${source.segment_summary.total} segments · ${source.segment_summary.processed} processed · ${source.segment_summary.failed} extraction failed</p>${source.error ? `<div class="error-box" role="alert">${escapeHtml(source.error)}</div>` : ""}${source.segments.map((segment) => `<p class="metadata">Segment ${segment.ordinal + 1}: ${escapeHtml(segment.state)} · ${segment.attempt_count} attempt(s)</p>`).join("")}${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">Retry failed segment</button>` : ""}
        <h3>Exact evidence spans</h3>${evidence}<details><summary>Source version details</summary><p class="metadata">Version: ${escapeHtml(source.source_version)} · Content SHA-256: ${escapeHtml(source.content_sha256)}</p></details></section>
      <section class="panel"><h2>Update source</h2><p class="hint">Submitting changed text creates a new immutable source version and runs reconciliation.${providerReady ? " The new version will be extracted by the configured provider." : " Enable a provider before saving a new version if you want semantic extraction."}</p>
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
    const attentionCards = attention.decisions.length ? attention.decisions.map((decision) => `<article class="card"><div class="card-head"><div><h3>${escapeHtml(decision.subject)} · ${escapeHtml(decision.relation)}</h3><p>${escapeHtml(decision.value)}</p></div>${badge(decision.memory_state)}</div><p class="hint">Source evidence changed or is unavailable. Revalidate this decision against a current source before relying on it.</p><button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(decision.decision_id)}">Revalidate decision</button></article>`).join("") : `<p class="muted">No Decision Memory records currently need revalidation.</p>`;
    page.innerHTML = `${heading("Review", "Decide what becomes authoritative", "Inspect the exact source passage, resolve only fields you can support, then approve or reject through the recorded human review lifecycle.")}
      <div class="section-title"><h2>Pending candidates</h2><span class="muted">${candidates.length}</span></div>${items}
      <div class="section-title"><h2>Supporting proposals</h2><span class="muted">${proposals.length} · not authoritative</span></div>${proposalItems}
      <div class="section-title"><h2>Decision records needing attention</h2></div>${attentionCards}`;
  }

  async function inspectCandidate(candidateId, revalidation = null) {
    const viewToken = ++state.agentViewToken;
    const workspaceId = state.workspace?.workspace_id;
    const { candidate } = await request(`/api/review/candidates/${encodeURIComponent(candidateId)}`);
    if (viewToken !== state.agentViewToken || workspaceId !== state.workspace?.workspace_id) return;
    if (revalidation && candidate.status === "APPROVED") {
      await inspectRevalidation(revalidation.original.decision_id);
      return;
    }
    state.revalidation = revalidation;
    state.candidateId = candidateId;
    const unresolved = candidate.current_unresolved_fields;
    const relationUnresolved = unresolved.includes("relation");
    const relationGuidance = relationUnresolved ? `<div class="review-guidance" role="note" id="relation-guidance"><strong>Canonical relation cannot be corrected in this review form.</strong><p>Extracted relation phrase: ${escapeHtml(candidate.relation_phrase || "Not present")}. This form cannot add a catalog mapping or turn an unknown relation into a canonical one.</p><p>Open the original source to inspect it. With a provider enabled, return to Sources and import the original artifact again to request a new extraction. Re-extraction may still leave the relation unresolved.</p><p>If you cannot confirm the candidate from its evidence, leave it unresolved and use Reject. Enter your reviewer name and reason to record the rejection; it will not enter Decision Memory.</p><button class="button quiet" type="button" data-action="inspect-source" data-id="${escapeHtml(candidate.source_id)}">Open original source</button></div>` : "";
    const resolutionFields = unresolved.length ? `<section class="panel"><h3>Resolve fields from the evidence</h3><p class="hint">Only an unresolved subject and an explicit effective time can be corrected here. Leave uncertain fields empty. Effective time never defaults to today.</p>
      ${unresolved.includes("subject") ? `<div class="field"><label for="review-subject">Subject</label><input id="review-subject" name="subject" value="${escapeHtml(candidate.subject || "")}" required></div>` : ""}
      ${relationGuidance}
      ${unresolved.includes("valid_from") ? `<div class="field"><label for="review-valid-from">Effective date or timestamp</label><input id="review-valid-from" name="valid_from" placeholder="YYYY-MM-DD or ISO 8601 with timezone" required></div>` : ""}</section>` : `<p class="hint">Subject, canonical relation, and effective time are resolved from the source.</p>`;
    page.innerHTML = `${heading("Review candidate", candidate.proposed_value || "Needs review", `${candidate.claim_type} · ${candidate.status}`)}
      <div class="detail-layout"><section class="panel"><div class="metadata"><span>Subject <strong>${escapeHtml(candidate.subject || "Needs review")}</strong></span><span>Relation <strong>${escapeHtml(candidate.relation || "Needs review")}</strong></span><span>Valid from <strong>${escapeHtml(candidate.valid_from || "Not resolved")}</strong></span></div>
        <p class="section-title"><strong>Review reasons</strong></p><p>${escapeHtml(candidate.review_reasons.join(" · ") || "Human authorization required")}</p>
        <h3>Exact supporting evidence</h3><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · lines ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <details><summary>Source version and provenance</summary><p>Version: ${escapeHtml(candidate.evidence.source_version)}</p><p>Evidence ref: ${escapeHtml(candidate.evidence.evidence_ref)}</p></details></section>
      <form id="candidate-review" class="panel form-stack" data-id="${escapeHtml(candidateId)}">${revalidation ? `<div class="notice"><strong>New approval for a stale decision</strong><p>Original: ${escapeHtml(revalidation.original.value)}. This approval applies only to this new candidate and its current evidence. The original decision and authorization remain in history.</p></div>` : ""}${resolutionFields}
        <div class="field"><label for="reviewer-id">Reviewer</label><input id="reviewer-id" name="reviewer_id" required maxlength="120" placeholder="Your name"></div>
        <div class="field"><label for="review-reason">Review reason</label><textarea id="review-reason" name="reason" required maxlength="1000" placeholder="Why is this evidence sufficient?"></textarea></div>
        <div class="actions"><button class="button" type="submit" data-review-action="approve" ${relationUnresolved ? 'disabled aria-describedby="relation-guidance"' : ""}>Approve and materialize</button><button class="button danger" type="submit" data-review-action="reject" formnovalidate>Reject</button></div></form></div>
      <p>${revalidation ? `<button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(revalidation.original.decision_id)}">Keep unresolved / back to comparison</button>` : `<button class="button quiet" data-action="back-review">Back to Review</button>`}</p>`;
  }

  async function inspectRevalidation(decisionId) {
    const viewToken = ++state.agentViewToken;
    const { revalidation: view } = await request(`/api/review/revalidation/${encodeURIComponent(decisionId)}`);
    if (viewToken !== state.agentViewToken) return;
    state.revalidation = view;
    const original = view.original;
    const reasonLabels = {
      ORIGINAL_EVIDENCE_UNAVAILABLE: "The exact historical evidence cannot be verified.",
      SOURCE_MISSING: "The original source record is unavailable.",
      SOURCE_REMOVED: "The source was removed from the active workspace. Historical evidence is retained for audit only.",
      EVIDENCE_WITHDRAWN_OR_CHANGED: "The original timestamp, speaker and exact quote no longer occur uniquely in the current source. The evidence was changed, withdrawn or duplicated.",
      SOURCE_RECONCILIATION_REQUIRED: "Source reconciliation still requires review.",
      DECISION_INVALIDATED: "This decision was invalidated. It cannot be restored through stale revalidation.",
    };
    const evidence = original.evidence.map(item => `<blockquote class="quote">${escapeHtml(item.quote || "Historical quote unavailable")}</blockquote><p class="metadata">${escapeHtml(item.source_name)} · lines ${item.line_start || "?"}–${item.line_end || "?"} · Version ${escapeHtml(item.source_version || "Unavailable")}</p>`).join("");
    const sources = view.current_sources.map(source => `<article class="card"><div class="card-head"><h3>${escapeHtml(source.name)}</h3>${badge(source.active ? source.ingestion_status : "REMOVED")}</div><p class="metadata">Current version: ${escapeHtml(source.source_version)}</p><pre class="quote">${escapeHtml(source.content)}</pre></article>`).join("");
    const candidates = view.candidates.map(candidate => `<article class="card"><div class="card-head"><h3>${escapeHtml(candidate.proposed_value)}</h3>${badge(candidate.status)}</div><p class="metadata">${escapeHtml(candidate.subject)} · ${escapeHtml(candidate.relation)} · Effective ${escapeHtml(candidate.valid_from)}</p><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · lines ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>${candidate.recorded_approval ? `<details><summary>Recorded approval (read-only)</summary><pre>${escapeHtml(JSON.stringify(candidate.recorded_approval, null, 2))}</pre></details><p class="hint">Approval is already recorded. Finish materialization using only that authorization.</p>` : ""}<button class="button" data-action="${candidate.status === "APPROVED" ? "retry-revalidation" : "revalidate-candidate"}" data-id="${escapeHtml(candidate.candidate_id)}">${candidate.status === "APPROVED" ? "Finish recorded approval" : "Review this new candidate"}</button></article>`).join("");
    page.innerHTML = `${heading("Review Attention", `${original.subject} · ${original.relation}`, "Compare the original approval with current sources. A new candidate needs a separate explicit human approval.")}
      <section class="panel"><div class="card-head"><h2>Original decision: ${escapeHtml(original.value)}</h2>${badge(original.memory_state)}</div><p class="metadata">Effective ${escapeHtml(original.valid_from)} · ${escapeHtml(view.status)}</p><h3>Why this needs attention</h3>${view.reasons.map(reason => `<p>${escapeHtml(reasonLabels[reason.code] || reason.code)}</p>`).join("")}${evidence}<details><summary>Original authorization receipts (read-only)</summary><pre>${escapeHtml(JSON.stringify(view.original_receipts, null, 2))}</pre></details></section>
      <section class="panel"><h2>Current sources</h2>${sources || `<p>No current source record is available. Add a source in Sources, then return to review.</p>`}</section>
      <section class="panel"><h2>New candidates for explicit review</h2>${candidates || (view.status === "RESOLVED" ? `<p>This revalidation is complete. The new approval and original record can be inspected in history.</p>` : view.status === "READ_ONLY" ? `<p>This record is read-only and cannot be revalidated here.</p>` : `<p>No valid new candidate is available. This decision stays unresolved. Add or update a source, finish extraction, and return here.</p>`)}${view.completion ? `<p>Revalidated by a new approval on ${escapeHtml(view.completion.authorized_at)}. The original record remains unchanged.</p><button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(view.completion.decision_id)}">View approved decision and history</button>` : ""}</section>
      <div class="actions"><button class="button quiet" data-action="back-review">${view.status === "UNRESOLVED" ? "Keep unresolved" : "Back to Review"}</button><button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(decisionId)}">Refresh comparison</button></div>`;
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
    const { history } = await request(`/api/decisions/${encodeURIComponent(decisionId)}/history`);
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
    const timeline = history.filter(item => item.valid_from <= recovery.as_of).map((item) => `<article class="timeline-item"><div class="card-head"><strong>${escapeHtml(item.value)}</strong>${badge(item.status)}${badge(item.memory_state)}</div>
      <p class="metadata">Decision ${escapeHtml(item.decision_id)} · ${escapeHtml(item.valid_from)}${item.valid_to ? ` → ${escapeHtml(item.valid_to)}` : " → open-ended"}</p>
      <p class="metadata">Review receipt: ${escapeHtml(item.authorization?.receipt_id || "Unavailable")}
      · Authorized ${escapeHtml(item.authorization?.authorized_at || "Unknown")}</p>${item.revalidated_by ? `<p>Revalidated by a separate human approval on ${escapeHtml(item.revalidated_by.authorized_at)}. Receipt: ${escapeHtml(item.revalidated_by.receipt_id)}</p>` : ""}${evidenceRows(item.evidence)}</article>`).join("");
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
      <section class="panel"><h2>Ask about this decision</h2><p class="hint">Ask for reviewed state or investigate its sources from the same Ask form.</p>
        <button class="button" data-action="ask-decision" data-subject="${escapeHtml(decision.subject)}"
          data-relation="${escapeHtml(decision.relation)}" data-as-of="${escapeHtml(asOf)}">Open Ask with this scope</button></section>
      <p><button class="button quiet" data-action="back-decisions">Back to Decision Memory</button></p>`;
  }

  function askLocale(query = document.getElementById("ask-query")?.value || "") {
    if (state.askLanguage !== "auto") return state.askLanguage;
    return /[\u3400-\u9fff]/.test(query) || state.language.startsWith("zh") ? "zh" : "en";
  }

  function askCopy(en, zh, locale = askLocale()) { return locale === "zh" ? zh : en; }

  function askLabel(en, zh) {
    return `<span data-ask-en="${escapeHtml(en)}" data-ask-zh="${escapeHtml(zh)}">${escapeHtml(askCopy(en, zh))}</span>`;
  }

  function localizeAsk(query) {
    const locale = askLocale(query);
    for (const element of page.querySelectorAll?.("[data-ask-en]") || []) {
      element.textContent = locale === "zh" ? element.dataset.askZh : element.dataset.askEn;
    }
    for (const option of page.querySelectorAll?.("[data-ask-subject]") || []) {
      option.textContent = `${option.dataset.askSubject} · ${decisionCategory(option.dataset.askRelation, locale)}`;
    }
    const input = document.getElementById("ask-query");
    if (input) input.placeholder = askCopy("What supplier are we currently using?", "我们现在采用哪家供应商？", locale);
  }

  function decisionCategory(relation, locale) {
    const normalized = String(relation || "").trim().toLowerCase();
    if (["uses supplier", "uses vendor"].includes(normalized)) return askCopy("Supplier", "供应商", locale);
    if (normalized === "has budget") return askCopy("Budget", "预算", locale);
    return askCopy("Decision", "决策", locale);
  }

  function askDate(value, locale) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return escapeHtml(value);
    const readable = new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-GB", {
      timeZone: "UTC", year: "numeric", month: "long", day: "numeric",
    }).format(parsed);
    return `<time datetime="${escapeHtml(value)}" title="${escapeHtml(value)}">${escapeHtml(readable)} · UTC</time>`;
  }

  function savedTaskRun(workspaceId) {
    try { return window.sessionStorage.getItem(`linkloom:last-task:${workspaceId}`); }
    catch { return null; }
  }

  async function showAsk() {
    const viewToken = state.agentViewToken;
    const workspaceId = state.workspace.workspace_id;
    const config = await request("/api/product/ask/config");
    if (viewToken !== state.agentViewToken || config.workspace_id !== workspaceId) return;
    state.askScopes = config.scopes || [];
    state.askAgent = config.agent;
    state.askSourceContext = config.investigation_context;
    if (config.agent?.task_investigation && config.agent?.external_provider) {
      document.getElementById("provider-state").textContent = "Task Provider configured. Ask requires per-question source permission; import extraction is disabled.";
    }
    const selected = state.askScopes.findIndex((slot) => slot.subject === state.askScope?.subject && slot.relation === state.askScope?.relation);
    state.askCandidates = [];
    state.askClarification = null;
    state.askResult = null;
    page.innerHTML = `<header class="page-heading ask-heading"><div><p class="eyebrow">Ask</p>
      <h1>${askLabel("Ask the project record", "问一问项目决策")}</h1>
      <p class="lede">${askLabel("Ask naturally. Get a checked answer with its original sources.", "自然提问，先看答案，再查证据。")}</p></div>
      <div class="field ask-language"><label for="ask-language">${askLabel("Answer language", "回答语言")}</label>
      <select id="ask-language"><option value="auto" ${state.askLanguage === "auto" ? "selected" : ""}>Auto / 跟随问题</option><option value="zh" ${state.askLanguage === "zh" ? "selected" : ""}>中文</option><option value="en" ${state.askLanguage === "en" ? "selected" : ""}>English</option></select></div></header>
      <section class="panel ask-composer"><p class="metadata">${askLabel("Workspace", "项目空间")} <strong>${escapeHtml(state.workspace.name)}</strong></p>
      <form id="ask-form" class="form-stack">
        <div class="field"><label for="ask-query">${askLabel("Your question", "你的问题")}</label><textarea id="ask-query" name="query" required maxlength="4000" placeholder="${escapeHtml(askCopy("What supplier are we currently using?", "我们现在采用哪家供应商？"))}"></textarea></div>
        <div class="field"><label for="ask-domain">${askLabel("Investigation scope (optional)", "调查领域（可选）")}</label><select id="ask-domain" name="investigation_domain">
          <option value="" data-ask-en="Automatic shortcuts / ask me to clarify" data-ask-zh="自动快捷路径／需要时澄清">${askCopy("Automatic shortcuts / ask me to clarify", "自动快捷路径／需要时澄清")}</option>
          <option value="tasks" data-ask-en="Tasks across the current workspace records" data-ask-zh="当前空间记录中的任务">${askCopy("Tasks across the current workspace records", "当前空间记录中的任务")}</option>
          <option value="source_relations" data-ask-en="Project → owner → direct leader" data-ask-zh="项目 → 负责人 → 直属领导">${askCopy("Project → owner → direct leader", "项目 → 负责人 → 直属领导")}</option></select>
          <span class="hint">${askLabel("Select a domain for unfamiliar wording. Reads the current workspace sources; conclusions remain unconfirmed. Refresh this page after source changes.", "自然语言未被识别时可选定领域；只读当前空间来源，结论仍未经确认。来源变更后请刷新页面。")}</span></div>
        <div id="task-provider-status" role="status"><p>${config.agent?.task_investigation ? askLabel("Task stages configured. Relationship proposals require independent confirmation.", "任务阶段已配置；关系提案仍需独立确认。") : askLabel("Task Provider unavailable. Configure the Host before running task investigation.", "任务 Provider 不可用，请先配置 Host。")}</p>
          ${!config.agent?.task_investigation ? `<p class="hint">${escapeHtml(config.agent?.task_provider?.setup || "--allow-external-provider --allow-task-investigation --task-max-requests 16 --task-max-input-tokens 500000 --task-max-output-tokens 100000 --task-max-cost-usd 2 --task-max-seconds 120 --task-request-timeout-seconds 30")}</p>` : ""}
          ${config.agent?.task_provider?.budgets ? `<p class="metadata">${askLabel("Per-question limits", "单次调查上限")}: ${escapeHtml(config.agent.task_provider.model_id)} · ${config.agent.task_provider.budgets.max_requests} requests · ${config.agent.task_provider.budgets.max_input_tokens} input / ${config.agent.task_provider.budgets.max_output_tokens} output tokens · USD ${config.agent.task_provider.budgets.max_cost_usd} · ${config.agent.task_provider.budgets.max_seconds}s</p>` : ""}
          <details><summary>${askLabel("Sources in this authorization", "本次授权的来源清单")} (${config.investigation_context?.source_count || 0})</summary><ul>${(config.investigation_context?.sources || []).map(s => `<li>${escapeHtml(s.name)} <small>${escapeHtml(s.version)}</small></li>`).join("")}</ul><p class="hint">${askLabel("Only the current snapshot, for this question. Does not approve business relationships.", "仅本次问题、当前来源快照；不授予业务关系批准权限。")}</p></details></div>
        <div class="ask-submit"><button id="ask-submit-button" class="button" type="submit">${askLabel("Ask", "提问")}</button><span class="hint">${askLabel("Ctrl / ⌘ + Enter to ask", "Ctrl / ⌘ + Enter 提交")}</span></div>
        <details id="ask-advanced" ${selected >= 0 ? "open" : ""}><summary>${askLabel("Advanced: decision and exact date", "高级选项：决策与精确日期")}</summary>
        <div class="field"><label for="ask-slot">${askLabel("Decision scope", "指定决策（可选）")}</label><select id="ask-slot">
          <option value="" data-ask-en="Discover automatically from my question" data-ask-zh="根据问题自动查找">${askCopy("Discover automatically from my question", "根据问题自动查找")}</option>${state.askScopes.map((slot, index) => `<option value="${index}" data-ask-subject="${escapeHtml(slot.subject)}" data-ask-relation="${escapeHtml(slot.relation)}" ${index === selected ? "selected" : ""}>${escapeHtml(slot.subject)} · ${escapeHtml(decisionCategory(slot.relation, askLocale()))}</option>`).join("")}</select>
          <span class="hint">${askLabel("Only reviewed, source-verified decisions are available.", "仅包含已审核且来源可核验的决策。")}</span></div>
        <div class="field"><label for="ask-date">${askLabel("Decision valid on this date (optional)", "查询哪一天有效的决策（可选）")}</label><input type="date" id="ask-date" name="as_of" value="${escapeHtml((state.askScope?.as_of || "").slice(0, 10))}"><span class="hint">${askLabel("Blank means now. Dates start at 00:00 UTC; history uses currently recorded approvals.", "留空查询当前；日期以 UTC 零点为准，使用目前记录的审核信息。")}</span></div></details>
        ${config.agent?.available && config.agent?.external_provider ? `<label class="ask-permission"><input id="ask-external" type="checkbox">${askLabel("Allow this request to send sources to the configured external AI provider and incur bounded model costs.", "仅授权本次向已配置的外部 AI 发送来源内容，并产生有上限的模型费用。")}</label>` : `<p class="hint">${config.agent?.available ? askLabel("Offline investigation demo is available.", "可使用离线调查演示。") : askLabel("AI investigation is not configured. Reviewed record queries remain available.", "尚未配置 AI 调查；可信决策查询可直接使用。")}</p>`}
      </form>${savedTaskRun(workspaceId) ? `<button class="button quiet" type="button" data-action="reopen-task">${askLabel("Recheck last task investigation", "重新核验上次任务调查")}</button>` : ""}</section><div id="ask-result" tabindex="-1" aria-live="polite"></div>
      <details class="panel" id="feedback-panel"><summary>${askLabel("Term not understood? Propose a correction", "术语没识别对？提出纠正")}</summary>
        <p>${askLabel("Terminology applies only to this workspace after your explicit approval. It cannot approve or change business decisions.", "术语仅在本空间经人工确认后生效，不能批准或修改业务决策。")}</p>
        <form id="feedback-form" class="form-stack">
          <div class="field"><label for="feedback-term">${askLabel("New term", "新术语")}</label><input id="feedback-term" name="term" required minlength="2" maxlength="40"><span class="hint">${askLabel("One term, 2–40 characters; sentences are unsupported.", "单个术语，2–40 字符；不支持整句。")}</span></div>
          <div class="field"><label for="feedback-relation">${askLabel("Means this existing relation", "对应已有关系")}</label><select id="feedback-relation" name="relation" required>${[...new Set(state.askScopes.map(s => s.relation))].map(relation => `<option value="${escapeHtml(relation)}">${escapeHtml(decisionCategory(relation, askLocale()))} · ${escapeHtml(relation)}</option>`).join("")}</select></div>
          <div class="field"><label for="feedback-actor">${askLabel("Your reviewer ID (local human assertion)", "你的审核者标识（本地人工声明）")}</label><input id="feedback-actor" name="actor" required maxlength="128" placeholder="local-user"></div>
          <button class="button quiet" type="submit">${askLabel("Propose only — pending review", "提出候选，等待确认")}</button>
        </form><div id="feedback-status" role="status" aria-live="polite"></div><div id="feedback-entries"></div>
      </details>`;
    await loadFeedback(workspaceId, viewToken);
  }

  async function loadFeedback(workspaceId = state.workspace?.workspace_id, viewToken = state.agentViewToken) {
    try {
      const data = await request(`/api/product/feedback?workspace_id=${encodeURIComponent(workspaceId)}`);
      if (viewToken !== state.agentViewToken || workspaceId !== state.workspace?.workspace_id) return;
      const target = document.getElementById("feedback-entries");
      if (!target) return;
      const labels = {pending: "待确认 / Pending", active: "已批准 / Approved", revoked: "已撤销 / Revoked", expired: "已过期 / Expired", rejected: "已拒绝 / Rejected"};
      target.innerHTML = (data.entries || []).map(entry => `<article class="ask-evidence"><h3>${escapeHtml(entry.term)} → ${escapeHtml(entry.relation)}</h3><p>${escapeHtml(labels[entry.status] || entry.status)} · ${escapeHtml(entry.actor)}</p>${entry.status === "pending" ? `<button type="button" class="button quiet" data-action="feedback-approve" data-id="${escapeHtml(entry.id)}">${askCopy("I confirm this terminology", "我确认此术语映射")}</button> <button type="button" class="button quiet" data-action="feedback-reject" data-id="${escapeHtml(entry.id)}">${askCopy("Reject", "拒绝")}</button>` : entry.status === "active" ? `<button type="button" class="button quiet" data-action="feedback-revoke" data-id="${escapeHtml(entry.id)}">${askCopy("Revoke mapping", "撤销映射")}</button>` : ""}</article>`).join("") || `<p class="hint">${askCopy("No terminology feedback in this workspace.", "本空间暂无术语反馈。")}</p>`;
    } catch (error) {
      if (viewToken === state.agentViewToken && workspaceId === state.workspace?.workspace_id) {
        const target = document.getElementById("feedback-status");
        if (target) target.textContent = error.message;
      }
    }
  }

  async function changeFeedback(action, data) {
    const workspaceId = state.workspace?.workspace_id;
    const target = document.getElementById("feedback-status");
    if (!target) return;
    const form = document.getElementById("ask-form");
    if (form.dataset.feedbackPending === "true") return;
    const submit = document.getElementById("ask-submit-button");
    form.dataset.feedbackPending = "true"; submit.disabled = true;
    invalidateAsk(); state.askClarification = null;
    try {
      await send("POST", `/api/product/feedback/${action}`, {...data, workspace_id: workspaceId});
      if (workspaceId !== state.workspace?.workspace_id || !target.isConnected) return;
      // A changed memory version invalidates any in-flight answer from this page.
      invalidateAsk(); state.askClarification = null;
      target.textContent = askCopy("Saved. Ask the same question again to check the current state.", "已保存。可直接再次提交原问题，检查当前效果。");
      await loadFeedback();
    } catch (error) {
      if (workspaceId === state.workspace?.workspace_id && target.isConnected) target.textContent = error.message;
    } finally {
      delete form.dataset.feedbackPending; submit.disabled = false;
    }
  }

  function invalidateAsk() {
    state.agentViewToken += 1;
    state.askCandidates = [];
    state.askResult = null;
    const target = document.getElementById("ask-result");
    if (target) target.innerHTML = "";
  }

  async function askRequest(path, options = {}) {
    let response;
    try { response = await fetch(path, { ...options, headers: { "Content-Type": "application/json" } }); }
    catch { throw new Error("Ask could not reach its local service."); }
    const payload = await response.json();
    if (payload.result) return payload.result;
    const error = new Error(payload.error?.message || "Ask status is unavailable.");
    error.code = payload.error?.code;
    throw error;
  }

  function askEvidence(item, index, locale) {
    const t = (en, zh) => askCopy(en, zh, locale);
    return `<article class="ask-evidence"><h3>[${index + 1}] ${escapeHtml(item.source_name)}</h3>
      <p class="metadata">${t("Original source", "原始来源")} · ${t("Lines", "行号")} ${escapeHtml(item.line_start)}–${escapeHtml(item.line_end)}</p>
      <blockquote class="quote">${escapeHtml(item.quote)}</blockquote>
      ${item.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(item.source_id)}">${t("View source", "查看来源")}</button>` : ""}
      <details class="ask-source-details"><summary>${t("Source version and exact references", "来源版本与完整引用")}</summary>
        <dl><dt>${t("Version", "来源版本")}</dt><dd><code>${escapeHtml(item.source_version)}</code></dd>
        <dt>${t("Evidence reference", "原始证据引用")}</dt><dd><code>${escapeHtml(item.evidence_ref)}</code></dd></dl>
        ${item.citation_id ? `<p>${t("Citation", "引用编号")}: <code>${escapeHtml(item.citation_id)}</code></p>` : ""}</details></article>`;
  }

  function askTechnical(result, locale, accepted = false) {
    const t = (en, zh) => askCopy(en, zh, locale);
    const metadata = accepted ? result : {status: result.status, path: result.path, scope: result.scope,
      route: result.route, discovery: result.discovery, reviewer: result.reviewer,
      agent_run_id: result.agent_run_id, metrics: result.metrics, error: result.error,
      uncertainty: result.uncertainty};
    return `<details class="ask-technical"><summary>${t("Review records and technical details", "审核记录与技术详情")}</summary>
      ${accepted ? (result.receipts || []).map((receipt) => `<section class="ask-receipt"><h3>${t("Recorded authorization", "审核收据")}</h3><dl>
        <dt>${t("Receipt", "收据编号")}</dt><dd><code>${escapeHtml(receipt.receipt_id)}</code></dd>
        <dt>${t("Authorization", "授权类型")}</dt><dd>${escapeHtml(receipt.authorization_type)}</dd>
        <dt>${t("Authorized", "审核时间")}</dt><dd>${askDate(receipt.authorized_at, locale)}</dd></dl><pre>${escapeHtml(JSON.stringify(receipt, null, 2))}</pre></section>`).join("") : ""}
      <pre>${escapeHtml(JSON.stringify(metadata, null, 2))}</pre></details>`;
  }

  function renderAskResult(result, viewToken) {
    if (viewToken !== state.agentViewToken) return;
    const target = document.getElementById("ask-result");
    if (!target) return;
    state.askResult = result;
    const locale = askLocale(result.query);
    const t = (en, zh) => askCopy(en, zh, locale);
    const source = result.path === "SOURCE_RELATION_INVESTIGATION";
    if (result.path === "TASK_INVESTIGATION" && ["PARTIAL", "UNKNOWN"].includes(result.status) && result.reviewer?.accepted === true) {
      if (result.agent_run_id && result.workspace_id === state.workspace?.workspace_id) {
        // Keep only a pointer in this tab. Every reopen goes through current
        // source, catalog, Ledger and Reviewer checks on the server.
        try { window.sessionStorage.setItem(`linkloom:last-task:${result.workspace_id}`, result.agent_run_id); }
        catch { /* Storage can be disabled; the current result remains usable. */ }
      }
      const prep = result.preparation || {};
      const plan = (prep.planning || []).at(-1)?.plan;
      const gapMessage = gap => gap === "BOUNDED_DISCOVERY_CANNOT_ESTABLISH_MISSING_ENTITY_ABSENCE" || gap.startsWith("NO_TASK_CANDIDATES")
        ? t("Only authorized materials were checked. A missing candidate does not prove that the task does not exist.", "本次只检查已授权资料；没有召回任务候选，不能证明任务不存在。")
        : gap.startsWith("UNSATISFIED_PREMISE") ? t("A later investigation step depends on an unconfirmed task relationship.", "后续调查步骤依赖尚未确认的任务关系。")
        : gap === "DISCOVERY_COVERAGE_INCOMPLETE" ? t("Some source material could not be fully examined within this investigation.", "部分来源资料尚未在本次调查范围内完整检查。")
        : t("Some investigation steps still lack supporting evidence. See the investigation trace for details.", "部分调查步骤仍缺少支持证据，详情见调查轨迹。");
      const view = result.task_review || {};
      const label = predicate => ({SAME_TASK: t("Same task?", "是否同一任务"), REVISES_PLAN: t("Plan revision?", "是否修订计划")}[predicate] || predicate);
      const proposalState = verdict => ({
        SUPPORTED: t("Supported proposal", "支持性提案"),
        REFUTED: t("Refuted proposal", "被反驳提案"),
        CONFLICT: t("Conflicting proposal", "冲突提案"),
        UNKNOWN: t("Unknown proposal", "未知提案"),
      }[verdict] || verdict || t("Unknown proposal", "未知提案"));
      const evidence = items => (items || []).map(item => `<blockquote class="quote">${escapeHtml(item.quote || item.span?.quote || "")}</blockquote>`).join("") || `<p>${t("None provided", "未提供")}</p>`;
      const stepState = status => ({SUPPORTED: t("Supported by current verified evidence", "已有当前有效的可信证据支持"), COMPLETED: t("Read completed", "读取已完成"), REFUTED: t("Counter-evidence found", "存在否定证据"), CONFLICT: t("Conflicting evidence", "证据冲突"), UNKNOWN: t("Still unknown", "仍然未知"), BLOCKED: t("Cannot continue yet", "暂时无法继续")}[status] || status);
      const original = id => (view.observations || []).find(o => o.observation_id === id);
      target.innerHTML = `<section class="panel ask-answer ask-answer--notice"><h2>${t("Task investigation: evidence and open questions", "任务调查：已知证据与待确认问题")}</h2>
        ${["FAKE", "REPLAY"].includes(prep.execution_kind) ? `<p class="metadata">${t("Offline scripted or recorded adapters: this run does not evaluate real-model understanding.", "本次使用离线脚本或回放适配器，不能据此评估真实模型的理解能力。")}</p>` : ""}
        <p>${t("Each record below preserves what its source says. A model suggestion does not confirm task identity or make a later date effective.", "以下记录分别保留各自原文中的信息。模型建议不代表已确认同一任务，也不代表较晚日期已正式生效。")}</p>
        <h3>${t("What the sources explicitly say", "原文明确记录的内容")}</h3>
        ${(view.observations || []).map(o => {
          const source = (result.evidence || []).find(e => e.evidence_ref === o.provenance.evidence_ref);
          const unknownFields = o.unknown_fields || [];
          const validationIssues = o.validation_issues || [];
          return `<article class="ask-evidence"><h4>${escapeHtml(source?.source_name || t("Source record", "来源记录"))} · ${escapeHtml(o.description || o.raw_text)}</h4>
            <p class="metadata">${t("Source version", "来源版本")}: <code>${escapeHtml(source?.source_version || o.provenance.artifact_version_id || t("Unavailable", "不可用"))}</code>${o.event_time ? ` · ${t("Recorded at", "记录时间")}: ${askDate(o.event_time, locale)}` : ""}</p>
            <dl><dt>${t("Recorded owner", "原文负责人")}</dt><dd>${escapeHtml(o.owner || t("Unknown", "未知"))}</dd>
            <dt>${t("Recorded deadline", "原文截止日期")}</dt><dd>${escapeHtml(o.deadline_text || t("Unknown", "未知"))}</dd>
            <dt>${t("Deadline role", "日期角色")}</dt><dd>${escapeHtml(o.deadline_role || "UNKNOWN")}</dd></dl>
            ${unknownFields.length ? `<p class="hint"><strong>${t("Unknown fields", "未知字段")}</strong>: ${escapeHtml(unknownFields.join(" · "))}</p>` : ""}
            ${validationIssues.length ? `<p class="hint"><strong>${t("Validation issues", "校验问题")}</strong>: ${escapeHtml(validationIssues.join(" · "))}</p>` : ""}
            <details><summary>${t("Original message and field evidence", "展开原始消息与字段证据")}</summary><blockquote class="quote">${escapeHtml(o.provenance.quote)}</blockquote>
            ${(o.field_evidence || []).map(f => `<p>${escapeHtml(f.field)}: ${escapeHtml(f.quote)}</p>`).join("")}</details>
            ${source?.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(source.source_id)}">${t("View source", "查看来源")}</button>` : ""}</article>`;
        }).join("") || `<p>${t("No verifiable task candidate was returned. This does not establish absence.", "本次没有返回可核验的任务候选，不能据此断言任务不存在。")}</p>`}
        <h3>${t("Relationships to review", "待审查的关系提案")}</h3>
        ${(view.proposals || []).map(p => `<article class="ask-evidence"><h4>${escapeHtml(label(p.predicate))}</h4>
          <p><strong>${t("Model proposal", "模型提案")}: <span data-task-proposal-verdict="${escapeHtml(p.proposed_verdict || "UNKNOWN")}">${escapeHtml(proposalState(p.proposed_verdict))}</span> (${escapeHtml(p.proposed_verdict || "UNKNOWN")})</strong>. ${t("Independent confirmation is still required.", "仍需独立确认。")}</p>
          <p class="hint">${p.evidence_status === "EVIDENCE_VALID" ? t("References verified; semantic review remains pending.", "引用已核验，语义仍待复核。") : t("References or source coverage are incomplete. This proposal remains uncertain and cannot unlock a trusted relation.", "引用或来源覆盖尚不完整；该提案仍不确定，不能解锁可信关系。")}</p>
          ${[p.subject, p.object].map(ref => `<p>${escapeHtml(original(ref.observation_id)?.raw_text || t("See the referenced source", "请查看对应来源"))}</p>`).join("")}
          <p>${t("Model-reported reasons", "模型报告的理由")}: ${escapeHtml((p.reason_codes || []).join(" · "))}</p>
          <details open><summary>${t("Supporting evidence", "支持证据")}</summary>${evidence(p.supporting_evidence)}</details>
          <details><summary>${t("Counter-evidence or conflicts", "反对证据或冲突")}</summary>${evidence(p.counter_evidence)}</details>
          <p>${t("Missing evidence reported by the model", "模型报告的证据缺口")}: ${escapeHtml((p.missing_evidence || []).join(" · ") || t("No additional gap reported; this does not imply confirmation.", "未报告额外缺口，仍不表示关系已确认。"))}</p></article>`).join("")}
        ${(view.relations || []).map(r => `<article class="ask-evidence"><h4>${escapeHtml(label(r.assessment?.predicate || ""))} · ${escapeHtml(stepState(r.current_supported ? "SUPPORTED" : r.assessment?.verdict === "SUPPORTED" ? "BLOCKED" : r.assessment?.verdict || "UNKNOWN"))}</h4>
          <p>${t("Existing relationship record, revalidated on read", "既有关系记录，读取时重新校验")}: ${escapeHtml(r.persistent_lifecycle)} · ${escapeHtml(r.evidence_state)}</p>
          <details><summary>${t("Supporting and counter-evidence", "支持与反对证据")}</summary>${evidence(r.assessment?.supporting_evidence)}${evidence(r.assessment?.counter_evidence)}</details></article>`).join("")}
        <h3>${t("Which steps ran, and why others stopped", "哪些步骤已执行，哪些仍被阻断")}</h3>
        ${(view.steps || []).map(s => `<article><p><strong>${escapeHtml(s.question)}</strong> — ${escapeHtml(stepState(s.status))}</p>
          ${(s.blocking_reasons || []).length ? `<p>${(s.blocking_reasons || []).some(r => r.startsWith("DEPENDENCY_")) ? t("A prerequisite relationship is not yet supported. This step has not continued.", "前置关系尚未获得可信支持，因此该步骤没有继续。") : t("No current, independently verifiable relationship record satisfies this step. A model suggestion cannot replace it.", "尚无当前有效、可独立重验的关系记录满足该步骤，模型提案不能替代这一前提。")}</p>` : ""}
          ${(s.comparisons || []).filter(c => c.current_valid).map(c => `<p>${t("Historical deadline", "历史计划截止边界")}: ${escapeHtml(c.current_comparison?.old_deadline?.value)} → ${t("Revised deadline", "修订计划截止边界")}: ${escapeHtml(c.current_comparison?.new_deadline?.value)} · ${t("Calendar-day difference", "日历日差值")}: ${escapeHtml(c.current_comparison?.delta_calendar_days)}</p><p>${c.current_comparison?.plan_currentness === "NOT_ESTABLISHED" ? t("The difference is computable; which plan is currently effective is still unknown.", "可以核算两次计划边界的差值，但尚不能确认当前哪版计划正式有效。") : t("Effective-time details are preserved in the P4 read below.", "生效时间条件保留在下方 P4 回读结果中。")}</p>`).join("")}</article>`).join("")}
        <p>${t("Reads actually performed", "实际完成的读取")}: ${escapeHtml((view.executed_tools || []).map(r => ({inspect_task_context: t("Task sources", "任务原文"), query_task_relations: t("Verified relationships", "可信关系"), analyze_task_semantics: t("Relationship proposals", "关系候选分析")}[r.tool] || r.tool)).join(" · "))}</p>
        <div class="ask-unknown"><strong>${t("Unresolved evidence", "尚未解决的证据缺口")}</strong><p>${t("Task records describe source statements. Current identity, revision and effective-date claims require separately validated relationships.", "任务记录反映来源中的陈述；任务身份、计划修订及当前生效日期，需要分别通过可信关系验证。")}</p>
        ${(prep.gaps || []).filter(gap => !gap.startsWith("UNSATISFIED_PREMISE:") || (view.unresolved || []).some(s => s.step_id === gap.split(":")[1])).map(gap => `<p>${escapeHtml(gapMessage(gap))}</p>`).join("")}</div>
        ${(result.uncertainty?.model_reported_gaps || []).map(gap => `<p>${t("Model-reported evidence gap", "模型报告的证据缺口")}: ${escapeHtml(gap)}</p>`).join("")}
        <h3>${t("What you can do next", "下一步可以做什么")}</h3>
        ${(view.next_actions || []).map(a => `<p>${a.kind === "PROVIDE_TASK_SOURCE" ? t("Provide the meeting or original task assignment that is missing.", "补充缺失的会议或任务分配原文。") : a.kind === "IDENTITY_AND_REVISION_REQUIRED" ? t("Date comparison needs both task identity and a valid plan revision. Both original dates are preserved above.", "比较日期需要先确认任务身份和计划修订；上方已保留两条原始日期。") : a.predicate === "REVISES_PLAN" ? t("Provide a source explicitly stating that the later plan replaces the earlier plan.", "补充明确说明后一计划替代前一计划的来源。") : t("Expand both original messages and check whether they describe the same work. An independently verifiable identity relationship is still needed.", "展开两条原始消息，核对是否确实描述同一项工作；仍需可独立验证的任务身份关系。")}</p>`).join("")}
        <p>${t("Human confirmation is not connected: this installation has no authenticated task-relation review interface. Viewing these records does not approve them.", "人工确认尚未接通：当前系统没有具备身份与权限校验的任务关系审核接口。查看原文不会批准关系。")}</p>
        <details><summary>${t("All original citations", "全部原始引用")}</summary>${(result.evidence || []).map((item, index) => askEvidence(item, index, locale)).join("")}</details>
        <details><summary>${t("Investigation trace and review", "调查轨迹与审查结果")}</summary><pre>${escapeHtml(JSON.stringify({steps: view.steps, tools: view.executed_tools, gaps: prep.gaps, reviewer: result.reviewer}, null, 2))}</pre></details></section>`;
      return;
    }
    if (result.path === "TASK_INVESTIGATION" && result.status === "BLOCKED") {
      const unavailable = result.error?.code === "TASK_INVESTIGATION_UNAVAILABLE";
      const permission = result.error?.code === "EXTERNAL_PROVIDER_PERMISSION_REQUIRED";
      let stopMessage = t("The configuration, source evidence or investigation budget did not pass the required checks. No verified conclusion is available.", "运行配置、来源证据或调查预算尚未通过必要检查，本次没有可核验的最终结论。");
      if (unavailable) stopMessage = t("Task investigation is unavailable in the current configuration. No investigation or external model call was started.", "当前配置不支持任务调查，尚未启动调查或外部模型调用。");
      else if (permission) stopMessage = t("Check the per-request permission above, then ask again. Sources are sent only after permission is granted.", "请查看上方来源清单与预算，勾选本次授权后再次提问；授权前不会发送来源内容。");
      else if (["PROVIDER_UNCERTAIN", "PROVIDER_TIMEOUT_UNCERTAIN"].includes(result.error?.code)) stopMessage = t("The Provider outcome is uncertain. No automatic retry or verified conclusion was released.", "Provider 执行结果不确定；系统不会自动重试，本次没有发布可核验结论。");
      else if (result.error?.code === "TASK_DATE_SCOPE_UNSUPPORTED") stopMessage = t("Clear the advanced decision date and state the investigation period in your question. The original question is kept.", "请清除高级选项中的决策日期，并在原问题中说明调查时段。原问题已保留。");
      target.innerHTML = `<section class="panel ask-answer ask-answer--notice" role="status"><h2>${unavailable ? t("Task investigation unavailable", "当前无法使用任务调查") : permission ? t("This investigation needs your permission", "这次调查需要你的授权") : t("Task investigation stopped", "任务调查已停止")}</h2><p>${stopMessage}</p><details><summary>${t("Reason and review", "停止原因与审查记录")}</summary><pre>${escapeHtml(JSON.stringify({error: result.error, preparation: result.preparation, uncertainty: result.uncertainty, reviewer: result.reviewer}, null, 2))}</pre></details></section>`;
      return;
    }
    if (source && ["COMPLETED", "INSUFFICIENT"].includes(result.status) && result.reviewer?.accepted === true && result.source_investigation) {
      renderSourceInvestigation(target, result, locale); return;
    }
    const agent = result.path === "AGENT" || source;
    const hasFinal = !source && result.status === "COMPLETED" && (!agent || result.reviewer?.accepted === true);
    const technical = askTechnical(result, locale, hasFinal);
    const code = result.error?.code;
    if (result.status === "INVESTIGATING") {
      target.innerHTML = `<section class="panel ask-answer" role="status"><h2>${t("Investigating the sources", "正在调查相关来源")}</h2><p class="loading">${t("Checking reviewed records and source evidence…", "正在查看可信记录，核验来源证据…")}</p><p class="hint">${t("The answer is not verified yet.", "本次结论尚未核验，请等待调查完成。")}</p>${technical}</section>`;
      return;
    }
    if (!hasFinal) {
      const ambiguous = result.discovery?.status === "AMBIGUOUS";
      const dateNeeded = ["DATE_SCOPE_REQUIRED", "INVALID_AS_OF"].includes(code);
      const permission = code === "EXTERNAL_PROVIDER_PERMISSION_REQUIRED";
      const noMatch = result.discovery?.status === "NO_MATCH";
      const memoryConflict = (result.discovery?.memory?.conflicts || []).length > 0;
      const uncertain = result.status === "PROVIDER_UNCERTAIN";
      const unsupported = code === "UNSUPPORTED_QUERY" || code === "TASK_SCOPE_CONFLICT";
      const admissionMessages = {
        INVESTIGATION_DOMAIN_REQUIRED: [t("Choose an investigation scope", "请选择调查领域"), t("Select tasks or project relationships above, or choose a reviewed decision. Your question has been kept; no investigation has started.", "请在上方选择任务或项目关系领域，或指定可信决策。原问题已保留，尚未启动调查。")],
        INVESTIGATION_CONTEXT_CHANGED: [t("The source scope changed", "来源范围已变化"), t("Refresh this page to review the current workspace sources, then select the domain and ask again.", "请刷新页面，查看当前空间来源后重新选择领域并提问。")],
        INVESTIGATION_CAPABILITY_UNAVAILABLE: [t("This investigation capability is unavailable", "当前不支持此调查能力"), t("The selected domain has no configured planner. No alternate investigation was started.", "所选领域尚无可用规划器；未改为执行其他调查。")],
        INVESTIGATION_DOMAIN_CONFLICT: [t("Check the investigation domain", "请核对调查领域"), t("This question requests project relationships. Select that domain to continue the original question.", "该问题请求项目关系调查，请选择对应领域以继续原问题。")],
        INVESTIGATION_SCOPE_DENIED: [t("The request exceeds read-only scope", "请求超出只读调查范围"), t("Ask about the current workspace records without write, credential or permission-bypass instructions.", "请针对当前空间记录提问，不包含写入、凭证或绕过权限的指令。")],
        INVESTIGATION_QUESTION_REQUIRED: [t("Enter an investigation question", "请输入调查问题"), t("A greeting does not start an investigation. Ask a question about the selected records.", "问候不会启动调查，请对选定记录提出问题。")],
      };
      const admissionMessage = admissionMessages[code];
      const title = admissionMessage ? admissionMessage[0] : unsupported ? t("This relationship question is not supported", "目前不能可靠地拆解这个关系问题") : memoryConflict ? t("Approved terminology conflicts", "已批准术语存在冲突") : ambiguous ? t("Which decision do you mean?", "你想问的是哪项决策？")
        : dateNeeded ? t("Choose a specific date", "请选择一个具体日期")
        : permission ? t("This investigation needs your permission", "这次调查需要你的授权")
        : uncertain ? t("Investigation outcome uncertain; no verified conclusion", "本次调查执行结果不确定，没有可核验的最终结论")
        : noMatch ? t("No matching reviewed decision found", "暂未找到与问题匹配的可信决策")
        : t("No verified answer is available", "本次尚未得到可核验的答案");
      const message = admissionMessage ? admissionMessage[1] : unsupported ? t("Try: Who is the direct leader of the owner of PROJECT? Or: Who owns the project behind ALIAS and who leads that owner? Clear any advanced decision selection first.", "支持项目 → 负责人 → 直属领导，以及英文别名 → 项目 → 负责人 → 直属领导问法。请修改问题，并清除高级选项中指定的正式决策。") : memoryConflict ? t("Revoke the conflicting mapping below, or choose a reviewed decision in advanced options.", "请在下方撤销冲突映射，或在高级选项中指定可信决策。") : ambiguous ? t("Choose one decision to continue your original question.", "选择一项后继续原问题，无需重新输入。")
        : dateNeeded ? t("Select one exact date above to check which decision was valid. Your question and confirmed decision are retained.", "请在上方选择精确日期，以核实当时有效的决策。原问题和已确认的决策会保留。")
        : permission ? t("Check the per-request permission above, then ask again. Sources are sent only after permission is granted.", "请勾选上方的本次授权，再次提问后才会发送来源内容并启动调查。")
        : uncertain ? t("Saved formal decisions are unaffected. No draft is presented as an answer.", "已保存的正式决策不受本次调查影响，本次未核验的内容不会作为答案。")
        : noMatch ? t("Add a known project or decision term, or choose a reviewed decision in advanced options.", "请补充已知项目或决策名称，也可在高级选项中指定可信决策。")
        : code === "AGENT_NOT_CONFIGURED" ? t("AI investigation is not configured. Direct reviewed queries are still available.", "尚未配置 AI 调查；仍可直接查询已审核记录。")
        : t("The request did not produce an accepted answer. Details are available below.", "本次请求未产生通过核验的答案，可展开下方详情查看原因。");
      state.askCandidates = ambiguous ? result.discovery.candidates || [] : [];
      const choices = state.askCandidates.map((slot, index) => `<article class="ask-choice"><h3>${escapeHtml(slot.subject)}</h3><p>${escapeHtml(decisionCategory(slot.relation, locale))}${slot.current_value ? ` · <strong>${escapeHtml(slot.current_value)}</strong>` : ""}</p><button type="button" class="button quiet" data-action="choose-ask-scope" data-index="${index}">${t("Choose this decision", "选择这项决策")}</button></article>`).join("");
      target.innerHTML = `<section class="panel ask-answer ask-answer--notice" role="${uncertain || result.status === "FAILED" ? "alert" : "status"}"><h2>${title}</h2><p>${message}</p>${choices ? `<div class="ask-choices">${choices}</div>` : ""}${ambiguous && result.discovery.candidate_count > state.askCandidates.length ? `<p class="hint">${t("More candidates are available in advanced options. Narrow the question if needed.", "更多候选可在高级选项中查看，也可补充问题缩小范围。")}</p>` : ""}${technical}</section>`;
      if (dateNeeded) { document.getElementById("ask-advanced").open = true; document.getElementById("ask-date").focus?.(); }
      return;
    }
    const claims = result.conclusions || [];
    const effective = result.timeline?.effective;
    const historical = Boolean(document.getElementById("ask-date")?.value) || /\d{4}\s*(?:-|年)/.test(result.query || "");
    const transitions = result.timeline?.transitions || [];
    const unknown = result.uncertainty?.unknown_fields || [];
    const reasonUnknown = agent && unknown.includes("replacement_reason");
    const investigationUnknown = agent && unknown.length > 0;
    const transition = transitions[transitions.length - 1];
    const subject = result.scope?.subject || "";
    const supplier = ["uses supplier", "uses vendor"].includes(String(result.scope?.relation).toLowerCase());
    const unknownText = unknown.map(field => field === "replacement_reason" ? t("The sources do not establish the replacement reason.", "现有可信来源没有证明更换原因。") : field === "effective_decision" ? t("No effective formal decision could be verified.", "未能核实该日期有效的正式决策。") : field === "investigation_conclusion" ? t("The investigation conclusion has not been verified.", "本次调查结论尚未核验。") : t("Some requested information is not supported by evidence.", "请求中的部分信息尚无可信证据。")).join(" ");
    const title = !claims.length ? t("No verifiable formal decision for this date", "该日期没有可核实的正式决策") : reasonUnknown ? (transition ? t("Change verified; the reason remains unknown", "更换事实已核实，原因尚无可信证据") : t("The reason is not established", "原因尚无可信证据")) : escapeHtml(claims[0].value);
    const sentence = reasonUnknown && transition ? `${escapeHtml(transition.from_value)} → ${escapeHtml(transition.to_value)}. ${t("The replacement is verified; its cause is not established.", "已核实发生了更换，但不能据此推断原因。")}`
      : claims.length ? (supplier ? t(`${escapeHtml(subject)} uses ${escapeHtml(claims[0].value)} as its reviewed supplier${historical ? " at the selected date" : ""}.`, `${escapeHtml(subject)} 项目${historical ? "在所选日期采用" : "当前采用"} ${escapeHtml(claims[0].value)} 作为供应商。`) : `${escapeHtml(subject)} · ${escapeHtml(decisionCategory(result.scope?.relation, locale))}: ${escapeHtml(claims[0].value)}.`) : t("There is no unique reviewed, source-verified effective decision for the selected date.", "所选日期没有唯一、已审核且来源可核验的有效决策。");
    const evidence = (result.evidence || []).map((item, index) => askEvidence(item, index, locale));
    const timelineButton = effective ? `<button class="button quiet" data-action="ask-timeline" data-id="${escapeHtml(effective.decision_id)}" data-as-of="${escapeHtml(result.scope?.as_of || "")}">${t("View decision timeline", "查看决策时间线")}</button>` : "";
    const history = `<details class="ask-history"><summary>${t("Decision history and unapproved proposals", "相关历史与未审核提案")}</summary>${transitions.map(item => `<p>${escapeHtml(item.from_value)} → ${escapeHtml(item.to_value)} · ${askDate(item.effective_at, locale)}</p>`).join("")}${!reasonUnknown && unknownText ? `<p class="hint">${escapeHtml(unknownText)}</p>` : ""}${(result.timeline?.proposals || []).map(item => `<p class="hint">${t("Unapproved proposal, not a formal decision", "未审核提案，非正式决策")}: ${escapeHtml(item.value)}</p>`).join("")}${timelineButton}</details>`;
    target.innerHTML = `<section class="panel ask-answer ${!claims.length || investigationUnknown ? "ask-answer--notice" : ""}">
      <p class="ask-answer-status">${!claims.length ? t("No verified decision", "没有可核实的正式决策") : agent ? t("Source investigation checked", "来源调查已核验") : t("Verified formal decision", "已核实的正式决策")}</p>
      <h2 class="${claims.length && !reasonUnknown ? "ask-answer-value" : "ask-answer-headline"}">${title}</h2><p class="ask-answer-sentence">${sentence}</p>
      ${investigationUnknown || !claims.length ? `<div class="ask-unknown"><strong>${t("What remains unknown", "尚未取得证据支持的内容")}</strong><p>${escapeHtml(unknownText || result.uncertainty?.statement || "")}</p></div>` : ""}
      <div class="ask-facts"><span>${historical ? t("Effective at the selected historical date", "所选历史日期的有效决策") : t("Current reviewed record", "当前可信记录")}: ${askDate(result.scope?.as_of, locale)}</span>${claims[0]?.effective_at ? `<span>${t("Effective from", "生效日期")}: ${askDate(claims[0].effective_at, locale)}</span>` : ""}</div>
      <div class="ask-pills"><span>${agent ? t("AI investigated the sources", "AI 已调查相关来源") : t("Direct reviewed-record lookup", "直接查询可信决策记录")}</span><span>${agent ? (state.askAgent?.run_mode === "OFFLINE_FAKE_GEMINI" ? t("Offline synthetic demo", "离线合成演示") : t("Model used", "使用了模型")) : t("0 model calls", "零模型调用")}</span></div>
      ${(result.discovery?.memory?.used || []).map(item => `<p class="hint">${t("Approved terminology used to locate this decision", "使用已批准术语定位此决策")}: ${escapeHtml(item.term)} → ${escapeHtml(item.relation)}. ${t("Terminology is not business evidence.", "术语解释不构成业务证据。")}</p>`).join("")}
      ${historical ? `<p class="hint">${t("Valid time uses approvals recorded now; this does not establish what people knew or approved at that historical time.", "这是使用目前审核记录查询的有效时间，不代表当时的人已经知情或批准。")}</p>` : ""}
      ${(result.rationale || []).map(item => `<p>${escapeHtml(item.statement || item.value || item.text || "")}</p>`).join("")}
      ${evidence.length ? `<section class="ask-core-evidence"><h2>${t("Original evidence", "原始证据")}</h2>${evidence[0]}${evidence.length > 1 ? `<details class="ask-more-evidence"><summary>${t("View remaining sources", "查看其余来源")} (${evidence.length - 1})</summary>${evidence.slice(1).join("")}</details>` : ""}</section>` : ""}
      ${reasonUnknown && transition ? `<p class="hint">${t("Replacement effective", "更换生效日期")}: ${askDate(transition.effective_at, locale)}</p>` : ""}${history}${technical}</section>`;
  }

  function revealAskResult(result) {
    if (result.status !== "INVESTIGATING" && !["DATE_SCOPE_REQUIRED", "INVALID_AS_OF"].includes(result.error?.code)) {
      document.getElementById("ask-result")?.scrollIntoView?.({block: "start", behavior: "smooth"});
    }
  }

  function renderSourceInvestigation(target, result, locale) {
    const t = (en, zh) => askCopy(en, zh, locale);
    const source = result.source_investigation;
    const complete = result.status === "COMPLETED" && source.answer;
    const conflict = result.uncertainty?.status === "conflicting_evidence";
    const labels = {alias_of_project: t("Alias → project", "别名 → 项目"), owner_of_project: t("Project → owner", "项目 → 负责人"), reports_to: t("Owner → direct leader", "负责人 → 直属领导")};
    const facts = source.facts || [];
    const coreRefs = new Set([...facts.flatMap(fact => fact.evidence_refs || []), ...Object.values(source.conflicts || {}).flat()]);
    const coreEvidence = (result.evidence || []).filter(item => coreRefs.has(item.evidence_ref));
    const otherEvidence = (result.evidence || []).filter(item => !coreRefs.has(item.evidence_ref));
    const title = complete ? escapeHtml(source.answer) : conflict ? t("Conflicting sources; no unique answer", "来源存在冲突，当前无法确定唯一结果") : t("Partly verified; evidence is still missing", "已核实部分关系，后续证据不足");
    const steps = (source.plan?.subgoals || []).map(goal => {
      const predicate = goal.relation_path[0];
      const fact = facts.find(item => item.predicate === predicate);
      return `<article class="ask-evidence"><h3>${escapeHtml(labels[predicate] || predicate)}</h3>${fact ? `<p>${escapeHtml(fact.subject)} → <strong>${escapeHtml(fact.value)}</strong>${fact.valid_on ? ` · ${escapeHtml(fact.valid_on)}` : ""}</p>` : `<p>${conflict ? t("No unique result; inspect the opposing sources below.", "没有唯一结果，请查看下方互相矛盾的原始来源。") : t("No verified result for this hop.", "尚无足够证据核实这一跳。")}</p>`}</article>`;
    }).join("");
    target.innerHTML = `<section class="panel ask-answer ${complete ? "" : "ask-answer--notice"}"><p class="ask-answer-status">${facts.length ? t("Source-supported relationship", "来源支持的关系") : t("Source investigation; no relationship verified", "来源调查，尚无关系得到核实")}</p>
      <h2 class="${complete ? "ask-answer-value" : "ask-answer-headline"}">${title}</h2>
      <p>${complete ? t("The relationship is supported by verified source reads and checked by Reviewer.", "根据已读取并核验的原始来源，可以确认该关系，结果已经过 Reviewer 核验。") : conflict ? t("The known upstream facts are retained. Opposing sources prevent a unique downstream conclusion.", "已确认的上游关系予以保留；下游来源互相矛盾，不能选择唯一结论。") : t("The known upstream facts are retained. The requested final relationship remains unknown.", "已确认的上游关系予以保留；尚未找到足够证据确认最终关系。")}</p>
      <p class="hint">${t("This is source investigation, not a formally approved business decision.", "这是来源关系调查结果，不代表已经通过正式业务审批。")}</p>
      <div class="ask-pills"><span>${(source.plan?.subgoals || []).length} ${t("hop investigation", "跳调查")}</span><span>${result.metrics?.model_turns || 0} ${t("model turns", "轮模型交互")}</span></div>
      ${steps}<section class="ask-core-evidence"><h2>${t("Sources supporting the verified facts or conflict", "支持已核实关系或冲突的来源")}</h2>${coreEvidence.map((item, index) => askEvidence(item, index, locale)).join("")}</section>
      ${otherEvidence.length ? `<details><summary>${t("Other sources read; no additional relationship established", "其他已读取材料，未据此确认额外关系")} (${otherEvidence.length})</summary>${otherEvidence.map((item, index) => askEvidence(item, coreEvidence.length + index, locale)).join("")}</details>` : ""}
      <details class="ask-technical"><summary>${t("Full investigation: plan, dependencies and tool calls", "查看完整调查过程：计划、依赖与工具记录")}</summary><pre>${escapeHtml(JSON.stringify({plan: source.plan, trace: source.trace, metrics: result.metrics, reviewer: result.reviewer}, null, 2))}</pre></details></section>`;
  }

  async function pollAsk(runId, workspaceId, viewToken) {
    if (viewToken !== state.agentViewToken) return;
    try {
      const result = await askRequest(`/api/product/ask/runs/${encodeURIComponent(runId)}?workspace_id=${encodeURIComponent(workspaceId)}`);
      if (viewToken !== state.agentViewToken) return;
      renderAskResult(result, viewToken);
      revealAskResult(result);
      if (result.status === "INVESTIGATING") window.setTimeout(() => pollAsk(runId, workspaceId, viewToken), 500);
    } catch (error) { showAskError(error, viewToken); }
  }

  function showAskError(error, viewToken) {
    if (viewToken !== state.agentViewToken) return;
    const target = document.getElementById("ask-result");
    if (target) target.innerHTML = `<section class="error-box" role="alert"><h2>${askCopy("The request could not be completed", "请求暂时无法完成")}</h2><p>${askCopy("Check the local service and try again.", "请检查本地服务后重试。")}</p><details><summary>${askCopy("Error details", "错误详情")}</summary><p>${escapeHtml(error.message)} ${escapeHtml(error.code || "")}</p></details></section>`;
  }

  async function submitAsk(form, clarifiedSlot = null) {
    if (form.dataset.feedbackPending === "true") {
      announce(askCopy("Wait for the terminology change to finish.", "请等待术语更改保存完成。"));
      return;
    }
    const viewToken = ++state.agentViewToken;
    const workspaceId = state.workspace.workspace_id;
    const data = Object.fromEntries(new FormData(form));
    const domain = data.investigation_domain;
    delete data.investigation_domain;
    localizeAsk(data.query);
    const slotValue = document.getElementById("ask-slot").value;
    const clarification = state.askClarification;
    const pendingSlot = clarification?.query === data.query && clarification?.workspaceId === workspaceId ? clarification.slot : null;
    const slot = clarifiedSlot || pendingSlot || (slotValue === "" ? null : state.askScopes[Number(slotValue)]);
    state.askScope = !clarifiedSlot && !pendingSlot && slot ? { ...slot, as_of: data.as_of || "" } : null;
    state.askCandidates = [];
    state.askResult = null;
    const permission = document.getElementById("ask-external");
    const allowExternal = permission?.checked === true;
    if (permission) permission.checked = false;
    const target = document.getElementById("ask-result");
    target.innerHTML = `<p class="loading">${askCopy("Looking for a reviewed answer…", "正在查找可信答案…")}</p>`;
    try {
      const result = await askRequest("/api/product/ask", { method: "POST", body: JSON.stringify({
        ...data, workspace_id: workspaceId, subject: slot?.subject, relation: slot?.relation,
        allow_external_provider: allowExternal,
        ...(domain ? {investigation_context: {domain, snapshot_hash: state.askSourceContext?.snapshot_hash}} : {}),
      }) });
      if (viewToken !== state.agentViewToken) return;
      renderAskResult(result, viewToken);
      revealAskResult(result);
      if (["COMPLETED", "INVESTIGATING"].includes(result.status)) state.askClarification = null;
      if (result.status === "INVESTIGATING") pollAsk(result.agent_run_id, workspaceId, viewToken);
    } catch (error) { showAskError(error, viewToken); }
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
    const viewToken = ++state.agentViewToken;
    state.askScope = null;
    state.askClarification = null;
    const target = document.getElementById("ask-result");
    if (target) target.innerHTML = "";
    const nextScreen = state.screen;
    page.innerHTML = `<p class="loading">Switching workspace…</p>`;
    try {
      const result = await send("POST", "/api/workspaces/current", { workspace_id: workspacePicker.value });
      if (viewToken !== state.agentViewToken) return;
      state.workspace = result.workspace;
      await refreshWorkspaceState();
      if (viewToken === state.agentViewToken) await loadScreen(nextScreen);
    } catch (error) { if (viewToken === state.agentViewToken) renderError(error); }
  });

  document.getElementById("new-workspace").addEventListener("click", () => {
    invalidateAsk();
    page.innerHTML = `${heading("Workspace", "Create a workspace", "Each workspace has its own source artifacts, review queue, and temporal decisions.")}
      <form id="workspace-create" class="panel form-stack"><div class="field"><label for="workspace-name">Workspace name</label><input id="workspace-name" name="name" required maxlength="120"></div><button class="button" type="submit">Create workspace</button><button class="button quiet" type="button" data-action="back-sources">Cancel</button></form>`;
  });

  page.addEventListener("keydown", (event) => {
    if (event.target?.id === "ask-query" && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault(); document.getElementById("ask-form").requestSubmit();
    }
  });

  page.addEventListener("input", (event) => {
    if (event.target?.id === "ask-query") localizeAsk(event.target.value);
    if (["ask-query", "ask-slot", "ask-domain"].includes(event.target?.id)) state.askClarification = null;
    if (["ask-query", "ask-date", "ask-slot", "ask-external", "ask-domain"].includes(event.target?.id)) invalidateAsk();
  });

  page.addEventListener("change", async (event) => {
    if (event.target?.id === "ask-language") {
      state.askLanguage = event.target.value; localizeAsk();
      if (state.askResult) renderAskResult(state.askResult, state.agentViewToken);
      return;
    }
    if (["ask-query", "ask-slot", "ask-domain"].includes(event.target?.id)) state.askClarification = null;
    if (["ask-query", "ask-date", "ask-slot", "ask-external", "ask-domain"].includes(event.target?.id)) { invalidateAsk(); return; }
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
        announce(state.provider?.ready
          ? "Source ingestion started."
          : "Source saved. Semantic extraction is unavailable until a provider is enabled.");
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
        if (action === "approve" && form.querySelector('[data-review-action="approve"]').disabled) return;
        if (action === "reject") {
          for (const id of ["reviewer-id", "review-reason"]) {
            const field = document.getElementById(id);
            if (!field.checkValidity()) { field.reportValidity(); return; }
          }
        }
        const reviewViewToken = state.agentViewToken;
        const revalidation = state.revalidation;
        const linked = action === "approve" && revalidation;
        if (linked) Object.assign(data, {
          workspace_id: revalidation.workspace_id, review_token: revalidation.review_token,
          candidate_id: form.dataset.id,
          candidate_fingerprint: revalidation.candidates.find(item => item.candidate_id === form.dataset.id)?.candidate_fingerprint,
        });
        const result = await send("POST", linked
          ? `/api/review/revalidation/${encodeURIComponent(revalidation.original.decision_id)}/approve`
          : `/api/review/candidates/${encodeURIComponent(form.dataset.id)}/${action}`, data);
        if (reviewViewToken !== state.agentViewToken) return;
        announce(action === "approve" ? "Candidate approved and materialized." : "Candidate rejected.");
        await refreshWorkspaceState();
        await loadReview();
        if (action === "approve" && result.decision_id) await inspectDecision(result.decision_id);
      } else if (form.id === "ask-form") {
        await submitAsk(form);
      } else if (form.id === "feedback-form") {
        await changeFeedback("propose", Object.fromEntries(new FormData(form)));
      } else if (form.id === "timeline-as-of") {
        await inspectDecision(form.dataset.id, String(new FormData(form).get("as_of") || ""));
      }
    } catch (error) { renderError(error, `<p><button class="button quiet" data-action="reload">Refresh</button></p>`); }
  });

  page.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const { action, id } = button.dataset;
    try {
      if (action === "inspect-source") await inspectSource(id);
      else if (action === "reopen-task") {
        const workspaceId = state.workspace.workspace_id;
        const runId = savedTaskRun(workspaceId);
        if (runId) await pollAsk(runId, workspaceId, ++state.agentViewToken);
      }
      else if (["feedback-approve", "feedback-reject", "feedback-revoke"].includes(action)) {
        const actor = document.getElementById("feedback-actor");
        if (!actor.checkValidity()) { actor.reportValidity(); return; }
        await changeFeedback(action.replace("feedback-", ""), {id, actor: actor.value, human_confirmed: true});
      }
      else if (action === "choose-ask-scope") {
        const slot = state.askCandidates[Number(button.dataset.index)];
        if (slot) {
          const form = document.getElementById("ask-form");
          state.askClarification = {slot, query: String(new FormData(form).get("query")), workspaceId: state.workspace.workspace_id};
          await submitAsk(form, slot);
        }
      }
      else if (action === "inspect-candidate") await inspectCandidate(id);
      else if (action === "revalidate-decision") await inspectRevalidation(id);
      else if (action === "revalidate-candidate") await inspectCandidate(id, state.revalidation);
      else if (action === "retry-revalidation") {
        const view = state.revalidation;
        const viewToken = state.agentViewToken;
        const candidate = view.candidates.find(item => item.candidate_id === id);
        const result = await send("POST", `/api/review/revalidation/${encodeURIComponent(view.original.decision_id)}/approve`, {
          workspace_id: view.workspace_id, review_token: view.review_token,
          candidate_id: id, candidate_fingerprint: candidate.candidate_fingerprint,
        });
        if (viewToken !== state.agentViewToken) return;
        announce("The recorded revalidation approval was materialized.");
        await inspectDecision(result.decision_id);
      }
      else if (action === "inspect-decision") await inspectDecision(id);
      else if (action === "ask-timeline") await inspectDecision(id, button.dataset.asOf || "");
      else if (action === "ask-decision") {
        state.askScope = { subject: button.dataset.subject, relation: button.dataset.relation, as_of: button.dataset.asOf || "" };
        await loadScreen("ask");
      }
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
