"""Read-only projection of existing, Host-authorized Ask records.

Usage is an observation of what Providers reported. Nothing here calls a Provider, schedules work or
decides whether an answer is published."""
from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from typing import Any

from linkloom.observability.pricing import INPUT_USD_PER_MILLION, OUTPUT_USD_PER_MILLION, estimate_cost_usd
from linkloom.observability.redaction import safe_ref
from linkloom.runtime.models import RuntimeState
from linkloom.ui.projection import collect_observed_evidence

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
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
        "schema_version": "ask-telemetry/v2",
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
    failures = []
    for scope, journal in journals.items():
        if journal is None:
            complete = False
            continue
        rows = journal.get("attempts", ())
        complete &= journal.get("requests", journal.get("requests_reserved")) == len(rows)
        legacy = "schema_version" not in journal
        if journal.get("failure"):
            failures.append({"kind": "provider_session", "scope_id": scope, "failure": journal["failure"]})
        for row in rows:
            attempts.append(_provider_attempt(scope, row, legacy))

    counts = Counter(row["status"] for row in attempts)
    observed_attempted = sum(counts[key] for key in ("SUCCEEDED", "FAILED", "WITHHELD", "UNCERTAIN"))
    complete &= counts["UNKNOWN"] == 0
    final = complete and not counts["PENDING"]
    # Run-level totals are the plain sum over every attempt of every scope (router, security, supervisor,
    # each worker attempt, comparison). Cached and reasoning tokens are sub-counts of input/output and
    # are summed only as their own fields, never added to input, output or total again.
    usage = {key: _aggregate([row["usage"][key] for row in attempts], complete)
             for key in (*_TOKEN_FIELDS, "cached_input_tokens", "reasoning_tokens", "duration_ms")}
    estimated = _aggregate([row["cost"]["estimated_usd"] for row in attempts], complete)
    reported = _aggregate([row["cost"]["provider_reported_usd"] for row in attempts], complete)
    return {
        "provider_calls": {
            "attempted": observed_attempted if final else None,
            "completed": counts["SUCCEEDED"] + counts["WITHHELD"] if final else None,
            "failed": counts["FAILED"] if final else None,
            "uncertain": counts["UNCERTAIN"] if final else None,
            "observed_attempted": observed_attempted,
            "pending": counts["PENDING"],
            "journal_coverage": "COMPLETE" if complete else "PARTIAL" if journals else "UNKNOWN",
        },
        "usage": usage,
        "cost": {
            "estimated_usd": estimated,
            "provider_reported_usd": reported,
            "pricing_basis": "observability_token_rates" if estimated["known_value"] is not None else None,
        },
        "attempt_nodes": attempts + failures,
    }


def _provider_attempt(scope, row, legacy):
    outcome = row.get("outcome")
    status = outcome if outcome in _PROVIDER_OUTCOMES else "UNKNOWN"
    # Only a usable response receipt supplies usage. A timeout envelope may be durable while usage isn't.
    # Legacy budget.json rows carry reservation fields (cost_reserved_usd, ...). Those were ceilings, not
    # observations, so they are never read as usage.
    receipt = status in {"SUCCEEDED", "FAILED", "WITHHELD"} and row.get("response_sha256")
    reported = (row.get("usage") or {}) if receipt else {}
    usage = {key: _field(reported.get(key)) for key in (*_TOKEN_FIELDS, "cached_input_tokens", "reasoning_tokens")}
    if usage["total_tokens"]["value"] is None and None not in (usage["input_tokens"]["value"],
                                                                usage["output_tokens"]["value"]):
        derived = usage["input_tokens"]["value"] + usage["output_tokens"]["value"]
        usage["total_tokens"] = {"value": derived, "source": "estimated"}
    if reported.get("duration_ms") is not None:
        usage["duration_ms"] = _field(reported["duration_ms"])
    elif receipt and row.get("duration_ms") is not None:
        usage["duration_ms"] = {"value": row["duration_ms"], "source": "estimated"}
    else:
        usage["duration_ms"] = _field(None)
    estimate = row.get("estimated_cost_usd") if receipt and "estimated_cost_usd" in row else None
    if estimate is None and receipt:
        estimate = estimate_cost_usd(usage["input_tokens"]["value"], usage["output_tokens"]["value"])
    billed = reported.get("provider_reported_cost_usd", row.get("provider_reported_cost_usd") if receipt else None)
    cost = {"estimated_usd": {"value": estimate, "source": "estimated" if estimate is not None else "unknown"},
            "provider_reported_usd": _field(billed)}
    return {
        "kind": "provider_attempt", "scope_id": scope, "status": status,
        **_select(row, "sequence", "stage", "request_sha256", "response_sha256"),
        "error_code": row.get("code"), "usage": usage, "cost": cost,
        "journal_format": "legacy" if legacy else "current",
        "token_rates": ({"input_usd_per_million": INPUT_USD_PER_MILLION, "output_usd_per_million": OUTPUT_USD_PER_MILLION}
                        if estimate is not None else None),
    }


def _field(value):
    return {"value": value, "source": "provider_reported" if value is not None else "unknown"}


def _aggregate(fields, complete):
    """Sum per-attempt {value, source} fields.

    The total is exact (value set) only when the journals are complete and every attempt has a value.
    Otherwise value is None (unknown) and known_value holds the sum of what was observed. The source is
    provider_reported only if every summed value was; any derived or rate-based value makes it estimated.
    """
    known = [field for field in fields if field["value"] is not None]
    known_value = sum(field["value"] for field in known) if known else None
    if known and all(field["source"] == "provider_reported" for field in known):
        source = "provider_reported"
    elif known:
        source = "estimated"
    else:
        source = "unknown"
    exact = complete and len(known) == len(fields)
    if exact and not fields:
        return {"value": 0, "source": "provider_reported", "known_value": 0, "unreported_requests": 0}
    return {"value": known_value if exact else None, "source": source if exact else "unknown",
            "known_value": known_value, "unreported_requests": len(fields) - len(known)}
