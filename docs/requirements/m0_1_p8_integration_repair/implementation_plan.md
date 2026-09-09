# Implementation Plan: M0.1 P8 Integration Repair

Status: **COMPLETED AND ACCEPTED — independent Reviewer
`PASS_WITH_FINDINGS`; human accepted on 2026-08-30**.

This plan governed the completed M0.1 Worker boundary in [SPEC.md](SPEC.md).
The accepted implementation preserved the current dirty worktree and used
focused evidence instead of unnecessary production-code churn. No further
M0.1 Worker work or downstream implementation is authorized by this record.

## Governing Boundary

- Parent milestone: `M0 — Production Agent Closeout`.
- Primary Gate advanced: Gate B integration readiness.
- Supporting Gate: Gate A readiness only.
- No Gate completion claim.
- Synthetic, read-only execution only.
- No Provider, model-driven RetrievalAgent, Memory/Context, Eval, Recovery
  redesign, retry framework, commit, push, or PR.

## Pre-Implementation Rule

Before editing, the Worker must:

1. read `AGENTS.md`, the root `SPEC.md`, the frozen Master, this SPEC, this
   plan, and `task.md`;
2. confirm the human explicitly approved this exact M0.1 boundary;
3. record `git status --short`, branch, `HEAD`, and the current diffs for every
   expected file;
4. treat current production/test edits as pre-existing work that must be
   preserved;
5. stop if the current diff changed materially after the Planner snapshot or
   if ownership cannot be resolved inside the exact file boundary.

No `git reset`, `git checkout --`, broad rewrite, or automatic formatter may be
used to recreate a clean baseline.

## Exact Production Files Expected To Change

| File | Existing dirty intent | Allowed M0.1 work |
|---|---|---|
| `src/linkloom/agents/runtime_adapter.py` | Accept ledger/callback, compose retrieval ToolRuntime, inject Coordinator | Verify and minimally complete interface/propagation; keep Adapter composition-only. |
| `src/linkloom/agents/coordinator.py` | Accept injected ToolRuntime, route RetrievalAgent, project ledger, fix usage | Verify one production retrieval runtime and consistent result projection/accounting. |
| `src/linkloom/agents/retrieval_agent.py` | Convert fixed search/read calls to typed ToolCalls through ToolRuntime | Preserve deterministic semantics; complete safe error/usage handling only if a focused test exposes a gap. |

These are the only authorized production files. A Worker may make zero new
production edits if the current dirty implementation already passes all
accepted evidence. Cosmetic rewriting is forbidden.

## Exact Tests Expected To Add Or Modify

### Add/Adopt

`tests/integration/test_retrieval_tool_runtime.py`

Required cases:

- `test_runtime_adapter_migrates_search_read_and_preserves_evidence_contract`;
- `test_search_failure_is_recorded_and_coordinator_fallback_remains_visible`;
- `test_read_failure_is_not_silently_swallowed`;
- `test_runtime_binding_preserves_stale_hash_validation`;
- `test_migrated_runtime_keeps_prechecks_before_executor_and_budget`;
- `test_whitespace_only_read_ref_is_rejected_before_policy_and_executor`;
- add or rename one focused case that proves an explicitly supplied
  `ToolExecutionLedger` and checkpoint callback are used by the Adapter-
  composed ToolRuntime, without a replacement ledger.

### Minimally Modify

`tests/integration/test_handoff_trace.py`

- strengthen
  `test_runtime_multi_agent_persists_state_and_jsonl_trace`;
- prove `RuntimeEngine.start_multi_agent()` reaches completion on the synthetic
  fixture;
- inspect checkpoint history for retrieval pending and terminal ledger states;
- prove final RuntimeState and result artifact contain the same call IDs,
  statuses, run identity, and terminal state;
- preserve trace privacy assertions.

`tests/integration/test_multi_agent_workflow.py`

- retain existing ask/connect behavior assertions;
- assert the accepted deterministic usage totals;
- do not add model-driven, Provider, Memory, or new Agent-role expectations.

No other test file may be modified without a SPEC adjustment. Existing P8
tests below are regression-only.

## Inspection-Only Files

The Worker may read but not modify:

- `src/linkloom/runtime/graph.py`;
- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`;
- `src/linkloom/runtime/checkpoint.py`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/tools/runtime.py`;
- `src/linkloom/tools/ledger.py`;
- `src/linkloom/tools/tool_policy.py`;
- `src/linkloom/agents/model_adapter.py`;
- `src/linkloom/agents/providers/`;
- `src/linkloom/agents/curator_agent.py`;
- `src/linkloom/agents/reviewer_agent.py`;
- Memory, Evaluation, Fixture, Gold, mutation, CLI, and packaging files.

If a failing acceptance test appears to require one of these files, classify
the failure and stop. Do not silently widen M0.1.

## RED Evidence Strategy

