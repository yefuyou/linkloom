# GATE 4 REVIEW PACKET — Current Six-BLOCKER Repair

Status: **ORIGINAL INDEPENDENT REVIEWER APPROVE — GATE 4 ACCEPTED (2026-09-16)**.

Latest user BLOCK/REJECT supersedes prior verdict below. Current contract and
evidence: `GATE4_FIX_RESPONSE.md`, SPEC Six-BLOCKER Addendum. Local focused178,
Memory31, Runtime173, Team+Golden25 passed;1 skipped. Real historical mps1/aer0/
iti0 uses separately approved pinned historical receipt without dimension
backfill. The original Reviewer task `审查 Runtime V2 Gate 1` has issued
the requested final **APPROVE**, with no unresolved BLOCKER. Its independently
saved decision is
[GATE4_ORIGINAL_REVIEWER_VERDICT.md](GATE4_ORIGINAL_REVIEWER_VERDICT.md).
Independent non-overlapping total: **407 passed, 1 skipped**; the 60 repair
cases are included in focused178. This is the current acceptance source,
not the prior r2 verdict preserved below.
Gate5 frozen; no Provider, staging, commit/push or real artifact edits.

## Prior Trust-Boundary Packet (Superseded History)

Prior packet is preserved below, not current approval. Its complete-only
historical restriction was explicitly amended for BLOCKER6. Do not infer
acceptance from the prior verdict or totals.

Status: **INDEPENDENT REVIEWER APPROVE — GATE 4 COMPLETE (2026-09-16)**

## Final Independent Verdict

Reviewer: `/root/gate4_reviewer_r2`, independent of the Builder.

**APPROVE. Findings: None. No unresolved BLOCKER remains.**

The Reviewer directly read the implementation/schema/reflection/lifecycle/
retrieval/context/evaluator/tests/fixtures/current normative packet and
independently verified:

- Ad-hoc findings, omitted dimensions/companions, and issuer-preserving receipt
  rewrites reject.
- Fabricated catalog + real receipt + recomputed canonical record rejects;
  cross-run save/forced accept rejects.
- Real receipt + forged lesson/applicability rejects at retrieval and direct
  context under full record/template policy.
- Stale Store get/list/provenance, retrieval and context reject.
- Normal sealed synthetic candidate -> explicit accept -> reload -> retrieval
  -> bounded context -> FakeModel replay passes.
- Historical partial projections remain audit-only, without backfill.
- Focused `118 passed`; adjacent Runtime V2 / Provider `173 passed`;
  `git diff --check` and untracked whitespace/conflict scan PASS.
- No Reviewer workspace mutation, Gold/Provider/network use, staging,
  commit or push.

The trusted-bootstrap pin assumption is explicit; it is not a request-supplied
catalog/authority replacement API or host-code-replacement sandbox.
Gate 4 is now complete under the user's Reviewer-APPROVE condition. Nothing is
staged/committed/pushed, and no next phase is started. Sections below preserve
the actual repair handoff and historical evidence.

Governing contract: `SPEC.md` normative Trust-Boundary Repair Addendum.
Builder/Worker implements; only the independent Reviewer owns final acceptance.
The packet below is current. Earlier repair packets are preserved as superseded
history. No commit/push, staging, Provider call, or next phase is authorized.

## 1. Previous Findings — Second-Round Formal BLOCK

### BLOCKER 1: evaluator completeness could be bypassed by omission

Original reproduction: evaluator grounding was `N/E`, but the caller supplied
only the supported semantic FAIL finding and omitted its grounding companion.
The old projector accepted the list and Reflection produced one candidate.
Checking every supplied finding was not checking the complete evaluator output.

This was a real trust-boundary failure: callers controlled which dimensions
Reflection could see. A missing contract/grounding result could be represented
as absence of a finding, making incomplete evaluation look causally usable.

Invariant violated: only complete authoritative evaluation may authorize
reflection; FAIL/N/E/BLOCKED in a required supporting dimension must prevent
generation even when the caller omits its companion.

### BLOCKER 2: provenance catalog could be fabricated with the ref

Original reproduction: caller constructed `run_id:ev_fabricated`, put the same
ref in its supplied available catalog, and the internally consistent record
passed Reflection, Policy, and Store. Membership in supplied_catalog was
mistaken for membership in sealed authoritative run evidence.

This was real: matching strings, hashes, and run prefixes do not establish who
owns the catalog. Caller-controlled ref + caller-controlled catalog could
manufacture durable, reviewable provenance.

Invariant violated: Experience refs must resolve in the corresponding sealed
run's authoritative evidence, with run/ref/source/seal/artifact binding.
Neither direct Store writes nor review/accept may manufacture that authority.

## 2. Resolution / Trust Boundary 1 — Evaluation Authority

Production symbols/files:

- `experience/authority.py`: `EvaluationAuthority.__init__`, `resolve`,
  `validate_result`, `AuthoritativeEvaluationResult.generation_allowed`.
- `evaluation/experience.py`: `EvaluationFindingProjector.project` now
  refuses the old findings/bytes/catalog arguments; Experience correctness
  and provenance evaluation require the configured authority.
- `experience/reflection.py`: `RunReflection.reflect` accepts only an issued
  authoritative result and checks complete dimension states before rule use.

The trusted evaluator-owned manifest is SHA-256 pinned at application bootstrap,
not request input. It registers observed summary, observed seal, complete
evaluation, and evaluation seal paths/digests. There is no request registration
or caller catalog API; a request may select a registered result ID only.
A receipt from another configured authority, a reconstructed receipt, or a
rewritten issued receipt is rejected.

The closed complete result requires completed=true, all six explicit dimensions
(runtime, infrastructure, provider_availability, contract, grounding, semantic),
complete non-PASS companion findings, finding/state consistency, source run
matching, observed/seal digests, and a matching evaluation seal. Its stable ID is
`evaluation_<evaluation SHA-256>`. Reads recheck the manifest and four pinned
artifacts; stale or replaced files reject.

