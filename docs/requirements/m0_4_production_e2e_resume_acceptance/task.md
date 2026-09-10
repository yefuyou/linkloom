# M0.4 Production E2E + Resume Acceptance — Task Record

Status: **ACCEPTED — Gate A and Gate B COMPLETE** under
[SPEC.md](SPEC.md) and [implementation_plan.md](implementation_plan.md).

The approved Worker boundary is offline W0-W5 only. A real-provider smoke is
required for final Gate A/M0.4 acceptance but remains separately gated and is
not authorized by this task record.

## Step

- Role: Planner
- Feature: M0.4 Production E2E + Resume Acceptance
- Date: 2026-09-01
- Parent milestone: M0 — Production Agent Closeout
- Primary gates: Gate A — Real Model; Gate B — Real Agent Loop
- Human boundary: M0.1/M0.2/M0.3 `ACCEPTED` and frozen; M0.4 planning only;
  no Worker implementation during this step; no M1.

## Planner Recovery Evidence

- Read repository governance and the `spec-driven-agent-dev` Planner/Worker/
  Reviewer contracts.
- Read root `SPEC.md`, `DEV_SPEC.md`, `docs/PRODUCT_ROADMAP.md`, multi-agent and
  acceptance governance, and the Master SPEC.
- Read accepted M0.1-M0.3 planning/task evidence and inspected the surrounding
  production code rather than relying on design documents alone.
- Inspected:
  - `RuntimeEngine.start_multi_agent()` and old HITL `resume()`;
  - RuntimeState/checkpointer/artifact contracts;
  - RuntimeAgentAdapter composition;
  - Coordinator task/policy construction;
  - RetrievalAgent model-loop delegation;
  - SingleAgentModelLoop durable `.run()`/`.resume()`;
  - `decide_model_resume()`;
  - ToolPolicyEnforcer ledger rehydration;
  - GeminiProviderAdapter boundary;
  - current production and durability tests.
- Recorded current Git status and latest commits without modifying Git state.
- Preserved the existing dirty worktree.
- Did not modify production code or tests, run a Provider, access a credential,
  access a real Vault, or perform Git remote operations.

## Repository State At Planning

- Branch/HEAD observed: `3000056 feat(runtime): build durable model-driven agent runtime`.
- Previous commits:
  - `e5652b4 feat: complete LinkLoom agent loop and safe writeback`;
  - `5cab199 WP-0: Asset freeze, credential isolation, and contract alignment`.
- Worktree: dirty with earlier user/agent changes and protected pytest
  directories that emit access warnings.
- M0.4 planning directory before this step: absent.
- M0.3 repository task record: still `READY FOR INDEPENDENT REVIEW`, stale
  relative to the human's explicit `ACCEPTED` declaration.
- Real provider smoke: `NOT_RUN`.

## Most Recent Accepted Test Evidence

| Slice | Recorded result |
|---|---:|
| M0.3 focused | 11 passed |
| Retrieval ToolRuntime | 10 passed |
| handoff/multi-agent/memory compatibility | 12 passed |
| M0.2 durability | 90 passed |
| P8 model/tool/runtime boundary | 32 passed |
| synthetic production two-policy smoke | 1 passed |
| full formal suite | NOT_RUN due permitted environment constraints |

These are predecessor records, not fresh M0.4 Worker results.

## Planner Findings

### Fresh production composition

`RuntimeEngine.start_multi_agent()` already owns the full fresh composition:

```text
RuntimeEngine
  -> RuntimeAgentAdapter
  -> Coordinator
  -> model-driven RetrievalAgent
  -> durable SingleAgentModelLoop
  -> Model/Provider adapter
  -> ToolRuntime
  -> ToolExecutionLedger/checkpoint/artifacts
  -> AgentResult/Coordinator result
  -> final RuntimeState/result/trace
```

Decision:

```text
FRESH E2E: NO PRODUCTION CHANGE REQUIRED
```

### CLI boundary

