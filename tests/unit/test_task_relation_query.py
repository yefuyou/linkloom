from dataclasses import replace
from datetime import date, timedelta
import json
import sqlite3

import pytest

from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent
from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, RelationAssessment
from linkloom.semantic_ingestion.task_revision_comparison import RevisionComparison
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from tests.task_revision_support import build_case, public_cases, run_case


def seed(path,case_id='D04'):
    case=next(c for c in public_cases() if c['case_id']==case_id)
    observations,artifacts,parsed,registry=build_case(case)
    predictions=run_case(case)['predictions']
    with TaskRelationStore(path) as store:
        for question,row in zip(case['questions'],predictions):
            identity=RelationAssessment.from_dict(row['same_task'])
            revision=RelationAssessment.from_dict(row['revision'])
            comparison=RevisionComparison.from_dict(row['comparison'])
            proposals=[]
            for item in question['evidence']:
                span=parsed[item['source_id']][item['segment_index']].evidence_span
                start=span.char_start+span.quote.index(item['quote'])
                proposals.append(EvidenceQuote(span,start,start+len(item['quote']),item['quote'],'RELATION_PROPOSAL'))
            store.put_assessment(identity)
            store.put_assessment(revision,evidence=proposals)
            store.put_comparison(comparison)
    args=dict(artifacts=tuple(artifacts.values()),source_registry=registry,observations=tuple(observations.values()))
    return case,observations,artifacts,registry,predictions,args


def retract(path,workspace,assessment_id):
    with TaskRelationStore(path) as store:
        previous=store.last_event(workspace,assessment_id)
        event=RelationEvent.create(workspace_id=workspace,assessment_id=assessment_id,event_type='RETRACTED',
            previous_event_id=previous.event_id,recorded_at=previous.recorded_at+timedelta(seconds=1),
            actor='human:fixture-reviewer',reason='identity withdrawn')
        store.append_event(event)


def test_dependency_retraction_immediately_blocks_revision_and_keeps_historical_delta(tmp_path):
    path=tmp_path/'p4.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    row=rows[0]
    with RelationQuery(path,**args) as query:
        assert query.get(case['workspace_id'],row['revision']['assessment_id']).current_supported
        assert query.comparison(case['workspace_id'],row['comparison']['comparison_id']).to_dict()['current_comparison']['delta_calendar_days']==4
        retract(path,case['workspace_id'],row['same_task']['assessment_id'])
        relation=query.get(case['workspace_id'],row['revision']['assessment_id'],include_history=True)
        assert not relation.current_supported
        comparison=query.comparison(case['workspace_id'],row['comparison']['comparison_id']).to_dict()
        assert comparison['current_comparison']['status']=='BLOCKED'
        assert comparison['current_comparison']['delta_calendar_days'] is None
        assert comparison['historical_comparison']['delta_calendar_days']==4
        assert comparison['blocking_reasons']


def test_source_version_change_and_mutated_bytes_fail_closed(tmp_path):
    path=tmp_path/'source.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    target=obs['A1'] if 'A1' in obs else next(iter(obs.values()))
    span=target.provenance
    registry.register_evidence(span.workspace_id,span.evidence_ref,content_hash=span.content_hash,source_version='replacement-version')
    with RelationQuery(path,**args) as query:
        read=query.get(case['workspace_id'],rows[0]['same_task']['assessment_id'],include_history=True)
        assert not read.current_supported
        assert read.evidence_state=='STALE'
    changed=tuple(replace(a,content=a.content.replace('14','13',1)) if a.artifact_id==span.artifact_id else a for a in args['artifacts'])
    with RelationQuery(path,**{**args,'artifacts':changed}) as query:
        assert not query.get(case['workspace_id'],rows[0]['same_task']['assessment_id'],include_history=True).current_supported


def test_read_only_no_context_unknown_and_no_autocreate(tmp_path):
    path=tmp_path/'unknown.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path,'D07')
    before=path.read_bytes()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as readonly:
        with RelationQuery(readonly,**args) as query:
            results=query.list(case['workspace_id'])
            assert results and all(not r.current_supported for r in results)
            assert any(r.to_dict()['assessment']['verdict']=='UNKNOWN' for r in results)
            assert query.comparison(case['workspace_id'],rows[0]['comparison']['comparison_id']).to_dict()['current_comparison']['delta_calendar_days'] is None
            assert readonly.total_changes==0
    assert path.read_bytes()==before
    with RelationQuery(path) as query:
        row=query.get(case['workspace_id'],rows[0]['same_task']['assessment_id'],include_history=True)
        assert not row.current_supported and row.blocking_reasons
        assert query.list(case['workspace_id'])==()
    missing=tmp_path/'missing.sqlite'
    with pytest.raises((ValueError,sqlite3.OperationalError,FileNotFoundError)):
        RelationQuery(missing)
    assert not missing.exists()


