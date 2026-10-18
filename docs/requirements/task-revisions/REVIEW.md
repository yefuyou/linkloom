# M2-P3 independent review

Review date: 2026-10-18
Scope: read-only review of the P3 task-plan revision and deadline-comparison
boundary against `M2_RELATION_DESIGN.md` and `M2_EVAL_SPEC.md`. The review
used only fresh synthetic `RawArtifact` values and in-memory source registries
under `work/m2-p3-task-revisions`. No H01-H04 material, holdout Gold,
Provider, original database, process, vault, production code, tests, or
fixtures was changed. The only review document owned here is this file.

## Result

**PASS — no REQUIRED findings remain in the bounded follow-up review.** The
initial three adversarial probes were fixed and replayed against the current
implementation. The original failures remain recorded below as review
history; this PASS is limited to R1–R3 and the stated compatibility boundary.

The current branch already contains a fix for the earlier “另一个子计划”
probe: a replacement sentence whose subject is another plan now returns
`UNKNOWN/NO_REVISION_EVIDENCE` and comparison `BLOCKED`. That case is recorded
as a passed guardrail below rather than duplicated as a finding.

## Verification performed

- `python -X utf8 work/m2-p3-task-revisions/probe.py` reproduced the range,
  source-update, unknown-year, scope-only, counter-evidence, and forged-identity
  cases.
- `python -X utf8 work/m2-p3-task-revisions/findings_probe.py` reproduced the
  current subject/negation/boundary probes.
- `PYTHONPATH=src python -m pytest -q tests/unit/test_task_revision_compatibility.py`
  passed **2 tests**, including the byte-stable P1/P2 assessment payload check.

## Initial findings (all resolved)

### R1 — `[REQUIRED — RESOLVED]` A revision sentence can escape the submitted observation range

Location: `src/linkloom/semantic_ingestion/task_plan_revision.py:59-62`,
especially `_own_statement`, which checks the sentence start but not its end.

Input artifacts:

```text
A.md: 星河项目任务 REV-14 的接口联调，计划在2026年10月14日前完成。
B.md: 星河项目任务 REV-14 的接口联调，计划在2026年10月18日前完成，现修订其计划，本条替代A.md计划，将原定2026年10月14日前完成改为2026年10月18日前完成。
```

The `B.md` observation's submitted `raw_text` ended immediately after
`2026年10月18日前完成`; the revision tail was outside its raw field range.
The submitted proposal evidence nevertheless covered the complete `B.md`
artifact span, so `_statements` yielded the full sentence. `_own_statement`
accepted it because the sentence start was inside the observation range.

Actual result: `REVISES_PLAN=SUPPORTED`, `changed_fields=[deadline]`, and
`RevisionComparison=COMPUTABLE` with `delta_calendar_days=+4`.

Expected result: the revision must remain unsupported (safe
`UNKNOWN`/`BLOCKED`, with a null delta) unless the complete revision sentence
is included in the observation's submitted range. A proposal must not borrow
its trailing business assertion from a wider artifact quote.

Follow-up result: `_own_statement` now requires the complete asserted sentence
to end inside the M1 raw range. The same probe returned
`REVISES_PLAN=UNKNOWN/NO_REVISION_EVIDENCE` and
`RevisionComparison=BLOCKED` with a null delta.

### R2 — `[REQUIRED — RESOLVED]` “不应替代原计划” still authorizes a revision

Location: `src/linkloom/semantic_ingestion/task_plan_revision.py:112-125`,
where `_self_plan_actions`, `denial`, and `replacement` are combined.

Input artifacts:

```text
A.md: 星河项目任务 REV-18 的接口联调，计划在2026年10月14日前完成。
B.md: 星河项目任务 REV-18 的接口联调；本条就是A.md的同一任务；资料更新，不应替代原计划，将原定2026年10月14日前完成改为2026年10月18日前完成。
```

