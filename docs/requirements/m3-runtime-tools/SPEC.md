# M3-B Runtime task relation tools

Approved boundary: the user's M3-B task, based on
`26392432dcef70692461e6f2f50bc1bd5528f69a`. This SPEC records that boundary;
it does not reopen the historical V1 release gate.

## Architecture and contracts

Extend RuntimeEngine -> RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
-> SingleAgentModelLoop -> ToolRuntime. Add an explicit `task_relations`
workflow, without changing intent routing or existing workflow capabilities.
Trusted composition injects a workspace-bound snapshot provider and optional
existing P4 sidecar path. Model arguments contain business IDs only, never paths.

`inspect_task_context(workspace_id, anchor_ids?, max_candidates?)` revalidates
bounded M1 observations and source versions and calls M2 discover_candidates.
Return ObservationRef, original extracted fields, provenance, rejected rows,
missing context, discovery budgets and truncation. Candidates remain unconfirmed.

`query_task_relations(workspace_id, observation_id?, include_history?, as_of?)`
uses P4 RelationQuery, including diagnostics, lifecycle, evidence, plans and date
comparisons. Missing sidecar returns NO_VERIFIED_RELATIONS without creating it.
Retractions and stale evidence cannot be restored by a read.

The model chooses tools and ends with receipt IDs. Runtime projects these receipts
into `task-relations-result/v1`; Reviewer rechecks their live source/lifecycle
views. There is no free-text relation assertion path. Existing Ledger,
Checkpoint, model/provider/token/step budgets and error categories remain owners.

## Scope and non-goals

Only synthetic fixtures or explicitly authorized read snapshots. No new Agent,
Coordinator, Router, semantic classifier, SemanticProposal model, relation store,
Decision Memory writes, UI changes, Gold/scorer changes or live Provider runs.
LLM semantic consumption is explicitly NOT_CONNECTED, with M2 candidates and
version-bound observations as the future input boundary. P4 replays existing
P1/P2/P3 rules; UNKNOWN is a capability/evidence gap, not business refutation.

The simpler alternative is direct Python tool calls, but it cannot establish the
requested ModelLoop/Ledger/Checkpoint integration. No second runtime is needed.
Future pressure: A's semantic proposal validator/replay contract and large P4
sidecar query budgets; neither authorizes automatic persistence here.

## Acceptance (fixed before implementation)

1. FakeModel executes inspect -> reads observation -> query -> reads query ->
   structured Final, through the existing durable Runtime and Reviewer.
2. Test wrong workspace, stale source, revoked relation, insufficient evidence,
   tool budget exhaustion, absent Provider and illegal arguments.
3. Independently verify ask/connect/team_decision regression and unchanged
   default capabilities. Model sees no SQLite path or raw filesystem tool.
4. No source/sidecar changes or database creation on reads; Runtime audit outputs
   are separate permitted writes. Record truncation and bounded-input failure.
5. Preserve P4 statuses/history/date comparison semantics, and prove no verified
   records is meaningful. Distinguish wiring, rule-derived output and unconnected
   LLM semantics in evidence and delivery.
6. Deliver structured test output, actual trace/checkpoint/review, reproduction
   commands and independent read-only review. Commit/push only the requested
   branch after acceptance; verify remote SHA. No merge or PR.

## Implementation and check plan

1. Contract tests then read-only tools; focused tests.
2. Minimal workflow/capability/runtime composition and receipt projection;
   durable integration tests including failures and regressions.
3. Offline synthetic evidence runner, full offline checks, independent review,
   repairs where required, DELIVERY.md, requested commit and push.

Architecture checkpoint: task-specific validation lives beside the task tools;
shared runtime retains orchestration and durability. No new dependency.
