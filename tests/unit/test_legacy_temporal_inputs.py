"""Preserve the exact archived synthetic experiment; never regenerate its Gold."""
import hashlib
from pathlib import Path

import pytest


@pytest.mark.legacy_historical
@pytest.mark.parametrize("name,expected", [
    ("GOLD.json", "3d5eb89aecc3ee81b36bbfd4dd7e1a59853e2340a72fc6645bddb780dc07bf62"),
    ("CASES.json", "fc9d4d7b0b58bbd4cd80724ab0aa9338bc8b333861aac2f2d5f23b9426bad844"),
])
def test_original_temporal_fixture_identity(name, expected):
    root = Path(__file__).resolve().parents[1] / "fixtures/legacy_temporal_decision"
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
