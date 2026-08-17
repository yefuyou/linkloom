import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from linkloom.mutations.models import ChangePlan, ChangeOperation, Precondition, Approval
from linkloom.mutations.apply import apply_plan, _hash_file

def _make_plan(expected_hashes, proposed_hashes):
    ops = []
    for i, (eh, ph) in enumerate(zip(expected_hashes, proposed_hashes)):
        ops.append(ChangeOperation(
            operation_id=f"op{i}", sequence=i, kind="append_block",
            target_relative_path=f"f{i}.md",
            expected_sha256=eh, expected_size_bytes=5,
            proposed_content_sha256=ph, precondition=Precondition(True, True, "synthetic"),
            payload={"content": " world"}, preview_diff="", evidence_refs=[], risk="", status=""
        ))
    return ChangePlan(
        plan_id="p1", plan_sha256="digest", created_from_run_id="run",
        source_index_sha256="idx", target_root_fingerprint="fp",
        operations=ops, operation_count=len(ops), diff_summary={},
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )

def test_partial_failure_recovery_on_writer_fail(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f0 = target_root / "f0.md"
    f0.write_bytes(b"hello")
    f1 = target_root / "f1.md"
    f1.write_bytes(b"hello")
    
    ehs = [_hash_file(f0), _hash_file(f1)]
    
    f_temp = tmp_path / "temp.md"
    f_temp.write_bytes(b"hello world")
    phs = [_hash_file(f_temp), _hash_file(f_temp)]
    
    plan = _make_plan(ehs, phs)
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op0", "op1"],
        decision="approve", actor="human", confirmation_phrase="APPROVE",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    def failing_writer(path, content):
        from linkloom.mutations.apply import default_writer
        if "f1.md" in str(path):
            raise RuntimeError("writer injection failure")
        default_writer(path, content)
        
    res = apply_plan(plan, approval, target_root, backup_root, writer=failing_writer)
    assert res.status == "rolled_back"
    assert "operation_failed" in [a["event_type"] for a in res.audit_log]
    assert "rollback_completed" in [a["event_type"] for a in res.audit_log]
    
    assert f0.read_bytes() == b"hello"
    assert f1.read_bytes() == b"hello"

def test_post_write_verifier_failure(tmp_path):
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    target_root.mkdir()
    
    f0 = target_root / "f0.md"
    f0.write_bytes(b"hello")
    
    ehs = [_hash_file(f0)]
    phs = ["wrongproposedhash"]
    
    plan = _make_plan(ehs, phs)
    approval = Approval(
        approval_id="app1", plan_id="p1", plan_sha256="digest",
        target_root_fingerprint="fp", approved_operation_ids=["op0"],
        decision="approve", actor="human", confirmation_phrase="APPROVE",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    res = apply_plan(plan, approval, target_root, backup_root)
    assert res.status == "rolled_back"
    assert f0.read_bytes() == b"hello"
