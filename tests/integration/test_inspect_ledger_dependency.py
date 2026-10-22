"""P2-B: a completed Runtime inspection must unlock its dependent relation.

The fixture uses Product HTTP and the existing synthetic P4 sidecar. Discovery
and planning are scripted offline stages; Runtime, the task relation query,
Reviewer, and cold read are the normal product paths. The first test is a
deliberately red regression test on the pre-fix baseline: the inspection is
durably completed, but the dependent relation still reports a blocked
dependency because the query does not consume the inspection ledger receipt.

Set ``LINKLOOM_P2B_EVIDENCE_PHASE=before`` or ``after`` while running the
focused test to persist the four JSON evidence views under the corresponding
requirements evidence directory. The default test run is side-effect free.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tools.contracts import ToolCall
from tests.integration.test_product_application_flow import _client
from tests.m3p_support import cold_read, http_ask, product_http, revision_product


class InspectThenRelationPlanner:
    """Offline G planner that makes the P2-B cross-chain dependency explicit."""

    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request.to_dict())
        payload = json.loads(request.user_input.split("INPUT_JSON\n", 1)[1])
        endpoints = payload["endpoints"]
        handles = [endpoint["handle"] for endpoint in endpoints]
        if len(handles) < 2:
            raise AssertionError("fixture requires two admitted task observations")
        steps = [
            dict(
                id="inspect_owner",
                kind="inspect",
                question="读取第一个任务的当前 owner 字段。",
                handles=[handles[0]],
                source_handles=[],
                depends_on=[],
                predicate="",
                field="owner",
                target_kind="",
                missing_entities=[],
            ),
            dict(
                id="identity",
                kind="relation",
                question="两条记录是否属于同一业务任务？",
                handles=handles[:2],
                source_handles=[],
                depends_on=["inspect_owner"],
                predicate="SAME_TASK",
                field="",
                target_kind="",
                missing_entities=[],
            ),
        ]
        arguments = dict(
            steps=steps,
            uncertainties=["需要将 Runtime inspection receipt 绑定到关系查询。"],
            refusal_reasons=[],
        )
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall("p2b-planner", request.available_tools[0].tool_id, arguments)
            ),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )


def _evidence_root(phase: str) -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "requirements"
        / "p2b-inspect-ledger-dependency"
        / "evidence"
        / phase
    )


def _persist_evidence(phase: str, *, http, ledger, reviewer, cold):
    root = _evidence_root(phase)
    root.mkdir(parents=True, exist_ok=True)
    for name, value in {
        "http.json": http,
        "ledger.json": ledger,
        "reviewer.json": reviewer,
        "cold-read.json": cold,
    }.items():
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def _run_http_case(tmp_path, *, records="both", model_factory=None, model_builder=None):
    app, service, _stages, workspace, _identity, _revision, _comparison = revision_product(
        tmp_path, records=records
    )
    planner = InspectThenRelationPlanner()
    service.task_models = replace(service.task_models, planner=planner)
    if model_builder is not None:
        service.model_factory = lambda _query: model_builder(workspace)
    elif model_factory is not None:
        service.model_factory = model_factory
    relation_path = tmp_path / "relations.sqlite"
    try:
        with product_http(app, service) as base:
            http_result = http_ask(base, workspace)
        ui_run_id = http_result["agent_run_id"]
        pointer_path = tmp_path / "work" / "investigations" / f"{ui_run_id}.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf8"))
        runtime_root = tmp_path / "work" / "investigations" / pointer["root"] / "runtime"
        state = SQLiteCheckpointer(runtime_root).get_latest(pointer["thread_id"])
        assert state is not None
        ledger = [record.to_dict() for record in state.tool_ledger]
        reviewer = http_result.get("reviewer", {})
        cold_status, cold_payload = cold_read(
            tmp_path / "product.sqlite", ui_run_id, workspace, relation_path
        )
        cold = {"status_code": cold_status, "payload": cold_payload}
        phase = os.environ.get("LINKLOOM_P2B_EVIDENCE_PHASE", "").strip().lower()
        if phase in {"before", "after"}:
            _persist_evidence(
                phase,
                http=http_result,
                ledger=ledger,
                reviewer=reviewer,
                cold=cold,
            )
        return http_result, ledger, reviewer, cold, planner.requests
    finally:
        app.close()


def _run_http_case_then_mutate(tmp_path, mutate):
    """Run the normal path, mutate synthetic source authority, then hot/cold read."""

    app, service, _stages, workspace, _identity, _revision, _comparison = revision_product(tmp_path)
    planner = InspectThenRelationPlanner()
    service.task_models = replace(service.task_models, planner=planner)
    relation_path = tmp_path / "relations.sqlite"
    try:
        with product_http(app, service) as base:
            http_result = http_ask(base, workspace)
            mutate(app, workspace, http_result)
            hot_status, hot_payload = _client(
                base,
                f"/api/product/ask/runs/{http_result['agent_run_id']}?workspace_id={workspace}",
            )
        cold_status, cold_payload = cold_read(
            tmp_path / "product.sqlite", http_result["agent_run_id"], workspace, relation_path
        )
        return http_result, {
            "hot": {"status_code": hot_status, "payload": hot_payload},
            "cold": {"status_code": cold_status, "payload": cold_payload},
        }
    finally:
        app.close()


def _update_first_source(app, workspace, _result):
    source = app.snapshot_investigation_sources(workspace)[1][0]
    artifact = app._artifact_from_version(
        dict(source, content=source["content"] + "\nSynthetic source version replacement.")
    )
    app.repository.update_source(
        workspace,
        source["source_id"],
        source["name"],
        artifact,
        tuple(segment.evidence_span for segment in parse_source_text(artifact).segments),
    )


def _revoke_first_evidence(app, workspace, result):
    evidence_ref = result["evidence"][0]["evidence_ref"]
    with app.repository._lock, app.repository._connection:
        app.repository._connection.execute(
            "DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?",
            (workspace, evidence_ref),
        )


def test_product_http_inspection_receipt_unblocks_dependent_relation(tmp_path):
    """A real completed inspect record is the premise for SAME_TASK."""

    http_result, ledger, reviewer, cold, _planning_requests = _run_http_case(tmp_path)

    assert http_result["status"] == "PARTIAL", http_result
    assert [row["tool"] for row in http_result["trace"]] == [
        "inspect_task_context",
        "query_task_relations",
    ]
    assert [row["tool_id"] for row in ledger] == [
        "inspect_task_context",
        "query_task_relations",
    ]
    assert [row["status"] for row in ledger] == ["completed", "completed"]
    inspect_record = ledger[0]
    assert inspect_record["result"]["status"] == "ok"
    assert inspect_record["result"]["value"]["investigation_receipts"]

    steps = {row["step_id"]: row for row in http_result["task_review"]["steps"]}
    identity = steps["identity"]
    assert identity["dependency_outcomes"] == {"inspect_owner": "COMPLETED"}
    assert identity["status"] == "SUPPORTED"
    assert identity["current_supported"] is True
    assert reviewer["accepted"] is True
    assert cold["status_code"] == 200, cold
    assert cold["payload"]["result"]["status"] == "PARTIAL", cold
    cold_steps = {
        row["step_id"]: row for row in cold["payload"]["result"]["task_review"]["steps"]
    }
    assert cold_steps["identity"]["dependency_outcomes"] == {"inspect_owner": "COMPLETED"}
    assert cold_steps["identity"]["status"] == "SUPPORTED"


def test_product_http_cold_replay_fails_closed_after_source_version_update(tmp_path):
    _, reads = _run_http_case_then_mutate(tmp_path, _update_first_source)

    for read in reads.values():
        assert read["status_code"] == 200, read
        result = read["payload"]["result"]
        assert result["status"] == "BLOCKED", read
        assert not result["evidence"]
        assert not result.get("trace")
        assert not result.get("task_review")


def test_product_http_cold_replay_fails_closed_after_source_evidence_revoke(tmp_path):
    _, reads = _run_http_case_then_mutate(tmp_path, _revoke_first_evidence)

    for read in reads.values():
        assert read["status_code"] == 200, read
        result = read["payload"]["result"]
        assert result["status"] == "BLOCKED", read
        assert not result["evidence"]
        assert not result.get("trace")
        assert not result.get("task_review")


def test_product_http_nonpositive_p4_relation_keeps_inspection_receipt_but_blocks_relation(tmp_path):
    result, ledger, reviewer, cold, _ = _run_http_case(tmp_path, records="none")

    assert result["status"] == "PARTIAL", result
    assert [row["status"] for row in ledger] == ["completed", "completed"]
    steps = {row["step_id"]: row for row in result["task_review"]["steps"]}
    identity = steps["identity"]
    assert identity["dependency_outcomes"] == {"inspect_owner": "COMPLETED"}
    assert identity["status"] == "BLOCKED"
    assert identity["current_supported"] is False
    assert "NO_RELATION_RECORDS" in identity["blocking_reasons"]
    assert reviewer["accepted"] is True
    assert cold["status_code"] == 200, cold
    cold_result = cold["payload"]["result"]
    assert cold_result["status"] == "PARTIAL", cold
    cold_identity = next(
        row for row in cold_result["task_review"]["steps"] if row["step_id"] == "identity"
    )
    assert cold_identity["dependency_outcomes"] == {"inspect_owner": "COMPLETED"}
    assert cold_identity["status"] == "BLOCKED"


def _query_before_inspection_model(workspace):
    """Query first, then inspect: the query must use its earlier Ledger prefix."""

    from linkloom.agents.model_adapter import FakeModelAdapter

    def call(request, tool, *, anchor_ids=None):
        args = {"workspace_id": workspace}
        if anchor_ids is not None:
            args["anchor_ids"] = anchor_ids
        return ModelAction.tool(ToolCall(tool, tool, args))

    def final(request):
        results = [history.tool_result for history in request.tool_history if history.tool_result]
        results.append(request.observation)
        by_tool = {item.tool_id: item.call_id for item in results}
        return ModelAction.final(
            json.dumps(
                {
                    "context_call_id": by_tool["inspect_task_context"],
                    "relations_call_id": by_tool["query_task_relations"],
                }
            )
        )

    return FakeModelAdapter(
        [
            lambda request: call(request, "query_task_relations"),
            lambda request: call(request, "inspect_task_context"),
            final,
        ]
    )


def _failed_inspection_model(workspace):
    """A failed inspection must not become a relation dependency receipt."""

    from linkloom.agents.model_adapter import FakeModelAdapter

    def call(request, tool, *, anchor_ids=None):
        args = {"workspace_id": workspace}
        if anchor_ids is not None:
            args["anchor_ids"] = anchor_ids
        return ModelAction.tool(ToolCall(tool, tool, args))

    def final(request):
        results = [history.tool_result for history in request.tool_history if history.tool_result]
        results.append(request.observation)
        by_tool = {item.tool_id: item.call_id for item in results}
        return ModelAction.final(
            json.dumps(
                {
                    "context_call_id": by_tool["inspect_task_context"],
                    "relations_call_id": by_tool["query_task_relations"],
                }
            )
        )

    return FakeModelAdapter(
        [
            lambda request: call(
                request, "inspect_task_context", anchor_ids=["unplanned-observation"]
            ),
            lambda request: call(request, "query_task_relations"),
            final,
        ]
    )


def test_product_http_query_before_inspection_does_not_retroactively_unlock_relation(tmp_path):
    result, ledger, _reviewer, cold, _ = _run_http_case(
        tmp_path,
        model_builder=_query_before_inspection_model,
    )

    assert result["status"] == "BLOCKED", result
    assert not result.get("task_review")
    assert cold["status_code"] == 200
    assert cold["payload"]["result"]["status"] == "BLOCKED"
    assert [record["tool_id"] for record in ledger] == [
        "query_task_relations",
        "inspect_task_context",
    ]


def test_product_http_failed_inspection_does_not_unlock_relation(tmp_path):
    result, ledger, _reviewer, cold, _ = _run_http_case(
        tmp_path,
        model_builder=_failed_inspection_model,
    )

    assert result["status"] == "BLOCKED", result
    assert not result.get("task_review")
    assert cold["status_code"] == 200
    assert cold["payload"]["result"]["status"] == "BLOCKED"
    assert [record["tool_id"] for record in ledger] == [
        "inspect_task_context",
        "query_task_relations",
    ]
    assert ledger[0]["status"] == "failed"
    assert ledger[1]["status"] == "completed"
