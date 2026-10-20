"""Current-workspace, read-only Product UI bridge to the existing Agent Runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from copy import deepcopy
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.context_engineering import ContextRuntimeConfig
from linkloom.runtime.errors import CheckpointError
from linkloom.runtime.errors import ToolArgumentValidationError, ValidationError
from linkloom.runtime.task_plan import TaskPlan, assess_task_plan, verified_plan_conflict_refs
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.agents.claim_grounding import assess_claim_text, ClaimSupportStatus, TYPED_CLAIM_RELATIONS
from linkloom.ui.projection import collect_observed_evidence
from linkloom.scanner import scan_vault
from linkloom.ui.backend import RuntimeRunBackend
from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.projection import WorkspacePresentationContext


@dataclass
class _InvestigationJob:
    temporary_root: TemporaryDirectory[str] | None
    backend: RuntimeRunBackend
    source_names: dict[str, str]
    source_versions: dict[str, dict[str, str]]
    workspace_id: str
    temporal_scope: tuple[str, str, str] | None = None
    temporal_view_holder: dict[str, Any] | None = None
    task_plan: TaskPlan | None = None
    task_preparation: Any = None


class ProductInvestigationService:
    """Export a selected source snapshot, then delegate all model/tools to Runtime."""

    def __init__(
        self,
        product_app: ProductApplication,
        *,
        model_factory: Callable[[str], Any] | None = None,
        run_mode: str = "MODEL_NOT_CONFIGURED",
        max_steps: int = 8,
        external_provider: bool = True,
        relation_max_steps: int = 12,
        task_models: Any = None,
        task_relation_paths: dict[str, Path] | None = None,
    ) -> None:
        self.product_app = product_app
        self.model_factory = model_factory
        self.task_models = task_models
        # Local Host composition only. HTTP/model arguments cannot choose paths.
        self.task_relation_paths = dict(task_relation_paths or {})
        self.run_mode = run_mode
        self.max_steps = max_steps
        self.external_provider = external_provider
        if isinstance(relation_max_steps, bool) or not isinstance(relation_max_steps, int) or not 1 <= relation_max_steps <= 12:
            raise ValueError("Source investigation allows 1–12 model turns.")
        self.relation_max_steps = relation_max_steps
        self._jobs: dict[str, _InvestigationJob] = {}
        self._lock = Lock()

    def config(self) -> dict[str, Any]:
        return {
            "available": self.model_factory is not None,
            "external_provider": self.external_provider,
            "run_mode": self.run_mode,
            "relation_max_model_turns": self.relation_max_steps,
            "task_investigation": self.task_models is not None and not self.external_provider,
            "task_execution_kinds": ([self.task_models.execution_kind]
                                      if self.task_models is not None else []),
            "message": (
                "Agent model is not configured. Start the explicit offline demo to inspect a synthetic run."
                if self.model_factory is None
                else "Investigation reads the selected workspace and never approves Decision Memory."
            ),
        }

    def start(
        self, query: str, workspace_id: str, *,
        temporal_scope: tuple[str, str, str] | None = None,
        task_plan: TaskPlan | None = None,
        task_investigation: bool = False,
    ) -> dict[str, Any]:
        if self.model_factory is None:
            raise ProductApplicationError(
                "MODEL_NOT_CONFIGURED", self.config()["message"], status=503
            )
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 4000:
            raise ProductApplicationError("INVALID_QUERY", "Enter a question of at most 4000 characters.")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ProductApplicationError("WORKSPACE_REQUIRED", "Select a workspace first.", status=409)
        if task_plan is not None:
            expected = plan_relation_investigation(query)
            if temporal_scope is not None or expected is None or expected.to_dict() != task_plan.to_dict():
                raise ProductApplicationError("INVALID_RELATION_PLAN", "Only a query-bound supported relation plan can start.")
        source_run = task_plan is not None
        offline_unavailable = self.task_models is None or self.external_provider
        if task_investigation and (offline_unavailable or source_run or temporal_scope is not None):
            raise ProductApplicationError("TASK_INVESTIGATION_UNAVAILABLE", "Task investigation requires explicitly configured offline stage adapters.", status=409)
        if task_investigation and getattr(self.task_models, "execution_kind", None) not in {"FAKE", "REPLAY"}:
            raise ProductApplicationError("TASK_INVESTIGATION_UNAVAILABLE", "Only explicitly configured offline task stages are enabled.", status=409)
        source_run = source_run or task_investigation
        max_steps = self.relation_max_steps if source_run else self.max_steps
        workspace, sources = self.product_app.snapshot_investigation_sources(workspace_id)
        task_models, outer_model = self.task_models, None
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
        temporary_root = None if task_investigation else TemporaryDirectory(prefix="linkloom-investigate-")
        try:
            root = (Path(temporary_root.name) if temporary_root else
                    Path(self.product_app.repository.database_path).parent / 'work' / 'investigations' / uuid4().hex[:12])
            root.mkdir(parents=True, exist_ok=temporary_root is not None)
            preparation = None
            if task_investigation:
                from linkloom.ui.task_investigation import ProductTaskSources, prepare_task_investigation
                task_sources = ProductTaskSources(workspace_id, sources,
                    lambda: self.product_app.snapshot_investigation_sources(workspace_id)[1],
                    current_evidence=lambda: self.product_app.repository.current_evidence(workspace_id))
                preparation = prepare_task_investigation(query.strip(), task_sources, task_models, root / "preparation",
                    relation_path=self.task_relation_paths.get(workspace_id))
                if preparation.manifest['status'] == 'BLOCKED':
                    return dict(kind='error', run={'id': 'blocked-' + root.name}, evidence=[],
                        reviewer={'performed': False}, preparation=preparation.manifest,
                        error=dict(code='TASK_PLAN_BLOCKED', classification='validation',
                                   message='The investigation plan did not pass Host admission.'))
            vault = root / "vault"
            vault.mkdir()
            source_names: dict[str, str] = {}
            source_versions: dict[str, dict[str, str]] = {}
            for source in sources:
                # The repository issued source ID is the path identity. The user name is display only.
                source_id = str(source["source_id"])
                if re.fullmatch(r"src_[0-9a-f]{32}", source_id) is None:
                    raise ProductApplicationError("SOURCE_ID_INVALID", "A source ID is invalid.", status=409)
                relative_path = f"{source_id}.md"
                (vault / relative_path).write_text(str(source["content"]), encoding="utf-8")
                source_names[relative_path] = str(source["name"])
                source_versions[relative_path] = {"source_id": source_id, "source_version": str(source["source_version"])}
            index = scan_vault(vault, root / "scan").index_path
            model = outer_model if outer_model is not None else self.model_factory(query.strip())
            engine = RuntimeEngine(
                vault_root=vault,
                index_path=index,
                checkpoint_dir=root / "runtime",
                trace_dir=root / "traces",
                model=model,
                decision_memory_store=self.product_app.store if not source_run else None,
                decision_memory_enabled=not source_run,
                memory_enabled=not source_run,
                decision_memory_workspace_id=workspace_id,
                temporal_decision_lookup=temporal_lookup,
                temporal_claim_validator=temporal_validator,
                task_relation_tools=preparation.tools if preparation else None,
                context_runtime_config=ContextRuntimeConfig() if preparation else None,
            )
            backend = RuntimeRunBackend(
                engine=engine,
                workspace=WorkspacePresentationContext(
                    display_name=str(workspace["name"]), document_count=len(sources)
                ),
                max_steps=max_steps,
                max_provider_requests=max_steps,
                final_protocol=("claim-proposal/v1" if temporal_scope is not None
                                else "team-decision-result/v1"),
                # The current TaskPlan relation vocabulary covers people and
                # projects, not approved business-decision slots. The model
                # still chooses read-only tools; no incompatible plan is sent.
                planning_mode="template" if temporal_scope is not None or task_plan is not None else None,
                workflow="task_relations" if preparation else "team_decision",
            )
            snapshot = backend.start(query.strip(), task_plan=task_plan.to_dict() if task_plan else None)
        except Exception:
            if temporary_root is not None:
                temporary_root.cleanup()
            raise
        with self._lock:
            self._jobs[snapshot["run"]["id"]] = _InvestigationJob(
                temporary_root, backend, source_names, source_versions, workspace_id,
                temporal_scope, temporal_view_holder, task_plan,
                preparation,
            )
        if preparation is not None:
            # Identity pointer only; all execution state remains in Checkpoint.
            pointer = dict(run_id=snapshot['run']['id'], thread_id=snapshot['run']['thread_id'],
                workspace_id=workspace_id, root=root.name)
            (root.parent / (pointer['run_id'] + '.json')).write_text(json.dumps(pointer), encoding='utf8')
        return self._decorate(snapshot, None, task_plan=task_plan, max_steps=max_steps)

    def save_task_ask(self, run_id, ask_run):
        job = self._jobs.get(run_id)
        if job is None or job.task_preparation is None:
            return
        path = job.backend.engine.checkpoint_dir.parent.parent / (run_id + '.json')
        pointer = json.loads(path.read_text(encoding='utf8'))
        pointer['ask'] = ask_run
        path.write_text(json.dumps(pointer, ensure_ascii=False), encoding='utf8')

    @staticmethod
    def _task_blocked(run_id, code):
        return dict(kind='error', run=dict(id=run_id), reviewer=dict(performed=False), evidence=[],
            error=dict(code=code, classification='validation', message='Saved evidence is no longer current; ask again.'))

    def read_saved_task(self, run_id, workspace_id):
        """Read existing Runtime artifacts only. Never resume execution or call a model."""
        if re.fullmatch(r'ui-[0-9a-f]{12}', run_id) is None:
            return None
        with self.product_app._store_lock:
            if self.product_app._workspace_id() != workspace_id:
                return None
            base = Path(self.product_app.repository.database_path).parent / 'work' / 'investigations'
            pointer_path = base / (run_id + '.json')
            if not pointer_path.is_file():
                return None
            pointer = json.loads(pointer_path.read_text(encoding='utf8'))
            if pointer.get('workspace_id') != workspace_id or pointer.get('run_id') != run_id:
                return None
            if re.fullmatch(r'[0-9a-f]{12}', pointer['root']) is None:
                return None
            root = base / pointer['root']
            try:
                from linkloom.runtime.checkpoint import SQLiteCheckpointer
                from linkloom.runtime.models import RunStatus
                from linkloom.ui.task_investigation import ProductTaskSources, restore_task_preparation
                from linkloom.ui.task_result import project_task_snapshot
                manifest = json.loads((root / 'preparation' / 'preparation.json').read_text(encoding='utf8'))
                workspace, rows = self.product_app.snapshot_investigation_sources(workspace_id)
                sources = ProductTaskSources(workspace_id, rows,
                    lambda: self.product_app.snapshot_investigation_sources(workspace_id)[1],
                    current_evidence=lambda: self.product_app.repository.current_evidence(workspace_id))
                if sources.identity != manifest['source_snapshot_hash']:
                    return pointer['ask'], self._task_blocked(run_id, 'SOURCE_SNAPSHOT_CHANGED')
                preparation = restore_task_preparation(sources, manifest,
                    relation_path=self.task_relation_paths.get(workspace_id))
                state = SQLiteCheckpointer(root / 'runtime').get_latest(pointer['thread_id'])
                if state is None or state.status not in {'completed', 'failed', 'rejected'} or not state.result_ref:
                    return pointer['ask'], self._task_blocked(run_id, 'SAVED_RESULT_NOT_COMPLETE')
                result_path = (root / 'runtime' / state.result_ref).resolve()
                if not result_path.is_relative_to((root / 'runtime').resolve()):
                    raise ValueError('INVALID_RESULT_PATH')
                artifact = json.loads(result_path.read_text(encoding='utf8'))
                status = RunStatus(state.run_id, state.thread_id, state.status,
                    error=state.error.to_dict() if state.error else None)
                snapshot = project_task_snapshot(manifest['question'], None, status, state, artifact)
                self._validate_task_read(preparation, state, snapshot, root)
                snapshot['run']['id'] = run_id
                self._task_review(snapshot, preparation)
                snapshot['recovery'] = dict(mode='SAVED_RESULT_READ_ONLY', session_resumed=False)
                return pointer['ask'], snapshot
            except Exception:
                return pointer['ask'], self._task_blocked(run_id, 'SAVED_RESULT_REVALIDATION_FAILED')

    @staticmethod
    def _validate_task_read(preparation, state, snapshot, root):
        payload = snapshot.get('task_investigation')
        if payload:
            from linkloom.agents.task_relation_result import review_task_relation_result
            from linkloom.runtime.artifacts import ModelArtifactStore
            tools = preparation.tools.for_run(state.run_id, ModelArtifactStore(root / 'runtime' / 'models'),
                                             preparation.manifest['question'])
            check = review_task_relation_result(payload, tools, records=state.tool_ledger, run_id=state.run_id)
            if check['status'] != 'pass':
                raise ValueError('RELATION_OR_SOURCE_CHANGED')

    def _task_review(self, snapshot, preparation):
        from linkloom.ui.task_result import project_task_review
        preparation.sources.validate_current()
        snapshot.setdefault('preparation', preparation.manifest)
        for evidence in snapshot['evidence']:
            evidence.update(preparation.sources.mapping[evidence['artifact_version_id']])
        snapshot['task_review'] = project_task_review(snapshot, snapshot['preparation'])

    def inspect(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None:
            return None
        if job.task_preparation is not None:
            with self.product_app._store_lock:
                if self.product_app._workspace_id() != job.workspace_id:
                    return None
                try:
                    job.task_preparation.sources.validate_current()
                    try:
                        snapshot = job.backend.inspect(run_id)
                    except Exception:
                        return {"kind": "error", "run": {"id": run_id}, "reviewer": {"performed": False},
                            "evidence": [], "error": {"code": "TASK_PROJECTION_FAILED", "classification": "runtime",
                            "message": "The task Runtime result could not be projected."}}
                    job.task_preparation.sources.validate_current()
                except Exception:
                    return {"kind": "error", "run": {"id": run_id}, "reviewer": {"performed": False},
                        "evidence": [], "error": {"code": "SOURCE_SNAPSHOT_CHANGED", "classification": "validation",
                        "message": "The authorized source snapshot changed; ask again."}}
                if snapshot is None:
                    return None
                if snapshot.get('task_investigation'):
                    state = job.backend.engine.checkpointer.get_latest(snapshot['run']['thread_id'])
                    try:
                        self._validate_task_read(job.task_preparation, state, snapshot,
                                                 job.backend.engine.checkpoint_dir.parent)
                    except Exception:
                        return self._task_blocked(run_id, 'RELATION_OR_SOURCE_CHANGED')
                snapshot['preparation'] = job.task_preparation.manifest
                self._task_review(snapshot, job.task_preparation)
                return snapshot
        with self.product_app._store_lock:
            if self.product_app._workspace_id() != job.workspace_id:
                return None
        snapshot = job.backend.inspect(run_id)
        if snapshot is None:
            return None
        try:
            state = job.backend.engine.checkpointer.get_latest(snapshot["run"]["thread_id"])
        except CheckpointError:
            if snapshot["kind"] not in {"running", "error"}:
                raise
            state = None  # The worker may not have created its first checkpoint yet.
        with self.product_app._store_lock:
            if self.product_app._workspace_id() != job.workspace_id:
                return None
            return self._decorate(snapshot, state, source_names=job.source_names, source_versions=job.source_versions,
                                  temporal_scope=job.temporal_scope,
                                  temporal_view_holder=job.temporal_view_holder, task_plan=job.task_plan,
                                  max_steps=job.backend.max_steps)

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

    def _decorate(self, snapshot, state, *, source_names=None, source_versions=None, temporal_scope=None,
                  temporal_view_holder=None, task_plan=None, max_steps=None):
        snapshot["run_mode"] = self.run_mode
        snapshot["read_only"] = True
        ledger = list(state.tool_ledger) if state is not None else []
        snapshot["metrics"] = {
            "max_model_turns": max_steps or self.max_steps,
            "model_turns": len(state.model_executions) if state is not None else 0,
            "search_count": sum(item.tool_id == "search_notes" for item in ledger),
            "read_count": sum(item.tool_id == "read_verified_note" for item in ledger),
            "tool_errors": sum(item.status == "failed" for item in ledger),
            "planning_provider_requests": snapshot.get("planning", {}).get("provider_requests",
                None if (snapshot.get("error") or {}).get("code", "").startswith("MODEL_PLAN_") else 0),
        }
        if snapshot.get("planning"):
            snapshot["planning"]["plan"] = task_plan.to_dict() if task_plan else snapshot.get("task_plan")
        snapshot["trace"] = [
            {
                "sequence": item.sequence,
                "tool": item.tool_id,
                "status": item.status,
                "subgoal": item.arguments.get("source_context", {}).get("subgoal_id"),
                "query": item.arguments.get("query"),
                "dependencies": item.arguments.get("source_context", {}).get("dependency_entities"),
                "reason": (item.error or {}).get("code"),
                "source": (
                    item.result.get("value", {}).get("relative_path")
                    if item.result and isinstance(item.result.get("value"), dict)
                    else None
                ),
            }
            for item in ledger
        ]
        for row, record in zip(snapshot["trace"], ledger):
            value = (record.result or {}).get("value")
            if isinstance(value, dict) and record.tool_id == "read_verified_note":
                row.update(evidence_ref=value.get("evidence_id"), quote_sha256=value.get("quote_sha256"),
                    **(source_versions or {}).get(value.get("relative_path"), {}))
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
            evidence.update((source_versions or {}).get(evidence["relative_path"], {}))
            read = next((r.result.get("value") for r in ledger if r.tool_id == "read_verified_note"
                         and r.status == "completed" and r.result and isinstance(r.result.get("value"), dict)
                         and r.result["value"].get("evidence_id") == evidence["ref"]), {})
            evidence.update({key: read.get(key) for key in ("content_sha256", "quote_sha256")})
        for finding in snapshot.get("partial_findings", []):
            finding["source_name"] = (source_names or {}).get(finding["source"], finding["source"])
        if temporal_scope is not None and snapshot["kind"] in {"success", "insufficient"}:
            view = (temporal_view_holder or {}).get("view")
            snapshot["temporal_decision"] = (
                deepcopy(view) if isinstance(view, dict) and view.get("status") == "FOUND"
                else None
            )
        if task_plan is not None and snapshot["kind"] in {"success", "insufficient"}:
            self._source_relations(snapshot, state, task_plan, source_versions or {})
        return snapshot

    def _source_relations(self, snapshot, state, plan, versions):
        """Project accepted Final atoms; this never searches or invents a hop."""
        if snapshot.get("reviewer", {}).get("decision") != "evidence_sufficient":
            return
        # A captured run is not a license to present an obsolete product source.
        with self.product_app._store_lock:
            for version in versions.values():
                current = self.product_app.repository.get_source(self.product_app._workspace_id(), version["source_id"])
                if current is None or current["source_version"] != version["source_version"]:
                    snapshot.update(kind="error", evidence=[], decision=None, rationale=[], error={"code": "SOURCE_VERSION_MISMATCH",
                        "classification": "runtime_failure", "message": "A source changed during investigation; ask again."})
                    return
        observed = collect_observed_evidence(r for r in state.tool_ledger if r.tool_id == "read_verified_note")
        cited = {e["id"]: e["ref"] for e in snapshot["evidence"]}
        texts = [(snapshot["decision"]["value"], snapshot["decision"]["citations"])]
        texts.extend((item["point"], item["citations"]) for item in snapshot["rationale"])
        facts = []
        for text, ids in texts:
            if text is None:
                continue
            claims = assess_claim_text(text, [cited[i] for i in ids], observed)
            if claims.status is not ClaimSupportStatus.SUPPORTED:
                raise ValidationError("Accepted source Final cannot be projected as verified facts.")
            for claim in claims.claims:
                predicate = next(p for p, spec in TYPED_CLAIM_RELATIONS.items() if claim.relation in spec["source_relations"])
                literal = re.search(re.escape(claim.value), text, re.I)
                facts.append({"subject": claim.subject, "predicate": predicate,
                    "value": literal.group() if literal else claim.value, "valid_on": claim.valid_on,
                    "evidence_refs": list(claim.supporting_evidence_refs)})
        leaders = [fact["value"] for fact in facts if fact["predicate"] == "reports_to"]
        search = next(r for r in state.tool_ledger if r.tool_id == "search_notes")
        assessment = assess_task_plan(plan, state.tool_ledger, run_id=search.run_id,
            task_id=search.task_id, agent_id=search.agent_id, expected_workspace_id=self.product_app._workspace_id())
        conflicts = {goal.subgoal_id: list(refs) for goal in plan.subgoals
                     if (refs := verified_plan_conflict_refs(plan, goal.subgoal_id, assessment.evidence_by_subgoal))}
        snapshot["source_investigation"] = {"plan": plan.to_dict(), "facts": facts,
            "thread_id": snapshot["run"]["thread_id"], "runtime_id": snapshot["run"].get("runtime_id"),
            "answer": leaders[0] if snapshot["kind"] == "success" and len(set(leaders)) == 1 else None,
            "evidence_level": "SOURCE_SUPPORTED" if facts else "UNRESOLVED", "trace": snapshot["trace"],
            "conflicts": conflicts}


__all__ = ["ProductInvestigationService"]
