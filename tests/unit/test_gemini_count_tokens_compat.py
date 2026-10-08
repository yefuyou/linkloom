from __future__ import annotations

from dataclasses import replace
import importlib
import inspect
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.semantic_ingestion.extraction import _provider_tool


HARNESS_MODULE = "tests.smoke.test_m12_real_provider_team_decision_smoke"


def _harness():
    return importlib.import_module(HARNESS_MODULE)


def _tool() -> dict[str, object]:
    return {
        "function_declarations": [
            {
                "name": "search_notes",
                "parameters_json_schema": {
                    "type": "OBJECT",
                    "properties": {"query": {"type": "STRING"}},
                },
            }
        ]
    }


def _contents(*texts: str) -> list[dict[str, object]]:
    return [
        {"role": "user", "parts": [{"text": text}]}
        if index % 2 == 0
        else {"role": "model", "parts": [{"text": text}]}
        for index, text in enumerate(texts)
    ]


@pytest.mark.parametrize(
    ("contents", "config", "has_system", "has_tools", "has_generation"),
    [
        (_contents("synthetic prompt"), {}, False, False, False),
        (_contents("synthetic prompt"), {"system_instruction": "synthetic system"}, True, False, False),
        (_contents("synthetic prompt"), {"tools": [_tool()]}, False, True, False),
        (
            _contents("synthetic prompt"),
            {"system_instruction": "synthetic system", "tools": [_tool()]},
            True,
            True,
            False,
        ),
        (
            _contents("first user turn", "model continuation", "second user turn"),
            {
                "system_instruction": "synthetic system",
                "tools": [_tool()],
                "temperature": 0.0,
                "max_output_tokens": 512,
            },
            True,
            True,
            True,
        ),
    ],
    ids=("contents-only", "system-instruction", "tools", "system-and-tools", "multi-turn"),
)
def test_count_tokens_request_has_complete_generation_content_shape(
    contents: list[dict[str, object]],
    config: dict[str, object],
    has_system: bool,
    has_tools: bool,
    has_generation: bool,
) -> None:
    harness = _harness()
    request = {
        "model": harness.MODEL,
        "contents": contents,
        "config": {
            **config,
            "automatic_function_calling": {"disable": True},
        },
    }

    serialized = harness._count_endpoint_generate_request(request)

    assert serialized["model"] == f"models/{harness.MODEL}"
    assert serialized["contents"] == contents
    assert "generateContentRequest" not in serialized
    if has_system:
        assert serialized["systemInstruction"] == {
            "parts": [{"text": "synthetic system"}],
            "role": "user",
        }
    else:
        assert "systemInstruction" not in serialized
    if has_tools:
        assert serialized["tools"][0]["functionDeclarations"][0]["name"] == "search_notes"
        assert "parametersJsonSchema" in serialized["tools"][0]["functionDeclarations"][0]
    else:
        assert "tools" not in serialized
    if has_generation:
        assert serialized["generationConfig"] == {
            "temperature": 0.0,
            "maxOutputTokens": 512,
        }
    else:
        assert "generationConfig" not in serialized


def test_count_tokens_request_preserves_required_function_call_mode():
    harness = _harness()
    request = {
        "model": harness.MODEL,
        "contents": _contents("synthetic planner prompt"),
        "config": {
            "tools": [_tool()],
            "tool_config": {"function_calling_config": {"mode": "ANY"}},
            "automatic_function_calling": {"disable": True},
        },
    }

    serialized = harness._count_endpoint_generate_request(request)

    assert serialized["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY"}
    }


def test_count_tokens_nullable_parameters_match_sdk_schema_enum_serialization() -> None:
    pytest.importorskip("google.genai")
    from google.genai import _common, models, types

    harness = _harness()
    request = {
        "model": harness.MODEL,
        "contents": _contents("synthetic semantic extraction prompt"),
        "config": {
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": "emit_semantic_candidates",
                            "description": "Return structured synthetic claims.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "subject": {
                                        "type": "string",
                                        "nullable": True,
                                        "maxLength": 512,
                                    }
                                },
                                "required": ["subject"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                }
            ],
            "automatic_function_calling": {"disable": True},
        },
    }

    generation = types._GenerateContentParameters.model_validate(request)
    sdk_shape = models._GenerateContentParameters_to_mldev(
        SimpleNamespace(vertexai=False),
        generation.model_dump(exclude_none=True),
    )
    sdk_shape = _common.convert_to_dict(sdk_shape)
    sdk_shape = json.loads(
        json.dumps(
            sdk_shape,
            default=lambda value: value.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
        )
    )
    count_shape = harness._count_endpoint_generate_request(request)

    count_schema = count_shape["tools"][0]["functionDeclarations"][0]["parameters"]
    sdk_schema = sdk_shape["tools"][0]["functionDeclarations"][0]["parameters"]
    assert count_schema["type"] == sdk_schema["type"]
    assert (
        count_schema["properties"]["subject"]["type"]
        == sdk_schema["properties"]["subject"]["type"]
    )
    assert count_schema["properties"]["subject"]["nullable"] is True


