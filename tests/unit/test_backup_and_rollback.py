import pytest
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

from linkloom.mutations.models import ChangePlan, ChangeOperation, Precondition
from linkloom.mutations.backup import create_backup, _hash_file
from linkloom.mutations.rollback import execute_rollback

def _make_op(rel_path, expected_hash):
    return ChangeOperation(
        operation_id="op1", sequence=1, kind="append_block",
        target_relative_path=rel_path,
        expected_sha256=expected_hash, expected_size_bytes=5,
        proposed_content_sha256="fake", precondition=Precondition(True, True, "synthetic"),
        payload={"content": " world"}, preview_diff="", evidence_refs=[], risk="", status=""
    )

def _make_plan(op):
    return ChangePlan(
        plan_id="p1", plan_sha256="digest", created_from_run_id="run",
        source_index_sha256="idx", target_root_fingerprint="fp",
        operations=[op], operation_count=1, diff_summary={},
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )

def test_backup_creates_manifest_and_files(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    h1 = _hash_file(f1)
    
    plan = _make_plan(_make_op("f1.md", h1))
    manifest = create_backup(plan, target_root, backup_root)
    
    assert manifest.complete
    assert len(manifest.files) == 1
    
    bak_path = backup_root / manifest.files[0].backup_relative_path
    assert bak_path.exists()
    assert _hash_file(bak_path) == h1

def test_backup_fails_if_hash_mismatch(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    
    plan = _make_plan(_make_op("f1.md", "wronghash"))
    manifest = create_backup(plan, target_root, backup_root)
    assert not manifest.complete

def test_rollback_restores_file(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    h1 = _hash_file(f1)
    
    plan = _make_plan(_make_op("f1.md", h1))
    manifest = create_backup(plan, target_root, backup_root)
    
    # Modify target file
    f1.write_bytes(b"hello world")
    
    assert execute_rollback(manifest, target_root, backup_root, ["op1"])
    assert f1.read_bytes() == b"hello"
