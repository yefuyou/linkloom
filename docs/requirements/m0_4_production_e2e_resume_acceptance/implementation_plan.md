# M0.4 Production E2E + Resume Acceptance — Implementation Plan

Status: **APPROVED FOR WORKER IMPLEMENTATION** under
[SPEC.md](SPEC.md), limited to the offline Worker packages W0-W5 below.

Role boundary: this document is Planner output. A separate Worker implements
it. A separate Reviewer accepts or rejects the result. The optional real
Provider smoke is not authorized by this plan and requires its own human gate.

## 1. Frozen Architecture Decision

M0.4 proves the current architecture and adds only its missing production
resume connection:

```text
fresh
  RuntimeEngine.start_multi_agent()
    -> existing production composition

resume
  RuntimeEngine.resume_multi_agent(thread_id)
    -> existing checkpoint / request / artifacts / recovery decision
    -> RuntimeAgentAdapter(resume mode, canonical retrieval task_id)
    -> existing Coordinator
    -> RetrievalAgent
    -> existing SingleAgentModelLoop.resume()
    -> existing ToolRuntime / ledger
    -> existing result and state projection
```

Do not create a second Agent loop, recovery controller, resume state model,
ledger, policy, registry, provider, or orchestration framework.

Fresh E2E requires no production change. Resume E2E permits only backward-
compatible wiring in:

- `src/linkloom/runtime/graph.py`;
- `src/linkloom/agents/runtime_adapter.py`;
- `src/linkloom/agents/coordinator.py`;
- `src/linkloom/agents/retrieval_agent.py`.

Any need to edit `model_loop.py`, `recovery.py`, `models.py`, `artifacts.py`,
`tools/`, provider code, CLI, Memory, or Evaluation is a stop condition.

## 2. Worker Preflight

Before editing, the Worker must:

1. read `AGENTS.md`, root `SPEC.md`, `docs/PRODUCT_ROADMAP.md`, the Master
   SPEC, this SPEC, this plan, and `task.md`;
2. record `git status --short` and `git log -3 --oneline`;
3. preserve every existing dirty hunk;
4. confirm the current production signatures of:
   - `RuntimeEngine.start_multi_agent()`;
   - old HITL `RuntimeEngine.resume()`;
   - `RuntimeAgentAdapter.run()`;
   - `Coordinator.run()`;
   - `RetrievalAgent.execute()`;
   - `SingleAgentModelLoop.resume()`;
   - `decide_model_resume()`;
5. confirm M0.1-M0.3 accepted focused tests pass or record exact pre-existing
   environment failures;
6. create only copied synthetic Vault/index/checkpoint/trace/artifact roots;
7. confirm no legal real-provider smoke approval exists and record
   `real_provider_smoke_test = NOT_RUN`;
8. update `task.md` with preflight facts before production edits.

The Worker must not repair unrelated Windows ACL paths or legacy evaluation
failures.

## 3. W0 — Production Characterization Tests

Create:

- `tests/integration/test_m04_production_e2e_resume.py`

Build one test fixture that creates:

- a tiny copied synthetic Vault;
- its deterministic Scanner index;
- checkpoint, trace, model artifact, and memory roots outside the Vault;
- a `RuntimeEngine` with an injected recording FakeModel or fake Provider;
- trusted search/read counters, preferably by minimally instrumenting the
  existing RuntimeAgentAdapter callbacks in the test;
- helpers to load the final RuntimeState, result artifact, trace events, and
  model artifacts.

The fixture must not construct RetrievalAgent or SingleAgentModelLoop.

### W0-A: search -> final

Use a model policy with two callbacks:

1. propose `search_notes` with distinctive schema-valid arguments;
2. assert exact previous ToolCall and ToolResult, then return Final.

Assert:

- `RuntimeEngine.start_multi_agent()` is the entry;
- one search, zero reads;
- two model requests and records;
- one completed ledger record;
- final state/result/evidence/trace consistency.

This may already be GREEN because M0.3 implemented the fresh path. Record it as
a characterization result, not fabricated RED evidence.

### W0-B: search -> read -> final

Use a three-callback policy:

1. search;
2. select one returned ref and read it;
3. assert exact read observation and return Final.

