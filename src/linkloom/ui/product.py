"""Thin local application boundary for workspaces, artifacts, review and memory."""

from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
from threading import Lock, RLock, Thread
from typing import Any
from uuid import uuid4

from linkloom.decision_memory.models import DecisionMemoryState, DecisionRecord
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import (
    EvidenceSpan,
    RawArtifact,
    source_episode_id_for,
)
from linkloom.semantic_ingestion.candidate_models import (
    CandidateDecisionFact,
    CandidateReasonCode,
    SemanticExtractionResult,
)
from linkloom.semantic_ingestion.extraction import (
    SemanticExtractionContext,
    SemanticExtractor,
)
from linkloom.semantic_ingestion.materialization import (
    CandidateReviewResolution,
    CandidateWorkflowState,
    MaterializationBlockedError,
    SemanticDecisionMaterializer,
)
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
from linkloom.semantic_ingestion.validation import CandidateValidator


MAX_SOURCE_BYTES = 256 * 1024
DEFAULT_PRODUCT_RELATION_RESOLVER = FrozenRelationResolver(
    ("uses vendor",),
    schema_version="linkloom-product-relations/v1",
)
_SAFE_FILENAME = re.compile(r"^[^/\\\x00-\x1f]{1,180}$")
_ASK_STOP_WORDS = frozenset(
    "a an are as at be by current currently did do does for from had has have how i in is it of on or our the their them they this to use using was we what when where which who why with working".split()
)


class ProductApplicationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ProductRepository:
    """Durable app-owned workspace/source catalog beside the temporal store."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            timeout=30,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = Lock()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ll_workspace (
                workspace_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ll_product_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ll_source (
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                name TEXT NOT NULL,
                current_version_id TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                ingestion_status TEXT NOT NULL,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                review_required_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, source_id),
                FOREIGN KEY (workspace_id) REFERENCES ll_workspace(workspace_id)
            );
            CREATE TABLE IF NOT EXISTS ll_source_version (
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                artifact_version_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ingestion_status TEXT NOT NULL,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                review_required_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                PRIMARY KEY (workspace_id, source_id, artifact_version_id),
                FOREIGN KEY (workspace_id, source_id)
                    REFERENCES ll_source(workspace_id, source_id)
            );
            CREATE TABLE IF NOT EXISTS ll_source_evidence (
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                artifact_version_id TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                quote_sha256 TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                quote TEXT NOT NULL,
                PRIMARY KEY (workspace_id, evidence_ref),
                FOREIGN KEY (workspace_id, source_id, artifact_version_id)
                    REFERENCES ll_source_version(workspace_id, source_id, artifact_version_id)
            );
            CREATE TABLE IF NOT EXISTS ll_ingestion_segment (
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                artifact_version_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                state TEXT NOT NULL,
                retry_eligible INTEGER NOT NULL DEFAULT 0,
                extraction_json TEXT,
                failure_category TEXT,
                PRIMARY KEY (workspace_id, source_id, artifact_version_id, segment_id),
                FOREIGN KEY (workspace_id, source_id, artifact_version_id)
                    REFERENCES ll_source_version(workspace_id, source_id, artifact_version_id)
            );
            CREATE TABLE IF NOT EXISTS ll_ingestion_attempt (
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                artifact_version_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                response_json TEXT,
                outcome TEXT NOT NULL,
                failure_category TEXT,
                PRIMARY KEY (workspace_id, source_id, artifact_version_id, segment_id, attempt_number),
                FOREIGN KEY (workspace_id, source_id, artifact_version_id, segment_id)
                    REFERENCES ll_ingestion_segment(workspace_id, source_id, artifact_version_id, segment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ll_source_workspace_active
                ON ll_source(workspace_id, active, updated_at);
            CREATE INDEX IF NOT EXISTS idx_ll_source_evidence_version
                ON ll_source_evidence(workspace_id, source_id, artifact_version_id);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_workspace(self, name: str) -> dict[str, Any]:
        clean = _bounded_text(name, "workspace name", 120)
        workspace_id = f"ws_{uuid4().hex}"
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO ll_workspace(workspace_id, name, created_at) VALUES (?, ?, ?)",
                (workspace_id, clean, now),
            )
            self._connection.execute(
                "INSERT INTO ll_product_state(state_key, state_value) VALUES ('current_workspace', ?) "
                "ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value",
                (workspace_id,),
            )
        return {"workspace_id": workspace_id, "name": clean, "selected": True}

    def list_workspaces(self) -> tuple[dict[str, Any], ...]:
        current = self.current_workspace_id()
        with self._lock:
            rows = self._connection.execute(
                "SELECT workspace_id, name, created_at FROM ll_workspace ORDER BY created_at, workspace_id"
            ).fetchall()
        return tuple(
            {
                "workspace_id": str(row["workspace_id"]),
                "name": str(row["name"]),
                "created_at": str(row["created_at"]),
                "selected": str(row["workspace_id"]) == current,
            }
            for row in rows
        )

    def current_workspace_id(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_value FROM ll_product_state WHERE state_key = 'current_workspace'"
            ).fetchone()
        return str(row["state_value"]) if row is not None else None

    def current_workspace(self) -> dict[str, Any] | None:
        workspace_id = self.current_workspace_id()
        return self.get_workspace(workspace_id) if workspace_id else None

    def get_workspace(self, workspace_id: str | None) -> dict[str, Any] | None:
        if not workspace_id:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT workspace_id, name, created_at FROM ll_workspace WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "workspace_id": str(row["workspace_id"]),
            "name": str(row["name"]),
            "created_at": str(row["created_at"]),
            "selected": self.current_workspace_id() == str(row["workspace_id"]),
        }

    def select_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            raise ProductApplicationError("WORKSPACE_NOT_FOUND", "Choose an existing workspace.", status=404)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO ll_product_state(state_key, state_value) VALUES ('current_workspace', ?) "
                "ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value",
                (workspace_id,),
            )
        return self.get_workspace(workspace_id) or workspace

    def create_source(
        self,
        workspace_id: str,
        source_id: str,
        name: str,
        artifact: RawArtifact,
        spans: tuple[EvidenceSpan, ...],
    ) -> tuple[dict[str, Any], str | None]:
        return self._insert_source(workspace_id, source_id, name, artifact, spans), None

    def update_source(
        self,
        workspace_id: str,
        source_id: str,
        name: str,
        artifact: RawArtifact,
        spans: tuple[EvidenceSpan, ...],
    ) -> tuple[dict[str, Any], str | None, bool]:
        current = self.get_source(workspace_id, source_id, include_deleted=True)
        if current is None:
            raise ProductApplicationError("SOURCE_NOT_FOUND", "The source is unavailable in this workspace.", status=404)
        if not current["active"]:
            raise ProductApplicationError("SOURCE_DELETED", "Restore a deleted source by importing it again.", status=409)
        if current["ingestion_status"] == "INGESTING":
            raise ProductApplicationError("SOURCE_BUSY", "Wait for the current source ingestion to finish.", status=409)
        if current["content_sha256"] == artifact.content_hash:
            with self._lock, self._connection:
                self._connection.execute(
                    "UPDATE ll_source SET name = ?, updated_at = ? WHERE workspace_id = ? AND source_id = ?",
                    (name, _now(), workspace_id, source_id),
                )
            updated = self.get_source(workspace_id, source_id, include_deleted=True)
            assert updated is not None
            return updated, str(current["source_version"]), False
        updated = self._insert_source(workspace_id, source_id, name, artifact, spans)
        return updated, str(current["source_version"]), True

    def _insert_source(
        self,
        workspace_id: str,
        source_id: str,
        name: str,
        artifact: RawArtifact,
        spans: tuple[EvidenceSpan, ...],
    ) -> dict[str, Any]:
        now = _now()
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT created_at FROM ll_source WHERE workspace_id = ? AND source_id = ?",
                (workspace_id, source_id),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """INSERT INTO ll_source(
                        workspace_id, source_id, name, current_version_id, active,
                        ingestion_status, candidate_count, review_required_count,
                        error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 'INGESTING', 0, 0, NULL, ?, ?)""",
                    (workspace_id, source_id, name, artifact.artifact_version_id, now, now),
                )
                created_at = now
            else:
                created_at = str(existing["created_at"])
                self._connection.execute(
                    """UPDATE ll_source SET name = ?, current_version_id = ?, active = 1,
                       ingestion_status = 'INGESTING', candidate_count = 0,
                       review_required_count = 0, error_message = NULL, updated_at = ?
                       WHERE workspace_id = ? AND source_id = ?""",
                    (name, artifact.artifact_version_id, now, workspace_id, source_id),
                )
            self._connection.execute(
                """INSERT OR IGNORE INTO ll_source_version(
                    workspace_id, source_id, artifact_version_id, source_name, source_uri,
                    content_sha256, content, created_at, ingestion_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'INGESTING')""",
                (
                    workspace_id,
                    source_id,
                    artifact.artifact_version_id,
                    name,
                    artifact.source_ref,
                    artifact.content_hash,
                    artifact.content,
                    now,
                ),
            )
            self._connection.executemany(
                """INSERT OR IGNORE INTO ll_source_evidence(
                    workspace_id, source_id, artifact_version_id, evidence_ref,
                    content_sha256, quote_sha256, line_start, line_end, quote
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        workspace_id,
                        source_id,
                        artifact.artifact_version_id,
                        span.evidence_ref,
                        artifact.content_hash,
                        span.quote_sha256,
                        span.line_start,
                        span.line_end,
                        span.quote,
                    )
                    for span in spans
                ],
            )
        result = self.get_source(workspace_id, source_id, include_deleted=True)
        assert result is not None
        return result

    def set_ingestion_result(
        self,
        workspace_id: str,
        source_id: str,
        version_id: str,
        *,
        status: str,
        candidate_count: int,
        review_required_count: int,
        error: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE ll_source_version SET ingestion_status = ?, candidate_count = ?,
                   review_required_count = ?, error_message = ?
                   WHERE workspace_id = ? AND source_id = ? AND artifact_version_id = ?""",
                (status, candidate_count, review_required_count, error, workspace_id, source_id, version_id),
            )
            self._connection.execute(
                """UPDATE ll_source SET ingestion_status = ?, candidate_count = ?,
                   review_required_count = ?, error_message = ?, updated_at = ?
                   WHERE workspace_id = ? AND source_id = ? AND current_version_id = ?""",
                (status, candidate_count, review_required_count, error, _now(), workspace_id, source_id, version_id),
            )

    def set_review_required_count(
        self,
        workspace_id: str,
        source_id: str,
        version_id: str,
        review_required_count: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE ll_source_version SET review_required_count = ? "
                "WHERE workspace_id = ? AND source_id = ? AND artifact_version_id = ?",
                (review_required_count, workspace_id, source_id, version_id),
            )
            self._connection.execute(
                "UPDATE ll_source SET review_required_count = ? "
                "WHERE workspace_id = ? AND source_id = ? AND current_version_id = ?",
                (review_required_count, workspace_id, source_id, version_id),
            )

    def set_retrying(self, workspace_id: str, source_id: str) -> dict[str, Any]:
        source = self.get_source(workspace_id, source_id)
        if source is None:
            raise ProductApplicationError("SOURCE_NOT_FOUND", "The source is unavailable in this workspace.", status=404)
        if source["ingestion_status"] == "INGESTING":
            raise ProductApplicationError("SOURCE_BUSY", "The source is already ingesting.", status=409)
        states = self.segment_states(workspace_id, source_id, str(source["source_version"]))
        if states and not any(item["retry_eligible"] for item in states):
            raise ProductApplicationError("NO_RETRYABLE_SEGMENT", "No failed segment has a recovery attempt remaining.", status=409)
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE ll_source SET ingestion_status = 'INGESTING', updated_at = ?
                   WHERE workspace_id = ? AND source_id = ? AND current_version_id = ?""",
                (_now(), workspace_id, source_id, source["source_version"]),
            )
            self._connection.execute(
                """UPDATE ll_source_version SET ingestion_status = 'INGESTING'
                   WHERE workspace_id = ? AND source_id = ? AND artifact_version_id = ?""",
                (workspace_id, source_id, source["source_version"]),
            )
        result = self.get_source(workspace_id, source_id)
        assert result is not None
        return result

    def ensure_segments(self, workspace_id: str, source_id: str, version_id: str, segments: tuple[Any, ...]) -> None:
        with self._lock, self._connection:
            self._connection.executemany(
                """INSERT OR IGNORE INTO ll_ingestion_segment
                   (workspace_id, source_id, artifact_version_id, segment_id, ordinal, state)
                   VALUES (?, ?, ?, ?, ?, 'PENDING')""",
                [(workspace_id, source_id, version_id, segment.segment_id, segment.ordinal) for segment in segments],
            )

    def segment_states(self, workspace_id: str, source_id: str, version_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT segment.*, (SELECT COUNT(*) FROM ll_ingestion_attempt AS attempt
                    WHERE attempt.workspace_id = segment.workspace_id AND attempt.source_id = segment.source_id
                    AND attempt.artifact_version_id = segment.artifact_version_id
                    AND attempt.segment_id = segment.segment_id) AS attempt_count
                   FROM ll_ingestion_segment AS segment WHERE workspace_id = ? AND source_id = ?
                   AND artifact_version_id = ? ORDER BY ordinal""",
                (workspace_id, source_id, version_id),
            ).fetchall()
        return tuple({**dict(row), "retry_eligible": bool(row["retry_eligible"]) and int(row["attempt_count"]) < 2} for row in rows)

    def mark_segment_running(self, workspace_id: str, source_id: str, version_id: str, segment_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE ll_ingestion_segment SET state = 'RUNNING' WHERE workspace_id = ?
                   AND source_id = ? AND artifact_version_id = ? AND segment_id = ?""",
                (workspace_id, source_id, version_id, segment_id),
            )

    def save_segment_attempt(
        self, workspace_id: str, source_id: str, version_id: str, segment_id: str,
        extraction: SemanticExtractionResult, *, state: str, retry_eligible: bool,
        failure_category: str | None,
    ) -> None:
        response_json = (
            json.dumps(extraction.response_evidence, ensure_ascii=False, sort_keys=True)
            if extraction.response_evidence is not None else None
        )
        # Bound audit payloads while preserving exact parser input for ordinary
        # provider responses. Oversized responses are explicitly non-replayable.
        if response_json is not None and len(response_json.encode("utf-8")) > 1024 * 1024:
            response_json = None
            failure_category = "TRUNCATION"
            retry_eligible = False
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT COUNT(*) AS count FROM ll_ingestion_attempt WHERE workspace_id = ?
                   AND source_id = ? AND artifact_version_id = ? AND segment_id = ?""",
                (workspace_id, source_id, version_id, segment_id),
            ).fetchone()
            attempt_number = int(row["count"]) + 1
            if attempt_number > 2:
                raise ProductApplicationError("ATTEMPT_LIMIT", "Segment recovery limit reached.", status=409)
            self._connection.execute(
                """INSERT INTO ll_ingestion_attempt VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (workspace_id, source_id, version_id, segment_id, attempt_number,
                 json.dumps(extraction.receipt.to_dict(), ensure_ascii=False, sort_keys=True),
                 response_json, state, failure_category),
            )
            self._connection.execute(
                """UPDATE ll_ingestion_segment SET state = ?, retry_eligible = ?,
                   extraction_json = ?, failure_category = ? WHERE workspace_id = ? AND source_id = ?
                   AND artifact_version_id = ? AND segment_id = ?""",
                (state, int(retry_eligible and attempt_number < 2),
                 json.dumps(extraction.to_dict(), ensure_ascii=False, sort_keys=True) if state == "ACCEPTED" else None,
                 failure_category, workspace_id, source_id, version_id, segment_id),
            )

    def ingestion_attempts(self, workspace_id: str, source_id: str, version_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM ll_ingestion_attempt WHERE workspace_id = ? AND source_id = ?
                   AND artifact_version_id = ? ORDER BY segment_id, attempt_number""",
                (workspace_id, source_id, version_id),
            ).fetchall()
        attempts = []
        for row in rows:
            receipt = json.loads(row["receipt_json"])
            response = json.loads(row["response_json"]) if row["response_json"] else None
            call = None
            if response is not None:
                action = response.get("action") or {}
                if action.get("kind") == "tool_call":
                    call = action.get("tool_call")
                proposal = response.get("proposal") or {}
                if proposal.get("kind") == "tool_calls" and len(proposal.get("tool_calls", [])) == 1:
                    call = proposal["tool_calls"][0].get("runtime_call")
            latest = receipt["attempts"][-1]
            arguments = call.get("arguments") if isinstance(call, dict) else None
            attempts.append({
                "source_id": str(row["source_id"]),
                "source_version": str(row["artifact_version_id"]),
                "segment_id": str(row["segment_id"]),
                "attempt_number": int(row["attempt_number"]),
                "provider": receipt["provider_id"],
                "model": receipt["model_id"],
                "request_fingerprint": latest["request_sha256"],
                "response_received": latest["response_sha256"] is not None,
                "provider_status": (response.get("provider_metadata") or {}).get("http_status") if response else None,
                "finish_reason": response.get("finish_reason") if response else None,
                "provider_response_id": latest["provider_response_id"],
                "latency_ms": latest["latency_ms"],
                "tool_call_present": call is not None,
                "function_name": call.get("tool_id") if isinstance(call, dict) else None,
                "argument_representation_type": type(arguments).__name__ if arguments is not None else None,
                "structured_arguments": arguments,
                "outcome": str(row["outcome"]),
                "failure_category": row["failure_category"],
                "receipt": receipt,
                "response": response,
                "replayable": response is not None,
            })
        return tuple(attempts)

    def delete_source(self, workspace_id: str, source_id: str) -> dict[str, Any]:
        source = self.get_source(workspace_id, source_id, include_deleted=True)
        if source is None:
            raise ProductApplicationError("SOURCE_NOT_FOUND", "The source is unavailable in this workspace.", status=404)
        if source["ingestion_status"] == "INGESTING":
            raise ProductApplicationError("SOURCE_BUSY", "Wait for ingestion to finish before removing this source.", status=409)
        if source["active"]:
            with self._lock, self._connection:
                self._connection.execute(
                    """UPDATE ll_source SET active = 0, ingestion_status = 'DELETED',
                       updated_at = ? WHERE workspace_id = ? AND source_id = ?""",
                    (_now(), workspace_id, source_id),
                )
        result = self.get_source(workspace_id, source_id, include_deleted=True)
        assert result is not None
        return result

    def list_sources(self, workspace_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT source_id FROM ll_source WHERE workspace_id = ? AND active = 1 ORDER BY name, source_id",
                (workspace_id,),
            ).fetchall()
        return tuple(self.get_source(workspace_id, str(row["source_id"])) for row in rows)  # type: ignore[misc]

    def get_source(
        self,
        workspace_id: str,
        source_id: str,
        *,
        include_deleted: bool = False,
        include_content: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT source.*, version.source_name, version.source_uri,
                          version.content_sha256, version.content, version.created_at AS version_created_at
                   FROM ll_source AS source
                   JOIN ll_source_version AS version
                     ON version.workspace_id = source.workspace_id
                    AND version.source_id = source.source_id
                    AND version.artifact_version_id = source.current_version_id
                   WHERE source.workspace_id = ? AND source.source_id = ?""",
                (workspace_id, source_id),
            ).fetchone()
        if row is None or (not include_deleted and not bool(row["active"])):
            return None
        result = {
            "workspace_id": str(row["workspace_id"]),
            "source_id": str(row["source_id"]),
            "name": str(row["name"]),
            "source_version": str(row["current_version_id"]),
            "source_name_at_version": str(row["source_name"]),
            "source_uri": str(row["source_uri"]),
            "content_sha256": str(row["content_sha256"]),
            "active": bool(row["active"]),
            "ingestion_status": str(row["ingestion_status"]),
            "candidate_count": int(row["candidate_count"]),
            "review_required_count": int(row["review_required_count"]),
            "error": str(row["error_message"]) if row["error_message"] else None,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "version_created_at": str(row["version_created_at"]),
        }
        segments = self.segment_states(workspace_id, source_id, str(row["current_version_id"]))
        result["segment_summary"] = {
            "total": len(segments),
            "processed": sum(item["state"] == "ACCEPTED" for item in segments),
            "failed": sum(item["state"] in {"MALFORMED", "FAILED"} for item in segments),
            "retryable": sum(item["retry_eligible"] for item in segments),
        }
        if include_content:
            result["content"] = str(row["content"])
        return result

    def get_version(self, workspace_id: str, source_id: str, version_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM ll_source_version WHERE workspace_id = ? AND source_id = ?
                   AND artifact_version_id = ?""",
                (workspace_id, source_id, version_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def current_evidence(self, workspace_id: str, source_id: str | None = None) -> tuple[dict[str, Any], ...]:
        sql = """SELECT evidence.* FROM ll_source_evidence AS evidence
            JOIN ll_source AS source ON source.workspace_id = evidence.workspace_id
             AND source.source_id = evidence.source_id
             AND source.current_version_id = evidence.artifact_version_id
            WHERE source.active = 1 AND source.workspace_id = ?"""
        params: list[str] = [workspace_id]
        if source_id is not None:
            sql += " AND source.source_id = ?"
            params.append(source_id)
        sql += " ORDER BY evidence.source_id, evidence.line_start, evidence.evidence_ref"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return tuple(dict(row) for row in rows)

    def all_versions(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM ll_source_version ORDER BY workspace_id, source_id, created_at"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def source_inventory(self) -> dict[str, dict[str, str]]:
        with self._lock:
            workspaces = self._connection.execute(
                "SELECT workspace_id FROM ll_workspace ORDER BY workspace_id"
            ).fetchall()
            active_sources = self._connection.execute(
                "SELECT workspace_id, source_id, current_version_id FROM ll_source "
                "WHERE active = 1 ORDER BY workspace_id, source_id"
            ).fetchall()
        inventory = {
            str(row["workspace_id"]): {
                str(evidence["evidence_ref"]): str(evidence["content_sha256"])
                for evidence in self.current_evidence(str(row["workspace_id"]))
            }
            for row in workspaces
        }
        for source in active_sources:
            workspace_id = str(source["workspace_id"])
            source_id = str(source["source_id"])
            current_version_id = str(source["current_version_id"])
            current_version = self.get_version(workspace_id, source_id, current_version_id)
            if current_version is None:
                continue
            current_segments = _segments_for_source_version(current_version)
            current_signatures: dict[tuple[str, str, str], list[str]] = {}
            for segment in current_segments:
                signature = _evidence_identity(segment)
                current_signatures.setdefault(signature, []).append(segment.evidence_span.evidence_ref)
            for historical in self._source_versions(workspace_id, source_id):
                if str(historical["artifact_version_id"]) == current_version_id:
                    continue
                historical_signatures: dict[tuple[str, str, str], list[Any]] = {}
                for segment in _segments_for_source_version(historical):
                    historical_signatures.setdefault(_evidence_identity(segment), []).append(segment)
                for signature, old_segments in historical_signatures.items():
                    current_refs = current_signatures.get(signature, [])
                    if len(old_segments) != 1 or len(current_refs) != 1:
                        continue
                    # Keep the old evidence identity in the current inventory only
                    # when its timestamp, speaker, and exact quote still occur once
                    # in the current immutable version of this same source.
                    inventory[workspace_id][old_segments[0].evidence_span.evidence_ref] = str(
                        historical["content_sha256"]
                    )
        return inventory

    def _source_versions(self, workspace_id: str, source_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM ll_source_version WHERE workspace_id = ? AND source_id = ? "
                "ORDER BY created_at, artifact_version_id",
                (workspace_id, source_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def evidence_source(self, workspace_id: str, evidence_ref: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT evidence.*, version.source_name, version.source_uri,
                          version.content, version.created_at
                   FROM ll_source_evidence AS evidence
                   JOIN ll_source_version AS version
                     ON version.workspace_id = evidence.workspace_id
                    AND version.source_id = evidence.source_id
                    AND version.artifact_version_id = evidence.artifact_version_id
                   WHERE evidence.workspace_id = ? AND evidence.evidence_ref = ?""",
                (workspace_id, evidence_ref),
            ).fetchone()
        return dict(row) if row is not None else None

    def spans_for_version(self, workspace_id: str, source_id: str, version_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM ll_source_evidence WHERE workspace_id = ?
                   AND source_id = ? AND artifact_version_id = ? ORDER BY line_start, evidence_ref""",
                (workspace_id, source_id, version_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)


class ProductApplication:
    """Application-facing composition of existing ingestion/review/memory APIs."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        extractor: SemanticExtractor | None,
        relation_resolver: FrozenRelationResolver | None = None,
    ) -> None:
        self.repository = ProductRepository(database_path)
        self.extractor = extractor
        self.relation_resolver = relation_resolver or DEFAULT_PRODUCT_RELATION_RESOLVER
        self._ingest_lock = Lock()
        self._store_lock = RLock()
        registry = SourceReferenceRegistry()
        versions = self.repository.all_versions()
        for version in versions:
            artifact = self._artifact_from_version(version)
            registry.register_episode(artifact.workspace_id, source_episode_id_for(artifact))
        for workspace in self.repository.list_workspaces():
            workspace_id = str(workspace["workspace_id"])
            for evidence in self.repository.current_evidence(workspace_id):
                registry.register_evidence(
                    workspace_id,
                    str(evidence["evidence_ref"]),
                    content_hash=str(evidence["content_sha256"]),
                    source_version=str(evidence["artifact_version_id"]),
                )
        self.store = TemporalDecisionStore(
            self.repository.database_path,
            source_registry=registry,
            check_same_thread=False,
        )
        self.materializer = SemanticDecisionMaterializer(self.store)
        self._reconcile()

    def close(self) -> None:
        with self._store_lock:
            self.store.close()
            self.repository.close()

    def handle_get(self, path: str) -> dict[str, Any]:
        with self._store_lock:
            return self._handle_get(path)

    def _handle_get(self, path: str) -> dict[str, Any]:
        if path == "/api/status":
            return {
                "ready": self.extractor is not None,
                "message": (
                    "Semantic provider ready. Importing a source sends its text for extraction."
                    if self.extractor is not None
                    else "No semantic provider is enabled. Set GEMINI_API_KEY and start with --allow-external-provider to send source text for extraction."
                ),
            }
        if path == "/api/workspaces":
            return {"workspaces": list(self.repository.list_workspaces())}
        if path == "/api/workspaces/current":
            return {"workspace": self.repository.current_workspace()}
        if path == "/api/sources":
            workspace_id = self._workspace_id()
            return {"sources": list(self.repository.list_sources(workspace_id))}
        if path.startswith("/api/sources/"):
            source_id = path.removeprefix("/api/sources/")
            if source_id.endswith("/ingestion-attempts"):
                source_id = source_id.removesuffix("/ingestion-attempts")
                source = self.repository.get_source(self._workspace_id(), source_id)
                if source is None:
                    raise ProductApplicationError("SOURCE_NOT_FOUND", "The source is unavailable in this workspace.", status=404)
                return {"attempts": list(self.repository.ingestion_attempts(
                    str(source["workspace_id"]), source_id, str(source["source_version"])
                ))}
            source = self.repository.get_source(
                self._workspace_id(), source_id, include_content=True
            )
            if source is None:
                raise ProductApplicationError("SOURCE_NOT_FOUND", "The source is unavailable in this workspace.", status=404)
            source["evidence"] = list(
                self.repository.spans_for_version(
                    str(source["workspace_id"]), source_id, str(source["source_version"])
                )
            )
            source["segments"] = list(self.repository.segment_states(
                str(source["workspace_id"]), source_id, str(source["source_version"])
            ))
            for segment in source["segments"]:
                segment.pop("extraction_json", None)
            return {"source": source}
        if path == "/api/review/candidates":
            return {
                "candidates": self._review_candidates(),
                "supporting_proposals": self._supporting_proposals(),
            }
        if path == "/api/review/attention":
            return {"decisions": self._attention_records()}
        if path.startswith("/api/review/candidates/"):
            candidate_id = path.removeprefix("/api/review/candidates/")
            return {"candidate": self._candidate_detail(candidate_id)}
        if path == "/api/decisions":
            return {"decisions": self._current_decisions()}
        if path.startswith("/api/decisions/") and path.endswith("/history"):
            decision_id = path.removeprefix("/api/decisions/").removesuffix("/history")
            return {"history": self._decision_history(decision_id)}
        if path.startswith("/api/decisions/"):
            decision_id = path.removeprefix("/api/decisions/")
            return {"decision": self._decision_detail(decision_id)}
        raise ProductApplicationError("NOT_FOUND", "The requested product resource was not found.", status=404)

    def handle_post(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        with self._store_lock:
            return self._handle_post(path, payload)

    def _handle_post(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if path == "/api/workspaces":
            workspace = self.repository.create_workspace(_required_payload_text(payload, "name", 120))
            return {"workspace": workspace}, 201
        if path == "/api/sources/import":
            workspace_id = self._workspace_id()
            name, content = _source_payload(payload)
            source_id = f"src_{uuid4().hex}"
            artifact = self._artifact(workspace_id, source_id, content)
            spans = self._parse_artifact(artifact)
            source, _ = self.repository.create_source(workspace_id, source_id, name, artifact, spans)
            self._register_new_version(artifact, spans)
            self._reconcile()
            self._start_ingestion(workspace_id, str(source["source_id"]), artifact)
            return {"source": source}, 202
        if path == "/api/workspaces/current":
            workspace_id = _required_payload_text(payload, "workspace_id", 128)
            return {"workspace": self.repository.select_workspace(workspace_id)}, 200
        if path == "/api/ask":
            return {"answer": self.ask(_required_payload_text(payload, "query", 4000), payload.get("as_of"))}, 200
        candidate_match = re.fullmatch(r"/api/review/candidates/([^/]+)/(approve|reject)", path)
        if candidate_match:
            candidate_id, action = candidate_match.groups()
            if action == "reject":
                self._reject_candidate(candidate_id, payload)
                return {"workflow_state": CandidateWorkflowState.REJECTED.value}, 200
            return self._approve_candidate(candidate_id, payload), 200
        retry_match = re.fullmatch(r"/api/sources/([^/]+)/retry", path)
        if retry_match:
            workspace_id = self._workspace_id()
            source_id = retry_match.group(1)
            source = self.repository.set_retrying(workspace_id, source_id)
            artifact = self._source_artifact(source)
            self._start_ingestion(workspace_id, source_id, artifact)
            return {"source": source}, 202
        raise ProductApplicationError("NOT_FOUND", "The requested product resource was not found.", status=404)

    def handle_put(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        with self._store_lock:
            return self._handle_put(path, payload)

    def _handle_put(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        match = re.fullmatch(r"/api/sources/([^/]+)", path)
        if not match:
            raise ProductApplicationError("NOT_FOUND", "The requested product resource was not found.", status=404)
        workspace_id = self._workspace_id()
        source_id = match.group(1)
        name, content = _source_payload(payload)
        artifact = self._artifact(workspace_id, source_id, content)
        spans = self._parse_artifact(artifact)
        source, old_version, changed = self.repository.update_source(
            workspace_id, source_id, name, artifact, spans
        )
        if changed:
            self._register_new_version(artifact, spans, old_version=old_version)
            self._stale_review_candidates(workspace_id, source_id, artifact.artifact_version_id)
            self._reconcile()
            self._start_ingestion(workspace_id, source_id, artifact)
            return {"source": source, "changed": True}, 202
        self._reconcile()
        return {"source": source, "changed": False}, 200

    def handle_delete(self, path: str) -> dict[str, Any]:
        with self._store_lock:
            return self._handle_delete(path)

    def _handle_delete(self, path: str) -> dict[str, Any]:
        match = re.fullmatch(r"/api/sources/([^/]+)", path)
        if not match:
            raise ProductApplicationError("NOT_FOUND", "The requested product resource was not found.", status=404)
        workspace_id = self._workspace_id()
        source_id = match.group(1)
        source = self.repository.delete_source(workspace_id, source_id)
        self._unregister_source_current_evidence(workspace_id, source_id)
        self._stale_review_candidates(workspace_id, source_id, None)
        self._reconcile()
        return {"source": source}

    def ask(self, query: str, as_of: object = None) -> dict[str, Any]:
        workspace_id = self._workspace_id()
        timestamp = _parse_as_of(as_of)
        terms = [
            token.casefold()
            for token in re.findall(r"[\w-]+", query, flags=re.UNICODE)
            if len(token) > 2 and token.casefold() not in _ASK_STOP_WORDS
        ]
        if not terms:
            return {"status": "NO_MATCH", "message": "Add a decision term or select a historic date.", "results": []}
        matched: dict[str, tuple[DecisionRecord, int]] = {}
        for term in dict.fromkeys(terms):
            for decision in self.store.search(workspace_id, term, as_of=timestamp, limit=50):
                record, score = matched.get(decision.decision_id, (decision, 0))
                matched[decision.decision_id] = (record, score + 1)
        ranked = sorted(
            matched.values(),
            key=lambda item: (-item[1], item[0].subject or item[0].subject_key, item[0].relation or ""),
        )[:5]
        if not ranked:
            return {
                "status": "NO_MATCH",
                "message": "No active, source-backed decision matched this question.",
                "results": [],
            }
        answer = self._decision_detail(ranked[0][0].decision_id, at=timestamp)
        history = self._decision_history(ranked[0][0].decision_id)
        previous = next(
            (item for item in history if item["decision_id"] == answer.get("supersedes_id")),
            None,
        )
        answer["replaced"] = (
            {"value": previous["value"], "valid_to": previous["valid_to"], "evidence": previous["evidence"]}
            if previous is not None
            else None
        )
        return {
            "status": "FOUND",
            "query": query,
            "as_of": timestamp.isoformat() if timestamp else None,
            "answer": answer,
            "results": [self._decision_payload(record) for record, _ in ranked],
        }

    def _start_ingestion(self, workspace_id: str, source_id: str, artifact: RawArtifact) -> None:
        worker = Thread(
            target=self._ingest,
            args=(workspace_id, source_id, artifact),
            daemon=True,
            name=f"linkloom-ingest-{source_id[-8:]}",
        )
        worker.start()

    def _ingest(self, workspace_id: str, source_id: str, artifact: RawArtifact) -> None:
        try:
            if self.extractor is None:
                raise ProductApplicationError(
                    "PROVIDER_UNAVAILABLE",
                    "Configure a semantic Provider and explicitly enable source-content requests, then retry ingestion.",
                    status=503,
                )
            with self._ingest_lock:
                parsed = parse_timestamped_text(artifact)
                if parsed.issues or not parsed.segments:
                    raise ProductApplicationError(
                        "SOURCE_PARSE_FAILED",
                        "Use timestamped lines such as [2026-10-08T10:00:00Z] Speaker: decision text.",
                        status=422,
                    )
                context = SemanticExtractionContext(
                    expected_workspace_id=workspace_id,
                    relation_resolver=self.relation_resolver,
                )
                pipeline = SemanticIngestionPipeline(self.extractor, CandidateValidator())
                version_id = artifact.artifact_version_id
                self.repository.ensure_segments(workspace_id, source_id, version_id, parsed.segments)
                states = {item["segment_id"]: item for item in self.repository.segment_states(
                    workspace_id, source_id, version_id
                )}
                results = []
                new_segment_ids: set[str] = set()
                for segment in parsed.segments:
                    state = states[segment.segment_id]
                    if state["state"] == "ACCEPTED":
                        trace = pipeline.replay_extraction(
                            json.loads(state["extraction_json"]), segment, artifact, context
                        )
                    elif state["state"] == "PENDING" or state["retry_eligible"]:
                        new_segment_ids.add(segment.segment_id)
                        self.repository.mark_segment_running(workspace_id, source_id, version_id, segment.segment_id)
                        trace = pipeline.process_segment_with_trace(segment, artifact, context)
                        failure = trace.extraction.failure_code
                        semantic_rejected = failure is None and any(
                            item.candidate is None for item in trace.candidate_results
                        )
                        provider_error = (trace.extraction.response_evidence or {}).get("error")
                        retryable_transport = (
                            isinstance(provider_error, dict)
                            and provider_error.get("outcome") == "known_failure"
                            and provider_error.get("retryable") is True
                        )
                        retryable = (
                            failure is CandidateReasonCode.MALFORMED_EXTRACTION
                            and trace.extraction.response_evidence is not None
                        ) or retryable_transport
                        segment_state = (
                            "MALFORMED" if failure is CandidateReasonCode.MALFORMED_EXTRACTION
                            else "FAILED" if failure is not None or semantic_rejected
                            else "ACCEPTED"
                        )
                        category = (
                            "RETRYABLE_EXTRACTION" if retryable
                            else "NON_RETRYABLE_SEMANTIC" if semantic_rejected or (
                                failure is not None and failure not in {
                                    CandidateReasonCode.EXTRACTION_FAILED,
                                    CandidateReasonCode.PROVIDER_REQUEST_BLOCKED,
                                    CandidateReasonCode.MALFORMED_EXTRACTION,
                                }
                            ) else "TRANSPORT_FAILURE" if failure is not None else None
                        )
                        self.repository.save_segment_attempt(
                            workspace_id, source_id, version_id, segment.segment_id,
                            trace.extraction, state=segment_state,
                            retry_eligible=retryable, failure_category=category,
                        )
                    else:
                        continue
                    results.extend(trace.candidate_results)
                results = CandidateValidator().flag_conflicts(tuple(results))
            with self._store_lock:
                errors: list[str] = []
                for result in results:
                    if result.candidate is None:
                        errors.extend(code.value for code in result.reason_codes)
                        continue
                    if result.candidate.segment_id not in new_segment_ids:
                        continue
                    self.materializer.capture(
                        result,
                        expected_workspace_id=workspace_id,
                    )
                segment_states = self.repository.segment_states(workspace_id, source_id, artifact.artifact_version_id)
                errors.extend(
                    str(item["failure_category"] or item["state"])
                    for item in segment_states if item["state"] != "ACCEPTED"
                )
                count, needs_review = self._candidate_counts(workspace_id, source_id, artifact.artifact_version_id)
                status = "PARTIAL" if errors and count else "FAILED" if errors else "COMPLETE"
                self.repository.set_ingestion_result(
                    workspace_id,
                    source_id,
                    artifact.artifact_version_id,
                    status=status,
                    candidate_count=count,
                    review_required_count=needs_review,
                    error=("Some source claims could not be retained: " + ", ".join(dict.fromkeys(errors))) if errors else None,
                )
        except ProductApplicationError as error:
            with self._store_lock:
                self._finish_failed(workspace_id, source_id, artifact, error)
        except Exception:
            with self._store_lock:
                self._finish_failed(
                    workspace_id,
                    source_id,
                    artifact,
                    ProductApplicationError(
                        "INGESTION_FAILED",
                        "Semantic ingestion failed. Check the provider configuration or source timestamps, then retry.",
                        status=422,
                    ),
                )

    def _finish_failed(
        self,
        workspace_id: str,
        source_id: str,
        artifact: RawArtifact,
        error: ProductApplicationError,
    ) -> None:
        count, needs_review = self._candidate_counts(workspace_id, source_id, artifact.artifact_version_id)
        self.repository.set_ingestion_result(
            workspace_id,
            source_id,
            artifact.artifact_version_id,
            status="FAILED",
            candidate_count=count,
            review_required_count=needs_review,
            error=f"{error.code}: {error}",
        )

    def _candidate_counts(self, workspace_id: str, source_id: str, version_id: str) -> tuple[int, int]:
        rows = self.store.list_semantic_candidate_rows(
            workspace_id,
            artifact_id=source_id,
            artifact_version_id=version_id,
            candidate_source_kind="SOURCE_INGESTION",
        )
        review = sum(
            str(row["workflow_state"])
            in {CandidateWorkflowState.PENDING_REVIEW.value, CandidateWorkflowState.CONFLICT.value}
            for row in rows
        )
        return len(rows), review

    def _review_candidates(self) -> list[dict[str, Any]]:
        workspace_id = self._workspace_id()
        candidates = self.materializer.list_pending(workspace_id)
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, CandidateDecisionFact):
                continue
            source = self.repository.get_source(workspace_id, candidate.artifact_id)
            if source is None or source["source_version"] != candidate.artifact_version_id:
                continue
            result.append(self._candidate_payload(candidate))
        return result

    def _supporting_proposals(self) -> list[dict[str, Any]]:
        workspace_id = self._workspace_id()
        rows = self.store.list_semantic_candidate_rows(
            workspace_id,
            workflow_state=CandidateWorkflowState.SUPPORTING_ONLY.value,
            candidate_source_kind="SOURCE_INGESTION",
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            candidate_id = str(row["candidate_id"])
            candidate = self.materializer.get_candidate(workspace_id, candidate_id)
            if (
                not isinstance(candidate, CandidateDecisionFact)
                or candidate.claim_type.value != "PROPOSAL"
            ):
                continue
            source = self.repository.get_source(workspace_id, candidate.artifact_id)
            if source is None or source["source_version"] != candidate.artifact_version_id:
                continue
            result.append(self._candidate_detail(candidate_id))
        return result

    def _candidate_detail(self, candidate_id: str) -> dict[str, Any]:
        workspace_id = self._workspace_id()
        candidate = self.materializer.get_candidate(workspace_id, candidate_id)
        if not isinstance(candidate, CandidateDecisionFact):
            raise ProductApplicationError("CANDIDATE_NOT_FOUND", "The candidate is unavailable in this workspace.", status=404)
        source = self.repository.get_source(workspace_id, candidate.artifact_id, include_deleted=True)
        if source is None:
            raise ProductApplicationError("CANDIDATE_SOURCE_UNAVAILABLE", "The source record is unavailable.", status=409)
        version = self.repository.get_version(workspace_id, candidate.artifact_id, candidate.artifact_version_id)
        if version is None:
            raise ProductApplicationError("CANDIDATE_SOURCE_UNAVAILABLE", "The exact source version is unavailable.", status=409)
        artifact = self._artifact_from_version(version)
        if not candidate.provenance.verify(artifact):
            raise ProductApplicationError("PROVENANCE_INVALID", "The candidate evidence no longer verifies against its captured source version.", status=409)
        payload = self._candidate_payload(candidate)
        payload["source"] = {
            "source_id": candidate.artifact_id,
            "name": str(version["source_name"]),
            "source_version": candidate.artifact_version_id,
            "content_sha256": candidate.content_sha256,
            "active": bool(source["active"]),
        }
        payload["evidence"] = {
            "quote": candidate.provenance.quote,
            "line_start": candidate.provenance.line_start,
            "line_end": candidate.provenance.line_end,
            "source_version": candidate.artifact_version_id,
            "evidence_ref": candidate.evidence_ref,
        }
        payload["current_unresolved_fields"] = self._unresolved_fields(candidate)
        return payload

    def _approve_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace_id()
        candidate = self.materializer.get_candidate(workspace_id, candidate_id)
        if not isinstance(candidate, CandidateDecisionFact):
            raise ProductApplicationError("CANDIDATE_NOT_FOUND", "The candidate is unavailable in this workspace.", status=404)
        resolution = self._review_resolution(payload)
        fingerprint = self.materializer.candidate_fingerprint(workspace_id, candidate_id)
        if fingerprint is None:
            raise ProductApplicationError("CANDIDATE_NOT_FOUND", "The candidate is unavailable in this workspace.", status=404)
        try:
            self.materializer.approve(
                workspace_id,
                candidate_id,
                reviewer_id=_required_payload_text(payload, "reviewer_id", 120),
                expected_candidate_fingerprint=fingerprint,
                reason=_required_payload_text(payload, "reason", 1000),
                resolution=resolution,
            )
            materialized = self.materializer.materialize(workspace_id, candidate_id)
        except (MaterializationBlockedError, ValueError) as error:
            code = getattr(error, "reason_code", "REVIEW_NOT_READY")
            raise ProductApplicationError(code, "This candidate needs additional source-supported review before it can be approved.", status=409) from error
        if materialized.receipt is None:
            raise ProductApplicationError("MATERIALIZATION_PENDING", "Approval was recorded but no Decision Memory receipt was produced.", status=409)
        self._sync_review_required_count(candidate)
        decision = self.store.get_decision(workspace_id, materialized.receipt.decision_id)
        return {
            "candidate_id": candidate_id,
            "workflow_state": materialized.workflow_state.value,
            "decision_id": materialized.receipt.decision_id,
            "memory_state": decision.memory_state.value if decision else None,
            "receipt_id": materialized.receipt.receipt_id,
        }

    def _reject_candidate(self, candidate_id: str, payload: dict[str, Any]) -> None:
        workspace_id = self._workspace_id()
        fingerprint = self.materializer.candidate_fingerprint(workspace_id, candidate_id)
        if fingerprint is None:
            raise ProductApplicationError("CANDIDATE_NOT_FOUND", "The candidate is unavailable in this workspace.", status=404)
        try:
            self.materializer.reject(
                workspace_id,
                candidate_id,
                reviewer_id=_required_payload_text(payload, "reviewer_id", 120),
                expected_candidate_fingerprint=fingerprint,
                reason=_required_payload_text(payload, "reason", 1000),
            )
            self._sync_review_required_count(
                self.materializer.get_candidate(workspace_id, candidate_id)
            )
        except (MaterializationBlockedError, ValueError) as error:
            raise ProductApplicationError("REVIEW_STALE", "The candidate changed or is no longer reviewable. Refresh the Review page.", status=409) from error

    def _sync_review_required_count(self, candidate: Any) -> None:
        if not isinstance(candidate, CandidateDecisionFact):
            return
        _, review_required_count = self._candidate_counts(
            candidate.workspace_id,
            candidate.artifact_id,
            candidate.artifact_version_id,
        )
        self.repository.set_review_required_count(
            candidate.workspace_id,
            candidate.artifact_id,
            candidate.artifact_version_id,
            review_required_count,
        )

    @staticmethod
    def _review_resolution(payload: dict[str, Any]) -> CandidateReviewResolution | None:
        subject = _optional_text(payload.get("subject"), "subject", 512)
        relation = _optional_text(payload.get("relation"), "relation", 512)
        subject_id = _optional_text(payload.get("subject_id"), "subject_id", 512)
        raw_valid_from = _optional_text(payload.get("valid_from"), "valid_from", 64)
        valid_from: date | datetime | None = None
        if raw_valid_from:
            try:
                if "T" in raw_valid_from:
                    valid_from = datetime.fromisoformat(raw_valid_from.replace("Z", "+00:00"))
                    if valid_from.tzinfo is None or valid_from.utcoffset() is None:
                        raise ValueError("timezone required")
                else:
                    valid_from = date.fromisoformat(raw_valid_from)
            except ValueError as error:
                raise ProductApplicationError("INVALID_VALID_TIME", "Enter an explicit valid date or timezone-aware timestamp.") from error
        if subject is None and relation is None and valid_from is None:
            return None
        try:
            return CandidateReviewResolution(
                subject=subject,
                relation=relation,
                valid_from=valid_from,
                subject_id=subject_id,
            )
        except ValueError as error:
            raise ProductApplicationError("INVALID_REVIEW_RESOLUTION", str(error)) from error

    def _current_decisions(self) -> list[dict[str, Any]]:
        workspace_id = self._workspace_id()
        all_records = self.store.list_decisions(workspace_id)
        now = datetime.now(UTC)
        current = [
            record
            for record in all_records
            if record.valid_to is None and record.valid_from <= now
        ]
        return [self._decision_payload(record) for record in current]

    def _attention_records(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self._current_decisions()
            if record["memory_state"]
            in {DecisionMemoryState.STALE.value, DecisionMemoryState.NEEDS_REVALIDATION.value, DecisionMemoryState.INVALIDATED.value}
        ]

    def _decision_detail(self, decision_id: str, *, at: datetime | None = None) -> dict[str, Any]:
        workspace_id = self._workspace_id()
        record = self.store.get_decision(workspace_id, decision_id)
        if record is None:
            raise ProductApplicationError("DECISION_NOT_FOUND", "The decision is unavailable in this workspace.", status=404)
        if at is not None and record.valid_from > at:
            record = self.store.get_as_of(workspace_id, record.subject_key, at) or record
        return self._decision_payload(record)

    def _decision_history(self, decision_id: str) -> list[dict[str, Any]]:
        workspace_id = self._workspace_id()
        record = self.store.get_decision(workspace_id, decision_id)
        if record is None:
            raise ProductApplicationError("DECISION_NOT_FOUND", "The decision is unavailable in this workspace.", status=404)
        return [
            self._decision_payload(item)
            for item in self.store.get_history(workspace_id, record.subject_key)
        ]

    def _decision_payload(self, record: DecisionRecord) -> dict[str, Any]:
        evidence = [
            self._evidence_payload(record.workspace_id, ref)
            for ref in self.store.get_decision_evidence(record.workspace_id, record.decision_id)
        ]
        return {
            "decision_id": record.decision_id,
            "workspace_id": record.workspace_id,
            "subject": record.subject or "Needs review",
            "relation": record.relation or "Needs review",
            "subject_key": record.subject_key,
            "value": record.value,
            "status": record.status.value,
            "memory_state": record.memory_state.value,
            "valid_from": record.valid_from.isoformat(),
            "valid_to": record.valid_to.isoformat() if record.valid_to else None,
            "supersedes_id": record.supersedes_id,
            "source_episode_id": record.source_episode_id,
            "evidence": evidence,
        }

    def _evidence_payload(self, workspace_id: str, evidence_ref: str) -> dict[str, Any]:
        source = self.repository.evidence_source(workspace_id, evidence_ref)
        if source is None:
            return {"evidence_ref": evidence_ref, "verified": False, "source_name": "Source unavailable"}
        artifact = self._artifact_from_version(source)
        parsed = parse_timestamped_text(artifact)
        span = next((item.evidence_span for item in parsed.segments if item.evidence_span.evidence_ref == evidence_ref), None)
        if span is None or not span.verify(artifact):
            return {"evidence_ref": evidence_ref, "verified": False, "source_name": str(source["source_name"])}
        return {
            "evidence_ref": evidence_ref,
            "verified": True,
            "source_name": str(source["source_name"]),
            "source_id": str(source["source_id"]),
            "source_version": str(source["artifact_version_id"]),
            "line_start": span.line_start,
            "line_end": span.line_end,
            "quote": span.quote,
        }

    def _candidate_payload(self, candidate: CandidateDecisionFact) -> dict[str, Any]:
        row = self.store.get_semantic_candidate_row(candidate.workspace_id, candidate.candidate_id)
        source = self.repository.get_source(candidate.workspace_id, candidate.artifact_id, include_deleted=True)
        return {
            "candidate_id": candidate.candidate_id,
            "claim_type": candidate.claim_type.value,
            "subject": candidate.subject,
            "relation": candidate.relation,
            "relation_phrase": candidate.relation_phrase,
            "proposed_value": candidate.value,
            "valid_from": candidate.valid_from.isoformat() if candidate.valid_from else None,
            "valid_to": candidate.valid_to.isoformat() if candidate.valid_to else None,
            "review_reasons": [reason.value for reason in candidate.review_reasons],
            "status": str(row["workflow_state"]) if row else "UNKNOWN",
            "source_id": candidate.artifact_id,
            "source_name": str(source["name"]) if source else "Source unavailable",
            "source_version": candidate.artifact_version_id,
            "candidate_fingerprint": str(row["candidate_fingerprint"]) if row else None,
        }

    @staticmethod
    def _unresolved_fields(candidate: CandidateDecisionFact) -> list[str]:
        fields: list[str] = []
        if candidate.subject is None or candidate.subject_key is None:
            fields.append("subject")
        if candidate.relation is None:
            fields.append("relation")
        if candidate.valid_from is None:
            fields.append("valid_from")
        return fields

    def _stale_review_candidates(
        self,
        workspace_id: str,
        source_id: str,
        current_version_id: str | None,
    ) -> None:
        review_states = (
            CandidateWorkflowState.PENDING_REVIEW.value,
            CandidateWorkflowState.CONFLICT.value,
            CandidateWorkflowState.APPROVED.value,
            CandidateWorkflowState.POLICY_AUTHORIZED.value,
        )
        for row in self.store.list_semantic_candidate_rows(
            workspace_id,
            artifact_id=source_id,
            candidate_source_kind="SOURCE_INGESTION",
        ):
            if str(row["artifact_version_id"]) == current_version_id:
                continue
            state = str(row["workflow_state"])
            if state not in review_states:
                continue
            self.store.transition_semantic_candidate(
                workspace_id=workspace_id,
                candidate_id=str(row["candidate_id"]),
                candidate_fingerprint=str(row["candidate_fingerprint"]),
                expected_states=(state,),
                new_state=CandidateWorkflowState.STALE.value,
                event_type="SOURCE_VERSION_CHANGED",
                actor_type="SYSTEM",
                actor_id="product-source-lifecycle",
                event_at=datetime.now(UTC),
                reason_code="SOURCE_VERSION_CHANGED",
                reason="source version changed before candidate review completed",
            )

    def _register_new_version(
        self,
        artifact: RawArtifact,
        spans: tuple[EvidenceSpan, ...],
        *,
        old_version: str | None = None,
    ) -> None:
        if old_version:
            self._unregister_source_version_evidence(
                artifact.workspace_id,
                artifact.artifact_id,
                old_version,
            )
        registry = self.store.source_registry
        registry.register_episode(artifact.workspace_id, source_episode_id_for(artifact))
        for span in spans:
            registry.register_evidence(
                artifact.workspace_id,
                span.evidence_ref,
                content_hash=artifact.content_hash,
                source_version=artifact.artifact_version_id,
            )

    def _unregister_source_current_evidence(self, workspace_id: str, source_id: str) -> None:
        source = self.repository.get_source(workspace_id, source_id, include_deleted=True)
        if source is None:
            return
        self._unregister_source_version_evidence(
            workspace_id, source_id, str(source["source_version"])
        )

    def _unregister_source_version_evidence(self, workspace_id: str, source_id: str, version_id: str) -> None:
        registry = self.store.source_registry
        for evidence in self.repository.spans_for_version(workspace_id, source_id, version_id):
            registry.unregister_evidence(workspace_id, str(evidence["evidence_ref"]))

    def _reconcile(self) -> None:
        self.store.reconcile(self.repository.source_inventory())
        self._sync_all_source_review_counts()

    def _sync_all_source_review_counts(self) -> None:
        for workspace in self.repository.list_workspaces():
            workspace_id = str(workspace["workspace_id"])
            for source in self.repository.list_sources(workspace_id):
                _, review_required_count = self._candidate_counts(
                    workspace_id,
                    str(source["source_id"]),
                    str(source["source_version"]),
                )
                self.repository.set_review_required_count(
                    workspace_id,
                    str(source["source_id"]),
                    str(source["source_version"]),
                    review_required_count,
                )

    def _artifact(self, workspace_id: str, source_id: str, content: str) -> RawArtifact:
        return RawArtifact(
            workspace_id=workspace_id,
            artifact_id=source_id,
            source_type="timestamped_text",
            content=content,
            source_uri=f"linkloom://{workspace_id}/{source_id}",
        )

    def _parse_artifact(self, artifact: RawArtifact) -> tuple[EvidenceSpan, ...]:
        parsed = parse_timestamped_text(artifact)
        if parsed.issues or not parsed.segments:
            detail = parsed.issues[0].detail if parsed.issues else "no timestamped segments were found"
            raise ProductApplicationError(
                "SOURCE_PARSE_FAILED",
                f"This source needs timestamped lines such as [2026-10-08T10:00:00Z] Speaker: decision text ({detail}).",
                status=422,
            )
        return tuple(segment.evidence_span for segment in parsed.segments)

    def _source_artifact(self, source: dict[str, Any]) -> RawArtifact:
        version = self.repository.get_version(
            str(source["workspace_id"]), str(source["source_id"]), str(source["source_version"])
        )
        if version is None:
            raise ProductApplicationError("SOURCE_VERSION_NOT_FOUND", "The current source version is unavailable.", status=409)
        return self._artifact_from_version(version)

    def _artifact_from_version(self, version: dict[str, Any]) -> RawArtifact:
        return RawArtifact(
            workspace_id=str(version["workspace_id"]),
            artifact_id=str(version["source_id"]),
            source_type="timestamped_text",
            content=str(version["content"]),
            source_uri=str(version["source_uri"]),
            ingestion_time=_datetime_from_text(str(version.get("created_at", version.get("version_created_at", _now())))),
        )

    def _workspace_id(self) -> str:
        workspace = self.repository.current_workspace()
        if workspace is None:
            raise ProductApplicationError("WORKSPACE_REQUIRED", "Create or select a workspace first.", status=409)
        return str(workspace["workspace_id"])


def _bounded_text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ProductApplicationError("INVALID_REQUEST", f"{name} must be non-empty text of at most {limit} characters.")
    return value.strip()


def _optional_text(value: object, name: str, limit: int) -> str | None:
    if value is None or value == "":
        return None
    return _bounded_text(value, name, limit)


def _required_payload_text(payload: dict[str, Any], key: str, limit: int) -> str:
    return _bounded_text(payload.get(key), key, limit)


def _source_payload(payload: dict[str, Any]) -> tuple[str, str]:
    name = _required_payload_text(payload, "name", 180)
    if not _SAFE_FILENAME.fullmatch(name) or name in {".", ".."}:
        raise ProductApplicationError("INVALID_SOURCE_NAME", "Use a file name without path separators.")
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
        raise ProductApplicationError("INVALID_SOURCE_NAME", "The source name must stay inside the selected workspace.")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProductApplicationError("INVALID_SOURCE_CONTENT", "Add non-empty timestamped source text.")
    if len(content.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ProductApplicationError("SOURCE_TOO_LARGE", "The source is larger than the 256 KB V1 limit.", status=413)
    return name, content


def _parse_as_of(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ProductApplicationError("INVALID_AS_OF", "Choose a valid historical date.")
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError as error:
        raise ProductApplicationError("INVALID_AS_OF", "Choose a valid historical date.") from error
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)


def _datetime_from_text(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _segments_for_source_version(version: dict[str, Any]):
    artifact = RawArtifact(
        workspace_id=str(version["workspace_id"]),
        artifact_id=str(version["source_id"]),
        source_type="timestamped_text",
        content=str(version["content"]),
        source_uri=str(version["source_uri"]),
        ingestion_time=_datetime_from_text(str(version["created_at"])),
    )
    return parse_timestamped_text(artifact).segments


def _evidence_identity(segment: Any) -> tuple[str, str, str]:
    return (segment.event_time.isoformat(), segment.speaker, segment.body_text)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


__all__ = ["MAX_SOURCE_BYTES", "ProductApplication", "ProductApplicationError", "ProductRepository"]
