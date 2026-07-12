"""Command-line entry point for linkloom's read-only tools."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from linkloom.scanner import ScannerError, scan_vault


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linkloom")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="Create a read-only index for a Markdown vault."
    )
    scan_parser.add_argument("input_root", type=Path, help="Markdown vault root.")
    scan_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory for generated artifacts. It must be outside the input root.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command != "scan":
        return 2

    try:
        result = scan_vault(args.input_root, args.output)
    except ScannerError as error:
        print(f"Error: {error}", file=sys.stderr)
        return error.exit_code
    except OSError as error:
        print(f"Error: could not write scan artifacts: {error}", file=sys.stderr)
        return 1

    print(f"Indexed notes: {result.note_count}")
    print(f"Warnings: {result.warning_count}")
    print(f"Index: {result.index_path}")
    print(f"Summary: {result.summary_path}")
    return 0

