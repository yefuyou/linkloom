from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"
sys.path.insert(0, str(SRC_ROOT))

from linkloom.loader import load_vault  # noqa: E402
from linkloom.retrieval import find_relation_candidates, retrieve_evidence, tokenize  # noqa: E402
from linkloom.scanner import scan_vault  # noqa: E402
from linkloom.schemas import NoteDocument  # noqa: E402


def test_tokenize_handles_mixed_cjk_and_english() -> None:
    tokens = tokenize("Scanner Overview 扫描入门 123 [[设计笔记]] #agent")
    assert "scanner" in tokens
    assert "overview" in tokens
    assert "扫描入门" in tokens
    assert "123" in tokens
    assert "设计笔记" in tokens
    assert "agent" in tokens


def test_retrieve_evidence_matches_headings_and_body(tmp_path: Path) -> None:
    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)
    documents, _, _ = load_vault(FIXTURE_ROOT, scan_result.index_path)

    evidence = retrieve_evidence(query="Scanner Overview", documents=documents, max_results=5)

    assert len(evidence) > 0
    # Top evidence should match heading in 00-扫描入门.md
    top = evidence[0]
    assert top.relative_path == "00-扫描入门.md"
    assert "Scanner Overview" in top.quote
    assert top.line_start == 7
    assert top.line_end == 7
    assert top.status == "verified"
    assert top.quote_sha256 == hashlib.sha256(top.quote.encode("utf-8")).hexdigest()
    assert top.evidence_id == "ev_p1_0001"


def test_retrieve_evidence_no_match_returns_empty(tmp_path: Path) -> None:
    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)
    documents, _, _ = load_vault(FIXTURE_ROOT, scan_result.index_path)

    evidence = retrieve_evidence(query="nonexistent_xyz_query_term_12345", documents=documents)
    assert evidence == []


def test_retrieve_evidence_exact_quote_validation() -> None:
    doc = NoteDocument(
        relative_path="test.md",
        title="Test Note",
        content="# Test Note\n\nLine 3 has alpha and beta.\nLine 4 has gamma.",
        headings=[{"level": 1, "text": "Test Note", "line": 1}],
        tags=["test"],
        wikilinks=[],
        size_bytes=50,
        content_sha256=hashlib.sha256(b"dummy").hexdigest(),
        line_count=4,
    )

    evidence = retrieve_evidence(query="alpha", documents=[doc], max_results=2)
    assert len(evidence) == 1
    assert evidence[0].line_start == 3
    assert evidence[0].quote == "Line 3 has alpha and beta."
    # Verify exact line match
    lines = doc.content.splitlines()
    assert lines[evidence[0].line_start - 1] == evidence[0].quote


def test_retrieve_evidence_deterministic_tie_breaking() -> None:
    doc_a = NoteDocument(
        relative_path="a.md",
        title="Note A",
        content="Same keyword match.",
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=20,
        content_sha256="a" * 64,
        line_count=1,
    )
    doc_b = NoteDocument(
        relative_path="b.md",
        title="Note B",
        content="Same keyword match.",
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=20,
        content_sha256="b" * 64,
        line_count=1,
    )

    ev1 = retrieve_evidence("keyword", [doc_b, doc_a])
    ev2 = retrieve_evidence("keyword", [doc_a, doc_b])

    assert [e.relative_path for e in ev1] == ["a.md", "b.md"]
    assert [e.relative_path for e in ev2] == ["a.md", "b.md"]


def test_find_relation_candidates_structural_signals(tmp_path: Path) -> None:
    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)
    documents, _, _ = load_vault(FIXTURE_ROOT, scan_result.index_path)

    candidates = find_relation_candidates(documents=documents, max_candidates=10)

    assert len(candidates) > 0
    # Check that left < right for all candidates
    for cand in candidates:
        assert cand.left["relative_path"] < cand.right["relative_path"]
        assert cand.left["relative_path"] != cand.right["relative_path"]
        assert cand.rank_score > 0
        assert len(cand.score_breakdown) > 0
        assert cand.status == "candidate"

    # Specific check: 00-扫描入门.md and projects/设计笔记.md have wikilinks and/or shared structure
    intro_design_cand = next(
        (
            c
            for c in candidates
            if c.left["relative_path"] == "00-扫描入门.md"
            and c.right["relative_path"] == "projects/设计笔记.md"
        ),
        None,
    )
    assert intro_design_cand is not None
    kinds = [item["kind"] for item in intro_design_cand.score_breakdown]
    assert "wikilink" in kinds
    assert len(intro_design_cand.evidence) > 0


