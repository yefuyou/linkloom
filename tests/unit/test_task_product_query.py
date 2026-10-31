"""Bounded product reads preserve human review and immutable evidence semantics."""
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.models import EvidenceReference, TaskCandidateInput
from linkloom.tasks.query import TaskQueryError
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError
from linkloom.tasks.store import TaskNotFoundError
from tests.unit.test_task_legacy_adapter import app, preview, REVIEW_TIME, TEXT
from tests.unit.test_task_merge import setup, prepare, apply
from tests.unit.test_task_rebind import NOW, revise


def confirm_current(app, candidate_id=None, *, patch=None, hour=0):
    identifier = candidate_id or app.observation.observation_id
    raw = app.service.preview_candidate(app.workspace, identifier)
    return app.service.confirm(app.workspace, identifier,
        expected_candidate_fingerprint=raw['fingerprint'], patch=patch,
        reviewer='审核员', reason='逐段检查原文', now=REVIEW_TIME + timedelta(hours=hour))


def additional(app, index):
    text = '由王芳负责联调报告，2026年10月16日前完成。'
    _, candidate = revise(app, source_id=f'other-{index}.txt', text=text,
        content='[2026-10-10T10:00:00+08:00] 评审人: ' + text)
    return candidate.task_observation.observation_id


def test_candidate_page_filters_in_service_and_rejected_items_keep_review_details(app):
    ids = [app.observation.observation_id, additional(app, 1), additional(app, 2)]
    values = app.service.list_candidates_page(app.workspace, limit=2)
    assert len(values['items']) == 2 and values['page'] == dict(limit=2, offset=0, next_offset=2, has_more=True)
    tail = app.service.list_candidates_page(app.workspace, limit=2, offset=2)
    assert len(tail['items']) == 1 and not tail['page']['has_more']
    assert {item['candidate_id'] for item in values['items'] + tail['items']} == set(ids)
    assert all('evidence' not in item['candidate'] and 'original_payload' not in item for item in values['items'])
    raw = app.service.preview_candidate(app.workspace, ids[1])
    app.service.reject(app.workspace, ids[1], expected_candidate_fingerprint=raw['fingerprint'],
        reviewer='拒绝审核员', reason='原文不是当前待办')
    processed = app.service.list_candidates_page(app.workspace, state='processed')
    assert len(processed['items']) == 1
    assert processed['items'][0]['review_status'] == 'rejected'
    assert processed['items'][0]['reviewer'] == '拒绝审核员' and processed['items'][0]['reason'] == '原文不是当前待办'
    assert {item['candidate_id'] for item in app.service.list_candidates_page(app.workspace)['items']} == {ids[0], ids[2]}
    assert app.service.list_candidates_page(app.workspace, offset=100)['items'] == []


def test_task_sql_page_refreshes_only_selected_tasks_and_owner_none_is_a_filter(app, monkeypatch):
    first = confirm_current(app)
    second = confirm_current(app, additional(app, 1), patch={'owner': None}, hour=1)
    third = confirm_current(app, additional(app, 2), hour=2)
    reader = Mock(wraps=app.service.source.list_candidates)
    refresh = Mock(wraps=app.service._refresh)
    monkeypatch.setattr(app.service.source, 'list_candidates', reader)
    monkeypatch.setattr(app.service, '_refresh', refresh)
    monkeypatch.setattr(app.service, '_view', Mock(side_effect=AssertionError('list must not build full histories')))
    middle = app.service.list_tasks_page(app.workspace, limit=1, offset=1)
    assert middle['items'][0]['task']['id'] == second['task']['id']
    assert middle['page']['has_more'] and refresh.call_count == reader.call_count == 1
    assert not {'events', 'timeline', 'review_history', 'original_candidate'} & middle['items'][0].keys()
    unassigned = app.service.list_tasks_page(app.workspace, owner=None)
    assert [item['task']['id'] for item in unassigned['items']] == [second['task']['id']]
    assert {item['task']['id'] for item in app.service.list_tasks_page(app.workspace, status='open')['items']} == {
        first['task']['id'], second['task']['id'], third['task']['id']}
    assert app.service.list_tasks_page(app.workspace, owner='不存在')['items'] == []


