"""Small ordinary-document adapter; immutable Source identity is independent of format.

One document is one exact context span (the product's existing 256 KB limit
still applies). Only explicit, unambiguous labelled ISO dates are metadata.
Neither a document date nor an import date is a speech/effective timestamp.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re

from linkloom.semantic_ingestion.artifacts import (
    EvidenceSpan, RawArtifact, evidence_ref_for, line_range_for, message_id_for, segment_id_for,
)
from linkloom.semantic_ingestion.timestamped_text import (
    ParseStatus, SourceParseIssue, TimestampedTextParseResult, TimestampedTextSegment, parse_timestamped_text,
)


@dataclass(frozen=True, slots=True)
class DocumentTextSegment:
    ordinal: int
    message_id: str
    segment_id: str
    body_text: str
    evidence_span: EvidenceSpan
    document_date: date | None = None
    event_date: date | None = None
    speaker: None = None
    event_time: None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_span, EvidenceSpan):
            raise TypeError("document evidence must be EvidenceSpan")
        if (isinstance(self.ordinal, bool) or self.ordinal != self.evidence_span.ordinal
            or self.message_id != self.evidence_span.message_id
            or self.segment_id != self.evidence_span.segment_id
            or self.body_text != self.evidence_span.quote):
            raise ValueError("document segment must match its exact evidence span")
        if self.speaker is not None or self.event_time is not None:
            raise ValueError("ordinary documents do not provide timestamped speech")
        for value in (self.document_date, self.event_date):
            if value is not None and type(value) is not date:
                raise ValueError("document metadata dates must preserve date precision")
        if self.document_date != _labelled_date(self.body_text, "文档日期|Document date") or self.event_date != _labelled_date(self.body_text, "会议日期|事件日期|Meeting date|Event date"):
            raise ValueError("document metadata dates must be explicitly source-grounded")


SourceSegment = TimestampedTextSegment | DocumentTextSegment
SOURCE_SEGMENT_TYPES = (TimestampedTextSegment, DocumentTextSegment)


@dataclass(frozen=True, slots=True)
class DocumentTextParseResult:
    artifact_id: str
    artifact_version_id: str
    artifact_fingerprint: str
    segments: tuple[DocumentTextSegment, ...]
    parser_version: str = "document-text/v1"
    status: ParseStatus = ParseStatus.COMPLETE
    issues: tuple[SourceParseIssue, ...] = ()


def _labelled_date(text: str, labels: str) -> date | None:
    # Full-line matching avoids confusing an effective date mentioned in prose
    # with document metadata. Conflicting/invalid values remain unknown.
    values = re.findall(rf"^[ \t]*(?:{labels})[ \t]*[:：][ \t]*([^\r\n]+?)[ \t]*\r?$", text, re.M | re.I)
    if not values or len(set(values)) != 1:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values[0]):
            return None
        return date.fromisoformat(values[0])
    except ValueError:
        return None


_ISO_TIME_IN_TEXT = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2}))?"
    r"|\d{4}年\d{1,2}月\d{1,2}日"
)
_METADATA_LINE = re.compile(
    r"^\s*(?:文档日期|会议日期|事件日期|Document date|Meeting date|Event date)\s*[:：]", re.I
)


def document_valid_time_is_grounded(text: str, value: date | datetime, *, end: bool = False) -> bool:
    """Require a local effective-time role, never a document/meeting date fallback.

    This intentionally small policy fails unresolved for other wording so that
    a human can supply valid time through the existing review resolution.
    It is also used when reconstructing persisted document candidates.
    """
    if end:
        before = r"(?:\buntil|\bthrough|\bvalid\s+(?:to|until)|\bexpires(?:\s+on)?|有效期至|有效截至|失效日期|终止日期)\s*[:：]?\s*$"
        after = r"^\s*(?:到期|失效|终止)"
    else:
        before = r"(?:\beffective(?:\s+(?:from|on|date|time))?|\bstarting|\bvalid\s+from|\b(?:replaced|superseded)\s+[^.!?\n]*?\s+on|生效日期|生效时间|开始日期|生效自)\s*[:：]?\s*$"
        after = r"^\s*(?:起|生效|开始生效)"
    for line in text.splitlines():
        if _METADATA_LINE.search(line):
            continue
        for match in _ISO_TIME_IN_TEXT.finditer(line):
            raw = match.group()
            try:
                observed = parse_source_time(raw)
            except ValueError:
                continue
            if observed == value and (
                re.search(before, line[:match.start()], re.I)
                or re.search(after, line[match.end():], re.I)
            ):
                return True
    return False


def parse_source_time(raw: str) -> date | datetime:
    """Normalize explicit written dates without inventing a time or timezone."""
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if match:
        return date(*(int(part) for part in match.groups()))
    if "T" in raw:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("explicit timestamp requires timezone")
        return value
    return date.fromisoformat(raw)


_source_parses = ContextVar("source_parses", default=None)


@contextmanager
def source_parse_session():
    """Reuse only pure parsing of the exact immutable objects in this scope.

    No authorization, registry or currentness result is stored. Retaining the
    object alongside its id prevents identity reuse; reset also runs on errors.
    """
    token = _source_parses.set({})
    try:
        yield
    finally:
        _source_parses.reset(token)


def parse_source_text(artifact: RawArtifact) -> TimestampedTextParseResult | DocumentTextParseResult:
    cache = _source_parses.get()
    if cache is None:
        return _parse_source_text(artifact)
    entry = cache.get(id(artifact))
    if entry is not None and entry[0] is artifact:
        return entry[1]
    parsed = _parse_source_text(artifact)
    cache[id(artifact)] = (artifact, parsed)
    return parsed


def _parse_source_text(artifact: RawArtifact) -> TimestampedTextParseResult | DocumentTextParseResult:
    if not isinstance(artifact, RawArtifact):
        raise TypeError("artifact must be RawArtifact")
    # Content identifies timestamped conversations, even in a Markdown file.
    # A malformed timestamp-shaped header still takes the strict old parser;
    # Markdown links and task lists are ordinary document syntax.
    if re.search(r"^\[\d{4}-\d{2}-\d{2}T", artifact.content, re.M):
        return parse_timestamped_text(artifact)
    text = artifact.content
    quote_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    message_id = message_id_for(artifact.artifact_version_id, 0)
    segment_id = segment_id_for(artifact.artifact_version_id, message_id, 0, len(text), quote_hash)
    first, last = line_range_for(text, 0, len(text))
    span = EvidenceSpan(
        workspace_id=artifact.workspace_id, artifact_id=artifact.artifact_id,
        artifact_version_id=artifact.artifact_version_id, source_ref=artifact.source_ref,
        content_hash=artifact.content_hash, ordinal=0, message_id=message_id, segment_id=segment_id,
        evidence_ref=evidence_ref_for(segment_id), char_start=0, char_end=len(text),
        line_start=first, line_end=last, quote=text, quote_sha256=quote_hash,
    )
    if not span.verify(artifact):
        raise ValueError("document evidence does not verify against original source")
    return DocumentTextParseResult(
        artifact.artifact_id, artifact.artifact_version_id, artifact.artifact_fingerprint,
        (DocumentTextSegment(0, message_id, segment_id, text, span,
                             _labelled_date(text, "文档日期|Document date"),
                             _labelled_date(text, "会议日期|事件日期|Meeting date|Event date")),),
    )
