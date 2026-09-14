# M1-G8 Review Gate

- `current_slice`: `Final Gate — Golden 8 production evaluation`
- `cycle`: `1`
- `review_cycle`: `1`
- `base_sha`: `48c8eb450e1150ad23f4219f6c292e0c5d8658c2`
- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `ACCEPT`

## Slice B Historical Goal

Prove the frozen synthetic `aer-002` Team Decision path can use a bounded,
model-visible set of multiple independently verified notes to synthesize the
current rollout boundary and open-action states.  The model remains the only
semantic synthesizer; the runtime owns scope, bounds, verified-source filtering,
durability, tool execution, and result validation.

## Slice B Historical Frozen Cases And Inputs

- Primary case: `aer-002`, loaded only through its public question,
  `workspace_id`, `source_notes`, and `distractor_notes` fields.
- Regression case: accepted `mps-001` production vertical slice.
- Each test copies only the frozen synthetic workspace into a private `.tmp`
  root.  It hashes both the original source workspace and the copied vault
  before and after execution.
- No test controller or production request receives a dataset `expected_*` or
  Gold field.  Assertions additionally serialize every FakeModel request and
  reject `expected_` there.

## Slice B Historical RED Evidence And Root Cause

1. Budget RED: `aer-002` requested eight model steps but the retrieval task
   retained Slice A's cap of three.  The controlled production path stopped
   after `search_notes -> read_verified_note -> read_verified_note` with
   `MODEL_MAX_STEPS_EXCEEDED`, before it could inspect four independent notes
   and produce Final.
2. Context RED: after the budget path was opened, the final sixth model turn
   could see only its latest tool observation.  The context-dependent
   FakeModel failed safely with `MODEL_ADAPTER_FAILED` because
   `ModelTurnRequest.evidence_context` did not exist; after the request field
   was introduced but before runtime wiring, the context remained empty and
   the same final-turn characterization failed.
3. Cold-restart test assembly initially used a fresh in-memory checkpoint on
   restart and therefore found no persisted thread.  The corrected test points
   the restarted engine at the same SQLite checkpoint.  This was a test setup
   defect, not a production-path failure.

Root cause: the Team Decision model-loop budget was not derived from the
persisted request policy, and the durable model request contained only the
latest observation rather than a bounded, current-task projection of verified
evidence.

## Slice B Historical Implementation

- `Coordinator` receives a request-derived Team Decision model budget while
  retaining the three-step default for `ask` and `connect`; the runtime
  preserves two Coordinator-only review/publication slots.
- `ModelTurnRequest` now has an additive, backward-compatible
  `evidence_context` field.  It accepts at most eight successful
  `search_notes` or `read_verified_note` results, only when every value is a
  quote-bearing `status="verified"` evidence record, and caps its serialized
  size at 24 KiB.  Historical request artifacts without the field load as an
  empty context.
- The durable and non-durable model loops build that context deterministically
  from completed ledger records scoped to the current `run_id`, `task_id`, and
  `agent_id`, and only from earlier sequences.  The exact request, including
  the context, is persisted before model invocation.
- Only `team_decision` enables this eight-result projection.  `ask` and
  `connect` retain their existing last-observation behavior.
- Gemini request mapping puts the validated evidence projection in the first
  user message as `verified_evidence_context=...`; the total model-visible
  input remains bounded by the existing 32 KiB provider limit.  No provider
  client is instantiated by the Slice B tests.

## Slice B Files

Production:

- `src/linkloom/agents/coordinator.py`
- `src/linkloom/agents/registry.py`
- `src/linkloom/agents/runtime_adapter.py`
- `src/linkloom/agents/model_adapter.py`
- `src/linkloom/runtime/model_loop.py`
- `src/linkloom/agents/retrieval_agent.py`
- `src/linkloom/agents/providers/gemini_api.py`

Tests:

- `tests/integration/test_m11_aer_002_vertical_slice.py`
- `tests/unit/test_m11_verified_evidence_context.py`

The worktree also contains accepted-but-uncommitted Slice A files and other
user-owned untracked material.  Reviewer should evaluate Slice B hunks and
must not interpret those carry-over files as newly claimed by this cycle.

## Slice B Historical GREEN Evidence

### Primary Trace

`aer-002` passes through the real RuntimeEngine -> RuntimeAgentAdapter ->
Coordinator -> RetrievalAgent -> SingleAgentModelLoop -> ToolRuntime path with
the controlled trajectory:

```text
search_notes -> read_verified_note x4 -> Final
```

The final model request contains five durable context results (one search and
four reads) spanning:

- `02-metric-design-review.md`
- `03-rollout-decision.md`
- `04-owner-status.md`
- `06-threshold-review.md`

The generated `TeamDecisionResult` is `partial`, retains deterministic metrics
as the release gate, and reports exactly these evidence-grounded action states:

- `Jae Min / in_progress`
- `unassigned / unassigned`
- `unassigned / blocked`

All final evidence references are asserted to be a subset of the model-visible
verified context.

### Cold Resume Trace

The SQLite checkpointer raises a test-only `ProcessCrash` only after the final
response is `response_durable`.  The persisted final request artifact is read
by hash and contains five context results.  A fresh engine using the same
SQLite checkpoint resumes without any replacement-model call or tool replay;
its ledger is byte-for-byte equivalent and its TeamDecisionResult equals a
fresh `aer-002` run.

### Commands And Results

```text
python -m pytest -q -p no:cacheprovider tests/unit/test_m11_verified_evidence_context.py tests/unit/test_p83_fake_model_loop.py tests/unit/test_p84_durable_model_loop.py tests/unit/test_p85_gemini_api_adapter.py tests/unit/test_p85_model_provider_contracts.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/eval/test_golden8_freeze.py
# 85 passed in 12.76s

python -m pytest -q -p no:cacheprovider tests/unit/test_p83_durable_tool_boundary.py tests/unit/test_p84_recovery.py tests/unit/test_p84_artifacts.py tests/unit/test_p85_schema_mapping.py tests/unit/test_p85_wp3_durable_integration.py tests/integration/test_p85_durable_provider.py
# 106 passed in 3.63s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 75.57s

python -m compileall -q src/linkloom tests
# pass

git diff --check
# pass; only existing CRLF and inaccessible old-temp-directory warnings
```

The only test-output warning across these commands is the existing
`pytest-asyncio` fixture-loop deprecation warning.

## Slice B Historical Safety Checks

- Frozen seed and Golden 8 status/diff checks are empty; no seed, manifest,
  workspace, or freeze-test file changed.
- The private-copy hash assertions passed for all `aer-002` runs.
- No real Obsidian vault was accessed or changed.
- No credential access, provider call, network call, writeback, UI, MCP,
  vector/embedding, memory framework, commit, or push occurred.
- A few pre-existing Windows ACL-denied temporary directories cause `git`
  directory warnings only; they are outside the claimed Slice B files and were
  neither modified nor deleted.

## Slice B Historical Deferred Work

- No broader Golden 8 semantic acceptance, threshold, or score claim.
- No raw full-note/section bundle; the context remains line-level verified
  evidence returned by existing safe tools.
- No multiple-search evidence-ID redesign, generic conversation memory, or
  cross-session semantic memory.
- No real provider evaluation.  Gemini mapping is covered offline only.

## Slice B Historical Worker Reflection

- **Role / boundary:** Worker for approved Slice B `aer-002`; no acceptance
  decision is made here.
- **What changed:** A bounded verified-evidence context and Team Decision
  budget path make the model's multi-note synthesis observable and durable.
- **What was learned:** A durable last-observation loop can safely execute
  many tools yet still fail semantic synthesis unless prior verified evidence
  crosses the model boundary.  Scope and byte limits must be enforced where
  that boundary is constructed, not in business-result code.
