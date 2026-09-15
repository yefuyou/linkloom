# LinkLoom Product UI V1.1 — Chinese Localization Design Addendum

## Status

**DESIGN_READY.** Gemini 3.1 Pro produced this direction through Antigravity
CLI 1.2.3 before implementation. It extends the accepted **Decision Brief +
Split Source Inspector** direction without redesigning the product.

## 1. Decision and rationale

LinkLoom will support `en-US` and `zh-CN` through one renderer and one product
payload. Localization belongs to the browser presentation layer and the
isolated demo fixture; it must not fork the page, Runtime, Provider,
ToolRuntime, grounding, or `TeamDecisionResult`.

The Chinese experience preserves the existing information hierarchy, surfaces,
evidence relationship, and density. It changes language, CJK typography, and
accessible labels only.

## 2. Locale interaction specification

- A quiet text language control sits in the top bar beside the read-only label.
  It uses `English / 中文`, not flags or unexplained abbreviations.
- Locale resolution order is explicit URL → saved preference → browser
  preference → `en-US`.
- `?lang=zh` and `?lang=zh-CN` canonicalize to `zh-CN`; `?lang=en` and
  `?lang=en-US` canonicalize to `en-US`. Unsupported values fall through
  safely.
- A user-triggered switch updates `?lang=` while preserving other parameters,
  stores only the locale in `localStorage`, sets `<html lang>`, and rerenders
  immediately without starting a new Agent run.
- If the initial query is still the locale's untouched demo default, changing
  language replaces it with the new localized default. User-edited queries
  are never overwritten.
- Dynamic live-provider prose remains in the language returned by that
  provider. A language switch localizes the product chrome, not arbitrary
  business content. Starting the bundled demo from its Chinese default query
  returns the Chinese fixture narrative.
- If URL or storage APIs are unavailable, the UI continues with the resolved
  in-memory locale.

## 3. Content ownership boundary

### Localized UI chrome

Page titles, headings, explanatory copy, buttons, field labels, progress-state
labels, action-status labels, missing-value labels, evidence relationship
labels, tooltips, ARIA labels, and live announcements are owned by the client
localization dictionary.

Stable payload values such as run-stage IDs, action statuses, line numbers,
workspace document counts, and citation ordinals are formatted by locale.
English labels already included by the projection are treated as compatibility
fallbacks, not translated business prose.

### Dynamic claims

Decision values, rationale points, alternatives and reasons, action and
unresolved descriptions, uncertainty statements, owners, workspace names, and
user questions are dynamic content. They are rendered exactly as returned.
Only the bundled demo supplies an explicit `zh-CN` variant of these fields.

### Verbatim source truth

Evidence filenames, paths, quotes, and document lines are never translated.
The Chinese inspector may localize “第 10–12 行” and “支持此结论”, but the
underlying English Markdown content remains byte-faithful source truth.

## 4. Exact bilingual copy inventory