Reflection does not guess whether the caller omitted a finding: it receives the
evaluator's full sealed result. Required supporting dimensions must all be PASS.
The existing one registered semantic FAIL may create one candidate. Supporting
FAIL/N/E/BLOCKED/PARTIAL/REVIEW_REQUIRED yields `()`; a malformed/missing
dimension or companion rejects before Reflection. All six PASS yields `()`
because there is no failure lesson, not a new success-reflection feature.

This closes omission at the input authority boundary. It is an intentional,
user-authorized local trust-contract change, not a new evaluator runner or an
expansion of Gate 4 product architecture.

## 3. Resolution / Trust Boundary 2 — Provenance Authority

Production symbols/files:

- `experience/authority.py`: `resolve` derives the catalog only from the
  registered sealed observed summary's typed `visible_evidence_refs`;
  `validate_record` re-resolves the receipt and exact provenance.
- `experience/models.py`: provenance adds `evaluation_result_id`,
  `observed_seal_sha256`, and `source_identity`. Legacy ReflectionInput is
  retained as an internal/archival shape, never an authorization token.
- `experience/policy.py`: `validate_candidate_record` requires authority;
  `validate_candidate_shape` is explicitly shape-only, not authorization.
- `experience/store.py`: save (including duplicate branch), event replay,
  reload, and review/accept revalidate authoritative provenance.
- `experience/retrieval.py` and `experience/context.py`: accepted records
  receive full record-policy plus authority validation before selection/rendering.
  Nonempty context construction requires configured authority.
- `evaluation/experience.py`: `evaluate_provenance` can no longer return
  PASS based only on an internally consistent caller projection/catalog.

Only evidence refs are supplied by the sealed evaluator finding. Metadata is
resolved by the authority from sealed artifacts, not by Reflection callers.
The persisted catalog is an audit copy and is never treated as the trust root.
Binding includes run ID, evidence ref, observed-summary digest, observed-seal
digest, complete-evaluation/result identity, and source-artifact identity.
Here source_identity is the sealed observed-summary SHA-256, not a caller
claim about note contents or source text.

Shared `policy.validate_experience_record` now checks original lifecycle shape,
canonical identity, registered template/Gold-safe policy, and authoritative
provenance. Retrieval and context assembly use it, so a real receipt cannot
authorize caller-rewritten lesson/applicability text. Store get/list/provenance
also use it before returning records; stale artifacts reject even read APIs.
Append-only raw event history is retained for audit, not claimed as fresh authority.

A fabricated catalog with a real receipt ID still fails exact re-resolution.
Cross-run refs, changed metadata, wrong seal/artifact identity, stale evidence,
and direct forged Store writes fail. Explicit review cannot override invalid
provenance, and reload checks replayed candidates/reviews again.

Both repairs stay inside the Experience/evaluation package. Runtime V2,
Provider, ToolRuntime, checkpoints, tool ledger, prompts, and Gold ownership
are unchanged. Reflection now does read-only local authority resolution;
its former no-I/O assumption is explicitly superseded. No auto mutation,
promotion, extra strategy/rule, external service, or schema-wide redesign.

### Independent checkpoint — consumer enforcement gaps

The Reviewer independently ran the previous 111 focused / 173 adjacent checks
and still reproduced (1) a canonically reconstructed accepted record with a real
receipt but caller-rewritten lesson entering retrieval/context, and (2) stale
get/list/provenance reads after the sealed observed artifact was replaced.
Receipt authenticity did not authorize record text; an old in-memory audit
record did not prove current seal validity. Both were real enforcement gaps in
Trust Boundary 2 and the registered-template/Gold-safe/context invariants.

Before these consumer edits, their regression tests produced **4 failed**;
direct forged selection bypass of context produced **1 failed**. The shared
full-record policy at retrieval, context, and Store reads closes them without
new product behavior, promotion, Runtime changes, or a new trust subsystem.

### Trust root and fixture limitations

The pin must come from trusted bootstrap configuration and must not be freshly
computed from a request's manifest. Digests prove integrity relative to that
root; this does not sandbox a host administrator replacing configuration/code.
Tests pin the static manifest at
`0eef9c9991e0bd2f096a4a70aff88a496ad3246b71487e1edeb420e3ccf91e7c`.

The new fixtures are explicitly synthetic complete evaluator/seal bundles.
They do not claim that a real evaluator or Provider was rerun. The original
three-run artifacts remain read-only audit history; incomplete historical
projections reject and are not automatically/backfilled upgraded receipts.
The source-evaluator digest in synthetic fixtures is synthetic lineage data,
not proof that an external evaluator executed.

## 4. Regression Evidence — RED to GREEN

Before production edits the exact omission reproduction, fabricated ref/catalog
reproduction, and direct Store forged-provenance proof produced **3 failed**.
The omission case reached candidate generation; the other two failed because
rejection did not occur. Additional RED: fabricated internally consistent
provenance report falsely returned PASS (**1 failed**), and malformed complete
artifact fields produced **4 failed**. After the repairs: focused **118 passed**.

Count mapping: 73 prior cases retained/migrated + 44 new authority/enforcement cases +
1 new provenance-report case = 118. Every new authority case below is in
`tests/unit/test_experience_authority.py`; parameter values enumerate cases,
not just function counts.

