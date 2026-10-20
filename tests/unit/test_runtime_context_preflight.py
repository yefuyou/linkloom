from dataclasses import replace

import pytest

from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.context.assembler import ContextAssembler
from linkloom.context.policy import ContextPolicy
from linkloom.context.working import WorkingContext
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.context_engineering import ContextRuntimeConfig, RuntimeWorkingContext
from linkloom.runtime.errors import ValidationError
from tests.integration.test_runtime_v2_multi_action_resume import _state
from tests.unit.test_runtime_context_sources import bridge_for


def test_runtime_persists_protected_state_mapping_and_complete_request_budget(tmp_path):
    bridge, context, _ = bridge_for()
    config = ContextRuntimeConfig()
    store = ModelArtifactStore(tmp_path)
    state = replace(_state(), workflow="task_relations")
    assembler = ContextAssembler(policy=config.policy, budget=config.local_budget)
    service = RuntimeWorkingContext(config, bridge.tools, store, context.question,
        lambda **kw: assembler.assemble(workspace_id=context.workspace_id, **kw))
    request = ModelTurnRequest(state.run_id, "turn1", "task", "retrieval_agent", 1, context.question, None, [])
    prepared, audit = service.prepare(request, state)
    assert context.question in prepared.user_input and "UNTRUSTED_OPERATIONAL_HINT" in prepared.user_input
    assert audit["status"] == "COMPLETE" and audit["coverage"][0]["complete"]
    working = WorkingContext.from_json(store.read(audit["working_ref"]["ref"])["working_json"])
    assert working.user_goal == context.question and working.unresolved_questions
    assert working.ledger_ref.run_id == state.run_id and len(working.evidence_refs) == 2
    assert audit["budget"]["input_tokens"] + audit["budget"]["output_reserve_tokens"] <= config.policy.context_window_tokens
    assert {"tools", "latest_result", "verified_evidence", "working_context", "compaction_summary", "request_envelope"} <= audit["budget"]["segments"].keys()
    assert state.tool_ledger == [] and state.model_executions == []


def test_no_room_blocks_with_audited_coverage_instead_of_dropping_originals(tmp_path):
    bridge, context, _ = bridge_for()
    config = ContextRuntimeConfig(policy=ContextPolicy(context_window_tokens=500, output_reserve_tokens=100))
    store = ModelArtifactStore(tmp_path)
    state = replace(_state(), workflow="task_relations")
    assembler = ContextAssembler(policy=config.policy)
    service = RuntimeWorkingContext(config, bridge.tools, store, context.question,
        lambda **kw: assembler.assemble(workspace_id=context.workspace_id, **kw))
    request = ModelTurnRequest(state.run_id, "turn1", "task", "retrieval_agent", 1, context.question, None, [])
    with pytest.raises(ValidationError) as error:
        service.prepare(request, state)
    assert error.value.details["code"] == "CONTEXT_INCOMPLETE"
    assert service.latest["status"] == "BLOCKED" and service.latest["errors"]


def test_injected_counter_measures_entire_actual_normalized_request_and_output(tmp_path):
    import json
    bridge, context, _ = bridge_for()
    policy = ContextPolicy(model_id="offline-byte-tokenizer", context_window_tokens=65536,
        output_reserve_tokens=512, token_counter=lambda text: len(text.encode("utf8")),
        token_count_kind="INJECTED_UTF8_BYTE_REFERENCE; NOT_PROVIDER_TOKENS")
    config = ContextRuntimeConfig(policy=policy, tokenizer_id="TEST_UTF8_BYTES_V1")
    assembler = ContextAssembler(policy=policy)
    service = RuntimeWorkingContext(config, bridge.tools, ModelArtifactStore(tmp_path), context.question,
        lambda **kw: assembler.assemble(workspace_id=context.workspace_id, **kw))
    state = replace(_state(), workflow="task_relations")
    request = ModelTurnRequest(state.run_id, "turn1", "task", "retrieval_agent", 1, context.question, None, [])
    result, audit = service.prepare(request, state)
    actual = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
    assert audit["budget"]["input_tokens"] == len(actual) + config.envelope.tokens
    assert audit["budget"]["input_bytes"] == len(actual) + config.envelope.bytes
    assert result.generation_options.max_output_tokens == 512
    assert not audit["budget"]["actual_provider_tokens"]


def test_untrusted_compactor_cannot_introduce_new_evidence_ids(tmp_path):
    from linkloom.agents.model_adapter import ModelToolInteraction
    from linkloom.context.working import CompactionProposal
    from linkloom.runtime.models import ToolExecutionRecord
    from linkloom.tools.contracts import ToolCall, ToolResult
    class Invented:
        def compact(self, request):
            return CompactionProposal("invented context_ev_v1_unknown", ("context_ev_v1_unknown",))
    bridge, context, _ = bridge_for()
    config = ContextRuntimeConfig(history_soft_bytes=1, compactor=Invented())
    store = ModelArtifactStore(tmp_path)
    assembler = ContextAssembler(policy=config.policy)
    service = RuntimeWorkingContext(config, bridge.tools, store, context.question,
        lambda **kw: assembler.assemble(workspace_id=context.workspace_id, **kw))
    state = replace(_state(), workflow="task_relations")
    result = ToolResult("call", "inspect_task_context", "ok", value={"workspace_id": context.workspace_id})
    call = ToolCall("call", "inspect_task_context", {"workspace_id": context.workspace_id},
        run_id=state.run_id, task_id="task", agent_id="retrieval_agent", sequence=1)
    state = replace(state, tool_ledger=[ToolExecutionRecord(call_id=call.call_id, run_id=state.run_id, task_id="task",
        agent_id="retrieval_agent", sequence=1, tool_id=call.tool_id, arguments=call.arguments, status="completed", result=result.to_dict())])
    request = ModelTurnRequest(state.run_id, "turn2", "task", "retrieval_agent", 2, context.question, None, [],
        tool_history=[ModelToolInteraction(call, result)])
    with pytest.raises(ValidationError): service.prepare(request, state)
    assert service.latest["status"] == "BLOCKED"
