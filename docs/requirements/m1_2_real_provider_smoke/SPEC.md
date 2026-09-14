# SPEC: M1.2 Real Provider Team Decision Smoke

> **2026-09-13 EXPERIMENTAL-SCOPE OVERRIDE — GET FIRST REAL MODEL EVIDENCE**
>
> This override supersedes every incompatible earlier requirement in this
> document. M1.2 is now an experimental, test-only first-evidence run, not a
> production billing, transport, or security-guard project. Before the first
> Gemini execution, retain only: fixed model and three-case allow-list;
> inference requests <= 13; official `countTokens` preflights <= 13; aggregate
> input <= 22,000; aggregate output including thinking <= 6,656; estimated
> experiment cost <= USD 0.25; semantic retry = 0; official response-usage
> recording; stop before further calls once a known limit is reached; no secret
> output/persistence; and no Gold/`expected_*` data in model/runtime input.
>
> Defer formal effective-base-URL anti-bypass work, Vertex/custom-endpoint
> generalisation, alias/configuration matrices, aggregate billing ledgers,
> under-count proofs, sealed-artifact/evaluator state machines, multi-layer
> hard-stop designs, and production transport policy. One fast scoped review
> may check only credential safety, no automatic retry, working counters,
> case allow-list, Gold isolation, test-only file scope, and absence of an
> obvious infinite-call path. It must not block on deferred hardening.

> **PHASE B AMENDMENT AUTHORIZED — REMOTE `countTokens` PRE-SEND GUARD APPROVED**

Status: Amendment 1 — the human has approved a Gemini Developer API
`countTokens` preflight strategy. This SPEC is the canonical scope and
acceptance boundary for M1.2. “Phase A” and “Phase B” below are the user's
execution labels, not a competing product roadmap or acceptance term. The
amendment authorizes a test-only harness change and later guarded execution;
it does not itself accept a real-model result.

Approved execution identity and immutable pricing snapshot: Gemini Developer
API, model `gemini-3.8-flash`, price snapshot dated 2026-09-12, input
USD 0.75 / 1M tokens and output including thinking USD 3.75 / 1M tokens. The
snapshot is valid only through 2026-12-31 as supplied by the human. A Vertex
AI billing path is not approved: detect it before any preflight or inference
request, stop, and return to human approval with a new official Vertex price
snapshot.

## Parent boundary

The prior M1 Team Decision Golden 8 Controlled Production Gate is accepted for
eight frozen synthetic cases with a controlled FakeModel. It proves the
production composition and safeguards, not real-model quality. M1.2 may only
establish a deliberately small, read-only real-provider smoke result for three
selected frozen cases after a separate human authorization.

## Problem

The accepted Golden 8 evidence does not show whether an actual Provider can
independently choose retrieval tools, observe returned evidence, and produce a
valid TeamDecisionResult through the existing Runtime. A smoke run must test
that boundary without exposing Gold to the model, prescribing a tool trajectory,
spending an unbounded amount, or confusing a few cases with broad model-quality
acceptance.

## Goals

1. Define a three-case, synthetic-only real-provider smoke scope with explicit
   execution, token, latency, retry, and cost limits.
2. Preserve the current ownership boundary: the model selects search_notes,
   read_verified_note, and Final timing; Runtime enforces tools, permissions,
   budgets, schema, evidence provenance, checkpoints, and durable artifacts.
3. Capture per-case outcome, grounding, trajectory, and Runtime telemetry.
4. Keep first real results, including failures, rather than rerunning semantic
   failures until one happens to pass.
5. Require named human authorization before Provider-client construction or a
   credential lookup.
6. Use the official Gemini Developer API `countTokens` endpoint as the only
   pre-send input-token authority for this smoke, with a separately bounded
   request counter and no downloaded or third-party tokenizer.

## Non-goals

- No real Provider request, credential read, token use, client construction,
  network call, or SDK availability probe in Phase A.
- No change to production Runtime, Provider adapter, ToolRuntime, Golden 8
  seed, manifest, workspace notes, or freeze test in Phase A.
- No FakeModel, scripted tool sequence, expected-field prompt injection,
  Gold-derived prompt construction, or test-selected model tools in a later
  smoke execution.
