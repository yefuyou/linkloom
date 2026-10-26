"""Real loopback Product boundaries; no Provider or pre-seeded relation proofs."""
from contextlib import contextmanager
import json
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from linkloom.ui.offline_task_demo import build_offline_task_demo
from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend


def call(base, path, method='GET', payload=None, origin=None):
    headers={'Content-Type':'application/json','Origin':origin or base}
    request=Request(base+path, data=json.dumps(payload).encode('utf8') if payload is not None else None,
                    method=method, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status,json.load(response)
    except HTTPError as error:
        return error.code,json.load(error)


@contextmanager
def reviewed_run(root):
    app,service=build_offline_task_demo(root/'offline.sqlite')
    server=create_http_server(_UnusedRunBackend(),product_app=app,investigation_service=service,
                              allow_local_task_review=True,port=0)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        ws=call(base,'/api/workspaces/current')[1]['workspace']['workspace_id']
        config=call(base,'/api/product/ask/config')[1]
        code,body=call(base,'/api/product/ask','POST',dict(query='请调查青竹日志迁移任务与日期变化。',workspace_id=ws,
            investigation_context=dict(domain='tasks',snapshot_hash=config['investigation_context']['snapshot_hash'])))
        assert code==202,body
        result=body['result']; deadline=monotonic()+30
        while result['status']=='INVESTIGATING' and monotonic()<deadline:
            sleep(.03)
            code,body=call(base,f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
            assert code==200,body
            result=body['result']
        assert result['status']=='PARTIAL',result
        path=f"/api/product/task-review?workspace_id={ws}&run_id={result['agent_run_id']}"
        code,view=call(base,path);assert code==200 and view['available'],view
        yield app,server,base,ws,result,path,view
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        if server.task_review:server.task_review.close()
        app.close()


def statement(ws,result,view,note='I checked the original sources; this is no business approval.'):
    return dict(workspace_id=ws,run_id=result['agent_run_id'],step_id='identity',action='REVIEW',
        request_id='req-'+uuid4().hex,capability=view['capability'],expected_revision=view['revision'],
        actor_label='Self-asserted local test reader',note=note,outcome='CHECKED')


def test_real_http_records_retry_and_rejects_authority_forgery(tmp_path):
    with reviewed_run(tmp_path) as (app,server,base,ws,result,path,view):
        payload=statement(ws,result,view)
        code,body=call(base,'/api/product/task-review','POST',payload)
        assert code==200 and body['receipt']['status']=='RECORDED',body
        receipt=body['receipt']
        assert receipt['actor_label']==payload['actor_label']
        code,retry=call(base,'/api/product/task-review','POST',payload)
        assert code==200 and retry['idempotent'] is True and retry['receipt']==receipt,retry
        code,collision=call(base,'/api/product/task-review','POST',{**payload,'note':'altered retry'})
        assert code==409 and collision['error']['code']=='REQUEST_ID_COLLISION',collision
        code,forged=call(base,'/api/product/task-review','POST',{**payload,'verdict':'SUPPORTED'})
        assert code==400,forged
        code,foreign_origin=call(base,'/api/product/task-review','POST',payload,origin='https://example.invalid')
        assert code==403 and foreign_origin['error']['code']=='INVALID_ORIGIN',foreign_origin
        code,current=call(base,path)
        assert code==200 and len(current['history'])==1 and current['business_approval'] is False
        # Checking a source does not promote the original ambiguous model proposal.
        assert all(not s.get('current_supported') for s in current['steps'])
        assert current['steps'][0]['proof']['status']!='CURRENT_CONFIRMED'


def test_real_http_source_update_redacts_notes_and_foreign_workspace(tmp_path):
    with reviewed_run(tmp_path) as (app,server,base,ws,result,path,view):
        quote=result['task_review']['observations'][0]['provenance']['quote']
        payload=statement(ws,result,view,note=quote)
        code,body=call(base,'/api/product/task-review','POST',payload);assert code==200,body
        source_id=result['evidence'][0]['source_id']
        source=call(base,'/api/sources/'+source_id)[1]['source']
        updated=source['content']+'\n[2029-06-06T09:00:00Z] 林枫: 本条是本次专用验收的来源版本更新说明。'
        code,changed=call(base,'/api/sources/'+source_id,'PUT',dict(name=source['name'],content=updated))
        assert code==202 and changed['changed'],changed
        code,stale=call(base,path)
        assert code==200 and not stale['available'] and not stale['steps'],stale
        assert quote not in json.dumps(stale,ensure_ascii=False)
        code,refused=call(base,'/api/product/task-review','POST',payload)
        assert code==409 and quote not in json.dumps(refused,ensure_ascii=False),refused
        code,newws=call(base,'/api/workspaces','POST',dict(name='Different synthetic workspace'))
        assert code==201,newws
        code,selected=call(base,'/api/workspaces/current','POST',dict(workspace_id=newws['workspace']['workspace_id']))
        assert code==200 and selected['workspace']['workspace_id']!=ws,selected
        code,foreign=call(base,path)
        assert code in {404,409} and quote not in json.dumps(foreign,ensure_ascii=False),foreign
