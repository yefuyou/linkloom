"""Structural and freeze checks for the V2 authoring output.

These tests intentionally do not parse ``final_public.json`` or
``final_gold.json``.  Independent-final validation uses only the frozen hash
manifest and the metadata catalog; a scoring worker owns any Gold reads.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest

from scripts.self_improvement.v2_dataset import (
    ATTACK_KINDS,
    DATASET_REVISION,
    DEFAULT_ROOT,
    PUBLIC_BASE_KEYS,
    PUBLIC_OPTIONAL_KEYS,
    SCHEMA_VERSION,
    SPLIT_COUNTS,
    validate_gold_case,
    validate_public_case,
)


ROOT = Path(DEFAULT_ROOT)


def _read_json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v2_freeze_has_expected_counts_and_hashes_without_reading_final_json() -> None:
    freeze = _read_json("freeze.json")
    assert freeze["schema_version"] == SCHEMA_VERSION
    assert freeze["dataset_revision"] == DATASET_REVISION
    assert freeze["counts"] == SPLIT_COUNTS
    assert freeze["final_validation"] == "catalog_and_hashes_only"
    assert freeze["gold_access"] == "scorer_only"
    assert "final_public.json" in freeze["files"]
    assert "final_gold.json" in freeze["files"]

    # Hashing bytes is permitted for freeze integrity; this test never parses
    # either final JSON file.
    for name, expected in freeze["files"].items():
        assert _sha256(ROOT / name) == expected, name


def test_catalog_is_metadata_only_and_has_all_split_rows() -> None:
    catalog = _read_json("catalog.json")
    assert catalog["schema_version"] == SCHEMA_VERSION
    assert catalog["dataset_revision"] == DATASET_REVISION
    assert catalog["gold_access"] == "scorer_only"
    assert len(catalog["cases"]) == sum(SPLIT_COUNTS.values())
    by_split = {split: [] for split in SPLIT_COUNTS}
    for row in catalog["cases"]:
        assert set(row) >= {
            "id",
            "split",
            "category",
            "family",
            "structural_signature",
            "capability",
        }
        assert not (set(row) & {"query", "documents", "values", "required_sources", "parent_facts"})
        by_split[row["split"]].append(row)
        assert row["capability"]["classification"] in {
            "planner_and_grounding_supported",
            "planner_unsupported",
            "source_unsupported",
        }
    assert {split: len(rows) for split, rows in by_split.items()} == SPLIT_COUNTS

    dev_families = {row["family"] for row in by_split["dev"]}
    final_families = {row["family"] for row in by_split["final"]}
    dev_signatures = {row["structural_signature"] for row in by_split["dev"]}
    final_signatures = {row["structural_signature"] for row in by_split["final"]}
    assert dev_families.isdisjoint(final_families)
    assert dev_signatures.isdisjoint(final_signatures)
    assert len(dev_signatures) == len(by_split["dev"])
    assert len(final_signatures) == len(by_split["final"])


@pytest.mark.parametrize("split", ["dev", "validation", "stress"])
def test_non_final_public_schema_is_exact(split: str) -> None:
    payload = _read_json(f"{split}_public.json")
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["partition"] == split
    assert len(payload["cases"]) == SPLIT_COUNTS[split]
    for case in payload["cases"]:
        validate_public_case(case)
        assert set(case) <= set(PUBLIC_BASE_KEYS) | PUBLIC_OPTIONAL_KEYS
        scenario = case.get("scenario")
        if scenario:
            assert scenario["kind"] in ATTACK_KINDS


@pytest.mark.parametrize("split", ["dev", "validation", "stress"])
def test_non_final_gold_schema_is_scoring_only(split: str) -> None:
    payload = _read_json(f"{split}_gold.json")
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["partition"] == split
    assert len(payload["cases"]) == SPLIT_COUNTS[split]
    for case in payload["cases"]:
        validate_gold_case(case)
        spans = case["expected"]["target_spans"]
        assert all(set(span) == {"path", "contains"} for span in spans)


def test_catalog_exposes_capability_boundary_and_supported_chinese_counterexample() -> None:
    catalog = _read_json("catalog.json")
    rows = catalog["cases"]
    unsupported_planner = [row for row in rows if row["capability"]["classification"] == "planner_unsupported"]
    unsupported_source = [row for row in rows if row["capability"]["classification"] == "source_unsupported"]
    assert unsupported_planner
    assert unsupported_source
    assert all(not row["capability"]["planner_supported"] for row in unsupported_planner)
    assert all(row["capability"]["planner_supported"] and not row["capability"]["grounding_supported"] for row in unsupported_source)

    chinese = [row for row in rows if "v1_filter_counterexample" in row["tags"]]
    assert chinese
    assert all(row["capability"]["planner_supported"] for row in chinese)
    assert all(row["capability"]["grounding_supported"] for row in chinese)


def test_baseline_calibration_is_sixteen_cases_and_does_not_dominate() -> None:
    catalog = _read_json("catalog.json")
    baseline = [row for row in catalog["cases"] if "baseline" in row["tags"]]
    assert len(baseline) == 16
    assert len(baseline) < len(catalog["cases"]) / 2
    assert all("known_safe" in row["tags"] for row in baseline)


def test_attack_catalog_covers_every_deterministic_scenario() -> None:
    catalog = _read_json("catalog.json")
    kinds = {row["scenario_kind"] for row in catalog["cases"] if "scenario_kind" in row}
    assert kinds == ATTACK_KINDS
    attack_rows = [row for row in catalog["cases"] if "scenario_kind" in row]
    assert len(attack_rows) >= 16
    assert all(1 <= row["max_steps"] <= 20 for row in attack_rows if "max_steps" in row)


def test_catalog_signatures_are_normalized_and_documents_are_modest() -> None:
    catalog = _read_json("catalog.json")
    for row in catalog["cases"]:
        signature = row["structural_signature"]
        assert not re.search(r"Meridian|Owner|Lead|青岚|林|周", signature)
        if row["document_count"] > 12:
            assert "top_k_edge" in row["tags"] or row["split"] == "stress"