def test_count_tokens_nullable_json_schema_matches_sdk_wire_payload() -> None:
    pytest.importorskip("google.genai")
    from google.genai import _common, models, types

    harness = _harness()
    nullable_schema = {
        "type": "object",
        "properties": {
            "subject": {
                "anyOf": [
                    {"type": "string", "maxLength": 512},
                    {"type": "null"},
                ]
            }
        },
        "required": ["subject"],
        "additionalProperties": False,
    }
    request = {
        "model": harness.MODEL,
        "contents": _contents("synthetic semantic extraction prompt"),
        "config": {
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": "emit_semantic_candidates",
                            "description": "Return structured synthetic claims.",
                            "parameters_json_schema": nullable_schema,
                        }
                    ]
                }
            ],
            "automatic_function_calling": {"disable": True},
        },
    }

    generation = types._GenerateContentParameters.model_validate(request)
    sdk_shape = models._GenerateContentParameters_to_mldev(
        SimpleNamespace(vertexai=False),
        generation.model_dump(exclude_none=True),
    )
    sdk_shape = _common.convert_to_dict(sdk_shape)
    sdk_shape = json.loads(
        json.dumps(
            sdk_shape,
            default=lambda value: value.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
        )
    )
    count_shape = harness._count_endpoint_generate_request(request)

    assert (
        count_shape["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]
        == sdk_shape["tools"][0]["functionDeclarations"][0]["parameters_json_schema"]
    )


def test_count_tokens_and_generation_use_equivalent_system_instruction_shape() -> None:
    google_genai = pytest.importorskip("google.genai")
    from google.genai import _common, models, types

    harness = _harness()
    request = {
        "model": harness.MODEL,
        "contents": _contents("synthetic prompt"),
        "config": {
            "system_instruction": "synthetic system",
            "tools": [_tool()],
            "temperature": 0.0,
            "max_output_tokens": 512,
            "automatic_function_calling": {"disable": True},
        },
    }
    generation = types._GenerateContentParameters.model_validate(request)
    generation_shape = models._GenerateContentParameters_to_mldev(
        SimpleNamespace(vertexai=False),
        generation.model_dump(exclude_none=True),
    )
    generation_shape = _common.convert_to_dict(generation_shape)
    count_shape = harness._count_endpoint_generate_request(request)

    assert google_genai.__version__ == harness.SDK_VERSION
    assert generation_shape["_url"]["model"] == count_shape["model"]
    assert generation_shape["contents"] == count_shape["contents"]
    assert generation_shape["systemInstruction"] == count_shape["systemInstruction"]
    assert generation_shape["generationConfig"] == count_shape["generationConfig"]
    generation_tool = generation_shape["tools"][0]["functionDeclarations"][0]
    count_tool = count_shape["tools"][0]["functionDeclarations"][0]
    assert generation_tool["name"] == count_tool["name"]


def test_fake_count_tokens_transport_receives_content_shaped_system_instruction() -> None:
    harness = _harness()
    seen: dict[str, object] = {}

    class FakeResponse:
        @staticmethod
        def read() -> bytes:
            return b'{"totalTokens":5}'

        @staticmethod
        def close() -> None:
            return None

    def fake_urlopen(request, timeout):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["endpoint"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    response = harness._raw_developer_count_tokens(
        "synthetic-api-key",
        model=harness.MODEL,
        request={
            "model": harness.MODEL,
            "contents": _contents("synthetic prompt"),
            "config": {
                "system_instruction": "synthetic system",
                "temperature": 0.0,
                "max_output_tokens": 512,
                "automatic_function_calling": {"disable": True},
            },
        },
        urlopen=fake_urlopen,
    )

    payload = seen["body"]
    nested = payload["generateContentRequest"]
    assert response["totalTokens"] == 5
    assert set(payload) == {"generateContentRequest"}
    assert nested["model"] == f"models/{harness.MODEL}"
    assert nested["contents"] == _contents("synthetic prompt")
    assert nested["systemInstruction"] == {
        "parts": [{"text": "synthetic system"}],
        "role": "user",
    }
    assert nested["generationConfig"] == {
        "temperature": 0.0,
        "maxOutputTokens": 512,
    }
    assert "synthetic-api-key" not in repr(payload)


def test_installed_count_tokens_config_is_not_misrepresented_as_developer_support() -> None:
    pytest.importorskip("google.genai")
    from google.genai import models, types

    fields = set(types.CountTokensConfig.model_fields)
    assert {"http_options", "system_instruction", "tools", "generation_config"} <= fields
    count_config_annotation = str(inspect.signature(models.Models.count_tokens).parameters["config"].annotation)
    generation_config_annotation = str(inspect.signature(models.Models.generate_content).parameters["config"].annotation)
    assert "CountTokensConfig" in count_config_annotation
    assert "GenerateContentConfig" in generation_config_annotation
    config = types.CountTokensConfig(
        system_instruction="synthetic system",
        tools=[_tool()],
        generation_config={"temperature": 0.0},
        http_options=types.HttpOptions(timeout=1_000),
    )

    with pytest.raises(ValueError, match="system_instruction.*Developer API"):
        models._CountTokensConfig_to_mldev(config.model_dump(exclude_none=True))


def test_count_request_diagnostics_exclude_prompt_secret_and_tool_names() -> None:
    harness = _harness()
    secret_prompt = "synthetic-sensitive-prompt-marker"
    request = {
        "model": harness.MODEL,
        "contents": _contents(secret_prompt),
        "config": {
            "system_instruction": "synthetic-sensitive-system-marker",
            "tools": [_tool()],
        },
    }

    metadata = harness.count_request_metadata(request)
    encoded = repr(metadata)

    assert metadata["request_shape"]["has_system_instruction"] is True
    assert metadata["request_shape"]["has_tools"] is True
    assert secret_prompt not in encoded
    assert "synthetic-sensitive-system-marker" not in encoded
    assert "search_notes" not in encoded


def test_persistable_count_tokens_diagnostic_drops_all_provider_message_text() -> None:
    harness = _harness()
    private_text = "private synthetic prompt/context marker"

    diagnostic = harness._persistable_count_tokens_failure_diagnostic(
        {
            "exception_class": "HTTPError",
            "http_status": 429,
            "provider_error_code": "RESOURCE_EXHAUSTED",
            "message": private_text,
        }
    )

    assert diagnostic["message"] == harness.COUNT_TOKENS_FAILURE_GENERIC_MESSAGE
    assert private_text not in json.dumps(diagnostic)


def _shape_failure(harness):
    return harness.CountTokensFailure(
        {
            "exception_class": "HTTPError",
            "http_status": 400,
            "provider_error_code": "INVALID_ARGUMENT",
            "message": "field generate_content_request.system_instruction expects Content",
        }
    )


def _successful_synthetic_response():
    return {
        "text": "synthetic response",
        "usage_metadata": {
            "prompt_token_count": 5,
            "candidates_token_count": 3,
            "total_token_count": 8,
        },
    }


def _new_guard(
    harness,
    delegate,
    *,
    aggregate=None,
    state=None,
    budget=None,
    transport_fallback_only=False,
    on_response_accepted=None,
):
    return harness.BudgetedGeminiClient(
        delegate,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=harness.MPS_001_RERUN_CASE_BUDGET,
        aggregate=aggregate or harness.AggregateUsage(),
        aggregate_budget=budget or harness.MPS_001_RERUN_AGGREGATE_BUDGET,
        allow_static_count_tokens_fallback=True,
        count_tokens_fallback_state=state,
        count_tokens_transport_fallback_only=transport_fallback_only,
        on_response_accepted=on_response_accepted,
    )


def test_response_fingerprint_and_usage_are_durable_before_usage_guard(
    tmp_path, monkeypatch
) -> None:
    harness = _harness()
    import scripts.run_public_memory_clean_eval as runner

    evidence_path = tmp_path / "accepted_response_evidence.jsonl"
    response = {
        "text": "synthetic private output",
        "usage_metadata": {
            "prompt_token_count": 5,
            "candidates_token_count": 3,
            "thoughts_token_count": 0,
        },
    }
    durable_evidence = []

    def persist_accepted(response_object):
        durable_evidence.append(
            runner._persist_accepted_response_evidence(
                evidence_path,
                run_id="synthetic-run",
                sequence=1,
                logical_case_id="synthetic-case",
                method="Flat Retrieval",
                attempt_no=1,
                logical_attempt_id="1:1",
                model=harness.MODEL,
                request_hash="a" * 64,
                response=response_object,
            )
        )

    delegate = harness.RecordingDelegate(response=response)
    guard = _new_guard(harness, delegate, on_response_accepted=persist_accepted)

    def usage_guard(_response):
        assert evidence_path.is_file()
        assert durable_evidence[0]["response_sha256"]
        assert durable_evidence[0]["usage_metadata"] == {
            "input_tokens": 5,
            "output_tokens": 3,
            "thinking_tokens": 0,
        }
        raise harness.SmokeBlocked("PROVIDER_USAGE_UNAVAILABLE")

    monkeypatch.setattr(harness, "_reported_usage", usage_guard)
    with pytest.raises(harness.SmokeBlocked, match="PROVIDER_USAGE_UNAVAILABLE"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic prompt"),
            config={
                "max_output_tokens": 512,
                "automatic_function_calling": {"disable": True},
            },
        )

    assert len(delegate.calls) == 1
    assert durable_evidence[0]["provider_response_accepted"] is True


def test_response_usage_is_durable_before_cost_guard(tmp_path, monkeypatch) -> None:
    harness = _harness()
    import scripts.run_public_memory_clean_eval as runner

    evidence_path = tmp_path / "accepted_response_evidence.jsonl"
    response = _successful_synthetic_response()
    durable_evidence = []

    def persist_accepted(response_object):
        durable_evidence.append(
            runner._persist_accepted_response_evidence(
                evidence_path,
                run_id="synthetic-run",
                sequence=2,
                logical_case_id="synthetic-case-2",
                method="Flat Retrieval",
                attempt_no=1,
                logical_attempt_id="2:1",
                model=harness.MODEL,
                request_hash="c" * 64,
                response=response_object,
            )
        )

    delegate = harness.RecordingDelegate(response=response)
    guard = _new_guard(harness, delegate, on_response_accepted=persist_accepted)

    def high_reported_cost(_response):
        assert durable_evidence[0]["usage_present"] is True
        assert evidence_path.is_file()
        return 5, 3, 0, 1.0

    monkeypatch.setattr(harness, "_reported_usage", high_reported_cost)
    with pytest.raises(harness.SmokeBlocked, match="REPORTED_COST_BUDGET_EXCEEDED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic prompt"),
            config={
                "max_output_tokens": 512,
                "automatic_function_calling": {"disable": True},
            },
        )

    assert len(delegate.calls) == 1
    assert durable_evidence[0]["provider_response_accepted"] is True
    assert guard.last_guard_state == "REPORTED_COST_BUDGET_EXCEEDED"


def test_response_acceptance_persistence_failure_stops_before_downstream_guards(monkeypatch) -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(response=_successful_synthetic_response())

    def fail_persistence(_response):
        raise RuntimeError("synthetic local persistence failure")

    guard = _new_guard(harness, delegate, on_response_accepted=fail_persistence)
    monkeypatch.setattr(
        harness,
        "_reported_usage",
        lambda _response: pytest.fail("usage validation ran before acceptance persistence completed"),
    )

    with pytest.raises(RuntimeError, match="synthetic local persistence failure"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic prompt"),
            config={
                "max_output_tokens": 512,
                "automatic_function_calling": {"disable": True},
            },
        )

    assert len(delegate.calls) == 1
    assert guard.provider_response_accepted is True
    assert guard.last_guard_state == "RESPONSE_ACCEPTED"

    with pytest.raises(harness.SmokeBlocked, match="PREFLIGHT_GUARD_TERMINAL"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic prompt"),
            config={
                "max_output_tokens": 512,
                "automatic_function_calling": {"disable": True},
            },
        )

    assert guard._terminal is True
    assert len(delegate.calls) == 1
    assert len(delegate.count_calls) == 1


def _synthetic_semantic_function_response():
    return {
        "candidates": [
            {
                "finish_reason": "STOP",
                "content": {
                    "parts": [
                        {"text": "do not persist this provider text"},
                        {
                            "function_call": {
                                "name": "emit_semantic_candidates",
                                "args": {
                                    "claims": [
                                        {
                                            "subject": "Mira",
                                            "relation": "vendor",
                                            "value": "Birchline",
                                        }
                                    ],
                                    "api_key": "synthetic-placeholder-only",
                                },
                            }
                        },
                    ]
                },
                "safety_ratings": [
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "NEGLIGIBLE"}
                ],
            }
        ],
        "usage_metadata": {
            "prompt_token_count": 5,
            "candidates_token_count": 12,
            "total_token_count": 17,
        },
    }


