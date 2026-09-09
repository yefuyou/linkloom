# SPEC: M0.4 Production E2E + Resume Acceptance

Status: **APPROVED FOR WORKER IMPLEMENTATION** within the offline Worker
boundary and the separately gated real-provider boundary defined below.

The human coordinator declared M0.1, M0.2, and M0.3 `ACCEPTED`, froze their
contracts, approved M0.4 planning, and authorized this Planner step on
2026-09-01. A separate Worker may implement only this SPEC. A separate
Reviewer must decide acceptance. This Planner does not implement or accept the
Worker output.

## Parent Milestone And Gates

- Parent execution milestone: **M0 — Production Agent Closeout**.
- Parent execution slice: **M0.4 — Production E2E + Resume Acceptance**.
- Primary Master gates:
  - **Gate A — Real Model**;
  - **Gate B — Real Agent Loop**.
- Governing Master:
  [MASTER_SPEC_V2.md](../linkloom-master/MASTER_SPEC_V2.md).
- Frozen predecessors:
  - [M0.1 P8 Integration Repair](../m0_1_p8_integration_repair/SPEC.md);
  - [M0.2 Durable Real-Provider Loop](../m0_2_durable_real_provider_loop/SPEC.md);
  - [M0.3 RetrievalAgent Model-Driven Migration](../m0_3_retrieval_agent_model_driven/SPEC.md).

M0.4 is an acceptance milestone, not an architecture milestone. It does not
replace any user-facing milestone in
[docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md), and it does not enter M1.

## Preconditions And Evidence Status

The planning baseline is:

- The human coordinator states that M0.1, M0.2, and M0.3 are `ACCEPTED` and
  frozen.
- The repository M0.3 `task.md` still says `READY FOR INDEPENDENT REVIEW`.
  That record is stale relative to the human's explicit declaration. M0.4 must
  not edit the M0.3 record or reinterpret its contracts.
- The most recent recorded focused evidence is:
  - M0.3 focused suite: `11 passed`;
  - Retrieval ToolRuntime integration: `10 passed`;
  - handoff/multi-agent/memory compatibility: `12 passed`;
  - frozen M0.2 durability slice: `90 passed`;
  - P8 model/tool/runtime boundary slice: `32 passed`;
  - synthetic production two-policy smoke: `1 passed`.
- The worktree is intentionally dirty and contains earlier accepted or
  user-owned changes. The Worker must preserve every unrelated hunk.
- No M0.4 planning directory existed before this Planner step.
- `real_provider_smoke_test = NOT_RUN` remains the truthful network-proof
  status.

## Problem

M0.1-M0.3 have supplied the required components:

- production model-selected retrieval trajectories;
- a durable `SingleAgentModelLoop`;
- provider-neutral and Gemini API adapter boundaries;
- runtime-owned tool validation, permission, budget, ledger, and execution;
- checkpointed model and tool lifecycle facts;
- fail-closed recovery decisions.

Those components have substantial focused coverage, but M0 closeout still
needs one acceptance proof that starts through the production dependency
composition and demonstrates:

1. model-selected `search -> final`;
2. model-selected `search -> read -> final`;
3. final result, durable model records, tool ledger, checkpoints, artifacts,
   and trace agreeing with one another;
4. a process restart reusing a durable terminal ToolResult without replaying
   the previous provider/model turn or executor call;
5. an ambiguous provider boundary refusing blind replay;
6. one separately approved real-provider run if Gate A and the M0 exit are to
   be declared complete.

The missing implementation is narrow. Fresh production composition already
works. Production composition does not yet expose the accepted model-loop
resume path.

## Reconstructed Current Production Path

### CLI fact

`src/linkloom/cli.py` constructs `RuntimeEngine` for `linkloom agent
ask/connect` without injecting a model. The engine therefore returns
`MODEL_NOT_CONFIGURED`. M0.4 must not misrepresent this CLI as a working
real-model entrypoint and must not add credential/provider bootstrap to the
CLI.

### Chosen production acceptance entrypoint

Fresh E2E acceptance enters through the public production composition API:

