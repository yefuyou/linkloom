"""One bounded acceptance campaign using the existing F and Product seams."""
import json, os, sys, hashlib, threading, time
from dataclasses import replace
from pathlib import Path
from datetime import datetime, UTC
from types import SimpleNamespace
sys.path[:0]=['src','.']
sys.stdout.reconfigure(encoding='utf-8')
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client, GEMINI_38_FLASH_MODEL_ID
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, TaskDiscoveryProfile
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService, _project
from linkloom.ui.task_provider import TaskProviderSession, TaskProviderLimits, compose_task_provider
from linkloom.ui.product import ProductApplication
from linkloom.ui.demo import DemoCase, DemoRunBackend
from linkloom.ui.server import create_http_server
from playwright.sync_api import sync_playwright

out=Path('work/m3s-live-phase3-20261022')
assert not (out/'global-provider/budget.json').exists(), 'EXISTING_RECEIPTS_NO_AUTOMATIC_RERUN'
prior=json.loads(Path('work/m3s-live-20261022/global-provider/budget.json').read_text(encoding='utf8'))
second=json.loads(Path('work/m3s-live-phase2-20261022/global-provider/budget.json').read_text(encoding='utf8'))
assert prior['requests_reserved']==11 and second['requests_reserved']==7 and second['failure'] is None
assert prior['failure'] is None
for key in ('requests_reserved','input_tokens_reserved','output_tokens_reserved','cost_reserved_usd'):
    prior[key]+=second[key]
assert all(r['outcome']=='SUCCEEDED' for r in prior['attempts'])
out.mkdir(parents=True,exist_ok=True)
limits=TaskProviderLimits(25-prior['requests_reserved'],1500000-prior['input_tokens_reserved'],200000-prior['output_tokens_reserved'],8-prior['cost_reserved_usd'],700,35,.25)
reservation=dict(previous_campaign=['work/m3s-live-20261022/global-provider/budget.json','work/m3s-live-phase2-20261022/global-provider/budget.json'],
    prior_m3s_requests=18,prior_m3s_reserved_usd=4.5,new_m3s_requests_limit=25,new_m3s_reserved_limit_usd=8,
    authorization='User explicitly approved at most 25 requests; monetary caps unchanged',
    prior_project_requests=47,prior_project_reserved_usd_conservative=13.93,global_limit_usd=50,
    model=GEMINI_38_FLASH_MODEL_ID,sdk='google-genai 2.22.0',thinking_level='low',retry_attempts=1,
    billing_project_verified=False,actual_bill_usd=None,promotional_credit_deduction_usd=None,
    started_at=datetime.now(UTC).isoformat())
with (out/'global-reservation.json').open('w',encoding='utf8') as f:
    json.dump(reservation,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
assert os.environ.get('GEMINI_API_KEY'), 'GEMINI_API_KEY_UNSET'
assert os.environ.get('GOOGLE_GENAI_USE_VERTEXAI','').lower() not in {'1','true'},'UNEXPECTED_PROVIDER_IDENTITY'
sdk=build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],http_options_kwargs=dict(timeout=30000,retry_options=dict(attempts=1)))
class Bridge:
    def generate_content(self,*,model,contents,config):return sdk.models.generate_content(model=model,contents=contents,config=config)
real=GeminiProviderAdapter(Bridge(),model_id=GEMINI_38_FLASH_MODEL_ID)
campaign=TaskProviderSession(out/'global-provider',limits,lambda:None)
class CampaignProvider(GeminiProviderAdapter):
    def complete(self,request):return campaign.complete(real,request.agent_id,request)
provider=CampaignProvider(Bridge(),model_id=GEMINI_38_FLASH_MODEL_ID)
SCENES=[
 dict(id='clear',question='闻夏的上线核查后来调整了吗？现在记录的负责人和交付日期是什么？与原来差几天？',
    source='[2034-02-01T09:00:00Z] 纪要: 晓汀项目的上线核查交给闻夏来掌舵，交付锁定2034-02-16。\n[2034-02-03T09:00:00Z] 纪要: 上次的上线核查仍由闻夏接着做。交付这一栏现填2034-02-20，原来2034-02-16的那档安排取消；本条接续前场的核查工作。'),
 dict(id='ambiguous',question='页面巡检现在由谁执行，交付日期定了吗？两次会议的安排有什么变化？',
    source='[2034-03-05T09:00:00Z] 纪要: 棠原项目的页面巡检已经列入本轮工作，韩桥只负责报进度，实际执行人尚未定。2034-03-05是讨论会议日期，交付日期尚无约定。\n[2034-03-06T09:00:00Z] 纪要: 页面巡检这件事待确认由韩桥还是易秋执行；交付也有2034-03-12和2034-03-15两种说法，均未定案。只是讨论选项，巡检任务本身照常推进。')]
