"""Restart, cold read and selective resume of a durable parallel run. Mock providers and synthetic data only."""
import hashlib
import json
import os
import shutil
from threading import Event

import pytest

from linkloom.ui.branch_budget import HARD_ABORT, BranchBudgetPool, classify_branch_for_resume, pass_router_reservation
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.product import ProductApplicationError
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession
from tests.integration.test_parallel_investigation import FACTS, QUESTION, MeetingProvider
from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)

LIMITS = TaskProviderLimits(14, 2_000_000, 60_000, 3.0, 120, 30, .01)
OPEN = Event()
OPEN.set()  # the first provider call never waits for the other branch


class Counter:
    """Model factory that records one mock provider per branch question."""

    def __init__(self):
        self.models = {}

    def __call__(self, question):
        model = MeetingProvider(question, OPEN, "complete", OPEN)
        self.models[question] = model
        return model

    @property
    def calls(self):
        return sum(model.calls for model in self.models.values())


def tree(path):
    return {item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(path.rglob("*")) if item.is_file()}


class Env:
    def __init__(self, tmp_path, base, server, app, workspace_id):
        self.tmp_path, self.base, self.server, self.app, self.workspace_id = tmp_path, base, server, app, workspace_id
        self.work = tmp_path / "work" / "investigations"
        self.services = []

    def service(self, factory=None, *, external_provider=False, limits=LIMITS):
        """A new service over the same database: the in-memory state of the previous one is gone."""
        service = ProductInvestigationService(self.app, model_factory=factory or Counter(),
            external_provider=external_provider, parallel_limits=limits)
        self.services.append(service)
        self.server.investigation_service = service
        self.server.unified_ask._runs.clear()
        return service

    def finished_run(self):
        factory = Counter()
        service = self.service(factory)
        status, payload = _client(self.base, "/api/product/ask", method="POST", data=dict(
            query=QUESTION, workspace_id=self.workspace_id, subject="Launch", relation="uses supplier",
            as_of="2025-02-20"))
        assert status == 202, payload
        run_id = payload["result"]["agent_run_id"]
        service.close(timeout=25)
        final = service.inspect(run_id)
        assert final["parallel_investigation"]["status"] == "COMPLETE", final["parallel_investigation"]
        return run_id, self.work / run_id.removeprefix("ui-parallel-")

    def get(self, run_id, workspace_id=None):
        return _client(self.base, f"/api/product/ask/runs/{run_id}?workspace_id={workspace_id or self.workspace_id}")

    def cont(self, run_id, workspace_id=None, **extra):
        return _client(self.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                       data=dict(workspace_id=workspace_id or self.workspace_id, **extra))

    def edit_json(self, path, change):
        data = json.loads(path.read_text(encoding="utf8"))
        change(data)
        path.write_text(json.dumps(data), encoding="utf8")

    def make_pending(self, root):
        """branch-2 looks like a crash while its request was in flight: no result, outcome PENDING."""
        (root / "branches" / "branch-2" / "result.json").unlink()
        self.edit_json(root / "branches" / "branch-2" / "budget.json",
                       lambda data: data["attempts"][-1].update(outcome="PENDING"))
        self.edit_json(root / "pool.json", lambda data: data["branches"]["branch-2"].update(status="RUNNING"))

    def make_never_started(self, root):
        """branch-2 looks like a crash before its first request: a fresh session journal, no result."""
        path = root / "branches" / "branch-2"
        limits = json.loads((path / "budget.json").read_text(encoding="utf8"))["limits"]
        shutil.rmtree(path)
        with pass_router_reservation(None):
            TaskProviderSession(path, TaskProviderLimits(**limits), lambda: None)
        self.edit_json(root / "pool.json", lambda data: data["branches"]["branch-2"].update(status="RUNNING"))
        assert classify_branch_for_resume(path) == "START"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    base, server, worker, app = _start_product_server(tmp_path, [
        _claim("Cobalt B"), _claim("Cobalt B", claim_type="PROPOSAL"),
        _claim("Cobalt B", claim_type="PROPOSAL"), _claim("Cobalt B", claim_type="PROPOSAL"),
    ])
    environment = None
    try:
        _, workspace = _client(base, "/api/workspaces", method="POST", data={"name": "Meetings"})
        workspace_id = workspace["workspace"]["workspace_id"]
        _, scope = _client(base, "/api/sources/import", method="POST", data=dict(
            name="approval.md", content="[2025-02-10T10:00:00Z] Alice: We decided to use Cobalt B as the supplier for Launch."))
        _wait_source(base, scope["source"]["source_id"])
        _, inbox = _client(base, "/api/review/candidates")
        status, approval = _client(base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
            method="POST", data=dict(reviewer_id="local", reason="Synthetic reviewed scope"))
        assert status == 200, approval
        for index, fact in enumerate(FACTS, 1):
            _, imported = _client(base, "/api/sources/import", method="POST", data=dict(
                name=f"meeting-{index}.md", content=f"[2025-02-1{index}T10:00:00Z] Alice: {fact}."))
            _wait_source(base, imported["source"]["source_id"])
        environment = Env(tmp_path, base, server, app, workspace_id)
        yield environment
    finally:
        for service in environment.services if environment else ():
            service.close(timeout=25)
        _stop_product_server(server, worker, app)


