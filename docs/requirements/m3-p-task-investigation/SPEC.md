# M3-P: Task investigation closure

Authorized boundary: the user's complete M3-P request, 2026-10-20. This
supersedes historical release freezes only for this isolated offline work.

## Product contract

Ask must show what each original task observation explicitly says, preserve
both dates, expose semantic proposals with supporting/counter/missing evidence,
and explain which dependent steps cannot proceed. A model proposal, an exact
quote, or Runtime Reviewer acceptance never creates business authority.

Reuse TaskObservation, ObservationRef, EvidenceQuote, RelationAssessment,
RelationQuery and the existing Runtime/ToolRuntime/Ledger. No new relation
model, authoritative store, semantic classifier or execution framework.

The Host may inject an existing per-workspace P4 sidecar through local service
composition. The model and HTTP request cannot choose its path. Only current
P4 replay with exact predicate/endpoints can satisfy relation prerequisites.
Unsupported source/time scope must stop the affected step, without widening.
Comparison requires both SAME_TASK and directed REVISES_PLAN and preserves
P4's date/currentness distinctions. Product reads never create P4 records.

P4 lifecycle events have actor text but no authenticated human confirmation
capability. Therefore this milestone adds no human approve/reject endpoint.
The review view explains that permission gap and points to the two originals.

Persist only run identity metadata alongside existing Runtime artifacts.
Cold reads use existing Checkpoint/result/ledger and revalidate current product
sources and P4. Saved-result viewing is not automatic F/G session recovery.

## Acceptance

1. Existing Product HTTP Ask + Fake stages reproduces continuity, conflict,
   missing candidates and replan; concise before evidence retained.
2. Accepted outputs expose source-bound observations and reviewable existing
   proposal objects; invalid proposals never gain trust or displayed quotes.
3. Missing trust yields useful PARTIAL/UNKNOWN: original facts, unknowns,
   completed tools, exact blocked steps and actionable next steps.
4. An existing valid P4 SAME_TASK/revision/comparison can be read through actual
   Runtime tools and satisfy only matching dependent steps. Negative, unknown,
   conflict, wrong endpoints, stale and revoked records cannot unlock them.
5. Same-name distinct tasks do not merge or inherit fields. Updating/removing
   a source prevents old source text/trace from publication.
6. A fresh process can read saved review results with current validation;
   it makes no model call and does not claim to resume a full session.
7. Existing Fast Path and relevant contracts pass offline; baseline failures
   are separately recorded. Independent read-only review has no open P1/P2.
8. Only the new branch is committed/pushed; forbidden M3-O files, manifests,
   LIVE ledgers and original worktree remain untouched. Provider/countTokens=0.

## Implementation and checks

First improve existing product projection/UI, then bind trusted P4 reads to
G steps, then saved-result reads. Add behavioral HTTP/Runtime tests early.
Use public synthetic fixtures and scripted adapters, never Gold as runtime
authority. Retain HTTP examples, commands and test results in this directory
and work/. No CI, browser, LIVE or real-model semantic claim without evidence.
