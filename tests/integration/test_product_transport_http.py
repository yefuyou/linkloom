"""Formal product HTTP -> Runtime -> official SDK -> injected FakeTransport."""
import hashlib
import json
from time import monotonic, sleep
import httpx
import pytest
from google.genai import types

from linkloom.agents.providers.gemini_api import build_gemini_sdk_client, GeminiProviderAdapter
from linkloom.ui.investigation import ProductInvestigationService
from scripts.gemini_transport.diagnostics import TransportDiagnostics
from scripts.gemini_transport.product import TransportGeminiProviderAdapter
from tests.smoke.test_p14_cn2_typed_gemini38_smoke import AppendOnlyProviderLedger, LedgeredGeminiClient, SmokeBlocked
from tests.smoke import run_temporal_product_http as runner


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


SETUP = {'workspace':'Offline transport lab', 'subject':'Launch', 'relation':'uses supplier', 'sources':[
    {'name':'prior.md','content':'[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for Launch.', 'claim_type':'DECISION','value':'Birchline','review':'approve'},
    {'name':'current.md','content':'[2026-10-07T10:00:00Z] Alice: Wrenwell replaced Birchline on 2026-10-07 as the current supplier for Launch.', 'claim_type':'DECISION','value':'Wrenwell','valid_from':'2026-10-07','review':'approve'},
    {'name':'proposal.md','content':'[2026-10-09T10:00:00Z] Alice: Cedar was proposed; no formal change was approved.', 'claim_type':'PROPOSAL','value':'Cedar','review':'leave_unapproved'},
]}


class FakeTransport(httpx.BaseTransport):
    """Synthetic stage events; no TCP/TLS. Actual trace mapping also has loopback tests."""
    def __init__(self, failure=None):
        self.failure, self.bodies, self.responses = failure, [], []

    def handle_request(self, request):
        body = json.loads(request.content)
        self.bodies.append(request.content)
        n = len(self.bodies)
        trace = request.extensions.get('trace', lambda *_: None)
        for stage in ('send_request_headers', 'send_request_body'):
            trace(f'http11.{stage}.started', {})
            trace(f'http11.{stage}.complete', {})
        trace('http11.receive_response_headers.started', {})
        if n == 4 and self.failure == 'headers':
            exc = httpx.RemoteProtocolError('FAKE_SECRET_PROMPT')
            trace('http11.receive_response_headers.failed', {'exception':exc})
            raise exc
        trace('http11.receive_response_headers.complete', {})
        if n == 1:
            part = {'functionCall':{'id':'c1','name':'search_decision_memory','args':{'query':'current supplier','limit':5}}}
        elif n == 2:
            text = next(x['text'] for x in walk(body) if 'team_decision_request=' in x.get('text',''))
            context = json.JSONDecoder().raw_decode(text.split('team_decision_request=',1)[1])[0]['source_context']
            part = {'functionCall':{'id':'c2','name':'search_notes','args':{'query':'Birchline Wrenwell supplier Launch','source_context':context,'limit':5}}}
        elif n in (3,4):
            search = next(x['functionResponse']['response']['value'] for x in walk(body)
                          if x.get('functionResponse',{}).get('name')=='search_notes')
            assert len(search)>=2, [list(x) for x in search]
            item = search[n-3]
            part = {'functionCall':{'id':f'c{n}','name':'read_verified_note','args':{'note_ref':item['evidence_id']}}}
        else:
            view = next(x for x in walk(body) if 'effective' in x and 'history' in x)
            reads = [x for x in walk(body) if 'evidence_id' in x and 'full_text' in x]
            # Verified read results may expose quote instead of full_text.
            if not reads:
                reads = [x for x in walk(body) if 'evidence_id' in x and 'quote' in x]
            current = next(x for x in reads if 'Wrenwell replaced' in (x.get('full_text','')+x.get('quote','')))
            prior = next(x for x in reads if 'use Birchline' in (x.get('full_text','')+x.get('quote','')))
            effective = view['effective']
            final = {'schema_version':'claim-proposal/v1','claims':[], 'temporal_decision':{
                'subject':'Launch','relation':'uses supplier','as_of':view['as_of'],
                'decision_id':effective['decision_id'],'receipt_id':effective['review_receipt']['receipt_id'],
                'value':'Wrenwell','supersedes_id':effective['supersedes_id'],
                'evidence_ids':[current['evidence_id']],'prior_evidence_ids':[prior['evidence_id']],
            },'uncertainty':{'status':'insufficient_evidence','unknown_fields':['replacement_reason']}}
            part = {'text':json.dumps(final)}
        response = json.dumps({'modelVersion':runner.MODEL,'usageMetadata':{'promptTokenCount':100+n,'candidatesTokenCount':20,'thoughtsTokenCount':0},
            'candidates':[{'content':{'role':'model','parts':[part]},'finishReason':'STOP'}]}).encode()
        self.responses.append(response)
        failure = n == 4 and self.failure == 'body'
        class Body(httpx.SyncByteStream):
            def __iter__(self):
                trace('http11.receive_response_body.started', {})
                if failure:
                    yield response[:10]
                    exc = httpx.RemoteProtocolError('FAKE_SECRET_RESPONSE')
                    trace('http11.receive_response_body.failed', {'exception':exc})
                    raise exc
                yield response
                trace('http11.receive_response_body.complete', {})
            def close(self):
                trace('http11.response_closed.started', {})
                trace('http11.response_closed.complete', {})
        return httpx.Response(200, headers={'x-request-id':f'fake-provider-{n}'}, stream=Body())


