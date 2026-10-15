# GATE 4 FIX RESPONSE — Six Reviewer BLOCKERs

Status: **ORIGINAL INDEPENDENT REVIEWER APPROVE — GATE 4 ACCEPTED**.
Date: 2026-09-16. Gate 5 remains frozen. The repair mapping below is Builder
evidence; final acceptance comes from the original independent Reviewer.

The original task `审查 Runtime V2 Gate 1` independently returned **APPROVE**,
no unresolved BLOCKER, and saved
[GATE4_ORIGINAL_REVIEWER_VERDICT.md](GATE4_ORIGINAL_REVIEWER_VERDICT.md).
It verified focused178 / Memory31 / Runtime173 / Team+Golden25 passed,
one intentional skip (**407 passed, 1 skipped**), all 60 repair cases,
compileall/diff/scoped whitespace and unchanged historical seals/linkage.
This records Gate 4 acceptance only and does not authorize Gate 5.

The latest user BLOCK/REJECT directive supersedes the prior r2 acceptance.
All edits in this repair are within Gate 4. Runtime V2, Provider adapters,
prompts, Gold, TeamDecision ownership, UI/README/MCP and real sealed artifacts
are unchanged. No real Provider, commit, staging, push or deletion occurred.

## BLOCKER 1 — Explicit Reflection eligibility

- Root cause: a registered semantic finding and same-run refs were insufficient
  to prove a reliable reflection basis. Applicability exclusions and typed run
  state/Provider errors could be ignored even after complete authority migration.
- Production: `AuthoritativeEvaluationResult.generation_allowed` is the shared
  eligibility gate for Reflection and Store authority. It requires all present
  typed run owners terminal; valid authority/seals; all five supporting evaluator
  dimensions PASS; one registered semantic FAIL; valid linked observations;
  positive task signals; no exclusion or Gold signal; and known Provider outcome.
  `resolve` reads real nested `error.code/details.outcome` structures. Reflection
  abstains with `()` when a valid authoritative result is ineligible.
- RED: initial five eligibility payloads generated candidates (`8 failed,
  4 passed` including other boundaries). Further typed running-state and nested
  transient-error probes produced `2 failed, 7 passed, 11 deselected`.
- GREEN: all nine eligibility cases, four unusable dimension cases, and historical
  eligibility cases pass in the final `178 passed` suite.
- Invariant: no weak candidate from grounding N/E, Provider transient/unknown,
  pure infrastructure failure, absent causal basis, exclusion or Gold signal.
  Malformed/untrusted inputs reject; valid but unusable receipts abstain.

## BLOCKER 2 — Supporting observation linkage

- Root cause: membership in a sealed run catalog did not prove that a ref supported
  the reflected observation. A same-run catalog orphan could pass.
- Production: complete results name `supporting_observation_ids`; sealed typed
  observations declare IDs, refs and `model_observed`. Authority requires exact
  finding-ref equality with linked model-observed observation refs and catalog
  membership. Save/reload/read/review/retrieval/context re-resolve this authority.
- RED: `test_catalog_membership_without_observation_link_rejects` appended a
  catalog-valid orphan to the finding; no exception occurred before repair.
- GREEN: orphan, unobserved observation, unrelated observation, cross-run,
  fabricated catalog, stale seal and historical exact-linkage cases pass.
- Invariant: same-run prefix/catalog membership alone never authorizes evidence.

## BLOCKER 3 — Entire opaque identifier grammar

- Root cause: word-boundary benchmark matching missed `run_mps-001`; broad ID
  characters/prefix paths could contain `run_safe:/private/gold.json`.
- Production: `policy.validate_source_identifiers` validates the entire
  `run_<opaque>:ev_<opaque>` grammar, rejects embedded benchmark IDs, paths,
  expected/Gold/answer-key hints, and binds refs to the exact run. Models,
  complete/historical authorities and policy call the same helper. Generic
  finding/review identifiers also receive Gold-safe text validation. Full
  lifecycle payloads, including review metadata, are scanned before normalization.
