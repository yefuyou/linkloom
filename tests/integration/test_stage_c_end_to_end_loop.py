from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.run_stage_c_context_memory_loop import build_stage_c_artifact


@pytest.fixture
def stage_c_root(request: pytest.FixtureRequest) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"stage-c-{request.node.name}-{uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_stage_c_canonical_trace_proves_grounded_supersession_loop(
    stage_c_root: Path,
) -> None:
    artifact_path = stage_c_root / "stage-c-trace.json"

    report = build_stage_c_artifact(artifact_path)
    persisted = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert persisted == report
    canonical = report["canonical_trace"]
    assert canonical["file"]["old_hash"] != canonical["file"]["new_hash"]
    assert canonical["index"]["updated_components"] == [
        "lexical",
        "vector",
        "directory",
        "manifest",
    ]
    assert canonical["index"]["changed_vector_documents"] == 1
    assert canonical["retrieval"]["old_current_evidence_count"] == 0
    assert canonical["retrieval"]["new_evidence"]["content_sha256"] == canonical["file"]["new_hash"]
    assert canonical["context"]["before"]["evidence_hashes"] != canonical["context"]["after"]["evidence_hashes"]
    assert canonical["agent"]["before"]["answer"] != canonical["agent"]["after"]["answer"]
    assert canonical["grounding"]["passed"] is True
    assert canonical["memory"]["current_before"]["value"] == "Supplier A"
    assert canonical["memory"]["current_after"]["value"].startswith("Supplier B")
    assert canonical["memory"]["history"][0]["status"] == "SUPERSEDED"
    assert canonical["memory"]["history"][0]["decision_id"] == "decision-a"
    assert canonical["provenance"]["reconciliation_issue_count"] == 0


def test_stage_c_trace_proves_lost_event_repair_and_workspace_isolation(
    stage_c_root: Path,
) -> None:
    report = build_stage_c_artifact(stage_c_root / "stage-c-trace.json")

    lost_event = report["lost_event_trace"]
    assert lost_event["file"]["old_hash"] != lost_event["file"]["new_hash"]
    assert lost_event["before_reconcile"]["exposed_evidence_count"] == 0
    assert "hash_drift" in lost_event["reconciliation"]["issue_types"]
    assert lost_event["reconciliation"]["repaired_count"] >= 1
    assert lost_event["after"]["agent_answer"].startswith("Supplier B")
    assert lost_event["after"]["grounding_passed"] is True
    assert lost_event["after"]["index_hash"] == lost_event["file"]["new_hash"]

    isolation = report["workspace_isolation_trace"]
    assert isolation["workspace_b_canary_is_strongest_match"] is True
    assert isolation["workspace_a_evidence_count"] >= 1
    assert isolation["workspace_a_exposed_canary_count"] == 0
    assert isolation["unauthorized_memory_rejected"] is True
    assert isolation["store_reads_before_authorized_lookup"] == 0
    assert isolation["agent_evidence_workspaces"] == ["workspace-a"]
    assert isolation["agent_memory_workspaces"] == ["workspace-a"]
    assert isolation["grounding_passed"] is True

    trace = report["unified_trace"]
    assert [event["sequence"] for event in trace] == list(range(1, len(trace) + 1))
    event_types = {event["event_type"] for event in trace}
    assert {
        "file.change.applied",
        "index.updated",
        "retrieval.completed",
        "context.assembled",
        "decision_memory.completed",
        "agent.completed",
        "grounding.completed",
        "decision_memory.materialized",
        "retrieval.stale_source_blocked",
        "index.reconciled",
        "security.workspace_scope_enforced",
    }.issubset(event_types)

    canonical_events = [
        event for event in trace if event["scenario"] == "canonical_event_update"
    ]
    canonical_types = [event["event_type"] for event in canonical_events]
    assert canonical_types.index("file.change.applied") < canonical_types.index(
        "index.updated"
    ) < canonical_types.index("context.assembled") < canonical_types.index(
        "retrieval.completed"
    )
    assert canonical_types.index("retrieval.completed") < canonical_types.index(
        "decision_memory.completed"
    ) < canonical_types.index("agent.completed") < canonical_types.index(
        "grounding.completed"
    ) < canonical_types.index("decision_memory.materialized")

    lost_events = [
        event
        for event in trace
        if event["scenario"] == "lost_event_reconciliation"
    ]
    lost_types = [event["event_type"] for event in lost_events]
    assert lost_types.index("file.change.event_lost") < lost_types.index(
        "retrieval.stale_source_blocked"
    ) < lost_types.index("index.reconciled") < lost_types.index(
        "retrieval.completed"
    ) < lost_types.index("decision_memory.completed") < lost_types.index(
        "agent.completed"
    )
