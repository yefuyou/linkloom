"""P8.2 state-contract and durable tool-ledger regression tests."""

from __future__ import annotations

import json

import pytest

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    AgentTurn,
    RuntimeState,
    SourceContext,
    TerminationState,
    ToolExecutionRecord,
)
from linkloom.tools.contracts import ToolCall, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger


def _source() -> SourceContext:
    return SourceContext(
        index_path=".artifacts/scan/vault_index.json",
        index_sha256="a" * 64,
        vault_root_fingerprint="root_p82",
        fixture_id="p82",
    )


def _call(call_id: str = "call_p82_1") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments={"query": "durability"},
        run_id="run_p82_1",
        task_id="task_p82_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _success(call: ToolCall) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_1"}],
    )


def _runtime_state(**changes) -> RuntimeState:
    values = {
        "schema_version": 1,
        "run_id": "run_p82_1",
        "thread_id": "thread_p82_1",
        "workflow": "ask",
        "status": "running",
        "step_seq": 1,
        "request_ref": "request_p82_1.json",
        "source": _source(),
        "created_at": "2026-08-23T10:00:00Z",
        "updated_at": "2026-08-23T10:00:01Z",
    }
    values.update(changes)
    return RuntimeState(**values)


def test_agent_turn_and_termination_roundtrip_without_message_or_observation_models():
    turn = AgentTurn(
        turn_id="turn_p82_1",
        run_id="run_p82_1",
        task_id="task_p82_1",
        agent_id="retrieval_agent",
        sequence=2,
        status="completed",
        tool_call_ids=["call_p82_1"],
        model_request_ref="models/turn_p82_1/request.json",
        model_response_ref="models/turn_p82_1/response.json",
        usage={"input_tokens": 10, "output_tokens": 4},
    )
    termination = TerminationState(
        status="completed",
        reason_code="agent_stop",
        reason="The runtime accepted the terminal decision.",
        sequence=2,
    )

    assert AgentTurn.from_dict(json.loads(json.dumps(turn.to_dict()))) == turn
    assert TerminationState.from_dict(json.loads(json.dumps(termination.to_dict()))) == termination


def test_agent_turn_rejects_absolute_artifact_refs_and_invalid_status():
    with pytest.raises(ValidationError):
        AgentTurn(
            turn_id="turn_p82_1",
            run_id="run_p82_1",
            task_id="task_p82_1",
            agent_id="retrieval_agent",
            sequence=0,
            status="unknown",
        )

    with pytest.raises(ValidationError):
        AgentTurn(
            turn_id="turn_p82_1",
            run_id="run_p82_1",
            task_id="task_p82_1",
            agent_id="retrieval_agent",
            sequence=0,
            status="running",
            model_request_ref="C:/private/prompt.json",
        )


def test_tool_execution_record_roundtrip_and_terminal_shapes():
    call = _call()
    record = ToolExecutionRecord(
        call_id=call.call_id,
        run_id=call.run_id,
        task_id=call.task_id,
        agent_id=call.agent_id,
        tool_id=call.tool_id,
        sequence=call.sequence,
        status="completed",
        arguments=call.arguments,
        result=_success(call).to_dict(),
        idempotency_key="run_p82_1:call_p82_1",
    )

    restored = ToolExecutionRecord.from_dict(json.loads(json.dumps(record.to_dict())))
    assert restored == record
    assert restored.result["status"] == "ok"
    assert restored.error is None

    with pytest.raises(ValidationError):
        ToolExecutionRecord(
            call_id="call_p82_bad",
            run_id="run_p82_1",
            task_id="task_p82_1",
            agent_id="retrieval_agent",
            tool_id="search_notes",
            sequence=1,
            status="pending",
            result=_success(call).to_dict(),
        )


def test_runtime_state_roundtrip_includes_turns_termination_and_ledger():
    call = _call()
    state = _runtime_state(
        turns=[
            AgentTurn(
                turn_id="turn_p82_1",
                run_id=call.run_id,
                task_id=call.task_id,
                agent_id=call.agent_id,
                sequence=1,
                status="running",
                tool_call_ids=[call.call_id],
            )
        ],
        tool_ledger=[
            ToolExecutionRecord(
                call_id=call.call_id,
                run_id=call.run_id,
                task_id=call.task_id,
                agent_id=call.agent_id,
                tool_id=call.tool_id,
                sequence=call.sequence,
                status="pending",
                arguments=call.arguments,
            )
        ],
        termination=TerminationState(status="running", sequence=1),
    )

    payload = json.loads(json.dumps(state.to_dict()))
    restored = RuntimeState.from_dict(payload)
    assert restored == state
    assert restored.tool_ledger[0].status == "pending"
    assert restored.termination.status == "running"


def test_old_runtime_checkpoint_without_p82_fields_uses_safe_defaults():
    legacy = _runtime_state().to_dict()
    legacy.pop("turns")
    legacy.pop("tool_ledger")
    legacy.pop("termination")

    restored = RuntimeState.from_dict(legacy)

    assert restored.turns == []
    assert restored.tool_ledger == []
    assert restored.termination is None


def test_tool_ledger_transitions_are_explicit_and_non_overwriting():
    call = _call()
    ledger = ToolExecutionLedger()

    pending = ledger.record_pending(call)
    assert pending.status == "pending"
    assert ledger.get(call.call_id) == pending

    completed = ledger.record_completed(call, _success(call))
    assert completed.status == "completed"
    assert completed.result["call_id"] == call.call_id
    assert ledger.list_for_run(call.run_id) == [completed]

    with pytest.raises(ValidationError) as duplicate:
        ledger.record_pending(call)
    assert duplicate.value.details["reason"] == "duplicate_call_id"

    with pytest.raises(ValidationError) as transition:
        ledger.record_failed(
            call,
            ToolError(code="TOOL_EXECUTION_FAILED", category="runtime", message="failed"),
        )
    assert transition.value.details["reason"] == "invalid_transition"


def test_tool_ledger_requires_pending_before_terminal_and_rejects_unknown_calls():
    call = _call()
    ledger = ToolExecutionLedger()
    error = ToolError(code="TOOL_EXECUTION_FAILED", category="runtime", message="failed")

    with pytest.raises(ValidationError) as unknown:
        ledger.record_failed(call, error)
    assert unknown.value.details["reason"] == "unknown_call_id"

    pending = ledger.record_pending(call)
    failed = ledger.record_failed(call, error)
    assert failed.status == "failed"
    assert failed.error == error.to_dict()
    assert ledger.get(call.call_id) != pending


def test_tool_ledger_rejects_call_identity_mismatch_without_mutating_record():
    call = _call()
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    mismatched = ToolCall(
        call_id=call.call_id,
        tool_id="read_verified_note",
        arguments={"note_ref": "note.md"},
        run_id=call.run_id,
        task_id=call.task_id,
        agent_id=call.agent_id,
        sequence=call.sequence,
    )

    with pytest.raises(ValidationError) as mismatch:
        ledger.record_completed(mismatched, _success(call))
    assert mismatch.value.details["reason"] == "call_identity_mismatch"
    assert ledger.get(call.call_id).status == "pending"
