# Independent acceptance review

Reviewer: independent `review_revalidation` agent, read-only review and tests.
Date: 2026-10-21. No reviewer edits, commits or remote changes.

**Code / real HTTP / SQLite: PASS. Full product acceptance: BLOCKED_BROWSER_ACCEPTANCE.**

Final independent checks: 70 tests passed (new feature plus temporal memory,
reconciliation and semantic materialization); new DOM and JavaScript syntax
passed; git diff --check passed. Counts overlap the main delivery checks.

The reviewer found and verified fixes for:

- Returning old source text could resurrect the old approval and trigger the
  one-active-decision SQLite constraint. Completed revalidation targets now
  remain STALE through rollback and restart. Real HTTP reproduction confirmed
  no effective decision is invented when the new evidence is also withdrawn.
- Candidate inspection could finish after a workspace change and overwrite the
  new view. The new flow now checks its view/workspace token; DOM regression
  confirms isolation.
- A failure after durable approval left an unrecoverable APPROVED candidate.
  The exact target-bound approval can now finish materialization via a distinct
  read-only recorded-approval action. Real HTTP fault tests confirmed that a
  different stale target and a generic ordinary approval cannot use this path.

Original decisions, dates, evidence and receipts remain intact. No Runtime,
M3-P, frozen evaluation, Provider default or separate approval framework was
found in the change set. Effective-time resolution uses the existing review
form and must finish before approval; subject and relation must identify the
same canonical slot.

The reviewer explicitly did not accept DOM tests as live-browser evidence.
Actual before/after screenshots remain missing because the browser cannot
reach the isolated local server. The full milestone is not declared complete.
