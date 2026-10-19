# Independent read-only review: M3-G

## Verdict

**PASS with notes**, after three Required findings were repaired and the same
independent reviewer verified their closure. No outstanding Required findings.
This accepts the standalone integration API and its contract boundary; it does
not accept production wiring or general LLM planning quality.

Reviewer: independent subagent `/root/independent_review`, `gpt-5.6-luna`, max
reasoning, separate context. The reviewer read source, tests, SPEC, DELIVERY,
staged file boundaries and recorded evidence; ran offline probes/tests; did not
edit source, mutate Git, access other Worktrees or call a real Provider.
The reviewer left a synthetic replay under ignored `work/review-question-replay`.

## Findings and closure

1. **P1 Required — authorization followed by mutable request changes.** Original
   guard could authorize a request then mutate its nested tool schema before the
   adapter saw it. Reproduction originally returned PLANNED with the mutation
   sent. Repair: guard receives a deep copy, its digest must remain unchanged,
   and original request digest is checked immediately before the adapter call.
   Closure: `test_guard_cannot_mutate_schema_after_authorizing_request`, adapter
   calls = 0. Independently confirmed **Closed**.
2. **P1 Required — session run identity collisions.** Source snapshot hashes do
   not include the user question. Original run ID reused that hash, causing
   different questions to share run/turn identity. Repair: Host generates a UUID
   run ID per planner session, stable across replans; turn/sequence advance per
   attempted call. Closure: `test_run_identity_distinguishes_sessions_and_survives_replan`.
   Independently confirmed **Closed**. Exact request hashes vary by session;
   recorded requests remain reproducible artifacts and plan content is stable.
3. **P1 Required — error response skipped known usage limits.** Original typed
   MODEL_TIMEOUT response could report 5,000 output tokens against a 4,096
   reservation, return early, and leave the session usable. Repair: known usage
   is checked before Provider error or stale-context handling; overrun exhausts
   the session. Closure: `test_provider_error_with_over_budget_usage_exhausts_session`.
   Independently confirmed **Closed**.

All three regressions were observed failing before repair. After repair, the
worker and independent reviewer each ran the existing focused tests: **40 passed**.
The worker evidence is [review-repairs.xml](evidence/review-repairs.xml).

## Acceptance matrix

| SPEC criterion | Evidence | Result |
|---|---|---|
| Structured output, typed handles, current source/Workspace checks | 40 focused tests, forged/foreign/stale/revoked/unauthorized paths | Pass |
| Missing entities, discovery, M2 nonexhaustive hints | Empty snapshot, no-candidate relation, incomplete recall tests; 12 public scripts | Pass; live missing-entity case remains limited |
| New public question families and no answer leakage | Public input loader separate from Fake scripts/scorer; requests have no Gold/case ID/expected categories | Pass |
| 2/3 hops, dependency premises, fanout, token/call limits, dedup | Focused dependency, duplicate, reservation and error-overrun tests | Pass |
| Replan keeps original goal and verified state | Multi-replan receipts, goal/lineage rejection, revocation during call tests | Pass |
| No self-approval, writes, DB privilege or runtime registration | Strict emitter fields; only new files; protected modules absent from diff | Pass |
| Reproducible outputs and metric separation | EXAMPLES, raw requests/results, separate scorer, Fake semantic_quality_claim=false | Pass with limits below |
| Review before commit/push; no real vault | This independent review and closure, synthetic-only fixtures | Pass |

## Evidence limits

- FakeModel replay covers 20/20 required work categories in 12 public questions;
  this establishes contracts, not measured model understanding.
- Exactly three real Gemini generation calls: two PLANNED, one rejected with
  INVALID_INSPECTION. Category coverage 6/7, no accepted invalid endpoint binding
  or duplicate work. No retries, no additional live run after repairs.
- Live source hashes predate the review repairs. The initial runner did not save
  rejected third-call arguments; hash, usage and validation error are retained.
  Bounded public output capture was added and tested offline. No claim is made
  that the repaired implementation passed live acceptance.
- Endpoint error rate means typed/authorized/Workspace binding errors. Endpoint
  relevance, unnecessary investigation and generalization are not measured by
  the small category scorer. The owner smoke proposed extra discovery work.
- A related earlier regression combination passed 102 tests (36 then-current
  Planner tests plus 66 existing tests). Current standalone tests passed 40.
- Full offline collection retains four pre-existing missing-script errors in
  [offline.xml](evidence/offline.xml). A sandbox continuation also encountered
  local HTTP WinError 10013; [diagnostic](evidence/sandbox-diagnostic.json).
  Broader final offline status is recorded in DELIVERY; full green is not claimed.

## Architecture checkpoint

Model understanding remains inside a single provider turn; deterministic code
validates action structure, provenance, dependencies and budgets. Host owns
scope, current receipt verification and scheduling. No new runtime, dependency,
store or permission channel was introduced. Sources and observations remain
unmodified; evidence and the existing Reviewer are still required for conclusions.
