"""One bounded AMI canary using existing F/Discovery/Provider contracts.

This process never imports a scorer or reads queries, summaries or qrels.
Without --execute it only prepares requests; no SDK client is constructed.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor
from linkloom.semantic_ingestion.task_observation_discovery import DiscoveryLimits, TaskObservationDiscoveryService

MEETINGS = ('ES2002a', 'ES2005c')
MODEL = 'gemini-3.8-flash'
GUARD = 'ami-explicit-two-meeting-canary/v1'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def load_sources(inputs):
    """Only transcript paths are opened; the data manifest is transcript-side."""
    sources, sidecars = [], []
    manifest = json.loads((inputs / 'manifest.json').read_text(encoding='utf8'))
    for meeting in MEETINGS:
        relative = f'transcripts/{meeting}.json'
        raw = (inputs / relative).read_bytes()
        if digest(raw) != manifest['files'][relative]:
            raise ValueError('transcript manifest mismatch')
        transcript = json.loads(raw)
        if digest(transcript['canonical_text'].encode('utf8')) != transcript['canonical_sha256']:
            raise ValueError('canonical hash mismatch')
        chunks = transcript['chunks']
        if len(chunks) != {'ES2002a': 8, 'ES2005c': 20}[meeting]:
            raise ValueError('incomplete meeting chunk population')
        if ''.join(c['text'] for c in chunks) != transcript['canonical_text']:
            raise ValueError('incomplete transcript chunks')
        cursor = 0
        for index, chunk in enumerate(chunks):
            text = chunk['text']
            start, end = chunk['start'], chunk['end']
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start <= end <= len(transcript['canonical_text'])
                    or start != cursor or end != start + len(text)
                    or transcript['canonical_text'][start:end] != text
                    or digest(text.encode('utf8')) != chunk['sha256']
                    or len(text) > 8000 or len(text.encode('utf8')) > 16000):
                raise ValueError('chunk hash/span/budget mismatch')
            cursor = end
            artifact = RawArtifact(workspace_id=meeting, artifact_id=f'{meeting}:chunk:{index:04d}',
                source_type='manual_text', content=text,
                ingestion_time=datetime(2026, 10, 23, tzinfo=UTC))
            sources.append(artifact)
            sidecars.append(dict(artifact_id=artifact.artifact_id, meeting_id=meeting,
                canonical_start=chunk['start'], canonical_end=chunk['end'],
                canonical_sha256=transcript['canonical_sha256'],
                native_utterances=[{key: act[key] for key in
                    ('utterance_id', 'speaker', 'text', 'char_start', 'char_end', 'source_member', 'member_sha256')}
                    for act in transcript['utterances']
                    if act['char_start'] < chunk['end'] and act['char_end'] > chunk['start']]))
    if len(sources) != 28:
        raise ValueError('unexpected canary source population')
    return sources, sidecars


def extract_sources(sources, provider, guard, on_batch, stopped=lambda: False, task_profile=None):
    registry = SourceReferenceRegistry()
    current = {(a.workspace_id, a.artifact_id): a for a in sources}
    for artifact in sources:
        for segment in parse_source_text(artifact).segments:
            registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
                content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id='gemini', model_id=MODEL,
        request_guard=guard, request_guard_id=GUARD, max_attempts=1, max_output_tokens=4096)
    service = TaskObservationDiscoveryService(extractor, source_registry=registry,
        current_source=lambda workspace, identity: current.get((workspace, identity)),
        limits=DiscoveryLimits(max_sources=3, max_calls=3), task_profile=task_profile)
    for meeting in MEETINGS:
        selected = [a for a in sources if a.workspace_id == meeting]
        for offset in range(0, len(selected), 3):
            if stopped():
                return
            result = service.discover(tuple(selected[offset:offset+3]), workspace_id=meeting)
            on_batch(dict(meeting_id=meeting, batch_start=offset, result=result.to_dict()))