- No claim of Golden 8 real-model 8/8, broad 30-case quality, production
  readiness, Gate D completion, or real-user product validation.
- No real vault, write-capable tool, memory expansion, generic retry framework,
  provider abstraction rewrite, commit, or push.
- No `countTokens` payload may contain expected_ fields, Gold answers, required
  claims, hidden evaluator labels, or a test-selected tool trajectory.
- No third-party tokenizer download, new tokenizer dependency, or fallback to a
  local byte/character estimator.

## Static execution-path audit

The existing Team Decision composition is already injectable and read-only:

    RuntimeEngine.start_multi_agent
      -> RuntimeAgentAdapter.run
         -> Coordinator.run
            -> RetrievalAgent.execute
               -> SingleAgentModelLoop
                  -> ToolRuntime
               -> TeamDecisionResult validation/projection
         -> durable RuntimeState + result artifact + trace

Observed facts from the current source:

| Surface | Current capability | M1.2 implication |
| --- | --- | --- |
| RuntimeEngine | Requires an injected model and positive max_provider_requests for multi-agent execution. | A smoke executor must inject a real ModelProviderAdapter and set explicit positive limits. |
| GeminiProviderAdapter | Exists, maps manual function calls to the Provider, disables provider-side auto tool execution, normalizes usage and Provider errors. | Gemini is the recommended first Provider because no new production adapter is needed. |
| Bootstrap | The adapter accepts a client; the normal CLI does not construct or inject a Gemini client. Existing M0.4 smoke bootstrap is test-local. | Phase B needs a new isolated test-only smoke harness, not a production CLI change. |
| Budget | Runtime persists max_steps and max_provider_requests; Team Decision derives a bounded model-loop limit from the request. | Provider and model-turn limits can fail closed today. Per-case input-token and cost enforcement is not yet implemented. |
| Trace and durability | Model request/response/observation artifacts, provider IDs/metadata, normalized usage, tool ledger, termination state, checkpoint, and trace are persisted. | Per-case telemetry can be recovered without modifying the main Runtime. |
| Cost | ModelUsage records input/output/total tokens and duration when supplied by the Provider; it has no cost field or price table. | Actual cost is UNAVAILABLE unless an approved pricing snapshot is supplied by the smoke harness. |

The existing tests/smoke/test_m04_real_provider_smoke.py is useful only as a
credential-isolation and client-construction reference. It is an ask smoke whose
prompt directs a fixed no-match trajectory, so it must not be reused as the
Team Decision semantic executor.

## Recommended first Provider

Use the existing GeminiProviderAdapter with a human-approved Gemini model ID.
This recommendation is based on the repository's tested adapter and test-local
bootstrap, not on a claim that any particular model name, availability, or
price is current. The exact model ID and associated pricing snapshot are
separate Phase B approval inputs.

## Selected smoke cases

Only public case identity, question, and private synthetic workspace are
available to the executor. Expected fields are evaluation-only and must not be
loaded until a run terminates and its artifacts are sealed.

| Case | Class | Why it is representative | Budget-envelope longest trajectory |
| --- | --- | --- | --- |
| mps-001 | Direct decision | A small authority-selection question tests whether a real model can discover and verify one decisive source without the multi-note case hiding basic failures. | At most 3 model turns / Provider requests: one search, one verified read, then Final. |
| aer-002 | Multi-note synthesis | It requires a current rollout boundary plus several distinct open items, so a model must retain and synthesize more than one verified result. | At most 6 turns / requests: one search, up to four verified reads, then Final. |
| iti-005 | Insufficient evidence / uncertainty | It tests that the model can preserve unknown owner/deadline fields and unresolved work rather than inventing assignment from adjacent status notes. | At most 4 turns / requests: one search, up to two verified reads, then Final. |

The trajectory column is a maximum budget envelope, not instruction text,
assertion, or required sequence. The model may make fewer calls or fail closed;
the executor must never choose a search query, evidence ref, or Final point for
the model.

## Inputs

### Gold-free execution inputs

- a static public smoke-case registry containing only case_id, workspace_id,
  and the public user question;
