"""Append-only fact records with a host-computed supersession chain."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
import hashlib
import json
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_record (
    fact_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_label TEXT NOT NULL,
    subject_basis TEXT NOT NULL,
    attribute_name TEXT NOT NULL,
    attribute_key TEXT NOT NULL,
    mergeable INTEGER NOT NULL CHECK (mergeable IN (0, 1)),
    value_text TEXT NOT NULL,
    value_normalized TEXT NOT NULL,
    value_kind TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_version_id TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    quote TEXT NOT NULL,
    evidence_sentence TEXT NOT NULL,
    evidence_start INTEGER NOT NULL,
    evidence_end INTEGER NOT NULL,
    key_content TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    review_reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    supersedes_fact_id TEXT,
    superseded_by_fact_id TEXT,
    corroborates_fact_id TEXT,
    UNIQUE (workspace_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_record_slot ON fact_record(workspace_id, slot_key, effective_at);
"""


@dataclass(frozen=True, slots=True)
class FactRecord:
    fact_id: str
    workspace_id: str
    slot_key: str
    subject_id: str
    subject_label: str
    subject_basis: str
    attribute_name: str
    attribute_key: str
    mergeable: bool
    value_text: str
    value_normalized: str
    value_kind: str
    effective_at: datetime
    candidate_id: str
    artifact_id: str
    artifact_version_id: str
    evidence_ref: str
    quote: str
    evidence_sentence: str
    evidence_start: int
    evidence_end: int
    key_content: str  # JSON list of the verified key items
    reviewer_id: str
    review_reason: str
    recorded_at: datetime
    supersedes_fact_id: str | None = None
    superseded_by_fact_id: str | None = None
    corroborates_fact_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        result = {item.name: getattr(self, item.name) for item in fields(self)}
        result["effective_at"] = self.effective_at.isoformat()
        result["recorded_at"] = self.recorded_at.isoformat()
        return result


def slot_key_for(workspace_id: str, subject_id: str, attribute_key: str, mergeable: bool, candidate_id: str) -> str:
    # A non-mergeable fact is its own slot: it never supersedes anything.
    identity = [workspace_id, subject_id, attribute_key] if mergeable else [workspace_id, "single", candidate_id]
    return "fslot_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


def fact_id_for(workspace_id: str, candidate_id: str) -> str:
    return "fact_" + hashlib.sha256(f"{workspace_id}\0{candidate_id}".encode("utf-8")).hexdigest()[:32]


class FactStore:
    """Fact rows live beside decision tables in the same SQLite file.

    The table is created on first use, which only happens with the switch on.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._ready = False

    def _ensure(self) -> None:
        if not self._ready:
            self._connection.executescript(_SCHEMA)
            self._ready = True

    def _exists(self) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_record'").fetchone() is not None

    def by_candidate(self, workspace_id: str, candidate_id: str) -> FactRecord | None:
        if not self._exists():
            return None
        row = self._connection.execute("SELECT * FROM fact_record WHERE workspace_id=? AND candidate_id=?",
                                       (workspace_id, candidate_id)).fetchone()
        return _record(row) if row is not None else None

    def candidate_ids(self, workspace_id: str) -> frozenset[str]:
        if not self._exists():
            return frozenset()
        return frozenset(str(row[0]) for row in self._connection.execute(
            "SELECT candidate_id FROM fact_record WHERE workspace_id=?", (workspace_id,)))

    def list(self, workspace_id: str) -> tuple[FactRecord, ...]:
        if not self._exists():
            return ()
        rows = self._connection.execute(
            "SELECT * FROM fact_record WHERE workspace_id=? ORDER BY slot_key, effective_at, recorded_at, fact_id",
            (workspace_id,)).fetchall()
        return tuple(_record(row) for row in rows)

    def add(self, record: FactRecord) -> FactRecord:
        """Insert idempotently by candidate, then recompute the slot's chain."""
        self._ensure()
        existing = self.by_candidate(record.workspace_id, record.candidate_id)
        if existing is not None:
            return existing
        names = [item.name for item in fields(FactRecord)]
        values = [_storage(getattr(record, name)) for name in names]
        with self._connection:
            self._connection.execute(
                f"INSERT INTO fact_record ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", values)
            self._rechain(record.workspace_id, record.slot_key)
        stored = self.by_candidate(record.workspace_id, record.candidate_id)
        assert stored is not None
        return stored

    def _rechain(self, workspace_id: str, slot_key: str) -> None:
        # Order by source time; recording order only breaks ties. A repeated
        # value corroborates the record it repeats instead of superseding it,
        # so late-arriving evidence never reorders history by import time.
        rows = self._connection.execute(
            "SELECT fact_id, value_normalized FROM fact_record WHERE workspace_id=? AND slot_key=? "
            "ORDER BY effective_at, recorded_at, fact_id", (workspace_id, slot_key)).fetchall()
        links: dict[str, list[str | None]] = {str(row[0]): [None, None, None] for row in rows}
        head: tuple[str, str] | None = None  # (fact_id, normalized value) of the newest distinct value
        for fact_id, value in ((str(row[0]), str(row[1])) for row in rows):
            if head is not None and head[1] == value:
                links[fact_id][2] = head[0]
                continue
            if head is not None:
                links[fact_id][0] = head[0]
                links[head[0]][1] = fact_id
            head = (fact_id, value)
        for fact_id, (supersedes, superseded_by, corroborates) in links.items():
            self._connection.execute(
                "UPDATE fact_record SET supersedes_fact_id=?, superseded_by_fact_id=?, corroborates_fact_id=? "
                "WHERE fact_id=?", (supersedes, superseded_by, corroborates, fact_id))


def value_at(records: tuple[FactRecord, ...] | list[FactRecord], as_of: datetime) -> FactRecord | None:
    """The slot's value in force at ``as_of``: newest distinct value not after it."""
    in_force = [record for record in records
                if record.corroborates_fact_id is None and record.effective_at <= as_of]
    return max(in_force, key=lambda record: (record.effective_at, record.recorded_at, record.fact_id), default=None)


def _storage(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


def _record(row: sqlite3.Row | tuple) -> FactRecord:
    data = dict(row) if isinstance(row, sqlite3.Row) else dict(zip([item.name for item in fields(FactRecord)], row))
    data["mergeable"] = bool(data["mergeable"])
    data["effective_at"] = datetime.fromisoformat(str(data["effective_at"]))
    data["recorded_at"] = datetime.fromisoformat(str(data["recorded_at"]))
    return FactRecord(**{item.name: data[item.name] for item in fields(FactRecord)})
