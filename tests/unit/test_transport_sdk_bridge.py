import json
import httpx
import pytest
from scripts.gemini_transport.diagnostics import TransportDiagnostics, DiagnosticClient
from scripts.gemini_transport.sdk import build_diagnostic_sdk


def test_real_sdk_fake_transport_preserves_payload_and_never_retries():
    events, payloads = [], []
    fail = False
    def send(request):
        payloads.append(request.content)
        if fail:
            raise httpx.RemoteProtocolError('PRIVATE_API_KEY')
        return httpx.Response(200, json={'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'PRIVATE_RESPONSE'}]}}]}, headers={'x-request-id': 'sdk-fake-123'})
    for enabled in (False, True):
        diag = TransportDiagnostics(events.append, enabled=enabled)
        with build_diagnostic_sdk('PRIVATE_API_KEY', diag, transport=httpx.MockTransport(send)) as sdk:
            result = DiagnosticClient(sdk, diag).call('sdk-1', lambda: sdk.models.generate_content(model='gemini-3.8-flash', contents='PRIVATE_PROMPT'))
            assert result.text == 'PRIVATE_RESPONSE'
    assert payloads[0] == payloads[1]
    fail = True
    diag = TransportDiagnostics(events.append)
    with build_diagnostic_sdk('PRIVATE_API_KEY', diag, transport=httpx.MockTransport(send)) as sdk:
        with pytest.raises(httpx.RemoteProtocolError):
            DiagnosticClient(sdk, diag).call('sdk-2', lambda: sdk.models.generate_content(model='gemini-3.8-flash', contents='PRIVATE_PROMPT'))
    assert len(payloads) == 3
    assert not any(secret in json.dumps(events) for secret in ('PRIVATE_API_KEY','PRIVATE_PROMPT','PRIVATE_RESPONSE'))
