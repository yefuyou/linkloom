"""Synthetic product catalogue + real persisted legacy extraction, with no network."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate, serialize_candidate
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, SemanticExtractionContext
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.models import TaskCandidateInput, canonical_json
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import CandidateUnavailableError, ReviewConflictError
from linkloom.tasks.store import TaskStore
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductRepository
from tests.unit.test_task_models import make_candidate


REVIEW_TIME = datetime(2026, 10, 20, 9, tzinfo=UTC)
TEXT = '由李明负责联调报告，2026年10月12日前完成。'
CONTENT = '[2026-10-08T10:00:00+08:00] 主持人: ' + TEXT


class RecordedProvider:
    def __init__(self, claim):
        self.claim, self.calls = claim, 0

    def complete(self, request):
        self.calls += 1
        return ModelResponse(action=ModelAction.tool(ToolCall(call_id='offline-review',
            tool_id=request.available_tools[0].tool_id, arguments={'claims': [deepcopy(self.claim)]})))


def seed(path, *, parts=('联调报告',), content=CONTENT, text=TEXT):
    repository = ProductRepository(path)
    workspace = repository.create_workspace('审核测试')['workspace_id']
    artifact = RawArtifact(workspace_id=workspace, artifact_id='weekly.txt', source_type='timestamped_text',
                           content=content, source_uri=f'linkloom://{workspace}/weekly.txt',
                           ingestion_time=REVIEW_TIME - timedelta(days=3))
    segments = parse_source_text(artifact).segments
    repository.create_source(workspace, artifact.artifact_id, '10/08 周会', artifact,
                             tuple(segment.evidence_span for segment in segments))
    registry = SourceReferenceRegistry()
    registry.register_episode(workspace, source_episode_id_for(artifact))
    for segment in segments:
        registry.register_evidence(workspace, segment.evidence_span.evidence_ref,
                                  content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    temporal = TemporalDecisionStore(path, source_registry=registry)
    claim = dict(claim_type='PLAN', subject=None, relation=None, value=None, temporal_status='NOT_STATED',
        valid_from=None, valid_to=None,
        confidence=dict.fromkeys(('claim_type','entity','relation','temporal','overall'), .95),
        task=dict(text=text, description_parts=list(parts), owner='李明', deadline_text='2026年10月12日',
                  project=None, vendor=None))
    provider = RecordedProvider(claim)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='task-review',
        request_guard=lambda request: ProviderRequestAuthorization.approve(request, guard_id='test',
            reservation_id='test', estimated_cost_upper_bound_usd=0), request_guard_id='test', max_attempts=1)
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    context = SemanticExtractionContext(workspace, FrozenRelationResolver(('uses vendor',)))
    results = [result for segment in segments for result in pipeline.process_segment(segment, artifact, context)]
    materializer = SemanticDecisionMaterializer(temporal)
    for result in results:
        materializer.capture(result, expected_workspace_id=workspace)
    repository.set_ingestion_result(workspace, artifact.artifact_id, artifact.artifact_version_id,
        status='COMPLETED', candidate_count=len(results), review_required_count=0)
    store = TaskStore(path)
    service = TaskReviewService(store, LegacyTaskCandidateSource())
    return SimpleNamespace(path=path, repository=repository, temporal=temporal, store=store,
        service=service, workspace=workspace, artifact=artifact, provider=provider,
        observation=results[0].candidate.task_observation, candidate=results[0].candidate)


def close(app):
    app.store.close()
    app.temporal.close()
    app.repository.close()


@pytest.fixture
def app(tmp_path):
    value = seed(tmp_path / 'product.sqlite')
    try:
        yield value
    finally:
        close(value)


def write_candidate(app, candidate, *, insert=False):
    encoded, fingerprint = serialize_candidate(candidate)
    connection = app.temporal._connection
    if insert:
        # Copy a real persisted row, changing only the independently serialized variant.
        row = dict(connection.execute('SELECT * FROM semantic_candidate LIMIT 1').fetchone())
        row.update(candidate_id=candidate.candidate_id, payload_json=encoded, candidate_fingerprint=fingerprint,
                   extraction_fingerprint=candidate.extraction_fingerprint)
        with connection:
            connection.execute('INSERT INTO semantic_candidate(' + ','.join(row) + ') VALUES(' +
                               ','.join('?' for _ in row) + ')', tuple(row.values()))
    else:
        # Deliberately bypass a guard only in this disposable corruption fixture.
        connection.execute('DROP TRIGGER IF EXISTS semantic_candidate_payload_immutable')
        with connection:
            connection.execute('UPDATE semantic_candidate SET payload_json=?,candidate_fingerprint=? WHERE candidate_id=?',
                               (encoded, fingerprint, candidate.candidate_id))


def preview(app):
    return app.service.preview_candidate(app.workspace, app.observation.observation_id)


def test_real_legacy_adapter_retains_full_payload_evidence_and_original_record_time(app):
    before = [tuple(row) for row in app.temporal._connection.execute('SELECT * FROM semantic_candidate')]
    value = preview(app)
    candidate = TaskCandidateInput.from_dict(value['candidate'])
    assert candidate.title == app.observation.description == '联调报告'
    assert candidate.owner == '李明' and candidate.deadline == '2026-10-12'
    assert candidate.meeting_time == app.observation.event_time != app.observation.recorded_at
    assert candidate.evidence[0].quote == TEXT and candidate.evidence[0].line_start == 1
    assert candidate.evidence[0].source_sha256 == app.artifact.content_hash
    assert value['original_payload'] == json.loads(serialize_candidate(app.candidate)[0])
    assert [tuple(row) for row in app.temporal._connection.execute('SELECT * FROM semantic_candidate')] == before
    assert app.provider.calls == 1


@pytest.mark.parametrize('tamper', ['fingerprint', 'content', 'version', 'source_workspace'])
def test_adapter_rejects_tampered_persistence_and_complete_original_source(app, tamper):
    connection = app.store._connection
    connection.execute('DROP TRIGGER semantic_candidate_payload_immutable')
    if tamper == 'fingerprint':
        connection.execute("UPDATE semantic_candidate SET candidate_fingerprint=?", ('0' * 64,))
    elif tamper == 'content':
        connection.execute("UPDATE ll_source_version SET content=content || '伪造' ")
    elif tamper == 'version':
        connection.execute('UPDATE semantic_candidate SET artifact_version_id=?', ('forged-version',))
        assert app.service.list_candidates(app.workspace) == ()
        return
    else:
        payload = json.loads(serialize_candidate(app.candidate)[0])
        payload['candidate']['workspace_id'] = 'another-workspace'
        encoded = canonical_json(payload)
        fingerprint = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        connection.execute('UPDATE semantic_candidate SET payload_json=?,candidate_fingerprint=?', (encoded, fingerprint))
    with pytest.raises(CandidateUnavailableError):
        app.service.list_candidates(app.workspace)


def test_full_payload_change_not_only_visible_fields_changes_fingerprint_but_import_time_does_not(app):
    original = preview(app)
    changed_time = app.candidate.ingestion_time + timedelta(days=1)
    candidate = replace(app.candidate, ingestion_time=changed_time,
                        task_observation=replace(app.observation, recorded_at=changed_time))
    write_candidate(app, candidate)
    assert preview(app)['fingerprint'] == original['fingerprint']
    changed = replace(candidate, task_observation=replace(candidate.task_observation, vendor='人工篡改但字段自洽'))
    write_candidate(app, changed)
    # An unsupported field alteration is caught by source replay, not just DTO values.
    with pytest.raises(CandidateUnavailableError):
        preview(app)
    write_candidate(app, replace(candidate, confidence=replace(candidate.confidence, overall=.8)))
    new = preview(app)
    assert new['candidate']['title'] == original['candidate']['title']
    assert new['fingerprint'] != original['fingerprint']


def test_conflicting_variants_need_exact_preview_fingerprint_and_new_variant_invalidates_old_preview(app):
    original = preview(app)
    variant = replace(app.candidate, candidate_id='independent-variant',
                      confidence=replace(app.candidate.confidence, overall=.8))
    write_candidate(app, variant, insert=True)
    values = app.service.list_candidates(app.workspace)
    assert len(values) == 2 and all(value['extraction_conflict'] for value in values)
    assert all(value['fingerprint'] != original['fingerprint'] for value in values)
    with pytest.raises(ReviewConflictError):
        preview(app)
    selected = app.service.preview_candidate(app.workspace, app.observation.observation_id,
                                             expected_candidate_fingerprint=values[1]['fingerprint'])
    assert selected['fingerprint'] == values[1]['fingerprint']
    assert selected['original_payload'] == values[1]['original_payload']


def test_unified_v1_wire_payload_and_digest_remain_backward_compatible():
    candidate = make_candidate()
    old_wire = dict(candidate_id=candidate.candidate_id, workspace_id=candidate.workspace_id,
        candidate_version='task-candidate-input/v1', title=candidate.title, owner=candidate.owner,
        deadline=candidate.deadline, deadline_text=candidate.deadline_text, meeting_time=candidate.meeting_time.isoformat(),
        source_decision_id=None, evidence=[e.to_dict() for e in candidate.evidence])
    assert candidate.to_dict() == old_wire and 'source_fingerprint' not in candidate.to_dict()
    assert candidate.fingerprint == hashlib.sha256(canonical_json(old_wire).encode('utf-8')).hexdigest()
    assert TaskCandidateInput.from_dict(old_wire) == candidate
    assert replace(candidate, source_fingerprint='a' * 64).fingerprint != candidate.fingerprint