```text
RuntimeEngine.start_multi_agent(RunRequest)
```

The test may inject a controlled `FakeModelAdapter` or fake provider at the
constructor boundary, but it must not directly instantiate RetrievalAgent,
Coordinator, RuntimeAgentAdapter, ToolRuntime, or SingleAgentModelLoop.

### Actual fresh execution trace

```text
RunRequest
  -> RuntimeEngine.start_multi_agent()
       -> validate ask/connect, thread, model, provider budget
       -> VaultReader/read_notes and source index SHA
       -> request_<run_id>.json
       -> RuntimeState(status=running, current_step=multi_agent)
       -> runtime-owned ToolExecutionLedger
       -> ModelArtifactStore
       -> checkpoint callbacks and OptionalTracer
       -> RuntimeAgentAdapter.run()
            -> VaultReader-backed trusted callbacks
            -> create_retrieval_tool_runtime(search, read, ledger, checkpoint)
            -> Coordinator(...)
                 -> create retrieval AgentTask
                 -> create ToolPolicyEnforcer
                 -> RetrievalAgent.execute()
                      -> SingleAgentModelLoop.run()
                           -> ModelTurnRequest
                           -> ModelAdapter.decide() or ModelProviderAdapter.complete()
                           -> normalized ModelAction / ModelResponse
                           -> ToolCall
                           -> ToolRuntime.execute()
                                -> ToolExecutionLedger pending/terminal facts
                                -> trusted search_notes/read_verified_note executor
                           -> ToolResult as next observation
                           -> next model turn or Final
                      -> AgentResult projection
                 -> Curator when workflow=connect
                 -> Reviewer
                 -> coordinator result
            -> source/memory/tool-ledger projection
       -> result artifact
       -> final RuntimeState
       -> trace manifest and RunStatus
```

This is the composition that M0.4 tests must exercise.

## Reconstructed Current Resume Path

The accepted lower-level model resume implementation exists:

```text
SingleAgentModelLoop.resume()
  -> run(..., resume=True)
  -> decide_model_resume(RuntimeState, ToolExecutionLedger)
  -> validate durable request/response/observation refs, SHA-256, and identity
  -> reuse durable response or terminal ToolResult
  -> continue with the next model turn
```

The production gap is:

- no production code calls `SingleAgentModelLoop.resume()`;
- `RuntimeEngine.resume(thread_id, interrupt_id, response)` is the older P2
  HITL interrupt API and cannot resume a model loop;
- `Coordinator.run()` creates a new random retrieval `task_id` on every call;
- `RuntimeAgentAdapter.run()` and `RetrievalAgent.execute()` always select the
  fresh `.run()` path;
- therefore a new process cannot re-enter the same durable retrieval scope
  `(run_id, task_id, agent_id)` through production composition.

This is a real, bounded production wiring gap. It is not permission to
redesign M0.2 recovery or M0.3 trajectory ownership.

## Goals

M0.4 must:

1. prove fresh `search -> final` through production composition;
2. prove fresh `search -> read -> final` through production composition;
3. prove successful empty search remains completed with no fabricated failure;
4. prove selected search/read/provider failures retain their accepted
   production semantics;
5. expose the minimum production API needed to resume an in-progress durable
   retrieval model loop;
6. preserve the original retrieval task identity on resume;
7. route resume through the existing `SingleAgentModelLoop.resume()` and
   `decide_model_resume()` authorities;
8. prove durable terminal ToolResult reuse does not replay the earlier model
   turn or executor call;
9. prove ambiguous `request_sent` state does not blindly invoke the provider;
10. correlate AgentResult, model records, tool ledger, artifacts, checkpoints,
    and trace;
11. state Gate A and Gate B honestly;
12. keep all fixtures synthetic and read-only.

## Exact Acceptance Boundary

### E2E A — model-selected search then final

```text
RuntimeEngine.start_multi_agent()
  -> model proposes search_notes with distinctive valid arguments
  -> ToolRuntime executes trusted search callback
  -> successful ToolResult reaches next ModelTurnRequest
  -> model returns Final
  -> RetrievalAgent projects the durable trajectory
  -> Coordinator and RuntimeEngine complete
```

