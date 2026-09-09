# SPEC: M0.3 RetrievalAgent Model-Driven Migration

Status: **APPROVED FOR WORKER IMPLEMENTATION** within the exact production and
test boundary frozen below.

The human coordinator declared M0.1 and M0.2 accepted, fixed this M0.3 scope,
and authorized this Planner step on 2026-08-30. A separate Worker may implement
only this SPEC. This Planner does not implement or accept Worker output.

## Parent Milestone And Gate

- Parent execution milestone: **M0 — Production Agent Closeout**.
- Parent execution slice: **M0.3 — RetrievalAgent model-driven migration**.
- Primary Master gate: **Gate B — Real Agent Loop**.
- Supporting Master gate: **Gate A — Real Model**, production-path reuse only.
- Governing Master:
  [MASTER_SPEC_V2.md](../linkloom-master/MASTER_SPEC_V2.md).
- Accepted predecessor:
  [M0.1 P8 Integration Repair](../m0_1_p8_integration_repair/SPEC.md).
- Frozen predecessor:
  [M0.2 Durable Real-Provider Loop](../m0_2_durable_real_provider_loop/SPEC.md).

M0.3 is a cross-cutting production integration slice. It does not replace any
user-facing milestone in [docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md),
and it does not enter M0.4 or M1.

## Preconditions And Evidence Status

The planning baseline for this SPEC is:

- M0.1 is `ACCEPTED` in repository planning evidence, including the corrected
  successful-empty-search semantics.
- The human coordinator states that M0.2 is `ACCEPTED` and its durability
  contract is frozen.
- The current M0.2 `task.md` still ends with Worker evidence awaiting its final
  independent re-review. That repository record is stale relative to the
  human's explicit status declaration. It should be reconciled by the main
  coordinator, but it is not an architecture blocker and does not authorize
  this M0.3 Worker to edit M0.2 files.
- Current source remains a dirty worktree. Every pre-existing change belongs to
  the user or an earlier role and must be preserved.

## Problem

The accepted production retrieval path now uses the existing ToolRuntime,
policy, ledger, and checkpoint boundary, but the trajectory is still authored
by Python inside `RetrievalAgent.execute()`:

```text
construct search_notes(query, source_context, limit=10)
  -> execute search
  -> for every candidate:
       extract evidence_id/ref/note_ref
       construct read_verified_note(note_ref)
       execute read
  -> return completed / failed
```

M0.2 already supplies the accepted durable model loop:

```text
ModelTurnRequest
  -> ModelResponse
  -> ToolRuntime
  -> ToolResult
  -> next ModelTurnRequest(previous_tool_call + observation)
  -> Final
```

The missing production seam is therefore not a new loop. It is the smallest
composition change that lets the existing RetrievalAgent delegate its
trajectory to that accepted loop while preserving Coordinator and M0.1 result
semantics.

## Current Production Retrieval Trace

The inspected production entry for this slice is
`RuntimeEngine.start_multi_agent()`. Root `linkloom ask/connect` uses service
classes, and `linkloom run ask/connect` uses the older P2 graph; neither is the
M0.3 retrieval path.

```text
linkloom agent ask/connect
  -> cli.py builds RunRequest and RuntimeEngine
  -> RuntimeEngine.start_multi_agent()
       -> creates RuntimeState, runtime-owned ToolExecutionLedger,
          persist_tool_ledger callback, and RuntimeAgentAdapter
  -> RuntimeAgentAdapter.run()
       -> binds VaultReader-backed search/read callbacks
       -> creates one retrieval ToolRuntime
       -> creates Coordinator(retrieval_agent=RetrievalAgent(), ...)
  -> Coordinator.run()
       -> creates retrieval AgentTask and ToolPolicyEnforcer
       -> calls RetrievalAgent.execute(..., tool_runtime, policy_enforcer)
  -> RetrievalAgent.execute()
       -> Python constructs search_notes ToolCall
       -> ToolRuntime.execute()
       -> Python loops candidates and constructs read_verified_note ToolCalls
       -> ToolRuntime.execute() for each read
       -> AgentResult
  -> Coordinator maps AgentResult.output_refs to evidence
  -> RuntimeAgentAdapter adds source/memory refs
  -> RuntimeEngine writes result artifact and final RuntimeState
```

