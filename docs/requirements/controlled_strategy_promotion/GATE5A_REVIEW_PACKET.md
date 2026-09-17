# GATE 5A REVIEW PACKET — Strategy Contract / Architecture

Status: **DRAFT FOR ORIGINAL INDEPENDENT REVIEWER; no implementation**.
Date: 2026-09-17. Base HEAD: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`.
User explicitly authorizes 5A -> review -> 5B -> review -> 5C, no Provider/push.

## 1. Experience versus Strategy boundary

Experience describes a source observation/lesson; Strategy specifies a future
decision procedure with evaluation requirements and its OWN promotion receipt.
Do not rename/reinsert arbitrary Experience prose. Registered mapping checks
source accepted status and turns one supported generic semantics finding into
a candidate evidence-classification procedure. Zero candidate is legitimate.

## 2. StrategyCandidate schema

`strategy-candidate/v1`: canonical strategy_id, generation_rule_id, title,
situation, behavioral_rule, applicability, exclusions, source_experience_ids,
typed source_review_receipts, expected_benefit, known_risks,
evaluation_requirements, status, created_at and review_transition. Frozen,
closed, generic registered template. Create candidate only; terminal record
needs separately persisted original-candidate review proof. See SPEC field table.

## 3. Provenance

Source receipts bind Experience/candidate-event/review-event/decision-hash/store
identity. All consumers freshly replay configured Gate 4 Store accepted proof.
Do not accept a caller's source IDs, copied receipt or raw accepted flag.
Comparison authority resolves bootstrap-pinned original paired observations,
seals, evaluator and condition/context IDs; no request registration API.

## 4. Gold isolation

Generation/context never receive expected-answer data, arbitrary source prose,
case IDs, filenames or private paths. Registered template equality plus closed
schema/value/opaque-ID policy applies to all lifecycle values. `expected_benefit`
is a schema-specific generic hypothesis, not a weakening of Gate 4 key checks.
Existing frozen Gold is evaluator-private and unchanged; both observed branches
are sealed BEFORE existing post-hoc oracles read Gold on private new copies.

## 5. Accepted transition

Evaluator outputs recommend_accept/recommend_reject/insufficient_evidence only.
None changes status. Human source receipt records exact strategy/candidate/
Store/comparison/baseline/candidate/deltas/regressions/actor/time/rationale.
Accepted requires recommend_accept + explicit persisted human decision + fresh
durable replay; insufficient or unsafe comparison cannot be overridden in v1.
Independent architecture approval and test fixture human receipts are not real
per-record human approval. Rejected history remains audit-only.

## 6. Injection boundary

Accepted strategy is an explicit provider-neutral artifact; no global prompt,
Provider or Runtime mutation. Candidate context has a separate offline-preview
type/heading, ONLY inside paired evaluation. Normal retrieval/final accepted
serializer rejects candidates and preview artifacts; no candidate production use.

## 7. Context bounds

Default3/2000, hard5/4000 characters and16000 UTF-8 bytes including framing.
Structured applicability and exclusion precedence; stable score/ID ordering,
identity dedup and whole-record inclusion. Final serialization freshly resolves
accepted Store records and rerenders rather than trusting caller text/counters.
Constructor bypass, >5 genuine records and lower configured bounds are RED cases.
Character/byte cost proxy is not token count; unavailable tokens/cost stay N/A.

## 8. Production touch points

5A changed ONLY four files in this directory. After APPROVE: new Strategy
package and small evaluation extension, Strategy tests/safe fixtures/docs.
Existing Gate 4 source, Runtime, tools, Provider, prompt, Gold/upstream evaluators
and original sealed artifacts remain unchanged. Stage 0 was already committed:
67 approved Gate 4 files; remaining19 tracked dirty files excluded; no push.

## 9. Deferred capabilities and evidence gaps

No live Provider efficacy or automatic activation, optimizer/platform, RBAC,
tokenizer, runtime wiring, code/prompt rewrite or new product scope. Current
real sealed mps is candidate; only existing accepted offline-test examples are
proven. Missing legitimate source approval -> no candidate; missing dimension
or unchanged frozen outputs -> insufficient evidence, not fake improvement.
A per-record human decision may be required before a real promotion demonstration;
do not replace it with code Reviewer approval. Unit fixture improvements establish
mechanism behavior only, not generalized model efficacy.

Fair comparison observes identical baseline/candidate public inputs, documents,
base prompt/tools/Runtime/retrieval/budgets/FakeModel/evaluator/Gold identities;
only separate Strategy context differs. Separate per-case Contract, grounding,
decision semantics, scope, unsupported inference, uncertainty, applicable tool
behavior, chars/byte cost. General conservative policy cannot hide regressions
or infer missing entailment/scope/uncertainty PASS from existing partial oracles.

## 10. SPEC / plan / evidence paths and request

- [SPEC.md](SPEC.md)
- [implementation_plan.md](implementation_plan.md)
- [task.md](task.md)
- [Gate 4 original verdict](../experience_reflection_layer/GATE4_ORIGINAL_REVIEWER_VERDICT.md)

5A checks: current base/index/status, planning scope/links/whitespace. Production
tests not run because no implementation; prior Gate 4 independent407/1 evidence
is not represented as Gate 5 tests. Original historical seals/pins are untouched.

Original task `审查 Runtime V2 Gate 1`, please independently assess this design
against the user's Gate 5 directive and current repository, then return
**APPROVE** or objective **BLOCKERs**. Save your verdict in
`GATE5A_ORIGINAL_REVIEWER_VERDICT.md` (your document only) and send a concise
callback to Builder task `01a0a4b4-536b-7200-9e8e-227488166398`.
Do not implement/fix production, invoke Provider, stage/commit/push or silently
approve a real Experience/Strategy. Builder stops implementation while pending.
