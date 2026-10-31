"""Deterministic shutdown barriers; no real providers or timing sleeps."""
from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelResponse
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunStatus
from linkloom.ui.backend import RuntimeRunBackend
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.lifecycle import BackgroundShutdownTimeout, BackgroundTasks, BackgroundWorkStopped, shutdown_deadline
from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.server import create_http_server
from linkloom.ui.task_provider import TaskProviderSession
from tests.unit.test_m3q_provider_budget import limits, request


def launch(call):
    done, values, errors = Event(), [], []
    def run():
        try:
            values.append(call())
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()
    worker = Thread(target=run)
    worker.start()
    return worker, done, values, errors


def finish(state):
    worker, done, values, errors = state
    assert done.wait(5), "test worker did not finish after barrier release"
    worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors
    return values


@pytest.fixture
def app(tmp_path):
    app = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=None)
    app.handle_post("/api/workspaces", {"name": "Shutdown fixture"})
    yield app
    app.close()


def service_for(app, monkeypatch, execute):
    # Seed the normal immutable Product source without an unrelated ingestion job.
    monkeypatch.setattr(app, "_start_ingestion", lambda *args: None)
    app.handle_post("/api/sources/import", {
        "name": "meeting.txt", "content": "[2026-10-08T10:00:00Z] Speaker: The team approved a provider."})
    monkeypatch.setattr(RuntimeEngine, "start_multi_agent", execute)
    return ProductInvestigationService(app,
        model_factory=lambda _: FakeModelAdapter([ModelAction.final("unused")]), external_provider=False)


def completed(request):
    return RunStatus(run_id=request.request_id, thread_id=request.thread_id, status="completed")


@pytest.mark.parametrize("raise_error", [False, True])
def test_application_close_waits_runtime_without_holding_store_lock(app, monkeypatch, raise_error):
    entered, release, worker_done, waiting, database_closed = (Event() for _ in range(5))
    def execute(_engine, request):
        entered.set()
        assert release.wait(5)
        # The close thread must not own this lock while waiting for us.
        with app._store_lock:
            assert app.repository.current_workspace_id()
        worker_done.set()
        if raise_error:
            raise ValueError("synthetic runtime failure")
        return completed(request)
    service = service_for(app, monkeypatch, execute)
    snapshot = service.start("Which provider was approved?", app._workspace_id())
    assert entered.wait(5)
    backend = service._jobs[snapshot["run"]["id"]].backend
    original_wait, original_close = backend._workers.wait, app.store.close
    def wait(deadline):
        waiting.set()
        original_wait(deadline)
    def close_database():
        assert worker_done.is_set()
        database_closed.set()
        original_close()
    monkeypatch.setattr(backend._workers, "wait", wait)
    monkeypatch.setattr(app.store, "close", close_database)
    closing = launch(app.close)
    try:
        assert waiting.wait(5)
        assert not database_closed.is_set()
        assert not closing[1].is_set()
    finally:
        release.set()
    finish(closing)
    assert database_closed.is_set()
    assert not app.pending_task_ids()
    app.close()  # Idempotent; does not close the same connection twice.
    with pytest.raises(ProductApplicationError, match="shutting down"):
        service.start("Another question", "workspace")
    with pytest.raises(BackgroundWorkStopped):
        backend.start("Another question")


def test_runtime_timeout_names_pending_run_and_keeps_database_open_for_retry(app, monkeypatch):
    entered, release = Event(), Event()
    def execute(_engine, request):
        entered.set()
        assert release.wait(5)
        return completed(request)
    service = service_for(app, monkeypatch, execute)
    snapshot = service.start("Which provider was approved?", app._workspace_id())
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert snapshot["run"]["id"] in error.value.task_ids
        assert app.repository.current_workspace_id()
        assert not app._closed
    finally:
        release.set()
    app.close()
    assert app._closed


