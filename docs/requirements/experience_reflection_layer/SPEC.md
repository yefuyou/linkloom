# SPEC: Gate 4 Experience / Reflection Layer

Status: **ORIGINAL INDEPENDENT REVIEWER APPROVE — GATE 4 ACCEPTED (2026-09-16)**

The latest explicit user six-BLOCKER repair directive supersedes the prior
acceptance entry and historical verdict below. Gate 5 is frozen. Only Gate 4
eligibility, supporting-observation linkage, opaque IDs, persisted review,
final context bounds, and three-case disposition may change. No Provider,
Runtime V2, prompt, Gold, TeamDecision or real artifact changes are authorized.
The original Reviewer task `审查 Runtime V2 Gate 1` issued the required new
APPROVE with no unresolved BLOCKER. Its independently saved decision is
[GATE4_ORIGINAL_REVIEWER_VERDICT.md](GATE4_ORIGINAL_REVIEWER_VERDICT.md).
This records Gate 4 acceptance only; Gate 5 remains frozen and unauthorized.

## Normative Six-BLOCKER Repair Addendum

`GATE4_FIX_RESPONSE.md` maps all six findings and RED/GREEN evidence. Local
focused178 was Builder verification; the separate original Reviewer verdict
supplies final acceptance. This addendum remains the latest contract.

- Shared Reflection/save eligibility requires terminal typed owners, valid
  authority/seals, allowed supporting outcomes, known Provider outcome, one
  causal basis, model-observed linked refs, no exclusion or Gold signal.
- Full source ID grammar/hint/path rejection covers models, both authorities,
  and full lifecycle policy including review metadata.
- Record.create is candidate-only. Terminal status requires original identity,
  candidate/review event IDs, explicit actor/source/time/evidence decision and
  hashed Store identity. Retrieval/context freshly replay persisted approval.
- Selection/context max5, configured final Builder item/character bounds and
  final serialization lesson-count validation protect upstream bypasses.
- User explicitly approved a minimal pinned historical-review receipt after
  complete-only migration broke the required real 1/0/0. This supersedes only
  the earlier complete-only historical restriction. HistoricalReviewAuthority/
  HistoricalReviewResult validate unchanged observed/seal/evaluator/review hashes,
  exact original states, terminal/Provider guards, existing TeamDecision owner
  checks and refs linked to actual Final semantics, verified model-called tool
  results and upstream review checks. Missing dimensions stay absent; no new
  evaluator or semantic scoring. mps candidate1; aer/iti abstain.
- Historical locators/review prose stay private to trusted bootstrap resolution;
  only opaque IDs/hashes enter Experience. Post-seal evaluator Gold access is
  not observed leakage; no Gold loader/expected payload reaches Reflection.
  Receipt creation time does not invent the original review timestamp.
  Historical candidate acceptance still needs separate persisted review.

No production Runtime wiring, Provider, Gold/real artifact mutation, new rule,
prompt mutation, promotion loop, RBAC or Gate5. Prior addendum continues except
where explicitly amended above.

## Normative Trust-Boundary Repair Addendum

The user explicitly authorized only Evaluation Authority and Provenance
Authority after the second-round BLOCK. This addendum supersedes the old
caller-projected finding input, hash-only authority, no-I/O reflection, and
historical replay acceptance contracts below. Other product scope, lifecycle,
Gold isolation, budgets, and forbidden-file boundaries are unchanged.

`sealed observed run -> complete evaluator result -> evaluation seal ->
configured EvaluationAuthority -> AuthoritativeEvaluationResult -> Reflection`.

- Trust root: evaluator-owned manifest and its SHA-256 pinned in trusted
  application bootstrap configuration. Request callers may select only a
  registered result ID; they may not register artifacts, replace the authority,
  supply arbitrary findings/catalogs, or recompute the trusted pin on load.
- The closed complete result must declare `completed: true`, explicit states
  for runtime/infrastructure/provider_availability/contract/grounding/semantic,
  and all non-PASS companion findings. It binds source run, observed digest,
  observed seal digest, and stable `evaluation_<sha256>` identity. Its own seal
  must match. Missing dimensions/companions or inconsistent identity reject.
- Reflection consumes only a receipt issued by its configured authority and
  re-resolves it before use. Supporting dimensions must all be PASS; the one
  existing registered semantic FAIL may create one candidate. An all-PASS
  evaluation has no failure lesson and abstains. FAIL/N/E/BLOCKED/PARTIAL or
  REVIEW_REQUIRED supporting dimensions cannot be hidden by omission.
