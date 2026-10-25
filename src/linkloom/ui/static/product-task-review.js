(() => {
  const REVIEW_PATH = "/api/product/task-review", SUPPORTED = "SUPPORTED", HOST_DISABLED = "LOCAL_TASK_REVIEW_DISABLED";
  const INVALIDATING = new Set(["SOURCE_SNAPSHOT_CHANGED", "SOURCE_CATALOG_UNAVAILABLE", "RUN_NOT_CURRENT_OR_UNAVAILABLE", "SOURCE_OBSERVATION_NOT_CURRENT", "TASK_REVIEW_UNAVAILABLE", "WORKSPACE_MISMATCH", "CAPABILITY_INVALID", "INVALID_ORIGIN", "TASK_REVIEW_SCOPE_MISMATCH", "TASK_REVIEW_RESPONSE_STALE", "SOURCE_EVIDENCE_NOT_CURRENT", "TASK_SOURCE_AUTHORIZATION_CHANGED", "RELATION_OR_SOURCE_CHANGED", "SOURCE_CHANGED"]);
  const OUTCOMES = ["CHECKED", "UNSURE", "DISAGREE"];
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
  const isChinese = language => String(language || "").toLowerCase().startsWith("zh");
  const copy = (language, en, zh) => isChinese(language) ? zh : en;
  const safeId = value => String(value || "").replace(/[^A-Za-z0-9_-]/g, "-");
  function requestId() {
    try {
      if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
        return globalThis.crypto.randomUUID();
      }
    } catch { /* Use a non-secret id below; the Host capability remains authoritative. */ }
    return `review-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }
  function fetcher() {
    if (typeof globalThis.fetch === "function") return globalThis.fetch.bind(globalThis);
    if (typeof window.fetch === "function") return window.fetch.bind(window);
    throw new Error("The local task review service is unavailable.");
  }
  async function jsonRequest(path, options = {}) {
    let response;
    try {
      response = await fetcher()(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      });
    } catch {
      const error = new Error("The local task review service could not be reached.");
      error.code = "TASK_REVIEW_UNREACHABLE";
      throw error;
    }
    let payload = {};
    try { payload = await response.json(); } catch { /* The status is still useful below. */ }
    if (!response.ok) {
      const error = new Error(payload?.error?.message || "The local task review request failed.");
      error.code = payload?.error?.code || "TASK_REVIEW_FAILED";
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }
  function valueFrom(form, selector) {
    const field = form?.querySelector?.(selector);
    return String(field?.value ?? "").trim();
  }
  function sourceText(value) {
    if (!value || typeof value !== "object") return "";
    return value.raw_text || value.provenance?.quote || value.quote || value.text || value.original || "";
  }
  function evidenceText(value) {
    if (!value || typeof value !== "object") return "";
    return value.quote || value.span?.quote || value.text || value.original || "";
  }
  function list(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function reasonOf(view, error) { return String(view?.reason || error?.code || error?.payload?.error?.code || "").toUpperCase(); } function hostDisabled(view, error) { return reasonOf(view, error) === HOST_DISABLED || view?.mode === "DISABLED"; }
  function invalidates(view, error) { return INVALIDATING.has(reasonOf(view, error)) || (error?.status === 403 && !hostDisabled(view, error)); }
  function sourceVerdict(step) { return String(step?.source_verdict || step?.status || "UNKNOWN").toUpperCase(); } function proofStatus(step) { return String(step?.source_proof_status || step?.proof?.status || "BLOCKED").toUpperCase(); }
  function stepStatus(step) { return sourceVerdict(step); } function stepEvidence(step, kind, legacyKind) { return list(step?.[kind] ?? step?.[legacyKind]).map(evidenceText).filter(Boolean); }
  function renderEvidence(language, title, values, empty) { const body = values.length ? values.map(value => `<blockquote class="quote task-review-quote">${escapeHtml(value)}</blockquote>`).join("") : `<p class="hint">${escapeHtml(empty)}</p>`;
    return `<section class="task-review-evidence" data-task-review-evidence="${escapeHtml(title)}"><h4>${escapeHtml(title)}</h4>${body}</section>`;
  }
  function renderOriginal(language, label, observation) {
    const text = sourceText(observation);
    return `<section class="task-review-original"><h4>${escapeHtml(label)}</h4>${text ? `<blockquote class="quote task-review-original-quote">${escapeHtml(text)}</blockquote>` : `<p class="hint">${escapeHtml(copy(language, "No current original was returned.", "当前未返回原始任务文本。"))}</p>`}</section>`;
  }
  function renderHistory(language, history) {
    const rows = list(history).map(item => {
      const action = String(item.action || item.phase || "AUDIT").toUpperCase();
      const status = String(item.status || item.outcome || "").toUpperCase();
      const actor = item.actor_label ? ` · ${escapeHtml(item.actor_label)} (${escapeHtml(copy(language, "self-asserted local identity", "自声明本地身份"))})` : "";
      const note = item.note ? `<p class="hint">${escapeHtml(item.note)}</p>` : "";
      const error = item.error_code ? ` · ${escapeHtml(item.error_code)}` : "";
      const runId = item.run_id || item.current_run_id || item.related_run_id;
      const currentRun = item.current_run === true || (runId && item.current_run === runId);
      const relatedRun = item.current_run === false || item.related_history === true || item.related === true;
      const marker = currentRun ? copy(language, "current run", "当前运行") : relatedRun ? copy(language, "related history", "相关历史") : "";
      const run = runId ? `<span data-task-review-history-run> · ${escapeHtml(copy(language, "run", "运行"))} ${escapeHtml(runId)}</span>` : "";
      const markerHtml = marker ? `<span data-task-review-history-marker> · ${escapeHtml(marker)}</span>` : "";
      return `<li data-task-review-history-item>${escapeHtml(action)}${status ? ` · ${escapeHtml(status)}` : ""}${run}${markerHtml}${actor}${error}${item.revision != null ? ` · revision ${escapeHtml(item.revision)}` : ""}${note}</li>`;
    }).join("");
    return `<details class="task-review-history"><summary>${escapeHtml(copy(language, "Local review history", "本地核验历史"))}</summary>${rows
      ? `<ol>${rows}</ol>`
      : `<p class="hint">${escapeHtml(copy(language, "No local statements yet.", "尚无本地核验声明。"))}</p>`}</details>`;
  }
  function renderForm(language, step) {
    const id = safeId(step.step_id);
    const status = stepStatus(step);
    const checkedDisabled = status !== SUPPORTED;
    const checkedNote = checkedDisabled
      ? copy(language, "CHECKED is available only when the Host reports SUPPORTED; local review cannot upgrade an unknown or conflicting proof.", "只有 Host 返回 SUPPORTED 时才能选择 CHECKED；本地核验不能把未知或冲突证据升级为已支持。")
      : copy(language, "CHECKED records only your local statement; it does not approve a business relationship.", "CHECKED 只记录你的本地核验声明，不批准任何业务关系。");
    return `<form class="form-stack task-review-form" data-task-review-form data-step-id="${escapeHtml(step.step_id)}" aria-label="${escapeHtml(copy(language, "Local review", "人工核验"))}">
      <div class="field"><label for="task-review-actor-${id}">${escapeHtml(copy(language, "Actor label (self-asserted local identity; not a login)", "执行人标识（自声明本地身份；不是登录身份）"))}</label><input id="task-review-actor-${id}" data-task-review-actor name="actor_label" maxlength="160" required placeholder="local-reviewer"></div>
      <div class="field"><label for="task-review-note-${id}">${escapeHtml(copy(language, "Review note", "核验备注"))}</label><textarea id="task-review-note-${id}" data-task-review-note name="note" maxlength="4096" required></textarea></div>
      <div class="field"><label for="task-review-outcome-${id}">${escapeHtml(copy(language, "Local outcome", "本地结论"))}</label><select id="task-review-outcome-${id}" data-task-review-outcome name="outcome">
        ${OUTCOMES.map(outcome => `<option value="${outcome}"${outcome === "CHECKED" && checkedDisabled ? " disabled" : ""}>${escapeHtml(outcome)}</option>`).join("")}
      </select><span class="hint">${escapeHtml(checkedNote)}</span></div>
      <div class="actions"><button class="button" type="submit" data-task-review-action="REVIEW">${escapeHtml(copy(language, "Save local review statement", "保存本地核验声明"))}</button>
        <button class="button quiet" type="submit" data-task-review-action="WITHDRAW">${escapeHtml(copy(language, "Withdraw review statement", "撤回核验声明"))}</button>
        <button class="button quiet" type="submit" data-task-review-action="CONTINUE">${escapeHtml(copy(language, "CONTINUE independent source check", "CONTINUE 独立来源核验"))}</button></div>
    </form>`;
  }
  function renderStep(language, step) {
    const source = sourceVerdict(step);
    const sourceKind = step.source_verdict_source ? String(step.source_verdict_source).toUpperCase() : ""; const proof = proofStatus(step);
    const subject = step.subject || step.task_a || step.originals?.[0];
    const object = step.object || step.task_b || step.originals?.[1];
    const sourceBlockers = list(step.source_blockers).map(String); const proofBlockers = list(step.blockers || step.blocking_reasons).map(String);
    const support = stepEvidence(step, "source_supporting_evidence", "supporting_evidence");
    const counter = stepEvidence(step, "source_counter_evidence", "counter_evidence");
    return `<article class="card task-review-step" data-task-review-step="${escapeHtml(step.step_id)}">
      <div class="card-head"><div><p class="eyebrow">${escapeHtml(step.predicate || step.kind || step.step_id)}</p><h3>${escapeHtml(copy(language, "Local review", "人工核验"))}</h3></div><div class="task-review-statuses"><span class="badge ${source === "SUPPORTED" ? "" : "warning"}" data-task-review-source-verdict="${escapeHtml(source)}">${escapeHtml(copy(language, "Source contract verdict", "来源合同核验"))}: ${escapeHtml(source)}</span>${sourceKind ? `<span class="badge" data-task-review-source-kind="${escapeHtml(sourceKind)}">${escapeHtml(copy(language, "Diagnostic source", "诊断来源"))}: ${escapeHtml(sourceKind)}</span>` : ""}<span class="badge ${proof === "CURRENT_CONFIRMED" ? "" : "warning"}" data-task-review-proof-status="${escapeHtml(proof)}">${escapeHtml(copy(language, "Persistent relation proof", "持久化关系证明"))}: ${escapeHtml(proof)}</span></div></div>
      <p class="hint">${escapeHtml(copy(language, "Business approval: unconfirmed. Human statements do not establish a trusted relation or business approval.", "业务批准：尚未确认。人工声明不会建立可信关系或业务批准。"))}</p>
      <div class="task-review-originals">${renderOriginal(language, copy(language, "Task original A", "任务原文 A"), subject)}${renderOriginal(language, copy(language, "Task original B", "任务原文 B"), object)}</div>
      <div class="task-review-current-evidence">${renderEvidence(language, copy(language, "Source-contract supporting evidence", "来源合同支持证据"), support, copy(language, "No current supporting evidence was returned.", "当前没有返回支持证据。"))}${renderEvidence(language, copy(language, "Source-contract counter-evidence", "来源合同反对证据"), counter, copy(language, "No current counter-evidence was returned.", "当前没有返回反对证据。"))}</div>
      <section class="task-review-source-blockers"><h4>${escapeHtml(copy(language, "Source-contract blockers", "来源合同阻塞项"))}</h4>${sourceBlockers.length ? `<ul>${sourceBlockers.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p class="hint">${escapeHtml(copy(language, "No source-contract blocker reported.", "来源合同未报告阻塞项。"))}</p>`}</section>
      <section class="task-review-proof-blockers"><h4>${escapeHtml(copy(language, "Persistent proof blockers", "持久化证明阻塞项"))}</h4>${proofBlockers.length ? `<ul>${proofBlockers.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p class="hint">${escapeHtml(copy(language, "No persistent-proof blocker reported.", "持久化证明未报告阻塞项。"))}</p>`}</section>
      ${renderForm(language, step)}</article>`;
  }
  function renderDisabled(language, view) {
    const reason = view?.reason || "LOCAL_TASK_REVIEW_DISABLED";
    return `<section id="task-review-panel" class="panel task-review-panel" data-task-review-unavailable role="status">
      <p class="eyebrow">${escapeHtml(copy(language, "Local review", "人工核验"))}</p><h3>${escapeHtml(copy(language, "Local review is unavailable", "人工核验当前不可用"))}</h3>
      <p>${escapeHtml(copy(language, "The localhost Host is not running with --allow-local-task-review. No local statement can be saved and no business approval is available.", "本地 Host 未以 --allow-local-task-review 启动。当前不能保存本地声明，也没有业务批准能力。"))}</p>
      <p class="hint">${escapeHtml(reason)}</p>
    </section>`;
  }
  function renderFailure(language, error) {
    const source = Boolean(error?.sourceInvalidated);
    return `<section id="task-review-panel" class="panel task-review-panel" data-task-review-unavailable role="alert">
      <p class="eyebrow">${escapeHtml(copy(language, "Local review", "人工核验"))}</p><h3>${escapeHtml(copy(language, source ? "The current task result was invalidated" : "Current source review is unavailable", source ? "当前任务结果已失效" : "当前来源核验不可用"))}</h3>
      <p>${escapeHtml(copy(language, source ? "The source, catalog, run or permission changed. Old task evidence was cleared; refresh the current task result before reviewing again." : "Previous review details were cleared. Refresh the current task result before trying again.", source ? "来源、目录、运行记录或权限已变化。旧任务证据已清除；请刷新当前任务结果后再核验。" : "先前的核验详情已清除。请先刷新当前任务结果，再重试。"))}</p>
      <p class="hint">${escapeHtml(error?.code || "TASK_REVIEW_UNAVAILABLE")}</p>
    </section>`;
  }
  function invalidate(context, error) {
    try { context.invalidateResult?.(); } catch { /* The stale result is still cleared below. */ }
    context.target.innerHTML = renderFailure(context.language, {...error, sourceInvalidated: true});
  }
  function clearResult(context) { try { context.invalidateResult?.(); } catch { /* The stale result is still cleared below. */ } context.target.innerHTML = ""; }
  function showUnavailable(context, view, error = view) {
    if (hostDisabled(view, error)) replacePanel(context.target, renderDisabled(context.language, view || {reason: HOST_DISABLED}));
    else if (invalidates(view, error)) invalidate(context, {...error, code: reasonOf(view, error)});
    else replacePanel(context.target, renderFailure(context.language, {...error, code: reasonOf(view, error)}));
  }
  function renderAvailable(language, view, notice = "") {
    const steps = list(view.steps);
    const history = renderHistory(language, view.history);
    return `<section id="task-review-panel" class="panel task-review-panel" data-task-review-panel>
      <div class="card-head"><div><p class="eyebrow">${escapeHtml(copy(language, "Host-owned local review", "Host 管理的本地核验"))}</p><h2>${escapeHtml(copy(language, "Review task relationship evidence", "核验任务关系证据"))}</h2></div><span class="badge warning">${escapeHtml(copy(language, "Unconfirmed", "尚未确认"))}</span></div>
      <p>${escapeHtml(copy(language, "Each statement is a self-asserted local record. It is separate from source proof and never business approval.", "每条声明都是自声明的本地记录，与来源证明分离，永远不等于业务批准。"))}</p>
      ${notice ? `<div class="review-guidance" role="status"><strong>${escapeHtml(notice)}</strong></div>` : ""}
      <div class="task-review-steps">${steps.length ? steps.map(step => renderStep(language, step)).join("") : `<p class="hint">${escapeHtml(copy(language, "No current reviewable relationship steps were returned.", "当前没有返回可人工核验的关系步骤。"))}</p>`}</div>
      ${history}
    </section>`;
  }
  function replacePanel(target, html) {
    const existing = target.querySelector?.("#task-review-panel");
    if (existing && typeof existing.outerHTML === "string") {
      existing.outerHTML = html;
      return;
    }
    target.innerHTML = html;
  }
  function cleanup(target) {
    const current = target.__taskReviewListeners;
    if (!current) return;
    if (typeof target.removeEventListener === "function") {
      target.removeEventListener("submit", current.submit);
      target.removeEventListener("click", current.click);
    }
    delete target.__taskReviewListeners;
  }
  function active(context) {
    return (!context.isCurrent || context.isCurrent())
      && context.target.isConnected !== false
      && context.target.__taskReviewContext === context;
  }
  function actionMessage(language, action, response) {
    if (action === "WITHDRAW") return copy(language, "Local review statement withdrawn; the formal relation lifecycle is unchanged.", "已撤回本地核验声明；正式关系生命周期未改变。");
    if (action === "CONTINUE") {
      if (response?.status === "COMMITTED") return copy(language, "The Host returned a source-proof receipt. Business approval remains unconfirmed.", "Host 返回了来源证明收据；业务批准仍未确认。");
      return copy(language, "Independent source verification did not establish a relation. Review the current blockers and audit status.", "独立来源核验没有建立关系。请查看当前阻塞项与审计状态。");
    }
    return copy(language, "Local review statement saved; this does not approve a business relationship.", "本地核验声明已保存；这不会批准任何业务关系。");
  }
  async function submit(context, view, form, action) {
    const language = context.language;
    const actor = valueFrom(form, "[data-task-review-actor]");
    const note = valueFrom(form, "[data-task-review-note]");
    const outcome = valueFrom(form, "[data-task-review-outcome]");
    if (!actor || !note) {
      replacePanel(context.target, renderAvailable(language, view, copy(language, "Enter your self-asserted actor label and review note first.", "请先填写自声明执行人标识和核验备注。")));
      return;
    }
    if (action === "REVIEW" && outcome === "CHECKED" && stepStatus(view.steps.find(step => step.step_id === form.dataset.stepId)) !== SUPPORTED) {
      replacePanel(context.target, renderAvailable(language, view, copy(language, "CHECKED is only allowed for a Host-supported step; the local statement cannot upgrade proof.", "只有 Host 支持的步骤才能选择 CHECKED；本地声明不能升级证明状态。")));
      return;
    }
    const payload = {
      workspace_id: context.workspaceId,
      run_id: context.runId,
      step_id: form.dataset.stepId,
      action,
      request_id: requestId(),
      capability: view.capability,
      expected_revision: view.revision,
      actor_label: actor,
      note,
    };
    if (action === "REVIEW") payload.outcome = outcome;
    try {
      const response = await jsonRequest(REVIEW_PATH, {method: "POST", body: JSON.stringify(payload)});
      if (!active(context)) return;
      const next = response.review;
      if (!next || next.workspace_id !== context.workspaceId || next.run_id !== context.runId) {
        throw Object.assign(new Error("The Host did not return a current review view."), {code: "TASK_REVIEW_RESPONSE_STALE"});
      }
      context.view = next;
      if (next.available) {
        replacePanel(context.target, renderAvailable(language, next, actionMessage(language, action, response)));
        bind(context.target, context);
      } else {
        showUnavailable(context, next);
        cleanup(context.target);
      }
    } catch (error) {
      if (!active(context)) return;
      const next = error.payload?.review;
      if (next && next.workspace_id === context.workspaceId && next.run_id === context.runId) {
        context.view = next;
        const blockers = list(error.payload.blockers).map(String);
        const notice = actionMessage(language, action, error.payload)
          + (blockers.length ? ` ${copy(language, "Blockers", "阻塞项")}: ${blockers.join(", ")}.` : "");
        if (next.available) {
          replacePanel(context.target, renderAvailable(language, next, notice));
          bind(context.target, context);
        } else showUnavailable(context, next, error);
        return;
      }
      if (invalidates(null, error)) invalidate(context, error);
      else replacePanel(context.target, renderFailure(language, error));
    }
  }
  function bind(target, context) {
    cleanup(target);
    const submitListener = event => {
      const form = event.target?.closest?.("[data-task-review-form]") || (event.target?.dataset && Object.prototype.hasOwnProperty.call(event.target.dataset, "taskReviewForm") ? event.target : null);
      if (!form) return;
      event.preventDefault();
      const action = String(event.submitter?.dataset?.taskReviewAction || "REVIEW").toUpperCase();
      if (!context.view?.capability) return;
      return submit(context, context.view, form, action);
    };
    const clickListener = event => {
      const button = event.target?.closest?.("[data-task-review-action]");
      if (!button || button.type === "submit") return;
      const form = button.closest?.("[data-task-review-form]");
      if (form) return submit(context, context.view, form, String(button.dataset.taskReviewAction).toUpperCase());
    };
    target.addEventListener?.("submit", submitListener);
    target.addEventListener?.("click", clickListener);
    target.__taskReviewListeners = {submit: submitListener, click: clickListener};
  }
  async function mountTaskReview(context) {
    const target = context?.target;
    if (!target || !context.workspaceId || !context.runId) return;
    const language = context.language || "en";
    cleanup(target);
    target.__taskReviewContext = context;
    const existing = target.querySelector?.("#task-review-panel");
    if (existing && typeof existing.remove === "function") existing.remove();
    else if (!target.querySelector) target.innerHTML = "";
    target.insertAdjacentHTML?.("beforeend", `<section id="task-review-panel" class="panel task-review-panel" data-task-review-loading role="status"><p class="loading">${escapeHtml(copy(language, "Loading current local review…", "正在加载当前人工核验…"))}</p></section>`);
    const query = `?workspace_id=${encodeURIComponent(context.workspaceId)}&run_id=${encodeURIComponent(context.runId)}`;
    try {
      const payload = await jsonRequest(`${REVIEW_PATH}${query}`);
      if (!active(context)) return;
      const view = payload.review || payload;
      if (view.workspace_id !== context.workspaceId || view.run_id !== context.runId) {
        replacePanel(target, renderFailure(language, {code: "TASK_REVIEW_SCOPE_MISMATCH"}));
        return;
      }
      context.view = view;
      if (view.available) { replacePanel(target, renderAvailable(language, view)); bind(target, context); }
      else showUnavailable(context, view);
    } catch (error) {
      if (!active(context)) return;
      if (hostDisabled(null, error)) replacePanel(target, renderDisabled(language, {reason: error.code}));
      else if (invalidates(null, error)) invalidate(context, error);
      else replacePanel(target, renderFailure(language, error));
    }
  }
  window.mountTaskReview = mountTaskReview;
})();
