"""Scoped, human-confirmed terminology; never a business fact or prompt input."""
from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import uuid4

from linkloom.memory import MemoryLifecycle, MemoryScope, MemoryStore
from linkloom.memory.models import MemoryCandidate, MemoryItem
from linkloom.memory.policy import validate_actor
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.product import ProductApplicationError
from linkloom.ui.scope_discovery import _mentions, _relation_phrases, _unexplained_terms

SCHEMA = "decision-term/v1"
KEY = "decision-term"


class FeedbackMemory:
    """Single local writer, serialized with ProductApplication._store_lock."""

    def __init__(self, log_path: str) -> None:
        self.store = MemoryStore(log_path)
        self.lifecycle = MemoryLifecycle(self.store)

    @staticmethod
    def _scoped(entry: MemoryCandidate | MemoryItem, workspace_id: str) -> bool:
        return (entry.scope is MemoryScope.PROJECT and entry.key == KEY
                and entry.metadata.get("scope_key") == workspace_id)

    @staticmethod
    def _mapping(value: object, resolver: FrozenRelationResolver,
                 slots: list[dict[str, Any]]) -> tuple[str, str]:
        if not isinstance(value, dict) or set(value) != {"schema_version", "term", "relation"} or value["schema_version"] != SCHEMA:
            raise ValueError("Only a structured decision-term/v1 mapping is allowed.")
        term, relation = value["term"], value["relation"]
        if isinstance(term, str):
            term = " ".join(term.casefold().split())
        if not isinstance(term, str) or not re.fullmatch(r"[^\W\d_][\w-]{1,39}", term, re.UNICODE):
            raise ValueError("Enter one literal term of 2–40 characters. Sentences and multi-word phrases are unsupported.")
        allowed = {slot["relation"] for slot in slots}
        if not isinstance(relation, str) or relation not in allowed or relation not in resolver.canonical_relations:
            raise ValueError("Choose an existing reviewed relation in this workspace.")
        protected = set(resolver.canonical_relations) | set(resolver.aliases)
        for canonical in resolver.canonical_relations:
            protected.update(_relation_phrases(canonical, resolver))
        for slot in slots:
            protected.update((slot["subject"], *slot.get("values", [])))
        if not _unexplained_terms(term, []) or any(
            _mentions(term, phrase) or _mentions(phrase.casefold(), term) for phrase in protected
        ):
            raise ValueError("A feedback term cannot override known vocabulary or trusted entity names.")
        return term, relation

    @staticmethod
    def _entry(entry: MemoryCandidate | MemoryItem) -> dict[str, Any]:
        item = isinstance(entry, MemoryItem)
        return {"id": entry.id, "candidate_id": entry.candidate_id if item else entry.id,
                "term": entry.value.get("term"), "relation": entry.value.get("relation"),
                "scope": entry.scope.value, "workspace_id": entry.metadata["scope_key"],
                "status": entry.status.value, "actor": entry.actor if item else entry.metadata.get("proposed_by"),
                "version": entry.id, "schema_version": SCHEMA}

    def listing(self, workspace_id: str) -> dict[str, Any]:
        self.store.reload()
        items = [item for item in self.store.items.values() if self._scoped(item, workspace_id)]
        confirmed = {item.candidate_id for item in items}
        candidates = [c for c in self.store.candidate_history.values()
                      if self._scoped(c, workspace_id) and c.id not in confirmed]
        return {"entries": [self._entry(entry) for entry in [*candidates, *items]],
                "workspace_id": workspace_id, "schema_version": SCHEMA}

    def action(self, action: str, payload: dict[str, Any], workspace_id: str,
               resolver: FrozenRelationResolver, slots: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
        self.store.reload()
        actor = payload.get("actor")
        try:
            validate_actor(actor)
            if action == "propose":
                value = {"schema_version": SCHEMA, "term": payload.get("term"), "relation": payload.get("relation")}
                term, relation = self._mapping(value, resolver, slots)
                value.update(term=term, relation=relation)
                entries = self.listing(workspace_id)["entries"]
                if sum(e["status"] in {"pending", "active"} for e in entries) >= 50:
                    raise ValueError("This workspace's 50 active/pending terminology limit is reached.")
                entry = self.lifecycle.propose_candidate(MemoryScope.PROJECT, KEY, value,
                    ["feedback:" + uuid4().hex], {"scope_key": workspace_id, "proposed_by": actor})
                return {"entry": self._entry(entry)}, 201
            if action not in {"approve", "reject", "revoke", "expire"}:
                raise ValueError("Unknown feedback action.")
            if payload.get("human_confirmed") is not True:
                raise ValueError("Explicit human confirmation is required for this lifecycle action.")
            identifier = payload.get("id")
            if not isinstance(identifier, str):
                raise ValueError("A feedback entry ID is required.")
            entry = (self.store.get_candidate(identifier) if action in {"approve", "reject"}
                     else self.store.get_item(identifier))
            if entry is None or not self._scoped(entry, workspace_id):
                raise ProductApplicationError("FEEDBACK_NOT_FOUND", "Feedback is unavailable in this workspace/state.", status=404)
            if action == "approve":
                term, _ = self._mapping(entry.value, resolver, slots)
                if any(self._scoped(item, workspace_id) and isinstance(item.value.get("term"), str)
                       and " ".join(item.value["term"].casefold().split()) == term
                       for item in self.store.list_active()):
                    raise ProductApplicationError("FEEDBACK_CONFLICT", "An active mapping already uses this term. Revoke it before replacing it.", status=409)
                entry = self.lifecycle.confirm_candidate(identifier, actor)
            elif action == "reject":
                entry = self.lifecycle.reject_candidate(identifier, actor)
            elif action == "revoke":
                entry = self.lifecycle.revoke_item(identifier, actor)
            else:
                entry = self.lifecycle.expire_item(identifier, actor)
            return {"entry": self._entry(entry)}, 200
        except ValueError as error:
            raise ProductApplicationError("INVALID_FEEDBACK", str(error)) from error

    def snapshot(self, workspace_id: str, query: str, resolver: FrozenRelationResolver,
                 slots: list[dict[str, Any]]) -> tuple[FrozenRelationResolver, dict[str, Any]]:
        """Read current active items afresh. No cache survives revocation/restart."""
        self.store.reload()
        grouped = {}
        for item in self.store.list_active():
            if not self._scoped(item, workspace_id):
                continue
            try:
                term, relation = self._mapping(item.value, resolver, slots)
            except ValueError:
                continue  # Legacy, malformed or no-longer-eligible items have no routing power.
            if _mentions(query.casefold(), term):
                grouped.setdefault(term, []).append((relation, item))
        conflicts = sorted(term for term, items in grouped.items() if len({r for r, _ in items}) != 1)
        aliases, used = dict(resolver.aliases), []
        for term, items in grouped.items():
            if term in conflicts:
                continue
            aliases[term] = items[0][0]
            used.extend(self._entry(item) for _, item in items)
        scoped_state = sorted(entry.id + ":" + entry.status.value
            for entry in [*self.store.candidate_history.values(), *self.store.items.values()]
            if self._scoped(entry, workspace_id))
        version = hashlib.sha256("|".join(scoped_state).encode()).hexdigest()
        combined = FrozenRelationResolver(resolver.canonical_relations, aliases=aliases,
            schema_version=resolver.schema_version + "+feedback/v1:" + version)
        return combined, {"schema_version": SCHEMA, "workspace_id": workspace_id,
                          "version": version, "resolver_fingerprint": combined.fingerprint,
                          "used": used, "conflicts": conflicts}
