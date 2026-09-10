# M0.3 RetrievalAgent Model-Driven Migration — Task Record

Status: **ACCEPTED — final M0 closeout completed on 2026-09-10**.

The original Worker and review state below is retained as dated evidence.
M0.3 is accepted; later M0.4 acceptance closed Gate A, Gate B, and M0.

## Step

- Role: Planner
- Feature: M0.3 RetrievalAgent model-driven migration
- Date: 2026-08-30
- Human boundary: M0.1 `ACCEPTED`; M0.2 `ACCEPTED` and durability frozen;
  M0.3 Planner only; no M0.4/M1; final result must be architecture-ready or a
  specific blocker.

## Planner Recovery Evidence

- Read repository governance contracts and the `spec-driven-agent-dev` role
  contract/template.
- Read M0.1 accepted SPEC/task evidence, including the empty-search correction.
- Read M0.2 SPEC/plan/task and current accepted public loop implementation.
- Inspected current source for RuntimeEngine, RuntimeAgentAdapter, Coordinator,
  RetrievalAgent, SingleAgentModelLoop, ToolRuntime, ToolPolicyEnforcer,
  ToolExecutionLedger, AgentResult, RuntimeState, and ModelArtifactStore.
- Inspected focused production-path tests.
- Recorded dirty-worktree status and preserved all existing changes.
- Did not run tests, change production/tests, access a real Vault/Provider, or
  perform Git remote operations during Planner work.

## Current Production Facts

- [x] RuntimeEngine creates RuntimeAgentAdapter.
- [x] RuntimeAgentAdapter creates one retrieval ToolRuntime and RetrievalAgent.
- [x] Coordinator creates the retrieval task/policy and calls RetrievalAgent.
- [x] RetrievalAgent currently hard-codes search arguments and search-first.
- [x] RetrievalAgent currently loops every candidate and hard-codes read args.
- [x] RetrievalAgent currently decides when to stop and calls ToolRuntime
  directly.
- [x] Coordinator maps AgentResult refs into workflow evidence/fallback.
- [x] RuntimeEngine owns checkpointer/final RuntimeState.
- [x] M0.2 already owns durable model/tool/observation/final execution.
- [x] No M0.2 internal change is required by the recommended seam.

## Frozen Planner Deliverables

- [x] Current production retrieval trace.
- [x] Deterministic seams.
- [x] One recommended migration seam.
- [x] Before/after ownership table.
- [x] Target production trace.
- [x] Minimal production file list with necessity reasons.
- [x] Minimal test file list.
- [x] RED tests and expected failure causes.
- [x] Acceptance criteria and 18-point core matrix.
- [x] Compatibility strategy.
- [x] Risks and focused regressions.
- [x] Worker implementation sequence.
- [x] M0.2-internal blocker stop rule.
- [x] Non-goals and safety boundary.

## Frozen Production Boundary

- [x] `src/linkloom/agents/retrieval_agent.py`
- [x] `src/linkloom/agents/runtime_adapter.py`
- [x] `src/linkloom/runtime/graph.py`

Every other production file is inspection-only. In particular, M0.2 loop,
RuntimeState, ArtifactStore, Recovery, Provider, ToolRuntime, ledger, policy,
Coordinator, and CLI files must remain unchanged.

## Frozen Test Boundary

- [x] add `tests/integration/test_m03_model_driven_retrieval.py`
- [x] minimally update `tests/integration/test_retrieval_tool_runtime.py`
- [x] minimally update `tests/integration/test_handoff_trace.py`
- [x] minimally update `tests/integration/test_multi_agent_workflow.py`
- [x] minimally update `tests/integration/test_memory_runtime.py`

M0.2 and lower-level tool tests are regression-only and must not be rewritten
to make M0.3 pass.

## RED Checklist

