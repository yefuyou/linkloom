"""Durable P2-B regression for non-positive SAME_TASK assessments.

The assessments in this file are produced by the public identity evaluator from
small synthetic source documents.  The runtime then persists the real inspect
receipt before querying the relation store; no relation verdict or dependency
outcome is supplied by the test.
"""

from datetime import UTC, datetime
from dataclasses import replace

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.integration.test_task_relation_runtime import execute, script
from tests.unit.test_task_investigation_p4_steps import _plan, _step


def _observe(text, *, source_id, workspace, project="青岚项目"):
    artifact = RawArtifact(
        workspace_id=workspace,
        artifact_id=source_id,
        source_type="manual_text",
        content=text,
        ingestion_time=datetime(2026, 10, 18, tzinfo=UTC),
    )
    segment = parse_source_text(artifact).segments[0]
    observation = build_task_observation(
        TaskProposal(text, (text,), project=project), segment, artifact
    )
    assert observation.verify(artifact)
    return artifact, observation


def _identity_case(kind):
    workspace = "ws-p2b-f-" + kind.lower()
    if kind == "UNKNOWN":
        left = _observe(
            "青岚项目：负责人：许南；完成报表导出；截止日期：2028-01-14。",
            source_id="A.md", workspace=workspace,
        )
        right = _observe(
            "青岚项目：负责人：许南；完成报表导出适配；截止日期：2028-01-18。",
            source_id="B.md", workspace=workspace,
        )
    elif kind == "REFUTED":
        left = _observe(
            "青岚项目任务 FLOW-31：完成报表导出。",
            source_id="A.md", workspace=workspace,
        )
        right = _observe(
            "青岚项目任务 FLOW-32：完成沙箱导出；这是独立任务，并非A.md的FLOW-31。",
            source_id="B.md", workspace=workspace,
        )
    elif kind == "CONFLICT":
        left = _observe(
            "青岚项目任务 FLOW-31：完成报表导出。",
            source_id="A.md", workspace=workspace,
        )
        right = _observe(
            "青岚项目的导出适配。本条与A.md的FLOW-31是同一个任务。"
            "本条不是A.md的FLOW-31，是独立任务。",
            source_id="B.md", workspace=workspace,
        )
    else:  # pragma: no cover - the parameter list is the contract
        raise AssertionError(kind)

    artifacts = (left[0], right[0])
    observations = (left[1], right[1])
    registry = SourceReferenceRegistry()
    for artifact in artifacts:
        for segment in parse_source_text(artifact).segments:
            registry.register_evidence(
                workspace,
                segment.evidence_span.evidence_ref,
                content_hash=artifact.content_hash,
                source_version=artifact.artifact_version_id,
            )
    # This is the production evaluator under test.  The returned assessment is
    # the only relation record written to the durable sidecar below.
    assessment = assess_same_task(
        right[1], left[1], artifacts=artifacts, source_registry=registry,
        context_observations=(),
    )
    return workspace, artifacts, observations, registry, assessment


@pytest.mark.parametrize("expected", ["UNKNOWN", "REFUTED", "CONFLICT"])
def test_completed_inspection_keeps_nonpositive_same_task_and_blocks_positive_dependents(
    tmp_path, expected,
):
    workspace, artifacts, observations, registry, assessment = _identity_case(expected)
    assert assessment.verdict == expected
    path = tmp_path / "relations.sqlite"
    with TaskRelationStore(path) as store:
        store.put_assessment(assessment)

    snapshot = TaskContextSnapshot(workspace, observations, artifacts, registry)
    inspect = replace(
        _step("inspect_owner", "inspect", "", (assessment.subject,), workspace),
        field="owner",
    )
    identity = _step(
        "identity", "relation", "SAME_TASK",
        (assessment.subject, assessment.object), workspace,
        ("inspect_owner",),
    )
    # REVISES_PLAN is an intentionally positive downstream question.  Its
    # dependency is blocked by the non-positive SAME_TASK verdict even though
    # the inspect receipt itself is complete and current.
    revision = _step(
        "revision", "relation", "REVISES_PLAN",
        (assessment.subject, assessment.object), workspace,
        ("identity",),
    )
    plan = _plan(workspace, (inspect, identity, revision))
    tools = TaskRelationTools(
        workspace,
        lambda: snapshot,
        relation_path=path,
        inspection_plan=plan,
        inspection_snapshot_hash=plan.snapshot_hash,
    )

    status, state, result, _ = execute(tmp_path, tools, model=script(workspace))

    assert status.status == "completed", (status.to_dict(), result)
    assert result["review"]["decision"] == "evidence_sufficient"
    query = result["result"]["task_relations"]["relation_query"]
    rows = {row["step_id"]: row for row in query["investigation_steps"]}

    # The query was allowed to consume only the completed inspection record;
    # the non-positive P4 verdict remains visible and cannot be promoted.
    assert rows["identity"]["dependency_outcomes"] == {"inspect_owner": "COMPLETED"}
    assert rows["identity"]["status"] == expected
    assert rows["identity"]["current_supported"] is False
    assert "P4_VERDICT_" + expected in rows["identity"]["blocking_reasons"]
    assert rows["revision"]["dependency_outcomes"] == {"identity": expected}
    assert rows["revision"]["status"] == "BLOCKED"
    assert rows["revision"]["current_supported"] is False
    assert rows["revision"]["blocking_reasons"]

    # These assertions read the checkpoint-reloaded ledger, proving that the
    # inspect evidence used above was durable rather than a local fixture.
    records = tuple(state.tool_ledger)
    assert [record.tool_id for record in records] == [
        "inspect_task_context", "query_task_relations",
    ]
    assert all(record.status == "completed" for record in records)
    inspect_record = records[0]
    assert tools.inspection_receipts_from_record(inspect_record)
    assert state.model_executions and len(state.model_executions) == 3
