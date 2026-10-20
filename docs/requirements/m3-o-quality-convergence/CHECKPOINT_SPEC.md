# Approved M3-O staged execution boundary

Authority: user's 2026-10-20 checkpoint patch request. Base 29ba35fe519bd238abc8d989043f7bd398afbd55.
Implement and validate offline, independent read-only review, commit/push only
codex/linkloom-m3o-ab-checkpoint. No real Provider/countTokens/grant or production ledger.

Extend the existing A/B executor and SQLite budget. FIRST_PAIR executes only
the first source A/B and returns. VERIFY_FIRST_PAIR has no sending capability:
revalidate exact persisted requests, raw/normalized responses, Usage, schema,
Host evidence, artifacts and exactly two COMPLETE ledger identities, then
persist an audited gate. CONTINUE_REMAINING requires this gate and the same
manifest/output/ledger, never resends earlier identities. Every subsequent
pair receives the same integrity validation before proceeding.

Use an OS-released exclusive campaign file lock across stages/processes,
and the existing transactional reserve-before-send ledger. Persist phase
starts/outcomes and artifact fingerprints in the same database. A crashed or
failed sending phase is terminal; complete trustworthy pairs may be read-only
verified, but no ambiguous or missing evidence permits further sends. A crash
after gate persistence and before sending continuation permits later continuation.

ab-v1 bytes stay unchanged. ab-v2 copies the same data/config/rubric/labels;
only execution identity/control policy/implementation binding changes. The old
SHA remains the v1 reproduction entry; its grant never authorizes v2. No change
to models, prompts, schema, sampling, budget, scoring, F/G/runtime or user data.

Acceptance: actual staged LIVE code with official SDK MockTransport, 2/0/24
sends and 26 unique ledger rows; failures/crashes/concurrency/expiry/tampering
stop without replay; old Mock/Replay and selected L/N/O contracts pass; v1
byte equality and v2 implementation/data/schema binding proven. Reviewer closes
in-scope P1/P2. READY means offline technical admission only, requiring new human
confirmation of the final manifest before any real LIVE run.
