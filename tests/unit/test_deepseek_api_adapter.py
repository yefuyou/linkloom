"""Offline contract tests for the DeepSeek Chat Completions adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from linkloom.agents.model_adapter import (
    ModelAction,
    ModelGenerationOptions,
    ModelTurnRequest,
    ModelUsage,
)
from linkloom.agents.providers.deepseek_api import (
    DeepSeekProviderAdapter,
    build_deepseek_request,
    map_tool_definition_to_deepseek_tool,
)
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult


@dataclass
class FakeDeepSeekClient:
    response: object | None = None
    exception: BaseException | None = None

    def __post_init__(self) -> None:
        self.requests: list[dict] = []

    def create_chat_completion(self, **request):
        self.requests.append(request)
        if self.exception is not None:
            raise self.exception
        return self.response


class FakeDeepSeekError(Exception):
    def __init__(self, *, status_code: int | None = None) -> None:
        super().__init__("raw provider error must not persist")
        self.status_code = status_code


def _definition(tool_id: str = "search_notes") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1",
        description=f"Use {tool_id} on verified notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _call() -> ToolCall:
    return ToolCall(
        call_id="call_deepseek_1",
        tool_id="search_notes",
        arguments={"query": "durable DeepSeek tools"},
        run_id="run_deepseek_1",
        task_id="task_deepseek_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _observation(call: ToolCall | None = None) -> ToolResult:
    call = call or _call()
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_deepseek_1", "quote": "verified"}],
        business_status="FOUND",
    )


def _request(**changes) -> ModelTurnRequest:
    values = {
        "run_id": "run_deepseek_1",
        "turn_id": "run_deepseek_1:turn:1",
        "task_id": "task_deepseek_1",
        "agent_id": "retrieval_agent",
        "sequence": 1,
        "user_input": "Return JSON after inspecting verified evidence.",
        "observation": None,
        "available_tools": [_definition()],
        "model_id": "deepseek-flash",
        "generation_options": ModelGenerationOptions(
            max_output_tokens=512,
            temperature=0.1,
        ),
    }
    values.update(changes)
    return ModelTurnRequest(**values)


def _response_message(
    *,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-deepseek-1",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": 17,
            "completion_tokens": 9,
            "total_tokens": 26,
            "prompt_cache_hit_tokens": 3,
            "prompt_cache_miss_tokens": 14,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _tool_call(*, arguments: str = '{"query":"durable DeepSeek tools"}') -> dict:
    return {
        "id": "call_deepseek_1",
        "type": "function",
        "function": {
            "name": "search_notes",
            "arguments": arguments,
        },
    }


def test_initial_request_uses_current_model_non_thinking_json_and_tools():
    payload = build_deepseek_request(
        _request(),
        system_instruction="Use tools and return one JSON object.",
        json_output=True,
    )

    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.1
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
    ]
    assert payload["messages"][0]["content"] == "Use tools and return one JSON object."
    assert payload["messages"][1]["content"] == _request().user_input
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == [
        map_tool_definition_to_deepseek_tool(_definition())
    ]
    assert payload["tools"][0]["function"]["parameters"] == _definition().input_schema
    assert "strict" not in payload["tools"][0]["function"]


def test_tool_call_response_maps_id_name_and_json_arguments_without_execution():
    client = FakeDeepSeekClient(
        response=_response_message(
            tool_calls=[_tool_call()],
            finish_reason="tool_calls",
        )
    )

    response = DeepSeekProviderAdapter(client).complete(_request())

    assert response.action == ModelAction.tool(_call())
    assert response.provider_continuation is None
    assert response.finish_reason == "tool_calls"
    assert response.usage == ModelUsage(
        input_tokens=17,
        output_tokens=9,
        total_tokens=26,
        duration_ms=response.usage.duration_ms,
    )
    assert response.provider_metadata["prompt_cache_hit_tokens"] == 3
    assert response.provider_metadata["prompt_cache_miss_tokens"] == 14
    assert response.provider_metadata["reasoning_tokens"] == 0
    assert len(client.requests) == 1


def test_tool_result_reconstructs_official_assistant_then_tool_message_pair():
    call = _call()
    payload = build_deepseek_request(
        _request(
            sequence=2,
            turn_id="run_deepseek_1:turn:2",
            previous_tool_call=call,
            observation=_observation(call),
        ),
        system_instruction="Use tools and return JSON.",
        json_output=True,
    )

    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assistant = payload["messages"][2]
    assert assistant == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.tool_id,
                    "arguments": json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }
        ],
    }
    tool_message = payload["messages"][3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == call.call_id
    assert json.loads(tool_message["content"]) == {
        "business_status": "FOUND",
        "status": "ok",
        "value": [{"evidence_id": "ev_deepseek_1", "quote": "verified"}],
    }


def test_final_json_maps_to_final_action_and_never_persists_reasoning_content():
    final = '{"schema_version":"team-decision-result/v1"}'
    raw = _response_message(content=final)
    raw["choices"][0]["message"]["reasoning_content"] = None
    client = FakeDeepSeekClient(response=raw)

    response = DeepSeekProviderAdapter(client, json_output=True).complete(_request())

    assert response.action == ModelAction.final(final)
    assert "reasoning_content" not in json.dumps(response.to_dict())


@pytest.mark.parametrize(
    ("raw", "code", "reason"),
    [
        (
            _response_message(
                tool_calls=[_tool_call(arguments="not-json")],
                finish_reason="tool_calls",
            ),
            "MODEL_TOOL_CALL_PARSE_FAILED",
            "function_arguments_not_json",
        ),
        (
            _response_message(
                tool_calls=[
                    {
                        **_tool_call(),
                        "function": {
                            "name": "undeclared_tool",
                            "arguments": "{}",
                        },
                    }
                ],
                finish_reason="tool_calls",
            ),
            "MODEL_RESPONSE_UNSUPPORTED",
            "undeclared_tool",
        ),
        (
            _response_message(content=None),
            "MODEL_RESPONSE_MALFORMED",
            "empty_action",
        ),
    ],
)
def test_malformed_or_undeclared_provider_actions_fail_closed(raw, code, reason):
    response = DeepSeekProviderAdapter(FakeDeepSeekClient(response=raw)).complete(
        _request()
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.details["reason"] == reason


def test_nonempty_reasoning_content_is_rejected_in_non_thinking_adapter():
    raw = _response_message(content="{}")
    raw["choices"][0]["message"]["reasoning_content"] = "hidden"

    response = DeepSeekProviderAdapter(FakeDeepSeekClient(response=raw)).complete(
        _request()
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_UNSUPPORTED"
    assert response.error.details["reason"] == "unexpected_reasoning_content"
    assert "hidden" not in json.dumps(response.to_dict())


def test_nonzero_reasoning_usage_is_rejected_in_non_thinking_adapter():
    raw = _response_message(content="{}")
    raw["usage"]["completion_tokens_details"]["reasoning_tokens"] = 1

    response = DeepSeekProviderAdapter(FakeDeepSeekClient(response=raw)).complete(
        _request()
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_UNSUPPORTED"
    assert response.error.details["reason"] == "unexpected_reasoning_usage"


@pytest.mark.parametrize(
    ("finish_reason", "code", "reason"),
    [
        ("aborted", "MODEL_CANCELLED", "provider_aborted"),
        (
            "insufficient_system_resource",
            "MODEL_TRANSIENT_FAILURE",
            "insufficient_system_resource",
        ),
    ],
)
def test_official_terminal_failure_reasons_map_to_provider_errors(
    finish_reason, code, reason
):
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(
            response=_response_message(content=None, finish_reason=finish_reason)
        )
    ).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.details["reason"] == reason


@pytest.mark.parametrize(
    ("exception", "code", "outcome"),
    [
        (FakeDeepSeekError(status_code=400), "MODEL_INVALID_REQUEST", "known_failure"),
        (FakeDeepSeekError(status_code=401), "MODEL_AUTH_REQUIRED", "known_failure"),
        (FakeDeepSeekError(status_code=402), "MODEL_UNAVAILABLE", "known_failure"),
        (FakeDeepSeekError(status_code=422), "MODEL_INVALID_REQUEST", "known_failure"),
        (FakeDeepSeekError(status_code=429), "MODEL_RATE_LIMITED", "known_failure"),
        (TimeoutError("secret timeout detail"), "MODEL_TIMEOUT", "unknown_provider_outcome"),
        (FakeDeepSeekError(status_code=503), "MODEL_TRANSIENT_FAILURE", "known_failure"),
    ],
)
def test_provider_errors_are_normalized_once_without_raw_error_text(
    exception, code, outcome
):
    client = FakeDeepSeekClient(exception=exception)

    response = DeepSeekProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.outcome == outcome
    assert response.error.message != str(exception)
    assert len(client.requests) == 1


def test_observation_without_previous_call_fails_before_provider_invocation():
    client = FakeDeepSeekClient(response=_response_message(content="{}"))

    response = DeepSeekProviderAdapter(client).complete(
        _request(observation=_observation())
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_INVALID_REQUEST"
    assert client.requests == []


def test_request_and_durable_response_contain_no_credential_or_deepseek_state_blob():
    client = FakeDeepSeekClient(response=_response_message(content="{}"))

    response = DeepSeekProviderAdapter(client, json_output=True).complete(_request())

    encoded_request = json.dumps(client.requests[0], ensure_ascii=False).lower()
    encoded_response = json.dumps(response.to_dict(), ensure_ascii=False).lower()
    assert "api_key" not in encoded_request + encoded_response
    assert "authorization" not in encoded_request + encoded_response
    assert "reasoning_content" not in encoded_request + encoded_response
    assert "provider_continuation" not in encoded_response
    assert response.provider_continuation is None
