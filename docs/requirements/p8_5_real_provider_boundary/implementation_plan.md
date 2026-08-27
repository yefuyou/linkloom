# Implementation Plan: P8.5 Real Provider Boundary

## Role And Gate

This is the Worker plan derived from the P8.5 SPEC. Planner has not accepted
the implementation, and Reviewer acceptance must be performed separately after
the Worker evidence is complete.

No production file may be modified until the human accepts this SPEC and plan
as the active P8.5 boundary.

## Existing Architecture Evidence

| Existing primitive | Reuse decision |
| --- | --- |
| `ModelTurnRequest` | Extend additively with optional model/options and optional `previous_tool_call`; preserve existing fake callers. The real provider may resolve a configured model ID only at its explicit composition boundary. |
| `ModelAction` | Keep as the one-action proposal shape. |
| `ModelAdapter` | Preserve legacy fake `decide()` compatibility; add a normalized response seam. |
| `SingleAgentModelLoop` | Continue owning lifecycle, checkpoint, max steps, and termination. |
| `ModelExecutionRecord` | Extend with safe provider IDs, finish reason, and normalized error only. |
| `ModelArtifactStore` | Reuse for normalized response artifacts and existing hash/forbidden-key checks. |
| `ToolDefinition` | Reuse as the only source for provider tool schema mapping. |
| `ToolRuntime` | Remains the only tool validation, permission, budget, executor boundary. |
| `ToolExecutionLedger` | Remains the recovery source of truth for terminal tool outcomes. |
| `ToolPolicyEnforcer` | Remains the only permission/max tool-call authority. |
| `AgyCliAdapter` | Do not reuse it. It is an evaluation-only adapter with a different payload and lifecycle; it is not the P8.5 provider boundary. |

## File Boundary

### Planning files created now

- `docs/requirements/p8_5_real_provider_boundary/SPEC.md`
- `docs/requirements/p8_5_real_provider_boundary/implementation_plan.md`
- `docs/requirements/p8_5_real_provider_boundary/task.md`

### Expected Worker production files

- `src/linkloom/agents/model_adapter.py`
  - add `ModelGenerationOptions`, `ModelResponse`, `ModelUsage`, and
    `ModelProviderError` neutral values;
  - add optional `previous_tool_call` to `ModelTurnRequest` so a provider can
    pair a durable ToolResult with the function call that caused it;
  - preserve `FakeModelAdapter` and legacy `ModelAction` behavior;
  - add the compatibility normalization seam.
- `src/linkloom/agents/providers/__init__.py`
  - export only the provider-neutral adapter contract and the Gemini API
    adapter; do not export SDK types or client construction internals.
- `src/linkloom/agents/providers/gemini_api.py`
  - implement direct Gemini API request mapping through the official
    `google-genai` client, complete ToolDefinition mapping, structured
    function-call/final parser, usage/ID extraction, error normalization, and
    capability declaration;
  - use an injected client protocol in offline tests;
  - keep SDK import and client construction behind a lazy optional provider
    boundary; importing the base runtime must not require `google-genai`.
- `pyproject.toml`
  - add an optional `gemini` extra for the official `google-genai` SDK only if
    the Worker dependency audit confirms the selected SDK version and Python
    compatibility; keep the base installation provider-free.
- `src/linkloom/runtime/model_loop.py`
  - normalize legacy `ModelAction` or new `ModelResponse`;
  - persist normalized response metadata/errors through the current P8.4
    lifecycle;
  - keep provider invocation after `request_sent` and before
    `response_obtained` only.
- `src/linkloom/runtime/models.py`
  - add only backward-compatible optional durable provider fields and safe
    model-error validation.
- `src/linkloom/runtime/artifacts.py`
  - modify only if the current forbidden-key or bounded-payload contract is
    insufficient for normalized provider metadata.

### Expected Worker test files

- `tests/unit/test_p85_model_provider_contracts.py`
- `tests/unit/test_p85_gemini_api_adapter.py`
- `tests/unit/test_p85_schema_mapping.py`
- `tests/integration/test_p85_durable_provider.py`
- minimal additions to `tests/unit/test_p84_durable_model_loop.py` only if a
  compatibility assertion must be made explicit.

Do not modify RetrievalAgent, Coordinator, RuntimeAgentAdapter, Memory,
Evaluation, CLI, writeback, or legacy relation_eval tests.

## Ordered Work Packages

### WP-0: Contract RED tests and baseline

Before production changes:

1. run the current P8.4 focused and P8.1–P8.4 regression slices;
2. add failing tests for `ModelResponse`, safe provider errors, and legacy fake
   compatibility;
3. add failing tests for request mapping, schema mapping, and response parsing;
4. record the baseline without changing legacy fixtures.

