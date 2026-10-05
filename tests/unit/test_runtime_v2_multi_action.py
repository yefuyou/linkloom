from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import (
    ModelProviderError,
    ModelResponse,
    ModelToolCall,
    ModelTurnProposal,
    ModelTurnRequest,
    runtime_call_id_for,
)
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.tools.contracts import ToolDefinition, ToolResult
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


RUN_ID = "run_runtime_v2_multi"
TASK_ID = "task_runtime_v2_multi"
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
    root = Path(".artifacts") / "runtime-v2-test-runs" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return ModelArtifactStore(root / "models")


def _state() -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id=RUN_ID,
        thread_id="thread_runtime_v2_multi",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request_runtime_v2_multi.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="e" * 64,
            vault_root_fingerprint="fixture_runtime_v2_multi",
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


def _policy(max_calls: int = 8) -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id=AGENT_ID,
            allowed_tool_ids=["search_notes"],
            denied_tool_ids=[],
            max_calls=max_calls,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _tool_proposal(
    request: ModelTurnRequest,
    arguments: list[dict],
) -> ModelResponse:
    proposal_id = f"{request.turn_id}:proposal"
    calls = [
        ModelToolCall.bind(
            provider_call_id=f"provider-call-{ordinal}",
            proposal_id=proposal_id,
            ordinal=ordinal,
            tool_id="search_notes",
            arguments=item,
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            sequence=request.sequence,
        )
        for ordinal, item in enumerate(arguments)
    ]
    return ModelResponse(proposal=ModelTurnProposal.tools(proposal_id, calls))


def _final_response(request: ModelTurnRequest, answer: str) -> ModelResponse:
    return ModelResponse(
        proposal=ModelTurnProposal.final(
            f"{request.turn_id}:proposal",
            answer,
        )
    )


def _run(provider, executor, artifact_store, *, max_calls: int = 8):
    registry = ToolRegistry()
    registry.register(_definition(), executor)
    snapshots = []
    result = SingleAgentModelLoop(
        provider,
        ToolRuntime(registry),
        _policy(max_calls=max_calls),
        max_steps=4,
    ).run(
        state=_state(),
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        user_input="exercise one multi-action turn",
        available_tools=[_definition()],
        artifact_store=artifact_store,
        checkpoint_callback=lambda state: snapshots.append(
            RuntimeState.from_dict(state.to_dict())
        ),
    )
    return result, snapshots


def test_three_calls_execute_strictly_in_order_and_form_one_durable_turn(
    artifact_store,
):
    executor_order: list[str] = []
    executing = False

    def executor(arguments):
        nonlocal executing
        assert executing is False
        executing = True
        executor_order.append(arguments["query"])
        executing = False
        return []

    def final(request: ModelTurnRequest) -> ModelResponse:
        assert len(request.tool_turns) == 1
        assert [
            result.result.status
            for result in request.tool_turns[0].tool_results
        ] == ["ok", "ok", "ok"]
        return _final_response(request, "multi-action complete")

    provider = ScriptedProvider(
        [
            lambda request: _tool_proposal(
                request,
                [{"query": "A"}, {"query": "B"}, {"query": "C"}],
            ),
            final,
        ]
    )

    result, snapshots = _run(provider, executor, artifact_store)

    assert result.status == "completed"
    assert result.final_answer == "multi-action complete"
    assert executor_order == ["A", "B", "C"]
    assert [item.status for item in result.tool_results] == ["ok", "ok", "ok"]
    first_record = result.state.model_executions[0]
    assert first_record.status == "tool_results_durable"
    assert first_record.proposal_id == f"{RUN_ID}:turn:1:proposal"
    assert len(first_record.tool_result_refs) == 3
    assert result.state.turns[0].tool_call_ids == [
        item["runtime_call_id"] for item in first_record.tool_result_refs
    ]
    assert any(
        record.status == "tool_results_partial"
        for snapshot in snapshots
        for record in snapshot.model_executions
    )


