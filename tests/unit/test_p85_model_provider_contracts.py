"""P8.5 WP-0/WP-1 provider-neutral model contract tests."""

from __future__ import annotations

import json

import pytest

from linkloom.agents.model_adapter import (
    FakeModelAdapter,
    ModelAction,
    ModelGenerationOptions,
    ModelProviderError,
    ModelResponse,
    ModelTurnRequest,
    ModelUsage,
    ProviderCapability,
)
from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult


def _definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search verified notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _call(call_id: str = "call_p85_1") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments={"query": "durability"},
        run_id="run_p85_1",
        task_id="task_p85_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _observation(call: ToolCall | None = None) -> ToolResult:
    call = call or _call()
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value={"matches": [{"evidence_id": "ev_p85_1"}]},
        business_status="FOUND",
    )


def _request(**changes) -> ModelTurnRequest:
    values = {
        "run_id": "run_p85_1",
        "turn_id": "run_p85_1:turn:1",
        "task_id": "task_p85_1",
        "agent_id": "retrieval_agent",
        "sequence": 1,
        "user_input": "find durable runtime evidence",
        "observation": None,
        "available_tools": [_definition()],
    }
    values.update(changes)
    return ModelTurnRequest(**values)


@pytest.mark.parametrize(
    "action",
    [
        ModelAction.tool(_call()),
        ModelAction.final("The answer is grounded in the retrieved evidence."),
    ],
)
def test_model_response_roundtrips_one_normalized_action_with_usage_and_metadata(action):
    response = ModelResponse(
        action=action,
        usage=ModelUsage(
            input_tokens=17,
            output_tokens=9,
            total_tokens=26,
            duration_ms=12.5,
        ),
        provider_request_id="provider_request_p85_1",
        provider_response_id="provider_response_p85_1",
        finish_reason="tool_call" if action.kind == "tool_call" else "stop",
        provider_metadata={"provider": "fake", "model": "synthetic"},
    )

    payload = json.loads(json.dumps(response.to_dict(), sort_keys=True))
    restored = ModelResponse.from_dict(payload)

    assert restored == response
    assert restored.action == action
    assert restored.usage.total_tokens == 26
    assert restored.provider_request_id == "provider_request_p85_1"
    assert restored.provider_response_id == "provider_response_p85_1"
    assert "hidden_reasoning" not in payload
    assert "api_key" not in payload


def test_model_response_error_envelope_roundtrips_without_an_action():
    error = ModelProviderError(
        code="MODEL_RATE_LIMITED",
        category="rate_limit",
        message="The model provider rate limit was reached.",
        retryable=True,
        outcome="known_failure",
        provider_request_id="provider_request_p85_rate",
        provider_metadata={"http_status": 429},
        details={"retry_after_seconds": 2},
    )
    response = ModelResponse(
        error=error,
        provider_response_id="provider_response_p85_rate",
        finish_reason="error",
        provider_metadata={"provider": "fake"},
    )

    restored = ModelResponse.from_dict(
        json.loads(json.dumps(response.to_dict(), sort_keys=True))
    )

    assert restored == response
    assert restored.action is None
    assert restored.error.code == "MODEL_RATE_LIMITED"
    assert restored.error.provider_request_id == "provider_request_p85_rate"


def test_model_response_rejects_ambiguous_or_empty_response():
    error = ModelProviderError(
        code="MODEL_RESPONSE_MALFORMED",
        category="malformed_response",
        message="The provider response was malformed.",
    )

    with pytest.raises(ValidationError):
        ModelResponse(action=ModelAction.final("answer"), error=error)

    with pytest.raises(ValidationError):
        ModelResponse()


def test_model_response_rejects_non_json_metadata_and_hidden_reasoning_fields():
    with pytest.raises(ValidationError):
        ModelResponse(
            action=ModelAction.final("answer"),
            provider_metadata={"non_json": object()},
        )

    with pytest.raises(ValidationError):
        ModelResponse(
            action=ModelAction.final("answer"),
            provider_metadata={"hidden_reasoning": "must not persist"},
        )

    with pytest.raises(ValidationError):
        ModelResponse(
            action=ModelAction.final("answer"),
            provider_metadata={"traceback": "Traceback (most recent call last)"},
        )

    with pytest.raises(ValidationError):
        ModelResponse.from_dict(
            {
                "action": ModelAction.final("answer").to_dict(),
                "hidden_reasoning": "must not be accepted",
            }
        )