@pytest.mark.parametrize('method', ['candidates', 'candidate_evidence', 'tasks', 'summary_events'])
@pytest.mark.parametrize('bounds', [dict(limit=0), dict(limit=101), dict(offset=-1), dict(offset=2**63), dict(limit=True)])
def test_public_page_bounds_reject_invalid_numbers_before_reading(app, method, bounds):
    if method == 'candidates':
        call = lambda: app.service.list_candidates_page(app.workspace, **bounds)
    elif method == 'candidate_evidence':
        call = lambda: app.service.candidate_evidence_page(app.workspace, 'unused', candidate_fingerprint='0'*64, **bounds)
    elif method == 'tasks':
        call = lambda: app.service.list_tasks_page(app.workspace, **bounds)
    else:
        call = lambda: app.service.task_events_page(app.workspace, 'unused', **bounds)
    with pytest.raises(ValueError):
        call()
    assert not app.store._connection.in_transaction


def test_empty_large_valid_offset_is_safe_for_sql_pages(app):
    view = confirm_current(app)
    assert app.service.list_tasks_page(app.workspace, offset=2**63-1)['items'] == []
    assert app.service.task_events_page(app.workspace, view['task']['id'], offset=2**63-1)['items'] == []


def test_foreign_workspace_resources_are_not_visible_and_missing_ids_are_typed(app):
    view = confirm_current(app)
    foreign = app.repository.create_workspace('另一个工作区')['workspace_id']
    assert app.service.list_tasks_page(foreign)['items'] == []
    assert app.service.list_candidates_page(foreign)['items'] == []
    with pytest.raises(TaskNotFoundError):
        app.service.task_summary(foreign, view['task']['id'])
    with pytest.raises(TaskNotFoundError):
        app.service.task_events_page(foreign, view['task']['id'])
    with pytest.raises(TaskQueryError) as error:
        app.service.candidate_evidence_page(foreign, app.observation.observation_id,
            candidate_fingerprint=view['candidate_fingerprint'])
    assert error.value.code == 'RESOURCE_NOT_FOUND'
    with pytest.raises(TaskQueryError) as error:
        app.service.list_tasks_page('missing-workspace')
    assert error.value.code == 'WORKSPACE_NOT_FOUND'
    with pytest.raises(TaskQueryError) as error:
        app.service.preview_candidate(app.workspace, app.observation.observation_id, expected_candidate_fingerprint='0'*64)
    assert error.value.code == 'CANDIDATE_CHANGED'
    with pytest.raises(TaskQueryError) as error:
        app.service.preview_undo_merge(app.workspace, view['task']['id'], 'missing-merge')
    assert error.value.code == 'RESOURCE_NOT_FOUND'


def test_request_reuses_candidate_authority_once_but_next_transaction_revalidates(app, monkeypatch):
    first = confirm_current(app)
    confirm_current(app, additional(app, 1), hour=1)
    reader = Mock(wraps=app.service.source.list_candidates)
    monkeypatch.setattr(app.service.source, 'list_candidates', reader)
    with app.service.request_scope():
        assert len(app.service.list_tasks_page(app.workspace)['items']) == 2
        assert reader.call_count == 1
        app.repository.delete_source(app.workspace, app.artifact.artifact_id)
        changed = app.service.task_summary(app.workspace, first['task']['id'])
        assert changed['review_status'] == 'needs_review' and reader.call_count == 2
    app.store._connection.execute('UPDATE ll_source SET active=1 WHERE workspace_id=? AND source_id=?',
                                 (app.workspace, app.artifact.artifact_id))
    assert app.service.task_summary(app.workspace, first['task']['id'])['review_status'] == 'needs_review'
    assert app.store._read_transaction_token is None


