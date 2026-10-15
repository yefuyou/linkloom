# M1.2 Real Provider Team Decision Smoke — implementation plan

> **2026-09-13 EXPERIMENTAL-SCOPE OVERRIDE — MINIMUM HARNESS ONLY**
>
> The current Worker goal is the first real evidence for only `mps-001`,
> `aer-002`, and `iti-005`. This override supersedes incompatible earlier plan
> detail. The test-only harness keeps just the approved model, allow-list,
> 13 inference / 13 preflight limits, 22,000 input / 6,656 output token limits,
> USD 0.25 ceiling, zero semantic retries, response usage recording, fail-closed
> known-budget stopping, credential non-disclosure, and Gold isolation. Do not
> expand production-grade base-URL, custom-endpoint, alias-matrix, billing-ledger,
> under-count-proof, artifact-state-machine, or transport-policy designs.
>
> A single fast scoped review is sufficient before the credential gate. It is
> limited to: no credential leakage, no automatic retry, counters, allow-list,
> Gold isolation, test-only scope, and no obvious infinite loop. Passing that
> review permits the authorized first execution; it does not certify a
> production guard.

> **PHASE B AMENDMENT AUTHORIZED — REMOTE `countTokens` PRE-SEND GUARD APPROVED**

Status: Amendment 1. The human approved the remote Gemini Developer API
`countTokens` strategy described in SPEC.md. This Planner update authorizes a
Worker to change only the existing test-only harness and run its offline tests.
Real execution remains contingent on a fresh independent Reviewer guard review.

The approved inference identity is Gemini Developer API `gemini-3.8-flash`.
The immutable human-supplied price snapshot is dated 2026-09-12: USD 0.75 / 1M
input tokens and USD 3.75 / 1M output including thinking tokens, valid through
2026-12-31. The harness must reject Vertex AI billing before it invokes either
`countTokens` or inference; Vertex pricing needs a new approval.

## 1. Scope and role boundary

This plan implements only the later, separately approved smoke harness
described by SPEC.md. The Planner owns this plan; a future Worker may implement
only after human approval, and a separate Reviewer must inspect actual run
artifacts before any smoke conclusion is accepted.

Phase A creates planning artifacts only. Phase B is a shorthand for the future
Worker execution step; the SPEC remains the canonical scope and acceptance
source.

## 2. Static audit findings to preserve

### 2.1 Existing production path

RuntimeEngine.start_multi_agent accepts team_decision, requires an injected
model and max_provider_requests greater than zero, persists a request
checkpoint, and calls RuntimeAgentAdapter.run. The adapter builds the existing
Coordinator, RetrievalAgent, SingleAgentModelLoop, and ToolRuntime composition.
The Coordinator publishes the validated TeamDecisionResult in a durable result
artifact; RuntimeState persists the ledger, model records, termination, and
trace.

### 2.2 Existing real-provider seam

GeminiProviderAdapter is a provider-neutral complete() adapter. It maps declared
tools to Gemini function declarations, disables automatic Provider tool
execution, returns either one normalized tool call or a Final, and records
Provider usage/metadata/errors without exposing a credential. It needs a
caller-supplied client; it is not constructed by the normal CLI.

tests/smoke/test_m04_real_provider_smoke.py contains an opt-in Gemini client
construction pattern, explicit SDK timeout, disabled ambient credential
fallback, private synthetic copy, trace, and secret scan. It must be copied only
as a safety pattern: its fixed ask prompt and fixed search/final assertion are
prohibited for M1.2.

### 2.3 Existing observability and limits

ModelExecutionRecord persists request/response/observation artifacts,
normalized action, Provider request/response IDs, finish reason, usage,
Provider metadata, and error. ModelUsage supports input/output/total tokens and
duration, but not cost. Trace summaries count Provider requests, tool calls,
retries, and duration.

The current Runtime hard-limits model turns and Provider requests, and every
model turn produces one tool action or Final. It does not provide a per-run
pre-send cumulative input-token estimator, monetary cost cap, or Gemini client
CLI bootstrap. Do not falsely describe the existing 32 KiB input-byte limit as
an exact token limit.

## 3. Proposed later file boundary

No production file is proposed for the first smoke. After approval, the Worker
may add exactly one test-only harness:

| File | Proposed responsibility |
| --- | --- |
| tests/smoke/test_m12_real_provider_team_decision_smoke.py | Explicit opt-in, credential isolation, private workspace copies, Gemini client injection, budget/secret guards, execution-only path, post-run evaluator, and machine-readable smoke summary. |

Possible artifacts are generated under a private ignored artifact root such as
.artifacts/m1_2_real_provider_smoke/<run-id>/; they are not source fixtures and
must not modify frozen workspaces.

Any need to change RuntimeEngine, RuntimeAgentAdapter, Coordinator,
RetrievalAgent, SingleAgentModelLoop, ToolRuntime, GeminiProviderAdapter, CLI,
schema, or frozen seed is a stop condition requiring a new Planner decision.
The first smoke must not use a production CLI bootstrap just for convenience.

## 4. Planned executor design

### 4.1 Gold-free execution process

