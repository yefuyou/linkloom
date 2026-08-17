import os
import shutil
import uuid
from pathlib import Path
from typing import List

from .backup import BackupManifest, _hash_file

def execute_rollback(manifest: BackupManifest, target_root: Path, backup_root: Path, applied_operation_ids: List[str] = None) -> bool:
    if not manifest.complete:
        return False
        
    try:
        tr_res = target_root.resolve()
        br_res = backup_root.resolve()
        
        for bf in reversed(manifest.files):
            target_path = target_root / bf.target_relative_path
            backup_path = backup_root / bf.backup_relative_path
            
            if not target_path.resolve().is_relative_to(tr_res):
                return False
            if not backup_path.resolve().is_relative_to(br_res):
                return False
                
            if not backup_path.exists():
                return False
                
            temp_path = target_path.with_name(target_path.name + f".rbk_{uuid.uuid4().hex}")
            mode = target_path.stat().st_mode if target_path.exists() else 0o644
            
            shutil.copy2(backup_path, temp_path)
            os.chmod(temp_path, mode)
            os.replace(temp_path, target_path)
            
            if _hash_file(target_path) != bf.original_sha256:
                return False
                
        return True
    except Exception:
        return False
