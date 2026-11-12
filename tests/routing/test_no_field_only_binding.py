"""A field or slot name alone never binds a decision slot; the project must be named.

Latin names are written with spaces here: binding a Latin name written directly next to CJK text
depends on the ASCII name boundary (fix/ascii-name-boundary), which adds the no-space forms."""
import pytest
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui import question_routing as r
from linkloom.ui.structured_router import rule_needs_router

RESOLVER = FrozenRelationResolver(("payment_gateway", "search_engine"))
SLOTS = [
    dict(subject="银杏", subject_aliases=["YX"], relation="payment_gateway", aliases=["payment gateway"],
         values=["Stripe", "Adyen"], slot_id="pg", workspace_id="one", reviewed=True),
    dict(subject="Tamarack", subject_aliases=["落叶松"], relation="search_engine", aliases=["search engine"],
         values=["OpenSearch"], slot_id="se", workspace_id="one", reviewed=True),
]


def route(query, slots=SLOTS):
    return r.route_question(query, slots, RESOLVER, workspace_id="one", embedder=None)


@pytest.mark.parametrize("query,forbidden", [
    ("项目“星砂渡口”的payment_gateway当前值是什么？", {"subject": "银杏", "relation": "payment_gateway"}),
    ("ghostwood里search_engine是什么？", {"subject": "Tamarack", "relation": "search_engine"}),
    ("项目“星砂渡口”的 payment_gateway 当前值是什么？", {"subject": "银杏", "relation": "payment_gateway"}),
    ("ghostwood 里 search_engine 是什么？", {"subject": "Tamarack", "relation": "search_engine"}),
])
def test_unnamed_or_unknown_project_never_borrows_the_only_matching_slot(query, forbidden):
    result = route(query)
    assert result["scope"] != forbidden and result["scope"] is None
    assert result["route"] in {"CLARIFY", "NO_MATCH"}


def test_named_project_still_binds():
    result = route("Tamarack 的 search_engine 是什么？")
    assert result["route"] == "CURRENT"
    assert result["scope"] == {"subject": "Tamarack", "relation": "search_engine"}


def test_field_only_question_clarifies_with_the_candidate_and_hands_off_to_the_model():
    result = route("search_engine 现在是什么？")
    assert result["route"] == "CLARIFY" and result["scope"] is None
    discovery = result["discovery"]
    assert discovery["status"] == "AMBIGUOUS" and discovery["entity_anchored"] is False
    assert {"subject": "Tamarack", "relation": "search_engine"} in [
        {k: c[k] for k in ("subject", "relation")} for c in discovery["candidates"]]
    # In cascade mode a rule CLARIFY is sent to the model; the host then re-checks its choice.
    assert rule_needs_router(result["route"])


def test_first_person_reference_keeps_the_single_project_default():
    single = [SLOTS[1]]
    for query in ("我们的 search_engine 是什么？", "What search_engine do we use?"):
        result = route(query, single)
        assert result["route"] == "CURRENT"
        assert result["scope"] == {"subject": "Tamarack", "relation": "search_engine"}
    # Without the first-person reference the same field-only question is not bound.
    result = route("What search_engine is used now?", single)
    assert result["scope"] is None and result["route"] == "CLARIFY"


QUARTZ_SLOTS = [dict(subject="Juniper", relation="uses platform", aliases=["platform"], values=["Quartz"],
                     slot_id="platform", workspace_id="one", reviewed=True)]


QUARTZ_QUERY = "项目“星砂渡口”还在用 Quartz 吗？"


def reply(candidate, mentions, entity_mentions):
    fields = dict(schema_version="router/v5", decision="ROUTE", intent="CURRENT", domain="DECISION", operation="READ",
                  candidate_ids=[candidate], mentions=mentions, unresolved_names=[], entity_mentions=entity_mentions,
                  time_text="", time_role="NONE", reason_code="NONE")
    if entity_mentions is None:  # a reply in the older schema, without the entity list
        fields.update(schema_version="router/v4")
        del fields["entity_mentions"]
    return fields


def cascade_ask(tmp_path, monkeypatch, query, answer, *, slots=None):
    """Real Ask/SQLite path in cascade mode with a scripted router; no provider call.

    answer: None for no configured model, text for an unusable reply, or a function of the offered
    candidates returning the model's reply."""
    import json
    from linkloom.agents.model_adapter import ModelAction, ModelResponse
    from linkloom.ui.router_provider import RouterProvider
    from linkloom.ui.structured_router import PROMPT
    from linkloom.ui.unified_ask import UnifiedAskService
    from unified_ask_fixture import ATLAS, UnifiedAskFixture

    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    requests = []

    class Model:
        def complete(self, request):
            requests.append(request)
            if isinstance(answer, str):
                return ModelResponse(action=ModelAction.final(answer))
            data = json.loads(request.user_input.removeprefix(PROMPT))
            return ModelResponse(action=ModelAction.final(json.dumps(answer(data["candidates"]), ensure_ascii=False)))

    demo = UnifiedAskFixture(tmp_path / "decisions.sqlite")
    try:
        demo.app.repository.select_workspace(demo.workspace_ids[ATLAS])
        workspace = demo.app._workspace_id()
        ask = UnifiedAskService(demo.app, lambda: None, graph_service=False, slot_embedder=None,
                                router_provider=RouterProvider(None if answer is None else Model(), mode="cascade"))
        if slots is not None:
            slots = [dict(slot, workspace_id=workspace) for slot in slots]
            monkeypatch.setattr(ask, "_trusted_slots", lambda _workspace: slots)
        rules = r.route_question(query, ask._trusted_slots(workspace), demo.app.relation_resolver,
                                 workspace_id=workspace, embedder=None)
        result, status = ask.ask(dict(query=query, workspace_id=workspace))
    finally:
        demo.stop()
    assert status == 200
    return rules, result, requests