- **Evidence:** RED characterizations, production-path frozen-copy tests,
  hash-backed final request artifact, cold resume, MPS/M0/provider regressions,
  and protected-path audit above.
- **Next step:** Independent Reviewer records `ACCEPT`, `CHANGES_REQUIRED`, or
  `BLOCKED` in this file.  Worker makes no further code changes until that
  verdict.

## Reviewer Cycle 1 — `aer-002`

### Verdict

`CHANGES_REQUIRED`

### Independent review evidence

- Reviewed the current Gate, the real worktree diff from
  `48c8eb450e1150ad23f4219f6c292e0c5d8658c2`, the claimed Slice B production
  path, the frozen `aer-002` record, and all five rollout workspace notes.
- Protected frozen paths have no tracked or untracked change:
  `docs/requirements/m1_team_decision_eval_seed` and
  `tests/eval/test_golden8_freeze.py`.
- Independently passed:
  - `tests/integration/test_m11_aer_002_vertical_slice.py` — `3 passed in 32.30s`;
  - `tests/unit/test_m11_verified_evidence_context.py`, accepted
    `mps-001` vertical slice, and Golden 8 freeze — `11 passed in 20.86s`;
  - M0.3/M0.4/ToolRuntime compatibility slice — `53 passed in 64.91s`.
- `python -m compileall -q src/linkloom tests` exited `0`; its only output was
  the pre-existing inaccessible `tests/.pytest-trajectory-2b-data` directory.
  `git diff --check` exited `0` with only existing CRLF/inaccessible-temp
  warnings.
- The real vertical trace and cold-resume path are present, and the evidence
  context is correctly built from prior, scoped, successful verified ledger
  results and persisted with the request.  These facts do not overcome the two
  blockers below.

### Blocking findings

#### B1 — P0: FakeModel is a case-specific hidden-answer oracle

- **File/function:**
  `tests/integration/test_m11_aer_002_vertical_slice.py`,
  `_multi_note_context_model()` (lines 353-396) and
  `_aer_result_from_visible_context()` (lines 178-304).
- **Reproducible evidence:** the policy hard-codes the four relevant frozen
  paths and source fragments (lines 357-362), then emits the frozen decision,
  rationale, action descriptions, owners, statuses, unresolved items, and
  uncertainty as literals (lines 222-295).  Those literals reproduce the
  `aer-002` Gold outcome despite the test only checking that request JSON does
  not contain the token `expected_`.
- **Broken invariant:** Frozen Gold may be used only by assertion/evaluation;
  a controlled model must not retain a hidden case answer or relevance map.
  Slice B must prove genuine multi-note synthesis from model-visible evidence,
  not a scripted answer that merely verifies expected snippets exist.
- **Why this blocks this Slice:** the passing six-turn trace proves ledger
  plumbing, but it cannot prove that the model synthesized the rollout state,
  owner/null discipline, or inactive-threshold conclusion from the visible
  evidence.  Removing one relevant context value makes the oracle fail, but
  retaining all values still causes it to output a pre-authored answer.
- **Minimal fix direction:** replace the case-specific target paths, quote
  fragments, and answer literals in the FakeModel with a controlled policy
  whose emitted fields are derived from the exact `request.evidence_context`
  values and whose behavior changes or fails when a required visible value is
  removed.  Keep all frozen expected values/refs exclusively in assertions or
  evaluation helpers; add a regression that rejects a Final policy relying on
  a closure-held answer.

#### B2 — P0: Claim-ref validation accepts ledger evidence absent from the final model request

- **File/function:**
  `src/linkloom/runtime/model_loop.py::_verified_evidence_context()`
  (lines 161-208),
  `src/linkloom/agents/retrieval_agent.py::_project_result()`
  (lines 298-306), and Gemini request projection
  `src/linkloom/agents/providers/gemini_api.py::build_gemini_request()`
  (lines 340-350).
- **Reproducible evidence:** create ten small successful verified tool results
  before a Final.  The bounded context retains at most eight prior results;
  the current observation can expose the tenth result, leaving the ninth
  outside the final request.  `_project_result()` nevertheless builds
  `visible_refs` from *every* scoped ledger record and passes them to
  `TeamDecisionResult.from_grounded_final()`.  A Final that cites the ninth
  ref therefore validates and can publish even though the stateless provider
  prompt contains neither that result in `verified_evidence_context` nor as
  the current tool observation.
- **Broken invariant:** for the bounded Slice B context, every material claim
  ref must be a successful verified result actually visible to the Final model
  request.  The Gate's stated final-ref subset assertion is only a happy-path
  test assertion; production currently permits the broader ledger set.
- **Why this blocks this Slice:** this is the central evidence-trust boundary
  for multi-note synthesis.  The current five-result `aer-002` trace does not
  reach the cap, so it masks the failure exactly where bounded-context behavior
  must be fail-closed.
- **Minimal fix direction:** derive eligible refs from the exact durable Final
  `ModelTurnRequest` (its validated `evidence_context`, plus its current
  observation when not already represented), not from all historical ledger
  records.  Add a production-path regression with more than the context cap
  that fails closed for a ref omitted from the Final request, and retain the
  existing cold-resume proof for the same persisted request.

### Acceptance boundary after this cycle

Not accepted.  The review verifies frozen-source immutability, real runtime
composition, bounded-context persistence, cold resume, and scoped M0
compatibility.  It does not accept `aer-002` until B1 and B2 are corrected and
independently re-reviewed.  Broader Golden 8 semantic evaluation remains
deferred.

## Worker Cycle 2 Correction — `aer-002`

- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `PENDING`
- No acceptance decision is made by the Worker in this section.

### B1 correction — controlled model derives its result from visible evidence

The controlled model in
`tests/integration/test_m11_aer_002_vertical_slice.py` no longer carries the
four frozen source paths, frozen quote fragments, person names, action text,
or a pre-authored `aer-002` answer.  Its Final policy receives only the exact
`request.evidence_context` values supplied by the real model loop and:

- selects a release-gate quote by generic semantic tokens;
- derives action status from generic observed status language;
- returns the observed quotes and opaque visible evidence IDs as result data;
- requires four distinct sources, including a diagnostic source distinct from
  the decision/action sources; and
- raises a deterministic error when a required evidence class is absent.

Frozen paths, expected result values, and the `Jae Min` assertion remain only
in evaluation assertions, not in the model controller or its Final generator.
The static source audit confirms that any remaining frozen paths/names are
assertion fixtures rather than inputs to the controlled model policy.

New regression:

```text
test_aer_002_context_policy_fails_when_a_required_visible_evidence_class_is_removed
```

It runs the real `aer-002` production path, removes every Final-visible value
whose derived generic status is `blocked`, and verifies that the controller
fails with `missing visible blocked evidence`.  This makes a missing
model-visible evidence class observable rather than allowing a closure-held
answer to survive.

### B2 correction — publication refs come from the durable Final request

`RetrievalAgent._final_model_visible_refs()` now loads the latest final
`ModelExecutionRecord` for the current run/task/agent, verifies the artifact
SHA-256 and runtime identity, parses the durable `ModelTurnRequest`, and
derives eligible refs only from its validated `evidence_context` plus current
validated observation.  `TeamDecisionResult.from_grounded_final()` receives
that exact set instead of every historical scoped ledger entry.

New production-path regression:

```text
test_team_decision_rejects_a_claim_ref_omitted_from_the_final_model_request
```

The test makes ten successful verified results through the real RuntimeEngine
path.  The final request has the eight-result bounded context and a current
tenth observation; the controlled Final cites the ninth result, which is
visible in neither location.  Before the production change this completed;
the corrected path fails closed.  The test namespaces only its synthetic tool
output IDs to avoid the existing repeated-search-ID collision masking the
context-cap condition; source and verified-result validation remain real.