Assert one search, one selected read, three model records, two terminal ledger
records, verified evidence projection, and correlated tool events.

This may also already be GREEN.

### W0-C: empty search

Return an empty successful search result and then Final. Assert:

- search ledger status `completed`;
- empty evidence/output refs;
- retrieval task completed;
- `fallback_used` is false;
- no `NO_VERIFIED_EVIDENCE` anywhere in result/state/trace.

### W0-D: compact failure cases

Add parameterized or separate production cases for:

- search executor failure;
- read executor failure;
- normalized provider/model error.

Do not recreate the M0.1-M0.3 full failure matrix. Assert only the durable
failure fact, safe AgentResult/Coordinator projection, and absence of an
unexpected executor call.

## 4. W1 — RED Resume Acceptance Tests

Write these tests before production resume wiring.

### RED-1: public production model resume exists

Start an in-progress production run, construct a new RuntimeEngine over the
same stores, and call:

```python
resumed_engine.resume_multi_agent(thread_id)
```

Expected pre-implementation RED cause:

```text
RuntimeEngine has no resume_multi_agent entrypoint
```

Do not work around RED by calling SingleAgentModelLoop.resume() directly.

### RED-2: terminal ToolResult is reused through production composition

Use a test-only checkpointer decorator:

1. delegate-save every RuntimeState;
2. when the latest retrieval ModelExecutionRecord first reaches
   `tool_result_durable`, raise `SimulatedProcessCrash(BaseException)` after
   the save returns;
3. allow the exception to escape the first engine so the crash point is not
   rewritten into a normal failed state.

Record before crash:

- one model tool-proposal invocation;
- one executor invocation;
- one terminal ledger record;
- durable observation artifact;
- canonical run/task/agent/call identities.

Restart with a new engine and a model whose only valid invocation is the next
Final turn. The callback must assert:

- sequence is the next sequence;
- `previous_tool_call` equals the durable ToolCall;
- observation equals the durable ToolResult;
- run/task/agent identities match the pre-crash records.

Required post-resume counts:

- replay of the original model turn: `0`;
- legitimate next model turn: `1`;
- replay of the original executor: `0`.

Expected RED causes before implementation include missing public resume,
regenerated retrieval task ID, and RetrievalAgent selecting `.run()` instead
of `.resume()`.

### RED-3: ambiguous request_sent blocks replay

Use a provider/model test double that:

1. is invoked after the runtime durably records `request_sent`;
2. increments a process-local invocation counter;
3. raises `SimulatedProcessCrash(BaseException)` before a response is
   returned/durable.

After restart, call the public resume entrypoint with a model/provider that
fails the test if invoked.

Assert:

- recovery reason is verification required;
- after-resume provider/model calls are `0`;
- after-resume executor calls are `0`;
- latest durable model record remains `request_sent`;
- no new retrieval fallback or terminal Coordinator rewrite appears;
- no new tool ledger record appears.

### RED-4: task scope is canonical

Tamper only the attempted resume composition—not durable state—by proving the
Worker cannot generate a new retrieval task ID and still pass existing model
record/ledger scope validation. The production implementation must derive the
one accepted task ID from durable records.

Behavioral assertion:

- resumed model request, prior ModelExecutionRecord, prior ToolExecutionRecord,
  and final retrieval AgentTask all use the same retrieval task ID.

Do not assert a private helper name.

### Required RED command

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_m04_production_e2e_resume.py
```

Record separately:

- characterization tests already GREEN;
- exact resume tests RED;
- failure messages reaching the intended missing seam.

## 5. W2 — RuntimeEngine Resume Boundary

Modify only `src/linkloom/runtime/graph.py` for this increment.

Add:

```python
def resume_multi_agent(self, thread_id: str) -> RunStatus:
    ...
