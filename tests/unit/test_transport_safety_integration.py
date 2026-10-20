import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import httpx
import pytest

from scripts.gemini_transport.diagnostics import TransportDiagnostics, DiagnosticClient, agent_turn, exception_chain, proxy_snapshot


def test_untrusted_type_metadata_and_proxy_scheme_never_leak():
    forged = type('PRIVATE_SECRET', (Exception,), {'__module__':'httpx.PRIVATE_SECRET'})
    encoded = json.dumps(exception_chain(forged('PRIVATE_PROMPT')))
    assert 'PRIVATE_' not in encoded
    assert 'PRIVATE_' not in json.dumps(proxy_snapshot('PRIVATE_SECRET://user:pass@proxy.invalid:8080'))


def test_concurrent_threads_keep_request_and_turn_context_isolated():
    events = []
    diag = TransportDiagnostics(events.append)
    barrier = Barrier(2)
    def operation(number):
        request = SimpleNamespace(sequence=number,tool_history=[1]*number,tool_turns=[1]*number)
        def send():
            barrier.wait(timeout=5)
            diag.emit('request_constructed',body_bytes=number)
            if number==2:
                raise httpx.RemoteProtocolError('SECRET')
            return 'ok'
        with agent_turn(request):
            try:
                DiagnosticClient(None,diag).call(f'private-{number}',send,generation_sequence=number)
            except httpx.RemoteProtocolError:
                pass
        diag.emit('call_started')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(operation,[1,2]))
    for n in (1,2):
        group = [x for x in events if x['generation_sequence']==n]
        assert group
        assert all(x['request_id']==diag.safe_identity(f'private-{n}') and x['agent_round']==n for x in group)
    restored = [x for x in events if x['generation_sequence']=='UNKNOWN']
    assert len(restored)==2 and all('agent_round' not in x for x in restored)
    assert 'private-' not in json.dumps(events) and 'SECRET' not in json.dumps(events)


def test_direct_emit_cannot_log_untrusted_fields_events_or_sequence():
    events = []
    diag = TransportDiagnostics(events.append)
    diag.emit('PRIVATE_EVENT', prompt='PRIVATE_PROMPT')
    diag.emit('response_headers', prompt='PRIVATE_PROMPT', http_version='PRIVATE_HEADER',
              provider_request_id_digest='PRIVATE_KEY', exception_chain=[{'module':'httpx','class':'PRIVATE_CLASS','relation':'PRIVATE'}])
    DiagnosticClient(None,diag).call('safe',lambda:None,generation_sequence='PRIVATE_SEQUENCE')
    assert 'PRIVATE' not in json.dumps(events)
    assert all(x['generation_sequence']=='UNKNOWN' for x in events)