### Direct architecture answers

| Question | Current code answer |
|---|---|
| Where is RetrievalAgent created? | `RuntimeAgentAdapter.run()` constructs `RetrievalAgent()` while assembling `Coordinator`. Direct tests also construct it explicitly. |
| Who calls it? | `Coordinator.run()` creates the retrieval task and calls `retrieval_agent.execute(...)`. |
| Where is `search -> read` hard-coded? | `RetrievalAgent.execute()`: one explicit search ToolCall followed by a Python `for` loop that creates one read ToolCall per candidate. |
| Who decides search arguments? | Python in `RetrievalAgent`: caller `query`, caller `source_context`, fixed `limit=10`. |
| Who decides read arguments? | Python in `RetrievalAgent`: it selects `evidence_id`, then `ref`, then `note_ref` from each search candidate and builds `{"note_ref": ref}`. |
| Who decides termination? | Python returns on search failure, loops until candidates are exhausted or read budget stops it, then returns completed/failed. Coordinator later decides fallback/workflow completion, and RuntimeEngine writes whole-run termination. |
| How does retrieval output return? | `AgentResult.output_refs` returns to Coordinator; Coordinator assigns it to `evidence`, then reviewer/curator consume those refs and the final result artifact includes them. |
| How are ToolRuntime, ledger, and checkpoint connected? | RuntimeEngine creates the ledger/callback; Adapter passes them to `create_retrieval_tool_runtime`; RetrievalAgent calls that ToolRuntime; pending/terminal transitions are checkpointed into RuntimeState; Coordinator serializes `tool_runtime.ledger`. |

## Deterministic Seams To Remove

The Worker must remove these sequencing decisions from production
`RetrievalAgent`:

1. `_call_id()` as the RetrievalAgent's trajectory identity generator;
2. unconditional construction of `search_notes`;
3. fixed search arguments and fixed `limit=10`;
4. candidate iteration in Python;
5. Python selection of the candidate ref for each read;
6. unconditional `read_verified_note` after non-empty search;
7. Python decision to stop after all candidates or budget break;
8. Python's direct calls to `ToolRuntime.execute()` outside the M0.2 loop.

Result projection is not sequencing. RetrievalAgent may still translate the
durable model-loop outcome and ledger into the existing `AgentResult` contract,
including safety failures, evidence refs, warnings, usage, and the M0.1 empty
search outcome.

## Goals

M0.3 must:

1. route the production RetrievalAgent through the accepted durable
   `SingleAgentModelLoop` exactly once;
2. let the model choose search, search arguments, read, read arguments,
   continuation, and Final;
3. preserve ToolRuntime as the only tool executor and existing policy/schema
   authority;
4. pass every ToolResult, including errors and empty search, back to the model
   as the next observation;
5. preserve M0.1 successful-empty-search and explicit failure semantics;
6. preserve M0.2 request/response/artifact/checkpoint/recovery behavior without
   copying or changing it;
7. produce durable trajectory evidence in `turns`, `model_executions`, model
   artifacts, ToolExecutionLedger, and tool trace events;
8. prove trajectory ownership with two FakeModel policies on the same
   production RetrievalAgent code.

## The One Recommended Migration Seam

### Decision

Use the **RetrievalAgent execution seam**, wired at the existing
`RuntimeAgentAdapter` composition root:

```text
RuntimeEngine
  owns: model dependency, current RuntimeState, checkpointer,
        ModelArtifactStore, runtime checkpoint callback
        |
        v
RuntimeAgentAdapter
  binds: Vault callbacks + one retrieval ToolRuntime
  creates: one configured RetrievalAgent
        |
        v
Coordinator
  unchanged: task creation, policy creation, result/fallback handling
        |
        v
RetrievalAgent.execute()
  creates exactly one SingleAgentModelLoop with the injected dependencies
  delegates the full trajectory
  projects ModelLoopResult + canonical state ledger -> AgentResult
```

This is one migration seam, not a second orchestration design. The supporting
changes in RuntimeEngine and RuntimeAgentAdapter only carry dependencies to the
existing RetrievalAgent call boundary.

### Required dependency flow

`RuntimeEngine` must provide an explicit injected
`ModelAdapter | ModelProviderAdapter` for `start_multi_agent()`. It must not
invent a default deterministic RetrievalAgent policy and must not silently
fall back when the model is absent or fails.