def juniper(mentions, entity_mentions):
    return lambda candidates: reply(next(c["id"] for c in candidates if c["label"].startswith("Juniper")),
                                    mentions, entity_mentions)


def assert_nothing_bound(result):
    assert result["status"] == "NEEDS_SCOPE" and result["router"]["decision"] == "CLARIFY"
    assert not result["scope"]["subject"] and not result["scope"]["relation"]
    assert not result["evidence"] and not result["conclusions"] and not result["router"].get("entities")


def test_cascade_unknown_project_with_a_known_value_never_binds(tmp_path, monkeypatch):
    """Cascade mode: the rules would bind Juniper by its recorded value alone, so the question goes to the
    model; it reports the project name it saw, the host does not know it, and nothing is bound.

    Rule mode (default) is unchanged: route_question itself still binds this question by the value."""
    rules, result, requests = cascade_ask(tmp_path, monkeypatch, QUARTZ_QUERY, juniper(["Quartz"], ["星砂渡口"]),
                                          slots=QUARTZ_SLOTS)
    assert rules["scope"] == {"subject": "Juniper", "relation": "uses platform"}  # the value-only rule binding
    assert len(requests) == 1 and result["router"]["source"] == "llm"
    assert result["router"]["reason_code"] == result["error"]["code"] == "ENTITY_NOT_FOUND"
    assert_nothing_bound(result)


@pytest.mark.parametrize("answer,calls,cause", [
    (None, 0, "MODEL_UNAVAILABLE"),                                  # no model configured
    ("not JSON", 1, "ROUTER_SCHEMA_INVALID"),                        # unusable reply
    (juniper(["Quartz"], None), 1, "ENTITY_MENTIONS_MISSING"),       # valid reply without entity_mentions
    (juniper(["Quartz"], []), 1, "ENTITY_MENTIONS_MISSING"),         # model failed to report an entity
    (juniper(["Quartz"], ["Invented"]), 1, "ENTITY_MENTIONS_MISSING"),  # nothing survives host verification
])
def test_cascade_value_only_binding_clarifies_without_a_valid_entity_list(tmp_path, monkeypatch, answer, calls, cause):
    """A value-only binding needs a valid model reply with entity_mentions. Without one the request
    clarifies; cascade mode never falls back to the rules' value binding."""
    rules, result, requests = cascade_ask(tmp_path, monkeypatch, QUARTZ_QUERY, answer, slots=QUARTZ_SLOTS)
    assert rules["scope"] == {"subject": "Juniper", "relation": "uses platform"}
    assert len(requests) == calls
    assert result["router"]["reason_code"] == result["error"]["code"] == "ENTITY_AMBIGUOUS"
    assert result["router"]["source"] == "host" and result["router"]["fallback_reason"] == cause
    assert_nothing_bound(result)


def test_cascade_value_only_question_binds_when_the_model_reports_the_recorded_value(tmp_path, monkeypatch):
    def supplier(candidates):
        return reply(next(c["id"] for c in candidates if "uses supplier" in c["label"]), ["Birchline"], ["Birchline"])
    rules, result, requests = cascade_ask(tmp_path, monkeypatch, "Is Birchline still in use?", supplier)
    assert rules["scope"] == {"subject": "Launch", "relation": "uses supplier"}  # by the recorded value alone
    assert len(requests) == 1 and result["status"] == "COMPLETED" and result["router"]["source"] == "llm"
    assert result["scope"]["subject"] == "Launch" and result["evidence"]
    assert result["conclusions"]


def test_cascade_unavailable_router_preserves_rule_clarification_reason(tmp_path, monkeypatch):
    rules, result, requests = cascade_ask(tmp_path, monkeypatch, "payment_gateway现在是什么？", None, slots=SLOTS)
    assert rules["route"] == "CLARIFY" and not requests
    assert result["router"]["fallback_reason"] == "MODEL_UNAVAILABLE"
    assert result["router"]["reason_code"] == result["error"]["code"] == "DECISION_SCOPE_AMBIGUOUS"
    assert_nothing_bound(result)
