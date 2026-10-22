#!/usr/bin/env python3
"""Run the public, deterministic M3-P Product UI acceptance path.

The script starts the normal local HTTP server with synthetic sources, submits
one explicitly scoped task investigation, then verifies a fresh-process style
cold read from saved Runtime artifacts.  It never configures a Provider or
writes a task relation record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from threading import Thread
from time import monotonic, sleep
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from linkloom.ui.demo import DemoCase, DemoRunBackend  # noqa: E402
from linkloom.ui.investigation import ProductInvestigationService  # noqa: E402
from linkloom.ui.offline_task_demo import build_offline_task_demo  # noqa: E402
from linkloom.ui.product import ProductApplication  # noqa: E402
from linkloom.ui.server import create_http_server  # noqa: E402


QUESTION = "请调查这几次会议里交付任务如何延续，截止日期的变动是否有可靠依据？"


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _output_directory(value: str | None) -> Path:
    output = (Path(value).expanduser() if value else
              ROOT / "work" / f"m3p-offline-{uuid4().hex[:12]}").resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing acceptance output: {output}")
    output.mkdir(parents=True)
    return output


def _request(base: str, path: str, *, method: str = "GET", payload: dict | None = None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _serve(app, service):
    server = create_http_server(
        DemoRunBackend(DemoCase.load_mps_001()),
        product_app=app,
        investigation_service=service,
        host="127.0.0.1",
        port=0,
    )
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, f"http://127.0.0.1:{server.server_port}"


def _wait(base: str, workspace_id: str, run_id: str) -> dict:
    deadline = monotonic() + 60
    while monotonic() < deadline:
        status, payload = _request(
            base,
            f"/api/product/ask/runs/{run_id}?workspace_id={workspace_id}",
        )
        if status != 200:
            raise RuntimeError(f"unexpected inspection status: {status} {payload}")
        result = payload["result"]
        if result["status"] != "INVESTIGATING":
            return result
        sleep(0.05)
    raise TimeoutError("offline task investigation did not finish within 60 seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        help="New directory for the database, HTTP evidence and Runtime artifacts; existing directories are rejected.",
    )
    args = parser.parse_args(argv)
    output = _output_directory(args.output)
    database = output / "product.sqlite"
    app, service = build_offline_task_demo(database)
    server = worker = None
    started = None
    result = None
    try:
        server, worker, base = _serve(app, service)
        status, config = _request(base, "/api/product/ask/config")
        if status != 200 or "investigation_context" not in config:
            raise RuntimeError(f"invalid Ask config: {status} {config}")
        _write_json(output / "config.json", config)
        workspace_id = config["workspace_id"]
        status, sources = _request(base, "/api/sources")
        if status != 200:
            raise RuntimeError(f"source listing failed: {status} {sources}")
        source_rows = [app.repository.get_source(workspace_id, row["source_id"], include_content=True)
                       for row in sources.get("sources", [])]
        _write_json(output / "source.json", {"workspace_id": workspace_id, "sources": source_rows})
        request_payload = {
            "query": QUESTION,
            "workspace_id": workspace_id,
            "investigation_context": {
                "domain": "tasks",
                "snapshot_hash": config["investigation_context"]["snapshot_hash"],
            },
        }
        _write_json(output / "request.json", request_payload)
        status, started = _request(base, "/api/product/ask", method="POST", payload=request_payload)
        if status not in {200, 202}:
            raise RuntimeError(f"unexpected Ask status: {status} {started}")
        _write_json(output / "started.json", started)
        result = _wait(base, workspace_id, started["result"]["agent_run_id"])
        if result["path"] != "TASK_INVESTIGATION":
            raise RuntimeError(f"task path was not selected: {result}")
        task_review = result.get("task_review") or {}
        observations = task_review.get("observations") or []
        reviewer = result.get("reviewer") or {}
        evidence = result.get("evidence") or []
        if (
            result.get("status") != "PARTIAL"
            or reviewer.get("accepted") is not True
            or len(observations) != 2
            or len(evidence) != 2
            or observations[1].get("deadline_role") != "UNKNOWN"
        ):
            raise RuntimeError(
                "offline acceptance requires PARTIAL, accepted review, two observations and two "
                f"evidence rows with second deadline_role UNKNOWN: {result}"
            )
        if any(item.get("verified") is not True for item in evidence):
            raise RuntimeError(f"offline acceptance contains unverified evidence: {evidence}")
        _write_json(output / "final.json", result)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if worker is not None:
            worker.join(timeout=3)
        app.close()

    # A new ProductApplication has no in-memory service run map or stage adapters.
    fresh = ProductApplication(database_path=database, extractor=None)
    restored = ProductInvestigationService(fresh)
    server = worker = None
    try:
        server, worker, base = _serve(fresh, restored)
        status, cold = _request(
            base,
            f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace_id}",
        )
        cold_result = cold.get("result") or {}
        if (
            status != 200
            or cold_result.get("recovery", {}).get("mode") != "SAVED_RESULT_READ_ONLY"
            or cold_result.get("status") != "PARTIAL"
            or cold_result.get("reviewer", {}).get("accepted") is not True
        ):
            raise RuntimeError(f"cold read did not revalidate saved artifacts: {status} {cold}")
        _write_json(output / "cold.json", cold)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if worker is not None:
            worker.join(timeout=3)
        fresh.close()

    runtime_files = sorted(
        str(path.relative_to(output)) for path in output.rglob("*")
        if path.is_file() and path.name != "runtime.json"
    )
    _write_json(output / "runtime.json", {"root": "work/investigations", "files": runtime_files})
    summary = {
        "status": "PASS",
        "mode": "OFFLINE_TASK_DEMO",
        "model": "FakeModel",
        "provider_requests": 0,
        "business_approval": False,
        "cold_read": "SAVED_RESULT_READ_ONLY",
        "output": str(output),
    }
    _write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
