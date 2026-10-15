# Semantic Ingestion SQLite Persistence and Migration

**Status:** Shared Semantic Ingestion and Agent-result lifecycle, schema v2, 2026-10-07.

This is the implementation design for the approved Step 3 boundary. It extends
the existing `TemporalDecisionStore`; it does not introduce a second
authoritative store.

## Current Store

`src/linkloom/decision_memory/store.py` owns one SQLite connection and the
following tables:

| Table | Current responsibility |
|---|---|
| `decision` | Authoritative DecisionRecord fields, valid time, lifecycle state, and self-referencing `supersedes_id`. |
| `decision_evidence` | Ordered workspace-validated source evidence references and optional hashes. |
| `action` | Action rows linked to an authoritative decision. |
| `action_evidence` | Evidence references for action rows. |
| `decision_candidate` | Existing generic DecisionMemory write candidate and outcome state. |
| `semantic_candidate` | Immutable source-specific candidate payload plus shared workflow state and source kind. |
| `semantic_candidate_event` | Append-only capture, policy, review, and materialization audit trail. |
| `semantic_materialization_receipt` | Immutable authorization and materialization outcome, including Agent action dispositions. |

`SourceReferenceRegistry` is an in-memory workspace-scoped catalog. The
connection enables SQLite foreign keys. Existing foreign keys are mostly
record-ID based; workspace isolation is additionally enforced by the store's
scoped reads and source validation. Existing indexes cover workspace/slot,
validity, source episode, action source, candidate workspace/slot/state, and
structured subject/relation lookup. A partial unique index allows one open
ACTIVE record for a workspace/slot.

Initialization creates tables idempotently, adds legacy columns, rebuilds the
active uniqueness index, and applies explicit SQLite schema migrations.
Materialization groups the prior-record update, new DecisionRecord, evidence,
eligible ActionRecords, receipt, workflow update, and audit event in one
SQLite transaction. Candidate capture and review are separately durable.

## Schema Version and Migration

Use SQLite `PRAGMA user_version`:

- `0` means an existing unversioned store or a new store with the base schema.
- `1` means the additive shared candidate lifecycle and relation-slot migration are installed.
- `2` adds explicit source-kind and Agent action-disposition fields.
- A version greater than `2` is rejected by this code version.

The `0 -> 1` migration runs under `BEGIN IMMEDIATE` and is idempotent. It adds
only tables, indexes, triggers, and relation-slot aliases; it does not delete
or rebuild existing DecisionRecord or evidence rows. It assigns deterministic
relation-scoped slot keys to existing records and generic candidates only when
both structured subject and relation are already present. Their old
`subject_key` is retained in `decision_slot_alias`, so existing
`get_current/get_history/get_as_of` callers remain supported when the alias is
unambiguous. Unstructured rows keep their original key. If two ACTIVE rows
would collide on the new workspace/slot key, migration aborts and rolls back
the `0 -> 1` transaction. Ambiguous legacy aliases require structured lookup;
they are never resolved by selecting an arbitrary relation.

SQLite DDL and the backfill execute in the same transaction. On failure, the
new version is not recorded and no Semantic Ingestion rows are written. The
existing base DecisionRecord data remains readable. No destructive down
migration is provided. The `1 -> 2` migration adds source-kind columns to the
candidate, event, and receipt tables, plus receipt action dispositions. Existing
rows default to `SOURCE_INGESTION`; both migrations preserve existing
DecisionRecord data.

## Shared Candidate Lifecycle Tables

### `semantic_candidate`

Stores an immutable, source-specific typed payload using its versioned JSON
contract. `candidate_source_kind` distinguishes `SOURCE_INGESTION` from
`AGENT_RESULT`; source candidates retain the `CandidateDecisionFact` contract,
and Agent candidates retain the complete `AgentMemoryCandidate` and original
`TeamDecisionResult` snapshot. Neither payload is flattened into the other.
Scalar columns support queries and integrity checks:

- `candidate_id`, `workspace_id`;
- `artifact_id`, `artifact_version_id`, `content_sha256`;
- `extraction_fingerprint`, `candidate_fingerprint`, `payload_version`;
- `candidate_source_kind`, `validation_state`, stable validation reason codes,
  `workflow_state`;
- canonical payload JSON and creation/update timestamps.

Both payload types use canonical JSON, not an untyped free-form extraction
blob. Source candidates retain their EvidenceSpan and versioned
ExtractionReceipt. Agent candidates retain their TeamDecisionResult snapshot,
evidence bindings, resolution decisions, actions, and uncertainty. Neither
copies full raw artifact text. The store rejects reuse of a candidate ID with
a different payload/fingerprint. Database triggers reject updates to immutable
identity/payload columns.

Indexes cover workspace + workflow state + creation time, workspace + source
artifact/version, and candidate fingerprint for replay detection.

### `semantic_candidate_event`

Append-only workflow/audit history, including policy decisions, human review,
source-version observations, conflicts, correction links, rejection, and
materialization outcomes. Each event stores workspace/candidate identity,
event kind, actor type and identity, event time, candidate fingerprint,
stable reason codes, and bounded canonical metadata. Candidate payloads and
event rows are not updated or deleted. Current workflow state is stored on
`semantic_candidate` for efficient listing; state transition and audit event
are committed together, and the event stream provides the history.

### `semantic_materialization_receipt`