def test_start_admission_is_registered_before_first_database_read(app, monkeypatch):
    entered, release = Event(), Event()
    snapshot_sources = app.snapshot_investigation_sources
    def snapshot(workspace_id):
        entered.set()
        assert release.wait(5)
        return snapshot_sources(workspace_id)
    service = service_for(app, monkeypatch, lambda _engine, request: completed(request))
    monkeypatch.setattr(app, "snapshot_investigation_sources", snapshot)
    starting = launch(lambda: service.start("Which provider was approved?", app._workspace_id()))
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(task.startswith("investigation-start-") for task in error.value.task_ids)
        assert app.repository.current_workspace_id()
        with pytest.raises(ProductApplicationError, match="shutting down"):
            service.start("New question", "workspace")
    finally:
        release.set()
    finish(starting)
    app.close()


def test_start_failure_does_not_clean_files_under_live_runtime(app, monkeypatch):
    entered, release = Event(), Event()
    def execute(_engine, request):
        entered.set()
        assert release.wait(5)
        return completed(request)
    service = service_for(app, monkeypatch, execute)
    original = RuntimeRunBackend.start
    def fail_after_start(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise ValueError("synthetic projection failure")
    monkeypatch.setattr(RuntimeRunBackend, "start", fail_after_start)
    with pytest.raises(ValueError, match="projection failure"):
        service.start("Which provider was approved?", app._workspace_id())
    assert entered.wait(5)
    root = service._backends[0].engine.vault_root.parent
    try:
        with pytest.raises(BackgroundShutdownTimeout):
            app.close(timeout=0)
        assert root.is_dir()
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    app.close()
    assert not root.exists()


def test_ingestion_worker_is_drained_before_database_close(app, monkeypatch):
    entered, release, used_database = Event(), Event(), Event()
    def ingest(*_args):
        entered.set()
        assert release.wait(5)
        assert app.repository.current_workspace_id()
        used_database.set()
    monkeypatch.setattr(app, "_ingest", ingest)
    source, _ = app.handle_post("/api/sources/import", {
        "name": "meeting.txt", "content": "[2026-10-08T10:00:00Z] Speaker: A decision."})
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(source["source"]["source_id"] in task for task in error.value.task_ids)
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    app.close()
    assert used_database.is_set()


def test_store_lock_wait_is_bounded_and_database_can_close_later(app):
    entered, release = Event(), Event()
    def hold_store():
        with app._store_lock:
            entered.set()
            assert release.wait(5)
    holding = launch(hold_store)
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout, match="application-store-lock"):
            app.close(timeout=0)
        assert not app._closed
    finally:
        release.set()
    finish(holding)
    assert app.repository.current_workspace_id()
    app.close()


def test_concurrent_close_has_one_deadline_and_can_retry(app):
    entered, release = Event(), Event()
    def ingest():
        entered.set()
        assert release.wait(5)
    app._ingestion_workers.start("ingest-barrier", ingest)
    assert entered.wait(5)
    waiting = Event()
    original = app._ingestion_workers.wait
    def wait(deadline):
        waiting.set()
        original(deadline)
    app._ingestion_workers.wait = wait
    closing = launch(app.close)
    assert waiting.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout, match="application-close"):
            app.close(timeout=0)
        assert not app._closed
    finally:
        release.set()
    finish(closing)
    app.close()


def test_http_request_threads_are_drained_and_new_requests_rejected(app, monkeypatch):
    entered, release = Event(), Event()
    server = create_http_server(SimpleNamespace(), product_app=app, port=0)
    serving = launch(server.serve_forever)
    original = app._handle_get
    def handle_get(path):
        entered.set()
        assert release.wait(5)
        return original(path)
    monkeypatch.setattr(app, "_handle_get", handle_get)
    def get():
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/status", timeout=5) as response:
            return response.status
    getting = launch(get)
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(task.startswith("http-request-") for task in error.value.task_ids)
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    try:
        assert finish(getting) == [200]
        app.close()
        rejected = []
        monkeypatch.setattr(server, "shutdown_request", lambda request: rejected.append(request))
        server.process_request("closed-socket", ("127.0.0.1", 1))
        assert rejected == ["closed-socket"]
    finally:
        server.shutdown()
        server.server_close()
        finish(serving)


