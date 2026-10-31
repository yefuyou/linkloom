"""General routing capabilities, independent of the user's held-out questions."""
import json
from unittest.mock import patch

import numpy as np
import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui import scope_discovery as scope
from linkloom.ui.question_routing import classify_task, route_question

RESOLVER = FrozenRelationResolver(("uses platform", "has allocation"))
SLOTS = [
    dict(subject="Juniper", relation="uses platform", values=["Quartz", "Jasper"],
         slot_id="platform", workspace_id="one", reviewed=True),
    dict(subject="Juniper", relation="has allocation", values=["9000"],
         slot_id="allocation", workspace_id="one", reviewed=True),
]


class SemanticModel:
    """Controlled vectors verify score fusion, not synonym memorization."""
    def __init__(self, query=(1.0, 0.02, 0.0)):
        self.query = query
        self.texts = []
    def encode(self, texts):
        self.texts.extend(texts)
        return np.asarray([
            [1, 0, 0] if "uses platform" in text else
            [0, 1, 0] if "has allocation" in text else self.query
            for text in texts
        ], dtype=np.float32)


@pytest.mark.parametrize("question", [
    "为啥没继续用？", "怎么会改成这样？", "咋就变了？", "最近换了？",
    "What made this happen?", "How come it changed?", "What changed recently?",
    "Has the selection changed?", "怎么改了？",
])
def test_colloquial_causes_and_changes_are_investigations(question):
    assert classify_task(question)["intent"] == "INVESTIGATION"


@pytest.mark.parametrize("question", [
    "最近用的是哪个？", "What is the current choice recently?", "Which one lately?",
])
def test_vague_recency_is_not_a_historical_point(question):
    assert classify_task(question)["intent"] == "CURRENT"
    assert scope.resolve_query_date(question, None) == (None, None)


@pytest.mark.parametrize("question", [
    "上个月用的是哪个？", "Which one on 2026-10-06?", "Which one yesterday?",
])
def test_specific_or_relative_date_requests_keep_as_of(question):
    assert classify_task(question)["intent"] == "AS_OF"


@pytest.mark.parametrize("question", [
    "Quartz 还在用吗？", "为啥不用 Quartz 了？", "What made Quartz change?",
    "Quartz",
])
def test_one_unique_history_value_identifies_its_slot_without_aliases(question):
    result = route_question(question, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["scope"] == {"subject": "Juniper", "relation": "uses platform"}
    assert result["decided_by"] == "slot_match"


def test_single_character_cjk_topic_inside_unspaced_question_is_retrievable():
    resolver = FrozenRelationResolver(("云", "库"))
    slots = [
        dict(subject="甲", relation=term, values=[value], workspace_id="one", reviewed=True)
        for term, value in (("云", "一号"), ("库", "二号"))
    ]
    result = route_question("云现在用哪个？", slots, resolver, workspace_id="one", embedder=None)
    assert result["scope"] == {"subject": "甲", "relation": "云"}
    assert {"云", "平", "台", "平台"} <= scope._tokens("云平台")


def test_zero_lexical_overlap_topic_reaches_embedding_and_matches():
    query = "Which substrate is Juniper using?"
    assert route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)["route"] == "NO_MATCH"
    model = SemanticModel()
    result = route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=model)
    assert result["route"] == "CURRENT" and result["scope"]["relation"] == "uses platform"
    top = result["discovery"]["candidates"][0]
    assert top["bm25_score"] is None and top["dense_score"] > scope.SLOT_DENSE_MIN_SCORE
    assert top["score"] >= scope.SLOT_MATCH_MIN_SCORE
    assert model.texts


def test_low_semantic_candidates_are_kept_and_sent_to_selector():
    model = SemanticModel(query=(0.5, 0.5, 0.7))
    requests = []
    class Selector:
        def complete(self, request):
            requests.append(request)
            assert request.generation_options.temperature is None
            options = json.loads(request.user_input.split("\n", 1)[1])["candidates"]
            assert {item["slot_id"] for item in options} == {"platform", "allocation"}
            return ModelResponse(action=ModelAction.final(json.dumps(
                {"intent": "CURRENT", "slot_id": "platform", "as_of": None})))
    result = route_question("Which substrate is Juniper using?", SLOTS, RESOLVER, workspace_id="one",
                            embedder=model, provider_factory=Selector)
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm"
    assert len(requests) == 1
    # Candidates above the recall floor but below semantic auto-match confidence
    # still reach the selector; rejected low-cosine hits do not.
    assert all(scope.SLOT_DENSE_RECALL_MIN_SCORE <= item["dense_score"] < scope.SLOT_DENSE_MIN_SCORE
               for item in result["discovery"]["candidates"])