def test_failed_read_cache_does_not_survive_rollback_or_request_exit(app, monkeypatch):
    original = app.service.source.list_candidates
    calls = []
    def fail_then_read(connection, workspace):
        calls.append(workspace)
        if len(calls) == 1:
            raise CandidateUnavailableError('one rejected source snapshot')
        return original(connection, workspace)
    monkeypatch.setattr(app.service.source, 'list_candidates', fail_then_read)
    with app.service.request_scope():
        with pytest.raises(CandidateUnavailableError):
            app.service.list_candidates_page(app.workspace)
        assert app.store._read_transaction_token is None
        assert len(app.service.list_candidates_page(app.workspace)['items']) == 1
    assert len(app.service.list_candidates_page(app.workspace)['items']) == 1
    assert len(calls) == 3


def multi_quote_service(app):
    content = '\n'.join('[2026-10-08T10:00:00+08:00] 主持人: ' + TEXT for _ in range(7))
    artifact = replace(app.artifact, content=content)
    segments = parse_source_text(artifact).segments
    app.repository.update_source(app.workspace, artifact.artifact_id, '多段原文', artifact,
                                 tuple(segment.evidence_span for segment in segments))
    evidence = tuple(EvidenceReference(app.workspace, artifact.artifact_id, artifact.artifact_version_id,
        segment.evidence_span.quote, segment.evidence_span.quote_sha256,
        message_id=segment.message_id, line_start=segment.evidence_span.line_start,
        line_end=segment.evidence_span.line_end, char_start=segment.evidence_span.char_start,
        char_end=segment.evidence_span.char_end, source_sha256=artifact.content_hash,
        meeting_time=segment.event_time) for segment in segments)
    candidate = TaskCandidateInput('neutral-multi-quote', app.workspace, '联调报告', evidence,
        owner='李明', meeting_time=segments[0].event_time, confidence='LOW', confidence_reason='原文责任描述需人工复核')
    class Source:
        reader = LegacyTaskCandidateSource()
        def list_candidates(self, connection, workspace):
            proof = self.reader.version_proof(connection, workspace, artifact.artifact_id, artifact.artifact_version_id,
                                               evidence=evidence, require_current=True)
            return (CandidateSnapshot(candidate, proof.context_token, '{}'),)
    return TaskReviewService(app.store, Source()), candidate


def test_multi_quote_history_caps_both_sides_together_without_losing_archived_evidence(app):
    service, candidate = multi_quote_service(app)
    raw = service.preview_candidate(app.workspace, candidate.candidate_id)
    initial = service.confirm(app.workspace, candidate.candidate_id, expected_candidate_fingerprint=raw['fingerprint'],
        reviewer='初审', reason='核对七段原文', now=REVIEW_TIME)
    service.edit_fields(app.workspace, initial['task']['id'], patch={'owner': '人工负责人'},
        expected_candidate_fingerprint=raw['fingerprint'], expected_review_revision=initial['review_revision'],
        reviewer='人工修改人', reason='线下另行指派', now=REVIEW_TIME + timedelta(hours=1))
    event_page = service.task_events_page(app.workspace, initial['task']['id'], limit=1, offset=1)
    event = event_page['items'][0]
    assert len(event['before_evidence']) + len(event['after_evidence']) == 5
    assert event['before_evidence'] and event['after_evidence'] and event['evidence_truncated']
    assert event['before_evidence_count'] == event['after_evidence_count'] == 7
    assert event['before_evidence_role'] == 'source' and event['after_evidence_role'] == 'context'
    assert event['meeting_time'] is None and event['after_evidence'][0]['meeting_time'] == candidate.meeting_time.isoformat()
    assert 'evidence' not in event and 'meeting_contexts' not in event
    archived = []
    for offset in (0, 3, 6):
        values = service.candidate_evidence_page(app.workspace, candidate.candidate_id,
            candidate_fingerprint=raw['fingerprint'], limit=3, offset=offset)
        archived.extend(values['items'])
    assert [item['line_start'] for item in archived] == list(range(1, 8))
    assert [item['quote'] for item in archived] == [reference.quote for reference in candidate.evidence]
    assert all(len(event.evidence) == 7 for event in app.store.get_events(app.workspace, initial['task']['id']))
    listed = service.list_candidates_page(app.workspace, state='all')['items'][0]
    assert listed['low_confidence'] and listed['confidence'] == 'LOW'


