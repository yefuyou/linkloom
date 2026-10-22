"""Saved real run: browser and fresh Host revalidation, no Provider configured."""
import hashlib,json,os,subprocess,sys,threading
from pathlib import Path
sys.path[:0]=['src','.']
sys.stdout.reconfigure(encoding='utf-8')
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.demo import DemoCase,DemoRunBackend
from linkloom.ui.server import create_http_server
from playwright.sync_api import sync_playwright

root=Path('work/m3s1-live-recheck-20261022/product')
pointer=json.loads(next((root/'work/investigations').glob('ui-*.json')).read_text(encoding='utf8'))
run,ws=pointer['run_id'],pointer['workspace_id']
ledger=Path('work/m3s1-live-recheck-20261022/global-provider/budget.json')
before=hashlib.sha256(ledger.read_bytes()).hexdigest()
app=ProductApplication(database_path=root/'product.sqlite',extractor=None)
service=ProductInvestigationService(app)
server=create_http_server(DemoRunBackend(DemoCase.load_mps_001()),product_app=app,investigation_service=service,host='127.0.0.1',port=0)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}/'
report=dict(run_id=run,workspace_id=ws,provider_configured=False,provider_calls=0)

def cold():
    env=dict(os.environ,PYTHONPATH='src;.;tests/integration',GEMINI_API_KEY='',DEEPSEEK_API_KEY='',PYTHONIOENCODING='utf-8')
    p=subprocess.run([sys.executable,'tests/m3p_support.py',str(root/'product.sqlite'),run,ws],env=env,capture_output=True,text=True,encoding='utf8',timeout=30)
    assert p.returncode==0,p.stderr
    return json.loads(p.stdout)

def blocked(response):
    r=response['result']
    assert r['status']=='BLOCKED' and not r['evidence'] and not r['trace'] and not r.get('task_review'),r

try:
    code,initial=cold();assert code==200 and initial['result']['status']=='PARTIAL'
    report['fresh_process_initial']=initial
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        page=browser.new_page(viewport=dict(width=1440,height=1100));errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base,wait_until='domcontentloaded');page.locator('#source-create').wait_for()
        page.evaluate('([key,value])=>sessionStorage.setItem(key,value)',[f'linkloom:last-task:{ws}',run])
        page.locator('[data-screen="ask"]').click();page.locator('[data-action="reopen-task"]').click()
        page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
        summaries=page.locator('#ask-result summary').filter(has_text='展开原始消息与字段证据')
        for i in range(summaries.count()): summaries.nth(i).click()
        report['cold_browser_text']=page.locator('#ask-result').inner_text()
        page.screenshot(path=str(root/'cold-fields.png'),full_page=True)
        source=app.snapshot_investigation_sources(ws)[1][0]
        page.locator('#ask-result [data-action="inspect-source"]').first.click()
        page.locator('#source-update').wait_for(timeout=15000)
        assert page.locator('#update-content').input_value()==source['content']
        report['source_opened_exact']=True
        page.screenshot(path=str(root/'source-open.png'),full_page=True)
        page.reload(wait_until='domcontentloaded');page.locator('#source-create').wait_for()
        page.locator('[data-screen="ask"]').click();page.locator('[data-action="reopen-task"]').click()
        page.locator('#ask-result').get_by_role('heading',name='任务调查：已知证据与待确认问题').wait_for(timeout=15000)
        report['refresh_text']=page.locator('#ask-result').inner_text()
        # Verify revocation against the saved real result, then restore only the
        # exact synthetic evidence row so source-update can be tested separately.
        with app.repository._lock,app.repository._connection:
            row=dict(app.repository._connection.execute('SELECT * FROM ll_source_evidence WHERE workspace_id=? LIMIT 1',(ws,)).fetchone())
            app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',(ws,row['evidence_ref']))
        hot=page.request.get(base+f'api/product/ask/runs/{run}?workspace_id={ws}').json();blocked(hot)
        code,revoked=cold();assert code==200;blocked(revoked)
        report['revoked_hot']=hot;report['revoked_fresh_process']=revoked
        with app.repository._lock,app.repository._connection:
            columns=','.join(row);marks=','.join('?' for _ in row)
            app.repository._connection.execute(f'INSERT INTO ll_source_evidence ({columns}) VALUES ({marks})',tuple(row.values()))
        code,restored=cold();assert code==200 and restored['result']['status']=='PARTIAL'
        # Real workspace switch must not expose this run in the other workspace.
        page.locator('#new-workspace').click();page.locator('#workspace-name').fill('M3-S1 unrelated public workspace')
        page.locator('#workspace-create button[type="submit"]').click();page.locator('#source-create').wait_for()
        response=page.request.get(base+f'api/product/ask/runs/{run}?workspace_id={ws}')
        assert response.status==409
        report['cross_workspace_status']=response.status
        report['cross_workspace_response']=response.json()
        with page.expect_response(lambda r:r.url.endswith('/api/workspaces/current') and r.request.method=='POST'):
            page.locator('#workspace-picker').select_option(ws)
        page.locator('#source-create').wait_for()
        page.locator('[data-screen="ask"]').click();page.locator('[data-action="reopen-task"]').click()
        page.locator('#ask-result [data-action="inspect-source"]').first.click();page.locator('#source-update').wait_for()
        page.locator('#update-content').fill(source['content']+'\n公开合成来源更新：旧引用不再是当前版本。')
        with page.expect_response(lambda r:'/api/sources/' in r.url and r.request.method=='PUT') as changed_response:
            page.locator('#source-update button[type="submit"]').click()
        assert changed_response.value.status==202
        changed=page.request.get(base+f'api/product/ask/runs/{run}?workspace_id={ws}').json();blocked(changed)
        code,changed_cold=cold();assert code==200;blocked(changed_cold)
        report['updated_hot']=changed;report['updated_fresh_process']=changed_cold
        report['browser']=browser.version;report['browser_errors']=errors
        browser.close()
finally:
    server.shutdown();server.server_close();app.close()
    report['provider_ledger_sha256_before']=before
    report['provider_ledger_sha256_after']=hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert report['provider_ledger_sha256_before']==report['provider_ledger_sha256_after']
    (root/'cold-browser-and-safety.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print('COLD_SOURCE_REFRESH_REVOKE_UPDATE_WORKSPACE_PASS; PROVIDER_CALLS=0')