def test_uncertain_provider_transport_remains_owned_after_runtime_finishes(app, tmp_path, monkeypatch):
    entered, release = Event(), Event()
    class ImmediateTimeout:
        def wait(self, _timeout):
            assert entered.wait(5)
            return False
        def set(self):
            pass
    monkeypatch.setattr("linkloom.ui.task_provider.Event", ImmediateTimeout)
    session = TaskProviderSession(tmp_path / "provider", limits(), lambda: None)
    service = ProductInvestigationService(app)
    service._provider_sessions.append(session)
    def transport(_request):
        entered.set()
        assert release.wait(5)
        return ModelResponse(action=ModelAction.final("unused"))
    response = session.complete(SimpleNamespace(complete=transport), "test", request())
    assert response.error.details["reason"] == "PROVIDER_TIMEOUT_UNCERTAIN"
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(task.startswith("task-provider-") for task in error.value.task_ids)
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    app.close()
    with pytest.raises(BackgroundWorkStopped):
        session.complete(SimpleNamespace(), "test", request())


def test_task_group_completes_finally_and_rejects_new_work_after_stop():
    tasks = BackgroundTasks()
    with pytest.raises(ValueError):
        with tasks.operation("exceptional-setup"):
            raise ValueError("synthetic setup error")
    tasks.stop_accepting()
    tasks.wait(shutdown_deadline(0))
    assert tasks.pending_ids() == ()
    with pytest.raises(BackgroundWorkStopped):
        tasks.start("new-worker", lambda: None)


def test_http_inspector_finishes_before_runtime_files_are_cleaned(app, monkeypatch):
    service = service_for(app, monkeypatch, lambda _engine, request: completed(request))
    snapshot = service.start("Which provider was approved?", app._workspace_id())
    service.close()
    root = service._jobs[snapshot["run"]["id"]].backend.engine.vault_root.parent
    entered, release, waiting = Event(), Event(), Event()
    server = create_http_server(SimpleNamespace(), product_app=app, port=0)
    serving = launch(server.serve_forever)
    def inspect_files(path):
        entered.set()
        assert release.wait(5)
        assert root.is_dir(), "Runtime files disappeared under an admitted inspector"
        return {"ready": False, "message": "inspected Runtime files"}
    monkeypatch.setattr(app, "handle_get", inspect_files)
    def get():
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/status", timeout=5) as response:
            return response.status
    getting = launch(get)
    assert entered.wait(5)
    original_wait = server._requests.wait
    def wait(deadline):
        waiting.set()
        original_wait(deadline)
    monkeypatch.setattr(server._requests, "wait", wait)
    closing = launch(app.close)
    try:
        assert waiting.wait(5)
        assert root.is_dir()
    finally:
        release.set()
    try:
        assert finish(getting) == [200]
        finish(closing)
        assert not root.exists()
    finally:
        server.shutdown()
        server.server_close()
        finish(serving)


def test_finished_operations_and_joined_threads_are_reclaimed():
    tasks = BackgroundTasks()
    for number in range(30):
        with tasks.operation(f"request-{number}"):
            pass
        assert not tasks._tasks
    for number in range(30):
        worker = tasks.start(f"worker-{number}", lambda: None)
        worker.join(5)
        assert not worker.is_alive()
    assert tasks.pending_ids() == ()
    assert not tasks._tasks


def test_completion_signal_does_not_hide_a_thread_that_is_still_alive(monkeypatch):
    import linkloom.ui.lifecycle as lifecycle
    signalled, release = Event(), Event()
    class FinishingEvent:
        def __init__(self):
            self.event = Event()
        def is_set(self):
            return self.event.is_set()
        def set(self):
            self.event.set()
            signalled.set()
            assert release.wait(5)
    monkeypatch.setattr(lifecycle, "Event", FinishingEvent)
    tasks = BackgroundTasks()
    worker = tasks.start("still-returning", lambda: None)
    assert signalled.wait(5)
    try:
        assert tasks.pending_ids() == ("still-returning",)
        with pytest.raises(BackgroundShutdownTimeout, match="still-returning"):
            tasks.wait(shutdown_deadline(0))
    finally:
        release.set()
    worker.join(5)
    tasks.wait(shutdown_deadline(0))
    assert not tasks._tasks