def test_semantic_response_replay_snapshot_keeps_only_sanitized_tool_payload() -> None:
    harness = _harness()

    snapshot = harness._semantic_response_replay_snapshot(
        _synthetic_semantic_function_response(),
        model=harness.MODEL,
    )

    assert snapshot["provider"] == "gemini"
    assert snapshot["model"] == harness.MODEL
    assert snapshot["candidate_count"] == 1
    assert snapshot["candidates"][0]["finish_reason"] == "STOP"
    assert snapshot["candidates"][0]["part_types"] == ["text", "function_call"]
    call = snapshot["candidates"][0]["function_calls"][0]
    assert call["name"] == "emit_semantic_candidates"
    assert call["arguments_representation"] == "dict"
    assert call["arguments"]["claims"][0]["value"] == "Birchline"
    assert "api_key" not in call["arguments"]
    assert snapshot["candidates"][0]["text_parts"][0]["utf8_bytes"] > 0

    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "do not persist this provider text" not in serialized
    assert "synthetic-placeholder-only" not in serialized
    assert len(snapshot["response_sha256"]) == 64


def _semantic_model_turn_request(harness):
    return ModelTurnRequest(
        run_id="synthetic-semantic-run",
        turn_id="synthetic-semantic-run:turn:1",
        task_id="synthetic-semantic-task",
        agent_id="synthetic-semantic-extractor",
        sequence=1,
        user_input="Extract the synthetic decision.",
        observation=None,
        available_tools=[_provider_tool()],
        model_id=harness.MODEL,
        generation_options=ModelGenerationOptions(
            max_output_tokens=512,
            temperature=0.0,
            require_tool_call=True,
        ),
    )


