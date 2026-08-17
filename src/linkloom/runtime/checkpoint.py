"""Durable checkpointer implementations (InMemory and SQLite) for Linkloom Runtime."""

from __future__ import annotations

import abc
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from linkloom.runtime.errors import (
    CheckpointCorruptError,
    CheckpointError,
    CheckpointWriteError,
    RuntimeSchemaIncompatibleError,
    ThreadNotFoundError,
)
from linkloom.runtime.models import RuntimeState


class BaseCheckpointer(abc.ABC):
    """Abstract base class for Linkloom state checkpointers."""

    @abc.abstractmethod
    def save(self, state: RuntimeState, checkpoint_id: str | None = None) -> str:
        """Persist runtime state and return the unique checkpoint_id."""
        raise NotImplementedError

    @abc.abstractmethod
    def load(self, thread_id: str, checkpoint_id: str | None = None) -> RuntimeState:
        """Load state for a thread. If checkpoint_id is None, loads the latest state."""
        raise NotImplementedError

    @abc.abstractmethod
    def list_checkpoints(self, thread_id: str) -> list[dict[str, Any]]:
        """List checkpoint metadata records for a thread in chronological order."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_latest(self, thread_id: str) -> RuntimeState | None:
        """Return the latest state for thread_id, or None if thread does not exist."""
        raise NotImplementedError


class InMemoryCheckpointer(BaseCheckpointer):
    """In-memory checkpointer suitable for unit testing and ephemeral executions."""

    def __init__(self) -> None:
        # thread_id -> list of (checkpoint_id, state_dict, created_at)
        self._threads: dict[str, list[dict[str, Any]]] = {}

    def save(self, state: RuntimeState, checkpoint_id: str | None = None) -> str:
        thread_id = state.thread_id
        if thread_id not in self._threads:
            self._threads[thread_id] = []

        history = self._threads[thread_id]
        cid = checkpoint_id or f"cp_{state.step_seq:04d}_{len(history) + 1:04d}"

        if any(item["checkpoint_id"] == cid for item in history):
            raise CheckpointError(
                f"Duplicate checkpoint_id '{cid}' for thread '{thread_id}'.",
                details={"thread_id": thread_id, "checkpoint_id": cid},
            )

        # A JSON round-trip gives the in-memory adapter the same immutability
        # boundary as SQLite and prevents callers from mutating nested lists
        # after a checkpoint has been saved.
        try:
            state_dict = json.loads(
                json.dumps(state.to_dict(), ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError) as err:
            raise CheckpointWriteError(
                f"Failed to serialize RuntimeState to JSON: {err}",
                details={"run_id": state.run_id, "error": str(err)},
            ) from err
        record = {
            "checkpoint_id": cid,
            "thread_id": thread_id,
            "run_id": state.run_id,
            "step_seq": state.step_seq,
            "status": state.status,
            "current_step": state.current_step,
            "state_dict": state_dict,
            "created_at": state.updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        history.append(record)
        return cid

    def load(self, thread_id: str, checkpoint_id: str | None = None) -> RuntimeState:
        if thread_id not in self._threads or not self._threads[thread_id]:
            raise ThreadNotFoundError(
                f"Thread '{thread_id}' not found.", details={"thread_id": thread_id}
            )

        history = self._threads[thread_id]
        if checkpoint_id is None:
            record = history[-1]
        else:
            match = next((item for item in history if item["checkpoint_id"] == checkpoint_id), None)
            if match is None:
                raise CheckpointError(
                    f"Checkpoint '{checkpoint_id}' not found in thread '{thread_id}'.",
                    details={"thread_id": thread_id, "checkpoint_id": checkpoint_id},
                )
            record = match

        state_dict = record["state_dict"]
        try:
            schema_version = state_dict.get("schema_version")
            if schema_version != 1:
                raise RuntimeSchemaIncompatibleError(
                    f"Incompatible state schema version: {schema_version}. Expected 1.",
                    details={"schema_version": schema_version},
                )
            return RuntimeState.from_dict(state_dict)
        except RuntimeSchemaIncompatibleError:
            raise
        except Exception as err:
            raise CheckpointCorruptError(
                f"Corrupt in-memory checkpoint payload: {err}",
                details={"thread_id": thread_id, "checkpoint_id": checkpoint_id},
            ) from err

    def list_checkpoints(self, thread_id: str) -> list[dict[str, Any]]:
        history = self._threads.get(thread_id, [])
        return [
            {
                "checkpoint_id": r["checkpoint_id"],
                "thread_id": r["thread_id"],
                "run_id": r["run_id"],
                "step_seq": r["step_seq"],
                "status": r["status"],
                "current_step": r["current_step"],
                "created_at": r["created_at"],
            }
            for r in history
        ]

    def get_latest(self, thread_id: str) -> RuntimeState | None:
        if thread_id not in self._threads or not self._threads[thread_id]:
            return None
        return self.load(thread_id)


class SQLiteCheckpointer(BaseCheckpointer):
    """Local SQLite checkpointer writing strictly to caller-provided checkpoint directory."""

    def __init__(self, checkpoint_dir: Path | str) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.db_path = self.checkpoint_dir / "checkpoints.sqlite"

    def _ensure_initialized(self) -> sqlite3.Connection:
        try:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        thread_id TEXT NOT NULL,
                        checkpoint_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        step_seq INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        current_step TEXT,
                        state_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (thread_id, checkpoint_id)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_seq
                    ON checkpoints (thread_id, step_seq)
                    """
                )
            return conn
        except (sqlite3.DatabaseError, OSError) as err:
            raise CheckpointWriteError(
                f"Failed to initialize SQLite checkpoint storage at '{self.db_path}': {err}",
                details={"db_path": str(self.db_path), "error": str(err)},
            ) from err

    def save(self, state: RuntimeState, checkpoint_id: str | None = None) -> str:
        state_dict = state.to_dict()
        try:
            state_json = json.dumps(state_dict, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as err:
            raise CheckpointWriteError(
                f"Failed to serialize RuntimeState to JSON: {err}",
                details={"run_id": state.run_id, "error": str(err)},
            ) from err

        conn = self._ensure_initialized()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                    (state.thread_id,),
                )
                count = cur.fetchone()[0]
                cid = checkpoint_id or f"cp_{state.step_seq:04d}_{count + 1:04d}"

                created_at = (
                    state.updated_at
                    or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                )
                cur.execute(
                    """
                    INSERT INTO checkpoints (
                        thread_id, checkpoint_id, run_id, step_seq, status, current_step, state_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.thread_id,
                        cid,
                        state.run_id,
                        state.step_seq,
                        state.status,
                        state.current_step,
                        state_json,
                        created_at,
                    ),
                )
                return cid
        except sqlite3.IntegrityError as err:
            raise CheckpointError(
                f"Duplicate checkpoint_id '{checkpoint_id}' for thread '{state.thread_id}'.",
                details={"thread_id": state.thread_id, "checkpoint_id": checkpoint_id},
            ) from err
        except sqlite3.DatabaseError as err:
            raise CheckpointWriteError(
                f"SQLite error during checkpoint write: {err}",
                details={"thread_id": state.thread_id, "error": str(err)},
            ) from err
        finally:
            conn.close()

    def load(self, thread_id: str, checkpoint_id: str | None = None) -> RuntimeState:
        if not self.db_path.exists():
            raise ThreadNotFoundError(
                f"Checkpoint store does not exist at '{self.db_path}'.",
                details={"db_path": str(self.db_path), "thread_id": thread_id},
            )

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
        except sqlite3.DatabaseError as err:
            raise CheckpointCorruptError(
                f"Failed to open corrupt SQLite database: {err}",
                details={"db_path": str(self.db_path), "error": str(err)},
            ) from err

        try:
            with conn:
                cur = conn.cursor()
                if checkpoint_id is None:
                    cur.execute(
                        """
                        SELECT state_json FROM checkpoints
                        WHERE thread_id = ?
                        ORDER BY step_seq DESC, rowid DESC
                        LIMIT 1
                        """,
                        (thread_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT state_json FROM checkpoints
                        WHERE thread_id = ? AND checkpoint_id = ?
                        """,
                        (thread_id, checkpoint_id),
                    )

                row = cur.fetchone()
                if row is None:
                    # Check if thread exists at all
                    cur.execute(
                        "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                        (thread_id,),
                    )
                    exists = cur.fetchone()[0]
                    if exists == 0:
                        raise ThreadNotFoundError(
                            f"Thread '{thread_id}' not found in checkpoint store.",
                            details={"thread_id": thread_id},
                        )
                    else:
                        raise CheckpointError(
                            f"Checkpoint '{checkpoint_id}' not found in thread '{thread_id}'.",
                            details={"thread_id": thread_id, "checkpoint_id": checkpoint_id},
                        )

                state_json = row["state_json"]
        except (sqlite3.DatabaseError, OSError) as err:
            if isinstance(err, (ThreadNotFoundError, CheckpointError)):
                raise
            raise CheckpointCorruptError(
                f"Corrupt or unreadable SQLite database: {err}",
                details={"db_path": str(self.db_path), "error": str(err)},
            ) from err
        finally:
            conn.close()

        try:
            state_dict = json.loads(state_json)
        except json.JSONDecodeError as err:
            raise CheckpointCorruptError(
                f"Corrupt JSON payload in checkpoint record: {err}",
                details={"thread_id": thread_id, "checkpoint_id": checkpoint_id},
            ) from err

        schema_version = state_dict.get("schema_version")
        if schema_version != 1:
            raise RuntimeSchemaIncompatibleError(
                f"Incompatible state schema version: {schema_version}. Expected 1.",
                details={"schema_version": schema_version},
            )

        return RuntimeState.from_dict(state_dict)

    def list_checkpoints(self, thread_id: str) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []

        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT checkpoint_id, thread_id, run_id, step_seq, status, current_step, created_at
                    FROM checkpoints
                    WHERE thread_id = ?
                    ORDER BY step_seq ASC, rowid ASC
                    """,
                    (thread_id,),
                )
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.DatabaseError as err:
            raise CheckpointCorruptError(
                f"Failed to query corrupt SQLite database: {err}",
                details={"db_path": str(self.db_path), "error": str(err)},
            ) from err
        finally:
            if conn is not None:
                conn.close()

    def get_latest(self, thread_id: str) -> RuntimeState | None:
        try:
            return self.load(thread_id)
        except ThreadNotFoundError:
            return None
