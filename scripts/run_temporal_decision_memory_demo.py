from __future__ import annotations

import argparse
import json
from pathlib import Path

from linkloom.decision_memory.demo import write_demo_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Temporal Decision Memory demo.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evaluation/temporal_decision_memory_demo.json"),
    )
    args = parser.parse_args()
    output = write_demo_report(args.output.resolve())
    print(json.dumps({"output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
