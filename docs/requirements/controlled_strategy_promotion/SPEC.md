# SPEC: Gate 5 — Controlled Strategy Promotion

Status: **Gate 5A/5B/5C original Reviewer APPROVE; frozen foundation accepted, selective local Git closure authorized**.
Date: 2026-09-17. Builder role: Worker within the approved architecture.
Base HEAD: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5` (`master`).

## Problem and workflow

Gate 4 records what happened and what was learned. A lesson or its suggested
strategy is not authorization to change the Agent's behavior. Gate 5 adds an
independent behavior proposal, a single-variable offline comparison and a
durable explicit human decision before any accepted-strategy reuse.

`accepted Experience -> candidate Strategy -> paired offline evaluation ->
recommendation -> explicit human review -> accepted/rejected -> bounded reuse`.

The user authorizes this boundary and 5A/5B/5C review checkpoints. This is not
self-modifying code, production promotion or proof of live-model improvement.

## Experience versus Strategy

Experience owns source observations, causal finding, reusable lesson and its
existing review transition. Strategy owns a prospective decision procedure,
applicability/limits, evaluation obligations and separate promotion lifecycle.
They have different schemas, IDs, logs and review receipts. Experience's
`suggested_strategy` is a proposal input, never executable authorization.

The first registered mapping may propose: before classifying an alternative as
rejected, require explicit rejection semantics in the cited evidence; comparison,
non-selection, lower ranking or supersession alone is insufficient. Preserve
uncertainty when evidence cannot establish rejection. This is provider-neutral,
not an instruction naming a Provider, case, answer, entity or filename.

## Goals and non-goals

Implement only deterministic, local candidate generation, immutable provenance,
paired offline comparison, conservative recommendation, explicit human review,
append-only history and bounded accepted-only context artifacts.

Never change Runtime, tools, Provider adapters, global/system prompt, retrieval
configuration or deployed behavior. No network, real Provider, thinking mode,
model routing, online learning, RL, automatic code/prompt rewrite, deployment,
automatic promotion, new dependency, generic experimentation framework,
multi-agent product runtime/debate, parallel tools, UI/README/MCP/auth/billing or
real Vault access/mutation. Rejected history is retained, not deleted.

## Contracts

All contracts are closed, frozen, exact-field parsed, finite JSON-safe, typed,
versioned and revalidated at consumption, including constructor-bypassed objects.
SHA-256 identities use canonical JSON; timestamps are timezone-aware UTC.

### StrategyCandidate (`strategy-candidate/v1`)

| Field | Contract |
|---|---|
| strategy_id | Canonical immutable content/provenance SHA-256; excludes created_at/status/review_transition |
| schema_version | Exact version |
| generation_rule_id | Registered provider-neutral semantic procedure/version |
| title, situation, behavioral_rule | Registered generic template; no source prose interpolation |
| applicability | Closed workflow + positive task-characteristic signals; at least one required overlap |
| exclusions | Explicit signal exclusions and semantic limits; take precedence over applicability |
| source_experience_ids | Nonempty unique Experience SHA-256 identities |
| source_review_receipts | Typed opaque ExperienceApprovalReference for every source, exactly matching IDs |
| expected_benefit | Generic prospective hypothesis, NOT an expected answer or claimed measured improvement |
| known_risks | Generic limits, including over-abstention and added context |
| evaluation_requirements | Target failure category and versioned mandatory comparison dimensions/policy |
| status | candidate / accepted / rejected; public create is candidate-only |
| created_at | Actual creation timestamp, not an invented historical review time |
| review_transition | None for candidate; durable receipt/event bindings required for terminal state |

`ExperienceApprovalReference` binds source Experience ID, original candidate
event ID, review event ID, review-decision SHA-256 and store-identity SHA-256.
It is a locator, not a bearer authority token. Configured `ExperienceStore.get`
and `validate_reviewed_record` must freshly replay the same accepted record;
the candidate's source receipt/content must exactly match. Orphan, candidate,
rejected, foreign-store, modified or stale sources cannot authorize generation,
save, review, retrieval or context. An unregistered or insufficient accepted
source yields no candidate. No generation path promotes Experience itself.

### StrategyReviewReceipt (`strategy-review-receipt/v1`)

Receipt contains strategy_id, original candidate-event ID, strategy store
identity, comparison/evaluator result ID+SHA, baseline result reference+SHA,
candidate result reference+SHA, shared condition/suite/policy identities,
dimension metric deltas, regression findings, recommendation, reviewer decision
(accepted/rejected), reviewer actor, source=human, reviewed_at and rationale.
Raw answers, Gold, filenames and evaluator-only prose are not copied into it.
Rationale is bounded Gold-safe generic text; human intent is supplied through a
separate trusted host review entrypoint, never a model/tool callback.

A persisted `StrategyReviewTransition` binds that receipt and review event ID
to the original candidate event, immutable strategy identity and configured
store. Shape or `status=accepted` alone is not proof. Fresh durable replay and
all current upstream authority bindings are required on every reuse.

Architecture Reviewer APPROVE, test-fixture review, evaluator recommendation
and real human per-strategy approval are distinct. Independent Reviewer or
deterministic-validator identities cannot masquerade as source=human.
Authentication/RBAC is deferred: the trusted application host and its explicit
human-review entrypoint are the trust root, not a sandbox against an
administrator replacing code/logs/bootstrap configuration.

## Generation and leakage isolation

Generation reads accepted Experience only from configured Gate 4 Store;
revalidates authority, exact source receipt and registered template, then
instantiates a reviewed Strategy procedure template. v1 permits zero or one
candidate per supported mapping; duplicates collapse by immutable identity.
Do not copy arbitrary `suggested_strategy`, observed answers or raw run text.

Reuse Gate 4 opaque run/evidence grammar and value-level Gold/path/case hint
checks. Scan all lifecycle values, not just behavioral_rule. The required field
name `expected_benefit` is allowlisted only by this closed Strategy schema;
it does not widen Gate 4's forbidden-key policy. Its value must equal the generic
registered hypothesis. Generic templates are the answer-literal boundary:
regex alone cannot detect an arbitrary private expected answer.

Gold is evaluator-private, never part of generation, candidate preview, request,
model context, retrieval, Store or human receipt. Frozen input dataset identity
may be an opaque SHA only. Existing expected-answer data is not modified.

## Offline comparison and evidence authority

`StrategyEvaluationRunner` is a small extension of `linkloom.evaluation`, not
a new general Agent Runner. The configured offline harness and evaluator are
trusted bootstrap dependencies, not selectable request callbacks.

1. Freeze suite/case order, public documents/question, base prompt, tools,
   retrieval inputs, Runtime/configuration, FakeModel abstraction, seed,
   budgets, evaluator and Gold identity before either branch. Their canonical
   hashes form one shared condition fingerprint. Gold identity is private to
   evaluation; no expected payload is given to inference.
2. Baseline request has no Strategy section. Candidate request differs only
   in a separately rendered bounded candidate-strategy context field. All
   other request fields and tool/document observations must be identical.
3. Produce both observed results first, including actual received request
   fingerprints/context and observed trajectory/tool results; seal each.
   Model/harness-observed receipts, not caller labels, establish isolation.
4. Only then run the same existing semantic/contract/grounding oracle on both
   observations. A test-only bridge may call the existing frozen post-hoc
   evaluators on private new output copies; never rewrite original artifacts.
   Production Strategy modules do not import `tests.*` or a Gold loader.
5. Persist complete paired per-case dimension results, seals and comparison
   under the local evaluation output root. A bootstrap-pinned comparison
   manifest/authority resolves exact immutable files, strategy/context/condition
   identity and all source/seal/evaluator bindings. No caller can register a
   fabricated comparison or issue an authority by recomputing request pins.
6. Store, human review, retrieval and final context freshly resolve that
   configured comparison authority and source Experience Store. A forged,
   partial, mismatched, omitted, cross-suite or stale result fails closed.

Gate 5C clarification of the existing independent-evaluator acceptance boundary:
producer-returned hashes are not oracle truth. A separately bootstrapped oracle
owner reads both sealed branches and issues `strategy-oracle-outcomes/v1` plus
an HMAC-SHA256 integrity seal. Exact seven-dimensional outcomes, ordered cases,
fixed proof_kind, suite/evaluator/oracle and strategy/branch/condition/context
hashes are authenticated. Comparison receipts include oracle_outcome_sha256.
ComparisonAuthority requires independent host-configured OracleAuthority;
it cannot learn a verifier/key/suite from a result, manifest or request.
The host configures the suite and 32-byte key before the producer runs. The
key never enters comparison artifacts, model context or review receipts.
Authority checks the issuer, signature, frozen identities and every row anew,
without reopening Gold. Rehashing all public claims cannot issue oracle truth.
This is local integrity binding, not authentication/RBAC or a generic signing
platform. Trusted administrator/code/key/bootstrap replacement is outside v1.

Do not re-score semantic improvement from Experience generation hints. Existing
TeamDecisionResult owns structural contract; existing Golden/business oracles
own exact decision comparisons and scope checks. Existing trajectory tools own
tool/retrieval checks where applicable. If an oracle does not establish a
dimension (e.g. actual entailment or unsupported inference), record N/E or
REVIEW_REQUIRED; do not upgrade citation membership into entailment PASS.

### Mandatory separate comparison dimensions

Contract; grounding; decision semantics; question scope; unsupported inference;
uncertainty; retrieval/tool behavior (explicit applicability or N/A); context
characters and UTF-8-byte cost proxy. Each metric has its per-case outcome,
evidence ref and delta; no aggregate overall PASS hides a regression.

No tokenizer is currently required: character/UTF-8 byte increase is a clearly
labeled deterministic proxy; unavailable token delta and monetary cost are
N/A, never invented zero. Frozen replay with unchanged scripted outputs does
not prove a strategy caused semantic improvement. A responsive FakeModel can
prove mechanism behavior, not real Provider efficacy; record proof_kind.

### Conservative promotion policy (`strategy-promotion-policy/v1`)

Uniform policy, frozen before results: recommend_reject on leakage, isolation
violation, over-budget context, any Contract/grounding regression or new P0/P1
regression; no accepted transition is permitted. Other new measured regressions
in scope, unsupported inference, uncertainty or tool behavior also prevent
acceptance. Never average a failed case away.

Recommend_accept only if all mandatory applicable dimensions are complete and
reliable, no per-case regressions exist, at least one target-category FAIL->PASS
is attributable to the sole context variable, and context delta is within the
same 4000-character/16000-UTF8-byte hard ceilings. No case-specific thresholds.
N/A requires a predeclared applicability reason, not post-hoc omission.
Incomplete evidence, unchanged outcomes, unresolved causes or non-responsive
frozen replay yield insufficient_evidence, not semantic FAIL or acceptance.
All decisions bind the complete result and exact deltas/regressions.

## Review and promotion lifecycle

Store has append-only candidate_saved/review_recorded events, canonical hashes,
fsync, collision/idempotency validation and deterministic replay. Reject a
corrupt complete event; incomplete final write handling follows the established
local Store pattern. No silent deletion or rewriting of valid history.

Recommendation never writes a lifecycle transition. Human acceptance requires
an issued recommend_accept comparison and complete explicit persisted receipt;
human rejection may preserve recommend_reject/insufficient evidence findings.
Insufficient_evidence cannot be overridden into accepted in v1. Rejected records
remain auditable and noninjectable. Test receipts exercise both branches but
do not purport to be real human decisions or activate a production strategy.

## Context and injection boundary

Normal `StrategyRetriever` and `StrategyContextBuilder` consume configured Store
records only. Accepted + fresh human receipt replay + fresh source/comparison
authority + applicability are all necessary. Candidate/rejected never reuse.

Defaults: top_k=3, max_chars=2000; absolute ceilings top_k=5, chars=4000 and
UTF-8 bytes=16000, including heading, delimiters and all rendered content.
Explicit structured signal overlap, exclusion precedence; order by match score
descending then strategy_id ascending, dedup identity, whole-record inclusion.
No run/case/answer-text ranking. Request bounds cannot widen configured bounds.

Final serializer must freshly resolve durable accepted records and rerender
from them, recheck configured and absolute item/character/byte bounds, model
text equality and ID/item accounting. Caller-supplied text, selection counters,
accepted flags or frozen-object bypasses are not trust. Trace IDs/hashes,
source receipts and evaluation results stay separate from model-visible text.

Model heading: `Relevant accepted strategy`; fields: Situation, Behavioral rule,
Applicability and Exclusions. No hidden global prompt mutation. This stage
provides an explicit reusable artifact and offline consumer, not automatic
production Runtime wiring.

Candidate evaluation preview uses a separate offline-only artifact/type and
heading `Candidate strategy under offline evaluation`. It is accessible only
within the configured offline comparison path; it cannot be passed as an
accepted context or returned by normal retrieval. Preview has the same final
hard bounds. This evaluation-only exception is not production promotion.

## Current evidence and proof limits

Gate 4 independent verdict remains APPROVE (407 passed, 1 skipped); real sealed
disposition remains mps1/aer0/iti0. Historical hashes and receipt pins are not
changed. The actual historical mps Experience is candidate until separately
reviewed; the existing accepted examples have explicit offline-test receipts.
No durable real human-approved Experience or Strategy is assumed from a Gate
architecture approval. Initial 5B proof may use existing accepted offline
Experience with fresh persisted fixture replay, labeled test-only. The real
source path must abstain if no legitimate accepted source can be resolved.
If final real human promotion proof needs a missing per-record decision, expose
that exact decision to the user; do not fabricate it to finish Gate 5.

## File responsibility and deferred capabilities

After 5A APPROVE only: new `src/linkloom/strategy/` package with models, policy,
generation, authority, store, retrieval, context and __init__; one small
`src/linkloom/evaluation/strategy.py` extension and its small trusted
`strategy_oracle.py` outcome owner/verifier; Strategy unit tests, independent
offline integration/harness bridge, safe sealed fixtures and this feature's
docs. Existing Experience and evaluators are reused read-only. Prefer no edits
to approved Gate 4 source. All 19 pre-existing tracked dirty files, Runtime V2
untracked files and unrelated tasks are outside ownership and staging.

Production touch points in 5A: NONE; 5B adds explicit artifact APIs only.
Deferred: live Provider efficacy studies,
production strategy wiring, real human record approvals when unavailable,
tokenizer integration, authentication, revocation/version migration, similarity
suppression, optimizer/router, deployment and automatic promotion.

## Acceptance criteria and checks

- Generation: accepted + exact persisted source review produces candidate;
  candidate/rejected/orphan/foreign/stale/insufficient sources abstain or reject.
- Leakage: case/answer/path/Gold-bearing values or IDs anywhere in lifecycle
  reject; filesystem/request spies prove Gold-free inference/generation/reuse.
- Governance: direct accepted construction, copied receipt, missing/different
  candidate/review event, evaluator auto-accept, nonhuman review and unpersisted
  accepted objects cannot activate normal context; valid persisted review can.
- Evaluation: sealed paired actual requests differ only by Strategy context;
  all frozen identities match; raw partial/forged/omitted dimensions reject;
  correct per-case deltas; regressions prevent promotion; no gains -> insufficient.
- Bounds: >5 genuine accepted records, configured lower bounds, Unicode/oversize,
  forged accounting/text and constructor bypass remain hard bounded at final
  serialization; deterministic order, duplicate and exclusion proofs.
- End-to-end offline: existing accepted test Experience -> candidate -> two
  evaluations -> recommendation -> explicitly labeled human test receipt ->
  terminal accepted/rejected; no live efficacy or real human approval fabricated.
- Adjacent Gate 4/Memory/Runtime/TeamDecision/Golden/business/eval and original
  seal/provenance regression pass. No Provider opt-in; no sealed source mutation.
- Original Reviewer 5A APPROVE precedes implementation; 5B APPROVE precedes
  5C integrity review; final 5C APPROVE required for completion claim/commit.

Commands are in [implementation_plan.md](implementation_plan.md), progress in
[task.md](task.md). Checks in 5A are document/scope/link/whitespace only; no
unchanged-code tests are repeated. No new implementation exists before approval.

## Open decisions and learning reflection

No additional architecture authority is assumed: 5A is submitted to the
original Reviewer under the user's explicit review protocol. A missing real
per-Experience/per-Strategy human approval remains a factual gap, not a fake
fixture success. The simplest safe v1 is a registered procedure + sealed paired
comparison + explicit persisted decision, not prompt optimization infrastructure.
Next: original Reviewer checks this SPEC/plan/packet and returns APPROVE or
objective BLOCKERs. This initial Planner reflection is historical: original 5A
APPROVE was issued before implementation. The original 5C oracle-binding
finding is resolved; the original [final recheck verdict](GATE5C_ORIGINAL_REVIEWER_RECHECK_VERDICT.md)
is APPROVE. Only status/checklist closure and the exact reviewed local commit
remain authorized; no source/test changes, production wiring or next-stage work.
