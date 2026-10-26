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
    creatingWorkspace: false,
    agentViewToken: 0,
    askScopes: [],
    askScope: null,
    askCandidates: [],
    askClarification: null,
    askLanguage: "auto",
    askResult: null,
    askLocaleRun: null,
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
      const error = new Error("Local LinkLoom service could not be reached.");
      error.code = "LOCAL_SERVICE_UNAVAILABLE";
      throw error;
    }
    let payload;
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload?.error?.message || copy("The operation could not be completed.", "操作暂时无法完成。"));
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

  function initialLanguage() {
    let requested, saved;
    try { requested = new URLSearchParams(window.location.search).get("lang"); } catch { /* Optional. */ }
    try { saved = window.localStorage.getItem("linkloom.locale"); } catch { /* Optional. */ }
    return String(requested || saved || document.documentElement.lang).startsWith("zh") ? "zh-CN" : "en-US";
  }
  state.language = initialLanguage();

  function claimTypeLabel(value) {
    const labels = {DECISION: ["Decision candidate", "决策候选"], PROPOSAL: ["Proposal", "提案"],
      FACT: ["Source statement", "原文陈述"], QUESTION: ["Question", "问题"],
      PLAN: ["Plan", "计划"], REJECTED_OPTION: ["Rejected option", "已否决方案"],
      OPINION: ["Opinion", "观点"], SPECULATION: ["Speculation", "推测"],
      ACTION: ["Action", "行动项"], TASK: ["Task", "任务"], REPLACEMENT: ["Replacement", "替代决策"]};
    return copy(...(Object.hasOwn(labels, value) ? labels[value] : ["Candidate", "候选"]));
  }

  function userError(error) {
    const messages = {
      LOCAL_SERVICE_UNAVAILABLE: ["Check that the local LinkLoom service is running, then try again.", "请检查本地 LinkLoom 服务是否正在运行，然后重试。"],
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
      SOURCE_BUSY: ["Source processing is in progress. Wait for it to finish, then try again.", "来源正在处理中，请等待完成后再操作。"],
      NO_RETRYABLE_SEGMENT: ["No failed segment has a retry remaining. Check the source and extraction details.", "暂无可重试片段，请核对来源和提取详情。"],
      ATTEMPT_LIMIT: ["The retry limit was reached. Check the extraction details before importing again.", "重试次数已用完，请检查提取详情后重新导入。"],
      INGESTION_FAILED: ["Extraction failed. Check AI setup and source timestamps before retrying.", "提取失败，请检查 AI 配置和来源时间戳后重试。"],
      INVALID_AS_OF: ["Choose a valid historical date.", "请选择有效的历史日期。"],
      INVALID_REVIEW_RESOLUTION: ["Check the review fields against the source evidence.", "请根据原文核对审核字段。"],
      INVALID_SOURCE_CONTENT: ["Enter source text with one timestamped message per line.", "请填写来源文本，每条消息独占一行并包含时间戳。"],
      INVALID_SOURCE_NAME: ["Use a file name without directory separators.", "请使用文件名，不包含目录分隔符。"],
      INVALID_VALID_TIME: ["Enter a date or a timestamp with a timezone.", "请输入日期或包含时区的时间戳。"],
      PROVIDER_UNAVAILABLE: ["AI extraction is unavailable. Enable it before extracting source text.", "AI 提取未启用，请配置后再提取来源内容。"],
      SOURCE_DELETED: ["This source was removed. Import it again to restore it.", "此来源已移除，可重新导入。"],
      SOURCE_PARSE_FAILED: ["Use timestamped lines: [2026-10-08T10:00:00Z] Speaker: message.", "请使用带时间戳的格式：[2026-10-08T10:00:00Z] 发言人：内容。"],
      SOURCE_VERSION_MISMATCH: ["The source changed. Refresh it before continuing.", "来源已变化，请刷新后继续。"],
      SOURCE_VERSION_NOT_FOUND: ["The current source version is unavailable. Check Sources.", "当前来源版本不可用，请检查来源列表。"],
      WORKSPACE_MISMATCH: ["The workspace changed. Select the intended workspace and try again.", "工作区已变化，请选择目标工作区后重试。"],
      WORKSPACE_NOT_FOUND: ["Choose an existing workspace.", "请选择已有工作区。"],
      NOT_FOUND: ["This record is unavailable. Refresh the page.", "此记录不可用，请刷新页面。"],
      INVALID_REQUEST: ["Check the entered fields and try again.", "请检查填写内容后重试。"],
    };
    if (Object.hasOwn(messages, error.code)) return copy(...messages[error.code]);
    if ([401, 403].includes(error.status)) return copy("Access unavailable. Check workspace and source permissions.", "访问不可用，请核对工作区和来源权限。");
    return copy("This operation could not finish. Refresh the page or inspect the error details.", "操作未能完成，请刷新页面或查看错误详情。");
  }

  function copy(en, zh) { return state.language.startsWith("zh") ? zh : en; }

  function relationLabel(value) {
    const labels = {"uses supplier": ["Supplier", "供应商"], "uses vendor": ["Supplier", "供应商"],
      "has budget": ["Budget", "预算"]};
    const key = String(value || "").toLowerCase();
    return Object.hasOwn(labels, key) ? copy(...labels[key]) : value;
  }

  function statusLabel(value) {
    const labels = {
      UNKNOWN: ["Unconfirmed", "待确认"], PARTIAL: ["Partial results", "部分结果"],
      BLOCKED: ["Blocked", "暂无法继续"], CONFLICT: ["Conflicting evidence", "证据冲突"],
      REFUTED: ["Counter-evidence found", "存在否定证据"], SUPPORTED: ["Evidence supports", "证据支持"],
      MODEL_PROPOSED: ["Model suggestion", "模型建议"], EVIDENCE_VALID: ["Citations checked", "引用已核验"],
      SAVED: ["Saved · extraction pending", "已保存 · 待提取"],
      PROPOSED_NOT_APPROVED: ["Unapproved proposal", "未批准提案"],
      INGESTING: ["Processing", "处理中"], COMPLETE: ["Processed", "已处理"], COMPLETED: ["Complete", "已完成"],
      FAILED: ["Extraction failed", "提取失败"], EXTRACTION_UNAVAILABLE: ["Extraction unavailable", "提取未启用"],
      PENDING_REVIEW: ["Pending review", "待审核"], APPROVED: ["Approved", "已批准"], REJECTED: ["Rejected", "已拒绝"],
      SUPPORTING_ONLY: ["Supporting proposal", "参考提案"], ACTIVE: ["Active", "有效"],
      STALE: ["Source changed", "来源已变化"], NEEDS_REVALIDATION: ["Needs review", "需复核"],
      INVALIDATED: ["Invalidated", "已失效"], SUPERSEDED: ["Replaced", "已替代"],
      DELETED: ["Removed", "已移除"], REMOVED: ["Removed", "已移除"], VERIFIED: ["Quote verified", "原文已核验"],
      DECISION: ["Decision candidate", "决策候选"], FACT: ["Fact candidate", "事实候选"],
      PROPOSAL: ["Proposal", "提案"], UNRESOLVED: ["Unresolved", "待处理"], RESOLVED: ["Resolved", "已处理"],
      READ_ONLY: ["Read-only", "只读"], FOUND: ["Reviewed record found", "已找到审核记录"],
      CURRENT: ["Current record", "当前记录"], CANDIDATE: ["Candidate", "候选"],
      AUTHORIZATION_OR_SOURCE_UNVERIFIED: ["Authorization or source needs verification", "授权或来源待核验"],
      SOURCE_CONFLICT_UNREVIEWED: ["Source conflict needs review", "来源冲突待审核"],
      SUBJECT_NOT_FOUND: ["No matching subject found", "暂未找到匹配对象"],
      RELATION_NOT_FOUND: ["No matching relationship found", "暂未找到匹配关系"],
      NO_VALID_RECORD_AT_TIME: ["No valid record found for this date", "暂未找到该日期的有效记录"],
    };
    return labels[value] ? copy(...labels[value]) : copy("Status unavailable", "状态暂不可用");
  }

  function reviewReasons(reasons) {
    const labels = {
      RELATION_UNRESOLVED: copy("Relationship needs clarification", "关系需澄清"),
      ENTITY_UNRESOLVED: copy("Subject needs clarification", "对象需澄清"),
      TEMPORAL_UNRESOLVED: copy("Effective date needs confirmation", "生效日期待确认"),
      VALUE_UNRESOLVED: copy("Value needs confirmation", "内容待确认"),
      CONFLICT_REQUIRES_REVIEW: copy("Conflicting evidence needs review", "证据冲突需审核"),
    };
    return [...new Set((reasons || []).map(reason => labels[reason] || copy("Review the source evidence", "请审核来源证据")))].join(" · ") || copy("Human review required", "需人工审核");
  }

  function badge(value, displayValue = value) {
    const status = String(value || "UNKNOWN").toUpperCase();
    const visualStatus = String(displayValue || status).toUpperCase();
    const kind = ["FAILED", "INVALIDATED"].includes(visualStatus) ? "error"
      : ["STALE", "NEEDS_REVALIDATION", "INGESTING", "PENDING_REVIEW", "CONFLICT", "PARTIAL", "AUTHORIZATION_OR_SOURCE_UNVERIFIED", "SOURCE_CONFLICT_UNREVIEWED"].includes(status) ? "warning"
        : ["SUPERSEDED", "DELETED", "REMOVED", "SUPPORTING_ONLY", "UNKNOWN", "BLOCKED", "EXTRACTION_UNAVAILABLE"].includes(status) ? "neutral" : "";
    return `<span class="badge ${kind}" data-status="${escapeHtml(status)}">${escapeHtml(statusLabel(visualStatus))}</span>`;
  }

  function heading(title, action = "") {
    return `<header class="page-heading"><h1>${escapeHtml(title)}</h1>${action}</header>`;
  }

  function renderError(error, retry = "") {
    page.innerHTML = `${heading(copy("This page could not load", "页面暂时无法加载"))}
      <section class="error-box" role="alert"><strong>${escapeHtml(userError(error))}</strong>
      ${error.code || error.message ? `<details><summary>${copy("Error details", "错误详情")}</summary><p>${escapeHtml(error.code || "")}</p><p>${escapeHtml(error.message || "")}</p></details>` : ""}${retry}</section>`;
    announce(userError(error));
  }

  function updateWorkspacePicker() {
    workspacePicker.innerHTML = `<option value="">${copy("Select workspace", "选择工作区")}</option>${state.workspaces.map((workspace) =>
      `<option value="${escapeHtml(workspace.workspace_id)}" ${workspace.workspace_id === state.workspace?.workspace_id ? "selected" : ""}>${escapeHtml(workspace.name)}</option>`
    ).join("")}`;
    workspacePicker.disabled = state.workspaces.length === 0;
    document.getElementById("provider-state").textContent = state.provider?.ready
      ? copy("AI extraction enabled", "AI 提取已启用") : copy("Local source storage · AI extraction off", "本地保存来源 · AI 提取未启用");
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
    state.creatingWorkspace = true;
    page.innerHTML = `${heading(copy("Create a workspace", "创建工作区"))}
      <form id="workspace-create" class="panel form-stack" aria-label="Create workspace">
        <div class="field"><label for="workspace-name">${copy("Workspace name", "工作区名称")}</label><input id="workspace-name" name="name" maxlength="120" required placeholder="Product launch"></div>
        <button class="button" type="submit">${copy("Create workspace", "创建工作区")}</button>
        ${state.workspace ? `<button class="button quiet" type="button" data-action="back-sources">${copy("Cancel", "取消")}</button>` : ""}
      </form>`;
  }

  async function loadScreen(screen = state.screen) {
    state.askLocaleRun = null;
    const viewToken = ++state.agentViewToken;
    setNavigation(screen);
    state.creatingWorkspace = false;
    state.sourceId = null;
    state.candidateId = null;
    state.revalidation = null;
    state.decisionId = null;
    if (!state.workspace) { await showNoWorkspace(); return; }
    page.innerHTML = `<section class="state-card"><p class="loading">${copy("Loading…", "正在加载…")}</p></section>`;
    try {
      if (screen === "sources") await loadSources();
      else if (screen === "review") await loadReview();
      else if (screen === "decisions") await loadDecisions();
      else await showAsk();
    } catch (error) {
      if (viewToken === state.agentViewToken) renderError(error, `<p><button class="button quiet" data-action="reload">${copy("Try again", "重试")}</button></p>`);
    }
  }

  async function loadSources() {
    const { sources } = await request("/api/sources");
    const providerReady = Boolean(state.provider?.ready);
    const list = sources.length ? sources.map((source) => `
      <article class="card" data-source-card="${escapeHtml(source.source_id)}">
        <div class="card-head"><div><h3>${escapeHtml(source.name)}</h3><p class="status-line">${copy("Updated", "更新于")} ${askDate(source.updated_at, state.language.startsWith("zh") ? "zh" : "en")}</p></div>${badge(source.ingestion_status, source.error?.startsWith("PROVIDER_UNAVAILABLE:") ? "SAVED" : source.ingestion_status)}</div>
        <div class="metadata"><span>${copy("Candidates", "候选")} <strong>${source.candidate_count}</strong></span><span>${copy("Review required", "待审核")} <strong>${source.review_required_count}</strong></span>${source.segment_summary.failed ? `<span>${copy("Extraction failed", "提取失败")} <strong>${source.segment_summary.failed}</strong></span>` : ""}</div>
        ${source.error ? `<p class="review-guidance">${source.error.startsWith("PROVIDER_UNAVAILABLE:") ? copy("Source saved · AI extraction is off", "来源已保存 · AI 提取未启用") : copy("Extraction failed. Open the source for details and retry options.", "提取失败，请打开来源查看原因和重试选项。")}</p>` : ""}
        <div class="actions"><button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(source.source_id)}">${copy("Inspect source", "查看来源")}</button>${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">${copy("Retry failed segment", "重试失败片段")}</button>` : ""}</div>
      </article>`).join("") : `<section class="empty"><h2>${copy("No sources yet", "暂无来源")}</h2><p>${copy("Import a meeting note or decision record to get started.", "导入会议纪要或决策记录，开始整理项目资料。")}</p></section>`;
    page.innerHTML = `${heading(copy("Sources", "来源"))}
      <div class="split"><section><p class="metadata">${sources.length} ${copy("sources", "个来源")}</p>${list}</section>
      <section class="panel"><h2>${copy("Add a source", "添加来源")}</h2><p class="hint">${copy("One message per line:", "每条消息独占一行：")} <code>[2026-10-08T10:00:00Z] ${copy("Speaker: message", "发言人：内容")}</code></p>
        ${providerReady ? `<p class="review-guidance">${copy("Importing sends source text to the configured external AI provider and may incur model costs.", "导入会将来源文本发送给已配置的外部 AI，可能产生模型费用。")}</p>` : `<div class="notice"><p>${copy("Save sources locally. Enable AI extraction to generate review candidates.", "可先在本地保存来源，启用 AI 提取后生成待审核候选。")}</p><details><summary>${copy("Extraction setup", "提取设置")}</summary><p>${escapeHtml(state.provider?.message || copy("Enable a provider to extract source text.", "请启用 AI 服务以提取来源内容。"))}</p></details></div>`}
        <form id="source-create" class="form-stack"><div class="field"><label for="source-file">${copy("Choose a Markdown or text artifact", "选择 Markdown 或文本文件")}</label><input id="source-file" type="file" accept=".md,.txt,text/markdown,text/plain"><span class="hint">${copy("Markdown or text, up to 256 KB.", "支持 Markdown 或文本，最大 256 KB。")}</span></div>
        <div class="field"><label for="source-name">${copy("Artifact name", "来源名称")}</label><input id="source-name" name="name" maxlength="180" required placeholder="meeting-2026-10-08.md"></div>
        <div class="field"><label for="source-content">${copy("Timestamped source text", "带时间戳的来源文本")}</label><textarea class="source-input" id="source-content" name="content" required></textarea></div>
        <button class="button" type="submit">${providerReady ? copy("Import and extract", "导入并提取") : copy("Save source", "保存来源")}</button></form></section></div>`;
  }

  async function inspectSource(sourceId) {
    const viewToken = ++state.agentViewToken;
    const workspaceId = state.workspace?.workspace_id;
    const { source } = await request(`/api/sources/${encodeURIComponent(sourceId)}`);
    if (viewToken !== state.agentViewToken || workspaceId !== state.workspace?.workspace_id) return;
    state.candidateId = null; state.revalidation = null; state.decisionId = null;
    const providerReady = Boolean(state.provider?.ready);
    state.sourceId = sourceId;
    const extractionUnavailable = source.error?.startsWith("PROVIDER_UNAVAILABLE:");
    const evidence = source.evidence.length ? source.evidence.map((item) => `<div class="card"><div class="metadata"><span>${copy("Lines", "行号")} ${item.line_start}–${item.line_end}</span><span>${badge("VERIFIED")}</span></div><blockquote class="quote">${escapeHtml(item.quote)}</blockquote></div>`).join("") : `<p class="empty">${copy("No timestamped evidence spans were parsed.", "暂未解析到带时间戳的证据。")}</p>`;
    page.innerHTML = `${heading(source.name)}
      <div class="detail-layout"><section class="panel"><div class="card-head"><h2>${copy("Ingestion", "处理状态")}</h2>${badge(source.ingestion_status, source.error?.startsWith("PROVIDER_UNAVAILABLE:") ? "SAVED" : source.ingestion_status)}</div>${extractionUnavailable ? `<p class="review-guidance">${copy("Source saved locally. Enable AI extraction, then import the original source again to extract candidates.", "来源已在本地保存。启用 AI 提取后，重新导入原始来源以提取候选。")}</p>` : ""}<p>${source.candidate_count} ${copy("candidates", "个候选")} · ${source.review_required_count} ${copy("need review", "个待审核")} · ${source.segment_summary.failed} ${copy("extraction failures", "个提取失败片段")}</p>${source.error ? `<details class="source-diagnostics"><summary>${copy("Extraction details", "提取详情")}</summary><p>${escapeHtml(source.error)}</p></details>` : ""}${source.segments.length ? `<details><summary>${copy("Processing details", "处理详情")}</summary>${source.segments.map((segment) => `<p class="metadata">${copy("Segment", "片段")} ${segment.ordinal + 1}: ${escapeHtml(segment.state)} · ${segment.attempt_count} ${copy("attempt(s)", "次尝试")}</p>`).join("")}</details>` : ""}${source.segment_summary.retryable ? `<button class="button quiet" data-action="retry-source" data-id="${escapeHtml(source.source_id)}">${copy("Retry failed segment", "重试失败片段")}</button>` : ""}
        <h3>${copy("Exact evidence spans", "原文证据")}</h3>${evidence}<details><summary>${copy("Source version details", "来源版本详情")}</summary><p class="metadata">${copy("Version:", "版本：")} ${escapeHtml(source.source_version)} · ${copy("Content SHA-256:", "内容 SHA-256：")} ${escapeHtml(source.content_sha256)}</p></details></section>
      <section class="panel"><h2>${copy("Update source", "更新来源")}</h2><p class="hint">${copy("Changes create a new version and recheck affected decisions.", "修改将保存为新版本，并重新核查受影响的决策。")}${providerReady ? copy(" New text is sent to the configured external AI provider and may incur costs.", " 新文本将发送至已配置的外部 AI，可能产生费用。") : copy(" AI extraction is off.", " AI 提取未启用。")}</p>
        <form id="source-update" class="form-stack" data-id="${escapeHtml(sourceId)}"><div class="field"><label for="update-name">${copy("Artifact name", "来源名称")}</label><input id="update-name" name="name" value="${escapeHtml(source.name)}" required></div>
        <div class="field"><label for="update-content">${copy("Timestamped source text", "带时间戳的来源文本")}</label><textarea class="source-input" id="update-content" name="content" required>${escapeHtml(source.content)}</textarea></div>
        <button class="button" type="submit">${copy("Save new source version", "保存新版本")}</button><button class="button danger" type="button" data-action="delete-source" data-id="${escapeHtml(sourceId)}">${copy("Remove from workspace", "从工作区移除")}</button></form></section></div>
      <p><button class="button quiet" data-action="back-sources">${copy("Back to sources", "返回来源")}</button></p>`;
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
      <article class="card"><div class="card-head"><div><p class="record-kind">${escapeHtml(claimTypeLabel(candidate.claim_type))}</p><h3>${escapeHtml(candidate.proposed_value || copy("Needs review", "待审核"))}</h3></div>${badge(candidate.status)}</div>
        <div class="metadata"><span>${copy("Subject", "对象")} <strong>${escapeHtml(candidate.subject || copy("Needs review", "待审核"))}</strong></span><span>${copy("Relation", "关系")} <strong>${escapeHtml(relationLabel(candidate.relation) || copy("Needs review", "待审核"))}</strong></span><span>${copy("Source", "来源")} <strong>${escapeHtml(candidate.source_name)}</strong></span><span>${copy("Effective", "生效日期")} <strong>${escapeHtml(candidate.valid_from || copy("Not resolved", "待确认"))}</strong></span></div>
        <p class="hint">${escapeHtml(reviewReasons(candidate.review_reasons))}</p><button class="button quiet" data-action="inspect-candidate" data-id="${escapeHtml(candidate.candidate_id)}">${copy("Review candidate", "审核候选")}</button></article>`).join("") : `<section class="empty"><h2>${copy("No candidates need review", "暂无待审核候选")}</h2><p>${copy("Import a source to generate candidates for review.", "导入来源后，可在此审核提取的候选。")}</p></section>`;
    const proposalItems = proposals.length ? proposals.map((candidate) => `
      <article class="card"><div class="card-head"><div><h3>${escapeHtml(candidate.subject || candidate.proposed_value || copy("Unresolved proposal", "待确认提案"))}</h3></div>${badge("SUPPORTING_ONLY")}</div>
        <p class="decision-value">${escapeHtml(candidate.proposed_value || copy("Value not resolved", "内容待确认"))}</p><p class="metadata">${escapeHtml(candidate.source_name)} · ${copy("lines", "行号")} ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="hint">${copy("Unapproved · kept as reference material", "未批准 · 作为参考材料保留")}</p></article>`).join("") : `<p class="muted">${copy("No proposals are currently held as supporting evidence.", "暂无参考提案。")}</p>`;
    const attentionCards = attention.decisions.length ? attention.decisions.map((decision) => `<article class="card"><div class="card-head"><div><h3>${escapeHtml(decision.subject)} · ${escapeHtml(relationLabel(decision.relation))}</h3><p>${escapeHtml(decision.value)}</p></div>${badge(decision.memory_state)}</div><p class="hint">${copy("Source evidence changed or is unavailable. Revalidate this decision against a current source before relying on it.", "来源证据已变化或不可用，请结合当前来源重新审核。")}</p><button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(decision.decision_id)}">${copy("Revalidate decision", "复核决策")}</button></article>`).join("") : `<p class="muted">${copy("No Decision Memory records currently need revalidation.", "暂无需要复核的决策。")}</p>`;
    page.innerHTML = `${heading(copy("Review", "审核"))}
      <div class="section-title"><h2>${copy("Pending candidates", "待审核候选")}</h2><span class="muted">${candidates.length}</span></div>${items}
      <div class="section-title"><h2>${copy("Supporting proposals", "参考提案")}</h2><span class="muted">${proposals.length} · ${copy("unapproved", "未批准")}</span></div>${proposalItems}
      <div class="section-title"><h2>${copy("Decision records needing attention", "需复核的决策")}</h2></div>${attentionCards}`;
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
    state.sourceId = null; state.decisionId = null;
    state.revalidation = revalidation;
    state.candidateId = candidateId;
    const unresolved = candidate.current_unresolved_fields;
    const relationUnresolved = unresolved.includes("relation");
    const relationGuidance = relationUnresolved ? `<div class="review-guidance" role="note" id="relation-guidance"><strong>${copy("Relationship unresolved · approval blocked", "关系待确认 · 暂无法批准")}</strong><p>${copy("Extracted phrase:", "提取的关系表述：")} ${escapeHtml(candidate.relation_phrase || copy("Not present", "未提供"))}</p><p>${copy("Check the original source. With AI extraction enabled, import the original source again to request a fresh extraction. This form can resolve only subject and effective date.", "请核对原文；启用 AI 提取后可重新导入原始来源。此表单仅可补充对象和生效日期。")}</p><p>${copy("Keep it pending, or reject it with your name and reason.", "可保留待处理，或填写审核人和理由后拒绝。")}</p><button class="button quiet" type="button" data-action="inspect-source" data-id="${escapeHtml(candidate.source_id)}">${copy("Open original source", "打开原始来源")}</button></div>` : "";
    const resolutionFields = unresolved.length ? `<section class="panel"><h3>${copy("Resolve fields from the evidence", "根据原文补充字段")}</h3><p class="hint">${copy("Fill the subject or effective date only when the source supports it. Leave uncertain fields empty.", "仅根据原文补充对象或生效日期；不确定的字段请留空。")}</p>
      ${unresolved.includes("subject") ? `<div class="field"><label for="review-subject">${copy("Subject", "对象")}</label><input id="review-subject" name="subject" value="${escapeHtml(candidate.subject || "")}" required></div>` : ""}
      ${relationGuidance}
      ${unresolved.includes("valid_from") ? `<div class="field"><label for="review-valid-from">${copy("Effective date or timestamp", "生效日期或时间")}</label><input id="review-valid-from" name="valid_from" placeholder="${copy("YYYY-MM-DD or ISO 8601 with timezone", "YYYY-MM-DD 或含时区的 ISO 8601 时间")}" required></div>` : ""}</section>` : `<p class="hint">${copy("Subject, relationship and effective date are available for review.", "请核对对象、关系和生效日期。")}</p>`;
    page.innerHTML = `${heading(candidate.proposed_value || copy("Review candidate", "审核候选"), `<div class="actions"><span class="badge neutral">${escapeHtml(claimTypeLabel(candidate.claim_type))}</span>${badge(candidate.status)}</div>`)}
      <div class="detail-layout"><section class="panel"><div class="metadata"><span>${copy("Subject", "对象")} <strong>${escapeHtml(candidate.subject || copy("Needs review", "待审核"))}</strong></span><span>${copy("Relation", "关系")} <strong>${escapeHtml(relationLabel(candidate.relation) || copy("Needs review", "待审核"))}</strong></span><span>${copy("Valid from", "生效日期")} <strong>${escapeHtml(candidate.valid_from || copy("Not resolved", "待确认"))}</strong></span></div>
        <p class="section-title"><strong>${copy("Review reasons", "审核原因")}</strong></p><p>${escapeHtml(reviewReasons(candidate.review_reasons))}</p>
        <h3>${copy("Exact supporting evidence", "支持原文")}</h3><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · ${copy("lines", "行号")} ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>
        <details><summary>${copy("Source version and provenance", "来源版本与出处")}</summary><p>${copy("Version:", "版本：")} ${escapeHtml(candidate.evidence.source_version)}</p><p>${copy("Evidence ref:", "证据编号：")} ${escapeHtml(candidate.evidence.evidence_ref)}</p></details></section>
      <form id="candidate-review" class="panel form-stack" data-id="${escapeHtml(candidateId)}">${revalidation ? `<div class="notice"><strong>${copy("New approval for a stale decision", "审核当前来源的新候选")}</strong><p>${copy("Original:", "原记录：")} ${escapeHtml(revalidation.original.value)}. ${copy("Approval applies to this candidate and its current evidence. The original approval stays in history.", "本次批准仅适用于此候选及当前证据，原批准保留在历史中。")}</p></div>` : ""}${resolutionFields}
        <div class="field"><label for="reviewer-id">${copy("Reviewer", "审核人")}</label><input id="reviewer-id" name="reviewer_id" required maxlength="120" placeholder="${copy("Your name", "你的姓名")}"></div>
        <div class="field"><label for="review-reason">${copy("Review reason", "审核理由")}</label><textarea id="review-reason" name="reason" required maxlength="1000" placeholder="${copy("Why is this evidence sufficient?", "请填写审核依据")}"></textarea></div>
        <div class="actions"><button class="button" type="submit" data-review-action="approve" ${relationUnresolved ? 'disabled aria-describedby="relation-guidance"' : ""}>${copy("Approve and save", "批准并保存")}</button><button class="button danger" type="submit" data-review-action="reject" formnovalidate>${copy("Reject", "拒绝")}</button></div></form></div>
      <p>${revalidation ? `<button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(revalidation.original.decision_id)}">${copy("Keep unresolved / back to comparison", "保留待处理并返回对照")}</button>` : `<button class="button quiet" data-action="back-review">${copy("Back to Review", "返回审核")}</button>`}</p>`;
  }

  async function inspectRevalidation(decisionId) {
    const viewToken = ++state.agentViewToken;
    const { revalidation: view } = await request(`/api/review/revalidation/${encodeURIComponent(decisionId)}`);
    if (viewToken !== state.agentViewToken) return;
    state.sourceId = null; state.candidateId = null; state.decisionId = null;
    state.revalidation = view;
    const original = view.original;
    const reasonLabels = {
      ORIGINAL_EVIDENCE_UNAVAILABLE: copy("The exact historical evidence cannot be verified.", "历史证据暂无法核验。"),
      SOURCE_MISSING: copy("The original source record is unavailable.", "原始来源记录不可用。"),
      SOURCE_REMOVED: copy("The source was removed from the active workspace. Historical evidence is retained for audit only.", "来源已从当前工作区移除；历史证据仅供查阅。"),
      EVIDENCE_WITHDRAWN_OR_CHANGED: copy("The original timestamp, speaker and exact quote no longer occur uniquely in the current source. The evidence was changed, withdrawn or duplicated.", "当前来源的时间、发言人或原文已变化，或出现重复，请核对。"),
      SOURCE_RECONCILIATION_REQUIRED: copy("Source reconciliation still requires review.", "来源变更仍需审核。"),
      DECISION_INVALIDATED: copy("This decision was invalidated. It cannot be restored through stale revalidation.", "此决策已失效，暂无法通过本次复核恢复。"),
    };
    const evidence = original.evidence.map(item => `<blockquote class="quote">${escapeHtml(item.quote || copy("Historical quote unavailable", "历史原文不可用"))}</blockquote><p class="metadata">${escapeHtml(item.source_name)} · ${copy("lines", "行号")} ${item.line_start || "?"}–${item.line_end || "?"} · ${copy("Version", "版本")} ${escapeHtml(item.source_version || copy("Unavailable", "不可用"))}</p>`).join("");
    const sources = view.current_sources.map(source => `<article class="card"><div class="card-head"><h3>${escapeHtml(source.name)}</h3>${badge(source.active ? source.ingestion_status : "REMOVED")}</div><p class="metadata">${copy("Current version:", "当前版本：")} ${escapeHtml(source.source_version)}</p><pre class="quote">${escapeHtml(source.content)}</pre></article>`).join("");
    const candidates = view.candidates.map(candidate => `<article class="card"><div class="card-head"><h3>${escapeHtml(candidate.proposed_value)}</h3>${badge(candidate.status)}</div><p class="metadata">${escapeHtml(candidate.subject)} · ${escapeHtml(relationLabel(candidate.relation))} · Effective ${escapeHtml(candidate.valid_from)}</p><blockquote class="quote">${escapeHtml(candidate.evidence.quote)}</blockquote><p class="metadata">${escapeHtml(candidate.source.name)} · ${copy("lines", "行号")} ${candidate.evidence.line_start}–${candidate.evidence.line_end}</p>${candidate.recorded_approval ? `<details><summary>${copy("Recorded approval (read-only)", "已记录的批准（只读）")}</summary><pre>${escapeHtml(JSON.stringify(candidate.recorded_approval, null, 2))}</pre></details><p class="hint">${copy("Approval is already recorded. Finish materialization using only that authorization.", "批准已记录，请完成保存。")}</p>` : ""}<button class="button" data-action="${candidate.status === "APPROVED" ? "retry-revalidation" : "revalidate-candidate"}" data-id="${escapeHtml(candidate.candidate_id)}">${candidate.status === "APPROVED" ? copy("Finish recorded approval", "完成已记录的批准") : copy("Review this new candidate", "审核此新候选")}</button></article>`).join("");
    page.innerHTML = `${heading(`${original.subject} · ${relationLabel(original.relation)}`)}
      <section class="panel"><div class="card-head"><h2>${copy("Original decision:", "原决策：")} ${escapeHtml(original.value)}</h2>${badge(original.memory_state)}</div><p class="metadata">${copy("Effective", "生效日期")} ${escapeHtml(original.valid_from)} · ${escapeHtml(statusLabel(view.status))}</p><h3>${copy("Why this needs attention", "复核原因")}</h3>${view.reasons.map(reason => `<p>${escapeHtml(reasonLabels[reason.code] || copy("Check current source evidence.", "请核对当前来源证据。"))}</p>`).join("")}${evidence}<details><summary>${copy("Original authorization receipts (read-only)", "原始授权记录（只读）")}</summary><pre>${escapeHtml(JSON.stringify(view.original_receipts, null, 2))}</pre></details></section>
      <section class="panel"><h2>${copy("Current sources", "当前来源")}</h2>${sources || `<p>${copy("No current source record is available. Add a source in Sources, then return to review.", "当前来源不可用，请补充来源后返回审核。")}</p>`}</section>
      <section class="panel"><h2>${copy("New candidates for explicit review", "待审核的新候选")}</h2>${candidates || (view.status === "RESOLVED" ? `<p>${copy("This revalidation is complete. The new approval and original record can be inspected in history.", "复核已完成，可在历史中查看新批准和原始记录。")}</p>` : view.status === "READ_ONLY" ? `<p>${copy("This record is read-only and cannot be revalidated here.", "此记录仅供查看，暂无法在此复核。")}</p>` : `<p>${copy("No valid new candidate is available. This decision stays unresolved. Add or update a source, finish extraction, and return here.", "暂无有效新候选。请添加或更新来源，完成提取后继续审核。")}</p>`)}${view.completion ? `<p>${copy("Revalidated by a new approval on", "新批准复核于")} ${escapeHtml(view.completion.authorized_at)}. ${copy("Original record retained in history.", "原记录保留在历史中。")}</p><button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(view.completion.decision_id)}">${copy("View approved decision and history", "查看已批准决策与历史")}</button>` : ""}</section>
      <div class="actions"><button class="button quiet" data-action="back-review">${view.status === "UNRESOLVED" ? copy("Keep unresolved", "保留待处理") : copy("Back to Review", "返回审核")}</button><button class="button quiet" data-action="revalidate-decision" data-id="${escapeHtml(decisionId)}">${copy("Refresh comparison", "刷新对照")}</button></div>`;
  }

  async function loadDecisions() {
    const { decisions } = await request("/api/decisions");
    const cards = decisions.length ? decisions.map((decision) => `
      <article class="card"><div class="card-head"><div><p class="record-kind">${escapeHtml(relationLabel(decision.relation))}</p><h3>${escapeHtml(decision.subject)}</h3></div>${badge(decision.memory_state)}</div>
        <p class="decision-value">${escapeHtml(decision.value)}</p><div class="metadata"><span>${copy("Valid from", "生效日期")} <strong>${escapeHtml(decision.valid_from)}</strong></span><span>${copy("Source", "来源")} <strong>${escapeHtml(decision.evidence[0]?.source_name || copy("Unavailable", "不可用"))}</strong></span></div>
        <button class="button quiet" data-action="inspect-decision" data-id="${escapeHtml(decision.decision_id)}">${copy("View history and evidence", "查看历史与证据")}</button></article>`).join("") : `<section class="empty"><h2>${copy("No decisions in this workspace", "工作区暂无决策")}</h2><p>${copy("Import a source, review a candidate, and approve it before it appears here.", "导入来源并审核候选，批准后即可在此查看。")}</p></section>`;
    page.innerHTML = `${heading(copy("Decision Memory", "决策记忆"))}
      <p class="metadata">${decisions.length} ${copy("records", "条记录")}</p><div class="grid">${cards}</div>`;
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
    state.sourceId = null; state.candidateId = null; state.revalidation = null;
    state.decisionId = decisionId;
    const evidenceRows = (evidence) => (evidence || []).map((item) => `<p class="metadata">
      ${copy("Source:", "来源：")} ${escapeHtml(item.source_name || copy("Unavailable", "不可用"))} · ${copy("lines", "行号")} ${escapeHtml(item.line_start || "?")}–${escapeHtml(item.line_end || "?")}</p>
      <blockquote class="quote">${escapeHtml(item.quote || copy("Verified quote unavailable", "核验原文不可用"))}</blockquote>
      ${item.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${escapeHtml(item.source_id)}">${copy("Inspect source", "查看来源")}</button>` : ""}
      <details><summary>${copy("Source references", "来源引用详情")}</summary><p class="metadata">${copy("Version", "版本")}: ${escapeHtml(item.source_version || copy("Unavailable", "不可用"))} · ${copy("Evidence", "证据")}: ${escapeHtml(item.evidence_ref || copy("Unavailable", "不可用"))}</p></details>`).join("");
    const timeline = history.filter(item => item.valid_from <= recovery.as_of).map((item) => `<article class="timeline-item"><div class="card-head"><strong>${escapeHtml(item.value)}</strong>${badge(item.memory_state || item.status)}</div>
      <p class="metadata">${askDate(item.valid_from, state.language.startsWith("zh") ? "zh" : "en")}${item.valid_to ? ` → ${askDate(item.valid_to, state.language.startsWith("zh") ? "zh" : "en")}` : copy(" → open-ended", " → 未设结束日期")}</p>
      <details><summary>${copy("Approval record", "批准记录")}</summary><pre>${escapeHtml(JSON.stringify({decision_id: item.decision_id, status: item.status, memory_state: item.memory_state, authorization: item.authorization, revalidated_by: item.revalidated_by}, null, 2))}</pre></details>${item.revalidated_by ? `<p>${copy("Revalidated by a separate human approval on", "独立人工批准复核于")} ${askDate(item.revalidated_by.authorized_at, state.language.startsWith("zh") ? "zh" : "en")}</p>` : ""}${evidenceRows(item.evidence)}</article>`).join("");
    const transitions = recovery.transitions.map((item) => `<p>${escapeHtml(item.from_value)} → ${escapeHtml(item.to_value)} on ${escapeHtml(item.effective_at)}.
      ${copy("Replacement reason:", "替代原因：")} ${item.reason_status === "NOT_SOURCE_VERIFIED" ? copy("Needs source evidence", "待补充来源证据") : escapeHtml(item.reason_status)}.</p>`).join("");
    const proposals = recovery.proposals.map((item) => `<article class="timeline-item"><strong>${escapeHtml(item.value)}</strong> ${badge(item.status)}
      <p class="hint">${copy("Proposal only; no formal review receipt.", "未批准的提案")}</p>${evidenceRows([item.evidence])}</article>`).join("");
    page.innerHTML = `${heading(`${decision.subject} · ${relationLabel(decision.relation)}`)}
      <section class="panel"><form id="timeline-as-of" class="form-stack" data-id="${escapeHtml(decisionId)}"><div class="field"><label for="timeline-date">${copy("Decision valid on", "查询有效日期")}</label>
        <input type="date" id="timeline-date" name="as_of" value="${escapeHtml(asOf)}"></div><button class="button" type="submit">${copy("Show reviewed state", "查询审核记录")}</button></form>
        <p class="hint">${copy("Historical dates use the approvals currently recorded.", "历史日期查询依据目前保存的审核记录。")}</p>
        <p>${copy("Selected date:", "查询日期：")} ${escapeHtml(recovery.as_of.slice(0, 10))} · ${escapeHtml(statusLabel(recovery.status))}</p>
        <h2>${recovery.effective ? escapeHtml(recovery.effective.value) : copy("No verified effective decision for this date", "暂未核实该日期的有效决策")}</h2></section>
      <section class="panel"><h2>${copy("Decision timeline", "决策时间线")}</h2><div class="timeline">${timeline || copy("No reviewed decision was effective by this date.", "暂未找到该日期前生效的审核记录。")}</div>
        <h3>${copy("Formal replacements", "已批准的替代记录")}</h3>${transitions || copy("No verified replacement by this date.", "暂未核实该日期前的替代记录。")}
        <h3>${copy("Unapproved proposals", "未批准的提案")}</h3>${proposals || copy("No unapproved proposal recorded by this date.", "暂无该日期前记录的未批准提案。")}</section>
      <section class="panel"><h2>${copy("Ask about this decision", "询问此决策")}</h2><p class="hint">${copy("Ask for reviewed state or investigate its sources from the same Ask form.", "查询审核记录，或调查相关来源。")}</p>
        <button class="button" data-action="ask-decision" data-subject="${escapeHtml(decision.subject)}"
          data-relation="${escapeHtml(decision.relation)}" data-as-of="${escapeHtml(asOf)}">${copy("Open Ask with this scope", "询问此决策")}</button></section>
      <p><button class="button quiet" data-action="back-decisions">${copy("Back to Decision Memory", "返回决策记忆")}</button></p>`;
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
      document.getElementById("provider-state").textContent = copy("Task investigation enabled · source permission required", "任务调查已启用 · 需逐次授权来源");
    }
    const selected = state.askScopes.findIndex((slot) => slot.subject === state.askScope?.subject && slot.relation === state.askScope?.relation);
    state.askCandidates = [];
    state.askClarification = null;
    state.askResult = null;
    page.innerHTML = `<header class="page-heading ask-heading"><h1>${askLabel("Ask", "提问")}</h1>
      <div class="field ask-language"><label for="ask-language">${askLabel("Answer language", "回答语言")}</label>
      <select id="ask-language"><option value="auto" ${state.askLanguage === "auto" ? "selected" : ""}>Auto / 跟随问题</option><option value="zh" ${state.askLanguage === "zh" ? "selected" : ""}>中文</option><option value="en" ${state.askLanguage === "en" ? "selected" : ""}>English</option></select></div></header>
      <section class="panel ask-composer"><p class="metadata">${askLabel("Workspace", "工作区")} <strong>${escapeHtml(state.workspace.name)}</strong></p>
      <form id="ask-form" class="form-stack">
        <div class="field"><label for="ask-query">${askLabel("Your question", "你的问题")}</label><textarea id="ask-query" name="query" required maxlength="4000" placeholder="${escapeHtml(askCopy("What supplier are we currently using?", "我们现在采用哪家供应商？"))}"></textarea></div>
        <div class="field"><label for="ask-domain">${askLabel("Investigation scope (optional)", "调查领域（可选）")}</label><select id="ask-domain" name="investigation_domain">
          <option value="" data-ask-en="Automatic" data-ask-zh="自动选择">${askCopy("Automatic", "自动选择")}</option>
          <option value="tasks" data-ask-en="Tasks across the current workspace records" data-ask-zh="当前工作区记录中的任务">${askCopy("Tasks across the current workspace records", "当前工作区记录中的任务")}</option>
          <option value="source_relations" data-ask-en="Project → owner → direct leader" data-ask-zh="项目 → 负责人 → 直属领导">${askCopy("Project → owner → direct leader", "项目 → 负责人 → 直属领导")}</option></select>
          <span class="hint">${askLabel("Choose a scope to focus your question. Refresh after updating sources.", "可选择领域缩小范围；来源更新后请刷新页面。")}</span></div>
        <div id="task-provider-status"><div id="task-setup-status" role="status" hidden><p>${config.agent?.task_investigation ? askLabel("Task investigation ready", "任务调查已就绪") : askLabel("Task investigation is unavailable in this setup.", "当前配置尚未启用任务调查。")}</p>
          ${!config.agent?.task_investigation ? `<details><summary>${askLabel("Setup details", "配置详情")}</summary><p class="hint">${escapeHtml(config.agent?.task_provider?.setup || "--allow-external-provider --allow-task-investigation --task-max-requests 16 --task-max-input-tokens 500000 --task-max-output-tokens 100000 --task-max-cost-usd 2 --task-max-seconds 120 --task-request-timeout-seconds 30")}</p></details>` : ""}</div>
          ${config.agent?.task_provider?.budgets ? `<div class="metadata">${askLabel("Per-question limits", "单次调查上限")}: ${escapeHtml(config.agent.task_provider.model_id)} · USD ${config.agent.task_provider.budgets.max_cost_usd} · ${config.agent.task_provider.budgets.max_requests} ${askLabel("requests", "次请求")} · ${config.agent.task_provider.budgets.max_seconds}s<details><summary>${askLabel("Token limits", "Token 上限")}</summary><p>${config.agent.task_provider.budgets.max_input_tokens} input / ${config.agent.task_provider.budgets.max_output_tokens} output</p></details></div>` : ""}
          <details><summary>${askLabel("Sources in this authorization", "本次授权的来源清单")} (${config.investigation_context?.source_count || 0})</summary><ul>${(config.investigation_context?.sources || []).map(s => `<li>${escapeHtml(s.name)} <small>${escapeHtml(s.version)}</small></li>`).join("")}</ul><p class="hint">${askLabel("Permission covers this question and the listed source versions.", "仅授权本次问题及所列来源版本。")}</p></details></div>
        <div class="ask-submit"><button id="ask-submit-button" class="button" type="submit">${askLabel("Ask", "提问")}</button><span class="hint">${askLabel("Ctrl / ⌘ + Enter to ask", "Ctrl / ⌘ + Enter 提交")}</span></div>
        <details id="ask-advanced" ${selected >= 0 ? "open" : ""}><summary>${askLabel("Advanced: decision and exact date", "高级选项：决策与精确日期")}</summary>
        <div class="field"><label for="ask-slot">${askLabel("Decision scope", "指定决策（可选）")}</label><select id="ask-slot">
          <option value="" data-ask-en="Discover automatically from my question" data-ask-zh="根据问题自动查找">${askCopy("Discover automatically from my question", "根据问题自动查找")}</option>${state.askScopes.map((slot, index) => `<option value="${index}" data-ask-subject="${escapeHtml(slot.subject)}" data-ask-relation="${escapeHtml(slot.relation)}" ${index === selected ? "selected" : ""}>${escapeHtml(slot.subject)} · ${escapeHtml(decisionCategory(slot.relation, askLocale()))}</option>`).join("")}</select>
          <span class="hint">${askLabel("Only reviewed, source-verified decisions are available.", "仅包含已审核且来源可核验的决策。")}</span></div>
        <div class="field"><label for="ask-date">${askLabel("Decision valid on this date (optional)", "查询哪一天有效的决策（可选）")}</label><input type="date" id="ask-date" name="as_of" value="${escapeHtml((state.askScope?.as_of || "").slice(0, 10))}"><span class="hint">${askLabel("Blank means now. Dates start at 00:00 UTC; history uses currently recorded approvals.", "留空查询当前；日期以 UTC 零点为准，使用目前记录的审核信息。")}</span></div></details>
        ${config.agent?.available && config.agent?.external_provider ? `<label class="ask-permission"><input id="ask-external" type="checkbox">${askLabel("Allow this request to send sources to the configured external AI provider and incur bounded model costs.", "仅授权本次向已配置的外部 AI 发送来源内容，并产生有上限的模型费用。")}</label>` : `<p class="hint">${config.agent?.available ? askLabel("Offline investigation demo is available.", "可使用离线调查演示。") : askLabel("AI investigation is not configured. Reviewed record queries remain available.", "尚未配置 AI 调查；可信决策查询可直接使用。")}</p>`}
      </form>${savedTaskRun(workspaceId) ? `<button class="button quiet" type="button" data-action="reopen-task">${askLabel("Recheck last task investigation", "重新核验上次任务调查")}</button>` : ""}</section><div id="ask-result" tabindex="-1" aria-live="polite"></div>
      <details class="panel" id="feedback-panel"><summary>${askLabel("Term not understood? Propose a correction", "术语没识别对？提出纠正")}</summary>
        <p>${askLabel("Terminology applies only to this workspace after your explicit approval. It cannot approve or change business decisions.", "术语仅在本工作区经人工确认后生效，不能批准或修改业务决策。")}</p>
        <form id="feedback-form" class="form-stack">
          <div class="field"><label for="feedback-term">${askLabel("New term", "新术语")}</label><input id="feedback-term" name="term" required minlength="2" maxlength="40"><span class="hint">${askLabel("One term, 2–40 characters; sentences are unsupported.", "单个术语，2–40 字符；不支持整句。")}</span></div>
          <div class="field"><label for="feedback-relation">${askLabel("Means this existing relation", "对应已有关系")}</label><select id="feedback-relation" name="relation" required>${[...new Set(state.askScopes.map(s => s.relation))].map(relation => `<option value="${escapeHtml(relation)}">${escapeHtml(decisionCategory(relation, askLocale()))}</option>`).join("")}</select></div>
          <div class="field"><label for="feedback-actor">${askLabel("Reviewer name (local record)", "审核人姓名（本地记录）")}</label><input id="feedback-actor" name="actor" required maxlength="128" placeholder="local-user"></div>
          <button class="button quiet" type="submit">${askLabel("Submit for review", "提交审核")}</button>
        </form><div id="feedback-status" role="status" aria-live="polite"></div><div id="feedback-entries"></div>
      </details>`;
    syncTaskOptions();
    await loadFeedback(workspaceId, viewToken);
  }

  function syncTaskOptions() {
    const status = document.getElementById("task-setup-status");
    if (status) status.hidden = document.getElementById("ask-domain")?.value !== "tasks";
  }

  async function loadFeedback(workspaceId = state.workspace?.workspace_id, viewToken = state.agentViewToken) {
    try {
      const data = await request(`/api/product/feedback?workspace_id=${encodeURIComponent(workspaceId)}`);
      if (viewToken !== state.agentViewToken || workspaceId !== state.workspace?.workspace_id) return;
      const target = document.getElementById("feedback-entries");
      if (!target) return;
      const labels = {pending: "待确认 / Pending", active: "已批准 / Approved", revoked: "已撤销 / Revoked", expired: "已过期 / Expired", rejected: "已拒绝 / Rejected"};
      target.innerHTML = (data.entries || []).map(entry => `<article class="ask-evidence"><h3>${escapeHtml(entry.term)} → ${escapeHtml(relationLabel(entry.relation))}</h3><p>${escapeHtml(labels[entry.status] || entry.status)} · ${escapeHtml(entry.actor)}</p>${entry.status === "pending" ? `<button type="button" class="button quiet" data-action="feedback-approve" data-id="${escapeHtml(entry.id)}">${askCopy("I confirm this terminology", "我确认此术语映射")}</button> <button type="button" class="button quiet" data-action="feedback-reject" data-id="${escapeHtml(entry.id)}">${askCopy("Reject", "拒绝")}</button>` : entry.status === "active" ? `<button type="button" class="button quiet" data-action="feedback-revoke" data-id="${escapeHtml(entry.id)}">${askCopy("Revoke mapping", "撤销映射")}</button>` : ""}</article>`).join("") || `<p class="hint">${askCopy("No terminology feedback in this workspace.", "本工作区暂无术语反馈。")}</p>`;
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
    state.askLocaleRun = null;
    state.agentViewToken += 1;
    state.askCandidates = [];
    state.askResult = null;
    const target = document.getElementById("ask-result");
    if (target) target.innerHTML = "";
  }

  async function askRequest(path, options = {}) {
    let response;
    try { response = await fetch(path, { ...options, headers: { "Content-Type": "application/json" } }); }
    catch { throw new Error(copy("Ask could not reach its local service.", "提问服务暂时无法连接。")); }
    const payload = await response.json();
    if (payload.result) return payload.result;
    const error = new Error(payload.error?.message || copy("Ask status is unavailable.", "提问状态暂时不可用。"));
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
      // The task renderer remains presentation-only.  Local review is mounted
      // as a narrow post-render extension so Host capabilities and source/CAS
      // checks stay in the dedicated review module/API.
      // The task answer language is resolved from the query locale.  Keep the
      // review panel on that same locale rather than the app's display-state
      // language, which may still be English while an answer is Chinese.
      const reviewLanguage = locale;
      window.mountTaskReview?.({
        target, result, workspaceId: state.workspace?.workspace_id,
        runId: result.agent_run_id, language: reviewLanguage,
        invalidateResult: () => invalidateAsk(),
        isCurrent: () => viewToken === state.agentViewToken
          && state.workspace?.workspace_id === result.workspace_id
          && askLocale(result.query) === reviewLanguage
          && target.isConnected !== false,
      });
      return;
    }
    if (result.path === "TASK_INVESTIGATION" && result.status === "BLOCKED") {
      const unavailable = result.error?.code === "TASK_INVESTIGATION_UNAVAILABLE";
      const permission = result.error?.code === "EXTERNAL_PROVIDER_PERMISSION_REQUIRED";
      let stopMessage = t("Check the investigation setup, source access and budget before trying again.", "请检查调查配置、来源权限和预算后重试。");
      if (unavailable) stopMessage = t("Task investigation is unavailable in the current configuration. No investigation or external model call was started.", "当前配置不支持任务调查，尚未启动调查或外部模型调用。");
      else if (permission) stopMessage = t("Check the per-request permission above, then ask again. Sources are sent only after permission is granted.", "请查看上方来源清单与预算，勾选本次授权后再次提问；授权前不会发送来源内容。");
      else if (["PROVIDER_UNCERTAIN", "PROVIDER_TIMEOUT_UNCERTAIN"].includes(result.error?.code)) stopMessage = t("The AI service response is uncertain. Check the run details before retrying; automatic retry is off.", "AI 服务响应状态不明，请检查运行详情后再决定是否重试；自动重试已停止。");
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
        EMPTY_WORKSPACE: [t("Add a source first", "请先添加来源"), t("Import meeting notes in Sources, then return to investigate.", "请先在来源页面导入会议记录，再返回调查。")],
        INVESTIGATION_DOMAIN_REQUIRED: [t("Choose an investigation scope", "请选择调查领域"), t("Select tasks or project relationships above, or choose a reviewed decision. Your question has been kept; no investigation has started.", "请在上方选择任务或项目关系领域，或指定可信决策。原问题已保留，尚未启动调查。")],
        INVESTIGATION_CONTEXT_CHANGED: [t("The source scope changed", "来源范围已变化"), t("Refresh this page to review the current workspace sources, then select the domain and ask again.", "请刷新页面，查看当前工作区来源后重新选择领域并提问。")],
        INVESTIGATION_CAPABILITY_UNAVAILABLE: [t("This investigation capability is unavailable", "当前不支持此调查能力"), t("Choose another scope or ask about a reviewed decision.", "请选择其他领域，或询问已审核的决策。")],
        INVESTIGATION_DOMAIN_CONFLICT: [t("Check the investigation domain", "请核对调查领域"), t("This question requests project relationships. Select that domain to continue the original question.", "该问题请求项目关系调查，请选择对应领域以继续原问题。")],
        INVESTIGATION_SCOPE_DENIED: [t("The request exceeds read-only scope", "请求超出只读调查范围"), t("Ask about the current workspace records without write, credential or permission-bypass instructions.", "请针对当前工作区记录提问，不包含写入、凭证或绕过权限的指令。")],
        INVESTIGATION_QUESTION_REQUIRED: [t("Enter an investigation question", "请输入调查问题"), t("Ask a question about the selected records.", "请对选定记录提出具体问题。")],
      };
      const admissionMessage = admissionMessages[code];
      const title = admissionMessage ? admissionMessage[0] : unsupported ? t("This relationship question is not supported", "目前不能可靠地拆解这个关系问题") : memoryConflict ? t("Approved terminology conflicts", "已批准术语存在冲突") : ambiguous ? t("Which decision do you mean?", "你想问的是哪项决策？")
        : dateNeeded ? t("Choose a specific date", "请选择一个具体日期")
        : permission ? t("This investigation needs your permission", "这次调查需要你的授权")
        : uncertain ? t("Investigation outcome uncertain", "调查执行结果不确定")
        : noMatch ? t("No matching reviewed decision found", "暂未找到与问题匹配的可信决策")
        : t("No verified answer is available", "本次尚未得到可核验的答案");
      const message = admissionMessage ? admissionMessage[1] : unsupported ? t("Try: Who is the direct leader of the owner of PROJECT? Or: Who owns the project behind ALIAS and who leads that owner? Clear any advanced decision selection first.", "支持项目 → 负责人 → 直属领导，以及英文别名 → 项目 → 负责人 → 直属领导问法。请修改问题，并清除高级选项中指定的正式决策。") : memoryConflict ? t("Revoke the conflicting mapping below, or choose a reviewed decision in advanced options.", "请在下方撤销冲突映射，或在高级选项中指定可信决策。") : ambiguous ? t("Choose one decision to continue your original question.", "选择一项后继续原问题，无需重新输入。")
        : dateNeeded ? t("Select one exact date above to check which decision was valid. Your question and confirmed decision are retained.", "请在上方选择精确日期，以核实当时有效的决策。原问题和已确认的决策会保留。")
        : permission ? t("Check the per-request permission above, then ask again. Sources are sent only after permission is granted.", "请勾选上方的本次授权，再次提问后才会发送来源内容并启动调查。")
        : uncertain ? t("Check the run details before retrying. No verified answer was returned.", "请检查运行详情后再决定是否重试，本次未返回可核验答案。")
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
    const unknownText = unknown.map(field => field === "replacement_reason" ? t("The replacement reason needs source evidence.", "缺少更换原因的来源证据。") : field === "effective_decision" ? t("No effective formal decision could be verified.", "未能核实该日期有效的正式决策。") : field === "investigation_conclusion" ? t("The investigation conclusion has not been verified.", "本次调查结论尚未核验。") : t("Some requested information is not supported by evidence.", "请求中的部分信息尚无可信证据。")).join(" ");
    const title = !claims.length ? t("No verifiable formal decision for this date", "该日期没有可核实的正式决策") : reasonUnknown ? (transition ? t("Change verified; the reason remains unknown", "更换事实已核实，原因尚无可信证据") : t("The reason is not established", "原因尚无可信证据")) : escapeHtml(claims[0].value);
    const sentence = reasonUnknown && transition ? `${escapeHtml(transition.from_value)} → ${escapeHtml(transition.to_value)}. ${t("Reason: unconfirmed.", "更换原因：待确认。")}`
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
      ${(result.discovery?.memory?.used || []).map(item => `<p class="hint">${t("Approved terminology used to locate this decision", "使用已批准术语定位此决策")}: ${escapeHtml(item.term)} → ${escapeHtml(item.relation)}. ${t("", "")}</p>`).join("")}
      ${historical ? `<p class="hint">${t("Historical dates use the approvals currently recorded.", "历史日期查询依据目前保存的审核记录。")}</p>` : ""}
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
      return `<article class="ask-evidence"><h3>${escapeHtml(labels[predicate] || predicate)}</h3>${fact ? `<p>${escapeHtml(fact.subject)} → <strong>${escapeHtml(fact.value)}</strong>${fact.valid_on ? ` · ${escapeHtml(fact.valid_on)}` : ""}</p>` : `<p>${conflict ? t("No unique result; inspect the opposing sources below.", "没有唯一结果，请查看下方互相矛盾的原始来源。") : t("This relationship needs more evidence.", "此关系待补充证据。")}</p>`}</article>`;
    }).join("");
    target.innerHTML = `<section class="panel ask-answer ${complete ? "" : "ask-answer--notice"}"><p class="ask-answer-status">${facts.length ? t("Source-supported relationship", "来源支持的关系") : t("Source investigation; no relationship verified", "来源调查，尚无关系得到核实")}</p>
      <h2 class="${complete ? "ask-answer-value" : "ask-answer-headline"}">${title}</h2>
      <p>${complete ? t("Source evidence supports this relationship.", "已核验的来源证据支持此关系。") : conflict ? t("The known upstream facts are retained. Opposing sources prevent a unique downstream conclusion.", "已确认的上游关系予以保留；下游来源互相矛盾，不能选择唯一结论。") : t("The known upstream facts are retained. The requested final relationship remains unknown.", "已确认的上游关系予以保留；尚未找到足够证据确认最终关系。")}</p>
      <p class="hint">${t("Source findings · business approval separate", "来源调查结果 · 业务批准需单独进行")}</p>
      <div class="ask-pills"><span>${(source.plan?.subgoals || []).length} ${t("relationship checks", "项关系核查")}</span></div>
      ${steps}<section class="ask-core-evidence"><h2>${t("Sources supporting the verified facts or conflict", "支持已核实关系或冲突的来源")}</h2>${coreEvidence.map((item, index) => askEvidence(item, index, locale)).join("")}</section>
      ${otherEvidence.length ? `<details><summary>${t("Other sources examined", "其他已查阅来源")} (${otherEvidence.length})</summary>${otherEvidence.map((item, index) => askEvidence(item, coreEvidence.length + index, locale)).join("")}</details>` : ""}
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
    state.askLocaleRun = null;
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
    page.innerHTML = `<p class="loading">${copy("Switching workspace\u2026", "正在切换工作区…")}</p>`;
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
    state.sourceId = null; state.candidateId = null; state.revalidation = null; state.decisionId = null;
    showNoWorkspace();
  });

  page.addEventListener("keydown", (event) => {
    if (event.target?.id === "ask-query" && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault(); document.getElementById("ask-form").requestSubmit();
    }
  });

  page.addEventListener("input", (event) => {
    if (event.target?.id === "ask-query") localizeAsk(event.target.value);
    if (event.target?.id === "ask-domain") syncTaskOptions();
    if (["ask-query", "ask-slot", "ask-domain"].includes(event.target?.id)) state.askClarification = null;
    if (["ask-query", "ask-date", "ask-slot", "ask-external", "ask-domain"].includes(event.target?.id)) invalidateAsk();
  });

  page.addEventListener("change", async (event) => {
    if (event.target?.id === "ask-language") {
      state.askLanguage = event.target.value; localizeAsk();
      if (state.askResult) renderAskResult(state.askResult, state.agentViewToken);
      return;
    }
    if (event.target?.id === "ask-domain") syncTaskOptions();
    if (["ask-query", "ask-slot", "ask-domain"].includes(event.target?.id)) state.askClarification = null;
    if (["ask-query", "ask-date", "ask-slot", "ask-external", "ask-domain"].includes(event.target?.id)) { invalidateAsk(); return; }
    if (event.target?.id !== "source-file") return;
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 256 * 1024) {
      announce(copy("Source files must be 256 KB or smaller.", "来源文件最大支持 256 KB。"));
      event.target.value = "";
      return;
    }
    try {
      document.getElementById("source-name").value = file.name;
      document.getElementById("source-content").value = await file.text();
      announce(`Loaded ${file.name} locally. Review the text before importing.`);
    } catch {
      announce(copy("LinkLoom could not read this file. Paste the timestamped text instead.", "文件读取失败，请粘贴带时间戳的文本。"));
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
          ? copy("Source ingestion started.", "已开始处理来源。")
          : copy("Source saved. Semantic extraction is unavailable until a provider is enabled.", "来源已保存，启用 AI 服务后可提取候选。"));
        await inspectSource(result.source.source_id);
        pollSource(result.source.source_id, true);
      } else if (form.id === "source-update") {
        const result = await send("PUT", `/api/sources/${encodeURIComponent(form.dataset.id)}`, Object.fromEntries(new FormData(form)));
        announce(result.changed ? copy("A new source version is ingesting.", "正在处理来源新版本。") : copy("No source text changes. Existing decision evidence remains unchanged.", "来源文本没有变化。"));
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
        announce(action === "approve" ? copy("Candidate approved and materialized.", "决策已批准并保存。") : copy("Candidate rejected.", "候选已拒绝。"));
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
    } catch (error) { renderError(error, `<p><button class="button quiet" data-action="reload">${copy("Refresh", "刷新")}</button></p>`); }
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
        announce(copy("The recorded revalidation approval was materialized.", "已保存本次复核的批准结果。"));
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
        announce(copy("Failed segment recovery started.", "已开始重试失败片段。"));
        await loadSources();
        pollSource(id);
      } else if (action === "delete-source") {
        if (!window.confirm(copy("Remove this source from the workspace? Its captured versions remain available for provenance.", "确认从工作区移除此来源？历史版本将保留，相关决策会重新核查。"))) return;
        await request(`/api/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
        announce(copy("Source removed; affected decisions were reconciled.", "来源已移除，已重新核查受影响的决策。"));
        await loadSources();
      }
    } catch (error) { renderError(error); }
  });

  function localizeChrome() {
    const nav = {sources: copy("Sources", "来源"), review: copy("Review", "审核"),
      decisions: copy("Decision Memory", "决策记忆"), ask: copy("Ask", "提问")};
    document.querySelectorAll(".primary-nav [data-screen]").forEach(button => {
      const label = button.querySelector?.(".nav-label");
      if (label) label.textContent = nav[button.dataset.screen];
    });
    for (const [id, en, zh] of [["workspace-label", "Workspace", "工作区"],
      ["new-workspace", "New workspace", "新建工作区"], ["interface-language-label", "Language", "界面语言"],
      ["skip-content", "Skip to content", "跳到主要内容"]]) {
      const element = document.getElementById(id);
      if (element) element.textContent = copy(en, zh);
    }
    document.documentElement.lang = state.language;
    document.title = copy("LinkLoom — Team Decision Memory", "LinkLoom — 团队决策记忆");
    workspacePicker.setAttribute("aria-label", copy("Select workspace", "选择工作区"));
    const picker = document.getElementById("interface-language");
    if (picker) picker.value = state.language.startsWith("zh") ? "zh" : "en";
  }

  document.getElementById("interface-language")?.addEventListener("change", async event => {
    const fields = [...(page.querySelectorAll?.("input:not([type=file]):not([type=checkbox]), textarea, select") || [])]
      .map(field => ({id: field.id, value: field.value}));
    const detail = {creating: state.creatingWorkspace, source: state.sourceId, candidate: state.candidateId, revalidation: state.revalidation, decision: state.decisionId};
    const taskRun = state.screen === "ask" && state.askResult?.path === "TASK_INVESTIGATION"
      && state.askResult.task_review && state.askResult.reviewer?.accepted === true ? state.askResult.agent_run_id
      : state.screen === "ask" && state.askLocaleRun?.workspaceId === state.workspace?.workspace_id ? state.askLocaleRun.runId : null;
    invalidateAsk();
    const workspaceId = state.workspace?.workspace_id;
    const language = event.target.value;
    state.language = language;
    try { window.localStorage.setItem("linkloom.locale", language); } catch { /* Optional. */ }
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("lang", language);
      window.history.replaceState(null, "", url);
    } catch { /* URL persistence is optional. */ }
    localizeChrome(); updateWorkspacePicker();
    try {
      const rendering = detail.creating ? showNoWorkspace() : detail.candidate ? inspectCandidate(detail.candidate, detail.revalidation)
        : detail.source ? inspectSource(detail.source)
        : detail.revalidation ? inspectRevalidation(detail.revalidation.original.decision_id)
        : detail.decision ? inspectDecision(detail.decision, fields.find(field => field.id === "timeline-date")?.value || "") : loadScreen();
      const viewToken = state.agentViewToken;
      state.askLocaleRun = taskRun ? {workspaceId, runId: taskRun} : null;
      await rendering;
      if (viewToken !== state.agentViewToken || workspaceId !== state.workspace?.workspace_id || language !== state.language) return;
      for (const field of fields) {
        const input = document.getElementById(field.id);
        if (input && field.id !== "ask-external") input.value = field.value;
      }
      if (state.screen === "ask") {
        localizeAsk(); syncTaskOptions();
        // Restore only a currently displayed task through the existing read-only
        // recheck; never reuse cached evidence or repeat an investigation POST.
        if (taskRun) await pollAsk(taskRun, workspaceId, viewToken);
      }
    } catch (error) { renderError(error); }
  });

  async function bootstrap() {
    try {
      localizeChrome();
      await refreshWorkspaceState();
      if (state.workspace) await loadScreen("sources");
      else await showNoWorkspace();
    } catch (error) { renderError(error); }
  }
  bootstrap();
})();
