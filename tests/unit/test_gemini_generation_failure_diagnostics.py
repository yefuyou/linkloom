from __future__ import annotations

import json

import httpx
from google import genai
from google.genai import types

from linkloom.agents.providers.gemini_api import (
    _gemini_wire_payload_fingerprints,
    build_gemini_sdk_client,
    map_tool_definition_to_gemini_function,
)
from linkloom.agents.providers.gemini_diagnostics import (
    gemini_exception_diagnostics,
    generation_failure_record,
)
from linkloom.agents.model_adapter import MODEL_PROVIDER_ERROR_SPECS, ModelProviderError
from linkloom.semantic_ingestion.extraction import _provider_tool


def test_generation_failure_record_keeps_only_allowlisted_safe_fields():
    fingerprint = "a" * 64
    record = generation_failure_record(
        {
            "provider": "gemini",
            "model": "gemini-3.8-flash",
            "failure_stage": "GENERATION",
            "failure_category": "PROVIDER_SERVER_ERROR",
            "exception_type": "ServerError",
            "exception_message_safe": "temporary provider failure",
            "http_status": 503,
            "provider_error_code": "UNAVAILABLE",
            "request_id": "provider-request-1",
            "retry_classification": "HTTP_503",
            "retryable": True,
            "api_endpoint_hostname": "generativelanguage.googleapis.com",
            "api_endpoint_port": 443,
            "low_level_failure_class": "HTTP_5XX",
            "failure_layer": "HTTP",
            "api_key": "must-not-persist",
            "prompt": "private user input",
            "response_json": {"secret": "raw provider body"},
        },
        attempt_number=2,
        request_fingerprint=fingerprint,
        response_accepted=False,
    )

    assert record["attempt_number"] == 2
    assert record["request_fingerprint"] == fingerprint
    assert record["response_accepted"] is False
    assert record["http_status"] == 503
    assert record["retryable"] is True
    serialized = json.dumps(record)
    assert "must-not-persist" not in serialized
    assert "private user input" not in serialized
    assert "raw provider body" not in serialized


def test_generation_failure_record_keeps_unknown_fields_unknown():
    record = generation_failure_record(
        {"failure_category": "UNKNOWN_PROVIDER_ERROR", "retryable": False},
        attempt_number=None,
        request_fingerprint="not-a-fingerprint",
        response_accepted=False,
    )

    assert record["http_status"] is None
    assert record["provider_error_code"] is None
    assert record["provider_request_id"] is None
    assert record["retryability_classification"] == "NON_RETRYABLE"
    assert record["attempt_number"] is None
    assert record["request_fingerprint"] is None
    assert record["response_accepted"] is False


