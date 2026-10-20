"""P4 sidecar publication must re-read P2's real temporary approval path."""
from dataclasses import replace
from datetime import timedelta

from linkloom.semantic_ingestion.task_relation_context import assess_relation
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote
from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from tests.task_change_support import create_reviewed_change
from tests.task_relations_support import public_cases
from tests.task_revision_support import build_case


def test_reviewed_change_receipt_readback_and_source_claim_remain_separate(tmp_path):
    case=next(c for c in public_cases() if c['case_id']=='D06')
    observations,artifacts,parsed,registry=build_case(case)
    question=next(q for q in case['questions'] if q.get('authority')=='REVIEWED_DECISION')
    change_input=question['object']
    evidence=[]
    for item in question['evidence']:
        span=parsed[item['source_id']][item['segment_index']].evidence_span
        start=span.char_start+span.quote.index(item['quote'])
        evidence.append(EvidenceQuote(span,start,start+len(item['quote']),item['quote'],'RELATION_PROPOSAL'))
    path=tmp_path/'relations.sqlite'
    with create_reviewed_change(tmp_path,artifacts[change_input['source_id']],registry,
            change_input['project'],change_input['before'],change_input['after']) as (decision_store,reviewed,audit):
        subject=observations[question['subject']['label']]
        args=dict(artifacts=tuple(artifacts.values()),source_registry=registry,
                  context_observations=tuple(observations.values()),evidence=evidence)
        approved=assess_relation('AFFECTED_BY',subject,reviewed,decision_store=decision_store,**args)
        claim=ChangeRef(reviewed.workspace_id,reviewed.project,reviewed.before_vendor,reviewed.after_vendor,reviewed.evidence)
        unreviewed=assess_relation('AFFECTED_BY',subject,claim,**args)
        assert approved.verdict==unreviewed.verdict=='SUPPORTED'
        with TaskRelationStore(path) as writer:
            writer.put_assessment(approved,evidence=evidence)
            writer.put_assessment(unreviewed,evidence=evidence)
        changes=decision_store._connection.total_changes
        decision_store._connection.execute('PRAGMA query_only=ON')
        query_args=dict(artifacts=args['artifacts'],source_registry=registry,observations=tuple(observations.values()))
        with RelationQuery(path,decision_store=decision_store,**query_args) as query:
            assert query.get(case['workspace_id'],approved.assessment_id).current_supported
            result=query.get(case['workspace_id'],unreviewed.assessment_id)
            assert result.current_supported
            assert result.assessment.object.kind=='SOURCE_CLAIM'
            assert 'FORMAL_AUTHORITY_NOT_ESTABLISHED' in result.assessment.authority_checks
        with RelationQuery(path,**query_args) as no_receipt_context:
            assert not no_receipt_context.get(case['workspace_id'],approved.assessment_id).current_supported
            assert no_receipt_context.get(case['workspace_id'],unreviewed.assessment_id).current_supported
        with TaskRelationStore(path) as writer:
            previous=writer.last_event(case['workspace_id'],approved.assessment_id)
            writer.append_event(RelationEvent.create(workspace_id=case['workspace_id'],assessment_id=approved.assessment_id,
                event_type='RETRACTED',reason='relation-only human correction',actor='human:synthetic',
                previous_event_id=previous.event_id,recorded_at=previous.recorded_at+timedelta(seconds=1)))
        with RelationQuery(path,decision_store=decision_store,**query_args) as query:
            assert not query.get(case['workspace_id'],approved.assessment_id).current_supported
            assert len(decision_store.list_decisions(case['workspace_id']))==2
        assert decision_store._connection.total_changes==changes
        assert audit['real_provider_calls']==0