Current CLI constructs RuntimeEngine without a model/provider and returns
`MODEL_NOT_CONFIGURED`. M0.4 does not add CLI provider bootstrap. The accepted
production test entrypoint is the RuntimeEngine Python API with explicit model
injection.

### Resume production gap

- Lower-level durable resume and recovery decisions exist and are accepted.
- No production code calls `SingleAgentModelLoop.resume()`.
- Existing `RuntimeEngine.resume()` is only P2 HITL interrupt resume.
- Coordinator generates a new retrieval task ID each invocation.
- Adapter/RetrievalAgent always choose fresh execution.

Decision:

```text
RESUME E2E: MINIMAL PRODUCTION WIRING REQUIRED
```

Approved seam:

- `RuntimeEngine.resume_multi_agent(thread_id)`;
- optional resume mode through RuntimeAgentAdapter;
- canonical durable retrieval task ID injected into Coordinator;
- RetrievalAgent selects existing `.resume()`;
- no changes to model loop, recovery, state models, artifacts, ToolRuntime,
  policy, ledger, provider, CLI, Memory, or Evaluation.

### Ambiguous state

`request_sent`/`response_obtained` is already classified by
`decide_model_resume()` as verification-required. Production resume must
preflight that decision and stop without provider/executor invocation or a
terminal Coordinator rewrite.

### Real Provider boundary

The Master requires one separately approved, budget-capped synthetic
real-provider production run to close Gate A. Offline tests are necessary but
insufficient.

Current exact status:

```text
real_provider_smoke_test = NOT_RUN
Gate A = PARTIAL / BLOCKED_ON_REAL_PROVIDER_SMOKE
```

This does not block offline Worker implementation. It does block final M0.4/M0
acceptance if unchanged.

## Approved Deliverables

- [x] Current production E2E trace reconstructed.
- [x] Exact acceptance boundary frozen.
- [x] Production fresh and resume entrypoints selected.
- [x] Search-final and search-read-final scenarios specified.
- [x] Terminal ToolResult and ambiguous request-sent resume scenarios
  specified.
- [x] Provider/model and executor duplicate-call counters specified.
- [x] State/model-record/artifact/ledger/trace/result assertions specified.
- [x] Gate A/B implications specified.
- [x] Minimal production diff specified.
- [x] Test file and RED tests specified.
- [x] Worker sequence and stop rules specified.
- [x] Hard non-goals frozen.
- [x] Real-provider smoke requirement and separate authorization boundary
  specified.

## Worker Checklist

### W0 — Characterization

- [x] Create `tests/integration/test_m04_production_e2e_resume.py`.
- [x] Add production search-final test.
- [x] Add production search-read-final test.
- [x] Add production successful-empty-search test.
- [x] Add compact search/read/provider failure cases.
- [x] Record which tests are already GREEN before M0.4 production edits.

### W1 — RED resume tests

- [x] Add missing production resume entrypoint test.
- [x] Add terminal ToolResult process-restart test.
- [x] Add ambiguous request-sent no-replay test.
- [x] Add canonical retrieval task identity test.
- [x] Record exact RED failures before implementation.

### W2 — RuntimeEngine

- [x] Add `resume_multi_agent(thread_id)` only.
- [x] Load original state/request/source/policy.
- [x] Derive exactly one canonical retrieval task ID.
- [x] Call existing `decide_model_resume()`.
- [x] Block ambiguous/manual decisions without durable terminal rewrite.
- [x] Recreate existing ledger/artifact/tracer/checkpoint composition.
- [x] Preserve old HITL `resume()` unchanged.

### W3 — composition identity

- [x] Add backward-compatible adapter resume parameters.
- [x] Add optional retrieval task ID to Coordinator.
- [x] Add explicit RetrievalAgent resume selection.
- [x] Preserve fresh defaults and M0.3 trajectory ownership.

### W4 — evidence

- [x] Verify RuntimeState/result consistency.
- [x] Verify model artifact refs/SHA/identity.
- [x] Verify ledger call IDs/status/results.
- [x] Verify trace tool correlation and no duplicate called event.
- [x] Verify no unexpected ask handoff.
- [x] Verify provider/executor before/after counters.

