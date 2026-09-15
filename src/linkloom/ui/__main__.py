"""Run the deterministic local Product UI preview."""

from __future__ import annotations

import argparse

from linkloom.ui.demo import DemoCase, DemoRunBackend
from linkloom.ui.server import create_http_server


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m linkloom.ui")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    backend = DemoRunBackend(DemoCase.load_mps_001())
    server = create_http_server(backend, host=args.host, port=args.port)
    print(f"LinkLoom Product UI demo: http://{args.host}:{server.server_port}")
    print("Read-only demo fixture: mps-001")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
