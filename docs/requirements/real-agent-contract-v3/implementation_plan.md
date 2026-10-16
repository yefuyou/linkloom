# Implementation plan: Real-Agent Contract V3

This plan is limited to the approved V3 repair boundary. It is deliberately
small: reuse the existing Runtime, RecoveryPolicy, ToolRuntime, evidence
Grounding, Reviewer, and append-only Provider Ledger; do not introduce a new
Agent framework or a second benchmark harness.

## 1. Freeze and baseline audit (Planner — this step)

1. Read the current Adaptive Planning and real-agent reliability fixture
   contracts, including public/Gold separation, workspace/document fields,
   `source_context`, observed evidence IDs, budgets, and result statuses.
2. Record the V3 baseline commit
   `37e45b169f19c4edd29db59f637e690caab31ad6` and protect production modules,
   historical Gold, old Ledgers, and existing fixtures.
3. Add exactly 10 development and 10 final synthetic cases covering the
   required contract failure classes. Keep the cases inline and synthetic so
   existing integration fixtures can adapt them without creating a renamed
   benchmark family.
4. Compute SHA-256 for `SPEC.md`, this plan, `dev.json`, and `final.json` and
   write them to `freeze.json` before the main Agent confirms any repair
   approach. Do not run final or real Provider calls.
5. Treat `input.source` and the source-context examples in `stimulus` as
   logical scanner inputs only. A fixture adapter must materialize the actual
   Runtime `SourceContext` from synthetic documents/index data before creating
   a ToolCall; pseudo-SHA, `index_id`, and `source_version` must never be
   injected directly into a Provider request.

### V2 qualification audit

The original V1 freeze used two Provider requests for most cases, which cannot
execute a Search → Read → Final loop and leaves no request for a legal bounded
Final correction. V1 is preserved byte-for-byte under `work/v3-freeze-v1/`.
V2 audits every case against the actual Runtime semantics: each model
`decide()`/`complete()` round consumes one request, while ToolRuntime work does
not; `max_steps` counts model turns; and an uncertain outcome consumes its
request and cannot be retried. The active budgets are:

| Cases | Required model rounds | `max_provider_requests` |
|---|---:|---:|
| `RAC3-D01` | 3 plus one optional correction | 4 |
| `RAC3-D02`, `RAC3-D04`, `RAC3-D08`, `RAC3-F01`, `RAC3-F05`, `RAC3-F06`, `RAC3-F07` | 4–5 by evidence reads | 4–5 |
| `RAC3-D03`, `RAC3-D05`, `RAC3-D07`, `RAC3-F03`, `RAC3-F04`, `RAC3-F08` | 3 | 3 |
| `RAC3-D06` | bounded invalid-call recovery | 4 |
| `RAC3-D09`, `RAC3-F09` | Search + Read, stop before Final | 2 |
| `RAC3-D10`, `RAC3-F10` | one uncertain request | 1 |
| `RAC3-F02` | Search/Read before stale rejection | 2 |

The exact per-case values and old/new hashes are authoritative in `freeze.json`.

## 2. P0 failure attribution (Main Agent, before repair)

1. Audit all 18 Adaptive Planning failures from the existing safe traces,
   RuntimeState, Tool Ledger, and saved reports. Mark unavailable facts as
   `UNKNOWN` or `NOT_OBSERVED`; never infer a model response from an outer
   error code.
2. Cross-check Self-Improvement V2 failures against code and tests. Separate
   Runtime defects, query-policy effects, FakeController-only behavior,
   provider formatting, and harness capture/scoring defects.
3. Record the smallest machine-readable fingerprint fields: `stage`, `code`,
   `subreason`, `plan_valid`, `required_evidence_available`,
   `required_evidence_read`, `final_received`, `final_schema_valid`,
   `final_grounded`, `provider_outcome`, `recoverable`, and
   `next_safe_action`.

No prompt, retry, or token-budget change is allowed before this attribution.

## 3. Development RED-to-GREEN contract checks (Worker)

Run only `dev.json` with deterministic offline adapters or existing integration
fixtures. For every case, assert the observable result and the guards recorded
in its author-visible Gold:

