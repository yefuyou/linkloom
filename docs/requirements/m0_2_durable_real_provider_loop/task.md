# M0.2 Durable Real-Provider Loop Completion Task Record

Status: **APPROVED FOR WORKER IMPLEMENTATION** within the frozen M0.2
production/test boundary.

## Governing SPEC

- [SPEC.md](SPEC.md)
- [implementation_plan.md](implementation_plan.md)
- Parent: `M0 — Production Agent Closeout`
- Primary Gate: Gate A — Real Model
- Supporting Gate: Gate B readiness only
- Predecessor: M0.1 `ACCEPTED` on 2026-08-30

A separate Worker is authorized after this documentation correction, but only
within the exact boundary below. Gate A and Gate B are not complete.

## Planner Snapshot — 2026-08-30

### Workspace

- Unique workspace: `D:\webproject\Linkloom`.
- Branch: `feature/p8-agent-runtime`.
- HEAD: `3000056 feat(runtime): build durable model-driven agent runtime`.
- Worktree: dirty with accepted M0.1 and unrelated concurrent trajectory work.
- Planner production/test edits: none.
- Provider/network/credential/real-Vault access: none.
- Real provider smoke: `NOT_RUN`.

### M0.1 acceptance transition

- Independent Reviewer: `PASS_WITH_FINDINGS`, no blockers.
- Empty-search blocking regression: resolved.
- Human coordinator: `M0.1 P8 Integration Repair — ACCEPTED`.
- Gate A/B: still incomplete.
- Remaining non-blocking M0.1 findings: deferred.

## Current Provider/Loop Reality

1. `ModelAdapter.decide()` is called in both the non-durable and durable
   branches of `SingleAgentModelLoop`.
2. `ModelProviderAdapter.complete()` is implemented by
   `GeminiProviderAdapter` and called only by offline provider tests; no runtime
   source calls it.
3. `ModelResponse` is not consumed or persisted by the loop.
4. Request artifacts already contain the full `ModelTurnRequest.to_dict()`,
   runtime identity, tool snapshot ref, and observation ref, with a recorded
   SHA-256.
5. Response artifacts currently contain only a normalized ModelAction, empty
   usage, and fake metadata.
6. `request_sent` is a durable invocation boundary only when an actual
   checkpoint callback successfully persists it. A request artifact or
   in-memory publish alone is insufficient; without that callback commitment,
   Provider invocation is forbidden.
7. `response_durable` is reusable without provider invocation, but the current
   loader can restore only ModelAction, not a full ModelResponse/error envelope.
8. `previous_tool_call` exists and is validated/mapped by the provider adapter,
   but neither loop request constructor populates it.
9. `tool_result_durable` can restore ToolResult, but currently does not carry
   the producing ToolCall into the next request.
10. P8.5 WP-4 and current code differ primarily in
    `runtime/model_loop.py`, additive durable record fields, and missing
    integration tests; no recovery or artifact-store redesign is evidenced.

## Planner Decisions

- [x] Reuse P8.5 WP-4 as the design basis.
- [x] Use one private compatibility seam, not a new adapter hierarchy.
- [x] Durable path prefers `complete()`; legacy fake `decide()` is wrapped.
- [x] Non-durable path remains legacy-only.
- [x] Persist full ModelResponse before action/error consumption.
- [x] Keep response artifact as payload source of truth.
- [x] Add only optional provider projections to ModelExecutionRecord.
- [x] Carry the exact durable runtime-bound ToolCall with ToolResult.
- [x] Reuse existing recovery decisions and ToolRuntime ownership.
- [x] Require an actual durable checkpoint callback and successful
      `request_durable` then `request_sent` persistence before `complete()`.
- [x] Fail closed with Provider call count zero when the callback is absent or
      fails before invocation.
- [x] Require artifact/ref/SHA/identity recovery for a full Provider
      `ModelResponse`; inline normalized action remains legacy-only.
- [x] Keep real smoke optional and separately approved.
- [x] Freeze RetrievalAgent migration to M0.3.

## Approved Exact Worker Boundary

Production:

- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`.

Tests:

- add `tests/integration/test_p85_durable_provider.py`;
- minimally modify `tests/unit/test_p84_artifacts.py`.

Evidence doc:

- update this `task.md` only.

All provider, artifact-store, recovery, ToolRuntime, RetrievalAgent,
Coordinator, RuntimeAgentAdapter, Memory, Evaluation, mutation, CLI, fixture,
Gold, and unrelated test files are inspection/regression only.

## Planned Worker Tasks

### W0 — RED first

- [ ] Record branch, HEAD, status, and pre-existing file diffs.
- [ ] Confirm human approval and P8.5 WP-3 review evidence.
- [ ] Add provider-only adapter RED.
- [ ] Add full-response durability RED.
- [ ] Add previous-tool-call RED.
- [ ] Add provider-error RED.
- [ ] Add durable-response reuse RED.
- [ ] Add no-durable-checkpoint-callback RED with Provider call count zero.
- [ ] Add failed `request_durable`/`request_sent` callback RED with Provider
      call count zero.
- [ ] Add full-response artifact/ref/SHA/identity recovery RED proving the
      inline action fallback is not canonical.
- [ ] Run and record exact expected failures before production edits.

### W1 — Durable record fields

- [ ] Add optional provider IDs, finish reason, and provider-error projection.
- [ ] Preserve old-record defaults and round-trip.
- [ ] Add unsafe-field/shape regressions.

### W2 — Provider response seam

- [ ] Accept durable `complete()` or legacy `decide()`.
- [ ] Reject provider-only adapter in non-durable mode before invocation.
- [ ] Require a real durable checkpoint callback before `complete()`.
- [ ] Enforce request artifact -> persisted `request_durable` -> persisted
      `request_sent` -> `complete()` ordering.
- [ ] Keep callback failures explicit and do not invoke the Provider.
- [ ] Write/load/verify full ModelResponse artifact.
- [ ] Do not recover a full Provider response from inline normalized action.
- [ ] Keep response durability before ToolRuntime/final/error.

### W3 — Observation relationship

- [ ] Carry runtime-bound previous ToolCall into the next request.
- [ ] Restore ToolCall plus ToolResult after durable resume.
- [ ] Fail closed on identity mismatch.
- [ ] Prove injected Gemini client function-response mapping offline.

### W4 — Provider errors

- [ ] Persist known and unknown-outcome normalized errors.
- [ ] Keep ToolRuntime untouched on provider error.
- [ ] Prove retryable hint causes no retry.
- [ ] Keep raw exception/traceback/secret data out of artifacts/state.

### W5 — Crash/resume

- [ ] No durable callback -> explicit failure and zero Provider calls.
- [ ] Failed `request_durable`/`request_sent` callback -> zero Provider calls.
- [ ] Request durable safe invocation.
- [ ] Request-sent ambiguity.
- [ ] Response-obtained ambiguity.
- [ ] Orphan response artifact fail-closed.
- [ ] Durable final/ToolCall/error reuse.
- [ ] Durable ToolResult next-turn correlation.
- [ ] Pending/terminal tool replay rules unchanged.

### W6 — Verification and handoff

- [ ] Focused M0.2 tests.
- [ ] P8.1–P8.5 regression.
- [ ] M0.1 deterministic regression.
- [ ] Compileall, diff check, status, and scope audit.
- [ ] Full-suite run/classification when environment-accessible.
- [ ] Record `real_provider_smoke_test = NOT_RUN` unless separately approved.
- [ ] Hand evidence to an independent Reviewer.
- [ ] Stop without M0.3, commit, push, or PR.

## Planned Test Commands

Focused:

```powershell
python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p85_durable_provider.py `
  tests/unit/test_p84_artifacts.py
```

The exact P8 and M0.1 regression commands are frozen in
[implementation_plan.md](implementation_plan.md).

Static:

```powershell
python -m compileall -q src/linkloom tests
git diff --check
git status --short
```

## Required Acceptance Evidence

- [ ] provider-only adapter reaches durable `complete()` exactly as planned;
- [ ] legacy FakeModel behavior remains green;
- [ ] no durable checkpoint callback -> explicit failure and Provider call
      count zero;
- [ ] callback failure before invocation -> explicit failure and Provider call
      count zero;
- [ ] request artifact, persisted `request_durable`, and persisted
      `request_sent` precede Provider invocation;
- [ ] full response durable before action/error consumption;
- [ ] full Provider response recovery requires artifact/ref/SHA/identity and
      does not use inline normalized action as its canonical source;
- [ ] ToolCall executes only through existing ToolRuntime/policy/ledger;
- [ ] next request contains matching previous ToolCall and ToolResult;
- [ ] fresh and resumed correlation both pass;
- [ ] final remains runtime-terminated;
- [ ] provider errors persist safely and never execute tools;
- [ ] usage/metadata/IDs/finish reason round-trip;
- [ ] durable response reuse has no duplicate provider invocation;
- [ ] ambiguous windows do not replay provider;
- [ ] pending/terminal tool recovery behavior remains unchanged;
- [ ] no RetrievalAgent, Memory, Eval, RAG, provider mapping, recovery,
      artifact-store, real Vault, credential, or network change;
