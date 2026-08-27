# SPEC: P8.4 Durable Model Loop

Status: Human-approved implementation boundary from the P8.4 execution brief on 2026-08-24.

## Objective

Give the existing provider-neutral `SingleAgentModelLoop` a durable, inspectable
boundary for one model turn and one tool observation. The implementation uses
`FakeModelAdapter` only. It must make a crash/restart decision from persisted
state without silently replaying a model request or a tool with unknown outcome.

This is a runtime contract slice, not a production model integration.

## Scope

- Add a runtime-owned `ModelExecutionRecord` with safe artifact references.
- Add a local JSON artifact store for model request/response artifacts with
  deterministic serialization and SHA-256 integrity references.
- Persist and restore model records and safe model-loop cursor state through the
  existing `RuntimeState` checkpointers.
- Rehydrate `ToolExecutionLedger` from `RuntimeState.tool_ledger`.
- Add a pure model/tool resume-decision contract.
- Upgrade the existing single-agent fake loop to persist request, response,
  observation and termination transitions.
- Keep `ToolResult` as the normalized observation representation; do not add a
  parallel `Observation` class.

## Ownership and source of truth

| State | Owner | Source of truth |
| --- | --- | --- |
| Model turn cursor | runtime / `AgentTurn` | `RuntimeState.turns` |
| Model exchange lifecycle | runtime / `ModelExecutionRecord` | `RuntimeState.model_executions` |
| Request/response payload | runtime artifact store | referenced artifact plus SHA-256 |
| Tool execution | `ToolRuntime` + `ToolExecutionLedger` | `RuntimeState.tool_ledger` after checkpoint |
| Final loop decision | runtime / `TerminationState` | `RuntimeState.termination` |
| Model proposal | model adapter output, normalized before persistence | response artifact; never authority for termination |

`RuntimeState.status` remains the P2/P4 whole-run status. A standalone fake
model loop reports its own completion in `TerminationState` and must not claim
that the enclosing P2/P4 run is completed unless the caller explicitly maps
that subflow to a run transition.

## ModelExecutionRecord contract

The record is JSON-safe and backward-compatible. It contains:

- `run_id`, `turn_id`, `task_id`, `agent_id`, `sequence`;
- lifecycle `status` (`request_durable`, `request_sent`,
  `response_obtained`, `response_durable`, `tool_result_durable`, `completed`,
  `failed`, `reinvoke_allowed`);
- `request_ref`, `tool_definition_snapshot_ref`, `observation_ref`,
  `response_ref`;
- `normalized_action` as a safe `ModelAction` dictionary when durable;
- `usage` and non-secret `provider_metadata`.

Refs are relative artifact references. Hashes are carried by artifact metadata,
not by absolute filesystem paths. No hidden reasoning, chain-of-thought,
provider-internal reasoning, secret/token, or unnecessary full Vault text may be
persisted.

## Artifact contract

The local `ModelArtifactStore` is deliberately not a blob platform. It writes
only below a caller-provided artifact root and returns a relative ref plus
SHA-256. Request artifacts may contain safe user/task representation, tool
definition snapshots, observation refs, runtime identity and provider/model
metadata. Response artifacts may contain normalized `ModelAction`, proposed
`ToolCall`, final proposal, usage, provider metadata and safe structured raw
response fields. Artifact JSON is UTF-8, `ensure_ascii=False`, sorted-key and
newline-terminated where practical. Reads verify both ref containment and hash.

## Crash and resume semantics

The implementation exposes decisions only; it never invokes a model or tool as
part of deciding:

| Window | Persisted condition | Decision |
| --- | --- | --- |
| A | turn exists; request durable; model not invoked | `safe_to_invoke_model` |
| B | request sent; response outcome unknown | `requires_verification` or `requires_manual_decision` |
| C | response obtained but response artifact not durable | `requires_verification` |
| D | response/action durable; ToolCall not executed | `reuse_durable_model_response` |
| E | ToolResult durable; next turn not created | `resume_from_tool_result` |
| terminal | runtime termination is non-running | `already_terminal` |

`requires_model_reinvoke` is available only for a caller that has explicitly
established that no provider request was sent (for example a durable request
record still in `request_durable`). The default decision for ambiguous provider
outcome is never blind reinvocation.

Completed and failed tool records are reused as terminal history. Pending tool
records use the existing read-only `decide_pending_recovery()` contract; no
automatic retry or side-effect replay is added.

## AgentTurn lifecycle

`AgentTurn` remains coarse-grained: `pending`, `running`, `completed`, `failed`,
`terminated`. Model exchange detail belongs to `ModelExecutionRecord`. A tool
error is an observation and does not force the whole runtime to terminate if a
future model turn can safely recover; the fake loop may still choose a terminal
failure for the current harness when configured to stop.

`TerminationState` is the final authority for the model-loop subflow. The
adapter cannot mark a run completed by returning a final string.

## Checkpoint payload boundary

`RuntimeState` stores identity, lifecycle, refs, hashes/metadata and compact
normalized tool error/result envelopes required for recovery. Inline tool
result envelopes are bounded at 128 KiB; an oversized result is not silently
truncated or treated as complete. The caller must retain the full observation
in an artifact and use its ref before exposing it to a future model turn.
Large or
sensitive model request/response payloads and future large observations belong
in the artifact store and are referenced from state. The first implementation
does not redesign `ToolResult`; it bounds model artifacts and keeps the ledger
compatible with existing P8.3 checkpoints.

## Commands

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q tests
python -m compileall -q src/linkloom tests
git diff --check
```

Focused tests may be run with `python -m pytest -q` and an explicit test path
while the editable install is unavailable. No real Vault is used.

## Explicit non-goals

- No OpenAI, Gemini, Anthropic or other network provider.
- No production `RetrievalAgent` migration.
- No multi-agent model loop.
- No ReAct, LangGraph, Memory or Evaluation redesign.
- No automatic retry/replay or exactly-once claim.
- No real-vault access or writeback change.
- No hidden reasoning persistence.
- No commit, push, PR or GitHub write.

## Acceptance criteria

- [ ] P8.4 SPEC and implementation plan are present.
- [ ] `ModelExecutionRecord` round-trips through JSON and rejects unsafe refs
      and hidden-reasoning fields.
- [ ] Request and response artifacts round-trip, return stable refs and reject
      tampering or path traversal.
- [ ] Old `RuntimeState` checkpoints without P8.4 fields still load safely.
- [ ] Restored completed, failed and pending ledger records remain present;
      duplicate call IDs cannot overwrite records.
- [ ] The fake loop durably records model request/response and observation
      boundaries, and uses restored terminal state without duplicate model/tool
      execution.
- [ ] Crash windows A-E and terminal behavior have regression tests.
- [ ] Observation changes can change the fake model action without relying on
      turn index alone.
- [ ] Full tests, compileall and diff checks are reported with no real Vault
      touched.