### Cycle 2 GREEN evidence

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_team_decision_vertical_slice.py tests/unit/test_m11_verified_evidence_context.py tests/eval/test_golden8_freeze.py
# 16 passed in 7.20s

python -m pytest -q -p no:cacheprovider tests/unit/test_m11_verified_evidence_context.py tests/unit/test_p83_fake_model_loop.py tests/unit/test_p84_durable_model_loop.py tests/unit/test_p85_gemini_api_adapter.py tests/unit/test_p85_model_provider_contracts.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/eval/test_golden8_freeze.py
# 87 passed in 7.93s

python -m pytest -q -p no:cacheprovider tests/unit/test_p83_durable_tool_boundary.py tests/unit/test_p84_recovery.py tests/unit/test_p84_artifacts.py tests/unit/test_p85_schema_mapping.py tests/unit/test_p85_wp3_durable_integration.py tests/integration/test_p85_durable_provider.py
# 106 passed in 1.73s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 7.59s

python -m compileall -q src/linkloom tests
# pass (only pre-existing inaccessible test-temp-directory output)

git diff --check
# pass (only existing CRLF / inaccessible-temp-directory warnings)
```

The existing `aer-002` cold-resume test remains in the Green suite: the
hash-verified Final request, including bounded evidence context, is resumed
without tool or replacement-model replay.  The protected frozen seed and
Golden 8 paths remain absent from both status and diff audits.  No real vault,
provider, credential, network, writeback, UI, MCP, vector/embedding, memory
framework, commit, or push was used.

### Worker reflection

The correction moves both trust decisions to their actual boundaries: the
test controller has no case answer beyond observed visible values, and the
production result validator cannot authorize a reference the Final model did
not receive.  The remaining decision is solely the independent Reviewer’s
second-cycle verdict.

## Reviewer Cycle 2 — `aer-002`

### Verdict

`ACCEPT`

### What is proven

- The frozen `aer-002` synthetic workspace is copied to a private test root;
  the frozen source, manifest, and freeze test have no diff, and source hashes
  remain unchanged through the vertical path.
- A controlled model reaches the actual
  `RuntimeEngine -> RuntimeAgentAdapter -> Coordinator -> RetrievalAgent ->
  SingleAgentModelLoop -> ToolRuntime -> TeamDecisionResult -> durable result`
  composition with `search_notes -> read_verified_note x4 -> Final`.
- The Final policy contains no frozen source path, expected answer, owner name,
  or case-specific quote fragment.  It derives its returned quote-backed fields
  and opaque refs from the exact final `evidence_context`; frozen paths/names
  found by the static audit are assertion-only.
- Material Team Decision refs now come only from the hash-verified durable
  Final `ModelTurnRequest` context plus its current verified observation.  The
  over-cap production regression proves a ninth historical ref, absent from the
  Final request, fails closed with `TEAM_DECISION_CONTRACT_ERROR`.
- Cold resume reuses the durable Final request/result without replaying tools or
  invoking a replacement model.  `ask`/`connect`, M0.3, M0.4, ToolRuntime,
  `mps-001`, and Golden 8 freeze scoped regressions remain green.

### Independent checks

- `tests/integration/test_m11_aer_002_vertical_slice.py`,
  `tests/integration/test_m11_team_decision_vertical_slice.py`,
  `tests/unit/test_m11_verified_evidence_context.py`, and
  `tests/eval/test_golden8_freeze.py`: `16 passed in 6.67s`.
- Full focused context/provider/durability suite: `87 passed in 8.01s`.
- M0.3/M0.4/ToolRuntime compatibility slice: `53 passed in 7.14s`.
- `python -m compileall -q src/linkloom tests`: pass, with only the existing
  inaccessible test-temp-directory notice.
- `git diff --check`: pass, with only existing CRLF/inaccessible-temp warnings.
- No real vault, provider, credential, network call, writeback, UI, MCP,
  vector/embedding, memory framework, commit, or push was used.

### Acceptance boundary and deferred work

This accepts Slice B `aer-002` only: bounded, model-visible multi-note
evidence accumulation; final-request claim-ref membership; controlled
production-path synthesis; and cold-resume durability.  It does not claim
Golden 8 completion, all-case semantic correctness, a real-provider result,
raw full-note/section reads, multiple-search ID redesign, generic memory, or
the later Slice C/D/E semantics.  Those remain deferred to their separately
reviewed Slices.

## Worker Cycle 1 — Slice C: `drm-003 + inc-004`

- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `ACCEPT`
- This section is the current handoff.  The accepted Slice B record above is
  retained as historical evidence.

### Goal and frozen inputs

Prove the existing Team Decision production composition can synthesize, from
multiple model-visible verified notes:

- `drm-003`: a rejected alternative and its independently supported rationale,
  current decision boundary, and a single unresolved legal action with null
  owner/deadline discipline; and
- `inc-004`: the selected mitigation plus distinct open actions with owner,
  deadline, and blocked / in-progress / pending status.

Each test copies only the real frozen synthetic workspace into a unique private
`.tmp` root, uses public `user_question`, `workspace_id`, `source_notes`, and
`distractor_notes` to start production, and hashes both the original and copied
Markdown trees before and after.  Full frozen records (including `expected_*`)
are loaded only after the model run for assertions.

### RED evidence and root cause

The first real production-path characterization failed at the Final turn for
both cases with `MODEL_ADAPTER_FAILED`: the new controlled model conflated
“all planned reads consumed” with “reads not planned,” then tried to parse a
read observation as a search-result list.  This was a test-controller state
machine bug, not a Runtime failure.

After that correction, the `drm-003` output published both a blocked and a
pending legal sign-off as separate actions.  The frozen evidence showed these
were two descriptions of the same action.  A generic source-derived action
identity merge now retains the stronger blocked state while preserving the
three distinct `inc-004` actions.

A subsequent RED showed longest-quote selection incorrectly preferred a
discussion/draft-like source over the current decision record.  The controlled
model now ranks visible decision evidence by `selected` / `current` /
`approved` signals and penalizes `discussion`, `comment`, `draft`, and
`proposed`; it requires an independent causal audit reason for a rejected
alternative.  No frozen path, owner name, quote fragment, or expected answer
was added to this policy.

The audit therefore found no Slice C production Runtime, contract, budget,
context, or durability-surface change to make.  Slice B's bounded durable
evidence context, exact Final-request ref validation, and eight-step Team
Decision policy already cover these trajectories.  The minimal Slice C change
is a real frozen-case production-path characterization with a source-derived
controlled model.

### Implementation boundary

Production files changed for Slice C: **none**.

New test file:

- `tests/integration/test_m11_slice_c_vertical_slice.py`

The controller retains only public case inputs and opaque refs discovered by
the real search ToolResult.  Its Final derives every output quote, owner,
deadline, status, and evidence reference from the exact
`request.evidence_context` values.  The test statically checks that the
controller and Final generator contain none of `expected_`, frozen case IDs,
frozen person names, or target answer fragments.  Expected fields remain in
post-run assertion code only.

### Production execution and grounding traces

`drm-003` follows:

```text
search_notes -> read_verified_note x4 -> Final
```

The four real reads are exactly the frozen relevant notes for draft,
independent review, current decision, and legal status.  The Final has five
bounded context ToolResults and produces one rejected alternative, current
partial decision, and one blocked legal action with `owner=null` and
`deadline=null`.

`inc-004` follows:

```text
search_notes -> read_verified_note x2 -> Final
```

The reads are the real mitigation decision and remediation status notes.  The
Final has three bounded context ToolResults and publishes exactly three
distinct action/unresolved states: blocked with a dated owner, in progress
with an owner and null deadline, and pending with the explicitly unassigned
owner and null deadline.

For both cases, every material declared ref is asserted to be a subset of the
successful verified evidence visible in the Final request.  The request trace
contains no `expected_` field.  No model result relies on only the last
observation: the Final uses the persisted evidence-context projection.

### No-hidden-answer regressions

- Removing the independently visible causal reason from `drm-003` makes the
  Final policy fail with `missing visible rejection reason evidence` rather
  than recreating a rejection rationale from closure state.
- Removing every visible `in_progress` value from `inc-004` produces a result
  without an in-progress action; the controller does not restore it from
  hidden state.

These are test-only reductions of already visible values after the real run;
they do not edit a frozen source or pass Gold into the controller.

### GREEN evidence and regressions

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_c_vertical_slice.py
# 4 passed in 27.91s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/eval/test_golden8_freeze.py
# 30 passed in 98.32s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 72.83s

python -m pytest -q -p no:cacheprovider tests/unit/test_p83_durable_tool_boundary.py tests/unit/test_p84_recovery.py tests/unit/test_p84_artifacts.py tests/unit/test_p85_schema_mapping.py tests/unit/test_p85_wp3_durable_integration.py tests/integration/test_p85_durable_provider.py
# 106 passed in 21.77s

python -m compileall -q src/linkloom tests
# pass; only the existing inaccessible test-temp-directory notice

git diff --check
# pass; only existing CRLF / inaccessible-temp-directory warnings
```

