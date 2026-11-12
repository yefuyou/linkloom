"""Deterministic host rules for fact subjects, attribute keys and values.

The model proposes an attribute *name*; only the host derives the key that
decides supersession. When a rule cannot tell two attributes apart the result
is "do not merge" (``mergeable=False``), never a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

FIRST_PERSON = frozenset({"i", "me", "my", "mine", "myself", "我", "我的", "本人", "自己"})
# Pronouns whose referent the host cannot recover from one message.
OTHER_PRONOUNS = frozenset({
    "you", "your", "yours", "yourself", "he", "him", "his", "she", "her", "hers", "they", "them",
    "their", "theirs", "we", "us", "our", "ours", "it", "its", "this", "that",
    "你", "你的", "您", "他", "她", "他们", "她们", "我们", "咱们", "它", "这", "那",
})
_ARTICLES = ("the ", "a ", "an ")
_POSSESSIVE_DETERMINERS = frozenset({"my", "your", "his", "her", "their", "our", "its"})
_LEADING_DROP = frozenset({"the", "a", "an", "current", "currently", "present"})
# Spelling variants and abbreviations only. Different words with similar
# meaning (job/occupation, home/address) are deliberately NOT merged.
_SPELLING = {
    "favourite": "favorite", "fav": "favorite", "colour": "color", "flavour": "flavor",
    "neighbour": "neighbor", "neighbourhood": "neighborhood", "organisation": "organization",
    "programme": "program", "centre": "center", "theatre": "theater", "travelling": "traveling",
    "num": "number", "no.": "number", "qty": "quantity", "pb": "personal best",
    "最爱的": "最喜欢的",
}
# Tokens that carry no attribute identity on their own. A key made only of
# these ("has", "is", "likes", "owns") would merge unrelated facts.
_GENERIC = frozenset({
    "is", "are", "was", "were", "be", "been", "being", "am", "has", "have", "had", "having",
    "own", "owns", "owned", "got", "get", "gets", "getting", "bought", "buy", "buys", "like",
    "likes", "liked", "love", "loves", "loved", "did", "do", "does", "doing", "made", "make",
    "makes", "went", "go", "goes", "use", "uses", "used", "want", "wants", "wanted", "need",
    "needs", "said", "says", "say", "mentioned", "mention", "status", "value", "info",
    "information", "detail", "details", "fact", "facts", "thing", "things", "item", "items",
    "attribute", "property", "statement", "about", "of", "to", "in", "on", "at", "for", "with",
    "and", "or", "by", "from", "as", "not", "no", "new", "now", "also", "just", "still",
    "是", "有", "喜欢", "拥有", "想", "要", "说", "的", "了", "在",
})
_WORD_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "dozen": "12", "a dozen": "12",
}
_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", "−": "-", " ": " "})
_EDGE_PUNCT = " \t\r\n\"'`.,;:!?()[]{}<>«»“”‘’。，；：！？（）【】"


def fold(text: str) -> str:
    """Case, width, quote/dash and whitespace folding shared by every rule."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_FOLD).casefold().split())


def _strip_article(text: str) -> str:
    for article in _ARTICLES:
        if text.startswith(article):
            return text[len(article):]
    return text


@dataclass(frozen=True, slots=True)
class SubjectResolution:
    subject_id: str | None
    label: str | None
    basis: str | None  # FIRST_PERSON | SPEAKER_NAME | SOURCE_MENTION
    reason: str | None


def resolve_subject(subject: str | None, quote: str, speaker: str | None) -> SubjectResolution:
    """Land a subject on an entity without asking the text to repeat it.

    First-person words and the speaker's own label map to the speaker of the
    message. Other pronouns are refused. Anything else must be a mention in
    the quoted message itself.
    """
    if subject is None or not fold(subject):
        return SubjectResolution(None, None, None, "SUBJECT_MISSING")
    folded = _strip_article(fold(subject))
    speaker_folded = fold(speaker) if speaker else None
    if folded in FIRST_PERSON or (speaker_folded and folded == speaker_folded):
        if not speaker_folded:
            return SubjectResolution(None, None, None, "SPEAKER_UNAVAILABLE")
        return SubjectResolution("speaker:" + speaker_folded, speaker.strip(),
                                 "FIRST_PERSON" if folded in FIRST_PERSON else "SPEAKER_NAME", None)
    if folded in OTHER_PRONOUNS:
        return SubjectResolution(None, None, None, "SUBJECT_PRONOUN_UNRESOLVED")
    if folded and locate_value(folded, quote) is not None:
        return SubjectResolution("mention:" + folded, subject.strip(), "SOURCE_MENTION", None)
    return SubjectResolution(None, None, None, "SUBJECT_UNGROUNDED")


