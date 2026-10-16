"""A rejected Final needs a new, counted model response and all original gates."""
import json

import pytest
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.models import RunRequest
from test_team_decision_final_contract_communication import (
    _engine, _search, _read, _final_from_read,
)


def run_correction(root, *, budget=4, repeat=False, fabricated=False, provider_envelope=False):
    saved = {}

    def invalid(request):
        saved["final"] = _final_from_read(request, omit_unknown_fields=False)
        return ModelAction.final('{"schema_version":')

    def correction(request):
        assert request.recovery_guidance and "Final" in request.recovery_guidance
        assert request.available_tools == []
        if repeat:
            return ModelAction.final("still invalid JSON")
        payload = json.loads(saved["final"].final_answer)
        if fabricated:
            payload["decision"]["value"] = "Fabricated Person is the accountable owner of Cobalt B."
        return ModelAction.final(json.dumps(payload))

    model = FakeModelAdapter([_search, _read, invalid, correction])
    selected = model
    if provider_envelope:
        from linkloom.agents.model_adapter import ModelResponse, ModelTurnProposal

        class Provider:
            def complete(self, request):
                action = model.decide(request)
                if action.kind == "final":
                    return ModelResponse(proposal=ModelTurnProposal.final(
                        request.turn_id + ":proposal", action.final_answer))
                return ModelResponse(action=action)

        selected = Provider()
    engine = _engine(root, selected)
    status = engine.start_multi_agent(RunRequest(
        request_id="v3_format_correction", thread_id="v3_format_correction",
        workflow="team_decision", query="Who is the accountable owner of Cobalt B in Atlas?",
        max_steps=budget, max_provider_requests=budget, dry_run=True,
    ))
    state = engine.checkpointer.get_latest(status.thread_id)
    result = json.loads((root / "checkpoints/results" / status.run_id / "result.json").read_text(encoding="utf-8"))
    return status, state, model, result


@pytest.mark.parametrize("provider_envelope", [False, True])
def test_invalid_final_gets_one_new_counted_response_before_reviewer(tmp_path, provider_envelope):
    status, state, model, result = run_correction(tmp_path, provider_envelope=provider_envelope)
    assert status.status == "completed"
    assert model.call_count == 4
    assert len(state.model_executions) == 4
    assert [r.status for r in state.model_executions].count("final_rejected") == 1
    assert len(state.tool_ledger) == 2
    assert result["review"]["decision"] == "evidence_sufficient"
    assert result["result"]["team_decision"]["decision"]["value"].startswith("As of 2025-02-15 Mira Chen")


@pytest.mark.parametrize("budget,repeat,fabricated", [(3, False, False), (4, True, False), (4, False, True)])
def test_correction_never_extends_budget_or_accepts_invalid_or_invented_facts(tmp_path, budget, repeat, fabricated):
    status, state, model, result = run_correction(tmp_path, budget=budget, repeat=repeat, fabricated=fabricated)
    assert status.status == "failed"
    assert model.call_count == budget
    assert result["result"] is None


@pytest.mark.parametrize("crash_boundary", ["response_durable", "final_rejected"])
def test_cold_resume_retains_single_final_correction_limit(tmp_path, crash_boundary):
    from linkloom.runtime.models import RuntimeState, SourceContext
    from linkloom.tools.runtime import create_retrieval_tool_runtime
    from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer
    from linkloom.runtime.artifacts import ModelArtifactStore
    from linkloom.runtime.model_loop import SingleAgentModelLoop
    from linkloom.agents.retrieval_agent import _validate_final_shape

    class Crash(BaseException):
        pass

    captured = []
    def _state():
        return RuntimeState(schema_version=1, run_id="run_v3", thread_id="thread_v3",
                            workflow="ask", status="running", step_seq=1,
                            request_ref="request.json", source=SourceContext(
                                index_path="vault_index.json", index_sha256="a" * 64,
                                vault_root_fingerprint="synthetic_v3"))

    def _runtime(executor):
        return create_retrieval_tool_runtime(lambda *args: [], lambda ref: {})

    def _policy():
        return ToolPolicyEnforcer(ToolCallPolicy(
            policy_version="p4-readonly-tools-v1", agent_id="retrieval_agent",
            allowed_tool_ids=["search_notes"], denied_tool_ids=[], max_calls=1,
            network="deny", vault_write="deny", gold_access="deny"))

    def checkpoint(state):
        captured.append(state)
        if state.model_executions and state.model_executions[-1].status == crash_boundary:
            raise Crash()

    store = ModelArtifactStore(tmp_path / "models")
    first = FakeModelAdapter([ModelAction.final("invalid")])
    kwargs = dict(task_id="task_v3", agent_id="retrieval_agent", user_input="synthetic",
                  available_tools=[], artifact_store=store)
    loop = SingleAgentModelLoop(first, _runtime(lambda **kw: []), _policy(),
                                max_steps=2, final_shape_validator=_validate_final_shape)
    with pytest.raises(Crash):
        loop.run(state=_state(), checkpoint_callback=checkpoint, **kwargs)
    second = FakeModelAdapter([ModelAction.final("invalid again")] * 2)
    resumed = SingleAgentModelLoop(second, _runtime(lambda **kw: []), _policy(),
                                   max_steps=1, final_shape_validator=_validate_final_shape)
    result = resumed.resume(state=captured[-1], **kwargs)
    assert result.status == "failed"
    assert second.call_count == 1
    assert sum(r.status == "final_rejected" for r in result.state.model_executions) == 2
