---
date: 2026-02-14
status: approved
authority: decision_record
---
# Retrieval Upgrade — Final Decision

## Decision

The team approved a phased hybrid retrieval path: retain BM25 as the lexical
baseline and add semantic retrieval behind the measured rollout boundary.

## Rejected Alternative

Replacing BM25 with pure semantic retrieval was rejected for the first release.
The benchmark showed lower recall on the reviewed sample and the team would
lose a useful exact-term diagnostic baseline.

## Rollout Boundary

This decision approves the approach, not unrestricted rollout. Embedding cache
security and release-readiness actions remain open.

## Approval

The retrieval review group approved this decision on 2026-02-14. A later status
update is authoritative for action completion.