The executor uses a static SmokeCase registry with only public fields:

    case_id, workspace_id, user_question, budget_id

It must not load expected fields, required/forbidden claims, or Gold answers
before the Provider execution terminates. It copies only the chosen frozen
workspace into a new private directory, hashes source and copy before/after,
scans the copy, injects the adapter, and calls the existing RuntimeEngine with
a RunRequest whose workflow is team_decision.

The model receives the existing Team Decision instruction generated from the
public query and model-visible ToolResults. It decides all tool and Final
actions. The harness may observe them but never alter them.

### 4.2 Budgeted transport with official remote token preflight

The test-only client wrapper must:

1. check explicit opt-in before reading GEMINI_API_KEY;
2. disable SDK ambient credential fallback during construction;
3. enforce distinct per-case/aggregate counters for inference requests,
   model turns, and `countTokens` preflight requests;
4. build the final outgoing GenerateContent request first, including forced
   `max_output_tokens=512`, system instruction, tool declarations, safety
   settings, and generation config;
5. call the official Gemini Developer API `models:countTokens` endpoint once
   against the identical complete GenerateContent request immediately before
   its matching inference request, with the same model ID and Developer
   endpoint. The installed SDK may be used only if it can serialize all
   token-bearing fields. If it rejects system instruction/tools on Developer
   API, use a small test-only standard-library HTTPS wrapper with the official
   `generateContentRequest` body instead; never silently reduce it to
   `contents` or use that wrapper for generation;
6. reject an inference request if the preflight call fails, returns malformed
   token data, reaches the separate 13-call preflight limit, exceeds its
   per-case/aggregate 4,000/12,000/6,000 and 22,000 counted-input limits, or
   cannot represent every input field that generation will receive;
7. enforce a 30-second timeout per inference and per preflight request, with
   separate 90/180/120-second per-case and 390-second aggregate elapsed-time
   ceilings for each request class;
8. reserve cost using both inference input and preflight-counted input at the
   approved input price plus inference output/thinking at the approved output
   price. The combined worst-case reserve is USD 0.05796 and must remain below
   the immutable USD 0.25 ceiling;
9. compare every successful formal-response usage record with its immediately
   preceding preflight count, record the delta, and terminate on a mismatch
   demonstrating that preflight was not exact or more conservative;
10. accumulate Provider-reported usage/cost telemetry and stop before any
    subsequent request after a budget breach;
11. never retry either `countTokens` or inference in this first smoke; and
12. redact exceptions and scan generated artifacts for the exact injected key.

The transport must inspect the installed SDK's effective HTTP base URL, not an
optional public endpoint label: it is valid only when `vertexai=False` and the
HTTPS authority is exactly `generativelanguage.googleapis.com`. A missing,
Vertex, custom, or look-alike base URL blocks before count and inference. Its
canonicalizer must reject conflicting snake/camel aliases and every generation
field the installed Developer API cannot serialize equivalently; SDK top-level
generation fields are nested into REST `generationConfig` only after that
check. On a post-response guard breach it records all already available
input/output/thinking/cost telemetry, marks the run terminal before another
preflight, and still emits sealed first-result and aggregate-stop artifacts.

The remote count endpoint replaces the blocked local estimator only for this
explicitly approved scope. A failed preflight has no bypass: it is a terminal
guard result, not a prompt-edit, tool decision, or opportunity to retry. The
harness must not install, download, or introduce a tokenizer dependency.

### 4.3 Case matrix

| Case | Max inference requests / turns | Max `countTokens` requests | Max inference input | Max preflight counted input | Output including thinking | Longest allowed envelope |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| mps-001 | 3 / 3 | 3 | 4,000 | 4,000 | 1,536 | search, one verified read, Final |
| aer-002 | 6 / 6 | 6 | 12,000 | 12,000 | 3,072 | search, up to four verified reads, Final |
| iti-005 | 4 / 4 | 4 | 6,000 | 6,000 | 2,048 | search, up to two verified reads, Final |

These are ceilings, never prompt text or expected trajectories. The aggregate
inference limit is 13 requests and 13 model turns. The separate aggregate
preflight limit is 13 `countTokens` requests. Both input-token ledgers are
capped at 22,000; inference output including thinking is capped at 6,656. Each
request class has a 390-second aggregate elapsed-time ceiling. The conservative
combined cost reserve is USD 0.05796 within the USD 0.25 hard ceiling.

## 5. Planned post-run evaluator

Only after Runtime finishes and artifacts are sealed may the evaluator load
frozen expected fields. It operates on the persisted result, ledger, trace, and
ModelExecutionRecords, then produces independent per-case cells:

| Dimension | Required report |
| --- | --- |
| Outcome | decision, action, rejected/unresolved, and uncertainty correctness |
| Grounding | material-claim evidence, unsupported-claim count, invisible/fabricated-ref rejection |
| Trajectory | actual tools/arguments, missing read, unnecessary/repeated call, premature Final, ordering issue |
| Runtime | requests, turns, tool calls, usage, estimate/actual cost, latency, status, normalized error, termination reason |

