"""Safe Gemini-specific diagnostics layered on shared provider metadata."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import errno
import re
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

from linkloom.agents.providers.failure_diagnostics import (
    exception_failure_diagnostics,
    response_failure_diagnostics,
    safe_diagnostic_text,
)


_MAX_CAUSES = 8
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,253}$")
_PROVIDER_STATUS_CLASSES = {
    "RESOURCE_EXHAUSTED": "PROVIDER_QUOTA",
    "RATE_LIMIT_EXCEEDED": "PROVIDER_RATE_LIMIT",
    "ABORTED": "PROVIDER_ABORTED",
    "UNAVAILABLE": "PROVIDER_OVERLOADED",
    "OVERLOADED": "PROVIDER_OVERLOADED",
}


def _read(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    try:
        return getattr(value, name, default)
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
        return default


def _exception_chain(exception: BaseException | None) -> list[BaseException]:
    if exception is None:
        return []
    result = [exception]
    seen = {id(exception)}
    current = exception
    while len(result) < _MAX_CAUSES + 1:
        cause = current.__cause__
        context = current.__context__
        next_value = cause if cause is not None else context
        if next_value is None:
            reason = _read(current, "reason")
            next_value = reason if isinstance(reason, BaseException) else None
        if next_value is None or id(next_value) in seen:
            break
        result.append(next_value)
        seen.add(id(next_value))
        current = next_value
    return result


def _provider_status_code(value: Any) -> str | None:
    """Read only structured provider status/code fields, never free-form text."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        for name in ("status", "reason", "type", "code"):
            candidate = _read(current, name)
            if isinstance(candidate, str):
                normalized = candidate.strip().upper()
                if normalized in _PROVIDER_STATUS_CLASSES:
                    return normalized
        body = _read(current, "body")
        if isinstance(body, Mapping):
            pending.append(body.get("error", body))
        elif isinstance(current, Mapping):
            pending.append(current.get("error"))
    return None


