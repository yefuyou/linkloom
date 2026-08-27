"""Independent single-agent model-loop harness for P8.3.

This is deliberately not the production RetrievalAgent path.  It proves the
smallest runtime-owned loop around the existing ToolRuntime and ledger using a
local FakeModelAdapter only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from linkloom.agents.model_adapter import ModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    AgentTurn,
    ModelExecutionRecord,
    RuntimeState,
    TerminationState,
)
from linkloom.runtime.recovery import decide_model_resume
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolPolicyEnforcer


MODEL_LOOP_STATUSES = frozenset({"completed", "failed", "budget_exhausted"})


@dataclass(frozen=True)
class ModelLoopResult:
    """Safe result of one local model-loop harness execution."""

    status: str
    state: RuntimeState
    final_answer: str | None = None
    last_observation: ToolResult | None = None
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if self.status not in MODEL_LOOP_STATUSES:
            raise ValidationError(
                f"ModelLoopResult.status must be one of {sorted(MODEL_LOOP_STATUSES)}."
            )
        if not isinstance(self.state, RuntimeState):
            raise ValidationError("ModelLoopResult.state must be RuntimeState.")
        if self.final_answer is not None and not isinstance(self.final_answer, str):
            raise ValidationError("ModelLoopResult.final_answer must be text or None.")
        if self.last_observation is not None and not isinstance(self.last_observation, ToolResult):
            raise ValidationError("ModelLoopResult.last_observation must be ToolResult or None.")
        if self.error is not None and not isinstance(self.error, ToolError):
            raise ValidationError("ModelLoopResult.error must be ToolError or None.")
        if self.status == "completed":
            if not self.final_answer or self.error is not None:
                raise ValidationError("Completed ModelLoopResult requires a final answer and no error.")
        elif self.error is None:
            raise ValidationError("Non-completed ModelLoopResult requires an error.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "state": self.state.to_dict(),
            "final_answer": self.final_answer,
            "last_observation": (
                self.last_observation.to_dict() if self.last_observation else None
            ),
            "error": self.error.to_dict() if self.error else None,
        }


class SingleAgentModelLoop:
    """Runtime-owned loop: model proposes, runtime executes, result is observed."""

    def __init__(
        self,
        model: ModelAdapter,
        tool_runtime: ToolRuntime,
        policy_enforcer: ToolPolicyEnforcer,
        *,
        max_steps: int = 8,
    ) -> None:
        if not hasattr(model, "decide") or not callable(model.decide):
            raise ValidationError("SingleAgentModelLoop model must implement decide().")
        if not isinstance(tool_runtime, ToolRuntime):
            raise ValidationError("SingleAgentModelLoop tool_runtime must be ToolRuntime.")
        if not isinstance(policy_enforcer, ToolPolicyEnforcer):
            raise ValidationError(
                "SingleAgentModelLoop policy_enforcer must be ToolPolicyEnforcer."
            )
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
            raise ValidationError("SingleAgentModelLoop max_steps must be a positive integer.")
        self.model = model
        self.tool_runtime = tool_runtime
        self.policy_enforcer = policy_enforcer
        self.max_steps = max_steps

    @staticmethod
    def _project_state(
        state: RuntimeState,
        turns: list[AgentTurn],
        ledger: Any,
        termination: TerminationState,
        model_executions: list[ModelExecutionRecord] | None = None,
    ) -> RuntimeState:
        return RuntimeState.from_dict({
            **state.to_dict(),
            "current_step": "model_loop",
            "step_seq": state.step_seq + 1,
            "turns": [turn.to_dict() for turn in turns],
            "tool_ledger": [record.to_dict() for record in ledger.to_list()],
            "termination": termination.to_dict(),
            "model_executions": [
                record.to_dict()
                for record in (model_executions if model_executions is not None else state.model_executions)
            ],
        })

    @staticmethod
    def _error(code: str, category: str, message: str, details: dict[str, Any]) -> ToolError:
        return ToolError(
            code=code,
            category=category,
            message=message,
            retryable=False,
            details=details,
            safe_to_expose=True,
        )

    def run(
        self,
        *,
        state: RuntimeState,
        task_id: str,
        agent_id: str,
        user_input: str,
        available_tools: Sequence[ToolDefinition],
        event_sink: Any = None,
        checkpoint_callback: Callable[[RuntimeState], None] | None = None,
        artifact_store: ModelArtifactStore | None = None,
        resume: bool = False,
    ) -> ModelLoopResult:
        """Run until a model final action or the runtime max-step boundary."""
        if resume or artifact_store is not None:
            return self._run_durable(
                state=state,
                task_id=task_id,
                agent_id=agent_id,
                user_input=user_input,
                available_tools=available_tools,
                event_sink=event_sink,
                checkpoint_callback=checkpoint_callback,
                artifact_store=artifact_store,
                resume=resume,
            )
        if not isinstance(state, RuntimeState):
            raise ValidationError("SingleAgentModelLoop state must be RuntimeState.")
        if state.termination is not None and state.termination.status != "running":
            raise ValidationError("SingleAgentModelLoop requires a running termination state.")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValidationError("SingleAgentModelLoop task_id must be non-empty.")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValidationError("SingleAgentModelLoop agent_id must be non-empty.")
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValidationError("SingleAgentModelLoop user_input must be non-empty.")
        if not isinstance(available_tools, (list, tuple)):
            raise ValidationError("SingleAgentModelLoop available_tools must be a sequence.")
        tool_definitions = list(available_tools)
        if any(not isinstance(tool, ToolDefinition) for tool in tool_definitions):
            raise ValidationError("SingleAgentModelLoop available_tools must contain ToolDefinitions.")
        if checkpoint_callback is not None and not callable(checkpoint_callback):
            raise ValidationError("SingleAgentModelLoop checkpoint_callback must be callable.")

        current_state = state
        turns = list(state.turns)
        ledger = ToolExecutionLedger.from_list(state.tool_ledger)
        termination = state.termination or TerminationState(status="running", sequence=state.step_seq)
        observation: ToolResult | None = None

        def publish(projected: RuntimeState) -> None:
            nonlocal current_state
            current_state = projected
            if checkpoint_callback is not None:
                checkpoint_callback(projected)

        def persist_ledger(current_ledger: Any) -> None:
            publish(self._project_state(current_state, turns, current_ledger, termination))

        def failed_result(error: ToolError) -> ModelLoopResult:
            nonlocal current_state, termination
            if turns and turns[-1].status == "running":
                turns[-1] = replace(turns[-1], status="failed")
            termination = TerminationState(
                status="failed",
                reason_code="model_loop_failed",
                reason="The local model loop failed safely.",
                sequence=turns[-1].sequence if turns else state.step_seq,
            )
            projected = self._project_state(current_state, turns, ledger, termination)
            try:
                publish(projected)
            except Exception:
                # A failed checkpoint must not expose its backend exception.
                current_state = projected
            return ModelLoopResult(
                status="failed",
                state=current_state,
                last_observation=observation,
                error=error,
            )

        try:
            next_sequence = max((turn.sequence for turn in turns), default=0) + 1
            for _ in range(self.max_steps):
                turn_id = f"{state.run_id}:turn:{next_sequence}"
                turn = AgentTurn(
                    turn_id=turn_id,
                    run_id=state.run_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    sequence=next_sequence,
                    status="running",
                )
                turns.append(turn)
                publish(self._project_state(current_state, turns, ledger, termination))

                request = ModelTurnRequest(
                    run_id=state.run_id,
                    turn_id=turn_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    sequence=next_sequence,
                    user_input=user_input,
                    observation=observation,
                    available_tools=list(tool_definitions),
                )
                try:
                    action = self.model.decide(request)
                except Exception:
                    return failed_result(
                        self._error(
                            "MODEL_ADAPTER_FAILED",
                            "runtime",
                            "Model adapter failed to produce a safe action.",
                            {"turn_id": turn_id},
                        )
                    )
                if not isinstance(action, ModelAction):
                    return failed_result(
                        self._error(
                            "MODEL_ACTION_INVALID",
                            "schema",
                            "Model adapter returned an invalid action.",
                            {"turn_id": turn_id},
                        )
                    )

                if action.kind == "final":
                    turns[-1] = replace(turns[-1], status="completed")
                    termination = TerminationState(
                        status="completed",
                        reason_code="model_final",
                        reason="The model proposed a final answer.",
                        sequence=next_sequence,
                    )
                    publish(self._project_state(current_state, turns, ledger, termination))
                    return ModelLoopResult(
                        status="completed",
                        state=current_state,
                        final_answer=action.final_answer,
                        last_observation=observation,
                    )

                proposed = action.tool_call
                if proposed is None:
                    return failed_result(
                        self._error(
                            "MODEL_ACTION_INVALID",
                            "schema",
                            "Model tool action did not contain a ToolCall.",
                            {"turn_id": turn_id},
                        )
                    )
                call = ToolCall(
                    call_id=f"{state.run_id}:turn:{next_sequence}:tool",
                    tool_id=proposed.tool_id,
                    arguments=dict(proposed.arguments),
                    run_id=state.run_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    sequence=next_sequence,
                )
                turns[-1] = replace(turns[-1], tool_call_ids=[call.call_id])
                publish(self._project_state(current_state, turns, ledger, termination))
                result = self.tool_runtime.execute(
                    call,
                    self.policy_enforcer,
                    event_sink=event_sink,
                    ledger=ledger,
                    checkpoint_callback=persist_ledger,
                )
                observation = result
                turns[-1] = replace(
                    turns[-1],
                    status="completed" if result.status == "ok" else "failed",
                )
                publish(self._project_state(current_state, turns, ledger, termination))
                next_sequence += 1

            termination = TerminationState(
                status="budget_exhausted",
                reason_code="max_steps_exhausted",
                reason="The runtime reached its model-loop max_steps boundary.",
                sequence=next_sequence - 1,
            )
            publish(self._project_state(current_state, turns, ledger, termination))
            return ModelLoopResult(
                status="budget_exhausted",
                state=current_state,
                last_observation=observation,
                error=self._error(
                    "MODEL_MAX_STEPS_EXCEEDED",
                    "budget",
                    "The model loop reached its maximum step count.",
                    {"max_steps": self.max_steps},
                ),
            )
        except Exception:
            return failed_result(
                self._error(
                    "MODEL_LOOP_RUNTIME_FAILED",
                    "runtime",
                    "The model loop failed safely.",
                    {"run_id": state.run_id},
                )
            )

    def resume(
        self,
        *,
        state: RuntimeState,
        task_id: str,
        agent_id: str,
        user_input: str,
        available_tools: Sequence[ToolDefinition],
        artifact_store: ModelArtifactStore,
        event_sink: Any = None,
        checkpoint_callback: Callable[[RuntimeState], None] | None = None,
    ) -> ModelLoopResult:
        """Resume a durable loop without replaying ambiguous work."""
        return self.run(
            state=state,
            task_id=task_id,
            agent_id=agent_id,
            user_input=user_input,
            available_tools=available_tools,
            event_sink=event_sink,
            checkpoint_callback=checkpoint_callback,
            artifact_store=artifact_store,
            resume=True,
        )

    def _run_durable(
        self,
        *,
        state: RuntimeState,
        task_id: str,
        agent_id: str,
        user_input: str,
        available_tools: Sequence[ToolDefinition],
        event_sink: Any,
        checkpoint_callback: Callable[[RuntimeState], None] | None,
        artifact_store: ModelArtifactStore | None,
        resume: bool,
    ) -> ModelLoopResult:
        """Run the runtime-owned durable model lifecycle with the fake adapter."""
        if not isinstance(state, RuntimeState):
            raise ValidationError("SingleAgentModelLoop state must be RuntimeState.")
        if not isinstance(artifact_store, ModelArtifactStore):
            raise ValidationError("Durable model loop requires ModelArtifactStore.")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValidationError("SingleAgentModelLoop task_id must be non-empty.")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValidationError("SingleAgentModelLoop agent_id must be non-empty.")
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValidationError("SingleAgentModelLoop user_input must be non-empty.")
        if not isinstance(available_tools, (list, tuple)):
            raise ValidationError("SingleAgentModelLoop available_tools must be a sequence.")
        tool_definitions = list(available_tools)
        if any(not isinstance(tool, ToolDefinition) for tool in tool_definitions):
            raise ValidationError("SingleAgentModelLoop available_tools must contain ToolDefinitions.")
        if checkpoint_callback is not None and not callable(checkpoint_callback):
            raise ValidationError("SingleAgentModelLoop checkpoint_callback must be callable.")

        current_state = state
        turns = list(state.turns)
        ledger = ToolExecutionLedger.from_list(state.tool_ledger)
        self.policy_enforcer.rehydrate_from_ledger(
            ledger.to_list(),
            run_id=state.run_id,
            task_id=task_id,
            agent_id=agent_id,
        )
        model_records = list(state.model_executions)
        termination = state.termination or TerminationState(status="running", sequence=state.step_seq)
        observation: ToolResult | None = None
        observation_ref: str | None = None
        forced_turn: AgentTurn | None = None
        forced_record: ModelExecutionRecord | None = None
        pending_action: ModelAction | None = None
        pending_record: ModelExecutionRecord | None = None

        def project() -> RuntimeState:
            return self._project_state(current_state, turns, ledger, termination, model_records)

        def publish(projected: RuntimeState) -> None:
            nonlocal current_state
            current_state = projected
            if checkpoint_callback is not None:
                checkpoint_callback(projected)

        def upsert_record(record: ModelExecutionRecord) -> None:
            for index, existing in enumerate(model_records):
                if existing.turn_id == record.turn_id:
                    model_records[index] = record
                    return
            model_records.append(record)

        def persist_ledger(_current_ledger: ToolExecutionLedger) -> None:
            publish(project())

        def blocked_result(error: ToolError) -> ModelLoopResult:
            return ModelLoopResult(
                status="failed",
                state=current_state,
                last_observation=observation,
                error=error,
            )

        def failed_result(error: ToolError) -> ModelLoopResult:
            nonlocal current_state, termination
            if turns and turns[-1].status == "running":
                turns[-1] = replace(turns[-1], status="failed")
            termination = TerminationState(
                status="failed",
                reason_code="model_loop_failed",
                reason="The durable model loop failed safely.",
                sequence=turns[-1].sequence if turns else state.step_seq,
            )
            projected = project()
            try:
                publish(projected)
            except Exception:
                current_state = projected
            return ModelLoopResult(
                status="failed",
                state=current_state,
                last_observation=observation,
                error=error,
            )

        def tool_definitions_artifact(turn_id: str):
            return artifact_store.write(
                f"model/{artifact_store.safe_segment(state.run_id)}"
                f"/{artifact_store.safe_segment(turn_id)}/tools.json",
                {"tools": [tool.to_dict() for tool in tool_definitions]},
                kind="tool_definitions",
            )

        def request_artifact(
            turn: AgentTurn,
            request: ModelTurnRequest,
            tools_ref: str,
            input_observation_ref: str | None,
        ):
            return artifact_store.write_request(
                state.run_id,
                turn.turn_id,
                {
                    "runtime_identity": {
                        "run_id": state.run_id,
                        "turn_id": turn.turn_id,
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "sequence": turn.sequence,
                    },
                    "user_input": request.user_input,
                    "tool_definition_snapshot_ref": tools_ref,
                    "observation_ref": input_observation_ref,
                    "model_request": request.to_dict(),
                },
            )

        def response_artifact(turn: AgentTurn, action: ModelAction):
            return artifact_store.write_response(
                state.run_id,
                turn.turn_id,
                {
                    "runtime_identity": {
                        "run_id": state.run_id,
                        "turn_id": turn.turn_id,
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "sequence": turn.sequence,
                    },
                    "normalized_action": action.to_dict(),
                    "usage": {},
                    "provider_metadata": {"adapter": "provider_neutral_fake"},
                },
            )

        def artifact_identity(record: ModelExecutionRecord) -> dict[str, Any]:
            return {
                "run_id": record.run_id,
                "turn_id": record.turn_id,
                "task_id": record.task_id,
                "agent_id": record.agent_id,
                "sequence": record.sequence,
            }

        def validate_record_context(record: ModelExecutionRecord) -> None:
            if (
                record.run_id != state.run_id
                or record.task_id != task_id
                or record.agent_id != agent_id
            ):
                raise ValidationError("Durable model record identity does not match the resume context.")

        def load_request(record: ModelExecutionRecord) -> ModelTurnRequest:
            """Load the exact request that crossed the durable model boundary."""
            if not record.request_ref or record.request_sha256 is None:
                raise ValidationError(
                    "Durable model record has no verifiable request artifact."
                )
            payload = artifact_store.read(
                record.request_ref,
                expected_sha256=record.request_sha256,
            )
            if payload.get("runtime_identity") != artifact_identity(record):
                raise ValidationError("Durable model request identity does not match its record.")
            raw_request = payload.get("model_request")
            if not isinstance(raw_request, dict):
                raise ValidationError("Durable model request artifact has no complete ModelTurnRequest.")
            request = ModelTurnRequest.from_dict(raw_request)
            if (
                request.run_id != record.run_id
                or request.turn_id != record.turn_id
                or request.task_id != record.task_id
                or request.agent_id != record.agent_id
                or request.sequence != record.sequence
            ):
                raise ValidationError("Durable model request identity does not match its record.")
            if payload.get("user_input") != request.user_input:
                raise ValidationError("Durable model request user_input does not match its envelope.")
            if payload.get("tool_definition_snapshot_ref") != record.tool_definition_snapshot_ref:
                raise ValidationError(
                    "Durable model request tool snapshot does not match its record."
                )
            if payload.get("observation_ref") != record.observation_ref:
                raise ValidationError(
                    "Durable model request observation does not match its record."
                )
            return request

        def turn_index(turn: AgentTurn) -> int:
            for index, existing in enumerate(turns):
                if existing.turn_id == turn.turn_id:
                    return index
            raise ValidationError("Durable model turn is missing from runtime state.")

        def load_action(record: ModelExecutionRecord) -> ModelAction:
            if record.response_ref:
                payload = artifact_store.read(record.response_ref, expected_sha256=record.response_sha256)
                if payload.get("runtime_identity") != artifact_identity(record):
                    raise ValidationError("Durable model response identity does not match its record.")
                raw_action = payload.get("normalized_action")
            else:
                raw_action = record.normalized_action
            action = ModelAction.from_dict(raw_action)
            if record.normalized_action is not None and action.to_dict() != record.normalized_action:
                raise ValidationError("Durable model action does not match its response artifact.")
            return action

        def record_tool_call(record: ModelExecutionRecord) -> ToolCall:
            if not isinstance(record.normalized_action, dict):
                raise ValidationError("Durable tool observation has no normalized ToolCall.")
            action = ModelAction.from_dict(record.normalized_action)
            if action.kind != "tool_call" or action.tool_call is None:
                raise ValidationError("Durable tool observation requires a ToolCall action.")
            return action.tool_call

        def validate_ledger_identity(existing: Any, call: ToolCall) -> None:
            if existing is not None and (
                existing.tool_id != call.tool_id
                or existing.run_id != call.run_id
                or existing.task_id != call.task_id
                or existing.agent_id != call.agent_id
                or existing.sequence != call.sequence
                or existing.arguments != call.arguments
            ):
                raise ValidationError("Restored tool ledger identity does not match the proposed call.")

        def validate_result_identity(result: ToolResult, call: ToolCall) -> None:
            if result.call_id != call.call_id or result.tool_id != call.tool_id:
                raise ValidationError("Restored ToolResult identity does not match the proposed call.")

        def is_uncertain_checkpoint(result: ToolResult) -> bool:
            return result.error is not None and result.error.code in {
                "TOOL_PENDING_CHECKPOINT_FAILED",
                "TOOL_TERMINAL_CHECKPOINT_FAILED",
            }

        def load_observation(record: ModelExecutionRecord) -> ToolResult:
            expected_call = record_tool_call(record)
            if record.observation_ref:
                payload = artifact_store.read(
                    record.observation_ref,
                    expected_sha256=record.observation_sha256,
                )
                if payload.get("runtime_identity") != artifact_identity(record):
                    raise ValidationError("Durable observation identity does not match its record.")
                raw_result = payload.get("tool_result")
                if not isinstance(raw_result, dict):
                    raise ValidationError("Durable observation artifact has no ToolResult.")
                result = ToolResult.from_dict(raw_result)
                validate_result_identity(result, expected_call)
                return result
            existing = ledger.get(expected_call.call_id)
            validate_ledger_identity(existing, expected_call)
            if existing is not None and existing.result is not None:
                result = ToolResult.from_dict(existing.result)
                validate_result_identity(result, expected_call)
                return result
            if existing is not None and existing.error is not None:
                return ToolResult(
                    call_id=expected_call.call_id,
                    tool_id=existing.tool_id,
                    status="error",
                    error=ToolError.from_dict(existing.error),
                )
            raise ValidationError("No durable ToolResult observation is available.")

        def bind_action(action: ModelAction, turn: AgentTurn) -> ModelAction:
            if action.kind == "final":
                return action
            proposal = action.tool_call
            assert proposal is not None
            if proposal.run_id == state.run_id and proposal.task_id == task_id and proposal.agent_id == agent_id:
                return action
            return ModelAction.tool(
                ToolCall(
                    call_id=f"{state.run_id}:turn:{turn.sequence}:tool",
                    tool_id=proposal.tool_id,
                    arguments=dict(proposal.arguments),
                    run_id=state.run_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    sequence=turn.sequence,
                )
            )

        def terminal_final(turn: AgentTurn, record: ModelExecutionRecord, action: ModelAction) -> ModelLoopResult:
            nonlocal termination
            turns[turn_index(turn)] = replace(turn, status="completed", model_response_ref=record.response_ref)
            upsert_record(replace(record, status="completed", normalized_action=action.to_dict()))
            termination = TerminationState(
                status="completed",
                reason_code="model_final",
                reason="The runtime accepted a durable model final answer.",
                sequence=turn.sequence,
            )
            publish(project())
            return ModelLoopResult(
                status="completed",
                state=current_state,
                final_answer=action.final_answer,
                last_observation=observation,
            )

        def execute_tool_action(
            turn: AgentTurn,
            record: ModelExecutionRecord,
            action: ModelAction,
        ) -> ToolResult | None:
            nonlocal observation, observation_ref
            bound = bind_action(action, turn)
            if bound.kind != "tool_call" or bound.tool_call is None:
                return None
            call = bound.tool_call
            current_turn_index = turn_index(turn)
            turns[current_turn_index] = replace(turns[current_turn_index], tool_call_ids=[call.call_id])
            if record.normalized_action != bound.to_dict():
                record = replace(record, normalized_action=bound.to_dict())
                upsert_record(record)
            publish(project())

            existing = ledger.get(call.call_id)
            validate_ledger_identity(existing, call)
            if existing is not None and existing.status == "pending":
                raise ValidationError("A pending tool call requires recovery verification.")
            if existing is not None and existing.status in {"completed", "failed"}:
                if existing.result is not None:
                    result = ToolResult.from_dict(existing.result)
                    validate_result_identity(result, call)
                elif existing.error is not None:
                    result = ToolResult(
                        call_id=call.call_id,
                        tool_id=call.tool_id,
                        status="error",
                        error=ToolError.from_dict(existing.error),
                    )
                else:
                    raise ValidationError("Terminal ledger record has no normalized outcome.")
            else:
                result = self.tool_runtime.execute(
                    call,
                    self.policy_enforcer,
                    event_sink=event_sink,
                    ledger=ledger,
                    checkpoint_callback=persist_ledger,
                )

            if is_uncertain_checkpoint(result):
                upsert_record(
                    replace(
                        record,
                        status="failed",
                        normalized_action=bound.to_dict(),
                    )
                )
                turns[current_turn_index] = replace(
                    turns[current_turn_index],
                    status="failed",
                )
                return result

            stored_observation = artifact_store.write_observation(
                state.run_id,
                turn.turn_id,
                {
                    "runtime_identity": {
                        "run_id": state.run_id,
                        "turn_id": turn.turn_id,
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "sequence": turn.sequence,
                    },
                    "tool_result": result.to_dict(),
                },
            )
            observation = result
            observation_ref = stored_observation.ref
            upsert_record(
                replace(
                    record,
                    status="tool_result_durable",
                    normalized_action=bound.to_dict(),
                    observation_ref=stored_observation.ref,
                    observation_sha256=stored_observation.sha256,
                )
            )
            turns[current_turn_index] = replace(
                turns[current_turn_index],
                status="completed" if result.status == "ok" else "failed",
            )
            publish(project())
            return result

        try:
            if resume:
                decision = decide_model_resume(current_state, ledger=ledger)
                record = (
                    next(
                        (
                            candidate
                            for candidate in model_records
                            if candidate.turn_id == decision.turn_id
                        ),
                        None,
                    )
                    if decision.turn_id
                    else (max(model_records, key=lambda item: (item.sequence, item.turn_id)) if model_records else None)
                )
                if record is not None:
                    validate_record_context(record)
                if decision.decision == "already_terminal":
                    if termination.status == "completed" and record is not None:
                        final_action = load_action(record)
                        if final_action.kind == "final":
                            return ModelLoopResult(
                                status="completed",
                                state=current_state,
                                final_answer=final_action.final_answer,
                            )
                    return blocked_result(
                        self._error(
                            "MODEL_ALREADY_TERMINAL",
                            "runtime",
                            "The model loop is already terminal.",
                            {},
                        )
                    )
                if decision.decision in {
                    "requires_verification",
                    "requires_manual_decision",
                }:
                    return blocked_result(
                        self._error(
                            "MODEL_RESUME_REQUIRES_VERIFICATION",
                            "runtime",
                            "Durable state requires verification before resume.",
                            {"decision": decision.decision, "reason_code": decision.reason_code},
                        )
                    )
                if decision.decision == "reuse_durable_model_response":
                    if record is None:
                        raise ValidationError("No durable model record is available for response reuse.")
                    pending_action = load_action(record)
                    pending_record = record
                    matching_turn = next((turn for turn in turns if turn.turn_id == record.turn_id), None)
                    if matching_turn is None:
                        matching_turn = AgentTurn(
                            turn_id=record.turn_id,
                            run_id=state.run_id,
                            task_id=task_id,
                            agent_id=agent_id,
                            sequence=record.sequence,
                            status="running",
                        )
                        turns.append(matching_turn)
                    forced_turn = matching_turn
                elif decision.decision == "resume_from_tool_result":
                    if record is None:
                        raise ValidationError("No durable model record is available for observation resume.")
                    observation = load_observation(record)
                    observation_ref = record.observation_ref
                elif decision.decision == "requires_model_reinvoke":
                    raise ValidationError("Explicit model reinvocation requires a new request record.")
                elif decision.decision == "safe_to_invoke_model" and record is not None:
                    forced_record = record
                    forced_turn = next((turn for turn in turns if turn.turn_id == record.turn_id), None)

            next_sequence = max((turn.sequence for turn in turns), default=0) + 1
            model_steps = 0
            while model_steps < self.max_steps:
                if pending_action is not None and forced_turn is not None:
                    turn = forced_turn
                    record = pending_record
                    action = pending_action
                    pending_action = None
                    pending_record = None
                    forced_turn = None
                    if record is None:
                        raise ValidationError("Durable action has no model execution record.")
                    if action.kind == "final":
                        return terminal_final(turn, record, action)
                    tool_result = execute_tool_action(turn, record, action)
                    if tool_result is not None and is_uncertain_checkpoint(tool_result):
                        return failed_result(tool_result.error)
                    next_sequence = max(next_sequence, turn.sequence + 1)
                    continue

                if forced_turn is not None:
                    turn = forced_turn
                    forced_turn = None
                elif forced_record is not None:
                    turn = AgentTurn(
                        turn_id=forced_record.turn_id,
                        run_id=state.run_id,
                        task_id=task_id,
                        agent_id=agent_id,
                        sequence=forced_record.sequence,
                        status="running",
                    )
                    turns.append(turn)
                else:
                    turn = AgentTurn(
                        turn_id=f"{state.run_id}:turn:{next_sequence}",
                        run_id=state.run_id,
                        task_id=task_id,
                        agent_id=agent_id,
                        sequence=next_sequence,
                        status="running",
                    )
                    turns.append(turn)
                record = forced_record
                forced_record = None
                model_steps += 1

                if record is None or record.status != "request_durable":
                    request = ModelTurnRequest(
                        run_id=state.run_id,
                        turn_id=turn.turn_id,
                        task_id=task_id,
                        agent_id=agent_id,
                        sequence=turn.sequence,
                        user_input=user_input,
                        observation=observation,
                        available_tools=list(tool_definitions),
                    )
                    tools_artifact = tool_definitions_artifact(turn.turn_id)
                    request_ref_artifact = request_artifact(
                        turn,
                        request,
                        tools_artifact.ref,
                        observation_ref,
                    )
                    record = ModelExecutionRecord(
                        run_id=state.run_id,
                        turn_id=turn.turn_id,
                        task_id=task_id,
                        agent_id=agent_id,
                        sequence=turn.sequence,
                        status="request_durable",
                        request_ref=request_ref_artifact.ref,
                        tool_definition_snapshot_ref=tools_artifact.ref,
                        observation_ref=observation_ref,
                        request_sha256=request_ref_artifact.sha256,
                    )
                    upsert_record(record)
                    turns[turn_index(turn)] = replace(
                        turn,
                        model_request_ref=request_ref_artifact.ref,
                    )
                    publish(project())
                else:
                    request = load_request(record)
                record = replace(record, status="request_sent")
                upsert_record(record)
                publish(project())
                try:
                    action = self.model.decide(request)
                except Exception:
                    upsert_record(replace(record, status="failed"))
                    return failed_result(
                        self._error(
                            "MODEL_ADAPTER_FAILED",
                            "runtime",
                            "Model adapter failed to produce a safe action.",
                            {"turn_id": turn.turn_id},
                        )
                    )
                if not isinstance(action, ModelAction):
                    upsert_record(replace(record, status="failed"))
                    return failed_result(
                        self._error(
                            "MODEL_ACTION_INVALID",
                            "schema",
                            "Model adapter returned an invalid action.",
                            {"turn_id": turn.turn_id},
                        )
                    )

                bound_action = bind_action(action, turn)
                record = replace(record, status="response_obtained")
                upsert_record(record)
                publish(project())
                response = response_artifact(turn, bound_action)
                record = replace(
                    record,
                    status="response_durable",
                    response_ref=response.ref,
                    response_sha256=response.sha256,
                    normalized_action=bound_action.to_dict(),
                )
                upsert_record(record)
                turns[turn_index(turn)] = replace(turn, model_response_ref=response.ref)
                publish(project())

                if bound_action.kind == "final":
                    return terminal_final(turns[turn_index(turn)], record, bound_action)
                tool_result = execute_tool_action(turns[turn_index(turn)], record, bound_action)
                if tool_result is not None and is_uncertain_checkpoint(tool_result):
                    return failed_result(tool_result.error)
                next_sequence = max(next_sequence, turn.sequence + 1)

            termination = TerminationState(
                status="budget_exhausted",
                reason_code="max_steps_exhausted",
                reason="The runtime reached its model-loop max_steps boundary.",
                sequence=max((turn.sequence for turn in turns), default=state.step_seq),
            )
            publish(project())
            return ModelLoopResult(
                status="budget_exhausted",
                state=current_state,
                last_observation=observation,
                error=self._error(
                    "MODEL_MAX_STEPS_EXCEEDED",
                    "budget",
                    "The model loop reached its maximum step count.",
                    {"max_steps": self.max_steps},
                ),
            )
        except Exception:
            return failed_result(
                self._error(
                    "MODEL_LOOP_RUNTIME_FAILED",
                    "runtime",
                    "The durable model loop failed safely.",
                    {"run_id": state.run_id},
                )
            )


__all__ = ["MODEL_LOOP_STATUSES", "ModelLoopResult", "SingleAgentModelLoop"]