def _synthetic_function_call_response(arguments):
    return {
        "candidates": [
            {
                "finish_reason": "STOP",
                "content": {
                    "parts": [
                        {
                            "function_call": {
                                "name": "emit_semantic_candidates",
                                "args": arguments,
                            }
                        }
                    ]
                },
            }
        ],
        "usage_metadata": {
            "prompt_token_count": 5,
            "candidates_token_count": 12,
            "total_token_count": 17,
        },
    }


@pytest.mark.parametrize(
    ("elapsed_seconds", "latency_budget_exceeded"),
    [(29.0, False), (31.868, True)],
    ids=("within-time-budget", "accepted-response-after-time-budget"),
)
def test_accepted_response_is_parsed_and_latency_is_recorded_separately(
    elapsed_seconds: float,
    latency_budget_exceeded: bool,
) -> None:
    harness = _harness()
    aggregate = harness.AggregateUsage()
    response = _synthetic_function_call_response(
        {
            "claims": [
                {
                    "claim_type": "DECISION",
                    "subject": "Juniper receipts pilot",
                    "relation": "uses vendor",
                    "value": "Birchline",
                    "temporal_status": "NOT_STATED",
                    "valid_from": None,
                    "valid_to": None,
                    "confidence": {
                        "claim_type": 0.95,
                        "entity": 0.95,
                        "relation": 0.95,
                        "temporal": 0.9,
                        "overall": 0.95,
                    },
                }
            ]
        }
    )
    delegate = harness.RecordingDelegate(response=response)
    case_budget = replace(
        harness.MPS_001_RERUN_CASE_BUDGET,
        request_timeout_seconds=30,
    )
    clock_values = iter((0.0, 0.0, 0.0, elapsed_seconds))
    accepted_record_states = []

    def persist_accepted(_response):
        record = aggregate.preflight_records[0]
        accepted_record_states.append(
            (
                "response_replay_observation" in record,
                record.get("response_latency_ms"),
                record.get("latency_budget_exceeded"),
            )
        )

    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=case_budget,
        aggregate=aggregate,
        aggregate_budget=harness.MPS_001_RERUN_AGGREGATE_BUDGET,
        allow_static_count_tokens_fallback=True,
        clock=lambda: next(clock_values),
        on_response_accepted=persist_accepted,
    )

    parsed = GeminiProviderAdapter(guard, model_id=harness.MODEL).complete(
        _semantic_model_turn_request(harness)
    )

    observation = aggregate.preflight_records[0]["response_replay_observation"]
    assert observation["candidate_count"] == 1
    assert observation["candidates"][0]["function_calls"][0]["arguments"]["claims"][0]["subject"] == "Juniper receipts pilot"
    assert accepted_record_states == [
        (
            True,
            round(elapsed_seconds * 1000, 3),
            latency_budget_exceeded,
        )
    ]
    assert parsed.error is None
    assert parsed.action is not None
    assert parsed.action.kind == "tool_call"
    assert parsed.action.tool_call.arguments["claims"][0]["value"] == "Birchline"
    assert guard.provider_response_accepted is True
    assert guard.last_guard_state == "RESPONSE_ACCEPTED"


