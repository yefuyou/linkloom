"""Observable progress facts reconstructed from a durable run."""

from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.runtime.progress import assess_progress
from linkloom.runtime.recovery_policy import decide_recovery
from linkloom.tools.contracts import ToolCall, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger


def _state(records, *, run_id="run_progress", workspace="workspace_a", workflow="ask"):
    return RuntimeState(
        schema_version=1,
        run_id=run_id,
        thread_id=f"thread_{run_id}",
        workflow=workflow,
        status="running",
        step_seq=1,
        request_ref=f"request_{run_id}.json",
        source=SourceContext(
            index_path="fixture/index.json",
            index_sha256="e" * 64,
            vault_root_fingerprint=workspace,
        ),
        tool_ledger=records,
    )


def _record(ledger, ordinal, tool_id, arguments, value):
    call = ToolCall(
        f"call_{ordinal}", tool_id, arguments,
        run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
        sequence=ordinal,
    )
    ledger.record_pending(call)
    ledger.record_completed(call, ToolResult(call.call_id, tool_id, "ok", value=value))


def _evidence(evidence_id):
    return {"evidence_id": evidence_id}


def test_distinct_queries_returning_the_same_evidence_confirm_stall():
    ledger = ToolExecutionLedger()
    _record(ledger, 1, "search_notes", {"query": "A"}, [_evidence("ev_1")])
    assert assess_progress(_state(ledger.to_list())).status == "PROGRESS"

    _record(ledger, 2, "search_notes", {"query": "B"}, [_evidence("ev_1")])
    suspected = assess_progress(_state(ledger.to_list()))
    assert suspected.status == "SUSPECTED_STALL"
    assert suspected.reason_code == "SEARCH_NO_NEW_EVIDENCE"

    _record(ledger, 3, "search_notes", {"query": "C"}, [_evidence("ev_1")])
    confirmed = assess_progress(_state(ledger.to_list()))
    assert confirmed.status == "CONFIRMED_STALL"
    assert confirmed.consecutive_no_progress == 2
    assert confirmed.evidence_ids == frozenset({"ev_1"})


def test_new_evidence_and_verified_read_reset_the_stall():
    ledger = ToolExecutionLedger()
    _record(ledger, 1, "search_notes", {"query": "A"}, [_evidence("ev_1")])
    _record(ledger, 2, "search_notes", {"query": "B"}, [_evidence("ev_1")])
    _record(ledger, 3, "search_notes", {"query": "C"}, [_evidence("ev_1"), _evidence("ev_2")])
    progressed = assess_progress(_state(ledger.to_list()))
    assert progressed.status == "PROGRESS"
    assert progressed.reason_code == "NEW_EVIDENCE"
    assert progressed.consecutive_no_progress == 0

    _record(ledger, 4, "search_notes", {"query": "D"}, [_evidence("ev_2")])
    assert assess_progress(_state(ledger.to_list())).status == "SUSPECTED_STALL"
    _record(ledger, 5, "read_verified_note", {"note_ref": "ev_1"}, _evidence("ev_1"))
    read = assess_progress(_state(ledger.to_list()))
    assert read.status == "PROGRESS"
    assert read.reason_code == "VERIFIED_READ"
    assert read.consecutive_no_progress == 0


def test_repeated_nonledger_schema_error_is_detected_without_tool_execution():
    error = ToolError(
        "TOOL_INVALID_ARGUMENTS", "input", "Invalid arguments.", retryable=False,
    )
    observations = []
    for ordinal in (1, 2):
        call = ToolCall(
            f"invalid_{ordinal}", "search_notes", {"query": 42},
            run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
            sequence=ordinal,
        )
        observations.append((call, ToolResult(call.call_id, call.tool_id, "error", error=error)))

    state = _state([])
    assert assess_progress(state, observations=observations[:1]).status == "SUSPECTED_STALL"
    repeated = assess_progress(state, observations=observations)
    assert repeated.status == "CONFIRMED_STALL"
    assert repeated.reason_code == "REPEATED_SCHEMA_ERROR"
    assert repeated.consecutive_schema_errors == 2


def test_cached_repeat_is_not_an_additional_effective_search():
    ledger = ToolExecutionLedger()
    _record(ledger, 1, "search_notes", {"query": "A"}, [])
    repeat = ToolCall(
        "cached_repeat", "search_notes", {"query": "A"},
        run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
        sequence=2,
    )
    observed = ToolResult(
        repeat.call_id, repeat.tool_id, "ok", value=[],
        business_status="REPEATED_NO_PROGRESS",
    )
    assessment = assess_progress(
        _state(ledger.to_list()), observations=[(repeat, observed)],
    )
    assert assessment.status == "SUSPECTED_STALL"
    assert assessment.consecutive_no_progress == 1


