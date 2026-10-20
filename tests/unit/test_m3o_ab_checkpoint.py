"""Staged production LIVE path with official SDK + MockTransport; no network."""
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
import pytest

from linkloom.evaluation import task_profile_ab as ab


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    root = tmp_path/'private-budget'
    monkeypatch.setattr(ab,'live_root',lambda:root,raising=False)
    monkeypatch.setenv('GEMINI_API_KEY','synthetic-no-network')
    from linkloom.agents import providers
    original = providers.build_gemini_sdk_client
    sends = []
    fault = {}
    def capture(request):
        sends.append(json.loads(request.content))
        if fault.get('timeout'):
            raise httpx.ReadTimeout('synthetic unknown response')
        if fault.get('http503'):
            return httpx.Response(503,json={'error':{'code':503,'status':'UNAVAILABLE','message':'synthetic'}})
        args={'claims':[]} if not fault.get('schema') else {'wrong':[]}
        if fault.get('task'):
            from tests.unit.test_task_observation_discovery import claim
            text=freeze['sources'][0]['content'].splitlines()[0].split(': ',1)[1]
            args={'claims':[claim(text,'修订告警手册')]}
        part={'functionCall':{'name':'emit_semantic_candidates','args':args}}
        if fault.get('signature'):
            part['thoughtSignature']='c3ludGhldGljLXNpZ25hdHVyZQ=='
        return httpx.Response(200,json={
            'candidates':[{'content':{'role':'model','parts':[part]},
                'finishReason':'STOP'}],
            'modelVersion':'other-model' if fault.get('version') else 'gemini-3.8-flash',
            'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':4096 if fault.get('output_cap') else 10,
                **({} if fault.get('usage') else {'thoughtsTokenCount':0}),
                'totalTokenCount':999 if fault.get('total') else 110}})
    def builder(key,**kwargs):
        opts = kwargs['http_options_kwargs']
        assert opts['retry_options']=={'attempts':1}
        opts['client_args']={'transport':httpx.MockTransport(capture)}
        if fault.get('extra_wire'):
            observer=kwargs['wire_payload_observer']
            def twice(data):
                observer(data)
                observer(data)
            kwargs['wire_payload_observer']=twice
        return original(key,**kwargs)
    monkeypatch.setattr(providers,'build_gemini_sdk_client',builder)
    freeze = ab.load_freeze()
    grant = dict(approved=True,experiment='m3o-ab/v2',manifest_sha256=freeze['manifest_sha256'],
        expires_at=(datetime.now(UTC)+timedelta(hours=1)).isoformat(),
        **{k:freeze['config'][k] for k in ('model_id','provider_id','max_requests','max_cost_usd',
            'input_price_ceiling_per_million','output_price_ceiling_per_million')})
    output = tmp_path/'output'
    def stage(name):
        return ab.execute(output,mode='live',grant=grant,stage=name,enable_live=True)
    return stage,sends,root,output,grant,fault


def test_first_pair_verify_continue_are_distinct_durable_stages(campaign):
    stage,sends,root,output,grant,fault = campaign
    first = stage('FIRST_PAIR')
    assert len(sends)==2 and first['counts']=={'A':1,'B':1}
    assert first['FIRST_PAIR_GATE']!='PASS'
    with pytest.raises(ValueError,match='FIRST_PAIR_GATE'):
        stage('CONTINUE_REMAINING')
    assert len(sends)==2
    verified = stage('VERIFY_FIRST_PAIR')
    assert verified['FIRST_PAIR_GATE']=='PASS' and len(sends)==2
    final = ab.run(output,mode='live',grant=grant,stage='CONTINUE_REMAINING',enable_live=True)
    assert len(sends)==26 and final['counts']=={'A':13,'B':13}
    assert final['sends_this_invocation']==24
    again = ab.run(output,mode='live',grant=grant,stage='CONTINUE_REMAINING',enable_live=True)
    assert again['sends_this_invocation']==0 and again['scores']==final['scores']
    assert len(sends)==26
    import sqlite3
    with sqlite3.connect(root/'budget.sqlite3') as db:
        rows = db.execute('SELECT identity,status FROM requests ORDER BY rowid').fetchall()
        assert len(rows)==len({r[0] for r in rows})==26
        assert all(r[1]=='COMPLETE' for r in rows)
        assert rows[:2]==[('D01-A','COMPLETE'),('D01-B','COMPLETE')]
    assert len(rows)*ab.load_freeze()['config']['reservation_usd'] <= 3
    # No stage invocation can replay a completed identity.
    with pytest.raises(ValueError):
        stage('FIRST_PAIR')
    assert len(sends)==26


