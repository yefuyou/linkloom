"""Current negative claims need the same lifecycle validity as positive ones."""
from dataclasses import replace

import pytest

from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.tools.task_relations import TaskRelationTools, _relation_status
from tests.unit.test_task_investigation_p4_steps import _fixture, _plan, _step
from tests.unit.test_task_relation_query import retract


@pytest.mark.parametrize(('case_id', 'verdict'), [('D01', 'REFUTED'), ('V04', 'CONFLICT')])
@pytest.mark.parametrize('invalidation', ['retracted', 'version', 'permission'])
def test_historical_negative_does_not_become_current_tool_verdict(tmp_path, case_id, verdict, invalidation):
    path = tmp_path / 'relations.sqlite'
    case, _, snapshot, identity, revision = _fixture(path, case_id)
    workspace = case['workspace_id']
    assert revision.verdict == verdict
    # Inspect the revision without a dependent identity step: both are legitimate
    # P4 read operations, and historical rows are explicitly requested below.
    step = _step('revision', 'relation', 'REVISES_PLAN',
                 (revision.subject, revision.object), workspace)
    tools = TaskRelationTools(workspace, lambda: snapshot, relation_path=path,
                              inspection_plan=_plan(workspace, (step,)))
    assert tools.query_task_relations(workspace)['investigation_steps'][0]['status'] == verdict
    if invalidation == 'retracted':
        retract(path, workspace, revision.assessment_id)
    else:
        span = revision.dependencies[0].span
        if invalidation == 'permission':
            snapshot.source_registry.unregister_evidence(workspace, span.evidence_ref)
        else:
            snapshot.source_registry.register_evidence(workspace, span.evidence_ref,
                content_hash=span.content_hash, source_version='new-version')
    before = path.read_bytes()
    result = tools.query_task_relations(workspace, include_history=True)
    row = result['investigation_steps'][0]
    assert row['relation_reads'][0]['category'] == 'HISTORICAL'
    assert row['relation_reads'][0]['current_supported'] is False
    assert row['status'] == 'BLOCKED'
    assert row['current_supported'] is False
    assert row['blocking_reasons']
    assert path.read_bytes() == before


def test_historical_refutation_cannot_override_current_unknown(tmp_path):
    path = tmp_path / 'relations.sqlite'
    case, _, snapshot, _, revision = _fixture(path, 'D01')
    with RelationQuery(path, artifacts=snapshot.artifacts, source_registry=snapshot.source_registry,
                       observations=snapshot.observations) as query:
        current = query.get(case['workspace_id'], revision.assessment_id)
    historical = replace(current, persistent_lifecycle='RETRACTED', category='HISTORICAL',
                         blocking_reasons=('LIFECYCLE_RETRACTED',))
    # This is a projection contract variation, not an additional model failure.
    unknown = replace(current, assessment=replace(current.assessment, verdict='UNKNOWN'))
    assert _relation_status((historical, unknown))[0] == 'UNKNOWN'
