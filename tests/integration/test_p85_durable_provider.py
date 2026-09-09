"""M0.2 offline durable Provider-loop acceptance tests."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import (
    FakeModelAdapter,
    ModelAction,
    ModelProviderError,
    ModelResponse,
    ModelTurnRequest,
    ModelUsage,
)
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.runtime.models import (
    AgentTurn,
    ModelExecutionRecord,
    RuntimeState,
    SourceContext,
)
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


class ScriptedProvider:
    """Provider-neutral offline adapter that never executes local tools."""

    def __init__(
        self,
        script: list[ModelResponse | Callable[[ModelTurnRequest], ModelResponse]],
    ) -> None:
        self._script = list(script)
        self.requests: list[ModelTurnRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def complete(self, request: ModelTurnRequest) -> ModelResponse:
        index = len(self.requests)
        self.requests.append(request)
        response = self._script[index]
        return response(request) if callable(response) else response


class SequencedGeminiClient:
    """Offline client seam for proving existing Gemini request mapping."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []

    def generate_content(self, *, model: str, contents: list[dict], config: dict):
        self.requests.append(
            {"model": model, "contents": contents, "config": config}
        )
        return self._responses[len(self.requests) - 1]


@pytest.fixture
def artifact_root() -> Path:
    root = Path(".artifacts") / "m0-2-test-runs" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state(**changes) -> RuntimeState:
    values = {
        "schema_version": 1,
        "run_id": "run_m02_provider",
        "thread_id": "thread_m02_provider",
        "workflow": "ask",
        "status": "running",
        "step_seq": 1,
        "request_ref": "request_m02_provider.json",
        "source": SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="c" * 64,
            vault_root_fingerprint="fixture_m02_provider",
        ),
    }
    values.update(changes)
    return RuntimeState(**values)


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


def _runtime(executor: Callable[[dict], list]) -> ToolRuntime:
    registry = ToolRegistry()
    registry.register(_definition(), executor)
    return ToolRuntime(registry)


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


def _snapshot(state: RuntimeState) -> RuntimeState:
    return RuntimeState.from_dict(state.to_dict())


def _provider_tool_response(request: ModelTurnRequest) -> ModelResponse:
    return ModelResponse(
        action=ModelAction.tool(
            ToolCall(
                call_id="provider-call-search-1",
                tool_id="search_notes",
                arguments={"query": "durable provider"},
                run_id=request.run_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                sequence=request.sequence,
            )
        ),
        usage=ModelUsage(input_tokens=11, output_tokens=7, total_tokens=18),
        provider_request_id="provider-request-tool",
        provider_response_id="provider-response-tool",
        finish_reason="stop",
        provider_metadata={"provider": "offline-test", "model": "synthetic-v1"},
    )


def _provider_final_response(answer: str = "durable final") -> ModelResponse:
    return ModelResponse(
        action=ModelAction.final(answer),
        usage=ModelUsage(input_tokens=13, output_tokens=5, total_tokens=18),
        provider_request_id="provider-request-final",
        provider_response_id="provider-response-final",
        finish_reason="stop",
        provider_metadata={"provider": "offline-test", "model": "synthetic-v1"},
    )


def _provider_empty_optional_response(answer: str = "empty optional provider final") -> ModelResponse:
    return ModelResponse(action=ModelAction.final(answer))


def _loop(provider, executor=lambda arguments: []) -> tuple[
    SingleAgentModelLoop,
    ToolPolicyEnforcer,
]:
    policy = _policy()
    return SingleAgentModelLoop(provider, _runtime(executor), policy, max_steps=4), policy


def _run(
    loop: SingleAgentModelLoop,
    store: ModelArtifactStore,
    checkpoint_callback,
):
    return loop.run(
        state=_state(),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="find durable provider evidence",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=checkpoint_callback,
    )


def test_provider_tool_observation_previous_call_then_final_is_fully_durable(artifact_root):
    executor_calls: list[dict] = []
    snapshots: list[RuntimeState] = []

    def final_after_observation(request: ModelTurnRequest) -> ModelResponse:
        assert request.observation is not None
        assert request.previous_tool_call is not None
        assert request.previous_tool_call.call_id == request.observation.call_id
        assert request.previous_tool_call.tool_id == request.observation.tool_id
        assert request.previous_tool_call.arguments == {"query": "durable provider"}
        return _provider_final_response(
            f"observed:{request.observation.value[0]['evidence_id']}"
        )

    provider = ScriptedProvider([_provider_tool_response, final_after_observation])
    loop, policy = _loop(
        provider,
        lambda arguments: executor_calls.append(arguments)
        or [{"evidence_id": "ev_m02"}],
    )
    store = ModelArtifactStore(artifact_root / "models")

    result = _run(loop, store, lambda state: snapshots.append(_snapshot(state)))

    assert result.status == "completed"
    assert result.final_answer == "observed:ev_m02"
    assert provider.call_count == 2
    assert executor_calls == [{"query": "durable provider"}]
    assert policy.call_count == 1
    assert [record.status for record in result.state.model_executions] == [
        "tool_result_durable",
        "completed",
    ]
    first_record = result.state.model_executions[0]
    assert first_record.provider_request_id == "provider-request-tool"
    assert first_record.provider_response_id == "provider-response-tool"
    assert first_record.finish_reason == "stop"
    assert first_record.usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "duration_ms": None,
    }
    payload = store.read(
        first_record.response_ref,
        expected_sha256=first_record.response_sha256,
    )
    assert payload["model_response"]["action"]["kind"] == "tool_call"
    assert payload["model_response"]["provider_response_id"] == "provider-response-tool"
    first_response_checkpoint = next(
        state
        for state in snapshots
        if state.model_executions
        and state.model_executions[0].status == "response_durable"
    )
    assert first_response_checkpoint.tool_ledger == []


