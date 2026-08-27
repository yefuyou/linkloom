# P8.5 Task Record

Status: Worker WP-0/WP-1/WP-2 implementation, WP-2 correction patch, WP-3
implementation, and the minimal WP-3 P1-B correction completed; independent
Reviewer re-review is required before any WP-4 or P8.6 work.

## Current Facts

- P8.1–P8.4 are implemented in the current workspace.
- P8.4 Reviewer result is `PASS_WITH_FINDINGS`.
- `FakeModelAdapter` and `SingleAgentModelLoop` are provider-neutral only at
  the one-action level; they do not yet carry normalized provider metadata or
  provider errors. `ModelTurnRequest` also needs a backward-compatible
  optional `previous_tool_call` for a real provider function-response turn.
- `ModelArtifactStore`, `ModelExecutionRecord`, `ToolExecutionLedger`,
  `ToolRuntime`, and `ToolPolicyEnforcer` already exist and must be reused.
- The repository has an evaluation-only `AgyCliAdapter` for a Gemini CLI
  workflow; it is not a model-loop adapter and is not the P8.5 provider
  boundary.
- `pyproject.toml` has no provider SDK dependency.
- No Gemini API key was read or required for planning.
- No real Vault was accessed or modified.

## Completed Planner Work

- Created the P8.5 provider-neutral boundary SPEC.
- Rejected Gemini CLI/subprocess as the first real provider boundary because
  it would introduce a second Agent/tool/permission runtime and blur the
  LinkLoom provider/runtime ownership boundary.
- Selected a direct Gemini API adapter through the official Google Gen AI
  Python SDK (`google-genai`), behind a provider-neutral client seam.
- Kept Gemini native function declarations and manual function-calling mode;
  LinkLoom remains the only executor of local tools.
- Defined one-ToolCall fail-closed behavior for multiple calls.
- Defined request/response/usage/error normalization.
- Defined P8.4 durable lifecycle integration and no-blind-replay rules.
- Defined offline fake-client and recorded-response test coverage.
- Defined the credential boundary: structured provider/credential secret
  fields are rejected from neutral requests, durable state, artifacts, traces,
  fixtures, and error messages; ordinary application text is not arbitrarily
  redacted, and no unknown credential discovery is allowed.
- Defined the optional real API smoke-test gate and `NOT_RUN` outcome when no
  legal credential is already available.
- Defined secret/privacy constraints and optional smoke-test behavior.
- Defined P8.6 RetrievalAgent readiness blockers without implementing P8.6.

## Evidence

Planning files:

- `docs/requirements/p8_5_real_provider_boundary/SPEC.md`
- `docs/requirements/p8_5_real_provider_boundary/implementation_plan.md`
- `docs/requirements/p8_5_real_provider_boundary/task.md`

No production code was changed in this Planner slice.

## Worker Acceptance Boundary

The Worker may implement only the approved P8.5 files and behavior listed in
the SPEC and implementation plan. The Worker must not:

- migrate RetrievalAgent;
- add a second ToolRuntime, ledger, registry, permission, or retry system;
- modify Memory, Evaluation, LangGraph, MCP, or legacy relation_eval;
- read real Vault content;
- persist structured provider/credential secret fields, raw SDK
  request/response objects, hidden reasoning, or full runtime state;
- claim native multi-tool support or exactly-once execution;
- commit, push, create a PR, or perform GitHub writes.

## Required Learning Result

The user should be able to explain:

1. why `ModelResponse` is separate from `ModelAction`;
2. why the provider adapter returns normalized errors while the runtime owns
   retry, checkpoint, and termination decisions.

## Required Interview Evidence

- provider-neutral request/response/error contract;
- Gemini API request, native function-schema, and response mapping;
- complete offline fake-client parser and error test matrix;
- durable response reuse without duplicate provider invocation;
- safe secret/privacy boundary;
- explicit capability matrix and unsupported features;
- P8.6 migration-readiness blocker list.

## Candidate Next Task

After independent Reviewer acceptance, implement WP-2 only: the injected
direct Gemini API client seam and request mapping, with fake-client offline
tests. Do not start RetrievalAgent migration or real API smoke automatically.

## Worker WP-0/WP-1 Completion

### Implemented