Acceptance: tests fail only because the new contract is absent; current P8.4
tests remain green before the new tests are added.

### WP-1: Provider-neutral response contract

Implement the smallest neutral values:

```text
ModelGenerationOptions
ModelUsage
ModelProviderError
ModelResponse
ProviderCapability
```

Invariants:

- JSON-safe values only;
- no SDK object fields;
- one `ModelAction` maximum;
- error and action are mutually exclusive;
- usage numbers reject bool/NaN/infinity and negative counts;
- provider metadata is bounded and forbidden-key checked;
- provider IDs and finish reasons are safe text or null;
- error messages never contain raw exception text or command output.

Compatibility behavior:

- existing `FakeModelAdapter.decide()` may keep returning `ModelAction`;
- runtime normalization wraps it with fake metadata;
- existing P8.3/P8.4 tests do not need a provider client.

Checkpoint: contract tests and compileall.

### WP-2: Gemini API client seam and request mapping

Implement an injected API-client seam:

```text
GeminiProviderAdapter.complete(ModelTurnRequest)
  -> GeminiRequestMapping
  -> injected GeminiClient protocol
  -> SDK response or provider exception
  -> ModelResponse
```

Rules:

- use the official `google-genai` client or another directly supported Gemini
  API client behind this adapter; do not invoke Gemini CLI, a shell, or a
  subprocess;
- resolve the model identifier from the neutral request or explicit adapter
  configuration; do not bake a CLI-era model name into reusable logic;
- accept an injected fake client in tests so no network, SDK authentication,
  or credential discovery occurs offline;
- map only bounded neutral request fields and native Gemini function
  declarations; never pass RuntimeState, checkpoints, the ledger, or raw
  artifacts to the provider;
- use manual function-calling mode. The provider may propose a function call,
  but LinkLoom executes it through ToolRuntime;
- use the selected SDK call surface `generate_content(model=..., contents=...,
  config=...)`; LinkLoom artifacts/checkpoints remain the source of truth;
- configure the SDK client for a single provider attempt where the selected
  SDK version supports retry control. The adapter must not add a retry loop;
  if the SDK cannot disable automatic retries, the Worker must stop and
  resolve that boundary before implementation rather than silently giving
  retry ownership to the SDK;
- map client timeout, authentication, rate-limit, invalid-request, malformed
  response, and unknown-outcome failures to safe provider-neutral errors;
- never persist SDK objects, raw request/response text, hidden reasoning, or
  exception tracebacks;
- no live client runs in unit tests.

Checkpoint: fake-client tests for request mapping, native function schema,
manual tool-call mode, timeout/error mapping, response IDs,
usage normalization, and secret non-persistence.

### WP-3: Tool schema mapping and response parsing

Implement pure functions or small methods for:

1. `ToolDefinition -> provider tool envelope`;
2. provider JSON root -> one ToolCall or final `ModelAction`;
3. provider usage/IDs/finish reason -> neutral response metadata.

Tool schema rules:

- preserve `type`, `properties`, `required`, `additionalProperties`, bounds,
  array `items`, descriptions, and version;
- reject unsupported safety-critical keywords rather than dropping them;
- validate provider-returned arguments as JSON object before constructing
  `ToolCall`;
- validate provider-returned tool IDs against the current request's declared
  tools and fail closed during adapter normalization when undeclared; keep
  ToolRuntime's later execution-time validation unchanged;
- reject multiple calls, empty responses, invalid finish reasons, malformed
  JSON, and ambiguous response shapes.

Checkpoint: all offline mapping/parser tests, including the 13 required fault
families in the SPEC.

### WP-4: Durable model-loop integration

Modify `SingleAgentModelLoop` minimally:

1. build/persist request artifact and `request_durable` as today;
2. set `request_sent` before invoking any adapter;
3. build the next `ModelTurnRequest` with the existing normalized previous
   ToolCall when the observation came from a tool; do not reconstruct a
   provider conversation from the full state;
4. invoke the neutral adapter seam;
5. on success, set `response_obtained`, write normalized response artifact,
   then set `response_durable`;
6. on provider error, persist a safe failed model record with normalized error
   and do not create a ToolCall or observation;
7. on action, keep current ToolRuntime/ledger path unchanged;
8. on final, keep `TerminationState` as runtime authority;
9. on resume, reuse durable response and never call provider again;
10. on ambiguous request state, return verification/manual decision without
   provider invocation.

Do not add adapter retries, side-effect replay, or provider-specific branches
to ToolRuntime.

Checkpoint: durable response reuse, provider error persistence, crash-window
compatibility, no duplicate provider invocation, and all P8.4 tests.

### WP-5: Security/privacy and capability matrix

Add a capability declaration for the Gemini API adapter:

