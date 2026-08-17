import difflib
import hashlib
from pathlib import Path
from typing import Tuple, Optional

def generate_preview(root: Path, target_relative_path: str, 
                     expected_hash: Optional[str], append_text: str) -> Tuple[str, str, int, str]:
    """Reads target bytes only, does NOT mutate target. 
    Returns diff, current_sha256, current_size, proposed_sha256."""
    
    target = root / target_relative_path
    if not target.exists():
        raise FileNotFoundError(f"Target file {target_relative_path} does not exist.")
        
    current_bytes = target.read_bytes()
    current_size = len(current_bytes)
    current_sha256 = hashlib.sha256(current_bytes).hexdigest()
    
    if expected_hash and expected_hash != current_sha256:
        raise ValueError("Stale expected hash")
        
    proposed_text = current_bytes.decode('utf-8') + append_text
    proposed_bytes = proposed_text.encode('utf-8')
    proposed_sha256 = hashlib.sha256(proposed_bytes).hexdigest()
    
    current_lines = current_bytes.decode('utf-8').splitlines(keepends=True)
    proposed_lines = proposed_text.splitlines(keepends=True)
    
    diff = "".join(difflib.unified_diff(
        current_lines, 
        proposed_lines, 
        fromfile=target_relative_path, 
        tofile=target_relative_path
    ))
    
    return diff, current_sha256, current_size, proposed_sha256
