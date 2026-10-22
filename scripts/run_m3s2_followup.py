"""Explicit user-directed supplementary campaign; never loops/retries failures."""
from datetime import UTC, datetime
import os
from pathlib import Path
import sys

sys.path[:0] = ['src', '.', 'scripts']
from run_m3s2_acceptance import DOC, read, save, digest, budget_snapshot, evaluate
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client, GEMINI_38_FLASH_MODEL_ID
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, TaskDiscoveryProfile
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService
from linkloom.ui.task_provider import TaskProviderSession, TaskProviderLimits

ROOT = Path('work/m3s2-followup')
INITIAL_LEDGERS = [Path(p) for p in (
    'work/m3s2-live/provider/budget.json', 'work/m3s2-unattempted/provider/budget.json',
    'work/m3s2-unattempted-2/provider/budget.json', 'work/m3s2-product/campaign-provider/budget.json')]


def reserve():
    assert not ROOT.exists(), 'SINGLE_USE_FOLLOWUP; no automatic rerun'
    snapshot = budget_snapshot()
    ledgers = [read(p) for p in INITIAL_LEDGERS]
    assert not any(a['outcome'] in {'PENDING', 'UNCERTAIN'} for x in ledgers for a in x['attempts'])
    requests = sum(x['requests_reserved'] for x in ledgers)
    cost = sum(x['cost_reserved_usd'] for x in ledgers)
    assert snapshot['prior_plus_s1_full_allocation_usd']+cost+2 <= 50
    reservation = dict(authorization='User continuation after initial M3-S2 delivery; explicit new bounded verification',
        started_at=datetime.now(UTC).isoformat(), snapshot=snapshot,
        initial_m3s2_requests=requests, initial_m3s2_reserved_usd=cost,
        cumulative_prior_requests=snapshot['prior']['requests']+requests,
        cumulative_prior_reserved_usd=snapshot['prior']['reserved_usd']+cost,
        prior_ledgers={str(p):digest(p) for p in INITIAL_LEDGERS},
        fixed_cases_sha256=digest(DOC/'cases.json'), max_requests=8, max_reserved_usd=2,
        project_cap_usd=50, sdk_attempts=1, model=GEMINI_38_FLASH_MODEL_ID,
        automatic_retries=0, actual_bill_usd=None)
    save(ROOT/'reservation.json', reservation)
    return reservation


def fields(*, unattempted_v3=False):
    if unattempted_v3:
        reservation = read(ROOT/'reservation.json')
        prior = read(ROOT/'f-provider/budget.json')
        assert prior['requests_reserved'] == 1 and prior['failure'] == 'MODEL_UNAVAILABLE'
        assert prior['attempts'][0]['outcome'] == 'FAILED'
        assert not (ROOT/'TASK_RECALL_V3.json').exists()
        phase = ROOT/'v3-unattempted'
        assert not phase.exists(), 'SINGLE_USE_V3_EXPERIMENT; no retry'
    else:
        reservation = reserve()
        phase = ROOT
    sdk = build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],
        http_options_kwargs=dict(timeout=45000, retry_options=dict(attempts=1)))
    class Bridge:
        def generate_content(self, **kwargs):
            return sdk.models.generate_content(**kwargs)
    real = GeminiProviderAdapter(Bridge(), model_id=GEMINI_38_FLASH_MODEL_ID)
    session = TaskProviderSession(phase/'f-provider', TaskProviderLimits(1 if unattempted_v3 else 2,
        250000, 8192, .25 if unattempted_v3 else .5, 150, 50, .25), lambda: None)
    class Campaign(GeminiProviderAdapter):
        def complete(self, request):
            save(phase/f'f-requests/{session.requests+1:02}.json', request.to_dict())
            response = session.complete(real, request.agent_id, request)
            save(phase/f'f-responses/{session.requests:02}.json', response.to_dict())
            return response
    provider = Campaign(Bridge(), model_id=GEMINI_38_FLASH_MODEL_ID)
    def guard(request):
        return ProviderRequestAuthorization.approve(request, guard_id='m3s2-followup',
            reservation_id='m3s2-user-continuation', estimated_cost_upper_bound_usd=.25)
    cases = read(DOC/'cases.json')['cases'][:4]
    source = '\n'.join(f'[2030-01-0{i+1}T09:00:00Z] {c["speaker"]}: {c["text"]}' for i,c in enumerate(cases))
    try:
        profiles = (TaskDiscoveryProfile.TASK_RECALL_V3,) if unattempted_v3 else (TaskDiscoveryProfile.FIELD_GROUNDED_V2, TaskDiscoveryProfile.TASK_RECALL_V3)
        for profile in profiles:
            assert digest(DOC/'cases.json') == reservation['fixed_cases_sha256']
            artifact = RawArtifact('m3s2-followup-public', 'N01-N04.txt', 'timestamped_text', source)
            registry = SourceReferenceRegistry()
            for segment in parse_source_text(artifact).segments:
                registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
                    content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
            extractor = ProviderNeutralSemanticExtractor(provider, provider_id='gemini', model_id=GEMINI_38_FLASH_MODEL_ID,
                request_guard=guard, request_guard_id='m3s2-followup', max_attempts=1, max_output_tokens=4096)
            result = TaskObservationDiscoveryService(extractor, source_registry=registry,
                current_source=lambda ws, aid: artifact, task_profile=profile).discover((artifact,), workspace_id=artifact.workspace_id)
            observations = (*result.valid_observations, *result.partial_observations)
            assert all(o.verify(artifact) for o in observations)
            row = dict(profile=profile.value, cases=[c['id'] for c in cases], source=source,
                discovery=result.to_dict(), evaluation=evaluate(cases, observations))
            save(ROOT/(profile.name+'.json'), row)
            print(profile.value, row['evaluation']['totals'], flush=True)
            if session.failure or any(e.failure_code for e in result.extractions):
                raise RuntimeError('STOP_FOLLOWUP_F_NO_RETRY: '+str(session.failure or 'EXTRACTION_FAILURE'))
    finally:
        sdk.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8')
    if sys.argv[1:] == ['fields']:
        fields()
    elif sys.argv[1:] == ['unattempted-v3']:
        fields(unattempted_v3=True)
    elif sys.argv[1:] == ['product']:
        from run_m3s2_product import main
        main(followup=True)
    else:
        raise SystemExit('Choose fields or product; each is single-use.')
