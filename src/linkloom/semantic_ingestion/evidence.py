"""Source-independent evidence bindings shared by Agent Memory inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re
from typing import Any, Mapping

from linkloom.evidence_identity import passage_evidence_id
from linkloom.schemas import NoteDocument
from linkloom.semantic_ingestion.artifacts import (
    EvidenceSpan,
    RawArtifact,
    line_range_for,
    source_episode_id_for_source,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value.strip()


def _span_dict(span: EvidenceSpan) -> dict[str, Any]:
    return {
        "workspace_id": span.workspace_id,
        "artifact_id": span.artifact_id,
        "artifact_version_id": span.artifact_version_id,
        "source_ref": span.source_ref,
        "content_hash": span.content_hash,
        "ordinal": span.ordinal,
        "message_id": span.message_id,
        "segment_id": span.segment_id,
        "evidence_ref": span.evidence_ref,
        "char_start": span.char_start,
        "char_end": span.char_end,
        "line_start": span.line_start,
        "line_end": span.line_end,
        "quote": span.quote,
        "quote_sha256": span.quote_sha256,
    }


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceBinding:
    """Verified exact source evidence without requiring a timestamped-chat shape.

    ``evidence_ref`` is the source path's original evidence identity. For a
    Runtime passage this remains its ``ev_v2_*`` ID; ``semantic_span`` is
    populated only when the source came through Semantic Ingestion.
    """

    workspace_id: str
    evidence_ref: str
    source_episode_id: str
    source_identity: str
    source_version: str
    content_hash: str
    source_type: str
    source_locator: str
    quote: str
    char_start: int
    char_end: int
    line_start: int | None
    line_end: int | None
    event_time: datetime | None = None
    source_speaker: str | None = None
    semantic_span: EvidenceSpan | None = None
    verification_kind: str = "SOURCE_SPAN_V1"
    quote_sha256: str = ""
    run_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "evidence_ref",
            "source_episode_id",
            "source_identity",
            "source_version",
            "source_type",
            "source_locator",
            "verification_kind",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if not isinstance(self.quote, str) or not self.quote:
            raise ValueError("quote must be non-empty text")
        if not _SHA256.fullmatch(self.content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        quote_hash = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.quote_sha256 and self.quote_sha256 != quote_hash:
            raise ValueError("quote_sha256 does not match quote")
        object.__setattr__(self, "quote_sha256", quote_hash)
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        if (
            isinstance(self.char_start, bool)
            or not isinstance(self.char_start, int)
            or isinstance(self.char_end, bool)
            or not isinstance(self.char_end, int)
            or self.char_start < 0
            or self.char_end <= self.char_start
            or self.char_end - self.char_start != len(self.quote)
        ):
            raise ValueError("character span must exactly cover the quote")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be supplied together")
        if self.line_start is not None and (
            isinstance(self.line_start, bool)
            or not isinstance(self.line_start, int)
            or isinstance(self.line_end, bool)
            or not isinstance(self.line_end, int)
            or self.line_start < 1
            or self.line_end < self.line_start
        ):
            raise ValueError("line range is invalid")
        if self.event_time is not None:
            if (
                not isinstance(self.event_time, datetime)
                or self.event_time.tzinfo is None
                or self.event_time.utcoffset() is None
            ):
                raise ValueError("event_time must be a timezone-aware source time or null")
            object.__setattr__(self, "event_time", self.event_time.astimezone(UTC))
        if self.source_speaker is not None:
            object.__setattr__(self, "source_speaker", _required_text(self.source_speaker, "source_speaker"))
        expected_episode = source_episode_id_for_source(
            self.workspace_id,
            self.source_identity,
            self.source_version,
        )
        if self.source_episode_id != expected_episode:
            raise ValueError("source_episode_id does not match source identity and version")
        if self.semantic_span is not None:
            if not isinstance(self.semantic_span, EvidenceSpan):
                raise TypeError("semantic_span must be EvidenceSpan or null")
            span = self.semantic_span
            if (
                span.workspace_id != self.workspace_id
                or span.evidence_ref != self.evidence_ref
                or span.artifact_id != self.source_identity
                or span.artifact_version_id != self.source_version
                or span.source_ref != self.source_locator
                or span.content_hash != self.content_hash
                or span.quote != self.quote
                or span.char_start != self.char_start
                or span.char_end != self.char_end
                or span.line_start != self.line_start
                or span.line_end != self.line_end
                or span.quote_sha256 != self.quote_sha256
            ):
                raise ValueError("semantic span does not match verified evidence fields")

    @property
    def artifact_id(self) -> str:
        """Compatibility name for the source's stable identity."""
        return self.source_identity

    @property
    def artifact_version_id(self) -> str:
        """Compatibility name for the immutable source version."""
        return self.source_version

    @property
    def source_ref(self) -> str:
        """Compatibility name for the immutable source locator."""
        return self.source_locator

    @property
    def content_sha256(self) -> str:
        return self.content_hash

    @property
    def segment_id(self) -> str | None:
        return self.semantic_span.segment_id if self.semantic_span is not None else None

    def verify_source(
        self,
        content: str,
        *,
        workspace_id: str | None = None,
        source_identity: str | None = None,
        source_version: str | None = None,
        source_locator: str | None = None,
    ) -> bool:
        """Recheck source hash, locator, exact quote offsets, and line range."""
        if not isinstance(content, str):
            return False
        if workspace_id is not None and workspace_id != self.workspace_id:
            return False
        if source_identity is not None and source_identity != self.source_identity:
            return False
        if source_version is not None and source_version != self.source_version:
            return False
        if source_locator is not None and source_locator != self.source_locator:
            return False
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != self.content_hash:
            return False
        if content[self.char_start:self.char_end] != self.quote:
            return False
        if self.line_start is not None:
            try:
                return line_range_for(content, self.char_start, self.char_end) == (
                    self.line_start,
                    self.line_end,
                )
            except ValueError:
                return False
        return True

    def verify(self, artifact: RawArtifact) -> bool:
        """Compatibility verifier for Semantic Ingestion-backed candidates."""
        if not isinstance(artifact, RawArtifact) or self.semantic_span is None:
            return False
        # EvidenceSpan.verify already checks the immutable source identity/hash,
        # exact quote offsets, and line range against this artifact.
        return self.semantic_span.verify(artifact)

    @classmethod
    def from_semantic_segment(
        cls,
        artifact: RawArtifact,
        segment: object,
    ) -> "VerifiedEvidenceBinding":
        from linkloom.semantic_ingestion.timestamped_text import TimestampedTextSegment

        if not isinstance(artifact, RawArtifact):
            raise TypeError("artifact must be RawArtifact")
        if not isinstance(segment, TimestampedTextSegment):
            raise TypeError("segment must be TimestampedTextSegment")
        span = segment.evidence_span
        if not span.verify(artifact):
            raise ValueError("semantic evidence span does not verify against its artifact")
        return cls(
            workspace_id=artifact.workspace_id,
            evidence_ref=span.evidence_ref,
            source_episode_id=source_episode_id_for_source(
                artifact.workspace_id,
                artifact.artifact_id,
                artifact.artifact_version_id,
            ),
            source_identity=artifact.artifact_id,
            source_version=artifact.artifact_version_id,
            content_hash=artifact.content_hash,
            source_type=artifact.source_type,
            source_locator=artifact.source_ref,
            quote=span.quote,
            char_start=span.char_start,
            char_end=span.char_end,
            line_start=span.line_start,
            line_end=span.line_end,
            event_time=segment.event_time,
            source_speaker=segment.speaker,
            semantic_span=span,
            verification_kind="SEMANTIC_EVIDENCE_SPAN_V1",
        )

    @classmethod
    def from_legacy_semantic_span(
        cls,
        *,
        source_episode_id: str,
        span: EvidenceSpan,
        event_time: datetime,
        source_speaker: str | None,
    ) -> "VerifiedEvidenceBinding":
        """Adapt an older persisted Semantic Ingestion candidate without changing its payload."""
        if not isinstance(span, EvidenceSpan):
            raise TypeError("span must be EvidenceSpan")
        if not isinstance(event_time, datetime):
            raise TypeError("event_time must be datetime")
        if not span.quote or hashlib.sha256(span.quote.encode("utf-8")).hexdigest() != span.quote_sha256:
            raise ValueError("legacy semantic span quote hash is invalid")
        return cls(
            workspace_id=span.workspace_id,
            evidence_ref=span.evidence_ref,
            source_episode_id=source_episode_id,
            source_identity=span.artifact_id,
            source_version=span.artifact_version_id,
            content_hash=span.content_hash,
            source_type="legacy_semantic_ingestion",
            source_locator=span.source_ref,
            quote=span.quote,
            char_start=span.char_start,
            char_end=span.char_end,
            line_start=span.line_start,
            line_end=span.line_end,
            event_time=event_time,
            source_speaker=source_speaker,
            semantic_span=span,
            verification_kind="SEMANTIC_EVIDENCE_SPAN_V1",
        )

    @classmethod
    def from_runtime_passage(
        cls,
        *,
        workspace_id: str,
        run_id: str,
        document: NoteDocument,
        passage: Mapping[str, object],
    ) -> "VerifiedEvidenceBinding":
        """Bind the Runtime's existing passage ID to the current exact source span."""
        workspace = _required_text(workspace_id, "workspace_id")
        run = _required_text(run_id, "run_id")
        if not isinstance(document, NoteDocument):
            raise TypeError("document must be NoteDocument")
        if not isinstance(passage, Mapping):
            raise TypeError("passage must be a mapping")
        try:
            path = _required_text(passage.get("relative_path"), "relative_path")
            evidence_ref = _required_text(passage.get("evidence_id"), "evidence_id")
            passage_workspace = _required_text(passage.get("workspace_id"), "workspace_id")
            source_ref = _required_text(passage.get("source_ref"), "source_ref")
            logical_path = _required_text(passage.get("logical_path"), "logical_path")
            content_hash = _required_text(passage.get("content_sha256"), "content_sha256")
            quote_hash = _required_text(passage.get("quote_sha256"), "quote_sha256")
        except ValueError as error:
            raise ValueError("PASSAGE_FIELDS_MISSING") from error
        raw_quote = passage.get("quote")
        if not isinstance(raw_quote, str) or not raw_quote:
            raise ValueError("PASSAGE_SPAN_MISMATCH")
        quote = raw_quote
        status = passage.get("status")
        line_start = passage.get("line_start")
        line_end = passage.get("line_end")
        if workspace != passage_workspace:
            raise ValueError("WORKSPACE_MISMATCH")
        if path != document.relative_path or source_ref != path or logical_path != f"/{path}":
            raise ValueError("SOURCE_IDENTITY_MISMATCH")
        if status != "verified":
            raise ValueError("PASSAGE_NOT_VERIFIED")
        if not _SHA256.fullmatch(content_hash) or not _SHA256.fullmatch(quote_hash):
            raise ValueError("SOURCE_HASH_MISMATCH")
        actual_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        if content_hash != document.content_sha256 or content_hash != actual_hash:
            raise ValueError("SOURCE_HASH_MISMATCH")
        actual_quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        if quote_hash != actual_quote_hash:
            raise ValueError("PASSAGE_HASH_MISMATCH")
        if (
            isinstance(line_start, bool)
            or not isinstance(line_start, int)
            or isinstance(line_end, bool)
            or not isinstance(line_end, int)
            or line_start != line_end
            or line_start < 1
        ):
            raise ValueError("PASSAGE_SPAN_MISMATCH")
        lines = document.content.splitlines(keepends=True)
        if line_start > len(lines):
            raise ValueError("PASSAGE_SPAN_MISMATCH")
        char_start = sum(len(line) for line in lines[:line_start - 1])
        line_content = lines[line_start - 1].rstrip("\r\n")
        char_end = char_start + len(line_content)
        if line_content != quote:
            raise ValueError("PASSAGE_SPAN_MISMATCH")
        expected_ref = passage_evidence_id(
            workspace_id=workspace,
            resource_id=path,
            document_id=path,
            logical_path=f"/{path}",
            source_ref=path,
            content_hash=content_hash,
            line_start=line_start,
            line_end=line_end,
            quote_hash=quote_hash,
        )
        if evidence_ref != expected_ref:
            raise ValueError("PASSAGE_IDENTITY_MISMATCH")
        return cls(
            workspace_id=workspace,
            evidence_ref=evidence_ref,
            source_episode_id=source_episode_id_for_source(workspace, path, content_hash),
            source_identity=path,
            source_version=content_hash,
            content_hash=content_hash,
            source_type="obsidian_markdown",
            source_locator=path,
            quote=quote,
            char_start=char_start,
            char_end=char_end,
            line_start=line_start,
            line_end=line_end,
            event_time=None,
            source_speaker=None,
            verification_kind="RUNTIME_PASSAGE_V2",
            run_id=run,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "evidence_ref": self.evidence_ref,
            "source_episode_id": self.source_episode_id,
            "source_identity": self.source_identity,
            "source_version": self.source_version,
            "content_hash": self.content_hash,
            "source_type": self.source_type,
            "source_locator": self.source_locator,
            "quote": self.quote,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "source_speaker": self.source_speaker,
            "semantic_span": _span_dict(self.semantic_span) if self.semantic_span else None,
            "verification_kind": self.verification_kind,
            "quote_sha256": self.quote_sha256,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerifiedEvidenceBinding":
        fields = {
            "workspace_id",
            "evidence_ref",
            "source_episode_id",
            "source_identity",
            "source_version",
            "content_hash",
            "source_type",
            "source_locator",
            "quote",
            "char_start",
            "char_end",
            "line_start",
            "line_end",
            "event_time",
            "source_speaker",
            "semantic_span",
            "verification_kind",
            "quote_sha256",
            "run_id",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("stored verified evidence binding is malformed")
        raw_span = value["semantic_span"]
        if raw_span is not None:
            if not isinstance(raw_span, dict):
                raise ValueError("stored Semantic Ingestion span is malformed")
            span = EvidenceSpan(**raw_span)
        else:
            span = None
        raw_time = value["event_time"]
        event_time = datetime.fromisoformat(raw_time) if isinstance(raw_time, str) else None
        return cls(
            workspace_id=value["workspace_id"],
            evidence_ref=value["evidence_ref"],
            source_episode_id=value["source_episode_id"],
            source_identity=value["source_identity"],
            source_version=value["source_version"],
            content_hash=value["content_hash"],
            source_type=value["source_type"],
            source_locator=value["source_locator"],
            quote=value["quote"],
            char_start=value["char_start"],
            char_end=value["char_end"],
            line_start=value["line_start"],
            line_end=value["line_end"],
            event_time=event_time,
            source_speaker=value["source_speaker"],
            semantic_span=span,
            verification_kind=value["verification_kind"],
            quote_sha256=value["quote_sha256"],
            run_id=value["run_id"],
        )
