"""Bounded local task-review records and source-proof continuation.

This module deliberately owns a sidecar separate from the product database and
the relation-proof sidecar.  A local review statement is an unauthenticated,
self-asserted observation; it is never used as a relation verdict or business
approval.  Only deterministic source assessors may produce proof records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.lifecycle import DEFAULT_SHUTDOWN_TIMEOUT, shutdown_deadline, shutdown_lock


_RUN_ID = re.compile(r"^ui-[0-9a-f]{12}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ACTIONS = frozenset({"REVIEW", "WITHDRAW", "CONTINUE"})
_OUTCOMES = frozenset({"CHECKED", "UNSURE", "DISAGREE"})
_MAX_REVIEW_CAPABILITIES = 256
_MAX_REVIEW_RUN_SNAPSHOTS = 32


class TaskReviewConflict(RuntimeError):
    """A safe compare-and-swap or idempotency conflict."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskReviewStore:
    """Append-only local statement journal with per-run revision CAS."""

    schema_version = "task-review-store/v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.parent.is_dir():
            raise ValueError("task review sidecar parent must already exist")
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, timeout=30, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            tables = {
                row[0]
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables and "task_review_metadata" not in tables:
                raise ValueError("refuse to initialize a non-review database")
            if not tables:
                self._connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE task_review_metadata(schema_version TEXT NOT NULL);
                    CREATE TABLE task_review_statement(
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        expected_revision INTEGER NOT NULL,
                        revision INTEGER NOT NULL,
                        actor_label TEXT NOT NULL,
                        note TEXT NOT NULL,
                        outcome TEXT,
                        status TEXT NOT NULL,
                        error_code TEXT,
                        source_snapshot_hash TEXT NOT NULL,
                        source_catalog_hash TEXT NOT NULL,
                        pair_hash TEXT NOT NULL,
                        proof_head TEXT,
                        payload_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(workspace_id, request_id)
                    );
                    CREATE INDEX task_review_statement_run
                        ON task_review_statement(workspace_id, run_id, revision);
                    CREATE TABLE task_review_audit(
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error_code TEXT,
                        source_snapshot_hash TEXT NOT NULL,
                        source_catalog_hash TEXT NOT NULL,
                        pair_hash TEXT NOT NULL,
                        proof_head TEXT,
                        details_hash TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX task_review_audit_request
                        ON task_review_audit(workspace_id, run_id, request_id, sequence);
                    INSERT INTO task_review_metadata VALUES ('task-review-store/v1');
                    CREATE TRIGGER immutable_task_review_statement_update
                        BEFORE UPDATE ON task_review_statement
                        BEGIN SELECT RAISE(ABORT, 'append-only task review sidecar'); END;
                    CREATE TRIGGER immutable_task_review_statement_delete
                        BEFORE DELETE ON task_review_statement
                        BEGIN SELECT RAISE(ABORT, 'append-only task review sidecar'); END;
                    CREATE TRIGGER immutable_task_review_audit_update
                        BEFORE UPDATE ON task_review_audit
                        BEGIN SELECT RAISE(ABORT, 'append-only task review audit'); END;
                    CREATE TRIGGER immutable_task_review_audit_delete
                        BEFORE DELETE ON task_review_audit
                        BEGIN SELECT RAISE(ABORT, 'append-only task review audit'); END;
                    CREATE TRIGGER immutable_task_review_metadata_update
                        BEFORE UPDATE ON task_review_metadata
                        BEGIN SELECT RAISE(ABORT, 'append-only task review metadata'); END;
                    CREATE TRIGGER immutable_task_review_metadata_delete
                        BEFORE DELETE ON task_review_metadata
                        BEGIN SELECT RAISE(ABORT, 'append-only task review metadata'); END;
                    COMMIT;
                    """
                )
            else:
                versions = [
                    row[0]
                    for row in self._connection.execute(
                        "SELECT schema_version FROM task_review_metadata"
                    )
                ]
                if versions != [self.schema_version]:
                    raise ValueError("unsupported task review sidecar schema")
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS task_review_audit(
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error_code TEXT,
                        source_snapshot_hash TEXT NOT NULL,
                        source_catalog_hash TEXT NOT NULL,
                        pair_hash TEXT NOT NULL,
                        proof_head TEXT,
                        details_hash TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS task_review_audit_request
                        ON task_review_audit(workspace_id, run_id, request_id, sequence);
                    CREATE TRIGGER IF NOT EXISTS immutable_task_review_audit_update
                        BEFORE UPDATE ON task_review_audit
                        BEGIN SELECT RAISE(ABORT, 'append-only task review audit'); END;
                    CREATE TRIGGER IF NOT EXISTS immutable_task_review_audit_delete
                        BEFORE DELETE ON task_review_audit
                        BEGIN SELECT RAISE(ABORT, 'append-only task review audit'); END;
                    CREATE TRIGGER IF NOT EXISTS immutable_task_review_metadata_update
                        BEFORE UPDATE ON task_review_metadata
                        BEGIN SELECT RAISE(ABORT, 'append-only task review metadata'); END;
                    CREATE TRIGGER IF NOT EXISTS immutable_task_review_metadata_delete
                        BEFORE DELETE ON task_review_metadata
                        BEGIN SELECT RAISE(ABORT, 'append-only task review metadata'); END;
                    """
                )
                # Preserve append-only rows while upgrading sidecars created
                # by an earlier v1 worker cut that lacked proof-chain binding.
                audit_columns = {
                    str(row[1])
                    for row in self._connection.execute(
                        "PRAGMA table_info(task_review_audit)"
                    )
                }
                if "proof_head" not in audit_columns:
                    self._connection.execute(
                        "ALTER TABLE task_review_audit ADD COLUMN proof_head TEXT"
                    )

    def close(self, timeout=DEFAULT_SHUTDOWN_TIMEOUT, *, deadline=None) -> None:
        deadline = shutdown_deadline(timeout) if deadline is None else deadline
        with shutdown_lock(self._lock, deadline, "task-review-store-lock"):
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "TaskReviewStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        fields = {
            key: payload.get(key)
            for key in (
                "workspace_id",
                "run_id",
                "step_id",
                "action",
                "request_id",
                "expected_revision",
                "actor_label",
                "note",
                "outcome",
                "source_snapshot_hash",
                "source_catalog_hash",
                "pair_hash",
                "proof_head",
            )
        }
        return canonical_hash(fields)

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        required = {
            "workspace_id",
            "run_id",
            "step_id",
            "action",
            "request_id",
            "expected_revision",
            "actor_label",
            "note",
            "source_snapshot_hash",
            "source_catalog_hash",
            "pair_hash",
        }
        if not isinstance(payload, dict) or not required <= set(payload):
            raise ValueError("incomplete review journal payload")
        for key in (
            "workspace_id",
            "step_id",
            "request_id",
            "actor_label",
            "source_snapshot_hash",
            "source_catalog_hash",
            "pair_hash",
        ):
            value = payload[key]
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"invalid {key}")
        if not _RUN_ID.fullmatch(payload["run_id"]):
            raise ValueError("invalid run_id")
        if not _ID.fullmatch(payload["step_id"]) or not _ID.fullmatch(payload["request_id"]):
            raise ValueError("invalid review identifier")
        if payload["action"] not in _ACTIONS:
            raise ValueError("invalid review action")
        if type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0:
            raise ValueError("invalid expected_revision")
        if not isinstance(payload["note"], str) or len(payload["note"]) > 4096:
            raise ValueError("invalid review note")
        outcome = payload.get("outcome")
        if outcome is not None and outcome not in _OUTCOMES:
            raise ValueError("invalid review outcome")
        status = payload.get("status", "RECORDED")
        if status not in {"RECORDED", "BLOCKED"}:
            raise ValueError("invalid review journal status")
        error_code = payload.get("error_code")
        if error_code is not None and (not isinstance(error_code, str) or len(error_code) > 160):
            raise ValueError("invalid review error code")

    @staticmethod
    def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": int(row["sequence"]),
            "workspace_id": str(row["workspace_id"]),
            "run_id": str(row["run_id"]),
            "step_id": str(row["step_id"]),
            "action": str(row["action"]),
            "request_id": str(row["request_id"]),
            "expected_revision": int(row["expected_revision"]),
            "revision": int(row["revision"]),
            "actor_label": str(row["actor_label"]),
            "note": str(row["note"]),
            "outcome": row["outcome"],
            "status": str(row["status"]),
            "error_code": row["error_code"],
            "source_snapshot_hash": str(row["source_snapshot_hash"]),
            "source_catalog_hash": str(row["source_catalog_hash"]),
            "pair_hash": str(row["pair_hash"]),
            "proof_head": row["proof_head"],
            "created_at": str(row["created_at"]),
            "payload_hash": str(row["payload_hash"]),
        }

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate(payload)
        fingerprint = self._fingerprint(payload)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT * FROM task_review_statement WHERE workspace_id=? AND request_id=?",
                    (payload["workspace_id"], payload["request_id"]),
                ).fetchone()
                if existing is not None:
                    if existing["payload_hash"] != fingerprint:
                        raise TaskReviewConflict(
                            "REQUEST_ID_COLLISION", "REQUEST_ID_COLLISION: request_id was already used for a different payload."
                        )
                    self._connection.commit()
                    return self._row_payload(existing)
                current_row = self._connection.execute(
                    "SELECT MAX(revision) AS revision FROM task_review_statement "
                    "WHERE workspace_id=? AND run_id=?",
                    (payload["workspace_id"], payload["run_id"]),
                ).fetchone()
                current = int(current_row["revision"] or 0)
                if payload["expected_revision"] != current:
                    raise TaskReviewConflict(
                        "STALE_REVIEW_REVISION",
                        f"STALE_REVIEW_REVISION: expected review revision {payload['expected_revision']}, current is {current}.",
                    )
                revision = current + 1
                created_at = datetime.now(UTC).isoformat()
                values = (
                    payload["workspace_id"],
                    payload["run_id"],
                    payload["step_id"],
                    payload["action"],
                    payload["request_id"],
                    payload["expected_revision"],
                    revision,
                    payload["actor_label"],
                    payload["note"],
                    payload.get("outcome"),
                    payload.get("status", "RECORDED"),
                    payload.get("error_code"),
                    payload["source_snapshot_hash"],
                    payload["source_catalog_hash"],
                    payload["pair_hash"],
                    payload.get("proof_head"),
                    fingerprint,
                    created_at,
                )
                self._connection.execute(
                    """INSERT INTO task_review_statement(
                        workspace_id,run_id,step_id,action,request_id,expected_revision,
                        revision,actor_label,note,outcome,status,error_code,
                        source_snapshot_hash,source_catalog_hash,pair_hash,proof_head,
                        payload_hash,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                row = self._connection.execute(
                    "SELECT * FROM task_review_statement WHERE sequence=last_insert_rowid()"
                ).fetchone()
                assert row is not None
                result = self._row_payload(row)
                self._connection.commit()
                return result
            except Exception:
                self._connection.rollback()
                raise

    def history(self, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM task_review_statement WHERE workspace_id=? AND run_id=? ORDER BY revision, sequence",
                (workspace_id, run_id),
            ).fetchall()
            audit_rows = self._connection.execute(
                "SELECT * FROM task_review_audit WHERE workspace_id=? AND run_id=? ORDER BY sequence",
                (workspace_id, run_id),
            ).fetchall()
        statements = [self._row_payload(row) for row in rows]
        audits = [
            {
                "kind": "audit",
                "sequence": int(row["sequence"]),
                "workspace_id": str(row["workspace_id"]),
                "run_id": str(row["run_id"]),
                "step_id": str(row["step_id"]),
                "request_id": str(row["request_id"]),
                "phase": str(row["phase"]),
                "status": str(row["status"]),
                "error_code": row["error_code"],
                "source_snapshot_hash": str(row["source_snapshot_hash"]),
                "source_catalog_hash": str(row["source_catalog_hash"]),
                "pair_hash": str(row["pair_hash"]),
                "proof_head": row["proof_head"],
                "details_hash": row["details_hash"],
                "created_at": str(row["created_at"]),
            }
            for row in audit_rows
        ]
        return sorted(
            [*statements, *audits],
            key=lambda item: (item.get("created_at", ""), int(item.get("sequence", 0))),
        )

    def related_history(
        self,
        workspace_id: str,
        run_id: str,
        source_snapshot_hash: str,
        source_catalog_hash: str,
        pair_hashes: set[str] | frozenset[str],
    ) -> list[dict[str, Any]]:
        """Read old receipts only for the exact current source and pair set.

        This is a read-only projection aid for a newly-created Product run.
        It never participates in revision/CAS or capability validation, and
        it deliberately excludes the current run so callers can mark the
        records as related rather than current.
        """
        hashes = sorted({str(value) for value in pair_hashes if str(value).strip()})
        if not hashes:
            return []
        placeholders = ",".join("?" for _ in hashes)
        params = (workspace_id, run_id, source_snapshot_hash, source_catalog_hash, *hashes)
        with self._lock:
            statement_rows = self._connection.execute(
                "SELECT * FROM task_review_statement "
                "WHERE workspace_id=? AND run_id<>? AND source_snapshot_hash=? "
                f"AND source_catalog_hash=? AND pair_hash IN ({placeholders}) "
                "ORDER BY sequence",
                params,
            ).fetchall()
            audit_rows = self._connection.execute(
                "SELECT * FROM task_review_audit "
                "WHERE workspace_id=? AND run_id<>? AND source_snapshot_hash=? "
                f"AND source_catalog_hash=? AND pair_hash IN ({placeholders}) "
                "ORDER BY sequence",
                params,
            ).fetchall()
        statements = [self._row_payload(row) for row in statement_rows]
        audits = [
            {
                "kind": "audit",
                "sequence": int(row["sequence"]),
                "workspace_id": str(row["workspace_id"]),
                "run_id": str(row["run_id"]),
                "step_id": str(row["step_id"]),
                "request_id": str(row["request_id"]),
                "phase": str(row["phase"]),
                "status": str(row["status"]),
                "error_code": row["error_code"],
                "source_snapshot_hash": str(row["source_snapshot_hash"]),
                "source_catalog_hash": str(row["source_catalog_hash"]),
                "pair_hash": str(row["pair_hash"]),
                "proof_head": row["proof_head"],
                "details_hash": row["details_hash"],
                "created_at": str(row["created_at"]),
            }
            for row in audit_rows
        ]
        return sorted(
            [*statements, *audits],
            key=lambda item: (item.get("created_at", ""), int(item.get("sequence", 0))),
        )

    def find_request(self, workspace_id: str, run_id: str, request_id: str) -> dict[str, Any] | None:
        # request_id is unique within a workspace, not merely within one run.
        # Scope lookup to that constraint so cross-run reuse becomes a safe
        # REQUEST_ID_COLLISION rather than a raw SQLite uniqueness error.
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM task_review_statement WHERE workspace_id=? AND request_id=?",
                (workspace_id, request_id),
            ).fetchone()
        return self._row_payload(row) if row is not None else None

    def audit_for_request(self, workspace_id: str, run_id: str, request_id: str) -> list[dict[str, Any]]:
        # Match the statement journal's workspace-wide request ID constraint.
        # A pending request in another run is still the same reserved identity.
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM task_review_audit WHERE workspace_id=? AND request_id=? ORDER BY sequence",
                (workspace_id, request_id),
            ).fetchall()
        return [
            {
                "kind": "audit",
                "sequence": int(row["sequence"]),
                "workspace_id": str(row["workspace_id"]),
                "run_id": str(row["run_id"]),
                "step_id": str(row["step_id"]),
                "request_id": str(row["request_id"]),
                "phase": str(row["phase"]),
                "status": str(row["status"]),
                "error_code": row["error_code"],
                "source_snapshot_hash": str(row["source_snapshot_hash"]),
                "source_catalog_hash": str(row["source_catalog_hash"]),
                "pair_hash": str(row["pair_hash"]),
                "proof_head": row["proof_head"],
                "details_hash": row["details_hash"],
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def audit(self, payload: dict[str, Any], *, phase: str, status: str, error_code: str | None = None,
              details: object = None) -> dict[str, Any]:
        self._validate(payload)
        if phase not in {"PENDING", "COMMITTED", "FAILED"}:
            raise ValueError("invalid review audit phase")
        if status not in {"PENDING", "COMMITTED", "FAILED"}:
            raise ValueError("invalid review audit status")
        details_hash = canonical_hash(details) if details is not None else None
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO task_review_audit(
                    workspace_id,run_id,step_id,request_id,phase,status,error_code,
                    source_snapshot_hash,source_catalog_hash,pair_hash,proof_head,details_hash,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["workspace_id"], payload["run_id"], payload["step_id"], payload["request_id"],
                    phase, status, error_code, payload["source_snapshot_hash"],
                    payload["source_catalog_hash"], payload["pair_hash"], payload.get("proof_head"),
                    details_hash, _now(),
                ),
            )
            return {
                "kind": "audit",
                "sequence": int(cursor.lastrowid),
                "workspace_id": payload["workspace_id"],
                "run_id": payload["run_id"],
                "step_id": payload["step_id"],
                "request_id": payload["request_id"],
                "phase": phase,
                "status": status,
                "error_code": error_code,
                "source_snapshot_hash": payload["source_snapshot_hash"],
                "source_catalog_hash": payload["source_catalog_hash"],
                "pair_hash": payload["pair_hash"],
                "proof_head": payload.get("proof_head"),
                "details_hash": details_hash,
            }

    def revision(self, workspace_id: str, run_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT MAX(revision) AS revision FROM task_review_statement WHERE workspace_id=? AND run_id=?",
                (workspace_id, run_id),
            ).fetchone()
        return int(row["revision"] or 0)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _CapabilityIssuer:
    """Short-lived opaque capability bound to one current review view."""

    def __init__(self, ttl_seconds: int = 900) -> None:
        self.ttl_seconds = ttl_seconds
        self._issued: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock = RLock()

    def issue(self, binding: dict[str, Any]) -> str:
        import time

        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._issued = {key: entry for key, entry in self._issued.items() if entry[1] >= now}
            while len(self._issued) >= _MAX_REVIEW_CAPABILITIES:
                self._issued.pop(next(iter(self._issued)))
            self._issued[hashlib.sha256(token.encode("utf-8")).hexdigest()] = (
                dict(binding), now + self.ttl_seconds
            )
        return token

    def clear(self, *, deadline=None) -> None:
        deadline = shutdown_deadline() if deadline is None else deadline
        with shutdown_lock(self._lock, deadline, "task-review-capability-lock"):
            self._issued.clear()

    def binding(self, token: object) -> dict[str, Any] | None:
        """Return a copy of a live token binding for conflict classification."""
        import time

        if not isinstance(token, str) or not token or len(token) > 256:
            return None
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            entry = self._issued.get(key)
            if entry is None:
                return None
            expected, expiry = entry
            if expiry < time.time():
                self._issued.pop(key, None)
                return None
            return dict(expected)

    def check(self, token: object, binding: dict[str, Any]) -> bool:
        expected = self.binding(token)
        return expected is not None and hmac.compare_digest(
            canonical_hash(expected), canonical_hash(binding)
        )


def _safe_hash(value: object) -> str:
    return canonical_hash(value)


def _source_catalog_hash(sources: tuple[dict[str, Any], ...], evidence: tuple[dict[str, Any], ...]) -> str:
    """Hash catalog identity without storing or disclosing source body text."""
    return _safe_hash(
        {
            "sources": [
                {
                    key: source.get(key)
                    for key in ("source_id", "source_version", "content_sha256", "active", "ingestion_status")
                }
                for source in sorted(sources, key=lambda item: str(item.get("source_id")))
            ],
            "evidence": [
                {
                    key: row.get(key)
                    for key in ("evidence_ref", "source_id", "artifact_version_id", "content_sha256", "quote_sha256")
                }
                for row in sorted(evidence, key=lambda item: str(item.get("evidence_ref")))
            ],
        }
    )


def _step_pair_hash(step: dict[str, Any]) -> str:
    return _safe_hash(
        {
            "step_id": step.get("step_id"),
            "predicate": step.get("predicate") or "",
            "subject": step.get("endpoints", [None, None])[0]
            if step.get("endpoints")
            else None,
            "object": step.get("endpoints", [None, None])[1]
            if len(step.get("endpoints", [])) > 1
            else None,
        }
    )


def _endpoint_observation(endpoint: object, observations: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(endpoint, dict):
        return None
    observation_id = endpoint.get("observation_id")
    value = observations.get(str(observation_id))
    return json.loads(json.dumps(value, ensure_ascii=False)) if value is not None else None


def _proposal_for_step(step: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any] | None:
    predicate = step.get("predicate") or ""
    candidates = [proposal for proposal in proposals if proposal.get("predicate") == predicate]
    if len(candidates) == 1:
        return candidates[0]
    endpoints = step.get("endpoints") or []
    endpoint_ids = {
        endpoint.get("observation_id")
        for endpoint in endpoints
        if isinstance(endpoint, dict) and endpoint.get("observation_id")
    }
    for proposal in candidates:
        ids = {
            (proposal.get(name) or {}).get("observation_id")
            for name in ("subject", "object")
            if isinstance(proposal.get(name), dict)
        }
        if ids and ids == endpoint_ids:
            return proposal
    return candidates[0] if candidates else None


def _review_steps(review: dict[str, Any]) -> list[dict[str, Any]]:
    observations = {
        str(row.get("observation_id")): row
        for row in review.get("observations", [])
        if isinstance(row, dict) and row.get("observation_id")
    }
    proposals = [row for row in review.get("proposals", []) if isinstance(row, dict)]
    result: list[dict[str, Any]] = []
    for raw in review.get("steps", []):
        if not isinstance(raw, dict) or not raw.get("step_id"):
            continue
        endpoints = raw.get("endpoints") if isinstance(raw.get("endpoints"), list) else []
        step = dict(raw)
        step["predicate"] = raw.get("predicate") or ""
        step["subject"] = _endpoint_observation(endpoints[0] if endpoints else None, observations)
        step["object"] = _endpoint_observation(endpoints[1] if len(endpoints) > 1 else None, observations)
        proposal = _proposal_for_step(raw, proposals)
        step["supporting_evidence"] = json.loads(
            json.dumps((proposal or {}).get("supporting_evidence", []), ensure_ascii=False)
        )
        step["counter_evidence"] = json.loads(
            json.dumps((proposal or {}).get("counter_evidence", []), ensure_ascii=False)
        )
        reads = raw.get("relation_reads", [])
        comparisons = raw.get("comparisons", [])
        proof_status = "CURRENT_CONFIRMED" if raw.get("current_supported") else str(raw.get("status") or "BLOCKED")
        step["proof"] = {
            "status": proof_status,
            "assessment_ids": list(raw.get("assessment_ids", [])),
            "relation_reads": json.loads(json.dumps(reads, ensure_ascii=False)),
            "comparisons": json.loads(json.dumps(comparisons, ensure_ascii=False)),
        }
        blockers = list(raw.get("blocking_reasons") or raw.get("blockers") or [])
        if not raw.get("current_supported") and not blockers:
            blockers.append("SOURCE_PROOF_NOT_CURRENT")
        step["blockers"] = sorted({str(item) for item in blockers if item})
        step["pair_hash"] = _step_pair_hash(raw)
        result.append(step)
    return result


def _proof_status_from_reads(reads: list[Any], comparisons: list[Any]) -> str | None:
    """Return a current proof state only when every captured item verifies."""
    if not reads and not comparisons:
        return None
    if reads and not all(bool(read.current_supported) for read in reads):
        return "BLOCKED"
    if comparisons and not all(bool(read.current_valid) for read in comparisons):
        return "BLOCKED"
    return "CURRENT_CONFIRMED"


class TaskReviewService:
    """Host-owned review composition over one exact Product task run."""

    def __init__(
        self,
        product_app: Any,
        result_loader: Callable[[str, str], tuple[dict[str, Any], int]],
        *,
        enabled: bool = False,
        sidecar_path: str | Path | None = None,
        proof_path_provider: Callable[[str], str | Path | None] | None = None,
        capability_ttl_seconds: int = 900,
    ) -> None:
        self.product_app = product_app
        self.result_loader = result_loader
        self.enabled = bool(enabled)
        self.mode = "LOCAL_HOST_SELF_ASSERTED" if self.enabled else "DISABLED"
        self.proof_path_provider = proof_path_provider
        self._lock = RLock()
        self._issuer = _CapabilityIssuer(capability_ttl_seconds)
        self._store: TaskReviewStore | None = None
        # Runtime's sealed ledger predates any independent CONTINUE proof
        # rows.  Keep the current in-process projection so a retry can still
        # bind to that exact run while TaskReviewService rechecks the proof
        # sidecar itself.  Nothing here is persisted as a source quote.
        self._run_snapshots: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
        if self.enabled:
            if sidecar_path is None:
                database_path = str(product_app.repository.database_path)
                if database_path == ":memory:":
                    # An in-memory Product has no durable parent to bind a
                    # review journal to.  Do not silently fall back to a
                    # process-global cwd file; require an explicit durable
                    # sidecar instead.
                    self.enabled = False
                    self.mode = "DISABLED"
                    return
                else:
                    sidecar_path = Path(database_path).with_name(
                        Path(database_path).name + ".task-review.sqlite"
                    )
            sidecar = Path(sidecar_path).expanduser().resolve()
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            self._store = TaskReviewStore(sidecar)

    @property
    def store_path(self) -> Path | None:
        return self._store.path if self._store is not None else None

    def close(self, timeout=DEFAULT_SHUTDOWN_TIMEOUT, *, deadline=None) -> None:
        deadline = shutdown_deadline(timeout) if deadline is None else deadline
        with shutdown_lock(self._lock, deadline, "task-review-service-lock"):
            self._issuer.clear(deadline=deadline)
            if self._store is not None:
                self._store.close(deadline=deadline)
            self._run_snapshots.clear()

    def _remember_run(self, workspace_id: str, run_id: str, result: dict[str, Any], status: int) -> None:
        # This is a bounded retry convenience, never an authority store. If a
        # projection is evicted, the normal unavailable/refresh path fails closed.
        with self._lock:
            key = (workspace_id, run_id)
            self._run_snapshots.pop(key, None)
            self._run_snapshots[key] = (deepcopy(result), status)
            while len(self._run_snapshots) > _MAX_REVIEW_RUN_SNAPSHOTS:
                self._run_snapshots.pop(next(iter(self._run_snapshots)))

    def _disabled(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return {
            "available": False,
            "mode": self.mode,
            "business_approval": False,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "source_snapshot_hash": None,
            "source_catalog_hash": None,
            "revision": 0,
            "capability": None,
            "steps": [],
            "history": [],
            "authority": "LOCAL_SELF_ASSERTED_ONLY; NO_BUSINESS_APPROVAL; NO_VERDICT_OVERRIDE",
            "reason": "LOCAL_TASK_REVIEW_DISABLED",
        }

    def _check_identifiers(self, workspace_id: object, run_id: object) -> tuple[str, str]:
        if not isinstance(workspace_id, str) or not workspace_id.strip() or len(workspace_id) > 128:
            raise ValueError("workspace_id is required")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id is invalid")
        return workspace_id, run_id

    def _assert_current_workspace(self, workspace_id: str) -> None:
        current = self.product_app._workspace_id()
        if current != workspace_id:
            from linkloom.ui.product import ProductApplicationError

            raise ProductApplicationError(
                "WORKSPACE_MISMATCH",
                "The selected workspace changed; no task review data was released.",
                status=409,
            )

    def _current_context(self, workspace_id: str) -> tuple[Any, tuple[dict[str, Any], ...], str, str]:
        from linkloom.ui.task_investigation import ProductTaskSources

        _workspace, sources = self.product_app.snapshot_investigation_sources(workspace_id)
        evidence = tuple(self.product_app.repository.current_evidence(workspace_id))
        task_sources = ProductTaskSources(
            workspace_id,
            sources,
            lambda: self.product_app.snapshot_investigation_sources(workspace_id)[1],
            current_evidence=lambda: self.product_app.repository.current_evidence(workspace_id),
        )
        return task_sources, sources, task_sources.identity, _source_catalog_hash(sources, evidence)

    @staticmethod
    def _redact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        redacted = []
        for row in history:
            item = dict(row)
            item["note"] = None
            item["actor_label"] = None
            item["stale"] = True
            redacted.append(item)
        return redacted

    def _unavailable(
        self,
        workspace_id: str,
        run_id: str,
        *,
        source_snapshot_hash: str | None = None,
        source_catalog_hash: str | None = None,
        reason: str,
        history: list[dict[str, Any]] | None = None,
        revision: int = 0,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "mode": self.mode,
            "business_approval": False,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "source_snapshot_hash": source_snapshot_hash,
            "source_catalog_hash": source_catalog_hash,
            "revision": revision,
            "capability": None,
            "steps": [],
            # A blocked projection cannot prove that free-text notes are
            # unrelated to the stale source.  Keep only durable metadata.
            "history": self._redact_history(history or []),
            "authority": "LOCAL_SELF_ASSERTED_ONLY; NO_BUSINESS_APPROVAL; NO_VERDICT_OVERRIDE",
            "reason": reason,
        }

    def _refresh_relation_proofs(
        self,
        workspace_id: str,
        run_id: str,
        steps: list[dict[str, Any]],
        task_sources: Any,
    ) -> None:
        """Re-read captured proof rows without trusting persisted projections.

        A Product run is immutable, but the relation sidecar has its own
        append-only lifecycle.  Replaying the existing deterministic assessors
        gives a cold GET the same assessment/comparison IDs produced by
        CONTINUE; RelationQuery remains the only source of ``CURRENT_CONFIRMED``.
        """
        path = self._proof_path(workspace_id)
        try:
            _result, current_sources, observations = self._load_rebuild_context(workspace_id, run_id)
            outputs, assessments, _evidence = self._assess_steps(steps, observations, current_sources)
            self._project_source_diagnostics(steps, outputs, assessments)
            if path is None or not path.is_file():
                for step in steps:
                    step["proof"]["status"] = "BLOCKED"
                    step["proof"]["relation_reads"] = []
                    step["proof"]["comparisons"] = []
                    step["blockers"] = sorted(set((*step.get("blockers", []), "SOURCE_PROOF_NOT_CURRENT")))
                return
            from linkloom.semantic_ingestion.task_relation_query import RelationQuery

            with RelationQuery(
                path,
                artifacts=current_sources.artifacts,
                source_registry=current_sources.registry,
                observations=observations,
            ) as query:
                for step in steps:
                    step_id = str(step["step_id"])
                    raw_proof = step["proof"]
                    assessment = assessments.get(step_id)
                    assessment_ids = list(raw_proof.get("assessment_ids") or [])
                    if assessment is not None and assessment.verdict == "SUPPORTED":
                        assessment_ids = [assessment.assessment_id]
                    relation_reads = []
                    read_objects = []
                    for assessment_id in assessment_ids:
                        read = query.get(workspace_id, str(assessment_id))
                        if read is None:
                            continue
                        read_objects.append(read)
                        relation_reads.append(read.to_dict())
                    comparison_ids = []
                    output = outputs.get(step_id) or {}
                    comparison = output.get("comparison")
                    if comparison is not None:
                        comparison_ids.append(comparison.comparison_id)
                    else:
                        comparison_ids.extend(
                            str(item.get("comparison_id"))
                            for item in raw_proof.get("comparisons", [])
                            if isinstance(item, dict) and item.get("comparison_id")
                        )
                    comparison_reads = []
                    comparison_objects = []
                    for comparison_id in comparison_ids:
                        comparison_read = query.comparison(workspace_id, comparison_id)
                        if comparison_read is None:
                            continue
                        comparison_objects.append(comparison_read)
                        comparison_reads.append(comparison_read.to_dict())
                    if assessment_ids or comparison_ids:
                        raw_proof["assessment_ids"] = assessment_ids
                        raw_proof["relation_reads"] = relation_reads
                        raw_proof["comparisons"] = comparison_reads
                        status = _proof_status_from_reads(read_objects, comparison_objects)
                        raw_proof["status"] = status or "BLOCKED"
                        if status != "CURRENT_CONFIRMED":
                            reasons = [
                                reason
                                for read in read_objects
                                for reason in read.blocking_reasons
                            ]
                            reasons.extend(
                                reason
                                for read in comparison_objects
                                for reason in read.blocking_reasons
                            )
                            step["blockers"] = sorted(set(
                                (*step.get("blockers", []), *(str(reason) for reason in reasons),
                                  "SOURCE_PROOF_NOT_CURRENT")
                            ))
        except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError):
            # A proof-sidecar read is advisory to the Product projection.  A
            # malformed/unreadable sidecar must never make an old positive
            # projection look current.
            for step in steps:
                step["proof"]["status"] = "BLOCKED"
                step["proof"]["relation_reads"] = []
                step["proof"]["comparisons"] = []
                step["blockers"] = sorted(set((*step.get("blockers", []), "SOURCE_PROOF_READBACK_FAILED")))

    @staticmethod
    def _project_source_diagnostics(
        steps: list[dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
        assessments: dict[str, Any],
    ) -> None:
        """Expose deterministic source diagnostics separately from proof state."""
        for step in steps:
            step_id = str(step.get("step_id"))
            output = outputs.get(step_id) or {}
            assessment = assessments.get(step_id)
            comparison = output.get("comparison")
            if assessment is not None:
                verdict = str(assessment.verdict)
                supporting = [item.to_dict() for item in assessment.supporting_evidence]
                counter = [item.to_dict() for item in assessment.counter_evidence]
                blockers = [str(item) for item in assessment.reason_codes]
                source_id = assessment.assessment_id
                source_kind = "RELATION_ASSESSMENT"
            elif comparison is not None:
                verdict = str(comparison.status)
                supporting = [
                    item.to_dict()
                    for projection in (comparison.old_deadline, comparison.new_deadline)
                    for item in projection.evidence
                ]
                counter = []
                blockers = [str(item) for item in comparison.reason_codes]
                source_id = comparison.comparison_id
                source_kind = "REVISION_COMPARISON"
            else:
                raw_status = str(output.get("status") or "UNKNOWN")
                verdict = "UNKNOWN" if raw_status == "BLOCKED" else raw_status
                supporting = []
                counter = []
                blockers = [str(item) for item in output.get("blockers", []) if item]
                source_id = None
                source_kind = "DETERMINISTIC_SOURCE_DIAGNOSTIC"
            step["source_verdict"] = verdict
            step["source_verdict_source"] = source_kind
            step["source_assessment_id"] = source_id
            step["source_supporting_evidence"] = supporting
            step["source_counter_evidence"] = counter
            step["source_blockers"] = sorted(set(blockers))
            step["source_proof_status"] = step.get("proof", {}).get("status", "BLOCKED")

    def get(self, workspace_id: object, run_id: object) -> dict[str, Any]:
        """Read one exact run while serializing against Product mutations."""
        product_lock = getattr(self.product_app, "_store_lock", None)
        if product_lock is None:
            return self._get(workspace_id, run_id)
        with product_lock:
            return self._get(workspace_id, run_id)

    def _get(self, workspace_id: object, run_id: object) -> dict[str, Any]:
        workspace_id, run_id = self._check_identifiers(workspace_id, run_id)
        if not self.enabled:
            return self._disabled(workspace_id, run_id)
        self._assert_current_workspace(workspace_id)
        assert self._store is not None
        history = self._store.history(workspace_id, run_id)
        revision = self._store.revision(workspace_id, run_id)
        try:
            _sources, source_rows, source_hash, catalog_hash = self._current_context(workspace_id)
        except Exception:
            return self._unavailable(
                workspace_id,
                run_id,
                reason="SOURCE_CATALOG_UNAVAILABLE",
                history=self._redact_history(history),
                revision=revision,
            )
        try:
            result, status = self.result_loader(run_id, workspace_id)
        except Exception:
            return self._unavailable(
                workspace_id,
                run_id,
                source_snapshot_hash=source_hash,
                source_catalog_hash=catalog_hash,
                reason="RUN_NOT_CURRENT_OR_UNAVAILABLE",
                history=self._redact_history(history),
                revision=revision,
            )
        preparation = result.get("preparation") if isinstance(result, dict) else None
        review = result.get("task_review") if isinstance(result, dict) else None
        if not isinstance(preparation, dict) or not isinstance(review, dict):
            # A source-proof CONTINUE intentionally adds rows outside the
            # original Runtime ledger.  The Runtime may reject that old
            # ledger as RELATION_OR_SOURCE_CHANGED; use only the exact
            # in-process run projection captured before CONTINUE, then apply
            # current source/catalog checks and RelationQuery below.  Other
            # loader failures (including source/catalog invalidation) remain
            # unavailable and redacted.
            error_code = (result.get("error") or {}).get("code") if isinstance(result, dict) else None
            cached = self._run_snapshots.get((workspace_id, run_id))
            if error_code in {
                "RELATION_OR_SOURCE_CHANGED",
                "TASK_PREPARATION_UNAVAILABLE",
                "SAVED_RESULT_REVALIDATION_FAILED",
            } and cached is not None:
                result, status = deepcopy(cached[0]), cached[1]
                preparation = result.get("preparation") if isinstance(result, dict) else None
                review = result.get("task_review") if isinstance(result, dict) else None
        if not isinstance(preparation, dict) or not isinstance(review, dict):
            return self._unavailable(
                workspace_id,
                run_id,
                source_snapshot_hash=source_hash,
                source_catalog_hash=catalog_hash,
                reason="TASK_REVIEW_UNAVAILABLE",
                history=history,
                revision=revision,
            )
        self._remember_run(workspace_id, run_id, result, status)
        if preparation.get("source_snapshot_hash") != source_hash:
            return self._unavailable(
                workspace_id,
                run_id,
                source_snapshot_hash=source_hash,
                source_catalog_hash=catalog_hash,
                reason="SOURCE_SNAPSHOT_CHANGED",
                history=self._redact_history(history),
                revision=revision,
            )
        steps = _review_steps(review)
        if not steps:
            return self._unavailable(
                workspace_id,
                run_id,
                source_snapshot_hash=source_hash,
                source_catalog_hash=catalog_hash,
                reason="NO_REVIEWABLE_STEPS",
                history=history,
                revision=revision,
            )
        self._refresh_relation_proofs(workspace_id, run_id, steps, _sources)
        # Proof lifecycle revalidation may change proof.status after source
        # diagnostics were derived.  Keep this field a final projection of
        # RelationQuery state; source_verdict remains the independent
        # deterministic assessment diagnosis.
        for step in steps:
            step["source_proof_status"] = step.get("proof", {}).get("status", "BLOCKED")
        current_history = [dict(item, current_run=True) for item in history]
        related_history = [
            dict(item, current_run=False)
            for item in self._store.related_history(
                workspace_id,
                run_id,
                source_hash,
                catalog_hash,
                {str(step["pair_hash"]) for step in steps},
            )
        ]
        history = sorted(
            [*current_history, *related_history],
            key=lambda item: (item.get("created_at", ""), int(item.get("sequence", 0))),
        )
        pair_hash = _safe_hash(
            [{"step_id": step["step_id"], "pair_hash": step["pair_hash"]} for step in steps]
        )
        capability = self._issuer.issue(
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "source_snapshot_hash": source_hash,
                "source_catalog_hash": catalog_hash,
                "pair_hash": pair_hash,
                "revision": revision,
                "proof_head": self._proof_head(workspace_id),
            }
        )
        return {
            "available": True,
            "mode": self.mode,
            "business_approval": False,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "source_snapshot_hash": source_hash,
            "source_catalog_hash": catalog_hash,
            "revision": revision,
            "capability": capability,
            "steps": steps,
            "history": history,
            "authority": "LOCAL_SELF_ASSERTED_ONLY; SOURCE_PROOFS_REQUIRE_CONTINUE; NO_BUSINESS_APPROVAL",
            "status": result.get("status"),
            "run_status": status,
        }

    @staticmethod
    def _request_fields() -> set[str]:
        return {
            "workspace_id", "run_id", "step_id", "action", "request_id",
            "capability", "expected_revision", "actor_label", "note", "outcome",
        }

    def _proof_path(self, workspace_id: str) -> Path | None:
        if self.proof_path_provider is None:
            return None
        value = self.proof_path_provider(workspace_id)
        return Path(value).expanduser().resolve() if value is not None else None

    def _proof_head(self, workspace_id: str) -> str:
        path = self._proof_path(workspace_id)
        if path is None or not path.is_file():
            return "NO_RELATION_SIDECAR"
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                return self._proof_head_from_snapshot(self._proof_snapshot_from_connection(connection, workspace_id))
            finally:
                connection.close()
        except (OSError, sqlite3.DatabaseError):
            return "RELATION_SIDECAR_UNREADABLE"

    @staticmethod
    def _proof_snapshot_from_connection(connection: sqlite3.Connection, workspace_id: str) -> dict[str, tuple[tuple[Any, ...], ...]]:
        """Read only the immutable identity rows needed for lifecycle CAS."""
        return {
            "assessments": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT assessment_id, payload_hash FROM task_relation_assessment "
                    "WHERE workspace_id=? ORDER BY assessment_id", (workspace_id,)
                ).fetchall()
            ),
            "comparisons": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT comparison_id, payload_hash FROM task_revision_comparison "
                    "WHERE workspace_id=? ORDER BY comparison_id", (workspace_id,)
                ).fetchall()
            ),
            "events": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT sequence, assessment_id, event_id, event_hash FROM task_relation_event "
                    "WHERE workspace_id=? ORDER BY sequence", (workspace_id,)
                ).fetchall()
            ),
        }

    @staticmethod
    def _proof_head_from_snapshot(snapshot: dict[str, tuple[tuple[Any, ...], ...]]) -> str:
        return _safe_hash(snapshot)

    def _proof_snapshot(self, workspace_id: str) -> dict[str, tuple[tuple[Any, ...], ...]]:
        path = self._proof_path(workspace_id)
        if path is None or not path.is_file():
            return {"assessments": (), "comparisons": (), "events": ()}
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            return self._proof_snapshot_from_connection(connection, workspace_id)
        finally:
            connection.close()

    @staticmethod
    def _validate_proof_delta(
        before: dict[str, tuple[tuple[Any, ...], ...]],
        after: dict[str, tuple[tuple[Any, ...], ...]],
        captured: list[dict[str, Any]],
        comparisons: list[dict[str, Any]],
    ) -> None:
        """Allow only the exact rows CONTINUE was supposed to add.

        The relation store is append-only, so unchanged rows must remain byte
        identical.  A lifecycle event added for an existing assessment (or an
        extra event on a newly captured assessment) is an external mutation,
        not part of this CONTINUE transaction.
        """
        before_assessments = {str(row[0]): row for row in before["assessments"]}
        after_assessments = {str(row[0]): row for row in after["assessments"]}
        before_comparisons = {str(row[0]): row for row in before["comparisons"]}
        after_comparisons = {str(row[0]): row for row in after["comparisons"]}
        if any(after_assessments.get(key) != value for key, value in before_assessments.items()):
            raise ValueError("STALE_PROOF_HEAD")
        if any(after_comparisons.get(key) != value for key, value in before_comparisons.items()):
            raise ValueError("STALE_PROOF_HEAD")
        allowed_assessments = {str(item.get("assessment_id")) for item in captured}
        allowed_comparisons = {str(item.get("comparison_id")) for item in comparisons}
        if not set(after_assessments) - set(before_assessments) <= allowed_assessments:
            raise ValueError("STALE_PROOF_HEAD")
        if not set(after_comparisons) - set(before_comparisons) <= allowed_comparisons:
            raise ValueError("STALE_PROOF_HEAD")
        before_events = {str(row[2]): row for row in before["events"]}
        after_events = {str(row[2]): row for row in after["events"]}
        if any(after_events.get(key) != value for key, value in before_events.items()):
            raise ValueError("STALE_PROOF_HEAD")
        new_events = [row for key, row in after_events.items() if key not in before_events]
        new_assessment_ids = set(after_assessments) - set(before_assessments)
        event_counts: dict[str, int] = {}
        for row in new_events:
            assessment_id = str(row[1])
            event_counts[assessment_id] = event_counts.get(assessment_id, 0) + 1
            if assessment_id not in new_assessment_ids:
                raise ValueError("STALE_PROOF_HEAD")
        if set(event_counts) != new_assessment_ids or any(count != 1 for count in event_counts.values()):
            raise ValueError("STALE_PROOF_HEAD")

    def _load_rebuild_context(self, workspace_id: str, run_id: str):
        """Read the exact current preparation without invoking a model."""
        from linkloom.semantic_ingestion.task_observation import TaskObservation

        result, _status = self.result_loader(run_id, workspace_id)
        preparation = result.get("preparation") if isinstance(result, dict) else None
        if not isinstance(preparation, dict):
            error_code = (result.get("error") or {}).get("code") if isinstance(result, dict) else None
            cached = self._run_snapshots.get((workspace_id, run_id))
            if error_code in {
                "RELATION_OR_SOURCE_CHANGED",
                "TASK_PREPARATION_UNAVAILABLE",
                "SAVED_RESULT_REVALIDATION_FAILED",
            } and cached is not None:
                result, _status = deepcopy(cached[0]), cached[1]
                preparation = result.get("preparation") if isinstance(result, dict) else None
        if not isinstance(preparation, dict):
            raise ValueError("TASK_PREPARATION_UNAVAILABLE")
        task_sources, _source_rows, source_hash, _catalog_hash = self._current_context(workspace_id)
        if source_hash != preparation.get("source_snapshot_hash"):
            raise ValueError("SOURCE_SNAPSHOT_CHANGED")
        discovery = preparation.get("discovery") or {}
        observations = tuple(
            TaskObservation.from_dict(value)
            for value in (
                *(discovery.get("valid_observations") or []),
                *(discovery.get("partial_observations") or []),
            )
        )
        artifacts = {artifact.artifact_version_id: artifact for artifact in task_sources.artifacts}
        if any(
            observation.provenance.artifact_version_id not in artifacts
            or observation.provenance.workspace_id != workspace_id
            or not observation.verify(artifacts[observation.provenance.artifact_version_id])
            for observation in observations
        ):
            raise ValueError("SOURCE_OBSERVATION_NOT_CURRENT")
        return result, task_sources, observations

    @staticmethod
    def _observation_endpoints(step: dict[str, Any], observations):
        from linkloom.semantic_ingestion.task_relation_models import ObservationRef

        by_id = {observation.observation_id: observation for observation in observations}
        endpoints = step.get("endpoints") or []
        values = []
        for endpoint in endpoints[:2]:
            if not isinstance(endpoint, dict) or not endpoint.get("observation_id"):
                values.append(None)
                continue
            observation = by_id.get(endpoint["observation_id"])
            if observation is None or ObservationRef.from_observation(observation).to_dict() != endpoint:
                raise ValueError("STEP_ENDPOINT_NOT_CURRENT")
            values.append(observation)
        while len(values) < 2:
            values.append(None)
        return tuple(values[:2])

    @staticmethod
    def _proof_evidence(observations):
        from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, canonical_hash

        values = {}
        for observation in observations:
            span = observation.provenance
            quote = EvidenceQuote(span, span.char_start, span.char_end, span.quote, "RELATION_PROPOSAL")
            values[canonical_hash(quote.to_dict())] = quote
        return tuple(values[key] for key in sorted(values))

    @staticmethod
    def _step_for_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
        return next((step for step in steps if step.get("step_id") == step_id), None)

    @staticmethod
    def _required_steps(steps: list[dict[str, Any]], target_id: str) -> list[dict[str, Any]]:
        by_id = {step.get("step_id"): step for step in steps}
        needed: set[str] = set()

        def add(step_id: str) -> None:
            if step_id in needed:
                return
            step = by_id.get(step_id)
            if step is None:
                raise ValueError("STEP_NOT_FOUND")
            needed.add(step_id)
            for dependency in step.get("depends_on", []):
                add(str(dependency))

        add(target_id)
        return [step for step in steps if step.get("step_id") in needed]

    def _assess_steps(self, steps, observations, task_sources):
        from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
        from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
        from linkloom.semantic_ingestion.task_revision_comparison import compare_revision

        evidence = self._proof_evidence(observations)
        assessments: dict[str, Any] = {}
        outputs: dict[str, dict[str, Any]] = {}
        for step in steps:
            step_id = str(step["step_id"])
            predicate = step.get("predicate") or ""
            subject, object_ = self._observation_endpoints(step, observations)
            if predicate == "SAME_TASK":
                if subject is None or object_ is None:
                    outputs[step_id] = {"status": "BLOCKED", "blockers": ["STEP_ENDPOINT_NOT_CURRENT"]}
                    continue
                assessment = assess_same_task(
                    subject, object_, artifacts=task_sources.artifacts,
                    source_registry=task_sources.registry, context_observations=observations,
                )
                assessments[step_id] = assessment
            elif predicate == "REVISES_PLAN":
                identity = next(
                    (value for value in assessments.values() if value.predicate == "SAME_TASK"), None
                )
                if identity is None:
                    outputs[step_id] = {"status": "BLOCKED", "blockers": ["SUPPORTED_SAME_TASK_PREMISE_REQUIRED"]}
                    continue
                if subject is None or object_ is None:
                    outputs[step_id] = {"status": "BLOCKED", "blockers": ["STEP_ENDPOINT_NOT_CURRENT"]}
                    continue
                assessment = assess_plan_revision(
                    subject, object_, same_task=identity, artifacts=task_sources.artifacts,
                    source_registry=task_sources.registry, evidence=evidence,
                    context_observations=observations,
                )
                assessments[step_id] = assessment
            elif step.get("kind") == "compare_dates":
                identity = next(
                    (value for value in assessments.values() if value.predicate == "SAME_TASK"), None
                )
                revision = next(
                    (value for value in assessments.values() if value.predicate == "REVISES_PLAN"), None
                )
                if identity is None or revision is None:
                    outputs[step_id] = {"status": "BLOCKED", "blockers": ["SUPPORTED_RELATION_PREMISES_REQUIRED"]}
                    continue
                if subject is None or object_ is None:
                    outputs[step_id] = {"status": "BLOCKED", "blockers": ["STEP_ENDPOINT_NOT_CURRENT"]}
                    continue
                comparison = compare_revision(
                    subject, object_, same_task=identity, revision=revision,
                    artifacts=task_sources.artifacts, source_registry=task_sources.registry,
                    evidence=evidence, context_observations=observations,
                )
                outputs[step_id] = {"comparison": comparison, "status": comparison.status,
                                    "blockers": list(comparison.reason_codes)}
            else:
                outputs[step_id] = {"status": "BLOCKED", "blockers": ["NO_EXECUTABLE_OPERATION"]}
            if step_id in assessments:
                assessment = assessments[step_id]
                outputs[step_id] = {
                    "assessment": assessment, "status": assessment.verdict,
                    "blockers": list(assessment.reason_codes),
                    "supporting_evidence": [item.to_dict() for item in assessment.supporting_evidence],
                    "counter_evidence": [item.to_dict() for item in assessment.counter_evidence],
                }
        return outputs, assessments, evidence

    def _capture_supported_proofs(self, workspace_id, task_sources, observations, outputs, assessments, evidence):
        """Capture only deterministic SUPPORTED proofs, then re-read them."""
        from linkloom.semantic_ingestion.task_relation_query import RelationQuery
        from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore

        path = self._proof_path(workspace_id)
        if path is None or not path.parent.is_dir():
            raise ValueError("RELATION_SIDECAR_UNAVAILABLE")
        captured: list[dict[str, Any]] = []
        comparisons: list[Any] = []
        with TaskRelationStore(path) as store:
            for step_id, assessment in assessments.items():
                if assessment.verdict != "SUPPORTED":
                    continue
                proposals = evidence if assessment.predicate == "REVISES_PLAN" else ()
                store.put_assessment(assessment, evidence=proposals)
                captured.append({
                    "step_id": step_id,
                    "predicate": assessment.predicate,
                    "assessment_id": assessment.assessment_id,
                    "verdict": assessment.verdict,
                })
            for step_id, output in outputs.items():
                comparison = output.get("comparison")
                if comparison is None or comparison.status != "COMPUTABLE":
                    continue
                store.put_comparison(comparison)
                comparisons.append((step_id, comparison.comparison_id))
        with RelationQuery(
            path,
            artifacts=task_sources.artifacts,
            source_registry=task_sources.registry,
            observations=observations,
        ) as query:
            for item in captured:
                read = query.get(workspace_id, item["assessment_id"])
                if read is None or not read.current_supported:
                    raise ValueError("SOURCE_PROOF_READBACK_FAILED")
                item["current_supported"] = True
                item["evidence_state"] = read.evidence_state
            for step_id, comparison_id in comparisons:
                read = query.comparison(workspace_id, comparison_id)
                if read is None or not read.current_valid:
                    raise ValueError("SOURCE_COMPARISON_READBACK_FAILED")
        return captured, [{"step_id": step_id, "comparison_id": comparison_id, "current_valid": True}
                          for step_id, comparison_id in comparisons]

    def _store_payload(self, payload, view, step, *, status="RECORDED", error_code=None, proof_head=None):
        return {
            "workspace_id": payload["workspace_id"],
            "run_id": payload["run_id"],
            "step_id": payload["step_id"],
            "action": payload["action"],
            "request_id": payload["request_id"],
            "expected_revision": payload["expected_revision"],
            "actor_label": payload["actor_label"],
            "note": payload["note"],
            "outcome": payload.get("outcome"),
            "source_snapshot_hash": view["source_snapshot_hash"],
            "source_catalog_hash": view["source_catalog_hash"],
            "pair_hash": step["pair_hash"],
            "proof_head": proof_head if proof_head is not None else self._proof_head(payload["workspace_id"]),
            "status": status,
            "error_code": error_code,
        }

    def _blocked_receipt(self, journal_payload, error_code: str):
        assert self._store is not None
        journal_payload = dict(journal_payload, status="BLOCKED", error_code=error_code)
        receipt = self._store.append(journal_payload)
        audit = self._store.audit(journal_payload, phase="FAILED", status="FAILED", error_code=error_code)
        return receipt, audit

    def post(self, payload: object) -> tuple[dict[str, Any], int]:
        """Write one local statement while serializing against Product mutations."""
        product_lock = getattr(self.product_app, "_store_lock", None)
        if product_lock is None:
            return self._post(payload)
        with product_lock:
            return self._post(payload)

    def _post(self, payload: object) -> tuple[dict[str, Any], int]:
        from linkloom.ui.product import ProductApplicationError

        if not self.enabled:
            raise ProductApplicationError(
                "LOCAL_TASK_REVIEW_DISABLED",
                "Local task review is disabled; restart the localhost Host with --allow-local-task-review.",
                status=403,
            )
        if not isinstance(payload, dict):
            raise ProductApplicationError("INVALID_TASK_REVIEW_REQUEST", "The review request must be a JSON object.")
        if set(payload) - self._request_fields():
            raise ProductApplicationError(
                "INVALID_TASK_REVIEW_REQUEST",
                "assessment, verdict, source and path overrides are not accepted.",
            )
        required = self._request_fields() - {"outcome"}
        if not required <= set(payload):
            raise ProductApplicationError("INVALID_TASK_REVIEW_REQUEST", "A complete review request is required.")
        try:
            workspace_id, run_id = self._check_identifiers(payload.get("workspace_id"), payload.get("run_id"))
            action = payload.get("action")
            if action not in _ACTIONS:
                raise ValueError("invalid action")
            request_id = payload.get("request_id")
            actor_label = payload.get("actor_label")
            step_id = payload.get("step_id")
            if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
                raise ValueError("invalid request_id")
            if not isinstance(step_id, str) or not _ID.fullmatch(step_id):
                raise ValueError("invalid step_id")
            if not isinstance(actor_label, str) or not actor_label.strip() or len(actor_label) > 160:
                raise ValueError("invalid actor_label")
            if not isinstance(payload.get("note"), str) or len(payload["note"]) > 4096:
                raise ValueError("invalid note")
            if type(payload.get("expected_revision")) is not int or payload["expected_revision"] < 0:
                raise ValueError("invalid expected_revision")
            if action == "REVIEW" and payload.get("outcome") not in _OUTCOMES:
                raise ValueError("REVIEW requires CHECKED, UNSURE or DISAGREE")
            if payload.get("outcome") is not None and payload.get("outcome") not in _OUTCOMES:
                raise ValueError("invalid outcome")
        except ValueError as error:
            raise ProductApplicationError("INVALID_TASK_REVIEW_REQUEST", str(error)) from error

        self._assert_current_workspace(workspace_id)
        assert self._store is not None
        view = self.get(workspace_id, run_id)
        if not view["available"]:
            raise ProductApplicationError(
                str(view.get("reason") or "TASK_REVIEW_UNAVAILABLE"),
                "The exact current task review is unavailable; no old source details were released.",
                status=409,
            )
        step = self._step_for_id(view["steps"], str(payload["step_id"]))
        if step is None:
            raise ProductApplicationError("STEP_NOT_FOUND", "The requested review step is not part of this run.", status=409)
        pair_hash = _safe_hash(
            [{"step_id": item["step_id"], "pair_hash": item["pair_hash"]} for item in view["steps"]]
        )
        proof_head = self._proof_head(workspace_id)
        binding = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "source_snapshot_hash": view["source_snapshot_hash"],
            "source_catalog_hash": view["source_catalog_hash"],
            "pair_hash": pair_hash,
            "revision": view["revision"],
            "proof_head": proof_head,
        }
        existing = self._store.find_request(workspace_id, run_id, request_id)
        prior_audits = self._store.audit_for_request(workspace_id, run_id, request_id)
        if existing is not None:
            candidate = self._store._fingerprint(
                self._store_payload(payload, view, step, proof_head=existing.get("proof_head"))
            )
            if candidate != existing.get("payload_hash"):
                raise ProductApplicationError("REQUEST_ID_COLLISION", "request_id was already used for a different payload.", status=409)
            prior_binding = dict(binding, revision=existing["expected_revision"], proof_head=existing.get("proof_head"))
            committed = any(item.get("phase") == "COMMITTED" for item in prior_audits)
            repair_pending = (
                action == "CONTINUE"
                and existing.get("status") == "RECORDED"
                and not committed
                and any(item.get("phase") == "PENDING" for item in prior_audits)
                and existing.get("proof_head") == binding.get("proof_head")
            )
            replay_committed = (
                action == "CONTINUE"
                and existing.get("status") == "RECORDED"
                and committed
                and existing.get("proof_head") == binding.get("proof_head")
            )
            # The first CONTINUE capability was bound before source proofs
            # were captured.  If the review receipt was durably appended but
            # its COMMITTED audit failed, accept only an exact retry whose
            # live token still validates against that original PENDING head,
            # while requiring the proof head now equal the stored receipt.
            pending_capability = False
            if repair_pending or replay_committed:
                for audit in prior_audits:
                    if audit.get("phase") != "PENDING":
                        continue
                    pending_binding = dict(
                        binding,
                        revision=existing["expected_revision"],
                        proof_head=audit.get("proof_head"),
                    )
                    if self._issuer.check(payload.get("capability"), pending_binding):
                        pending_capability = True
                        break
            if not pending_capability and not self._issuer.check(payload.get("capability"), prior_binding):
                raise ProductApplicationError("CAPABILITY_INVALID", "The Host review capability is stale or invalid.", status=403)
            # A receipt may have committed in the review sidecar immediately
            # before its COMMITTED audit row failed.  The exact retry repairs
            # only that missing audit marker; it never writes another review
            # statement or replays source proof.
            if repair_pending and pending_capability:
                try:
                    committed_audit = self._store.audit(existing, phase="COMMITTED", status="COMMITTED")
                except Exception:
                    return {
                        "receipt": existing,
                        "audit": None,
                        "review": self.get(workspace_id, run_id),
                        "status": "BLOCKED",
                        "blockers": ["COMMITTED_AUDIT_UNAVAILABLE"],
                    }, 409
                return {
                    "receipt": existing,
                    "audit": committed_audit,
                    "review": self.get(workspace_id, run_id),
                    "status": "COMMITTED",
                    "idempotent": True,
                }, 200
            if replay_committed and pending_capability:
                return {
                    "receipt": existing,
                    "audit": next(
                        item for item in reversed(prior_audits)
                        if item.get("phase") == "COMMITTED"
                    ),
                    "review": self.get(workspace_id, run_id),
                    "status": "COMMITTED",
                    "idempotent": True,
                }, 200
            if action == "CONTINUE":
                # No generic CONTINUE receipt may fall through to a successful
                # idempotent response.  It is either repaired above, replayed
                # above, or remains explicitly blocked for a fresh safe retry.
                blocker = "STALE_PROOF_HEAD" if committed else "AUDIT_NOT_COMPLETE"
                return {
                    "receipt": existing,
                    "audit": next((item for item in reversed(prior_audits) if item.get("phase") != "COMMITTED"), None),
                    "review": self.get(workspace_id, run_id),
                    "status": "BLOCKED",
                    "blockers": [blocker],
                }, 409
            return {"receipt": existing, "review": self.get(workspace_id, run_id), "idempotent": True}, 200
        pending_request = next(
            (item for item in prior_audits if item.get("phase") == "PENDING"), None
        )
        if pending_request is not None:
            pending_fingerprint = canonical_hash(self._store._fingerprint(self._store_payload(
                payload, view, step, proof_head=pending_request.get("proof_head")
            )))
            if pending_request.get("details_hash") != pending_fingerprint:
                raise ProductApplicationError(
                    "REQUEST_ID_COLLISION", "request_id belongs to a different pending request.", status=409,
                )
        if payload["expected_revision"] != view["revision"]:
            raise ProductApplicationError(
                "STALE_REVIEW_REVISION",
                f"The review revision is stale; expected {payload['expected_revision']}, current is {view['revision']}.",
                status=409,
            )
        if not self._issuer.check(payload.get("capability"), binding):
            issued = self._issuer.binding(payload.get("capability"))
            stable_keys = (
                "workspace_id", "run_id", "source_snapshot_hash",
                "source_catalog_hash", "pair_hash",
            )
            same_view = issued is not None and all(
                issued.get(key) == binding.get(key) for key in stable_keys
            )
            if same_view and issued.get("revision") != binding.get("revision"):
                raise ProductApplicationError(
                    "STALE_REVIEW_REVISION",
                    "The Host review capability belongs to an older review revision.",
                    status=409,
                )
            recovery = (
                action == "CONTINUE"
                and same_view
                and pending_request is not None
                and bool(prior_audits)
                and any(item.get("phase") == "FAILED" for item in prior_audits)
                and prior_audits[-1].get("proof_head") == binding.get("proof_head")
                and issued.get("proof_head") == next(
                    (item.get("proof_head") for item in prior_audits if item.get("phase") == "PENDING"),
                    issued.get("proof_head"),
                )
            )
            if same_view and issued.get("proof_head") != binding.get("proof_head") and not recovery:
                raise ProductApplicationError(
                    "STALE_PROOF_HEAD",
                    "The source-proof lifecycle changed after this review view; refresh before continuing.",
                    status=409,
                )
            if not recovery:
                raise ProductApplicationError("CAPABILITY_INVALID", "The Host review capability is stale or invalid.", status=403)

        journal_payload = self._store_payload(payload, view, step, proof_head=proof_head)
        if action in {"REVIEW", "WITHDRAW"}:
            receipt = self._store.append(journal_payload)
            return {"receipt": receipt, "review": self.get(workspace_id, run_id), "idempotent": False}, 200
        return self._continue(payload, view, step, journal_payload)

    def _continue(self, payload, view, target_step, journal_payload):
        assert self._store is not None
        workspace_id = payload["workspace_id"]
        run_id = payload["run_id"]
        try:
            _result, task_sources, observations = self._load_rebuild_context(workspace_id, run_id)
            required = self._required_steps(view["steps"], target_step["step_id"])
            outputs, assessments, evidence = self._assess_steps(required, observations, task_sources)
        except Exception as error:
            code = str(error) if str(error) else "CONTINUE_REBUILD_FAILED"
            if code in {"SOURCE_SNAPSHOT_CHANGED", "SOURCE_OBSERVATION_NOT_CURRENT"}:
                # The attempted operation is still durable as a failed audit,
                # but its old source-bound note/quotes are never projected by
                # a subsequent unavailable GET.
                self._store.audit(
                    journal_payload,
                    phase="FAILED",
                    status="FAILED",
                    error_code=code,
                )
                raise __import__("linkloom.ui.product", fromlist=["ProductApplicationError"]).ProductApplicationError(
                    code,
                    "The current source snapshot changed; no stale quotes were released.",
                    status=409,
                ) from error
            self._store.audit(journal_payload, phase="FAILED", status="FAILED", error_code=code)
            return {
                "receipt": None,
                "review": self.get(workspace_id, run_id),
                "status": "BLOCKED",
                "blockers": [code],
            }, 409

        # A relation-sidecar lifecycle event can be appended independently of
        # the review sidecar.  Reject that race before writing a new audit or
        # proof record; a retry must refresh the Host capability.
        current_proof_head = self._proof_head(workspace_id)
        if current_proof_head != journal_payload.get("proof_head"):
            failed_payload = dict(journal_payload, proof_head=current_proof_head)
            failed = self._store.audit(
                failed_payload, phase="FAILED", status="FAILED", error_code="STALE_PROOF_HEAD"
            )
            raise __import__("linkloom.ui.product", fromlist=["ProductApplicationError"]).ProductApplicationError(
                "STALE_PROOF_HEAD",
                "The source-proof lifecycle changed after this review view; refresh before continuing.",
                status=409,
            )

        target_output = outputs.get(target_step["step_id"], {})
        blockers: list[str] = []
        for step in required:
            output = outputs.get(step["step_id"], {})
            predicate = step.get("predicate") or ""
            if predicate in {"SAME_TASK", "REVISES_PLAN"} and output.get("status") != "SUPPORTED":
                blockers.extend(str(item) for item in output.get("blockers", []) if item)
                blockers.append("SOURCE_PROOF_" + str(output.get("status") or "BLOCKED"))
            if step.get("kind") == "compare_dates" and output.get("status") != "COMPUTABLE":
                blockers.extend(str(item) for item in output.get("blockers", []) if item)
                blockers.append("SOURCE_COMPARISON_" + str(output.get("status") or "BLOCKED"))
        blockers = sorted(set(blockers))
        if blockers:
            error_code = blockers[0]
            receipt, audit = self._blocked_receipt(journal_payload, error_code)
            return {
                "receipt": receipt,
                "audit": audit,
                "review": self.get(workspace_id, run_id),
                "status": "BLOCKED",
                "blockers": blockers,
            }, 409

        proof_path = self._proof_path(workspace_id)
        proof_before = self._proof_snapshot(workspace_id)
        if proof_path is not None and proof_path.is_file():
            if self._proof_head_from_snapshot(proof_before) != journal_payload.get("proof_head"):
                failed_payload = dict(journal_payload, proof_head=self._proof_head(workspace_id))
                failed = self._store.audit(
                    failed_payload, phase="FAILED", status="FAILED", error_code="STALE_PROOF_HEAD"
                )
                return {
                    "receipt": None,
                    "audit": failed,
                    "pending_audit": None,
                    "review": self.get(workspace_id, run_id),
                    "status": "BLOCKED",
                    "blockers": ["STALE_PROOF_HEAD"],
                }, 409

        pending = self._store.audit(
            journal_payload, phase="PENDING", status="PENDING",
            details=self._store._fingerprint(journal_payload),
        )
        try:
            captured, comparisons = self._capture_supported_proofs(
                workspace_id, task_sources, observations, outputs, assessments, evidence
            )
        except Exception as error:
            code = str(error) if str(error) else "SOURCE_PROOF_CAPTURE_FAILED"
            failed_payload = dict(journal_payload, proof_head=self._proof_head(workspace_id))
            failed = self._store.audit(
                failed_payload, phase="FAILED", status="FAILED", error_code=code
            )
            if code == "RELATION_SIDECAR_UNAVAILABLE":
                receipt, final_audit = self._blocked_receipt(journal_payload, code)
                return {
                    "receipt": receipt,
                    "audit": final_audit,
                    "pending_audit": pending,
                    "review": self.get(workspace_id, run_id),
                    "status": "BLOCKED",
                    "blockers": [code],
                }, 409
            return {
                "receipt": None,
                "audit": failed,
                "pending_audit": pending,
                "review": self.get(workspace_id, run_id),
                "status": "BLOCKED",
                "blockers": [code],
            }, 409

        # The review CAS is checked after proof capture as well.  Proof is a
        # separate immutable source artifact; a concurrent local statement can
        # therefore reject this receipt without being overwritten.
        if self._store.revision(workspace_id, run_id) != payload["expected_revision"]:
            failed_payload = dict(journal_payload, proof_head=self._proof_head(workspace_id))
            failed = self._store.audit(
                failed_payload, phase="FAILED", status="FAILED", error_code="STALE_REVIEW_REVISION"
            )
            return {
                "receipt": None,
                "audit": failed,
                "pending_audit": pending,
                "review": self.get(workspace_id, run_id),
                "status": "BLOCKED",
                "blockers": ["STALE_REVIEW_REVISION"],
            }, 409
        # Hold a write transaction on the proof sidecar while the review
        # receipt and its COMMITTED audit are appended.  This closes the
        # capture/readback -> review-CAS race: external lifecycle events wait
        # until this transaction is complete, and an event that landed before
        # the guard is rejected unless it is exactly one of this CONTINUE's
        # newly captured records.
        proof_connection = None
        receipt = None
        final_payload = None
        try:
            proof_path = self._proof_path(workspace_id)
            if proof_path is None or not proof_path.is_file():
                raise ValueError("RELATION_SIDECAR_UNAVAILABLE")
            proof_connection = sqlite3.connect(proof_path, timeout=30, isolation_level=None)
            proof_connection.execute("PRAGMA busy_timeout = 30000")
            proof_connection.execute("BEGIN IMMEDIATE")
            proof_after = self._proof_snapshot_from_connection(proof_connection, workspace_id)
            self._validate_proof_delta(proof_before, proof_after, captured, comparisons)
            final_payload = dict(
                journal_payload,
                proof_head=self._proof_head_from_snapshot(proof_after),
            )
            receipt = self._store.append(final_payload)
            committed = self._store.audit(final_payload, phase="COMMITTED", status="COMMITTED")
            proof_connection.commit()
            return {
                "receipt": receipt,
                "audit": committed,
                "pending_audit": pending,
                "review": self.get(workspace_id, run_id),
                "status": "COMMITTED",
                "proof": {"assessments": captured, "comparisons": comparisons},
            }, 200
        except Exception as error:
            code = str(error) if str(error) else "CONTINUE_COMMIT_FAILED"
            failed_payload = dict(
                final_payload or journal_payload,
                proof_head=(
                    self._proof_head_from_snapshot(proof_after)
                    if "proof_after" in locals()
                    else self._proof_head(workspace_id)
                ),
            )
            try:
                failed = self._store.audit(
                    failed_payload, phase="FAILED", status="FAILED", error_code=code
                )
            except Exception:
                failed = None
            if proof_connection is not None:
                try:
                    proof_connection.rollback()
                except sqlite3.DatabaseError:
                    pass
            return {
                "receipt": receipt,
                "audit": failed,
                "pending_audit": pending,
                "review": self.get(workspace_id, run_id),
                "status": "BLOCKED",
                "blockers": [code],
            }, 409
        finally:
            if proof_connection is not None:
                proof_connection.close()
