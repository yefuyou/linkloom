"""Complete verification of the already successful LIVE Product, Provider-free."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys

sys.path[:0] = ['src', '.', 'scripts']
from run_m3d_live import ROOT, DOC, read, digest, save, public, blocked, cold
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.extraction import _recall_schema
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from tests.m3p_support import product_http
from playwright.sync_api import sync_playwright


def main():
    assert not (DOC/'evidence/live-saved-verification.json').exists(), 'SAVED_VERIFICATION_RECEIPT_EXISTS; never overwrite'
    os.environ.update(GEMINI_API_KEY='', DEEPSEEK_API_KEY='', PYTHONIOENCODING='utf8')
    original = read(ROOT/'result.json')
    result, case = original['result'], original['case']
    run, ws = original['run_id'], original['workspace_id']
    assert result['status']=='PARTIAL' and result['reviewer']['accepted']
    budget = read(ROOT/'global-provider/budget.json')
    assert budget['failure'] is None and all(a['outcome']=='SUCCEEDED' for a in budget['attempts'])
    before = digest(ROOT/'global-provider/budget.json')
    app = ProductApplication(database_path=ROOT/'product.sqlite', extractor=None)
    report = dict(mode='PROVIDER_FREE_VERIFICATION_OF_SAVED_REAL_F_G_RUNTIME_PRODUCT',
        run_id=run, workspace_id=ws, original_harness_acceptance=original['acceptance'],
        original_harness_failure=original.get('failure'), additional_real_requests=0)
    pictures = Path('output/playwright/m3d-live-saved')
    pictures.mkdir(parents=True, exist_ok=True)
    try:
        source, = app.snapshot_investigation_sources(ws)[1]
        artifact = RawArtifact(ws, source['source_id'], 'timestamped_text', source['content'],
            source_uri=source['source_uri'],
            ingestion_time=datetime.fromisoformat(source['version_created_at'].replace('Z','+00:00')))
        assert source['content']==case['source']
        prep = original['preparation']
        observations = prep['discovery']['valid_observations']+prep['discovery']['partial_observations']
        assert len(observations)==2 and all(TaskObservation.from_dict(row).verify(artifact) for row in observations)
        known = next(row for row in observations if '兼容矩阵核验' in row['description'])
        unknown = next(row for row in observations if '索引重建' in row['description'])
        assert known['owner']=='乔雨' and known['deadline']=='2037-08-19' and known['deadline_text']=='2037-08-19'
        assert unknown['owner'] is None and unknown['deadline_text'] is None
        assert set(unknown['unknown_fields'])=={'owner','deadline','project','vendor'}
        f_request, = [read(path) for path in (ROOT/'requests').glob('*.json') if read(path)['agent_id']=='semantic-extractor-v1']
        assert 'task-recall/v3' in f_request['user_input']
        assert canonical_hash(f_request['available_tools'][0]['input_schema'])==canonical_hash(_recall_schema())
        report.update(source_verified_observations=observations, f_v3_schema_sha256=canonical_hash(_recall_schema()),
            canonical_source=dict(source_uri=artifact.source_uri, ingestion_time=artifact.ingestion_time.isoformat(),
                artifact_version_id=artifact.artifact_version_id, content_hash=artifact.content_hash),
            live_replan_hit=len(prep['planning'])==2, live_replan_claimed=False,
            no_p4_promotion=not result['conclusions'] and all(not p.get('publishable') for p in result['task_review']['proposals']))
        assert report['no_p4_promotion']
        assert {r['tool'] for r in result['task_review']['executed_tools']}=={'inspect_task_context','query_task_relations'}
        for path in (ROOT/'wire').glob('*.json'):
            wire = read(path)
            assert wire['model']=='gemini-3.8-flash' and wire['config']['thinking_config']['thinking_level']=='LOW'
            assert wire['sdk_attempts']==1
        code, fresh = cold(ROOT/'product.sqlite',run,ws)
        assert code==200 and fresh['result']['task_review']['observations']==result['task_review']['observations']
        report['fresh_process_cold']=dict(code=code,body=fresh)
        with product_http(app, ProductInvestigationService(app)) as base, sync_playwright() as p:
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
            page = browser.new_page(viewport=dict(width=1440,height=1100))
            errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base,wait_until='domcontentloaded');page.locator('#source-create').wait_for()
            page.evaluate('([key,value])=>sessionStorage.setItem(key,value)',[f'linkloom:last-task:{ws}',run])
            page.locator('[data-screen="ask"]').click();page.locator('[data-action="reopen-task"]').click()
            page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            summaries=page.locator('#ask-result summary').filter(has_text='展开原始消息与字段证据')
            for i in range(summaries.count()): summaries.nth(i).click()
            report['fields_text']=page.locator('#ask-result').inner_text()
            assert 'UNKNOWN' in report['fields_text'] and '2037-08-19' in report['fields_text']
            page.screenshot(path=str(pictures/'fields.png'),full_page=True)
            page.locator('#ask-result [data-action="inspect-source"]').first.click();page.locator('#source-update').wait_for()
            assert page.locator('#update-content').input_value()==case['source']
            report['source_opened_exact']=True
            page.screenshot(path=str(pictures/'source.png'),full_page=True)
            page.reload(wait_until='domcontentloaded');page.locator('#source-create').wait_for()
            page.locator('[data-screen="ask"]').click();page.locator('[data-action="reopen-task"]').click()
            page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            report['refresh_verified']=True
            with app.repository._lock,app.repository._connection:
                row=dict(app.repository._connection.execute('SELECT * FROM ll_source_evidence WHERE workspace_id=? LIMIT 1',(ws,)).fetchone())
                app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',(ws,row['evidence_ref']))
            hot=page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}').json();blocked(hot)
            code,revoked=cold(ROOT/'product.sqlite',run,ws);assert code==200;blocked(revoked)
            report['revoked_hot']=hot;report['revoked_cold']=revoked
            with app.repository._lock,app.repository._connection:
                columns=','.join(row);marks=','.join('?' for _ in row)
                app.repository._connection.execute(f'INSERT INTO ll_source_evidence ({columns}) VALUES ({marks})',tuple(row.values()))
            other=app.repository.create_workspace('Integration D unrelated synthetic workspace')
            app.repository.select_workspace(other['workspace_id'])
            response=page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}')
            code,_=cold(ROOT/'product.sqlite',run,ws)
            assert response.status==code==409
            report['cross_workspace_hot_status']=response.status;report['cross_workspace_cold_status']=code
            app.repository.select_workspace(ws)
            changed=page.request.put(base+'/api/sources/'+source['source_id'],data=dict(name=source['name'],content=source['content']+'\nSynthetic new version.'))
            assert changed.status==202
            hot=page.request.get(base+f'/api/product/ask/runs/{run}?workspace_id={ws}').json();blocked(hot)
            code,updated=cold(ROOT/'product.sqlite',run,ws);assert code==200;blocked(updated)
            report['updated_hot']=hot;report['updated_cold']=updated
            report.update(browser=browser.version,browser_errors=errors)
            assert not errors
            browser.close()
        report.update(acceptance='PASS',provider_journal_sha256_before=before,
            provider_journal_sha256_after=digest(ROOT/'global-provider/budget.json'))
        assert before==report['provider_journal_sha256_after']
    finally:
        app.close()
        save(DOC/'evidence/live-saved-verification.json',public(report))
    print('SAVED_REAL_PRODUCT_SOURCE_BROWSER_COLD_REVOKE_UPDATE_WORKSPACE_PASS; ADDITIONAL_REAL_REQUESTS=0',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf8')
    main()