(out/'product-inputs-before-results.json').write_text(json.dumps(SCENES,ensure_ascii=False,indent=2),encoding='utf8')

def product_scene(scene):
    root=out/scene['id'];root.mkdir(exist_ok=True)
    app=ProductApplication(database_path=root/'product.sqlite',extractor=None)
    service=compose_task_provider(app,provider,TaskProviderLimits(12,500000,100000,3,240,35,.25))
    server=create_http_server(DemoRunBackend(DemoCase.load_mps_001()),product_app=app,investigation_service=service,host='127.0.0.1',port=0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}/'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
            page=browser.new_page(viewport=dict(width=1440,height=1100));errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            count=campaign.requests
            page.goto(base,wait_until='domcontentloaded');page.locator('#workspace-create').wait_for(timeout=15000)
            page.locator('#workspace-name').fill('M3-S public '+scene['id']);page.locator('#workspace-create button').click()
            page.locator('#source-create').wait_for();page.locator('#source-name').fill('meetings.txt');page.locator('#source-content').fill(scene['source'])
            page.locator('#source-create button[type="submit"]').click();page.locator('#source-update').wait_for(timeout=30000)
            page.locator('[data-screen="ask"]').click();page.locator('#ask-query').wait_for()
            page.locator('#ask-query').fill(scene['question']);page.locator('#ask-domain').select_option('tasks')
            page.locator('#ask-submit-button').click();page.locator('#ask-result h2').wait_for()
            assert campaign.requests==count,'UNAUTHORIZED_PROVIDER_CALL'
            page.locator('#ask-external').check()
            with page.expect_response(lambda response: response.url.endswith('/api/product/ask') and response.request.method=='POST',timeout=180000) as pending:
                page.locator('#ask-submit-button').click()
            submitted=pending.value.json()
            (root/'ask-submission.json').write_text(json.dumps(submitted,ensure_ascii=False,indent=2),encoding='utf8')
            submitted_result=submitted.get('result',{})
            if submitted_result.get('status')=='BLOCKED':
                page.screenshot(path=str(root/'blocked.png'),full_page=True)
                (root/'blocked.json').write_text(json.dumps(dict(submitted=submitted,browser_text=page.locator('#ask-result').inner_text()),ensure_ascii=False,indent=2),encoding='utf8')
                print('PRODUCT '+scene['id']+' BLOCKED calls='+str(campaign.requests-count),flush=True)
                return
            end=time.monotonic()+220
            while time.monotonic()<end:
                pointers=list((root/'work/investigations').glob('ui-*.json'))
                if pointers:
                    pointer=json.loads(pointers[-1].read_text(encoding='utf8'))
                    response=page.request.get(base+'api/product/ask/runs/'+pointer['run_id']+'?workspace_id='+pointer['workspace_id'])
                    result=response.json().get('result',{})
                    if result.get('status')!='INVESTIGATING':break
                page.wait_for_timeout(500)
            else:raise RuntimeError('PRODUCT_LOCAL_POLL_TIMEOUT')
            page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
            page.locator('#ask-result details').first.locator('summary').click()
            text=page.locator('#ask-result').inner_text()
            page.screenshot(path=str(root/'product-fields.png'),full_page=True)
            page.locator('#ask-result [data-action="inspect-source"]').first.click();page.locator('#source-update').wait_for(timeout=15000)
            source_text=page.locator('#page').inner_text()
            assert page.locator('#update-content').input_value()==scene['source']
            before=campaign.requests
            page.reload(wait_until='domcontentloaded');page.locator('#source-create').wait_for();page.locator('[data-screen="ask"]').click()
            page.locator('[data-action="reopen-task"]').wait_for();page.locator('[data-action="reopen-task"]').click()
            page.locator('[data-task-proposal-verdict]').first.wait_for(timeout=15000)
            assert campaign.requests==before,'REFRESH_CALLED_PROVIDER'
            row=dict(scene=scene['id'],run_id=pointer['run_id'],workspace_id=pointer['workspace_id'],database=str((root/'product.sqlite').resolve()),
                result=result,browser=browser.version,browser_errors=errors,result_text=text,source_opened=True,
                refresh_text=page.locator('#ask-result').inner_text(),requests_before_authorization=count,requests_after=count if campaign.requests==count else campaign.requests,
                requests_before_refresh=before,requests_after_refresh=campaign.requests)
            (root/'browser.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
            browser.close()
            print('PRODUCT '+scene['id']+' '+result.get('status','')+' calls='+str(campaign.requests-count),flush=True)
    finally:
        server.shutdown();server.server_close();app.close()

try:
    for scene in SCENES[1:]:
        if campaign.failure:break
        product_scene(scene)
finally:
    sdk.close()
    print('M3S_TOTAL_REQUESTS='+str(18+campaign.requests)+' RESERVED='+str(4.5+campaign.cost),flush=True)
