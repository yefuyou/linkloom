"""Offline tests: no sockets or Provider requests are used."""
import json
from types import SimpleNamespace

import httpx

from tests.smoke.transport_observability import TransportObserver, configured_route


def test_effective_sdk_route_inspection_does_not_send_requests():
    # Explicit test proxy; construction/routing inspection never connects.
    with httpx.Client(proxy="http://private-user:private-password@127.0.0.1:7897") as client:
        result = configured_route(client, httpx.URL("https://api.deepseek.com/chat/completions?secret=value"))
    assert result == {
        "target_hostname": "api.deepseek.com", "target_port": 443,
        "configured_proxy_hostname": "127.0.0.1", "configured_proxy_port": 7897,
        "route_inspection": "PROXY",
    }
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_connect_failure_keeps_attempted_peer_and_drops_secrets():
    with httpx.Client(proxy="http://user:password@127.0.0.1:9") as client:
        observer = TransportObserver(SimpleNamespace(_client=client))
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions",
                                headers={"Authorization": "Bearer secret-key"},
                                json={"messages": [{"content": "private prompt"}]})
        # Invoke the same hook and httpcore events locally, without send().
        previous_events = []
        request.extensions["trace"] = lambda event, info: previous_events.append(event)
        observer.on_request(request)
        callback = request.extensions["trace"]
        callback("connection.connect_tcp.started", {"host": "127.0.0.1", "port": 9,
                                                      "password": "secret-password"})
        callback("connection.connect_tcp.failed", {"exception": ConnectionRefusedError("secret-key")})
        callback("http11.receive_response_headers.complete", {"headers": {"authorization": "secret-key"}})
        assert len(previous_events) == 3
    record = observer.records[0]
    assert record["connect_target_hostname"] == "127.0.0.1"
    assert record["connect_target_port"] == 9
    assert record["connection_phase"] == "connect_tcp"
    assert record["connection_outcome"] == "failed"
    encoded = json.dumps(record)
    assert all(value not in encoded for value in ("password", "secret", "private prompt", "Authorization"))


def test_tls_phase_is_distinct_from_connect_and_direct_route_has_null_proxy():
    with httpx.Client(trust_env=False) as client:
        observer = TransportObserver(SimpleNamespace(_client=client))
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        observer.on_request(request)
        callback = request.extensions["trace"]
        callback("connection.connect_tcp.started", {"host": b"api.deepseek.com", "port": 443})
        callback("connection.connect_tcp.complete", {})
        callback("connection.start_tls.failed", {"exception": RuntimeError("private")})
    record = observer.records[0]
    assert record["configured_proxy_hostname"] is None
    assert record["configured_proxy_port"] is None
    assert record["route_inspection"] == "DIRECT"
    assert record["connection_phase"] == "tls_handshake"
    assert record["connection_outcome"] == "failed"


def test_sdk_wrapper_captures_failure_without_retry_or_request_changes():
    import pytest
    from openai import APIConnectionError, OpenAI
    from tests.smoke.test_deepseek_real_provider_smoke import OpenAICompatibleDeepSeekClient

    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        request.extensions["trace"]("connection.connect_tcp.started", {"host": "127.0.0.1", "port": 9})
        request.extensions["trace"]("connection.connect_tcp.failed", {"exception": RuntimeError("private")})
        raise httpx.ConnectError("offline simulated connect failure", request=request)

    with OpenAI(api_key="offline-placeholder", base_url="https://api.deepseek.com",
                max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as sdk:
        wrapped = OpenAICompatibleDeepSeekClient(sdk)
        with pytest.raises(APIConnectionError):
            wrapped.create_chat_completion(model="deepseek-flash",
                messages=[{"role": "user", "content": "private prompt"}],
                thinking={"type": "disabled"})
        assert sdk.max_retries == 0
        assert len(calls) == 1
        assert calls[0]["messages"] == [{"role": "user", "content": "private prompt"}]
        assert wrapped.transport_observer.records[0]["connect_target_port"] == 9
        assert wrapped.transport_observer.records[0]["connection_outcome"] == "failed"
        assert "private" not in json.dumps(wrapped.transport_observer.records)


def test_proxy_connect_response_keeps_phase_and_status_without_headers():
    with httpx.Client(proxy="http://user:password@127.0.0.1:7897") as client:
        observer = TransportObserver(SimpleNamespace(_client=client))
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        observer.on_request(request)
        callback = request.extensions["trace"]
        callback("connection.connect_tcp.started", {"host": "127.0.0.1", "port": 7897})
        callback("connection.connect_tcp.complete", {})
        callback("http11.send_request_headers.started", {"request": SimpleNamespace(method=b"CONNECT")})
        callback("http11.receive_response_headers.complete", {
            "return_value": (b"HTTP/1.1", 502, b"private reason", [(b"secret", b"password")])})
    record = observer.records[0]
    assert record["connection_phase"] == "proxy_connect"
    assert record["proxy_connect_status"] == 502
    assert "private" not in json.dumps(record)
    assert "password" not in json.dumps(record)