The committed baseline defect is real, but the current worktree already
contains an in-progress repair. The Worker must not destroy user work merely
to manufacture RED.

Use this evidence order:

1. Record the static committed-baseline mismatch:

   ```powershell
   git show HEAD:src/linkloom/agents/runtime_adapter.py
   git show HEAD:src/linkloom/runtime/graph.py
   ```

   The committed Engine supplies two keywords that the committed Adapter does
   not accept. The tracked synthetic RuntimeEngine smoke test is the mapped
   regression witness.

2. Run the focused current-worktree tests before any Worker edit. Record the
   exact result as `CURRENT_DIRTY_BASELINE`.

3. Add the missing supplied-ledger/callback and intermediate-checkpoint
   assertions before changing production code. Run them immediately.

4. If they fail, retain the exact failure as actual RED evidence and repair
   only the smallest implicated seam.

5. If they already pass, record `RED_NOT_REPRODUCIBLE_IN_CURRENT_DIRTY_TREE`
   plus the committed source mismatch. Do not introduce a fake failure or edit
   production code for ceremony.

An isolated clean-checkout reproduction is optional only with separate human
approval. It is not required for M0.1 and must not disturb the current worktree.

## Minimal Implementation Order

### Step 0 — Freeze The Worker Boundary

- record branch, `HEAD`, dirty files, and exact pre-existing diffs;
- confirm only the six expected production/test files enter Worker scope;
- confirm the M0.1 docs are accepted by the human.

Checkpoint: no file changed by the Worker yet.

### Step 1 — Capture Focused Baseline

- run the current focused retrieval/runtime tests;
- classify assertion failures separately from Temp ACL/setup errors;
- inspect whether the dirty source already aligns the method signature and
  object propagation.

Checkpoint: `CURRENT_DIRTY_BASELINE` recorded in `task.md`.

### Step 2 — Complete Regression Witnesses First

- adopt the untracked focused integration test file;
- add supplied-ledger/callback identity assertions;
- add intermediate checkpoint and final state/result consistency assertions;
- preserve ask/connect behavior and usage assertions.

Checkpoint: new assertions run before any further production edit; RED or
already-green outcome recorded honestly.

### Step 3 — Minimal Adapter Repair

Only if a focused failure requires it:

- align `RuntimeAgentAdapter.run()` with the existing Engine keyword boundary;
- pass the supplied ledger/callback unchanged to the existing retrieval
  ToolRuntime factory;
- keep VaultReader callback composition in the Adapter;
- do not persist RuntimeState or create another ledger in the Adapter.

Checkpoint: focused Adapter propagation cases pass.

### Step 4 — Minimal Coordinator Repair

Only if required:

- accept/inject one retrieval ToolRuntime;
- pass it into RetrievalAgent;
- report the injected runtime ledger in the result;
- maintain current direct-test fallback compatibility;
- count existing Reviewer policy calls in usage without pretending they are
  represented in the retrieval ledger.

Checkpoint: ask/connect integration and usage cases pass.

### Step 5 — Minimal RetrievalAgent Repair

Only if required:

- preserve fixed `search_notes` then `read_verified_note` sequencing;
- construct typed calls with stable per-task identity;
- call the injected ToolRuntime and consume safe ToolResult values;
- retain safe failures and fallback visibility;
- do not add any model, prompt, context, or termination logic.

Checkpoint: focused error, policy, stale-source, and evidence-contract cases
pass.

### Step 6 — Focused GREEN

Run the exact focused integration slice and record count/duration/result.

Checkpoint: all focused M0.1 assertions pass or the task remains incomplete.

### Step 7 — Existing P8 Regression

Run the exact P8 primitive regression slice. Do not edit primitive files to
force green.

Checkpoint: no M0.1-caused regression in contracts, policy, ledger,
checkpoint, model loop, or provider-neutral contracts.

### Step 8 — Synthetic Production Smoke

Run the exact production-path smoke on synthetic fixtures and inspect final
state/result/trace evidence.

Checkpoint: no real Vault, network, or Provider invocation occurred.

### Step 9 — Full Suite And Boundary Report

Run the full suite once after focused boundaries are green. Classify every
non-pass and update `task.md`. Do not repair unrelated failures.

Checkpoint: Worker stops and hands evidence to an independent Reviewer.

## Test Commands

### Focused Integration Regression

```powershell
python -m pytest -q `
  tests/integration/test_retrieval_tool_runtime.py `
  tests/integration/test_handoff_trace.py::test_runtime_multi_agent_persists_state_and_jsonl_trace `
  tests/integration/test_multi_agent_workflow.py
