# Independent security review: P2-B Host Ledger inspection dependencies

**Reviewer:** independent read-only review agent
**Baseline:** `33390e2eeaad71d215e279b2f724cae672ea4f80`
**Reviewed worktree:** `work/m3d-fix-inspect-ledger-dependency`
**Review scope:** `SPEC.md`, the nine changed production files, the Runtime/Planner/Reviewer cold-read paths, the BEFORE/AFTER evidence, and focused adversarial replays. This review created no production or existing-test changes.

## Verdict

**PASS for the P2-B trust boundary.** No unresolved P1/P2 bypass was found. The two original P2 findings (same-Runtime terminal checkpoint uncertainty and self-authenticated task identity) remain fixed. The follow-up review also closed the forged-plan workspace/snapshot binding gap and the uncertain-query lifecycle gap.

## Findings

### Former P2 — Inspection plan metadata binding: fixed

**Status: FIXED**
**Files/lines:** `src/linkloom/tools/task_relations.py:373-399, 519-527` and `src/linkloom/ui/task_investigation.py:186-240`.

The Host now carries an independently supplied `inspection_snapshot_hash`,
preserves it through `for_run`, and rejects a plan with a foreign workspace,
missing Host snapshot binding, or mismatched plan snapshot before the inspection
can complete. Cold restoration reconstructs `SemanticContext` and compares both
workspace and the canonical `context.source_snapshot_hash` before creating the
replay tools.

The receipt validator still checks the plan ID, step IDs, step workspace, result
hash, arguments and current source spans. The missing independent plan binding
was the reason a foreign/stale plan could previously be made internally
self-consistent before the inspect call.

The old independent replay (no model, trace, or TaskResult authority) used the
existing fixture chain and reproduced the bug before this patch:

```powershell
$env:PYTHONPATH='src'
python work/reviewer-repro/foreign_plan_workspace.py
python work/reviewer-repro/stale_plan_snapshot.py
```

Observed output for both cases:

```text
inspect ok None
query ok None
relation {'inspect_owner': 'COMPLETED'}
```

The first replay replaced only `plan.workspace_id` with `foreign-workspace`; the
second replaced only `plan.snapshot_hash` with a stale digest. The plan's step
workspace and current P4 source remained unchanged, so the old receipt checks
passed and the dependent SAME_TASK row was released.

After the fix, both Runtime mutations fail before producing a successful inspect
record, and both cold-restore mutations raise the saved-plan context error. The
new tests cover both metadata mutations in both paths. This is specifically the
requested foreign Plan/Workspace/hash boundary; ordinary model planner output
still cannot directly set these Host bindings.

### Former P2 — Lifecycle replay of an uncertain query: fixed

**Status: FIXED**
**Files/lines:** `src/linkloom/runtime/context_engineering.py:143-154` and
`src/linkloom/tools/task_relations.py:461-497`.

`record_checkpoint_uncertain` deliberately preserves `status == "completed"`
and the successful result while placing `TOOL_TERMINAL_CHECKPOINT_FAILED` in
`record.error`. The Host query projection now rejects a bound query with an
error marker or a status outside its pending/completed lifecycle, and context
lifecycle replay rejects any completed record carrying an error before replay.
Thus explicit continuation of the same Runtime cannot treat a terminally
uncertain query as durable.

## Rechecks of the two initial findings

### Same-Runtime terminal uncertainty: fixed

`ToolRuntime` now records terminal checkpoint uncertainty on the completed row
and the Host inspection validator rejects any row with `record.error`. The
independent replay:

```powershell
$env:PYTHONPATH='src'
python work/reviewer-repro/terminal_only_inspect.py
```

returned:

```text
inspection error TOOL_TERMINAL_CHECKPOINT_FAILED
inspection_memory completed
query ok None
query_relation {'inspect_owner': 'BLOCKED'}
query_memory completed
durable [('p2b_1', 'completed'), ('p2b_2', 'completed')]
```

The durable and in-memory rows retain the uncertainty marker, so a later
checkpoint cannot launder the result into a trusted inspection.

### Self-authenticated foreign task identity: fixed

`task_relation_task_id` derives the canonical retrieval task from Host
`model_executions`; online, context and cold-read paths pass that identity into
the Reviewer/Host projection. The independent self-consistent pair replay now
returns:

```text
review {'status': 'fail', 'reason': 'task_relation_review_failed'}
```

The fresh-process cold evidence also rejects jointly altered foreign task rows
with `SAVED_RESULT_REVALIDATION_FAILED` and a BLOCKED response.

## Checks that passed

- Independently ran the focused Host/P8.3/P8.4 selection: **49 passed**.
- Replayed terminal uncertainty and foreign task identity after the fixes.
- Independently ran the new focused selection for the two Runtime plan metadata
  cases and uncertain-query lifecycle case: **3 passed**.
- Independently ran the two cold plan-binding cases: **2 passed**.
- Replayed source revoke/version changes, unexecuted/failed records, foreign
  run/task/agent/workspace/sequence, duplicate call/step/receipt, argument,
  result-hash, receipt-id and source-version tampering through the focused Host
  suite.
- Current checked-in evidence records the final related selection as **55
  passed** in `evidence/final-review-regressions.xml`, including Runtime,
  TaskResult projection and semantic review guards.
- Product HTTP AFTER and fresh-process cold success evidence is present under
  `evidence/after/`; the sandbox-only Windows loopback `WinError 10013` from a
  non-elevated local run is not treated as a source failure. The fresh-process
  foreign-task capture is `evidence/after/cold-process-canonical-task.json`.
- No paid model, real vault, remote Git operation, or production/test mutation
  was used by this review.

## Final acceptance decision

**PASS.** The former plan workspace/snapshot and uncertain-query lifecycle
findings are closed and independently rechecked. No unresolved P1/P2 bypass
allowing model-declared completion remains in the reviewed P2-B boundary.