- Added `ModelGenerationOptions` with JSON-safe bounded generation options.
- Added `ModelUsage` with nullable token counts and strict numeric validation.
- Added `ModelProviderError` with normalized code/category taxonomy, safe
  message, retryability hint, outcome classification, provider request ID,
  bounded metadata, and safe details.
- Added `ModelResponse` as an envelope around exactly one `ModelAction` or one
  normalized provider error; action and error cannot coexist or both be absent.
- Added `ProviderCapability` with provider-side tool execution hard-disabled.
- Added a separate `ModelProviderAdapter.complete()` protocol returning
  `ModelResponse`; the legacy `ModelAdapter.decide()` protocol remains
  unchanged for FakeModel and P8.3/P8.4.
- Added backward-compatible `previous_tool_call`, `model_id`, and generation
  options fields to `ModelTurnRequest`, including JSON round-trip support and
  observation identity validation.
- Preserved `FakeModelAdapter`, `ModelAction`, and the existing
  `SingleAgentModelLoop` execution path.
- Extended the existing persisted-key guard with raw request/response,
  traceback, authorization, credential, and token-value variants.

### RED evidence

Before production implementation, the new provider-contract test module failed
during collection with exit code 1 because `ModelGenerationOptions` did not yet
exist in `model_adapter.py`. No implementation was added before this check.

### Verification evidence

- P8.5 focused contract tests: `24 passed`.
- P8.2–P8.5 regression slice without protected `tmp_path` integration cases:
  `71 passed`.
- P8.1 tool contract/registry/runtime/ledger tests: `37 passed`.
- `python -m compileall -q src/linkloom tests`: exit 0; pytest scratch
  directory enumeration emitted the existing protected-path warning.
- `git diff --check`: exit 0.
- Full `python -m pytest -q tests --tb=short`: `339 passed, 35 failed, 147
  errors`; the errors are dominated by the machine's protected Temp ACL and
  the failures include the pre-existing relation-evaluation Gold/data-path
  issues. These unrelated fixtures were not modified.
- No Gemini network call, SDK import, API key, CLI, shell, subprocess, real
  Vault access, commit, push, or PR was performed.

### Learning result

1. `ModelAction` is the runtime proposal; `ModelResponse` is the provider
   envelope carrying usage, IDs, finish reason, metadata, and a normalized
   error around that proposal.
2. Provider errors are safe data returned to the runtime; the `retryable`
   value is a normalized hint, while retry policy and replay decisions remain
   runtime-owned.

### Interview evidence

- A RED-to-GREEN contract test run proving the new models were absent first and
  then validated after implementation.
- JSON round-trip evidence for tool calls, final answers, usage, provider IDs,
  metadata, errors, and `previous_tool_call`.
- Negative tests proving ambiguous responses, unsupported fields, hidden
  reasoning, raw traceback, and structured forbidden provider metadata fail
  closed; ordinary application text remains exact.

### Remaining issues

- P8.5 still needs the independent Reviewer pass; Worker must not self-accept
  this slice.
- The two retrieval integration tests could not initialize pytest's `tmp_path`
  because Windows denied access to the protected temp roots; this is an
  environment check gap, not a contract failure.
- Legacy `relation_eval` Gold/data-path failures remain unchanged.

## Worker WP-2 Completion

### Implemented

- Added `GeminiProviderAdapter` behind an injected `GeminiClient` protocol.
- Added bounded Gemini-shaped request mapping with manual function calling,
  official SDK-style model/contents/config arguments, model selection,
  generation options, and observation context.
- Added `ToolDefinition` to function-declaration mapping using
  `parameters_json_schema`; unsupported schema keywords fail closed.
- Added one final-text and one function-call response normalization path.
- Added SDK-like attribute response extraction without importing SDK types.
- Added usage, provider request/response ID, model version, finish-reason, and
  local duration normalization.
- Added provider error-response and exception normalization without automatic
  retry or raw exception persistence.
- Added explicit fail-closed handling for empty responses, malformed calls,
  invalid finish reasons, multiple calls, missing model IDs, and observation
  requests without `previous_tool_call`.
- Kept tool execution, permissions, budgets, checkpoints, recovery, and
  termination outside the adapter.

### RED evidence