### W5 — validation and handoff

- [x] Run M0.4 focused suite.
- [x] Run M0.1/M0.2/M0.3/P8 regression slices.
- [x] Run offline synthetic production smoke.
- [x] Run compileall, diff check, and status.
- [x] Perform Worker self-review and only regression-backed corrections.
- [x] Update this task record with exact evidence.
- [x] Hand off to independent Reviewer without self-acceptance.
- [x] Stop before real Provider smoke, commit, push, PR, or M1.

## Expected File Boundary

### Mandatory Worker production files

- `src/linkloom/runtime/graph.py`
- `src/linkloom/agents/runtime_adapter.py`
- `src/linkloom/agents/coordinator.py`
- `src/linkloom/agents/retrieval_agent.py`

### Mandatory Worker test/task files

- `tests/integration/test_m04_production_e2e_resume.py`
- this `task.md`

### Forbidden without a new approval

- model loop, recovery, RuntimeState/models, artifacts;
- ToolRuntime, ledger, policy, registry, contracts;
- Provider/Model contracts and Gemini adapter;
- CLI or dependency files;
- Scanner, Memory, Evaluation, writeback, UI;
- existing predecessor tests except separately approved fixture correction.

## Planned RED/GREEN Matrix

| Test | Before production edit | Required final |
|---|---|---|
| production search -> final | may already pass | pass |
| production search -> read -> final | may already pass | pass |
| successful empty search | may already pass | pass |
| compact failure semantics | may already pass | pass |
| `resume_multi_agent` API | RED: absent | pass |
| terminal ToolResult restart | RED: no production resume/identity wiring | pass |
| ambiguous request_sent | RED: no production resume gate | pass, no replay |
| canonical retrieval task scope | RED: Coordinator regenerates ID | pass |

The Worker must not claim already-GREEN characterization tests as RED.

## Duplicate-Call Evidence Template

| Scenario | Operation identity | Before crash | After resume | Duplicate count |
|---|---|---:|---:|---:|
| terminal ToolResult — original provider/model turn | | | | |
| terminal ToolResult — legitimate next model turn | | | | N/A |
| terminal ToolResult — original executor | | | | |
| ambiguous request_sent — provider/model | | | | |
| ambiguous request_sent — executor | | | | |

Claim boundary:

```text
durable reuse produced zero duplicate calls at the tested boundaries
```

Forbidden claim:

```text
exactly-once execution
```

## Durable Consistency Evidence Template

For each primary scenario, Worker fills:

- run/thread/task/agent/call identities:
- model execution status sequence:
- tool ledger status sequence:
- request artifact verification:
- response artifact verification:
- observation artifact verification:
- latest checkpoint status and ID:
- result artifact ref:
- final evidence refs:
- trace event correlation:
- fallback/handoff semantics:

## Validation Record Template

| Command/slice | Passed | Failed | Skipped | Unavailable | Notes |
|---|---:|---:|---:|---:|---|
| M0.4 focused | | | | | |
| M0.3 production | | | | | |
| Retrieval ToolRuntime | | | | | |
| M0.2/P8 durability | | | | | |
| composition compatibility | | | | | |
| full tests | | | | | |
| compileall | | | | | |
| git diff --check | | | | | |
| offline synthetic smoke | | | | | |

## Gate Decision Template

### Gate B

- production direct-final evidence:
- production multi-tool evidence:
- failure/budget evidence:
- terminal-result resume evidence:
- ambiguous no-replay evidence:
- independent Reviewer verdict:
- status: `INCOMPLETE | COMPLETE`

### Gate A

- provider-neutral production integration evidence:
- credential isolation evidence:
- real provider smoke approval:
- real provider smoke result:
- usage/latency/model identity evidence:
- independent Reviewer verdict:
- status: `PARTIAL / BLOCKED_ON_REAL_PROVIDER_SMOKE | COMPLETE`

### M0.4 final

- status:
  `OFFLINE_READY_FOR_REVIEW | BLOCKED_ON_REAL_PROVIDER_SMOKE | ACCEPTED`
