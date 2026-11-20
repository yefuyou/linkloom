/* LinkLoom v2 · B1-4 Progressive UX & Visual Workbench Layer
 * Independent module: enhances navigation hierarchy, side-by-side review,
 * undated-decision zone, Ask trace drawer, and global settings drawer without
 * modifying product.js or fabricating backend data.
 */
(() => {
  const page = document.getElementById("page");
  const contextBar = document.getElementById("workbench-context-bar");
  const settingsTrigger = document.getElementById("settings-trigger");
  const settingsDialog = document.getElementById("settings-drawer");
  const settingsBody = document.getElementById("settings-drawer-body");
  const settingsClose = document.getElementById("settings-close");
  const workspacePicker = document.getElementById("workspace-picker");
  const reviewCountEl = document.getElementById("review-count");
  const providerStateEl = document.getElementById("provider-state");

  if (!page || !contextBar) return;

  const isZh = () => String(document.documentElement.lang || "zh").toLowerCase().startsWith("zh");
  const t = (en, zh) => (isZh() ? zh : en);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function currentWorkspace() {
    const id = workspacePicker?.value || "";
    const label = workspacePicker?.selectedOptions?.[0]?.textContent?.trim() || id || t("None selected", "未选择");
    return { id, label };
  }

  function currentHash() {
    return String(window.location?.hash || "#meetings").replace(/^#/, "") || "meetings";
  }

  function primaryViewForHash(hash) {
    if (hash === "decisions") return "evolution";
    if (hash === "ask") return "ask";
    return "meetings";
  }

  function renderContextBar() {
    const ws = currentWorkspace();
    const hash = currentHash();
    const view = primaryViewForHash(hash);
    const pendingCount = Number(reviewCountEl?.textContent || 0) || 0;

    if (settingsTrigger) {
      settingsTrigger.textContent = t("Settings", "设置");
      settingsTrigger.setAttribute("aria-label", t("Open workspace settings and glossary", "打开工作区设置与术语表"));
    }

    const countBadge = pendingCount > 0
      ? `<span class="workbench-pill-count">${pendingCount}</span>`
      : "";

    let subnav = "";
    if (view === "meetings") {
      subnav = `
        <div class="workbench-subtabs" role="navigation" aria-label="${escapeHtml(t("Meetings and review views", "会议与审核子视图"))}">
          <a class="workbench-subtab" href="/#meetings" ${hash === "meetings" ? 'aria-current="page"' : ""}>${escapeHtml(t("Meetings & Import", "会议列表与导入"))}</a>
          <a class="workbench-subtab" href="/#review" ${hash === "review" ? 'aria-current="page"' : ""}>${escapeHtml(t("Review Queue", "待确认队列"))}${countBadge}</a>
        </div>`;
    } else if (view === "evolution") {
      subnav = `
        <div class="workbench-subtabs" role="navigation" aria-label="${escapeHtml(t("Evolution views", "演变看板子视图"))}">
          <a class="workbench-subtab" href="/#decisions" aria-current="page">${escapeHtml(t("Decisions & History", "决议演变"))}</a>
          <a class="workbench-subtab" href="/tasks">${escapeHtml(t("Tasks & Events", "待办任务看板"))}</a>
        </div>`;
    } else {
      subnav = `
        <div class="workbench-subtabs" aria-label="${escapeHtml(t("Ask workbench", "Ask 工作台"))}">
          <span class="workbench-subtab is-static">${escapeHtml(t("Ask · Evidence · Trace", "提问 · 原文证据 · 运行调用链"))}</span>
        </div>`;
    }

    const viewTitle = view === "meetings"
      ? t("① Meetings & Review", "① 会议与审核")
      : view === "evolution"
        ? t("② Decisions & Tasks Evolution", "② 决议与任务演变")
        : t("③ Ask Workbench", "③ Ask 工作台");

    contextBar.hidden = false;
    contextBar.innerHTML = `
      <div class="workbench-context-meta">
        <span class="workbench-view-label">${escapeHtml(viewTitle)}</span>
        <span class="workbench-ws-chip" title="${escapeHtml(ws.id)}">${escapeHtml(t("Workspace", "工作区"))}: <strong>${escapeHtml(ws.label)}</strong></span>
      </div>
      ${subnav}
    `;
  }

  /* ── 1. Side-by-side review enhancement (left source reader + right review form) ── */
  const sourceCache = new Map();

  async function enhanceCandidateReviewSplit() {
    const form = page.querySelector("#candidate-review");
    const layout = page.querySelector(".detail-layout");
    if (!form || !layout || layout.dataset.workbenchEnhanced === "true") return;
    layout.dataset.workbenchEnhanced = "true";
    layout.classList.add("review-split");

    const validFromInput = form.querySelector("#review-valid-from");
    const leaveUndatedCb = form.querySelector("#review-leave-undated");
    const fillTodayBtn = form.querySelector('[data-action="fill-review-today"]');
    const clearDateBtn = form.querySelector('[data-action="clear-review-date"]');
    if (leaveUndatedCb && validFromInput) {
      leaveUndatedCb.addEventListener("change", () => {
        if (leaveUndatedCb.checked) {
          validFromInput.value = "";
          validFromInput.disabled = true;
          validFromInput.removeAttribute("required");
        } else {
          validFromInput.disabled = false;
          validFromInput.setAttribute("required", "");
        }
      });
    }
    fillTodayBtn?.addEventListener("click", () => {
      if (!validFromInput) return;
      if (leaveUndatedCb && leaveUndatedCb.checked) {
        leaveUndatedCb.checked = false;
        validFromInput.disabled = false;
        validFromInput.setAttribute("required", "");
      }
      validFromInput.value = new Date().toISOString().slice(0, 10);
      validFromInput.focus();
    });
    clearDateBtn?.addEventListener("click", () => {
      if (!validFromInput) return;
      validFromInput.value = "";
      validFromInput.focus();
    });

    const evidenceWrap = layout.querySelector("[data-evidence-source]");
    const sourceId = evidenceWrap?.dataset?.evidenceSource;
    const quoteText = evidenceWrap?.querySelector("blockquote.quote")?.textContent?.trim() || "";
    if (!sourceId || !evidenceWrap) return;

    const readerSlot = document.createElement("div");
    readerSlot.className = "review-source-reader";
    readerSlot.innerHTML = `<p class="hint">${escapeHtml(t("Loading source context…", "正在加载来源原文上下文…"))}</p>`;
    evidenceWrap.appendChild(readerSlot);

    try {
      let source = sourceCache.get(sourceId);
      if (!source) {
        const res = await fetch(`/api/sources/${encodeURIComponent(sourceId)}`);
        if (!res.ok) throw new Error("SOURCE_UNAVAILABLE");
        const data = await res.json();
        source = data.source;
        if (source) sourceCache.set(sourceId, source);
      }
      if (!readerSlot.isConnected || typeof source?.content !== "string") {
        readerSlot.remove();
        return;
      }
      const lines = source.content.split(/\r?\n/);
      const renderedLines = lines.slice(0, 250).map((line, idx) => {
        const lineNo = idx + 1;
        const hit = quoteText && line.includes(quoteText.slice(0, Math.min(24, quoteText.length)));
        return `<div class="source-line ${hit ? "is-highlighted" : ""}"><span class="source-lineno">${lineNo}</span><span class="source-linetext">${escapeHtml(line || " ")}</span></div>`;
      }).join("");
      const truncated = lines.length > 250
        ? `<p class="hint">${escapeHtml(t(`Showing first 250 of ${lines.length} lines. Use "View original quote" for exact span jump.`, `仅展示前 250 行（共 ${lines.length} 行），精确定位请点击上方“查看原文”。`))}</p>`
        : "";
      readerSlot.innerHTML = `
        <details class="review-source-context" open>
          <summary>${escapeHtml(t("Source transcript context", "来源原文上下文"))} (${lines.length} ${escapeHtml(t("lines", "行"))})</summary>
          <div class="source-lines-box" role="region" aria-label="${escapeHtml(t("Source transcript", "来源原文"))}">${renderedLines}</div>
          ${truncated}
        </details>
      `;
    } catch {
      if (readerSlot.isConnected) {
        readerSlot.innerHTML = `<p class="hint">${escapeHtml(t("Full source transcript could not be loaded here; verified quote above remains authoritative.", "此处暂无法展开全文上下文，以上方核验原文为准。"))}</p>`;
      }
    }
  }

  /* ── 2. Decisions Evolution: Undated Decisions Zone ("时间未知 / 待核验") ── */
  function renderUndatedCards(items) {
    if (!items.length) {
      return `<p class="empty-inline">${escapeHtml(t("No confirmed undated decisions in this workspace.", "当前工作区暂无生效时间未知的已确认决议。"))}</p>`;
    }
    const reviewerDefault = localReviewer();
    return items.map(item => {
      const ev = item.evidence?.[0];
      const evStatus = item.evidence_status || "UNKNOWN";
      return `
        <article class="card undated-card" data-undated-id="${escapeHtml(item.decision_id)}">
          <div class="card-head">
            <div>
              <p class="record-kind">${escapeHtml(item.subject || "")} · ${escapeHtml(item.relation || "")}</p>
              <h3>${escapeHtml(item.value)}</h3>
            </div>
            <div class="actions">
              <span class="badge warning">${escapeHtml(t("Time unknown", "时间未知"))}</span>
              <span class="badge ${evStatus === "VERIFIED" ? "" : "warning"}">${escapeHtml(evStatus)}</span>
            </div>
          </div>
          ${ev ? `<blockquote class="quote">${escapeHtml(ev.quote)}</blockquote><p class="metadata">${escapeHtml(ev.source_name || ev.source_id || "")} · ${escapeHtml(t("lines", "行号"))} ${escapeHtml(ev.line_start ?? "?")}–${escapeHtml(ev.line_end ?? "?")}</p>` : ""}
          <details class="undated-promote-details">
            <summary>${escapeHtml(t("Fill effective date (promote to dated timeline)", "补充生效日期"))}</summary>
            <form class="form-stack undated-promote-form" data-id="${escapeHtml(item.decision_id)}">
              <input type="hidden" name="time_basis" value="USER_SELECTED">
              <div class="field">
                <label>${escapeHtml(t("Effective date", "生效日期"))}</label>
                <input type="date" name="valid_from" required>
                <div class="actions">
                  <button type="button" class="button quiet" data-undated-today>${escapeHtml(t("Fill today", "填充今天"))}</button>
                </div>
              </div>
              <div class="field">
                <label>${escapeHtml(t("Reviewer", "审核人"))}</label>
                <input name="reviewer_id" required maxlength="120" value="${escapeHtml(reviewerDefault)}">
              </div>
              <div class="field">
                <label>${escapeHtml(t("Reason", "补日期依据"))}</label>
                <input name="reason" required maxlength="1000" placeholder="${escapeHtml(t("Why is this effective date confirmed?", "请填写确认生效日期的依据"))}">
              </div>
              <p class="error-inline undated-promote-error" role="alert" hidden></p>
              <button type="submit" class="button">${escapeHtml(t("Save effective date", "确认并保存生效日期"))}</button>
            </form>
          </details>
        </article>
      `;
    }).join("");
  }

  async function enhanceDecisionsEvolution() {
    const filterForm = page.querySelector("#decisions-date-filter");
    if (!filterForm || page.querySelector(".undated-zone")) return;

    const zone = document.createElement("section");
    zone.className = "panel undated-zone";
    zone.setAttribute("aria-labelledby", "undated-zone-title");
    zone.innerHTML = `
      <div class="card-head">
        <div>
          <p class="record-kind">${escapeHtml(t("Confirmed · Effective date unknown", "内容已确认 · 生效时间未知"))}</p>
          <h2 id="undated-zone-title">${escapeHtml(t("Time unknown / Pending date verification", "时间未知 / 待核验区域"))}</h2>
        </div>
        <span class="badge neutral" id="undated-zone-count">…</span>
      </div>
      <p class="hint">${escapeHtml(t(
        "These decisions have confirmed content but no effective date yet. They are excluded from time-ordered CURRENT / AS_OF conclusions until a date is explicitly confirmed.",
        "以下决议的内容已人工确认，但生效日期尚未知；在明确补充生效日期前，不会混入按时间排序的 CURRENT / AS_OF 结论。"
      ))}</p>
      <div id="undated-zone-list"><p class="loading">${escapeHtml(t("Loading undated decisions…", "正在加载时间未知决议…"))}</p></div>
    `;
    page.appendChild(zone);

    zone.addEventListener("click", (event) => {
      const todayBtn = event.target.closest("[data-undated-today]");
      if (!todayBtn) return;
      const form = todayBtn.closest("form.undated-promote-form");
      const dateInput = form?.querySelector('input[name="valid_from"]');
      const basisInput = form?.querySelector('input[name="time_basis"]');
      if (dateInput) dateInput.value = new Date().toISOString().slice(0, 10);
      if (basisInput) basisInput.value = "USER_TODAY";
    });

    zone.addEventListener("input", (event) => {
      if (event.target?.name === "valid_from") {
        const basisInput = event.target.closest("form.undated-promote-form")?.querySelector('input[name="time_basis"]');
        if (basisInput) basisInput.value = "USER_SELECTED";
      }
    });

    zone.addEventListener("submit", async (event) => {
      const form = event.target;
      if (!form?.classList?.contains("undated-promote-form")) return;
      event.preventDefault();
      const errEl = form.querySelector(".undated-promote-error");
      const submitBtn = form.querySelector('button[type="submit"]');
      const data = Object.fromEntries(new FormData(form));
      try { window.localStorage.setItem("linkloom.tasks.reviewer", String(data.reviewer_id || "").trim()); } catch { /* Optional */ }
      if (errEl) { errEl.textContent = ""; errEl.hidden = true; }
      if (submitBtn) submitBtn.disabled = true;
      try {
        const res = await fetch(`/api/decisions/undated/${encodeURIComponent(form.dataset.id)}/promote`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            valid_from: data.valid_from,
            time_basis: data.time_basis || "USER_SELECTED",
            reviewer_id: data.reviewer_id,
            reason: data.reason,
          }),
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload?.error?.message || t("Could not promote undated decision.", "补充生效日期失败，请检查输入。"));
        document.querySelector('.primary-nav [data-screen="decisions"]')?.click();
      } catch (err) {
        if (errEl) { errEl.textContent = err.message; errEl.hidden = false; }
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });

    const listEl = zone.querySelector("#undated-zone-list");
    const countEl = zone.querySelector("#undated-zone-count");
    try {
      const res = await fetch("/api/decisions/undated");
      if (!res.ok) throw new Error("LOAD_FAILED");
      const data = await res.json();
      if (!zone.isConnected) return;
      const items = data.undated || [];
      if (countEl) countEl.textContent = String(items.length);
      if (listEl) listEl.innerHTML = renderUndatedCards(items);
    } catch {
      if (zone.isConnected && listEl) {
        if (countEl) countEl.textContent = "!";
        listEl.innerHTML = `<p class="error-inline">${escapeHtml(t("Could not load undated decisions.", "无法读取时间未知决议列表。"))}</p>`;
      }
    }
  }

  /* ── 3. Ask Workbench: Truthful Execution & Trace Integration (no DOM JSON.parse) ── */
  function formatUsage(usage) {
    if (!usage || typeof usage !== "object") return t("Not reported", "未报告");
    const inp = usage.input_tokens ?? usage.reported_input_tokens ?? usage.prompt_tokens;
    const out = usage.output_tokens ?? usage.reported_output_tokens ?? usage.completion_tokens;
    const tot = usage.total_tokens ?? usage.reported_total_tokens;
    if (inp == null && out == null && tot == null) return t("Not reported", "未报告");
    return `${inp ?? "?"} / ${out ?? "?"}${tot != null ? ` (total ${tot})` : ""}`;
  }

  function renderAskTraceFromResult(result, target) {
    const answerCard = target?.querySelector?.(".ask-answer");
    if (!result || !answerCard) return;

    // Remove any previous injected strip/drawer on re-render
    answerCard.querySelector(".trace-drawer")?.remove();
    answerCard.querySelector(".trace-execution-strip")?.remove();

    const exec = result.execution || null;
    const routerCalls = exec?.router_provider_calls;
    const answerCalls = exec?.answer_provider_calls;
    const hasExec = exec && typeof exec === "object";
    const totalKnownZero = hasExec && routerCalls === 0 && answerCalls === 0;
    const routerUsageText = hasExec ? formatUsage(exec.router_reported_usage) : t("Not reported", "未报告");

    const metrics = result.metrics || {};
    const reservedUsd = metrics.reserved_upper_bound_usd ?? metrics.cost_reserved_usd ?? null;

    const callBadge = !hasExec
      ? t("Provider calls: Unknown", "模型调用: 未知")
      : totalKnownZero
        ? t("0 model calls", "0 模型调用")
        : t(`Router: ${routerCalls ?? "?"} · Answer: ${answerCalls ?? "?"}`, `Router: ${routerCalls ?? "?"} · Answer: ${answerCalls ?? "?"}`);

    // Reuse existing .ask-trace (GET /api/product/ask/runs/{id}/trace + renderBranchTrace) when present
    const existingTrace = answerCard.querySelector(".ask-trace");
    if (existingTrace) {
      const strip = document.createElement("div");
      strip.className = "trace-execution-strip";
      strip.innerHTML = `
        <div class="trace-summary-grid">
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Answer path", "回答路径"))}</span><strong>${escapeHtml(exec?.answer_path || result.path || "UNKNOWN")}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Router calls", "Router 调用"))}</span><strong>${escapeHtml(routerCalls ?? t("Unknown", "未知"))}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Answer calls", "Answer 调用"))}</span><strong>${escapeHtml(answerCalls ?? t("Unknown", "未知"))}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Router reported usage", "Router 报告用量"))}</span><strong>${escapeHtml(routerUsageText)}</strong></div>
          ${reservedUsd !== null ? `<div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Reserved ceiling", "预留上限"))}</span><strong>${escapeHtml(`$${reservedUsd}`)}</strong></div>` : ""}
        </div>
      `;
      const loadBtn = existingTrace.querySelector('[data-action="load-trace"]');
      if (loadBtn) existingTrace.insertBefore(strip, loadBtn);
      else existingTrace.appendChild(strip);
      return;
    }

    const nodes = [];
    if (result.route) nodes.push({ name: "Router", detail: `${result.route.route || "—"} (${t("calls", "调用")}: ${routerCalls ?? t("Unknown", "未知")})` });
    nodes.push({ name: "Host Gate", detail: result.scope?.subject ? `${result.scope.subject} · ${result.scope.relation || ""}` : t("Workspace scope checked", "已校验工作区范围") });
    if (result.agent_run_id) nodes.push({ name: "Investigation Worker", detail: `run_id: ${result.agent_run_id}` });
    if (result.reviewer) {
      const rev = result.reviewer.decision || (result.reviewer.accepted === true ? "ACCEPTED" : result.reviewer.accepted === false ? "REJECTED" : "—");
      nodes.push({ name: "Reviewer", detail: String(rev) });
    }

    const drawer = document.createElement("details");
    drawer.className = "trace-drawer";
    drawer.innerHTML = `
      <summary>${escapeHtml(t("Execution & Usage Summary", "执行与用量摘要"))} · <span class="badge neutral">${escapeHtml(callBadge)}</span></summary>
      <div class="trace-drawer-body">
        <div class="trace-summary-grid">
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Answer path", "回答路径"))}</span><strong>${escapeHtml(exec?.answer_path || result.path || "UNKNOWN")}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Outcome", "终态"))}</span><strong>${escapeHtml(result.status || "UNKNOWN")}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Router calls", "Router 调用"))}</span><strong>${escapeHtml(routerCalls ?? t("Unknown", "未知"))}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Answer calls", "Answer 调用"))}</span><strong>${escapeHtml(answerCalls ?? t("Unknown", "未知"))}</strong></div>
          <div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Router reported usage", "Router 报告用量"))}</span><strong>${escapeHtml(routerUsageText)}</strong></div>
          ${reservedUsd !== null ? `<div class="trace-stat"><span class="trace-stat-label">${escapeHtml(t("Reserved ceiling", "预留上限"))}</span><strong>${escapeHtml(`$${reservedUsd}`)}</strong></div>` : ""}
        </div>
        <ol class="trace-nodes">
          ${nodes.map(n => `<li><strong>${escapeHtml(n.name)}</strong><span>${escapeHtml(n.detail)}</span></li>`).join("")}
        </ol>
      </div>
    `;
    const technicalEl = answerCard.querySelector(".ask-technical");
    if (technicalEl) answerCard.insertBefore(drawer, technicalEl);
    else answerCard.appendChild(drawer);
  }

  window.onLinkLoomAskResult = (result, target) => {
    renderAskTraceFromResult(result, target);
  };

  /* ── 4. Global Settings Drawer (Workspace, Glossary, Provider) ── */
  let lastFocused = null;

  async function renderSettingsDrawer() {
    if (!settingsBody) return;
    const ws = currentWorkspace();
    const providerText = providerStateEl?.textContent?.trim() || t("Unknown", "未知");
    const providerReady = providerStateEl?.dataset?.ready === "true";

    settingsBody.innerHTML = `
      <section class="settings-section">
        <h3>${escapeHtml(t("Current Workspace", "当前工作区"))}</h3>
        <p class="hint">${escapeHtml(t("All meetings, decisions, tasks and glossary terms are strictly isolated per workspace.", "所有会议、决议、任务与术语均按工作区严格隔离。"))}</p>
        <div class="settings-kv"><span>${escapeHtml(t("Active", "当前工作区"))}</span><strong>${escapeHtml(ws.label)}</strong></div>
        <div class="actions">
          <button type="button" class="button quiet" data-settings-action="new-workspace">${escapeHtml(t("Create new workspace", "新建工作区"))}</button>
        </div>
      </section>

      <section class="settings-section">
        <div class="card-head">
          <h3>${escapeHtml(t("Workspace Glossary", "工作区术语表 (Glossary)"))}</h3>
          <span class="badge ${ws.id ? "" : "neutral"}">${escapeHtml(ws.id ? t("Live API", "已连接") : t("No workspace", "未选择工作区"))}</span>
        </div>
        <p class="hint">${escapeHtml(t("Approved terms help retrieval and Ask disambiguation. They never alter formal decisions.", "经人工确认的术语用于辅助检索与 Ask 消歧，不会修改正式业务决议。"))}</p>
        <div id="settings-glossary-list"><p class="loading">${escapeHtml(t("Loading glossary…", "正在加载术语表…"))}</p></div>
        <form id="settings-glossary-form" class="form-stack settings-glossary-form">
          <div class="field">
            <label for="settings-glossary-term">${escapeHtml(t("New term (2–40 chars)", "新增术语（2–40 字符）"))}</label>
            <input id="settings-glossary-term" name="term" required minlength="2" maxlength="40" placeholder="${escapeHtml(t("e.g. Vendor / 供方", "例如：供方"))}">
          </div>
          <div class="field">
            <label for="settings-glossary-relation">${escapeHtml(t("Maps to relation", "映射关系"))}</label>
            <input id="settings-glossary-relation" name="relation" required value="uses supplier" placeholder="uses supplier">
          </div>
          <div class="field">
            <label for="settings-glossary-actor">${escapeHtml(t("Reviewer name", "操作人姓名"))}</label>
            <input id="settings-glossary-actor" name="actor" required maxlength="128" value="${escapeHtml(localReviewer())}">
          </div>
          <button type="submit" class="button quiet" ${ws.id ? "" : "disabled"}>${escapeHtml(t("Submit term for review", "提交术语待确认"))}</button>
          <p id="settings-glossary-status" class="hint" role="status" aria-live="polite"></p>
        </form>
      </section>

      <section class="settings-section">
        <div class="card-head">
          <h3>${escapeHtml(t("AI Provider Status", "AI 模型连接状态"))}</h3>
          <span class="badge ${providerReady ? "" : "warning"}">${escapeHtml(providerReady ? t("Ready", "已就绪") : t("Read-only / Off", "未配置"))}</span>
        </div>
        <p class="hint">${escapeHtml(providerText)}</p>
      </section>
    `;

    await loadSettingsGlossary(ws.id);
  }

  function localReviewer() {
    try { return String(window.localStorage.getItem("linkloom.tasks.reviewer") || "").trim(); } catch { return ""; }
  }

  async function loadSettingsGlossary(workspaceId) {
    const listEl = document.getElementById("settings-glossary-list");
    if (!listEl) return;
    if (!workspaceId) {
      listEl.innerHTML = `<p class="hint">${escapeHtml(t("Select or create a workspace first.", "请先选择或新建一个工作区。"))}</p>`;
      return;
    }
    try {
      const res = await fetch(`/api/product/feedback?workspace_id=${encodeURIComponent(workspaceId)}`);
      if (!res.ok) throw new Error("LOAD_FAILED");
      const data = await res.json();
      const entries = data.entries || [];
      if (!entries.length) {
        listEl.innerHTML = `<p class="empty-inline">${escapeHtml(t("No approved or pending glossary terms yet.", "当前工作区暂无术语记录。"))}</p>`;
        return;
      }
      const statusMap = {
        pending: t("Pending", "待确认"),
        active: t("Approved", "已批准"),
        revoked: t("Revoked", "已撤销"),
        rejected: t("Rejected", "已拒绝"),
        expired: t("Expired", "已过期"),
      };
      listEl.innerHTML = entries.map(entry => `
        <article class="settings-glossary-item">
          <div class="card-head">
            <strong>${escapeHtml(entry.term)} → ${escapeHtml(entry.relation)}</strong>
            <span class="badge ${entry.status === "active" ? "" : entry.status === "pending" ? "warning" : "neutral"}">${escapeHtml(statusMap[entry.status] || entry.status)}</span>
          </div>
          <p class="hint">${escapeHtml(t("Actor", "操作人"))}: ${escapeHtml(entry.actor || "—")}</p>
          <div class="actions">
            ${entry.status === "pending" ? `<button type="button" class="button quiet" data-glossary-op="approve" data-id="${escapeHtml(entry.id)}">${escapeHtml(t("Confirm", "确认"))}</button><button type="button" class="button quiet" data-glossary-op="reject" data-id="${escapeHtml(entry.id)}">${escapeHtml(t("Reject", "拒绝"))}</button>` : ""}
            ${entry.status === "active" ? `<button type="button" class="button danger" data-glossary-op="revoke" data-id="${escapeHtml(entry.id)}">${escapeHtml(t("Revoke", "撤销"))}</button>` : ""}
          </div>
        </article>
      `).join("");
    } catch {
      listEl.innerHTML = `<p class="error-inline">${escapeHtml(t("Could not load glossary entries from local service.", "无法从本地服务读取术语表。"))}</p>`;
    }
  }

  function openSettings() {
    if (!settingsDialog) return;
    lastFocused = document.activeElement;
    if (typeof settingsDialog.showModal === "function") settingsDialog.showModal();
    else settingsDialog.setAttribute("open", "");
    settingsTrigger?.setAttribute("aria-expanded", "true");
    renderSettingsDrawer();
    settingsClose?.focus?.();
  }

  function closeSettings() {
    if (!settingsDialog) return;
    if (typeof settingsDialog.close === "function" && settingsDialog.open) settingsDialog.close();
    else settingsDialog.removeAttribute("open");
    settingsTrigger?.setAttribute("aria-expanded", "false");
    lastFocused?.focus?.();
  }

  settingsTrigger?.addEventListener("click", openSettings);
  settingsClose?.addEventListener("click", closeSettings);
  settingsDialog?.addEventListener("click", (event) => {
    if (event.target === settingsDialog) closeSettings();
  });
  settingsDialog?.addEventListener("close", () => {
    settingsTrigger?.setAttribute("aria-expanded", "false");
  });

  settingsBody?.addEventListener("click", async (event) => {
    const newWsBtn = event.target.closest('[data-settings-action="new-workspace"]');
    if (newWsBtn) {
      closeSettings();
      document.getElementById("new-workspace")?.click();
      return;
    }
    const opBtn = event.target.closest("[data-glossary-op]");
    if (!opBtn) return;
    const op = opBtn.dataset.glossaryOp;
    const id = opBtn.dataset.id;
    const ws = currentWorkspace();
    const actorInput = document.getElementById("settings-glossary-actor");
    const actor = String(actorInput?.value || localReviewer()).trim();
    const statusEl = document.getElementById("settings-glossary-status");
    if (!actor) {
      if (statusEl) statusEl.textContent = t("Enter your reviewer name first.", "请先填写操作人姓名。");
      actorInput?.focus?.();
      return;
    }
    try { window.localStorage.setItem("linkloom.tasks.reviewer", actor); } catch { /* Optional */ }
    opBtn.disabled = true;
    try {
      const res = await fetch(`/api/product/feedback/${encodeURIComponent(op)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, actor, workspace_id: ws.id, human_confirmed: true }),
      });
      if (!res.ok) throw new Error("OP_FAILED");
      if (statusEl) statusEl.textContent = t("Updated.", "已更新。");
      await loadSettingsGlossary(ws.id);
    } catch {
      if (statusEl) statusEl.textContent = t("Operation failed.", "操作失败，请检查输入或服务状态。");
    } finally {
      opBtn.disabled = false;
    }
  });

  settingsBody?.addEventListener("submit", async (event) => {
    if (event.target?.id !== "settings-glossary-form") return;
    event.preventDefault();
    const ws = currentWorkspace();
    const statusEl = document.getElementById("settings-glossary-status");
    const data = Object.fromEntries(new FormData(event.target));
    try { window.localStorage.setItem("linkloom.tasks.reviewer", String(data.actor || "").trim()); } catch { /* Optional */ }
    try {
      const res = await fetch("/api/product/feedback/propose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...data, workspace_id: ws.id }),
      });
      if (!res.ok) throw new Error("PROPOSE_FAILED");
      event.target.querySelector("#settings-glossary-term").value = "";
      if (statusEl) statusEl.textContent = t("Submitted as pending term.", "已提交为待确认术语。");
      await loadSettingsGlossary(ws.id);
    } catch {
      if (statusEl) statusEl.textContent = t("Could not submit term.", "术语提交失败，请检查长度与格式。");
    }
  });

  /* ── Reactive observers ── */
  function syncEnhancements() {
    renderContextBar();
    enhanceCandidateReviewSplit();
    enhanceDecisionsEvolution();
  }

  let scheduled = false;
  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => {
      scheduled = false;
      syncEnhancements();
    });
  }

  const observer = new MutationObserver(scheduleSync);
  observer.observe(page, { childList: true, subtree: true });
  if (reviewCountEl) observer.observe(reviewCountEl, { childList: true, characterData: true });
  if (workspacePicker) observer.observe(workspacePicker, { childList: true, attributes: true });
  window.addEventListener("hashchange", scheduleSync);
  document.getElementById("interface-language")?.addEventListener("change", () => setTimeout(scheduleSync, 50));

  syncEnhancements();
})();
