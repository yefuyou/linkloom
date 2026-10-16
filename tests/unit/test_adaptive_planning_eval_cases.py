"""Integrity checks for the answer-blind adaptive-planning evaluation data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


FIXTURES = Path(__file__).parents[1] / "fixtures" / "adaptive_planning"
PARTITIONS = ("dev", "validation", "heldout")
PUBLIC_FORBIDDEN_KEYS = {
    "answer",
    "expected",
    "expected_chain",
    "expected_facts",
    "facts",
    "gold",
    "insufficient_reason",
    "refusal_reason",
    "outcome",
    "evidence_paths",
    "root",
}


def _read(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_partitions_are_sized_and_queries_are_unique() -> None:
    public = [_read(f"public_{part}.json") for part in PARTITIONS]
    assert [len(item["cases"]) for item in public] == [24, 18, 18]
    cases = [case for dataset in public for case in dataset["cases"]]
    assert len(cases) >= 60
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["query"] for case in cases}) == len(cases)
    assert {dataset["partition"] for dataset in public} == set(PARTITIONS)


def test_public_inputs_have_no_gold_answer_fields() -> None:
    for part in PARTITIONS:
        public = _read(f"public_{part}.json")
        assert not PUBLIC_FORBIDDEN_KEYS.intersection(_keys(public))
        for case in public["cases"]:
            assert {"id", "query", "category", "documents", "expected_route", "support_flags"} <= case.keys()
            assert case["documents"]
            for document in case["documents"]:
                assert set(document) == {"path", "content"}
                assert isinstance(document["path"], str)
                assert isinstance(document["content"], str)


def test_gold_ids_match_their_public_partition() -> None:
    for part in PARTITIONS:
        public = _read(f"public_{part}.json")
        gold = _read(f"gold_{part}.json")
        public_ids = {case["id"] for case in public["cases"]}
        gold_ids = {case["id"] for case in gold["cases"]}
        assert public_ids == gold_ids
        assert len(gold_ids) == len(gold["cases"])


def test_heldout_files_match_the_frozen_hash_manifest() -> None:
    freeze = _read("freeze_heldout.json")
    for name, expected in freeze["files"].items():
        actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert actual == expected
