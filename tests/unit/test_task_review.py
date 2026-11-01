from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import sqlite3
from threading import Barrier

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.models import TaskStatus
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import ReviewConflictError
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskNotFoundError, TaskSchemaError, TaskStore
from tests.unit.test_task_legacy_adapter import app, close, preview, seed, write_candidate, REVIEW_TIME, CONTENT


def confirm(app, **options):
    value = preview(app)
    return app.service.confirm(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=value['fingerprint'], reviewer='审核人', reason='逐字核实原文',
        now=REVIEW_TIME, **options)


def edit(app, view, patch, **options):
    return app.service.edit_fields(app.workspace, view['task']['id'], patch=patch,
        expected_candidate_fingerprint=view['candidate_fingerprint'],
        expected_review_revision=view['review_revision'], reviewer='人工编辑人', reason='人工修正字段',
        now=REVIEW_TIME + timedelta(hours=1), **options)


def version(app, content):
    artifact = replace(app.artifact, content=content)
    spans = tuple(segment.evidence_span for segment in parse_source_text(artifact).segments)
    app.repository.update_source(app.workspace, artifact.artifact_id, '修订原文', artifact, spans)
    app.repository.set_ingestion_result(app.workspace, artifact.artifact_id, artifact.artifact_version_id,
        status='COMPLETED', candidate_count=1, review_required_count=0)
    return artifact


def test_confirmation_patch_is_one_audit_with_original_and_final_values_and_no_rewritten_candidate(app):
    before = [tuple(row) for row in app.temporal._connection.execute('SELECT * FROM semantic_candidate')]
    raw = preview(app)
    view = confirm(app, patch={'owner': '王芳', 'deadline': None, 'deadline_text': '待重新确认'})
    task = view['task']
    assert task['owner'] == '王芳' and task['deadline'] is None and task['deadline_text'] == '待重新确认'
    assert task['title'] == '联调报告' and task['status'] == 'open'
    assert view['review_status'] == 'approved' and len(view['events']) == len(view['review_history']) == 1
    assert view['events'][0]['new_value'] == task
    audit = view['review_history'][0]
    assert audit['action'] == 'confirm' and audit['reviewer'] == '审核人' and audit['reason'] == '逐字核实原文'
    assert audit['reviewed_at'] == REVIEW_TIME.isoformat()
    assert audit['candidate_fingerprint'] == raw['fingerprint']
    assert audit['candidate']['original_payload'] == raw['original_payload']
    assert audit['original_values']['owner'] == '李明' and audit['final_values']['owner'] == '王芳'
    assert audit['human_fields'] == ['owner', 'deadline']
    assert view['field_sources']['owner']['origin'] == view['field_sources']['deadline']['origin'] == 'human'
    assert view['field_sources']['title']['origin'] == 'source'
    assert view['field_sources']['owner']['candidate_fingerprint'] == raw['fingerprint']
    assert view['events'][0]['evidence_role'] == 'context'
    assert view['original_candidate'] == raw
    link = app.store.get_link(app.workspace, app.observation.observation_id)
    assert link.candidate_fingerprint == raw['fingerprint']
    assert app.store.replay_task(app.workspace, task['id']).to_dict() == task
    assert [tuple(row) for row in app.temporal._connection.execute('SELECT * FROM semantic_candidate')] == before
    assert app.provider.calls == 1  # Only the fixture import used the offline provider.
    assert app.service.list_candidates(app.workspace) == ()
    assert len(app.service.list_candidates(app.workspace, include_reviewed=True)) == 1


def test_source_values_confirmed_without_patch_and_manual_edit_is_context_only_with_one_audit(app):
    initial = confirm(app)
    assert all(item['origin'] == 'source' for item in initial['field_sources'].values())
    view = edit(app, initial, {'title': '整理联调验收报告', 'owner': None,
                             'deadline': '2026-10-16', 'deadline_text': '人工约定 10/16'})
    assert view['task']['title'] == '整理联调验收报告' and view['task']['owner'] is None
    assert [event['event_type'] for event in view['events']] == [
        'created', 'owner_changed', 'deadline_changed', 'title_changed']
    assert len(view['review_history']) == 2 and view['review_history'][-1]['action'] == 'edit'
    assert view['original_candidate'] == initial['original_candidate']
    for event in view['events'][1:]:
        assert event['meeting_time'] is None and event['evidence_role'] == 'context'
        assert event['evidence'] == initial['events'][0]['evidence']
        assert event['evidence'][0]['meeting_time'] == initial['events'][0]['meeting_time']
        assert event['actor'] == '人工编辑人'
    assert view['field_sources']['owner']['reviewer'] == '人工编辑人'
    assert app.store.replay_task(app.workspace, view['task']['id']).to_dict() == view['task']
    no_change = edit(app, view, {'owner': None})
    assert no_change['task'] == view['task'] and no_change['events'] == view['events']
    assert len(no_change['review_history']) == 3 and no_change['review_revision'] != view['review_revision']
    assert app.provider.calls == 1


