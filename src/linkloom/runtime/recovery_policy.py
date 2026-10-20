"""Pure, bounded recovery recommendations for the existing model loop."""

from __future__ import annotations

from dataclasses import dataclass

from linkloom.agents.model_adapter import ModelProviderError
from linkloom.runtime.progress import ProgressAssessment
from linkloom.runtime.recovery import ModelResumeDecision
from linkloom.tools.contracts import ToolError


_TERMINAL_TOOL_ERRORS = frozenset({
    "TOOL_PERMISSION_DENIED",
    "TOOL_UNKNOWN",
    "TOOL_INVALID_OUTPUT",
    "TOOL_LEDGER_CONFLICT",
    "TOOL_PENDING_CHECKPOINT_FAILED",
    "TOOL_TERMINAL_CHECKPOINT_FAILED",
})


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason_code: str
    hint: str | None = None


def decide_recovery(
    progress: ProgressAssessment,
    *,
    error: ToolError | None = None,
    provider_error: ModelProviderError | None = None,
    resume_decision: ModelResumeDecision | None = None,
) -> RecoveryDecision:
    """Choose a next-step constraint, never a model or tool invocation."""
    if resume_decision is not None and resume_decision.decision in {
        "requires_verification", "requires_manual_decision",
    }:
        return RecoveryDecision("REQUIRE_VERIFICATION", resume_decision.reason_code)
    if provider_error is not None:
        if provider_error.retryable and provider_error.outcome == "known_failure":
            return RecoveryDecision("DEFER_TO_PROVIDER_RETRY", provider_error.code)
        return RecoveryDecision("TERMINATE", provider_error.code)
    terminal_code = error.code if error is not None else progress.latest_error_code
    if terminal_code in _TERMINAL_TOOL_ERRORS:
        return RecoveryDecision("TERMINATE", terminal_code)
    if terminal_code == "TOOL_BUDGET_EXCEEDED":
        return RecoveryDecision(
            "STOP_TOOL_USE", terminal_code,
            "The tool budget is exhausted. Finish using already verified evidence.",
        )
    if terminal_code == "TOOL_PRECONDITION_FAILED":
        return RecoveryDecision(
            "READ_PARENT_EVIDENCE", terminal_code,
            "Read an evidence_id returned by the parent search before making a dependent search.",
        )
    if progress.reason_code == "VERIFIED_SOURCE_CONFLICT" and progress.conflicted_subgoals:
        slots = ", ".join(progress.conflicted_subgoals)
        if len(slots) > 120:
            slots = f"{len(progress.conflicted_subgoals)} verified subgoals"
        refs = sorted(progress.conflict_evidence_ids)
        cited = ", ".join(refs) if len(refs) <= 2 else "the verified read evidence IDs"
        return RecoveryDecision(
            "FINAL_CONFLICT", "VERIFIED_SOURCE_CONFLICT",
            f"Verified current sources conflict for subgoal(s) {slots}. "
            f"Stop tools. Produce Final with conflicting_evidence, cite both supporting sources from {cited}, "
            "and leave disputed dependent conclusions unknown.",
        )
    if progress.consecutive_tool_errors >= 2:
        return RecoveryDecision("TERMINATE", "REPEATED_TOOL_ERROR")
    if progress.consecutive_schema_errors >= 2:
        return RecoveryDecision("TERMINATE", "SCHEMA_CORRECTION_EXHAUSTED")
    if progress.consecutive_schema_errors == 1:
        return RecoveryDecision(
            "CORRECT_ARGUMENTS", "SCHEMA_CORRECTION_NEEDED",
            "Correct the tool arguments against its schema once; do not repeat the same invalid call.",
        )
    if progress.pending_alternate_subgoals:
        slots = ", ".join(progress.pending_alternate_subgoals)
        return RecoveryDecision(
            "ADJUST_SEARCH", "PLAN_ALTERNATE_PENDING",
            f"Required relation evidence is still missing for {slots}. "
            "A distinct alternate_question is already in the validated TaskPlan. "
            "Try its alternate query once before an insufficient-evidence Final; "
            "do not repeat the primary search or use an unverified entity.",
        )
    if progress.status == "CONFIRMED_STALL" and progress.reason_code == "SEARCH_NO_NEW_EVIDENCE":
        return RecoveryDecision(
            "STOP_SEARCH_STRATEGY", "SEARCH_NO_NEW_EVIDENCE",
            "Stop repeating this search strategy. One different query is available within the existing budget; otherwise read verified evidence or finish with the available evidence.",
        )
    if progress.reason_code == "SEARCH_NO_NEW_EVIDENCE":
        return RecoveryDecision(
            "ADJUST_SEARCH", "SEARCH_NO_NEW_EVIDENCE",
            "The search found no new evidence. Adjust the search strategy or use already verified evidence.",
        )
    return RecoveryDecision("CONTINUE", progress.reason_code)


__all__ = ["RecoveryDecision", "decide_recovery"]