- RED evidence: independent identifier increment recorded missing-helper
  collection RED, then edge RED `17 passed, 1 failed`. The original Reviewer
  supplied both exact leakage payloads; their regressions are explicit in
  `test_experience_identifier_fix.py`. Collection RED is not semantic execution.
- GREEN: all 18 identifier cases pass, including both exact payloads, valid opaque
  IDs, embedded paths and expected/Gold hints; both payloads also reject in
  historical receipt source binding.
- Invariant: externally returned Experience provenance contains opaque IDs and
  hashes, never filesystem/case/answer hints. Internal pinned source locators are
  not returned in Experience or model context.

## BLOCKER 4 — Persisted explicit approval boundary

- Root cause: raw `status="accepted"` granted retrieval eligibility without an
  explicit persisted candidate-to-review transition.
- Production: `ExperienceRecord.create` only creates candidate. Accepted/rejected
  construction requires `ExperienceReviewTransition` bound to original identity.
  The transition stores candidate event ID, full explicit decision (actor/source/
  reviewed_at/evidence), review event ID and hashed store identity. Identity
  excludes status/time/transition, preserving the original candidate ID.
  `ExperienceStore.record_review` verifies the persisted original candidate before
  append; replay reconstructs the transition. `validate_reviewed_record` performs
  fresh durable replay and exact comparison, not caller metadata trust.
  Retrieval and context require a configured Store and this persisted proof.
- RED: direct accepted creation succeeded in the initial boundary RED.
- GREEN: direct accepted/rejected constructors reject; candidate is not selected;
  explicit persisted accept is retrieved; rejected stays audit-only; reload and
  forged/tampered transition checks pass. Existing tests now create acceptance
  through real test Store events, not `replace(status="accepted")` shortcuts.
- Invariant: Reflection cannot promote; raw accepted metadata cannot enter context.
  No RBAC, automatic reviewer or confidence/replay promotion was introduced.

## BLOCKER 5 — Final hard top-k defense

- Root cause: direct six-record Selection/context construction bypassed retrieval's
  top-k ceiling; final rendering trusted the upstream record count.
- Production: Selection and Context contracts reject >5 IDs/records. Builder
  independently enforces configured hard_top_k 0..5 and max_chars 0..4000, preserves
  deterministic ranked order, deduplicates IDs, selects whole records, and checks
  persisted acceptance/authority. Final Context `to_dict` revalidates frozen-object
  bypasses and lesson-count/identity cardinality. No Provider injection was added.
- RED: six-record Selection succeeded in the initial RED. A sixth untracked lesson
  survived final serialization: `1 failed, 17 deselected` before serialization fix.
- GREEN: six genuinely sealed, durably accepted records cannot construct Selection;
  constructor-bypassed input injects exactly first five (or configured three),
  repeated output/order is identical, and sixth-lesson serialization rejects.
- Invariant: final Builder output <= configured item/character bound; serializer
  cannot silently carry a sixth lesson. Production Runtime remains unwired.

## BLOCKER 6 — Preserve real historical 1 / 0 / 0

- Root cause: the separately introduced complete-result Authority migration made
  all three original historical projections audit-only. This was not an error in
  the original mps lesson interpretation. The mps raw evaluator exposes only three
  dimensions, so pretending it was a complete six-dimension result was unacceptable.
- Explicit authorization: user approved **bootstrap-pinned historical-review
  receipt** compatibility after the conflict was surfaced in this repair.
- Production: distinct `HistoricalReviewAuthority` / `HistoricalReviewResult`
  validate pinned receipt, original observed/seal/evaluator/review-record hashes,
  run IDs, terminal/Provider guards and existing TeamDecision contract/review owner.
  mps source refs must equal actual Final rejected-alternative refs and occur in
  completed model-called tool results with verified evidence plus upstream review
  checks. Source evaluator booleans/dispositions must match the original artifact.
  Missing evaluator dimensions stay absent. Receipt creation timestamp is not an
  invented past-review timestamp. This is not another evaluator or case lookup.
