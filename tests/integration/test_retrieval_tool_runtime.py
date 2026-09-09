import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.base import AgentTask
from linkloom.agents.coordinator import Coordinator
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.agents.registry import create_default_registry
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.models import (
    PolicySnapshot,
    RuntimeState,
    SourceContext,
    TerminationState,
)
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


class RecordingTracer:
    def __init__(self):
        self.events = []

    def emit(self, event_type, status, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "status": status,
                "actor": kwargs.get("actor"),
                "attributes": kwargs.get("attributes") or {},
                "error": kwargs.get("error"),
            }
        )


class RecordingSink:
    def __init__(self):
        self.events = []

    def emit(self, event_type, actor, status, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "status": status,
                "attributes": kwargs.get("attributes") or {},
                "error": kwargs.get("error"),
            }
        )


@pytest.fixture
def m03_root(request: pytest.FixtureRequest) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"m03-compat-{request.node.name}-{uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root


def _sample_adapter(tmp_path: Path) -> tuple[RuntimeAgentAdapter, Path]:
    project_root = Path(__file__).resolve().parents[2]
    vault_root = tmp_path / "vault"
    shutil.copytree(project_root / "tests" / "fixtures" / "sample_vault", vault_root)
    scan = scan_vault(vault_root, tmp_path / "scan")
    return RuntimeAgentAdapter(vault_root, scan.index_path), vault_root


def _policy(allowed: list[str], max_calls: int = 5) -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=allowed,
            denied_tool_ids=["write_file", "rename_file", "read_gold", "raw_filesystem"],
            max_calls=max_calls,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _search_action(query: str, source_context: dict | None = None, limit: int = 10):
    from linkloom.tools.contracts import ToolCall

    return ModelAction.tool(
        ToolCall(
            call_id="model_search_proposal",
            tool_id="search_notes",
            arguments={
                "query": query,
                "source_context": source_context or {},
                "limit": limit,
            },
        )
    )


def _search_then_final(query: str) -> FakeModelAdapter:
    def search(request: ModelTurnRequest) -> ModelAction:
        return _search_action(query)

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        return ModelAction.final("model final")

    return FakeModelAdapter([search, final])


def _runtime_state(run_id: str, root: Path) -> tuple[RuntimeState, ModelArtifactStore, list[RuntimeState]]:
    state = RuntimeState(
        schema_version=1,
        run_id=run_id,
        thread_id=f"thread_{uuid4().hex[:10]}",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request.json",
        source=SourceContext(
            index_path="vault_index.json",
            index_sha256="0" * 64,
            vault_root_fingerprint="fixture-root",
        ),
        policy=PolicySnapshot(max_steps=12, max_provider_requests=8),
        termination=TerminationState(status="running", sequence=1),
        created_at="2026-08-30T00:00:00Z",
        updated_at="2026-08-30T00:00:00Z",
    )
    snapshots: list[RuntimeState] = []
    return state, ModelArtifactStore(root / "models"), snapshots


def _coordinator(tool_funcs, run_id: str, root: Path, model: FakeModelAdapter):
    state, artifact_store, snapshots = _runtime_state(run_id, root)

    def checkpoint(current_state: RuntimeState) -> None:
        snapshots.append(current_state)

    coordinator = Coordinator(
        registry=create_default_registry(),
        retrieval_agent=RetrievalAgent(
            model=model,
            initial_state=state,
            artifact_store=artifact_store,
            state_checkpoint_callback=checkpoint,
        ),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs,
    )
    return coordinator, snapshots


def _adapter_model_dependencies(
    root: Path,
    run_id: str,
    model: FakeModelAdapter,
) -> dict:
    state, artifact_store, snapshots = _runtime_state(run_id, root)

    def checkpoint(current_state: RuntimeState) -> None:
        snapshots.append(current_state)

    return {
        "model": model,
        "initial_state": state,
        "artifact_store": artifact_store,
        "state_checkpoint_callback": checkpoint,
    }


