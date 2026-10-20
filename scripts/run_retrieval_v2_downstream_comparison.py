"""Run an offline Current-versus-Hybrid downstream boundary comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from linkloom.indexing import SentenceTransformerEmbedder
from linkloom.retrieval_v2.downstream_comparison import (
    render_downstream_comparison_markdown,
    run_downstream_comparison,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("docs/evaluation/retrieval_v2_downstream_comparison.json"),
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("docs/evaluation/retrieval_v2_downstream_comparison.md"),
    )
    parser.add_argument(
        "--model",
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    json_path = args.output_json if args.output_json.is_absolute() else repo_root / args.output_json
    markdown_path = (
        args.output_markdown
        if args.output_markdown.is_absolute()
        else repo_root / args.output_markdown
    )
    report = run_downstream_comparison(
        repo_root,
        embedder=SentenceTransformerEmbedder(args.model),
    )
    report["method"]["embedding"] = args.model

    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_downstream_comparison_markdown(report),
        encoding="utf-8",
    )
    summary = {
        mode: {
            metric: values[metric]
            for metric in (
                "evidence_availability_at_5",
                "team_decision_contract_pass_rate",
                "grounding_pass_rate",
                "mean_context_bytes",
                "mean_latency_ms",
            )
        }
        for mode, values in report["modes"].items()
    }
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), "modes": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
