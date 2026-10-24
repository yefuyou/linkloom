"""Explicit test-only bridge from the sealed research schema to M1/M3 inputs.

Original archive bytes and native IDs remain separate. The document envelope
below creates new local offsets/versions; it supplies no speaker, event time,
owner, current business state, Gold label or authenticated relation.
"""
from datetime import UTC, datetime
import json
from pathlib import Path
import zipfile

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticContext, SemanticQuery

ARCHIVE = Path(__file__).parent / 'fixtures/m2_real_failure_receipts.zip'


def read_json(path):
    with zipfile.ZipFile(ARCHIVE) as archive:
        return json.loads(archive.read(path))


def recorded(case_id, model):
    calls = read_json('manifest.json')['calls']
    path = next(p for p in calls if p.endswith('-' + case_id + '-' + model))
    return read_json(path + '/proposal.json')


def real_context(case_id, predicate='SAME_TASK'):
    case = read_json('inputs/' + case_id + '.json')
    registry = SourceReferenceRegistry()
    workspace = 'm2-real-regression-' + case_id
    artifacts, spans, segments, offsets = [], [], {}, {}
    for source in case['allowed_sources']:
        if source['permission'] != 'ALLOWED' or source['source_status'] != 'CURRENT':
            raise ValueError('Use explicit permission/version downgrade tests; do not authorize historical/denied input')
        text = ''
        for utterance in source['utterances']:
            start = len(text)
            text += utterance['utterance_id'] + '\n' + utterance['text'] + '\n\n'
            offsets[utterance['utterance_id']] = (start, len(text) - 2, utterance['text'])
        artifact = RawArtifact(workspace, source['source_id'], 'markdown', text,
                               ingestion_time=datetime(2026, 10, 24, tzinfo=UTC))
        segment, = parse_source_text(artifact).segments
        span = segment.evidence_span
        registry.register_evidence(workspace, span.evidence_ref, content_hash=span.content_hash,
                                    source_version=span.artifact_version_id)
        artifacts.append(artifact)
        spans.append((source['source_id'] + ':1', span))
        segments[source['source_id']] = segment, artifact
    observations, endpoints = [], []
    for item in case['observations']:
        segment, artifact = segments[item['source_id']]
        anchors = [offsets[native] for native in item['anchor_utterance_ids']]
        start, end = min(a[0] for a in anchors), max(a[1] for a in anchors)
        raw = artifact.content[start:end]
        observation = build_task_observation(TaskProposal(raw, tuple(a[2] for a in anchors)), segment, artifact)
        assert observation.verify(artifact)
        observations.append(observation)
        endpoints.append((item['observation_id'], ObservationRef.from_observation(observation)))
    queries = ((SemanticQuery('SAME_TASK', 'B', 'A'), SemanticQuery(predicate, 'B', 'A'))
               if predicate == 'REVISES_PLAN' else (SemanticQuery(predicate, 'A', 'B'),))
    return SemanticContext(workspace, case['question'], tuple(artifacts), tuple(observations),
        tuple(endpoints), tuple(spans), registry, frozenset(a.artifact_version_id for a in artifacts),
        True, False, queries)


def typed_variant(context, record, verdict, *, keep_counter=True):
    """Adversarial contract variation, NOT a replay of the research wire response.

    The caller names the verdict. In particular NONE has no automatic mapping.
    Citations retain their original text; research-only prose/approval is omitted.
    """
    def citations(values):
        return [dict(span_id=v['source_id'] + ':1', quote=v['quote']) for v in values]

    rows = []
    for query in context.queries:
        premise = query.predicate == 'SAME_TASK' and len(context.queries) > 1
        proposed = 'SUPPORTED' if premise else verdict
        support = citations(record['evidence'])
        counter = citations(record['counterevidence']) if keep_counter and not premise else []
        if proposed == 'REFUTED':
            counter, support = support + counter, []
        elif proposed == 'UNKNOWN':
            support, counter = [], support + counter
        rows.append(dict(predicate=query.predicate, subject=query.subject, object=query.object,
            proposed_verdict=proposed, supporting_evidence=support, counter_evidence=counter,
            missing_evidence=['Identity and scope are not established.'] if proposed == 'UNKNOWN' else [],
            reason_codes=['TEST_ONLY_RESEARCH_SCHEMA_VARIANT'],
            changed_fields=['scope'] if query.predicate == 'REVISES_PLAN' and proposed == 'SUPPORTED' else []))
    return dict(proposals=rows)
