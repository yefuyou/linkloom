from __future__ import annotations

from datetime import UTC, datetime
import hashlib

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.timestamped_text import (
    ParseStatus,
    parse_timestamped_text,
)


def _artifact(
    content: str,
    *,
    workspace_id: str = "team-alpha",
    artifact_id: str = "meeting-2026-10-01",
    source_type: str = "timestamped_text",
    ingestion_time: datetime | None = None,
    source_uri: str | None = None,
) -> RawArtifact:
    return RawArtifact(
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        source_type=source_type,
        content=content,
        ingestion_time=ingestion_time or datetime(2026, 10, 7, tzinfo=UTC),
        source_uri=source_uri,
    )


def test_raw_artifact_hashes_exact_utf8_and_has_stable_version_identity() -> None:
    content = "[2026-10-01T10:03:00Z] Alice: 决定 😀"
    first = _artifact(content)
    replay = _artifact(
        content,
        ingestion_time=datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert first.content_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert first.artifact_version_id.startswith("artifact_v1_")
    assert first.artifact_version_id == replay.artifact_version_id
    assert first.ingestion_time != replay.ingestion_time


def test_raw_artifact_uses_stable_local_source_reference_when_uri_is_absent() -> None:
    artifact = _artifact("content")

    assert artifact.source_ref == "artifact://team-alpha/meeting-2026-10-01"


def test_raw_artifact_metadata_is_small_scalar_json_and_read_only() -> None:
    artifact = RawArtifact(
        workspace_id="team-alpha",
        artifact_id="meeting-1",
        source_type="timestamped_text",
        content="source content",
        metadata={"source_label": "planning", "revision": 2},
    )

    assert artifact.metadata["source_label"] == "planning"
    with pytest.raises(TypeError):
        artifact.metadata["source_label"] = "changed"  # type: ignore[index]

    with pytest.raises(ValueError, match="secret-bearing"):
        RawArtifact(
            workspace_id="team-alpha",
            artifact_id="meeting-2",
            source_type="timestamped_text",
            content="source content",
            metadata={"api_key": "must-not-be-kept"},
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workspace_id": "  "},
        {"artifact_id": ""},
        {"content": ""},
        {"ingestion_time": datetime(2026, 10, 7)},
    ],
)
def test_raw_artifact_rejects_missing_identity_content_or_naive_ingestion_time(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "workspace_id": "team-alpha",
        "artifact_id": "meeting-1",
        "source_type": "timestamped_text",
        "content": "source content",
        "ingestion_time": datetime(2026, 10, 7, tzinfo=UTC),
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        RawArtifact(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "source_uri",
    [
        "https://user:password@example.test/meeting",
        "https://example.test/meeting?access_token=secret",
        "https://example.test/meeting?api_key=secret",
    ],
)
def test_raw_artifact_rejects_source_uris_that_embed_credentials(source_uri: str) -> None:
    with pytest.raises(ValueError):
        _artifact("source content", source_uri=source_uri)


def test_parser_extracts_inline_and_multiline_turns_with_exact_unicode_spans() -> None:
    content = (
        "[2026-10-01T10:03:00-07:00] Alice: We decided to use Vendor A 😀.\n"
        "\n"
        "[2026-10-03T16:20:00+08:00] Bob:\n"
        "After the security review, Vendor B replaces Vendor A.\n"
    )
    artifact = _artifact(content)

    parsed = parse_timestamped_text(artifact)

    assert parsed.status is ParseStatus.COMPLETE
    assert len(parsed.segments) == 2
    alice, bob = parsed.segments
    assert alice.speaker == "Alice"
    assert alice.body_text == "We decided to use Vendor A 😀."
    assert alice.event_time == datetime(2026, 10, 1, 17, 3, tzinfo=UTC)
    assert alice.evidence_span.quote == alice.body_text
    assert alice.evidence_span.line_start == 1
    assert alice.evidence_span.line_end == 1
    assert content[alice.evidence_span.char_start:alice.evidence_span.char_end] == alice.body_text
    assert alice.evidence_span.verify(artifact)

    assert bob.speaker == "Bob"
    assert bob.body_text == "After the security review, Vendor B replaces Vendor A."
    assert bob.event_time == datetime(2026, 10, 3, 8, 20, tzinfo=UTC)
    assert bob.evidence_span.line_start == 4
    assert bob.evidence_span.line_end == 4
    assert content[bob.evidence_span.char_start:bob.evidence_span.char_end] == bob.body_text
    assert bob.evidence_span.verify(artifact)


def test_parser_preserves_multiline_crlf_quote_and_unicode_character_offsets() -> None:
    content = (
        "[2026-10-01T10:03:00Z] Alice:\r\n"
        "  First line 😀\r\n"
        "  Second line  \r\n"
        "\r\n"
    )
    artifact = _artifact(content)

    parsed = parse_timestamped_text(artifact)

    assert parsed.status is ParseStatus.COMPLETE
    segment = parsed.segments[0]
    assert segment.body_text == "First line 😀\r\n  Second line"
    span = segment.evidence_span
    assert (span.line_start, span.line_end) == (2, 3)
    assert content[span.char_start:span.char_end] == span.quote
    assert span.verify(artifact)


def test_segment_and_evidence_identities_are_stable_across_reingestion_time() -> None:
    content = "[2026-10-01T10:03:00Z] Alice: We decided to use Vendor A."
    first = parse_timestamped_text(_artifact(content))
    replay = parse_timestamped_text(
        _artifact(content, ingestion_time=datetime(2026, 10, 8, tzinfo=UTC))
    )

    assert first.segments[0].message_id == replay.segments[0].message_id
    assert first.segments[0].segment_id == replay.segments[0].segment_id
    assert first.segments[0].evidence_span.evidence_ref == replay.segments[0].evidence_span.evidence_ref


def test_parser_marks_naive_timestamp_block_failed_without_assigning_its_body() -> None:
    content = (
        "[2026-10-01T10:03:00] Alice: This timestamp has no timezone.\n"
        "This continuation belongs to the invalid block.\n"
        "\n"
        "[2026-10-03T16:20:00Z] Bob: This block is valid.\n"
    )
    parsed = parse_timestamped_text(_artifact(content))

    assert parsed.status is ParseStatus.PARTIAL
    assert len(parsed.segments) == 1
    assert parsed.segments[0].speaker == "Bob"
    assert parsed.segments[0].body_text == "This block is valid."
    assert parsed.issues[0].reason_code == "SOURCE_PARSE_FAILED"
    assert parsed.issues[0].line_start == 1
    assert "This continuation belongs" not in parsed.segments[0].body_text


def test_parser_rejects_orphan_text_and_unknown_source_type_explicitly() -> None:
    orphan = parse_timestamped_text(_artifact("A sentence without a timestamped speaker."))
    unsupported = parse_timestamped_text(
        _artifact(
            "[2026-10-01T10:03:00Z] Alice: text",
            source_type="email",
        )
    )

    assert orphan.status is ParseStatus.FAILED
    assert orphan.segments == ()
    assert orphan.issues[0].reason_code == "SOURCE_PARSE_FAILED"

    assert unsupported.status is ParseStatus.FAILED
    assert unsupported.segments == ()
    assert unsupported.issues[0].reason_code == "SOURCE_PARSE_FAILED"


def test_candidate_evidence_cannot_be_verified_against_another_workspace_or_source_version() -> None:
    content = "[2026-10-01T10:03:00Z] Alice: We decided to use Vendor A."
    parsed = parse_timestamped_text(_artifact(content))
    span = parsed.segments[0].evidence_span

    wrong_workspace = _artifact(content, workspace_id="team-beta")
    changed_version = _artifact(content + " corrected")

    assert not span.verify(wrong_workspace)
    assert not span.verify(changed_version)
