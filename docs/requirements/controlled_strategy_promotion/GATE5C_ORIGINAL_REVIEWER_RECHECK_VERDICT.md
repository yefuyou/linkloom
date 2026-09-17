APPROVE

# Gate 5C Original Reviewer Recheck Verdict — Final Integrity

Date: 2026-09-17. Role: Original Independent Reviewer / Architecture
Gatekeeper. Gate: 5C — Controlled Strategy Promotion. This is the recheck of
the preserved original Gate 5C BLOCK, not a new architecture review or a
per-patch review.

## BLOCKERS

None. The single original Gate 5C P1 is resolved within the approved narrow
repair boundary.

## IMPORTANT

- This APPROVE accepts the reviewed Gate 5 foundation and permits the Builder
  to finalize only the status/checklist metadata and the exact selective
  35-file local commit described in `GATE5C_REVIEW_PACKET.md`.
- It does not authorize source/test changes after this verdict, Provider use,
  Runtime production wiring, live-efficacy claims, deployment, push, or a
  subsequent phase. Any new source or test change requires a new review.
- The trust boundary is explicit: the host configures the FrozenSuite, fixed
  evaluator, 32-byte integrity key and OracleVerifier/OracleAuthority before
  the producer Runner. The key/verifier/suite are not taken from a bundle,
  manifest or request. HMAC is local integrity only; this does not claim
  hostile same-process Python isolation, authentication/RBAC, or real host
  key provisioning.

## NON-BLOCKING

- Responsive `recommend_accept` remains synthetic mechanism evidence, not live
  model efficacy or real human intent. The retained frozen real trace remains
  `scripted_replay` + `insufficient_evidence` and its `human:test_fixture`
  rejection is not a real user decision. Historical source disposition remains
  `mps=1, aer=0, iti=0`; no real accepted Strategy is claimed.
- Full semantic entailment, scope, unsupported-inference and real Provider
  efficacy remain correctly represented as `REVIEW_REQUIRED`/`N/E` where the
  existing oracle cannot establish them. Tokenizer/currency pricing,
  authentication, revocation, migration and production wiring remain deferred
  by the SPEC.
- The first local adjacent attempt hit the known Windows temporary-directory
  permission setup failure. A fresh validated project `.tmp/` basetemp run
  completed successfully with 418 passed and one disabled opt-in Provider
  skip; no ACL was changed and no old temporary root was deleted.

## VERDICT

APPROVE

Gate 5C final integrity is accepted for the frozen scope. The prior original
`GATE5C_ORIGINAL_REVIEWER_VERDICT.md` remains a preserved historical BLOCK and
is not rewritten. The Builder may now perform the exact selective local commit
workflow, subject to the packet's 35-file allowlist and no production/public
action.

## Review scope and boundary

I read the complete current chain: `GATE5C_REVIEW_PACKET.md`,
`GATE5C_REVIEW_FIX_PACKET.md`, `SPEC.md`, `implementation_plan.md`,
`task.md`, the updated `GATE5_OFFLINE_PROOF.md`, Gate 5A packet/verdict, the
first Gate 5B BLOCK, the Gate 5B fix packet and the Gate 5B Round 2 APPROVE.
I inspected the 21 frozen Strategy/evaluation Python files, the new
`strategy_oracle.py`, all Strategy tests/helpers and the final lifecycle and
context consumers. I made no source or test edit, did not invoke a Provider or
Gold loader, and did not stage, commit, push or promote anything.

The current 21-file source-freeze digest independently recomputes to
`9b139f202665a3f32773a0a86d1d24347ded4710a7ec009cd185ec202cba2f1a` using the
packet algorithm: ordinal repo-relative path order, UTF-8
`path<TAB>lowercase raw SHA-256<LF>` material, then SHA-256. Base/master remains
`85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`; the index is empty and the 19
unrelated tracked dirty files remain outside this feature.

## Resolution of the original Gate 5C P1

The original BLOCK showed that a producer could rewrite comparison outcomes and
`proof_kind`, rehash public rows/suite/result/seals/manifest and obtain a
false `recommend_accept`. The Worker fix introduces two distinct host-rooted
objects:

