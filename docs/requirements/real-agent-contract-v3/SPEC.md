# SPEC: Real-Agent Contract V3 repair regression set

Status: **FROZEN V2 BEFORE REPAIR CONFIRMATION — Planner artifact**

This SPEC defines the smallest offline contract boundary for the V3 repair
worktree. It is governed by the repository `SPEC.md`, `DEV_SPEC.md`,
`AGENTS.md`, and the V3 user request. The worktree starts at Adaptive Planning
commit `37e45b169f19c4edd29db59f637e690caab31ad6` on
`codex/linkloom-real-agent-contract-v3`.

## Problem

The Adaptive Planning and Self-Improvement runs exposed failures at shared
boundaries: a model can have sufficient verified evidence and still return an
invalid Final; a tool call can omit or falsify Host-owned context; bounded
context can hide a required passage; and budget, freshness, workspace, or
Provider uncertainty can be misreported as ordinary missing evidence. The
existing Runtime, RecoveryPolicy, evidence Grounding, Reviewer, and append-only
Provider Ledger already own the safety decisions. V3 needs a small independent
regression set that makes those decisions observable before a production fix is
accepted.

This is a contract/evaluation boundary, not a new Agent feature and not a new
retrieval benchmark.

## Goals

1. Freeze 20 new synthetic scenarios: exactly 10 development cases and 10
   final cases.
2. Exercise complete evidence with invalid Final, valid Complete, valid
   Partial, Conflict, Chinese entity boundaries, malformed Tool arguments,
   same-file multiple passages, bounded Context, budget exhaustion, Provider
   Outcome Uncertain, Workspace isolation, and source Freshness.
3. Reuse the current production ownership: Runtime and ToolRuntime validate and
   execute; RecoveryPolicy gives at most one bounded legal recovery; Grounding
   validates claims against observed verified evidence; Reviewer decides review
   status; the Provider Ledger remains append-only and authoritative for request
   accounting.
4. Keep the causal comparison staged: **A** fixes the Contract with the query
   strategy held fixed; **B** is an optional later `query-only` comparison with
   the repaired Contract held fixed.
5. Preserve a reproducible SHA manifest before the main Agent confirms a repair
   design.

## Non-goals

- During this Planner/freeze step, no production source change, prompt tuning,
  retry expansion, token-budget expansion, new relation, new tool, or new
  Provider is performed. The approved V3 Worker phase may make the minimal
  production repair after the main Agent confirms this boundary.
- No modification of the historical Adaptive Planning, Self-Improvement,
  Recovery, relation Gold, or any existing evaluation fixture.
- No real vault, private workspace, network call, Gemini call, or Provider
  Ledger write while authoring or freezing this set.
- No FakeController result presented as real-model evidence.
- No external blind-test claim. Gold is intentionally author-visible to the
  LinkLoom author and is used for local contract checks only.
- No final-partition execution before the repair is complete; no repeated final
  run for tuning.

## Architecture boundary

The frozen case is an input/evaluation record consumed by an offline adapter or
an existing integration fixture. The intended path is:

```text
case.input
  -> RunRequest / TaskPlan / ToolCall fixture
  -> Runtime + ToolRuntime + append-only Ledger
  -> RecoveryPolicy (bounded, if applicable)
  -> observed verified evidence and ModelTurnRequest context
  -> TeamDecisionResult / relation Final validation
  -> claim Grounding
  -> Reviewer
  -> observable status and audit record
```

The set does not replace any of those components. A runner may adapt
`case.input` to the existing `tests/integration` fixtures and may use the
development `case.stimulus` as a deterministic model/provider fault injection.
The runner must never put `case.gold` into a model request, ToolResult, prompt,
or Provider input. The model-input projection is therefore the fields under
`input` plus the explicitly documented offline stimulus only; `gold` is read
after execution by the local evaluator.

## Case and file contract

The three JSON files are:

| File | Cases | Use | Execution rule |
|---|---:|---|---|
| `tests/fixtures/real_agent_contract_v3/dev.json` | 10 | red/green diagnosis and focused regression | may be run repeatedly with deterministic offline stimulus |
| `tests/fixtures/real_agent_contract_v3/final.json` | 10 | independent post-fix check | run once, only after the repair is confirmed; do not use its contents to debug |
| `tests/fixtures/real_agent_contract_v3/freeze.json` | manifest | identity and integrity | records the SHA-256 of the four frozen content files |