def test_missing_extracted_title_is_not_raw_text_and_must_be_supplied_by_human(tmp_path):
    value = seed(tmp_path / 'missing-title.sqlite', parts=())
    try:
        raw = preview(value)
        assert raw['candidate']['title'] is None
        with pytest.raises(ValueError, match='title'):
            confirm(value)
        assert value.store.list_tasks(value.workspace) == ()
        assert value.store.get_link(value.workspace, value.observation.observation_id) is None
        view = confirm(value, patch={'title': '人工补充的任务名'})
        assert view['task']['title'] == '人工补充的任务名'
        assert view['review_history'][0]['original_values']['title'] is None
        assert view['field_sources']['title']['origin'] == 'human'
        assert view['original_candidate']['candidate']['title'] is None
    finally:
        close(value)


@pytest.mark.parametrize('patch', [
    {'deadline': None}, {'deadline_text': None}, {'title': None}, {'title': '  '},
    {'deadline': '10/16', 'deadline_text': '10/16'}, {'status': 'done'}, {'owner': ''},
])
def test_invalid_patch_does_not_consume_or_partially_create_candidate(app, patch):
    with pytest.raises(ValueError):
        confirm(app, patch=patch)
    assert app.store.list_tasks(app.workspace) == ()
    assert app.store.get_link(app.workspace, app.observation.observation_id) is None
    assert app.store._connection.execute('SELECT COUNT(*) FROM task_review_event').fetchone()[0] == 0


def test_reject_has_one_audit_no_task_and_repeated_or_stale_reviews_are_rejected(app):
    value = preview(app)
    with pytest.raises(ReviewConflictError):
        app.service.reject(app.workspace, app.observation.observation_id,
            expected_candidate_fingerprint='old-fingerprint', reviewer='审核人', reason='拒绝')
    audit = app.service.reject(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=value['fingerprint'], reviewer='审核人', reason='此项不需要建立任务', now=REVIEW_TIME)
    assert audit['action'] == 'reject' and audit['task_id'] is None and audit['review_status'] == 'rejected'
    assert audit['candidate_fingerprint'] == value['fingerprint'] and audit['candidate'] == value
    assert app.store.list_tasks(app.workspace) == ()
    for operation in (lambda: confirm(app), lambda: app.service.reject(app.workspace, app.observation.observation_id,
            expected_candidate_fingerprint=value['fingerprint'], reviewer='审核人', reason='重复拒绝')):
        with pytest.raises(CandidateAlreadyReviewedError):
            operation()
    assert app.store._connection.execute('SELECT COUNT(*) FROM task_review_event').fetchone()[0] == 1


def test_confirmed_candidate_cannot_be_confirmed_twice_or_edited_with_stale_revision(app):
    view = confirm(app)
    with pytest.raises(CandidateAlreadyReviewedError):
        confirm(app)
    newer = edit(app, view, {'owner': '王芳'})
    with pytest.raises(ReviewConflictError):
        edit(app, view, {'owner': '赵敏'})
    assert app.service.get_task(app.workspace, view['task']['id']) == newer