- Evidence catalog comes only from the pinned sealed observed run's typed
  `visible_evidence_refs`; finding refs are resolved there, never in a caller
  catalog. Provenance adds result identity, observed-seal digest, and
  source-artifact identity (`source_identity` = observed-summary SHA-256).
  Run/ref/source/seal/evaluation bindings are rechecked at save, reload,
  get/list/provenance reads, review/accept, retrieval, context assembly, and
  provenance evaluation. Retrieval/context validate full closed identity,
  registered template and Gold-safe policy, not receipt membership alone.
- `ReflectionInput` remains an internal/archival data shape, not an authority
  token. A manually constructed frozen object is not proof of evaluation.
- Authority resolution performs read-only local artifact I/O; no Gold loader,
  raw evaluator answer payload, Provider, model, network, or mutation is used.
  Pins protect against request-level fabricated catalogs and stale files; this
  is not a sandbox against replacement of trusted bootstrap configuration or
  application code by a host administrator.
- Existing historical three-run artifacts are immutable audit evidence, not
  automatically upgraded receipts. Missing evaluator dimensions are not
  inferred/backfilled. Normal lifecycle proof uses explicitly synthetic sealed
  complete-result fixtures, not a claim of historical production replay.

Added file responsibility is limited to `experience/authority.py`, the
authority tests/helper/fixtures, and necessary Experience-layer consumer
contract migration. No Runtime V2/Provider/ToolRuntime ownership changes,
parallel evaluator, new reflection rule, promotion, or product feature.

## Problem

LinkLoom can execute durable provider-neutral Agent runs and already evaluates
runtime, contract, grounding, semantic outcome, Provider availability, and
trajectory behavior through existing evaluation paths. It does not yet have a
small, auditable mechanism that turns a sufficiently supported evaluator
finding into reusable experience, preserves provenance and review state, and
retrieves only relevant accepted experience for a later task.

The missing capability is not self-modifying code or automatic prompt tuning.
It is the bounded loop:

```text
Completed or sealed Run
  -> existing evaluator output
  -> Gold-free finding projection
  -> deterministic reflection or abstention
  -> candidate ExperienceRecord
  -> append-only local store and explicit review
  -> deterministic relevant retrieval
  -> explicit bounded context section for offline replay
```

## Goals

1. Reuse existing evaluation artifacts and classifications as the only
   upstream evidence; do not build another Agent evaluation runner.
2. Define a strict, provider-neutral `ExperienceRecord` with durable
   provenance and an explicit candidate/accepted/rejected lifecycle.
3. Implement deterministic `RunReflection` that may return zero records and
   cannot promote its own output.
4. Prevent benchmark answers, expected values, Gold-only data, case lookup
   tables, and copied case-specific entities from entering experience.
5. Store and replay experience locally with append-only audit history.
6. Retrieve only accepted, applicable records under hard item and character
   budgets with deterministic ordering.
7. Render an explicit `Relevant prior experience` section for offline replay
   without modifying a system prompt or production Runtime.
8. Add Experience-layer correctness, leakage, generalizability, relevance,
   provenance, and bounded-context checks as extensions of the existing
   evaluation package.

## Non-Goals

- No production prompt, system prompt, tool description, source code, or skill
  mutation.
- No automatic promotion, confidence-threshold promotion, or replay-based
  promotion.
- No online learning, reinforcement loop, self-rewriting Agent, or automatic
  code generation.
- No new Provider request, LLM reflection, LLM relevance judge, embedding,
  vector database, external database, graph database, or external retrieval
  service.
- No Runtime V2 redesign, parallel tool execution, provider-adapter change,
  TeamDecision schema change, UI redesign, MCP, auth, or LangGraph migration.
- No new Gold corpus, expected-answer dataset, benchmark memory, or parallel
  evaluation runner.
- No real Vault read or mutation. Sealed synthetic artifacts are read-only.
- No production Runtime injection in Gate 4. The explicit context section is
  proven through an offline consumer/FakeModel boundary first.

## Existing Evaluation Inventory

Gate 4 treats existing evaluation as upstream evidence and reuses its contracts
instead of duplicating them.

### Formal relation evaluation

