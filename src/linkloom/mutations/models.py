from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import hashlib
import json
from datetime import datetime

@dataclass(frozen=True)
class Precondition:
    must_exist: bool
    must_not_be_symlink: bool
    allowed_root: str

@dataclass(frozen=True)
class ChangeOperation:
    operation_id: str
    sequence: int
    kind: str
    target_relative_path: str
    expected_sha256: str
    expected_size_bytes: int
    proposed_content_sha256: str
    precondition: Precondition
    payload: Dict[str, Any]
    preview_diff: str
    evidence_refs: List[str]
    risk: str
    status: str
    source_relative_path: Optional[str] = None

@dataclass(frozen=True)
class ChangePlan:
    plan_id: str
    plan_sha256: str
    created_from_run_id: str
    source_index_sha256: str
    target_root_fingerprint: str
    operations: List[ChangeOperation]
    operation_count: int
    diff_summary: Dict[str, Any]
    created_at: datetime
    expires_at: datetime
    schema_version: int = 1
    target_root_mode: str = "synthetic_fixture_only"
    approval_status: str = "pending"

    def __post_init__(self):
        op_ids = set()
        targets = set()
        for op in self.operations:
            if op.operation_id in op_ids:
                raise ValueError("Duplicate operation id")
            op_ids.add(op.operation_id)
            if op.target_relative_path in targets:
                raise ValueError("Duplicate target")
            targets.add(op.target_relative_path)

    @classmethod
    def compute_digest(cls, plan_id: str, operations: List[ChangeOperation]) -> str:
        ops_data = []
        for op in operations:
            ops_data.append({
                "operation_id": op.operation_id,
                "sequence": op.sequence,
                "kind": op.kind,
                "target_relative_path": op.target_relative_path,
                "expected_sha256": op.expected_sha256,
                "proposed_content_sha256": op.proposed_content_sha256,
                "payload": op.payload
            })
        data = {
            "plan_id": plan_id,
            "operations": ops_data
        }
        canonical_json = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()

@dataclass(frozen=True)
class Approval:
    approval_id: str
    plan_id: str
    plan_sha256: str
    target_root_fingerprint: str
    approved_operation_ids: List[str]
    decision: str
    actor: str
    confirmation_phrase: str
    created_at: datetime
    expires_at: datetime

class PermissionError(ValueError):
    pass
