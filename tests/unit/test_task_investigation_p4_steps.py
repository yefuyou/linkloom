from dataclasses import dataclass

import pytest

from linkloom.semantic_ingestion.task_relation_models import ObservationRef, RelationAssessment, canonical_hash
from linkloom.semantic_ingestion.task_relation_question_models import InvestigationPlan, InvestigationStep
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.unit.test_task_relation_query import seed


def test_product_source_registry_requires_current_catalog_membership():
    from datetime import UTC, datetime
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.document_text import parse_source_text
    from linkloom.ui.task_investigation import ProductTaskSources
    artifact = RawArtifact('workspace', 'src_' + 'a' * 32, 'timestamped_text',
        '[2029-06-01T09:00:00Z] 林枫: 完成索引迁移。')
    source = dict(source_id=artifact.artifact_id, source_version=artifact.artifact_version_id,
        content=artifact.content, name='meeting.txt', created_at=datetime.now(UTC).isoformat())
    span = parse_source_text(artifact).segments[0].evidence_span
    evidence = [dict(source_id=artifact.artifact_id, artifact_version_id=artifact.artifact_version_id,
        evidence_ref=span.evidence_ref, content_sha256=span.content_hash, quote_sha256=span.quote_sha256,
        line_start=span.line_start, line_end=span.line_end, quote=span.quote)]
    current = list(evidence)
    sources = ProductTaskSources('workspace', (source,), lambda: (source,), current_evidence=lambda: current)
    sources.validate_current()
    current.clear()
    with pytest.raises(ValueError, match='SOURCE_EVIDENCE_NOT_CURRENT'):
        sources.validate_current()
    with pytest.raises(ValueError, match='SOURCE_EVIDENCE_NOT_CURRENT'):
        ProductTaskSources('workspace', (source,), lambda: (source,), current_evidence=lambda: current)


def _step(step_id, kind, predicate, endpoints, workspace, depends_on=(), *, time_scope=("", ""),
          source_bindings=(), source_handles=()):
    return InvestigationStep(
        id=step_id,
        kind=kind,
        question=step_id,
        handles=tuple("endpoint_" + str(index) for index in range(len(endpoints))),
        endpoints=tuple(endpoints),
        source_handles=tuple(source_handles),
        depends_on=tuple(depends_on),
        predicate=predicate,
        field="",
        target_kind="",
        missing_entities=(),
        workspace_id=workspace,
        source_bindings=tuple(source_bindings),
        time_scope=time_scope,
    )


def _fixture(path, case_id="D04"):
    case, observations, artifacts, registry, rows, _ = seed(path, case_id)
    identity = RelationAssessment.from_dict(rows[0]["same_task"])
    revision = RelationAssessment.from_dict(rows[0]["revision"])
    snapshot = TaskContextSnapshot(case["workspace_id"], tuple(observations.values()),
                                   tuple(artifacts.values()), registry)
    return case, observations, snapshot, identity, revision


def _plan(workspace, steps):
    return InvestigationPlan("bounded relation investigation", workspace,
                             canonical_hash([workspace, "m3-p"]), tuple(steps), (), ())


def test_query_projects_exact_p4_relation_and_replayed_comparison(tmp_path):
    case, observations, snapshot, identity, revision = _fixture(tmp_path / "p4.sqlite")
    identity_endpoints = (identity.subject, identity.object)
    revision_endpoints = (revision.subject, revision.object)
    plan = _plan(case["workspace_id"], (
        _step("identity", "relation", "SAME_TASK", tuple(reversed(identity_endpoints)), case["workspace_id"]),
        _step("revision", "relation", "REVISES_PLAN", revision_endpoints, case["workspace_id"], ("identity",)),
        _step("dates", "compare_dates", "", revision_endpoints, case["workspace_id"], ("identity", "revision")),
    ))
    tools = TaskRelationTools(case["workspace_id"], lambda: snapshot,
                               relation_path=tmp_path / "p4.sqlite", inspection_plan=plan)

    result = tools.query_task_relations(case["workspace_id"], as_of="2027-01-01")
    by_id = {row["step_id"]: row for row in result["investigation_steps"]}
    assert by_id["identity"]["status"] == "SUPPORTED"
    assert by_id["identity"]["dependency_outcomes"] == {}
    assert by_id["identity"]["assessment_ids"] == [identity.assessment_id]
    assert by_id["identity"]["relation_reads"][0]["predicate"] == "SAME_TASK"
    assert by_id["identity"]["relation_reads"][0]["current_supported"] is True
    assert by_id["identity"]["relation_reads"][0]["persistent_lifecycle"] == "ACTIVE"
    assert by_id["revision"]["status"] == "SUPPORTED"
    assert by_id["revision"]["dependency_outcomes"] == {"identity": "SUPPORTED"}
    assert by_id["dates"]["status"] == "COMPLETED"
    assert by_id["dates"]["comparison_current_valid"] is True
    assert by_id["dates"]["comparisons"][0]["current_comparison"]["status"] == "COMPUTABLE"
    assert result == tools.query_task_relations(case["workspace_id"], as_of="2027-01-01")

    # Compaction keeps the complete ObservationRef in the catalog while the
    # step view carries only a deterministic reference.
    subject = by_id["identity"]["relation_reads"][0]["subject"]
    assert result["observation_refs"][subject["observation_ref"]] == identity.subject.to_dict()


