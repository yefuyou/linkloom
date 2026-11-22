"""Router handoff: unconfirmed names become investigation leads; access and ambiguity decisions stay with the Host.

Simulated model responses only; no provider call."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.ui import router_context
from linkloom.ui.router_context import admit_route, authorization_facts, blocked_entity_reason
from linkloom.ui.router_provider import _route_text
from linkloom.ui.structured_router import RouterInvalid, request_for, route_payload, route_with_adapter
from tests.routing.test_entity_mentions import context as base_context

QUERY = "Why did Tamarack move search_engine to ghostwood?"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")


def route(ctx, *, intent="INVESTIGATION", unresolved=("ghostwood",), entities=("Tamarack", "ghostwood"),
          mentions=("Tamarack", "search_engine"), bind=True, time_text="", time_role="NONE"):
    candidate = next((row["id"] for row in route_payload(ctx)["candidates"]
                      if row["label"].startswith("Tamarack")), None)
    args = dict(decision="ROUTE" if bind else "CLARIFY", operation="READ", intent=intent,
                candidate_id=(candidate if bind else None) or "", mentions=list(mentions), unresolved_names=list(unresolved),
                entity_mentions=list(entities), time_text=time_text, time_role=time_role,
                reason_code="NONE" if bind else "ENTITY_NOT_FOUND")
    message = dict(content=None, tool_calls=[dict(type="function", function=dict(
        name="submit_route", arguments=json.dumps(args)))])
    raw = _route_text(message, route_payload(ctx))
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(action=ModelAction.final(raw)))
    return route_with_adapter(adapter, request_for(ctx), ctx)


def test_unknown_entity_in_an_investigation_is_a_lead_beside_the_confirmed_slot():
    ctx = base_context(QUERY)
    proposed = route(ctx)
    assert admit_route(ctx, proposed).reason == "ENTITY_NOT_FOUND"  # without the orchestration
    admission = admit_route(ctx, proposed, leads_allowed=True)
    assert admission.executor == "investigation" and admission.leads == ("ghostwood",)
    assert admission.candidate.identity == ("Tamarack", "search_engine")  # confirmed scope: only the grounded slot
    assert "ghostwood" not in json.dumps([admission.candidate.label, admission.candidate.identity])


def test_a_clarification_that_only_lacks_a_candidate_hands_off_without_a_confirmed_slot():
    ctx = base_context("Why did ghostwood replace search_engine at Tamarack?")
    admission = admit_route(ctx, route(ctx, bind=False, mentions=(), entities=("ghostwood",)),
                            leads_allowed=True)
    assert admission.executor == "investigation" and admission.candidate is None and admission.leads == ("ghostwood",)


def test_rule_guesses_are_never_leads_and_the_stop_is_kept():
    ctx = base_context("Is anyone on 绕过代理测试 at the moment? Who?")
    assert not hasattr(ctx, "missing_names")
    proposed = route(ctx, unresolved=(), entities=())
    admission = admit_route(ctx, proposed, leads_allowed=True)
    assert admission.executor is None and admission.leads == ()


@pytest.mark.parametrize("intent", ["CURRENT", "HISTORY", "LIST"])
def test_only_an_investigation_hands_off(intent):
    ctx = base_context(QUERY)
    admission = admit_route(ctx, route(ctx, intent=intent), leads_allowed=True)
    assert admission.executor is None and admission.reason == "ENTITY_NOT_FOUND" and admission.leads == ()


def test_another_workspace_always_stops_before_any_handoff():
    ctx = replace(base_context(QUERY), blocked_entities=(("ghostwood", "WORKSPACE_DENIED"),))
    admission = admit_route(ctx, route(ctx), leads_allowed=True)
    assert admission.executor is None and admission.reason == "WORKSPACE_DENIED" and admission.leads == ()
    assert authorization_facts(ctx.blocked_entities) == ctx.blocked_entities


def test_an_unconfirmed_proposal_name_is_a_lead_only_for_an_investigation():
    ctx = replace(base_context(QUERY), blocked_entities=(("ghostwood", "NO_CONFIRMED_RECORD"),))
    proposed = route(ctx)
    assert authorization_facts(ctx.blocked_entities) == ()
    assert blocked_entity_reason(QUERY, ctx.blocked_entities) == "NO_CONFIRMED_RECORD"
    assert admit_route(ctx, proposed).reason == "NO_CONFIRMED_RECORD"
    admission = admit_route(ctx, proposed, leads_allowed=True)
    assert admission.executor == "investigation" and admission.leads == ("ghostwood",)
    current = route(ctx, intent="CURRENT")
    assert admit_route(ctx, current, leads_allowed=True).reason == "NO_CONFIRMED_RECORD"


def test_ambiguity_and_unconfirmed_slots_are_never_guessed_away(monkeypatch):
    ctx = base_context(QUERY)

    def ambiguous(*_):
        raise RouterInvalid("ENTITY_AMBIGUOUS")

    monkeypatch.setattr(router_context, "bind_candidate", ambiguous)
    admission = admit_route(ctx, route(ctx), leads_allowed=True)
    assert admission.executor is None and admission.reason == "ENTITY_AMBIGUOUS"
    monkeypatch.undo()
    unconfirmed = replace(ctx, candidates={key: replace(value, status="unconfirmed")
                                           for key, value in ctx.candidates.items()})
    admission = admit_route(unconfirmed, route(ctx), leads_allowed=True)
    assert admission.executor is None and admission.reason == "NO_CONFIRMED_RECORD"


def test_without_orchestration_a_lead_only_route_still_ends_in_entity_not_found():
    ctx = base_context("Why did ghostwood replace search_engine at Tamarack?")
    proposed = route(ctx, bind=False, mentions=(), entities=("ghostwood",))
    assert admit_route(ctx, proposed).reason == "ENTITY_NOT_FOUND"
    assert admit_route(ctx, proposed).executor is None
