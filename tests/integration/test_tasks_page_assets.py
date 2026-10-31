"""The separate formal-task page is reachable through the local HTTP host."""
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

from linkloom.ui.server import create_http_server
from tests.task_mvp_browser_server import BrowserBackend


@pytest.mark.parametrize("route,filename,content_type", [
    ("/tasks", "tasks.html", "text/html"),
    ("/tasks.js", "tasks.js", "text/javascript"),
    ("/tasks.css", "tasks.css", "text/css"),
])
def test_formal_task_assets_have_independent_http_routes(route, filename, content_type):
    host = create_http_server(BrowserBackend(), port=0)
    thread = Thread(target=lambda: host.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{host.server_port}" + route, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith(content_type)
            assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
            expected = Path(__file__).parents[2] / "src/linkloom/ui/static" / filename
            assert response.read() == expected.read_bytes()
    finally:
        host.shutdown()
        thread.join(timeout=2)
        host.server_close()
