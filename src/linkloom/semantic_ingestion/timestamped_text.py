"""Deterministic parsing of timestamped plain-text team conversations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import re

from linkloom.semantic_ingestion.artifacts import (
    TIMESTAMPED_TEXT_PARSER_VERSION,
    EvidenceSpan,
    RawArtifact,
    evidence_ref_for,
    line_range_for,
    message_id_for,
    segment_id_for,
)


_HEADER_PATTERN = re.compile(
    r"^\[(?P<timestamp>[^\]\r\n]+)\][ \t]+(?P<speaker>[^:\r\n]+):(?P<body>.*)$"
)
_HEADER_CANDIDATE_PATTERN = re.compile(r"^\[[^\]\r\n]*\]")
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


class ParseStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SourceParseIssue:
    reason_code: str
    line_start: int
    line_end: int
    detail: str

    def __post_init__(self) -> None:
        if self.reason_code != "SOURCE_PARSE_FAILED":
            raise ValueError("unsupported source parse reason code")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("parse issue line range is invalid")
        if not self.detail.strip():
            raise ValueError("parse issue detail is required")


@dataclass(frozen=True, slots=True)
class TimestampedTextSegment:
    ordinal: int
    message_id: str
    segment_id: str
    speaker: str
    event_time: datetime
    body_text: str
    evidence_span: EvidenceSpan

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")
        if not self.speaker.strip() or not self.body_text:
            raise ValueError("speaker and body_text are required")
        if not isinstance(self.event_time, datetime):
            raise ValueError("event_time must be a timezone-aware datetime")
        if self.event_time.tzinfo is None or self.event_time.utcoffset() is None:
            raise ValueError("event_time must be timezone-aware")
        object.__setattr__(self, "event_time", self.event_time.astimezone(UTC))
        if self.message_id != self.evidence_span.message_id:
            raise ValueError("segment message_id does not match its evidence span")
        if self.segment_id != self.evidence_span.segment_id:
            raise ValueError("segment_id does not match its evidence span")
        if self.body_text != self.evidence_span.quote:
            raise ValueError("body_text must equal the exact evidence quote")


@dataclass(frozen=True, slots=True)
class TimestampedTextParseResult:
    artifact_id: str
    artifact_version_id: str
    artifact_fingerprint: str
    parser_version: str
    status: ParseStatus
    segments: tuple[TimestampedTextSegment, ...]
    issues: tuple[SourceParseIssue, ...]

    def __post_init__(self) -> None:
        if self.parser_version != TIMESTAMPED_TEXT_PARSER_VERSION:
            raise ValueError("unsupported parser version")
        if not self.artifact_id.strip() or not self.artifact_version_id.strip():
            raise ValueError("artifact identity is required")
        if self.status is ParseStatus.COMPLETE and (not self.segments or self.issues):
            raise ValueError("COMPLETE requires segments and no issues")
        if self.status is ParseStatus.PARTIAL and (not self.segments or not self.issues):
            raise ValueError("PARTIAL requires both segments and issues")
        if self.status is ParseStatus.FAILED and (self.segments or not self.issues):
            raise ValueError("FAILED requires issues and no segments")


@dataclass(frozen=True, slots=True)
class _SourceLine:
    number: int
    start: int
    text: str


def _source_lines(content: str) -> tuple[_SourceLine, ...]:
    lines: list[_SourceLine] = []
    cursor = 0
    for number, raw_line in enumerate(content.splitlines(keepends=True), start=1):
        text = raw_line.rstrip("\r\n")
        lines.append(_SourceLine(number=number, start=cursor, text=text))
        cursor += len(raw_line)
    if content and not lines:
        lines.append(_SourceLine(number=1, start=0, text=content))
    return tuple(lines)


def _looks_like_header(text: str) -> bool:
    return _HEADER_CANDIDATE_PATTERN.match(text) is not None


def _parse_event_time(value: str) -> datetime:
    if _ISO_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be ISO-8601 with an explicit timezone")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an explicit timezone")
    return parsed.astimezone(UTC)


def _issue(line_start: int, line_end: int, detail: str) -> SourceParseIssue:
    return SourceParseIssue(
        reason_code="SOURCE_PARSE_FAILED",
        line_start=line_start,
        line_end=max(line_start, line_end),
        detail=detail,
    )


def _block_end(lines: tuple[_SourceLine, ...], header_index: int) -> int:
    index = header_index + 1
    while index < len(lines):
        line = lines[index]
        if not line.text.strip() or _looks_like_header(line.text):
            break
        index += 1
    return index


def parse_timestamped_text(artifact: RawArtifact) -> TimestampedTextParseResult:
    """Parse timestamped speaker/message blocks without calling a Provider.

    A parse issue is scoped to the affected block or orphan-text run. Valid
    blocks in the same artifact remain available as segments; callers must not
    infer a timestamp for any issue span.
    """
    if not isinstance(artifact, RawArtifact):
        raise TypeError("artifact must be RawArtifact")

    lines = _source_lines(artifact.content)
    segments: list[TimestampedTextSegment] = []
    issues: list[SourceParseIssue] = []

    if artifact.source_type != "timestamped_text":
        issues.append(_issue(1, max(1, len(lines)), "unsupported source type"))
        return TimestampedTextParseResult(
            artifact_id=artifact.artifact_id,
            artifact_version_id=artifact.artifact_version_id,
            artifact_fingerprint=artifact.artifact_fingerprint,
            parser_version=TIMESTAMPED_TEXT_PARSER_VERSION,
            status=ParseStatus.FAILED,
            segments=(),
            issues=tuple(issues),
        )

    index = 0
    message_ordinal = 0
    while index < len(lines):
        line = lines[index]
        if not line.text.strip():
            index += 1
            continue

        match = _HEADER_PATTERN.fullmatch(line.text)
        if match is None:
            # Preserve invalid source text in RawArtifact, but do not turn it
            # into an implicitly timestamped message.
            end_index = index + 1
            while end_index < len(lines):
                following = lines[end_index]
                if not following.text.strip() or _looks_like_header(following.text):
                    break
                end_index += 1
            issues.append(
                _issue(line.number, lines[end_index - 1].number, "text is not in a timestamped speaker block")
            )
            index = end_index
            continue

        ordinal = message_ordinal
        message_ordinal += 1
        block_end = _block_end(lines, index)
        try:
            event_time = _parse_event_time(match.group("timestamp").strip())
            speaker = match.group("speaker").strip()
            if not speaker:
                raise ValueError("speaker label is empty")
        except ValueError as error:
            issues.append(_issue(line.number, lines[max(index, block_end - 1)].number, str(error)))
            index = block_end
            continue

        inline_body = match.group("body")
        body_start: int | None = None
        body_end: int | None = None
        if inline_body.strip():
            body_start = line.start + match.start("body")
            body_end = line.start + len(line.text)

        continuation_end = block_end
        for continuation_index in range(index + 1, block_end):
            continuation = lines[continuation_index]
            if body_start is None:
                body_start = continuation.start
            body_end = continuation.start + len(continuation.text)

        if body_start is None or body_end is None or body_end <= body_start:
            issues.append(_issue(line.number, line.number, "message body is empty"))
            index = max(index + 1, continuation_end)
            continue

        raw_quote = artifact.content[body_start:body_end]
        left_trim = len(raw_quote) - len(raw_quote.lstrip())
        right_trim = len(raw_quote) - len(raw_quote.rstrip())
        quote_start = body_start + left_trim
        quote_end = body_end - right_trim
        if quote_end <= quote_start:
            issues.append(_issue(line.number, line.number, "message body is empty"))
            index = max(index + 1, continuation_end)
            continue

        quote = artifact.content[quote_start:quote_end]
        quote_sha256 = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        body_line_start, body_line_end = line_range_for(
            artifact.content,
            quote_start,
            quote_end,
        )
        message_id = message_id_for(artifact.artifact_version_id, ordinal)
        segment_id = segment_id_for(
            artifact.artifact_version_id,
            message_id,
            quote_start,
            quote_end,
            quote_sha256,
        )
        evidence_span = EvidenceSpan(
            workspace_id=artifact.workspace_id,
            artifact_id=artifact.artifact_id,
            artifact_version_id=artifact.artifact_version_id,
            source_ref=artifact.source_ref,
            content_hash=artifact.content_hash,
            ordinal=ordinal,
            message_id=message_id,
            segment_id=segment_id,
            evidence_ref=evidence_ref_for(segment_id),
            char_start=quote_start,
            char_end=quote_end,
            line_start=body_line_start,
            line_end=body_line_end,
            quote=quote,
            quote_sha256=quote_sha256,
        )
        if not evidence_span.verify(artifact):
            issues.append(_issue(line.number, body_line_end, "source evidence span failed verification"))
            index = max(index + 1, continuation_end)
            continue

        segments.append(
            TimestampedTextSegment(
                ordinal=ordinal,
                message_id=message_id,
                segment_id=segment_id,
                speaker=speaker,
                event_time=event_time,
                body_text=quote,
                evidence_span=evidence_span,
            )
        )
        index = max(index + 1, continuation_end)

    if segments and issues:
        status = ParseStatus.PARTIAL
    elif segments:
        status = ParseStatus.COMPLETE
    else:
        # RawArtifact rejects empty/whitespace content; any non-empty artifact
        # that produced no segment therefore has at least one parse issue.
        if not issues:
            issues.append(_issue(1, max(1, len(lines)), "no timestamped message blocks found"))
        status = ParseStatus.FAILED

    return TimestampedTextParseResult(
        artifact_id=artifact.artifact_id,
        artifact_version_id=artifact.artifact_version_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
        parser_version=TIMESTAMPED_TEXT_PARSER_VERSION,
        status=status,
        segments=tuple(segments),
        issues=tuple(issues),
    )
