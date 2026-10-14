import json
import httpx
import pytest

from scripts.gemini_transport.diagnostics import TransportDiagnostics, DiagnosticClient
from scripts.gemini_transport.ledger import DiagnosticLedger, ExperimentStopped


def test_disabled_and_enabled_preserve_wire_request_and_response():
    captured = []
    def send(request):
        captured.append((request.content, dict(request.headers)))
        return httpx.Response(200, json={'ok': True}, headers={'x-request-id': 'provider-123'})
    events = []
    for enabled in (False, True):
        diag = TransportDiagnostics(events.append, enabled=enabled)
        with httpx.Client(transport=httpx.MockTransport(send), event_hooks=diag.hooks()) as client:
            wrapped = DiagnosticClient(client, diag)
            response = wrapped.call('request-1', lambda: client.post('https://example.invalid', content=b'PRIVATE_PROMPT', headers={'Authorization': 'SECRET_KEY'}))
            assert response.json() == {'ok': True}
    assert captured[0] == captured[1]
    encoded = json.dumps(events)
    assert 'PRIVATE_PROMPT' not in encoded and 'SECRET_KEY' not in encoded
    assert any(x['event'] == 'request_constructed' and x['body_bytes'] == 14 for x in events)
    assert any(len(x.get('provider_request_id_digest', '')) == 64 for x in events)
    assert any(x['event'] == 'response_complete' for x in events)


def test_original_exception_chain_and_uncertain_stop(tmp_path):
    events = []
    diag = TransportDiagnostics(events.append)
    def send(request):
        try:
            raise httpx.RemoteProtocolError('SECRET_KEY PRIVATE_PROMPT')
        except Exception as inner:
            raise httpx.RemoteProtocolError('Authorization: SECRET_KEY') from inner
    ledger = DiagnosticLedger(tmp_path / 'ledger.jsonl', experiment_id='offline-1', max_requests=4)
    with httpx.Client(transport=httpx.MockTransport(send), event_hooks=diag.hooks()) as client:
        wrapper = DiagnosticClient(client, diag)
        with pytest.raises(httpx.RemoteProtocolError):
            ledger.call(lambda rid: wrapper.call(rid, lambda: client.get('https://example.invalid')))
        with pytest.raises(ExperimentStopped):
            ledger.call(lambda rid: pytest.fail('must not send'))
    rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert rows[-1]['status'] == 'UNCERTAIN'
    assert len([x for x in rows if x['event'] == 'request_reserved']) == 1
    failure = next(x for x in events if x['event'] == 'call_failed')
    assert [x['class'] for x in failure['exception_chain']] == ['RemoteProtocolError', 'RemoteProtocolError']
    assert 'SECRET_KEY' not in json.dumps(events) and 'PRIVATE_PROMPT' not in json.dumps(events)


def test_diagnostic_sink_failure_does_not_change_result():
    def broken(_event):
        raise OSError('SECRET')
    diag = TransportDiagnostics(broken)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'ok')), event_hooks=diag.hooks()) as client:
        assert DiagnosticClient(client, diag).call('r1', lambda: client.get('https://example.invalid')).content == b'ok'
    assert diag.failures > 0


def test_stream_length_unknown_without_consuming_it():
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail('diagnostics must not read stream')
    events = []
    diag = TransportDiagnostics(events.append)
    request = httpx.Request('POST', 'https://example.invalid', stream=Stream())
    diag.request_hook(request)
    assert events[-1]['body_bytes'] == 'UNKNOWN'


def test_trace_fields_are_whitelisted_and_chained():
    events, original = [], []
    diag = TransportDiagnostics(events.append)
    request = httpx.Request('GET', 'https://example.invalid')
    request.extensions['trace'] = lambda name, info: original.append(name)
    diag.request_hook(request)
    request.extensions['trace']('http11.receive_response_headers.failed', {'exception': httpx.RemoteProtocolError('SECRET'), 'headers': {'Authorization': 'SECRET'}})
    request.extensions['trace']('SECRET_EVENT', {'secret': 'SECRET'})
    assert original == ['http11.receive_response_headers.failed', 'SECRET_EVENT']
    assert 'SECRET' not in json.dumps(events)


def test_ledger_budget_and_exclusive_identity(tmp_path):
    p = tmp_path / 'ledger.jsonl'
    ledger = DiagnosticLedger(p, experiment_id='offline', max_requests=1)
    assert ledger.call(lambda rid: 'ok') == 'ok'
    with pytest.raises(ExperimentStopped):
        ledger.call(lambda rid: 'bad')
    with pytest.raises(FileExistsError):
        DiagnosticLedger(p, experiment_id='offline', max_requests=1)
    request_rows = [json.loads(x) for x in (tmp_path/f'{ledger.experiment_id}-01.ledger.jsonl').read_text().splitlines()]
    assert request_rows[-1]['status'] == 'SUCCESS'


def test_ledger_write_failure_prevents_sending(tmp_path, monkeypatch):
    ledger = DiagnosticLedger(tmp_path/'ledger.jsonl', experiment_id='broken')
    def fail(_row):
        raise OSError('disk error')
    monkeypatch.setattr(ledger, 'append', fail)
    with pytest.raises(OSError):
        ledger.call(lambda rid: pytest.fail('must not send'))
    with pytest.raises(ExperimentStopped):
        ledger.call(lambda rid: pytest.fail('must not send'))


def test_prepare_never_constructs_or_calls_sdk(tmp_path, monkeypatch):
    from scripts.gemini_transport.prepare import prepare
    from google import genai
    monkeypatch.setattr(genai, 'Client', lambda **kw: pytest.fail('must not construct SDK'))
    packet = prepare(tmp_path/'packet', 'new-offline')
    assert packet['generation_requests_executed'] == 0
    assert packet['state'] == 'PREPARED_NOT_AUTHORIZED'
    with pytest.raises(FileExistsError):
        prepare(tmp_path/'packet', 'new-offline')


def test_untrusted_id_values_are_never_logged(tmp_path):
    events = []
    diag = TransportDiagnostics(events.append)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'ok', headers={'x-request-id': 'PRIVATE_API_KEY'})), event_hooks=diag.hooks()) as c:
        DiagnosticClient(c, diag).call('PRIVATE_PROMPT', lambda: c.get('https://example.invalid'))
    ledger = DiagnosticLedger(tmp_path/'ledger.jsonl', experiment_id='PRIVATE_API_KEY')
    ledger.call(lambda rid: 'ok')
    assert 'PRIVATE_API_KEY' not in json.dumps(events)
    assert 'PRIVATE_PROMPT' not in json.dumps(events)
    assert 'PRIVATE_API_KEY' not in ledger.path.read_text()
    forged = 'gnt-' + 'a' * 32 + '-01'
    DiagnosticClient(None, diag).call(forged, lambda: None)
    diag.emit('test', request_id='PRIVATE_API_KEY')
    assert forged not in json.dumps(events)
    assert 'PRIVATE_API_KEY' not in json.dumps(events)
    ledger.call(lambda rid: DiagnosticClient(None, diag).call(rid, lambda: None), diagnostics=diag)
    reservation = [json.loads(x) for x in ledger.path.read_text().splitlines() if json.loads(x)['event'] == 'request_reserved'][-1]
    assert reservation['diagnostic_request_id'] == events[-2]['request_id']