`dev.json` and `final.json` use
`schema_version = "real-agent-contract-v3-fixture/v1"` and have exactly these
top-level fields:

```json
{
  "schema_version": "real-agent-contract-v3-fixture/v1",
  "dataset": "real-agent-contract-v3",
  "partition": "dev | final",
  "purpose": "author_visible_contract_debugging | post_fix_single_run",
  "base_commit": "37e45b169f19c4edd29db59f637e690caab31ad6",
  "gold_visibility": "author_visible",
  "external_blind_test": false,
  "cases": []
}
```

Each case has exactly `id`, `categories`, `input`, `stimulus`, and `gold`.
`input` follows the existing integration fixture vocabulary:

- `workspace_id`, `vault_root_fingerprint`, and `source` identify the active
  workspace/index snapshot;
- `query` is the user question;
- `documents` is a non-empty list of synthetic Markdown records with
  `workspace_id`, `path`, `content`, `valid`, and `source_version`;
- `budget` contains non-negative `max_steps`, `max_provider_requests`, and
  `max_context_bytes`;
- optional `task_plan` is a serialized existing `TaskPlan` used only by
  dependency/alternate-query cases.

The `input.source` object is logical fixture metadata for a synthetic
scanner/index builder. `index_id`, `index_sha256`, `document_count`, and
`source_version` in this object are not production fingerprints and are not
model-authored facts. The adapter must materialize the authoritative Runtime
`SourceContext` from the synthetic documents and index before constructing a
`RunRequest` or ToolCall. In particular, the `stimulus` ToolCall examples are
offline logical arguments: their source context must be replaced by the
materialized Runtime identity before a production-compatible ToolRuntime is
invoked. No pseudo-SHA or raw fixture metadata may be injected directly into a
Provider prompt or accepted as a source identity.

`stimulus` is an offline execution aid, never a Gold answer. It may contain
scripted ToolCalls, an invalid raw Final, a provider outcome, a source update,
or a context-pressure marker. A final case intentionally contains no scripted
Final answer; its stimulus is provider-input metadata only. Real Provider
execution remains outside this freeze task.

`gold` is the author-visible expected observation. It records only the
externally checkable result: `business_status`, `runtime_status`,
`final_contract`, `grounding`, `reviewer`, `evidence_paths`, `facts`,
`required_guards`, and `forbidden_coercions`. It is not a blind benchmark and
must not be copied into production instructions. Evidence paths are resolved
to the current run's opaque evidence IDs by the evaluator; a filename alone is
never accepted as a claim reference.

## Required semantic checks

The evaluator must distinguish all of the following:

- valid Complete, valid Partial, and valid Conflict Final results;
- invalid JSON/schema, out-of-scope, unobserved-reference, or unsupported
  relation Final results;
- malformed Tool arguments before executor invocation;
- a Host-owned source-context mismatch from a model-owned dependency entity;
- same-file passages that are independently read and deduplicated without
  losing a required relation;
- context truncation that preserves a required verified read, versus context
  loss that makes a claim unsupported;
- a hard budget outcome from evidence insufficiency;
- Provider Outcome Uncertain from an ordinary retryable failure, with no
  automatic re-request after uncertainty;
- a foreign Workspace or stale source from an eligible current source.

Partial and Conflict are safety-preserving successes when their Final is
valid, grounded, and reviewed. Partial keeps verified facts and marks missing
downstream fields unknown. Conflict keeps both competing source identities and
does not select a winner. Budget failure and Provider uncertainty must never be
projected as `insufficient_evidence`; a Reviewer PASS is not human business
approval.

## Qualification revision V2: budget and source materialization

The first written freeze (V1) used `max_provider_requests = 2` for most cases.
That is not executable for a normal Search → Read → Final path: every
`ModelAdapter.decide()` or Provider `complete()` round consumes one model
request, and a bounded Final correction would consume one additional model
round. V1 is preserved byte-for-byte under `work/v3-freeze-v1/` and is not
used as the active freeze.

V2 changes only eligibility semantics and per-case budgets:

- one Search + one Read + Final requires 3 requests;
- one Search + two Reads + Final requires 4 requests;
- one Search + three Reads + Final requires 5 requests;
- an invalid Final may reserve one extra request only for a new bounded
  correction turn; it may never be accepted by deterministic rewriting;
- the budget-exhaustion cases permit Search + Read (2 requests) and stop
  before Final;