At `start_multi_agent()` it must:

1. retain one mutable state cursor whose value is always the latest successfully
   persisted RuntimeState;
2. create `ModelArtifactStore` under the existing checkpoint root, outside the
   Vault;
3. expose one RuntimeState checkpoint callback that validates run/thread
   identity, persists the supplied state, and advances the cursor only after a
   successful save;
4. pass the model, current state, artifact store, and callback into
   `RuntimeAgentAdapter.run()`;
5. use the cursor's latest state when writing the Coordinator result and final
   whole-workflow checkpoint.

`RuntimeAgentAdapter` must:

1. keep its current trusted VaultReader callback composition;
2. create the same single retrieval ToolRuntime from those callbacks;
3. create a configured RetrievalAgent with the model/state/artifact/checkpoint
   dependencies;
4. keep a local latest-state cursor updated by the same successful callback;
5. after Coordinator returns, project the canonical latest state's retrieval
   ledger into `result["tool_ledger"]` because M0.2 rehydrates and passes its
   own state ledger explicitly to ToolRuntime rather than mutating
   `tool_runtime.ledger`;
6. return no raw RuntimeState, model object, callback, credential, or private
   path in the result artifact.

`Coordinator` must remain unchanged. Its existing task, policy, call signature,
fallback, Curator, and Reviewer behavior are sufficient.

`RetrievalAgent.execute()` must:

1. resolve only `task.allowed_tool_ids` from the injected ToolRuntime registry
   into `available_tools`;
2. build one bounded retrieval instruction from `query` and `source_context`;
   this is an instruction envelope, not Gate C Context Assembly;
3. calculate a positive hard model-step bound from `task.max_steps` and the
   remaining Runtime policy provider-request budget;
4. fail explicitly before model invocation when no model-request budget
   remains;
5. construct one `SingleAgentModelLoop(model, tool_runtime, policy_enforcer,
   max_steps=...)`;
6. call its durable `run()` with RuntimeState, task/agent identity, available
   tool definitions, ModelArtifactStore, event sink, and checkpoint callback;
7. never call ToolRuntime directly;
8. project the returned durable state/ledger into `AgentResult` without
   selecting another tool or changing a ToolCall argument.

### Why this is the minimum

- It changes the only production class that currently owns deterministic
  search/read order.
- It reuses M0.2's exact model/tool/observation/final loop.
- It leaves Coordinator's orchestration and M0.1 fallback semantics intact.
- It leaves ToolRuntime, policy, ledger, artifact, state, provider, and Recovery
  contracts intact.
- It makes the ownership proof observable at the production Engine/Adapter
  path with an injected FakeModel.

## Target Production Trace

```text
RuntimeEngine.start_multi_agent
  -> RuntimeAgentAdapter.run
  -> Coordinator.run
  -> RetrievalAgent.execute
  -> SingleAgentModelLoop.run(durable)
       -> ModelTurnRequest(query + source context + retrieval tools)
       -> FakeModel or existing ModelProviderAdapter
       -> ModelResponse(tool_call=search_notes, model-chosen args)
       -> durable response
       -> ToolRuntime validates + authorizes + executes
       -> ToolExecutionLedger + checkpoint
       -> ToolResult(search observation)
       -> durable observation
       -> next ModelTurnRequest(previous search call + observation)
       -> ModelResponse(read_verified_note or Final)
       -> ...
       -> durable ModelResponse(Final)
       -> runtime-owned model_final termination
  -> RetrievalAgent projects AgentResult
  -> Coordinator continues existing review/handoff/fallback
  -> RuntimeEngine writes workflow_completed or explicit workflow failure
```

M0.3 does not claim production crash/resume completion. The durable facts are
reused and preserved; M0.4 separately accepts full production resume behavior.

## Ownership After Migration

