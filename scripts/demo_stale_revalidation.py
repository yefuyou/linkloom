"""Synthetic-only, keyless HTTP demo. Starts a fresh SQLite workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Event

from tests.integration.test_product_application_flow import (
    _client, _start_product_server, _stop_product_server, _wait_source,
)
from tests.integration.test_stale_decision_revalidation import OLD, NEW, explicit_claim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New empty demo directory")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    if (args.directory / "product.sqlite").exists():
        parser.error("Use a fresh directory; existing databases are never overwritten.")
    base, server, worker, app = _start_product_server(args.directory, [
        explicit_claim("Birchline", "01"), explicit_claim("Wrenwell", "07"),
    ])
    try:
        _client(base, "/api/workspaces", method="POST", data={"name": "Synthetic Revalidation Demo"})
        _, imported = _client(base, "/api/sources/import", method="POST", data={"name": "launch-decision.md", "content": OLD})
        source_id = imported["source"]["source_id"]
        _wait_source(base, source_id)
        _, inbox = _client(base, "/api/review/candidates")
        status, approved = _client(base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
                                   method="POST", data={"reviewer_id": "original-reviewer", "reason": "Synthetic initial approval"})
        if status != 200:
            raise RuntimeError(approved)
        _client(base, f"/api/sources/{source_id}", method="PUT", data={"name": "launch-decision.md", "content": NEW})
        _wait_source(base, source_id)
        metadata = {"url": base, "source_id": source_id, "original_decision_id": approved["decision_id"],
                    "provider": "injected scripted FakeProvider; synthetic claims only; no API key or network provider"}
        (args.directory / "demo.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        print(json.dumps(metadata), flush=True)
        Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_product_server(server, worker, app)


if __name__ == "__main__":
    main()