def test_retrieve_evidence_title_retrieval_returns_zero_when_no_source_line_exists() -> None:
    doc = NoteDocument(
        relative_path="a.md",
        title="OnlyTitle",
        content="body without title string",
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=25,
        content_sha256=hashlib.sha256(b"dummy").hexdigest(),
        line_count=1,
    )
    evidence = retrieve_evidence("OnlyTitle", [doc])
    assert evidence == []


def test_retrieve_evidence_title_retrieval_with_frontmatter() -> None:
    content = "---\ntitle: OnlyTitle\n---\nbody text"
    doc = NoteDocument(
        relative_path="a.md",
        title="OnlyTitle",
        content=content,
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=len(content.encode("utf-8")),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        line_count=4,
    )
    evidence = retrieve_evidence("OnlyTitle", [doc])
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.line_start == 2
    assert ev.line_end == 2
    assert ev.quote == "title: OnlyTitle"
    assert ev.source_kind == "note_body"
    assert ev.status == "verified"
    assert ev.quote_sha256 == hashlib.sha256(b"title: OnlyTitle").hexdigest()


def test_retrieve_evidence_title_retrieval_with_heading() -> None:
    content = "# OnlyTitle\nbody text"
    doc = NoteDocument(
        relative_path="a.md",
        title="OnlyTitle",
        content=content,
        headings=[{"level": 1, "text": "OnlyTitle", "line": 1}],
        tags=[],
        wikilinks=[],
        size_bytes=len(content.encode("utf-8")),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        line_count=2,
    )
    evidence = retrieve_evidence("OnlyTitle", [doc])
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.line_start == 1
    assert ev.line_end == 1
    assert ev.quote == "# OnlyTitle"
    assert ev.source_kind == "heading"
    assert ev.status == "verified"


def test_find_relation_candidates_omits_candidates_without_verified_source_quotes() -> None:
    # Reproducer: headings and tags declared in metadata but neither query nor title nor signals exist in body lines
    doc_a = NoteDocument(
        relative_path="a.md",
        title="ZZZTitle",
        content="bodyA",
        headings=[{"level": 2, "text": "Shared", "line": 1}],
        tags=["shared"],
        wikilinks=[],
        size_bytes=5,
        content_sha256="a" * 64,
        line_count=1,
    )
    doc_b = NoteDocument(
        relative_path="b.md",
        title="YYYTitle",
        content="bodyB",
        headings=[{"level": 2, "text": "Shared", "line": 1}],
        tags=["shared"],
        wikilinks=[],
        size_bytes=5,
        content_sha256="b" * 64,
        line_count=1,
    )
    candidates = find_relation_candidates([doc_a, doc_b])
    assert candidates == []


def test_find_relation_candidates_includes_candidates_with_verified_source_quotes() -> None:
    doc_a = NoteDocument(
        relative_path="a.md",
        title="ZZZTitle",
        content="# Shared\nbodyA",
        headings=[{"level": 1, "text": "Shared", "line": 1}],
        tags=["shared"],
        wikilinks=[],
        size_bytes=15,
        content_sha256="a" * 64,
        line_count=2,
    )
    doc_b = NoteDocument(
        relative_path="b.md",
        title="YYYTitle",
        content="# Shared\nbodyB",
        headings=[{"level": 1, "text": "Shared", "line": 1}],
        tags=["shared"],
        wikilinks=[],
        size_bytes=15,
        content_sha256="b" * 64,
        line_count=2,
    )
    from linkloom.retrieval import find_relation_candidates_with_evidence
    candidates, evidence = find_relation_candidates_with_evidence([doc_a, doc_b])
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.candidate_id == "pair_p1_0001"
    assert len(cand.evidence) > 0
    assert cand.evidence == ["ev_p1_0001", "ev_p1_0002"]
    assert len(evidence) == 2
    assert all(e.status == "verified" for e in evidence)


