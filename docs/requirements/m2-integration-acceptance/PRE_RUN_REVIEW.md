# Independent pre-run review — 2026-10-18

Reviewer: `/root/final_reviewer`, independent read-only role. Initial reviewer
`/root/reviewer` completed an earlier static review, then could not continue
because its model quota was exhausted. No holdout relation predictions existed
at either pre-run review. No Reviewer edited production code or annotations.

Final pre-run verdict: **PASS — implementation may be frozen and H01–H04
executed once**. This approves the measurement protocol, not M2 semantics.

Independent evidence: `materialize(cases) == holdout-snapshots.json`; every
required Gold quote is present in the exact materialized segment from its
declared source index, with zero misses. The earlier reviewer checked source
content, hashes, Unicode offsets, provenance and evidence spans with zero
integrity errors. Input/Gold question IDs and source families align.

Required findings fixed before execution:

1. Use parsed segment evidence instead of timestamp-prefixed whole-source
   proposals. H03 task raw ranges retain A2/B2 continuation lines from the
   same source utterance, so neither counter-evidence nor support is dropped.
2. Bind required and decisive proof to source index, immutable version/hash
   and exact quoted text; reject duplicate, missing and extra prediction IDs;
   compare the declared predicate and typed endpoints; validate comparison
   IDs, evidence, dates, precision and boundaries.
3. H02 change proof originally included the timestamp/speaker header, which
   the parser excludes. The author annotation now quotes the exact parsed
   statement from the same source; business text and Gold verdicts did not
   change.
4. Formal authority is a hard gate: no decision store, zero formal decisions,
   SOURCE_CLAIM_ONLY and AUTHORITY_NOT_ESTABLISHED must hold. Nonzero safety
   counters cause explicit failure even if relationship scores look correct.

H02 original wording exceeds the existing finite grammar. This is a potential
implementation failure to measure, not permission to rewrite source/Gold or
extend production rules before acceptance. The same applies to H01/H03/H04.

The reviewer independently ran the harness tests: six passed; the lifecycle
case failed before entering its body because the review sandbox could not
create pytest's external temp directory. The main Agent's project-owned temp
runner passed all seven on the same implementation. This environmental failure
is retained separately from application failures. No Provider or M3 holdout ran.
