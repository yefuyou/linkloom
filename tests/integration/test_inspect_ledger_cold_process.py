"""P2-B cold replay from a fresh Python process.

The child process has no model configuration.  It only opens the saved Product
database/checkpoint and runs the existing read-only ``cold_read`` CLI path.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

from linkloom.runtime.checkpoint import SQLiteCheckpointer
from tests.integration.test_inspect_ledger_dependency import (
    InspectThenRelationPlanner,
    _evidence_root,
)
from tests.m3p_support import http_ask, product_http, revision_product


def _fresh_cold_read(database, run_id, workspace, relation_path):
    """Read one saved run through a separate Python interpreter."""

    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src")
    env["PYTHONUTF8"] = "1"
    for key in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY",
    ):
        env.pop(key, None)
    command = [
        sys.executable,
        "-m",
        "tests.m3p_support",
        str(database),
        run_id,
        workspace,
        str(relation_path),
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    status_code, payload = json.loads(completed.stdout.strip())
    return status_code, payload


def _persist_process_evidence(name, *, status_code, payload):
    if os.environ.get("LINKLOOM_P2B_EVIDENCE_PHASE", "").strip().lower() != "after":
        return
    evidence = {
        "fresh_process": True,
        "python": sys.executable,
        "command_module": "tests.m3p_support",
        "status_code": status_code,
        "payload": payload,
    }
    target = _evidence_root("after") / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")


def test_product_http_cold_read_replays_in_fresh_python_process(tmp_path):
    app, service, _stages, workspace, _identity, _revision, _comparison = revision_product(tmp_path)
    planner = InspectThenRelationPlanner()
    service.task_models = replace(service.task_models, planner=planner)
    relation_path = tmp_path / "relations.sqlite"
    try:
        with product_http(app, service) as base:
            http_result = http_ask(base, workspace)
        run_id = http_result["agent_run_id"]
    finally:
        # The child must reopen the persisted files after the Product process
        # has released its database handles.
        app.close()

    status_code, payload = _fresh_cold_read(
        tmp_path / "product.sqlite", run_id, workspace, relation_path
    )
    assert status_code == 200, payload
    assert payload["result"]["status"] == "PARTIAL", payload
    steps = {
        row["step_id"]: row for row in payload["result"]["task_review"]["steps"]
    }
    assert steps["identity"]["dependency_outcomes"] == {"inspect_owner": "COMPLETED"}
    assert steps["identity"]["status"] == "SUPPORTED"

    _persist_process_evidence("cold-process.json", status_code=status_code, payload=payload)


def test_product_saved_run_rejects_foreign_ledger_task_ids_in_fresh_process(tmp_path):
    """Cold Product validation binds both tool records to model task identity."""

    app, service, _stages, workspace, _identity, _revision, _comparison = revision_product(tmp_path)
    planner = InspectThenRelationPlanner()
    service.task_models = replace(service.task_models, planner=planner)
    relation_path = tmp_path / "relations.sqlite"
    try:
        with product_http(app, service) as base:
            http_result = http_ask(base, workspace)
        run_id = http_result["agent_run_id"]
        pointer_path = tmp_path / "work" / "investigations" / f"{run_id}.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf8"))
        runtime_root = tmp_path / "work" / "investigations" / pointer["root"] / "runtime"
        checkpointer = SQLiteCheckpointer(runtime_root)
        state = checkpointer.get_latest(pointer["thread_id"])
        assert state is not None
        assert len(state.tool_ledger) == 2
        assert state.model_executions
        original_task_ids = [record.task_id for record in state.tool_ledger]
        foreign_ledger = [
            replace(record, task_id="foreign-canonical-task")
            for record in state.tool_ledger
        ]
        tampered = replace(state, tool_ledger=foreign_ledger)
        checkpointer.save(tampered, checkpoint_id="p2b-foreign-ledger-task")
        assert [record.task_id for record in tampered.tool_ledger] == [
            "foreign-canonical-task", "foreign-canonical-task",
        ]
        assert [record.task_id for record in state.tool_ledger] == original_task_ids
        assert [record.task_id for record in tampered.model_executions] == [
            record.task_id for record in state.model_executions
        ]
    finally:
        app.close()

    status_code, payload = _fresh_cold_read(
        tmp_path / "product.sqlite", run_id, workspace, relation_path
    )
    assert status_code == 200, payload
    assert payload["result"]["status"] == "BLOCKED", payload
    assert payload["result"]["error"]["code"] == "SAVED_RESULT_REVALIDATION_FAILED"
    _persist_process_evidence(
        "cold-process-canonical-task.json", status_code=status_code, payload=payload
    )
