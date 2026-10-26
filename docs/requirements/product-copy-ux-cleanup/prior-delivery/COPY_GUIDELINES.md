# Product copy and information hierarchy

Write for the person completing a task. Keep precise boundaries in the interaction
and in one clear status, rather than repeating them in each paragraph.

## Titles and layout

- Use one page title: Sources / 来源, Review / 审核, Decision Memory / 决策记忆, Ask / 提问.
- `heading(title, description?, action?)` creates no eyebrow and no empty description container.
- Add instructions beside the input that needs them. Avoid repeating navigation or button labels.
- Results: answer or actual record count → status → evidence → unresolved questions → next action → details.
- Show one clear notice per uncertainty. A separate AI-field interpretation label is appropriate because fields and relationships have different trust boundaries.
- Remove unused wrappers and spacing. Native `details` holds optional diagnostics; do not hide essential blockers there.

## Status and authority

| Internal value | English | 中文 | Meaning to retain |
|---|---|---|---|
| UNKNOWN | Needs confirmation / Insufficient information | 待确认／资料不足 | Search or evidence is inconclusive; no claim of absence. |
| PARTIAL | Partial results | 部分结果 | Only the displayed, checked part is available. |
| BLOCKED | Cannot continue | 暂时无法继续 | Show the actual blocker and repair action immediately. |
| REFUTED | Refuted by evidence | 证据否定 | Different from missing evidence. |
| CONFLICT | Conflicting evidence | 证据冲突 | Preserve both sides; no unique answer. |
| MODEL_PROPOSED | AI proposal | AI 提案 | Not a human-approved conclusion. |
| EVIDENCE_VALID | Citations checked | 引用已核验 | Quote checking is distinct from interpreting or approving it. |
| STALE | Source changed | 来源已变化 | Revalidate before relying on this decision. |
| NEEDS_REVALIDATION | Needs revalidation | 待复核 | Current evidence needs human review. |
| SUPERSEDED | Replaced | 已替代 | Historical record, with validity dates retained. |
| FAKE / REPLAY execution | Offline demo | 离线演示 | Must remain visible; no claim of real-model evaluation. |

`FAILED` remains the stored extraction state. A saved source with
`PROVIDER_UNAVAILABLE` may display “Saved · extraction pending / 已保存 · 待提取”,
alongside its unavailable-extraction notice. Do not change the underlying status,
invent extracted candidates or claim that failed processing succeeded.

## Sentence style

Use concrete status and action: “Confirm the prerequisite relationship to continue /
需先确认前置关系，才能继续”. Describe the missing evidence when it is known.
Avoid paragraphs built around “does not mean”, “cannot replace”, “不构成”, or
“尚未证明”. These remain legitimate in engineering documentation when needed.

Use “AI” for ordinary setup and investigation copy; “Provider”, “Runtime”, “P4”,
“Reviewer” as a component, raw enum values, startup flags and receipts belong in
technical details. The human reviewer field can still be called “Reviewer / 审核人”.

## Information that stays immediately visible

- External source transmission and unchecked per-question authorization.
- The configured per-question cost limit and time limit when available.
- Missing relationship prerequisite, source/access changes and extraction failure.
- Partial coverage, unclear date roles, AI proposal attribution and unconfirmed effective plan.
- Offline execution. Do not infer “real model used” from a successful local test.
- Historical queries use approvals recorded now, possibly entered after the requested date.

Source lists, complete quotes, field evidence, both supporting and counter-evidence,
source/version hashes and approval receipts must remain accessible. Data, source
names, original quotes and audit IDs are never translated or silently rewritten.

## Localization and verification

Keep English and Chinese together at the literal interface boundary (`uiText`,
`askLabel` / `askCopy`, and the legacy i18n catalog). Never run translation or
normalization over interpolated source data. Match meaning, action and trust
status across languages; avoid literal engineering translations.

Copy assertions may change when wording changes. Keep the same business fixtures,
state outcomes, approval gates, escaping, evidence isolation and date-comparison
checks. A DOM test verifies rendering contracts; only a real browser run can
verify mobile wrapping, visual spacing and actual interactions.