Required evidence:

- one search executor invocation;
- zero read executor invocations;
- one completed search ledger record;
- two model requests/records with distinct sequences;
- the second request contains the exact prior ToolCall and ToolResult;
- production result and final RuntimeState are completed;
- result evidence matches durable ledger projection;
- no trajectory switch exists outside model policy.

### E2E B — model-selected search, read, then final

```text
RuntimeEngine.start_multi_agent()
  -> model proposes search_notes
  -> search ToolResult observation
  -> model selects one returned evidence ref
  -> model proposes read_verified_note(note_ref=<selected ref>)
  -> verified read ToolResult observation
  -> model returns Final
  -> production result
```

Required evidence:

- one search and exactly one model-selected read;
- the selected `note_ref` is not inserted by Python sequencing;
- three model requests/records;
- exact search and read observation handoff;
- two terminal completed ledger records;
- final evidence ref equals the verified read result;
- trace has one `tool.called` and one terminal event for each `call_id`.

### E2E C — terminal ToolResult durable resume

The accepted crash point is after the terminal ToolResult and observation are
durably checkpointed but before the next model turn begins:

```text
first process
  -> model turn 1 proposes tool
  -> ToolRuntime executes once
  -> terminal ToolExecutionRecord durable
  -> observation artifact durable
  -> ModelExecutionRecord.status = tool_result_durable
  -> simulated process death

new RuntimeEngine instance
  -> resume_multi_agent(thread_id)
  -> load exact RuntimeState and original request
  -> preserve run_id/task_id/agent_id
  -> existing recovery decision = resume_from_tool_result
  -> validate durable response and observation identity/SHA
  -> do not invoke the previous model turn
  -> do not execute the previous tool call
  -> next model turn receives exact previous ToolCall + ToolResult
  -> model Final
  -> normal Coordinator/RuntimeEngine finalization
```

The test crash must happen after the checkpoint backend has saved the target
state. A test-only `BaseException` subclass may model process death so normal
`except Exception` safety projection does not rewrite the durable crash point.
No production crash-injection switch is added.

### E2E D — ambiguous request_sent fails closed

```text
first process
  -> request_durable checkpoint
  -> request_sent checkpoint
  -> provider invocation begins
  -> test provider records the attempt and process dies before response durable

new RuntimeEngine instance
  -> resume_multi_agent(thread_id)
  -> existing recovery decision = requires_verification
  -> no provider/model invocation
  -> no executor invocation
  -> no terminal rewrite of the ambiguous durable state
  -> safe verification-required outcome
```

`resume_multi_agent()` must consult `decide_model_resume()` before re-entering
Coordinator for a decision that is already known to be blocked. It must not
turn this state into a new retrieval failure/fallback and must not invent an
automatic retry or verification implementation.

## Production Resume Entry Point

M0.4 freezes this minimal public API:

```python
RuntimeEngine.resume_multi_agent(thread_id: str) -> RunStatus
```

It is intentionally separate from the existing HITL method:

```python
RuntimeEngine.resume(thread_id, interrupt_id, response)
```

`resume_multi_agent()` accepts no query, ToolCall, task ID, model response, or
caller-supplied RuntimeState. Those values come only from durable state and
artifacts.

Required entrypoint behavior:

1. load the latest state for `thread_id`;
2. require the same in-progress M0.4 retrieval scope rather than a completed,
   rejected, stale, or unrelated P2 state;
3. require an injected model/provider;
4. load and validate the persisted `RunRequest` named by `request_ref`;
5. verify workflow, thread identity when present, source index SHA, and frozen
   policy limits against RuntimeState;
6. re-read synthetic source documents through `VaultReader` so note hash
   verification still applies;
7. identify exactly one retrieval `task_id` from durable model records for the
   active `(run_id, retrieval_agent)` scope; zero or multiple identities fail
   closed;
