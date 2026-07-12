"""Read-only Markdown vault scanning with deterministic artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
INLINE_TAG_PATTERN = re.compile(r"(?<![\w/])#([\w\-/]+)")


class ScannerError(Exception):
    """A user-correctable scan error with a CLI exit code."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ScanResult:
    note_count: int
    warning_count: int
    index_path: Path
    summary_path: Path


def scan_vault(input_root: Path | str, output_root: Path | str) -> ScanResult:
    """Scan Markdown notes without mutating the input root."""

    raw_input_root = Path(input_root)
    if raw_input_root.is_symlink():
        raise ScannerError("Input root must not be a symbolic link.")
    if not raw_input_root.exists() or not raw_input_root.is_dir():
        raise ScannerError("Input root must be an existing directory.")

    root = raw_input_root.resolve()
    output = Path(output_root).resolve()
    if _is_inside(output, root):
        raise ScannerError("Output directory must be outside the input root.")

    notes: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for current_path, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_path)
        _remove_symlink_directories(current, directory_names, root, warnings)

        for file_name in sorted(file_names, key=str.casefold):
            path = current / file_name
            relative_path = _relative_path(path, root)

            if path.is_symlink():
                _add_warning(
                    warnings,
                    relative_path,
                    "SYMLINK_SKIPPED",
                    "Symbolic-link file was skipped.",
                )
                continue
            if path.suffix.lower() != ".md":
                continue

            record = _scan_file(path, root, warnings)
            if record is not None:
                notes.append(record)

    notes.sort(key=lambda note: note["relative_path"])
    warnings.sort(key=lambda warning: (
        warning["relative_path"],
        warning["code"],
        warning.get("line", 0),
    ))

    index = {
        "schema_version": 1,
        "note_count": len(notes),
        "notes": notes,
        "warnings": warnings,
    }

    output.mkdir(parents=True, exist_ok=True)
    index_path = output / "vault_index.json"
    summary_path = output / "scan_summary.md"
    _write_text(index_path, json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_text(summary_path, _build_summary(index))

    return ScanResult(
        note_count=len(notes),
        warning_count=len(warnings),
        index_path=index_path,
        summary_path=summary_path,
    )


def _scan_file(
    path: Path, root: Path, warnings: list[dict[str, Any]]
) -> dict[str, Any] | None:
    relative_path = _relative_path(path, root)
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        _add_warning(
            warnings,
            relative_path,
            "FILE_READ_ERROR",
            "Source file could not be read.",
        )
        return None

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        _add_warning(warnings, relative_path, "UTF8_DECODE_ERROR", str(error))
        return None

    frontmatter, body_start = _parse_frontmatter(text, relative_path, warnings)
    headings, inline_tags, wikilinks = _parse_body(text, body_start)

    title = _frontmatter_title(frontmatter)
    if not title:
        title = next((heading["text"] for heading in headings if heading["level"] == 1), "")
    if not title:
        title = path.stem

    tags = sorted(
        set(
            tag for tag in _frontmatter_tags(frontmatter) + inline_tags
            if _is_valid_tag(tag)
        )
    )
    return {
        "relative_path": relative_path,
        "title": title,
        "headings": headings,
        "tags": tags,
        "wikilinks": sorted(set(wikilinks)),
        "size_bytes": len(raw_bytes),
        "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def _parse_frontmatter(
    text: str, relative_path: str, warnings: list[dict[str, Any]]
) -> tuple[dict[str, Any], int]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, 0

    closing_index = next((index for index, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if closing_index is None:
        _add_warning(
            warnings,
            relative_path,
            "FRONTMATTER_PARSE_ERROR",
            "Frontmatter opening marker has no closing marker.",
            line=1,
        )
        return {}, 0

    try:
        parsed = yaml.safe_load("\n".join(lines[1:closing_index])) or {}
    except yaml.YAMLError as error:
        _add_warning(
            warnings,
            relative_path,
            "FRONTMATTER_PARSE_ERROR",
            str(error),
            line=1,
        )
        return {}, closing_index + 1

    if not isinstance(parsed, dict):
        _add_warning(
            warnings,
            relative_path,
            "FRONTMATTER_PARSE_ERROR",
            "Frontmatter must be a mapping.",
            line=1,
        )
        return {}, closing_index + 1
    return parsed, closing_index + 1


def _parse_body(text: str, body_start: int) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    headings: list[dict[str, Any]] = []
    tags: list[str] = []
    wikilinks: list[str] = []
    in_fenced_code = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        if line_number <= body_start:
            continue

        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fenced_code = not in_fenced_code
            continue
        if in_fenced_code:
            continue

        heading_match = HEADING_PATTERN.match(line)
        if heading_match:
            headings.append(
                {
                    "level": len(heading_match.group(1)),
                    "text": heading_match.group(2).strip(),
                    "line": line_number,
                }
            )

        tags.extend(match.group(1) for match in INLINE_TAG_PATTERN.finditer(line))
        for match in WIKILINK_PATTERN.finditer(line):
            target = match.group(1).split("|", maxsplit=1)[0].split("#", maxsplit=1)[0].strip()
            if target:
                wikilinks.append(target)

    return headings, tags, wikilinks


def _frontmatter_title(frontmatter: dict[str, Any]) -> str:
    value = frontmatter.get("title")
    return value.strip() if isinstance(value, str) else ""


def _frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    value = frontmatter.get("tags")
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _is_valid_tag(tag: str) -> bool:
    if tag.isdigit():
        return False
    if len(tag) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in tag):
        return False
    return True


def _remove_symlink_directories(
    current: Path, directory_names: list[str], root: Path, warnings: list[dict[str, Any]]
) -> None:
    for directory_name in list(directory_names):
        candidate = current / directory_name
        if candidate.is_symlink():
            directory_names.remove(directory_name)
            _add_warning(
                warnings,
                _relative_path(candidate, root),
                "SYMLINK_SKIPPED",
                "Symbolic-link directory was skipped.",
            )


def _add_warning(
    warnings: list[dict[str, Any]],
    relative_path: str,
    code: str,
    message: str,
    line: int | None = None,
) -> None:
    warning: dict[str, Any] = {
        "relative_path": relative_path,
        "code": code,
        "message": message,
    }
    if line is not None:
        warning["line"] = line
    warnings.append(warning)


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _write_text(path: Path, content: str) -> None:
    temporary_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    )
    try:
        with temporary_file:
            temporary_file.write(content)
        os.replace(temporary_file.name, path)
    except Exception:
        Path(temporary_file.name).unlink(missing_ok=True)
        raise


def _build_summary(index: dict[str, Any]) -> str:
    lines = [
        "# Scan Summary",
        "",
        f"- Notes indexed: {index['note_count']}",
        f"- Warnings: {len(index['warnings'])}",
        "",
        "## Notes",
        "",
    ]
    lines.extend(f"- `{note['relative_path']}` - {note['title']}" for note in index["notes"])

    lines.extend(["", "## Warnings", ""])
    if index["warnings"]:
        for warning in index["warnings"]:
            location = f":{warning['line']}" if "line" in warning else ""
            lines.append(
                f"- `{warning['relative_path']}{location}` [{warning['code']}] {warning['message']}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"
