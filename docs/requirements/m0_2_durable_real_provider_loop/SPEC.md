# SPEC: M0.2 Durable Real-Provider Loop Completion

Status: **APPROVED FOR WORKER IMPLEMENTATION** within the frozen file/test
boundary below.

This Child SPEC was created under the human-approved Planner-only boundary on
2026-08-30 and corrected after P8.5 WP-3 acceptance. The human coordinator now
authorizes a separate Worker to implement only this frozen M0.2 boundary. This
planning correction itself does not implement production/tests and does not
authorize a provider network call, M0.3, or any Git remote operation.

## Parent Milestone

- Parent execution milestone: **M0 — Production Agent Closeout**.
- Parent execution slice: **M0.2 — Durable Real-Provider Loop Completion**.
- Primary canonical product relationship: **Milestone 2 — Can Find**.
- Supporting canonical relationship: **Milestone 5 — Can Act**.
- Governing Master:
  [MASTER_SPEC_V2.md](../linkloom-master/MASTER_SPEC_V2.md).
- Predecessor: [M0.1 P8 Integration Repair](../m0_1_p8_integration_repair/SPEC.md),
  accepted on 2026-08-30.

M0.2 is a cross-cutting runtime integration slice. It is not a replacement
product milestone and does not change
[docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md).

## Master Gate

Primary:

- **Gate A — Real Model**: connect the existing provider-neutral response
  boundary to the durable runtime path.

Supporting:

- **Gate B — Real Agent Loop readiness**: prove the durable model/tool/model
  mechanics on an isolated synthetic loop.

M0.2 cannot complete Gate B. The production RetrievalAgent remains a fixed
Python `search -> read -> return` workflow until M0.3. Offline M0.2 acceptance
also does not by itself complete Gate A: Gate A still requires a separately
approved, credentialed, budget-capped synthetic real-provider smoke and its
own independent evidence.

## Problem

P8.3–P8.5 produced two compatible but disconnected halves:

1. `SingleAgentModelLoop` owns durable request, response, ToolRuntime,
   checkpoint, resume, max-step, and termination behavior, but invokes only
   the legacy `ModelAdapter.decide() -> ModelAction` seam.
2. `GeminiProviderAdapter` implements
   `ModelProviderAdapter.complete() -> ModelResponse`, including native tool
   proposal parsing, final answers, usage, IDs, metadata, and normalized
   provider errors, but no production runtime calls it.

As a result, the repository can prove a durable FakeModel loop and can prove
an offline provider adapter separately, but it cannot prove this chain:

```text
ModelTurnRequest
  -> ModelProviderAdapter.complete()
  -> ModelResponse
  -> durable response artifact
  -> ToolCall / Final / normalized provider error
  -> existing ToolRuntime or runtime-owned termination
```

The existing `ModelTurnRequest.previous_tool_call` contract is also inactive
inside the loop. After a ToolResult becomes the next observation, the request
contains the observation but not the durable ToolCall that produced it. A
Gemini function-response turn therefore cannot be assembled by the current
loop.

## Current Facts

These facts come from the current dirty worktree, not from planned behavior.

### Current model invocation

- `SingleAgentModelLoop.__init__()` requires an object with callable
  `decide()` and rejects a provider adapter that exposes only `complete()`.
- The non-durable loop calls `self.model.decide(request)`.
- The durable loop also calls `self.model.decide(request)` after checkpointing
  `request_sent`.
- `ModelProviderAdapter.complete()` is implemented by
  `GeminiProviderAdapter`, but current repository usage is limited to the
  provider adapter/schema unit tests.
- `src/linkloom/runtime/model_loop.py` neither imports nor consumes
  `ModelResponse` or `ModelProviderError`.

### Current request durability

The durable loop already writes:

```text
tool definitions artifact
request artifact:
  runtime_identity
  user_input
  tool_definition_snapshot_ref
  observation_ref
  complete model_request = ModelTurnRequest.to_dict()
ModelExecutionRecord:
  request_ref
  request_sha256
  status = request_durable
```

P8.5 WP-3 corrected resume so a `request_durable` turn is loaded from this
artifact and SHA-256 rather than rebuilt from new caller input. Missing,
tampered, malformed, or identity-mismatched request artifacts fail closed.

