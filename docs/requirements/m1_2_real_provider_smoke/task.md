# M1.2 Real Provider Team Decision Smoke — task record

> **2026-09-14 MERGE-READINESS DISPOSITION**
>
> Gemini and DeepSeek have both completed a real multi-turn Agent Loop through
> the production Runtime and ToolRuntime boundaries. Historical execution
> records below remain chronological evidence; they are not the current gate.
> The current stage is closed for infrastructure work and is being prepared for
> merge.

## Known semantic limitation — DeepSeek `mps-001`

The first terminal DeepSeek semantic baseline is classified as infrastructure
PASS, TeamDecision contract PASS, grounding PASS, and semantic FAIL. The
decision and status were correct, but the model populated
`rejected_alternatives` from evidence that only described non-selected,
comparative, or superseded options. The source-backed Gold expectation of an
empty list is reasonable because none of those alternatives was explicitly
rejected.

This is an accepted, non-blocking semantic limitation:

    non-selected / comparison / superseded -> rejected

No prompt, Provider, Runtime, ToolRuntime, evaluator, Golden data, or retry
change is part of this merge-readiness disposition. Future semantic
calibration, if desired, must be scoped separately.

> **2026-09-13 EXPERIMENTAL-SCOPE OVERRIDE — FIRST-EVIDENCE EXECUTION ACTIVE**
>
> The human stopped production-grade guard expansion. The only active outcome
> is one first real Gemini result for each of `mps-001`, `aer-002`, and
> `iti-005`, or an early stop at a known approved limit. The retained
> experimental controls are: fixed model/cases, inference <= 13,
> `countTokens` <= 13, input <= 22,000, output including thinking <= 6,656,
> USD 0.25, zero semantic retries, official response usage telemetry,
> no subsequent call after a reached known limit, credential non-disclosure,
> and Gold/`expected_*` isolation. All broader transport, endpoint, billing,
> artifact-state, and under-count hardening is deferred.
>
> The pre-execution review is one fast scoped check only: credential safety,
> no automatic retry, counters, allow-list, Gold isolation, test-only scope,
> and no obvious infinite call path. It is not an approval of model quality or
> a production-grade safety implementation.

> **PHASE B AMENDMENT AUTHORIZED — REMOTE `countTokens` PRE-SEND GUARD APPROVED**

Status: Amendment 1 is active. The user authorized Gemini Developer API
`countTokens` as the only pre-send token guard and approved the later real
smoke boundary. The prior local-tokenizer blocker is superseded only after the
test-only harness is updated, its offline tests pass, and an independent
Reviewer accepts the guard. No new Provider request has occurred under this
amendment.

## Governing boundary

`SPEC.md` is the sole scope and acceptance source. This record reports task
state; it grants neither a credential read nor a remote request by itself.

The execution path remains exactly:

    RuntimeEngine -> RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
      -> SingleAgentModelLoop -> ToolRuntime -> TeamDecisionResult
      -> grounding validation -> durable artifact

No production Runtime, adapter, ToolRuntime, Frozen Golden 8, or Eval Seed
change is authorized. The only Worker file boundary is the existing test-only
manual harness:

    tests/smoke/test_m12_real_provider_team_decision_smoke.py

## Completed Phase A evidence

- [x] The controlled FakeModel Golden 8 result was distinguished from a
  real-model quality result.
- [x] The production execution path, GeminiProviderAdapter seam, runtime
  budgets, trace/usage surfaces, and test-local bootstrap limitation were
  audited read-only.
- [x] The executor-facing registry is Gold-free; expected fields and evaluator
  assertions stay unavailable until execution terminates and artifacts seal.
- [x] Only synthetic workspaces may be copied into private smoke directories;
  no real vault access or mutation is in scope.
- [x] The former local-tokenizer guard stopped before a Client/Provider call.
  Its historical `23 passed, 1 deselected` offline result is not evidence that
  the new remote-preflight guard has passed.

## Approved selected cases

