"""Optional instrumentation for the existing product Adapter and SDK boundary."""
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from .diagnostics import DiagnosticClient, agent_turn


def environment_snapshot():
    """Local configuration only, no DNS/network calls or raw environment values."""
    import os
    import ssl
    import sys
    import urllib.request
    from .diagnostics import proxy_snapshot, versions
    proxies = {}
    for scheme, value in urllib.request.getproxies().items():
        if scheme in ('http', 'https', 'all'):
            try:
                proxies[scheme] = proxy_snapshot(value)
            except Exception:
                proxies[scheme] = {'proxy': 'UNKNOWN'}
    registry = {'state': 'UNAVAILABLE'}
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
                registry = {'proxy_enabled': bool(winreg.QueryValueEx(key, 'ProxyEnable')[0])}
        except OSError:
            pass
    return {'versions': versions(), 'python': sys.version, 'tls': ssl.OPENSSL_VERSION,
            'resolved_proxies': proxies, 'windows_user_proxy': registry,
            'process_proxy_variables_set': {name: bool(os.environ.get(name)) for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')},
            'tun_path': 'UNKNOWN', 'winhttp_proxy': 'UNKNOWN',
            'tls_verification': True, 'http_options': {'timeout_ms':130000,'sdk_attempts':1},
            'connection_reuse_id':'UNKNOWN', 'server_received':'UNKNOWN'}


class TransportGeminiProviderAdapter(GeminiProviderAdapter):
    def complete(self, request):
        with agent_turn(request):
            return super().complete(request)


def diagnosed_generation(operation, diagnostics, ledger):
    # Invoked by Ledger.request only AFTER its durable reservation.
    identity = f'{ledger.run_id}-http-{ledger.request_count:02d}'
    return DiagnosticClient(None, diagnostics).call(identity, operation,
        generation_sequence=ledger.request_count)


def product_adapter(client, diagnostics, *, model_id):
    adapter_type = TransportGeminiProviderAdapter if diagnostics is not None and diagnostics.enabled else GeminiProviderAdapter
    return adapter_type(client, model_id=model_id)
