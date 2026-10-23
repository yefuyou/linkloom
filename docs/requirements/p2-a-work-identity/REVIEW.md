# Independent read-only review

Reviewer: separate `identity_reviewer` agent, `gpt-5.6-luna`, reasoning `max`.
Review boundary: P2-A SPEC and production diff against
`33390e2eeaad71d215e279b2f724cae672ea4f80`. No files changed by Reviewer.

Verdict: **P1 = 0, P2 = 0; no blocking findings in the production diff**.

Reviewed contracts:

- TaskProposal remains untrusted model excerpts, without an identity-bearing
  candidate index. Its wire fields and prompts are unchanged.
- TaskFieldEvidence provides exact Unicode offsets. v2 requires source-text,
  unique work occurrence and containment in the raw fragment. `verify` replays
  against the canonical message and original artifact/version.
- TaskObservation selects strict identity verification by v1/v2 prefix. New v2
  identity ignores candidate order, raw window, timestamps and field roles/values.
  Same-work conflicting extraction retains the same ID and is quarantined;
  independent work in the same raw range survives.
- ObservationRef shape and complete payload hash, excluding recorded_at, are
  unchanged. Saved v1 payloads/refs replay exactly. No v1/v2 alias or migration
  exists; new endpoints cannot inherit old P4 records.
- DiscoveryGroup remains a model proposal's binding, not an accepted business
  identity. Its proposal-signature ID is intentionally not a stable work ID.

Evidence examined: baseline v1 fixture; BEFORE/AFTER HTTP receipts; 98 related
contract passes; 14 independent identity tests; baseline-generated v1 database
reopened by new code with HTTP 200 and unchanged task_review/ObservationRef.
The host also completed all 7 new HTTP regressions. The Reviewer's own sandbox
socket retry was blocked by local permissions; host HTTP results are retained
in evidence/after-http.xml and evidence/after-http.json.

Nonblocking notes:

- Changing description/binding order can change DiscoveryGroup's signature ID;
  future callers must not promote it to external business identity.
- Direct new field-bound construction defaults to v2. Legacy Discovery callers
  explicitly select v1; future legacy callers must preserve that selection.

This is a local independent review, not CI verification or a PR merge approval.
