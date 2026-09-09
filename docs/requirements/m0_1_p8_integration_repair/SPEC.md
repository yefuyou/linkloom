# SPEC: M0.1 P8 Integration Repair

Status: **ACCEPTED — independent Reviewer `PASS_WITH_FINDINGS`; human accepted
on 2026-08-30**.

The previous blocking regression that misclassified a successful empty search
as retrieval failure was corrected and independently re-reviewed. Remaining
non-blocking findings are deferred to their separately approved downstream
boundaries. This acceptance closes M0.1 only: it does not complete Gate A or
Gate B and does not authorize M0.2 implementation.

## Parent Milestone

- Parent execution milestone: **M0 — Production Agent Closeout**.
- Parent execution slice: **M0.1 — P8 Integration Repair**.
- Primary canonical product relationship: **Milestone 2 — Can Find**.
- Supporting canonical relationship: **Milestone 5 — Can Act**.
- Governing Master:
  [MASTER_SPEC_V2.md](../linkloom-master/MASTER_SPEC_V2.md).

M0.1 repairs current composition seams so M0.2 can begin safely. It is not a
new product milestone and does not replace
[docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md).

## Master Gate

Primary:

- **Gate B — Real Agent Loop**, integration readiness only.

Supporting:

- **Gate A — Real Model**, readiness only.

M0.1 cannot complete either Gate. Production remains deterministic after this
task, and no real Provider is connected.

## Problem

The committed P8 runtime contains compatible lower-level primitives but an
inconsistent production composition boundary.

At committed `HEAD=3000056`, `RuntimeEngine.start_multi_agent()` already
creates a runtime-owned `ToolExecutionLedger` and checkpoint callback, then
passes them to `RuntimeAgentAdapter.run()`. The committed adapter signature
does not accept those keyword arguments. A synthetic production call can
therefore fail before `Coordinator` starts.

The committed production `RetrievalAgent` also invokes legacy read-tool
wrappers directly. Those calls do not use the ledger and checkpoint callback
created by `RuntimeEngine`, so the final `RuntimeState.tool_ledger` cannot
represent the retrieval lifecycle. Read failures are silently skipped, and
Coordinator usage omits Reviewer tool-policy consumption.

The current dirty worktree contains an in-progress repair for these seams. It
accepts and propagates the ledger/callback, composes one retrieval
`ToolRuntime`, routes deterministic search/read calls through it, projects its
ledger, and adds regression assertions. These edits are useful current
evidence, but they are uncommitted, have no M0.1 test record, and have not been
independently accepted. M0.1 must verify and minimally complete them without
designing a second runtime.

## Current Facts

Facts are separated by evidence class. They must not be merged into one
completion claim.

### Committed Baseline

Committed source at `HEAD=3000056` establishes:

- `RuntimeEngine.start_multi_agent()` creates `ToolExecutionLedger` and
  `persist_tool_ledger`, then calls `RuntimeAgentAdapter.run()` with
  `tool_ledger` and `tool_checkpoint_callback`
  (`src/linkloom/runtime/graph.py:386-452`);
- the committed `RuntimeAgentAdapter.run()` signature ends at
  `injected_memory` and rejects those two keywords;
- the committed `RetrievalAgent.execute()` calls legacy `search_notes()` and
  `read_verified_note()` wrappers directly;
- committed `Coordinator` has no injected `ToolRuntime` and returns no durable
  retrieval ledger;
- committed Reviewer policy calls are absent from total tool-call usage.

This is the reproducible source-level integration defect. The existing
synthetic RuntimeEngine smoke test is its regression witness, even though this
Planner did not destructively reset the dirty worktree to rerun the defect.

### Current Dirty-Worktree Evidence

The current worktree modifies:

- `src/linkloom/agents/runtime_adapter.py` — accepts the runtime-owned ledger
  and callback, builds one retrieval `ToolRuntime`, and injects it into
  `Coordinator`;
- `src/linkloom/agents/coordinator.py` — accepts the injected runtime, routes
  RetrievalAgent through it, projects its ledger, and includes Reviewer policy
  usage;
