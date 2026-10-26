"""Small local HTTP server for LinkLoom's Product UI."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from linkloom.ui.backend import RunBackend
from linkloom.ui.product import MAX_SOURCE_BYTES, ProductApplication, ProductApplicationError
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.task_review import TaskReviewService
from linkloom.ui.unified_ask import UnifiedAskService


MAX_REQUEST_BYTES = 32 * 1024
MAX_PRODUCT_REQUEST_BYTES = MAX_SOURCE_BYTES + 16 * 1024
STATIC_ROOT = Path(__file__).with_name("static")
STATIC_FILES = {
    "/": ("product.html", "text/html; charset=utf-8"),
    "/product.html": ("product.html", "text/html; charset=utf-8"),
    "/product.css": ("product.css", "text/css; charset=utf-8"),
    "/product.js": ("product.js", "text/javascript; charset=utf-8"),
    "/product-task-result.js": ("product-task-result.js", "text/javascript; charset=utf-8"),
    "/product-task-review.js": ("product-task-review.js", "text/javascript; charset=utf-8"),
    "/legacy-ask": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class LinkLoomHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        backend: RunBackend,
        product_app: ProductApplication | None = None,
        investigation_service: ProductInvestigationService | None = None,
        *,
        allow_local_task_review: bool = False,
        task_review_sidecar_path: str | Path | None = None,
    ) -> None:
        self.backend = backend
        self.product_app = product_app
        self.investigation_service = investigation_service
        self.unified_ask = (UnifiedAskService(product_app, lambda: self.investigation_service)
                            if product_app is not None else None)
        if product_app is not None:
            # Relation sidecars are Host-owned.  Keep an explicitly composed
            # map when one was supplied, but do not let an empty map fall
            # through to a copied local dict: Product Runtime and task-review
            # CONTINUE must resolve the same path.  The request never carries
            # a sidecar path.
            proof_paths = getattr(investigation_service, "task_relation_paths", None)
            if proof_paths is None:
                proof_paths = {}
                if investigation_service is not None:
                    investigation_service.task_relation_paths = proof_paths
            if not isinstance(proof_paths, dict):
                raise ValueError("task_relation_paths must be a Host-owned mapping")
            self._task_relation_paths = proof_paths
            self._default_task_relation_sidecar = self._host_relation_sidecar(product_app)
            current_workspace = product_app.repository.current_workspace_id()
            if current_workspace:
                self._task_relation_sidecar_for(current_workspace)
            self.task_review = TaskReviewService(
                product_app,
                lambda run_id, workspace_id: self.unified_ask.inspect(run_id, workspace_id),
                enabled=allow_local_task_review,
                sidecar_path=task_review_sidecar_path,
                proof_path_provider=self._task_relation_sidecar_for,
            )
        else:
            self._task_relation_paths = {}
            self._default_task_relation_sidecar = None
            self.task_review = None
        super().__init__(address, LinkLoomRequestHandler)

    @staticmethod
    def _host_relation_sidecar(product_app: ProductApplication) -> Path | None:
        database_path = str(getattr(product_app.repository, "database_path", ""))
        if not database_path or database_path == ":memory:":
            # There is no durable Product parent to bind a relation sidecar
            # to.  Do not create a process-global fallback file.
            return None
        if database_path:
            path = Path(database_path).expanduser().with_name(
                Path(database_path).expanduser().name + ".relations.sqlite"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _task_relation_sidecar_for(self, workspace_id: str) -> Path | None:
        if workspace_id in self._task_relation_paths:
            value = self._task_relation_paths[workspace_id]
            return Path(value).expanduser() if value is not None else None
        if self._default_task_relation_sidecar is None:
            return None
        self._task_relation_paths[workspace_id] = self._default_task_relation_sidecar
        return self._default_task_relation_sidecar

    def ensure_task_relation_sidecar(self, workspace_id: object) -> None:
        if isinstance(workspace_id, str) and workspace_id.strip():
            self._task_relation_sidecar_for(workspace_id)

    def server_close(self) -> None:
        if self.task_review is not None:
            self.task_review.close()
        super().server_close()


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

    def _reject_task_review_origin(self) -> bool:
        """Allow only same-origin localhost browser requests for local review."""
        origin = self.headers.get("Origin")
        if not origin:
            return False
        parsed = urlsplit(origin)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        host_header = self.headers.get("Host", "").strip().lower()
        host_hostname = host_header.split(":", 1)[0].rstrip(".")
        if parsed.scheme.lower() != "http" or hostname not in {"127.0.0.1", "localhost"} or hostname != host_hostname:
            self._send_error_payload(HTTPStatus.FORBIDDEN, "INVALID_ORIGIN", "Task review requires the local Product UI origin.")
            return True
        try:
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            self._send_error_payload(HTTPStatus.FORBIDDEN, "INVALID_ORIGIN", "Task review origin is malformed.")
            return True
        host_port = host_header.rsplit(":", 1)[-1] if ":" in host_header else "80"
        if host_port.isdigit() and int(host_port) != origin_port:
            self._send_error_payload(HTTPStatus.FORBIDDEN, "INVALID_ORIGIN", "Task review origin does not match the current Host.")
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self._reject_non_local_host():
            return
        path = unquote(urlsplit(self.path).path)
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            if path == "/" and self.server.product_app is None:
                filename = "index.html"
            try:
                body = (STATIC_ROOT / filename).read_bytes()
            except OSError:
                self._send_error_payload(500, "STATIC_ASSET_ERROR", "The UI asset is unavailable.")
                return
            self._send_bytes(body, status=200, content_type=content_type)
            return
        if path == "/api/product/task-review":
            if self._reject_task_review_origin():
                return
            query = parse_qs(urlsplit(self.path).query)
            workspace_id = query.get("workspace_id", [""])[0]
            run_id = query.get("run_id", [""])[0]
            if self.server.task_review is None:
                self._send_error_payload(HTTPStatus.FORBIDDEN, "LOCAL_TASK_REVIEW_DISABLED", "Task review is unavailable.")
                return
            try:
                self._send_json(self.server.task_review.get(workspace_id, run_id))
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            except ValueError as error:
                self._send_error_payload(400, "INVALID_TASK_REVIEW_REQUEST", str(error))
            return
        if self.server.unified_ask is not None and path == "/api/product/ask/config":
            try:
                self._send_json(self.server.unified_ask.config())
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            return
        if self.server.unified_ask is not None and path == "/api/product/feedback":
            try:
                workspace_id = parse_qs(urlsplit(self.path).query).get("workspace_id", [""])[0]
                self._send_json(self.server.unified_ask.feedback_list(workspace_id))
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            return
        if self.server.unified_ask is not None and path.startswith("/api/product/ask/runs/"):
            try:
                workspace_id = parse_qs(urlsplit(self.path).query).get("workspace_id", [""])[0]
                result, status = self.server.unified_ask.inspect(path.rsplit("/", 1)[-1], workspace_id)
                self._send_json({"result": result}, status=status)
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            return
        if path == "/api/context":
            self._send_json(self.server.backend.context())
            return
        if path.startswith("/api/agent/decisions/runs/"):
            service = self.server.investigation_service
            snapshot = service.inspect(path.rsplit("/", 1)[-1]) if service is not None else None
            if snapshot is None:
                self._send_error_payload(404, "RUN_NOT_FOUND", "The temporal Agent run was not found.")
            else:
                failure_status = {
                    "provider_outcome_uncertain": HTTPStatus.SERVICE_UNAVAILABLE,
                    "provider_failure": HTTPStatus.BAD_GATEWAY,
                    "tool_policy_denied": HTTPStatus.FORBIDDEN,
                    "runtime_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
                }
                classification = (snapshot.get("error") or {}).get("classification")
                self._send_json(
                    snapshot,
                    status=(failure_status.get(classification, HTTPStatus.INTERNAL_SERVER_ERROR)
                            if snapshot.get("kind") == "error" else HTTPStatus.OK),
                )
            return
        if self.server.product_app is not None and path.startswith("/api/") and path not in {
            "/api/runs", "/api/context"
        } and not path.startswith("/api/runs/") and not path.startswith("/api/demo/"):
            try:
                self._send_json(self.server.product_app.handle_get(path))
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
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
        if path == "/api/product/task-review":
            if self._reject_task_review_origin():
                return
            payload = self._read_json_payload(MAX_REQUEST_BYTES)
            if payload is None:
                return
            if self.server.task_review is None:
                self._send_error_payload(HTTPStatus.FORBIDDEN, "LOCAL_TASK_REVIEW_DISABLED", "Task review is unavailable.")
                return
            try:
                result, status = self.server.task_review.post(payload)
                self._send_json(result, status=status)
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            return
        if path.startswith("/api/product/feedback/") and self.server.unified_ask is not None:
            payload = self._read_json_payload(MAX_REQUEST_BYTES)
            if payload is None:
                return
            try:
                result, status = self.server.unified_ask.feedback_action(path.rsplit("/", 1)[-1], payload)
                self._send_json(result, status=status)
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            return
        if path == "/api/product/ask" and self.server.unified_ask is not None:
            payload = self._read_json_payload(MAX_REQUEST_BYTES)
            if payload is None:
                return
            self.server.ensure_task_relation_sidecar(payload.get("workspace_id"))
            try:
                result, status = self.server.unified_ask.ask(payload)
                self._send_json({"result": result}, status=status)
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
            return
        if path == "/api/agent/decisions/ask":
            service = self.server.investigation_service
            if service is None:
                self._send_error_payload(503, "AGENT_NOT_CONFIGURED", "The temporal Agent is not configured.")
                return
            payload = self._read_json_payload(MAX_REQUEST_BYTES)
            if payload is None:
                return
            fields = ("query", "workspace_id", "subject", "relation")
            if any(not isinstance(payload.get(key), str) or not payload[key].strip()
                   or len(payload[key]) > (4000 if key == "query" else 512) for key in fields):
                self._send_error_payload(400, "INVALID_TEMPORAL_REQUEST", "A question and selected decision scope are required.")
                return
            if payload.get("as_of") is not None and not isinstance(payload["as_of"], str):
                self._send_error_payload(400, "INVALID_TEMPORAL_REQUEST", "as_of must be text or null.")
                return
            try:
                result = service.start_temporal(
                    payload["query"], payload["workspace_id"],
                    subject=payload["subject"], relation=payload["relation"],
                    as_of=payload.get("as_of"),
                )
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
                return
            self._send_json(result, status=HTTPStatus.ACCEPTED)
            return
        if path != "/api/runs":
            if self.server.product_app is None or not path.startswith("/api/"):
                self._send_error_payload(404, "NOT_FOUND", "The requested resource was not found.")
                return
            payload = self._read_json_payload(MAX_PRODUCT_REQUEST_BYTES)
            if payload is None:
                return
            try:
                result, status = self.server.product_app.handle_post(path, payload)
            except ProductApplicationError as error:
                self._send_error_payload(error.status, error.code, str(error))
                return
            self.server.ensure_task_relation_sidecar(self.server.product_app._workspace_id())
            self._send_json(result, status=status)
            return
        if self.headers.get_content_type() != "application/json":
            self._send_error_payload(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "UNSUPPORTED_MEDIA_TYPE",
                "Run requests must use application/json.",
            )
            return
        payload = self._read_json_payload(MAX_REQUEST_BYTES)
        if payload is None:
            return
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            self._send_error_payload(400, "INVALID_QUERY", "A decision question is required.")
            return
        try:
            snapshot = self.server.backend.start(query.strip())
        except Exception:
            self._send_error_payload(500, "RUN_START_ERROR", "The run could not be started.")
            return
        self._send_json(snapshot, status=HTTPStatus.ACCEPTED)

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self._reject_non_local_host():
            return
        if self.server.product_app is None:
            self._send_error_payload(404, "NOT_FOUND", "The requested resource was not found.")
            return
        payload = self._read_json_payload(MAX_PRODUCT_REQUEST_BYTES)
        if payload is None:
            return
        try:
            result, status = self.server.product_app.handle_put(unquote(urlsplit(self.path).path), payload)
        except ProductApplicationError as error:
            self._send_error_payload(error.status, error.code, str(error))
            return
        self._send_json(result, status=status)

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self._reject_non_local_host():
            return
        if self.server.product_app is None:
            self._send_error_payload(404, "NOT_FOUND", "The requested resource was not found.")
            return
        try:
            result = self.server.product_app.handle_delete(unquote(urlsplit(self.path).path))
        except ProductApplicationError as error:
            self._send_error_payload(error.status, error.code, str(error))
            return
        self._send_json(result)

    def _read_json_payload(self, maximum_bytes: int) -> dict[str, Any] | None:
        if self.headers.get_content_type() != "application/json":
            self._send_error_payload(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "UNSUPPORTED_MEDIA_TYPE",
                "This operation requires application/json.",
            )
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > maximum_bytes:
            self._send_error_payload(400, "INVALID_REQUEST", "The request body is invalid or too large.")
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_payload(400, "INVALID_JSON", "The request must be valid JSON.")
            return None
        if not isinstance(payload, dict):
            self._send_error_payload(400, "INVALID_REQUEST", "The request body must be a JSON object.")
            return None
        return payload


def create_http_server(
    backend: RunBackend,
    *,
    product_app: ProductApplication | None = None,
    investigation_service: ProductInvestigationService | None = None,
    allow_local_task_review: bool = False,
    task_review_sidecar_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> LinkLoomHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Product UI V1 binds to localhost only.")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535.")
    return LinkLoomHTTPServer(
        (host, port),
        backend,
        product_app,
        investigation_service,
        allow_local_task_review=allow_local_task_review,
        task_review_sidecar_path=task_review_sidecar_path,
    )


__all__ = ["LinkLoomHTTPServer", "create_http_server"]
