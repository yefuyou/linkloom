"""Read-only comparison of evidenced deadline boundaries, never actual delay."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re

from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import (
    EvidenceQuote, ObservationRef, RelationAssessment, SourceDependency, canonical_hash, _strict,
)
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision, plan_statement_assertive
from linkloom.semantic_ingestion.task_relation_validation import _sentences


@dataclass(frozen=True, slots=True)
class DeadlineProjection:
    value: str | None
    precision: str
    role: str
    bound: str
    evidence: tuple[EvidenceQuote, ...]

    def __post_init__(self):
        if self.bound not in {'BEFORE','ON_OR_BEFORE','ON','UNKNOWN'}:
            raise ValueError('invalid deadline bound')
        if self.value is not None and date.fromisoformat(self.value).isoformat() != self.value:
            raise ValueError('invalid deadline value')
        if not isinstance(self.evidence,tuple) or any(not isinstance(e,EvidenceQuote) for e in self.evidence):
            raise ValueError('invalid date evidence')

    def to_dict(self):
        return dict(value=self.value,precision=self.precision,role=self.role,bound=self.bound,
                    evidence=[e.to_dict() for e in self.evidence])

    @classmethod
    def from_dict(cls,data):
        values=_strict(data,('value','precision','role','bound','evidence'))
        values['evidence']=tuple(EvidenceQuote.from_dict(e) for e in values['evidence'])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class RevisionComparison:
    subject: ObservationRef | None
    object: ObservationRef | None
    same_task_id: str | None
    revision_id: str | None
    status: str
    reason_codes: tuple[str,...]
    old_deadline: DeadlineProjection
    new_deadline: DeadlineProjection
    delta_calendar_days: int | None
    dependencies: tuple[SourceDependency,...]
    asserted_effective_at: str | None = None
    as_of: str | None = None
    plan_currentness: str = 'NOT_ESTABLISHED'
    comparison_id: str = field(init=False)

    def __post_init__(self):
        if self.status not in {'COMPUTABLE','BLOCKED','NOT_APPLICABLE','NOT_COMPARABLE'}:
            raise ValueError('invalid comparison status')
        if self.plan_currentness not in {'NOT_ESTABLISHED','FUTURE_EFFECTIVE','EFFECTIVE_AT_REFERENCE'}:
            raise ValueError('invalid plan time status')
        if not self.reason_codes or any(not isinstance(v,str) or not v for v in self.reason_codes):
            raise ValueError('comparison requires reasons')
        if self.status == 'COMPUTABLE':
            old,new=self.old_deadline,self.new_deadline
            if (type(self.delta_calendar_days) is not int or not self.same_task_id or not self.revision_id
                or old.role != 'TASK_DEADLINE' or new.role != 'TASK_DEADLINE'
                or old.precision != 'DAY' or new.precision != 'DAY'
                or old.bound=='UNKNOWN' or old.bound != new.bound or not old.evidence or not new.evidence
                or old.value is None or new.value is None
                or self.delta_calendar_days != (date.fromisoformat(new.value)-date.fromisoformat(old.value)).days):
                raise ValueError('unsafe computable comparison')
        elif self.delta_calendar_days is not None:
            raise ValueError('blocked/noncomparable results require null delta')
        object.__setattr__(self,'comparison_id','revision_comparison_v1_'+canonical_hash(self._payload()))

    def _payload(self):
        return {'schema_version':'revision-comparison/v1','authority':'SUPPORTING_ONLY',
            'subject':self.subject.to_dict() if self.subject else None,
            'object':self.object.to_dict() if self.object else None,
            'same_task_id':self.same_task_id,'revision_id':self.revision_id,'status':self.status,
            'reason_codes':list(self.reason_codes),'old_deadline':self.old_deadline.to_dict(),
            'new_deadline':self.new_deadline.to_dict(),'delta_calendar_days':self.delta_calendar_days,
            'dependencies':[d.to_dict() for d in self.dependencies],
            'asserted_effective_at':self.asserted_effective_at,'as_of':self.as_of,'plan_currentness':self.plan_currentness,
            'interpretation':'PLAN_DEADLINE_BOUNDARY_CHANGE_ONLY'}

    def to_dict(self):
        return {'comparison_id':self.comparison_id,**self._payload()}

    @classmethod
    def from_dict(cls,data):
        try:
            names=('subject','object','same_task_id','revision_id','status','reason_codes','old_deadline',
                   'new_deadline','delta_calendar_days','dependencies','asserted_effective_at','as_of','plan_currentness')
            values={name:data[name] for name in names}
            for name in ('subject','object'):
                values[name]=ObservationRef.from_dict(values[name]) if values[name] is not None else None
            for name in ('old_deadline','new_deadline'):
                values[name]=DeadlineProjection.from_dict(values[name])
            values['dependencies']=tuple(SourceDependency.from_dict(d) for d in values['dependencies'])
            values['reason_codes']=tuple(values['reason_codes'])
            result=cls(**values)
            if result.to_dict()!=data:
                raise ValueError('altered comparison payload')
            return result
        except (TypeError,KeyError,AttributeError) as error:
            raise ValueError('malformed comparison') from error


def deadline_projection(observation):
    if not isinstance(observation,TaskObservation):
        return DeadlineProjection(None,'UNKNOWN','UNKNOWN','UNKNOWN',())
    span=observation.provenance
    fields=[e for e in observation.field_evidence if e.field=='deadline']
    evidence=[EvidenceQuote(span,e.char_start,e.char_end,e.quote,'DEADLINE_BOUND') for e in fields]
    bounds=set()
    if observation.deadline_text is not None:
        raw_start=next(e.char_start for e in observation.field_evidence if e.field=='raw_text')
        for start,end,sentence,asserted in _sentences(observation.raw_text):
            if not plan_statement_assertive(sentence,asserted):
                continue
            for match in re.finditer(re.escape(observation.deadline_text),sentence):
                absolute=raw_start+start+match.start()
                if not any(e.char_start<=absolute and e.char_end>=absolute+len(match.group()) for e in fields):
                    continue
                # The nearest clause supplies the boundary role; document dates
                # elsewhere in the sentence cannot complete this task deadline.
                left=re.split('[，,]',sentence[:match.start()])[-1]
                right=re.split('[，,。；;]',sentence[match.end():])[0]
                exclusive=bool(re.search(r'\bbefore\s*$',left,re.I) or re.match(r'\s*(?:之前|以前|前)\s*(?:完成|提交|交付|上线|验收)',right))
                inclusive=bool(re.search(r'(?:最迟|最晚|\bby|\bdue by|\bno later than)\s*(?:在)?\s*[:：为是]?\s*$',left,re.I)
                               or re.match(r'\s*[（(]?含当日[）)]?',right)
                               or re.match(r'\s*[，,]按该日或之前完成',sentence[match.end():])
                               or re.match(r'[^，,。；;]*[，,]\s*含当日[。；;]?',sentence[match.end():]))
                exact=bool(re.match(r'\s*[（(]?(?:当天|当日)完成[）)]?',right))
                choices=[name for name,ok in (('BEFORE',exclusive),('ON_OR_BEFORE',inclusive),('ON',exact)) if ok]
                bounds.add(choices[0] if len(choices)==1 else 'UNKNOWN')
                evidence.append(EvidenceQuote(span,raw_start+start,raw_start+end,
                    observation.raw_text[start:end],'DEADLINE_BOUND'))
    unique={canonical_hash(e.to_dict()):e for e in evidence}
    return DeadlineProjection(observation.deadline,observation.deadline_precision,observation.deadline_role,
        next(iter(bounds)) if len(bounds)==1 else 'UNKNOWN',tuple(unique[k] for k in sorted(unique)))


def compare_revision(new,old,*,same_task,revision,artifacts,source_registry,evidence=(),context_observations=(),as_of=None):
    """Reverify both relation proofs at the caller's snapshot before any math.

    as_of is an optional explicit calendar reference for a declared effective
    date, never a deadline fallback or a general current-plan selector.
    """
    if as_of is not None and type(as_of) is not date:
        raise ValueError('as_of must be an explicit calendar date')
    replay=assess_plan_revision(new,old,same_task=same_task,artifacts=artifacts,source_registry=source_registry,
        evidence=evidence,context_observations=context_observations)
    old_date,new_date=deadline_projection(old),deadline_projection(new)
    def finish(status,reasons,delta=None):
        effective=replay.asserted_effective_at
        currentness='NOT_ESTABLISHED'
        if effective and as_of:
            currentness='FUTURE_EFFECTIVE' if as_of<date.fromisoformat(effective) else 'EFFECTIVE_AT_REFERENCE'
        return RevisionComparison(ObservationRef.from_observation(new) if isinstance(new,TaskObservation) else None,
            ObservationRef.from_observation(old) if isinstance(old,TaskObservation) else None,
            same_task.assessment_id if isinstance(same_task,RelationAssessment) else None,
            revision.assessment_id if isinstance(revision,RelationAssessment) else None,status,tuple(reasons),
            old_date,new_date,delta,replay.dependencies,effective,as_of.isoformat() if as_of else None,currentness)
    try:
        canonical=isinstance(revision,RelationAssessment) and RelationAssessment.from_dict(revision.to_dict()).assessment_id==replay.assessment_id
    except (ValueError,TypeError,AttributeError):
        canonical=False
    if not canonical:
        return finish('BLOCKED',('INVALID_OR_STALE_REVISION_PREMISE',))
    if replay.verdict=='REFUTED':
        return finish('NOT_APPLICABLE',replay.reason_codes)
    if replay.verdict!='SUPPORTED':
        return finish('BLOCKED',replay.reason_codes)
    if any(d.role!='TASK_DEADLINE' or d.precision!='DAY' or d.value is None for d in (old_date,new_date)):
        return finish('NOT_COMPARABLE',('DEADLINE_ROLE_OR_PRECISION_UNSUITABLE',))
    if old_date.bound=='UNKNOWN' or old_date.bound!=new_date.bound:
        return finish('NOT_COMPARABLE',('DEADLINE_BOUND_UNKNOWN_OR_INCOMPATIBLE',))
    delta=(date.fromisoformat(new_date.value)-date.fromisoformat(old_date.value)).days
    unchanged=any(re.search(r'截止(?:边界|日期)?不变|截止未变',e.quote) for e in replay.supporting_evidence)
    restated=any(re.search(r'将原定'+re.escape(old.deadline_text or '')+r'前完成改为'
                         +re.escape(new.deadline_text or '')+r'前完成',e.quote) for e in replay.supporting_evidence)
    if delta==0 and not (unchanged or restated):
        return finish('NOT_COMPARABLE',('UNCHANGED_DEADLINE_NOT_ASSERTED',))
    if delta!=0 and 'deadline' not in replay.changed_fields:
        return finish('NOT_COMPARABLE',('DEADLINE_CHANGE_NOT_ASSERTED',))
    return finish('COMPUTABLE',('VERIFIED_PLAN_BOUNDARIES',),delta)
