"""Cascade entity contract: the model lists the entity names it sees in the question, copied exactly; the host
locates each one in the question and matches it against the workspace's known names and aliases. A located
name that is not known is an unknown entity (CLARIFY / ENTITY_NOT_FOUND) and nothing may bind in its place.

Simulated model responses only; no provider call."""
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.temporal_graph.models import GraphSnapshot, Node
from linkloom.ui.router_context import admit_route, build_context, handoff_blocker, value_only_binding
from linkloom.ui.router_provider import _route_text, _submission
from linkloom.ui.structured_router import request_for, route_payload, route_with_adapter

RESOLVER = FrozenRelationResolver(("payment_gateway", "search_engine", "uses platform"))
SLOTS = [
    dict(subject="银杏", subject_aliases=["YX"], relation="payment_gateway", aliases=["payment gateway"],
         values=["Stripe", "Adyen"], slot_id="pg", workspace_id="one", reviewed=True),
    dict(subject="Tamarack", subject_aliases=["落叶松"], relation="search_engine", aliases=["search engine"],
         values=["OpenSearch"], slot_id="se", workspace_id="one", reviewed=True),
    dict(subject="Juniper", relation="uses platform", aliases=["platform"], values=["Quartz"],
         slot_id="platform", workspace_id="one", reviewed=True),
]
TASK = Node("one", "task", "bypass-proxy-test", "绕过代理测试", "Tamarack")
MEMBER_A, MEMBER_E = Node("one", "person", "member-a", "组员A"), Node("one", "person", "member-e", "组员E")
REPORTS_TO = SimpleNamespace(source=MEMBER_A, target=MEMBER_E, blocker=False, time_match=lambda when: "VALID",
                             to_dict=lambda: dict(type="reports_to", source="组员A", target="组员E"))
GRAPH = GraphSnapshot("one", (REPORTS_TO,), catalogue=(TASK,), records=(
    dict(node_id=TASK.node_id, kind="task", title="绕过代理测试", project="Tamarack", project_aliases=["落叶松"],
         states=[dict(valid_from="2026-10-06", valid_to=None)]),))

K02 = "Is anyone on 绕过代理测试 at the moment? Who?"
K14 = "组员A 汇报的那个人管着哪个项目？我得找对方开删除权限。"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")


def context(query):
    return build_context(query, "one", SLOTS, RESOLVER, view=lambda *_: dict(status="FOUND", history=[object()]),
                         graph_snapshot=GRAPH, graph_available=True, query_time="2026-11-11T02:00:00+00:00")


def model_route(ctx, label, mentions, entity_mentions, intent="CURRENT"):
    """One simulated submit_route tool call through the real wire parsing and host validation."""
    candidate = next(row["id"] for row in route_payload(ctx)["candidates"] if row["label"].startswith(label))
    args = dict(decision="ROUTE", operation="READ", intent=intent, candidate_id=candidate, mentions=mentions,
                unresolved_names=[], time_text="", time_role="NONE", reason_code="NONE")
    if entity_mentions is not None:
        args["entity_mentions"] = entity_mentions
    message = dict(content=None, tool_calls=[dict(type="function", function=dict(
        name="submit_route", arguments=json.dumps(args, ensure_ascii=False)))])
    raw = _route_text(message, route_payload(ctx))
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(action=ModelAction.final(raw)))
    return route_with_adapter(adapter, request_for(ctx), ctx)


def test_tool_schema_asks_the_model_for_entity_mentions():
    tool = _submission(route_payload(context(K02)))["function"]["parameters"]
    assert tool["properties"]["entity_mentions"] == {"type": "array", "items": {"type": "string"}}
    assert "entity_mentions" in tool["required"]


def test_v6_k02_auxiliary_question_no_longer_invents_a_name():
    ctx = context(K02)
    # The rule regex reads "anyone" as a project name. In cascade mode that guess no longer stops the request.
    assert ctx.missing_names == ("anyone",)
    assert handoff_blocker(ctx) == "ENTITY_NOT_FOUND" and handoff_blocker(ctx, model_entities=True) is None
    route = model_route(ctx, "绕过代理测试", ["绕过代理测试"], ["绕过代理测试"])
    assert route.entity_mentions == ("绕过代理测试",)
    admission = admit_route(ctx, route, model_entities=True)
    assert admission.executor == "graph" and admission.reason == "NONE"
    assert admission.candidate.kind == "task" and admission.candidate.identity == (TASK.node_id,)
    # Without the model-entity contract (llm_first mode) the admission is unchanged.
    assert admit_route(ctx, route).reason == "ENTITY_NOT_FOUND"


