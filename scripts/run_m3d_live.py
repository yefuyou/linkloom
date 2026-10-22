"""One authorized, single-use combined Product LIVE; no retries or fallback."""
from datetime import UTC, datetime
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep

sys.path[:0] = ['src', '.', 'scripts']
from audit_m3d_budget import DOC, audit, read, digest
from run_m3s2_product import cold
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.extraction import _recall_schema
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession, compose_task_provider
from tests.m3p_support import product_http
from playwright.sync_api import sync_playwright

ROOT = Path('work/m3d-live')  # Keep durable Runtime paths below Windows MAX_PATH.


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def public(value):
    if isinstance(value, dict):
        return {k:public(v) for k,v in value.items() if k not in {
            'provider_turn_continuation', 'thought_signature', 'thoughtSignature'}}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def blocked(body):
    result = body['result']
    assert result['status']=='BLOCKED' and not result['evidence'] and not result['trace']
    assert not result.get('task_review')


def main():
    assert not ROOT.exists(), 'SINGLE_USE_LIVE; EXISTING_RUN_IS_NEVER_RETRIED'
    assert os.environ.get('GEMINI_API_KEY'), 'GEMINI_API_KEY_UNSET; NO_PAID_REQUEST'
    assert os.environ.get('GOOGLE_GENAI_USE_VERTEXAI','').lower() not in {'1','true'}
    assert version('google-genai') == '2.22.0'
    regression = read(DOC/'evidence/regression-final.json')
    assert regression['final_fail']==regression['final_skip']==0
    assert regression['unique_cases']==regression['final_pass']
    assert read(DOC/'evidence/offline-browser.json')['refresh_verified']
    for name in ('A','B','C'):
        assert read(DOC/f'evidence/offline-{name}.json')['result']['status']=='PARTIAL'
    case_path = DOC/'LIVE_CASE.json'
    case = read(case_path)
    snapshot = audit()  # fresh current journals immediately before campaign allocation
    limits = TaskProviderLimits(12, 1000000, 65536, 3, 600, 65, .25)
    reservation = dict(authorization='Explicit Integration D user task; one new synthetic combined acceptance',
        started_at=datetime.now(UTC).isoformat(), case_sha256=digest(case_path), snapshot=snapshot,
        new_max_requests=12, new_max_reserved_usd=3, project_max_usd=50,
        model='gemini-3.8-flash', sdk=version('google-genai'), thinking_level='low', sdk_attempts=1,
        failed_or_uncertain_automatic_retries=0, historical_reservations_retained=True,
        actual_bill_usd=None, billing_project_verified=False)
    save(ROOT/'reservation.json', reservation)
    campaign = TaskProviderSession(ROOT/'global-provider', limits, lambda: None)
    sdk = build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],
        http_options_kwargs=dict(timeout=60000, retry_options=dict(attempts=1)))

    class Bridge:
        def generate_content(self, *, model, contents, config):
            assert model == 'gemini-3.8-flash'
            # Runtime's generic turn options omit thinking_level. Apply the
            # user's explicit transport setting to EVERY actual SDK request.
            config = dict(config, thinking_config=dict(thinking_level='LOW'))
            save(ROOT/f'wire/{campaign.requests:02}.json', dict(model=model, contents=contents, config=config,
                sdk_attempts=1, timeout_ms=60000, reservation_sha256=digest(ROOT/'global-provider/budget.json')))
            assert read(ROOT/'global-provider/budget.json')['attempts'][-1]['outcome']=='PENDING'
            return sdk.models.generate_content(model=model, contents=contents, config=config)

    real = GeminiProviderAdapter(Bridge(), model_id='gemini-3.8-flash')

    class Campaign(GeminiProviderAdapter):
        def complete(self, request):
            assert digest(case_path) == reservation['case_sha256']
            assert all(digest(row['path'])==row['sha256'] for row in snapshot['current_journals'])
            sequence = campaign.requests+1
            save(ROOT/f'requests/{sequence:02}.json', request.to_dict())
            response = campaign.complete(real, request.agent_id, request)
            save(ROOT/f'responses/{sequence:02}.json', response.to_dict())
            print('LIVE_REQUEST', campaign.requests, request.agent_id,
                  campaign.attempts[-1]['outcome'] if campaign.attempts else campaign.failure, flush=True)
            return response

    provider = Campaign(Bridge(), model_id='gemini-3.8-flash')
    app = ProductApplication(database_path=ROOT/'product.sqlite', extractor=None)
    service = compose_task_provider(app, provider, limits)
    report = dict(mode='REAL_F_G_RUNTIME_PRODUCT_HTTP_AND_CHROMIUM', case=case,
        model='gemini-3.8-flash', sdk='2.22.0', thinking_level='low', sdk_attempts=1,
        status='STARTED', live_replan_claimed=False, actual_bill_usd=None)
    screenshots = Path('output/playwright/m3d-live')
    screenshots.mkdir(parents=True, exist_ok=True)
    try:
        with product_http(app, service) as base, sync_playwright() as p:
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            page = browser.new_page(viewport=dict(width=1440, height=1100))
            errors = []
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.goto(base, wait_until='domcontentloaded')
            page.locator('#workspace-name').fill('Integration D public synthetic combined A B')
            page.locator('#workspace-create button[type="submit"]').click()
            page.locator('#source-create').wait_for()
            page.locator('#source-name').fill('combined-A-B.txt')
            page.locator('#source-content').fill(case['source'])
            page.locator('#source-create button[type="submit"]').click()
            page.locator('#source-update').wait_for(timeout=30000)
            page.locator('[data-screen="ask"]').click()
            page.locator('#ask-query').fill(case['question'])
            page.locator('#ask-domain').select_option('tasks')
            page.locator('#ask-submit-button').click()
            page.locator('#ask-result h2').wait_for()
            assert campaign.requests==0, 'PROVIDER_CALLED_BEFORE_SOURCE_GRANT'
            report['requests_without_authorization']=0
            page.locator('#ask-external').check()
            with page.expect_response(lambda r:r.url.endswith('/api/product/ask') and r.request.method=='POST', timeout=240000) as pending:
                page.locator('#ask-submit-button').click()
            submission = pending.value.json()
            save(ROOT/'ask-submission.json', submission)
            result = submission['result']
            run, ws = result['agent_run_id'], result['workspace_id']
            end = monotonic()+400
            while result['status']=='INVESTIGATING' and monotonic()<end:
                sleep(.25)
                result = page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}').json()['result']
            report.update(result=result, run_id=run, workspace_id=ws,
                status=result['status'], browser=browser.version, browser_errors=errors)
            save(ROOT/'result.json', public(report))
            page.screenshot(path=str(screenshots/'terminal.png'), full_page=True)
            assert not campaign.failure, campaign.failure
            assert result['status']=='PARTIAL' and result['reviewer']['accepted'], result.get('error')
            prep_path, = (ROOT/'work/investigations').glob('*/preparation/preparation.json')
            prep = read(prep_path)
            report['preparation']=prep
            source, = app.snapshot_investigation_sources(ws)[1]
            artifact = RawArtifact(ws, source['source_id'], 'timestamped_text', case['source'],
                source_uri=source['source_uri'],
                ingestion_time=datetime.fromisoformat(source['version_created_at'].replace('Z','+00:00')))
            observations = prep['discovery']['valid_observations']+prep['discovery']['partial_observations']
            assert len(observations)==2, observations
            assert all(TaskObservation.from_dict(row).verify(artifact) for row in observations)
            known = next(row for row in observations if '兼容矩阵核验' in row['description'])
            unknown = next(row for row in observations if '索引重建' in row['description'])
            assert known['owner']=='乔雨' and known['deadline']=='2037-08-19'
            assert unknown['owner'] is None and unknown['deadline_text'] is None
            assert set(unknown['unknown_fields'])=={'owner','deadline','project','vendor'}
            f_request, = [read(path) for path in (ROOT/'requests').glob('*.json')
                if read(path)['agent_id']=='semantic-extractor-v1']
            assert 'task-recall/v3' in f_request['user_input']
            assert canonical_hash(f_request['available_tools'][0]['input_schema'])==canonical_hash(_recall_schema())
            report['f_v3_schema_sha256']=canonical_hash(_recall_schema())
            report['source_verified_observations']=observations
            report['live_replan_hit']=len(prep['planning'])==2
            report['live_replan_claimed']=report['live_replan_hit']
            report['audited_stages']={path.name:read(path) for path in prep_path.parent.glob('*.json') if path.name!='preparation.json'}
            assert not result['conclusions']
            assert all(not row.get('publishable') for row in result['task_review']['proposals'])
            page.locator('#ask-result').get_by_role('heading', name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            summaries=page.locator('#ask-result summary').filter(has_text='展开原始消息与字段证据')
            for i in range(summaries.count()): summaries.nth(i).click()
            report['fields_text']=page.locator('#ask-result').inner_text()
            assert 'UNKNOWN' in report['fields_text'] and '2037-08-19' in report['fields_text']
            page.screenshot(path=str(screenshots/'fields.png'), full_page=True)
            page.locator('#ask-result [data-action="inspect-source"]').first.click()
            page.locator('#source-update').wait_for()
            assert page.locator('#update-content').input_value()==case['source']
            report['source_opened_exact']=True
            page.screenshot(path=str(screenshots/'source.png'), full_page=True)
            before = digest(ROOT/'global-provider/budget.json')
            page.reload(wait_until='domcontentloaded'); page.locator('#source-create').wait_for()
            page.locator('[data-screen="ask"]').click(); page.locator('[data-action="reopen-task"]').click()
            page.locator('#ask-result').get_by_role('heading', name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            code, fresh = cold(ROOT/'product.sqlite', run, ws)
            assert code==200 and fresh['result']['task_review']['observations']==result['task_review']['observations']
            report['fresh_process_cold']=dict(code=code, body=fresh)
            # Revoke/restore only this disposable synthetic evidence row.
            with app.repository._lock, app.repository._connection:
                row=dict(app.repository._connection.execute('SELECT * FROM ll_source_evidence WHERE workspace_id=? LIMIT 1',(ws,)).fetchone())
                app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',(ws,row['evidence_ref']))
            hot=page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}').json(); blocked(hot)
            code, revoked=cold(ROOT/'product.sqlite',run,ws); assert code==200; blocked(revoked)
            report['revoked_hot']=hot; report['revoked_cold']=revoked
            with app.repository._lock, app.repository._connection:
                columns=','.join(row); marks=','.join('?' for _ in row)
                app.repository._connection.execute(f'INSERT INTO ll_source_evidence ({columns}) VALUES ({marks})',tuple(row.values()))
            other=app.repository.create_workspace('Integration D unrelated synthetic workspace')
            app.repository.select_workspace(other['workspace_id'])
            cross=page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}')
            assert cross.status==409
            report['cross_workspace_status']=cross.status
            code, cross_cold=cold(ROOT/'product.sqlite',run,ws); assert code==409
            report['cross_workspace_cold_status']=code
            app.repository.select_workspace(ws)
            update=page.request.put(base+'/api/sources/'+source['source_id'], data=dict(name=source['name'],content=case['source']+'\nSynthetic new version.'))
            assert update.status==202
            hot=page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}').json(); blocked(hot)
            code, changed=cold(ROOT/'product.sqlite',run,ws); assert code==200; blocked(changed)
            report['updated_hot']=hot; report['updated_cold']=changed
            assert before==digest(ROOT/'global-provider/budget.json')
            report['refresh_cold_safety_additional_provider_calls']=0
            report['provider_journal_sha256_before_safety']=before
            report['provider_journal_sha256_after_safety']=digest(ROOT/'global-provider/budget.json')
            assert not errors
            report['acceptance']='PASS'
            browser.close()
    except Exception as error:
        report['acceptance']='FAIL'
        report['failure']=type(error).__name__+': '+str(error)
        raise
    finally:
        app.close(); sdk.close()
        budget=read(ROOT/'global-provider/budget.json')
        report.update(new_requests=budget['requests_reserved'], new_reserved_usd=budget['cost_reserved_usd'],
            cumulative_requests=102+budget['requests_reserved'], cumulative_reserved_usd=round(27.68+budget['cost_reserved_usd'],8),
            budget=budget, automatic_retries=0)
        originals=[p for folder in ('requests','responses','wire') for p in (ROOT/folder).glob('*.json')]
        manifest=[dict(path=str(p), sha256=digest(p)) for p in sorted(originals)]
        save(ROOT/'result.json', public(report))
        save(DOC/'evidence/live-result.json', public(report))
        save(DOC/'evidence/live-reservation.json', reservation)
        save(DOC/'evidence/live-original-manifest.json',manifest)
        print('LIVE_FINAL',report.get('acceptance'),'REQUESTS',campaign.requests,'RESERVED',campaign.cost,
              'FAILURE',campaign.failure,flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf8')
    main()
