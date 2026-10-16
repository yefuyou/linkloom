"""Pair admission is checked before any SDK or ledger work."""
import json
from pathlib import Path

import pytest
from scripts.contract_v3_live import verify_pair_identity


def baseline(root, **changes):
    folder = root / "old"
    folder.mkdir()
    record = {"phase": "A", "case": "case-one", "case_sha256": "sealed-snapshot-and-query", "planning": "rules", **changes}
    (folder / "attempt.json").write_text(json.dumps(record), encoding="utf-8")
    (folder / "result.json").write_text("{}", encoding="utf-8")


def test_pair_identity_accepts_same_sealed_input(tmp_path):
    baseline(tmp_path)
    result = verify_pair_identity({"id": "case-one"}, "sealed-snapshot-and-query", "rules", tmp_path)
    assert len(result["baseline_attempt_sha256"]) == 64


@pytest.mark.parametrize("changes", [{"case_sha256": "changed-snapshot"}, {"planning": "model"}])
def test_pair_identity_rejects_changed_data_or_planning(tmp_path, changes):
    baseline(tmp_path, **changes)
    with pytest.raises(RuntimeError, match="PAIR_INPUT_IDENTITY_MISMATCH"):
        verify_pair_identity({"id": "case-one"}, "sealed-snapshot-and-query", "rules", tmp_path)


def test_pair_requires_one_terminal_baseline(tmp_path):
    with pytest.raises(RuntimeError, match="PAIR_REQUIRES_ONE_BASELINE"):
        verify_pair_identity({"id": "case-one"}, "sealed", "rules", tmp_path)
