import json
import os
import shutil
import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable

from .models import ChangePlan, Approval, ChangeOperation
from .permission import validate_target_root, validate_target_path, PermissionError
from .backup import BackupManifest, create_backup, _hash_file
from .rollback import execute_rollback

@dataclass(frozen=True)
class ApplyResult:
    apply_id: str
    plan_id: str
    approval_id: str
    status: str
    applied_operation_ids: List[str]
    failed_operation_id: Optional[str]
    backup_id: Optional[str]
    post_apply_hashes: Dict[str, str]
    rollback_available: bool
    audit_log: List[Dict[str, Any]]
    error: Optional[str]
    started_at: datetime
    finished_at: datetime

def default_writer(target_path: Path, content: bytes):
    temp_path = target_path.with_name(target_path.name + f".tmp_{uuid.uuid4().hex}")
    try:
        mode = target_path.stat().st_mode if target_path.exists() else 0o644
        with open(temp_path, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, target_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise e

def default_verifier(target_path: Path, expected_sha256: str):
    if _hash_file(target_path) != expected_sha256:
        raise ValueError("POST_WRITE_VERIFICATION_FAILED")

def apply_plan(
    plan: ChangePlan, 
    approval: Approval, 
    target_root: Path, 
    backup_root: Path, 
    synthetic_only: bool = True,
    writer: Callable = default_writer,
    verifier: Callable = default_verifier
) -> ApplyResult:
    started_at = datetime.now(timezone.utc)
    audit_log = []
    
    def _append_audit(event_type: str, details: Dict[str, Any] = None):
        audit_log.append({
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {}
        })

    def _fail(status: str, error: str, backup_id: str = None, applied: List[str] = None, post_hashes: Dict[str, str] = None, failed_op: str = None, rollback_available: bool = False):
        return ApplyResult(
            apply_id=f"app_{uuid.uuid4().hex}", plan_id=plan.plan_id, approval_id=approval.approval_id,
            status=status, applied_operation_ids=applied or [], failed_operation_id=failed_op,
            backup_id=backup_id, post_apply_hashes=post_hashes or {}, rollback_available=rollback_available,
            audit_log=audit_log, error=error,
            started_at=started_at, finished_at=datetime.now(timezone.utc)
        )

    if approval.decision != "approve":
        _append_audit("approval_rejected")
        return _fail("rejected", "Approval was not 'approve'")
        
    if approval.plan_id != plan.plan_id or approval.plan_sha256 != plan.plan_sha256:
        return _fail("failed", "Plan digest/id mismatch")

    if approval.target_root_fingerprint != plan.target_root_fingerprint:
        return _fail("failed", "Root fingerprint mismatch")
        
    if set(approval.approved_operation_ids) != set(op.operation_id for op in plan.operations):
        return _fail("failed", "Not all operations approved")

    if datetime.now(timezone.utc) > approval.expires_at or datetime.now(timezone.utc) > plan.expires_at:
        return _fail("failed", "Approval or plan expired")
        
    if approval.actor != "human":
        return _fail("failed", "Non-human approval")

    try:
        validate_target_root(target_root, synthetic_only=synthetic_only, fingerprint=plan.target_root_fingerprint)
        for op in plan.operations:
            validate_target_path(target_root, op.target_relative_path)
            if op.kind != "append_block":
                raise PermissionError("UNSUPPORTED_OPERATION")
    except PermissionError as e:
        return _fail("failed", str(e))

    for op in plan.operations:
        target_path = target_root / op.target_relative_path
        if not target_path.exists():
            return _fail("stale", "STALE_HASH")
        if _hash_file(target_path) != op.expected_sha256:
            return _fail("stale", "STALE_HASH")

    backup_manifest = create_backup(plan, target_root, backup_root)
    _append_audit("backup_created", {"backup_id": backup_manifest.backup_id, "complete": backup_manifest.complete})
    
    if not backup_manifest.complete:
        return _fail("failed", "BACKUP_INCOMPLETE", backup_id=backup_manifest.backup_id)

    applied_ops = []
    post_hashes = {}
    
    for op in plan.operations:
        target_path = target_root / op.target_relative_path
        
        if _hash_file(target_path) != op.expected_sha256:
            _append_audit("verification_failed", {"operation_id": op.operation_id, "reason": "STALE_HASH"})
            rollback_res = execute_rollback(backup_manifest, target_root, backup_root, applied_ops)
            _append_audit("rollback_completed" if rollback_res else "rollback_failed")
            return _fail(
                "rolled_back" if rollback_res else "rollback_failed",
                "STALE_HASH",
                backup_id=backup_manifest.backup_id,
                applied=applied_ops,
                post_hashes=post_hashes,
                failed_op=op.operation_id,
                rollback_available=not rollback_res
            )
            
        _append_audit("operation_started", {"operation_id": op.operation_id})
        
        try:
            with open(target_path, "rb") as f:
                original_content = f.read()
            payload_text = op.payload.get("content", op.payload.get("text", ""))
            new_content = original_content + payload_text.encode('utf-8')
            
            try:
                writer(target_path, new_content)
            except Exception:
                raise RuntimeError("WRITER_FAILURE")
                
            verifier(target_path, op.proposed_content_sha256)
            
            applied_ops.append(op.operation_id)
            post_hashes[op.target_relative_path] = op.proposed_content_sha256
            _append_audit("operation_applied", {"operation_id": op.operation_id})
            
        except Exception as e:
            error_cat = str(e) if str(e) in ["WRITER_FAILURE", "POST_WRITE_VERIFICATION_FAILED"] else "WRITER_FAILURE"
            _append_audit("operation_failed", {"operation_id": op.operation_id, "error": error_cat})
            _append_audit("rollback_started")
            rollback_res = execute_rollback(backup_manifest, target_root, backup_root, applied_ops)
            if rollback_res:
                _append_audit("rollback_completed")
                status = "rolled_back"
            else:
                _append_audit("rollback_failed")
                status = "rollback_failed"
                
            return _fail(
                status,
                error_cat,
                backup_id=backup_manifest.backup_id,
                applied=applied_ops,
                post_hashes=post_hashes,
                failed_op=op.operation_id,
                rollback_available=not rollback_res
            )
            
    return ApplyResult(
        apply_id=f"app_{uuid.uuid4().hex}", plan_id=plan.plan_id, approval_id=approval.approval_id,
        status="applied", applied_operation_ids=applied_ops, failed_operation_id=None,
        backup_id=backup_manifest.backup_id, post_apply_hashes=post_hashes, rollback_available=True,
        audit_log=audit_log, error=None,
        started_at=started_at, finished_at=datetime.now(timezone.utc)
    )