def _provider_error_code(value: Any) -> str | None:
    """Return a bounded structured Provider code/status, excluding HTTP numbers."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        for name in ("status", "reason", "type", "code"):
            candidate = _read(current, name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:128]
        body = _read(current, "body")
        if isinstance(body, Mapping):
            pending.append(body.get("error", body))
        elif isinstance(current, Mapping):
            pending.append(current.get("error"))
    return None


def _status_number(value: Any) -> int | None:
    for candidate in (value, _read(value, "response")):
        for name in ("status_code", "http_status", "code"):
            status = _read(candidate, name)
            if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
                return status
    return None


def _response_finish_class(finish_reason: Any) -> str | None:
    value = getattr(finish_reason, "value", finish_reason)
    if not isinstance(value, str):
        return None
    return {
        "SAFETY": "PROVIDER_SAFETY_BLOCK",
        "RECITATION": "PROVIDER_SAFETY_BLOCK",
        "ABORTED": "PROVIDER_ABORTED",
        "MAX_TOKENS": "PROVIDER_INCOMPLETE",
    }.get(value.strip().upper())


def _http_class(status: int) -> str:
    if status in {400, 401, 403, 404, 429}:
        return f"HTTP_{status}"
    if 400 <= status < 500:
        return "HTTP_4XX"
    if 500 <= status < 600:
        return "HTTP_5XX"
    return "UNKNOWN_PROVIDER_FAILURE"


def _exception_classification(
    exception: BaseException,
    base: Mapping[str, Any],
) -> tuple[str, str]:
    structured_status = _provider_status_code(exception)
    if structured_status is not None:
        status = base.get("http_status")
        return (
            _PROVIDER_STATUS_CLASSES[structured_status],
            "HTTP" if isinstance(status, int) else "PROVIDER_RESPONSE",
        )

    status = base.get("http_status")
    if isinstance(status, int):
        return _http_class(status), "HTTP"

    nodes = _exception_chain(exception)
    typed_nodes = [
        (node, type(node).__name__, type(node).__module__.casefold())
        for node in nodes
    ]
    if any(
        name in {"ProxyError", "ProxyConnectionError"}
        and module.startswith(("httpx", "httpcore"))
        for _, name, module in typed_nodes
    ):
        return "TRANSPORT_PROXY_CONNECT", "PROXY_CONNECT"
    if any(isinstance(node, ssl.SSLError) for node in nodes):
        return "TRANSPORT_TLS", "TRANSPORT"
    if any(
        isinstance(node, socket.gaierror)
        or (
            isinstance(getattr(node, "errno", None), int)
            and getattr(node, "errno", None)
            in {getattr(socket, "EAI_NONAME", None), getattr(socket, "EAI_AGAIN", None)}
        )
        for node in nodes
    ):
        return "TRANSPORT_DNS", "TRANSPORT"
    if any(
        isinstance(node, TimeoutError)
        or name in {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"}
        for node, name, _ in typed_nodes
    ):
        return "TRANSPORT_TIMEOUT", "TRANSPORT"
    if any(
        isinstance(node, ConnectionError)
        or name == "ConnectError"
        or getattr(node, "errno", None)
        in {errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH, errno.ENETUNREACH}
        or getattr(node, "winerror", None) in {10061, 10060, 10054, 10065}
        for node, name, _ in typed_nodes
    ):
        return "TRANSPORT_CONNECT", "TRANSPORT"
    if any(
        module.startswith("google.") or module.startswith(("httpx", "httpcore"))
        for _, _, module in typed_nodes
    ):
        return "SDK_ERROR", "SDK"
    return "UNKNOWN_PROVIDER_FAILURE", "UNKNOWN"


def _response_classification(
    response: Any,
    *,
    provider_error: Any = None,
    finish_reason: Any = None,
    parse_error: bool = False,
    status: int | None = None,
) -> tuple[str, str]:
    finish_class = _response_finish_class(finish_reason)
    if finish_class is not None:
        return finish_class, "PROVIDER_RESPONSE"

    structured_status = _provider_status_code(provider_error)
    if structured_status is None:
        structured_status = _provider_status_code(response)
    if structured_status is not None:
        return (
            _PROVIDER_STATUS_CLASSES[structured_status],
            "HTTP" if status is not None else "PROVIDER_RESPONSE",
        )

    if status is not None:
        return _http_class(status), "HTTP"
    if provider_error is not None:
        return "PROVIDER_FAILED_RESPONSE", "PROVIDER_RESPONSE"
    if parse_error:
        return "RESPONSE_PARSE_ERROR", "SDK_PARSE"
    return "PROVIDER_FAILED_RESPONSE", "PROVIDER_RESPONSE"


def _payload_for_redaction(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Provide sensitive request strings to the shared redactor in memory only."""
    strings: list[str] = []
    pending: list[Any] = [payload.get("config"), payload.get("contents")]
    while pending and len(strings) < 4096:
        value = pending.pop()
        if isinstance(value, Mapping):
            pending.extend(reversed(list(value.values())))
        elif isinstance(value, (list, tuple)):
            pending.extend(reversed(value))
        elif isinstance(value, str) and value:
            strings.append(value)
    # The shared walker consumes lists in reverse; reverse here so user-visible
    # contents and tool observations are retained ahead of schema strings.
    return {"content": list(reversed(strings))}


def _request_shape(payload: Mapping[str, Any]) -> dict[str, Any]:
    contents = payload.get("contents")
    config = payload.get("config")
    roles: Counter[str] = Counter()
    part_types: Counter[str] = Counter()
    thought_signatures = 0
    if isinstance(contents, (list, tuple)):
        for item in contents:
            role = _read(item, "role")
            if isinstance(role, str):
                roles[role] += 1
            parts = _read(item, "parts", [])
            if isinstance(parts, (list, tuple)):
                for part in parts:
                    if isinstance(part, Mapping):
                        part_types.update(str(key) for key in part)
                        thought_signatures += int("thought_signature" in part)
    declarations = []
    tools = _read(config, "tools", [])
    if isinstance(tools, (list, tuple)):
        for tool in tools:
            declarations.extend(_read(tool, "function_declarations", []) or [])
    return {
        "contents_count": len(contents) if isinstance(contents, (list, tuple)) else 0,
        "role_counts": dict(sorted(roles.items())),
        "part_type_counts": dict(sorted(part_types.items())),
        "system_instruction_present": _read(config, "system_instruction") is not None,
        "tool_declaration_count": len(declarations),
        "thought_signature_count": thought_signatures,
        "provider_continuation_present": bool(
            part_types.get("function_call") or part_types.get("function_response")
        ),
        "generation_config_fields": sorted(
            str(key) for key in config
        ) if isinstance(config, Mapping) else [],
    }