| Regression function / parameters | Cases | Finding -> fix -> behavior proved |
|---|---:|---|
| test_reviewer_omission_attack_does_not_generate_candidate | 1 | B1: exact old grounding N/E + omitted companion attack is rejected. |
| test_reviewer_fabricated_ref_and_catalog_are_not_authority | 1 | B2: old caller ref/catalog cannot enter Reflection. |
| test_store_direct_fabricated_provenance_is_rejected | 1 | B2: fabricated provenance cannot directly create a JSONL log. |
| test_complete_supporting_dimensions_pass_can_generate_candidate | 1 | B1/B2: complete sealed supporting PASS + supported semantic FAIL yields a candidate with resolved provenance. |
| test_ne_dimension_without_caller_companion_cannot_be_omitted [grounding, contract] | 2 | B1: complete evaluator owns companions; no request findings are supplied; either N/E prevents candidate. |
| test_complete_nonpass_supporting_dimension_blocks_generation [FAIL, N/E, BLOCKED, PARTIAL] | 4 | B1: full grounding state cannot be suppressed by a semantic finding. |
| test_missing_required_dimension_is_rejected | 1 | B1: missing contract dimension rejects, even with seals. |
| test_complete_artifact_rejects_malformed_closed_field_types [catalog_type, dimension_type, signals_type, source_digest] | 4 | B1/B2: malformed catalog/state/signal/lineage types reject under the closed complete contract. |
| test_omitted_blocking_companion_in_sealed_result_is_rejected [grounding, contract] | 2 | B1: even a sealed malformed result cannot omit its non-PASS companion. |
| test_uncompleted_evaluator_result_is_rejected | 1 | B1: completed=false cannot authorize Reflection. |
| test_caller_cannot_supply_findings_even_with_configured_authority | 1 | B1: possessing the configured authority does not open an ad-hoc findings API. |
| test_receipt_from_different_bootstrap_authority_is_rejected | 1 | B1/B2: request cannot swap authority roots by supplying another issued receipt. |
| test_caller_cannot_omit_dimension_by_rewriting_issued_receipt | 1 | B1: retaining issuer/result ID while rewriting dimensions/findings still rejects against sealed files. |
| test_decoy_outside_sealed_evidence_collection_is_not_resolvable | 1 | B2: evidence-looking text outside typed visible_evidence_refs is not catalog authority. |
| test_canonical_cross_run_provenance_cannot_be_saved_or_accepted | 1 | B2: canonically well-formed run-B refs carrying run-A receipt fail both save and forced review/accept. |
| test_manually_reconstructed_authoritative_result_is_rejected | 1 | B1: frozen/manual receipt reconstruction is not evaluator issuance. |
| test_evaluator_run_identity_mismatch_is_rejected | 1 | B1: evaluator identity must match the sealed source run. |
| test_all_dimensions_pass_abstains_without_a_semantic_failure | 1 | B1: all-PASS evaluator remains an abstention, not new lesson generation. |
| test_real_ref_with_wrong_run_is_rejected | 1 | B2: real evidence name with wrong run cannot resolve. |
| test_fabricated_ref_not_in_sealed_catalog_is_rejected | 1 | B2: sealed evaluator ref must actually exist in sealed observed evidence. |
| test_real_ref_caller_rewritten_metadata_is_rejected [source_run_id, observed_summary_sha256, observed_seal_sha256, source_identity, evaluation_result_id] | 5 | B2: every run/source/seal/artifact metadata binding rejects alteration before persistence. |
| test_fabricated_catalog_with_real_receipt_is_rejected | 1 | B2: fabricated ref + fabricated catalog + real receipt + recomputed canonical ID still fails authority validation. |
| test_catalog_changed_after_authority_bootstrap_is_rejected | 1 | B2: stale/replaced observed catalog rejects Reflection. |
| test_catalog_present_but_run_seal_mismatch_is_rejected | 1 | B2: catalog membership cannot override mismatched seal identity. |
| test_candidate_review_cannot_bypass_stale_provenance | 1 | B2: stale seal/evidence prevents accept; candidate stays candidate; reload also rejects. |

Additional checkpoint regression cases (same authority test file):

| Regression function / parameters | Cases | Behavior locked |
|---|---:|---|
| test_retrieval_revalidates_record_shape_not_only_real_receipt [reusable_lesson, applicability] | 2 | Real receipt + recomputed canonical ID cannot authorize caller-rewritten template fields. |
| test_store_reads_cannot_return_stale_authoritative_provenance [get, list, provenance] | 3 | Replaced sealed observed artifact prevents any current-record/provenance read return. |
| test_context_cannot_render_direct_caller_forged_selection | 1 | Hand-built selection cannot render forged lesson; missing authority and real-receipt/invalid-template paths reject. |
| test_context_rechecks_sealed_authority_after_retrieval | 1 | Valid selection snapshot cannot render after source seal/evidence becomes stale. |

The 45th case is
`test_provenance_report_cannot_trust_fabricated_projection_catalog` in
`test_experience_evaluation.py`: both caller-only fabricated projection and
fabricated record against the real configured result report FAIL.

Existing policy/reflection tests now reject legacy manually supplied projections
at the stronger input boundary rather than pretending they are authoritative
abstention inputs. Existing Store/retrieval/ranking/bounds tests use real issued
synthetic receipts. The one v1 template has equal relevance scores: the ranking
test now proves stable ID ties with valid templates rather than admitting an
invalid custom applicability to manufacture a score difference. Applicability
tampering is explicitly a rejection proof, not a positive ranking fixture. The two integration cases retain Gold/network spies and
normal candidate -> save -> explicit accept -> reload -> relevant bounded
retrieval/context -> FakeModel flow, plus historical-artifact audit/rejection.
The previous seven first-repair cases remain enumerated in the preserved packet.

## 5. Negative Proofs

| Attack | Exact current failure proof |
|---|---|
| Omission attack | Old list API rejects; full grounding/contract N/E result returns (); missing dimension or companion rejects; issuer-preserving receipt rewrite rejects. |
| Fabricated catalog attack | Fabricated ref/catalog cannot enter Reflection; canonical forged record using real result ID fails Store; fabricated projection cannot get provenance PASS. |
| Cross-run provenance attack | Wrong-run evaluator/ref rejects; canonical run-B record with run-A receipt cannot save or accept. |
| Wrong/stale sealed artifact | Changed observed/seal files and rewritten metadata reject; Store read/review/reload and context-after-retrieval cannot rescue stale evidence. |
| Real receipt + forged record/selection | Canonical rewritten lesson/applicability rejects at retrieval; hand-built forged selection rejects at context. |

These prove attack failure, not merely happy-path catalog self-consistency.

