from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import (
    ModelResponse,
    ModelToolCall,
    ModelTurnProposal,
    ModelTurnRequest,
)
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.tools.contracts import ToolDefinition
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


RUN_ID = "run_runtime_v2_resume"
TASK_ID = "task_runtime_v2_resume"
AGENT_ID = "retrieval_agent"


class ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.requests: list[ModelTurnRequest] = []

    def complete(self, request: ModelTurnRequest) -> ModelResponse:
        step = self.script[len(self.requests)]
        self.requests.append(request)
        return step(request) if callable(step) else step


@pytest.fixture
def artifact_store() -> ModelArtifactStore:
    root = Path(".artifacts") / "runtime-v2-resume-tests" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return ModelArtifactStore(root / "models")


def _state() -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id=RUN_ID,
        thread_id="thread_runtime_v2_resume",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request_runtime_v2_resume.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="f" * 64,
            vault_root_fingerprint="fixture_runtime_v2_resume",
        ),
    )


def _definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search synthetic notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _loop(provider, executor) -> SingleAgentModelLoop:
    registry = ToolRegistry()
    registry.register(_definition(), executor)
    policy = ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id=AGENT_ID,
            allowed_tool_ids=["search_notes"],
            denied_tool_ids=[],
            max_calls=8,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )
    return SingleAgentModelLoop(provider, ToolRuntime(registry), policy, max_steps=4)


def _tool_response(request: ModelTurnRequest) -> ModelResponse:
    proposal_id = f"{request.turn_id}:proposal"
    calls = [
        ModelToolCall.bind(
            provider_call_id=f"provider-call-{name}",
            proposal_id=proposal_id,
            ordinal=ordinal,
            tool_id="search_notes",
            arguments={"query": name},
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            sequence=request.sequence,
        )
        for ordinal, name in enumerate(("A", "B", "C"))
    ]
    return ModelResponse(proposal=ModelTurnProposal.tools(proposal_id, calls))


def _tool_response_for(*names: str):
    def respond(request: ModelTurnRequest) -> ModelResponse:
        proposal_id = f"{request.turn_id}:proposal"
        calls = [
            ModelToolCall.bind(
                provider_call_id=f"provider-call-{name}",
                proposal_id=proposal_id,
                ordinal=ordinal,
                tool_id="search_notes",
                arguments={"query": name},
                run_id=request.run_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                sequence=request.sequence,
            )
            for ordinal, name in enumerate(names)
        ]
        return ModelResponse(
            proposal=ModelTurnProposal.tools(proposal_id, calls)
        )

    return respond


def _final_response(request: ModelTurnRequest) -> ModelResponse:
    return ModelResponse(
        proposal=ModelTurnProposal.final(
            f"{request.turn_id}:proposal",
            "resumed complete",
        )
    )


def _run(loop, state, artifact_store, callback, *, resume=False):
    method = loop.resume if resume else loop.run
    return method(
        state=state,
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        user_input="resume one multi-action proposal",
        available_tools=[_definition()],
        artifact_store=artifact_store,
        checkpoint_callback=callback,
    )


