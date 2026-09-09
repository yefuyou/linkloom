# M0.3 RetrievalAgent Model-Driven Migration — Implementation Plan

Status: **APPROVED FOR WORKER IMPLEMENTATION** under
[SPEC.md](SPEC.md).

Role boundary: this document is Planner output. A separate Worker implements
it; a separate Reviewer accepts or rejects the result.

## 1. Frozen Architecture Decision

Implement one seam only:

```text
RuntimeEngine dependency/state ownership
  -> RuntimeAgentAdapter composition
  -> existing Coordinator call
  -> RetrievalAgent delegates once to existing SingleAgentModelLoop
  -> existing ToolRuntime
```

Do not create a second loop, a RetrievalLoop class, an orchestration framework,
a provider registry, a mode flag, or a deterministic sequencing fallback.

M0.2 internals are frozen. If implementation needs to edit any of these, stop:

- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/runtime/recovery.py`;
- `src/linkloom/agents/model_adapter.py`;
- `src/linkloom/agents/providers/`;
- `src/linkloom/tools/runtime.py`;
- `src/linkloom/tools/ledger.py`;
- `src/linkloom/tools/tool_policy.py`.

## 2. Worker Preflight

Before editing:

1. read `AGENTS.md`, root `SPEC.md`, `docs/PRODUCT_ROADMAP.md`, this SPEC,
   this plan, and `task.md`;
2. record `git status --short` and `git log -3 --oneline`;
3. confirm the current dirty files and preserve every unrelated hunk;
4. confirm M0.1 accepted semantics in
   `tests/integration/test_retrieval_tool_runtime.py`;
5. confirm M0.2 public API in `SingleAgentModelLoop.run()` and do not edit it;
6. confirm the only expected production files are RetrievalAgent,
   RuntimeAgentAdapter, and RuntimeEngine;
7. use copied synthetic fixtures only;
8. do not run a network, credential, or real-Vault command.

The Worker should update `task.md` with the exact preflight facts before coding.

## 3. RED Tests First

Add `tests/integration/test_m03_model_driven_retrieval.py` before production
changes. Use explicit FakeModel policies and the real
`RuntimeEngine.start_multi_agent()` path.

### RED-1: policy alone owns trajectory

Add one parameterized or paired test using the same production setup:

```text
Policy A: search -> final
Policy B: search -> read -> final
```

Required assertions:

- A has exactly one search executor call and zero read calls;
- B has exactly one search and one model-selected read;
- production RetrievalAgent source/config is the same in both runs;
- no sequencing flag or Retriever subclass differs;
- model requests, model records, tool ledger, and events reflect each trajectory.

Expected RED cause: current RetrievalAgent ignores the model and always performs
its fixed search/read sequence.

### RED-2: search arguments are model-owned

Use distinctive schema-valid arguments, for example a non-default limit and a
source-context sentinel. Assert exact equality at:

```text
FakeModel ToolCall.arguments
  == search executor arguments
  == terminal ledger record.arguments
