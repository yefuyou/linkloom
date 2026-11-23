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
from linkloom.agents.claim_grounding import claim_conflicts, normalize_entity
from linkloom.agents.team_decision import _VerifiedClaim
from linkloom.scanner import scan_vault
from linkloom.ui.backend import RuntimeRunBackend
from linkloom.ui.lifecycle import (
    BackgroundShutdownTimeout, BackgroundTasks, BackgroundWorkStopped, DEFAULT_SHUTDOWN_TIMEOUT, shutdown_deadline)
from linkloom.orchestration.files import write_json as _write_json
from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.projection import WorkspacePresentationContext


def _chain_end(claims):
    """The object no other verified claim continues from, when the chain has exactly one end."""
    subjects = {normalize_entity(claim.subject) for claim in claims}
    ends = {normalize_entity(claim.object_value): claim.object_value for claim in claims
            if normalize_entity(claim.object_value) not in subjects}
    return next(iter(ends.values())) if len(ends) == 1 else None


@dataclass
class _InvestigationJob:
    temporary_root: TemporaryDirectory[str] | None
    backend: RuntimeRunBackend
    source_names: dict[str, str]
    source_versions: dict[str, dict[str, str]]
    workspace_id: str
    temporal_scope: tuple[str, str, str] | None = None
    temporal_view_holder: dict[str, Any] | None = None
    source_relation: bool = False
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
        task_only: bool = False,
        parallel_limits=None,
        confirm_over_tasks=None,
    ) -> None:
        from linkloom.retrieval_v2.shared_lexical import WorkspaceLexicalIndexes

        self._lexical_indexes = WorkspaceLexicalIndexes()
        self.product_app = product_app
        self.model_factory = model_factory
        self.task_models = task_models
        self.task_only = task_only
        self.parallel_limits = parallel_limits  # total Provider budget of one orchestrated run (Host configured)
        self.confirm_over_tasks = confirm_over_tasks  # a Supervisor plan larger than this waits for a reviewer
        self._orch_runs = {}
        self._active = set()
        self._parallel_workers = BackgroundTasks()
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
        self._starts = BackgroundTasks()
        self._backends: list[RuntimeRunBackend] = []
        self._provider_sessions: list[Any] = []
        self._temporary_roots: list[TemporaryDirectory[str]] = []
        self.product_app.register_background_owner(self)

    def config(self) -> dict[str, Any]:
        task_available = (self.task_models is not None and self.model_factory is not None
            and (self.task_models.execution_kind == 'LIVE') == self.external_provider)
        task_live = task_available and self.task_models.execution_kind == 'LIVE'
        from dataclasses import asdict
        return {
            "available": self.model_factory is not None,
            "external_provider": self.external_provider,
            "run_mode": self.run_mode,
            "relation_max_model_turns": self.relation_max_steps,
            "task_investigation": task_available,
            "temporal_agent": self.model_factory is not None and not self.task_only,
            "task_provider": dict(configured=task_available, external=bool(task_live),
                model_id=self.task_models.model_id if task_available else None,
                budgets=asdict(self.task_models.provider_limits) if task_live else None,
                source_permission='PER_QUESTION_CURRENT_WORKSPACE_SNAPSHOT',
                execution_permission='HOST_TASK_STAGES_ONLY', business_approval=False,
                setup='Set GEMINI_API_KEY; python -m linkloom.ui --allow-external-provider --allow-task-investigation '
                    '--task-max-requests 16 --task-max-input-tokens 500000 --task-max-output-tokens 100000 '
                    '--task-max-cost-usd 2 --task-max-seconds 120 --task-request-timeout-seconds 30'),
            "task_execution_kinds": ([self.task_models.execution_kind]
                                      if self.task_models is not None else []),
            "message": (
                "Agent model is not configured. Start the explicit offline demo to inspect a synthetic run."
                if self.model_factory is None
                else "Investigation reads the selected workspace and never approves Decision Memory."
            ),
        }

    def pending_task_ids(self):
        with self._lock:
            owners = (*self._backends, *self._provider_sessions)
        return (*self._starts.pending_ids(), *self._parallel_workers.pending_ids(),
                *(task_id for owner in owners for task_id in owner.pending_task_ids()))

    def stop_accepting(self) -> None:
        self._starts.stop_accepting()

    def close(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT, *, deadline=None) -> None:
        deadline = shutdown_deadline(timeout) if deadline is None else deadline
        self.stop_accepting()
        # An admitted preparation may still create and register its Runtime.
        self._starts.wait(deadline)
        self._parallel_workers.stop_accepting()
        self._parallel_workers.wait(deadline)
        with self._lock:
            backends = tuple(self._backends)
            sessions = tuple(self._provider_sessions)
            orchestrations = tuple(self._orch_runs.values())
        for backend in backends:
            backend.stop_accepting()
        for backend in backends:
            backend.close(deadline=deadline)
        for session in sessions:
            session.close(deadline=deadline)
        for run in orchestrations:
            run.close()

    def release_resources(self, *, deadline=None):
        # HTTP inspectors can still read Runtime files after workers finish.
        # The application invokes this only after every registered owner drains.
        with self._lock:
            temporary_roots = tuple(self._temporary_roots)
        for root in temporary_roots:
            root.cleanup()
        self._lexical_indexes.clear()

    def start(
        self, query: str, workspace_id: str, *,
        temporal_scope: tuple[str, str, str] | None = None,
        source_relation: bool = False,
        task_investigation: bool = False,
        task_source_authorization: dict | None = None,
    ) -> dict[str, Any]:
        try:
            with self._starts.operation("investigation-start-" + uuid4().hex[:12]):
                return self._start(query, workspace_id, temporal_scope=temporal_scope,
                    source_relation=source_relation, task_investigation=task_investigation,
                    task_source_authorization=task_source_authorization)
        except BackgroundWorkStopped as error:
            raise ProductApplicationError("APPLICATION_SHUTTING_DOWN", str(error), status=503) from error

    def _start(
        self, query: str, workspace_id: str, *,
        temporal_scope: tuple[str, str, str] | None = None,
        source_relation: bool = False,
        task_investigation: bool = False,
        task_source_authorization: dict | None = None,
    ) -> dict[str, Any]:
        if self.model_factory is None:
            raise ProductApplicationError(
                "MODEL_NOT_CONFIGURED", self.config()["message"], status=503
            )
        if self.task_only and not task_investigation:
            raise ProductApplicationError('AGENT_STAGE_NOT_AUTHORIZED', 'Only task stages are configured.', status=403)
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 4000:
            raise ProductApplicationError("INVALID_QUERY", "Enter a question of at most 4000 characters.")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ProductApplicationError("WORKSPACE_REQUIRED", "Select a workspace first.", status=409)
        if source_relation and temporal_scope is not None:
            raise ProductApplicationError("TASK_SCOPE_CONFLICT", "A source investigation cannot use a decision slot.")
        source_run = source_relation
        if task_investigation and (not self.config()['task_investigation'] or source_run or temporal_scope is not None):
            raise ProductApplicationError("TASK_INVESTIGATION_UNAVAILABLE", "Task stages require explicit Host configuration.", status=409)
        source_run = source_run or task_investigation
        from linkloom.ui.graph_investigation import current_graph_provider_session
        graph_provider_session = current_graph_provider_session()
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
        if temporary_root is not None:
            with self._lock:
                self._temporary_roots.append(temporary_root)
        try:
            root = (Path(temporary_root.name) if temporary_root else
                    Path(self.product_app.repository.database_path).parent / 'work' / 'investigations' / uuid4().hex[:12])
            root.mkdir(parents=True, exist_ok=temporary_root is not None)
            preparation = None
            if task_investigation:
                from linkloom.ui.task_investigation import ProductTaskSources, prepare_task_investigation
                from linkloom.ui.task_provider import TaskProviderSession
                from linkloom.ui.ask_admission import source_context
                task_sources = ProductTaskSources(workspace_id, sources,
                    lambda: self.product_app.snapshot_investigation_sources(workspace_id)[1],
                    current_evidence=lambda: self.product_app.repository.current_evidence(workspace_id))
                provider_session = None
                if task_models.execution_kind == 'LIVE':
                    from linkloom.semantic_ingestion.task_relation_models import canonical_hash
                    expected = dict(workspace_id=workspace_id,
                        snapshot_hash=source_context(self.product_app, workspace_id)['snapshot_hash'],
                        question_sha256=canonical_hash(query.strip()))
                    if task_source_authorization != expected:
                        raise ProductApplicationError('EXTERNAL_PROVIDER_PERMISSION_REQUIRED',
                            'Authorize this question and exact current source snapshot.', status=403)
                    def validate_grant():
                        if (self.product_app._workspace_id() != workspace_id or
                                source_context(self.product_app, workspace_id)['snapshot_hash'] != expected['snapshot_hash']):
                            raise ValueError('TASK_SOURCE_AUTHORIZATION_CHANGED')
                        task_sources.validate_current()
                    provider_session = graph_provider_session
                    if provider_session is None:
                        provider_session = TaskProviderSession(root / 'provider', task_models.provider_limits, validate_grant)
                        with self._lock:
                            self._provider_sessions.append(provider_session)
                preparation = prepare_task_investigation(query.strip(), task_sources, task_models, root / "preparation",
                    relation_path=self.task_relation_paths.get(workspace_id), provider_session=provider_session)
                if preparation.manifest['status'] == 'BLOCKED':
                    return dict(kind='error', run={'id': 'blocked-' + root.name}, evidence=[],
                        reviewer={'performed': False}, preparation=preparation.manifest,
                        error=dict(code=preparation.manifest.get('provider_failure', 'TASK_PLAN_BLOCKED'), classification='validation',
                                   message='The investigation plan did not pass Host admission.'))
            vault, index, source_names, source_versions = self._materialize_sources(root, sources)
            confirmed_fact_index = (None if task_investigation else
                                    self._confirmed_fact_index_factory(workspace_id, sources, source_versions))
            from linkloom.ui.task_provider import _ROUTER_RESERVATION, TaskProviderSession
            if self.external_provider and not task_investigation and graph_provider_session is None and _ROUTER_RESERVATION.get() is not None:
                if task_models is None or task_models.provider_limits is None:
                    raise ProductApplicationError("TASK_PROVIDER_BUDGET_EXHAUSTED", "No remaining Provider budget for investigation.", status=409)
                from linkloom.ui.graph_investigation import _GRAPH_PROVIDER
                spent = _ROUTER_RESERVATION.get()
                graph_provider_session = TaskProviderSession(root / 'provider', task_models.provider_limits, spent.validate)
                with self._lock:
                    self._provider_sessions.append(graph_provider_session)
                _GRAPH_PROVIDER.set(graph_provider_session)
            model = outer_model if outer_model is not None else self.model_factory(query.strip())
            if preparation and preparation.provider_session:
                model = preparation.provider_session.bind(model, 'runtime')
            elif graph_provider_session is not None:
                from linkloom.ui.graph_investigation import bind_graph_fallback
                model = bind_graph_fallback(model, 'legacy-runtime')
            engine = RuntimeEngine(
                lexical_indexes=self._lexical_indexes,
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
                confirmed_fact_index=confirmed_fact_index,
            )
            backend = RuntimeRunBackend(
                engine=engine,
                workspace=WorkspacePresentationContext(
                    display_name=str(workspace["name"]), document_count=len(sources)
                ),
                max_steps=max_steps,
                max_provider_requests=max_steps,
                # A source investigation answers with Typed Claims; the Host checks
                # them deterministically and projects them without re-parsing text.
                final_protocol=("claim-proposal/v1" if temporal_scope is not None or source_run and not preparation
                                else "team-decision-result/v1"),
                # The model plans the investigation itself (when it can propose a
                # plan); the Host validates plan structure only.
                planning_mode="template" if temporal_scope is not None else None,
                workflow="task_relations" if preparation else "team_decision",
            )
            with self._lock:
                self._backends.append(backend)
            snapshot = backend.start(query.strip())
        except Exception:
            # A backend may already be running if projection/setup failed. Keep
            # its files owned until close has drained every worker, even here.
            raise
        with self._lock:
            self._jobs[snapshot["run"]["id"]] = _InvestigationJob(
                temporary_root, backend, source_names, source_versions, workspace_id,
                temporal_scope, temporal_view_holder, source_run and not preparation,
                preparation,
            )
        if preparation is not None:
            # Identity pointer only; all execution state remains in Checkpoint.
            pointer = dict(run_id=snapshot['run']['id'], thread_id=snapshot['run']['thread_id'],
                workspace_id=workspace_id, root=root.name)
            (root.parent / (pointer['run_id'] + '.json')).write_text(json.dumps(pointer), encoding='utf8')
        return self._decorate(snapshot, None, source_relation=source_run and not preparation, max_steps=max_steps)

    def save_task_ask(self, run_id, ask_run):
        job = self._jobs.get(run_id)
        if job is None or job.task_preparation is None:
            return
        path = job.backend.engine.checkpoint_dir.parent.parent / (run_id + '.json')
        pointer = json.loads(path.read_text(encoding='utf8'))
        pointer['ask'] = ask_run
        path.write_text(json.dumps(pointer, ensure_ascii=False), encoding='utf8')

    # --- Orchestrated (LangGraph) investigations --------------------------------------------------------

    def orchestration_enabled(self):
        """The Host configured orchestration budgets; a graph/router Provider reservation uses the old single path."""
        from linkloom.ui.graph_investigation import current_graph_provider_session
        return bool(self.parallel_limits and self.model_factory is not None and not self.task_only
                    and current_graph_provider_session() is None)

    def start_orchestrated(self, query, workspace_id, *, temporal_scope=None, entity_leads=(), blocked_entities=()):
        """Start a Supervisor/Worker run on LangGraph. Host-confirmed scope and unconfirmed leads stay separate."""
        try:
            with self._starts.operation("orchestration-start-" + uuid4().hex[:12]):
                return self._start_orchestrated(query, workspace_id, temporal_scope=temporal_scope,
                    entity_leads=entity_leads, blocked_entities=blocked_entities)
        except BackgroundWorkStopped as error:
            raise ProductApplicationError("APPLICATION_SHUTTING_DOWN", str(error), status=503) from error

    def _start_orchestrated(self, query, workspace_id, *, temporal_scope=None, entity_leads=(), blocked_entities=()):
        from linkloom.orchestration.limits import OrchestrationLimits
        from linkloom.orchestration.runs import OrchestrationRun, load_record, write_record
        from linkloom.ui.task_investigation import ProductTaskSources
        from linkloom.ui.task_provider import remaining_provider_limits
        if self.model_factory is None:
            raise ProductApplicationError("MODEL_NOT_CONFIGURED", self.config()["message"], status=503)
        if not self.orchestration_enabled():
            raise ProductApplicationError("ORCHESTRATION_UNAVAILABLE",
                "Orchestrated investigation needs the configured task budgets.", status=409)
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 4000:
            raise ProductApplicationError("INVALID_QUERY", "Enter a question of at most 4000 characters.")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ProductApplicationError("WORKSPACE_REQUIRED", "Select a workspace first.", status=409)
        workspace, sources = self.product_app.snapshot_investigation_sources(workspace_id)
        if not sources:
            raise ProductApplicationError(
                "EMPTY_WORKSPACE", "Import a source into this workspace before investigating.", status=409)
        confirmed = dict(workspace_id=workspace_id, blocked_entities=[list(fact) for fact in blocked_entities])
        if temporal_scope is not None:
            if len(temporal_scope) != 3 or not all(isinstance(value, str) and value.strip() for value in temporal_scope):
                raise ProductApplicationError("TEMPORAL_SCOPE_INVALID", "A decision slot and as_of are required.")
            subject, relation, as_of = temporal_scope
            view = self.product_app.recover_decision(subject, relation, as_of, _workspace_id=workspace_id)
            if view["status"] != "FOUND":
                raise ProductApplicationError(
                    "TEMPORAL_SCOPE_UNVERIFIED", "No reviewed decision is available for that scope.", status=409)
            confirmed.update(subject=subject, relation=relation, as_of=view["as_of"])
        leads = list(dict.fromkeys(str(name).strip() for name in entity_leads if str(name).strip()))
        if len(leads) > 8 or any(len(name) > 160 for name in leads):
            raise ProductApplicationError("ENTITY_LEADS_INVALID", "At most eight short entity leads are accepted.")
        try:
            provider = remaining_provider_limits(self.parallel_limits)
            limits = OrchestrationLimits(provider=provider, deadline_seconds=float(provider.max_seconds),
                                         confirm_over_tasks=self.confirm_over_tasks)
        except ValueError as error:
            raise ProductApplicationError("TASK_PROVIDER_BUDGET_EXHAUSTED",
                "No remaining Provider budget for investigation.", status=409) from error
        root = self._parallel_base() / uuid4().hex[:12]
        root.mkdir(parents=True)
        run_id = "ui-parallel-" + root.name
        vault, index, source_names, source_versions = self._materialize_sources(root, sources)
        identity = ProductTaskSources._identity(sources)
        write_record(root, query=query.strip(), workspace_id=workspace_id, identity=identity,
            workspace=WorkspacePresentationContext(display_name=str(workspace["name"]), document_count=len(sources)),
            source_names=source_names, source_versions=source_versions, index=index,
            confirmed_scope=confirmed, entity_leads=leads, limits=limits)
        run = OrchestrationRun(root, run_id, load_record(root), model_factory=self.model_factory,
                               validate=self._parallel_validator(workspace_id, identity))
        _write_json(root.parent / (run_id + ".json"), dict(run_id=run_id, thread_id=run_id,
            workspace_id=workspace_id, root=root.name, kind="parallel"))
        with self._lock:
            self._orch_runs[run_id] = run
            self._active.add(run_id)
        self._parallel_workers.start(run_id, lambda: self._drive(run, None))
        return self._orch_snapshot(run)

    def _drive(self, run, approval):
        """Worker thread: run or resume the LangGraph run. All orchestration state lives in its checkpoint."""
        try:
            run.resume(approval) if approval is not None else run.start()  # start resumes an existing checkpoint
        except BaseException as error:  # a stop leaves the checkpoint; a later continue resumes from it
            _write_json(run.root / "failure.json", dict(error=type(error).__name__))
        finally:
            with self._lock:
                self._active.discard(run.run_id)

    def _confirmed_fact_index_factory(self, workspace_id, sources, source_versions):
        """Reload human-confirmed relation facts for this run's frozen sources on every check."""
        from linkloom.agents.confirmed_facts import FrozenSource, load_confirmed_fact_index

        content = {str(source["source_id"]): str(source["content"]) for source in sources}
        frozen = tuple(FrozenSource(path, item["source_id"], item["source_version"], content[item["source_id"]])
                       for path, item in source_versions.items())
        frozen_ids = {(item.source_id, item.source_version) for item in frozen}
        app = self.product_app

        def load():
            with app._store_lock:
                if app._workspace_id() != workspace_id:
                    return None
                active = {(s["source_id"], s["source_version"]) for s in app.repository.list_sources(workspace_id)
                          if s.get("active", True)}
            allowed = frozen_ids & active
            return load_confirmed_fact_index(
                app.repository.database_path, workspace_id, frozen,
                authorize_source=lambda ws, source, version: ws == workspace_id and (source, version) in allowed,
            )
        return load

    @staticmethod
    def _materialize_sources(root, sources):
        """Copy the frozen source versions into a read-only vault and index them for the Workers' retrieval."""
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
        return vault, index, source_names, source_versions

    def _orch_snapshot(self, run):
        """Render one run from its LangGraph state. Source authorization is re-checked on every render."""
        from linkloom.orchestration.runs import is_cancelled
        from linkloom.orchestration.snapshot import ABORTED, CANCELLED, render_snapshot
        withheld = None
        if is_cancelled(run.root):
            withheld = (CANCELLED, "PARALLEL_CANCELLED")
        else:
            try:
                self._parallel_validator(run.record["workspace_id"], run.record["identity"])()
            except Exception:
                withheld = (ABORTED, "TASK_SOURCE_AUTHORIZATION_CHANGED")
        try:
            values = run.state()
        except Exception:
            return self._parallel_failure(run.run_id, "PARALLEL_STATE_UNREADABLE", run.record["query"], run.workspace)
        with self._lock:
            running = run.run_id in self._active
        return render_snapshot(run.run_id, run.record["query"], run.workspace, values, running=running,
                               withheld=withheld, pending=values.get("interrupts") or ())

    def save_parallel_ask(self, run_id, ask_run):
        """Keep the Ask identity next to the pointer so a restarted server can show this run."""
        path = self._parallel_base() / (run_id + '.json')
        if run_id in self._orch_runs and path.is_file():
            pointer = json.loads(path.read_text(encoding='utf8'))
            pointer['ask'] = ask_run
            _write_json(path, pointer)

    def _parallel_base(self):
        return Path(self.product_app.repository.database_path).parent / 'work' / 'investigations'

    def _parallel_pointer(self, run_id, workspace_id):
        """Validated (root, pointer) of a saved orchestrated run, or None. The caller holds the store lock."""
        from linkloom.orchestration.runs import CHECKPOINT, RECORD
        if re.fullmatch(r'ui-parallel-[0-9a-f]{12}', run_id) is None:
            return None
        base = self._parallel_base()
        pointer_path = base / (run_id + '.json')
        if not pointer_path.is_file() or self.product_app._workspace_id() != workspace_id:
            return None
        try:
            pointer = json.loads(pointer_path.read_text(encoding='utf8'))
            if (pointer.get('workspace_id') != workspace_id or pointer.get('run_id') != run_id
                    or pointer.get('kind') != 'parallel' or re.fullmatch(r'[0-9a-f]{12}', pointer['root']) is None
                    or run_id != 'ui-parallel-' + pointer['root']):
                return None
            root = (base / pointer['root']).resolve()
            if not root.is_relative_to(base.resolve()) or not (root / RECORD).is_file() \
                    or not (root / CHECKPOINT).is_file():
                return None
        except Exception:
            return None
        return root, pointer

    def _ask_of(self, pointer, root, workspace_id):
        from linkloom.orchestration.runs import load_record
        if isinstance(pointer.get('ask'), dict):
            return pointer['ask']
        route = dict(intent='INVESTIGATION', route='INVESTIGATION', code='SOURCE_INVESTIGATION',
                     reason='The Supervisor delegated this investigation to Workers.')
        return dict(query=load_record(root)['query'], workspace_id=workspace_id, scope={}, route=route)

    def _parallel_validator(self, workspace_id, identity):
        def validate():
            from linkloom.ui.task_investigation import ProductTaskSources
            _, current = self.product_app.snapshot_investigation_sources(workspace_id)
            if ProductTaskSources._identity(current) != identity:
                raise ValueError('TASK_SOURCE_AUTHORIZATION_CHANGED')
        return validate

    def _open_saved(self, root, run_id, workspace_id):
        """A run object for a saved directory: the live one if this process owns it, else read from disk."""
        from linkloom.orchestration.runs import OrchestrationRun, load_record
        with self._lock:
            live = self._orch_runs.get(run_id)
        if live is not None:
            return live, False
        record = load_record(root)
        return OrchestrationRun(root, run_id, record, model_factory=self.model_factory,
                                validate=self._parallel_validator(workspace_id, record['identity'])), True

    def read_saved_parallel(self, run_id, workspace_id):
        """Read an orchestrated run from its LangGraph checkpoint. Never calls a model and never writes there."""
        with self.product_app._store_lock:
            found = self._parallel_pointer(run_id, workspace_id)
            if found is None:
                return None
            root, pointer = found
            try:
                ask = self._ask_of(pointer, root, workspace_id)
                run, cold = self._open_saved(root, run_id, workspace_id)
            except Exception:
                return None
            try:
                return ask, self._orch_snapshot(run)
            finally:
                if cold:
                    run.close()

    def parallel_trace(self, run_id, workspace_id, *, provider_calls_outside_run=True):
        """Read-only trace of an orchestrated run as (source, trace), or None. Never calls a model or writes.

        ``provider_calls_outside_run``: whether this Ask may have used a Provider whose journal is not
        under the run directory (Router / security gate). Telemetry then never claims complete coverage.
        """
        from linkloom.orchestration.trace import build_trace
        if not self.parallel_limits or re.fullmatch(r'ui-parallel-[0-9a-f]{12}', str(run_id)) is None:
            return None
        with self.product_app._store_lock:
            if self.product_app._workspace_id() != workspace_id:
                return None
            found = self._parallel_pointer(run_id, workspace_id)
            if found is None:
                return None
            try:
                run, cold = self._open_saved(found[0], run_id, workspace_id)
            except Exception:
                return None
            with self._lock:
                source = 'LIVE' if not cold else 'SAVED'
            try:
                snapshot = self._orch_snapshot(run)
                outcome = snapshot['parallel_investigation']
                trace = build_trace(run.root, run_id, run.state(), outcome)
                trace['telemetry'] = _ask_telemetry(run.root, run_id, workspace_id, outcome, trace, snapshot,
                                                    (found[1].get('ask') or {}).get('route') or {},
                                                    provider_calls_outside_run)
                return source, trace
            except Exception:
                raise ProductApplicationError('TRACE_UNREADABLE', 'The saved call chain cannot be read.', status=409)
            finally:
                if cold:
                    run.close()

    @staticmethod
    def _parallel_failure(run_id, code, query=None, workspace=None):
        from linkloom.runtime.models import RunStatus
        from linkloom.ui.projection import project_run_snapshot
        snapshot = project_run_snapshot(query=query or 'Saved orchestrated investigation',
            workspace=workspace or WorkspacePresentationContext(display_name='Workspace'),
            status=RunStatus(run_id=run_id, thread_id=run_id, status='failed',
                             error=dict(code=code, category='runtime', message='Orchestrated investigation stopped.')))
        snapshot['parallel_investigation'] = dict(status='FAILED', reason=code, tasks=[], branch_states=[],
                                                  continuable=[])
        snapshot['read_only'] = True
        return snapshot

    def continue_parallel(self, run_id, workspace_id, *, allow_external_provider=False, approval=None):
        """Resume a saved run from its LangGraph checkpoint. Accepted tasks are reused, never paid again."""
        if self.external_provider and allow_external_provider is not True:
            raise ProductApplicationError('EXTERNAL_PROVIDER_PERMISSION_REQUIRED',
                'Allow this bounded external investigation before source text is sent or model costs are incurred.',
                status=403)
        try:
            with self._starts.operation('parallel-continue-' + uuid4().hex[:12]):
                return self._continue_parallel(run_id, workspace_id, approval)
        except BackgroundWorkStopped as error:
            raise ProductApplicationError('APPLICATION_SHUTTING_DOWN', str(error), status=503) from error

    def _continue_parallel(self, run_id, workspace_id, approval):
        from linkloom.orchestration.runs import is_cancelled
        if approval is not None and not isinstance(approval, dict):
            raise ProductApplicationError('INVALID_APPROVAL', 'A reviewer decision must be an object.')
        if not self.orchestration_enabled():
            raise ProductApplicationError('PARALLEL_CONTINUE_UNAVAILABLE',
                'Continuing a run needs the configured orchestrated investigation.', status=409)
        with self.product_app._store_lock:
            if self.product_app._workspace_id() != workspace_id:
                raise ProductApplicationError('WORKSPACE_MISMATCH',
                    'Select the requested workspace before continuing.', status=409)
            found = self._parallel_pointer(run_id, workspace_id)
            if found is None:
                raise ProductApplicationError('RUN_NOT_FOUND', 'The parallel run was not found.', status=404)
            root, pointer = found
            with self._lock:
                if run_id in self._active:
                    raise ProductApplicationError('PARALLEL_RUN_ACTIVE', 'This run is still running.', status=409)
            try:
                run, cold = self._open_saved(root, run_id, workspace_id)
                self._parallel_validator(workspace_id, run.record['identity'])()
            except Exception as error:
                raise ProductApplicationError('TASK_SOURCE_AUTHORIZATION_CHANGED',
                    'The authorized sources changed; ask again.', status=409) from error
            try:
                values = run.state()
                if is_cancelled(root) or values.get('status') == 'ABORTED':
                    raise ProductApplicationError('PARALLEL_RUN_STOPPED', 'The run was stopped; nothing is continued.',
                                                  status=409)
                if values.get('status', 'RUNNING') != 'RUNNING':
                    raise ProductApplicationError('NO_CONTINUABLE_BRANCH', 'The run has already finished.', status=409)
                if values.get('interrupts') and approval is None:
                    raise ProductApplicationError('CONFIRMATION_REQUIRED',
                        'The Supervisor plan waits for a reviewer decision.', status=409)
                if not values.get('interrupts'):
                    approval = None  # nothing is waiting for a human; a stray decision resumes nothing
            except BaseException:
                if cold:
                    run.close()
                raise
            with self._lock:
                self._orch_runs[run_id] = run
                self._active.add(run_id)
            self._parallel_workers.start(run_id + '-resume', lambda: self._drive(run, approval))
            return self._ask_of(pointer, root, workspace_id), self._orch_snapshot(run)

    @staticmethod
    def _task_blocked(run_id, code):
        message = ('Provider outcome is uncertain; no automatic retry or conclusion was released.'
                   if code in {'PROVIDER_UNCERTAIN', 'PROVIDER_TIMEOUT_UNCERTAIN'}
                   else 'Task execution or saved evidence did not pass revalidation; no conclusion was released.')
        return dict(kind='error', run=dict(id=run_id), reviewer=dict(performed=False), evidence=[],
            error=dict(code=code, classification='validation', message=message))

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
                if artifact.get('review', {}).get('decision') != 'evidence_sufficient':
                    return pointer['ask'], self._task_blocked(run_id, 'SAVED_RESULT_REVIEW_NOT_ACCEPTED')
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
        if preparation.manifest['execution_kind'] == 'LIVE' and snapshot.get('kind') != 'running':
            from linkloom.ui.task_provider import validate_task_provider_receipt
            validate_task_provider_receipt(root / 'provider' / 'budget.json', state.updated_at)
        payload = snapshot.get('task_investigation')
        if payload:
            from linkloom.agents.task_relation_result import review_task_relation_result, task_relation_task_id
            from linkloom.runtime.artifacts import ModelArtifactStore
            tools = preparation.tools.for_run(state.run_id, ModelArtifactStore(root / 'runtime' / 'models'),
                                             preparation.manifest['question'])
            check = review_task_relation_result(payload, tools, records=state.tool_ledger, run_id=state.run_id,
                task_id=task_relation_task_id(state.model_executions, state.run_id))
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
            orchestrated = self._orch_runs.get(run_id)
            job = self._jobs.get(run_id)
            if (job and job.task_preparation and job.task_preparation.provider_session
                    and job.task_preparation.provider_session.failure):
                return self._task_blocked(run_id, job.task_preparation.provider_session.failure)
        if orchestrated is not None:
            with self.product_app._store_lock:
                if self.product_app._workspace_id() != orchestrated.record['workspace_id']:
                    return None
                return self._orch_snapshot(orchestrated)
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
                session = job.task_preparation.provider_session
                if session and snapshot['kind'] == 'running':
                    from time import monotonic
                    if monotonic() - session.started > session.limits.max_seconds:
                        return self._task_blocked(run_id, 'TASK_PROVIDER_BUDGET_EXHAUSTED')
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
                                  temporal_view_holder=job.temporal_view_holder, source_relation=job.source_relation,
                                  max_steps=job.backend.max_steps)

    def start_temporal(
        self, query: str, workspace_id: str, *, subject: str, relation: str,
        as_of: str | None = None, entity_leads=(), blocked_entities=(),
    ) -> dict[str, Any]:
        try:
            with self._starts.operation("temporal-start-" + uuid4().hex[:12]):
                return self._start_temporal(query, workspace_id, subject=subject, relation=relation, as_of=as_of,
                                            entity_leads=entity_leads, blocked_entities=blocked_entities)
        except BackgroundWorkStopped as error:
            raise ProductApplicationError("APPLICATION_SHUTTING_DOWN", str(error), status=503) from error

    def _start_temporal(
        self, query: str, workspace_id: str, *, subject: str, relation: str,
        as_of: str | None = None, entity_leads=(), blocked_entities=(),
    ) -> dict[str, Any]:
        if self.task_only:
            raise ProductApplicationError('AGENT_STAGE_NOT_AUTHORIZED', 'Temporal stages are not enabled.', status=403)
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
        scope = (subject, relation, view["as_of"])
        if self.orchestration_enabled():
            return self._start_orchestrated(query, workspace_id, temporal_scope=scope,
                                            entity_leads=entity_leads, blocked_entities=blocked_entities)
        return self._start(query, workspace_id, temporal_scope=scope)

    def cancel_parallel(self, run_id: str) -> bool:
        """Cancel is a HARD stop: the next Provider call is refused and nothing from this run is published."""
        with self._lock:
            run = self._orch_runs.get(run_id)
        if run is None:
            return False
        run.cancel()
        return True

    def _decorate(self, snapshot, state, *, source_names=None, source_versions=None, temporal_scope=None,
                  temporal_view_holder=None, source_relation=False, max_steps=None):
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
            snapshot["planning"]["plan"] = snapshot.get("task_plan")
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
        if source_relation and snapshot["kind"] in {"success", "insufficient"}:
            self._source_relations(snapshot, source_versions or {})
        return snapshot

    def _source_relations(self, snapshot, versions):
        """Project the Host-verified Typed Claims of a source investigation; nothing is re-parsed."""
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
        claims = snapshot.get("typed_claims") or []
        facts = [{"subject": claim["subject"], "predicate": claim["predicate"], "value": claim["object"],
                  "valid_on": claim["valid_on"], "evidence_refs": list(claim["evidence_ids"])} for claim in claims]
        parsed = [_VerifiedClaim(claim["subject"], claim["predicate"], claim["object"], tuple(claim["evidence_ids"]),
                                 claim["quote"], claim["valid_on"], claim["temporal_kind"]) for claim in claims]
        conflicts = {f"{left.predicate}:{left.subject}": list(dict.fromkeys((*left.evidence_ids, *right.evidence_ids)))
                     for left, right in claim_conflicts(parsed)}
        snapshot["source_investigation"] = {"plan": snapshot.get("task_plan"), "facts": facts,
            "thread_id": snapshot["run"]["thread_id"], "runtime_id": snapshot["run"].get("runtime_id"),
            "answer": _chain_end(parsed) if snapshot["kind"] == "success" and not conflicts else None,
            "evidence_level": "SOURCE_SUPPORTED" if facts else "UNRESOLVED", "trace": snapshot["trace"],
            "conflicts": conflicts}