@pytest.mark.parametrize('fault_name',['timeout','usage','version','schema','http503','total','output_cap'])
def test_unknown_or_invalid_result_stops_without_retry(campaign,fault_name):
    stage,sends,root,output,grant,fault = campaign
    fault[fault_name]=True
    with pytest.raises(ValueError):
        stage('FIRST_PAIR')
    assert len(sends)==1
    for name in ('FIRST_PAIR','VERIFY_FIRST_PAIR','CONTINUE_REMAINING'):
        with pytest.raises(ValueError):
            stage(name)
    assert len(sends)==1


def test_expired_or_old_grant_never_sends(campaign):
    stage,sends,root,output,grant,fault = campaign
    grant['expires_at']='2000-01-01T00:00:00Z'
    with pytest.raises(ValueError,match='EXPIRED'):
        stage('FIRST_PAIR')
    assert not sends and not root.exists()
    grant['expires_at']=(datetime.now(UTC)+timedelta(hours=1)).isoformat()
    grant['experiment']='m3o-ab/v1'
    with pytest.raises(ValueError,match='AUTHORIZATION'):
        stage('FIRST_PAIR')
    assert not sends


@pytest.mark.parametrize('suffix',['request','response','sdk','host'])
def test_changed_artifact_never_passes_gate_or_continues(campaign,suffix):
    stage,sends,root,output,grant,fault = campaign
    stage('FIRST_PAIR')
    path = output/('D01-A.'+suffix+'.json')
    path.write_text('{}',encoding='utf8')
    with pytest.raises(ValueError):
        stage('VERIFY_FIRST_PAIR')
    with pytest.raises(ValueError):
        stage('CONTINUE_REMAINING')
    assert len(sends)==2


def test_missing_completed_artifact_never_continues(campaign):
    stage,sends,root,output,grant,fault = campaign
    stage('FIRST_PAIR')
    stage('VERIFY_FIRST_PAIR')
    # Simulate unavailable durable sidecar without deleting it.
    path = output/'D01-A.host.json'
    path.rename(output/'D01-A.host.unavailable')
    with pytest.raises((ValueError,FileNotFoundError)):
        stage('CONTINUE_REMAINING')
    assert len(sends)==2


def test_same_ledger_cannot_bind_another_output(campaign):
    stage,sends,root,output,grant,fault = campaign
    stage('FIRST_PAIR')
    stage('VERIFY_FIRST_PAIR')
    for action in ('FIRST_PAIR','CONTINUE_REMAINING'):
        with pytest.raises(ValueError):
            ab.execute(output.parent/'another',mode='live',grant=grant,stage=action,enable_live=True)
    assert len(sends)==2


def test_live_enable_stage_and_manifest_are_mandatory(campaign):
    stage,sends,root,output,grant,fault = campaign
    for kwargs in ({'stage':'FIRST_PAIR'},{'enable_live':True}):
        with pytest.raises(ValueError):
            ab.execute(output,mode='live',grant=grant,**kwargs)
    grant['manifest_sha256']='0'*64
    with pytest.raises(ValueError,match='AUTHORIZATION'):
        stage('FIRST_PAIR')
    assert not sends and not root.exists()


def test_wrong_model_grant_and_changed_source_are_zero_send(campaign,monkeypatch):
    import shutil
    stage,sends,root,output,grant,fault=campaign
    grant['model_id']='gemini-2.5-flash'
    with pytest.raises(ValueError,match='AUTHORIZATION'):stage('FIRST_PAIR')
    grant['model_id']='gemini-3.8-flash'
    changed=output.parent/'changed-freeze'
    shutil.copytree(ab.FREEZE,changed)
    (changed/'sources.json').write_text('[]')
    monkeypatch.setattr(ab,'FREEZE',changed)
    with pytest.raises(ValueError,match='FREEZE_HASH_MISMATCH'):stage('FIRST_PAIR')
    assert not sends and not root.exists()


