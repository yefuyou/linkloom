"""Synthetic offline integration inputs, no Gold/scorer or live Provider."""
import json

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.models import RunRequest
from linkloom.tools.contracts import ToolCall
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.task_semantic_support import selected_context, response_arguments
from tests.unit.test_task_relation_semantic import FakeModel
from tests.integration.test_task_relation_runtime import engine_for


def runtime_script(workspace_id, *, before_final=None):
    seen = {}
    def inspect(request):
        return ModelAction.tool(ToolCall("inspect", "inspect_task_context", {"workspace_id": workspace_id}))
    def query(request):
        seen["context_call_id"] = request.observation.call_id
        return ModelAction.tool(ToolCall("query", "query_task_relations", {"workspace_id": workspace_id, "include_history": True}))
    def analyze(request):
        seen["relations_call_id"] = request.observation.call_id
        tool = next(t for t in request.available_tools if t.tool_id == "analyze_task_semantics")
        job = tool.input_schema["properties"]["investigation_id"]["enum"][0]
        return ModelAction.tool(ToolCall("analyze", "analyze_task_semantics", {
            "workspace_id": workspace_id, "investigation_id": job}))
    def final(request):
        assert request.observation.tool_id == "analyze_task_semantics" and request.observation.status == "ok"
        if before_final:
            before_final()
        return ModelAction.final(json.dumps({**seen, "semantic_call_id": request.observation.call_id}))
    return FakeModelAdapter([inspect, query, analyze, final])


def setup(root, name="revision", *, snapshot=None, adapter=None, policy=None, before_final=None):
    root.mkdir(parents=True, exist_ok=True)
    original = selected_context(name)
    live = {"snapshot": snapshot or SemanticSourceSnapshot.from_context(original)}
    inner = adapter or FakeModel(response_arguments(original))
    semantic = SemanticAnalysis("investigation", original.workspace_id, lambda: live["snapshot"], inner,
        model_id="gemini-3.8-flash", policy=policy or SemanticCallPolicy("FAKE"))
    raw = TaskContextSnapshot(original.workspace_id, original.observations, original.artifacts, original.source_registry)
    tools = TaskRelationTools(original.workspace_id, lambda: raw, semantic_analysis=semantic)
    outer = runtime_script(original.workspace_id, before_final=before_final)
    engine = engine_for(root, tools, outer)
    return engine, outer, inner, live, original


def execute(engine, root, question, *, steps=4):
    status = engine.start_multi_agent(RunRequest(request_id="m3d", workflow="task_relations", query=question,
        max_steps=steps, max_provider_requests=steps))
    state = engine.checkpointer.get_latest(status.thread_id)
    result = json.loads((root / "checkpoints" / state.result_ref).read_text(encoding="utf8")) if state and state.result_ref else None
    return status, state, result