def test_transport_timeout_before_response_remains_a_timeout(monkeypatch) -> None:
    harness = _harness()
    monkeypatch.setattr(harness, "MAX_TRANSPORT_RETRIES_PER_REQUEST", 0)
    aggregate = harness.AggregateUsage()
    delegate = harness.RecordingDelegate(exception=TimeoutError("synthetic transport timeout"))
    clock_values = iter((0.0, 0.0, 0.0, 0.0))
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=harness.MPS_001_RERUN_CASE_BUDGET,
        aggregate=aggregate,
        aggregate_budget=harness.MPS_001_RERUN_AGGREGATE_BUDGET,
        allow_static_count_tokens_fallback=True,
        clock=lambda: next(clock_values),
    )

    response = GeminiProviderAdapter(guard, model_id=harness.MODEL).complete(
        _semantic_model_turn_request(harness)
    )

    assert response.error is not None
    assert response.error.code == "MODEL_TIMEOUT"
    assert guard.provider_response_accepted is False
    assert len(delegate.calls) == 1
    assert aggregate.preflight_records[0]["inference_status"] == "failed"
    assert "response_replay_observation" not in aggregate.preflight_records[0]


def test_malformed_accepted_response_after_latency_budget_is_not_a_timeout() -> None:
    harness = _harness()
    aggregate = harness.AggregateUsage()
    delegate = harness.RecordingDelegate(
        response=_synthetic_function_call_response(["malformed arguments"])
    )
    case_budget = replace(
        harness.MPS_001_RERUN_CASE_BUDGET,
        request_timeout_seconds=30,
    )
    clock_values = iter((0.0, 0.0, 0.0, 31.868))
    guard = harness.BudgetedGeminiClient(
        delegate,
        case=harness.MPS_001_RERUN_CASE,
        case_budget=case_budget,
        aggregate=aggregate,
        aggregate_budget=harness.MPS_001_RERUN_AGGREGATE_BUDGET,
        allow_static_count_tokens_fallback=True,
        clock=lambda: next(clock_values),
    )

    response = GeminiProviderAdapter(guard, model_id=harness.MODEL).complete(
        _semantic_model_turn_request(harness)
    )

    assert response.error is not None
    assert response.error.code == "MODEL_TOOL_CALL_PARSE_FAILED"
    assert guard.provider_response_accepted is True
    assert aggregate.preflight_records[0]["latency_budget_exceeded"] is True
    assert guard.last_guard_state == "RESPONSE_ACCEPTED"


