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

out=Path('work/m3s-live-20261022')
assert not (out/'global-provider/budget.json').exists(), 'EXISTING_RECEIPTS_NO_AUTOMATIC_RERUN'
out.mkdir(parents=True,exist_ok=True)
fixture=Path('docs/requirements/m3-s-task-field-grounding/field-cases.json')
held=Path('docs/requirements/m3-s-task-field-grounding/holdout-expectations.json')
data=json.loads(fixture.read_text(encoding='utf8'))
expect={r['id']:r['expected'] for r in json.loads(held.read_text(encoding='utf8'))['cases']}
cases=data['cases']+[dict(r,expected=expect[r['id']]) for r in data['holdout_inputs_fixed_before_expectation_reveal']]
assert len(cases)==16
key=os.environ.get('GEMINI_API_KEY')
assert key, 'GEMINI_API_KEY_UNSET'
assert os.environ.get('GOOGLE_GENAI_USE_VERTEXAI','').lower() not in {'1','true'},'UNEXPECTED_PROVIDER_IDENTITY'
baseline=json.loads(Path('../m3r/docs/requirements/m3-r-llm-first-semantic-closure/evidence/live-summary.json').read_text(encoding='utf8'))
assert baseline['cumulative_requests']==47
reservation=dict(prior_requests=47,prior_reserved_usd_conservative=13.93,new_requests_limit=24,new_reserved_usd_limit=8,
    global_limit_usd=50,fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),holdout_sha256=hashlib.sha256(held.read_bytes()).hexdigest(),
    started_at=datetime.now(UTC).isoformat(),sdk='google-genai 2.22.0',model=GEMINI_38_FLASH_MODEL_ID,thinking_level='low',
    billing_project_verified=False,actual_bill_usd=None,promotional_credit_deduction_usd=None,
    billing_identity='Existing local authorized API key, default Google SDK endpoint; project/bill not exposed by generation API')