def test_invalid_middle_call_is_durable_and_does_not_suppress_later_call(
    artifact_store,
):
    executor_order: list[str] = []

    def final(request: ModelTurnRequest) -> ModelResponse:
        results = request.tool_turns[0].tool_results
        assert [item.result.status for item in results] == ["ok", "error", "ok"]
        assert results[1].result.error.code == "TOOL_INVALID_ARGUMENTS"
        return _final_response(request, "failure observed")

    provider = ScriptedProvider(
        [
            lambda request: _tool_proposal(
                request,
                [{"query": "A"}, {}, {"query": "C"}],
            ),
            final,
        ]
    )

    result, _ = _run(
        provider,
        lambda arguments: executor_order.append(arguments["query"]) or [],
        artifact_store,
    )

    assert result.status == "completed"
    assert executor_order == ["A", "C"]
    assert [item.status for item in result.tool_results] == ["ok", "error", "ok"]


def test_middle_business_not_found_is_observed_and_c_still_runs(artifact_store):
    executor_order: list[str] = []
    middle_call_id = runtime_call_id_for(
        run_id=RUN_ID,
        proposal_id=f"{RUN_ID}:turn:1:proposal",
        ordinal=1,
        provider_call_id="provider-call-1",
    )

    def executor(arguments):
        executor_order.append(arguments["query"])
        if arguments["query"] == "B":
            return ToolResult(
                call_id=middle_call_id,
                tool_id="search_notes",
                status="ok",
                value=[],
                business_status="NOT_FOUND",
            )
        return []

    def final(request: ModelTurnRequest) -> ModelResponse:
        results = request.tool_turns[0].tool_results
        assert results[1].result.business_status == "NOT_FOUND"
        return _final_response(request, "business failure observed")

    provider = ScriptedProvider(
        [
            lambda request: _tool_proposal(
                request,
                [{"query": "A"}, {"query": "B"}, {"query": "C"}],
            ),
            final,
        ]
    )

    result, _ = _run(provider, executor, artifact_store)

    assert result.status == "completed"
    assert executor_order == ["A", "B", "C"]
    assert result.tool_results[1].business_status == "NOT_FOUND"


def test_middle_terminal_tool_error_is_durable_and_c_still_runs(artifact_store):
    executor_order: list[str] = []

    def executor(arguments):
        executor_order.append(arguments["query"])
        if arguments["query"] == "B":
            raise RuntimeError("synthetic executor failure")
        return []

    def final(request: ModelTurnRequest) -> ModelResponse:
        results = request.tool_turns[0].tool_results
        assert results[1].result.error.code == "TOOL_EXECUTION_FAILED"
        return _final_response(request, "tool error observed")

    provider = ScriptedProvider(
        [
            lambda request: _tool_proposal(
                request,
                [{"query": "A"}, {"query": "B"}, {"query": "C"}],
            ),
            final,
        ]
    )

    result, _ = _run(provider, executor, artifact_store)

    assert result.status == "completed"
    assert executor_order == ["A", "B", "C"]
    assert result.tool_results[1].status == "error"


def test_budget_exhaustion_mid_proposal_never_invokes_over_budget_members(
    artifact_store,
):
    executor_order: list[str] = []

    def final(request: ModelTurnRequest) -> ModelResponse:
        results = request.tool_turns[0].tool_results
        assert [item.result.status for item in results] == ["ok", "error", "error"]
        assert [item.result.error.code for item in results[1:]] == [
            "TOOL_BUDGET_EXCEEDED",
            "TOOL_BUDGET_EXCEEDED",
        ]
        return _final_response(request, "budget failures observed")

    provider = ScriptedProvider(
        [
            lambda request: _tool_proposal(
                request,
                [{"query": "A"}, {"query": "B"}, {"query": "C"}],
            ),
            final,
        ]
    )

    result, _ = _run(
        provider,
        lambda arguments: executor_order.append(arguments["query"]) or [],
        artifact_store,
        max_calls=1,
    )

    assert result.status == "completed"
    assert executor_order == ["A"]


