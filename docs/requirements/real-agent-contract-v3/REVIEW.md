# Real-Agent Contract V3 Independent Review

## Review Scope

- Feature: `real-agent-contract-v3`
- SPEC: `docs/requirements/real-agent-contract-v3/SPEC.md`
- Plan: `docs/requirements/real-agent-contract-v3/implementation_plan.md`
- Review target: the current uncommitted production diff and the new Final-correction regression tests on `codex/linkloom-real-agent-contract-v3`
- Files examined: `src/linkloom/runtime/model_loop.py`, `src/linkloom/runtime/recovery.py`, `src/linkloom/runtime/recovery_policy.py`, `src/linkloom/runtime/models.py`, `src/linkloom/agents/retrieval_agent.py`, `src/linkloom/agents/providers/gemini_api.py`, `src/linkloom/tools/runtime.py`, and `tests/integration/test_final_correction_v3.py`
- Review mode: read-only; no source, fixture Gold, or Provider data was changed

## Verdict

**Needs changes.** The normal in-process Final correction is bounded and keeps the original projection, Grounding, and Reviewer path, but a persisted invalid Final can lose its only correction request after a cold resume at `response_durable`. The current cold-resume regression does not exercise the remaining-budget case. The final partition is still `NOT_RUN`, and the recorded real Provider call count is `0`, so this review cannot support an integration-ready or real-provider claim.

## Findings

### Critical: cold resume can consume the remaining correction budget without invoking the correction

- Location: `src/linkloom/runtime/model_loop.py:2233-2251`; budget calculation is in `src/linkloom/agents/retrieval_agent.py:1253-1285`.
- Expected behavior: when the first invalid Final is durably recorded as `response_durable`, replaying that already-paid provider response must leave the remaining provider/model-turn slot available for exactly one correction request.
- Actual behavior: `decide_model_resume()` returns `reuse_durable_model_response`. The pending-response branch calls `reject_final_shape()`, then increments the local `model_steps` at lines 2248-2251. `RetrievalAgent.execute()` has already reduced `max_steps` to the remaining request budget. With an original budget of four and three prior rounds, resume receives `max_steps=1`; replay consumes that one local slot, the loop exits as `MODEL_MAX_STEPS_EXCEEDED`, and the correction model is never called.
- Reproduction: construct a durable state whose latest record is `response_durable` and whose stored response is invalid; resume with `SingleAgentModelLoop(max_steps=1, final_shape_validator=_validate_final_shape)`. Observed result: `budget_exhausted`, `model.call_count == 0`, model statuses `['final_rejected']`, and error `MODEL_MAX_STEPS_EXCEEDED` with `max_steps: 1`.
- Impact: a process crash between `response_durable` and Final-shape handling makes a safe, otherwise recoverable invalid Final fail even when the SPEC allocates one correction request. This violates the checkpoint/resume and bounded-correction contract.
- Required evidence/fix direction: add a regression that resumes through `RetrievalAgent.execute()` with only the residual request budget and proves one correction call occurs; account for durable response replay separately from new model turns, or otherwise preserve the residual slot without allowing a post-budget Provider call. Keep the existing one-rejection limit.

### High: the new cold-resume test is not runnable under the ordinary test command

- Location: `tests/integration/test_final_correction_v3.py:76`.
- Actual behavior: the test imports `from test_p84_durable_model_loop import _state, _runtime, _policy`, although the module is under `tests/unit/`. A normal `python -m pytest tests/integration/test_final_correction_v3.py` collection fails with `ModuleNotFoundError: No module named 'test_p84_durable_model_loop'` unless `tests/unit` is manually added to `sys.path`.
- Impact: the claimed Final-correction regression set is not reproducible from the repository's normal pytest entry point, and the cold-resume evidence can be accidentally omitted. Use a package-qualified import or a shared test helper that is importable from both test trees.

### High: the cold-resume test does not cover the residual-budget failure above

- Location: `tests/integration/test_final_correction_v3.py:95-105`.
- Actual behavior: both resume cases instantiate the loop with `max_steps=2`, even though production `RetrievalAgent.execute()` passes the remaining budget after counting prior model records. The test therefore gives response replay an extra local slot and reports a pass while the production residual case with `max_steps=1` fails as described above.
- Required test: persist at `response_durable`, resume with `max_steps=1`, and assert exactly one correction provider/model call, then separately exercise `final_rejected` resume with the same remaining budget.

### Note: the Final correction remains guarded by the normal final pipeline

The inspected path does not turn a rejected Final into success by rewriting it. `_validate_final_shape()` is shape-only; the eventual result still goes through `TeamDecisionResult.from_grounded_final`, task-plan scope checks, claim grounding, and Reviewer projection. During `CORRECT_FINAL`, `available_tools` is empty and the added action/proposal guards reject a tool call. The fabricated-fact and repeated-invalid cases are therefore directionally useful, subject to the cold-resume gap above.

## Acceptance Matrix

