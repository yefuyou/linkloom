"""Model-driven retrieval delegated to the accepted durable model loop."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable

from linkloom.agents.base import AgentResult, AgentTask
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.model_loop import ModelLoopResult, SingleAgentModelLoop
from linkloom.runtime.models import RuntimeState, ToolExecutionRecord
from linkloom.tools.contracts import ToolError
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolPolicyEnforcer


StateCheckpointCallback = Callable[[RuntimeState], None]


class RetrievalAgent:
    """Project one durable model trajectory into the existing AgentResult."""

    def __init__(
        self,
        agent_id: str = "retrieval_agent",
        *,
        model: Any | None = None,
        initial_state: RuntimeState | None = None,
        artifact_store: ModelArtifactStore | None = None,
        state_checkpoint_callback: StateCheckpointCallback | None = None,
        resume_model_loop: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self.model = model
        self.initial_state = initial_state
        self.artifact_store = artifact_store
        self.state_checkpoint_callback = state_checkpoint_callback
        self.resume_model_loop = resume_model_loop

    @staticmethod
    def _safe_error(
        code: str,
        category: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ToolError(
            code=code,
            category=category,
            message=message,
            retryable=False,
            details=details or {},
            safe_to_expose=True,
        ).to_dict()

    def _failed_result(
        self,
        task: AgentTask,
        usage: dict[str, int],
        error: dict[str, Any],
        warnings: list[str],
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_id=self.agent_id,
            status="failed",
            output_type="evidence_bundle",
            output_refs=[],
            summary="Failed to retrieve evidence",
            confidence=None,
            handoff=None,
            warnings=warnings,
            usage=usage,
            error=error,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _retrieval_instruction(query: str, source_context: dict[str, Any]) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("Retrieval query must be non-empty text.")
        if not isinstance(source_context, dict):
            raise ValidationError("Retrieval source_context must be an object.")
        try:
            serialized = json.dumps(
                {"query": query, "source_context": source_context},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValidationError("Retrieval request is not JSON serializable.") from error
        if len(serialized.encode("utf-8")) > 16 * 1024:
            raise ValidationError("Retrieval request exceeds the bounded instruction size.")
        return (
            "Retrieve verified evidence for the request below. Choose only from the "
            "available retrieval tools, pass business arguments exactly as needed, "
            "and return Final when the evidence is sufficient.\n"
            f"retrieval_request={serialized}"
        )

    @staticmethod
    def _available_tools(task: AgentTask, tool_runtime: ToolRuntime) -> list[Any]:
        definitions = []
        for tool_id in task.allowed_tool_ids:
            definition, _ = tool_runtime.registry.resolve(tool_id)
            definitions.append(definition)
        return definitions

    @staticmethod
    def _scoped_records(
        state: RuntimeState,
        task: AgentTask,
        agent_id: str,
    ) -> list[ToolExecutionRecord]:
        records = [
            record
            for record in state.tool_ledger
            if record.run_id == task.run_id
            and record.task_id == task.task_id
            and record.agent_id == agent_id
        ]
        return sorted(records, key=lambda record: (record.sequence, record.call_id))

    @staticmethod
    def _has_durable_final(
        state: RuntimeState,
        task: AgentTask,
        agent_id: str,
    ) -> bool:
        """Identify a terminal response the durable loop may consume locally.

        This is only a budget-preflight exception.  The model loop remains the
        authority that validates and consumes the durable response during
        ``resume``.
        """
        records = sorted(
            (
                record
                for record in state.model_executions
                if record.run_id == task.run_id
                and record.task_id == task.task_id
                and record.agent_id == agent_id
            ),
            key=lambda record: (record.sequence, record.turn_id),
        )
        if not records:
            return False
        latest = records[-1]
        action = latest.normalized_action
        return (
            latest.status == "response_durable"
            and isinstance(action, dict)
            and action.get("kind") == "final"
        )

    @staticmethod
    def _append_warning(warnings: list[str], tool_id: str, code: str) -> None:
        warning = f"{tool_id}:{code}"
        if warning not in warnings:
            warnings.append(warning)

    @staticmethod
    def _record_error(record: ToolExecutionRecord) -> dict[str, Any] | None:
        if not isinstance(record.error, dict):
            return None
        try:
            return ToolError.from_dict(record.error).to_dict()
        except ValidationError:
            return {
                "code": "TOOL_ERROR_MALFORMED",
                "category": "runtime",
                "message": "A tool failure was recorded with an invalid safe error envelope.",
                "retryable": False,
                "affected_refs": [],
                "details": {"tool_id": record.tool_id},
                "safe_to_expose": True,
            }

    @staticmethod
    def _successful_value(record: ToolExecutionRecord) -> Any:
        if record.status != "completed" or not isinstance(record.result, dict):
            return None
        if record.result.get("status") != "ok":
            return None
        return record.result.get("value")

    @staticmethod
    def _refs_from_value(value: Any) -> list[str]:
        items = value if isinstance(value, list) else [value]
        refs: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("evidence_id") or item.get("ref") or item.get("note_ref")
            if isinstance(ref, str) and ref and ref not in refs:
                refs.append(ref)
        return refs

    def _project_result(
        self,
        task: AgentTask,
        loop_result: ModelLoopResult,
        usage: dict[str, int],
    ) -> AgentResult:
        state = loop_result.state
        records = self._scoped_records(state, task, self.agent_id)
        warnings: list[str] = []
        failed_errors: list[dict[str, Any]] = []

        for record in records:
            if record.status != "failed":
                continue
            error = self._record_error(record)
            if error is None:
                continue
            failed_errors.append(error)
            self._append_warning(warnings, record.tool_id, error["code"])

        last_observation = loop_result.last_observation
        if last_observation is not None and last_observation.error is not None:
            observation_error = last_observation.error.to_dict()
            self._append_warning(
                warnings,
                last_observation.tool_id,
                observation_error["code"],
            )
        else:
            observation_error = None

        successful_reads = [
            record
            for record in records
            if record.tool_id == "read_verified_note"
            and self._successful_value(record) is not None
        ]
        read_refs: list[str] = []
        for record in successful_reads:
            for ref in self._refs_from_value(self._successful_value(record)):
                if ref not in read_refs:
                    read_refs.append(ref)

        successful_searches = [
            record
            for record in records
            if record.tool_id == "search_notes"
            and record.status == "completed"
            and isinstance(self._successful_value(record), list)
        ]
        latest_search_value = (
            self._successful_value(successful_searches[-1])
            if successful_searches
            else None
        )
        search_refs = self._refs_from_value(latest_search_value)
        reached_final = (
            loop_result.status == "completed"
            and state.termination is not None
            and state.termination.reason_code == "model_final"
        )

        if reached_final and read_refs:
            return AgentResult(
                task_id=task.task_id,
                agent_id=self.agent_id,
                status="completed",
                output_type="evidence_bundle",
                output_refs=read_refs,
                summary=f"Found {len(read_refs)} verified evidence item(s)",
                confidence=None,
                handoff=None,
                warnings=warnings,
                usage=usage,
                error=None,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if (
            reached_final
            and successful_searches
            and latest_search_value == []
            and not failed_errors
        ):
            return AgentResult(
                task_id=task.task_id,
                agent_id=self.agent_id,
                status="completed",
                output_type="evidence_bundle",
                output_refs=[],
                summary="No matching notes",
                confidence=None,
                handoff={"status": "not_required", "reason_code": "no_matching_notes"},
                warnings=warnings,
                usage=usage,
                error=None,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if reached_final and successful_searches and search_refs and not failed_errors:
            return AgentResult(
                task_id=task.task_id,
                agent_id=self.agent_id,
                status="completed",
                output_type="evidence_bundle",
                output_refs=search_refs,
                summary=f"Found {len(search_refs)} verified evidence item(s)",
                confidence=None,
                handoff=None,
                warnings=warnings,
                usage=usage,
                error=None,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        error = (
            failed_errors[-1]
            if failed_errors
            else observation_error
            or (
                loop_result.error.to_dict()
                if loop_result.error is not None
                else self._safe_error(
                    "NO_VERIFIED_EVIDENCE",
                    "runtime",
                    "No verified evidence was available.",
                    {"reason": "model_final_without_verified_evidence"},
                )
            )
        )
        return self._failed_result(task, usage, error, warnings)

    def execute(
        self,
        task: AgentTask,
        query: str,
        source_context: dict[str, Any],
        policy_enforcer: ToolPolicyEnforcer,
        tool_runtime: ToolRuntime,
        event_sink: Any = None,
    ) -> AgentResult:
        """Run the accepted durable model loop once and project its state."""
        usage = {"steps": 0, "tool_calls": 0, "provider_requests": 0}

        try:
            if task.agent_id != self.agent_id:
                raise ValidationError("Retrieval task identity does not match the agent.")
            if not callable(getattr(self.model, "decide", None)) and not callable(
                getattr(self.model, "complete", None)
            ):
                return self._failed_result(
                    task,
                    usage,
                    self._safe_error(
                        "MODEL_NOT_CONFIGURED",
                        "provider",
                        "Retrieval requires an injected model.",
                    ),
                    [],
                )
            if not isinstance(self.initial_state, RuntimeState):
                raise ValidationError("Retrieval requires a RuntimeState.")
            if self.initial_state.run_id != task.run_id:
                raise ValidationError("Retrieval state identity does not match the task run.")
            if not isinstance(self.artifact_store, ModelArtifactStore):
                raise ValidationError("Retrieval requires a ModelArtifactStore.")
            if not callable(self.state_checkpoint_callback):
                raise ValidationError("Retrieval requires a RuntimeState checkpoint callback.")

            available_tools = self._available_tools(task, tool_runtime)
            instruction = self._retrieval_instruction(query, source_context)
            consumed_provider_requests = max(
                self.initial_state.usage.provider_requests,
                len(self.initial_state.model_executions),
            )
            remaining_provider_requests = (
                self.initial_state.policy.max_provider_requests
                - consumed_provider_requests
            )
            durable_final_reuse = (
                self.resume_model_loop
                and self._has_durable_final(self.initial_state, task, self.agent_id)
            )
            if remaining_provider_requests <= 0 and not durable_final_reuse:
                return self._failed_result(
                    task,
                    usage,
                    self._safe_error(
                        "MODEL_PROVIDER_BUDGET_EXHAUSTED",
                        "budget",
                        "The retrieval model request budget is exhausted.",
                    ),
                    [],
                )
            max_steps = min(
                task.max_steps,
                self.initial_state.policy.max_steps,
                remaining_provider_requests,
            )
            if self.resume_model_loop:
                spent = [record for record in self.initial_state.model_executions
                         if record.status != "request_durable"]
                task_spent = sum(
                    (record.run_id, record.task_id, record.agent_id)
                    == (task.run_id, task.task_id, self.agent_id) for record in spent
                )
                max_steps = min(max_steps, task.max_steps - task_spent,
                                self.initial_state.policy.max_steps - len(spent))
                if max_steps <= 0 and not durable_final_reuse:
                    return self._failed_result(
                        task, usage,
                        self._safe_error("MODEL_MAX_STEPS_EXCEEDED", "budget",
                                         "The retrieval model step budget is exhausted."), [],
                    )
            if durable_final_reuse:
                # This is one local loop-consumption slot, not a new provider
                # request or a reset of either execution budget.
                max_steps = max(max_steps, 1)
            if max_steps <= 0:
                return self._failed_result(
                    task,
                    usage,
                    self._safe_error(
                        "MODEL_PROVIDER_BUDGET_EXHAUSTED",
                        "budget",
                        "The retrieval model request budget is exhausted.",
                    ),
                    [],
                )

            loop = SingleAgentModelLoop(
                self.model,
                tool_runtime,
                policy_enforcer,
                max_steps=max_steps,
            )
            invoke_loop = loop.resume if self.resume_model_loop else loop.run
            loop_result = invoke_loop(
                state=self.initial_state,
                task_id=task.task_id,
                agent_id=self.agent_id,
                user_input=instruction,
                available_tools=available_tools,
                event_sink=event_sink,
                checkpoint_callback=self.state_checkpoint_callback,
                artifact_store=self.artifact_store,
            )
            scoped_model_records = [
                record
                for record in loop_result.state.model_executions
                if record.run_id == task.run_id
                and record.task_id == task.task_id
                and record.agent_id == self.agent_id
            ]
            scoped_tool_records = self._scoped_records(loop_result.state, task, self.agent_id)
            usage = {
                "steps": len(scoped_model_records),
                "tool_calls": len(scoped_tool_records),
                "provider_requests": len(scoped_model_records),
            }
            return self._project_result(task, loop_result, usage)
        except ValidationError as error:
            return self._failed_result(
                task,
                usage,
                self._safe_error(
                    "RETRIEVAL_CONTRACT_ERROR",
                    "runtime",
                    "Retrieval could not satisfy its runtime contract.",
                    {"reason": error.details.get("reason", "validation")},
                ),
                [],
            )
        except Exception:
            return self._failed_result(
                task,
                usage,
                self._safe_error(
                    "RETRIEVAL_ERROR",
                    "runtime",
                    "Retrieval workflow failed.",
                ),
                [],
            )
