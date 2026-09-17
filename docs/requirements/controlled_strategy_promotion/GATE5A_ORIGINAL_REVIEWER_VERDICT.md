APPROVE

# Gate 5A Original Reviewer Verdict

Date: 2026-09-17
Role: Independent Reviewer / Architecture Gatekeeper
Gate: 5A — Controlled Strategy Promotion (Strategy Contract / Architecture only)
Base HEAD: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5` (`feat(agent): add governed experience reflection`)

## Scope and evidence

I read the four Gate 5A documents in this directory: `SPEC.md`,
`implementation_plan.md`, `task.md`, and `GATE5A_REVIEW_PACKET.md`. I also
checked the governing repository contracts, the existing Gate 4 Experience
authority/store/policy/retrieval/context boundaries, `TeamDecisionResult`, and
the unchanged Golden/business sealed-observation interfaces.

The index is empty and the master HEAD is unchanged. There is no
`src/linkloom/strategy/` package and no `src/linkloom/evaluation/strategy.py`.
The existing tracked dirty work and untracked Runtime V2/planning work were
left untouched. No implementation, test, Provider call, commit, push, or
production promotion was performed.

## Blockers

None. I found no objective architecture or contract blocker within the
approved 5A boundary.

## Architecture verdict

- Experience and Strategy are separate lifecycle objects with separate
  schemas, identities, stores/logs, and review receipts. `suggested_strategy`
  remains proposal input and is never executable authorization.
- Candidate generation is restricted to accepted, freshly revalidated Gate 4
  Experience records. Source approval references bind the source Experience,
  candidate event, review event, decision hash, and store identity; missing,
  stale, foreign, rejected, candidate, or insufficient sources yield no
  candidate.
- `StrategyCandidate` is provider-neutral and generic. Its registered
  `expected_benefit` is a prospective hypothesis, not an answer, Gold value,
  case hint, or measured improvement. The schema and lifecycle are closed and
  identity-hashed independently of Experience.
- Candidate evaluation, human review, acceptance/rejection, and bounded reuse
  are explicit stages. The plan does not silently approve an Experience or a
  Strategy.

## Evaluation and fairness verdict

- The paired comparison freezes suite/case order, public inputs, base
  prompt/tools/Runtime/retrieval/configuration, FakeModel, seed, budgets, and
  evaluator/Gold identities before either branch runs.
- Baseline and candidate branches are distinguished by one variable only: a
  separately rendered bounded Strategy context. The harness records actual
  model-observed request fingerprints/context and trajectories, seals both
  observations, and resolves comparison authority from pinned artifacts rather
  than caller-supplied or recomputed claims.
- Required dimensions are evaluated per case (contract, grounding, decision
  semantics, question scope, unsupported inference, uncertainty, retrieval or
  tool behavior when applicable, and context character/UTF-8 byte cost).
  Missing, partial, unresolved, unchanged, or oracle-inapplicable evidence is
  `N/E`/`REVIEW_REQUIRED` and leads to `insufficient_evidence`; it cannot be
  converted into a PASS or an acceptance.
- The conservative policy rejects leakage, isolation failures, budget
  overruns, contract/grounding regressions, and new severe regressions. A
  recommendation is not a lifecycle transition and does not claim live
  efficacy.

## Governance and injection-safety verdict

- Normal retrieval can use only accepted Strategies with a fresh persisted
  human review receipt, fresh source/comparison authority, and applicability
  checks. Candidate preview is a separate type/heading available only inside
  paired offline evaluation and cannot enter normal retrieval or final
  accepted context.
- Acceptance requires `recommend_accept` plus an explicit persisted
  `source=human` per-Strategy decision. Reviewer, validator, model, or tool
  callback identities cannot masquerade as that human source; recommendation
  output alone never writes the transition.
- Gold, raw answers, filenames, evaluator-private prose, and case/answer/path
  hints remain evaluator-private. Lifecycle values are scanned for the Gate 4
  opaque-ID/value/path/case-hint grammar before persistence or rendering.

## Context bounds verdict

The final serializer freshly resolves and renders accepted records, deduplicates
and orders them deterministically, then rechecks the hard ceilings including
framing: at most 5 items, 4000 characters, and 16000 UTF-8 bytes. The default
3-item/2000-character budget and constructor-bypass/Unicode/oversize checks are
explicitly part of the planned acceptance boundary.

## Scope and deferred work

Gate 5A has no production touch points. After this approval, Builder may begin
5B RED→GREEN work only in the new Strategy package, the dedicated strategy
evaluation module, tests/fixtures, and related docs, reusing existing Gate 4
and evaluator contracts read-only. Runtime, Provider, prompt, Gold, upstream
evaluator, and live efficacy changes remain frozen. Gate 5C promotion and any
real production activation remain out of scope.

## Non-blocking limitations

- Gate 4’s configured manifest/host review entrypoint is the stated trust root;
  authentication/RBAC, revocation, migration, and deployment concerns are
  explicitly deferred rather than hidden in this architecture gate.
- No durable real per-record human-approved Experience or Strategy is assumed
  by the evidence. A 5B fixture may use an existing accepted test Experience
  only with a freshly persisted, explicitly test-only receipt; a missing real
  source approval must remain visible and produce zero candidate rather than a
  fabricated success.
- Existing sealed real-provider artifacts and Golden/business fixtures are
  infrastructure/oracle inputs only; they do not establish Strategy efficacy or
  a real human decision.

## Final decision

**APPROVE — Gate 5A architecture is accepted.** Builder may start Gate 5B
within the frozen boundary. This is an architecture-gate approval only: it
approves no individual Experience or Strategy, authorizes no Provider use, and
does not promote anything to production.