def test_v6_k14_relative_clause_is_not_a_name():
    ctx = context(K14)
    assert ctx.missing_names and handoff_blocker(ctx, model_entities=True) is None
    route = model_route(ctx, "组员A", ["组员A"], ["组员A"], intent="RELATION_MULTIHOP")
    admission = admit_route(ctx, route, model_entities=True)
    assert admission.executor == "graph" and admission.candidate.identity == (MEMBER_A.node_id,)
    assert route.domain == "RELATION" and route.intent == "RELATION_MULTIHOP"


@pytest.mark.parametrize("query,unknown,label,mentions", [
    ("项目“星砂渡口”的payment_gateway当前值是什么？", "星砂渡口", "银杏 · payment_gateway", ["payment_gateway"]),
    ("项目“星砂渡口”的 payment_gateway 当前值是什么？", "星砂渡口", "银杏 · payment_gateway", ["payment_gateway"]),
    ("ghostwood里search_engine是什么？", "ghostwood", "Tamarack · search_engine", ["search_engine"]),
    ("ghostwood 里 search_engine 是什么？", "ghostwood", "Tamarack · search_engine", ["search_engine"]),
    ("项目“星砂渡口”还在用 Quartz 吗？", "星砂渡口", "Juniper · uses platform", ["Quartz"]),
])
def test_unknown_entity_clarifies_and_never_binds_the_slot_the_model_picked(query, unknown, label, mentions):
    ctx = context(query)
    # The model picks the only slot with that field or value, but also reports the name it saw.
    route = model_route(ctx, label, mentions, [unknown])
    assert route.decision == "ROUTE" and route.entity_mentions == (unknown,)
    admission = admit_route(ctx, route, model_entities=True)
    assert admission.reason == "ENTITY_NOT_FOUND"
    assert admission.executor is None and admission.candidate is None and admission.as_of is None


@pytest.mark.parametrize("query,name,identity", [
    ("Tamarack 的 search_engine 是什么？", "Tamarack", ("Tamarack", "search_engine")),
    ("YX 的 payment_gateway 是什么？", "YX", ("银杏", "payment_gateway")),
    ("落叶松的 search_engine 是什么？", "落叶松", ("Tamarack", "search_engine")),
    ("Juniper 还在用 Quartz 吗？", "Juniper", ("Juniper", "uses platform")),
])
def test_known_name_or_alias_binds_its_own_slot(query, name, identity):
    ctx = context(query)
    route = model_route(ctx, " · ".join(identity), [name], [name])
    admission = admit_route(ctx, route, model_entities=True)
    assert admission.executor == "decision" and admission.reason == "NONE"
    assert admission.candidate.identity == identity


def test_entity_mention_that_is_not_question_text_is_invalid_and_dropped():
    ctx = context("Tamarack 的 search_engine 是什么？")
    route = model_route(ctx, "Tamarack · search_engine", ["Tamarack"], ["星砂渡口", "Tamarack", "ＴＡＭＡＲＡＣＫ"])
    # Only text the host finds in the question survives, as the question's own spelling.
    assert route.entity_mentions == ("Tamarack",)
    assert admit_route(ctx, route, model_entities=True).candidate.identity == ("Tamarack", "search_engine")


def test_value_question_without_an_unknown_entity_still_binds_by_value():
    ctx = context("现在还在用 Quartz 吗？")
    route = model_route(ctx, "Juniper · uses platform", ["Quartz"], [])
    assert route.entity_mentions == ()
    assert admit_route(ctx, route, model_entities=True).candidate.identity == ("Juniper", "uses platform")


def test_reply_without_entity_mentions_keeps_the_host_name_check():
    ctx = context(K02)
    route = model_route(ctx, "绕过代理测试", ["绕过代理测试"], None)
    assert route.entity_mentions is None
    assert admit_route(ctx, route, model_entities=True).reason == "ENTITY_NOT_FOUND"


@pytest.mark.parametrize("entity_mentions", ["星砂渡口", [1], ["x"] * 9, ["长" * 161]])
def test_malformed_entity_mentions_never_become_a_route(entity_mentions):
    from linkloom.ui.structured_router import RouterInvalid
    ctx = context("Tamarack 的 search_engine 是什么？")
    with pytest.raises(RouterInvalid, match="ROUTER_SCHEMA_INVALID"):
        model_route(ctx, "Tamarack · search_engine", ["Tamarack"], entity_mentions)