- RED: approved compatibility API was absent (`1 collection error`). The original
  sources were already demonstrably rejected by the prior complete-only contract.
  Historical Gold task-signal RED: `1 failed, 16 passed`; then fixed and rerun.
- GREEN: **unchanged sealed mps -> one candidate; aer -> (); iti -> ()**. All 17
  historical tests pass: exact real linkage, durable separate acceptance, foreign
  issuer, exclusion/Gold, orphan/unobserved/cross-run, no dimension backfill,
  changed disposition/digest/identifier and stale receipt paths.
- Invariant: historical evidence compatibility is explicit, pinned and read-only;
  no absent field becomes PASS and no Provider incident becomes semantic FAIL.

### Exact historical source proof

| Source | Observed SHA-256 | Seal SHA-256 | Existing evaluator SHA-256 |
|---|---|---|---|
| mps | `40eb79e79ae170d42ce0727fce84fe443f5e73c04d8d4295586a712472dc9b6c` | `d05fea3edd5172b478a224618a9fc6e60b6bae7a818f2a6a5c56de73984fc725` | `29a1aea4f2e5a6e24c70858ab7d14fd61c4891ba8afac963e0d658ee89ff297d` |
| aer | `cad4f7cfe9ed216e4afb0f024ab840c7d4fb9baa1219d5aa3c783a8e83dc75fe` | `c8e63cd5b04d2a17b8ee9b237ad156df7e57d5b7d6a77fe081aa60bd102c3bc4` | `9e326e332d240248ee7d4386e9f6ec6c8b411b07e8d722c4cb12bf9f44ce9083` |
| iti | `9358a9ad8118d91439c0905ddb2cd505c4d9b222635d8759a89d9730af73db05` | `cb46956508a3a497e91b40201745dc2f1f324ebe623d6a897463718ecd620f91` | `4659968edf56411768ca3277d9742c30b7e981107e9262ad5c67b982906d27c6` |

Original source dispositions remain: mps infra/grounding PASS, semantic FAIL;
contract is explicitly established in the existing M1.2 review and checked with
its existing owner, not added as an evaluator dimension. aer retains successful
Runtime multi-action proof followed by unknown Provider outcome; no Final means
Final contract/grounding/semantic N/E, not FAIL. iti keeps infra/contract/grounding
PASS and overall POSTHOC_REVIEW_REQUIRED, without inventing a causal lesson.
Original posthoc `gold_accessed=true` is post-seal evaluator access, not observed
run leakage; no Gold loader/expected answer is forwarded or opened by Reflection.

Pinned synthetic manifest: `e58cd2e2b2c7994c8e6ce97ca869c3b317eb2ca312a1974efef05a06554d2c01`.
Pinned historical manifest: `70b8c3a4091e2ea350093edc1d1c60fe5e517c801739ae64730f9933148ad0d0`.
Pins are trusted test/application bootstrap configuration, not request inputs.

## Changed-file responsibility

- Production: `experience/models.py`, `authority.py`, `reflection.py`, `policy.py`,
  `store.py`, `retrieval.py`, `context.py`; `evaluation/experience.py` only adapts
  candidate normalization to exclude reviewed transition metadata.
- Tests: four new fix files (`test_gate4_fix_boundaries.py`:20,
  `test_experience_review_transition_fix.py`:5, identifier:18, historical:17)
  total **60 cases**; existing Experience model/policy/retrieval/evaluation/
  authority/replay tests and helper migrated to explicit persisted reviews.
- Fixtures: only Gate 4 synthetic authority fields/seals/pin updated; four safe
  historical receipt/manifest JSON files added. Original `.artifacts` untouched.
