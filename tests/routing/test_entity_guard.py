"""General organization/proper-name guards and grammatical references."""
from copy import deepcopy
import json
import numpy as np
import pytest
from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui import scope_discovery as s, question_routing as r

RESOLVER = FrozenRelationResolver(("uses platform",), aliases={"平台": "uses platform"})
SLOTS = [dict(subject="Launch", subject_aliases=["云帆", "Loom Team"], relation="uses platform",
              values=["Quartz"], slot_id="platform", workspace_id="one", reviewed=True)]

class Model:
    def encode(self, texts):
        return np.asarray([[1., 0.] for _ in texts])

class Forced:
    def complete(self, request):
        return ModelResponse(action=ModelAction.final('{"intent":"CURRENT","slot_id":"platform","as_of":null}'))


@pytest.mark.parametrize("name", ["Ghost team", "Ghost Team", "Ghost-team", "Ghost_team", "GhostTeam",
    "Ghost's team", "Ghost的团队", "Ghost团队", "幽灵团队", "幽灵组", "幽灵部门", "Ghost项目",
    "Ghost project", "Ghost Harbor group", "Ghost Harbor department", "GHOST TEAM"])
@pytest.mark.parametrize("template", ["Please show the platform selected by {}.", "关于{}，现在用哪个平台？",
                                       "{}: which platform?"])
@pytest.mark.parametrize("embedded", [False, True])
def test_unknown_organization_anywhere_cannot_bind_to_launch(name, template, embedded):
    query = template.format(name)
    result = r.route_question(query, SLOTS, RESOLVER, workspace_id="one",
                              embedder=Model() if embedded else None, provider_factory=Forced)
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert result["discovery"]["entity_check"]["hard_veto"]
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


@pytest.mark.parametrize("query", ["Ghost recommended the platform.", "The platform suggested by Ghost?",
                                  "Our platform, according to Ghost, is which one?"])
@pytest.mark.parametrize("provider", [None, Forced])
def test_unbound_capital_name_outside_old_frames_never_auto_matches(query, provider):
    result = r.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=Model(), provider_factory=provider)
    assert result["route"] == "CLARIFY" and result["scope"] is None
    assert "Ghost" in result["discovery"]["entity_check"]["unbound_proper_names"]
    assert result["discovery"]["raw_candidates"]


@pytest.mark.parametrize("label", ["Launch team", "Launch's team", "云帆团队", "云帆项目", "Loom Team group"])
def test_registered_names_and_aliases_are_not_rejected_by_organization_scan(label):
    result = r.route_question(f"Please show the platform chosen by {label}.", SLOTS, RESOLVER,
                              workspace_id="one", embedder=Model())
    assert not result["discovery"]["entity_check"]["hard_veto"]
    assert not result["discovery"]["entity_check"]["unbound_proper_names"]
    assert result["route"] != "NO_MATCH"
    assert result["scope"] is None or result["scope"]["subject"] == "Launch"


@pytest.mark.parametrize("reference", ["我们", "咱们", "我司", "we", "our", "us", "We", "Our", "Us",
                                       "我们的团队", "咱们的项目", "our team", "our project", "my", "ours"])