- [x] Policy A proves `search -> final` with zero read.
- [x] Policy B proves `search -> selected read -> final`.
- [x] Search arguments are exact at proposal/executor/ledger.
- [x] Search ToolResult is exact in the next ModelTurnRequest.
- [x] Read arguments are exact at proposal/executor/ledger.
- [x] Read ToolResult is exact in the Final ModelTurnRequest.
- [x] Empty search reaches model before exact M0.1 completed result.
- [x] Search failure reaches model and remains explicit/fallback-visible.
- [x] Read failure reaches model and remains explicit.
- [x] Invalid/disallowed calls remain ToolRuntime/policy refusals.
- [x] Model/tool budgets stop without an extra call.
- [x] Missing model fails without deterministic fallback.

## Worker Checklist

- [x] Record preflight status and existing dirty paths.
- [x] Run and record RED before production edits.
- [x] Implement RuntimeEngine model/state/artifact plumbing only.
- [x] Implement Adapter composition/latest-state ledger projection only.
- [x] Replace RetrievalAgent sequencing with one durable M0.2 loop delegation.
- [x] Preserve exact model ToolCall business arguments.
- [x] Preserve exact ToolResult observations.
- [x] Preserve empty-search completed/no-fallback semantics.
- [x] Preserve explicit search/read failure evidence.
- [x] Run focused M0.3 tests.
- [x] Run M0.1 semantic regressions.
- [x] Run unchanged M0.2 durability regressions.
- [x] Run ToolRuntime/policy/ledger regressions.
- [x] Run compile/diff/status/scope audit.
- [x] Confirm no real Vault, Provider, credential, or network access.
- [x] Record `real_provider_smoke_test = NOT_RUN`.
- [x] Hand evidence to an independent Reviewer.
- [x] Do not commit, push, open a PR, or start M0.4.

## Core Acceptance Checklist

- [x] 1. Model chooses search.
- [x] 2. Search args reach ToolRuntime unchanged.
- [x] 3. Search observation returns to model.
- [x] 4. Model chooses read.
- [x] 5. Read args reach ToolRuntime unchanged.
- [x] 6. Read observation returns to model.
- [x] 7. Model chooses durable Final.
- [x] 8. Search-to-final has no forced read.
- [x] 9. Search-to-read-to-final completes.
- [x] 10. Empty search retains M0.1 semantics.
- [x] 11. Search failure is explicit.
- [x] 12. Read failure is explicit.
- [x] 13. Invalid/disallowed tools are runtime-refused.
- [x] 14. Max steps/budgets terminate exactly.
- [x] 15. Durable model/ledger/trace records match trajectory.
- [x] 16. RetrievalAgent has no direct tool sequencing/execution.
- [x] 17. M0.2 durability is reused, not copied.
- [x] 18. Offline FakeModel production E2E proves both policies.

## Worker Implementation Evidence

### Preflight Evidence

- Read `AGENTS.md`, the project SPEC/DEV_SPEC/roadmap/role contracts, the
  M0.3 SPEC, implementation plan, task record, and the required Worker/TDD/
  incremental-implementation instructions.
- `git status --short` recorded existing user changes in the master planning
  docs, P8.5 task record, `src/linkloom.egg-info/SOURCES.txt`, Coordinator,
  RetrievalAgent, RuntimeAgentAdapter, M0.2 model loop/models, and existing
  integration/unit tests, plus unrelated untracked planning/evaluation/test
  artifacts. None were reset, cleaned, or overwritten.
- Latest commits recorded: `3000056 feat(runtime): build durable model-driven
  agent runtime`, `e5652b4 feat: complete LinkLoom agent loop and safe
  writeback`, `5cab199 WP-0: Asset freeze, credential isolation, and contract
  alignment`.
- Confirmed the approved production boundary is only RetrievalAgent,
  RuntimeAgentAdapter, and RuntimeEngine; M0.2 durability files and
  Coordinator remain inspection-only.
- Confirmed all fixtures are synthetic. No real Vault, provider, credential,
  network, or Git remote operation was used.

### RED Results

- Required command: `python -m pytest -q
  tests/integration/test_m03_model_driven_retrieval.py`
- Initial run was environment-only: pytest could not create its default
  `tmp_path` base and raised `PermissionError: [WinError 5]` before tests.
- The new test file was then calibrated to use a unique explicit workspace
  `.tmp` directory without deleting existing paths. The rerun produced
  `6 failed, 3 warnings`.
