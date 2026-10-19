# M3-K: Investigation Planner quality and dependency deduplication

Authorized by the user's M3-K execution request. Base: remote G HEAD
`167a46694048e9ae6dd26879c2c6986fed8c0a15`; branch
`codex/linkloom-m3-k-planner-quality`. This focused follow-up supersedes old
release/process gates only within this boundary. No additional approval needed
for its internal implementation loop, independent review, commit and normal push.

## Diagnosis and architecture

Read the three saved G Gemini requests/results, rejected-output provenance,
validator, typed signature and tests before implementation. The third response's
arguments were NOT saved. Its exact malformed field cannot be reconstructed from
a response hash. Distinguish that unresolved historical cause from reproducible
validator defects and schema limitations; never label an invented replay as live.

Keep the existing single-turn planner. LLM chooses semantic decomposition and
states a concrete goal-related evidence gap. Host owns workspace, authorized
handles, source versions, capability constraints, DAG, dependency receipts,
replan lineage, original goal and cumulative reservations. No new executor,
coordinator, agent runtime, provider requests or dependencies.

Extend the existing typed operation identity with bound evidence spans, temporal
scope and recursive typed premises (including verified result digest/outcome).
Question/gap prose and step/handle aliases do not define identity. Endpoint refs
already bind versions; source-only operations and versionless ProjectRefs also
need bound evidence. Unknown entity labels remain search requirements, never
endpoints; distinct requirements must not collapse into one search.

Expose ready versus pending work using Host receipts. Completed work is not
ready again; missing, refuted, conflicting or unknown relation premises must not
authorize dependent operations. Ready relation projection must not silently
discard temporal scope. Preserve all complete step contracts for Host tools.

The existing Gemini adapter supports neither oneOf nor anyOf. Keep a compatible
flat schema with explicit operation descriptions and a generated typed capability
inventory; retain strict Host validation and provide precise public diagnostics.
Do not expand the provider adapter or weaken validation to accommodate outputs.

## Fixed acceptance criteria

1. Diagnose all saved live outputs; record exact known/unknown causes. Separate
   illegal output, underexpressive schema and false rejection. Preserve original
   live evidence, historical Gold, frozen datasets and previous scores.
2. Reject illegal Inspection with step ID and exact structural violations;
   support only one supplied ObservationRef and supported field. Source handles
   never become endpoints. Test source/type/workspace/capability boundaries.
3. Paraphrased same typed work deduplicates, including aliases and symmetric
   predicates. Different evidence versions, temporal ranges, unknown entities
   or dependency premises do not merge. Repeated work without a new explicit
   evidence gap is rejected and counted.
4. Discovery requires an explicit gap. Measure excessive/unjustified proposed
   work, even in rejected plans. Unknown entities may discover or clarify;
   candidate misses never prove nonexistence. Semantic goal relevance remains
   an LLM/evaluator responsibility, not a keyword classifier.
5. Keep DAG/budget constraints; ready work respects verified dependencies and
   negative/conflict outcomes. Replans preserve original goal, receipt lineage,
   completed work, current source bindings and cumulative reservations.
6. Deliver separate category coverage, valid-plan rate, excess steps,
   false/missed deduplication probes and budget/cost checks. Offline FakeModel
   proves contracts only. New paid calls and additional model allowance: zero.
7. Relevant regression tests, bounded full-offline attempt, independent read-only
   review and repairs before commit/push. No PR, merge, deployment, real vault,
   F/E source changes, or edits in another Worktree.

## Implementation and verification order

1. Add failing typed-identity and invalid-inspection/quality probes; implement
   scoped identities, compatible schema guidance and diagnostic validation.
2. Add readiness, receipt-outcome and replan source-binding regression tests;
   implement only existing planner/model seams.
3. Run offline scripted replay and a separate M3-K evaluator, with immutable G
   live metrics as a historical input. Run relevant and full offline checks.
4. Independent review, required repairs, delivery evidence, commit, normal push,
   and remote SHA readback. No model retry or live re-evaluation.
