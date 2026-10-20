from dataclasses import replace
from datetime import timedelta
import sqlite3

import pytest

from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent
from linkloom.semantic_ingestion.task_relation_models import RelationAssessment
from tests.task_revision_support import public_cases, run_case


def identity():
    row = run_case(next(c for c in public_cases() if c['case_id']=='D04'))['predictions'][0]
    return RelationAssessment.from_dict(row['same_task'])


def event(store, assessment, kind='RETRACTED', **kwargs):
    prior=store.last_event(assessment.subject.workspace_id,assessment.assessment_id)
    return RelationEvent.create(workspace_id=assessment.subject.workspace_id,
        assessment_id=assessment.assessment_id,event_type=kind,reason='manual evidence correction',
        actor='human:offline-reviewer',previous_event_id=prior.event_id,
        recorded_at=prior.recorded_at+timedelta(seconds=1),**kwargs)


def test_reopen_idempotent_preserves_first_snapshot_and_terminal_event(tmp_path):
    path=tmp_path/'relations.sqlite'
    assessment=identity()
    with TaskRelationStore(path) as store:
        assert store.put_assessment(assessment)
        assert not store.put_assessment(replace(assessment,assessed_at=assessment.assessed_at+timedelta(days=1)))
        revoke=event(store,assessment)
        assert store.append_event(revoke)
        assert not store.append_event(revoke)
        assert not store.put_assessment(assessment)
    with TaskRelationStore(path) as reopened:
        assert reopened.last_event(assessment.subject.workspace_id,assessment.assessment_id)==revoke
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM task_relation_assessment').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM task_relation_event').fetchone()[0]==2


def test_append_only_and_invalid_transition_or_stale_predecessor(tmp_path):
    assessment=identity()
    path=tmp_path/'events.sqlite'
    with TaskRelationStore(path) as store:
        store.put_assessment(assessment)
        revoke=event(store,assessment)
        store.append_event(revoke)
        with pytest.raises(ValueError):
            store.append_event(replace(revoke,event_type='ACTIVE',previous_event_id=revoke.event_id,
                recorded_at=revoke.recorded_at+timedelta(seconds=1)))
        with pytest.raises(ValueError):
            store.append_event(replace(revoke,reason='different event with stale predecessor'))
    with sqlite3.connect(path) as conn:
        for sql in ('UPDATE task_relation_assessment SET predicate="RELATED_TO"',
                    'DELETE FROM task_relation_assessment','DELETE FROM task_relation_event',
                    'UPDATE task_relation_event SET event_hash="corrupt"'):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_replace_requires_real_same_workspace_record_and_leaves_old_payload(tmp_path):
    assessment=identity()
    with TaskRelationStore(tmp_path/'replace.sqlite') as store:
        store.put_assessment(assessment)
        with pytest.raises(ValueError):
            store.append_event(event(store,assessment,'REPLACED',replacement_id='missing'))
        corrected=replace(assessment,reason_codes=(*assessment.reason_codes,'RECHECKED'))
        store.put_assessment(corrected)
        replacement=event(store,assessment,'REPLACED',replacement_id=corrected.assessment_id)
        assert store.append_event(replacement)
        assert store.last_event(assessment.subject.workspace_id,assessment.assessment_id).replacement_id==corrected.assessment_id


def test_stale_cannot_reactivate_and_event_clock_cannot_move_back(tmp_path):
    assessment=identity()
    with TaskRelationStore(tmp_path/'stale.sqlite') as store:
        store.put_assessment(assessment)
        stale=event(store,assessment,'STALE')
        with pytest.raises(ValueError):
            store.append_event(replace(stale,recorded_at=stale.recorded_at-timedelta(days=1)))
        store.append_event(stale)
        with pytest.raises(ValueError):
            store.append_event(replace(stale,event_type='ACTIVE',previous_event_id=stale.event_id))
        assert store.append_event(event(store,assessment,'INVALIDATED'))
        with pytest.raises(ValueError):
            store.append_event(event(store,assessment,'RETRACTED'))


def test_writer_refuses_to_initialize_another_database(tmp_path):
    path=tmp_path/'unrelated.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE existing_business_record(value TEXT)')
        conn.execute('INSERT INTO existing_business_record VALUES ("preserve")')
    before=path.read_bytes()
    with pytest.raises(ValueError):
        TaskRelationStore(path)
    assert path.read_bytes()==before


def test_unrelated_same_predicate_cannot_be_claimed_as_assessment_correction(tmp_path):
    original=identity()
    # Independent source identifiers in the same workspace: same predicate
    # alone must not grant an unrelated record a correction lineage.
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.document_text import parse_source_text
    from linkloom.semantic_ingestion.task_observation import TaskProposal,build_task_observation
    from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
    from linkloom.decision_memory.sources import SourceReferenceRegistry
    artifacts=[]
    observations=[]
    registry=SourceReferenceRegistry()
    for index,text in enumerate(('我负责青柳项目任务 SEND-26 的发送校验。','我负责青柳项目任务 SEND-26 的发送校验。')):
        artifact=RawArtifact(workspace_id=original.subject.workspace_id,artifact_id=f'unrelated-{index}.md',
                             source_type='text',content=text)
        segment=parse_source_text(artifact).segments[0]
        observation=build_task_observation(TaskProposal(text=text,description_parts=(text,),project='青柳项目'),segment,artifact)
        span=observation.provenance
        registry.register_evidence(span.workspace_id,span.evidence_ref,content_hash=span.content_hash,source_version=span.artifact_version_id)
        artifacts.append(artifact)
        observations.append(observation)
    unrelated=assess_same_task(*observations,artifacts=artifacts,source_registry=registry)
    assert unrelated.verdict=='SUPPORTED'
    with TaskRelationStore(tmp_path/'unrelated-correction.sqlite') as store:
        store.put_assessment(original)
        store.put_assessment(unrelated)
        with pytest.raises(ValueError,match='endpoint'):
            store.append_event(event(store,original,'REPLACED',replacement_id=unrelated.assessment_id))