def _review_tools():
    return {
        "validate_evidence": lambda ref: {"status": "pass", "ref": ref},
        "validate_schema": lambda result_type, payload: {"status": "pass"},
    }


def test_runtime_adapter_migrates_search_read_and_preserves_evidence_contract(m03_root: Path):
    adapter, _ = _sample_adapter(m03_root)
    tracer = RecordingTracer()
    model = _search_then_final("Scanner")

    result = adapter.run(
        run_id="run_p81_retrieval",
        workflow="ask",
        query="Scanner",
        source_context={},
        tracer=tracer,
        **_adapter_model_dependencies(m03_root, "run_p81_retrieval", model),
    )

    assert result["status"] == "completed"
    assert result["evidence"]
    assert all(ref.startswith("ev_p1_") for ref in result["evidence"])

    tool_events = [event for event in tracer.events if event["event_type"] == "tool.called"]
    assert tool_events
    assert tool_events[0]["attributes"]["tool"] == "search_notes"
    assert all(event["attributes"]["call_id"] for event in tool_events)
    assert len({event["attributes"]["call_id"] for event in tool_events}) == len(tool_events)
    assert all(
        key not in json.dumps(event, ensure_ascii=False)
        for event in tool_events
        for key in ("policy_enforcer", "VaultReader", "filesystem")
    )

    called_tools = [event["attributes"]["tool"] for event in tool_events]
    assert called_tools[0] == "search_notes"
    assert all(tool == "read_verified_note" for tool in called_tools[1:])
    terminal_events = [
        event
        for event in tracer.events
        if event["event_type"] in {"tool.completed", "tool.failed"}
    ]
    assert {
        event["attributes"]["call_id"] for event in terminal_events
    } == {
        event["attributes"]["call_id"] for event in tool_events
    }
    assert all(
        sum(
            event["attributes"]["call_id"] == called["attributes"]["call_id"]
            for event in terminal_events
        )
        == 1
        for called in tool_events
    )
    assert result["tool_ledger"]
    assert all(record["status"] == "completed" for record in result["tool_ledger"])
    assert {record["call_id"] for record in result["tool_ledger"]} == {
        event["attributes"]["call_id"] for event in tool_events
    }


def test_runtime_adapter_projects_canonical_state_ledger(m03_root: Path):
    adapter, _ = _sample_adapter(m03_root)
    supplied_ledger = ToolExecutionLedger()
    callback_ledgers = []
    checkpoint_snapshots = []

    def checkpoint(current_ledger):
        callback_ledgers.append(current_ledger)
        checkpoint_snapshots.append(
            [record.to_dict() for record in current_ledger.to_list()]
        )

    state, artifact_store, state_snapshots = _runtime_state(
        "run_m01_supplied_ledger",
        m03_root,
    )

    def checkpoint_state(current_state: RuntimeState) -> None:
        state_snapshots.append(current_state)
        checkpoint_snapshots.append(
            [record.to_dict() for record in current_state.tool_ledger]
        )

    result = adapter.run(
        run_id="run_m01_supplied_ledger",
        workflow="ask",
        query="Scanner",
        source_context={},
        tool_ledger=supplied_ledger,
        tool_checkpoint_callback=checkpoint,
        model=_search_then_final("Scanner"),
        initial_state=state,
        artifact_store=artifact_store,
        state_checkpoint_callback=checkpoint_state,
    )

    result_records = result["tool_ledger"]
    assert result["status"] == "completed"
    assert result_records
    assert state_snapshots
    assert result_records == checkpoint_snapshots[-1]
    assert any(
        any(record["status"] == "pending" for record in snapshot)
        for snapshot in checkpoint_snapshots
    )
    assert all(record["status"] == "completed" for record in result_records)
    assert supplied_ledger.to_list() == []
    assert callback_ledgers == []


