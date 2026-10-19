# M3-C Context Engineering V1

Authorized boundary: the user's M3-C request, baseline
`26392432dcef70692461e6f2f50bc1bd5528f69a`. Product direction remains in
`docs/PRODUCT_ROADMAP.md`.

## Architecture and contracts

1. Extend ContextAssembler in place. Keep evidence/memory/experience limits and
   existing workspace, scope and provenance gates. Scan a bounded candidate pool,
   count only selected evidence, and select declared evidence groups atomically.
   Groups describe observable task-plan requirements, never evaluation Gold.
   Report every inspected rejection, omitted-tail count/range and every
   incompletely covered declared subgoal. Consume a bounded prefix of the
   caller-ranked input; do not scan/materialize an arbitrarily large tail.
2. ContextPolicy owns model identity, token counting, request byte/token ceilings,
   output reserve and caller-measured non-context reservations. The effective
   assembler allocation is the minimum of its local budget and remaining model
   allocation. This is an opt-in interface for future Runtime wiring; default
   callers gain backfill and multilingual estimation without a new Runtime.
3. EvidenceRecovery reads immutable locators and current source snapshots through
   an injected loader. Verify workspace, permission, active/current version,
   complete source hash, exact quote range/hash and evidence identity. Historical
   sources require explicit historical mode and remain labelled historical.
4. WorkingContext is a versioned JSON-serializable operational state, separate
   from RuntimeState and Tool Ledger. A Compactor only proposes summary text and
   references. A deterministic guard preserves goals, unresolved questions,
   conflicts, negative evidence, execution failures, next investigations and raw
   ledger/checkpoint references. Only freshly restored original quotes may be
   used as evidence; summary text is always an untrusted operational hint.

The simplest alternative (larger caps) is measured as experiment B. No generic
framework, second Runtime, live model call, real-vault mutation, product entry
point change or protected shared-file edit is in scope.

## Acceptance criteria

- Oversized early evidence permits smaller later evidence to fill available slots.
- Chinese estimates and injectable exact token counters are tested; serialized
  byte and token reservations are observable and enforceable.
- Conflicting/chain groups are all-or-none; coverage reports include absent,
  filtered and budget-excluded requirements.
- Working state round-trips across turns. Fake compaction cannot erase safety
  state, invent verified refs or mutate its input/raw records. Missing sources
  yield incomplete recovery, never substitute summaries for evidence.
- Recovery rejects cross-workspace, forbidden, deleted, superseded, changed,
  mismatched span/hash/identity sources. Exact repeated passages are distinguishable.
- Independent synthetic A/B/C experiment covers two/three hops, scattered same
  file passages, conflicts, supplier/plan revisions, noise, long history, source
  changes and restoration; Gold is evaluation-only. Report measured metrics and
  limitations, including lack of automatic Agent integration.
- Relevant regression checks and independent read-only review; delivery evidence,
  commit and normal push to the requested branch, remote SHA readback. No PR/merge.

## Implementation order

Audit -> selection/policy tests and implementation -> recovery tests and
implementation -> working-state/compaction tests and implementation -> independent
synthetic experiment -> regressions -> read-only review -> delivery and Git.

## Pressure points and trade-offs

Caller-declared evidence groups cannot discover an unknown contradiction. A
bounded greedy strategy is deterministic and inexpensive but not globally
optimal. A source loader must supply current authoritative state and permissions;
versioned locators alone cannot prove currentness. Full request serialization,
tool schema overhead and provider tokenization remain Runtime responsibilities.