- M0 exit eligible: `YES | NO`

## Real-Provider Smoke Gate

Current status:

```text
real_provider_smoke_test = NOT_RUN
```

Do not change this value unless a separate human approval names:

- synthetic Vault/fixture;
- exact Provider and model;
- legal credential boundary;
- prompt/evidence disclosure;
- request/output/time/cost limits;
- supported official client setup;
- retained normalized artifacts;
- stop conditions.

No smoke may use Gemini CLI, shell/subprocess, a real Vault, provider-owned
local tool execution, automatic retry, or raw credential/payload persistence.

## Safety Confirmation — Planner

- No production code changed.
- No tests changed.
- No real Vault was read or written.
- No network, Provider, credential, or external cost was used.
- No write-capable tool was introduced.
- No commit, push, PR, branch rewrite, reset, rebase, stash, or clean occurred.
- Existing dirty worktree content was preserved.

## Learning Points

1. **Production composition acceptance**: a direct RetrievalAgent or loop test
   cannot prove the dependency wiring that users actually invoke.
2. **Durable replay safety**: one must preserve operation identity and count
   real invocations before claiming that a restart avoided duplicates.

## Interview Evidence Planned

- production fresh/resume call graph;
- two model-owned policies with the same runtime composition;
- crash-boundary timeline;
- provider/executor counter table;
- ledger/model-record/artifact/checkpoint/trace consistency report;
- honest Gate A/B status and exactly-once limitation.

## Remaining Issues Before Worker Starts

- No architecture blocker exists for offline implementation.
- Production resume wiring is missing by design and is the approved Worker
  change.
- Final M0.4 acceptance remains conditional on a separately authorized real
  Provider smoke.
- The stale M0.3 task status should eventually be reconciled by the main
  coordinator, not by the M0.4 Worker.

## Planner Verdict

```text
APPROVED FOR WORKER IMPLEMENTATION
```

This verdict authorizes only the offline M0.4 Worker boundary. It does not
authorize a real Provider call, acceptance, M0 exit, M1, or Git write.

## Worker Evidence — 2026-09-07

Human explicitly authorized offline W0-W5. Initial HEAD remains `3000056`;
existing modified/untracked files were preserved. Only the four approved
production files, the new M0.4 integration test, and this record are in scope.

Pre-implementation RED: `python -m pytest -q -p no:cacheprovider
tests/integration/test_m04_production_e2e_resume.py` returned **6 passed,
4 failed**. All four failures were `AttributeError: RuntimeEngine has no
attribute resume_multi_agent`, after real SQLite checkpoints from production
start. Fresh trajectories/empty search/tool failures/provider error already
passed. The tests use cold engine/checkpointer objects and synthetic Vaults.

### Implementation and self-review

Only these six files were edited by this Worker:
- src/linkloom/runtime/graph.py: production resume preflight/composition; shared existing finalization.
- src/linkloom/agents/runtime_adapter.py: resume context, durable request binding and verified evidence cache restoration.
- src/linkloom/agents/coordinator.py: optional canonical retrieval task ID.
- src/linkloom/agents/retrieval_agent.py: existing loop.resume selection and remaining step allowance on resume.
- tests/integration/test_m04_production_e2e_resume.py: 21 synthetic production acceptance cases.
- this task record.

The four production files were already dirty before this Worker. The whole
HEAD diff is not attributable to M0.4. No frozen lower-layer files were edited.

First wiring yielded 8 passed / 2 failed: a fresh adapter lost its ephemeral
verified-evidence cache. Restoration now consumes canonical completed ledger
results and validates current source hash, quote and quote hash without
replaying tools. Expanded regression tests then exposed reset step allowance,
an unnormalized changed-source loader error, and mutable persisted query
continuation (3 failed / 3 passed / 10 deselected). Minimal fixes preserve
remaining step allowance, normalize stale sources and bind the original query
to the first SHA-verified durable request. All are regression-covered.

