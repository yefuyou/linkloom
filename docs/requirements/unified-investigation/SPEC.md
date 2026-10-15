# SPEC: Unified multi-hop investigation integration

Status: Implemented within the user-approved 2026-10-15 boundary; local offline
and browser acceptance plus independent review completed (see DELIVERY.md).
Base: `5be2e14559a1fbec3f37052d38be97fadfbf5335`.

## Problem and goal

`POST /api/product/ask` currently reaches the existing temporal investigation
path, while the bounded source-relation slice is only demonstrated by the
standalone integration driver. Product Ask must recognize the already-supported
typed two/three-hop plans and execute them through the real Runtime, while
keeping source relations separate from reviewed business decisions.

The product route accepts only the deterministic `TaskPlan/v2` grammar: project
→ owner → direct leader, alias → project → owner → direct leader, and the one
generalized Chinese form `{ENTITY} 项目负责人的直属领导是谁` (entity is not
hard-coded). Every dependent search must use an entity copied from a preceding
`read_verified_note`.

## Scope and contracts

- `UnifiedAskService` first attempts `plan_relation_investigation(query)`. A
  supported plan is recorded as `route.code=SOURCE_RELATION_MULTIHOP`, retains
  the existing workspace and per-request provider permission, and otherwise
  leaves Temporal, Discovery, Feedback, and legacy routes unchanged.
- `ProductInvestigationService` passes the immutable plan to
  `RuntimeRunBackend`; the backend passes `RunRequest(workflow="team_decision",
  task_plan=..., planning_mode="template", final_protocol="team-decision-result/v1")`
  to the existing `RuntimeEngine`. The product path must not copy the
  `tests/integration/run_bounded_multihop.py` controller.
- The internal `_source_relations` result is projected in the actual product
  field `source_investigation`; it is not a new authority store or Final
  schema. It may expose `complete`, `partial`, `conflict`, `unsupported`, or
  `UNCERTAIN`, hop bindings, Reviewer disposition, and existing evidence
  identities. A relation claim is shown only when its cited passage was read
  back as verified and the Runtime Reviewer accepted the final; it never
  creates or approves Temporal Decision Memory.
- Source runs explicitly disable project-memory recall, Decision Memory tool
  registration and Agent-result candidate capture. They do not allocate a
  separate Memory database. Existing Temporal defaults remain unchanged.
- `conflict` means incompatible verified reads with the same normalized
  subject, relation, and valid-time scope, from distinct evidence identities;
  dependent conflict checks also bind the verified parent owner/project and
  reuse the existing owner-source/date/project filtering. Different subjects,
  relations, dates, projects, or unverified/search-only hits remain
  `partial`/`insufficient`, not conflicting. This fixes HOLD-G.
- Source investigation owns an explicit 12-round `RunRequest` cap. Keep the
  existing Temporal 8-round cap, retrieval identity limit 12, tool limit 20,
  and 5000 ms controller deadline; do not enlarge global Runtime limits.

## Non-goals and safety

No arbitrary natural-language planner, new Runtime framework/database, copied
controller, Decision Memory write, real vault, real Provider request, new
heldout dataset, or generalized evaluation harness. Existing Temporal,
Discovery, Feedback, legacy APIs, and per-request `allow_external_provider`
authorization remain authoritative. Workspace polling/race checks must reject
cross-workspace reads and stale UI responses; UNCERTAIN never renders a draft
as a final answer.

## Fixed acceptance

Use the fixed user Case1–Case10 acceptance set: (1) reviewed supplier
`Wrenwell` is deterministic with zero model calls; (2) Temporal reason is
`UNKNOWN`; (3) `Orion` two-hop source investigation is complete; (4) the
existing English three-hop grammar is complete; (5) same-day direct-leader
evidence is `conflict`; (6) missing downstream evidence is partial with the
verified prefix; (7) HOLD-H remains unsupported; (8) Feedback Memory exposes
its four lifecycle states without becoming business evidence; (9) Provider
`UNCERTAIN` has no Final and no retry; (10) browser workspace/query race
cannot publish a stale response. Complete results contain verified source
identities; partial/conflict preserve the prefix and null the main decision
(an owner prefix is never presented as the final leader); unsupported does not
run Runtime. The answer-first UI renders all five source-relation states
without changing the existing decision presentation.

Retain the historical first blind heldout result of 8/10. Any replay of that
exposed set is a regression report, not a new blind evaluation; do not add
generalized heldout cases or a new harness. Acceptance evidence records zero
real Provider requests and zero real-vault access, plus focused legacy
Temporal/Discovery/Feedback regressions.

## Implementation plan

See [`implementation_plan.md`](implementation_plan.md). The Planner does not
approve its own draft; Worker implementation and independent review remain
separate.

## Planner reflection

The existing bounded slice already owns typed planning, Runtime evidence, and
Reviewer checks; the missing boundary is product request plumbing and safe UI
projection. Preserve the frozen fixtures and historical 8/10 record, and keep
all authority with verified reads plus the existing Reviewer.