Before adding the adapter package, the new WP-2 tests failed during collection
with `ModuleNotFoundError: No module named 'linkloom.agents.providers'`.

### Verification evidence

- New WP-2 adapter/schema tests: `24 passed`.
- P8.5 contract plus WP-2 tests: `48 passed`.
- P8.1–P8.4 focused regression slice: `84 passed`.
- Retrieval runtime integration: `4 passed, 2 errors`; both errors occurred
  during pytest `tmp_path` setup because of Windows Temp ACL denial.
- Full suite after WP-2: `363 passed, 35 failed, 147 errors`; the additional
  24 passes are the new WP-2 tests. Existing failures/errors remain in legacy
  evaluation, dataset, memory, and protected-temp paths.
- `python -m compileall -q src/linkloom tests`: exit 0 with the existing
  protected pytest scratch-directory warning.
- `git diff --check`: exit 0 with existing line-ending warnings only.
- No Gemini network call, SDK import, API key, CLI, shell, subprocess, real
  Vault access, commit, push, branch, or PR was performed.

### Learning result

1. The adapter maps provider-specific function declarations and response
   shapes into neutral LinkLoom contracts, while preserving ToolRuntime as the
   only executor.
2. A provider timeout or malformed response becomes a safe `ModelProviderError`;
   `retryable` is descriptive and the adapter performs no retry.

### Interview evidence

- RED-to-GREEN proof for the injected provider seam.
- Offline request and schema mapping examples.
- Final/function-call/SDK-like response parsing examples.
- Error normalization tests covering authentication, invalid request, rate
  limit, timeout, unavailable, malformed, and unsupported outcomes.
- Multiple-call rejection and no-provider-tool-execution evidence.

### WP-2 completion boundary

- WP-2 adapter-only implementation is complete for independent review.
- `real_provider_smoke_test = NOT_RUN` because no credential or network access
  was used.
- WP-3/WP-4 response and durable-loop integration must not begin automatically.

## Worker WP-2 Correction Patch

### Exact blocker fixes

- Changed the injected `GeminiClient` seam to keyword-only
  `generate_content(model=..., contents=..., config=...)`.
- Removed the unsupported root-level `store=False` field; no replacement
  privacy field was invented.
- Aligned function-call parsing with the official response behavior where a
  single structured function call may have `STOP` as its finish reason.
- Rejected provider-returned function names not present in the current
  `ModelTurnRequest.available_tools` allowlist.
- Normalized official `google-genai`-shaped exception status from `APIError.code`
  with the existing `status_code` compatibility fallback.

### Correction evidence

- Added RED regressions for the SDK seam, root-level store field, function call
  plus `STOP`, undeclared tools, and `APIError.code` mappings. Against the
  pre-fix implementation: `7 failed, 21 deselected`.
- Corrected WP-2 focused tests: `31 passed`.
- P8.5 contract plus WP-2 tests: `55 passed`.
- P8.1–P8.4 focused regression slice: `84 passed`.
- No network call, credential read, real Vault access, or provider retry was
  performed.

### Re-review boundary

- This is a Worker correction record only; it does not accept WP-2.
- `real_provider_smoke_test = NOT_RUN` remains unchanged.
- Independent Reviewer must re-check the corrected provider surface before
  any later P8.5 work package begins.

## Worker WP-3 Completion — Durable Integration Readiness

### Documentation alignment

- Removed the stale root-level `store=False` request statement from the
  accepted SDK request surface; the selected call remains
  `generate_content(model=..., contents=..., config=...)`.
- Documented fail-closed validation of provider-returned tool IDs against the
  current request's declared tools before ToolRuntime execution validation.

### P1-A exact request rehydration

- Root cause: after a `request_durable` checkpoint, the loop rebuilt
  `ModelTurnRequest` from the new caller's `user_input`, `available_tools`, and
  other mutable inputs instead of reading the durable request.
- The existing request artifact now carries the exact neutral
  `ModelTurnRequest.to_dict()` snapshot. Resume reads it through the existing
  `ModelArtifactStore` and recorded SHA-256, validates artifact/record/request
  identity, and fails closed when the artifact is missing, corrupt, or
  inconsistent. No caller fallback is used.

### P1-B durable tool-budget reconstruction

