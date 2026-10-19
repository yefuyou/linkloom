# Independent read-only M3-I review

Reviewer: `/root/m3i_readonly_review`, a separate read-only agent, on 2026-10-19.
The reviewer did not edit source, acceptance criteria or artifacts. The first
review turn stopped at an account usage limit after reporting findings; review
was resumed when the user asked to continue. The final explicit recommendation
was received before the implementation commit/push.

Verdict: **no remaining P1/P2 found; accept, commit and normally push within the
Fake/Replay Host integration boundary**. This does not accept real Provider
semantics or a complete intelligent business loop.

## Evidence examined

The reviewer read the SPEC, DELIVERY, final diff, `product-accepted` summary and
checks manifest, and the XML results: 265 offline, 26 HTTP, 15 product controls,
2 final HTTP, 3 publication-race and 1 invalid-citation test. The reviewer
independently executed the DOM test and `git diff --check`; both passed.
The counts overlap; they are not an aggregate unique-test total.

## Findings and closure

1. Generic Runtime planning could run again after G when an outer model exposed
   `propose_task_plan`. Fixed: task workflow forces template admission and rejects
   a generic `task_plan`. A trap-adapter product test proves zero second-planner
   calls.
2. The bounded-discovery absence caveat was retained only in G's plan, missing
   from top-level release gaps and the main UI. Fixed: plan gaps are retained in
   the manifest and shown in the primary unresolved-evidence section. Integration
   and DOM assertions cover the statement that missing recall proves no absence.
3. Additional closure checked: advanced `as_of` is blocked before stage calls;
   source checks surround projection under one product store lock; invalid
   semantic citations return top-level BLOCKED and only EVIDENCE_VALID proposal
   spans may extend original-source references.

The reviewer checked the cold-resume, E pressure, Fast Path and stale-source
boundaries against source and executable evidence. No new planner/executor,
model loop, semantic engine or authoritative context store was introduced.

## Residual trust boundary and unassessed capabilities

`TaskInvestigationModels` checks an execution-kind label and accepts Host-injected
adapter objects. `_AuditedAdapter` calls those objects directly. The no-network/
no-payment guarantee for the demonstrated path therefore depends on trusted
Host composition, not on classifying arbitrary injected Python objects. Current
production CLI supplies no task adapters; the repository's reachable test/runner
path supplies scripted Fake adapters. The reviewer did not classify this as a
P2 within the present boundary. Before default/live task configuration, use a
closed adapter capability/provenance boundary and also constrain the outer model.

NOT_EVALUATED remains explicit for real Provider semantics/business outcomes,
semantic-premise acceptance and dependent date/revision work, time/source-scoped
relation execution, product job discovery/F-G recovery after server restart,
real Vault use, CI/deployment, real-browser visual acceptance, and arbitrary
long Windows paths.

The coordinator records this independent recommendation; the implementation
does not self-approve semantic truth.
