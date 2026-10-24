# M2 real-failure semantic regression

Boundary authorized by the 2026-10-24 user request: audit latest master, preserve
the completed M2 experiment, reproduce offline, repair only demonstrated product
gaps. Baseline: `614c4164edd256d2cc63edf1bb9f66befe25cefb`.

## Contract and architecture

The research experiment's primary-type JSON is not the M3 typed proposal API.
Keep original wire requests, responses, proposals and the pre-model analyst
reference byte-for-byte. The reference is provisional, not human Gold. A test
bridge may build source-bound observations from original utterances and adapt
supported predicates explicitly; it must never claim a fresh model run or map
NONE to REFUTED, REVOKED to REVISES_PLAN, or AFFECTS to AFFECTED_BY silently.

Production ownership stays unchanged: M1 owns observations, M3 validates source
quotes and emits unconfirmed proposals, P4 replays finite relation proofs and
lifecycle, the tool layer projects their current state. No new semantic engine,
relation regex, model prompt, dependency, persistence API or business authority.

## Acceptance

1. Map R01–R09 to code paths and distinguish protected behavior, confirmed gaps,
   unsupported vocabulary and N/E. Preserve failed model interpretations.
2. Offline tests demonstrate exact quotes cannot grant semantic acceptance,
   ESTABLISHED, owner/completion authority or business memory writes.
3. UNKNOWN remains insufficient evidence; explicit verified negative evidence
   remains predicate-specific REFUTED, never universal NONE.
4. Verify revision direction, identity and effective-time gates; revoked access
   and stale versions cannot supply current positive or negative conclusions.
5. Reproduce any proposed production repair as a failing test first. Reuse
   existing lifecycle and source-validity fields for the smallest repair.
6. Retain N/E for unsupported business revocation and natural unresolved pure
   cross-meeting conflict; controlled mutations are contract tests only.
7. No paid calls, real vault, historical Gold/ledger edits, commits, pushes, PR
   mutations or changes to other worktrees.

## Implementation / checks

Read-only audit → immutable local fixture import → record gap map and RED
evidence → minimal proven repair → focused M2/M3 contracts and existing offline
CI selections → independent read-only review → delivery. Use repository pytest
and DOM checks. Fixture integrity is checked against copied SHA-256 entries from
the original seal. New tests disable network sockets; no Provider is constructed.

The possible repair is limited to current P4 verdict projection in
`tools/task_relations.py`; preserve historical audit rows. Broaden only if another
failure is reproduced within this approved boundary.

## Subsequent submission authorization — 2026-10-24

After independent acceptance, the user authorized committing this minimal repair
and its necessary regression fixtures/tests, an ordinary push to its independent
branch, a Draft PR against master, and updates to that PR's description. This
supersedes only the original Git-action restriction in acceptance item 7. No
feature expansion, real Provider call, frozen-data edit, force push, history
rewrite, other-PR mutation or master merge is authorized.

The submission may exclude unnecessary raw transport metadata. The unchanged
original archive remains local; a separate distribution archive retains exact
sealed bytes for the necessary request/assembled-response/proposal/reference
files, excluding SDK streams with opaque Provider thought signatures. Tests bind
those assembled responses to the same proposals and frozen references. No answer,
reference or original experiment is edited; prior raw-stream reconstruction is
reported as earlier local evidence, not a current CI check.
