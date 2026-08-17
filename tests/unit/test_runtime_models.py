"""Unit tests for Linkloom Runtime models, validation, and JSON-safety."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from linkloom.runtime.errors import (
    StateTransitionError,
    ValidationError,
)
from linkloom.runtime.models import (
    AttemptRecord,
    ErrorEnvelope,
    InterruptEnvelope,
    PolicySnapshot,
    RunRequest,
    RunStatus,
    RuntimeState,
    SourceContext,
    UsageEnvelope,
    validate_state_transition,
)
from linkloom.runtime.policy import ReadOnlyPolicy


def _sample_source_context() -> SourceContext:
    return SourceContext(
        index_path=".artifacts/scan/vault_index.json",
        index_sha256="a" * 64,
        index_schema_version=1,
        vault_root_fingerprint="root_0123456789abcdef",
        fixture_id="sample_vault_v1",
        document_count=5,
    )


def _sample_runtime_state(
    status: str = "running",
    pending_interrupt: InterruptEnvelope | None = None,
    result_ref: str | None = None,
    error: ErrorEnvelope | None = None,
) -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id="run_test_0001",
        thread_id="thread_test_0001",
        parent_run_id=None,
        workflow="ask",
        status=status,
        current_step="retrieve_context",
        step_seq=1,
        request_ref="req_test_0001",
        source=_sample_source_context(),
        evidence_refs=["ev_0001"],
        pending_interrupt=pending_interrupt,
        result_ref=result_ref,
        attempts=[
            AttemptRecord(
                attempt_id="attempt_0001",
                node_name="retrieve_context",
                step_seq=1,
                input_sha256="b" * 64,
                idempotency_key="run_test_0001:1:retrieve_context:input_hash",
                status="completed",
                retry_index=0,
                output_ref="out_0001",
                error_code=None,
                started_at="2026-08-16T10:00:00Z",
                finished_at="2026-08-16T10:00:01Z",
            )
        ],
        usage=UsageEnvelope(
            step_count=1,
            provider_requests=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0.0,
        ),
        policy=PolicySnapshot(
            policy_version="p2-readonly-v1",
            write_capability=False,
            network_capability=False,
            max_steps=12,
            max_provider_requests=0,
        ),
        error=error,
        created_at="2026-08-16T10:00:00Z",
        updated_at="2026-08-16T10:00:01Z",
    )


def test_runtime_state_json_roundtrip() -> None:
    state = _sample_runtime_state(status="running")
    state_dict = state.to_dict()

    # Must be JSON serializable
    json_str = json.dumps(state_dict, ensure_ascii=False)
    loaded_dict = json.loads(json_str)

    restored_state = RuntimeState.from_dict(loaded_dict)
    assert restored_state == state
    assert restored_state.to_dict() == state_dict


def test_runtime_state_rejects_non_serializable_fields() -> None:
    # State cannot contain Path objects in string fields or raw clients
    with pytest.raises(ValidationError):
        RuntimeState(
            schema_version=1,
            run_id="run_01",
            thread_id="thread_01",
            workflow="ask",
            status="running",
            current_step="load",
            step_seq=0,
            request_ref=Path("/tmp/foo"),  # type: ignore[arg-type]
            source=_sample_source_context(),
            evidence_refs=[],
            usage=UsageEnvelope(),
            policy=PolicySnapshot(),
            created_at="2026-08-16T10:00:00Z",
            updated_at="2026-08-16T10:00:00Z",
        )

    with pytest.raises(ValidationError):
        RuntimeState(
            schema_version=1,
            run_id="run_01",
            thread_id="thread_01",
            workflow="ask",
            status="running",
            current_step="load",
            step_seq=0,
            request_ref="req_01",
            source=_sample_source_context(),
            evidence_refs=[],
            intent={"client": object()},
            usage=UsageEnvelope(),
            policy=PolicySnapshot(),
        )


def test_p2_contract_rejects_unapproved_workflows_and_missing_state_fields() -> None:
    with pytest.raises(ValidationError):
        RunRequest(request_id="req_01", workflow="organize", query="test")  # type: ignore[arg-type]

    state_dict = _sample_runtime_state().to_dict()
    state_dict.pop("thread_id")
    with pytest.raises(ValidationError):
        RuntimeState.from_dict(state_dict)

    with pytest.raises(ValidationError):
        RuntimeState(
            schema_version=1,
            run_id="run_01",
            thread_id="thread_01",
            workflow="organize",  # type: ignore[arg-type]
            status="running",
            current_step="load",
            step_seq=0,
            request_ref="req_01",
            source=_sample_source_context(),
            usage=UsageEnvelope(),
            policy=PolicySnapshot(),
        )


def test_p2_persistence_redacts_process_path_and_rejects_absolute_source_path() -> None:
    request = RunRequest(
        request_id="req_01",
        workflow="ask",
        query="test",
        vault_root="C:/private/vault",
    )
    assert "vault_root" not in request.to_dict()
    assert request.to_dict(include_process_fields=True)["vault_root"] == "C:/private/vault"

    with pytest.raises(ValidationError):
        SourceContext(
            index_path="C:/private/vault/index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="root_01",
        )


def test_p2_policy_and_terminal_error_contracts() -> None:
    with pytest.raises(ValidationError):
        PolicySnapshot(write_capability=True)  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _sample_runtime_state(status="rejected", error=None)


def test_runtime_state_paused_requires_interrupt() -> None:
    # Paused without pending_interrupt should raise ValidationError
    with pytest.raises(ValidationError) as exc_info:
        _sample_runtime_state(status="paused", pending_interrupt=None)
    assert "pending_interrupt" in str(exc_info.value)

    # Paused with pending_interrupt should succeed
    interrupt = InterruptEnvelope(
        interrupt_id="int_0001",
        kind="clarification_required",
        message="Please clarify the search term",
        payload={"options": ["opt1", "opt2"]},
        allowed_responses=["resume", "reject"],
        checkpoint_id="cp_0001",
        created_at="2026-08-16T10:00:00Z",
    )
    state = _sample_runtime_state(status="paused", pending_interrupt=interrupt)
    assert state.status == "paused"
    assert state.pending_interrupt is not None


def test_runtime_state_running_must_not_have_interrupt() -> None:
    interrupt = InterruptEnvelope(
        interrupt_id="int_0001",
        kind="clarification_required",
        message="Please clarify",
        payload={},
        allowed_responses=["resume"],
        checkpoint_id="cp_0001",
        created_at="2026-08-16T10:00:00Z",
    )
    with pytest.raises(ValidationError) as exc_info:
        _sample_runtime_state(status="running", pending_interrupt=interrupt)
    assert "pending_interrupt must be None" in str(exc_info.value)


def test_runtime_state_completed_requires_result_ref() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _sample_runtime_state(status="completed", result_ref=None)
    assert "result_ref" in str(exc_info.value)

    state = _sample_runtime_state(status="completed", result_ref="res_0001")
    assert state.status == "completed"
    assert state.result_ref == "res_0001"


def test_runtime_state_failed_or_stale_requires_error() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _sample_runtime_state(status="failed", error=None)
    assert "error" in str(exc_info.value)

    err = ErrorEnvelope(
        code="CONTENT_CHANGED",
        category="runtime",
        message="Source note modified during run",
        retryable=False,
    )
    state = _sample_runtime_state(status="stale", error=err)
    assert state.status == "stale"
    assert state.error.code == "CONTENT_CHANGED"


def test_state_transition_validation() -> None:
    # Valid transitions
    assert validate_state_transition("accepted", "running") == "running"
    assert validate_state_transition("running", "paused") == "paused"
    assert validate_state_transition("paused", "running") == "running"
    assert validate_state_transition("running", "completed") == "completed"
    assert validate_state_transition("running", "failed") == "failed"
    assert validate_state_transition("running", "rejected") == "rejected"
    assert validate_state_transition("running", "stale") == "stale"
    assert validate_state_transition("failed", "running") == "running"
    assert validate_state_transition("paused", "expired") == "expired"

    # Invalid transitions
    with pytest.raises(StateTransitionError):
        validate_state_transition("completed", "running")

    with pytest.raises(StateTransitionError):
        validate_state_transition("accepted", "completed")

    with pytest.raises(StateTransitionError):
        validate_state_transition("paused", "completed")


def test_run_request_validation() -> None:
    req = RunRequest(
        request_id="req_0001",
        workflow="ask",
        query="What is scanner?",
        vault_root="tests/fixtures/sample_vault",
        index_path=".artifacts/scan/vault_index.json",
        max_steps=10,
    )
    req_dict = req.to_dict()
    assert req_dict["request_id"] == "req_0001"
    assert req_dict["workflow"] == "ask"
    assert req_dict["schema_version"] == 1
    assert req_dict["dry_run"] is True

    # Invalid workflow
    with pytest.raises(ValidationError):
        RunRequest(
            request_id="req_0002",
            workflow="write_back",  # type: ignore[arg-type]
            query="test",
        )


def test_run_status_summary() -> None:
    status = RunStatus(
        run_id="run_0001",
        thread_id="thread_0001",
        status="paused",
        current_step="clarify_request",
        checkpoint_id="cp_0003",
        interrupt={"message": "Need query clarification"},
    )
    status_dict = status.to_dict()
    assert status_dict["status"] == "paused"
    assert status_dict["checkpoint_id"] == "cp_0003"


def test_readonly_policy_enforcement() -> None:
    policy = ReadOnlyPolicy(max_steps=5, max_provider_requests=0)
    snapshot = policy.to_snapshot()
    assert snapshot.write_capability is False
    assert snapshot.network_capability is False
    assert snapshot.max_steps == 5

    # Cannot enable write capability in ReadOnlyPolicy
    with pytest.raises(ValidationError):
        ReadOnlyPolicy(write_capability=True)  # type: ignore[call-arg]
