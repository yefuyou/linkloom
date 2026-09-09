"""Command-line entry point for the offline trajectory evaluator."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from .runner import TrajectoryEvaluationRunner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run synthetic trajectory evaluation")
    parser.add_argument("--repo", default=".", help="LinkLoom repository root")
    parser.add_argument("--cases", required=True, help="Synthetic cases JSONL")
    parser.add_argument("--output", required=True, help="Artifact output directory")
    parser.add_argument(
        "--fixture",
        default=None,
        help="Optional trajectory_kb_v1 path; other fixtures are rejected",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run = TrajectoryEvaluationRunner(
            repo_root=Path(args.repo),
            cases_path=Path(args.cases),
            output_dir=Path(args.output),
            fixture_root=Path(args.fixture) if args.fixture else None,
        ).run()
    except (OSError, TypeError, ValueError) as exc:
        print(f"trajectory evaluation invalid: {exc}", file=sys.stderr)
        return 2
    counts = run.failure_summary["counts"]
    print(
        "trajectory evaluation: "
        f"{counts['PASS']} PASS, {counts['FAIL']} FAIL, "
        f"{counts['NOT_IMPLEMENTED']} NOT_IMPLEMENTED"
    )
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
