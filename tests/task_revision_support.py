"""P3 public inputs only: no evaluator labels or Gold paths."""
from datetime import UTC, date, datetime
from dataclasses import replace
import json
from pathlib import Path

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_revision_comparison import compare_revision
from linkloom.decision_memory.sources import SourceReferenceRegistry

INPUT=Path(__file__).parent/'fixtures/task_revisions/inputs/dev.json'


def public_cases():
    data=json.loads(INPUT.read_text(encoding='utf8'))
    if set(data)!={'schema_version','split','cases'} or data['schema_version']!='task-revisions-input/v1' or data['split']!='dev':
        raise ValueError('invalid public revision input')
    return data['cases']


def build_case(case):
    registry=SourceReferenceRegistry()
    artifacts={s['artifact_id']:RawArtifact(workspace_id=case['workspace_id'],**s,
        ingestion_time=datetime(2026,10,18,tzinfo=UTC)) for s in case['sources']}
    parsed={name:parse_source_text(a).segments for name,a in artifacts.items()}
    for name,segments in parsed.items():
        for segment in segments:
            registry.register_evidence(case['workspace_id'],segment.evidence_span.evidence_ref,
                content_hash=artifacts[name].content_hash,source_version=artifacts[name].artifact_version_id)
    observations={}
    for item in case['observations']:
        artifact=artifacts[item['source_id']]
        observation=build_task_observation(TaskProposal.from_dict(item['task']),parsed[item['source_id']][item['segment_index']],artifact)
        if not observation.verify(artifact):
            raise ValueError('M1 public observation did not verify')
        observations[item['label']]=observation
    return observations,artifacts,parsed,registry


def run_case(case):
    observations,artifacts,parsed,registry=build_case(case)
    before={label:o.to_dict() for label,o in observations.items()}
    rows=[]
    for question in case['questions']:
        evidence=[]
        for item in question['evidence']:
            span=parsed[item['source_id']][item['segment_index']].evidence_span
            if span.quote.count(item['quote'])!=1:
                raise ValueError('ambiguous public proof')
            start=span.char_start+span.quote.index(item['quote'])
            evidence.append(EvidenceQuote(span,start,start+len(item['quote']),item['quote'],'RELATION_PROPOSAL'))
        new,old=(observations[question[key]] for key in ('subject','object'))
        args=dict(artifacts=list(artifacts.values()),source_registry=registry,context_observations=tuple(observations.values()))
        identity=assess_same_task(new,old,**args)
        revision=assess_plan_revision(new,old,same_task=identity,evidence=evidence,**args)
        comparison=compare_revision(new,old,same_task=identity,revision=revision,evidence=evidence,
            as_of=date.fromisoformat(question['as_of']) if question.get('as_of') else None,**args)
        rows.append(dict(question_id=question['question_id'],same_task=identity.to_dict(),
                         revision=revision.to_dict(),comparison=comparison.to_dict()))
    assert before=={label:o.to_dict() for label,o in observations.items()}
    return dict(case_id=case['case_id'],predictions=rows,observations_unchanged=True,
        sources=[dict(workspace_id=a.workspace_id,artifact_id=a.artifact_id,artifact_version_id=a.artifact_version_id,
                      content_hash=a.content_hash,content=a.content) for a in artifacts.values()])


def invalid_premise_trials():
    """Five explicit contract violations, separate from semantic Dev scores."""
    case=next(c for c in public_cases() if c['case_id']=='D04')
    rows=[]
    for variant in ('forged_identity','stale_identity','tampered_source','cross_workspace','wrong_pair'):
        observations,artifacts,parsed,registry=build_case(case)
        question=case['questions'][0]
        new,old=(observations[question[key]] for key in ('subject','object'))
        quotes=[EvidenceQuote(s.evidence_span,s.evidence_span.char_start,s.evidence_span.char_end,
                              s.evidence_span.quote,'RELATION_PROPOSAL') for segments in parsed.values() for s in segments]
        args=dict(artifacts=list(artifacts.values()),source_registry=registry,context_observations=tuple(observations.values()))
        identity=assess_same_task(new,old,**args)
        if variant=='forged_identity':
            identity=replace(identity,reason_codes=('FAKE_PROOF',))
        elif variant=='stale_identity':
            registry.register_evidence(old.provenance.workspace_id,old.provenance.evidence_ref,
                                      content_hash='0'*64,source_version='new-version')
        elif variant=='tampered_source':
            args['artifacts']=[replace(a,content=a.content+'来源已更新。') if a.artifact_id==old.provenance.artifact_id else a for a in args['artifacts']]
        elif variant=='cross_workspace':
            foreign=replace(artifacts[new.provenance.artifact_id],workspace_id='isolated-other-workspace')
            item=next(o for o in case['observations'] if o['label']==question['subject'])
            new=build_task_observation(TaskProposal.from_dict(item['task']),parse_source_text(foreign).segments[item['segment_index']],foreign)
            args['artifacts'].append(foreign)
        else:
            new=next(o for o in observations.values() if o.observation_id not in {new.observation_id,old.observation_id})
        revision=assess_plan_revision(new,old,same_task=identity,evidence=quotes,**args)
        comparison=compare_revision(new,old,same_task=identity,revision=revision,evidence=quotes,**args)
        rows.append(dict(variant=variant,revision=revision.to_dict(),comparison=comparison.to_dict(),
                         released=revision.verdict=='SUPPORTED' or comparison.delta_calendar_days is not None))
    return rows