def test_successful_empty_search_completes_without_retrieval_fallback(m03_root: Path):
    sink = RecordingSink()
    read_calls = []
    tool_funcs = {
        "search_notes": lambda query, source_context, limit: [],
        "read_verified_note": lambda ref: read_calls.append(ref) or {"ref": ref},
        **_review_tools(),
    }

    coordinator, snapshots = _coordinator(
        tool_funcs,
        "run_m01_empty_search",
        m03_root,
        _search_then_final("no matching notes"),
    )
    result = coordinator.run(
        run_id="run_m01_empty_search",
        workflow="ask",
        query="no matching notes",
        source_context={},
        initial_refs=["request.md"],
        event_sink=sink,
    )

    assert result["status"] == "completed"
    assert result["agent_tasks"][0]["status"] == "completed"
    assert result["evidence"] == []
    assert result["fallback_used"] is False
    assert result["errors"] == []
    assert result["review"]["decision"] != "needs_human"
    assert read_calls == []
    state_records = snapshots[-1].tool_ledger
    assert len(state_records) == 1
    search_record = state_records[0].to_dict()
    assert search_record["tool_id"] == "search_notes"
    assert search_record["status"] == "completed"
    assert search_record["error"] is None
    assert not any(event["event_type"] == "tool.failed" for event in sink.events)
    assert "NO_VERIFIED_EVIDENCE" not in json.dumps(result, ensure_ascii=False)


def test_retrieval_agent_empty_search_returns_exact_m01_agent_result(m03_root: Path):
    run_id = "run_m01_agent_empty_search"
    state, artifact_store, snapshots = _runtime_state(run_id, m03_root)
    read_calls = []
    runtime = create_retrieval_tool_runtime(
        lambda query, source_context, limit: [],
        lambda ref: read_calls.append(ref) or {"ref": ref},
    )
    agent = RetrievalAgent(
        model=_search_then_final("no matching notes"),
        initial_state=state,
        artifact_store=artifact_store,
        state_checkpoint_callback=snapshots.append,
    )
    task = AgentTask(
        task_id="task_m01_agent_empty",
        run_id=run_id,
        parent_task_id=None,
        parent_agent_id="coordinator",
        agent_id="retrieval_agent",
        workflow="ask",
        input_refs=["request.md"],
        allowed_tool_ids=["search_notes", "read_verified_note"],
        max_steps=3,
        deadline_ms=5000,
        status="queued",
        attempt=0,
        created_at="2026-08-30T00:00:00Z",
    )

    agent_result = agent.execute(
        task=task,
        query="no matching notes",
        source_context={},
        policy_enforcer=_policy(["search_notes", "read_verified_note"]),
        tool_runtime=runtime,
    )

    assert agent_result.status == "completed"
    assert agent_result.output_refs == []
    assert agent_result.error is None
    assert agent_result.handoff == {
        "status": "not_required",
        "reason_code": "no_matching_notes",
    }
    assert read_calls == []
    assert snapshots[-1].tool_ledger[0].tool_id == "search_notes"
    assert snapshots[-1].tool_ledger[0].status == "completed"
    assert snapshots[-1].tool_ledger[0].result["value"] == []