```

Expected RED cause: current Python replaces them with caller query/context and
`limit=10`.

### RED-3: search observation reaches model

Policy B's second callback must assert:

- `request.previous_tool_call.tool_id == "search_notes"`;
- previous call arguments are exact;
- `request.observation` is the exact successful ToolResult;
- call/tool identity matches.

Expected RED cause: production RetrievalAgent currently has no model turn.

### RED-4: read ref is model-owned

Return at least two search results. Make Policy B read the second result only.
Assert no read occurs for the first result and the selected ref reaches executor
and ledger unchanged.

Expected RED cause: current Python reads every candidate in order.

### RED-5: read observation reaches model Final turn

The third FakeModel callback asserts the exact previous read ToolCall and exact
read ToolResult before returning Final.

Expected RED cause: current production path never builds ModelTurnRequest.

### RED-6: successful empty search remains model-driven

Policy:

```text
search -> observe [] -> final
```

Assert exact M0.1 AgentResult/Coordinator/ledger semantics and that the model
received `[]` before Final.

Expected RED cause: current empty semantics pass, but there is no model
observation/Final proof.

### RED-7: search and read errors are observations

Create separate scripted policies:

- search executor error -> model receives error ToolResult -> Final;
- search success -> read executor error -> model receives error ToolResult ->
  Final;
- one read error -> another model-selected read success -> Final.

Assert safe error codes, no raw exception/path leakage, terminal failed ledger
records, warnings, fallback/no-fallback rules from the SPEC, and exact model
observations.

### RED-8: ToolRuntime/policy remains authority

Policies propose:

- unknown tool;
- known but task-disallowed tool;
- malformed search args;
- malformed read ref;
- tool call after tool budget exhaustion.

Assert no unauthorized executor runs, the ToolError returns to the model, and
the final result cannot present refused work as evidence. Assert the failed
tool event and durable observation/model record, and assert that refusals before
authorization do not fabricate a ToolExecutionLedger record.

### RED-9: hard model budget

Use a policy that always proposes another tool. Set the remaining model request
budget below the next step and assert:

- exact model call count;
- no extra ToolRuntime call;
- `MODEL_MAX_STEPS_EXCEEDED` or the frozen explicit no-budget error;
- runtime/fallback state is explicit.

Also prove zero provider-request budget fails before the first FakeModel call.

### RED-10: no model means no hidden fallback

Call the production multi-agent entry without an injected model. Assert a safe,
explicit failure and zero search/read calls.

### Required RED command

```powershell
python -m pytest -q tests/integration/test_m03_model_driven_retrieval.py
```

Record the failing test names and why each failure proves the current
deterministic seam. Do not weaken a test to obtain RED.

## 4. Production Increment 1 — RuntimeEngine Dependency And State Cursor

Modify only `src/linkloom/runtime/graph.py`.

### Required change

1. Add an optional provider-neutral retrieval model dependency to
   `RuntimeEngine.__init__()` without importing a concrete Provider.
2. Keep non-multi-agent P2 behavior unchanged.
3. In `start_multi_agent()`, fail closed before Adapter execution if the model
   is absent or the request provider budget is zero.
4. Create one `ModelArtifactStore` below the checkpoint root.
5. Replace the ledger-only cursor with one canonical RuntimeState cursor.
6. Add a RuntimeState checkpoint callback that:
   - requires the exact run and thread IDs;
   - saves the supplied state through the existing checkpointer;
   - advances the cursor only after save succeeds;
   - never exposes backend exception text to the result.
7. Keep the existing ToolRuntime ledger callback only as a compatibility
   wrapper over that same state cursor; do not create an independent state
   lifecycle.
8. Pass model, current RuntimeState, artifact store, and state callback to
   RuntimeAgentAdapter.
9. Build result/failure/final checkpoints from the cursor's latest state so
   model turns and executions cannot be overwritten by the pre-loop state.
10. Change the stale phrase "deterministic workflow" in whole-run completion
    text only if necessary to avoid a false trace claim; do not redesign
    TerminationState.

### Checkpoint

- import/constructor compatibility for P2 callers;
- explicit missing-model failure;
- state cursor retains an injected model checkpoint in a focused test;
- no M0.2 file changed.

## 5. Production Increment 2 — Adapter Composition

Modify only `src/linkloom/agents/runtime_adapter.py`.

### Required change

1. Extend `RuntimeAgentAdapter.run()` only with the four M0.3 dependencies:
   model, initial RuntimeState, ModelArtifactStore, and RuntimeState checkpoint
   callback.
2. Validate dependency types/callability at the composition boundary.
3. Keep the VaultReader callbacks and retrieval ToolRuntime schemas unchanged.
4. Keep one retrieval ToolRuntime.
5. Create a local latest-state cursor whose callback delegates to the runtime
   callback and advances only after success.
6. Construct `RetrievalAgent` with these dependencies.
7. Keep Coordinator construction and call unchanged.
8. After Coordinator returns, set `result["tool_ledger"]` from the local latest
   RuntimeState scoped ledger, not from stale `tool_runtime.ledger`.
9. Keep source and memory-ref projections unchanged.
10. Do not return model/state/callback/artifact-store objects in result JSON.

### Checkpoint

- Adapter still creates one ToolRuntime;
- Coordinator signature is unchanged;
- latest state ledger equals Adapter result ledger;
- no private path/model object leaks into result.

## 6. Production Increment 3 — RetrievalAgent Delegation

Modify only `src/linkloom/agents/retrieval_agent.py`.

### Remove

- `_call_id()`;
- `_execute_tool()` and direct ToolRuntime calls;
- fixed search ToolCall construction;
- fixed `limit=10`;
- Python candidate loop and ref selection;
- Python break/continue sequencing.

Retain or replace only the safe error/result projection helpers needed by the
existing AgentResult contract.

### Add

1. constructor dependencies for model, initial RuntimeState, ModelArtifactStore,
   and RuntimeState callback;
2. a bounded retrieval instruction serializer containing query and
   source_context, with no Memory/Context Assembly;
3. task-allowed ToolDefinition resolution from the existing ToolRuntime
   registry;
4. a hard model-step bound derived from task max steps and remaining runtime
   provider-request budget;
5. one `SingleAgentModelLoop` construction and one durable `run()` delegation;
6. result projection from `ModelLoopResult`, its last observation, and its
   scoped ledger;
7. stable evidence-ref deduplication;
8. M0.1 empty-search, search failure, and read failure mapping;
9. explicit ungrounded-Final, invalid-tool, model failure, and budget mapping;
10. AgentResult usage derived from durable scoped model/tool records, without
    guessing unavailable token/cost values.

### Argument rule

Do not copy caller query/context into a model ToolCall after the model responds.
The retrieval instruction may show those values to the model; the model's
ToolCall business arguments must then flow unchanged.

### Evidence projection algorithm

Use this exact order:

1. select ledger records matching current run/task/agent;
2. sort by `(sequence, call_id)` only as a defensive deterministic projection;
3. collect safe error codes/warnings from authorized ledger outcomes and the
   loop's last observation, without inventing a ledger row for a refusal;
4. if successful reads exist, collect refs from those read ToolResults;
5. otherwise, if a successful non-empty search exists and the model reached
   Final, collect evidence IDs/refs from the latest successful search result;
6. if the latest successful search value is `[]` and the model reached Final,
   return the exact `no_matching_notes` completed result;
7. if search failed or no successful retrieval evidence exists, return a safe
   failed AgentResult;
8. never execute a compensating tool during projection.

### Checkpoint

- a source search confirms no `ToolCall(` construction for search/read and no
  `.execute(` call on ToolRuntime remains in RetrievalAgent;
- both FakeModel policies pass without Retriever changes;
- M0.1 semantics pass.

## 7. Compatibility Test Calibration

After the new production path is GREEN, minimally update only the listed
existing tests.

### `tests/integration/test_retrieval_tool_runtime.py`

- replace implicit `RetrievalAgent()` setup with explicit FakeModel policies;
- keep empty search, search failure, read failure, stale-hash, schema, policy,
  ledger, checkpoint, and no-path-leak assertions;
- do not preserve the old assertion that Python always reads all results.

### `tests/integration/test_handoff_trace.py`

- inject explicit FakeModel policy and positive provider-request budget;
- assert final state contains model executions and model artifact refs;
- assert model action order agrees with tool ledger and JSONL tool events;
- retain checkpoint pending/terminal lifecycle and final result/state equality.

### `tests/integration/test_multi_agent_workflow.py`

- use one helper to construct model-backed RetrievalAgent for direct
  Coordinator tests;
- retain ask/connect task/handoff/reviewer/fallback and per-run limit behavior;
- update usage expectations only to the accepted model-driven counts.

### `tests/integration/test_memory_runtime.py`

- inject an offline FakeModel and positive provider-request budget;
- retain all existing memory-ref/hash/no-raw-memory assertions;
- do not expose memory to ModelTurnRequest; Gate C remains out of scope.

## 8. Focused GREEN Commands

Run in this order and record exact results:

```powershell
python -m pytest -q tests/integration/test_m03_model_driven_retrieval.py
```

```powershell
python -m pytest -q tests/integration/test_retrieval_tool_runtime.py
```

```powershell
python -m pytest -q tests/integration/test_handoff_trace.py tests/integration/test_multi_agent_workflow.py tests/integration/test_memory_runtime.py
```

If Windows Temp ACL prevents `tmp_path` setup, classify the error, select one
explicit repository-external approved temp directory or existing safe project
test strategy, and rerun only after explaining the setup failure. Do not change
application behavior to hide an environment error.

## 9. Frozen Regression Commands

### M0.2 durability — unchanged files

```powershell
python -m pytest -q tests/integration/test_p85_durable_provider.py tests/unit/test_p84_durable_model_loop.py tests/unit/test_p84_artifacts.py
```

### Existing runtime/tool boundaries

```powershell
python -m pytest -q tests/unit/test_p83_fake_model_loop.py tests/unit/test_p83_durable_tool_boundary.py tests/unit/test_tool_runtime.py tests/unit/test_tool_ledger_runtime.py
```

### Compile and diff

```powershell
python -m compileall -q src/linkloom tests
```

```powershell
git diff --check
```

```powershell
git status --short
```

No command may use a real Vault, real Provider, credential, or network.

## 10. Acceptance Evidence Extraction

The Worker handoff must include a compact trajectory table for both policies:

| Evidence | Policy A | Policy B |
|---|---|---|
| FakeModel request count | exact | exact |
| Model action order | search, final | search, read, final |
| Tool executor order | search | search, read |
| Proposed arguments | recorded | recorded |
| Executor arguments | equal | equal |
| Ledger arguments | equal | equal |
| Observation round-trip | search | search and read |
| ModelExecutionRecord status/order | recorded | recorded |
| Final RuntimeState ledger | equal to result | equal to result |
| Network/Vault mutation | zero | zero |

Also record:

- exact files changed;
- exact RED failures before production edits;
- exact GREEN/regression results;
- skipped/unavailable checks with reasons;
- whether any M0.2 internal changed (must be `no`);
- whether Coordinator or CLI changed (must be `no`);
- real provider smoke status (must be `NOT_RUN`);
- remaining risks;
- confirmation that no real Vault was touched.

## 11. Stop Rules

Stop and report a blocker before editing when any of these is proven:

1. `SingleAgentModelLoop.run()` cannot accept the current production
   ToolRuntime, RuntimeState, artifact store, or state callback without an
   internal contract change;
2. the loop rewrites business arguments rather than only binding runtime
   identity;
3. exact ToolResult observations cannot reach the next ModelTurnRequest;
4. the canonical model-loop state cannot be preserved through Adapter/Engine
   without changing RuntimeState or Recovery;
5. Coordinator must be redesigned to carry model state;
6. production acceptance requires CLI credential/provider bootstrap;
7. any required file is outside the frozen boundary.

Use the exact blocker format from the SPEC. Do not solve an M0.4 problem inside
M0.3.

## 12. Worker Implementation Sequence

1. Preflight and record the dirty-worktree boundary.
2. Add all M0.3 RED tests; run and record RED.
3. Implement RuntimeEngine model/state/artifact plumbing.
4. Implement Adapter composition and canonical state-ledger projection.
5. Replace RetrievalAgent deterministic sequencing with one M0.2 loop
   delegation and semantic projection.
6. Run the focused ownership proof.
7. Calibrate only the four listed compatibility test files.
8. Run M0.1 semantic regressions.
9. Run unchanged M0.2 and ToolRuntime regressions.
10. Run compile/diff/status and exact-scope audit.
11. Update `task.md` with evidence and remaining risks.
12. Stop for an independent Reviewer; do not self-accept, commit, push, or
    begin M0.4.

## Learning Reflection

### Step

- Role: Planner
- Feature: M0.3 RetrievalAgent model-driven migration
- Files touched: planning documents only

### What Changed

The implementation is split into one test-first ownership proof and three
small production increments, each aligned with an existing owner.

### What I Learned

Trajectory ownership is best proved by behavioral substitution, not by source
labels: the same Retriever must exhibit two tool paths when only the model
policy changes. State-cursor preservation is the main integration regression to
guard because M0.2's local ledger is projected through RuntimeState.

### Evidence

- RED test names and expected causes;
- exact production/test file list;
- focused and frozen regression commands;
- stop rules for M0.2 and out-of-scope changes.

### Next Step

A separate Worker follows this order and hands the resulting evidence to a
separate Reviewer.
