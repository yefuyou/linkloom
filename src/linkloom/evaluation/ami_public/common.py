"""Versioned file contracts shared by custodian, predictor, and scorer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ARCHIVE_SHA256 = "b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d"
QUERY_SHA256 = "d3209488cdd2ee0d9f0d2e77e460ce9dc55be8d3810b460d86b3c633839d6c1d"
MEETINGS = tuple(f"{series}{suffix}" for series in ("ES2002", "ES2005") for suffix in "abcd")
VERSION = "ami-public-retrieval/v1"
QUERY_FIELDS = {"query_id", "meeting_id", "split", "query_text", "query_origin", "task"}
PASSAGE_FIELDS = {"meeting_id", "utterance_id", "piece_id", "text", "text_sha256",
                  "speaker", "char_start", "char_end", "utterance_start", "utterance_end",
                  "canonical_sha256", "source_member", "member_sha256", "archive_sha256"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def passage_source_ref(passage: dict) -> str:
    return f'ami:{passage["archive_sha256"]}:{passage["source_member"]}#id({passage["utterance_id"]})'


def passage_logical_path(passage: dict) -> str:
    return f'/ami/{passage["meeting_id"]}/transcript.txt'


def transcript_projection_digest(rows: list[dict], id_key: str) -> str:
    """Compare representations without depending on their canonical offsets."""
    projection = sorted([{"id": r[id_key], "text": r["text"], "native_word_ids": r["native_word_ids"]}
                         for r in rows], key=lambda r: r["id"])
    return sha(json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def write_rows(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def unique_rows(rows: list[dict], key: str) -> dict[str, dict]:
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {key}")
    return result


def verify_files(root: Path, files: dict[str, str]) -> None:
    for name, digest in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path.read_bytes()) != digest:
            raise ValueError(f"file hash mismatch: {name}")


def validate_queries(rows: list[dict]) -> None:
    unique_rows(rows, "query_id")
    for row in rows:
        if set(row) != QUERY_FIELDS or not row["query_text"].strip():
            raise ValueError("query schema violation (no answer fields allowed)")
        if row["meeting_id"] not in MEETINGS or row["split"] != ("dev" if row["meeting_id"].startswith("ES2002") else "test"):
            raise ValueError("series split violation")


def validate_passages(rows: list[dict]) -> None:
    unique_rows(rows, "piece_id")
    for row in rows:
        if set(row) != PASSAGE_FIELDS or sha(row["text"].encode("utf-8")) != row["text_sha256"]:
            raise ValueError("passage schema/hash violation (transcript fields only)")
        if len(row["text"]) > 8000 or len(row["text"].encode("utf-8")) > 16000:
            raise ValueError("passage exceeds input budget")
