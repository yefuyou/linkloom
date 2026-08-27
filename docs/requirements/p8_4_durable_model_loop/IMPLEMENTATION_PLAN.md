# Implementation Plan: P8.4 Durable Model Loop

Status: Approved for implementation by the P8.4 execution brief on 2026-08-24.

## Design decisions

1. Put runtime-owned state models in `src/linkloom/runtime/models.py` so the
   existing checkpointers and JSON validation remain the only state boundary.
2. Keep artifact I/O in a new small `src/linkloom/runtime/artifacts.py`; it
   accepts only safe dictionaries and returns relative refs plus SHA-256.
3. Reuse `ToolExecutionLedger(records=...)` rather than creating a second
   ledger or registry. A runtime factory/rehydration helper will make the
   source-of-truth direction explicit.
4. Keep recovery decisions pure in a new `src/linkloom/runtime/recovery.py`.
   Existing pending-tool recovery remains the authority for pending tools.
5. Extend `SingleAgentModelLoop` additively. Existing P8.3 callers continue to
   work without an artifact store; durable behavior is enabled by an explicit
   store/checkpoint argument.
6. `RuntimeState.status` remains the enclosing run status. Model-loop result
   completion is represented by `TerminationState` and `ModelLoopResult`.

## Ordered work packages

### WP-1: Durable models and artifacts

Files: `runtime/models.py`, `runtime/__init__.py`, new `runtime/artifacts.py`,
new unit tests.

Acceptance:

- `ModelExecutionRecord` has all approved identity, lifecycle, refs, action,
  usage and metadata fields with JSON-safe validation and old-field defaults.
- `RuntimeState.model_executions` round-trips and defaults to an empty list.
- Artifact writes are deterministic, relative-root confined and hash verified.
- Hidden reasoning, absolute paths, traversal paths, NaN/infinity and unsafe
  values are rejected.

Checkpoint: focused contract/artifact tests, compileall.

### WP-2: Ledger rehydration and recovery contract

Files: `tools/ledger.py` (only additive helper if needed), new
`runtime/recovery.py`, new unit tests.

Acceptance:

- `ToolExecutionLedger.from_list` or equivalent restores all terminal and
  pending records and rejects duplicates without mutation.
- A runtime created from checkpoint state receives the restored ledger.
- Recovery decisions are pure and include A-E plus terminal outcomes.
- Trace data is not read to rebuild the ledger.

Checkpoint: restored-ledger and decision tests.

### WP-3: Durable model lifecycle

Files: `agents/model_adapter.py` only if safe normalization is needed,
`runtime/model_loop.py`, new/updated state tests.

Acceptance:

- A turn and request record are persisted before adapter invocation.
- Response artifact and normalized action are persisted before tool execution.
- Tool results are checkpointed and supplied as the next request observation.
- Final action produces runtime-owned `TerminationState`.
- Existing non-durable P8.3 loop tests retain their behavior.

Checkpoint: focused fake-loop and lifecycle tests.

### WP-4: Resume and crash-window regression

Files: new `tests/unit/test_p84_durable_model_loop.py`, new
`tests/unit/test_p84_artifacts.py`, new `tests/unit/test_p84_recovery.py`.

Acceptance:

- A-E, terminal, integrity, compatibility, observation-driven, max-step and
  max-tool-call behavior are covered.
- Restored durable response does not call the adapter again.
- Restored completed tool result does not call the executor again.
- Ambiguous model outcome is never auto-reinvoked.

Checkpoint: all focused P8.4 tests pass.

### WP-5: Repository verification

Run:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q tests
python -m compileall -q src/linkloom tests
git diff --check
git status --short
```

Classify unrelated legacy fixture/permission failures without changing their
files. No commit or remote write is permitted.

## File boundary

Allowed P8.4 production changes are limited to:

- `src/linkloom/runtime/models.py`
- `src/linkloom/runtime/__init__.py`
- `src/linkloom/runtime/artifacts.py`
- `src/linkloom/runtime/recovery.py`
- `src/linkloom/tools/ledger.py` (only rehydration API if required)
- `src/linkloom/runtime/model_loop.py`

Allowed tests are new P8.4 unit tests plus minimal edits to existing P8.3 tests
only when the durable contract changes an assertion that was previously
underspecified. No Coordinator, RetrievalAgent, Memory, Evaluation, provider,
CLI, LangGraph, vault or writeback modules are in scope.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Model payload leaks sensitive text | Store refs and safe normalized fields; reject hidden reasoning and secrets by contract. |
| Response is lost after provider call | Mark ambiguous window as verification/manual decision; never blind retry. |
| Restored ledger starts empty | Construct from `RuntimeState.tool_ledger` and test terminal/pending records. |
| P8.3 behavior regresses | Preserve default no-store path and run focused plus full tests. |
| Runtime and loop statuses diverge | Keep `RuntimeState.status` as enclosing run authority; test `TerminationState` separately. |
| Duplicate side effect | Reuse terminal ledger records and explicitly refuse automatic pending replay. |

## Completion evidence

The final report must include exact files changed, model record and artifact
shapes, A-E decisions, durable loop path, focused/full test counts, compileall,
diff check, `git status --short`, and remaining real-provider blockers.