## 6. Current Gate 4 Invariants

- Reflection produces only zero/one candidate Experience, never accepted.
- Candidate does not automatically influence Runtime, prompts, Provider or tools.
- Explicit persisted review is the only Store lifecycle promotion path.
- Only accepted Experience is retrievable; candidate/rejected cannot enter
  selection or production/model context.
- Provenance is resolved from configured sealed authority, not supplied_catalog.
- Gold/expected answers/arrays, answer keys, case entities/lookup tables, and
  arbitrary evaluator prose do not enter Experience. Reflection does not load Gold.
- Retrieval defaults top-k=3 / max_chars=2000; hard limits 5 / 4000.
  Whole records are skipped rather than truncated.
- Ordering is deterministic: score descending, then experience_id ascending.
- Workflow/task/exclusion/pattern filtering prevents irrelevant injection.
- Context exposes only lesson, strategy, applicability; identities/provenance
  remain trace-only. Gate 4 production Runtime injection remains out of scope.
- Runtime V2 / Provider / ToolRuntime ownership is unchanged.
- No auto prompt/code/tool mutation, auto promotion, new rule/strategy feature,
  Provider/network/LLM invocation, UI, README, or vector DB.

## 7. Verification

| Class | Result | Scope |
|---|---|---|
| Previous focused baseline (historical) | 73 passed | First repair, not current trust-boundary acceptance. |
| Current focused validation | 118 passed | Models, authority, policy/projector, Reflection, Store/review, retrieval/context, evaluation, offline replay. |
| Current adjacent regression | 173 passed | Runtime V2 / fake Provider adapter / durability / resume compatibility. |
| Working-tree whitespace | git diff --check: PASS | Tracked diff; untracked Gate 4 files separately checked without staging. |

Focused command:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_experience_authority.py tests/unit/test_experience_models.py tests/unit/test_experience_policy.py tests/unit/test_run_reflection.py tests/unit/test_experience_store.py tests/unit/test_experience_retrieval.py tests/unit/test_experience_evaluation.py tests/integration/test_experience_offline_replay.py
```

Adjacent command:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_runtime_v2_model_proposal.py tests/unit/test_runtime_v2_multi_action.py tests/unit/test_runtime_v2_grounding_visibility.py tests/integration/test_runtime_v2_multi_action_resume.py tests/unit/test_deepseek_api_adapter.py tests/unit/test_p85_gemini_api_adapter.py tests/integration/test_deepseek_durable_provider.py tests/integration/test_p85_durable_provider.py tests/integration/test_m04_production_e2e_resume.py
git diff --check
```

No real-Provider opt-in is enabled. Existing pytest asyncio deprecation and Git
LF/CRLF notices are unchanged environment warnings, not failed checks.
Adjacent results do not reclassify dirty Runtime/Provider files as Gate 4-owned.
Tests are Builder evidence pointers, not Reviewer verdict.

## 8. Working Tree Hygiene

Baseline: master ahead origin/master by 7 commits, 19 tracked modified files,
17 untracked groups. Current: same ahead 7 and same 19 tracked modifications;
20 untracked groups = 14 Gate 4 + 6 unrelated. Increase is exactly the three
new Gate 4 groups authority test, test helper, and tests package marker.
Nothing staged, committed, pushed, deleted, or restored.

Gate 4-owned groups:

- docs/requirements/experience_reflection_layer/
- src/linkloom/evaluation/experience.py
- src/linkloom/experience/ (including new authority.py)
- tests/__init__.py
- tests/experience_authority_support.py
- tests/fixtures/experience_reflection_v1/ (new authority bundles; originals retained)
- tests/integration/test_experience_offline_replay.py
- tests/unit/test_experience_authority.py
- tests/unit/test_experience_evaluation.py
- tests/unit/test_experience_models.py
- tests/unit/test_experience_policy.py
- tests/unit/test_experience_retrieval.py
- tests/unit/test_experience_store.py
- tests/unit/test_run_reflection.py

All 19 tracked modifications remain unrelated prior project/Runtime V2 work,
with the exact unchanged path inventory preserved under Archived Previous
Repair Packet / Working Tree Hygiene. The six unrelated untracked groups are:

- docs/requirements/runtime_v2_multi_action/
- tasks/
- tests/integration/test_runtime_v2_multi_action_resume.py
- tests/unit/test_runtime_v2_grounding_visibility.py
- tests/unit/test_runtime_v2_model_proposal.py
- tests/unit/test_runtime_v2_multi_action.py

Do not use `git add -A` or `git add .`. Do not commit/push. Preserve working
tree until independent final verdict. No next phase even after this handoff.

## 9. Reviewer Request

请重新独立检查上一轮两个 trust-boundary findings 的修复，并给 Gate 4
最终 verdict。不要仅依据 Builder test summary，也不要把旧 archive 当成
当前 authority contract。

Read directly: production implementation, Experience schema/provenance,
complete result/authority resolver and pinned manifest, reflection planner,
review/save/reload lifecycle, retrieval path, prompt/context assembly,
regression tests (especially attacks with canonical IDs/real receipt IDs),
safe synthetic fixtures, and Gate 4 SPEC/addendum, implementation plan, task
record, this packet and relevant prior findings.

Verify issuance/configured-root ownership and completeness rather than whether
findings look complete. Verify sealed-run catalog authority rather than caller
catalog consistency. Try omission, fabricated-catalog, cross-run, stale-seal,
and lifecycle bypass independently. Read the normal complete-result path too.
No Gold, real Provider/network, code edits, staging, commit/push, or next phase.

Return only an evidence-backed final **APPROVE** (no unresolved BLOCKER) or
**BLOCK** (precise reproducible findings with file/line/test evidence).
Gate 4 is complete only after independent APPROVE.

## Learning Reflection

Worker learned that immutable data shapes and digest self-consistency do not
establish authority. The change moves trust to a bootstrap-pinned complete
evaluation and sealed-run evidence resolver. Evidence is 118 focused / 173
adjacent plus negative attacks; next action is independent verdict only.