- a private temporary copy of the selected frozen synthetic workspace;
- an index built from that copy;
- an injected Gemini client and model ID after opt-in;
- an approved immutable budget manifest with no expected or Gold fields;
- an empty private memory/checkpoint/trace/artifact root outside the workspace.

### Post-run evaluation inputs

- the sealed result artifact, RuntimeState, model-execution records, tool
  ledger, and trace;
- frozen expected fields and Gold data, loaded only after model execution stops
  and never serialized into a model request.

## Outputs

For each case, a private artifact bundle must contain:

- sanitized run manifest and source-copy hash evidence;
- RuntimeState, checkpoint, Team Decision result or normalized failure;
- durable per-turn request/response/observation artifacts and ToolRuntime
  ledger;
- trace summary with counts and termination reason;
- a post-run metric record whose unavailable values are represented as
  UNAVAILABLE, not zero or pass;
- a secret-scan result proving credentials were not persisted.

The aggregate report must contain only the three selected case IDs, exact
budgets consumed, pass/fail/unavailable metric cells, and its acceptance
boundary.

## Safety and credential handling

### Phase A — current authorization

Only static source, frozen synthetic data, and accepted evidence may be read.
No environment lookup of GEMINI_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY, or any
credential-like value is permitted. A client must not be constructed, even
merely to validate an SDK installation.

### Phase B — authorized amendment, still guard-gated

The user supplied the exact `AUTHORIZE REAL PROVIDER SMOKE` approval and this
amendment approves official remote `countTokens` only as a pre-send guard.
The test-only harness may read only GEMINI_API_KEY after a separate explicit
opt-in flag and exact authorization flag are present. The value must be passed
directly to the SDK constructor, redacted from exceptions and logs, never put in
RunRequest, RuntimeState, trace, artifact, command output, or committed file.
The SDK ambient credential fallback must be disabled during construction.

All Provider-visible content is limited to the synthetic workspace selected
above. No real vault path or note may be used.

`countTokens` is a Provider preflight request, but is tracked separately from
the 13 model-inference requests and 13 model turns below. It is not a semantic
model turn and must not invoke tools, return a TeamDecisionResult, or influence
the model's next choice beyond allowing or rejecting the exact request on
budget grounds.

## Model and Runtime ownership

The real model exclusively decides:

- whether to search and the query it proposes;
- which returned evidence to read and whether another read is needed;
- when to return Final;
- the semantic contents of the TeamDecisionResult.

Runtime exclusively owns:

- manual ToolRuntime execution, schemas, permissions, and read-only policy;
- request, step, and provider-request limits with fail-closed termination;
- checkpointing, durable artifacts, trace, and result persistence;
- strict Team Decision parsing and evidence-reference membership validation.

Test/harness code may select one of the three public cases and configure a
budget. It must not decide model tools, alter a tool call, inject a Gold answer,
or repair a semantic failure.

## Cost, token, and latency guard

These Phase B limits are human-approved under Amendment 1. “Inference request”
means `generate_content` used by the production Runtime path. “Preflight
request” means one official Gemini Developer API
`POST models/{model}:countTokens` call immediately preceding a candidate
inference request.

| Case | Max inference requests | Max `countTokens` preflight requests | Max model turns | Max inference input tokens | Max preflight counted-input tokens | Max output tokens including thinking | Per-request output cap | Max inference elapsed time | Max preflight elapsed time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mps-001 | 3 | 3 | 3 | 4,000 | 4,000 | 1,536 | 512 | 90 s | 90 s |
| aer-002 | 6 | 6 | 6 | 12,000 | 12,000 | 3,072 | 512 | 180 s | 180 s |
| iti-005 | 4 | 4 | 4 | 6,000 | 6,000 | 2,048 | 512 | 120 s | 120 s |
| **Total** | **13** | **13** | **13** | **22,000** | **22,000** | **6,656** | — | **390 s** | **390 s** |

- Automatic retry count is 0 for both counters. The inference cap is exactly
  13, not 13 plus hidden retry attempts; the separate `countTokens` cap is
  exactly 13, not 13 plus retry or probing attempts.
