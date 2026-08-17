import hashlib
import json
from typing import Any, Dict, List, Optional
from .models import MemoryItem, MemoryScope, MemoryStatus


class MemoryRetriever:
    """Retrieves and ranks active memory items bounded by scope and budget."""

    def __init__(self, active_items: List[MemoryItem]):
        self.active_items = active_items

    def retrieve(
        self,
        query: str,
        scope: MemoryScope,
        scope_key: Optional[str] = None,
        max_items: int = 5,
        max_chars: int = 4000,
    ) -> List[Dict[str, Any]]:
        if max_items < 0 or max_chars < 0:
            raise ValueError("memory retrieval budgets must be non-negative")
        filtered = []
        for item in self.active_items:
            if item.status is not MemoryStatus.ACTIVE:
                continue
            if item.scope not in (scope, MemoryScope.GLOBAL):
                continue
            if scope_key is not None and item.scope != MemoryScope.GLOBAL:
                if item.metadata.get("scope_key") and item.metadata.get("scope_key") != scope_key:
                    continue
            filtered.append(item)

        def score(item: MemoryItem) -> int:
            q_lower = query.lower()
            q_tokens = set(q_lower.split())
            v_str = json.dumps(item.value).lower()
            overlap = sum(1 for t in q_tokens if t in v_str)
            key_match = 10 if item.key.lower() in q_lower else 0
            return overlap + key_match

        # Rank deterministically by score descending, then key ascending
        ranked = sorted(filtered, key=lambda x: (-score(x), x.key))

        results = []
        current_chars = 0
        for item in ranked:
            if len(results) >= max_items:
                break

            value_str = json.dumps(item.value, ensure_ascii=False)
            value_sha256 = hashlib.sha256(
                json.dumps(item.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

            if current_chars + len(value_str) > max_chars:
                continue

            current_chars += len(value_str)
            results.append({
                "memory_id": item.id,
                "scope": item.scope.value,
                "key": item.key,
                "value": item.value,
                "value_sha256": value_sha256,
                "source_refs": list(item.source_refs),
            })

        return results
