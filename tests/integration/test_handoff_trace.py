"""Integration tests for handoff trace and events."""

import json
import shutil
from pathlib import Path

import pytest
from linkloom.agents.coordinator import Coordinator
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.agents.registry import create_default_registry
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault


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


def test_handoff_trace_sequence():
    def search_notes_fake(q, ctx, l): return [{"ref": "n1.md"}]
    def read_fake(r): return {"ref": r, "content": "x"}
    def build_fake(l, r): return [{"type": "rel"}]
    def val_ev_fake(r): return {"status": "pass"}
    def val_sch_fake(t, p): return {"status": "pass"}
    
    registry = create_default_registry()
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
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
    
    res = coordinator.run("run_2", "ask", "q", {}, ["a.md"])
    assert res["review"]["decision"] == "evidence_insufficient"


def test_agent_error_emits_fallback():
    def err_func(*args, **kwargs): raise ValueError("err")
    
    registry = create_default_registry()
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
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


def test_runtime_multi_agent_persists_state_and_jsonl_trace(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[2]
    vault_root = tmp_path / "vault"
    shutil.copytree(project_root / "tests" / "fixtures" / "sample_vault", vault_root)
    scan = scan_vault(vault_root, tmp_path / "scan")
    trace_root = tmp_path / "traces"
    checkpoint_root = tmp_path / "checkpoints"
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=scan.index_path,
        checkpoint_dir=checkpoint_root,
        trace_dir=trace_root,
    )

    status = engine.start_multi_agent(
        RunRequest(
            request_id="req_p4_runtime",
            workflow="ask",
            query="Scanner",
            vault_root=str(vault_root),
            dry_run=True,
        )
    )

    assert status.status == "completed"
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    assert state.agent_tasks
    assert all(task["status"] == "completed" for task in state.agent_tasks)
    result_path = checkpoint_root / "results" / status.run_id / "result.json"
    assert result_path.exists()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["workflow"] == "ask"
    assert result["evidence"]

    events_path = trace_root / status.run_id / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event_type"] == "agent.task.created" for event in events)
    assert any(event["event_type"] == "agent.task.completed" for event in events)
    assert all(event["actor"] in {"runtime", "agent"} for event in events)
    assert all("Scanner" not in json.dumps(event, ensure_ascii=False) for event in events)
    manifest = json.loads((trace_root / status.run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