1. `OracleOwner` receives a preconfigured FrozenSuite, private evaluator and
   key. It validates both actual sealed branches, the exact configured request,
   typed document/tool vectors and complete trajectory condition before the
   evaluator reads private Gold, then exclusively creates the closed
   `strategy-oracle-outcomes/v1` matrix and HMAC seal.
2. `ComparisonAuthority` requires a separately configured `OracleAuthority`.
   `OracleVerifier` checks issuer/MAC, fixed suite/proof/evaluator/oracle
   identities, ordered case matrix and every dimension outcome. Authority then
   requires summary/receipt oracle hash identity and compares every row to the
   independently issued matrix before rechecking all branch evidence, cost,
   context and lifecycle bindings.

The producer may still recompute public comparison hashes, but it cannot issue a
different oracle outcome without the independently bootstrapped key. The fix is
therefore an independent authority edge, not another producer-controlled hash.

## Independent verification evidence

- `python -m pytest -q -p no:cacheprovider tests/unit/test_strategy_oracle_binding.py`:
  **13 passed in 2.11s**. This covers the two public rehash claim attacks, the
  original frozen-trace relabel/outcome attack, signed matrix/proof/issuer/
  boolean/non-ASCII MAC mutations, foreign key/suite trust, missing authority,
  key/model/artifact isolation and malformed bootstrap keys.
- Full Strategy focused command from the implementation plan:
  **95 passed in 135.24s**. This includes the prior 5B contracts, governance,
  fairness, evidence/cost, stale/constructor/context-bound tests and the new
  oracle-binding cases.
- Mandated adjacent host run with a new validated project basetemp
  `.tmp/gate5c-recheck-adjacent-e41a7ce227a041958daa37d89a3d0b60`:
  **418 passed, 1 skipped in 27.79s**. The only skip is the disabled opt-in
  real Provider path; no Provider was called.
- `python -m compileall -q src/linkloom/strategy
  src/linkloom/evaluation/strategy.py src/linkloom/evaluation/strategy_oracle.py`:
  PASS. Tracked `git diff --check` and the owned source/test/packet whitespace
  scan: PASS.
- Read-only replay of the two current signed proof roots independently loaded
  the private-bootstrap fixture key, reconstructed the host FrozenSuite and
  OracleVerifier, and resolved through `ComparisonAuthority`, `StrategyStore`,
  `StrategyRetriever` and `StrategyContextBuilder` without Runner/oracle/Gold
  or write calls. Responsive: manifest
  `6eced98a2f74e8aa5295adbda6b22e5bac9c702175d05cb3eb57f6137e983d9f`, oracle
  outcome `62e9214d8b25fc84804a7893d60dd4e6dec5fd2c98e91a9a857e0215c9173553`,
  7 rows + 1 cost, `recommend_accept`, accepted record and 1 serialized
  context item. Frozen: manifest
  `8e9e711c6fe55d2c5d9c299cff0f88df50135b983fd5da4fa9c134e53c68e3e7`, oracle
  outcome `8ba336b8ec6124b12ebef43afc1133d4933632a627009566b6523171f105257d`,
  7 rows + 1 cost, `insufficient_evidence`, rejected record and 0 context
  items. All manifest/artifact/seal/result IDs matched the packet.
- The random 32-byte restart fixtures are ignored `.tmp/*/private-bootstrap`
  files, outside comparison roots, receipts, model text and the prospective
  Git list. A read-only byte scan found no key or hexadecimal key in either
  proof's artifacts or lifecycle logs.

## Adversarial integrity result

The two original attack families now fail closed:

- Rewriting public metric outcomes (including `N/E -> PASS`) or relabeling
  `scripted_replay -> responsive_fake`, then recomputing comparison rows,
  proof/suite hashes, seals, result ID and manifest, is rejected because the
  summary no longer matches the independently signed oracle matrix.
- Rewriting a signed oracle matrix/proof/issuer/seal field and recomputing its
  public digest is rejected because the host verifier's HMAC and issuer/fixed
  identity checks fail.

