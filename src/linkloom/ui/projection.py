"""Project durable runtime facts into the LinkLoom product UI contract.

This module is deliberately provider-neutral and read-only.  It validates the
strict business result, translates opaque evidence ids into friendly citation
handles, and never resolves an evidence reference that was not observed in the
runtime tool ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Iterable, Mapping

from linkloom.agents.team_decision import NULL_DECISION_STATUSES, TeamDecisionResult
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RunStatus, ToolExecutionRecord
from linkloom.schemas import NoteDocument


UI_SCHEMA_VERSION = "linkloom-product-ui/v1"


class UIProjectionError(ValueError):
    """Fail-closed presentation boundary with a stable safe code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspacePresentationContext:
    """Safe workspace metadata shown in product chrome."""

    display_name: str
    document_count: int | None = None
    read_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("Workspace display_name must be non-empty text.")
        if (
            self.document_count is not None
            and (
                isinstance(self.document_count, bool)
                or not isinstance(self.document_count, int)
                or self.document_count < 0
            )
        ):
            raise ValueError("Workspace document_count must be a non-negative integer or None.")
        if self.read_only is not True:
            raise ValueError("The LinkLoom Product UI V1 workspace must be read-only.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "document_count": self.document_count,
            "read_only": self.read_only,
        }


def _status_dict(status: RunStatus | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(status, RunStatus):
        return status.to_dict()
    if isinstance(status, Mapping):
        return dict(status)
    raise UIProjectionError("UI_RUN_STATUS_ERROR", "Run status is unavailable.")


def _ledger_dict(record: ToolExecutionRecord | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(record, ToolExecutionRecord):
        return record.to_dict()
    if isinstance(record, Mapping):
        return dict(record)
    raise UIProjectionError("UI_EVIDENCE_CONTRACT_ERROR", "Tool ledger record is invalid.")


def _is_safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    return not (
        Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or normalized.startswith("/")
        or ".." in parts
    )


def _validated_evidence(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    required = {
        "evidence_id",
        "relative_path",
        "content_sha256",
        "line_start",
        "line_end",
        "quote",
        "quote_sha256",
        "status",
    }
    if not required <= set(value):
        return None
    evidence_id = value.get("evidence_id")
    quote = value.get("quote")
    line_start = value.get("line_start")
    line_end = value.get("line_end")
    content_sha256 = value.get("content_sha256")
    quote_sha256 = value.get("quote_sha256")
    if (
        not isinstance(evidence_id, str)
        or not evidence_id
        or not _is_safe_relative_path(value.get("relative_path"))
        or not isinstance(quote, str)
        or not quote
        or isinstance(line_start, bool)
        or not isinstance(line_start, int)
        or isinstance(line_end, bool)
        or not isinstance(line_end, int)
        or line_start < 1
        or line_end < line_start
        or not isinstance(content_sha256, str)
        or len(content_sha256) != 64
        or not isinstance(quote_sha256, str)
        or hashlib.sha256(quote.encode("utf-8")).hexdigest() != quote_sha256
        or value.get("status") != "verified"
    ):
        return None
    return dict(value)


def collect_observed_evidence(
    tool_ledger: Iterable[ToolExecutionRecord | Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collect verified evidence returned by completed, successful tool calls."""

    observed: dict[str, dict[str, Any]] = {}
    for raw_record in tool_ledger:
        record = _ledger_dict(raw_record)
        result = record.get("result")
        if record.get("status") != "completed" or not isinstance(result, Mapping):
            continue
        if result.get("status") != "ok" or result.get("tool_id") != record.get("tool_id"):
            continue
        raw_value = result.get("value")
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for raw_evidence in values:
            evidence = _validated_evidence(raw_evidence)
            if evidence is not None:
                observed.setdefault(evidence["evidence_id"], evidence)
    return observed


def _progress(status: str, tool_ledger: Iterable[ToolExecutionRecord | Mapping[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    records = [_ledger_dict(record) for record in tool_ledger]
    completed_tools = {
        record.get("tool_id")
        for record in records
        if record.get("status") == "completed"
    }
    labels = [
        ("searching", "Searched the workspace"),
        ("reading", "Read decision records"),
        ("checking", "Checked supporting claims"),
        ("ready", "Answer ready"),
    ]

    if status == "completed":
        active_index = 4
        stage = "ready"
    elif "read_verified_note" in completed_tools:
        active_index = 2
        stage = "checking"
    elif "search_notes" in completed_tools:
        active_index = 1
        stage = "reading"
    else:
        active_index = 0
        stage = "searching"

    steps: list[dict[str, str]] = []
    for index, (step_id, label) in enumerate(labels):
        if index < active_index:
            state = "complete"
        elif index == active_index and active_index < len(labels):
            state = "active"
        else:
            state = "pending"
        if active_index == len(labels):
            state = "complete"
        steps.append({"id": step_id, "label": label, "state": state})
    return stage, steps


def _base_payload(
    *,
    query: str,
    workspace: WorkspacePresentationContext,
    status: dict[str, Any],
    tool_ledger: Iterable[ToolExecutionRecord | Mapping[str, Any]],
) -> dict[str, Any]:
    stage, steps = _progress(str(status.get("status", "")), tool_ledger)
    return {
        "schema_version": UI_SCHEMA_VERSION,
        "kind": "running",
        "workspace": workspace.to_dict(),
        "query": query,
        "run": {
            "id": str(status.get("run_id", "")),
            "thread_id": str(status.get("thread_id", "")),
            "status": str(status.get("status", "")),
            "stage": stage,
        },
        "decision": None,
        "rationale": [],
        "rejected_alternatives": [],
        "actions": [],
        "unresolved_items": [],
        "uncertainty": None,
        "evidence": [],
        "provenance": {"expanded": False, "steps": steps},
        "error": None,
    }


def _safe_runtime_error(status: Mapping[str, Any]) -> dict[str, Any]:
    raw_error = status.get("error")
    error = raw_error if isinstance(raw_error, Mapping) else {}
    raw_code = error.get("code")
    code = (
        raw_code
        if isinstance(raw_code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", raw_code)
        else "UI_RUNTIME_ERROR"
    )
    return {
        "title": "LinkLoom could not complete this reconstruction.",
        "message": "The workspace was left unchanged. You can safely try the question again.",
        "code": code,
        "technical_message": "No additional safe details are available.",
    }


def _extract_team_decision(result_artifact: object) -> TeamDecisionResult:
    try:
        if not isinstance(result_artifact, Mapping):
            raise ValidationError("Result artifact must be an object.")
        result = result_artifact.get("result")
        if result_artifact.get("status") != "completed" or not isinstance(result, Mapping):
            raise ValidationError("Completed result artifact is unavailable.")
        raw_decision = result.get("team_decision")
        return TeamDecisionResult.from_dict(raw_decision)
    except (ValidationError, TypeError, ValueError) as error:
        raise UIProjectionError(
            "UI_TEAM_DECISION_CONTRACT_ERROR",
            "TeamDecisionResult could not be validated for display.",
        ) from error


def _citation_label(evidence: Mapping[str, Any]) -> str:
    start = int(evidence["line_start"])
    end = int(evidence["line_end"])
    location = f"line {start}" if start == end else f"lines {start}–{end}"
    return f"{evidence['relative_path']} · {location}"


def _source_preview(
    evidence: Mapping[str, Any],
    documents: Mapping[str, NoteDocument],
) -> dict[str, Any]:
    document = documents.get(str(evidence["relative_path"]))
    fallback_reason = "Full source preview is unavailable; showing the verified excerpt."
    if document is not None:
        lines = document.content.splitlines()
        start = int(evidence["line_start"])
        end = int(evidence["line_end"])
        selected = "\n".join(lines[start - 1 : end]) if end <= len(lines) else ""
        if (
            document.content_sha256 == evidence["content_sha256"]
            and str(evidence["quote"]) in selected
        ):
            return {
                "preview_kind": "document",
                "quote": evidence["quote"],
                "lines": [
                    {"number": index, "text": text}
                    for index, text in enumerate(lines, start=1)
                ],
                "fallback_reason": None,
            }
        fallback_reason = "The source changed after evidence capture; showing the verified excerpt."
    return {
        "preview_kind": "quote",
        "quote": evidence["quote"],
        "lines": [],
        "fallback_reason": fallback_reason,
    }


def _citation_ids(refs: Iterable[str], citation_by_ref: Mapping[str, str]) -> list[str]:
    return [citation_by_ref[ref] for ref in refs]


def _documents_by_path(source_documents: Iterable[NoteDocument]) -> dict[str, NoteDocument]:
    documents: dict[str, NoteDocument] = {}
    for document in source_documents:
        if not isinstance(document, NoteDocument) or not _is_safe_relative_path(document.relative_path):
            continue
        documents[document.relative_path] = document
    return documents


def _project_running_evidence(
    observed: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, NoteDocument],
) -> list[dict[str, Any]]:
    return [
        {
            "id": f"observed-{index}",
            "ordinal": index,
            "ref": ref,
            "label": _citation_label(evidence),
            "relative_path": evidence["relative_path"],
            "line_start": evidence["line_start"],
            "line_end": evidence["line_end"],
            "supports": ["Currently reviewing"],
            "source": _source_preview(evidence, documents),
        }
        for index, (ref, evidence) in enumerate(observed.items(), start=1)
    ]


def _claim_support_labels(result: TeamDecisionResult) -> dict[str, list[str]]:
    supports: dict[str, list[str]] = {ref: [] for ref in result.evidence_refs}

    def add(refs: Iterable[str], label: str) -> None:
        for ref in refs:
            if label not in supports[ref]:
                supports[ref].append(label)

    add(result.decision["evidence_refs"], "Final decision")
    for item in result.rationale:
        add(item["evidence_refs"], "Rationale")
    for item in result.rejected_alternatives:
        add(item["evidence_refs"], f"Rejected alternative: {item['alternative']}")
    for item in result.actions:
        add(item["evidence_refs"], f"Action: {item['description']}")
    for item in result.unresolved_items:
        add(item["evidence_refs"], f"Unresolved: {item['description']}")
    add(result.uncertainty["evidence_refs"], "Uncertainty")
    return supports


def _project_item(
    item: Mapping[str, Any],
    *,
    citation_by_ref: Mapping[str, str],
) -> dict[str, Any]:
    projected = dict(item)
    projected.pop("evidence_refs", None)
    projected["citations"] = _citation_ids(item["evidence_refs"], citation_by_ref)
    if "owner" in projected:
        projected["owner_label"] = projected["owner"] or "Unassigned"
    if "deadline" in projected:
        projected["deadline_label"] = projected["deadline"] or "Not recorded"
    return projected


def project_run_snapshot(
    *,
    query: str,
    workspace: WorkspacePresentationContext,
    status: RunStatus | Mapping[str, Any],
    result_artifact: Mapping[str, Any] | None = None,
    tool_ledger: Iterable[ToolExecutionRecord | Mapping[str, Any]] = (),
    source_documents: Iterable[NoteDocument] = (),
) -> dict[str, Any]:
    """Return one complete, JSON-safe product presentation snapshot."""

    if not isinstance(query, str) or not query.strip():
        raise UIProjectionError("UI_QUERY_ERROR", "A non-empty decision question is required.")
    if not isinstance(workspace, WorkspacePresentationContext):
        raise UIProjectionError("UI_WORKSPACE_ERROR", "Workspace context is unavailable.")
    status_data = _status_dict(status)
    ledger_records = list(tool_ledger)
    documents = _documents_by_path(source_documents)
    payload = _base_payload(
        query=query,
        workspace=workspace,
        status=status_data,
        tool_ledger=ledger_records,
    )
    run_status = str(status_data.get("status", ""))

    if run_status in {"accepted", "running"}:
        payload["evidence"] = _project_running_evidence(
            collect_observed_evidence(ledger_records),
            documents,
        )
        return payload
    if run_status == "paused":
        payload["kind"] = "attention"
        payload["evidence"] = _project_running_evidence(
            collect_observed_evidence(ledger_records),
            documents,
        )
        return payload
    if run_status in {"failed", "rejected", "stale", "expired"}:
        payload["kind"] = "error"
        payload["error"] = _safe_runtime_error(status_data)
        return payload
    if run_status != "completed":
        raise UIProjectionError("UI_RUN_STATUS_ERROR", "Run status is not supported by the UI.")

    result = _extract_team_decision(result_artifact)
    observed = collect_observed_evidence(ledger_records)
    missing = [ref for ref in result.evidence_refs if ref not in observed]
    if missing:
        raise UIProjectionError(
            "UI_EVIDENCE_CONTRACT_ERROR",
            "TeamDecisionResult references evidence that was not present in observed evidence.",
        )

    citation_by_ref = {
        ref: f"citation-{index}"
        for index, ref in enumerate(result.evidence_refs, start=1)
    }
    support_labels = _claim_support_labels(result)
    payload["kind"] = (
        "insufficient"
        if result.decision["status"] in NULL_DECISION_STATUSES
        else "success"
    )
    payload["decision"] = {
        "value": result.decision["value"],
        "status": result.decision["status"],
        "citations": _citation_ids(result.decision["evidence_refs"], citation_by_ref),
    }
    payload["rationale"] = [
        _project_item(item, citation_by_ref=citation_by_ref)
        for item in result.rationale
    ]
    payload["rejected_alternatives"] = [
        _project_item(item, citation_by_ref=citation_by_ref)
        for item in result.rejected_alternatives
    ]
    payload["actions"] = [
        _project_item(item, citation_by_ref=citation_by_ref)
        for item in result.actions
    ]
    payload["unresolved_items"] = [
        _project_item(item, citation_by_ref=citation_by_ref)
        for item in result.unresolved_items
    ]
    payload["uncertainty"] = _project_item(
        result.uncertainty,
        citation_by_ref=citation_by_ref,
    )
    payload["evidence"] = [
        {
            "id": citation_by_ref[ref],
            "ordinal": index,
            "ref": ref,
            "label": _citation_label(observed[ref]),
            "relative_path": observed[ref]["relative_path"],
            "line_start": observed[ref]["line_start"],
            "line_end": observed[ref]["line_end"],
            "supports": support_labels[ref],
            "source": _source_preview(observed[ref], documents),
        }
        for index, ref in enumerate(result.evidence_refs, start=1)
    ]
    return payload


__all__ = [
    "UIProjectionError",
    "WorkspacePresentationContext",
    "collect_observed_evidence",
    "project_run_snapshot",
]
