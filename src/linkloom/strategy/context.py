"""Final accepted-context serialization freshly resolves approval and all bounds."""
from dataclasses import dataclass, field

from .models import require_digest
from .policy import render_procedure
from .retrieval import StrategySelection, bounds, validated_query
from .store import StrategyStore

HEADING = "Relevant accepted strategy"


def render(records):
    return HEADING + "\n" + "\n\n".join(render_procedure(r, "").lstrip("\n") for r in records) if records else ""


def choose(store, ids, query, top_k, max_chars):
    if type(store) is not StrategyStore or not isinstance(ids, tuple):
        raise ValueError("configured Store and immutable IDs required")
    bounds(top_k, max_chars)
    validated_query(query)
    seen, ranked = set(), []
    for identity in ids:
        require_digest(identity)
        if identity in seen:
            continue
        seen.add(identity)
        record = store.get(identity)
        if record is None or record.status != "accepted":
            raise ValueError("candidate/rejected/orphan Strategy cannot enter accepted context")
        store.validate_reviewed_record(record)
        signals = set(query.task_characteristics)
        overlap = len(signals.intersection(record.applicability.task_characteristics))
        if query.workflow not in record.applicability.workflows or signals.intersection(record.exclusions) or not overlap:
            continue
        if query.pattern_types and "evidence_semantics" not in query.pattern_types:
            continue
        ranked.append((overlap, record))
    records = []
    for _, record in sorted(ranked, key=lambda item: (-item[0], item[1].strategy_id)):
        if len(records) >= top_k:
            break
        text = render(tuple(records) + (record,))
        if len(text) <= max_chars and len(text.encode("utf-8")) <= 16000:
            records.append(record)
    return tuple(records)


@dataclass(frozen=True)
class StrategyContextSection:
    strategy_ids: tuple[str, ...]
    model_text: str
    _selection: StrategySelection = field(repr=False)
    _builder: "StrategyContextBuilder" = field(repr=False)

    def to_dict(self):
        if type(self._builder) is not StrategyContextBuilder:
            raise ValueError("final context requires configured serializer owner")
        return self._builder.serialize(self)


class StrategyContextBuilder:
    def __init__(self, store, *, hard_top_k=5, max_chars=4000):
        if type(store) is not StrategyStore:
            raise ValueError("persisted Strategy Store required")
        bounds(hard_top_k, max_chars)
        self._store, self._hard_top_k, self._max_chars = store, hard_top_k, max_chars

    def _records(self, selection, ids):
        if type(selection) is not StrategySelection:
            raise ValueError("candidate preview is not an accepted Strategy selection")
        bounds(self._hard_top_k, self._max_chars)
        bounds(selection.top_k, selection.max_chars)
        return choose(self._store, ids, selection.query, min(self._hard_top_k, selection.top_k), min(self._max_chars, selection.max_chars))

    def build(self, selection):
        if type(selection) is not StrategySelection:
            raise ValueError("accepted Strategy selection required")
        records = self._records(selection, selection.strategy_ids)
        return StrategyContextSection(tuple(r.strategy_id for r in records), render(records), selection, self)

    def serialize(self, section):
        if type(section) is not StrategyContextSection or section._builder is not self:
            raise ValueError("invalid context serializer owner")
        records = self._records(section._selection, section.strategy_ids)
        ids, text = tuple(r.strategy_id for r in records), render(records)
        if section.strategy_ids != ids or section.model_text != text:
            raise ValueError("caller context differs from fresh bounded accepted serialization")
        if len(ids) > min(5, self._hard_top_k) or len(text) > min(4000, self._max_chars) or len(text.encode("utf-8")) > 16000:
            raise ValueError("final serialization exceeds hard/configured bounds")
        return {"strategy_ids": list(ids), "model_text": text, "total_chars": len(text),
                "total_utf8_bytes": len(text.encode("utf-8"))}