def test_unexpected_wire_observer_count_blocks_before_network(campaign):
    stage,sends,root,output,grant,fault=campaign
    fault['extra_wire']=True
    with pytest.raises(ValueError):stage('FIRST_PAIR')
    assert not sends
    with sqlite3.connect(root/'budget.sqlite3') as db:
        assert db.execute('SELECT status FROM requests').fetchone()==('UNKNOWN_STOP',)


def test_third_or_pending_identity_invalidates_first_pair(campaign):
    stage,sends,root,output,grant,fault=campaign
    stage('FIRST_PAIR')
    with sqlite3.connect(root/'budget.sqlite3') as db:
        db.execute("INSERT INTO requests VALUES ('D02-A','{}','RESERVED',NULL)")
    with pytest.raises(ValueError,match='LEDGER_PREFIX'):stage('VERIFY_FIRST_PAIR')
    with pytest.raises(ValueError):stage('CONTINUE_REMAINING')
    assert len(sends)==2


def test_existing_output_is_not_a_resume_signal(campaign):
    stage,sends,root,output,grant,fault=campaign
    output.mkdir()
    (output/'user-draft.txt').write_text('keep')
    with pytest.raises(ValueError,match='OUTPUT_EXISTS'):stage('FIRST_PAIR')
    assert not sends and (output/'user-draft.txt').read_text()=='keep'


@pytest.mark.parametrize('action',['FIRST_PAIR','VERIFY_FIRST_PAIR','CONTINUE_REMAINING'])
def test_existing_empty_ledger_is_not_reinitialized(campaign,action):
    stage,sends,root,output,grant,fault=campaign
    root.mkdir()
    ledger=root/'budget.sqlite3'
    ledger.write_bytes(b'')
    with pytest.raises(ValueError):stage(action)
    assert ledger.read_bytes()==b'' and not sends


def test_expired_authorization_between_stages_sends_nothing(campaign):
    stage,sends,root,output,grant,fault=campaign
    stage('FIRST_PAIR')
    grant['expires_at']='2000-01-01T00:00:00Z'
    for name in ('VERIFY_FIRST_PAIR','CONTINUE_REMAINING'):
        with pytest.raises(ValueError,match='EXPIRED'):stage(name)
    assert len(sends)==2


def test_gate_failure_is_persisted_and_cannot_be_erased_by_restoring_file(campaign):
    stage,sends,root,output,grant,fault=campaign
    stage('FIRST_PAIR')
    path=output/'D01-A.host.json'
    original=path.read_bytes()
    path.write_text('{}')
    with pytest.raises(ValueError):stage('VERIFY_FIRST_PAIR')
    path.write_bytes(original)
    with pytest.raises(ValueError):stage('VERIFY_FIRST_PAIR')
    with pytest.raises(ValueError):stage('CONTINUE_REMAINING')
    assert len(sends)==2


def test_verifier_hashes_and_parses_the_same_artifact_bytes(campaign,monkeypatch):
    stage,sends,root,output,grant,fault=campaign
    stage('FIRST_PAIR')
    watched={p:0 for p in output.glob('D01-*.json')}
    original=Path.open
    def opened(path,*args,**kwargs):
        if path in watched:watched[path]+=1
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    stage('VERIFY_FIRST_PAIR')
    assert set(watched.values())=={1}


def test_labels_and_rubric_are_never_opened_by_stages(campaign,monkeypatch):
    stage,sends,root,output,grant,fault = campaign
    original = Path.open
    def guarded(path,*args,**kwargs):
        if path.parent==ab.FREEZE and path.name in ('labels.json','groups.json','rubric.json'):
            pytest.fail('score data opened by LIVE execution')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    stage('FIRST_PAIR')
    stage('VERIFY_FIRST_PAIR')
    stage('CONTINUE_REMAINING')
    assert len(sends)==26


def test_completed_schema_bad_response_is_not_a_mock_success(campaign):
    stage,sends,root,output,grant,fault = campaign
    fault['schema']=True
    with pytest.raises(ValueError):stage('FIRST_PAIR')
    with sqlite3.connect(root/'budget.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM requests').fetchone()==(1,)
        assert db.execute('SELECT state FROM campaign').fetchone()==('FIRST_FAILED',)
        record=json.loads(db.execute('SELECT response FROM requests').fetchone()[0])
        assert record['normalized']['proposal']['tool_calls'][0]['runtime_call']['arguments']=={'wrong':[]}