def run_http(tmp_path, monkeypatch, *, failure=None, broken_diagnostics=False):
    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE','bm25')
    base, server, worker, app, workspace = runner._setup(tmp_path, SETUP)
    events, turns = [], []
    def sink(event):
        if broken_diagnostics:
            raise OSError('FAKE_SECRET_SINK')
        events.append(event)
    diagnostics = TransportDiagnostics(sink)
    if broken_diagnostics:
        diagnostics.request_hook = lambda _: (_ for _ in ()).throw(OSError('FAKE_SECRET_HOOK'))
    transport = FakeTransport(failure)
    sdk = build_gemini_sdk_client('FAKE_SECRET_KEY', transport_diagnostics=diagnostics,
        http_options_kwargs={'timeout':130000,'retry_options':types.HttpRetryOptions(attempts=1),'client_args':{'transport':transport}})
    ledger = AppendOnlyProviderLedger(tmp_path/'provider.jsonl', batch_id='offline-http',run_id='offline-http',max_requests=6,max_runtime_runs=1,max_input_tokens_per_request=None)
    client = LedgeredGeminiClient(sdk,ledger,transport_diagnostics=diagnostics)
    class Capture(TransportGeminiProviderAdapter):
        def complete(self, request):
            turns.append(request)
            return super().complete(request)
    service = ProductInvestigationService(app,model_factory=lambda _:Capture(client,model_id=runner.MODEL),max_steps=8)
    server.investigation_service = service
    ledger.runtime_started()
    try:
        status, snapshot = runner._client(base,'/api/agent/decisions/ask',method='POST',data={
            'query':'为什么从 Birchline 换成 Wrenwell？现在正式执行哪个方案？', 'workspace_id':workspace,
            'subject':'Launch','relation':'uses supplier','as_of':'2026-10-10'})
        assert status == 202
        run_id = snapshot['run']['id']
        deadline = monotonic()+60
        while snapshot['kind']=='running' and monotonic()<deadline:
            sleep(.02)
            _, snapshot = runner._client(base,f'/api/agent/decisions/runs/{run_id}')
        job = service._jobs[run_id]
        artifact = job.backend._safe_result_artifact(job.backend.engine.inspect(snapshot['run']['thread_id']))
        if failure:
            assert snapshot['kind']=='error'
            assert len(transport.bodies)==4
            assert ledger.halted
            with pytest.raises(SmokeBlocked,match='TERMINAL_STOP'):
                client.generate_content(model=runner.MODEL,contents=[],config={})
        else:
            assert snapshot['kind'] in ('success','insufficient'), snapshot
            assert runner._score(snapshot,artifact,{'effective':'Wrenwell','replaces':'Birchline','replacement_reason':'NOT_SOURCE_VERIFIED','forbidden_current':['Cedar']})['label']=='ANSWERED_CORRECT'
            assert snapshot['metrics']['read_count']==2
            assert artifact['result']['review_decision']=='evidence_sufficient'
            # Diagnostic insertion cannot authorize an unreviewed value/forged receipt.
            effective = snapshot['temporal_decision']['effective']
            assert effective['authorization']['authorization_type']=='HUMAN_APPROVAL'
            assert effective['evidence'][0]['verified'] is True
            assert snapshot['temporal_decision']['proposals'][0]['value']=='Cedar'
            assert snapshot['temporal_decision']['proposals'][0]['status']=='PROPOSED_NOT_APPROVED'
        rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
        if not broken_diagnostics:
            for row in rows:
                if row['event']=='request_reserved':
                    group = [x for x in events if x['generation_sequence']==row['request_sequence']]
                    assert group and all(x['request_id']==diagnostics.safe_identity(row['request_id']) for x in group)
                    for event in group:
                        if event['event']=='response_headers':
                            assert event['provider_request_id_digest']==diagnostics.safe_identity(f"fake-provider-{row['request_sequence']}").removeprefix('external-')
        return events, turns, transport, rows
    finally:
        ledger.close()
        sdk.close()
        runner._stop_product_server(server,worker,app)