```

### Input and state preflight

The method must:

1. validate non-empty `thread_id` through existing conventions;
2. load the latest RuntimeState or raise existing `ThreadNotFoundError`;
3. require:
   - `state.status == "running"`;
   - `state.current_step == "model_loop"`;
   - running termination;
   - `workflow in {"ask", "connect"}`;
4. reject old P2/HITL, terminal, stale, and post-retrieval states through
   existing error classes;
5. require an injected model/provider;
6. safely resolve `state.request_ref` under `checkpoint_dir`;
7. parse it with `RunRequest.from_dict()`;
8. verify request workflow, explicit thread ID when present, and policy limits
   match RuntimeState;
9. recalculate index SHA and compare with `state.source.index_sha256`;
10. instantiate RuntimeAgentAdapter and read documents so existing note hash
    verification remains active;
11. derive exactly one active retrieval task ID from model records matching
    `(state.run_id, agent_id="retrieval_agent")`;
12. reject zero or multiple task IDs.

Do not accept replacement query/state/task/call values from the caller.

### Recovery preflight

Construct the existing ledger from `state.tool_ledger` and call
`decide_model_resume(state, ledger=...)`.

Proceed into composition only for an existing decision that the accepted loop
can consume without external intervention, such as:

- `safe_to_invoke_model`;
- `reuse_durable_model_response`;
- `resume_from_tool_result`.

For:

- `requires_verification`;
- `requires_manual_decision`;
- unsupported explicit reinvocation;

return/raise an existing safe error carrying the recovery reason. Do not invoke
the model/provider, do not invoke an executor, and do not save a terminal
replacement state. The exact durable ambiguous checkpoint remains the source
of truth.

For `already_terminal`, do not re-run composition. Return the existing status
or reject the illegal resume through an existing state-transition error; choose
the smallest behavior consistent with current RuntimeEngine conventions and
freeze it in a test.

### Composition restoration

For a resumable decision:

- open `OptionalTracer(..., load_existing=True)`;
- use the existing `ModelArtifactStore(checkpoint_dir / "models")`;
- build a ToolExecutionLedger from the state;
- recreate the same RuntimeState and ledger checkpoint callbacks with strict
  run/thread identity checks;
- call RuntimeAgentAdapter with:
  - original run/workflow/query/source/policy limits;
  - latest RuntimeState;
  - existing ledger/artifact store/tracer;
  - resume mode;
  - canonical retrieval task ID;
- write the result artifact and final state under the original run ID;
- preserve current result/manifest semantics.

Avoid a broad refactor of `start_multi_agent()`. A tiny private finalization
helper is allowed only if it removes exact duplicate code without changing the
fresh behavior and has regression coverage.

### W2 checkpoint

Run RED-1 and RED-3. RED-1 should advance past missing API. RED-3 must block
without any adapter/provider/executor invocation.

## 6. W3 — Preserve Retrieval Scope Through Composition

### RuntimeAgentAdapter

Add optional keyword-only parameters with fresh-safe defaults, for example:

```python
resume_model_loop: bool = False
retrieval_task_id: str | None = None
```

Rules:

- fresh mode rejects or ignores no hidden resume identity;
- resume mode requires a non-empty canonical retrieval task ID;
- adapter does not call `decide_model_resume()` and does not own recovery;
- adapter passes resume mode to RetrievalAgent and task ID to Coordinator;
- trusted callbacks, ToolRuntime, ledger, and checkpoint callbacks are
  unchanged.

### Coordinator

Add one optional `retrieval_task_id` argument to `run()`.

Rules:

- when absent, generate the existing random retrieval task ID;
- when present, use it only for the top-level retrieval AgentTask;
- validate it with existing AgentTask/model identity constraints;
- do not reuse it for Curator or Reviewer;
- do not add general task restoration or Coordinator replay;
- all budgets, fallback behavior, handoffs, and projections remain unchanged.

### RetrievalAgent

Add an explicit constructor or execution flag for resume mode, default false.

Rules:

- fresh mode calls the existing `SingleAgentModelLoop.run()` exactly as M0.3;
- resume mode calls the existing `SingleAgentModelLoop.resume()` with the same
  state/task/agent/instruction/tools/artifact/event/checkpoint dependencies;
- no deterministic search/read sequencing is reintroduced;
- no recovery decision logic is duplicated;
- AgentResult projection is unchanged;
- current provider and tool budget calculations remain unchanged unless a RED
  test proves a narrow resume-count bug; such a finding must be reported before
  expanding scope.

### W3 checkpoint

Run RED-2 and RED-4. They must now pass while fresh W0-A/W0-B remain green.

## 7. W4 — Complete Offline Acceptance Matrix

Run the full M0.4 test file and add only missing assertions required by the
SPEC.

### State consistency

For each primary success path, load the latest state through the checkpointer
and assert:

- state/result run and thread identities;
- completed termination and result ref;
- agent task status;
- full model execution sequence;
- full tool ledger sequence;
- evidence projection.

### Artifact consistency

Read request, response, and observation artifacts using each record's expected
SHA-256. Assert runtime identity fields and normalized action/result match the
records.

Do not inspect or snapshot hidden reasoning/raw provider payloads.

### Trace consistency

Read the production trace and assert:

- monotonic event sequence;
- one `tool.called` and one terminal tool event per executed `call_id`;
- no duplicate `tool.called` after terminal-result resume;
- expected agent task/reviewer events;
- no unexpected handoff for ask workflow.

Do not add a new event type or trace subsystem.

### Budget regression

Run at least one existing max-step/provider-budget case from the M0.3/P8 slice.
Do not duplicate its full matrix in the M0.4 file.

## 8. W5 — Self-Review And Minimal Corrections

Before handing off, the Worker must review the diff against these questions:

1. Does any fresh path now depend on resume-only arguments?
2. Can a caller inject query, task ID, ToolCall, or RuntimeState into resume?
3. Does production resume call the existing recovery authority?
4. Can ambiguous `request_sent` invoke the provider or executor?
5. Can resume generate a new retrieval task ID?
6. Does policy rehydration see the original task scope?
7. Can terminal ToolResult resume append a duplicate ledger record?
8. Does the next legitimate model turn remain distinguishable from replay?
9. Was old HITL `RuntimeEngine.resume()` changed?
10. Was any frozen M0.2/M0.3 contract changed?
11. Was any real Vault, credential, network, or write tool used?
12. Is exactly-once language absent?

Fix only an evidenced M0.4 defect and add a regression test for every fix.

## 9. Focused Validation Commands

### M0.4 acceptance

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_m04_production_e2e_resume.py
```

