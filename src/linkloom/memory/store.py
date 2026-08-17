import copy
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import EventType, MemoryCandidate, MemoryEvent, MemoryItem, MemoryScope, MemoryStatus
from .policy import (
    MemoryPolicyViolation,
    validate_actor,
    validate_candidate_value,
    validate_memory_input,
)


def generate_id(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Append-only local memory store with deterministic replayable state."""

    def __init__(self, log_path: str):
        self.log_path = os.fspath(log_path)
        self.candidates: Dict[str, MemoryCandidate] = {}
        self.candidate_history: Dict[str, MemoryCandidate] = {}
        self.active_items: Dict[str, MemoryItem] = {}
        self.items: Dict[str, MemoryItem] = {}
        self.events: List[MemoryEvent] = []
        self.reload()

    @staticmethod
    def _candidate_payload(candidate: MemoryCandidate) -> Dict[str, Any]:
        return {
            "id": candidate.id,
            "scope": candidate.scope.value,
            "key": candidate.key,
            "value": copy.deepcopy(candidate.value),
            "source_refs": list(candidate.source_refs),
            "created_at": candidate.created_at,
            "status": candidate.status.value,
            "metadata": copy.deepcopy(candidate.metadata),
        }

    @staticmethod
    def _item_payload(item: MemoryItem) -> Dict[str, Any]:
        return {
            "id": item.id,
            "candidate_id": item.candidate_id,
            "scope": item.scope.value,
            "key": item.key,
            "value": copy.deepcopy(item.value),
            "source_refs": list(item.source_refs),
            "confirmed_at": item.confirmed_at,
            "actor": item.actor,
            "status": item.status.value,
            "metadata": copy.deepcopy(item.metadata),
        }

    def _append_event(self, event_type: EventType, payload: Dict[str, Any]) -> MemoryEvent:
        timestamp = now_iso()
        event_id = generate_id(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + timestamp
            + uuid.uuid4().hex
        )
        event = MemoryEvent(event_id=event_id, timestamp=timestamp, event_type=event_type, payload=payload)
        directory = os.path.dirname(os.path.abspath(self.log_path))
        os.makedirs(directory, exist_ok=True)
        line = json.dumps(
            {
                "event_id": event.event_id,
                "timestamp": event.timestamp,
                "event_type": event.event_type.value,
                "payload": event.payload,
            },
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(fd, "a", encoding="utf-8", newline="") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            # fdopen owns the descriptor after it succeeds; do not hide the original error.
            raise
        self._apply_event(event)
        self.events.append(event)
        return event

    def _apply_event(self, event: MemoryEvent) -> None:
        payload = event.payload
        if event.event_type == EventType.CANDIDATE_CREATED:
            candidate = MemoryCandidate(
                id=payload["id"],
                scope=payload["scope"],
                key=payload["key"],
                value=payload["value"],
                source_refs=payload["source_refs"],
                created_at=payload["created_at"],
                status=payload.get("status", MemoryStatus.PENDING.value),
                metadata=payload.get("metadata", {}),
            )
            validate_memory_input(candidate.key, candidate.value, candidate.source_refs, candidate.metadata)
            self.candidate_history[candidate.id] = candidate
            if candidate.status == MemoryStatus.PENDING:
                self.candidates[candidate.id] = candidate
            return

        if event.event_type == EventType.CANDIDATE_CONFIRMED:
            candidate_id = payload["candidate_id"]
            candidate = self.candidate_history.get(candidate_id)
            if candidate is None or candidate.status != MemoryStatus.PENDING:
                return
            edited_value = payload.get("edited_value")
            value = copy.deepcopy(candidate.value if edited_value is None else edited_value)
            validate_memory_input(candidate.key, value, candidate.source_refs, candidate.metadata)
            candidate.status = MemoryStatus.SUPERSEDED
            self.candidates.pop(candidate_id, None)
            item = MemoryItem(
                id=payload["item_id"],
                candidate_id=candidate.id,
                scope=candidate.scope,
                key=candidate.key,
                value=value,
                source_refs=list(candidate.source_refs),
                confirmed_at=payload["confirmed_at"],
                actor=payload["actor"],
                status=MemoryStatus.ACTIVE,
                metadata=copy.deepcopy(candidate.metadata),
            )
            self.items[item.id] = item
            self.active_items[item.id] = item
            return

        if event.event_type == EventType.CANDIDATE_REJECTED:
            candidate = self.candidate_history.get(payload["candidate_id"])
            if candidate is not None and candidate.status == MemoryStatus.PENDING:
                candidate.status = MemoryStatus.REJECTED
                self.candidates.pop(candidate.id, None)
            return

        if event.event_type in (EventType.ITEM_REVOKED, EventType.ITEM_EXPIRED, EventType.ITEM_SUPERSEDED):
            item = self.items.get(payload["item_id"])
            if item is None or item.status != MemoryStatus.ACTIVE:
                return
            if event.event_type == EventType.ITEM_REVOKED:
                item.status = MemoryStatus.REVOKED
            elif event.event_type == EventType.ITEM_EXPIRED:
                item.status = MemoryStatus.EXPIRED
            else:
                item.status = MemoryStatus.SUPERSEDED
            self.active_items.pop(item.id, None)

    def reload(self) -> None:
        self.candidates.clear()
        self.candidate_history.clear()
        self.active_items.clear()
        self.items.clear()
        self.events.clear()
        if not os.path.exists(self.log_path):
            return
        with open(self.log_path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    # A torn final append can be safely ignored and repaired by the next append.
                    continue
                raise
            event = MemoryEvent(
                event_id=data["event_id"],
                timestamp=data["timestamp"],
                event_type=data["event_type"],
                payload=data["payload"],
            )
            self._apply_event(event)
            self.events.append(event)

    def create_candidate(
        self,
        scope: MemoryScope,
        key: str,
        value: Dict[str, Any],
        source_refs: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryCandidate:
        try:
            scope = scope if isinstance(scope, MemoryScope) else MemoryScope(scope)
        except (TypeError, ValueError) as exc:
            raise MemoryPolicyViolation("invalid memory scope") from exc
        metadata = {} if metadata is None else copy.deepcopy(metadata)
        value = copy.deepcopy(value)
        source_refs = list(source_refs)
        validate_memory_input(key, value, source_refs, metadata)
        created_at = now_iso()
        candidate_id = generate_id(
            json.dumps(
                {"scope": scope.value, "key": key, "value": value, "source_refs": source_refs},
                sort_keys=True,
                ensure_ascii=False,
            )
            + created_at
            + uuid.uuid4().hex
        )
        payload = {
            "id": candidate_id,
            "scope": scope.value,
            "key": key,
            "value": value,
            "source_refs": source_refs,
            "created_at": created_at,
            "status": MemoryStatus.PENDING.value,
            "metadata": metadata,
        }
        self._append_event(EventType.CANDIDATE_CREATED, payload)
        return self.candidates[candidate_id]

    def get_candidate(self, candidate_id: str, *, include_terminal: bool = False) -> Optional[MemoryCandidate]:
        return self.candidate_history.get(candidate_id) if include_terminal else self.candidates.get(candidate_id)

    def list_candidates(
        self,
        status: Optional[MemoryStatus] = None,
        *,
        include_terminal: bool = False,
    ) -> List[MemoryCandidate]:
        pool = self.candidate_history.values() if include_terminal or status is not None else self.candidates.values()
        if status is None:
            return list(pool)
        wanted = status if isinstance(status, MemoryStatus) else MemoryStatus(status)
        return [candidate for candidate in pool if candidate.status == wanted]

    def confirm_candidate(
        self,
        candidate_id: str,
        actor: str,
        edited_value: Optional[Dict[str, Any]] = None,
    ) -> MemoryItem:
        validate_actor(actor)
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ValueError("pending candidate not found")
        if edited_value is not None:
            validate_candidate_value(edited_value)
        confirmed_at = now_iso()
        item_id = generate_id(candidate_id + actor + confirmed_at + uuid.uuid4().hex)
        payload: Dict[str, Any] = {
            "item_id": item_id,
            "candidate_id": candidate_id,
            "actor": actor,
            "confirmed_at": confirmed_at,
        }
        if edited_value is not None:
            payload["edited_value"] = copy.deepcopy(edited_value)
        self._append_event(EventType.CANDIDATE_CONFIRMED, payload)
        return self.active_items[item_id]

    def reject_candidate(self, candidate_id: str, actor: str) -> MemoryCandidate:
        validate_actor(actor)
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ValueError("pending candidate not found")
        self._append_event(
            EventType.CANDIDATE_REJECTED,
            {"candidate_id": candidate_id, "actor": actor, "rejected_at": now_iso()},
        )
        return self.candidate_history[candidate_id]

    def list_active(self) -> List[MemoryItem]:
        return list(self.active_items.values())

    def get_item(self, item_id: str, *, include_terminal: bool = False) -> Optional[MemoryItem]:
        if include_terminal:
            return self.items.get(item_id)
        return self.active_items.get(item_id)

    def revoke_item(self, item_id: str, actor: str) -> MemoryItem:
        validate_actor(actor)
        item = self.active_items.get(item_id)
        if item is None:
            raise ValueError("active item not found")
        self._append_event(EventType.ITEM_REVOKED, {"item_id": item_id, "actor": actor, "revoked_at": now_iso()})
        return self.items[item_id]

    def expire_item(self, item_id: str, actor: str) -> MemoryItem:
        validate_actor(actor)
        if item_id not in self.active_items:
            raise ValueError("active item not found")
        self._append_event(EventType.ITEM_EXPIRED, {"item_id": item_id, "actor": actor, "expired_at": now_iso()})
        return self.items[item_id]

    def supersede_item(self, item_id: str, actor: str) -> MemoryItem:
        validate_actor(actor)
        if item_id not in self.active_items:
            raise ValueError("active item not found")
        self._append_event(EventType.ITEM_SUPERSEDED, {"item_id": item_id, "actor": actor, "superseded_at": now_iso()})
        return self.items[item_id]