Old HITL resume() was compared with the initial source and is unchanged.
Resume only supports the approved active retrieval/model-loop boundary, not
general Coordinator replay or already-completed runs. It uses existing
decide_model_resume and SingleAgentModelLoop.resume; no recovery engine,
provider retry, new state model or deterministic retrieval sequence was added.

### Fresh validation results

| Check | Result |
|---|---|
| M0.4 focused, final rerun | 21 passed in 8.96s |
| M0.3 production | 11 passed |
| Retrieval ToolRuntime / M0.1 compatibility | 10 passed |
| handoff + multi-agent + memory compatibility | 12 passed |
| M0.2/P8 durability: p85_durable_provider, p85_wp3_durable_integration, p84_durable_model_loop, p84_artifacts | 106 passed |
| Synthetic production smoke | Included in 17: two policies, empty/failure cases and cold-composition resume |
| Full formal tests | 491 passed, 33 failed, 143 errors |
| compileall src/linkloom tests | Exit 0; protected tests/.pytest-trajectory-2b-data could not be listed |
| Targeted compile of changed production/test files | Passed |
| git diff --check | Passed; existing CRLF warnings only |

Full-suite failures/errors are in existing Windows temporary-directory ACL and
legacy relation/dataset/evaluation paths. They were not repaired. Additional
old P2/HITL/trace/failure-injection tests with a workspace basetemp produced
10 setup errors (WinError 5), so that runtime regression is unavailable, not
claimed green. No ACL changes or unrelated test changes were made.

Reproduce focused validation:
```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_m04_production_e2e_resume.py
python -m pytest -q -p no:cacheprovider tests/integration/test_p85_durable_provider.py tests/unit/test_p85_wp3_durable_integration.py tests/unit/test_p84_durable_model_loop.py tests/unit/test_p84_artifacts.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_retrieval_tool_runtime.py tests/integration/test_handoff_trace.py tests/integration/test_multi_agent_workflow.py tests/integration/test_memory_runtime.py
```

### Counter and durable-consistency evidence

| Crash boundary / continuation | Model before / after | Executor before / after | Duplicate old model / executor calls |
|---|---:|---:|---:|
| terminal search result -> Final | 1 / 1 | 1 / 0 | 0 / 0 |
| terminal search result -> read -> Final | 1 / 2 | 1 / 1 (new read) | 0 / 0 |
| response durable before search -> Final | 1 / 1 | 0 / 1 (first search) | 0 / 0 |
| ambiguous request_sent | 1 / 0 | 0 / 0 | 0 / 0 |

Crashes are test-only BaseException injection followed by newly constructed
engine/provider/SQLite checkpointer objects; this is a simulated process
restart, not an OS subprocess/kill test. Subsequent model turns are legitimate
continuation, not duplicate calls. Claim: zero duplicate calls within tested
durable boundaries, NOT exactly-once.

Retained synthetic evidence:
- .tmp/m04-bf7407e34d0c4fe2b01053c66b80e1aa/resume_evidence.json
- .tmp/m04-47e922cb33e84fea8ff1e6a76ca84a6f/resume_evidence.json

The first sample preserves run_p4_bc6f0bc53a1d / m04-thread /
task_854bb4d9 / retrieval_agent / proposal:1; only turn:2 is invoked after
restart. Model statuses are tool_result_durable, completed; ledger completed;
AgentResult completed with ev_p1_0001 and ev_p1_0002 and no warnings/error.
Each sample directory retains SQLite checkpoints, checkpoint/model artifacts,
result JSON and traces. Tests jointly assert run/task/agent/call identities,
artifact SHA/identity, AgentResult evidence refs, RuntimeState/result, terminal
ledger facts, monotonic trace and correlated tool called/terminal events.
Successful empty search remains completed with no fallback; real search/read
failure remains observable, failed in the ledger and safely projected.

Ambiguous resume returns MODEL_RESUME_REQUIRES_VERIFICATION without invoking
provider/executor or rewriting the saved running checkpoint as completed/failed.
The failed return represents the resume attempt, not a new durable run outcome.

### Scope and gate handoff

