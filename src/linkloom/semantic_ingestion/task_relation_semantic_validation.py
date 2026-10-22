"""Source/permission verification, never a semantic verdict or P4 promotion.

No legacy identity, causality or revision assessor is executed here. All dates
remain conditional on an unaccepted model premise. No persistence is performed.
"""
from dataclasses import asdict,dataclass
from datetime import date
import re

from linkloom.semantic_ingestion.task_relation_models import ObservationRef,ProjectRef,ChangeRef,EvidenceQuote
from linkloom.semantic_ingestion.task_relation_validation import verify_relation_inputs,verify_evidence_quote
from linkloom.semantic_ingestion.task_change_authority import verify_change_authority
from linkloom.semantic_ingestion.task_relation_semantic_models import PREDICATES,VERDICTS


def relation_key(predicate,subject,object):
    """Symmetry is canonical identity, not a claim of two proven directions."""
    from linkloom.semantic_ingestion.task_relation_models import canonical_hash
    pair=(canonical_hash(subject.to_dict()),canonical_hash(object.to_dict()))
    return (predicate,*(sorted(pair) if predicate in {'SAME_TASK','RELATED_TO','PROJECT_ALIAS'} else pair))


def endpoint_shape(predicate,subject,object):
    if subject==object:
        return False
    task=isinstance(subject,ObservationRef)
    if predicate in {'SAME_TASK','RELATED_TO','REVISES_PLAN'}:
        return task and isinstance(object,ObservationRef)
    if predicate=='AFFECTED_BY':
        return task and isinstance(object,ChangeRef)
    if predicate=='IN_PROJECT':
        return task and isinstance(object,ProjectRef)
    return predicate=='PROJECT_ALIAS' and isinstance(subject,ProjectRef) and isinstance(object,ProjectRef)


def context_errors(context,*,decision_store=None):
    """Validate the complete caller-declared bounded inventory, before exposure.

    Completeness is an explicit caller assertion about this authorized snapshot,
    never global candidate recall. We cannot discover omitted external sources.
    """
    errors=set()
    if not context.workspace_id or not context.question or not context.inventory_complete or context.truncated:
        errors.add('INCOMPLETE_CONTEXT')
    limits=context.limits
    if (len(context.artifacts)>limits.max_artifacts or len(context.observations)>limits.max_observations
        or len(context.endpoints)>limits.max_endpoints or len(context.queries)>limits.max_proposals
        or sum(len(s.quote) for _,s in context.allowed_spans)>limits.max_allowed_chars):
        errors.add('CONTEXT_LIMIT_EXCEEDED')
    if not context.artifacts or not context.endpoints or not context.allowed_spans:
        errors.add('EMPTY_CONTEXT')
    if not context.queries:
        errors.add('EMPTY_QUERY_SET')
    if any(a.workspace_id!=context.workspace_id for a in context.artifacts):
        errors.add('CROSS_WORKSPACE')
    if any(a.artifact_version_id not in context.authorized_artifact_versions for a in context.artifacts):
        errors.add('UNAUTHORIZED_SOURCE')
    if len({a.artifact_id for a in context.artifacts})!=len(context.artifacts):
        errors.add('AMBIGUOUS_SOURCE_VERSION')
    source_errors,_,_,_=verify_relation_inputs((),context.artifacts,context.source_registry,context.observations)
    errors.update(source_errors)
    spans=dict(context.allowed_spans)
    endpoints=dict(context.endpoints)
    if (len(spans)!=len(context.allowed_spans) or len(endpoints)!=len(context.endpoints)
        or any(not isinstance(k,str) or not k or len(k)>128 for k in (*spans,*endpoints))):
        errors.add('DUPLICATE_OR_INVALID_HANDLE')
    for span in spans.values():
        proof=EvidenceQuote(span,span.char_start,span.char_end,span.quote,'RELATION_PROPOSAL')
        e,_=verify_evidence_quote(proof,context.workspace_id,context.artifacts,context.source_registry)
        errors.update(e)
        if span.evidence_ref in context.revoked_evidence_refs:
            errors.add('EVIDENCE_REVOKED')
    observations={ObservationRef.from_observation(o):o for o in context.observations}
    for observation in context.observations:
        if observation.provenance not in spans.values():
            errors.add('OBSERVATION_CONTEXT_NOT_AUTHORIZED')
        if observation.observation_id in context.revoked_observation_ids:
            errors.add('OBSERVATION_REVOKED')
    for ref in endpoints.values():
        if ref.workspace_id!=context.workspace_id:
            errors.add('CROSS_WORKSPACE')
        if isinstance(ref,ObservationRef):
            if ref not in observations:
                errors.add('OBSERVATION_REF_MISMATCH')
        elif isinstance(ref,ProjectRef):
            if not any(ref.name in s.quote for s in spans.values()):
                errors.add('PROJECT_NOT_IN_AUTHORIZED_TEXT')
        elif isinstance(ref,ChangeRef):
            if ref.evidence.span not in spans.values():
                errors.add('CHANGE_CONTEXT_NOT_AUTHORIZED')
            if any(value not in ref.evidence.quote for value in (ref.project,ref.before_vendor,ref.after_vendor)):
                errors.add('CHANGE_FIELDS_NOT_IN_SOURCE')
            e,_=verify_evidence_quote(ref.evidence,context.workspace_id,context.artifacts,context.source_registry)
            errors.update(e)
            if ref.kind=='REVIEWED_DECISION':
                e,_,_=verify_change_authority(ref,decision_store,context.source_registry,context.artifacts)
                errors.update(e)
        else:
            errors.add('INVALID_ENDPOINT')
    keys=[]
    for q in context.queries:
        if q.subject not in endpoints or q.object not in endpoints:
            errors.add('UNKNOWN_QUERY_ENDPOINT')
        elif not endpoint_shape(q.predicate,endpoints[q.subject],endpoints[q.object]):
            errors.add('INVALID_QUERY_SHAPE')
        else:
            keys.append(relation_key(q.predicate,endpoints[q.subject],endpoints[q.object]))
    if len(keys)!=len(set(keys)):
        errors.add('DUPLICATE_QUERY')
    return errors


