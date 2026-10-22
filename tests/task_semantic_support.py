"""Public-only M1 fixtures. No evaluator or Gold import."""
from datetime import UTC, datetime
import json
from pathlib import Path

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, ProjectRef, ChangeRef, EvidenceQuote
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticContext, SemanticQuery

INPUT=Path(__file__).parent/'fixtures/task_semantic/inputs/dev.json'


def public_cases():
    return json.loads(INPUT.read_text(encoding='utf8'))['cases']


def context_for(case):
    artifacts={s['id']:RawArtifact(case['workspace_id'],s['artifact_id'],s['source_type'],s['content'],
        ingestion_time=datetime(2026,10,18,tzinfo=UTC)) for s in case['sources']}
    parsed={name:parse_source_text(a).segments for name,a in artifacts.items()}
    registry=SourceReferenceRegistry()
    spans=[]
    for name,segments in parsed.items():
        for i,segment in enumerate(segments):
            span=segment.evidence_span
            registry.register_evidence(case['workspace_id'],span.evidence_ref,
                content_hash=span.content_hash,source_version=span.artifact_version_id)
            spans.append((name+':'+str(i+1),span))
    endpoints,observations=[],[]
    for task in case['tasks']:
        segment=parsed[task['source']][task['ordinal']]
        raw=segment.evidence_span.quote
        observation=build_task_observation(TaskProposal(raw,(raw,),
            owner=segment.speaker if raw.startswith('我负责') else None,
            project=task['project'],deadline_text=task['deadline_text']),segment,artifacts[task['source']])
        if not observation.verify(artifacts[task['source']]):
            raise ValueError('public M1 observation failed readback')
        observations.append(observation)
        endpoints.append((task['handle'],ObservationRef.from_observation(observation)))
    for project in case['projects']:
        endpoints.append((project['handle'],ProjectRef(case['workspace_id'],project['name'])))
    for change in case['changes']:
        span=parsed[change['source']][change['ordinal']].evidence_span
        proof=EvidenceQuote(span,span.char_start,span.char_end,span.quote,'CHANGE_CLAIM')
        endpoints.append((change['handle'],ChangeRef(case['workspace_id'],change['project'],
            change['before'],change['after'],proof)))
    return SemanticContext(workspace_id=case['workspace_id'],question=case['question'],
        artifacts=tuple(artifacts.values()),observations=tuple(observations),endpoints=tuple(endpoints),
        allowed_spans=tuple(spans),source_registry=registry,
        authorized_artifact_versions=frozenset(a.artifact_version_id for a in artifacts.values()),
        inventory_complete=True,truncated=False,
        queries=tuple(SemanticQuery(*q) for q in case['queries']))


def selected_context(name='revision'):
    return context_for(next(c for c in public_cases() if c['case_id']==name))


def response_arguments(context,verdict='SUPPORTED'):
    """One invented untrusted response for contract tests, not LLM quality."""
    citations=[dict(span_id=handle,quote=span.quote) for handle,span in context.allowed_spans]
    return dict(proposals=[dict(predicate=q.predicate,subject=q.subject,object=q.object,
        proposed_verdict=verdict,supporting_evidence=citations if verdict in {'SUPPORTED','CONFLICT'} else [],
        counter_evidence=citations if verdict in {'REFUTED','CONFLICT'} else [],
        missing_evidence=['A direct evidentiary bridge is missing.'] if verdict=='UNKNOWN' else [],
        reason_codes=['INVENTED_FAKE_RESPONSE'],changed_fields=['deadline'] if q.predicate=='REVISES_PLAN' and verdict=='SUPPORTED' else [])
        for q in context.queries])
