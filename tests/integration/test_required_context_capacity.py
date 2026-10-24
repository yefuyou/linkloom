"""Default count expansion preserves required sets, hard budgets and no-send gates."""
from dataclasses import replace
import json

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.context.assembler import ContextAssembler, ContextBudget
from linkloom.context.policy import EvidenceGroup
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.runtime.context_engineering import ContextRuntimeConfig, RuntimeWorkingContext, encoded
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RunRequest
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tools.contracts import ToolCall
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.integration.test_runtime_v2_multi_action_resume import _state
from tests.integration.test_task_relation_runtime import engine_for
from tests.unit.test_context_assembler import _evidence


def tools_for(count):
    # Distinct tasks, exact source text and evidence identities, not repeated quotes.
    artifact = RawArtifact("capacity", "meeting", "timestamped_text", "\n".join(
        f"[2026-12-01T09:{i:02d}:00Z] Ada: I will finish task T-{i} for component {i}." for i in range(count)))
    registry = SourceReferenceRegistry()
    for segment in parse_source_text(artifact).segments:
        span = segment.evidence_span
        registry.register_evidence("capacity", span.evidence_ref, content_hash=span.content_hash,
            source_version=span.artifact_version_id)
    raw = TaskContextSnapshot("capacity", (), (artifact,), registry)
    return TaskRelationTools("capacity", lambda: raw)


def service_for(tmp_path, count, config=None):
    config = config or ContextRuntimeConfig()
    tools = tools_for(count)
    assembler = ContextAssembler(budget=config.local_budget, policy=config.policy)
    service = RuntimeWorkingContext(config, tools, ModelArtifactStore(tmp_path), "Inspect all declared evidence.",
        lambda **kw: assembler.assemble(workspace_id="capacity", **kw))
    state = replace(_state(), workflow="task_relations")
    request = ModelTurnRequest(state.run_id, "turn1", "task", "retrieval_agent", 1, service.question, None, [])
    return service, state, request


@pytest.mark.parametrize("count", [6, 7, 8])
def test_required_groups_expand_only_to_needed_capacity(tmp_path, count):
    service, state, request = service_for(tmp_path, count)
    prepared, audit = service.prepare(request, state)
    assert ContextBudget().max_evidence == service.config.local_budget.max_evidence == 5
    required = {i for g in audit["groups"] for i in g["evidence_ids"]}
    assert len(required) == count
    assert all(c["complete"] and not c["missing_evidence_ids"] for c in audit["coverage"])
    assert len(audit["recovery"]["evidence"]) == count
    body = encoded(prepared.to_dict())
    assert audit["budget"]["input_tokens"] == service.config.policy.count(body) + service.config.envelope.tokens
    assert audit["budget"]["input_bytes"] == len(body.encode()) + service.config.envelope.bytes
    assert audit["budget"]["input_bytes"] <= 96 * 1024
    assert audit["budget"]["input_tokens"] + audit["budget"]["output_reserve_tokens"] <= service.config.policy.context_window_tokens
    assert len(prepared.user_input.encode()) <= 32 * 1024


def test_same_six_different_required_items_fail_fixed_five_and_pass_dynamic():
    items = tuple(_evidence(str(i), i + 1) for i in range(6))
    groups = (EvidenceGroup("support", ("0", "1", "2"), ("investigate",)),
              EvidenceGroup("counter", ("3", "4", "5"), ("investigate",), True))
    assembler = ContextAssembler()
    kwargs = dict(workspace_id="borealis", query="Q", retrieved_evidence=items,
        evidence_groups=groups, task_subgoals=("investigate",))
    fixed = assembler.assemble(**kwargs)
    dynamic = assembler.assemble(**kwargs, required_evidence_limit=8)
    assert not fixed.selection.coverage[0].complete
    assert set(dynamic.selection.selected_ids) == set("012345")
    assert dynamic.selection.coverage[0].complete
    assert assembler.budget.max_evidence == 5


def test_optional_items_do_not_expand_or_displace_required_groups():
    items = tuple(_evidence(str(i), i + 1) for i in range(8))
    assembler = ContextAssembler()
    assert len(assembler.assemble(workspace_id="borealis", query="Q", retrieved_evidence=items,
        required_evidence_limit=8).selection.selected_ids) == 5
    groups = (EvidenceGroup("required", tuple(str(i) for i in range(6)), ("g",)),)
    bundle = assembler.assemble(workspace_id="borealis", query="Q", retrieved_evidence=items,
        evidence_groups=groups, required_evidence_limit=8)
    assert bundle.selection.selected_ids == tuple(str(i) for i in range(6))