- an uncertain Provider outcome consumes its reserved request and is not
  retried, so the uncertain cases allow exactly 1 request;
- `max_steps` counts the same model turns; ToolRuntime execution itself does
  not consume Provider requests.

The V2 allocation is recorded by case ID in `freeze.json`. It is a
qualification audit, not a live result and not a guarantee that a real
Provider will choose the intended trajectory.

## Evaluation phases

### A — fixed-query Contract repair

Use only the development partition for diagnosis and repeated offline
red/green checks. Hold the query construction/retrieval strategy fixed. A
candidate passes this SPEC only if the focused tests show no weakening of
ToolRuntime permission, source identity, Verified Read, Grounding, Reviewer,
Recovery, budget, Workspace, Freshness, or Provider uncertainty guards.

The main Agent may compare the original baseline and candidate on the same
development input, but must report baseline failures separately from candidate
regressions. The development Gold is visible to the author and is not evidence
of general model quality.

### B — optional `query-only` comparison

Only after A is accepted may the main Agent compare the existing `query-only`
strategy. Candidate Contract code, source identity, budgets, tool permissions,
and evaluator remain fixed. If A consumes the available budget, B is skipped
and recorded as not run. A and B results must never be combined into one causal
claim.

## Acceptance criteria

1. The SPEC and implementation plan exist before production repair work.
2. `dev.json` contains exactly 10 unique cases and `final.json` contains
   exactly 10 unique cases; all 20 queries and case IDs are unique.
3. The category set covers every required V3 focus named above, including
   complete-invalid-Final, Partial, Conflict, Chinese boundary, Tool argument,
   same-file passages, Context, budget, uncertainty, Workspace, and Freshness.
4. Every document is synthetic, workspace-scoped, relative Markdown, and
   represented inline; no case references a real vault or old Gold file.
5. Each case has explicit author-visible Gold, and a loader test can prove that
   Gold-like fields are absent from the model-input projection.
6. Final cases are frozen before repair confirmation and are not executed,
   inspected for debugging, or used to tune prompts before the repair is
   complete. The final partition is executed at most once afterward.
7. `freeze.json` records the base commit, exact case IDs/counts/category
   coverage, author-visible/non-blind status, freeze ordering, V2 qualification
   reason, budget semantics, and SHA-256 for `SPEC.md`,
   `implementation_plan.md`, `dev.json`, and `final.json`.
8. The freeze task itself performs no final execution and no real Provider
   call, and does not modify production code or historical Gold.

## Protected files and review gate

This Planner step may write only:

- `docs/requirements/real-agent-contract-v3/SPEC.md`;
- `docs/requirements/real-agent-contract-v3/implementation_plan.md`;
- `tests/fixtures/real_agent_contract_v3/dev.json`;
- `tests/fixtures/real_agent_contract_v3/final.json`;
- `tests/fixtures/real_agent_contract_v3/freeze.json`.

The main Agent must review the SHA manifest and confirm the repair design before
any Worker changes production code. An independent Reviewer later checks the
implementation and acceptance evidence; this Planner step does not approve the
repair.

## Learning reflection

### Step

- Role: Planner
- Feature: real-agent-contract-v3
- Files touched or reviewed: current Runtime/Recovery/Grounding/Reviewer/
  Provider Ledger contracts, Adaptive Planning and real-agent reliability
  fixture loaders, and the five files listed above.

### What Changed

Defined a small author-visible, non-blind, 10+10 independent synthetic
contract set and its freeze manifest. The final partition has no scripted Final
stimulus and is reserved for one post-fix execution.

### What I Learned

Existing tests already enforce closed TaskPlan and Final contracts, exact
`source_context` identity, observed evidence IDs, workspace scope, and
append-only Provider accounting. The V3 set should expose those boundaries and
their failure classes rather than create a second runtime or a renamed copy of
the old benchmark.

### Evidence

The repository baseline is Adaptive Planning commit
`37e45b169f19c4edd29db59f637e690caab31ad6`; existing fixture contracts and
ToolRuntime schemas were inspected before authoring this set. The final SHA
values are recorded in `tests/fixtures/real_agent_contract_v3/freeze.json`.

### Next Step

Main Agent confirms the repair boundary and then Worker implements only the
approved Runtime/Final Contract changes. Worker runs development checks; final
is run once after repair completion, followed by independent review.
