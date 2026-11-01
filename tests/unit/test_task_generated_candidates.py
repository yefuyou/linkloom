"""Already-generated v2 batches join formal review without any model requests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact, evidence_ref_for, segment_id_for
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.meeting_tasks_v2 import MeetingTaskBatch, MeetingTaskExtractor
from linkloom.tasks.candidate_source import ProductTaskCandidateSource
from linkloom.tasks.candidate_store import GeneratedTaskBatchStore, StoredTaskCandidateSource
from linkloom.tasks.models import ConfidenceMetadata, canonical_json
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import CandidateUnavailableError, ReviewConflictError
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskSchemaError, TaskStore
from linkloom.ui.product import ProductRepository
from tests.unit.test_meeting_tasks_v2 import Adapter, ROSTER, extractor, task


NOW = datetime(2034, 2, 1, tzinfo=UTC)
CONTENT = ('[2034-01-02T09:00:00Z] X: I will send the package.\n'
           '[2034-01-02T09:01:00Z] Y: The design was reviewed separately.\n'
           '[2034-01-02T09:02:00Z] Y: Please do it before the next workshop, or after the release.\n'
           '[2034-01-02T09:03:00Z] X: I finished the demo.')


def row(**options):
    return task(evidence=[{'message_ordinal': 0, 'supports': ['work', 'adoption', 'owner']},
                          {'message_ordinal': 2, 'supports': ['deadline']}], **options)


def completed():
    return task(task='Finish the demo', deadline={'raw_text': None, 'kind': 'UNSPECIFIED'},
        end_state='COMPLETED', deliverable_key='demo', evidence=[{'message_ordinal': 3,
        'supports': ['work', 'adoption', 'owner', 'completion']}])


def seed(path, *, rows=None):
    repository = ProductRepository(path)
    workspace = repository.create_workspace('Generated task candidates')['workspace_id']
    artifact = RawArtifact(workspace, 'generated.txt', 'timestamped_text', CONTENT,
        source_uri=f'linkloom://{workspace}/generated.txt', ingestion_time=NOW)
    segments = parse_source_text(artifact).segments
    repository.create_source(workspace, artifact.artifact_id, 'Original meeting', artifact,
                             tuple(segment.evidence_span for segment in segments))
    provider = Adapter([row()] if rows is None else rows)
    batch = extractor(provider).extract(artifact, ROSTER)
    repository.set_ingestion_result(workspace, artifact.artifact_id, artifact.artifact_version_id,
        status='COMPLETED', candidate_count=len(batch.candidates), review_required_count=0)
    store = TaskStore(path)
    intake = GeneratedTaskBatchStore(store)
    service = TaskReviewService(store, ProductTaskCandidateSource())
    return SimpleNamespace(path=path, repository=repository, workspace=workspace, artifact=artifact,
        store=store, intake=intake, service=service, batch=batch, provider=provider)


def close(app):
    app.store.close()
    app.repository.close()


@pytest.fixture
def app(tmp_path):
    value = seed(tmp_path/'generated.sqlite')
    try:
        yield value
    finally:
        close(value)


def save(app, batch=None, **options):
    return app.intake.save(app.workspace, app.artifact.artifact_id, app.artifact.artifact_version_id,
                          batch if batch is not None else app.batch, now=NOW, **options)


def queued(app):
    return app.service.list_candidates(app.workspace)


def confirm(app, item=None):
    item = item if item is not None else queued(app)[0]
    return app.service.confirm(app.workspace, item['candidate']['candidate_id'],
        expected_candidate_fingerprint=item['fingerprint'], reviewer='审核人', reason='核对完整原文', now=NOW)


def counts(app):
    return tuple(app.store._connection.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                 for name in ('task_candidate_batch', 'task_candidate_entry', 'task', 'task_event', 'task_review_event'))


def test_real_v2_multispan_batch_persists_reopens_confirms_without_extraction_or_provider_calls(app):
    calls = len(app.provider.requests)
    with patch.object(MeetingTaskExtractor, 'extract', side_effect=AssertionError('no extraction during intake/review')):
        receipt = save(app)
        assert app.intake.get_batch(app.workspace, receipt['id'])['payload'] == json.loads(canonical_json(app.batch.to_dict()))
        app.store.close()
        app.store = TaskStore(app.path)
        app.intake = GeneratedTaskBatchStore(app.store)
        app.service = TaskReviewService(app.store, ProductTaskCandidateSource())
        item = queued(app)[0]
        candidate = item['candidate']
        assert candidate['title'] == 'Send the package' and candidate['owner'] == 'person-x'
        assert candidate['deadline'] is None and candidate['deadline_text'] == 'before the next workshop'
        assert candidate['meeting_time'] is None and item['low_confidence'] is False
        assert 'confidence' not in candidate and 'confidence_reason' not in candidate
        assert [e['line_start'] for e in candidate['evidence']] == [1, 3]
        assert [e['meeting_time'] for e in candidate['evidence']] == ['2034-01-02T09:00:00+00:00', '2034-01-02T09:02:00+00:00']
        raw = item['original_payload']['candidate']
        assert len(raw['evidence']) == 2 and raw['evidence'][1]['supports'] == ['deadline']
        assert raw['reason'] and raw['member_ids'] and raw['deadline']['kind'] == 'EVENT_BASED'
        view = confirm(app, item)
        assert view['events'][0]['evidence'] == candidate['evidence']
        assert view['original_candidate']['original_payload'] == item['original_payload']
        assert app.store.replay_task(app.workspace, view['task']['id']).to_dict() == view['task']
        assert len(app.provider.requests) == calls == 1
        assert queued(app) == ()


def test_multiple_and_unknown_owners_preserved_without_choosing_first_or_auto_confirming(tmp_path):
    for index, owners in enumerate((['X','Y'], [])):
        app = seed(tmp_path/f'owner-{index}.sqlite', rows=[row(owner_ids=owners)])
        try:
            save(app)
            item = queued(app)[0]
            assert item['candidate']['owner'] is None
            assert item['original_payload']['candidate']['owner_ids'] == (['person-x', 'person-y'] if owners else [])
            assert counts(app)[2:] == (0,0,0)
        finally:
            close(app)


def test_batch_diagnostics_excluded_candidates_and_conflicting_raw_deadlines_remain_visible(tmp_path):
    rows = [row(), row(deadline={'raw_text': 'after the release', 'kind': 'EVENT_BASED'}), completed()]
    app = seed(tmp_path/'variants.sqlite', rows=rows)
    try:
        receipt = save(app)
        full = app.intake.get_batch(app.workspace, receipt['id'])['payload']
        assert len(full['excluded']) == 1 and full['excluded'][0]['end_state'] == 'COMPLETED'
        assert len(full['responses'][0]['tasks']) == 3 and full['coverage_complete'] is True
        items = queued(app)
        assert len(items) == 1 and items[0]['candidate']['deadline_text'] is None
        assert [d['raw_text'] for d in items[0]['original_payload']['candidate']['deadline_variants']] == [
            'before the next workshop', 'after the release']
        assert counts(app)[:2] == (1, 2)
    finally:
        close(app)


def test_incomplete_coverage_and_rejected_diagnostics_are_not_erased(app):
    batch = replace(app.batch, coverage_complete=False,
        rejected=({'window':0, 'candidate_index':1, 'reason':'SOURCE_EVIDENCE_INVALID'},))
    receipt = save(app, batch)
    assert receipt['payload']['rejected'][0]['reason'] == 'SOURCE_EVIDENCE_INVALID'
    assert receipt['payload']['coverage_complete'] is False
    assert queued(app)[0]['original_payload']['coverage_complete'] is False


def test_duplicate_receipt_and_identical_candidate_across_batches_are_idempotent_and_deduplicated(app):
    first = save(app)
    assert save(app) == first
    batch = extractor(Adapter([row(), completed()])).extract(app.artifact, ROSTER)
    second = save(app, batch)
    assert second['id'] != first['id'] and counts(app)[:2] == (2, 3)
    assert len(queued(app)) == 1
    assert app.service.preview_candidate(app.workspace, app.batch.candidates[0].candidate_id) == {
        key:value for key,value in queued(app)[0].items() if key in ('candidate','context_token','original_payload','extraction_conflict','fingerprint')}
    view = confirm(app)
    assert len(view['events']) == 1
    with pytest.raises(CandidateAlreadyReviewedError):
        confirm(app, app.service.list_candidates(app.workspace, include_reviewed=True)[0])


def test_high_low_metadata_variants_invalidate_prior_approval_and_need_exact_fingerprint(app):
    identifier = app.batch.candidates[0].candidate_id
    save(app, confidence_by_candidate={identifier: ConfidenceMetadata('HIGH', '明确采用。')})
    before = queued(app)[0]
    view = confirm(app, before)
    save(app, confidence_by_candidate={identifier: ConfidenceMetadata('LOW', '负责人仍需核对。')})
    current = app.service.get_task(app.workspace, view['task']['id'])
    assert current['review_status'] == 'needs_review' and current['task'] == view['task']
    items = queued(app)
    assert len(items) == 2 and all(item['extraction_conflict'] for item in items)
    assert all(item['fingerprint'] != before['fingerprint'] for item in items)
    low = next(item for item in items if item['low_confidence'])
    assert low['candidate']['confidence_reason'] == '负责人仍需核对。'
    with pytest.raises(ReviewConflictError):
        app.service.preview_candidate(app.workspace, identifier)
    assert app.service.preview_candidate(app.workspace, identifier,
        expected_candidate_fingerprint=low['fingerprint'])['candidate']['confidence'] == 'LOW'
    assert app.service.get_task(app.workspace, view['task']['id']) == current


def test_low_marker_and_original_reason_survive_persisted_rejection_and_removed_source(app):
    identifier = app.batch.candidates[0].candidate_id
    save(app, confidence_by_candidate={identifier:ConfidenceMetadata('LOW','需要人工核对。')})
    item = queued(app)[0]
    app.service.reject(app.workspace, identifier, expected_candidate_fingerprint=item['fingerprint'],
        reviewer='拒绝审核人', reason='不创建任务', now=NOW)
    assert app.service.list_candidates(app.workspace, include_reviewed=True)[0]['low_confidence'] is True
    app.repository.delete_source(app.workspace, app.artifact.artifact_id)
    app.store.close()
    app.store=TaskStore(app.path)
    app.service=TaskReviewService(app.store, ProductTaskCandidateSource())
    rejected = app.service.list_candidates(app.workspace, include_reviewed=True)[0]
    assert rejected['review_status']=='rejected' and rejected['reviewer']=='拒绝审核人'
    assert rejected['reason']=='不创建任务' and rejected['low_confidence'] is True
    assert rejected['candidate']['confidence_reason']=='需要人工核对。' and not rejected['is_current']
    assert rejected['original_payload']['candidate']['reason'] == item['original_payload']['candidate']['reason']


def test_raw_deadline_whitespace_survives_intake_confirmation_snapshot_and_replay(tmp_path):
    app=seed(tmp_path/'raw.sqlite', rows=[row(deadline={'raw_text':' before the next workshop', 'kind':'EVENT_BASED'})])
    try:
        save(app)
        item=queued(app)[0]
        assert item['candidate']['deadline_text']==' before the next workshop'
        view=confirm(app,item)
        assert view['task']['deadline_text']==' before the next workshop'
        assert view['events'][0]['new_value']['deadline_text']==' before the next workshop'
        assert app.store.replay_task(app.workspace,view['task']['id']).deadline_text==' before the next workshop'
    finally:
        close(app)


@pytest.mark.parametrize('change', ['wrong_source_ref','substring','wrong_quote','wrong_owner_type','wrong_state','bad_support','missing_work','wrong_deadline','false_coverage','wrong_version','unknown_confidence'])
def test_invalid_structure_or_any_bad_evidence_rolls_back_entire_batch(app, change):
    candidate=app.batch.candidates[0]
    confidence=None
    if change in ('wrong_source_ref','substring','wrong_quote'):
        span=candidate.evidence[1].span
        if change=='wrong_source_ref':
            span=replace(span,source_ref='linkloom://wrong')
        elif change=='substring':
            quote=span.quote[1:]; sha=hashlib.sha256(quote.encode('utf8')).hexdigest()
            sid=segment_id_for(span.artifact_version_id,span.message_id,span.char_start+1,span.char_end,sha)
            span=replace(span,char_start=span.char_start+1,quote=quote,quote_sha256=sha,segment_id=sid,evidence_ref=evidence_ref_for(sid))
            assert span.verify(app.artifact)
        else:
            object.__setattr__(span,'quote','tampered')
        candidate=replace(candidate,evidence=(candidate.evidence[0],replace(candidate.evidence[1],span=span)))
    elif change=='wrong_owner_type': candidate=replace(candidate,owner_ids=('person-x',False))
    elif change=='wrong_state': candidate=replace(candidate,adopted='yes')
    elif change=='bad_support': candidate=replace(candidate,evidence=(replace(candidate.evidence[0],supports=('invented',)),*candidate.evidence[1:]))
    elif change=='missing_work': candidate=replace(candidate,evidence=(replace(candidate.evidence[0],supports=('owner',)),*candidate.evidence[1:]))
    elif change=='wrong_deadline': candidate=replace(candidate,deadline=replace(candidate.deadline,raw_text='2034-01-99'))
    elif change=='unknown_confidence': confidence={'absent':ConfidenceMetadata('LOW','理由')}
    batch=replace(app.batch,candidates=(candidate,))
    if change=='false_coverage': batch=replace(batch,rejected=({'window':0,'candidate_index':1,'reason':'invalid'},))
    if change=='wrong_version':
        class Unsupported(MeetingTaskBatch):
            def to_dict(self):
                return {**super().to_dict(),'version':'future-v3-unimplemented'}
        batch=Unsupported(**{key:getattr(batch,key) for key in MeetingTaskBatch.__dataclass_fields__})
    before=counts(app)
    with pytest.raises((ValueError,CandidateUnavailableError)):
        save(app,batch,confidence_by_candidate=confidence)
    assert counts(app)==before


@pytest.mark.parametrize('change',['foreign_workspace','unknown_workspace','wrong_source','old_version','inactive','source_tampering','evidence_removed'])
def test_current_catalogue_is_verified_on_intake_and_review(app, change):
    workspace=app.workspace; source=app.artifact.artifact_id; version=app.artifact.artifact_version_id
    if change=='foreign_workspace': workspace=app.repository.create_workspace('other')['workspace_id']
    elif change=='unknown_workspace': workspace='unknown'
    elif change=='wrong_source': source='another-source'
    elif change=='old_version':
        artifact=replace(app.artifact,content=app.artifact.content+'\n[2034-01-02T09:04:00Z] X: Revised notes.')
        app.repository.update_source(app.workspace,source,'Revision',artifact,tuple(s.evidence_span for s in parse_source_text(artifact).segments))
    elif change=='inactive': app.repository.delete_source(app.workspace,source)
    else:
        with app.repository._connection:
            if change=='source_tampering': app.repository._connection.execute('UPDATE ll_source_version SET content=content||? WHERE workspace_id=?',('tampered',workspace))
            else: app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE line_start=3 AND workspace_id=?',(workspace,))
    before=counts(app)
    with pytest.raises((ValueError,CandidateUnavailableError)):
        app.intake.save(workspace,source,version,app.batch)
    assert counts(app)==before


def test_source_change_and_restore_are_latched_and_epoch_bound_with_each_original_quote(app):
    receipt=save(app); initial=queued(app)[0]; view=confirm(app,initial)
    changed=replace(app.artifact,content=app.artifact.content+'\n[2034-01-02T09:04:00Z] X: New note.')
    app.repository.update_source(app.workspace,changed.artifact_id,'Revision',changed,tuple(s.evidence_span for s in parse_source_text(changed).segments))
    assert app.service.get_task(app.workspace,view['task']['id'])['review_status']=='needs_review'
    app.repository.set_ingestion_result(app.workspace, changed.artifact_id, changed.artifact_version_id,
        status='COMPLETED', candidate_count=0, review_required_count=0)
    app.repository.update_source(app.workspace,app.artifact.artifact_id,'Restore',app.artifact,tuple(s.evidence_span for s in parse_source_text(app.artifact).segments))
    restored=queued(app)[0]
    assert restored['fingerprint'] != initial['fingerprint']
    assert app.service.get_task(app.workspace,view['task']['id'])['review_status']=='needs_review'
    assert app.intake.get_batch(app.workspace,receipt['id'])==receipt


def test_evidence_withdrawal_after_confirmation_requires_review_without_destroying_history(app):
    save(app); view=confirm(app)
    with app.repository._connection:
        app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE line_start=3 AND workspace_id=?',(app.workspace,))
    current=app.service.get_task(app.workspace,view['task']['id'])
    assert current['review_status']=='needs_review' and current['events']==view['events']
    assert current['task']==view['task'] and len(current['review_history'])==2


def test_insert_failure_rolls_back_receipt_and_first_entry(app):
    batch=extractor(Adapter([row(),completed()])).extract(app.artifact,ROSTER)
    app.store._connection.execute("""CREATE TRIGGER fail_second_entry BEFORE INSERT ON task_candidate_entry
        WHEN NEW.reviewable=0 BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
    before=counts(app)
    with pytest.raises(sqlite3.IntegrityError,match='injected failure'):
        save(app,batch)
    assert counts(app)==before