def _singular(token: str) -> str:
    if len(token) <= 3 or not token.isascii():
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


@dataclass(frozen=True, slots=True)
class AttributeKey:
    key: str
    mergeable: bool


def attribute_key(name: str | None, *, subject_words: tuple[str, ...] = ()) -> AttributeKey:
    """Normalize a model-given attribute name into the host's key.

    Steps: NFKC + casefold; drop a leading possessive only when it names the
    fact's own subject ("my", "user's" for the speaker "user"); split on
    ``_ - /`` and drop other punctuation; drop a leading article or
    "current"; map spelling variants; singularize the last token (and map
    its spelling again). A key
    whose tokens are all generic verbs/fillers is not mergeable.
    """
    if name is None:
        return AttributeKey("", False)
    text = fold(name).replace("e-mail", "email")
    own = {fold(word) for word in subject_words if word} | FIRST_PERSON
    possessive = re.match(r"^([\w.]+)(?:'s|')\s+", text)
    if possessive and fold(possessive.group(1)) in own:
        text = text[possessive.end():]
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"[^\w\s.']|(?<!\w)['.]|['.](?!\w)", " ", text)
    tokens = text.split()
    while tokens and (tokens[0] in _LEADING_DROP or tokens[0] in _POSSESSIVE_DETERMINERS & own):
        tokens.pop(0)
    tokens = " ".join(_SPELLING.get(token, token) for token in tokens).split()
    if tokens:
        tokens[-1] = _SPELLING.get(_singular(tokens[-1]), _singular(tokens[-1]))
    key = " ".join(tokens)
    mergeable = any(token not in _GENERIC and not token.isdigit() for token in tokens)
    return AttributeKey(key, mergeable)


def normalize_value(value: str) -> str:
    """Display/comparison form only; evidence always uses the source span."""
    text = fold(value).strip(_EDGE_PUNCT)
    for word, digit in sorted(_WORD_NUMBERS.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"\b{re.escape(word)}\b", digit, text)
    return " ".join(text.split())


def value_kind(normalized: str) -> str:
    if re.fullmatch(r"\d{1,3}(?::\d{2}){1,2}(?:\s*(?:am|pm))?", normalized):
        return "TIME"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return "DATE"
    if re.match(r"^(?:[$€£¥]\s*\d|\d[\d,.]*\s*(?:usd|dollars?|eur|euros?|gbp|yuan|rmb|元)\b)", normalized):
        return "MONEY"
    if re.match(r"^-?\d[\d,.]*\b", normalized):
        return "NUMBER"
    return "TEXT"


def _word(character: str) -> bool:
    return character.isascii() and character.isalnum()


def locate_value(value: str | None, quote: str) -> tuple[int, int] | None:
    """Find the value as an exact span of the quote, tolerating only folding.

    Case, width, curly quotes/dashes, repeated whitespace and punctuation at
    the value's edges are ignored; every other character must match. The
    returned offsets address the original quote, so the stored evidence text
    is a verbatim source fragment.
    """
    if value is None:
        return None
    target = fold(value).strip(_EDGE_PUNCT)
    if not target:
        return None
    chars: list[str] = []
    origin: list[int] = []
    previous_space = True
    for index, character in enumerate(quote):
        for piece in fold(character) if not character.isspace() else " ":
            if piece == " ":
                if previous_space:
                    continue
                previous_space = True
            else:
                previous_space = False
            chars.append(piece)
            origin.append(index)
    folded_quote = "".join(chars)
    start = folded_quote.find(target)
    while start >= 0:
        end = start + len(target)
        # A value never starts or ends inside a word or number ("5" is not in "25").
        # (CJK text has no word spaces, so the rule applies to ASCII letters/digits only.)
        clean_left = start == 0 or not (_word(folded_quote[start - 1]) and _word(target[0]))
        clean_right = end == len(folded_quote) or not (_word(folded_quote[end]) and _word(target[-1]))
        if clean_left and clean_right:
            return origin[start], origin[end - 1] + 1
        start = folded_quote.find(target, start + 1)
    return None
