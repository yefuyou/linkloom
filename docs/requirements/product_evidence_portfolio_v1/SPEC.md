# LinkLoom Product Evidence & Portfolio V1 — SPEC

## Status

**APPROVED.** The user's 2026-09-15 phase brief authorizes autonomous local
execution, the two named DeepSeek cases, post-hoc evaluation, README and
portfolio material, scoped fixes, screenshots, selective commits, and no push.

## Outcome

Turn the accepted Agent/runtime/UI baseline into honest product evidence that
shows whether LinkLoom can recover a multi-document decision boundary and can
refuse to invent missing assignments or deadlines.

## Scope

- Integrate Product UI V1 into the current local mainline.
- Run exactly `aer-002`, then `iti-005`, with `deepseek-flash`.
- Keep thinking disabled, JSON output enabled, semantic retries at zero, and
  the existing Runtime, ToolRuntime, strict `TeamDecisionResult`, and grounding
  ownership unchanged.
- Keep execution input Gold-free. Load frozen expected fields only after each
  observed result is terminal and sealed.
- Produce one evidence matrix covering the existing real `mps-001` result and
  the two new first results.
- Fix only product-display blockers supported by the real results.
- Rewrite both READMEs around decision recovery, evidence, uncertainty, the
  product demo, architecture, and honest real-provider evidence.
- Draft three to four resume bullets and architecture-diagram content.

## Non-goals

- Runtime, Provider, ToolRuntime, grounding, strict-contract, Golden expected,
  memory, MCP, auth, billing, admin, routing, fallback, or provider expansion.
- Gemini A/B, DeepSeek Pro, thinking, other Golden cases, semantic reruns, or
  case-specific prompt tuning.
- Production-readiness or benchmark-success-rate claims.
- Push or public release.

## Acceptance criteria

1. Product UI V1 is committed on the current local mainline with unrelated
   dirty files excluded.
2. A test-only DeepSeek runner permits only `aer-002` and `iti-005`, preserves
   autonomous model tool choice, and records thinking disabled / semantic
   retry zero.
3. Each case runs once to its first terminal result; a semantic failure is
   retained without resampling.
4. Observed execution is sealed before Gold is loaded for post-hoc analysis.
5. Artifacts prove source immutability and contain no credential value.
6. The evaluation matrix reports Infra, Contract, Grounding, Decision, Scope,
   Uncertainty, Overall, plus what worked, failed, and the product implication.
7. Findings distinguish infrastructure, contract, semantic limitation, and
   expected uncertainty.
8. The UI renders the relevant real result shapes, or any blocking projection
   bug is fixed and reviewed without redesign.
9. README and portfolio notes describe only verified capabilities and results.
10. Scoped regression and reviewer evidence pass; commits remain local.

## Stop conditions

Stop for a Runtime/grounding/strict-contract/Golden change, DeepSeek Pro or
thinking, a new Provider, materially higher API cost, credential leakage, or a
new P0/P1 architecture blocker. Record ordinary semantic failures and continue
within this approved phase.
