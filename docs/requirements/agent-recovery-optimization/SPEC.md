# Recovery mainline integration

The current user request authorizes integration of existing Recovery behavior
from `cd668f36300e5bfae7e8c654a54359dc40627d33` onto master
`c5099ce242abcaad4a785710d435e0c582394b43`. This is an incremental port of
`49736a7..cd668f3`, not a new Runtime or a new evaluation project.

## Existing behavior to integrate

- Assess search progress using new evidence and the current workspace/TaskPlan.
- When reachable missing subgoals are exhausted, stop tools and request a null
  Typed Final that retains independently verified, cited facts and names gaps.
- Preserve distinct matching passages from one file through retrieval/context;
  keep top-k/context caps, source identity, Verified Read and claim validation.
- Terminate identity, permission and unrecoverable failures; distinguish partial
  evidence from failed/uncertain execution in Unified Ask.

Inputs remain synthetic/source-scoped searches, the existing Ledger, verified
reads and Typed Final. Outputs use existing result/projection contracts. No
real vault, Provider request, automatic retry, new dependency or data migration.
Grounding, Reviewer, authority and budget checks retain their existing roles.

## Acceptance and implementation sequence

1. Port necessary candidate tests first; record current master behavior.
2. Port the eight product-source deltas and necessary regressions only. Exclude
   historical Gold, freeze manifests, scores, Provider logs and experiment runners.
3. Verify complete answers; reviewed partials with explicit gaps; safe no-progress
   termination; denial/stale-source/invalid-evidence rejection; cold recovery
   without repeated completed calls or Ledger bypass. Use existing FakeModel.
4. Run focused Recovery tests, Core, Legacy Historical, Frozen Ledger and UI/DOM
   checks. Add Recovery tests to the existing offline workflow selections.
5. Obtain independent read-only review and actual final-SHA CI evidence. Deliver
   one Draft PR to master; Ready and merge require separate user approval.

Adaptive, M2 and M3 feature integration, broad refactoring, LIVE evaluation,
editing frozen evidence or rewriting branch history are outside this boundary.