def test_provider_final_response_persists_usage_metadata_and_ids(artifact_root):
    provider = ScriptedProvider([_provider_final_response("direct final")])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")
    snapshots: list[RuntimeState] = []

    result = _run(loop, store, lambda state: snapshots.append(_snapshot(state)))

    assert result.status == "completed"
    assert result.final_answer == "direct final"
    assert provider.call_count == 1
    record = result.state.model_executions[-1]
    assert record.provider_request_id == "provider-request-final"
    assert record.provider_response_id == "provider-response-final"
    assert record.finish_reason == "stop"
    assert record.provider_metadata == {
        "provider": "offline-test",
        "model": "synthetic-v1",
    }
    assert result.state.turns[-1].usage == record.usage
    assert [
        snapshot.model_executions[-1].status
        for snapshot in snapshots
        if snapshot.model_executions
    ][:2] == ["request_durable", "request_sent"]


def test_provider_only_adapter_is_rejected_in_non_durable_loop(artifact_root):
    provider = ScriptedProvider([_provider_final_response()])
    loop, _ = _loop(provider)

    with pytest.raises(ValidationError, match="durable"):
        loop.run(
            state=_state(),
            task_id="task_m02_provider",
            agent_id="retrieval_agent",
            user_input="non durable provider",
            available_tools=[_definition()],
        )

    assert provider.call_count == 0


def test_missing_durable_checkpoint_callback_prevents_provider_invocation(artifact_root):
    provider = ScriptedProvider([_provider_final_response()])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")

    result = _run(loop, store, None)

    assert result.status == "failed"
    assert result.error.code == "MODEL_DURABLE_CHECKPOINT_REQUIRED"
    assert provider.call_count == 0
    assert not any(
        record.status == "request_sent" for record in result.state.model_executions
    )


@pytest.mark.parametrize("failed_status", ["request_durable", "request_sent"])
def test_failed_pre_provider_checkpoint_prevents_invocation(artifact_root, failed_status):
    provider = ScriptedProvider([_provider_final_response()])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")
    durable_snapshots: list[RuntimeState] = []

    def checkpoint(state: RuntimeState) -> None:
        status = state.model_executions[-1].status
        if status == failed_status:
            raise RuntimeError("checkpoint backend unavailable")
        durable_snapshots.append(_snapshot(state))

    result = _run(loop, store, checkpoint)

    assert result.status == "failed"
    assert result.error.code == "MODEL_CHECKPOINT_FAILED"
    assert result.error.details["checkpoint_status"] == failed_status
    assert provider.call_count == 0
    assert not any(
        snapshot.model_executions[-1].status == failed_status
        for snapshot in durable_snapshots
        if snapshot.model_executions
    )


def test_provider_error_is_durable_and_never_executes_tool(artifact_root):
    executor_calls: list[dict] = []
    provider_error = ModelProviderError(
        code="MODEL_RATE_LIMITED",
        category="rate_limit",
        message="Provider request was rate limited.",
        retryable=True,
        provider_request_id="provider-error-request",
        provider_metadata={"provider": "offline-test"},
        details={"reason": "rate_limit"},
    )
    provider = ScriptedProvider(
        [
            ModelResponse(
                error=provider_error,
                usage=ModelUsage(input_tokens=4, total_tokens=4),
                provider_response_id="provider-error-response",
                finish_reason="error",
                provider_metadata={"provider": "offline-test", "model": "synthetic-v1"},
            )
        ]
    )
    loop, _ = _loop(
        provider,
        lambda arguments: executor_calls.append(arguments) or [],
    )
    store = ModelArtifactStore(artifact_root / "models")
    snapshots: list[RuntimeState] = []

    result = _run(loop, store, lambda state: snapshots.append(_snapshot(state)))

    assert result.status == "failed"
    assert result.error.code == "MODEL_RATE_LIMITED"
    assert result.error.category == "provider"
    assert result.error.retryable is True
    assert provider.call_count == 1
    assert executor_calls == []
    record = result.state.model_executions[-1]
    assert record.provider_error == provider_error.to_dict()
    assert record.provider_response_id == "provider-error-response"
    payload = store.read(record.response_ref, expected_sha256=record.response_sha256)
    assert payload["model_response"]["error"] == provider_error.to_dict()
    assert any(
        snapshot.model_executions[-1].status == "response_durable"
        for snapshot in snapshots
    )


def _provider_response_durable_state(
    store: ModelArtifactStore,
) -> RuntimeState:
    turn_id = "run_m02_provider:turn:1"
    response = _provider_final_response("reused final")
    artifact = store.write_response(
        "run_m02_provider",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_m02_provider",
                "turn_id": turn_id,
                "task_id": "task_m02_provider",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "model_response": response.to_dict(),
            "normalized_action": response.action.to_dict(),
            "usage": response.usage.to_dict(),
            "provider_metadata": response.provider_metadata,
        },
    )
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id=turn_id,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        request_ref="model/run_m02_provider/turn_1/request.json",
        response_ref=artifact.ref,
        response_sha256=artifact.sha256,
        normalized_action=response.action.to_dict(),
        usage=response.usage.to_dict(),
        provider_metadata=response.provider_metadata,
        provider_request_id=response.provider_request_id,
        provider_response_id=response.provider_response_id,
        finish_reason=response.finish_reason,
        response_origin="provider",
    )
    return _state(
        turns=[
            AgentTurn(
                turn_id=turn_id,
                run_id="run_m02_provider",
                task_id="task_m02_provider",
                agent_id="retrieval_agent",
                sequence=1,
                status="running",
            )
        ],
        model_executions=[record],
    )


def test_response_durable_resume_reuses_full_response_without_provider_call(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    state = _provider_response_durable_state(store)
    provider = ScriptedProvider([_provider_final_response("must not run")])
    loop, _ = _loop(provider)
    snapshots: list[RuntimeState] = []

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="resume durable response",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: snapshots.append(_snapshot(current)),
    )

    assert result.status == "completed"
    assert result.final_answer == "reused final"
    assert provider.call_count == 0