def test_null_selection_on_low_scores_falls_back_with_candidates():
    model = SemanticModel(query=(0.5, 0.5, 0.7))
    class Selector:
        def complete(self, request):
            return ModelResponse(action=ModelAction.final(
                '{"intent":"CURRENT","slot_id":null,"as_of":null}'))
    result = route_question("Which substrate is Juniper using?", SLOTS, RESOLVER, workspace_id="one",
                            embedder=model, provider_factory=Selector)
    assert result["route"] == "CLARIFY" and result["scope"] is None
    assert result["llm_attempted"] and result["discovery"]["candidates"]


def test_default_without_provider_never_initializes_local_model(monkeypatch):
    monkeypatch.delenv("LINKLOOM_RETRIEVAL_MODE", raising=False)
    def forbidden():
        raise AssertionError("Provider-free product routing tried to load embeddings")
    monkeypatch.setattr(scope, "get_slot_embedder", forbidden)
    result = route_question("Which platform is Juniper currently using?", SLOTS, RESOLVER,
                            workspace_id="one")
    assert result["route"] == "CURRENT"
    assert result["scope"] == {"subject": "Juniper", "relation": "uses platform"}


def test_default_auto_mode_with_provider_uses_available_local_model(monkeypatch):
    monkeypatch.delenv("LINKLOOM_RETRIEVAL_MODE", raising=False)
    model = SemanticModel()
    monkeypatch.setattr(scope, "get_slot_embedder", lambda: model)
    from types import SimpleNamespace
    from linkloom.agents.model_adapter import ModelAction, ModelResponse
    choice = json.dumps({"intent": "CURRENT", "slot_id": scope.decision_slot_id(
        SLOTS[0]["subject"], SLOTS[0]["relation"]), "as_of": None})
    selector = SimpleNamespace(complete=lambda request: ModelResponse(action=ModelAction.final(choice)))
    result = route_question("Which substrate is Juniper using?", SLOTS, RESOLVER,
                            workspace_id="one", provider_factory=lambda: selector)
    assert result["route"] == "CURRENT" and model.texts


def test_explicit_bm25_mode_never_initializes_model(monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    def forbidden():
        raise AssertionError("BM25 mode tried to initialize optional model")
    monkeypatch.setattr(scope, "get_slot_embedder", forbidden)
    result = route_question("Quartz 还在用吗？", SLOTS, RESOLVER, workspace_id="one")
    assert result["scope"]["relation"] == "uses platform"


def test_missing_extra_keeps_bm25_working():
    scope.get_slot_embedder.cache_clear()
    try:
        with patch.object(scope.importlib.util, "find_spec", return_value=None):
            assert scope.get_slot_embedder() is None
    finally:
        scope.get_slot_embedder.cache_clear()


def test_missing_cache_uses_local_only_and_falls_back(monkeypatch):
    from linkloom.indexing import vector
    constructed = []
    class Unavailable:
        def __init__(self, *, local_files_only):
            constructed.append(local_files_only)
        def encode(self, texts):
            raise OSError("model is not cached")
    scope.get_slot_embedder.cache_clear()
    monkeypatch.setattr(scope.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(vector, "SentenceTransformerEmbedder", Unavailable)
    try:
        assert scope.get_slot_embedder() is None
        assert constructed == [True]
    finally:
        scope.get_slot_embedder.cache_clear()


def test_history_identity_is_not_overridden_by_semantic_similarity():
    result = route_question("Quartz 还在用吗？", SLOTS, RESOLVER, workspace_id="one",
                            embedder=SemanticModel(query=(0.0, 1.0, 0.0)))
    assert result["scope"]["relation"] == "uses platform"


def test_close_semantic_candidates_request_clarification():
    result = route_question("Which substrate is Juniper using?", SLOTS, RESOLVER, workspace_id="one",
                            embedder=SemanticModel(query=(1.0, 1.0, 0.0)))
    assert result["route"] == "CLARIFY" and result["scope"] is None


def test_foreign_subject_is_rejected_after_workspace_only_semantic_recall():
    model = SemanticModel()
    inputs = [*SLOTS, dict(SLOTS[0], subject="Stranger", workspace_id="other", slot_id="foreign")]
    result = route_question("Which substrate is Stranger using?", inputs, RESOLVER,
                            workspace_id="one", embedder=model)
    # Authorized migration: encode only local reviewed documents, then reject.
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}
    assert model.texts and not any("Stranger" in doc for doc in model.texts[:-1])


@pytest.mark.parametrize("subject", ["Stranger", "Quartz", "platform"])
def test_explicit_subject_does_not_turn_into_a_history_value_or_topic(subject):
    model = SemanticModel()
    result = route_question(f"Which platform does {subject} currently use?", SLOTS, RESOLVER,
                            workspace_id="one", embedder=model)
    # Stranger is still a hard unknown. A known value/topic in an actor role
    # remains unresolved and must not become Juniper's subject identity.
    assert result["route"] == ("NO_MATCH" if subject == "Stranger" else "CLARIFY")
    assert result["scope"] is None
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}
    assert model.texts


