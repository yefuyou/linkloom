# Implementation Plan: Controlled Strategy Promotion

Status: **5A/5B/5C original Reviewer APPROVE; frozen implementation accepted, local Git closure authorized**.
Base HEAD: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`.
Governing boundary: [SPEC.md](SPEC.md). One canonical feature directory; no
competing copy in unrelated existing `tasks/`. User controls 5A/5B/5C checkpoints.

## Sequence and ownership

0. DONE: original Reviewer approved Gate 4; user specifically approved the
   67-file selective commit. `85a6dc3` has the exact approved staged scope;
   19 unrelated tracked dirty files remain uncommitted; no push. Do not repeat
   that commit, amend it or include remaining Runtime V2 dirty work.
1. 5A: inspect existing Experience Store/authority/policy, TeamDecision contract,
   frozen Golden/business post-hoc oracles and trajectory harness. Draft SPEC,
   plan, task, 10-section Review Packet only; send original Reviewer and stop.
2. Only after original 5A APPROVE: Worker RED -> GREEN within slices below;
   acceptance criteria/policy are frozen, not weakened to get the first gain.
3. Submit unified 5B packet after offline proof and adjacent regression;
   fix objective BLOCKERs within the approved boundary and resubmit.
4. Original 5C Integrity Review of source, sealed proof, exact lifecycle,
   fairness/leakage/bounds. Only final APPROVE permits completion claim and
   user-authorized selective Gate 5 commit. No push or next-stage auto-start.

## Internal implementation slices (not human approval boundaries)

| Slice | Files/responsibility | Acceptance and verification |
|---|---|---|
| 1 | strategy/models.py, policy.py, __init__; test_strategy_contract.py | RED accepted constructor/case-answer-path/closed schema; GREEN immutable candidate, generic procedure and typed opaque provenance |
| 2 | strategy/generation.py; test_strategy_generation.py; test-only source helper | RED candidate/rejected/orphan/foreign/stale sources; GREEN configured Gate 4 Store replay and one registered mapping; zero legitimate source -> zero candidate |
| 3 | evaluation/strategy.py; strategy/authority.py; test_strategy_evaluation.py; safe suite fixtures | RED unfair conditions/partial reports/forged seals/deltas/regressions; GREEN one-variable paired actual-request receipts, evaluator-owned sealed complete comparisons and conservative recommendation |
| 4 | strategy/store.py; test_strategy_governance.py | RED direct/copied/unpersisted accepted and nonhuman/insufficient approval; GREEN append-only original-candidate -> explicit human receipt; fresh replay and all source/comparison bindings |
| 5 | strategy/retrieval.py, context.py; test_strategy_context.py | RED candidate injection/constructor/text/accounting/config bound bypass with >5 genuine accepted records; GREEN accepted-only fresh final serializer, applicability/order/dedup/whole-record char+byte limits |
| 6 | tests/integration/test_strategy_offline_promotion.py; test-only frozen-oracle bridge; task/proof docs | Existing accepted test Experience -> paired frozen inference -> existing oracle -> recommendation -> explicit test human review -> terminal result; genuine unchanged replay may be insufficient, not invented improvement |

Each slice stays focused (normally 2–5 files plus its safe fixtures). Run focused
tests on meaningful changes and proceed internally; do not request review for
each patch. Avoid generic platform or large speculative refactors. Checkpoint
after source generation, sealed paired comparison, durable governance/bounds.

## Evaluation reuse and proof

Use unchanged `TeamDecisionResult.from_dict` for structural contract.
Test-only frozen bridge reuses `tests/smoke/test_deepseek_real_provider_smoke.py`
`evaluate_sealed_case` and business harness's counterpart on NEW private copies,
after observed seals. Those legacy functions write outputs; never point them at
the original historical roots. Do not call their real-run/client builders.
Do not claim their reference-membership check establishes full semantic
grounding. Existing uncertainty/scope information may be partial; preserve
N/E/REVIEW_REQUIRED and consequent insufficient_evidence. Production Strategy
does not import tests or change upstream evaluation behavior.

Source acceptance fixtures, mechanistic FakeModel gains and real human decisions
are separately labeled. Real historical source requires a legitimate persisted
accepted Experience; if unavailable, record zero candidate and the exact gap.
Human strategy acceptance cannot be synthesized by the independent code Reviewer.

## Planned verification commands (run only after 5A APPROVE)

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_strategy_contract.py tests/unit/test_strategy_generation.py tests/unit/test_strategy_evaluation.py tests/unit/test_strategy_governance.py tests/unit/test_strategy_context.py tests/unit/test_strategy_integrity.py tests/unit/test_strategy_review_fix.py tests/unit/test_strategy_oracle_binding.py tests/integration/test_strategy_offline_promotion.py
python -m pytest -q -p no:cacheprovider tests/unit/test_experience_models.py tests/unit/test_experience_policy.py tests/unit/test_run_reflection.py tests/unit/test_experience_store.py tests/unit/test_experience_retrieval.py tests/unit/test_experience_evaluation.py tests/unit/test_experience_authority.py tests/unit/test_gate4_fix_boundaries.py tests/unit/test_experience_review_transition_fix.py tests/unit/test_experience_identifier_fix.py tests/unit/test_experience_historical_receipt.py tests/integration/test_experience_offline_replay.py
python -m pytest -q -p no:cacheprovider tests/unit/test_memory_models.py tests/unit/test_memory_policy.py tests/unit/test_memory_store.py tests/unit/test_memory_retriever.py tests/unit/test_memory_lifecycle.py tests/integration/test_memory_runtime.py
python -m pytest -q -p no:cacheprovider tests/unit/test_runtime_v2_model_proposal.py tests/unit/test_runtime_v2_multi_action.py tests/unit/test_runtime_v2_grounding_visibility.py tests/integration/test_runtime_v2_multi_action_resume.py tests/unit/test_deepseek_api_adapter.py tests/unit/test_p85_gemini_api_adapter.py tests/integration/test_deepseek_durable_provider.py tests/integration/test_p85_durable_provider.py tests/integration/test_m04_production_e2e_resume.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_team_decision_final_contract_communication.py tests/eval/test_golden8_freeze.py tests/smoke/test_deepseek_business_evaluation.py
python -m pytest -q -p no:cacheprovider tests/unit/test_trajectory_grader.py tests/unit/test_trajectory_metrics.py
python -m compileall -q src/linkloom/strategy src/linkloom/evaluation/strategy.py src/linkloom/evaluation/strategy_oracle.py
git diff --check
```

