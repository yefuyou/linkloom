"""Structural quote/code ranges, with original Unicode offsets preserved."""
import re


_SPOKEN_APOSTROPHE_WORDS = frozenset({
    "bout", "cause", "cept", "cross", "em", "fore", "gain", "kay", "mid",
    "mongst", "nother", "round", "scuse", "til", "tis", "twas", "twere",
    "twill", "un", "uns",
})
_SYMMETRIC_QUOTES = frozenset({'"', "'"})


def _escaped(text, index):
    """Return whether the delimiter is escaped by an odd backslash run."""
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return bool(backslashes % 2)


def _spoken_elision(text, index):
    """Recognise a leading apostrophe in a common spoken-word elision."""
    if text[index] != "'":
        return False
    end = index + 1
    while end < len(text) and text[end].isalpha():
        end += 1
    if text[index + 1:end].casefold() not in _SPOKEN_APOSTROPHE_WORDS:
        return False
    # Prefer a complete same-line quoted passage over the elision reading.
    # Apostrophes inside contractions are not closing quote evidence. AMI
    # utterances have native line boundaries; an elision never spans turns.
    line_end = text.find('\n', end)
    limit = len(text) if line_end < 0 else line_end
    if any(text[pos] == "'" and not _escaped(text, pos)
           and not text[pos + 1:pos + 2].isalnum() for pos in range(end, limit)):
        return False
    return end == len(text) or not text[end].isalnum()


def _apostrophe_is_literal(text, index, stack):
    """Keep contractions/possessives out of the structural quote stack."""
    previous = text[index - 1] if index else ""
    following = text[index + 1:index + 2]
    previous_word = bool(previous and previous.isalnum())
    following_word = bool(following and following.isalnum())
    if previous_word and following_word:
        return True
    if _spoken_elision(text, index):
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
    if not following or following.isspace() or following in ".,;:!?)]}>”’」』。！？，；：、":
        return False
    return True


def quoted_ranges(text):
    ranges = [(m.start(),m.end()) for m in re.finditer(
        r'(?m)^[ \t]*>[^\n]*|```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)',text)]
    code_offsets = {i for left,right in ranges for i in range(left,right)}
    stack = []
    pairs = {'“':'”','‘':'’','「':'」','『':'』'}
    for i,char in enumerate(text):
        if i in code_offsets:
            continue
        if _escaped(text, i):
            continue
        if char in _SYMMETRIC_QUOTES:
            if char == "'" and _apostrophe_is_literal(text, i, stack):
                continue
            if (stack and stack[-1][1] == char
                    and not _symmetric_quote_opens(text, i, char)):
                start, _ = stack.pop()
                ranges.append((start, i + 1))
            else:
                stack.append((i, char))
            continue
        closing = bool(stack and char == stack[-1][1])
        if closing:
            start,_ = stack.pop()
            ranges.append((start,i+1))
        elif char in pairs:
            stack.append((i,pairs[char]))
    ranges.extend((start,len(text)) for start,_ in stack)
    return ranges


def unquoted_text(text):
    visible = list(text)
    for left,right in quoted_ranges(text):
        visible[left:right] = ['\n' if c=='\n' else ' ' for c in text[left:right]]
    return ''.join(visible)
