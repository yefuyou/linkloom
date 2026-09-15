"""Runtime-backed lifecycle adapter for the read-only Product UI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Lock, Thread
from typing import Any, Protocol
from uuid import uuid4

from linkloom.loader import VaultReader
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest, RunStatus
from linkloom.ui.projection import (
    UI_SCHEMA_VERSION,
    UIProjectionError,
    WorkspacePresentationContext,
    project_run_snapshot,
)


class RunBackend(Protocol):
    def context(self) -> dict[str, Any]: ...

    def start(self, query: str) -> dict[str, Any]: ...

    def inspect(self, run_id: str) -> dict[str, Any] | None: ...


@dataclass
class _RuntimeJob:
    ui_run_id: str
    query: str
    thread_id: str
    initial_status: RunStatus
    final_status: RunStatus | None = None


class RuntimeRunBackend:
    """Run an injected configured engine without owning provider setup."""

    def __init__(
        self,
        *,
        engine: RuntimeEngine,
        workspace: WorkspacePresentationContext,
        max_steps: int = 8,
        max_provider_requests: int = 8,
    ) -> None:
        if not isinstance(engine, RuntimeEngine):
            raise TypeError("RuntimeRunBackend requires a RuntimeEngine.")
        if max_steps < 1 or max_provider_requests < 1:
            raise ValueError("RuntimeRunBackend requires positive execution limits.")
        self.engine = engine
        self.workspace = workspace
        self.max_steps = max_steps
        self.max_provider_requests = max_provider_requests
        self._jobs: dict[str, _RuntimeJob] = {}
        self._lock = Lock()
        self._documents = VaultReader(
            vault_root=engine.vault_root,
            index_path=engine.index_path,
        ).read_notes()

    def context(self) -> dict[str, Any]:
        return {
            "schema_version": UI_SCHEMA_VERSION,
            "workspace": self.workspace.to_dict(),
            "default_query": "",
            "read_only_message": "LinkLoom searches this workspace without changing its notes.",
            "mode": "runtime",
        }

    def start(self, query: str) -> dict[str, Any]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("A non-empty decision question is required.")
        ui_run_id = f"ui-{uuid4().hex[:12]}"
        thread_id = f"ui-thread-{uuid4().hex[:12]}"
        initial = RunStatus(
            run_id=ui_run_id,
            thread_id=thread_id,
            status="accepted",
        )
        job = _RuntimeJob(
            ui_run_id=ui_run_id,
            query=clean_query,
            thread_id=thread_id,
            initial_status=initial,
        )
        with self._lock:
            self._jobs[ui_run_id] = job

        worker = Thread(target=self._execute, args=(job,), daemon=True)
        worker.start()
        return project_run_snapshot(
            query=clean_query,
            workspace=self.workspace,
            status=initial,
        )

    def _execute(self, job: _RuntimeJob) -> None:
        try:
            status = self.engine.start_multi_agent(
                RunRequest(
                    request_id=job.ui_run_id,
                    thread_id=job.thread_id,
                    workflow="team_decision",
                    query=job.query,
                    max_steps=self.max_steps,
                    max_provider_requests=self.max_provider_requests,
                    dry_run=True,
                    caller="product_ui",
                )
            )
        except Exception:
            status = RunStatus(
                run_id=job.ui_run_id,
                thread_id=job.thread_id,
                status="failed",
                error={
                    "code": "UI_RUNTIME_ERROR",
                    "category": "runtime",
                    "message": "The runtime could not complete the request.",
                },
            )
        with self._lock:
            job.final_status = status

    def _current_status(self, job: _RuntimeJob) -> RunStatus:
        with self._lock:
            final = job.final_status
        if final is not None:
            return final
        try:
            return self.engine.inspect(job.thread_id)
        except Exception:
            return job.initial_status

    def _safe_result_artifact(self, status: RunStatus) -> dict[str, Any] | None:
        if not status.result_ref:
            return None
        relative = PurePosixPath(status.result_ref.replace("\\", "/"))
        if (
            relative.is_absolute()
            or PureWindowsPath(status.result_ref).is_absolute()
            or ".." in relative.parts
        ):
            return None
        path = self.engine.checkpoint_dir.joinpath(*relative.parts)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _project(self, job: _RuntimeJob, status: RunStatus) -> dict[str, Any]:
        state = self.engine.checkpointer.get_latest(job.thread_id)
        ledger = state.tool_ledger if state is not None else []
        try:
            snapshot = project_run_snapshot(
                query=job.query,
                workspace=self.workspace,
                status=status,
                result_artifact=self._safe_result_artifact(status),
                tool_ledger=ledger,
                source_documents=self._documents,
            )
        except UIProjectionError as error:
            failed = RunStatus(
                run_id=status.run_id,
                thread_id=status.thread_id,
                status="failed",
                error={
                    "code": error.code,
                    "category": "validation",
                    "message": str(error),
                },
            )
            snapshot = project_run_snapshot(
                query=job.query,
                workspace=self.workspace,
                status=failed,
                tool_ledger=ledger,
            )
        snapshot["run"]["runtime_id"] = snapshot["run"]["id"]
        snapshot["run"]["id"] = job.ui_run_id
        return snapshot

    def inspect(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None:
            return None
        return self._project(job, self._current_status(job))


__all__ = ["RunBackend", "RuntimeRunBackend"]