def test_missing_output_usage_blocks_without_zero_fill_and_reserves_output_cap() -> None:
    harness = _harness()
    aggregate = harness.AggregateUsage()
    response = {
        "text": "synthetic response with thinking only",
        "usage_metadata": {
            "prompt_token_count": 5,
            "thoughts_token_count": 157,
        },
    }
    delegate = harness.RecordingDelegate(response=response)
    guard = _new_guard(harness, delegate, aggregate=aggregate)

    returned = guard.generate_content(
        model=harness.MODEL,
        contents=_contents("synthetic prompt"),
        config={
            "max_output_tokens": 512,
            "automatic_function_calling": {"disable": True},
        },
    )

    assert returned["text"] == "synthetic response with thinking only"
    assert guard.provider_response_accepted is True
    assert guard._terminal is True
    assert guard.last_guard_state == "RESPONSE_ACCEPTED_USAGE_INCOMPLETE"
    assert guard.preflight_records[0]["reported_output_tokens"] == harness.UNAVAILABLE
    assert guard.reported_billable_output_tokens == 157
    assert aggregate.reported_billable_output_tokens == 157
    assert guard.reserved_unknown_output_tokens == (
        harness.MPS_001_RERUN_CASE_BUDGET.per_request_output_tokens - 157
    )
    assert aggregate.reserved_unknown_output_tokens == guard.reserved_unknown_output_tokens
    from scripts.run_public_memory_clean_eval import _spent_upper_bound_usd

    assert _spent_upper_bound_usd(aggregate) >= harness._combined_cost_usd(
        aggregate.estimated_input_tokens,
        aggregate.preflight_counted_input_tokens,
        harness.MPS_001_RERUN_CASE_BUDGET.per_request_output_tokens,
    )
    with pytest.raises(harness.SmokeBlocked, match="PREFLIGHT_GUARD_TERMINAL"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic prompt"),
            config={
                "max_output_tokens": 512,
                "automatic_function_calling": {"disable": True},
            },
        )
    assert len(delegate.calls) == 1
    assert len(delegate.count_calls) == 1


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_token_count": 5, "candidates_token_count": 3, "thoughts_token_count": 2}, (5, 3, 2)),
        ({"input_tokens": 5, "output_tokens": 3, "thinking_tokens": 2}, (5, 3, 2)),
        ({"promptTokenCount": 5, "candidatesTokenCount": 3, "thinkingTokenCount": 2}, (5, 3, 2)),
    ],
)
def test_usage_normalizer_preserves_provider_and_sdk_aliases(usage, expected) -> None:
    harness = _harness()

    assert harness._reported_usage({"usage_metadata": usage})[:3] == expected


