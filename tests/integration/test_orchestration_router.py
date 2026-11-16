"""Ask routing hands unconfirmed historical entities to the orchestration as leads, end to end.

Real product Ask (structured router -> UnifiedAskService -> ProductInvestigationService -> LangGraph), real
Runtime, Reviewer gate and synthesis. The router model is a script that answers the way the router contract
asks (INVESTIGATION, the unknown name reported in unresolved_names). The Host decides what to do with it.
"""
import json

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.router_provider import RouterProvider
from linkloom.ui.structured_router import PROMPT
from linkloom.ui.unified_ask import UnifiedAskService
from tests.integration.test_orchestration_langgraph import FACT, Q_OWNER, Q_SUCC, TASKS, World, delegation
from tests.integration.test_orchestration_product import (
    COUNTER, SOURCE_REASON, TOTAL, Env, assert_cited, make_env,  # noqa: F401 (pytest fixture)
)

Q3 = ("Which plan replaced Meridian A, who owns it, who is that owner's direct leader, "
      "and when did the replacement take effect?")
LEAD = "Meridian A"


class ScriptedRouter:
    """Replies like the router contract asks for an investigation question that names an unknown object."""

    def __init__(self, *, bind=False, intent="INVESTIGATION", leads=(LEAD,)):
        self.bind, self.intent, self.leads, self.requests = bind, intent, tuple(leads), []

    def complete(self, request):
        self.requests.append(request)
        data = json.loads(request.user_input.removeprefix(PROMPT))
        ids = [item["id"] for item in data["candidates"]][:1] if self.bind else []
        reply = dict(schema_version="router/v5", decision="ROUTE" if ids else "CLARIFY", intent=self.intent,
            domain="DECISION", operation="READ", candidate_ids=ids, mentions=["Launch"] if ids else [],
            unresolved_names=list(self.leads), entity_mentions=list(self.leads) + (["Launch"] if ids else []),
            time_text="", time_role="NONE", reason_code="NONE" if ids else "ENTITY_NOT_FOUND")
        return ModelResponse(action=ModelAction.final(json.dumps(reply)))


def ask_service(env, router, mode):
    return UnifiedAskService(env.app, lambda: env.service, graph_service=False, slot_embedder=None,
                             router_provider=RouterProvider(router, mode=mode))


def trace_of(env, run_id):
    status, payload = env.run_trace(run_id)
    assert status == 200, payload
    return payload["result"]


def worker_context(world, description):
    worker = next(item for item in world.workers if item.description == description)
    query = json.loads(worker.requests[0].user_input.rsplit("team_decision_request=", 1)[1])["query"]
    return json.loads(query.split("investigation_context=", 1)[1])


def drive(env, ask, query):
    result, status = ask.ask(dict(query=query, workspace_id=env.workspace_id))
    assert status == 202, result
    return result, env.finish(result["agent_run_id"])


def test_original_q3_runs_on_the_graph_with_the_unknown_entity_as_a_lead(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], TASKS)
    env = make_env(world)
    router = ScriptedRouter()
    started, final = drive(env, ask_service(env, router, "llm_first"), Q3)
    assert len(router.requests) == 1 and started["router"]["entity_leads"] == [LEAD]
    assert started["router"]["decision"] == "ROUTE" and started["router"].get("reason_code") == "NONE"
    assert started["error"] is None and LEAD not in [item["label"] for item in started["router"]["entities"]]
    assert final["status"] == "COMPLETED" and final["parallel_investigation"]["status"] == "COMPLETE"
    assert_cited(final)
    state = trace_of(env, final["agent_run_id"])
    # the Host-confirmed scope and the unconfirmed lead stay in separate state fields
    assert state["entity_leads"] == [LEAD] and LEAD not in json.dumps(state["confirmed_scope"])
    assert state["confirmed_scope"]["workspace_id"] == env.workspace_id and "subject" not in state["confirmed_scope"]
    context = worker_context(world, Q_SUCC)
    assert context["entity_leads_unverified"] == [LEAD] and "confirmed_scope" in context
    assert LEAD not in json.dumps(context["confirmed_scope"])
    supervisor = json.loads(world.supervisor_requests[0].user_input.split("context=", 1)[1])
    assert supervisor["entity_leads"] == [LEAD] and LEAD not in json.dumps(supervisor["confirmed_scope"])