def test_model_executor_and_ledger_receive_identical_business_arguments(m03_root: Path):
    adapter, _ = _sample_adapter(m03_root)
    run_id = "run_m03_executor_argument_integrity"
    state, artifact_store, snapshots = _runtime_state(run_id, m03_root)
    proposed_arguments = []
    executor_arguments = []
    search_arguments = {
        "query": "Scanner",
        "source_context": {"model_owned": "executor-integrity"},
        "limit": 2,
    }
    original_search = adapter._search_notes
    original_read = adapter._read_verified_note_for_tool

    def recording_search(query, source_context, limit):
        executor_arguments.append(
            {
                "query": query,
                "source_context": source_context,
                "limit": limit,
            }
        )
        return original_search(query, source_context, limit)

    def recording_read(ref):
        executor_arguments.append({"note_ref": ref})
        return original_read(ref)

    adapter._search_notes = recording_search
    adapter._read_verified_note_for_tool = recording_read

    def model_search(request: ModelTurnRequest) -> ModelAction:
        proposed_arguments.append(dict(search_arguments))
        return ModelAction.tool(
            ToolCall(
                call_id="proposal_executor_search",
                tool_id="search_notes",
                arguments=dict(search_arguments),
            )
        )

    def model_read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        selected_ref = request.observation.value[1]["evidence_id"]
        arguments = {"note_ref": selected_ref}
        proposed_arguments.append(arguments)
        return ModelAction.tool(
            ToolCall(
                call_id="proposal_executor_read",
                tool_id="read_verified_note",
                arguments=arguments,
            )
        )

    def model_final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        return ModelAction.final("executor argument integrity final")

    model = FakeModelAdapter([model_search, model_read, model_final])
    result = adapter.run(
        run_id=run_id,
        workflow="ask",
        query="caller query is not the model payload",
        source_context={},
        model=model,
        initial_state=state,
        artifact_store=artifact_store,
        state_checkpoint_callback=snapshots.append,
    )

    ledger_arguments = [record["arguments"] for record in result["tool_ledger"]]
    assert result["status"] == "completed"
    assert proposed_arguments == executor_arguments == ledger_arguments
    assert proposed_arguments[0] == search_arguments
    assert proposed_arguments[1] == {"note_ref": proposed_arguments[1]["note_ref"]}


def test_search_failure_is_recorded_and_coordinator_fallback_remains_visible(m03_root: Path):
    sink = RecordingSink()

    def search_failure(query, source_context, limit):
        raise RuntimeError("internal /absolute/secret.txt")

    tool_funcs = {
        "search_notes": search_failure,
        "read_verified_note": lambda ref: {"ref": ref},
        **_review_tools(),
    }
    model = _search_then_final("failure")
    coordinator, snapshots = _coordinator(
        tool_funcs,
        "run_p81_search_failure",
        m03_root,
        model,
    )
    result = coordinator.run(
        run_id="run_p81_search_failure",
        workflow="ask",
        query="failure",
        source_context={},
        initial_refs=["request.md"],
        event_sink=sink,
    )

    assert result["status"] == "completed"
    assert result["fallback_used"] is True
    failed_tools = [event for event in sink.events if event["event_type"] == "tool.failed"]
    assert failed_tools
    assert failed_tools[0]["error"]["code"] == "TOOL_EXECUTION_FAILED"
    assert "secret.txt" not in json.dumps(failed_tools[0], ensure_ascii=False)
    assert snapshots[-1].tool_ledger[0].status == "failed"
    assert model.requests[1].observation is not None
    assert model.requests[1].observation.error is not None
    assert model.requests[1].observation.error.code == "TOOL_EXECUTION_FAILED"


def test_read_failure_is_not_silently_swallowed(m03_root: Path):
    sink = RecordingSink()

    def read_failure(ref):
        raise RuntimeError("reader failed for /vault/private.md")

    tool_funcs = {
        "search_notes": lambda query, source_context, limit: [{"ref": "note_ref_0"}],
        "read_verified_note": read_failure,
        **_review_tools(),
    }
    def search_then_read(request: ModelTurnRequest) -> ModelAction:
        if request.observation is None:
            return _search_action("failure")
        from linkloom.tools.contracts import ToolCall

        return ModelAction.tool(
            ToolCall(
                call_id="model_read_proposal",
                tool_id="read_verified_note",
                arguments={"note_ref": request.observation.value[0]["ref"]},
            )
        )

    model = FakeModelAdapter(
        [
            search_then_read,
            search_then_read,
            lambda request: ModelAction.final("model final after read failure"),
        ]
    )
    coordinator, snapshots = _coordinator(
        tool_funcs,
        "run_p81_read_failure",
        m03_root,
        model,
    )
    result = coordinator.run(
        run_id="run_p81_read_failure",
        workflow="ask",
        query="failure",
        source_context={},
        initial_refs=["request.md"],
        event_sink=sink,
    )

    assert result["status"] == "completed"
    assert result["fallback_used"] is True
    failed_tools = [event for event in sink.events if event["event_type"] == "tool.failed"]
    assert failed_tools
    assert failed_tools[0]["attributes"]["tool"] == "read_verified_note"
    assert failed_tools[0]["error"]["code"] == "TOOL_EXECUTION_FAILED"
    failed_records = [
        record.to_dict()
        for record in snapshots[-1].tool_ledger
        if record.status == "failed"
    ]
    assert len(failed_records) == 1
    assert failed_records[0]["error"]["code"] == "TOOL_EXECUTION_FAILED"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "private.md" not in serialized
    assert "reader failed" not in serialized
    assert model.requests[1].observation is not None
    assert model.requests[1].observation.status == "ok"
    assert model.requests[2].observation is not None
    assert model.requests[2].observation.error is not None
    assert model.requests[2].observation.error.code == "TOOL_EXECUTION_FAILED"


