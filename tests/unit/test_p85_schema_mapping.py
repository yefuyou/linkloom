"""P8.5 WP-2 Gemini function-schema mapping tests."""

from __future__ import annotations

import copy

from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
from linkloom.agents.providers.gemini_api import (
    GeminiProviderAdapter,
    map_tool_definition_to_gemini_function,
)
from linkloom.tools.contracts import ToolDefinition
from linkloom.tools.runtime import create_retrieval_tool_runtime


def _definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="read_verified_note",
        version="2",
        description="Read one verified note.",
        input_schema={
            "type": "object",
            "description": "Arguments for reading one note.",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative note path.",
                    "pattern": r"^[^:]+$",
                    "minLength": 1,
                    "maxLength": 120,
                },
                "line_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    )


def _request() -> ModelTurnRequest:
    return ModelTurnRequest(
        run_id="run_p85_schema",
        turn_id="run_p85_schema:turn:1",
        task_id="task_p85_schema",
        agent_id="retrieval_agent",
        sequence=1,
        user_input="read the verified note",
        observation=None,
        available_tools=[_definition()],
        model_id="gemini-test-model",
        generation_options=ModelGenerationOptions(temperature=0),
    )


def test_tool_definition_maps_to_one_manual_function_declaration_without_version_leak():
    definition = _definition()
    canonical_schema = copy.deepcopy(definition.input_schema)
    expected_projection = copy.deepcopy(canonical_schema)
    expected_projection["properties"]["path"].pop("maxLength")

    mapped = map_tool_definition_to_gemini_function(definition)

    assert mapped == {
        "name": "read_verified_note",
        "description": "Read one verified note.",
        "parameters_json_schema": expected_projection,
    }
    assert definition.input_schema == canonical_schema
    assert "version" not in mapped
    assert "output_schema" not in mapped


def test_tool_schema_mapping_returns_a_deep_copy_and_does_not_mutate_definition():
    definition = _definition()
    original_schema = copy.deepcopy(definition.input_schema)

    mapped = map_tool_definition_to_gemini_function(definition)
    mapped["parameters_json_schema"]["properties"]["path"]["maxLength"] = 1

    assert definition.input_schema == original_schema


def test_production_read_tool_tells_model_to_replay_search_evidence_id():
    runtime = create_retrieval_tool_runtime(
        lambda query, source_context, limit: [],
        lambda note_ref: {"ref": note_ref},
    )
    definition, _executor = runtime.registry.resolve("read_verified_note")

    mapped = map_tool_definition_to_gemini_function(definition)

    assert "evidence_id" in mapped["description"]
    note_ref_schema = mapped["parameters_json_schema"]["properties"]["note_ref"]
    assert "search_notes" in note_ref_schema["description"]
    assert "evidence_id" in note_ref_schema["description"]
    assert "relative_path" in note_ref_schema["description"]


def test_adapter_request_groups_function_declarations_and_preserves_generation_options():
    from dataclasses import dataclass

    @dataclass
    class Client:
        response: object

        def __post_init__(self):
            self.requests = []

        def generate_content(self, *, model, contents, config):
            self.requests.append(
                {
                    "model": model,
                    "contents": contents,
                    "config": config,
                }
            )
            return self.response

    client = Client({"text": "done"})
    GeminiProviderAdapter(client).complete(_request())

    request = client.requests[0]
    assert request["config"]["tools"] == [
        {"function_declarations": [map_tool_definition_to_gemini_function(_definition())]}
    ]
    assert request["config"]["automatic_function_calling"] == {"disable": True}
    assert request["config"]["temperature"] == 0