- `src/linkloom/agents/retrieval_agent.py` — preserves deterministic
  `search -> read -> return` semantics while expressing calls as typed
  `ToolCall` values through `ToolRuntime`;
- `tests/integration/test_handoff_trace.py` and
  `tests/integration/test_multi_agent_workflow.py` — add final-state ledger and
  usage assertions;
- untracked `tests/integration/test_retrieval_tool_runtime.py` — adds focused
  retrieval-runtime, safe failure, stale-source, policy, and schema cases.

Source inspection shows that the suspected keyword-signature mismatch is
repaired in the dirty worktree and that the same supplied ledger/callback is
propagated into the retrieval ToolRuntime. This is **source-level repaired,
not accepted**. No current M0.1 test run has established completeness.

Other dirty trajectory-evaluation files and `src/linkloom.egg-info/SOURCES.txt`
are unrelated concurrent work. M0.1 neither owns nor accepts them.

### Historical Evidence

- [P8.4](../p8_4_durable_model_loop/SPEC.md) records
  `PASS_WITH_FINDINGS` for the isolated durable FakeModel loop.
- [P8.5](../p8_5_real_provider_boundary/task.md) records provider contracts,
  an offline Gemini adapter, and P8.5 Worker corrections, but still requires
  independent re-review.
- P8.5 explicitly records that no Gemini provider is connected to
  `SingleAgentModelLoop`, no production `previous_tool_call` is populated, and
  no real-provider smoke run occurred.
- A historical repository-wide run recorded
  `458 passed, 2 skipped, 8 failed, 7 errors`. It was not rerun by this
  Planner and is not current M0.1 verification.

### Current M0.1 Test Status

`NOT_RUN`. This Planner performed source, test, document, and Git inspection
only. No production test result may be inferred from the dirty patch.

## Current Execution Paths

### Production Multi-Agent Path

The actual current-workspace path is:

```text
CLI agent ask/connect
  -> RuntimeEngine.start_multi_agent
  -> RuntimeAgentAdapter.run
  -> compose trusted VaultReader callbacks
  -> compose one retrieval ToolRuntime with Runtime-owned ledger/callback
  -> Coordinator.run
  -> deterministic RetrievalAgent.execute
  -> search_notes ToolCall
  -> ToolRuntime validation / policy / pending ledger / checkpoint
  -> executor / terminal ledger / checkpoint
  -> one or more read_verified_note ToolCalls through the same runtime
  -> optional Curator legacy deterministic stage (connect only)
  -> Reviewer legacy deterministic stage
  -> Coordinator result + retrieval tool ledger
  -> RuntimeEngine final RuntimeState checkpoint + result artifact + trace
```

Current evidence locations:

- CLI entry: `src/linkloom/cli.py:424-450`;
- runtime composition and checkpoint ownership:
  `src/linkloom/runtime/graph.py:316-547`;
- adapter composition: `src/linkloom/agents/runtime_adapter.py:143-199`;
- Coordinator routing: `src/linkloom/agents/coordinator.py:16-289`;
- deterministic retrieval sequence:
  `src/linkloom/agents/retrieval_agent.py:75-202`;
- tool lifecycle: `src/linkloom/tools/runtime.py:328-595`.

This remains a fixed Python workflow. The model does not select tools,
arguments, continuation, or termination.

### Isolated Model-Loop Path

The isolated path is:

```text
explicit test/caller
  -> FakeModelAdapter + ToolRuntime + ToolPolicyEnforcer
  -> SingleAgentModelLoop.run/resume
  -> rehydrate RuntimeState tool ledger and policy count
  -> write durable ModelTurnRequest artifact/record
  -> ModelAdapter.decide -> ModelAction
  -> write durable normalized response
  -> ToolRuntime.execute or runtime-owned final termination
  -> write observation artifact and RuntimeState checkpoint
  -> next fake-model turn or terminal ModelLoopResult
```

Current evidence locations:

