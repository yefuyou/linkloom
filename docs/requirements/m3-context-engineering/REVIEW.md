# Independent read-only review

Reviewer: separate `context_readonly_review` subagent, `gpt-5.6-luna`, max reasoning.
No file edits, live model calls, Git mutations or real-vault operations by the
Reviewer. The Reviewer inspected source, tests, experiment and delivery claims.

## First pass: Request changes

1. **P1, required:** Assembler rejected only exact `False` source flags, allowing
   malformed `"false"` strings through permission/currentness gates. A direct
   Reviewer probe reproduced model-visible evidence with these string flags.
2. **P2, required:** Group count and evidence members were bounded, but group
   subgoal arrays were not. One group could produce 1,000 coverage records under
   `max_candidates=1`.
3. **P2, residual concern:** Recovery bounded ref count but not source or quote
   sizes. Add limits or require a hard loader bound before Runtime exposure.

Reviewer found the Gold separation structurally sound and synthetic claims
proportionate: source topic/link/polarity tags are caller hints, and scores are
not Agent/model accuracy. Initial `new-tests.xml`: 29 passed. Reviewer also ran
no-write focused probes and tests without modifying the Worktree.

## Worker remediation

- Present source flags must be exact booleans; malformed values are dropped as
  `INVALID_PROVENANCE`. Absent metadata remains backward-compatible with the
  existing trusted retriever contract.
- Group inputs require immutable tuples, boolean conflict and bounded nonempty
  IDs. Total nested subgoal occurrences and distinct goal IDs are bounded before
  selection, in addition to the existing candidate/group/member limits.
- Recovery now bounds each source to 4 MiB, each quote to 64 KiB and total output
  quotes to 256 KiB by default; limits are configurable. Oversize results are
  `incomplete` with `SOURCE_TOO_LARGE`, `QUOTE_TOO_LARGE` or `RECOVERY_BUDGET`.
  Source resource activity must be exact `True`.
- Red-first regression: **20 failed, 21 passed** before these fixes (18 malformed
  source-flag combinations, nested hint limits, restoration byte limits).

## Targeted re-review

Second pass confirmed all three original findings resolved, then requested two
further fixes:

1. **P2:** Removing a conflicting duplicate quote did not release its recovery
   byte budget, incorrectly preventing a later valid quote from being restored.
2. **P2:** Candidate processing sorted/materialized the entire incoming sequence
   before enforcing its inspection limit.

Both were reproduced by two failing tests before remediation. Recovery now
recomputes used bytes after removal. Assembler inspects/sorts only the configured
prefix of the caller-ranked sequence and reports the tail by bounded count/range,
with exact missing declared-subgoal IDs. A lazy million-item pool regression
asserts that the tail is never iterated or materialized.

Final affected result after these fixes: **174 passed, 2 baseline-reproduced
failures**, including **all 51 new tests passed**.

Third targeted pass confirmed both second-pass findings resolved, with no
additional substantive issue in the remediations. The Reviewer verified the
bounded lazy-pool behavior, released recovery budget, final affected evidence and
clean diff check.

**Final qualified verdict: Accept scoped M3-C implementation.** The two known
baseline failures and incomplete broad-suite exploration remain documented;
no broad-suite success is inferred from these completed affected tests.
