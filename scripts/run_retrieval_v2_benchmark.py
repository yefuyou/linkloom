"""Run the frozen Retrieval V2 benchmark with the configured local embedder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from linkloom.indexing import SentenceTransformerEmbedder
from linkloom.retrieval_v2.benchmark import FrozenRetrievalBenchmark, write_benchmark_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evaluation/retrieval_v2_benchmark.json"),
    )
    parser.add_argument(
        "--model",
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    parser.add_argument("--no-directory-aware", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    output = args.output if args.output.is_absolute() else repo_root / args.output
    benchmark = FrozenRetrievalBenchmark(
        repo_root,
        embedder=SentenceTransformerEmbedder(args.model),
    )
    report = benchmark.run(include_directory_aware=not args.no_directory_aware)
    write_benchmark_report(report, output)
    summary = {mode: value.metrics for mode, value in report.modes.items()}
    print(json.dumps({"output": str(output), "metrics": summary}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
