"""Integration tests for P4 multi-agent workflow."""

import shutil
from pathlib import Path

import pytest
from linkloom.agents.coordinator import Coordinator
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.agents.registry import create_default_registry
from linkloom.loader import VaultReader
from linkloom.retrieval import retrieve_evidence
from linkloom.scanner import scan_vault


class FakeEventSink:
    def __init__(self):
        self.events = []
    def emit(self, event_type, actor, status, **kwargs):
        self.events.append((event_type, actor, status, kwargs))


def search_notes_fake(query, source_context, limit):
    return [{"ref": "note1.md"}, {"ref": "note2.md"}]


def read_verified_note_fake(ref):
    if "error" in ref:
        raise ValueError("Simulated read error")
    return {"ref": ref, "content": "Fake content", "status": "verified"}


def build_pair_signals_fake(left, right):
    return [{"type": "relation", "source": left, "target": right}]


def validate_evidence_fake(ref):
    return {"status": "pass", "ref": ref}


def validate_schema_fake(result_type, payload):
    return {"status": "pass", "payload": payload}


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"


def make_real_tools(tmp_path: Path):
    vault_root = tmp_path / "vault"
    shutil.copytree(FIXTURE_ROOT, vault_root)
    scan = scan_vault(vault_root, tmp_path / "scan")
    reader = VaultReader(vault_root=vault_root, index_path=scan.index_path)
    documents = reader.read_notes()
    evidence_by_id = {}

    def search(query, source_context, limit):
        evidence = retrieve_evidence(query, documents, max_results=limit)
        values = [item.to_dict() for item in evidence]
        evidence_by_id.update({item["evidence_id"]: item for item in values})
        return values

    def read(ref):
        if ref in evidence_by_id:
            return evidence_by_id[ref]
        for document in documents:
            if document.relative_path == ref:
                return document.to_dict()
        raise ValueError(f"unknown verified ref: {ref}")

    def validate_evidence(ref):
        item = evidence_by_id.get(ref)
        return {"status": "pass", "ref": ref} if item and item["status"] == "verified" else {"status": "fail", "ref": ref}

    return vault_root, scan.index_path, reader.get_source_context().to_dict(), {
        "search_notes": search,
        "read_verified_note": read,
        "validate_evidence": validate_evidence,
        "validate_schema": validate_schema_fake,
    }


def test_ask_workflow_synthetic():
    registry = create_default_registry()
    tool_funcs = {
        "search_notes": search_notes_fake,
        "read_verified_note": read_verified_note_fake,
        "validate_evidence": validate_evidence_fake,
        "validate_schema": validate_schema_fake
    }
    
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs
    )
    
    sink = FakeEventSink()
    result = coordinator.run(
        run_id="run_ask_1",
        workflow="ask",
        query="test query",
        source_context={},
        initial_refs=["initial.md"],
        event_sink=sink
    )
    
    assert result["status"] == "completed"
    assert result["workflow"] == "ask"
    assert not result["fallback_used"]
    assert len(result["evidence"]) == 2
    assert result["evidence"] == ["note1.md", "note2.md"]
    assert result["review"]["decision"] == "evidence_sufficient"
    assert len(result["agent_tasks"]) == 2  # Retrieval, Reviewer
    assert len(result["handoffs"]) == 0


def test_connect_workflow_synthetic():
    registry = create_default_registry()
    tool_funcs = {
        "search_notes": search_notes_fake,
        "read_verified_note": read_verified_note_fake,
        "build_pair_signals": build_pair_signals_fake,
        "validate_evidence": validate_evidence_fake,
        "validate_schema": validate_schema_fake
    }
    
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs
    )
    
    sink = FakeEventSink()
    result = coordinator.run(
        run_id="run_connect_1",
        workflow="connect",
        query="test connect",
        source_context={},
        initial_refs=["noteA.md", "noteB.md"],
        event_sink=sink
    )
    
    assert result["status"] == "completed"
    assert result["workflow"] == "connect"
    assert not result["fallback_used"]
    assert len(result["result"]["candidate_refs"]) == 1
    assert result["review"]["decision"] == "evidence_sufficient"
    assert len(result["agent_tasks"]) == 3  # Retrieval, Curator, Reviewer
    assert len(result["handoffs"]) == 1
    assert result["handoffs"][0]["task_id"] == result["agent_tasks"][1]["task_id"]
    assert result["handoffs"][0]["status"] == "completed"
    assert all(task["status"] == "completed" for task in result["agent_tasks"])
    assert result["agent_tasks"][1]["parent_task_id"] == result["agent_tasks"][0]["task_id"]
    assert result["agent_tasks"][2]["parent_task_id"] == result["agent_tasks"][1]["task_id"]


