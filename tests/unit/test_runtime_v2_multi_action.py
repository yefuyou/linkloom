from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import (
    FakeModelAdapter,
    ModelAction,
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
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult
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


@pytest.mark.parametrize('protocol', ['legacy', 'v2'])
def test_exhausted_plan_cannot_execute_tools_after_final_only_request(
    artifact_store, monkeypatch, protocol,
):
    from linkloom.runtime.progress import ProgressAssessment

    monkeypatch.setattr('linkloom.runtime.model_loop.assess_progress',
                        lambda *args, **kwargs: ProgressAssessment(
                            'SUSPECTED_STALL', 'SEARCH_NO_NEW_EVIDENCE',
                            exhausted_subgoals=('leader',)))
    executed = []

    def executor(arguments):
        executed.append(arguments['query'])
        return []

    if protocol == 'v2':
        provider = ScriptedProvider([
            lambda request: _tool_proposal(request, [{'query': 'unnecessary A'}, {'query': 'unnecessary B'}]),
        ])
    else:
        provider = FakeModelAdapter([
            ModelAction.tool(ToolCall('unnecessary', 'search_notes', {'query': 'unnecessary'})),
        ])
    result, _ = _run(provider, executor, artifact_store)
    assert executed == []
    assert result.state.tool_ledger == []
    assert result.status == 'failed'
    assert result.error.code == 'MODEL_PROGRESS_STALLED'
    assert result.final_answer is None
    assert len(provider.requests) == 1
    assert provider.requests[0].available_tools == []


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
    assert [item.error.code for item in result.tool_results[1:]] == [
        "TOOL_BUDGET_EXCEEDED", "TOOL_BUDGET_EXCEEDED",
    ]
    # Budget exhaustion permits a Final over prior observations, never more tools.
    assert [item.arguments["query"] for item in result.state.tool_ledger] == ["A"]
    assert len(result.state.model_executions[0].tool_result_refs) == 3


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
        ] == [
            [{"evidence_id": f"ev-{prefix}-{index}"}]
            for prefix in ("B", "C") for index in range(8)
        ]
        return _final_response(request, "bounded history observed")

    def executor(arguments):
        executor_order.append(arguments["query"])
        # This is a history-window probe, so each search must make progress.
        # The original all-empty fixture is preserved as a stop regression below.
        return [{"evidence_id": f"ev-{arguments['query']}"}]

    result, _ = _run(
        ScriptedProvider([proposal("A"), proposal("B"), proposal("C"), final]),
        executor,
        artifact_store,
        max_calls=24,
    )

    assert result.status == "completed"
    assert executor_order == [
        f"{prefix}-{index}" for prefix in ("A", "B", "C") for index in range(8)
    ]
    assert [item.arguments["query"] for item in result.state.tool_ledger] == executor_order
    refs = [ref for record in result.state.model_executions for ref in record.tool_result_refs]
    assert [ref["runtime_call_id"] for ref in refs] == [
        item.call_id for item in result.state.tool_ledger
    ]
    for ref, record in zip(refs, result.state.tool_ledger, strict=True):
        stored = artifact_store.read(ref["result_ref"], expected_sha256=ref["result_sha256"])
        assert stored["model_tool_result"]["result"] == record.result


def test_empty_search_history_stops_before_later_proposal_members(artifact_store):
    """The old empty history fixture must still trigger the progress guard."""
    executor_order: list[str] = []

    def proposal(prefix):
        return lambda request: _tool_proposal(
            request, [{"query": f"{prefix}-{index}"} for index in range(8)],
        )

    provider = ScriptedProvider([
        proposal("A"), proposal("B"), proposal("C"),
        lambda _request: pytest.fail("stalled searches must not produce a successful Final"),
    ])
    result, _ = _run(
        provider,
        lambda arguments: executor_order.append(arguments["query"]) or [],
        artifact_store,
        max_calls=24,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_PROGRESS_STALLED"
    assert result.final_answer is None
    assert result.state.termination.status == "failed"
    assert executor_order == [f"A-{index}" for index in range(8)]
    assert len(provider.requests) == 2
    assert [item.arguments["query"] for item in result.state.tool_ledger] == executor_order
    first, blocked = result.state.model_executions
    assert first.status == "tool_results_durable"
    assert [ref["runtime_call_id"] for ref in first.tool_result_refs] == [
        item.call_id for item in result.state.tool_ledger
    ]
    assert blocked.tool_result_refs == []


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


def test_policy_denial_is_durable_and_stops_later_call_without_final(
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
        pytest.fail("permission denial must terminate before another model call or Final")

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

    # The former expectation that C and a Final run after denial was obsolete.
    # The denied result is durable, but it grants no permission to continue.
    assert result.status == "failed"
    assert result.error.code == "MODEL_UNRECOVERABLE_TOOL_ERROR"
    assert result.error.details["reason_code"] == "TOOL_PERMISSION_DENIED"
    assert result.final_answer is None
    assert result.state.termination.status == "failed"
    assert executor_order == ["A"]
    assert len(provider.requests) == 1
    record, = result.state.model_executions
    assert [ref["ordinal"] for ref in record.tool_result_refs] == [0, 1]
    stored_results = [
        artifact_store.read(ref["result_ref"], expected_sha256=ref["result_sha256"])["model_tool_result"]
        for ref in record.tool_result_refs
    ]
    assert [item["result"]["status"] for item in stored_results] == ["ok", "error"]
    assert stored_results[1]["result"]["error"]["code"] == "TOOL_PERMISSION_DENIED"
    # Denial occurs before the executor/ledger pending boundary. C never starts.
    assert [item.call_id for item in result.state.tool_ledger] == [
        record.tool_result_refs[0]["runtime_call_id"],
    ]
    assert result.state.tool_ledger[0].result == stored_results[0]["result"]


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
