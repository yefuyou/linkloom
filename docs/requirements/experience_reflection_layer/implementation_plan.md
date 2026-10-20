# Implementation Plan: Gate 4 Experience / Reflection Layer

Status: **ORIGINAL INDEPENDENT REVIEWER APPROVE — GATE 4 ACCEPTED (2026-09-16)**

The latest user directive supersedes prior acceptance: RED -> GREEN for the
six explicit BLOCKERs, offline regression, GATE 4 FIX RESPONSE, then original
Reviewer re-review. Gate 5, Runtime/Provider/prompts/Gold/real artifacts freeze.
Prior authority repair below remains history and is preserved, not approval.

Latest increment: six-BLOCKER RED -> shared eligibility/linkage/IDs/persisted
review/final bounds -> explicitly approved distinct historical receipt ->
178 focused /31 Memory /173 Runtime /25 Team+Golden passed,1 skipped ->
GATE4_FIX_RESPONSE.md -> original Reviewer final APPROVE, no unresolved BLOCKER.
The decision is recorded in
[GATE4_ORIGINAL_REVIEWER_VERDICT.md](GATE4_ORIGINAL_REVIEWER_VERDICT.md),
not Builder self-acceptance. No Gate 5 work is authorized; it remains frozen.

## Prior Authority Repair Increment (History)

Only the two user-authorized trust boundaries are in scope. The SPEC's
normative authority addendum supersedes older projection/no-I/O/historical
replay work-package assumptions below.

1. RED: reproduce grounding N/E omission, fabricated ref + caller catalog,
   and direct Store fabricated provenance. All three failed before production
   edits. Additional RED exposed a false PASS provenance report and malformed
   complete-artifact types.
2. GREEN: add read-only, bootstrap-pinned `EvaluationAuthority`; resolve full
   sealed dimensions/companions/run/evaluation identity before Reflection;
   resolve provenance only from the corresponding sealed run evidence.
3. Migrate Reflection, Policy, Store save/reload/read/review, accepted retrieval,
   context assembly and Experience evaluation to the same configured authority. No caller
   registration API. Preserve archival shapes as unauthoritative data.
4. Prove normal complete-result lifecycle with synthetic sealed fixtures,
   and negative omission/fabricated-catalog/cross-run/stale-seal paths.
5. Gate 4 focused: 118 passed. Runtime V2 / Provider adjacent: 173 passed.
   Refresh Review Packet and request independent APPROVE/BLOCK. No staging,
   commit, push, Provider call, or next phase.

Independent checkpoint found receipt-only retrieval allowed a canonical
caller-rewritten lesson, and Store read APIs returned stale evidence. Both
were reproduced RED (4 failed), plus direct forged context selection RED
(1 failed). Shared full-record policy now guards retrieval/context and Store
reads; seven additional enforcement cases are included in the 118 total.

Current exact commands and per-test proof mapping are in
`GATE4_REVIEW_PACKET.md`. Previous work packages are retained as history,
not permission to broaden this repair.

## Overview

Implement the approved Gate 4 boundary as a small provider-neutral package.
Existing evaluators remain authoritative; the new layer accepts only a
Gold-free finding projection, deterministically reflects or abstains, stores
reviewed lifecycle events locally, retrieves accepted relevant experience, and
builds a bounded explicit context section for offline replay.

No work package may change Runtime V2, providers, prompts, TeamDecision, Gold,
or existing evaluation behavior.

## Architecture Decisions

1. Add `linkloom.experience` as a separate domain namespace; do not overload
   personal `MemoryStore` or create another Evaluation Runner.
2. Reuse existing evaluator outputs through a narrow safe projection rather
   than importing test harnesses or Gold loaders into production.
3. Make v1 reflection deterministic and template-registered. This is stricter
   than free-form reflection but gives a provable Gold-isolation boundary.
4. Default to abstention. Generic mismatch, ambiguous cause, Provider-only
   failure, and semantic `N/E` produce no Experience.
5. Use append-only JSONL event history patterned after `MemoryStore`, with an
   Experience-specific schema and explicit review decision.
6. Retrieve accepted records only using structured applicability signals. No
   free-text entity match, LLM judge, embedding, or vector service.
7. Keep model-visible context separate from trace-only provenance and prove it
   offline. Do not wire production Runtime injection in Gate 4.
8. Extend `linkloom.evaluation` with deterministic Experience checks; do not
   fork its run lifecycle or datasets.

## Dependency Graph

```text
closed models + policy/template registry
                 |
                 +--> evaluator finding projection
                 |            |
                 |            v
                 +------> RunReflection
                 |            |
                 v            v
          append-only Store <- candidate records
                 |
          explicit review event
                 |
                 v
       structured Retrieval + Context Builder
                 |
                 v
        Experience evaluation checks
                 |
                 v
      sealed-artifact offline replay proof
                 |
                 v
       compatibility regression + review packet
```

