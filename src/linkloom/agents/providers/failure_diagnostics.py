"""Bounded, secret-aware diagnostics for provider failures.

This module deliberately records exception/response metadata only. It never
serializes a request object or response body wholesale.
"""

from __future__ import annotations

from collections.abc import Mapping
import errno
import re
import socket
import ssl
from typing import Any


_MAX_TEXT = 512
_MAX_CAUSES = 8
_MAX_SENSITIVE_VALUES = 4096
_SENSITIVE_REQUEST_KEYS = frozenset(
    {"content", "prompt", "input", "arguments"}
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"),
    re.compile(
        r"(?i)\b(?:api[\s_-]?key|key|access[_-]?token|oauth[_-]?token|token|secret|password)\b\s*[:=]\s*"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
    ),
    re.compile(
        r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+(?::[^\s/@]*)?(?=@)"
    ),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
)


def _read(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    try:
        return getattr(value, name, default)
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
        return default


def _sensitive_request_values(payload: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    values: list[str] = []
    stack: list[tuple[Any, bool]] = [(payload, False)]
    while stack and len(values) < _MAX_SENSITIVE_VALUES:
        current, sensitive_text = stack.pop()
        if isinstance(current, Mapping):
            for key, item in current.items():
                stack.append(
                    (
                        item,
                        sensitive_text or str(key).lower() in _SENSITIVE_REQUEST_KEYS,
                    )
                )
        elif isinstance(current, (list, tuple)):
            stack.extend((item, sensitive_text) for item in current)
        elif isinstance(current, str) and sensitive_text and current.strip():
            values.append(current)
            # Exceptions sometimes echo one prompt token instead of the full
            # request. Mask exact prompt tokens too, preferring privacy over a
            # potentially revealing error message.
            values.extend(
                token
                for token in re.findall(r"[\w@.:/+~-]{4,}", current, flags=re.UNICODE)
                if token != current
            )
    return sorted(set(values), key=len, reverse=True)


def _safe_text(value: Any, sensitive_values: list[str]) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    for sensitive in sensitive_values:
        text = text.replace(sensitive, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", text)
    text = " ".join(text.split())
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + "…"
    return text


def safe_diagnostic_text(
    value: Any,
    request_payload: Mapping[str, Any] | None = None,
) -> str | None:
    """Sanitize a bounded scalar diagnostic using the shared redaction rules."""
    return _safe_text(value, _sensitive_request_values(request_payload))


def _safe_exception_repr(exception: BaseException, sensitive: list[str]) -> str:
    # Sanitize the ordinary message before applying repr escaping. Sanitizing
    # repr(exception) directly can miss prompts containing quotes/newlines.
    message = _safe_text(str(exception), sensitive) or ""
    return f"{type(exception).__name__}({message!r})"


def _http_status(exception: Any) -> int | None:
    response = _read(exception, "response")
    for candidate in (exception, response):
        for name in ("status_code", "http_status", "status", "code"):
            value = _read(candidate, name)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 100 <= value <= 599
            ):
                return value
    return None


def _headers_request_id(value: Any) -> Any:
    headers = _read(value, "headers")
    if isinstance(headers, Mapping):
        for name, item in headers.items():
            if str(name).lower() in {"x-request-id", "request-id"}:
                return item
    return None


def _exception_nodes(exception: BaseException) -> list[tuple[str, Any]]:
    """Return the primary exception followed by cause/context/reason nodes."""

    nodes: list[tuple[str, Any]] = [("exception", exception)]
    seen = {id(exception)}
    pending: list[tuple[str, Any]] = []
    current: BaseException = exception
    while len(nodes) < _MAX_CAUSES + 1:
        if len(pending) >= _MAX_CAUSES:
            break
        cause = current.__cause__
        context = current.__context__
        next_exception = cause if cause is not None else context
        if next_exception is not None and id(next_exception) not in seen:
            pending.append(("cause" if cause is not None else "context", next_exception))
            seen.add(id(next_exception))
            current = next_exception
            continue
        reason = _read(current, "reason")
        if reason is not None and id(reason) not in seen:
            if isinstance(reason, BaseException):
                pending.append(("reason", reason))
                seen.add(id(reason))
                current = reason
                continue
            pending.append(("reason", reason))
        break
    for relation, node in pending[:_MAX_CAUSES]:
        nodes.append((relation, node))
    return nodes


def _node_type(node: Any) -> str | None:
    return type(node).__name__ if isinstance(node, BaseException) else None


def _classify_transport(
    exception: BaseException,
    nodes: list[Any] | None = None,
) -> str:
    nodes = nodes or [node for _, node in _exception_nodes(exception)]
    names = [type(node).__name__.lower() for node in nodes]
    reason_text = [str(node).lower() for node in nodes if isinstance(node, str)]
    if any(
        isinstance(node, ssl.SSLError) or "ssl" in name or "tls" in name
        for node, name in zip(nodes, names)
    ) or any(
        any(marker in text for marker in ("ssl", "tls", "certificate"))
        for text in reason_text
    ):
        return "TRANSPORT_TLS"
    if any(
        isinstance(node, TimeoutError)
        or "timeout" in name
        or "deadline" in name
        for node, name in zip(nodes, names)
    ) or any(
        "timeout" in text or "timed out" in text or "deadline" in text
        for text in reason_text
    ):
        return "TRANSPORT_TIMEOUT"
    if any(
        any(
            marker in text
            for marker in ("name resolution", "getaddrinfo", "dns", "nodename")
        )
        for text in reason_text
    ):
        return "TRANSPORT_DNS"
    for node in nodes:
        if isinstance(node, socket.gaierror):
            return "TRANSPORT_DNS"
        if isinstance(node, OSError) and getattr(node, "errno", None) in {
            getattr(socket, "EAI_NONAME", None),
            getattr(socket, "EAI_AGAIN", None),
        }:
            return "TRANSPORT_DNS"
    if any(
        "connect" in name
        or isinstance(node, ConnectionError)
        or (
            isinstance(node, OSError)
            and getattr(node, "errno", None)
            in {
                errno.ECONNREFUSED,
                errno.ECONNRESET,
                errno.EHOSTUNREACH,
                errno.ENETUNREACH,
            }
        )
        or getattr(node, "winerror", None) in {10061, 10060, 10054, 10065}
        for node, name in zip(nodes, names)
    ) or any(
        any(
            marker in text
            for marker in ("connection refused", "connection reset", "connect")
        )
        for text in reason_text
    ):
        return "TRANSPORT_CONNECT"
    modules = [type(node).__module__.lower() for node in nodes]
    if any(
        module.startswith("openai")
        or module.startswith("httpx")
        or module.startswith("httpcore")
        for module in modules
    ):
        return "SDK_ERROR"
    return "UNKNOWN_PROVIDER_FAILURE"


def _error_payload(value: Any) -> Any:
    body = _read(value, "body")
    if body is None:
        body = _read(value, "error")
    if isinstance(body, Mapping) and "error" in body:
        return body.get("error")
    return body


def _provider_error_fields(
    value: Any,
    sensitive: list[str],
) -> tuple[str | None, str | None]:
    error = _error_payload(value)
    code = _read(error, "code") or _read(error, "type") or _read(value, "code")
    if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
        code = None
    message = _read(error, "message") or _read(value, "message")
    return _safe_text(code, sensitive), _safe_text(message, sensitive)


def exception_failure_diagnostics(
    exception: BaseException,
    *,
    high_level_outcome: str,
    request_payload: Mapping[str, Any] | None,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Build a JSON-safe diagnostic record without persisting request content."""

    sensitive = _sensitive_request_values(request_payload)
    status = _http_status(exception)
    nodes = _exception_nodes(exception)
    low_level = (
        "HTTP_429"
        if status == 429
        else "HTTP_4XX"
        if status is not None and 400 <= status < 500
        else "HTTP_5XX"
        if status is not None and 500 <= status < 600
        else _classify_transport(exception, [node for _, node in nodes])
    )
    nested = [
        {
            "relation": relation,
            "exception_type": _node_type(node),
            "exception_repr": (
                _safe_exception_repr(node, sensitive)
                if isinstance(node, BaseException)
                else _safe_text(node, sensitive)
            ),
            "errno": (
                getattr(node, "errno", None)
                if isinstance(getattr(node, "errno", None), int)
                else None
            ),
            "winerror": (
                getattr(node, "winerror", None)
                if isinstance(getattr(node, "winerror", None), int)
                else None
            ),
        }
        for relation, node in nodes[1:]
    ]
    response = _read(exception, "response")
    code, message = _provider_error_fields(exception, sensitive)
    if code is None and response is not None:
        code, message = _provider_error_fields(response, sensitive)
    request_id = (
        _read(exception, "request_id")
        or _read(exception, "provider_request_id")
        or _headers_request_id(response)
        or _headers_request_id(exception)
    )
    if not isinstance(request_id, str):
        request_id = None
    errno_value = getattr(exception, "errno", None)
    winerror_value = getattr(exception, "winerror", None)
    return {
        "failure_layer": "http" if status is not None else (
            "transport" if low_level.startswith("TRANSPORT_") else (
                "sdk" if low_level == "SDK_ERROR" else "unknown"
            )
        ),
        "exception_type": type(exception).__name__,
        "exception_message": _safe_text(str(exception), sensitive),
        "exception_repr": _safe_exception_repr(exception, sensitive),
        "nested_cause": nested,
        "http_status": status,
        "provider_error_code": code,
        "provider_error_message": message,
        "request_id": _safe_text(request_id, sensitive),
        "finish_reason": None,
        "response_status": None,
        "timeout_type": next(
            (type(node).__name__ for _, node in nodes if "timeout" in type(node).__name__.lower()),
            None,
        ),
        "errno": errno_value if isinstance(errno_value, int) else None,
        "winerror": winerror_value if isinstance(winerror_value, int) else None,
        "elapsed_ms": round(max(0.0, elapsed_ms), 3),
        "high_level_outcome": high_level_outcome,
        "low_level_failure": low_level,
    }


def response_failure_diagnostics(
    response: Any,
    *,
    high_level_outcome: str,
    request_payload: Mapping[str, Any] | None,
    elapsed_ms: float,
    finish_reason: str | None = None,
    provider_error: Any = None,
) -> dict[str, Any]:
    """Summarize a provider-native failed response without storing its body."""

    sensitive = _sensitive_request_values(request_payload)
    error = provider_error if provider_error is not None else _read(response, "error")
    status = _http_status(error) or _http_status(response)
    error_code, error_message = _provider_error_fields({"error": error}, sensitive)
    if error_code is None and finish_reason == "insufficient_system_resource":
        error_code = finish_reason
    request_id = (
        _read(response, "request_id")
        or _read(response, "provider_request_id")
        or _headers_request_id(response)
    )
    if not isinstance(request_id, str):
        request_id = None
    response_status = _read(response, "status")
    if response_status is None:
        response_status = _read(response, "response_status")
    if not isinstance(response_status, str):
        response_status = None
    if finish_reason == "aborted" or error_code == "aborted":
        low_level = "PROVIDER_ABORTED"
    elif (
        finish_reason == "insufficient_system_resource"
        or error_code == "insufficient_system_resource"
        or (status is not None and status >= 500 and "overload" in (error_message or "").lower())
    ):
        low_level = "PROVIDER_OVERLOADED"
    elif status == 429:
        low_level = "HTTP_429"
    elif status is not None and 400 <= status < 500:
        low_level = "HTTP_4XX"
    elif status is not None and 500 <= status < 600:
        low_level = "HTTP_5XX"
    else:
        low_level = "PROVIDER_FAILED_RESPONSE"
    return {
        "failure_layer": "http" if status is not None else "provider_response",
        "exception_type": None,
        "exception_message": None,
        "exception_repr": None,
        "nested_cause": [],
        "http_status": status,
        "provider_error_code": error_code,
        "provider_error_message": error_message,
        "request_id": _safe_text(request_id, sensitive),
        "finish_reason": _safe_text(finish_reason, sensitive),
        "response_status": _safe_text(response_status, sensitive),
        "timeout_type": None,
        "errno": None,
        "winerror": None,
        "elapsed_ms": round(max(0.0, elapsed_ms), 3),
        "high_level_outcome": high_level_outcome,
        "low_level_failure": low_level,
    }


__all__ = [
    "exception_failure_diagnostics",
    "response_failure_diagnostics",
    "safe_diagnostic_text",
]