Slice C changes no production durability surface, so it adds no duplicate cold
resume implementation.  The accepted `aer-002` cold-resume regression remains
in the 30-test focused suite and proves the existing Final-request context is
reused without tool or replacement-model replay.

### Safety and protected-path audit

- `git status --short -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py`
  and the corresponding name-only diff are empty.
- Frozen source and private-copy hash assertions pass for every Slice C run.
- No real Obsidian vault, provider call, credential access, network call,
  writeback, UI, MCP, vector/embedding, generic memory framework, commit, or
  push occurred.
- Existing inaccessible Windows temporary directories and CRLF warnings are
  outside the claimed Slice C change and were neither modified nor deleted.

### Deferred work

- Slice D: `ret-005 + iti-005` stale/current precedence, proposed versus
  assigned, null discipline, and calibrated uncertainty.
- Slice E, full Golden 8 production evaluation, real-provider evaluation,
  multiple-search evidence-ID redesign, raw full-note/section reads, and
  generic memory remain unclaimed.

### Worker reflection

Slice C demonstrates that the Runtime should keep enforcing evidence identity,
durability, and publication boundaries while model-side synthesis resolves
semantic conflicts: duplicate action mentions and authoritative current versus
discussion/draft language.  The Worker made no acceptance decision and now
freezes this boundary for independent review.

## Reviewer Cycle 1 — Slice C: `drm-003 + inc-004`

### Verdict

`ACCEPT`

### Independent review evidence

- **Review-cycle safety:** `review_cycle` is `1`; no blocker was found, so the
  human-escalation threshold is not implicated.
- **Frozen authenticity:** the real `drm-003` and `inc-004` case records and
  their matching workspaces were independently read. The test copies the
  frozen workspace into a private temporary root and verifies source hashes.
  The frozen seed directory and `tests/eval/test_golden8_freeze.py` have no
  working-tree diff. Expected fields are loaded only after the run for
  assertions; the model controller and its result generator contain no
  `expected_` field access, frozen case ID, target owner name, or target answer
  fragment.
- **Production authenticity:** both cases enter through `RuntimeEngine` and
  exercise the established `RuntimeAgentAdapter -> Coordinator ->
  RetrievalAgent compatibility host -> SingleAgentModelLoop -> ToolRuntime ->
  TeamDecisionResult -> durable result` route. The Slice C test does not call
  a parser or unit helper as a substitute for that vertical path.
- **`drm-003`:** the completed path is `search_notes -> read_verified_note x4
  -> Final`. The final synthesis distinguishes the superseded 90-day draft,
  the independently evidenced audit-history rationale, the current 180-day /
  365-day boundary, and the blocked legal-sign-off action. It preserves the
  frozen distinction between a proposed person and an assigned owner: the
  action has `owner=null` and `deadline=null`, rather than inventing either.
- **`inc-004`:** the completed path is `search_notes -> read_verified_note x2
  -> Final`. The final result identifies the selected mitigation and keeps the
  three open actions distinct: blocked with a dated owner, in progress with an
  owner and null deadline, and pending with an explicitly unassigned owner and
  null deadline.
- **Evidence trust and ownership:** every published claim reference is asserted
  against successful ToolResults visible in the Final request, not merely the
  overall ledger. The two reduction checks demonstrate that removal of the
  visible rejection rationale fails synthesis and removal of visible
  `in_progress` evidence removes that state. Semantic selection remains in the
  controlled model; Runtime behaviour remains execution, bounded context,
  schema/provenance validation, and durability.
- **Durability and compatibility:** Slice C changes no production durability
  surface, so a new cold-resume test is not required. The previously accepted
  bounded Final-context cold-resume coverage remains in the focused suite.
  Slice A/B, TeamDecision contract, Golden freeze, M0.3/M0.4, and ToolRuntime
  compatibility checks pass.

### Reviewer-executed checks

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_c_vertical_slice.py
# 4 passed in 25.83s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/eval/test_golden8_freeze.py
# 30 passed in 94.38s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 63.67s

python -m compileall -q src/linkloom tests
# exit 0; existing inaccessible test-temp-directory notice only

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both exit 0; existing CRLF/inaccessible-temp warnings only
```

### Acceptance boundary and deferred work

This accepts Slice C only: source-grounded rejected-alternative reasoning,
unresolved work, assigned-versus-suggested owner handling, null owner/deadline
discipline, and the three distinct action-status states on the two named frozen
cases. It does not accept Slice D stale/current authority or uncertainty
semantics, Slice E planned/completed and unsupported-claim semantics,
completion of all Golden 8 cases, real-provider evaluation, raw
full-note/section reads, multiple-search evidence-ID redesign, or generic
memory.

## Worker Cycle 1 — Slice D: `ret-005 + iti-005`

- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `ACCEPT`
- This is the current handoff; the accepted Slice B and C evidence remains
  above as durable history.

### Goal and frozen inputs

Prove existing Team Decision production composition can use bounded,
model-visible verified evidence to distinguish:

- `ret-005`: an earlier superseded draft from the current approved approach,
  current rollout incompleteness, and one blocked action whose status and
  owner/deadline evidence cross note boundaries; and
- `iti-005`: insufficient assignment evidence, suggestion versus assignment,
  null deadlines, explicit unassigned ownership, and calibrated uncertainty.

Tests copy only each real frozen synthetic workspace into a unique private
`.tmp` root.  They start the real path using public case fields only; full
frozen records are loaded after execution solely for assertions.  Original and
copied Markdown trees are hash-checked before and after every run.

### RED evidence and root cause

Initial `ret-005` RED reported the capitalized subject in “Release is blocked”
as the owner, with no deadline.  The actual visible evidence showed that the
blocker appears in release readiness while the named owner/date appear in a
separate security-status record.  The model policy now treats an explicit
`<person> owns` statement as ownership evidence and associates it to the
blocked action through shared non-status subject tokens; both refs are
published for the one action.

Initial `iti-005` RED selected only the suggestion/status note because a
generic “no assignment” match outranked the distinct current readiness record
that says “no named owner.”  The policy now prefers an explicit missing-owner
statement for the pending checksum item and independently reads the suggestion
record for the unassigned export-retention item.

These failures were source-derived controlled-model selection gaps, not
Runtime, evidence-context, budget, contract, or durability defects.  The
minimal change is a new frozen production-path characterization; Slice D makes
no production-file change.

### Implementation boundary

Production files changed for Slice D: **none**.

New test file:

- `tests/integration/test_m11_slice_d_vertical_slice.py`

The controlled model retains only public case input and opaque search refs.  It
derives all final text, nulls, action status, owner/date, uncertainty, and refs
from the exact `request.evidence_context`.  Static checks confirm that its
controller and Final generator contain no `expected_`, frozen case ID, target
person name, or target-answer literal; expected data is assertion-only.

### Production execution and grounding traces

`ret-005` follows:

```text
search_notes -> read_verified_note x4 -> Final
```

The real reads are exactly the semantic draft, final decision, cache-security
status, and release-readiness notes.  The Final has five bounded context
ToolResults and publishes a partial current decision, one superseded
alternative, and one blocked action with the observed owner/date and both
supporting refs.

`iti-005` follows:

```text
search_notes -> read_verified_note x2 -> Final
```

The reads are exactly the owner-status and integration-readiness notes.  The
Final has three bounded context ToolResults, publishes a null
`insufficient_evidence` decision with no action items, and records two
unresolved fields: pending unknown ownership/deadline and explicitly
unassigned ownership.

For both cases, every declared material ref is asserted to be a successful
verified ref visible in the Final request.  The serialized model requests
contain no `expected_` data, and synthesis uses context rather than the final
observation alone.

### No-hidden-answer regressions

- Removing every visible supersession value from `ret-005` makes the Final
  policy fail with `missing visible supersession evidence`.
- Removing every visible explicit-unassigned value from `iti-005` makes the
  Final policy fail with `missing visible explicitly unassigned ownership
  evidence`.

Both reductions are test-only projections after a successful real run; they do
not alter frozen notes or pass Gold into the controller.

### GREEN evidence and regressions

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_d_vertical_slice.py
# 4 passed in 3.46s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/eval/test_golden8_freeze.py
# 34 passed in 18.72s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 25.84s

python -m compileall -q src/linkloom tests
# pass; only the existing inaccessible test-temp-directory notice

git diff --check
# pass; only existing CRLF / inaccessible-temp-directory warnings
```

