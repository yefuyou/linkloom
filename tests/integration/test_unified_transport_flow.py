import json
import httpx
import pytest
from google.genai import types
from linkloom.agents.providers.gemini_api import build_gemini_sdk_client, GeminiProviderAdapter
from linkloom.ui.__main__ import GeminiSDKClientBridge
from tests.integration.unified_transport_fixture import UnifiedTransportHarness, MODEL
from tests.integration.unified_ask_fixture import ATLAS, OTHER, SUBJECT, RELATION, request_json, poll_product_ask
from tests.smoke.provider_ledger import SmokeBlocked


@pytest.fixture
def harness(tmp_path,monkeypatch):
    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE','bm25')
    item = UnifiedTransportHarness(tmp_path,diagnostics=True)
    yield item
    item.close()


def ask(harness, query, date='2026-10-10', allow=False):
    fixture = harness.fixture
    return request_json(fixture.base_url,'/api/product/ask',method='POST',timeout=30,data={
        'query':query,'workspace_id':fixture.workspace_ids[ATLAS], 'subject':SUBJECT,'relation':RELATION,
        'as_of':date,'allow_external_provider':allow})


def terminal(harness, started):
    return poll_product_ask(harness.fixture,started['result']['agent_run_id'],harness.fixture.workspace_ids[ATLAS])


def test_unified_current_historical_and_permission_construct_zero_sdk(harness):
    for query,date,value in [('What supplier are we currently using?','2026-10-10','Wrenwell'),
                             ('What supplier was formally adopted on 2026-10-06?','2026-10-06','Birchline')]:
        status,payload = ask(harness,query,date)
        result = payload['result']
        assert status==200 and result['path']=='DETERMINISTIC' and result['status']=='COMPLETED'
        assert result['conclusions'][0]['value']==value
        assert result['scope']['as_of'].startswith(date)
        assert result['receipts'][0]['authorization_type']=='HUMAN_APPROVAL'
        assert all(x['verified'] for x in result['evidence'])
    harness.service.external_provider = True
    # Service config owns this declaration; use its real configured state.
    status,payload = ask(harness,'Why did Launch change suppliers?')
    assert status==403 and payload['result']['error']['code']=='EXTERNAL_PROVIDER_PERMISSION_REQUIRED'
    assert harness.sdk_constructions==0 and harness.runs==[]


def test_unified_runtime_through_sdk_diagnostics_and_identical_payload(harness):
    status,started = ask(harness,'Why did Launch change suppliers?')
    assert status==202 and started['result']['path']=='AGENT'
    status,payload = terminal(harness,started)
    result = payload['result']
    assert status==200 and result['status']=='COMPLETED',result
    assert result['reviewer']['accepted'] and result['reviewer']['decision']=='evidence_sufficient'
    assert result['conclusions'][0]['value']=='Wrenwell'
    assert result['uncertainty']['unknown_fields']==['replacement_reason']
    assert result['timeline']['proposals'][0]['status']=='PROPOSED_NOT_APPROVED'
    assert all(x['verified'] for x in result['evidence']) and result['receipts']
    run = harness.runs[0]
    transport,events = run['transport'],run['events']
    assert len(transport.bodies)==5
    ledger_rows = [json.loads(x) for x in run['ledger'].path.read_text().splitlines()]
    for reservation in (x for x in ledger_rows if x['event']=='request_reserved'):
        n = reservation['request_sequence']
        group = [x for x in events if x['generation_sequence']==n]
        assert group and all(x['request_id']==run['diagnostics'].safe_identity(reservation['request_id']) for x in group)
        assert all(x['agent_round']==run['turns'][n-1].sequence for x in group)
        assert any(x['event']=='http11.receive_response_body.complete' for x in group)
        assert any(x.get('provider_request_id_digest','UNKNOWN')!='UNKNOWN' for x in group)
        assert next(x for x in group if x['event']=='request_constructed')['body_bytes']==len(transport.bodies[n-1])
    assert next(x for x in events if x['generation_sequence']==4)['tool_turn_count']>=3
    replay = []
    def fake(request):
        replay.append(request.content)
        return httpx.Response(200,content=transport.responses[len(replay)-1])
    with build_gemini_sdk_client('PRIVATE_OFFLINE_KEY',http_options_kwargs={'timeout':130000,
            'retry_options':types.HttpRetryOptions(attempts=1),'client_args':{'transport':httpx.MockTransport(fake)}}) as sdk:
        adapter = GeminiProviderAdapter(GeminiSDKClientBridge(sdk),model_id=MODEL)
        for turn in run['turns']:
            assert adapter.complete(turn).error is None
    assert replay==transport.bodies
    serialized = json.dumps(events)
    assert 'PRIVATE_' not in serialized and 'Wrenwell' not in serialized and 'Birchline' not in serialized
    assert 'Authorization' not in serialized and 'functionResponse' not in serialized
    harness.fixture.select_workspace(OTHER)
    status,payload = request_json(harness.fixture.base_url,
        f"/api/product/ask/runs/{started['result']['agent_run_id']}?workspace_id={harness.fixture.workspace_ids[ATLAS]}")
    assert status==409 and payload['error']['code']=='WORKSPACE_MISMATCH'


