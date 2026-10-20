"""Pure resume decisions for durable single-agent model turns."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ModelExecutionRecord, RuntimeState, _assert_json_safe_primitive
from linkloom.tools.ledger import ToolExecutionLedger


MODEL_RESUME_DECISIONS = frozenset(
    {
        "safe_to_invoke_model",
        "reuse_durable_model_response",
        "requires_model_reinvoke",
        "requires_verification",
        "requires_manual_decision",
        "resume_from_tool_result",
        "resume_durable_proposal",
        "resume_from_tool_results",
        "already_terminal",
    }
)


@dataclass(frozen=True)
class ModelResumeDecision:
    """A recommendation only; it never invokes a model or a tool."""

    decision: str
    reason_code: str
    message: str
    run_id: str
    turn_id: str | None = None
    call_id: str | None = None
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.decision not in MODEL_RESUME_DECISIONS:
            raise ValidationError("ModelResumeDecision.decision is not supported.")
        for name, value in (("reason_code", self.reason_code), ("message", self.message), ("run_id", self.run_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"ModelResumeDecision.{name} must be non-empty text.")
        if self.turn_id is not None and (not isinstance(self.turn_id, str) or not self.turn_id.strip()):
            raise ValidationError("ModelResumeDecision.turn_id must be text or null.")
        if self.call_id is not None and (not isinstance(self.call_id, str) or not self.call_id.strip()):
            raise ValidationError("ModelResumeDecision.call_id must be text or null.")
        if self.details is not None:
            if not isinstance(self.details, dict):
                raise ValidationError("ModelResumeDecision.details must be an object or null.")
            _assert_json_safe_primitive(self.details, "ModelResumeDecision.details")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latest_record(state: RuntimeState) -> ModelExecutionRecord | None:
    if not state.model_executions:
        return None
    return max(state.model_executions, key=lambda record: (record.sequence, record.turn_id))


def _action_call_id(record: ModelExecutionRecord) -> str | None:
    action = record.normalized_action
    if not isinstance(action, dict) or action.get("kind") != "tool_call":
        return None
    proposal = action.get("tool_call")
    if not isinstance(proposal, dict):
        return None
    call_id = proposal.get("call_id")
    return call_id if isinstance(call_id, str) and call_id.strip() else None


def _proposal_call_ids(record: ModelExecutionRecord) -> list[str] | None:
    proposal = record.normalized_proposal
    if not isinstance(proposal, dict) or proposal.get("kind") != "tool_calls":
        return None
    raw_calls = proposal.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return None
    call_ids = []
    for item in raw_calls:
        if not isinstance(item, dict):
            return None
        runtime_call = item.get("runtime_call")
        call_id = runtime_call.get("call_id") if isinstance(runtime_call, dict) else None
        if not isinstance(call_id, str) or not call_id.strip():
            return None
        call_ids.append(call_id)
    return call_ids


def _decision(
    state: RuntimeState,
    decision: str,
    reason_code: str,
    message: str,
    record: ModelExecutionRecord | None = None,
    call_id: str | None = None,
    **details: Any,
) -> ModelResumeDecision:
    return ModelResumeDecision(
        decision=decision,
        reason_code=reason_code,
        message=message,
        run_id=state.run_id,
        turn_id=record.turn_id if record else None,
        call_id=call_id,
        details=details or None,
    )


def decide_model_resume(
    state: RuntimeState,
    *,
    ledger: ToolExecutionLedger | None = None,
) -> ModelResumeDecision:
    """Map persisted runtime state to a safe resume recommendation."""
    if not isinstance(state, RuntimeState):
        raise ValidationError("Model resume requires RuntimeState.")
    if ledger is None:
        ledger = ToolExecutionLedger.from_list(state.tool_ledger)
    if not isinstance(ledger, ToolExecutionLedger):
        raise ValidationError("Model resume ledger must be ToolExecutionLedger or None.")

    if state.termination is not None and state.termination.status != "running":
        return _decision(
            state,
            "already_terminal",
            "termination_already_set",
            "The runtime termination state is already final.",
        )

    record = _latest_record(state)
    if record is None or record.status == "request_durable":
        return _decision(
            state,
            "safe_to_invoke_model",
            "request_durable_before_invoke",
            "A durable request exists and no model invocation is recorded.",
            record,
        )
    if record.status in {"request_sent", "response_obtained"}:
        return _decision(
            state,
            "requires_verification",
            "model_outcome_ambiguous",
            "The persisted lifecycle cannot prove whether a provider response exists.",
            record,
        )
    if record.status == "reinvoke_allowed":
        return _decision(
            state,
            "requires_model_reinvoke",
            "request_explicitly_not_sent",
            "An external verifier explicitly marked the model request safe to invoke again.",
            record,
        )

    proposal_call_ids = _proposal_call_ids(record)
    if record.status in {
        "response_durable",
        "tool_results_partial",
        "tool_results_durable",
    } and proposal_call_ids is not None:
        completed_count = len(record.tool_result_refs)
        if completed_count > len(proposal_call_ids):
            return _decision(
                state,
                "requires_manual_decision",
                "proposal_result_prefix_invalid",
                "The durable proposal result prefix is longer than its call list.",
                record,
            )
        if completed_count == len(proposal_call_ids):
            return _decision(
                state,
                "resume_from_tool_results",
                "tool_results_already_durable",
                "The complete ordered tool result set is durable.",
                record,
            )
        next_call_id = proposal_call_ids[completed_count]
        existing = ledger.get(next_call_id)
        if existing is not None and existing.status == "pending":
            return _decision(
                state,
                "requires_verification",
                "tool_outcome_ambiguous",
                "The next proposal tool call is pending with an unknown outcome.",
                record,
                next_call_id,
            )
        return _decision(
            state,
            "resume_durable_proposal",
            "proposal_prefix_durable",
            "The durable proposal can continue from its verified result prefix.",
            record,
            next_call_id,
            completed_count=completed_count,
        )

    call_id = _action_call_id(record)
    if record.status == "response_durable":
        if call_id is None:
            return _decision(
                state,
                "reuse_durable_model_response",
                "durable_final_or_normalized_action",
                "A normalized model response is durable and can be reused by the runtime.",
                record,
            )
        existing = ledger.get(call_id)
        if existing is None:
            return _decision(
                state,
                "reuse_durable_model_response",
                "tool_call_not_started",
                "A durable tool proposal exists and its tool call has not started.",
                record,
                call_id,
            )
        if existing.status == "pending":
            return _decision(
                state,
                "requires_verification",
                "tool_outcome_ambiguous",
                "A tool call is pending with an unknown external outcome.",
                record,
                call_id,
            )
        return _decision(
            state,
            "resume_from_tool_result",
            "tool_result_already_durable",
            "A terminal tool result is already in the checkpoint ledger.",
            record,
            call_id,
        )
    if record.status == "tool_result_durable":
        return _decision(
            state,
            "resume_from_tool_result",
            "observation_already_durable",
            "The tool result is durable and can become the next model observation.",
            record,
            call_id,
        )
    if record.status == "completed":
        return _decision(
            state,
            "already_terminal",
            "model_execution_completed",
            "The model execution record is already terminal.",
            record,
        )
    return _decision(
        state,
        "requires_manual_decision",
        "model_execution_failed_or_invalid",
        "The persisted model execution cannot be resumed automatically.",
        record,
    )


__all__ = ["MODEL_RESUME_DECISIONS", "ModelResumeDecision", "decide_model_resume"]