Slice D changes no production durability surface.  The accepted `aer-002`
cold-resume proof stays in the 34-test focused suite and covers reuse of the
durable final evidence context without tool or replacement-model replay.

### Safety and protected-path audit

- The frozen Eval Seed directory and `tests/eval/test_golden8_freeze.py` are
  absent from both status and name-only diff audits.
- Frozen-source and private-copy hashes pass for every Slice D run.
- No real Obsidian vault, provider call, credential access, network call,
  writeback, UI, MCP, vector/embedding, generic memory framework, commit, or
  push occurred.

### Deferred work

- Slice E: `drm-002 + aer-005` planned-versus-completed, pending approval
  versus rejection, insufficient evidence, and unsupported-claim control.
- Final full Golden 8 production evaluation, real-provider evaluation,
  multiple-search evidence-ID redesign, raw full-note/section reads, and
  generic memory remain unclaimed.

### Worker reflection

Slice D keeps the Runtime at provenance/persistence/publication boundaries and
requires model-side synthesis to resolve cross-note action identity, current
authority, and absence of assignment evidence.  The Worker makes no acceptance
decision and freezes this boundary for independent review.

## Reviewer Cycle 1 — Slice D: `ret-005 + iti-005`

### Verdict

`ACCEPT`

### Independent review evidence

- **Review-cycle safety:** `review_cycle` is `1`; no blocker was found, so no
  human escalation is required.
- **Frozen authenticity:** the real `ret-005` and `iti-005` records and their
  matching synthetic workspaces were independently read. Each test run copies
  the workspace under a private `.tmp` root and verifies original/copy hashes.
  The frozen Eval Seed directory and `tests/eval/test_golden8_freeze.py` have
  no working-tree diff. Expected fields are loaded after execution for
  assertions; model requests contain no `expected_` data.
- **Production authenticity:** both cases start at `RuntimeEngine` and exercise
  the established `RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
  compatibility host -> SingleAgentModelLoop -> ToolRuntime ->
  TeamDecisionResult -> durable result` route. The new Slice D characterization
  does not replace the vertical path with a direct parser or unit helper.
- **`ret-005` authority and action proof:** the actual durable trace is
  `search_notes -> read_verified_note x4 -> Final`. The Final visibly separates
  the superseded February 3 draft from the current approved hybrid target and
  confirms rollout has not crossed its release boundary. The blocked release
  action uses two model-visible references: the release blocker and the
  independent cache-security owner/date record, yielding `Yuan Li` and
  `2026-02-28` rather than treating “Release” as an owner.
- **`iti-005` assignment and uncertainty proof:** the actual durable trace is
  `search_notes -> read_verified_note x2 -> Final`. It yields a null
  `insufficient_evidence` decision, no action items, a pending unresolved item
  with `owner=null` / `deadline=null`, and a distinct explicitly unassigned
  ownership item. The only named person is retained as a suggestion in
  rationale, not promoted to an owner. Uncertainty explicitly names the unknown
  owner and deadline fields.
- **Evidence trust and ownership:** all result claim refs are checked against
  successful verified ToolResults visible in the durable Final request, rather
  than the whole ledger. Removing visible supersession evidence or explicitly
  unassigned evidence makes the controlled model fail instead of reconstructing
  the answer from hidden state. Semantic selection stays in the model test
  controller; the production Runtime performs only execution, bounded context,
  schema/provenance validation, and durability.
- **Durability and compatibility:** Slice D adds no production durability
  surface, so a duplicate cold-resume test is not required. The accepted
  bounded Final-context resume coverage remains in the focused suite. Existing
  Slice A/B/C, TeamDecision contract, Golden freeze, M0.3/M0.4, and ToolRuntime
  compatibility checks pass.

### Reviewer-executed checks

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_d_vertical_slice.py
# 4 passed in 3.11s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/eval/test_golden8_freeze.py
# 34 passed in 18.32s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 15.38s

python -m compileall -q src/linkloom tests
# exit 0; existing inaccessible test-temp-directory notice only

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both exit 0; existing CRLF/inaccessible-temp warnings only
```

### Acceptance boundary and deferred work

This accepts Slice D only: current-versus-superseded authority, current rollout
incompleteness, cross-note blocked-action ownership/deadline grounding,
proposed-versus-assigned distinction, null discipline, explicitly unassigned
ownership, and calibrated insufficient-evidence output for the two named frozen
cases. It does not accept Slice E planned-versus-completed or
pending-versus-rejected semantics, all-Golden-8 semantic completion,
real-provider evaluation, raw full-note/section reads, multiple-search
evidence-ID redesign, or generic memory.

## Worker Cycle 1 — Slice E: `drm-002 + aer-005`

- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `CHANGES_REQUIRED`

### Slice goal

Prove the existing Team Decision production composition can derive, from
model-visible verified evidence only:

- `drm-002`: the current approved split retention policy, a directly rejected
  alternative and independent review rationale, two completed preparation
  checkpoints, a blocked legal sign-off, a pending exception list, and the
  boundary that preparation is not authorized deletion; and
- `aer-005`: a separately tracked rollout scope with explicitly unassigned
  ownership, calibrated partial uncertainty, and no unsupported claim that the
  separate scope was completed or silently covered.

Each run copies the real frozen synthetic workspace into a unique private
`.tmp` root, accepts only public `user_question`, `workspace_id`,
`source_notes`, and `distractor_notes` in the controlled model, and verifies
both frozen source and copy hashes before and after execution.  Expected fields
are loaded only after execution for assertions.

### Frozen-case RED and root cause

The initial real production-path characterization intentionally made
`search_notes -> Final` without a verified read.  Both frozen cases failed:

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_e_vertical_slice.py
# 2 failed in 15.09s
# TEAM_DECISION_CONTRACT_ERROR
```