| Concern | Owner | Frozen rule |
|---|---|---|
| Whether to search | Model | RetrievalAgent must not auto-create search. |
| Search arguments | Model | Runtime may bind call identity only; argument object remains equal to the model proposal. |
| Whether to read | Model | Empty or non-empty search must not force a read. |
| Which result to read | Model | Python must not extract a candidate and manufacture read args. |
| Whether to continue | Model | Every ToolResult returns as an observation; model chooses another tool or Final. |
| Final proposal | Model | Final is durable before result projection. |
| Available tools and schemas | LinkLoom | Only task-allowed retrieval definitions are model-visible. |
| Call identity binding | M0.2 runtime | Run/task/agent/turn IDs may be normalized; business arguments may not be rewritten. |
| Schema validation | ToolRuntime | Invalid arguments never reach the executor. |
| Permission and tool-call budget | ToolPolicyEnforcer | No model/provider bypass. |
| Model-step/provider-request hard budget | LinkLoom runtime | Positive bounded loop; exhaustion is explicit. |
| Tool execution | ToolRuntime | The Provider never executes a local tool. |
| Durable tool facts | ToolExecutionLedger | One active state ledger for the M0.2 loop. |
| Request/response/observation durability | SingleAgentModelLoop + ModelArtifactStore | Reuse M0.2 unchanged. |
| Checkpoint/recovery classification | Runtime/checkpointer + existing Recovery | No M0.3 redesign. |
| AgentResult semantic projection | RetrievalAgent | Projection may accept/reject evidence; it may not choose another tool. |
| Whole-workflow orchestration and final status | Coordinator + RuntimeEngine | Curator/Reviewer and workflow completion remain outside the retrieval model loop. |

## ToolCall Argument Integrity

The accepted M0.2 loop may replace model-supplied runtime identity fields with
the current run/task/agent/turn identity. It must preserve:

```text
bound_call.tool_id == proposed_call.tool_id
bound_call.arguments == proposed_call.arguments
executor_received_arguments == proposed_call.arguments
ledger_record.arguments == proposed_call.arguments
```

M0.3 must not normalize a query, inject `limit=10`, choose `source_context`,
replace `note_ref`, or add/remove business arguments after the model returns a
ToolCall. ToolRuntime schema validation decides whether those arguments are
valid.

## AgentResult Projection And Compatibility Semantics

Projection uses `ModelLoopResult` plus the canonical scoped records in
`ModelLoopResult.state.tool_ledger`, ordered by sequence. The result's
`last_observation` is also required because schema, unknown-tool, permission,
and pre-authorization budget refusals deliberately do not create a false
authorized ledger record. Projection never invokes a tool or reads a model
artifact as a substitute for the accepted runtime result.

### Evidence projection

1. If one or more `read_verified_note` calls completed successfully, output
   refs come from those read observations in stable call order.
2. If the model chooses `search -> final` with a successful non-empty search
   and no read, output refs come from the verified evidence IDs in the latest
   successful search observation. This permits a valid no-forced-read path.
3. Duplicate refs are removed in first-seen order.
4. A model Final with no successful search/read evidence is not promoted to a
   grounded retrieval success. It becomes an explicit safe retrieval failure.

This is result validation, not trajectory sequencing.

### Successful empty search: frozen M0.1 semantics

The empty list must first be returned to the model as the exact successful
`search_notes` ToolResult observation. RetrievalAgent must not short-circuit the
loop before the model chooses Final.

After Final, projection must produce exactly:

```text
status = completed
output_refs = []
error = None
handoff.status = not_required
handoff.reason_code = no_matching_notes
```

Coordinator must therefore keep:

```text
fallback_used = false
errors = []
```

The search ledger record remains `completed`, with `result.value=[]` and no
fabricated `NO_VERIFIED_EVIDENCE` or tool failure.

### Search failure

- ToolRuntime creates the safe ToolError, failed ledger record, and failed tool
  event.
- The exact error ToolResult returns to the next model request.
- The model may decide whether to continue its trajectory, but a terminal
  trajectory containing an unresolved search failure cannot be projected as a
  successful retrieval. AgentResult is failed with the safe search error and
  Coordinator fallback remains visible.
- Raw executor exception text and private paths remain forbidden.

### Read failure

- ToolRuntime creates the safe ToolError, failed ledger record, and failed tool
  event.
- The exact error ToolResult returns to the model.
- The failure code remains in RetrievalAgent warnings.
- If no successful evidence exists at Final, AgentResult is failed with the
  safe read error and Coordinator fallback remains visible.
- If another model-selected read succeeds, AgentResult may complete with the
  successful refs while retaining the failed read warning and ledger record,
  matching the accepted M0.1 partial-read behavior.