## TDD Work Packages

### WP-0 — Evidence and boundary snapshot

**Description:** Freeze the Planner-approved file boundary, record current
dirty work, hash the three source observed/evaluator artifacts, and capture the
existing focused regression baseline. Do not copy Gold values or modify sealed
roots.

**Acceptance criteria:**

- [ ] `git status --short` records and preserves unrelated user changes.
- [ ] Source run IDs, observed seals, evaluator hashes, and available layered
      outcomes are recorded without expected values.
- [ ] No Provider opt-in environment variable or network client is used.

**Verification:** Read-only artifact hash script; existing focused Runtime V2
and evaluation tests.

**Dependencies:** Human approval of this SPEC and plan.

**Files likely touched:** `task.md` evidence section only.

**Estimated scope:** S.

### WP-1 — RED: closed contracts and leakage policy

**Description:** Add failing tests for `EvaluatorFinding`, `ReflectionInput`,
`ExperienceApplicability`, `ExperienceRecord`, `ExperienceReviewDecision`,
`ExperienceQuery`, `ExperienceSelection`, and context/provenance contracts.

**Acceptance criteria:**

- [ ] Unknown/missing/wrong-type fields, invalid enums, invalid hashes/IDs,
      duplicate refs, and non-JSON values are rejected.
- [ ] Candidate identity is deterministic and independent of status/time.
- [ ] Case IDs, expected/Gold fields, answer-key shapes, copied target/entity
      values, unregistered templates, and absolute paths fail closed.

**Verification:**
`python -m pytest -q -p no:cacheprovider tests/unit/test_experience_models.py tests/unit/test_experience_policy.py`

**Dependencies:** WP-0.

**Files likely touched:** two test files.

**Estimated scope:** S.

### WP-2 — GREEN: models, policy, and safe finding projection

**Description:** Implement the minimum closed contracts, template registry,
leakage validation, observed-seal verification, and allowlisted evaluator
finding projection required by WP-1.

**Acceptance criteria:**

- [ ] Production code has no `tests.*` or Gold-loader import.
- [ ] Projection verifies artifact identity/hashes and omits evaluator-only
      fields and arbitrary prose.
- [ ] Only registered finding codes and opaque run/evidence refs cross the
      boundary.

**Verification:** WP-1 tests GREEN plus evaluation isolation regressions.

**Dependencies:** WP-1.

**Files likely touched:**

- `src/linkloom/experience/models.py`
- `src/linkloom/experience/policy.py`
- `src/linkloom/experience/__init__.py`
- `src/linkloom/evaluation/experience.py`

**Estimated scope:** M.

### Checkpoint A — Contract and Gold boundary

- [ ] WP-1/2 tests pass.
- [ ] Static import/filesystem spies prove zero Gold access.
- [ ] Reviewer-readable contract examples contain no benchmark target.
- [ ] No forbidden production file changed.

### WP-3 — RED: reflection correctness and abstention

**Description:** Add failing tests for the first registered rule and all
mandatory abstention paths.

**Acceptance criteria:**

- [ ] Supported explicit-rejection finding produces one candidate.
- [ ] Provider transient, semantic `N/E`, generic mismatch, missing/orphan
      evidence, unknown rule, ambiguous cause, and the current `iti-005` safe
      projection produce `()`.
- [ ] Reflection cannot emit accepted/rejected or copy finding/source prose.

**Verification:**
`python -m pytest -q -p no:cacheprovider tests/unit/test_run_reflection.py`

**Dependencies:** Checkpoint A.

**Files likely touched:** one test file and safe input fixtures.

**Estimated scope:** S.

### WP-4 — GREEN: deterministic `RunReflection`

**Description:** Implement registered rule matching, evidence preconditions,
canonical record creation, deduplication, and abstention.

**Acceptance criteria:**

- [ ] `reflect()` is pure apart from injected clock and performs no I/O.
- [ ] Text is selected from the registered template; no source string is
      interpolated.
- [ ] Zero or one record is produced for current v1 inputs, always candidate.

**Verification:** WP-3 GREEN and contract/policy regressions.

**Dependencies:** WP-3.

**Files likely touched:** `src/linkloom/experience/reflection.py`.

**Estimated scope:** S.

### WP-5 — RED: store lifecycle and provenance

**Description:** Add failing tests for append-only save/list/get, explicit
review decisions, provenance lookup, reload, duplicates, collision, torn tail,
and rejected/candidate visibility.

**Acceptance criteria:**

- [ ] Save accepts only candidate records with valid registered generation.
- [ ] Candidate-to-accepted/rejected requires an explicit actor/source/review
      evidence; no confidence/replay shortcut exists.
- [ ] Reload reconstructs identical current state and full history.

