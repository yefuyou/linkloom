"""Small contract checks for the fair one-shot RAG baseline."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from scripts.reliability_rag_baseline import (  # noqa: E402
    CASE_IDS,
    _cases,
    _ground,
    _request,
)


def test_baseline_is_restricted_to_the_two_exposed_relation_cases():
    cases = _cases()
    assert tuple(case["id"] for case in cases) == CASE_IDS
    assert all("expected" not in case and "gold" not in case for case in cases)


def test_request_has_no_tools_and_only_verified_hit_context():
    reads = [{
        "evidence_id": "ev_test",
        "relative_path": "owner.md",
        "content_sha256": "content",
        "quote": "Mira Fern is the accountable owner of Larch Meridian.",
        "quote_sha256": "quote",
        "line_start": 1,
        "line_end": 1,
        "status": "verified",
    }]
    request = _request(CASE_IDS[0], "Who owns Larch Meridian?", reads)
    assert request.available_tools == []
    assert request.evidence_context[0].value == reads
    assert "gold" not in request.user_input.casefold()


def test_grounding_separates_supported_claims_from_unknown_citations():
    reads = [{
        "evidence_id": "ev_test",
        "relative_path": "owner.md",
        "content_sha256": "content",
        "quote": "Mira Fern is the accountable owner of Larch Meridian.",
        "quote_sha256": "quote",
        "line_start": 1,
        "line_end": 1,
        "status": "verified",
    }]
    supported = _ground({"facts": [{
        "point": "Mira Fern is the accountable owner of Larch Meridian.",
        "evidence_refs": ["ev_test"],
    }], "answer": "Mira Fern", "uncertainty": None}, reads)
    assert supported["citation_supported"] is True
    unknown = _ground({"facts": [{
        "point": "Mira Fern reports directly to Kellan Shore.",
        "evidence_refs": ["missing"],
    }], "answer": "Kellan Shore", "uncertainty": None}, reads)
    assert unknown["citation_supported"] is False
    assert unknown["one_shot_correct"] == "unscored_without_gold"