@pytest.mark.parametrize("mode", ["llm_first", "cascade"])
@pytest.mark.parametrize("bind", [False, True])
def test_counterexample_is_resolved_by_source_evidence_not_assumed(make_env, mode, bind):
    world = World([delegation("answer_single")], {COUNTER: (SOURCE_REASON, LEAD, "ok")})
    env = make_env(world)
    started, final = drive(env, ask_service(env, ScriptedRouter(bind=bind), mode), COUNTER)
    assert started["router"]["entity_leads"] == [LEAD] and final["status"] == "COMPLETED"
    scope = trace_of(env, final["agent_run_id"])["confirmed_scope"]
    # A grounded Launch slot becomes the confirmed scope; the unknown historical entity never does.
    assert (scope.get("subject"), scope.get("relation")) == (("Launch", "uses supplier") if bind else (None, None))
    assert LEAD not in json.dumps(scope)
    answer, evidence = assert_cited(final)
    # The premise names Meridian A as the replacing party. Only the cited source says who replaced whom.
    quotes = [item["quote"] for item in evidence.values()]
    assert any("Launch replaced Meridian A with Cobalt B" in quote for quote in quotes)
    assert {item["source_id"] for item in evidence.values()} == {env.source_ids[SOURCE_REASON]}
    assert SOURCE_REASON in json.dumps(answer) and "Meridian A replaced Launch" not in json.dumps(answer)
    assert worker_context(world, COUNTER)["entity_leads_unverified"] == [LEAD]


def test_lead_handoff_needs_the_orchestration_and_never_relaxes_other_stops(make_env):
    world = World([], {})
    env = make_env(world)
    off = ProductInvestigationService(env.app, model_factory=world.factory, external_provider=False)
    env.use(off)
    router = ScriptedRouter()
    result, status = ask_service(env, router, "llm_first").ask(dict(query=COUNTER, workspace_id=env.workspace_id))
    assert status == 200 and result["status"] == "NEEDS_SCOPE" and result["error"]["code"] == "ENTITY_NOT_FOUND"
    assert not result["evidence"] and not world.supervisor_requests
    assert not list((env.tmp_path / "work" / "investigations").glob("*/langgraph.sqlite"))
    # A lookup (not an investigation) with an unknown name still stops, orchestration or not.
    env.use(ProductInvestigationService(env.app, model_factory=world.factory, external_provider=False,
                                        parallel_limits=TOTAL))
    current = ScriptedRouter(intent="CURRENT", bind=True)
    result, status = ask_service(env, current, "llm_first").ask(
        dict(query="What supplier is Meridian A using?", workspace_id=env.workspace_id))
    assert status == 200 and result["error"]["code"] == "ENTITY_NOT_FOUND" and not world.supervisor_requests


def test_another_workspace_name_is_never_a_lead(tmp_path, make_env):
    world = World([delegation("answer_single")], {})
    env = make_env(world, others=("Orchid Labs",))
    router = ScriptedRouter(leads=("Orchid Labs",))
    query = "Why did Orchid Labs replace Launch with Cobalt B?"
    for mode in ("llm_first", "cascade"):
        result, status = ask_service(env, router, mode).ask(dict(query=query, workspace_id=env.workspace_id))
        assert status == 200 and result["status"] == "NEEDS_SCOPE"
        assert result["error"]["code"] == "WORKSPACE_DENIED" and not result["evidence"]
    assert not world.supervisor_requests and not world.workers
    assert not list((env.tmp_path / "work" / "investigations").glob("*/langgraph.sqlite"))
    # Other workspace names never reach the Supervisor, a Worker, or a run's inspect output either.
    ok, final = drive(env, ask_service(env, ScriptedRouter(), "llm_first"), COUNTER)
    assert final["status"] != "NEEDS_SCOPE"
    exposed = json.dumps([final, trace_of(env, final["agent_run_id"])]) + world.supervisor_requests[0].user_input
    assert "Orchid Labs" not in exposed


def test_cascade_without_router_candidates_keeps_the_legacy_fallback_and_masks_nothing(make_env):
    world = World([], {})
    env = make_env(world)
    router = ScriptedRouter()
    result, status = ask_service(env, router, "cascade").ask(dict(query=Q3, workspace_id=env.workspace_id))
    assert status == 200 and not router.requests and not world.supervisor_requests
    assert result["router"]["fallback_reason"] == "NO_ROUTER_CANDIDATES" and result["error"]["code"] != "ENTITY_NOT_FOUND"
    assert not list((env.tmp_path / "work" / "investigations").glob("*/langgraph.sqlite"))
