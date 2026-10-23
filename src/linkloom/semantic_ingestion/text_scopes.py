"""Structural quote/code ranges, with original Unicode offsets preserved."""
import re
import unicodedata

from linkloom.semantic_ingestion.timestamped_text import timestamped_message_body


_SPOKEN_APOSTROPHE_WORDS = frozenset({
    "bout", "cause", "cept", "cross", "em", "fore", "gain", "kay", "mid",
    "mongst", "nother", "round", "scuse", "til", "tis", "twas", "twere",
    "twill", "un", "uns",
    "cos", "cuz", "ere", "gainst", "neath", "nuff", "twould", "twixt",
})
_SYMMETRIC_QUOTES = frozenset({'"', "'"})
_PAIRS = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"', "'": "'"}
_CONTRACTION_SUFFIXES = frozenset({'s', 't', 'd', 'm', 're', 've', 'll'})
_CURLY_ELISIONS = frozenset({('y', 'all'), ('o', 'clock'), ('rock', 'n'), ('n', 'roll')})


def _word_char(char):
    """Latin words and combining marks; never join English to adjacent Han."""
    return bool(char) and (char.isdecimal() or 'LATIN' in unicodedata.name(char, '')
                           or unicodedata.category(char).startswith('M'))


def _curly_contraction(text, index):
    end = index + 1
    while end < len(text) and _word_char(text[end]):
        end += 1
    suffix = text[index + 1:end].casefold()
    if suffix in _CONTRACTION_SUFFIXES:
        return True
    start = index - 1
    while start >= 0 and _word_char(text[start]):
        start -= 1
    return (text[start + 1:index].casefold(), suffix) in _CURLY_ELISIONS


def _spoken_elision(text, index, body_starts):
    """Recognise a leading apostrophe in a common spoken-word elision."""
    if text[index] != "'":
        return False
    end = index + 1
    while end < len(text) and text[end].isalpha():
        end += 1
    if text[index + 1:end].casefold() not in _SPOKEN_APOSTROPHE_WORDS:
        return False
    previous = index - 1
    while previous >= 0 and text[previous].isspace():
        previous -= 1
    # Reuse the canonical timestamp parser: a speaker header is metadata,
    # whereas an explicit quotation introducer protects even an unclosed quote.
    if previous >= 0 and text[previous] in ':：' and index not in body_starts:
        return False
    return not text[end:end + 1].isalnum() and text[end:end + 1] != '_'


def _apostrophe_is_literal(text, index, stack, elision):
    """Keep contractions/possessives out of the structural quote stack."""
    previous = text[index - 1] if index else ""
    following = text[index + 1:index + 2]
    previous_word = _word_char(previous)
    following_word = _word_char(following)
    if previous_word and following_word:
        return True
    if elision:
        return True
    # Outside a single-quoted range, a trailing apostrophe is a plural
    # possessive. Inside a different quote, the same rule avoids changing its
    # scope while still allowing a real nested quote after whitespace.
    return previous_word and (not stack or stack[-1][1] != "'")


def _symmetric_quote_opens(text, index, delimiter):
    """Disambiguate nested same-delimiter opens from their closing marks."""
    previous = text[index - 1] if index else ""
    following = text[index + 1:index + 2]
    if previous == delimiter:
        return False
    if previous and (previous.isalnum() or previous in ")]}>”’」』"):
        return False
    if not following or following.isspace() or following in ".,;:!?)]}>\"'”’」』。！？，；：、":
        return False
    return True