def test_reference_never_reduces_confidence_even_when_parser_calls_it_unknown(monkeypatch, reference):
    monkeypatch.setattr(s, "parse_query_clues", lambda *a: {"unresolved": [reference]})
    result = s.discover_scope("Which platform is Launch using?", SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["confidence"] == result["raw_candidates"][0]["score"]
    assert result["scope"] == {"subject": "Launch", "relation": "uses platform"}


@pytest.mark.parametrize("query", ["我们现在用哪个平台？", "咱们现在用哪个平台？", "我司的平台是哪个？",
                                  "Which platform do we use?", "What is our platform?", "Which platform is used by us?",
                                  "我们团队现在用哪个平台？", "What platform did our team choose?"])
def test_references_in_real_questions_remain_bindable(query):
    result = r.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert not result["discovery"]["entity_check"]["hard_veto"]
    assert not result["discovery"]["entity_check"]["unbound_proper_names"]
    assert result["scope"] == {"subject": "Launch", "relation": "uses platform"}


@pytest.mark.parametrize("accepted", [False, True])
def test_embedding_floor_rejects_only_semantic_recall_and_cannot_be_resurrected(monkeypatch, accepted):
    import linkloom.indexing.vector as vector
    from types import SimpleNamespace
    value = s.SLOT_DENSE_RECALL_MIN_SCORE + (.01 if accepted else -.01)
    class Index:
        def __init__(self, embedder): pass
        def build(self, documents): pass
        def search(self, *args, **kwargs):
            return [SimpleNamespace(resource_id="platform", score=value)]
    monkeypatch.setattr(vector, "VectorIndex", Index)
    calls = []
    def factory():
        calls.append(1)
        return Forced()
    result = r.route_question("Which substrate?", SLOTS, RESOLVER, workspace_id="one",
                              embedder=Model(), provider_factory=factory)
    if accepted:
        assert result["route"] == "CURRENT" and result["decided_by"] == "llm"
        assert result["discovery"]["raw_candidates"][0]["dense_score"] == value
    else:
        assert result["route"] == "NO_MATCH" and result["scope"] is None
        assert result["discovery"]["raw_candidates"] == result["discovery"]["candidates"] == []
        assert not calls


def test_complete_lexical_alias_is_independent_of_low_dense_similarity():
    class Orthogonal:
        def encode(self, texts):
            return np.asarray([[1., 0.] if "uses platform" in t else [0., 1.] for t in texts])
    result = r.route_question("Which platform?", SLOTS, RESOLVER, workspace_id="one", embedder=Orthogonal())
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Launch"
    assert result["discovery"]["raw_candidates"][0]["dense_score"] is None


@pytest.mark.parametrize("label", ["大云帆团队", "新云帆团队", "大 云帆团队", "新 Loom Team group", "Ghost Launch team", "大Launch项目", "Will team", "May project"])
def test_registered_suffix_does_not_bind_a_different_full_organization(label):
    result = r.route_question(f"Which platform was chosen by {label}?", SLOTS, RESOLVER,
                              workspace_id="one", embedder=Model(), provider_factory=Forced)
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert result["discovery"]["entity_check"]["hard_veto"]
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


@pytest.mark.parametrize("label", ["时光团队", "请问时光团队", "来自时光团队"])
def test_entity_detection_does_not_rewrite_registered_time_like_names(label):
    slots = deepcopy(SLOTS)
    slots[0]["subject_aliases"].append("时光")
    result = r.route_question(f"{label}，现在用哪个平台？", slots, RESOLVER, workspace_id="one", embedder=Model())
    assert result["route"] != "NO_MATCH"
    assert not result["discovery"]["entity_check"]["hard_veto"]
    assert not result["discovery"]["entity_check"]["unbound_proper_names"]
    assert result["scope"] is None or result["scope"]["subject"] == "Launch"



def test_verified_proposal_name_is_not_a_reviewed_answer_or_index_term():
    slots = deepcopy(SLOTS)
    slots[0]["reference_values"] = ["ProposalOnly"]
    snapshot = s.workspace_snapshot(slots, RESOLVER, "one")
    # Recognizing a source-backed mention cannot promote it to approved history.
    assert not s.entity_check("Is ProposalOnly the current platform?", snapshot)["unbound_proper_names"]
    assert s.retrieve_raw("ProposalOnly?", snapshot, s.RetrievalConfig(None)) == []
    assert slots[0]["values"] == ["Quartz"]
    result = r.route_question("Which platform does ProposalOnly use?", slots, RESOLVER,
                              workspace_id="one", embedder=Model(), provider_factory=Forced)
    assert result["scope"] is None and result["route"] == "CLARIFY"
