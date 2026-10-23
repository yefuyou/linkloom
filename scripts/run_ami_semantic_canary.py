"""One bounded AMI canary using existing F/Discovery/Provider contracts.

This process never imports a scorer or reads queries, summaries or qrels.
Without --execute it only prepares requests; no SDK client is constructed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess

from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_observation_discovery import DiscoveryLimits, TaskObservationDiscoveryService
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession

MEETINGS = ('ES2002a', 'ES2005c')
MODEL = 'gemini-3.8-flash'
GUARD = 'ami-explicit-two-meeting-canary/v1'
LIMITS = TaskProviderLimits(28, 1_400_000, 114_688, 2.80, 2700, 90, .10,
                            input_usd_per_million=1, output_usd_per_million=5)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def append(path, value):
    with path.open('a', encoding='utf8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()


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
        if ''.join(c['text'] for c in chunks) != transcript['canonical_text']:
            raise ValueError('incomplete transcript chunks')
        for index, chunk in enumerate(chunks):
            text = chunk['text']
            if (transcript['canonical_text'][chunk['start']:chunk['end']] != text
                    or digest(text.encode('utf8')) != chunk['sha256']
                    or len(text) > 8000 or len(text.encode('utf8')) > 16000):
                raise ValueError('chunk hash/span/budget mismatch')
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
    if len(sources) != LIMITS.max_requests:
        raise ValueError('unexpected canary source population')
    return sources, sidecars


def extract_sources(sources, provider, guard, on_batch, stopped=lambda: False):
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
        limits=DiscoveryLimits(max_sources=3, max_calls=3))
    for meeting in MEETINGS:
        selected = [a for a in sources if a.workspace_id == meeting]
        for offset in range(0, len(selected), 3):
            if stopped():
                return
            result = service.discover(tuple(selected[offset:offset+3]), workspace_id=meeting)
            on_batch(dict(meeting_id=meeting, batch_start=offset, result=result.to_dict()))


def run(inputs, output, *, execute=False):
    if output.exists():
        raise FileExistsError('fresh output directory required; never resume a paid canary')
    output.mkdir(parents=True)
    sources, sidecars = load_sources(inputs)
    save(output / 'sources.json', [dict(artifact_id=a.artifact_id, workspace_id=a.workspace_id,
        artifact_version_id=a.artifact_version_id, content_hash=a.content_hash, content=a.content,
        source_ref=a.source_ref) for a in sources])
    save(output / 'native_sidecars.json', sidecars)
    requests = []

    def plan(request):
        requests.append(request.to_dict())
        raise PermissionError('offline preparation: no provider authorization')

    class NeverCalled:
        def complete(self, request):
            raise AssertionError('offline preparation must never invoke a provider')

    extract_sources(sources, NeverCalled(), plan, lambda row: None)
    sizes = [len(json.dumps(r, ensure_ascii=False).encode('utf8')) + 4096 for r in requests]
    reserves = [max(.10, (s + 4096 * 5) / 1_000_000) for s in sizes]
    if (len(requests) != 28 or sum(sizes) > LIMITS.max_input_tokens
            or sum(reserves) > LIMITS.max_cost_usd + 1e-10 or any(c > .10 for c in reserves)):
        raise ValueError('planned requests exceed existing global budget')
    save(output / 'planned_requests.json', requests)
    save(output / 'plan.json', dict(model=MODEL, meetings=MEETINGS, requests=len(requests),
        limits=asdict(LIMITS), input_tokens_upper_bound=sum(sizes),
        estimated_cost_upper_bound_usd=round(sum(reserves), 8),
        provider_attempts=1, sdk_attempts=1, task_profile=None,
        credit_verification='USER_CONFIRMED', product_e2e='NOT_RUN',
        git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()))
    if not execute:
        print('PREFLIGHT: 28 exact F requests, USD 2.80 upper reservation; zero Provider calls', flush=True)
        return

    import os
    sdk = build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],
        http_options_kwargs=dict(timeout=90000, retry_options=dict(attempts=1)))

    class RecordingClient:
        def generate_content(self, **kwargs):
            response = sdk.models.generate_content(**kwargs)
            append(output / 'sdk_raw_responses.jsonl', response.model_dump(mode='json'))
            return response

    adapter = GeminiProviderAdapter(RecordingClient(), model_id=MODEL)
    session = TaskProviderSession(output / 'provider', LIMITS, lambda: None)

    class RecordingAdapter:
        def complete(self, request):
            actual = request.to_dict()
            if actual != requests[len(session.attempts)-1]:
                raise ValueError('live request differs from prepared transcript-only request')
            append(output / 'actual_requests.jsonl', actual)
            response = adapter.complete(request)
            append(output / 'model_responses.jsonl', response.to_dict())
            print(f'Provider {len(session.attempts)}/28: ' +
                  ('ERROR ' + response.error.code if response.error else 'received'), flush=True)
            return response

    def authorize(request):
        return ProviderRequestAuthorization.approve(request, guard_id=GUARD,
            reservation_id=digest(json.dumps(request.to_dict(), sort_keys=True).encode('utf8')),
            estimated_cost_upper_bound_usd=.10)

    extract_sources(sources, session.bind(RecordingAdapter(), 'F'), authorize,
        lambda row: append(output / 'discovery.jsonl', row), stopped=lambda: bool(session.failure))
    save(output / 'completion.json', dict(completed_at=datetime.now(UTC).isoformat(),
        provider_calls=session.requests, reserved_cost_usd=round(session.cost, 8),
        failure=session.failure, product_e2e='NOT_RUN'))
    save(output / 'output_seal.json', dict(sealed=True, files={
        str(p.relative_to(output)).replace('\\', '/'): digest(p.read_bytes())
        for p in sorted(output.rglob('*')) if p.is_file()}))
    print('Canary finished; no automatic additional batch.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    run(args.inputs, args.output, execute=args.execute)