# Archived Previous Repair Packet (Superseded)

The former 73-case handoff below was formally BLOCKED in round two. Its
architecture/completion/replay claims are historical, not current authority.

# GATE 4 REVIEW PACKET — Experience / Reflection Layer

Status: **READY FOR INDEPENDENT RE-REVIEW — NOT APPROVED**

Governing SPEC: `docs/requirements/experience_reflection_layer/SPEC.md`

Implementation owner: Worker. Acceptance owner: independent Reviewer.

The sections immediately below are the authoritative re-review handoff. The
first-round packet is retained afterward as an explicitly superseded evidence
archive. Gate 4 remains frozen until an independent Reviewer returns `APPROVE`
with no unresolved BLOCKER.

## 1. Previous Findings

### Finding 1 — Mixed incomplete findings could still produce Experience

**Original problem.** A supported semantic `FAIL` could coexist in one
`ReflectionInput` with a Provider `PARTIAL` finding or a grounding `N/E`
finding, and `RunReflection` could still emit an Experience from the semantic
item. The input as a whole therefore did not need to be one unambiguous, fully
evaluable causal finding.

**Why it was real.** Provider failure before a Final and unevaluated grounding
are evidence gaps, not semantic lessons. The semantic-looking item did not make
the mixed input safe, so an incomplete run could become a reusable rule.

**Invariant affected.** Reflection must conservatively abstain unless it has
one causally specific, supported semantic finding. Provider-only,
infrastructure-only, mixed, ambiguous, or `N/E` evidence must not create an
Experience.

### Finding 2 — Provenance was syntactic and the evaluator artifact was not bound to the finding

**Original problem.** Evidence resolution only checked that a ref string began
with the source run ID. A fabricated ref such as
`run_p4_961cf695f6f8:ev_not_an_observation` could therefore look run-local
without appearing in the sealed observed evidence. Evaluator bytes were used
only to compute SHA-256; the projector did not establish that they were
parseable, used a registered schema, matched the observed case/schema, or
supported the declared finding code, dimension, and outcome.

**Why it was real.** A prefix proves naming shape, not referential integrity. A
digest proves which bytes were supplied, not that those bytes substantiate the
finding. A caller could construct apparently hash-bound provenance around an
orphan ref or unrelated evaluator artifact.

**Invariant affected.** Every Experience must have closed, resolvable
provenance to the sealed observed run and to the evaluator artifact that
supports its causal finding. Reflection must not accept decoy evidence or
arbitrary artifact hashes as semantic proof.

## 2. Resolution

### Finding 1 resolution

Direct production changes:

- `src/linkloom/experience/reflection.py` — `RunReflection.reflect` now
  requires exactly one finding. It must be the registered
  `explicit_rejection_requires_evidence/v1` semantic `FAIL`; any companion
  Provider, grounding, unresolved, unknown, or additional finding makes the
  method return `()`.
- `src/linkloom/evaluation/experience.py` —
  `evaluate_reflection_correctness` now reports
  `ambiguous_or_mixed_findings` when the input is not a single causal finding.

This closes the issue because a semantic item can no longer override an
incomplete companion finding. The deterministic result for a mixed input is
abstention. This is a local fail-closed correction; it does not enlarge or
change the approved Gate 4 architecture.

### Finding 2 resolution

Direct production changes:

- `src/linkloom/experience/models.py` — `ReflectionInput` now carries the
  evaluator artifact SHA-256, evaluator schema version, and authoritative
  `available_source_evidence_refs` projected from the sealed observed summary.
  `ExperienceProvenance` persists that schema and catalog, and requires every
  selected source ref to be a catalog member.
- `src/linkloom/evaluation/experience.py` —
  `EvaluationFindingProjector.project`, `_load_object`,
  `_typed_visible_evidence_ids`, and `_validate_evaluator_support` now parse
  the observed summary, seal, and evaluator artifact; use only the typed
  top-level `visible_evidence_refs`; require registered evaluator and observed
  schemas; bind evaluator/observed case identity; and verify that artifact
  content supports the finding's code, dimension, and outcome contract.
- `src/linkloom/experience/reflection.py` — `RunReflection.reflect` now binds
  the finding hash to the input evaluator hash, checks the evaluator schema
  allowed for the generation rule, and requires selected refs to be a subset
  of the authoritative evidence catalog.
- `src/linkloom/experience/policy.py` — `validate_candidate_record` now
  replays the complete closed record contract, checks the generation-rule /
  evaluator-schema mapping, and rejects tampered provenance or identity.

`ExperienceStore.save` already called `validate_candidate_record`, so its
persistence gate inherits the stronger validation without a direct edit to
`store.py` or a new Store architecture.

This closes the issue because prefix-only or decoy refs, malformed or
wrong-case evaluators, unsupported schemas, and evaluator artifacts that do
not substantiate the finding now fail before persistence or reuse. The changes
only strengthen the approved projection, provenance, reflection, and
persistence contracts. They add no Provider call, database, Runtime state,
prompt mutation, retrieval architecture, or parallel evaluator.

## 3. Regression Evidence

The focused Gate 4 count increased from `66` to `73`. These are the seven new
pytest cases and the behavior each locks:

| # | Finding | Regression case | Behavior locked |
|---|---|---|---|
| 1 | Finding 1 | `test_supported_semantic_finding_mixed_with_incomplete_layer_abstains` — Provider `PARTIAL` parameter | Semantic `FAIL` plus `provider_transient_before_final/v1` returns `()`; a Provider incident cannot become semantic Experience. |
| 2 | Finding 1 | `test_supported_semantic_finding_mixed_with_incomplete_layer_abstains` — grounding `N/E` parameter | Semantic `FAIL` plus unevaluated grounding returns `()`; incomplete grounding cannot be ignored. |
| 3 | Finding 2 | `test_projection_rejects_unidentified_or_mismatched_evaluator_artifact` — malformed-bytes parameter | Non-JSON/unidentified evaluator bytes cannot become hash-only provenance. |
| 4 | Finding 2 | `test_projection_rejects_unidentified_or_mismatched_evaluator_artifact` — wrong-case parameter | A parseable evaluator with another case identity cannot support the finding. |
| 5 | Finding 2 | `test_projection_ignores_decoy_evidence_outside_typed_visible_collection` | Evidence-looking data outside top-level `visible_evidence_refs` is not resolvable evidence. |
| 6 | Finding 2 | `test_same_run_prefix_does_not_make_an_orphan_evidence_ref_valid` | A matching run prefix is insufficient; the exact ref must exist in the authoritative catalog. |
| 7 | Finding 2 | `test_store_revalidates_source_evidence_resolution_before_persisting` | Provenance tampered after construction is rejected before the JSONL store is created or appended. |

The existing
`test_local_sealed_artifact_hashes_match_safe_projection_fixtures` integration
test was also strengthened without increasing the test count. It now reruns
the projector against all three local sealed artifact families and requires
the result to equal the checked-in safe projection, including evaluator
schema/hash and the typed evidence catalog.

## 4. Current Gate 4 Invariants

- Reflection creates zero or one `candidate` Experience only.
- Reflection cannot create `accepted` / `rejected` status or promote itself.
- Candidate Experience does not automatically affect Runtime, Provider
  behavior, tool selection, prompts, or production context.
- Only a persisted explicit review decision can change candidate lifecycle
  status.
- Only `accepted` Experience can be retrieved.
- Candidate and rejected Experience cannot enter `ExperienceSelection` or
  model-visible context.
- Experience provenance includes the source run, observed-summary digest,
  evaluator digest/schema, finding IDs, selected evidence refs, and the
  authoritative available-evidence catalog.
- Gold fields, expected answers/arrays, answer keys, benchmark/case/workspace
  IDs, selected entities, and copied evaluator prose do not enter Experience.
- Retrieval defaults to `top_k=3`, `max_chars=2000`, with hard ceilings of
  `5` / `4000`; whole records are skipped rather than truncated.
- Ranking is deterministic: score descending, then `experience_id` ascending.
- Workflow, task-characteristic, exclusion, and optional pattern filters keep
  irrelevant Experience out of the selection.
- Model-visible context contains only the explicit `Relevant prior experience`
  section with lesson, strategy, and applicability. IDs, hashes, run IDs, and
  evidence refs remain trace-only.
- Runtime V2, Provider adapters, ToolRuntime, the tool ledger, policy
  ownership, checkpoints, TeamDecision, prompts, and Gold ownership are
  unchanged by Gate 4.
- Gate 4 contains no automatic prompt, source-code, tool-description, tool,
  Provider, or Runtime mutation path.

## 5. Verification

| Validation class | Scope | Result | Meaning |
|---|---|---|---|
| Focused validation | Gate 4 models, policy/projector, reflection, lifecycle/store, retrieval/context, evaluation, and offline replay | `73 passed` | Includes the seven Reviewer regression cases above. |
| Adjacent regression | Runtime V2, fake Provider adapters, durability/recovery, grounding visibility, and resume | `173 passed` | Confirms the local Gate 4 fix did not change adjacent Runtime V2 / Provider / ToolRuntime ownership or behavior. |
| Working-tree integrity | `git diff --check` | PASS | No whitespace-error diff was introduced. |

`73 passed` is the focused Gate 4 claim. `173 passed` is adjacent
compatibility evidence, not a claim that the Runtime V2 dirty files belong to
Gate 4. Existing unrelated working-tree changes are preserved and are not
reclassified as Gate 4 evidence. The Builder's summaries are evidence
pointers, not the acceptance decision.

## 6. Working Tree Hygiene

Current state at packet refresh:

- `master...origin/master [ahead 7]`;
- `19` tracked modified files;
- `17` untracked content groups from `git status --short`;
- nothing has been staged, committed, or pushed for Gate 4.

Gate 4 owns these 11 untracked groups:

- `docs/requirements/experience_reflection_layer/`
- `src/linkloom/evaluation/experience.py`
- `src/linkloom/experience/`
- `tests/fixtures/experience_reflection_v1/`
- `tests/integration/test_experience_offline_replay.py`
- `tests/unit/test_experience_evaluation.py`
- `tests/unit/test_experience_models.py`
- `tests/unit/test_experience_policy.py`
- `tests/unit/test_experience_retrieval.py`
- `tests/unit/test_experience_store.py`
- `tests/unit/test_run_reflection.py`

All 19 tracked modifications are unrelated prior project / Runtime V2 work and
are not Gate 4-owned:

- `AGENTS.md`
- `src/linkloom.egg-info/SOURCES.txt`
- `src/linkloom/agents/model_adapter.py`
- `src/linkloom/agents/providers/deepseek_api.py`
- `src/linkloom/agents/providers/gemini_api.py`
- `src/linkloom/agents/retrieval_agent.py`
- `src/linkloom/runtime/artifacts.py`
- `src/linkloom/runtime/graph.py`
- `src/linkloom/runtime/model_loop.py`
- `src/linkloom/runtime/models.py`
- `src/linkloom/runtime/recovery.py`
- `tests/integration/test_deepseek_durable_provider.py`
- `tests/integration/test_m04_production_e2e_resume.py`
- `tests/integration/test_p85_durable_provider.py`
- `tests/smoke/deepseek_business_harness.py`
- `tests/smoke/test_deepseek_business_evaluation.py`
- `tests/smoke/test_m04_real_provider_smoke.py`
- `tests/unit/test_deepseek_api_adapter.py`
- `tests/unit/test_p85_gemini_api_adapter.py`

The other 6 untracked groups are unrelated prior work:

- `docs/requirements/runtime_v2_multi_action/`
- `tasks/`
- `tests/integration/test_runtime_v2_multi_action_resume.py`
- `tests/unit/test_runtime_v2_grounding_visibility.py`
- `tests/unit/test_runtime_v2_model_proposal.py`
- `tests/unit/test_runtime_v2_multi_action.py`

