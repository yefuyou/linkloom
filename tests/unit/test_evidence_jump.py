"""Exact current evidence identities, without text-search fallbacks."""
from copy import deepcopy

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.ui.product import ProductApplicationError, _evidence_jump_projection


TEXT = ("[2026-10-01T09:00:00Z] 甲: 😀先讨论\r\n"
        "[2026-10-01T10:00:00Z] 乙: 同样的话 <script>x</script>\r\n"
        "[2026-10-01T11:00:00Z] 乙: 同样的话 <script>x</script>")


def fixture(content=TEXT):
    artifact = RawArtifact("ws_test", "src_test", "timestamped_text", content,
                           source_uri="linkloom://ws_test/src_test")
    spans = [segment.evidence_span for segment in parse_source_text(artifact).segments]
    rows = [dict(workspace_id=artifact.workspace_id, source_id=artifact.artifact_id,
        artifact_version_id=artifact.artifact_version_id, evidence_ref=span.evidence_ref,
        content_sha256=artifact.content_hash, quote_sha256=span.quote_sha256,
        line_start=span.line_start, line_end=span.line_end, quote=span.quote) for span in spans]
    source = dict(workspace_id=artifact.workspace_id, source_id=artifact.artifact_id,
        source_version=artifact.artifact_version_id, content_sha256=artifact.content_hash,
        source_uri=artifact.source_uri, active=True, content=content)
    registry = SourceReferenceRegistry()
    for span in spans:
        registry.register_evidence(artifact.workspace_id, span.evidence_ref,
            content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    return source, rows, registry


def test_identical_quotes_keep_distinct_existing_ids_and_unicode_codepoint_ranges():
    source, rows, registry = fixture()
    view = _evidence_jump_projection(source, rows, registry)
    assert view["validated"] is True
    semantic = [span for span in view["spans"] if span["verification_kind"] == "SEMANTIC_SPAN"]
    assert [span["evidence_ref"] for span in semantic] == [row["evidence_ref"] for row in rows]
    first, second, third = semantic
    assert second["quote"] == third["quote"]
    assert second["evidence_ref"] != third["evidence_ref"]
    assert second["char_start"] < third["char_start"]
    assert [second["line_start"], third["line_start"]] == [2, 3]
    for span in view["spans"]:
        assert TEXT[span["char_start"]:span["char_end"]] == span["quote"]
    assert first["quote"].startswith("😀")


@pytest.mark.parametrize("mutation", ["version", "hash", "content", "inactive", "missing",
    "duplicate", "catalog_quote", "catalog_position", "catalog_workspace", "unknown_ref",
    "registry_revoked", "registry_hash", "registry_version"])
def test_invalid_binding_never_yields_original_content(mutation):
    source, rows, registry = fixture()
    if mutation == "version": source["source_version"] = "old-version"
    elif mutation == "hash": source["content_sha256"] = "0" * 64
    elif mutation == "content": source["content"] += "tampered"
    elif mutation == "inactive": source["active"] = False
    elif mutation == "missing": rows.pop()
    elif mutation == "duplicate": rows.append(deepcopy(rows[0]))
    elif mutation == "catalog_quote": rows[0]["quote"] = "tampered"
    elif mutation == "catalog_position": rows[0]["line_start"] = 99
    elif mutation == "catalog_workspace": rows[0]["workspace_id"] = "other"
    elif mutation == "unknown_ref": rows[0]["evidence_ref"] = "unknown"
    elif mutation == "registry_revoked": registry.unregister_evidence(source["workspace_id"], rows[0]["evidence_ref"])
    else:
        registry.register_evidence(source["workspace_id"], rows[0]["evidence_ref"],
            content_hash="0" * 64 if mutation == "registry_hash" else source["content_sha256"],
            source_version="old" if mutation == "registry_version" else source["source_version"])
    with pytest.raises(ProductApplicationError) as failure:
        _evidence_jump_projection(source, rows, registry)
    assert failure.value.code == "SOURCE_EVIDENCE_UNAVAILABLE"
    assert failure.value.status == 409
    assert "同样的话" not in str(failure.value)


def test_ordinary_document_uses_its_existing_span_and_id():
    source, rows, registry = fixture("# 文档 😀\n\n原话 <b>必须转义</b>\n")
    view = _evidence_jump_projection(source, rows, registry)
    semantic = [span for span in view["spans"] if span["verification_kind"] == "SEMANTIC_SPAN"]
    assert len(semantic) == 1
    assert semantic[0]["evidence_ref"] == rows[0]["evidence_ref"]
    assert semantic[0]["char_start"] == 0
    assert semantic[0]["char_end"] == len(source["content"])


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_runtime_ids_match_existing_retrieval_with_exact_platform_newline_mapping(monkeypatch, tmp_path, newline):
    import hashlib
    import os
    from linkloom.evidence_identity import passage_evidence_id

    source, rows, registry = fixture()
    monkeypatch.setattr(os, "linesep", newline)
    view = _evidence_jump_projection(source, rows, registry)
    runtime = [span for span in view["spans"] if span["verification_kind"] == "RUNTIME_PASSAGE"]
    written = tmp_path / "src_test.md"
    written.write_text(TEXT, encoding="utf-8", newline=newline)
    physical = written.read_bytes().decode("utf-8")
    assert len(runtime) == 3
    expected = []
    for index, raw in enumerate(physical.splitlines(keepends=True), 1):
        quote = raw.splitlines()[0]
        if quote:
            expected.append(passage_evidence_id(workspace_id=source["workspace_id"],
                resource_id="src_test.md", document_id="src_test.md", logical_path="/src_test.md",
                source_ref="src_test.md", content_hash=hashlib.sha256(physical.encode("utf-8")).hexdigest(),
                line_start=index, line_end=index, quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest()))
    assert [span["evidence_ref"] for span in runtime] == expected
    assert len(set(expected)) == 3
    for span in runtime:
        assert TEXT[span["char_start"]:span["char_end"]] == span["quote"]
    assert runtime[1]["quote"].endswith("同样的话 <script>x</script>")
    assert runtime[2]["quote"].endswith("同样的话 <script>x</script>")
    assert runtime[1]["evidence_ref"] != runtime[2]["evidence_ref"]
    assert [span["line_start"] for span in runtime] == [1, 2, 3]