- The five model-backed production-path tests failed with
  `TypeError: RuntimeEngine.__init__() got an unexpected keyword argument
  'model'`, proving the approved model dependency seam is absent.
- The missing-model test observed `status == completed` instead of the
  required explicit failure, proving the current deterministic fallback still
  runs when no model is configured.
- No production file had been edited when this RED evidence was captured.

### Exact Changed Files

- `src/linkloom/runtime/graph.py` — inject model, fail closed on missing/zero
  provider budget, maintain the canonical RuntimeState cursor, create the M0.2
  artifact store, and project the latest state into final checkpoints.
- `src/linkloom/agents/runtime_adapter.py` — validate and pass the four M0.3
  dependencies, keep one retrieval ToolRuntime, and project its latest state
  ledger.
- `src/linkloom/agents/retrieval_agent.py` — remove deterministic sequencing
  and delegate exactly once to `SingleAgentModelLoop` with safe AgentResult
  projection.
- `tests/integration/test_m03_model_driven_retrieval.py` — production-path
  FakeModel ownership, argument/observation, empty-search, refusal, budget,
  and missing-model evidence.
- `tests/integration/test_retrieval_tool_runtime.py` — model-backed adapter,
  failure-observation, canonical ledger, and existing tool-boundary coverage.
- `tests/integration/test_handoff_trace.py` — explicit model injection,
  canonical checkpoint lifecycle, model artifacts, and trace assertions.
- `tests/integration/test_multi_agent_workflow.py` — explicit model-backed
  direct Coordinator compatibility coverage and updated model-driven counts.
- `tests/integration/test_memory_runtime.py` — offline model injection while
  retaining memory-reference redaction checks.
- `docs/requirements/m0_3_retrieval_agent_model_driven/task.md` — this Worker
  evidence record.

### GREEN Results

- `python -m pytest -q --tb=short
  tests/integration/test_m03_model_driven_retrieval.py`: **9 passed, 2
  warnings**.
- `python -m pytest -q --tb=short
  tests/integration/test_retrieval_tool_runtime.py`: **8 passed, 2
  warnings** before the acceptance-hardening assertions; the current
  post-hardening run is **10 passed, 2 warnings**.
- `python -m pytest -q --tb=short tests/integration/test_handoff_trace.py
  tests/integration/test_multi_agent_workflow.py
  tests/integration/test_memory_runtime.py`: **12 passed, 2 warnings**.
- Combined M0.3/adapter focused run before hardening: **17 passed, 2
  warnings**; current M0.3/adapter run after hardening: **19 passed, 2
  warnings**.

### Post-GREEN Acceptance Hardening

- Added the direct `RetrievalAgent` empty-search proof in the approved
  ToolRuntime integration test. It now asserts the exact M0.1 `AgentResult`:
  `status == completed`, `output_refs == []`, `error is None`, and
  `handoff == {"status": "not_required", "reason_code": "no_matching_notes"}`;
  it also asserts no read callback and a completed empty `search_notes` ledger
  record.
- Added a recording-executor proof on the same `RuntimeAgentAdapter` /
  `RetrievalAgent` production composition path. The test compares the FakeModel
  proposed business arguments, the arguments received by the real tool callback,
  and the canonical ledger arguments. They are equal; only M0.2 identity
  binding fields are outside this business-argument comparison.
- These assertions ran directly against the existing implementation and passed;
  this is post-GREEN acceptance hardening, not a new RED phase. No production
  file was changed for this hardening.

### Regression Results

- `python -m pytest -q --tb=short
  tests/integration/test_p85_durable_provider.py
  tests/unit/test_p84_durable_model_loop.py tests/unit/test_p84_artifacts.py`:
  **90 passed, 2 warnings**; regression files were not modified by this
  Worker.
- `python -m pytest -q --tb=short tests/unit/test_p83_fake_model_loop.py
  tests/unit/test_p83_durable_tool_boundary.py tests/unit/test_tool_runtime.py
  tests/unit/test_tool_ledger_runtime.py`: **32 passed, 2 warnings**.