Do not use `git add -A` or `git add .`. Do not commit at this stage. Preserve
the current working tree until the independent Reviewer issues the final Gate
4 verdict.

## 7. Reviewer Request

Please independently re-check both previous findings and issue the final Gate
4 verdict. Do not rely only on the Builder's test summary.

Read and trace directly:

- production implementation under `src/linkloom/experience/` and
  `src/linkloom/evaluation/experience.py`;
- Experience schema and provenance contracts in `models.py`;
- reflection planner in `reflection.py`;
- review lifecycle and persistence in `store.py` and `policy.py`;
- accepted-only retrieval in `retrieval.py`;
- prompt/context assembly in `context.py` and the offline FakeModel consumer;
- the seven regression cases and strengthened sealed-artifact replay;
- Gate 4 `SPEC.md`, `implementation_plan.md`, `task.md`, and this packet.

Confirm specifically that finding 1 now always abstains on mixed/incomplete
evidence and finding 2 now has resolvable observed evidence plus evaluator-
content binding through projection, reflection, candidate validation, and
Store persistence.

Return one final verdict: `APPROVE` or `BLOCK`. If `BLOCK`, give precise,
reproducible findings with file/line or test evidence. If `APPROVE`, state that
there is no unresolved BLOCKER. Do not modify code, commit, push, or begin the
next phase.

## Learning Reflection

- **Role:** Worker handoff to independent Reviewer.
- **What changed:** this packet now records both prior findings, the exact
  production fixes, all seven regression cases, current invariants, and clean
  Gate 4 ownership boundaries.
- **What was learned:** hashing an artifact and prefix-checking a reference are
  not provenance; safe reflection needs both causal completeness and
  referential closure before a candidate is eligible for review.
- **Evidence:** Gate 4 `73 passed`, Runtime V2 `173 passed`, and `git diff
  --check` PASS.
- **Next step:** independent final `APPROVE` or `BLOCK`; Gate 4 remains
  unapproved until then.

# Archived First-Round Packet (Superseded)

The material below preserves the original `66 passed` handoff for audit
history. Its old verification totals and Reviewer request are superseded by
the authoritative re-review sections above.

## A1. Failure Patterns

| Pattern | Evidence verdict | Gate 4 disposition |
|---|---|---|
| Non-selected / compared / superseded is not explicit rejection | Supported by `mps-001` | One generic candidate through `explicit_rejection_requires_evidence/v1` |
| Preserve direct-question scope | Plausible but not independently isolated | No Experience in v1 |
| Do not invent missing owner/deadline/decision/rejection | Useful boundary but not a new causal finding | No Experience in v1 |
| Historical/intermediate/final authority | No observed failure in the reviewed cases | No Experience |
| Natural multi-tool proposals | Runtime architecture evidence only | Excluded from semantic Experience |

No pattern was created to meet a quota.

## 2. Source Evaluator Findings

| Run | Layered result | Safe finding | Result |
|---|---|---|---|
| `run_p4_961cf695f6f8` (`mps-001`) | Runtime/contract/grounding succeeded; semantic rejected-alternative field failed | `explicit_rejection_requires_evidence/v1`, evaluator SHA-256 `29a1aea4f2e5a6e24c70858ab7d14fd61c4891ba8afac963e0d658ee89ff297d` | one candidate |
| `run_p4_dd10d1b9eee7` (`aer-002`) | Runtime continuation evidence succeeded; Provider availability partial; semantic/grounding Final N/E | `provider_transient_before_final/v1`, manifest SHA-256 `9e326e332d240248ee7d4386e9f6ec6c8b411b07e8d722c4cb12bf9f44ce9083` | `()` |
| `run_p4_b63d23ad517d` (`iti-005`) | Runtime/contract/grounding pass; semantic mismatch cause unresolved | `semantic_mismatch_cause_unresolved/v1`, evaluator SHA-256 `4659968edf56411768ca3277d9742c30b7e981107e9262ad5c67b982906d27c6` | `()` |

The safe fixtures contain no `case_id`, expected field, Gold value, answer key,
workspace ID, or benchmark-to-answer mapping. Local verification hashes raw
evaluator bytes but does not parse their Gold-aware contents.

## 3. Experience Schema

`ExperienceRecord` contains the required semantic, applicability, lifecycle,
source-run, and evidence fields plus:

- `generation_rule_id` for registered-template enforcement;
- `provenance` entries binding source run, observed-summary SHA-256,
  evaluator-artifact SHA-256, finding IDs, and evidence refs.

`experience_id` is the SHA-256 of canonical semantic/provenance content. It
excludes `created_at` and `status`, so explicit review does not change identity.
All model contracts use exact-field `from_dict` validation and immutable
tuples.

## 4. Reflection Flow

```text
safe ReflectionInput
  -> registered finding and applicability checks
  -> causal/evaluator-hash/evidence consistency checks
  -> registered generic template
  -> candidate ExperienceRecord or ()
```

`RunReflection` is pure apart from an injected clock. It opens no file, calls
no network, reads no Gold, invokes no model, and emits at most one v1 record.
Semantic text is selected from the template registry; no run or evaluator
prose is interpolated.

## 5. Abstention Behavior

Reflection returns `()` for:

- Provider/infrastructure-only incidents;
- semantic or grounding `N/E`;
- `REVIEW_REQUIRED` / unresolved causal mismatch;
- unknown finding code;
- missing evidence refs;
- refs belonging to another run;
- mixed evaluator-artifact hashes;
- ambiguous semantic findings;
- task characteristics outside the registered applicability.

The offline replay proves `aer-002 -> ()` and `iti-005 -> ()`.

## 6. Provenance

Every emitted record binds:

- source run ID;
- observed-summary SHA-256;
- evaluator artifact SHA-256;
- evaluator finding ID;
- source evidence refs.