def test_mock_http_error_projects_structured_provider_diagnostics_and_redacts_secrets():
    private_prompt = "PRIVATE_PROMPT_SENTINEL"
    secret = "AIza000000000000000000000000000000000000000"
    response_body = {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "Request contains an invalid argument.",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [
                        {
                            "field": "tools[0].functionDeclarations[0].parametersJsonSchema.anyOf",
                            "description": "Unknown field in generated request.",
                        }
                    ],
                    "api_key": secret,
                    "prompt": private_prompt,
                    "diagnostic_url": "https://example.test/?x-goog-signature=signature-secret&credential=query-secret",
                    "header_value": "Cookie: session=first; Path=/, Cookie: other=second",
                    "diagnostic_note": "x" * 12_000,
                }
            ],
        }
    }
    wire_observations: list[dict[str, str]] = []

    def mock_provider_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={
                "x-goog-request-id": "req-gemini-123",
                "x-cloud-trace-context": "trace-gemini-456/1;o=1",
            },
            json=response_body,
        )

    sdk = build_gemini_sdk_client(
        api_key="synthetic-no-network",
        genai_module=genai,
        types_module=types,
        http_options_kwargs={
            "client_args": {"transport": httpx.MockTransport(mock_provider_error)}
        },
        wire_payload_observer=wire_observations.append,
    )
    try:
        declaration = map_tool_definition_to_gemini_function(_provider_tool())
        try:
            sdk.models.generate_content(
                model="gemini-3.8-flash",
                contents=[{"role": "user", "parts": [{"text": private_prompt}]}],
                config={
                    "tools": [{"function_declarations": [declaration]}],
                    "tool_config": {"function_calling_config": {"mode": "ANY"}},
                },
            )
        except Exception as sdk_error:
            wrapper = RuntimeError("SDK wrapper")
            wrapper.__cause__ = sdk_error
        else:
            raise AssertionError("the fake HTTP 400 response must raise an SDK exception")

        details = gemini_exception_diagnostics(
            wrapper,
            high_level_outcome="known_failure",
            model="gemini-3.8-flash",
            client=sdk,
            payload={"contents": [{"parts": [{"text": private_prompt}]}]},
            elapsed_ms=1.25,
        )
        details.update({"retry_classification": "NON_RETRYABLE", "retryable": False})
        category, _ = MODEL_PROVIDER_ERROR_SPECS["MODEL_INVALID_REQUEST"]
        persisted_error = ModelProviderError(
            code="MODEL_INVALID_REQUEST",
            category=category,
            message="The Gemini request was invalid.",
            retryable=False,
            outcome="known_failure",
            details={"reason": "provider_exception", **details},
        )
        record = generation_failure_record(
            persisted_error.details,
            attempt_number=1,
            request_fingerprint="b" * 64,
            response_accepted=False,
        )
    finally:
        sdk.close()

    assert record["http_status"] == 400
    assert record["provider_error_code"] == "INVALID_ARGUMENT"
    assert record["provider_error_status"] == "INVALID_ARGUMENT"
    assert record["sanitized_error_message"] == "Request contains an invalid argument."
    assert record["provider_request_id"] == "req-gemini-123"
    assert record["provider_trace_id"] == "trace-gemini-456/1;o=1"
    assert record["sdk_exception_fqcn"] == "google.genai.errors.ClientError"
    assert any(
        entry["exception_fqcn"].endswith("ClientError")
        for entry in record["sdk_exception_cause_chain"]
    )
    assert record["retryability_classification"] == "NON_RETRYABLE"
    assert record["retryable"] is False
    assert record["provider_error_details"]
    assert record["field_violations"][0]["field"].endswith("parametersJsonSchema.anyOf")
    assert "tools[0].functionDeclarations[0].parametersJsonSchema.anyOf" in record[
        "unknown_field_paths"
    ]
    assert record["provider_error_details_truncated"] is True
    assert len(json.dumps(record["provider_error_details"], ensure_ascii=False)) <= 8192
    assert record["wire_payload_sha256"] == wire_observations[0]["wire_payload_sha256"]
    assert record["wire_schema_subtree_sha256"] == wire_observations[0]["wire_schema_subtree_sha256"]
    assert record["wire_tool_config_sha256"] == wire_observations[0]["wire_tool_config_sha256"]
    serialized = json.dumps(record, ensure_ascii=False)
    assert secret not in serialized
    assert private_prompt not in serialized
    assert "signature-secret" not in serialized
    assert "query-secret" not in serialized
    assert "session=first" not in serialized
    assert '"api_key"' not in serialized


def test_failure_projection_emits_null_for_unavailable_provider_diagnostics():
    record = generation_failure_record(
        {"retryable": False},
        attempt_number=1,
        request_fingerprint="c" * 64,
        response_accepted=False,
    )

    assert record["provider_error_details"] is None
    assert record["field_violations"] == []
    assert record["unknown_field_paths"] == []
    assert record["provider_request_id"] is None
    assert record["provider_trace_id"] is None
    assert record["sdk_exception_fqcn"] is None
    assert record["sdk_exception_cause_chain"] == []
    assert record["wire_payload_sha256"] is None
    assert record["wire_schema_subtree_sha256"] is None
    assert record["wire_tool_config_sha256"] is None


def test_failure_projection_keeps_full_wire_hash_when_optional_subtrees_are_absent():
    wire_bytes = b'{"contents":[]}'
    request = httpx.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
        content=wire_bytes,
    )
    fingerprints = _gemini_wire_payload_fingerprints(request)
    assert fingerprints is not None
    request.extensions["linkloom_gemini_wire_fingerprints"] = fingerprints
    response = httpx.Response(400, request=request)
    exception = RuntimeError("synthetic failure")
    exception.response = response

    details = gemini_exception_diagnostics(
        exception,
        high_level_outcome="known_failure",
        model="test",
        client=None,
        payload={"contents": []},
        elapsed_ms=0.5,
    )
    record = generation_failure_record(
        details,
        attempt_number=1,
        request_fingerprint="d" * 64,
        response_accepted=False,
    )

    assert record["wire_payload_sha256"] == fingerprints["wire_payload_sha256"]
    assert record["wire_schema_subtree_sha256"] is None
    assert record["wire_tool_config_sha256"] is None
