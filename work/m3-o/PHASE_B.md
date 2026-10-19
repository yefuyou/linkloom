# Phase B checkpoint

Actual chain: `UnifiedAskService._task_request -> ProductInvestigationService.start
-> prepare_task_investigation -> F TaskObservationDiscoveryService -> G
InvestigationPlanner -> InvestigationPlan.ready_steps -> TaskRelationTools
registration -> existing Runtime/ToolRuntime/ToolExecutionLedger -> semantic
analysis -> task result projection -> independent Runtime Reviewer`.

| Operation | G can plan | Host accepts | Actual tool | Trusted receipt | Final capability |
|---|---|---|---|---|---|
| inspect | yes | one bound TaskObservation; unscoped | inspect_task_context | COMPLETED inspection, source-bound; never semantic SUPPORTED | newly bound ready G inspections execute in existing Runtime |
| discover | yes | bounded F source snapshot | F already scans once; no additional search tool | UNKNOWN bounded-source receipt | missing candidate is UNKNOWN; coverage gaps retained |
| relation | yes | ready, typed, no unsupported source/time scope | analyze_task_semantics | EVIDENCE_VALID raw proposal / Runtime tool receipt | no independent semantic support; dependent relations stay blocked |
| compare_dates | yes | structural plan only after SAME_TASK and REVISES_PLAN | query_task_relations reads existing P4 comparisons | existing verified P4 facts only | this product F/G does not materialize such relations; blocked |
| clarify | yes | visible plan/gap | none | none | no automatic conversation tool |

Production changes: `task_relation_question_planner.py` accepts optional Host
execution metadata (default API preserved). `ui/task_investigation.py` provides
the matrix to G and records `ready_tool_operations`, with explicit unsupported
time-inspect gaps. `tools/task_relations.py` binds simple inspection to planned
targets, advertises exact steps in its registered tool, emits COMPLETED receipts
only for actually returned verified observations, and revalidates durable
ToolExecutionRecord receipt digests/current source bindings for future G replans.
`for_run` preserves the plan. No new Runtime/executor or semantic authority.

RED: `b-red.xml` reproduces missing capability/binding and silent scoped-inspect
gap. GREEN: `b-green.xml` 92 passed. `b-chain.xml` 70 passed after receipt bridge,
including product F/G/Runtime/Reviewer, actual tool ledger readback, rejected
receipt promotion, stale source receipt invalidation, unresolved relation
premises, contradictory evidence, snapshot races, missing sources, context
compression/recovery and checkpoint resume without repeated tool actions.
Counts overlap; do not sum them as unique tests.

G's five planning kinds remain available; capability metadata explicitly states
execution limits. Simple Owner plan has F=2 existing source scans, G=1,
Semantic=0; no extra discover/replan. Inspection evidence remains UNCONFIRMED.
The helper consumes actual ledger records and the existing G result_verifier
can use those receipts; no new automatic product replan loop was introduced.
Current automatic G replan is still only the bounded F UNKNOWN receipt path.

SAME_TASK model support is a proposed verdict. Source citation validity proves
source integrity, not semantic truth. This product has no independent semantic
confirmation path writing approved P4 relations. Adding one, or automatically
unlocking revision/date execution, is OUT_OF_SCOPE. Do not lower that boundary
to eliminate PARTIAL/UNKNOWN. Proceed to C.

## Independent-review closure work

The reviewer found two P1s. Unsupported ready time/source scopes now make
Host preparation BLOCKED; the existing service does not start Runtime, and
TaskRelationTools refuses generic inspect/query before snapshot reads if a
Host scope block exists. Extra inspection source bindings now require current
workspace/registration/hash/version as well as exact artifact evidence, both
when issuing and revalidating receipts. Six first-review reproductions are
recorded in review-red.xml, followed by 17 passing review-green checks.
Direct executor denial and revoked-counter-source issuance checks were added
afterward and are included in the final affected regression. No semantic
premise was promoted.

Final independent closure: a mixed inspect/relation plan now restricts every
inspect read to the planned targets. `mixed-red.xml` reproduced arbitrary-anchor
acceptance; `mixed-green-final.xml` passed 34 Host/tool/product-chain tests.
Independent read-only re-review returned PASS, closing two P1 and four P2 across
A/B. No model proposal was promoted to trusted semantic authority.