assert reservation['prior_reserved_usd_conservative']+reservation['new_reserved_usd_limit']<=50
with (out/'global-reservation.json').open('w',encoding='utf8') as f:
    json.dump(reservation,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
sdk=build_gemini_sdk_client(key,http_options_kwargs=dict(timeout=30000,retry_options=dict(attempts=1)))
class Bridge:
    def generate_content(self,*,model,contents,config):return sdk.models.generate_content(model=model,contents=contents,config=config)
real=GeminiProviderAdapter(Bridge(),model_id=GEMINI_38_FLASH_MODEL_ID)
campaign=TaskProviderSession(out/'global-provider',TaskProviderLimits(24,1500000,200000,8,900,35,.25),lambda:None)
class CampaignProvider(GeminiProviderAdapter):
    def complete(self,request):
        return campaign.complete(real,request.agent_id,request)
provider=CampaignProvider(Bridge(),model_id=GEMINI_38_FLASH_MODEL_ID)
def permit(request):return ProviderRequestAuthorization.approve(request,guard_id='m3s-live',reservation_id='m3s-fixed-campaign',estimated_cost_upper_bound_usd=.25)

def observations(result):return [o.to_dict() for o in (*result.valid_observations,*result.partial_observations)]
def evaluate(expected, rows, ordinal):
    selected=[o for o in rows if o['provenance']['ordinal']==ordinal]
    report=[]
    for gold in expected:
        matching=[o for o in selected if gold['anchor'] in o['raw_text']]
        pred=matching[0] if len(matching)==1 else None
        fields={}
        for name in ['owner','deadline','project','vendor']:
            wanted=(gold['deadline_text'],gold['deadline'],gold['precision'],gold['role']) if name=='deadline' else gold[name]
            actual=(pred['deadline_text'],pred['deadline'],pred['deadline_precision'],pred['deadline_role']) if name=='deadline' and pred else pred.get(name) if pred else None
            missing=gold['deadline_text'] is None if name=='deadline' else wanted is None
            absent=pred is not None and (pred['deadline_text'] is None if name=='deadline' else actual is None)
            fields[name]=dict(expected=wanted,actual=actual,
                outcome='MISSING_OBSERVATION' if pred is None else 'CORRECT_UNKNOWN' if missing and absent else 'CORRECT_ACCEPT' if not missing and actual==wanted else 'FALSE_ACCEPT' if not absent else 'REJECTED')
        report.append(dict(anchor=gold['anchor'],fields=fields))
    return report

def f_diagnostics():
    reports=[]
    for batch in range(8):
        pair=cases[batch*2:batch*2+2]
        content='\n'.join(f'[2030-01-0{i+1}T09:00:00Z] {c["speaker"]}: {c["text"]}' for i,c in enumerate(pair))
        artifact=RawArtifact('m3s-public-f',f'f-{batch+1}.txt','timestamped_text',content)
        parsed=parse_source_text(artifact)
        registry=SourceReferenceRegistry()
        for s in parsed.segments:registry.register_evidence(artifact.workspace_id,s.evidence_span.evidence_ref,content_hash=artifact.content_hash,source_version=artifact.artifact_version_id)
        extractor=ProviderNeutralSemanticExtractor(provider,provider_id='gemini',model_id=GEMINI_38_FLASH_MODEL_ID,request_guard=permit,request_guard_id='m3s-live',max_attempts=1)
        service=TaskObservationDiscoveryService(extractor,source_registry=registry,
            current_source=lambda ws,aid:artifact if ws==artifact.workspace_id and aid==artifact.artifact_id else None,
            task_profile=TaskDiscoveryProfile.FIELD_GROUNDED_V2)
        result=service.discover((artifact,),workspace_id=artifact.workspace_id)
        current=observations(result)
        legacy=[]
        for extraction in result.extractions:
            old=SimpleNamespace(claims=tuple(replace(c,task=replace(c.task,field_bindings=None,work_state=None)) if c.task else c for c in extraction.claims))
            found,_,_=_project(old,artifact,parsed.segments)
            legacy.extend(o.to_dict() for o in found)
        row=dict(batch=batch+1,cases=[c['id'] for c in pair],source=content,discovery=result.to_dict(),legacy_observations=legacy,
            raw_responses=[r.response_evidence for r in result.extractions],
            new_evaluation=[dict(case_id=c['id'],results=evaluate(c['expected'],current,i)) for i,c in enumerate(pair)],
            legacy_evaluation=[dict(case_id=c['id'],results=evaluate(c['expected'],legacy,i)) for i,c in enumerate(pair)],
            all_new_observations_verify=all(o.verify(artifact) for o in (*result.valid_observations,*result.partial_observations)))
        (out/f'f-batch-{batch+1}.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
        reports.append(row)
        print('F_BATCH '+str(batch+1)+' '+str(len(current))+' observations '+str([f.reason for f in result.failures]),flush=True)
        if campaign.failure:raise RuntimeError('STOP_'+campaign.failure)
    (out/'f-summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')

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
            page.locator('#ask-external').check();page.locator('#ask-submit-button').click()
            end=time.monotonic()+260
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
            page.locator('#ask-result [data-action="inspect-source"]').first.click();page.get_by_role('heading',name='meetings.txt').wait_for()
            source_text=page.locator('#page').inner_text()
            before=campaign.requests
            page.reload(wait_until='domcontentloaded');page.locator('#source-create').wait_for();page.locator('[data-screen="ask"]').click()
            page.locator('[data-action="reopen-task"]').wait_for();page.locator('[data-action="reopen-task"]').click()
            page.locator('[data-task-proposal-verdict]').first.wait_for(timeout=15000)
            assert campaign.requests==before,'REFRESH_CALLED_PROVIDER'
            row=dict(scene=scene['id'],run_id=pointer['run_id'],workspace_id=pointer['workspace_id'],database=str((root/'product.sqlite').resolve()),
                result=result,browser=browser.version,browser_errors=errors,result_text=text,source_opened='meetings.txt' in source_text,
                refresh_text=page.locator('#ask-result').inner_text(),requests_before_authorization=count,requests_after=count if campaign.requests==count else campaign.requests,
                requests_before_refresh=before,requests_after_refresh=campaign.requests)
            (root/'browser.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
            browser.close()
            print('PRODUCT '+scene['id']+' '+result.get('status','')+' calls='+str(campaign.requests-count),flush=True)
    finally:
        server.shutdown();server.server_close();app.close()

try:
    f_diagnostics()
    for scene in SCENES:
        if campaign.failure:break
        product_scene(scene)
finally:
    sdk.close()
    print('CAMPAIGN_REQUESTS='+str(campaign.requests)+' RESERVED='+str(campaign.cost),flush=True)