| Criterion | Evidence | Result |
|---|---|---|
| At most one new Final correction request | In-process correction tests and `rejected_finals` policy | Pass for uninterrupted execution; cold resume at `response_durable` fails the residual-budget case |
| Correction is counted against provider/model budget | `RetrievalAgent.execute()` remaining-budget calculation and normal correction tests | Fail for cold resume because durable replay consumes the only remaining local step |
| No tools during correction | `model_loop.py:1774`, `1949-1953`, `2340-2343`; correction tests assert `available_tools == []` | Pass for the covered paths |
| Invalid or invented Final cannot bypass Grounding/Reviewer | `retrieval_agent.py` final projection and fabricated-fact regression | Pass for the covered uninterrupted path |
| Partial and Conflict behavior preserved | Existing task-plan/recovery contracts; no final-partition execution | Not evidenced by this new correction test set |
| Provider Outcome Uncertain is terminal and not resent | Gemini adapter diff and existing V3 provider tests | Pass in offline tests reported by the Worker; no real Provider call |
| Source identity / Workspace guards remain enforced | Tool schema and semantic validator diff; focused contract tests | Pass in focused offline checks reported by the Worker |
| Final partition executed at most once after repair | `tests/fixtures/real_agent_contract_v3/freeze.json` | Not run; manifest says `NOT_RUN` and `real_provider_calls: 0` |

## Verification Evidence

- `git diff --check`: passed.
- Focused offline tests were inspected; the Worker reports the current focused/cold/provider checks as passing, but the standard invocation needs the test-import correction above.
- An independent direct reproduction of the residual-budget resume case produced `budget_exhausted 0 ['final_rejected']` with `MODEL_MAX_STEPS_EXCEEDED`.
- No real Provider was invoked. The V3 freeze manifest still records `final_execution: NOT_RUN` and `real_provider_calls: 0`.
- No real vault was used or mutated.

## Missing Evidence

- A regression proving correction after a cold `response_durable` resume with only one remaining request/model-turn slot.
- A normal repository pytest command that collects the new cold-resume test without manual `sys.path` changes.
- Final partition execution once, after the repair and development checks are accepted; until then its status remains `NOT_RUN`.

## Learning Reflection

### Step

- Role: Reviewer
- Feature: `real-agent-contract-v3`
- Files reviewed: production Final-correction, recovery, Provider, source-identity changes and new correction tests

### What Changed

The patch adds a single shape-validation correction path, records rejected Finals durably, blocks tools during correction, tightens source-context schema identity, and prevents retry classification for explicit unknown Provider outcomes.

### What I Learned

The normal correction path correctly separates shape rejection from the later evidence and Reviewer gates. Durable response replay is a separate local operation, however, and currently shares the same `max_steps` counter as a new model request. That distinction must be represented in the acceptance test and budget accounting.

### Evidence

The critical resume behavior was reproduced offline with a synthetic `response_durable` checkpoint and no Provider/network call. The freeze manifest and current worker evidence were read without opening the final partition for debugging.

### Next Step

Worker fixes the residual-budget cold-resume path and makes the cold-resume test importable/runnable; Reviewer reruns the focused offline checks and then records whether the final partition may execute once.

## Second review: cold-resume repair (2026-10-16)

### Disposition

The original cold-resume production blocker is **resolved for the inspected
`team-decision` Final-correction path**. The overall V3 acceptance gate remains
**blocked** and this review does not claim `PASS` or `INTEGRATION_CANDIDATE`:
the development qualification still does not execute all required cases, and
the frozen Final partition is still `NOT_RUN`.

### Evidence that removes the original blocker

- In `src/linkloom/runtime/model_loop.py:2248-2269`, replaying an already
  durable Final response marks the record `final_rejected` but no longer
  increments `model_steps`. A subsequent correction is therefore the only new
  model/provider turn and remains bounded by the residual `max_steps` and
  `max_provider_requests` calculated in `retrieval_agent.py:1262-1305`.
- The updated cold-resume test no longer imports a private `tests/unit` helper
  and resumes with `max_steps=1`. `work/v3-regression/reviewfix.log` records
  `7 passed`; an independent direct invocation of both
  `response_durable` and `final_rejected` boundaries also passed and observed
  exactly one correction call with that budget.
- The uninterrupted correction cases still pass the format, repeated-invalid,
  fabricated-fact, provider-envelope, and grounding/Reviewer assertions. The
  correction request is built with `available_tools=[]` at
  `model_loop.py:2338-2341`, and both legacy action and Provider proposal tool
  paths reject a tool after `final_rejected` at `model_loop.py:1774-1776` and
  `1949-1953`.
- Offline Provider/identity checks passed independently: the six V3 ledger
  tests plus the three Gemini/source-identity tests passed (`9 passed`). An
  explicit `ProviderOutcomeUnknownError` remains terminal, with no retry
  classification and one SDK call. The supplied `work/v3-regression/final-http.log`
  records the HTTP correction test as `1 passed`; no real Gemini request was
  made by this review.

### Remaining acceptance blocker: required D06 coverage is skipped

