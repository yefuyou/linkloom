"""Independent recall and final validation, including deliberately broken rules."""
from copy import deepcopy
import json
import numpy as np
import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui import scope_discovery as scope, question_routing as routing
from linkloom.ui.product import ProductApplicationError
from scripts.eval_routing import routing_metrics

RESOLVER = FrozenRelationResolver(("uses platform", "has allocation"), aliases={"平台": "uses platform"})
SLOTS = [dict(subject="Launch", relation="uses platform", values=["Quartz"], slot_id="platform", workspace_id="one", reviewed=True),
         dict(subject="Launch", relation="has allocation", values=["9000"], slot_id="allocation", workspace_id="one", reviewed=True)]

class Model:
    def encode(self, texts):
        return np.asarray([[1., 0.] if "platform" in t or "Quartz" in t or "平台" in t else [0., 1.] for t in texts])


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("query,gold", [("Which platform is Launch using?", "platform"),
    ("Launch 当前采用哪个平台？", "platform"), ("Quartz 还在用吗？", "platform"),
    ("What is Launch's allocation?", "allocation")])
@pytest.mark.parametrize("failure", ["normal", "UNKNOWN", "wrong_subject", "wrong_topic", "exception"])
def test_original_pool_and_scores_survive_all_rule_failures(monkeypatch, query, gold, embedded, failure):
    model = Model() if embedded else None
    snapshot = scope.workspace_snapshot(SLOTS, RESOLVER, "one")
    original = deepcopy(SLOTS)
    config = scope.RetrievalConfig(model)
    expected = scope.retrieve_raw(query, snapshot, config)
    assert gold in {c["slot_id"] for c in expected}
    if failure != "normal":
        def broken(*args, **kwargs):
            if failure == "exception":
                raise ValueError("injected parser failure")
            return {"subjects": ["Other"] if failure == "wrong_subject" else [],
                    "topics": ["Other"] if failure == "wrong_topic" else [],
                    "values": [], "unresolved": []}
        monkeypatch.setattr(scope, "parse_query_clues", broken)
        def intent(*args, **kwargs):
            if failure == "exception":
                raise ValueError("injected intent failure")
            return {"intent": "UNKNOWN" if failure == "UNKNOWN" else "INVESTIGATION",
                    "supported": False, "code": "INJECTED", "reason": "injected", "decided_by": "rule"}
        monkeypatch.setattr(routing, "classify_task", intent)
    result = routing.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=model)
    # Full candidates, ordering, metadata and float scores are exactly equal.
    assert result["discovery"]["raw_candidates"] == expected
    assert gold in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}
    assert result["route"] != "NO_MATCH"
    assert SLOTS == original