8. call the existing `decide_model_resume()` authority;
9. block ambiguous/manual/reinvoke-unsupported decisions without provider or
   executor calls and without overwriting the ambiguous checkpoint;
10. for accepted reusable decisions, create `OptionalTracer(...,
    load_existing=True)`, restore the runtime-owned ledger/artifact callbacks,
    and re-enter the normal adapter/coordinator composition;
11. finalize result, RuntimeState, artifact, and manifest using the original
    `run_id` and `thread_id`.

No new recovery decision enum, state model, retry policy, or ledger is allowed.

## Task Identity And Scope Preservation

The model loop and tool budget are scoped by:

```text
run_id + task_id + agent_id
```

On resume:

- `run_id` comes from the latest RuntimeState;
- `agent_id` remains `retrieval_agent`;
- `task_id` comes from the canonical durable `ModelExecutionRecord`, not a new
  UUID and not caller input;
- Coordinator receives that exact ID only for its retrieval task;
- Curator/Reviewer task IDs remain fresh because M0.4 resumes only while the
  retrieval model loop is active;
- `ToolPolicyEnforcer.rehydrate_from_ledger()` remains the single process-local
  budget reconstruction mechanism and sees the preserved scope.

If the checkpoint is not in the active retrieval model-loop boundary, M0.4
must fail closed rather than attempting a general Coordinator replay.

## Duplicate-Call Evidence Design

M0.4 must measure invocations rather than infer them from a final string.

### Model/provider counter

Use a recording fake model/provider that stores, for every actual invocation:

- process label (`before_crash` or `after_resume`);
- `run_id`;
- `turn_id`;
- `task_id`;
- sequence;
- whether a previous ToolCall/observation was present.

For terminal ToolResult resume, expected evidence is:

| Measurement | Before crash | After resume |
|---|---:|---:|
| original tool-proposal turn invocations | 1 | 0 |
| legitimate next Final turn invocations | 0 | 1 |

The next Final turn is not a duplicate. A duplicate means re-invoking the same
durable operation identity/sequence.

For ambiguous `request_sent`, expected after-resume provider invocations are
zero.

### Executor counter

Record trusted search/read executor invocations with `call_id`, tool ID, and
arguments.

For terminal ToolResult resume:

| Measurement | Before crash | After resume |
|---|---:|---:|
| original executor call | 1 | 0 |

The terminal ledger must still contain exactly one record for the original
`call_id`.

M0.4 may claim **durable reuse with zero duplicate calls at tested boundaries**.
It must not claim distributed or universal exactly-once execution.

## Failure-Path Acceptance

M0.4 adds only a compact production matrix.

### Successful empty search

- search executor returns an empty list without raising;
- ToolResult is `ok`;
- terminal search ledger outcome is `completed`;
- RetrievalAgent result is completed with empty output refs;
- Coordinator result has empty evidence, no fallback, and no
  `NO_VERIFIED_EVIDENCE`;
- workflow completion is not converted into failure merely because no note
  matched.

### Search ToolRuntime failure

- executor or ToolRuntime returns a normalized failure;
- failed terminal ledger record and safe ToolResult error remain visible;
- RetrievalAgent failure and Coordinator fallback semantics remain those
  accepted in M0.1/M0.3;
- no traceback, absolute private path, or fabricated empty success appears.

### Read ToolRuntime failure

- a model-selected read fails through ToolRuntime;
- failure remains in terminal ledger and RetrievalAgent projection;
- it is not silently skipped or converted into empty-search success.

### Provider/model error

- normalized model/provider failure produces the accepted safe retrieval
  failure path;
- no tool executor runs when the model did not produce a valid tool action;
- no provider retry is attempted.

### Budget termination

One existing production-composable max-step or provider-budget case must remain
green in the focused regression slice. M0.4 does not build a new budget matrix.

## Trace, Ledger, Artifact, And State Assertions

Every primary E2E test must assert more than a final answer.

### RuntimeState/checkpoint

- original `run_id` and `thread_id` are preserved;
- state sequences remain monotonic;
- turns, model records, and tool records have matching task/agent scope;
- terminal state has the correct `result_ref` and termination reason;
- resume starts from a checkpoint actually loaded by a new RuntimeEngine.

