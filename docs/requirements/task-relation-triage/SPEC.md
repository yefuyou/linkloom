# Task relation triage

User-authorized independent experiment, 2026-10-21. Exact parent:
`13adb03c61df8c882a32ce71004b006f7a94fd9b` (PR #6). No original branch edits.

## Problem and selected boundary

Original M2 first predictions at `dd76a25` show SAME_TASK succeeded in H01,
H03 and H04. Finite directed revision grammar missed positive replacements;
H03 recognized only the denial and lost the conflict. H02 additionally missed
change bridges. This is not evidence of real-model quality. M2 stays 0/4 FAIL.

The smallest independently useful product repair is to surface already-returned
unconfirmed discovery pairs beside exact P4 results and model proposals. PR #6
displays proposals and generic blocked steps, but hides Discovery candidates
and makes users manually associate separate records with the checked relation.
Do not change proof grammar to fit exposed examples.

## Product contract

The normal Ask HTTP result and UI gain read-only relation checks, each bound to
two complete current ObservationRefs, its predicate, direction and originals:

* ESTABLISHED: an exact existing P4 investigation step is SUPPORTED and current;
  no conflicting/refuting or invalidated matching P4 record overrides it.
* POSSIBLE: an existing unconfirmed discovery candidate or evidence-valid
  positive model proposal suggests this pair; no authoritative conclusion.
* NOT_ESTABLISHED: no positive hint, a returned negative/conflicting P4 result,
  returned stale/retracted record, unsupported scope, or unresolved prerequisites. This does
  not prove that the tasks are unrelated.

Candidate similarity/reason codes never establish SAME_TASK or REVISES_PLAN.
SAME_TASK may match reversed endpoints; revisions retain exact ordered endpoints.
One relation's support cannot establish another predicate. Expose supporting,
counter and hint evidence separately with source version and line locations;
retain the exact blocked-step reasons and model uncertainty. Do not inherit
owners, dates, effective time or currentness, issue new execution receipts,
write a relation, or provide a confirmation button.

Only pairs actually returned by existing discovery, planning, proposals or P4
are shown; no all-pairs graph or new semantic inference. Disclose bounded or
truncated discovery and P4 reads. No pair means no returned check, never no relationship.
The existing query defaults to excluding historical relations. Disclose this
explicitly: a fresh run cannot report a past retraction omitted by that read;
it may show a new POSSIBLE hint but cannot revive the old proof. Saved runs
still block entirely when existing validation detects changed/retracted proof.
Existing source/authorization, Grounding, Reviewer, Ledger and cold-read checks
run before this projection. Failure returns no triage or old evidence. No new
authentication contract is introduced.

Independent review correction: the initial wording implied that all past
retractions could be diagnosed. The actual default read omits history, so that
claim was removed and the omission is now a visible capability limit. No P4,
lifecycle, source authorization or Reviewer acceptance rule was changed.

## Implementation and acceptance

1. Add a small UI projection over existing validated dictionaries; wire it into
   `task_review` after existing product validation. Leave Runtime and P1–P4 intact.
2. Render pair checks and exact source links in existing Ask, with bilingual
   status, direction and separate evidence origins. Keep original audit views.
3. Real localhost HTTP on synthetic SQLite sources demonstrates discovered-only
   pairs omitted by G, positive/negative/unknown proposals, exact P4 success,
   directed mismatch, blocked prerequisites, cold reads, evidence revocation
   and cross-workspace rejection. Product reads leave P4 bytes unchanged.
4. Add targeted regression/DOM checks, run existing product and safety gates;
   record before/after receipts separately from historical experiments.
5. Independent read-only review required by root AGENTS.md before completion.

No real Gemini or other provider, Gold/Seal/history edits, permission changes,
new runtime, business authorization, #6 edit/merge or generalization claim.
