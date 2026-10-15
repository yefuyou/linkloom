# Human-Governed Feedback Memory

Approved boundary: the user's feedback-loop request; base 70052cd. No Provider
requests, real vaults, Decision Memory writes, prompt changes or model training.

Add one structured PROJECT memory type: decision-term/v1, short literal term to
an existing reviewed relation, with exact workspace metadata.scope_key. Reuse
MemoryLifecycle and its append-only MemoryStore. Pending feedback cannot affect
discovery. Explicit local human confirmation activates it; revoke/expire removes
it on the next query. Existing generic memory supports superseding; this product
slice replaces mappings by revoke followed by propose/approve. No auto approval.
The reviewed first version accepts one continuous Chinese term or Latin
identifier (2–40 characters); multi-word phrases and sentences are unsupported.

Implementation: a small feedback service validates terms and scoped lifecycle
actions, reconstructs an immutable resolver snapshot per query, and returns
versioned memory provenance separately from business evidence. Unified Ask still
uses its trusted slots, recover_decision and original Validator/Reviewer. HTTP
and a small Ask details panel expose pending, approved, rejected and revoked
states. The existing local UI's actor is an explicit human assertion, not a new
authentication/role system. One local server owns the memory writer.

Acceptance fixed before implementation: reuse the synthetic discovery fixture;
query `我们现在采用哪家供方？`, Atlas workspace, as_of 2026-10-10, same approved
decisions. BEFORE=NO_MATCH (observed against base); PENDING=NO_MATCH;
APPROVED=MATCHED/Wrenwell/DETERMINISTIC with original evidence/receipt;
REVOKED=NO_MATCH. Restart preserves only valid scoped state. Other Workspace
unchanged, conflicting active targets refused, expiry removes effect, changed
source cannot retain stale authoritative conclusions. No unknown-expression
substring expansion, canonical relation override, feedback as evidence or calls
to an external model. Freeze this question; do not edit fixed vocabulary to pass.

Sequence: define HTTP acceptance contracts; implement scoped lifecycle adapter;
connect immutable discovery input; expose minimal UI; execute offline and browser
flows; independent boundary review; commit/push this separate branch only.
