"""Read-only B1-6 projection of existing, Host-authorized Ask records."""
from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from typing import Any

from linkloom.observability.pricing import INPUT_USD_PER_MILLION, OUTPUT_USD_PER_MILLION
from linkloom.observability.redaction import safe_ref
from linkloom.runtime.models import RuntimeState
from linkloom.ui.projection import collect_observed_evidence

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
_RESERVATIONS = ("input_tokens_reserved", "output_tokens_reserved", "cost_reserved_usd")
_PROVIDER_OUTCOMES = {"SUCCEEDED", "FAILED", "UNCERTAIN", "WITHHELD", "PENDING"}
_EVENT_KINDS = {
    "supervisor_decision": "supervisor", "supervisor_aborted": "supervisor",
    "plan_approved": "host", "plan_rejected": "host", "wave_dispatched": "host",
    "worker_finished": "worker", "synthesized": "synthesizer", "synthesis_withheld": "synthesizer",
}


def build_ask_telemetry(
    *,
    run_id: str,
    workspace_id: str,
    status: str,
    orchestration_trace: Mapping[str, Any] | None = None,
    router: Mapping[str, Any] | None = None,
    provider_journals: Mapping[str, Mapping[str, Any] | None] | None = None,
    provider_journals_complete: bool = False,
    runtime_states: Mapping[str, RuntimeState] | None = None,
    branch_states: Collection[Mapping[str, Any]] = (),
    authorized_evidence_refs: Collection[str] = (),
    total_duration_ms: int | None = None,
) -> dict[str, Any]:
    """Project one authorized Ask without opening a checkpoint or a Provider.

    The Host verifies workspace and run ownership before calling. It supplies
    real Provider journals under canonical, run-local scope IDs, e.g. supervisor/round-0 or
    tasks/t1/attempt-0-1. Runtime snapshots can use the same scope IDs. Pass
    None for unreadable journals, and set completeness only when every
    Provider path (including Router) is accounted for. A confirmed local-only
    Ask passes an empty journal map with completeness true. A saved run with
    no confirmed Router linkage leaves completeness false.

    Evidence IDs/blackboard keys must be separately authorized for readback.
    Branch recovery states are capabilities reported by the checkpoint view,
    including REUSED. They are never counted as additional Provider attempts.
    Duration, when supplied, is the measured end-to-end wall time.
    """
    trace = orchestration_trace or {}
    if orchestration_trace is not None and trace.get("run_id") != run_id:
        raise ValueError("orchestration trace belongs to another run")

    provider = _provider_summary(provider_journals or {}, provider_journals_complete)
    nodes = []
    if router is not None:
        nodes.append({"kind": "router", **_select(router, "source", "attempted", "decision", "reason_code")})
    for event in trace.get("events", ()):
        event_type = event["event"]
        nodes.append({
            "kind": _EVENT_KINDS.get(event_type, "orchestration"),
            **_select(event, "event", "round", "task", "tasks", "attempt", "status", "action", "failure"),
        })
    recovery = {row["branch"]: row for row in branch_states}
    # task_rows.evidence_keys is linked to reviewed branch results by the
    # existing blackboard projection. Never publish model observations as proof.
    for task in trace.get("tasks", ()):
        branch = recovery.get(task["id"], {})
        refs = _authorized_refs(task.get("evidence_keys", ()), authorized_evidence_refs)
        nodes.append({
            "kind": "worker", "event": "checkpoint_state",
            **_select(task, "id", "round", "attempt", "status", "failure"),
            "recovery_state": branch.get("state"), "recovery_reason": branch.get("reason"),
            "result_ref": task.get("result_ref"), "evidence_refs": refs if task.get("result_ref") else None,
        })
    for scope_id, state in (runtime_states or {}).items():
        nodes.extend(_runtime_nodes(scope_id, state, authorized_evidence_refs))
    nodes.extend(provider.pop("attempt_nodes"))

    failures = {node[key] for node in nodes for key in ("failure", "error_code")
                if node.get(key)}
    if router and router.get("reason_code") not in {None, "NONE"}:
        failures.add(router["reason_code"])
    return {
        "schema_version": "ask-telemetry/v1",
        "run_id": run_id, "workspace_id": workspace_id, "status": status,
        "total_duration_ms": total_duration_ms,
        **provider, "nodes": nodes, "failure_codes": sorted(failures),
        # EvalResultAdapter has evaluation run IDs, not an Ask-to-case link.
        "eval_case_id": None,
    }


def _select(row, *keys):
    return {key: row[key] for key in keys if key in row}


def _authorized_refs(refs, authorized):
    return sorted(set(refs).intersection(authorized)) or None


def _runtime_nodes(scope_id, state, authorized):
    runtime = {"scope_id": scope_id, "runtime_run_id": state.run_id, "thread_id": state.thread_id}
    for attempt in state.attempts:
        yield {"kind": "runtime_step", **runtime, "attempt_id": attempt.attempt_id,
               "node": attempt.node_name, "sequence": attempt.step_seq, "retry_index": attempt.retry_index,
               "status": attempt.status, "error_code": attempt.error_code}
    for turn in state.model_executions:
        yield {"kind": "model_turn", **runtime, "turn_id": turn.turn_id, "sequence": turn.sequence,
               "status": turn.status, "response_origin": turn.response_origin,
               "error_code": (turn.provider_error or {}).get("code")}
    for tool in state.tool_ledger:
        evidence = collect_observed_evidence((tool,))
        yield {"kind": "tool", **runtime, "tool_id": tool.tool_id, "call_id": tool.call_id,
               "sequence": tool.sequence, "status": tool.status,
               "outcome": (tool.result or {}).get("status"),
               "arguments_sha256": safe_ref(tool.arguments)["sha256"], "error_code": (tool.error or {}).get("code"),
               "evidence_refs": _authorized_refs(evidence, authorized)}


