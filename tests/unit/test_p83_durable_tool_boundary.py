"""P8.3 Phase A tests for durable ToolRuntime ordering and recovery advice."""

from __future__ import annotations

import json

import pytest

from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.tools.contracts import ToolCall, ToolDefinition
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.recovery import PendingRecoveryDecision, decide_pending_recovery
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


def _definition(tool_id: str = "search_notes") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
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


def _call(call_id: str = "call_p83_1", arguments: dict | None = None) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments=arguments if arguments is not None else {"query": "durability"},
        run_id="run_p83_1",
        task_id="task_p83_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _policy(max_calls: int = 3) -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["search_notes"],
            denied_tool_ids=[],
            max_calls=max_calls,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _runtime(executor, *, callback=None, ledger=None) -> ToolRuntime:
    registry = ToolRegistry()
    registry.register(_definition(), executor)
    return ToolRuntime(registry, ledger=ledger, checkpoint_callback=callback)


def _state(ledger: ToolExecutionLedger) -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id="run_p83_1",
        thread_id="thread_p83_1",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request_p83_1.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="root_p83",
        ),
        tool_ledger=ledger.to_list(),
    )


def test_pending_checkpoint_is_visible_before_executor_and_terminal_checkpoint_follows():
    ledger = ToolExecutionLedger()
    observed = []

    def checkpoint(current: ToolExecutionLedger) -> None:
        observed.append(current.get("call_p83_1").status)

    def executor(arguments):
        observed.append(f"executor:{ledger.get('call_p83_1').status}")
        return [{"evidence_id": "ev_1"}]

    result = _runtime(executor, callback=checkpoint, ledger=ledger).execute(_call(), _policy())

    assert result.status == "ok"
    assert observed == ["pending", "executor:pending", "completed"]


def test_pending_checkpoint_failure_blocks_executor_and_leaves_pending_recovery_record():
    ledger = ToolExecutionLedger()
    executions = []

    def checkpoint(current: ToolExecutionLedger) -> None:
        raise OSError("checkpoint backend secret path")

    result = _runtime(
        lambda arguments: executions.append(arguments) or [],
        callback=checkpoint,
        ledger=ledger,
    ).execute(_call(), _policy())

    assert result.status == "error"
    assert result.error.code == "TOOL_PENDING_CHECKPOINT_FAILED"
    assert executions == []
    assert ledger.get("call_p83_1").status == "pending"


def test_successful_executor_requires_terminal_checkpoint():
    checkpoints = []

    def checkpoint(current: ToolExecutionLedger) -> None:
        checkpoints.append(current.to_list()[0].status)

    result = _runtime(lambda arguments: [], callback=checkpoint).execute(_call(), _policy())

    assert result.status == "ok"
    assert checkpoints == ["pending", "completed"]


def test_executor_exception_records_failed_then_persists_terminal_record():
    checkpoints = []

    def checkpoint(current: ToolExecutionLedger) -> None:
        checkpoints.append(current.get("call_p83_1").status)

    result = _runtime(
        lambda arguments: (_ for _ in ()).throw(RuntimeError("secret traceback")),
        callback=checkpoint,
    ).execute(_call(), _policy())

    assert result.error.code == "TOOL_EXECUTION_FAILED"
    assert checkpoints == ["pending", "failed"]


def test_executor_success_and_terminal_checkpoint_failure_are_distinguishable():
    ledger = ToolExecutionLedger()
    calls = 0

    def checkpoint(current: ToolExecutionLedger) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("database unavailable")

    result = _runtime(lambda arguments: [{"evidence_id": "ev_1"}], callback=checkpoint, ledger=ledger).execute(
        _call(), _policy()
    )

    assert result.error.code == "TOOL_TERMINAL_CHECKPOINT_FAILED"
    assert result.error.details["executor_status"] == "succeeded"
    assert result.error.details["durability"] == "uncertain"
    assert result.error.details["external_execution_may_have_happened"] is True
    assert ledger.get("call_p83_1").status == "completed"


def test_executor_failure_and_terminal_checkpoint_failure_keep_both_outcomes():
    calls = 0

    def checkpoint(current: ToolExecutionLedger) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("database unavailable")

    result = _runtime(
        lambda arguments: (_ for _ in ()).throw(RuntimeError("executor secret")),
        callback=checkpoint,
    ).execute(_call(), _policy())

    assert result.error.code == "TOOL_TERMINAL_CHECKPOINT_FAILED"
    assert result.error.details["executor_status"] == "failed"
    assert result.error.details["executor_error_code"] == "TOOL_EXECUTION_FAILED"
    assert result.error.details["durability"] == "uncertain"


def test_pending_record_reload_gets_recovery_decision_without_automatic_retry():
    call = _call("pending_reload")
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    state = _state(ledger)
    checkpointer = InMemoryCheckpointer()
    checkpointer.save(state, checkpoint_id="cp_pending")

    restored = checkpointer.load("thread_p83_1", "cp_pending")
    restored_ledger = ToolExecutionLedger(restored.tool_ledger)
    decision = decide_pending_recovery(restored_ledger.get(call.call_id))

    assert isinstance(decision, PendingRecoveryDecision)
    assert decision.decision == "safe_to_retry"
    assert restored_ledger.get(call.call_id).status == "pending"
    with pytest.raises(ValidationError):
        restored_ledger.record_pending(call)


def test_non_read_only_pending_recovery_requires_manual_decision():
    call = _call("write_like", {"query": "anything"})
    record = ToolExecutionLedger()
    record.record_pending(call)
    pending = record.get(call.call_id)
    decision = decide_pending_recovery(pending, read_only_tool_ids=set())

    assert decision.decision == "requires_manual_decision"

