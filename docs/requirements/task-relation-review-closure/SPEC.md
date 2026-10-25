# SPEC: Task relationship local review and safe continuation

Status: implementation boundary supplied by user's 2026-10-25 request. This executable scope does not grant business authority or approve stronger identity/semantic overrides.

## Scope

Open exact task pair/support/counter evidence -> durable bounded local human statement -> reload/history -> separate source-proof recheck -> revision/date continuation only when existing contracts pass.

No Provider/Key handling, real vault, frozen/Gold changes, PR24 currentness copy, P2/broad UI cleanup, SSO/RBAC, proof-parser changes, business approval, push/PR/merge/automatic commit.

MODEL_PROPOSED = existing proposal; EVIDENCE_VERIFIED = exact checked quotes; HUMAN_REVIEWED = independent unauthenticated local statement; ESTABLISHED = only existing RelationQuery current_supported/CURRENT_CONFIRMED after canonical replay. Business state remains unconfirmed. Manual review never overrides UNKNOWN/CONFLICT.

## API

GET /api/product/task-review?workspace_id=W&run_id=R returns available, mode, business_approval=false, source_snapshot_hash, revision, capability, steps (step_id,predicate,subject/object,evidence/proof/blockers), history and authority limits. Verify current Workspace and accepted saved run; changed source must not disclose old quotes.

POST /api/product/task-review accepts workspace_id, run_id, step_id, action=REVIEW|WITHDRAW|CONTINUE, request_id, capability, expected_revision, actor_label, note and optional outcome=CHECKED|UNSURE|DISAGREE. Reject arbitrary assessment/verdict/source/path/endpoint substitution and actor permission claims. Return durable receipt/history and fresh source-verified continuation or explicit blockers. Identical request-ID retry is idempotent, changed retry/stale revision is 409, permission failure 403, foreign Workspace reveals no data.

--allow-local-task-review is explicit Host opt-in. Local Host/same-origin + issued view capability authorize local records only. Actor label is self-asserted, not authenticated. Disabled capability fails closed. External model permissions unchanged. Continuation source proofs can only be computed by existing assess_same_task/assess_plan_revision/compare_revision and independently re-read via RelationQuery; human statements never become source evidence.

## Persistence and concurrency

Dedicated append-only SQLite review sidecar, fixed Host paths, unique (workspace,request_id), payload hashes, exact run/pair/direction/source binding and revision compare-and-swap. Serialize against Product source/workspace mutations and use SQLite transaction boundaries across connections. Existing TaskRelationStore captures immutable source proofs only. Local withdrawal affects a statement, not formal relation lifecycle. Preserve pending/failure audit for incomplete separate proof capture; never issue false successful receipts. Revalidate catalog authorization, version/hash and P4 lifecycle before mutations and reads.

## Acceptance

A1 Original P1 re-created in Chromium before code, originals unchanged.
A2 User opens pair, both observations, versions, support/counter evidence and uncertainty.
A3 Opted-in local operation yields persisted receipt readable after refresh/restart with current-source checks.
A4 Statement/withdrawal cannot establish unproved relation, identity or business approval.
A5 Independently satisfied canonical source proofs allow revision/date continuation; missing identity/revision/date role/boundary stays UNKNOWN/BLOCKED with null delta. Preseeded P4 fixtures do not count as user-created proof.
A6 Source update/revocation invalidates preview/conclusions and hides stale source details.
A7 Identical retry idempotent, altered retry/stale revision/concurrent mutation rejected or serialized, audit retained.
A8 Workspace/run/permission/source hash/pair/direction/lifecycle verified before write, no cross-workspace leaks.
A9 Actual Chromium screenshot/HTTP/HAR/Console/Trace plus focused backend/HTTP/UI regression; independent read-only review.
A10 Repeat original P1. DEMO_P1_RESOLVED only for genuine end-to-end user behavior; otherwise DEMO_P1_BLOCKED even if useful review records work.

Original demo UNKNOWN cannot be fixed by assertion. If A5/A10 require grammar/identity expansion, do not weaken criteria: report BLOCKED. See implementation_plan.md.


## Architecture review clarifications (before acceptance)

- This service captures only independently SUPPORTED source assessments in P4; all other deterministic verdicts stay diagnostic/review metadata. Existing Store can retain untrusted assessments, but this product API does not use row existence/ACTIVE as proof.
- Cross-database continuation is NOT one ACID transaction. A minimal PENDING -> original proof writer -> RelationQuery readback -> COMMITTED/FAILED audit protocol is required. If proof write succeeds but receipt completion fails, preserve pending/failure and exact request binding for idempotent recovery; no false success. Test this failure point.
- Safe projection must omit old observation text/support/counter quotes after source version changes OR evidence-catalog-only revoke. Current source hash alone is insufficient; revalidate catalog identities as well.
- Host capability is issued, hashed server-side, expires, binds Workspace/run/step/pair/direction/source/catalog/review revision. It is local permission, not authenticated identity. Exact request retry has identical fingerprint; altered request is 409.
- Withdrawal appends a local statement event only. It must not call relation RETRACTED, change proof bytes/lifecycle, or remove independent source support.
- No human override of REFUTED/NOT_ASSESSABLE/stale/missing as well as UNKNOWN/CONFLICT.
- Original ambiguous Qingzhu demo and positive explicit-source fixture are separate acceptance cases. The latter may adapt public D04 source reference to the actual source ID assigned by a real UI import; record the exact new source input. Do not change any frozen input or preseed proof state.