**Verification:**
`python -m pytest -q -p no:cacheprovider tests/unit/test_experience_store.py`

**Dependencies:** WP-2.

**Files likely touched:** one test file.

**Estimated scope:** S.

### WP-6 — GREEN: append-only Experience store

**Description:** Implement Experience-specific JSONL events using the proven
local persistence mechanics from `MemoryStore` without changing Memory.

**Acceptance criteria:**

- [ ] Appends are flushed/fsynced and replay deterministically.
- [ ] Identical save is idempotent; ID/payload collision fails closed.
- [ ] Rejected records remain queryable for audit and are never deleted.

**Verification:** WP-5 GREEN plus existing memory-store regressions.

**Dependencies:** WP-5.

**Files likely touched:** `src/linkloom/experience/store.py`.

**Estimated scope:** S.

### Checkpoint B — Candidate lifecycle

- [ ] Reflection and store suites pass together.
- [ ] `aer-002` and `iti-005` abstention remains explicit.
- [ ] No automatic promotion path exists.
- [ ] Append-only log stays outside Vault roots in tests.

### WP-7 — RED: relevance, applicability, and bounded context

**Description:** Add failing positive/negative retrieval tests, status
filtering, deterministic tie ordering, hard-budget rejection, whole-record
budget behavior, and context/provenance separation.

**Acceptance criteria:**

- [ ] An accepted comparison/final-decision rule is retrieved for a matching
      structured query.
- [ ] Unrelated, Provider-incident, and explicit-rejection-evidence-present
      queries receive no such rule.
- [ ] Candidate/rejected records never enter model-visible context; top-k and
      character ceilings cannot be widened by caller input.

**Verification:**
`python -m pytest -q -p no:cacheprovider tests/unit/test_experience_retrieval.py`

**Dependencies:** Checkpoint B.

**Files likely touched:** one test file.

**Estimated scope:** S.

### WP-8 — GREEN: deterministic retrieval and explicit context

**Description:** Implement structured hard filters, stable scoring/order,
bounded whole-record selection, and the `Relevant prior experience` renderer.

**Acceptance criteria:**

- [ ] Retrieval uses only pattern/applicability/task metadata.
- [ ] Model-visible text contains lesson, strategy, and applicability only.
- [ ] Experience/run/evidence IDs and hashes remain trace-only metadata.

**Verification:** WP-7 GREEN plus memory-retriever regression.

**Dependencies:** WP-7.

**Files likely touched:**

- `src/linkloom/experience/retrieval.py`
- `src/linkloom/experience/context.py`

**Estimated scope:** S.

### WP-9 — RED/GREEN: Experience evaluation extension

**Description:** Add deterministic checks for correctness, leakage,
generalizability, relevance, provenance, and bounded context inside the
existing evaluation namespace.

**Acceptance criteria:**

- [ ] Every dimension reports its own status/evidence; no single score hides a
      Provider or semantic distinction.
- [ ] Case-specific lookup, missing provenance, unsupported template,
      irrelevant retrieval, and context overflow each fail the correct
      dimension only.
- [ ] No new runner, Gold corpus, Provider, judge, or embedding is introduced.

**Verification:**
`python -m pytest -q -p no:cacheprovider tests/unit/test_experience_evaluation.py`

**Dependencies:** WP-4, WP-6, WP-8.

**Files likely touched:**

- `tests/unit/test_experience_evaluation.py`
- `src/linkloom/evaluation/experience.py`

**Estimated scope:** S.

### Checkpoint C — Experience-layer behavior

- [ ] Unit suites for model/policy/reflection/store/retrieval/evaluation pass.
- [ ] All evaluation dimensions remain separate.
- [ ] No benchmark target appears in record or context snapshots.
- [ ] File impact remains inside the approved boundary.

### WP-10 — Offline sealed-run replay proof

**Description:** Add safe hash-bound projections of the three historical runs,
explicit review-decision fixtures, and one end-to-end offline replay test.

**Acceptance criteria:**

- [ ] The sealed `mps-001` projection creates one candidate, persists it,
      records explicit acceptance, retrieves it for a relevant task, and builds
      bounded context with complete provenance.
- [ ] The sealed `aer-002` Provider transient and current `iti-005` generic
      mismatch projection both produce `()`.
- [ ] Irrelevant replay receives no Experience, and filesystem spies prove
      zero Gold/network/Provider/judge access throughout.

**Verification:**
`python -m pytest -q -p no:cacheprovider tests/integration/test_experience_offline_replay.py`

**Dependencies:** Checkpoint C.

**Files likely touched:**

- `tests/integration/test_experience_offline_replay.py`
- `tests/fixtures/experience_reflection_v1/`

**Estimated scope:** S.

### WP-11 — Compatibility and Gate 4 evidence