def test_runtime_binding_preserves_stale_hash_validation(m03_root: Path):
    adapter, vault_root = _sample_adapter(m03_root)
    evidence = adapter._search_notes("Scanner", {}, 1)
    assert evidence

    target = vault_root / evidence[0]["relative_path"]
    target.write_text(target.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")

    runtime = create_retrieval_tool_runtime(
        adapter._search_notes,
        adapter._read_verified_note_for_tool,
    )
    result = runtime.execute(
        ToolCall(
            call_id="call_stale_read",
            tool_id="read_verified_note",
            arguments={"note_ref": evidence[0]["evidence_id"]},
            run_id="run_stale",
            task_id="task_stale",
            agent_id="retrieval_agent",
        ),
        _policy(["read_verified_note"]),
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_EXECUTION_FAILED"


def test_migrated_runtime_keeps_prechecks_before_executor_and_budget():
    calls = []
    runtime = create_retrieval_tool_runtime(
        lambda query, source_context, limit: calls.append((query, source_context, limit)) or [],
        lambda ref: calls.append(ref) or {"ref": ref},
    )

    malformed = runtime.execute(
        ToolCall("call_bad", "search_notes", {"limit": 1}),
        _policy(["search_notes"], max_calls=1),
    )
    denied = runtime.execute(
        ToolCall("call_denied", "search_notes", {"query": "q", "source_context": {}, "limit": 1}),
        _policy([], max_calls=1),
    )
    exhausted = runtime.execute(
        ToolCall("call_exhausted", "search_notes", {"query": "q", "source_context": {}, "limit": 1}),
        _policy(["search_notes"], max_calls=0),
    )
    invalid_read_ref = runtime.execute(
        ToolCall(
            "call_invalid_ref",
            "read_verified_note",
            {"note_ref": "../gold/secret.md"},
        ),
        _policy(["read_verified_note"], max_calls=1),
    )

    assert malformed.error is not None and malformed.error.code == "TOOL_INVALID_ARGUMENTS"
    assert denied.error is not None and denied.error.code == "TOOL_PERMISSION_DENIED"
    assert exhausted.error is not None and exhausted.error.code == "TOOL_BUDGET_EXCEEDED"
    assert invalid_read_ref.error is not None and invalid_read_ref.error.code == "TOOL_INVALID_ARGUMENTS"
    assert calls == []


def test_whitespace_only_read_ref_is_rejected_before_policy_and_executor():
    calls = []
    runtime = create_retrieval_tool_runtime(
        lambda query, source_context, limit: calls.append((query, source_context, limit)) or [],
        lambda ref: calls.append(ref) or {"ref": ref},
    )
    policy = _policy(["read_verified_note"], max_calls=1)

    result = runtime.execute(
        ToolCall("call_whitespace_ref", "read_verified_note", {"note_ref": "   "}),
        policy,
    )

    assert result.error is not None and result.error.code == "TOOL_INVALID_ARGUMENTS"
    assert calls == []
    assert policy.call_count == 0
