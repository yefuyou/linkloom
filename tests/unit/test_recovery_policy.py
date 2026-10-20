"""Bounded recovery decisions, without invoking a model or tool."""

from linkloom.agents.model_adapter import ModelProviderError
from linkloom.runtime.progress import ProgressAssessment
from linkloom.runtime.recovery import ModelResumeDecision
from linkloom.runtime.recovery_policy import decide_recovery
from linkloom.tools.contracts import ToolError


def _progress(status, reason, *, no_progress=0, schema_errors=0):
    return ProgressAssessment(
        status, reason,
        consecutive_no_progress=no_progress,
        consecutive_schema_errors=schema_errors,
    )


def test_one_argument_correction_is_allowed_and_second_error_terminates():
    first = decide_recovery(_progress("SUSPECTED_STALL", "SCHEMA_CORRECTION_NEEDED", schema_errors=1))
    assert first.action == "CORRECT_ARGUMENTS"
    assert first.hint is not None
    second = decide_recovery(_progress("CONFIRMED_STALL", "REPEATED_SCHEMA_ERROR", schema_errors=2))
    assert second.action == "TERMINATE"


def test_empty_search_suggests_adjustment_but_confirmed_stall_stops_search():
    first = decide_recovery(_progress("SUSPECTED_STALL", "SEARCH_NO_NEW_EVIDENCE", no_progress=1))
    assert first.action == "ADJUST_SEARCH"
    assert "search" in first.hint.lower()
    second = decide_recovery(_progress("CONFIRMED_STALL", "SEARCH_NO_NEW_EVIDENCE", no_progress=2))
    assert second.action == "STOP_SEARCH_STRATEGY"
    assert "read" in second.hint.lower()


def test_new_evidence_or_read_keeps_normal_search_available():
    for reason in ("NEW_EVIDENCE", "VERIFIED_READ"):
        assert decide_recovery(_progress("PROGRESS", reason)).action == "CONTINUE"


def test_permission_failure_terminates_and_budget_allows_a_final_answer_only():
    progress = _progress("SUSPECTED_STALL", "TOOL_ERROR")
    denied = ToolError("TOOL_PERMISSION_DENIED", "permission", "Denied.")
    assert decide_recovery(progress, error=denied).action == "TERMINATE"
    exhausted = ToolError("TOOL_BUDGET_EXCEEDED", "budget", "Exhausted.")
    assert decide_recovery(progress, error=exhausted).action == "STOP_TOOL_USE"


def test_provider_retry_classification_never_authorizes_unknown_outcome():
    progress = _progress("NO_ACTIVITY", "NO_TOOL_ACTIVITY")
    known = ModelProviderError(
        "MODEL_RATE_LIMITED", "rate_limit", "Rate limited.",
        outcome="known_failure",
    )
    unknown = ModelProviderError(
        "MODEL_RATE_LIMITED", "rate_limit", "Outcome unknown.",
        outcome="unknown_provider_outcome",
    )
    assert decide_recovery(progress, provider_error=known).action == "DEFER_TO_PROVIDER_RETRY"
    assert decide_recovery(progress, provider_error=unknown).action == "TERMINATE"


def test_uncertain_checkpoint_recovery_takes_priority_over_tool_progress():
    progress = _progress("PROGRESS", "NEW_EVIDENCE")
    pending = ModelResumeDecision(
        "requires_verification", "TOOL_PENDING_UNCERTAIN",
        "Verify the pending call.", "run_progress",
    )
    decision = decide_recovery(progress, resume_decision=pending)
    assert decision.action == "REQUIRE_VERIFICATION"
    assert decision.reason_code == "TOOL_PENDING_UNCERTAIN"
