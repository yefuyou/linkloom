"""P8.4 ledger rehydration and pure model-resume decision tests."""

from __future__ import annotations

import pytest

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    ModelExecutionRecord,
    RuntimeState,
    SourceContext,
    TerminationState,
    ToolExecutionRecord,
)
from linkloom.runtime.recovery import decide_model_resume
from linkloom.tools.contracts import ToolCall, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger


def _call(call_id: str = "call_p84_1") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments={"query": "resume"},
        run_id="run_p84_recovery",
        task_id="task_p84_recovery",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _result(call: ToolCall) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_resume"}],
    )


def _state(*, records=None, ledger=None, termination=None) -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id="run_p84_recovery",
        thread_id="thread_p84_recovery",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request_p84_recovery.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="c" * 64,
            vault_root_fingerprint="fixture_p84_recovery",
        ),
        model_executions=records or [],
        tool_ledger=ledger or [],
        termination=termination,
    )


def _record(status: str, *, action=None, observation_ref=None) -> ModelExecutionRecord:
    return ModelExecutionRecord(
        run_id="run_p84_recovery",
        turn_id="run_p84_recovery:turn:1",
        task_id="task_p84_recovery",
        agent_id="retrieval_agent",
        sequence=1,
        status=status,
        request_ref="model/run_p84_recovery/turn_1/request.json",
        response_ref=(
            "model/run_p84_recovery/turn_1/response.json"
            if action is not None
            else None
        ),
        observation_ref=observation_ref,
        normalized_action=action,
    )


def _tool_action(call_id: str = "call_p84_1") -> dict:
    return {
        "kind": "tool_call",
        "tool_call": {
            "call_id": call_id,
            "tool_id": "search_notes",
            "arguments": {"query": "resume"},
        },
    }


def test_ledger_from_list_preserves_terminal_and_pending_records_without_overwrite():
    call = _call()
    completed = ToolExecutionRecord(
        call_id=call.call_id,
        run_id=call.run_id,
        task_id=call.task_id,
        agent_id=call.agent_id,
        tool_id=call.tool_id,
        sequence=call.sequence,
        status="completed",
        arguments=call.arguments,
        result=_result(call).to_dict(),
    )
    pending_call = _call("call_p84_pending")
    pending = ToolExecutionRecord(
        call_id=pending_call.call_id,
        run_id=pending_call.run_id,
        task_id=pending_call.task_id,
        agent_id=pending_call.agent_id,
        tool_id=pending_call.tool_id,
        sequence=pending_call.sequence,
        status="pending",
        arguments=pending_call.arguments,
    )

    restored = ToolExecutionLedger.from_list([completed.to_dict(), pending.to_dict()])

    assert [record.status for record in restored.to_list()] == ["completed", "pending"]
    with pytest.raises(ValidationError):
        ToolExecutionLedger.from_list([completed.to_dict(), completed.to_dict()])


def test_ledger_from_list_preserves_failed_record_and_safe_error():
    call = _call("call_p84_failed")
    failed = ToolExecutionRecord(
        call_id=call.call_id,
        run_id=call.run_id,
        task_id=call.task_id,
        agent_id=call.agent_id,
        tool_id=call.tool_id,
        sequence=call.sequence,
        status="failed",
        arguments=call.arguments,
        error={
            "code": "TOOL_EXECUTION_FAILED",
            "category": "runtime",
            "message": "Tool execution failed.",
        },
    )

    restored = ToolExecutionLedger.from_list([failed.to_dict()])

    assert restored.get(call.call_id).status == "failed"
    assert restored.get(call.call_id).error["code"] == "TOOL_EXECUTION_FAILED"


def test_resume_decisions_cover_safe_request_ambiguity_durable_response_and_terminal_state():
    assert decide_model_resume(_state()).decision == "safe_to_invoke_model"
    assert decide_model_resume(_state(records=[_record("request_sent")])).decision == "requires_verification"
    assert decide_model_resume(_state(records=[_record("response_obtained")])).decision == "requires_verification"
    assert (
        decide_model_resume(_state(records=[_record("reinvoke_allowed")])).decision
        == "requires_model_reinvoke"
    )
    assert (
        decide_model_resume(_state(records=[_record("response_durable", action=_tool_action())])).decision
        == "reuse_durable_model_response"
    )
    assert (
        decide_model_resume(
            _state(
                records=[_record("tool_result_durable", action=_tool_action(), observation_ref="model/obs.json")]
            )
        ).decision
        == "resume_from_tool_result"
    )
    assert (
        decide_model_resume(
            _state(termination=TerminationState(status="completed", reason_code="model_final", sequence=1))
        ).decision
        == "already_terminal"
    )


def test_completed_tool_record_is_resume_observation_not_a_second_execution():
    call = _call()
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    ledger.record_completed(call, _result(call))
    record = _record("response_durable", action=_tool_action())

    decision = decide_model_resume(_state(records=[record], ledger=ledger.to_list()))

    assert decision.decision == "resume_from_tool_result"
    assert decision.call_id == call.call_id


def test_pending_tool_record_requires_verification_and_never_auto_replays():
    call = _call()
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    record = _record("response_durable", action=_tool_action())

    decision = decide_model_resume(_state(records=[record], ledger=ledger.to_list()))

    assert decision.decision == "requires_verification"
    assert decision.call_id == call.call_id
