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
  function uiText(en, zh) { return state.language.startsWith("zh") ? zh : en; }

  function initialLanguage() {
    let requested, saved;
    try { requested = new URLSearchParams(window.location.search).get("lang"); } catch { /* Optional. */ }
    try { saved = window.localStorage.getItem("linkloom.locale"); } catch { /* Optional. */ }
    return String(requested || saved || document.documentElement.lang).startsWith("zh") ? "zh-CN" : "en-US";
  }
  state.language = initialLanguage();

  function localizeShell() {
    document.documentElement.lang = state.language;
    document.title = uiText("LinkLoom — Team Decision Memory", "LinkLoom — 团队决策记忆");
    document.querySelectorAll("[data-ui-en]").forEach(el => {
      el.textContent = uiText(el.dataset.uiEn, el.dataset.uiZh);
    });
    document.getElementById("workspace-picker")?.setAttribute("aria-label", uiText("Select workspace", "选择工作区"));
    const select = document.getElementById("interface-language");
    if (select) select.value = state.language.startsWith("zh") ? "zh" : "en";
  }

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
      throw new Error(uiText("LinkLoom could not reach its local application. Check that the server is still running.", "无法连接本地服务，请检查服务是否正在运行。"));
    }
    let payload;
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload?.error?.message || uiText("The operation could not be completed.", "操作未完成。"));
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

  function claimTypeLabel(value) {
    const labels = {DECISION: ["Decision candidate", "决策候选"], PROPOSAL: ["Proposal", "提案"],
      FACT: ["Source statement", "原文陈述"], QUESTION: ["Question", "问题"],
      PLAN: ["Plan", "计划"], REJECTED_OPTION: ["Rejected option", "已否决方案"],
      OPINION: ["Opinion", "观点"], SPECULATION: ["Speculation", "推测"],
      ACTION: ["Action", "行动项"], TASK: ["Task", "任务"], REPLACEMENT: ["Replacement", "替代决策"]};
    return uiText(...(labels[value] || ["Candidate", "候选"]));
  }

  function statusLabel(value) {
    const labels = {
      RESOLVED: ["Revalidated", "复核完成"], UNRESOLVED: ["Needs review", "待复核"], READ_ONLY: ["Read-only", "只读"],
      UNKNOWN: ["Needs confirmation", "待确认"], PARTIAL: ["Partial results", "部分结果"],
      BLOCKED: ["Cannot continue", "暂时无法继续"], CONFLICT: ["Conflicting evidence", "证据冲突"],
      REFUTED: ["Refuted by evidence", "证据否定"], FAILED: ["Extraction failed", "提取失败"],
      SAVED: ["Saved · extraction pending", "已保存 · 待提取"],
      COMPLETE: ["Extracted", "已提取"], INGESTING: ["Extracting…", "正在提取…"],
      PENDING_REVIEW: ["Pending review", "待审核"], APPROVED: ["Approved", "已批准"],
      REJECTED: ["Rejected", "已拒绝"], ACTIVE: ["Current", "当前有效"], CURRENT: ["Current", "当前有效"],
      FOUND: ["Reviewed record found", "已找到审核记录"], CANDIDATE: ["Pending review", "待审核"],
      PROPOSED_NOT_APPROVED: ["Unapproved proposal", "未批准提案"],
      SUBJECT_NOT_FOUND: ["No matching subject found", "未找到匹配对象"],
      RELATION_NOT_FOUND: ["No matching relationship found", "未找到匹配关系"],
      NO_VALID_RECORD_AT_TIME: ["No reviewed record for this date", "该日期暂无已审核记录"],
      STALE: ["Source changed", "来源已变化"], NEEDS_REVALIDATION: ["Needs revalidation", "待复核"],
      INVALIDATED: ["Invalidated", "已失效"], SUPERSEDED: ["Replaced", "已替代"],
      DELETED: ["Removed", "已移除"], REMOVED: ["Removed", "已移除"],
      SUPPORTING_ONLY: ["Unapproved proposal", "未批准提案"], VERIFIED: ["Quote checked", "原文已核验"],
      MODEL_PROPOSED: ["AI proposal", "AI 提案"], EVIDENCE_VALID: ["Citations checked", "引用已核验"],
    };
    return uiText(...(labels[String(value || "UNKNOWN").toUpperCase()] || labels.UNKNOWN));
  }

  function reviewReason(code) {
    const labels = {
      ENTITY_UNRESOLVED: ["Subject needs confirmation", "对象待确认"],
      RELATION_UNRESOLVED: ["Relationship needs confirmation", "关系待确认"],
      TEMPORAL_UNRESOLVED: ["Effective date needs confirmation", "生效日期待确认"],
      CONFLICT_REQUIRES_REVIEW: ["Conflicting records need review", "记录存在冲突，需复核"],
    };
    return uiText(...(labels[code] || ["Evidence needs review", "证据待复核"]));
  }

  function badge(value, displayValue = value) {
    const status = String(value || "UNKNOWN").toUpperCase();
    const visualStatus = String(displayValue || status).toUpperCase();
    const kind = ["FAILED", "INVALIDATED", "BLOCKED"].includes(visualStatus) ? "error"
      : ["STALE", "NEEDS_REVALIDATION", "INGESTING", "PENDING_REVIEW", "CONFLICT", "PARTIAL", "UNKNOWN"].includes(status) ? "warning"
        : ["SUPERSEDED", "DELETED", "REMOVED", "SUPPORTING_ONLY"].includes(status) ? "neutral" : "";
    return `<span class="badge ${kind}" data-status="${escapeHtml(status)}">${escapeHtml(statusLabel(displayValue))}</span>`;
  }

  function heading(title, description = "", action = "") {
    return `<header class="page-heading"><div><h1>${escapeHtml(title)}</h1>${description ? `<p class="lede">${escapeHtml(description)}</p>` : ""}</div>${action}</header>`;
  }

  function userError(error) {
    const messages = {
      WORKSPACE_REQUIRED: ["Create or select a workspace first.", "请先创建或选择工作区。"],
      SOURCE_NOT_FOUND: ["This source is unavailable in the current workspace.", "此来源在当前工作区中不可用。"],
      SOURCE_TOO_LARGE: ["Choose a source file of 256 KB or smaller.", "请选择不超过 256 KB 的来源文件。"],
      CANDIDATE_NOT_FOUND: ["This candidate is unavailable. Refresh Review.", "候选已不可用，请刷新审核页面。"],
      CANDIDATE_SOURCE_UNAVAILABLE: ["The candidate’s source is unavailable. Check current sources.", "候选来源已不可用，请核对当前来源。"],
      PROVENANCE_INVALID: ["The source evidence changed. Review the current version.", "来源证据已变化，请核对当前版本。"],
      RELATION_UNRESOLVED: ["Confirm the relationship before approval.", "请确认关系后再批准。"],
      REVIEW_STALE: ["This candidate changed. Refresh Review before continuing.", "候选已变化，请刷新审核页面后继续。"],
      MATERIALIZATION_PENDING: ["Approval recorded; saving the decision is incomplete.", "批准已记录，决策尚未保存完成。"],
      DECISION_NOT_FOUND: ["This decision is unavailable in the current workspace.", "此决策在当前工作区中不可用。"],
    };
    return messages[error.code] ? uiText(...messages[error.code]) : error.message || uiText("The operation failed.", "操作失败。");
  }

  function renderError(error, retry = "") {
    page.innerHTML = `${heading(uiText("This page could not load", "页面加载失败"))}
      <section class="error-box" role="alert"><strong>${escapeHtml(userError(error))}</strong>
      ${error.code ? `<details><summary>${uiText("Error details", "错误详情")}</summary><p>${escapeHtml(error.code)} · ${escapeHtml(error.message)}</p></details>` : ""}${retry}</section>`;
    announce(userError(error));
  }

  function updateWorkspacePicker() {
    localizeShell();
    workspacePicker.innerHTML = `<option value="">${uiText("Select workspace", "选择工作区")}</option>${state.workspaces.map((workspace) =>
      `<option value="${escapeHtml(workspace.workspace_id)}" ${workspace.workspace_id === state.workspace?.workspace_id ? "selected" : ""}>${escapeHtml(workspace.name)}</option>`
    ).join("")}`;
    workspacePicker.disabled = state.workspaces.length === 0;
    document.getElementById("provider-state").textContent = state.provider?.ready ? uiText("AI extraction ready", "AI 提取已就绪") : uiText("Decision extraction unavailable", "决策提取未配置");
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
    page.innerHTML = `${heading(uiText("Create a workspace", "创建工作区"), uiText("Keep each project’s sources and decisions together.", "集中管理项目的来源与决策。"))}
      <form id="workspace-create" class="panel form-stack" aria-label="${escapeHtml(uiText("Create workspace", "创建工作区"))}">
        <div class="field"><label for="workspace-name">${uiText("Workspace name", "工作区名称")}</label><input id="workspace-name" name="name" maxlength="120" required placeholder="Product launch"></div>
        <button class="button" type="submit">${uiText("Create workspace", "创建工作区")}</button>
      </form>`;
  }

  async function loadScreen(screen = state.screen) {
    const viewToken = ++state.agentViewToken;
    setNavigation(screen);
    state.detailView = null;
    state.sourceId = null;
    state.candidateId = null;
    state.revalidation = null;
    state.decisionId = null;
    if (!state.workspace) { await showNoWorkspace(); return; }
    page.innerHTML = `<section class="state-card"><p class="loading">${uiText("Loading…", "正在加载…")}</p></section>`;
    try {
      if (screen === "sources") await loadSources();
      else if (screen === "review") await loadReview();
      else if (screen === "decisions") await loadDecisions();
      else await showAsk();
    } catch (error) {
      if (viewToken === state.agentViewToken) renderError(error, `<p><button class="button quiet" data-action="reload">${uiText("Try again", "重试")}</button></p>`);
    }
  }

  async function loadSources() {
    const viewToken = state.agentViewToken;
    const { sources } = await request("/api/sources");
    if (viewToken !== state.agentViewToken) return;
    const providerReady = Boolean(state.provider?.ready);
    const list = sources.length ? sources.map((source) => `
      <article class="card" data-source-card="${escapeHtml(source.source_id)}">
        <div class="card-head"><div><h3>${escapeHtml(source.name)}</h3><p class="status-line">${uiText("Saved", "保存于")} ${escapeHtml(source.updated_at)}</p></div>${badge(source.ingestion_status, source.error?.startsWith("PROVIDER_UNAVAILABLE:") ? "SAVED" : source.ingestion_status)}</div>
        <div class="metadata"><span>${uiText("Candidates", "候选")} <strong>${source.candidate_count}</strong></span><span>${uiText("Review required", "待审核")} <strong>${source.review_required_count}</strong></span><span>${source.segment_summary.total} ${uiText("segments", "个片段")} · ${source.segment_summary.processed} ${uiText("processed", "已处理")} · ${source.segment_summary.failed} ${uiText("failed", "失败")}</span></div>
        ${source.error ? `<p class="hint">${source.error.startsWith("PROVIDER_UNAVAILABLE:") ? uiText("Decision extraction needs AI setup.", "决策提取需先配置 AI。") : uiText("Decision extraction failed. Open source details to review the error.", "决策提取失败，请查看来源详情。")}</p>` : ""}
        <div class="actions"><button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(source.source_id)}">${uiText("Inspect source", "查看来源")}</button>${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">${uiText("Retry failed segment", "重试失败片段")}</button>` : ""}</div>
      </article>`).join("") : `<section class="empty"><h2>${uiText("No sources yet", "暂无来源")}</h2><p>${uiText("Add meeting notes or a decision record using the form.", "使用右侧表单添加会议记录或决策记录。")}</p></section>`;
    page.innerHTML = `${heading(uiText("Sources", "来源"))}
      <div class="split"><section><div class="section-title"><h2>${uiText("Workspace sources", "工作区来源")}</h2><span class="muted">${sources.length} ${uiText("sources", "个来源")}</span></div>${list}</section>
      <section class="panel"><h2>${uiText("Add a source", "添加来源")}</h2><p class="hint">${uiText("One timestamped message per line:", "每行一条带时间戳的消息：")} <code>[2026-10-08T10:00:00Z] Speaker: what was said</code></p>
        ${providerReady ? `<p class="hint">${uiText("Submitting sends source text to the configured AI provider and may incur model costs.", "提交将向已配置的 AI 服务发送来源内容，并可能产生模型费用。")}</p>` : `<div class="review-guidance"><strong>${uiText("Save sources now; extract decisions after AI setup.", "可先保存来源，配置 AI 后再提取决策。")}</strong><details><summary>${uiText("AI setup details", "AI 配置详情")}</summary><p>${escapeHtml(state.provider?.message || "Configure a provider before extracting source text.")}</p></details></div>`}
        <form id="source-create" class="form-stack"><div class="field"><label for="source-file">${uiText("Choose a Markdown or text artifact", "选择 Markdown 或文本文件")}</label><input id="source-file" type="file" accept=".md,.txt,text/markdown,text/plain"></div>
        <div class="field"><label for="source-name">${uiText("Artifact name", "来源名称")}</label><input id="source-name" name="name" maxlength="180" required placeholder="meeting-2026-10-08.md"></div>
        <div class="field"><label for="source-content">${uiText("Timestamped source text", "带时间戳的原文")}</label><textarea class="source-input" id="source-content" name="content" required></textarea></div>
        <button class="button" type="submit">${providerReady ? uiText("Import and extract", "导入并提取") : uiText("Save source", "保存来源")}</button></form></section></div>`;
  }

  async function inspectSource(sourceId) {
    const viewToken = ++state.agentViewToken;
    const workspaceId = state.workspace?.workspace_id;
    const { source } = await request(`/api/sources/${encodeURIComponent(sourceId)}`);
    if (viewToken !== state.agentViewToken || workspaceId !== state.workspace?.workspace_id) return;
    const providerReady = Boolean(state.provider?.ready);
    state.detailView = "source";
    state.sourceId = sourceId;
    const extractionUnavailable = source.error?.startsWith("PROVIDER_UNAVAILABLE:");
    const evidence = source.evidence.length ? source.evidence.map((item) => `<div class="card"><div class="metadata"><span>${uiText("Lines", "行号")} ${item.line_start}–${item.line_end}</span><span>${badge("VERIFIED")}</span></div><blockquote class="quote">${escapeHtml(item.quote)}</blockquote></div>`).join("") : `<p class="empty">${uiText("No timestamped evidence spans were parsed.", "未解析出带时间戳的原文，请检查输入格式。")}</p>`;
    page.innerHTML = `${heading(source.name)}
      <div class="detail-layout"><section class="panel"><div class="card-head"><h2>${uiText("Ingestion", "提取状态")}</h2>${badge(source.ingestion_status, source.error?.startsWith("PROVIDER_UNAVAILABLE:") ? "SAVED" : source.ingestion_status)}</div>${extractionUnavailable ? `<p class="review-guidance"><strong>${uiText("Source saved; decision extraction is unavailable.", "来源已保存，决策提取暂不可用。")}</strong> ${uiText("Set up AI, then import the original file again to extract decisions.", "配置 AI 后，请重新导入原文件以提取决策。")}</p>` : ""}<p>${source.candidate_count} ${uiText("candidates", "个候选")} · ${source.review_required_count} ${uiText("need review", "待审核")} · ${source.segment_summary.total} ${uiText("segments", "个片段")} · ${source.segment_summary.processed} ${uiText("processed", "已处理")} · ${source.segment_summary.failed} ${uiText("failed", "失败")}</p>${source.error && !extractionUnavailable ? `<div class="error-box" role="alert">${uiText("Decision extraction failed. Check the details and retry.", "决策提取失败，请查看详情后重试。")}</div>` : ""}<details><summary>${uiText("Extraction details", "提取详情")}</summary>${source.error ? `<p>${escapeHtml(source.error)}</p>` : ""}${source.segments.map((segment) => `<p class="metadata">${uiText("Segment", "片段")} ${segment.ordinal + 1}: ${escapeHtml(segment.state)} · ${segment.attempt_count} ${uiText("attempts", "次尝试")}</p>`).join("")}</details>${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">${uiText("Retry failed segment", "重试失败片段")}</button>` : ""}
        <h3>${uiText("Exact evidence spans", "来源原文")}</h3>${evidence}<details><summary>${uiText("Source version details", "来源版本详情")}</summary><p class="metadata">${uiText("Version", "版本")}: ${escapeHtml(source.source_version)} · SHA-256: ${escapeHtml(source.content_sha256)}</p></details></section>
      <section class="panel"><h2>${uiText("Update source", "更新来源")}</h2><p class="hint">${uiText("Updating creates a new source version and rechecks affected decisions.", "更新将创建新版本，并重新核验相关决策。")}${providerReady ? uiText(" Source text will be sent to the configured AI provider and may incur model costs.", "来源内容将发送至已配置的 AI 服务，并可能产生模型费用。") : uiText(" Decision extraction is unavailable until AI is configured.", "配置 AI 后才能提取决策。")}</p>
        <form id="source-update" class="form-stack" data-id="${escapeHtml(sourceId)}"><div class="field"><label for="update-name">${uiText("Artifact name", "来源名称")}</label><input id="update-name" name="name" value="${escapeHtml(source.name)}" required></div>
        <div class="field"><label for="update-content">${uiText("Timestamped source text", "带时间戳的原文")}</label><textarea class="source-input" id="update-content" name="content" required>${escapeHtml(source.content)}</textarea></div>
        <button class="button" type="submit">${uiText("Save new source version", "保存新版本")}</button><button class="button danger" type="button" data-action="delete-source" data-id="${escapeHtml(sourceId)}">${uiText("Remove from workspace", "从工作区移除")}</button></form></section></div>
      <p><button class="button quiet" data-action="back-sources">${uiText("Back to sources", "返回来源")}</button></p>`;
    if (source.ingestion_status === "INGESTING") pollSource(sourceId, true);
  }

  async function pollSource(sourceId, refreshScreen = false) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const { source } = await request(`/api/sources/${encodeURIComponent(sourceId)}`);
      if (source.ingestion_status !== "INGESTING") {
        announce(uiText(`Extraction: ${statusLabel(source.ingestion_status)}. ${source.review_required_count} candidates need review.`, `提取状态：${statusLabel(source.ingestion_status)}。${source.review_required_count} 个候选待审核。`));
        await refreshWorkspaceState();
        if (refreshScreen && state.screen === "sources" && state.sourceId === sourceId) await inspectSource(sourceId);
        else if (state.screen === "sources") await loadSources();
        return;
      }
    }
  }

  async function loadReview() {
    const viewToken = state.agentViewToken;
    const [{ candidates, supporting_proposals: proposals = [] }, attention] = await Promise.all([
      request("/api/review/candidates"), request("/api/review/attention"),
    ]);
    if (viewToken !== state.agentViewToken) return;
    document.getElementById("review-count").textContent = candidates.length ? String(candidates.length) : "";
    const items = candidates.length ? candidates.map((candidate) => `
      <article class="card"><div class="card-head"><div><p class="metadata">${escapeHtml(claimTypeLabel(candidate.claim_type))}</p><h3>${escapeHtml(candidate.proposed_value || uiText("Needs review", "待审核"))}</h3></div>${badge(candidate.status)}</div>
        <div class="metadata"><span>${uiText("Subject", "对象")} <strong>${escapeHtml(candidate.subject || uiText("Needs review", "待审核"))}</strong></span><span>${uiText("Relation", "关系")} <strong>${escapeHtml(candidate.relation || uiText("Needs review", "待审核"))}</strong></span><span>${uiText("Source", "来源")} <strong>${escapeHtml(candidate.source_name)}</strong></span><span>${uiText("Effective", "生效时间")} <strong>${escapeHtml(candidate.valid_from || uiText("Not resolved", "待确认"))}</strong></span></div>
        ${candidate.review_reasons.length ? `<p class="hint">${escapeHtml(candidate.review_reasons.map(reviewReason).join(" · "))}</p>` : ""}<button class="button quiet" data-action="inspect-candidate" data-id="${escapeHtml(candidate.candidate_id)}">${uiText("Review candidate", "审核候选")}</button></article>`).join("") : `<section class="empty"><h2>${uiText("No candidates need review", "暂无待审核候选")}</h2><p>${uiText("Decisions awaiting your approval appear here.", "需要你批准的决策将显示在这里。")}</p></section>`;
    const proposalItems = proposals.length ? proposals.map((candidate) => `
      <article class="card"><div class="card-head"><div><h3>${escapeHtml(candidate.subject || candidate.proposed_value || uiText("Unresolved proposal", "待确认提案"))}</h3></div>${badge("SUPPORTING_ONLY")}</div>
        <p class="decision-value">${escapeHtml(candidate.proposed_value || uiText("Value not resolved", "内容待确认"))}</p><p class="metadata">${escapeHtml(candidate.source_name)} · ${uiText("lines", "行号")} ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="hint">${uiText("Unapproved proposal · kept outside Decision Memory.", "未批准提案 · 尚未纳入决策记忆")}</p></article>`).join("") : `<p class="muted">${uiText("No proposals are currently held as supporting evidence.", "暂无支持性提案。")}</p>`;
    const attentionCards = attention.decisions.length ? attention.decisions.map((decision) => `<article class="card"><div class="card-head"><div><h3>${escapeHtml(decision.subject)} · ${escapeHtml(decision.relation)}</h3><p>${escapeHtml(decision.value)}</p></div>${badge(decision.memory_state)}</div><p class="hint">${uiText("Source evidence changed or is unavailable. Revalidate this decision against a current source before relying on it.", "来源已变化或不可用，请核对当前原文后复核此决策。")}</p><button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(decision.decision_id)}">${uiText("Revalidate decision", "复核决策")}</button></article>`).join("") : `<p class="muted">${uiText("No Decision Memory records currently need revalidation.", "暂无待复核决策。")}</p>`;
    page.innerHTML = `${heading(uiText("Review", "审核"))}
      <div class="section-title"><h2>${uiText("Pending candidates", "待审核候选")}</h2><span class="muted">${candidates.length}</span></div>${items}
      <div class="section-title"><h2>${uiText("Supporting proposals", "支持性提案")}</h2><span class="muted">${proposals.length}</span></div>${proposalItems}
      <div class="section-title"><h2>${uiText("Decision records needing attention", "待复核决策")}</h2></div>${attentionCards}`;
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
    state.detailView = "candidate";
    state.candidateId = candidateId;
    const unresolved = candidate.current_unresolved_fields;
    const relationUnresolved = unresolved.includes("relation");
    const relationGuidance = relationUnresolved ? `<div class="review-guidance" role="note" id="relation-guidance"><strong>${uiText("Resolve the relationship before approval.", "确认关系后才能批准。")}</strong><p>Extracted wording: ${escapeHtml(candidate.relation_phrase || uiText("Not present", "未提供"))}.</p><p>${uiText("Check the source. With AI enabled, import the original file again for a new extraction. If the relationship remains unclear, keep it pending or reject it with your name and reason.", "请核对原文。启用 AI 后，可重新导入原始文件以再次提取。若关系仍不明确，可保持待审核，或填写姓名和理由后拒绝。")}</p><button class="button quiet" type="button" data-action="inspect-source" data-id="${escapeHtml(candidate.source_id)}">${uiText("Open original source", "查看原始来源")}</button></div>` : "";
    const resolutionFields = unresolved.length ? `<section class="panel"><h3>${uiText("Resolve fields from the evidence", "根据原文补全字段")}</h3><p class="hint">${uiText("Fill in the subject and effective date from the source. Leave uncertain fields empty; the date has no default.", "请根据原文填写对象和生效日期。不确定的字段留空，日期不会自动填写。")}</p>
      ${unresolved.includes("subject") ? `<div class="field"><label for="review-subject">${uiText("Subject", "对象")}</label><input id="review-subject" name="subject" value="${escapeHtml(candidate.subject || "")}" required></div>` : ""}
      ${relationGuidance}
      ${unresolved.includes("valid_from") ? `<div class="field"><label for="review-valid-from">${uiText("Effective date or timestamp", "生效日期或时间")}</label><input id="review-valid-from" name="valid_from" placeholder="${escapeHtml(uiText("YYYY-MM-DD or ISO 8601 with timezone", "YYYY-MM-DD 或包含时区的 ISO 8601 时间"))}" required></div>` : ""}</section>` : `<p class="hint">${uiText("Subject, relationship and effective date are available from the source.", "对象、关系和生效日期已从来源中提取。")}</p>`;
    page.innerHTML = `${heading(candidate.proposed_value || uiText("Needs review", "待审核"))}
      <div class="detail-layout"><section class="panel">${badge(candidate.status)}<div class="metadata"><span>${uiText("Subject", "对象")} <strong>${escapeHtml(candidate.subject || uiText("Needs review", "待审核"))}</strong></span><span>${uiText("Relation", "关系")} <strong>${escapeHtml(candidate.relation || uiText("Needs review", "待审核"))}</strong></span><span>${uiText("Valid from", "生效时间")} <strong>${escapeHtml(candidate.valid_from || uiText("Not resolved", "待确认"))}</strong></span></div>
        <p class="section-title"><strong>${uiText("Review reasons", "需要审核的原因")}</strong></p><p>${escapeHtml(candidate.review_reasons.map(reviewReason).join(" · ") || uiText("Human authorization required", "需人工批准"))}</p><details><summary>${uiText("Review details", "审核详情")}</summary><p>${escapeHtml(candidate.review_reasons.join(" · "))}</p></details>
        <h3>${uiText("Exact supporting evidence", "支持此决策的原文")}</h3><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · ${uiText("lines", "行号")} ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <details><summary>${uiText("Source version and provenance", "来源版本与引用")}</summary><p>${uiText("Version", "版本")}: ${escapeHtml(candidate.evidence.source_version)}</p><p>${uiText("Evidence reference", "证据引用")}: ${escapeHtml(candidate.evidence.evidence_ref)}</p></details></section>
      <form id="candidate-review" class="panel form-stack" data-id="${escapeHtml(candidateId)}">${revalidation ? `<div class="notice"><strong>${uiText("New approval for a stale decision", "重新审核过期决策")}</strong><p>${uiText("Original", "原记录")}: ${escapeHtml(revalidation.original.value)}. ${uiText("Approve this candidate against current evidence. The previous approval stays in history.", "请根据当前证据批准此候选，原审批保留在历史中。") }</p></div>` : ""}${resolutionFields}
        <div class="field"><label for="reviewer-id">${uiText("Reviewer", "审核人")}</label><input id="reviewer-id" name="reviewer_id" required maxlength="120" placeholder="${escapeHtml(uiText("Your name", "你的姓名"))}"></div>
        <div class="field"><label for="review-reason">${uiText("Review reason", "审核理由")}</label><textarea id="review-reason" name="reason" required maxlength="1000" placeholder="${escapeHtml(uiText("Why is this evidence sufficient?", "请说明批准或拒绝的依据"))}"></textarea></div>
        <div class="actions"><button class="button" type="submit" data-review-action="approve" ${relationUnresolved ? 'disabled aria-describedby="relation-guidance"' : ""}>${uiText("Approve decision", "批准决策")}</button><button class="button danger" type="submit" data-review-action="reject" formnovalidate>${uiText("Reject", "拒绝")}</button></div></form></div>
      <p>${revalidation ? `<button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(revalidation.original.decision_id)}">${uiText("Keep unresolved / back to comparison", "暂不确认，返回对照")}</button>` : `<button class="button quiet" data-action="back-review">${uiText("Back to Review", "返回审核")}</button>`}</p>`;
  }

  async function inspectRevalidation(decisionId) {
    const viewToken = ++state.agentViewToken;
    const { revalidation: view } = await request(`/api/review/revalidation/${encodeURIComponent(decisionId)}`);
    if (viewToken !== state.agentViewToken) return;
    state.detailView = "revalidation";
    state.revalidation = view;
    const original = view.original;
    const reasonLabels = {
      ORIGINAL_EVIDENCE_UNAVAILABLE: uiText("The exact historical evidence cannot be verified.", "历史原文无法核验。"),
      SOURCE_MISSING: uiText("The original source record is unavailable.", "原始来源不可用。"),
      SOURCE_REMOVED: uiText("The source was removed from the active workspace. Historical evidence is retained for audit only.", "来源已从工作区移除，历史证据仅供追溯。"),
      EVIDENCE_WITHDRAWN_OR_CHANGED: uiText("The original timestamp, speaker and exact quote no longer occur uniquely in the current source. The evidence was changed, withdrawn or duplicated.", "当前来源中的时间、发言人或原文已变化、撤回或重复，请重新核对。"),
      SOURCE_RECONCILIATION_REQUIRED: uiText("Source reconciliation still requires review.", "来源变更需要复核。"),
      DECISION_INVALIDATED: uiText("This decision was invalidated. It cannot be restored through stale revalidation.", "此决策已失效，无法通过过期记录复核恢复。"),
    };
    const evidence = original.evidence.map(item => `<blockquote class="quote">${escapeHtml(item.quote || uiText("Historical quote unavailable", "历史原文不可用"))}</blockquote><p class="metadata">${escapeHtml(item.source_name)} · ${uiText("lines", "行号")} ${item.line_start || "?"}–${item.line_end || "?"} · ${uiText("Version", "版本")} ${escapeHtml(item.source_version || uiText("Unavailable", "不可用"))}</p>`).join("");
    const sources = view.current_sources.map(source => `<article class="card"><div class="card-head"><h3>${escapeHtml(source.name)}</h3>${badge(source.active ? source.ingestion_status : "REMOVED")}</div><p class="metadata">${uiText("Current version", "当前版本")}: ${escapeHtml(source.source_version)}</p><pre class="quote">${escapeHtml(source.content)}</pre></article>`).join("");
    const candidates = view.candidates.map(candidate => `<article class="card"><div class="card-head"><h3>${escapeHtml(candidate.proposed_value)}</h3>${badge(candidate.status)}</div><p class="metadata">${escapeHtml(candidate.subject)} · ${escapeHtml(candidate.relation)} · Effective ${escapeHtml(candidate.valid_from)}</p><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · ${uiText("lines", "行号")} ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>${candidate.recorded_approval ? `<details><summary>${uiText("Recorded approval (read-only)", "已记录的批准（只读）")}</summary><pre>${escapeHtml(JSON.stringify(candidate.recorded_approval, null, 2))}</pre></details><p class="hint">${uiText("Approval is already recorded. Finish materialization using only that authorization.", "批准已记录，请完成保存。")}</p>` : ""}<button class="button" data-action="${candidate.status === "APPROVED" ? "retry-revalidation" : "revalidate-candidate"}" data-id="${escapeHtml(candidate.candidate_id)}">${candidate.status === "APPROVED" ? "Finish recorded approval" : "Review this new candidate"}</button></article>`).join("");
    page.innerHTML = `${heading(`${original.subject} · ${original.relation}`, uiText("Review current evidence before approving a replacement.", "核对当前证据后，再批准替代记录。"))}
      <section class="panel"><div class="card-head"><h2>${uiText("Original decision", "原决策")}: ${escapeHtml(original.value)}</h2>${badge(original.memory_state)}</div><p class="metadata">Effective ${escapeHtml(original.valid_from)} · ${escapeHtml(statusLabel(view.status))}</p><h3>${uiText("Why this needs attention", "复核原因")}</h3>${view.reasons.map(reason => `<p>${escapeHtml(reasonLabels[reason.code] || uiText("Evidence needs review", "证据待复核"))}</p>`).join("")}${evidence}<details><summary>${uiText("Original authorization receipts (read-only)", "原审批记录（只读）")}</summary><pre>${escapeHtml(JSON.stringify({receipts: view.original_receipts, reasons: view.reasons}, null, 2))}</pre></details></section>
      <section class="panel"><h2>${uiText("Current sources", "当前来源")}</h2>${sources || `<p>${uiText("No current source record is available. Add a source in Sources, then return to review.", "当前来源不可用，请添加来源后返回审核。")}</p>`}</section>
      <section class="panel"><h2>${uiText("New candidates for explicit review", "待批准的新候选")}</h2>${candidates || (view.status === "RESOLVED" ? `<p>${uiText("This revalidation is complete. The new approval and original record can be inspected in history.", "复核已完成，新批准和原记录均可在历史中查看。")}</p>` : view.status === "READ_ONLY" ? `<p>${uiText("This record is read-only and cannot be revalidated here.", "此记录为只读，无法在此复核。")}</p>` : `<p>${uiText("No valid new candidate is available. This decision stays unresolved. Add or update a source, finish extraction, and return here.", "暂无可用的新候选，此决策仍待确认。请添加或更新来源，完成提取后返回审核。")}</p>`)}${view.completion ? `<p>${uiText("Revalidation approved", "复核已批准")} ${escapeHtml(view.completion.authorized_at)}. ${uiText("The original record remains in history.", "原记录保留在历史中。") }</p><button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(view.completion.decision_id)}">${uiText("View approved decision and history", "查看已批准决策及历史")}</button>` : ""}</section>
      <div class="actions"><button class="button quiet" data-action="back-review">${view.status === "UNRESOLVED" ? uiText("Keep unresolved", "暂不确认") : uiText("Back to Review", "返回审核")}</button><button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(decisionId)}">${uiText("Refresh comparison", "刷新对照")}</button></div>`;
  }

  async function loadDecisions() {
    const viewToken = state.agentViewToken;
    const { decisions } = await request("/api/decisions");
    if (viewToken !== state.agentViewToken) return;
    const cards = decisions.length ? decisions.map((decision) => `
      <article class="card"><div class="card-head"><div><p class="metadata">${escapeHtml(decisionCategory(decision.relation, state.language.startsWith("zh") ? "zh" : "en"))}</p><h3>${escapeHtml(decision.subject)}</h3></div>${badge(decision.memory_state)}</div>
        <p class="decision-value">${escapeHtml(decision.value)}</p><div class="metadata"><span>${uiText("Valid from", "生效时间")} <strong>${escapeHtml(decision.valid_from)}</strong></span><span>${uiText("Source", "来源")} <strong>${escapeHtml(decision.evidence[0]?.source_name || uiText("Unavailable", "不可用"))}</strong></span></div>
        <button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(decision.decision_id)}">${uiText("View history and evidence", "查看历史与证据")}</button></article>`).join("") : `<section class="empty"><h2>${uiText("No decisions in this workspace", "暂无决策记录")}</h2><p>${uiText("Import a source, review a candidate, and approve it before it appears here.", "导入来源并批准候选决策后，即可在此查看。")}</p></section>`;
    page.innerHTML = `${heading(uiText("Decision Memory", "决策记忆"))}
      <div class="section-title"><h2>${uiText("Current records", "当前记录")}</h2><span class="muted">${decisions.length}</span></div><div class="grid">${cards}</div>`;
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
    state.detailView = "decision";
    state.decisionId = decisionId;
    const evidenceRows = (evidence) => (evidence || []).map((item) => `<div class="metadata">
      ${uiText("Source", "来源")}: ${escapeHtml(item.source_name || uiText("Unknown", "未知"))} · ${uiText("lines", "行号")} ${escapeHtml(item.line_start || "?")}–${escapeHtml(item.line_end || "?")}
      </div><details><summary>${uiText("Source version and references", "来源版本与引用")}</summary><p>${uiText("Version", "版本")}: ${escapeHtml(item.source_version || uiText("Unavailable", "不可用"))} · ${uiText("Evidence", "证据")}: ${escapeHtml(item.evidence_ref || uiText("Unavailable", "不可用"))}</p></details>
      <blockquote class="quote">${escapeHtml(item.quote || "Verified quote unavailable")}</blockquote>
      ${item.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(item.source_id)}">${uiText("Inspect source", "查看来源")}</button>` : ""}`).join("");
    const timeline = history.filter(item => item.valid_from <= recovery.as_of).map((item) => `<article class="timeline-item"><div class="card-head"><strong>${escapeHtml(item.value)}</strong>${badge(item.memory_state || item.status)}</div>
      <p class="metadata">${escapeHtml(item.valid_from)}${item.valid_to ? ` → ${escapeHtml(item.valid_to)}` : uiText(" → open-ended", " → 持续有效")}</p>
      <details><summary>${uiText("Approval details", "审批详情")}</summary><p>${uiText("Decision", "决策")}: ${escapeHtml(item.decision_id)} · ${uiText("Receipt", "审批记录")}: ${escapeHtml(item.authorization?.receipt_id || uiText("Unavailable", "不可用"))} · ${escapeHtml(item.authorization?.authorized_at || uiText("Unknown", "未知"))}</p></details>${item.revalidated_by ? `<p>${uiText("Revalidated", "复核批准于")} ${escapeHtml(item.revalidated_by.authorized_at)} · ${escapeHtml(item.revalidated_by.receipt_id)}</p>` : ""}${evidenceRows(item.evidence)}</article>`).join("");
    const transitions = recovery.transitions.map((item) => `<p>${escapeHtml(item.from_value)} → ${escapeHtml(item.to_value)} on ${escapeHtml(item.effective_at)}.
      ${uiText("Replacement reason", "替代原因")}: ${item.reason_status === "NOT_SOURCE_VERIFIED" ? uiText("Needs source evidence", "待补充原文证据") : escapeHtml(item.reason_status)}.</p>`).join("");
    const proposals = recovery.proposals.map((item) => `<article class="timeline-item"><strong>${escapeHtml(item.value)}</strong> ${badge(item.status)}
      <p class="hint">${uiText("Proposal only; no formal review receipt.", "未批准提案")}</p>${evidenceRows([item.evidence])}</article>`).join("");
    page.innerHTML = `${heading(`${decision.subject} · ${decision.relation}`)}
      <section class="panel"><form id="timeline-as-of" class="form-stack" data-id="${escapeHtml(decisionId)}"><div class="field"><label for="timeline-date">${uiText("Decision valid on", "查询日期")}</label>
        <input type="date" id="timeline-date" name="as_of" value="${escapeHtml(asOf)}"></div><button class="button" type="submit">${uiText("Show reviewed state", "查看已审核记录")}</button></form>
        <p class="hint">${uiText("Historical results use approvals recorded today, which may have been added after the selected date.", "历史查询使用目前已记录的审批信息，其中可能包含所选日期之后补录的审批。")}</p>
        <p>${uiText("Selected date", "所选日期")}: ${escapeHtml(recovery.as_of.slice(0, 10))} · ${escapeHtml(statusLabel(recovery.status))}</p>
        <h2>${recovery.effective ? escapeHtml(recovery.effective.value) : uiText("No verified effective decision for this date", "该日期的有效决策待核实")}</h2></section>
      <section class="panel"><h2>${uiText("Decision timeline", "决策时间线")}</h2><div class="timeline">${timeline || uiText("No reviewed decision was effective by this date.", "该日期暂无已审核的生效决策。")}</div>
        <h3>${uiText("Formal replacements", "已批准的替代关系")}</h3>${transitions || uiText("No verified replacement by this date.", "该日期暂无已核实的替代关系。")}
        <h3>${uiText("Unapproved proposals", "未批准提案")}</h3>${proposals || uiText("No unapproved proposal recorded by this date.", "该日期暂无未批准提案。")}</section>
      <section class="panel"><h2>${uiText("Ask about this decision", "询问此决策")}</h2>
        <button class="button" data-action="ask-decision" data-subject="${escapeHtml(decision.subject)}"
          data-relation="${escapeHtml(decision.relation)}" data-as-of="${escapeHtml(asOf)}">${uiText("Open Ask with this scope", "询问此决策")}</button></section>
      <p><button class="button quiet" data-action="back-decisions">${uiText("Back to Decision Memory", "返回决策记忆")}</button></p>`;
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
      document.getElementById("provider-state").textContent = uiText("AI investigation ready · authorize each question.", "AI 调查已就绪 · 每次提问需授权");
    }
    const selected = state.askScopes.findIndex((slot) => slot.subject === state.askScope?.subject && slot.relation === state.askScope?.relation);
    state.askCandidates = [];
    state.askClarification = null;
    state.askResult = null;
    page.innerHTML = `<header class="page-heading ask-heading"><div><h1>${askLabel("Ask", "提问")}</h1></div>
      <div class="field ask-language"><label for="ask-language">${askLabel("Answer language", "回答语言")}</label>
      <select id="ask-language"><option value="auto" ${state.askLanguage === "auto" ? "selected" : ""}>Auto / 跟随问题</option><option value="zh" ${state.askLanguage === "zh" ? "selected" : ""}>中文</option><option value="en" ${state.askLanguage === "en" ? "selected" : ""}>English</option></select></div></header>
      <section class="panel ask-composer"><p class="metadata">${askLabel("Workspace", "项目空间")} <strong>${escapeHtml(state.workspace.name)}</strong></p>
      <form id="ask-form" class="form-stack">
        <div class="field"><label for="ask-query">${askLabel("Your question", "你的问题")}</label><textarea id="ask-query" name="query" required maxlength="4000" placeholder="${escapeHtml(askCopy("What supplier are we currently using?", "我们现在采用哪家供应商？"))}"></textarea></div>
        <div class="field"><label for="ask-domain">${askLabel("Investigation scope (optional)", "调查领域（可选）")}</label><select id="ask-domain" name="investigation_domain">
          <option value="" data-ask-en="Choose automatically" data-ask-zh="自动选择">${askCopy("Choose automatically", "自动选择")}</option>
          <option value="tasks" data-ask-en="Tasks across the current workspace records" data-ask-zh="当前空间记录中的任务">${askCopy("Tasks across the current workspace records", "当前空间记录中的任务")}</option>
          <option value="source_relations" data-ask-en="Project → owner → direct leader" data-ask-zh="项目 → 负责人 → 直属领导">${askCopy("Project → owner → direct leader", "项目 → 负责人 → 直属领导")}</option></select>
          <span class="hint">${askLabel("Choose a scope if needed. Refresh after updating sources.", "需要时指定调查范围。更新来源后请刷新页面。")}</span></div>
        <div id="task-provider-status" role="status">${!config.agent?.task_investigation ? `<p>${askLabel("Task investigation requires AI setup.", "任务调查需先配置 AI。")}</p>` : ""}
          ${!config.agent?.task_investigation ? `<details><summary>${askLabel("AI setup details", "AI 配置详情")}</summary><p class="hint">${escapeHtml(config.agent?.task_provider?.setup || "--allow-external-provider --allow-task-investigation --task-max-requests 16 --task-max-input-tokens 500000 --task-max-output-tokens 100000 --task-max-cost-usd 2 --task-max-seconds 120 --task-request-timeout-seconds 30")}</p></details>` : ""}
          ${config.agent?.task_provider?.budgets ? `<p class="metadata">${askLabel("Per-question cost limit", "单次费用上限")}: USD ${config.agent.task_provider.budgets.max_cost_usd} · ${config.agent.task_provider.budgets.max_seconds}s</p><details><summary>${askLabel("Model and usage limits", "模型与用量限制")}</summary><p>${escapeHtml(config.agent.task_provider.model_id)} · ${config.agent.task_provider.budgets.max_requests} requests · ${config.agent.task_provider.budgets.max_input_tokens} input / ${config.agent.task_provider.budgets.max_output_tokens} output tokens</p></details>` : ""}
          <details><summary>${askLabel("Sources in this authorization", "本次授权的来源清单")} (${config.investigation_context?.source_count || 0})</summary><ul>${(config.investigation_context?.sources || []).map(s => `<li>${escapeHtml(s.name)} <small>${escapeHtml(s.version)}</small></li>`).join("")}</ul><p class="hint">${askLabel("Applies to these source versions for this question only.", "仅限本次问题及所列来源版本。")}</p></details></div>
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
    if (result.path === "TASK_INVESTIGATION" && ["PARTIAL", "UNKNOWN"].includes(result.status) && result.reviewer?.accepted === true && result.task_review) {
      if (result.agent_run_id && result.workspace_id === state.workspace?.workspace_id) {
        // Keep only a pointer in this tab. Every reopen goes through current
        // source, catalog, Ledger and Reviewer checks on the server.
        try { window.sessionStorage.setItem(`linkloom:last-task:${result.workspace_id}`, result.agent_run_id); }
        catch { /* Storage can be disabled; the current result remains usable. */ }
      }
      target.innerHTML = window.renderTaskAnswer(result, {
        escapeHtml, t, date: value => askDate(value, locale),
        citation: (item, index) => askEvidence(item, index, locale),
      });
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
      else if (["SOURCE_SNAPSHOT_CHANGED", "SOURCE_EVIDENCE_NOT_CURRENT", "TASK_SOURCE_AUTHORIZATION_CHANGED", "RELATION_OR_SOURCE_CHANGED"].includes(result.error?.code)) stopMessage = t("The sources, access or relationship evidence changed. Previous task details are hidden. Refresh the source list and investigate again.", "来源、访问权限或关系证据已变化，旧任务详情已隐藏。请刷新来源清单后重新调查。");
      target.innerHTML = `<section class="panel ask-answer ask-answer--notice" role="status"><h2>${unavailable ? t("Task investigation unavailable", "当前无法使用任务调查") : permission ? t("This investigation needs your permission", "这次调查需要你的授权") : t("Task investigation stopped", "任务调查已停止")}</h2><p>${stopMessage}</p><details><summary>${t("Reason and review", "停止原因与审查记录")}</summary><pre>${escapeHtml(JSON.stringify({code: result.error?.code, review_decision: result.reviewer?.decision}, null, 2))}</pre></details></section>`;
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
      target.innerHTML = `<section class="panel ask-answer" role="status"><h2>${t("Investigating the sources", "正在调查相关来源")}</h2><p class="loading">${t("Checking reviewed records and source evidence…", "正在查看可信记录，核验来源证据…")}</p>${technical}</section>`;
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
    const unknownText = unknown.map(field => field === "replacement_reason" ? t("The replacement reason needs supporting evidence.", "更换原因待补充证据。") : field === "effective_decision" ? t("No effective formal decision could be verified.", "未能核实该日期有效的正式决策。") : field === "investigation_conclusion" ? t("The investigation conclusion has not been verified.", "本次调查结论尚未核验。") : t("Some requested information is not supported by evidence.", "请求中的部分信息尚无可信证据。")).join(" ");
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
      return `<article class="ask-evidence"><h3>${escapeHtml(labels[predicate] || predicate)}</h3>${fact ? `<p>${escapeHtml(fact.subject)} → <strong>${escapeHtml(fact.value)}</strong>${fact.valid_on ? ` · ${escapeHtml(fact.valid_on)}` : ""}</p>` : `<p>${conflict ? t("No unique result; inspect the opposing sources below.", "没有唯一结果，请查看下方互相矛盾的原始来源。") : t("Evidence is missing for this relationship.", "这一关系待补充证据。")}</p>`}</article>`;
    }).join("");
    target.innerHTML = `<section class="panel ask-answer ${complete ? "" : "ask-answer--notice"}"><p class="ask-answer-status">${facts.length ? t("Source-supported relationship", "来源支持的关系") : t("Source investigation; no relationship verified", "来源调查，尚无关系得到核实")}</p>
      <h2 class="${complete ? "ask-answer-value" : "ask-answer-headline"}">${title}</h2>
      <p>${complete ? t("The cited sources support this relationship.", "所引原文支持这一关系。") : conflict ? t("The known upstream facts are retained. Opposing sources prevent a unique downstream conclusion.", "已确认的上游关系予以保留；下游来源互相矛盾，不能选择唯一结论。") : t("The known upstream facts are retained. The requested final relationship remains unknown.", "已确认的上游关系予以保留；尚未找到足够证据确认最终关系。")}</p>
      <p class="hint">${t("Source-supported relationship · business approval pending.", "来源支持的关系 · 业务审批待确认。")}</p>

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
    page.innerHTML = `<p class="loading">${uiText("Switching workspace…", "正在切换工作区…")}</p>`;
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
    page.innerHTML = `${heading(uiText("Create a workspace", "创建工作区"))}
      <form id="workspace-create" class="panel form-stack"><div class="field"><label for="workspace-name">${uiText("Workspace name", "工作区名称")}</label><input id="workspace-name" name="name" required maxlength="120"></div><button class="button" type="submit">${uiText("Create workspace", "创建工作区")}</button><button class="button quiet" type="button" data-action="back-sources">${uiText("Cancel", "取消")}</button></form>`;
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
      announce(uiText("Source files must be 256 KB or smaller.", "来源文件不得超过 256 KB。"));
      event.target.value = "";
      return;
    }
    try {
      document.getElementById("source-name").value = file.name;
      document.getElementById("source-content").value = await file.text();
      announce(uiText(`Loaded ${file.name}. Review the text before importing.`, `已读取 ${file.name}，请核对内容后导入。`));
    } catch {
      announce(uiText("LinkLoom could not read this file. Paste the timestamped text instead.", "文件读取失败，请直接粘贴带时间戳的原文。"));
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
          ? uiText("Source ingestion started.", "正在提取来源。")
          : uiText("Source saved; set up AI to extract decisions.", "来源已保存，配置 AI 后即可提取决策。"));
        await inspectSource(result.source.source_id);
        pollSource(result.source.source_id, true);
      } else if (form.id === "source-update") {
        const result = await send("PUT", `/api/sources/${encodeURIComponent(form.dataset.id)}`, Object.fromEntries(new FormData(form)));
        announce(result.changed ? uiText("Extracting the updated source…", "正在提取更新后的来源…") : uiText("No source changes to save.", "来源内容未变化。"));
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
        announce(action === "approve" ? uiText("Decision approved and saved.", "决策已批准并保存。") : uiText("Candidate rejected.", "候选已拒绝。"));
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
    } catch (error) { renderError(error, `<p><button class="button quiet" data-action="reload">${uiText("Refresh", "刷新")}</button></p>`); }
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
        if (runId) {
          invalidateAsk();
          await pollAsk(runId, workspaceId, state.agentViewToken);
        }
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
        announce(uiText("Revalidation approval saved.", "已保存复核批准结果。"));
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
        announce(uiText("Retrying failed extraction.", "正在重试失败片段。"));
        await loadSources();
        pollSource(id);
      } else if (action === "delete-source") {
        if (!window.confirm(uiText("Remove this source from the workspace? Its captured versions remain available for provenance.", "确定从工作区移除此来源？已保存的版本将保留用于追溯。"))) return;
        await request(`/api/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
        announce(uiText("Source removed; affected decisions were reconciled.", "来源已移除，相关决策已重新核验。"));
        await loadSources();
      }
    } catch (error) { renderError(error); }
  });

  document.getElementById("interface-language")?.addEventListener("change", async event => {
    state.language = event.target.value === "zh" ? "zh-CN" : "en-US";
    try { window.localStorage.setItem("linkloom.locale", state.language); } catch { /* Optional. */ }
    localizeShell();
    if (state.screen === "ask" && document.getElementById("ask-form")) {
      state.askLanguage = event.target.value;
      document.getElementById("ask-language").value = state.askLanguage;
      localizeAsk();
      if (state.askResult) renderAskResult(state.askResult, state.agentViewToken);
      return;
    }
    const inputs = [...page.querySelectorAll("input:not([type=file]), textarea")]
      .map(el => [el.id, el.value]);
    const sourceId = state.sourceId, candidateId = state.candidateId,
      decisionId = state.decisionId, revalidation = state.revalidation;
    try {
      if (page.querySelector("#workspace-create")) {
        document.getElementById("new-workspace").click();
      } else if (state.detailView === "source") await inspectSource(sourceId);
      else if (state.detailView === "candidate") await inspectCandidate(candidateId, revalidation);
      else if (state.detailView === "revalidation") await inspectRevalidation(revalidation.original.decision_id);
      else if (state.detailView === "decision") await inspectDecision(decisionId, document.getElementById("timeline-date")?.value || "");
      else await loadScreen();
      for (const [id, value] of inputs) {
        const el = document.getElementById(id); if (el) el.value = value;
      }
      updateWorkspacePicker();
    } catch (error) { renderError(error); }
  });

  async function bootstrap() {
    try {
      await refreshWorkspaceState();
      if (state.workspace) await loadScreen("sources");
      else await showNoWorkspace();
    } catch (error) { renderError(error); }
  }
  localizeShell();
  bootstrap();
})();