def states(result):
    return {row["branch"]: row["state"] for row in result["parallel_investigation"]["branch_states"]}


def test_restart_reads_a_finished_run_without_any_model_call(env):
    run_id, root = env.finished_run()
    live = env.get(run_id)[1]["result"]
    factory = Counter()
    env.service(factory)  # restart: nothing in memory
    before = tree(env.work)
    status, payload = env.get(run_id)
    assert status == 200, payload
    result = payload["result"]
    assert result["status"] == "COMPLETED" and result["parallel_investigation"]["status"] == "COMPLETE"
    assert states(result) == {"branch-1": "REUSED", "branch-2": "REUSED"}
    assert result["parallel_investigation"]["continuable"] == []
    assert len(result["evidence"]) == 3 and result["parallel_investigation"]["answer"]["published"] is True
    assert [row["citation_id"] for row in result["evidence"]] == [row["citation_id"] for row in live["evidence"]]
    assert factory.calls == 0
    assert tree(env.work) == before  # a cold read writes nothing, not even the pointer
    assert "branch_states" not in live["parallel_investigation"]


def test_pending_branch_is_a_gap_and_is_never_resent(env):
    run_id, root = env.finished_run()
    env.make_pending(root)
    factory = Counter()
    env.service(factory)
    before = tree(env.work)
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200 and result["status"] == "PARTIAL"
    assert states(result) == {"branch-1": "REUSED", "branch-2": "UNCERTAIN"}
    assert "may have been billed" in next(row for row in result["parallel_investigation"]["branch_states"]
                                          if row["branch"] == "branch-2")["message"]
    assert result["parallel_investigation"]["continuable"] == []
    assert result["parallel_investigation"]["gaps"] == {"branch-2": "UNCERTAIN"}
    status, refused = env.cont(run_id)
    assert status == 409 and refused["error"]["code"] == "NO_CONTINUABLE_BRANCH", refused
    assert factory.calls == 0 and tree(env.work) == before
    assert not (root / "resumed.json").exists()