@pytest.mark.parametrize("limit", [1, 4, 6])
def test_injected_assembler_keeps_its_explicit_cap(tmp_path, limit):
    from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
    tools = tools_for(7)
    config = ContextRuntimeConfig()
    (tmp_path / "engine").mkdir()
    engine = engine_for(tmp_path / "engine", tools, FakeModelAdapter([ModelAction.final("unused")]))
    assembler = ContextAssembler(budget=ContextBudget(max_evidence=limit), policy=config.policy)
    adapter = RuntimeAgentAdapter(engine.vault_root, engine.index_path, workspace_id="capacity",
        retrieval_mode="bm25", context_assembler=assembler, context_runtime_config=config,
        decision_memory_enabled=False)
    service, state, request = service_for(tmp_path / "service", 7, config)
    service.assemble = adapter._assemble_context
    with pytest.raises(ValidationError):
        service.prepare(request, state)
    assert service.latest["status"] == "BLOCKED"
    assert len(service.latest["coverage"][0]["missing_evidence_ids"]) == 7


@pytest.mark.parametrize("kind", ["tokens", "chars", "bytes", "output_reserve", "output_request", "explicit_count", "hard_count"])
def test_dynamic_capacity_still_blocks_and_reports_missing_evidence(tmp_path, kind):
    config = ContextRuntimeConfig()
    if kind == "tokens": config = replace(config, local_budget=replace(config.local_budget, max_tokens=20))
    if kind == "chars": config = replace(config, local_budget=replace(config.local_budget, max_chars=100))
    if kind == "bytes": config = replace(config, policy=replace(config.policy, max_request_bytes=1024))
    if kind == "output_reserve": config = replace(config, policy=replace(config.policy, context_window_tokens=8000, output_reserve_tokens=7500))
    if kind == "explicit_count": config = replace(config, local_budget=replace(config.local_budget, max_evidence=4))
    service, state, request = service_for(tmp_path, 9 if kind == "hard_count" else 6, config)
    if kind == "output_request":
        request = replace(request, generation_options=replace(request.generation_options, max_output_tokens=4096))
    with pytest.raises(ValidationError) as error:
        service.prepare(request, state)
    assert error.value.details["code"] == "CONTEXT_INCOMPLETE"
    assert service.latest["status"] == "BLOCKED"
    if kind != "output_request":
        missing = {i for c in service.latest["coverage"] for i in c["missing_evidence_ids"]}
        assert len(missing) == (9 if kind == "hard_count" else 6)


@pytest.mark.parametrize("count", [6, 9])
def test_real_runtime_enforces_required_capacity_before_model_requests(tmp_path, count):
    workspace = "capacity"
    receipts = {}
    def inspect(r):
        return ModelAction.tool(ToolCall("inspect", "inspect_task_context", {"workspace_id": workspace}))
    def query(r):
        receipts["context_call_id"] = r.observation.call_id
        return ModelAction.tool(ToolCall("query", "query_task_relations", {"workspace_id": workspace}))
    def final(r):
        return ModelAction.final(json.dumps({**receipts, "relations_call_id": r.observation.call_id}))
    model = FakeModelAdapter([inspect, query, final])
    engine = engine_for(tmp_path, tools_for(count), model)
    engine._context_runtime_config = ContextRuntimeConfig()
    status = engine.start_multi_agent(RunRequest(request_id="capacity", workflow="task_relations",
        query="Inspect all declared evidence.", max_steps=3, max_provider_requests=3))
    assert status.status == ("completed" if count == 6 else "failed")
    assert model.call_count == (3 if count == 6 else 0)


def test_dynamic_capacity_also_respects_a_lower_candidate_pool(tmp_path):
    config = ContextRuntimeConfig()
    config = replace(config, policy=replace(config.policy, max_candidates=2))
    service, state, request = service_for(tmp_path, 2, config)
    _, audit = service.prepare(request, state)
    assert all(c["complete"] for c in audit["coverage"])


@pytest.mark.parametrize("assembler_limit", [9, 12])
def test_injected_assembler_cannot_authorize_nine_required_items(tmp_path, assembler_limit):
    service, state, request = service_for(tmp_path, 9)
    assembler = ContextAssembler(budget=ContextBudget(max_evidence=assembler_limit), policy=service.config.policy)
    service.assemble = lambda **kw: assembler.assemble(workspace_id="capacity", **kw)
    with pytest.raises(ValidationError) as error:
        service.prepare(request, state)
    assert error.value.details["code"] == "CONTEXT_INCOMPLETE"
    assert service.latest["status"] == "BLOCKED"
    assert all(not c["complete"] and len(c["missing_evidence_ids"]) == 9 for c in service.latest["coverage"])


def test_filtered_counterevidence_is_not_replaced_by_a_one_sided_pass():
    items = tuple(_evidence(str(i), i + 1, workspace_id="other" if i == 5 else "borealis") for i in range(6))
    groups = (EvidenceGroup("whole_conflict", tuple(str(i) for i in range(6)), ("g",), True),)
    bundle = ContextAssembler().assemble(workspace_id="borealis", query="Q", retrieved_evidence=items,
        evidence_groups=groups, required_evidence_limit=8)
    assert not bundle.selection.selected_ids and not bundle.selection.coverage[0].complete
    assert bundle.selection.coverage[0].missing_evidence_ids == tuple(str(i) for i in range(6))