- No network, credential, real Vault, Gemini, commit, push, PR or M1.
- real_provider_smoke_test = NOT_RUN.
- Gate A = PARTIAL / pending separately approved real Provider production smoke.
- Gate B = offline evidence ready; independent acceptance still pending.
- M0.4 = OFFLINE_READY_FOR_REVIEW, not ACCEPTED; M0 exit is not authorized.
- Next action: independent Reviewer checks the six-file delta and retained evidence.
- Learning/interview evidence: production composition vs isolated unit testing;
  identity-bound durable reuse vs an unsupported exactly-once claim.

## Reviewer Rework — Durable Final Zero-Budget Recovery — 2026-09-07

The independent Reviewer found one blocking M0.4 defect: RetrievalAgent's
resume preflight rejected zero remaining provider/step budget before the
canonical durable loop could consume an already durable Final response. The
required distinction is now covered by four regression tests:

- durable Final + zero provider budget: completed, post-resume model calls 0,
  executor calls 0;
- durable Final + zero step budget: completed, post-resume model calls 0,
  executor calls 0;
- terminal ToolResult + zero provider budget: existing budget error, no new
  provider or executor call;
- terminal ToolResult + zero step budget: existing max-step error, no new
  provider or executor call.

The minimal implementation keeps `SingleAgentModelLoop` and recovery as the
only recovery authority. `RetrievalAgent` recognizes only the narrowly scoped
latest `response_durable` Final record to allow one local loop-consumption
slot; it does not reset either budget and the loop still validates/reuses the
durable artifact. A resume-only Coordinator allowance of two units was needed
for the already-spent model steps plus Reviewer/finalization: the existing
Coordinator check rejects `total_steps >= max_total_steps`, so this allowance
does not authorize another model invocation.

Post-fix validation:

| Slice | Result |
|---|---:|
| new budget regressions | 4 passed |
| full M0.4 focused | 21 passed |
| M0.3 production | 11 passed |
| M0.1 Retrieval ToolRuntime | 10 passed |
| M0.2/P8 durability | 106 passed |
| composition compatibility | 12 passed |
| compileall | exit 0; existing protected test directory warning |
| git diff --check | passed; existing line-ending warnings only |

No M0.2 model-loop/recovery/budget internals, Provider, Memory, Evaluation,
real Vault, network, credentials, commit, push, PR, or M1 content was added.
The status remains `READY FOR INDEPENDENT REVIEW — OFFLINE`; this Worker does
not accept M0.4.

## Gate A manual smoke preparation — 2026-09-09

Human declares M0.4 ACCEPTED, M0 Offline CLOSED, Gate B COMPLETE — OFFLINE.
This subsequent authorization builds only the manual smoke harness. Gate A
remains PARTIAL; real_provider_smoke_test = NOT_RUN.

Changed files: pyproject.toml; tests/smoke/test_m04_real_provider_smoke.py;
this preparation record. No production-source changes in this task.

Optional dependency: smoke = [pytest>=8.0, google-genai==2.22.0]. User explicitly
authorized upgrading the current Python environment. Verified installed 2.22.0,
official imports and HttpOptions(retry_options=HttpRetryOptions(attempts=1)):
SDK field documentation states attempts includes the original request and 1
means no retries. Timeout is 30 seconds; wrapper cap is two model calls, with
automatic function calling disabled and max_output_tokens=1024 per call.
Pip also upgraded google-auth/pydantic and reported shared-environment conflicts:
google-adk 1.22.0 and google-cloud-aiplatform 1.133.0 require google-genai<2.
Those unrelated packages were not upgraded. SDK installation used PyPI; no
Gemini/provider request was made.

The test-only wrapper implements GeminiClient.generate_content and delegates
once to SDK models.generate_content. Existing GeminiProviderAdapter performs
all normalization; RuntimeEngine.start_multi_agent is the acceptance entry.
SDK 2.22.0 unconditionally calls get_env_api_key even with an explicit key;
the wrapper patches that fallback to None during construction, without reading
host credentials. An offline guarded-environment test verifies this behavior.

