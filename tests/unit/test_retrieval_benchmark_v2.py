from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from linkloom.retrieval_v2.benchmark import FrozenRetrievalBenchmark


class HashingEmbedder:
    """Deterministic local stand-in; the real benchmark uses SentenceTransformer."""

    dimensions = 64

    def encode(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.casefold().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                matrix[row, int.from_bytes(digest[:2], "big") % self.dimensions] += 1.0
        return matrix


def test_frozen_benchmark_loads_30_cases_36_notes_and_runs_all_modes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    benchmark = FrozenRetrievalBenchmark(repo_root, embedder=HashingEmbedder())

    assert len(benchmark.cases) == 30
    assert len(benchmark.documents) == 36
    assert len({document.workspace_id for document in benchmark.documents}) == 6

    report = benchmark.run(include_directory_aware=True)

    assert set(report.modes) == {"current", "bm25", "dense", "hybrid", "directory_hybrid"}
    assert all(len(mode_report.cases) == 30 for mode_report in report.modes.values())
    assert all(0.0 <= mode_report.metrics["recall_at_5"] <= 1.0 for mode_report in report.modes.values())
    assert report.gold_sha256_before == report.gold_sha256_after
    assert '"relevant_source_refs"' in json.dumps(report.to_dict())
