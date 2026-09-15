"""Truthful, deterministic `mps-001` fixtures for UI development only."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from linkloom.runtime.models import RunStatus
from linkloom.schemas import NoteDocument
from linkloom.ui.projection import (
    UI_SCHEMA_VERSION,
    WorkspacePresentationContext,
    project_run_snapshot,
)


@dataclass(frozen=True)
class DemoCase:
    """Loaded fixture whose quotes and hashes are derived from frozen notes."""

    case_id: str
    context: WorkspacePresentationContext
    default_query: str
    insufficient_query: str
    success_result: dict[str, Any]
    insufficient_result: dict[str, Any]
    evidence: dict[str, dict[str, Any]]
    documents: list[NoteDocument]

    @classmethod
    def load_mps_001(cls) -> "DemoCase":
        fixture_path = Path(__file__).with_name("demo_data") / "mps-001.json"
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        repository_root = Path(__file__).resolve().parents[3]
        workspace_data = raw["workspace"]
        workspace_root = repository_root / workspace_data["workspace_relative_path"]

        documents: list[NoteDocument] = []
        by_path: dict[str, NoteDocument] = {}
        for relative_path in raw["documents"]:
            path = workspace_root / relative_path
            raw_bytes = path.read_bytes()
            content = raw_bytes.decode("utf-8")
            lines = content.splitlines()
            title = next(
                (line.removeprefix("# ").strip() for line in lines if line.startswith("# ")),
                Path(relative_path).stem,
            )
            document = NoteDocument(
                relative_path=relative_path,
                title=title,
                content=content,
                headings=[],
                tags=[],
                wikilinks=[],
                size_bytes=len(raw_bytes),
                content_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                line_count=len(lines),
            )
            documents.append(document)
            by_path[relative_path] = document

        evidence: dict[str, dict[str, Any]] = {}
        for location in raw["evidence_locations"]:
            document = by_path[location["relative_path"]]
            line_start = int(location.get("line_start", location.get("line")))
            line_end = int(location.get("line_end", line_start))
            quote = "\n".join(document.content.splitlines()[line_start - 1 : line_end])
            evidence_id = location["evidence_id"]
            evidence[evidence_id] = {
                "evidence_id": evidence_id,
                "relative_path": document.relative_path,
                "content_sha256": document.content_sha256,
                "line_start": line_start,
                "line_end": line_end,
                "quote": quote,
                "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                "source_kind": "note_body",
                "reason": "Frozen mps-001 UI development evidence.",
                "status": "verified",
            }

        return cls(
            case_id=raw["case_id"],
            context=WorkspacePresentationContext(
                display_name=workspace_data["display_name"],
                document_count=workspace_data["document_count"],
            ),
            default_query=raw["default_query"],
            insufficient_query=raw["insufficient_query"],
            success_result=raw["success_result"],
            insufficient_result=raw["insufficient_result"],
            evidence=evidence,
            documents=documents,
        )


def _ledger_record(
    call_id: str,
    tool_id: str,
    sequence: int,
    values: list[dict[str, Any]] | dict[str, Any],
) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "run_id": "demo-runtime-run",
        "task_id": "demo-retrieval",
        "agent_id": "retrieval_agent",
        "tool_id": tool_id,
        "sequence": sequence,
        "status": "completed",
        "arguments": {},
        "result": {
            "call_id": call_id,
            "tool_id": tool_id,
            "status": "ok",
            "value": values,
            "error": None,
            "business_status": None,
        },
        "error": None,
    }


class DemoRunBackend:
    """Development-only state replay using the production UI projection."""

    def __init__(self, case: DemoCase) -> None:
        self.case = case
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def context(self) -> dict[str, Any]:
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "workspace": self.case.context.to_dict(),
            "default_query": self.case.default_query,
            "read_only_message": "LinkLoom searches this workspace without changing its notes.",
            "mode": "demo",
        }

    def _all_ledger(self) -> list[dict[str, Any]]:
        return [
            _ledger_record(
                "demo-search",
                "search_notes",
                1,
                list(self.case.evidence.values()),
            )
        ]

    def _reading_ledger(self) -> list[dict[str, Any]]:
        values = list(self.case.evidence.values())[:4]
        return [_ledger_record("demo-search", "search_notes", 1, values)]

    def _checking_ledger(self) -> list[dict[str, Any]]:
        values = list(self.case.evidence.values())[:4]
        return [
            _ledger_record("demo-search", "search_notes", 1, values),
            _ledger_record("demo-read", "read_verified_note", 2, values[0]),
        ]

    def _snapshot(
        self,
        *,
        status: str,
        query: str,
        result: dict[str, Any] | None = None,
        ledger: list[dict[str, Any]] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_status = RunStatus(
            run_id="demo-runtime-run",
            thread_id="demo-runtime-thread",
            status=status,
            result_ref="results/demo-runtime-run/result.json" if status == "completed" else None,
            error=error,
        )
        artifact = None
        if result is not None:
            artifact = {"status": "completed", "result": {"team_decision": result}}
        return project_run_snapshot(
            query=query,
            workspace=self.case.context,
            status=run_status,
            result_artifact=artifact,
            tool_ledger=ledger or [],
            source_documents=self.case.documents,
        )

    def demo_snapshot(self, state: str) -> dict[str, Any] | None:
        if state == "empty":
            return {
                "schema_version": UI_SCHEMA_VERSION,
                "kind": "empty",
                "workspace": WorkspacePresentationContext(
                    display_name="Empty project",
                    document_count=0,
                ).to_dict(),
                "query": "",
                "run": None,
                "decision": None,
                "rationale": [],
                "rejected_alternatives": [],
                "actions": [],
                "unresolved_items": [],
                "uncertainty": None,
                "evidence": [],
                "provenance": None,
                "error": None,
            }
        if state == "running":
            return self._snapshot(
                status="running",
                query=self.case.default_query,
                ledger=self._reading_ledger(),
            )
        if state == "success":
            return self._snapshot(
                status="completed",
                query=self.case.default_query,
                result=self.case.success_result,
                ledger=self._all_ledger(),
            )
        if state == "insufficient":
            return self._snapshot(
                status="completed",
                query=self.case.insufficient_query,
                result=self.case.insufficient_result,
                ledger=self._all_ledger(),
            )
        if state == "error":
            return self._snapshot(
                status="failed",
                query=self.case.default_query,
                error={
                    "code": "TEAM_DECISION_CONTRACT_ERROR",
                    "category": "validation",
                    "message": "The structured team-decision result did not pass validation.",
                },
            )
        return None

    @staticmethod
    def _with_run_id(snapshot: dict[str, Any], run_id: str) -> dict[str, Any]:
        copy = deepcopy(snapshot)
        copy["run"]["runtime_id"] = copy["run"]["id"]
        copy["run"]["id"] = run_id
        return copy

    def start(self, query: str) -> dict[str, Any]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("A non-empty decision question is required.")
        run_id = f"demo-{uuid4().hex[:12]}"
        if clean_query == self.case.default_query:
            target = "success"
        elif clean_query == self.case.insufficient_query:
            target = "insufficient"
        else:
            target = "unsupported"
        with self._lock:
            self._runs[run_id] = {"query": clean_query, "target": target, "polls": 0}
        started = self._snapshot(status="accepted", query=clean_query)
        return self._with_run_id(started, run_id)

    def inspect(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run["polls"] += 1
            polls = run["polls"]
            query = run["query"]
            target = run["target"]

        if polls == 1:
            snapshot = self._snapshot(
                status="running",
                query=query,
                ledger=self._reading_ledger(),
            )
        elif polls == 2:
            snapshot = self._snapshot(
                status="running",
                query=query,
                ledger=self._checking_ledger(),
            )
        elif target == "insufficient":
            snapshot = self.demo_snapshot("insufficient")
        elif target == "unsupported":
            snapshot = self._snapshot(
                status="failed",
                query=query,
                error={
                    "code": "DEMO_QUERY_NOT_AVAILABLE",
                    "category": "demo",
                    "message": "The mps-001 UI fixture replays only its two documented questions.",
                },
            )
        else:
            snapshot = self.demo_snapshot("success")
        assert snapshot is not None
        return self._with_run_id(snapshot, run_id)


__all__ = ["DemoCase", "DemoRunBackend"]
