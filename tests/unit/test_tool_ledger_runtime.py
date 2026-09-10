"""ToolRuntime integration tests for the P8.2 durable tool ledger boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


def _definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _call(call_id: str = "call_runtime_1", arguments: dict | None = None) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments=arguments if arguments is not None else {"query": "state"},
        run_id="run_runtime_1",
        task_id="task_runtime_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _policy(*, allowed: list[str] | None = None, max_calls: int = 3) -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=allowed if allowed is not None else ["search_notes"],
            denied_tool_ids=[],
            max_calls=max_calls,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _runtime(executor, ledger: ToolExecutionLedger | None = None) -> ToolRuntime:
    registry = ToolRegistry()
    registry.register(_definition(), executor)
    return ToolRuntime(registry, ledger=ledger)


def test_runtime_records_completed_call_after_valid_execution():
    ledger = ToolExecutionLedger()
    observed_pending = []

    def executor(arguments):
        observed_pending.append(ledger.get("call_runtime_1").status)
        return [{"evidence_id": "ev_1"}]

    runtime = _runtime(executor, ledger)

    result = runtime.execute(_call(), _policy())

    assert result.status == "ok"
    assert observed_pending == ["pending"]
    record = ledger.get("call_runtime_1")
    assert record is not None
    assert record.status == "completed"
    assert record.result["status"] == "ok"


def test_runtime_records_failed_call_for_executor_exception_and_consumes_budget():
    ledger = ToolExecutionLedger()
    runtime = _runtime(lambda arguments: (_ for _ in ()).throw(RuntimeError("secret")), ledger)
    policy = _policy(max_calls=1)

    result = runtime.execute(_call(), policy)

    assert result.error is not None
    assert result.error.code == "TOOL_EXECUTION_FAILED"
    assert policy.call_count == 1
    record = ledger.get("call_runtime_1")
    assert record is not None
    assert record.status == "failed"
    assert record.error["code"] == "TOOL_EXECUTION_FAILED"


@pytest.mark.parametrize(
    ("call", "policy"),
    [
        (_call("malformed", {}), _policy(max_calls=1)),
        (_call("denied"), _policy(allowed=[], max_calls=1)),
        (_call("budget"), _policy(max_calls=0)),
    ],
)
def test_pre_authorization_failures_do_not_create_ledger_records_or_execute(call, policy):
    executions = []
    ledger = ToolExecutionLedger()
    runtime = _runtime(lambda arguments: executions.append(arguments) or [], ledger)

    result = runtime.execute(call, policy)

    assert result.status == "error"
    assert ledger.get(call.call_id) is None
    assert executions == []
    assert policy.call_count == 0


def test_unknown_tool_does_not_create_pending_record():
    ledger = ToolExecutionLedger()
    runtime = _runtime(lambda arguments: [], ledger)
    policy = _policy()
    call = ToolCall(
        call_id="unknown_tool_call",
        tool_id="does_not_exist",
        arguments={"query": "state"},
        run_id="run_runtime_1",
        task_id="task_runtime_1",
        agent_id="retrieval_agent",
    )

    result = runtime.execute(call, policy)

    assert result.error is not None
    assert result.error.code == "TOOL_UNKNOWN"
    assert ledger.get(call.call_id) is None
    assert policy.call_count == 0


def test_invalid_output_is_terminal_failed_record_after_authorization():
    ledger = ToolExecutionLedger()
    runtime = _runtime(lambda arguments: {"not": "an array"}, ledger)
    policy = _policy(max_calls=1)

    result = runtime.execute(_call(), policy)

    assert result.error is not None
    assert result.error.code == "TOOL_INVALID_OUTPUT"
    assert policy.call_count == 1
    assert ledger.get("call_runtime_1").status == "failed"


def test_business_not_found_is_completed_not_execution_failed():
    ledger = ToolExecutionLedger()
    call = _call()

    def executor(arguments):
        return ToolResult(
            call_id=call.call_id,
            tool_id=call.tool_id,
            status="ok",
            value=[],
            business_status="NOT_FOUND",
        )

    runtime = _runtime(executor, ledger)
    result = runtime.execute(call, _policy())

    assert result.status == "ok"
    assert result.business_status == "NOT_FOUND"
    assert ledger.get(call.call_id).status == "completed"
    assert ledger.get(call.call_id).result["business_status"] == "NOT_FOUND"


def test_duplicate_call_id_is_explicit_and_never_overwrites_existing_record():
    ledger = ToolExecutionLedger()
    ledger.record_pending(_call())
    runtime = _runtime(lambda arguments: [], ledger)

    result = runtime.execute(_call(), _policy())

    assert result.error is not None
    assert result.error.code == "TOOL_LEDGER_CONFLICT"
    assert ledger.get("call_runtime_1").status == "pending"


def test_pending_record_survives_checkpoint_reload_as_unknown_outcome():
    call = _call("crash_window")
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)

    state = RuntimeState(
        schema_version=1,
        run_id="run_runtime_1",
        thread_id="thread_runtime_1",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request_runtime_1.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="root_runtime",
        ),
        tool_ledger=ledger.to_list(),
    )
    checkpointer = InMemoryCheckpointer()
    checkpointer.save(state, checkpoint_id="cp_pending")

    restored = checkpointer.load("thread_runtime_1", "cp_pending")
    restored_ledger = ToolExecutionLedger(restored.tool_ledger)

    record = restored_ledger.get(call.call_id)
    assert record is not None
    assert record.status == "pending"
    assert record.result is None
    assert record.error is None
    with pytest.raises(ValidationError):
        restored_ledger.record_pending(call)