**Description:** Run adjacent and broader regressions, audit the production
diff, generate examples/intentional abstentions, and prepare the Gate 4 Review
Packet. Do not run any Provider.

**Acceptance criteria:**

- [ ] Runtime V2, TeamDecision, grounding, Gemini, DeepSeek, cold resume,
      memory, formal evaluation, and trajectory evaluation focused suites pass.
- [ ] `compileall` and `git diff --check` pass.
- [ ] Review packet contains all 14 requested sections and exact commands.

**Verification:** Commands below and independent Reviewer inspection.

**Dependencies:** WP-10.

**Files likely touched:** `task.md` and a Gate 4 review packet document only.

**Estimated scope:** S.

## Planned Verification Commands

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_experience_models.py tests/unit/test_experience_policy.py tests/unit/test_run_reflection.py tests/unit/test_experience_store.py tests/unit/test_experience_retrieval.py tests/unit/test_experience_evaluation.py tests/integration/test_experience_offline_replay.py
python -m pytest -q -p no:cacheprovider tests/unit/test_memory_models.py tests/unit/test_memory_policy.py tests/unit/test_memory_store.py tests/unit/test_memory_retriever.py tests/unit/test_memory_lifecycle.py tests/integration/test_memory_runtime.py
python -m pytest -q -p no:cacheprovider tests/test_evaluation_runner.py tests/unit/test_evaluation_models.py tests/unit/test_evaluation_dataset.py tests/unit/test_evaluation_metrics.py tests/unit/test_evaluation_isolation.py tests/unit/test_evaluation_bad_cases.py tests/integration/test_formal_evaluation.py tests/integration/test_formal_evaluation_contract.py tests/integration/test_evaluation_safety.py tests/integration/test_trajectory_evaluation.py
python -m pytest -q -p no:cacheprovider tests/unit/test_runtime_v2_model_proposal.py tests/unit/test_runtime_v2_multi_action.py tests/unit/test_runtime_v2_grounding_visibility.py tests/integration/test_runtime_v2_multi_action_resume.py tests/unit/test_deepseek_api_adapter.py tests/unit/test_p85_gemini_api_adapter.py tests/integration/test_deepseek_durable_provider.py tests/integration/test_p85_durable_provider.py tests/integration/test_m04_production_e2e_resume.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_team_decision_final_contract_communication.py
python -m pytest -q -p no:cacheprovider tests/eval/test_golden8_freeze.py tests/smoke/test_deepseek_business_evaluation.py
python -m compileall -q src/linkloom tests
git diff --check
git status --short
```

No command may set a real-Provider opt-in environment variable.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Gold-aware finding becomes answer memory | Critical | Closed safe projection, registered templates, no arbitrary prose interpolation, filesystem spy |
| Case-specific rule disguised as generalization | High | Rule triggers on finding/task characteristics, never case/workspace ID; negative cross-task tests |
| Provider transient becomes semantic lesson | High | Layer-aware precondition and mandatory abstention test |
| Grounding pass is treated as semantic pass | High | Separate dimension contract and evaluator checks |
| Personal Memory and Experience lifecycles blur | Medium | Separate namespace/schema/store; reuse mechanics only |
| Context grows without bound | High | Accepted-only hard filter, hard 5/4000 ceilings, whole-record accounting |
| Hidden prompt contamination | High | Explicit context object/heading, trace metadata separate, offline consumer only |
| Evaluator finding cause is too generic | High | Abstain unless registered causal finding and supporting refs exist |
| Sealed local artifacts are unavailable in clean CI | Medium | Commit only Gold-free hash-bound projections; local audit additionally resolves original roots |
| Post-hoc evaluator artifact is not independently sealed | Medium | Hash it in projection and bind it to observed case/run identity; state limitation in review packet |

## Stop Rules

Stop and request a new decision if implementation would require:

- modifying a forbidden Runtime/Agent/Provider/TeamDecision file;
- reading or copying Gold into Reflection or Experience;
- free-form LLM reflection/relevance;
- a new external dependency, database, embedding, Provider, or network call;
- production prompt or system-prompt mutation;
- more than the single approved v1 generation rule without new evidence;
- automatic promotion;
- a real Vault path or mutation.

## Planner Checkpoint

- [x] Every work package has objective acceptance and verification.
- [x] Dependencies are ordered contract -> reflection/store -> retrieval ->
      evaluation -> replay -> regression.
- [x] Work packages are S/M and avoid shared-file parallel implementation.
- [x] Checkpoints protect Gold isolation, lifecycle, context, and compatibility.
- [x] Human has reviewed and approved the SPEC and plan.
- [x] Worker role has begun implementation.

## Learning Reflection

The plan places the highest-risk work—closed input and leakage policy—before
storage or retrieval. It intentionally accepts fewer lessons rather than
forcing a semantic explanation from incomplete evaluator evidence.