- Before every attempted inference request, exactly one successful
  `countTokens` request must use the same model and the same complete
  `GenerateContentRequest` representation that will be handed to
  `generate_content` after the harness forces the output cap. This includes
  contents, system instruction, tool/function declarations, safety settings,
  and generation configuration. If the installed SDK cannot represent that
  complete request in `countTokens`, the harness must block rather than count a
  smaller payload.
- The installed SDK may be used for the preflight only when it can serialize
  that complete request on the Gemini Developer API path. If its
  `models.count_tokens` surface rejects system instruction or tools, the
  test-only harness may instead make one standard-library HTTPS call to the
  official `models:countTokens` endpoint with its
  `generateContentRequest` body. That alternative must carry every token-bearing
  generation field, uses no new dependency, does not generate content, and is
  subject to the same separate count counter, timeout, credential redaction,
  endpoint, and fail-closed rules.
- The Developer-only guard must validate the SDK's actual effective request
  base URL, not merely a convenience attribute. It must be HTTPS with the
  exact `generativelanguage.googleapis.com` authority and `vertexai=False`;
  a missing, custom, look-alike, or Vertex base URL blocks before both count
  and inference. A hostname substring check is insufficient.
- SDK-friendly aliases that map to one REST field, and any field that the
  installed Developer API generation surface cannot serialize equivalently,
  must fail closed rather than silently being overwritten or represented only
  in preflight. In particular, SDK top-level generation fields are normalized
  to REST `generationConfig` only after this representability check.
- `countTokens` uses the official request form that accepts a complete
  `generateContentRequest`, rather than a text-only approximation. It is
  allowed to be more conservative, never less. The preflight counted-input
  total shares the per-case and aggregate 4,000/12,000/6,000 and 22,000 token
  ceilings; a failed preflight is terminal and no inference request may follow.
- `countTokens` calls have no model output budget. They are separately recorded
  as `preflight_count_requests`, `preflight_counted_input_tokens`, and
  preflight latency. Their conservative cost reserve charges each counted input
  token at the approved input rate even if the Provider later treats the call
  as no-charge. Thus the maximum conservative combined reserve is
  `(22,000 inference input + 22,000 preflight-counted input) * $0.75 / 1M +
  6,656 output/thinking * $3.75 / 1M = $0.05796`, below the immutable USD 0.25
  hard ceiling. Any count-specific billed cost supplied by the Provider is also
  added to actual cost telemetry and can trigger the same fail-closed ceiling.
- The aggregate estimated-cost ceiling is USD 0.25. Before a run, a
  human-approved immutable pricing snapshot must calculate a worst-case cost
  from the token ceilings at or below this amount. Missing, stale, or
  unapproved pricing data blocks execution.
- actual_cost_usd is UNAVAILABLE unless the Provider explicitly reports a
  billable cost. estimated_cost_usd is available only from the approved snapshot
  and must identify that snapshot.
- Provider `usage_metadata` remains post-response telemetry. The harness must
  record the preflight count, reported prompt tokens, and their delta for every
  inference request. A missing, malformed, or over-budget reported usage value,
  or a mismatch that proves the preflight request was not equivalent or more
  conservative, preserves the first result and terminates the smoke without a
  retry.
- If a post-response usage/token/cost/timeout guard fails, it must first record
  every already available telemetry value and then become terminal before any
  further preflight or inference. A hard guard stop still writes a sealed
  first-result artifact and explicit evaluator-unavailable/aggregate-stop
  record; it never loses the failed evidence.
- The smoke transport must set the 512-token output cap before every Provider
  inference request and stop on any inference, preflight, token, time, or
  money-budget breach.

## Retry policy

The first real outcome is immutable evidence. The following classifications are
recorded separately:

| Failure class | First-smoke action |
| --- | --- |
| Explicit transient Provider/network error | Record the normalized Provider error and stop. Automatic retry is disabled (0); a later manual rerun needs new human authorization. |
| Invalid structured output / malformed Provider response | Terminal failure; no retry. |
| Bad tool trajectory, repeated call, premature Final, or budget exhaustion | Terminal behavioral failure; no retry. |
| Insufficient evidence result | Valid model outcome; evaluate it, do not retry. |
| Semantically wrong but structurally valid result | Terminal evaluation failure; no retry. |

