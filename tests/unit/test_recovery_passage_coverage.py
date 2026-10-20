from __future__ import annotations

import hashlib
import json
from pathlib import Path

from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
from linkloom.schemas import NoteDocument


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "real_agent_reliability"
    / "public_frozen.json"
)


def _note(path: str, content: str) -> NoteDocument:
    return NoteDocument(
        relative_path=path,
        title=path.rsplit("/", 1)[-1].removesuffix(".md"),
        content=content,
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=len(content.encode("utf-8")),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        line_count=len(content.splitlines()),
    )


def _fixture_case(case_id: str) -> dict:
    public = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return next(case for case in public["cases"] if case["id"] == case_id)


def _fixture_documents(case_id: str) -> list[NoteDocument]:
    case = _fixture_case(case_id)
    workspace_id = case["workspace_id"]
    return [
        _note(document["path"], document["content"])
        for document in case["documents"]
        if document.get("workspace_id") == workspace_id
        and document.get("valid", True) is not False
    ]


def test_bm25_preserves_multiple_relation_passages_from_one_note() -> None:
    document = next(
        document
        for document in _fixture_documents("RAR-F-O")
        if document.relative_path == "o-hostile.md"
    )
    backend = RuntimeRetrievalBackend(
        workspace_id="frozen-workspace",
        document_provider=lambda: [document],
        mode="bm25",
    )

    result = backend.search("Who is the direct leader of Sana Wu?", top_k=8)

    passages = [
        item for item in result.evidence if item["relative_path"] == "o-hostile.md"
    ]
    quotes = {item["quote"] for item in passages}
    relation_quotes = {
        "Sana Wu is the accountable owner of Hostile Delta.",
        "Sana Wu reports directly to Theo Gray.",
    }
    assert relation_quotes <= quotes
    relation_passages = [item for item in passages if item["quote"] in relation_quotes]
    assert len({item["evidence_id"] for item in relation_passages}) == 2
    for item in passages:
        assert item["workspace_id"] == "frozen-workspace"
        assert item["source_ref"] == "o-hostile.md"
        assert item["index_version"] == 2
        assert item["content_sha256"] == document.content_sha256
        assert item["line_start"] == item["line_end"]
        assert item["quote_sha256"] == hashlib.sha256(
            item["quote"].encode("utf-8")
        ).hexdigest()


def test_same_file_noise_stays_bounded_by_top_k_and_keeps_relation_hits() -> None:
    documents = _fixture_documents("RAR-F-O") + _fixture_documents("RAR-F-M")
    backend = RuntimeRetrievalBackend(
        workspace_id="frozen-workspace",
        document_provider=lambda: documents,
        mode="bm25",
    )

    result = backend.search("Who is the direct leader of Sana Wu?", top_k=2)
    quotes = {item["quote"] for item in result.evidence}

    assert len(result.evidence) <= 2
    assert {
        "Sana Wu is the accountable owner of Hostile Delta.",
        "Sana Wu reports directly to Theo Gray.",
    } <= quotes
    assert len(backend.search("Who is the direct leader of Sana Wu?", top_k=1).evidence) <= 1


def test_frozen_foreign_workspace_source_is_filtered_before_retrieval() -> None:
    case = _fixture_case("RAR-F-N")
    documents = _fixture_documents("RAR-F-N")

    assert case["workspace_id"] == "frozen-workspace"
    assert [document.relative_path for document in documents] == ["n-owner.md"]
    assert any(
        document["workspace_id"] == "other-workspace"
        for document in case["documents"]
    )


def test_content_update_invalidates_old_passage_identity() -> None:
    documents = _fixture_documents("RAR-F-O")
    backend = RuntimeRetrievalBackend(
        workspace_id="frozen-workspace",
        document_provider=lambda: documents,
        mode="bm25",
    )

    before = backend.search("Who is the direct leader of Sana Wu?", top_k=8)
    old_ids = {item["evidence_id"] for item in before.evidence}
    old_hashes = {item["content_sha256"] for item in before.evidence}
    original = documents[0]
    documents[0] = _note(
        original.relative_path,
        original.content + "\nA later source version adds a review marker.",
    )

    after = backend.search("Who is the direct leader of Sana Wu?", top_k=8)
    new_ids = {item["evidence_id"] for item in after.evidence}
    new_hashes = {item["content_sha256"] for item in after.evidence}

    assert old_ids.isdisjoint(new_ids)
    assert old_hashes.isdisjoint(new_hashes)