@pytest.mark.parametrize("ambiguous_status", ["request_sent", "response_obtained"])
def test_ambiguous_provider_outcome_resume_never_reinvokes(artifact_root, ambiguous_status):
    store = ModelArtifactStore(artifact_root / "models")
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id="run_m02_provider:turn:1",
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status=ambiguous_status,
        request_ref="model/run_m02_provider/turn_1/request.json",
    )
    provider = ScriptedProvider([_provider_final_response("must not run")])
    loop, _ = _loop(provider)

    result = loop.resume(
        state=_state(model_executions=[record]),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="ambiguous provider outcome",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_RESUME_REQUIRES_VERIFICATION"
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "record_change",
    [
        {"response_ref": None, "response_sha256": None},
        {"response_sha256": None},
    ],
)
def test_provider_response_recovery_requires_artifact_ref_and_sha(artifact_root, record_change):
    store = ModelArtifactStore(artifact_root / "models")
    state = _provider_response_durable_state(store)
    original = state.model_executions[0]
    state = _state(
        turns=state.turns,
        model_executions=[replace(original, **record_change)],
    )
    provider = ScriptedProvider([_provider_final_response("must not run")])
    loop, _ = _loop(provider)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="missing canonical response artifact",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert provider.call_count == 0


def test_legacy_fake_model_remains_deterministic_without_durable_callback(artifact_root):
    fake = FakeModelAdapter([ModelAction.final("legacy deterministic final")])
    loop = SingleAgentModelLoop(fake, _runtime(lambda arguments: []), _policy())

    result = loop.run(
        state=_state(),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="legacy fake",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
    )

    assert result.status == "completed"
    assert result.final_answer == "legacy deterministic final"
    assert fake.call_count == 1


