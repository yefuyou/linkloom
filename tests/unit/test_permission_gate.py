import pytest
from pathlib import Path
from linkloom.mutations.permission import validate_target_root, process_approval, ApprovalRegistry
from linkloom.mutations.models import ChangePlan, ChangeOperation, Precondition
from datetime import datetime, timedelta, timezone

def test_validate_target_root_rejects_real():
    real_path = Path(__file__).parent.parent.parent.parent
    with pytest.raises(ValueError, match="Real repository or Vault path rejected"):
        validate_target_root(real_path, fingerprint="f1")

def test_validate_target_root_accepts_synthetic(tmp_path):
    assert validate_target_root(tmp_path, fingerprint="f1")

def test_validate_target_root_rejects_tmp_string():
    # just containing tmp is not enough
    import tempfile
    sys_tmp = Path(tempfile.gettempdir()).resolve().as_posix()
    bad_path = Path(sys_tmp).parent / "something_tmp_else"
    with pytest.raises(ValueError, match="Real repository or Vault path rejected"):
        validate_target_root(bad_path, fingerprint="f1")

def test_process_approval_agent_actor_rejected():
    registry = ApprovalRegistry()
    plan = ChangePlan("p1", "sha1", "r1", "idx", "f1", [], 0, {}, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match="Only actor exactly human is accepted"):
        process_approval(registry, plan, "APPROVE p1 root=f1 ops=0 sha=sha1", "Agent")

def test_process_approval_exact_match():
    registry = ApprovalRegistry()
    plan = ChangePlan("p1", "abcdef1234567890", "r1", "idx", "f1", [], 0, {}, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    approval = process_approval(registry, plan, "APPROVE p1 root=f1 ops=0 sha=abcdef1234567890", "human")
    assert approval.decision == "approve"

def test_process_approval_vague_rejected():
    registry = ApprovalRegistry()
    plan = ChangePlan("p1", "abcdef1234567890", "r1", "idx", "f1", [], 0, {}, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match="vague yes/continue/approve all"):
        process_approval(registry, plan, "yes please", "human")

def test_process_approval_duplicate_rejected():
    registry = ApprovalRegistry()
    plan = ChangePlan("p1", "abcdef1234567890", "r1", "idx", "f1", [], 0, {}, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    process_approval(registry, plan, "APPROVE p1 root=f1 ops=0 sha=abcdef1234567890", "human")
    with pytest.raises(ValueError, match="DUPLICATE_APPROVAL"):
        process_approval(registry, plan, "APPROVE p1 root=f1 ops=0 sha=abcdef1234567890", "human")
    with pytest.raises(ValueError, match="DUPLICATE_APPROVAL"):
        process_approval(registry, plan, "REJECT p1 root=f1 ops=0 sha=abcdef1234567890", "human")

def test_process_approval_mismatch_rejected():
    registry = ApprovalRegistry()
    plan = ChangePlan("p1", "abcdef1234567890", "r1", "idx", "f1", [], 0, {}, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match="digest mismatch"):
        process_approval(registry, plan, "APPROVE p1 root=f1 ops=0 sha=1111111111111111", "human")

def test_process_approval_partial_ops_rejected():
    registry = ApprovalRegistry()
    op1 = ChangeOperation("op1", 1, "append_block", "a", "s1", 1, "s2", Precondition(True, True, "f1"), {}, "d", ["#1"], "low", "pending")
    op2 = ChangeOperation("op2", 2, "append_block", "b", "s1", 1, "s2", Precondition(True, True, "f1"), {}, "d", ["#1"], "low", "pending")
    plan = ChangePlan("p1", "abcdef1234567890", "r1", "idx", "f1", [op1, op2], 2, {}, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match="vague yes/continue/approve all or incorrect format"):
        process_approval(registry, plan, "APPROVE p1 root=f1 ops=op1 sha=abcdef1234567890", "human")

def test_process_approval_expired():
    registry = ApprovalRegistry()
    plan = ChangePlan("p1", "abcdef1234567890", "r1", "idx", "f1", [], 0, {}, datetime.now(timezone.utc) - timedelta(hours=2), datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(ValueError, match="EXPIRED_APPROVAL"):
        process_approval(registry, plan, "APPROVE p1 root=f1 ops=0 sha=abcdef1234567890", "human")