@pytest.mark.parametrize('scenario,stage',[('headers','http11.receive_response_headers.failed'),('body','http11.receive_response_body.failed')])
def test_unified_fourth_disconnect_is_uncertain_no_final_or_resend(harness,scenario,stage):
    harness.scenario=scenario
    _,started = ask(harness,'Why did Launch change suppliers?')
    status,payload = terminal(harness,started)
    result = payload['result']
    assert status==503 and result['status']=='PROVIDER_UNCERTAIN',result
    assert result['conclusions']==result['receipts']==result['evidence']==[]
    assert not result['reviewer']['accepted']
    run = harness.runs[0]
    assert len(run['transport'].bodies)==4
    assert any(x['event']==stage and x['generation_sequence']==4 for x in run['events'])
    rows = [json.loads(x) for x in run['ledger'].path.read_text().splitlines()]
    assert [x['status'] for x in rows if x['event']=='request_finished']==['SUCCESS_USAGE_REPORTED']*3+['UNCERTAIN']
    with pytest.raises(SmokeBlocked,match='TERMINAL_STOP'):
        run['client'].generate_content(model=MODEL,contents=[],config={})
    assert len(run['transport'].bodies)==4


def test_unified_sink_failure_keeps_business_ledger_success(harness):
    harness.broken_sink=True
    _,started = ask(harness,'Why did Launch change suppliers?')
    status,payload = terminal(harness,started)
    assert status==200 and payload['result']['status']=='COMPLETED'
    run = harness.runs[0]
    assert run['diagnostics'].failures>0
    rows = [json.loads(x) for x in run['ledger'].path.read_text().splitlines()]
    assert all(x['status']=='SUCCESS_USAGE_REPORTED' for x in rows if x['event']=='request_finished')


@pytest.mark.parametrize('scenario',['forged_receipt','wrong_evidence'])
def test_unified_transport_cannot_authorize_invalid_final(harness,scenario):
    harness.scenario=scenario
    _,started = ask(harness,'Why did Launch change suppliers?')
    status,payload = terminal(harness,started)
    assert status==500 and payload['result']['status']=='FAILED'
    assert payload['result']['conclusions']==payload['result']['receipts']==[]


def test_startup_diagnostic_failure_blocks_sdk_and_consumes_runtime_permission(harness,monkeypatch):
    import tests.integration.unified_transport_fixture as module
    def unavailable():
        raise OSError('PRIVATE_STARTUP_FAILURE')
    monkeypatch.setattr(module,'environment_snapshot',unavailable)
    with pytest.raises(OSError):
        harness.model_factory('Why did Launch change suppliers?')
    assert harness.sdk_constructions==0 and harness.runs==[]
    with pytest.raises(SmokeBlocked,match='RUNTIME_BUDGET_EXHAUSTED'):
        harness.model_factory('Why did Launch change suppliers?')