| Capability | Existing owner | Direct Gate 4 reuse |
|---|---|---|
| Inference/Gold separation | `src/linkloom/evaluation/dataset.py` exposes `InferenceDataset` without Gold and loads Gold only through `EvaluationDataset` | Preserve the same phase boundary: Reflection receives a safe finding projection, never a Gold loader or expected field |
| Deterministic metrics | `src/linkloom/evaluation/metrics.py` | Reuse the pure-function style and explicit zero/denominator semantics |
| Bad-case taxonomy | `src/linkloom/evaluation/models.py`, `bad_cases.py` | Reuse structured category/severity/finding patterns; do not reduce all layers to one PASS/FAIL |
| Payload and filesystem isolation | `src/linkloom/evaluation/isolation.py` | Reuse safe-payload, source snapshot, and mutation-detection concepts; add Experience-specific leakage rules |
| Stable artifacts/reporting | `src/linkloom/evaluation/reporting.py`, `runner.py` | Reuse JSON-safe, deterministic artifact-writing conventions; do not invoke `FormalEvaluationRunner` for Experience |
| Malformed/noisy scenarios | `FormalEvaluationRunner` mock scenarios and unit/integration tests | Reuse their test intent for malformed Experience and irrelevant/noisy retrieval cases |

`FormalEvaluationRunner` is relation-dataset-specific. It remains unchanged and
is not generalized into a new all-purpose runner during Gate 4.

### Agent trajectory evaluation

`src/linkloom/evaluation/trajectory/` already provides:

- closed versioned case, observation, result, event, assertion, termination,
  and attribution contracts;
- deterministic assertion handlers and a registered failure attribution;
- independent metrics that are not computed from one aggregate result;
- capability gating and explicit `NOT_IMPLEMENTED` rather than false failure;
- a six-artifact offline run with `provider=none`, `judge=NOT_EVALUATED`, and
  no Gold or network access.

Gate 4 should copy these architectural properties—closed schemas, registered
rules, deterministic projections, separate dimensions, and explicit
not-evaluated states—not its trajectory dataset or runner.

### TeamDecision and Runtime V2 evaluation

The real TeamDecision evidence path currently lives in the smoke harnesses and
their sealed artifacts:

- `tests/smoke/test_m12_real_provider_team_decision_smoke.py` seals a Gold-free
  observed result and loads Gold only afterward in the post-hoc evaluator.
- `tests/smoke/deepseek_business_harness.py` preserves separate
  infrastructure, contract, grounding, decision/scope/uncertainty, and
  Provider-availability evidence.
- `docs/requirements/m1_team_decision_eval_seed/` owns the frozen Golden 8;
  inference may not read it.
- `docs/requirements/runtime_v2_multi_action/GATE3_REAL_EVALUATION.md` records
  the reviewed layered disposition for the latest `aer-002` and `iti-005`
  runs.

These post-hoc evaluators are test-specific rather than a stable production
API. Gate 4 therefore adds a narrow allowlist projection from already-produced
evaluator findings into a Gold-free `ReflectionInput`. Production Experience
code must not import `tests.*`, parse Golden 8, or reproduce TeamDecision
scoring.

### Run, artifact, trace, and local persistence primitives

- `src/linkloom/runtime/checkpoint.py` persists replayable Runtime state.
- `src/linkloom/runtime/artifacts.py` provides write-once, hash-verified model
  request/response/result artifacts.
- `src/linkloom/observability/reader.py` validates trace manifests and event
  sequences; `observability/redaction.py` keeps traces deliberately lossy.
- Real smoke artifacts add `observed_summary.json`, its SHA-256 seal, and a
  post-hoc evaluator result.
- `src/linkloom/memory/store.py` demonstrates append-only JSONL events, fsync,
  deterministic replay, lifecycle history, and safe torn-tail handling.
- `src/linkloom/memory/retriever.py` demonstrates bounded deterministic local
  ranking, but its free-text/value matching and full-value return are not safe
  enough for Experience injection.

Gate 4 reuses the persistence mechanics and bounded-ranking shape, but keeps
Experience in its own typed namespace because personal Memory and reviewed
Agent Experience have different schemas, statuses, provenance, and injection
rules.

## Phase 1 Failure Pattern Analysis

### Evidence roots

- `mps-001`:
  `.artifacts/deepseek_real_provider_smoke/mps-001-745eb207a01449a98e39ee23b9b4ed1e/mps-001`
  (`run_p4_961cf695f6f8`)
