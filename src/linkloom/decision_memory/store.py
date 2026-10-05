"""SQLite-backed temporal decision and business-relation index."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from linkloom.decision_memory.models import (
    ActionRecord,
    DecisionCandidate,
    DecisionEvolution,
    DecisionMemoryState,
    DecisionRecord,
    DecisionStatus,
)
from linkloom.decision_memory.policy import (
    DecisionMemoryWritePolicy,
    DecisionWriteAction,
    DecisionWriteResult,
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
    memory_state TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (memory_state IN ('CANDIDATE', 'ACTIVE', 'SUPERSEDED', 'STALE', 'INVALIDATED', 'NEEDS_REVALIDATION')),
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
    source_hash TEXT,
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
    source_hash TEXT,
    PRIMARY KEY (action_id, evidence_ref)
);

CREATE TABLE IF NOT EXISTS decision_candidate (
    candidate_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    source_evidence_refs TEXT NOT NULL,
    source_hashes TEXT NOT NULL DEFAULT '{}',
    provenance_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CANDIDATE', 'ACTIVE', 'SUPERSEDED', 'STALE', 'INVALIDATED', 'NEEDS_REVALIDATION')),
    outcome TEXT,
    decision_id TEXT REFERENCES decision(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_workspace_subject
    ON decision(workspace_id, subject_key);
CREATE INDEX IF NOT EXISTS idx_decision_workspace_subject_validity
    ON decision(workspace_id, subject_key, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_decision_source_episode
    ON decision(source_episode_id);
CREATE INDEX IF NOT EXISTS idx_action_source_decision
    ON action(source_decision_id);
CREATE INDEX IF NOT EXISTS idx_decision_candidate_workspace_subject_state
    ON decision_candidate(workspace_id, subject_key, status);
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
        self._ensure_column(
            "decision",
            "memory_state",
            "TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (memory_state IN ('CANDIDATE', 'ACTIVE', 'SUPERSEDED', 'STALE', 'INVALIDATED', 'NEEDS_REVALIDATION'))",
        )
        self._ensure_column("decision_evidence", "source_hash", "TEXT")
        self._ensure_column("action_evidence", "source_hash", "TEXT")
        self._ensure_column(
            "decision_candidate",
            "source_hashes",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        # Recreate the partial uniqueness guard so stale/invalidated records do
        # not block a later valid current truth.
        self._connection.execute("DROP INDEX IF EXISTS ux_decision_current_subject")
        self._connection.execute(
            "CREATE UNIQUE INDEX ux_decision_current_subject "
            "ON decision(workspace_id, subject_key) "
            "WHERE valid_to IS NULL AND memory_state = 'ACTIVE'"
        )
        self._connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute(f'PRAGMA table_info("{table}")')
        }
        if column not in columns:
            self._connection.execute(
                f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}'
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> TemporalDecisionStore:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()

    def _register_candidate(self, candidate: DecisionCandidate) -> None:
        decision = candidate.decision
        payload = (
            candidate.candidate_id,
            decision.workspace_id,
            decision.subject_key,
            decision.value,
            json.dumps(decision.source_evidence_refs, ensure_ascii=False),
            json.dumps(
                {
                    evidence_ref: self._source_registry.evidence_hash(
                        decision.workspace_id, evidence_ref
                    )
                    for evidence_ref in decision.source_evidence_refs
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            decision.provenance_run_id,
            _to_storage_time(candidate.created_at),
            DecisionMemoryState.CANDIDATE.value,
        )
        with self._connection:
            existing = self._connection.execute(
                "SELECT * FROM decision_candidate WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
            if existing is not None:
                existing_identity = (
                    str(existing["workspace_id"]),
                    str(existing["subject_key"]),
                    str(existing["proposed_value"]),
                    str(existing["source_evidence_refs"]),
                    str(existing["provenance_run_id"]),
                )
                candidate_identity = (payload[1], payload[2], payload[3], payload[4], payload[6])
                if existing_identity != candidate_identity:
                    raise ValueError("candidate_id is already registered with different content")
                return
            self._connection.execute(
                """
                INSERT INTO decision_candidate (
                    candidate_id, workspace_id, subject_key, proposed_value,
                    source_evidence_refs, source_hashes, provenance_run_id,
                    created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    def get_candidate_state(self, candidate_id: str) -> DecisionMemoryState | None:
        row = self._connection.execute(
            "SELECT status FROM decision_candidate WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return DecisionMemoryState(str(row["status"])) if row is not None else None

    def reconcile(self, source_inventory):
        """Reconcile temporal memory against a workspace-scoped source snapshot."""
        from linkloom.decision_memory.reconciliation import DecisionReconciler

        return DecisionReconciler(self).reconcile(source_inventory)

    def _set_decision_memory_state(
        self,
        decision_id: str,
        state: DecisionMemoryState,
    ) -> None:
        self._connection.execute(
            "UPDATE decision SET memory_state = ? WHERE decision_id = ?",
            (state.value, decision_id),
        )

    def _set_candidate_state(
        self,
        candidate_id: str,
        state: DecisionMemoryState,
        *,
        outcome: str | None = None,
        decision_id: str | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE decision_candidate
                SET status = ?, outcome = ?, decision_id = ?
                WHERE candidate_id = ?
                """,
                (state.value, outcome, decision_id, candidate_id),
            )

    def _materialize_candidate_with_policy(
        self,
        candidate: DecisionCandidate,
        *,
        approved_by: str,
        policy: DecisionMemoryWritePolicy,
    ) -> DecisionWriteResult:
        self._register_candidate(candidate)
        decision = candidate.decision
        candidate_row = self._connection.execute(
            "SELECT status, source_hashes FROM decision_candidate WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()
        if candidate_row is None:
            raise RuntimeError("registered decision candidate could not be read back")
        candidate_state = DecisionMemoryState(str(candidate_row["status"]))
        if candidate_state in {
            DecisionMemoryState.STALE,
            DecisionMemoryState.INVALIDATED,
            DecisionMemoryState.NEEDS_REVALIDATION,
        }:
            return DecisionWriteResult(
                candidate_id=candidate.candidate_id,
                action=DecisionWriteAction.KEEP_CANDIDATE,
                reason="candidate_requires_revalidation",
                decision=None,
            )
        open_row = self._connection.execute(
            """
            SELECT * FROM decision
            WHERE workspace_id = ? AND subject_key = ? AND valid_to IS NULL
            ORDER BY valid_from DESC, decision_id
            LIMIT 1
            """,
            (decision.workspace_id, decision.subject_key),
        ).fetchone()
        prior = self._row_to_decision(open_row) if open_row is not None else None
        current = self.get_current(decision.workspace_id, decision.subject_key)
        source_hashes = json.loads(str(candidate_row["source_hashes"]))
        changed_refs = [
            evidence_ref
            for evidence_ref in decision.source_evidence_refs
            if source_hashes.get(evidence_ref) is not None
            and self._source_registry.evidence_hash(decision.workspace_id, evidence_ref)
            != source_hashes[evidence_ref]
        ]
        unchanged_refs = [
            evidence_ref
            for evidence_ref in decision.source_evidence_refs
            if self._source_registry.has_evidence(decision.workspace_id, evidence_ref)
            and evidence_ref not in changed_refs
        ]
        if changed_refs:
            state = (
                DecisionMemoryState.NEEDS_REVALIDATION
                if unchanged_refs
                else DecisionMemoryState.INVALIDATED
            )
            self._set_candidate_state(
                candidate.candidate_id,
                state,
                outcome=DecisionWriteAction.KEEP_CANDIDATE.value,
            )
            return DecisionWriteResult(
                candidate_id=candidate.candidate_id,
                action=DecisionWriteAction.KEEP_CANDIDATE,
                reason="source_changed_before_materialization",
                decision=None,
            )
        assessment = policy.evaluate(
            current_value=current.value if current is not None else None,
            proposed_value=decision.value,
            workspace_resolved=self._source_registry.has_workspace(decision.workspace_id),
            subject_resolved=bool(decision.subject_key.strip()),
            episode_resolved=self._source_registry.has_episode(
                decision.workspace_id, decision.source_episode_id
            ),
            evidence_resolved=bool(decision.source_evidence_refs)
            and all(
                self._source_registry.has_evidence(decision.workspace_id, evidence_ref)
                for evidence_ref in decision.source_evidence_refs
            ),
            team_decision_contract_pass=candidate.team_decision_contract_pass,
            grounding_pass=candidate.grounding_pass,
        )
        if assessment.action is DecisionWriteAction.KEEP_CANDIDATE:
            return DecisionWriteResult(
                candidate_id=candidate.candidate_id,
                action=assessment.action,
                reason=assessment.reason,
                decision=None,
            )
        if not approved_by.strip():
            raise PermissionError("explicit approval is required to materialize decision memory")
        if assessment.action is DecisionWriteAction.NO_OP:
            for action in candidate.actions:
                if action.workspace_id != decision.workspace_id:
                    raise ValueError("action workspace_id must match its source decision")
                if action.source_decision_id != decision.decision_id:
                    raise ValueError("action source_decision_id must match the materialized decision")
                self._source_registry.validate_action(action)
            if candidate.actions:
                return DecisionWriteResult(
                    candidate_id=candidate.candidate_id,
                    action=DecisionWriteAction.KEEP_CANDIDATE,
                    reason="actions_not_applied_by_no_op",
                    decision=None,
                )
            assert current is not None
            self._set_candidate_state(
                candidate.candidate_id,
                DecisionMemoryState.ACTIVE,
                outcome=assessment.action.value,
                decision_id=current.decision_id,
            )
            return DecisionWriteResult(
                candidate_id=candidate.candidate_id,
                action=assessment.action,
                reason=assessment.reason,
                decision=current,
            )

        supersedes_id = prior.decision_id if prior is not None else None
        if prior is not None and decision.valid_from <= prior.valid_from:
            return DecisionWriteResult(
                candidate_id=candidate.candidate_id,
                action=DecisionWriteAction.KEEP_CANDIDATE,
                reason="supersession_not_chronological",
                decision=None,
            )
        if prior is None and decision.supersedes_id is not None:
            return DecisionWriteResult(
                candidate_id=candidate.candidate_id,
                action=DecisionWriteAction.KEEP_CANDIDATE,
                reason="supersession_target_unresolved",
                decision=None,
            )
        # The write policy owns the supersession pointer. This makes retries
        # idempotent while retaining the existing strict materialization checks.
        from dataclasses import replace

        normalized_decision = replace(
            decision,
            supersedes_id=supersedes_id,
            memory_state=DecisionMemoryState.ACTIVE,
        )
        normalized_candidate = replace(candidate, decision=normalized_decision)
        stored = self._materialize_candidate(normalized_candidate, approved_by=approved_by)
        return DecisionWriteResult(
            candidate_id=candidate.candidate_id,
            action=assessment.action,
            reason=assessment.reason,
            decision=stored,
        )

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

        self._register_candidate(candidate)

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
                    SET status = ?, valid_to = ?, memory_state = ?
                    WHERE decision_id = ?
                    """,
                    (
                        DecisionStatus.SUPERSEDED.value,
                        _to_storage_time(decision.valid_from),
                        (
                            current.memory_state.value
                            if current.memory_state is not DecisionMemoryState.ACTIVE
                            else DecisionMemoryState.SUPERSEDED.value
                        ),
                        current.decision_id,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE decision_candidate
                    SET status = ?, outcome = ?
                    WHERE decision_id = ? AND status = ?
                    """,
                    (
                        DecisionMemoryState.SUPERSEDED.value,
                        DecisionMemoryState.SUPERSEDED.value,
                        current.decision_id,
                        DecisionMemoryState.ACTIVE.value,
                    ),
                )

            self._connection.execute(
                """
                INSERT INTO decision (
                    decision_id, workspace_id, subject_key, value, status, memory_state,
                    valid_from, valid_to, supersedes_id, source_episode_id,
                    provenance_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.workspace_id,
                    decision.subject_key,
                    decision.value,
                    decision.status.value,
                    decision.memory_state.value,
                    _to_storage_time(decision.valid_from),
                    None,
                    decision.supersedes_id,
                    decision.source_episode_id,
                    decision.provenance_run_id,
                ),
            )
            self._insert_evidence(
                "decision_evidence",
                "decision_id",
                decision.decision_id,
                decision.source_evidence_refs,
                workspace_id=decision.workspace_id,
            )
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
                    workspace_id=action.workspace_id,
                )
            self._connection.execute(
                """
                UPDATE decision_candidate
                SET status = ?, outcome = ?, decision_id = ?
                WHERE candidate_id = ?
                """,
                (
                    DecisionMemoryState.ACTIVE.value,
                    DecisionWriteAction.ACTIVATE.value
                    if decision.supersedes_id is None
                    else DecisionWriteAction.SUPERSEDE.value,
                    decision.decision_id,
                    candidate.candidate_id,
                ),
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
              AND memory_state = 'ACTIVE'
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
              AND memory_state IN ('ACTIVE', 'SUPERSEDED')
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
        validity = "valid_to IS NULL AND memory_state = 'ACTIVE'"
        if as_of is not None:
            timestamp = _to_storage_time(as_of)
            validity = (
                "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) "
                "AND memory_state IN ('ACTIVE', 'SUPERSEDED')"
            )
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
            memory_state=DecisionMemoryState(str(row["memory_state"])),
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
        *,
        workspace_id: str | None = None,
    ) -> None:
        if table == "decision_evidence":
            if workspace_id is None:
                raise ValueError("workspace_id is required for decision evidence")
            self._connection.executemany(
                "INSERT INTO decision_evidence "
                "(decision_id, ordinal, evidence_ref, source_hash) VALUES (?, ?, ?, ?)",
                (
                    (
                        record_id,
                        ordinal,
                        evidence_ref,
                        self._source_registry.evidence_hash(workspace_id, evidence_ref),
                    )
                    for ordinal, evidence_ref in enumerate(evidence_refs)
                ),
            )
            return
        if table == "action_evidence":
            if workspace_id is None:
                raise ValueError("workspace_id is required for action evidence")
            self._connection.executemany(
                "INSERT INTO action_evidence "
                "(action_id, ordinal, evidence_ref, source_hash) VALUES (?, ?, ?, ?)",
                (
                    (
                        record_id,
                        ordinal,
                        evidence_ref,
                        self._source_registry.evidence_hash(workspace_id, evidence_ref),
                    )
                    for ordinal, evidence_ref in enumerate(evidence_refs)
                ),
            )
            return
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