def _client_chain(client: Any) -> list[Any]:
    pending = [client]
    result: list[Any] = []
    seen: set[int] = set()
    while pending and len(result) < 24:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        for name in (
            "delegate", "_delegate", "client", "_client", "sdk_client",
            "_sdk_client", "_api_client", "_httpx_client", "models",
        ):
            nested = _read(current, name)
            if nested is not None and not isinstance(nested, (str, bytes, int, float, bool)):
                pending.append(nested)
    return result


def _host_port(url: Any) -> tuple[str | None, int | None]:
    host = _read(url, "host")
    port = _read(url, "port")
    scheme = _read(url, "scheme")
    if not isinstance(host, str):
        if not isinstance(url, str):
            return None, None
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
            scheme = parsed.scheme
        except ValueError:
            return None, None
    if not isinstance(host, str) or not _HOST_PATTERN.fullmatch(host):
        return None, None
    if not isinstance(port, int) or isinstance(port, bool):
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    if port is not None and not 1 <= port <= 65535:
        port = None
    return host, port


def _request_url(exception: BaseException | None) -> Any:
    for node in _exception_chain(exception):
        response = _read(node, "response")
        for owner in (node, response):
            request = _read(owner, "request")
            url = _read(request, "url")
            if url is not None:
                return url
    return None


def _route_metadata(client: Any, exception: BaseException | None) -> dict[str, Any]:
    layers = _client_chain(client)
    url = _request_url(exception)
    if url is None:
        for layer in layers:
            candidate = (
                _read(layer, "api_endpoint")
                or _read(layer, "_effective_base_url")
                or _read(_read(_read(layer, "_api_client"), "_http_options"), "base_url")
            )
            if candidate is not None:
                url = candidate
                break
    target_host, target_port = _host_port(url)
    proxy_host = proxy_port = None
    if url is not None:
        route_url = url
        if isinstance(url, str):
            try:
                import httpx

                route_url = httpx.URL(url)
            except (ImportError, TypeError, ValueError):
                route_url = None
        for layer in layers:
            transport_for_url = getattr(layer, "_transport_for_url", None)
            if not callable(transport_for_url) or route_url is None:
                continue
            try:
                transport = transport_for_url(route_url)
                pool = getattr(transport, "_pool", None)
                proxy_url = getattr(pool, "_proxy_url", None)
                proxy_host, proxy_port = _host_port(proxy_url)
            except (AttributeError, TypeError, ValueError):
                pass
            break
    return {
        "target_hostname": target_host,
        "target_port": target_port,
        "configured_proxy_hostname": proxy_host,
        "configured_proxy_port": proxy_port,
    }


def _connection_phase(low_level: str, failure_layer: str, exception: BaseException | None) -> str | None:
    if low_level == "TRANSPORT_DNS":
        return "DNS"
    if low_level == "TRANSPORT_PROXY_CONNECT":
        return "PROXY_CONNECT"
    if low_level == "TRANSPORT_TLS":
        return "TLS"
    if low_level == "TRANSPORT_CONNECT":
        return "TCP_CONNECT"
    if low_level.startswith("HTTP_") or failure_layer == "HTTP":
        return "RESPONSE_HEADERS"
    if low_level == "RESPONSE_PARSE_ERROR" or failure_layer == "SDK":
        return "SDK_PARSE"
    if low_level == "TRANSPORT_TIMEOUT":
        names = [type(node).__name__.casefold() for node in _exception_chain(exception)]
        if any("connect" in name for name in names):
            return "TCP_CONNECT"
        if any("read" in name for name in names):
            return "RESPONSE_READ"
        if any("write" in name for name in names):
            return "REQUEST_WRITE"
    return None


