"""Fixture-only simulated confirmations using existing formal Decision and Task APIs.

No question, expected answer, Gold, model output or network enters these functions.
The injected Task batch adapter implements the existing extractor-neutral Host seam;
all snapshots remain in the existing immutable generated-batch/review tables.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC,date,datetime,timedelta
from pathlib import Path
import hashlib,json
from linkloom.runtime.errors import ValidationError
from linkloom.agents.model_adapter import ModelAction,ModelResponse
from linkloom.agents.memory_candidate import (AgentDecisionClaimProposal,AgentMemoryResolutionContext,AgentRunEvidence,
    AgentRunEvidenceCatalog,MemoryCandidateBuilder,WorkspaceSubject,WorkspaceSubjectRegistry)
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.semantic_ingestion.artifacts import (RawArtifact,EvidenceSpan,source_episode_id_for,message_id_for,segment_id_for,evidence_ref_for)
from linkloom.semantic_ingestion.evidence import VerifiedEvidenceBinding
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer,CandidateReviewResolution
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.tasks.models import TaskCandidateInput,EvidenceReference,canonical_json
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import CandidateUnavailableError,digest
from linkloom.tasks.candidate_store import (GeneratedTaskBatchStore,StoredTaskCandidateSource,PreparedBatch,PreparedCandidate)
from linkloom.tasks.candidate_source import ProductTaskCandidateSource
from linkloom.tasks.store import TaskStore
from linkloom.ui.product import ProductRepository


def no_gold(value):
    if isinstance(value,dict):
        if set(value)&{'questions','question','expected','gold','expected_edges'}:raise ValueError('GOLD_OR_QUESTION_INPUT_FORBIDDEN')
        for item in value.values():no_gold(item)
    elif isinstance(value,list):
        for item in value:no_gold(item)


def spans(artifact):
    result=[];offset=0
    for i,line in enumerate(artifact.content.splitlines(keepends=True)):
        text=line.rstrip('\r\n')
        if text:
            sha=hashlib.sha256(text.encode()).hexdigest();message=message_id_for(artifact.artifact_version_id,i)
            segment=segment_id_for(artifact.artifact_version_id,message,offset,offset+len(text),sha)
            span=EvidenceSpan(artifact.workspace_id,artifact.artifact_id,artifact.artifact_version_id,artifact.source_ref,
                artifact.content_hash,i,message,segment,evidence_ref_for(segment),offset,offset+len(text),i+1,i+1,text,sha)
            if not span.verify(artifact):raise ValueError('EXACT_LINE_PROOF_REQUIRED')
            result.append(span)
        offset+=len(line)
    return tuple(result)


def _binding(artifact,span):
    return VerifiedEvidenceBinding(workspace_id=artifact.workspace_id,evidence_ref=span.evidence_ref,
        source_episode_id=source_episode_id_for(artifact),source_identity=artifact.artifact_id,source_version=artifact.artifact_version_id,
        content_hash=artifact.content_hash,source_type='document_text',source_locator=artifact.source_ref,quote=span.quote,
        char_start=span.char_start,char_end=span.char_end,line_start=span.line_start,line_end=span.line_end,
        semantic_span=span,verification_kind='SEMANTIC_EVIDENCE_SPAN_V1',quote_sha256=span.quote_sha256)


def _agent_candidate(artifact,span,registry,*,run,subject,relation,value,subject_phrase=None,relation_phrase=None,claim_type='DECISION'):
    canonical=WorkspaceSubject.create(artifact.workspace_id,subject)
    subjects=WorkspaceSubjectRegistry(artifact.workspace_id,(canonical,))
    phrase=relation_phrase or span.quote
    resolver=FrozenRelationResolver((relation,),aliases={phrase:relation})
    binding=_binding(artifact,span)
    result=TeamDecisionResult(decision=dict(value=value,status='approved',evidence_refs=[span.evidence_ref]),rationale=[],
        rejected_alternatives=[],actions=[],unresolved_items=[],uncertainty=dict(status='none',statement=None,unknown_fields=[],evidence_refs=[]),
        evidence_refs=[span.evidence_ref])
    built=MemoryCandidateBuilder().build(result,workspace_id=artifact.workspace_id,run_id=run,
        evidence_catalog=AgentRunEvidenceCatalog(run,[AgentRunEvidence(binding,observed_content=artifact.content)]),source_registry=registry,
        relation_resolver=resolver,resolution_context=AgentMemoryResolutionContext(artifact.workspace_id,subjects,
            AgentDecisionClaimProposal(claim_type=claim_type,subject_phrase=subject_phrase,relation_phrase=phrase,evidence_refs=(span.evidence_ref,))))
    if built.candidate is None:raise ValueError('GIVEN_CONFIRMATION_NOT_REPRESENTABLE')
    return built.candidate,canonical


class GivenTaskBatchAdapter:
    """An explicit Host adapter for already supplied structured DEV records."""
    format_version='graph-dev-given-task/v1'
    def encode(self,batch):return deepcopy(batch)
    def prepare(self,db,workspace,source,version,payload,confidence):
        no_gold(payload)
        if set(payload)!={'candidates','meeting','confirmation_label'} or payload['confirmation_label']!='模拟人工确认':raise ValueError('INVALID_GIVEN_BATCH')
        row=db.execute("""SELECT v.*,s.active,s.current_version_id FROM ll_source_version v JOIN ll_source s
            ON s.workspace_id=v.workspace_id AND s.source_id=v.source_id
            WHERE v.workspace_id=? AND v.source_id=? AND v.artifact_version_id=?""",(workspace,source,version)).fetchone()
        if row is None or not row['active'] or row['current_version_id']!=version:raise CandidateUnavailableError('source unavailable')
        if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha256']:raise CandidateUnavailableError('source hash changed')
        prepared=[];dependencies={}
        for item in payload['candidates']:
            candidate=TaskCandidateInput.from_dict(item)
            if candidate.workspace_id!=workspace:raise ValueError('FOREIGN_TASK')
            for ref in candidate.evidence:
                record=db.execute("""SELECT v.*,s.active,s.current_version_id FROM ll_source_version v JOIN ll_source s
                    ON s.workspace_id=v.workspace_id AND s.source_id=v.source_id
                    WHERE v.workspace_id=? AND v.source_id=? AND v.artifact_version_id=?""",
                    (workspace,ref.source_id,ref.source_version_id)).fetchone()
                if record is None or not record['active'] or record['current_version_id']!=ref.source_version_id:raise CandidateUnavailableError('task witness unavailable')
                if (ref.source_sha256!=record['content_sha256'] or hashlib.sha256(record['content'].encode()).hexdigest()!=record['content_sha256']
                    or record['content'][ref.char_start:ref.char_end]!=ref.quote):raise CandidateUnavailableError('task witness changed')
                saved=db.execute("""SELECT * FROM ll_source_evidence WHERE workspace_id=? AND source_id=? AND artifact_version_id=?
                    AND line_start=? AND line_end=? AND quote_sha256=?""",(workspace,ref.source_id,ref.source_version_id,ref.line_start,ref.line_end,ref.quote_sha256)).fetchall()
                if len(saved)!=1 or saved[0]['quote']!=ref.quote:raise CandidateUnavailableError('exact witness required')
                epoch=db.execute('SELECT COALESCE(MAX(sequence),0) FROM task_review_source_change WHERE workspace_id=? AND source_id=?',(workspace,ref.source_id)).fetchone()[0]
                dependencies[ref.source_id]=[ref.source_version_id,record['content_sha256'],epoch]
            prepared.append(PreparedCandidate(candidate,canonical_json(dict(given=item,meeting=payload['meeting'])),True))
        return PreparedBatch(digest(dependencies),tuple(prepared))


class GivenTaskCandidateSource(ProductTaskCandidateSource):
    def __init__(self):super().__init__(StoredTaskCandidateSource(adapter=GivenTaskBatchAdapter()))
    def version_proof(self,db,workspace,source,version,*,evidence=(),require_current=False):
        from linkloom.tasks.review_models import SourceVersionProof
        if not db.in_transaction:raise ValueError('ACTIVE_TRANSACTION_REQUIRED')
        row=db.execute("""SELECT v.*,v.rowid AS sequence,s.active,s.current_version_id FROM ll_source_version v JOIN ll_source s
            ON s.workspace_id=v.workspace_id AND s.source_id=v.source_id
            WHERE v.workspace_id=? AND v.source_id=? AND v.artifact_version_id=?""",(workspace,source,version)).fetchone()
        if row is None or not row['active'] or require_current and row['current_version_id']!=version:raise CandidateUnavailableError('source unavailable')
        artifact=RawArtifact(workspace,source,'document',row['content'],source_uri=row['source_uri'])
        if artifact.artifact_version_id!=version or artifact.content_hash!=row['content_sha256']:raise CandidateUnavailableError('complete source version failed verification')
        original=spans(artifact)
        for ref in evidence:
            matching=[span for span in original if span.quote==ref.quote and span.quote_sha256==ref.quote_sha256 and
                span.line_start==ref.line_start and span.line_end==ref.line_end and span.char_start==ref.char_start and span.char_end==ref.char_end]
            if (ref.workspace_id!=workspace or ref.source_id!=source or ref.source_version_id!=version or
                ref.source_sha256!=artifact.content_hash or len(matching)!=1):raise CandidateUnavailableError('exact source witness failed verification')
            saved=db.execute('SELECT * FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',(workspace,matching[0].evidence_ref)).fetchone()
            if saved is None or saved['quote']!=ref.quote or saved['quote_sha256']!=ref.quote_sha256:raise CandidateUnavailableError('source witness receipt changed')
        epoch=db.execute('SELECT COALESCE(MAX(sequence),0) FROM task_review_source_change WHERE workspace_id=? AND source_id=?',(workspace,source)).fetchone()[0]
        return SourceVersionProof(workspace,source,version,row['sequence'],artifact.content_hash,
            tuple(sorted({r.meeting_time for r in evidence if r.meeting_time})),digest([version,epoch]),
            digest([r.to_dict() for r in evidence]),row['current_version_id']==version)


def task_candidate_source():return GivenTaskCandidateSource()


def _approve(materializer,store,workspace,candidate,canonical,*,effective,reason,now):
    materializer.capture(candidate,expected_workspace_id=workspace,now=now)
    resolution=CandidateReviewResolution(subject=canonical.display_label if candidate.subject_id is None else None,
        subject_id=canonical.subject_id if candidate.subject_id is None else None,
        valid_from=effective if candidate.effective_time is None else None)
    materializer.approve(workspace,candidate.candidate_id,reviewer_id='graph-eval-formal-human',
        expected_candidate_fingerprint=materializer.candidate_fingerprint(workspace,candidate.candidate_id),
        reason=canonical_json(reason),resolution=resolution,now=now)
    receipt=materializer.materialize(workspace,candidate.candidate_id,now=now).receipt
    if receipt is None:raise ValueError('FORMAL_MATERIALIZATION_REQUIRED')
    return receipt


def build_meetings(fixture,database_path,*,recorded_at):
    no_gold(fixture)
    recorded_at=datetime.fromisoformat(recorded_at)
    if recorded_at.utcoffset() is None:raise ValueError('RECORDED_TIME_REQUIRES_TIMEZONE')
    repository=ProductRepository(database_path);registry=SourceReferenceRegistry();documents={};decisions={};tasks={};audit=[]
    workspace=repository.create_workspace(fixture['workspace'])['workspace_id']
    try:
        for meeting in fixture['meetings']:
            text='\n'.join(meeting['lines']);source='src_'+digest([fixture['workspace'],meeting['id']])[:32]
            artifact=RawArtifact(workspace,source,'document',text,source_uri='fixture://'+meeting['id'],ingestion_time=recorded_at)
            proof=spans(artifact)
            repository.create_source(workspace,source,meeting['name'],artifact,proof)
            registry.register_episode(workspace,source_episode_id_for(artifact))
            for span in proof:registry.register_evidence(workspace,span.evidence_ref,content_hash=artifact.content_hash,source_version=artifact.artifact_version_id)
            documents[meeting['id']]=(meeting,artifact,proof)
        originals={d['id']:d for d in fixture['confirmed_decisions']}
        def family(identifier):
            seen=set()
            while originals[identifier].get('supersedes'):
                if identifier in seen:raise ValueError('FIXTURE_REPLACEMENT_CYCLE')
                seen.add(identifier);identifier=originals[identifier]['supersedes']
            return identifier
        with TemporalDecisionStore(database_path,source_registry=registry) as store:
            materializer=SemanticDecisionMaterializer(store)
            for i,item in enumerate(sorted(fixture['confirmed_decisions'],key=lambda r:r['valid_from'])):
                meeting,artifact,proof=documents[item['meeting']];line=int(item['evidence'].split('#')[1]);span=proof[line-1]
                if item['valid_from']!=meeting['date']:raise ValueError('DECISION_MEETING_TIME_MISMATCH:'+item['id'])
                candidate,canonical=_agent_candidate(artifact,span,registry,run='given-'+item['id'],subject=fixture['topic'],
                    relation='meeting_decision_'+family(item['id']),value=span.quote)
                reason=dict(confirmation_label='模拟人工确认',original_record=deepcopy(item),external_record_id=item['id'],
                    graph_valid_time=dict(kind='date',value=meeting['date']),system_recorded_at=recorded_at.isoformat(),
                    meeting=dict(document_id=artifact.artifact_id,source_version_id=artifact.artifact_version_id,content_sha256=artifact.content_hash,date=meeting['date']))
                receipt=_approve(materializer,store,workspace,candidate,canonical,effective=date.fromisoformat(meeting['date']),reason=reason,
                    now=recorded_at+timedelta(seconds=i))
                if item.get('supersedes') and receipt.supersedes_id!=decisions[item['supersedes']]:raise ValueError('EXPLICIT_REPLACEMENT_NOT_PRESERVED')
                decisions[item['id']]=receipt.decision_id;audit.append(dict(kind='decision',external_id=item['id'],receipt_id=receipt.receipt_id))
            # A pending proposal is captured, never approved or materialized.
            for i,item in enumerate(fixture.get('unconfirmed',[])):
                meeting_id,number=item['evidence'].split('#');meeting,artifact,proof=documents[meeting_id];span=proof[int(number)-1]
                from tests.graph_eval_support import _GivenCandidates
                from linkloom.semantic_ingestion.document_text import DocumentTextSegment
                from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor,ProviderRequestAuthorization,SemanticExtractionContext
                from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
                from linkloom.semantic_ingestion.validation import CandidateValidator
                claim=dict(claim_type='PROPOSAL',subject=fixture['topic'],relation='meeting_proposal',value=span.quote,
                    temporal_status='NOT_STATED',valid_from=None,valid_to=None,
                    confidence=dict.fromkeys(('claim_type','entity','relation','temporal','overall'),.95))
                provider=ProviderNeutralSemanticExtractor(_GivenCandidates([claim]),provider_id='given-fixture-no-model',model_id='given',
                    request_guard=lambda r:ProviderRequestAuthorization.approve(r,guard_id='given',reservation_id='given',estimated_cost_upper_bound_usd=0),
                    request_guard_id='given',max_attempts=1)
                segment=DocumentTextSegment(span.ordinal,span.message_id,span.segment_id,span.quote,span)
                result=SemanticIngestionPipeline(provider,CandidateValidator()).process_segment(segment,artifact,
                    SemanticExtractionContext(workspace,FrozenRelationResolver(('meeting_proposal',))))
                if len(result)!=1 or result[0].candidate is None:raise ValueError('PENDING_PROPOSAL_NOT_REPRESENTABLE')
                materializer.capture(result[0],expected_workspace_id=workspace,now=recorded_at)
        task_store=TaskStore(database_path)
        try:
            adapter=GivenTaskBatchAdapter();batches=GeneratedTaskBatchStore(task_store,adapter=adapter)
            source=task_candidate_source();review=TaskReviewService(task_store,source)
            serial=0
            for task in fixture['confirmed_tasks']:
                previous_owner_ref=None;previous_owner=None
                for index,item in enumerate(task['history']):
                    meeting,artifact,proof=documents[item['meeting']];span=proof[int(item['evidence'].split('#')[1])-1]
                    when=datetime.combine(date.fromisoformat(meeting['date']),datetime.min.time(),tzinfo=UTC)
                    reference=EvidenceReference(workspace,artifact.artifact_id,artifact.artifact_version_id,span.quote,span.quote_sha256,
                        line_start=span.line_start,line_end=span.line_end,char_start=span.char_start,char_end=span.char_end,
                        source_sha256=artifact.content_hash,meeting_time=when)
                    evidence=[reference]
                    if item['owner'] not in span.quote:
                        if item['owner']!=previous_owner or previous_owner_ref is None:raise ValueError('OWNER_NOT_CONFIRMED:'+task['id'])
                        evidence.append(previous_owner_ref)
                    else:previous_owner_ref=reference
                    previous_owner=item['owner'];month,day=map(int,item['deadline'].split('/'));deadline=date(int(meeting['date'][:4]),month,day)
                    candidate=TaskCandidateInput('given-task-'+task['id']+'-'+str(index),workspace,task['title'],tuple(evidence),owner=item['owner'],
                        deadline=deadline.isoformat(),deadline_text=item['deadline'],meeting_time=when,
                        source_decision_id=decisions.get(task.get('source_decision')),candidate_version=adapter.format_version,
                        source_fingerprint=digest(item))
                    batch=dict(candidates=[candidate.to_dict()],meeting=dict(date=meeting['date'],document_id=artifact.artifact_id),confirmation_label='模拟人工确认')
                    batches.save(workspace,artifact.artifact_id,artifact.artifact_version_id,batch,now=recorded_at+timedelta(seconds=100+serial))
                    preview=review.preview_candidate(workspace,candidate.candidate_id)
                    reason=canonical_json(dict(confirmation_label='模拟人工确认',original_record=deepcopy(item),external_task_id=task['id'],
                        graph_valid_time=dict(kind='date',value=meeting['date']),system_recorded_at=(recorded_at+timedelta(seconds=100+serial)).isoformat(),
                        meeting=dict(document_id=artifact.artifact_id,source_version_id=artifact.artifact_version_id,content_sha256=artifact.content_hash,date=meeting['date'])))
                    now=recorded_at+timedelta(seconds=100+serial);serial+=1
                    if index==0:
                        result=review.confirm(workspace,candidate.candidate_id,expected_candidate_fingerprint=preview['fingerprint'],
                            reviewer='graph-eval-formal-human',reason=reason,now=now)
                        tasks[task['id']]=result['task']['id']
                    else:
                        if item.get('via')!='人工合并':raise ValueError('EXPLICIT_HUMAN_MERGE_REQUIRED:'+task['id'])
                        apply=['deadline']
                        current=review.get_task(workspace,tasks[task['id']],now=now)['task']
                        if current['owner']!=candidate.owner:apply.append('owner')
                        merged=review.preview_merge(workspace,tasks[task['id']],candidate.candidate_id,expected_candidate_fingerprint=preview['fingerprint'],apply_fields=apply,now=now)
                        result=review.merge(workspace,tasks[task['id']],candidate.candidate_id,expected_candidate_fingerprint=preview['fingerprint'],
                            expected_review_revision=merged['review_revision'],expected_preview_token=merged['preview_token'],apply_fields=apply,
                            reviewer='graph-eval-formal-human',reason=reason,now=now)
                    audit.append(dict(kind='task',external_id=task['id'],review_event_id=result['review_history'][-1]['id']))
            return dict(workspace_id=workspace,decisions=decisions,tasks=tasks,audit=audit,confirmation_label='模拟人工确认')
        finally:task_store.close()
    finally:repository.close()


def build_meridian(fixture,database_path):
    from tests.graph_eval_support import confirm_candidates
    no_gold(fixture)
    documents=[dict(workspace_id=fixture['workspace'],path=s['id']+'.md',content=s['text']) for s in fixture['sources']]
    originals=[]
    for i,edge in enumerate(fixture['confirmed_edges']):
        relation=edge['type'];subject=edge['from'];value=edge['to']
        if relation=='supersedes':relation='superseded_by';subject,value=value,subject
        elif relation=='owner_of':subject,value=value,subject
        original=dict(relation=relation,subject=subject,value=value,valid_on=edge['valid_from'],valid_to=edge.get('valid_to'),
            temporal_kind='event',project_scope=None)
        originals.append(dict(candidate_id='meridian-'+str(i),case_id='meridian',workspace_id=fixture['workspace'],
            source_path=edge['evidence']+'.md',original_candidate=original))
    return confirm_candidates(dict(split='dev',cases=[dict(case_id='meridian',workspace_id=fixture['workspace'],documents=documents)],
        candidates=originals),database_path)