- loop contract and dispatch: `src/linkloom/runtime/model_loop.py:73-156`;
- non-durable request/tool path: `src/linkloom/runtime/model_loop.py:213-337`;
- durable ledger/artifact path: `src/linkloom/runtime/model_loop.py:364-1004`;
- model request/provider contracts: `src/linkloom/agents/model_adapter.py`;
- artifact ownership: `src/linkloom/runtime/artifacts.py`;
- durable state contracts: `src/linkloom/runtime/models.py:765-1152`.

No production source calls `SingleAgentModelLoop`. It still consumes the
legacy `ModelAdapter.decide() -> ModelAction` seam rather than
`ModelProviderAdapter.complete() -> ModelResponse`.

## Integration Drift Classification

| Drift | Current status | Owner |
|---|---|---|
| `RuntimeEngine` passes ledger/callback but committed Adapter rejects them | Dirty source repair exists; current verification missing | M0.1 |
| Committed RetrievalAgent bypasses ToolRuntime and durable ledger | Dirty deterministic ToolRuntime migration exists; current verification missing | M0.1 |
| Coordinator result/final RuntimeState lack retrieval ledger in committed baseline | Dirty projection exists; intermediate/final consistency must be tested | M0.1 |
| Read-tool failures are silently skipped in committed RetrievalAgent | Dirty safe-error/fallback behavior exists; focused regression required | M0.1 |
| Reviewer calls are omitted from Coordinator usage in committed baseline | Dirty accounting repair exists; ask/connect regression required | M0.1 |
| Production path does not call `ModelProviderAdapter.complete()` or persist `ModelResponse` lifecycle | Not implemented | M0.2 |
| Production model requests do not populate `previous_tool_call` | Contract exists; production activation not implemented | M0.2 |
| RetrievalAgent remains fixed `search -> read -> return` | Intentional current behavior | M0.3 |
| Memory refs are appended after execution rather than entering model context | Outside M0 | M2.2 |

Curator and Reviewer still use legacy direct wrappers and are not represented
in the dirty retrieval ledger. M0.1 must report that limitation and must not
claim full ToolRuntime coverage or Gate B completion. Migrating those stages
would expand this repair beyond the inspected retrieval seam; it requires a
separate accepted boundary if later Gate B acceptance proves it necessary.

## Goals

M0.1 must:

1. make the current RuntimeEngine/RuntimeAgentAdapter interface callable;
2. propagate the runtime-owned ledger and checkpoint callback unchanged into
   exactly one production retrieval ToolRuntime;
3. keep deterministic RetrievalAgent search/read behavior while using existing
   typed ToolCall, policy, ledger, checkpoint, and event primitives;
4. preserve one permission/budget authority in `ToolPolicyEnforcer`;
5. project retrieval ledger facts consistently into checkpoints, final
   `RuntimeState`, and the result artifact;
6. surface safe search/read failures without raw exception leakage or silent
   evidence swallowing;
7. keep Coordinator usage internally consistent for the existing ask/connect
   workflows;
8. leave objective focused, P8 regression, and synthetic production-smoke
   evidence for independent review.

## Scope

### A. Current Composition And Interface Consistency

- align `RuntimeEngine.start_multi_agent()` keyword arguments with
  `RuntimeAgentAdapter.run()`;
- preserve `RuntimeEngine` as owner of the current durable RuntimeState and
  checkpoint callback;
- pass the existing ledger/callback through the Adapter without adapting their
  lifecycle or creating a second owner;
- inject one Adapter-composed retrieval ToolRuntime into Coordinator;
- preserve direct Coordinator test compatibility without changing the
  production ownership path.

### B. Existing P8 Primitive Reuse

- reuse `ToolRuntime` for retrieval validation and execution;
- reuse `ToolPolicyEnforcer` for permission and call budget;
- reuse `ToolExecutionLedger` for pending/completed/failed tool facts;
- reuse the existing RuntimeEngine checkpoint callback and `RuntimeState`;
- reuse existing trace/event primitives;
- reuse the existing retrieval runtime factory and registry contracts.