@pytest.mark.parametrize("preparation_raises", [False, True])
def test_blocked_or_exceptional_preparation_keeps_provider_session_owned(app, tmp_path, monkeypatch, preparation_raises):
    from linkloom.semantic_ingestion.task_relation_models import canonical_hash
    from linkloom.ui.ask_admission import source_context
    entered, release = Event(), Event()
    class ImmediateTimeout:
        def wait(self, _timeout):
            assert entered.wait(5)
            return False
        def set(self):
            pass
    monkeypatch.setattr("linkloom.ui.task_provider.Event", ImmediateTimeout)
    service = service_for(app, monkeypatch, lambda _engine, request: completed(request))
    service.external_provider = True
    service.task_models = SimpleNamespace(execution_kind="LIVE", model_id="offline", provider_limits=limits())
    def prepare(*_args, provider_session, **_kwargs):
        def transport(_request):
            entered.set()
            assert release.wait(5)
            return ModelResponse(action=ModelAction.final("unused"))
        provider_session.complete(SimpleNamespace(complete=transport), "planning", request())
        if preparation_raises:
            raise ValueError("synthetic preparation failure")
        return SimpleNamespace(manifest={"status": "BLOCKED"})
    monkeypatch.setattr("linkloom.ui.task_investigation.prepare_task_investigation", prepare)
    workspace, question = app._workspace_id(), "Which work remains?"
    permission = dict(workspace_id=workspace,
        snapshot_hash=source_context(app, workspace)["snapshot_hash"], question_sha256=canonical_hash(question))
    try:
        if preparation_raises:
            with pytest.raises(ValueError, match="preparation failure"):
                service.start(question, workspace, task_investigation=True, task_source_authorization=permission)
        else:
            result = service.start(question, workspace, task_investigation=True, task_source_authorization=permission)
            assert result["kind"] == "error"
        assert len(service._provider_sessions) == 1
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(task.startswith("task-provider-") for task in error.value.task_ids)
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    app.close()


def test_admitted_runtime_can_make_provider_call_while_shutdown_drains(app, tmp_path, monkeypatch):
    entered, release, waiting, called = Event(), Event(), Event(), Event()
    session = TaskProviderSession(tmp_path / "provider", limits(), lambda: app.repository.current_workspace_id())
    def execute(_engine, run_request):
        entered.set()
        assert release.wait(5)
        response = session.complete(SimpleNamespace(complete=lambda _: ModelResponse(action=ModelAction.final("ok"))),
                                    "runtime", request())
        assert response.action.final_answer == "ok"
        called.set()
        return completed(run_request)
    service = service_for(app, monkeypatch, execute)
    service._provider_sessions.append(session)
    snapshot = service.start("Which provider was approved?", app._workspace_id())
    assert entered.wait(5)
    backend = service._jobs[snapshot["run"]["id"]].backend
    original = backend._workers.wait
    def wait(deadline):
        waiting.set()
        original(deadline)
    monkeypatch.setattr(backend._workers, "wait", wait)
    closing = launch(app.close)
    try:
        assert waiting.wait(5)
    finally:
        release.set()
    finish(closing)
    assert called.is_set()


def test_http_review_sidecar_stays_open_until_admitted_handler_returns(app, monkeypatch):
    entered, release = Event(), Event()
    server = create_http_server(SimpleNamespace(), product_app=app, port=0, allow_local_task_review=True)
    def review_get(_workspace, _run_id):
        entered.set()
        assert release.wait(5)
        row = server.task_review._store._connection.execute("SELECT COUNT(*) FROM task_review_statement").fetchone()
        return {"count": row[0]}
    monkeypatch.setattr(server.task_review, "get", review_get)
    serving = launch(server.serve_forever)
    def get():
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/product/task-review?workspace_id=fixture&run_id=fixture",
                     timeout=5) as response:
            return response.status
    getting = launch(get)
    assert entered.wait(5)
    server.shutdown()
    finish(serving)
    server.server_close()
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(task.startswith("http-request-") for task in error.value.task_ids)
        assert server.task_review._store._connection is not None
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    assert finish(getting) == [200]
    app.close()
    assert server.task_review._store._connection is None


def test_review_store_lock_uses_remaining_application_deadline(app):
    entered, release = Event(), Event()
    server = create_http_server(SimpleNamespace(), product_app=app, port=0, allow_local_task_review=True)
    def hold_store():
        with server.task_review._store._lock:
            entered.set()
            assert release.wait(5)
    holding = launch(hold_store)
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout, match="task-review-store-lock"):
            app.close(timeout=0)
        assert app.repository.current_workspace_id()
        assert server.task_review._store._connection is not None
    finally:
        release.set()
        server.server_close()
    finish(holding)
    app.close()