### M0.3 production trajectory

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py
```

### M0.1 Retrieval ToolRuntime semantics

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_retrieval_tool_runtime.py
```

### Frozen M0.2 durability and provider integration

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_p85_durable_provider.py tests/unit/test_p85_wp3_durable_integration.py tests/unit/test_p84_durable_model_loop.py tests/unit/test_p84_artifacts.py
```

### Production composition compatibility

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_handoff_trace.py tests/integration/test_multi_agent_workflow.py tests/integration/test_memory_runtime.py
```

### Static validation

```powershell
python -m compileall -q src/linkloom tests
git diff --check
git status --short
```

### Full formal suite when environment permits

```powershell
python -m pytest -q -p no:cacheprovider tests
```

If protected Windows ACL paths prevent collection/setup, report exact paths and
errors. Do not delete, chmod, take ownership of, or otherwise repair unrelated
temporary directories.

## 10. Synthetic Production Smoke

After focused tests, run one offline copy-based smoke that uses:

- synthetic Vault/index;
- `RuntimeEngine.start_multi_agent()`;
- Policy B `search -> read -> final`;
- persisted checkpoint/model artifacts/ledger/trace/result;
- no monkeypatch of the high-level composition;
- no network or credential.

Record:

- command or focused test selector;
- final RunStatus;
- model request count;
- executor count;
- ledger count;
- result artifact path;
- checkpoint and trace roots.

This is an offline production-composition smoke, not the real-provider smoke.

## 11. Optional Real-Provider Smoke Gate — Not Yet Authorized

Do not enter this package under the current approval.

If the human later supplies a separate approval, it must state:

- synthetic fixture/Vault path;
- provider and exact model ID;
- legal credential mechanism;
- data disclosure;
- request, token/output, time, and cost cap;
- whether a test-only official SDK client wrapper may be created;
- artifact and redaction expectations.

Only then may the Worker add a non-default smoke file such as:

```text
tests/smoke/test_m04_real_provider_smoke.py
```

The smoke must enter through `RuntimeEngine.start_multi_agent()` with the
existing `GeminiProviderAdapter`. It must not add Gemini CLI, subprocess,
provider retry, or local tool execution inside the provider.

