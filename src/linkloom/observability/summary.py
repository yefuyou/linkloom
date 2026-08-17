"""Safe, local Markdown rendering for validated trace metadata."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping

from linkloom.observability.events import TraceEvent
from linkloom.observability.reader import TraceManifest


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _safe_label(value: Any, *, max_chars: int = 160) -> str:
    if not isinstance(value, str):
        return "<unavailable>"
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return f"sha256:{_hash(value)}"
    value = value.replace("\r", " ").replace("\n", " ")
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return value[:max_chars]


def _safe_ref(ref: Mapping[str, Any] | None) -> str | None:
    if not isinstance(ref, Mapping):
        return None
    if isinstance(ref.get("artifact_id"), str):
        return f"artifact:{_safe_label(ref['artifact_id'])}"
    if isinstance(ref.get("id"), str):
        return f"id:{_safe_label(ref['id'])}"
    if isinstance(ref.get("sha256"), str):
        return f"sha256:{ref['sha256']}"
    for key in ("path", "relative_path"):
        if isinstance(ref.get(key), str):
            return f"path:{_safe_label(ref[key])}"
    return None


def _error_dict(error: Any) -> dict[str, Any]:
    if isinstance(error, Mapping):
        return dict(error)
    if hasattr(error, "to_dict"):
        data = error.to_dict()
        return dict(data) if isinstance(data, Mapping) else {}
    return {}


def _event_attribute_refs(event: TraceEvent) -> tuple[set[str], set[str]]:
    evidence: set[str] = set()
    artifacts: set[str] = set()
    attrs = event.attributes if isinstance(event.attributes, Mapping) else {}
    for key, value in attrs.items():
        lowered = str(key).lower()
        target = evidence if "evidence" in lowered else artifacts if "artifact" in lowered else None
        if target is None:
            continue
        if isinstance(value, str):
            target.add(_safe_label(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    target.add(_safe_label(item))
                elif isinstance(item, Mapping):
                    ref = _safe_ref(item)
                    if ref:
                        target.add(ref)
        elif isinstance(value, Mapping):
            ref = _safe_ref(value)
            if ref:
                target.add(ref)
    return evidence, artifacts


def summarize_trace(events: Iterable[TraceEvent], manifest: TraceManifest | None = None) -> str:
    event_list = list(events)
    if not event_list:
        lines = ["# Trace Summary", "", "No events found."]
        if manifest:
            lines.extend(
                [
                    f"- **Run ID:** {_safe_label(manifest.run_id)}",
                    f"- **Complete:** {manifest.complete}",
                    f"- **Incomplete Reason:** {_safe_label(manifest.incomplete_reason) if manifest.incomplete_reason else 'None'}",
                    f"- **Redaction Policy:** {manifest.redaction_policy_version}",
                ]
            )
        return "\n".join(lines) + "\n"

    first_event = event_list[0]
    run_id = _safe_label(first_event.run_id)
    thread_id = _safe_label(first_event.thread_id)
    final_status = "unknown"
    for event in reversed(event_list):
        if event.event_type == "run.completed":
            final_status = "completed"
            break
        if event.event_type == "run.failed":
            final_status = "failed"
            break
        if event.event_type == "run.stale":
            final_status = "stale"
            break
        if event.event_type == "policy.rejected":
            final_status = "rejected"
            break
        if event.event_type.startswith("run."):
            final_status = event.status
            break
    if final_status == "unknown":
        final_status = event_list[-1].status
    total_duration_ms = sum(event.duration_ms or 0 for event in event_list)
    tool_calls = sum(event.event_type == "tool.called" for event in event_list)
    provider_calls = sum(event.event_type == "provider.requested" for event in event_list)
    retries = sum(event.event_type == "retry.scheduled" for event in event_list)
    agent_tasks = sum(event.event_type == "agent.task.created" for event in event_list)
    handoffs = sum(event.event_type == "handoff.requested" for event in event_list)
    fallbacks = sum(event.event_type == "agent.fallback.used" for event in event_list)
    checkpoints = {
        _safe_label(event.attributes.get("checkpoint_id"))
        for event in event_list
        if event.event_type == "checkpoint.saved" and isinstance(event.attributes.get("checkpoint_id"), str)
    }
    interrupts = [event.status for event in event_list if event.event_type == "interrupt.raised"]
    errors: set[str] = set()
    evidence_refs: set[str] = set()
    artifact_refs: set[str] = set()
    for event in event_list:
        if event.error is not None:
            error = _error_dict(event.error)
            code = _safe_label(error.get("code", "TRACE_ERROR"))
            message_hash = _hash(error.get("message", ""))
            errors.add(f"{code} (message_sha256:{message_hash})")
        output_ref = _safe_ref(event.output_ref)
        if output_ref:
            artifact_refs.add(output_ref)
        attr_evidence, attr_artifacts = _event_attribute_refs(event)
        evidence_refs.update(attr_evidence)
        artifact_refs.update(attr_artifacts)

    policy = manifest.redaction_policy_version if manifest else first_event.redaction.get("policy_version", "unknown")
    secrets_detected = sum(
        value for event in event_list for value in [event.redaction.get("secrets_detected", 0)] if isinstance(value, int)
    )
    raw_content = any(event.redaction.get("raw_content_included", False) for event in event_list)

    lines = [
        "# Trace Summary",
        "",
        f"**Run ID:** {run_id} | **Thread ID:** {thread_id}",
        f"**Final Status:** {final_status}",
        f"**Events:** {len(event_list)} (seq: {event_list[0].seq} - {event_list[-1].seq})",
    ]
    if manifest:
        lines.extend(
            [
                f"**Complete:** {manifest.complete}",
                f"**Incomplete Reason:** {_safe_label(manifest.incomplete_reason) if manifest.incomplete_reason else 'None'}",
                f"**Source Hash:** {manifest.source_index_sha256}",
            ]
        )
    lines.extend(["", "## Timeline", ""])
    step_count = 0
    for event in event_list:
        if event.event_type.startswith("step."):
            node = _safe_label(event.node_name) if event.node_name else "<unnamed>"
            lines.append(f"- `{event.event_type}` `{node}` ({event.duration_ms or 0}ms)")
            step_count += 1
    if step_count == 0:
        lines.append("- No step events recorded.")

    lines.extend(
        [
            "",
            "## Metrics",
            f"- **Tool Calls:** {tool_calls}",
            f"- **Provider Calls:** {provider_calls}",
            f"- **Retries:** {retries}",
            f"- **Agent Tasks:** {agent_tasks}",
            f"- **Handoffs:** {handoffs}",
            f"- **Fallbacks:** {fallbacks}",
            f"- **Total Duration:** {total_duration_ms}ms",
        ]
    )
    if checkpoints or interrupts:
        lines.extend(["", "## Checkpoints & Interrupts"])
        if checkpoints:
            lines.append(f"- **Checkpoints:** {', '.join(sorted(checkpoints))}")
        if interrupts:
            lines.append(f"- **Interrupts:** {', '.join(interrupts)}")
    if evidence_refs:
        lines.extend(["", "## Evidence References"])
        lines.extend(f"- {ref}" for ref in sorted(evidence_refs))
    if artifact_refs:
        lines.extend(["", "## Artifact References"])
        lines.extend(f"- {ref}" for ref in sorted(artifact_refs))
    if errors:
        lines.extend(["", "## Errors"])
        lines.extend(f"- {error}" for error in sorted(errors))
    lines.extend(
        [
            "",
            "## Redaction",
            f"- **Policy:** {policy}",
            f"- **Secrets Detected:** {secrets_detected}",
            f"- **Raw Quotes Included:** {raw_content}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_summary(path: str | Path, events: Iterable[TraceEvent], manifest: TraceManifest | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(summarize_trace(events, manifest), encoding="utf-8")
