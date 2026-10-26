# Gap analysis — Task relationship review closure

Baseline 7720390a796662ff42c27b5eef6f8685e5155802, 2026-10-25. Branch codex/task-relation-review-closure. Original demo evidence is read-only.

## Exact chain and gaps

product.js:488–509 renderAskResult -> product-task-result.js display only -> server.py:206 POST /api/product/ask -> UnifiedAskService.ask -> ProductInvestigationService.start (investigation.py:187–215) -> ProductTaskSources/current source catalog -> prepare_task_investigation (task_investigation.py:252,343–369) -> Runtime/TaskRelationTools -> RelationQuery.

No Product relation write endpoint exists. Normal launcher supplies no Host task_relation_paths. task_result.py:97 deliberately reports human_review.available=false. TaskRelationStore.put_assessment (:145), append_event (:181), put_comparison (:215) are low-level append-only writes, not permissioned approval services. Their actor string is not identity. ACTIVE is capture lifecycle, never proof/publication.

RelationQuery._verify (:133–228) replays exact source/version/hash, observation payload, complete inventory, premises and lifecycle. Only ACTIVE + VERIFIED + SUPPORTED/current_supported/CURRENT_CONFIRMED is established source proof. assess_same_task (task_relation_validation.py:307), assess_plan_revision(new,old,...) (task_plan_revision.py:176) and compare_revision (task_revision_comparison.py:149) must remain unchanged. Dates require valid identity, directional revision, task-deadline roles, day precision and matching known boundary semantics.

Thus: reusable human relation review service absent; low-level immutable write/retract exists; independent human statement does not yet exist; Product needs a new Host-owned API and persistent composition, not buttons. Existing reviewed Decision Memory approval concerns another contract.

Fresh shipped offline demo in actual Chromium reproduced original P1: model SAME_TASK only; identity BLOCKED/NO_RELATION_RECORDS; revision BLOCKED/DEPENDENCY_NOT_SUPPORTED:identity; second deadline=null. New evidence: output/playwright/task-relation-review/baseline/{browser.json,p1-reproduced.png,browser.har,trace.zip}. No injected P4 state.

The original texts also do NOT satisfy finite proofs: vague continuation is not unique identity, second date role/boundary unknown. Human assertion cannot repair these facts.

## P2 recorded and excluded

UX-02 saved source shown FAILED; UX-03 offline onboarding/examples mismatch; UX-04 source navigation loses question/result. These are the three original report issues, not guesses. No fixes here.

## Compatibility

PR24 OPEN Draft d776a267086cd56789d812fe343f636ba9b9d9f0 changes tools/task_relations.py currentness. Do not copy/edit it.
PR25 OPEN Draft 3d4c05025c64aed0f51a15b6aa7d476febc972f8 rewrites product-task-result.js/product.js/product.html/CSS/UI tests. Keep task renderer and CSS unchanged; use separate review module plus narrow post-render hook/script include, verify against latest PR25 before editing. No cherry-pick of copy changes.

## Minimum implementation

Explicit localhost Host opt-in permits local unauthenticated self-asserted review records, with same-origin/current-view capability and exact Workspace/run/step/source bindings. Human statement is independent of canonical source proof and never business approval. Separate continuation re-evaluates original proofs; only proven source assessments may enter existing sidecar/RelationQuery. UNKNOWN/CONFLICT remain. Original demo can remain blocked; final verdict follows actual evidence.


## During-execution compatibility update

At worktree creation, fetched master was 7720390. During this task PR24 merged to f2ce6b82d635a7b78b17a6d100556c354f7a461a (2026-10-25T09:22:57Z). This branch has not merged master or copied that fix. PR25 remains OPEN Draft at 3d4c05025c64aed0f51a15b6aa7d476febc972f8. The narrow UI tracked diff was actually git-applied to a work/ shadow tree of PR25; both syntax checks and task_review/task_answer/unified_ask/stale_revalidation DOM checks passed. This is composition evidence, not PR25 browser acceptance.
