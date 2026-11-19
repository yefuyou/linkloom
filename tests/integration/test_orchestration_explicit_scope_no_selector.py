"""Explicit Subject/Relation plus orchestration adds no selector model call before the Supervisor decides."""
from linkloom.ui import question_routing
from tests.integration.test_orchestration_langgraph import World, delegation
from tests.integration.test_orchestration_product import (  # noqa: F401 (pytest fixture)
    SOURCE_REASON, WHY, make_env, started,
)


def test_explicit_scope_investigation_question_reaches_the_supervisor_without_a_selector_call(make_env, monkeypatch):
    selector_calls = []
    real = question_routing._select
    monkeypatch.setattr(question_routing, "_select", lambda *args, **kwargs: selector_calls.append(1) or real(*args, **kwargs))
    world = World([delegation("answer_single")], {WHY: (SOURCE_REASON, "Meridian A", "ok")})
    env = make_env(world)
    _, result = started(env, WHY)
    assert result["status"] == "COMPLETED" and result["parallel_investigation"]["status"] == "COMPLETE"
    assert len(world.supervisor_requests) == 1 and selector_calls == []