Without separate approval, task evidence must retain:

```text
real_provider_smoke_test = NOT_RUN
```

## 12. Acceptance Evidence Extraction

The Worker must write exact evidence into `task.md`.

### Changed files

List every changed file and explain its M0.4 purpose. Any file outside the
approved boundary is a blocker unless separately approved before the edit.

### Production paths

Record fresh and resume traces with actual public methods and identity flow.

### RED evidence

Record:

- tests already GREEN because they characterize M0.3;
- resume tests RED before implementation;
- exact exception/assertion proving the intended seam was missing.

### Invocation evidence

For each resume scenario, report:

| Scenario | Provider/model before | Provider/model after | Executor before | Executor after | Result |
|---|---:|---:|---:|---:|---|
| terminal ToolResult | | | | | |
| ambiguous request_sent | | | | | |

Also record operation identities so a legitimate next turn is not mislabeled a
duplicate.

### Durable consistency

Record:

- model record statuses;
- tool ledger statuses/call IDs;
- artifact refs and verification result;
- checkpoint count/latest status;
- trace correlation;
- final result ref and evidence.

### Test totals

For each command, report passed, failed, skipped, and unavailable counts.

### Gate statement

Report separately:

- Gate B evidence/readiness;
- Gate A offline evidence;
- `real_provider_smoke_test` result;
- whether final M0.4 acceptance is eligible or blocked on the smoke.

## 13. Reviewer Acceptance Focus

The independent Reviewer should prioritize:

1. primary tests actually enter production composition;
2. new resume API is not old HITL resume renamed;
3. canonical task identity survives restart;
4. ambiguous state is not replayed or destructively rewritten;
5. terminal ToolResult is reused without provider/executor replay;
6. next model turn is legitimate and receives exact durable context;
7. fresh M0.3 trajectory remains model-owned;
8. empty/failure/budget semantics remain frozen;
9. result, state, ledger, artifacts, and trace agree;
10. real Provider and Gate A claims are honest.

The Reviewer must not silently fix findings.

## 14. Stop Rules

Stop and report `BLOCKED` if:

- production resume requires changing accepted recovery decisions;
- exact retrieval task identity cannot be reconstructed from durable records;
- supporting the test requires a general Coordinator replay;
- `RuntimeState` schema must change;
- model-loop or ToolRuntime internals appear to require redesign;
- the only way to pass is to replay a provider/tool call;
- a test requires a real Vault or write-capable tool;
- a real smoke lacks explicit credential/network/budget/disclosure approval;
- existing dirty changes overlap and cannot be preserved safely;
- focused regression failure is caused by M0.4 and cannot be fixed within the
  approved files.

Do not enter M1, create a follow-on Runtime phase, or broaden the task to make
the diff aesthetically cleaner.

## 15. Worker Implementation Sequence

Execute in this order without waiting for additional confirmation unless a
stop rule triggers:

1. preflight and task record;
2. W0 characterization tests;
3. W1 RED resume tests and recorded failure;
4. W2 production resume entrypoint/preflight;
5. W3 canonical identity and resume-mode wiring;
6. focused GREEN M0.4 tests;
7. W4 state/artifact/ledger/trace assertions;
8. frozen M0.1-M0.3/P8 regressions;
9. offline synthetic production smoke;
10. W5 self-review and only necessary regression-backed fixes;
11. compile/diff/status checks;
12. task evidence and independent Reviewer handoff;
13. stop before any real-provider smoke, commit, push, PR, or M1.

## Learning Reflection

### Step

M0.4 Worker plan — production E2E and process-restart acceptance.

### What Changed

The plan adds one production resume entrypoint and three narrow resume-mode
parameters while keeping all execution and recovery ownership in accepted
components.

### What I Learned

1. Resume safety is an identity problem as much as a persistence problem.
2. E2E evidence is strongest when final output, durable facts, and invocation
   counters independently agree.

### Evidence

- RED/characterization split;
- crash-boundary fixtures;
- no-duplicate counter table;
- production file boundary;
- regression commands;
- Gate A/B reporting rules.

### Next Step

A separate Worker implements W0-W5 offline and hands evidence to an independent
Reviewer. A real-provider smoke remains a separate human authorization gate.