The failure was expected and local to the controlled model: discovery titles
and snippets alone did not provide a grounded material Final.  The root cause
was missing model-side selection of the required verified source reads, not a
Runtime policy failure or a frozen-fixture problem.

During GREEN iteration, three source-derived controller gaps were separately
reproduced and narrowed:

1. a general review selector did not use the observable review source to read
   the rejection rationale;
2. the result initially omitted the visible boundary that preparation is not
   actual deletion; and
3. a current status value initially displaced the formal approved decision,
   while a generic blocked-status value displaced the legal no-deadline record.

The minimal correction is limited to the new test controller.  It now selects
the formal approval over a merely current status, reads the review, migration,
and legal status records by observable source evidence, and emits only
source-derived claims and refs.  No production Runtime semantic rule was
added.

### Production execution and grounding traces

`drm-002` follows:

```text
search_notes -> read_verified_note x4 -> Final
```

The actual reads are exactly the review, formal decision, legal-status, and
migration-status records.  The Final has bounded model-visible ToolResults and
publishes a partial decision with two completed preparation actions, one
blocked legal action, one pending exception action, the rejected alternative,
and explicit no-deletion / null-owner / null-deadline evidence.

`aer-005` follows:

```text
search_notes -> read_verified_note x2 -> Final
```

The actual reads are exactly the rollout decision and owner-status records.
The Final publishes a partial decision, one `unassigned` action and unresolved
item, and uncertainty over completion, owner, and deadline.  It does not
publish any completed multilingual claim or judge-sample claim.

For both cases, every declared material ref is asserted to be a successful
verified ref visible in the durable Final request.  Serialized model requests
contain no `expected_` data; static controller checks also reject frozen case
IDs, target answer literals, and target person names.  The controller derives
Final text solely from `evidence_context`, not from a final observation or
hidden answer map.

### No-hidden-answer regressions

- Removing all visible completed-preparation evidence from `drm-002` makes the
  Final policy fail with `missing visible completed preparation evidence`.
- Removing all visible separate-scope evidence from `aer-005` makes the Final
  policy fail with `missing visible separate scope evidence`.

These are test-only projections after successful real runs; they do not alter
the frozen workspaces or pass Gold into the controlled model.

### GREEN evidence and regressions

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_e_vertical_slice.py
# 4 passed in 29.76s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_m11_slice_e_vertical_slice.py tests/eval/test_golden8_freeze.py
# 38 passed in 149.03s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 64.94s

python -m compileall -q src/linkloom tests
# pass; only the existing inaccessible test-temp-directory notice

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both pass; only existing CRLF / inaccessible-temp-directory warnings
```

Slice E changes no production durability surface.  The accepted `aer-002`
cold-resume proof remains in the focused suite and covers reuse of durable
bounded Final evidence context without tool or replacement-model replay.

### Safety and protected-path audit

- The frozen Eval Seed directory and `tests/eval/test_golden8_freeze.py` are
  absent from both status and name-only diff audits.
- Frozen-source and private-copy hashes pass for every Slice E run.
- No real Obsidian vault, provider call, credential access, network call,
  writeback, UI, MCP, vector/embedding, generic memory framework, commit, or
  push occurred.

### Deferred work

- Final all-Golden-8 production evaluation and its human-approved acceptance
  threshold remain unclaimed.
- Real-provider evaluation, multiple-search evidence-ID redesign, raw
  full-note/section reads, and generic memory remain deferred.

### Worker reflection

Slice E keeps semantic distinctions in the model-side controlled synthesis:
approval versus a status update, preparation versus completed deletion,
separate scope versus silent coverage, and direct source support versus an
unsupported claim.  The Runtime remains limited to tool execution, bounded
context, schema/provenance validation, and durability.  The Worker makes no
acceptance decision and freezes this boundary for independent review.

## Reviewer Cycle 1 — Slice E: `drm-002 + aer-005`

### Verdict

`CHANGES_REQUIRED`

### Independent review evidence

- **Review-cycle safety:** `review_cycle` is `1`; the automatic
  human-escalation threshold is not yet implicated.
- **Frozen authenticity otherwise holds:** the real `drm-002` and `aer-005`
  records and their matching synthetic workspaces were independently read.
  The vertical test copies each workspace under a private `.tmp` root and
  verifies source/copy hashes. The frozen Eval Seed and
  `tests/eval/test_golden8_freeze.py` have no working-tree diff.
- **Real production route is present:** the test starts at `RuntimeEngine` and
  reaches the existing `RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
  compatibility host -> SingleAgentModelLoop -> ToolRuntime ->
  TeamDecisionResult -> durable result` path. `retrieval_agent.py` continues
  to validate only durable Final-request-visible refs and schema/provenance; it
  is not the source of the semantic-selection defect below.
- **Scoped execution passes but is insufficient for acceptance:**
  `tests/integration/test_m11_slice_e_vertical_slice.py` independently reports
  `4 passed in 26.54s`. A separate `drm-002` run reports `1 passed, 3
  deselected in 9.95s` and produces the claimed four-read durable trace. Those
  passing runs demonstrate the exact path through the defective helper, not
  an authentic no-Gold synthesis.
- `python -m compileall -q src/linkloom tests` exits `0`; its only output is
  the existing inaccessible test-temp-directory notice. `git diff --check` and
  the protected frozen-path diff both exit `0`, with only existing
  CRLF/inaccessible-temp warnings.

### Blocking findings

#### E1 — P0: Slice E FakeModel retains a frozen target-answer literal outside its leak guard

- **File/function:**
  `tests/integration/test_m11_slice_e_vertical_slice.py`,
  `_select_review()` (lines 189-197) and `_select_rejected_alternative()`
  (lines 200-207), both reachable from `_planned_read_refs()` and
  `_migration_result()`.
- **Reproducible evidence:** the independently read `drm-002` Gold records
  `expected_rejected_alternatives[0].alternative` as `Uniform 90-day
  deletion`. The live controller contains the same target fragment twice:
  `"90-day" in _source_text(value, values)` and `"90-day" in
  _quote(value)`. In the real durable run those helpers select the review and
  rejected-alternative evidence before Final. The test's purported static guard
  constructs `controller_source` from only `inspect.getsource(_slice_e_model)`
  plus `inspect.getsource(_source_derived_result)` (lines 564-575), so it does
  not inspect either reachable helper and passes despite the literal.
- **Broken invariant:** Frozen Gold must be assertion/evaluation-only. A
  controlled model may derive a result from model-visible ToolResults, but may
  not retain a case-specific target answer fragment or relevance rule in a
  helper that controls reads or Final synthesis. The no-hidden-answer check
  must cover the complete reachable controller, not just dispatch wrappers.
- **Why this blocks this Slice:** Slice E is intended to prove source-grounded
  planned/completed and rejected/unsupported-claim distinctions. A fixed
  `90-day` target steers both the review read and rejected-alternative output
  toward this frozen case's Gold. The successful runtime trace therefore proves
  plumbing and provenance, but cannot prove authentic model-side synthesis
  without a hidden answer shortcut.
- **Minimal fix direction:** remove frozen target literals and case-specific
  relevance rules from every helper reachable by `_slice_e_model`; replace them
  with a general policy that selects observed explicit alternative/rejection
  and independent-rationale evidence from `request.evidence_context`, then
  derives output text/refs from those values. Expand the anti-leak regression
  to inspect all transitive controller helpers (or equivalent complete source
  coverage), and keep frozen expected values solely in post-run assertions.

### Acceptance boundary after this cycle

Not accepted. The review confirms frozen-source immutability, the real
production route, model-visible evidence plumbing, and a passing Slice E
vertical suite. It does not accept `drm-002 + aer-005` until E1 removes the
hidden target-answer shortcut and the corrected characterization is
independently re-reviewed. Full Golden 8 semantic evaluation, real-provider
evaluation, raw full-note/section reads, multiple-search evidence-ID redesign,
and generic memory remain deferred.

## Worker Cycle 2 — Slice E: `drm-002 + aer-005`

- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `ACCEPT`

### E1-only correction

This cycle addresses only Reviewer finding E1.  No production file, frozen
fixture, contract, runtime policy, or test outside the Slice E
characterization changed.

- `_select_review()` no longer carries a frozen target value.  It selects an
  observable review source only when that source itself contains a generic
  causal/risk signal (`would`, `because`, `cannot`, or `risk`).
- `_select_rejected_alternative()` now selects an observed explicit rejection
  through generic language (`rejected`, `not accepted`, or `declined`), then
  emits the exact source quote and ref.
- The partial policy output now joins two visible fragments from the same
  formal approved-decision source.  Its continuation picker prefers an
  observed numeric-duration fragment over a legal-boundary fragment, without
  retaining any target duration or target alternative literal.
- `_controller_source()` now enumerates every helper reachable from
  `_slice_e_model()`'s decision path: tool construction, verified-result and
  context projection, all selectors, action/result assembly, read planning,
  Final dispatch, and the controlled model closure.  The anti-leak assertion
  evaluates that complete source, not only the two dispatch wrappers.

Expected frozen fields remain post-run assertion data only.  The controlled
model continues to receive only public input and exact model-visible
`evidence_context`; it has no expected field, case-ID, target name, target
duration, or target alternative map.

### Corrected no-hidden-answer proof

- Removing all visible completed-preparation values still causes
  `missing visible completed preparation evidence`.
- Removing all visible separate-scope values still causes
  `missing visible separate scope evidence`.
- Removing all visible explicit-rejection values now causes
  `missing visible explicitly rejected alternative evidence`.

The last reduction directly verifies that the rejected-alternative result is
not reconstructed from a retained Gold target phrase.

### Corrected GREEN evidence and regressions

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_e_vertical_slice.py
# 4 passed in 28.67s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_m11_slice_e_vertical_slice.py tests/eval/test_golden8_freeze.py
# 38 passed in 146.29s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 66.33s

python -m compileall -q src/linkloom tests
# pass; only the existing inaccessible test-temp-directory notice

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both pass; only existing CRLF / inaccessible-temp-directory warnings
```

