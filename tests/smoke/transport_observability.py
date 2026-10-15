"""Read-only HTTPX/httpcore route telemetry for the smoke harness.

No transport, timeout, retries, TLS, or environment settings are changed.
Only host/port and fixed phase labels are retained, never URLs or headers.
"""
from __future__ import annotations

import re
from typing import Any


def safe_hostname(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    if not isinstance(value, str) or len(value) > 253:
        return None
    return value if re.fullmatch(r"[a-zA-Z0-9_.:\-]+", value) else None


def safe_port(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 65535 else None


def configured_route(client: Any, url: Any) -> dict[str, Any]:
    """Inspect the installed HTTPX routing table without opening a connection."""
    route = {
        "target_hostname": safe_hostname(url.host),
        "target_port": safe_port(url.port) or (443 if url.scheme == "https" else 80),
        "configured_proxy_hostname": None,
        "configured_proxy_port": None,
        "route_inspection": "UNAVAILABLE",
    }
    try:
        transport = client._transport_for_url(url)
        pool = getattr(transport, "_pool", None)
        proxy = getattr(pool, "_proxy_url", None)
        if proxy is not None:
            route["configured_proxy_hostname"] = safe_hostname(proxy.host)
            route["configured_proxy_port"] = safe_port(proxy.port)
        route["route_inspection"] = "PROXY" if proxy is not None else (
            "DIRECT" if type(pool).__name__ == "ConnectionPool" else "UNAVAILABLE"
        )
    except (AttributeError, TypeError, ValueError, UnicodeError):
        pass
    return route


class TransportObserver:
    """Observe request routes and actual connect targets through trace callbacks."""

    def __init__(self, sdk_client: Any) -> None:
        self.records: list[dict[str, Any]] = []
        self.client = getattr(sdk_client, "_client", None)
        hooks = getattr(self.client, "event_hooks", None)
        if isinstance(hooks, dict):
            hooks.setdefault("request", []).append(self.on_request)

    def on_request(self, request: Any) -> None:
        record = {
            **configured_route(self.client, request.url),
            "connect_target_hostname": None,
            "connect_target_port": None,
            "connection_phase": None,
            "connection_outcome": None,
            "proxy_connect_status": None,
        }
        self.records.append(record)
        previous = request.extensions.get("trace")
        proxy_connect_pending = False

        def trace(event: str, info: dict[str, Any]) -> None:
            nonlocal proxy_connect_pending
            # Do not stringify arbitrary trace data: it may contain a request,
            # response headers, credentials, an exception, or an SSL object.
            if event in {
                "connection.connect_tcp.started",
                "connection.connect_tcp.complete",
                "connection.connect_tcp.failed",
            }:
                record["connection_phase"] = "connect_tcp"
                record["connection_outcome"] = event.rsplit(".", 1)[1]
                if event.endswith(".started"):
                    record["connect_target_hostname"] = safe_hostname(info.get("host"))
                    record["connect_target_port"] = safe_port(info.get("port"))
            elif event in {
                f"{prefix}.start_tls.{outcome}"
                for prefix in ("connection", "proxy")
                for outcome in ("started", "complete", "failed")
            }:
                record["connection_phase"] = "tls_handshake"
                record["connection_outcome"] = event.rsplit(".", 1)[1]
            elif event == "http11.send_request_headers.started" and getattr(
                info.get("request"), "method", None
            ) == b"CONNECT":
                proxy_connect_pending = True
                record["connection_phase"] = "proxy_connect"
                record["connection_outcome"] = "started"
            elif proxy_connect_pending and event in {
                "http11.send_request_headers.failed",
                "http11.receive_response_headers.failed",
                "http11.receive_response_headers.complete",
            }:
                record["connection_phase"] = "proxy_connect"
                record["connection_outcome"] = event.rsplit(".", 1)[1]
                response = info.get("return_value")
                if isinstance(response, tuple) and len(response) >= 2:
                    status = response[1]
                    if type(status) is int and 100 <= status <= 599:
                        record["proxy_connect_status"] = status
                proxy_connect_pending = False
            if previous is not None:
                previous(event, info)

        request.extensions["trace"] = trace
