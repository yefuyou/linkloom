# Worker Record: Team Decision & Action Seed Semantic Correction

## 1. Status and boundary

DATA Worker revision following the previous independent semantic review FAIL.
Status: **ACCEPTED AS EVAL SEED** after final independent semantic re-review.
The corrected corpus contains 30 cases, six workspaces, and 36 notes; the
canonical Git/LF SHA-256 is
`49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`.
Golden 8 is **FORMALLY FROZEN** in
[GOLDEN_8_FREEZE.md](GOLDEN_8_FREEZE.md). This acceptance does not authorize
or complete M1 production implementation. The historical
correction/re-review evidence below is retained unchanged.
Governing scope: existing SPEC.md / implementation_plan.md plus the user's
explicit Gold Semantic Correction request. Source notes are factual authority.

## 2. Exact changed files

Only this directory:
- dataset.jsonl: existing Gold corrections and global trajectory calibration.
- README.md: evidence, uncertainty, action and metric contracts.
- validate_dataset.py: minimal structural consistency checks and metric names.
- task.md: this evidence/handoff record.

No production/runtime/M0/tests/Eval runner change; no network, credentials,
provider, real vault, commit, push, PR, or unrelated-worktree cleanup.

## 3. Corrected case count

30 existing records changed; zero new cases or workspaces. All 14 mandatory
cases corrected, all 9 non-blocking cases calibrated, all 30 inspected against
the 36 notes. Global trace limits and evidence inventory affect all cases.
The existing 6/6/5/5/4/4 difficulty distribution is preserved.

## 4. Mandatory correction table

Source paths below are relative to workspaces/. Multiple anchors after a
filename refer to separate sections in that file.

| Case | Previous defect | Corrected Gold | Supporting source | Why supported |
|---|---|---|---|---|
| mps-002 | 完成日误填 deadline | deadline=null；描述保留 completed on 2026-01-26 | model_provider_selection/04-action-status.md#completed | 源文给完成日而非截止日 |
| mps-003 | 原型事实引用错位且 discussion 被列 distractor | 原型小 JSON、未测 residency/ops 引用 office-hours；列为 relevant | model_provider_selection/05-office-hours-prototype.md#prototype-observation；#decision-boundary | 实验事实与非批准边界由原文共同支持 |
| mps-004 | 完成日误当 deadline；remaining-gates 单独承载 owner/date | deadline=null；owner/date 使用 action-status blocked/in-progress；保留 readiness/ownership | model_provider_selection/04-action-status.md#completed；#blocked；#in-progress；06-adoption-readiness.md#ownership | 区分已分配 validation 与 proposed follow-up，不虚构 replacement |
| ret-002 | blocked until 日期；虚构 retrieval team | due 2026-02-28 与 security sign-off blocker 分开；未记录 owner=null | retrieval_upgrade/04-cache-security-status.md#blocked-action；#current-status | 日期不解除阻塞；baseline 完成未给责任人 |
| ret-003 | one-query 缺实际观察支持；嵌套引用与 distractor 冲突 | 补 observation 与 decision-boundary，office-hours relevant | retrieval_upgrade/05-query-tuning-office-hours.md#observation；#decision-boundary | 直接支持一个 synonym-heavy query 与非批准 |
| inc-002 | incident team 虚构 owner；partial 被造为 owned in_progress action | 删除制造的任务；保留 production verification partial 于 reasoning | deployment_incident/04-remediation-status.md#current-status；#completion-meaning | 系统验证状态不是独立指派任务 |
| inc-003 | office-hours required claim 无证据；mitigation 引错 section | 补 discussion observation/boundary；mitigation 指向 decision | deployment_incident/03-mitigation-decision.md#decision；06-operations-office-hours.md#decision-boundary | 决策段支持所选方案，讨论段支持未批准变更 |
| inc-004 | blocked until 2026-03-12 | Inez runbook due 日期、blocked on ops review；Tao in_progress；load test unassigned/pending | deployment_incident/04-remediation-status.md#open-actions | 原文明确三个业务对象；未给日期仍 null |
| inc-005 | partial 被造为 incident team owned task | 删除制造的 action/unresolved；partial 状态保留 | deployment_incident/04-remediation-status.md#current-status；#completion-meaning | 不把验证范围局部完成包装为整体完成 |
| aer-003 | pending threshold 放入 rejected；最新状态为 distractor | 只保留 judge gate rejected；threshold pending sign-off 于 reasoning；threshold-review relevant | agent_evaluation_rollout/02-metric-design-review.md#judge-boundary；06-threshold-review.md#proposal-status；#blocker | 明确拒绝 gate 与尚待批准 threshold 两类状态 |
| iti-002 | checksum 旧 in_progress；拆成两任务 | 合并为 checksum test blocked on review；review owner 未记录 | internal_tool_integration/06-integration-readiness.md#current-status；#open-action | 最新状态覆盖旧记录，未声称 review 自身 blocked |
| iti-003 | 同一 checksum 业务对象 action/unresolved 不一致 | 两个投影一致描述 test blocked on review；不造独立 review task | internal_tool_integration/06-integration-readiness.md#current-status；#open-action | 保留同一业务事实及未知责任人 |
| iti-005 | 缺 owner 推断 review/assignment blocked | 不造 action；未决字段 pending、owner=null；retention unassigned；Mira suggested；deadline=null | internal_tool_integration/04-owner-status.md#ownership；06-integration-readiness.md#open-action | pending 表示信息待定，不是 review execution 状态 |
| drm-005 | exception list 引不含该事实的 safety-boundary；整体 null | 当前 90-day policy 与 deletion authorization 明确 No；sign-off 与 exception-list 分列未决 | data_retention_migration/02-retention-review.md#migration-constraint；05-migration-status.md#remaining-work；04-legal-status.md#safety-boundary | 约束、完成状态、授权分别引用各自支持段 |

