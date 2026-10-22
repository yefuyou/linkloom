from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from linkloom.semantic_ingestion.ami_nxt import adapt_meeting, chunk_text


NITE = 'xmlns:nite="http://nite.sourceforge.net/"'


def _zip(tmp_path: Path, members: dict[str, str | bytes]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "ami-fixture.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return path


def _fixture(tmp_path: Path, *, bad_href: str | None = None) -> Path:
    href = bad_href or "ES2002a.A.words.xml#id(w10)..id(w2)"
    metadata = f'''<?xml version="1.0" encoding="UTF-8"?>
<nite:root nite:id="root" {NITE}>
  <meeting nite:id="meeting-node" observation="ES2002a" dateOnly="2020-01-02" startTime="09h00" custom="保留">
    <speaker nite:id="spk-A" nxt_agent="A"/>
  </meeting>
</nite:root>'''
    words = f'''<?xml version="1.0" encoding="UTF-8"?>
<nite:root nite:id="words-root" {NITE}>
  <w nite:id="w10" starttime="2.0" endtime="2.5">你好</w>
  <vocalsound nite:id="n1" starttime="2.5" endtime="2.7" type="laugh"/>
  <w nite:id="w2" starttime="2.7" endtime="3.0">café</w>
  <container nite:id="container-id"><w nite:id="nested-not-word">容器</w></container>
  <w nite:id="w30">未覆盖</w>
  <w nite:id="w-start-only" starttime="4.0" custom="start-only">开始</w>
  <w nite:id="w-end-only" endtime="5.0" custom="end-only">结束</w>
</nite:root>'''
    da = f'''<?xml version="1.0" encoding="UTF-8"?>
<nite:root nite:id="da-root" {NITE}>
  <dact nite:id="da-late">
    <nite:child href="{href}"/>
  </dact>
  <dact nite:id="da-empty"/>
</nite:root>'''
    return _zip(tmp_path, {
        "corpusResources/meetings.xml": metadata,
        "words/ES2002a.A.words.xml": words,
        "dialogueActs/ES2002a.A.dialog-act.xml": da,
        "abstractive/ES2002a.abssumm.xml": b"GOLD MUST NOT BE READ",
    })


def test_adapt_meeting_preserves_unicode_ranges_metadata_and_unsegmented_words(tmp_path: Path):
    archive = _fixture(tmp_path)
    result = adapt_meeting(archive, "ES2002a")

    assert result["meeting_id"] == "ES2002a"
    assert result["metadata"]["custom"] == "保留"
    assert result["metadata"]["timezone"] == "UNKNOWN"
    assert result["metadata"]["event_time"] is None
    assert {row["native_word_id"] for row in result["words"]} == {
        "w10", "w2", "w30", "w-start-only", "w-end-only", "nested-not-word"
    }
    assert result["words"][0]["text"] == "你好"
    assert result["nontext_events"][0]["native_nontext_id"] == "n1"
    assert result["utterances"][0]["utterance_id"] == "da-late"
    assert result["utterances"][0]["native_word_ids"] == ["w10", "w2"]
    assert result["utterances"][0]["native_nontext_ids"] == ["n1"]
    assert any(u.get("is_unsegmented") for u in result["utterances"])


def test_word_attributes_and_one_sided_times_are_preserved(tmp_path: Path):
    result = adapt_meeting(_fixture(tmp_path), "ES2002a")
    words = {row["native_word_id"]: row for row in result["words"]}

    start_only = words["w-start-only"]
    assert start_only["audio_start_seconds"] == "4.0"
    assert start_only["audio_end_seconds"] is None
    assert start_only["attributes"]["custom"] == "start-only"
    assert start_only["attributes"]["starttime"] == "4.0"

    end_only = words["w-end-only"]
    assert end_only["audio_start_seconds"] is None
    assert end_only["audio_end_seconds"] == "5.0"
    assert end_only["attributes"]["custom"] == "end-only"
    assert end_only["attributes"]["endtime"] == "5.0"

    by_id = {row["utterance_id"]: row for row in result["utterances"]}
    assert by_id["unsegmented:w-start-only"]["start_seconds"] == "4.0"
    assert by_id["unsegmented:w-start-only"]["end_seconds"] is None
    assert by_id["unsegmented:w-end-only"]["start_seconds"] is None
    assert by_id["unsegmented:w-end-only"]["end_seconds"] == "5.0"
    assert list(by_id).index("unsegmented:w-start-only") < list(by_id).index("unsegmented:w-end-only")
    assert list(by_id).index("unsegmented:w-end-only") < list(by_id).index("unsegmented:w30")


def test_da_ranges_follow_document_order_and_time_sort_has_missing_last(tmp_path: Path):
    archive = _fixture(tmp_path)
    result = adapt_meeting(archive, "ES2002a")
    ids = [u["utterance_id"] for u in result["utterances"]]
    assert ids.index("da-late") < ids.index("da-empty")
    assert result["utterances"][0]["text"] == "你好 café"
    assert result["utterances"][0]["word_spans"][1]["text"] == "café"


def test_chunk_text_is_unicode_safe_contiguous_and_hashes_bytes():
    text = "甲乙😀丙丁"
    chunks = chunk_text(text, max_chars=2, max_bytes=6, boundaries=[2, 4, len(text)])
    assert "".join(chunk["text"] for chunk in chunks) == text
    assert [(chunk["start"], chunk["end"]) for chunk in chunks] == [(0, 2), (2, 3), (3, 5)]
    for chunk in chunks:
        assert chunk["sha256"] == hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()
        assert chunk["start"] <= chunk["end"]


@pytest.mark.parametrize("max_bytes", [1, 3])
def test_chunk_text_rejects_budget_smaller_than_a_unicode_codepoint(max_bytes: int):
    with pytest.raises(ValueError, match="max_bytes"):
        chunk_text("😀", max_chars=1, max_bytes=max_bytes)


def test_chunk_text_rejects_boolean_budgets_and_boundaries():
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=True)
    with pytest.raises(ValueError):
        chunk_text("x", max_bytes=False)
    with pytest.raises(ValueError):
        chunk_text("x", boundaries=[True])


