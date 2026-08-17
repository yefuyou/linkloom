"""Inference-side compatibility helpers.

This module has no file writes, network calls, provider credentials, or
imports from the experimental evaluator. ``mock_inference`` is retained for
the small legacy contract test; the formal runner uses its own evaluator-side
oracle builder and persists only sanitized prediction records.
"""

from __future__ import annotations

from typing import List

from .dataset import EvaluationDataset
from .models import PredictionRecord


def mock_inference(dataset: EvaluationDataset, scenario: str = "perfect") -> List[PredictionRecord]:
    """Return deterministic relation records for the legacy contract tests.

    This is explicitly a test double, not a model baseline. The formal runner
    marks all such runs as oracle plumbing and baseline-ineligible.
    """
    if scenario not in {"perfect", "noisy"}:
        return []
    records: list[PredictionRecord] = []
    for pair in dataset.get_gold_pairs():
        records.append(
            PredictionRecord(
                source_id=pair.source,
                target_id=pair.target,
                relation_type=pair.relation_type,
                direction="forward",
                evidence_refs=[],
                should_link=pair.should_link,
                confidence=1.0 if scenario == "perfect" else 0.8,
            )
        )
    if scenario == "noisy":
        records.append(
            PredictionRecord(
                source_id="mock_fp_source",
                target_id="mock_fp_target",
                relation_type="concept-to-practice",
                direction="forward",
                evidence_refs=[],
                should_link=True,
                confidence=0.5,
            )
        )
    return records
