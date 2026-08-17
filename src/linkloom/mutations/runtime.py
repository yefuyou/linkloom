import sqlite3
import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path

from linkloom.mutations.models import ChangePlan, Approval
from linkloom.mutations.apply import apply_plan
from linkloom.observability.events import EventEmitter
from linkloom.mutations.permission import validate_target_root, validate_target_path, PermissionError
from linkloom.mutations.backup import _hash_file

class InvalidStateTransitionError(ValueError):
    pass

@dataclass
class WritebackState:
    run_id: str
    thread_id: str
    status: str
    plan_id: str
    plan_sha256: str
    target_root_fingerprint: str
    operation_ids: List[str]
    approval_id: Optional[str] = None
    backup_id: Optional[str] = None
    applied_operation_ids: List[str] = field(default_factory=list)
    checkpoint_id: Optional[str] = None
    interrupt_id: Optional[str] = None
    source_run_id: Optional[str] = None
    memory_refs: List[str] = field(default_factory=list)
    eval_run_id: Optional[str] = None
    error_code: Optional[str] = None
    last_event_seq: int = 0
    created_at: str = ""
    updated_at: str = ""

class WritebackStore:
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS writeback_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_sha256 TEXT NOT NULL,
                target_root_fingerprint TEXT NOT NULL,
                operation_ids TEXT NOT NULL,
                approval_id TEXT,
                backup_id TEXT,
                applied_operation_ids TEXT,
                checkpoint_id TEXT,
                interrupt_id TEXT,
                source_run_id TEXT,
                memory_refs TEXT,
                eval_run_id TEXT,
                error_code TEXT,
                last_event_seq INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        self.conn.commit()

    def save(self, state: WritebackState):
        self.conn.execute('''
            INSERT INTO writeback_runs (
                run_id, thread_id, status, plan_id, plan_sha256, target_root_fingerprint,
                operation_ids, approval_id, backup_id, applied_operation_ids, checkpoint_id,
                interrupt_id, source_run_id, memory_refs, eval_run_id, error_code,
                last_event_seq, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                approval_id=excluded.approval_id,
                backup_id=excluded.backup_id,
                applied_operation_ids=excluded.applied_operation_ids,
                checkpoint_id=excluded.checkpoint_id,
                interrupt_id=excluded.interrupt_id,
                error_code=excluded.error_code,
                last_event_seq=excluded.last_event_seq,
                updated_at=excluded.updated_at
        ''', (
            state.run_id, state.thread_id, state.status, state.plan_id, state.plan_sha256,
            state.target_root_fingerprint, json.dumps(state.operation_ids), state.approval_id,
            state.backup_id, json.dumps(state.applied_operation_ids), state.checkpoint_id,
            state.interrupt_id, state.source_run_id, json.dumps(state.memory_refs),
            state.eval_run_id, state.error_code, state.last_event_seq, state.created_at, state.updated_at
        ))
        self.conn.commit()

    def load(self, run_id: str) -> Optional[WritebackState]:
        row = self.conn.execute('SELECT * FROM writeback_runs WHERE run_id = ?', (run_id,)).fetchone()
        if not row:
            return None
        return WritebackState(
            run_id=row['run_id'],
            thread_id=row['thread_id'],
            status=row['status'],
            plan_id=row['plan_id'],
            plan_sha256=row['plan_sha256'],
            target_root_fingerprint=row['target_root_fingerprint'],
            operation_ids=json.loads(row['operation_ids']),
            approval_id=row['approval_id'],
            backup_id=row['backup_id'],
            applied_operation_ids=json.loads(row['applied_operation_ids']) if row['applied_operation_ids'] else [],
            checkpoint_id=row['checkpoint_id'],
            interrupt_id=row['interrupt_id'],
            source_run_id=row['source_run_id'],
            memory_refs=json.loads(row['memory_refs']) if row['memory_refs'] else [],
            eval_run_id=row['eval_run_id'],
            error_code=row['error_code'],
            last_event_seq=row['last_event_seq'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )

    def close(self):
        self.conn.close()

def _now():
    return datetime.now(timezone.utc).isoformat()

class WritebackRuntime:
    def __init__(self, store: WritebackStore, event_emitter: EventEmitter):
        self.store = store
        self.emitter = event_emitter

    def create_run(self, plan: ChangePlan, thread_id: str = "default", memory_refs: List[str] = None, eval_run_id: Optional[str] = None) -> WritebackState:
        run_id = f"wb_{uuid.uuid4().hex}"
        now = _now()
        state = WritebackState(
            run_id=run_id,
            thread_id=thread_id,
            status="pending",
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            target_root_fingerprint=plan.target_root_fingerprint,
            operation_ids=[op.operation_id for op in plan.operations],
            created_at=now,
            updated_at=now,
            source_run_id=plan.created_from_run_id,
            memory_refs=memory_refs or [],
            eval_run_id=eval_run_id
        )
        self.emitter.run_id = run_id
        self.emitter.thread_id = thread_id
        self.emitter.seq = 0
        self.emitter.emit("writeback.proposed", "runtime", "ok", attributes={"plan_id": plan.plan_id})
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        return state

    def request_approval(self, state: WritebackState) -> WritebackState:
        if state.status not in ("pending", "previewed"):
            raise InvalidStateTransitionError(f"Cannot request approval from {state.status}")
        state.status = "awaiting_approval"
        state.updated_at = _now()
        self.emitter.run_id = state.run_id
        self.emitter.thread_id = state.thread_id
        self.emitter.seq = state.last_event_seq
        self.emitter.emit("writeback.approval.requested", "runtime", "ok", attributes={"plan_id": state.plan_id})
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        return state

    def accept_approval(self, state: WritebackState, approval: Approval) -> WritebackState:
        if state.status in ("approved", "paused", "applying", "verified", "applied", "failed", "rolled_back", "rejected", "stale"):
            self.emitter.run_id = state.run_id
            self.emitter.thread_id = state.thread_id
            self.emitter.seq = state.last_event_seq
            self.emitter.emit("writeback.failed", "runtime", "rejected", error={"code": "DUPLICATE_APPROVAL", "category": "writeback", "message": "safe rejection"})
            state.last_event_seq = self.emitter.seq
            self.store.save(state)
            return state
        if state.status != "awaiting_approval":
            raise InvalidStateTransitionError(f"Cannot accept approval from {state.status}")
            
        if approval.plan_id != state.plan_id or approval.plan_sha256 != state.plan_sha256:
            raise ValueError("PLAN_DIGEST_MISMATCH")
        if approval.target_root_fingerprint != state.target_root_fingerprint:
            raise ValueError("ROOT_FINGERPRINT_MISMATCH")
        if approval.actor != "human":
            raise ValueError("INVALID_APPROVAL")
        if approval.decision != "approve":
            raise ValueError("INVALID_APPROVAL")
        if datetime.now(timezone.utc) > approval.expires_at:
            raise ValueError("EXPIRED_APPROVAL")
        if set(approval.approved_operation_ids) != set(state.operation_ids):
            raise ValueError("INVALID_APPROVAL")

        state.status = "approved"
        state.approval_id = approval.approval_id
        state.updated_at = _now()
        self.emitter.run_id = state.run_id
        self.emitter.thread_id = state.thread_id
        self.emitter.seq = state.last_event_seq
        self.emitter.emit("writeback.approval.accepted", "human", "ok", attributes={"approval_id": approval.approval_id})
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        return state

    def reject_approval(self, state: WritebackState, approval: Approval) -> WritebackState:
        if state.status in ("approved", "paused", "applying", "verified", "applied", "failed", "rolled_back", "rejected", "stale"):
            self.emitter.run_id = state.run_id
            self.emitter.thread_id = state.thread_id
            self.emitter.seq = state.last_event_seq
            self.emitter.emit("writeback.failed", "runtime", "rejected", error={"code": "DUPLICATE_APPROVAL", "category": "writeback", "message": "safe rejection"})
            state.last_event_seq = self.emitter.seq
            self.store.save(state)
            return state
        if state.status != "awaiting_approval":
            raise InvalidStateTransitionError(f"Cannot reject approval from {state.status}")
            
        if approval.plan_id != state.plan_id or approval.plan_sha256 != state.plan_sha256:
            raise ValueError("PLAN_DIGEST_MISMATCH")
        if approval.target_root_fingerprint != state.target_root_fingerprint:
            raise ValueError("ROOT_FINGERPRINT_MISMATCH")
        if approval.actor != "human":
            raise ValueError("INVALID_APPROVAL")
        if approval.decision != "reject":
            raise ValueError("INVALID_APPROVAL")
        if datetime.now(timezone.utc) > approval.expires_at:
            raise ValueError("EXPIRED_APPROVAL")

        state.status = "rejected"
        state.approval_id = approval.approval_id
        state.updated_at = _now()
        self.emitter.run_id = state.run_id
        self.emitter.thread_id = state.thread_id
        self.emitter.seq = state.last_event_seq
        self.emitter.emit("writeback.approval.rejected", "human", "rejected", error={"code": "REJECTED"})
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        return state

    def checkpoint_pause(self, state: WritebackState) -> WritebackState:
        if state.status != "approved":
            raise InvalidStateTransitionError(f"Cannot pause from {state.status}")
        state.status = "paused"
        state.checkpoint_id = f"ckpt_{uuid.uuid4().hex}"
        state.updated_at = _now()
        self.emitter.run_id = state.run_id
        self.emitter.thread_id = state.thread_id
        self.emitter.seq = state.last_event_seq
        self.emitter.emit("writeback.checkpointed", "runtime", "paused", attributes={"checkpoint_id": state.checkpoint_id})
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        return state

    def resume(self, state: WritebackState, plan: ChangePlan, approval: Approval, target_root: Path, backup_root: Path, synthetic_only: bool = True, apply_callable: Optional[Any] = None) -> WritebackState:
        ALLOWED_STATUSES = {"pending", "awaiting_approval", "approved", "paused", "applying", "verified", "applied", "failed", "rolled_back", "rejected", "stale"}
        if state.status not in ALLOWED_STATUSES:
            raise InvalidStateTransitionError("ILLEGAL_TRANSITION")

        if state.status in ("applied", "rolled_back", "rejected", "stale", "failed"):
            self.emitter.run_id = state.run_id
            self.emitter.thread_id = state.thread_id
            self.emitter.seq = state.last_event_seq
            self.emitter.emit("writeback.failed", "runtime", "rejected", error={"code": "RESUME_TERMINAL", "category": "writeback", "message": "safe rejection"})
            state.last_event_seq = self.emitter.seq
            self.store.save(state)
            return state

        if approval.decision == "reject":
            if state.status not in ("applied", "rolled_back", "rejected", "stale", "failed"):
                state.status = "rejected"
                state.error_code = "REJECTED"
                self.store.save(state)
            return state
            
        if state.status not in ("approved", "paused"):
            raise InvalidStateTransitionError(f"ILLEGAL_TRANSITION")

        # Validate approval
        if approval.plan_id != plan.plan_id or approval.plan_sha256 != plan.plan_sha256:
            raise ValueError("PLAN_DIGEST_MISMATCH")
        if approval.target_root_fingerprint != plan.target_root_fingerprint:
            raise ValueError("ROOT_FINGERPRINT_MISMATCH")
        if approval.actor != "human":
            raise ValueError("INVALID_APPROVAL")
        if datetime.now(timezone.utc) > approval.expires_at:
            raise ValueError("EXPIRED_APPROVAL")
        if set(approval.approved_operation_ids) != set(op.operation_id for op in plan.operations):
            raise ValueError("INVALID_APPROVAL")

        # Recompute digest and compare
        computed_digest = ChangePlan.compute_digest(plan.plan_id, plan.operations)
        if computed_digest != plan.plan_sha256 or computed_digest != state.plan_sha256:
            state.status = "failed"
            state.error_code = "PLAN_DIGEST_MISMATCH"
            self.emitter.run_id = state.run_id
            self.emitter.thread_id = state.thread_id
            self.emitter.seq = state.last_event_seq
            self.emitter.emit("writeback.failed", "runtime", "failed", error={"code": "PLAN_DIGEST_MISMATCH"})
            state.last_event_seq = self.emitter.seq
            self.store.save(state)
            return state

        if plan.plan_id != state.plan_id:
            state.status = "failed"
            state.error_code = "PLAN_ID_MISMATCH"
            self.store.save(state)
            return state

        if plan.target_root_fingerprint != state.target_root_fingerprint:
            state.status = "failed"
            state.error_code = "ROOT_FINGERPRINT_MISMATCH"
            self.emitter.run_id = state.run_id
            self.emitter.thread_id = state.thread_id
            self.emitter.seq = state.last_event_seq
            self.emitter.emit("writeback.failed", "runtime", "failed", error={"code": "ROOT_FINGERPRINT_MISMATCH"})
            state.last_event_seq = self.emitter.seq
            self.store.save(state)
            return state

        # Synthetic root/path policy and existence/hash checks
        try:
            validate_target_root(target_root, synthetic_only=synthetic_only, fingerprint=plan.target_root_fingerprint)
            for op in plan.operations:
                validate_target_path(target_root, op.target_relative_path)
                
                target_path = target_root / op.target_relative_path
                if not target_path.exists():
                    raise ValueError("STALE_HASH")
                
                if _hash_file(target_path) != op.expected_sha256:
                    raise ValueError("STALE_HASH")
                    
        except PermissionError as e:
            state.status = "failed"
            state.error_code = str(e)
            self.emitter.run_id = state.run_id
            self.emitter.thread_id = state.thread_id
            self.emitter.seq = state.last_event_seq
            self.emitter.emit("writeback.failed", "runtime", "failed", error={"code": str(e)})
            state.last_event_seq = self.emitter.seq
            self.store.save(state)
            return state
        except ValueError as e:
            if str(e) == "STALE_HASH":
                state.status = "stale"
                state.error_code = "STALE_HASH"
                self.emitter.run_id = state.run_id
                self.emitter.thread_id = state.thread_id
                self.emitter.seq = state.last_event_seq
                self.emitter.emit("writeback.failed", "runtime", "stale", error={"code": "STALE_HASH"})
                state.last_event_seq = self.emitter.seq
                self.store.save(state)
                return state
            else:
                raise

        # Preflight passed
        self.emitter.run_id = state.run_id
        self.emitter.thread_id = state.thread_id
        self.emitter.seq = state.last_event_seq
        self.emitter.emit("writeback.hash_validated", "runtime", "ok", attributes={"plan_id": state.plan_id})
        
        state.status = "applying"
        if not state.checkpoint_id:
            state.checkpoint_id = f"ckpt_{uuid.uuid4().hex}"
        state.updated_at = _now()
        self.emitter.seq = self.emitter.seq  # emit advances seq
        self.emitter.emit("writeback.resumed", "runtime", "started", attributes={"plan_id": state.plan_id})
        self.emitter.emit("writeback.started", "runtime", "started", attributes={"plan_id": state.plan_id})
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        
        if apply_callable is None:
            apply_res = apply_plan(plan, approval, target_root, backup_root, synthetic_only=synthetic_only)
        else:
            apply_res = apply_callable(plan, approval, target_root, backup_root, synthetic_only=synthetic_only)
        
        state.backup_id = apply_res.backup_id
        state.applied_operation_ids = apply_res.applied_operation_ids
        state.updated_at = _now()

        if apply_res.status == "applied":
            state.status = "verified"
            self.emitter.emit("writeback.verified", "runtime", "ok")
            state.status = "applied"
            self.emitter.emit("writeback.completed", "runtime", "ok")
        elif apply_res.status == "stale":
            state.status = "stale"
            state.error_code = "STALE_HASH"
            self.emitter.emit("writeback.failed", "runtime", "stale", error={"code": "STALE_HASH"})
        elif apply_res.status == "rejected":
            state.status = "rejected"
            state.error_code = "REJECTED"
            self.emitter.emit("writeback.failed", "runtime", "failed", error={"code": "REJECTED"})
        elif apply_res.status in ("failed", "rollback_failed"):
            state.status = "failed"
            state.error_code = apply_res.error
            self.emitter.emit("writeback.failed", "runtime", "failed", error={"code": apply_res.error})
        elif apply_res.status == "rolled_back":
            state.status = "rolled_back"
            state.error_code = apply_res.error
            self.emitter.emit("writeback.rollback", "runtime", "failed", error={"code": apply_res.error})
            
        state.last_event_seq = self.emitter.seq
        self.store.save(state)
        
        return state
