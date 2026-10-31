"""Human cross-meeting merge and immutable reverse-order undo on real source catalogues."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import sqlite3
from threading import Barrier

import pytest

from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tasks.merge import MergeBlockedError
from linkloom.tasks.models import TaskCandidateInput, TaskStatus
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import CandidateUnavailableError, ReviewConflictError
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskNotFoundError, TaskStore
from tests.unit.test_task_legacy_adapter import app, close, seed, preview, CONTENT
from tests.unit.test_task_rebind import NOW, original_task, revise


B_TEXT='由王芳负责联调验收报告，2026年10月16日前完成。'
B_CONTENT='[2026-10-10T10:00:00+08:00] 评审人: '+B_TEXT
C_TEXT='由陈宁负责联调终版报告，2026年10月20日前完成。'
C_CONTENT='[2026-10-12T11:00:00+08:00] 负责人: '+C_TEXT


def setup(app):
    initial=original_task(app)
    artifact,candidate=revise(app,content=B_CONTENT,text=B_TEXT,parts=('联调验收报告',),source_id='meeting-b.txt')
    return initial,artifact,candidate.task_observation.observation_id


def prepare(app,task_id,candidate_id,fields=('owner','deadline'),*,service=None,day=1):
    service=service or app.service
    raw=service.preview_candidate(app.workspace,candidate_id)
    return service.preview_merge(app.workspace,task_id,candidate_id,expected_candidate_fingerprint=raw['fingerprint'],
                                 apply_fields=list(fields),now=NOW+timedelta(days=day))


def apply(app,value,*,service=None,day=1,**options):
    arguments=dict(expected_candidate_fingerprint=value['candidate_fingerprint'],expected_review_revision=value['review_revision'],
        expected_preview_token=value['preview_token'],apply_fields=value['apply_fields'],reviewer='跨会审核人',reason='人工核对为同一工作',
        now=NOW+timedelta(days=day))
    arguments.update(options)
    return (service or app.service).merge(app.workspace,value['task']['id'],value['candidate']['candidate']['candidate_id'],**arguments)


def undo(app,view,merge_id=None,*,service=None,day=4,**options):
    service=service or app.service
    merge_id=merge_id or next(item['id'] for item in reversed(view['merge_history']) if item['operation']=='merge' and item['undone_by'] is None)
    value=service.preview_undo_merge(app.workspace,view['task']['id'],merge_id,now=NOW+timedelta(days=day))
    arguments=dict(expected_review_revision=value['review_revision'],expected_preview_token=value['preview_token'],
                   reviewer='撤销审核人',reason='重新检查后撤销本次匹配',now=NOW+timedelta(days=day))
    arguments.update(options)
    return service.undo_merge(app.workspace,view['task']['id'],merge_id,**arguments)


def business(value): return {key:item for key,item in value.items() if key!='updated_at'}


def state(app):
    connection=app.store._connection
    tables=[row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'task%'")]
    return {table:tuple(tuple(row) for row in connection.execute('SELECT * FROM '+table)) for table in sorted(tables)}


def test_two_meetings_merge_owner_and_deadline_have_three_events_each_with_original_evidence(app):
    initial,artifact,identifier=setup(app)
    value=prepare(app,initial['task']['id'],identifier)
    assert value['differences']['owner']==dict(current='李明',candidate='王芳')
    assert value['same_task']=='human_decision_required'
    view=apply(app,value)
    assert view['task']['owner']=='王芳' and view['task']['deadline']=='2026-10-16'
    assert view['task']['title']==initial['task']['title'] and view['task']['status']=='open'
    assert [item['event_type'] for item in view['events']]==['created','owner_changed','deadline_changed']
    created,owner,deadline=view['events']
    assert created['evidence'][0]['quote'] in CONTENT and created['evidence'][0]['source_id']==app.artifact.artifact_id
    for event in (owner,deadline):
        assert event['evidence'][0]['quote']==B_TEXT and event['evidence'][0]['line_start']==1
        assert event['evidence'][0]['source_version_id']==artifact.artifact_version_id
        assert event['meeting_time']==event['evidence'][0]['meeting_time']=='2026-10-10T02:00:00+00:00'
        assert event['before_evidence'][0]['source_id']==app.artifact.artifact_id
        assert event['after_evidence'][0]['source_id']=='meeting-b.txt'
        assert event['meeting_contexts'][0]['source_name']=='另一来源'
        assert event['operation']=='merge' and event['actor']=='跨会审核人'
    assert owner['old_value']=='李明' and owner['new_value']=='王芳'
    assert deadline['old_value']['deadline']=='2026-10-12' and deadline['new_value']['deadline']=='2026-10-16'
    assert view['human_declarations']['same_task'] is True and view['human_declarations']['origin']=='human_statement'
    assert view['merge_machine_validation']['same_task']=='not_machine_verified'
    assert view['field_sources']['title']==initial['field_sources']['title']
    assert len(view['timeline'])==3 and app.store.replay_task(app.workspace,view['task']['id']).to_dict()==view['task']
    assert app.service.get_task(app.workspace,view['task']['id'])==view


@pytest.mark.parametrize('fields',[[],['owner'],['deadline'],['title']])
def test_only_explicitly_selected_fields_and_provenance_change_with_status_preserved(app,fields):
    initial,_,identifier=setup(app)
    app.store.set_status(app.workspace,initial['task']['id'],'done',actor='负责人',reason='手工完成',now=NOW+timedelta(hours=1))
    value=prepare(app,initial['task']['id'],identifier,fields)
    before=dict(value['current_values'])
    view=apply(app,value)
    assert view['task']['status']=='done'
    for field in ('title','owner','deadline'):
        if field not in fields:
            assert view['review_history'][-1]['final_values'][field]==before[field]
            assert view['field_sources'][field]==initial['field_sources'][field]
    assert len(view['events'])==2+len(fields)
    assert view['review_history'][-1]['action']=='merge'
    assert app.store.replay_task(app.workspace,view['task']['id']).to_dict()==view['task']


def test_undo_restores_both_sides_and_keeps_all_history_and_original_unique_receipt(app):
    initial,_,identifier=setup(app)
    merged=apply(app,prepare(app,initial['task']['id'],identifier))
    historical=tuple(app.store._connection.execute('SELECT * FROM task_observation_link WHERE observation_id=?',(identifier,)).fetchone())
    restored=undo(app,merged)
    assert business(restored['task'])==business(initial['task'])
    assert restored['field_sources']==initial['field_sources'] and restored['original_candidate']==initial['original_candidate']
    assert restored['review_status']=='approved' and restored['task']['updated_at']>merged['task']['updated_at']
    assert len(restored['events'])==5 and all(event['operation']=='undo_merge' for event in restored['events'][-2:])
    assert all(event['meeting_time'] is None and event['evidence_role']=='context' for event in restored['events'][-2:])
    assert app.store.get_link(app.workspace,identifier) is None
    assert tuple(app.store._connection.execute('SELECT * FROM task_observation_link WHERE observation_id=?',(identifier,)).fetchone())==historical
    pending=app.service.list_candidates(app.workspace)
    assert len(pending)==1 and pending[0]['candidate']['candidate_id']==identifier and pending[0]['reviewed'] is False
    assert restored['merge_history'][0]['undone_by']['reviewer']=='撤销审核人'
    assert app.store.replay_task(app.workspace,restored['task']['id']).to_dict()==restored['task']


@pytest.mark.parametrize('action',['create','reject','merge','lowlevel_create'])
def test_released_candidate_can_be_processed_again_without_rewriting_old_link(app,action):
    initial,_,identifier=setup(app)
    merged=apply(app,prepare(app,initial['task']['id'],identifier));restored=undo(app,merged)
    row=app.service.preview_candidate(app.workspace,identifier)
    if action=='merge':
        result=apply(app,prepare(app,initial['task']['id'],identifier,day=5),day=5)
        assert result['task']['id']==initial['task']['id']
    elif action=='create':
        result=app.service.confirm(app.workspace,identifier,expected_candidate_fingerprint=row['fingerprint'],
            reviewer='新审核人',reason='撤销后独立建立',now=NOW+timedelta(days=5))
        assert result['task']['id']!=initial['task']['id']
    elif action=='reject':
        app.service.reject(app.workspace,identifier,expected_candidate_fingerprint=row['fingerprint'],
            reviewer='新审核人',reason='撤销后忽略',now=NOW+timedelta(days=5))
        assert app.store.get_link(app.workspace,identifier).review_action=='reject'
    else:
        app.store.create_from_candidate(app.workspace,TaskCandidateInput.from_dict(row['candidate']),reviewer='底层调用人',
            reason='同一有效账本再次确认',now=NOW+timedelta(days=5))
    assert app.store.get_link(app.workspace,identifier) is not None
    with pytest.raises(CandidateAlreadyReviewedError):
        app.store.reject_candidate(app.workspace,TaskCandidateInput.from_dict(row['candidate']),
                                  reviewer='重复调用',reason='必须拒绝',now=NOW+timedelta(days=6))


def test_three_meetings_reverse_order_undo_uses_true_field_origin_and_can_undo_parent(app):
    initial,_,bid=setup(app)
    one=apply(app,prepare(app,initial['task']['id'],bid,['owner']))
    _,cc=revise(app,content=C_CONTENT,text=C_TEXT,owner='陈宁',deadline='2026年10月20日',parts=('联调终版报告',),source_id='meeting-c.txt')
    cid=cc.task_observation.observation_id
    two=apply(app,prepare(app,initial['task']['id'],cid,['deadline'],day=2),day=2)
    with pytest.raises(MergeBlockedError) as error:
        app.service.preview_undo_merge(app.workspace,two['task']['id'],one['merge_history'][0]['id'],now=NOW+timedelta(days=3))
    assert error.value.blocking_operations[0]['id']==two['merge_history'][-1]['id']
    restored_one=undo(app,two,day=3)
    event=restored_one['events'][-1]
    assert event['event_type']=='deadline_changed' and event['new_value']['deadline']=='2026-10-12'
    assert {item['source_id'] for item in event['evidence']}=={app.artifact.artifact_id,'meeting-c.txt'}
    assert {item['source_id'] for item in event['after_evidence']}=={app.artifact.artifact_id}
    assert restored_one['field_sources']==one['field_sources']
    restored=undo(app,restored_one,one['merge_history'][0]['id'],day=4)
    assert business(restored['task'])==business(initial['task'])
    assert restored['field_sources']==initial['field_sources']
    assert app.store.replay_task(app.workspace,restored['task']['id']).to_dict()==restored['task']


@pytest.mark.parametrize('kind',['status','status_roundtrip','edit','noop_edit'])
def test_later_manual_changes_block_undo_without_overwriting_them(app,kind):
    initial,_,identifier=setup(app)
    merged=apply(app,prepare(app,initial['task']['id'],identifier))
    if kind.startswith('status'):
        app.store.set_status(app.workspace,merged['task']['id'],'blocked',actor='负责人',reason='后续手动状态',now=NOW+timedelta(days=2))
        if kind=='status_roundtrip':
            app.store.set_status(app.workspace,merged['task']['id'],'open',actor='负责人',reason='回到相同状态也保留后续事件',now=NOW+timedelta(days=2))
    else:
        app.service.edit_fields(app.workspace,merged['task']['id'],patch={'owner':'后来的人'} if kind=='edit' else {},
            expected_candidate_fingerprint=merged['candidate_fingerprint'],expected_review_revision=merged['review_revision'],
            reviewer='后续编辑人',reason='保留此修改',now=NOW+timedelta(days=2))
    before=state(app)
    with pytest.raises(MergeBlockedError) as error: undo(app,merged)
    assert error.value.blocking_operations and state(app)==before


def test_old_preview_never_revives_after_merge_undo_and_candidate_reprocessing(app):
    initial,_,identifier=setup(app)
    old=prepare(app,initial['task']['id'],identifier)
    merged=apply(app,old);undo(app,merged)
    before=state(app)
    with pytest.raises(ReviewConflictError): apply(app,old,day=5)
    assert state(app)==before
    fresh=prepare(app,initial['task']['id'],identifier,day=5)
    assert fresh['candidate_assignment_revision'] and fresh['preview_token']!=old['preview_token']
    apply(app,fresh,day=5)


@pytest.mark.parametrize('tamper',['token','review','fields','candidate','status'])
def test_merge_revalidates_every_preview_binding_and_preserves_state_on_conflict(app,tamper):
    initial,_,identifier=setup(app)
    value=prepare(app,initial['task']['id'],identifier)
    options={}
    if tamper=='token': options['expected_preview_token']='0'*64
    elif tamper=='review': options['expected_review_revision']='0'
    elif tamper=='fields': options['apply_fields']=['title']
    elif tamper=='candidate': options['expected_candidate_fingerprint']='0'*64
    else: app.store.set_status(app.workspace,initial['task']['id'],'blocked',actor='负责人',reason='预览后状态变更',now=NOW+timedelta(hours=1))
    before=state(app)
    with pytest.raises((ReviewConflictError,CandidateAlreadyReviewedError)): apply(app,value,**options)
    assert state(app)==before


@pytest.mark.parametrize('fields',[None,'owner',['unknown'],['owner','owner']])
def test_merge_rejects_implicit_or_invalid_field_selection(app,fields):
    initial,_,identifier=setup(app)
    raw=app.service.preview_candidate(app.workspace,identifier)
    with pytest.raises(ValueError):
        app.service.preview_merge(app.workspace,initial['task']['id'],identifier,
                                  expected_candidate_fingerprint=raw['fingerprint'],apply_fields=fields,now=NOW+timedelta(days=1))


def test_retained_old_meeting_field_dependency_is_revalidated_and_history_kept(app):
    initial,_,identifier=setup(app)
    merged=apply(app,prepare(app,initial['task']['id'],identifier,['owner']))
    with app.repository._connection:
        app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE workspace_id=? AND source_id=?',
                                           (app.workspace,app.artifact.artifact_id))
    current=app.service.get_task(app.workspace,merged['task']['id'])
    assert current['review_status']=='needs_review' and current['task']==merged['task']
    assert current['events']==merged['events']
    assert len(current['review_history'])==len(merged['review_history'])+1
    restored=undo(app,current)
    assert restored['review_status']=='needs_review' and restored['field_sources']==initial['field_sources']
    assert business(restored['task'])==business(initial['task'])


def test_removed_incoming_meeting_does_not_prevent_undo_or_fabricate_current_pending_evidence(app):
    initial,artifact,identifier=setup(app)
    merged=apply(app,prepare(app,initial['task']['id'],identifier))
    app.repository.delete_source(app.workspace,artifact.artifact_id)
    current=app.service.get_task(app.workspace,merged['task']['id'])
    assert current['review_status']=='needs_review'
    restored=undo(app,current)
    assert restored['review_status']=='approved' and business(restored['task'])==business(initial['task'])
    assert app.store.get_link(app.workspace,identifier) is None
    assert all(row['candidate']['candidate_id']!=identifier for row in app.service.list_candidates(app.workspace))
    assert restored['merge_history'][0]['after_review']['candidate']['candidate']['evidence'][0]['quote']==B_TEXT


@pytest.mark.parametrize('failure_at',['owner_changed','deadline_changed','task_candidate_assignment','task_review_event','task_merge_operation'])
def test_merge_failure_rolls_back_snapshot_events_claim_audit_and_operation(app,failure_at):
    initial,_,identifier=setup(app);value=prepare(app,initial['task']['id'],identifier)
    table='task_event' if failure_at.endswith('_changed') else failure_at
    where=" WHEN NEW.event_type='"+failure_at+"'" if table=='task_event' else ''
    app.store._connection.execute(f"CREATE TRIGGER fail_merge BEFORE INSERT ON {table}{where} BEGIN SELECT RAISE(ABORT,'injected'); END")
    before=state(app)
    with pytest.raises(sqlite3.IntegrityError,match='injected'): apply(app,value)
    assert state(app)==before and app.store.get_link(app.workspace,identifier) is None


@pytest.mark.parametrize('failure_at',['task_event','task_candidate_assignment','task_review_event','task_merge_operation'])
def test_undo_failure_rolls_back_compensation_release_audit_and_operation(app,failure_at):
    initial,_,identifier=setup(app);merged=apply(app,prepare(app,initial['task']['id'],identifier))
    app.store._connection.execute(f"CREATE TRIGGER fail_undo BEFORE INSERT ON {failure_at} BEGIN SELECT RAISE(ABORT,'injected'); END")
    before=state(app)
    with pytest.raises(sqlite3.IntegrityError,match='injected'): undo(app,merged)
    assert state(app)==before and app.store.get_link(app.workspace,identifier) is not None


@pytest.mark.parametrize('operation',['merge','undo'])
def test_concurrent_operations_on_two_connections_commit_once(app,operation):
    initial,_,identifier=setup(app);value=prepare(app,initial['task']['id'],identifier)
    merged=apply(app,value) if operation=='undo' else None
    other_store=TaskStore(app.path);other=TaskReviewService(other_store,app.service.source)
    barrier=Barrier(4)
    def run(index):
        barrier.wait();service=app.service if index%2==0 else other
        try:
            if operation=='merge': result=apply(app,value,service=service)
            else: result=undo(app,merged,service=service)
            return ('ok',result['task']['id'])
        except (CandidateAlreadyReviewedError,ReviewConflictError): return ('conflict',None)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(run,range(4)))
        assert [row[0] for row in results].count('ok')==1
        assert app.store._connection.execute('SELECT COUNT(*) FROM task_merge_operation WHERE operation=?',
                                            ('merge' if operation=='merge' else 'undo_merge',)).fetchone()[0]==1
    finally: other_store.close()


@pytest.mark.parametrize('table',['task_candidate_assignment','task_merge_operation'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_assignment_and_merge_history_are_append_only(app,table,operation):
    initial,_,identifier=setup(app);apply(app,prepare(app,initial['task']['id'],identifier))
    statement=f'DELETE FROM {table}' if operation=='DELETE' else f'UPDATE {table} SET workspace_id=workspace_id'
    with pytest.raises(sqlite3.IntegrityError,match='append-only'): app.store._connection.execute(statement)


def test_reopen_keeps_history_effective_candidate_state_and_old_schema_metadata(app):
    initial,_,identifier=setup(app);merged=apply(app,prepare(app,initial['task']['id'],identifier));restored=undo(app,merged)
    app.store._connection.execute('PRAGMA user_version=73')
    app.store.close();app.store=TaskStore(app.path);app.service=TaskReviewService(app.store,app.service.source)
    assert app.service.get_task(app.workspace,initial['task']['id'])==restored
    assert app.store.get_link(app.workspace,identifier) is None
    assert app.store._connection.execute('PRAGMA user_version').fetchone()[0]==73
    assert [tuple(row) for row in app.store._connection.execute('SELECT id,version FROM task_schema_metadata')]==[(1,1)]
    assert [tuple(row) for row in app.store._connection.execute('SELECT id,version FROM task_review_schema_metadata')]==[(1,1)]
    assert app.store.replay_task(app.workspace,initial['task']['id']).to_dict()==restored['task']


def test_undo_restores_manual_field_source_without_claiming_source_quote_supports_manual_value(app):
    raw=preview(app)
    initial=app.service.confirm(app.workspace,app.observation.observation_id,expected_candidate_fingerprint=raw['fingerprint'],
        patch={'owner':'人工指定人'},reviewer='人工编辑人',reason='原文以外的人工调整',now=NOW)
    _,candidate=revise(app,content=B_CONTENT,text=B_TEXT,parts=('联调验收报告',),source_id='meeting-b.txt')
    merged=apply(app,prepare(app,initial['task']['id'],candidate.task_observation.observation_id,['owner']))
    restored=undo(app,merged)
    event=restored['events'][-1]
    assert restored['task']['owner']=='人工指定人'
    assert event['after_field_source']==initial['field_sources']['owner']
    assert event['after_field_source']['origin']=='human' and event['after_evidence_role']=='context'
    assert event['before_field_source']['origin']=='source' and event['before_evidence_role']=='source'
    assert event['evidence_role']=='context' and event['meeting_time'] is None


def test_reconfirm_of_merged_task_cannot_reapprove_retained_invalid_field_sources(app):
    initial,_,identifier=setup(app)
    merged=apply(app,prepare(app,initial['task']['id'],identifier,['owner']))
    app.repository.delete_source(app.workspace,app.artifact.artifact_id)
    current=app.service.get_task(app.workspace,merged['task']['id'])
    assert current['review_status']=='needs_review'
    raw=app.service.preview_candidate(app.workspace,identifier)
    before=state(app)
    with pytest.raises(ReviewConflictError,match='field sources'):
        app.service.confirm(app.workspace,identifier,expected_candidate_fingerprint=raw['fingerprint'],
            expected_review_revision=current['review_revision'],reviewer='复核人',reason='不能确认仍依赖失效A的值',now=NOW+timedelta(days=2))
    assert state(app)==before
    repaired=app.service.confirm(app.workspace,identifier,expected_candidate_fingerprint=raw['fingerprint'],
        expected_review_revision=current['review_revision'],patch={'title':'人工重新确认标题','deadline':None,'deadline_text':'人工确认待定'},
        reviewer='复核人',reason='明确替换失效来源的字段为人工值',now=NOW+timedelta(days=2))
    assert repaired['review_status']=='approved'
    assert repaired['field_sources']['title']['origin']=='human'
    assert repaired['field_sources']['deadline']['origin']=='human'


def test_v2_multispan_low_confidence_merge_and_undo_keep_each_original_time_and_expression(tmp_path):
    from tests.unit.test_task_generated_candidates import seed as generated_seed,close as generated_close,row as generated_row,NOW as generated_now
    from tests.unit.test_meeting_tasks_v2 import Adapter,ROSTER,extractor
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.tasks.models import ConfidenceMetadata
    app=generated_seed(tmp_path/'v2-cross-meeting.sqlite')
    try:
        app.intake.save(app.workspace,app.artifact.artifact_id,app.artifact.artifact_version_id,app.batch)
        raw=app.service.list_candidates(app.workspace)[0]
        initial=app.service.confirm(app.workspace,raw['candidate']['candidate_id'],expected_candidate_fingerprint=raw['fingerprint'],
            reviewer='首会审核人',reason='确认首会原文',now=generated_now)
        content=('[2034-01-04T09:00:00Z] Y: I will take over sending the package.\n'
                 '[2034-01-04T09:01:00Z] X: We reviewed a different document.\n'
                 '[2034-01-04T09:02:00Z] X: Please send it after the release.')
        artifact=RawArtifact(app.workspace,'followup-v2.txt','timestamped_text',content,
                             source_uri=f'linkloom://{app.workspace}/followup-v2.txt')
        app.repository.create_source(app.workspace,artifact.artifact_id,'Follow-up meeting',artifact,
                                     tuple(item.evidence_span for item in parse_source_text(artifact).segments))
        batch=extractor(Adapter([generated_row(owner_ids=['Y'],deadline={'raw_text':'after the release','kind':'EVENT_BASED'})])).extract(artifact,ROSTER)
        identifier=batch.candidates[0].candidate_id
        app.intake.save(app.workspace,artifact.artifact_id,artifact.artifact_version_id,batch,
                        confidence_by_candidate={identifier:ConfidenceMetadata('LOW','需要人工核对跨会责任承接。')})
        raw=app.service.preview_candidate(app.workspace,identifier)
        value=app.service.preview_merge(app.workspace,initial['task']['id'],identifier,
            expected_candidate_fingerprint=raw['fingerprint'],apply_fields=['owner','deadline'],now=generated_now+timedelta(days=1))
        assert value['low_confidence'] is True and value['candidate']['candidate']['confidence_reason']
        merged=app.service.merge(app.workspace,initial['task']['id'],identifier,
            expected_candidate_fingerprint=raw['fingerprint'],expected_review_revision=value['review_revision'],
            expected_preview_token=value['preview_token'],apply_fields=['owner','deadline'],reviewer='跨会审核人',
            reason='逐段人工确认同一工作',now=generated_now+timedelta(days=1))
        assert merged['task']['owner']=='person-y' and merged['task']['deadline'] is None
        assert merged['task']['deadline_text']=='after the release' and len(merged['events'])==3
        for event in merged['events'][1:]:
            assert event['meeting_time'] is None and len(event['evidence'])==2
            assert [item['line_start'] for item in event['evidence']]==[1,3]
            assert [item['meeting_time'] for item in event['evidence']]==['2034-01-04T09:00:00+00:00','2034-01-04T09:02:00+00:00']
            assert all(item['source_name']=='Follow-up meeting' for item in event['meeting_contexts'])
        merge_id=merged['merge_history'][-1]['id']
        preview_undo=app.service.preview_undo_merge(app.workspace,initial['task']['id'],merge_id,now=generated_now+timedelta(days=2))
        restored=app.service.undo_merge(app.workspace,initial['task']['id'],merge_id,
            expected_review_revision=preview_undo['review_revision'],expected_preview_token=preview_undo['preview_token'],
            reviewer='撤销人',reason='人工撤销并重新审核',now=generated_now+timedelta(days=2))
        assert business(restored['task'])==business(initial['task'])
        pending=app.service.list_candidates(app.workspace)
        assert pending[0]['candidate']['candidate_id']==identifier and pending[0]['low_confidence'] is True
        assert app.store.replay_task(app.workspace,initial['task']['id']).to_dict()==restored['task']
    finally: generated_close(app)


@pytest.mark.parametrize('boundary',['workspace','task','candidate','undo_workspace','undo_task'])
def test_workspace_and_resource_boundaries_reject_foreign_merge_and_undo(app,boundary):
    initial,_,identifier=setup(app)
    other=app.repository.create_workspace('another')['workspace_id']
    value=prepare(app,initial['task']['id'],identifier)
    if boundary in ('workspace','task','candidate'):
        workspace=other if boundary=='workspace' else app.workspace
        task_id='task_missing' if boundary=='task' else initial['task']['id']
        candidate_id='candidate_missing' if boundary=='candidate' else identifier
        before=state(app)
        with pytest.raises((TaskNotFoundError,ReviewConflictError,CandidateUnavailableError)):
            app.service.preview_merge(workspace,task_id,candidate_id,
                expected_candidate_fingerprint=value['candidate_fingerprint'],apply_fields=['owner'],now=NOW+timedelta(days=1))
        assert state(app)==before
    else:
        merged=apply(app,value);merge_id=merged['merge_history'][-1]['id'];before=state(app)
        with pytest.raises((TaskNotFoundError,ReviewConflictError)):
            app.service.preview_undo_merge(other if boundary=='undo_workspace' else app.workspace,
                'task_missing' if boundary=='undo_task' else initial['task']['id'],merge_id,now=NOW+timedelta(days=2))
        assert state(app)==before