Confirm existing trajectory test paths before execution; no fake successful
command if a path is absent. Use project temporary output roots outside Vaults;
retain logs, no authorized deletion. Network/client spies and Gold access spies
separate inference from post-seal evaluation. Revalidate original seal/pin/source
hashes in Gate 4 historical regression; no real Provider opt-in variable.

## Risks, limitations and reflection

Fair-looking labels without actual harness-observed request identity are not
proof. Durable status without current source/comparison resolution is not
approval. Regex without registered template equality is not answer isolation.
Frozen unchanged scripted outputs are insufficient evidence of efficacy.

Trusted bootstrap/host review configuration is the trust root; no new RBAC or
external services. Known legacy path/config noise stays outside scope. The
planning skills' optional Definition-of-Done attachment is absent; project
SPEC/acceptance contracts and explicit user gates remain normative.
Next role after verified 5B handoff: original independent Reviewer for Gate 5B.

For independent adjacent recheck on this host, use host execution with a NEW
UUID project `.tmp/` --basetemp, verify its resolved path remains under `.tmp/`
and does not exist before invocation, and retain policy=all. Sandbox pytest
mode-0700/cleanup may cause WinError 5 even with project basetemp; do not fix
ACLs, delete old roots, or report a permission setup failure as semantic FAIL.

## Gate 5C bounded repair

Original 5C BLOCK: outcome/proof-kind claims were producer-rehashable. First
reproduce both attacks as actual failing tests; configure oracle owner and
separate verifier from host suite/key BEFORE Runner. Owner validates both sealed
observations and then executes the fixed private evaluator; it issues the
closed signed outcome matrix. Runner derives comparison rows from this matrix.
Authority verifies host-rooted signature, immutable suite/proof/evaluator and
each outcome; Store/context retain fresh replay. No production wiring, new
dependency, auth/RBAC, policy change or source artifact mutation.
Run new integrity tests, full focused and mandated adjacent suites, retain fresh
proof roots, and submit one original 5C recheck. Preserve the first BLOCK verdict
and unsigned historical outputs. Retained test-only restart key fixtures stay
under `.tmp/*/private-bootstrap/`, outside comparison artifacts and Git scope;
real host key provisioning is deferred, not silently configured in production.