The real frozen paths remain unchanged, every run re-verifies private-copy
hashes, and no real vault, provider, credential, network call, writeback, UI,
MCP, vector/embedding, generic memory framework, commit, or push occurred.

The Worker requests independent Cycle 2 review of E1 only and freezes this
boundary pending the Reviewer verdict.

## Reviewer Cycle 2 — Slice E: `drm-002 + aer-005`

### Verdict

`ACCEPT`

### Independent review evidence

- **Review-cycle safety:** `review_cycle` is `2`; E1 is resolved, so no
  human-escalation threshold applies.
- **Frozen and production authenticity:** the real `drm-002` and `aer-005`
  records/workspaces were independently rechecked. Frozen paths and
  `tests/eval/test_golden8_freeze.py` have no working-tree diff; private-copy
  hash checks pass. Slice E still enters through `RuntimeEngine` and the
  established `RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
  compatibility host -> SingleAgentModelLoop -> ToolRuntime ->
  TeamDecisionResult -> durable result` composition. No Cycle 2 production,
  contract, runtime-policy, or durability-surface change was introduced.
- **E1 correction is authentic:** `_select_review()` now uses only an observed
  review source and generic causal/risk language; `_select_rejected_alternative()`
  uses only observed explicit-rejection language. The fixed `90-day` target
  fragment is absent from every model-reachable helper and appears only in the
  post-run assertion. `_controller_source()` enumerates the complete custom
  decision-path helper set, including the previously omitted selectors; its
  anti-leak audit passes. Removing all model-visible explicit-rejection values
  now fails with `missing visible explicitly rejected alternative evidence`.
- **Actual durable grounding:** the independent `drm-002` run follows
  `search_notes -> read_verified_note x4 -> Final`. Its Final visibly derives
  the 180/365 current policy from two formal-decision fragments, the rejected
  alternative and independent review rationale from separate visible refs, two
  completed preparation states, blocked/pending unresolved work, and the
  no-deletion / null-owner / null-deadline boundary. The `aer-005` trajectory
  remains the two-read, partial separate-scope result with unassigned ownership
  and completion/owner/deadline uncertainty.
- **Evidence trust and compatibility:** result refs remain a subset of
  successful verified ToolResults visible in the durable Final request; model
  requests contain no `expected_` field. The production Runtime continues to
  enforce execution, bounded context, schema/provenance, and durability rather
  than semantic entailment. Slice E adds no durability surface, so a duplicate
  cold-resume test is not required; the accepted Final-context resume coverage
  remains in the focused suite.

### Reviewer-executed checks

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_slice_e_vertical_slice.py
# 4 passed in 29.23s

python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_m11_verified_evidence_context.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_m11_slice_e_vertical_slice.py tests/eval/test_golden8_freeze.py
# 38 passed in 149.62s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 65.99s

python -m compileall -q src/linkloom tests
# exit 0; existing inaccessible test-temp-directory notice only

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both exit 0; existing CRLF/inaccessible-temp warnings only
```

### Acceptance boundary and deferred work

This accepts Slice E `drm-002 + aer-005`: source-grounded current-policy and
rejected-alternative synthesis, preparation-versus-authorized-deletion
distinction, completed/blocked/pending status handling, null discipline,
separate-scope/unassigned ownership, calibrated uncertainty, and the repaired
anti-Gold control. It does not accept an all-Golden-8 semantic completion,
real-provider evaluation, raw full-note/section reads, multiple-search
evidence-ID redesign, generic memory, or any real-vault mutation.

## Worker Cycle 1 — Final Gate: Golden 8 production evaluation

- `worker_status`: `AWAITING_REVIEW`
- `reviewer_status`: `ACCEPT`

### Evaluation scope and audit

This is a read-only final evaluation gate, not a new feature slice. It makes
no production, fixture, contract, policy, or provider change, so it does not
manufacture a RED merely to satisfy an implementation loop. The frozen manifest
identity test first verified the exact eight-case Git/LF dataset and manifest
order:

```text
mps-001 -> aer-002 -> drm-003 -> inc-004 -> ret-005 -> iti-005 -> drm-002 -> aer-005
```

Each primary execution enters the accepted real composition:

```text
RuntimeEngine -> RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
-> SingleAgentModelLoop -> ToolRuntime -> TeamDecisionResult -> durable result
```

Each uses a private copy of its real frozen synthetic workspace. Controllers
receive public case input plus observed verified ToolResults; expected fields
are assertion/evaluation-only. No production prompt or model request receives
`expected_*` or Gold data.

### Manifest-order primary production results

```text
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py::test_frozen_mps_001_production_path_persists_current_decision_from_real_workspace tests/integration/test_m11_aer_002_vertical_slice.py::test_aer_002_synthesizes_multiple_verified_notes_visible_to_the_final_model_turn tests/integration/test_m11_slice_c_vertical_slice.py::test_slice_c_frozen_cases_synthesize_rejection_and_open_actions_from_visible_evidence tests/integration/test_m11_slice_d_vertical_slice.py::test_slice_d_frozen_cases_preserve_current_authority_and_insufficient_assignment tests/integration/test_m11_slice_e_vertical_slice.py::test_slice_e_frozen_cases_preserve_preparation_and_partial_scope_boundaries
# 8 passed in 43.09s
```

The command expands parametrized cases in frozen manifest order and proves one
actual production-path result for every Golden 8 case.

### Metric report — execution evidence, not an invented threshold

| Required report | Current result | Boundary |
| --- | --- | --- |
| Task success | **8/8 primary cases passed** | Exact frozen primary paths only. |
| Decision accuracy | **8/8 case-specific decision semantics assertions passed** | No normalized cross-case scoring threshold exists. |
| Rejected-alternative accuracy | **3/3 cases with an expected rejected alternative passed** (`drm-003`, `ret-005`, `drm-002`) | Cases with empty expected alternatives are asserted as empty, not counted as automatic semantic success. |
| Unresolved-item recall | **Case-specific tuple/count assertions passed for every case that publishes unresolved work** | No separately approved aggregate-recall threshold. |
| Action-item accuracy | **Case-specific owner/deadline/status assertions passed for every case that publishes actions** | Explicit empty action cases remain separately asserted. |
| Evidence groundedness | **8/8 primary results assert declared refs are a subset of successful Final-visible verified ToolResults** | Provenance membership, not a Runtime semantic-entailment claim. |
| Unsupported claim rate | **No unsupported claim observed in targeted frozen assertions; normalized rate = UNAVAILABLE** | No approved scorer or threshold; this is not a global quality percentage. |
| Trajectory violations | **0 observed in the eight asserted primary paths** | Existing bounded trajectory assertions only; no new general evaluator was added. |

The absence of an approved Level-3 numerical threshold remains explicit. This
gate establishes that the frozen Golden 8 production characterizations run and
that their asserted evidence/semantic boundaries pass; it must not be
misrepresented as a human-thresholded, real-provider, broad-30-case, or
production-vault quality acceptance.

### Full accepted-slice regression evidence

```text
python -m pytest -q -p no:cacheprovider tests/eval/test_golden8_freeze.py
# 1 passed in 0.77s

