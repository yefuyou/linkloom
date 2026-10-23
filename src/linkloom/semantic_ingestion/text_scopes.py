"""Structural quote/code ranges, with original Unicode offsets preserved."""
import re
import unicodedata

from linkloom.semantic_ingestion.timestamped_text import timestamped_message_body


# English leading elisions are lexical apostrophes, not quotation intent.
# This is a general vocabulary of shortened words, never a task/meeting rule.
# A matching quote still takes precedence, including when the word is elided.
_LEADING_ELISIONS = frozenset({
    'bout', 'cause', 'cos', 'cuz', 'em', 'ere', 'gainst', 'kay', 'neath',
    'nuff', 'round', 'scuse', 'til', 'tis', 'twas', 'twere', 'twill',
    'twould', 'twixt',
})
_PAIRS = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"', "'": "'"}
_CONTRACTION_SUFFIXES = frozenset({'s', 't', 'd', 'm', 're', 've', 'll'})
_BROKEN_NESTING = -1


def _leading_elision(text, start):
    end = start + 1
    while end < len(text) and text[end].isalpha():
        end += 1
    return (text[start + 1:end].casefold() in _LEADING_ELISIONS
            and not text[end:end + 1].isalnum() and text[end:end + 1] != '_')


def _apostrophe_word_char(char):
    # English apostrophe rules must not join Latin text to adjacent Han prose.
    # Combining marks count without normalizing/changing the original offsets.
    return bool(char) and (char.isdecimal() or 'LATIN' in unicodedata.name(char, '')
                           or unicodedata.category(char).startswith('M'))


def _curly_contraction(text, start):
    end = start + 1
    while end < len(text) and _apostrophe_word_char(text[end]):
        end += 1
    return text[start+1:end].casefold() in _CONTRACTION_SUFFIXES


def _quote_tokens(text, code_offsets):
    body_starts, cursor = set(), 0
    for line in text.splitlines(keepends=True):
        body = timestamped_message_body(line.rstrip('\r\n'))
        if body is not None:
            body_starts.add(cursor + len(line.rstrip()) - len(body))
        cursor += len(line)
    tokens = []
    for i, char in enumerate(text):
        if i in code_offsets or char not in _PAIRS and char not in _PAIRS.values():
            continue
        before, after = text[i-1:i] if i else '', text[i+1:i+2]
        before_word, after_word = _apostrophe_word_char(before), _apostrophe_word_char(after)
        if before_word and after_word and (char == "'" or char == '’' and _curly_contraction(text, i)):
            continue  # A contraction is not a delimiter, even inside a quote.
        opens = char in _PAIRS and not (char == "'" and before_word)
        closes = not (char == "'" and after_word)
        previous = i - 1
        while previous >= 0 and text[previous].isspace():
            previous -= 1
        # A validated source-message header is metadata, not quoted speech.
        # Unrecognized document labels remain conservative quotation cues.
        introduced = previous >= 0 and text[previous] in ':：' and i not in body_starts
        elision = char == "'" and _leading_elision(text, i) and not introduced
        tokens.append((i, char, opens, closes, elision))
    return tokens


def _matching_ends(tokens):
    # Resolve each possible opener at its own nesting level. An inner quote's
    # entire span is skipped, so its apostrophes cannot close an outer quote.
    # Suffix tables avoid recursion/backtracking: six closer states per token.
    suffix = {closer: [None] * (len(tokens) + 1) for closer in _PAIRS.values()}
    ends = [None] * len(tokens)
    for i in range(len(tokens) - 1, -1, -1):
        _, char, opens, closes, elision = tokens[i]
        end = suffix[_PAIRS[char]][i+1] if opens else None
        ends[i] = end
        for closer, matches in suffix.items():
            if char == closer and closes:
                matches[i] = i
            elif elision and closer == "'":
                matches[i] = matches[i+1]  # e.g. 'em within a single quotation
            elif opens and end is not None and end >= 0:
                matches[i] = matches[end+1]
            elif not opens or elision and end is None:
                matches[i] = matches[i+1]
            else:
                # Distinguish malformed inner quotation from an ordinary word
                # elision with no closer. The former must remain protected.
                matches[i] = _BROKEN_NESTING
    return ends


def quoted_ranges(text):
    ranges = [(m.start(),m.end()) for m in re.finditer(
        r'(?m)^[ \t]*>[^\n]*|```[\s\S]*?(?:```|\Z)|`[^`\n]*(?:`|$)',text)]
    code_offsets = {i for left,right in ranges for i in range(left,right)}
    tokens = _quote_tokens(text, code_offsets)
    ends = _matching_ends(tokens)
    stack = []
    for index, (i, char, opens, closes, elision) in enumerate(tokens):
        if stack and char == stack[-1][1] and closes:
            start,_ = stack.pop()
            ranges.append((start,i+1))
        elif opens:
            if elision and (ends[index] is None or stack and stack[-1][1] == "'"):
                continue
            stack.append((i, _PAIRS[char]))
    ranges.extend((start,len(text)) for start,_ in stack)
    return ranges


def unquoted_text(text):
    visible = list(text)
    for left,right in quoted_ranges(text):
        visible[left:right] = ['\n' if c=='\n' else ' ' for c in text[left:right]]
    return ''.join(visible)