def test_never_started_branch_is_continued_once_and_alone(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    idle = Counter()
    env.service(idle)
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200 and result["status"] == "PARTIAL"
    assert states(result) == {"branch-1": "REUSED", "branch-2": "CONTINUABLE"}
    assert result["parallel_investigation"]["continuable"] == ["branch-2"]
    assert idle.calls == 0
    factory = Counter()
    service = env.service(factory)
    status, started = env.cont(run_id)
    assert status == 202, started
    assert started["result"]["parallel_investigation"]["status"] == "RUNNING"
    assert json.loads((root / "resumed.json").read_text(encoding="utf8")) == dict(branches=["branch-2"])
    service.close(timeout=25)
    assert [model.chain for model in factory.models.values()] == [False]  # only branch-2's provider was asked
    assert factory.calls == 3
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200 and result["parallel_investigation"]["status"] == "COMPLETE", result["parallel_investigation"]
    assert states(result) == {"branch-1": "REUSED", "branch-2": "REUSED"} and len(result["evidence"]) == 3
    assert (root / "resume" / "branches" / "branch-2" / "result.json").is_file()
    again = Counter()
    env.service(again)
    status, refused = env.cont(run_id)
    assert status == 409 and refused["error"]["code"] == "PARALLEL_ALREADY_RESUMED", refused
    cold = env.get(run_id)[1]["result"]
    assert cold["parallel_investigation"]["status"] == "COMPLETE" and again.calls == 0


def test_second_continue_in_the_same_process_is_refused(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    service = env.service(Counter())
    assert env.cont(run_id)[0] == 202
    status, refused = env.cont(run_id)
    assert status == 409 and refused["error"]["code"] in ("PARALLEL_RUN_ACTIVE", "PARALLEL_ALREADY_RESUMED")
    service.close(timeout=25)


def test_resume_pool_branch_without_an_outcome_is_uncertain(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    (root / "resumed.json").write_text(json.dumps(dict(branches=["branch-2"])), encoding="utf8")
    factory = Counter()
    env.service(factory)
    result = env.get(run_id)[1]["result"]
    assert states(result) == {"branch-1": "REUSED", "branch-2": "UNCERTAIN"}
    assert env.cont(run_id)[0] == 409 and factory.calls == 0


def test_completed_checkpoint_is_rebuilt_read_only_without_a_request(env):
    run_id, root = env.finished_run()
    live = env.get(run_id)[1]["result"]
    (root / "branches" / "branch-2" / "result.json").unlink()
    env.edit_json(root / "pool.json", lambda data: data["branches"]["branch-2"].update(status="RUNNING"))
    assert classify_branch_for_resume(root / "branches" / "branch-2") == "RESUME"
    factory = Counter()
    env.service(factory)
    before = tree(env.work)
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200 and result["parallel_investigation"]["status"] == "COMPLETE"
    assert next(row for row in result["parallel_investigation"]["branch_states"]
                if row["branch"] == "branch-2")["reason"] == "REBUILT_FROM_CHECKPOINT"
    assert [row["citation_id"] for row in result["evidence"]] == [row["citation_id"] for row in live["evidence"]]
    assert factory.calls == 0 and tree(env.work) == before
    assert not (root / "branches" / "branch-2" / "result.json").exists()


def test_unusable_checkpoint_is_not_safe_and_not_continuable(env):
    run_id, root = env.finished_run()
    (root / "branches" / "branch-2" / "result.json").unlink()
    (root / "branches" / "branch-2" / "runtime" / "checkpoints.sqlite").unlink()
    env.edit_json(root / "pool.json", lambda data: data["branches"]["branch-2"].update(status="RUNNING"))
    factory = Counter()
    env.service(factory)
    result = env.get(run_id)[1]["result"]
    assert states(result) == {"branch-1": "REUSED", "branch-2": "RESUME_NOT_SAFE"}
    assert result["parallel_investigation"]["continuable"] == [] and result["status"] == "PARTIAL"
    assert env.cont(run_id)[0] == 409 and factory.calls == 0


def test_changed_sources_publish_nothing_and_block_continue(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    _, imported = _client(env.base, "/api/sources/import", method="POST", data=dict(
        name="late.md", content="[2025-02-15T10:00:00Z] Alice: A later note changes the authorized sources."))
    _wait_source(env.base, imported["source"]["source_id"])
    factory = Counter()
    env.service(factory)
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200 and result["parallel_investigation"]["status"] == "ABORTED"
    assert result["parallel_investigation"]["reason"] == "TASK_SOURCE_AUTHORIZATION_CHANGED"
    assert result["evidence"] == [] and "answer" not in result["parallel_investigation"]
    assert set(states(result).values()) == {"WITHHELD"} and result["parallel_investigation"]["continuable"] == []
    status, refused = env.cont(run_id)
    assert status == 409 and refused["error"]["code"] == "TASK_SOURCE_AUTHORIZATION_CHANGED"
    assert factory.calls == 0 and not (root / "resumed.json").exists()


def test_identity_mismatch_in_the_record_publishes_nothing(env):
    run_id, root = env.finished_run()
    env.edit_json(root / "parallel.json", lambda data: data.update(identity="0" * 64))
    env.service(Counter())
    result = env.get(run_id)[1]["result"]
    assert result["parallel_investigation"]["status"] == "ABORTED" and result["evidence"] == []


@pytest.mark.parametrize("kind", ["HARD_ABORT", "CANCELLED", "resume_pool"])
def test_stopped_pools_publish_nothing(env, kind):
    run_id, root = env.finished_run()
    if kind == "resume_pool":
        env.make_never_started(root)
        pool = BranchBudgetPool(root / "resume", LIMITS, lambda: None, run_id, 1)
        pool.stop(HARD_ABORT, "MODEL_AUTH_REQUIRED")
        (root / "resumed.json").write_text(json.dumps(dict(branches=["branch-2"])), encoding="utf8")
    else:
        env.edit_json(root / "pool.json", lambda data: data.update(stop=[kind, "STOPPED_FOR_TEST"]))
    factory = Counter()
    env.service(factory)
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200
    assert result["parallel_investigation"]["status"] == ("CANCELLED" if kind == "CANCELLED" else "ABORTED")
    assert result["evidence"] == [] and "answer" not in result["parallel_investigation"]
    assert set(states(result).values()) == {"WITHHELD"}
    assert env.cont(run_id)[0] == 409 and factory.calls == 0


def test_corrupted_budget_journal_is_uncertain(env):
    run_id, root = env.finished_run()
    (root / "branches" / "branch-2" / "budget.json").write_text("{not json", encoding="utf8")
    factory = Counter()
    env.service(factory)
    result = env.get(run_id)[1]["result"]
    assert states(result) == {"branch-1": "REUSED", "branch-2": "UNCERTAIN"}
    assert result["status"] == "PARTIAL" and factory.calls == 0


def test_unreadable_record_is_a_failure_and_not_a_result(env):
    run_id, root = env.finished_run()
    (root / "parallel.json").write_text("{not json", encoding="utf8")
    env.service(Counter())
    status, payload = env.get(run_id)
    result = payload["result"]
    assert status == 200 and result["parallel_investigation"]["status"] == "FAILED"
    assert result["parallel_investigation"]["reason"] == "PARALLEL_STATE_UNREADABLE" and result["evidence"] == []
    assert env.cont(run_id)[0] == 409


@pytest.mark.parametrize("tamper", ["traversal", "other_root", "run_id", "workspace_id", "kind"])
def test_tampered_pointer_is_rejected(env, tamper):
    run_id, root = env.finished_run()
    other = env.work / "aaaaaaaaaaaa"
    shutil.copytree(root, other)
    pointer = env.work / (run_id + ".json")

    def change(data):
        if tamper == "traversal":
            data["root"] = "../" + root.name
        elif tamper == "other_root":
            data["root"] = other.name
        elif tamper == "run_id":
            data["run_id"] = "ui-parallel-bbbbbbbbbbbb"
        elif tamper == "workspace_id":
            data["workspace_id"] = "ws_other"
        else:
            data["kind"] = "task"
    env.edit_json(pointer, change)
    factory = Counter()
    env.service(factory)
    status, payload = env.get(run_id)
    assert status == 404 and payload["error"]["code"] == "RUN_NOT_FOUND", payload
    status, payload = env.cont(run_id)
    assert status == 404 and factory.calls == 0
    assert not (root / "resumed.json").exists() and not (other / "resumed.json").exists()


def test_continue_needs_external_provider_permission(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    factory = Counter()
    service = env.service(factory, external_provider=True)
    status, refused = env.cont(run_id)
    assert status == 403 and refused["error"]["code"] == "EXTERNAL_PROVIDER_PERMISSION_REQUIRED", refused
    status, refused = env.cont(run_id, allow_external_provider="yes")
    assert status == 403
    assert factory.calls == 0 and not (root / "resumed.json").exists()
    status, started = env.cont(run_id, allow_external_provider=True)
    assert status == 202, started
    service.close(timeout=25)
    assert factory.calls == 3
    assert env.get(run_id)[1]["result"]["parallel_investigation"]["status"] == "COMPLETE"


def test_http_routes_reject_another_workspace(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    factory = Counter()
    env.service(factory)
    other = "ws_other"
    status, payload = env.get(run_id, other)
    assert status == 409 and payload["error"]["code"] == "WORKSPACE_MISMATCH"
    status, payload = env.cont(run_id, other)
    assert status == 409 and payload["error"]["code"] == "WORKSPACE_MISMATCH"
    status, payload = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST", data={})
    assert status == 409 and payload["error"]["code"] == "WORKSPACE_REQUIRED"
    assert factory.calls == 0 and not (root / "resumed.json").exists()


def test_every_layer_checks_the_workspace_on_its_own(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    factory = Counter()
    service = env.service(factory, external_provider=True)
    # The workspace check in front of the service answers before the provider permission is looked at.
    status, payload = env.cont(run_id, "ws_other")
    assert status == 409 and payload["error"]["code"] == "WORKSPACE_MISMATCH", payload
    # The pointer is retargeted so its own workspace field cannot reject the request; only the
    # comparison with the selected workspace is left inside the service.
    env.edit_json(env.work / (run_id + ".json"), lambda data: data.update(workspace_id="ws_other"))
    assert service.read_saved_parallel(run_id, "ws_other") is None
    with pytest.raises(ProductApplicationError) as error:
        service.continue_parallel(run_id, "ws_other", allow_external_provider=True)
    assert error.value.code == "WORKSPACE_MISMATCH"
    assert factory.calls == 0 and not (root / "resumed.json").exists()


def test_pointer_root_that_resolves_outside_the_work_directory_is_rejected(env):
    run_id, root = env.finished_run()
    env.make_never_started(root)
    outside = env.tmp_path / "outside"
    shutil.copytree(root, outside)
    shutil.rmtree(root)
    try:
        os.symlink(outside, root, target_is_directory=True)
    except OSError:
        pytest.skip("this platform does not allow creating a symlink here")
    factory = Counter()
    env.service(factory)
    status, payload = env.get(run_id)
    assert status == 404 and payload["error"]["code"] == "RUN_NOT_FOUND", payload
    status, payload = env.cont(run_id)
    assert status == 404 and factory.calls == 0
    assert not (outside / "resumed.json").exists()


def test_flag_off_writes_no_parallel_files_and_cannot_continue(env):
    run_id, root = env.finished_run()
    factory = Counter()
    service = env.service(factory, limits=None)
    assert env.cont(run_id)[1]["error"]["code"] == "PARALLEL_CONTINUE_UNAVAILABLE"
    before = set(env.work.iterdir())
    status, payload = _client(env.base, "/api/product/ask", method="POST", data=dict(
        query=QUESTION, workspace_id=env.workspace_id, subject="Launch", relation="uses supplier",
        as_of="2025-02-20"))
    assert "parallel_investigation" not in payload["result"]
    service.close(timeout=25)
    created = set(env.work.iterdir()) - before
    assert not [path for path in created if path.name.startswith("ui-parallel-")]
    assert not [path for path in created if (path / "parallel.json").exists()]