python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_m11_slice_e_vertical_slice.py tests/eval/test_golden8_freeze.py
# 23 passed in 146.78s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 62.98s

python -m compileall -q src/linkloom tests
# pass; only the existing inaccessible test-temp-directory notice

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both pass; only existing CRLF / inaccessible-temp-directory warnings
```

The 23-test suite includes fabricated/invisible-ref failures, Final-context
durability/resume coverage, source-hash immutability, evidence-removal
anti-hidden-answer checks, and the Cycle 2 transitive-controller anti-Gold
guard. No real Obsidian vault, provider call, credential access, network call,
writeback, UI, MCP, vector/embedding, generic memory framework, commit, or
push occurred.

### Deferred / unavailable items

- Human-approved Level-3 aggregate metric thresholds remain unavailable.
- Real-provider evaluation, broad 30-case evaluation, raw full-note/section
  reads, multiple-search evidence-ID redesign, generic memory, and any
  real-vault mutation remain unclaimed.

The Worker requests independent Final Gate review of this exact evidence and
freezes the repository boundary pending verdict.

## Reviewer Cycle 1 — Final Gate: Golden 8 production evaluation

### Verdict

`ACCEPT`

### Independent review evidence

- **Review-cycle safety:** `review_cycle` is `1`; no blocker was found and no
  human-escalation threshold applies.
- **Frozen authenticity:** the manifest lists exactly the eight intended cases
  in canonical order. Independent collection confirms the requested primary
  command expands to the same eight IDs in that order. The Git/LF freeze test
  passes, and protected frozen paths have no working-tree diff. The primary
  characterizations continue to copy real synthetic workspaces privately and
  assert source immutability; expected fields stay in post-run evaluation.
- **Production authenticity:** each primary case exercises
  `RuntimeEngine -> RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
  compatibility host -> SingleAgentModelLoop -> ToolRuntime ->
  TeamDecisionResult -> durable result`. The eight primary tests are not a
  parser/helper substitute for that route. Existing anti-Gold, visible-Final
  evidence, and evidence-removal controls remain included in the accepted-slice
  regression suite.
- **What is proven:** all eight frozen primary production characterizations
  pass their defined decision/action/unresolved/evidence and trajectory
  assertions. The aggregate report is accurate only as execution evidence:
  8/8 primary paths passed, the three cases with expected rejected alternatives
  passed their case assertions, and no asserted trajectory violation occurred.
  Model-visible claim-reference membership is verified per primary case.
- **Metric discipline:** the Gate correctly reports unsupported-claim rate and
  normalized aggregate quality as unavailable where no approved scorer or
  Level-3 threshold exists. It does not misrepresent these fixture runs as a
  real-provider, broad-corpus, or human-thresholded evaluation.
- **Durability and compatibility:** this gate changes no production durability
  surface. Existing Final-context resume coverage remains in the accepted tests;
  M0.3/M0.4 and ToolRuntime compatibility are independently green.

### Reviewer-executed checks

```text
python -m pytest --collect-only -q tests/integration/test_m11_team_decision_vertical_slice.py::test_frozen_mps_001_production_path_persists_current_decision_from_real_workspace tests/integration/test_m11_aer_002_vertical_slice.py::test_aer_002_synthesizes_multiple_verified_notes_visible_to_the_final_model_turn tests/integration/test_m11_slice_c_vertical_slice.py::test_slice_c_frozen_cases_synthesize_rejection_and_open_actions_from_visible_evidence tests/integration/test_m11_slice_d_vertical_slice.py::test_slice_d_frozen_cases_preserve_current_authority_and_insufficient_assignment tests/integration/test_m11_slice_e_vertical_slice.py::test_slice_e_frozen_cases_preserve_preparation_and_partial_scope_boundaries
# 8 tests collected, in manifest order

python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py::test_frozen_mps_001_production_path_persists_current_decision_from_real_workspace tests/integration/test_m11_aer_002_vertical_slice.py::test_aer_002_synthesizes_multiple_verified_notes_visible_to_the_final_model_turn tests/integration/test_m11_slice_c_vertical_slice.py::test_slice_c_frozen_cases_synthesize_rejection_and_open_actions_from_visible_evidence tests/integration/test_m11_slice_d_vertical_slice.py::test_slice_d_frozen_cases_preserve_current_authority_and_insufficient_assignment tests/integration/test_m11_slice_e_vertical_slice.py::test_slice_e_frozen_cases_preserve_preparation_and_partial_scope_boundaries
# 8 passed in 6.05s

python -m pytest -q -p no:cacheprovider tests/eval/test_golden8_freeze.py
# 1 passed in 0.12s

python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_m11_aer_002_vertical_slice.py tests/integration/test_m11_slice_c_vertical_slice.py tests/integration/test_m11_slice_d_vertical_slice.py tests/integration/test_m11_slice_e_vertical_slice.py tests/eval/test_golden8_freeze.py
# 23 passed in 19.67s

python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py tests/integration/test_m04_production_e2e_resume.py tests/unit/test_tool_ledger_runtime.py tests/unit/test_tool_runtime.py
# 53 passed in 9.66s

python -m compileall -q src/linkloom tests
# exit 0; existing inaccessible test-temp-directory notice only

git diff --check
git diff --exit-code -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
# both exit 0; existing CRLF/inaccessible-temp warnings only
```

### Acceptance boundary and deferred work

This accepts the Final Gate only: the exact frozen Golden 8, synthetic,
controlled-model production-path execution report and its bounded metric
statements. It does not accept a real-provider result, broad 30-case quality,
an approved aggregate threshold, raw full-note/section retrieval,
multiple-search evidence-ID redesign, generic memory, or any real-vault
mutation.
