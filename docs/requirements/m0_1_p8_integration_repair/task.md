# M0.1 P8 Integration Repair Task Record

Status: **ACCEPTED — independent Reviewer `PASS_WITH_FINDINGS`; human accepted
on 2026-08-30**.

## Governing SPEC

- [SPEC.md](SPEC.md)
- [implementation_plan.md](implementation_plan.md)
- Parent: `M0 — Production Agent Closeout`
- Primary Gate: Gate B integration readiness
- Supporting Gate: Gate A readiness only

M0.1 is closed. Gate A and Gate B remain incomplete, and no M0.2 Worker is
authorized by this acceptance record.

## Planner Snapshot — 2026-08-29

### Workspace

- Branch: `feature/p8-agent-runtime`.
- Committed `HEAD`: `3000056 feat(runtime): build durable model-driven agent runtime`.
- Worktree: dirty with concurrent Runtime/Retrieval and trajectory-evaluation
  changes.
- M0.1 source/test inspection: complete.
- M0.1 product tests: `NOT_RUN` by Planner.
- Real Vault/network/Provider access: none.

### Current Production Path

```text
CLI agent ask/connect
  -> RuntimeEngine.start_multi_agent
  -> RuntimeAgentAdapter
  -> Coordinator
  -> deterministic RetrievalAgent
  -> existing retrieval ToolRuntime / policy / ledger / checkpoint callback
  -> result artifact + final RuntimeState
```

### Current Isolated Model-Loop Path

```text
explicit test/caller
  -> FakeModelAdapter
  -> SingleAgentModelLoop
  -> ModelAdapter.decide
  -> existing ToolRuntime
  -> ModelArtifactStore + ModelExecutionRecord + RuntimeState
```

The paths share primitives but are not integrated. Production does not call
`SingleAgentModelLoop` or a real Provider.

## Verified Planner Findings

### Committed Defects

1. RuntimeEngine supplies `tool_ledger` and `tool_checkpoint_callback`, while
   committed RuntimeAgentAdapter rejects those keywords.
2. Committed RetrievalAgent bypasses ToolRuntime/ledger/checkpoint by calling
   legacy wrappers directly.
3. Committed Coordinator returns no retrieval ledger for final RuntimeState.
4. Committed RetrievalAgent silently skips read failures.
5. Committed Coordinator usage omits Reviewer policy calls.

### Dirty-Worktree Repair State

- RuntimeAgentAdapter signature and callback propagation: source-level repaired.
- One Adapter-composed retrieval ToolRuntime: source-level present.
- Deterministic RetrievalAgent typed ToolCalls: source-level present.
- Coordinator retrieval ledger/result projection: source-level present.
- Reviewer usage accounting: source-level present.
- Focused retrieval-runtime tests: present but untracked/unaccepted.
- Intermediate checkpoint evidence: not yet fully recorded.
- Current M0.1 focused/P8/smoke results: unavailable.

Verdict: **repair started, not accepted and not complete**.

### Explicit Current Limitation

Curator and Reviewer still use legacy direct callbacks and are not represented
in the retrieval ToolRuntime ledger. M0.1 will report this limitation and will
not claim full Gate B or silently migrate the whole Multi-Agent workflow.

## Planner Work Completed

- [x] Read repository contracts and frozen Master.
- [x] Reconstruct current production multi-agent path.
- [x] Reconstruct isolated SingleAgentModelLoop path.
- [x] Compare committed baseline with current dirty production/test diffs.
- [x] Re-verify the suspected RuntimeEngine/RuntimeAgentAdapter mismatch.
- [x] Classify M0.1 versus M0.2/M0.3 work.
- [x] Define exact production/test file boundary.
- [x] Define honest RED strategy for a pre-repaired dirty worktree.
- [x] Define focused, P8 regression, synthetic smoke, and full-suite rules.
- [x] Create M0.1 SPEC, implementation plan, and task record.
- [x] Human approved the exact Worker boundary in the M0.1 Worker handoff.
- [x] Worker implementation/evidence.
- [x] Independent Reviewer acceptance: `PASS_WITH_FINDINGS`, no blockers.
- [x] Human acceptance of completed M0.1 evidence on 2026-08-30.

## Accepted Worker Boundary (Closed)

The checklist below is retained as historical execution planning. Completion
and review evidence appears later in this record; it is not an open Worker
queue.

Production files:

- `src/linkloom/agents/runtime_adapter.py`;
- `src/linkloom/agents/coordinator.py`;
- `src/linkloom/agents/retrieval_agent.py`.

Test files:

- `tests/integration/test_retrieval_tool_runtime.py`;
- `tests/integration/test_handoff_trace.py`;
- `tests/integration/test_multi_agent_workflow.py`.