| Capability | P8.5 status |
| --- | --- |
| One structured ToolCall | supported through Gemini native function calling |
| Final answer | supported |
| Multiple ToolCall | unsupported by the LinkLoom P8.5 adapter; fail closed |
| Usage token counts | optional; null when the API response does not expose them |
| Duration | supported locally |
| Provider request/response IDs | extract interaction/response/step IDs if present; otherwise null |
| Timeout handling | supported as normalized error; no blind replay |
| Rate-limit handling | supported as normalized error; no adapter retry |
| Cancellation | supported only as a normalized cancellation if exposed by the client |
| Streaming | unsupported |
| Provider-side automatic tool execution | disabled/not used |
| Native function-calling | supported; LinkLoom still owns execution |
| JSON schema constraints | supported subset only; fail closed on safety loss |
| Reasoning/thought metadata | unsupported and never persisted |

Run a source-level search and tests proving no structured forbidden
provider/credential field enters RuntimeState, ModelExecutionRecord, artifact
payload, trace event, or error message. Ordinary application text must remain
available for exact recovery; this boundary does not perform arbitrary string
redaction.

Checkpoint: security tests and capability report.

### WP-6: Optional synthetic real smoke

Only if explicitly opt-in and a valid Gemini API credential is already
available to the official SDK/client:

- use a synthetic `search_notes` ToolDefinition;
- send one minimal direct API request through `GeminiProviderAdapter`;
- assert a structured ToolCall or safe normalized provider error;
- never read a real Vault;
- do not print or persist credentials, SDK configuration, or raw provider
  responses.

The Worker must not search for unknown credential files or ask the user to
paste a secret. If no legal credential is already available, record exactly
`real_provider_smoke_test = NOT_RUN` and continue; this is not a failure of the
offline implementation.

### WP-7: Independent Reviewer pass

Reviewer checks:

- provider SDK details do not leak into runtime contracts;
- request mapping does not include full RuntimeState or ledger;
- tool schema constraints are not silently dropped;
- multiple calls fail closed;
- provider errors are safe and retry ownership is clear;
- `response_durable` and `tool_result_durable` resume semantics remain intact;
- no structured provider/credential secret field appears in code, artifact,
  trace, test output, or diff;
- RetrievalAgent and legacy relation_eval remain untouched.

Worker must not mark this SPEC accepted. Reviewer reports PASS, PASS_WITH_FINDINGS,
FAIL, or BLOCKED with evidence.

## Test Matrix

### Contract tests

- request identity/model/options round-trip;
- optional previous ToolCall plus ToolResult identity round-trip;
- response ToolCall and final variants;
- response action/error mutual exclusion;
- usage and metadata JSON-safety;
- provider error taxonomy and retryable/outcome flags;
- legacy FakeModelAdapter compatibility.

### Mapping/parser tests

- all supported JSON schema types;
- required/additionalProperties/bounds/descriptions/version;
- unsupported schema keyword fail-closed;
- one ToolCall;
- final answer;
- malformed arguments;
- unknown tool;
- multiple ToolCall;
- empty response;
- invalid finish reason;
- malformed JSON/structured response;
- usage and IDs;
- secret redaction.

### Error tests

- authentication/missing credentials;
- invalid request;
- malformed provider response;
- rate limit;
- timeout/unknown outcome;
- transient failure;
- unavailable model;
- ToolCall parse failure;
- unsupported shape;
- cancellation;
- no SDK traceback leakage.

### Durable integration tests

- request is checkpointed before provider invocation;
- response is durable before ToolRuntime;
- durable final response is reused without provider call;
- durable ToolCall is reused without second provider call;
- tool_result_durable resumes with existing ToolResult;
- request_sent unknown outcome blocks blind reinvocation;
- provider error becomes safe failed record;
- existing FakeModel and P8.1–P8.4 behavior remains unchanged.

## Verification Commands

Worker must run, using a workspace-local temporary directory if Windows ACL
requires it:

```powershell
Set-Location D:\webproject\Linkloom
python -m pip install -e ".[dev]"
python -m pytest -q <P8.5 focused tests>
python -m pytest -q <P8.1-P8.5 regression slice>
python -m pytest -q tests
python -m compileall -q src/linkloom tests
git diff --check
git status --short
```

The known legacy `relation_eval` failures must be classified, not repaired in
P8.5.

## P8.6 Handoff

P8.5 completion must leave a readiness record, not implementation:

- retrieval prompt/system instruction contract is missing;
- evidence-grounded final answer schema is missing;
- deterministic fallback and provider-unavailable UX are missing;
- retrieval-specific termination/eval gate is missing;
- disclosure policy for cloud-sent note content is missing;
- production RetrievalAgent migration remains unstarted.

Candidate next task: write and approve a separate P8.6 RetrievalAgent
model-driven migration SPEC after P8.5 Reviewer acceptance.
