"""Safe, read-only loading of Markdown notes verified against Scanner v1 index."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from linkloom.schemas import NoteDocument, SourceContext


class LoaderError(Exception):
    """Errors during vault or index loading with structured code and exit code."""

    def __init__(self, message: str, code: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def calculate_fingerprint(path: Path) -> str:
    """Generate a stable fingerprint for a path without exposing raw private absolute paths."""
    resolved_bytes = str(path.resolve()).encode("utf-8")
    return f"root_{hashlib.sha256(resolved_bytes).hexdigest()[:16]}"


def _is_inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


class VaultReader:
    """Read-only reader that validates note contents against a Scanner v1 index."""

    def __init__(
        self,
        vault_root: Path | str,
        index_path: Path | str | None = None,
        index_data: dict[str, Any] | None = None,
    ) -> None:
        raw_root = Path(vault_root)
        if raw_root.is_symlink():
            raise LoaderError("Input root must not be a symbolic link.", code="SYMLINK_REJECTED")
        if not raw_root.exists() or not raw_root.is_dir():
            raise LoaderError("Input root must be an existing directory.", code="SOURCE_NOT_FOUND")

        self.root = raw_root.resolve()
        self.vault_root_fingerprint = calculate_fingerprint(self.root)
        self.warnings: list[dict[str, Any]] = []

        if index_data is not None:
            self.index = index_data
            self.index_sha256 = hashlib.sha256(
                json.dumps(index_data, sort_keys=True).encode("utf-8")
            ).hexdigest()
        elif index_path is not None:
            raw_index_path = Path(index_path)
            if not raw_index_path.exists() or not raw_index_path.is_file():
                raise LoaderError(
                    f"Index file not found: {raw_index_path}", code="SOURCE_NOT_FOUND"
                )
            try:
                raw_bytes = raw_index_path.read_bytes()
                self.index_sha256 = hashlib.sha256(raw_bytes).hexdigest()
                self.index = json.loads(raw_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
                raise LoaderError(
                    f"Could not read index file: {err}", code="SOURCE_NOT_FOUND"
                ) from err
        else:
            raise LoaderError("Either index_path or index_data must be provided.", code="SOURCE_NOT_FOUND")

        schema_version = self.index.get("schema_version")
        if schema_version != 1:
            raise LoaderError(
                f"Unsupported index schema version: {schema_version}. Expected 1.",
                code="INDEX_SCHEMA_UNSUPPORTED",
            )

        notes = self.index.get("notes", [])
        seen_paths: set[str] = set()
        for note in notes:
            rel_path = note.get("relative_path", "")
            if rel_path in seen_paths:
                raise LoaderError(
                    f"Duplicate note path in index: {rel_path}", code="DUPLICATE_NOTE_PATH"
                )
            seen_paths.add(rel_path)

        for warning in self.index.get("warnings", []):
            self.warnings.append(dict(warning))

    def get_source_context(self) -> SourceContext:
        return SourceContext(
            index_sha256=self.index_sha256,
            index_schema_version=1,
            vault_root_fingerprint=self.vault_root_fingerprint,
        )

    def read_notes(self) -> list[NoteDocument]:
        """Read and verify all notes referenced in the index."""
        documents: list[NoteDocument] = []

        for note_record in self.index.get("notes", []):
            rel_path_str = note_record.get("relative_path", "")
            if not rel_path_str:
                raise LoaderError("Empty relative_path in index record.", code="PATH_OUTSIDE_ROOT")

            # Path traversal / validity check
            if ".." in rel_path_str.replace("\\", "/").split("/") or rel_path_str.startswith("/") or rel_path_str.startswith("\\") or (len(rel_path_str) > 1 and rel_path_str[1] == ":"):
                raise LoaderError(
                    f"Relative path escapes root: {rel_path_str}", code="PATH_OUTSIDE_ROOT"
                )

            note_path = (self.root / rel_path_str).resolve()
            if not _is_inside(note_path, self.root):
                raise LoaderError(
                    f"Resolved note path is outside vault root: {rel_path_str}",
                    code="PATH_OUTSIDE_ROOT",
                )

            if note_path.is_symlink():
                raise LoaderError(
                    f"Symbolic-link note rejected: {rel_path_str}", code="SYMLINK_REJECTED"
                )

            if not note_path.exists() or not note_path.is_file():
                raise LoaderError(
                    f"Source note file not found: {rel_path_str}", code="SOURCE_NOT_FOUND"
                )

            try:
                raw_bytes = note_path.read_bytes()
            except OSError as err:
                raise LoaderError(
                    f"Failed to read note file: {rel_path_str}", code="FILE_READ_ERROR"
                ) from err

            # Check content SHA-256
            current_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            expected_sha256 = note_record.get("content_sha256", "")
            if current_sha256 != expected_sha256:
                raise LoaderError(
                    f"Content hash changed for note: {rel_path_str} (expected {expected_sha256}, got {current_sha256})",
                    code="CONTENT_CHANGED",
                )

            # Decode UTF-8
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as err:
                self.warnings.append(
                    {
                        "relative_path": rel_path_str,
                        "code": "UTF8_DECODE_ERROR",
                        "message": str(err),
                    }
                )
                continue

            lines = text.splitlines()
            documents.append(
                NoteDocument(
                    relative_path=rel_path_str,
                    title=note_record.get("title", note_path.stem),
                    content=text,
                    headings=note_record.get("headings", []),
                    tags=note_record.get("tags", []),
                    wikilinks=note_record.get("wikilinks", []),
                    size_bytes=len(raw_bytes),
                    content_sha256=current_sha256,
                    line_count=len(lines),
                )
            )

        return documents


def load_vault(
    vault_root: Path | str, index_path: Path | str
) -> tuple[list[NoteDocument], SourceContext, list[dict[str, Any]]]:
    """Convenience helper to read vault documents with full validation."""
    reader = VaultReader(vault_root=vault_root, index_path=index_path)
    documents = reader.read_notes()
    return documents, reader.get_source_context(), reader.warnings