- TeamDecisionResult/typed Final parsing and exact field closure;
- Final scope, observed evidence membership, Workspace identity, and Grounding;
- valid Partial retention and Conflict preservation;
- ToolRuntime argument rejection before executor invocation;
- `dependency_entities`, `query_variant`, parent-child relation binding, and
  legal alternate queries;
- same-file evidence identity, quote/hash freshness, and read-after-search;
- model Context selection under a byte budget, including mandatory read
  retention and audit of dropped evidence;
- MAX_STEPS/provider request accounting and explicit budget failure;
- Provider Outcome Uncertain terminal handling with zero re-request after the
  uncertain result;
- stale-source rejection and foreign-Workspace suppression;
- Reviewer and recovery status propagation without converting failures into
  business uncertainty.

Tests should fail against each reproducible baseline defect before the minimal
fix, then pass after the fix. They must not read final cases as a debugging
oracle, and must not pass `gold` or expected Final payloads into a controller.

## 4. Minimal repair implementation (Worker)

1. Fix only the boundary proven by P0 and the RED development cases. Prefer
   aligning Prompt/Schema/Adapter/Validator contracts and preserving evidence
   identity over adding behavior.
2. If one bounded Final correction is necessary, route it through the existing
   ModelLoop/RecoveryPolicy, count it as a Provider request and Context use,
   stop at the existing MAX_STEPS/request limits, and forbid correction after
   Provider Outcome Uncertain. Never fabricate entities, refs, reads, or
   approval semantics.
3. Keep source identity, Workspace, Freshness, Tool permissions, Verified Read,
   Grounding, Reviewer, Ledger, and Partial/Conflict behavior unchanged.
4. Add focused regression tests only in the approved repair/test boundary. Do
   not alter old Gold to make a case pass.

## 5. A fixed-query verification (Main Agent)

Run the development set repeatedly as needed with the same query strategy,
Provider configuration, source identity, and budgets. Compare baseline and
candidate observations and report:

- baseline failures;
- candidate-fixed failures;
- candidate regressions;
- missing environment or unexecuted checks;
- request, Context, latency, and Ledger cost deltas.

Acceptance requires all critical safety guards to remain intact and no
candidate regression in the development contract cases. This is offline
evidence; it does not prove real-provider quality.

## 6. Final freeze gate and one-time execution (Main Agent)

1. Before any final execution, verify that the five fixture/spec files match
   the SHA manifest and that no final case was opened for debugging or used to
   tune the repair.
2. Confirm the Worker repair and focused checks are complete. If the SHA changes
   because a case is contaminated, invalidate the final acceptance and stop;
   do not regenerate cases to improve a score.
3. Execute `final.json` once after the repair, using the fixed query strategy
   and the same safe synthetic source boundary. Preserve every result,
   including failures; do not select a best run.
4. Record the final outcome and evidence SHA without copying final Gold into
   prompts or public product claims. The result is author-visible validation,
   not an external blind test.

No real Gemini call is part of this plan's freeze step. Any later real-provider
phase requires its own permission, budget reservation, strict request identity,
append-only Ledger records, and uncertain-outcome stop rule from the V3 user
request.

## 7. B optional query-only comparison

Only if A is stable and budget allows, repeat a separate comparison with
`query-only` as the sole changed variable. Hold the repaired Contract, source,
Workspace, permissions, provider, model parameters, and budgets fixed. If it is
skipped, mark it `NOT_RUN`; do not merge its result with A or use the earlier
Self-Improvement negative live result as a substitute.

## 8. Independent review and handoff

An independent Reviewer checks the final diff and evidence against this SPEC:

- no invalid Final was washed into success;
- no source identity, Verified Read, Workspace, Freshness, Tool permission,
  budget, Provider uncertainty, Grounding, or Reviewer gate was bypassed;
- Partial and Conflict remain lossless and explicit;
- old Gold/fixtures/Ledgers and unrelated worktrees remain untouched;
- the final partition ran at most once and the author-visible/non-blind status
  is disclosed.

The Planner does not approve the Worker or Reviewer output. Commit/push and any
real Provider experiment remain outside this planning artifact unless the
corresponding V3 authority explicitly covers them.