- Docs: SPEC, plan, task, Review Packet and this Fix Response. Prior verdicts are
  historical, not current authorization. Unrelated 19 tracked dirty files and
  Runtime V2 untracked files are preserved; no broad staging commands.

## Offline verification

Final focused: **178 passed**, including **17 actual historical receipt tests**.
Memory: **31 passed**. Runtime V2/Provider offline regression: **173 passed**.
TeamDecision + Golden/business: **25 passed, 1 skipped** (16 TeamDecision +9 Golden/
business). Total non-overlapping executed tests: **407 passed, 1 skipped**.
Compileall, tracked `git diff --check`, scoped new-code whitespace/conflict scan
passed. `rg` no-match exit 1 is not a whitespace failure. Seal/provenance validation
is executable in complete authority negatives and original historical audit.

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_experience_models.py tests/unit/test_experience_policy.py tests/unit/test_run_reflection.py tests/unit/test_experience_store.py tests/unit/test_experience_retrieval.py tests/unit/test_experience_evaluation.py tests/unit/test_experience_authority.py tests/unit/test_gate4_fix_boundaries.py tests/unit/test_experience_review_transition_fix.py tests/unit/test_experience_identifier_fix.py tests/unit/test_experience_historical_receipt.py tests/integration/test_experience_offline_replay.py
python -m pytest -q -p no:cacheprovider tests/unit/test_memory_models.py tests/unit/test_memory_policy.py tests/unit/test_memory_store.py tests/unit/test_memory_retriever.py tests/unit/test_memory_lifecycle.py tests/integration/test_memory_runtime.py
python -m pytest -q -p no:cacheprovider tests/unit/test_runtime_v2_model_proposal.py tests/unit/test_runtime_v2_multi_action.py tests/unit/test_runtime_v2_grounding_visibility.py tests/integration/test_runtime_v2_multi_action_resume.py tests/unit/test_deepseek_api_adapter.py tests/unit/test_p85_gemini_api_adapter.py tests/integration/test_deepseek_durable_provider.py tests/integration/test_p85_durable_provider.py tests/integration/test_m04_production_e2e_resume.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_team_decision_final_contract_communication.py tests/eval/test_golden8_freeze.py tests/smoke/test_deepseek_business_evaluation.py
python -m compileall -q src/linkloom/experience src/linkloom/evaluation/experience.py tests/experience_authority_support.py tests/unit/test_experience_historical_receipt.py tests/unit/test_gate4_fix_boundaries.py tests/unit/test_experience_review_transition_fix.py tests/unit/test_experience_identifier_fix.py
git diff --check
```

No real-Provider opt-in was set. Mid-run approval-service quota rejection and
subagent interruptions were reported; affected tests were later actually run
after its reset. They are not represented as successes of rejected calls.

## Remaining NON-BLOCKING limits

- Trusted host/bootstrap/log configuration is a trust root, not an adversarial
  host-code replacement sandbox. No request-level authority registration exists.
- Gate 4 remains offline only; no Runtime/Provider context wiring or RBAC.
- Fresh approval replay is simple local O(log length) I/O; no performance expansion.
- Historical tests skip in clean checkouts without the original sealed local roots;
  synthetic complete-result tests remain available. Both ran locally here.
- Existing pytest-asyncio deprecation warning and intentional Golden skip remain.
- Previously recorded unrelated Memory e2e `MODEL_NOT_CONFIGURED` and legacy
  hard-coded vault-steward test path are not fixed or relabeled by this repair.

## Original Reviewer request (Completed Handoff History)

Please independently read production, schemas, both receipt contracts,
Reflection eligibility, typed linkage, persisted lifecycle, retrieval/context,
all 60 new cases, original sealed sources and these documents. Do not rely only
on Builder totals. Return **APPROVE** or **BLOCK** with precise remaining findings.
Only the original task `审查 Runtime V2 Gate 1` owns this requested final verdict.
Gate 5 stays frozen until its explicit APPROVE; Builder does not declare PASS.