- Added `ToolPolicyEnforcer.rehydrate_from_ledger(records, ...)`. It
  interprets the existing ledger facts and restores the process-local count
  without mutating or executing the ledger.
- A `pending` record counts only when it is present in the durable ledger: it
  is the post-checkpoint budget commitment boundary. `completed` and `failed`
  records count once as terminal outcomes. Unknown, malformed, denied,
  pre-authorization failures, and pending records that never crossed a
  durable checkpoint do not count after restart.
- Rehydration filters by the current `run_id`, `task_id`, and policy-owned
  `agent_id` when the durable model loop supplies its scope. Same-run records
  belonging to another task or agent remain ledger facts but do not consume
  this policy's budget.
- A durable ledger above the configured max is rejected fail-closed; duplicate
  call IDs remain an error, the ledger remains the facts source, and the policy
  remains the budget authority.

### WP-3 evidence

- RED before production changes: the new focused file reported `5 failed, 3
  passed`; resumed input drifted to caller `input B`, tampered/missing request
  artifacts still allowed completion, and restored budget remained zero.
- Focused WP-3 tests after implementation: `8 passed`.
- Required P8.1–P8.5 unit regression slice: `147 passed`.
- `python -m compileall -q src/linkloom tests`: exit 0; the existing protected
  `tests/.pytest-trajectory-2b-data` directory could not be listed.
- `git diff --check`: exit 0; only existing line-ending/protected-directory
  warnings were emitted.
- Full `python -m pytest -q tests`: `378 passed, 35 failed, 147 errors`.
  The errors are dominated by the host's protected pytest Temp ACL; failures
  remain in the pre-existing legacy relation/evaluation and memory fixture
  paths. No unrelated fixture was changed.

### Boundary and follow-up

- No Gemini provider was connected to `SingleAgentModelLoop`; no provider
  retry, `ModelResponse.error` loop handling, `previous_tool_call` population,
  RetrievalAgent migration, Memory/Evaluation change, network call, real Vault
  access, or GitHub write was performed.
- WP-3 is ready for independent review only; it is not self-accepted.

## Worker WP-3 Correction — P1-B Durable Commitment and Scope

### Implemented

- Added non-consuming `ToolPolicyEnforcer.check_call_eligibility()` and
  post-pending `commit_call()` while preserving the legacy
  `authorize_call()` authorize-and-consume behavior for direct P4 tool
  wrappers.
- Changed `ToolRuntime` ordering to resolve and validate input, check policy
  eligibility, create pending, successfully checkpoint pending when a
  callback exists, commit exactly one policy call, and only then invoke the
  executor. Pending-checkpoint failure leaves the executor untouched and does
  not consume the in-process budget.
- Kept executor failures and invalid outputs as consumed calls after the
  commitment point; duplicate call IDs are rejected by the existing ledger and
  cannot consume a second unit.
- Updated the durable `SingleAgentModelLoop` rehydration call to pass its real
  `run_id`, `task_id`, and `agent_id` context. The policy's configured agent
  remains authoritative; a mismatched explicit agent scope fails closed.
- Corrected security wording to forbid structured provider/credential fields
  while preserving ordinary application text exactly; no arbitrary string
  redaction was introduced.

### Correction evidence

- RED before the production correction: the focused WP-3 file reported `6
  failed, 8 passed`; budget was consumed before pending checkpoint,
  commitment ordering was absent, duplicate calls consumed twice, and scoped
  rehydration parameters were unavailable.
- Focused WP-3 correction tests: `16 passed`.
- P8.1–P8.5/WP-3 regression slice: `171 passed, 4 errors`. The four errors
  occurred during unrelated integration `tmp_path` setup because the host
  denied access to its protected Windows Temp root; no application assertion
  failed in the slice.
- `python -m compileall -q src/linkloom tests`: exit 0; the existing protected
  `tests/.pytest-trajectory-2b-data` directory could not be listed.
- `git diff --check`: exit 0 with existing line-ending warnings only.
- No Memory, Evaluation, UI, Gemini/provider loop, real Vault/API/credential,
  or GitHub operation was performed.

### Correction boundary

- This remains a Worker correction record only. WP-3 is not accepted here;
  an independent Reviewer must re-check the commitment ordering, scope
  filtering, recovery behavior, and security wording.
