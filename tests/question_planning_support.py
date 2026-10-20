"""Public input loader only: no expected plans, Gold, scores or case IDs."""
from datetime import UTC, datetime
import json
from pathlib import Path

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_candidates import DiscoveryLimits, DiscoveryResult
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote, ObservationRef, ProjectRef
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticContext

INPUT = Path(__file__).parent / 'fixtures/question_planning/inputs.json'


def public_inputs():
    return json.loads(INPUT.read_text(encoding='utf8'))


def planning_context(question, public=None):
    public = public or public_inputs()
    workspace = question['workspace_id']
    sources = [] if question.get('empty_snapshot') else public['sources']
    artifacts = {s['handle']: RawArtifact(workspace, 'public-' + s['handle'], 'document', s['text'],
                 ingestion_time=datetime(2026, 10, 19, tzinfo=UTC)) for s in sources}
    segments = {h: parse_source_text(a).segments[0] for h, a in artifacts.items()}
    registry = SourceReferenceRegistry()
    for segment in segments.values():
        span = segment.evidence_span
        registry.register_evidence(workspace, span.evidence_ref,
                                   content_hash=span.content_hash, source_version=span.artifact_version_id)
    endpoints, observations = [], []
    for task in public['tasks']:
        if task['handle'] not in question['task_handles']:
            continue
        segment, artifact = segments[task['source']], artifacts[task['source']]
        raw = segment.evidence_span.quote
        observation = build_task_observation(TaskProposal(raw, (raw,), owner=task['owner'],
                         project=task['project'], deadline_text=task['deadline_text']), segment, artifact)
        assert observation.verify(artifact)
        observations.append(observation)
        endpoints.append((task['handle'], ObservationRef.from_observation(observation)))
    if artifacts:
        endpoints.extend((('delivery', ProjectRef(workspace, '曙光/交付组')),
                          ('purchasing', ProjectRef(workspace, '曙光/采购组'))))
    if question['include_change']:
        span = segments['s_change'].evidence_span
        quote = EvidenceQuote(span, span.char_start, span.char_end, span.quote, 'CHANGE_CLAIM')
        endpoints.append(('change', ChangeRef(workspace, '曙光/交付组', '海帆', '星港', quote)))
    context = SemanticContext(workspace, question['question'], tuple(artifacts.values()),
        tuple(observations), tuple(endpoints), tuple((h, s.evidence_span) for h, s in segments.items()),
        registry, frozenset(a.artifact_version_id for a in artifacts.values()),
        inventory_complete=not question.get('empty_snapshot', False), truncated=False)
    discovery = DiscoveryResult((), (), len(observations), 0, DiscoveryLimits(), {}, 0, {},
        ('CANDIDATE_LIMIT',) if question.get('candidate_truncated') else ())
    return context, discovery