def test_forged_canonical_assessment_cannot_be_published(tmp_path):
    path=tmp_path/'forge.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    real=RelationAssessment.from_dict(rows[0]['same_task'])
    forged=replace(real,reason_codes=('UNTRUSTED_REWRITTEN_PROOF',))
    with TaskRelationStore(path) as store:
        store.put_assessment(forged)
    with RelationQuery(path,**args) as query:
        result=query.get(case['workspace_id'],forged.assessment_id,include_history=True)
        assert not result.current_supported
        assert 'ASSESSMENT_REPLAY_MISMATCH' in result.blocking_reasons


def test_future_effective_and_unknown_effective_are_distinct(tmp_path):
    path=tmp_path/'future.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path,'V05')
    revision=rows[0]['revision']
    with RelationQuery(path,**args) as query:
        before=query.plan(case['workspace_id'],revision['subject']['observation_id'],as_of=date(2026,12,15))
        after=query.plan(case['workspace_id'],revision['subject']['observation_id'],as_of=date(2026,12,21))
    assert before['status']=='FUTURE_EFFECTIVE'
    assert before['effective_plan'] is None
    assert before['future_plans']
    assert after['status']=='EFFECTIVE'
    assert after['effective_plan']['observation_id']==revision['subject']['observation_id']
    unknown=tmp_path/'unknown-plan.sqlite'
    case,obs,artifacts,registry,rows,args=seed(unknown)
    with RelationQuery(unknown,**args) as query:
        plan=query.plan(case['workspace_id'],rows[0]['revision']['subject']['observation_id'],as_of=date(2027,1,1))
        assert plan['status']=='EFFECTIVE_TIME_UNKNOWN'
        assert plan['effective_plan'] is None


def test_replaced_assessment_does_not_poison_valid_corrected_plan(tmp_path):
    path=tmp_path/'correction.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    original=RelationAssessment.from_dict(rows[0]['revision'])
    new,old=original.observations
    context=(new,old)
    evidence=[EvidenceQuote(new.provenance,new.provenance.char_start,new.provenance.char_end,
                            new.provenance.quote,'RELATION_PROPOSAL')]
    identity=assess_same_task(new,old,artifacts=args['artifacts'],source_registry=registry,context_observations=context)
    corrected=assess_plan_revision(new,old,same_task=identity,evidence=evidence,artifacts=args['artifacts'],
                                  source_registry=registry,context_observations=context)
    assert corrected.verdict=='SUPPORTED' and corrected.assessment_id!=original.assessment_id
    with TaskRelationStore(path) as store:
        store.put_assessment(identity)
        store.put_assessment(corrected,evidence=evidence)
        previous=store.last_event(case['workspace_id'],original.assessment_id)
        store.append_event(RelationEvent.create(workspace_id=case['workspace_id'],assessment_id=original.assessment_id,
            event_type='REPLACED',reason='explicit new proof assessment',actor='human:reviewer',previous_event_id=previous.event_id,
            recorded_at=previous.recorded_at+timedelta(seconds=1),replacement_id=corrected.assessment_id))
    with RelationQuery(path,**args) as query:
        assert query.get(case['workspace_id'],corrected.assessment_id).current_supported
        plan=query.plan(case['workspace_id'],new.observation_id,as_of=date(2027,1,1))
        assert plan['status']=='EFFECTIVE_TIME_UNKNOWN'
        assert plan['declared_terminal']


def test_ambiguous_current_versions_and_missing_m1_payload_never_publish(tmp_path):
    from linkloom.semantic_ingestion.document_text import parse_source_text
    path=tmp_path/'versions.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    old=next(iter(artifacts.values()))
    new=replace(old,content=old.content+'\n资料补充。')
    for segment in parse_source_text(new).segments:
        registry.register_evidence(case['workspace_id'],segment.evidence_span.evidence_ref,
                                  content_hash=new.content_hash,source_version=new.artifact_version_id)
    with RelationQuery(path,**{**args,'artifacts':(*args['artifacts'],new)}) as query:
        read=query.get(case['workspace_id'],rows[0]['same_task']['assessment_id'])
        assert not read.current_supported
        assert 'AMBIGUOUS_CURRENT_SOURCE_VERSION' in read.blocking_reasons
    with RelationQuery(path,artifacts=args['artifacts'],source_registry=registry) as query:
        assert not query.get(case['workspace_id'],rows[0]['same_task']['assessment_id']).current_supported


