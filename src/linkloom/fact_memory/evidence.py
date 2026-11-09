"""Value evidence: the full source sentence plus key-content verification.

The stored evidence is the whole sentence of the message that states the fact,
copied verbatim from the verified quote. The model's value may be a rewording,
but every piece of *key content* in it (numbers, dates, clock times/durations,
proper nouns) must appear in that one sentence after number and date
normalization. A value with no key content cannot be checked and is refused.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from linkloom.fact_memory.normalize import fold

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_COUNTS = {"once": 1, "twice": 2, "thrice": 3, "dozen": 12, "a dozen": 12, "half a dozen": 6}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
_MONTHS = {name: index for index, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
    ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
    ("november", "nov"), ("december", "dec")), start=1) for name in names}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s*(\d{4}))?"
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("year", "month", "day")),
    (re.compile(rf"\b({_MONTH})\.?\s+{_DAY}\b{_YEAR}"), ("month_name", "day", "year")),
    (re.compile(rf"\b{_DAY}\s+(?:of\s+)?({_MONTH})\b\.?{_YEAR}"), ("day", "month_name", "year")),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b"), ("month", "day", "year")),
    (re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"), ("year", "month", "day")),
    (re.compile(r"(\d{1,2})月(\d{1,2})日"), ("month", "day")),
)
_TIME = re.compile(r"\b\d{1,3}(?::\d{2}){1,2}\b")
# Capitalized words that are not names by themselves.
_NOT_NAMES = frozenset("""i a an the my our your his her their its this that these those in on at for of to and or but
with from by as is are was were be it we you he she they me us them there here when what which who how
mr mrs ms dr st""".split())
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


def _numberize(text: str) -> str:
    """Fold, then rewrite number words ("twenty-five", "a dozen", "twice") as digits."""
    text = fold(text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    for phrase, number in sorted(_COUNTS.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"\b{phrase}\b", str(number), text)
    tens = "|".join(_TENS)
    units = "|".join(_UNITS)
    def compound(match: re.Match) -> str:
        value = _TENS[match.group(1)] + (_UNITS[match.group(2)] if match.group(2) else 0)
        return str(value)
    text = re.sub(rf"\b({tens})(?:[\s-]({units}))?\b", compound, text)
    text = re.sub(rf"\b({units})\b", lambda match: str(_UNITS[match.group(1)]), text)
    def scaled(match: re.Match) -> str:
        return str(int(float(match.group(1)) * _SCALES[match.group(2)]))
    return re.sub(r"\b(\d+(?:\.\d+)?)\s+(hundred|thousand|million)\b", scaled, text)


def _canonical_number(raw: str) -> str:
    number = float(raw)
    return str(int(number)) if number.is_integer() else repr(number)


def _extract(text: str) -> tuple[set[str], str]:
    """Return date/time/number keys of numberized text and the text with those spans blanked."""
    keys: set[str] = set()
    work = _numberize(text)
    for pattern, roles in _DATE_PATTERNS:
        def date_key(match: re.Match) -> str:
            parts = dict(zip(roles, match.groups()))
            month = _MONTHS[parts["month_name"]] if "month_name" in parts else int(parts["month"])
            day, year = int(parts["day"]), parts.get("year")
            if 1 <= month <= 12 and 1 <= day <= 31:
                keys.add(f"date:{month:02d}-{day:02d}" + (f"-{year}" if year else ""))
                return " " * len(match.group())
            return match.group()
        work = pattern.sub(date_key, work)
    for match in _TIME.finditer(work):
        keys.add("time:" + ":".join(str(int(part)) for part in match.group().split(":")))
    work = _TIME.sub(lambda match: " " * len(match.group()), work)
    # A unit glued to the number stays part of it: "5k" is not the number 5.
    for match in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)([a-z]*)", work):
        keys.add("num:" + _canonical_number(match.group(1)) + match.group(2))
    return keys, work


def _names(text: str) -> list[tuple[str, bool]]:
    """Capitalized words as (folded word, starts a sentence or clause)."""
    original = unicodedata.normalize("NFKC", text)
    names = []
    for match in re.finditer(r"[A-Z][\w'’&-]*", original):
        word = fold(match.group()).strip("'-&")
        # Number words and month names are already covered by number/date keys.
        if (word and word not in _NOT_NAMES and word not in _MONTHS
                and not re.fullmatch(r"[\d\s-]+", _numberize(word))):
            before = original[:match.start()].rstrip(" \t\"'“‘(")
            after_abbreviation = re.search(r"([\w.]+)\.$", before)
            initial = not before or (before[-1] in ".!?:;\n" and not (
                after_abbreviation and fold(after_abbreviation.group(1)) in _ABBREVIATIONS))
            names.append((word, initial))
    return names


def key_content(value: str) -> tuple[str, ...]:
    """Key content of a fact value: numbers, dates, times/durations, proper nouns.

    A capitalized word that opens the value or one of its clauses ("Pros: ...")
    is only capitalized by position, so it is not a name here; see
    ``verify_value`` for when the source sentence confirms it as one.
    """
    keys, _ = _extract(value)
    keys.update("name:" + word for word, initial in _names(value) if not initial)
    return tuple(sorted(keys))


def _confirmed_names(value: str, sentence: str) -> set[str]:
    """Position-capitalized value words that the sentence capitalizes mid-clause."""
    inside = {word for word, initial in _names(sentence) if not initial}
    return {"name:" + word for word, initial in _names(value) if initial and word in inside}


def sentence_keys(sentence: str) -> set[str]:
    keys, _ = _extract(sentence)
    keys.update("name:" + word.strip("'-&") for word in re.findall(r"[\w'&-]+", fold(sentence)))
    return keys


def _matches(key: str, available: set[str]) -> bool:
    if key in available:
        return True
    if key.startswith("date:") and key.count("-") == 1:  # month-day without year
        return any(item.startswith(key + "-") for item in available)
    return False


def verify_value(value: str | None, quote: str) -> ValueEvidence:
    """Pick the one sentence of ``quote`` that carries every key of ``value``."""
    if value is None or not fold(value):
        return ValueEvidence((), None, None, None, "FACT_VALUE_MISSING")
    base = set(key_content(value))
    checked = False
    for start, end in sentences(quote):
        sentence = quote[start:end]
        available = sentence_keys(sentence)
        # An opening capitalized word never makes a value verifiable on its own,
        # but it must still occur in the sentence ("Sunrise Bakery" needs "sunrise").
        opening = {"name:" + word for word, initial in _names(value) if initial}
        if not all(item in available for item in opening):
            checked = checked or bool(base)
            continue
        keys = base | _confirmed_names(value, sentence)
        if not keys:
            continue
        checked = True
        if all(_matches(key, available) for key in keys):
            return ValueEvidence(tuple(sorted(keys)), sentence, start, end, None)
    if not base and not checked:
        return ValueEvidence((), None, None, None, "FACT_VALUE_UNVERIFIABLE")
    return ValueEvidence(tuple(sorted(base)), None, None, None, "FACT_VALUE_UNGROUNDED")
