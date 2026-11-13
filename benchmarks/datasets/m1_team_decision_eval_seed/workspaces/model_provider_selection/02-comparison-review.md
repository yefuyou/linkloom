---
date: 2026-01-15
status: reviewed
authority: technical_review
---
# Provider Comparison Review

## Comparison

The review compared Borealis B, Aster A, and Cedar C against the Atlas Lantern
pilot requirements. Prototype response quality was not treated as sufficient
for adoption.

## Data Residency

Aster A documented processing in the synthetic `eu-north` region used by the
pilot. Borealis B required a residency exception, and Cedar C did not provide
an auditable region commitment in the review packet.

## Operations

Aster A had a named operational escalation path and a bounded usage report.
Borealis B would have required the small team to own an additional failover
runbook. Cedar C lacked the streaming behavior required by the adapter.

## Recommendation

The review recommended Aster A for the pilot, subject to regional evidence and
an on-call runbook. Borealis B and Cedar C remained comparison candidates until
the approval record was written.
