"""Bind human-confirmed relation facts to one run's verified reads.

The temporal graph projection (``SQLiteGraphStore.load_edges``) re-verifies
every HUMAN_APPROVAL receipt against its decision row, source version, content
hash and quoted line range. This module reuses those edges as structured
source facts for the Host conflict check. It adds no fact store and no
sentence parsing; it only checks that a confirmed fact belongs to the exact
read a claim cites:

* same workspace and a ``verified`` read;
* the read's path is a source frozen for this run and still current, with the
  same physical content hash, and the confirmed evidence is from that version;
* the cited source lines overlap the reviewed span.

Confirmed facts are used to reject conflicting claims. A claim that no
confirmed fact covers may still be answered from its own valid EvidenceSpan.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from linkloom.agents.claim_grounding import (
    TYPED_CLAIM_RELATIONS, _Fact, normalize_entity, validate_typed_relation_registry,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FrozenSource:
    """One source version that the Host copied into this run's read-only vault."""

    relative_path: str
    source_id: str
    source_version: str
    content: str

    @property
    def physical_sha256(self) -> str:
        # Product investigation writes source_id.md with platform newlines;
        # the scanner hashes those bytes (see ui.product._runtime_passage_spans).
        return hashlib.sha256(self.content.replace("\n", os.linesep).encode("utf-8")).hexdigest()


def _edge_facts(edge: Any) -> tuple[_Fact, ...]:
    """Map one confirmed edge onto every registered typed relation it witnesses."""
    facts = []
    for spec in TYPED_CLAIM_RELATIONS.values():
        mapping = spec.get("graph_edge")
        if mapping is None or mapping[0] != edge.edge_type:
            continue
        subject, value = (edge.source, edge.target) if mapping[1] == "source" else (edge.target, edge.source)
        if edge.valid_time_kind == "atemporal":
            valid_on, kind, valid_to = None, None, None
        elif edge.valid_time_kind == "snapshot":
            valid_on, kind, valid_to = edge.valid_from, "snapshot", None
        else:
            valid_on, kind, valid_to = edge.valid_from, "interval", edge.valid_to
        scope = subject.scope or value.scope
        facts.append(_Fact(
            ("not_" if edge.blocker else "") + spec["fact_relation"],
            normalize_entity(subject.label), normalize_entity(value.label),
            valid_on, kind, normalize_entity(scope) if scope else None, valid_to, "confirmed",
        ))
    return tuple(facts)


class ConfirmedFactIndex:
    """Confirmed relation facts for one workspace, addressable by verified read."""

    def __init__(self, workspace_id: str, edges: Iterable[Any], sources: Iterable[FrozenSource]) -> None:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("ConfirmedFactIndex requires a workspace.")
        validate_typed_relation_registry()
        self.workspace_id = workspace_id
        self._sources = {source.relative_path: source for source in sources}
        by_source: dict[str, list[tuple[Any, tuple[_Fact, ...]]]] = {}
        for edge in edges:
            if edge.source.workspace_id != workspace_id:
                continue
            facts = _edge_facts(edge)
            if not facts:
                continue
            for item in edge.evidence:
                if item.workspace_id == workspace_id and item.role in {"source", "blocker"}:
                    by_source.setdefault(item.source_id, []).append((item, facts))
        self._by_source = by_source

    def source_current(self, evidence: Mapping[str, Any]) -> bool:
        """The cited read comes from a frozen source version that is still current."""
        source = self._sources.get(evidence.get("relative_path")) if isinstance(evidence, Mapping) else None
        return source is not None and evidence.get("content_sha256") == source.physical_sha256

    def facts_for(self, evidence: Mapping[str, Any]) -> tuple[_Fact, ...]:
        """Return confirmed facts whose reviewed span overlaps the cited source lines."""
        if not isinstance(evidence, Mapping):
            return ()
        source = self._sources.get(evidence.get("relative_path"))
        quote = evidence.get("quote")
        start, end = evidence.get("line_start"), evidence.get("line_end")
        if (
            source is None
            or evidence.get("status") != "verified"
            or not isinstance(evidence.get("evidence_id"), str)
            or evidence.get("workspace_id") != self.workspace_id
            or evidence.get("content_sha256") != source.physical_sha256
            or not isinstance(quote, str) or not quote.strip()
            or type(start) is not int or type(end) is not int or not 1 <= start <= end
        ):
            return ()
        lines = source.content.splitlines()[start - 1:end]
        if len(lines) != end - start + 1 or quote not in "\n".join(lines):
            return ()
        result: list[_Fact] = []
        for item, facts in self._by_source.get(source.source_id, ()):
            if (item.source_version_id == source.source_version
                    and item.line_start <= end and start <= item.line_end):
                result.extend(fact for fact in facts if fact not in result)
        return tuple(result)


def load_confirmed_fact_index(
    database_path: str, workspace_id: str, sources: Iterable[FrozenSource], *,
    authorize_source: Callable[[str, str, str], bool],
) -> ConfirmedFactIndex | None:
    """Re-read confirmed edges now; None means no confirmed facts are usable."""
    from linkloom.temporal_graph.store import SQLiteGraphStore

    current = tuple(source for source in sources
                    if authorize_source(workspace_id, source.source_id, source.source_version))
    if str(database_path) == ":memory:" or not os.path.isfile(database_path):
        return None
    edge_types = {spec["graph_edge"][0] for spec in TYPED_CLAIM_RELATIONS.values()
                  if spec.get("graph_edge") is not None}
    try:
        store = SQLiteGraphStore(database_path, authorized_workspace=workspace_id,
                                 authorize_source=authorize_source)
        snapshot = store.load_edges(workspace_id, edge_types=edge_types)
    except (OSError, ValueError, PermissionError, sqlite3.Error) as error:
        _LOG.warning("Confirmed relation facts unavailable for grounding: %s", error)
        return None
    return ConfirmedFactIndex(workspace_id, snapshot.edges, current)


__all__ = ["ConfirmedFactIndex", "FrozenSource", "load_confirmed_fact_index"]