## 5. Non-blocking calibration

| Case | Result |
|---|---|
| mps-001 | Direct provider answer no longer requires full rejected-option inventory or an extra office-hours claim; rationale anchor added. |
| mps-005 | Supported No plus current Aster A replaces blanket null/insufficient; no future replacement inferred. |
| inc-001 | Reviewed root cause moved to reasoning; decision placeholder N/A documented; root_cause_accuracy replaces decision_accuracy. |
| aer-002 | Added complete scope/threshold evidence to mixed summary; duplicate trace removed; action fields remain source-supported. |
| aer-004 | Added score and proposal-status evidence; false interpretation moved out of rejected business alternatives into reasoning. |
| aer-005 | Separate multilingual scope and unassigned owner retained; no completed slice established; future dates/coverage unknown, not blanket null. |
| iti-004 | Nested rejection anchor included in support pool; reads no longer pretend to cover multiple notes. |
| drm-002 | Completed prep owner absent -> null; completed-work and safety-boundary support full decision summary; Casey only proposed. |
| drm-004 | Policy values now cite decision section; authorization is a known permission boundary, not fabricated separately owned action; duplicate trace removed. |

Other records received only justified global consistency/metric/trace changes.
ret-005 also includes the original owner/date status section; this adds an
existing note to case scope, not a new source note or workspace.

## 6. Global semantic contract

Completion date != deadline; due date != unblock condition; suggested owner !=
assigned owner; pending approval != rejected proposal; readiness partial !=
owned in-progress action. Null owner records no named owner; explicit unassigned
is distinguished where the source states it. No dates or assignments invented.
Preserve reviewed findings separately from approved decisions. Source history
was not rewritten to fit Gold.

## 7. All four L6 cases

- mps-005: known No/current Aster A, expected_outcome success; no speculative future adoption.
- aer-005: known separate scope/unassigned owner, completion not established;
  partial_with_uncertainty does not imply an unsuccessful answer.
- iti-005: missing review owner/deadlines, retention explicitly unassigned, Mira
  only suggested; unresolved fields pending, not blocked execution.
- drm-005: known No to current 90-day policy and deletion authorization; legal
  sign-off blocked and exception-list completion unresolved; final owner/date
  not recorded. Outcome partial_with_uncertainty, not generic null.

## 8. Evidence and relevance

Nested support is included in the top-level evidence pool; referenced notes are
source/relevant, never distractors. Prototype/discussion notes are relevant for
their actual observations and non-approval boundaries, not decision authority.
The pool is not an exhaustive required citation list. Worker re-read source
sections for the 14 corrections, including combined owner/date/status support.

## 9. Trajectory calibration

Preferred: discovery plus one read per relevant note. Alternative omits one read
only when actual returned search section content supplies the missing facts.
All cases retain distinct preferred/alternative examples, final-after-evidence,
and read-after-discovery. Up to three searches and one extra read beyond relevant
note count are allowed; useful evidence acquisition is not a loop merely because
the same tool name recurs. These are synthetic caps, not measured optimums.
No runtime/tool execution behavior was changed.