def test_comparison_forgery_cannot_hide_behind_explicit_as_of(tmp_path):
    from linkloom.semantic_ingestion.task_revision_comparison import DeadlineProjection
    path=tmp_path/'comparison-forge.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    original=RevisionComparison.from_dict(rows[0]['comparison'])
    wrong=replace(original,new_deadline=replace(original.new_deadline,value='2026-10-19'),delta_calendar_days=5)
    with TaskRelationStore(path) as store:
        store.put_comparison(wrong)
    with RelationQuery(path,**args) as query:
        read=query.comparison(case['workspace_id'],wrong.comparison_id,as_of=date(2027,1,1)).to_dict()
        assert not read['current_valid']
        assert read['current_comparison']['delta_calendar_days'] is None
        assert 'COMPARISON_REPLAY_MISMATCH' in read['blocking_reasons']


@pytest.mark.parametrize('case_id,status',[('D01','NOT_APPLICABLE'),('V01','NOT_COMPARABLE'),('V04','BLOCKED')])
def test_nonpositive_comparison_semantics_survive_readback(tmp_path,case_id,status):
    path=tmp_path/'states.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path,case_id)
    with RelationQuery(path,**args) as query:
        result=query.comparison(case['workspace_id'],rows[0]['comparison']['comparison_id']).to_dict()
        assert result['current_comparison']['status']==status
        assert result['current_comparison']['delta_calendar_days'] is None
        assert not result['current_valid']


def test_sql_inserted_corrupt_record_is_diagnostic_only(tmp_path):
    path=tmp_path/'corrupt.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    with sqlite3.connect(path) as conn:
        conn.execute('''INSERT INTO task_relation_assessment SELECT workspace_id,?,predicate,subject_json,
            object_json,payload_json,payload_hash,recipe_json,recipe_hash,record_hash FROM task_relation_assessment
            WHERE assessment_id=?''',('forged-row-id',rows[0]['same_task']['assessment_id']))
    with RelationQuery(path,**args) as query:
        result=query.get(case['workspace_id'],'forged-row-id')
        assert not result.current_supported and result.assessment is None
        assert 'STORED_RECORD_INTEGRITY_FAILED' in result.blocking_reasons


def test_same_endpoints_with_competing_effective_claims_cannot_select_by_id(tmp_path):
    from copy import deepcopy
    case=deepcopy(next(c for c in public_cases() if c['case_id']=='D04'))
    question=case['questions'][0]
    item=next(o for o in case['observations'] if o['label']==question['subject'])
    text=item['task']['text']
    effective_clauses=('本计划自2027年2月1日起生效。','本计划自2027年3月1日起生效。')
    expanded=text+''.join(effective_clauses)
    item['task']['text']=expanded
    item['task']['description_parts']=[expanded]
    source=next(s for s in case['sources'] if s['artifact_id']==item['source_id'])
    source['content']=source['content'].replace(text,expanded)
    observations,artifacts,parsed,registry=build_case(case)
    new,old=(observations[question[k]] for k in ('subject','object'))
    args=dict(artifacts=tuple(artifacts.values()),source_registry=registry,context_observations=tuple(observations.values()))
    identity=assess_same_task(new,old,**args)
    span=new.provenance
    def quote(value):
        start=span.char_start+span.quote.index(value)
        return EvidenceQuote(span,start,start+len(value),value,'RELATION_PROPOSAL')
    path=tmp_path/'competing-times.sqlite'
    with TaskRelationStore(path) as store:
        store.put_assessment(identity)
        for clause in effective_clauses:
            proposals=[quote(text),quote(clause)]
            relation=assess_plan_revision(new,old,same_task=identity,evidence=proposals,**args)
            assert relation.verdict=='SUPPORTED'
            store.put_assessment(relation,evidence=proposals)
    with RelationQuery(path,artifacts=args['artifacts'],source_registry=registry,observations=tuple(observations.values())) as query:
        plan=query.plan(case['workspace_id'],new.observation_id,as_of=date(2027,2,15))
        assert plan['status']=='CONFLICT'
        assert plan['effective_plan'] is None