### Current response durability

The durable loop currently writes only a fake-oriented response projection:

```text
runtime_identity
normalized_action = ModelAction.to_dict()
usage = {}
provider_metadata = {adapter: provider_neutral_fake}
```

`ModelExecutionRecord` can carry `normalized_action`, `usage`, and
`provider_metadata`, but it has no explicit provider request ID, provider
response ID, finish reason, or normalized provider-error projection. A full
`ModelResponse` is not an artifact or checkpoint input today.

### Current previous-tool relationship

- `ModelTurnRequest` already supports `previous_tool_call`, `observation`,
  `model_id`, and bounded generation options.
- Contract validation requires a previous call to have an observation and
  requires its `call_id` and `tool_id` to match that observation.
- `GeminiProviderAdapter` already maps a valid previous call plus observation
  into provider function-call/function-response content.
- Both current loop request constructors omit `previous_tool_call`, including
  after a successful durable tool observation and after resume from
  `tool_result_durable`.

### Current recovery semantics

The existing pure `decide_model_resume()` contract already establishes:

| Durable fact | Current decision |
|---|---|
| no model record or `request_durable` | `safe_to_invoke_model` |
| `request_sent` | `requires_verification` |
| `response_obtained` | `requires_verification` |
| `response_durable` | `reuse_durable_model_response` or reuse terminal tool outcome |
| `tool_result_durable` | `resume_from_tool_result` |
| non-running termination | `already_terminal` |

This contract is sufficient for M0.2. Recovery redesign and blind provider
replay are not required.

### Current governance evidence

- M0.1 is accepted with independent `PASS_WITH_FINDINGS`, no blockers.
- P8.5's implementation plan already defines WP-4 durable model-loop
  integration and is the design basis for M0.2.
- The current P8.5 task record still says its WP-3 correction awaits
  independent re-review. Before a Worker starts M0.2, the coordinator must
  cite or record the actual independent WP-3 review evidence; this is a
  governance-record prerequisite, not an architecture redesign request.
- No real provider smoke has run. Its current truthful status is
  `real_provider_smoke_test = NOT_RUN`.

## Current And Target Execution Paths

Current durable path:

```text
ModelTurnRequest
  -> ModelAdapter.decide()
  -> ModelAction
  -> fake-oriented response artifact
  -> ToolRuntime / TerminationState
```

M0.2 target:

```text
ModelTurnRequest
  -> minimal runtime compatibility seam
       -> ModelProviderAdapter.complete() -> ModelResponse
       -> legacy ModelAdapter.decide() -> wrapped ModelResponse
  -> full normalized response artifact + hash
  -> response_durable checkpoint
  -> ModelAction.tool -> existing ToolRuntime / policy / ledger
  -> ToolResult observation
  -> next ModelTurnRequest(previous_tool_call + observation)
  -> ModelResponse(final) or ModelResponse(error)
  -> runtime-owned termination
```

This remains an isolated single-agent runtime harness. M0.2 does not route the
production RetrievalAgent through it.

## Goals

M0.2 must:

1. let the durable loop invoke one existing `ModelProviderAdapter.complete()`
   without breaking legacy FakeModel tests;
2. make a complete normalized `ModelResponse` durable before its action or
   error affects ToolRuntime or termination;
3. preserve usage, safe metadata, provider request/response IDs, finish reason,
   and normalized provider errors when available;
4. populate `previous_tool_call` from the exact durable normalized ToolCall
   that produced the current ToolResult observation;
5. reuse durable provider responses without duplicate provider invocation;
6. keep `request_sent` and `response_obtained` crash windows fail-closed;
7. keep ToolRuntime, ToolPolicyEnforcer, ToolExecutionLedger, max steps,
   checkpoints, resume decisions, and termination under LinkLoom ownership;
8. prove the complete offline provider -> tool -> observation -> provider ->
   final chain with deterministic injected clients and synthetic tools.

## Scope

### A. Minimal provider compatibility seam

`SingleAgentModelLoop` will accept either:

- a provider adapter with `complete(ModelTurnRequest) -> ModelResponse`; or
- the existing legacy fake adapter with
  `decide(ModelTurnRequest) -> ModelAction`.

