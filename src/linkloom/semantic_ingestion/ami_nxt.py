"""Faithful, read-only adapter for the transcript part of an AMI NXT archive.

The adapter deliberately has a small source boundary.  It reads meeting
metadata, transcript word members, and dialogue-act child references only.
Summary, extractive, action, decision, and Gold members are never opened by
this module.  All offsets are Python Unicode code-point offsets into the
versioned canonical representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path, PurePosixPath
import re
from collections.abc import Sequence
import xml.etree.ElementTree as ET
import zipfile


NITE_URI = "http://nite.sourceforge.net/"
NITE_ID = f"{{{NITE_URI}}}id"
NITE_CHILD = f"{{{NITE_URI}}}child"
FORMAT_VERSION = "ami-nxt/v1"
_METADATA_MEMBER = "corpusResources/meetings.xml"
_HREF_RE = re.compile(r"([^#]+)#id\(([^()]+)\)(?:\.\.id\(([^()]+)\))?$")
_XML_DECLARATION_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_XML_DECLARATION_TEXT_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _finite_decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _time_for_event(event: dict) -> Decimal | None:
    return _finite_decimal(event.get("audio_start_seconds"))


def _tagged_time(events: Sequence[dict], *, end: bool) -> tuple[str | None, Decimal | None]:
    """Return only the requested boundary; never infer one from the other."""
    values: list[tuple[Decimal, str]] = []
    key = "audio_end_seconds" if end else "audio_start_seconds"
    for event in events:
        raw = event.get(key)
        parsed = _finite_decimal(raw)
        if raw is not None and parsed is not None:
            values.append((parsed, raw))
    if not values:
        return None, None
    selected = max(values, key=lambda item: item[0]) if end else min(values, key=lambda item: item[0])
    return selected[1], selected[0]


def _event_sort_time(events: Sequence[dict]) -> Decimal | None:
    values: list[Decimal] = []
    for event in events:
        for key in ("audio_start_seconds", "audio_end_seconds"):
            parsed = _finite_decimal(event.get(key))
            if parsed is not None:
                values.append(parsed)
    return min(values) if values else None


def _unsafe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or "\\" in name


def _contains_xml_declaration(data: bytes) -> bool:
    if _XML_DECLARATION_RE.search(data):
        return True
    # The byte spelling is interleaved for UTF-16/32.  Decode a few standard
    # XML encodings solely for this declaration check; ElementTree still gets
    # the original bytes below so it remains the source of text decoding.
    for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
        try:
            if _XML_DECLARATION_TEXT_RE.search(data.decode(encoding)):
                return True
        except UnicodeDecodeError:
            continue
    return False


@dataclass(frozen=True)
class _ParsedXml:
    name: str
    digest: str
    root: ET.Element
    elements: tuple[ET.Element, ...]
    positions: dict[str, int]


@dataclass
class _DraftUtterance:
    utterance_id: str
    speaker: str
    text_events: list[dict]
    native_nontext_ids: list[str]
    source_member: str
    member_sha256: str
    original_hrefs: list[str]
    start_seconds: str | None
    end_seconds: str | None
    sort_time: Decimal | None
    kind: str


def _parse_xml_bytes(name: str, data: bytes) -> tuple[ET.Element, tuple[ET.Element, ...], dict[str, int]]:
    if _contains_xml_declaration(data):
        raise ValueError(f"XML entity/DTD declaration is not accepted: {name}")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML in {name}: {exc}") from exc
    elements: list[ET.Element] = []
    positions: dict[str, int] = {}
    for node in root.iter():
        native_id = node.attrib.get(NITE_ID)
        if native_id is None:
            continue
        if native_id in positions:
            raise ValueError(f"duplicate NXT ID {native_id!r} in {name}")
        positions[native_id] = len(elements)
        elements.append(node)
    return root, tuple(elements), positions


def _speaker_from_member(name: str, meeting_id: str, annotation: str) -> str:
    prefix = f"{annotation}/{meeting_id}."
    suffix = f".{annotation}.xml" if annotation == "words" else f".{annotation}.xml"
    # The caller uses the exact AMI member shapes; this fallback keeps the
    # source speaker visible for a small synthetic fixture with unusual names.
    basename = PurePosixPath(name).name
    if basename.startswith(prefix.rsplit("/", 1)[-1]) and basename.endswith(suffix):
        middle = basename[len(meeting_id) + 1 : -len(suffix)]
        if middle:
            return middle.split(".", 1)[0]
    pieces = basename.split(".")
    return pieces[1] if len(pieces) >= 3 else "UNKNOWN"


def _event_common(
    *,
    native_id: str,
    source_member: str,
    member_sha256: str,
    speaker: str,
    node: ET.Element,
    nativeordinal: int,
    text: str,
) -> dict:
    start = node.attrib.get("starttime")
    end = node.attrib.get("endtime")
    return {
        "source_member": source_member,
        "member_sha256": member_sha256,
        "sourcefilehash": member_sha256,
        "speaker": speaker,
        "audio_start_seconds": start if _finite_decimal(start) is not None else None,
        "audio_end_seconds": end if _finite_decimal(end) is not None else None,
        "nativeordinal": nativeordinal,
        "native_ordinal": nativeordinal,
        "source_ordinal": nativeordinal,
        "element_type": _local_name(node.tag),
        "tag": _local_name(node.tag),
        "attributes": dict(node.attrib),
        "text": text,
        "native_id": native_id,
    }


def _load_member(archive: zipfile.ZipFile, name: str, cache: dict[str, _ParsedXml], hashes: dict[str, str]) -> _ParsedXml:
    if _unsafe_member_name(name):
        raise ValueError(f"unsafe archive member path: {name}")
    if name not in cache:
        try:
            data = archive.read(name)
        except KeyError as exc:
            raise ValueError(f"missing archive member: {name}") from exc
        digest = _sha256(data)
        root, elements, positions = _parse_xml_bytes(name, data)
        cache[name] = _ParsedXml(name, digest, root, elements, positions)
        hashes[name] = digest
    return cache[name]


def _resolve_href(
    href: str,
    *,
    word_members: dict[str, _ParsedXml],
    events_by_member_id: dict[tuple[str, str], dict],
) -> list[dict]:
    match = _HREF_RE.fullmatch(href)
    if match is None:
        raise ValueError(f"unsupported or malformed NXT reference: {href!r}")
    basename, first_id, last_id = match.groups()
    if _unsafe_member_name(basename):
        raise ValueError(f"unsafe NXT reference member: {basename!r}")
    candidates: list[str] = []
    # Official NXT child hrefs use a bare source basename.  A path is allowed
    # only when it is the exact whitelisted archive member; this prevents a
    # Gold/summary member with a colliding basename from becoming a source.
    if "/" in basename:
        if basename in word_members:
            candidates = [basename]
    else:
        candidates = [name for name in word_members if PurePosixPath(name).name == basename]
    if len(candidates) != 1:
        raise ValueError(f"reference does not identify one whitelisted word member: {href!r}")
    member = candidates[0]
    parsed = word_members[member]
    if first_id not in parsed.positions or (last_id is not None and last_id not in parsed.positions):
        raise ValueError(f"reference names an unknown NXT ID: {href!r}")
    first = parsed.positions[first_id]
    last = parsed.positions[last_id or first_id]
    if last < first:
        raise ValueError(f"reversed NXT range: {href!r}")
    resolved: list[dict] = []
    for node in parsed.elements[first : last + 1]:
        native_id = node.attrib[NITE_ID]
        event = events_by_member_id.get((member, native_id))
        # NXT range positions also contain root/container IDs.  They are not
        # words/events and must not be guessed from numeric ID suffixes.
        if event is not None:
            resolved.append(event)
    if not resolved:
        raise ValueError(f"reference resolves to no source word or event: {href!r}")
    return resolved


def chunk_text(
    text: str,
    *,
    max_chars: int = 8000,
    max_bytes: int = 16000,
    boundaries: Sequence[int] = (),
) -> list[dict]:
    """Split text without breaking Unicode code points.

    ``boundaries`` contains preferred end offsets (in code points).  A chunk
    ends at the furthest preferred boundary that fits both budgets; a long
    region is split mechanically and remains exactly reversible.
    """

    if not isinstance(text, str):
        raise TypeError("text must be str")
    if (
        isinstance(max_chars, bool)
        or isinstance(max_bytes, bool)
        or not isinstance(max_chars, int)
        or not isinstance(max_bytes, int)
        or max_chars <= 0
        or max_bytes <= 0
    ):
        raise ValueError("chunk budgets must be positive integers")
    normalized: list[int] = []
    previous = -1
    for boundary in boundaries:
        if isinstance(boundary, bool) or not isinstance(boundary, int) or boundary < 0 or boundary > len(text):
            raise ValueError("chunk boundary is outside text")
        if boundary < previous:
            raise ValueError("chunk boundaries must be monotonic")
        if boundary != previous and boundary > 0:
            normalized.append(boundary)
        previous = boundary
    if not text:
        return []
    if max(len(character.encode("utf-8")) for character in text) > max_bytes:
        raise ValueError("max_bytes is too small for a Unicode code point in text")

    chunks: list[dict] = []
    position = 0
    boundary_index = 0
    text_length = len(text)
    while position < text_length:
        char_limit = min(text_length, position + max_chars)
        byte_end = position
        used_bytes = 0
        for index in range(position, char_limit):
            width = len(text[index].encode("utf-8"))
            if index > position and used_bytes + width > max_bytes:
                break
            used_bytes += width
            byte_end = index + 1
        if byte_end <= position:
            byte_end = min(text_length, position + 1)

        while boundary_index < len(normalized) and normalized[boundary_index] <= position:
            boundary_index += 1
        candidate = position
        lookahead = boundary_index
        while lookahead < len(normalized) and normalized[lookahead] <= byte_end:
            candidate = normalized[lookahead]
            lookahead += 1
        end = candidate if candidate > position else byte_end
        piece = text[position:end]
        digest = _sha256(piece.encode("utf-8"))
        chunks.append({
            "ordinal": len(chunks),
            "start": position,
            "end": end,
            "char_start": position,
            "char_end": end,
            "text": piece,
            "sha256": digest,
            "text_sha256": digest,
        })
        position = end
        while boundary_index < len(normalized) and normalized[boundary_index] <= position:
            boundary_index += 1
    return chunks


def _canonical_header(meeting_id: str, draft: _DraftUtterance) -> str:
    start = draft.start_seconds if draft.start_seconds is not None else "UNKNOWN"
    end = draft.end_seconds if draft.end_seconds is not None else "UNKNOWN"
    return (
        f"{FORMAT_VERSION} meeting={meeting_id} speaker={draft.speaker} "
        f"id={draft.utterance_id} start={start} end={end}\n"
    )


def _make_word_span(event: dict, start: int, end: int) -> dict:
    text = event["text"]
    digest = _sha256(text.encode("utf-8"))
    return {
        "native_word_id": event["native_word_id"],
        "start": start,
        "end": end,
        "char_start": start,
        "char_end": end,
        "text": text,
        "hash": digest,
        "text_sha256": digest,
        "sourcefilehash": event["member_sha256"],
        "member_sha256": event["member_sha256"],
        "source_member": event["source_member"],
    }


def adapt_meeting(
    archive_path: Path,
    meeting_id: str,
    *,
    expected_sha256: str | None = None,
) -> dict:
    """Adapt one AMI meeting into deterministic, source-grounded records."""

    archive_path = Path(archive_path)
    archive_bytes = archive_path.read_bytes()
    archive_sha256 = _sha256(archive_bytes)
    if expected_sha256 is not None and archive_sha256.lower() != expected_sha256.lower():
        raise ValueError(f"archive hash mismatch: expected {expected_sha256}, got {archive_sha256}")

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if _METADATA_MEMBER not in names:
            raise ValueError(f"missing archive member: {_METADATA_MEMBER}")

        cache: dict[str, _ParsedXml] = {}
        member_hashes: dict[str, str] = {}
        metadata_xml = _load_member(archive, _METADATA_MEMBER, cache, member_hashes)
        meetings = [
            node
            for node in metadata_xml.root.iter()
            if _local_name(node.tag) == "meeting" and node.attrib.get("observation") == meeting_id
        ]
        if len(meetings) != 1:
            raise ValueError(f"expected one meeting metadata record for {meeting_id!r}, got {len(meetings)}")
        metadata = dict(meetings[0].attrib)
        metadata["timezone"] = "UNKNOWN"
        metadata["event_time"] = None

        word_member_re = re.compile(rf"^words/{re.escape(meeting_id)}\.[^/.]+\.words\.xml$")
        da_member_re = re.compile(rf"^dialogueActs/{re.escape(meeting_id)}\.[^/.]+\.dialog-act\.xml$")
        word_names = sorted(name for name in names if word_member_re.fullmatch(name))
        da_names = sorted(name for name in names if da_member_re.fullmatch(name))
        selected_names = [_METADATA_MEMBER, *word_names, *da_names]
        if any(selected_names.count(name) > 1 for name in set(selected_names)):
            raise ValueError("duplicate whitelisted archive member name")
        word_members = {
            name: _load_member(archive, name, cache, member_hashes)
            for name in word_names
        }
        da_members = {
            name: _load_member(archive, name, cache, member_hashes)
            for name in da_names
        }

        words: list[dict] = []
        nontext_events: list[dict] = []
        events_by_member_id: dict[tuple[str, str], dict] = {}
        for name in word_names:
            parsed = word_members[name]
            speaker = _speaker_from_member(name, meeting_id, "words")
            leaf_ordinal = 0
            for nativeordinal, node in enumerate(parsed.elements):
                native_id = node.attrib[NITE_ID]
                if list(node) or _local_name(node.tag) in {"root", "container"}:
                    continue
                text = "".join(node.itertext())
                common = _event_common(
                    native_id=native_id,
                    source_member=name,
                    member_sha256=parsed.digest,
                    speaker=speaker,
                    node=node,
                    nativeordinal=nativeordinal,
                    text=text,
                )
                common["leaf_ordinal"] = leaf_ordinal
                leaf_ordinal += 1
                if text and not text.isspace():
                    common["native_word_id"] = native_id
                    words.append(common)
                else:
                    common["native_nontext_id"] = native_id
                    nontext_events.append(common)
                events_by_member_id[(name, native_id)] = common

        covered_words: set[tuple[str, str]] = set()
        drafts: list[_DraftUtterance] = []
        da_drafts: list[_DraftUtterance] = []
        for name in da_names:
            parsed = da_members[name]
            speaker = _speaker_from_member(name, meeting_id, "dialog-act")
            for node in parsed.root.iter():
                if _local_name(node.tag) != "dact":
                    continue
                native_id = node.attrib.get(NITE_ID)
                if not native_id:
                    raise ValueError(f"dialogue act without NXT ID in {name}")
                original_hrefs: list[str] = []
                text_events: list[dict] = []
                native_nontext_ids: list[str] = []
                for child in node.findall(NITE_CHILD):
                    href = child.attrib.get("href")
                    if href is None:
                        raise ValueError(f"dialogue act child without href in {name}#{native_id}")
                    original_hrefs.append(href)
                    for event in _resolve_href(
                        href,
                        word_members=word_members,
                        events_by_member_id=events_by_member_id,
                    ):
                        if event["speaker"] != speaker:
                            raise ValueError(
                                f"cross-speaker word reference in {name}#{native_id}: {href!r}"
                            )
                        if "native_word_id" in event:
                            text_events.append(event)
                            covered_words.add((event["source_member"], event["native_word_id"]))
                        else:
                            native_nontext_ids.append(event["native_nontext_id"])
                start_seconds, _ = _tagged_time(text_events + [
                    event for event in nontext_events
                    if event["native_nontext_id"] in native_nontext_ids
                ], end=False)
                end_seconds, _ = _tagged_time(text_events + [
                    event for event in nontext_events
                    if event["native_nontext_id"] in native_nontext_ids
                ], end=True)
                da_drafts.append(_DraftUtterance(
                    utterance_id=native_id,
                    speaker=speaker,
                    text_events=text_events,
                    native_nontext_ids=native_nontext_ids,
                    source_member=name,
                    member_sha256=parsed.digest,
                    original_hrefs=original_hrefs,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    sort_time=_event_sort_time(text_events + [
                        event for event in nontext_events
                        if event["native_nontext_id"] in native_nontext_ids
                    ]),
                    kind="dialogue_act",
                ))

        da_drafts.sort(key=lambda draft: (
            draft.sort_time is None,
            draft.sort_time if draft.sort_time is not None else Decimal(0),
            draft.speaker,
            draft.utterance_id,
        ))
        drafts.extend(da_drafts)

        for event in words:
            key = (event["source_member"], event["native_word_id"])
            if key in covered_words:
                continue
            native_id = event["native_word_id"]
            drafts.append(_DraftUtterance(
                utterance_id=f"unsegmented:{native_id}",
                speaker=event["speaker"],
                text_events=[event],
                native_nontext_ids=[],
                source_member=event["source_member"],
                member_sha256=event["member_sha256"],
                original_hrefs=[f"{event['source_member']}#id({native_id})"],
                start_seconds=event["audio_start_seconds"],
                end_seconds=event["audio_end_seconds"],
                sort_time=_event_sort_time([event]),
                kind="unsegmented",
            ))
        unsegmented_drafts = sorted(
            drafts[len(da_drafts) :],
            key=lambda draft: (
                draft.sort_time is None,
                draft.sort_time if draft.sort_time is not None else Decimal(0),
                draft.speaker,
                draft.utterance_id,
            ),
        )
        drafts = da_drafts + unsegmented_drafts

        canonical_parts: list[str] = []
        utterances: list[dict] = []
        canonical_offset = 0
        for draft in drafts:
            header = _canonical_header(meeting_id, draft)
            canonical_parts.append(header)
            canonical_offset += len(header)
            char_start = canonical_offset
            text = " ".join(event["text"] for event in draft.text_events)
            word_spans: list[dict] = []
            word_offset = char_start
            for index, event in enumerate(draft.text_events):
                if index:
                    word_offset += 1
                word_end = word_offset + len(event["text"])
                word_spans.append(_make_word_span(event, word_offset, word_end))
                word_offset = word_end
            canonical_parts.append(text)
            canonical_offset += len(text)
            char_end = canonical_offset
            canonical_parts.append("\n")
            canonical_offset += 1
            utterances.append({
                "utterance_id": draft.utterance_id,
                "meeting_id": meeting_id,
                "speaker": draft.speaker,
                "text": text,
                "native_word_ids": [event["native_word_id"] for event in draft.text_events],
                "native_nontext_ids": list(draft.native_nontext_ids),
                "source_member": draft.source_member,
                "member_sha256": draft.member_sha256,
                "source_member_sha256": draft.member_sha256,
                "original_hrefs": list(draft.original_hrefs),
                "start_seconds": draft.start_seconds,
                "end_seconds": draft.end_seconds,
                "char_start": char_start,
                "char_end": char_end,
                "canonical_start": char_start - len(header),
                "canonical_end": canonical_offset,
                "word_spans": word_spans,
                "kind": draft.kind,
                "is_unsegmented": draft.kind == "unsegmented",
            })
        canonical_text = "".join(canonical_parts)
        canonical_sha256 = _sha256(canonical_text.encode("utf-8"))

        preferred_boundaries = [utterance["canonical_end"] for utterance in utterances]
        raw_chunks = chunk_text(canonical_text, boundaries=preferred_boundaries)
        chunks: list[dict] = []
        for raw_chunk in raw_chunks:
            chunk_start, chunk_end = raw_chunk["start"], raw_chunk["end"]
            spans: list[dict] = []
            for utterance in utterances:
                utterance_start = utterance["canonical_start"]
                utterance_end = utterance["canonical_end"]
                if utterance_end <= chunk_start or utterance_start >= chunk_end:
                    continue
                span_start = max(chunk_start, utterance_start)
                span_end = min(chunk_end, utterance_end)
                text_start = max(chunk_start, utterance["char_start"])
                text_end = min(chunk_end, utterance["char_end"])
                spans.append({
                    "utterance_id": utterance["utterance_id"],
                    "start": span_start,
                    "end": span_end,
                    "char_start": span_start,
                    "char_end": span_end,
                    "text_start": text_start,
                    "text_end": text_end,
                    "local_start": span_start - chunk_start,
                    "local_end": span_end - chunk_start,
                    "complete": utterance_start >= chunk_start and utterance_end <= chunk_end,
                })
            chunk = dict(raw_chunk)
            chunk["chunk_id"] = f"{meeting_id}:chunk:{raw_chunk['ordinal']}"
            chunk["utterance_spans"] = spans
            chunks.append(chunk)

    members = {name: member_hashes[name] for name in member_hashes}
    word_count = len(words)
    nontext_count = len(nontext_events)
    unsegmented_count = sum(utterance["is_unsegmented"] for utterance in utterances)
    counts = {
        "words": word_count,
        "word_count": word_count,
        "nontext_events": nontext_count,
        "nontext_event_count": nontext_count,
        "dialogue_acts": len(da_drafts),
        "utterances": len(utterances),
        "utterance_count": len(utterances),
        "unsegmented_utterances": unsegmented_count,
        "unsegmented_words": word_count - len(covered_words),
        "covered_words": len(covered_words),
        "chunks": len(chunks),
        "chunk_count": len(chunks),
        "canonical_chars": len(canonical_text),
        "canonical_bytes": len(canonical_text.encode("utf-8")),
        "source_members": len(members),
    }
    return {
        "format_version": FORMAT_VERSION,
        "meeting_id": meeting_id,
        "archive_sha256": archive_sha256,
        "members": members,
        "metadata": metadata,
        "words": words,
        "nontext_events": nontext_events,
        "utterances": utterances,
        "canonical_text": canonical_text,
        "canonical_sha256": canonical_sha256,
        "chunks": chunks,
        "counts": counts,
    }


__all__ = ["adapt_meeting", "chunk_text", "FORMAT_VERSION"]
