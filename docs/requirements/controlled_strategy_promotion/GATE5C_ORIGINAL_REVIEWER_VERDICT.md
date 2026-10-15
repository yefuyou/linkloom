BLOCK

# Gate 5C Original Reviewer Verdict — Final Integrity

Date: 2026-09-17. Role: Independent Reviewer / Architecture Gatekeeper.
Gate: 5C — Controlled Strategy Promotion. This is an original final-integrity
review; the Worker prepared the packet but did not self-approve, commit, push,
call a Provider, or wire Runtime production behavior.

## BLOCKERS

### P1 — Comparison authority does not independently bind oracle outcomes or proof kind

The final comparison is internally self-consistent but not independently
truth-bound. `ComparisonAuthority.resolve()` reconstructs `FrozenSuite` from
the sealed branch observations and `summary.proof_kind`, then trusts the
producer-supplied row outcomes after checking only that their evidence
locators point to those observations. `MetricEvidenceReference` deliberately
uses the same `sealed_observation/v1` reason for both `PASS` and `FAIL`.
There is no evaluator-owned sealed oracle-result artifact (or immutable,
externally configured expected outcome/proof-kind pin) against which each
dimension outcome is checked. The manifest SHA passed to the authority is the
bundle/runner-returned pin, so the current API does not establish independent
authority over a newly produced manifest.

Expected: every per-case/per-dimension outcome and `proof_kind` must be bound
to an evaluator-owned sealed output or a genuinely immutable external suite /
evaluator pin. Authority must reject a comparison whose rows or proof kind are
changed even when the producer rehashes the comparison, branch locators,
seals, result ID, suite ID, and manifest.

Actual: I loaded the retained frozen proof into memory only (no file writes),
changed its seven rows to all `PASS/PASS` except `decision_semantics`
`FAIL/PASS`, changed the declared proof kind from `scripted_replay` to
`responsive_fake`, recomputed the suite hash, recommendation, result ID,
comparison hash/seal, manifest result entry, and supplied the resulting
manifest pin to the real `ComparisonAuthority`. `resolve()` returned the
fabricated `recommend_accept` with these rows:

```text
contract PASS/PASS 0
grounding PASS/PASS 0
decision_semantics FAIL/PASS 1
question_scope PASS/PASS 0
unsupported_inference PASS/PASS 0
uncertainty PASS/PASS 0
retrieval_tools PASS/PASS 0
```

The retained frozen proof is otherwise unchanged on disk and originally
resolves to `insufficient_evidence`. This is a promotion-governance failure:
the authority verifies hashes and arithmetic, but not that the evaluator
actually issued the claimed outcomes. Merely giving PASS and FAIL different
reason strings would not solve the circular producer-rehash path.

Required fix: add the independent evaluator-owned sealed outcome/proof-kind
binding, make the authority verify it, and add adversarial tests that re-seal
metric outcomes and relabel `proof_kind` while leaving observations unchanged;
both attacks must fail closed. Re-run the focused integrity/evaluation proof
and resubmit a new 5C packet and verdict. Until then, no human receipt may
promote this Strategy foundation on the strength of this comparison.

## IMPORTANT

- The two prior Gate 5B P1 findings remain resolved: typed canonical
  document/tool vectors and complete trajectories are in the shared condition;
  per-case metric evidence and measured context-cost rows are recreated by
  the authority.
- The other five final-integrity checks pass for the reviewed frozen scope:
  Strategy is a separate procedure rather than renamed Experience prompt text;
  generic semantic fields contain no case/Gold/answer/path data; candidate to
  normal-use requires the persisted human receipt; grounding/contract
  regressions and unknown evidence fail closed without averaging; and the
  5/4000/16000 bounds include framing and serializer/constructor bypasses.
- This BLOCK is limited to the missing independent oracle binding. It is not a
  request to add Provider wiring, live efficacy, authentication/RBAC, or other
  deferred scope.

## NON-BLOCKING

- Responsive fake `recommend_accept` is mechanism evidence only. The retained
  frozen real trace remains `insufficient_evidence` and its test-only rejection
  is not a real human decision; no live efficacy or real accepted Strategy is
  claimed (`mps=1, aer=0, iti=0`).
- The reviewed source freeze is unchanged and independently matches
  `171fe1a4689f6341697fd8aeb44c84ab59d6e435f020775c7835d9006c17aac5` for the
  19 owned Python files. Before this verdict the prospective 31-file list was
  complete except for this forthcoming verdict; after writing it, the exact
  list must be checked again.
