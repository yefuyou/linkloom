"""P8.4 durable FakeModel loop and resume regression tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.recovery import decide_model_resume
from linkloom.runtime.models import (
    AgentTurn,
    ModelExecutionRecord,
    RuntimeState,
    SourceContext,
    ToolExecutionRecord,
)
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


@pytest.fixture
def artifact_root() -> Path:
    """Use the repository artifact area because this host protects pytest temp ACLs."""
    root = Path(".artifacts") / "p84-test-runs" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state(**changes) -> RuntimeState:
    values = {
        "schema_version": 1,
        "run_id": "run_p84_loop",
        "thread_id": "thread_p84_loop",
        "workflow": "ask",
        "status": "running",
        "step_seq": 1,
        "request_ref": "request_p84_loop.json",
        "source": SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="d" * 64,
            vault_root_fingerprint="fixture_p84_loop",
        ),
    }
    values.update(changes)
    return RuntimeState(**values)


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


def _runtime(executor, ledger=None):
    registry = ToolRegistry()
    registry.register(_definition(), executor)
    return ToolRuntime(registry, ledger=ledger)


def _policy(max_calls: int = 4) -> ToolPolicyEnforcer:
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


def _proposal(call_id: str = "proposal_search") -> ToolCall:
    return ToolCall(call_id=call_id, tool_id="search_notes", arguments={"query": "durable"})


def test_durable_loop_persists_model_artifacts_and_tool_observation(artifact_root):
    model = FakeModelAdapter(
        [
            ModelAction.tool(_proposal()),
            lambda request: ModelAction.final(
                f"done:{request.observation.value[0]['evidence_id']}"
            ),
        ]
    )
    runtime = _runtime(lambda arguments: [{"evidence_id": "ev_p84"}])
    store = ModelArtifactStore(artifact_root / "models")

    result = SingleAgentModelLoop(model, runtime, _policy(), max_steps=3).run(
        state=_state(),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="find durable evidence",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "completed"
    assert result.final_answer == "done:ev_p84"
    assert result.state.status == "running"
    assert result.state.termination.status == "completed"
    assert [record.status for record in result.state.model_executions] == [
        "tool_result_durable",
        "completed",
    ]
    assert all(record.request_ref for record in result.state.model_executions)
    assert all(record.response_ref for record in result.state.model_executions)
    assert result.state.model_executions[0].observation_ref


def test_resume_from_durable_final_response_does_not_call_model_again(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    turn_id = "run_p84_loop:turn:1"
    action = ModelAction.final("durable final")
    response = store.write_response(
        "run_p84_loop",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": turn_id,
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {"adapter": "provider_neutral_fake"},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id=turn_id,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        request_ref="model/run_p84_loop/turn_1/request.json",
        response_ref=response.ref,
        normalized_action=action.to_dict(),
        response_sha256=response.sha256,
    )
    state = _state(
        turns=[
            AgentTurn(
                turn_id=turn_id,
                run_id="run_p84_loop",
                task_id="task_p84_loop",
                agent_id="retrieval_agent",
                sequence=1,
                status="running",
            )
        ],
        model_executions=[record],
    )
    calls = []
    model = FakeModelAdapter([lambda request: calls.append(request) or ModelAction.final("wrong")])

    result = SingleAgentModelLoop(model, _runtime(lambda arguments: []), _policy()).resume(
        state=state,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="ignored after durable final",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "completed"
    assert result.final_answer == "durable final"
    assert calls == []


def test_resume_uses_latest_model_record_by_sequence_not_list_position(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    latest_turn_id = "run_p84_loop:turn:2"
    action = ModelAction.final("latest durable answer")
    response = store.write_response(
        "run_p84_loop",
        latest_turn_id,
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": latest_turn_id,
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 2,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {"adapter": "provider_neutral_fake"},
        },
    )
    latest = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id=latest_turn_id,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=2,
        status="response_durable",
        request_ref="model/run_p84_loop/turn_2/request.json",
        response_ref=response.ref,
        normalized_action=action.to_dict(),
        response_sha256=response.sha256,
    )
    older = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id="run_p84_loop:turn:1",
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="request_durable",
        request_ref="model/run_p84_loop/turn_1/request.json",
    )
    state = _state(
        turns=[
            AgentTurn(
                turn_id="run_p84_loop:turn:1",
                run_id="run_p84_loop",
                task_id="task_p84_loop",
                agent_id="retrieval_agent",
                sequence=1,
                status="completed",
            ),
            AgentTurn(
                turn_id=latest_turn_id,
                run_id="run_p84_loop",
                task_id="task_p84_loop",
                agent_id="retrieval_agent",
                sequence=2,
                status="running",
            ),
        ],
        model_executions=[latest, older],
    )
    model = FakeModelAdapter([ModelAction.final("must not call model")])

    result = SingleAgentModelLoop(model, _runtime(lambda arguments: []), _policy()).resume(
        state=state,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="ignored after latest durable final",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "completed"
    assert result.final_answer == "latest durable answer"
    assert model.call_count == 0


def test_resume_rejects_response_artifact_bound_to_another_turn(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    action = ModelAction.final("wrong turn answer")
    response = store.write_response(
        "run_p84_loop",
        "run_p84_loop:turn:1",
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": "run_p84_loop:turn:1",
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id="run_p84_loop:turn:2",
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=2,
        status="response_durable",
        request_ref="model/run_p84_loop/turn_2/request.json",
        response_ref=response.ref,
        normalized_action=action.to_dict(),
        response_sha256=response.sha256,
    )
    model = FakeModelAdapter([ModelAction.final("must not call model")])

    result = SingleAgentModelLoop(model, _runtime(lambda arguments: []), _policy()).resume(
        state=_state(
            turns=[
                AgentTurn(
                    turn_id=record.turn_id,
                    run_id="run_p84_loop",
                    task_id="task_p84_loop",
                    agent_id="retrieval_agent",
                    sequence=2,
                    status="running",
                )
            ],
            model_executions=[record],
        ),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="artifact mismatch",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0


def test_pending_checkpoint_failure_does_not_become_durable_observation(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    model = FakeModelAdapter(
        [
            ModelAction.tool(_proposal("pending_checkpoint_call")),
            ModelAction.final("must not continue after uncertain pending boundary"),
        ]
    )
    runtime = _runtime(lambda arguments: [{"evidence_id": "must_not_execute"}])

    def checkpoint(current_state):
        if current_state.tool_ledger and current_state.tool_ledger[-1].status == "pending":
            raise RuntimeError("checkpoint backend unavailable")

    result = SingleAgentModelLoop(model, runtime, _policy(), max_steps=2).run(
        state=_state(),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="pending checkpoint failure",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=checkpoint,
    )

    assert result.status == "failed"
    assert result.error.code == "TOOL_PENDING_CHECKPOINT_FAILED"
    assert model.call_count == 1
    assert result.state.tool_ledger[0].status == "pending"
    assert result.state.model_executions[-1].status == "failed"


def test_terminal_checkpoint_failure_does_not_continue_with_uncertain_observation(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    model = FakeModelAdapter(
        [
            ModelAction.tool(_proposal("terminal_checkpoint_call")),
            ModelAction.final("must not continue after uncertain terminal boundary"),
        ]
    )
    runtime = _runtime(lambda arguments: [{"evidence_id": "executor_ran"}])

    def checkpoint(current_state):
        if current_state.tool_ledger and current_state.tool_ledger[-1].status == "completed":
            raise RuntimeError("terminal checkpoint backend unavailable")

    result = SingleAgentModelLoop(model, runtime, _policy(), max_steps=2).run(
        state=_state(),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="terminal checkpoint failure",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=checkpoint,
    )

    assert result.status == "failed"
    assert result.error.code == "TOOL_TERMINAL_CHECKPOINT_FAILED"
    assert model.call_count == 1
    assert result.state.model_executions[-1].status == "failed"
    assert result.state.model_executions[-1].observation_ref is None
    assert result.state.turns[-1].status == "failed"


def test_resume_rejects_terminal_tool_result_identity_mismatch(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    old_call = _proposal("reused_call")
    old_result = ToolResult(
        call_id=old_call.call_id,
        tool_id=old_call.tool_id,
        status="ok",
        value=[{"evidence_id": "old_arguments"}],
    )
    ledger = ToolExecutionLedger()
    ledger.record_pending(old_call)
    ledger.record_completed(old_call, old_result)
    new_call = ToolCall(
        call_id=old_call.call_id,
        tool_id=old_call.tool_id,
        arguments={"query": "different_arguments"},
        run_id="run_p84_loop",
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
    )
    turn_id = "run_p84_loop:turn:1"
    action = ModelAction.tool(new_call)
    response = store.write_response(
        "run_p84_loop",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": turn_id,
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id=turn_id,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        request_ref="model/run_p84_loop/turn_1/request.json",
        response_ref=response.ref,
        normalized_action=action.to_dict(),
        response_sha256=response.sha256,
    )
    executions = []
    model = FakeModelAdapter([ModelAction.final("must not execute or reuse")])

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executions.append(arguments) or []),
        _policy(),
    ).resume(
        state=_state(
            turns=[
                AgentTurn(
                    turn_id=turn_id,
                    run_id="run_p84_loop",
                    task_id="task_p84_loop",
                    agent_id="retrieval_agent",
                    sequence=1,
                    status="running",
                )
            ],
            tool_ledger=ledger.to_list(),
            model_executions=[record],
        ),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="identity mismatch",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert executions == []
    assert model.call_count == 0


def test_resume_does_not_replay_pending_tool(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    call = _proposal("pending_resume_call")
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    turn_id = "run_p84_loop:turn:1"
    action = ModelAction.tool(call)
    response = store.write_response(
        "run_p84_loop",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": turn_id,
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id=turn_id,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        request_ref="model/run_p84_loop/turn_1/request.json",
        response_ref=response.ref,
        normalized_action=action.to_dict(),
        response_sha256=response.sha256,
    )
    executions = []
    model = FakeModelAdapter([ModelAction.final("must not resume ambiguous tool")])

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executions.append(arguments) or []),
        _policy(),
    ).resume(
        state=_state(
            turns=[
                AgentTurn(
                    turn_id=turn_id,
                    run_id="run_p84_loop",
                    task_id="task_p84_loop",
                    agent_id="retrieval_agent",
                    sequence=1,
                    status="running",
                    tool_call_ids=[call.call_id],
                )
            ],
            tool_ledger=ledger.to_list(),
            model_executions=[record],
        ),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="pending resume",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_RESUME_REQUIRES_VERIFICATION"
    assert executions == []
    assert model.call_count == 0


def test_resume_from_completed_tool_result_reuses_observation_and_does_not_execute_tool(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    call = _proposal("call_p84_done")
    tool_result = ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_restored"}],
    )
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    ledger.record_completed(call, tool_result)
    turn_id = "run_p84_loop:turn:1"
    action = ModelAction.tool(call)
    response = store.write_response(
        "run_p84_loop",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": turn_id,
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {"adapter": "provider_neutral_fake"},
        },
    )
    observation = store.write_observation(
        "run_p84_loop",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": turn_id,
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "tool_result": tool_result.to_dict(),
        },
    )
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id=turn_id,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="tool_result_durable",
        request_ref="model/run_p84_loop/turn_1/request.json",
        response_ref=response.ref,
        observation_ref=observation.ref,
        normalized_action=action.to_dict(),
        response_sha256=response.sha256,
    )
    state = _state(
        turns=[
            AgentTurn(
                turn_id=turn_id,
                run_id="run_p84_loop",
                task_id="task_p84_loop",
                agent_id="retrieval_agent",
                sequence=1,
                status="completed",
                tool_call_ids=[call.call_id],
            )
        ],
        tool_ledger=ledger.to_list(),
        model_executions=[record],
    )
    executions = []
    model = FakeModelAdapter(
        [lambda request: ModelAction.final(f"restored:{request.observation.value[0]['evidence_id']}")]
    )

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executions.append(arguments) or []),
        _policy(),
    ).resume(
        state=state,
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="resume from result",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "completed"
    assert result.final_answer == "restored:ev_restored"
    assert executions == []
    assert model.call_count == 1


def test_ambiguous_model_window_returns_safe_error_without_model_call(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id="run_p84_loop:turn:1",
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="request_sent",
        request_ref="model/run_p84_loop/turn_1/request.json",
    )
    model = FakeModelAdapter([ModelAction.final("must not run")])

    result = SingleAgentModelLoop(model, _runtime(lambda arguments: []), _policy()).resume(
        state=_state(model_executions=[record]),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="ambiguous",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_RESUME_REQUIRES_VERIFICATION"
    assert model.call_count == 0


@pytest.mark.parametrize(
    ("crash_status", "expected_decision", "expected_model_calls"),
    [
        ("request_durable", "safe_to_invoke_model", 0),
        ("request_sent", "requires_verification", 0),
        ("response_obtained", "requires_verification", 1),
        ("response_durable", "reuse_durable_model_response", 1),
    ],
)
def test_crash_windows_a_to_d_have_explicit_resume_decisions(
    artifact_root,
    crash_status,
    expected_decision,
    expected_model_calls,
):
    model = FakeModelAdapter([ModelAction.final("durable answer")])
    store = ModelArtifactStore(artifact_root / "models")
    snapshots = []

    def checkpoint(current_state):
        snapshots.append(current_state)
        if current_state.model_executions[-1].status == crash_status:
            raise RuntimeError("simulated process crash")

    result = SingleAgentModelLoop(model, _runtime(lambda arguments: []), _policy()).run(
        state=_state(),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="crash boundary",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=checkpoint,
    )

    assert result.status == "failed"
    assert snapshots
    durable_snapshot = next(
        snapshot
        for snapshot in reversed(snapshots)
        if snapshot.termination is None or snapshot.termination.status == "running"
    )
    assert durable_snapshot.model_executions[-1].status == crash_status
    assert decide_model_resume(durable_snapshot).decision == expected_decision
    assert model.call_count == expected_model_calls


def test_window_e_reuses_terminal_ledger_result_without_executor_replay(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    call = _proposal("call_p84_window_e")
    result = ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_window_e"}],
    )
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    ledger.record_completed(call, result)
    response = store.write_response(
        "run_p84_loop",
        "run_p84_loop:turn:1",
        {
            "runtime_identity": {
                "run_id": "run_p84_loop",
                "turn_id": "run_p84_loop:turn:1",
                "task_id": "task_p84_loop",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": ModelAction.tool(call).to_dict(),
            "usage": {},
            "provider_metadata": {"adapter": "provider_neutral_fake"},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_p84_loop",
        turn_id="run_p84_loop:turn:1",
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        request_ref="model/run_p84_loop/turn_1/request.json",
        response_ref=response.ref,
        response_sha256=response.sha256,
        normalized_action=ModelAction.tool(call).to_dict(),
    )
    executions = []
    model = FakeModelAdapter(
        [lambda request: ModelAction.final(f"observed:{request.observation.value[0]['evidence_id']}")]
    )

    resumed = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executions.append(arguments) or []),
        _policy(),
    ).resume(
        state=_state(
            turns=[
                AgentTurn(
                    turn_id=record.turn_id,
                    run_id=record.run_id,
                    task_id=record.task_id,
                    agent_id=record.agent_id,
                    sequence=1,
                    status="running",
                    tool_call_ids=[call.call_id],
                )
            ],
            tool_ledger=ledger.to_list(),
            model_executions=[record],
        ),
        task_id="task_p84_loop",
        agent_id="retrieval_agent",
        user_input="window e",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert resumed.status == "completed"
    assert resumed.final_answer == "observed:ev_window_e"
    assert executions == []


def test_same_input_with_different_observation_changes_fake_model_action():
    model = FakeModelAdapter(
        [
            lambda request: ModelAction.final(
                "not_found"
                if request.observation.business_status == "NOT_FOUND"
                else "evidence"
            ),
            lambda request: ModelAction.final(
                "not_found"
                if request.observation.business_status == "NOT_FOUND"
                else "evidence"
            ),
        ]
    )
    not_found = ToolResult(
        call_id="call_obs_nf",
        tool_id="search_notes",
        status="ok",
        value=[],
        business_status="NOT_FOUND",
    )
    evidence = ToolResult(
        call_id="call_obs_ev",
        tool_id="search_notes",
        status="ok",
        value=[{"evidence_id": "ev_obs"}],
    )

    actions = [
        model.decide(
            ModelTurnRequest(
                run_id="run_obs",
                turn_id="turn_obs",
                task_id="task_obs",
                agent_id="retrieval_agent",
                sequence=2,
                user_input="same input",
                observation=observation,
                available_tools=[],
            )
        )
        for observation in (not_found, evidence)
    ]

    assert [action.final_answer for action in actions] == ["not_found", "evidence"]
