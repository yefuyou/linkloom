"""Tier 2 Persistent / Profile Memory with strict store -> read -> prompt boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from .lifecycle import MemoryLifecycle
from .models import MemoryCandidate, MemoryItem, MemoryScope, MemoryStatus
from .policy import MemoryPolicyViolation, validate_actor
from .retriever import MemoryRetriever
from .store import MemoryStore


class Tier2Category(StrEnum):
    GLOSSARY = "glossary"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    CONFIRMED_PROFILE = "confirmed_profile"
    RESTRICTED_IDENTITY = "restricted_identity"


class Tier2Exposure(StrEnum):
    PROMPT_ALLOWED = "prompt_allowed"
    READ_AUTHORIZED_ONLY = "read_authorized_only"
    STORE_ONLY = "store_only"


_CATEGORY_KEY = {
    Tier2Category.GLOSSARY: "tier2.glossary",
    Tier2Category.PREFERENCE: "tier2.preference",
    Tier2Category.CONSTRAINT: "tier2.constraint",
    Tier2Category.CONFIRMED_PROFILE: "tier2.profile",
    Tier2Category.RESTRICTED_IDENTITY: "tier2.restricted_identity",
}

_DEFAULT_EXPOSURE = {
    Tier2Category.GLOSSARY: Tier2Exposure.PROMPT_ALLOWED,
    Tier2Category.PREFERENCE: Tier2Exposure.PROMPT_ALLOWED,
    Tier2Category.CONSTRAINT: Tier2Exposure.PROMPT_ALLOWED,
    Tier2Category.CONFIRMED_PROFILE: Tier2Exposure.READ_AUTHORIZED_ONLY,
    Tier2Category.RESTRICTED_IDENTITY: Tier2Exposure.STORE_ONLY,
}


class Tier2MemoryService:
    """Manages workspace-scoped Tier 2 memory with human confirmation and exposure gates.

    Backend capability only: no HTTP route or Ask path calls this service yet.
    """

    def __init__(self, log_path: str) -> None:
        self.store = MemoryStore(log_path)
        self.lifecycle = MemoryLifecycle(self.store)

    def reload(self) -> None:
        self.store.reload()

    @staticmethod
    def _scoped(entry: MemoryCandidate | MemoryItem, workspace_id: str) -> bool:
        return (
            entry.scope is MemoryScope.PROJECT
            and entry.metadata.get("scope_key") == workspace_id
            and entry.metadata.get("tier") == "tier2"
        )

    @staticmethod
    def _require_human(human_confirmed: bool) -> None:
        if human_confirmed is not True:
            raise MemoryPolicyViolation("Explicit human confirmation is required.")

    @staticmethod
    def _resolve_exposure(category: Tier2Category, exposure: Tier2Exposure | None) -> Tier2Exposure:
        resolved = exposure if exposure is not None else _DEFAULT_EXPOSURE[category]
        if category is Tier2Category.RESTRICTED_IDENTITY and resolved is Tier2Exposure.PROMPT_ALLOWED:
            raise MemoryPolicyViolation("Restricted identity memory can never be marked prompt_allowed.")
        return resolved

    def propose(
        self,
        workspace_id: str,
        *,
        category: Tier2Category | str,
        value: dict[str, Any],
        source_refs: list[str],
        actor: str,
        exposure: Tier2Exposure | str | None = None,
    ) -> MemoryCandidate:
        self.store.reload()
        validate_actor(actor)
        cat = Tier2Category(category)
        exp = self._resolve_exposure(cat, Tier2Exposure(exposure) if exposure is not None else None)
        return self.lifecycle.propose_candidate(
            MemoryScope.PROJECT,
            _CATEGORY_KEY[cat],
            dict(value),
            list(source_refs),
            metadata={
                "tier": "tier2",
                "scope_key": workspace_id,
                "category": cat.value,
                "exposure": exp.value,
                "proposed_by": actor,
            },
        )

    def confirm(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        actor: str,
        human_confirmed: bool = True,
        edited_value: dict[str, Any] | None = None,
    ) -> MemoryItem:
        self.store.reload()
        self._require_human(human_confirmed)
        validate_actor(actor)
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None or not self._scoped(candidate, workspace_id):
            raise KeyError(f"Tier 2 candidate not found in workspace: {candidate_id}")
        return self.lifecycle.confirm_candidate(candidate_id, actor, edited_value=edited_value)

    def edit_active(
        self,
        workspace_id: str,
        item_id: str,
        *,
        actor: str,
        edited_value: dict[str, Any],
        human_confirmed: bool = True,
    ) -> MemoryItem:
        """Edit an active Tier 2 item by superseding it with a newly confirmed version."""
        self.store.reload()
        self._require_human(human_confirmed)
        validate_actor(actor)
        item = self.store.get_item(item_id)
        if item is None or not self._scoped(item, workspace_id) or item.status is not MemoryStatus.ACTIVE:
            raise KeyError(f"Active Tier 2 item not found in workspace: {item_id}")
        # Persist and validate the replacement first, so a rejected value leaves the
        # current item active. If confirmation then fails, the replacement remains a
        # pending candidate that can still be confirmed.
        new_candidate = self.lifecycle.propose_candidate(
            item.scope,
            item.key,
            dict(edited_value),
            [f"edit:{item_id}:{uuid4().hex[:8]}"],
            metadata={**dict(item.metadata), "edited_from": item_id, "edited_by": actor},
        )
        self.lifecycle.supersede_item(item_id, actor)
        return self.lifecycle.confirm_candidate(new_candidate.id, actor)

    def reject(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        actor: str,
        human_confirmed: bool = True,
    ) -> MemoryCandidate:
        self.store.reload()
        self._require_human(human_confirmed)
        validate_actor(actor)
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None or not self._scoped(candidate, workspace_id):
            raise KeyError(f"Tier 2 candidate not found in workspace: {candidate_id}")
        return self.lifecycle.reject_candidate(candidate_id, actor)

    def revoke(
        self,
        workspace_id: str,
        item_id: str,
        *,
        actor: str,
        human_confirmed: bool = True,
    ) -> MemoryItem:
        self.store.reload()
        self._require_human(human_confirmed)
        validate_actor(actor)
        item = self.store.get_item(item_id)
        if item is None or not self._scoped(item, workspace_id):
            raise KeyError(f"Tier 2 item not found in workspace: {item_id}")
        return self.lifecycle.revoke_item(item_id, actor)

    def list_entries(
        self,
        workspace_id: str,
        *,
        category: Tier2Category | str | None = None,
        include_inactive: bool = True,
    ) -> list[dict[str, Any]]:
        self.store.reload()
        cat_val = Tier2Category(category).value if category is not None else None
        items = [
            item
            for item in self.store.items.values()
            if self._scoped(item, workspace_id)
            and (cat_val is None or item.metadata.get("category") == cat_val)
            and (include_inactive or item.status is MemoryStatus.ACTIVE)
        ]
        confirmed_ids = {item.candidate_id for item in items}
        candidates = (
            [
                cand
                for cand in self.store.candidate_history.values()
                if self._scoped(cand, workspace_id)
                and cand.id not in confirmed_ids
                and (cat_val is None or cand.metadata.get("category") == cat_val)
            ]
            if include_inactive
            else []
        )
        return [
            {
                "id": entry.id,
                "candidate_id": entry.candidate_id if isinstance(entry, MemoryItem) else entry.id,
                "category": entry.metadata.get("category"),
                "exposure": entry.metadata.get("exposure"),
                "status": entry.status.value,
                "value": dict(entry.value),
                "workspace_id": workspace_id,
            }
            for entry in [*candidates, *items]
        ]

    def read_authorized(
        self,
        workspace_id: str,
        *,
        category: Tier2Category | str | None = None,
        allow_restricted: bool = False,
    ) -> list[MemoryItem]:
        """Workspace-scoped read filter: never returns pending/revoked items or STORE_ONLY items by default.

        This is a storage filter, not an authorization check. ``workspace_id`` and
        ``allow_restricted`` must come from a caller that has already verified the
        user's identity and workspace membership; ``actor`` strings elsewhere in
        this service are only format-checked audit labels.
        """
        self.store.reload()
        cat_val = Tier2Category(category).value if category is not None else None
        results: list[MemoryItem] = []
        for item in self.store.list_active():
            if not self._scoped(item, workspace_id):
                continue
            if cat_val is not None and item.metadata.get("category") != cat_val:
                continue
            exposure = item.metadata.get("exposure", Tier2Exposure.READ_AUTHORIZED_ONLY.value)
            if exposure == Tier2Exposure.STORE_ONLY.value and not allow_restricted:
                continue
            results.append(item)
        return results

    def build_prompt_context(
        self,
        workspace_id: str,
        *,
        query: str = "",
        max_items: int = 10,
        max_chars: int = 2000,
    ) -> list[dict[str, Any]]:
        """Prompt-injection boundary: only PROMPT_ALLOWED active items may enter model prompts."""
        self.store.reload()
        eligible = [
            item
            for item in self.store.list_active()
            if self._scoped(item, workspace_id)
            and item.metadata.get("exposure") == Tier2Exposure.PROMPT_ALLOWED.value
            and item.metadata.get("category") != Tier2Category.RESTRICTED_IDENTITY.value
        ]
        retriever = MemoryRetriever(eligible)
        return retriever.retrieve(
            query=query,
            scope=MemoryScope.PROJECT,
            scope_key=workspace_id,
            max_items=max_items,
            max_chars=max_chars,
            include_global=False,
            require_scope_key=True,
        )