- `aer-002`:
  `.artifacts/deepseek_business_evaluation/run-gate3-authorized-20260916-aer-002-01/aer-002`
  (`run_p4_dd10d1b9eee7`)
- `iti-005`:
  `.artifacts/deepseek_business_evaluation/run-gate3-authorized-20260916-iti-005-01/iti-005`
  (`run_p4_b63d23ad517d`)

### Case classification

| Case | Observed behavior | Existing evaluator finding | Layered classification | Experience disposition |
|---|---|---|---|---|
| `mps-001` | The run completed after one retrieval turn and a Final. It selected the authoritative current decision correctly, but populated explicit rejection semantics from evidence that established comparison, non-selection, or supersession only. | Post-hoc: infrastructure `PASS`, contract-valid result, grounding `PASS`, semantic `FAIL`; `correctness.rejected_alternatives=false`. The accepted M1.2 review records the root semantic error as `non-selected / comparison / superseded -> rejected`. Supporting observed refs include the comparison and supersession evidence used by the Final. | Infrastructure success; Provider success; retrieval sufficient; temporal/current-authority selection correct; question scope expanded into optional rejected semantics; unsupported semantic inference present; contract and grounding pass do not imply semantic pass. | **Produce one candidate** for explicit-rejection evidence semantics. This is the only currently supported reusable Experience. |
| `aer-002` | Three Provider responses produced 1/2/1 tool-call proposals and four ordered durable results; a fourth request ended with `MODEL_TRANSIENT_FAILURE / unknown_provider_outcome` before another proposal or Final. | Gate 3: Runtime evidence `PASS`; Provider availability `PARTIAL`; Runtime continuation contract `PASS`; Final contract, grounding, and semantic outcome `N/E`. No semantic post-hoc file exists. | Runtime multi-action success followed by Provider availability failure. Planning/retrieval progressed, but question scope, temporal synthesis, unsupported inference, uncertainty, grounding, and semantic outcome are not evaluable without a Final. | **Return `[]`.** A Provider incident must not become an Agent semantic lesson. Pattern E is retained as Runtime architecture evidence only. |
| `iti-005` | Two ordered searches were durable and the next turn returned a grounded Final. The answer preserved missing owner/deadline uncertainty and did not hit forbidden claims, but represented the outcome with extra decision/rejection semantics. | Post-hoc: infrastructure, contract, and grounding `PASS`; decision status/value exact matches are false; forbidden-claim hits are empty; uncertainty is `partial`; overall `POSTHOC_REVIEW_REQUIRED`. | Provider and Runtime success; retrieval sufficient; no proven unsupported owner/deadline claim; uncertainty present; semantic mismatch exists. The current evaluator output does not isolate whether the cause is question-scope expansion, decision-status semantics, or another representation difference. | **Return `[]` in v1.** Patterns B/C are plausible, but the current finding is not causally specific enough to justify a durable generalized lesson without inventing one. |

### Candidate-pattern verdict

| Candidate | Verdict | Reason |
|---|---|---|
| A — non-selected/compared/superseded is not explicit rejection | **Supported; create one candidate Experience** | The sealed `mps-001` Final, supporting evidence, field-level evaluator mismatch, and accepted review finding identify the exact semantic overreach without needing a benchmark answer. |
| B — direct questions should preserve question scope | **Partially supported; no separate Experience yet** | `mps-001` and `iti-005` both expanded optional fields, but only the former has a precise accepted root-cause finding, already captured more specifically by A. The `iti-005` evaluator does not isolate scope as the cause. |
| C — missing owner/deadline/decision/rejection must not be invented | **Observed as a useful boundary; no new Experience yet** | `iti-005` avoided forbidden claims and preserved uncertainty. That is positive evidence, but the current post-hoc result does not independently validate a distinct new lesson. It may become an applicability/limit of a future accepted rule. |
| D — distinguish historical/intermediate/final authority | **Not established as a failure pattern** | `mps-001` selected the authoritative current decision correctly. The corpus contains this pressure, but no current evaluator finding proves an authority-ordering failure. |
| E — models naturally emit multiple independent tool calls | **Supported architecture lesson; excluded from semantic Experience** | Gate 3 proves the Runtime fact. Injecting it into later semantic tasks would not improve evidence interpretation and would mix architecture evidence with Agent decision guidance. |

### Proposed v1 candidate Experience

