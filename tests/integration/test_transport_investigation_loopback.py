"""Loopback-only tests of diagnostic observations, not Gemini reliability."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from scripts.gemini_transport.diagnostics import TransportDiagnostics, DiagnosticClient, proxy_snapshot
from scripts.gemini_transport.ledger import DiagnosticLedger, ExperimentStopped


@pytest.fixture
def local_server():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        calls = 0
        targets = []
        def log_message(self, *args):
            pass
        def do_GET(self):
            Handler.calls += 1
            Handler.targets.append(self.path)
            if self.path in ('/close', '/wait_close') or self.path == '/nth' and Handler.calls == 3:
                if self.path == '/wait_close':
                    time.sleep(.1)
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            partial = self.path == '/partial'
            self.send_response(200)
            self.send_header('Content-Length', '20' if partial else '2')
            self.send_header('x-request-id', 'local-request')
            self.end_headers()
            self.wfile.write(b'abc' if partial else b'OK')
            self.wfile.flush()
            if partial:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
            if self.path == '/idle':
                time.sleep(.05)
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}', Handler
    server.shutdown()
    server.server_close()
    worker.join(2)


def request(client, diag, url, identity='offline-1'):
    return DiagnosticClient(client, diag).call(identity, lambda: client.get(url))


def test_reused_and_new_clients_have_distinct_connection_observations(local_server):
    base, _ = local_server
    events = []
    diag = TransportDiagnostics(events.append)
    with httpx.Client(trust_env=False, timeout=60, event_hooks=diag.hooks()) as c:
        for n in (1, 2):
            assert request(c, diag, base + '/ok', f'r{n}').status_code == 200
    connections = [e['request_id'] for e in events if e['event'] == 'connection.connect_tcp.started']
    assert connections == [diag.safe_identity('r1')]
    assert any(e['event'] == 'http11.receive_response_body.complete' for e in events)
    for n in (3, 4):
        with httpx.Client(trust_env=False, timeout=60, event_hooks=diag.hooks()) as c:
            request(c, diag, base + '/ok', f'r{n}')
    assert [e['request_id'] for e in events if e['event'] == 'connection.connect_tcp.started'] == [diag.safe_identity(x) for x in ('r1', 'r3', 'r4')]


@pytest.mark.parametrize('path,stage', [('/close','http11.receive_response_headers.failed'),('/wait_close','http11.receive_response_headers.failed'),('/partial','http11.receive_response_body.failed')])
def test_diagnostics_distinguish_disconnect_stages(local_server, path, stage):
    base, _ = local_server
    events = []
    diag = TransportDiagnostics(events.append)
    with httpx.Client(trust_env=False, timeout=60, event_hooks=diag.hooks()) as c:
        with pytest.raises(httpx.RemoteProtocolError):
            request(c, diag, base + path)
    assert any(e['event'] == stage for e in events)
    assert any(e['event'] == 'http11.send_request_body.complete' for e in events)
    assert not any(e['event'] == 'response_complete' for e in events)
    if path == '/partial':
        assert any(e['event'] == 'response_headers' for e in events)
    assert any(len(e.get('exception_chain', [])) >= 2 for e in events if e['event'] == 'call_failed')


def test_expired_keepalive_is_visible_as_new_tcp(local_server):
    base, _ = local_server
    events = []
    diag = TransportDiagnostics(events.append)
    with httpx.Client(trust_env=False, timeout=60, event_hooks=diag.hooks()) as c:
        request(c, diag, base + '/idle', 'r1')
        time.sleep(.15)
        request(c, diag, base + '/ok', 'r2')
    assert [e['request_id'] for e in events if e['event'] == 'connection.connect_tcp.started'] == [diag.safe_identity(x) for x in ('r1', 'r2')]


def test_nth_failure_stops_ledger_without_retry(local_server, tmp_path):
    base, handler = local_server
    ledger = DiagnosticLedger(tmp_path/'ledger.jsonl', experiment_id='nth', max_requests=4)
    diag = TransportDiagnostics(lambda event: None)
    with httpx.Client(trust_env=False, timeout=60, event_hooks=diag.hooks()) as c:
        for _ in range(2):
            ledger.call(lambda rid: request(c, diag, base+'/nth', rid))
        with pytest.raises(httpx.RemoteProtocolError):
            ledger.call(lambda rid: request(c, diag, base+'/nth', rid))
        with pytest.raises(ExperimentStopped):
            ledger.call(lambda rid: request(c, diag, base+'/nth', rid))
    assert handler.calls == 3
    assert json.loads(ledger.path.read_text().splitlines()[-1])['status'] == 'UNCERTAIN'


@pytest.mark.parametrize('mode', ['explicit', 'environment', 'ignore_environment'])
def test_proxy_configuration_observes_actual_http_routing(local_server, monkeypatch, mode):
    base, handler = local_server
    monkeypatch.setenv('HTTP_PROXY', base)
    monkeypatch.setenv('NO_PROXY', '')
    diag = TransportDiagnostics(lambda event: None, proxy=base if mode == 'explicit' else None)
    options = {'proxy': base, 'trust_env': False} if mode == 'explicit' else {'trust_env': mode == 'environment'}
    with httpx.Client(timeout=60, event_hooks=diag.hooks(), **options) as c:
        url = base+'/ok' if mode == 'ignore_environment' else 'http://example.invalid/ok'
        assert request(c, diag, url).status_code == 200
    assert handler.targets == (['/ok'] if mode == 'ignore_environment' else ['http://example.invalid/ok'])


def test_proxy_authentication_is_not_in_snapshot():
    assert 'PASSWORD' not in json.dumps(proxy_snapshot('http://USER:PASSWORD@proxy.invalid:8080'))
