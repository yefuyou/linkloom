from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"
sys.path.insert(0, str(SRC_ROOT))

from linkloom.scanner import ScannerError, scan_vault  # noqa: E402


def fixture_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def read_index(output_root: Path) -> dict:
    return json.loads((output_root / "vault_index.json").read_text(encoding="utf-8"))


def records_by_path(index: dict) -> dict[str, dict]:
    return {record["relative_path"]: record for record in index["notes"]}


def test_scan_extracts_fixture_structure(tmp_path: Path) -> None:
    output_root = tmp_path / "scan"

    result = scan_vault(FIXTURE_ROOT, output_root)
    index = read_index(output_root)
    records = records_by_path(index)

    assert result.note_count == 5
    assert result.warning_count == 1
    assert [record["relative_path"] for record in index["notes"]] == sorted(records)
    assert set(records) == {
        "00-扫描入门.md",
        "projects/设计笔记.md",
        "无标题.md",
        "损坏-frontmatter.md",
        "综合结构测试.md",
    }

    intro = records["00-扫描入门.md"]
    assert intro["title"] == "扫描入门"
    assert intro["tags"] == ["agent", "scanner", "学习/评测"]
    assert intro["wikilinks"] == ["projects/设计笔记"]
    assert intro["headings"] == [{"level": 1, "text": "Scanner Overview", "line": 7}]
    assert len(intro["content_sha256"]) == 64
    assert intro["size_bytes"] == len((FIXTURE_ROOT / "00-扫描入门.md").read_bytes())

    design = records["projects/设计笔记.md"]
    assert design["title"] == "设计笔记"
    assert design["headings"] == [
        {"level": 1, "text": "设计笔记", "line": 1},
        {"level": 2, "text": "细节", "line": 3},
    ]
    assert design["tags"] == ["架构"]
    assert design["wikilinks"] == ["00-扫描入门"]

    untitled = records["无标题.md"]
    assert untitled["title"] == "无标题"
    assert untitled["headings"] == []
    assert untitled["tags"] == ["零散"]

    malformed = records["损坏-frontmatter.md"]
    assert malformed["title"] == "仍然可读"
    assert malformed["tags"] == ["损坏测试"]
    assert malformed["wikilinks"] == ["00-扫描入门"]
    assert index["warnings"][0]["code"] == "FRONTMATTER_PARSE_ERROR"
    assert index["warnings"][0]["relative_path"] == "损坏-frontmatter.md"
    assert "不会被识别" not in intro["tags"]
    assert "不会被识别" not in intro["wikilinks"]

    raw_index = (output_root / "vault_index.json").read_text(encoding="utf-8")
    assert str(FIXTURE_ROOT.resolve()) not in raw_index
    assert "schema_version" in raw_index
    assert (output_root / "scan_summary.md").exists()


def test_scan_is_deterministic_and_does_not_modify_fixture(tmp_path: Path) -> None:
    before = fixture_snapshot(FIXTURE_ROOT)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    scan_vault(FIXTURE_ROOT, first_output)
    scan_vault(FIXTURE_ROOT, second_output)

    assert fixture_snapshot(FIXTURE_ROOT) == before
    assert (first_output / "vault_index.json").read_bytes() == (
        second_output / "vault_index.json"
    ).read_bytes()
    assert (first_output / "scan_summary.md").read_bytes() == (
        second_output / "scan_summary.md"
    ).read_bytes()


def test_rejects_missing_input_and_output_inside_input(tmp_path: Path) -> None:
    with pytest.raises(ScannerError) as missing_error:
        scan_vault(tmp_path / "missing", tmp_path / "out")
    assert missing_error.value.exit_code == 2

    unsafe_output = FIXTURE_ROOT / "generated"
    with pytest.raises(ScannerError) as unsafe_error:
        scan_vault(FIXTURE_ROOT, unsafe_output)
    assert unsafe_error.value.exit_code == 2
    assert not unsafe_output.exists()