M0.1 may not add a parallel execution, budget, ledger, checkpoint, or trace
implementation.

### C. Deterministic Retrieval Regression Boundary

- retain the current fixed search-first, read-candidates-next order;
- retain current evidence refs, stale-source validation, read-only policy, and
  Coordinator fallback semantics;
- represent each attempted authorized retrieval call with a unique typed call
  identity;
- keep pre-authorization/schema failures from invoking an executor or creating
  a false durable execution record;
- record authorized executor outcomes through the existing ledger lifecycle.

### D. Runtime Projection And Observability

- prove the supplied ledger receives pending and terminal transitions;
- prove checkpoint snapshots and final RuntimeState agree with the ledger;
- prove result artifact ledger identities match the final state;
- prove failure events expose safe normalized errors rather than private paths;
- prove ask/connect usage includes all currently counted deterministic policy
  calls without claiming that every legacy specialist call is durable.

## Non-Goals

M0.1 does not authorize:

- modifying or implementing `GeminiProviderAdapter`;
- connecting `ModelProviderAdapter.complete()`;
- implementing `ModelResponse` lifecycle integration;
- populating production `previous_tool_call`;
- executing a real Provider network call;
- beginning RetrievalAgent model-driven migration;
- changing RetrievalAgent's current business semantics;
- implementing semantic or hybrid RAG;
- implementing Memory -> Model Context;
- implementing Team Decision & Action Agent behavior;
- adding an Agent role;
- expanding the Multi-Agent architecture;
- adding a Runtime abstraction;
- redesigning Recovery;
- adding a general retry framework;
- repairing unrelated legacy tests or evaluation;
- migrating Curator/Reviewer into a new whole-workflow ToolRuntime design;
- modifying Fixture, Gold, a real Vault, writeback, provider credentials, or
  network configuration;
- committing, pushing, or creating a PR.

If a focused M0.1 failure can only be solved by one of these changes, the
Worker must record a downstream blocker and stop.

## Ownership

| Concern | Required owner | M0.1 boundary |
|---|---|---|
| Whole-run state and checkpoint persistence | `RuntimeEngine` / existing checkpointer | Adapter receives an opaque callback; it does not save RuntimeState directly. |
| Tool registration, input/output validation, and executor lifecycle | existing `ToolRuntime` | One retrieval runtime is composed and injected; no second tool runtime path for RetrievalAgent. |
| Tool permission and call budget | existing `ToolPolicyEnforcer` | Eligibility and commitment remain in the existing enforcer. |
| Durable tool execution facts | existing `ToolExecutionLedger` | RuntimeEngine supplies the ledger; ToolRuntime performs its transitions. |
| Trusted VaultReader-backed callback composition | `RuntimeAgentAdapter` | Adapter binds callbacks and objects only; it does not own durable lifecycle. |
| Workflow sequencing and handoff/fallback | `Coordinator` | Existing deterministic order remains unchanged. |
| Model turn, model artifacts, provider response lifecycle | `SingleAgentModelLoop` / Runtime | Not connected by M0.1. |

### Special Architecture Answer

`RuntimeAgentAdapter` should remain a **composition bridge**. In the current
dirty design it owns trusted VaultReader-backed callback binding and object
assembly, which is appropriate. It does not need to own SQLite persistence,
ledger transitions, policy commitment, model artifacts, retry, or recovery.

The inspected dirty patch does not require a lifecycle redesign: the ledger
and callback originate in RuntimeEngine and are passed unchanged into
ToolRuntime. The remaining Curator/Reviewer legacy path is an explicit coverage
limitation, not permission to move lifecycle responsibility into the Adapter.

## Inputs

- current local branch `feature/p8-agent-runtime`;
- committed baseline `HEAD=3000056`;
- current uncommitted production and test diffs;
- synthetic fixtures under `tests/fixtures/`;
- existing P8.1-P8.5 runtime, tool, provider-contract, and regression tests;
- frozen Master SPEC and repository governance contracts.

