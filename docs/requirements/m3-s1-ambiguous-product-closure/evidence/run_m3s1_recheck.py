"""One fixed original-ambiguity acceptance run through normal Product HTTP/UI."""
import hashlib,json,os,sys,threading,time
from datetime import datetime,UTC
from importlib.metadata import version
from pathlib import Path
sys.path[:0]=['src','.'];sys.stdout.reconfigure(encoding='utf-8')
from linkloom.agents.providers import GeminiProviderAdapter,build_gemini_sdk_client
from linkloom.ui.task_provider import TaskProviderSession,TaskProviderLimits,compose_task_provider
from linkloom.ui.product import ProductApplication
from linkloom.ui.demo import DemoCase,DemoRunBackend
from linkloom.ui.server import create_http_server
from playwright.sync_api import sync_playwright

out=Path('work/m3s1-live-recheck-20261022');root=out/'product'
assert not (out/'global-provider/budget.json').exists(),'EXISTING_LIVE_RECEIPTS_NO_RETRY'
case_path=Path('docs/requirements/m3-s1-ambiguous-product-closure/LIVE_RECHECK.json')
case=json.loads(case_path.read_text(encoding='utf8'))
assert version('google-genai')=='2.22.0'
assert hashlib.sha256(case['source'].encode()).hexdigest()==case['source_sha256']
assert hashlib.sha256(case['question'].encode()).hexdigest()==case['question_sha256']
assert os.environ.get('GEMINI_API_KEY'),'GEMINI_API_KEY_UNSET'
assert os.environ.get('GOOGLE_GENAI_USE_VERTEXAI','').lower() not in {'1','true'},'UNEXPECTED_PROVIDER_IDENTITY'
prior_root=Path('../m3s/work');journals=[];requests=0;reserved=0
for name in ['m3s-live-20261022','m3s-live-phase2-20261022','m3s-live-phase3-20261022','m3s-live-phase4-20261022']:
    p=prior_root/name/'global-provider/budget.json';d=json.loads(p.read_text(encoding='utf8'))
    requests+=d['requests_reserved'];reserved+=d['cost_reserved_usd']
    journals.append(dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),requests=d['requests_reserved'],reserved_usd=d['cost_reserved_usd'],failure=d['failure']))
assert requests==22 and reserved==5.5
budget=case['budget'];assert 47+requests==budget['prior_requests']
assert 13.93+reserved==budget['prior_reserved_usd_conservative']
assert budget['prior_reserved_usd_conservative']+budget['new_max_reserved_usd']<=budget['project_max_usd']
phase1_path=Path('work/m3s1-live-20261022/global-provider/budget.json')
phase1=json.loads(phase1_path.read_text(encoding='utf8'))
assert phase1['requests_reserved']==3 and phase1['cost_reserved_usd']==.75 and phase1['failure'] is None
assert all(r['outcome']=='SUCCEEDED' for r in phase1['attempts'])
root.mkdir(parents=True)
reservation=dict(case_sha256=hashlib.sha256(case_path.read_bytes()).hexdigest(),started_at=datetime.now(UTC).isoformat(),
    prior_requests=69,prior_reserved_usd_conservative=19.43,new_max_requests=12,new_max_reserved_usd=3,previous_attempt_requests=3,previous_attempt_reserved_usd=.75,previous_attempt_journal_sha256=hashlib.sha256(phase1_path.read_bytes()).hexdigest(),project_max_usd=50,
    previous_unknown_reservation_retained=True,authorization='Explicit new M3-S1 user task; not an automatic retry of M3-S UNCERTAIN',
    prior_journals=journals,model='gemini-3.8-flash',sdk=version('google-genai'),thinking_level='low',sdk_attempts=1,
    billing_project_verified=False,actual_bill_usd=None,promotional_credit_deduction_usd=None)
with (out/'global-reservation.json').open('w',encoding='utf8') as f:
    json.dump(reservation,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
limits=TaskProviderLimits(12-phase1['requests_reserved'],1000000-phase1['input_tokens_reserved'],65536-phase1['output_tokens_reserved'],3-phase1['cost_reserved_usd'],600-phase1['elapsed_seconds'],65,.25)
campaign=TaskProviderSession(out/'global-provider',limits,lambda:None)
sdk=build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],http_options_kwargs=dict(timeout=60000,retry_options=dict(attempts=1)))
class Bridge:
    def generate_content(self,*,model,contents,config):return sdk.models.generate_content(model=model,contents=contents,config=config)