| Semantic key | English | 简体中文 |
|---|---|---|
| page.loading | Opening the project record… | 正在打开项目记录… |
| page.initial | Recover a team decision | 恢复团队决策 |
| page.running | Recovering decision | 正在恢复决策 |
| page.success | Recovered decision | 已恢复决策 |
| page.insufficient | Not enough evidence | 证据不足 |
| page.error | Reconstruction failed | 决策恢复失败 |
| skip | Skip to decision brief | 跳到决策简报 |
| workspace.context | Workspace context | 工作区信息 |
| workspace.fallback | Decision workspace | 决策工作区 |
| workspace.readOnly | Read-only | 只读 |
| workspace.note.one | 1 project note | 1 篇项目记录 |
| workspace.note.many | {count} project notes | {count} 篇项目记录 |
| provenance.trigger | How LinkLoom found this | LinkLoom 如何找到答案 |
| provenance.kicker | Provenance | 来源说明 |
| provenance.title | How the answer was reconstructed | 答案是如何恢复的 |
| initial.eyebrow | Decision recovery · grounded in project records | 决策恢复 · 基于项目记录 |
| initial.title | Find the decision. / See the proof. | 找到最终决定。/ 查看依据。 |
| initial.lede | LinkLoom reconstructs what your team finally decided from project documents, then connects each claim to the record behind it. | LinkLoom 从项目文档中恢复团队最终做出的决定，并把每项结论连接到背后的记录。 |
| initial.structure | Answer structure | 答案结构 |
| initial.decision | The decision | 最终决定 |
| initial.why | Why it won | 为什么这样决定 |
| initial.open | What remains open | 还有什么未确定 |
| query.kicker | Ask this workspace | 向当前工作区提问 |
| query.title | What did the team decide? | 团队最后做出了什么决定？ |
| query.hint | Ask for a final choice, rationale, owner, or unresolved gate. | 可以询问最终选择、决策理由、负责人或尚未解决的关口。 |
| query.label | Decision question | 决策问题 |
| query.readOnly | Source notes remain unchanged. | 源笔记不会被修改。 |
| query.submit | Recover decision | 恢复决策 |
| running.eyebrow | Reconstructing the decision | 正在恢复决策 |
| running.title | Reading the project record, not guessing the answer. | 正在阅读项目记录，而不是猜测答案。 |
| running.copy | LinkLoom is locating the authoritative decision, then checking its rationale, actions, and unresolved gates against the source notes. | LinkLoom 正在定位权威的决策记录，并依据源笔记核对决策理由、行动项和未决关口。 |
| running.question | Your question | 你的问题 |
| running.evidence | Evidence in progress | 正在查找证据 |
| running.basis | Finding the basis for the answer | 正在寻找答案依据 |
| running.reviewing | Currently reviewing | 当前正在核对 |
| step.searching | Searched the workspace | 已搜索工作区 |
| step.reading | Read decision records | 已读取决策记录 |
| step.checking | Checked supporting claims | 已核对支撑结论 |
| step.ready | Answer ready | 答案已就绪 |
| step.state.complete | complete | 已完成 |
| step.state.active | active | 进行中 |
| step.state.pending | pending | 待处理 |
| question | Question | 问题 |
| outcome.approved | Approved · Confirmed | 已批准 · 已确认 |
| outcome.insufficient | Not enough evidence · Unknown | 证据不足 · 未知 |
| outcome.partial | Partial decision · Confirmed record | 部分决策 · 已有记录确认 |
| rationale | Why this decision was made | 为什么这样决定 |
| known | Known from the project record | 项目记录中已确认的信息 |
| alternatives | Alternatives the team rejected | 团队否决的替代方案 |
| actions | What happens next | 接下来要做什么 |
| action | Action | 行动项 |
| owner | Owner | 负责人 |
| due | Due | 截止日期 |
| status | Status | 状态 |
| status.complete | Complete | 已完成 |
| status.inProgress | In progress | 进行中 |
| status.blocked | Blocked | 受阻 |
| status.pending | Pending | 待处理 |
| status.unknown | Unknown | 未知 |
| value.unassigned | Unassigned | 未分配 |
| value.notRecorded | Not recorded | 未记录 |
| uncertainty | Uncertainty | 不确定性 |
| uncertainty.insufficient | Still unresolved | 仍未确定 |
| uncertainty.partial | What is not yet settled | 还有什么未确定 |
| uncertainty.unknown | Not established: | 尚未确认： |
| inspector.label | Evidence source inspector | 证据来源检查器 |
| inspector.close | Close evidence inspector | 关闭证据检查器 |
| inspector.empty | Select a citation to inspect its source. | 选择一个引用，查看对应来源。 |
| inspector.kicker | Source evidence | 来源证据 |
| inspector.supports | Supports: | 支持此结论： |
| inspector.navigation | Evidence citations | 证据引用 |
| inspector.brief | Evidence in this brief | 本简报中的证据 |
| inspector.keys | J / K to navigate | 按 J / K 切换 |
| inspector.quote | Verified source excerpt | 已验证的来源摘录 |
| inspector.lines | Source document lines | 来源文档行 |
| inspector.fallback | Only the verified excerpt is available. | 当前仅有已验证的来源摘录。 |
| error.kicker | Operational failure · not an evidence conclusion | 运行失败 · 这不是证据结论 |
| error.title | LinkLoom could not complete this reconstruction. | LinkLoom 未能完成这次决策恢复。 |
| error.message | The workspace was left unchanged. You can safely try the question again. | 工作区未被修改，你可以安全地重试这个问题。 |
| error.retry | Try this question again | 重新尝试这个问题 |
| error.details | Technical details | 技术详情 |
| empty.kicker | Empty workspace | 空工作区 |
| empty.title | There are no project records to search yet. | 当前没有可供搜索的项目记录。 |
| empty.copy | Choose a workspace containing Markdown project notes, then ask LinkLoom what the team decided. | 请选择一个包含 Markdown 项目笔记的工作区，再询问 LinkLoom 团队最后做出了什么决定。 |
| empty.return | Return to the question | 返回提问 |

Live announcements use the same language, including “LinkLoom 已开始搜索工作
区。”, “答案已就绪，项目记录提供了决策依据。”, “答案已就绪，但证据不足。”
and “LinkLoom 未能完成这次决策恢复。”.

## 5. Chinese `mps-001` demo content

- Default question: `Atlas Lantern 试点最终批准了哪个模型提供商？`
- Decision: `Aster A 是 Atlas Lantern 合成试点及下一阶段集成中获批的模型提供商。`
- Rationale:
  - `相比更快的早期原型，该决定更重视可审计的数据边界和责任明确的运维路径。`
  - `比较记录确认 Aster A 在合成的 eu-north 区域内处理数据，并具备明确的运维升级路径。`