def test_multi_call_turn_keeps_observed_order_when_schema_error_precedes_valid_search():
    invalid = ToolCall(
        "invalid_first", "search_notes", {"query": 42},
        run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
        sequence=1,
    )
    bad = ToolResult(
        invalid.call_id, invalid.tool_id, "error",
        error=ToolError("TOOL_INVALID_ARGUMENTS", "input", "Invalid arguments."),
    )
    ledger = ToolExecutionLedger()
    valid = ToolCall(
        "valid_second", "search_notes", {"query": "B"},
        run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
        sequence=1,
    )
    good = ToolResult(valid.call_id, valid.tool_id, "ok", value=[_evidence("ev_new")])
    ledger.record_pending(valid)
    ledger.record_completed(valid, good)
    assessment = assess_progress(
        _state(ledger.to_list()), observations=[(invalid, bad), (valid, good)],
    )
    assert assessment.status == "PROGRESS"
    assert assessment.consecutive_schema_errors == 0


def test_invalid_arguments_do_not_count_as_an_effective_empty_search():
    invalid = ToolCall(
        "invalid_query", "search_notes", {"query": 42},
        run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
        sequence=1,
    )
    bad = ToolResult(
        invalid.call_id, invalid.tool_id, "error",
        error=ToolError("TOOL_INVALID_ARGUMENTS", "input", "Invalid arguments."),
    )
    ledger = ToolExecutionLedger()
    _record(ledger, 2, "search_notes", {"query": "corrected"}, [])
    assessment = assess_progress(
        _state(ledger.to_list()), observations=[(invalid, bad)],
    )
    assert assessment.status == "SUSPECTED_STALL"
    assert assessment.reason_code == "SEARCH_NO_NEW_EVIDENCE"
    assert assessment.consecutive_no_progress == 1


def test_assessment_survives_checkpoint_reload_and_is_scoped_to_one_run_workspace():
    ledger = ToolExecutionLedger()
    _record(ledger, 1, "search_notes", {"query": "A"}, [])
    _record(ledger, 2, "search_notes", {"query": "B"}, [])
    state = _state(ledger.to_list(), workflow="team_decision")
    restored = RuntimeState.from_dict(state.to_dict())
    assert assess_progress(restored) == assess_progress(state)
    assert assess_progress(restored).status == "CONFIRMED_STALL"

    other_run = _state([], run_id="run_elsewhere", workspace="workspace_b")
    assert assess_progress(other_run).status == "NO_ACTIVITY"


def test_error_only_failed_ledger_record_still_terminates_after_checkpoint_reload():
    call = ToolCall(
        "denied_search", "search_notes", {"query": "A"},
        run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
        sequence=1,
    )
    ledger = ToolExecutionLedger()
    ledger.record_pending(call)
    ledger.record_failed(
        call, ToolError("TOOL_PERMISSION_DENIED", "permission", "Denied."),
    )
    restored = RuntimeState.from_dict(_state(ledger.to_list()).to_dict())
    assessment = assess_progress(restored)
    assert assessment.latest_error_code == "TOOL_PERMISSION_DENIED"
    assert decide_recovery(assessment).action == "TERMINATE"
    assert assessment.evidence_ids == frozenset()


def test_repeated_tool_failures_stop_without_becoming_missing_evidence():
    ledger = ToolExecutionLedger()
    _record(ledger, 1, "search_notes", {"query": "Absent project"}, [])
    for ordinal in (2, 3):
        call = ToolCall(
            f"failed_{ordinal}", "search_notes", {"query": f"retry {ordinal}"},
            run_id="run_progress", task_id="task_progress", agent_id="retrieval_agent",
            sequence=ordinal,
        )
        ledger.record_pending(call)
        ledger.record_failed(call, ToolError("TOOL_EXECUTION_FAILED", "runtime", "Tool failed."))
    first = assess_progress(_state(ledger.to_list()), before_sequence=3)
    assert decide_recovery(first).action == "CONTINUE"
    second = assess_progress(_state(ledger.to_list()))
    assert second.reason_code == "REPEATED_TOOL_ERROR"
    assert decide_recovery(second).action == "TERMINATE"
    assert second.evidence_ids == frozenset()


def test_plan_exhaustion_does_not_use_searches_after_the_assessment_boundary():
    from linkloom.runtime.team_decision_planner import plan_relation_investigation

    plan = plan_relation_investigation('Who is the direct leader of the owner of Cedar Lantern?')
    ledger = ToolExecutionLedger()
    _record(ledger, 2, 'search_notes', {
        'query': plan.subgoals[0].question,
        'source_context': {'subgoal_id': plan.subgoals[0].subgoal_id},
    }, [])
    restored = RuntimeState.from_dict(_state(ledger.to_list()).to_dict())
    kwargs = dict(task_id='task_progress', agent_id='retrieval_agent', task_plan=plan,
                  expected_workspace_id='workspace_a')
    earlier = assess_progress(restored, before_sequence=2, **kwargs)
    assert earlier.exhausted_subgoals == ()
    assert decide_recovery(earlier).action == 'CONTINUE'
    exhausted = assess_progress(restored, before_sequence=3, **kwargs)
    assert exhausted.exhausted_subgoals == tuple(item.subgoal_id for item in plan.subgoals)
    assert decide_recovery(exhausted).action == 'FINAL_PARTIAL'
