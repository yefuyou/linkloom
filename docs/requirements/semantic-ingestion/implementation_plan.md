# Semantic Ingestion — Implementation Plan

**Status:** IMPLEMENTED IN THE CURRENT V1 WORKTREE. The bounded browser and
real-Gemini release acceptance is recorded in
[the product roadmap](../../PRODUCT_ROADMAP.md) (2026-10-08). V1 feature work is
frozen; this plan does not imply a commit, CI run, or deployment. Detailed run
receipts remain local under `work/` and are excluded from the public package.

The original 2026-10-07 user task explicitly authorized prerequisites and Step 3. The
product contract/roadmap reconciliation is recorded in `SPEC.md`,
`DEV_SPEC.md`, both README files, and `docs/PRODUCT_ROADMAP.md`. SQLite schema,
migration, transaction, and lifecycle decisions are reviewed in
`docs/architecture/SEMANTIC_INGESTION_SQLITE_MIGRATION.md`. This records the
task authorization; it does not claim an external human or reviewer approval.

Steps 1–3 are implemented in the current release candidate. The approved Step
3 scope is closed; remaining work is release curation, packaging, and
documentation only.

The plan has three steps. Each step has its own offline acceptance boundary and can be evaluated with synthetic fixtures. Do not modify the V2 planner, traversal, ContextAssembler, Reader, benchmark data, or scoring protocol.

## Step 1 — Timestamped artifact and provenance boundary

**Scope**

- Define the V1 RawArtifact and immutable artifact-version identity.
- Implement deterministic parsing of timestamped speaker/message text.
- Emit stable segment IDs, source hashes, exact Unicode span offsets, and verified evidence refs.
- Reject unsupported formats and preserve parse failures with stable codes.
- Do not call a provider or write DecisionRecord values.

**Independent acceptance**

- Synthetic inputs produce deterministic segments and identical IDs across repeated parses.
- Every emitted evidence ref resolves to the exact source hash and quote/span.
- Missing/ambiguous timestamps, malformed lines, changed content, and unsupported source types produce explicit failures and no memory candidate.
- No external provider request and no source file mutation occurs.

## Step 2 — Provider-neutral candidate extraction and validation

**Scope**

- Define the strict candidate schema and claim/relation/temporal status enums.
- Add a provider-neutral extraction interface using the existing model adapter contract, with explicit model, prompt/schema fingerprints, bounded retries, response receipt, usage, latency, and request/response hashes.
- Validate structured output locally; bind every claim to a verified Step 1 span; classify decisions versus proposals, questions, rejected options, plans, actions, opinions, and speculation.
- Map relations against a frozen catalog/alias version; never auto-expand it.
- Keep every result a candidate or supporting-evidence item. No store mutation.

**Independent acceptance**

- Fake Provider fixtures cover the eight acceptance scenario outputs, malformed schema, missing/invalid source spans, unknown relation/entity, uncertain temporal values, conflicting claims, and provider failure.
- Replaying a persisted accepted extraction returns the same candidate set without a second provider call.
- No authoritative memory changes; incomplete usage remains unknown and cost safety is checked before each provider request.

## Step 3 — Risk policy, idempotent materialization, and retrieval proof

**Scope**

- Extend materialization authorization to distinguish explicit human batch approval from a versioned low-risk auto-policy. Never spoof approved_by.
- Persist deterministic policy outcomes as append-only events; let human review attest an ambiguous subject or confirm an existing canonical relation through a fingerprint-bound overlay without rewriting extracted evidence.
- Make decision-slot identity relation-scoped while preserving existing records and unambiguous legacy-key lookup behavior.
- Store approved future-effective DecisionRecords with their exact valid-time boundary; derive current/historical truth from valid-time queries without a scheduler.
- Persist immutable candidate/outcome/provenance linkage, append-only review/policy events, and receipts in the existing SQLite store; apply source-change, duplicate, correction, and reconciliation rules.
- Reuse the existing DecisionMaterializer, write policy, source registry, store, and structured lookup. Use retrieval only as a read-only proof surface; do not modify planner, traversal, ContextAssembler, or Reader.
- Keep auto-policy authorization disabled by default until a versioned calibration profile is available; all other eligible candidates require an explicit human approval.

**Independent acceptance**

- Fake Provider end-to-end runs verify the current/historical replacement scenario, future-effective query validity, conflict hold, proposal exclusion, idempotent re-ingestion, correction audit, provenance integrity, and stable failure codes.
- No source change silently deletes a record; unresolved provenance prevents authoritative activation.
- Policy authorization and all lifecycle transitions are inspectable after a partial run and safe to replay.
- Existing V2 retrieval tests and a frozen synthetic retrieval contract pass; no clean-held-out or broad accuracy claim is made from this fixture.

## Approved Worker Gate

- **Human/task authorization:** satisfied by the 2026-10-07 user authorization for prerequisite completion and Step 3; no external approver is represented.
- **Product contract/roadmap:** reconciled to Team Decision Recovery; Scanner retained as historical infrastructure.
- **SQLite persistence/migration:** reviewed in `docs/architecture/SEMANTIC_INGESTION_SQLITE_MIGRATION.md`, including legacy lookup and collision behavior.
- **Future-effective semantics:** query-time validity intervals use existing DecisionRecord fields; no scheduler is required.
- **Authorization/transaction contract:** human and versioned policy authorization are distinct; candidate, record, evidence, event, receipt, and workflow update are atomic.
- **Acceptance evidence:** the 2026-10-08 release report records focused offline regressions and the separately authorized live-browser acceptance. No additional live Provider call is part of precommit verification.
- **Scope boundary:** no real connectors, real workspace writeback, RHELM-specific behavior, benchmark tuning, or changes to V2 retrieval algorithms.