- [ ] focused/P8/M0.1/static/full-suite results classified;
- [ ] independent Reviewer decision;
- [ ] human acceptance;
- [ ] explicit Gate A/Gate B status and M0.3 deferral.

## Governance Prerequisite

- P8.5 WP-3 independent review: `PASS_WITH_FINDINGS`, no blocker.
- Human coordinator: `WP-3 ACCEPTED — PASS_WITH_FINDINGS`.
- Both findings are frozen as M0.2 guards in the SPEC and plan.
- Human coordinator approves M0.2 Worker implementation only within the exact
  production/test boundary above.

## Gate And Claims Boundary

- M0.2 offline acceptance may complete the durable provider-integration
  portion of Gate A.
- Gate A remains incomplete while the separately approved real-provider smoke
  is `NOT_RUN`.
- Gate B remains incomplete because production RetrievalAgent is deterministic.
- M0.2 does not prove production scale, general retry, exactly-once behavior,
  multi-provider routing, or real-Vault safety.

## Deferred M0.3 Work

- production RetrievalAgent model-loop composition;
- retrieval prompt/system instruction contract;
- model-selected search/read/continue/final behavior;
- evidence-grounded final schema;
- deterministic fallback and provider-unavailable UX;
- cloud note-content disclosure;
- retrieval trajectory/outcome acceptance.

## Learning Points

1. `ModelResponse` records one provider attempt; `ModelAction` is only its
   proposed runtime action.
2. A durable response can be reused, while `request_sent` without a durable
   response is ambiguous and cannot be replayed blindly.

## Interview Evidence Planned

- complete offline provider -> ToolRuntime -> observation -> provider -> final
  trace;
- request/response artifacts and checkpoint transitions;
- provider and tool call-count evidence across crashes;
- previous-tool-call identity proof;
- provider-error/usage/metadata durability;
- explicit provider-proposes/runtime-executes ownership diagram;
- honest Gate A/Gate B limitations.

## Planner Learning Reflection

### Step

- Role: Planner.
- Feature: M0.2 Durable Real-Provider Loop Completion.
- Files created: `SPEC.md`, `implementation_plan.md`, `task.md`.

### What Changed

The downstream instruction “connect the provider” is now an exact durable
response-lifecycle task with a two-production-file boundary and crash-window
acceptance.

### What I Learned

The request, artifact, recovery, ToolRuntime, and provider contracts already
exist. The smallest missing state is a full durable ModelResponse projection
plus the existing ToolCall carried forward with its ToolResult.

### Evidence

- current source call-path inspection;
- P8.3/P8.4/P8.5 contract and regression inspection;
- M0.1 acceptance update;
- no production/test/network/credential/Vault/Git remote action.

### Next Step

A separate Worker executes the approved M0.2 RED-to-GREEN plan. A separate
Reviewer accepts or rejects the result. This documentation task does not start
implementation, and M0.3 remains deferred.

## Worker Implementation Evidence — 2026-08-30

Status: **IMPLEMENTED — READY FOR INDEPENDENT REVIEW**. This is a Worker
handoff, not acceptance of M0.2.

### Boundary And Initial State

- Branch: `feature/p8-agent-runtime`.
- HEAD before edits: `3000056 feat(runtime): build durable model-driven agent runtime`.
- The worktree was already dirty with M0.1, agent-trajectory, Agent production,
  integration-test, and planning-document changes. They were preserved.
- The approved production files and `tests/unit/test_p84_artifacts.py` had no
  pre-edit diff. `tests/integration/test_p85_durable_provider.py` did not exist.
- No reset, restore, checkout, stash, clean, commit, push, PR, network,
  credential discovery, real Provider call, or real Vault access occurred.

### RED Evidence

Before production implementation:

```text
tests/integration/test_p85_durable_provider.py
tests/unit/test_p84_artifacts.py
19 failed, 8 passed
```

Failures covered the absent `complete()` seam, incomplete response durability,
missing `previous_tool_call`, checkpoint guards, provider-error handling,
provider-response recovery, and additive record fields. A later bounded-error
regression was also observed RED (`1 failed`: expected ValidationError was not
raised) before reusing the existing 128 KiB inline-checkpoint size guard.

### Exact Worker Files

Production:

- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/runtime/models.py`.

Tests:

- `tests/integration/test_p85_durable_provider.py`;
- `tests/unit/test_p84_artifacts.py`.

Evidence:

- `docs/requirements/m0_2_durable_real_provider_loop/task.md`.

No other production or test file was modified by this Worker.

### Implemented Contract

1. `SingleAgentModelLoop` accepts either `complete()` or legacy `decide()`.
   Durable mode prefers `complete()` when both exist; non-durable mode remains
   `decide()`-only.
2. A real provider call requires a callable durable checkpoint sink and this
   exact successful order:

   ```text
   request artifact
   -> request_durable checkpoint
   -> request_sent checkpoint
   -> provider.complete()
   ```

   The provider call count remains zero when the callback is absent or either
   pre-call checkpoint fails.
3. The complete normalized `ModelResponse` is written with SHA-256 and runtime
   identity before its action/error is consumed. Record projections retain
   normalized action/error, usage, safe metadata, provider request/response
   IDs, and finish reason.
4. Provider recovery requires response ref, SHA-256, artifact identity, full
   `ModelResponse`, and exact record/artifact projection agreement. Legacy
   inline action fallback is used only by the legacy `decide()` path.
5. The runtime-bound ToolCall is retained with its ToolResult and becomes
   `previous_tool_call` on the next request, including resume from
   `tool_result_durable`.
6. Provider errors become safe provider-category `ToolError` results only
   after the response is durable. Retryability is retained as data but causes
   no retry and never enters ToolRuntime.
7. `ModelExecutionRecord` gained backward-compatible optional provider
   projection fields. Structured forbidden keys, invalid text/shape, non-JSON
   data, and oversized inline provider errors fail closed.

### Crash And Ownership Evidence

- Missing durable callback: provider calls `0`.
- Failed `request_durable`: provider calls `0`.
- Failed `request_sent`: provider calls `0`.
- `request_sent` / `response_obtained` resume: provider calls `0`, verification
  required.
- `response_durable` final/error reuse: provider calls `0`.
- Missing ref/SHA, identity mismatch, or projection mismatch: provider calls
  `0`, recovery fails closed.
- Response artifact written but `response_durable` checkpoint failed: orphan
  artifact is not inferred; durable state remains `response_obtained`.
- Fresh synthetic chain: provider calls `2`, ToolRuntime executor calls `1`,
  policy count `1`, then runtime-owned final termination.
- Resumed terminal ToolResult: executor calls `0`; exactly one new provider
  turn receives the matching ToolCall/ToolResult pair.
- Existing injected `GeminiProviderAdapter` receives native function-call and
  function-response content; the client receives no RuntimeState, ledger,
  policy, or ToolRuntime object.

### Verification Results

Focused M0.2:

```text
41 passed in 0.74s
```

P8.1–P8.5 frozen regression slice:

```text
193 passed in 1.50s
```

Synthetic provider/Gemini loop smoke:

```text
2 passed in 0.17s
```

M0.1 deterministic regression:

- combined command: `11 passed, 5 errors`;
- all five errors were setup-time Windows Temp ACL `PermissionError` failures;
- a fresh workspace-local `--basetemp` reproduced the same host ACL failure;
- the explicit empty-search Criterion 9 regression passed independently:
  `1 passed in 0.10s`.

Static checks:

- `python -m compileall -q src/linkloom tests`: exit `0`; it reported that the
  pre-existing protected `tests/.pytest-trajectory-2b-data` directory could
  not be listed;
- `git diff --check`: exit `0` (line-ending warnings only);
- forbidden-surface search: production matches were only the existing
  structured forbidden-key list and normalized `raw_response` local variable;
  test matches were intentional redaction fixtures.

Full suite, run once as required:

```text
417 passed, 35 failed, 148 errors, 3 warnings in 10.88s
```

Classification:

- `M0_2_CAUSED`: none evidenced; focused and frozen P8 regressions are green;
- `ENVIRONMENT_SETUP`: Windows Temp/workspace pytest ACL failures, including
  tmp fixtures, Memory temporary files, and pytest cache;
- `PRE_EXISTING_UNRELATED`: legacy relation/evaluation fixture/path failures;
- `UNKNOWN`: none within the approved M0.2 boundary.

### Safety And Gate Status

- `real_provider_smoke_test = NOT_RUN`.
- All fixtures and artifacts are synthetic and workspace-local.
- No provider SDK type, Provider execution authority, retry loop, new ledger,
  new policy, or ToolRuntime implementation was added to runtime code.
- Gate A remains incomplete until a separately approved credentialed real
  provider smoke exists.
- Gate B remains incomplete because production RetrievalAgent is still the
  deterministic Python workflow.
- M0.3 RetrievalAgent migration, Memory/context assembly, Eval expansion,
  retries, streaming, and real-Vault work remain deferred.

### Reviewer Handoff

The implementation and objective evidence are ready for an independent
Reviewer. The Worker does not declare M0.2 accepted.

### Blocker Correction Worker Evidence — 2026-08-30

The independent Reviewer reported exactly two blocking defects. This worker
implemented only those corrections in `model_loop.py` and the M0.2 provider
regression tests.

#### RED evidence before the production correction

The new blocker tests were run before the production patch:

```text
3 failed, 3 passed, 25 deselected
```

The failures demonstrated that a provider-origin response record could fall
through to the legacy inline action recovery when resumed with a
`FakeModelAdapter`, and that a stale provider ToolCall sequence could reach
the tool execution path.

#### Implemented correction

1. Provider-origin recovery is determined from durable record/artifact
   provenance, not from the currently injected adapter. A provider-origin
   record requires a verifiable response artifact and a complete durable
   `ModelResponse`; missing ref/SHA, hash failure, identity failure, or a
   missing full envelope fails closed without invoking the provider. Legacy
   P8.4 fake records marked with the legacy adapter metadata continue to use
   the compatibility fallback.
2. A provider ToolCall whose durable run/task/agent identity matches the
   current turn must also have the current turn sequence. A mismatch fails
   before `ToolRuntime` or the tool ledger is reached. Matching calls and
   resumed terminal ToolResults retain the existing behavior.
3. Existing `MODEL_LOOP_RUNTIME_FAILED` compatibility is preserved for
   durable response recovery failures; the safe error message/details now
   identify the artifact-verification reason without exposing internal
   exception data.

#### Verification

- M0.2 focused provider/artifact tests: `46 passed`.
- P8.1–P8.5 frozen regression slice: `198 passed`.
- Synthetic provider/Gemini loop smoke: `2 passed`.
- M0.1 empty-search regression: `1 passed`.
- M0.1 deterministic integration command: `11 passed, 5 errors`; all five
  errors were setup-time Windows Temp ACL failures, with no application
  assertion failure from those tests.
- `python -m compileall -q src/linkloom tests`: exit `0`; the existing
  protected trajectory fixture directory could not be listed.
- `git diff --check`: exit `0`; Git emitted only existing line-ending and
  protected-directory warnings.
- No real provider, credential, API, or real Vault was accessed.

This worker does not declare M0.2 accepted. The implementation and evidence
are ready for the independent Reviewer to re-review the two blocking fixes.

### Second Re-review Provenance Correction — 2026-08-30

The second independent Reviewer resolved the stale ToolCall sequence finding
and reported one remaining blocker: durable response provenance was still
inferred from optional Provider projections. This worker changed only the
approved M0.2 model-loop boundary and its regression tests.

#### RED evidence

The new explicit-origin tests were run before the production correction:

```text
10 failed, 1 passed, 31 deselected
```

The failures covered the missing `response_origin` contract and Provider
responses whose IDs, finish reason, error, and metadata were all empty.

#### Implemented correction

1. `ModelExecutionRecord.response_origin` is an optional,
   backward-compatible field with only `provider` and `legacy` values. New
   durable records set it at the model boundary: `complete()` writes
   `provider`; `decide()` writes `legacy`.
2. Provider recovery uses the persisted origin, then requires response ref,
   SHA-256, identity, full `ModelResponse`, and projection consistency. It
   never selects the canonical path from the currently injected adapter or
   optional Provider fields.
3. A pre-origin record is treated as legacy only when it matches the bounded
   old P8.4 artifact shape. An originless ambiguous record fails closed rather
   than using inline `normalized_action` fallback.
4. The existing stale-sequence correction remains unchanged and continues to
   reject before ToolRuntime.

#### Verification

- Provenance blocker-specific tests: `12 passed`.
- M0.2 focused provider/artifact tests: `58 passed`.
- P8.1–P8.5 frozen regression slice: `210 passed`.
- Explicit Provider smoke plus stale/matching/legacy regressions: `5 passed`.
- M0.1 empty-search regression: `1 passed`.
- M0.1 deterministic integration command: `11 passed, 5 errors`; the five
  errors remain setup-time Windows Temp ACL failures.
- `python -m compileall -q src/linkloom tests`: exit `0`; the existing
  protected trajectory fixture directory could not be listed.
- `git diff --check`: exit `0`; only line-ending and protected-directory
  warnings were emitted.
- No real Provider, API credential, network, or real Vault was accessed.

This remains Worker evidence only. M0.2 is not declared accepted and is ready
for independent re-review of the explicit durable provenance correction.

### Final Provenance Tightening Worker Evidence — 2026-08-30

The latest independent Reviewer left one blocking finding: an originless
artifact with non-legacy Provider or unknown metadata could still be accepted
by the P8.4 compatibility fallback. The stale ToolCall sequence finding was
already resolved and was not changed in this correction.

#### RED evidence

Two regression tests were added before changing production code. They failed
because the originless artifact was incorrectly recovered as a completed
legacy response:

```text
2 failed, 2 passed, 42 deselected
```

The two passing tests were the explicit legacy-origin recovery and the
existing genuine legacy recovery compatibility test.

#### Implemented correction

`is_legacy_p84_record()` now accepts an artifact-backed originless record only
when all of the following are true:

- the record has no Provider-only projection fields and its metadata remains
  one of the pre-origin-compatible values;
- the artifact has exactly the historical P8.4 fields:
  `runtime_identity`, `normalized_action`, `usage`, and `provider_metadata`;
- the artifact has no `model_response` envelope;
- artifact metadata is exactly `{}` or `{"adapter": "fake"}`;
- normalized action and usage have the historical dictionary shape.

Therefore Provider-shaped metadata such as `{"provider": "gemini", ...}` and
unknown metadata fail closed when `response_origin` is absent. Explicit
`provider` records still require the full verifiable response artifact and
envelope; explicit `legacy` records and genuine historical P8.4 artifacts
remain compatible.

#### Verification

- New provenance regression plus legacy compatibility: `4 passed`.
- Full M0.2 durable-provider file: `46 passed`.
- P8.1–P8.5/WP3 focused regression slice: `213 passed`.
- Provider smoke, stale-sequence, matching-sequence, and legacy recovery:
  `5 passed`.
- M0.1 successful empty-search Criterion 9 regression: `1 passed`.
- M0.1 search/read failure and stale-hash slice: `2 passed, 1 error`; the
  error occurred before the stale-hash test at fixture setup because the host
  Windows Temp directory returned `PermissionError [WinError 5]`. The search
  and read failure assertions passed.
- Full `tests/integration/test_retrieval_tool_runtime.py`: `5 passed, 3
  errors`; all three errors were the same host Windows Temp ACL failure during
  `tmp_path` setup, including the stale-hash test.
- `python -m compileall -q src/linkloom tests`: exit `0`; the existing
  protected `tests/.pytest-trajectory-2b-data` directory could not be listed.
- `git diff --check`: exit `0`; only existing line-ending/protected-directory
  warnings were emitted.
- No real Provider, credential, network, or real Vault was accessed.

Criterion 8 has passing Worker evidence for response/artifact projection and
runtime-identity checks, including the new provenance cases, but acceptance
remains the independent Reviewer's responsibility.

This is Worker evidence only. M0.2 is not declared accepted; the change is
ready for the final independent Reviewer re-review of the remaining
originless-provenance blocker.

### Historical P8.4 Marker Correction Worker Evidence — 2026-08-30

The latest independent Reviewer identified the final compatibility defect:
the originless legacy whitelist did not include the marker emitted by the
committed pre-M0.2 P8.4 production writer.

#### HEAD evidence inspected

- `HEAD` is `3000056`.
- `src/linkloom/runtime/model_loop.py` writes the historical response artifact
  with exactly four fields: `runtime_identity`, `normalized_action`, `usage`,
  and `provider_metadata`; it has no `model_response`.
- The exact production artifact metadata marker is
  `{"adapter": "provider_neutral_fake"}`.
- `{"adapter": "fake"}` has no production-writer evidence in HEAD; it was
  only present in a test fixture and was corrected.
- `{}` has no production artifact-marker evidence in HEAD. The old production
  record itself defaults its unprojected `provider_metadata` field to `{}`;
  this is not treated as an artifact marker.

#### RED evidence

After adding the exact historical-marker recovery and unproven-marker
regressions, before the production whitelist correction:

```text
4 failed, 45 deselected
```

The real `provider_neutral_fake` artifact was rejected, while `fake` and `{}`
were incorrectly accepted as legacy.

#### Implemented correction

1. Artifact-backed originless fallback now requires the exact historical P8.4
   field set and `provider_metadata == {"adapter": "provider_neutral_fake"}`.
2. Originless records without a response artifact are no longer classified as
   legacy from inline action data alone.
3. The successful P8.4 compatibility fixtures, including explicit legacy
   recovery, were corrected to use the production marker. Remaining `{}` and
   `fake` occurrences are failure-only fixtures and do not authorize
   production legacy fallback.
4. Explicit `response_origin="provider"` and
   `response_origin="legacy"` semantics remain unchanged.

#### Verification

- Historical marker, adversarial Provider/unknown metadata, explicit legacy,
  and explicit Provider recovery regressions: `10 passed`.
- Full M0.2 durable-provider file: `49 passed`.
- P8.1–P8.5/WP3 focused regression slice: `216 passed`.
- Synthetic Provider/Gemini smoke, stale sequence, matching sequence, and
  historical recovery: `5 passed`.
- M0.1 empty-search regression: `1 passed`.
- Full M0.1 retrieval integration file previously observed: `5 passed, 3
  errors`; all errors were host Windows Temp ACL failures during `tmp_path`
  setup, not application assertions.
- `python -m compileall -q src/linkloom tests`: exit `0`; the existing
  protected trajectory fixture directory could not be listed.
- `git diff --check`: exit `0`; only existing line-ending/protected-directory
  warnings were emitted.
- No real Provider, credential, network, or real Vault was accessed.

Criterion 8 has passing Worker evidence for response/artifact projection,
identity, exact historical-marker, and fail-closed checks. Acceptance remains
the independent Reviewer's responsibility.

This remains Worker evidence only. M0.2 is not declared accepted and is ready
for final independent re-review.

### Legacy Artifact Integrity And Usage Correction Worker Evidence — 2026-08-30

The latest independent Reviewer identified two remaining recovery defects in
the bounded historical P8.4 compatibility path: artifact-backed legacy records
could be read without a checkpointed response SHA-256, and malformed artifact
usage could be hidden by loading only the record projection.

#### RED evidence

Before the production correction, the three blocker-specific behavior tests
failed as expected:

```text
3 failed in 3.48s
```

Both the originless historical record and explicit `response_origin="legacy"`
record incorrectly completed when `response_sha256` was null. A historical
artifact with `usage={"bogus": "not-a-model-usage-field"}` also incorrectly
completed without invoking `ModelUsage.from_dict()` on the artifact value.

#### Implemented correction

1. Explicit legacy recovery now requires both non-empty `response_ref` and a
   checkpointed `response_sha256` before reading its artifact.
2. An artifact-backed originless historical record with an incomplete ref/hash
   pair fails closed before `ModelArtifactStore.read()` can skip integrity
   verification.
3. Accepted legacy artifact usage and the durable record projection are both
   normalized through the existing `ModelUsage.from_dict()` contract and must
   match before recovery returns a response.
4. The recovered legacy `ModelResponse` uses the validated artifact usage.
5. No ArtifactStore, Recovery, Provider, ToolRuntime, RetrievalAgent, Memory,
   Evaluation, credential, network, or Vault boundary changed.

#### Regression coverage

- originless historical artifact with missing SHA fails closed;
- explicit legacy artifact with missing SHA fails closed;
- malformed historical artifact usage fails closed;
- genuine historical `usage={}` remains recoverable without model replay;
- valid non-empty canonical `ModelUsage` remains recoverable;
- artifact usage must match the record projection;
- explicit legacy compatibility remains recoverable;
- every recovery case asserts FakeModel invocation count remains zero.

#### Verification

- Blocker and compatibility slice: `7 passed`.
- Full M0.2 durable-provider file: `54 passed`.
- Approved focused M0.2 provider/artifact slice: `69 passed`.
- P8.4 durable-loop regression: `16 passed`.
- P8.1–P8.5/WP3 focused regression slice: `221 passed`.
- Synthetic Provider/Gemini, stale/matching sequence, and historical recovery
  smoke: `5 passed`.
- M0.1 successful empty-search regression: `1 passed`.
- `python -m compileall -q src/linkloom tests`: exit `0`; only the existing
  protected `tests/.pytest-trajectory-2b-data` directory could not be listed.
- `git diff --check`: exit `0`; Git emitted line-ending warnings only.
- No real Provider, credential, network, API, or real Vault was accessed.

#### Learning and interview evidence

- Integrity lesson: a durable artifact file is not recoverable authority unless
  the checkpoint owns both its reference and content hash.
- Contract lesson: checking only that provider usage is an object is weaker
  than canonical deserialization through the existing provider-neutral model.
- Interview evidence: the RED-to-GREEN tests demonstrate fail-closed historical
  compatibility without model replay or relaxation of the provider path.

Criterion 8 now has Worker evidence that legacy response ref/hash, runtime
identity, canonical usage, and record projection are validated together.
Acceptance remains the independent Reviewer's responsibility. This Worker does
not declare M0.2 accepted and recommends one ultra-focused independent
re-review of these two findings only.

### Terminal ToolResult Canonical-Validation Correction — 2026-08-30

The ultra-focused Reviewer found that `resume_from_tool_result` bypassed the
canonical durable-response loader. The branch reconstructed a ToolCall from
`ModelExecutionRecord.normalized_action` and restored the terminal observation
without first validating the response artifact that authorized that action.

#### RED evidence

Before the production correction, the A–F terminal-ledger slice produced:

```text
4 failed, 2 passed in 4.62s
```

The originless missing-SHA, explicit-legacy missing-SHA, malformed artifact
usage, and artifact/record usage-mismatch cases all incorrectly continued to a
new model turn and completed. The valid historical and valid Provider terminal
resume cases remained green.

#### Implemented correction

1. `resume_from_tool_result` now calls the existing canonical
   `load_response(record)` before loading an observation or continuing the
   model loop.
2. The branch accepts only a ToolCall from the validated
   `ModelResponse.action`; raw `record.normalized_action` is no longer its
   ToolCall recovery authority.
3. `load_observation()` receives that validated ToolCall and performs the
   existing observation/ledger correlation against it.
4. The old `record_tool_call()` reconstruction helper was removed, so future
   response-integrity rules added to `load_response()` automatically cover the
   terminal-result resume path.
5. Terminal ToolResult semantics remain unchanged: the response artifact may
   be reread, but neither the first model response nor ToolRuntime executor is
   replayed.
6. `src/linkloom/runtime/recovery.py` was not modified. Its call-id inspection
   remains a non-authoritative planning hint; consumption of the response-derived
   ToolCall occurs only after canonical validation in the model loop.

#### P8.4 fixture calibration

Two accepted P8.4 terminal-result tests contained response fixtures that the
new invariant correctly rejected. One named a nonexistent response artifact
with a fabricated SHA; the other still used empty artifact metadata. They were
updated to write the actual historical four-field response artifact with
`provider_metadata={"adapter": "provider_neutral_fake"}` and its real SHA-256.
No P8.4 production or Recovery behavior changed.

#### Regression coverage

- originless historical + terminal result + missing SHA fails closed;
- explicit legacy + terminal result + missing SHA fails closed;
- malformed historical artifact usage + terminal result fails closed;
- valid but mismatched artifact/record usage + terminal result fails closed;
- valid historical terminal result restores exact previous ToolCall and
  observation, invokes only the next model turn, and does not execute the tool;
- valid full Provider response follows the same validation path, reuses its
  terminal result, and does not duplicate the first Provider call or executor;
- direct non-terminal B1/B2 regressions remain green.

#### Verification

- Terminal-ledger A–F slice: `6 passed`.
- Direct B1/B2 and historical compatibility slice: `7 passed`.
- Full M0.2 durable-provider file: `59 passed`.
- Approved focused M0.2 provider/artifact slice: `74 passed`.
- P8.4 durable-loop suite: `16 passed`.
- P8.1–P8.5/WP3 focused regression: `226 passed`.
- Provider/Gemini, stale/matching sequence, historical recovery, and both
  terminal-resume smoke paths: `7 passed`.
- M0.1 successful empty-search regression: `1 passed`.
- `python -m compileall -q src/linkloom tests`: exit `0`; only the existing
  protected `tests/.pytest-trajectory-2b-data` directory could not be listed.
- `git diff --check`: exit `0`; line-ending warnings only.
- No network, credential, real Provider, real Vault, commit, push, or PR was
  used.

#### Learning and interview evidence

- A terminal ToolResult proves the executor does not need replay; it does not
  independently prove the integrity of the model response that proposed it.
- Converging all response-derived action consumption on one canonical loader
  prevents future integrity checks from being omitted by one recovery branch.
- The RED-to-GREEN terminal variants demonstrate this distinction with both
  zero executor calls and zero model continuation for corrupted responses.

Criterion 8 now has path-complete Worker evidence: both direct response reuse
and terminal ToolResult resume validate response ref/SHA, runtime identity,
provenance, canonical usage, action, and projection through the same authority.
This is Worker evidence only. M0.2 is not declared accepted and is ready for a
FINAL independent acceptance re-review of this invariant.