@pytest.mark.parametrize(
    ("code", "category", "retryable", "outcome"),
    [
        ("MODEL_AUTH_REQUIRED", "authentication", False, "known_failure"),
        ("MODEL_INVALID_REQUEST", "invalid_request", False, "known_failure"),
        ("MODEL_RATE_LIMITED", "rate_limit", True, "known_failure"),
        ("MODEL_TIMEOUT", "timeout", False, "unknown_provider_outcome"),
        ("MODEL_TRANSIENT_FAILURE", "transient", True, "known_failure"),
        ("MODEL_UNAVAILABLE", "unavailable_model", False, "known_failure"),
        ("MODEL_RESPONSE_MALFORMED", "malformed_response", False, "known_failure"),
        ("MODEL_TOOL_CALL_PARSE_FAILED", "tool_call_parse", False, "known_failure"),
        ("MODEL_RESPONSE_UNSUPPORTED", "unsupported_shape", False, "known_failure"),
        ("MODEL_CANCELLED", "cancelled", False, "known_failure"),
    ],
)
def test_model_provider_error_taxonomy_is_normalized(
    code, category, retryable, outcome
):
    error = ModelProviderError(
        code=code,
        category=category,
        message="The provider operation failed safely.",
        retryable=retryable,
        outcome=outcome,
        provider_request_id="provider_request_p85_error",
        provider_metadata={"provider": "fake"},
    )

    assert error.to_dict()["code"] == code
    assert error.to_dict()["category"] == category
    assert error.retryable is retryable
    assert error.outcome == outcome


def test_model_provider_error_rejects_mismatched_or_unsafe_fields():
    with pytest.raises(ValidationError):
        ModelProviderError(
            code="MODEL_RATE_LIMITED",
            category="timeout",
            message="The provider operation failed safely.",
        )

    with pytest.raises(ValidationError):
        ModelProviderError(
            code="MODEL_INVALID_REQUEST",
            category="invalid_request",
            message="api_key=raw-secret-value",
        )

    with pytest.raises(ValidationError):
        ModelProviderError(
            code="MODEL_INVALID_REQUEST",
            category="invalid_request",
            message="secret=raw-secret-value",
        )

    with pytest.raises(ValidationError):
        ModelProviderError(
            code="MODEL_INVALID_REQUEST",
            category="invalid_request",
            message="Traceback (most recent call last): private failure",
        )

    with pytest.raises(ValidationError):
        ModelProviderError(
            code="MODEL_INVALID_REQUEST",
            category="invalid_request",
            message="The provider operation failed safely.",
            provider_metadata={"api_token": "secret"},
        )

    with pytest.raises(ValidationError):
        ModelProviderError(
            code="MODEL_INVALID_REQUEST",
            category="invalid_request",
            message="The provider operation failed safely.",
            details={"chain_of_thought": "private reasoning"},
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": True},
        {"output_tokens": -1},
        {"total_tokens": float("nan")},
        {"duration_ms": float("inf")},
    ],
)
def test_model_usage_rejects_invalid_numeric_fields(kwargs):
    with pytest.raises(ValidationError):
        ModelUsage(**kwargs)


def test_provider_capability_defaults_keep_tool_execution_with_linkloom_runtime():
    capability = ProviderCapability()
    restored = ProviderCapability.from_dict(
        json.loads(json.dumps(capability.to_dict(), sort_keys=True))
    )

    assert restored == capability
    assert capability.supports_tool_calls is True
    assert capability.provider_executes_tools is False

    with pytest.raises(ValidationError):
        ProviderCapability(provider_executes_tools=True)


def test_model_turn_request_previous_tool_call_roundtrips_and_legacy_defaults_work():
    call = _call()
    request = _request(
        observation=_observation(call),
        previous_tool_call=call,
        model_id="configured-model",
        generation_options=ModelGenerationOptions(
            max_output_tokens=128,
            temperature=0.2,
        ),
    )

    restored = ModelTurnRequest.from_dict(
        json.loads(json.dumps(request.to_dict(), sort_keys=True))
    )

    assert restored == request
    assert restored.previous_tool_call == call
    assert restored.generation_options.temperature == 0.2

    legacy = _request()
    assert legacy.previous_tool_call is None
    assert legacy.model_id is None
    assert legacy.generation_options == ModelGenerationOptions()
    assert ModelTurnRequest.from_dict(legacy.to_dict()) == legacy
    assert _request(generation_options={"temperature": 0.1}).generation_options == (
        ModelGenerationOptions(temperature=0.1)
    )


def test_model_turn_request_rejects_mismatched_previous_tool_call_identity():
    observation_call = _call("call_p85_observation")
    previous_call = _call("call_p85_previous")

    with pytest.raises(ValidationError):
        _request(
            observation=_observation(observation_call),
            previous_tool_call=previous_call,
        )

    with pytest.raises(ValidationError):
        _request(previous_tool_call=previous_call)


def test_fake_model_adapter_still_returns_model_action_without_provider_types():
    adapter = FakeModelAdapter([ModelAction.final("legacy fake answer")])
    request = _request()

    action = adapter.decide(request)

    assert isinstance(action, ModelAction)
    assert action == ModelAction.final("legacy fake answer")
    assert adapter.call_count == 1
    assert adapter.requests == [request]
