# M3-K independent review

Reviewer: separate read-only `gpt-5.6-luna`, reasoning `max`, task
`/root/m3k_review`. Reviewed the approved M3-K SPEC, diff, tests, saved evidence
and metric definitions. No review edits, paid Provider calls or Git actions.

## Initial result: Request changes

No Critical findings. Three Required findings were independently reproduced:

1. A later structural error discarded earlier DUPLICATE_WORK diagnostics and
   reset excess_steps to zero.
2. An implicit full-source completed step survived inventory expansion because
   only old span membership was checked.
3. Explicit-source relation projection dropped its evidence scope when returning
   an endpoint-only SemanticQuery.

The Reviewer also verified the reporting boundary: original live scores stay
2/3 and 6/7, 3 calls and 18,055 tokens; authored outputs are contract-only;
valid-plan denominator is 11 non-refusal attempts plus a separate valid refusal.

## Worker repairs and evidence

Four regression cases reproduced the findings before repair (3 failed, 1
positive-control passed), then the complete Planner group passed **79 tests**:
[review-repairs.xml](evidence/review-repairs.xml).

1. Keep the current request's collected quality issues through structural
   rejection; report them alongside the structural issue with partial evaluation
   scope. The mixed duplicate/illegal-relation probe retains excess_steps=1.
2. Compare the implicit full-source set exactly. Expanded inventory invalidates
   that receipt. Explicit source scopes still allow the required discovery ->
   Host-bound new change -> dependent impact replan while preserving the original
   goal and completed step. Both positive/negative controls pass.
3. Reject projection of explicit source scope with
   SOURCE_SCOPED_OPERATION_REQUIRES_HOST_BINDING; time scope remains guarded.
   Host must consume the full ready step through existing tools.

Final independent repair verification: **PASS**. The same independent Reviewer
re-read the three repairs and new regression tests, verified the 79-test evidence,
and closed all Required findings. No new Required/Critical findings remain.
The 157 related tests and contract-only metric/reporting boundary were accepted.