def test_nonempty_host_evidence_and_sdk_thought_signature_survive_gate(campaign):
    stage,sends,root,output,grant,fault=campaign
    fault.update(task=True,signature=True)
    stage('FIRST_PAIR')
    host=ab.read(output/'D01-A.host.json')
    assert host['valid_observations'] or host['partial_observations']
    assert stage('VERIFY_FIRST_PAIR')['FIRST_PAIR_GATE']=='PASS'
    assert len(sends)==2


def test_existing_final_report_blocks_before_remaining_sends(campaign):
    stage,sends,root,output,grant,fault=campaign
    stage('FIRST_PAIR');stage('VERIFY_FIRST_PAIR')
    path=output/'execution.json'
    path.write_text('{"unrelated":"keep"}')
    with pytest.raises(ValueError):stage('CONTINUE_REMAINING')
    assert len(sends)==2 and path.read_text()=='{"unrelated":"keep"}'


def _crash_worker(root,output,point):
    """Separate process; power loss releases OS lock, SQLite retains real state."""
    import socket
    from linkloom.agents import providers
    from linkloom.evaluation import task_profile_checkpoint as checkpoint
    socket.socket.connect=lambda *a:(_ for _ in ()).throw(AssertionError('NO_NETWORK'))
    socket.getaddrinfo=socket.socket.connect
    ab.live_root=lambda:Path(root)
    os.environ['GEMINI_API_KEY']='synthetic-no-network'
    original_builder=providers.build_gemini_sdk_client
    def capture(request):
        if point=='UNKNOWN_AFTER_SEND' and len(list(Path(output).glob('*.sdk.json')))>=2:
            os._exit(71)
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'functionCall':{
            'name':'emit_semantic_candidates','args':{'claims':[]}}}]},'finishReason':'STOP'}],
            'modelVersion':'gemini-3.8-flash','usageMetadata':{
                'promptTokenCount':100,'candidatesTokenCount':10,'thoughtsTokenCount':0,'totalTokenCount':110}})
    def builder(key,**kwargs):
        kwargs['http_options_kwargs']['client_args']={'transport':httpx.MockTransport(capture)}
        return original_builder(key,**kwargs)
    providers.build_gemini_sdk_client=builder
    audit=checkpoint._audit
    def crash_audit(db,kind,detail):
        audit(db,kind,detail)
        if kind=='ARTIFACTS_SEALED' and detail['identity']==('D01-A' if point=='AFTER_A' else 'D01-B') and point in ('AFTER_A','AFTER_B'):
            os._exit(71)
        if point=='AFTER_LAST_PAIR' and kind=='PAIR_VERIFIED' and detail['count']==26:
            os._exit(71)
    checkpoint._audit=crash_audit
    transition=checkpoint._transition
    def crash_transition(db,state,gate=None):
        transition(db,state,gate)
        if point=='BEFORE_FINAL_REPORT' and state in ('FINALIZING','COMPLETED'):
            os._exit(71)
    checkpoint._transition=crash_transition
    save=ab.save
    def crash_save(path,value):
        if point=='COMPLETE_BEFORE_FILES' and path.name=='D01-A.sdk.json':os._exit(71)
        return save(path,value)
    ab.save=crash_save
    reserve=ab.Budget.reserve
    def crash_reserve(self,identity,request):
        if point=='CONTINUE_BEFORE_SEND' and identity=='D02-A':os._exit(71)
        return reserve(self,identity,request)
    ab.Budget.reserve=crash_reserve
    f=ab.load_freeze()
    grant=dict(approved=True,experiment='m3o-ab/v2',manifest_sha256=f['manifest_sha256'],
        expires_at=(datetime.now(UTC)+timedelta(hours=1)).isoformat(),
        **{k:f['config'][k] for k in ('model_id','provider_id','max_requests','max_cost_usd',
            'input_price_ceiling_per_million','output_price_ceiling_per_million')})
    ab.execute(output,mode='live',grant=grant,stage='FIRST_PAIR',enable_live=True)
    ab.execute(output,mode='live',grant=grant,stage='VERIFY_FIRST_PAIR',enable_live=True)
    if point=='AFTER_GATE':os._exit(71)
    ab.execute(output,mode='live',grant=grant,stage='CONTINUE_REMAINING',enable_live=True)
    raise AssertionError('injection did not terminate')