No other production or test file is authorized.

## Planned Worker Tasks

### W0 — Boundary Snapshot

- [ ] Confirm explicit human approval.
- [ ] Record branch, `HEAD`, status, and six-file pre-existing diff.
- [ ] Confirm no concurrent edit changed the Planner facts.

### W1 — Baseline And Regression Witnesses

- [ ] Record committed source mismatch without resetting the worktree.
- [ ] Run the current dirty focused baseline before Worker edits.
- [ ] Add supplied-ledger/callback and intermediate-checkpoint assertions first.
- [ ] Record actual RED or
  `RED_NOT_REPRODUCIBLE_IN_CURRENT_DIRTY_TREE` honestly.

### W2 — Minimal Integration Repair

- [ ] Complete Adapter interface/propagation only if focused evidence requires it.
- [ ] Complete Coordinator injection/projection/accounting only if required.
- [ ] Complete deterministic RetrievalAgent ToolRuntime handling only if required.
- [ ] Make zero cosmetic or downstream changes.

### W3 — Verification

- [ ] Focused M0.1 integration regression.
- [ ] Existing P8 regression slice.
- [ ] Synthetic production ask/connect smoke.
- [ ] Full-suite run and classification.
- [ ] Compileall, diff check, status, and no-real-Vault evidence.

### W4 — Worker Handoff

- [ ] Update this task record with exact results and Worker-added hunks.
- [ ] State Curator/Reviewer ledger limitation.
- [ ] State M0.2/M0.3 deferrals.
- [ ] Stop without commit, push, PR, M0.2, or M0.3.

## Planned Test Commands

Focused integration:

```powershell
python -m pytest -q `
  tests/integration/test_retrieval_tool_runtime.py `
  tests/integration/test_handoff_trace.py::test_runtime_multi_agent_persists_state_and_jsonl_trace `
  tests/integration/test_multi_agent_workflow.py
```

Synthetic production smoke:

```powershell
python -m pytest -q `
  tests/integration/test_handoff_trace.py::test_runtime_multi_agent_persists_state_and_jsonl_trace `
  tests/integration/test_multi_agent_workflow.py::test_ask_workflow_synthetic `
  tests/integration/test_multi_agent_workflow.py::test_connect_workflow_synthetic
```

Full suite:

```powershell
python -m pytest -q tests --tb=short
```

The exact P8 regression command is frozen in
[implementation_plan.md](implementation_plan.md).

## Current Test Evidence

| Check | Current M0.1 status | Interpretation |
|---|---|---|
| Focused M0.1 integration | NOT_RUN | Planner did not execute product tests. |
| Existing P8 regression | NOT_RUN | Must be run by approved Worker. |
| Synthetic production smoke | NOT_RUN | Must use fixture only. |
| Full suite | NOT_RUN | Historical count is not current verification. |
| Historical full suite | `458 passed, 2 skipped, 8 failed, 7 errors` | Recorded historical evidence only; not rerun here. |
| No real Vault/network | PASS for Planner | Source/doc/Git inspection only. |

## Acceptance Evidence Checklist

An independent Reviewer must receive:

- [ ] exact Worker-changed files and hunks;
- [ ] committed defect witness and current dirty baseline;
- [ ] focused integration result;
- [ ] checkpoint pending/terminal and final ledger identity evidence;
- [ ] P8 primitive regression result;
- [ ] synthetic production-smoke result;
- [ ] full-suite classification;
- [ ] compileall/diff/status results;
- [ ] no real Vault/network/Provider/Fixture/Gold evidence;
- [ ] proof that production remains deterministic;
- [ ] explicit Gate A/Gate B non-completion statement;
- [ ] M0.2 and M0.3 deferrals;
- [ ] remaining risks and skipped checks.

## Downstream Deferrals

### M0.2

- `ModelProviderAdapter.complete()` integration;
- `ModelResponse` lifecycle and normalized Provider errors/usage/metadata;
- production `previous_tool_call`;
- Provider durability/unknown-outcome handling;
- any separately approved real-provider smoke.

### M0.3

- model-selected retrieval tools and arguments;
- observation-driven continuation;
- model-driven final/termination;
- model-driven RetrievalAgent trajectory acceptance.

## Planner Recommendation

**Recommend human approval of M0.1 implementation only within the exact
three-production-file / three-test-file boundary.**

Reason: the committed defect is concrete, the current dirty repair is already
narrow, and objective focused/P8/smoke evidence can determine whether any
additional production edit is needed. This recommendation is not approval and
does not authorize the Worker until the human explicitly accepts the boundary.

## Learning Points

1. A composition bridge should pass existing dependencies and ownership
   boundaries; it should not duplicate ToolRuntime, policy, ledger, or
   checkpoint behavior.
