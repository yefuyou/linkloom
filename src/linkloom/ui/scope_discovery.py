"""Workspace-filtered decision-slot retrieval and exact valid-time dates."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
from functools import lru_cache
import os
import re
from dataclasses import dataclass, replace
from typing import Any

from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.indexing.bm25 import BM25Index, tokenize_for_index
from linkloom.indexing.models import IndexDocument
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.product import ProductApplicationError, _parse_as_of

# One global policy, independent of domains, workspaces and evaluation cases.
SLOT_MATCH_MIN_SCORE = 0.5
SLOT_MATCH_MIN_MARGIN = 0.15
SLOT_LLM_CANDIDATES = 5
SLOT_CLARIFY_CANDIDATES = 3
SLOT_DENSE_WEIGHT = 0.25
SLOT_DENSE_MIN_SCORE = 0.65
# Smallest two-decimal value above the absent-topic cosine envelope on the
# frozen development set. This is a recall floor, not an automatic-match gate.
SLOT_DENSE_RECALL_MIN_SCORE = 0.46
AUTO_EMBEDDER = object()


@lru_cache(maxsize=1)
def get_slot_embedder() -> Any:
    """Reuse the local multilingual model; missing extra/cache stays lexical."""
    if importlib.util.find_spec("sentence_transformers") is None:
        return None
    try:
        from linkloom.indexing.vector import SentenceTransformerEmbedder
        embedder = SentenceTransformerEmbedder(local_files_only=True)
        embedder.encode([""])  # Resolve cache/load failures once, without network.
        return embedder
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


_RELATION_VERBS = frozenset("uses use has have is are of the a an".split())

_RELATIVE_DATE = re.compile(
    r"(?:上|下|本|这|前|后)(?:个)?(?:月|周|年|季度|星期)|去年|今年|明年|"
    r"昨天|前天|今天|明天|后天|当时|以前|之前|过去|至今|迄今|早上|上午|下午|晚上|点|时|"
    r"\b(last|previous|this|next|past)\s+(month|year|week|quarter)|"
    r"\b(today|yesterday|tomorrow|tonight|ago|then|before|after|during)\b|"
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b|"
    r"\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b|"
    r"\d{4}年|\d{1,2}月|\b\d{4}-\d{2}(?!-\d{2})\b|\b\d{4}\b|\bq[1-4]\b",
    re.IGNORECASE,
)
_VAGUE_RECENCY = re.compile(r"最近|近期|\b(?:recently|lately)\b", re.I)
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", re.IGNORECASE)
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_CHINESE_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_DATE_RANGE = re.compile(
    r"\b(between|since|until)\b|\b(before|after|from)\s+\d|"
    r"\b(to|and|through|until)\s+(now|today|present)\b|"
    r"从.*\d.*(?:到|至)|\d.*(?:到|至).*\d|至今|迄今|到现在|到今天", re.IGNORECASE)



def decision_slot_id(subject: str, relation: str) -> str:
    identity = "\0".join(map(normalize_temporal_component, (subject, relation)))
    return "slot_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _mentions(query: str, phrase: str) -> bool:
    phrase = normalize_temporal_component(phrase)
    if not phrase:
        return False
    if re.search(r"[\u3400-\u9fff]", phrase):
        return phrase in query.casefold()
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", query.casefold()) is not None


def _tokens(text: str) -> set[str]:
    tokens = set(tokenize_for_index(text))
    # Routing indexes add CJK unigrams as well as the shared tokenizer's
    # bigrams. Expanding both documents and questions keeps BM25 symmetric.
    tokens.update(re.findall(r"[\u4e00-\u9fff]", text))
    # Generic plural normalization, with the same transform for every document/query.
    tokens.update(token[:-1] for token in tuple(tokens)
                  if re.fullmatch(r"[a-z]{4,}s", token) and not token.endswith("ss"))
    return tokens


def _relation_phrases(relation: str, resolver: FrozenRelationResolver) -> set[str]:
    phrases = {relation}
    phrases.update(alias for alias, target in resolver.aliases.items()
                   if normalize_temporal_component(target) == normalize_temporal_component(relation))
    phrases.update(token for token in _tokens(relation)
                   if token not in _RELATION_VERBS and (len(token) > 2 or
                       re.fullmatch(r"[\u4e00-\u9fff]", token)))
    return phrases


def _unexplained_terms(query: str, matched_phrases: list[str]) -> list[str]:
    """Compatibility for feedback term validation ONLY; never used for routing."""
    # Reject an isolated question/time operator as a terminology alias. This is
    # not a coverage/stopword filter over a user's natural-language question.
    if re.fullmatch(r"(?:current(?:ly)?|now|what|which|why|when|how|"
                    r"(?:现|当|目)前|现在|为什么|为何|怎么|如何)", query, re.I):
        return []
    return re.findall(r"[a-z][a-z0-9_-]*|[\u3400-\u9fff]+|\d+", query.casefold())


def _without_dates(query: str) -> str:
    return _VAGUE_RECENCY.sub(" ", _RELATIVE_DATE.sub(" ", _CHINESE_DATE.sub(" ", _ISO_DATE.sub(" ",
        _ISO_TIMESTAMP.sub(" ", query))))).strip(" ,，:：")


# Closed grammatical reference forms, not a vocabulary of workspace topics.
_REFERENCE = re.compile(r"\b(?:we|our|ours|us|i|me|my|mine|you|your|yours|they|their|theirs|them|it|its)\b|"
                        r"我们|咱们|我司|你们|他们|她们|它们|本人|本公司|本团队|本项目|咱|我|你|他|她|它", re.I)
_ORG_EN = r"team|group|department|project"
_ORG_SUFFIX = re.compile(r"(?:(?<=\s)|(?<=[_-]))(?i:" + _ORG_EN + r")\b|"
                         r"(?:Team|Group|Department|Project|TEAM|GROUP|DEPARTMENT|PROJECT)\b|团队|部门|项目|组")
_GRAMMAR_TOKEN = re.compile(
    r"(?:what|which|who|why|how|when|where|is|are|was|were|be|been|being|do|does|did|"
    r"has|have|had|can|could|will|would|should|may|the|a|an|this|that|these|those|own|"
    r"as|of|in|on|at|for|to|from|by|with|and|or|about|please|tell|show|compare|explain|summari[sz]e|"
    r"currently|now|already|still|not|recently|lately|" + _ORG_EN + r")", re.I)
_CJK_ENTITY_PREFIX = re.compile(r"^(?:(?:请问|为什么|为何|为啥|怎么会|问题是|现在|当前|目前|最近|关于|来自|针对|对于|向|自己|"
                                r"(?:上|下|本|这|前|后)(?:个)?(?:月|周|年|季度|星期)|今天|昨天|这个|那个|这|那|该|本|在|是|用|由|给|对|和|与|的)|"
                                r"(?:我们|咱们|我司|你们|他们|她们|它们|我|你|他|她|它))+")


def _without_references(text: str) -> str:
    return _REFERENCE.sub(" ", text)


def _deictic(entity: str) -> bool:
    value = entity.strip()
    if re.fullmatch(r"(?:this|that)(?: project)?|(?:这|那|该|本)(?:个)?项目", value, re.I):
        return True
    # Pronouns may include a possessive marker and a generic organization noun.
    rest = _without_references(value).strip()
    return bool(_REFERENCE.search(value)) and bool(re.fullmatch(
        r"(?:的|own|['’]s|\s|team|group|department|project|团队|部门|项目|组)*", rest, re.I))


def _grammar_token(word: str) -> bool:
    return _deictic(word) or _GRAMMAR_TOKEN.fullmatch(word) is not None


def _name_spans(query: str, names: set[str]) -> list[tuple[int, int]]:
    spans = []
    for name in names:
        pattern = r"(?<![A-Za-z0-9_])" + r"\s+".join(map(re.escape, name.split())) + r"(?![A-Za-z0-9_])"
        spans.extend((m.start(), m.end()) for m in re.finditer(pattern, query, re.I))
    return spans


def _organization_labels(query: str, bound_names: set[str]) -> list[str]:
    """Locate name + organization markers anywhere, independent of verb order."""
    labels = []
    covered = _name_spans(query, bound_names)
    for match in _ORG_SUFFIX.finditer(query):
        if any(a <= match.start() and match.end() <= b for a, b in covered):
            continue  # An organization word inside a registered full name.
        prefix = re.sub(r"(?:['’]s|的)\s*$", "", query[:match.start()].rstrip(" _-"))
        known_suffixes = [(a, b) for a, b in _name_spans(prefix, bound_names) if b == len(prefix)]
        if known_suffixes:
            start = min(a for a, _ in known_suffixes)
            leading = re.search(r"(?:[A-Z][\w-]*\s+)+$", prefix[:start])
            leading_han = re.search(r"[\u3400-\u9fff]+$", prefix[:start].rstrip())
            if leading and any(not _grammar_token(w) for w in leading[0].split()):
                label = leading[0] + prefix[start:]
            elif leading_han and _CJK_ENTITY_PREFIX.sub("", leading_han[0]):
                # A known suffix is not evidence for the whole organization.
                label = prefix[leading_han.start():]
            else:
                label = prefix[start:]
            labels.append(label)
            continue
        latin = re.search(r"[A-Za-z][\w-]*(?:[ \t]+[A-Za-z][\w-]*)*$", prefix)
        if latin:
            words = latin[0].split()
            selected = []
            for word in reversed(words):
                if _grammar_token(word):
                    # A marker makes a capitalized label such as "Will team"
                    # a name candidate; auxiliaries outside that position stay
                    # grammatical. Pronouns/determiners never become names.
                    label_position = not selected and word[0].isupper() and not _deictic(word) and not re.fullmatch(
                        r"what|which|who|whose|this|that|these|those|the|a|an|own", word, re.I)
                    if not label_position:
                        break
                selected.insert(0, word)
                label = normalize_temporal_component(" ".join(selected))
                previous = len(words) - len(selected) - 1
                if label in bound_names and (previous < 0 or not words[previous][0].isupper()
                                             or _grammar_token(words[previous])):
                    break
            label = " ".join(selected).strip("_-")
        else:
            han = re.search(r"[\u3400-\u9fff]+$", prefix)
            label = _CJK_ENTITY_PREFIX.sub("", han[0]) if han else ""
        if label and not _deictic(label):
            labels.append(label)
    return labels


def _unbound_capitals(query: str, names: set[str]) -> list[str]:
    covered = _name_spans(query, names)
    return sorted({m[0] for m in re.finditer(r"(?<![A-Za-z0-9_])[A-Z][A-Za-z0-9_-]*", query)
                   if not _grammar_token(m[0]) and not any(a <= m.start() and m.end() <= b for a, b in covered)})


def _parts(phrase: str) -> list[str]:
    return [re.sub(r"^(?:which|what|哪家|哪个)\s*", "", part.strip(), flags=re.I) for part in re.split(r"\s+(?:and|or)\s+|和|或|与", phrase, flags=re.I)
            if part.strip()]


def _clean_binding(value: str) -> str:
    value = re.sub(r"^(?:(?:为什么|为何|为啥|怎么会|请问|比较|对比)\s*)+", "", value.strip())
    return re.sub(r"(?:为什么|为何|为啥|怎么会)$", "", value).strip(" \t'\"“”")


def _query_bindings(query: str, relation_names: set[str], known_values: set[str],
                    subject_names: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Parse explicit grammatical arguments, never a remainder/stopword list."""
    text = re.sub(r"^(?:as\s+of|截至)\s*[,，:]?\s*", "", _without_dates(query), flags=re.I)
    subjects, topics, values = [], [], []
    patterns = (
        r"\b(?:does|did|do|is|are|was|were|has|have|had)\s+(.+?)\s+(?:(?:currently|now|already)\s+)?(?:use|using|used|adopt|adopted|choose|chosen|pick|picked|select|selected|decide|decided)\b",
        r"^(.+?)\s+(?:currently|now)\s+(?:uses?|using|adopts?)\b",
        r"^([^，,？?。]+?)(?:现在|当前|目前)?(?:采用|使用|选用|用的是|用的|用)(?:哪|什)",
        r"\b(?:is|are|was|were)\s+(.+?)\s+(?:on|with)\b",
        r"\b([^\W\d_][\w-]*)['’]s\b",
        r"^(.+?)(?:现在|当前|目前)(?:是(?:什么|哪个|哪家)|选了(?:什么|哪个|哪家))",
    )
    for index, pattern in enumerate(patterns):
        if index == 1 and re.match(r"(?:what|which|who|why|how|when|is|are|was|were|does|did|do)\b", text, re.I):
            continue
        if index == 2 and re.match(r"^(?:(?:现在|当前|目前|正式)\s*)?(?:采用|使用|选用|用的是|用的|用)", text):
            continue  # A verb-initial question has no explicit subject.
        match = re.search(pattern, text, re.I)
        if match:
            if index == 3:
                # A complete "on/with VALUE" predicate, or a preposition-ended
                # interrogative. Do not reinterpret a previously parsed verb.
                if subjects or re.search(r"\b(?:using|use|used)\b", match[1], re.I):
                    continue
                if not re.search(r"\b(?:on|with)\s+[^\s?？!.,]", text, re.I) and not re.match(
                        r"^(?:what|which)\b.*\b(?:is|are)\b", text, re.I):
                    continue
            actor = _clean_binding(match[1])
            if index == 2 and normalize_temporal_component(actor) in relation_names:
                topics.append(actor)  # A leading nominal topic: "TOPIC now use which?"
            else:
                subjects.extend(part for part in _parts(actor) if not _deictic(part))
    # Possessive Chinese noun phrases. Workspace terms/values remain topic/value
    # arguments, while any explicit object name must resolve in this workspace.
    for match in re.finditer(r"([A-Za-z][\w-]*|[\u3400-\u9fff]+)\s*的", text):
        phrase = _clean_binding(match[1]).casefold()
        if phrase in subject_names:
            subjects.append(phrase)
            continue
        if phrase in known_values:
            continue
        if re.fullmatch(r"(?:(?:目前|当前|现在|正式)\s*)?(?:选定|采用|使用|选用|批准|审批|决定|用)", phrase):
            continue  # A relative verb clause, not a possessor.
        relative = re.fullmatch(r"(.+?)(?:现在|当前|目前)?(?:选定|采用|使用|选用|批准|审批|决定|用)", phrase)
        if relative:
            actor = relative[1].strip()
            if _deictic(actor):
                continue
            if actor in subject_names:
                subjects.append(actor)
                continue
        for term in sorted(relation_names, key=len, reverse=True):
            phrase = phrase.replace(term, " ")
        phrase = phrase.strip()
        if phrase:
            subjects.extend(part for part in _parts(phrase) if not _deictic(part)
                            and normalize_temporal_component(part) not in known_values)
    for pattern in (
        r"\b(?:what|which)\s+(.+?)\s+(?:does|did|do|is|are|was|were)\b",
        r"(?:哪家|哪个)([^？?，,。]+?)(?=[？?，,。]|$)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            topics.extend(_parts(match[1]))
    # Replacement arguments are values, even when phrased passively.
    for pattern in (
        r"\bfrom\s+(.+?)\s+to\s+([^?.!]+)",
        r"从\s*(.+?)\s*(?:换成|改为|替换为)\s*([^？?。]+)",
        r"\b(?:was|is)\s+(.+?)\s+(?:replaced|superseded)\s+by\s+([^?.!]+)",
        r"(.+?)\s*被\s*(.+?)\s*(?:替换|取代|替代)",
        r"^(.+?)(?:为什么|为何)?\s*(?:换成|替换为|改用)\s*([^？?。]+)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            values.extend(map(_clean_binding, (match[1], match[2])))
            break
    # A trailing accompaniment is a real argument even after a parsed subject;
    # it must not disappear when an earlier verb/preposition pattern succeeds.
    accompaniment = re.search(r"\bwith\s+([^?.!]+)", text, re.I)
    if accompaniment:
        for entity in _parts(_clean_binding(accompaniment[1])):
            if normalize_temporal_component(entity) not in subject_names | relation_names:
                values.append(entity)
    # Data-anchored comparative operands catch unknown values in either language.
    # Delimiters belong to question grammar, not a list of business nouns.
    for value in known_values:
        boundary = "" if re.search(r"[\u3400-\u9fff]", value) else r"(?<!\w)"
        pattern = boundary + re.escape(value) + r"\s*(?:和|与|或|\band\b|\bor\b)\s*(.+?)(?=的|选择|依据|区别|差异|方案|\s+\b(?:in|for|on|at|with|as|now|currently|today)\b|[，,？?。.!]|$)"
        match = re.search(pattern, text, re.I)
        if match:
            other = _clean_binding(match[1])
            normalized_other = normalize_temporal_component(other)
            # A known value can be qualified by a known relation, e.g. "VALUE
            # decision sources". Keep the operand, not its explanatory suffix.
            for known in sorted(known_values, key=len, reverse=True):
                if normalized_other == known or normalized_other.startswith(known + " "):
                    normalized_other = known
                    break
            if normalized_other not in subject_names | relation_names:
                values.append(normalized_other)
    return subjects, topics, values


def _eligible_slots(slots: list[dict[str, Any]], workspace_id: str) -> list[dict[str, Any]]:
    # Filter BEFORE indexing, entity resolution, scoring and provider context.
    # Missing provenance never silently becomes trusted, even for a single slot.
    return [dict(slot, slot_id=slot.get("slot_id") or decision_slot_id(slot["subject"], slot["relation"]))
            for slot in slots if workspace_id and slot.get("workspace_id") == workspace_id
            and slot.get("reviewed") is True and slot.get("subject") and slot.get("relation")]


@dataclass(frozen=True)
class WorkspaceSnapshot:
    workspace_id: str
    slots: tuple[dict[str, Any], ...]
    resolver: FrozenRelationResolver


@dataclass(frozen=True)
class RetrievalConfig:
    embedder: Any = AUTO_EMBEDDER


def workspace_snapshot(slots: list[dict[str, Any]], resolver: FrozenRelationResolver,
                       workspace_id: str) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(workspace_id, tuple(deepcopy(_eligible_slots(slots, workspace_id))), resolver)


def retrieve_raw(query: str, snapshot: WorkspaceSnapshot, config: RetrievalConfig) -> list[dict[str, Any]]:
    """Only the original question, authorized snapshot and retrieval config enter here.

    No intent, grammatical binding or guessed scope may filter this population.
    Exceptions in required lexical retrieval propagate; optional dense failure
    leaves lexical candidates usable (or propagates if there is no fallback).
    """
    eligible, resolver, workspace_id = snapshot.slots, snapshot.resolver, snapshot.workspace_id
    if not eligible:
        return []
    normalized = normalize_temporal_component(query)
    history_matches = {slot["slot_id"] for slot in eligible
                       if any(_mentions(normalized, str(value)) for value in slot.get("values", []))}
    subject_matches = {slot["slot_id"] for slot in eligible
                       if any(_mentions(normalized, name) for name in
                              [slot["subject"], *slot.get("subject_aliases", [])])}
    documents, semantic_documents, by_id, topic_tokens = [], [], {}, {}
    for slot in eligible:
        identifier = slot["slot_id"]
        aliases = sorted(_relation_phrases(slot["relation"], resolver) | set(slot.get("aliases", [])))
        topic = " ".join([slot["relation"], *map(str, slot.get("values", [])), *aliases])
        content = " ".join([slot["subject"], *slot.get("subject_aliases", []), topic])
        expanded = content + " " + " ".join(sorted(_tokens(content)))
        documents.append(IndexDocument(workspace_id, identifier, identifier, identifier + ".txt",
                                       expanded, identifier))
        semantic_documents.append(replace(documents[-1], content=content))
        topic_anchored = any(_mentions(normalized, phrase) or
                             (len(phrase.split()) == 1 and phrase.casefold() in _tokens(query))
                             for phrase in aliases + slot.get("values", []))
        by_id[identifier] = dict(slot, aliases=aliases, conflicting_mentions=False,
                                 topic_anchored=topic_anchored,
                                 history_anchor=len(history_matches) == 1 and identifier in history_matches,
                                 unresolved_topic=False)
        topic_tokens[identifier] = _tokens(topic) - _RELATION_VERBS
    index = BM25Index()
    index.build(documents)
    query_tokens = _tokens(query) - _RELATION_VERBS
    search_query = query + " " + " ".join(sorted(query_tokens))
    # Compare different question lengths on a per-query-token scale. This
    # common denominator preserves BM25 ordering and avoids repeated terms in
    # a long question dominating the global confidence policy in tiny corpora.
    score_scale = max(1, len(tokenize_for_index(search_query)))
    hits = index.search(search_query, workspace_id=workspace_id, top_k=len(documents))
    ranked = {}
    for hit in hits:
        if not query_tokens.intersection(topic_tokens[hit.resource_id]):
            continue  # A project name by itself is not a decision topic.
        raw = hit.score / score_scale
        # Monotone softsign preserves BM25 order and supports its negative IDF
        # in tiny workspaces. Scores are bounded in (0,2); raw BM25 is exposed.
        ranked[hit.resource_id] = dict(by_id[hit.resource_id], score=1 + raw / (1 + abs(raw)),
                                       bm25_score=hit.score, dense_score=None)
    embedder = config.embedder
    if embedder is AUTO_EMBEDDER:
        embedder = (None if os.environ.get("LINKLOOM_RETRIEVAL_MODE") == "bm25"
                    else get_slot_embedder())
    if embedder is not None:
        try:
            from linkloom.indexing.vector import VectorIndex
            dense = VectorIndex(embedder)
            dense.build(semantic_documents)
            hits = dense.search(query, workspace_id=workspace_id, top_k=len(documents))
            accepted = {h.resource_id for h in hits if h.score >= SLOT_DENSE_RECALL_MIN_SCORE}
            # Complete lexical aliases/history are independent evidence. Shared
            # fragments cannot keep a rejected semantic hit in clarification.
            ranked = {key: c for key, c in ranked.items() if key in accepted or c["topic_anchored"]}
            for hit in hits:
                if hit.score < SLOT_DENSE_RECALL_MIN_SCORE:
                    continue
                lexical = ranked.get(hit.resource_id)
                candidate = ranked.setdefault(hit.resource_id,
                    dict(by_id[hit.resource_id], score=0.0, bm25_score=None, dense_score=None))
                candidate["dense_score"] = hit.score
                semantic_score = 2 * hit.score
                candidate["score"] = (
                    (1 - SLOT_DENSE_WEIGHT) * candidate["score"] + SLOT_DENSE_WEIGHT * semantic_score
                    if lexical is not None else semantic_score)
                candidate["topic_anchored"] = (candidate["topic_anchored"]
                                                or hit.score >= SLOT_DENSE_MIN_SCORE)
        except (ImportError, OSError, RuntimeError, ValueError):
            if not ranked:
                raise  # Do not label an incomplete retrieval as NO_MATCH.
    for candidate in ranked.values():
        identifier = candidate["slot_id"]
        candidate["subject_anchored"] = identifier in subject_matches
        if candidate["history_anchor"] or (candidate["subject_anchored"] and candidate["topic_anchored"]):
            candidate["score"] = max(candidate["score"], 2.0)
    return sorted(ranked.values(), key=lambda item: (-item["score"], -bool(item.get("history_anchor")), item["slot_id"]))


def rank_decision_slots(query: str, slots: list[dict[str, Any]], resolver: FrozenRelationResolver,
                        *, workspace_id: str, embedder: Any = AUTO_EMBEDDER,
                        include_weak: bool = False) -> list[dict[str, Any]]:
    """Compatibility entry; raw recall is independent of every understanding rule."""
    return retrieve_raw(query, workspace_snapshot(slots, resolver, workspace_id), RetrievalConfig(embedder))


def _named(phrase: str) -> bool:
    # Name-shaped tokens in explicit grammatical positions. A broad Chinese
    # clause is not a proper name merely because an old regex captured it.
    return bool(re.fullmatch(r"[A-Za-z][\w-]*|(?:[A-Z][\w-]*\s+)+[A-Z][\w-]*|"
                             r"(?i:project)\s+\d+|[\u3400-\u9fff]+项目", phrase))


def entity_check(query: str, snapshot: WorkspaceSnapshot) -> dict[str, Any]:
    """Independent raw-text name positions; never consumes parser hypotheses."""
    slots, resolver = snapshot.slots, snapshot.resolver
    original_query = query
    names = {normalize_temporal_component(n) for slot in slots for n in
             [slot["subject"], *slot.get("subject_aliases", []), *map(str, slot.get("values", [])),
              *slot.get("reference_values", []), *_relation_phrases(slot["relation"], resolver), *slot.get("aliases", [])]}
    # Only remove a leading time expression for legacy grammatical frames.
    # General entity scanning below always receives the original text. Never
    # delete loose time characters (e.g. 时) from a registered name.
    for pattern in (_ISO_TIMESTAMP, _ISO_DATE, _CHINESE_DATE, _RELATIVE_DATE):
        match = pattern.match(query)
        if match and len(match[0]) > 1 and not any(a == 0 for a, _ in _name_spans(query, names)):
            query = query[match.end():].lstrip(" ,，:：")
    positions, actors = [], []
    for position_kind, pattern in enumerate((
        r"\b(?:does|did|do|is|are|was|were|has|have|had)\s+(?!(?:currently|now|already|still|being|been|not)\b)([\w-]+(?:\s+[\w-]+)*?)\s+(?:(?:currently|now|already|still)\s+)?(?:use|using|used|adopt|adopted|choose|chosen|pick|picked|select|selected|decide|decided|on|with)\b",
        r"\b((?-i:[A-Z][\w-]*(?:\s+[A-Z][\w-]*)*)|[A-Za-z][\w-]*)['’]s\b",
        r"^([A-Za-z][\w-]*|[\u3400-\u9fff]+项目)\s*(?:现在|当前|目前)?(?:采用|使用|选用|用的是|用的|用|选了|是)",
        r"([A-Za-z][\w-]*(?:\s*项目)?|[\u3400-\u9fff]+项目)\s*的",
        r"\bfrom\s+([\w-]+)\s+to\s+([\w-]+)",
        r"\b(?:was|is)\s+([\w-]+)\s+(?:replaced|superseded)\s+by\s+([\w-]+)",
        r"\bwith\s+([\w-]+)(?=[?.!]|$)",
    )):
        for match in re.finditer(pattern, query, re.I):
            words = [part for group in match.groups() for part in _parts(group)]
            positions.extend(words)
            if position_kind in (0, 2):
                actors.extend(words)
    for known in sorted(names, key=len, reverse=True):
        if not _mentions(query.casefold(), known):
            continue
        match = re.search(re.escape(known) + r"\s+(?:and|or)\s+([\w-]+)\b", query, re.I)
        if match:
            positions.append(match[1])
    hard = [word for word in positions if _named(re.sub(r"\s+项目$", "", word)) and not _deictic(word)
            and normalize_temporal_component(word) not in names
            and normalize_temporal_component(re.sub(r"\s*项目$", "", word)) not in names]
    subjects = {normalize_temporal_component(n) for slot in slots for n in
                [slot["subject"], *slot.get("subject_aliases", [])]}
    role_conflict = any(normalize_temporal_component(w) in names - subjects
                        for w in actors if not _deictic(w))
    entity_names = {normalize_temporal_component(n) for slot in slots for n in
                    [slot["subject"], *slot.get("subject_aliases", []), *map(str, slot.get("values", [])),
                     *slot.get("reference_values", [])]}
    organization_names = _organization_labels(original_query, entity_names)
    hard.extend(n for n in organization_names if normalize_temporal_component(n) not in entity_names)
    role_conflict = role_conflict or any(normalize_temporal_component(n) not in subjects
                                       for n in organization_names if normalize_temporal_component(n) in entity_names)
    capitals = _unbound_capitals(original_query, names)
    return {"hard_veto": bool(hard), "unbound_names": sorted(set(hard)),
            "unbound_proper_names": capitals, "role_conflict": role_conflict}


def parse_query_clues(query: str, snapshot: WorkspaceSnapshot) -> dict[str, Any]:
    subjects = {normalize_temporal_component(n) for slot in snapshot.slots
                for n in [slot["subject"], *slot.get("subject_aliases", [])]}
    values = {normalize_temporal_component(str(v)) for slot in snapshot.slots for v in slot.get("values", [])}
    topics = {normalize_temporal_component(n) for slot in snapshot.slots for n in
              _relation_phrases(slot["relation"], snapshot.resolver) | set(slot.get("aliases", []))}
    actors, themes, operands = _query_bindings(query, topics, values, subjects)
    unresolved = [s for s in actors if normalize_temporal_component(s) not in subjects]
    unresolved += [t for t in themes if normalize_temporal_component(t) not in topics]
    unresolved += [v for v in operands if normalize_temporal_component(v) not in values]
    return {"subjects": actors, "topics": themes, "values": operands, "unresolved": unresolved}


def discover_scope(query: str, slots: list[dict[str, Any]], resolver: FrozenRelationResolver,
                   *, workspace_id: str, subject: str = "", relation: str = "",
                   embedder: Any = AUTO_EMBEDDER, include_weak: bool = False) -> dict[str, Any]:
    snapshot = workspace_snapshot(slots, resolver, workspace_id)
    # Run before parsing. Even a parser exception cannot cancel or change recall.
    raw = retrieve_raw(query, snapshot, RetrievalConfig(embedder))
    try:
        clues = parse_query_clues(query, workspace_snapshot(slots, resolver, workspace_id))
        if not isinstance(clues, dict) or any(
                not isinstance(clues.get(k, []), list) or
                any(not isinstance(v, str) for v in clues.get(k, []))
                for k in ("subjects", "topics", "values", "unresolved")):
            raise ValueError("invalid parser clues")
    except Exception:
        clues = {"unresolved": [], "parser_error": True}
    safety = entity_check(query, snapshot)
    candidates = deepcopy(raw)
    # Candidate validation is data-anchored, not an intersection that removes
    # raw candidates. Preserve every original score for diagnostics/evaluation.
    subject_hits = {normalize_temporal_component(s["subject"]) for s in snapshot.slots
                    if any(_mentions(query.casefold(), n) for n in [s["subject"], *s.get("subject_aliases", [])])}
    groups = []
    for slot in snapshot.slots:
        if any(_mentions(query.casefold(), n) for n in _relation_phrases(slot["relation"], resolver)
               if n not in _RELATION_VERBS):
            groups.append(slot["slot_id"])
    # A one-character nominal operand can abbreviate a workspace topic.
    # Inspect conjunction operands, not every CJK character in the sentence
    # (source/evidence wording often shares characters with unrelated aliases).
    operands = re.findall(r"(?:的|\s|^)([\u3400-\u9fff])\s*(?:和|与)", query)
    operands += re.findall(r"(?:和|与)\s*([\u3400-\u9fff])(?=[?？，,。]|$)", query)
    for operand in operands:
        for slot in snapshot.slots:
            if operand in _tokens(" ".join(_relation_phrases(slot["relation"], resolver))):
                groups.append(slot["slot_id"])
    multiple_topics = len({s["relation"] for s in snapshot.slots if s["slot_id"] in groups}) > 1
    for candidate in candidates:
        candidate["conflicting_mentions"] = multiple_topics or len(subject_hits) > 1
        candidate["unresolved_topic"] = bool(clues.get("unresolved"))
    explicit = bool(subject or relation)
    if explicit:
        canonical = resolver.resolve(relation).canonical_relation or relation
        candidates = [dict(s, score=2.0, bm25_score=None, dense_score=None) for s in snapshot.slots
                      if normalize_temporal_component(s["subject"]) == normalize_temporal_component(subject)
                      and normalize_temporal_component(s["relation"]) == normalize_temporal_component(canonical)]
    # Soft uncertainty reduces confidence through a uniform evidence-coverage
    # factor; it never edits the raw pool or its scores.
    confidence = candidates[0]["score"] if candidates else 0.0
    if clues.get("unresolved") and candidates:
        known = set().union(*(_tokens(str(x)) for s in snapshot.slots for x in
                              [s["subject"], s["relation"], *s.get("values", []),
                               *_relation_phrases(s["relation"], resolver)]))
        missing = set().union(*(_tokens(_without_references(x)) for x in clues["unresolved"] if not _deictic(x))) - known
        confidence /= 1 + len(missing)
    if candidates and not candidates[0].get("topic_anchored") and candidates[0].get("dense_score") is None:
        # A fragment of a multiword alias is weak evidence, not a full alias.
        confidence = min(confidence, max(0.0, candidates[0]["score"] - 1.0))
    if safety["role_conflict"]:
        confidence = 0.0
    margin = candidates[0]["score"] - candidates[1]["score"] if len(candidates) > 1 else float("inf")
    if candidates and candidates[0].get("history_anchor"):
        margin = float("inf")
    matched = bool(candidates and not safety["hard_veto"] and not safety["unbound_proper_names"] and (explicit or (
        confidence >= SLOT_MATCH_MIN_SCORE and margin >= SLOT_MATCH_MIN_MARGIN
        and candidates[0].get("topic_anchored", True)
        and not candidates[0].get("conflicting_mentions"))))
    status = "EXPLICIT" if explicit and matched else "MATCHED" if matched else "AMBIGUOUS" if candidates else "NO_MATCH"
    if safety["hard_veto"]:
        status = "NO_MATCH"
    return {"status": status, "scope": {k: candidates[0][k] for k in ("subject", "relation")} if matched else None,
            "candidates": candidates[:SLOT_LLM_CANDIDATES], "candidate_count": len(candidates),
            "raw_candidates": raw, "clues": clues, "entity_check": safety,
            "confidence": confidence, "basis": [],
            "reason": "One reviewed slot clears the global confidence and margin." if matched else
                      "Candidates require disambiguation." if candidates else "No related reviewed slot was retrieved.",
            "decided_by": "rule" if explicit and matched else "slot_match" if matched else "fallback"}


def resolve_query_date(query: str, selected: object) -> tuple[str | None, dict[str, str] | None]:
    """Resolve exact dates only; as_of is a point, never an invented month day."""
    clarification = {"code": "DATE_SCOPE_REQUIRED",
                     "message": "Choose one exact valid-time date (00:00 UTC). Relative periods, ranges and conflicting dates need clarification."}
    try:
        timestamp = _parse_as_of(selected)
        timestamps = _ISO_TIMESTAMP.findall(query)
        without_times = _ISO_TIMESTAMP.sub(" ", query)
        dates = _ISO_DATE.findall(without_times)
        chinese_dates = _CHINESE_DATE.findall(without_times)
        dates.extend(f"{int(year):04d}-{int(month):02d}-{int(day):02d}" for year, month, day in chinese_dates)
        points = timestamps + dates
        distinct = {_parse_as_of(point) for point in points}
        mismatch = bool(timestamp and ((timestamps and timestamp not in distinct)
                        or (dates and any(timestamp.date().isoformat() != day for day in dates))))
        if len(distinct) > 1 or mismatch or (_DATE_RANGE.search(query) and (points or _RELATIVE_DATE.search(query))):
            return None, clarification
        if timestamp:
            return str(selected), None
        if distinct:
            remainder = _CHINESE_DATE.sub(" ", _ISO_DATE.sub(" ", without_times))
            if _RELATIVE_DATE.search(remainder):
                return None, clarification
            return points[0], None
        if _RELATIVE_DATE.search(query):
            return None, clarification
        return None, None
    except ProductApplicationError as error:
        return None, {"code": error.code, "message": str(error)}
