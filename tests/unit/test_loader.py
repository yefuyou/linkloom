from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"
sys.path.insert(0, str(SRC_ROOT))

from linkloom.loader import LoaderError, VaultReader, load_vault  # noqa: E402
from linkloom.scanner import scan_vault  # noqa: E402


def fixture_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_loader_reads_sample_vault_accurately(tmp_path: Path) -> None:
    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)

    before_snapshot = fixture_snapshot(FIXTURE_ROOT)
    documents, source_context, warnings = load_vault(FIXTURE_ROOT, scan_result.index_path)
    after_snapshot = fixture_snapshot(FIXTURE_ROOT)

    # Immutability check
    assert before_snapshot == after_snapshot

    # All valid notes loaded (excluding unparseable/error ones if any, 5 notes in sample vault)
    assert len(documents) == 5
    doc_paths = {doc.relative_path for doc in documents}
    assert doc_paths == {
        "00-扫描入门.md",
        "projects/设计笔记.md",
        "无标题.md",
        "损坏-frontmatter.md",
        "综合结构测试.md",
    }

    # Verify SHA-256 preservation
    for doc in documents:
        expected_bytes = (FIXTURE_ROOT / doc.relative_path).read_bytes()
        expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
        assert doc.content_sha256 == expected_sha256
        assert doc.size_bytes == len(expected_bytes)
        assert doc.line_count == len(doc.content.splitlines())

    # Verify no absolute path leakage
    for doc in documents:
        assert str(FIXTURE_ROOT.resolve()) not in doc.relative_path
        assert ":" not in doc.relative_path  # No Windows drive letter

    assert source_context.index_schema_version == 1
    assert len(source_context.index_sha256) == 64
    assert source_context.vault_root_fingerprint.startswith("root_")


def test_loader_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    bad_index = {
        "schema_version": 2,
        "note_count": 0,
        "notes": [],
        "warnings": [],
    }
    index_path = tmp_path / "vault_index.json"
    index_path.write_text(json.dumps(bad_index), encoding="utf-8")

    with pytest.raises(LoaderError) as exc_info:
        load_vault(FIXTURE_ROOT, index_path)
    assert exc_info.value.code == "INDEX_SCHEMA_UNSUPPORTED"


def test_loader_rejects_missing_source_and_index(tmp_path: Path) -> None:
    # Missing vault
    with pytest.raises(LoaderError) as exc_info1:
        VaultReader(tmp_path / "missing_vault", tmp_path / "index.json")
    assert exc_info1.value.code == "SOURCE_NOT_FOUND"

    # Missing index
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(LoaderError) as exc_info2:
        VaultReader(vault, tmp_path / "missing_index.json")
    assert exc_info2.value.code == "SOURCE_NOT_FOUND"


def test_loader_rejects_duplicate_paths_in_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note", encoding="utf-8")
    note_sha256 = hashlib.sha256((vault / "note.md").read_bytes()).hexdigest()

    bad_index = {
        "schema_version": 1,
        "note_count": 2,
        "notes": [
            {"relative_path": "note.md", "content_sha256": note_sha256},
            {"relative_path": "note.md", "content_sha256": note_sha256},
        ],
        "warnings": [],
    }
    index_path = tmp_path / "vault_index.json"
    index_path.write_text(json.dumps(bad_index), encoding="utf-8")

    with pytest.raises(LoaderError) as exc_info:
        VaultReader(vault, index_path)
    assert exc_info.value.code == "DUPLICATE_NOTE_PATH"


def test_loader_rejects_path_escaping_root(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    outside_sha256 = hashlib.sha256(outside.read_bytes()).hexdigest()

    bad_index = {
        "schema_version": 1,
        "note_count": 1,
        "notes": [
            {"relative_path": "../outside.md", "content_sha256": outside_sha256},
        ],
        "warnings": [],
    }
    index_path = tmp_path / "vault_index.json"
    index_path.write_text(json.dumps(bad_index), encoding="utf-8")

    reader = VaultReader(vault, index_path)
    with pytest.raises(LoaderError) as exc_info:
        reader.read_notes()
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_loader_detects_content_change(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note_file = vault / "test.md"
    note_file.write_text("# Original", encoding="utf-8")

    scan_output = tmp_path / "scan"
    scan_result = scan_vault(vault, scan_output)

    # Mutate note file
    note_file.write_text("# Modified Content", encoding="utf-8")

    reader = VaultReader(vault, scan_result.index_path)
    with pytest.raises(LoaderError) as exc_info:
        reader.read_notes()
    assert exc_info.value.code == "CONTENT_CHANGED"


def test_loader_handles_utf8_decode_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    valid = vault / "valid.md"
    valid.write_text("# Valid", encoding="utf-8")
    invalid = vault / "invalid.md"
    invalid.write_bytes(b"\xff\xfe\x00")

    # Manually craft index containing both
    index = {
        "schema_version": 1,
        "note_count": 2,
        "notes": [
            {
                "relative_path": "invalid.md",
                "content_sha256": hashlib.sha256(invalid.read_bytes()).hexdigest(),
            },
            {
                "relative_path": "valid.md",
                "content_sha256": hashlib.sha256(valid.read_bytes()).hexdigest(),
            },
        ],
        "warnings": [],
    }
    index_path = tmp_path / "vault_index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    reader = VaultReader(vault, index_path)
    docs = reader.read_notes()

    # Valid doc is loaded, invalid doc skipped with warning
    assert len(docs) == 1
    assert docs[0].relative_path == "valid.md"
    assert any(w["code"] == "UTF8_DECODE_ERROR" for w in reader.warnings)


def test_loader_rejects_symlink_note(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    target = tmp_path / "target.md"
    target.write_text("# Target", encoding="utf-8")
    link = vault / "link.md"

    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("Symlinks not supported on this platform")

    index = {
        "schema_version": 1,
        "note_count": 1,
        "notes": [
            {
                "relative_path": "link.md",
                "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        ],
        "warnings": [],
    }
    index_path = tmp_path / "vault_index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    reader = VaultReader(vault, index_path)
    with pytest.raises(LoaderError) as exc_info:
        reader.read_notes()
    assert exc_info.value.code == "SYMLINK_REJECTED"
