# Product copy and information hierarchy

Write for the user's next decision. Explain the result, evidence and next action
with the shortest wording that preserves their meaning. These guidelines govern
presentation, not F/G/Runtime/Reviewer/P4 or authorization rules.

## Page structure

Use one clear page title and the actual controls or records. Add input help only
where the format, permission or choice needs explanation. A page heading never
requires an eyebrow or subtitle. Remove obsolete DOM and spacing with the text.

Show answers before diagnostics: **result → necessary state → source evidence →
next action → expandable details**. State one uncertainty once in the main view;
use details to explain its specific evidence, opposing evidence and gaps.

## Shared terminology

| English | 中文 | Meaning |
|---|---|---|
| Workspace | 工作区 | Data and access scope |
| Sources | 来源 | Imported text and captured versions |
| Review | 审核 | Human review of a candidate |
| Decision Memory | 决策记忆 | Approved decision records and their history |
| Ask | 提问 | Reviewed lookup or source investigation |
| Model suggestion · unconfirmed | 模型建议 · 待确认 | AI interpretation awaiting confirmation |
| Approve and save | 批准并保存 | Explicit human approval and persistence |
| Source changed | 来源已变化 | Evidence needs another review |
| Offline demo | 离线演示 | Preset or replayed responses |

Translate interface text explicitly. Never translate or rewrite a user's source
name, workspace name, quote, owner or entered value as if it were interface copy.
Switching interface language preserves current form text and historical query
date. Re-read detail data; discard old answers and per-request permission.

## State language

| Data state / context | Product expression | Preserve |
|---|---|---|
| UNKNOWN | Unconfirmed / 待确认; insufficient information / 资料不足 | Lack of proof is not absence or refutation. |
| PARTIAL | Partial results / 部分结果 | Keep known records and name remaining checks. |
| REFUTED | Counter-evidence found / 存在否定证据 | Show it separately from UNKNOWN. |
| CONFLICT | Conflicting evidence / 证据冲突 | Keep both sides; do not select a unique conclusion. |
| BLOCKED | Blocked / 暂无法继续 | Show the concrete prerequisite or permission immediately. |
| MODEL_PROPOSED | Model suggestion / 模型建议 | A suggestion stays unapproved even with checked citations. |
| EVIDENCE_VALID | Citations checked / 引用已核验 | Citation validity does not approve the interpretation. |
| STALE / NEEDS_REVALIDATION | Source changed / Needs review | Show currentness prominently, before historical evidence. |
| Open-ended validity interval | No end date / 未设结束日期 | Do not imply the record is currently valid. |

A model suggestion of support/refutation is explicitly attributed to the model;
an existing relationship proof is labelled separately. No wording change may
change which evidence or reviewer outcome releases a result. Counts come from
the actual returned records; never invent a quantity or infer complete coverage.

## Disclosure placement

Keep external data sending, possible fees, per-question authorization, source
changes, access failure and immediate blockers visible beside the relevant action
or result. Cost/request/time ceilings remain visible when provided. Setup commands,
Token details, raw proposals, receipt IDs, hashes and tool traces can be expanded.

Provide repair guidance: add a source, check a quote, clear an incompatible date,
confirm a prerequisite or check a failed run before retrying. Prefer concrete
states over repeated sentences explaining what a result cannot establish.

## Documentation and verification

Describe current capabilities and setup plainly in both READMEs and the demo.
Keep research protocols, experimental conclusions and historical acceptance in
their technical documents. Preserve frozen evidence and original receipts.

Verify both languages, empty/partial/unknown/conflict/blocked states, proposal
attribution, source currentness, workspace isolation, date scope and mobile
layout. Update wording assertions while retaining safety assertions. Actual
browser evidence and DOM tests must be reported separately.