The first implementation may deterministically emit this semantic content from
the registered `explicit_rejection_requires_evidence/v1` rule. It must remain
`candidate` until an explicit review decision is recorded.

```json
{
  "situation": "A decision task uses a corpus containing compared, non-selected, or superseded alternatives alongside a current decision.",
  "observed_outcome": "The response applied explicit rejection semantics even though the cited evidence established comparison, non-selection, or supersession only.",
  "pattern_type": "evidence_semantics",
  "reusable_lesson": "Comparison, non-selection, or supersession does not by itself prove explicit rejection.",
  "suggested_strategy": "Populate rejected semantics only when the cited evidence explicitly establishes rejection; otherwise omit the claim or preserve uncertainty.",
  "applicability": {
    "workflows": ["team_decision"],
    "task_characteristics": ["current_decision", "compared_options"],
    "exclude_when": ["explicit_rejection_evidence_present"]
  },
  "counterexamples_or_limits": [
    "When an authoritative source explicitly records rejection and the cited evidence supports it, rejected semantics are appropriate."
  ],
  "source_run_ids": ["run_p4_961cf695f6f8"],
  "source_evidence_refs": [
    "run_p4_961cf695f6f8:ev_p1_0016",
    "run_p4_961cf695f6f8:ev_p1_0018"
  ],
  "confidence": "high",
  "status": "candidate"
}
```

The stored record also receives `schema_version`, deterministic
`experience_id`, `created_at`, and `generation_rule_id`. It contains no case
ID, expected value, selected entity, expected array, or Gold-only field.

## Gate 4 Gap Analysis

The repository lacks only these Experience-specific components:

1. A closed Gold-free `ReflectionInput` and `EvaluatorFinding` projection that
   references sealed runs and evaluator outputs by hash without forwarding
   expected values or evaluator-only payloads.
2. `ExperienceRecord`, applicability, review-decision, query, selection, and
   context-section contracts.
3. An Experience-specific policy that allows only registered generalized
   templates in v1, rejects benchmark/case identifiers and forbidden
   evaluation fields, and never copies run text or evaluator summaries.
4. Deterministic reflection rules with strong evidence preconditions and
   explicit abstention for Provider-only, infrastructure-only, generic, or
   causally ambiguous findings.
5. A local append-only Experience store with explicit review events,
   provenance lookup, and replayable status.
6. Deterministic accepted-only retrieval with applicability filters, strict
   top-k/character limits, and stable tie-breaking.
7. An explicit context renderer that separates model-visible lesson content
   from trace-only provenance.
8. Small Experience-layer evaluators added to the existing evaluation package,
   plus safe replay fixtures projected from sealed historical artifacts.

It does **not** lack another general Evaluation Runner, new Runtime state,
model/provider support, or semantic judge.

## Architecture Boundary

```text
sealed observed result + evaluator result
              |
              v
EvaluationFindingProjector
  - verifies observed seal and artifact identity
  - allowlists layered outcomes and safe finding codes
  - emits hashes/opaque refs, never expected values
              |
              v
RunReflection (registered deterministic rules)
  - checks evidence and causal specificity
  - returns [] or candidate ExperienceRecord(s)
              |
              v
ExperiencePolicy -> ExperienceStore (append-only events)
              |
      explicit review decision
              |
              v
accepted ExperienceRecord(s)
              |
              v
ExperienceRetriever -> ExperienceContextBuilder
  - hard applicability filter
  - deterministic rank
  - top-k and character budget
  - model text separated from provenance
              |
              v
offline replay/FakeModel consumer only
```

Existing evaluator code owns correctness judgments. The projector only narrows
and normalizes already-produced findings. `RunReflection` does not read Gold,
re-score the task, inspect expected fields, or invoke a model.

## Proposed Contracts

### `EvaluatorFinding` and `ReflectionInput`

```python
@dataclass(frozen=True)
class EvaluatorFinding:
    finding_id: str
    dimension: Literal[
        "runtime", "contract", "grounding", "semantic",
        "infrastructure", "provider_availability"
    ]
    outcome: Literal["PASS", "FAIL", "PARTIAL", "N/E", "REVIEW_REQUIRED"]
    code: str
    source_evidence_refs: tuple[str, ...]
    evaluator_artifact_sha256: str

@dataclass(frozen=True)
class ReflectionInput:
    schema_version: Literal["reflection-input/v1"]
    source_run_id: str
    observed_summary_sha256: str
    findings: tuple[EvaluatorFinding, ...]
    task_characteristics: tuple[str, ...]
```