def test_shutdown_rejects_public_api_before_any_database_mutation(app):
    app._api_operations.stop_accepting()
    before = app.repository.list_workspaces()
    calls = [lambda: app.handle_get("/api/status"),
             lambda: app.handle_post("/api/workspaces", {"name": "must not be saved"}),
             lambda: app.handle_post("/api/sources/import", {"name": "must not be saved", "content": "text"}),
             lambda: app.handle_put("/api/sources/unknown", {"name": "must not be saved", "content": "text"}),
             lambda: app.handle_delete("/api/sources/unknown")]
    for call in calls:
        with pytest.raises(ProductApplicationError) as error:
            call()
        assert error.value.status == 503
        assert error.value.code == "APPLICATION_SHUTTING_DOWN"
    assert app.repository.list_workspaces() == before
    app.close()
    for call in calls:
        with pytest.raises(ProductApplicationError) as error:
            call()
        assert error.value.status == 503


def test_admitted_import_can_enqueue_ingestion_before_shutdown_drains(app, monkeypatch):
    entered, release, waiting, ingested = Event(), Event(), Event(), Event()
    original_post, original_wait = app._handle_post, app._api_operations.wait
    def post(path, payload):
        entered.set()
        assert release.wait(5)
        return original_post(path, payload)
    def wait(deadline):
        waiting.set()
        original_wait(deadline)
    monkeypatch.setattr(app, "_handle_post", post)
    monkeypatch.setattr(app._api_operations, "wait", wait)
    monkeypatch.setattr(app, "_ingest", lambda *_: ingested.set())
    importing = launch(lambda: app.handle_post("/api/sources/import", {
        "name": "meeting.txt", "content": "[2026-10-08T10:00:00Z] Speaker: A decision."}))
    assert entered.wait(5)
    closing = launch(app.close)
    try:
        assert waiting.wait(5)
    finally:
        release.set()
    assert finish(importing)[0][1] == 202
    finish(closing)
    assert ingested.is_set()


def test_temporal_start_registers_before_workspace_read_and_keeps_its_admission(app, monkeypatch):
    service = service_for(app, monkeypatch, lambda _engine, request: completed(request))
    workspace = app._workspace_id()
    entered, release = Event(), Event()
    def current_workspace():
        entered.set()
        assert release.wait(5)
        return workspace
    monkeypatch.setattr(app, "_workspace_id", current_workspace)
    monkeypatch.setattr(app, "recover_decision", lambda *_args, **_kwargs: {
        "status": "FOUND", "as_of": "2026-10-08T10:00:00Z"})
    starting = launch(lambda: service.start_temporal("Which provider was approved?", workspace,
        subject="selected decision", relation="approved provider"))
    assert entered.wait(5)
    try:
        with pytest.raises(BackgroundShutdownTimeout) as error:
            app.close(timeout=0)
        assert any(task.startswith("temporal-start-") for task in error.value.task_ids)
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    assert finish(starting)[0]["kind"] == "running"
    app.close()


def test_server_rejected_during_shutdown_releases_unregistered_review_store(app, monkeypatch):
    from linkloom.ui.task_review import TaskReviewService
    entered, release, closed = Event(), Event(), []
    def worker():
        entered.set()
        assert release.wait(5)
    app._ingestion_workers.start("ingestion-during-construction", worker)
    assert entered.wait(5)
    original_close = TaskReviewService.close
    def close(self, *args, **kwargs):
        original_close(self, *args, **kwargs)
        closed.append(self)
    monkeypatch.setattr(TaskReviewService, "close", close)
    try:
        with pytest.raises(BackgroundShutdownTimeout):
            app.close(timeout=0)
        with pytest.raises(BackgroundWorkStopped):
            create_http_server(SimpleNamespace(), product_app=app, port=0, allow_local_task_review=True)
        assert len(closed) == 1
        assert closed[0]._store._connection is None
        assert app.repository.current_workspace_id()
    finally:
        release.set()
    app.close()