def test_real_gemini_adapter_accepts_selector_request_without_temperature():
    from linkloom.agents.providers import GeminiProviderAdapter
    calls = []
    class Transport:
        def generate_content(self, **payload):
            calls.append(payload)
            assert "temperature" not in payload["config"]
            return {"text": '{"intent":"CURRENT","slot_id":"platform","as_of":null}',
                    "finish_reason": "STOP"}
    inputs = [SLOTS[0], dict(SLOTS[0], subject="Willow", slot_id="willow")]
    adapter = GeminiProviderAdapter(Transport())
    result = route_question("Which platform are we using now?", inputs, RESOLVER,
                            workspace_id="one", embedder=None, provider_factory=lambda: adapter)
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm"
    assert len(calls) == 1


@pytest.mark.parametrize("selected", ["platform", None])
def test_zero_overlap_without_embeddings_still_reaches_configured_selector(selected):
    requests = []
    class Selector:
        def complete(self, request):
            requests.append(request)
            options = json.loads(request.user_input.split("\n", 1)[1])["candidates"]
            assert {item["slot_id"] for item in options} == {"platform", "allocation"}
            return ModelResponse(action=ModelAction.final(json.dumps(
                {"intent": "CURRENT", "slot_id": selected, "as_of": None})))
    result = route_question("Which substrate?", SLOTS, RESOLVER, workspace_id="one",
                            embedder=None, provider_factory=Selector)
    # Current contract: no related recall means no arbitrary slot backfill,
    # regardless of the answer a configured selector would have supplied.
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert not result["llm_attempted"] and requests == []
    assert result["discovery"]["candidates"] == []


@pytest.mark.parametrize("topic", ["未知组平台", "平台未知组"])
def test_unknown_topic_qualifiers_are_not_swallowed_by_a_known_alias(topic):
    resolver = FrozenRelationResolver(("uses platform",), aliases={"平台": "uses platform"})
    result = route_question(f"Juniper 当前采用哪家{topic}？", SLOTS, resolver,
                            workspace_id="one", embedder=None)
    # Final contract: the explicit unknown organization suffix is a hard veto.
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


@pytest.mark.parametrize("embedded", [False, True])
def test_unresolved_period_without_a_topic_still_allows_null_selection(embedded):
    calls = []
    class Selector:
        def complete(self, request):
            calls.append(request)
            return ModelResponse(action=ModelAction.final(
                '{"intent":"AS_OF","slot_id":null,"as_of":null}'))
    result = route_question("What was chosen last week?", SLOTS, RESOLVER, workspace_id="one",
                            embedder=SemanticModel(query=(0.1, 0.1, 0.98)) if embedded else None,
                            provider_factory=Selector)
    # No lexical hit, and optional semantic hits below the new recall floor.
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert not calls and not result["llm_attempted"]
    assert result["discovery"]["raw_candidates"] == result["discovery"]["candidates"] == []


def test_selector_can_identify_a_slot_but_cannot_guess_an_unresolved_date():
    class Selector:
        def complete(self, request):
            return ModelResponse(action=ModelAction.final(
                '{"intent":"AS_OF","slot_id":"platform","as_of":null}'))
    result = route_question("Which substrate last week?", SLOTS, RESOLVER, workspace_id="one",
                            embedder=SemanticModel(query=(0.5, 0.5, 0.7)), provider_factory=Selector)
    assert result["llm_attempted"] and result["route"] == "CLARIFY"
    assert result["scope"]["relation"] == "uses platform" and result["as_of"] is None
    assert result["date_error"]["code"] == "DATE_SCOPE_REQUIRED"


@pytest.mark.parametrize("query", ["Which platforms are we using?", "Which platform are we using?"])
def test_latin_topic_word_boundaries_preserve_plural_normalization(query):
    result = route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["route"] == "CURRENT" and result["scope"]["relation"] == "uses platform"