The durable path prefers `complete()` when available. The legacy `decide()`
result is wrapped internally as a `ModelResponse` with empty usage and explicit
legacy-fake metadata. No new bridge class, base-class hierarchy, provider
registry, or second loop is created.

The non-durable P8.3 path remains legacy-only. `complete()` may execute only
when all of these facts hold:

1. a valid `artifact_store` has written the exact request artifact;
2. an actual durable checkpoint callback is present;
3. that callback successfully persisted `request_durable`; and
4. that callback successfully persisted `request_sent`.

Artifact persistence alone is insufficient. An in-memory `publish()` is not a
durable checkpoint. Missing or failed checkpoint capability must fail closed
before `complete()`, keep the Provider call count at zero, and expose an
explicit failure.

### B. Full ModelResponse lifecycle

For every durable provider attempt:

1. build or reload the exact `ModelTurnRequest`;
2. write the request artifact and SHA-256;
3. persist `request_durable` through the actual checkpoint callback;
4. persist `request_sent` through that callback immediately before invocation;
5. only after both callback operations succeed, invoke the adapter once;
6. persist `response_obtained` after a normalized `ModelResponse` returns;
7. write the complete normalized response artifact;
8. persist `response_durable` with ref/hash and safe projections;
9. only then consume action/error.

If either pre-invocation checkpoint callback is absent or fails, the Provider
must not run and the failure must remain explicit; execution must not silently
continue.

The response artifact is the payload source of truth and contains:

```text
runtime_identity
model_response = ModelResponse.to_dict()
normalized_action = action projection or null
usage = normalized usage projection
provider_metadata = safe metadata projection
```

The existing projection fields remain for P8.4 compatibility, but loading must
verify that they match the full envelope. Raw SDK request/response objects and
hidden reasoning never enter the artifact.

Canonical recovery of a Provider-generated full `ModelResponse` requires its
response artifact, `response_ref`, SHA-256, and runtime identity validation.
The legacy P8.4 inline `normalized_action` fallback may remain for compatible
legacy records, but it must not recover or impersonate a full M0.2 Provider
response.

### C. Additive durable record projection

`ModelExecutionRecord` remains the lifecycle/index source of truth. Add only
the optional fields required to index a normalized provider response:

- `provider_request_id`;
- `provider_response_id`;
- `finish_reason`;
- `provider_error` as a safe `ModelProviderError.to_dict()` projection.

Existing `normalized_action`, `usage`, `provider_metadata`, refs, and hashes
remain. Old checkpoints without the new fields must still deserialize with
null/default values. Runtime loading verifies record projections against the
response artifact; duplicated projections are derived indexes, not competing
payload authority.

### D. previous_tool_call activation

When a durable ToolCall produces a ToolResult:

- retain the runtime-bound ToolCall from the durable normalized action;
- pass that ToolCall and the ToolResult together to the next request;
- persist both through the existing request/observation artifacts;
- on resume from `tool_result_durable`, reconstruct both from the same durable
  model record and observation artifact/ledger;
- reject call/tool/run/task/agent/sequence identity mismatch before invoking
  the provider.

The loop does not add a Message, Conversation, AgentTurnMessage, or generic
Observation model. `ToolResult` remains the observation representation.

### E. Provider error integration

A `ModelResponse.error` is a provider outcome, not a ToolResult and not a
ToolRuntime failure.

- Persist the normalized error inside the response artifact and durable record
  before terminal handling.
- Do not construct or execute a ToolCall.
- Map a safe derived error into the existing `ModelLoopResult.error` contract
  without changing that result type.
- Preserve normalized code, retryable hint, outcome classification, safe IDs,
  metadata, and details; do not persist a raw exception or traceback.
- `unknown_provider_outcome` produces an explicit fail-closed termination and
  never automatic replay.
- `retryable` remains descriptive. M0.2 adds no retry loop.

An adapter contract violation or raised exception still becomes a safe runtime
failure. Provider SDK exceptions should normally already be normalized by the
adapter and must not leak through the loop.

## Ownership