def test_formal_http_runtime_payload_and_ledger_are_unchanged(tmp_path, monkeypatch):
    events, turns, transport, rows = run_http(tmp_path,monkeypatch)
    replay_bodies = []
    def disabled(request):
        replay_bodies.append(request.content)
        return httpx.Response(200,content=transport.responses[len(replay_bodies)-1])
    with build_gemini_sdk_client('FAKE_SECRET_KEY',http_options_kwargs={'timeout':130000,'retry_options':types.HttpRetryOptions(attempts=1),'client_args':{'transport':httpx.MockTransport(disabled)}}) as sdk:
        from linkloom.ui.__main__ import GeminiSDKClientBridge
        adapter = GeminiProviderAdapter(GeminiSDKClientBridge(sdk),model_id=runner.MODEL)
        for turn in turns:
            assert adapter.complete(turn).error is None
    assert replay_bodies == transport.bodies  # Exact bytes of actual formal Runtime turns.
    assert len(turns)==5
    for n in range(1,6):
        group = [x for x in events if x['generation_sequence']==n]
        assert any(x['event']=='request_constructed' and x['body_bytes']==len(transport.bodies[n-1]) for x in group)
        assert all(x['agent_round']==turns[n-1].sequence for x in group)
        assert any(x['event']=='response_headers' and x['provider_request_id_digest']!='UNKNOWN' for x in group)
        assert any(x['event']=='http11.receive_response_body.complete' for x in group)
    assert all(x['status']=='SUCCESS_USAGE_REPORTED' for x in rows if x['event']=='request_finished')
    assert 'FAKE_SECRET' not in json.dumps(events)
    assert next(x for x in events if x['generation_sequence']==4)['tool_turn_count']>=3
    assert sum(1 for x in walk(json.loads(transport.bodies[3])) if 'functionResponse' in x)>=3


@pytest.mark.parametrize('failure,stage',[('headers','http11.receive_response_headers.failed'),('body','http11.receive_response_body.failed')])
def test_formal_http_fourth_request_failure_stage_and_no_resend(tmp_path, monkeypatch, failure, stage):
    events, turns, transport, rows = run_http(tmp_path,monkeypatch,failure=failure)
    assert any(x['event']==stage and x['generation_sequence']==4 for x in events)
    assert [x['status'] for x in rows if x['event']=='request_finished']==['SUCCESS_USAGE_REPORTED']*3+['UNCERTAIN']
    assert len(transport.bodies)==4
    assert 'FAKE_SECRET' not in json.dumps(events)


def test_formal_http_diagnostics_failure_does_not_pollute_business_ledger(tmp_path,monkeypatch):
    _, _, transport, rows = run_http(tmp_path,monkeypatch,broken_diagnostics=True)
    assert len(transport.bodies)==5
    assert all(x['status']=='SUCCESS_USAGE_REPORTED' for x in rows if x['event']=='request_finished')