@pytest.mark.parametrize('point',['AFTER_A','AFTER_B','AFTER_GATE','CONTINUE_BEFORE_SEND','UNKNOWN_AFTER_SEND','COMPLETE_BEFORE_FILES'])
def test_process_crash_never_resends_or_guesses_missing_evidence(campaign,point):
    stage,sends,root,output,grant,fault=campaign
    code='from tests.unit.test_m3o_ab_checkpoint import _crash_worker; import sys; _crash_worker(*sys.argv[1:])'
    child=subprocess.run([sys.executable,'-c',code,str(root),str(output),point],capture_output=True,timeout=35)
    assert child.returncode==71,child.stderr.decode(errors='replace')
    if point in ('AFTER_B','AFTER_GATE'):
        assert stage('VERIFY_FIRST_PAIR')['FIRST_PAIR_GATE']=='PASS'
        assert not sends
        stage('CONTINUE_REMAINING')
        assert len(sends)==24
    else:
        for action in ('FIRST_PAIR','VERIFY_FIRST_PAIR','CONTINUE_REMAINING'):
            with pytest.raises((ValueError,FileNotFoundError)):
                stage(action)
        assert not sends
    with sqlite3.connect(root/'budget.sqlite3') as db:
        rows=db.execute('SELECT identity,status FROM requests ORDER BY rowid').fetchall()
        assert len(rows)==len({r[0] for r in rows})
        if point=='UNKNOWN_AFTER_SEND':assert rows[-1]==('D02-A','RESERVED')


def test_cross_process_campaign_lock_blocks_both_sending_phases(campaign):
    stage,sends,root,output,grant,fault=campaign
    code='from pathlib import Path; import sys; from linkloom.evaluation.task_profile_checkpoint import campaign_lock\nwith campaign_lock(Path(sys.argv[1])):\n print("LOCKED",flush=True)\n sys.stdin.readline()'
    def blocked(action):
        child=subprocess.Popen([sys.executable,'-u','-c',code,str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            assert child.stdout.readline().strip()=='LOCKED'
            with pytest.raises(ValueError,match='ALREADY_RUNNING'):stage(action)
        finally:
            child.communicate('release\n',timeout=20)
        assert child.returncode==0
    blocked('FIRST_PAIR')
    assert not sends
    stage('FIRST_PAIR');stage('VERIFY_FIRST_PAIR')
    blocked('CONTINUE_REMAINING')
    assert len(sends)==2


@pytest.mark.parametrize('point',['BEFORE_FINAL_REPORT','AFTER_LAST_PAIR'])
def test_completed_requests_can_finalize_after_process_restart_without_sending(campaign,point):
    stage,sends,root,output,grant,fault=campaign
    code='from tests.unit.test_m3o_ab_checkpoint import _crash_worker; import sys; _crash_worker(*sys.argv[1:])'
    child=subprocess.run([sys.executable,'-c',code,str(root),str(output),point],capture_output=True,timeout=55)
    assert child.returncode==71,child.stderr.decode(errors='replace')
    assert not (output/'execution.json').exists()
    report=stage('CONTINUE_REMAINING')
    assert report['status']=='LIVE_AB_COMPLETED' and not sends
    assert ab.read(output/'execution.json')['counts']=={'A':13,'B':13}
    assert stage('CONTINUE_REMAINING')==report and not sends


def test_atomic_final_publication_never_overwrites_a_racing_file(tmp_path,monkeypatch):
    from linkloom.evaluation.task_profile_checkpoint import _publish_final
    with sqlite3.connect(':memory:',isolation_level=None) as db:
        db.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY,kind TEXT,detail TEXT,at TEXT)')
        save=ab.save
        def racing(path,value):
            save(path,value)
            (tmp_path/'execution.json').write_text('{"keep":"original"}')
        monkeypatch.setattr(ab,'save',racing)
        with pytest.raises(FileExistsError):
            _publish_final(db,tmp_path,{'completed':True})
        assert (tmp_path/'execution.json').read_text()=='{"keep":"original"}'
