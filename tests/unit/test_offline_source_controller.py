"""Offline fake-model policy must not turn host scheduling delay into refusal."""
import json
from types import SimpleNamespace

from run_bounded_multihop import SourceOnlyController
from linkloom.runtime.team_decision_planner import plan_relation_investigation


def test_host_delay_does_not_expire_offline_model_before_first_turn(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("run_bounded_multihop.time.monotonic", lambda: clock[0])
    controller = SourceOnlyController()
    clock[0] = 31.0  # Snapshot/index/server setup is not a model failure.
    plan = plan_relation_investigation("Orion 项目负责人的直属领导是谁？")
    request = SimpleNamespace(
        user_input="team_decision_request=" + json.dumps({
            "task_plan": plan.to_dict(), "source_context": {},
        }), observation=None, sequence=1,
    )
    action = controller(request)
    assert action.tool_call.tool_id == "search_notes"
    assert action.tool_call.arguments["source_context"]["subgoal_id"] == "owner"