@pytest.mark.parametrize("durable_prefix", [1, 2])
def test_cold_resume_continues_after_durable_prefix_without_replay(
    artifact_store,
    durable_prefix,
):
    executor_order: list[str] = []
    durable_states: list[RuntimeState] = []

    def stop_after_prefix(state: RuntimeState) -> None:
        snapshot = RuntimeState.from_dict(state.to_dict())
        durable_states.append(snapshot)
        latest = snapshot.model_executions[-1]
        if (
            latest.status == "tool_results_partial"
            and len(latest.tool_result_refs) == durable_prefix
        ):
            raise RuntimeError("synthetic crash after durable prefix")

    first_provider = ScriptedProvider([_tool_response])
    interrupted = _run(
        _loop(
            first_provider,
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        _state(),
        artifact_store,
        stop_after_prefix,
    )
    durable_state = next(
        state
        for state in durable_states
        if state.model_executions[-1].status == "tool_results_partial"
        and len(state.model_executions[-1].tool_result_refs) == durable_prefix
    )

    resumed_provider = ScriptedProvider([_final_response])
    resumed = _run(
        _loop(
            resumed_provider,
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        durable_state,
        artifact_store,
        lambda state: None,
        resume=True,
    )

    assert interrupted.status == "failed"
    assert resumed.status == "completed"
    assert executor_order == ["A", "B", "C"]
    assert len(first_provider.requests) == 1
    assert len(resumed_provider.requests) == 1
    assert len(resumed.state.model_executions[0].tool_result_refs) == 3


def test_terminal_ledger_reconstructs_missing_result_ref_without_replay(
    artifact_store,
):
    executor_order: list[str] = []
    durable_states: list[RuntimeState] = []

    def stop_after_a_terminal_ledger(state: RuntimeState) -> None:
        snapshot = RuntimeState.from_dict(state.to_dict())
        if (
            snapshot.model_executions[-1].status == "response_durable"
            and len(snapshot.tool_ledger) == 1
            and snapshot.tool_ledger[0].status == "completed"
        ):
            durable_states.append(snapshot)
            raise RuntimeError("synthetic crash before result-ref checkpoint")

    first_provider = ScriptedProvider([_tool_response])
    _run(
        _loop(
            first_provider,
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        _state(),
        artifact_store,
        stop_after_a_terminal_ledger,
    )

    resumed_provider = ScriptedProvider([_final_response])
    resumed = _run(
        _loop(
            resumed_provider,
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        durable_states[-1],
        artifact_store,
        lambda state: None,
        resume=True,
    )

    assert resumed.status == "completed"
    assert executor_order == ["A", "B", "C"]
    assert len(resumed.state.model_executions[0].tool_result_refs) == 3


def test_pending_middle_member_blocks_resume_and_keeps_c_unstarted(
    artifact_store,
):
    executor_order: list[str] = []
    pending_states: list[RuntimeState] = []

    def stop_when_b_is_pending(state: RuntimeState) -> None:
        snapshot = RuntimeState.from_dict(state.to_dict())
        latest = snapshot.model_executions[-1]
        pending = [item for item in snapshot.tool_ledger if item.status == "pending"]
        if latest.status == "tool_results_partial" and pending:
            pending_states.append(snapshot)
            raise RuntimeError("synthetic pending ambiguity")

    _run(
        _loop(
            ScriptedProvider([_tool_response]),
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        _state(),
        artifact_store,
        stop_when_b_is_pending,
    )
    resumed_provider = ScriptedProvider([_final_response])
    resumed = _run(
        _loop(
            resumed_provider,
            lambda arguments: pytest.fail("pending resume must not execute a tool"),
        ),
        pending_states[-1],
        artifact_store,
        lambda state: None,
        resume=True,
    )

    assert resumed.status == "failed"
    assert resumed.error.code == "MODEL_RESUME_REQUIRES_VERIFICATION"
    assert executor_order == ["A"]
    assert resumed_provider.requests == []


def test_cold_resume_preserves_prior_complete_turns_before_resumed_proposal(
    artifact_store,
):
    executor_order: list[str] = []
    durable_states: list[RuntimeState] = []

    def crash_after_b_result(state: RuntimeState) -> None:
        snapshot = RuntimeState.from_dict(state.to_dict())
        latest = snapshot.model_executions[-1]
        if (
            latest.sequence == 2
            and latest.status == "tool_results_partial"
            and len(latest.tool_result_refs) == 1
        ):
            durable_states.append(snapshot)
            raise RuntimeError("synthetic crash after B")

    _run(
        _loop(
            ScriptedProvider(
                [_tool_response_for("A"), _tool_response_for("B", "C")]
            ),
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        _state(),
        artifact_store,
        crash_after_b_result,
    )

    def final(request: ModelTurnRequest) -> ModelResponse:
        assert [turn.source_sequence for turn in request.tool_turns] == [1, 2]
        assert [
            result.result.status
            for turn in request.tool_turns
            for result in turn.tool_results
        ] == ["ok", "ok", "ok"]
        return _final_response(request)

    resumed_provider = ScriptedProvider([final])
    resumed = _run(
        _loop(
            resumed_provider,
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        durable_states[-1],
        artifact_store,
        lambda _state: None,
        resume=True,
    )

    assert resumed.status == "completed"
    assert executor_order == ["A", "B", "C"]


def test_reused_provider_id_across_turns_keeps_run_wide_ledger_and_result_identity(
    artifact_store,
):
    executor_order: list[str] = []

    def repeated_provider_response(request: ModelTurnRequest) -> ModelResponse:
        proposal_id = f"{request.turn_id}:proposal"
        call = ModelToolCall.bind(
            provider_call_id="provider-reused-across-turns",
            proposal_id=proposal_id,
            ordinal=0,
            tool_id="search_notes",
            arguments={"query": f"turn-{request.sequence}"},
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            sequence=request.sequence,
        )
        return ModelResponse(
            proposal=ModelTurnProposal.tools(proposal_id, [call])
        )

    provider = ScriptedProvider(
        [repeated_provider_response, repeated_provider_response, _final_response]
    )
    result = _run(
        _loop(
            provider,
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        _state(),
        artifact_store,
        lambda _state: None,
    )

    assert result.status == "completed"
    assert executor_order == ["turn-1", "turn-2"]
    first_record, second_record = result.state.model_executions[:2]
    first_ref = first_record.tool_result_refs[0]
    second_ref = second_record.tool_result_refs[0]
    assert first_ref["provider_call_id"] == second_ref["provider_call_id"]
    assert first_ref["runtime_call_id"] != second_ref["runtime_call_id"]
    assert first_ref["result_id"] != second_ref["result_id"]
    assert [record.call_id for record in result.state.tool_ledger] == [
        first_ref["runtime_call_id"],
        second_ref["runtime_call_id"],
    ]


def test_cold_resume_rejects_tool_result_identity_mismatch_without_replay(
    artifact_store,
):
    durable_states: list[RuntimeState] = []

    def crash_after_a_result(state: RuntimeState) -> None:
        snapshot = RuntimeState.from_dict(state.to_dict())
        latest = snapshot.model_executions[-1]
        if (
            latest.status == "tool_results_partial"
            and len(latest.tool_result_refs) == 1
        ):
            durable_states.append(snapshot)
            raise RuntimeError("synthetic crash after A")

    _run(
        _loop(ScriptedProvider([_tool_response]), lambda _arguments: []),
        _state(),
        artifact_store,
        crash_after_a_result,
    )
    durable_state = durable_states[-1]
    record = durable_state.model_executions[-1]
    original_ref = record.tool_result_refs[0]
    payload = artifact_store.read(
        original_ref["result_ref"],
        expected_sha256=original_ref["result_sha256"],
    )
    payload["model_tool_result"]["provider_call_id"] = "provider-call-tampered"
    tampered_artifact = artifact_store.write(
        "tampered/result-identity.json",
        payload,
        kind="tool_result",
    )
    tampered_ref = {
        **original_ref,
        "provider_call_id": "provider-call-tampered",
        "result_ref": tampered_artifact.ref,
        "result_sha256": tampered_artifact.sha256,
    }
    tampered_record = replace(record, tool_result_refs=[tampered_ref])
    tampered_state = RuntimeState.from_dict(
        {
            **durable_state.to_dict(),
            "model_executions": [tampered_record.to_dict()],
        }
    )
    resumed_provider = ScriptedProvider([_final_response])

    resumed = _run(
        _loop(
            resumed_provider,
            lambda _arguments: pytest.fail("identity mismatch must not replay"),
        ),
        tampered_state,
        artifact_store,
        lambda _state: None,
        resume=True,
    )

    assert resumed.status == "failed"
    assert resumed.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert resumed_provider.requests == []


def test_cold_resume_rejects_result_payload_that_disagrees_with_ledger(
    artifact_store,
):
    durable_states: list[RuntimeState] = []

    def crash_after_a_result(state: RuntimeState) -> None:
        snapshot = RuntimeState.from_dict(state.to_dict())
        latest = snapshot.model_executions[-1]
        if (
            latest.status == "tool_results_partial"
            and len(latest.tool_result_refs) == 1
        ):
            durable_states.append(snapshot)
            raise RuntimeError("synthetic crash after A")

    _run(
        _loop(ScriptedProvider([_tool_response]), lambda _arguments: []),
        _state(),
        artifact_store,
        crash_after_a_result,
    )
    durable_state = durable_states[-1]
    record = durable_state.model_executions[-1]
    original_ref = record.tool_result_refs[0]
    payload = artifact_store.read(
        original_ref["result_ref"],
        expected_sha256=original_ref["result_sha256"],
    )
    payload["model_tool_result"]["result"]["value"] = [
        {"unexpected": "artifact-only-payload"}
    ]
    tampered_artifact = artifact_store.write(
        "tampered/result-payload.json",
        payload,
        kind="tool_result",
    )
    tampered_ref = {
        **original_ref,
        "result_ref": tampered_artifact.ref,
        "result_sha256": tampered_artifact.sha256,
    }
    tampered_record = replace(record, tool_result_refs=[tampered_ref])
    tampered_state = RuntimeState.from_dict(
        {
            **durable_state.to_dict(),
            "model_executions": [tampered_record.to_dict()],
        }
    )
    resumed_provider = ScriptedProvider([_final_response])

    resumed = _run(
        _loop(
            resumed_provider,
            lambda _arguments: pytest.fail("payload mismatch must not replay"),
        ),
        tampered_state,
        artifact_store,
        lambda _state: None,
        resume=True,
    )

    assert resumed.status == "failed"
    assert resumed.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert resumed_provider.requests == []


def test_v2_terminal_final_is_reusable_without_provider_reinvocation(
    artifact_store,
):
    executor_order: list[str] = []
    completed = _run(
        _loop(
            ScriptedProvider([_tool_response, _final_response]),
            lambda arguments: executor_order.append(arguments["query"]) or [],
        ),
        _state(),
        artifact_store,
        lambda _state: None,
    )
    assert completed.status == "completed"

    resumed_provider = ScriptedProvider([])
    resumed = _run(
        _loop(
            resumed_provider,
            lambda _arguments: pytest.fail("terminal V2 state must not replay"),
        ),
        completed.state,
        artifact_store,
        lambda _state: None,
        resume=True,
    )

    assert resumed.status == "completed"
    assert resumed.final_answer == "resumed complete"
    assert executor_order == ["A", "B", "C"]
    assert resumed_provider.requests == []