The projection rejects `case_id`, `expected_*`, Gold fields, answer keys,
expected arrays, and arbitrary evaluator prose. A finding code is useful only
if registered. A generic boolean semantic mismatch without a registered causal
finding is insufficient and leads to abstention.

### `ExperienceRecord`

```python
@dataclass(frozen=True)
class ExperienceRecord:
    experience_id: str
    schema_version: Literal["experience-record/v1"]
    situation: str
    observed_outcome: str
    pattern_type: str
    reusable_lesson: str
    suggested_strategy: str
    applicability: ExperienceApplicability
    counterexamples_or_limits: tuple[str, ...]
    source_run_ids: tuple[str, ...]
    source_evidence_refs: tuple[str, ...]
    confidence: Literal["low", "medium", "high"]
    created_at: str
    status: Literal["candidate", "accepted", "rejected"]
    generation_rule_id: str
```

`experience_id` is SHA-256 over the canonical immutable semantic/provenance
payload, excluding `created_at` and `status`, so review status changes do not
change identity. `confidence` is descriptive evidence strength only; it never
promotes or selects a record by itself.

### `RunReflection`

```python
class RunReflection:
    def reflect(self, evidence: ReflectionInput) -> tuple[ExperienceRecord, ...]: ...
```

Rules:

1. Output status is always `candidate`.
2. Provider/infrastructure-only findings return `()`.
3. `N/E` semantic or grounding outcomes cannot trigger semantic experience.
4. Unknown finding codes, missing evidence, orphan refs, generic mismatch, or
   ambiguous causal attribution return `()`.
5. v1 output prose comes only from a reviewed template registry. Run text,
   entity names, evaluator prose, case IDs, and expected values are never
   interpolated.
6. Duplicate semantic/provenance payloads collapse to one deterministic ID.

### `ExperienceStore`

```python
class ExperienceStore:
    def save(self, record: ExperienceRecord) -> ExperienceRecord: ...
    def get(self, experience_id: str) -> ExperienceRecord | None: ...
    def list(self, status: str | None = None) -> tuple[ExperienceRecord, ...]: ...
    def record_review(self, decision: ExperienceReviewDecision) -> ExperienceRecord: ...
    def provenance(self, experience_id: str) -> ExperienceProvenance: ...
```

The store uses an append-only JSONL event log, fsync, deterministic replay, and
torn-final-line handling patterned after `MemoryStore`. `save` accepts only a
candidate produced by a registered rule. `record_review` requires an explicit
actor, source (`human`, `independent_reviewer`, or
`deterministic_validator`), decision evidence ref, and accepted/rejected
choice. It records a review event; it does not decide or auto-promote.

### Retrieval and context

```python
class ExperienceRetriever:
    def retrieve(
        self,
        query: ExperienceQuery,
        *,
        top_k: int = 3,
        max_chars: int = 2000,
    ) -> ExperienceSelection: ...

class ExperienceContextBuilder:
    def build(self, selection: ExperienceSelection) -> ExperienceContextSection: ...
```

Hard ceilings are `top_k <= 5` and `max_chars <= 4000`. Retrieval:

1. selects `accepted` only;
2. filters by workflow, required task characteristics, and `exclude_when`;
3. requires at least one explicit applicability signal overlap;
4. scores registered pattern match and signal overlap only—never benchmark ID,
   source run, answer text, free-text entity overlap, embedding, or LLM judge;
5. sorts by score descending then `experience_id` ascending;
6. stops before either item or rendered-character budget is exceeded.

The model-visible section contains only `reusable_lesson`,
`suggested_strategy`, and a compact applicability boundary under the literal
heading `Relevant prior experience`. Experience IDs, run IDs, evidence refs,
and hashes are returned separately for trace/audit and are not placed in model
text.

## Promotion and Lifecycle

- Reflection creates `candidate` only.
- Confidence never changes status.
- Replay success never changes status.
- Retrieval never changes status.
- `accepted` or `rejected` requires a persisted explicit review decision.
- Rejected records remain auditable and are not injected.
- Candidate records are visible for review but are not injected.
- Gate 4 implements no automatic review loop.

## Gold-Safety Rules