Opt-in must equal LINKLOOM_RUN_REAL_PROVIDER_SMOKE=1 before any key lookup.
Only GEMINI_API_KEY is accepted. Model defaults to gemini-3.8-flash and may be
overridden using LINKLOOM_GATE_A_MODEL; empty/malformed overrides fail safely.
SDK log output is suppressed during construction/calls, exceptions have constant
messages and hidden pytest frames, and exact key echo is blocked before adapter
normalization. Successful runs scan retained artifacts for the supplied key and
print only a compact sanitized summary. No key is put in durable state.

Synthetic source is a private copy of tests/fixtures/sample_vault; Scanner builds
its index. Snapshots verify source and copy remain unchanged. The originally
suggested hyphenated query splits into common lexical terms including "a" and
matches Scanner Overview in this fixture. The fixed equivalent no-match query
is zzzz_gate_a_no_match (one token). This adjustment was made offline before
any authorized real attempt, and does not change retrieval production logic.
RunRequest limits remain max_provider_requests=2, max_steps=4, dry_run=True.

Validation: harness 15 passed / 1 skipped; the skipped test is the real smoke.
Gemini adapter + M0.4 production regression: 49 passed (28 + 21).
Offline tests cover opt-in-before-key, absent key, invalid/default model, safe
construction, no credential fallback, SDK typed mapping, two-call cap, isolated
fixture, production composition, key-echo rejection, and wrong model behavior
(direct Final, wrong tool, invalid args, repeated tool). These are preparation
checks, not evidence of a real-provider PASS.

Future USER-only execution (not executed by Worker):

```powershell
Set-Location D:\webproject\Linkloom
$env:LINKLOOM_RUN_REAL_PROVIDER_SMOKE="1"
$env:LINKLOOM_GATE_A_MODEL="gemini-3.8-flash"
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new(
  "", (Read-Host "Gemini API key" -AsSecureString)
).Password
python -m pytest -q -s --tb=no -p no:cacheprovider tests/smoke/test_m04_real_provider_smoke.py::test_real_provider_smoke
Remove-Item Env:GEMINI_API_KEY
Remove-Item Env:LINKLOOM_GATE_A_MODEL
Remove-Item Env:LINKLOOM_RUN_REAL_PROVIDER_SMOKE
```

Preparation status: READY FOR USER-AUTHORIZED REAL PROVIDER SMOKE.
No host credentials accessed; no real Vault or Provider used; no commit/push/PR.

## Gate A real-provider smoke completion — 2026-09-10

The human completed the separately authorized, capped production smoke exactly
once. This record retains only the sanitized execution evidence; no credential,
raw request/response, provider payload, or real-Vault content is recorded.

| Field | Sanitized result |
|---|---|
| Provider / model | Gemini / `gemini-3.8-flash` |
| Provider calls | 2 |
| Local tool calls | 1 — `search_notes` |
| Trajectory | provider ToolCall -> local ToolRuntime -> ToolResult -> provider Final |
| Final agent status | `completed` |
| Model response origin | `provider` |
| Usage | available |
| Credential-leak check | `PASS` |
| Smoke pytest result | `1 passed` |

The run entered the production composition through `RuntimeEngine`, not a
direct adapter/unit-test path. It used the synthetic fixture copy, a read-only
tool, manual provider function calling, two-call and step caps, and no
automatic retry. The user instructed that no further real-provider runs be
made because this single successful run is sufficient Gate A evidence.

### Final gate decision

```text
M0.1 = ACCEPTED
M0.2 = ACCEPTED
M0.3 = ACCEPTED
M0.4 = ACCEPTED

Gate A = COMPLETE — real-provider production smoke
Gate B = COMPLETE — production Agent Loop
M0 = COMPLETE
```

Claim boundary: the tested production trajectory proves real Gemini
`model -> ToolCall -> local ToolRuntime -> ToolResult -> model Final` once. It
does not claim exactly-once execution, general multi-tool support, or a
production retry framework. The retained automated evidence remains the M0.4
offline/resume suite plus the sanitized one-pass smoke result.