| Concern | Owner after M0.2 | Rule |
|---|---|---|
| Provider request mapping/call/response normalization | existing provider adapter | No local tool execution or runtime decision. |
| Model-turn lifecycle and checkpoints | `SingleAgentModelLoop` | Request/response durability precedes consumption. |
| Request/response payload | `ModelArtifactStore` | Relative ref + SHA-256; normalized JSON only. |
| Lifecycle index/projections | `ModelExecutionRecord` in `RuntimeState` | Must match artifact payload. |
| Tool validation/permission/budget/execution | existing `ToolRuntime` + `ToolPolicyEnforcer` | Provider only proposes a ToolCall. |
| Tool durable facts | existing `ToolExecutionLedger` | No provider-owned ledger. |
| Resume classification | existing `decide_model_resume()` | Pure decision; no provider/tool invocation. |
| Max steps and termination | runtime / `TerminationState` | Model final is a proposal, not authority. |
| Retry or manual recovery decision | runtime/external coordinator | No automatic provider retry in M0.2. |

### Architecture decision

M0.2 uses a **minimal compatibility seam inside `SingleAgentModelLoop`**. It
does not replace the loop's dependency with provider-only types because doing
so would unnecessarily break P8.3/P8.4 FakeModel evidence. It also does not
add a formal adapter hierarchy because the two existing protocols already
express the required boundaries.

In the durable path:

```text
callable complete() present -> consume ModelResponse directly
else callable decide() present -> wrap ModelAction in ModelResponse
else -> fail validation before any model/provider call
```

If an object exposes both methods, durable execution uses `complete()`;
non-durable legacy execution uses `decide()`.

## Durable State Transitions

```text
request_prepared (in process only)
  -> request artifact + SHA-256
  -> request_durable checkpoint successfully persisted by callback
  -> request_sent checkpoint successfully persisted by callback
  -> one adapter invocation
  -> normalized ModelResponse in process
  -> response_obtained checkpoint
  -> response artifact + SHA-256
  -> response_durable checkpoint
       -> action.tool_call -> ToolRuntime -> tool_result_durable
       -> action.final -> completed termination
       -> error -> failed/verification-required termination
```

An artifact file that exists without a checkpointed ref/hash does not become a
source of truth. An in-memory event publish also does not prove durability.
Resume follows `RuntimeState`, verifies its referenced artifact, and ignores
unreferenced orphan files.

## Crash And Resume Semantics

| Window | Last durable state | Resume behavior | Provider replay |
|---|---|---|---|
| No durable checkpoint callback | no durable request state | Fail explicitly before `complete()`; an artifact alone is not authorization. | forbidden; call count 0 |
| `request_durable` callback fails | persisted state remains before `request_durable` | Preserve explicit checkpoint failure; do not continue. | forbidden for the failed attempt; call count 0 |
| `request_sent` callback fails | `request_durable` | Preserve explicit checkpoint failure; a later attempt may proceed only after `request_sent` is successfully persisted. | forbidden for the failed attempt; call count 0 |
| Before request durability | no record for the attempt | No recoverable invocation; caller may start a normal new turn. | none recorded |
| Request durable, `request_sent` absent | `request_durable` | Reload exact request artifact; invoke only after the callback successfully persists `request_sent`. | allowed only after that persistence |
| `request_sent`, no durable response | `request_sent` | Return verification/manual-decision error. | forbidden |
| Provider returned, response not durable | `response_obtained` | Outcome remains ambiguous. | forbidden |
| Response file written but ref/hash checkpoint failed | persisted state remains pre-`response_durable` | Ignore orphan file; require verification. | forbidden |
| Successful response durable | `response_durable` | Reload and verify full ModelResponse, then consume its action. | forbidden |
| Error response durable | `response_durable` | Reload error and terminalize safely. | forbidden |
| Tool proposal durable, no ledger record | `response_durable` | Reuse ToolCall and execute through ToolRuntime once. | provider forbidden; tool allowed once |
| Tool ledger pending | pending ledger fact | Existing verification decision; no blind tool replay. | forbidden |
| Terminal ToolResult/observation durable | terminal ledger / `tool_result_durable` | Reuse result and build next request with matching previous ToolCall. | only the new turn may call provider |
| Runtime termination non-running | terminal state | Return existing terminal result/blocked state. | forbidden |