No real Vault, credential, network access, or Gold modification is an input.

## Outputs

For an approved Worker task, outputs are limited to:

- a callable deterministic production integration seam;
- consistent retrieval tool ledger/checkpoint/result projections;
- safe focused regression evidence;
- P8 regression and synthetic production-smoke evidence;
- a classified full-suite result;
- an updated M0.1 task record.

It does not output a real-model Agent, business answer, RAG result, or Provider
smoke claim.

## Exact File Boundary

Expected production files:

- `src/linkloom/agents/runtime_adapter.py`;
- `src/linkloom/agents/coordinator.py`;
- `src/linkloom/agents/retrieval_agent.py`.

Expected tests:

- add/adopt `tests/integration/test_retrieval_tool_runtime.py`;
- minimally modify `tests/integration/test_handoff_trace.py`;
- minimally modify `tests/integration/test_multi_agent_workflow.py`.

Inspection-only dependencies that are not authorized for modification:

- `src/linkloom/runtime/graph.py`;
- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`;
- `src/linkloom/runtime/checkpoint.py`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/tools/runtime.py`;
- `src/linkloom/tools/ledger.py`;
- `src/linkloom/tools/tool_policy.py`;
- Provider, Memory, Evaluation, Fixture, Gold, and mutation modules.

If implementation requires a production or test file outside the expected
boundary, the Worker must stop and request a SPEC adjustment before editing.

## Safety And Permissions

- Only synthetic fixtures may be used.
- The production path remains read-only with network and Vault write denied.
- No test may access a real Vault or Provider.
- Existing user/concurrent dirty edits must be preserved; no reset, checkout,
  overwrite, or broad formatter is authorized.
- Safe tool errors must not contain raw executor exceptions, credentials,
  absolute private paths, or Vault content.
- No Git commit, push, PR, or remote write is authorized.

## Acceptance Criteria

An independent Reviewer can accept M0.1 only when all of the following have
objective evidence:

1. `RuntimeEngine.start_multi_agent()` and `RuntimeAgentAdapter.run()` accept
   the same ledger/callback boundary and a synthetic production call reaches
   Coordinator without an interface error.
2. RuntimeEngine remains the source of the supplied ledger and checkpoint
   callback; the production retrieval path does not replace them with a second
   ledger or checkpoint lifecycle.
3. RuntimeAgentAdapter remains a composition bridge and passes the existing
   objects into one retrieval ToolRuntime.
4. RetrievalAgent remains deterministic and issues `search_notes` followed by
   zero or more `read_verified_note` calls through that ToolRuntime.
5. Tool schema, policy, budget, pending checkpoint, executor, terminal ledger,
   and event ordering reuse existing P8 behavior.
6. Malformed, denied, exhausted, stale-source, search-failure, and read-failure
   cases fail safely; pre-execution failures do not invoke the executor or
   consume a false durable call.
7. Authorized retrieval calls have unique identities and exactly one terminal
   ledger outcome when terminal persistence succeeds.
8. Intermediate checkpoint evidence contains retrieval pending/terminal
   transitions, and the final RuntimeState ledger matches the result artifact
   ledger by call identity and status.
9. Ask/connect workflow outputs and evidence semantics remain unchanged, while
   usage totals match the currently authorized deterministic calls.
10. The final evidence explicitly states that Curator/Reviewer legacy direct
    callbacks are not represented in the retrieval ledger and that Gate B is
    not complete.
11. Focused integration regression, existing P8 regression, and synthetic
    production smoke pass within the M0.1 boundary.
12. Full-suite non-passes are classified as M0.1-caused, pre-existing,
    environment-related, or unknown. M0.1-caused failures enter this repair;
    M0.1-caused and unknown failures block acceptance; environment failures
    remain blocked until their setup cause and controlled rerun are recorded;
    proven pre-existing unrelated failures are reported but not changed.
13. No Provider call, `ModelResponse` integration, `previous_tool_call`
    population, model-driven retrieval, Memory/Context, Eval expansion,
    Fixture/Gold edit, real-Vault access, or writeback occurs.