2. A source-level repair in a dirty worktree remains unaccepted until its
   durable state projection and regression evidence are independently checked.

## Interview Evidence Planned

- committed-versus-dirty integration diagnosis;
- one-runtime/one-ledger ownership explanation;
- deterministic ToolRuntime lifecycle trace;
- failure visibility and no-private-error regression;
- an honest boundary: integration repair, not real Provider or complete Agent.

## Remaining Issues

- M0.1 is accepted with non-blocking findings deferred; this record does not
  invent or close findings that were not named in the human acceptance
  handoff.
- Gate A and Gate B remain incomplete.
- Curator/Reviewer legacy calls remain outside the retrieval ledger.
- Real-provider durable-loop integration remains M0.2 work.
- Model-driven RetrievalAgent migration remains M0.3 work.

## Learning Reflection

### Step

- Role: Planner.
- Feature: M0.1 P8 Integration Repair.
- Files created: `SPEC.md`, `implementation_plan.md`, `task.md`.

### What Changed

The next implementation boundary is now a concrete, testable integration
repair rather than a vague instruction to “connect P8.”

### What I Learned

The current dirty patch appears to contain most of the repair, so the safest
Worker behavior is evidence-first and incremental. The missing work may be
acceptance evidence rather than more architecture.

### Evidence

- committed/dirty source comparison;
- exact call-path reconstruction;
- exact file and test boundary;
- no production/test/Fixture/Gold/real-Vault modification by Planner.

### Next Step

Human approves or adjusts the exact M0.1 Worker boundary. A separate Worker
then implements/verifies; a separate Reviewer accepts or rejects the result.

## Worker Evidence — 2026-08-29

### Boundary Snapshot

- Role: independent Worker; acceptance remains a separate Reviewer decision.
- Branch / committed baseline: `feature/p8-agent-runtime` at `3000056`.
- The three production files and two tracked integration tests were already
  dirty; `test_retrieval_tool_runtime.py` was already untracked.
- The Worker did not reset, restore, overwrite, commit, push, create a PR, use
  a real Vault, call a Provider, or access credentials/network.

### RED / Pre-Fix Evidence

- Committed `HEAD` still proves the original mismatch: RuntimeEngine passes
  `tool_ledger` and `tool_checkpoint_callback`, while the committed Adapter
  signature rejects them; committed RetrievalAgent also calls legacy wrappers.
- Current dirty-tree baseline initially reported `10 passed, 4 errors`; all
  four errors were Windows global Temp ACL setup failures before assertions.
- A controlled sandbox-external rerun with a fresh synthetic basetemp produced
  `14 passed`.
- The Worker then added the missing ledger/callback identity and intermediate
  checkpoint witnesses before any production edit. They passed immediately:
  `2 passed`. Classification:
  `RED_NOT_REPRODUCIBLE_IN_CURRENT_DIRTY_TREE`.

### Worker Changes

- No production hunk was added: SHA-256 values for the three approved
  production files remained unchanged from the Worker snapshot.
- `tests/integration/test_retrieval_tool_runtime.py`: added proof that the
  Adapter-composed ToolRuntime retains the supplied ledger/callback identity,
  pending and terminal snapshots are visible, terminal trace events correlate
  one-to-one with call IDs, and read failures remain durable and path-safe.
- `tests/integration/test_handoff_trace.py`: added checkpoint-history proof for
  pending-to-completed retrieval lifecycles and exact final
  RuntimeState/result-artifact ledger equality.
- `tests/integration/test_multi_agent_workflow.py`: no Worker-added hunk; the
  pre-existing ask/connect usage assertions were retained.

### Verification Results

- Focused M0.1 integration: `15 passed`.
- Existing P8 regression slice: `159 passed`.
- Synthetic production smoke: `3 passed`.
- Full suite: `519 passed, 2 skipped, 27 failed, 7 errors`.
- Controlled mutation/writeback rerun outside the repository temp root:
  `42 passed`; the 19 full-suite mutation failures were environment/path-
  placement effects caused by an in-repository basetemp and are not M0.1.
- Remaining full-suite non-passes: 8 failures and 7 errors in legacy
  relation-evaluation tests that still reference the retired
  `D:\\webproject\\vault-steward` / historical C-drive checkout. Classified
  `PRE_EXISTING_UNRELATED`; not modified.
- `python -m compileall -q src/linkloom tests`: exit 0, with the known protected
  `tests/.pytest-trajectory-2b-data` listing warning.
- `git diff --check`: exit 0; only existing LF-to-CRLF warnings.

### Acceptance-Criteria Evidence

1. PASS — Engine/Adapter ledger and callback keywords align in the synthetic
   production smoke.
2. PASS — RuntimeEngine supplies the ledger/callback; identity regression
   proves the Adapter does not replace them.
