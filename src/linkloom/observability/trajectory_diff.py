"""Read-only trajectory comparison of persisted runs.

Only data that the existing Trace, Checkpoint and Recovery contracts already
persist is used. Anything that cannot be computed is reported as N/A together
with the reason; nothing is inferred, estimated or fabricated.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from linkloom.observability.events import TraceEvent
from linkloom.observability.reader import TraceManifest, TraceReader
from linkloom.observability.redaction import RedactionPolicy
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.models import RuntimeState
from linkloom.runtime.recovery import decide_model_resume

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+\S{8,})")
_MAX_TEXT = 200
_MAX_ITEMS = 12
_UNVERIFIED_MODEL_STATUSES = {"request_sent", "response_obtained"}
_NA_NO_TS = "trace events carry no started_at/finished_at"
_NA_NO_GOLD = "no independent gold answer supplied"


def na(reason: str) -> dict[str, Any]:
    return {"value": None, "na": reason}


def val(value: Any) -> dict[str, Any]:
    return {"value": value, "na": None}


def _text(value: str) -> str:
    if " " not in value and (PureWindowsPath(value).is_absolute() or Path(value).is_absolute()):
        return "<absolute-path-redacted>"
    value = _SECRET_RE.sub("<secret-redacted>", value)
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", value)
    return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "..."


def _clean(value: Any, depth: int = 0) -> Any:
    """Bound and scrub arbitrary JSON before it can reach the exported page."""
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, list):
        items = [_clean(item, depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"... {len(value) - _MAX_ITEMS} more")
        return items
    if isinstance(value, dict):
        if depth >= 4:
            return "<nested>"
        return {_text(str(k)): _clean(v, depth + 1) for k, v in list(value.items())[:40]}
    return value


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def max_concurrency(events: list[TraceEvent]) -> dict[str, Any]:
    """Peak overlap between non-nested event intervals, or N/A.

    Needs a complete [started_at, finished_at] interval on every event.
    Events related through parent_event_id are nested, not parallel.
    """
    if not events:
        return na("no events")
    if any(e.started_at is None or e.finished_at is None for e in events):
        return na(_NA_NO_TS)
    parent = {e.event_id: e.parent_event_id for e in events}

    def ancestors(event_id: str) -> set[str]:
        found: set[str] = set()
        current = parent.get(event_id)
        while current and current not in found:
            found.add(current)
            current = parent.get(current)
        return found

    anc = {e.event_id: ancestors(e.event_id) for e in events}
    iv = [(e.event_id, _ts(e.started_at), _ts(e.finished_at)) for e in events]
    peak = 1
    for eid, start, _ in iv:
        group = [eid]
        for oid, ostart, oend in iv:
            if oid == eid or not (ostart <= start < oend):
                continue
            if all(oid not in anc[g] and g not in anc[oid] for g in group):
                group.append(oid)
        peak = max(peak, len(group))
    return val(peak)


def _call_id(event: TraceEvent) -> str | None:
    value = event.attributes.get("call_id")
    return value if isinstance(value, str) else None


def _evidence_id(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        for key in ("evidence_id", "id"):
            if isinstance(entry.get(key), str):
                return entry[key]
    return None


def _identity(item: Mapping[str, Any]) -> dict[str, Any]:
    rel = item.get("relative_path")
    if not isinstance(rel, str) or Path(rel).is_absolute() or PureWindowsPath(rel).is_absolute():
        rel = None
    return {
        "evidence_id": item.get("evidence_id"),
        "workspace_id": item.get("workspace_id"),
        "source_ref": _text(rel) if rel else None,
        "source_kind": item.get("source_kind"),
        "line_start": item.get("line_start"),
        "line_end": item.get("line_end"),
        "content_sha256": item.get("content_sha256"),
        "index_version": item.get("index_version"),
        "status": item.get("status"),
    }


def _ledger_evidence(state: RuntimeState) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for record in state.tool_ledger:
        result = record.result if isinstance(record.result, dict) else {}
        value = result.get("value")
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, Mapping) and isinstance(item.get("evidence_id"), str):
                found.setdefault(item["evidence_id"], _identity(item))
    return found


def _read_result(checkpoint_dir: Path, state: RuntimeState, diagnostics: list[str]) -> dict[str, Any] | None:
    import json

    ref = state.result_ref
    if not ref:
        return None
    base = checkpoint_dir.resolve()
    path = (base / ref).resolve()
    if base not in path.parents or not path.is_file():
        diagnostics.append("result_ref missing or outside checkpoint directory")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        diagnostics.append(f"result file unreadable: {type(exc).__name__}")
        return None
    return data if isinstance(data, dict) else None


def _timeline(events: list[TraceEvent]) -> list[dict[str, Any]]:
    seq_of = {e.event_id: e.seq for e in events}
    rows = []
    for e in events:
        attrs = RedactionPolicy().apply(e.attributes).redacted_data
        error = e.error if isinstance(e.error, dict) else None
        rows.append(
            {
                "seq": e.seq,
                "event_type": e.event_type,
                "actor": e.actor,
                "status": e.status,
                "node": _text(e.node_name) if e.node_name else None,
                "call_id": _call_id(e),
                "parent_seq": seq_of.get(e.parent_event_id) if e.parent_event_id else None,
                "started_at": e.started_at,
                "finished_at": e.finished_at,
                "duration_ms": e.duration_ms,
                "attributes": _clean(attrs),
                "output_ref": _clean(e.output_ref) if e.output_ref else None,
                "error_code": _text(str(error.get("code"))) if error else None,
            }
        )
    return rows


def _recovery(state: RuntimeState, checkpoint_dir: Path) -> dict[str, Any]:
    decision = decide_model_resume(state)
    models = []
    for record in sorted(state.model_executions, key=lambda r: r.sequence):
        if record.status in _UNVERIFIED_MODEL_STATUSES:
            note = "unverified: persisted lifecycle cannot prove a provider response; verification required"
        elif record.status == "reinvoke_allowed":
            note = "an external verifier marked the request as not sent"
        elif record.status == "failed":
            note = "failed: manual decision required"
        else:
            note = "durable lifecycle state"
        models.append({"sequence": record.sequence, "turn_id": _text(record.turn_id), "status": record.status, "note": note})
    tools = [
        {
            "call_id": _text(r.call_id),
            "tool_id": _text(r.tool_id),
            "status": r.status,
            "note": "pending: outcome unknown, requires verification" if r.status == "pending" else "terminal ledger record",
        }
        for r in state.tool_ledger
    ]
    try:
        checkpoints = [
            {k: c.get(k) for k in ("checkpoint_id", "step_seq", "status", "current_step", "created_at")}
            for c in SQLiteCheckpointer(checkpoint_dir).list_checkpoints(state.thread_id)
        ]
    except Exception:  # corrupt store: decision above already came from a loaded state
        checkpoints = []
    term = state.termination
    return {
        "decision": decision.to_dict(),
        "model_executions": models,
        "tool_ledger": tools,
        "checkpoints": _clean(checkpoints),
        "termination": {"status": term.status, "reason_code": term.reason_code} if term else None,
        "pending_interrupt": state.pending_interrupt is not None,
    }


def load_run(
    label: str,
    trace_root: str | Path,
    run_id: str,
    checkpoint_dir: str | Path | None = None,
    *,
    thread_id: str | None = None,
    execution_mode: str = "unspecified",
    data_source: str = "unspecified",
    is_mock: bool | None = None,
) -> dict[str, Any]:
    """Load one run through TraceReader (+ checkpoint when given) into a JSON-safe dict."""
    diagnostics: list[str] = []
    root_text = str(Path(trace_root))

    def why(exc: Exception) -> str:  # keep local filesystem paths out of the page
        return _text(str(exc).replace(root_text, "<trace_root>"))

    reader = TraceReader(trace_root)
    manifest: TraceManifest | None = None
    events: list[TraceEvent] = []
    try:
        manifest = reader.read_manifest(run_id)
    except ValueError as exc:
        diagnostics.append(f"manifest: {why(exc)}")
    try:
        events = reader.read_events(run_id)
    except ValueError as exc:
        diagnostics.append(f"events: {why(exc)}")
    if not events and not any(d.startswith("events:") for d in diagnostics):
        diagnostics.append("events: events.jsonl missing or empty")
    if manifest is not None and events and (
        manifest.event_count != len(events) or manifest.last_seq != events[-1].seq
    ):
        diagnostics.append(
            f"manifest/event mismatch: manifest has {manifest.event_count} events, log has {len(events)}"
        )
    if manifest is not None and not manifest.complete:
        diagnostics.append(f"manifest marked incomplete: {_text(manifest.incomplete_reason or 'no reason recorded')}")

    state: RuntimeState | None = None
    result: dict[str, Any] | None = None
    cp_dir = Path(checkpoint_dir) if checkpoint_dir is not None else None
    if cp_dir is None:
        diagnostics.append("checkpoint: none supplied; result, evidence and recovery sections are N/A")
    else:
        tid = thread_id or (events[0].thread_id if events else None)
        if tid is None:
            diagnostics.append("checkpoint: thread_id unknown because no events could be read")
        else:
            try:
                state = SQLiteCheckpointer(cp_dir).load(tid)
            except Exception as exc:
                diagnostics.append(f"checkpoint: {type(exc).__name__}")
            if state is not None and state.run_id != run_id:
                diagnostics.append(f"checkpoint run_id {_text(state.run_id)} does not match {_text(run_id)}; ignored")
                state = None
            if state is not None:
                result = _read_result(cp_dir, state, diagnostics)

    run: dict[str, Any] = {
        "label": label,
        "run_id": run_id,
        "provenance": {
            "execution_mode": execution_mode,
            "data_source": data_source,
            "is_mock": is_mock,
            "thread_id": events[0].thread_id if events else None,
            "manifest_complete": manifest.complete if manifest else None,
            "event_count": len(events),
            "checkpoint_loaded": state is not None,
            "diagnostics": diagnostics,
        },
        "timeline": _timeline(events),
        "l1": {},
        "l2": {},
        "l3": {},
        "recovery": None,
        "models": [],
    }
    count = lambda *types: sum(e.event_type in types for e in events)  # noqa: E731
    ledger = list(state.tool_ledger) if state else []

    # L1: hard contracts
    unverified = [
        r.turn_id for r in (state.model_executions if state else []) if r.status in _UNVERIFIED_MODEL_STATUSES
    ] + [r.call_id for r in ledger if r.status == "pending"]
    cp_ok = bool(cp_dir and state is not None)
    run["l1"] = {
        "policy_rejections": val(count("policy.rejected")) if events else na("no events"),
        "policy_note": "A zero count means no rejection was recorded; it is not proof that checks passed.",
        "tool_failures": val(count("tool.failed") + sum(r.status == "failed" for r in ledger)) if events else na("no events"),
        "record_complete": val(manifest.complete) if manifest else na("manifest unavailable"),
        "event_log_consistent": val(not any(d.startswith(("events:", "manifest/event")) for d in diagnostics) and bool(events)),
        "source_index_matches": (
            val(manifest.source_index_sha256 == state.source.index_sha256)
            if manifest and state
            else na("needs manifest and checkpoint")
        ),
        "artifact_present": (
            val(result is not None) if cp_ok and state.result_ref else na("no result_ref in checkpoint")
        ),
        "uncertain_items": val(len(unverified)) if cp_ok else na("no checkpoint"),
        "safe_stop": (
            val(f"{state.termination.status}: {state.termination.reason_code}")
            if state and state.termination
            else na("no termination state")
        ),
    }

    # L2: outcome and evidence
    if state is not None:
        cited = []
        for entry in state.evidence_refs:
            eid = _evidence_id(entry)
            if eid and eid not in cited:
                cited.append(eid)
        known = _ledger_evidence(state)
        identities = {eid: known.get(eid) for eid in cited}
        run["l2"] = {
            "final_status": val(state.status),
            "cited": cited,
            "identities": identities,
            "verified_cited": [e for e in cited if (known.get(e) or {}).get("status") == "verified"],
            "gaps": [e for e in cited if e not in known],
            "retrieved_not_cited": [e for e in known if e not in cited],
            "review_decision": (
                val(result["review"].get("decision"))
                if result and isinstance(result.get("review"), dict)
                else na("no review record in result artifact")
            ),
            "workspaces": sorted({i["workspace_id"] for i in known.values() if i.get("workspace_id")}),
            "gold_recall": na(_NA_NO_GOLD),
            "rubric_accuracy": na(_NA_NO_GOLD),
        }
        run["models"] = [
            {"sequence": r.sequence, "status": r.status, "agent_id": _text(r.agent_id), "response_origin": r.response_origin,
             "action_kind": (r.normalized_action or {}).get("kind"), "tool_id": _text(str(((r.normalized_action or {}).get("tool_call") or {}).get("tool_id") or "")) or None,
             "request_sha256": r.request_sha256, "response_sha256": r.response_sha256, "usage": _clean(r.usage)}
            for r in sorted(state.model_executions, key=lambda r: r.sequence)
        ]
        run["recovery"] = _recovery(state, cp_dir)
    else:
        run["l2"] = {"final_status": na("no checkpoint"), "cited": [], "identities": {}, "verified_cited": [],
                     "gaps": [], "retrieved_not_cited": [], "review_decision": na("no checkpoint"), "workspaces": [],
                     "gold_recall": na(_NA_NO_GOLD), "rubric_accuracy": na(_NA_NO_GOLD)}

    # L3: efficiency and topology
    durations = [e.duration_ms for e in events if e.duration_ms is not None]
    starts = [_ts(e.started_at) for e in events if e.started_at]
    ends = [_ts(e.finished_at) for e in events if e.finished_at]
    usage_rows = [r.usage for r in (state.model_executions if state else [])]
    tokens_known = bool(usage_rows) and all(
        isinstance(u.get("input_tokens"), int) and isinstance(u.get("output_tokens"), int) for u in usage_rows
    )
    run["l3"] = {
        "provider_requests": (
            val(len(state.model_executions)) if state else na("no checkpoint; trace has no provider.* events")
        ),
        "tool_calls": val(count("tool.called")) if events else na("no events"),
        "failures": val(count("tool.failed", "provider.failed", "step.failed", "run.failed")) if events else na("no events"),
        "wall_time_ms": (
            val(int((max(ends) - min(starts)).total_seconds() * 1000)) if starts and ends else na(_NA_NO_TS)
        ),
        "summed_duration_ms": val(sum(durations)) if durations else na("no event recorded duration_ms"),
        "input_tokens": val(sum(u["input_tokens"] for u in usage_rows)) if tokens_known else na("provider usage not recorded"),
        "output_tokens": val(sum(u["output_tokens"] for u in usage_rows)) if tokens_known else na("provider usage not recorded"),
        "estimated_cost_usd": na("no token usage or priced model recorded; the UsageEnvelope default 0.0 is not a measurement"),
        "reserved_budget": (
            val({"max_provider_requests": state.policy.max_provider_requests, "max_steps": state.policy.max_steps})
            if state
            else na("no checkpoint")
        ),
        "handoffs": val(count("handoff.requested")) if events else na("no events"),
        "checkpoints_saved": val(count("checkpoint.saved")) if events else na("no events"),
        "interrupts": val(count("interrupt.raised")) if events else na("no events"),
        "resumes": val(count("interrupt.resumed")) if events else na("no events"),
        "max_concurrency": max_concurrency(events),
    }
    return run


def _flat(run: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for layer in ("l1", "l3"):
        for key, item in run[layer].items():
            if isinstance(item, dict) and "na" in item:
                out[f"{layer}.{key}"] = item
    out["l2.final_status"] = run["l2"]["final_status"]
    out["l2.review_decision"] = run["l2"]["review_decision"]
    return out


def diff_runs(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two loaded runs by outcome and behavior, never by tool order or answer text."""
    if a["run_id"] == b["run_id"]:
        raise ValueError("diff requires two different run_ids; refusing to merge a run with itself")
    ca, cb = a["l2"]["cited"], b["l2"]["cited"]
    comparable = bool(a["l2"]["workspaces"]) and a["l2"]["workspaces"] == b["l2"]["workspaces"]
    fa, fb = _flat(a), _flat(b)
    metrics = [
        {"key": k, "a": fa[k], "b": fb[k], "same": fa[k] == fb[k]} for k in fa if k in fb
    ]
    return {
        "schema": "trajectory-diff-v1",
        "runs": [dict(a), dict(b)],
        "metrics": metrics,
        "evidence": {
            "comparable": comparable,
            "reason": None if comparable else "runs differ in workspace_id or workspace unknown; evidence ids are not comparable",
            "shared": [e for e in ca if e in cb],
            "only_a": [e for e in ca if e not in cb],
            "only_b": [e for e in cb if e not in ca],
            "gold_recall": na(_NA_NO_GOLD),
            "similarity_is_not_accuracy": True,
        },
    }