### Model execution records and artifacts

- each invoked turn has one `ModelExecutionRecord`;
- request/response/observation refs required by the tested lifecycle exist;
- recorded SHA-256 values validate through `ModelArtifactStore`;
- normalized action and ToolCall identity agree;
- terminal ToolResult resume uses the durable observation artifact;
- provider secrets, raw SDK objects, hidden reasoning, and tracebacks are absent.

### ToolExecutionLedger

- one record per `call_id`;
- arguments and scope match the model-proposed ToolCall;
- statuses/results/errors match the projected AgentResult;
- a reused terminal result is not appended as a second record.

### Trace and handoff

- trace event sequence remains valid and monotonic across a reopened tracer;
- every executed tool call has one `tool.called` and one corresponding
  `tool.completed` or `tool.failed` with the same `call_id`;
- no second `tool.called` exists for a reused terminal result;
- ask-workflow acceptance does not invent a curator handoff;
- existing Coordinator task/reviewer events remain observable;
- M0.4 does not add a new trace event taxonomy merely to label resume.

### Final result

- result evidence and task status agree with durable facts;
- result tool-ledger projection equals the canonical final state ledger;
- source and memory remain refs/metadata under their frozen contracts;
- the final string alone is never sufficient acceptance evidence.

## Provider Boundary Decision

### Mandatory offline acceptance

All Worker RED/GREEN E2E and resume tests use an injected
`FakeModelAdapter`, recording provider, or injected fake Gemini client. They
must run without network, credentials, external cost, or a real Vault.

This proves production dependency composition, Runtime ownership, and Gate B
semantics. It does not by itself prove Gate A's network criterion.

### Separately gated real-provider smoke

The Master SPEC explicitly requires at least one separately approved,
budget-capped synthetic real-provider run for Gate A. Therefore:

- a real-provider smoke **is required for final M0.4/M0 exit acceptance**;
- it is **not authorized by this Planner/Worker approval**;
- it must not run automatically in `pytest`;
- absence of a legal credential does not block offline implementation;
- if it remains unapproved or unavailable, record exactly:

```text
real_provider_smoke_test = NOT_RUN
Gate A = PARTIAL / BLOCKED_ON_REAL_PROVIDER_SMOKE
Gate B = eligible for COMPLETE after independent offline E2E review
M0.4 final acceptance = BLOCKED_ON_REAL_PROVIDER_SMOKE
```

If separately approved, the smoke must:

1. use the existing `GeminiProviderAdapter` and an injected official supported
   Gemini API client, not Gemini CLI or subprocess;
2. enter through `RuntimeEngine.start_multi_agent()`;
3. use a copied synthetic Vault and read-only tools;
4. set an explicit model ID and request/cost/output bounds;
5. disclose the bounded prompt/evidence sent to the provider;
6. load a legal credential only through the supported environment/client
   boundary;
7. never print or persist credentials or raw SDK request/response objects;
8. record normalized model identity, provider request count, usage, latency,
   safe outcome, ledger, and final state;
9. stop after one approved scenario;
10. report `PASS`, `FAIL`, or `BLOCKED` honestly.

The repository currently has no official Gemini SDK dependency or production
credential/client factory. M0.4 must not add either merely for offline tests.
The separate smoke approval must name the legal client setup before invocation.

## Gate Implications

### Gate B — Real Agent Loop

Gate B may be marked `COMPLETE` only after independent review confirms:

- production `search -> final`;
- production `search -> read -> final`;
- model-owned tool choice and arguments;
- ToolRuntime/policy/ledger/checkpoint ownership;
- terminal ToolResult reuse with no duplicate prior model/executor invocation;
- ambiguous provider state blocks blind replay;
- failure and budget semantics remain safe.

### Gate A — Real Model

- Offline production E2E with the existing provider adapter boundary makes
  Gate A architecture-ready but not complete.
- Gate A remains `PARTIAL / BLOCKED_ON_REAL_PROVIDER_SMOKE` while
  `real_provider_smoke_test = NOT_RUN`.