3. PASS — Adapter only binds VaultReader callbacks and injects one existing
   retrieval ToolRuntime.
4. PASS — Retrieval remains deterministic `search -> zero-or-more reads`.
5. PASS — existing ToolRuntime schema/policy/budget/ledger/event ordering is
   exercised by focused and P8 regressions.
6. PASS — malformed, denied, exhausted, stale, search-failure, and read-failure
   cases are safe and pre-execution cases do not run executors.
7. PASS — call IDs are unique with exactly one terminal ledger/trace outcome.
8. PASS — checkpoint history contains pending/terminal facts; final state and
   result artifact ledgers are exactly equal.
9. PASS — ask/connect semantics pass and usage totals remain 7/9.
10. PASS WITH LIMITATION — Curator/Reviewer legacy direct callbacks are not in
    the retrieval ledger; Gate B is not complete.
11. PASS — focused, P8, and synthetic smoke are green.
12. PASS — full-suite non-passes are classified with a controlled rerun; no
    M0.1-caused or unknown failure remains.
13. PASS — no Provider/model-driven retrieval/Memory/Eval/Fixture/Gold/real-
    Vault/writeback scope was entered.
14. WORKER COMPLETE / REVIEW PENDING — changed files and evidence are recorded;
    independent Reviewer and human acceptance remain outstanding.

### Remaining Boundary

- RetrievalAgent is still deterministic and production still does not call
  `ModelProviderAdapter.complete()`.
- Curator and Reviewer calls are counted in usage where currently implemented,
  but their legacy direct callbacks are not projected into the retrieval
  ToolExecutionLedger.
- M0.2 owns durable real-provider integration and production
  `previous_tool_call`; M0.3 owns model-selected retrieval and termination.

### Worker Learning Reflection

#### Step

- Role: Worker.
- Feature: M0.1 P8 Integration Repair.
- Files changed by this Worker: the two approved integration tests and this
  task record; pre-existing production repairs were verified without churn.

#### What Changed

The unaccepted dirty repair now has objective identity, checkpoint, terminal
trace, safe-failure, focused, regression, smoke, and classified full-suite
evidence.

#### What I Learned

The repair did not need another Runtime abstraction: preserving one injected
ledger/callback and proving its projections was enough. A ledger records tool
facts; Runtime checkpoints own recoverable whole-run snapshots.

#### Evidence

`15` focused passes, `159` P8 passes, `3` smoke passes, the controlled `42`
mutation/writeback passes, compile exit 0, and diff check exit 0.

#### Next Step

An independent Reviewer should inspect the exact dirty production patch plus
the Worker-added regression hunks and accept or reject M0.1. Do not start M0.2
or M0.3 before that decision.

## Reviewer Blocking Correction — 2026-08-29

- Reviewer verdict before correction: `FAIL` for one blocking Criterion 9
  regression: a successful empty search was misclassified as retrieval
  failure.
- RED evidence: the new Coordinator-path regression failed because the
  retrieval task status was `failed` instead of `completed`.
- Root cause: RetrievalAgent treated every empty evidence set as failure, and
  the shared AgentResult contract requires a completed no-output result to
  carry an explicit non-output outcome.
- Correction: only a non-empty candidate set that yields no verified evidence
  enters the failure branch; a zero-candidate search returns completed with
  empty refs and an explicit `no_matching_notes` / `not_required` outcome.
- Search remains a completed terminal ledger record with no fabricated tool
  failure or fallback. Actual search/read ToolRuntime failures retain their
  failed ledger, ToolResult.error, trace, and fallback behavior.
- Targeted empty-search + read-failure regression: `2 passed`.
- Approved focused M0.1 tests: `16 passed`.
- P8 regression slice: `159 passed`.
- Synthetic production smoke: `3 passed`.
- Status: Worker correction complete; M0.1 awaits independent re-review and is
  not self-accepted.

## Independent Re-Review And Human Acceptance — 2026-08-30

- Independent Reviewer verdict: `PASS_WITH_FINDINGS`.
- Blocking findings: none.
- Previous blocker: resolved. A successful `search_notes` call with zero
  candidates remains a completed retrieval with empty evidence and does not
  fabricate `NO_VERIFIED_EVIDENCE`, retrieval failure, or fallback.
- Real search/read failures remain observable through normalized ToolResult,
  terminal ledger, trace, and workflow state behavior.
- Remaining Reviewer findings are non-blocking and deferred; they do not
  silently expand M0.1.
- Human coordinator decision: `M0.1 P8 Integration Repair — ACCEPTED`.
- Gate statement: M0.1 completes neither Gate A nor Gate B.
- Git boundary: no commit, push, PR, or GitHub write was authorized by this
  acceptance update.