1. Production Experience modules must not import a Gold loader or `tests.*`.
2. `ReflectionInput` is a closed allowlist and rejects expected/Gold fields.
3. v1 reflection text comes from registered generic templates only.
4. No benchmark ID, workspace ID, expected answer, expected array, selected
   entity, case-specific target, or evaluator-only raw payload is stored.
5. Source provenance uses opaque run/evidence IDs and hashes.
6. Context rendering omits provenance IDs and never includes observed answers.
7. Tests spy on filesystem access to prove generation, storage, retrieval,
   context construction, and replay never open Golden 8 or relation Gold.
8. Safe replay fixtures are allowlisted projections of already-sealed outputs;
   they contain findings and hashes, not expected values.

## Failure Modes

| Failure | Required behavior |
|---|---|
| Observed seal missing or mismatched | Reject projection; no Experience write |
| Evaluator artifact missing/mismatched | Return explicit unavailable finding; Reflection abstains |
| Provider transient without semantic Final | Preserve Provider classification; Reflection returns `()` |
| Generic semantic mismatch without causal code | Reflection returns `()` |
| Evidence ref is not present in the source run | Reject record before store |
| Forbidden field, benchmark ID, copied target, or unregistered template | Gold/leakage validation fails closed |
| Reflection emits accepted/rejected | Reject before store |
| Duplicate identical candidate | Idempotently return existing record |
| Same ID with different payload | Reject as integrity error |
| Torn final store event | Ignore only the torn tail during reload; preserve earlier state |
| Candidate/rejected record requested for injection | Filter out |
| No applicability overlap | Return no result |
| Requested budget exceeds hard ceiling | Reject query rather than silently widening it |
| One record exceeds remaining context budget | Skip it; never truncate semantic text mid-record |

## Experience-Layer Evaluation

Gate 4 extends `src/linkloom/evaluation` with deterministic checks; it does not
create another general Runner.

| Dimension | Objective check |
|---|---|
| Reflection correctness | Registered rule prerequisites, source finding, source run, and evidence refs all match; ambiguous inputs abstain |
| Gold leakage | Closed schema, forbidden-field scan, registered-template equality, no benchmark IDs/entities, and filesystem spy proving zero Gold reads |
| Generalizability | Rule is not a case lookup; applicability has positive signals and explicit limits; semantic text contains no source-specific target |
| Retrieval relevance | A matching final-decision/comparison task retrieves the accepted rule; unrelated retrieval and Provider-incident tasks do not |
| Provenance | Run ID, observed seal hash, evaluator artifact hash, finding ID, and evidence refs resolve consistently |
| Bounded context | Hard top-k and character ceilings, accepted-only status, whole-record rendering, deterministic ordering |

These results remain separate. A grounding pass is not converted into semantic
pass, and a Provider failure is not converted into semantic failure.

## Offline Replay Acceptance Criteria

- [ ] A safe projection of the sealed `mps-001` finding produces exactly one
      candidate through `explicit_rejection_requires_evidence/v1`.
- [ ] The candidate contains no case ID, target answer, expected value/array,
      Gold-only field, or copied source entity.
- [ ] Safe projections of `aer-002` and `iti-005` both produce `()` for the
      current v1 rules.
- [ ] Provider-only/infrastructure-only findings cannot trigger a semantic
      candidate.
- [ ] Candidate and rejected records are never selected for model context.
- [ ] After an explicit independent-review fixture accepts the generic record,
      a matching final-decision/comparison query retrieves it.
- [ ] An unrelated task and an `explicit_rejection_evidence_present` task do
      not retrieve it.
- [ ] The rendered context has the explicit heading and contains only lesson,
      strategy, and applicability boundary; trace provenance remains separate.
- [ ] Store growth cannot exceed hard top-k/character injection limits and
      produces deterministic selection order.
- [ ] Every selected record resolves to its source run, finding, seal/evaluator
      hashes, and source evidence refs.
- [ ] Filesystem spies prove zero Gold reads across projection output,
      reflection, storage, retrieval, context building, and replay.
- [ ] Existing Runtime V2, TeamDecision, grounding, Gemini, DeepSeek, cold
      resume, formal evaluation, and trajectory evaluation regressions pass.
- [ ] No Provider request, judge request, real Vault read/write, prompt
      mutation, code mutation, commit, push, or remote state change occurs.

## Expected File Impact

### New production files after approval

