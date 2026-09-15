"use strict";

const app = document.getElementById("app");
const liveRegion = document.getElementById("status-live");

const viewState = {
  context: null,
  snapshot: null,
  selectedEvidenceId: null,
  selectedClaim: null,
  pollTimer: null,
  lastCitationTrigger: null,
};

const finalKinds = new Set(["success", "insufficient", "error", "attention"]);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function displayStatus(status) {
  const labels = {
    completed: "Complete",
    in_progress: "In progress",
    blocked: "Blocked",
    pending: "Pending",
    unassigned: "Unassigned",
  };
  return labels[status] || String(status || "Unknown").replaceAll("_", " ");
}

function documentCountLabel(workspace) {
  if (!workspace || workspace.document_count === null || workspace.document_count === undefined) {
    return "";
  }
  const count = Number(workspace.document_count);
  return `${count} project ${count === 1 ? "note" : "notes"}`;
}

function announce(message) {
  liveRegion.textContent = "";
  window.setTimeout(() => {
    liveRegion.textContent = message;
  }, 20);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload?.error?.message || "The request failed.");
    error.code = payload?.error?.code || "HTTP_ERROR";
    throw error;
  }
  return payload;
}

function topbarHtml(workspace, snapshot = null) {
  const count = documentCountLabel(workspace);
  const showProvenance = Boolean(snapshot && snapshot.provenance);
  return `
    <header class="context-bar" id="context-bar" aria-label="Workspace context">
      <div class="brand-lockup" aria-label="LinkLoom">
        <span class="brand-thread" aria-hidden="true"></span>
        <span class="brand-name">LinkLoom</span>
      </div>
      <span class="context-divider" aria-hidden="true"></span>
      <div class="workspace-lockup">
        <span class="workspace-name">${escapeHtml(workspace?.display_name || "Decision workspace")}</span>
        ${count ? `<span class="document-count">${escapeHtml(count)}</span>` : ""}
      </div>
      <div class="context-actions">
        <span class="read-only-chip">Read-only</span>
        ${showProvenance ? `
          <button class="provenance-button" id="provenance-toggle" type="button" aria-expanded="false" aria-controls="provenance-popover">
            How LinkLoom found this <span class="provenance-chevron" aria-hidden="true">▾</span>
          </button>
          <section class="provenance-popover" id="provenance-popover" aria-labelledby="provenance-title" hidden>
            <p class="popover-kicker">Provenance</p>
            <h2 id="provenance-title">How the answer was reconstructed</h2>
            ${progressStepsHtml(snapshot.provenance.steps, "provenance-steps")}
          </section>
        ` : ""}
      </div>
    </header>
  `;
}

function progressStepsHtml(steps, className) {
  return `
    <ol class="${className}">
      ${(steps || []).map((step) => `
        <li class="is-${escapeHtml(step.state)}">
          <span class="step-mark" aria-hidden="true"></span>
          <span>${escapeHtml(step.label)}</span>
          ${className === "running-steps" ? `<span class="step-state">${escapeHtml(step.state)}</span>` : ""}
        </li>
      `).join("")}
    </ol>
  `;
}