@dataclass(frozen=True,slots=True)
class EvidenceValidation:
    proposal_id:str
    stage:str
    errors:tuple[str,...]
    supporting_evidence:tuple[EvidenceQuote,...]=()
    counter_evidence:tuple[EvidenceQuote,...]=()
    authority:str='SUPPORTING_ONLY'
    semantic_status:str='NOT_EVALUATED'
    publishable:bool=False
    validator_version:str='task-semantic-evidence/v1'

    def to_dict(self):
        return asdict(self)


def _resolve(citation,context):
    span=citation.span
    # Exact Unicode codepoints; neither stripping nor normalization is allowed.
    first=span.quote.find(citation.quote) if citation.quote else -1
    if (span not in dict(context.allowed_spans).values() or first<0
        or span.quote.find(citation.quote,first+1)!=-1):
        raise ValueError('QUOTE_NOT_UNIQUE_EXACT_AUTHORIZED_SUBSTRING')
    start=span.char_start+first
    proof=EvidenceQuote(span,start,start+len(citation.quote),citation.quote,'RELATION_PROPOSAL')
    errors,_=verify_evidence_quote(proof,context.workspace_id,context.artifacts,context.source_registry)
    if errors:
        raise ValueError('QUOTE_SOURCE_INVALID')
    return proof


def validate_proposals(result,context,*,decision_store=None):
    common=context_errors(context,decision_store=decision_store)
    if result.status!='EXECUTED':
        common.add('EXECUTION_NOT_VALID')
    refs=dict(context.endpoints).values()
    reports=[]
    for p in result.proposals:
        errors=set(common)
        if p.source_snapshot_hash!=context.source_snapshot_hash:
            errors.add('SNAPSHOT_CHANGED')
        if p.predicate not in PREDICATES or p.proposed_verdict not in VERDICTS or not endpoint_shape(p.predicate,p.subject,p.object):
            errors.add('INVALID_PROPOSAL_SHAPE')
        if p.subject not in refs or p.object not in refs:
            errors.add('UNKNOWN_ENDPOINT')
        if (not p.reason_codes or (p.proposed_verdict in {'SUPPORTED','CONFLICT'} and not p.supporting_evidence)
            or (p.proposed_verdict in {'REFUTED','CONFLICT'} and not p.counter_evidence)
            or (p.proposed_verdict=='UNKNOWN' and not p.missing_evidence)):
            errors.add('MISSING_VERDICT_EVIDENCE')
        if (set(p.changed_fields)-{'deadline','scope'} or (p.changed_fields and p.predicate!='REVISES_PLAN')
            or (p.predicate=='REVISES_PLAN' and p.proposed_verdict=='SUPPORTED' and not p.changed_fields)):
            errors.add('INVALID_CHANGE_FIELDS')
        support,counter=[],[]
        for citations,resolved in ((p.supporting_evidence,support),(p.counter_evidence,counter)):
            for citation in citations:
                try:
                    resolved.append(_resolve(citation,context))
                except ValueError as exc:
                    errors.add(str(exc))
        authority='SOURCE_CLAIM_ONLY' if isinstance(p.object,ChangeRef) and p.object.kind=='SOURCE_CLAIM' else 'SUPPORTING_ONLY'
        reports.append(EvidenceValidation(p.proposal_id,'EVIDENCE_INVALID' if errors else 'EVIDENCE_VALID',
            tuple(sorted(errors)),tuple(support),tuple(counter),authority))
    # A revision's identity dependency is explicit, still only MODEL_PROPOSED.
    valid_identity={relation_key('SAME_TASK',p.subject,p.object) for p,r in zip(result.proposals,reports)
        if p.predicate=='SAME_TASK' and p.proposed_verdict=='SUPPORTED' and r.stage=='EVIDENCE_VALID'}
    from dataclasses import replace
    for i,(p,r) in enumerate(zip(result.proposals,reports)):
        if p.predicate=='REVISES_PLAN' and p.proposed_verdict=='SUPPORTED' and relation_key('SAME_TASK',p.subject,p.object) not in valid_identity:
            reports[i]=replace(r,stage='EVIDENCE_INVALID',errors=(*r.errors,'MODEL_IDENTITY_PREMISE_MISSING'))
    return tuple(reports)


