# SPEC: P8.5 Real Provider Boundary

## Status

Planner draft created after the human replied `开始` on 2026-08-25.

This document defines the implementation boundary. It is not an independent
Reviewer acceptance and it does not authorize P8.6 RetrievalAgent migration.
Worker implementation must begin only after this SPEC, the implementation plan,
and the task record are explicitly accepted as the active boundary.

## Problem

P8.1–P8.4 already provide a provider-neutral fake model loop, durable model
records, artifact references, ledger rehydration, and crash-window decisions.
The current adapter seam is still too narrow for a real provider:

- `ModelTurnRequest` carries identity, input, one `ToolResult` observation, and
  `ToolDefinition` values, but has no explicit model identifier, bounded
  generation options, or the previous bounded `ToolCall` needed to map a
  `ToolResult` back to a Gemini function response step.
- `ModelAction` represents only one tool proposal or one final answer. It does
  not carry usage, provider IDs, finish reason, or normalized provider error.
- `ModelAdapter.decide()` returns only `ModelAction`, so a provider adapter
  cannot return safe usage and response metadata without leaking SDK types into
  the runtime.
- P8.4 durable state already has `usage` and `provider_metadata`, but it lacks
  an explicit durable model-provider error envelope and provider request/
  response identity fields.
- The repository contains an evaluation-only `AgyCliAdapter` for a Gemini CLI
  workflow. It maps relation-evaluation payloads to `PredictionRecord`; it is
  not a model-loop adapter and must not be reused as the P8.5 provider
  boundary.

## User Story

As a LinkLoom runtime, I can send one bounded model turn to one configured real
provider adapter, normalize its response into a safe `ModelResponse`, and pass
that response through the existing P8.4 durable lifecycle without allowing the
provider SDK, credentials, retry policy, or tool execution to leak into the
runtime.

## Chosen Provider

P8.5 implements one direct Gemini API adapter using the official Google Gen AI
Python SDK (`google-genai`) and the SDK's function-calling API. The provider
model identifier is configuration, not a hard-coded CLI model name. A real
environment may select an available Gemini 3 model such as the configured
Gemini 3.1 Pro model after an account/model-capability check.

Reasons:

1. LinkLoom already owns the Agent loop, ToolRuntime, permission, budget,
   checkpoint, recovery, and termination boundaries.
2. The Gemini API exposes native function declarations and structured function
   call steps; the provider returns a proposal and LinkLoom executes it.
3. The official SDK can be isolated behind an injected client protocol and a
   lazy/optional import boundary, so offline tests do not import, instantiate,
   or authenticate a live client.
4. The base LinkLoom dependency set remains provider-free; the SDK belongs in
   an optional Gemini provider extra.

The P8.5 adapter will not import evaluation `InferenceRequest`,
`PredictionRecord`, or `RealProviderRunner`. It will not invoke the Gemini CLI,
shell, subprocess, or provider-side automatic tool execution. It will set
provider-side state storage off where the API supports that option; LinkLoom's
P8.4 artifacts and checkpoints remain the only runtime source of truth.

The official Gemini API supports function calling and may return multiple
function calls. P8.5 deliberately exposes only one call to LinkLoom's current
single-call loop; additional calls fail closed rather than being silently
discarded.

Official reference basis for the Worker implementation:

