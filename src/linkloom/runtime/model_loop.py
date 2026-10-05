"""Independent single-agent model-loop harness for P8.3.

This is deliberately not the production RetrievalAgent path.  It proves the
smallest runtime-owned loop around the existing ToolRuntime and ledger using a
local FakeModelAdapter only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Callable, Sequence

from linkloom.agents.model_adapter import (
    MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES,
    MAX_VERIFIED_EVIDENCE_CONTEXT_RESULTS,
    ModelAction,
    ModelAdapter,
    ModelProviderAdapter,
    ModelProviderError,
    ModelResponse,
    ModelToolCall,
    ModelToolInteraction,
    ModelToolResult,
    ModelToolTurn,
    ModelTurnProposal,
    ModelTurnRequest,
    ModelUsage,
    ProviderContinuation,
    ProviderTurnContinuation,
    is_verified_evidence_result,
    runtime_call_id_for,
    select_bounded_model_tool_turns,
)
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    AgentTurn,
    ModelExecutionRecord,
    MODEL_RESPONSE_ORIGINS,
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
    tool_results: list[ToolResult] = field(default_factory=list)
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
        if not isinstance(self.tool_results, list) or any(
            not isinstance(result, ToolResult) for result in self.tool_results
        ):
            raise ValidationError("ModelLoopResult.tool_results must contain ToolResult values.")
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
            "tool_results": [result.to_dict() for result in self.tool_results],
            "error": self.error.to_dict() if self.error else None,
        }


class SingleAgentModelLoop:
    """Runtime-owned loop: model proposes, runtime executes, result is observed."""

    def __init__(
        self,
        model: ModelAdapter | ModelProviderAdapter,
        tool_runtime: ToolRuntime,
        policy_enforcer: ToolPolicyEnforcer,
        *,
        max_steps: int = 8,
        verified_evidence_context_limit: int = 0,
    ) -> None:
        has_decide = callable(getattr(model, "decide", None))
        has_complete = callable(getattr(model, "complete", None))
        if not has_decide and not has_complete:
            raise ValidationError(
                "SingleAgentModelLoop model must implement decide() or complete()."
            )
        if not isinstance(tool_runtime, ToolRuntime):
            raise ValidationError("SingleAgentModelLoop tool_runtime must be ToolRuntime.")
        if not isinstance(policy_enforcer, ToolPolicyEnforcer):
            raise ValidationError(
                "SingleAgentModelLoop policy_enforcer must be ToolPolicyEnforcer."
            )
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
            raise ValidationError("SingleAgentModelLoop max_steps must be a positive integer.")
        if (
            isinstance(verified_evidence_context_limit, bool)
            or not isinstance(verified_evidence_context_limit, int)
            or verified_evidence_context_limit < 0
            or verified_evidence_context_limit > MAX_VERIFIED_EVIDENCE_CONTEXT_RESULTS
        ):
            raise ValidationError(
                "SingleAgentModelLoop verified_evidence_context_limit must be between zero and the bounded result limit."
            )
        self.model = model
        self._has_legacy_decide = has_decide
        self._has_provider_complete = has_complete
        self.tool_runtime = tool_runtime
        self.policy_enforcer = policy_enforcer
        self.max_steps = max_steps
        self.verified_evidence_context_limit = verified_evidence_context_limit

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

    def _verified_evidence_context(
        self,
        ledger: ToolExecutionLedger,
        *,
        run_id: str,
        task_id: str,
        agent_id: str,
        before_sequence: int,
    ) -> list[ToolResult]:
        """Expose a small, deterministic projection of prior verified evidence.

        This is deliberately an opt-in evidence ledger view, not a general
        conversation-memory mechanism.  Its exact payload is retained in the
        durable ModelTurnRequest artifact before any model invocation.
        """
        if self.verified_evidence_context_limit == 0:
            return []
        context: list[ToolResult] = []
        records = sorted(
            (
                record
                for record in ledger.to_list()
                if record.run_id == run_id
                and record.task_id == task_id
                and record.agent_id == agent_id
                and record.sequence < before_sequence
                and record.status == "completed"
                and record.result is not None
            ),
            key=lambda record: (record.sequence, record.call_id),
        )
        for record in records:
            result = ToolResult.from_dict(record.result)
            if not is_verified_evidence_result(result):
                continue
            trial = [*context, result]
            encoded = json.dumps(
                [item.to_dict() for item in trial],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(encoded.encode("utf-8")) > MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES:
                continue
            context.append(result)
            if len(context) == self.verified_evidence_context_limit:
                break
        return context

    @staticmethod
    def _completed_tool_history(
        model_records: list[ModelExecutionRecord],
        ledger: ToolExecutionLedger,
        *,
        run_id: str,
        task_id: str,
        agent_id: str,
        before_sequence: int,
        current_call_id: str | None,
    ) -> list[ModelToolInteraction]:
        """Rebuild prior completed exchanges from durable neutral records."""

        history: list[ModelToolInteraction] = []
        for record in sorted(
            (
                candidate
                for candidate in model_records
                if candidate.run_id == run_id
                and candidate.task_id == task_id
                and candidate.agent_id == agent_id
                and candidate.sequence < before_sequence
                and isinstance(candidate.normalized_action, dict)
                and candidate.normalized_action.get("kind") == "tool_call"
            ),
            key=lambda candidate: (candidate.sequence, candidate.turn_id),
        ):
            raw_call = record.normalized_action.get("tool_call")
            if not isinstance(raw_call, dict):
                raise ValidationError(
                    "Durable tool history contains an invalid normalized action."
                )
            call = ToolCall.from_dict(raw_call)
            if call.call_id == current_call_id:
                continue
            ledger_record = ledger.get(call.call_id)
            if (
                ledger_record is None
                or ledger_record.status not in {"completed", "failed"}
                or ledger_record.result is None
            ):
                raise ValidationError(
                    "Durable tool history has no completed result for a model tool call."
                )
            history.append(
                ModelToolInteraction(
                    tool_call=call,
                    tool_result=ToolResult.from_dict(ledger_record.result),
                )
            )
        return history

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
        if not self._has_legacy_decide:
            raise ValidationError(
                "ModelProviderAdapter.complete() requires the durable model loop."
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
                    evidence_context=self._verified_evidence_context(
                        ledger,
                        run_id=state.run_id,
                        task_id=task_id,
                        agent_id=agent_id,
                        before_sequence=next_sequence,
                    ),
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
        pending_response: ModelResponse | None = None
        pending_record: ModelExecutionRecord | None = None
        previous_tool_call: ToolCall | None = None
        provider_continuation: ProviderContinuation | None = None
        completed_tool_turns: list[ModelToolTurn] = []
        ordered_tool_results: list[ToolResult] = []

        def project() -> RuntimeState:
            return self._project_state(current_state, turns, ledger, termination, model_records)

        def publish(projected: RuntimeState) -> None:
            nonlocal current_state
            current_state = projected
            if checkpoint_callback is not None:
                checkpoint_callback(projected)

        def commit_provider_checkpoint(
            projected: RuntimeState,
            checkpoint_status: str,
        ) -> ToolError | None:
            """Commit a provider boundary before exposing it as current state."""
            nonlocal current_state
            if checkpoint_callback is None:
                return self._error(
                    "MODEL_DURABLE_CHECKPOINT_REQUIRED",
                    "runtime",
                    "Provider invocation requires a durable checkpoint callback.",
                    {"checkpoint_status": checkpoint_status},
                )
            try:
                checkpoint_callback(projected)
            except Exception:
                return self._error(
                    "MODEL_CHECKPOINT_FAILED",
                    "runtime",
                    "The durable model checkpoint could not be committed.",
                    {"checkpoint_status": checkpoint_status},
                )
            current_state = projected
            return None

        def publish_model_boundary(checkpoint_status: str) -> ToolError | None:
            projected = project()
            if self._has_provider_complete:
                return commit_provider_checkpoint(projected, checkpoint_status)
            publish(projected)
            return None

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

        def compatibility_action(response: ModelResponse) -> ModelAction | None:
            """Project the canonical response into the legacy action shape.

            V2 providers retain their Provider call IDs in ``proposal`` while
            the runtime owns the run-wide ``ToolCall.call_id``.  The legacy
            projection must therefore use the bound runtime call ID so old
            consumers and M0.4 artifacts continue to correlate actions with
            the durable tool ledger.  A multi-call proposal has no lossless
            singleton action view and remains ``None``.
            """
            if response.proposal is not None:
                if response.proposal.kind == "final":
                    return ModelAction.final(response.proposal.final_answer)
                if len(response.proposal.tool_calls) == 1:
                    return ModelAction.tool(
                        response.proposal.tool_calls[0].runtime_call
                    )
                return None
            return response.action

        def response_artifact(turn: AgentTurn, response: ModelResponse):
            projected_action = compatibility_action(response)
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
                    "model_response": response.to_dict(),
                    "normalized_action": (
                        projected_action.to_dict()
                        if projected_action is not None
                        else None
                    ),
                    "normalized_proposal": (
                        response.proposal.to_dict()
                        if response.proposal is not None
                        else None
                    ),
                    "usage": response.usage.to_dict(),
                    "provider_metadata": response.provider_metadata,
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

        def projected_provider_request_id(response: ModelResponse) -> str | None:
            if response.provider_request_id is not None:
                return response.provider_request_id
            if response.error is not None:
                return response.error.provider_request_id
            return None

        def is_legacy_p84_record(
            record: ModelExecutionRecord,
            payload: dict[str, Any] | None,
        ) -> bool:
            """Recognize only the explicitly bounded pre-origin P8.4 shape."""
            if record.response_origin is not None:
                return record.response_origin == "legacy"
            if any(
                value is not None
                for value in (
                    record.provider_request_id,
                    record.provider_response_id,
                    record.finish_reason,
                    record.provider_error,
                )
            ):
                return False
            if record.provider_metadata != {}:
                return False
            if payload is None:
                return False
            if set(payload) != {
                "runtime_identity",
                "normalized_action",
                "usage",
                "provider_metadata",
            }:
                return False
            return (
                payload["provider_metadata"] == {"adapter": "provider_neutral_fake"}
                and isinstance(payload["normalized_action"], dict)
                and isinstance(payload["usage"], dict)
            )

        def durable_response_origin(
            record: ModelExecutionRecord,
            payload: dict[str, Any] | None,
        ) -> str:
            if record.response_origin in MODEL_RESPONSE_ORIGINS:
                return record.response_origin
            if is_legacy_p84_record(record, payload):
                return "legacy"
            raise ValidationError(
                "Durable model response origin is missing or cannot be proven legacy.",
                details={"reason": "response_origin_missing"},
            )

        def load_response(record: ModelExecutionRecord) -> ModelResponse:
            payload: dict[str, Any] | None = None
            if record.response_origin == "provider" and (
                not record.response_ref or record.response_sha256 is None
            ):
                raise ValidationError(
                    "Durable provider response has no verifiable response artifact.",
                    details={"reason": "provider_response_artifact_missing"},
                )
            if record.response_origin == "legacy" and (
                not record.response_ref or record.response_sha256 is None
            ):
                raise ValidationError(
                    "Durable legacy response has no verifiable response artifact.",
                    details={"reason": "legacy_response_artifact_missing"},
                )
            if (
                record.response_origin is None
                and (
                    record.response_ref is not None
                    or record.response_sha256 is not None
                )
                and (
                    not record.response_ref or record.response_sha256 is None
                )
            ):
                raise ValidationError(
                    "Historical durable response has incomplete artifact identity.",
                    details={"reason": "legacy_response_artifact_missing"},
                )
            if record.response_ref:
                payload = artifact_store.read(
                    record.response_ref,
                    expected_sha256=record.response_sha256,
                )
                if payload.get("runtime_identity") != artifact_identity(record):
                    raise ValidationError(
                        "Durable model response identity does not match its record."
                    )

            origin = durable_response_origin(record, payload)
            if origin == "provider":
                raw_response = payload.get("model_response") if payload is not None else None
                if not isinstance(raw_response, dict):
                    raise ValidationError(
                        "Durable provider response artifact has no complete ModelResponse.",
                        details={"reason": "provider_response_envelope_missing"},
                    )
                response = ModelResponse.from_dict(raw_response)
                if (
                    response.provider_continuation is not None
                    and response.provider_continuation.source_turn_id
                    != record.turn_id
                ):
                    raise ValidationError(
                        "Durable provider continuation does not match its source turn."
                    )
                if (
                    response.provider_turn_continuation is not None
                    and response.provider_turn_continuation.source_turn_id
                    != record.turn_id
                ):
                    raise ValidationError(
                        "Durable Provider turn continuation does not match its source turn."
                    )
                # ``ModelResponse`` owns the V1 compatibility projection: a
                # terminal proposal and a singleton V2 tool proposal expose a
                # canonical ``action`` view, while a true multi-call proposal
                # intentionally has no lossy singleton representation.  Keep
                # that view in the legacy record field alongside the canonical
                # V2 proposal so existing consumers can continue reading it.
                projected_action_value = compatibility_action(response)
                projected_action = (
                    projected_action_value.to_dict()
                    if projected_action_value is not None
                    else None
                )
                projected_proposal = (
                    response.proposal.to_dict()
                    if response.proposal is not None
                    else None
                )
                projected_error = (
                    response.error.to_dict() if response.error is not None else None
                )
                expected_projection = {
                    "normalized_action": projected_action,
                    "normalized_proposal": projected_proposal,
                    "proposal_id": (
                        response.proposal.proposal_id
                        if response.proposal is not None
                        else None
                    ),
                    "usage": response.usage.to_dict(),
                    "provider_metadata": response.provider_metadata,
                    "provider_request_id": projected_provider_request_id(response),
                    "provider_response_id": response.provider_response_id,
                    "finish_reason": response.finish_reason,
                    "provider_error": projected_error,
                }
                actual_projection = {
                    "normalized_action": record.normalized_action,
                    "normalized_proposal": record.normalized_proposal,
                    "proposal_id": record.proposal_id,
                    "usage": record.usage,
                    "provider_metadata": record.provider_metadata,
                    "provider_request_id": record.provider_request_id,
                    "provider_response_id": record.provider_response_id,
                    "finish_reason": record.finish_reason,
                    "provider_error": record.provider_error,
                }
                if expected_projection != actual_projection:
                    raise ValidationError(
                        "Durable provider response does not match its record projection."
                    )
                if payload.get("normalized_action") != projected_action:
                    raise ValidationError(
                        "Durable model action does not match its response artifact."
                    )
                if payload.get("normalized_proposal") != projected_proposal:
                    raise ValidationError(
                        "Durable model proposal does not match its response artifact."
                    )
                if payload.get("usage") != response.usage.to_dict():
                    raise ValidationError(
                        "Durable model usage does not match its response artifact."
                    )
                if payload.get("provider_metadata") != response.provider_metadata:
                    raise ValidationError(
                        "Durable provider metadata does not match its response artifact."
                    )
                return response

            if payload is None:
                raise ValidationError(
                    "Durable legacy response has no verifiable response artifact.",
                    details={"reason": "legacy_response_artifact_missing"},
                )
            raw_action = payload.get("normalized_action")
            action = ModelAction.from_dict(raw_action)
            if (
                record.normalized_action is not None
                and action.to_dict() != record.normalized_action
            ):
                raise ValidationError(
                    "Durable model action does not match its response artifact."
                )
            artifact_usage = ModelUsage.from_dict(payload.get("usage"))
            record_usage = ModelUsage.from_dict(record.usage)
            if artifact_usage.to_dict() != record_usage.to_dict():
                raise ValidationError(
                    "Durable model usage does not match its response artifact."
                )
            return ModelResponse(
                action=action,
                usage=artifact_usage,
                provider_metadata=dict(record.provider_metadata),
            )

        def load_action(record: ModelExecutionRecord) -> ModelAction:
            response = load_response(record)
            if response.action is None:
                raise ValidationError("Durable model response has no action.")
            return response.action

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

        def load_observation(
            record: ModelExecutionRecord,
            expected_call: ToolCall,
        ) -> ToolResult:
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

        def load_model_tool_result(
            record: ModelExecutionRecord,
            proposal: ModelTurnProposal,
            ordinal: int,
        ) -> ModelToolResult:
            if ordinal >= len(record.tool_result_refs) or ordinal >= len(
                proposal.tool_calls
            ):
                raise ValidationError(
                    "Durable ModelToolResult ordinal is outside its proposal prefix."
                )
            ref = record.tool_result_refs[ordinal]
            payload = artifact_store.read(
                ref["result_ref"],
                expected_sha256=ref["result_sha256"],
            )
            if payload.get("runtime_identity") != artifact_identity(record):
                raise ValidationError(
                    "Durable ModelToolResult identity does not match its record."
                )
            raw_result = payload.get("model_tool_result")
            if not isinstance(raw_result, dict):
                raise ValidationError(
                    "Durable tool-result artifact has no ModelToolResult."
                )
            model_result = ModelToolResult.from_dict(raw_result)
            model_call = proposal.tool_calls[ordinal]
            runtime_call = model_call.runtime_call
            expected_ref = {
                "ordinal": ordinal,
                "result_id": model_result.result_id,
                "provider_call_id": model_result.provider_call_id,
                "runtime_call_id": model_result.runtime_call_id,
                "tool_id": model_result.result.tool_id,
                "result_ref": ref["result_ref"],
                "result_sha256": ref["result_sha256"],
            }
            if ref != expected_ref or (
                model_result.run_id != record.run_id
                or model_result.proposal_id != proposal.proposal_id
                or model_result.ordinal != ordinal
                or model_result.provider_call_id != model_call.provider_call_id
                or model_result.runtime_call_id != runtime_call.call_id
                or model_result.result.call_id != runtime_call.call_id
                or model_result.result.tool_id != runtime_call.tool_id
            ):
                raise ValidationError(
                    "Durable ModelToolResult does not match its proposal identity."
                )
            existing = ledger.get(runtime_call.call_id)
            validate_ledger_identity(existing, runtime_call)
            if existing is not None:
                if existing.status == "pending":
                    raise ValidationError(
                        "A durable ModelToolResult cannot reference a pending ledger call."
                    )
                if existing.result is not None:
                    ledger_result = ToolResult.from_dict(existing.result)
                elif existing.error is not None:
                    ledger_result = ToolResult(
                        call_id=runtime_call.call_id,
                        tool_id=runtime_call.tool_id,
                        status="error",
                        error=ToolError.from_dict(existing.error),
                    )
                else:
                    raise ValidationError(
                        "Terminal ledger record has no normalized outcome."
                    )
                if model_result.result.to_dict() != ledger_result.to_dict():
                    raise ValidationError(
                        "Durable ModelToolResult payload does not match its ledger outcome."
                    )
            return model_result

        def bind_action(action: ModelAction, turn: AgentTurn) -> ModelAction:
            if action.kind == "final":
                return action
            proposal = action.tool_call
            assert proposal is not None
            if proposal.run_id == state.run_id and proposal.task_id == task_id and proposal.agent_id == agent_id:
                if proposal.sequence != turn.sequence:
                    raise ValidationError(
                        "Provider ToolCall sequence does not match the current runtime turn.",
                        details={
                            "reason": "tool_call_sequence_mismatch",
                            "expected_sequence": turn.sequence,
                            "received_sequence": proposal.sequence,
                        },
                    )
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

        def bind_proposal(
            proposal: ModelTurnProposal,
            turn: AgentTurn,
        ) -> ModelTurnProposal:
            expected_proposal_id = f"{turn.turn_id}:proposal"
            if proposal.proposal_id != expected_proposal_id:
                raise ValidationError(
                    "Provider proposal identity does not match the current runtime turn.",
                    details={"reason": "proposal_id_mismatch"},
                )
            if proposal.kind == "final":
                return proposal
            for ordinal, model_call in enumerate(proposal.tool_calls):
                call = model_call.runtime_call
                if (
                    call.run_id != state.run_id
                    or call.task_id != task_id
                    or call.agent_id != agent_id
                    or call.sequence != turn.sequence
                ):
                    raise ValidationError(
                        "Provider ToolCall identity does not match the current runtime turn.",
                        details={"reason": "tool_call_identity_mismatch"},
                    )
                expected_call_id = runtime_call_id_for(
                    run_id=state.run_id,
                    proposal_id=proposal.proposal_id,
                    ordinal=ordinal,
                    provider_call_id=model_call.provider_call_id,
                )
                if call.call_id != expected_call_id:
                    raise ValidationError(
                        "Provider ToolCall Runtime identity is not canonical.",
                        details={"reason": "runtime_call_id_mismatch"},
                    )
            return proposal

        def invoke_model(request: ModelTurnRequest, turn: AgentTurn) -> ModelResponse:
            if self._has_provider_complete:
                response = self.model.complete(request)  # type: ignore[union-attr]
                if not isinstance(response, ModelResponse):
                    raise ValidationError(
                        "ModelProviderAdapter.complete() must return ModelResponse."
                    )
            else:
                action = self.model.decide(request)  # type: ignore[union-attr]
                if not isinstance(action, ModelAction):
                    raise ValidationError(
                        "ModelAdapter.decide() must return ModelAction."
                    )
                response = ModelResponse(
                    action=action,
                    usage=ModelUsage(),
                    provider_metadata={"adapter": "provider_neutral_fake"},
                )
            if response.proposal is not None:
                bound_proposal = bind_proposal(response.proposal, turn)
                return replace(
                    response,
                    action=None,
                    proposal=bound_proposal,
                )
            if response.action is None:
                return response
            return replace(response, action=bind_action(response.action, turn))

        def provider_tool_error(error: ModelProviderError) -> ToolError:
            details: dict[str, Any] = {"outcome": error.outcome}
            if error.provider_request_id is not None:
                details["provider_request_id"] = error.provider_request_id
            return ToolError(
                code=error.code,
                category="provider",
                message=error.message,
                retryable=bool(error.retryable),
                details=details,
                safe_to_expose=True,
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
                tool_results=list(ordered_tool_results),
            )

        def terminal_proposal(
            turn: AgentTurn,
            record: ModelExecutionRecord,
            proposal: ModelTurnProposal,
        ) -> ModelLoopResult:
            nonlocal termination
            if proposal.kind != "final" or proposal.final_answer is None:
                raise ValidationError("Terminal proposal must contain a final answer.")
            turns[turn_index(turn)] = replace(
                turn,
                status="completed",
                model_response_ref=record.response_ref,
            )
            upsert_record(
                replace(
                    record,
                    status="completed",
                    # Preserve the V1 compatibility projection populated at
                    # the durable response boundary.  Multi-call proposals
                    # remain null because no lossless singleton action exists.
                    normalized_action=record.normalized_action,
                    normalized_proposal=proposal.to_dict(),
                    proposal_id=proposal.proposal_id,
                )
            )
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
                final_answer=proposal.final_answer,
                last_observation=observation,
                tool_results=list(ordered_tool_results),
            )

        def execute_tool_proposal(
            turn: AgentTurn,
            record: ModelExecutionRecord,
            proposal: ModelTurnProposal,
            continuation: ProviderTurnContinuation | None,
        ) -> ToolError | None:
            nonlocal observation, previous_tool_call, provider_continuation
            if proposal.kind != "tool_calls":
                return None
            if continuation is not None and (
                continuation.source_turn_id != turn.turn_id
                or not continuation.matches_calls(
                    proposal.proposal_id,
                    proposal.tool_calls,
                )
            ):
                raise ValidationError(
                    "Provider continuation identity does not match the bound proposal."
                )

            current_turn_index = turn_index(turn)
            runtime_call_ids = [
                model_call.runtime_call.call_id
                for model_call in proposal.tool_calls
            ]
            turns[current_turn_index] = replace(
                turns[current_turn_index],
                tool_call_ids=runtime_call_ids,
            )
            record = replace(
                record,
                # Preserve the V1 compatibility projection populated at the
                # durable response boundary.  A multi-call proposal keeps
                # this null; singleton proposals retain their action view.
                normalized_action=record.normalized_action,
                normalized_proposal=proposal.to_dict(),
                proposal_id=proposal.proposal_id,
            )
            upsert_record(record)
            checkpoint_error = publish_model_boundary("proposal_bound")
            if checkpoint_error is not None:
                return checkpoint_error

            model_results: list[ModelToolResult] = []
            result_refs = list(record.tool_result_refs)
            if len(result_refs) > len(proposal.tool_calls):
                raise ValidationError(
                    "Durable result prefix exceeds the proposal call list."
                )
            for ordinal in range(len(result_refs)):
                restored_result = load_model_tool_result(
                    record,
                    proposal,
                    ordinal,
                )
                model_results.append(restored_result)
                ordered_tool_results.append(restored_result.result)
                observation = restored_result.result
                previous_tool_call = proposal.tool_calls[ordinal].runtime_call
                provider_continuation = None

            for ordinal in range(len(result_refs), len(proposal.tool_calls)):
                model_call = proposal.tool_calls[ordinal]
                call = model_call.runtime_call
                existing = ledger.get(call.call_id)
                validate_ledger_identity(existing, call)
                if existing is not None and existing.status == "pending":
                    raise ValidationError(
                        "A pending tool call requires recovery verification."
                    )
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
                        raise ValidationError(
                            "Terminal ledger record has no normalized outcome."
                        )
                else:
                    result = self.tool_runtime.execute(
                        call,
                        self.policy_enforcer,
                        event_sink=event_sink,
                        ledger=ledger,
                        checkpoint_callback=persist_ledger,
                    )

                if is_uncertain_checkpoint(result):
                    upsert_record(replace(record, status="failed"))
                    turns[current_turn_index] = replace(
                        turns[current_turn_index],
                        status="failed",
                    )
                    return result.error

                model_result = ModelToolResult.for_call(
                    run_id=state.run_id,
                    proposal_id=proposal.proposal_id,
                    ordinal=ordinal,
                    model_call=model_call,
                    result=result,
                )
                stored_result = artifact_store.write_tool_result(
                    state.run_id,
                    turn.turn_id,
                    ordinal,
                    call.call_id,
                    {
                        "runtime_identity": {
                            "run_id": state.run_id,
                            "turn_id": turn.turn_id,
                            "task_id": task_id,
                            "agent_id": agent_id,
                            "sequence": turn.sequence,
                        },
                        "model_tool_result": model_result.to_dict(),
                    },
                )
                result_refs.append(
                    {
                        "ordinal": ordinal,
                        "result_id": model_result.result_id,
                        "provider_call_id": model_call.provider_call_id,
                        "runtime_call_id": call.call_id,
                        "tool_id": call.tool_id,
                        "result_ref": stored_result.ref,
                        "result_sha256": stored_result.sha256,
                    }
                )
                model_results.append(model_result)
                ordered_tool_results.append(result)
                observation = result
                previous_tool_call = call
                provider_continuation = None
                status = (
                    "tool_results_durable"
                    if len(result_refs) == len(proposal.tool_calls)
                    else "tool_results_partial"
                )
                if status == "tool_results_durable":
                    turns[current_turn_index] = replace(
                        turns[current_turn_index],
                        status="completed",
                    )
                record = replace(
                    record,
                    status=status,
                    tool_result_refs=list(result_refs),
                )
                upsert_record(record)
                checkpoint_error = publish_model_boundary(status)
                if checkpoint_error is not None:
                    return checkpoint_error

            completed_tool_turns.append(
                ModelToolTurn(
                    proposal_id=proposal.proposal_id,
                    source_turn_id=turn.turn_id,
                    source_sequence=turn.sequence,
                    tool_calls=list(proposal.tool_calls),
                    tool_results=model_results,
                    provider_continuation=continuation,
                )
            )
            return None

        def execute_tool_action(
            turn: AgentTurn,
            record: ModelExecutionRecord,
            action: ModelAction,
            continuation: ProviderContinuation | None,
        ) -> ToolResult | None:
            nonlocal observation, observation_ref, previous_tool_call, provider_continuation
            bound = bind_action(action, turn)
            if bound.kind != "tool_call" or bound.tool_call is None:
                return None
            call = bound.tool_call
            if continuation is not None and (
                continuation.source_turn_id != turn.turn_id
                or not continuation.matches_tool_call(call)
            ):
                raise ValidationError(
                    "Provider continuation identity does not match the bound tool call."
                )
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
            previous_tool_call = call
            provider_continuation = continuation
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
                        terminal_response = load_response(record)
                        if terminal_response.proposal is not None:
                            if terminal_response.proposal.kind != "final":
                                raise ValidationError(
                                    "Terminal V2 model response is not a final proposal."
                                )
                            return ModelLoopResult(
                                status="completed",
                                state=current_state,
                                final_answer=terminal_response.proposal.final_answer,
                            )
                        final_action = terminal_response.action
                        if final_action is not None and final_action.kind == "final":
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
                    pending_response = load_response(record)
                    if (
                        pending_response.proposal is not None
                        and pending_response.proposal.kind == "tool_calls"
                    ):
                        completed_tool_turns = list(load_request(record).tool_turns)
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
                elif decision.decision == "resume_durable_proposal":
                    if record is None:
                        raise ValidationError(
                            "No durable model record is available for proposal resume."
                        )
                    pending_response = load_response(record)
                    completed_tool_turns = list(load_request(record).tool_turns)
                    pending_record = record
                    matching_turn = next(
                        (turn for turn in turns if turn.turn_id == record.turn_id),
                        None,
                    )
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
                elif decision.decision == "resume_from_tool_results":
                    if record is None:
                        raise ValidationError(
                            "No durable model record is available for result resume."
                        )
                    restored_response = load_response(record)
                    completed_tool_turns = list(load_request(record).tool_turns)
                    restored_proposal = restored_response.proposal
                    if (
                        restored_proposal is None
                        or restored_proposal.kind != "tool_calls"
                    ):
                        raise ValidationError(
                            "Durable result resume requires a tool-call proposal."
                        )
                    matching_turn = next(
                        (turn for turn in turns if turn.turn_id == record.turn_id),
                        None,
                    )
                    if matching_turn is None:
                        matching_turn = AgentTurn(
                            turn_id=record.turn_id,
                            run_id=state.run_id,
                            task_id=task_id,
                            agent_id=agent_id,
                            sequence=record.sequence,
                            status="completed",
                            tool_call_ids=[
                                call.runtime_call.call_id
                                for call in restored_proposal.tool_calls
                            ],
                            model_response_ref=record.response_ref,
                        )
                        turns.append(matching_turn)
                    proposal_error = execute_tool_proposal(
                        matching_turn,
                        record,
                        restored_proposal,
                        restored_response.provider_turn_continuation,
                    )
                    if proposal_error is not None:
                        return blocked_result(proposal_error)
                elif decision.decision == "resume_from_tool_result":
                    if record is None:
                        raise ValidationError("No durable model record is available for observation resume.")
                    validated_response = load_response(record)
                    validated_action = validated_response.action
                    if (
                        validated_action is None
                        or validated_action.kind != "tool_call"
                        or validated_action.tool_call is None
                    ):
                        raise ValidationError(
                            "Durable tool observation requires a validated ToolCall action."
                        )
                    validated_call = validated_action.tool_call
                    observation = load_observation(record, validated_call)
                    observation_ref = record.observation_ref
                    previous_tool_call = validated_call
                    provider_continuation = validated_response.provider_continuation
                elif decision.decision == "requires_model_reinvoke":
                    raise ValidationError("Explicit model reinvocation requires a new request record.")
                elif decision.decision == "safe_to_invoke_model" and record is not None:
                    if record.status == "request_durable":
                        completed_tool_turns = list(load_request(record).tool_turns)
                    forced_record = record
                    forced_turn = next((turn for turn in turns if turn.turn_id == record.turn_id), None)

            next_sequence = max((turn.sequence for turn in turns), default=0) + 1
            model_steps = 0
            while model_steps < self.max_steps:
                if pending_response is not None and forced_turn is not None:
                    turn = forced_turn
                    record = pending_record
                    response_envelope = pending_response
                    pending_response = None
                    pending_record = None
                    forced_turn = None
                    if record is None:
                        raise ValidationError("Durable response has no model execution record.")
                    if response_envelope.error is not None:
                        upsert_record(replace(record, status="failed"))
                        return failed_result(provider_tool_error(response_envelope.error))
                    proposal = response_envelope.proposal
                    if proposal is not None:
                        if proposal.kind == "final":
                            return terminal_proposal(turn, record, proposal)
                        proposal_error = execute_tool_proposal(
                            turn,
                            record,
                            proposal,
                            response_envelope.provider_turn_continuation,
                        )
                        if proposal_error is not None:
                            return blocked_result(proposal_error)
                    else:
                        action = response_envelope.action
                        if action is None:
                            raise ValidationError(
                                "Durable response has no proposal, action, or provider error."
                            )
                        if action.kind == "final":
                            return terminal_final(turn, record, action)
                        tool_result = execute_tool_action(
                            turn,
                            record,
                            action,
                            response_envelope.provider_continuation,
                        )
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
                    # Once a V2 proposal has completed, its whole model-turn
                    # grouping is the canonical Provider history—even for a
                    # singleton proposal.  Legacy observation fields are used
                    # only by the pre-V2 ModelAction path.
                    grouped_history = bool(completed_tool_turns)
                    request = ModelTurnRequest(
                        run_id=state.run_id,
                        turn_id=turn.turn_id,
                        task_id=task_id,
                        agent_id=agent_id,
                        sequence=turn.sequence,
                        user_input=user_input,
                        observation=None if grouped_history else observation,
                        available_tools=list(tool_definitions),
                        previous_tool_call=None if grouped_history else previous_tool_call,
                        provider_continuation=(
                            None if grouped_history else provider_continuation
                        ),
                        evidence_context=self._verified_evidence_context(
                            ledger,
                            run_id=state.run_id,
                            task_id=task_id,
                            agent_id=agent_id,
                            before_sequence=turn.sequence,
                        ),
                        tool_history=(
                            []
                            if grouped_history
                            else self._completed_tool_history(
                                model_records,
                                ledger,
                                run_id=state.run_id,
                                task_id=task_id,
                                agent_id=agent_id,
                                before_sequence=turn.sequence,
                                current_call_id=(
                                    previous_tool_call.call_id
                                    if previous_tool_call is not None
                                    else None
                                ),
                            )
                        ),
                        tool_turns=(
                            select_bounded_model_tool_turns(completed_tool_turns)
                            if grouped_history
                            else []
                        ),
                    )
                    tools_artifact = tool_definitions_artifact(turn.turn_id)
                    request_ref_artifact = request_artifact(
                        turn,
                        request,
                        tools_artifact.ref,
                        None if grouped_history else observation_ref,
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
                    checkpoint_error = publish_model_boundary("request_durable")
                    if checkpoint_error is not None:
                        return blocked_result(checkpoint_error)
                else:
                    request = load_request(record)
                record = replace(record, status="request_sent")
                upsert_record(record)
                checkpoint_error = publish_model_boundary("request_sent")
                if checkpoint_error is not None:
                    return blocked_result(checkpoint_error)
                try:
                    response_envelope = invoke_model(request, turn)
                except ValidationError as error:
                    upsert_record(replace(record, status="failed"))
                    if error.details.get("reason") == "tool_call_sequence_mismatch":
                        return failed_result(
                            self._error(
                                "MODEL_TOOL_CALL_SEQUENCE_MISMATCH",
                                "schema",
                                "Provider ToolCall sequence does not match the current runtime turn.",
                                {
                                    "turn_id": turn.turn_id,
                                    "expected_sequence": error.details.get("expected_sequence"),
                                    "received_sequence": error.details.get("received_sequence"),
                                },
                            )
                        )
                    return failed_result(
                        self._error(
                            "MODEL_ADAPTER_FAILED",
                            "runtime",
                            "Model adapter failed to produce a safe response.",
                            {"turn_id": turn.turn_id},
                        )
                    )
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
                record = replace(record, status="response_obtained")
                upsert_record(record)
                checkpoint_error = publish_model_boundary("response_obtained")
                if checkpoint_error is not None:
                    return blocked_result(checkpoint_error)
                response = response_artifact(turn, response_envelope)
                # ModelResponse.__post_init__ provides a V1 action projection
                # for final/singleton proposals.  Persist it even when the
                # canonical response is represented as a V2 proposal.
                projected_action = compatibility_action(response_envelope)
                normalized_action = (
                    projected_action.to_dict()
                    if projected_action is not None
                    else None
                )
                normalized_proposal = (
                    response_envelope.proposal.to_dict()
                    if response_envelope.proposal is not None
                    else None
                )
                provider_error = (
                    response_envelope.error.to_dict()
                    if response_envelope.error is not None
                    else None
                )
                record = replace(
                    record,
                    status="response_durable",
                    response_ref=response.ref,
                    response_sha256=response.sha256,
                    normalized_action=normalized_action,
                    normalized_proposal=normalized_proposal,
                    proposal_id=(
                        response_envelope.proposal.proposal_id
                        if response_envelope.proposal is not None
                        else None
                    ),
                    usage=response_envelope.usage.to_dict(),
                    provider_metadata=dict(response_envelope.provider_metadata),
                    provider_request_id=projected_provider_request_id(response_envelope),
                    provider_response_id=response_envelope.provider_response_id,
                    finish_reason=response_envelope.finish_reason,
                    provider_error=provider_error,
                    response_origin=(
                        "provider" if self._has_provider_complete else "legacy"
                    ),
                )
                upsert_record(record)
                turns[turn_index(turn)] = replace(
                    turn,
                    model_response_ref=response.ref,
                    usage=response_envelope.usage.to_dict(),
                )
                checkpoint_error = publish_model_boundary("response_durable")
                if checkpoint_error is not None:
                    return blocked_result(checkpoint_error)

                if response_envelope.error is not None:
                    upsert_record(replace(record, status="failed"))
                    return failed_result(provider_tool_error(response_envelope.error))

                bound_proposal = response_envelope.proposal
                if bound_proposal is not None:
                    if bound_proposal.kind == "final":
                        return terminal_proposal(
                            turns[turn_index(turn)],
                            record,
                            bound_proposal,
                        )
                    proposal_error = execute_tool_proposal(
                        turns[turn_index(turn)],
                        record,
                        bound_proposal,
                        response_envelope.provider_turn_continuation,
                    )
                    if proposal_error is not None:
                        return blocked_result(proposal_error)
                else:
                    bound_action = response_envelope.action
                    if bound_action is None:
                        raise ValidationError(
                            "Model response has no proposal, action, or provider error."
                        )
                    if bound_action.kind == "final":
                        return terminal_final(
                            turns[turn_index(turn)],
                            record,
                            bound_action,
                        )
                    tool_result = execute_tool_action(
                        turns[turn_index(turn)],
                        record,
                        bound_action,
                        response_envelope.provider_continuation,
                    )
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
        except ValidationError as error:
            recovery_reason = error.details.get("reason")
            if recovery_reason in {
                "provider_response_artifact_missing",
                "legacy_response_artifact_missing",
                "provider_response_envelope_missing",
                "hash_mismatch",
                "response_origin_missing",
            }:
                recovery_message = (
                    "Durable model response origin could not be verified."
                    if recovery_reason == "response_origin_missing"
                    else (
                        "Durable legacy response artifact could not be verified."
                        if recovery_reason == "legacy_response_artifact_missing"
                        else "Durable provider response artifact could not be verified."
                    )
                )
                return failed_result(
                    self._error(
                        "MODEL_LOOP_RUNTIME_FAILED",
                        "runtime",
                        recovery_message,
                        {
                            "run_id": state.run_id,
                            "reason": recovery_reason,
                        },
                    )
                )
            return failed_result(
                self._error(
                    "MODEL_LOOP_RUNTIME_FAILED",
                    "runtime",
                    "The durable model loop failed safely.",
                    {"run_id": state.run_id},
                )
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