- Targeted `compileall` for all changed production/test files: **passed**.
- `git diff --check` for changed tracked files: **passed**.
- Static source check for `ToolCall(` and direct ToolRuntime execution in
  `retrieval_agent.py`: **no matches**.
- Because this hardening changed tests only, the already completed M0.2 and
  ToolRuntime regression evidence remains applicable: **90 passed** and
  **32 passed**, respectively; their regression files remained untouched.

### Trajectory Evidence

- Policy A on the production Engine/Adapter/Coordinator path records
  `search_notes -> Final`, with two model executions and no read ledger row.
- Policy B on the same path records `search_notes ->
  read_verified_note -> Final`; the second search result is selected by the
  FakeModel and the read arguments remain unchanged through ToolRuntime and
  the ledger.
- The next model requests receive the exact successful Search/Read ToolResult
  envelopes. The final model execution is durable before AgentResult
  projection.
- Empty search remains completed with `evidence=[]`, no fallback/errors, and
  the exact `no_matching_notes` handoff. Search/read executor failures are
  explicit and reach the next model request. Runtime refusal produces no
  fabricated authorized ledger row.

### Skipped Or Environment-Limited Checks

- `real_provider_smoke_test = NOT_RUN` by scope: no network, credentials, or
  real provider were used.
- Full `python -m compileall -q src/linkloom tests` reported the pre-existing
  inaccessible path `tests\\.pytest-trajectory-2b-data`; targeted compile of
  every changed file passed.
- Every pytest invocation emitted existing Windows `WinError 5` cache/temp
  permission warnings. Approved test files use unique `.tmp` synthetic roots;
  no application behavior was changed for the environment issue.
- Worker-created `.tmp/m03-*` synthetic run artifacts remain in place; no
  existing user directory was deleted or cleaned.
- Independent Reviewer acceptance is pending; Worker does not mark PASS or
  ACCEPTED.

### Remaining Risks

- M0.3 proves the production durable facts and model-owned trajectory, but
  full production crash/resume acceptance remains M0.4.
- Real provider adapter/network smoke and provider-specific behavior remain
  outside this offline boundary.
- Existing unrelated dirty and untracked user paths were preserved. No
  commit, push, PR, or real-vault operation was performed.

### Safety Confirmation

- No unapproved production file was changed. `model_loop.py`, `models.py`,
  Coordinator, provider, ToolRuntime, ledger, policy, CLI, and regression
  files remained inspection-only.
- No real Obsidian vault, network, credentials, or provider was accessed.
- The Worker did not modify the M0.2 durability contract or start M0.4/M1.

### Worker Learning Points

1. Model-driven means the model owns the next action and its business
   arguments; Python only supplies the bounded instruction and available tool
   definitions.
2. ToolRuntime remains the execution authority while RuntimeState and the
   M0.2 loop make each model/tool/observation boundary durable and inspectable.

### Interview Evidence

- Runnable ownership proof: the same production path produces
  `search_notes -> Final` and `search_notes -> read_verified_note -> Final`
  under two FakeModel policies.
- Exact argument/observation proof: model request, ToolRuntime ledger, and
  durable observation artifacts agree on the model-selected business payload.
- Safety proof: unknown-tool refusal returns to the model without an
  authorized ledger row; empty search remains a completed no-match result;
  executor failures remain explicit and redacted.

### Candidate Next Task

`M0.4 Production E2E + resume acceptance` — independently review this M0.3
implementation first, then plan crash/resume acceptance for the already
durable production trajectory. Do not start it in this Worker handoff.

## Planner Learning Reflection

### What Changed

Created the M0.3 SPEC, implementation plan, acceptance matrix, RED proof, file
boundary, compatibility strategy, stop rules, and Worker checklist. No
production or test code changed.

### What I Learned

1. The actual deterministic ownership is localized in RetrievalAgent; the
   accepted lower-level loop and ToolRuntime already provide the required
   execution authorities.
2. The highest-value proof is substitutability: the same production Retriever
   must change its tool path when only FakeModel policy changes.

### Evidence