This intentionally does not rely on the existing Provider error retryable flag
to run a hidden retry loop. Any future nonzero retry policy requires a new SPEC
amendment, an expanded request/cost budget, and human approval.

## Metrics

Metrics are per case first; no total quality score is invented.

### Outcome

- decision correctness;
- action correctness, including owner/deadline/status null discipline;
- rejected-alternative and unresolved-item correctness;
- uncertainty correctness.

### Grounding

- whether every material claim has evidence;
- unsupported-claim count;
- whether fabricated or Final-invisible evidence is rejected by Runtime.

### Tool trajectory

- chosen tools and arguments;
- missing necessary reads, unnecessary calls, and repeated calls;
- premature Final and ordering issues;
- actual trajectory compared post-run only with the case evaluation criteria.

### Runtime

- Provider requests, model turns, tool calls, input/output/total tokens;
- estimated and actual cost, each with UNAVAILABLE when appropriate;
- per-request and end-to-end latency;
- normalized Provider error, Runtime status, and termination reason.

## Failure modes

- Missing client or zero request budget: fail before Provider invocation.
- Missing credential after explicit opt-in: skip or block safely without
  exposing a value.
- `countTokens` unavailable, fails, exceeds its separate request/time/input
  budget, or cannot represent the exact generation request: block before the
  corresponding inference invocation.
- Provider output has an undeclared tool, invalid schema, or invalid JSON:
  retain the first artifact and fail closed.
- A model reads no adequate evidence, fabricates a ref, or ends prematurely:
  preserve the result and mark the appropriate evaluation cell failed.
- Any frozen source hash changes: invalidate the run; do not evaluate it.
- Any secret appears in a candidate artifact/log: fail the smoke and do not
  publish the artifact summary.

## Phase A acceptance criteria

- The Phase A revision of the three documents carried the exact planning-only
  marker. Amendment 1 records the later human authorization separately and
  preserves the prohibition on any real request until implementation, offline
  tests, and independent Reviewer approval complete.
- They document the audited production path, candidate adapter/bootstrap,
  selected cases, per-case budgets, retry policy, metrics, and credential
  boundary.
- They explicitly state that no current Phase A check calls a Provider or reads
  credentials.
- They state whether production code is needed: no production Runtime change is
  proposed; a new test-only smoke harness is required before Phase B.
- Frozen Golden 8 and Eval Seed remain unchanged.

## Later execution acceptance boundary

If Phase B is later independently accepted, its only allowed conclusion is:

    Real-provider smoke accepted for selected cases

It remains insufficient evidence for any claim about all Golden 8 cases,
30-case quality, production readiness, real users, real vaults, or a completed
product milestone.

## Human approvals and Amendment 1 record

1. Exact phrase: AUTHORIZE REAL PROVIDER SMOKE.
2. Gemini Developer API model ID `gemini-3.8-flash` and SDK/version path.
3. The three-case list and exact 13-request, 22,000-input-token,
   6,656-output-token, and USD 0.25 ceiling.
4. The immutable Gemini Developer API pricing snapshot dated 2026-09-12:
   USD 0.75 / 1M input tokens and USD 3.75 / 1M output/thinking tokens.
5. Acknowledgement that only selected synthetic notes may leave the machine.
6. Approval of the test-only harness SPEC boundary and independent Reviewer
   requirement before a result is accepted.

The user has supplied all of the above and additionally approved:

- official Gemini Developer API `countTokens` as the only pre-send token guard;
- a separate hard cap of 13 preflight requests, while inference remains capped
  at 13;
- no third-party tokenizer or new tokenizer dependency;
- fail-closed behavior if `countTokens` fails; and
- comparison/recording of preflight counts and formal-response usage metadata.

No real execution result is accepted until a Worker implements these exact
rules, an independent Reviewer accepts the harness, and the guarded command
produces its first artifacts.

## Planner learning reflection

The official count endpoint can count a complete generation request, including
system instructions and function declarations. Separating its call count from
model inference avoids confusing a safety preflight with a model turn, while a
conservative duplicate-input cost reserve preserves a single USD 0.25 safety
ceiling.
