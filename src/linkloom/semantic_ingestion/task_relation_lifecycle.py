"""Append-only relation events and exact, bounded replay recipes.

The hashes detect corruption; they are not signatures or a replacement for
filesystem access control. Observation snapshots remain owned by M1.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json

from linkloom.semantic_ingestion.task_relation_models import (
    ChangeRef, EvidenceQuote, ObservationRef, ProjectRef, RelationAssessment, canonical_hash, _strict,
)

STATES = frozenset({'ACTIVE', 'STALE', 'RETRACTED', 'REPLACED', 'INVALIDATED'})
TRANSITIONS = {'ACTIVE': STATES - {'ACTIVE'}, 'STALE': {'RETRACTED', 'REPLACED', 'INVALIDATED'}}
SCHEMA_VERSION = 'task-relation-store/v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


@dataclass(frozen=True, slots=True)
class RelationEvent:
    workspace_id: str
    assessment_id: str
    event_type: str
    reason: str
    actor: str
    previous_event_id: str | None
    recorded_at: datetime
    evidence: tuple[EvidenceQuote, ...] = ()
    replacement_id: str | None = None
    event_id: str = field(init=False)

    def __post_init__(self):
        if not all(nonempty(v) for v in (self.workspace_id,self.assessment_id,self.reason,self.actor)):
            raise ValueError('event requires workspace, assessment, actor and reason')
        if self.event_type not in STATES or (self.previous_event_id is not None and not nonempty(self.previous_event_id)):
            raise ValueError('invalid lifecycle event')
        if not isinstance(self.recorded_at,datetime) or self.recorded_at.utcoffset() is None:
            raise ValueError('event time must be timezone-aware')
        if (not isinstance(self.evidence,tuple) or any(not isinstance(q,EvidenceQuote)
                or q.span.workspace_id != self.workspace_id for q in self.evidence)):
            raise ValueError('invalid lifecycle evidence')
        if (self.event_type=='REPLACED') != (self.replacement_id is not None):
            raise ValueError('only replacement event requires replacement ID')
        if self.replacement_id is not None and (not nonempty(self.replacement_id) or self.replacement_id==self.assessment_id):
            raise ValueError('replacement must name a different assessment')
        object.__setattr__(self,'event_id','relation_event_v1_'+canonical_hash(self._payload()))

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs)

    def _payload(self):
        return dict(workspace_id=self.workspace_id,assessment_id=self.assessment_id,event_type=self.event_type,
            reason=self.reason,actor=self.actor,previous_event_id=self.previous_event_id,
            recorded_at=self.recorded_at.isoformat(),evidence=[q.to_dict() for q in self.evidence],
            replacement_id=self.replacement_id)

    def to_dict(self):
        return {'event_id':self.event_id,**self._payload()}

    @classmethod
    def from_dict(cls, data):
        values=_strict(data,('event_id','workspace_id','assessment_id','event_type','reason','actor',
                             'previous_event_id','recorded_at','evidence','replacement_id'))
        values.pop('event_id')
        values['recorded_at']=datetime.fromisoformat(values['recorded_at'])
        values['evidence']=tuple(EvidenceQuote.from_dict(q) for q in values['evidence'])
        result=cls(**values)
        if result.to_dict()!=data:
            raise ValueError('noncanonical lifecycle event')
        return result


def workspace_of(record):
    refs=[ref for ref in (record.subject,record.object) if ref is not None]
    workspaces={ref.workspace_id for ref in refs}
    if isinstance(record,RelationAssessment):
        workspaces.update(d.span.workspace_id for d in record.dependencies)
    if len(workspaces)!=1:
        raise ValueError('record requires one unambiguous workspace')
    return next(iter(workspaces))


def replacement_scope(assessment):
    """Version-independent source anchors for explicit assessment corrections.

    This is not task entity matching. Moving a task to another source/segment
    requires explicit new assessments and retraction, not an inferred alias.
    """
    anchors=[]
    for endpoint,observation in zip((assessment.subject,assessment.object),assessment.observations):
        if isinstance(endpoint,ObservationRef):
            anchors.append(('OBSERVATION',endpoint.workspace_id,endpoint.artifact_id,observation.provenance.ordinal))
        elif isinstance(endpoint,ProjectRef):
            anchors.append(('PROJECT',endpoint.workspace_id,endpoint.name))
        elif isinstance(endpoint,ChangeRef):
            anchors.append(('CHANGE',endpoint.workspace_id,endpoint.project,endpoint.before_vendor,endpoint.after_vendor,
                            endpoint.kind,endpoint.evidence.span.artifact_id,endpoint.evidence.span.ordinal))
        else:
            anchors.append(('MISSING',))
    return tuple(sorted(anchors)) if assessment.predicate=='SAME_TASK' else tuple(anchors)


def make_recipe(assessment, evidence=(), replay_premise_ids=()):
    """Store locators into existing dependency spans, never another source body.

    Original proposal boundaries matter. A query must not widen a narrow
    submitted proposal to an entire document while reconstructing the proof.
    """
    if not isinstance(evidence,(tuple,list)) or any(not isinstance(q,EvidenceQuote) for q in evidence):
        raise ValueError('invalid replay proposals')
    spans={canonical_hash(asdict(d.span)):d.span for d in assessment.dependencies}
    proposals=[]
    for quote in evidence:
        key=canonical_hash(asdict(quote.span))
        if key not in spans:
            raise ValueError('proposal span missing from assessment dependencies')
        proposals.append(dict(span_hash=key,char_start=quote.char_start,char_end=quote.char_end,
                             role=quote.role,quote_hash=canonical_hash(quote.quote)))
    refs={canonical_hash(d.observation.to_dict()):d.observation.to_dict()
          for d in assessment.dependencies if d.observation is not None}
    if not isinstance(replay_premise_ids,(tuple,list)) or any(not nonempty(v) for v in replay_premise_ids):
        raise ValueError('invalid replay premise references')
    premises=sorted(set((*assessment.premise_ids,*replay_premise_ids)))
    return dict(schema_version='relation-replay/v1',observation_refs=[refs[k] for k in sorted(refs)],
                proposals=sorted(proposals,key=encoded),premise_ids=premises)


def read_recipe(assessment, data):
    _strict(data,('schema_version','observation_refs','proposals','premise_ids'))
    if data['schema_version']!='relation-replay/v1':
        raise ValueError('unsupported replay recipe')
    spans={canonical_hash(asdict(d.span)):d.span for d in assessment.dependencies}
    proposals=[]
    for locator in data['proposals']:
        _strict(locator,('span_hash','char_start','char_end','role','quote_hash'))
        span=spans[locator['span_hash']]
        start,end=locator['char_start'],locator['char_end']
        quote=EvidenceQuote(span,start,end,span.quote[start-span.char_start:end-span.char_start],locator['role'])
        if canonical_hash(quote.quote)!=locator['quote_hash']:
            raise ValueError('proposal integrity mismatch')
        proposals.append(quote)
    if make_recipe(assessment,proposals,data['premise_ids'])!=data:
        raise ValueError('noncanonical replay recipe')
    return tuple(ObservationRef.from_dict(ref) for ref in data['observation_refs']),tuple(proposals),tuple(data['premise_ids'])


def validate_event_chain(events, workspace, assessment_id):
    previous=None
    for event in events:
        if event.workspace_id!=workspace or event.assessment_id!=assessment_id:
            raise ValueError('event scope mismatch')
        if previous is None:
            if event.event_type!='ACTIVE' or event.previous_event_id is not None:
                raise ValueError('missing initial ACTIVE event')
        elif (event.previous_event_id!=previous.event_id or event.recorded_at<previous.recorded_at
              or event.event_type not in TRANSITIONS.get(previous.event_type,())):
            raise ValueError('invalid lifecycle chain')
        previous=event
    if previous is None:
        raise ValueError('missing lifecycle chain')
    return previous.event_type