### Invalid, unknown, denied, or exhausted tool call

ToolRuntime and ToolPolicyEnforcer remain authoritative. Their ToolError is
returned to the model and remains explicit in the observation artifact, next
model request, JSONL failed-tool event, and final result projection. A refusal
that occurs before authorization must **not** fabricate a durable ledger
record; authorized executor failures retain their normal failed ledger record.
The model cannot turn a runtime refusal into a successful tool execution.

## Primary M0.3 Acceptance Proof

One production-path offline test must create the same synthetic Vault/index and
call the same `RuntimeEngine.start_multi_agent()` code twice. The only behavior
dependency changed between runs is the injected FakeModel policy.

### Policy A

```text
turn 1: model chooses search_notes(model_search_args)
observation: successful search result
turn 2: model chooses Final

trajectory = search_notes -> final
```

Assertions:

- no `read_verified_note` executor call exists;
- no read ledger record/event exists;
- search arguments reach executor and ledger unchanged;
- second ModelTurnRequest contains the exact search ToolCall and ToolResult;
- model execution records end in a durable Final.

### Policy B

```text
turn 1: model chooses search_notes(the same model_search_args)
observation: successful search result
turn 2: model chooses read_verified_note(model_selected_ref)
observation: successful read result
turn 3: model chooses Final

trajectory = search_notes -> read_verified_note -> final
```

Assertions:

- read arguments reach executor and ledger unchanged;
- the selected ref is the FakeModel policy's ref, not a Python-selected ref;
- third ModelTurnRequest contains the exact read ToolCall and ToolResult;
- model execution records and tool ledger agree on order and identity.

The test must reuse the same RetrievalAgent class and production implementation
without a sequencing flag, subclass, monkeypatch, or mode branch. If changing
policy alone cannot change the trajectory, M0.3 fails.

## Acceptance Matrix

An independent Reviewer may accept M0.3 only with objective evidence for all
of the following:

1. Model chooses `search_notes` on the production Engine/Adapter/Coordinator/
   RetrievalAgent path.
2. Model search arguments reach ToolRuntime executor and ledger unchanged.
3. The exact search ToolResult observation reaches the next model request.
4. Model chooses `read_verified_note`.
5. Model read arguments reach ToolRuntime executor and ledger unchanged.
6. The exact read ToolResult observation reaches the next model request.
7. Model chooses a durable Final.
8. `search -> final` is not changed into `search -> read -> final` by Python.
9. `search -> read -> final` completes normally.
10. Successful empty search preserves the exact M0.1 AgentResult, Coordinator,
    ledger, and no-fallback semantics.
11. Search failure remains a safe explicit ToolResult/ledger/trace/AgentResult
    failure and Coordinator fallback remains visible.
12. Read failure remains safe and explicit; no-evidence failure and
    partial-success warning behavior are both covered.
13. Invalid, unknown, disallowed, and schema-invalid tools remain rejected by
    ToolRuntime/policy before any unauthorized executor runs; their ToolResult
    and trace remain explicit and pre-authorization refusals create no false
    ledger record.
14. Max model steps and remaining provider-request/tool budgets terminate
    explicitly without an extra model or executor call.
15. Durable turns, model execution records, artifacts, tool ledger, and JSONL
    tool trace together record the real model-driven trajectory.
16. Production RetrievalAgent has no direct ToolRuntime execution or hidden
    search/read sequencing branch.
17. M0.2 durability is imported and reused; no copy of its loop, artifact,
    checkpoint, or Recovery logic exists in agent code.
18. A deterministic offline FakeModel production E2E proves both Policy A and
    Policy B on synthetic fixtures with no network or real Vault.

Additional compatibility criteria:

19. Missing model dependency or zero provider-request budget fails explicitly;
    there is no automatic deterministic fallback.
20. Existing P2 `RuntimeEngine.start()`, root ask/connect services, Curator,
    Reviewer, Memory reference projection, and read-only Vault boundary have no
    M0.3-caused regression.
21. M0.2 focused durability tests pass unchanged.
22. Exact changed files, RED/GREEN evidence, skipped/environment checks,
    remaining risks, independent Reviewer verdict, and human acceptance are
    recorded.

## Exact Production File Boundary

Only these production files are expected to change:

