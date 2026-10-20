"""Raw-byte integrity of the one seal-validated reconstructed historical ledger."""

import hashlib
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
LEDGER = (
    "docs/evaluation/public_memory_benchmark_clean/"
    "e2_revised_mh_only_20261005/final_evaluation_v1_20261005/"
    "formal_rehearsal_budget_ledger.json"
)
ORIGINAL_SHA256 = "a882d54781f8c505558c459a5bc0ab17726723f67dfe06e8545c505a06386640"


def _git(*args, data=None):
    return subprocess.run(
        ["git", *args], cwd=ROOT, input=data, capture_output=True, check=True,
    ).stdout


def test_committed_blob_and_worktree_match_the_unchanged_original_seal():
    seal_bytes = _git("cat-file", "blob", f"HEAD:{LEDGER}.seal.json")
    assert json.loads(seal_bytes)["sha256"] == ORIGINAL_SHA256
    assert json.loads((ROOT / (LEDGER + ".seal.json")).read_bytes())["sha256"] == ORIGINAL_SHA256
    blob = _git("cat-file", "blob", f"HEAD:{LEDGER}")
    disk = (ROOT / LEDGER).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == ORIGINAL_SHA256
    assert hashlib.sha256(disk).hexdigest() == ORIGINAL_SHA256
    assert blob == disk
    assert len(blob) == 3888 and blob.count(b"\r\n") == 81


def test_ledger_has_exact_byte_preserving_attribute():
    assert _git("check-attr", "-z", "text", "--", LEDGER).split(b"\0") == [
        LEDGER.encode(), b"text", b"unset", b"",
    ]


@pytest.mark.parametrize("autocrlf,eol", [("false", "lf"), ("true", "crlf"), ("input", "lf")])
def test_checkout_and_clean_filters_preserve_sealed_bytes(autocrlf, eol):
    """Exercise both Git filter directions without changing config or any file."""
    config = ("-c", f"core.autocrlf={autocrlf}", "-c", f"core.eol={eol}")
    checked_out = _git(*config, "cat-file", "--filters", f"HEAD:{LEDGER}")
    assert hashlib.sha256(checked_out).hexdigest() == ORIGINAL_SHA256
    assert checked_out == (ROOT / LEDGER).read_bytes()
    # hash-object without -w verifies what git add would store, without writing.
    cleaned_oid = _git(*config, "hash-object", f"--path={LEDGER}", "--stdin", data=checked_out)
    assert cleaned_oid.strip() == _git("rev-parse", f"HEAD:{LEDGER}").strip()
