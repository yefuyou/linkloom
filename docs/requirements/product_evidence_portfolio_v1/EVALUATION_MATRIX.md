# LinkLoom Real-Provider Evidence Matrix

## Scope and reading rules

This matrix records three first-result DeepSeek runs against frozen synthetic
workspaces. It is not a benchmark score and it does not extrapolate to private
or production vaults.

- `PASS`: the named layer was reached and satisfied its contract.
- `PARTIAL`: the core behavior worked, but a bounded semantic limitation was
  observed.
- `FAIL`: the named layer was reached and failed.
- `N/E`: not evaluated because an earlier infrastructure layer prevented a
  `TeamDecisionResult` from existing.
- `BLOCKED_INFRASTRUCTURE`: the business question could not be evaluated
  without changing an approved Provider/Runtime boundary.

Gold fields were unavailable to execution and were loaded only after each
observed run was terminal and sealed. No semantic rerun was performed.

## Product matrix

| Case | User intent | Infra | Contract | Grounding | Decision | Scope | Uncertainty | Overall |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `mps-001` | Recover the approved Atlas Lantern provider | PASS | PASS | PASS | PASS | PARTIAL | PASS | PARTIAL |
| `aer-002` | Recover the rollout boundary and remaining actions across records | FAIL | N/E | N/E | N/E | N/E | N/E | BLOCKED_INFRASTRUCTURE |
| `iti-005` | Identify owners and deadlines without inventing missing assignments | FAIL | N/E | N/E | N/E | N/E | N/E | BLOCKED_INFRASTRUCTURE |

## Case evidence

### `mps-001` — core decision recovery works, scope precision needs work

- **Observed:** the two-turn Agent searched the six-note workspace, returned a
  strict `TeamDecisionResult`, selected **Aster A**, cited only observed
  evidence, and reported no uncertainty for the directly supported decision.
- **Worked:** Infrastructure, strict contract, claim-level grounding, decision
  value/status, empty actions, empty unresolved items, and source immutability.
- **Did not work:** the model treated two compared non-selected providers and a
  superseded early proposal as explicitly rejected alternatives. The frozen
  Gold expected no rejected alternatives.
- **Classification:** semantic limitation, not an infrastructure or contract
  defect.
- **Product implication:** the decision brief is trustworthy for the central
  choice, but auxiliary labels such as “rejected” need tighter semantic
  precision before they should be treated as exact project facts.
- **Run facts:** 2 model turns, 2 provider requests, 11,237 reported tokens,
  zero reasoning tokens, semantic retries `0`, source unchanged, estimated
  peak cost `$0.003157392`.
- **Local evidence:**
  `.artifacts/deepseek_real_provider_smoke/mps-001-745eb207a01449a98e39ee23b9b4ed1e/mps-001/observed_summary.json`
  and `posthoc_evaluation.json`.

### `aer-002` — multi-document semantics not reached

- **Observed:** the first Provider response contained multiple tool calls. The
  DeepSeek adapter failed closed with
  `MODEL_RESPONSE_UNSUPPORTED / multiple_tool_calls` before any tool executed.
- **Worked:** bounded request, thinking disabled, structured output request,
  zero semantic retries, terminal sealing, post-hoc Gold access, credential
  scan, budget enforcement, and source immutability.
- **Did not work:** no normalized Agent action, evidence, or
  `TeamDecisionResult` was produced. Contract, grounding, decision, scope, and
  uncertainty therefore remain **not evaluated**, not failed.
- **Classification:** infrastructure defect.
- **Product implication:** the current DeepSeek integration cannot yet be used
  as evidence for multi-document decision synthesis when the model emits more
  than one call in a turn.
- **Run facts:** 1 model turn, 1 provider request, 2,507 reported tokens, zero
  reasoning tokens, semantic retries `0`, source unchanged, estimated peak
  cost `$0.00049632`.
- **Local evidence:**
  `.artifacts/deepseek_business_evaluation/run-d80ec8a974e34e6ab54b93006fde74a6/aer-002/observed_summary.json`
  and `posthoc_evaluation.json`.

### `iti-005` — honest uncertainty semantics not reached

- **Observed:** the first Provider response hit the same
  `MODEL_RESPONSE_UNSUPPORTED / multiple_tool_calls` boundary before any tool
  executed.
- **Worked:** the same safety, sealing, budget, credential, and immutability
  boundaries as `aer-002`.
- **Did not work:** no business result existed, so the intended test—showing
  missing owners/deadlines as insufficient evidence—was not semantically
  evaluated.
- **Classification:** infrastructure defect. It must not be reported as a
  hallucination or uncertainty failure.
- **Product implication:** the UI has a truthful operational-failure state,
  but this run does not prove the model's missing-evidence behavior.
- **Run facts:** 1 model turn, 1 provider request, 2,559 reported tokens, zero
  reasoning tokens, semantic retries `0`, source unchanged, estimated peak
  cost `$0.00054522`.
- **Local evidence:**
  `.artifacts/deepseek_business_evaluation/run-d80ec8a974e34e6ab54b93006fde74a6/iti-005/observed_summary.json`
  and `posthoc_evaluation.json`.

## Root-cause boundary

The team-decision instruction already says that each turn must choose either
one next tool call or Final. The current [DeepSeek Chat Completions
contract](https://api-docs.deepseek.com/api/create-chat-completion/), however,
permits `tool_choice: auto` to return one or more calls, while
LinkLoom's `ModelResponse` and `ToolRuntime` deliberately own exactly one
action per turn. Both new cases exposed that same capability mismatch.

Resolving it requires an explicit Provider/Runtime architecture decision:
either normalize and sequence multiple calls while preserving durable action
semantics, or expose a provider mode that can enforce one call. Prompt tuning
or resampling these two cases would not repair the contract and was not done.

## UI interpretation

The committed Product UI renders three distinct product states:

1. successful decision recovery with linked evidence (`mps-001` demo fixture);
2. insufficient evidence with unknown fields (truthful synthetic UI fixture);
3. operational failure with a safe stable code (the real `aer-002` and
   `iti-005` result shape).

Only the first state has a successful real DeepSeek semantic result in this
matrix. The insufficient-evidence screen demonstrates the strict result and UI
contract, not a successful `iti-005` DeepSeek run.