- Existing retained proof checks were read-only: responsive resolves to one
  accepted test record with 7 metric rows and 1 cost row; frozen resolves to a
  rejected test record with the same evidence shape and zero serialized
  Strategies. No runner, oracle, Gold, Provider, or write-capable path was
  invoked during this review.
- Existing focused/adjacent evidence remains 82 focused plus 3 offline-proof
  checks and 418 adjacent tests with one opt-in Provider skip; compileall and
  configured whitespace checks were already green. No source/test change was
  made in this review, and the index remains empty.
- Deferred v1 limits remain as documented: local single-writer trust roots,
  no auth/RBAC or revocation, no tokenizer/currency pricing, and no
  production Runtime integration.

## VERDICT

BLOCK

Gate 5C final integrity is not accepted. The Builder must fix the P1 above and
resubmit for an original Reviewer recheck. There is no authorization for the
selective 31-file local commit, status completion, Provider use, production
promotion, push, or next-stage work.

## Review scope and evidence

I read the complete Gate 5 chain: `SPEC.md`, implementation plan, task ledger,
offline proof, Gate 5A packet/verdict, the first Gate 5B BLOCK, the Gate 5B fix
packet, the Gate 5B Round 2 APPROVE, and `GATE5C_REVIEW_PACKET.md`. I inspected
the Strategy models, generic policy, generation, evaluation runner, authority,
Store, retrieval/context builders, final serializer, and all owned Strategy
tests/helpers. I independently verified the two retained proof roots,
manifest/result/seal identities, exact branch request equality, typed vectors,
complete trajectories, per-case rows/costs, accepted/rejected Store replay,
and the 19-file source-freeze digest. The adversarial result above used
in-memory copies and monkey-patched reads only; the retained proof files were
not changed.

The authority contract currently has a circular trust shape:

```text
producer oracle rows + proof kind
        -> producer hashes/seals/manifest pin
        -> ComparisonAuthority verifies consistency
```

The missing edge is an independently sealed evaluator outcome:

```text
evaluator-owned oracle result/proof pin
        -> ComparisonAuthority verifies every row and proof kind
```

Without that edge, a producer can create an internally valid but semantically
false recommendation. The existing tests cover non-context re-sealing,
orphan locators, unknown outcomes, cost arithmetic, and lifecycle boundaries,
but do not cover this re-sealed metric-outcome/proof-kind attack.

## Six-check acceptance matrix

| Check | Result | Evidence |
| --- | --- | --- |
| 1. Real eval/promotion governance, not Experience rename | PASS | Separate Strategy lifecycle, paired evaluation, authority evidence, and persisted human receipt; no prompt-only injection. |
| 2. No semantic benchmark/Gold/path derivation | PASS | Generic provider-neutral template and public-input evaluator-private-key rejection; production Strategy path does not load Gold. |
| 3. No candidate-to-normal-use receipt bypass | PASS | Candidate-only creation/save, current persisted candidate event, human receipt bindings, fresh source/comparison replay, accepted-only retrieval. |
| 4. Canonical vectors/trajectories and per-case evidence/cost fairness | BLOCKED | Observation equality and qualified rows pass, but outcome/proof-kind authority is not independent; re-sealed comparison attack succeeds. |
| 5. Grounding/contract fail-closed policy | BLOCKED | Policy is conservative and passes normal regressions, but a re-sealed false PASS can bypass the policy's intended oracle input. |
| 6. Final 5/4000/16000 bounds including bypasses | PASS | Constructor, framing, serializer, stale/oversize/saturation tests and fresh accepted-only context replay pass. |

## Learning reflection

### Step

Original Independent Reviewer performing the Gate 5C final integrity and
promotion-eligibility review.

### What changed

No source or test code changed. The final packet consolidated the prior 5A/5B
chain, frozen proof roots, source freeze, lifecycle replay and prospective
commit scope. This review added only this Reviewer-owned verdict.

### What I learned

Hashing and re-sealing prove consistency, not evaluator truth. A comparison
authority needs an independent, sealed oracle binding for each outcome and the
proof kind; otherwise a producer can alter both claim and hashes and receive a
valid-looking recommendation. Evidence locators, shared-condition equality,
and conservative policy remain necessary but are not sufficient by themselves.

### Evidence and next role

The retained proof, source inspection, prior focused/adjacent tests, exact
freeze digest, and the zero-write in-memory attack support this BLOCK. The next
role is the Builder Worker to implement the narrow oracle-binding fix and
resubmit; the original Reviewer must then repeat the integrity check. No
commit, push, Provider call, or production promotion is authorized meanwhile.
