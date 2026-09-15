"""Small local HTTP server for LinkLoom's Product UI."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from linkloom.ui.backend import RunBackend


MAX_REQUEST_BYTES = 32 * 1024
STATIC_ROOT = Path(__file__).with_name("static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class LinkLoomHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], backend: RunBackend) -> None:
        self.backend = backend
        super().__init__(address, LinkLoomRequestHandler)


class LinkLoomRequestHandler(BaseHTTPRequestHandler):
    server: LinkLoomHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _send_bytes(self, body: bytes, *, status: int, content_type: str) -> None:
        self._headers(status, content_type, len(body))
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        self._send_bytes(
            body,
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _send_error_payload(self, status: int, code: str, message: str) -> None:
        self._send_json(
            {"error": {"code": code, "message": message}},
            status=status,
        )

    def _request_host_is_local(self) -> bool:
        raw_host = self.headers.get("Host", "").strip().lower()
        hostname = raw_host.split(":", 1)[0].rstrip(".")
        return hostname in {"127.0.0.1", "localhost"}

    def _reject_non_local_host(self) -> bool:
        if self._request_host_is_local():
            return False
        self._send_error_payload(
            HTTPStatus.MISDIRECTED_REQUEST,
            "INVALID_HOST",
            "The Product UI accepts local requests only.",
        )
        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self._reject_non_local_host():
            return
        path = unquote(urlsplit(self.path).path)
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            try:
                body = (STATIC_ROOT / filename).read_bytes()
            except OSError:
                self._send_error_payload(500, "STATIC_ASSET_ERROR", "The UI asset is unavailable.")
                return
            self._send_bytes(body, status=200, content_type=content_type)
            return
        if path == "/api/context":
            self._send_json(self.server.backend.context())
            return
        if path.startswith("/api/runs/"):
            run_id = path.removeprefix("/api/runs/")
            snapshot = self.server.backend.inspect(run_id)
            if snapshot is None:
                self._send_error_payload(404, "RUN_NOT_FOUND", "The requested run was not found.")
                return
            self._send_json(snapshot)
            return
        if path.startswith("/api/demo/"):
            state = path.removeprefix("/api/demo/")
            snapshot_provider = getattr(self.server.backend, "demo_snapshot", None)
            snapshot = snapshot_provider(state) if callable(snapshot_provider) else None
            if snapshot is None:
                self._send_error_payload(404, "DEMO_STATE_NOT_FOUND", "The demo state was not found.")
                return
            self._send_json(snapshot)
            return
        self._send_error_payload(404, "NOT_FOUND", "The requested resource was not found.")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self._reject_non_local_host():
            return
        path = unquote(urlsplit(self.path).path)
        if path != "/api/runs":
            self._send_error_payload(404, "NOT_FOUND", "The requested resource was not found.")
            return
        if self.headers.get_content_type() != "application/json":
            self._send_error_payload(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "UNSUPPORTED_MEDIA_TYPE",
                "Run requests must use application/json.",
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_error_payload(400, "INVALID_REQUEST", "The request body is invalid.")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_payload(400, "INVALID_JSON", "The request must be valid JSON.")
            return
        query = payload.get("query") if isinstance(payload, dict) else None
        if not isinstance(query, str) or not query.strip():
            self._send_error_payload(400, "INVALID_QUERY", "A decision question is required.")
            return
        try:
            snapshot = self.server.backend.start(query.strip())
        except Exception:
            self._send_error_payload(500, "RUN_START_ERROR", "The run could not be started.")
            return
        self._send_json(snapshot, status=HTTPStatus.ACCEPTED)


def create_http_server(
    backend: RunBackend,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> LinkLoomHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Product UI V1 binds to localhost only.")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535.")
    return LinkLoomHTTPServer((host, port), backend)


__all__ = ["LinkLoomHTTPServer", "create_http_server"]