## 10. Metric-contract calibration

action_item_accuracy compares description/owner/deadline/status; root-cause
correctness has its own name. Empty/non-applicable Gold is N/A, not perfect
recall. Business readiness partial and answer task_success are separate.
Direct decision questions do not demand all rejected alternatives.
Names/contracts only: no metrics or model scores implemented.

## 11. Validator changes

Bounded worker delegated using user-requested Luna max. Scope is only recursive
evidence path/anchor/relevance/inventory consistency, duplicate symbolic traces,
and registering the two contract names. No natural-language entailment checker.

## 12. Validation results

Initial post-data validator: FAIL with 18 unknown-metric errors while the two
new contract names were not yet registered; no blind retry or Gold rollback.
Final validator: exit 0.

```text
PASS: 30 cases, 6 workspaces, 36 notes
PASS: difficulty={1: 6, 2: 6, 3: 5, 4: 5, 5: 4, 6: 4}
PASS: hard_negative_tags=['mentioned_vs_decided', 'planned_vs_completed', 'proposed_vs_assigned', 'stale_vs_current', 'unsupported_claim']
PASS: stale_or_conflict=4; insufficient_or_ambiguous=4
PASS: paths, evidence anchors, trajectory constraints, claims, and metric registry
```

Nine in-memory regression assertions passed: completion dates not deadlines;
due dates not unblock conditions; no fabricated team owners; no manufactured
production-verification tasks; threshold not rejected; single checksum object;
missing review fields not blocked; L6 known negatives retained; four L6 cases.
These assertions check corrected projections, not semantic entailment.
Bounded validator worker reports baseline PASS and five in-memory negative
checks caught: nested wrong anchor, nested distractor, nested ref absent from
top-level pool, preferred/alternative duplicate, alternative/alternative
duplicate. The production validator entry point reaches recursive validation
through _validate_structured_fields. No negative-fixture files were created.

## 13. Whitespace and scope evidence

git diff --check: exit 0, pre-existing CRLF warnings only.
Because this seed is untracked, additional directory scan checked trailing
spaces/tabs and final newline for all .md/.jsonl/.py files: PASS.
36 source-note SHA256 hashes match the pre-edit baseline.
Existing unrelated dirty paths were preserved; no reset/restore/clean performed.

## 14. Programmatic consistency

Independent recursive scan: 30 unique IDs, 6 workspaces, 36 notes; all evidence
paths/anchors and nested membership valid; zero relevant/distractor conflicts
and zero duplicate preferred/alternative arrays. Final independent scan: 488
top-level/nested reference occurrences; all 42 text files pass whitespace and
final-newline checks; validator Python AST parses.

## 15. Golden 8

Proposed only, NOT frozen: mps-001, aer-002, drm-003, inc-004, ret-005, iti-005,
drm-002, aer-005. inc-004 and iti-005 corrected before future consideration.
No M1 implementation or acceptance started.

## 16. Remaining risks and learning reflection

Structural PASS cannot prove semantic entailment or every valid paraphrase.
Trace caps are synthetic and require later empirical calibration under separate
approval. Missing-field answers are corpus-snapshot scoped. Full independent
acceptance review remains required; a diagnostic spot-check is not acceptance.

Luna max read-only diagnostic checked the 14 mandatory cases plus mps-005 and
aer-005 (16 total). It reported three narrow wording defects, now corrected:
ret-002 unresolved description now names security sign-off for isolation and
retention, not a separate release sign-off; aer-005 unresolved description is
ownership only, with unestablished completion left in decision/reasoning;
drm-005 exception-list descriptions now say before any deletion job runs,
not before any job (mapping/dry-run remain permitted). The other 13 checked
cases had no concrete findings in that diagnostic. These three final wording
edits were checked by the Worker and revalidated structurally; they have not
received another independent semantic pass. The diagnostic did not cover the
other 14 cases or provide full trajectory/metric acceptance.

Learning: (1) correct citations require semantic support, not merely valid
anchors; (2) business state and permission are distinct from answer success.
Portfolio evidence: 30 corrected records, 14-case source-backed correction table,
validator command and negative-check evidence. No model quality score claimed.

## 17. Historical Worker handoff before final re-review

Candidate next task only: focused independent DATA re-review, then a separate
decision on freezing Golden 8. Do not proceed automatically to M1.
READY FOR FOCUSED INDEPENDENT DATA RE-REVIEW. Not ACCEPTED AS EVAL SEED.
