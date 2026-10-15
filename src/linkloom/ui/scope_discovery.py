"""Explainable, model-free discovery over an already verified workspace index."""
from __future__ import annotations

import re
from typing import Any

from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.product import ProductApplicationError, _parse_as_of

# Vocabulary describes relations, never fixture entities or authoritative values.
_SUPPLIER = ("supplier", "vendor", "供应商", "供货商")
_RELATION_VERBS = {"uses", "use", "has", "have", "is", "are", "of", "the", "a", "an"}
_QUESTION_WORDS = set("""what which who why how when where we our i the a an is are
was were did do does use uses using now current currently adopted approved effective
decision decisions supplier suppliers vendor vendors switch switched change changed
from to for and or compare explain show tell me receipt receipts reason reasons
source sources evidence on at in this project team it its last month year week
today yesterday used by adopted approval valid formal replacement replaced rationale
original support supported conflicts conflict uncertainty uncertain unverified
provider fail failed budget differences difference compare investigate investigation
were had has have been currently approved effective which""".split())
_RELATIVE_DATE = re.compile(
    r"(?:上|下|本|这|前|后)(?:个)?(?:月|周|年|季度|星期)|去年|今年|明年|"
    r"昨天|前天|今天|明天|后天|当时|以前|之前|最近|过去|至今|迄今|早上|上午|下午|晚上|点|时|"
    r"\b(last|previous|this|next|past)\s+(month|year|week|quarter)|"
    r"\b(today|yesterday|tomorrow|tonight|recently|ago|then|before|after|during)\b|"
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b|"
    r"\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b|"
    r"\d{4}年|\d{1,2}月|\b\d{4}-\d{2}(?!-\d{2})\b|\b\d{4}\b|\bq[1-4]\b",
    re.IGNORECASE,
)
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", re.IGNORECASE)
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_CHINESE_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_DATE_RANGE = re.compile(
    r"\b(between|since|until)\b|\b(before|after|from)\s+\d|"
    r"\b(to|and|through|until)\s+(now|today|present)\b|"
    r"从.*\d.*(?:到|至)|\d.*(?:到|至).*\d|至今|迄今|到现在|到今天", re.IGNORECASE)
_CHINESE_QUESTION_WORDS = (
    "为什么", "为何", "我们", "这个项目", "该项目", "本项目", "项目", "现在", "当前",
    "目前", "采用", "正式", "已审批", "用的是", "用的", "使用", "用了", "用", "从",
    "换成了", "换成", "替换为", "被", "替换", "换", "成", "了", "的", "是", "哪家", "哪", "谁",
    "请", "告诉", "我", "吗", "在", "于", "和", "或", "什么", "怎么", "如何",
)


