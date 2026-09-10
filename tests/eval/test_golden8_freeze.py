"""Regression contract for the formally frozen Team Decision Golden 8."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATASET_RELATIVE_PATH = Path(
    "docs/requirements/m1_team_decision_eval_seed/dataset.jsonl"
)
MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "docs/requirements/m1_team_decision_eval_seed/golden8_manifest.json"
)
CANONICAL_DATASET_SHA256 = (
    "49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F"
)
GOLDEN8_CASE_IDS = [
    "mps-001",
    "aer-002",
    "drm-003",
    "inc-004",
    "ret-005",
    "iti-005",
    "drm-002",
    "aer-005",
]


def _git_dataset_bytes() -> bytes:
    return subprocess.check_output(
        ["git", "show", f"HEAD:{DATASET_RELATIVE_PATH.as_posix()}"],
        cwd=REPOSITORY_ROOT,
    )


def test_golden8_manifest_locks_the_canonical_dataset_and_exact_case_order() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    dataset_bytes = _git_dataset_bytes()
    dataset = [
        json.loads(line)
        for line in dataset_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    dataset_case_ids = [case["case_id"] for case in dataset]

    assert manifest["canonical_dataset_sha256"] == CANONICAL_DATASET_SHA256
    assert manifest["case_ids"] == GOLDEN8_CASE_IDS
    assert len(manifest["case_ids"]) == 8
    assert len(set(manifest["case_ids"])) == 8
    assert all(case_id in dataset_case_ids for case_id in manifest["case_ids"])
    assert hashlib.sha256(dataset_bytes).hexdigest().upper() == CANONICAL_DATASET_SHA256