def test_fallback_visibility():
    def search_error(*args, **kwargs):
        raise ValueError("Provider timeout")
        
    registry = create_default_registry()
    tool_funcs = {
        "search_notes": search_error,
        "read_verified_note": read_verified_note_fake,
        "validate_evidence": validate_evidence_fake,
        "validate_schema": validate_schema_fake
    }
    
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs
    )
    
    result = coordinator.run(
        run_id="run_fallback",
        workflow="ask",
        query="timeout test",
        source_context={},
        initial_refs=["fallback.md"],
        event_sink=FakeEventSink()
    )
    
    assert result["status"] == "completed"  # Fallback recovers it
    assert result["fallback_used"] is True
    assert result["evidence"] == []


def test_duplicate_input_cycle_prevention():
    registry = create_default_registry()
    tool_funcs = {
        "search_notes": search_notes_fake,
        "read_verified_note": read_verified_note_fake,
        "validate_evidence": validate_evidence_fake,
        "validate_schema": validate_schema_fake
    }
    
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs
    )
    
    # Intentionally trigger duplicate ref inputs
    coordinator._seen_inputs.add(tuple(sorted(["initial.md"])))
    
    result = coordinator.run(
        run_id="run_cycle",
        workflow="ask",
        query="cycle query",
        source_context={},
        initial_refs=["initial.md"]
    )
    
    assert result["status"] == "failed"
    assert any("Duplicate" in e for e in result["errors"])


def test_real_fixture_ask_returns_verified_evidence_refs(tmp_path: Path):
    vault_root, index_path, source_context, tool_funcs = make_real_tools(tmp_path)
    coordinator = Coordinator(
        registry=create_default_registry(),
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs,
    )

    result = coordinator.run(
        run_id="run_real_ask",
        workflow="ask",
        query="Scanner",
        source_context=source_context,
        initial_refs=["request_real"],
    )

    assert result["status"] == "completed"
    assert result["evidence"]
    assert all(ref.startswith("ev_p1_") for ref in result["evidence"])
    assert result["review"]["decision"] == "evidence_sufficient"
    assert index_path.exists()
    assert not any(path.name == "result.json" for path in vault_root.rglob("result.json"))


def test_coordinator_limits_and_state_are_per_run():
    registry = create_default_registry()
    tool_funcs = {
        "search_notes": search_notes_fake,
        "read_verified_note": read_verified_note_fake,
        "validate_evidence": validate_evidence_fake,
        "validate_schema": validate_schema_fake,
    }
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs,
    )
    coordinator.max_total_steps = 0
    limited = coordinator.run("run_limit", "ask", "q", {}, ["initial.md"])
    assert limited["status"] == "failed"
    assert any("max_total_steps" in error for error in limited["errors"])

    coordinator.max_total_steps = 12
    first = coordinator.run("run_first", "ask", "q", {}, ["initial.md"])
    second = coordinator.run("run_second", "ask", "q", {}, ["initial.md"])
    assert first["status"] == "completed"
    assert second["status"] == "completed"


def test_failed_curator_does_not_emit_illegal_handoff_status():
    registry = create_default_registry()
    tool_funcs = {
        "search_notes": search_notes_fake,
        "read_verified_note": read_verified_note_fake,
        "build_pair_signals": lambda left, right: (_ for _ in ()).throw(ValueError("curator timeout")),
        "validate_evidence": validate_evidence_fake,
        "validate_schema": validate_schema_fake,
    }
    coordinator = Coordinator(
        registry=registry,
        retrieval_agent=RetrievalAgent(),
        curator_agent=CuratorAgent(),
        reviewer_agent=ReviewerAgent(),
        tool_funcs=tool_funcs,
    )
    result = coordinator.run("run_curator_failure", "connect", "q", {}, ["a.md", "b.md"])
    assert result["handoffs"][0]["status"] == "rejected"
    assert result["review"]["decision"] == "needs_human"
