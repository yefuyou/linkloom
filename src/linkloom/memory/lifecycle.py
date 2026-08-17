from typing import Any, Dict, List, Optional
from .models import MemoryCandidate, MemoryItem, MemoryScope, MemoryStatus
from .store import MemoryStore


class MemoryLifecycle:
    """Domain service managing the lifecycle of memory items."""

    def __init__(self, store: MemoryStore):
        self.store = store

    def propose_candidate(
        self,
        scope: MemoryScope,
        key: str,
        value: Dict[str, Any],
        source_refs: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryCandidate:
        """Propose a new memory candidate. It remains pending until confirmed."""
        return self.store.create_candidate(scope, key, value, source_refs, metadata)

    def confirm_candidate(
        self, candidate_id: str, actor: str, edited_value: Optional[Dict[str, Any]] = None
    ) -> MemoryItem:
        """Explicitly confirm a pending candidate, making it an active memory item."""
        return self.store.confirm_candidate(candidate_id, actor, edited_value)

    def reject_candidate(self, candidate_id: str, actor: str) -> MemoryCandidate:
        """Reject a pending candidate."""
        return self.store.reject_candidate(candidate_id, actor)

    def revoke_item(self, item_id: str, actor: str) -> MemoryItem:
        """Revoke an active memory item."""
        return self.store.revoke_item(item_id, actor)

    def expire_item(self, item_id: str, actor: str) -> MemoryItem:
        """Expire an active memory item."""
        return self.store.expire_item(item_id, actor)

    def supersede_item(self, item_id: str, actor: str) -> MemoryItem:
        """Supersede an active memory item with a newer one."""
        return self.store.supersede_item(item_id, actor)

    def list_candidates(
        self, status: Optional[MemoryStatus] = None, *, include_terminal: bool = False
    ) -> List[MemoryCandidate]:
        """List candidates, optionally filtered by status."""
        return self.store.list_candidates(status, include_terminal=include_terminal)

    def list_active(self) -> List[MemoryItem]:
        """List all active memory items."""
        return self.store.list_active()