@pytest.mark.parametrize("query,scope,expected", [
    ("项目“星砂渡口”还在用 Quartz 吗？", dict(subject="Juniper", relation="uses platform"), True),
    ("现在还在用 Quartz 吗？", dict(subject="Juniper", relation="uses platform"), True),
    ("Juniper 还在用 Quartz 吗？", dict(subject="Juniper", relation="uses platform"), False),
    ("落叶松的 search_engine 是什么？", dict(subject="Tamarack", relation="search_engine"), False),
    ("我们还在用 Quartz 吗？", dict(subject="Juniper", relation="uses platform"), False),
    ("search_engine 现在是什么？", None, False),
])
def test_cascade_sends_a_value_only_rule_binding_to_the_model(query, scope, expected):
    assert value_only_binding(query, scope, SLOTS) is expected


def test_cascade_ask_uses_the_model_entities_end_to_end(tmp_path):
    """Real Ask/SQLite path with a scripted router: no provider call."""
    from linkloom.ui.router_provider import RouterProvider
    from linkloom.ui.structured_router import PROMPT
    from linkloom.ui.unified_ask import UnifiedAskService
    from unified_ask_fixture import ATLAS, UnifiedAskFixture

    requests = []

    def service(app, entity_mentions):
        class Model:
            def complete(self, request):
                requests.append(request)
                data = json.loads(request.user_input.removeprefix(PROMPT))
                supplier = next(c["id"] for c in data["candidates"] if "uses supplier" in c["label"])
                return ModelResponse(action=ModelAction.final(json.dumps(dict(
                    schema_version="router/v5", decision="ROUTE", intent="CURRENT", domain="DECISION",
                    operation="READ", candidate_ids=[supplier], mentions=["supplier"], unresolved_names=[],
                    entity_mentions=entity_mentions, time_text="", time_role="NONE", reason_code="NONE"))))
        return UnifiedAskService(app, lambda: None, graph_service=False,
                                 router_provider=RouterProvider(Model(), mode="cascade"), slot_embedder=None)

    demo = UnifiedAskFixture(tmp_path / "decisions.sqlite")
    try:
        demo.app.repository.select_workspace(demo.workspace_ids[ATLAS])
        workspace = demo.app._workspace_id()
        # The rule regex takes "anyone" for a project; the model names only Launch and the host knows it.
        known, status = service(demo.app, ["Launch"]).ask(
            dict(query="Is anyone on Launch still using the same supplier?", workspace_id=workspace))
        assert status == 200 and len(requests) == 1 and known["status"] == "COMPLETED", known
        assert known["router"]["source"] == "llm" and known["scope"]["subject"] == "Launch"
        assert known["evidence"] and known["conclusions"]
        # The model picks the same slot for a project the workspace does not know.
        unknown, status = service(demo.app, ["UnlistedProject"]).ask(
            dict(query="What supplier is UnlistedProject using?", workspace_id=workspace))
        assert status == 200 and len(requests) == 2 and unknown["status"] == "NEEDS_SCOPE"
        assert unknown["router"]["reason_code"] == "ENTITY_NOT_FOUND" and unknown["router"]["source"] == "llm"
        assert not unknown["evidence"] and not unknown["conclusions"] and not unknown["scope"]["subject"]
    finally:
        demo.stop()


# Wordings written for this change, outside every evaluation set.
@pytest.mark.parametrize("query,label,mentions,entity_mentions,expected", [
    # unknown project name: nothing binds, whatever slot the model picked
    ("Which search_engine does Driftmoor run on?", "Tamarack · search_engine", ["search_engine"], ["Driftmoor"],
     "ENTITY_NOT_FOUND"),
    ("青岚计划的 payment_gateway 选的是哪家？", "银杏 · payment_gateway", ["payment_gateway"], ["青岚计划"],
     "ENTITY_NOT_FOUND"),
    # known project alias: binds the alias's own project
    ("What payment gateway has YX settled on?", "银杏 · payment_gateway", ["YX", "payment gateway"], ["YX"],
     ("银杏", "payment_gateway")),
    ("落叶松那边现在用的 search engine 是哪个？", "Tamarack · search_engine", ["落叶松", "search engine"], ["落叶松"],
     ("Tamarack", "search_engine")),
    # only a recorded value, no project named: the value binds once the model reports no unknown entity
    ("Is OpenSearch still the pick?", "Tamarack · search_engine", ["OpenSearch"], [],
     ("Tamarack", "search_engine")),
    ("Adyen 现在还在用吗？", "银杏 · payment_gateway", ["Adyen"], ["Adyen"],
     ("银杏", "payment_gateway")),
])
def test_generalization_wordings(query, label, mentions, entity_mentions, expected):
    ctx = context(query)
    admission = admit_route(ctx, model_route(ctx, label, mentions, entity_mentions), model_entities=True)
    if expected == "ENTITY_NOT_FOUND":
        assert admission.reason == expected and admission.executor is None and admission.candidate is None
    else:
        assert admission.executor == "decision" and admission.candidate.identity == expected
