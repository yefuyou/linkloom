"""SQLite-backed temporal decision and business-relation index."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from linkloom.decision_memory.models import (
    ActionRecord,
    DecisionCandidate,
    DecisionEvolution,
    DecisionRecord,
    DecisionStatus,
)
from linkloom.decision_memory.sources import SourceReferenceRegistry


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS decision (
    decision_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    value TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CURRENT', 'SUPERSEDED')),
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    supersedes_id TEXT REFERENCES decision(decision_id),
    source_episode_id TEXT NOT NULL,
    provenance_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_evidence (
    decision_id TEXT NOT NULL REFERENCES decision(decision_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    evidence_ref TEXT NOT NULL,
    PRIMARY KEY (decision_id, evidence_ref)
);

CREATE TABLE IF NOT EXISTS action (
    action_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    description TEXT NOT NULL,
    owner TEXT NOT NULL,
    deadline TEXT,
    status TEXT NOT NULL,
    source_decision_id TEXT NOT NULL REFERENCES decision(decision_id)
);

CREATE TABLE IF NOT EXISTS action_evidence (
    action_id TEXT NOT NULL REFERENCES action(action_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    evidence_ref TEXT NOT NULL,
    PRIMARY KEY (action_id, evidence_ref)
);

CREATE INDEX IF NOT EXISTS idx_decision_workspace_subject
    ON decision(workspace_id, subject_key);
CREATE INDEX IF NOT EXISTS idx_decision_workspace_subject_validity
    ON decision(workspace_id, subject_key, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_decision_source_episode
    ON decision(source_episode_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_current_subject
    ON decision(workspace_id, subject_key)
    WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_action_source_decision
    ON action(source_decision_id);
"""


class TemporalDecisionStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        source_registry: SourceReferenceRegistry | None = None,
    ) -> None:
        raw_path = str(db_path)
        if raw_path != ":memory:":
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = raw_path
        self._source_registry = source_registry or SourceReferenceRegistry()
        self._connection = sqlite3.connect(raw_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> TemporalDecisionStore:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()

    def _materialize_candidate(
        self,
        candidate: DecisionCandidate,
        *,
        approved_by: str,
    ) -> DecisionRecord:
        if not approved_by.strip():
            raise PermissionError("explicit approval is required to materialize decision memory")
        if not candidate.team_decision_contract_pass or not candidate.grounding_pass:
            raise ValueError("candidate no longer satisfies the write gate")

        decision = candidate.decision
        actions = candidate.actions
        if decision.status is not DecisionStatus.CURRENT or decision.valid_to is not None:
            raise ValueError("new decisions must enter memory as current")
        self._source_registry.validate_decision(decision)
        for action in actions:
            if action.workspace_id != decision.workspace_id:
                raise ValueError("action workspace_id must match its source decision")
            if action.source_decision_id != decision.decision_id:
                raise ValueError("action source_decision_id must match the materialized decision")
            self._source_registry.validate_action(action)

        with self._connection:
            current_row = self._connection.execute(
                """
                SELECT * FROM decision
                WHERE workspace_id = ? AND subject_key = ? AND valid_to IS NULL
                """,
                (decision.workspace_id, decision.subject_key),
            ).fetchone()
            if current_row is None:
                if decision.supersedes_id is not None:
                    raise ValueError("superseded decision does not exist for this workspace and subject")
            else:
                current = self._row_to_decision(current_row)
                if decision.supersedes_id != current.decision_id:
                    raise ValueError("a new current truth must explicitly supersede the existing decision")
                if decision.valid_from <= current.valid_from:
                    raise ValueError("superseding decision must start after the current decision")
                self._connection.execute(
                    """
                    UPDATE decision
                    SET status = ?, valid_to = ?
                    WHERE decision_id = ?
                    """,
                    (
                        DecisionStatus.SUPERSEDED.value,
                        _to_storage_time(decision.valid_from),
                        current.decision_id,
                    ),
                )

            self._connection.execute(
                """
                INSERT INTO decision (
                    decision_id, workspace_id, subject_key, value, status,
                    valid_from, valid_to, supersedes_id, source_episode_id,
                    provenance_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.workspace_id,
                    decision.subject_key,
                    decision.value,
                    decision.status.value,
                    _to_storage_time(decision.valid_from),
                    None,
                    decision.supersedes_id,
                    decision.source_episode_id,
                    decision.provenance_run_id,
                ),
            )
            self._insert_evidence("decision_evidence", "decision_id", decision.decision_id, decision.source_evidence_refs)
            for action in actions:
                self._connection.execute(
                    """
                    INSERT INTO action (
                        action_id, workspace_id, description, owner, deadline,
                        status, source_decision_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.action_id,
                        action.workspace_id,
                        action.description,
                        action.owner,
                        action.deadline,
                        action.status,
                        action.source_decision_id,
                    ),
                )
                self._insert_evidence(
                    "action_evidence",
                    "action_id",
                    action.action_id,
                    action.source_evidence_refs,
                )
        stored = self.get_decision(decision.workspace_id, decision.decision_id)
        if stored is None:
            raise RuntimeError("materialized decision could not be read back")
        return stored

    def get_decision(self, workspace_id: str, decision_id: str) -> DecisionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM decision WHERE workspace_id = ? AND decision_id = ?",
            (workspace_id, decision_id),
        ).fetchone()
        return self._row_to_decision(row) if row is not None else None

    def get_current(self, workspace_id: str, subject_key: str) -> DecisionRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM decision
            WHERE workspace_id = ? AND subject_key = ? AND valid_to IS NULL
            """,
            (workspace_id, subject_key),
        ).fetchone()
        return self._row_to_decision(row) if row is not None else None

    def get_history(self, workspace_id: str, subject_key: str) -> tuple[DecisionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM decision
            WHERE workspace_id = ? AND subject_key = ?
            ORDER BY valid_from, decision_id
            """,
            (workspace_id, subject_key),
        ).fetchall()
        return tuple(self._row_to_decision(row) for row in rows)

    def get_as_of(
        self,
        workspace_id: str,
        subject_key: str,
        as_of: datetime,
    ) -> DecisionRecord | None:
        timestamp = _to_storage_time(as_of)
        row = self._connection.execute(
            """
            SELECT * FROM decision
            WHERE workspace_id = ? AND subject_key = ?
              AND valid_from <= ?
              AND (valid_to IS NULL OR valid_to > ?)
            ORDER BY valid_from DESC, decision_id
            LIMIT 1
            """,
            (workspace_id, subject_key, timestamp, timestamp),
        ).fetchone()
        return self._row_to_decision(row) if row is not None else None

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        as_of: datetime | None = None,
        limit: int = 5,
    ) -> tuple[DecisionRecord, ...]:
        if limit <= 0:
            return ()
        pattern = f"%{query.strip()}%"
        parameters: list[object] = [workspace_id, pattern, pattern]
        validity = "valid_to IS NULL"
        if as_of is not None:
            timestamp = _to_storage_time(as_of)
            validity = "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            parameters.extend((timestamp, timestamp))
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT * FROM decision
            WHERE workspace_id = ?
              AND (subject_key LIKE ? COLLATE NOCASE OR value LIKE ? COLLATE NOCASE)
              AND {validity}
            ORDER BY subject_key, valid_from DESC, decision_id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(self._row_to_decision(row) for row in rows)

    def get_evolution(self, workspace_id: str, subject_key: str) -> tuple[DecisionEvolution, ...]:
        transitions: list[DecisionEvolution] = []
        for current in self.get_history(workspace_id, subject_key):
            if current.supersedes_id is None:
                continue
            previous = self.get_decision(workspace_id, current.supersedes_id)
            if previous is None:
                raise RuntimeError(f"missing superseded decision: {current.supersedes_id}")
            transitions.append(
                DecisionEvolution(
                    previous_decision_id=previous.decision_id,
                    current_decision_id=current.decision_id,
                    workspace_id=current.workspace_id,
                    subject_key=current.subject_key,
                    from_value=previous.value,
                    to_value=current.value,
                    changed_at=current.valid_from,
                    source_evidence_refs=current.source_evidence_refs,
                )
            )
        return tuple(transitions)

    def get_actions(self, workspace_id: str, decision_id: str) -> tuple[ActionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM action
            WHERE workspace_id = ? AND source_decision_id = ?
            ORDER BY action_id
            """,
            (workspace_id, decision_id),
        ).fetchall()
        return tuple(
            ActionRecord(
                action_id=str(row["action_id"]),
                workspace_id=str(row["workspace_id"]),
                description=str(row["description"]),
                owner=str(row["owner"]),
                deadline=str(row["deadline"]) if row["deadline"] is not None else None,
                status=str(row["status"]),
                source_decision_id=str(row["source_decision_id"]),
                source_evidence_refs=self.get_action_evidence(
                    workspace_id,
                    str(row["action_id"]),
                ),
            )
            for row in rows
        )

    def get_decision_evidence(self, workspace_id: str, decision_id: str) -> tuple[str, ...]:
        return self._get_evidence(
            "decision_evidence",
            "decision_id",
            workspace_id,
            decision_id,
            "decision",
        )

    def get_action_evidence(self, workspace_id: str, action_id: str) -> tuple[str, ...]:
        return self._get_evidence(
            "action_evidence",
            "action_id",
            workspace_id,
            action_id,
            "action",
        )

    def schema_indexes(self) -> dict[str, tuple[str, ...]]:
        names = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'decision'"
        ).fetchall()
        indexes: dict[str, tuple[str, ...]] = {}
        for row in names:
            name = str(row["name"])
            columns = self._connection.execute(f'PRAGMA index_info("{name}")').fetchall()
            indexes[name] = tuple(str(column["name"]) for column in columns)
        return indexes

    def _row_to_decision(self, row: sqlite3.Row) -> DecisionRecord:
        decision_id = str(row["decision_id"])
        return DecisionRecord(
            decision_id=decision_id,
            workspace_id=str(row["workspace_id"]),
            subject_key=str(row["subject_key"]),
            value=str(row["value"]),
            status=DecisionStatus(str(row["status"])),
            valid_from=_from_storage_time(str(row["valid_from"])),
            valid_to=(
                _from_storage_time(str(row["valid_to"]))
                if row["valid_to"] is not None
                else None
            ),
            supersedes_id=(str(row["supersedes_id"]) if row["supersedes_id"] is not None else None),
            source_episode_id=str(row["source_episode_id"]),
            source_evidence_refs=self.get_decision_evidence(
                str(row["workspace_id"]),
                decision_id,
            ),
            provenance_run_id=str(row["provenance_run_id"]),
        )

    def _insert_evidence(
        self,
        table: str,
        id_column: str,
        record_id: str,
        evidence_refs: tuple[str, ...],
    ) -> None:
        self._connection.executemany(
            f"INSERT INTO {table} ({id_column}, ordinal, evidence_ref) VALUES (?, ?, ?)",
            (
                (record_id, ordinal, evidence_ref)
                for ordinal, evidence_ref in enumerate(evidence_refs)
            ),
        )

    def _get_evidence(
        self,
        table: str,
        id_column: str,
        workspace_id: str,
        record_id: str,
        owner_table: str,
    ) -> tuple[str, ...]:
        owner_alias = "owner"
        rows = self._connection.execute(
            f"""
            SELECT evidence.evidence_ref
            FROM {table} AS evidence
            JOIN {owner_table} AS {owner_alias}
              ON {owner_alias}.{id_column} = evidence.{id_column}
            WHERE {owner_alias}.workspace_id = ?
              AND evidence.{id_column} = ?
            ORDER BY evidence.ordinal, evidence.evidence_ref
            """,
            (workspace_id, record_id),
        ).fetchall()
        return tuple(str(row["evidence_ref"]) for row in rows)


def _to_storage_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("temporal lookup requires a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _from_storage_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)