def test_structural_evidence_extraction_helpers() -> None:
    from linkloom.retrieval import (
        _find_shared_heading_token_evidence,
        _find_shared_tag_evidence,
        _find_wikilink_evidence,
        _note_identifiers,
    )

    doc_target = NoteDocument(
        relative_path="notes/target_note.md",
        title="Target Title",
        content="# Target Title\nSome content.",
        headings=[{"level": 1, "text": "Target Title", "line": 1}],
        tags=["learning"],
        wikilinks=[],
        size_bytes=30,
        content_sha256="target" * 10,
        line_count=2,
    )

    source_content = (
        "---\ntags:\n  - learning\n---\n"
        "# Source Header\n"
        "Link with alias: [[notes/target_note#sec1|Target Display]].\n"
        "Inline tag here: #learning/deep\n"
    )
    doc_source = NoteDocument(
        relative_path="notes/source_note.md",
        title="Source Header",
        content=source_content,
        headings=[{"level": 1, "text": "Source Header", "line": 4}],
        tags=["learning", "learning/deep"],
        wikilinks=["notes/target_note"],
        size_bytes=len(source_content.encode("utf-8")),
        content_sha256="source" * 10,
        line_count=6,
    )

    # 1. Wikilink extraction
    target_ids = _note_identifiers(doc_target)
    wl_evs = _find_wikilink_evidence(doc_source, "notes/target_note", target_ids)
    assert len(wl_evs) == 1
    assert wl_evs[0].line_start == 6
    assert "[[notes/target_note" in wl_evs[0].quote
    assert wl_evs[0].source_kind == "wikilink"
    assert wl_evs[0].status == "verified"

    # 2. Shared tag extraction (frontmatter and inline)
    tag_evs = _find_shared_tag_evidence(doc_source, "learning")
    assert len(tag_evs) >= 2
    assert any(ev.line_start == 3 and "learning" in ev.quote for ev in tag_evs)
    assert any(ev.line_start == 7 and "#learning" in ev.quote for ev in tag_evs)
    assert all(ev.source_kind == "tag" for ev in tag_evs)

    # 3. Heading token extraction
    h_evs = _find_shared_heading_token_evidence(doc_source, "header")
    assert len(h_evs) == 1
    assert h_evs[0].line_start == 5
    assert h_evs[0].source_kind == "heading"


def test_find_relation_candidates_sample_vault_evidence_support(tmp_path: Path) -> None:
    from linkloom.retrieval import find_relation_candidates_with_evidence

    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)
    documents, _, _ = load_vault(FIXTURE_ROOT, scan_result.index_path)

    candidates, evidence = find_relation_candidates_with_evidence(documents=documents, query="Scanner")
    assert len(candidates) > 0
    assert len(evidence) > 0

    ev_map = {ev.evidence_id: ev for ev in evidence}

    # Verify every candidate has structural signal support in its quotes
    for cand in candidates:
        cand_evs = [ev_map[eid] for eid in cand.evidence]
        assert len(cand_evs) > 0
        has_support = False
        for sb in cand.score_breakdown:
            kind = sb["kind"]
            val = sb["value"]
            if kind == "wikilink":
                target = val.split("->")[-1].strip() if "->" in val else val
                if any(ev.source_kind == "wikilink" and "[[" in ev.quote and (target in ev.quote or Path(target).stem in ev.quote) for ev in cand_evs):
                    has_support = True
                    break
            elif kind == "shared_tag":
                if any(ev.source_kind == "tag" and val in ev.quote for ev in cand_evs):
                    has_support = True
                    break
            elif kind == "shared_heading_token":
                if any(ev.source_kind == "heading" and val.lower() in ev.quote.lower() for ev in cand_evs):
                    has_support = True
                    break
        assert has_support, f"Candidate {cand.candidate_id} lacks supporting structural evidence"