| Case | Coverage class | Why selected | Maximum autonomous envelope |
| --- | --- | --- | --- |
| mps-001 | Direct decision | Tests basic search/read/final autonomy without multi-note complexity masking a failure. | 3 inference turns: search, one verified read, Final. |
| aer-002 | Multi-note synthesis | Requires the model to retain and combine several independently observed notes and open items. | 6 inference turns: search, up to four verified reads, Final. |
| iti-005 | Uncertainty / insufficient evidence | Tests whether the model leaves owner/deadline unresolved rather than fabricating them. | 4 inference turns: search, up to two verified reads, Final. |

The envelopes are ceilings, never tool instructions. The model alone chooses
search queries, reads, continuation, Final timing, and TeamDecisionResult.

## Amendment 1 budget manifest

Approved Provider: Gemini Developer API `gemini-3.8-flash`.

Immutable human-supplied price snapshot: 2026-09-12, USD 0.75 / 1M input
tokens and USD 3.75 / 1M output including thinking tokens, valid through
2026-12-31. Vertex AI billing is unapproved: detect it before any remote
preflight or inference request, stop, obtain an official Vertex price snapshot,
and request a new human cost approval.

| Case | Inference requests / turns | `countTokens` preflight requests | Inference input | Preflight counted input | Output incl. thinking |
| --- | ---: | ---: | ---: | ---: | ---: |
| mps-001 | 3 / 3 | 3 | 4,000 | 4,000 | 1,536 |
| aer-002 | 6 / 6 | 6 | 12,000 | 12,000 | 3,072 |
| iti-005 | 4 / 4 | 4 | 6,000 | 6,000 | 2,048 |
| **Total** | **13 / 13** | **13** | **22,000** | **22,000** | **6,656** |

- A preflight call is separate from an inference request and model turn; both
  counters have their own hard ceiling of 13, with zero automatic retries.
- Every inference must have one immediately preceding successful official
  Developer API `models:countTokens` request. It must represent the identical
  final generation request, or a demonstrably more conservative one, including
  model, contents, system instruction, tools/function declarations, safety
  settings, and generation config after the forced 512-output-token cap. If the
  installed SDK cannot serialize those fields on Developer API, the isolated
  harness may use the official endpoint's `generateContentRequest` body through
  a standard-library HTTPS wrapper; it may never silently count only contents
  or use that wrapper for generation.
- A failed, malformed, over-limit, or non-equivalent preflight fails closed:
  it produces zero matching inference calls and no retry.
- The separate preflight counted-input ledger has the same 4,000/12,000/6,000
  per-case and 22,000 aggregate ceilings. It has no output-token allowance.
- Per-request timeout is 30 seconds. Inference and preflight each have their
  own 90/180/120-second per-case and 390-second aggregate elapsed limits.
- Conservatively reserve USD 0.05796 for both 22,000-token input ledgers plus
  6,656 output/thinking tokens. Hard total safety ceiling remains USD 0.25;
  any Provider-reported count-specific cost is added to telemetry and this same
  ceiling.
- Compare each formal response's usage metadata with its preceding preflight
  count, record the delta, and stop future requests if it proves the preflight
  was not equivalent or more conservative.

## Required isolated implementation and offline evidence

Worker must first add offline RED/GREEN coverage without constructing a Client,
reading a credential, or using a network:

- [ ] failed, malformed, timeout, or over-budget `countTokens` produces zero
  matching inference requests and zero retries;
- [ ] exact complete-request equivalence (or conservative superset) is proved
  for count and generation, including system instruction and tool declarations;
- [ ] separate count/inference counters, input ledgers, latency ceilings, cost
  reserve, and usage-delta recording are proved;
- [ ] preflight request cannot contain expected/Gold/evaluator fields and
  Vertex is rejected before any remote call;
- [ ] no tokenizer package, download, or local estimator fallback is used;
- [ ] focused offline tests pass and show no actual Provider call.