@pytest.mark.parametrize('table',['task_candidate_batch','task_candidate_entry'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_generated_history_tables_are_append_only(app, table, operation):
    save(app)
    statement=f'DELETE FROM {table}' if operation=='DELETE' else f'UPDATE {table} SET workspace_id=workspace_id'
    with pytest.raises(sqlite3.IntegrityError,match='append-only'):
        app.store._connection.execute(statement)
    assert counts(app)[:2]==(1,1)


def test_concurrent_same_batch_intake_on_two_connections_commits_once(app):
    other_store=TaskStore(app.path); other=GeneratedTaskBatchStore(other_store)
    barrier=Barrier(4)
    def run(index):
        barrier.wait()
        intake=app.intake if index%2==0 else other
        return intake.save(app.workspace,app.artifact.artifact_id,app.artifact.artifact_version_id,app.batch)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(run,range(4)))
        assert len({r['id'] for r in results})==1 and counts(app)[:2]==(1,1)
    finally:
        other_store.close()


def test_empty_batch_still_verifies_source_and_retains_coverage_unknown(app):
    empty=MeetingTaskBatch((),(),(),(),False,())
    receipt=save(app,empty)
    assert receipt['payload']['coverage_complete'] is False and queued(app)==()
    assert counts(app)[:2]==(1,0)
    with pytest.raises(CandidateUnavailableError):
        app.intake.save(app.workspace,'unknown',app.artifact.artifact_version_id,empty)


def test_additive_reopen_keeps_unrelated_tables_old_metadata_and_workspace_foreign_keys(app):
    connection=app.store._connection
    connection.execute('CREATE TABLE unrelated(value TEXT)'); connection.execute("INSERT INTO unrelated VALUES('retained')")
    connection.execute('PRAGMA user_version=73')
    save(app)
    other=TaskStore(app.path)
    try:
        GeneratedTaskBatchStore(other)
        assert other._connection.execute('PRAGMA user_version').fetchone()[0]==73
        assert other._connection.execute('SELECT value FROM unrelated').fetchone()[0]=='retained'
        assert tuple(other._connection.execute('SELECT id,version FROM task_schema_metadata').fetchone())==(1,1)
        assert tuple(other._connection.execute('SELECT id,version FROM task_review_schema_metadata').fetchone())==(1,1)
        foreign=app.repository.create_workspace('foreign')['workspace_id']
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("""INSERT INTO task_candidate_entry(batch_id,workspace_id,candidate_id,input_json,
                original_payload_json,reviewable) VALUES(?,?,?,?,?,?)""",(save(app)['id'],foreign,'forged','{}','{}',1))
        with pytest.raises(CandidateUnavailableError): app.intake.get_batch(foreign,save(app)['id'])
        with app.store._transaction():
            assert StoredTaskCandidateSource().list_candidates(connection,foreign)==()
    finally:
        other.close()


def test_composition_reads_both_legacy_and_new_candidates_without_changing_legacy_fingerprint(tmp_path):
    from tests.unit.test_task_legacy_adapter import seed as legacy_seed, close as legacy_close, preview
    app=legacy_seed(tmp_path/'combined.sqlite')
    try:
        old=preview(app)
        artifact=RawArtifact(app.workspace,'generated.txt','timestamped_text',CONTENT,source_uri=f'linkloom://{app.workspace}/generated.txt')
        app.repository.create_source(app.workspace,artifact.artifact_id,'Generated',artifact,tuple(s.evidence_span for s in parse_source_text(artifact).segments))
        batch=extractor(Adapter([row()])).extract(artifact,ROSTER)
        GeneratedTaskBatchStore(app.store).save(app.workspace,artifact.artifact_id,artifact.artifact_version_id,batch)
        app.service=TaskReviewService(app.store,ProductTaskCandidateSource())
        items=app.service.list_candidates(app.workspace)
        assert len(items)==2
        legacy=next(item for item in items if item['candidate']['candidate_id']==app.observation.observation_id)
        assert legacy['fingerprint']==old['fingerprint'] and legacy['low_confidence'] is False
    finally:
        legacy_close(app)


class FixtureFormatAdapter:
    """Independent test protocol, deliberately not an unmerged extractor format."""
    format_version = 'fixture-generated/v1'

    def encode(self, batch):
        from linkloom.tasks.meeting_adapter import MeetingV2BatchAdapter
        return {'format_version': self.format_version, 'generated': MeetingV2BatchAdapter().encode(batch)}

    def prepare(self, connection, workspace_id, source_id, source_version_id, payload, confidence):
        from linkloom.tasks.candidate_store import PreparedBatch, PreparedCandidate
        from linkloom.tasks.meeting_adapter import MeetingV2BatchAdapter
        if set(payload) != {'format_version', 'generated'} or payload['format_version'] != self.format_version:
            raise ValueError('invalid independent fixture format')
        original = MeetingV2BatchAdapter().prepare(connection, workspace_id, source_id, source_version_id,
            payload['generated'], {key.removeprefix('fixture_'): value for key, value in confidence.items()})
        candidates = tuple(PreparedCandidate(replace(item.candidate,
            candidate_id='fixture_' + item.candidate.candidate_id, candidate_version=self.format_version),
            canonical_json({'format_version': self.format_version, 'generated': json.loads(item.original_payload_json)}),
            item.reviewable) for item in original.candidates)
        return PreparedBatch(original.source_context, candidates)


def test_registry_keeps_old_v2_and_independent_format_readable_after_reopen(app):
    native = save(app)
    adapter = FixtureFormatAdapter()
    fixture_id = 'fixture_' + app.batch.candidates[0].candidate_id
    # A custom default writer retains the old v2 registration automatically.
    custom = GeneratedTaskBatchStore(app.store, adapter=adapter)
    other = custom.save(app.workspace, app.artifact.artifact_id, app.artifact.artifact_version_id,
        app.batch, confidence_by_candidate={fixture_id: ConfidenceMetadata('LOW', 'Fixture confidence.')}, now=NOW)
    assert other['format_version'] == adapter.format_version
    assert custom.get_batch(app.workspace, native['id']) == native
    app.store.close()
    app.store = TaskStore(app.path)
    app.intake = GeneratedTaskBatchStore(app.store, adapters=(adapter,))
    app.service = TaskReviewService(app.store,
        ProductTaskCandidateSource(StoredTaskCandidateSource(adapters=(adapter,))))
    assert app.intake.get_batch(app.workspace, native['id']) == native
    assert app.intake.get_batch(app.workspace, other['id']) == other
    items = queued(app)
    assert len(items) == 2
    assert {item['candidate']['candidate_version'] for item in items} == {adapter.format_version, app.batch.to_dict()['version']}
    low = next(item for item in items if item['low_confidence'])
    view = confirm(app, low)
    assert view['original_candidate']['candidate']['confidence_reason'] == 'Fixture confidence.'
    assert view['original_candidate']['candidate']['candidate_version'] == adapter.format_version
    assert app.store.replay_task(app.workspace, view['task']['id']).to_dict() == view['task']
    assert len(queued(app)) == 1 and queued(app)[0]['candidate']['candidate_id'] == app.batch.candidates[0].candidate_id
    # No caller may silently reinterpret the custom bytes with the default v2 decoder.
    with app.store._transaction():
        with pytest.raises(CandidateUnavailableError):
            StoredTaskCandidateSource().list_candidates(app.store._connection, app.workspace)


def test_registry_selects_explicit_format_and_rejects_unknown_or_duplicate_registration(app):
    adapter = FixtureFormatAdapter()
    intake = GeneratedTaskBatchStore(app.store, adapters=(adapter,))
    receipt = intake.save(app.workspace, app.artifact.artifact_id, app.artifact.artifact_version_id,
        app.batch, format_version=adapter.format_version, now=NOW)
    assert receipt['format_version'] == adapter.format_version
    before = counts(app)
    with pytest.raises(ValueError, match='no trusted adapter'):
        intake.save(app.workspace, app.artifact.artifact_id, app.artifact.artifact_version_id,
            app.batch, format_version='unregistered-format')
    assert counts(app) == before
    with pytest.raises(ValueError, match='duplicate adapter'):
        GeneratedTaskBatchStore(app.store, adapters=(adapter, FixtureFormatAdapter()))
