from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import httpx
from google import genai
from google.genai import types

from linkloom.agents.providers.gemini_api import (
    build_gemini_sdk_client,
    map_tool_definition_to_gemini_function,
)
from linkloom.semantic_ingestion.extraction import _provider_tool
from linkloom.tools.contracts import ToolDefinition


def test_gemini_function_mapper_preserves_nullable_types_and_projects_max_length() -> None:
    schema = {
        "type": "object",
        "properties": {
            "claim": {
                "type": "object",
                "properties": {
                    "subject": {"type": ["string", "null"], "maxLength": 512},
                },
                "required": ["subject"],
                "additionalProperties": False,
            },
        },
        "required": ["claim"],
        "additionalProperties": False,
    }
    definition = ToolDefinition(
        tool_id="emit_semantic_candidates",
        version="semantic-candidate/v1",
        description="Return structured source-grounded claim proposals.",
        input_schema=schema,
        output_schema=schema,
    )

    mapped = map_tool_definition_to_gemini_function(definition)

    mapped_subject = mapped["parameters_json_schema"]["properties"]["claim"][
        "properties"
    ]["subject"]
    assert mapped_subject == {"type": ["string", "null"]}
    assert schema["properties"]["claim"]["properties"]["subject"]["type"] == [
        "string",
        "null",
    ]
    assert schema["properties"]["claim"]["properties"]["subject"]["maxLength"] == 512


def test_gemini_sdk_serializes_json_schema_using_api_wire_field_name() -> None:
    schema = {
        "type": "object",
        "properties": {
            "claim": {
                "type": "string",
                "enum": ["supported", "unknown"],
            },
            "details": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["note", "decision"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["source", "confidence"],
                "additionalProperties": False,
            },
        },
        "required": ["claim", "details"],
        "additionalProperties": False,
    }
    definition = ToolDefinition(
        tool_id="emit_semantic_candidates",
        version="semantic-candidate/v1",
        description="Return structured source-grounded claim proposals.",
        input_schema=schema,
        output_schema=schema,
    )
    declaration = map_tool_definition_to_gemini_function(definition)
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "emit_semantic_candidates",
                                        "args": {"claim": "synthetic"},
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    sdk = build_gemini_sdk_client(
        api_key="synthetic-no-network",
        genai_module=genai,
        types_module=types,
        http_options_kwargs={
            "client_args": {"transport": httpx.MockTransport(capture)}
        },
    )
    try:
        sdk.models.generate_content(
            model="gemini-3.8-flash",
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={
                "tools": [{"function_declarations": [declaration]}],
                "tool_config": {"function_calling_config": {"mode": "ANY"}},
            },
        )
    finally:
        sdk.close()

    body = captured["body"]
    assert isinstance(body, dict)
    wire_declaration = body["tools"][0]["functionDeclarations"][0]
    assert "parametersJsonSchema" in wire_declaration
    assert "parameters_json_schema" not in wire_declaration
    assert wire_declaration["parametersJsonSchema"] == declaration["parameters_json_schema"]
    assert wire_declaration["parametersJsonSchema"] == schema
    assert wire_declaration["parametersJsonSchema"]["properties"]["claim"]["enum"] == [
        "supported",
        "unknown",
    ]
    assert (
        wire_declaration["parametersJsonSchema"]["properties"]["details"][
            "additionalProperties"
        ]
        is False
    )
    assert wire_declaration["parametersJsonSchema"]["properties"]["details"][
        "properties"
    ]["source"]["enum"] == ["note", "decision"]


def test_exact_semantic_schema_wire_contract_and_fingerprints() -> None:
    definition = _provider_tool()
    declaration = map_tool_definition_to_gemini_function(definition)
    captured: dict[str, object] = {}
    observations: list[dict[str, str]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured["wire_bytes"] = request.content
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "emit_semantic_candidates",
                                        "args": {"claims": []},
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    sdk = build_gemini_sdk_client(
        api_key="synthetic-no-network",
        genai_module=genai,
        types_module=types,
        http_options_kwargs={
            "client_args": {"transport": httpx.MockTransport(capture)}
        },
        wire_payload_observer=observations.append,
    )
    try:
        sdk.models.generate_content(
            model="gemini-3.8-flash",
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={
                "tools": [{"function_declarations": [declaration]}],
                "tool_config": {"function_calling_config": {"mode": "ANY"}},
            },
        )
    finally:
        sdk.close()

    body = captured["body"]
    wire_bytes = captured["wire_bytes"]
    assert isinstance(body, dict)
    assert isinstance(wire_bytes, bytes)
    wire_declaration = body["tools"][0]["functionDeclarations"][0]
    assert "parametersJsonSchema" in wire_declaration
    assert "parameters_json_schema" not in wire_declaration

    canonical = definition.input_schema
    wire_schema = wire_declaration["parametersJsonSchema"]
    expected_wire_schema = deepcopy(canonical)
    expected_claims_schema = expected_wire_schema["properties"]["claims"]
    expected_claims_schema.pop("maxItems")
    expected_claim_properties = expected_claims_schema["items"]["properties"]
    for field in ("subject", "relation", "value", "valid_from", "valid_to"):
        expected_claim_properties[field] = {
            key: value
            for key, value in expected_claim_properties[field].items()
            if key != "maxLength"
        }
    assert wire_schema == expected_wire_schema

    canonical_claim_schema = canonical["properties"]["claims"]["items"]
    claim_schema = wire_schema["properties"]["claims"]["items"]
    for field in ("subject", "relation", "value", "valid_from", "valid_to"):
        assert canonical_claim_schema["properties"][field]["type"] == ["string", "null"]
        assert canonical_claim_schema["properties"][field]["maxLength"] in {40, 512}
        assert claim_schema["properties"][field]["type"] == ["string", "null"]
        assert "maxLength" not in claim_schema["properties"][field]
    assert canonical["properties"]["claims"]["maxItems"] == 20
    assert "maxItems" not in wire_schema["properties"]["claims"]
    assert claim_schema["additionalProperties"] is False
    assert claim_schema["properties"]["confidence"]["additionalProperties"] is False
    assert wire_schema["additionalProperties"] is False
    assert claim_schema["properties"]["claim_type"]["enum"]
    assert claim_schema["properties"]["temporal_status"]["enum"]
    assert claim_schema["properties"]["confidence"]["type"] == "object"
    assert claim_schema["properties"]["confidence"]["properties"]["overall"][
        "minimum"
    ] == 0
    assert claim_schema["properties"]["confidence"]["properties"]["overall"][
        "maximum"
    ] == 1
    assert "responseSchema" not in body
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"

    assert len(observations) == 1
    observation = observations[0]
    assert observation["wire_payload_sha256"] == hashlib.sha256(wire_bytes).hexdigest()
    canonical_wire_json = json.dumps(
        wire_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert observation["wire_schema_subtree_sha256"] == hashlib.sha256(
        canonical_wire_json
    ).hexdigest()
    config_json = json.dumps(
        body["toolConfig"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert observation["wire_tool_config_sha256"] == hashlib.sha256(config_json).hexdigest()
