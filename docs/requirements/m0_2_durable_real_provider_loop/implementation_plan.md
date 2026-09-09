# Implementation Plan: M0.2 Durable Real-Provider Loop Completion

Status: **APPROVED FOR WORKER IMPLEMENTATION** within the frozen boundary in
this plan.

This plan implements only the boundary in [SPEC.md](SPEC.md). It reuses the
P8.5 WP-4 direction and current P8.4 lifecycle rather than introducing a new
runtime, recovery system, or provider abstraction.

## Governing Boundary

- Parent: `M0 — Production Agent Closeout`.
- Active slice: `M0.2 — Durable Real-Provider Loop Completion`.
- Primary Gate advanced: Gate A — provider boundary integration.
- Supporting Gate advanced: Gate B readiness only.
- M0.2 cannot complete Gate B; M0.3 owns production model-driven retrieval.
- Required implementation and acceptance are synthetic and offline.
- No network, credential discovery, real Vault, Memory, Eval, RAG, M0.3,
  commit, push, or PR.

## Architecture Decision

Use one private compatibility seam inside `SingleAgentModelLoop`:

```text
durable turn
  -> complete() available
       -> ModelResponse
  -> otherwise decide() available
       -> ModelAction
       -> wrap as legacy ModelResponse
```

Reasons:

1. The current provider protocol and legacy FakeModel protocol already exist.
2. Replacing `decide()` outright would break accepted P8.3/P8.4 evidence for
   no product benefit.
3. A new adapter hierarchy or bridge object would duplicate protocol
   responsibilities and expand the review surface.
4. Provider calls must remain durable-only. The non-durable P8.3 path continues
   to require `decide()`.
5. `complete()` requires an actual durable checkpoint callback. Request
   artifact persistence or an in-memory publish is not a substitute.
6. The required pre-call commitment order is request artifact written,
   `request_durable` persisted, `request_sent` persisted, and only then
   `complete()`.

The seam normalizes outputs only. It does not map provider payloads, execute
tools, retry, checkpoint, route Agents, or own termination.

## Dependency Graph

```text
existing ModelResponse contract
          |
          v
additive ModelExecutionRecord fields
          |
          v
full response artifact load/write invariants
          |
          v
durable complete()/decide() compatibility seam
          |
          +--------------------+
          |                    |
          v                    v
provider action/error      legacy FakeModel action
          |                    |
          +----------+---------+
                     v
          existing ToolRuntime / TerminationState
                     |
                     v
          ToolResult + previous_tool_call
                     |
                     v
              next provider request
```

The work is sequential because every slice changes the same durable response
contract and model loop. Parallel Workers would create unnecessary merge and
ownership risk.

## Exact Production File Boundary

### Expected modifications

#### `src/linkloom/runtime/models.py`

Add backward-compatible optional fields to `ModelExecutionRecord`:

- `provider_request_id`;
- `provider_response_id`;
- `finish_reason`;
- `provider_error`.

Requirements:

- JSON-safe values only;
- no forbidden persisted keys;
- safe optional text for IDs/reason;
- error must be an object or null and remain bounded/safe;
- old dictionaries without the fields deserialize unchanged;
- `to_dict()` / `from_dict()` round-trip exactly.

Do not add provider SDK types, new statuses, a second error hierarchy, or a
RuntimeState schema migration framework.

#### `src/linkloom/runtime/model_loop.py`

Implement:

- constructor validation for either `complete()` or legacy `decide()`;
- durable-only provider invocation;
- one private output-normalization seam;
- full ModelResponse response artifact write/load/verification;
- safe durable provider-error terminal handling;
- response metadata/usage/ID projections into ModelExecutionRecord and
  AgentTurn where already supported;
- previous ToolCall carry-forward with ToolResult;
- resume reconstruction of both observation and previous ToolCall;
- reuse of a durable action/final/error without provider replay.

Preserve:

- request artifact rehydration from P8.5 WP-3;
- current `request_durable -> request_sent -> response_obtained ->
  response_durable` statuses;
- current ToolRuntime/policy/ledger execution path;
- max-steps and TerminationState ownership;
- pending-tool verification behavior;
- safe generic handling when an adapter violates its protocol.

Do not move Gemini mapping or SDK knowledge into this file.

### Inspection-only production dependencies

