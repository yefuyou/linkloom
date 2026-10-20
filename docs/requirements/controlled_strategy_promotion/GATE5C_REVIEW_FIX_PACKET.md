# GATE 5C REVIEW FIX PACKET — INDEPENDENT ORACLE TRUTH

Date: 2026-09-17. Builder/Worker evidence, NOT a Reviewer verdict.
Original [5C BLOCK](GATE5C_ORIGINAL_REVIEWER_VERDICT.md) is preserved unchanged.
Base/master: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`. Index empty.

## Objective P1 and actual RED

Old authority trusted producer outcome labels and proof_kind after checking
hash consistency. Two RED tests produced valid-looking recommend_accept by
rewriting outcomes (N/E -> PASS) or relabeling scripted replay to responsive,
recomputing complete comparison/suite/seal/result/manifest public hashes.
Both failed with DID NOT RAISE before implementation (2 failed, 1.75s).
No original artifacts, Provider, Gold, template or evaluator was modified.

## Independent binding, not another producer hash

Trusted application host configures a FrozenSuite, fixed private evaluator,
32-byte integrity key and independent OracleVerifier BEFORE Runner. Runner gets
OracleOwner, while ComparisonAuthority gets separately configured OracleAuthority.
Neither key nor verifier is returned in EvaluationBundle, loaded from manifest,
chosen by request metadata or registered during resolve.

Owner reads both actual branch files and seals. It checks exact configured
case order/public request/config/context, typed observation vectors and complete
trajectory condition equality BEFORE the private evaluator can access Gold.
It executes the fixed evaluator on those observations and fsyncs exclusive-create
oracle_outcomes.json and oracle_outcomes_seal.json. There is no arbitrary-rows
sign API. All outcomes originate from the configured evaluator callback.

Closed `strategy-oracle-outcomes/v1` payload:

- strategy_id, suite_sha256, fixed proof_kind;
- evaluator_sha256, oracle_sha256;
- baseline_sha256, candidate_sha256, condition_sha256, context_sha256;
- ordered cases: opaque case_key and exact baseline/candidate seven-dimension
  maps with PASS/FAIL/N/E/REVIEW_REQUIRED/predeclared tool N/A only.

Seal is closed/versioned with exact boolean sealed, payload SHA, opaque issuer
identity and domain-separated HMAC-SHA256. Stdlib only. Authority independently
checks issuer, MAC, frozen suite/proof/evaluator/oracle/case order and exact row
outcomes. Summary and human receipt additionally bind oracle_outcome_sha256.
Producer may recompute every public hash, but cannot issue a different oracle
outcome under independently bootstrapped host trust. Valid signed outcomes for
another strategy/context/branch/suite are not transferable. Read-only review and
normal reuse freshly verify this binding without invoking oracle or opening Gold.

## Trust and scope limits

This is local integrity, not auth/RBAC, asymmetric multi-party signing, code
sandboxing or a generic signing platform. Trusted host code, suite, evaluator,
key provisioning and explicit human entrypoint remain the v1 trust root.
An administrator replacing private key/verifier/bootstrap/code is outside scope;
we do not claim Python private attributes isolate hostile same-process code.
Real host key provisioning/rotation is deferred. API requires externally supplied
bootstrap key/suite; no production key file loader/default or environment secret.

Test-only random keys are generated before producer and retained under ignored
`.tmp/*/private-bootstrap/*.bin` for independent restart review. They are outside
comparison directories, model context, receipts and the exact Git allowlist;
never printed. They are synthetic local integrity fixtures, not Provider/account
credentials. No real credential access, new dependency, Runtime/adapter/tool
wiring, global prompt mutation, auth/RBAC, policy threshold or registered rule
change. No Provider, Vault, deployment, promotion, staging, commit or push.

## GREEN and regression

New file test_strategy_oracle_binding.py has 13 cases: both original public
claim attacks; original real frozen-trace relabel/outcome attack; signed matrix,
proof, issuer, boolean, non-ASCII MAC mutations; foreign key/fixed suite trust;
missing independent authority; private key/model/artifact isolation; malformed
bootstrap key types/lengths. All pass. Fully resealed outcome claims and signed
matrix edits fail closed, not merely fail a stale public digest.

Focused integrity intermediate: 33 passed, 10.09s. Full final focused:
**95 passed in 159.78s**. Mandatory adjacent Experience/Reflection, Memory,
Runtime V2, TeamDecision, Golden/business, trajectory eval/seal/provenance:
**418 passed, 1 skipped in 37.97s**. Total **513 passed, 1 skipped**; skip is
disabled opt-in real Provider. Host basetemp is a new validated project .tmp UUID;
old outputs/ACLs are untouched. Compileall and git diff --check PASS.

Both fresh [signed offline proofs](GATE5_OFFLINE_PROOF.md) independently replay
authority -> Store -> final context without Gold or oracle/write calls:
responsive fake recommend_accept -> explicit TEST receipt -> accepted (1 item);
real frozen trace insufficient_evidence -> explicit TEST rejection (0 items).
Both have 7 metric rows, 1 cost row, 514-char/byte context delta. No new actual
human-approved Strategy/Experience or live efficacy is claimed. Historical source
disposition remains mps1 candidate/aer0/iti0; generator abstains there.

## Exact final freeze and handoff

Current 21 owned Python source/test files have freeze:
`9b139f202665a3f32773a0a86d1d24347ded4710a7ec009cd185ec202cba2f1a`.
Algorithm and exact prospective **35-file** selective commit scope are in
[unified final packet](GATE5C_REVIEW_PACKET.md). All 19 unrelated tracked dirty
files remain byte-identical, uncommitted; original Gate 4 base unchanged.
Original 5A/5B approvals and both historical BLOCK verdicts remain auditable.

Next role: original Independent Reviewer inspects the outcome issuance/trust
edge and repeats adversarial integrity review, writing DISTINCT
GATE5C_ORIGINAL_REVIEWER_RECHECK_VERDICT.md, then callbacks Builder. Only final
APPROVE permits status/checklist metadata closure and exact selective local
feature commit. No source/test changes after APPROVE; any new finding returns to
Worker. Do not silently fix source, stage/commit/push, call Provider or start
another phase in the review task.
