# M3-R Implementation Plan

1. **Audit / RED** — capture r8 plan, semantic_call_limit, allowed tools, ledger, final projection, and old semantic-rule entry points. Add a failing contract test proving source-bound relation steps should produce proposal queries without changing P4 `to_semantic_query` behavior.
2. **P0 GREEN** — add the smallest proposal-only query projection and use it in TaskPreparation for semantic candidate setup. Keep `query_task_relations` source-scoped blocking and P4 lifecycle checks unchanged.
3. **Evidence projection** — inspect existing SemanticAnalysis/Reviewer/UI projections. Add only missing candidate/evidence status fields or answer-first presentation required by real output.
4. **Offline and security regression** — run focused Planner, semantic context, Runtime, Reviewer, source identity, permission, and lifecycle tests. Add independent synthetic cases with fixed expectations before any live call.
5. **LIVE** — use the existing low Product configuration and a fresh public synthetic workspace; budget conservatively from the cumulative M3-Q ledger. Verify real `analyze_task_semantics`, exact citations, reviewer, refresh and cold read.
6. **Independent review** — review diff and receipts for semantic overreach, P4 writes, regex/hardcoded cases, budget/permission leaks, and P1/P2. Commit and push only after review.