- `src/linkloom/agents/model_adapter.py`;
- `src/linkloom/agents/providers/__init__.py`;
- `src/linkloom/agents/providers/gemini_api.py`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/runtime/recovery.py`;
- `src/linkloom/tools/contracts.py`;
- `src/linkloom/tools/runtime.py`;
- `src/linkloom/tools/ledger.py`;
- `src/linkloom/tools/tool_policy.py`.

Current evidence says these are sufficient without modification. If a RED test
proves a required change outside the two expected production files, the Worker
must stop, document the exact blocker, and request a SPEC adjustment.

## Exact Test File Boundary

### Add

`tests/integration/test_p85_durable_provider.py`

This is the previously planned P8.5 durable-provider integration file and is
adopted by M0.2 rather than creating a duplicate test family.

Required helpers remain test-local:

- a scripted provider-neutral adapter exposing only `complete()`;
- an injected fake Gemini client using the existing
  `GeminiProviderAdapter`;
- synthetic read-only ToolDefinition, ToolRuntime, policy, ledger, and
  workspace-local artifact root;
- call counters and checkpoint crash hooks.

### Minimally modify

`tests/unit/test_p84_artifacts.py`

Add only:

- new ModelExecutionRecord field round-trip;
- old-record backward compatibility;
- forbidden provider-error/metadata field rejection;
- optional provider IDs/reason validation.

### Regression-only tests

Do not modify these unless a separate SPEC adjustment is approved:

- `tests/unit/test_p83_fake_model_loop.py`;
- `tests/unit/test_p84_recovery.py`;
- `tests/unit/test_p84_durable_model_loop.py`;
- `tests/unit/test_p85_model_provider_contracts.py`;
- `tests/unit/test_p85_schema_mapping.py`;
- `tests/unit/test_p85_gemini_api_adapter.py`;
- `tests/unit/test_p85_wp3_durable_integration.py`;
- existing ToolRuntime, policy, registry, and ledger tests;
- M0.1 retrieval/multi-agent integration tests.

## Pre-Implementation Gate

Before editing, the Worker must:

1. read `AGENTS.md`, root `SPEC.md`, Product Roadmap, Master V2, M0.1 accepted
   record, M0.2 SPEC/plan/task, and P8.4/P8.5 records;
2. confirm this plan still records human approval of this exact M0.2 boundary;
3. confirm the P8.5 task records `WP-3 ACCEPTED — PASS_WITH_FINDINGS` and carry
   both non-blocking findings into the RED tests;
4. record branch, HEAD, `git status --short`, and current diffs for the two
   production and two test files;
5. preserve every unrelated dirty file and avoid reset/checkout/stash/clean;
6. confirm no real provider, credential, or Vault input is required;
7. stop if any expected file changed materially after this Planner snapshot.

## RED Strategy

Do not change production before the new behavioral tests fail for the expected
missing integration.

### RED-1: Provider seam absent

Construct a test adapter with only:

```python
def complete(request: ModelTurnRequest) -> ModelResponse: ...
```

Expected current failure: `SingleAgentModelLoop` rejects it because it lacks
`decide()`.

### RED-2: Full response not durable

Expect usage, IDs, finish reason, safe metadata, and a complete serialized
ModelResponse in response artifact/state.

Expected current failure: current loop writes only normalized action, empty
usage, and fake metadata.

### RED-3: previous_tool_call absent

Run provider ToolCall -> ToolRuntime -> next provider turn and assert the
second request contains the exact runtime-bound previous ToolCall matching the
ToolResult.

Expected current failure: current request constructors leave the field null.

### RED-4: provider error unsupported

Return a valid `ModelResponse(error=...)` and assert it becomes a durable safe
failed model outcome without ToolRuntime invocation.

Expected current failure: current loop accepts only ModelAction.

### RED-5: durable full-response reuse

Crash after `response_durable`, resume, and assert the provider response is
loaded as a complete envelope with no duplicate invocation.

Expected current failure: current loader understands only ModelAction
artifacts and cannot preserve provider envelope fields/error.

### RED-6: no durable checkpoint callback

Provide a valid artifact store and provider-only adapter but no durable
checkpoint callback. Assert explicit failure before `complete()`, Provider call
count zero, and no false durable `request_sent` representation.

### RED-7: failed pre-invocation checkpoint callback

Fail the callback at `request_durable` and separately at `request_sent`. Assert
the failure remains explicit and Provider call count stays zero in both cases.

### RED-8: full response requires canonical artifact recovery

Remove or tamper with the response artifact/ref/SHA/identity for a
Provider-generated full `ModelResponse`. Assert fail-closed recovery rather
than the legacy inline `normalized_action` fallback.

Record the exact failures in `task.md`. If any test is unexpectedly green,
record `RED_NOT_REPRODUCIBLE` for that criterion and do not manufacture a
failure.

## Ordered Work Packages

### W0 — Boundary Snapshot And RED

Files:

- `tests/integration/test_p85_durable_provider.py`;
- `tests/unit/test_p84_artifacts.py`.

Tasks:

1. record pre-edit Git and focused-test state;
2. add the eight RED groups above;
3. run only the new focused tests;
4. retain the exact failure output;
5. make no production edit until RED is recorded.

Acceptance:

- failures correspond to missing provider-loop integration, not fixture,
  import, ACL, or unrelated application failures;
- no real provider/network/credential/Vault is touched.

Checkpoint: RED evidence saved; Worker continues without human confirmation
only if no architecture blocker or file-scope expansion is found.

### W1 — Additive Durable Record Projection

Files:

- `src/linkloom/runtime/models.py`;
- `tests/unit/test_p84_artifacts.py`.

Tasks:

1. add optional provider ID/reason/error fields;
2. preserve existing defaults and serialization;
3. reject unsafe provider-error content and invalid field shapes;
4. keep old RuntimeState/ModelExecutionRecord payloads loadable.

Acceptance:

- artifact/model contract tests pass;
- no state status or schema-version redesign occurs.

Checkpoint: model record contract independently green.

### W2 — Provider Compatibility Seam And Response Durability

Files:

- `src/linkloom/runtime/model_loop.py`;
- `tests/integration/test_p85_durable_provider.py`.

Tasks:

1. validate either `complete()` or legacy `decide()`;
2. forbid provider-only adapter use in the non-durable path;
3. normalize a legacy action into a ModelResponse internally;
4. require an actual durable checkpoint callback before the provider seam;
5. write the request artifact, persist `request_durable`, persist
   `request_sent`, and only then invoke `complete()`;
6. fail explicitly with zero Provider calls when either callback is absent or
   fails;
7. checkpoint `response_obtained` after normalized response return;
8. write full response artifact and hash;
9. checkpoint `response_durable` before consuming action/error;
10. verify full envelope against artifact ref/hash/identity and legacy
    projections during load;
11. reserve inline normalized-action fallback for legacy compatibility only.

Acceptance:

- provider-only test adapter reaches the durable loop;
- FakeModel tests remain unchanged and green;
- no callback or failed pre-call callback produces zero Provider invocations;
- ToolRuntime/final/error does not run before response durability;
- tampered/mismatched/missing full response artifact fails closed without the
  legacy inline fallback.

Checkpoint: direct final and one tool proposal are durable.

### W3 — Tool Observation Correlation

Files:

- `src/linkloom/runtime/model_loop.py`;
- `tests/integration/test_p85_durable_provider.py`.

Tasks:

1. retain the runtime-bound ToolCall used for execution;
2. build the next ModelTurnRequest with that call plus ToolResult;
3. reconstruct the pair after `tool_result_durable` resume;
4. validate identity before provider invocation;
5. prove an injected Gemini client receives function-call/function-response
   content through the existing adapter.

Acceptance:

- exact call/tool identity matches in fresh and resumed paths;
- the provider cannot execute the tool;
- the existing policy count and ledger outcome prove LinkLoom ownership;
- a second provider turn returns final successfully.

Checkpoint: full offline provider -> tool -> provider -> final path green.

### W4 — Provider Error And Safe Terminal Handling

Files:

- `src/linkloom/runtime/model_loop.py`;
- `tests/integration/test_p85_durable_provider.py`.

Tasks:

1. persist normalized provider error before terminal handling;
2. map safe derived data into ModelLoopResult without changing its type;
3. distinguish known failure and unknown provider outcome in durable reason
   data;
4. assert no ToolRuntime/executor call occurs;
5. assert retryable is not acted on;
6. keep adapter exception handling safe and traceback-free.

Acceptance:

- known error, timeout/unknown outcome, and adapter contract violation are
  deterministic and safe;
- durable error response resumes without provider replay;
- no second error/retry hierarchy is added.

Checkpoint: provider-error matrix green.

### W5 — Crash Windows And Resume/Re-use

Files:

- `src/linkloom/runtime/model_loop.py` only if a focused test proves a narrow
  implementation defect;
- `tests/integration/test_p85_durable_provider.py`.

Required crash hooks:

1. durable checkpoint callback absent;
2. `request_durable` callback fails;
3. `request_sent` callback fails;
4. `request_sent` persisted;
5. `response_obtained`;
6. response artifact written but `response_durable` checkpoint fails;
7. `response_durable` final;
8. `response_durable` ToolCall before tool execution;
9. `tool_result_durable` before next request;
10. `response_durable` provider error;
11. full response artifact/ref/SHA missing or invalid during resume.

Acceptance:

- missing/failed pre-invocation checkpoint capability invokes zero times;
- safe request invokes exactly once only after `request_sent` persists;
- ambiguous states invoke zero times on resume;
- durable response invokes zero duplicate calls;
- full Provider response never recovers from inline action alone;
- a new turn after a reused ToolResult is the only legitimate next provider
  invocation;
- pending tool never auto-replays;
- terminal ToolResult never re-executes.

Checkpoint: crash matrix green without Recovery changes.

### W6 — Regression, Scope Audit, And Worker Handoff

Files:

- `docs/requirements/m0_2_durable_real_provider_loop/task.md` only.

Tasks:

1. run focused M0.2 tests;
2. run P8.1–P8.5 regression;
3. run M0.1 deterministic retrieval regression;
4. run compileall, diff check, status, and forbidden-scope searches;
5. run full suite once if the environment can do so at low cost;
6. classify every non-pass;
7. record `real_provider_smoke_test = NOT_RUN` unless a separate explicit gate
   was approved;
8. hand exact evidence to an independent Reviewer;
9. stop without M0.3 or Git writes.

Acceptance:

- all M0.2-caused checks are green;
- every environment or pre-existing non-pass is evidenced rather than hidden;
- task record states Gate A/Gate B limits.

## Required Focused Test Matrix

All M0.2 acceptance tests are offline and deterministic. The mandatory list is:

1. Provider ToolCall -> durable response;
2. ToolRuntime observation -> next request;
3. `previous_tool_call` population;
4. Provider final response;
5. Provider error normalization and persistence;
6. usage, metadata, request ID, and response ID persistence;
7. `response_durable` reuse without a duplicate Provider call;
8. `request_sent` ambiguous outcome fails closed;
9. `response_obtained` ambiguous outcome fails closed;
10. no durable checkpoint callback -> Provider call count zero;
11. failed checkpoint callback before Provider invocation -> Provider call
    count zero;
12. full `ModelResponse` recovery requires artifact plus SHA-256 and identity;
13. legacy FakeModel compatibility remains deterministic.

### Provider compatibility

- provider adapter with only `complete()` works in durable mode;
- provider-only adapter is rejected before invocation in non-durable mode;
- legacy FakeModel `decide()` still works in both existing P8.3/P8.4 paths;
- an object with neither method fails validation;
- if both methods exist, durable path calls only `complete()`.

### Full response lifecycle

- ToolCall response artifact/record round-trip;
- final response artifact/record round-trip;
- error response artifact/record round-trip;
- usage/metadata/IDs/finish reason retained;
- absent optional fields remain null;
- artifact projection mismatch and hash tamper fail closed;
- provider response never contains SDK objects/raw payloads.

### Tool correlation

- one ToolCall executes through existing ToolRuntime;
- policy/ledger count one authorized call;
- next request carries matching previous ToolCall and ToolResult;
- resumed terminal ToolResult carries the same pair;
- mismatched pair fails before provider invocation;
- provider/client never receives ToolRuntime, policy, ledger, or full state.

### Provider errors

- known authentication/invalid request style error;
- rate-limit error with retryable hint but zero retries;
- timeout/unknown outcome error;
- malformed adapter return;
- adapter exception safety;
- no ToolRuntime call and no raw exception/traceback/secret persistence.

The existing P8.5 adapter tests remain responsible for the full provider error
taxonomy; M0.2 tests only prove durable runtime integration of representative
categories.

### Crash/resume

- safe request reuse from exact artifact;
- request-sent ambiguity;
- response-obtained ambiguity;
- orphan response artifact ignored;
- durable final reuse;
- durable ToolCall reuse;
- durable error reuse;
- pending tool verification;
- terminal ToolResult reuse and next-turn correlation.

## Verification Commands

Run from the unique workspace:

```powershell
Set-Location D:\webproject\Linkloom
```

### RED and focused GREEN

```powershell
python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p85_durable_provider.py `
  tests/unit/test_p84_artifacts.py
```