@pytest.mark.parametrize('reverse_import',[False,True])
def test_explicit_future_revision_keeps_known_effective_predecessor(tmp_path,reverse_import):
    project,owner,key='白榆项目','柳青','QUEUE-32'
    first=f'我负责{project}任务 {key} 的队列接入，计划在2027年3月10日前完成。'
    second=f'我负责{project}任务 {key}；本条就是base.md的同一任务，现修订其计划，将原定2027年3月10日前完成改为2027年3月12日前完成，本计划替代原计划。本计划自2027年3月1日起生效。'
    third=f'我负责{project}任务 {key}；本条就是one.md的同一任务，现修订其计划，将原定2027年3月12日前完成改为2027年3月15日前完成，本计划替代原计划。本计划自2027年3月5日起生效。'
    data=[('A','base.md',first,'2027年3月10日'),('B','one.md',second,'2027年3月12日'),('C','next.md',third,'2027年3月15日')]
    case=dict(workspace_id='p4-explicit-effective-chain',sources=[dict(artifact_id=name,source_type='timestamped_text',
        content=f'[2027-02-28T09:00:00+08:00] {owner}: {text}\n') for _,name,text,_ in data],
        observations=[dict(label=label,source_id=name,segment_index=0,task=dict(text=text,description_parts=[text],
            owner=owner,deadline_text=deadline,project=project,vendor=None)) for label,name,text,deadline in data])
    observations,artifacts,parsed,registry=build_case(case)
    args=dict(artifacts=tuple(artifacts.values()),source_registry=registry,context_observations=tuple(observations.values()))
    path=tmp_path/'known-future.sqlite'
    with TaskRelationStore(path) as writer:
        for new_label,old_label in ([('C','B'),('B','A')] if reverse_import else [('B','A'),('C','B')]):
            new,old=observations[new_label],observations[old_label]
            identity=assess_same_task(new,old,**args)
            proposals=[EvidenceQuote(o.provenance,o.provenance.char_start,o.provenance.char_end,o.provenance.quote,'RELATION_PROPOSAL')
                       for o in (new,old)]
            revision=assess_plan_revision(new,old,same_task=identity,evidence=proposals,**args)
            assert revision.verdict=='SUPPORTED'
            writer.put_assessment(identity)
            writer.put_assessment(revision,evidence=proposals)
    with RelationQuery(path,artifacts=args['artifacts'],source_registry=registry,observations=tuple(observations.values())) as query:
        before=query.plan(case['workspace_id'],observations['C'].observation_id,as_of=date(2027,3,3))
        after=query.plan(case['workspace_id'],observations['C'].observation_id,as_of=date(2027,3,6))
    assert before['status']=='FUTURE_EFFECTIVE'
    assert before['effective_plan']['observation_id']==observations['B'].observation_id
    assert before['future_plans'][0]['observation_id']==observations['C'].observation_id
    assert after['effective_plan']['observation_id']==observations['C'].observation_id


@pytest.mark.parametrize('reuse',[True,False])
def test_current_known_competitor_is_not_hidden_by_stored_context(tmp_path,reuse):
    from copy import deepcopy
    from linkloom.semantic_ingestion.task_revision_comparison import compare_revision
    case=deepcopy(next(c for c in public_cases() if c['case_id']=='D04'))
    question=case['questions'][0]
    item=next(o for o in case['observations'] if o['label'] not in (question['subject'],question['object']))
    text=item['task']['text']
    expanded=text+('任务编号已被复用，编号存在冲突。' if reuse else '交付说明已补充。')
    item['task']['text']=expanded
    item['task']['description_parts']=[expanded]
    source=next(s for s in case['sources'] if s['artifact_id']==item['source_id'])
    source['content']=source['content'].replace(text,expanded)
    observations,artifacts,parsed,registry=build_case(case)
    new,old=(observations[question[k]] for k in ('subject','object'))
    args=dict(artifacts=tuple(artifacts.values()),source_registry=registry,context_observations=(new,old))
    identity=assess_same_task(new,old,**args)
    evidence=[EvidenceQuote(new.provenance,new.provenance.char_start,new.provenance.char_end,new.provenance.quote,'RELATION_PROPOSAL')]
    revision=assess_plan_revision(new,old,same_task=identity,evidence=evidence,**args)
    comparison=compare_revision(new,old,same_task=identity,revision=revision,evidence=evidence,**args)
    assert comparison.delta_calendar_days==4
    path=tmp_path/'extra-context.sqlite'
    with TaskRelationStore(path) as writer:
        writer.put_assessment(identity)
        writer.put_assessment(revision,evidence=evidence)
        writer.put_comparison(comparison)
    with RelationQuery(path,artifacts=args['artifacts'],source_registry=registry,observations=tuple(observations.values())) as query:
        identity_read=query.get(case['workspace_id'],identity.assessment_id)
        revision_read=query.get(case['workspace_id'],revision.assessment_id)
        comparison_read=query.comparison(case['workspace_id'],comparison.comparison_id).to_dict()
        assert identity_read.current_supported is not reuse
        assert revision_read.current_supported is not reuse
        assert comparison_read['current_valid'] is not reuse
        assert comparison_read['current_comparison']['delta_calendar_days']==(None if reuse else 4)
        if reuse:
            assert 'CURRENT_CONTEXT_CHANGED_REASSESSMENT_REQUIRED' in identity_read.blocking_reasons


