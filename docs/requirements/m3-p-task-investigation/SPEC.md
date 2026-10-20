# M3-P mainline integration

Authorized by the user's complete integration request, 2026-10-21. Baseline:
master `ca2e775ce17581f8b69b2e1f9daf813a1230fb1c`; transplant source:
`dd76a25bd9e70544c8e0534538b694015928fea1`. This boundary supersedes the old
release freeze only for this integration. Historical experiment acceptance is
not changed or inherited.

## Product behavior

Unified Ask exposes separate source-bound task observations, owner/deadline
fields and exact evidence. Model SAME_TASK/REVISES_PLAN proposals remain
unconfirmed. Same names never authorize field inheritance. Unknown date roles
remain UNKNOWN, including the public 2029-06-20/2029-06-24 example.

Existing finite P1/P2/P3 proofs may be replayed read-only through P4 only after
workspace, exact endpoints/predicate, evidence, lifecycle and premises pass.
Date comparison requires valid SAME_TASK and directed REVISES_PLAN. The public
API-4 example may yield four calendar days while retaining
`plan_currentness=NOT_ESTABLISHED`. No authenticated business approval contract
exists here; no confirmation control or relation write API is added.

## Architecture and authority

Product source snapshots -> bounded F observation discovery -> typed G plan ->
existing RuntimeEngine/ModelLoop/ToolRuntime/Ledger -> Grounding/Reviewer ->
existing Ask projection. E Working Context is a bounded hook in that same loop.
There is no second runtime, planner or authoritative relation database.

The Host owns source/workspace identity, provider permissions and optional local
P4 sidecar composition. HTTP and model input cannot select a database path or
grant themselves permission. Default product startup keeps task investigation
unavailable until explicitly composed. A clearly labeled deterministic offline
demo may expose the complete UI/HTTP path without external model requests.

Saved completed task results reuse Runtime artifacts and are validated against
current workspace, source identity/version, current Evidence Catalog membership,
hashes, P4 lifecycle, Tool Ledger and Reviewer. Retained source text cannot
recreate revoked EvidenceSpans. Failed revalidation returns no old quotes,
relations or trace. Cold reads never invoke models or resume a full F/G session.

Useful PARTIAL requires actual grounded receipts and Reviewer acceptance.
Permission/identity failures, exhausted execution, no progress and unknown
provider outcomes cannot be represented as successful partial investigations.

## Scope and acceptance

1. Real Product HTTP plus deterministic FakeModel verifies independent
   observations, distinct same-name tasks, unknown date roles and cited PARTIAL.
2. Missing/invalid/wrong-endpoint/retracted/stale P4 cannot unlock dependents;
   matching valid finite proofs can unlock only their exact prerequisites.
3. Source update/delete or evidence-only revocation blocks hot and cold result
   reads without stale content. Cold read makes zero model calls.
4. Existing Reviewed State, Decision Memory, Source Relations and Recovery pass
   relevant regressions, Core CI, Legacy Gate, DOM/JS and frozen-byte checks.
5. Independent read-only review closes P1/P2 before Draft PR delivery.
6. Preserve original failures, including M2 0/4. No LIVE/provider/countTokens,
   frozen experiment edits, history merge, automatic Ready/Merge or release.

Profile B is assessed separately; no global default or new A/B evaluation is
included. Real-provider semantic acceptance and authenticated task relation
approval remain outside this delivery.

## Implementation plan

Select the minimal F/G/P4 and E dependency closure; preserve master Recovery.
Integrate observation/relation presentation, explicit capability state and saved
result checks in the current Host. Verify progressively with focused contracts,
real localhost HTTP, browser observation and existing merge gates. Record new
evidence and limitations under this directory, then commit/push a fresh branch
and open a Draft PR against master. Stop at Draft PR.
