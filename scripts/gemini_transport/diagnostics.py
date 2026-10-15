"""Public HTTPX hooks and HTTPCore trace, with allowlisted diagnostic fields."""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import metadata
import re
import hashlib
import hmac
import secrets
import time
import math
from urllib.parse import urlsplit
import httpx
import httpcore

_EXCEPTIONS = {getattr(module, name): (module.__name__, name)
               for module in (httpx, httpcore)
               for name in ('RemoteProtocolError','LocalProtocolError','ConnectError','ReadError',
                            'WriteError','ProxyError','ConnectTimeout','ReadTimeout','WriteTimeout',
                            'PoolTimeout','UnsupportedProtocol','HTTPStatusError')
               if hasattr(module, name)}
_EXCEPTIONS.update({cls: ('builtins', cls.__name__) for cls in
                    (OSError, TimeoutError, ConnectionError, ConnectionResetError,
                     BrokenPipeError, RuntimeError, ValueError, TypeError)})

_identity = ContextVar('transport_identity', default=('UNKNOWN', 0, None))
_turn = ContextVar('transport_agent_turn', default={})


@contextmanager
def agent_turn(request):
    token = _turn.set({'agent_round': request.sequence,
                       'tool_history_count': len(request.tool_history),
                       'tool_turn_count': len(request.tool_turns)})
    try:
        yield
    finally:
        _turn.reset(token)
_TRACE = re.compile(r'^(connection\.(connect_tcp|start_tls)|proxy\.start_tls|http(11|2)\.(send_request_headers|send_request_body|receive_response_headers|receive_response_body|response_closed))\.(started|complete|failed)$')


def versions():
    result = {}
    for name in ('google-genai', 'httpx', 'httpcore'):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = 'UNAVAILABLE'
    return result


def proxy_snapshot(proxy):
    if not proxy:
        return {'proxy': 'ENVIRONMENT_DEPENDENT', 'connection_reuse': 'UNKNOWN', 'server_received': 'UNKNOWN'}
    parsed = urlsplit(proxy)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    scheme = parsed.scheme if parsed.scheme in ('http','https','socks5','socks5h') else 'UNKNOWN'
    return {'proxy_type': scheme, 'proxy_target': 'LOOPBACK' if local else 'REDACTED_REMOTE', 'proxy_port': parsed.port, 'connection_reuse': 'UNKNOWN', 'server_received': 'UNKNOWN'}


def exception_chain(exc):
    # Messages, args and traceback source lines can contain credentials.
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 8:
        seen.add(id(exc))
        module, name = _EXCEPTIONS.get(type(exc), ('OTHER', 'OTHER'))
        chain.append({'module': module, 'class': name, 'relation': 'cause' if exc.__cause__ is not None else 'context' if not exc.__suppress_context__ and exc.__context__ is not None else 'end'})
        exc = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
    return chain


