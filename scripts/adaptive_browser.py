"""One synthetic live UI investigation, sharing Linkloom B's single budget."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Thread
import time

from scripts.adaptive_live import ROOT, OUT, connect, write_new
from scripts.adaptive_evaluation import _seed_sources, _UnusedBackend, ObservedAdapter, _preserve_run_artifacts
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.server import create_http_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="AP-D10")
    parser.add_argument("--trial", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    if not args.run or not all(c.isalnum() or c == "-" for c in args.run):
        raise ValueError("INVALID_RUN_ID")
    cases = json.loads((ROOT / "tests/fixtures/adaptive_planning/public_dev.json").read_text(encoding="utf8"))["cases"]
    case = next(c for c in cases if c["id"] == args.case)
    root = OUT / "browser" / args.run
    root.mkdir(parents=True, exist_ok=False)
    app = ProductApplication(database_path=root / "product.sqlite", extractor=None)
    workspace, source_names = _seed_sources(app, case, root)
    sdk, provider, identity = connect()
    model = ObservedAdapter(provider, case["query"])
    created = []
    def factory(query):
        if created or query != case["query"]:
            raise ValueError("BROWSER_SINGLE_FROZEN_TASK_ONLY")
        created.append(query)
        return model
    service = ProductInvestigationService(app, model_factory=factory, external_provider=True,
        adaptive_planning=True, relation_max_steps=12, run_mode="LIVE_GEMINI_SYNTHETIC_UI")
    server = create_http_server(_UnusedBackend(), product_app=app, investigation_service=service, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    with provider.task(case["id"], args.trial, "B"):
        worker.start()
        packet = {"url": f"http://127.0.0.1:{server.server_port}", "query": case["query"],
                  "case_id": case["id"], "workspace_id": workspace, "stop_file": str((root / "stop").resolve())}
        write_new(root / "ready.json", packet)
        print(json.dumps(packet, ensure_ascii=False), flush=True)
        started, captured = time.monotonic(), False
        try:
            while not (root / "stop").exists() and time.monotonic() - started < 900:
                if not captured:
                    for run_id in list(server.unified_ask._runs):
                        result, status = server.unified_ask.inspect(run_id, workspace)
                        if result["status"] != "INVESTIGATING":
                            write_new(root / "final.json", result)
                            # Same original artifacts as the HTTP benchmark.
                            _preserve_run_artifacts(root, service, run_id,
                                {"query": case["query"], "workspace_id": workspace}, None, result)
                            print(json.dumps({"terminal": result["status"], "accounting": provider.summary()}), flush=True)
                            captured = True
                time.sleep(.5)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            write_new(root / "accounting.json", provider.summary())
            sdk.close()
            app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