def test_static_fallback_is_explicit_and_preserves_hard_cost_guard() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(
        response=_successful_synthetic_response(),
        count_exception=_shape_failure(harness),
    )
    guard = _new_guard(harness, delegate)

    result = guard.generate_content(
        model=harness.MODEL,
        contents=_contents("small synthetic request"),
        config={
            "system_instruction": "synthetic system",
            "automatic_function_calling": {"disable": True},
        },
    )

    record = guard.preflight_records[0]
    assert result["text"] == "synthetic response"
    assert record["count_tokens_status"] == "UNAVAILABLE"
    assert record["token_estimation_source"] == "STATIC_CONSERVATIVE"
    assert record["billing_preflight_uncertainty"] is True
    assert isinstance(record["estimated_input_token_upper_bound"], int)
    assert record["counted_input_tokens"] == "UNAVAILABLE"
    assert len(delegate.count_calls) == 1
    assert len(delegate.calls) == 1


def test_static_fallback_requires_the_exact_deterministic_400_signature() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_exception=harness.CountTokensFailure(
            {
                "exception_class": "HTTPError",
                "http_status": 503,
                "provider_error_code": "UNAVAILABLE",
                "message": "temporary provider issue",
            }
        )
    )
    guard = _new_guard(harness, delegate)

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS_FAILED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic"),
            config={"automatic_function_calling": {"disable": True}},
        )

    assert delegate.calls == []


def test_static_fallback_accepts_explicit_transport_failure_and_records_uncertainty() -> None:
    harness = _harness()
    state = harness.CountTokensFallbackState()
    delegate = harness.RecordingDelegate(
        response=_successful_synthetic_response(),
        count_exception=harness.CountTokensFailure(
            {
                "exception_class": "URLError",
                "http_status": harness.UNAVAILABLE,
                "provider_error_code": harness.UNAVAILABLE,
                "message": "countTokens request failed",
            }
        ),
    )
    guard = _new_guard(harness, delegate, state=state, transport_fallback_only=True)
    contents = _contents("synthetic transport fallback request")
    config = {
        "system_instruction": "synthetic system",
        "automatic_function_calling": {"disable": True},
    }

    response = guard.generate_content(
        model=harness.MODEL,
        contents=contents,
        config=config,
    )

    record = guard.preflight_records[0]
    assert response["text"] == "synthetic response"
    assert record["count_tokens_status"] == "UNAVAILABLE"
    assert record["token_estimation_source"] == "STATIC_CONSERVATIVE"
    assert record["billing_preflight_uncertainty"] is True
    assert record["failure_class"] == "TRANSPORT_URLError"
    assert isinstance(record["estimated_input_token_upper_bound"], int)
    assert state.unavailable is True
    assert state.failure_class == "TRANSPORT_URLError"
    assert len(delegate.count_calls) == 1
    assert len(delegate.calls) == 1
    assert delegate.calls[0]["contents"] == contents
    assert delegate.calls[0]["config"]["system_instruction"] == config["system_instruction"]
    assert delegate.calls[0]["config"]["automatic_function_calling"] == config[
        "automatic_function_calling"
    ]
    assert guard.automatic_retry_count == 0


def test_transport_only_fallback_rejects_deterministic_400_before_generation() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_exception=_shape_failure(harness),
    )
    guard = _new_guard(harness, delegate, transport_fallback_only=True)

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS_FAILED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic"),
            config={
                "system_instruction": "synthetic system",
                "automatic_function_calling": {"disable": True},
            },
        )

    assert delegate.calls == []
    assert guard.preflight_records[0]["count_tokens_status"] != "UNAVAILABLE"


@pytest.mark.parametrize(
    "exception_class",
    [
        "URLError",
        "gaierror",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "RemoteDisconnected",
        "TimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "SSLError",
        "ProxyError",
    ],
)
def test_safe_transport_fallback_allowlist_is_explicit(exception_class: str) -> None:
    harness = _harness()
    assert harness._matches_safe_count_tokens_transport_failure(
        {
            "exception_class": exception_class,
            "http_status": harness.UNAVAILABLE,
            "provider_error_code": harness.UNAVAILABLE,
        }
    ) is True


def test_safe_transport_fallback_rejects_http_response_and_provider_error() -> None:
    harness = _harness()
    assert harness._matches_safe_count_tokens_transport_failure(
        {
            "exception_class": "URLError",
            "http_status": 503,
            "provider_error_code": harness.UNAVAILABLE,
        }
    ) is False
    assert harness._matches_safe_count_tokens_transport_failure(
        {
            "exception_class": "URLError",
            "http_status": harness.UNAVAILABLE,
            "provider_error_code": "RESOURCE_EXHAUSTED",
        }
    ) is False


def test_static_fallback_rejects_unknown_no_status_exception() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_exception=harness.CountTokensFailure(
            {
                "exception_class": "MysteryFailure",
                "http_status": harness.UNAVAILABLE,
                "provider_error_code": harness.UNAVAILABLE,
                "message": "countTokens request failed",
            }
        )
    )
    guard = _new_guard(harness, delegate)

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS_FAILED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic"),
            config={"automatic_function_calling": {"disable": True}},
        )

    assert delegate.calls == []