def _quote_marks(text, code_offsets):
    body_starts, cursor = set(), 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip('\r\n')
        body = timestamped_message_body(stripped)
        if body is not None:
            body_starts.add(cursor + len(stripped.rstrip()) - len(body))
        cursor += len(line)
    marks = []
    backslashes = 0
    for i, char in enumerate(text):
        escaped = backslashes % 2
        backslashes = backslashes + 1 if char == '\\' else 0
        if i in code_offsets or escaped or char not in _PAIRS and char not in _PAIRS.values():
            continue
        before, after = text[i-1:i] if i else '', text[i+1:i+2]
        if _word_char(before) and _word_char(after) and (
                char == "'" or char == '’' and _curly_contraction(text, i)):
            continue
        opens = char in _PAIRS and (char not in _SYMMETRIC_QUOTES
                                    or _symmetric_quote_opens(text, i, char))
        closes = char in _PAIRS.values() and (char not in _SYMMETRIC_QUOTES or not opens)
        marks.append((i, char, opens, closes, _spoken_elision(text, i, body_starts)))
    return marks


def _paired_elisions(marks):
    """Reuse #18's bounded suffix lookahead for elisions, with #20's mark rules.

    A closer inside a nested span cannot pair an outer elision. Six fixed
    closer states avoid repeated forward scans and preserve multiline quotes.
    The stack below remains the single owner of emitted quote ranges.
    """
    suffix = {closer: [None] * (len(marks) + 1) for closer in _PAIRS.values()}
    paired = set()
    for index in range(len(marks) - 1, -1, -1):
        position, char, opens, closes, elision = marks[index]
        end = suffix[_PAIRS[char]][index + 1] if opens else None
        if elision and end is not None:
            paired.add(position)
        for closer, matches in suffix.items():
            if char == closer and closes:
                matches[index] = index
            elif elision and closer == "'":
                matches[index] = matches[index + 1]
            elif opens and end is not None and end >= 0:
                matches[index] = matches[end + 1]
            elif not opens or elision and end is None:
                matches[index] = matches[index + 1]
            else:
                matches[index] = -1  # malformed nested quotation stays protected
    return paired


def _plural_possessives(text, marks):
    """Keep a plural possessive inside a quote when a later closer exists.

    The spelling is ambiguous with a quote ending in s. Prefer the longer
    protected scope in that case; never release the remainder of a quotation.
    """
    next_closer, possessives = {}, set()
    for index, char, _, closes, _ in reversed(marks):
        if char not in {"'", '’'}:
            continue
        following = index + 1
        while following < len(text) and text[following].isspace():
            following += 1
        if (char in next_closer and text[index-1:index].casefold() == 's'
                and following > index + 1 and text[following:following+1].isalpha()):
            possessives.add(index)
        if closes:
            next_closer[char] = index
    return possessives


def quoted_ranges(text):
    ranges = [(m.start(),m.end()) for m in re.finditer(
        r'(?m)^[ \t]*>[^\n]*|```[\s\S]*?(?:```|\Z)|`[^`\n]*(?:`|$)',text)]
    code_offsets = {i for left,right in ranges for i in range(left,right)}
    stack = []
    marks = _quote_marks(text, code_offsets)
    paired_elisions = _paired_elisions(marks)
    plural_possessives = _plural_possessives(text, marks)
    for i, char, opens, closes, elision in marks:
        if i in plural_possessives and stack and stack[-1][1] == char:
            continue
        if char in _SYMMETRIC_QUOTES:
            if char == "'" and _apostrophe_is_literal(
                    text, i, stack, elision and i not in paired_elisions):
                continue
            if (stack and stack[-1][1] == char
                    and closes):
                start, _ = stack.pop()
                ranges.append((start, i + 1))
            else:
                stack.append((i, char))
            continue
        closing = bool(stack and char == stack[-1][1])
        if closing:
            start,_ = stack.pop()
            ranges.append((start,i+1))
        elif char in _PAIRS:
            stack.append((i,_PAIRS[char]))
    ranges.extend((start,len(text)) for start,_ in stack)
    return ranges


def unquoted_text(text):
    visible = list(text)
    for left,right in quoted_ranges(text):
        visible[left:right] = ['\n' if c=='\n' else ' ' for c in text[left:right]]
    return ''.join(visible)