def test_canonical_word_spans_and_chunk_spans_round_trip(tmp_path: Path):
    result = adapt_meeting(_fixture(tmp_path), "ES2002a")
    canonical = result["canonical_text"]
    assert result["canonical_sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    for utterance in result["utterances"]:
        assert canonical[utterance["char_start"]:utterance["char_end"]] == utterance["text"]
        for span in utterance["word_spans"]:
            assert canonical[span["start"]:span["end"]] == span["text"]
            assert span["hash"] == hashlib.sha256(span["text"].encode("utf-8")).hexdigest()
            assert span["sourcefilehash"]
    assert "".join(chunk["text"] for chunk in result["chunks"]) == canonical


@pytest.mark.parametrize("payload, message", [
    ("<!DOCTYPE root [<!ENTITY x 'bad'>]><root/>", "entity"),
])
def test_rejects_dtd_and_entity_payloads(tmp_path: Path, payload: str, message: str):
    archive = _zip(tmp_path, {
        "corpusResources/meetings.xml": payload,
        "words/ES2002a.A.words.xml": "<root/>",
    })
    with pytest.raises(ValueError, match=message):
        adapt_meeting(archive, "ES2002a")


def test_rejects_bad_reference_and_archive_hash(tmp_path: Path):
    archive = _fixture(tmp_path, bad_href="ES2002a.A.words.xml#id(does-not-exist)")
    with pytest.raises(ValueError, match="reference|unknown|id"):
        adapt_meeting(archive, "ES2002a")
    with pytest.raises(ValueError, match="archive hash"):
        adapt_meeting(archive, "ES2002a", expected_sha256="0" * 64)


def test_gold_members_are_not_read_and_output_is_reproducible(tmp_path: Path):
    archive = _fixture(tmp_path)
    first = adapt_meeting(archive, "ES2002a")
    second = adapt_meeting(archive, "ES2002a")
    assert first == second
    assert all("abstractive" not in member for member in first["members"])
    assert first["format_version"] == "ami-nxt/v1"
    gold_ref = _fixture(tmp_path / "gold-ref", bad_href="Gold/ES2002a.A.words.xml#id(w10)")
    with pytest.raises(ValueError, match="whitelisted"):
        adapt_meeting(gold_ref, "ES2002a")


def test_rejects_cross_speaker_word_references(tmp_path: Path):
    metadata = f'<nite:root nite:id="r" {NITE}><meeting nite:id="m" observation="ES2002a"/></nite:root>'
    words_a = f'<nite:root nite:id="ra" {NITE}><w nite:id="a1" starttime="1">A</w></nite:root>'
    words_b = f'<nite:root nite:id="rb" {NITE}><w nite:id="b1" starttime="1">B</w></nite:root>'
    dact = f'<nite:root nite:id="rd" {NITE}><dact nite:id="da"><nite:child href="ES2002a.B.words.xml#id(b1)"/></dact></nite:root>'
    archive = _zip(tmp_path, {
        "corpusResources/meetings.xml": metadata,
        "words/ES2002a.A.words.xml": words_a,
        "words/ES2002a.B.words.xml": words_b,
        "dialogueActs/ES2002a.A.dialog-act.xml": dact,
    })
    with pytest.raises(ValueError, match="cross-speaker"):
        adapt_meeting(archive, "ES2002a")


def test_rejects_duplicate_nxt_ids_in_a_source_member(tmp_path: Path):
    metadata = f'<nite:root nite:id="r" {NITE}><meeting nite:id="m" observation="ES2002a"/></nite:root>'
    words = f'<nite:root nite:id="dup" {NITE}><w nite:id="dup">word</w></nite:root>'
    archive = _zip(tmp_path, {
        "corpusResources/meetings.xml": metadata,
        "words/ES2002a.A.words.xml": words,
    })
    with pytest.raises(ValueError, match="duplicate"):
        adapt_meeting(archive, "ES2002a")