The Store preserves this provenance through candidate, accepted, and rejected
states. The retriever returns trace provenance separately from model-visible
text. Local-only integration validation also compares all three safe fixture
hashes to the sealed `.artifacts` roots; clean CI may skip only that local
artifact-resolution assertion while still executing the self-contained replay.

## 7. Gold Isolation

Controls:

- production Experience code imports neither `tests.*`, Gold loaders,
  providers, nor Runtime;
- projection parses only observed summary and its Gold-free seal;
- evaluator bytes are opaque and used only for SHA-256;
- closed payload policy rejects expected/Gold/answer-key/case/workspace fields,
  benchmark IDs, absolute paths, and non-JSON values;
- only registered generic template text may be stored;
- filesystem spies reject known Gold paths through reflection, storage,
  retrieval, context building, and replay;
- safe fixture scan reports no forbidden field tokens.

## 8. Retrieval Behavior

`ExperienceRetriever`:

1. considers `accepted` only;
2. requires workflow match;
3. requires explicit task-characteristic overlap;
4. applies `exclude_when` as a hard filter;
5. applies optional pattern-type matching;
6. sorts by score descending, then `experience_id` ascending;
7. never uses case IDs, answer text, free-text entity similarity, embeddings,
   or an LLM judge.

Relevant final-decision/comparison queries retrieve the accepted rule.
Action-inbox, Provider-incident, unrelated-signal, and
`explicit_rejection_evidence_present` queries do not.

## 9. Bounded Context Strategy

- defaults: `top_k=3`, `max_chars=2000`;
- hard ceilings: `top_k<=5`, `max_chars<=4000`;
- widening attempts raise an error;
- whole records are skipped rather than truncated;
- exact rendered length is carried in `ExperienceSelection` and revalidated by
  `ExperienceContextBuilder`;
- model text contains only the literal heading `Relevant prior experience`,
  reusable lesson, strategy, and applicability boundary;
- IDs, hashes, run IDs, and evidence refs remain trace-only.

## 10. Production Diff

New production files:

- `src/linkloom/experience/__init__.py`
- `src/linkloom/experience/models.py`
- `src/linkloom/experience/policy.py`
- `src/linkloom/experience/reflection.py`
- `src/linkloom/experience/store.py`
- `src/linkloom/experience/retrieval.py`
- `src/linkloom/experience/context.py`
- `src/linkloom/evaluation/experience.py`

New tests and safe fixtures match the approved SPEC file list. No existing
Runtime V2, Agent, Provider, prompt, TeamDecision, Gold, evaluation-case,
sealed-artifact, or smoke-harness file was modified by Gate 4. Those paths had
pre-existing Gate 3 dirty work at WP-0 and remain outside Gate 4 ownership.

The implementation adds no dependency, database, embedding, vector service,
MCP, UI, auth, LangGraph, or production multi-agent architecture.

## 11. Tests

Recorded results:

| Scope | Result |
|---|---|
| Gate 4 models/policy/reflection/store/retrieval/evaluation/replay | `66 passed` |
| Adjacent Memory | `31 passed` |
| Formal Evaluation + Trajectory | `34 passed` |
| Runtime V2 + Gemini/DeepSeek fake adapters + resume | `173 passed` |
| TeamDecision vertical/communication | `16 passed` |
| Golden freeze + business evaluation | `9 passed, 1 skipped` |
| Changed-file `compileall` | PASS |
| `git diff --check` | PASS |
| forbidden-import / fixture-leakage / whitespace / conflict-marker scans | PASS |

Total non-overlapping focused results: **329 passed, 1 skipped**.

The skipped test is the explicitly opt-in live smoke path; no Provider opt-in
was set. A separate legacy `tests/test_evaluation_runner.py` was not counted:
it predates Gate 4 and hard-codes the absent `D:/webproject/vault-steward`
repository. Current formal evaluation tests listed above pass.

## 12. Example Candidate Experience

```json
{
  "status": "candidate",
  "generation_rule_id": "explicit_rejection_requires_evidence/v1",
  "pattern_type": "evidence_semantics",
  "reusable_lesson": "Comparison, non-selection, or supersession does not by itself prove explicit rejection.",
  "suggested_strategy": "Populate rejected semantics only when the cited evidence explicitly establishes rejection; otherwise omit the claim or preserve uncertainty.",
  "applicability": {
    "workflows": ["team_decision"],
    "task_characteristics": ["current_decision", "compared_options"],
    "exclude_when": ["explicit_rejection_evidence_present"]
  }
}
```

No benchmark ID, entity, expected answer, or copied evaluator prose appears in
the semantic record.

## 13. Runs Intentionally Producing `[]`

- `aer-002`: Provider transient occurred before a Final; Runtime evidence is
  preserved, but semantic Experience is forbidden.
- `iti-005`: semantic mismatch exists, but current evaluator output does not
  identify one causally specific reusable lesson.
- unit cases additionally cover semantic `N/E`, unknown rule, orphan evidence,
  missing evidence, mixed evaluator hashes, ambiguous findings, and unrelated
  task characteristics.

## 14. Known Limitations

- v1 has one semantic generation rule and intentionally low recall.
- Reflection is deterministic/template-based; there is no free-form reflective
  model.
- Post-hoc evaluator artifacts are hash-bound but are not independently sealed;
  observed summaries are sealed.
- Safe fixtures preserve hashes and findings, not full source artifacts; local
  artifact resolution is an additional check and may skip in clean CI.
- Experience is proven only through offline/FakeModel context consumption. It
  is not wired into production Runtime or prompts in Gate 4.
- Review promotion is explicit event recording. Gate 4 does not decide that a
  candidate deserves acceptance and has no automatic promotion path.
- The unrelated legacy hard-coded evaluation test remains outside scope.

Requested Reviewer decision: return `APPROVE` only if there is no unresolved
BLOCKER for leakage, hardcoding, provenance, abstention, relevance, context
bounds, parallel-evaluation duplication, Runtime/prompt mutation, or automatic
promotion. Otherwise return precise BLOCKER findings without modifying code.
