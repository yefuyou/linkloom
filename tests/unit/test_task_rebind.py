"""Human revision binding on real, offline legacy candidates and synthetic SQLite files."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from threading import Barrier

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
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
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskNotFoundError, TaskSchemaError, TaskStore
from tests.unit.test_task_legacy_adapter import (app, seed, close, preview, write_candidate, RecordedProvider,
                                                CONTENT, TEXT)


NOW = datetime(2030, 1, 1, tzinfo=UTC)
NEW_TEXT = '由王芳负责联调报告，2026年10月16日前完成。'
NEW_CONTENT = '[2026-10-08T10:00:00+08:00] 主持人: ' + NEW_TEXT


def original_task(app, *, legacy=False, candidate=None):
    if legacy:
        candidate = candidate or TaskCandidateInput.from_dict(preview(app)['candidate'])
        task = app.store.create_from_candidate(app.workspace, candidate,
            reviewer='旧版审核人', reason='首次确认', now=NOW)
        return app.service.get_task(app.workspace, task.id, now=NOW)
    return app.service.confirm(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=preview(app)['fingerprint'], reviewer='首次审核人', reason='首次确认', now=NOW)


def revise(app, *, content=NEW_CONTENT, text=NEW_TEXT, owner='王芳', deadline='2026年10月16日',
           parts=('联调报告',), source_id=None, extract=True):
    source_id = source_id or app.artifact.artifact_id
    artifact = RawArtifact(workspace_id=app.workspace, artifact_id=source_id,
        source_type='timestamped_text', content=content, source_uri=f'linkloom://{app.workspace}/{source_id}')
    segments = parse_source_text(artifact).segments
    spans = tuple(segment.evidence_span for segment in segments)
    if source_id == app.artifact.artifact_id:
        app.repository.update_source(app.workspace, source_id, '人工修订的同一原文', artifact, spans)
    else:
        app.repository.create_source(app.workspace, source_id, '另一来源', artifact, spans)
    results = []
    if extract:
        registry = app.temporal.source_registry
        registry.register_episode(app.workspace, source_episode_id_for(artifact))
        for span in spans:
            registry.register_evidence(app.workspace, span.evidence_ref,
                                      content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
        claim = dict(claim_type='PLAN', subject=None, relation=None, value=None, temporal_status='NOT_STATED',
            valid_from=None, valid_to=None,
            confidence=dict.fromkeys(('claim_type','entity','relation','temporal','overall'), .95),
            task=dict(text=text, description_parts=list(parts), owner=owner, deadline_text=deadline,
                      project=None, vendor=None))
        provider = RecordedProvider(claim)
        extractor = ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='revision-review',
            request_guard=lambda request: ProviderRequestAuthorization.approve(request, guard_id='test',
                reservation_id='test', estimated_cost_upper_bound_usd=0), request_guard_id='test', max_attempts=1)
        pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
        context = SemanticExtractionContext(app.workspace, FrozenRelationResolver(('uses vendor',)))
        results = [result for segment in segments for result in pipeline.process_segment(segment, artifact, context)]
        for result in results:
            SemanticDecisionMaterializer(app.temporal).capture(result, expected_workspace_id=app.workspace)
    app.repository.set_ingestion_result(app.workspace, source_id, artifact.artifact_version_id,
        status='COMPLETED', candidate_count=len(results), review_required_count=0)
    return artifact, results[0].candidate if results else None


def binding_preview(app, initial, candidate, *, now=NOW + timedelta(days=1)):
    raw = app.service.preview_candidate(app.workspace, candidate.task_observation.observation_id)
    return app.service.preview_rebind(app.workspace, initial['task']['id'], candidate.task_observation.observation_id,
                                      expected_candidate_fingerprint=raw['fingerprint'], now=now)


def apply_binding(app, value, choices=None, *, service=None, now=NOW + timedelta(days=2), **options):
    return (service or app.service).rebind(app.workspace, value['task']['id'],
        value['new_candidate']['candidate']['candidate_id'],
        expected_candidate_fingerprint=value['new_candidate_fingerprint'],
        expected_review_revision=value['review_revision'], expected_preview_token=value['preview_token'],
        choices=choices if choices is not None else {field: {'choice': 'candidate'} for field in value['required_choices']},
        reviewer='重绑审核人', reason='人工比对同一次原文修订', now=now, **options)


def prepare_invalidation(app, initial, *, content='新版记录已删除本任务及全部会议时间。'):
    artifact, _ = revise(app, content=content, extract=False)
    value = app.service.preview_invalidation(app.workspace, initial['task']['id'], artifact.artifact_version_id,
                                             now=NOW + timedelta(days=1))
    return artifact, value


def apply_invalidation(app, value, *, service=None, **options):
    args = dict(expected_review_revision=value['review_revision'], expected_preview_token=value['preview_token'],
                attest_same_meeting=True, attest_task_absent=True, reviewer='人工核实人',
                reason='逐字看过新版，仍属同次会议且已无此项', now=NOW + timedelta(days=2))
    args.update(options)
    return (service or app.service).invalidate(app.workspace, value['task']['id'], value['source_version_id'], **args)


def undo(app, invalidated, *, service=None, **options):
    args = dict(target_review_event_id=invalidated['review_history'][-1]['id'],
                expected_review_revision=invalidated['review_revision'], reviewer='撤销人',
                reason='人工发现之前的失效判断需要重新复核', now=NOW + timedelta(days=3))
    args.update(options)
    return (service or app.service).undo_invalidation(app.workspace, invalidated['task']['id'], **args)


def test_rebind_verified_newer_same_source_saves_single_audit_old_evidence_and_replay(app):
    initial = original_task(app)
    old_link = app.store.get_link(app.workspace, app.observation.observation_id)
    app.store.set_status(app.workspace, initial['task']['id'], 'in_progress', actor='负责人', reason='手动开始', now=NOW)
    _, candidate = revise(app)
    old_rows = [tuple(row) for row in app.temporal._connection.execute('SELECT * FROM semantic_candidate ORDER BY candidate_id')]
    value = binding_preview(app, initial, candidate)
    assert set(value['differences']) == {'owner', 'deadline'}
    assert value['current_values']['owner'] == '李明' and value['candidate_values']['owner'] == '王芳'
    assert value['source_proof']['new']['version_sequence'] > value['source_proof']['old']['version_sequence']
    result = apply_binding(app, value)
    assert result['task']['id'] == initial['task']['id'] and result['task']['status'] == 'in_progress'
    assert result['task']['owner'] == '王芳' and result['task']['deadline'] == '2026-10-16'
    assert [audit['action'] for audit in result['review_history']] == ['confirm', 'needs_review', 'rebind']
    audit = result['review_history'][-1]
    details = audit['binding_details']
    assert details['operation'] == 'rebind' and details['old_candidate_fingerprint'] == initial['candidate_fingerprint']
    assert details['new_candidate_fingerprint'] == value['new_candidate_fingerprint']
    assert details['source_proof'] == value['source_proof'] and details['legacy_basis'] is None
    assert audit['reviewer'] == '重绑审核人' and audit['reason'] == '人工比对同一次原文修订'
    assert audit['reviewed_at'] == (NOW + timedelta(days=2)).isoformat()
    assert app.store.get_link(app.workspace, app.observation.observation_id) == old_link
    new_link = app.store.get_link(app.workspace, candidate.task_observation.observation_id)
    assert new_link.review_action == 'merge' and new_link.task_id == initial['task']['id']
    assert result['events'][0] == initial['events'][0]
    changes = [event for event in result['events'] if event['event_type'] in ('owner_changed', 'deadline_changed')]
    assert len(changes) == 2
    for event in changes:
        assert event['evidence'] == value['new_candidate']['candidate']['evidence']
        assert event['meeting_time'] == candidate.event_time.isoformat()
        assert event['evidence_role'] == 'source'
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == result['task']
    assert [tuple(row) for row in app.temporal._connection.execute('SELECT * FROM semantic_candidate ORDER BY candidate_id')] == old_rows


def test_mixed_current_candidate_manual_choices_preserve_business_state_and_mark_each_event_origin(app):
    initial = original_task(app)
    text = '由王芳负责联调验收报告，2026年10月16日前完成。'
    _, candidate = revise(app, content=NEW_CONTENT.replace(NEW_TEXT, text), text=text, parts=('联调验收报告',))
    value = binding_preview(app, initial, candidate)
    choices = dict(owner={'choice': 'current'}, deadline={'choice': 'candidate'},
                   title={'choice': 'manual', 'value': '人工归档报告'})
    result = apply_binding(app, value, choices)
    assert result['task']['owner'] == initial['task']['owner'] and result['task']['title'] == '人工归档报告'
    assert result['field_sources']['owner']['origin'] == result['field_sources']['title']['origin'] == 'human'
    assert result['field_sources']['deadline']['origin'] == 'source'
    changes = {event['event_type']: event for event in result['events'][1:]}
    assert set(changes) == {'deadline_changed', 'title_changed'}
    assert changes['deadline_changed']['evidence_role'] == 'source' and changes['deadline_changed']['meeting_time']
    assert changes['title_changed']['evidence_role'] == 'context' and changes['title_changed']['meeting_time'] is None
    assert result['review_history'][-1]['binding_details']['field_choices'] == choices
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == result['task']


def test_all_current_choices_do_not_change_snapshot_but_record_human_approval_of_differences(app):
    initial = original_task(app)
    _, candidate = revise(app)
    value = binding_preview(app, initial, candidate)
    result = apply_binding(app, value, {field: {'choice': 'current'} for field in value['required_choices']})
    assert result['task'] == initial['task'] and result['events'] == initial['events']
    assert result['field_sources']['owner']['origin'] == result['field_sources']['deadline']['origin'] == 'human'
    assert result['review_status'] == 'approved' and result['review_history'][-1]['action'] == 'rebind'


@pytest.mark.parametrize('choices', [
    {}, {'owner': {'choice': 'candidate'}}, {'owner': {'choice': 'guess'}, 'deadline': {'choice': 'candidate'}},
    {'owner': {'choice': 'manual'}, 'deadline': {'choice': 'candidate'}},
    {'owner': {'choice': 'current', 'value': None}, 'deadline': {'choice': 'candidate'}},
    {'owner': {'choice': 'candidate'}, 'deadline': {'choice': 'manual', 'value': {'deadline': None}}},
    {'owner': {'choice': 'candidate'}, 'deadline': {'choice': 'manual', 'value': {'deadline': '10/16', 'deadline_text': '10/16'}}},
    {'owner': {'choice': 'candidate'}, 'deadline': {'choice': 'candidate'}, 'status': {'choice': 'manual', 'value': 'done'}},
])
def test_missing_or_invalid_explicit_choices_leave_binding_snapshot_and_audit_unchanged(app, choices):
    initial = original_task(app)
    _, candidate = revise(app)
    value = binding_preview(app, initial, candidate)
    before = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=1))
    with pytest.raises(ValueError):
        apply_binding(app, value, choices)
    assert app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=2)) == before
    assert app.store.get_link(app.workspace, candidate.task_observation.observation_id) is None


@pytest.mark.parametrize('cause', ['other_source', 'other_meeting', 'missing_old_time', 'missing_new_time', 'same_version', 'old_version', 'not_current', 'consumed', 'bad_original', 'missing_evidence', 'mixed_evidence'])
def test_rebind_refuses_unprovable_or_unauthorized_source_revision(app, cause):
    initial = original_task(app)
    if cause == 'missing_old_time':
        # A valid legacy task may lack meeting metadata; rebinding must not guess it.
        app2_candidate = TaskCandidateInput.from_dict(preview(app)['candidate'])
        no_time = replace(app2_candidate, candidate_id='legacy-missing-time', meeting_time=None,
                          evidence=tuple(replace(item, meeting_time=None) for item in app2_candidate.evidence))
        initial = original_task(app, legacy=True, candidate=no_time)
    if cause == 'same_version':
        write_candidate(app, replace(app.candidate, candidate_id='same-version-variant',
                                    confidence=replace(app.candidate.confidence, overall=.7)), insert=True)
        candidate = app.candidate
    else:
        content = NEW_CONTENT.replace('10-08T10:00', '10-10T10:00') if cause == 'other_meeting' else NEW_CONTENT
        if cause == 'missing_new_time':
            content = NEW_TEXT
        _, candidate = revise(app, content=content, source_id='another.txt' if cause == 'other_source' else None)
    if cause == 'old_version':
        first_new = binding_preview(app, initial, candidate)
        initial = apply_binding(app, first_new)
        revise(app, content=CONTENT, text=TEXT, owner='李明', deadline='2026年10月12日', extract=False)
        candidate = app.candidate
    if cause == 'not_current':
        raw = app.service.preview_candidate(app.workspace, candidate.task_observation.observation_id)
        revise(app, content=CONTENT, extract=False)
        with pytest.raises(ReviewConflictError):
            app.service.preview_rebind(app.workspace, initial['task']['id'], candidate.task_observation.observation_id,
                expected_candidate_fingerprint=raw['fingerprint'], now=NOW + timedelta(days=3))
        return
    if cause == 'consumed':
        raw = app.service.preview_candidate(app.workspace, candidate.task_observation.observation_id)
        app.service.reject(app.workspace, candidate.task_observation.observation_id,
            expected_candidate_fingerprint=raw['fingerprint'], reviewer='人工', reason='已处理', now=NOW + timedelta(days=1))
    if cause in ('bad_original', 'missing_evidence'):
        connection = app.store._connection
        if cause == 'bad_original':
            connection.execute('UPDATE ll_source_version SET content=content || ? WHERE artifact_version_id=?',
                               ('损坏', candidate.artifact_version_id))
        else:
            connection.execute('DELETE FROM ll_source_evidence WHERE artifact_version_id=?', (candidate.artifact_version_id,))
    if cause == 'mixed_evidence':
        # A second source in historical evidence makes the old basis ambiguous.
        from tests.unit.test_task_models import make_evidence
        old = app.service.storage.latest(app.workspace, task_id=initial['task']['id'])
        # Use a separate legacy task rather than editing immutable prior evidence.
        base = TaskCandidateInput.from_dict(old['candidate']['candidate'])
        mixed = replace(base, candidate_id='legacy-multiple-sources', evidence=(*base.evidence,
            make_evidence('another.txt', 'another-version', workspace=app.workspace)))
        initial = original_task(app, legacy=True, candidate=mixed)
    with pytest.raises((ReviewConflictError, CandidateUnavailableError, CandidateAlreadyReviewedError)):
        binding_preview(app, initial, candidate, now=NOW + timedelta(days=4))


def test_preview_token_and_review_revision_block_changes_after_preview(app):
    initial = original_task(app)
    _, candidate = revise(app)
    value = binding_preview(app, initial, candidate)
    with pytest.raises(ReviewConflictError):
        apply_binding(app, dict(value, review_revision='stale'))
    with pytest.raises(ReviewConflictError):
        apply_binding(app, dict(value, preview_token='stale'))
    app.store.set_status(app.workspace, initial['task']['id'], 'blocked', actor='负责人', reason='状态在预览后变化', now=NOW + timedelta(days=1))
    with pytest.raises(ReviewConflictError):
        apply_binding(app, value)
    fresh = binding_preview(app, initial, candidate)
    assert fresh['preview_token'] != value['preview_token']
    # Source can round-trip using existing versions without the pending form noticing.
    revise(app, content=CONTENT, extract=False)
    revise(app, content=NEW_CONTENT, extract=False)
    with pytest.raises(ReviewConflictError):
        apply_binding(app, fresh)


def test_restoring_old_version_cannot_use_historical_link_to_reconfirm_or_rebind_back(app):
    initial = original_task(app)
    _, candidate = revise(app)
    result = apply_binding(app, binding_preview(app, initial, candidate))
    revise(app, content=CONTENT, extract=False)
    current = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=3))
    assert current['review_status'] == 'needs_review'
    raw = preview(app)
    with pytest.raises(CandidateAlreadyReviewedError):
        app.service.confirm(app.workspace, app.observation.observation_id, expected_candidate_fingerprint=raw['fingerprint'],
            expected_review_revision=current['review_revision'], reviewer='旧链接', reason='不允许倒绑', now=NOW + timedelta(days=4))
    with pytest.raises((CandidateAlreadyReviewedError, ReviewConflictError)):
        binding_preview(app, result, app.candidate, now=NOW + timedelta(days=4))
    assert app.store.get_task(app.workspace, initial['task']['id']).to_dict() == result['task']


def test_invalidation_without_any_time_anchor_labels_human_declarations_and_undo_preserves_history(app):
    initial = original_task(app)
    app.store.set_status(app.workspace, initial['task']['id'], 'done', actor='负责人', reason='业务状态独立', now=NOW)
    _, value = prepare_invalidation(app, initial)
    assert value['machine_validation']['matching_original_time_present'] is False
    assert value['machine_validation']['same_meeting'] == value['machine_validation']['task_absence'] == 'not_machine_verified'
    invalid = apply_invalidation(app, value)
    task = app.store.get_task(app.workspace, initial['task']['id']).to_dict()
    assert invalid['review_status'] == 'invalidated' and task['status'] == 'done'
    declarations = invalid['human_declarations']
    assert declarations['origin'] == 'human_statement' and declarations['same_meeting'] is declarations['task_absent'] is True
    assert declarations['reviewer'] == '人工核实人' and declarations['reason'] and declarations['reviewed_at']
    assert invalid['machine_validation']['verified_properties'] == ['same_source', 'newer_version', 'current_active', 'original_integrity']
    assert 'same_meeting' not in invalid['machine_validation']['verified_properties']
    assert invalid['review_history'][-1]['binding_details']['new_candidate_fingerprint'] is None
    assert app.service.get_task(app.workspace, initial['task']['id']) == invalid
    with pytest.raises(ReviewConflictError):
        app.service.edit_fields(app.workspace, initial['task']['id'], patch={'owner': None},
            expected_candidate_fingerprint=invalid['candidate_fingerprint'], expected_review_revision=invalid['review_revision'],
            reviewer='普通编辑', reason='不能解除失效')
    renewed = undo(app, invalid)
    assert renewed['review_status'] == 'needs_review' and renewed['task'] == task
    assert renewed['events'] == invalid['events'] and renewed['review_history'][:-1] == invalid['review_history']
    detail = renewed['review_history'][-1]['binding_details']
    assert detail['operation'] == 'undo_invalidate'
    assert detail['target_review_event_id'] == invalid['review_history'][-1]['id']
    assert renewed['machine_validation'] is None  # undo does not pretend to revalidate the source.
    assert renewed['human_declarations']['withdraws_invalidation'] is True
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == task
    assert app.service.get_task(app.workspace, initial['task']['id']) == renewed


@pytest.mark.parametrize('missing', ['same_meeting', 'task_absent', 'integer', 'string'])
def test_invalidation_requires_both_strict_human_declarations(app, missing):
    initial = original_task(app)
    _, value = prepare_invalidation(app, initial)
    arguments = {'attest_same_meeting': False} if missing == 'same_meeting' else {'attest_task_absent': False}
    if missing == 'integer':
        arguments = {'attest_same_meeting': 1}
    if missing == 'string':
        arguments = {'attest_task_absent': 'true'}
    before = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=1))
    with pytest.raises(ValueError):
        apply_invalidation(app, value, **arguments)
    assert app.service.get_task(app.workspace, initial['task']['id']) == before


@pytest.mark.parametrize('cause', ['old_version', 'inactive', 'content', 'hash', 'stale_preview', 'stale_revision'])
def test_human_declarations_cannot_override_failed_machine_proof_or_stale_invalidation(app, cause):
    initial = original_task(app)
    artifact, value = prepare_invalidation(app, initial)
    connection = app.store._connection
    if cause == 'old_version':
        value = dict(value, source_version_id=app.artifact.artifact_version_id)
    elif cause == 'inactive':
        connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=?', (app.workspace,))
    elif cause in ('content', 'hash'):
        column = 'content' if cause == 'content' else 'content_sha256'
        connection.execute(f'UPDATE ll_source_version SET {column}=? WHERE artifact_version_id=?', ('corrupted', artifact.artifact_version_id))
    elif cause == 'stale_preview':
        revise(app, content='第三个不同的完整版本。', extract=False)
    else:
        value = dict(value, review_revision='stale')
    with pytest.raises((ReviewConflictError, CandidateUnavailableError)):
        apply_invalidation(app, value)
    assert app.store.get_task(app.workspace, initial['task']['id']).to_dict() == initial['task']
    assert app.store._connection.execute("SELECT COUNT(*) FROM task_review_event WHERE action='invalidate'").fetchone()[0] == 0


def test_invalidated_is_latched_for_query_confirm_and_rebind_until_special_undo(app):
    initial = original_task(app)
    _, value = prepare_invalidation(app, initial)
    invalid = apply_invalidation(app, value)
    revise(app, content=CONTENT, extract=False)
    raw = preview(app)
    with pytest.raises(CandidateAlreadyReviewedError):
        app.service.confirm(app.workspace, app.observation.observation_id, expected_candidate_fingerprint=raw['fingerprint'],
            expected_review_revision=invalid['review_revision'], reviewer='普通确认', reason='不允许自动恢复')
    _, candidate = revise(app)
    with pytest.raises(ReviewConflictError):
        binding_preview(app, initial, candidate)
    assert app.service.get_task(app.workspace, initial['task']['id']) == invalid
    with pytest.raises(ReviewConflictError):
        apply_invalidation(app, value)
    renewed = undo(app, invalid)
    rebound = apply_binding(app, binding_preview(app, renewed, candidate, now=NOW + timedelta(days=4)),
                            now=NOW + timedelta(days=5))
    assert rebound['review_status'] == 'approved' and rebound['task']['id'] == initial['task']['id']
    assert [audit['binding_details']['operation'] for audit in rebound['review_history'] if audit['binding_details']] == [
        'invalidate', 'undo_invalidate', 'rebind']


def test_undo_rejects_wrong_target_revision_workspace_repeat_and_retroactive_time(app):
    initial = original_task(app)
    _, value = prepare_invalidation(app, initial)
    invalid = apply_invalidation(app, value)
    for options in ({'target_review_event_id': initial['review_history'][0]['id']}, {'expected_review_revision': 'old'},
                    {'now': NOW}, {'reviewer': ''}, {'reason': ''}):
        with pytest.raises((ValueError, ReviewConflictError)):
            undo(app, invalid, **options)
    workspace = app.repository.create_workspace('另一个空间')['workspace_id']
    with pytest.raises(TaskNotFoundError):
        app.service.undo_invalidation(workspace, initial['task']['id'], target_review_event_id=invalid['review_history'][-1]['id'],
            expected_review_revision=invalid['review_revision'], reviewer='人工', reason='越权')
    renewed = undo(app, invalid)
    with pytest.raises(ReviewConflictError):
        undo(app, invalid)
    assert app.service.get_task(app.workspace, initial['task']['id']) == renewed


def test_retroactive_invalidation_is_rejected_without_audit(app):
    initial = original_task(app)
    _, value = prepare_invalidation(app, initial)
    with pytest.raises(ValueError, match='timestamp'):
        apply_invalidation(app, value, now=NOW)
    assert app.store._connection.execute("SELECT COUNT(*) FROM task_review_event WHERE action='invalidate'").fetchone()[0] == 0

def test_safe_legacy_rebind_needs_every_field_choice_and_keeps_old_fingerprint_opaque(app):
    initial = original_task(app, legacy=True)
    app.store.set_status(app.workspace, initial['task']['id'], 'done', actor='负责人', reason='纯状态允许', now=NOW)
    old_link = app.store.get_link(app.workspace, app.observation.observation_id)
    _, candidate = revise(app)
    value = binding_preview(app, initial, candidate)
    assert set(value['required_choices']) == {'title', 'owner', 'deadline'}
    assert value['old_candidate_fingerprint'] == old_link.candidate_fingerprint
    assert value['legacy_basis']['candidate_snapshot'] == 'unknown'
    assert value['legacy_basis']['link'] == old_link.to_dict()
    assert value['legacy_basis']['created_event']['evidence'] == initial['events'][0]['evidence']
    assert app.service.get_task(app.workspace, initial['task']['id'])['review_history'] == ()
    with pytest.raises(ValueError):
        apply_binding(app, value, {'owner': {'choice': 'candidate'}, 'deadline': {'choice': 'candidate'}})
    result = apply_binding(app, value, {'title': {'choice': 'current'}, 'owner': {'choice': 'candidate'},
                                       'deadline': {'choice': 'manual', 'value': {'deadline': None, 'deadline_text': None}}})
    assert result['task']['status'] == 'done' and result['task']['owner'] == '王芳'
    assert result['task']['deadline'] is result['task']['deadline_text'] is None
    assert result['field_sources']['title']['origin'] == result['field_sources']['deadline']['origin'] == 'human'
    assert result['field_sources']['owner']['origin'] == 'source'
    assert len(result['review_history']) == 1 and result['review_history'][0]['action'] == 'rebind'
    assert result['review_history'][0]['candidate']['fingerprint'] == value['new_candidate_fingerprint']
    assert result['review_history'][0]['binding_details']['legacy_basis'] == value['legacy_basis']
    assert result['events'][0]['evidence'] == initial['events'][0]['evidence']
    assert app.store.get_link(app.workspace, app.observation.observation_id) == old_link
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == result['task']


def test_legacy_invalidation_with_unknown_candidate_and_time_can_be_undone_without_inventing_history(app):
    raw = TaskCandidateInput.from_dict(preview(app)['candidate'])
    raw = replace(raw, meeting_time=None, evidence=tuple(replace(value, meeting_time=None) for value in raw.evidence))
    initial = original_task(app, legacy=True, candidate=raw)
    _, value = prepare_invalidation(app, initial)
    invalid = apply_invalidation(app, value)
    audit = invalid['review_history'][0]
    assert audit['candidate'] is None and invalid['original_candidate'] is None
    assert invalid['field_sources'] == {} and audit['binding_details']['legacy_basis']['candidate_snapshot'] == 'unknown'
    assert invalid['human_declarations']['origin'] == 'human_statement'
    assert invalid['machine_validation']['matching_original_time_present'] is False
    assert invalid['task'] == initial['task'] and invalid['events'] == initial['events']
    undone = undo(app, invalid)
    assert undone['review_status'] == 'needs_review' and undone['original_candidate'] is None
    assert [audit['candidate'] for audit in undone['review_history']] == [None, None]
    assert undone['review_history'][-1]['binding_details']['target_review_event_id'] == audit['id']
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == initial['task']


@pytest.mark.parametrize('cause', ['field_event', 'merge_link', 'snapshot_mismatch', 'wrong_source_hash', 'missing_position'])
def test_ambiguous_or_damaged_legacy_binding_has_no_automatic_migration_exit(app, cause):
    raw = TaskCandidateInput.from_dict(preview(app)['candidate'])
    if cause in ('wrong_source_hash', 'missing_position'):
        evidence = raw.evidence[0]
        evidence = replace(evidence, source_sha256='f' * 64) if cause == 'wrong_source_hash' else replace(
            evidence, message_id='opaque-message', line_start=None, line_end=None, char_start=None, char_end=None)
        raw = replace(raw, evidence=(evidence,))
    initial = original_task(app, legacy=True, candidate=raw)
    if cause in ('field_event', 'merge_link'):
        # Both operations existed in v1, but are intentionally outside safe migration.
        alternative = replace(raw, candidate_id='legacy-extra', owner='曾经的人工值' if cause == 'field_event' else raw.owner)
        app.store.merge_candidate(app.workspace, initial['task']['id'], alternative,
            apply_fields=['owner'] if cause == 'field_event' else [], reviewer='旧版', reason='历史操作', now=NOW)
    elif cause == 'snapshot_mismatch':
        app.store._connection.execute('UPDATE task SET title=? WHERE id=?', ('缺少事件的损坏值', initial['task']['id']))
    artifact, candidate = revise(app)
    raw_new = app.service.preview_candidate(app.workspace, candidate.task_observation.observation_id)
    for action in (
        lambda: app.service.preview_rebind(app.workspace, initial['task']['id'], candidate.task_observation.observation_id,
            expected_candidate_fingerprint=raw_new['fingerprint'], now=NOW + timedelta(days=1)),
        lambda: app.service.preview_invalidation(app.workspace, initial['task']['id'], artifact.artifact_version_id,
            now=NOW + timedelta(days=1))):
        with pytest.raises((ReviewConflictError, CandidateUnavailableError)):
            action()
    assert app.store._connection.execute('SELECT COUNT(*) FROM task_review_event').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['new_version', 'deleted', 'new_variant'])
def test_processed_rejection_survives_source_changes_without_assigning_old_reason_to_new_variants(app, change):
    raw = preview(app)
    audit = app.service.reject(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=raw['fingerprint'], reviewer='拒绝审核人', reason='无需正式任务', now=NOW)
    exact = app.service.list_candidates(app.workspace, include_reviewed=True)
    assert len(exact) == 1 and exact[0]['review_status'] == 'rejected' and exact[0]['is_current'] is True
    if change == 'new_version':
        revise(app)
    elif change == 'deleted':
        app.repository.delete_source(app.workspace, app.artifact.artifact_id)
    else:
        write_candidate(app, replace(app.candidate, candidate_id='new-current-variant',
                                    confidence=replace(app.candidate.confidence, overall=.7)), insert=True)
    values = app.service.list_candidates(app.workspace, include_reviewed=True)
    rejected = [value for value in values if value['review_status'] == 'rejected']
    assert len(rejected) == 1
    entry = rejected[0]
    assert entry['is_current'] is False and entry['fingerprint'] == raw['fingerprint']
    assert entry['original_payload'] == raw['original_payload']
    assert entry['reviewer'] == audit['reviewer'] and entry['reason'] == audit['reason']
    assert entry['reviewed_at'] == audit['reviewed_at'] and entry['processed_action'] == 'reject'
    assert all('reason' not in value and 'reviewer' not in value for value in values if value['is_current'])
    assert all(value['review_status'] != 'rejected' for value in app.service.list_candidates(app.workspace))
    assert app.store.list_tasks(app.workspace) == ()


@pytest.mark.parametrize('failure_at', ['owner_changed', 'deadline_changed', 'task_observation_link', 'task_review_event', 'task_review_binding_detail'])
def test_rebind_write_failure_rolls_back_fields_all_events_link_audit_and_detail(app, failure_at):
    initial = original_task(app)
    _, candidate = revise(app)
    value = binding_preview(app, initial, candidate)
    before = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=1))
    table = 'task_event' if failure_at.endswith('_changed') else failure_at
    when = f"WHEN NEW.event_type='{failure_at}'" if table == 'task_event' else ''
    app.store._connection.execute(f'''CREATE TRIGGER inject_rebind BEFORE INSERT ON {table} {when}
        BEGIN SELECT RAISE(ABORT,'injected rebind failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='injected rebind failure'):
        apply_binding(app, value)
    assert app.service.get_task(app.workspace, initial['task']['id']) == before
    assert app.store.get_link(app.workspace, candidate.task_observation.observation_id) is None
    assert app.store._connection.execute('SELECT COUNT(*) FROM task_review_binding_detail').fetchone()[0] == 0
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == initial['task']
    app.store._connection.execute('DROP TRIGGER inject_rebind')
    assert apply_binding(app, value)['review_status'] == 'approved'


@pytest.mark.parametrize('operation', ['invalidate', 'undo'])
@pytest.mark.parametrize('failure_at', ['task_review_event', 'task_review_binding_detail'])
def test_invalidation_or_undo_audit_failure_is_atomic(app, operation, failure_at):
    initial = original_task(app)
    _, value = prepare_invalidation(app, initial)
    invalidated = apply_invalidation(app, value) if operation == 'undo' else None
    before = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=2))
    app.store._connection.execute(f'''CREATE TRIGGER inject_invalidation BEFORE INSERT ON {failure_at}
        BEGIN SELECT RAISE(ABORT,'injected invalidation failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='injected invalidation failure'):
        undo(app, invalidated) if operation == 'undo' else apply_invalidation(app, value)
    assert app.service.get_task(app.workspace, initial['task']['id']) == before
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == initial['task']
    app.store._connection.execute('DROP TRIGGER inject_invalidation')
    result = undo(app, invalidated) if operation == 'undo' else apply_invalidation(app, value)
    assert result['review_status'] == ('needs_review' if operation == 'undo' else 'invalidated')


@pytest.mark.parametrize('operation', ['rebind', 'invalidate', 'undo'])
def test_concurrent_binding_lifecycle_operations_commit_exactly_once_across_connections(app, operation):
    initial = original_task(app)
    if operation == 'rebind':
        _, candidate = revise(app)
        value = binding_preview(app, initial, candidate)
    else:
        _, value = prepare_invalidation(app, initial)
    invalidated = apply_invalidation(app, value) if operation == 'undo' else None
    other_store = TaskStore(app.path)
    other_service = TaskReviewService(other_store, LegacyTaskCandidateSource())
    services = (app.service, other_service)
    barrier = Barrier(4)
    def run(index):
        barrier.wait(timeout=10)
        try:
            if operation == 'rebind':
                return apply_binding(app, value, service=services[index % 2])
            if operation == 'invalidate':
                return apply_invalidation(app, value, service=services[index % 2])
            return undo(app, invalidated, service=services[index % 2])
        except (ReviewConflictError, CandidateAlreadyReviewedError):
            return None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(4)))
        assert sum(value is not None for value in results) == 1
        result = app.service.get_task(app.workspace, initial['task']['id'])
        operations = [value['binding_details']['operation'] for value in result['review_history'] if value['binding_details']]
        assert operations.count('undo_invalidate' if operation == 'undo' else operation) == 1
        assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == result['task']
    finally:
        other_store.close()


def test_binding_details_append_only_workspace_fk_and_reopening_preserve_unrelated_data(app):
    initial = original_task(app)
    _, candidate = revise(app)
    result = apply_binding(app, binding_preview(app, initial, candidate))
    connection = app.store._connection
    connection.execute('PRAGMA user_version=77')
    connection.execute('CREATE TABLE unrelated_task_test(value TEXT)')
    connection.execute('INSERT INTO unrelated_task_test VALUES(?)', ('原值',))
    for sql in ('UPDATE task_review_binding_detail SET operation=operation', 'DELETE FROM task_review_binding_detail'):
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            connection.execute(sql)
    audit = result['review_history'][-1]
    row = dict(connection.execute('SELECT * FROM task_review_binding_detail').fetchone())
    row['workspace_id'] = 'another-workspace'
    # A different audit ID avoids the primary-key duplicate masking the workspace FK.
    row['review_event_id'] = initial['review_history'][0]['id']
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        connection.execute('INSERT INTO task_review_binding_detail(' + ','.join(row) + ') VALUES(' +
                           ','.join('?' for _ in row) + ')', tuple(row.values()))
    with TaskStore(app.path) as reopened:
        service = TaskReviewService(reopened, LegacyTaskCandidateSource())
        assert service.get_task(app.workspace, initial['task']['id']) == result
        assert [tuple(row) for row in reopened._connection.execute('SELECT * FROM task_schema_metadata')] == [(1, 1)]
        assert [tuple(row) for row in reopened._connection.execute('SELECT * FROM task_review_schema_metadata')] == [(1, 1)]
        assert reopened._connection.execute('PRAGMA user_version').fetchone()[0] == 77
        assert reopened._connection.execute('SELECT value FROM unrelated_task_test').fetchone()[0] == '原值'
    connection.execute('DROP TRIGGER task_review_binding_detail_append_only_update')
    with pytest.raises(TaskSchemaError, match='guards'):
        TaskReviewService(app.store, LegacyTaskCandidateSource())

def test_newer_catalogue_order_does_not_depend_on_lexical_version_hash(app):
    initial = original_task(app)
    content = None
    for index in range(128):
        proposed = NEW_CONTENT + f' 修订编号{index}'
        artifact = replace(app.artifact, content=proposed)
        if artifact.artifact_version_id < app.artifact.artifact_version_id:
            content = proposed
            break
    assert content is not None, 'fixture could not construct a lexically earlier new content hash'
    artifact, candidate = revise(app, content=content)
    value = binding_preview(app, initial, candidate)
    assert artifact.artifact_version_id < app.artifact.artifact_version_id
    assert value['source_proof']['new']['version_sequence'] > value['source_proof']['old']['version_sequence']
    assert apply_binding(app, value)['task']['owner'] == '王芳'


def test_rebinding_source_protocol_rejects_a_new_candidate_with_mixed_version_evidence(app):
    from linkloom.tasks.review_models import CandidateSnapshot
    initial = original_task(app)
    old_evidence = TaskCandidateInput.from_dict(preview(app)['candidate']).evidence[0]
    _, candidate = revise(app)
    class MixedSource:
        def list_candidates(self, connection, workspace):
            return tuple(replace(value, candidate=replace(value.candidate, evidence=(*value.candidate.evidence, old_evidence)))
                         for value in LegacyTaskCandidateSource().list_candidates(connection, workspace))
        def version_proof(self, *args, **kwargs):
            return LegacyTaskCandidateSource().version_proof(*args, **kwargs)
    service = TaskReviewService(app.store, MixedSource())
    raw = service.preview_candidate(app.workspace, candidate.task_observation.observation_id)
    with pytest.raises(ReviewConflictError, match='multiple evidence'):
        service.preview_rebind(app.workspace, initial['task']['id'], candidate.task_observation.observation_id,
            expected_candidate_fingerprint=raw['fingerprint'], now=NOW + timedelta(days=1))
    assert app.store.get_task(app.workspace, initial['task']['id']).to_dict() == initial['task']


def test_new_binding_and_invalidation_apis_are_workspace_scoped_and_never_call_extractor(app, monkeypatch):
    from unittest.mock import Mock
    initial = original_task(app)
    _, candidate = revise(app)
    raw = app.service.preview_candidate(app.workspace, candidate.task_observation.observation_id)
    workspace = app.repository.create_workspace('无权访问原任务')['workspace_id']
    for action in (
        lambda: app.service.preview_rebind(workspace, initial['task']['id'], candidate.task_observation.observation_id,
            expected_candidate_fingerprint=raw['fingerprint'], now=NOW + timedelta(days=1)),
        lambda: app.service.preview_invalidation(workspace, initial['task']['id'], candidate.artifact_version_id,
            now=NOW + timedelta(days=1))):
        with pytest.raises(TaskNotFoundError):
            action()
    unused = Mock(side_effect=AssertionError('task review must not invoke extraction'))
    monkeypatch.setattr(RecordedProvider, 'complete', unused)
    value = binding_preview(app, initial, candidate)
    result = apply_binding(app, value)
    assert result['review_status'] == 'approved'
    assert app.service.list_candidates(app.workspace) == ()
    assert app.service.get_task(app.workspace, initial['task']['id']) == result
    unused.assert_not_called()


def test_real_2a_schema_upgrades_additively_without_rewriting_old_review_rows(app, tmp_path):
    initial = original_task(app)
    # Build a separate 2A-only database from a SQL dump, omitting ONLY 2B objects.
    # The original fixture and its guarded audit tables remain untouched.
    older_path = tmp_path / 'prior-2a.sqlite'
    statements = [statement for statement in app.store._connection.iterdump()
                  if 'task_review_binding_detail' not in statement and 'task_review_id_workspace' not in statement]
    with sqlite3.connect(older_path) as connection:
        connection.executescript('\n'.join(statements))
        connection.execute('PRAGMA user_version=91')
        old_rows = [tuple(row) for row in connection.execute('SELECT * FROM task_review_event ORDER BY sequence')]
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='task_review_binding_detail'").fetchone() is None
    with TaskStore(older_path) as store:
        service = TaskReviewService(store, LegacyTaskCandidateSource())
        assert service.get_task(app.workspace, initial['task']['id']) == initial
        assert [tuple(row) for row in store._connection.execute('SELECT * FROM task_review_event ORDER BY sequence')] == old_rows
        assert [tuple(row) for row in store._connection.execute('SELECT * FROM task_schema_metadata')] == [(1, 1)]
        assert [tuple(row) for row in store._connection.execute('SELECT * FROM task_review_schema_metadata')] == [(1, 1)]
        assert store._connection.execute('PRAGMA user_version').fetchone()[0] == 91
        assert store._connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='task_review_binding_detail'").fetchone()[0] == 1

def test_ordinary_reconfirmation_cannot_approve_unknown_legacy_basis_after_undo_and_source_restore(app):
    initial = original_task(app, legacy=True)
    _, value = prepare_invalidation(app, initial)
    invalidated = apply_invalidation(app, value)
    undone = undo(app, invalidated)
    assert undone['review_status'] == 'needs_review' and undone['field_sources'] == {}
    assert undone['original_candidate'] is None
    revise(app, content=CONTENT, extract=False)
    raw = preview(app)
    before = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=4))
    tables = ('task', 'task_event', 'task_observation_link', 'task_review_event', 'task_review_binding_detail',
              'll_source', 'll_source_version', 'll_source_evidence', 'semantic_candidate')
    saved = {table: [tuple(row) for row in app.store._connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
             for table in tables}
    with pytest.raises(ReviewConflictError, match='controlled legacy'):
        app.service.confirm(app.workspace, app.observation.observation_id,
            expected_candidate_fingerprint=raw['fingerprint'], expected_review_revision=before['review_revision'],
            reviewer='普通重审人', reason='不能绕过旧数据的逐字段人工迁移', now=NOW + timedelta(days=4))
    after = app.service.get_task(app.workspace, initial['task']['id'], now=NOW + timedelta(days=4))
    assert after == before and after['review_status'] == 'needs_review'
    assert after['field_sources'] == {} and after['original_candidate'] is None
    assert {table: [tuple(row) for row in app.store._connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in tables} == saved