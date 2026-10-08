"""SQLite-backed temporal decision and business-relation index."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from linkloom.decision_memory.models import (
    ActionRecord,
    DecisionCandidate,
    DecisionEvolution,
    DecisionMemoryState,
    DecisionRecord,
    DecisionStatus,
    TemporalLookupResult,
    TemporalLookupStatus,
    decision_slot_key_for,
    normalize_temporal_component,
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
    subject TEXT,
    relation TEXT,
    subject_normalized TEXT,
    relation_normalized TEXT,
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
    subject TEXT,
    relation TEXT,
    subject_normalized TEXT,
    relation_normalized TEXT,
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


_MIGRATION_1_SQL = (
    """
    CREATE TABLE IF NOT EXISTS decision_slot_alias (
        workspace_id TEXT NOT NULL,
        legacy_subject_key TEXT NOT NULL,
        subject_key TEXT NOT NULL,
        PRIMARY KEY (workspace_id, legacy_subject_key, subject_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_decision_slot_alias_lookup "
    "ON decision_slot_alias(workspace_id, legacy_subject_key)",
    """
    CREATE TABLE IF NOT EXISTS semantic_candidate (
        candidate_id TEXT NOT NULL UNIQUE,
        workspace_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        artifact_version_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        extraction_fingerprint TEXT NOT NULL,
        candidate_fingerprint TEXT NOT NULL,
        payload_version TEXT NOT NULL,
        validation_state TEXT NOT NULL
            CHECK (validation_state IN ('ACCEPTED', 'UNRESOLVED')),
        validation_reasons_json TEXT NOT NULL,
        workflow_state TEXT NOT NULL
            CHECK (workflow_state IN (
                'PENDING_REVIEW', 'APPROVED', 'POLICY_AUTHORIZED',
                'MATERIALIZED', 'DUPLICATE', 'SUPPORTING_ONLY',
                'CONFLICT', 'REJECTED', 'STALE', 'INVALIDATED'
            )),
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, candidate_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_candidate_workspace_state "
    "ON semantic_candidate(workspace_id, workflow_state, created_at, candidate_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_candidate_source_version "
    "ON semantic_candidate(workspace_id, artifact_id, artifact_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_candidate_fingerprint "
    "ON semantic_candidate(workspace_id, candidate_fingerprint)",
    """
    CREATE TABLE IF NOT EXISTS semantic_candidate_event (
        event_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        actor_type TEXT NOT NULL
            CHECK (actor_type IN ('SYSTEM', 'HUMAN', 'POLICY')),
        actor_id TEXT NOT NULL,
        event_at TEXT NOT NULL,
        candidate_fingerprint TEXT NOT NULL,
        reason_code TEXT,
        reason TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY (workspace_id, candidate_id)
            REFERENCES semantic_candidate(workspace_id, candidate_id)
            ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_candidate_event_history "
    "ON semantic_candidate_event(workspace_id, candidate_id, event_at, event_id)",
    """
    CREATE TABLE IF NOT EXISTS semantic_materialization_receipt (
        receipt_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        candidate_fingerprint TEXT NOT NULL,
        authorization_type TEXT NOT NULL
            CHECK (authorization_type IN ('HUMAN_APPROVAL', 'POLICY_AUTHORIZATION')),
        authorization_id TEXT NOT NULL,
        policy_id TEXT,
        policy_version TEXT,
        policy_fingerprint TEXT,
        authorization_reason TEXT NOT NULL,
        authorized_at TEXT NOT NULL,
        decision_id TEXT NOT NULL REFERENCES decision(decision_id) ON DELETE RESTRICT,
        supersedes_id TEXT REFERENCES decision(decision_id) ON DELETE RESTRICT,
        correction_of_candidate_id TEXT,
        correction_of_decision_id TEXT REFERENCES decision(decision_id) ON DELETE RESTRICT,
        effective_at TEXT NOT NULL,
        materialized_at TEXT NOT NULL,
        result_state TEXT NOT NULL
            CHECK (result_state IN ('MATERIALIZED', 'FUTURE_EFFECTIVE_PENDING', 'DUPLICATE')),
        materialization_key TEXT NOT NULL,
        review_resolution_fingerprint TEXT,
        receipt_fingerprint TEXT NOT NULL,
        UNIQUE (workspace_id, candidate_id),
        FOREIGN KEY (workspace_id, candidate_id)
            REFERENCES semantic_candidate(workspace_id, candidate_id)
            ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_receipt_materialization_key "
    "ON semantic_materialization_receipt(workspace_id, materialization_key)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_receipt_decision "
    "ON semantic_materialization_receipt(workspace_id, decision_id)",
    """
    CREATE TRIGGER IF NOT EXISTS semantic_candidate_payload_immutable
    BEFORE UPDATE OF candidate_id, workspace_id, artifact_id, artifact_version_id,
        content_sha256, extraction_fingerprint, candidate_fingerprint,
        payload_version, validation_state, validation_reasons_json, payload_json,
        created_at ON semantic_candidate
    BEGIN
        SELECT RAISE(ABORT, 'semantic candidate evidence is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS semantic_candidate_no_delete
    BEFORE DELETE ON semantic_candidate
    BEGIN
        SELECT RAISE(ABORT, 'semantic candidate evidence is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS semantic_candidate_event_append_only_update
    BEFORE UPDATE ON semantic_candidate_event
    BEGIN
        SELECT RAISE(ABORT, 'semantic candidate events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS semantic_candidate_event_append_only_delete
    BEFORE DELETE ON semantic_candidate_event
    BEGIN
        SELECT RAISE(ABORT, 'semantic candidate events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS semantic_receipt_immutable_update
    BEFORE UPDATE ON semantic_materialization_receipt
    BEGIN
        SELECT RAISE(ABORT, 'materialization receipts are immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS semantic_receipt_immutable_delete
    BEFORE DELETE ON semantic_materialization_receipt
    BEGIN
        SELECT RAISE(ABORT, 'materialization receipts are immutable');
    END
    """,
)

_MIGRATION_2_SQL = (
    "ALTER TABLE semantic_candidate ADD COLUMN candidate_source_kind TEXT NOT NULL DEFAULT 'SOURCE_INGESTION' CHECK (candidate_source_kind IN ('SOURCE_INGESTION', 'AGENT_RESULT'))",
    "ALTER TABLE semantic_candidate_event ADD COLUMN candidate_source_kind TEXT NOT NULL DEFAULT 'SOURCE_INGESTION' CHECK (candidate_source_kind IN ('SOURCE_INGESTION', 'AGENT_RESULT'))",
    "ALTER TABLE semantic_materialization_receipt ADD COLUMN candidate_source_kind TEXT NOT NULL DEFAULT 'SOURCE_INGESTION' CHECK (candidate_source_kind IN ('SOURCE_INGESTION', 'AGENT_RESULT'))",
    "ALTER TABLE semantic_materialization_receipt ADD COLUMN action_dispositions_json TEXT NOT NULL DEFAULT '[]'",
    "DROP TRIGGER IF EXISTS semantic_candidate_payload_immutable",
    """
    CREATE TRIGGER semantic_candidate_payload_immutable
    BEFORE UPDATE OF candidate_id, workspace_id, artifact_id, artifact_version_id,
        content_sha256, extraction_fingerprint, candidate_fingerprint,
        payload_version, validation_state, validation_reasons_json, payload_json,
        created_at, candidate_source_kind ON semantic_candidate
    BEGIN
        SELECT RAISE(ABORT, 'semantic candidate evidence is immutable');
    END
    """,
)

_LATEST_SCHEMA_VERSION = 2


class TemporalDecisionStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        source_registry: SourceReferenceRegistry | None = None,
        check_same_thread: bool = True,
    ) -> None:
        raw_path = str(db_path)
        if raw_path != ":memory:":
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = raw_path
        self._source_registry = source_registry or SourceReferenceRegistry()
        if check_same_thread:
            self._connection = sqlite3.connect(raw_path)
        else:
            self._connection = sqlite3.connect(raw_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        initial_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if initial_version > _LATEST_SCHEMA_VERSION:
            self._connection.close()
            raise RuntimeError(
                f"database schema version {initial_version} is newer than supported "
                f"version {_LATEST_SCHEMA_VERSION}"
            )
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
        for column in ("subject", "relation", "subject_normalized", "relation_normalized"):
            self._ensure_column("decision", column, "TEXT")
            self._ensure_column("decision_candidate", column, "TEXT")
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_workspace_subject_relation "
            "ON decision(workspace_id, subject_normalized, relation_normalized, valid_from, valid_to)"
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
        self._migrate_to_latest()

    @property
    def schema_version(self) -> int:
        return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    def _migrate_to_latest(self) -> None:
        while self.schema_version < _LATEST_SCHEMA_VERSION:
            if self.schema_version == 0:
                self._migrate_v1()
                continue
            if self.schema_version == 1:
                self._migrate_v2()
                continue
            raise RuntimeError(f"no migration path from schema version {self.schema_version}")

    def _migrate_v1(self) -> None:
        """Add semantic workflow tables and backfill structured relation slots atomically."""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _MIGRATION_1_SQL:
                self._connection.execute(statement)

            decision_rows = self._connection.execute(
                """
                SELECT decision_id, workspace_id, subject_key, subject, relation,
                       valid_to, memory_state
                FROM decision
                WHERE subject IS NOT NULL AND relation IS NOT NULL
                ORDER BY workspace_id, decision_id
                """
            ).fetchall()
            candidate_rows = self._connection.execute(
                """
                SELECT candidate_id, workspace_id, subject_key, subject, relation
                FROM decision_candidate
                WHERE subject IS NOT NULL AND relation IS NOT NULL
                ORDER BY workspace_id, candidate_id
                """
            ).fetchall()
            active_slots: dict[tuple[str, str], list[str]] = {}
            for row in decision_rows:
                slot_key = decision_slot_key_for(
                    str(row["subject"]), str(row["relation"])
                )
                if slot_key is None:
                    continue
                if row["valid_to"] is None and str(row["memory_state"]) == "ACTIVE":
                    active_slots.setdefault((str(row["workspace_id"]), slot_key), []).append(
                        str(row["decision_id"])
                    )
            collisions = [items for items in active_slots.values() if len(items) > 1]
            if collisions:
                raise RuntimeError(
                    "relation-scoped slot collision during SQLite migration: "
                    + "; ".join(",".join(items) for items in collisions)
                )

            for row in (*decision_rows, *candidate_rows):
                old_key = str(row["subject_key"])
                slot_key = decision_slot_key_for(
                    str(row["subject"]), str(row["relation"])
                )
                if slot_key is None or old_key == slot_key:
                    continue
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO decision_slot_alias (
                        workspace_id, legacy_subject_key, subject_key
                    ) VALUES (?, ?, ?)
                    """,
                    (str(row["workspace_id"]), old_key, slot_key),
                )
                table = "decision" if "decision_id" in row.keys() else "decision_candidate"
                id_column = "decision_id" if table == "decision" else "candidate_id"
                self._connection.execute(
                    f'UPDATE "{table}" SET subject_key = ? WHERE "{id_column}" = ?',
                    (slot_key, str(row[id_column])),
                )

            self._connection.execute("PRAGMA user_version = 1")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _migrate_v2(self) -> None:
        """Add explicit candidate-origin metadata to the shared lifecycle."""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _MIGRATION_2_SQL:
                self._connection.execute(statement)
            self._connection.execute(f"PRAGMA user_version = {_LATEST_SCHEMA_VERSION}")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

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

    @property
    def source_registry(self) -> SourceReferenceRegistry:
        return self._source_registry

    @contextmanager
    def _immediate_transaction(self) -> Iterator[None]:
        if self._connection.in_transaction:
            raise RuntimeError("semantic store operation cannot nest a SQLite transaction")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def __enter__(self) -> TemporalDecisionStore:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()

    def _register_candidate(self, candidate: DecisionCandidate) -> None:
        candidate = self._canonicalize_candidate_slot(candidate)
        decision = candidate.decision
        payload = (
            candidate.candidate_id,
            decision.workspace_id,
            decision.subject_key,
            decision.subject,
            decision.relation,
            (
                normalize_temporal_component(decision.subject)
                if decision.subject is not None
                else None
            ),
            (
                normalize_temporal_component(decision.relation)
                if decision.relation is not None
                else None
            ),
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
                    str(existing["subject"]) if existing["subject"] is not None else None,
                    str(existing["relation"]) if existing["relation"] is not None else None,
                    str(existing["proposed_value"]),
                    str(existing["source_evidence_refs"]),
                    str(existing["provenance_run_id"]),
                )
                candidate_identity = (
                    payload[1],
                    payload[2],
                    payload[3],
                    payload[4],
                    payload[7],
                    payload[8],
                    payload[10],
                )
                if existing_identity != candidate_identity:
                    raise ValueError("candidate_id is already registered with different content")
                return
            self._connection.execute(
                """
                INSERT INTO decision_candidate (
                    candidate_id, workspace_id, subject_key, subject, relation,
                    subject_normalized, relation_normalized, proposed_value,
                    source_evidence_refs, source_hashes, provenance_run_id,
                    created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    def _canonicalize_candidate_slot(
        self,
        candidate: DecisionCandidate,
    ) -> DecisionCandidate:
        decision = candidate.decision
        slot_key = decision_slot_key_for(decision.subject, decision.relation)
        if slot_key is None or decision.subject_key == slot_key:
            return candidate
        self._connection.execute(
            """
            INSERT OR IGNORE INTO decision_slot_alias (
                workspace_id, legacy_subject_key, subject_key
            ) VALUES (?, ?, ?)
            """,
            (decision.workspace_id, decision.subject_key, slot_key),
        )
        return replace(
            candidate,
            decision=replace(decision, subject_key=slot_key),
        )

    def _resolve_subject_key_alias(self, workspace_id: str, subject_key: str) -> str:
        aliases = self._connection.execute(
            """
            SELECT subject_key FROM decision_slot_alias
            WHERE workspace_id = ? AND legacy_subject_key = ?
            ORDER BY subject_key
            """,
            (workspace_id, subject_key),
        ).fetchall()
        keys = tuple(dict.fromkeys(str(row["subject_key"]) for row in aliases))
        if len(keys) > 1:
            raise ValueError(
                "legacy subject_key maps to multiple relation slots; use structured lookup"
            )
        return keys[0] if keys else subject_key

    def get_candidate_state(self, candidate_id: str) -> DecisionMemoryState | None:
        row = self._connection.execute(
            "SELECT status FROM decision_candidate WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return DecisionMemoryState(str(row["status"])) if row is not None else None

    def _append_semantic_event_locked(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        event_type: str,
        actor_type: str,
        actor_id: str,
        event_at: datetime,
        candidate_fingerprint: str,
        reason_code: str | None,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> str:
        candidate_row = self._connection.execute(
            "SELECT candidate_source_kind FROM semantic_candidate "
            "WHERE workspace_id = ? AND candidate_id = ?",
            (workspace_id, candidate_id),
        ).fetchone()
        if candidate_row is None:
            raise ValueError("candidate event requires a persisted candidate")
        candidate_source_kind = str(candidate_row["candidate_source_kind"])
        event_metadata = dict(metadata or {})
        if (
            event_metadata.get("candidate_source_kind") is not None
            and event_metadata["candidate_source_kind"] != candidate_source_kind
        ):
            raise ValueError("candidate event source kind does not match its candidate")
        event_metadata["candidate_source_kind"] = candidate_source_kind
        event_id = f"semantic_event_v1_{uuid4().hex}"
        self._connection.execute(
            """
            INSERT INTO semantic_candidate_event (
                event_id, workspace_id, candidate_id, event_type, actor_type,
                actor_id, event_at, candidate_fingerprint, reason_code, reason,
                metadata_json, candidate_source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                workspace_id,
                candidate_id,
                event_type,
                actor_type,
                actor_id,
                _to_storage_time(event_at),
                candidate_fingerprint,
                reason_code,
                reason,
                json.dumps(
                    event_metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                candidate_source_kind,
            ),
        )
        return event_id

    def save_semantic_candidate(
        self,
        *,
        candidate_id: str,
        workspace_id: str,
        artifact_id: str,
        artifact_version_id: str,
        content_sha256: str,
        extraction_fingerprint: str,
        candidate_fingerprint: str,
        payload_version: str,
        validation_state: str,
        validation_reasons: tuple[str, ...],
        payload_json: str,
        created_at: datetime,
        observed_at: datetime,
        candidate_source_kind: str = "SOURCE_INGESTION",
    ) -> bool:
        """Persist immutable candidate evidence; return False for exact replay."""
        if candidate_source_kind not in {"SOURCE_INGESTION", "AGENT_RESULT"}:
            raise ValueError("unsupported semantic candidate source kind")
        encoded_reasons = json.dumps(
            list(validation_reasons),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._immediate_transaction():
            existing = self._connection.execute(
                """
                SELECT candidate_fingerprint, payload_json, extraction_fingerprint,
                       candidate_source_kind
                FROM semantic_candidate WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, candidate_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["candidate_fingerprint"]) != candidate_fingerprint
                    or str(existing["payload_json"]) != payload_json
                    or str(existing["extraction_fingerprint"]) != extraction_fingerprint
                    or str(existing["candidate_source_kind"]) != candidate_source_kind
                ):
                    raise ValueError("candidate identity is already bound to different evidence")
                return False

            prior_versions = self._connection.execute(
                """
                SELECT DISTINCT artifact_version_id, content_sha256
                FROM semantic_candidate
                WHERE workspace_id = ? AND artifact_id = ?
                  AND candidate_source_kind = 'SOURCE_INGESTION'
                  AND artifact_version_id != ?
                ORDER BY artifact_version_id
                """,
                (workspace_id, artifact_id, artifact_version_id),
            ).fetchall()
            self._connection.execute(
                """
                INSERT INTO semantic_candidate (
                    candidate_id, workspace_id, artifact_id, artifact_version_id,
                    content_sha256, extraction_fingerprint, candidate_fingerprint,
                    payload_version, validation_state, validation_reasons_json,
                    workflow_state, payload_json, created_at, updated_at,
                    candidate_source_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    workspace_id,
                    artifact_id,
                    artifact_version_id,
                    content_sha256,
                    extraction_fingerprint,
                    candidate_fingerprint,
                    payload_version,
                    validation_state,
                    encoded_reasons,
                    payload_json,
                    _to_storage_time(created_at),
                    _to_storage_time(observed_at),
                    candidate_source_kind,
                ),
            )
            self._append_semantic_event_locked(
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                event_type="CANDIDATE_CAPTURED",
                actor_type="SYSTEM",
                actor_id=(
                    "agent-memory-candidate"
                    if candidate_source_kind == "AGENT_RESULT"
                    else "semantic-ingestion"
                ),
                event_at=observed_at,
                candidate_fingerprint=candidate_fingerprint,
                reason_code=None,
                reason="validated candidate evidence persisted",
                metadata={
                    "artifact_id": artifact_id,
                    "artifact_version_id": artifact_version_id,
                    "extraction_fingerprint": extraction_fingerprint,
                    "validation_state": validation_state,
                    "validation_reasons": list(validation_reasons),
                    "candidate_source_kind": candidate_source_kind,
                },
            )
            if prior_versions:
                self._append_semantic_event_locked(
                    workspace_id=workspace_id,
                    candidate_id=candidate_id,
                    event_type="SOURCE_VERSION_OBSERVED",
                    actor_type="SYSTEM",
                    actor_id=(
                        "agent-memory-candidate"
                        if candidate_source_kind == "AGENT_RESULT"
                        else "semantic-ingestion"
                    ),
                    event_at=observed_at,
                    candidate_fingerprint=candidate_fingerprint,
                    reason_code="SOURCE_VERSION_CHANGED",
                    reason="a distinct immutable artifact version was already recorded",
                    metadata={
                        "prior_versions": [
                            {
                                "artifact_version_id": str(row["artifact_version_id"]),
                                "content_sha256": str(row["content_sha256"]),
                            }
                            for row in prior_versions
                        ],
                        "artifact_version_id": artifact_version_id,
                        "content_sha256": content_sha256,
                        "candidate_source_kind": candidate_source_kind,
                    },
                )
        return True

    def get_semantic_candidate_row(
        self,
        workspace_id: str,
        candidate_id: str,
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            """
            SELECT * FROM semantic_candidate
            WHERE workspace_id = ? AND candidate_id = ?
            """,
            (workspace_id, candidate_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_semantic_candidate_rows(
        self,
        workspace_id: str,
        *,
        workflow_state: str | None = None,
        artifact_id: str | None = None,
        artifact_version_id: str | None = None,
        candidate_fingerprint: str | None = None,
        candidate_source_kind: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        clauses = ["workspace_id = ?"]
        parameters: list[str] = [workspace_id]
        for column, value in (
            ("workflow_state", workflow_state),
            ("artifact_id", artifact_id),
            ("artifact_version_id", artifact_version_id),
            ("candidate_fingerprint", candidate_fingerprint),
            ("candidate_source_kind", candidate_source_kind),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        rows = self._connection.execute(
            "SELECT * FROM semantic_candidate WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, candidate_id",
            parameters,
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_semantic_candidate_events(
        self,
        workspace_id: str,
        candidate_id: str,
    ) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM semantic_candidate_event
            WHERE workspace_id = ? AND candidate_id = ?
            ORDER BY event_at, event_id
            """,
            (workspace_id, candidate_id),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_semantic_receipt_row(
        self,
        workspace_id: str,
        candidate_id: str,
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            """
            SELECT * FROM semantic_materialization_receipt
            WHERE workspace_id = ? AND candidate_id = ?
            """,
            (workspace_id, candidate_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def transition_semantic_candidate(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        candidate_fingerprint: str,
        expected_states: tuple[str, ...],
        new_state: str,
        event_type: str,
        actor_type: str,
        actor_id: str,
        event_at: datetime,
        reason_code: str | None,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        with self._immediate_transaction():
            row = self._connection.execute(
                """
                SELECT candidate_fingerprint, workflow_state
                FROM semantic_candidate WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, candidate_id),
            ).fetchone()
            if row is None:
                raise PermissionError("candidate is unavailable in the authorized workspace")
            if str(row["candidate_fingerprint"]) != candidate_fingerprint:
                raise ValueError("candidate approval is stale")
            current_state = str(row["workflow_state"])
            if current_state not in expected_states:
                return False
            self._connection.execute(
                """
                UPDATE semantic_candidate SET workflow_state = ?, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ?
                  AND candidate_fingerprint = ? AND workflow_state = ?
                """,
                (
                    new_state,
                    _to_storage_time(event_at),
                    workspace_id,
                    candidate_id,
                    candidate_fingerprint,
                    current_state,
                ),
            )
            self._append_semantic_event_locked(
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                event_at=event_at,
                candidate_fingerprint=candidate_fingerprint,
                reason_code=reason_code,
                reason=reason,
                metadata=metadata,
            )
        return True

    def materialize_semantic_candidate(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        candidate_fingerprint: str,
        candidate_source_kind: str,
        decision: DecisionRecord,
        materialization_key: str,
        receipt_id: str,
        receipt_fingerprint: str,
        authorization_type: str,
        authorization_id: str,
        policy_id: str | None,
        policy_version: str | None,
        policy_fingerprint: str | None,
        authorization_reason: str,
        authorized_at: datetime,
        materialized_at: datetime,
        effective_at: datetime,
        correction_of_candidate_id: str | None = None,
        correction_of_decision_id: str | None = None,
        review_resolution_fingerprint: str | None = None,
        action_dispositions: tuple[dict[str, object], ...] = (),
    ) -> dict[str, object]:
        """Atomically materialize a persisted candidate and append its receipt."""
        if decision.workspace_id != workspace_id:
            raise PermissionError("DecisionRecord workspace does not match the candidate workspace")
        if authorization_type not in {"HUMAN_APPROVAL", "POLICY_AUTHORIZATION"}:
            raise ValueError("unsupported authorization type")
        with self._immediate_transaction():
            row = self._connection.execute(
                """
                SELECT * FROM semantic_candidate
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, candidate_id),
            ).fetchone()
            if row is None:
                raise PermissionError("candidate is unavailable in the authorized workspace")
            if str(row["candidate_fingerprint"]) != candidate_fingerprint:
                raise ValueError("candidate fingerprint changed before materialization")
            payload_json = str(row["payload_json"])
            actual_fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            if actual_fingerprint != candidate_fingerprint:
                raise ValueError("persisted candidate payload fingerprint is invalid")
            if str(row["candidate_source_kind"]) != candidate_source_kind:
                raise PermissionError("candidate source kind changed before materialization")
            from linkloom.semantic_ingestion.materialization import (
                CandidateReviewResolution,
                _apply_view_review_resolution,
                _candidate_view_from_row,
            )
            from linkloom.semantic_ingestion.materializable import (
                CandidateSourceKind,
                action_mapping_for,
            )

            try:
                source_kind = CandidateSourceKind(candidate_source_kind)
                candidate_object, candidate_view, canonical_payload, canonical_fingerprint = (
                    _candidate_view_from_row(dict(row))
                )
            except (ValueError, TypeError) as error:
                raise ValueError("persisted candidate payload cannot form a materialization view") from error
            if (
                canonical_payload != payload_json
                or canonical_fingerprint != candidate_fingerprint
                or candidate_object.candidate_id != candidate_id
                or candidate_object.workspace_id != workspace_id
                or candidate_view.source_kind is not source_kind
            ):
                raise ValueError("persisted candidate materialization view is inconsistent")
            existing_receipt = self._connection.execute(
                """
                SELECT * FROM semantic_materialization_receipt
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, candidate_id),
            ).fetchone()
            if existing_receipt is not None:
                return dict(existing_receipt)

            if correction_of_candidate_id is not None:
                correction_target = self._connection.execute(
                    """
                    SELECT receipt.decision_id
                    FROM semantic_candidate AS candidate
                    JOIN semantic_materialization_receipt AS receipt
                      ON receipt.workspace_id = candidate.workspace_id
                     AND receipt.candidate_id = candidate.candidate_id
                    WHERE candidate.workspace_id = ? AND candidate.candidate_id = ?
                    """,
                    (workspace_id, correction_of_candidate_id),
                ).fetchone()
                if correction_target is None:
                    raise ValueError(
                        "correction target candidate and materialization receipt must exist in the same workspace"
                    )
                if correction_of_decision_id != str(correction_target["decision_id"]):
                    raise ValueError("correction receipt decision does not match its target receipt")
            elif correction_of_decision_id is not None:
                raise ValueError("correction decision cannot be set without a correction candidate")

            workflow_state = str(row["workflow_state"])
            expected_state = (
                "APPROVED" if authorization_type == "HUMAN_APPROVAL" else "POLICY_AUTHORIZED"
            )
            if workflow_state != expected_state:
                raise PermissionError("candidate lacks the requested durable authorization")
            authorization_event_type = (
                "HUMAN_APPROVAL"
                if authorization_type == "HUMAN_APPROVAL"
                else "POLICY_AUTHORIZATION"
            )
            authorization_event = self._connection.execute(
                """
                SELECT actor_type, actor_id, event_at, candidate_fingerprint,
                       reason, metadata_json
                FROM semantic_candidate_event
                WHERE workspace_id = ? AND candidate_id = ? AND event_type = ?
                ORDER BY event_at DESC, event_id DESC
                LIMIT 1
                """,
                (workspace_id, candidate_id, authorization_event_type),
            ).fetchone()
            if authorization_event is None:
                raise PermissionError("candidate authorization event is missing")
            expected_actor_type = "HUMAN" if authorization_type == "HUMAN_APPROVAL" else "POLICY"
            if (
                str(authorization_event["actor_type"]) != expected_actor_type
                or str(authorization_event["actor_id"]) != authorization_id
                or str(authorization_event["event_at"]) != _to_storage_time(authorized_at)
                or str(authorization_event["candidate_fingerprint"]) != candidate_fingerprint
                or str(authorization_event["reason"]) != authorization_reason
            ):
                raise PermissionError("candidate authorization does not match its audit event")
            authorization_metadata = json.loads(str(authorization_event["metadata_json"]))
            if not isinstance(authorization_metadata, dict):
                raise ValueError("candidate authorization metadata is malformed")
            if authorization_type == "POLICY_AUTHORIZATION":
                if (
                    authorization_metadata.get("policy_id") != policy_id
                    or authorization_metadata.get("policy_version") != policy_version
                    or authorization_metadata.get("policy_fingerprint") != policy_fingerprint
                ):
                    raise PermissionError("policy authorization does not match its audit event")
            elif policy_id is not None or policy_version is not None or policy_fingerprint is not None:
                raise ValueError("human approval cannot carry policy authorization fields")
            resolution = authorization_metadata.get("review_resolution")
            resolution_fingerprint = authorization_metadata.get("review_resolution_fingerprint")
            if resolution is None:
                if review_resolution_fingerprint is not None or resolution_fingerprint is not None:
                    raise ValueError("review-resolution fingerprint is inconsistent")
                review_resolution = None
            else:
                if authorization_type != "HUMAN_APPROVAL" or not isinstance(resolution, dict):
                    raise PermissionError("only a human approval may carry candidate resolution")
                review_resolution = CandidateReviewResolution.from_dict(resolution)
                if review_resolution is None:
                    raise ValueError("candidate review resolution is malformed")
                actual_resolution_fingerprint = review_resolution.fingerprint
                if (
                    resolution_fingerprint != actual_resolution_fingerprint
                    or review_resolution_fingerprint != actual_resolution_fingerprint
                ):
                    raise ValueError("review resolution is not bound to the authorization event")
            candidate_view = _apply_view_review_resolution(candidate_view, review_resolution)
            if (
                decision.subject != candidate_view.subject
                or decision.relation != candidate_view.relation
                or decision.value != candidate_view.value
            ):
                raise ValueError("DecisionRecord fields do not match candidate and review authorization")
            if decision.subject_key != candidate_view.subject_key:
                raise ValueError("DecisionRecord slot does not match candidate review resolution")
            if candidate_view.valid_from is None or _to_storage_time(decision.valid_from) != _to_storage_time(
                _as_utc(candidate_view.valid_from)
            ):
                raise ValueError("DecisionRecord valid_from does not match candidate review resolution")
            if (
                (candidate_view.valid_to is None and decision.valid_to is not None)
                or (
                    candidate_view.valid_to is not None
                    and (
                        decision.valid_to is None
                        or _to_storage_time(decision.valid_to)
                        != _to_storage_time(_as_utc(candidate_view.valid_to))
                    )
                )
            ):
                raise ValueError("DecisionRecord valid_to does not match candidate temporal bounds")
            primary_evidence = candidate_view.primary_evidence
            if (
                str(row["artifact_id"]) != primary_evidence.artifact_id
                or str(row["artifact_version_id"]) != primary_evidence.artifact_version_id
                or str(row["content_sha256"]) != primary_evidence.content_sha256
            ):
                raise ValueError("candidate source index does not match its immutable provenance")
            candidate_provenance_matches = (
                decision.source_episode_id == primary_evidence.source_episode_id
                and decision.source_evidence_refs == candidate_view.decision_evidence_refs
                and decision.provenance_run_id == candidate_view.provenance_run_id
            )
            # A duplicate candidate may intentionally reuse an already persisted
            # DecisionRecord. Its provenance belongs to the original candidate;
            # the new candidate's own complete provenance remains in its immutable
            # payload and workflow events.
            persisted_decision = self.get_decision(workspace_id, decision.decision_id)
            reuses_persisted_decision = persisted_decision == decision
            if not candidate_provenance_matches and not reuses_persisted_decision:
                raise ValueError("DecisionRecord mapping does not match candidate provenance")
            self._source_registry.validate_decision(decision)
            evidence_by_ref = candidate_view.evidence_by_ref()
            for binding in candidate_view.evidence:
                if (
                    binding.workspace_id != workspace_id
                    or not self._source_registry.has_episode(workspace_id, binding.source_episode_id)
                    or not self._source_registry.has_evidence(workspace_id, binding.evidence_ref)
                    or self._source_registry.evidence_hash(workspace_id, binding.evidence_ref)
                    != binding.content_hash
                    or self._source_registry.evidence_version(workspace_id, binding.evidence_ref)
                    != binding.source_version
                ):
                    raise ValueError("registered source evidence changed before materialization")
            if (
                not reuses_persisted_decision
                and any(reference not in evidence_by_ref for reference in decision.source_evidence_refs)
            ):
                raise ValueError("DecisionRecord references evidence outside the candidate")
            _preflight_actions, expected_action_dispositions = action_mapping_for(
                candidate_view,
                source_decision_id=decision.decision_id,
            )
            if list(expected_action_dispositions) != list(action_dispositions):
                raise ValueError("action mapping does not match the immutable Agent candidate")

            duplicate = self._connection.execute(
                """
                SELECT receipt.* FROM semantic_materialization_receipt AS receipt
                JOIN decision AS decision
                  ON decision.workspace_id = receipt.workspace_id
                 AND decision.decision_id = receipt.decision_id
                WHERE receipt.workspace_id = ? AND receipt.materialization_key = ?
                  AND decision.memory_state IN ('ACTIVE', 'SUPERSEDED')
                ORDER BY receipt.materialized_at, receipt.receipt_id
                LIMIT 1
                """,
                (workspace_id, materialization_key),
            ).fetchone()
            prior = self._connection.execute(
                """
                SELECT * FROM decision
                WHERE workspace_id = ? AND subject_key = ?
                  AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
                  AND memory_state IN ('ACTIVE', 'SUPERSEDED')
                ORDER BY valid_from DESC, decision_id
                LIMIT 1
                """,
                (
                    workspace_id,
                    decision.subject_key,
                    _to_storage_time(decision.valid_from),
                    _to_storage_time(decision.valid_from),
                ),
            ).fetchone()
            duplicate_decision_id: str | None = None
            duplicate_supersedes_id: str | None = None
            result_state = "MATERIALIZED"
            if duplicate is not None:
                duplicate_decision_id = str(duplicate["decision_id"])
                duplicate_supersedes_id = (
                    str(duplicate["supersedes_id"])
                    if duplicate["supersedes_id"] is not None
                    else None
                )
                result_state = "DUPLICATE"
            elif prior is not None and normalize_temporal_component(str(prior["value"])) == normalize_temporal_component(decision.value):
                duplicate_decision_id = str(prior["decision_id"])
                duplicate_supersedes_id = (
                    str(prior["supersedes_id"]) if prior["supersedes_id"] is not None else None
                )
                result_state = "DUPLICATE"

            if duplicate_decision_id is not None:
                decision_id = duplicate_decision_id
                supersedes_id = duplicate_supersedes_id
                final_state = "DUPLICATE"
                event_type = "MATERIALIZATION_DUPLICATE"
                reason_code = "DUPLICATE"
                reason = "candidate maps to an existing authoritative record"
            else:
                later_rows = self._connection.execute(
                    """
                    SELECT decision_id FROM decision
                    WHERE workspace_id = ? AND subject_key = ?
                      AND valid_from > ?
                      AND memory_state IN ('ACTIVE', 'SUPERSEDED')
                    ORDER BY valid_from, decision_id
                    LIMIT 1
                    """,
                    (
                        workspace_id,
                        decision.subject_key,
                        _to_storage_time(decision.valid_from),
                    ),
                ).fetchall()
                if later_rows:
                    raise ValueError(
                        "new decision would precede an already materialized successor"
                    )
                now_storage = _to_storage_time(materialized_at)
                if prior is None:
                    if decision.supersedes_id is not None:
                        raise ValueError("supersession target is unresolved for this workspace/slot")
                else:
                    if decision.supersedes_id != str(prior["decision_id"]):
                        raise ValueError("supersession target does not match the effective predecessor")
                    if decision.valid_from <= _from_storage_time(str(prior["valid_from"])):
                        raise ValueError("superseding decision must start after its predecessor")
                self._write_decision_locked(decision)
                decision_id = decision.decision_id
                supersedes_id = decision.supersedes_id
                final_state = "MATERIALIZED"
                event_type = "MATERIALIZATION_COMMITTED"
                reason_code = "FUTURE_EFFECTIVE_PENDING" if _to_storage_time(decision.valid_from) > now_storage else None
                reason = (
                    "authorized DecisionRecord persisted with future valid time"
                    if reason_code
                    else "authorized DecisionRecord and evidence persisted"
                )
                if reason_code:
                    result_state = "FUTURE_EFFECTIVE_PENDING"

            materialized_actions, actual_action_dispositions = action_mapping_for(
                candidate_view,
                source_decision_id=decision_id,
            )
            if list(actual_action_dispositions) != list(action_dispositions):
                raise ValueError("Agent action mapping changed during materialization")
            evidence_by_ref = candidate_view.evidence_by_ref()
            for action in materialized_actions:
                if action.workspace_id != workspace_id or action.source_decision_id != decision_id:
                    raise ValueError("materialized ActionRecord does not match its DecisionRecord")
                self._source_registry.validate_action(action)
                if any(
                    reference not in evidence_by_ref
                    or self._source_registry.evidence_hash(workspace_id, reference)
                    != evidence_by_ref[reference].content_sha256
                    for reference in action.source_evidence_refs
                ):
                    raise ValueError("ActionRecord evidence changed before materialization")
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

            authorized = _to_storage_time(authorized_at)
            materialized = _to_storage_time(materialized_at)
            self._connection.execute(
                """
                INSERT INTO semantic_materialization_receipt (
                    receipt_id, workspace_id, candidate_id, candidate_fingerprint,
                    authorization_type, authorization_id, policy_id, policy_version,
                    policy_fingerprint, authorization_reason, authorized_at,
                    decision_id, supersedes_id, correction_of_candidate_id,
                    correction_of_decision_id, effective_at, materialized_at,
                    result_state, materialization_key, review_resolution_fingerprint,
                    receipt_fingerprint, candidate_source_kind, action_dispositions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    workspace_id,
                    candidate_id,
                    candidate_fingerprint,
                    authorization_type,
                    authorization_id,
                    policy_id,
                    policy_version,
                    policy_fingerprint,
                    authorization_reason,
                    authorized,
                    decision_id,
                    supersedes_id,
                    correction_of_candidate_id,
                    correction_of_decision_id,
                    _to_storage_time(effective_at),
                    materialized,
                    result_state,
                    materialization_key,
                    review_resolution_fingerprint,
                    receipt_fingerprint,
                    candidate_source_kind,
                    json.dumps(
                        list(actual_action_dispositions),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            )
            self._connection.execute(
                """
                UPDATE semantic_candidate
                SET workflow_state = ?, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ?
                  AND candidate_fingerprint = ?
                """,
                (final_state, materialized, workspace_id, candidate_id, candidate_fingerprint),
            )
            self._append_semantic_event_locked(
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                event_type=event_type,
                actor_type="HUMAN" if authorization_type == "HUMAN_APPROVAL" else "POLICY",
                actor_id=authorization_id,
                event_at=materialized_at,
                candidate_fingerprint=candidate_fingerprint,
                reason_code=reason_code,
                reason=reason,
                metadata={
                    "authorization_type": authorization_type,
                    "authorization_id": authorization_id,
                    "policy_id": policy_id,
                    "policy_version": policy_version,
                    "decision_id": decision_id,
                    "supersedes_id": supersedes_id,
                    "correction_of_candidate_id": correction_of_candidate_id,
                    "correction_of_decision_id": correction_of_decision_id,
                    "result_state": result_state,
                    "action_dispositions": list(actual_action_dispositions),
                    "candidate_source_kind": candidate_source_kind,
                },
            )
            receipt_row = self._connection.execute(
                """
                SELECT * FROM semantic_materialization_receipt
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, candidate_id),
            ).fetchone()
            if receipt_row is None:
                raise RuntimeError("materialization receipt was not persisted")
            return dict(receipt_row)

    def record_extraction_correction(
        self,
        *,
        workspace_id: str,
        incorrect_candidate_id: str,
        corrected_candidate_id: str,
        reviewer_id: str,
        reason: str,
        event_at: datetime,
    ) -> None:
        from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate

        with self._immediate_transaction():
            incorrect_row = self._connection.execute(
                """
                SELECT * FROM semantic_candidate
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, incorrect_candidate_id),
            ).fetchone()
            corrected_row = self._connection.execute(
                """
                SELECT * FROM semantic_candidate
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, corrected_candidate_id),
            ).fetchone()
            if incorrect_row is None or corrected_row is None:
                raise PermissionError("correction candidates are unavailable in this workspace")
            incorrect = deserialize_candidate(str(incorrect_row["payload_json"]))
            corrected = deserialize_candidate(str(corrected_row["payload_json"]))
            if (
                incorrect.artifact_id != corrected.artifact_id
                or incorrect.artifact_version_id != corrected.artifact_version_id
                or incorrect.evidence_ref != corrected.evidence_ref
                or incorrect.extraction_fingerprint == corrected.extraction_fingerprint
            ):
                raise ValueError("extraction correction must bind to the same source span and a new extraction")
            if str(incorrect_row["workflow_state"]) == "INVALIDATED":
                return
            old_receipt = self._connection.execute(
                """
                SELECT decision_id FROM semantic_materialization_receipt
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (workspace_id, incorrect_candidate_id),
            ).fetchone()
            old_decision_id = str(old_receipt["decision_id"]) if old_receipt is not None else None
            self._connection.execute(
                """
                UPDATE semantic_candidate SET workflow_state = 'INVALIDATED', updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ?
                """,
                (_to_storage_time(event_at), workspace_id, incorrect_candidate_id),
            )
            if old_decision_id is not None:
                self._connection.execute(
                    """
                    UPDATE decision SET memory_state = ?
                    WHERE workspace_id = ? AND decision_id = ?
                    """,
                    (DecisionMemoryState.INVALIDATED.value, workspace_id, old_decision_id),
                )
                self._connection.execute(
                    """
                    UPDATE decision_candidate SET status = ?, outcome = ?
                    WHERE workspace_id = ? AND decision_id = ?
                    """,
                    (
                        DecisionMemoryState.INVALIDATED.value,
                        "EXTRACTION_CORRECTION",
                        workspace_id,
                        old_decision_id,
                    ),
                )
            self._append_semantic_event_locked(
                workspace_id=workspace_id,
                candidate_id=incorrect_candidate_id,
                event_type="EXTRACTION_CORRECTED",
                actor_type="HUMAN",
                actor_id=reviewer_id,
                event_at=event_at,
                candidate_fingerprint=str(incorrect_row["candidate_fingerprint"]),
                reason_code="EXTRACTION_CORRECTION",
                reason=reason,
                metadata={
                    "corrected_candidate_id": corrected_candidate_id,
                    "invalidated_decision_id": old_decision_id,
                },
            )
            self._append_semantic_event_locked(
                workspace_id=workspace_id,
                candidate_id=corrected_candidate_id,
                event_type="CORRECTS_EXTRACTION",
                actor_type="HUMAN",
                actor_id=reviewer_id,
                event_at=event_at,
                candidate_fingerprint=str(corrected_row["candidate_fingerprint"]),
                reason_code="EXTRACTION_CORRECTION",
                reason=reason,
                metadata={
                    "incorrect_candidate_id": incorrect_candidate_id,
                    "invalidated_decision_id": old_decision_id,
                },
            )

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
        candidate = self._canonicalize_candidate_slot(candidate)
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
        prior = self.get_as_of(
            decision.workspace_id,
            decision.subject_key,
            decision.valid_from,
        )
        current = prior
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
            later = self._connection.execute(
                """
                SELECT decision_id FROM decision
                WHERE workspace_id = ? AND subject_key = ?
                  AND valid_from > ?
                  AND memory_state IN ('ACTIVE', 'SUPERSEDED')
                ORDER BY valid_from, decision_id
                LIMIT 1
                """,
                (
                    decision.workspace_id,
                    decision.subject_key,
                    _to_storage_time(decision.valid_from),
                ),
            ).fetchone()
            if later is not None and str(later["decision_id"]) == decision.supersedes_id:
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

        candidate = self._canonicalize_candidate_slot(candidate)
        self._register_candidate(candidate)

        decision = candidate.decision
        actions = candidate.actions
        if decision.status is not DecisionStatus.CURRENT:
            raise ValueError("new decisions must enter memory with CURRENT status")
        self._source_registry.validate_decision(decision)
        for action in actions:
            if action.workspace_id != decision.workspace_id:
                raise ValueError("action workspace_id must match its source decision")
            if action.source_decision_id != decision.decision_id:
                raise ValueError("action source_decision_id must match the materialized decision")
            self._source_registry.validate_action(action)

        with self._connection:
            now = _to_storage_time(datetime.now(UTC))
            if _to_storage_time(decision.valid_from) > now:
                future_rows = self._connection.execute(
                    """
                    SELECT decision_id FROM decision
                    WHERE workspace_id = ? AND subject_key = ?
                      AND valid_from > ?
                      AND memory_state IN ('ACTIVE', 'SUPERSEDED')
                    ORDER BY valid_from, decision_id
                    """,
                    (decision.workspace_id, decision.subject_key, now),
                ).fetchall()
                if future_rows:
                    raise ValueError("a future-effective successor already exists for this slot")
            self._write_decision_locked(decision)
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

    def _write_decision_locked(self, decision: DecisionRecord) -> None:
        """Write one authoritative record and close its temporal predecessor.

        The caller owns the SQLite transaction and has already validated the
        source and authorization. Both the generic and Semantic Ingestion
        materializers use this same lifecycle path.
        """
        current_row = self._connection.execute(
            """
            SELECT * FROM decision
            WHERE workspace_id = ? AND subject_key = ?
              AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
              AND memory_state IN ('ACTIVE', 'SUPERSEDED')
            ORDER BY valid_from DESC, decision_id
            LIMIT 1
            """,
            (
                decision.workspace_id,
                decision.subject_key,
                _to_storage_time(decision.valid_from),
                _to_storage_time(decision.valid_from),
            ),
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
                WHERE decision_id = ? AND workspace_id = ?
                """,
                (
                    DecisionStatus.SUPERSEDED.value,
                    _to_storage_time(decision.valid_from),
                    DecisionMemoryState.SUPERSEDED.value,
                    current.decision_id,
                    current.workspace_id,
                ),
            )
            self._connection.execute(
                """
                UPDATE decision_candidate
                SET status = ?, outcome = ?
                WHERE decision_id = ? AND workspace_id = ? AND status = ?
                """,
                (
                    DecisionMemoryState.SUPERSEDED.value,
                    DecisionMemoryState.SUPERSEDED.value,
                    current.decision_id,
                    current.workspace_id,
                    DecisionMemoryState.ACTIVE.value,
                ),
            )

        self._connection.execute(
            """
            INSERT INTO decision (
                decision_id, workspace_id, subject_key, subject, relation,
                subject_normalized, relation_normalized, value, status,
                memory_state, valid_from, valid_to, supersedes_id,
                source_episode_id, provenance_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.workspace_id,
                decision.subject_key,
                decision.subject,
                decision.relation,
                normalize_temporal_component(decision.subject)
                if decision.subject is not None
                else None,
                normalize_temporal_component(decision.relation)
                if decision.relation is not None
                else None,
                decision.value,
                decision.status.value,
                decision.memory_state.value,
                _to_storage_time(decision.valid_from),
                _to_storage_time(decision.valid_to) if decision.valid_to is not None else None,
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

    def get_decision(self, workspace_id: str, decision_id: str) -> DecisionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM decision WHERE workspace_id = ? AND decision_id = ?",
            (workspace_id, decision_id),
        ).fetchone()
        return self._row_to_decision(row) if row is not None else None

    def list_decisions(self, workspace_id: str) -> tuple[DecisionRecord, ...]:
        """List every decision state for one workspace, oldest first.

        Product browsing and reconciliation summaries need the full persisted
        lifecycle, including records that are no longer active.  Temporal
        retrieval should continue to use ``lookup``/``search``.
        """
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty text")
        rows = self._connection.execute(
            """SELECT * FROM decision WHERE workspace_id = ?
               ORDER BY valid_from, decision_id""",
            (workspace_id,),
        ).fetchall()
        return tuple(self._row_to_decision(row) for row in rows)

    def get_current(
        self,
        workspace_id: str,
        subject_key: str,
        *,
        as_of: datetime | None = None,
    ) -> DecisionRecord | None:
        return self.get_as_of(
            workspace_id,
            subject_key,
            as_of or datetime.now(UTC),
        )

    def get_history(self, workspace_id: str, subject_key: str) -> tuple[DecisionRecord, ...]:
        direct = self._connection.execute(
            "SELECT 1 FROM decision WHERE workspace_id = ? AND subject_key = ? LIMIT 1",
            (workspace_id, subject_key),
        ).fetchone()
        resolved_key = (
            subject_key
            if direct is not None
            else self._resolve_subject_key_alias(workspace_id, subject_key)
        )
        rows = self._connection.execute(
            """
            SELECT * FROM decision
            WHERE workspace_id = ? AND subject_key = ?
            ORDER BY valid_from, decision_id
            """,
            (workspace_id, resolved_key),
        ).fetchall()
        return tuple(self._row_to_decision(row) for row in rows)

    def get_as_of(
        self,
        workspace_id: str,
        subject_key: str,
        as_of: datetime,
    ) -> DecisionRecord | None:
        timestamp = _to_storage_time(as_of)
        direct = self._connection.execute(
            "SELECT 1 FROM decision WHERE workspace_id = ? AND subject_key = ? LIMIT 1",
            (workspace_id, subject_key),
        ).fetchone()
        resolved_key = (
            subject_key
            if direct is not None
            else self._resolve_subject_key_alias(workspace_id, subject_key)
        )
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
            (workspace_id, resolved_key, timestamp, timestamp),
        ).fetchone()
        return self._row_to_decision(row) if row is not None else None

    def lookup(
        self,
        workspace_id: str,
        subject: str,
        relation: str,
        *,
        as_of: datetime | None = None,
    ) -> TemporalLookupResult:
        """Look up exact normalized subject and relation components.

        This structured path never falls back to lexical matching. ``search``
        remains the legacy lexical API for callers that need free-text search.
        """
        normalized_subject = normalize_temporal_component(subject)
        normalized_relation = normalize_temporal_component(relation)
        if not normalized_subject:
            raise ValueError("subject must not be empty after normalization")
        if not normalized_relation:
            raise ValueError("relation must not be empty after normalization")
        timestamp = _to_storage_time(as_of or datetime.now(UTC))

        subject_exists = self._connection.execute(
            "SELECT 1 FROM decision "
            "WHERE workspace_id = ? AND subject_normalized = ? LIMIT 1",
            (workspace_id, normalized_subject),
        ).fetchone()
        if subject_exists is None:
            return TemporalLookupResult(TemporalLookupStatus.SUBJECT_NOT_FOUND)

        relation_exists = self._connection.execute(
            "SELECT 1 FROM decision "
            "WHERE workspace_id = ? AND subject_normalized = ? "
            "AND relation_normalized = ? LIMIT 1",
            (workspace_id, normalized_subject, normalized_relation),
        ).fetchone()
        if relation_exists is None:
            return TemporalLookupResult(TemporalLookupStatus.RELATION_NOT_FOUND)

        parameters: list[object] = [workspace_id, normalized_subject, normalized_relation]
        validity = (
            "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) "
            "AND memory_state IN ('ACTIVE', 'SUPERSEDED')"
        )
        parameters.extend((timestamp, timestamp))
        rows = self._connection.execute(
            f"""
            SELECT * FROM decision
            WHERE workspace_id = ?
              AND subject_normalized = ?
              AND relation_normalized = ?
              AND {validity}
            ORDER BY valid_from DESC, decision_id
            """,
            parameters,
        ).fetchall()
        records = tuple(self._row_to_decision(row) for row in rows)
        if not records:
            return TemporalLookupResult(TemporalLookupStatus.NO_VALID_RECORD_AT_TIME)
        return TemporalLookupResult(TemporalLookupStatus.FOUND, records)

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
        parameters: list[object] = [workspace_id, pattern, pattern, pattern, pattern, pattern]
        timestamp = _to_storage_time(as_of or datetime.now(UTC))
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
              AND (
                    subject_key LIKE ? COLLATE NOCASE
                 OR value LIKE ? COLLATE NOCASE
                 OR subject_normalized LIKE ? COLLATE NOCASE
                 OR relation_normalized LIKE ? COLLATE NOCASE
                 OR (COALESCE(subject_normalized, '') || ' ' ||
                     COALESCE(relation_normalized, '')) LIKE ? COLLATE NOCASE
              )
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
            subject=str(row["subject"]) if row["subject"] is not None else None,
            relation=str(row["relation"]) if row["relation"] is not None else None,
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


def _as_utc(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("temporal lookup requires a timezone-aware datetime")
        return value.astimezone(UTC)
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC)


def _from_storage_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)