__all__ = ["ProductInvestigationService"]


def _ask_telemetry(root, run_id, workspace_id, outcome, trace, snapshot, route, provider_calls_outside_run):
    """Project #135's read-only telemetry from the run's own Provider journals (budget.json).

    Reservations, Provider-reported usage, rate-based estimates and the (unknown) bill stay separate;
    nothing is read outside the run directory and no model is called.
    """
    from linkloom.observability.ask_telemetry import build_ask_telemetry
    from linkloom.orchestration.files import read_json
    root = Path(root).resolve()
    journals = {}
    scopes = [('comparison', root / 'comparison')]
    scopes += [(f'supervisor/{path.name}', path) for path in sorted((root / 'supervisor').glob('round-*'))]
    scopes += [(f'tasks/{path.parent.name}/{path.name}', path) for path in sorted((root / 'tasks').glob('*/attempt-*'))]
    for scope, directory in scopes:
        directory = directory.resolve()
        if not directory.is_relative_to(root) or not (directory / 'budget.json').exists():
            continue
        journal = read_json(directory / 'budget.json')
        journals[scope] = journal if isinstance(journal, dict) else None
    withheld = outcome.get('status') in {'ABORTED', 'CANCELLED'} or snapshot.get('kind') == 'error'
    authorized = set() if withheld else {
        item[key] for item in snapshot.get('evidence') or () for key in ('evidence_id', 'evidence_ref') if item.get(key)}
    router = route.get('router') if isinstance(route.get('router'), dict) else None
    return build_ask_telemetry(
        run_id=run_id, workspace_id=workspace_id, status=str(outcome.get('status') or trace.get('status')),
        orchestration_trace=trace, router=router, provider_journals=journals,
        # The Router/security journals are not linked to this run, so coverage is complete only
        # when the Host had no such Provider for this Ask.
        provider_journals_complete=not provider_calls_outside_run and router is None,
        branch_states=outcome.get('branch_states') or (), authorized_evidence_refs=authorized,
    )
