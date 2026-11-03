"""DEV-only simulated approval through the existing formal materializer, without Gold."""
from copy import deepcopy
from datetime import UTC,date,datetime
import hashlib,json
from pathlib import Path
from linkloom.agents.model_adapter import ModelResponse,ModelAction,ModelUsage
from linkloom.tools.contracts import ToolCall
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.ui.product import ProductRepository
from linkloom.semantic_ingestion.artifacts import RawArtifact,source_episode_id_for
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor,ProviderRequestAuthorization,SemanticExtractionContext
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer,CandidateReviewResolution,MaterializationBlockedError


class _GivenCandidates:
    """Format bridge only: return the provided candidates; no extraction/model/network."""
    def __init__(self,claims):self.claims=claims
    def complete(self,request):
        return ModelResponse(action=ModelAction.tool(ToolCall('given-candidates',request.available_tools[0].tool_id,
            dict(claims=deepcopy(self.claims)))),usage=ModelUsage(input_tokens=0,output_tokens=0,total_tokens=0))


def _scope(original,all_originals):
    predicate=original['relation'].removeprefix('not_');scope=original.get('project_scope') or ''
    types={}
    # Existing legacy relation vocabulary has declared person/alias roles. Plan names
    # are declared by the explicit plan-replacement relation, never a question/Gold.
    for fact in all_originals:
        if fact.get('project_scope')!=original.get('project_scope'):continue
        p=fact['relation'].removeprefix('not_')
        if p in {'supersedes','superseded_by'}:
            for label in (fact['subject'],fact['value']):types.setdefault(label,set()).add('plan')
    result=dict(business_scope=scope)
    if predicate in {'owner_of','owner_of_project'}:
        choices=types.get(original['subject'],{'project'})
        if len(choices)==1:result['subject_kind']=next(iter(choices))
    elif predicate in {'supersedes','superseded_by'}:result.update(subject_kind='plan',object_kind='plan')
    elif predicate in {'alias_of','alias_of_project'}:
        choices=types.get(original['value'],{'project'})
        if len(choices)==1:result['object_kind']=next(iter(choices))
    return result



def _given_agent_candidate(original,artifact,segment,registry,run_id):
    # The existing Agent approval path accepts explicit human time resolution.
    # Preserve snapshot kind in the approval audit; never change source-candidate
    # effective-time assertions or relabel the snapshot as an open-ended interval.
    from linkloom.agents.memory_candidate import (AgentDecisionClaimProposal,AgentMemoryResolutionContext,
        AgentRunEvidence,AgentRunEvidenceCatalog,MemoryCandidateBuilder,WorkspaceSubject,WorkspaceSubjectRegistry)
    from linkloom.agents.team_decision import TeamDecisionResult
    from linkloom.semantic_ingestion.evidence import VerifiedEvidenceBinding
    span=segment.evidence_span
    binding=VerifiedEvidenceBinding(workspace_id=artifact.workspace_id,evidence_ref=span.evidence_ref,
        source_episode_id=source_episode_id_for(artifact),source_identity=artifact.artifact_id,source_version=artifact.artifact_version_id,
        content_hash=artifact.content_hash,source_type='document_text',source_locator=artifact.source_ref,quote=span.quote,
        char_start=span.char_start,char_end=span.char_end,line_start=span.line_start,line_end=span.line_end,
        semantic_span=span,verification_kind='SEMANTIC_EVIDENCE_SPAN_V1',quote_sha256=span.quote_sha256)
    evidence=AgentRunEvidence(binding,observed_content=artifact.content)
    predicate=original['relation'];body=span.quote.casefold()
    choices={'owner_of':('owner','负责人','所有者'),'direct_leader_of':('reports directly to','直属领导','直接领导','上级'),
             'reports_to':('reports directly to','直属领导'),'superseded_by':('replaced','superseded','替代','取代','接替'),
             'alias_of':('alias','aliases','别名')}
    phrase=next((x for x in choices.get(predicate,()) if x.casefold() in body),predicate)
    resolver=FrozenRelationResolver((predicate,),aliases={phrase:predicate})
    subjects=WorkspaceSubjectRegistry(artifact.workspace_id,(WorkspaceSubject.create(artifact.workspace_id,original['subject']),))
    proposal=AgentDecisionClaimProposal(claim_type='FACT',subject_phrase=original['subject'],relation_phrase=phrase,evidence_refs=(span.evidence_ref,))
    result=TeamDecisionResult(decision=dict(value=original['value'],status='approved',evidence_refs=[span.evidence_ref]),
        rationale=[],rejected_alternatives=[],actions=[],unresolved_items=[],
        uncertainty=dict(status='none',statement=None,unknown_fields=[],evidence_refs=[]),evidence_refs=[span.evidence_ref])
    built=MemoryCandidateBuilder().build(result,workspace_id=artifact.workspace_id,run_id=run_id,
        evidence_catalog=AgentRunEvidenceCatalog(run_id,[evidence]),source_registry=registry,relation_resolver=resolver,
        resolution_context=AgentMemoryResolutionContext(artifact.workspace_id,subjects,proposal))
    if built.candidate is None:raise ValueError('GIVEN_CANDIDATE_NOT_REPRESENTABLE')
    return built.candidate


