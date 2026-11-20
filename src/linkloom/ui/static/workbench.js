/* Presentation extensions for the existing product and formal task pages. */
(() => {
  "use strict";

  const language = (en, zh) => document.documentElement.lang.startsWith("zh") ? zh : en;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function settings() {
    const dialog = document.querySelector(".workbench-settings");
    const opener = document.querySelector("[data-open-settings]");
    const picker = document.querySelector("#workspace-picker, #workspace");
    if (!dialog || !opener || !picker) return;
    const switcher = dialog.querySelector("[data-focus-workspace]");
    opener.addEventListener("click", () => {
      dialog.querySelector("[data-workspace-name]").textContent = picker.value
        ? picker.selectedOptions[0]?.textContent
        : language("No workspace selected", "尚未选择工作区");
      switcher.disabled = picker.disabled;
      dialog.showModal();
    });
    switcher.addEventListener("click", () => {
      dialog.close();
      picker.focus();
    });
    dialog.querySelector("#new-workspace")?.addEventListener("click", () => dialog.close());
  }

  function selectedCitation(source, reference) {
    const jump = source?.evidence_jump;
    if (source?.active !== true || jump?.validated !== true
        || source.workspace_id !== reference.workspace || jump.workspace_id !== reference.workspace
        || source.source_id !== reference.id || jump.source_id !== reference.id
        || source.source_version !== reference.version || jump.source_version !== reference.version
        || jump.content_sha256 !== source.content_sha256 || typeof source.content !== "string") return null;
    const spans = (jump.spans || []).filter(span => span.evidence_ref === reference.evidenceRef);
    if (spans.length !== 1) return null;
    const [span] = spans;
    const chars = Array.from(source.content);
    const {char_start: start, char_end: end, quote} = span;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start
        || end > chars.length || chars.slice(start, end).join("") !== quote) return null;
    return {before: chars.slice(0, start).join(""), quote, after: chars.slice(end).join(""),
      lineStart: span.line_start, lineEnd: span.line_end};
  }

  function mountReview(form) {
    const layout = form.parentElement;
    if (!layout?.classList.contains("detail-layout") || layout.classList.contains("workbench-review")) return;
    layout.classList.add("workbench-review");
    form.prepend(element("h2", language("Review candidate", "审核候选")));
    const evidence = layout.querySelector("[data-evidence-source]");
    const opener = evidence?.querySelector('[data-action="inspect-evidence"]');
    if (!opener) return;
    const source = element("section", "", "workbench-original");
    const title = element("h3", language("Original meeting notes", "会议原文"));
    source.setAttribute("aria-label", title.textContent);
    const status = element("p", language("Checking the source version…", "正在核验来源版本…"), "workbench-subtle");
    status.setAttribute("role", "status");
    source.append(title, status);
    evidence.before(source);
    const reference = {...opener.dataset};
    const current = () => form.isConnected
      && document.getElementById("workspace-picker")?.value === reference.workspace;

    async function read() {
      try {
        const response = await fetch("/api/sources/" + encodeURIComponent(reference.id), {cache: "no-store"});
        if (!response.ok) throw new Error("source unavailable");
        const payload = await response.json();
        if (!current()) return;
        const match = selectedCitation(payload.source, reference);
        if (!match) throw new Error("citation unavailable");
        title.textContent = payload.source.name;
        status.textContent = language("Verified lines ", "已核验行 ") + match.lineStart + "–" + match.lineEnd;
        const notes = element("pre", "", "workbench-transcript");
        notes.tabIndex = 0;
        notes.setAttribute("aria-label", title.textContent);
        const mark = element("mark", match.quote);
        mark.tabIndex = -1;
        notes.append(match.before, mark, match.after);
        const jump = element("button", language("Jump to highlighted quote", "定位高亮引文"), "button quiet");
        jump.type = "button";
        jump.addEventListener("click", () => {
          mark.focus({preventScroll: true});
          notes.scrollTop += mark.getBoundingClientRect().top - notes.getBoundingClientRect().top - 36;
          notes.scrollIntoView({block: "nearest"});
        });
        source.append(jump, notes);
        source.classList.add("workbench-original-verified");
        notes.scrollTop = Math.max(0, mark.offsetTop - 36);
      } catch {
        if (!current()) return;
        status.textContent = language(
          "The original source could not be verified. Reopen the quote to check the latest version.",
          "暂时无法核验原文。可通过下方「查看原话」核对最新版本。");
      }
    }
    read();
  }

  function decisionNavigation(heading) {
    if (!heading || heading.nextElementSibling?.classList.contains("evolution-switch")) return;
    const nav = element("nav", "", "evolution-switch");
    nav.setAttribute("aria-label", language("Evolution views", "演变视图"));
    const decision = element("a", language("Decisions", "决议"));
    decision.href = "/#decisions";
    decision.setAttribute("aria-current", "page");
    const tasks = element("a", language("Tasks", "任务"));
    tasks.href = "/tasks?view=tasks";
    nav.append(decision, tasks);
    heading.after(nav);
  }

  function mountDecisionOverview(filter) {
    if (filter.dataset.workbenchReady) return;
    filter.dataset.workbenchReady = "true";
    const grid = filter.parentElement.querySelector(".grid");
    if (!grid) return;
    const current = element("h2", filter.elements.as_of.value
      ? language("Effective on the selected date", "所选日期有效的决议")
      : language("Current decisions", "当前决议"), "workbench-section-heading");
    grid.before(current);
    const affected = [...grid.children].filter(card => card.querySelector(
      '[data-status="STALE"], [data-status="INVALIDATED"], [data-status="NEEDS_REVALIDATION"]'));
    if (affected.length) {
      const attention = element("section", "", "workbench-attention");
      attention.append(element("h2", language("Needs review", "待复核")));
      const cards = element("div", "", "grid");
      cards.append(...affected);
      if (!grid.children.length) grid.append(element("p", language("No current decisions.", "暂无有效的当前决议。"), "empty"));
      attention.append(cards);
      grid.after(attention);
    }

    const undated = element("section", "", "workbench-undated panel");
    undated.append(element("h2", language("Effective date unknown", "生效时间未知")));
    const records = element("div");
    records.id = "workbench-undated-records";
    records.append(element("p", language(
      "The workspace list of confirmed decisions with unknown effective dates is awaiting integration.",
      "已确认但生效时间未知的决议，待接入工作区列表。"), "workbench-subtle"));
    undated.append(records);
    filter.parentElement.append(undated);
  }

  // Kiro can pass the authorized workspace listing once it is available.
  function renderUndated(container, records) {
    const cards = records.map(record => {
      const card = element("article", "", "workbench-undated-card");
      card.append(element("h3", record.subject), element("p", record.value
        ?? language("Value unavailable", "内容不可用")));
      for (const evidence of record.evidence || []) {
        if (evidence.status === "VERIFIED" && evidence.quote) card.append(element("blockquote", evidence.quote, "quote"));
      }
      return card;
    });
    container.replaceChildren(...(cards.length ? cards : [
      element("p", language("No confirmed decisions with unknown effective dates.", "暂无已确认且生效时间未知的决议。"), "workbench-subtle"),
    ]));
  }

  function traceHtml(trace, escape, t) {
    const events = Array.isArray(trace) ? trace.filter(entry => entry?.tool) : [];
    if (!events.length) return "";
    const rows = events.map(entry => `<li><strong>${escape(entry.tool)}</strong>
      ${entry.status ? `<span class="metadata">${escape(entry.status)}</span>` : ""}
      ${entry.reason ? `<p>${escape(entry.reason)}</p>` : ""}</li>`).join("");
    return `<details class="workbench-trace"><summary>${t("Run trace", "运行轨迹")} (${events.length})</summary><ol>${rows}</ol></details>`;
  }

  function product(page) {
    if (location.pathname === "/" && !location.hash) {
      history.replaceState(null, "", location.pathname + location.search + "#meetings");
    }
    let showGlossary = new URLSearchParams(location.search).get("settings") === "glossary";
    document.querySelector("[data-open-glossary]")?.addEventListener("click", event => {
      event.preventDefault();
      document.querySelector(".workbench-settings").close();
      showGlossary = true;
      if (!page.querySelector("#feedback-panel")) document.querySelector('[data-screen="ask"]').click();
      else enhance();
    });

    function enhance() {
      for (const node of document.querySelectorAll("[data-label-en]")) {
        const label = language(node.dataset.labelEn, node.dataset.labelZh);
        if (node.textContent !== label) node.textContent = label;
      }
      const labels = {sources: ["Meetings & review", "会议与审核"],
        decisions: ["Decision & task evolution", "决议与任务演变"], ask: ["Ask", "Ask 工作台"]};
      for (const [screen, label] of Object.entries(labels)) {
        const nav = document.querySelector('.primary-nav [data-screen="' + screen + '"] .nav-label');
        const text = language(...label);
        if (nav && nav.textContent !== text) nav.textContent = text;
      }
      const review = page.querySelector("#candidate-review");
      if (review) mountReview(review);
      const filter = page.querySelector("#decisions-date-filter, #timeline-as-of");
      if (filter) {
        decisionNavigation(page.querySelector(".page-heading"));
        if (filter.id === "decisions-date-filter") mountDecisionOverview(filter);
      }
      const heading = page.querySelector(".page-heading h1");
      if (heading && page.querySelector("#source-create")) heading.textContent = language("Meetings & review", "会议与审核");
      if (heading && page.querySelector("#decisions-date-filter")) heading.textContent = language("Decision evolution", "决议演变");
      if (heading && page.querySelector("#ask-form")) heading.textContent = language("Ask", "Ask 工作台");

      const glossary = page.querySelector("#feedback-panel");
      if (showGlossary && glossary) {
        showGlossary = false;
        glossary.hidden = false;
        glossary.open = true;
        const summary = glossary.querySelector("summary");
        summary.textContent = language("Workspace terminology", "工作区术语");
        const relation = glossary.querySelector("#feedback-relation");
        if (relation && !relation.options.length) {
          glossary.querySelector("#feedback-form").hidden = true;
          glossary.querySelector("#feedback-form").after(element("p",
            language("Approve a decision with a relation before proposing new terms.", "批准含决议关系的记录后，可为该关系提交新术语。"),
            "workbench-subtle"));
        }
        summary.focus();
        glossary.scrollIntoView({block: "start"});
        const url = new URL(location.href);
        url.searchParams.delete("settings");
        history.replaceState(null, "", url);
      }
    }

    // Observe page replacements, not changes inside a form or a streamed answer.
    new MutationObserver(enhance).observe(page, {childList: true});
    new MutationObserver(enhance).observe(document.documentElement, {attributes: true, attributeFilter: ["lang"]});
    enhance();
  }

  window.LinkLoomWorkbench = {renderUndated, traceHtml};
  settings();
  const page = document.getElementById("page");
  if (page) product(page);
})();
