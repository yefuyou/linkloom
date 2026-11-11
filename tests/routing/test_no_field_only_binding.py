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


@pytest.mark.xfail(reason="不存在的项目 + 已知的值仍可能错绑", strict=False)
def test_known_risk_unknown_project_with_a_known_value_may_still_bind():
    result = r.route_question("项目“星砂渡口”还在用 Quartz 吗？", QUARTZ_SLOTS, FrozenRelationResolver(("uses platform",)),
                              workspace_id="one", embedder=None)
    assert result["scope"] is None
