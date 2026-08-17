import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import ChangePlan

@dataclass(frozen=True)
class BackupFile:
    target_relative_path: str
    backup_relative_path: str
    original_sha256: str
    original_size_bytes: int
    backup_sha256: str
    verified: bool

    def to_dict(self):
        return {
            "target_relative_path": self.target_relative_path,
            "backup_relative_path": self.backup_relative_path,
            "original_sha256": self.original_sha256,
            "original_size_bytes": self.original_size_bytes,
            "backup_sha256": self.backup_sha256,
            "verified": self.verified
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass(frozen=True)
class BackupManifest:
    backup_id: str
    plan_id: str
    backup_root: str
    files: List[BackupFile]
    complete: bool
    created_at: datetime

    def to_dict(self):
        return {
            "backup_id": self.backup_id,
            "plan_id": self.plan_id,
            "files": [f.to_dict() for f in self.files],
            "complete": self.complete,
            "created_at": self.created_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data, backup_root: str = ""):
        files = [BackupFile.from_dict(f) for f in data.get("files", [])]
        created_at_str = data.get("created_at")
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            backup_id=data.get("backup_id", ""),
            plan_id=data.get("plan_id", ""),
            backup_root=backup_root,
            files=files,
            complete=data.get("complete", False),
            created_at=created_at
        )

def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def create_backup(plan: ChangePlan, target_root: Path, backup_root: Path) -> BackupManifest:
    backup_id = f"bak_{uuid.uuid4().hex}"
    
    # Check root containment
    try:
        tr_res = target_root.resolve()
        br_res = backup_root.resolve()
        if br_res == tr_res or tr_res in br_res.parents:
            return BackupManifest(backup_id, plan.plan_id, str(backup_root), [], False, datetime.now(timezone.utc))
    except Exception:
        pass

    backup_files = []
    complete = True
    
    targets = {op.target_relative_path: op for op in plan.operations}
    
    for rel_path, op in targets.items():
        target_path = target_root / rel_path
        
        try:
            if not target_path.resolve().is_relative_to(tr_res):
                complete = False
                break
        except Exception:
            complete = False
            break
            
        if not target_path.exists() or target_path.is_symlink() or not target_path.is_file():
            complete = False
            break
            
        try:
            original_sha = _hash_file(target_path)
            original_size = target_path.stat().st_size
            
            if original_sha != op.expected_sha256:
                complete = False
                break
                
            backup_rel = f"{backup_id}_{uuid.uuid4().hex}.bak"
            backup_path = backup_root / backup_rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            
            shutil.copy2(target_path, backup_path)
            
            backup_sha = _hash_file(backup_path)
            
            verified = (backup_sha == original_sha)
            if not verified:
                complete = False
                
            backup_files.append(BackupFile(
                target_relative_path=rel_path,
                backup_relative_path=backup_rel,
                original_sha256=original_sha,
                original_size_bytes=original_size,
                backup_sha256=backup_sha,
                verified=verified
            ))
            
            if not complete:
                break
                
        except Exception:
            complete = False
            break
            
    manifest = BackupManifest(
        backup_id=backup_id,
        plan_id=plan.plan_id,
        backup_root=str(backup_root),
        files=backup_files,
        complete=complete,
        created_at=datetime.now(timezone.utc)
    )
    
    if backup_files or complete:
        manifest_path = backup_root / backup_id / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        
    return manifest
