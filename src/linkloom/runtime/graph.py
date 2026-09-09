"""Linkloom Runtime execution graph and engine."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from linkloom.loader import VaultReader, LoaderError, calculate_fingerprint
from linkloom.retrieval import retrieve_evidence, find_relation_candidates_with_evidence
from linkloom.services.ask import AskService
from linkloom.services.connect import ConnectService
from linkloom.runtime.models import (
    AttemptRecord,
    ErrorEnvelope,
    InterruptEnvelope,
    PolicySnapshot,
    RunRequest,
    RunStatus,
    RuntimeState,
    SourceContext,
    TerminationState,
    UsageEnvelope,
    validate_state_transition,
)
from linkloom.runtime.checkpoint import (
    BaseCheckpointer,
    InMemoryCheckpointer,
    SQLiteCheckpointer,
)
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.policy import ReadOnlyPolicy
from linkloom.runtime.recovery import decide_model_resume
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.runtime.errors import (
    BudgetExceededError,
    CheckpointError,
    CheckpointWriteError,
    InterruptNotFoundError,
    InterruptResponseInvalidError,
    PermissionDeniedError,
    RuntimeModelError,
    RuntimeSchemaIncompatibleError,
    StaleSourceError,
    StateTransitionError,
    ThreadBusyError,
    ThreadNotFoundError,
    ValidationError,
)

# Dual-Track Explanation for Interview Readiness:
# 大白话 (Plain-Language Analogy):
# RuntimeEngine 就像是一个“带存档功能的打字机”。每一次打字（执行步骤），它都会在一张纸上做记录并放入抽屉（Checkpoint）。
# 即使中途停电或者纸张被抽走（故障注入、进程中断），我们也可以从抽屉里拿出上一张纸（Resume）继续，
# 并且会先核对纸上的水印（Source Hash）是否与原来一致，防止有人篡改了背景笔记。
#
# 专业学术概念 (Industry Technical Definition):
# RuntimeEngine encapsulates a durable finite state machine (FSM) control plane.
# It enforces state persistence across step boundaries using an abstract Checkpointer interface,
# implements structured exception categorization to translate runtime faults into non-interactive failures,
# and verifies note index version integrity (via cryptographic SHA-256 hashes) before transitioning to downstream nodes,
# mitigating stale read anomalies in distributed state operations.


class OptionalTracer:
    def __init__(self, trace_dir: Path | str | None, run_id: str, thread_id: str, load_existing: bool = False):
        self.active = bool(trace_dir)
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.run_id = run_id
        self.thread_id = thread_id
        self.emitter = None
        self.reader = None
        self.manifest_error: str | None = None
        if self.active:
            from linkloom.observability.sinks import JsonlEventSink
            from linkloom.observability.events import EventEmitter
            from linkloom.observability.reader import TraceReader
            run_trace_dir = self.trace_dir / run_id
            self.sink = JsonlEventSink(run_trace_dir)
            self.emitter = EventEmitter(run_id, thread_id, self.sink)
            self.reader = TraceReader(self.trace_dir)
            if load_existing:
                events = self.reader.read_events(run_id)
                self.emitter.load_existing_events(events)

    def emit(
        self,
        event_type: str,
        status: str,
        node_name: str | None = None,
        attributes: dict[str, Any] | None = None,
        error: Any = None,
        duration_ms: int | None = None,
        output_ref: dict[str, Any] | None = None,
        actor: str = "runtime",
        parent_event_id: str | None = None,
    ):
        if self.active and self.emitter:
            return self.emitter.emit(
                event_type=event_type,
                actor=actor,
                status=status,
                parent_event_id=parent_event_id,
                node_name=node_name,
                attributes=attributes,
                error=error,
                duration_ms=duration_ms,
                output_ref=output_ref,
            )

    def write_manifest(self, complete: bool, source_index_sha256: str, incomplete_reason: str | None = None):
        if not (self.active and self.reader):
            return None
        from linkloom.observability.reader import TraceManifest

        if len(source_index_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_index_sha256):
            source_index_sha256 = "0" * 64
        events = self.reader.read_events(self.run_id)
        manifest = TraceManifest(
            trace_schema_version=1,
            run_id=self.run_id,
            event_count=len(events),
            first_seq=events[0].seq if events else None,
            last_seq=events[-1].seq if events else None,
            event_log="events.jsonl",
            summary="trace_summary.md",
            source_index_sha256=source_index_sha256,
            redaction_policy_version="trace-redaction-v1",
            complete=complete,
            incomplete_reason=incomplete_reason,
        )
        self.reader.write_manifest(manifest)
        return manifest


DEFAULT_TRACE_DIR = Path(".artifacts") / "traces"


class RuntimeEngine:
    """Engine executing the Linkloom Runtime state graph."""

    def __init__(
        self,
        vault_root: Path | str,
        index_path: Path | str,
        checkpoint_dir: Path | str,
        checkpointer: BaseCheckpointer | None = None,
        pause_after: str | None = None,
        fail_at: str | None = None,
        fail_once: bool = True,
        trace_dir: Path | str | None = None,
        memory_root: Path | str | None = None,
        model: Any | None = None,
    ) -> None:
        self.vault_root = Path(vault_root)
        self.index_path = Path(index_path)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.trace_dir = Path(trace_dir) if trace_dir is not None else DEFAULT_TRACE_DIR
        self.memory_root = Path(memory_root) if memory_root is not None else self.checkpoint_dir / "memory"
        
        resolved_root = self.vault_root.resolve()
        resolved_checkpoint_dir = self.checkpoint_dir.resolve()
        if resolved_checkpoint_dir == resolved_root or resolved_root in resolved_checkpoint_dir.parents:
            raise ValidationError(
                "Runtime checkpoint_dir must be outside the Vault root.",
                details={"code": "CHECKPOINT_INSIDE_VAULT"},
            )
            
        resolved_memory_root = self.memory_root.resolve()
        if resolved_memory_root == resolved_root or resolved_root in resolved_memory_root.parents:
            raise ValidationError(
                "Runtime memory_root must be outside the Vault root.",
                details={"code": "MEMORY_INSIDE_VAULT"},
            )
            
        if self.trace_dir:
            resolved_trace_dir = self.trace_dir.resolve()
            if resolved_trace_dir == resolved_root or resolved_root in resolved_trace_dir.parents:
                raise ValidationError(
                    "Runtime trace_dir must be outside the Vault root.",
                    details={"code": "TRACE_INSIDE_VAULT"},
                )
                
        self.checkpointer = checkpointer or SQLiteCheckpointer(self.checkpoint_dir)
        self.pause_after = pause_after
        self.fail_at = fail_at
        self.fail_once = fail_once
        self.model = model

        # Track failure injection occurrence
        self._injected_failed = False
        self.policy = ReadOnlyPolicy()

    def _save_state(self, state: RuntimeState) -> str:
        """Helper to save state to checkpointer. Wraps write errors appropriately."""
        try:
            return self.checkpointer.save(state)
        except CheckpointWriteError:
            raise
        except Exception as e:
            raise CheckpointWriteError(
                f"Failed to persist checkpoint: {e}",
                details={"error": str(e)},
            )

    def _calculate_index_sha256(self) -> str:
        """Helper to compute current index file sha256."""
        if not self.index_path.exists():
            raise LoaderError(
                f"Index file not found at {self.index_path}",
                code="SOURCE_NOT_FOUND",
            )
        return hashlib.sha256(self.index_path.read_bytes()).hexdigest()

    def _build_status(self, state: RuntimeState, checkpoint_id: str | None = None) -> RunStatus:
        """Map RuntimeState to RunStatus summary."""
        interrupt_dict = state.pending_interrupt.to_dict() if state.pending_interrupt else None
        error_dict = state.error.to_dict() if state.error else None
        return RunStatus(
            run_id=state.run_id,
            thread_id=state.thread_id,
            status=state.status,
            current_step=state.current_step,
            checkpoint_id=checkpoint_id,
            interrupt=interrupt_dict,
            result_ref=state.result_ref,
            error=error_dict,
        )

    def start(self, request: RunRequest) -> RunStatus:
        """Create a new run instance and begin step executions."""
        if request.workflow not in ("ask", "connect"):
            raise ValidationError(f"Workflow '{request.workflow}' not supported in P2.")

        # Ensure no cross-thread state leakage by assigning clean thread if null
        thread_id = request.thread_id or f"thread_p2_{uuid.uuid4().hex[:12]}"
        
        # Verify if thread already exists to prevent overwrite
        if self.checkpointer.get_latest(thread_id) is not None:
            raise ThreadBusyError(f"Thread '{thread_id}' already has active records.")

        run_id = f"run_p2_{uuid.uuid4().hex[:12]}"
        run_policy = ReadOnlyPolicy(
            max_steps=request.max_steps,
            max_provider_requests=request.max_provider_requests,
        )
        
        tracer = OptionalTracer(self.trace_dir, run_id, thread_id)
        tracer.emit("run.accepted", "ok")
        tracer.emit("run.started", "started")

        # Calculate source state
        try:
            idx_sha = self._calculate_index_sha256()
        except Exception as e:
            # Wrap as structured failure
            err = ErrorEnvelope(code="SOURCE_NOT_FOUND", category="path", message=str(e))
            tracer.emit("run.failed", "failed", error=err)
            tracer.write_manifest(complete=False, source_index_sha256="0"*64, incomplete_reason="Source not found")
            return RunStatus(run_id=run_id, thread_id=thread_id, status="failed", error=err.to_dict())

        # Write request file inside checkpoint directory to separate from vault root
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        req_ref = f"request_{run_id}.json"
        req_path = self.checkpoint_dir / req_ref
        req_path.write_text(json.dumps(request.to_dict(include_process_fields=False), ensure_ascii=False, indent=2))

        source_context = SourceContext(
            index_path=str(self.index_path.relative_to(self.checkpoint_dir) if self.index_path.is_relative_to(self.checkpoint_dir) else self.index_path.name),
            index_sha256=idx_sha,
            index_schema_version=1,
            vault_root_fingerprint=calculate_fingerprint(self.vault_root),
            fixture_id=None,
            document_count=0,
        )

        state = RuntimeState(
            schema_version=1,
            run_id=run_id,
            thread_id=thread_id,
            workflow=request.workflow,
            status="accepted",
            step_seq=0,
            request_ref=req_ref,
            source=source_context,
            policy=run_policy.to_snapshot(),
            usage=UsageEnvelope(step_count=0),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        # accept_request node
        state = RuntimeState.from_dict({
            **state.to_dict(),
            "status": "running",
            "current_step": "resolve_source",
            "step_seq": 1,
            "usage": {"step_count": 1},
        })

        try:
            checkpoint_id = self._save_state(state)
            tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": checkpoint_id})
        except CheckpointWriteError as e:
            tracer.emit("checkpoint.failed", "failed", error=e)
            tracer.write_manifest(complete=False, source_index_sha256=idx_sha, incomplete_reason="Checkpoint failed")
            return RunStatus(
                run_id=run_id,
                thread_id=thread_id,
                status="failed",
                current_step="accept_request",
                error=e.to_envelope(),
            )

        return self._execute(state, checkpoint_id, tracer)

    def start_multi_agent(self, request: RunRequest) -> RunStatus:
        """Run the P4 manager-as-tools workflow with durable checkpoint/trace output.

        This is additive to the P2 graph. It deliberately has no interrupt or
        write path: the coordinator returns a read-only result artifact and
        the Runtime persists only refs and task metadata in its checkpoint.
        """
        if request.workflow not in ("ask", "connect"):
            raise ValidationError(f"Workflow '{request.workflow}' not supported in P4.")

        thread_id = request.thread_id or f"thread_p4_{uuid.uuid4().hex[:12]}"
        if self.checkpointer.get_latest(thread_id) is not None:
            raise ThreadBusyError(f"Thread '{thread_id}' already has active records.")
        run_id = f"run_p4_{uuid.uuid4().hex[:12]}"
        tracer = OptionalTracer(self.trace_dir, run_id, thread_id)
        tracer.emit("run.accepted", "ok")
        tracer.emit("run.started", "started")

        if self.model is None:
            error_env = ErrorEnvelope(
                code="MODEL_NOT_CONFIGURED",
                category="provider",
                message="Multi-agent execution requires an injected model.",
            )
            tracer.emit("run.failed", "failed", error=error_env)
            tracer.write_manifest(
                complete=False,
                source_index_sha256="0" * 64,
                incomplete_reason="model not configured",
            )
            return RunStatus(
                run_id=run_id,
                thread_id=thread_id,
                status="failed",
                error=error_env.to_dict(),
            )
        if request.max_provider_requests <= 0:
            error_env = ErrorEnvelope(
                code="MODEL_PROVIDER_BUDGET_EXHAUSTED",
                category="budget",
                message="Multi-agent execution requires a positive model request budget.",
            )
            tracer.emit("run.failed", "failed", error=error_env)
            tracer.write_manifest(
                complete=False,
                source_index_sha256="0" * 64,
                incomplete_reason="model request budget exhausted",
            )
            return RunStatus(
                run_id=run_id,
                thread_id=thread_id,
                status="failed",
                error=error_env.to_dict(),
            )

        try:
            index_sha = self._calculate_index_sha256()
            from linkloom.agents.runtime_adapter import RuntimeAgentAdapter

            adapter = RuntimeAgentAdapter(self.vault_root, self.index_path)
            documents = adapter.reader.read_notes()
        except Exception as exc:
            error_env = ErrorEnvelope(
                code=getattr(exc, "code", "SOURCE_ERROR"),
                category="path",
                message=str(exc),
            )
            tracer.emit("run.failed", "failed", error=error_env)
            tracer.write_manifest(complete=False, source_index_sha256="0" * 64, incomplete_reason="source failed")
            return RunStatus(run_id=run_id, thread_id=thread_id, status="failed", error=error_env.to_dict())

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        request_ref = f"request_{run_id}.json"
        (self.checkpoint_dir / request_ref).write_text(
            json.dumps(request.to_dict(include_process_fields=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        source_context = SourceContext(
            index_path=self.index_path.name,
            index_sha256=index_sha,
            index_schema_version=1,
            vault_root_fingerprint=calculate_fingerprint(self.vault_root),
            document_count=len(documents),
        )
        state = RuntimeState(
            schema_version=1,
            run_id=run_id,
            thread_id=thread_id,
            workflow=request.workflow,
            status="accepted",
            step_seq=0,
            request_ref=request_ref,
            source=source_context,
            policy=ReadOnlyPolicy(
                max_steps=request.max_steps,
                max_provider_requests=request.max_provider_requests,
            ).to_snapshot(),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        state = RuntimeState.from_dict({
            **state.to_dict(),
            "status": "running",
            "current_step": "multi_agent",
            "step_seq": 1,
            "termination": TerminationState(status="running", sequence=1).to_dict(),
        })
        # RuntimeState is the canonical cursor for the durable model loop.
        # The legacy ledger callback below remains only as a compatibility
        # wrapper for direct ToolRuntime callers.
        state_cursor = {"state": state}

        def persist_runtime_state(current_state: RuntimeState) -> None:
            if not isinstance(current_state, RuntimeState):
                raise ValidationError("Runtime checkpoint callback requires RuntimeState.")
            if current_state.run_id != run_id or current_state.thread_id != thread_id:
                raise ValidationError(
                    "Runtime checkpoint identity does not match the active run.",
                    details={"reason": "checkpoint_identity_mismatch"},
                )
            self._save_state(current_state)
            state_cursor["state"] = current_state

        tool_ledger = ToolExecutionLedger(state.tool_ledger)

        def persist_tool_ledger(current_ledger: ToolExecutionLedger) -> None:
            if not isinstance(current_ledger, ToolExecutionLedger):
                raise ValidationError("Tool ledger checkpoint requires ToolExecutionLedger.")
            previous = state_cursor["state"]
            updated = RuntimeState.from_dict({
                **previous.to_dict(),
                "current_step": "tool_execution",
                "step_seq": previous.step_seq + 1,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tool_ledger": [record.to_dict() for record in current_ledger.to_list()],
            })
            persist_runtime_state(updated)

        try:
            checkpoint_id = self._save_state(state)
            tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": checkpoint_id})
            artifact_store = ModelArtifactStore(self.checkpoint_dir / "models")
            from linkloom.memory.store import MemoryStore
            from linkloom.memory.retriever import MemoryRetriever
            from linkloom.memory.models import MemoryScope

            self.memory_root.mkdir(parents=True, exist_ok=True)
            store = MemoryStore(str(self.memory_root / "memory.jsonl"))
            retriever = MemoryRetriever(store.list_active())
            injected_memory = retriever.retrieve(
                query=request.query,
                scope=MemoryScope.THREAD,
                scope_key=thread_id,
            )
            memory_refs = [
                {
                    "memory_id": m["memory_id"],
                    "scope": m["scope"],
                    "key": m["key"],
                    "value_sha256": m["value_sha256"],
                }
                for m in injected_memory
            ]
            if memory_refs:
                tracer.emit("memory.injected", "ok", attributes={
                    "count": len(memory_refs),
                    "refs": [
                        {
                            "memory_id": ref["memory_id"],
                            "scope": ref["scope"],
                            "value_sha256": ref["value_sha256"],
                        }
                        for ref in memory_refs
                    ],
                })

            result = adapter.run(
                run_id=run_id,
                workflow=request.workflow,
                query=request.query,
                source_context=source_context.to_dict(),
                tracer=tracer,
                max_total_steps=min(12, request.max_steps),
                injected_memory=injected_memory,
                tool_ledger=tool_ledger,
                tool_checkpoint_callback=persist_tool_ledger,
                model=self.model,
                initial_state=state_cursor["state"],
                artifact_store=artifact_store,
                state_checkpoint_callback=persist_runtime_state,
            )
            # The callback may have advanced the durable cursor several times
            # while the coordinator was running.
            state = state_cursor["state"]

            return self._finish_multi_agent(state, result, tracer)
        except Exception as exc:
            state = state_cursor["state"]
            error_env = ErrorEnvelope(
                code=getattr(exc, "code", "AGENT_RUNTIME_ERROR"),
                category=getattr(exc, "category", "agent"),
                message="The multi-agent runtime failed safely.",
            )
            failed_state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "current_step": "multi_agent",
                "step_seq": state.step_seq + 1,
                "tool_ledger": [record.to_dict() for record in state.tool_ledger],
                "termination": TerminationState(
                    status="failed",
                    reason_code="agent_runtime_error",
                    reason="The runtime could not complete the coordinator execution.",
                    sequence=state.step_seq + 1,
                ).to_dict(),
                "error": error_env.to_dict(),
            })
            checkpoint_id = self._save_state(failed_state)
            tracer.emit("run.failed", "failed", error=error_env)
            tracer.write_manifest(complete=False, source_index_sha256=index_sha, incomplete_reason="agent runtime error")
            return self._build_status(failed_state, checkpoint_id)

    def _finish_multi_agent(self, state: RuntimeState, result: dict[str, Any], tracer: OptionalTracer) -> RunStatus:
        """Publish the same coordinator result for fresh and resumed execution."""
        run_id = state.run_id
        index_sha = state.source.index_sha256
        result_dir = self.checkpoint_dir / "results" / run_id
        result_dir.mkdir(parents=True, exist_ok=True)
        result_ref = f"results/{run_id}/result.json"
        (result_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result_status = result.get("status")
        if result_status == "completed":
            state_data = {
                **state.to_dict(),
                "status": "completed",
                "current_step": None,
                "step_seq": state.step_seq + 1,
                "agent_tasks": list(result.get("agent_tasks", [])),
                "evidence_refs": list(result.get("evidence", [])),
                "memory_refs": list(result.get("memory_refs", [])),
                "tool_ledger": [record.to_dict() for record in state.tool_ledger],
                "termination": TerminationState(
                    status="completed",
                    reason_code="workflow_completed",
                    reason="Coordinator completed the model-driven workflow.",
                    sequence=state.step_seq + 1,
                ).to_dict(),
                "result_ref": result_ref,
            }
            final_state = RuntimeState.from_dict(state_data)
            checkpoint_id = self._save_state(final_state)
            tracer.emit(
                "artifact.written",
                "ok",
                node_name="multi_agent",
                output_ref={"kind": "artifact", "path": result_ref},
            )
            tracer.emit("run.completed", "ok", attributes={"result_ref": result_ref})
            tracer.write_manifest(complete=True, source_index_sha256=index_sha)
            return self._build_status(final_state, checkpoint_id)

        error_env = ErrorEnvelope(
            code="AGENT_WORKFLOW_FAILED",
            category="agent",
            message="Multi-agent coordinator did not complete.",
            details={"error_count": len(result.get("errors", []))},
        )
        final_state = RuntimeState.from_dict({
            **state.to_dict(),
            "status": "failed",
            "current_step": "multi_agent",
            "step_seq": state.step_seq + 1,
            "agent_tasks": list(result.get("agent_tasks", [])),
            "evidence_refs": list(result.get("evidence", [])),
            "memory_refs": list(result.get("memory_refs", [])),
            "tool_ledger": [record.to_dict() for record in state.tool_ledger],
            "termination": TerminationState(
                status="failed",
                reason_code="agent_workflow_failed",
                reason="Coordinator did not complete the workflow.",
                sequence=state.step_seq + 1,
            ).to_dict(),
            "error": error_env.to_dict(),
        })
        checkpoint_id = self._save_state(final_state)
        tracer.emit("run.failed", "failed", error=error_env)
        tracer.write_manifest(complete=False, source_index_sha256=index_sha, incomplete_reason="agent workflow failed")
        return self._build_status(final_state, checkpoint_id)

    def resume_multi_agent(self, thread_id: str) -> RunStatus:
        """Reopen an active retrieval execution without replaying ambiguous work."""
        from linkloom.agents.runtime_adapter import RuntimeAgentAdapter

        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValidationError("Resume requires a non-empty thread ID.")
        state = self.checkpointer.get_latest(thread_id)
        if state is None:
            raise ThreadNotFoundError("No checkpoint exists for the requested thread.")
        if (state.thread_id != thread_id or state.status != "running"
                or state.current_step != "model_loop" or state.agent_tasks
                or state.termination is None or state.termination.status != "running"):
            raise StateTransitionError("Only an active retrieval model loop can be resumed.")
        if not callable(getattr(self.model, "decide", None)) and not callable(getattr(self.model, "complete", None)):
            raise ValidationError("Multi-agent resume requires an injected model.")

        # Resume accepts only an identity. The original request and limits come
        # from the checkpoint store; paths supplied to this engine remain local.
        try:
            root = self.checkpoint_dir.resolve()
            request_path = (root / state.request_ref).resolve()
            if not request_path.is_relative_to(root) or Path(state.request_ref).is_absolute():
                raise ValueError("request outside checkpoint root")
            request = RunRequest.from_dict(json.loads(request_path.read_text(encoding="utf-8")))
        except Exception:
            raise ValidationError("The persisted run request is unavailable or invalid.") from None
        if (request.workflow != state.workflow
                or request.thread_id not in (None, thread_id)
                or request.max_steps != state.policy.max_steps
                or request.max_provider_requests != state.policy.max_provider_requests):
            raise ValidationError("The persisted run request does not match the checkpoint.")
        try:
            if (calculate_fingerprint(self.vault_root) != state.source.vault_root_fingerprint
                    or self._calculate_index_sha256() != state.source.index_sha256):
                raise StaleSourceError("The source no longer matches the durable run.")
        except (LoaderError, OSError):
            raise StaleSourceError("The durable source is unavailable.") from None

        records = state.model_executions
        task_ids = {record.task_id for record in records}
        if (len(task_ids) != 1 or any(record.run_id != state.run_id
                or record.agent_id != "retrieval_agent" for record in records)):
            raise ValidationError("Checkpoint must identify one canonical retrieval task.")
        task_id = next(iter(task_ids))
        if any((turn.run_id, turn.task_id, turn.agent_id) != (state.run_id, task_id, "retrieval_agent")
               for turn in state.turns):
            raise ValidationError("Checkpoint turns disagree with retrieval identity.")
        ledger = ToolExecutionLedger(state.tool_ledger)
        if any((record.run_id, record.task_id, record.agent_id) != (state.run_id, task_id, "retrieval_agent")
               for record in ledger.to_list()):
            raise ValidationError("Checkpoint tools disagree with retrieval identity.")
        decision = decide_model_resume(state, ledger=ledger)
        if decision.decision not in {"safe_to_invoke_model", "reuse_durable_model_response", "resume_from_tool_result"}:
            # This is a rejected resume attempt, not a terminal rewrite of the
            # durable run. An external verification workflow remains separate.
            return RunStatus(
                run_id=state.run_id, thread_id=thread_id, status="failed",
                current_step=state.current_step,
                error=ErrorEnvelope(
                    code="MODEL_RESUME_REQUIRES_VERIFICATION", category="runtime",
                    message="Durable state requires verification before resume.",
                    details={"decision": decision.decision, "reason_code": decision.reason_code},
                ).to_dict(),
            )

        try:
            adapter = RuntimeAgentAdapter(self.vault_root, self.index_path)
            adapter.reader.read_notes()
        except (LoaderError, OSError):
            raise StaleSourceError("The source no longer matches the durable run.") from None
        tracer = OptionalTracer(self.trace_dir, state.run_id, thread_id, load_existing=True)
        cursor = {"state": state}

        def persist(current: RuntimeState) -> None:
            if not isinstance(current, RuntimeState) or (current.run_id, current.thread_id) != (state.run_id, thread_id):
                raise ValidationError("Resume checkpoint identity does not match the active run.")
            self._save_state(current)
            cursor["state"] = current

        result = adapter.run(
            run_id=state.run_id, workflow=state.workflow, query=request.query,
            source_context=state.source.to_dict(), tracer=tracer,
            # The model-step budget is already fully consumed by a durable
            # terminal response in this resume path.  Coordinator still needs
            # one projection step for Reviewer/final state publication; this
            # does not authorize another model invocation.
            # Coordinator's existing boundary check is strict (it rejects
            # total_steps >= max_total_steps), so reserve one additional
            # slot for that final check after the Reviewer projection.
            max_total_steps=min(12, state.policy.max_steps) + 2,
            tool_ledger=ledger, model=self.model, initial_state=state,
            artifact_store=ModelArtifactStore(self.checkpoint_dir / "models"),
            state_checkpoint_callback=persist, resume_model_loop=True,
            retrieval_task_id=task_id,
        )
        result["memory_refs"] = list(state.memory_refs)
        return self._finish_multi_agent(cursor["state"], result, tracer)

    def inspect(self, thread_id: str) -> RunStatus:
        """Inspect the latest state status summary of a thread."""
        latest_state = self.checkpointer.get_latest(thread_id)
        if latest_state is None:
            raise ThreadNotFoundError(f"Thread '{thread_id}' not found.")
        
        # Get checkpoint ID of the latest
        history = self.checkpointer.list_checkpoints(thread_id)
        checkpoint_id = history[-1]["checkpoint_id"] if history else None
        return self._build_status(latest_state, checkpoint_id)

    def resume(self, thread_id: str, interrupt_id: str, response: str) -> RunStatus:
        """Resume execution from a paused state after validating user inputs."""
        latest_state = self.checkpointer.get_latest(thread_id)
        if latest_state is None:
            raise ThreadNotFoundError(f"Thread '{thread_id}' not found.")

        if latest_state.status != "paused":
            raise StateTransitionError(f"Cannot resume thread '{thread_id}' with status '{latest_state.status}'.")

        if not latest_state.pending_interrupt or latest_state.pending_interrupt.interrupt_id != interrupt_id:
            raise InterruptNotFoundError(f"Interrupt '{interrupt_id}' not active for thread '{thread_id}'.")

        # P2 strictly accepts resume or reject responses
        if response not in ("resume", "reject"):
            raise InterruptResponseInvalidError(f"Response must be 'resume' or 'reject', got '{response}'.")

        tracer = OptionalTracer(self.trace_dir, latest_state.run_id, thread_id, load_existing=True)

        if response == "reject":
            tracer.emit("interrupt.resumed", "ok", attributes={"response": "reject"})
            # Transition to rejected
            error_env = ErrorEnvelope(
                code="USER_REJECTED",
                category="runtime",
                message="Execution rejected by the user.",
            )
            state = RuntimeState.from_dict({
                **latest_state.to_dict(),
                "status": "rejected",
                "pending_interrupt": None,
                "error": error_env.to_dict(),
                "step_seq": latest_state.step_seq + 1,
            })
            try:
                cid = self._save_state(state)
            except CheckpointWriteError as e:
                tracer.emit("checkpoint.failed", "failed", error=e)
                tracer.write_manifest(complete=False, source_index_sha256=latest_state.source.index_sha256, incomplete_reason="Checkpoint failed")
                raise
                
            tracer.emit("policy.rejected", "rejected", error=error_env)
            tracer.emit("run.failed", "failed", error=error_env) # Using failed since rejected is for status
            tracer.write_manifest(complete=True, source_index_sha256=latest_state.source.index_sha256)
            return self._build_status(state, cid)

        # response is "resume". Perform source stale check first.
        tracer.emit("interrupt.resumed", "ok")
        try:
            current_sha = self._calculate_index_sha256()
        except Exception as e:
            error_env = ErrorEnvelope(code="SOURCE_NOT_FOUND", category="path", message=str(e))
            state = RuntimeState.from_dict({
                **latest_state.to_dict(),
                "status": "failed",
                "pending_interrupt": None,
                "error": error_env.to_dict(),
                "step_seq": latest_state.step_seq + 1,
            })
            cid = self._save_state(state)
            tracer.emit("run.failed", "failed", error=error_env)
            tracer.write_manifest(complete=False, source_index_sha256=latest_state.source.index_sha256, incomplete_reason="Source not found")
            return self._build_status(state, cid)

        if current_sha != latest_state.source.index_sha256:
            error_env = ErrorEnvelope(
                code="CONTENT_CHANGED",
                category="runtime",
                message="Index file has been modified. State is now stale.",
            )
            state = RuntimeState.from_dict({
                **latest_state.to_dict(),
                "status": "stale",
                "pending_interrupt": None,
                "error": error_env.to_dict(),
                "step_seq": latest_state.step_seq + 1,
            })
            cid = self._save_state(state)
            tracer.emit("run.stale", "stale", error=error_env)
            tracer.write_manifest(complete=False, source_index_sha256=current_sha, incomplete_reason="State is now stale")
            return self._build_status(state, cid)

        # Transition to running
        state = RuntimeState.from_dict({
            **latest_state.to_dict(),
            "status": "running",
            "pending_interrupt": None,
            "current_step": latest_state.current_step, # Retain current_step from checkpoint
            "step_seq": latest_state.step_seq + 1,
        })
        cid = self._save_state(state)
        tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": cid})

        return self._execute(state, cid, tracer)

    def retry(self, run_id: str, failed_attempt_id: str | None = None) -> RunStatus:
        """Explicitly retry a failed run preserving state lineage."""
        # Find the latest state matching run_id across all threads
        thread_id = None
        if isinstance(self.checkpointer, SQLiteCheckpointer):
            import sqlite3
            if self.checkpointer.db_path.exists():
                conn = sqlite3.connect(self.checkpointer.db_path)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT thread_id FROM checkpoints WHERE run_id = ? LIMIT 1", (run_id,))
                    row = cur.fetchone()
                    if row:
                        thread_id = row[0]
                finally:
                    conn.close()
        elif isinstance(self.checkpointer, InMemoryCheckpointer):
            for tid, history in self.checkpointer._threads.items():
                if any(h["run_id"] == run_id for h in history):
                    thread_id = tid
                    break

        if thread_id is None:
            raise ThreadNotFoundError(f"Run '{run_id}' not found.")

        latest_state = self.checkpointer.get_latest(thread_id)
        if latest_state is None:
            raise ThreadNotFoundError(f"Thread '{thread_id}' not found.")

        if latest_state.status != "failed":
            raise StateTransitionError(f"Cannot retry run '{run_id}' which has status '{latest_state.status}'.")
        if failed_attempt_id is not None and not any(
            attempt.attempt_id == failed_attempt_id and attempt.status == "failed"
            for attempt in latest_state.attempts
        ):
            raise ValidationError(
                f"Failed attempt '{failed_attempt_id}' is not present in run '{run_id}'."
            )

        retry_run_id = f"run_p2_{uuid.uuid4().hex[:12]}"
        
        old_tracer = OptionalTracer(self.trace_dir, latest_state.run_id, thread_id, load_existing=True)
        old_tracer.emit("retry.scheduled", "ok", attributes={"retry_run_id": retry_run_id})
        old_tracer.write_manifest(complete=False, source_index_sha256=latest_state.source.index_sha256, incomplete_reason="retry scheduled")
        
        tracer = OptionalTracer(self.trace_dir, retry_run_id, thread_id)
        tracer.emit("run.accepted", "ok", attributes={"parent_run_id": run_id})
        tracer.emit("run.started", "started")

        # Retry creates a new run in the same thread and preserves lineage;
        # result artifacts from the failed run are never overwritten.
        state = RuntimeState.from_dict({
            **latest_state.to_dict(),
            "run_id": retry_run_id,
            "status": "running",
            "error": None,
            "current_step": latest_state.current_step or "resolve_source",
            "step_seq": latest_state.step_seq + 1,
            "parent_run_id": run_id,
        })
        try:
            cid = self._save_state(state)
            tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": cid})
        except CheckpointWriteError as e:
            tracer.emit("checkpoint.failed", "failed", error=e)
            tracer.write_manifest(complete=False, source_index_sha256=latest_state.source.index_sha256, incomplete_reason="Checkpoint failed")
            raise
        return self._execute(state, cid, tracer)

    def _append_attempt(
        self,
        state: RuntimeState,
        node_name: str,
        status: str,
        *,
        error_code: str | None = None,
        output_ref: str | None = None,
    ) -> RuntimeState:
        """Append a JSON-safe attempt record without replacing prior lineage."""
        input_payload = {
            "request_ref": state.request_ref,
            "workflow": state.workflow,
            "node_name": node_name,
            "step_seq": state.step_seq,
            "evidence_refs": state.evidence_refs,
        }
        input_sha256 = hashlib.sha256(
            json.dumps(input_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        retry_index = sum(1 for attempt in state.attempts if attempt.node_name == node_name)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        attempt = AttemptRecord(
            attempt_id=f"attempt_p2_{uuid.uuid4().hex[:12]}",
            node_name=node_name,
            step_seq=state.step_seq,
            input_sha256=input_sha256,
            idempotency_key=f"{state.run_id}:{state.step_seq}:{node_name}:{input_sha256}",
            status=status,
            retry_index=retry_index,
            output_ref=output_ref,
            error_code=error_code,
            started_at=now,
            finished_at=now,
        )
        data = state.to_dict()
        data["attempts"] = [*data.get("attempts", []), attempt.to_dict()]
        return RuntimeState.from_dict(data)

    def _execute(self, state: RuntimeState, checkpoint_id: str, tracer: OptionalTracer) -> RunStatus:
        """Internal execution loop stepping through logical graph nodes."""
        current_cid = checkpoint_id

        try:
            while state.status == "running":
                step = state.current_step

                if state.step_seq > state.policy.max_steps:
                    error_env = ErrorEnvelope(
                        code="BUDGET_EXCEEDED",
                        category="budget",
                        message=(
                            f"Execution step limit exceeded: {state.step_seq} > "
                            f"{state.policy.max_steps}."
                        ),
                    )
                    state = RuntimeState.from_dict({
                        **state.to_dict(),
                        "status": "failed",
                        "error": error_env.to_dict(),
                        "step_seq": state.step_seq + 1,
                    })
                    state = self._append_attempt(
                        state, step or "unknown", "failed", error_code=error_env.code
                    )
                    current_cid = self._save_state(state)
                    tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": current_cid})
                    tracer.emit("run.failed", "failed", error=error_env)
                    tracer.write_manifest(complete=False, source_index_sha256=state.source.index_sha256, incomplete_reason="budget exceeded")
                    return self._build_status(state, current_cid)

                tracer.emit("step.started", "started", node_name=step)
                step_start_time = time.time()

                # 1. Failure Injection
                if self.fail_at == step:
                    if not self.fail_once or not self._injected_failed:
                        self._injected_failed = True
                        error_env = ErrorEnvelope(
                            code="INJECTED_FAILURE",
                            category="runtime",
                            message=f"Injected execution failure at step {step}.",
                        )
                        state = RuntimeState.from_dict({
                            **state.to_dict(),
                            "status": "failed",
                            "error": error_env.to_dict(),
                            "step_seq": state.step_seq + 1,
                        })
                        state = self._append_attempt(
                            state, step, "failed", error_code=error_env.code
                        )
                        try:
                            current_cid = self._save_state(state)
                            tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": current_cid})
                        except CheckpointWriteError as e:
                            tracer.emit("checkpoint.failed", "failed", error=e)
                            pass
                        tracer.emit("step.failed", "failed", node_name=step, error=error_env, duration_ms=0)
                        tracer.emit("run.failed", "failed", error=error_env)
                        tracer.write_manifest(complete=False, source_index_sha256=state.source.index_sha256, incomplete_reason="injected failure")
                        return self._build_status(state, current_cid)

                # Node routing logic
                if step == "resolve_source":
                    state, current_cid = self._node_resolve_source(state, tracer)
                elif step == "retrieve_context":
                    state, current_cid = self._node_retrieve_context(state, tracer)
                elif step == "emit_result":
                    state, current_cid = self._node_emit_result(state, tracer)
                else:
                    break

                duration_ms = int((time.time() - step_start_time) * 1000)

                if state.status in ("failed", "stale"):
                    error_code = state.error.code if state.error else None
                    state = self._append_attempt(
                        state, step, "failed", error_code=error_code
                    )
                    tracer.emit("step.failed", "failed", node_name=step, error=state.error, duration_ms=duration_ms)
                elif state.status == "paused":
                    state = self._append_attempt(
                        state, step, "completed", output_ref=state.result_ref
                    )
                    tracer.emit("step.completed", "paused", node_name=step, duration_ms=duration_ms)
                else:
                    state = self._append_attempt(
                        state, step, "completed", output_ref=state.result_ref
                    )
                    tracer.emit("step.completed", "ok", node_name=step, duration_ms=duration_ms)

                current_cid = self._save_state(state)
                tracer.emit("checkpoint.saved", "ok", attributes={"checkpoint_id": current_cid})

                if state.status in ("failed", "stale", "paused", "completed"):
                    if state.status == "failed":
                        tracer.emit("run.failed", "failed", error=state.error)
                        tracer.write_manifest(complete=False, source_index_sha256=state.source.index_sha256, incomplete_reason="run failed")
                    elif state.status == "stale":
                        tracer.emit("run.stale", "stale", error=state.error)
                        tracer.write_manifest(complete=False, source_index_sha256=state.source.index_sha256, incomplete_reason="stale source")
                    elif state.status == "completed":
                        tracer.emit("run.completed", "ok", attributes={"result_ref": state.result_ref})
                        tracer.write_manifest(complete=True, source_index_sha256=state.source.index_sha256)
                    elif state.status == "paused":
                        tracer.write_manifest(complete=False, source_index_sha256=state.source.index_sha256, incomplete_reason="paused")
                    return self._build_status(state, current_cid)
        except CheckpointWriteError as e:
            # Catch checkpoint save errors during execution loop and report failed
            err_dict = e.to_envelope()
            failed_state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err_dict,
                "current_step": state.current_step,
            })
            tracer.emit("checkpoint.failed", "failed", error=e)
            tracer.emit("run.failed", "failed", error=err_dict)
            tracer.write_manifest(complete=False, source_index_sha256=state.source.index_sha256, incomplete_reason="Checkpoint failed")
            return self._build_status(failed_state, None)

        return self._build_status(state, current_cid)

    def _node_resolve_source(self, state: RuntimeState, tracer: OptionalTracer) -> tuple[RuntimeState, str]:
        """Node resolve_source: verifies index file hash and root directories."""
        try:
            sha = self._calculate_index_sha256()
        except Exception as e:
            err = ErrorEnvelope(code="SOURCE_NOT_FOUND", category="path", message=str(e))
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        # Initialize VaultReader to read and populate SourceContext details
        tracer.emit("tool.called", "started", node_name="resolve_source", attributes={"tool": "reader"})
        try:
            reader = VaultReader(vault_root=self.vault_root, index_path=self.index_path)
            # Just read notes structure without editing
            notes = reader.read_notes()
            doc_count = len(notes)
            tracer.emit("tool.completed", "ok", node_name="resolve_source", attributes={"tool": "reader", "document_count": doc_count})
        except Exception as e:
            err = ErrorEnvelope(code="SCHEMA_VALIDATION_ERROR", category="schema", message=str(e))
            tracer.emit("tool.failed", "failed", node_name="resolve_source", attributes={"tool": "reader"}, error=err)
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        source_context = SourceContext(
            index_path=state.source.index_path,
            index_sha256=sha,
            index_schema_version=1,
            vault_root_fingerprint=state.source.vault_root_fingerprint,
            fixture_id=state.source.fixture_id,
            document_count=doc_count,
        )

        state = RuntimeState.from_dict({
            **state.to_dict(),
            "source": source_context.to_dict(),
            "current_step": "retrieve_context",
            "step_seq": state.step_seq + 1,
        })
        return state, self._save_state(state)

    def _node_retrieve_context(self, state: RuntimeState, tracer: OptionalTracer) -> tuple[RuntimeState, str]:
        """Node retrieve_context: performs read-only context retrieval and handles pause policy."""
        tracer.emit("tool.called", "started", node_name="retrieve_context", attributes={"tool": "retrieval"})
        try:
            reader = VaultReader(vault_root=self.vault_root, index_path=self.index_path)
            documents = reader.read_notes()
        except Exception as e:
            err = ErrorEnvelope(code="SCHEMA_VALIDATION_ERROR", category="schema", message=str(e))
            tracer.emit("tool.failed", "failed", node_name="retrieve_context", attributes={"tool": "retrieval"}, error=err)
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        # Recover request query
        req_ref = state.request_ref
        req_path = self.checkpoint_dir / req_ref
        try:
            req_data = json.loads(req_path.read_text(encoding="utf-8"))
            query = req_data.get("query", "")
        except Exception as e:
            err = ErrorEnvelope(code="RUNTIME_ERROR", category="runtime", message=f"Failed to read request payload: {e}")
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        evidence_dicts = []
        try:
            if state.workflow == "ask":
                evidence_refs = retrieve_evidence(query=query, documents=documents, max_results=5)
                evidence_dicts = [ev.to_dict() for ev in evidence_refs]
            elif state.workflow == "connect":
                _, evidence_refs = find_relation_candidates_with_evidence(
                    documents=documents, query=query, max_candidates=10
                )
                evidence_dicts = [ev.to_dict() for ev in evidence_refs]
        except Exception as e:
            err = ErrorEnvelope(code="RUNTIME_ERROR", category="runtime", message=f"Retrieval failed: {e}")
            tracer.emit("tool.failed", "failed", node_name="retrieve_context", attributes={"tool": "retrieval"}, error=err)
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        tracer.emit(
            "tool.completed",
            "ok",
            node_name="retrieve_context",
            attributes={"tool": "retrieval", "evidence_count": len(evidence_dicts)},
        )

        # Extract only refs (ID + note metadata)
        state = RuntimeState.from_dict({
            **state.to_dict(),
            # Checkpoints carry evidence identifiers only. Full quotes remain
            # in the immutable result artifact produced by emit_result.
            "evidence_refs": [ev["evidence_id"] for ev in evidence_dicts],
            "current_step": "emit_result",
            "step_seq": state.step_seq + 1,
        })

        # Save checkpoint before pause decision
        cid = self._save_state(state)

        if self.pause_after == "retrieve_context":
            # Transition to paused state
            interrupt = InterruptEnvelope(
                interrupt_id=f"int_p2_{uuid.uuid4().hex[:12]}",
                kind="clarification_required",
                message="retrieve_context complete. Awaiting human confirmation.",
                allowed_responses=["resume", "reject"],
                checkpoint_id=cid,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "paused",
                "pending_interrupt": interrupt.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            cid = self._save_state(state)
            tracer.emit("interrupt.raised", "paused", attributes={"interrupt_id": interrupt.interrupt_id})

        return state, cid

    def _node_emit_result(self, state: RuntimeState, tracer: OptionalTracer) -> tuple[RuntimeState, str]:
        """Node emit_result: calls P1 services to produce final result files."""
        # Double check source index integrity before final response emission
        try:
            sha = self._calculate_index_sha256()
            if sha != state.source.index_sha256:
                raise StaleSourceError("Index hash changed during run execution.")
        except Exception as e:
            err = ErrorEnvelope(code="CONTENT_CHANGED", category="runtime", message=str(e))
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "stale",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        req_ref = state.request_ref
        req_path = self.checkpoint_dir / req_ref
        try:
            req_data = json.loads(req_path.read_text(encoding="utf-8"))
            query = req_data.get("query", "")
        except Exception as e:
            err = ErrorEnvelope(code="RUNTIME_ERROR", category="runtime", message=f"Failed to read request: {e}")
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        # Invoke original services to generate outputs in a run-specific,
        # immutable artifact directory. Never write under the Vault root.
        artifact_dir = self.checkpoint_dir / "results" / state.run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        result_envelope = None
        service_name = "ask_service" if state.workflow == "ask" else "connect_service"
        tracer.emit("tool.called", "started", node_name="emit_result", attributes={"tool": service_name})
        try:
            if state.workflow == "ask":
                result_envelope = AskService.run(
                    vault_root=self.vault_root,
                    index_path=self.index_path,
                    query=query,
                    output_dir=artifact_dir,
                    request_id=req_data.get("request_id", "req_p2_0001"),
                    run_id=state.run_id,
                )
            elif state.workflow == "connect":
                result_envelope = ConnectService.run(
                    vault_root=self.vault_root,
                    index_path=self.index_path,
                    query=query,
                    output_dir=artifact_dir,
                    request_id=req_data.get("request_id", "req_p2_0002"),
                    run_id=state.run_id,
                )
        except Exception as e:
            err = ErrorEnvelope(code="RUNTIME_ERROR", category="runtime", message=f"Service run execution failed: {e}")
            tracer.emit("tool.failed", "failed", node_name="emit_result", attributes={"tool": service_name}, error=err)
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        if not result_envelope:
            err = ErrorEnvelope(code="RUNTIME_ERROR", category="runtime", message="Service execution returned no result envelope.")
            tracer.emit("tool.failed", "failed", node_name="emit_result", attributes={"tool": service_name}, error=err)
            state = RuntimeState.from_dict({
                **state.to_dict(),
                "status": "failed",
                "error": err.to_dict(),
                "step_seq": state.step_seq + 1,
            })
            return state, self._save_state(state)

        tracer.emit("tool.completed", "ok", node_name="emit_result", attributes={"tool": service_name})

        # Checkpoint results
        state = RuntimeState.from_dict({
            **state.to_dict(),
            "status": "completed",
            "result_ref": f"results/{state.run_id}/result.json",
            "current_step": None,
            "step_seq": state.step_seq + 1,
        })
        tracer.emit(
            "artifact.written",
            "ok",
            node_name="emit_result",
            output_ref={"kind": "artifact", "path": f"results/{state.run_id}/result.json"},
        )
        return state, self._save_state(state)
