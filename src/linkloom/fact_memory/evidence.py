"""Fact value evidence is a located source span inside one complete sentence."""
from __future__ import annotations

from dataclasses import dataclass
import re

from linkloom.fact_memory.normalize import fold, locate_value

_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "e.g", "i.e", "approx", "no"})


@dataclass(frozen=True, slots=True)
class ValueEvidence:
    keys: tuple[str, ...]
    sentence: str | None
    start: int | None
    end: int | None
    reason: str | None


def sentences(quote: str) -> list[tuple[int, int]]:
    """Sentence spans (start, end) of the quote, trimmed of outer whitespace."""
    spans, start = [], 0
    for match in re.finditer(r"[.!?。！？]+(?=\s|$)|[。！？]+|\n+", quote):
        end = match.end()
        word = re.search(r"([\w.]+)\.$", quote[start:end])
        if match.group().startswith(".") and word and fold(word.group(1)) in _ABBREVIATIONS:
            continue
        spans.append((start, end))
        start = end
    spans.append((start, len(quote)))
    trimmed = []
    for left, right in spans:
        text = quote[left:right]
        lead = len(text) - len(text.lstrip())
        tail = len(text.rstrip())
        if text.strip():
            trimmed.append((left + lead, left + tail))
    return trimmed


def verify_value(value: str | None, quote: str) -> ValueEvidence:
    """Require a literal value span within one source sentence, using existing folding."""
    if value is None or not fold(value):
        return ValueEvidence((), None, None, None, "FACT_VALUE_MISSING")
    for start, end in sentences(quote):
        if locate_value(value, quote[start:end]) is not None:
            return ValueEvidence((), quote[start:end], start, end, None)
    return ValueEvidence((), None, None, None, "FACT_VALUE_NOT_IN_SOURCE")
