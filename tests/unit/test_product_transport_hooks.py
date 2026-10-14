import httpx
from google.genai import types
from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
from scripts.gemini_transport.diagnostics import TransportDiagnostics


def test_product_builder_observes_final_wire_body_without_changing_it():
    bodies, events = [], []
    def fake(request):
        bodies.append(request.content)
        return httpx.Response(200, json={'candidates':[{'content':{'role':'model','parts':[{'text':'ok'}]}}]})
    for enabled in (False, True):
        diag = TransportDiagnostics(events.append, enabled=enabled)
        with build_gemini_sdk_client('FAKE_SECRET', transport_diagnostics=diag,
                http_options_kwargs={'retry_options':types.HttpRetryOptions(attempts=1),
                'client_args':{'transport':httpx.MockTransport(fake)}}) as sdk:
            sdk.models.generate_content(model='gemini-3.8-flash', contents='FAKE_PROMPT', config={
                'tools':[{'function_declarations':[{'name':'test','parameters_json_schema':{'type':'object','properties':{}}}]}],
                'automatic_function_calling':{'disable':True},
            })
    assert bodies[0] == bodies[1]
    assert b'parametersJsonSchema' in bodies[1]
    assert next(x for x in events if x['event']=='request_constructed')['body_bytes'] == len(bodies[1])


def test_diagnostic_identity_validation_cannot_block_business_operation():
    from scripts.gemini_transport.diagnostics import DiagnosticClient
    diag = TransportDiagnostics(lambda event: None)
    assert DiagnosticClient(None,diag).call('a long identity with spaces '*20,lambda:'ok')=='ok'


def test_product_environment_snapshot_redacts_proxy_credentials(monkeypatch):
    import json
    import urllib.request
    from scripts.gemini_transport.product import environment_snapshot
    monkeypatch.setattr(urllib.request,'getproxies',lambda:{'https':'http://PRIVATE_USER:PRIVATE_PASSWORD@PRIVATE_HOST:8080'})
    encoded = json.dumps(environment_snapshot())
    assert 'PRIVATE_' not in encoded
    assert '8080' in encoded


def test_runner_diagnostic_flag_alone_does_not_authorize_execute(monkeypatch):
    import sys
    from tests.smoke import run_temporal_product_http as runner
    monkeypatch.setattr(sys,'argv',['runner','T02','--transport-diagnostics'])
    monkeypatch.setattr(runner,'_freeze',lambda:({},{}))
    monkeypatch.setattr(runner,'_remaining',lambda *args:6)
    monkeypatch.setattr(runner,'_run',lambda *args,**kw:(_ for _ in ()).throw(AssertionError('no execute')))
    assert runner.main()==0