def test_historical_rejection_evidence_is_readable_after_source_withdrawal(app):
    raw = preview(app)
    app.service.reject(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=raw['fingerprint'], reviewer='审核员', reason='不接收')
    app.repository.delete_source(app.workspace, app.artifact.artifact_id)
    result = app.service.candidate_evidence_page(app.workspace, app.observation.observation_id,
        candidate_fingerprint=raw['fingerprint'])
    assert result['items'][0]['quote'] == TEXT
    assert result['is_current'] is False and result['evidence_basis'] == 'review_snapshot'
    history = app.service.list_candidates_page(app.workspace, state='processed')['items'][0]
    assert history['review_status'] == 'rejected' and history['is_current'] is False


def test_merge_request_does_not_repeat_full_candidate_or_version_verification(app, monkeypatch):
    initial, _, identifier = setup(app)
    value = prepare(app, initial['task']['id'], identifier)
    candidates = Mock(wraps=app.service.source.list_candidates)
    versions = Mock(wraps=app.service.source.version_proof)
    monkeypatch.setattr(app.service.source, 'list_candidates', candidates)
    monkeypatch.setattr(app.service.source, 'version_proof', versions)
    with app.service.request_scope():
        merged = apply(app, value)
    assert candidates.call_count == versions.call_count == 1
    assert merged['task']['owner'] == '王芳'
    assert app.store._read_transaction_token is None


def test_summary_preserves_latest_invalidation_declaration_after_a_merge(app):
    initial, old_b, identifier = setup(app)
    merged = apply(app, prepare(app, initial['task']['id'], identifier))
    new_b = replace(old_b, content='[2026-10-10T10:00:00+08:00] chair: no tasks remain')
    app.repository.update_source(app.workspace, new_b.artifact_id, '新版原文', new_b,
        tuple(segment.evidence_span for segment in parse_source_text(new_b).segments))
    app.repository.set_ingestion_result(app.workspace, new_b.artifact_id, new_b.artifact_version_id,
        status='COMPLETED', candidate_count=0, review_required_count=0)
    when = NOW + timedelta(days=3)
    value = app.service.preview_invalidation(app.workspace, initial['task']['id'], new_b.artifact_version_id, now=when)
    app.service.invalidate(app.workspace, initial['task']['id'], new_b.artifact_version_id,
        expected_review_revision=value['review_revision'], expected_preview_token=value['preview_token'],
        attest_same_meeting=True, attest_task_absent=True, reviewer='失效审核人', reason='新版已无此任务', now=when)
    latest = app.service.storage.latest(app.workspace, task_id=initial['task']['id'])
    summary = app.service.task_summary(app.workspace, initial['task']['id'])
    assert summary['review_status'] == 'invalidated'
    assert summary['human_declarations'] == latest['binding_details']['attestation']
    assert summary['human_declarations']['reviewer'] == '失效审核人'
    assert summary['merge_human_declarations'] == merged['human_declarations']
    assert 'task_event_tail_before' not in summary['merge_machine_validation']

def test_summary_distinguishes_retained_field_provenance_from_current_binding(app):
    initial, _, identifier = setup(app)
    merged = apply(app, prepare(app, initial['task']['id'], identifier, fields=['owner']))
    summary = app.service.task_summary(app.workspace, initial['task']['id'])
    assert {item['source_id'] for item in summary['binding_source_summary']} == {'meeting-b.txt'}
    assert {item['source_id'] for item in summary['source_summary']} == {'weekly.txt', 'meeting-b.txt'}
    assert summary['field_source_summary']['owner']['sources'][0]['source_id'] == 'meeting-b.txt'
    assert summary['field_source_summary']['title']['sources'][0]['source_id'] == 'weekly.txt'
    assert summary['field_source_summary']['deadline']['sources'][0]['source_id'] == 'weekly.txt'
    assert summary['field_sources'] == merged['field_sources']