@pytest.mark.parametrize(("case_id", "step_id", "predicate", "expected"), (
    ("D01", "revision", "REVISES_PLAN", "REFUTED"),
    ("D07", "identity", "SAME_TASK", "UNKNOWN"),
    ("V04", "revision", "REVISES_PLAN", "CONFLICT"),
))
def test_non_supported_p4_verdicts_remain_visible_and_block_dependents(
        tmp_path, case_id, step_id, predicate, expected):
    case, _, snapshot, identity, revision = _fixture(tmp_path / (case_id + ".sqlite"), case_id)
    relation = revision if step_id == "revision" else identity
    dependency = ("identity",) if step_id == "revision" else ()
    steps = []
    if step_id == "revision":
        steps.append(_step("identity", "relation", "SAME_TASK",
                           (identity.subject, identity.object), case["workspace_id"]))
    steps.append(_step(step_id, "relation", predicate,
                       (relation.subject, relation.object), case["workspace_id"], dependency))
    result = TaskRelationTools(case["workspace_id"], lambda: snapshot,
                               relation_path=tmp_path / (case_id + ".sqlite"),
                               inspection_plan=_plan(case["workspace_id"], steps)).query_task_relations(
                                   case["workspace_id"])
    row = next(item for item in result["investigation_steps"] if item["step_id"] == step_id)
    assert row["status"] == expected
    assert row["current_supported"] is False
    assert row["assessment_ids"] == [relation.assessment_id]
    assert row["blocking_reasons"]
    assert "P4_VERDICT_" + expected in row["blocking_reasons"]


def test_missing_sidecar_and_unsupported_scope_explain_blocked_steps(tmp_path):
    case, _, snapshot, identity, _ = _fixture(tmp_path / "source.sqlite")
    step = _step("identity", "relation", "SAME_TASK", (identity.subject, identity.object),
                 case["workspace_id"], time_scope=("2029-01-01", "2029-01-03"))
    result = TaskRelationTools(case["workspace_id"], lambda: snapshot,
                               relation_path=tmp_path / "not-created.sqlite",
                               inspection_plan=_plan(case["workspace_id"], (step,))).query_task_relations(
                                   case["workspace_id"])
    row = result["investigation_steps"][0]
    assert row["status"] == "BLOCKED"
    assert {"NO_RELATION_RECORDS", "TIME_SCOPED_OPERATION_UNSUPPORTED"} <= set(row["blocking_reasons"])
    assert row["assessment_ids"] == []


def test_source_binding_and_for_run_configuration_are_preserved(tmp_path):
    case, _, snapshot, identity, _ = _fixture(tmp_path / "p4.sqlite")
    step = _step("identity", "relation", "SAME_TASK", (identity.subject, identity.object),
                 case["workspace_id"], source_bindings=(identity.observations[0].provenance,))
    snapshot.source_registry.register_evidence(case["workspace_id"], identity.observations[0].provenance.evidence_ref,
                                               content_hash=identity.observations[0].provenance.content_hash,
                                               source_version="replacement")

    @dataclass
    class Semantic:
        def bind(self, run_id, artifact_store, question):
            return self

    tools = TaskRelationTools(case["workspace_id"], lambda: snapshot,
                              relation_path=tmp_path / "p4.sqlite", semantic_analysis=Semantic(),
                              inspection_plan=_plan(case["workspace_id"], (step,)))
    run_tools = tools.for_run("run", object(), "question")
    assert run_tools._relation_path == tools._relation_path
    assert run_tools.inspection_plan == tools.inspection_plan
    row = run_tools.query_task_relations(case["workspace_id"])["investigation_steps"][0]
    assert row["status"] == "BLOCKED"
    assert "SOURCE_BINDING_NOT_CURRENT" in row["blocking_reasons"]


def test_source_scoped_steps_do_not_consume_unscoped_p4_reads(tmp_path):
    case, _, snapshot, identity, _ = _fixture(tmp_path / "p4.sqlite")
    step = _step("identity", "relation", "SAME_TASK", (identity.subject, identity.object),
                 case["workspace_id"], source_handles=("source_a",))
    result = TaskRelationTools(case["workspace_id"], lambda: snapshot,
                               relation_path=tmp_path / "p4.sqlite",
                               inspection_plan=_plan(case["workspace_id"], (step,))).query_task_relations(
                                   case["workspace_id"])
    row = result["investigation_steps"][0]
    assert row["status"] == "BLOCKED"
    assert "SOURCE_SCOPED_OPERATION_UNSUPPORTED" in row["blocking_reasons"]
