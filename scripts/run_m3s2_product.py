"""Single real Product request, followed by provider-free cold reads."""
import json
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path[:0] = ['src', '.', 'scripts']
from run_m3s2_acceptance import DOC, OUT, read, save, digest, budget_snapshot
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client, GEMINI_38_FLASH_MODEL_ID
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.extraction import _recall_schema
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_provider import TaskProviderSession, TaskProviderLimits, compose_task_provider
from tests.m3p_support import product_http

ROOT = Path('work/m3s2-product')


def _client(base, path, *, method='GET', data=None):
    body = json.dumps(data).encode('utf8') if data is not None else None
    request = Request(base+path, data=body, method=method, headers={'Content-Type':'application/json'})
    try:
        response = urlopen(request, timeout=180)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode('utf8'))


def cold(database, run, ws):
    env = dict(os.environ, PYTHONPATH='src;.', PYTHONIOENCODING='utf8', GEMINI_API_KEY='', DEEPSEEK_API_KEY='')
    result = subprocess.run([sys.executable, 'tests/m3p_support.py', str(database), run, ws],
        capture_output=True, text=True, encoding='utf8', env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def main(*, replay=False, followup=False):
    global ROOT
    live_root = ROOT
    if replay:
        ROOT = Path('work/m3s2-product-replay')
    elif followup:
        ROOT = Path('work/m3s2-followup/product')
    assert not ROOT.exists(), 'SINGLE_USE_PRODUCT_RUN; no retries'
    ledgers = [OUT/'provider/budget.json', Path('work/m3s2-unattempted/provider/budget.json'),
               Path('work/m3s2-unattempted-2/provider/budget.json')]
    previous = [read(p) for p in ledgers if p.exists()]
    assert sum(p['requests_reserved'] for p in previous) <= 14
    assert not any(a['outcome'] in {'PENDING', 'UNCERTAIN'} for p in previous for a in p['attempts'])
    assert sum(p['cost_reserved_usd'] for p in previous)+1.5 <= 5
    snap = budget_snapshot()
    if followup:
        reservation = read('work/m3s2-followup/reservation.json')
        assert reservation['max_requests'] == 8 and reservation['max_reserved_usd'] == 2
        assert digest(DOC/'cases.json') == reservation['fixed_cases_sha256']
        f_ledgers = [Path('work/m3s2-followup/f-provider/budget.json'),
                     Path('work/m3s2-followup/v3-unattempted/f-provider/budget.json')]
        f_budgets = [read(p) for p in f_ledgers if p.exists()]
        assert sum(b['requests_reserved'] for b in f_budgets) <= 2
        assert sum(b['cost_reserved_usd'] for b in f_budgets) <= .5
        assert not any(a['outcome'] in {'PENDING', 'UNCERTAIN'} for b in f_budgets for a in b['attempts'])
        assert snap['prior_plus_s1_full_allocation_usd']+reservation['initial_m3s2_reserved_usd']+2 <= 50
    else:
        assert snap['prior_plus_s1_full_allocation_usd']+5 <= 50
    cases = {c['id']: c for c in read(DOC/'cases.json')['cases']}
    scene = dict(source='\n'.join(f'[2030-01-0{i+1}T09:00:00Z] {cases[k]["speaker"]}: {cases[k]["text"]}' for i,k in enumerate(['N01','N07'])),
        question='请列出原文中的离线索引重建和兼容矩阵核验两条任务观察，各自的负责人、交付日期及证据是什么？未确定的字段请明确写未知。')
    save(ROOT/'reservation.json', dict(**snap, prior_f_ledger_sha256=digest(OUT/'provider/budget.json'),
        max_requests=6, max_reserved_usd=1.5,
        combined_m3s2_max_requests=reservation['initial_m3s2_requests']+8 if followup else 20,
        combined_m3s2_max_reserved_usd=reservation['initial_m3s2_reserved_usd']+2 if followup else 5, scene=scene,
        followup_reservation_sha256=digest('work/m3s2-followup/reservation.json') if followup else None))
    sdk = None if replay else build_gemini_sdk_client(os.environ['GEMINI_API_KEY'], http_options_kwargs=dict(timeout=45000, retry_options=dict(attempts=1)))
    class Bridge:
        def generate_content(self, **kwargs):
            return sdk.models.generate_content(**kwargs)
    if replay:
        from linkloom.agents.model_adapter import ModelResponse, ModelAction
        from linkloom.tools.contracts import ToolCall
        from tests.integration.test_m3q_provider_host import ScriptedGeminiTransport
        from tests.m3p_support import ScriptedStage
        class RecordedStage:
            def __init__(self, stage, sequence):
                self.stage, self.sequence = stage, sequence
            def complete(self, request):
                response = read(live_root/f'responses/{self.sequence:02}.json')
                arguments = response['proposal']['tool_calls'][0]['runtime_call']['arguments']
                return ModelResponse(action=ModelAction.tool(ToolCall('recorded', request.available_tools[0].tool_id, arguments)))
        transport = ScriptedGeminiTransport([RecordedStage('discovery', 1), RecordedStage('planning', 2), ScriptedStage('semantics')])
        real = GeminiProviderAdapter(transport, model_id=GEMINI_38_FLASH_MODEL_ID)
    else:
        real = GeminiProviderAdapter(Bridge(), model_id=GEMINI_38_FLASH_MODEL_ID)
    session = TaskProviderSession(ROOT/'campaign-provider', TaskProviderLimits(6, 1000000, 30000, 1.5, 300, 50, .25), lambda: None)
    class Campaign(GeminiProviderAdapter):
        def complete(self, request):
            save(ROOT/f'requests/{session.requests+1:02}.json', request.to_dict())
            response = session.complete(real, request.agent_id, request)
            save(ROOT/f'responses/{session.requests:02}.json', response.to_dict())
            return response
    provider = Campaign(Bridge(), model_id=GEMINI_38_FLASH_MODEL_ID)
    database = ROOT/'product.sqlite'
    app = ProductApplication(database_path=database, extractor=None)
    report = dict(scene=scene, database=str(database.resolve()),
        mode='OFFLINE_REPLAY_OF_REAL_F_G; SCRIPTED_RUNTIME' if replay else 'REAL_PROVIDER',
        new_billable_requests=0 if replay else None)
    if replay:
        assert scene == read(live_root/'reservation.json')['scene']
        report['original_response_hashes'] = {str(live_root/f'responses/{i:02}.json'): digest(live_root/f'responses/{i:02}.json') for i in (1,2)}
    try:
        workspace = app.repository.create_workspace('M3-S2 public task recall')
        ws = workspace['workspace_id']; app.repository.select_workspace(ws)
        source_id = 'src_'+uuid4().hex
        artifact = RawArtifact(ws, source_id, 'timestamped_text', scene['source'], source_uri='product://'+source_id)
        app.repository.create_source(ws, source_id, 'm3s2-meeting.txt', artifact,
            tuple(s.evidence_span for s in parse_source_text(artifact).segments))
        app.repository.set_ingestion_result(ws, source_id, artifact.artifact_version_id,
            status='COMPLETE', candidate_count=0, review_required_count=0)
        service = compose_task_provider(app, provider, TaskProviderLimits(6, 1000000, 30000, 1.5, 280, 50, .25))
        with product_http(app, service) as base:
            code, config = _client(base, '/api/product/ask/config')
            payload = dict(query=scene['question'], workspace_id=ws,
                investigation_context=dict(domain='tasks', snapshot_hash=config['investigation_context']['snapshot_hash']))
            code, denied = _client(base, '/api/product/ask', method='POST', data=payload)
            assert session.requests == 0
            report['without_permission'] = dict(code=code, result=denied)
            payload['allow_external_provider'] = True
            code, body = _client(base, '/api/product/ask', method='POST', data=payload)
            result = body['result']; end = monotonic()+310
            while result['status'] == 'INVESTIGATING' and monotonic() < end:
                sleep(.15)
                code, body = _client(base, f'/api/product/ask/runs/{result["agent_run_id"]}?workspace_id={ws}')
                result = body['result']
            report.update(workspace_id=ws, run_id=result['agent_run_id'], result=result,
                source=dict(version=artifact.artifact_version_id, hash=artifact.content_hash, source_id=source_id))
            save(ROOT/'product-result.json', report)
            print('PRODUCT', result['status'], 'requests', session.requests, flush=True)
            assert not session.failure, session.failure
            f_requests = [read(p) for p in (ROOT/'requests').glob('*.json') if read(p)['agent_id'] == 'semantic-extractor-v1']
            assert len(f_requests) == 1
            assert 'task-recall/v3' in f_requests[0]['user_input']
            assert canonical_hash(f_requests[0]['available_tools'][0]['input_schema']) == canonical_hash(_recall_schema())
            report['product_f_schema_sha256'] = canonical_hash(_recall_schema())
            discovery = read(next((ROOT/'work/investigations').glob('*/preparation/preparation.json')))['discovery']
            raw_observations = discovery['valid_observations'] + discovery['partial_observations']
            for data in raw_observations:
                observation = TaskObservation.from_dict(data)
                assert observation.verify(artifact)
                assert observation.provenance.workspace_id == ws and observation.provenance.artifact_version_id == artifact.artifact_version_id
                assert observation.provenance.content_hash == artifact.content_hash
            report['verified_source_observations'] = raw_observations
            report['canonical_speakers'] = [s.speaker for s in parse_source_text(artifact).segments]
            assert result.get('task_review'), result
            observations = result['task_review']['observations']
            assert len(observations) == 2, observations
            text = json.dumps(observations, ensure_ascii=False)
            assert '离线索引重建' in text and '兼容矩阵核验' in text and '2036-04-17' in text
            before = digest(ROOT/'campaign-provider/budget.json')
            status, restored = cold(database, result['agent_run_id'], ws)
            report['cold_read'] = dict(code=status, result=restored)
            assert status == 200 and restored['result']['task_review']['observations'] == observations
            other = app.repository.create_workspace('M3-S2 unrelated synthetic workspace')['workspace_id']
            app.repository.select_workspace(other)
            status, denied = _client(base, f'/api/product/ask/runs/{result["agent_run_id"]}?workspace_id={ws}')
            assert status == 409
            report['cross_workspace_status'] = status
            app.repository.select_workspace(ws)
            # Update only this disposable synthetic source; old saved result must fail closed.
            updated = replace(artifact, content=scene['source']+'\nSynthetic source version changed.')
            app.repository.update_source(ws, source_id, 'm3s2-meeting.txt', updated,
                tuple(s.evidence_span for s in parse_source_text(updated).segments))
            status, changed = cold(database, result['agent_run_id'], ws)
            assert status == 200 and changed['result']['status'] == 'BLOCKED'
            assert not changed['result']['evidence'] and not changed['result']['trace'] and not changed['result'].get('task_review')
            report['changed_source_cold_read'] = dict(code=status, result=changed)
            assert before == digest(ROOT/'campaign-provider/budget.json')
            report['cold_revalidation_additional_provider_calls'] = 0
    finally:
        save(ROOT/'product-result.json', report)
        app.close()
        if sdk is not None:
            sdk.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8')
    main(replay=sys.argv[1:] == ['replay'])