class TransportDiagnostics:
    def __init__(self, sink, *, enabled=True, proxy=None):
        self.sink, self.enabled = sink, enabled
        self.route, self.dependencies = proxy_snapshot(proxy), versions()
        self._salt = secrets.token_bytes(32)
        self.failures = 0

    def safe_identity(self, value):
        return 'external-' + hmac.new(self._salt, value.encode(), hashlib.sha256).hexdigest()

    def emit(self, event, **fields):
        if not self.enabled:
            return
        try:
            if event not in ('call_started','call_failed','response_complete','request_constructed','response_headers') and not _TRACE.fullmatch(event):
                return
            identity, started, sequence = _identity.get()
            safe = {}
            for key in ('body_bytes','connection_port','http_status'):
                value = fields.get(key)
                if type(value) is int and 0 <= value <= 20000000:
                    safe[key] = value
                elif key in fields:
                    safe[key] = 'UNKNOWN'
            if type(fields.get('stage_ms')) in (int,float) and math.isfinite(fields['stage_ms']) and fields['stage_ms'] >= 0:
                safe['stage_ms'] = fields['stage_ms']
            enums = {'proxy':('ENVIRONMENT_DEPENDENT',), 'proxy_type':('http','https','socks5','socks5h'),
                     'proxy_target':('LOOPBACK','REDACTED_REMOTE'), 'connection_target':('LOOPBACK','REDACTED_REMOTE'),
                     'connection_reuse':('UNKNOWN',), 'server_received':('UNKNOWN',),
                     'http_version':('HTTP/1.0','HTTP/1.1','HTTP/2')}
            for key, allowed in enums.items():
                if key in fields:
                    safe[key] = fields[key] if fields[key] in allowed else 'UNKNOWN'
            if 'proxy_port' in fields:
                value = fields['proxy_port']
                safe['proxy_port'] = value if type(value) is int and 1 <= value <= 65535 else 'UNKNOWN'
            digest = fields.get('provider_request_id_digest')
            if 'provider_request_id_digest' in fields:
                safe['provider_request_id_digest'] = digest if isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest) else 'UNKNOWN'
            if isinstance(fields.get('versions'),dict):
                safe['versions'] = {key:value if isinstance(value,str) and re.fullmatch(r'\d+(\.\d+){1,3}',value) else 'UNAVAILABLE'
                                    for key,value in fields['versions'].items() if key in ('google-genai','httpx','httpcore')}
            if isinstance(fields.get('exception_chain'),list):
                known = set(_EXCEPTIONS.values())
                safe['exception_chain'] = [{'module':item.get('module') if (item.get('module'),item.get('class')) in known else 'OTHER',
                                            'class':item.get('class') if (item.get('module'),item.get('class')) in known else 'OTHER',
                                            'relation':item.get('relation') if item.get('relation') in ('cause','context','end') else 'end'}
                                           for item in fields['exception_chain'][:8] if isinstance(item,dict)]
            turn = {key:value for key,value in _turn.get().items() if key in ('agent_round','tool_history_count','tool_turn_count') and type(value) is int and 0 <= value <= 10000}
            self.sink({**safe, **turn, 'generation_sequence': sequence if type(sequence) is int and 1 <= sequence <= 10000 else 'UNKNOWN', 'event': event, 'request_id': self.safe_identity(identity), 'utc': datetime.now(timezone.utc).isoformat(), 'elapsed_ms': round((time.monotonic() - started) * 1000, 3) if started else None})
        except Exception:
            self.failures += 1

    def hooks(self):
        return {'request': [self.request_hook], 'response': [self.response_hook]} if self.enabled else {}

    def request_hook(self, request):
        if not self.enabled:
            return
        try:
            try:
                length = len(request.content)
            except httpx.RequestNotRead:
                length = 'UNKNOWN'
            self.emit('request_constructed', body_bytes=length, versions=self.dependencies, **self.route)
            original = request.extensions.get('trace')
            stages = {}
            def trace(name, info):
                if original:
                    original(name, info)
                try:
                    if _TRACE.fullmatch(name):
                        fields = {}
                        stage, suffix = name.rsplit('.', 1)
                        if suffix == 'started':
                            stages[stage] = time.monotonic()
                        elif stage in stages:
                            fields['stage_ms'] = round((time.monotonic() - stages.pop(stage)) * 1000, 3)
                        if name == 'connection.connect_tcp.started':
                            fields['connection_target'] = 'LOOPBACK' if info.get('host') in ('127.0.0.1', 'localhost', '::1', b'127.0.0.1', b'localhost', b'::1') else 'REDACTED_REMOTE'
                            if isinstance(info.get('port'), int):
                                fields['connection_port'] = info['port']
                        if name.endswith('.failed') and isinstance(info.get('exception'), BaseException):
                            fields['exception_chain'] = exception_chain(info['exception'])
                        self.emit(name, **fields)
                except Exception:
                    self.failures += 1
            request.extensions['trace'] = trace
        except Exception:
            self.failures += 1

    def response_hook(self, response):
        try:
            provider_id = 'UNKNOWN'
            for header in ('x-request-id', 'x-goog-request-id'):
                value = response.headers.get(header, '')
                if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
                    provider_id = hmac.new(self._salt, value.encode(), hashlib.sha256).hexdigest()
                    break
            self.emit('response_headers', http_status=response.status_code, http_version=response.http_version, provider_request_id_digest=provider_id)
        except Exception:
            self.failures += 1


class DiagnosticClient:
    """Wrap synchronous non-stream SDK calls; preserve the original exception."""
    def __init__(self, client, diagnostics):
        self.client, self.diagnostics = client, diagnostics

    def call(self, request_id, operation, *, generation_sequence=None):
        # Identity is always hashed by emit; logging must not reject a valid operation.
        token = _identity.set((request_id, time.monotonic(), generation_sequence))
        def evidence(event, exception=None):
            try:
                self.diagnostics.emit(event, **({'exception_chain': exception_chain(exception)} if exception is not None else {}))
            except Exception:
                pass
        try:
            evidence('call_started')
            try:
                result = operation()
            except BaseException as exc:
                evidence('call_failed', exc)
                raise
            evidence('response_complete')
            return result
        finally:
            _identity.reset(token)