@pytest.mark.parametrize('source_status,conflict',[('NOT_CURRENT',False),('CURRENT',True),('CURRENT',False)])
def test_m1_query_adapter_preserves_source_and_extraction_status(tmp_path,source_status,conflict):
    from linkloom.semantic_ingestion.task_observation_query import TaskObservationRead
    path=tmp_path/'m1-adapter.sqlite'
    case,obs,artifacts,registry,rows,args=seed(path)
    class ExistingM1Reader:
        def list(self,workspace_id,*,include_history):
            return tuple(TaskObservationRead(o,('synthetic-candidate',),source_status,conflict) for o in obs.values())
    with RelationQuery(path,artifacts=args['artifacts'],source_registry=registry,observation_query=ExistingM1Reader()) as query:
        read=query.get(case['workspace_id'],rows[0]['same_task']['assessment_id'])
        assert read.current_supported is (source_status=='CURRENT' and not conflict)


def test_stale_old_source_does_not_poison_a_fresh_proven_revision_chain(tmp_path):
    from linkloom.semantic_ingestion.document_text import parse_source_text
    from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
    path=tmp_path/'fresh-version-chain.sqlite'
    case,observations,artifacts,registry,rows,args=seed(path)
    old_revision=RelationAssessment.from_dict(rows[0]['revision'])
    previous,base=old_revision.observations
    old_artifact=artifacts[previous.provenance.artifact_id]
    replacement_text=previous.deadline_text.replace('18日','19日')
    new_artifact=replace(old_artifact,content=old_artifact.content.replace(previous.deadline_text,replacement_text))
    parsed=parse_source_text(new_artifact)
    task=next(o['task'] for o in case['observations'] if o['label']==case['questions'][0]['subject'])
    updated={**task,'text':task['text'].replace(previous.deadline_text,replacement_text),
             'description_parts':[t.replace(previous.deadline_text,replacement_text) for t in task['description_parts']],
             'deadline_text':replacement_text}
    current=build_task_observation(TaskProposal.from_dict(updated),parsed.segments[0],new_artifact)
    for segment in parsed.segments:
        registry.register_evidence(case['workspace_id'],segment.evidence_span.evidence_ref,
                                  content_hash=new_artifact.content_hash,source_version=new_artifact.artifact_version_id)
    registry.register_evidence(case['workspace_id'],previous.provenance.evidence_ref,
                              content_hash=new_artifact.content_hash,source_version=new_artifact.artifact_version_id)
    sources=(*args['artifacts'],new_artifact)
    fresh_args=dict(artifacts=sources,source_registry=registry,context_observations=(current,base))
    identity=assess_same_task(current,base,**fresh_args)
    proposals=[EvidenceQuote(current.provenance,current.provenance.char_start,current.provenance.char_end,
                             current.provenance.quote,'RELATION_PROPOSAL')]
    revision=assess_plan_revision(current,base,same_task=identity,evidence=proposals,**fresh_args)
    assert revision.verdict=='SUPPORTED'
    with TaskRelationStore(path) as writer:
        writer.put_assessment(identity)
        writer.put_assessment(revision,evidence=proposals)
    with RelationQuery(path,artifacts=sources,source_registry=registry,observations=(*observations.values(),current)) as query:
        assert query.get(case['workspace_id'],revision.assessment_id).current_supported
        plan=query.plan(case['workspace_id'],current.observation_id,as_of=date(2027,1,1))
        assert plan['status']=='EFFECTIVE_TIME_UNKNOWN'
        assert plan['declared_terminal']['observation_id']==current.observation_id
        assert not query.get(case['workspace_id'],old_revision.assessment_id).current_supported