def _unexplained_terms(query: str, matched_phrases: list[str]) -> list[str]:
    # Conservative lexical coverage: unsupported wording asks for manual scope.
    # This prevents ignored unknown entities in any case or language from being
    # silently replaced by the only supplier in the workspace.
    remainder = _ISO_TIMESTAMP.sub(" ", query)
    remainder = _ISO_DATE.sub(" ", remainder)
    remainder = _CHINESE_DATE.sub(" ", remainder)
    remainder = _RELATIVE_DATE.sub(" ", remainder)
    for phrase in sorted(matched_phrases, key=len, reverse=True):
        if re.search(r"[\u3400-\u9fff]", phrase):
            remainder = remainder.replace(phrase.casefold(), " ")
        else:
            remainder = re.sub(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", " ", remainder)
    for word in sorted(_CHINESE_QUESTION_WORDS, key=len, reverse=True):
        remainder = remainder.replace(word, "")
    return [word for word in re.findall(r"[a-z][a-z0-9_-]*|[\u3400-\u9fff]+|\d+", remainder)
            if word not in _QUESTION_WORDS and word not in _RELATION_VERBS]


def _mentions(query: str, phrase: str) -> bool:
    phrase = normalize_temporal_component(phrase)
    if not phrase:
        return False
    # Latin word boundaries prevent Ann from matching planning, while Chinese
    # names/phrases are commonly embedded without whitespace.
    if re.search(r"[\u3400-\u9fff]", phrase):
        return phrase in query
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", query) is not None


def _relation_phrases(relation: str, resolver: FrozenRelationResolver) -> set[str]:
    phrases = {relation}
    phrases.update(alias for alias, target in resolver.aliases.items()
                   if normalize_temporal_component(target) == normalize_temporal_component(relation))
    words = set(re.findall(r"[a-z]+|[\u3400-\u9fff]+", relation.casefold())) - _RELATION_VERBS
    phrases.update(word for word in words if len(word) > 2)
    if words.intersection(_SUPPLIER):
        phrases.update(_SUPPLIER)
        phrases.update(("suppliers", "vendors"))
    return phrases


def discover_scope(query: str, slots: list[dict[str, Any]], resolver: FrozenRelationResolver,
                   *, subject: str = "", relation: str = "") -> dict[str, Any]:
    """Every recognized identifier constrains the result; no top-1 heuristic."""
    def finish(status: str, selected: list[dict[str, Any]], reason: str,
               basis: list[str] | None = None) -> dict[str, Any]:
        candidates = [{key: slot[key] for key in
                       ("subject", "relation", "values", "current_value", "decision_ids") if key in slot}
                      for slot in selected[:5]]
        return {"status": status, "scope": {key: selected[0][key] for key in ("subject", "relation")}
                if status in {"MATCHED", "EXPLICIT"} else None,
                "candidates": candidates, "candidate_count": len(selected),
                "reason": reason, "basis": basis or []}

    if subject or relation:
        canonical = resolver.resolve(relation).canonical_relation or relation
        selected = [slot for slot in slots
                    if normalize_temporal_component(slot["subject"]) == normalize_temporal_component(subject)
                    and normalize_temporal_component(slot["relation"]) == normalize_temporal_component(canonical)]
        return finish("EXPLICIT" if len(selected) == 1 else "NO_MATCH", selected,
                      "Explicit scope is verified in this workspace." if selected
                      else "The explicit scope has no reviewed, source-verified decision in this workspace.")

    normalized = normalize_temporal_component(query)
    identifiers: dict[str, set[int]] = {}
    relations: dict[str, set[int]] = {}
    for index, slot in enumerate(slots):
        for phrase in (slot["subject"], *slot.get("values", [])):
            if _mentions(normalized, phrase):
                identifiers.setdefault(phrase, set()).add(index)
        for phrase in sorted(_relation_phrases(slot["relation"], resolver)):
            if _mentions(normalized, phrase):
                relations.setdefault(phrase, set()).add(index)
    constraints = [*identifiers.values(), *relations.values()]
    basis = [*identifiers, *relations]
    if not constraints:
        return finish("NO_MATCH", [], "No reviewed subject, history value or relation term matches the question.")
    unexplained = _unexplained_terms(normalized, basis)
    if unexplained:
        return finish("NO_MATCH", [], "Some question terms are outside the trusted index or supported wording. Choose an explicit decision scope: " + ", ".join(unexplained))
    selected_indices = set.intersection(*constraints)
    if not selected_indices:
        involved = set.union(*constraints)
        return finish("AMBIGUOUS", [slot for i, slot in enumerate(slots) if i in involved],
                      "The question spans conflicting or multiple decision scopes. Select one decision; multi-decision investigation is not supported.", basis)

    selected = [slot for i, slot in enumerate(slots) if i in selected_indices]
    return finish("MATCHED" if len(selected) == 1 else "AMBIGUOUS", selected,
                  "One reviewed decision matches all recognized terms." if len(selected) == 1
                  else "Several reviewed decisions match. Select one to continue the same question.", basis)


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
        if len(distinct) > 1 or mismatch or _DATE_RANGE.search(query):
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