```

This verifies the concrete M0.1 seam. If Windows Temp ACL prevents fixture
setup, diagnose it before one controlled rerun with a newly named,
workspace-local `--basetemp` path under `.artifacts/`. Do not delete or reuse
an unknown protected directory.

### Existing P8 Regression

```powershell
python -m pytest -q `
  tests/unit/test_tool_contract_models.py `
  tests/unit/test_tool_registry.py `
  tests/unit/test_tool_policy.py `
  tests/unit/test_tool_runtime.py `
  tests/unit/test_tool_ledger_runtime.py `
  tests/unit/test_p82_state_contracts.py `
  tests/unit/test_p83_durable_tool_boundary.py `
  tests/unit/test_p83_fake_model_loop.py `
  tests/unit/test_p84_artifacts.py `
  tests/unit/test_p84_recovery.py `
  tests/unit/test_p84_durable_model_loop.py `
  tests/unit/test_p85_model_provider_contracts.py `
  tests/unit/test_p85_schema_mapping.py `
  tests/unit/test_p85_gemini_api_adapter.py `
  tests/unit/test_p85_wp3_durable_integration.py
```

The Provider tests are contract regressions only. They do not authorize a
network client or M0.2 integration.

### Production Path Smoke

```powershell
python -m pytest -q `
  tests/integration/test_handoff_trace.py::test_runtime_multi_agent_persists_state_and_jsonl_trace `
  tests/integration/test_multi_agent_workflow.py::test_ask_workflow_synthetic `
  tests/integration/test_multi_agent_workflow.py::test_connect_workflow_synthetic
```

Only synthetic fixture paths are permitted.

### Full Suite

```powershell
python -m pytest -q tests --tb=short
```

### Static And Boundary Checks

```powershell
python -m compileall -q src/linkloom tests
git diff --check
git status --short
```

## Full-Suite Interpretation Rules

1. The historical `458 passed, 2 skipped, 8 failed, 7 errors` result is not a
   current baseline and must not be copied as a fresh run.
2. Every current non-pass must be classified as:
   - `M0_1_CAUSED`;
   - `PRE_EXISTING_UNRELATED`;
   - `ENVIRONMENT_SETUP`;
   - `UNKNOWN`.
3. `M0_1_CAUSED` and `UNKNOWN` non-passes block M0.1 acceptance.
4. `PRE_EXISTING_UNRELATED` failures may remain only when file/test/diff
   evidence proves they are outside the six-file M0.1 boundary.
5. `ENVIRONMENT_SETUP` must include the exact setup error and any controlled
   rerun result; it is not silently converted into PASS.
6. Unrelated legacy relation-evaluation or protected Temp ACL failures must not
   expand M0.1 scope.
7. Full-suite red may coexist with a Reviewer decision only when focused/P8
   boundaries are green and every remaining non-pass is explicitly unchanged,
   unrelated, and independently accepted as such.

## Required Evidence From Worker

The Worker handoff must contain:

1. pre-edit branch, `HEAD`, and `git status --short`;
2. exact pre-existing and Worker-added diffs for the six files;
3. committed mismatch evidence and current dirty baseline result;
4. RED or `RED_NOT_REPRODUCIBLE_IN_CURRENT_DIRTY_TREE` evidence;
5. focused integration command and exact result;
6. P8 regression command and exact result;
7. synthetic production-smoke result;
8. full-suite classification;
9. compileall/diff/status results;
10. checkpoint/RuntimeState/result ledger identity evidence;
11. confirmation that production remains deterministic;
12. confirmation that no real Vault, network, Provider, Fixture/Gold,
    unrelated file, commit, push, or PR was touched;
13. remaining Curator/Reviewer ledger limitation and M0.2/M0.3 deferrals.

## Rollback And Preservation Boundary

- Existing dirty changes belong to the current worktree and must not be
  discarded.
- The Worker must use incremental patches and identify which hunks it added.
- No `git reset --hard`, `git checkout --`, broad revert, or directory deletion
  is authorized.
- If a Worker-added hunk must be withdrawn, remove only that known hunk after
  verifying the target file and preserving prior changes.
- No commit is created automatically; Git remains a human boundary after
  independent review.

## Stop Boundary

Stop immediately when:

- a required fix leaves the three production or three test files;
- a new Runtime, ledger, budget, checkpoint, retry, recovery, Provider,
  Memory, Eval, or Agent architecture appears necessary;
- production semantics would become model-driven;
- a real Vault, credential, network call, Fixture, or Gold change is proposed;
- current concurrent edits cannot be safely distinguished from Worker edits;
- focused/P8 evidence cannot prove the seam without widening scope.

If focused, P8, and synthetic smoke evidence pass, stop M0.1. Do not continue
into M0.2 or M0.3.

## Downstream Handoff

### M0.2

M0.2 owns `ModelProviderAdapter.complete()`, `ModelResponse` lifecycle,
production `previous_tool_call`, durable Provider response/error integration,
and any separately approved real-provider smoke.

### M0.3

M0.3 owns model-selected retrieval tools/arguments, observation-driven
continuation, model-driven termination, and RetrievalAgent trajectory
acceptance.

No file or abstraction for either child task is pre-created here.
