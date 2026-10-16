# SPEC: Self-Improvement V2 Hardening

## Problem

V1 improved six mechanically related cases through a Fake `SourceOnlyController`;
it cannot establish structural generalization or real-provider transfer.
## User-visible result

One reproducible `scripts/self_improvement/run.py --protocol v2` command produces
freeze identities, per-case metrics, paired strategy comparisons, layered
regression attribution, and a machine-readable decision; provider work is
explicit/opt-in and normal execution is offline.
## Scope

- Extend `run.py` with bounded V2 while preserving all V1 files, Gold, result
  identities, and conclusions. Freeze 96 structural cases: DEV 32, Validation
  24, independent Final 24, Stress 16; vary language/layout/ambiguity/ranking,
  conflicts/updates/workspaces/injection/invalid calls/insufficient evidence and
  label planner-unsupported shapes.
- Compare identical public inputs with Baseline, V1 `relation-routing`, V1
  `narrow-first`, and at most three strictly validated configurations.
- Fresh worker receives public data only; fresh scorer loads Gold after sealed raw
  execution and verifies citations, grounding, source/workspace identity,
  reviewer/refusal. Record per-case outcome, conflict recall, evidence/version,
  candidates versus verified reads, calls/turns/context bytes, latency, and usage.
- Allow at most two targeted repairs with hypothesis, attacked class, expected
  gain, recall risk, and exit. Evaluate Final once after screening; failed Final
  is retained and cannot drive same-batch retuning.
## Architecture and contracts

`run.py` owns experiment IDs, manifests, phase ordering, budget, and admission;
fixture code owns public construction/leak checks; `worker.py` executes; `scorer.py`
owns Gold/scoring. Phases write separate content-addressed artifacts and refuse overwrite.

Reuse `RuntimeEngine`, `RuntimeAgentAdapter`, model adapters, `ToolExecutionLedger`,
Verified Read, Grounding, and Reviewer. A lab subclass may override
`_build_runtime_agent_adapter`, call `super()`, and inject a per-instance
`SubgoalQueryStrategy`; production defaults stay unchanged. Add a default-off seam
only if this supported injection is insufficient. `read_mode` pending-read pruning
is FakeController-only unless a real path proves otherwise; never call it Gemini
transfer. No second Runtime.

Candidate config is an exact whitelist (bounded query/read mode, limit, id,
explanation). Reject unknown fields/values, code, Gold paths, permission changes,
or ignore-failure instructions. Candidates cannot alter workspace/source identity,
Plan Validator, ToolRuntime, Grounding, Reviewer, Memory authority, or Ledger semantics.
## Provider and safety boundary

Use validated `gemini-3.8-flash` only: ≤120 requests, $6 reserved ceiling, three
hours, serial, SDK retry 0, canary ≤12. Two strict `Provider Outcome Uncertain`
results stop the batch; unknown outcomes never replay. Synthetic data only;
payloads/secrets ignored. Decisions are `REJECTED`, `EXPERIMENTAL_ACCEPTED`, or
`INTEGRATION_CANDIDATE`; production activation/`PRODUCTION_ACCEPTED` is forbidden.
## Acceptance criteria

1. The V2 command runs offline through baseline, DEV analysis, candidate
   execution, Validation, one Final, layered regression, and machine decision;
   reruns use a new experiment ID and never rewrite a freeze or Ledger.
2. Structural diversity, public/Gold separation, and author visibility are
   recorded; no worker/generator input contains Gold or Final results.
3. Baseline, both V1 controls, and each accepted candidate have paired per-case
   and per-class metrics, with search-hit versus verified-read distinguished.
4. Real-agent results explicitly separate query-strategy transfer, model tool
   behaviour, planner unsupported, provider failure, and unknown outcomes.
5. Core, Historical, Candidate Differential, and Full Repository regression
   reports distinguish baseline failures, new failures, collection failures, and
   environment failures; no failures are deleted or blindly skipped.
6. Any safety regression, conflict-recall loss, invalid evidence, provider
   uncertainty violation, budget violation, or missing independent evidence
   rejects the candidate. `PRODUCTION_ACCEPTED` is impossible in this protocol.
## Checks

Unit/contract checks cover schema, freeze hashes, structure/leak detection,
fresh-process Gold isolation, immutable artifacts, ledger/cost accounting, and
provider stop rules. Integration checks exercise all 96 cases and the three
fixed strategies through the existing Runtime. Adversarial checks cover invalid
configs, Gold-path attempts, stale Ledgers, unsafe claims, conflict loss, and
Final reuse. Run layered base/candidate regressions before any provider canary.
