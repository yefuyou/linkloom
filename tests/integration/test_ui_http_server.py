from __future__ import annotations

import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from linkloom.ui.demo import DemoCase, DemoRunBackend
from linkloom.ui.server import create_http_server


def _json(url: str) -> dict:
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def test_local_server_serves_assets_and_demo_product_states() -> None:
    server = create_http_server(
        DemoRunBackend(DemoCase.load_mps_001()),
        host="127.0.0.1",
        port=0,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
            assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
        assert "LinkLoom" in html
        assert "/app.js" in html
        assert _json(base + "/api/context")["workspace"]["display_name"] == "Atlas Lantern"
        assert _json(base + "/api/demo/success")["kind"] == "success"
        assert _json(base + "/api/demo/insufficient")["kind"] == "insufficient"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_mobile_action_rows_keep_visible_field_labels() -> None:
    server = create_http_server(
        DemoRunBackend(DemoCase.load_mps_001()),
        host="127.0.0.1",
        port=0,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/app.js", timeout=2) as response:
            script = response.read().decode("utf-8")
        with urlopen(base + "/app.css", timeout=2) as response:
            stylesheet = response.read().decode("utf-8")

        assert 'data-label="Owner"' in script
        assert 'data-label="Due"' in script
        assert 'data-label="Status"' in script
        assert 'content: attr(data-label)' in stylesheet
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_frontend_retry_and_mobile_inspector_keep_safe_state_boundaries() -> None:
    server = create_http_server(
        DemoRunBackend(DemoCase.load_mps_001()),
        host="127.0.0.1",
        port=0,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/app.js", timeout=2) as response:
            script = response.read().decode("utf-8")

        assert "function retryFromError()" in script
        assert "function syncInspectorMode()" in script
        assert 'inspector.setAttribute("aria-hidden", "true")' in script
        assert "inspector.inert = true" in script
        assert 'inspector.setAttribute("role", "dialog")' in script
        assert 'inspector.setAttribute("aria-modal", "true")' in script
        assert 'document.querySelector(".inspector-close")?.focus()' in script
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_server_validates_start_payload_and_exposes_run_resource() -> None:
    backend = DemoRunBackend(DemoCase.load_mps_001())
    server = create_http_server(backend, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            base + "/api/runs",
            data=json.dumps({"query": backend.context()["default_query"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            started = json.loads(response.read().decode("utf-8"))
            assert response.status == 202
        assert _json(base + f"/api/runs/{started['run']['id']}")["kind"] == "running"

        bad = Request(
            base + "/api/runs",
            data=b'{"query":""}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(bad, timeout=2)
        except HTTPError as error:
            assert error.code == 400
            payload = json.loads(error.read().decode("utf-8"))
            assert payload["error"]["code"] == "INVALID_QUERY"
        else:
            raise AssertionError("empty query should have failed")

        wrong_content_type = Request(
            base + "/api/runs",
            data=json.dumps({"query": backend.context()["default_query"]}).encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        try:
            urlopen(wrong_content_type, timeout=2)
        except HTTPError as error:
            assert error.code == 415
            payload = json.loads(error.read().decode("utf-8"))
            assert payload["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
        else:
            raise AssertionError("non-JSON run request should have failed")

        wrong_host = Request(base + "/api/context", headers={"Host": "attacker.example"})
        try:
            urlopen(wrong_host, timeout=2)
        except HTTPError as error:
            assert error.code == 421
            payload = json.loads(error.read().decode("utf-8"))
            assert payload["error"]["code"] == "INVALID_HOST"
        else:
            raise AssertionError("non-local Host header should have failed")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
