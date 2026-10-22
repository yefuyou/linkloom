from dataclasses import replace

from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.tools.contracts import ToolCall
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from tests.task_semantic_support import selected_context, response_arguments
from tests.unit.test_task_relation_semantic import FakeModel


def prepared(tmp_path, *, name="revision", policy=None, adapter=None):
    context = selected_context(name)
    snapshot = SemanticSourceSnapshot.from_context(context)
    model = adapter or FakeModel(response_arguments(context))
    service = SemanticAnalysis("investigate", context.workspace_id, lambda: snapshot, model,
        model_id="gemini-3.8-flash", policy=policy or SemanticCallPolicy("FAKE"))
    bound = service.bind("outer-run", ModelArtifactStore(tmp_path), context.question)
    call = ToolCall("semantic-call", "analyze_task_semantics", {"workspace_id": context.workspace_id,
        "investigation_id": "investigate"}, run_id="outer-run", task_id="retrieval-task", agent_id="retrieval_agent", sequence=3)
    return bound, call, model, snapshot


def test_one_inner_request_per_run_and_cached_receipt_survives_new_service(tmp_path):
    bound, call, model, snapshot = prepared(tmp_path)
    first = bound.execute(call)
    assert first["analysis_status"] == "EXECUTED", first
    assert len(model.requests) == 1
    second = bound.execute(replace(call, call_id="repeat-call", sequence=4))
    assert second["invocation_id"] == first["invocation_id"] and second["cache_hit"]
    assert len(model.requests) == 1
    restored = SemanticAnalysis("investigate", snapshot.workspace_id, lambda: snapshot, None,
        model_id="gemini-3.8-flash", policy=SemanticCallPolicy("FAKE")).bind(
            "outer-run", ModelArtifactStore(tmp_path), snapshot.question)
    cached = restored.execute(call)
    assert cached["cache_hit"] and cached["execution"]["reserved_requests"] == 1
    assert restored.review(cached, call_id=call.call_id)["status"] == "pass"
    assert cached["proposals"][1]["comparison"]["delta_calendar_days"] == 4
    assert all(p["semantic_status"] == "SEMANTIC_NOT_EVALUATED" and not p["publishable"] for p in cached["proposals"])


def test_ambiguous_reserved_attempt_cannot_rebill_after_crash(tmp_path):
    class Crash(FakeModel):
        def complete(self, request):
            self.requests.append(request)
            raise KeyboardInterrupt("synthetic process crash")
    import pytest
    bound, call, model, _ = prepared(tmp_path, adapter=Crash({}))
    with pytest.raises(KeyboardInterrupt):
        bound.execute(call)
    result = bound.execute(call)
    assert result["analysis_status"] == "BLOCKED"
    assert "SEMANTIC_OUTCOME_UNKNOWN" in result["errors"] and len(model.requests) == 1


def test_inner_budget_and_provider_failure_are_separate(tmp_path):
    bound, call, model, _ = prepared(tmp_path / "budget", policy=SemanticCallPolicy("FAKE", max_requests=0))
    assert "SEMANTIC_REQUEST_BUDGET_EXHAUSTED" in bound.execute(call)["errors"]
    assert not model.requests
    class Failed(FakeModel):
        def complete(self, request):
            self.requests.append(request)
            raise RuntimeError("synthetic provider failure")
    bound, call, model, _ = prepared(tmp_path / "failure", adapter=Failed({}))
    result = bound.execute(call)
    assert result["analysis_status"] == "PROVIDER_FAILED"
    assert result["execution"]["reserved_requests"] == 1
    assert result["execution"]["new_live_provider_requests"] == 0
    assert result["execution"]["cost_usd"] is None
    bound.execute(call)
    assert len(model.requests) == 1


def test_new_live_semantic_policy_is_not_enabled():
    import pytest
    with pytest.raises(ValueError, match='not enabled'):
        SemanticCallPolicy('LIVE')