An official-SDK-shaped local fake may exercise the wrapper only; it must not
choose model tools, return a semantic TeamDecisionResult, or simulate Provider
success as a real smoke result.

## Credential and execution release

The harness may check `GEMINI_API_KEY` only after both opt-in variables below
are set. It may report only configured/not configured and that environment
variable name. It must never echo, store, serialize, trace, test-fixture, or
commit the value.

    $env:LINKLOOM_RUN_REAL_PROVIDER_SMOKE = '1'
    $env:LINKLOOM_REAL_PROVIDER_SMOKE_AUTHORIZATION = 'AUTHORIZE REAL PROVIDER SMOKE'
    $env:LINKLOOM_GATE_A_MODEL = 'gemini-3.8-flash'
    python -m pytest -q -p no:cacheprovider tests/smoke/test_m12_real_provider_team_decision_smoke.py

The command may run only after the independent Reviewer accepts the updated
guard. It may perform at most 13 `countTokens` calls and 13 formal inference
requests across only the three named cases. If credential configuration blocks
it, no other probing or Provider call is allowed.

## First-result and evaluation record

For each case, preserve the first real trajectory and report decision/action/
rejected-unresolved/uncertainty correctness; evidence coverage, unsupported
claims, and fabricated/invisible-evidence rejection; tools, reads, repeated or
unnecessary calls, premature Final, and ordering; plus inference/preflight
requests, tokens, latency, cost, usage delta, retry count, and termination.

No semantic retry is permitted for wrong decisions, tool choice, malformed
semantic answer, unsupported claims, missing evidence, premature Final, or
uncertainty errors. A transient Provider/network error is also zero-retry under
this approval and is recorded as the first outcome.

Permitted final conclusion after execution is only:

    Real-provider smoke result for mps-001 / aer-002 / iti-005

It cannot establish Golden 8 real-model coverage, 30-case quality, production
readiness, Gate D completion, or real-user validation.

## First real execution record — early hard stop

The authorized command was run once with the fixed model and exact opt-in.
The result is sealed under
`.artifacts/m1_2_real_provider_smoke/run-bc2005ab9b4e4d5fa7dd3448acd7045f/`.
It is the first valid experiment result and must not be overwritten or
semantically rerun.

| Case | First outcome | `countTokens` | Inference | Result / grounding |
| --- | --- | ---: | ---: | --- |
| `mps-001` | `COUNT_TOKENS_FAILED`; fail closed | 1 | 0 | No TeamDecisionResult; no tool action; evaluation unavailable. |
| `aer-002` | Not started after hard stop | 0 | 0 | Unavailable. |
| `iti-005` | Not started after hard stop | 0 | 0 | Unavailable. |

- The first preflight took about 0.937 seconds and returned no usable token or
  cost metadata. Therefore input, output/thinking, and actual cost are
  `UNAVAILABLE`; no formal generation request was made.
- The runtime recorded one failed model-attempt record caused by the preflight,
  with zero provider requests, zero tool calls, zero Final actions, and no
  automatic retry. This is an infrastructure/preflight failure, not a model
  semantic failure or tool-trajectory judgment.
- The sealed evaluator-unavailable record states `gold_loaded: false`.
  The executor's Gold boundary remained intact, and no credential value was
  written to the observed artifacts.
- The hard-stop guard fired before later cases. No known token, inference,
  count-request, or USD 0.25 ceiling was exceeded; the failed preflight itself
  was the termination condition. Cost cannot be asserted as USD 0 because the
  Provider supplied no usage metadata.
- No production Runtime, provider adapter, Frozen Golden 8, or Eval Seed was
  changed for this execution. The harness remains test-only.

## Deferred follow-up

Do not automatically rerun any case. The next useful increment is a
read-only/offline diagnosis of why this exact official `countTokens` request
failed, followed by a fresh human authorization before any replacement smoke
experiment. The former production-grade endpoint, alias, billing-ledger,
under-count-proof, and artifact-state requirements remain deferred by the
2026-09-13 experimental-scope override.
