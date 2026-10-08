"""Immutable raw source artifacts and exact, workspace-scoped evidence spans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import parse_qsl, quote, urlsplit


TIMESTAMPED_TEXT_PARSER_VERSION = "timestamped-text/v1"
_MAX_METADATA_BYTES = 4096
_SENSITIVE_METADATA_KEY = re.compile(
    r"(?:password|passwd|secret|token|credential|authorization|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:password|passwd|secret|token|credential|authorization|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RawArtifactSourceType(StrEnum):
    """V1 source-type name; future adapters may define additional values."""

    TIMESTAMPED_TEXT = "timestamped_text"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} must be non-empty text without control characters")
    return normalized


def _stable_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_source_uri(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _required_text(value, "source_uri")
    try:
        parsed = urlsplit(normalized)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("source_uri must not contain embedded credentials")
        if any(_SENSITIVE_QUERY_KEY.search(key) for key, _ in parse_qsl(parsed.query)):
            raise ValueError("source_uri must not contain secret-bearing query parameters")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("source_uri is malformed") from error
    return normalized


def _normalize_metadata(value: Mapping[str, object] | None) -> Mapping[str, object]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping of JSON scalar values")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        normalized_key = _required_text(key, "metadata key")
        if _SENSITIVE_METADATA_KEY.search(normalized_key):
            raise ValueError("metadata keys must not identify secret-bearing values")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("metadata values must be JSON scalar values")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("metadata numbers must be finite")
        if isinstance(item, str) and any(ord(character) == 0 for character in item):
            raise ValueError("metadata strings must not contain NUL")
        normalized[normalized_key] = item
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds the size limit")
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """One immutable text source version, scoped to a LinkLoom workspace.

    Ingestion time is operational metadata and is deliberately excluded from
    content/version fingerprints.
    """

    workspace_id: str
    artifact_id: str
    source_type: str
    content: str
    source_uri: str | None = None
    parent_id: str | None = None
    thread_id: str | None = None
    author: str | None = None
    ingestion_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, object] = field(default_factory=dict)
    content_hash: str = field(init=False)
    artifact_fingerprint: str = field(init=False)
    artifact_version_id: str = field(init=False)
    source_ref: str = field(init=False)

    def __post_init__(self) -> None:
        workspace_id = _required_text(self.workspace_id, "workspace_id")
        artifact_id = _required_text(self.artifact_id, "artifact_id")
        source_type = _required_text(self.source_type, "source_type")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be non-empty text")
        try:
            content_bytes = self.content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("content must be valid UTF-8 text") from error
        if not isinstance(self.ingestion_time, datetime):
            raise ValueError("ingestion_time must be a timezone-aware datetime")
        if self.ingestion_time.tzinfo is None or self.ingestion_time.utcoffset() is None:
            raise ValueError("ingestion_time must be timezone-aware")

        for name in ("parent_id", "thread_id", "author"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_text(value, name))

        source_uri = _safe_source_uri(self.source_uri)
        metadata = _normalize_metadata(self.metadata)
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        fingerprint = _stable_sha256(
            [
                "linkloom-raw-artifact-v1",
                workspace_id,
                artifact_id,
                source_type,
                content_hash,
            ]
        )

        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_uri", source_uri)
        object.__setattr__(self, "ingestion_time", self.ingestion_time.astimezone(UTC))
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "artifact_fingerprint", fingerprint)
        object.__setattr__(self, "artifact_version_id", f"artifact_v1_{fingerprint}")
        object.__setattr__(
            self,
            "source_ref",
            source_uri
            if source_uri is not None
            else f"artifact://{quote(workspace_id, safe='-._~')}/{quote(artifact_id, safe='-._~')}",
        )


def source_episode_id_for(artifact: RawArtifact) -> str:
    """Return the stable workspace-local episode identity for an artifact version."""
    if not isinstance(artifact, RawArtifact):
        raise TypeError("artifact must be RawArtifact")
    return source_episode_id_for_source(
        artifact.workspace_id,
        artifact.artifact_id,
        artifact.artifact_version_id,
    )


def source_episode_id_for_source(
    workspace_id: str,
    source_identity: str,
    source_version: str,
) -> str:
    """Return a stable workspace-local episode ID for any immutable source version.

    Semantic Ingestion keeps its historical identity because it delegates to
    this function with the RawArtifact identity and artifact version.
    """
    workspace = _required_text(workspace_id, "workspace_id")
    identity = _required_text(source_identity, "source_identity")
    version = _required_text(source_version, "source_version")
    fingerprint = _stable_sha256([workspace, identity, version])
    return f"episode_v1_{fingerprint}"


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """A verifiable quote from exactly one immutable RawArtifact version.

    Character offsets use Python Unicode code-point indexing and are end
    exclusive. They address quote text, not the surrounding timestamp/speaker
    header.
    """

    workspace_id: str
    artifact_id: str
    artifact_version_id: str
    source_ref: str
    content_hash: str
    ordinal: int
    message_id: str
    segment_id: str
    evidence_ref: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    quote: str
    quote_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "artifact_id",
            "artifact_version_id",
            "source_ref",
            "message_id",
            "segment_id",
            "evidence_ref",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if not _SHA256_PATTERN.fullmatch(self.content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        if not _SHA256_PATTERN.fullmatch(self.quote_sha256):
            raise ValueError("quote_sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ValueError("character span must be non-empty and end-exclusive")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("line span must be a valid one-based inclusive range")
        if not self.quote or hashlib.sha256(self.quote.encode("utf-8")).hexdigest() != self.quote_sha256:
            raise ValueError("quote_sha256 does not match quote")
        expected_message_id = message_id_for(self.artifact_version_id, self.ordinal)
        if self.message_id != expected_message_id:
            raise ValueError("message_id does not match artifact version and ordinal")
        expected_segment_id = segment_id_for(
            self.artifact_version_id,
            self.message_id,
            self.char_start,
            self.char_end,
            self.quote_sha256,
        )
        if self.segment_id != expected_segment_id:
            raise ValueError("segment_id does not match its immutable evidence span")
        if self.evidence_ref != evidence_ref_for(self.segment_id):
            raise ValueError("evidence_ref does not match segment_id")

    def verify(self, artifact: RawArtifact) -> bool:
        """Verify workspace, source version, offsets, and exact quote binding."""
        if not isinstance(artifact, RawArtifact):
            return False
        return (
            self.workspace_id == artifact.workspace_id
            and self.artifact_id == artifact.artifact_id
            and self.artifact_version_id == artifact.artifact_version_id
            and self.source_ref == artifact.source_ref
            and self.content_hash == artifact.content_hash
            and self.char_end <= len(artifact.content)
            and artifact.content[self.char_start:self.char_end] == self.quote
            and line_range_for(artifact.content, self.char_start, self.char_end)
            == (self.line_start, self.line_end)
            and hashlib.sha256(self.quote.encode("utf-8")).hexdigest() == self.quote_sha256
        )


def line_range_for(content: str, char_start: int, char_end: int) -> tuple[int, int]:
    """Return the one-based inclusive lines touched by a non-empty span."""
    if char_start < 0 or char_end <= char_start or char_end > len(content):
        raise ValueError("character span is outside content")
    line_starts = [0]
    for match in re.finditer(r"\r\n|\r|\n", content):
        line_starts.append(match.end())
    start_line = _line_number_at(line_starts, char_start)
    end_line = _line_number_at(line_starts, char_end - 1)
    return start_line, end_line


def _line_number_at(line_starts: list[int], position: int) -> int:
    low = 0
    high = len(line_starts)
    while low < high:
        middle = (low + high) // 2
        if line_starts[middle] <= position:
            low = middle + 1
        else:
            high = middle
    return low


def message_id_for(artifact_version_id: str, ordinal: int) -> str:
    digest = _stable_sha256(
        [TIMESTAMPED_TEXT_PARSER_VERSION, artifact_version_id, ordinal]
    )
    return f"message_v1_{digest}"


def segment_id_for(
    artifact_version_id: str,
    message_id: str,
    char_start: int,
    char_end: int,
    quote_sha256: str,
) -> str:
    digest = _stable_sha256(
        [
            TIMESTAMPED_TEXT_PARSER_VERSION,
            artifact_version_id,
            message_id,
            char_start,
            char_end,
            quote_sha256,
        ]
    )
    return f"segment_v1_{digest}"


def evidence_ref_for(segment_id: str) -> str:
    return f"evidence:semantic-ingestion:v1:{segment_id}"