function renderInitial(context) {
  viewState.snapshot = null;
  viewState.selectedEvidenceId = null;
  app.dataset.state = "initial";
  document.documentElement.dataset.state = "initial";
  document.title = "LinkLoom — Recover a team decision";
  app.innerHTML = `
    ${topbarHtml(context.workspace)}
    <main class="initial-view" id="main-content">
      <section class="initial-copy" aria-labelledby="initial-title">
        <p class="eyebrow">Decision recovery · grounded in project records</p>
        <h1 id="initial-title">Find the decision.<br>See the proof.</h1>
        <p class="lede">LinkLoom reconstructs what your team finally decided from project documents, then connects each claim to the record behind it.</p>
        <div class="value-order" aria-label="Answer structure">
          <div><span>01</span><strong>The decision</strong></div>
          <div><span>02</span><strong>Why it won</strong></div>
          <div><span>03</span><strong>What remains open</strong></div>
        </div>
      </section>
      <section class="query-composer" aria-labelledby="query-title">
        <p class="query-label">Ask this workspace</p>
        <h2 id="query-title">What did the team decide?</h2>
        <p class="composer-hint" id="query-hint">Ask for a final choice, rationale, owner, or unresolved gate.</p>
        <form id="query-form">
          <label class="sr-only" for="decision-query">Decision question</label>
          <textarea class="query-input" id="decision-query" name="query" aria-describedby="query-hint read-only-note" required>${escapeHtml(context.default_query || "")}</textarea>
          <div class="composer-footer">
            <span class="read-only-note" id="read-only-note">${escapeHtml(context.read_only_message || "Source notes remain unchanged.")}</span>
            <button class="primary-button" id="recover-button" type="submit">Recover decision</button>
          </div>
        </form>
      </section>
    </main>
  `;
  bindQueryForm();
  markReady();
}

function bindQueryForm() {
  const form = document.getElementById("query-form");
  const input = document.getElementById("decision-query");
  const button = document.getElementById("recover-button");
  if (!form || !input || !button) return;

  const updateDisabled = () => {
    button.disabled = !input.value.trim();
  };
  input.addEventListener("input", updateDisabled);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    button.disabled = true;
    await startRun(query);
  });
  updateDisabled();
}