The evaluator may fail a case; it may not call the Provider, modify a prior
trajectory, or launch a new attempt. All unavailable values remain explicitly
UNAVAILABLE.

## 6. Retry handling

The first implementation uses automatic_retry_count = 0.

- Provider or network error: retain the first normalized failure and stop.
- Invalid structured response: terminal, no retry.
- Tool, trajectory, or budget failure: terminal, no retry.
- Insufficient evidence or semantic error: evaluate the first result, no retry.

If a future human requests one transient retry, the Worker must not just change
a constant. A Planner must update the total call, token, time, and cost limits,
and the Reviewer must confirm first-attempt preservation.

## 7. Approved Phase B command shape — still reviewer-gated

After the harness exists and the human gives exact authorization, the intended
command shape is:

    # GEMINI_API_KEY must already be supplied by the user environment; never echo it.
    $env:LINKLOOM_RUN_REAL_PROVIDER_SMOKE = '1'
    $env:LINKLOOM_REAL_PROVIDER_SMOKE_AUTHORIZATION = 'AUTHORIZE REAL PROVIDER SMOKE'
    $env:LINKLOOM_GATE_A_MODEL = 'gemini-3.8-flash'
    python -m pytest -q -p no:cacheprovider tests/smoke/test_m12_real_provider_team_decision_smoke.py

The human authorization now covers this command after Worker implementation,
offline tests, and an independent Reviewer guard acceptance. The harness must
require an immutable budget/pricing manifest and refuse its absence. If the
credential is absent, report only whether it is configured and the required
environment variable name `GEMINI_API_KEY`; never echo or persist its value.

## 8. Planned checks before and after a later run

### Before client construction

- exact user authorization, model ID, case list, pricing snapshot, and USD
  ceiling are recorded;
- selected frozen workspace hashes and Golden freeze test are clean;
- executor static scan confirms no expected or Gold symbol/content is reachable
  from the model request path;
- the immutable remote-preflight manifest records the separate inference and
  `countTokens` counters, input ledgers, timeout ceilings, and cost reserve;
- no credential value is logged.

### Offline RED/GREEN checks before real execution

1. RED: prove a failed/malformed/over-limit `countTokens` response produces
   zero matching inference calls and no retry.
2. RED: prove the wrapper sends the same complete request fields to the
   official `countTokens` request and `generate_content`, including tools and
   system instruction; a missing representable field must block. Where the
   installed SDK cannot represent this on Developer API, prove the test-only
   official REST `generateContentRequest` form carries them instead.
3. RED: prove separate 13-count and 13-inference counters, per-case/aggregate
   input ledgers, count/inference time ceilings, projected USD 0.25 cost guard,
   Gold isolation, and Vertex pre-send rejection.
4. GREEN: make each safety condition pass only with an official-SDK-shaped
   fake count surface; no fake may choose a model tool, simulate a semantic
   TeamDecisionResult, or contact a Provider.
5. RED: prove the actual SDK effective base URL rejects Vertex/custom/look-alike
   hosts before either call; prove alias conflicts and SDK-rejected config fail
   closed; prove post-response limits preserve telemetry, become terminal, and
   still produce sealed first-result/aggregate-stop evidence.

### After execution

- source and private-copy hashes are unchanged;
- actual request count, model turns, tool calls, tokens, and latency stay within
  approved budget or the case is marked budget failure;
- separate preflight count request/latency/token ledgers and preflight-vs-usage
  deltas are present for each attempted inference request;
- artifacts contain no credential value;
- all Final evidence refs are successful, verified, and model-visible;
- post-run evaluation reports each metric as pass/fail/unavailable without
  invoking the Provider;
- independent Reviewer checks evidence before acceptance.

## 9. Stop conditions

Stop immediately and return to the Planner if any of these is true:

- no exact authorization or pricing snapshot;
- the proposed model is not constructible without changing production code;
- the official `countTokens` request cannot be constructed with the complete
  generation request, fails, reaches its separate budget, or reports a token
  count beyond the remaining limit;
- a Provider model requires a second hidden tool loop or Provider-side tool
  execution;
- an executor would need Gold/expected fields to choose tools or build prompts;
- source hashes change, a credential reaches an artifact, or a real vault path
  would be touched;
- any request, token, time, or cost limit would be exceeded.

## 10. Reviewer checkpoints

The independent Reviewer must verify:

1. the implemented harness matches this SPEC and only the approved test-only
   file boundary;
2. the model, not test code, chose all tool calls and Final timing;
3. Gold data was unavailable to the execution path and used only after run;
4. every case obeyed its budget or failed closed;
5. each inference has exactly one preceding count preflight with an equivalent
   or more conservative complete request and a recorded usage delta;
6. first-failure preservation and zero automatic retries for both request
   classes;
7. trace/artifact/ledger/result consistency and no credential leak; and
8. no claim expands beyond Real-provider smoke accepted for selected cases.

## 11. Planner learning reflection

The M1.2 runtime path stays unchanged. The new safety seam is not a tokenizer
or a second decision loop: it is a bounded official token-count preflight that
must mirror the exact GenerateContent request and remain separately auditable
from model inference.