def test_tool_result_durable_resume_preserves_previous_call_without_reexecution(
    artifact_root,
):
    provider = ScriptedProvider([_provider_tool_response])
    executor_calls: list[dict] = []
    loop, _ = _loop(
        provider,
        lambda arguments: executor_calls.append(arguments)
        or [{"evidence_id": "ev_resumed"}],
    )
    store = ModelArtifactStore(artifact_root / "models")

    def stop_before_second_provider_turn(state: RuntimeState) -> None:
        latest = state.model_executions[-1]
        if latest.sequence == 2 and latest.status == "request_durable":
            raise RuntimeError("synthetic checkpoint stop")

    interrupted = _run(loop, store, stop_before_second_provider_turn)

    assert interrupted.status == "failed"
    assert interrupted.error.code == "MODEL_CHECKPOINT_FAILED"
    assert provider.call_count == 1
    assert executor_calls == [{"query": "durable provider"}]
    assert interrupted.state.model_executions[-1].status == "tool_result_durable"

    def final_from_resumed_observation(request: ModelTurnRequest) -> ModelResponse:
        assert request.observation is not None
        assert request.previous_tool_call is not None
        assert request.previous_tool_call.call_id == request.observation.call_id
        assert request.previous_tool_call.tool_id == request.observation.tool_id
        assert request.previous_tool_call.arguments == {"query": "durable provider"}
        return _provider_final_response("resumed final")

    resumed_provider = ScriptedProvider([final_from_resumed_observation])
    resumed_loop, resumed_policy = _loop(
        resumed_provider,
        lambda arguments: pytest.fail("terminal tool result must not re-execute"),
    )
    resumed = resumed_loop.resume(
        state=interrupted.state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="find durable provider evidence",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "completed"
    assert resumed.final_answer == "resumed final"
    assert provider.call_count == 1
    assert resumed_provider.call_count == 1
    assert executor_calls == [{"query": "durable provider"}]
    assert resumed_policy.call_count == 1


def test_existing_gemini_adapter_receives_function_response_from_runtime_loop(
    artifact_root,
):
    client = SequencedGeminiClient(
        [
            {
                "function_calls": [
                    {
                        "id": "gemini-loop-call",
                        "name": "search_notes",
                        "args": {"query": "gemini durable"},
                    }
                ],
                "response_id": "gemini-loop-tool-response",
                "finish_reason": "STOP",
            },
            {
                "text": "Gemini observed the LinkLoom tool result.",
                "response_id": "gemini-loop-final-response",
                "finish_reason": "STOP",
            },
        ]
    )
    adapter = GeminiProviderAdapter(client, model_id="gemini-test-model")
    executor_calls: list[dict] = []
    loop, policy = _loop(
        adapter,
        lambda arguments: executor_calls.append(arguments)
        or [{"evidence_id": "ev_gemini_loop"}],
    )

    result = _run(
        loop,
        ModelArtifactStore(artifact_root / "models"),
        lambda state: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "Gemini observed the LinkLoom tool result."
    assert len(client.requests) == 2
    assert executor_calls == [{"query": "gemini durable"}]
    assert policy.call_count == 1
    second_contents = client.requests[1]["contents"]
    assert second_contents[1]["parts"][0]["function_call"] == {
        "id": "gemini-loop-call",
        "name": "search_notes",
        "args": {"query": "gemini durable"},
    }
    assert second_contents[2]["parts"][0]["function_response"] == {
        "id": "gemini-loop-call",
        "name": "search_notes",
        "response": {
            "status": "ok",
            "value": [{"evidence_id": "ev_gemini_loop"}],
        },
    }
    assert "runtime_state" not in client.requests[1]
    assert "tool_ledger" not in client.requests[1]


def test_response_durable_checkpoint_failure_leaves_ambiguous_state_and_no_replay(
    artifact_root,
):
    provider = ScriptedProvider([_provider_final_response("orphan response")])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")

    def fail_response_commit(state: RuntimeState) -> None:
        if state.model_executions[-1].status == "response_durable":
            raise RuntimeError("response checkpoint unavailable")

    interrupted = _run(loop, store, fail_response_commit)

    assert interrupted.status == "failed"
    assert interrupted.error.code == "MODEL_CHECKPOINT_FAILED"
    assert interrupted.error.details["checkpoint_status"] == "response_durable"
    assert provider.call_count == 1
    assert interrupted.state.model_executions[-1].status == "response_obtained"
    assert interrupted.state.model_executions[-1].response_ref is None
    assert (
        store.root
        / "model/run_m02_provider/run_m02_provider_turn_1/response.json"
    ).exists()

    resumed_provider = ScriptedProvider([_provider_final_response("must not run")])
    resumed_loop, _ = _loop(resumed_provider)
    resumed = resumed_loop.resume(
        state=interrupted.state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="orphan response must not be inferred",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "failed"
    assert resumed.error.code == "MODEL_RESUME_REQUIRES_VERIFICATION"
    assert resumed_provider.call_count == 0


def test_durable_provider_error_reuses_artifact_without_retry(artifact_root):
    error = ModelProviderError(
        code="MODEL_TIMEOUT",
        category="timeout",
        message="Provider completion timed out.",
        outcome="unknown_provider_outcome",
        provider_request_id="provider-timeout-request",
        provider_metadata={"provider": "offline-test"},
    )
    provider = ScriptedProvider(
        [
            ModelResponse(
                error=error,
                usage=ModelUsage(input_tokens=3, total_tokens=3),
                provider_response_id="provider-timeout-response",
                finish_reason="error",
                provider_metadata={"provider": "offline-test"},
            )
        ]
    )
    loop, _ = _loop(provider)
    snapshots: list[RuntimeState] = []
    store = ModelArtifactStore(artifact_root / "models")

    first = _run(loop, store, lambda state: snapshots.append(_snapshot(state)))
    durable_error_state = next(
        state
        for state in snapshots
        if state.model_executions[-1].status == "response_durable"
    )

    assert first.status == "failed"
    assert first.error.code == "MODEL_TIMEOUT"
    assert first.error.details["outcome"] == "unknown_provider_outcome"
    assert provider.call_count == 1

    resumed_provider = ScriptedProvider([_provider_final_response("must not run")])
    resumed_loop, _ = _loop(resumed_provider)
    resumed = resumed_loop.resume(
        state=durable_error_state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="reuse durable provider error",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "failed"
    assert resumed.error.code == "MODEL_TIMEOUT"
    assert resumed.error.details["outcome"] == "unknown_provider_outcome"
    assert resumed_provider.call_count == 0


@pytest.mark.parametrize("provider_behavior", ["invalid_response", "exception"])
def test_provider_contract_violation_is_safe_and_never_retried(
    artifact_root,
    provider_behavior,
):
    if provider_behavior == "invalid_response":
        script = [object()]
    else:
        def raise_provider_error(request: ModelTurnRequest) -> ModelResponse:
            raise RuntimeError("api_key=must-not-be-persisted traceback")

        script = [raise_provider_error]
    provider = ScriptedProvider(script)  # type: ignore[arg-type]
    loop, _ = _loop(provider)

    result = _run(
        loop,
        ModelArtifactStore(artifact_root / "models"),
        lambda state: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_ADAPTER_FAILED"
    assert provider.call_count == 1
    durable_text = str(result.to_dict()).lower()
    assert "api_key" not in durable_text
    assert "traceback" not in durable_text


def test_durable_path_prefers_complete_when_adapter_exposes_both_methods(
    artifact_root,
):
    class HybridAdapter:
        def __init__(self) -> None:
            self.complete_calls = 0
            self.decide_calls = 0

        def complete(self, request: ModelTurnRequest) -> ModelResponse:
            self.complete_calls += 1
            return _provider_final_response("complete selected")

        def decide(self, request: ModelTurnRequest) -> ModelAction:
            self.decide_calls += 1
            return ModelAction.final("wrong legacy method")

    adapter = HybridAdapter()
    loop, _ = _loop(adapter)

    result = _run(
        loop,
        ModelArtifactStore(artifact_root / "models"),
        lambda state: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "complete selected"
    assert adapter.complete_calls == 1
    assert adapter.decide_calls == 0


def test_provider_response_projection_mismatch_fails_closed_without_reinvoke(
    artifact_root,
):
    store = ModelArtifactStore(artifact_root / "models")
    state = _provider_response_durable_state(store)
    record = replace(
        state.model_executions[0],
        provider_response_id="different-response-id",
    )
    provider = ScriptedProvider([_provider_final_response("must not run")])
    loop, _ = _loop(provider)

    result = loop.resume(
        state=_state(turns=state.turns, model_executions=[record]),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="projection mismatch",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert provider.call_count == 0


def test_provider_response_identity_mismatch_fails_closed_without_reinvoke(
    artifact_root,
):
    store = ModelArtifactStore(artifact_root / "models")
    response = _provider_final_response("wrong identity")
    turn_id = "run_m02_provider:turn:1"
    artifact = store.write_response(
        "run_m02_provider",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_m02_provider",
                "turn_id": turn_id,
                "task_id": "task_m02_provider",
                "agent_id": "retrieval_agent",
                "sequence": 2,
            },
            "model_response": response.to_dict(),
            "normalized_action": response.action.to_dict(),
            "usage": response.usage.to_dict(),
            "provider_metadata": response.provider_metadata,
        },
    )
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id=turn_id,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        response_ref=artifact.ref,
        response_sha256=artifact.sha256,
        normalized_action=response.action.to_dict(),
        usage=response.usage.to_dict(),
        provider_metadata=response.provider_metadata,
        provider_request_id=response.provider_request_id,
        provider_response_id=response.provider_response_id,
        finish_reason=response.finish_reason,
        response_origin="provider",
    )
    provider = ScriptedProvider([_provider_final_response("must not run")])
    loop, _ = _loop(provider)

    result = loop.resume(
        state=_state(
            turns=[
                AgentTurn(
                    turn_id=turn_id,
                    run_id="run_m02_provider",
                    task_id="task_m02_provider",
                    agent_id="retrieval_agent",
                    sequence=1,
                )
            ],
            model_executions=[record],
        ),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="identity mismatch",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert provider.call_count == 0


def test_provider_response_hash_tamper_fails_closed_without_reinvoke(
    artifact_root,
):
    store = ModelArtifactStore(artifact_root / "models")
    state = _provider_response_durable_state(store)
    record = state.model_executions[0]
    (store.root / record.response_ref).write_text("{}\n", encoding="utf-8")
    provider = ScriptedProvider([_provider_final_response("must not run")])
    loop, _ = _loop(provider)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="tampered response",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert provider.call_count == 0


def test_response_durable_tool_proposal_reuses_without_provider_replay(
    artifact_root,
):
    provider = ScriptedProvider([_provider_tool_response])
    loop, _ = _loop(
        provider,
        lambda arguments: pytest.fail("crash must precede ToolRuntime"),
    )
    store = ModelArtifactStore(artifact_root / "models")
    durable_snapshots: list[RuntimeState] = []

    def crash_after_response_commit(state: RuntimeState) -> None:
        if state.model_executions[-1].status == "response_durable":
            durable_snapshots.append(_snapshot(state))
            raise KeyboardInterrupt("synthetic process crash after persistence")

    with pytest.raises(KeyboardInterrupt, match="synthetic process crash"):
        _run(loop, store, crash_after_response_commit)

    assert provider.call_count == 1
    assert len(durable_snapshots) == 1
    assert durable_snapshots[0].tool_ledger == []

    resumed_provider = ScriptedProvider([_provider_final_response("after reused tool")])
    executor_calls: list[dict] = []
    resumed_loop, resumed_policy = _loop(
        resumed_provider,
        lambda arguments: executor_calls.append(arguments)
        or [{"evidence_id": "ev_reused_tool_response"}],
    )
    resumed = resumed_loop.resume(
        state=durable_snapshots[0],
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="find durable provider evidence",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "completed"
    assert resumed.final_answer == "after reused tool"
    assert resumed_provider.call_count == 1
    assert executor_calls == [{"query": "durable provider"}]
    assert resumed_policy.call_count == 1


def test_adapter_without_decide_or_complete_is_rejected():
    with pytest.raises(ValidationError, match=r"decide\(\) or complete\(\)"):
        SingleAgentModelLoop(
            object(),
            _runtime(lambda arguments: []),
            _policy(),
        )


def test_provider_optional_response_projection_remains_null(artifact_root):
    provider = ScriptedProvider([ModelResponse(action=ModelAction.final("minimal"))])
    loop, _ = _loop(provider)

    result = _run(
        loop,
        ModelArtifactStore(artifact_root / "models"),
        lambda state: None,
    )

    assert result.status == "completed"
    record = result.state.model_executions[-1]
    assert record.provider_request_id is None
    assert record.provider_response_id is None
    assert record.finish_reason is None
    assert record.provider_error is None


def test_provider_origin_missing_response_artifact_cannot_use_fake_legacy_fallback(
    artifact_root,
):
    store = ModelArtifactStore(artifact_root / "models")
    original = _provider_response_durable_state(store)
    provider_record = replace(
        original.model_executions[0],
        response_ref=None,
        response_sha256=None,
    )
    state = _state(turns=original.turns, model_executions=[provider_record])
    fake = FakeModelAdapter([ModelAction.final("must not recover inline provider action")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="provider artifact is missing",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert fake.call_count == 0
    assert "artifact" in result.error.message.lower()


def test_provider_origin_missing_response_sha_cannot_use_fake_legacy_fallback(
    artifact_root,
):
    store = ModelArtifactStore(artifact_root / "models")
    original = _provider_response_durable_state(store)
    provider_record = replace(original.model_executions[0], response_sha256=None)
    state = _state(turns=original.turns, model_executions=[provider_record])
    fake = FakeModelAdapter([ModelAction.final("must not recover without sha")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="provider response hash is missing",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert fake.call_count == 0
    assert "artifact" in result.error.message.lower()


def test_legacy_provider_neutral_fake_response_recovery_remains_compatible(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    turn_id = "run_m02_provider:turn:1"
    action = ModelAction.final("legacy durable answer")
    artifact = store.write_response(
        "run_m02_provider",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_m02_provider",
                "turn_id": turn_id,
                "task_id": "task_m02_provider",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {},
            "provider_metadata": {"adapter": "provider_neutral_fake"},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id=turn_id,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        response_ref=artifact.ref,
        response_sha256=artifact.sha256,
        normalized_action=action.to_dict(),
        provider_metadata={},
    )
    state = _state(
        turns=[
            AgentTurn(
                turn_id=turn_id,
                run_id="run_m02_provider",
                task_id="task_m02_provider",
                agent_id="retrieval_agent",
                sequence=1,
                status="running",
            )
        ],
        model_executions=[record],
    )
    fake = FakeModelAdapter([ModelAction.final("must not call legacy recovery")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="legacy durable response",
        available_tools=[_definition()],
        artifact_store=store,
    )

    assert result.status == "completed"
    assert result.final_answer == "legacy durable answer"
    assert fake.call_count == 0


def test_stale_provider_tool_call_sequence_is_rejected_before_tool_runtime(
    artifact_root,
):
    def stale_tool_response(request: ModelTurnRequest) -> ModelResponse:
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall(
                    call_id="stale-sequence-call",
                    tool_id="search_notes",
                    arguments={"query": "stale sequence"},
                    run_id=request.run_id,
                    task_id=request.task_id,
                    agent_id=request.agent_id,
                    sequence=99,
                )
            )
        )

    provider = ScriptedProvider([stale_tool_response])
    executor_calls: list[dict] = []
    loop, _ = _loop(
        provider,
        lambda arguments: executor_calls.append(arguments)
        or [{"evidence_id": "must_not_execute"}],
    )
    state = _state(
        turns=[
            AgentTurn(
                turn_id="run_m02_provider:turn:1",
                run_id="run_m02_provider",
                task_id="task_m02_provider",
                agent_id="retrieval_agent",
                sequence=1,
                status="completed",
            )
        ]
    )

    result = loop.run(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="reject stale provider tool call",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert executor_calls == []
    assert result.state.tool_ledger == []
    assert provider.call_count == 1
    assert "sequence" in result.error.message.lower()


def test_matching_provider_tool_call_sequence_executes_and_correlates(
    artifact_root,
):
    def matching_tool_response(request: ModelTurnRequest) -> ModelResponse:
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall(
                    call_id="matching-sequence-call",
                    tool_id="search_notes",
                    arguments={"query": "matching sequence"},
                    run_id=request.run_id,
                    task_id=request.task_id,
                    agent_id=request.agent_id,
                    sequence=request.sequence,
                )
            )
        )

    def final_after_matching_tool(request: ModelTurnRequest) -> ModelResponse:
        assert request.previous_tool_call is not None
        assert request.observation is not None
        assert request.previous_tool_call.sequence == 2
        assert request.previous_tool_call.call_id == request.observation.call_id
        return _provider_final_response("matching sequence final")

    provider = ScriptedProvider([matching_tool_response, final_after_matching_tool])
    executor_calls: list[dict] = []
    loop, policy = _loop(
        provider,
        lambda arguments: executor_calls.append(arguments)
        or [{"evidence_id": "ev_matching_sequence"}],
    )
    state = _state(
        turns=[
            AgentTurn(
                turn_id="run_m02_provider:turn:1",
                run_id="run_m02_provider",
                task_id="task_m02_provider",
                agent_id="retrieval_agent",
                sequence=1,
                status="completed",
            )
        ]
    )

    result = loop.run(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="accept matching provider tool call",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "matching sequence final"
    assert executor_calls == [{"query": "matching sequence"}]
    assert policy.call_count == 1
    assert result.state.tool_ledger[0].sequence == 2


def test_provider_origin_is_persisted_when_all_optional_fields_are_empty(artifact_root):
    provider = ScriptedProvider([_provider_empty_optional_response()])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")
    snapshots: list[RuntimeState] = []

    result = _run(loop, store, lambda state: snapshots.append(_snapshot(state)))

    assert result.status == "completed"
    record = result.state.model_executions[-1]
    assert record.response_origin == "provider"
    assert record.provider_request_id is None
    assert record.provider_response_id is None
    assert record.finish_reason is None
    assert record.provider_error is None
    assert record.provider_metadata == {}

    restored = ModelExecutionRecord.from_dict(
        json.loads(json.dumps(record.to_dict()))
    )
    assert restored.response_origin == "provider"
    assert any(
        snapshot.model_executions[-1].response_origin == "provider"
        for snapshot in snapshots
        if snapshot.model_executions
        and snapshot.model_executions[-1].status == "response_durable"
    )


@pytest.mark.parametrize(
    "record_change",
    [
        {"response_ref": None},
        {"response_sha256": None},
        {"response_ref": None, "response_sha256": None},
    ],
)
def test_provider_origin_empty_optional_fields_missing_artifact_fails_closed(
    artifact_root,
    record_change,
):
    provider = ScriptedProvider([_provider_empty_optional_response()])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")
    snapshots: list[RuntimeState] = []
    _run(loop, store, lambda state: snapshots.append(_snapshot(state)))
    durable = next(
        snapshot
        for snapshot in snapshots
        if snapshot.model_executions[-1].status == "response_durable"
    )
    record = durable.model_executions[-1]
    broken = replace(record, **record_change)
    fake = FakeModelAdapter([ModelAction.final("must not use legacy fallback")])
    resumed = SingleAgentModelLoop(fake, _runtime(lambda arguments: []), _policy()).resume(
        state=_state(turns=durable.turns, model_executions=[broken]),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="provider origin artifact recovery",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "failed"
    assert fake.call_count == 0
    assert "artifact" in resumed.error.message.lower()


def test_provider_origin_empty_optional_fields_recovers_full_response(artifact_root):
    provider = ScriptedProvider([_provider_empty_optional_response("durable empty fields")])
    loop, _ = _loop(provider)
    store = ModelArtifactStore(artifact_root / "models")
    snapshots: list[RuntimeState] = []
    _run(loop, store, lambda state: snapshots.append(_snapshot(state)))
    durable = next(
        snapshot
        for snapshot in snapshots
        if snapshot.model_executions[-1].status == "response_durable"
    )
    fake = FakeModelAdapter([ModelAction.final("must not invoke on recovery")])

    resumed = SingleAgentModelLoop(fake, _runtime(lambda arguments: []), _policy()).resume(
        state=durable,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="reuse provider response with empty optional fields",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "completed"
    assert resumed.final_answer == "durable empty fields"
    assert fake.call_count == 0


def test_originless_ambiguous_record_does_not_use_legacy_fallback(artifact_root):
    turn_id = "run_m02_provider:turn:1"
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id=turn_id,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        normalized_action=ModelAction.final("ambiguous inline answer").to_dict(),
    )
    fake = FakeModelAdapter([ModelAction.final("must not use ambiguous fallback")])

    result = SingleAgentModelLoop(fake, _runtime(lambda arguments: []), _policy()).resume(
        state=_state(
            turns=[
                AgentTurn(
                    turn_id=turn_id,
                    run_id="run_m02_provider",
                    task_id="task_m02_provider",
                    agent_id="retrieval_agent",
                    sequence=1,
                    status="running",
                )
            ],
            model_executions=[record],
        ),
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="ambiguous origin",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
        checkpoint_callback=lambda state: None,
    )

    assert result.status == "failed"
    assert fake.call_count == 0
    assert "origin" in result.error.message.lower()


def test_originless_provider_neutral_fake_artifact_recovers_historical_p84(
    artifact_root,
):
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
    )
    fake = FakeModelAdapter([ModelAction.final("must not reinvoke historical response")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="historical P8.4 artifact",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "originless artifact answer"
    assert fake.call_count == 0


def test_originless_historical_artifact_missing_sha_fails_closed(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
    )
    record = replace(state.model_executions[0], response_sha256=None)
    state = _state(turns=state.turns, model_executions=[record])
    fake = FakeModelAdapter([ModelAction.final("must not recover without sha")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="originless historical artifact without sha",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0


def test_explicit_legacy_artifact_missing_sha_fails_closed(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
    )
    record = replace(
        state.model_executions[0],
        response_origin="legacy",
        response_sha256=None,
    )
    state = _state(turns=state.turns, model_executions=[record])
    fake = FakeModelAdapter([ModelAction.final("must not recover legacy without sha")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="explicit legacy artifact without sha",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0


def test_historical_artifact_with_malformed_usage_fails_closed(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
        artifact_usage={"bogus": "not-a-model-usage-field"},
    )
    fake = FakeModelAdapter([ModelAction.final("must not mask malformed usage")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="historical artifact with malformed usage",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0


def test_historical_artifact_with_valid_model_usage_remains_compatible(artifact_root):
    usage = ModelUsage(
        input_tokens=8,
        output_tokens=3,
        total_tokens=11,
        duration_ms=4.5,
    ).to_dict()
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
        artifact_usage=usage,
        record_usage=usage,
    )
    fake = FakeModelAdapter([ModelAction.final("must not reinvoke valid history")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="historical artifact with canonical usage",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "originless artifact answer"
    assert result.state.model_executions[0].usage == usage
    assert fake.call_count == 0


def test_historical_artifact_usage_must_match_record_projection(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
        artifact_usage=ModelUsage(input_tokens=2, total_tokens=2).to_dict(),
    )
    fake = FakeModelAdapter([ModelAction.final("must not mask usage mismatch")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="historical artifact with mismatched usage projection",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"adapter": "fake"}, id="unproven-fake-adapter"),
        pytest.param({}, id="unproven-empty-metadata"),
    ],
)
def test_originless_artifact_without_historical_marker_fails_closed(
    artifact_root,
    metadata,
):
    store, state = _originless_artifact_state(artifact_root, metadata)
    fake = FakeModelAdapter([ModelAction.final("must not use unproven legacy fallback")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="unproven legacy marker",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0
    assert "origin" in result.error.message.lower()


def _originless_artifact_state(
    artifact_root: Path,
    artifact_provider_metadata: dict,
    *,
    artifact_usage: dict | None = None,
    record_usage: dict | None = None,
) -> tuple[ModelArtifactStore, RuntimeState]:
    turn_id = "run_m02_provider:turn:1"
    action = ModelAction.final("originless artifact answer")
    store = ModelArtifactStore(artifact_root / "models")
    artifact = store.write_response(
        "run_m02_provider",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_m02_provider",
                "turn_id": turn_id,
                "task_id": "task_m02_provider",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {} if artifact_usage is None else artifact_usage,
            "provider_metadata": artifact_provider_metadata,
        },
    )
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id=turn_id,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        response_ref=artifact.ref,
        response_sha256=artifact.sha256,
        normalized_action=action.to_dict(),
        usage={} if record_usage is None else record_usage,
        provider_metadata={},
    )
    return store, _state(
        turns=[
            AgentTurn(
                turn_id=turn_id,
                run_id="run_m02_provider",
                task_id="task_m02_provider",
                agent_id="retrieval_agent",
                sequence=1,
                status="running",
            )
        ],
        model_executions=[record],
    )


def _terminal_historical_artifact_state(
    artifact_root: Path,
    *,
    response_origin: str | None = None,
    include_response_sha: bool = True,
    artifact_usage: dict | None = None,
    record_usage: dict | None = None,
) -> tuple[ModelArtifactStore, RuntimeState, ToolCall, ToolResult]:
    turn_id = "run_m02_provider:turn:1"
    call = ToolCall(
        call_id="historical-terminal-call",
        tool_id="search_notes",
        arguments={"query": "historical terminal"},
        run_id="run_m02_provider",
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
    )
    action = ModelAction.tool(call)
    store = ModelArtifactStore(artifact_root / "models")
    artifact = store.write_response(
        "run_m02_provider",
        turn_id,
        {
            "runtime_identity": {
                "run_id": "run_m02_provider",
                "turn_id": turn_id,
                "task_id": "task_m02_provider",
                "agent_id": "retrieval_agent",
                "sequence": 1,
            },
            "normalized_action": action.to_dict(),
            "usage": {} if artifact_usage is None else artifact_usage,
            "provider_metadata": {"adapter": "provider_neutral_fake"},
        },
    )
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id=turn_id,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        response_ref=artifact.ref,
        response_sha256=artifact.sha256 if include_response_sha else None,
        normalized_action=action.to_dict(),
        usage={} if record_usage is None else record_usage,
        provider_metadata={},
        response_origin=response_origin,
    )
    result = ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "historical-terminal-evidence"}],
    )
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    ledger.record_completed(call, result)
    state = _state(
        turns=[
            AgentTurn(
                turn_id=turn_id,
                run_id="run_m02_provider",
                task_id="task_m02_provider",
                agent_id="retrieval_agent",
                sequence=1,
                status="running",
            )
        ],
        model_executions=[record],
        tool_ledger=ledger.to_list(),
    )
    return store, state, call, result


def test_terminal_originless_historical_response_missing_sha_fails_closed(
    artifact_root,
):
    store, state, _, _ = _terminal_historical_artifact_state(
        artifact_root,
        include_response_sha=False,
    )
    model = FakeModelAdapter([ModelAction.final("must not continue")])
    executor_calls: list[dict] = []
    loop, _ = _loop(model, lambda arguments: executor_calls.append(arguments) or [])

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="terminal historical response without sha",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0
    assert executor_calls == []


def test_terminal_explicit_legacy_response_missing_sha_fails_closed(artifact_root):
    store, state, _, _ = _terminal_historical_artifact_state(
        artifact_root,
        response_origin="legacy",
        include_response_sha=False,
    )
    model = FakeModelAdapter([ModelAction.final("must not continue")])
    executor_calls: list[dict] = []
    loop, _ = _loop(model, lambda arguments: executor_calls.append(arguments) or [])

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="terminal explicit legacy response without sha",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0
    assert executor_calls == []


def test_terminal_historical_response_malformed_usage_fails_closed(artifact_root):
    store, state, _, _ = _terminal_historical_artifact_state(
        artifact_root,
        artifact_usage={"bogus": "not-a-model-usage-field"},
    )
    model = FakeModelAdapter([ModelAction.final("must not continue")])
    executor_calls: list[dict] = []
    loop, _ = _loop(model, lambda arguments: executor_calls.append(arguments) or [])

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="terminal historical response with malformed usage",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0
    assert executor_calls == []


def test_terminal_historical_response_usage_mismatch_fails_closed(artifact_root):
    store, state, _, _ = _terminal_historical_artifact_state(
        artifact_root,
        artifact_usage=ModelUsage(input_tokens=3, total_tokens=3).to_dict(),
    )
    model = FakeModelAdapter([ModelAction.final("must not continue")])
    executor_calls: list[dict] = []
    loop, _ = _loop(model, lambda arguments: executor_calls.append(arguments) or [])

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="terminal historical response with usage mismatch",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.final_answer is None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0
    assert executor_calls == []


def test_valid_terminal_historical_response_reuses_result_after_validation(
    artifact_root,
):
    store, state, expected_call, expected_result = _terminal_historical_artifact_state(
        artifact_root,
    )
    executor_calls: list[dict] = []

    def final_after_validated_history(request: ModelTurnRequest) -> ModelAction:
        assert request.previous_tool_call == expected_call
        assert request.observation == expected_result
        return ModelAction.final("validated historical terminal resume")

    model = FakeModelAdapter([final_after_validated_history])
    loop, policy = _loop(
        model,
        lambda arguments: executor_calls.append(arguments) or [],
    )

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="valid terminal historical response",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "validated historical terminal resume"
    assert model.call_count == 1
    assert executor_calls == []
    assert policy.call_count == 1


def test_originless_artifact_with_provider_metadata_fails_closed(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"provider": "gemini", "model": "gemini-3.1-pro"},
    )
    fake = FakeModelAdapter([ModelAction.final("must not recover provider-shaped artifact")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="provider-shaped originless artifact",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0
    assert "origin" in result.error.message.lower()


def test_originless_artifact_with_unknown_metadata_fails_closed(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"source": "unknown"},
    )
    fake = FakeModelAdapter([ModelAction.final("must not recover unknown artifact")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="unknown originless artifact",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "failed"
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert fake.call_count == 0
    assert "origin" in result.error.message.lower()


def test_explicit_legacy_origin_recovers_legacy_artifact(artifact_root):
    store, state = _originless_artifact_state(
        artifact_root,
        {"adapter": "provider_neutral_fake"},
    )
    record = state.model_executions[0]
    state = _state(
        turns=state.turns,
        model_executions=[replace(record, response_origin="legacy")],
    )
    fake = FakeModelAdapter([ModelAction.final("must not invoke explicit legacy recovery")])
    loop, _ = _loop(fake)

    result = loop.resume(
        state=state,
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        user_input="explicit legacy artifact",
        available_tools=[_definition()],
        artifact_store=store,
        checkpoint_callback=lambda current: None,
    )

    assert result.status == "completed"
    assert result.final_answer == "originless artifact answer"
    assert fake.call_count == 0


@pytest.mark.parametrize("response_origin", ["provider", "legacy"])
def test_model_execution_record_response_origin_roundtrips_and_old_payload_defaults(
    response_origin,
):
    record = ModelExecutionRecord(
        run_id="run_m02_provider",
        turn_id="run_m02_provider:turn:1",
        task_id="task_m02_provider",
        agent_id="retrieval_agent",
        sequence=1,
        status="response_durable",
        normalized_action=ModelAction.final("origin roundtrip").to_dict(),
        response_origin=response_origin,
    )

    restored = ModelExecutionRecord.from_dict(
        json.loads(json.dumps(record.to_dict()))
    )
    assert restored == record
    assert restored.response_origin == response_origin

    old_payload = record.to_dict()
    old_payload.pop("response_origin")
    old_record = ModelExecutionRecord.from_dict(old_payload)
    assert old_record.response_origin is None


@pytest.mark.parametrize("invalid_origin", ["unknown", "", 1, True])
def test_model_execution_record_rejects_invalid_response_origin(invalid_origin):
    with pytest.raises(ValidationError, match="response_origin"):
        ModelExecutionRecord(
            run_id="run_m02_provider",
            turn_id="run_m02_provider:turn:1",
            task_id="task_m02_provider",
            agent_id="retrieval_agent",
            sequence=1,
            status="response_durable",
            response_origin=invalid_origin,
        )