### P8.1–P8.5 regression

```powershell
python -m pytest -q -p no:cacheprovider `
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
  tests/unit/test_p85_wp3_durable_integration.py `
  tests/integration/test_p85_durable_provider.py
```

### M0.1 deterministic production regression

```powershell
python -m pytest -q -p no:cacheprovider `
  tests/integration/test_retrieval_tool_runtime.py `
  tests/integration/test_handoff_trace.py::test_runtime_multi_agent_persists_state_and_jsonl_trace `
  tests/integration/test_multi_agent_workflow.py
```

### Static and scope checks

```powershell
python -m compileall -q src/linkloom tests
git diff --check
git status --short
git diff --name-only
rg -n "subprocess|Gemini CLI|api_key|authorization|raw_response|traceback" `
  src/linkloom/runtime/model_loop.py `
  src/linkloom/runtime/models.py `
  tests/integration/test_p85_durable_provider.py
```

Search matches must be interpreted; validation tests may intentionally mention
forbidden field names. Do not claim a security failure solely from a test
fixture string.

### Full suite

```powershell
python -m pytest -q tests --tb=short
```

If protected Windows Temp ACL paths prevent collection/setup, record the exact
environment failure and use a new, verified workspace-local artifact root only
for the focused controlled rerun. Do not delete or repair unrelated protected
directories in M0.2.