The sentence explicitly describes a source update and says that it should not
replace the old plan. The current comma-split subject guard sees the empty
clause immediately before `将原定` and lets the sentence through. The denial
pattern does not include `不应替代`; the negative look-behind in
`(?<!不)替代原计划` also misses it because the character immediately before
`替代` is `应`.

Actual result: `REVISES_PLAN=SUPPORTED`, `changed_fields=[deadline]`, and
`RevisionComparison=COMPUTABLE` with `delta_calendar_days=+4`.

Expected result: the explicit counter-statement must block a positive
revision, preferably as `REFUTED` with `PLAN_COUNTER` evidence (and at minimum
never as `SUPPORTED`); comparison must retain a null delta.

Follow-up result: the shared positive-clause guard now excludes modal
negation and trailing-comma counter clauses. The same probe returned
`REVISES_PLAN=UNKNOWN/NO_REVISION_EVIDENCE` and
`RevisionComparison=BLOCKED` with a null delta.

### R3 — `[REQUIRED — RESOLVED]` Conflicting deadline-boundary cues are guessed as `BEFORE`

Location: `src/linkloom/semantic_ingestion/task_revision_comparison.py:130-140`,
where only the nearest post-date clause is inspected and later contradictory
boundary text is ignored.

Input artifacts:

```text
A.md: 星河项目任务 REV-24 的接口联调，截止2026年10月14日前完成，含当日。
B.md: 星河项目任务 REV-24 的接口联调；本条就是A.md的同一任务；现修订其计划，将原定2026年10月14日前完成改为2026年10月18日前完成，本计划替代原计划。
```

`日前完成` supplies an exclusive boundary while `含当日` supplies an
inclusive boundary for the same deadline. The equivalent finite-grammar
variant `计划最晚在2026年10月14日前完成` also projected `BEFORE` in the
focused probe.

Actual result: old and new projections were both `BEFORE`, so the comparison
was `COMPUTABLE` with `delta_calendar_days=+4`.

Expected result: the old boundary is ambiguous/inconsistent and must project
to `UNKNOWN`; the comparison must be `NOT_COMPARABLE` with a null delta. The
date difference must not turn contradictory boundary language into a numeric
revision.

Follow-up result: the boundary projection now retains comma-separated
`含当日` and `最晚在` cues and reports conflicting inclusive/exclusive cues as
`UNKNOWN`. The same probe returned `old_bound=UNKNOWN`,
`RevisionComparison=NOT_COMPARABLE/DEADLINE_BOUND_UNKNOWN_OR_INCOMPATIBLE`,
and a null delta.

## Guardrails that passed

- A source-update-only pair returned `UNKNOWN/NO_REVISION_EVIDENCE` and a
  blocked comparison with a null delta.
- A forged canonical `SAME_TASK` payload with unsupported semantic proof was
  rejected as `NOT_ASSESSABLE/IDENTITY_PREMISE_REPLAY_MISMATCH`.
- Positive revision evidence plus an anchored old-plan counter-evidence pair
  returned `CONFLICT/CONFLICTING_REVISION_EVIDENCE`; comparison was blocked.
- Month/day-only dates returned `NOT_COMPARABLE` with a null delta rather than
  inferring a year or reporting zero.
- Scope-only revision without an explicit unchanged-deadline assertion kept
  the comparison non-comparable with a null delta.
- The current “另一个子计划” probe is now `UNKNOWN/NO_REVISION_EVIDENCE`
  after the `_self_plan_actions` change, so no global-reference finding is
  carried into this report.

## Follow-up re-review

Follow-up date: 2026-10-18. Only the three independent synthetic probes were
replayed after the targeted fixes; no full suite, Gold, Provider, database, or
architecture review was run. R1, R2, and R3 all now preserve the required
non-positive or non-comparable outcomes. The earlier focused compatibility
check remains `2 passed`, including the byte-stable P1/P2 assessment payload
check. The main agent separately reports its focused P3 suite at 44 passed.

No REQUIRED finding remains in this bounded re-review scope. No implementation,
test, fixture, vault, provider, holdout, or database state was modified by the
reviewer.