14. Exact changed files and commands/results are recorded, followed by an
    independent Reviewer decision and human acceptance.

Code presence or a green focused test alone does not complete Gate A or Gate B.

## Checks

The exact test order and commands are defined in
[implementation_plan.md](implementation_plan.md). The minimum layers are:

1. focused integration regression;
2. existing P8 tool/ledger/policy/checkpoint/model-loop regression;
3. synthetic production-path smoke;
4. full-suite classification;
5. compile/diff/status and no-real-Vault evidence.

## Downstream Deferrals

### M0.2 — Durable Real-Provider Loop Completion

- connect `ModelProviderAdapter.complete()` to the durable loop;
- normalize and persist `ModelResponse` action/error/usage/metadata;
- populate production `previous_tool_call`;
- apply provider failure and unknown-outcome rules;
- perform only a separately approved, synthetic, budget-capped real-provider
  smoke run.

### M0.3 — RetrievalAgent Model-Driven Migration

- replace fixed search/read sequencing with model-selected tools and arguments;
- allow observation-dependent continuation and runtime-owned termination;
- preserve deterministic fallback and read-only tool policy;
- add model-driven retrieval trajectory acceptance.

M0.1 must not pre-build either downstream implementation.

## Interview Evidence

M0.1 may truthfully demonstrate:

- recovery of a broken composition boundary from committed and dirty evidence;
- dependency injection of one existing ToolRuntime and runtime-owned callback;
- explicit ownership across RuntimeState, policy, ledger, Adapter, and
  Coordinator;
- regression testing for deterministic tool lifecycle and safe failures;
- scope control that separates integration repair from Provider and
  model-driven migration.

It must not be described as a real Provider, production model-driven Agent,
complete ReAct loop, exactly-once system, or complete Gate B.

## Learning Objective

The user should be able to explain:

1. why an integration/composition layer should bind existing dependencies
   instead of copying ToolRuntime validation, policy, or execution duties;
2. why durable ledger and checkpoint ownership must be explicit before a real
   Provider can safely enter the loop.

## Stop Rule

M0.1 stops immediately when the inspected P8 integration seam is callable,
uses the existing retrieval ToolRuntime/ledger/checkpoint ownership, and passes
the specified focused, P8 regression, and synthetic smoke boundary.

- Any real Provider work enters M0.2.
- Any model-selected RetrievalAgent work enters M0.3.
- Any need for a new Runtime abstraction, recovery redesign, general retry,
  whole-workflow Agent migration, or unrelated test repair stops the Worker
  and returns to planning.

## Dependencies And Human Approval

- This SPEC, [implementation_plan.md](implementation_plan.md), and
  [task.md](task.md) must be reviewed together.
- Planner creation does not authorize Worker implementation.
- A human must explicitly approve **M0.1 implementation within the exact file
  boundary** before any production or test edit.
- Worker and independent Reviewer must remain separate roles.

## Planner Learning Reflection

### Step

- Role: Planner.
- Feature: M0.1 P8 Integration Repair.
- Files reviewed: repository contracts, frozen Master, current runtime/agent/
  tool code, current Git diff, P8.4/P8.5 records, and relevant tests.

### What Changed

The suspected Engine/Adapter mismatch is now grounded in committed source, and
the current dirty repair is separated from accepted capability. M0.1 is
bounded to deterministic retrieval integration rather than real-model work.

### What I Learned

1. The dirty worktree already contains most of the likely repair, so the next
   Worker must verify and minimally complete it rather than redesign it.
2. The production and isolated model-loop paths share primitives but remain
   different orchestrations; connecting them is downstream work.

### Evidence

- `git show HEAD` proves the committed signature mismatch.
- Current source proves ledger/callback propagation exists in the dirty patch.
- Current tests provide an unaccepted focused regression foundation.
- No M0.1 test was run and no real Vault was touched by this Planner.

### Next Step

Human reviews and either approves or adjusts the exact M0.1 implementation
boundary. Only then may a separate Worker act.