def test_runtime_keeps_only_the_newest_whole_tool_turns_within_history_limit(
    artifact_store,
):
    executor_order: list[str] = []

    def proposal(prefix: str):
        return lambda request: _tool_proposal(
            request,
            [{"query": f"{prefix}-{index}"} for index in range(8)],
        )

    def final(request: ModelTurnRequest) -> ModelResponse:
        assert [turn.source_sequence for turn in request.tool_turns] == [2, 3]
        assert [
            result.result.value
            for turn in request.tool_turns
            for result in turn.tool_results
        ] == [[] for _ in range(16)]
        return _final_response(request, "bounded history observed")

    result, _ = _run(
        ScriptedProvider([proposal("A"), proposal("B"), proposal("C"), final]),
        lambda arguments: executor_order.append(arguments["query"]) or [],
        artifact_store,
        max_calls=24,
    )

    assert result.status == "completed"
    assert len(executor_order) == 24


def test_tool_result_artifact_helper_has_explicit_kind_and_stable_path(artifact_store):
    stored = artifact_store.write_tool_result(
        RUN_ID,
        f"{RUN_ID}:turn:1",
        0,
        "rtc_test_result",
        {"model_tool_result": {"result_id": "tr_test_result"}},
    )

    assert stored.kind == "tool_result"
    assert stored.ref.endswith("/tool-results/00-rtc_test_result.json")
    assert artifact_store.read(stored.ref) == {
        "model_tool_result": {"result_id": "tr_test_result"}
    }


def test_policy_denial_is_one_ordered_result_and_later_call_still_runs(
    artifact_store,
):
    read_definition = ToolDefinition(
        tool_id="read_verified_note",
        version="1",
        description="Read a synthetic note.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )
    search_definition = _definition()
    registry = ToolRegistry()
    executor_order: list[str] = []
    registry.register(
        search_definition,
        lambda arguments: executor_order.append(arguments["query"]) or [],
    )
    registry.register(
        read_definition,
        lambda _arguments: pytest.fail("denied executor must not run"),
    )
    policy = ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id=AGENT_ID,
            allowed_tool_ids=["search_notes"],
            denied_tool_ids=["read_verified_note"],
            max_calls=8,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )

    def tools(request: ModelTurnRequest) -> ModelResponse:
        proposal_id = f"{request.turn_id}:proposal"
        calls = [
            ModelToolCall.bind(
                provider_call_id=f"provider-call-{ordinal}",
                proposal_id=proposal_id,
                ordinal=ordinal,
                tool_id=tool_id,
                arguments={"query": query},
                run_id=request.run_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                sequence=request.sequence,
            )
            for ordinal, (tool_id, query) in enumerate(
                (
                    ("search_notes", "A"),
                    ("read_verified_note", "B"),
                    ("search_notes", "C"),
                )
            )
        ]
        return ModelResponse(proposal=ModelTurnProposal.tools(proposal_id, calls))

    def final(request: ModelTurnRequest) -> ModelResponse:
        results = request.tool_turns[0].tool_results
        assert [result.result.status for result in results] == ["ok", "error", "ok"]
        assert results[1].result.error.code == "TOOL_PERMISSION_DENIED"
        return _final_response(request, "denial observed")

    provider = ScriptedProvider([tools, final])
    result = SingleAgentModelLoop(
        provider,
        ToolRuntime(registry),
        policy,
        max_steps=4,
    ).run(
        state=_state(),
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        user_input="exercise per-member policy",
        available_tools=[search_definition, read_definition],
        artifact_store=artifact_store,
        checkpoint_callback=lambda _state: None,
    )

    assert result.status == "completed"
    assert executor_order == ["A", "C"]


def test_malformed_provider_response_executes_zero_proposal_members(artifact_store):
    executor_calls: list[str] = []
    malformed = ModelResponse(
        error=ModelProviderError(
            code="MODEL_RESPONSE_MALFORMED",
            category="malformed_response",
            message="Synthetic malformed response.",
            retryable=False,
            details={"reason": "one_member_invalid"},
        )
    )

    result, _ = _run(
        ScriptedProvider([malformed]),
        lambda arguments: executor_calls.append(arguments["query"]) or [],
        artifact_store,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_RESPONSE_MALFORMED"
    assert executor_calls == []
