import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")


class MemoryScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    WORKFLOW = "workflow"
    THREAD = "thread"


class MemoryStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EventType(str, Enum):
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_CONFIRMED = "candidate_confirmed"
    CANDIDATE_REJECTED = "candidate_rejected"
    ITEM_REVOKED = "item_revoked"
    ITEM_EXPIRED = "item_expired"
    ITEM_SUPERSEDED = "item_superseded"


def _ensure_json(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-safe") from exc


def _ensure_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _ensure_scope(value: MemoryScope) -> MemoryScope:
    try:
        return value if isinstance(value, MemoryScope) else MemoryScope(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("scope must be global, project, workflow, or thread") from exc


def _ensure_key(value: str) -> None:
    if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
        raise ValueError("key must be a short stable identifier")


def _ensure_refs(value: List[str]) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError("source_refs must be a non-empty list")
    if any(not isinstance(ref, str) or not _REF_RE.fullmatch(ref) for ref in value):
        raise ValueError("source_refs must contain opaque safe identifiers")
    if len(set(value)) != len(value):
        raise ValueError("source_refs must not contain duplicates")


def _ensure_timestamp(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or "T" not in value:
        raise ValueError(f"{name} must be an ISO-8601 timestamp")


def _ensure_actor(value: str) -> None:
    if not isinstance(value, str) or not _ACTOR_RE.fullmatch(value):
        raise ValueError("actor must be a short stable identifier")


@dataclass
class MemoryCandidate:
    id: str
    scope: MemoryScope
    key: str
    value: Dict[str, Any]
    source_refs: List[str]
    created_at: str
    status: MemoryStatus = MemoryStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_digest(self.id, "id")
        self.scope = _ensure_scope(self.scope)
        _ensure_key(self.key)
        if not isinstance(self.value, dict):
            raise ValueError("value must be a JSON object")
        _ensure_json(self.value, "value")
        _ensure_refs(self.source_refs)
        _ensure_timestamp(self.created_at, "created_at")
        try:
            self.status = self.status if isinstance(self.status, MemoryStatus) else MemoryStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid memory candidate status") from exc
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a JSON object")
        _ensure_json(self.metadata, "metadata")


@dataclass
class MemoryItem:
    id: str
    candidate_id: str
    scope: MemoryScope
    key: str
    value: Dict[str, Any]
    source_refs: List[str]
    confirmed_at: str
    actor: str
    status: MemoryStatus = MemoryStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_digest(self.id, "id")
        _ensure_digest(self.candidate_id, "candidate_id")
        self.scope = _ensure_scope(self.scope)
        _ensure_key(self.key)
        if not isinstance(self.value, dict):
            raise ValueError("value must be a JSON object")
        _ensure_json(self.value, "value")
        _ensure_refs(self.source_refs)
        _ensure_timestamp(self.confirmed_at, "confirmed_at")
        _ensure_actor(self.actor)
        try:
            self.status = self.status if isinstance(self.status, MemoryStatus) else MemoryStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid memory item status") from exc
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a JSON object")
        _ensure_json(self.metadata, "metadata")


@dataclass
class MemoryEvent:
    event_id: str
    timestamp: str
    event_type: EventType
    payload: Dict[str, Any]

    def __post_init__(self) -> None:
        _ensure_digest(self.event_id, "event_id")
        _ensure_timestamp(self.timestamp, "timestamp")
        try:
            self.event_type = self.event_type if isinstance(self.event_type, EventType) else EventType(self.event_type)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid memory event type") from exc
        if not isinstance(self.payload, dict):
            raise ValueError("event payload must be a JSON object")
        _ensure_json(self.payload, "event payload")
