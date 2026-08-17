from pathlib import Path
from typing import List, Dict
from datetime import datetime, timezone
import re
from .models import PermissionError, ChangePlan, Approval

def validate_target_root(root: Path, synthetic_only: bool = True, fingerprint: str = None) -> bool:
    """Rejects real repo/vault paths and non-explicit roots."""
    absolute_root = root.resolve()
    str_root = absolute_root.as_posix()
    
    if not fingerprint:
        raise PermissionError("Explicit root fingerprint required")
        
    if synthetic_only:
        if str_root.endswith("/linkloom") or "/tests/fixtures" in str_root or "linkloom/tests/fixtures" in str_root:
            raise PermissionError("Real repository or Vault path rejected")
            
        import tempfile
        sys_tmp = Path(tempfile.gettempdir()).resolve().as_posix()
        is_sys_tmp = str_root.startswith(sys_tmp) or str_root.startswith("/tmp/") or str_root.startswith("/private/var/folders/")
        
        if not is_sys_tmp:
            raise PermissionError("Real repository or Vault path rejected")
            
    return True

def validate_target_path(root: Path, target_relative_path: str):
    """Rejects absolute paths, traversal, and symlinks."""
    if Path(target_relative_path).is_absolute():
        raise PermissionError("ABSOLUTE_PATH_NOT_ALLOWED")
    if ".." in Path(target_relative_path).parts:
        raise PermissionError("PATH_TRAVERSAL_NOT_ALLOWED")
    
    target = root / target_relative_path
    
    # Check if target or any of its parents (up to root) is a symlink
    curr = target
    while curr != root.parent and curr != curr.parent:
        if curr.is_symlink():
            raise PermissionError("SYMLINK_NOT_ALLOWED")
        if curr == root:
            break
        curr = curr.parent

    # Prove the resolved target remains under the resolved synthetic target root
    resolved_root = root.resolve()
    try:
        resolved_target = target.resolve()
    except Exception:
        raise PermissionError("PATH_RESOLUTION_FAILED")
        
    if not resolved_target.is_relative_to(resolved_root):
        raise PermissionError("PATH_OUTSIDE_ROOT")

class ApprovalRegistry:
    def __init__(self):
        self.approvals: Dict[str, Approval] = {}

def process_approval(registry: ApprovalRegistry, plan: ChangePlan, phrase: str, actor: str) -> Approval:
    if actor != "human":
        raise ValueError("Only actor exactly human is accepted")
        
    match = re.match(r"^(APPROVE|REJECT) ([A-Za-z0-9_-]+) root=([A-Za-z0-9_-]+) ops=(\d+) sha=([a-fA-F0-9]+)$", phrase.strip())
    if not match:
        raise ValueError("vague yes/continue/approve all or incorrect format")
        
    decision, plan_id, root_fp, ops_count_str, sha = match.groups()
    decision = decision.lower()
    
    if plan_id != plan.plan_id:
        raise ValueError("plan id mismatch")
    if root_fp != plan.target_root_fingerprint:
        raise ValueError("root mismatch")
        
    ops_count = int(ops_count_str)
    if ops_count != plan.operation_count:
        raise ValueError("operation count mismatch")
        
    if sha != plan.plan_sha256:
        raise ValueError("digest mismatch")
        
    approval_id = f"app_{plan_id}"
    
    if approval_id in registry.approvals:
        raise ValueError("DUPLICATE_APPROVAL")
        
    if datetime.now(timezone.utc) > plan.expires_at:
        raise ValueError("EXPIRED_APPROVAL")
        
    approval = Approval(
        approval_id=approval_id,
        plan_id=plan_id,
        plan_sha256=sha,
        target_root_fingerprint=root_fp,
        approved_operation_ids=[op.operation_id for op in plan.operations] if decision == "approve" else [],
        decision=decision,
        actor=actor,
        confirmation_phrase=phrase,
        created_at=datetime.now(timezone.utc),
        expires_at=plan.expires_at
    )
    
    registry.approvals[approval_id] = approval
    return approval
