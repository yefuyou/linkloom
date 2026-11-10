"""A Latin name written directly next to CJK text is still that name, not a longer unknown one."""
import pytest
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui import scope_discovery as s, question_routing as r

RESOLVER = FrozenRelationResolver(("payment_gateway", "search_engine"))
SLOTS = [
    dict(subject="银杏", subject_aliases=["YX"], relation="payment_gateway", aliases=["payment gateway"],
         values=["Stripe", "Adyen"], slot_id="pg", workspace_id="one", reviewed=True),
    dict(subject="Tamarack", subject_aliases=["落叶松"], relation="search_engine", aliases=["search engine"],
         values=["OpenSearch"], slot_id="se", workspace_id="one", reviewed=True),
]


@pytest.mark.parametrize("query,subject,relation", [
    ("Tamarack目前的search_engine是什么？", "Tamarack", "search_engine"),
    ("YX现在的payment_gateway是什么？", "银杏", "payment_gateway"),
])
def test_latin_name_adjacent_to_cjk_binds_the_named_slot(query, subject, relation):
    snapshot = s.workspace_snapshot(SLOTS, RESOLVER, "one")
    check = s.entity_check(query, snapshot)
    assert not check["hard_veto"] and check["unbound_names"] == []
    result = r.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["route"] == "CURRENT"
    assert result["scope"] == {"subject": subject, "relation": relation}


def test_latin_relation_after_cjk_name_is_no_longer_an_unknown_name():
    # Without the ASCII boundary the whole "payment_gateway当前记录" was vetoed as one unknown name.
    query = "银杏payment_gateway当前记录的值是什么？"
    check = s.entity_check(query, s.workspace_snapshot(SLOTS, RESOLVER, "one"))
    assert not check["hard_veto"] and check["unbound_names"] == [] and check["unbound_proper_names"] == []
    result = r.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["route"] != "NO_MATCH"
    assert result["scope"] in (None, {"subject": "银杏", "relation": "payment_gateway"})


@pytest.mark.parametrize("query,phrase", [
    ("Tamarack目前的search_engine是什么？", "Tamarack"),
    ("YX现在的payment_gateway是什么？", "YX"),
    ("银杏payment_gateway当前记录的值", "payment_gateway"),
])
def test_mentions_uses_ascii_boundaries_for_latin_phrases(query, phrase):
    assert s._mentions(query.casefold(), phrase)


@pytest.mark.parametrize("query,phrase", [("Tamarackian setup", "Tamarack"), ("myYX现在", "YX"),
                                          ("payment_gateway2 当前", "payment_gateway")])
def test_ascii_boundary_still_rejects_a_longer_latin_word(query, phrase):
    assert not s._mentions(query.casefold(), phrase)


@pytest.mark.parametrize("query,unknown", [
    ("Ghostwood目前的search_engine是什么？", "Ghostwood"),
    ("Ghostwood的payment_gateway是什么？", "Ghostwood"),
])
def test_unknown_latin_name_next_to_cjk_is_still_unbound_by_its_own_extent(query, unknown):
    check = s.entity_check(query, s.workspace_snapshot(SLOTS, RESOLVER, "one"))
    assert unknown in check["unbound_names"] + check["unbound_proper_names"]
    assert all(name.isascii() for name in check["unbound_names"] + check["unbound_proper_names"])
    result = r.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["scope"] is None
