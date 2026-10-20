"""Regression checks for the independent read-only review findings."""
from copy import deepcopy
from dataclasses import replace

import pytest

from linkloom.agents.task_relation_result import review_task_relation_result
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.evaluation.task_semantic_replay import load_recorded_semantic_adapter
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot, build_semantic_context
from tests.semantic_integration_support import setup, execute
from tests.task_semantic_support import public_cases, context_for, response_arguments
from tests.unit.test_semantic_invocation import prepared
from tests.unit.test_task_relation_semantic import FakeModel
from tests.integration.test_semantic_recorded_runtime import ARTIFACTS


@pytest.mark.parametrize("kind", ["PARTIAL", "FAILED"])
def test_incomplete_raw_parser_result_is_blocked_before_semantic_adapter(tmp_path, kind):
    case = deepcopy(next(c for c in public_cases() if c["case_id"] == "revision"))
    if kind == "PARTIAL":
        case["sources"][0]["content"] += "\n[2026-12-02Tbad] Missing speaker: unparseable competing claim\n"
    else:
        case["sources"].append(dict(id="broken", artifact_id="broken.md", source_type="timestamped_text",
            content="[2026-12-02Tbad] Missing speaker: hidden source claim\n"))
    context = context_for(case)
    snapshot = SemanticSourceSnapshot.from_context(context)
    # from_context intentionally has no handle for completely unparsed sources;
    # the host catalog must still include them instead of dropping the file.
    snapshot = replace(snapshot, source_handles=tuple((a.artifact_version_id, a.artifact_id) for a in context.artifacts))
    built = build_semantic_context(snapshot, expected_workspace_id=context.workspace_id)
    assert not built.ready and built.errors == ("SOURCE_PARSE_INCOMPLETE",)
    assert any(p["status"] == kind for p in built.manifest()["input_inventory"]["source_parsing"])
    bound, call, _, _ = prepared(tmp_path)
    fake = FakeModel(response_arguments(context))
    bound.owner.snapshot_provider = lambda: snapshot
    bound.owner.adapter = fake
    result = bound.execute(call)
    assert result["analysis_status"] == "BLOCKED" and not fake.requests


@pytest.mark.parametrize("field,value", [("semantic_status", "SEMANTIC_ACCEPTED"), ("publishable", True),
    ("workspace_id", "other"), ("investigation_id", "forged"), ("errors", []),
    ("execution", {"kind": "LIVE", "new_live_provider_requests": 1})])
def test_reviewer_rejects_changed_blocked_projection(tmp_path, field, value):
    bound, call, _, _ = prepared(tmp_path, policy=SemanticCallPolicy("FAKE", max_requests=0))
    result = bound.execute(call)
    assert result["result_artifact"] is None and bound.review(result, call_id=call.call_id)["status"] == "pass"
    result[field] = value
    assert bound.review(result, call_id=call.call_id)["status"] == "fail"


@pytest.mark.parametrize("change", ["call_id", "arguments", "sequence", "other_run", "status", "value", "answer_status", "publishable"])
def test_reviewer_correlates_all_receipts_to_current_durable_ledger(tmp_path, change):
    engine, _, _, _, context = setup(tmp_path)
    status, state, result = execute(engine, tmp_path, context.question)
    assert status.status == "completed"
    payload = deepcopy(result["result"]["task_relations"])
    tools = engine._task_relation_tools.for_run(state.run_id, ModelArtifactStore(tmp_path / "checkpoints/models"), context.question)
    records = list(state.tool_ledger)
    assert review_task_relation_result(payload, tools, records=records, run_id=state.run_id)["status"] == "pass"
    if change == "call_id": payload["receipts"][0]["call_id"] = "invented-call"
    if change == "arguments": payload["receipts"][0]["arguments"]["max_candidates"] = 10
    if change == "sequence": payload["receipts"][0]["sequence"] = 30
    if change == "other_run": records[0] = replace(records[0], run_id="other-run")
    if change == "status": records[0] = replace(records[0], status="failed", result=None, error={"code": "SYNTHETIC_FAILURE"})
    if change == "value": payload["context"]["missing_context"].append("forged")
    if change == "answer_status": payload["answer_status"] = "COMPLETE"
    if change == "publishable": payload["publishable"] = True
    assert review_task_relation_result(payload, tools, records=records, run_id=state.run_id)["status"] == "fail"


def test_limitations_and_original_source_commit_are_preserved(tmp_path):
    adapter = load_recorded_semantic_adapter(ARTIFACTS, "revision")
    assert adapter.provenance["source_commit"] == "04a446d10494a2f6d61ea1f0a745a3ce9f1e8d0c"
    assert adapter.provenance["imported_from_commit"] == "bd5de920e4bcf1a76c12253c7001910552baf287"
    engine, _, _, _, context = setup(tmp_path, adapter=adapter, policy=SemanticCallPolicy("REPLAY"))
    status, _, result = execute(engine, tmp_path, context.question)
    assert status.status == "completed"
    assert {"BOUNDED_TOOL_RECEIPT_PROJECTION", "UNKNOWN_IS_NOT_BUSINESS_REFUTATION", "SEMANTIC_NOT_EVALUATED"} <= set(
        result["result"]["task_relations"]["limitations"])