def _assemble(
    *,
    provider: str,
    model: str,
    high_level_outcome: str,
    low_level_failure_class: str,
    failure_layer: str,
    base: Mapping[str, Any],
    client: Any,
    payload: Mapping[str, Any],
    exception: BaseException | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "high_level_outcome": high_level_outcome,
        "low_level_failure_class": low_level_failure_class,
        "failure_layer": failure_layer,
        "exception_type": base.get("exception_type"),
        "exception_message_safe": base.get("exception_message"),
        "exception_repr_safe": base.get("exception_repr"),
        "nested_cause_chain": base.get("nested_cause", []),
        "http_status": base.get("http_status"),
        "provider_error_code": base.get("provider_error_code"),
        "provider_error_message_safe": base.get("provider_error_message"),
        "request_id": base.get("request_id"),
        "response_status": base.get("response_status"),
        "finish_reason": base.get("finish_reason"),
        "timeout_type": base.get("timeout_type"),
        "errno": base.get("errno"),
        "winerror": base.get("winerror"),
        **_route_metadata(client, exception),
        "connection_phase": _connection_phase(
            low_level_failure_class, failure_layer, exception
        ),
        "elapsed_ms": base.get("elapsed_ms"),
        "request_shape_summary": _request_shape(payload),
    }


def gemini_exception_diagnostics(
    exception: BaseException,
    *,
    high_level_outcome: str,
    model: str,
    client: Any,
    payload: Mapping[str, Any],
    elapsed_ms: float,
) -> dict[str, Any]:
    redaction_payload = _payload_for_redaction(payload)
    base = exception_failure_diagnostics(
        exception,
        high_level_outcome=high_level_outcome,
        request_payload=redaction_payload,
        elapsed_ms=elapsed_ms,
    )
    low_level, failure_layer = _exception_classification(exception, base)
    provider_code = safe_diagnostic_text(
        _provider_error_code(exception), redaction_payload
    )
    if provider_code is not None:
        base["provider_error_code"] = provider_code
    return _assemble(
        provider="gemini",
        model=model,
        high_level_outcome=high_level_outcome,
        low_level_failure_class=low_level,
        failure_layer=failure_layer,
        base=base,
        client=client,
        payload=payload,
        exception=exception,
    )


def gemini_response_diagnostics(
    response: Any,
    *,
    high_level_outcome: str,
    model: str,
    client: Any,
    payload: Mapping[str, Any],
    elapsed_ms: float,
    provider_error: Any = None,
    finish_reason: Any = None,
    parse_error: bool = False,
    exception: BaseException | None = None,
) -> dict[str, Any]:
    redaction_payload = _payload_for_redaction(payload)
    base = response_failure_diagnostics(
        response,
        high_level_outcome=high_level_outcome,
        request_payload=redaction_payload,
        elapsed_ms=elapsed_ms,
        finish_reason=finish_reason,
        provider_error=provider_error,
    )
    status = _status_number(provider_error) or _status_number(response)
    low_level, failure_layer = _response_classification(
        response,
        provider_error=provider_error,
        finish_reason=finish_reason,
        parse_error=parse_error,
        status=status,
    )
    provider_code = safe_diagnostic_text(
        _provider_error_code(provider_error), redaction_payload
    )
    if provider_code is not None:
        base["provider_error_code"] = provider_code
    if exception is not None:
        base["exception_type"] = type(exception).__name__
        base["exception_message"] = base.get("exception_message")
        base["exception_repr"] = base.get("exception_repr")
        exception_data = exception_failure_diagnostics(
            exception,
            high_level_outcome=high_level_outcome,
            request_payload=redaction_payload,
            elapsed_ms=elapsed_ms,
        )
        base.update({
            "exception_message": exception_data.get("exception_message"),
            "exception_repr": exception_data.get("exception_repr"),
            "nested_cause": exception_data.get("nested_cause", []),
            "timeout_type": exception_data.get("timeout_type"),
            "errno": exception_data.get("errno"),
            "winerror": exception_data.get("winerror"),
        })
    return _assemble(
        provider="gemini",
        model=model,
        high_level_outcome=high_level_outcome,
        low_level_failure_class=low_level,
        failure_layer=failure_layer,
        base=base,
        client=client,
        payload=payload,
        exception=exception,
    )


__all__ = ["gemini_exception_diagnostics", "gemini_response_diagnostics"]
