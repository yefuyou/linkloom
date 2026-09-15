"use strict";

(function exposeLocalization(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.LinkLoomI18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function buildLocalization() {
  const DEFAULT_LOCALE = "en-US";
  const CHINESE_LOCALE = "zh-CN";

  const MESSAGES = {
    "en-US": {
      "page.loading": "Opening the project record…",
      "page.initial": "Recover a team decision",
      "page.running": "Recovering decision",
      "page.success": "Recovered decision",
      "page.insufficient": "Not enough evidence",
      "page.error": "Reconstruction failed",
      "skip": "Skip to decision brief",
      "workspace.context": "Workspace context",
      "workspace.fallback": "Decision workspace",
      "workspace.readOnly": "Read-only",
      "workspace.opening": "Opening decision workspace…",
      "locale.control": "Language",
      "locale.switchTo": "Switch interface language to Chinese",
      "locale.option": "中文",
      "locale.changed": "Interface language changed to English.",
      "provenance.trigger": "How LinkLoom found this",
      "provenance.short": "Provenance",
      "provenance.kicker": "Provenance",
      "provenance.title": "How the answer was reconstructed",
      "initial.eyebrow": "Decision recovery · grounded in project records",
      "initial.titleLine1": "Find the decision.",
      "initial.titleLine2": "See the proof.",
      "initial.lede": "LinkLoom reconstructs what your team finally decided from project documents, then connects each claim to the record behind it.",
      "initial.structure": "Answer structure",
      "initial.decision": "The decision",
      "initial.why": "Why it won",
      "initial.open": "What remains open",
      "query.kicker": "Ask this workspace",
      "query.title": "What did the team decide?",
      "query.hint": "Ask for a final choice, rationale, owner, or unresolved gate.",
      "query.label": "Decision question",
      "query.readOnly": "Source notes remain unchanged.",
      "query.submit": "Recover decision",
      "running.eyebrow": "Reconstructing the decision",
      "running.title": "Reading the project record, not guessing the answer.",
      "running.copy": "LinkLoom is locating the authoritative decision, then checking its rationale, actions, and unresolved gates against the source notes.",
      "running.question": "Your question",
      "running.evidence": "Evidence in progress",
      "running.basis": "Finding the basis for the answer",
      "running.reviewing": "Currently reviewing",
      "running.reviewingAria": "Currently reviewed source",
      "step.searching": "Searched the workspace",
      "step.reading": "Read decision records",
      "step.checking": "Checked supporting claims",
      "step.ready": "Answer ready",
      "stepState.complete": "complete",
      "stepState.active": "active",
      "stepState.pending": "pending",
      "stage.searching": "searching the workspace",
      "stage.reading": "reading decision records",
      "stage.checking": "checking supporting claims",
      "stage.ready": "preparing the answer",
      "question": "Question",
      "outcome.approved": "Approved · Confirmed",
      "outcome.insufficient": "Not enough evidence · Unknown",
      "outcome.partial": "Partial decision · Confirmed record",
      "result.insufficientFallback": "The workspace does not contain enough evidence to answer this question.",
      "rationale": "Why this decision was made",
      "known": "Known from the project record",
      "alternatives": "Alternatives the team rejected",
      "actions": "What happens next",
      "action": "Action",
      "owner": "Owner",
      "due": "Due",
      "status": "Status",
      "status.completed": "Complete",
      "status.in_progress": "In progress",
      "status.blocked": "Blocked",
      "status.pending": "Pending",
      "status.unassigned": "Unassigned",
      "status.unknown": "Unknown",
      "value.unassigned": "Unassigned",
      "value.notRecorded": "Not recorded",
      "uncertainty": "Uncertainty",
      "uncertainty.insufficient": "Still unresolved",
      "uncertainty.partial": "What is not yet settled",
      "uncertainty.unknown": "Not established:",
      "inspector.label": "Evidence source inspector",
      "inspector.close": "Close evidence inspector",
      "inspector.open": "Open evidence {ordinal}: {location}",
      "inspector.evidence": "Evidence {ordinal}: {location}",
      "inspector.empty": "Select a citation to inspect its source.",
      "inspector.kicker": "Source evidence",
      "inspector.supports": "Supports:",
      "inspector.navigation": "Evidence citations",
      "inspector.brief": "Evidence in this brief",
      "inspector.keys": "J / K to navigate",
      "inspector.quote": "Verified source excerpt",
      "inspector.lines": "Source document lines",
      "inspector.fallback": "Only the verified excerpt is available.",
      "error.kicker": "Operational failure · not an evidence conclusion",
      "error.title": "LinkLoom could not complete this reconstruction.",
      "error.message": "The workspace was left unchanged. You can safely try the question again.",
      "error.networkTitle": "The decision workspace could not be reached.",
      "error.networkMessage": "Your notes were not changed. Check the local LinkLoom server and try again.",
      "error.retry": "Try this question again",
      "error.details": "Technical details",
      "error.request": "The request failed.",
      "error.networkTechnical": "Network request failed.",
      "error.unsupportedState": "This product state is not supported.",
      "empty.kicker": "Empty workspace",
      "empty.title": "There are no project records to search yet.",
      "empty.copy": "Choose a workspace containing Markdown project notes, then ask LinkLoom what the team decided.",
      "empty.return": "Return to the question",
      "announce.started": "LinkLoom started searching the workspace.",
      "announce.running": "LinkLoom is {stage}.",
      "announce.success": "Answer ready. The decision is supported by project evidence.",
      "announce.insufficient": "Answer ready. There is not enough evidence.",
      "announce.error": "LinkLoom could not complete the reconstruction."
    },
    "zh-CN": {
      "page.loading": "正在打开项目记录…",
      "page.initial": "恢复团队决策",
      "page.running": "正在恢复决策",
      "page.success": "已恢复决策",
      "page.insufficient": "证据不足",
      "page.error": "决策恢复失败",
      "skip": "跳到决策简报",
      "workspace.context": "工作区信息",
      "workspace.fallback": "决策工作区",
      "workspace.readOnly": "只读",
      "workspace.opening": "正在打开决策工作区…",
      "locale.control": "界面语言",
      "locale.switchTo": "将界面语言切换为英文",
      "locale.option": "English",
      "locale.changed": "界面语言已切换为中文。",
      "provenance.trigger": "LinkLoom 如何找到答案",
      "provenance.short": "来源说明",
      "provenance.kicker": "来源说明",
      "provenance.title": "答案是如何恢复的",
      "initial.eyebrow": "决策恢复 · 基于项目记录",
      "initial.titleLine1": "找到最终决定。",
      "initial.titleLine2": "查看依据。",
      "initial.lede": "LinkLoom 从项目文档中恢复团队最终做出的决定，并把每项结论连接到背后的记录。",
      "initial.structure": "答案结构",
      "initial.decision": "最终决定",
      "initial.why": "为什么这样决定",
      "initial.open": "还有什么未确定",
      "query.kicker": "向当前工作区提问",
      "query.title": "团队最后做出了什么决定？",
      "query.hint": "可以询问最终选择、决策理由、负责人或尚未解决的关口。",
      "query.label": "决策问题",
      "query.readOnly": "源笔记不会被修改。",
      "query.submit": "恢复决策",
      "running.eyebrow": "正在恢复决策",
      "running.title": "正在阅读项目记录，而不是猜测答案。",
      "running.copy": "LinkLoom 正在定位权威的决策记录，并依据源笔记核对决策理由、行动项和未决关口。",
      "running.question": "你的问题",
      "running.evidence": "正在查找证据",
      "running.basis": "正在寻找答案依据",
      "running.reviewing": "当前正在核对",
      "running.reviewingAria": "当前核对的来源",
      "step.searching": "已搜索工作区",
      "step.reading": "已读取决策记录",
      "step.checking": "已核对支撑结论",
      "step.ready": "答案已就绪",
      "stepState.complete": "已完成",
      "stepState.active": "进行中",
      "stepState.pending": "待处理",
      "stage.searching": "正在搜索工作区",
      "stage.reading": "正在读取决策记录",
      "stage.checking": "正在核对支撑结论",
      "stage.ready": "正在准备答案",
      "question": "问题",
      "outcome.approved": "已批准 · 已确认",
      "outcome.insufficient": "证据不足 · 未知",
      "outcome.partial": "部分决策 · 已有记录确认",
      "result.insufficientFallback": "工作区没有足够证据回答这个问题。",
      "rationale": "为什么这样决定",
      "known": "项目记录中已确认的信息",
      "alternatives": "团队否决的替代方案",
      "actions": "接下来要做什么",
      "action": "行动项",
      "owner": "负责人",
      "due": "截止日期",
      "status": "状态",
      "status.completed": "已完成",
      "status.in_progress": "进行中",
      "status.blocked": "受阻",
      "status.pending": "待处理",
      "status.unassigned": "未分配",
      "status.unknown": "未知",
      "value.unassigned": "未分配",
      "value.notRecorded": "未记录",
      "uncertainty": "不确定性",
      "uncertainty.insufficient": "仍未确定",
      "uncertainty.partial": "还有什么未确定",
      "uncertainty.unknown": "尚未确认：",
      "inspector.label": "证据来源检查器",
      "inspector.close": "关闭证据检查器",
      "inspector.open": "打开证据 {ordinal}：{location}",
      "inspector.evidence": "证据 {ordinal}：{location}",
      "inspector.empty": "选择一个引用，查看对应来源。",
      "inspector.kicker": "来源证据",
      "inspector.supports": "支持此结论：",
      "inspector.navigation": "证据引用",
      "inspector.brief": "本简报中的证据",
      "inspector.keys": "按 J / K 切换",
      "inspector.quote": "已验证的来源摘录",
      "inspector.lines": "来源文档行",
      "inspector.fallback": "当前仅有已验证的来源摘录。",
      "error.kicker": "运行失败 · 这不是证据结论",
      "error.title": "LinkLoom 未能完成这次决策恢复。",
      "error.message": "工作区未被修改，你可以安全地重试这个问题。",
      "error.networkTitle": "无法连接到决策工作区。",
      "error.networkMessage": "你的笔记未被修改。请检查本地 LinkLoom 服务后重试。",
      "error.retry": "重新尝试这个问题",
      "error.details": "技术详情",
      "error.request": "请求失败。",
      "error.networkTechnical": "网络请求失败。",
      "error.unsupportedState": "当前产品状态不受支持。",
      "empty.kicker": "空工作区",
      "empty.title": "当前没有可供搜索的项目记录。",
      "empty.copy": "请选择一个包含 Markdown 项目笔记的工作区，再询问 LinkLoom 团队最后做出了什么决定。",
      "empty.return": "返回提问",
      "announce.started": "LinkLoom 已开始搜索工作区。",
      "announce.running": "LinkLoom 当前进度：{stage}。",
      "announce.success": "答案已就绪，项目记录提供了决策依据。",
      "announce.insufficient": "答案已就绪，但证据不足。",
      "announce.error": "LinkLoom 未能完成这次决策恢复。"
    }
  };

  function normalizeLocale(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "zh" || normalized === "zh-cn" || normalized.startsWith("zh-hans")) return CHINESE_LOCALE;
    if (normalized === "en" || normalized === "en-us" || normalized.startsWith("en-")) return DEFAULT_LOCALE;
    return null;
  }

  function resolveLocale({ urlLocale = null, storedLocale = null, browserLanguages = [] } = {}) {
    return normalizeLocale(urlLocale)
      || normalizeLocale(storedLocale)
      || (browserLanguages || []).map(normalizeLocale).find(Boolean)
      || DEFAULT_LOCALE;
  }

  function t(locale, key, values = {}) {
    const selected = MESSAGES[normalizeLocale(locale) || DEFAULT_LOCALE] || MESSAGES[DEFAULT_LOCALE];
    let message = selected[key] ?? MESSAGES[DEFAULT_LOCALE][key] ?? key;
    Object.entries(values).forEach(([name, value]) => {
      message = message.replaceAll(`{${name}}`, String(value));
    });
    return message;
  }

  function documentCountLabel(locale, count) {
    if (count === null || count === undefined || Number.isNaN(Number(count))) return "";
    const value = Number(count);
    if (normalizeLocale(locale) === CHINESE_LOCALE) return `${value} 篇项目记录`;
    return `${value} project ${value === 1 ? "note" : "notes"}`;
  }

  function locationLabel(locale, start, end) {
    if (normalizeLocale(locale) === CHINESE_LOCALE) {
      return start === end ? `第 ${start} 行` : `第 ${start}–${end} 行`;
    }
    return start === end ? `Line ${start}` : `Lines ${start}–${end}`;
  }

  function displayStatus(locale, status) {
    const key = `status.${String(status || "unknown")}`;
    const known = MESSAGES[normalizeLocale(locale) || DEFAULT_LOCALE]?.[key];
    return known || String(status || t(locale, "status.unknown")).replaceAll("_", " ");
  }

  function supportLabel(locale, value) {
    const text = String(value || "");
    const separator = text.indexOf(":");
    const category = (separator >= 0 ? text.slice(0, separator) : text).trim();
    const suffix = separator >= 0 ? text.slice(separator + 1).trim() : "";
    if (normalizeLocale(locale) !== CHINESE_LOCALE) {
      return category === "Unresolved" ? "Unresolved gates" : text || "Evidence";
    }
    const categories = {
      "Final decision": "最终决定",
      "Rationale": "决策理由",
      "Rejected alternative": "已拒绝的替代方案",
      "Action": "行动项",
      "Unresolved": "未决关口",
      "Uncertainty": "不确定性",
      "Currently reviewing": "当前正在核对",
      "Evidence": "证据"
    };
    const localized = categories[category] || category || "证据";
    return suffix ? `${localized}：${suffix}` : localized;
  }

  function fallbackReason(locale, value) {
    if (normalizeLocale(locale) !== CHINESE_LOCALE) return value || t(locale, "inspector.fallback");
    const known = {
      "Full source preview is unavailable; showing the verified excerpt.": "无法显示完整来源，以下为已验证的摘录。",
      "The source changed after evidence capture; showing the verified excerpt.": "来源在证据采集后发生变化，以下为当时已验证的摘录。",
      "Only the verified excerpt is available.": t(locale, "inspector.fallback")
    };
    return known[value] || value || t(locale, "inspector.fallback");
  }

  return Object.freeze({
    CHINESE_LOCALE,
    DEFAULT_LOCALE,
    MESSAGES,
    displayStatus,
    documentCountLabel,
    fallbackReason,
    locationLabel,
    normalizeLocale,
    resolveLocale,
    supportLabel,
    t
  });
});
