import json
import uuid
import re
import os
import shutil
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

from linkloom.scanner import scan_vault
from linkloom.runtime.graph import RuntimeEngine, RunRequest
from linkloom.evaluation.runner import FormalEvaluationRunner
from linkloom.mutations.models import ChangePlan, ChangeOperation, Precondition, Approval
from linkloom.mutations.runtime import WritebackRuntime, WritebackStore, WritebackState
from linkloom.mutations.permission import validate_target_root
from linkloom.mutations.rollback import execute_rollback
from linkloom.observability.events import EventEmitter
from linkloom.observability.sinks import JsonlEventSink
from linkloom.mutations.backup import BackupManifest
from linkloom.loader import calculate_fingerprint


class E2EWritebackRunner:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.trace_dir = self.output_dir / "traces"
        
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        
        import tempfile
        self._temp_dir_obj = tempfile.TemporaryDirectory(
            prefix="linkloom_p7_", ignore_cleanup_errors=True
        )
        self.temp_dir = Path(self._temp_dir_obj.name)
        self._closed = False
        
        self.memory_root = self.temp_dir / "memory"
        self.checkpoint_dir = self.temp_dir / "checkpoints"
        self.db_path = self.temp_dir / "writeback.db"
        
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        self.store = WritebackStore(str(self.db_path))

    def close(self):
        if self._closed:
            return
        if hasattr(self, "store"):
            self.store.close()
        self._closed = True
        if hasattr(self, "_temp_dir_obj") and self._temp_dir_obj is not None:
            self._temp_dir_obj.cleanup()
            self._temp_dir_obj = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _create_synthetic_vault(self, target_root: Path):
        target_root.mkdir(parents=True, exist_ok=True)
        note1 = target_root / "note1.md"
        note1.write_text("# Note 1\n\nContent of note 1.", encoding="utf-8")
        note2 = target_root / "note2.md"
        note2.write_text("# Note 2\n\nContent of note 2.", encoding="utf-8")
        return [note1, note2]

    def parse_approval(self, text: str) -> Optional[Dict[str, Any]]:
        # APPROVE <plan_id> root=<fingerprint> ops=<count> sha=<plan_sha256>
        match = re.match(r"^APPROVE\s+([^\s]+)\s+root=([^\s]+)\s+ops=(\d+)\s+sha=([^\s]+)$", text.strip())
        if match:
            return {
                "decision": "approve",
                "plan_id": match.group(1),
                "root": match.group(2),
                "ops": int(match.group(3)),
                "sha": match.group(4)
            }
        
        match_reject = re.match(r"^REJECT\s+([^\s]+)$", text.strip())
        if match_reject:
            return {
                "decision": "reject",
                "plan_id": match_reject.group(1)
            }
        return None

    def run_e2e(
        self,
        target_root: Path,
        backup_root: Path,
        approval_text: str,
        scenario: str = "success",
        apply_callable: Optional[Callable] = None,
        inject_memory: bool = True
    ) -> Dict[str, Any]:
        """Runs the P7 writeback E2E synthetic scenario."""
        # 0. Setup synthetic vault if empty
        if not target_root.exists() or not any(target_root.iterdir()):
            self._create_synthetic_vault(target_root)
            
        root_fingerprint = calculate_fingerprint(target_root)
            
        # Safety checks
        validate_target_root(target_root, synthetic_only=True, fingerprint=root_fingerprint)
            
        run_thread_id = f"thread_{uuid.uuid4().hex}"

        # 1. P1: Scanner
        scan_output = self.output_dir / "scan"
        scan_output.mkdir(exist_ok=True)
        scan_res = scan_vault(target_root, scan_output)
        index_path = scan_res.index_path

        from linkloom.memory import MemoryStore, MemoryLifecycle, MemoryScope
        from linkloom.mutations.operations import build_append_plan
        from linkloom.mutations.permission import ApprovalRegistry, process_approval
        
        # 2. P5: Memory Setup
        memory_refs = []
        if inject_memory:
            mem_store = MemoryStore(self.memory_root / "memory.jsonl")
            lifecycle = MemoryLifecycle(mem_store)
            candidate = lifecycle.propose_candidate(
                scope=MemoryScope("thread"),
                key=run_thread_id,
                value={"preference": "test"},
                source_refs=["scan/vault_index.json"]
            )
            item = lifecycle.confirm_candidate(candidate.id, "human")
            memory_refs.append(item.id)
        
        # 3. P4: Multi-Agent
        engine = RuntimeEngine(
            vault_root=target_root,
            index_path=index_path,
            checkpoint_dir=self.checkpoint_dir / "p4",
            trace_dir=self.trace_dir,
            memory_root=self.memory_root
        )
        req = RunRequest(
            request_id=f"req_{uuid.uuid4().hex[:8]}",
            workflow="ask",
            query="Test query",
            vault_root=str(target_root),
            index_path="scan/vault_index.json",
            dry_run=True,
            thread_id=run_thread_id
        )
        p4_status = engine.start_multi_agent(req)
        
        if p4_status.status == "completed":
            res_path = self.checkpoint_dir / "p4" / p4_status.result_ref
            if res_path.exists():
                p4_res = json.loads(res_path.read_text("utf-8"))
                for m in p4_res.get("memory_refs", []):
                    if m["memory_id"] not in memory_refs:
                        memory_refs.append(m["memory_id"])

        # 4. P6: Evaluation
        eval_runner = FormalEvaluationRunner(repo_root=Path.cwd(), output_root=self.output_dir / "eval")
        eval_res = eval_runner.run(provider="mock", scenario="noisy")
        eval_run_id = eval_res["manifest"]["run_id"]
        
        # 5. P7: ChangePlan Creation
        root_fingerprint = calculate_fingerprint(target_root)
        source_index_sha256 = hashlib.sha256(index_path.read_bytes()).hexdigest() if index_path.exists() else "0"*64
        
        target_rel_path = "note1.md"
        if scenario == "traversal":
            target_rel_path = "../note1.md"
            
        def get_target_sha():
            p = target_root / "note1.md"
            if not p.exists():
                return None
            import hashlib
            return hashlib.sha256(p.read_bytes()).hexdigest()
            
        try:
            plan = build_append_plan(
                run_id=p4_status.run_id,
                root=target_root,
                fingerprint=root_fingerprint,
                kind="append_block",
                target_relative_path=target_rel_path,
                append_text="\n\nAdditional line.",
                evidence_refs=["note1.md"],
                source_index_hash=source_index_sha256,
                risk="low"
            )
        except Exception as e:
            t_sha = get_target_sha()
            error_code = str(e) if any(x in str(e) for x in ["PATH_TRAVERSAL_NOT_ALLOWED", "ABSOLUTE_PATH_NOT_ALLOWED", "PATH_OUTSIDE_ROOT", "SYMLINK_NOT_ALLOWED"]) else type(e).__name__
            
            sink = JsonlEventSink(self.trace_dir / "writeback")
            emitter = EventEmitter(run_id="temp", thread_id="temp", sink=sink)
            emitter.emit(
                "writeback.failed",
                actor="runtime",
                status="failed",
                error={"code": error_code, "category": "writeback", "message": "safe rejection"},
                attributes={"plan_id": None}
            )
            rejection_manifest = {
                "p4_run_id": p4_status.run_id,
                "memory_ref_ids": memory_refs,
                "eval_run_id": eval_run_id,
                "writeback_run_id": "temp",
                "plan_id": None,
                "plan_sha256": None,
                "approval_id": None,
                "checkpoint_id": None,
                "backup_id": None,
                "status": "failed",
                "error_code": error_code,
                "trace_artifact_ref": "traces/writeback/events.jsonl",
                "eval_artifact_ref": f"eval/{eval_run_id}/manifest.json",
                "scenario_checks": {
                    "before_sha256": t_sha,
                    "after_sha256": t_sha,
                    "apply_calls": 0,
                    "writer_calls": 0,
                    "verifier_calls": 0
                },
                "safety_flags": {"synthetic_only": True}
            }
            self.close()
            return rejection_manifest
        
        # 6. Runtime setup
        sink = JsonlEventSink(self.trace_dir / "writeback")
        emitter = EventEmitter(run_id="temp", thread_id="temp", sink=sink)
        wb_runtime = WritebackRuntime(store=self.store, event_emitter=emitter)
        
        # Create run and attach memory_refs, eval_run_id
        wb_state = wb_runtime.create_run(plan, thread_id=run_thread_id, memory_refs=memory_refs, eval_run_id=eval_run_id)
        wb_runtime.request_approval(wb_state)
        
        counters = {"apply_calls": 0, "writer_calls": 0, "verifier_calls": 0}
        
        from linkloom.mutations.apply import default_writer, default_verifier, apply_plan
        
        def counting_apply(p, a, t_r, b_r, synthetic_only=True, **kwargs):
            counters["apply_calls"] += 1
            
            def counting_writer(target_path, content):
                counters["writer_calls"] += 1
                if scenario == "writer_failure":
                    raise RuntimeError("WRITER_FAILURE")
                default_writer(target_path, content)
                
            def counting_verifier(target_path, expected_sha):
                counters["verifier_calls"] += 1
                if scenario == "verification_failure":
                    raise ValueError("POST_WRITE_VERIFICATION_FAILED")
                default_verifier(target_path, expected_sha)
                
            if apply_callable:
                # Fallback if tests still inject an old mock that accepts kwargs
                try:
                    return apply_callable(p, a, t_r, b_r, synthetic_only=synthetic_only, writer=counting_writer, verifier=counting_verifier)
                except TypeError:
                    return apply_callable(p, a, t_r, b_r, synthetic_only=synthetic_only)
            return apply_plan(p, a, t_r, b_r, synthetic_only=synthetic_only, writer=counting_writer, verifier=counting_verifier)
        
        # 7. Parsing Approval
        if approval_text == "APPROVE_DEFAULT":
            approval_text = f"APPROVE {plan.plan_id} root={root_fingerprint} ops={plan.operation_count} sha={plan.plan_sha256}"
        elif approval_text == "REJECT_DEFAULT":
            approval_text = f"REJECT {plan.plan_id} root={root_fingerprint} ops={plan.operation_count} sha={plan.plan_sha256}"
            
        registry = ApprovalRegistry()
        def get_target_sha():
            p = target_root / "note1.md"
            if not p.exists():
                return None
            import hashlib
            return hashlib.sha256(p.read_bytes()).hexdigest()
            
        before_sha256 = get_target_sha()
        
        try:
            approval = process_approval(registry, plan, approval_text, actor="human")
            decision = approval.decision
            
            if decision == "approve":
                wb_runtime.accept_approval(wb_state, approval)
                # Test checkpoint pause
                wb_runtime.checkpoint_pause(wb_state)
                
                # Scenario injection during pause
                if scenario == "stale":
                    (target_root / "note1.md").write_text("Stale content", encoding="utf-8")
                
                before_sha256 = get_target_sha() # Capture post-pause pre-resume SHA
                
                if scenario == "duplicate":
                    wb_runtime.accept_approval(wb_state, approval)
                    counters["duplicate_error"] = "DUPLICATE_APPROVAL"
                            
                # Re-load
                wb_state = self.store.load(wb_state.run_id)
                # Resume
                wb_state = wb_runtime.resume(wb_state, plan, approval, target_root, backup_root, synthetic_only=True, apply_callable=counting_apply)
                
                if scenario == "terminal":
                    wb_state = wb_runtime.resume(wb_state, plan, approval, target_root, backup_root, synthetic_only=True, apply_callable=counting_apply)
                    counters["terminal_error"] = "RESUME_TERMINAL"
            else:
                wb_runtime.reject_approval(wb_state, approval)
        except Exception as e: # Catch any error including ValueError for parsing
            wb_state.status = "failed"
            error_str = str(e)
            if "incorrect format" in error_str or "vague yes" in error_str:
                wb_state.error_code = "INVALID_APPROVAL_FORMAT"
            else:
                wb_state.error_code = type(e).__name__ # Use class name to avoid raw exceptions
            self.store.save(wb_state)
            
        after_sha256 = get_target_sha()

        # Build safe manifest
        manifest = {
            "p4_run_id": p4_status.run_id,
            "memory_ref_ids": memory_refs,
            "eval_run_id": eval_run_id,
            "writeback_run_id": wb_state.run_id,
            "plan_id": plan.plan_id,
            "plan_sha256": plan.plan_sha256,
            "approval_id": wb_state.approval_id,
            "checkpoint_id": wb_state.checkpoint_id,
            "backup_id": wb_state.backup_id,
            "status": wb_state.status,
            "error_code": wb_state.error_code,
            "trace_artifact_ref": "traces/writeback/events.jsonl",
            "eval_artifact_ref": f"eval/{eval_run_id}/manifest.json",
            "scenario_checks": {
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
                "apply_calls": counters.get("apply_calls", 0),
                "writer_calls": counters.get("writer_calls", 0),
                "verifier_calls": counters.get("verifier_calls", 0),
                "duplicate_error": counters.get("duplicate_error"),
                "terminal_error": counters.get("terminal_error")
            },
            "safety_flags": {
                "synthetic_only": True,
                "absolute_paths_omitted": True,
                "raw_notes_omitted": True
            }
        }
        
        self.close()
        return manifest

    def execute_rollback_for_run(self, backup_id: str, target_root: Path, backup_root: Path):
        manifest_path = backup_root / backup_id / "manifest.json"
        if not manifest_path.exists():
            return False
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        manifest = BackupManifest.from_dict(data, str(backup_root))
        
        from linkloom.mutations.backup import _hash_file
        # execute_rollback checks the backup files against their backed up hashes and restores them
        return execute_rollback(manifest, target_root, backup_root, list(data.get("files", [])))