def _provider_summary(journals, complete):
    attempts = []
    reservations = {key: [] for key in _RESERVATIONS}
    failures = []
    for scope, journal in journals.items():
        if journal is None:
            complete = False
            continue
        rows = journal.get("attempts", ())
        complete &= journal.get("requests", journal.get("requests_reserved")) == len(rows)
        for key in reservations:
            reservations[key].append(journal.get(key))
        if journal.get("failure"):
            failures.append({"kind": "provider_session", "scope_id": scope, "failure": journal["failure"]})
        for row in rows:
            attempts.append(_provider_attempt(scope, row))

    counts = Counter(row["status"] for row in attempts)
    observed_attempted = sum(counts[key] for key in ("SUCCEEDED", "FAILED", "WITHHELD", "UNCERTAIN"))
    complete &= counts["UNKNOWN"] == 0
    usage_rows = [row["reported_usage"] for row in attempts]
    estimates = [row["estimated_usage_usd"] for row in attempts]
    actual_estimate = _complete_sum(estimates, complete)
    known_estimate = sum(value for value in estimates if value is not None)
    known_count = sum(value is not None for value in estimates)

    return {
        "provider_calls": {
            "attempted": observed_attempted if complete and not counts["PENDING"] else None,
            "completed": counts["SUCCEEDED"] + counts["WITHHELD"] if complete and not counts["PENDING"] else None,
            "failed": counts["FAILED"] if complete and not counts["PENDING"] else None,
            "uncertain": counts["UNCERTAIN"] if complete and not counts["PENDING"] else None,
            "observed_attempted": observed_attempted,
            "pending": counts["PENDING"],
            "journal_coverage": "COMPLETE" if complete else "PARTIAL" if journals else "UNKNOWN",
        },
        "usage": {
            **{f"reported_{key}": _complete_sum([row[key] for row in usage_rows], complete) for key in _TOKEN_FIELDS},
            "reported_cached_input_tokens": None,
            "reasoning_tokens": None,
            "reserved_input_upper_bound": _complete_sum(reservations["input_tokens_reserved"], complete),
            "reserved_output_upper_bound": _complete_sum(reservations["output_tokens_reserved"], complete),
        },
        "cost": {
            "estimated_actual_usage_usd": actual_estimate,
            "known_estimated_usage_usd": known_estimate if known_count else None,
            "reserved_upper_bound_usd": _complete_sum(reservations["cost_reserved_usd"], complete),
            "observed_reserved_upper_bound_usd": _complete_sum(
                reservations["cost_reserved_usd"], bool(reservations["cost_reserved_usd"]),
            ),
            "unresolved_reserved_upper_bound_usd": _complete_sum(
                [row["reserved_usd"] for row in attempts if row["estimated_usage_usd"] is None], complete,
            ),
            "pricing_basis": "observability_token_rates" if known_count else None,
            "billing_status": "KNOWN_ESTIMATE" if actual_estimate is not None else "PARTIAL" if known_count else "UNKNOWN",
            "actual_billed_usd": None,
        },
        "attempt_nodes": attempts + failures,
    }


def _provider_attempt(scope, row):
    outcome = row.get("outcome")
    status = outcome if outcome in _PROVIDER_OUTCOMES else "UNKNOWN"
    # Reservation is written before transport. Only a usable response receipt
    # supplies usage. A timeout envelope may be durable while usage isn't.
    if status in {"SUCCEEDED", "FAILED", "WITHHELD"} and row.get("response_sha256"):
        reported = row.get("usage") or {}
    else:
        reported = {}
    usage = {key: reported.get(key) for key in _TOKEN_FIELDS}
    rates = (INPUT_USD_PER_MILLION, OUTPUT_USD_PER_MILLION)
    tokens = (usage["input_tokens"], usage["output_tokens"])
    estimate = None
    pricing = None
    if all(value is not None for value in (*tokens, *rates)):
        estimate = sum(token * rate for token, rate in zip(tokens, rates)) / 1_000_000
        pricing = dict(input_usd_per_million=rates[0], output_usd_per_million=rates[1])
    return {
        "kind": "provider_attempt", "scope_id": scope, "status": status,
        **_select(row, "sequence", "stage", "request_sha256", "response_sha256"),
        "error_code": row.get("code"), "reported_usage": usage, "duration_ms": reported.get("duration_ms"),
        "input_reserved": row.get("input_reserved"), "output_reserved": row.get("output_reserved"),
        "reserved_usd": row.get("cost_reserved_usd"), "estimated_usage_usd": estimate,
        "token_rates": pricing,
    }


def _complete_sum(values, complete):
    return sum(values) if complete and all(value is not None for value in values) else None
