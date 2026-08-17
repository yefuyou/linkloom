"""Unit tests for InMemoryCheckpointer and SQLiteCheckpointer."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from linkloom.runtime.checkpoint import (
    InMemoryCheckpointer,
    SQLiteCheckpointer,
)
from linkloom.runtime.errors import (
    CheckpointCorruptError,
    CheckpointError,
    CheckpointWriteError,
    RuntimeSchemaIncompatibleError,
    ThreadNotFoundError,
)
from linkloom.runtime.models import (
    ErrorEnvelope,
    PolicySnapshot,
    RuntimeState,
    SourceContext,
    UsageEnvelope,
)


def _sample_state(
    run_id: str = "run_01",
    thread_id: str = "thread_01",
    step_seq: int = 1,
    status: str = "running",
) -> RuntimeState:
    err = (
        ErrorEnvelope(code="ERR_TEST", category="runtime", message="test failure")
        if status in ("failed", "stale", "rejected")
        else None
    )
    res = "result_01" if status == "completed" else None
    return RuntimeState(
        schema_version=1,
        run_id=run_id,
        thread_id=thread_id,
        parent_run_id=None,
        workflow="ask",
        status=status,
        current_step="retrieve_context",
        step_seq=step_seq,
        request_ref=f"req_{run_id}",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="c" * 64,
            index_schema_version=1,
            vault_root_fingerprint="root_12345",
            fixture_id="sample_vault_v1",
            document_count=3,
        ),
        evidence_refs=["ev_1"],
        result_ref=res,
        error=err,
        usage=UsageEnvelope(step_count=step_seq),
        policy=PolicySnapshot(),
        created_at="2026-08-16T10:00:00Z",
        updated_at="2026-08-16T10:00:01Z",
    )


def test_in_memory_checkpointer_save_and_load() -> None:
    cp = InMemoryCheckpointer()
    state = _sample_state(run_id="run_01", thread_id="thread_01", step_seq=1)

    checkpoint_id = cp.save(state, checkpoint_id="cp_0001")
    assert checkpoint_id == "cp_0001"

    loaded = cp.load(thread_id="thread_01", checkpoint_id="cp_0001")
    assert loaded == state

    latest = cp.get_latest("thread_01")
    assert latest == state

    # Save step 2
    state2 = _sample_state(run_id="run_01", thread_id="thread_01", step_seq=2)
    cp.save(state2, checkpoint_id="cp_0002")

    latest2 = cp.get_latest("thread_01")
    assert latest2 == state2
    assert latest2.step_seq == 2

    records = cp.list_checkpoints("thread_01")
    assert len(records) == 2
    assert [r["checkpoint_id"] for r in records] == ["cp_0001", "cp_0002"]


def test_in_memory_checkpointer_rejects_duplicate_checkpoint_id() -> None:
    cp = InMemoryCheckpointer()
    state = _sample_state()
    cp.save(state, checkpoint_id="cp_0001")

    with pytest.raises(CheckpointError):
        cp.save(state, checkpoint_id="cp_0001")


def test_in_memory_checkpointer_missing_thread_or_checkpoint() -> None:
    cp = InMemoryCheckpointer()
    with pytest.raises(ThreadNotFoundError):
        cp.load("non_existent_thread")

    assert cp.get_latest("non_existent_thread") is None


def test_in_memory_checkpoint_isolated_from_nested_mutation() -> None:
    cp = InMemoryCheckpointer()
    state = _sample_state()
    cp.save(state, checkpoint_id="cp_0001")

    state.evidence_refs.append("mutated_after_save")
    loaded = cp.load("thread_01", "cp_0001")
    assert loaded.evidence_refs == ["ev_1"]


def test_in_memory_get_latest_does_not_hide_corruption() -> None:
    cp = InMemoryCheckpointer()
    cp.save(_sample_state(), checkpoint_id="cp_0001")
    cp._threads["thread_01"][0]["state_dict"]["source"] = "corrupt"  # type: ignore[index]

    with pytest.raises(CheckpointCorruptError):
        cp.get_latest("thread_01")


def test_sqlite_checkpointer_save_and_load(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "runtime_checkpoints"
    cp = SQLiteCheckpointer(checkpoint_dir=checkpoint_dir)

    state = _sample_state(run_id="run_01", thread_id="thread_01", step_seq=1)
    cp_id = cp.save(state, checkpoint_id="cp_0001")
    assert cp_id == "cp_0001"

    # SQLite file exists inside caller provided checkpoint directory
    db_file = checkpoint_dir / "checkpoints.sqlite"
    assert db_file.exists()

    loaded = cp.load(thread_id="thread_01", checkpoint_id="cp_0001")
    assert loaded == state

    latest = cp.get_latest("thread_01")
    assert latest == state


def test_sqlite_checkpointer_uniqueness_and_ordering(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "runtime_checkpoints"
    cp = SQLiteCheckpointer(checkpoint_dir=checkpoint_dir)

    state1 = _sample_state(run_id="run_01", thread_id="thread_01", step_seq=1)
    state2 = _sample_state(run_id="run_01", thread_id="thread_01", step_seq=2)

    cp.save(state1, checkpoint_id="cp_0001")
    cp.save(state2, checkpoint_id="cp_0002")

    # Duplicate (thread_id, checkpoint_id) must fail
    with pytest.raises(CheckpointError):
        cp.save(state1, checkpoint_id="cp_0001")

    records = cp.list_checkpoints("thread_01")
    assert len(records) == 2
    assert [r["checkpoint_id"] for r in records] == ["cp_0001", "cp_0002"]
    assert [r["step_seq"] for r in records] == [1, 2]


def test_sqlite_checkpointer_corrupt_database(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "runtime_checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    db_file = checkpoint_dir / "checkpoints.sqlite"
    # Write garbage bytes into sqlite file
    db_file.write_bytes(b"CORRUPT_SQLITE_GARBAGE_BYTES")

    cp = SQLiteCheckpointer(checkpoint_dir=checkpoint_dir)
    with pytest.raises(CheckpointCorruptError):
        cp.load("thread_01", "cp_0001")

    with pytest.raises(CheckpointCorruptError):
        cp.get_latest("thread_01")


def test_sqlite_checkpointer_schema_version_mismatch(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "runtime_checkpoints"
    cp = SQLiteCheckpointer(checkpoint_dir=checkpoint_dir)
    state = _sample_state(run_id="run_01", thread_id="thread_01", step_seq=1)
    cp.save(state, checkpoint_id="cp_0001")

    # Manually tamper with state_json to have incompatible schema_version 99
    db_file = checkpoint_dir / "checkpoints.sqlite"
    conn = sqlite3.connect(db_file)
    with conn:
        conn.execute(
            "UPDATE checkpoints SET state_json = replace(state_json, '\"schema_version\": 1', '\"schema_version\": 99') WHERE checkpoint_id = 'cp_0001'"
        )
    conn.close()

    with pytest.raises(RuntimeSchemaIncompatibleError):
        cp.load("thread_01", "cp_0001")


def test_sqlite_checkpointer_write_failure_on_readonly_dir(tmp_path: Path) -> None:
    # Point checkpoint directory to a file rather than directory to trigger OS/IO failure
    bad_dir = tmp_path / "file_blocking_dir"
    bad_dir.write_text("not a directory", encoding="utf-8")

    cp = SQLiteCheckpointer(checkpoint_dir=bad_dir)
    state = _sample_state()
    with pytest.raises(CheckpointWriteError):
        cp.save(state)