def test_static_fallback_does_not_bypass_other_invalid_argument_fields() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_exception=harness.CountTokensFailure(
            {
                "exception_class": "HTTPError",
                "http_status": 400,
                "provider_error_code": "INVALID_ARGUMENT",
                "message": "field generate_content_request.contents is invalid",
            }
        )
    )
    guard = _new_guard(harness, delegate)

    with pytest.raises(harness.SmokeBlocked, match="COUNT_TOKENS_FAILED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic"),
            config={"automatic_function_calling": {"disable": True}},
        )

    assert delegate.calls == []


def test_static_fallback_blocks_when_upper_bound_exceeds_input_cap() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(count_exception=_shape_failure(harness))
    guard = _new_guard(harness, delegate)

    with pytest.raises(harness.SmokeBlocked, match="STATIC_COUNT_TOKENS_BOUND_UNSAFE"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("x" * 51_000),
            config={
                "system_instruction": "synthetic system",
                "automatic_function_calling": {"disable": True},
            },
        )

    assert delegate.calls == []


def test_static_fallback_blocks_before_generation_when_cost_cap_is_too_small() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(count_exception=_shape_failure(harness))
    tiny_cost_budget = replace(
        harness.MPS_001_RERUN_AGGREGATE_BUDGET,
        max_cost_usd=0.000001,
    )
    guard = _new_guard(harness, delegate, budget=tiny_cost_budget)

    with pytest.raises(harness.SmokeBlocked, match="PROJECTED_COST_BUDGET_EXCEEDED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic"),
            config={
                "system_instruction": "synthetic system",
                "automatic_function_calling": {"disable": True},
            },
        )

    assert len(delegate.count_calls) == 1
    assert delegate.calls == []


def test_transport_fallback_still_blocks_generation_when_cost_cap_is_too_small() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(
        count_exception=harness.CountTokensFailure(
            {
                "exception_class": "URLError",
                "http_status": harness.UNAVAILABLE,
                "provider_error_code": harness.UNAVAILABLE,
                "message": "countTokens request failed",
            }
        )
    )
    tiny_cost_budget = replace(
        harness.MPS_001_RERUN_AGGREGATE_BUDGET,
        max_cost_usd=0.000001,
    )
    guard = _new_guard(harness, delegate, budget=tiny_cost_budget)

    with pytest.raises(harness.SmokeBlocked, match="PROJECTED_COST_BUDGET_EXCEEDED"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("synthetic transport under tiny cap"),
            config={
                "system_instruction": "synthetic system",
                "automatic_function_calling": {"disable": True},
            },
        )

    assert len(delegate.count_calls) == 1
    assert delegate.calls == []


def test_static_fallback_state_prevents_repeating_known_bad_count_request() -> None:
    harness = _harness()
    state = harness.CountTokensFallbackState()
    first_delegate = harness.RecordingDelegate(
        response=_successful_synthetic_response(),
        count_exception=_shape_failure(harness),
    )
    first = _new_guard(harness, first_delegate, state=state)
    first.generate_content(
        model=harness.MODEL,
        contents=_contents("first synthetic request"),
        config={
            "system_instruction": "synthetic system",
            "automatic_function_calling": {"disable": True},
        },
    )
    second_delegate = harness.RecordingDelegate(response=_successful_synthetic_response())
    second = _new_guard(
        harness,
        second_delegate,
        aggregate=first.aggregate,
        state=state,
    )
    second.generate_content(
        model=harness.MODEL,
        contents=_contents("second synthetic request"),
        config={
            "system_instruction": "synthetic system",
            "automatic_function_calling": {"disable": True},
        },
    )

    assert state.unavailable is True
    assert len(first_delegate.count_calls) == 1
    assert second_delegate.count_calls == []
    assert second.preflight_records[0]["count_tokens_status"] == "UNAVAILABLE"
    assert second.preflight_records[0]["failure_class"] == "HTTP_400_SYSTEM_INSTRUCTION_SCHEMA"


def test_static_fallback_rejects_shapes_outside_frozen_text_only_case() -> None:
    harness = _harness()
    delegate = harness.RecordingDelegate(count_exception=_shape_failure(harness))
    guard = _new_guard(harness, delegate)

    with pytest.raises(harness.SmokeBlocked, match="STATIC_COUNT_TOKENS_BOUND_UNSAFE"):
        guard.generate_content(
            model=harness.MODEL,
            contents=_contents("user turn", "model turn", "second user turn"),
            config={
                "system_instruction": "synthetic system",
                "automatic_function_calling": {"disable": True},
            },
        )

    assert delegate.calls == []
