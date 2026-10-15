"""Deterministic accepted-only Strategy selection from configured durable Store."""
from dataclasses import dataclass

from linkloom.experience.models import ExperienceQuery

from .models import require_digest
from .store import StrategyStore


def bounds(top_k, max_chars):
    if type(top_k) is not int or not 0 <= top_k <= 5 or type(max_chars) is not int or not 0 <= max_chars <= 4000:
        raise ValueError("Strategy bounds must stay within hard 5 items /4000 characters")


def validated_query(query):
    if type(query) is not ExperienceQuery or ExperienceQuery.from_dict(query.to_dict()) != query:
        raise ValueError("closed structured applicability query required")
    return query


@dataclass(frozen=True)
class StrategySelection:
    strategy_ids: tuple[str, ...]
    query: ExperienceQuery
    top_k: int
    max_chars: int

    def __post_init__(self):
        bounds(self.top_k, self.max_chars)
        validated_query(self.query)
        if not isinstance(self.strategy_ids, tuple) or len(self.strategy_ids) > 5 or len(set(self.strategy_ids)) != len(self.strategy_ids):
            raise ValueError("selection IDs must be unique and hard bounded")
        for identity in self.strategy_ids:
            require_digest(identity)


class StrategyRetriever:
    def __init__(self, store):
        if type(store) is not StrategyStore:
            raise ValueError("configured durable Strategy Store required")
        self._store = store

    def retrieve(self, query, *, top_k=3, max_chars=2000):
        from .context import choose
        bounds(top_k, max_chars)
        validated_query(query)
        ids = tuple(r.strategy_id for r in self._store.list("accepted"))
        records = choose(self._store, ids, query, top_k, max_chars)
        return StrategySelection(tuple(r.strategy_id for r in records), query, top_k, max_chars)
