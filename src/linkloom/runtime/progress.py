"""Deterministic progress guard for durable, read-only model proposals."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Sequence

from linkloom.runtime.models import RuntimeState
from linkloom.runtime.task_plan import (
    TaskPlan, assess_task_plan, conflicted_subgoals, pending_alternate_subgoals,
    refusal_missing_subgoals,
)
from linkloom.agents.claim_grounding import verified_subgoal_conflict_refs
from linkloom.tools.contracts import ToolCall, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger


REPEATED_NO_PROGRESS = "REPEATED_NO_PROGRESS"


@dataclass(frozen=True)
class ProgressAssessment:
    """A scoped, recomputable description of observable tool progress."""

    status: str
    reason_code: str
    consecutive_no_progress: int = 0
    consecutive_schema_errors: int = 0
    consecutive_tool_errors: int = 0
    evidence_ids: frozenset[str] = frozenset()
    latest_tool_id: str | None = None
    latest_error_code: str | None = None
    conflicted_subgoals: tuple[str, ...] = ()
    conflict_evidence_ids: frozenset[str] = frozenset()
    pending_alternate_subgoals: tuple[str, ...] = ()
    exhausted_subgoals: tuple[str, ...] = ()
    latest_error_path: str | None = None


def assess_progress(
    state: RuntimeState,
    *,
    task_id: str | None = None,
    agent_id: str | None = None,
    before_sequence: int | None = None,
    observations: Sequence[tuple[ToolCall, ToolResult]] = (),
    task_plan: TaskPlan | None = None,
    expected_workspace_id: str | None = None,
) -> ProgressAssessment:
    """Derive progress from this checkpoint's ledger and non-ledger results.

    The RuntimeState owns the run and source snapshot boundary. Schema errors
    occur before ToolRuntime creates a ledger record, so callers may supply
    their already-durable model observations for those cases. No tool executes.
    """
    if not isinstance(state, RuntimeState):
        raise TypeError("Progress assessment requires RuntimeState.")

    ledger_events: dict[str, tuple[int, ToolCall, ToolResult]] = {}
    for order, record in enumerate(state.tool_ledger):
        if (
            record.run_id != state.run_id
            or (task_id is not None and record.task_id != task_id)
            or (agent_id is not None and record.agent_id != agent_id)
            or (before_sequence is not None and record.sequence >= before_sequence)
            or record.status not in {"completed", "failed"}
        ):
            continue
        call = ToolCall(
            record.call_id, record.tool_id, record.arguments,
            run_id=record.run_id, task_id=record.task_id,
            agent_id=record.agent_id, sequence=record.sequence,
        )
        if record.result is not None:
            result = ToolResult.from_dict(record.result)
        elif record.status == "failed" and record.error is not None:
            result = ToolResult(
                record.call_id, record.tool_id, "error",
                error=ToolError.from_dict(record.error),
            )
        else:
            continue
        if result.call_id != call.call_id or result.tool_id != call.tool_id:
            continue
        ledger_events[call.call_id] = (order, call, result)

    events: list[tuple[int, int, ToolCall, ToolResult]] = []
    observed_ids: set[str] = set()
    for order, (call, result) in enumerate(observations):
        if (
            call.run_id != state.run_id
            or (task_id is not None and call.task_id != task_id)
            or (agent_id is not None and call.agent_id != agent_id)
            or (before_sequence is not None and call.sequence >= before_sequence)
            or result.call_id != call.call_id
            or result.tool_id != call.tool_id
            or call.call_id in observed_ids
        ):
            continue
        ledger_event = ledger_events.get(call.call_id)
        if ledger_event is not None:
            _, ledger_call, ledger_result = ledger_event
            if call != ledger_call or result != ledger_result:
                continue
            call, result = ledger_call, ledger_result
        observed_ids.add(call.call_id)
        events.append((call.sequence, order, call, result))
    for call_id, (order, call, result) in ledger_events.items():
        if call_id not in observed_ids:
            events.append((call.sequence, len(observations) + order, call, result))
    events.sort(key=lambda item: (item[0], item[1]))

    evidence_ids: set[str] = set()
    no_progress = 0
    schema_errors = 0
    tool_errors = 0
    previous_schema: tuple[str, str] | None = None
    status = "NO_ACTIVITY"
    reason = "NO_TOOL_ACTIVITY"
    latest_tool_id = None
    latest_error_code = None
    latest_error_path = None

    for _, _, call, result in events:
        # The prior same-argument cache is an observation, not an effective
        # search. The model must still be allowed to change its query.
        if result.business_status == REPEATED_NO_PROGRESS:
            continue
        latest_tool_id = call.tool_id
        latest_error_code = result.error.code if result.error is not None else None
        latest_error_path = result.error.details.get('path') if result.error is not None else None
        if latest_error_code == "TOOL_INVALID_ARGUMENTS":
            fingerprint = (
                call.tool_id,
                json.dumps(call.arguments, sort_keys=True, separators=(",", ":")),
            )
            schema_errors += 1
            no_progress = 0
            if previous_schema == fingerprint:
                status, reason = "CONFIRMED_STALL", "REPEATED_SCHEMA_ERROR"
            else:
                status, reason = "SUSPECTED_STALL", "SCHEMA_CORRECTION_NEEDED"
            previous_schema = fingerprint
            continue

        previous_schema = None
        schema_errors = 0
        if result.status != "ok":
            if latest_error_code == "TOOL_EXECUTION_FAILED":
                tool_errors += 1
                status = "CONFIRMED_STALL" if tool_errors >= 2 else "SUSPECTED_STALL"
                reason = "REPEATED_TOOL_ERROR" if tool_errors >= 2 else "TOOL_ERROR"
            else:
                tool_errors = 0
                no_progress = 0
                status, reason = "SUSPECTED_STALL", "TOOL_ERROR"
            continue
        if call.tool_id == "read_verified_note":
            no_progress = 0
            tool_errors = 0
            status, reason = "PROGRESS", "VERIFIED_READ"
            continue
        if call.tool_id != "search_notes" or not isinstance(result.value, list):
            continue
        found = {
            item["evidence_id"]
            for item in result.value
            if isinstance(item, dict)
            and isinstance(item.get("evidence_id"), str)
            and item["evidence_id"]
        }
        if found - evidence_ids:
            evidence_ids.update(found)
            no_progress = 0
            tool_errors = 0
            status, reason = "PROGRESS", "NEW_EVIDENCE"
        else:
            no_progress += 1
            status = "CONFIRMED_STALL" if no_progress >= 2 else "SUSPECTED_STALL"
            reason = "SEARCH_NO_NEW_EVIDENCE"

    conflict_slots: tuple[str, ...] = ()
    conflict_refs: frozenset[str] = frozenset()
    pending_alternates: tuple[str, ...] = ()
    exhausted: tuple[str, ...] = ()
    if task_plan is not None and task_id is not None and agent_id is not None and latest_error_code is None:
        plan_assessment = assess_task_plan(
            task_plan, state.tool_ledger, run_id=state.run_id,
            task_id=task_id, agent_id=agent_id,
            expected_workspace_id=expected_workspace_id or state.source.vault_root_fingerprint,
        )
        pending_alternates = pending_alternate_subgoals(
            task_plan, plan_assessment, state.tool_ledger,
            run_id=state.run_id, task_id=task_id, agent_id=agent_id,
        )
        conflict_slots = conflicted_subgoals(task_plan, plan_assessment)
        if conflict_slots:
            blocked = set(conflict_slots)
            for subgoal in task_plan.subgoals:
                if any(parent in blocked for parent in subgoal.depends_on):
                    blocked.add(subgoal.subgoal_id)
            independent_ready = any(
                plan_assessment.status_by_subgoal[item.subgoal_id] == "ready"
                and item.subgoal_id not in blocked
                for item in task_plan.subgoals
            )
            if not independent_ready:
                conflict_refs = frozenset(
                    ref
                    for slot in conflict_slots
                    for ref in verified_subgoal_conflict_refs(
                        task_plan.get(slot).question,
                        plan_assessment.evidence_by_subgoal[slot],
                    )
                )
                status, reason = "CONFIRMED_CONFLICT", "VERIFIED_SOURCE_CONFLICT"
            else:
                conflict_slots = ()
        if pending_alternates and not conflict_slots:
            status, reason = "SUSPECTED_STALL", "PLAN_ALTERNATE_PENDING"
        missing = refusal_missing_subgoals(task_plan, plan_assessment)
        searched = {
            record.arguments.get('source_context', {}).get('subgoal_id')
            for record in state.tool_ledger
            if (record.run_id, record.task_id, record.agent_id) == (state.run_id, task_id, agent_id)
            and record.tool_id == 'search_notes' and record.status == 'completed'
        }
        reachable_missing = [slot for slot in missing
                             if not any(parent in missing for parent in task_plan.get(slot).depends_on)]
        read_ids = {
            value['evidence_id'] for record in state.tool_ledger
            if (record.run_id, record.task_id, record.agent_id) == (state.run_id, task_id, agent_id)
            and record.tool_id == 'read_verified_note' and record.status == 'completed'
            and isinstance(value := (record.result or {}).get('value'), dict)
            and isinstance(value.get('evidence_id'), str)
        }
        if (missing and reachable_missing and evidence_ids <= read_ids
                and all(slot in searched for slot in reachable_missing)
                and not pending_alternates and not conflict_slots):
            exhausted = missing

    return ProgressAssessment(
        status=status,
        reason_code=reason,
        consecutive_no_progress=no_progress,
        consecutive_schema_errors=schema_errors,
        consecutive_tool_errors=tool_errors,
        evidence_ids=frozenset(evidence_ids),
        latest_tool_id=latest_tool_id,
        latest_error_code=latest_error_code,
        conflicted_subgoals=conflict_slots,
        conflict_evidence_ids=conflict_refs,
        pending_alternate_subgoals=pending_alternates,
        exhausted_subgoals=exhausted,
        latest_error_path=latest_error_path,
    )


def repeated_search_observation(
    call: ToolCall,
    ledger: ToolExecutionLedger,
) -> ToolResult | None:
    """Reuse a completed identical search as a marked observation, not an execution.

    This narrow guard applies only to the read-only, snapshot-bound notes search.
    The model sees the prior array plus an explicit no-progress business status,
    so it can revise its query or finish. The synthetic result is persisted by
    the model loop's existing observation/result artifact, not the tool ledger.
    """
    if (
        call.tool_id != "search_notes"
        or not call.run_id
        or not call.task_id
        or not call.agent_id
    ):
        return None
    for record in reversed(ledger.to_list()):
        if (
            record.call_id == call.call_id
            or record.run_id != call.run_id
            or record.task_id != call.task_id
            or record.agent_id != call.agent_id
            or record.tool_id != call.tool_id
            # A single V2 proposal is atomic from the model's perspective.
            # Preserve every member of that proposal, including deliberate
            # duplicate calls used by the existing resume contract.
            or record.sequence >= call.sequence
            or record.arguments != call.arguments
            or record.status != "completed"
            or record.result is None
        ):
            continue
        previous = ToolResult.from_dict(record.result)
        if (
            previous.call_id == record.call_id
            and previous.tool_id == record.tool_id
            and previous.status == "ok"
            and previous.business_status in {None, "FOUND"}
            and isinstance(previous.value, list)
        ):
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="ok",
                value=deepcopy(previous.value),
                business_status=REPEATED_NO_PROGRESS,
            )
    return None


__all__ = [
    "ProgressAssessment", "REPEATED_NO_PROGRESS", "assess_progress",
    "repeated_search_observation",
]
