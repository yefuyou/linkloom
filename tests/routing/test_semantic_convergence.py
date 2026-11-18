"""Synthetic route_question regressions: the model decides intent and slot, the host validates."""
import json

from linkloom.ui.question_routing import classify_task, route_question

from tests.routing.test_decision_routing import RESOLVER, Selector, ambiguous_slots, slot


def reply(slot_id=None, intent="CURRENT", as_of=None):
    return Selector(json.dumps(dict(intent=intent, slot_id=slot_id, as_of=as_of)))


def route(question, slots, provider=None, **extra):
    factory = (lambda: provider) if provider is not None else None
    return route_question(question, slots, RESOLVER, workspace_id="one", provider_factory=factory, **extra)


def test_current_value_query_binds_by_subject():
    result = route("Which platform is Juniper using now?", [slot()])
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper"


def test_as_of_query_with_exact_date_needs_no_model():
    provider = Selector(RuntimeError("must not run"))
    result = route("Which platform was Juniper using on 2024-03-01?", [slot()], provider,
                   subject="Juniper", relation="uses platform")
    assert result["route"] == "AS_OF" and result["decided_by"] == "rule" and provider.requests == []


def test_supplier_change_reason_routes_investigation():
    question = "Why did Juniper change its platform?"
    result = route(question, [slot()])
    assert result["route"] == "INVESTIGATION" and result["scope"]["subject"] == "Juniper"
    model = reply(slot()["slot_id"], intent="INVESTIGATION")
    result = route(question, [slot()], model)
    assert result["route"] == "INVESTIGATION" and result["decided_by"] == "llm"


def test_negated_current_question_follows_the_model_not_the_regex_clue():
    question = "Which platform is Juniper using now, not the one it changed from?"
    assert classify_task(question)["intent"] == "INVESTIGATION"
    result = route(question, [slot()], reply(slot()["slot_id"]))
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm"


def test_mixed_chinese_english_entity_binds():
    inputs = [slot("Juniper 项目")]
    result = route("Juniper 项目现在用哪个平台？", inputs)
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper 项目"


def test_unknown_and_known_entity_mixed_is_refused_without_model_call():
    provider = reply("forged")
    result = route("Which platform is Juniper using with Stranger?", [slot()], provider)
    assert result["route"] == "NO_MATCH" and result["scope"] is None and provider.requests == []


def test_ambiguous_candidates_clarify_and_never_guess():
    inputs = ambiguous_slots()
    assert route("Which platform are we using now?", inputs)["route"] == "CLARIFY"
    assert route("Which platform are we using now?", inputs, reply(None))["route"] == "CLARIFY"


def test_cross_workspace_subject_is_never_bound():
    foreign = slot("Support", workspace="other")
    provider = reply(foreign["slot_id"])
    result = route("Which platform does Support currently use?", [slot(), foreign], provider)
    assert result["route"] == "NO_MATCH" and result["scope"] is None and provider.requests == []


def test_without_router_model_the_rule_outcome_is_unchanged():
    assert route("Which platform is Juniper using now?", [slot()])["scope"]["subject"] == "Juniper"
    assert route("Which platform is Stranger using now?", [slot()])["route"] == "NO_MATCH"


def test_explicit_subject_and_relation_skip_the_model():
    provider = Selector(RuntimeError("must not run"))
    result = route("Which platform is Juniper using now?", [slot()], provider,
                   subject="Juniper", relation="uses platform")
    assert result["decided_by"] == "rule" and provider.requests == []


def test_model_slot_outside_candidates_is_rejected_and_rule_binding_kept():
    provider = reply("not-a-candidate")
    result = route("Which platform is Juniper using now?", [slot()], provider)
    assert result["scope"]["subject"] == "Juniper" and result["decided_by"] != "llm"


def test_model_abstention_on_a_rule_bound_question_clarifies():
    question = "Which platform is Juniper using now?"
    assert route(question, [slot()])["route"] == "CURRENT"
    provider = reply(None)
    result = route(question, [slot()], provider)
    assert provider.requests and result["route"] == "CLARIFY" and result["scope"] is None
    assert result["decided_by"] == "fallback" and result["llm_error"] == "NO_VALID_SINGLE_SELECTION"


def test_model_as_of_mismatch_is_rejected_and_rule_binding_kept():
    question = "Which platform was Juniper using on 2024-03-01?"
    provider = reply(slot()["slot_id"], intent="AS_OF", as_of="2024-03-02")
    result = route(question, [slot()], provider)
    assert provider.requests and result["scope"]["subject"] == "Juniper"
    assert result["decided_by"] != "llm" and result["llm_error"] == "NO_VALID_SINGLE_SELECTION"