def _date_projection(observation):
    """M1 date precision plus local date boundary grammar; no revision replay.

    This does not interpret whether a plan was revised. Ambiguous bounds remain
    unknown. Ingestion/event dates never fill in a missing calendar year.
    """
    bounds=set()
    text=observation.deadline_text
    if text:
        for e in observation.field_evidence:
            if e.field!='deadline':
                continue
            if e.quote.count(text)!=1:
                bounds.add('UNKNOWN')
                continue
            offset=e.char_start-observation.provenance.char_start+e.quote.index(text)+len(text)
            right=observation.provenance.quote[offset:offset+18]
            start=offset-len(text)
            left=observation.provenance.quote[max(0,start-12):start]
            if right.startswith(('前完成','之前完成','以前完成','前提交','前交付')):
                bounds.add('BEFORE')
            elif right.startswith(('（含当日）','(含当日)','含当日')) or re.search(r'(?:最迟|最晚|\bby)\s*(?:在)?$',left):
                bounds.add('ON_OR_BEFORE')
            elif right.startswith(('当天完成','当日完成')):
                bounds.add('ON')
            else:
                bounds.add('UNKNOWN')
    return dict(value=observation.deadline,precision=observation.deadline_precision,role=observation.deadline_role,
        bound=next(iter(bounds)) if len(bounds)==1 else 'UNKNOWN')


def compare_proposed_revision(proposal,result,context):
    outcome=dict(status='BLOCKED',delta_calendar_days=None,conditional_on_model_semantics=True,
        semantic_status='NOT_EVALUATED',publishable=False,interpretation='PLAN_DEADLINE_BOUNDARY_CHANGE_ONLY',
        proposal_id=proposal.proposal_id,source_snapshot_hash=context.source_snapshot_hash)
    reports=validate_proposals(result,context)
    report=next((r for p,r in zip(result.proposals,reports) if p==proposal),None)
    if proposal.predicate!='REVISES_PLAN' or report is None or report.stage!='EVIDENCE_VALID':
        return outcome
    if proposal.proposed_verdict=='REFUTED':
        return dict(outcome,status='NOT_APPLICABLE')
    if proposal.proposed_verdict!='SUPPORTED':
        return outcome
    observations={ObservationRef.from_observation(o):o for o in context.observations}
    old,new=_date_projection(observations[proposal.object]),_date_projection(observations[proposal.subject])
    outcome.update(old_deadline=old,new_deadline=new)
    if (old['precision']!='DAY' or new['precision']!='DAY' or old['role']!='TASK_DEADLINE'
        or new['role']!='TASK_DEADLINE' or old['bound']=='UNKNOWN' or old['bound']!=new['bound']
        or old['value'] is None or new['value'] is None):
        return dict(outcome,status='NOT_COMPARABLE')
    delta=(date.fromisoformat(new['value'])-date.fromisoformat(old['value'])).days
    if delta and 'deadline' not in proposal.changed_fields:
        return dict(outcome,status='NOT_COMPARABLE')
    return dict(outcome,status='COMPUTABLE',delta_calendar_days=delta)