- Rejected alternatives:
  - Borealis B: `该方案需要数据驻留例外，并会增加当前阶段无法接受的运维负担。`
  - Cedar C: `评审未确认可审计的区域承诺，其流式行为也不符合适配器要求。`
- Actions:
  - `本地适配器契约检查`
  - `区域证据验证`
  - `值班运行手册`
- Unresolved:
  - `区域证据验证仍在等待法务确认，因此处于受阻状态。`
  - `值班运行手册仍在编写中。`
- Partial uncertainty: `区域就绪工作尚未完成，当前记录也无法确认整体接入已经完成。`
- Unknown fields: `区域就绪完成情况`, `值班运行手册完成情况`.
- Insufficient question: `Atlas Lantern 试点何时进入生产环境？`
- Insufficient statement: `工作区没有记录已确认的生产切换日期。`

Names, dates, evidence refs, filenames, quotes, and full source lines remain
unchanged.

## 6. Responsive and accessibility behavior

- CJK content uses the existing font stack plus `PingFang SC`, `Microsoft
  YaHei`, `Noto Sans CJK SC`, and system sans fallbacks. Source document text
  keeps the source-oriented mono/sans treatment.
- Chinese body copy uses a slightly more open line height without changing the
  desktop grid or surface system.
- The language control is keyboard operable, exposes its target language in a
  localized accessible name, and retains focus after rerender.
- `<html lang>`, document title, skip link, tooltips, ARIA labels, modal labels,
  and `aria-live` content update with locale.
- Evidence shortcuts remain J/K; only their explanation is localized.
- The 390 × 844 inspector remains an inert closed bottom sheet and a focused,
  trapped modal dialog when opened.

## 7. Component and file impact map

- `static/app.js`: one dictionary, locale resolution/persistence, formatting,
  localized templates, accessible names, and rerender behavior.
- `static/index.html`: localizable loading-shell hooks with English fallback.
- `static/app.css`: compact language control and CJK line-height/font rules.
- `demo_data/mps-001.json`: isolated `zh-CN` question/result variant.
- `demo.py`: accepts localized fixture questions and returns the matching
  locale's result through the existing snapshot projection.
- UI unit/integration tests: locale resolution, source fidelity, Chinese demo
  behavior, all primary states, URL preservation, and accessibility.
- No change to Runtime, Provider adapters, ToolRuntime, grounding ownership, or
  `TeamDecisionResult`.

## 8. Acceptance criteria

1. `?lang=zh` renders the complete Chinese chrome across initial, running,
   success, partial/insufficient, empty, and error states.
2. Switching language is immediate, keyboard accessible, persists the locale,
   preserves other URL parameters, and never starts a run.
3. The Chinese demo query returns localized decision content through the same
   UI payload and renderer.
4. English source filenames, quotes, and document lines are identical before
   and after locale switches.
5. Action statuses, absent owners/deadlines, progress steps, citation
   relationships, titles, tooltips, ARIA labels, and live announcements are
   localized.
6. Desktop and mobile layouts retain the approved hierarchy; the mobile
   inspector focus/inert behavior remains correct.
7. English remains fully supported and existing product tests stay green.

## 9. Screenshot review checklist

- Chinese initial, running, success, insufficient, and error at 1440 × 1024.
- Chinese initial and success/inspector at 390 × 844.
- No CJK clipping, awkward orphan headings, action-table overflow, or top-bar
  collision.
- Decision remains the strongest element; evidence remains visibly linked.
- English source truth is visually distinct and explicitly credible inside the
  Chinese inspector.
- No new dashboard chrome, card grid, gradient, glow, or decorative icon.

## 10. Risks and non-goals

- Mixed Chinese UI and English source text needs deliberate line-height and
  wrapping review.
- Runtime machine translation, LLM translation, locale negotiation at the
  Provider layer, more languages, a new design system, and layout redesign are
  out of scope.
- A live English provider result is not silently translated when the chrome is
  switched to Chinese.

## 11. Gemini screenshot review record

Gemini 3.1 Pro reviewed the real browser captures through Antigravity CLI
conversation `86326f89-5a53-445f-ae66-c832f32f7a53`.

The first localization review confirmed the evidence-first product structure,
verbatim English source treatment, uncertainty hierarchy, and responsive
action reflow. It identified three concrete visual issues:

1. Chinese display headings were falling back to an unsuitable serif style.
2. The mobile result header placed language and provenance controls too close.
3. Chinese rationale copy needed more line-height.

The implementation changed only those areas: Chinese display type now uses the
modern sans stack; result-state mobile utilities occupy a separate header row;
and Chinese rationale/alternative copy has more breathing room. The decision,
evidence inspector, uncertainty treatment, colors, and document-like structure
were deliberately left unchanged.

Gemini's second review returned PASS for CJK display typography, paragraph
density, mobile header clarity, and the preserved evidence/uncertainty
hierarchy. It reported no remaining blocker and concluded: **READY — polished,
structurally sound, and portfolio-ready.**
