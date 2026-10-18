# M1 — TaskObservation

Boundary authorized by the 2026-10-18 user request. Implements only source-bound
PLAN/ACTION observations; no business task identity, impact edges, deadline
revision inference, UI, planner, live provider, or DecisionRecord changes.

Status: locally implemented; 190 offline tests passed and independent acceptance
re-review passed. No CI, live-provider, deployment or publication claim.

## Architecture and contracts

- Extend semantic extraction with a nullable task proposal: exact task text,
  verbatim description parts, optional owner, raw deadline text, project, vendor.
  Existing recorded claims remain replayable without that extension.
- Deterministic validation produces a TaskObservation sidecar on source
  candidates. It binds fields to exact source offsets. A first-person owner
  requires a canonical parsed speaker and an explicit responsibility cue.
- Deadlines are source text with a deadline role; retain date precision and
  unknown year/day rather than filling from event, document or ingestion time.
- Stable observation identity is scoped to workspace, immutable version and
  exact task-text location. It identifies an observation, never a business task.
- Reuse candidate persistence and SUPPORTING_ONLY; observations have no path
  into DecisionRecord. Versioned serialization preserves older payloads.
- A read-only Python query accepts workspace, source and/or exact project,
  and can include history. Current results require matching registered source
  version and hash. Unregistered/historical versions are explicitly labelled.
- Extraction failures remain existing rejected results, distinct from successful
  but incomplete observations and their always-unconfirmed relationship status.

## Acceptance

1. The original five lines yield two observations: 陈晨; interface access and
   integration by 2026-10-14; new-provider interface adjustment by 2026-10-18.
   No same-task, four-day delay, or exhaustive impact claim is emitted.
2. At least two independent samples cover Markdown/TXT, named and speaker owners,
   changed descriptions, missing fields/year, conflicting time roles and dates.
3. Important fields resolve to exact immutable source text; invented or misbound
   fields remain unknown with validation issues. No cross-segment field filling.
4. Serialization/replay and repeated capture are idempotent. New source versions
   retain prior observations, exclude them from current queries, and expose
   history without implying a plan revision.
5. Offline tests use independent temporary databases. Decision review,
   supplier supersession and provenance regression checks pass.

No installation, original database/process, frozen Gold, old evaluation ledger,
commit, push, or PR is authorized by this SPEC.