@pytest.mark.parametrize('failure_at', ['task_event', 'task_observation_link', 'task_review_event'])
def test_confirmation_failure_rolls_back_snapshot_created_event_link_and_review_audit(app, failure_at):
    connection = app.store._connection
    connection.execute(f'''CREATE TRIGGER inject_confirm_failure BEFORE INSERT ON {failure_at}
        BEGIN SELECT RAISE(ABORT,'injected confirmation failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='injected confirmation failure'):
        confirm(app)
    assert app.store.list_tasks(app.workspace) == ()
    for table in ('task_event', 'task_observation_link', 'task_review_event'):
        assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    connection.execute('DROP TRIGGER inject_confirm_failure')
    assert confirm(app)['review_status'] == 'approved'


@pytest.mark.parametrize('failure_at', ['second_field_event', 'audit'])
def test_manual_edit_failure_rolls_back_all_actual_changes_and_review_revision(app, failure_at):
    initial = confirm(app)
    connection = app.store._connection
    table = 'task_event' if failure_at == 'second_field_event' else 'task_review_event'
    condition = "WHEN NEW.event_type='deadline_changed'" if failure_at == 'second_field_event' else ''
    connection.execute(f'''CREATE TRIGGER inject_edit_failure BEFORE INSERT ON {table} {condition}
        BEGIN SELECT RAISE(ABORT,'injected edit failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='injected edit failure'):
        edit(app, initial, {'owner': '王芳', 'deadline': None, 'deadline_text': None})
    assert app.service.get_task(app.workspace, initial['task']['id']) == initial
    assert app.store.replay_task(app.workspace, initial['task']['id']).to_dict() == initial['task']


def test_unobserved_roundtrip_of_already_existing_versions_invalidates_once_and_latches(app):
    other = CONTENT.replace('2026年10月12日', '2026年10月16日')
    version(app, other)
    version(app, CONTENT)
    initial = confirm(app, patch={'owner': '人工负责人'})
    task_id = initial['task']['id']
    app.store.set_status(app.workspace, task_id, 'done', actor='负责人', reason='手工完成', now=REVIEW_TIME)
    old = app.store.get_task(app.workspace, task_id)
    max_rowid = app.store._connection.execute('SELECT MAX(rowid) FROM ll_source_version').fetchone()[0]
    version(app, other)
    version(app, CONTENT)  # No query occurred between changes; neither version is new.
    assert app.store._connection.execute('SELECT MAX(rowid) FROM ll_source_version').fetchone()[0] == max_rowid
    changed = app.service.get_task(app.workspace, task_id)
    assert changed['review_status'] == 'needs_review' and changed['task']['status'] == 'done'
    assert app.store.get_task(app.workspace, task_id) == old
    assert [audit['action'] for audit in changed['review_history']] == ['confirm', 'needs_review']
    assert app.service.get_task(app.workspace, task_id) == changed
    with pytest.raises(ReviewConflictError):
        edit(app, initial, {'owner': '不允许依赖旧确认'})
    current = preview(app)
    assert current['fingerprint'] != initial['candidate_fingerprint']
    with pytest.raises(ReviewConflictError):
        app.service.confirm(app.workspace, app.observation.observation_id,
            expected_candidate_fingerprint=current['fingerprint'], reviewer='重审人', reason='显式重审')
    renewed = app.service.confirm(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=current['fingerprint'], expected_review_revision=changed['review_revision'],
        reviewer='重审人', reason='同一ID候选重新核对当前原文', now=REVIEW_TIME + timedelta(days=1))
    assert renewed['review_status'] == 'approved' and renewed['task'] == changed['task']
    assert renewed['field_sources'] == changed['field_sources']
    assert [audit['action'] for audit in renewed['review_history']] == ['confirm', 'needs_review', 'reconfirm']
    assert [event['event_type'] for event in renewed['events']] == ['created', 'status_changed']
    assert renewed['task']['id'] == task_id
    assert app.store.get_link(app.workspace, app.observation.observation_id).candidate_fingerprint == initial['candidate_fingerprint']


@pytest.mark.parametrize('cause', ['version', 'withdraw_restore', 'missing_evidence', 'evidence_hash', 'new_variant'])
def test_source_invalidity_blocks_edit_persists_needs_review_and_preserves_business_history(app, cause):
    initial = confirm(app)
    connection = app.store._connection
    if cause == 'version':
        version(app, CONTENT + '\n[2026-10-08T10:01:00+08:00] 主持人: 补充一句。')
    elif cause == 'withdraw_restore':
        connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=?', (app.workspace,))
        connection.execute('UPDATE ll_source SET active=1 WHERE workspace_id=?', (app.workspace,))
    elif cause == 'missing_evidence':
        connection.execute('DELETE FROM ll_source_evidence WHERE workspace_id=?', (app.workspace,))
    elif cause == 'evidence_hash':
        connection.execute('UPDATE ll_source_evidence SET quote_sha256=?', ('f' * 64,))
    else:
        write_candidate(app, replace(app.candidate, candidate_id='new-variant',
                                    confidence=replace(app.candidate.confidence, overall=.7)), insert=True)
    with pytest.raises(ReviewConflictError):
        edit(app, initial, {'owner': '不能用失效确认修改'})
    view = app.service.get_task(app.workspace, initial['task']['id'])
    assert view['review_status'] == 'needs_review'
    assert view['task'] == initial['task'] and view['events'] == initial['events']
    assert len(view['review_history']) == 2
    assert app.service.get_task(app.workspace, initial['task']['id']) == view


def test_workspace_boundaries_apply_to_candidates_tasks_events_audit_and_mutations(app):
    initial = confirm(app)
    another = app.repository.create_workspace('另一个工作区')['workspace_id']
    assert app.service.list_candidates(another) == () and app.service.list_tasks(another) == ()
    with pytest.raises(TaskNotFoundError):
        app.service.get_task(another, initial['task']['id'])
    with pytest.raises(TaskNotFoundError):
        app.service.edit_fields(another, initial['task']['id'], patch={'owner': None},
            expected_candidate_fingerprint=initial['candidate_fingerprint'], expected_review_revision=initial['review_revision'],
            reviewer='审核人', reason='越权')
    with pytest.raises(ReviewConflictError):
        app.service.confirm(another, app.observation.observation_id,
            expected_candidate_fingerprint=initial['candidate_fingerprint'], reviewer='审核人', reason='越权')
    assert app.service.get_task(app.workspace, initial['task']['id']) == initial
    assert app.service.list_tasks(app.workspace, owner='李明')[0] == initial


@pytest.mark.parametrize('operation', ['confirm', 'edit'])
def test_concurrent_review_and_edits_across_connections_apply_exactly_once(app, operation):
    second_store = TaskStore(app.path)
    second = TaskReviewService(second_store, LegacyTaskCandidateSource())
    services = (app.service, second)
    initial = confirm(app) if operation == 'edit' else None
    candidate = preview(app)
    barrier = Barrier(4)
    def run(index):
        service = services[index % 2]
        barrier.wait(timeout=10)
        try:
            if operation == 'confirm':
                return service.confirm(app.workspace, app.observation.observation_id,
                    expected_candidate_fingerprint=candidate['fingerprint'], reviewer='并发审核人', reason='人工确认',
                    now=REVIEW_TIME)
            return service.edit_fields(app.workspace, initial['task']['id'], patch={'owner': f'人工负责人{index}'},
                expected_candidate_fingerprint=initial['candidate_fingerprint'],
                expected_review_revision=initial['review_revision'], reviewer='并发审核人', reason='人工修改',
                now=REVIEW_TIME + timedelta(hours=1))
        except (CandidateAlreadyReviewedError, ReviewConflictError):
            return None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(run, range(4)))
        assert sum(result is not None for result in results) == 1
        task = app.store.list_tasks(app.workspace)[0]
        view = app.service.get_task(app.workspace, task.id)
        assert len(view['events']) == len(view['review_history']) == (1 if operation == 'confirm' else 2)
        assert app.store.replay_task(app.workspace, task.id) == task
    finally:
        second_store.close()


def test_review_schema_is_independent_append_only_reopenable_and_preserves_base_version(app):
    view = confirm(app)
    connection = app.store._connection
    connection.execute('PRAGMA user_version=77')
    connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=?', (app.workspace,))
    for table in ('task_review_event', 'task_review_source_change'):
        for operation in ('UPDATE ' + table + ' SET workspace_id=workspace_id', 'DELETE FROM ' + table):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                connection.execute(operation)
    assert [tuple(row) for row in connection.execute('SELECT * FROM task_schema_metadata')] == [(1, 1)]
    with TaskStore(app.path) as reopened:
        service = TaskReviewService(reopened, LegacyTaskCandidateSource())
        assert reopened._connection.execute('PRAGMA user_version').fetchone()[0] == 77
        stale = service.get_task(app.workspace, view['task']['id'])
        assert stale['review_status'] == 'needs_review'
        assert stale['task'] == view['task']
    connection.execute('UPDATE task_review_schema_metadata SET version=99')
    with pytest.raises(TaskSchemaError, match='version'):
        TaskReviewService(app.store, LegacyTaskCandidateSource())

def test_original_acceptance_wording_keeps_unknown_owner_until_explicit_human_patch(tmp_path):
    text = '联调报告由李明负责，2026年10月12日前完成。'
    value = seed(tmp_path / 'literal-wording.sqlite', text=text,
                 content='[2026-10-08T10:00:00+08:00] 主持人: ' + text)
    try:
        raw = preview(value)
        assert raw['candidate']['owner'] is None
        assert 'OWNER_UNGROUNDED' in value.observation.validation_issues
        view = confirm(value, patch={'owner': '李明'})
        assert view['task']['owner'] == '李明'
        assert view['field_sources']['owner']['origin'] == 'human'
        assert view['review_history'][0]['original_values']['owner'] is None
        assert view['original_candidate']['original_payload'] == raw['original_payload']
    finally:
        close(value)


def test_default_candidate_list_exposes_fresh_same_id_variant_and_revision_for_explicit_reconfirmation(app):
    initial = confirm(app, patch={'owner': '人工负责人'})
    write_candidate(app, replace(app.candidate, candidate_id='new-variant',
                                confidence=replace(app.candidate.confidence, overall=.7)), insert=True)
    values = app.service.list_candidates(app.workspace)
    assert len(values) == 2
    for value in values:
        assert value['review_status'] == 'needs_review' and value['task_id'] == initial['task']['id']
        assert value['review_revision'] != initial['review_revision']
    chosen = values[1]
    view = app.service.confirm(app.workspace, app.observation.observation_id,
        expected_candidate_fingerprint=chosen['fingerprint'], expected_review_revision=chosen['review_revision'],
        reviewer='重审人', reason='精确选择第二个变体重新核对', now=REVIEW_TIME + timedelta(days=1),
        patch={'title': '人工修订标题'})
    assert view['review_status'] == 'approved' and view['task']['owner'] == '人工负责人'
    assert view['task']['title'] == '人工修订标题' and view['task']['id'] == initial['task']['id']
    assert [event['event_type'] for event in view['events']] == ['created', 'title_changed']
    assert view['field_sources']['owner'] == initial['field_sources']['owner']
    assert app.service.list_candidates(app.workspace) == ()


def test_base_v1_task_without_review_audit_is_conservatively_needs_review_not_silently_approved(app):
    from linkloom.tasks.models import TaskCandidateInput
    candidate = TaskCandidateInput.from_dict(preview(app)['candidate'])
    task = app.store.create_from_candidate(app.workspace, candidate, reviewer='旧版审核人', reason='v1低层写入', now=REVIEW_TIME)
    view = app.service.get_task(app.workspace, task.id)
    assert view['review_status'] == 'needs_review' and view['review_revision'] is None
    assert view['review_history'] == () and view['field_sources'] == {}
    assert view['original_candidate'] is None and view['task'] == task.to_dict()
    with pytest.raises(ReviewConflictError):
        app.service.edit_fields(app.workspace, task.id, patch={'owner': None},
            expected_candidate_fingerprint=candidate.fingerprint, expected_review_revision='1',
            reviewer='人工', reason='不能隐式批准')
    assert app.store.get_task(app.workspace, task.id) == task


def test_independent_review_schema_first_open_is_serialized_across_connections(tmp_path):
    from linkloom.ui.product import ProductRepository
    for attempt in range(4):
        path = tmp_path / f'review-first-open-{attempt}.sqlite'
        repository = ProductRepository(path)
        repository.create_workspace('并发审核初始化')
        barrier = Barrier(4)
        def open_service(_):
            barrier.wait(timeout=10)
            store = TaskStore(path)
            try:
                return TaskReviewService(store, LegacyTaskCandidateSource())
            except BaseException:
                store.close()
                raise
        futures = []
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(open_service, index) for index in range(4)]
            services = [future.result() for future in futures]
            for service in services:
                assert [tuple(row) for row in service.store._connection.execute(
                    'SELECT * FROM task_review_schema_metadata')] == [(1, 1)]
                assert [tuple(row) for row in service.store._connection.execute(
                    'SELECT * FROM task_schema_metadata')] == [(1, 1)]
                assert service.store._connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='task_review_source_epoch'").fetchone()[0] == 1
        finally:
            for future in futures:
                if future.done() and not future.cancelled() and future.exception() is None:
                    future.result().store.close()
            repository.close()