- `src/linkloom/experience/__init__.py`
- `src/linkloom/experience/authority.py`
- `src/linkloom/experience/models.py`
- `src/linkloom/experience/policy.py`
- `src/linkloom/experience/reflection.py`
- `src/linkloom/experience/store.py`
- `src/linkloom/experience/retrieval.py`
- `src/linkloom/experience/context.py`
- `src/linkloom/evaluation/experience.py`

### New tests/fixtures after approval

- `tests/unit/test_experience_models.py`
- `tests/unit/test_experience_authority.py`
- `tests/experience_authority_support.py`
- `tests/__init__.py`
- `tests/unit/test_experience_policy.py`
- `tests/unit/test_run_reflection.py`
- `tests/unit/test_experience_store.py`
- `tests/unit/test_experience_retrieval.py`
- `tests/unit/test_experience_evaluation.py`
- `tests/integration/test_experience_offline_replay.py`
- `tests/fixtures/experience_reflection_v1/` containing only safe, hash-bound
  finding projections and explicit review decisions—no Gold or expected data.

### Planning files in this round

- `docs/requirements/experience_reflection_layer/SPEC.md`
- `docs/requirements/experience_reflection_layer/implementation_plan.md`
- `docs/requirements/experience_reflection_layer/task.md`

### Explicitly forbidden modifications

- `src/linkloom/runtime/artifacts.py`
- `src/linkloom/runtime/checkpoint.py`
- `src/linkloom/runtime/graph.py`
- `src/linkloom/runtime/model_loop.py`
- `src/linkloom/runtime/models.py`
- `src/linkloom/runtime/recovery.py`
- `src/linkloom/agents/model_adapter.py`
- `src/linkloom/agents/providers/deepseek_api.py`
- `src/linkloom/agents/providers/gemini_api.py`
- `src/linkloom/agents/retrieval_agent.py`
- `src/linkloom/agents/runtime_adapter.py`
- `src/linkloom/agents/coordinator.py`
- `src/linkloom/agents/team_decision.py`
- all existing prompts, Golden datasets, eval cases, sealed artifacts, and
  Provider smoke harnesses.

If implementation proves that production injection requires any forbidden
file, stop and request a new architecture decision. Do not hide the change in a
wrapper or query mutation.

## Permission and Safety

- All inputs are synthetic sealed artifacts or safe projections.
- Store and replay output roots must be outside every Vault root.
- The store is local and append-only; no source artifact is overwritten.
- No Experience is automatically active.
- No production Agent consumes Experience during Gate 4.
- No network, Provider, judge, embedding, external database, or real Vault is
  available to the implementation or acceptance tests.

## Review Gates

1. This Planner draft requires explicit human approval before tests or code.
2. Worker implements only the approved files and TDD packages in
   `implementation_plan.md`.
3. Worker produces offline evidence and a `GATE 4 REVIEW PACKET`.
4. The independent Reviewer checks leakage, hardcoding, provenance, context
   bounds, abstention, no parallel Eval stack, and no Runtime/prompt mutation.
5. Gate 4 completes only after Reviewer `APPROVE` with all BLOCKER findings
   resolved.

## Open Questions

No product decision blocks this draft. The proposed conservative defaults are
`top_k=3`, `max_chars=2000`, hard ceilings `5/4000`, accepted-only injection,
and one v1 generation rule. Changing these boundaries after approval requires
a SPEC update before Worker code.

## Learning Reflection

### Step

- Role: Planner.
- Feature: Gate 4 Experience / Reflection Layer.
- Files reviewed: evaluation, trajectory, memory, Runtime artifact/trace
  boundaries, Golden 8 governance, and the three sealed case records.

### What Changed

Defined a minimal Experience boundary that consumes existing evaluator
findings, abstains on ambiguous or Provider-only evidence, stores candidates
with review provenance, and retrieves accepted experience deterministically.

### What I Learned

The strongest Gate 4 design constraint is not storage; it is preventing a
Gold-aware evaluator result from becoming an answer-bearing memory. A narrow
allowlist projection plus registered generic templates is safer than free-form
reflection for v1.

### Evidence

The failure analysis uses sealed `mps-001`, `aer-002`, and `iti-005` artifacts,
their post-hoc outcomes where available, and the already-approved Gate 3 layer
classification. No new Provider result was generated.

### Next Step

Human reviews this SPEC and `implementation_plan.md`. Worker implementation
must not begin until explicit approval.