## Optional Real-Provider Smoke Boundary

No real smoke is part of W0–W6. If the human later approves a separate smoke:

1. use an already legal provider credential through its supported
   environment/client boundary;
2. use a synthetic prompt and read-only synthetic tool definition;
3. cap requests, output tokens, and cost explicitly;
4. disclose exactly what leaves the machine;
5. retain only normalized ModelResponse/artifacts;
6. never print or persist credentials or raw SDK payloads;
7. report pass/fail/blocked and exact usage.

Without that approval, record exactly:

```text
real_provider_smoke_test = NOT_RUN
```

## Full-Suite Interpretation

- `M0_2_CAUSED` and `UNKNOWN` failures block M0.2 acceptance.
- `PRE_EXISTING_UNRELATED` and `ENVIRONMENT_SETUP` non-passes require concrete
  file/error evidence and do not authorize unrelated repair.
- A green focused/P8/M0.1 boundary cannot be reported as full-suite green when
  unrelated failures or errors remain.
- The current protected Temp ACL and legacy relation/evaluation history must be
  classified, not modified in this task.

## Worker Handoff Evidence

The Worker must report:

1. pre-edit branch, HEAD, and status;
2. exact changed production/test/doc files and hunks;
3. RED output before implementation;
4. compatibility-seam decision and actual method called;
5. response artifact and ModelExecutionRecord examples;
6. previous ToolCall/ToolResult correlation evidence;
7. provider error/usage/metadata/ID evidence;
8. crash-window decision and provider/tool call counts;
9. focused, P8, M0.1, full-suite, compile, and diff results;
10. secret/privacy/synthetic-only audit;
11. `real_provider_smoke_test` status;
12. explicit Gate A/Gate B non-completion where applicable;
13. remaining risks and M0.3 deferral.