`tests/unit/test_contract_v3_cases.py::test_dev_run_writes_auditable_per_case_report`
currently fails (`5 passed, 1 failed`) because RAC3-D06 is returned by
`scripts/contract_v3_cases.py:995-1016` as
`N/A/ProductionBoundary` when its frozen `task_plan.goal` differs textually
from `input.query`. The test requires every development case to use a
`FakeModelAdapter/` model, so the run cannot qualify. D06 is the frozen case
for malformed source context, dependency binding, and executor suppression;
skipping it leaves the exact identity/tool safety path unobserved. RAC3-F07
has the same goal/query mismatch in the frozen Final input, so this gap would
also affect the one-time Final evidence. This is a qualification/runner gap,
not evidence that the guard is safe; it must be resolved without changing old
Gold or treating the skipped case as a pass.

### Final gate

The critical correction implementation is ready for a single Final-partition
run only after the development qualification/coverage gate is repaired or
formally dispositioned by the main Agent. Until then, keep the manifest's
`final_execution: NOT_RUN` and `real_provider_calls: 0` claims, and do not use
the Final cases to tune the runner or production behavior. The independent
review status therefore remains **needs changes / acceptance evidence
incomplete**, despite the cold-resume P0 being fixed.

## Third review: live/provider boundary (2026-10-16)

### Scope and disposition

This addendum is limited to `scripts/contract_v3_live.py`,
`scripts/contract_v3_provider.py`, and their `adaptive_provider` /
`adaptive_evaluation` boundaries. It does not re-run the frozen Final
partition or any real Provider call. No Gold, production source, or fixture
was changed.

There is no new Gold-leak, request-budget, or same-trial cross-phase
Provider-uncertainty bypass in the inspected path. The existing paired
artifacts checked here have matching case hashes. One evidence-integrity gap
remains and must be treated as a blocker for an *automated* old/new paired
claim.

### P1: phase-pair input identity is recorded but not enforced

`contract_v3_live.py:68-72` records a per-attempt `case_sha256`, but it does
not compare a phase-B case to the phase-A case. The Provider ledger task key
is only `(case_id, trial, phase)` (`contract_v3_live.py:87`,
`adaptive_provider.py:481-488,523-544`); it stores a request payload
fingerprint, not the public case or snapshot identity. Consequently, a caller
can invoke phase B with the same case ID and a changed query/documents or
snapshot and receive an accepted run. The resulting pair could attribute an
input change to the Contract fix. The current `normal-old/new` and
`partial-old/new` artifacts are clean on this check (equal case hashes,
`rules` planning, and the expected baseline/current code roots), but that is
manual evidence rather than an enforced invariant. Before accepting a paired
comparison, bind B to A's canonical case hash plus snapshot hash and planning
profile (or reject a mismatched pair in the orchestrator).

Also keep `phase` and `result.variant` separate in reports: phase B with
`--planning rules` intentionally produces `result.variant = "A"` because the
variant field identifies planning strategy, not the old/new Contract phase.
Inferring the Contract comparison axis from `variant` would mislabel an
otherwise valid pair.

### Confirmed safe boundaries in this scope

- `contract_v3_live.py` accepts only the public case fields and never reads
  `scoring-gold.json`; `adaptive_evaluation.run_case()` has no Gold input
  path. Gold is consumed only by the separate scoring function after the
  run.
- `AdaptiveProvider._admit()` appends a durable reservation before the SDK
  call, checks request/cost/deadline limits, and `_sdk_call()` performs one
  invocation. The official SDK is configured with `HttpRetryOptions(attempts=1)`.
  Unknown usage remains reserved, so the accounting path does not bypass the
  hard cap.
- A finished strict `provider_outcome_uncertain` marks `(case, trial)`
  terminal and blocks a later phase for that same trial; a pending ledger
  reservation fails closed on provider restart. The live runner always uses
  trial `1`, so the inspected A/B path has no cross-phase replay after strict
  uncertainty. A new numeric trial is an independently admitted trial and is
  outside this paired phase path.

### Final-run decision

The scripts provide no reason to re-run the already-recorded one-time Final
diagnostic. Keep its `0/10` result out of integration evidence as directed;
the one-time claim gate remains the frozen runner/manifest responsibility.
This addendum does not change the prior overall verdict: the review is not a
`PASS` or `INTEGRATION_CANDIDATE` claim.

## Fourth review: phase-pair identity repair (2026-10-16)

The prior P1 is **resolved for the inspected live runner path**. In
`scripts/contract_v3_live.py`, `verify_pair_identity()` now runs after public
case/snapshot validation but before trial-directory creation, `attempt.json`
writing, `module.connect()`, or `provider.task()`. It requires exactly one
phase-A attempt for the same case, equal canonical case-file SHA (which
includes the snapshot identity fields), equal planning mode, and an existing
baseline result; the B attempt records the baseline attempt SHA.

Evidence reviewed: `work/v3-regression/pair-identity.log` reports `4 passed`,
and `pair-identity-existing.log` validates all three existing normal/partial/
conflict pairs. No SDK or new paid request was used by this review. No further
finding is raised in this narrow scope. The recorded final Provider-uncertain
run remains diagnostic evidence only; this does not change the overall
no-reliability-improvement or no-integration-claim status.
