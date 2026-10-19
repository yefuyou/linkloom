# M3-G: LLM-driven investigation planning

Approved boundary: the user's M3-G request, baseline
`31dde91f0e9b6015abc5754e995692a2f1db89ec`, isolated branch
`codex/linkloom-m3-question-planning`. This records that authorization and
supersedes historical release freezes only for this independent capability.

## Behavior and architecture

Natural-language question + authorized, version-verified SemanticContext +
optional M2 DiscoveryResult + explicit PlanningLimits -> one guarded
ModelProviderAdapter turn -> strict handle/type/DAG/budget validation ->
InvestigationPlan. The existing TaskPlan's question/dependency idea is reused;
its runtime grammar and file are unchanged. No new agent or executor is needed.

The model selects inspect, discover, relation, compare_dates or clarify steps.
Relation steps bind existing ObservationRef / ProjectRef / ChangeRef handles
and convert directly to SemanticQuery. Sources use separate allowed-span handles.
Missing objects stay discovery requirements; there are no synthetic future refs.
Discovery-dependent relation queries require a later explicit replan after Host
validation and handle binding. A discovery operation may group several search
questions for the same entity kind/anchors. Candidate recall never defines the
endpoint universe and absence never means refutation.

Host owns the original goal, workspace, reference bindings, plan identity,
budgets, result verification and replan history. Model owns semantic
decomposition only. Plans contain no verdict, date delta, approval, DB permission,
write capability or publishable conclusion. Evidence and Reviewer remain needed.
No calls to Runtime, UnifiedAsk, Coordinator, task_plan.py, P4 or task extraction.
No new dependencies. New source files live beside semantic ingestion.

Replanning accepts a previous plan, explicit gaps and Host-verified step receipts.
The caller must revalidate receipts through an injected verifier. Host preserves
the original goal, exact receipts and completed steps; model supplies only new
steps. Repeated work is rejected by typed execution signature across history.
Replans share a cumulative call/input/output reservation ceiling, with no retry.

Simpler alternative: keyword rules or fixed query templates. These do not cover
unfamiliar paraphrases/multi-intent decomposition; rules are used only to validate
typed structure. Future pressure: large snapshots require explicit partitioning;
semantic equivalence across different discovery anchors still needs evaluation.

## Acceptance criteria (fixed before implementation)

1. One structured emitter call; no executor. FakeModel tests prove host-only
   endpoints, workspace isolation, source freshness, exact-request authorization,
   strict unknown-field rejection and failure without retry.
2. Missing entities generate discover/clarify steps; candidate omissions and
   truncated recall stay uncertainty. No candidate-only endpoint filtering.
3. Public synthetic inputs cover owner, project membership, vendor impact,
   identity, revision/deadline, conflict, same-name ambiguity, workspace collision,
   compound intent, vague question, injection and incomplete recall. No Gold,
   case ID, filename or expected plan enters model input or plan generation.
4. Support two/three-hop dependencies. Enforce subgoal/relation/fanout/hop,
   input/output token and total call budgets. Deduplicate equivalent typed work
   independent of question phrasing; refuse invalid DAGs or missing premises.
5. Replan preserves original goal and verified results, rejects invalid lineage,
   forged/stale receipts, duplicate completed work and exhausted total budgets.
6. Deliver readable question/output comparisons and reproducible JSON with
   coverage, wrong endpoints, duplicates, missed decomposition and call usage.
   FakeModel results establish contracts only; real semantic quality is reported
   separately. Real smoke: at most three serial requests, no retries, explicit
   token/call caps; unavailable credentials/provider is an explicit limitation.
7. Independent read-only review before commit/push. Relevant regression checks
   and full offline suite; preserve baseline failures. No PR/merge, real vault,
   protected-file edits or runtime registration.

## Implementation plan

1. Write contract tests (RED), then bounded request/plan models and validation.
2. Add guarded single-turn planner, semantic query conversion and replanning;
   focused FakeModel failure-path tests after each increment.
3. Add public inputs, offline contract replay and separate metric evaluation,
   plus capped real-provider smoke if credentials are available.
4. Relevant and full offline tests, independent review, required repairs,
   DELIVERY.md / REVIEW.md, commit and normal push, remote SHA readback; stop.

No real vault is read or mutated. Output artifacts are synthetic and local.
