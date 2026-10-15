# GATE 5B REVIEW PACKET

Status: ROUND 2 ORIGINAL REVIEWER APPROVE; historical 5B handoff.
Date: 2026-09-17. Builder: Worker. Gate 5A formally approved by original
task `审查 Runtime V2 Gate 1`; see its owned verdict in this directory.
Base/master HEAD: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`.
No Provider, production promotion, additional staging, commit or push.
Original [GATE5B_ORIGINAL_REVIEWER_VERDICT.md](GATE5B_ORIGINAL_REVIEWER_VERDICT.md)
is the preserved initial BLOCK; both P1s were fixed and formally resolved in
[original recheck APPROVE](GATE5B_ORIGINAL_REVIEWER_RECHECK_VERDICT.md).
See [GATE5B_REVIEW_FIX_PACKET.md](GATE5B_REVIEW_FIX_PACKET.md).

## 1. Production diff

New isolated artifact package: `src/linkloom/strategy/{models,policy,generation,
authority,store,retrieval,context,__init__}.py`; dedicated offline comparison
extension: `src/linkloom/evaluation/strategy.py`. Seven unit files, one integration
file, two test-only helpers and this feature's docs. All current Gate 5 files
are untracked, so plain `git diff` alone does not display the delivered source.
Inspect the explicit new paths; upstream source is not changed by Gate 5.

No production Runtime/system prompt/Provider/tool/retrieval wiring. The 19 old
tracked dirty files retain identical entry SHA-256 bytes and remain unstaged;
untracked Runtime V2/docs/tasks also remain outside ownership. Index empty,
master/base unchanged, historical evidence and frozen Gold unchanged.

## 2. Candidate generation

Fresh configured Gate 4 ExperienceStore is the only source. Immutable source
approval references bind exact candidate/review events, decision hash and
Store identity. Registered supported accepted lesson -> prospective generic
procedure; candidate/rejected/empty/unsupported sources -> none; orphan/stale/
foreign or mismatched receipts fail closed. No source promotion or arbitrary
lesson/answer prose interpolation. Different schemas, IDs, logs and lifecycle.

## 3. Evaluation isolation

Configured immutable suite + one harness + one oracle; nine shared owner
identities freeze Runtime/tools/retrieval/base prompt/budgets/model abstraction/
seed/evaluator/oracle. Actual typed harness-received requests must equal the
frozen request and pairwise match excluding only strategy_context.
Both observed branches are fsynced/sealed Gold-free before either oracle call.
Complete seven per-case dimensions, measured deltas/severity, context characters/
UTF-8 bytes and baseline/candidate evidence refs are persisted and sealed.

Bootstrap-pinned ComparisonAuthority verifies exact manifest/artifact hashes,
closed booleans/schemas, both seals, complete cases/dimensions, actual shared
conditions (request minus context, typed document/tool vectors, complete
trajectory), suite, context text/cost, comparison seal and strategy binding.
Each metric carries explicit paired MetricEvidenceReference; every case has a
ContextCostDelta with measured baseline/candidate/char-byte deltas and evidence.
Authority derives those locators/costs again from actual sealed observations.
Consumers do not accept request-recomputed pins/reports or cached approvals.
Mechanistic responsive fake and non-causal scripted replay are labeled apart.

## 4. Promotion policy

Frozen general `strategy-promotion-policy/v1`: any measured regression, candidate
Contract/grounding FAIL or context overflow prevents acceptance. Supporting
unknown outcomes / nonresponsive replay / no target gain -> insufficient_evidence.
Recommend_accept needs complete applicable evidence, no per-case regression,
target decision semantics FAIL->PASS and same hard context ceiling. Tool N/A
requires predeclared inapplicability; no post-hoc omission or case threshold.
Recommendation is never a state change; insufficient cannot be forced accepted.

## 5. Approval transition

Candidate-only public construction/save. Separate explicit HumanStrategyDecision
entrypoint; source=human and typed bounded actor/rationale, not approved=true.
Versioned receipt embeds complete comparison references/results/deltas/regressions,
human actor/source/time/reason and original candidate/Store identity. Append-only
hash-chained fsynced event, then terminal state derived by current replay.
Fresh source and comparison proof are required during save/review/get/list/reuse.
Copied terminal/unpersisted/foreign review cannot inject. Rejected retained.
Incomplete final bytes retained and append refused pending explicit recovery;
corrupt complete events rejected. Trusted host entrypoint is not new auth/RBAC.

## 6. Leakage controls

Closed exact registered provider-neutral semantic fields including generic
expected_benefit; Gate 4 value/path/case/hint policy over all lifecycle values.
Opaque provenance only. Gold-private input keys recursively forbidden.
No production Gold loader/tests import, no benchmark answer mapping, no prompt
mutation. Integration spies prohibit Gold access in generation/review/reuse
and prohibit inference Gold until both observed seals; socket/client guards
prohibit Provider. Legacy oracle writes only fresh private post-seal copies.

## 7. Hard context bounds

Defaults 3/2000; hard top-k 5, characters 4000, UTF-8 bytes 16000 including
framing. Structured applicability/positive overlap, exclusion precedence,
score-desc/ID-asc order, dedup, whole records. Configured lower bounds win.
Normal selection/context accepts only freshly durable accepted Strategies;
candidate preview has a distinct offline request/type/heading.
Final to_dict rerenders from current Store/source/comparison proof, checks exact
text/IDs/accounting and bounds even on constructor/frozen-object bypass.
Six genuine durable synthetic strategies, oversize Unicode, stale source/result
and caller bounds/text tests exercise the final boundary, not just constructors.

## 8. Offline proof

[GATE5_OFFLINE_PROOF.md](GATE5_OFFLINE_PROOF.md) records retained roots/pins,
actual metrics and three paths: existing accepted fixture -> responsive fake ->
explicit test acceptance -> bounded reuse; existing accepted fixture + unchanged
real frozen output + original oracle -> insufficient_evidence -> explicit test
rejection; historical unapproved source -> zero Strategy. All test approvals
are labeled; no real human decision or semantic benefit fabricated.

## 9. RED -> GREEN and verification

Contract/generation/evaluation/governance/context/integration slices began with
focused RED (missing required module/type), then GREEN under frozen criteria.
Additional actual reproduced RED: missing receipt schema_version (KeyError),
three registered numeric-seal guards (DID NOT RAISE). Minimal fixes: explicit
closed receipt version, strict canonical seal comparison (no 1==True coercion).
Round-1 complete focused: 69 passed; original Reviewer independently passed 69
but found two objective P1 gaps. Round-2 fix RED: five actual reproduced
failures (missing refs/costs; three non-context changes incorrectly allowed).
New review-fix module: 13 passed, including re-sealed producer attacks.
Final Round-2 full focused: **82 passed in 139.29s**. Required adjacent host
recheck: **418 passed, 1 skipped in 29.68s**; combined **500 passed, 1 skipped**.
No successful count inferred; sandbox basetemp WinError 5 attempt is explicitly
not counted as successful verification or a semantic/code failure.
Compileall and git diff --check pass; owned source/docs whitespace/conflict
scan clean. Original Reviewer cosmetically normalized its 5A metadata trailing
spaces itself; verdict text/APPROVE unchanged. No Builder verdict rewrite.
The configured `git diff --check` exits 0. A diagnostic override with
core.autocrlf=false treated existing dirty CRLF lines as trailing whitespace;
that unrelated line-ending noise is not a Gate 5 finding and was not modified.

## 10. Adjacent regressions and next role

Latest combined required adjacent offline command: **418 passed, 1 skipped**.
Gate 4 178, Memory 31, Runtime 173, Team/Golden/business 25 + trajectory 11.
The skip is the opt-in real Provider smoke, disabled; no real call. Gate 4
seal/provenance/historical disposition is revalidated. Existing pytest-asyncio
fixture-scope deprecation is outside ownership; no configuration expansion.

Builder reflection: governed recommendation, fresh immutable evidence and
explicit persisted human decision are separate trust boundaries. Frozen partial
oracle evidence must abstain rather than manufacture a gain. Local single-writer
append/replay is intentionally small, with fresh-history I/O (six-record
saturation test dominates runtime); no distributed store or performance platform.

Next: original Independent Reviewer decides 5B APPROVE/BLOCK and owns
`GATE5B_ORIGINAL_REVIEWER_RECHECK_VERDICT.md`; first BLOCK stays intact.
Builder fixes objective findings only.
Gate 5C/final completion/local commit remain frozen until the required verdicts.
