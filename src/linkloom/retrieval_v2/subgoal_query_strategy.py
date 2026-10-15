"""Bounded query construction for dependent retrieval subgoals.

The caller owns provenance and decides which entity strings are already
verified.  This module only combines those strings with the current subgoal;
it does not inspect evidence, infer entities, or perform retrieval itself.
"""

from __future__ import annotations

from collections.abc import Iterable


_MAX_QUERIES = 2


class SubgoalQueryStrategy:
    """Build a small deterministic set of entity-scoped subgoal queries."""

    max_queries = _MAX_QUERIES

    def build_queries(
        self,
        current_subgoal: str,
        verified_entities: Iterable[str] | None,
        *,
        alternate_subgoal: str | None = None,
    ) -> tuple[str, ...]:
        """Return a primary query and, when supplied, one alternate query.

        A dependent subgoal cannot advance without at least one verified
        entity. Duplicate entities are collapsed case-insensitively. A
        {verified_entity} template binds only with one unique entity; an
        untemplated query receives the verified entity as a prefix. No entity
        is inferred.
        """

        subgoal = _normalize_text(current_subgoal)
        if not subgoal or verified_entities is None:
            return ()
        alternate = _normalize_text(alternate_subgoal) if alternate_subgoal else ""

        if isinstance(verified_entities, str):
            verified_entities = (verified_entities,)

        entities: list[str] = []
        seen_entities: set[str] = set()
        for entity in verified_entities:
            if not isinstance(entity, str):
                continue
            normalized_entity = _normalize_text(entity)
            entity_key = normalized_entity.casefold()
            if not entity_key or entity_key in seen_entities:
                continue
            seen_entities.add(entity_key)
            entities.append(normalized_entity)
        if not entities:
            return ()

        entity_prefix = " ".join(entities)
        placeholder = "{verified_entity}"
        has_template = placeholder in subgoal or placeholder in alternate
        if has_template and len(entities) != 1:
            return ()
        entity = entities[0] if has_template else entity_prefix

        def bind(query: str) -> str:
            if not query:
                return ""
            if placeholder in query:
                return query.replace(placeholder, entity)
            return f"{entity_prefix} {query}"

        primary = bind(subgoal)
        alternate_query = bind(alternate)
        queries = [primary]
        if alternate_query:
            queries.append(alternate_query)
        deduplicated: list[str] = []
        seen_queries: set[str] = set()
        for query in queries[:_MAX_QUERIES]:
            query_key = query.casefold()
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            deduplicated.append(query)
        return tuple(deduplicated)


def _normalize_text(value: str) -> str:
    """Collapse caller-provided whitespace without inventing text."""

    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


__all__ = ["SubgoalQueryStrategy"]