real=GeminiProviderAdapter(Bridge(),model_id='gemini-3.8-flash')
class CampaignProvider(GeminiProviderAdapter):
    def complete(self,request):return campaign.complete(real,request.agent_id,request)
provider=CampaignProvider(Bridge(),model_id='gemini-3.8-flash')
app=ProductApplication(database_path=root/'product.sqlite',extractor=None)
service=compose_task_provider(app,provider,limits)
server=create_http_server(DemoRunBackend(DemoCase.load_mps_001()),product_app=app,investigation_service=service,host='127.0.0.1',port=0)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}/'
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        page=browser.new_page(viewport=dict(width=1440,height=1100));errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base,wait_until='domcontentloaded');page.locator('#workspace-create').wait_for()
        page.locator('#workspace-name').fill('M3-S1 original ambiguous meetings');page.locator('#workspace-create button').click()
        page.locator('#source-create').wait_for();page.locator('#source-name').fill('original-ambiguous-meetings.txt')
        page.locator('#source-content').fill(case['source']);page.locator('#source-create button[type="submit"]').click()
        page.locator('#source-update').wait_for(timeout=30000)
        page.locator('[data-screen="ask"]').click();page.locator('#ask-query').fill(case['question'])
        page.locator('#ask-domain').select_option('tasks')
        page.locator('#ask-submit-button').click();page.locator('#ask-result h2').wait_for()
        assert campaign.requests==0,'UNAUTHORIZED_PROVIDER_CALL'
        page.locator('#ask-external').check()
        with page.expect_response(lambda r:r.url.endswith('/api/product/ask') and r.request.method=='POST',timeout=240000) as pending:
            page.locator('#ask-submit-button').click()
        submitted=pending.value.json();(root/'ask-submission.json').write_text(json.dumps(submitted,ensure_ascii=False,indent=2),encoding='utf8')
        result=submitted['result'];run_id=result['agent_run_id'];ws=result['workspace_id']
        end=time.monotonic()+330
        while result['status']=='INVESTIGATING' and time.monotonic()<end:
            page.wait_for_timeout(500)
            result=page.request.get(base+f'api/product/ask/runs/{run_id}?workspace_id={ws}').json()['result']
        row=dict(run_id=run_id,workspace_id=ws,result=result,browser=browser.version,browser_errors=errors,
            requests_without_authorization=0,requests_after_completion=campaign.requests)
        (root/'browser.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
        page.screenshot(path=str(root/'terminal.png'),full_page=True)
        assert result['status']=='PARTIAL',result.get('error')
        assert result['reviewer']['accepted']
        observations=result['task_review']['observations']
        page_tasks=[o for o in observations if '页面巡检' in (o['description']or'')]
        assert page_tasks and all(o['owner'] is None and o['deadline_text'] is None and o['deadline_role']=='UNKNOWN' for o in page_tasks)
        assert result['evidence'] and not result['conclusions']
        assert any(r['tool']=='inspect_task_context' for r in result['task_review']['executed_tools'])
        assert not any(r.get('current_supported') for r in result['task_review']['relations'])
        page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
        summaries=page.locator('#ask-result summary').filter(has_text='展开原始消息与字段证据')
        for i in range(summaries.count()):summaries.nth(i).click()
        row['result_text']=page.locator('#ask-result').inner_text()
        page.screenshot(path=str(root/'fields.png'),full_page=True)
        page.locator('#ask-result [data-action="inspect-source"]').first.click();page.locator('#source-update').wait_for()
        assert page.locator('#update-content').input_value()==case['source']
        row['source_opened_exact']=True
        page.screenshot(path=str(root/'source-open.png'),full_page=True)
        before=campaign.requests
        page.reload(wait_until='domcontentloaded');page.locator('#source-create').wait_for()
        page.locator('[data-screen="ask"]').click();page.locator('[data-action="reopen-task"]').click()
        page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
        assert campaign.requests==before
        row['refresh_text']=page.locator('#ask-result').inner_text()
        row['refresh_provider_requests']=campaign.requests-before
        (root/'browser.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
        browser.close()
        print('ORIGINAL_AMBIGUOUS_PRODUCT_LIVE_PARTIAL_ACCEPTED; REQUESTS='+str(campaign.requests),flush=True)
finally:
    server.shutdown();server.server_close();app.close();sdk.close()
    print('M3S1_REQUESTS='+str(3+campaign.requests)+' RESERVED='+str(.75+campaign.cost)+' FAILURE='+str(campaign.failure),flush=True)