1. `src/linkloom/agents/retrieval_agent.py`
   - **Why required:** it currently owns the forbidden deterministic sequence;
     M0.3 must replace that body with one durable loop delegation and result
     projection.
2. `src/linkloom/agents/runtime_adapter.py`
   - **Why required:** it is the existing composition root that creates
     RetrievalAgent and binds its one ToolRuntime; it must pass model/state/
     artifact/checkpoint dependencies and project the latest canonical ledger.
3. `src/linkloom/runtime/graph.py`
   - **Why required:** RuntimeEngine alone owns the production RuntimeState,
     checkpointer, checkpoint root, and final state cursor; it must inject the
     model and durable state callback without moving persistence into agents.

No production change is expected in:

- `src/linkloom/agents/coordinator.py`;
- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/runtime/recovery.py`;
- `src/linkloom/tools/runtime.py`;
- `src/linkloom/tools/ledger.py`;
- `src/linkloom/tools/tool_policy.py`;
- `src/linkloom/agents/providers/`;
- `src/linkloom/cli.py`.

If a RED test proves that an M0.2 durability internal, ToolRuntime contract,
Provider contract, Coordinator contract, or CLI/provider bootstrap must change,
the Worker must stop and report:

```text
BLOCKED — M0.3 requires a frozen M0.2 or out-of-scope contract change: <proof>
```

It must not silently expand the file boundary.

## Exact Test File Boundary

Expected test changes:

1. add `tests/integration/test_m03_model_driven_retrieval.py` for the two-policy
   ownership proof, argument/observation integrity, budget, invalid tool, and
   production Engine path;
2. minimally update `tests/integration/test_retrieval_tool_runtime.py` to run
   M0.1 empty-search/search-failure/read-failure semantics through explicit
   FakeModel policies;
3. minimally update `tests/integration/test_handoff_trace.py` to inject a
   FakeModel and assert durable model records plus tool-ledger/trace agreement;
4. minimally update `tests/integration/test_multi_agent_workflow.py` so direct
   Coordinator tests construct an explicitly model-backed RetrievalAgent while
   retaining ask/connect/fallback assertions;
5. minimally update `tests/integration/test_memory_runtime.py` to inject an
   offline FakeModel and retain the existing memory-reference assertions.

Regression-only; do not edit unless the Worker stops for a SPEC adjustment:

- `tests/integration/test_p85_durable_provider.py`;
- `tests/unit/test_p84_durable_model_loop.py`;
- `tests/unit/test_p84_artifacts.py`;
- ToolRuntime/policy/ledger unit tests;
- Provider adapter tests;
- evaluation, mutation, Gold, and real-Vault tests.

## Safety And Permissions

- Required evidence uses copied synthetic fixtures only.
- No test may read or modify a real Vault.
- Retrieval tools remain read-only; write, network tool capability, Gold, and
  raw filesystem access remain denied.
- A FakeModel or injected offline provider client is required for acceptance.
- No credential lookup, environment secret enumeration, network request, or
  real-provider smoke is authorized.
- Model artifacts and checkpoints remain outside the Vault.
- Raw prompt/provider payloads, credentials, SDK objects, hidden reasoning,
  traceback, private absolute paths, and raw Vault content must not enter
  result/trace evidence.
- Existing dirty changes must not be reset, overwritten, broadly formatted, or
  claimed by the M0.3 Worker.
- No commit, push, PR, branch rewrite, or GitHub write is authorized.

## Compatibility Strategy

1. Keep `Coordinator.run()` and `RetrievalAgent.execute()`'s Coordinator-facing
   signature stable; inject durable dependencies when Adapter constructs the
   RetrievalAgent.
2. Keep P2 `RuntimeEngine.start()` and root service commands unchanged.
3. Make the model dependency optional at `RuntimeEngine` construction only so
   non-agent callers remain import-compatible; `start_multi_agent()` must fail
   closed when it is absent.
4. Do not add an implicit deterministic fallback. Tests and production
   composition must inject a model explicitly.
5. The CLI currently has no approved real-provider client factory. M0.3 proves
   the injected production Engine path offline and makes no claim that
   `linkloom agent ask/connect` can bootstrap credentials by itself. That
   composition and real-provider E2E belong to M0.4 or a separately approved
   boundary.
6. Keep result keys, evidence refs, fallback visibility, source/memory refs,
   and read-only behavior compatible.
7. Preserve M0.2 public contracts exactly; production uses them rather than
   wrapping them in another loop abstraction.

## Risks And Focused Regressions

| Risk | Required focused regression |
|---|---|
| Python still forces read after search | Policy A has zero read calls/records/events. |
| Model args are rewritten | Use unusual but schema-valid query, context, limit, and selected ref; assert proposal/executor/ledger equality. |
| Tool executes twice through two loops | Executor call count equals terminal ledger record count and model policy steps. |
| M0.2 state ledger and ToolRuntime default ledger diverge | Adapter result and final RuntimeState must use the latest model-loop state ledger. |
| Stale outer state overwrites model records | Final checkpoint retains every model execution, turn, artifact ref, and tool record. |
| Empty search regresses to fallback | Exact M0.1 empty matrix assertions. |
| Search/read errors disappear after model Final | Error observation, ledger, warning/result, event, and fallback assertions. |
| Model overrides runtime refusal | Unknown/denied/invalid call never reaches an unauthorized executor and cannot become evidence. |
| Budget allows an extra call | FakeModel and executor call counts stop exactly at boundary. |
| Direct Final is mislabeled grounded | Final without a successful retrieval observation fails safely. |
| Whole-run termination hides model trajectory | Final state is `workflow_completed`, while durable model records retain `model_final`; M0.4 owns crash/resume acceptance. |
| Provider failure silently invokes deterministic fallback | Missing/failing model remains explicit; no deterministic RetrievalAgent branch exists. |
| Curator/Reviewer are accidentally migrated | Ask/connect regression shows only RetrievalAgent uses model loop. |
| Dirty worktree scope drift | Changed-file audit permits only the frozen production/test/docs paths. |

## Non-Goals

M0.3 does not authorize:

- M0.2 durability optimization or redesign;
- M0.2 model-loop, artifact, state-model, or Recovery changes;
- Team Decision & Action behavior;
- Memory or Context Assembly;
- hybrid, semantic, embedding, reranking, or vector RAG;
- Agent Eval expansion;
- HITL or writeback expansion;
- Curator migration;
- Reviewer migration;
- a new Provider;
- Provider retry or automatic replay;
- Runtime, Recovery, checkpoint, or ToolRuntime redesign;
- provider registry/routing/streaming/parallel calls;
- a credentialed real-provider or network smoke;
- real-Vault access or mutation;
- M0.4 or M1;
- commit, push, PR, or GitHub write.

## Checks

The required RED/GREEN and regression commands are frozen in
[implementation_plan.md](implementation_plan.md). Worker evidence must include:

- RED ownership tests before production edits;
- focused M0.3 GREEN tests;
- M0.1 semantic regressions;
- unchanged M0.2 durability regressions;
- synthetic production Engine E2E;
- compile, diff, status, and exact-scope audit;
- explicit classification of environment-only test failures.

## Open Questions

None inside M0.3. CLI credential/provider bootstrap, production crash/resume,
and real-network acceptance are intentionally deferred rather than guessed.

## Learning Reflection

### Step

- Role: Planner
- Feature: M0.3 RetrievalAgent model-driven migration
- Files reviewed: RuntimeEngine, RuntimeAgentAdapter, Coordinator,
  RetrievalAgent, SingleAgentModelLoop, ToolRuntime/policy/ledger contracts,
  M0.1/M0.2 planning evidence, and focused production tests.

### What Changed

This SPEC freezes one migration seam and an evidence-based Worker boundary. No
production or test implementation changed.

### What I Learned

The deterministic ownership is concentrated in RetrievalAgent, while durable
state and tool execution already have accepted authorities. The only subtle
integration issue is that M0.2 executes against a ledger rehydrated from
RuntimeState; Adapter and RuntimeEngine must therefore preserve the latest
state cursor instead of trusting ToolRuntime's unused default ledger.

### Evidence

- current source trace documented above;
- M0.1 accepted empty/failure semantics;
- M0.2 accepted public model-loop call shape;
- two-policy production-path acceptance design;
- exact production/test file boundaries.

### Next Step

A separate Worker implements only this frozen SPEC, begins with RED tests, and
hands objective evidence to an independent Reviewer.
