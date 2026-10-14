"""Start the repeatable synthetic Unified Ask browser demo.

Example::

    $env:PYTHONPATH = "src"
    $env:LINKLOOM_RETRIEVAL_MODE = "bm25"
    python tests/integration/run_unified_ask_demo.py --port 8794

The generated SQLite database is placed under ``work/`` and contains only the
synthetic Atlas/Other fixture.  Press Ctrl+C to stop the localhost server.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from uuid import uuid4

from unified_ask_fixture import ATLAS, OTHER, UnifiedAskFixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline Unified Ask browser fixture")
    parser.add_argument("--port", type=int, default=8794, help="localhost port (0 chooses a free port)")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="optional SQLite path; otherwise work/unified_ask_demo/run-<id>.sqlite",
    )
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parents[2]
    database_path = args.db or repo / "work" / "unified_ask_demo" / f"run-{uuid4().hex[:12]}.sqlite"
    fixture = UnifiedAskFixture(database_path, port=args.port)
    fixture.configure_agent(external_provider=False)
    atlas_id = fixture.workspace_ids[ATLAS]  # type: ignore[index]
    other_id = fixture.workspace_ids[OTHER]  # type: ignore[index]
    print(f"Unified Ask offline demo: {fixture.base_url}", flush=True)
    print(f"database: {database_path}", flush=True)
    print(f"Atlas workspace_id: {atlas_id}", flush=True)
    print(f"Other workspace_id: {other_id}", flush=True)
    print("Fake Agent is enabled with external_provider=False; no credentials or network calls are used.", flush=True)
    print("Try: current lookup, historical lookup, 'Why did Launch change suppliers?', 'uncertain provider', or 'fail provider'.", flush=True)
    try:
        # UnifiedAskFixture starts the HTTPServer on its worker thread.  Keep
        # the CLI process alive by joining that worker instead of calling
        # serve_forever a second time on the same server.
        while fixture.worker is not None and fixture.worker.is_alive():
            fixture.worker.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping offline Unified Ask demo.", flush=True)
    finally:
        fixture.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
