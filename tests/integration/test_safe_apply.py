import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from linkloom.mutations.models import ChangePlan, ChangeOperation, Precondition, Approval
from linkloom.mutations.apply import apply_plan, _hash_file

def _make_plan(expected_hash, proposed_hash):
    op = ChangeOperation(
        operation_id="op1", sequence=1, kind="append_block",
        target_relative_path="f1.md",
        expected_sha256=expected_hash, expected_size_bytes=5,
        proposed_content_sha256=proposed_hash, precondition=Precondition(True, True, "synthetic"),
        payload={"content": " world"}, preview_diff="", evidence_refs=[], risk="", status=""
    )
    return ChangePlan(
        plan_id="p1", plan_sha256="digest", created_from_run_id="run",
        source_index_sha256="idx", target_root_fingerprint="fp",
        operations=[op], operation_count=1, diff_summary={},
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )

def test_safe_apply_success(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    expected_hash = _hash_file(f1)
    
    f_temp = tmp_path / "temp.md"
    f_temp.write_bytes(b"hello world")
    proposed_hash = _hash_file(f_temp)
    
    plan = _make_plan(expected_hash, proposed_hash)
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE p1 root=fp ops=1 sha=digest",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    res = apply_plan(plan, approval, target_root, backup_root)
    assert res.status == "applied"
    assert f1.read_bytes() == b"hello world"
    event_types = [a["event_type"] for a in res.audit_log]
    assert "operation_applied" in event_types

def test_reject_approval(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    
    plan = _make_plan("h1", "h2")
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=[],
        decision="reject", actor="human", confirmation_phrase="REJECT ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    res = apply_plan(plan, approval, target_root, backup_root)
    assert res.status == "rejected"
    assert f1.read_bytes() == b"hello"

def test_stale_hash_rejection(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    
    plan = _make_plan("oldhash", "newhash")
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    res = apply_plan(plan, approval, target_root, backup_root)
    assert res.status == "stale"
    assert f1.read_bytes() == b"hello"

def test_real_vault_rejection(tmp_path):
    target_root = tmp_path / "linkloom"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    plan = _make_plan("h1", "h2")
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    res = apply_plan(plan, approval, target_root, backup_root, synthetic_only=True)
    assert res.status == "failed"
    assert "Real repository or Vault path rejected" in res.error

def test_parent_symlink_rejection(tmp_path, monkeypatch):
    target_root = tmp_path / "target"
    target_root.mkdir()
    
    # Mock is_symlink to return True for our fake symlink path
    sym_nested = target_root / "sym_nested"
    
    original_is_symlink = Path.is_symlink
    def mock_is_symlink(self):
        if self.name == "sym_nested":
            return True
        return original_is_symlink(self)
        
    monkeypatch.setattr(Path, "is_symlink", mock_is_symlink)
    
    plan = _make_plan("h1", "h2")
    import dataclasses
    op = dataclasses.replace(plan.operations[0], target_relative_path="sym_nested/f1.md")
    plan = dataclasses.replace(plan, operations=[op])
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    res = apply_plan(plan, approval, target_root, tmp_path / "backup")
    assert res.status == "failed"
    assert res.error == "SYMLINK_NOT_ALLOWED"

def test_resolved_outside_root_rejection(tmp_path):
    target_root = tmp_path / "target"
    target_root.mkdir()
    
    plan = _make_plan("h1", "h2")
    import dataclasses
    # Trying path traversal
    op = dataclasses.replace(plan.operations[0], target_relative_path="../escaped.md")
    plan = dataclasses.replace(plan, operations=[op])
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    res = apply_plan(plan, approval, target_root, tmp_path / "backup")
    assert res.status == "failed"
    assert res.error == "PATH_TRAVERSAL_NOT_ALLOWED"

def test_writer_exception_redaction(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    expected_hash = _hash_file(f1)
    
    plan = _make_plan(expected_hash, "newhash")
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    def leaky_writer(target_path, content):
        raise PermissionError(f"Cannot write to absolute path {target_path} containing secret")
        
    res = apply_plan(plan, approval, target_root, backup_root, writer=leaky_writer)
    assert res.status == "rollback_failed" or res.status == "rolled_back"
    assert res.error == "WRITER_FAILURE"
    assert "secret" not in str(res.audit_log)
    assert str(target_root) not in str(res.audit_log)

def test_writer_failure_rollback(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    expected_hash = _hash_file(f1)
    
    plan = _make_plan(expected_hash, "newhash")
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    def failing_writer(target_path, content):
        raise Exception("fail")
        
    res = apply_plan(plan, approval, target_root, backup_root, writer=failing_writer)
    assert res.status == "rolled_back"
    assert res.error == "WRITER_FAILURE"
    assert f1.read_bytes() == b"hello"
    
def test_verifier_failure_rollback(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    expected_hash = _hash_file(f1)
    
    plan = _make_plan(expected_hash, "newhash")
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE ...",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    def mock_writer(target_path, content):
        target_path.write_bytes(content)
        
    def failing_verifier(target_path, expected_hash):
        raise ValueError("POST_WRITE_VERIFICATION_FAILED")
        
    res = apply_plan(plan, approval, target_root, backup_root, writer=mock_writer, verifier=failing_verifier)
    assert res.status == "rolled_back"
    assert res.error == "POST_WRITE_VERIFICATION_FAILED"
    assert f1.read_bytes() == b"hello"