The retained frozen proof and original historical observed/seal/post-hoc/Gold
pins remain unchanged. No signed result from a different strategy, branch,
context or fixed suite can satisfy the verifier's identities and the final
`ComparisonAuthority.validate(strategy, summary)` check. The producer's public
manifest remains a locator/integrity layer, not an oracle-truth authority.

## Six-check acceptance matrix

| Check | Result | Evidence |
| --- | --- | --- |
| 1. Real evaluation and promotion governance, not Experience rename | PASS | Separate Strategy lifecycle, actual paired Runner, independent oracle, authority-issued evidence and explicit persisted human entrypoint; no prompt-only injection. |
| 2. No semantic benchmark/Gold/answer/path derivation | PASS | Provider-neutral registered template, evaluator-private input-key rejection, opaque IDs/reasons and no Gold access in generation, Store, retrieval or final serialization. |
| 3. No candidate-to-normal-use receipt bypass | PASS | Candidate-only save, current persisted candidate event, source/comparison/oracle replay, `source=human` receipt and accepted-only fresh context; candidate/rejected proofs serialize zero normal items. |
| 4. Canonical vectors/trajectory and per-case evidence fairness | PASS | Typed canonical document/tool vectors, complete trajectory shared condition, exact paired requests, seven qualified rows, per-case context costs, signed outcome matrix and fresh authority recheck. |
| 5. Grounding/contract fail-closed policy | PASS | Uniform no-averaging policy rejects regressions and candidate Contract/Grounding FAIL; N/E/REVIEW_REQUIRED stays insufficient; signed oracle outcomes cannot be rewritten into a gain. |
| 6. Final 5/4000/16000 bounds including bypasses | PASS | Constructor/framing/serializer/stale/Unicode/oversize and six-record synthetic saturation tests pass; accepted-only final context freshly rerenders and measures bytes. |

## Five-axis architecture gate

- Correctness: the independent oracle outcome/proof binding closes the original
  semantic integrity gap; focused and adjacent suites are green.
- Readability and simplicity: the fix is a small dedicated owner/verifier
  module and explicit authority dependency; no policy/template or unrelated
  Runtime refactor was added.
- Architecture: Runner, OracleOwner, OracleAuthority, ComparisonAuthority,
  Store and final context keep issuance, verification, lifecycle and model
  serialization separate; no Provider/production touch point was introduced.
- Security and evaluation integrity: private key material stays outside
  artifacts/model/receipts/Git; HMAC, issuer, fixed suite and exact matrix
  checks fail closed under the documented local-integrity threat model.
- Performance and operational scope: the bounded local replay adds the
  intended file/hash/MAC work only; no unbounded context or network path was
  added. The Windows temp permission issue was environmental and resolved by
  the prescribed host basetemp procedure.

## Gate disposition and next boundary

Gate 5C acceptance criteria are satisfied for the reviewed frozen scope. The
Builder may now finalize status metadata and make the exact 35-file selective
local commit `feat(agent): add governed strategy promotion`, after verifying
the staged tree, message, tree/count, source freeze and preservation of the 19
unrelated dirty files. Do not use `git add -A`, amend, push, Provider,
production Runtime wiring or automatic next-stage work. Any discrepancy in the
35-file allowlist or any new source/test change returns to the Reviewer.

## Learning reflection

### Step

Original Independent Reviewer performing the Gate 5C oracle-binding recheck.

### What changed

The Worker added a bounded host-configured oracle owner/verifier and signed
closed seven-dimension outcome matrix, bound it through ComparisonSummary and
the persisted receipt, and added 13 adversarial integrity tests. No change was
made during this review; the original BLOCK remains preserved separately.

### What I learned

Sealed observations and per-metric locators establish attribution and cost, but
they do not establish which outcome an evaluator issued. A producer-rehashable
comparison must therefore be paired with an independently issued, fixed-suite
oracle receipt. HMAC is appropriate for this local integrity boundary only when
the host/key/verifier trust limitation is explicit.

### Evidence and next role

The 13 binding tests, 95 focused tests, 418 adjacent tests plus one disabled
Provider skip, direct signed-proof replay, exact 21-file freeze, and preserved
verdict chain support this APPROVE. The next role is the Builder to finalize
only the exact selective local commit; no Provider or production promotion is
implied.