- [SPEC.md](SPEC.md)
- [implementation_plan.md](implementation_plan.md)
- current source and focused test inspection recorded in the Planner recovery
  section
- human instruction declaring M0.1/M0.2 accepted and M0.3 active

### Next Step

A separate Worker begins with the RED checklist. Any need to modify M0.2
internals or another production file is a blocker, not permission to expand.

## Blocking Projection-Precedence Correction — Worker Evidence (2026-08-31)

### Scope And Root Cause

- Role: Worker correcting the sole blocking finding from the independent M0.3
  review; this record does not accept M0.3.
- Root cause: `_project_result()` returned the successful-empty-search special
  case before consulting accumulated failed search/read ledger outcomes. A
  later completed `search_notes` result with `value=[]` could therefore erase
  an earlier authoritative ToolRuntime failure from the final AgentResult.
- Correction: the empty-search completion branch now requires
  `not failed_errors`. It otherwise remains unchanged. Successful read evidence
  still retains the accepted partial-success behavior, including warnings from
  an earlier failed read.

### Exact Files Changed For This Correction

- `src/linkloom/agents/retrieval_agent.py` — add the historical-failure guard to
  the empty-search projection branch only.
- `tests/integration/test_m03_model_driven_retrieval.py` — add the two approved
  mixed-trajectory regressions.
- `docs/requirements/m0_3_retrieval_agent_model_driven/task.md` — record this
  Worker evidence.

No other production file was changed for this correction.

### RED Evidence

- Pre-change M0.3 baseline: **9 passed**.
- Corrected mixed-trajectory tests before the production edit: **2 failed, 9
  deselected**.
- Both failures reached the intended semantic assertion: the durable ledger
  contained the failed retrieval outcome followed by a completed empty search,
  while result projection incorrectly returned `completed` instead of
  `failed`.

### GREEN Semantic Evidence

| Trajectory/check | Result |
|---|---|
| search failure -> empty search -> Final | failed search remains authoritative; **passed** |
| successful search -> read failure -> empty search -> Final | failed read remains authoritative; **passed** |
| isolated empty search -> Final | completed, empty refs, no error, `no_matching_notes`; **passed** |
| direct search failure -> Final | explicit ToolRuntime failure and fallback; **passed** |
| direct read failure -> Final | explicit ToolRuntime failure and fallback; **passed** |
| search -> Final | production synthetic policy path; **passed** |
| search -> read -> Final | production synthetic policy path; **passed** |

### Validation Results

- New mixed regressions: **2 passed, 9 deselected**.
- Isolated-empty plus direct search/read semantic checks: **5 passed**.
- Full focused M0.3 suite: **11 passed**.
- Retrieval ToolRuntime integration suite: **10 passed**.
- Handoff, multi-agent, and memory compatibility regressions: **12 passed**.
- Frozen M0.2 durability regression: **90 passed**.
- P8 model/tool/runtime boundary regression: **32 passed**.
- Synthetic production two-policy smoke: **1 passed**.
- Changed-file `compileall`: **passed**.
- `git diff --check`: **passed**; only existing LF/CRLF notices were emitted.
- Full repository suite: **NOT_RUN** as permitted by the Reviewer instruction;
  unrelated protected Windows ACL paths remain present.

### Acceptance-Criterion Readiness

- Criterion 12 is ready for focused independent re-review: direct and mixed
  read failures remain explicit, the new empty-search precedence regression is
  covered, and the existing successful-read partial-result branch was not
  changed.
- Criterion 13 remains ready for focused independent re-review: ToolRuntime and
  policy code were untouched, and the focused M0.3, Retrieval ToolRuntime, and
  P8 boundary regressions remain green.
- M0.3 is handed back for independent focused re-review. This Worker does not
  mark it `PASS` or `ACCEPTED` and does not start M0.4.

### Safety Confirmation

- No real Vault, provider, credential, network, or Git remote operation was
  used.
- No commit, push, PR, branch rewrite, reset, stash, or clean was performed.
- Model trajectory ownership, M0.2 durability, ToolRuntime, Coordinator,
  Provider, Recovery, Memory, and Evaluation implementations were not changed.