## Stop And Rollback Boundary

- Preserve all pre-existing dirty worktree changes.
- Use `apply_patch`-sized, Worker-owned hunks only.
- Do not reset, checkout, rebase, stash, clean, or broadly format.
- If a Worker hunk must be withdrawn, remove only that identified hunk after
  verifying surrounding user changes.
- Stop before modifying provider mapping, artifacts, recovery, RetrievalAgent,
  Coordinator, RuntimeAgentAdapter, Memory, Evaluation, fixtures, Gold,
  mutation, or UI.
- Stop if a generic retry, new hierarchy, new status machine, or schema
  migration framework appears necessary.
- Stop after W6. Do not enter optional smoke, M0.3, commit, push, or PR.

## Reviewer Boundary

The Worker may not accept M0.2. An independent Reviewer must verify:

- exact compliance with [SPEC.md](SPEC.md);
- no provider or ToolRuntime ownership leak;
- response durability before consumption;
- no durable checkpoint callback means no Provider invocation;
- `request_sent` persistence succeeds before every `complete()` call;
- full Provider response recovery requires artifact/ref/SHA/identity rather
  than the legacy inline-action fallback;
- previous-tool identity across fresh/resumed paths;
- provider-error safety and zero automatic retry;
- crash-window call counts;
- backward compatibility;
- no real Vault/network/credential/Git remote action;
- truthful Gate A/Gate B status.

The Reviewer reports `PASS`, `PASS_WITH_FINDINGS`, `FAIL`, or `BLOCKED` with
objective evidence. Human acceptance remains separate.
