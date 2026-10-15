# Bounded multi-hop history and heldout freeze

## Scope

This record covers the read-only audit and independent heldout freeze for the
bounded multi-hop slice. The fixture is synthetic and contains no credentials,
provider output, Ledger, or real workspace data.

## Historical T03/T04 audit

The available checkout documentation was inspected read-only before freezing
the heldout input. `docs/TEMPORAL_AGENT_INTEGRATION.md` explicitly states that
historical Provider ledgers, Gold files, traces, and paid-run outputs are not
part of the source branch. The transport integration record also describes
historical T03/T04 identities as non-reusable and treats interrupted transport,
unknown Provider execution state, and incomplete traces as unknown transport
evidence.

No exact historical T03/T04 CASES, GOLD values, or complete trace bundle was
available in the new Worktree's committed docs or in the inspected local
`work/` artifacts. They are therefore recorded as `UNAVAILABLE_FOR_ANSWER`
and are not used as wrong-answer evidence. A disconnected or interrupted run
cannot be scored as a semantic wrong answer.

The following behaviors are established by current source contracts and this
new task, not recovered T03/T04 questions or historical scoring rules. The
original per-case intent and scoring cannot be confirmed without the absent
bundle; these behaviors define the new independent offline acceptance only:

- two-hop project → owner → direct leader and three-hop alias → project →
  owner → direct leader;
- cross-file verified reads with dependent entities copied from the preceding
  read;
- exact typed relation direction, source/evidence identity, and workspace
  binding;
- historical `As of` snapshots selecting the queried valid-time owner and
  leader rather than a later snapshot;
- ambiguity, conflicting sources, missing next hops, irrelevant retrieval,
  unsupported questions, and out-of-workspace evidence fail closed while
  preserving any verified prefix;
- bounded read/search/round behavior with provenance visible in the resulting
  trace.

## Heldout freeze

`tests/fixtures/bounded_multihop/heldout.json` is frozen independently from
the development fixture. It contains ten cases tagged A through J and uses
only the existing ClaimGrounding source forms for `alias_of_project`,
`owner_of_project`, and `reports_to`. Each case keeps its expected values and
required sources in the evaluator-side fixture; the Runtime input must expose
documents and the query only, never the expected answer.

Expected outcome semantics are `complete`, `partial`, or `refused`. `partial`
retains verified prefix values and a non-success reason; `refused` returns no
unverified value. Transport/provider interruption remains unavailable for
answer scoring and does not alter these semantic expectations.

The freeze is immutable for evaluation. If a case fails, diagnose the Runtime
or evidence contract; do not change its query, source text, expected values,
or outcome to make the score pass.
