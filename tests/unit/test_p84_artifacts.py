"""P8.4 durable model record and artifact contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.runtime.artifacts import ArtifactRef, ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    ModelExecutionRecord,
    RuntimeState,
    SourceContext,
    ToolExecutionRecord,
)
from linkloom.tools.contracts import ToolResult


@pytest.fixture
def artifact_root() -> Path:
    root = Path(".artifacts") / "p84-test-runs" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state(**changes) -> RuntimeState:
    values = {
        "schema_version": 1,
        "run_id": "run_p84_artifact",
        "thread_id": "thread_p84_artifact",
        "workflow": "ask",
        "status": "running",
        "step_seq": 1,
        "request_ref": "request_p84_artifact.json",
        "source": SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="fixture_p84",
        ),
    }
    values.update(changes)
    return RuntimeState(**values)


def _record(**changes) -> ModelExecutionRecord:
    values = {
        "run_id": "run_p84_artifact",
        "turn_id": "run_p84_artifact:turn:1",
        "task_id": "task_p84_artifact",
        "agent_id": "retrieval_agent",
        "sequence": 1,
        "status": "request_durable",
        "request_ref": "model/run_p84_artifact/turn_1/request.json",
        "tool_definition_snapshot_ref": "model/run_p84_artifact/turn_1/tools.json",
        "observation_ref": None,
        "response_ref": None,
        "normalized_action": None,
        "usage": {"input_tokens": 2},
        "provider_metadata": {"provider": "fake", "model": "scripted"},
    }
    values.update(changes)
    return ModelExecutionRecord(**values)


def test_model_execution_record_roundtrips_and_runtime_state_defaults_are_backward_compatible():
    record = _record()
    restored = ModelExecutionRecord.from_dict(json.loads(json.dumps(record.to_dict())))
    assert restored == record

    state = _state(model_executions=[record])
    restored_state = RuntimeState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored_state.model_executions == [record]

    legacy = state.to_dict()
    legacy.pop("model_executions")
    assert RuntimeState.from_dict(legacy).model_executions == []


def test_model_execution_record_roundtrips_optional_provider_response_projection():
    record = _record(
        status="response_durable",
        response_ref="model/run_p84_artifact/turn_1/response.json",
        response_sha256="b" * 64,
        normalized_action={
            "kind": "final",
            "tool_call": None,
            "final_answer": "durable answer",
        },
        provider_request_id="provider-request-1",
        provider_response_id="provider-response-1",
        finish_reason="stop",
        provider_error=None,
    )

    restored = ModelExecutionRecord.from_dict(
        json.loads(json.dumps(record.to_dict()))
    )

    assert restored == record
    assert restored.provider_request_id == "provider-request-1"
    assert restored.provider_response_id == "provider-response-1"
    assert restored.finish_reason == "stop"
    assert restored.provider_error is None


def test_model_execution_record_old_payload_defaults_provider_projection_to_none():
    payload = _record().to_dict()
    for field_name in (
        "provider_request_id",
        "provider_response_id",
        "finish_reason",
        "provider_error",
    ):
        payload.pop(field_name, None)

    restored = ModelExecutionRecord.from_dict(payload)

    assert restored.provider_request_id is None
    assert restored.provider_response_id is None
    assert restored.finish_reason is None
    assert restored.provider_error is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"provider_request_id": ""}, "provider_request_id"),
        ({"provider_response_id": "bad\nresponse"}, "provider_response_id"),
        ({"finish_reason": "x" * 513}, "finish_reason"),
        ({"provider_error": "not-an-object"}, "provider_error"),
        (
            {"provider_error": {"code": "MODEL_TIMEOUT", "api_key": "secret"}},
            "forbidden persisted key",
        ),
    ],
)
def test_model_execution_record_rejects_invalid_provider_projection(changes, message):
    with pytest.raises(ValidationError, match=message):
        _record(**changes)


def test_model_execution_record_bounds_inline_provider_error():
    with pytest.raises(ValidationError, match="inline checkpoint size limit"):
        _record(
            provider_error={
                "code": "MODEL_RESPONSE_MALFORMED",
                "details": {"safe_payload": "x" * (128 * 1024)},
            }
        )


def test_model_execution_record_rejects_hidden_reasoning_and_unsafe_refs():
    with pytest.raises(ValidationError):
        _record(normalized_action={"hidden_reasoning": "do not persist"})

    with pytest.raises(ValidationError):
        _record(request_ref="C:/private/request.json")

    with pytest.raises(ValidationError):
        _record(response_ref="../response.json")


def test_runtime_state_rejects_model_record_from_another_run():
    with pytest.raises(ValidationError):
        _state(model_executions=[_record(run_id="other_run")])


def test_tool_execution_record_rejects_forbidden_persisted_result_fields():
    with pytest.raises(ValidationError):
        ToolExecutionRecord(
            call_id="call_forbidden_result",
            run_id="run_p84_artifact",
            task_id="task_p84_artifact",
            agent_id="retrieval_agent",
            tool_id="search_notes",
            sequence=1,
            status="completed",
            result={"chain_of_thought": "must not persist"},
        )

    with pytest.raises(ValidationError):
        ToolExecutionRecord(
            call_id="call_forbidden_error",
            run_id="run_p84_artifact",
            task_id="task_p84_artifact",
            agent_id="retrieval_agent",
            tool_id="search_notes",
            sequence=1,
            status="failed",
            error={"code": "TOOL_EXECUTION_FAILED", "details": {"api_token": "secret"}},
        )


def test_tool_ledger_result_is_bounded_when_inline_in_runtime_state():
    with pytest.raises(ValidationError) as error:
        ToolExecutionRecord(
            call_id="call_large_result",
            run_id="run_p84_artifact",
            task_id="task_p84_artifact",
            agent_id="retrieval_agent",
            tool_id="read_verified_note",
            sequence=1,
            status="completed",
            arguments={"note_ref": "note.md"},
            result=ToolResult(
                call_id="call_large_result",
                tool_id="read_verified_note",
                status="ok",
                value={"content": "x" * (128 * 1024)},
            ).to_dict(),
        )

    assert error.value.details["reason"] == "inline_result_too_large"


def test_artifact_store_writes_deterministic_relative_hashed_artifacts(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    payload = {"kind": "request", "available_tools": [{"tool_id": "search_notes"}]}

    artifact = store.write("model/run_1/turn_1/request.json", payload, kind="request")

    assert isinstance(artifact, ArtifactRef)
    assert artifact.ref == "model/run_1/turn_1/request.json"
    assert len(artifact.sha256) == 64
    assert store.read(artifact.ref, expected_sha256=artifact.sha256) == payload
    assert (artifact_root / "models" / artifact.ref).read_bytes().endswith(b"\n")

    same = store.write(artifact.ref, payload, kind="request")
    assert same == artifact


def test_artifact_store_rejects_tampering_traversal_and_forbidden_payload_keys(artifact_root):
    store = ModelArtifactStore(artifact_root / "models")
    artifact = store.write("model/run_1/request.json", {"safe": True}, kind="request")

    path = artifact_root / "models" / artifact.ref
    path.write_text('{"safe":false}\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        store.read(artifact.ref, expected_sha256=artifact.sha256)

    with pytest.raises(ValidationError):
        store.write("../outside.json", {"safe": True}, kind="request")

    with pytest.raises(ValidationError):
        store.write("model/run_1/response.json", {"chain_of_thought": "secret"}, kind="response")

    with pytest.raises(ValidationError):
        store.write("model/run_1/response.json", {"api_token": "secret"}, kind="response")