async function startRun(query) {
  stopPolling();
  try {
    const snapshot = await fetchJson("/api/runs", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    renderSnapshot(snapshot);
    announce("LinkLoom started searching the workspace.");
    pollRun(snapshot.run.id);
  } catch (error) {
    renderNetworkError(error);
  }
}

function pollRun(runId) {
  stopPolling();
  const poll = async () => {
    try {
      const snapshot = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
      renderSnapshot(snapshot);
      if (!finalKinds.has(snapshot.kind)) {
        viewState.pollTimer = window.setTimeout(poll, 800);
      }
    } catch (error) {
      renderNetworkError(error);
    }
  };
  viewState.pollTimer = window.setTimeout(poll, 650);
}

function stopPolling() {
  if (viewState.pollTimer) {
    window.clearTimeout(viewState.pollTimer);
    viewState.pollTimer = null;
  }
}

function renderSnapshot(snapshot) {
  viewState.snapshot = snapshot;
  if (snapshot.kind === "running" || snapshot.kind === "attention") {
    renderRunning(snapshot);
  } else if (snapshot.kind === "success" || snapshot.kind === "insufficient") {
    renderResult(snapshot);
  } else if (snapshot.kind === "error") {
    renderError(snapshot);
  } else if (snapshot.kind === "empty") {
    renderEmpty(snapshot);
  } else {
    renderNetworkError(new Error("This product state is not supported."));
  }
}

function renderRunning(snapshot) {
  const evidence = snapshot.evidence?.[0];
  app.dataset.state = "running";
  document.documentElement.dataset.state = "running";
  document.title = "LinkLoom — Recovering decision";
  app.innerHTML = `
    ${topbarHtml(snapshot.workspace, snapshot)}
    <main class="workspace-layout" id="main-content">
      <section class="brief-pane running-brief" aria-labelledby="running-title">
        <div class="running-copy">
          <p class="eyebrow">Reconstructing the decision</p>
          <h1 id="running-title">Reading the project record, not guessing the answer.</h1>
          <p>LinkLoom is locating the authoritative decision, then checking its rationale, actions, and unresolved gates against the source notes.</p>
        </div>
        <div class="running-question">
          <p class="query-label">Your question</p>
          <p>${escapeHtml(snapshot.query)}</p>
        </div>
      </section>
      <aside class="source-inspector running-inspector" aria-labelledby="running-progress-title">
        <p class="inspector-kicker">Evidence in progress</p>
        <h2 id="running-progress-title">Finding the basis for the answer</h2>
        ${progressStepsHtml(snapshot.provenance.steps, "running-steps")}
        ${evidence ? `
          <section class="currently-reading" aria-label="Currently reviewed source">
            <p class="section-label">Currently reviewing</p>
            <p class="source-filename">${escapeHtml(evidence.relative_path)}</p>
            <blockquote>${escapeHtml(evidence.source.quote)}</blockquote>
          </section>
        ` : ""}
      </aside>
    </main>
  `;
  announce(`LinkLoom is ${snapshot.run.stage.replaceAll("_", " ")}.`);
  markReady();
}

function citationHtml(citationId) {
  const evidence = viewState.snapshot?.evidence?.find((item) => item.id === citationId);
  if (!evidence) return "";
  return `<button class="citation-button" type="button" data-evidence-id="${escapeHtml(evidence.id)}" title="${escapeHtml(evidence.label)}" aria-label="Open evidence ${evidence.ordinal}: ${escapeHtml(evidence.label)}">[${evidence.ordinal}]</button>`;
}

function citationsHtml(citations) {
  const buttons = (citations || []).map(citationHtml).join("");
  return buttons ? `<span class="citation-group">${buttons}</span>` : "";
}

function claimAttributes(citations) {
  return `data-claim data-citations="${escapeHtml((citations || []).join(" "))}"`;
}

function resultDefaultEvidence(snapshot) {
  const preferred = snapshot.kind === "insufficient"
    ? snapshot.uncertainty?.citations?.[0]
    : snapshot.decision?.citations?.[0];
  return preferred || snapshot.evidence?.[0]?.id || null;
}

function renderResult(snapshot) {
  viewState.selectedEvidenceId = resultDefaultEvidence(snapshot);
  viewState.selectedClaim = null;
  const insufficient = snapshot.kind === "insufficient";
  app.dataset.state = snapshot.kind;
  document.documentElement.dataset.state = snapshot.kind;
  document.title = insufficient
    ? "LinkLoom — Not enough evidence"
    : "LinkLoom — Recovered decision";

  const decisionText = insufficient
    ? snapshot.uncertainty?.statement || "The workspace does not contain enough evidence to answer this question."
    : snapshot.decision.value;
  const partial = !insufficient && snapshot.decision?.status === "partial";
  let outcomeLabel = "Approved · Confirmed";
  if (insufficient) outcomeLabel = "Not enough evidence · Unknown";
  if (partial) outcomeLabel = "Partial decision · Confirmed record";

  app.innerHTML = `
    ${topbarHtml(snapshot.workspace, snapshot)}
    <main class="workspace-layout" id="main-content">
      <article class="brief-pane" aria-labelledby="decision-title">
        <div class="brief-document">
          <header class="question-context">
            <p class="query-label">Question</p>
            <p>${escapeHtml(snapshot.query)}</p>
          </header>
          <section class="outcome-block claim ${partial ? "is-partial" : ""}" ${claimAttributes(snapshot.decision?.citations || snapshot.uncertainty?.citations)}>
            <span class="outcome-label ${insufficient ? "is-insufficient" : ""}">${escapeHtml(outcomeLabel)}</span>
            <h1 class="decision-headline" id="decision-title">
              ${escapeHtml(decisionText)}
              ${insufficient ? citationsHtml(snapshot.uncertainty?.citations) : citationsHtml(snapshot.decision?.citations)}
            </h1>
          </section>
          ${rationaleSectionHtml(snapshot, insufficient)}
          ${alternativesSectionHtml(snapshot)}
          ${actionsSectionHtml(snapshot)}
          ${uncertaintySectionHtml(snapshot)}
        </div>
      </article>
      <aside class="source-inspector" id="source-inspector" aria-label="Evidence source inspector"></aside>
      <button class="inspector-scrim" type="button" tabindex="-1" aria-hidden="true" data-action="close-inspector" aria-label="Close evidence inspector"></button>
    </main>
  `;
  viewState.selectedClaim = document.querySelector(".outcome-block[data-claim]");
  syncEvidenceSelection({ scrollSource: true });
  syncInspectorMode();
  announce(insufficient ? "Answer ready. There is not enough evidence." : "Answer ready. The decision is supported by project evidence.");
  markReady();
}

function rationaleSectionHtml(snapshot, insufficient) {
  if (!snapshot.rationale?.length) return "";
  return `
    <section class="brief-section" aria-labelledby="rationale-title">
      <h2 id="rationale-title">${insufficient ? "Known from the project record" : "Why this decision was made"}</h2>
      <ul class="rationale-list">
        ${snapshot.rationale.map((item) => `
          <li class="claim" ${claimAttributes(item.citations)}>${escapeHtml(item.point)} ${citationsHtml(item.citations)}</li>
        `).join("")}
      </ul>
    </section>
  `;
}

function alternativesSectionHtml(snapshot) {
  if (!snapshot.rejected_alternatives?.length) return "";
  return `
    <section class="brief-section" aria-labelledby="alternatives-title">
      <h2 id="alternatives-title">Alternatives the team rejected</h2>
      <ul class="alternatives-list">
        ${snapshot.rejected_alternatives.map((item) => `
          <li class="claim" ${claimAttributes(item.citations)}>
            <span class="alternative-name">${escapeHtml(item.alternative)}</span>
            <span class="alternative-reason">${escapeHtml(item.reason)} ${citationsHtml(item.citations)}</span>
          </li>
        `).join("")}
      </ul>
    </section>
  `;
}

function actionsSectionHtml(snapshot) {
  if (!snapshot.actions?.length) return "";
  return `
    <section class="brief-section" aria-labelledby="actions-title">
      <h2 id="actions-title">What happens next</h2>
      <table class="actions-table">
        <thead><tr><th>Action</th><th>Owner</th><th>Due</th><th>Status</th></tr></thead>
        <tbody>
          ${snapshot.actions.map((item) => `
            <tr class="claim" ${claimAttributes(item.citations)}>
              <td data-label="Action"><span class="action-name">${escapeHtml(item.description)}</span> ${citationsHtml(item.citations)}</td>
              <td class="action-meta" data-label="Owner">${escapeHtml(item.owner_label)}</td>
              <td class="action-meta" data-label="Due">${escapeHtml(item.deadline_label)}</td>
              <td data-label="Status"><span class="status-label status-${escapeHtml(item.status)}">${escapeHtml(displayStatus(item.status))}</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </section>
  `;
}

function uncertaintySectionHtml(snapshot) {
  const uncertainty = snapshot.uncertainty;
  const unresolved = snapshot.unresolved_items || [];
  if (!uncertainty && !unresolved.length) return "";
  return `
    <section class="uncertainty-section" aria-labelledby="uncertainty-title">
      <p class="section-label">Uncertainty</p>
      <h2 id="uncertainty-title">${snapshot.kind === "insufficient" ? "Still unresolved" : "What is not yet settled"}</h2>
      ${uncertainty?.statement && snapshot.kind !== "insufficient" ? `
        <p class="uncertainty-statement claim" ${claimAttributes(uncertainty.citations)}>
          ${escapeHtml(uncertainty.statement)} ${citationsHtml(uncertainty.citations)}
        </p>
      ` : ""}
      ${unresolved.length ? `
        <ul class="unresolved-list">
          ${unresolved.map((item) => `
            <li class="claim" ${claimAttributes(item.citations)}>
              <span>${escapeHtml(item.description)} ${citationsHtml(item.citations)}</span>
              <span class="status-label status-${escapeHtml(item.status)}">${escapeHtml(displayStatus(item.status))}</span>
            </li>
          `).join("")}
        </ul>
      ` : ""}
      ${uncertainty?.unknown_fields?.length ? `
        <p class="unknown-fields"><strong>Not established:</strong> ${escapeHtml(uncertainty.unknown_fields.join(" · "))}</p>
      ` : ""}
    </section>
  `;
}

function inspectorHtml(evidence) {
  if (!evidence) {
    return `<p class="inspector-empty">Select a citation to inspect its source.</p>`;
  }
  const evidenceCount = viewState.snapshot.evidence.length;
  const supports = summarizedSupports(evidence.supports);
  const location = evidence.line_start === evidence.line_end
    ? `Line ${evidence.line_start}`
    : `Lines ${evidence.line_start}–${evidence.line_end}`;
  return `
    <header class="inspector-header">
      <div class="inspector-title-row">
        <div>
          <p class="inspector-kicker">Source evidence</p>
          <h2 class="inspector-title" id="inspector-title" tabindex="-1">${escapeHtml(evidence.relative_path)}</h2>
          <p class="source-location">${escapeHtml(location)}</p>
        </div>
        <span class="inspector-position">${evidence.ordinal} / ${evidenceCount}</span>
        <button class="inspector-close" type="button" data-action="close-inspector" aria-label="Close evidence inspector">×</button>
      </div>
      <p class="supports-line"><strong>Supports:</strong> ${escapeHtml(supports)}</p>
    </header>
    <div class="source-scroll" id="source-scroll" tabindex="0" aria-label="${escapeHtml(evidence.label)}">
      ${sourcePreviewHtml(evidence)}
    </div>
    <nav class="evidence-nav" aria-label="Evidence citations">
      <div class="evidence-nav-label"><span>Evidence in this brief</span><span>J / K to navigate</span></div>
      <div class="evidence-nav-list">
        ${viewState.snapshot.evidence.map((item) => `
          <button class="citation-button" type="button" data-evidence-id="${escapeHtml(item.id)}" title="${escapeHtml(item.label)}" aria-label="Evidence ${item.ordinal}: ${escapeHtml(item.label)}">[${item.ordinal}]</button>
        `).join("")}
      </div>
    </nav>
  `;
}

function summarizedSupports(values) {
  const labels = (values || []).map((value) => {
    const category = String(value).split(":", 1)[0].trim();
    return category === "Unresolved" ? "Unresolved gates" : category;
  });
  const unique = [...new Set(labels.filter(Boolean))];
  if (unique.includes("Unresolved gates")) {
    const uncertaintyIndex = unique.indexOf("Uncertainty");
    if (uncertaintyIndex >= 0) unique.splice(uncertaintyIndex, 1);
  }
  return unique.join(" · ") || "Evidence";
}

function sourcePreviewHtml(evidence) {
  const source = evidence.source;
  if (source.preview_kind !== "document") {
    return `
      <section class="quote-fallback" aria-label="Verified source excerpt">
        <blockquote>${escapeHtml(source.quote)}</blockquote>
        <p class="fallback-reason">${escapeHtml(source.fallback_reason || "Only the verified excerpt is available.")}</p>
      </section>
    `;
  }
  return `
    <div class="source-document" role="table" aria-label="Source document lines">
      ${source.lines.map((line) => {
        const highlighted = line.number >= evidence.line_start && line.number <= evidence.line_end;
        return `
          <div class="source-line ${highlighted ? "is-highlighted" : ""}" role="row" ${highlighted ? 'data-highlighted="true"' : ""}>
            <span class="source-line-number" role="cell" aria-hidden="true">${line.number}</span>
            <span class="source-line-text" role="cell">${line.text ? escapeHtml(line.text) : "&nbsp;"}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function selectEvidence(evidenceId, trigger = null) {
  if (!viewState.snapshot?.evidence?.some((item) => item.id === evidenceId)) return;
  viewState.selectedEvidenceId = evidenceId;
  viewState.lastCitationTrigger = trigger;
  viewState.selectedClaim = trigger?.closest?.("[data-claim]") || null;
  syncEvidenceSelection({ scrollSource: true });
  if (window.matchMedia("(max-width: 1023px)").matches) {
    openInspector();
  }
}

function syncEvidenceSelection({ scrollSource = false } = {}) {
  const evidence = viewState.snapshot?.evidence?.find((item) => item.id === viewState.selectedEvidenceId);
  const inspector = document.getElementById("source-inspector");
  if (inspector) inspector.innerHTML = inspectorHtml(evidence);

  document.querySelectorAll("[data-evidence-id]").forEach((button) => {
    const active = button.dataset.evidenceId === viewState.selectedEvidenceId;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll("[data-claim]").forEach((claim) => {
    claim.classList.remove("has-active-evidence");
  });
  let selectedClaim = viewState.selectedClaim;
  if (!selectedClaim || !document.contains(selectedClaim)) {
    selectedClaim = [...document.querySelectorAll("[data-claim]")].find((claim) => {
      const citations = (claim.dataset.citations || "").split(" ");
      return citations.includes(viewState.selectedEvidenceId);
    }) || null;
  }
  selectedClaim?.classList.add("has-active-evidence");

  if (scrollSource && evidence?.source?.preview_kind === "document") {
    window.requestAnimationFrame(() => {
      const sourceScroll = document.getElementById("source-scroll");
      const highlighted = sourceScroll?.querySelector('[data-highlighted="true"]');
      if (sourceScroll && highlighted) {
        sourceScroll.scrollTop = Math.max(0, highlighted.offsetTop - sourceScroll.clientHeight * 0.35);
      }
    });
  }
}

function openInspector() {
  document.body.classList.add("inspector-open");
  syncInspectorMode();
  window.requestAnimationFrame(() => document.querySelector(".inspector-close")?.focus());
}

function closeInspector() {
  document.body.classList.remove("inspector-open");
  syncInspectorMode();
  if (viewState.lastCitationTrigger instanceof HTMLElement) {
    viewState.lastCitationTrigger.focus();
  }
}

const inspectorMedia = window.matchMedia("(max-width: 1023px)");

function syncInspectorMode() {
  const inspector = document.getElementById("source-inspector");
  if (!inspector) return;
  if (!inspectorMedia.matches) {
    document.body.classList.remove("inspector-open");
    inspector.removeAttribute("role");
    inspector.removeAttribute("aria-modal");
    inspector.removeAttribute("aria-hidden");
    inspector.inert = false;
    return;
  }
  const open = document.body.classList.contains("inspector-open");
  if (open) {
    inspector.setAttribute("role", "dialog");
    inspector.setAttribute("aria-modal", "true");
    inspector.removeAttribute("aria-hidden");
    inspector.inert = false;
  } else {
    inspector.removeAttribute("role");
    inspector.removeAttribute("aria-modal");
    inspector.setAttribute("aria-hidden", "true");
    inspector.inert = true;
  }
}

function renderError(snapshot) {
  app.dataset.state = "error";
  document.documentElement.dataset.state = "error";
  document.title = "LinkLoom — Reconstruction failed";
  app.innerHTML = `
    ${topbarHtml(snapshot.workspace, snapshot)}
    <main class="state-message" id="main-content">
      <span class="error-rule" aria-hidden="true"></span>
      <div role="alert" aria-atomic="true">
        <p class="eyebrow">Operational failure · not an evidence conclusion</p>
        <h1>${escapeHtml(snapshot.error?.title || "LinkLoom could not complete this reconstruction.")}</h1>
        <p class="state-copy">${escapeHtml(snapshot.error?.message || "The project record was left unchanged.")}</p>
      </div>
      <button class="primary-button" type="button" data-action="retry">Try this question again</button>
      <details class="technical-details">
        <summary>Technical details</summary>
        <code>${escapeHtml(snapshot.error?.code || "UI_RUNTIME_ERROR")}\n${escapeHtml(snapshot.error?.technical_message || "")}</code>
      </details>
    </main>
  `;
  announce("LinkLoom could not complete the reconstruction.");
  markReady();
}

function renderNetworkError(error) {
  stopPolling();
  const workspace = viewState.context?.workspace || { display_name: "Decision workspace" };
  renderError({
    kind: "error",
    workspace,
    query: viewState.snapshot?.query || "",
    provenance: viewState.snapshot?.provenance || null,
    error: {
      title: "The decision workspace could not be reached.",
      message: "Your notes were not changed. Check the local LinkLoom server and try again.",
      code: error.code || "NETWORK_ERROR",
      technical_message: error.message || "Network request failed.",
    },
  });
}

function retryFromError() {
  if (viewState.context) {
    renderInitial(viewState.context);
    return;
  }
  bootstrap();
}

function renderEmpty(snapshot) {
  app.dataset.state = "empty";
  document.documentElement.dataset.state = "empty";
  app.innerHTML = `
    ${topbarHtml(snapshot.workspace)}
    <main class="state-message" id="main-content">
      <p class="eyebrow">Empty workspace</p>
      <h1>There are no project records to search yet.</h1>
      <p class="state-copy">Choose a workspace containing Markdown project notes, then ask LinkLoom what the team decided.</p>
      <button class="secondary-button" type="button" data-action="retry">Return to the question</button>
    </main>
  `;
  markReady();
}

function toggleProvenance() {
  const button = document.getElementById("provenance-toggle");
  const panel = document.getElementById("provenance-popover");
  if (!button || !panel) return;
  const open = button.getAttribute("aria-expanded") !== "true";
  button.setAttribute("aria-expanded", String(open));
  panel.hidden = !open;
}

function navigateEvidence(delta) {
  const evidence = viewState.snapshot?.evidence || [];
  if (!evidence.length) return;
  const current = Math.max(0, evidence.findIndex((item) => item.id === viewState.selectedEvidenceId));
  const next = (current + delta + evidence.length) % evidence.length;
  selectEvidence(evidence[next].id);
}

function trapInspectorFocus(event) {
  if (event.key !== "Tab" || !document.body.classList.contains("inspector-open")) return;
  const inspector = document.getElementById("source-inspector");
  const focusable = inspector?.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])');
  if (!focusable?.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

document.addEventListener("click", (event) => {
  const citation = event.target.closest("[data-evidence-id]");
  if (citation) {
    selectEvidence(citation.dataset.evidenceId, citation);
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "close-inspector") closeInspector();
  if (action === "retry") retryFromError();
  if (event.target.closest("#provenance-toggle")) toggleProvenance();
  const popover = document.getElementById("provenance-popover");
  const toggle = document.getElementById("provenance-toggle");
  if (popover && toggle && !popover.hidden && !popover.contains(event.target) && !toggle.contains(event.target)) {
    toggle.setAttribute("aria-expanded", "false");
    popover.hidden = true;
  }
});

inspectorMedia.addEventListener("change", syncInspectorMode);

document.addEventListener("keydown", (event) => {
  trapInspectorFocus(event);
  if (event.key === "Escape") {
    if (document.body.classList.contains("inspector-open")) closeInspector();
    const panel = document.getElementById("provenance-popover");
    const button = document.getElementById("provenance-toggle");
    if (panel && !panel.hidden) {
      panel.hidden = true;
      button?.setAttribute("aria-expanded", "false");
      button?.focus();
    }
  }
  const inspector = document.getElementById("source-inspector");
  if (inspector?.contains(document.activeElement)) {
    if (event.key.toLowerCase() === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      navigateEvidence(1);
    } else if (event.key.toLowerCase() === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      navigateEvidence(-1);
    }
  }
  const target = event.target;
  const isEditing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable;
  if (event.key === "/" && !isEditing) {
    const query = document.getElementById("decision-query");
    if (query) {
      event.preventDefault();
      query.focus();
    }
  }
});

function markReady() {
  window.__LINKLOOM_READY__ = true;
  document.documentElement.dataset.linkloomUi = "ready";
}

async function bootstrap() {
  try {
    const context = await fetchJson("/api/context");
    viewState.context = context;
    const requested = new URLSearchParams(window.location.search).get("state") || "initial";
    if (requested === "initial") {
      renderInitial(context);
      return;
    }
    const snapshot = await fetchJson(`/api/demo/${encodeURIComponent(requested)}`);
    renderSnapshot(snapshot);
  } catch (error) {
    renderNetworkError(error);
  }
}

bootstrap();
