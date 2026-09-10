"""Integration tests for handoff trace and events."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from linkloom.agents.coordinator import Coordinator
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.agents.registry import create_default_registry
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import (
    PolicySnapshot,
    RunRequest,
    RuntimeState,
    SourceContext,
    TerminationState,
)
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


class TraceEventSink:
    def __init__(self):
        self.events = []
    def emit(self, event_type, actor, status, **kwargs):
        self.events.append({
            "event_type": event_type,
            "actor": actor,
            "status": status,
            "attributes": kwargs.get("attributes", {}),
            "error": kwargs.get("error")
        })


@pytest.fixture
def m03_root(request: pytest.FixtureRequest) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"m03-handoff-{request.node.name}-{uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root


def _model_agent(run_id: str, root: Path, query: str = "q") -> RetrievalAgent:
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

    def search(request: ModelTurnRequest) -> ModelAction:
        return ModelAction.tool(
            ToolCall(
                call_id="model_search",
                tool_id="search_notes",
                arguments={"query": query, "source_context": {}, "limit": 10},
            )
        )

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        return ModelAction.final("model final")

    return RetrievalAgent(
        model=FakeModelAdapter([search, final]),
        initial_state=state,
        artifact_store=ModelArtifactStore(root / f"models-{run_id}"),
        state_checkpoint_callback=lambda current_state: None,
    )


def test_handoff_trace_sequence(m03_root: Path):
    def search_notes_fake(q, ctx, l): return [{"ref": "n1.md"}]
    def read_fake(r): return {"ref": r, "content": "x"}
    def build_fake(l, r): return [{"type": "rel"}]
    def val_ev_fake(r): return {"status": "pass"}
    def val_sch_fake(t, p): return {"status": "pass"}
    
    registry = create_default_registry()
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=_model_agent("run_1", m03_root),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs={
            "search_notes": search_notes_fake,
            "read_verified_note": read_fake,
            "build_pair_signals": build_fake,
            "validate_evidence": val_ev_fake,
            "validate_schema": val_sch_fake
        }
    )
    
    sink = TraceEventSink()
    coordinator.run("run_1", "connect", "q", {}, ["a.md", "b.md"], event_sink=sink)
    
    event_types = [e["event_type"] for e in sink.events]
    assert "run.started" in event_types
    assert "agent.task.created" in event_types
    assert "handoff.requested" in event_types
    assert "agent.fallback.used" not in event_types
    assert "run.completed" in event_types
    
    # check if reviewer insufficient isn't changed
    def val_ev_fail(r): return {"status": "fail"}
    coordinator.tool_funcs["validate_evidence"] = val_ev_fail
    coordinator.retrieval_agent = _model_agent("run_2", m03_root)
    
    res = coordinator.run("run_2", "ask", "q", {}, ["a.md"])
    assert res["review"]["decision"] == "evidence_insufficient"


def test_agent_error_emits_fallback(m03_root: Path):
    def err_func(*args, **kwargs): raise ValueError("err")
    
    registry = create_default_registry()
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=_model_agent("run_3", m03_root),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs={
            "search_notes": err_func,
            "read_verified_note": err_func,
            "validate_evidence": err_func,
            "validate_schema": err_func
        }
    )
    
    sink = TraceEventSink()
    coordinator.run("run_3", "ask", "q", {}, ["a.md"], event_sink=sink)
    
    event_types = [e["event_type"] for e in sink.events]
    assert "agent.task.failed" in event_types
    assert "agent.fallback.used" in event_types


def test_runtime_multi_agent_persists_state_and_jsonl_trace(m03_root: Path):
    project_root = Path(__file__).resolve().parents[2]
    vault_root = m03_root / "vault"
    shutil.copytree(project_root / "tests" / "fixtures" / "sample_vault", vault_root)
    scan = scan_vault(vault_root, m03_root / "scan")
    trace_root = m03_root / "traces"
    checkpoint_root = m03_root / "checkpoints"
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=scan.index_path,
        checkpoint_dir=checkpoint_root,
        trace_dir=trace_root,
        model=_model_agent("unused", m03_root, "Scanner").model,
    )

    status = engine.start_multi_agent(
        RunRequest(
            request_id="req_p4_runtime",
            workflow="ask",
            query="Scanner",
            vault_root=str(vault_root),
            max_provider_requests=8,
            dry_run=True,
        )
    )

    assert status.status == "completed"
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    assert state.agent_tasks
    assert all(task["status"] == "completed" for task in state.agent_tasks)
    assert state.tool_ledger
    assert all(record.status == "completed" for record in state.tool_ledger)
    assert all(record.run_id == status.run_id for record in state.tool_ledger)
    assert state.termination is not None
    assert state.termination.status == "completed"

    checkpoint_history = engine.checkpointer.list_checkpoints(status.thread_id)
    checkpoint_states = [
        engine.checkpointer.load(status.thread_id, item["checkpoint_id"])
        for item in checkpoint_history
    ]
    tool_checkpoint_states = [
        checkpoint_state
        for checkpoint_state in checkpoint_states
        if checkpoint_state.tool_ledger
    ]
    assert tool_checkpoint_states
    final_records = {record.call_id: record for record in state.tool_ledger}
    for call_id in final_records:
        lifecycle = [
            record.status
            for checkpoint_state in tool_checkpoint_states
            for record in checkpoint_state.tool_ledger
            if record.call_id == call_id
        ]
        assert "pending" in lifecycle
        assert lifecycle[-1] == "completed"

    result_path = checkpoint_root / "results" / status.run_id / "result.json"
    assert result_path.exists()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["workflow"] == "ask"
    assert result["evidence"]
    assert result["tool_ledger"]
    assert result["tool_ledger"] == [record.to_dict() for record in state.tool_ledger]
    assert state.model_executions
    assert all(record.request_ref and record.response_ref for record in state.model_executions)
    assert any(
        record.normalized_action and record.normalized_action["kind"] == "final"
        for record in state.model_executions
    )

    events_path = trace_root / status.run_id / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event_type"] == "agent.task.created" for event in events)
    assert any(event["event_type"] == "agent.task.completed" for event in events)
    assert all(event["actor"] in {"runtime", "agent"} for event in events)
    assert all("Scanner" not in json.dumps(event, ensure_ascii=False) for event in events)
    manifest = json.loads((trace_root / status.run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
