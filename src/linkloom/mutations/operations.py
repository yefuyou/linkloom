from typing import Optional, List, Dict, Any, Tuple
import hashlib
from pathlib import Path
from .models import ChangeOperation, Precondition, PermissionError
from .permission import validate_target_path, validate_target_root
from .preview import generate_preview

def create_operation(operation_id: str, sequence: int, kind: str, 
                     target_relative_path: str, expected_sha256: str,
                     expected_size_bytes: int, proposed_content_sha256: str,
                     allowed_root: str, payload: Dict[str, Any],
                     preview_diff: str, evidence_refs: List[str], risk: str) -> ChangeOperation:
    status = "pending"
    if kind != "append_block":
        status = "OPERATION_NOT_SUPPORTED"
    
    return ChangeOperation(
        operation_id=operation_id,
        sequence=sequence,
        kind=kind,
        target_relative_path=target_relative_path,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
        proposed_content_sha256=proposed_content_sha256,
        precondition=Precondition(must_exist=True, must_not_be_symlink=True, allowed_root=allowed_root),
        payload=payload,
        preview_diff=preview_diff,
        evidence_refs=evidence_refs,
        risk=risk,
        status=status
    )

from datetime import datetime, timezone, timedelta
from .models import ChangePlan

def build_append_plan(run_id: str, root: Path, fingerprint: str, 
                      kind: str, target_relative_path: str, 
                      append_text: str, evidence_refs: List[str],
                      source_index_hash: str, risk: str) -> ChangePlan:
    
    if kind != "append_block":
        raise ValueError("OPERATION_NOT_SUPPORTED")

    if not evidence_refs:
        raise ValueError("Evidence refs cannot be empty")

    validate_target_root(root, fingerprint=fingerprint)
    validate_target_path(root, target_relative_path)
    
    # generate_preview safely reads the file, generates deterministic unified diff
    # but generates NO mutations on target.
    # We do not write to target, backup, or source.
    diff, expected_sha256, expected_size_bytes, proposed_sha256 = generate_preview(
        root, target_relative_path, expected_hash=None, append_text=append_text
    )
    
    lines_added = sum(1 for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++'))
    lines_removed = sum(1 for line in diff.splitlines() if line.startswith('-') and not line.startswith('---'))

    op = create_operation(
        operation_id=f"op_{run_id}_1",
        sequence=1,
        kind=kind,
        target_relative_path=target_relative_path,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
        proposed_content_sha256=proposed_sha256,
        allowed_root=fingerprint,
        payload={"text": append_text},
        preview_diff=diff,
        evidence_refs=evidence_refs,
        risk=risk
    )
    
    plan_id = f"plan_{run_id}"
    digest = ChangePlan.compute_digest(plan_id, [op])
    now = datetime.now(timezone.utc)
    
    return ChangePlan(
        plan_id=plan_id,
        plan_sha256=digest,
        created_from_run_id=run_id,
        source_index_sha256=source_index_hash,
        target_root_fingerprint=fingerprint,
        operations=[op],
        operation_count=1,
        diff_summary={"files_changed": 1, "lines_added": lines_added, "lines_removed": lines_removed},
        created_at=now,
        expires_at=now + timedelta(minutes=30),
        schema_version=1,
        target_root_mode="synthetic_fixture_only",
        approval_status="pending"
    )
