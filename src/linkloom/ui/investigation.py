"""Current-workspace, read-only Product UI bridge to the existing Agent Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Callable

from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.errors import CheckpointError
from linkloom.runtime.errors import ToolArgumentValidationError, ValidationError
from linkloom.scanner import scan_vault
from linkloom.ui.backend import RuntimeRunBackend
from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.projection import WorkspacePresentationContext


@dataclass
class _InvestigationJob:
    temporary_root: TemporaryDirectory[str]
    backend: RuntimeRunBackend
    source_names: dict[str, str]
    workspace_id: str
    temporal_scope: tuple[str, str, str] | None = None
    temporal_view_holder: dict[str, Any] | None = None


class ProductInvestigationService:
    """Export a selected source snapshot, then delegate all model/tools to Runtime."""

    def __init__(
        self,
        product_app: ProductApplication,
        *,
        model_factory: Callable[[str], Any] | None = None,
        run_mode: str = "MODEL_NOT_CONFIGURED",
        max_steps: int = 8,
    ) -> None:
        self.product_app = product_app
        self.model_factory = model_factory
        self.run_mode = run_mode
        self.max_steps = max_steps
        self._jobs: dict[str, _InvestigationJob] = {}
        self._lock = Lock()

    def config(self) -> dict[str, Any]:
        return {
            "available": self.model_factory is not None,
            "run_mode": self.run_mode,
            "message": (
                "Agent model is not configured. Start the explicit offline demo to inspect a synthetic run."
                if self.model_factory is None
                else "Investigation reads the selected workspace and never approves Decision Memory."
            ),
        }

    def start(
        self, query: str, workspace_id: str, *,
        temporal_scope: tuple[str, str, str] | None = None,
    ) -> dict[str, Any]:
        if self.model_factory is None:
            raise ProductApplicationError(
                "MODEL_NOT_CONFIGURED", self.config()["message"], status=503
            )
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 4000:
            raise ProductApplicationError("INVALID_QUERY", "Enter a question of at most 4000 characters.")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ProductApplicationError("WORKSPACE_REQUIRED", "Select a workspace first.", status=409)
        workspace, sources = self.product_app.snapshot_investigation_sources(workspace_id)
        temporal_view_holder: dict[str, Any] | None = None
        if temporal_scope is not None:
            if len(temporal_scope) != 3 or not all(
                isinstance(value, str) and value.strip() for value in temporal_scope
            ):
                raise ProductApplicationError("TEMPORAL_SCOPE_INVALID", "A decision slot and as_of are required.")
            subject, relation, as_of = temporal_scope
            temporal_view_holder = {}
            scoped_view = self.product_app.recover_decision(
                subject, relation, as_of, _workspace_id=workspace_id,
            )
            if scoped_view["status"] != "FOUND":
                raise ProductApplicationError(
                    "TEMPORAL_SCOPE_UNVERIFIED", "No reviewed decision is available for that scope.", status=409,
                )
            # A trusted tool and verifier both use this exact run scope. A model
            # may select the lookup but may not change the workspace or date.
            def temporal_lookup(given_subject: str | None, given_relation: str | None,
                                given_as_of: str | None):
                selected = given_subject is None and given_relation is None
                exact = given_subject == subject and given_relation == relation
                if not (selected or exact):
                    raise ValidationError("Reviewed decision lookup exceeds this run's decision scope.")
                if given_as_of not in (None, scoped_view["as_of"]):
                    # Reject before invoking the Product store. The model may
                    # correct this read-only argument, but no other date is read.
                    raise ToolArgumentValidationError(
                        "Reviewed decision lookup date exceeds this run's scope.",
                        details={"reason": "temporal_scope_mismatch", "path": "$.as_of"},
                    )
                return self.product_app.recover_decision(
                    subject, relation, scoped_view["as_of"], _workspace_id=workspace_id,
                )

            def temporal_validator(given_workspace, claim, evidence, read_ids):
                if (given_workspace != workspace_id or claim["subject"] != subject
                        or claim["relation"] != relation or claim["as_of"] != scoped_view["as_of"]):
                    raise ValidationError("Temporal Decision Final exceeds this run's decision scope.")
                verified = self.product_app.verify_temporal_claim(
                    workspace_id, claim, dict(evidence), set(read_ids),
                )
                temporal_view_holder["view"] = deepcopy(verified)
                return verified
        else:
            temporal_lookup = None
            temporal_validator = None
        if not sources:
            raise ProductApplicationError(
                "EMPTY_WORKSPACE", "Import a source into this workspace before investigating.", status=409
            )
        temporary_root = TemporaryDirectory(prefix="linkloom-investigate-")
        try:
            root = Path(temporary_root.name)
            vault = root / "vault"
            vault.mkdir()
            source_names: dict[str, str] = {}
            for source in sources:
                # The repository issued source ID is the path identity. The user name is display only.
                source_id = str(source["source_id"])
                if re.fullmatch(r"src_[0-9a-f]{32}", source_id) is None:
                    raise ProductApplicationError("SOURCE_ID_INVALID", "A source ID is invalid.", status=409)
                relative_path = f"{source_id}.md"
                (vault / relative_path).write_text(str(source["content"]), encoding="utf-8")
                source_names[relative_path] = str(source["name"])
            index = scan_vault(vault, root / "scan").index_path
            engine = RuntimeEngine(
                vault_root=vault,
                index_path=index,
                checkpoint_dir=root / "runtime",
                trace_dir=root / "traces",
                model=self.model_factory(query.strip()),
                decision_memory_store=self.product_app.store,
                decision_memory_workspace_id=workspace_id,
                temporal_decision_lookup=temporal_lookup,
                temporal_claim_validator=temporal_validator,
            )
            backend = RuntimeRunBackend(
                engine=engine,
                workspace=WorkspacePresentationContext(
                    display_name=str(workspace["name"]), document_count=len(sources)
                ),
                max_steps=self.max_steps,
                max_provider_requests=self.max_steps,
                final_protocol=("claim-proposal/v1" if temporal_scope is not None
                                else "team-decision-result/v1"),
                # The current TaskPlan relation vocabulary covers people and
                # projects, not approved business-decision slots. The model
                # still chooses read-only tools; no incompatible plan is sent.
                planning_mode="template" if temporal_scope is not None else None,
            )
            snapshot = backend.start(query.strip())
        except Exception:
            temporary_root.cleanup()
            raise
        with self._lock:
            self._jobs[snapshot["run"]["id"]] = _InvestigationJob(
                temporary_root, backend, source_names, workspace_id,
                temporal_scope, temporal_view_holder,
            )
        return self._decorate(snapshot, None)

    def inspect(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None:
            return None
        snapshot = job.backend.inspect(run_id)
        if snapshot is None:
            return None
        try:
            state = job.backend.engine.checkpointer.get_latest(snapshot["run"]["thread_id"])
        except CheckpointError:
            if snapshot["kind"] != "running":
                raise
            state = None  # The worker may not have created its first checkpoint yet.
        return self._decorate(snapshot, state, source_names=job.source_names,
                              temporal_scope=job.temporal_scope,
                              temporal_view_holder=job.temporal_view_holder)

    def start_temporal(
        self, query: str, workspace_id: str, *, subject: str, relation: str,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        if self.model_factory is None:
            raise ProductApplicationError(
                "MODEL_NOT_CONFIGURED", self.config()["message"], status=503,
            )
        if workspace_id != self.product_app._workspace_id():
            raise ProductApplicationError(
                "WORKSPACE_MISMATCH", "Select the requested workspace before investigating.", status=409,
            )
        view = self.product_app.recover_decision(
            subject, relation, as_of, _workspace_id=workspace_id,
        )
        return self.start(query, workspace_id, temporal_scope=(subject, relation, view["as_of"]))

    def _decorate(self, snapshot, state, *, source_names=None, temporal_scope=None,
                  temporal_view_holder=None):
        snapshot["run_mode"] = self.run_mode
        snapshot["read_only"] = True
        ledger = list(state.tool_ledger) if state is not None else []
        snapshot["metrics"] = {
            "max_model_turns": self.max_steps,
            "model_turns": len(state.model_executions) if state is not None else 0,
            "search_count": sum(item.tool_id == "search_notes" for item in ledger),
            "read_count": sum(item.tool_id == "read_verified_note" for item in ledger),
            "tool_errors": sum(item.status == "failed" for item in ledger),
        }
        snapshot["trace"] = [
            {
                "sequence": item.sequence,
                "tool": item.tool_id,
                "status": item.status,
                "subgoal": item.arguments.get("source_context", {}).get("subgoal_id"),
                "reason": (item.error or {}).get("code"),
                "source": (
                    item.result.get("value", {}).get("relative_path")
                    if item.result and isinstance(item.result.get("value"), dict)
                    else None
                ),
            }
            for item in ledger
        ]
        if snapshot["kind"] == "insufficient":
            snapshot["partial_findings"] = [
                {
                    "source": (item.result or {}).get("value", {}).get("relative_path"),
                    "line": (item.result or {}).get("value", {}).get("line_start"),
                    "quote": (item.result or {}).get("value", {}).get("quote"),
                }
                for item in ledger
                if item.tool_id == "read_verified_note"
                and item.status == "completed"
                and isinstance((item.result or {}).get("value"), dict)
            ]
        for evidence in snapshot["evidence"]:
            evidence["source_name"] = (source_names or {}).get(evidence["relative_path"], evidence["relative_path"])
        for finding in snapshot.get("partial_findings", []):
            finding["source_name"] = (source_names or {}).get(finding["source"], finding["source"])
        if temporal_scope is not None and snapshot["kind"] in {"success", "insufficient"}:
            view = (temporal_view_holder or {}).get("view")
            snapshot["temporal_decision"] = (
                deepcopy(view) if isinstance(view, dict) and view.get("status") == "FOUND"
                else None
            )
        return snapshot


__all__ = ["ProductInvestigationService"]
