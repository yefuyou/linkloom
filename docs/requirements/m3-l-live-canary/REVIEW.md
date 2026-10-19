# Independent read-only M3-L review

Reviewer: `/root/m3l_review`, separate read-only agent, 2026-10-19.
Scope: M3-L implementation against `df95bfb15b4af462fec062c218d04f0d84774e00`,
the user's approved boundary, SPEC, DELIVERY, actual source and offline evidence.
The Reviewer did not edit implementation, acceptance criteria or frozen data.
Review was interrupted by account usage limits and resumed after the user said
to continue; the interruption was not treated as approval.

Final recommendation, received before commit/push:

> P1/P2 已闭合，建议 APPROVE；未发现剩余 P1/P2。

This approves the bounded offline Host/transport admission only. It does not
authorize or accept real Provider semantics, cloud billing, business completion,
PR creation, master merge, Vault access or live execution.

## Findings and repairs

1. **P2: Initial F/G source validation did not stop the campaign.** The first
   ask could be BLOCKED with zero SDK sends while the ledger stayed READY;
   restoring the snapshot allowed another ask. Reproduced independently and by
   a failing main-agent regression. Fixed with a source-change callback covering
   preparation validation and initial artifact-identity failure. Snapshot-read
   exceptions also stop. The second case cannot send after the failure.
2. **P2: First admitted source versions were not durably bound.** A new Host
   could previously accept a different source-version map under the same
   campaign/manifest. Fixed by atomic `sources_bound` ledger events, checked on
   subsequent binding and reservation. Legacy requests without a binding cannot
   acquire a retrospective authorization. New Host/restart regression passes.
3. **Related P2: Source IDs were checked as a set, permitting swapped content.**
   Fixed by checking each deterministic source ID against its frozen text;
   swapped ID/text pairs stop the campaign. The new regression failed before
   repair and passes afterwards.

The Reviewer rechecked each repair and the known LIVE outer-adapter rejection.
No semantic relation is promoted; the unconfirmed revision premise remains
blocked. Existing Runtime, tools, Ledger, Checkpoint, E and Reviewer are reused.

## Independently executed evidence

- Source-boundary regressions: **3/3 passed**, `work/l-review-src`.
- Outer adapter impersonation guard: **1/1 passed**, `work/l-review-outer`.
- C1/C2/C3 normal-chain serial rerun: **3/3 passed**, `work/l-review-cases`.
- Broader focused review run: **44 passed**. Two case polls returned
  INVESTIGATING under disk contention using the old short polling window;
  their independent serial rerun passed. The HTTP attempt was blocked by
  WinError 10013 in the local socket environment; it is not a passing HTTP
  result. The main-agent localhost gate has separate **17 passed** evidence.

Main-agent evidence also retains the three failing pre-repair tests, later
passing source tests, the real product HTTP path with mocked SDK, all-stage
budget traces, concurrency/restart/unknown-usage injections and prior failures.
Counts overlap. No test result is added to claim a unique aggregate total.

## Remaining trust and capability limits

Trusted local Host code, grant files and durable storage remain the authority.
This is not a sandbox against an administrator replacing code or deleting all
campaign state. FAKE/REPLAY labels do not prove arbitrary Python objects cannot
network; the offline gates deny network independently. No real SDK transport
request or cloud invoice was verified. Application reservation is not a cloud
bill hard limit. Semantic scoring, independent semantic confirmation and real
business task completion remain NOT_EVALUATED; F/G preparation recovery is not
implemented. The separate human LIVE approval gate remains closed.

Reflection: independent review found source-authority gaps that passing normal
wiring tests missed; durable binding and failure-stop regressions now protect
those boundaries without introducing another execution loop.