One durable receipt per candidate records authorization type, human or policy
identity/version, reason, candidate/policy fingerprints, DecisionRecord ID,
supersession/correction target, effective time, materialization time, result
state, deterministic materialization key, and optional human-review resolution
fingerprint. A duplicate candidate may have
its own `DUPLICATE` receipt pointing to the already existing DecisionRecord;
replaying the same candidate returns the original receipt. A reused
DecisionRecord keeps its original provenance while the new candidate remains
auditable in its own immutable payload and workflow events. The receipt records
candidate source kind and action dispositions. It is immutable and has a
workspace-scoped foreign key to the candidate and a foreign key to the
DecisionRecord.

The materialization key hashes workspace, relation-scoped slot, normalized
value, effective time, and evidence identity. Source-ingestion candidates keep
their artifact-version/reference key; Agent candidates include all decision
evidence bindings and source kind. The key prevents a second candidate from
creating the same authoritative fact twice while preserving a receipt for the
replay.

## Relation-Scoped Slot Identity

`subject_key` remains the DecisionRecord slot key and the existing partial
unique constraint remains in force. Structured records use the same stable
versioned hash produced by Step 2 from normalized `[subject, canonical
relation]`. This prevents two relations for one subject from sharing a slot.
The migration records old-key aliases before backfilling structured rows.
Store APIs resolve a legacy alias only when it maps to one slot; otherwise
callers must use structured `lookup(subject, relation, as_of=...)`.

## Lifecycle and Review

Candidate states are current-state projections: `PENDING_REVIEW`, `APPROVED`,
`POLICY_AUTHORIZED`, `MATERIALIZED`, `DUPLICATE`, `SUPPORTING_ONLY`,
`CONFLICT`, `REJECTED`, `STALE`, and `INVALIDATED`. Transitions append an
event. Review approval/rejection binds to the exact immutable candidate
fingerprint and workspace. A new artifact content version or extraction
fingerprint is a distinct candidate and cannot inherit an old approval.

When deterministic validation leaves a structured field unresolved, a human
may append an adjudication overlay as part of approval. The immutable candidate
payload is not rewritten. A source-ingestion overlay may resolve the subject,
confirm a relation already marked canonical, or set an explicit valid-time
start. An Agent subject overlay must select a canonical workspace subject ID
from the candidate's captured registry snapshot; it cannot create an identity.
Neither path can add or rewrite a relation, replace the value, or change
evidence. The overlay hash is included in the materialization receipt. Without
an explicit resolution, the unresolved candidate stays blocked.

An Agent `TeamDecisionResult` approval is not memory authorization. Agent
candidates enter the shared review workflow, and the default policy never
auto-authorizes them because the result has no calibrated memory-confidence
contract. Every decision evidence reference must remain workspace-bound and
registered. Only action proposals with grounded description, owner, deadline
when present, and bound evidence become ActionRecords; other proposals remain
in the immutable candidate and receipt disposition list.

The default policy has no calibrated confidence profile and therefore does
not auto-authorize. It keeps eligible low-risk candidates reviewable. A
versioned calibration profile may enable `AUTO_ALLOW` only when every
field-specific threshold and deterministic gate passes. Conflicts,
supersession/replacement, future-effective records, and uncertain semantics
require explicit human review. Unsupported semantic classes remain supporting
evidence and are never materialized. Invalid workspace or provenance is a hard
rejection.

## Temporal Validity and Supersession

Approved future-effective records are stored in `decision` immediately with
their exact `valid_from`; they do not become current early. When a successor
is materialized, the existing record is retained with `valid_to` equal to the
successor's `valid_from`, and `supersedes_id` explicitly links the successor
to its predecessor. Current and as-of reads evaluate the half-open interval
`valid_from <= as_of < valid_to` (or no end), so the old value remains current
before the future boundary and the new value is current at/after it. This uses
existing valid-time fields and needs no scheduler or midnight row-flip.

The existing open-ACTIVE partial unique index remains safe: the prior record
becomes SUPERSEDED with a finite `valid_to`, while the successor is the one
open-ended ACTIVE row. Query-time current lookup includes a SUPERSEDED row
when its validity interval contains the requested time. Only one successor per
slot may be scheduled at a time; a second overlapping future plan is blocked
for review.

## Corrections

- **Business evolution:** append a new DecisionRecord with `supersedes_id` and
  a valid-time boundary; retain both records and source evidence.
- **Extraction correction:** append a correction event, invalidate the
  erroneous candidate/derived record without creating a business
  supersession edge, then review/materialize the corrected candidate. The
  receipt links the correction target.
- **Source correction:** retain the old artifact version identity and create a
  distinct new version/candidate. Record the version change; revalidation via
  `DecisionReconciler` can mark dependent records `NEEDS_REVALIDATION`. Never
  rewrite the old candidate's extraction evidence or silently bind its
  approval to the new version.

## Atomic Transaction Boundary

`TemporalDecisionStore` owns one `BEGIN IMMEDIATE` transaction that:

1. reloads the workspace-scoped candidate and verifies its immutable payload
   fingerprint, latest workflow state, and registered source/version hashes;
2. verifies the recorded policy decision or fingerprint-bound human approval;
3. checks duplicate identity, relation slot, current record, chronology, and
   explicit replacement/review authorization;
4. inserts or reuses the DecisionRecord, writes its evidence, inserts only
   eligible Agent ActionRecords and their evidence, updates the prior record's
   temporal interval/state when superseding, and writes the receipt;
5. updates candidate workflow state and appends audit events.

Any exception rolls back the whole materialization. Candidate capture and
review are separately committed durable states, so a crash before
materialization remains reviewable and replayable. No accepted candidate is
re-sent to an extraction provider as part of Step 3.
