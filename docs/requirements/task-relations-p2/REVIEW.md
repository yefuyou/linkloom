# M2-P2 independent review

Review date: 2026-10-18
Scope: read-only review of the P2 relation context, work-proof, and change-authority implementation against `M2_RELATION_DESIGN.md` and the Dev-only `M2_EVAL_SPEC.md`. No production code, tests, fixtures, vault, provider, holdout, or database state was changed. This review adds only this file.

## Result

**PASS — no REQUIRED findings remain in the bounded follow-up review.** The three initial findings below were fixed and independently reprobed successfully. The original `Needs changes` result is preserved as review history under “Initial findings.”

### Initial findings

**Initial result: Needs changes.** The published Dev cases and the authority checks passed, but three independent synthetic probes exposed correctness failures that the then-current acceptance cases did not cover: omitted-range project context, nested-heading scope handling, and repeated-transition uniqueness.

## Verification performed

- `python scripts/run_relation_checks.py review-independent-p2 tests/unit/test_task_relations_p2.py tests/unit/test_task_change_authority.py tests/integration/test_task_relations_p2_dev.py` → **50 passed**.
- Replayed the existing P1 observation helper and compared serialized observation payloads and IDs with the P1 delivery evidence; the IDs remained unchanged.
- Used fresh synthetic `RawArtifact` values in workspace `ws-review` and an in-memory `SourceReferenceRegistry`; no provider, holdout, real vault, or database write was used.

## Required findings

### R1 — `[REQUIRED]` A narrow proposal can borrow an unsubmitted heading and declaration

Location: `src/linkloom/semantic_ingestion/task_relation_context.py:78-117`, especially the use of `span.quote` at lines 91–101 and the proof constructed at line 115.

Reproduction source:

```text
# 星河项目
本节任务仅属于星河项目。
签名参数适配任务，计划在2026年10月18日前完成。
```

The only relation proposal supplied to `assess_relation(IN_PROJECT, ...)` was an exact `EvidenceQuote` for the last task line. The quote's canonical source span was registered and current, but its submitted character range contained neither the heading nor the declaration. The observation had `project=None`.

Actual result: `SUPPORTED`, with a `PROJECT_BINDING` supporting quote spanning `# 星河项目\n本节任务仅属于星河项目。\n`, even though that range was not supplied as proposal evidence.

Expected result: the relation must remain `UNKNOWN`/`NOT_ASSESSABLE`, or the caller must supply the heading/declaration ranges explicitly. A source snapshot being readable does not make omitted proof ranges part of the submitted proposition. The current implementation scans the complete canonical `span.quote` and manufactures a larger proof from `heading.start()` to the task offset, so a cherrypicked task quote can obtain project context that was never proposed. This violates the separate project-binding evidence requirement and can create false `IN_PROJECT` confirmations.

### R2 — `[REQUIRED]` Nested heading scope ends too early

Location: `src/linkloom/semantic_ingestion/task_relation_context.py:91-99`.

Reproduction source:

```text
# 星河项目
本节任务仅属于星河项目。
## 接口子区
签名参数适配任务，计划在2026年10月18日前完成。
# 天河项目
其他任务。
```

With full current source context supplied, the task under `## 接口子区` returned `UNKNOWN` for `IN_PROJECT → 星河项目`.

Expected result: `SUPPORTED`. The design defines a heading scope as ending at the next heading of the same or higher level. The `# 星河项目` scope therefore remains active through the nested `## 接口子区`; only the later `# 天河项目` closes it. The implementation selects the latest prior heading (`prior[-1]`) and then treats the next heading of any level as terminating the scope (`following = next(...)`), so a nested subheading hides the parent project binding and causes a false negative.

### R3 — `[REQUIRED]` Repeated transitions in one source sentence bypass unique-change validation

Location: `src/linkloom/semantic_ingestion/task_work_proofs.py:87-94`.

Synthetic source artifacts:

```text
A.md: 星河项目供应商由杉林数据改为远川服务，星河项目供应商由杉林数据改为远川服务。
B.md: 我负责星河项目的接口适配；因为A.md记载的从杉林数据改用远川服务，此任务必须调整签名参数。
```

The `ChangeRef` was a source claim for the full A.md span and the proposal was the full B.md span. `assess_relation(AFFECTED_BY, ...)` returned `SUPPORTED` with `EXPLICIT_DIRECT_CHANGE_IMPACT`.

Expected result: `UNKNOWN` because the source claim does not identify one unique concrete supplier transition. The implementation checks `sum(bool(re.search(transition, sentence)) ...)`; `bool(re.search(...))` contributes one for a sentence regardless of whether the transition occurs once or twice. The direct-impact bridge therefore accepts an ambiguous repeated transition and publishes a false causal relation. The same occurrence-vs-sentence-count issue is present in the local-demonstrative branch at lines 102–104.

## Review boundary

The positive Dev scores, authority readback, zero assessment-time SQL writes, P1 serialized IDs, and M1/P1 regression results do not cover these omitted-range, nested-heading, or repeated-occurrence probes. No further architecture review was performed.

## Reviewer reflection

The focused acceptance suite is useful for the declared fixtures and confirms the authority boundary, but these probes show that proof-range containment and uniqueness need to be tested at the parser/validator boundary, not only through full-context fixture proposals. The next review should rerun the three probes after the smallest targeted fixes and then repeat the focused acceptance command once.

## Follow-up re-review

Follow-up date: 2026-10-18. The implementation changes were inspected read-only at `task_relation_context.py:82-132` and `task_work_proofs.py:87-105`. The heading validator now requires individually submitted title and scope-declaration quotes, keeps a parent scope through child headings, and evaluates a child preamble separately. The direct-impact validator now counts regex occurrences rather than merely counting sentences.

The same three synthetic probes were rerun:

- **R1 narrow proposal:** supplied only the task-line quote, with no title or declaration quote. Result: `UNKNOWN` (`INSUFFICIENT_RELATION_EVIDENCE`), with no project support evidence.
- **R2 nested heading:** supplied the title, declaration, and task quotes individually. With `# 星河项目` followed by `## 接口子区` and then `# 天河项目`, result: `SUPPORTED` (`EXPLICIT_PROJECT_SCOPE`), with the submitted parent title/declaration as support.
- **R3 repeated transition:** repeated the exact supplier transition twice in one A.md sentence and supplied the direct causal bridge from B.md. Result: `UNKNOWN` (`NO_UNIQUE_DIRECT_CHANGE_BRIDGE`), with no direct-impact support evidence.

All three original findings are resolved by the inspected changes and their independent probes. No REQUIRED finding remains in this bounded re-review scope. No implementation, test, fixture, vault, provider, holdout, or database state was modified by the reviewer.