def test_invalid_utf8_is_reported_and_valid_notes_continue(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "valid.md").write_text("# Valid", encoding="utf-8")
    (vault / "invalid.md").write_bytes(b"\xff\xfe\x00")

    scan_vault(vault, tmp_path / "output")
    index = read_index(tmp_path / "output")

    assert [note["relative_path"] for note in index["notes"]] == ["valid.md"]
    assert index["warnings"][0]["code"] == "UTF8_DECODE_ERROR"
    assert index["warnings"][0]["relative_path"] == "invalid.md"


def test_file_read_error_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    unreadable = vault / "unreadable.md"
    unreadable.write_text("# Unreadable", encoding="utf-8")

    original_read_bytes = Path.read_bytes

    def fail_for_fixture(path: Path) -> bytes:
        if path == unreadable:
            raise OSError("Cannot open D:\\private\\vault\\secret.md")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_for_fixture)
    scan_vault(vault, tmp_path / "output")
    index = read_index(tmp_path / "output")

    assert index["notes"] == []
    assert index["warnings"] == [
        {
            "relative_path": "unreadable.md",
            "code": "FILE_READ_ERROR",
            "message": "Source file could not be read.",
        }
    ]


def test_symbolic_link_is_skipped_when_supported(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "real.md").write_text("# Real", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    link = vault / "linked.md"

    try:
        os.symlink(outside, link)
    except OSError as error:
        pytest.skip(f"Symbolic links are unavailable on this platform: {error}")

    scan_vault(vault, tmp_path / "output")
    index = read_index(tmp_path / "output")

    assert [note["relative_path"] for note in index["notes"]] == ["real.md"]
    assert index["warnings"][0]["code"] == "SYMLINK_SKIPPED"
    assert index["warnings"][0]["relative_path"] == "linked.md"


def test_cli_creates_artifacts_without_network(tmp_path: Path) -> None:
    output_root = tmp_path / "cli-output"
    environment = os.environ.copy()
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(SRC_ROOT) if not existing_python_path else (
        str(SRC_ROOT) + os.pathsep + existing_python_path
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "linkloom",
            "scan",
            str(FIXTURE_ROOT),
            "--output",
            str(output_root),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Indexed notes: 5" in completed.stdout
    assert "Warnings: 1" in completed.stdout
    assert (output_root / "vault_index.json").exists()
    assert (output_root / "scan_summary.md").exists()


def test_content_hash_is_source_byte_fingerprint(tmp_path: Path) -> None:
    output_root = tmp_path / "hash-output"
    scan_vault(FIXTURE_ROOT, output_root)
    records = records_by_path(read_index(output_root))

    expected = hashlib.sha256((FIXTURE_ROOT / "无标题.md").read_bytes()).hexdigest()
    assert records["无标题.md"]["content_sha256"] == expected


def test_mixed_structure_parsing(tmp_path: Path) -> None:
    output_root = tmp_path / "scan"
    scan_vault(FIXTURE_ROOT, output_root)
    index = read_index(output_root)
    records = records_by_path(index)

    mixed = records["综合结构测试.md"]
    assert mixed["relative_path"] == "综合结构测试.md"
    assert mixed["title"] == "优先标题"
    assert mixed["headings"] == [
        {"level": 1, "text": "正文一号标题", "line": 8},
        {"level": 2, "text": "子标题甲", "line": 12},
        {"level": 2, "text": "子标题乙", "line": 35},
    ]
    assert mixed["tags"] == ["agent-evaluation", "学习/评测", "知识管理/测试"]
    assert mixed["wikilinks"] == ["指标体系梳理", "普通笔记", "项目复盘"]
    expected_bytes = (FIXTURE_ROOT / "综合结构测试.md").read_bytes()
    assert mixed["size_bytes"] == len(expected_bytes)
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    assert mixed["content_sha256"] == expected_hash