M0.2 makes no exactly-once claim. It proves durable reuse and conservative
at-most-one invocation within the covered checkpoint protocol, while ambiguous
outcomes remain explicit.

## Inputs

- existing `ModelTurnRequest`, `ModelResponse`, `ModelProviderError`, and
  `ModelAction` contracts;
- existing `GeminiProviderAdapter` with an injected offline fake client;
- existing P8.4 artifact/checkpoint/recovery models;
- existing ToolRuntime, ToolPolicyEnforcer, and ToolExecutionLedger;
- synthetic ToolDefinitions and tool executors only;
- current dirty worktree, preserving unrelated changes.

No real Vault, credential, network endpoint, raw SDK object, Gold label, or
private path is an M0.2 input.

## Outputs

- one provider-neutral durable model-loop seam;
- full normalized ModelResponse artifacts and indexed state projections;
- a matching previous ToolCall/ToolResult request relationship;
- safe provider-error durability and fail-closed termination;
- deterministic offline E2E and crash/resume evidence;
- an updated M0.2 task record for independent review.

It does not output a model-driven RetrievalAgent, business answer workflow,
RAG improvement, Memory context, or real-provider network result.

## Exact File Boundary

Expected production files:

- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`.

Expected tests:

- add `tests/integration/test_p85_durable_provider.py`;
- minimally modify `tests/unit/test_p84_artifacts.py` for additive durable
  record round-trip/security coverage.

Expected evidence document:

- update `docs/requirements/m0_2_durable_real_provider_loop/task.md`.

Inspection/regression only unless a separate SPEC adjustment is approved:

- `src/linkloom/agents/model_adapter.py`;
- `src/linkloom/agents/providers/`;
- `src/linkloom/runtime/artifacts.py`;
- `src/linkloom/runtime/recovery.py`;
- ToolRuntime, policy, ledger, RetrievalAgent, Coordinator, Adapter, Memory,
  Evaluation, mutation, CLI, fixture, and Gold files;
- existing P8.3/P8.4/P8.5 tests other than the one additive artifact test.

The current artifact store and recovery decision table are sufficient. If a
Worker proves that either must change, it must stop and request a narrow SPEC
adjustment before editing.

## Safety, Permission, And Privacy Boundary

- Provider adapters receive only bounded `ModelTurnRequest` fields and
  approved ToolDefinitions.
- Local tools remain read-only synthetic tools for acceptance.
- Provider adapters never execute tools or consume ToolPolicyEnforcer budget.
- API keys, credentials, authorization headers, provider tokens, raw provider
  requests/responses, SDK objects, traceback, and hidden reasoning are
  forbidden from durable state, artifacts, traces, tests, and reports.
- Ordinary application text required for exact recovery remains exact; M0.2
  does not implement arbitrary string DLP or redaction.
- No credential discovery, environment enumeration for secrets, or request for
  pasted secrets is allowed.
- No real Vault or network call is allowed in the required implementation
  path.

## Non-Goals

M0.2 does not authorize:

- production RetrievalAgent model-driven migration;
- any RetrievalAgent business-semantic change;
- M0.3 or M0.4 implementation;
- Team Decision & Action business logic;
- Memory -> model context or Context Assembly;
- semantic/hybrid RAG;
- Agent Eval expansion;
- Curator/Reviewer migration;
- additional Agent roles or Multi-Agent redesign;
- a new provider or changes to the current Gemini mapping absent a proven
  blocker;
- a provider registry, routing, load balancing, streaming, or parallel calls;
- a generic retry framework or automatic replay;
- Runtime, checkpoint, artifact-store, or Recovery redesign;
- a Message/Conversation/Observation framework;
- UI, writeback, real Vault, Fixture/Gold, or legacy evaluation repair;
- uncontrolled real-provider smoke;
- commit, push, PR, branch rewrite, or GitHub write.

## Acceptance Criteria

An independent Reviewer may accept M0.2 only when all of the following have
objective evidence:

1. `SingleAgentModelLoop` accepts the existing provider `complete()` seam in
   durable mode and retains all existing FakeModel `decide()` behavior.
2. A provider exposing only `complete()` cannot be invoked through the
   non-durable path.
3. The exact request artifact and its SHA-256 are written before the durable
   request checkpoints and before any Provider call.
4. **No durable checkpoint -> no Provider invocation:** with a valid
   ArtifactStore and Provider adapter but no durable checkpoint callback, the
   loop fails explicitly before `complete()`, Provider call count is zero, and
   no `request_sent` state is falsely represented as durable.
5. `request_durable` and then `request_sent` are successfully persisted through
   the callback before `complete()`; a failed callback remains observable and
   leaves Provider call count at zero.
6. A complete normalized `ModelResponse` artifact and SHA-256 are durable
   before ToolRuntime, final termination, or provider-error terminalization.
7. Provider-response recovery requires response artifact, `response_ref`,
   SHA-256, and runtime identity validation; the legacy inline action fallback
   is not canonical for a full M0.2 response.
8. Response artifact, record projections, and runtime identity are validated
   against each other; tampering or mismatch fails closed.
9. An injected provider/client produces a ToolCall, the existing ToolRuntime
   executes it under existing policy/ledger rules, and the resulting
   observation reaches the next provider request.
10. That next request contains a `previous_tool_call` whose call/tool identity
    exactly matches the observation, including after resume from a durable
    ToolResult.
11. A second provider response can produce a final answer, while
    `TerminationState` remains the final authority.
12. Provider request/response IDs, finish reason, usage, safe metadata, and a
    normalized provider error round-trip through artifact/state when present;
    absent values remain null rather than guessed.
13. A normalized provider error never invokes ToolRuntime and is exposed only
    through safe durable/derived error data.
14. `request_sent` and `response_obtained` crash windows never blindly invoke
    the provider again.
15. `response_durable` resumes reuse the full response without a duplicate
    invocation; a durable tool proposal or final/error response is consumed
    from the artifact.
16. Pending tools remain verification-required, while terminal ToolResults are
    reused without executor replay.
17. Provider `retryable` remains a hint; no adapter/runtime retry loop is
    added.
18. Existing P8.1–P8.5, M0.1 deterministic retrieval, compile, and diff
    boundaries have no M0.2-caused regression.
19. Production RetrievalAgent remains deterministic and unchanged; Gate B is
    explicitly still incomplete.
20. Required acceptance uses synthetic tools and offline injected clients
    only. Real smoke is either separately approved and recorded or exactly
    `real_provider_smoke_test = NOT_RUN`.
21. Exact changed files, test results, skipped/environment checks, remaining
    risks, independent Reviewer verdict, and human acceptance are recorded.

## Checks

The exact commands and RED/GREEN order are defined in
[implementation_plan.md](implementation_plan.md). Required layers are:

1. new provider-loop RED tests;
2. focused durable provider-loop GREEN tests;
3. P8.1–P8.5 runtime/provider regression;
4. M0.1 deterministic retrieval regression;
5. compileall, diff check, status, and scope audit;
6. full-suite classification when low-cost and environment-accessible.

## Optional Real-Provider Smoke Gate

The required M0.2 Worker path is offline. A real smoke is a separate optional
acceptance step and may occur only when the human explicitly approves:

- the synthetic prompt and tool definition;
- the selected model/provider;
- network disclosure and content boundary;
- request/token/cost budget;
- an already legal credential available through the provider's supported
  configuration boundary.

The Worker must not search for credentials or ask the user to paste one. When
the gate is absent, record exactly:

```text
real_provider_smoke_test = NOT_RUN
```

Offline success with `NOT_RUN` can accept the M0.2 integration boundary, but
cannot complete Gate A's separate network-proof criterion.

## Required Reviewer Evidence

- exact production/test/doc diff within the approved boundary;
- RED evidence before production implementation;
- one complete injected provider -> ToolRuntime -> observation -> provider ->
  final artifact/ledger/state trace;
- durable response reuse with provider-call counts;
- request-sent and response-obtained ambiguous-window evidence;
- provider-error, usage, IDs, metadata, and privacy evidence;
- previous-tool-call identity evidence in fresh and resumed execution;
- P8 and M0.1 regression results;
- explicit `Gate A PARTIAL` / `Gate B INCOMPLETE` statement unless separate
  Gate evidence exists;
- no real Vault/network/credential/Git remote operation confirmation.

## Deferred M0.3 Work

M0.3 owns:

- connecting production RetrievalAgent to the accepted model loop;
- retrieval system instructions and tool-selection policy exposed to the
  model;
- model-selected search/read arguments and continuation;
- evidence-required final response semantics;
- deterministic fallback/provider-unavailable UX;
- retrieval-specific trajectory and outcome acceptance;
- cloud disclosure for selected note evidence.

M0.2 must not pre-build these behaviors or claim that the production request
path is model-driven.

## Interview Evidence

M0.2 may truthfully demonstrate:

- a provider-neutral compatibility seam that preserves a legacy fake while
  introducing a full response envelope;
- write-ahead request durability and response-before-consumption durability;
- fail-closed ambiguous outcome handling without pretending to have
  exactly-once execution or general retry;
- strict ownership: provider proposes, LinkLoom validates and executes;
- durable correlation from ToolCall to ToolResult to the next provider turn.

It must not be described as a production RetrievalAgent, complete ReAct
system, complete Gate A/B, multi-provider platform, or real-Vault deployment.

## Learning Objective

The user should be able to explain only these two concepts:

1. **Provider response envelope versus runtime action** — `ModelResponse`
   carries provider execution facts, while `ModelAction` is only the proposed
   next step.
2. **Ambiguous outcome durability** — why `request_sent` without a durable
   response cannot be replayed safely, and why a durable response can be
   reused without a second provider call.

## Stop Rule

Stop M0.2 when the offline provider response lifecycle, durable reuse,
previous-tool-call relation, safe error path, and regression evidence pass
inside the two-production-file boundary.

Stop and return to planning before editing if implementation requires:

- RetrievalAgent, Coordinator, RuntimeAgentAdapter, provider mapping,
  artifact-store, or recovery redesign;
- a new abstraction hierarchy, registry, runtime, ledger, policy, retry, or
  state-machine framework;
- network access, credential discovery, real Vault, Memory, Eval, RAG,
  business semantics, or M0.3 work;
- files outside the exact boundary for anything other than approved test/task
  evidence.

## Dependencies And Human Approval

- This SPEC, [implementation_plan.md](implementation_plan.md), and
  [task.md](task.md) must be reviewed together.
- P8.5 WP-3 is `ACCEPTED — PASS_WITH_FINDINGS`; its independent Reviewer found
  no blocker, and both findings are mandatory M0.2 guards in this SPEC.
- The human coordinator has explicitly approved **M0.2 Worker implementation
  within the exact file/test boundary** after this documentation correction.
- This approval does not authorize M0.3, real-provider smoke, network access,
  credentials, a real Vault, or Git remote operations.
- Worker and independent Reviewer must remain separate roles.

## Planner Learning Reflection

### Step

- Role: Planner.
- Feature: M0.2 Durable Real-Provider Loop Completion.
- Files reviewed: repository governance, Master/M0.1/P8.4/P8.5 records,
  provider contracts/adapter, model loop, artifacts, state/recovery models,
  and P8.3–P8.5 tests.

### What Changed

P8.5 WP-4 is now expressed as a two-production-file Child SPEC with explicit
response source-of-truth, previous-tool-call, provider-error, and crash-window
acceptance rather than a request to “connect Gemini.”

### What I Learned

1. The current request and recovery boundaries already cover the hard
   ambiguous-outcome rule; the missing work is response-envelope consumption,
   not a recovery redesign.
2. `previous_tool_call` is already validated and provider-mapped. The loop only
   needs to carry the durable bound ToolCall forward with its observation.

### Evidence

- Two current `self.model.decide()` call sites and no runtime `complete()` call.
- Fake-oriented response artifact with empty usage.
- Existing full `ModelResponse` and Gemini offline adapter contracts.
- Existing recovery decisions for request/response/tool crash windows.
- No production/test/provider/network/credential/Vault change by this Planner.

### Next Step

A separate Worker is now authorized to write RED tests first and implement only
the exact M0.2 boundary. A separate Reviewer then accepts or rejects it. M0.3
remains deferred.
