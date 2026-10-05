from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from linkloom.retrieval_v2.downstream_comparison import (
    render_downstream_comparison_markdown,
    run_downstream_comparison,
)


class HashingEmbedder:
    dimensions = 64

    def encode(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.casefold().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                matrix[row, int.from_bytes(digest[:2], "big") % self.dimensions] += 1.0
        return matrix


def test_downstream_comparison_covers_contract_grounding_context_and_limits_claims() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    report = run_downstream_comparison(repo_root, embedder=HashingEmbedder())

    assert report["dataset"]["case_count"] == 30
    assert report["dataset"]["workspace_count"] == 6
    assert report["dataset"]["document_count"] == 36
    assert report["dataset"]["gold_unchanged"] is True
    assert set(report["modes"]) == {"current", "hybrid"}
    markdown = render_downstream_comparison_markdown(report)
    assert "Semantic result |" in markdown
    assert "N/E" in markdown
    assert "does not establish semantic accuracy" in markdown

    for mode in report["modes"].values():
        assert mode["case_count"] == 30
        assert 0.0 <= mode["evidence_availability_at_5"] <= 1.0
        assert mode["team_decision_contract_pass_rate"] == 1.0
        assert mode["grounding_pass_rate"] == 1.0
        assert mode["mean_context_bytes"] > 0
        assert mode["mean_latency_ms"] >= 0
        assert mode["cold_start_case_count"] == 6
        assert mode["semantic_result"]["status"] == "N/E"


def test_downstream_comparison_rejects_non_top_five_metrics() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    with pytest.raises(ValueError, match="fixed at top_k=5"):
        run_downstream_comparison(repo_root, embedder=HashingEmbedder(), top_k=3)
