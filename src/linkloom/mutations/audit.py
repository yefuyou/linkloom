from dataclasses import dataclass
from typing import Dict, Any, Optional
from datetime import datetime

@dataclass(frozen=True)
class AuditRecord:
    event_id: str
    event_type: str  # plan_created, previewed, approval_recorded, approval_rejected
    timestamp: datetime
    run_id: str
    plan_id: Optional[str] = None
    plan_sha256: Optional[str] = None
    actor: Optional[str] = None
    target_root_fingerprint: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    # Must NOT contain note full text, absolute path, or secrets.