def confirm_candidates(public,database_path):
    """Preserve all original candidates and use the production capture/approval/receipt path.

    Missing time/identity is never filled. Such a candidate is retained with a
    simulated human confirmation event, but cannot be materialized into a trusted edge.
    """
    def check(value):
        if isinstance(value,dict):
            if set(value)&{'expected','gold','expected_legacy','required_sources','expected_edges'}:raise ValueError('GOLD_INPUT_FORBIDDEN')
            for v in value.values():check(v)
        elif isinstance(value,list):
            for v in value:check(v)
    check(public)
    if public.get('split')!='dev':raise ValueError('DEV_ONLY')
    now=datetime(2026,11,2,tzinfo=UTC);mapping={};confirmed=[]
    repository=ProductRepository(database_path);registry=SourceReferenceRegistry()
    documents={};groups={}
    for item in public['candidates']:
        groups.setdefault((item['case_id'],item['workspace_id'],item['source_path']),[]).append(item)
    try:
        for case in public['cases']:
            for doc in case['documents']:
                key=(case['case_id'],doc['workspace_id']);doc_key=(*key,doc['path'])
                if key not in mapping:mapping[key]=repository.create_workspace('模拟人工确认 '+case['case_id']+' '+doc['workspace_id'])['workspace_id']
                workspace=mapping[key]
                source='src_'+hashlib.sha256(json.dumps([case['case_id'],doc['path']],ensure_ascii=False).encode()).hexdigest()[:32]
                artifact=RawArtifact(workspace,source,'document',doc['content'],source_uri='fixture://'+doc['path'],ingestion_time=now)
                segments=parse_source_text(artifact).segments
                repository.create_source(workspace,source,doc['path'],artifact,tuple(s.evidence_span for s in segments))
                registry.register_episode(workspace,source_episode_id_for(artifact))
                for segment in segments:registry.register_evidence(workspace,segment.evidence_span.evidence_ref,
                    content_hash=artifact.content_hash,source_version=artifact.artifact_version_id)
                documents[doc_key]=(artifact,segments)
        with TemporalDecisionStore(database_path,source_registry=registry) as store:
            materializer=SemanticDecisionMaterializer(store)
            for key,items in groups.items():
                artifact,segments=documents[key];workspace=artifact.workspace_id
                claims=[]
                for item in items:
                    original=item['original_candidate']
                    claims.append(dict(claim_type='FACT',subject=original['subject'],relation=original['relation'],value=original['value'],
                        temporal_status='EXPLICIT' if original.get('valid_on') else 'NOT_STATED',valid_from=original.get('valid_on'),
                        valid_to=original.get('valid_to'),confidence=dict.fromkeys(('claim_type','entity','relation','temporal','overall'),.95)))
                guard_id='given-public-candidates'
                extractor=ProviderNeutralSemanticExtractor(_GivenCandidates(claims),provider_id='simulated-human-no-model',model_id='given-candidates',
                    request_guard=lambda r:ProviderRequestAuthorization.approve(r,guard_id=guard_id,reservation_id='no-model',estimated_cost_upper_bound_usd=0),
                    request_guard_id=guard_id,max_attempts=1)
                resolver=FrozenRelationResolver(tuple(sorted({c['relation'] for c in claims})))
                results=SemanticIngestionPipeline(extractor,CandidateValidator()).process_segment(segments[0],artifact,SemanticExtractionContext(workspace,resolver))
                if len(results)!=len(items):raise ValueError('CANDIDATE_COUNT_CHANGED')
                all_originals=[i['original_candidate'] for i in public['candidates'] if i['case_id']==key[0] and i['workspace_id']==key[1]]
                for item,result in zip(items,results):
                    if result.candidate is None:raise ValueError('ORIGINAL_CANDIDATE_NOT_REPRESENTABLE')
                    original=deepcopy(item['original_candidate']);candidate=result.candidate
                    if (candidate.subject,candidate.relation,candidate.value)!=(original['subject'],original['relation'],original['value']):
                        raise ValueError('CANDIDATE_MEANING_CHANGED')
                    capture_input=result
                    if original.get('valid_on') and candidate.valid_from is None:
                        candidate=_given_agent_candidate(original,artifact,segments[0],registry,'given-'+item['candidate_id'])
                        capture_input=candidate
                    materializer.capture(capture_input,expected_workspace_id=workspace,now=now)
                    fingerprint=materializer.candidate_fingerprint(workspace,candidate.candidate_id)
                    reason=json.dumps(dict(confirmation_label='模拟人工确认',original_candidate_id=item['candidate_id'],
                        original_candidate=original,graph_scope=_scope(original,all_originals)),ensure_ascii=False,sort_keys=True)
                    materialized=False;blocked=None
                    resolution=CandidateReviewResolution(valid_from=date.fromisoformat(original['valid_on'])) if original.get('valid_on') else None
                    try:
                        materializer.approve(workspace,candidate.candidate_id,reviewer_id='graph-eval-simulated-human',
                            expected_candidate_fingerprint=fingerprint,reason=reason,resolution=resolution if getattr(candidate,'valid_from',getattr(candidate,'effective_time',None)) is None else None,now=now)
                        materializer.materialize(workspace,candidate.candidate_id,now=now)
                        materialized=True
                    except MaterializationBlockedError as error:
                        blocked=error.reason_code
                        # Keep EVERY unresolved original candidate, without manufacturing a receipt/date.
                        row=store.get_semantic_candidate_row(workspace,candidate.candidate_id)
                        if row['workflow_state']!='APPROVED':
                            store.transition_semantic_candidate(workspace_id=workspace,candidate_id=candidate.candidate_id,
                                candidate_fingerprint=fingerprint,expected_states=(row['workflow_state'],),new_state='APPROVED',
                                event_type='HUMAN_APPROVAL',actor_type='HUMAN',actor_id='graph-eval-simulated-human',event_at=now,
                                reason_code=blocked,reason=reason,metadata=dict(confirmation_label='模拟人工确认',not_materializable=blocked))
                    assert json.loads(reason)['original_candidate']==item['original_candidate']
                    confirmed.append(dict(candidate_id=item['candidate_id'],formal_candidate_id=candidate.candidate_id,
                        workspace_id=workspace,fingerprint=fingerprint,materialized=materialized,blocked_reason=blocked))
        return dict(workspaces=mapping,confirmed=confirmed,confirmation_label='模拟人工确认')
    finally:repository.close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public',type=Path,required=True);parser.add_argument('--database',type=Path,required=True)
    args=parser.parse_args();result=confirm_candidates(json.loads(args.public.read_text(encoding='utf8')),args.database)
    print(json.dumps(dict(confirmation_label=result['confirmation_label'],workspaces=len(result['workspaces']),confirmed=len(result['confirmed']),
                         materialized=sum(r['materialized'] for r in result['confirmed'])),ensure_ascii=True))