def test_raw_retrieval_never_invokes_a_parser(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("raw retrieval called a rule parser")
    monkeypatch.setattr(scope, "_query_bindings", forbidden)
    monkeypatch.setattr(scope, "parse_query_clues", forbidden)
    monkeypatch.setattr(routing, "classify_task", forbidden)
    hits = scope.retrieve_raw("Which platform now?", scope.workspace_snapshot(SLOTS, RESOLVER, "one"), scope.RetrievalConfig(None))
    assert hits[0]["slot_id"] == "platform"


def test_ghost_cannot_be_bound_to_launch_even_if_selector_proposes_it(monkeypatch):
    class Forced:
        def complete(self, request):
            return ModelResponse(action=ModelAction.final('{"intent":"CURRENT","slot_id":"platform","as_of":null}'))
    query = "Which platform does Ghost use?"
    discovery = scope.discover_scope(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    choice = routing._select(Forced(), query, discovery["candidates"], routing.classify_task(query), None)
    assert choice["slot_id"] == "platform"
    # Inject a previously accepted bad selection to exercise the final guard,
    # in addition to the ordinary pre-provider guard.
    discovery.update(scope={"subject": "Launch", "relation": "uses platform"}, decided_by="llm")
    monkeypatch.setattr(routing, "discover_scope", lambda *a, **kw: deepcopy(discovery))
    result = routing.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None, provider_factory=Forced)
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


def test_retrieval_failure_is_an_existing_error_not_no_match(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("injected index failure")
    monkeypatch.setattr(scope.BM25Index, "search", fail)
    with pytest.raises(ProductApplicationError) as error:
        routing.route_question("Which platform now?", SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert error.value.code == "RETRIEVAL_FAILED" and error.value.status == 503


def test_weak_candidates_without_provider_clarify_instead_of_disappearing():
    class Weak:
        def encode(self, texts):
            return np.asarray([[1., 0., 0.] if "uses platform" in t else [0., 1., 0.] if "has allocation" in t else [.1, .1, .98] for t in texts])
    result = routing.route_question("Which substrate?", SLOTS, RESOLVER, workspace_id="one", embedder=Weak())
    # The final contract drops semantic hits below the recall floor entirely.
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert result["discovery"]["raw_candidates"] == result["discovery"]["candidates"] == []


def test_new_metrics_use_raw_pool_and_do_not_count_clarification_as_correct_automatic():
    def row(expected, route, actual, recalled, correct):
        return {"expected_slot": expected, "actual": {"route": route, "slot": actual, "raw_candidates": recalled},
                "slot_correct": actual == expected, "route_slot_date_correct": correct}
    rows = [row("a", "CURRENT", "a", ["a"], True), row("a", "CURRENT", "b", ["a", "b"], False),
            row("a", "CLARIFY", None, ["a"], False), row("a", "NO_MATCH", None, [], False),
            row(None, "NO_MATCH", None, [], True)]
    m = routing_metrics(rows)
    assert m["candidate_recall"] == {"count": 3, "total": 4, "rate": .75}
    assert m["automatic_accuracy"] == {"count": 1, "total": 2, "rate": .5}
    assert m["wrong_hit_rate"] == {"count": 1, "total": 2, "rate": .5}
    assert m["false_rejection_rate"] == {"count": 1, "total": 4, "rate": .25}
    assert m["clarify_rate"] == {"count": 1, "total": 5, "rate": .2}


@pytest.mark.parametrize("value", [None, [], "UNKNOWN", {"unresolved": [None]}, {"intent": []}])
def test_malformed_rule_outputs_keep_original_candidates(monkeypatch, value):
    query = "Which platform is Launch using?"
    raw = scope.retrieve_raw(query, scope.workspace_snapshot(SLOTS, RESOLVER, "one"), scope.RetrievalConfig(None))
    monkeypatch.setattr(scope, "parse_query_clues", lambda *a: deepcopy(value))
    monkeypatch.setattr(routing, "classify_task", lambda *a: deepcopy(value))
    result = routing.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["discovery"]["raw_candidates"] == raw
    assert "platform" in {c["slot_id"] for c in raw}
    # Intent is only a clue: a malformed intent must not veto an otherwise
    # globally confident, independently verified slot.
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Launch"


@pytest.mark.parametrize("operator", ["currently", "being", "still", "not"])
def test_passive_operators_are_not_unknown_proper_names(operator):
    query = f"Which platform is {operator} used?"
    result = routing.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert not result["discovery"]["entity_check"]["hard_veto"]
    assert result["route"] != "NO_MATCH"
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


def test_parser_cannot_mutate_raw_snapshot_or_candidates(monkeypatch):
    query = "Which platform is Launch using?"
    raw = scope.retrieve_raw(query, scope.workspace_snapshot(SLOTS, RESOLVER, "one"), scope.RetrievalConfig(None))
    def mutate(query, snapshot):
        snapshot.slots[0]["values"].append("MUTATED")
        snapshot.slots[0]["subject"] = "Other"
        return {"unresolved": []}
    monkeypatch.setattr(scope, "parse_query_clues", mutate)
    result = routing.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["discovery"]["raw_candidates"] == raw
    assert result["scope"]["subject"] == "Launch"


@pytest.mark.parametrize("query", ["Which platform does Ghost Harbor currently use?",
    "Which platform is Ghost Harbor using?", "What is Ghost Harbor's platform?"])
def test_multiword_unknown_names_cannot_be_selected_by_llm(query):
    class Forced:
        def complete(self, request):
            return ModelResponse(action=ModelAction.final('{"intent":"CURRENT","slot_id":"platform","as_of":null}'))
    result = routing.route_question(query, SLOTS, RESOLVER, workspace_id="one", embedder=None, provider_factory=Forced)
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert result["discovery"]["entity_check"]["hard_veto"]
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


def test_lowercase_common_noun_phrase_is_not_a_proper_name():
    result = routing.route_question("Which platform does the team use?", SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert not result["discovery"]["entity_check"]["hard_veto"]
    assert result["route"] == "CLARIFY" and result["scope"] is None



def test_http_service_does_not_parse_intent_before_original_recall(monkeypatch):
    from threading import RLock
    from types import SimpleNamespace
    from linkloom.ui import unified_ask
    app = SimpleNamespace(repository=SimpleNamespace(database_path=":memory:"),
                          _workspace_id=lambda: "one", _store_lock=RLock(), relation_resolver=RESOLVER)
    service = unified_ask.UnifiedAskService(app, lambda: None, slot_embedder=None)
    monkeypatch.setattr(service, "_trusted_slots", lambda *a: deepcopy([*SLOTS, dict(SLOTS[0], subject="Willow", slot_id="willow")]))
    def forbidden(*a):
        raise ValueError("injected intent failure")
    monkeypatch.setattr(unified_ask, "classify_task", forbidden)
    monkeypatch.setattr(routing, "classify_task", forbidden)
    result, status = service.ask({"workspace_id": "one", "query": "Which platform are we using?"})
    assert status == 200 and result["route"]["route"] == "CLARIFY"
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}
    assert not result["conclusions"]



def test_wrong_intent_cannot_turn_an_imperative_into_a_state_answer(monkeypatch):
    monkeypatch.setattr(routing, "classify_task", lambda *a: {"intent": "CURRENT", "supported": True})
    result = routing.route_question("Replace the platform", SLOTS, RESOLVER, workspace_id="one", embedder=None)
    assert result["route"] == "CLARIFY" and result["scope"] is None
    assert "platform" in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}