- [Gemini API function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini API structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini API error codes](https://ai.google.dev/gemini-api/docs/api-errors)
- [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/)

## Objective

Create a provider-neutral model execution boundary and one offline-testable
Gemini API adapter that:

1. maps a bounded `ModelTurnRequest` to a Gemini API request;
2. maps `ToolDefinition` to a native Gemini function declaration;
3. parses exactly one structured ToolCall or one final answer;
4. normalizes usage, provider metadata, request/response IDs, finish reason,
   and provider errors;
5. preserves P8.4 request/response/observation durability and recovery rules;
6. rejects structured provider/credential secret fields, hidden reasoning, and
   raw SDK tracebacks; ordinary application text may be persisted exactly when
   it is required for deterministic recovery, while full runtime state and
   unnecessary Vault payloads remain out of the provider boundary;
7. provides deterministic fake-client and recorded-response tests without
   network access.

## Scope

### Provider-neutral request

The request contract must expose only these fields:

| Field | Required behavior |
| --- | --- |
| `run_id` | Runtime identity; never sent as a filesystem path. |
| `turn_id` | Runtime turn correlation. |
| `task_id` | Task identity. |
| `agent_id` | Agent identity. |
| `sequence` | Positive turn sequence. |
| `user_input` | Bounded user/task input. |
| `observation` | Optional normalized `ToolResult` projection. |
| `previous_tool_call` | Optional normalized `ToolCall` that produced `observation`; needed only to build a provider function-response turn. |
| `available_tools` | Only approved `ToolDefinition` values. |
| `model_id` | Optional for legacy fake callers; the real adapter must resolve it from this field or its explicit provider configuration and fail closed if neither exists. |
| `generation_options` | Small JSON-safe option set; defaults to empty for legacy callers and contains only options used by this provider. |

The first option set is limited to optional `max_output_tokens` and
`temperature` only when the selected provider can honor them. Provider-only
flags such as API keys, HTTP headers, endpoint URLs, retry configuration, or
SDK objects do not belong in the neutral request.

`previous_tool_call` is not a new message or conversation abstraction. It is a
bounded reference to the already durable normalized action for the preceding
turn. When a real provider request includes an observation, the runtime must
populate it and the provider mapping must require its identity to match the
observation's `call_id` and `tool_id`. The neutral request remains permissive
enough for existing fake callers that construct observation-only requests; the
real adapter fails closed if it cannot build a valid function-response turn.
The runtime derives it from the existing durable model action/
ToolExecutionLedger; the provider does not receive the full ledger or
checkpoint state.

### Provider-neutral response

Add a `ModelResponse` value that contains:

- exactly one `ModelAction` proposal, or a normalized provider error;
- normalized usage with optional input/output/total tokens and duration;
- safe provider metadata;
- optional provider request ID and response ID;
- optional finish/stop reason;
- no raw prompt, raw Vault payload, hidden reasoning, or SDK response object.

`ModelAction` remains the proposal submodel:

- `tool_call`: exactly one `ToolCall` proposal;
- `final`: exactly one non-empty final answer proposal.

### Provider error taxonomy

The adapter returns a provider-neutral `ModelProviderError` data value. It is
not a new SDK exception hierarchy. It must include a stable code, category,
safe message, `retryable`, safe details, and an outcome classification:

| Code family | Category | Default retryable | Outcome rule |
| --- | --- | ---: | --- |
| `MODEL_AUTH_REQUIRED` | authentication | no | Known failure; never auto-retry. |
| `MODEL_INVALID_REQUEST` | invalid_request | no | Known failure. |
| `MODEL_RESPONSE_MALFORMED` | malformed_response | no | Known failure; no ToolCall. |
| `MODEL_RATE_LIMITED` | rate_limit | yes | No adapter retry; runtime records failure. |
| `MODEL_TIMEOUT` | timeout | no for automatic replay | Provider outcome may be unknown. |
| `MODEL_TRANSIENT_FAILURE` | transient | yes | No adapter retry. |
| `MODEL_UNAVAILABLE` | unavailable_model | no | Known failure. |
| `MODEL_TOOL_CALL_PARSE_FAILED` | tool_call_parse | no | Known failure. |
| `MODEL_RESPONSE_UNSUPPORTED` | unsupported_shape | no | Known failure. |
| `MODEL_TOOL_SCHEMA_UNSUPPORTED` | tool_schema | no | Known failure before provider invocation. |
| `MODEL_CANCELLED` | cancelled | no | Known cancellation. |

The outcome classification must distinguish `known_failure` from
`unknown_provider_outcome`. A timeout or interrupted process must not be
silently treated as safe to reinvoke. The runtime may stop and require
verification; the adapter never owns retry orchestration.

### Provider adapter seam

The neutral seam is conceptually:

```text
ModelTurnRequest
  -> ProviderAdapter.complete(request)
  -> ModelResponse
```

The adapter owns only:

- provider request mapping;
- provider client invocation;
- response parsing;
- usage and error normalization;
- provider capability declaration.

The adapter does not own:

- ToolRuntime execution;
- ToolPolicyEnforcer permission or budget;
- `max_steps`;
- checkpoint callbacks;
- recovery decisions;
- retry loops;
- Memory, Evaluation, Handoff, or Agent routing.

The existing FakeModelAdapter remains usable by P8.3/P8.4 tests. A small
compatibility normalization may wrap a legacy `ModelAction` as a
`ModelResponse`; existing fake behavior must not change.

### Gemini API request mapping

The adapter maps one bounded `ModelTurnRequest` to the official Gemini
function-calling request. The provider-facing request contains only:

```json
{
  "model": "configured-gemini-model",
  "input": "bounded task input and safe observation context",
  "tools": [
    {
      "type": "function",
      "name": "search_notes",
      "description": "...",
      "parameters": {
        "type": "object",
        "required": ["query", "source_context", "limit"],
        "properties": {},
        "additionalProperties": false
      }
    }
  ],
}
```

For a subsequent turn, the adapter maps the previous bounded ToolCall and
ToolResult to the provider's function-call/function-result input steps. It
does not send the full RuntimeState, ledger, checkpoint, or artifact contents.
The provider interaction ID is not used as LinkLoom state; the runtime sends
the required bounded turn context explicitly.

The adapter must parse one of these normalized provider shapes:

```json
{
  "type": "function_call",
  "id": "provider-call-id",
  "name": "search_notes",
  "args": {},
  "finish_reason": "tool_call"
}
```

or:

```json
{
  "type": "text",
  "text": "safe answer text",
  "finish_reason": "stop",
  "response_id": "provider-response-id"
}
```

The Worker must normalize the SDK response through explicit field extraction;
it must not persist or expose the SDK object. Missing/invalid function name,
non-object arguments, undeclared tool calls, empty final text, malformed
response steps, and unsupported response shapes become provider-neutral errors.
The provider's raw prompt, raw response, thought signature, and exception
traceback are not stored.

The current P8.4 loop has only `observation`; the Worker must make the
`previous_tool_call` addition backward-compatible and populate it from the
durable normalized action before calling a real provider. Existing fake model
callers that do not use provider function-response mapping may continue to
leave it null.

### Multiple ToolCall strategy

P8.5 chooses strategy A: allow exactly one ToolCall per model response. If the
provider returns more than one, the adapter returns
`MODEL_RESPONSE_UNSUPPORTED` with `reason=multiple_tool_calls`; it must not
silently select the first call or discard the rest.

Parallel tool execution, fan-out, and multi-call continuation are P8.6 or
later concerns.

## Tool Schema Mapping

The mapping must preserve the current `ToolDefinition` contract:

- tool name/id and version;
- description;
- object, array, string, integer, number, boolean, and null types;
- `required`;
- `properties`;
- `additionalProperties`;
- `minLength`/`maxLength`;
- `minimum`/`maximum`;
- `minItems`/`maxItems`;
- `items` and `pattern` when supported by the JSON protocol.

`tool_id`/name and version remain LinkLoom-side identity metadata. Only the
provider-supported function name, description, and parameter schema are sent
as Gemini function declaration fields; a LinkLoom version field must not be
smuggled into the provider schema as an unsupported keyword.

Because the Gemini API and SDK support a defined JSON Schema subset,
unsupported keywords cannot be silently dropped. If a definition cannot be
represented without losing a required safety constraint, the adapter fails
closed before provider invocation with `MODEL_TOOL_SCHEMA_UNSUPPORTED`.

Provider schema validation is not a replacement for ToolRuntime validation.
The runtime remains authoritative before permission and executor invocation.

## Durable Lifecycle Integration

The real adapter must enter the existing P8.4 lifecycle unchanged:

```text
request artifact durable
  -> ModelExecutionRecord(request_durable)
  -> ModelExecutionRecord(request_sent)
  -> provider invocation
  -> normalized ModelResponse
  -> response_obtained
  -> response artifact + hash
  -> response_durable
  -> ToolRuntime or TerminationState
```

Required invariants:

1. The provider is never called before the request checkpoint succeeds.
2. A response action is never sent to ToolRuntime before its response artifact
   and hash are durable.
3. A `response_durable` checkpoint reuses the normalized action without a
   second provider call.
4. A `tool_result_durable` checkpoint resumes with the existing ToolResult and
   never re-executes the tool.
5. `request_sent` or an unknown provider outcome never causes blind
   reinvocation.
6. Provider errors become safe failed model records; SDK exceptions do not
   cross into `SingleAgentModelLoop`.
7. `max_steps` remains runtime-owned. Existing ToolPolicyEnforcer remains the
   authority for tool permission and `max_tool_calls`.

Extend `ModelTurnRequest` additively with the optional `previous_tool_call`
field. Extend `ModelExecutionRecord` only as needed for:

- provider request/response IDs;
- finish reason;
- normalized model-provider error;
- normalized usage and safe metadata.

Do not create a second checkpoint, ledger, permission system, or artifact store.

## Security And Privacy

### Credentials

The adapter must not accept or persist API keys in `ModelTurnRequest`,
`RuntimeState`, `ModelExecutionRecord`, artifact payloads, trace events, test
fixtures, or exception messages.

For the Gemini API adapter, credentials remain owned by the SDK/client
configuration boundary and environment. The provider adapter may receive an
injected client in tests or construct an SDK client only from an explicit
composition boundary. It must not accept an API key in the neutral request,
durable state, artifacts, traces, or error messages, and it must not hard-code
a private user path.

### Provider input boundary

The provider may receive only:

- the current user/task input after bounded-size validation;
- the current safe `ToolResult` observation projection;
- approved ToolDefinitions;
- neutral model options.

It must not receive:

- the full `RuntimeState`;
- the complete ToolExecutionLedger;
- checkpoint internals;
- absolute filesystem paths;
- raw internal exceptions;
- the full Vault or Gold/evaluation data;
- write-capable tools unless a later approved feature explicitly exposes them.

### Persisted response boundary

Persist only the normalized action, safe usage, safe provider metadata, IDs,
finish reason, and normalized error. Raw SDK request/response objects are not
durable artifacts. If safe structured provider metadata is retained, it must
be bounded JSON and pass the existing forbidden-key validation.

## Optional Real Smoke Test

The smoke test is opt-in and synthetic only. It may run only when:

- the official Gemini SDK/client is available;
- the user environment already owns valid provider authentication;
- the explicit smoke-test flag is enabled;
- no credential or provider-secret field is printed or persisted;
- only a synthetic prompt and synthetic read-only `search_notes` definition are
  used;

Without those conditions the report must contain exactly:

```text
real_provider_smoke_test = NOT_RUN
```

Offline fake-client and recorded-response tests are mandatory and do not count
as a real smoke test.

## P8.6 Readiness Audit (No Implementation)

After P8.5, production RetrievalAgent migration is still blocked until a new
P8.6 SPEC addresses:

- retrieval system/prompt instruction contract;
- tool availability and read-only capability boundary;
- evidence-required final answer schema;
- retrieval-specific termination conditions;
- max-step and max-tool-call behavior;
- trace correlation and model/provider metadata policy;
- checkpoint and resume behavior with real provider uncertainty;
- provider-unavailable fallback to deterministic RetrievalAgent;
- trajectory/evaluation gate on synthetic fixtures;
- explicit user-visible disclosure when note content leaves the device.

P8.5 must produce a short readiness checklist, but must not change
`RetrievalAgent`.

## Explicit Non-Goals

- No production RetrievalAgent migration.
- No multi-agent model-driven migration.
- No Memory or Evaluation redesign.
- No LangGraph or MCP redesign.
- No automatic provider retry framework.
- No automatic side-effect replay or exactly-once claim.
- No concurrent provider fan-out or multiple ToolCall execution.
- No provider routing or load balancing.
- No repair of legacy `relation_eval` fixtures or paths.
- No real Vault access or writeback.
- No commit, push, PR, or GitHub write.

## Acceptance Criteria

- [ ] P8.5 SPEC, implementation plan, and task record are present and
      explicitly identify the approved boundary.
- [ ] The provider-neutral request/response/error contract is JSON-safe,
      backward-compatible with P8.4, and contains no SDK types.
- [ ] `ModelResponse` can represent exactly one ToolCall or one final answer,
      plus usage, metadata, IDs, finish reason, or normalized error.
- [ ] The Gemini API adapter has an injected client seam and no API-key field,
      hard-coded private path, CLI invocation, or credential persistence.
- [ ] ToolDefinition schema mapping preserves required safety constraints and
      fails closed when it cannot represent one.
- [ ] ToolCall, final answer, malformed response, empty response, invalid
      finish reason, and multiple ToolCall behavior are covered offline.
- [ ] Authentication, invalid request, malformed response, rate limit,
      timeout, transient, unavailable, parse, unsupported, and cancellation
      errors map to stable safe categories without SDK traceback leakage.
- [ ] Usage and request/response identity normalization is tested; missing
      provider token counts remain null rather than guessed.
- [ ] P8.4 request/response durability and resume behavior remain unchanged;
      durable response reuse does not call the provider twice.
- [ ] Provider errors are durably safe and do not cause blind reinvocation.
- [ ] Existing FakeModel and P8.1–P8.4 tests remain green.
- [ ] No real Vault is touched; real smoke is either safely executed on a
      synthetic request or reported `NOT_RUN`.
- [ ] P8.6 RetrievalAgent migration blockers are documented without code.

## Required Evidence

The Worker completion report must include:

1. exact changed files;
2. provider choice and capability matrix;
3. request/schema/response/error mapping examples;
4. focused offline test count;
5. P8.1–P8.4 regression count;
6. full-suite classification, including unchanged legacy relation_eval issues;
7. compileall and diff-check results;
8. secret/privacy audit result;
9. real smoke status;
10. explicit claims boundary and P8.6 blockers.