- Gate A becomes eligible for `COMPLETE` only after the separate smoke evidence
  passes independent review.

### M0.4 and M0 exit

- Offline Worker completion is not permission to overstate M0.4 as fully
  accepted.
- M0.4 final acceptance and M0 exit require both Gate A and Gate B.
- After M0.4 passes, new Runtime abstraction work stops unless a later accepted
  business SPEC proves it necessary.

## Minimal Production Diff

### Fresh E2E

```text
NO PRODUCTION CHANGE REQUIRED
```

`RuntimeEngine.start_multi_agent()` already composes the accepted fresh path.

### Resume E2E

Minimal backward-compatible wiring is required in only these production files:

1. `src/linkloom/runtime/graph.py`
   - add `RuntimeEngine.resume_multi_agent(thread_id)`;
   - load/validate canonical state, request, source, task scope, and existing
     recovery decision;
   - rebuild the same adapter/ledger/artifact/tracer callbacks;
   - finalize the original run after successful continuation;
   - do not change the old HITL `resume()` contract.
2. `src/linkloom/agents/runtime_adapter.py`
   - accept optional resume mode and canonical retrieval task ID;
   - default them to the current fresh behavior;
   - pass them through without owning recovery decisions.
3. `src/linkloom/agents/coordinator.py`
   - accept an optional retrieval task ID for the resumed retrieval task only;
   - preserve random IDs for fresh execution and all other agents;
   - do not replay a post-retrieval Coordinator state.
4. `src/linkloom/agents/retrieval_agent.py`
   - select existing `SingleAgentModelLoop.resume()` only when the production
     resume mode was explicitly supplied;
   - keep fresh `.run()` behavior and AgentResult projection unchanged.

No other production file is approved. In particular, do not modify:

- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/recovery.py`;
- `src/linkloom/runtime/models.py`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/tools/`;
- `src/linkloom/agents/model_adapter.py`;
- `src/linkloom/agents/providers/`;
- `src/linkloom/cli.py`;
- Memory, Evaluation, writeback, UI, or Scanner.

If the approved four-file seam cannot satisfy the RED tests, the Worker must
stop and report the exact blocker. It must not silently expand the boundary.

## Exact Test File Boundary

Mandatory new test file:

- `tests/integration/test_m04_production_e2e_resume.py`

It owns:

- production search-final acceptance;
- production search-read-final acceptance;
- production empty-search and compact failure cases;
- terminal ToolResult restart/resume;
- ambiguous request-sent no-replay;
- provider/model and executor call counters;
- state/model-record/artifact/ledger/trace/result consistency.

Existing M0.1-M0.3/P8 tests are regression inputs only and should not be edited
unless a demonstrably incorrect fixture prevents the approved M0.4 behavior.
Such a need is a stop-and-report condition, not implicit permission.

Optional, separately approved smoke file:

- `tests/smoke/test_m04_real_provider_smoke.py`

It must be excluded from default tests and must not be created or run until the
human approves its credential, network, model, disclosure, and budget boundary.

## Acceptance Criteria

M0.4 offline Worker output is ready for independent review only if all of the
following pass:

1. Fresh tests enter through `RuntimeEngine.start_multi_agent()`.
2. Resume tests enter through `RuntimeEngine.resume_multi_agent()` on a new
   engine instance using the same durable stores.
3. No primary M0.4 test directly invokes RetrievalAgent or
   SingleAgentModelLoop.
4. Policy A produces `search -> final`.
5. Policy B produces `search -> read -> final`.
6. Tool choice, arguments, and stopping remain model-owned.
7. Successful empty search completes without fallback or fabricated failure.
8. Search failure remains visible in ToolResult, ledger, and result semantics.
9. Read failure remains visible and is not silently swallowed.
10. Provider/model failure runs no tool and is normalized safely.
11. At least one existing budget termination regression remains green.
12. Terminal ToolResult resume preserves original run/task/agent/call identity.
13. The prior tool-proposal model turn is not invoked after resume.
14. The prior executor call is not invoked after resume.
15. The legitimate next model turn receives exact previous ToolCall and
    ToolResult and may return Final.
