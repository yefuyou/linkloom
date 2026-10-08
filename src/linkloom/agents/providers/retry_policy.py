"""Provider-neutral, conservative retry classification for transient failures."""

from __future__ import annotations

from collections.abc import Mapping
import errno
import socket
import ssl
from typing import Any, Iterator
from urllib.error import HTTPError, URLError


MAX_NESTED_CAUSE_DEPTH = 8
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
NON_RETRYABLE_HTTP_STATUSES = frozenset({400, 401, 402, 403})
NON_RETRYABLE_PROVIDER_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "FAILED_PRECONDITION",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
    }
)
_OVERLOAD_CODES = frozenset({"RESOURCE_EXHAUSTED", "OVERLOADED"})
_OTHER_TRANSIENT_PROVIDER_CODES = frozenset(
    {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL", "ABORTED"}
)
_HTTP_EXCEPTION_NAMES = frozenset({"HTTPError", "HTTPStatusError"})
_PROXY_EXCEPTION_NAMES = frozenset(
    {"ProxyError", "ProxyConnectionError", "ProxyConnectError"}
)
_TIMEOUT_EXCEPTION_NAMES = frozenset(
    {
        "Timeout",
        "TimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "WriteTimeoutError",
    }
)
_TRANSIENT_TLS_EXCEPTION_NAMES = frozenset(
    {"SSLError", "SSLEOFError", "SSLWantReadError", "SSLWantWriteError"}
)
_DETERMINISTIC_TLS_EXCEPTION_NAMES = frozenset(
    {"SSLCertVerificationError", "CertificateError", "InvalidCertificate"}
)
_NETWORK_MODULE_PREFIXES = (
    "httpx",
    "httpcore",
    "urllib3",
    "requests",
)


def _exception_chain(error: BaseException | None) -> Iterator[BaseException]:
    """Yield a primary exception and a bounded, cycle-safe wrapper chain."""
    current = error
    seen: set[int] = set()
    for _ in range(MAX_NESTED_CAUSE_DEPTH):
        if not isinstance(current, BaseException) or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        next_error: BaseException | None = None
        for candidate in (
            current.__cause__,
            current.__context__,
            getattr(current, "reason", None),
            getattr(current, "cause", None),
            getattr(current, "original_exception", None),
            getattr(current, "__wrapped__", None),
        ):
            if isinstance(candidate, BaseException) and id(candidate) not in seen:
                next_error = candidate
                break
        current = next_error


def _safe_status(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _status_from_mapping(diagnostic: Mapping[str, Any] | None) -> int | None:
    if not isinstance(diagnostic, Mapping):
        return None
    for key in ("http_status", "status_code", "response_status"):
        status = _safe_status(diagnostic.get(key))
        if status is not None:
            return status
    return None


def _status_from_error(error: BaseException | None) -> int | None:
    for node in _exception_chain(error):
        response = getattr(node, "response", None)
        for candidate in (
            getattr(node, "status_code", None),
            getattr(node, "http_status", None),
            getattr(node, "code", None),
            getattr(response, "status_code", None),
            getattr(response, "status", None),
        ):
            status = _safe_status(candidate)
            if status is not None:
                return status
    return None


def _provider_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if code and len(code) <= 80 else None


def _provider_code_from_mapping(diagnostic: Mapping[str, Any] | None) -> str | None:
    if not isinstance(diagnostic, Mapping):
        return None
    for key in ("provider_error_code", "provider_code", "status"):
        code = _provider_code(diagnostic.get(key))
        if code is not None:
            return code
    return None


def _provider_code_from_error(error: BaseException | None) -> str | None:
    for node in _exception_chain(error):
        for value in (getattr(node, "status", None), getattr(node, "reason", None)):
            code = _provider_code(value)
            if code is not None and code.replace("_", "").isalnum():
                return code
    return None


def _diagnostic_exception_classes(diagnostic: Mapping[str, Any] | None) -> Iterator[tuple[str, str]]:
    if not isinstance(diagnostic, Mapping):
        return
    top_level = diagnostic.get("exception_class") or diagnostic.get("exception_type")
    if isinstance(top_level, str):
        yield top_level.rsplit(".", 1)[-1], ""
    for key in ("nested_cause_chain", "nested_cause", "causes"):
        chain = diagnostic.get(key)
        if not isinstance(chain, (list, tuple)):
            continue
        for entry in chain[:MAX_NESTED_CAUSE_DEPTH]:
            if isinstance(entry, str):
                yield entry.rsplit(".", 1)[-1], ""
            elif isinstance(entry, Mapping):
                name = entry.get("exception_type") or entry.get("type") or entry.get("class")
                module = entry.get("module")
                if isinstance(name, str):
                    yield name.rsplit(".", 1)[-1], module if isinstance(module, str) else ""


def _transport_classification(error: BaseException | None) -> str | None:
    nodes = list(_exception_chain(error))
    if not nodes:
        return None
    typed = [(node, type(node).__name__, type(node).__module__.casefold()) for node in nodes]

    if any(name in _DETERMINISTIC_TLS_EXCEPTION_NAMES for _, name, _ in typed):
        return None
    if any(
        name in _PROXY_EXCEPTION_NAMES
        and (not module or module.startswith(_NETWORK_MODULE_PREFIXES))
        for _, name, module in typed
    ):
        return "TRANSPORT_PROXY_CONNECT"
    if any(isinstance(node, socket.gaierror) or name in {"gaierror", "herror"} for node, name, _ in typed):
        return "TRANSPORT_DNS"
    if any(
        isinstance(node, OSError)
        and getattr(node, "errno", None)
        in {getattr(socket, "EAI_NONAME", None), getattr(socket, "EAI_AGAIN", None)}
        for node, _, _ in typed
    ):
        return "TRANSPORT_DNS"
    if any(
        isinstance(node, ssl.SSLError)
        or (
            name in _TRANSIENT_TLS_EXCEPTION_NAMES
            and (not module or module in {"ssl", "_ssl"} or module.startswith(_NETWORK_MODULE_PREFIXES))
        )
        for node, name, module in typed
    ):
        return "TRANSPORT_TLS"
    if any(
        isinstance(node, TimeoutError)
        or (
            name in _TIMEOUT_EXCEPTION_NAMES
            and (not module or module.startswith(_NETWORK_MODULE_PREFIXES))
        )
        for node, name, module in typed
    ):
        return "TRANSPORT_TIMEOUT"
    for node, name, module in typed:
        if isinstance(node, URLError):
            reason = getattr(node, "reason", None)
            if isinstance(reason, socket.gaierror):
                return "TRANSPORT_DNS"
            if isinstance(reason, TimeoutError):
                return "TRANSPORT_TIMEOUT"
            if isinstance(reason, ssl.SSLError):
                return "TRANSPORT_TLS"
            return "TRANSPORT_CONNECT"
        if isinstance(node, HTTPError) or name in _HTTP_EXCEPTION_NAMES:
            continue
        if (
            name == "ConnectError"
            and module.startswith(_NETWORK_MODULE_PREFIXES)
        ):
            return "TRANSPORT_CONNECT"
        if isinstance(node, ConnectionError):
            return "TRANSPORT_CONNECT"
        if isinstance(node, OSError) and getattr(node, "errno", None) in {
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
        }:
            return "TRANSPORT_CONNECT"
        if getattr(node, "winerror", None) in {10061, 10060, 10054, 10065}:
            return "TRANSPORT_CONNECT"
    return None


def _transport_from_diagnostic(diagnostic: Mapping[str, Any] | None) -> str | None:
    classes = list(_diagnostic_exception_classes(diagnostic))
    if any(name in _DETERMINISTIC_TLS_EXCEPTION_NAMES for name, _ in classes):
        return None
    for target in (
        _PROXY_EXCEPTION_NAMES,
        frozenset({"gaierror", "herror"}),
        _TRANSIENT_TLS_EXCEPTION_NAMES,
        _TIMEOUT_EXCEPTION_NAMES,
        frozenset({
            "ConnectError",
            "ConnectionError",
            "ConnectionRefusedError",
            "ConnectionResetError",
            "RemoteDisconnected",
            "URLError",
        }),
    ):
        for name, module in classes:
            if name not in target:
                continue
            if name in _PROXY_EXCEPTION_NAMES and module and not module.startswith(_NETWORK_MODULE_PREFIXES):
                continue
            if name in _TRANSIENT_TLS_EXCEPTION_NAMES and module and not (
                module in {"ssl", "_ssl"} or module.startswith(_NETWORK_MODULE_PREFIXES)
            ):
                continue
            if name in _TIMEOUT_EXCEPTION_NAMES and module and not module.startswith(_NETWORK_MODULE_PREFIXES):
                continue
            if name in {"gaierror", "herror"}:
                return "TRANSPORT_DNS"
            if name in _PROXY_EXCEPTION_NAMES:
                return "TRANSPORT_PROXY_CONNECT"
            if name in _TRANSIENT_TLS_EXCEPTION_NAMES:
                return "TRANSPORT_TLS"
            if name in _TIMEOUT_EXCEPTION_NAMES:
                return "TRANSPORT_TIMEOUT"
            if name in {
                "ConnectError",
                "ConnectionError",
                "ConnectionRefusedError",
                "ConnectionResetError",
                "RemoteDisconnected",
                "URLError",
            }:
                return "TRANSPORT_CONNECT"
    return None


def classify_retryable_failure(
    error: BaseException | None,
    diagnostic: Mapping[str, Any] | None = None,
) -> str | None:
    """Return a stable transient class, or ``None`` for every other failure.

    Only exact HTTP statuses, known provider codes, and explicit transport
    exception types can be retried. Error messages are intentionally ignored.
    """
    status = _status_from_mapping(diagnostic) or _status_from_error(error)
    provider_code = _provider_code_from_mapping(diagnostic) or _provider_code_from_error(error)

    if status in NON_RETRYABLE_HTTP_STATUSES or provider_code in NON_RETRYABLE_PROVIDER_CODES:
        return None
    if status in RETRYABLE_HTTP_STATUSES:
        return f"HTTP_{status}"
    if provider_code in _OVERLOAD_CODES:
        return "PROVIDER_OVERLOADED"
    if provider_code in _OTHER_TRANSIENT_PROVIDER_CODES:
        return f"PROVIDER_{provider_code}"

    if error is not None and (
        isinstance(error, HTTPError) or type(error).__name__ in _HTTP_EXCEPTION_NAMES
    ):
        return None
    transport = _transport_classification(error)
    if transport is not None:
        return transport
    return _transport_from_diagnostic(diagnostic)


__all__ = [
    "MAX_NESTED_CAUSE_DEPTH",
    "NON_RETRYABLE_HTTP_STATUSES",
    "RETRYABLE_HTTP_STATUSES",
    "classify_retryable_failure",
]