def test_generic_adapter_preserves_multisource_evidence_and_checks_each_source_epoch(app):
    import hashlib
    import json
    from linkloom.tasks.models import EvidenceReference, TaskCandidateInput, canonical_json
    from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError, digest
    raw = preview(app)
    original = TaskCandidateInput.from_dict(raw['candidate'])
    secondary = RawArtifact(workspace_id=app.workspace, artifact_id='appendix.txt', source_type='timestamped_text',
        content='[2026-10-08T10:01:00+08:00] 主持人: 报告需要附测试结果。',
        source_uri=f'linkloom://{app.workspace}/appendix.txt')
    segment = parse_source_text(secondary).segments[0]
    span = segment.evidence_span
    app.repository.create_source(app.workspace, secondary.artifact_id, '原文附件', secondary, (span,))
    second_evidence = EvidenceReference(app.workspace, secondary.artifact_id, secondary.artifact_version_id,
        span.quote, span.quote_sha256, message_id=span.message_id, line_start=span.line_start, line_end=span.line_end,
        char_start=span.char_start, char_end=span.char_end, source_sha256=span.content_hash, meeting_time=segment.event_time)
    candidate = replace(original, candidate_id='generic-multi-reference', evidence=(*original.evidence, second_evidence),
                        candidate_version='generic-test/v1', source_fingerprint=digest({'unmapped': 'retained'}))
    app.store._connection.execute('CREATE TABLE test_review_input(workspace_id TEXT,payload TEXT)')
    app.store._connection.execute('INSERT INTO test_review_input VALUES(?,?)', (app.workspace, canonical_json(candidate.to_dict())))
    class GenericSource:
        def list_candidates(self, connection, workspace):
            assert connection.in_transaction
            results = []
            for row in connection.execute('SELECT payload FROM test_review_input WHERE workspace_id=?', (workspace,)):
                value = TaskCandidateInput.from_dict(json.loads(row[0]))
                contexts = []
                for evidence in value.evidence:
                    source = connection.execute('''SELECT source.active, source.current_version_id, version.content
                        FROM ll_source AS source JOIN ll_source_version AS version
                        ON version.workspace_id=source.workspace_id AND version.source_id=source.source_id
                            AND version.artifact_version_id=source.current_version_id
                        WHERE source.workspace_id=? AND source.source_id=?''',
                        (workspace, evidence.source_id)).fetchone()
                    if (not source or not source['active'] or source['current_version_id'] != evidence.source_version_id
                        or source['content'][evidence.char_start:evidence.char_end] != evidence.quote
                        or hashlib.sha256(source['content'].encode('utf-8')).hexdigest() != evidence.source_sha256):
                        raise CandidateUnavailableError('generic source failed binding verification')
                    epoch = connection.execute('''SELECT COALESCE(MAX(sequence),0) FROM task_review_source_change
                        WHERE workspace_id=? AND source_id=?''', (workspace, evidence.source_id)).fetchone()[0]
                    versions = [tuple(item) for item in connection.execute('''SELECT rowid,artifact_version_id,content_sha256
                        FROM ll_source_version WHERE workspace_id=? AND source_id=? ORDER BY rowid''',
                        (workspace, evidence.source_id))]
                    contexts.append((evidence.source_id, evidence.source_version_id, epoch, versions))
                results.append(CandidateSnapshot(value, digest(contexts), row[0]))
            return tuple(results)
    service = TaskReviewService(app.store, GenericSource())
    current = service.preview_candidate(app.workspace, candidate.candidate_id)
    view = service.confirm(app.workspace, candidate.candidate_id, expected_candidate_fingerprint=current['fingerprint'],
                           reviewer='人工', reason='核对两段不同来源原文', now=REVIEW_TIME)
    evidence = view['events'][0]['evidence']
    assert evidence == [item.to_dict() for item in candidate.evidence]
    assert view['events'][0]['source_version_id'] is None
    assert [item['source_id'] for item in evidence] == ['weekly.txt', 'appendix.txt']
    assert len({item['source_version_id'] for item in evidence}) == 2
    app.store._connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=? AND source_id=?',
                                  (app.workspace, secondary.artifact_id))
    app.store._connection.execute('UPDATE ll_source SET active=1 WHERE workspace_id=? AND source_id=?',
                                  (app.workspace, secondary.artifact_id))
    stale = service.get_task(app.workspace, view['task']['id'])
    assert stale['review_status'] == 'needs_review' and stale['task'] == view['task']
    assert stale['events'] == view['events']