# P8.4 Task Record

Status: implementation complete; independent review result is PASS_WITH_FINDINGS.

## Completed work

- Added the approved SPEC and implementation plan.
- Added `ModelExecutionRecord` and durable model-loop lifecycle state.
- Added the confined, deterministic `ModelArtifactStore` with SHA-256 refs.
- Added ledger rehydration and a pure model-resume decision contract.
- Extended the provider-neutral fake single-agent loop with request, response,
  observation, termination, and safe resume boundaries.
- Added crash-window A-E, artifact integrity, checkpoint compatibility,
  duplicate-execution, and observation-driven regression coverage.
- Added a 128 KiB bound for inline tool-result envelopes in checkpoint state;
  oversized values must use an artifact reference instead of silent truncation.
- Bound durable response and observation artifacts to the runtime record identity
  (`run_id`, `turn_id`, `task_id`, `agent_id`, and sequence).
- Rejected cross-run model records, forbidden persisted result/error fields, and
  mismatched terminal ledger/tool-result identities during resume.
- Stopped the durable loop at both pending and terminal checkpoint uncertainty;
  neither boundary may produce a durable observation or continue to another
  model turn.

## Evidence

- Focused P8.4 tests: 28 passed.
- P8.1–P8.3 regression slice: 76 passed.
- Full test suite: 466 passed, 2 skipped, 8 failed, 7 errors.
- The 8 failures and 7 errors are pre-existing `relation_eval` fixture/path
  failures tied to the deleted `D:\webproject\vault-steward` workspace and
  missing `tests/eval/relation_gold.yaml`; no P8.4 test failed.
- `python -m compileall -q src/linkloom tests`: passed.
- `git diff --check`: passed.
- No real Vault was accessed or modified. No commit, push, PR, or GitHub write
  was performed.

## Learning points

1. A request/response artifact plus hash makes durable payload identity
   inspectable without putting large or sensitive payloads in checkpoints.
2. A rehydrated tool ledger is the recovery source of truth for terminal tool
   outcomes; ambiguous model outcomes must not be blindly replayed.

## Interview evidence

- A runnable fake-model durable loop demo with persisted request/response/
  observation artifacts.
- Regression tests for crash windows A-E, integrity failure, terminal resume,
  and observation-dependent next actions.
- A clear boundary statement: this is provider-neutral durability, not an
  exactly-once guarantee and not a real-provider integration.

## Review findings and fixes

- Fixed latest-record selection during resume so the highest durable sequence,
  rather than list position, controls recovery.
- Fixed artifact identity validation so a response or observation from another
  turn/context cannot be reused.
- Fixed pending-checkpoint and terminal-checkpoint uncertainty so the loop
  fails closed without claiming a durable observation.
- Fixed runtime-state run ownership and unsafe persisted tool envelope checks.

## Remaining issues

- Legacy `relation_eval` tests need a separately approved fixture/path repair.
- A real provider adapter still needs an explicit idempotency and ambiguous
  response policy before network calls are allowed.
- Oversized inline tool results are rejected at the current checkpoint boundary;
  artifact-backed large-observation transport is intentionally deferred.
- The resume contract deliberately stops on ambiguous provider outcomes and
  pending tool outcomes; it does not claim exactly-once side-effect execution.

## Candidate next task

Human review of the PASS_WITH_FINDINGS evidence, followed by a separately
approved SPEC for any provider or later-phase work. Do not implement P8.5 in
this task.