16. Ambiguous `request_sent` produces verification-required behavior with zero
    post-resume provider and executor calls.
17. Ambiguous state is not blindly rewritten into a terminal Coordinator
    failure/fallback.
18. Ledger contains no duplicate `call_id`.
19. Model records and artifacts pass existing identity/SHA validation.
20. RuntimeState, result artifact, ledger projection, and trace agree.
21. Old HITL `RuntimeEngine.resume()` behavior remains unchanged.
22. M0.1/M0.2/M0.3 focused regressions pass.
23. No real Vault, write-capable tool, credential, or network is used offline.
24. `git diff --check` and compile validation pass.
25. Real smoke status is reported honestly; full M0.4 acceptance is withheld
    while it is `NOT_RUN`.

## Hard Non-Goals

M0.4 must not implement or redesign:

- Team Decision or M1;
- Memory-to-model context or Context Assembly;
- semantic/hybrid RAG or reranking;
- evaluation runner expansion;
- HITL expansion;
- a new Provider or provider registry;
- provider retry, streaming, or automatic reinvocation;
- Runtime, checkpoint, recovery, ToolRuntime, or ledger architecture;
- RetrievalAgent trajectory or projection rules;
- Curator/Reviewer model migration;
- CLI provider bootstrap;
- UI;
- writeback or any real-Vault mutation;
- general Coordinator replay after retrieval has completed;
- exactly-once claims.

## Safety And Stop Rules

- Use copied synthetic fixtures only.
- Runtime checkpoint, trace, model artifacts, and memory roots remain outside
  the synthetic Vault.
- No write-capable tool may enter the M0.4 tool definitions.
- Do not read unknown credentials or probe the network.
- A real smoke needs a separate explicit human approval.
- A need to modify any frozen M0.2/P8.4 durability file is a blocker.
- A need to redesign M0.3 projection is a blocker.
- A checkpoint that cannot identify one canonical retrieval task is blocked;
  do not invent a task ID.
- A recovery decision of `requires_verification` or
  `requires_manual_decision` is blocked; do not replay.
- Protected Windows temporary ACL failures must be recorded, not repaired in
  M0.4.
- Do not commit, push, create a PR, reset, rebase, stash, or clean.

## Resolved Planning Decisions

| Question | Decision |
|---|---|
| Fresh production entrypoint | `RuntimeEngine.start_multi_agent()` |
| Model-loop resume entrypoint | new `RuntimeEngine.resume_multi_agent(thread_id)` |
| Existing HITL resume | unchanged and separate |
| Offline model | injected FakeModel/fake Provider through production composition |
| Resume identity | durable `run_id + retrieval task_id + retrieval_agent` |
| Recovery authority | existing `decide_model_resume()` and `SingleAgentModelLoop.resume()` |
| Primary resume proof | durable terminal ToolResult reuse |
| Ambiguous proof | `request_sent` with no durable response, no blind replay |
| Fresh production diff | none |
| Resume production diff | four narrow backward-compatible files |
| Real Provider smoke | required for final Gate A/M0.4, separately authorized |
| Exactly-once claim | forbidden |

## Learning Reflection

### Step

M0.4 Planner — production composition and durable resume acceptance.

### What Changed

No runtime capability is redesigned. The SPEC converts accepted component-level
behavior into production E2E and restart evidence and exposes only the missing
resume wiring.

### What I Learned

1. A durable lower-level resume API is not a production resume capability until
   the production composition can restore the same execution identity.
2. No-duplicate evidence must distinguish replay of an old operation from the
   legitimate next model turn.

### Evidence

- current production trace;
- exact resume seam;
- E2E and crash scenarios;
- invocation-counter design;
- Gate A/B truth table;
- bounded production/test files.

### Next Step

A separate Worker writes RED production-resume tests, implements only the
approved wiring, runs offline acceptance/regressions, and hands the result to
an independent Reviewer. Real-provider smoke remains a separate human gate.
