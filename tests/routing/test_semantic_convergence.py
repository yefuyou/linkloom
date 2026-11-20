"""Synthetic route_question regressions: the model decides intent and slot, the host validates."""
import json

from linkloom.ui.question_routing import classify_task, route_question

from tests.routing.test_decision_routing import RESOLVER, Selector, ambiguous_slots, slot


def reply(slot_id=None, intent="CURRENT", as_of=None):
    return Selector(json.dumps(dict(intent=intent, slot_id=slot_id, as_of=as_of, entity_mentions=[])))


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
    # Rule-bound: the deterministic path answers and the selector is not called.
    assert result["route"] == "INVESTIGATION" and result["decided_by"] != "llm" and model.requests == []


def test_negated_current_question_follows_the_model_not_the_regex_clue():
    # Ambiguous candidates: the selector is genuinely needed, and its intent beats the regex clue.
    question = "Which platform are we using now, not the one it changed from?"
    assert classify_task(question)["intent"] == "INVESTIGATION"
    result = route(question, ambiguous_slots(), reply(slot()["slot_id"]))
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm"


def test_mixed_chinese_english_entity_binds():
    inputs = [slot("Juniper 项目")]
    result = route("Juniper 项目现在用哪个平台？", inputs)
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper 项目"


def test_unknown_and_known_entity_mixed_is_refused():
    result = route("Which platform is Juniper using with Stranger?", [slot()], reply("forged"))
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    # A model reply that picks the known slot, whether it lists the unknown name or not, still ends NO_MATCH.
    for mentions in (["Juniper", "Stranger"], ["Juniper"], []):
        chosen = Selector(json.dumps(dict(intent="CURRENT", slot_id=slot()["slot_id"], as_of=None,
                                          entity_mentions=mentions)))
        result = route("Which platform is Juniper using with Stranger?", [slot()], chosen)
        assert result["route"] == "NO_MATCH" and result["scope"] is None and result["decided_by"] == "fallback"


def test_ambiguous_candidates_clarify_and_never_guess():
    inputs = ambiguous_slots()
    assert route("Which platform are we using now?", inputs)["route"] == "CLARIFY"
    assert route("Which platform are we using now?", inputs, reply(None))["route"] == "CLARIFY"


def test_cross_workspace_subject_is_never_bound():
    foreign = slot("Support", workspace="other")
    provider = reply(foreign["slot_id"])
    result = route("Which platform does Support currently use?", [slot(), foreign], provider)
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    # The other workspace's slot is not even a candidate, so a reply naming it cannot bind.
    assert foreign["slot_id"] not in {item["slot_id"] for item in result["discovery"]["candidates"]}
    chosen = Selector(json.dumps(dict(intent="CURRENT", slot_id=slot()["slot_id"], as_of=None,
                                      entity_mentions=["platform"])))
    result = route("Which platform does Support currently use?", [slot(), foreign], chosen)
    assert result["route"] == "NO_MATCH" and result["scope"] is None


def test_without_router_model_the_rule_outcome_is_unchanged():
    assert route("Which platform is Juniper using now?", [slot()])["scope"]["subject"] == "Juniper"
    assert route("Which platform is Stranger using now?", [slot()])["route"] == "NO_MATCH"


def test_explicit_subject_and_relation_skip_the_model():
    provider = Selector(RuntimeError("must not run"))
    result = route("Which platform is Juniper using now?", [slot()], provider,
                   subject="Juniper", relation="uses platform")
    assert result["decided_by"] == "rule" and provider.requests == []


def test_model_slot_outside_candidates_is_rejected_and_never_rebound_by_rules():
    provider = reply("not-a-candidate")
    result = route("Which platform are we using now?", ambiguous_slots(), provider)
    assert provider.requests and result["route"] == "CLARIFY" and result["scope"] is None
    assert result["decided_by"] == "fallback" and result["llm_error"] == "NO_VALID_SINGLE_SELECTION"


def test_an_ordinary_question_the_rules_bind_makes_no_selector_call():
    # Router off: an evolution question the deterministic path can answer is answered directly.
    question = "How has Juniper's platform evolved over time?"
    for provider in (reply(slot()["slot_id"], intent="INVESTIGATION"), Selector("not json")):
        result = route(question, [slot()], provider)
        assert provider.requests == [] and result["decided_by"] != "llm"
        assert result["route"] == classify_task(question)["intent"] and result["scope"]["subject"] == "Juniper"


def test_an_explicit_scope_with_a_plain_lookup_needs_no_model():
    provider = reply(None)
    result = route("Which platform is Juniper using now?", [slot()], provider,
                   subject=slot()["subject"], relation=slot()["relation"])
    assert provider.requests == [] and result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper"


def test_an_explicit_scope_with_investigation_wording_keeps_the_scope_and_the_rule_intent():
    # The explicit scope needs no disambiguation, so a failing or abstaining selector is never called.
    for provider in (reply(None), Selector("not json")):
        result = route("Why did Juniper change its platform?", [slot()], provider,
                       subject=slot()["subject"], relation=slot()["relation"])
        assert provider.requests == [] and result["route"] == "INVESTIGATION"
        assert result["scope"] == {"subject": slot()["subject"], "relation": slot()["relation"]}


def test_an_abstaining_selector_cannot_degrade_a_rule_bound_investigation_wording():
    question = "Which platform is Juniper using now, not the one that changed?"
    assert route(question, [slot()])["route"] == "INVESTIGATION"
    provider = reply(None)
    result = route(question, [slot()], provider)
    assert provider.requests == [] and result["route"] == "INVESTIGATION" and result["scope"]["subject"] == "Juniper"
    assert result["llm_error"] is None
    # With ambiguous candidates the selector runs, and its abstention clarifies.
    provider = reply(None)
    result = route("Which platform are we using now, not the one that changed?", ambiguous_slots(), provider)
    assert provider.requests and result["route"] == "CLARIFY" and result["scope"] is None
    assert result["decided_by"] == "fallback" and result["llm_error"] == "NO_VALID_SINGLE_SELECTION"


def test_model_as_of_mismatch_is_rejected_and_never_changes_the_date():
    question = "Which platform were we using on 2024-03-01, not the one that changed?"
    provider = reply(slot()["slot_id"], intent="AS_OF", as_of="2024-03-02")
    result = route(question, ambiguous_slots(), provider)
    assert provider.requests and result["route"] == "CLARIFY" and result["scope"] is None and result["as_of"] is None
    assert result["decided_by"] != "llm" and result["llm_error"] == "NO_VALID_SINGLE_SELECTION"
    settled = reply(slot()["slot_id"], intent="AS_OF", as_of="2024-03-02")
    result = route("Which platform was Juniper using on 2024-03-01?", [slot()], settled,
                   subject=slot()["subject"], relation=slot()["relation"])
    assert settled.requests == [] and result["route"] == "AS_OF" and result["as_of"] == "2024-03-01"


def test_a_reply_without_entity_mentions_is_invalid_and_never_binds():
    provider = Selector(json.dumps(dict(intent="CURRENT", slot_id=slot()["slot_id"], as_of=None)))
    result = route("Which platform are we using now?", ambiguous_slots(), provider)
    assert provider.requests and result["route"] == "CLARIFY" and result["scope"] is None
    assert result["decided_by"] == "fallback" and result["llm_error"] == "NO_VALID_SINGLE_SELECTION"


def test_a_valid_model_choice_is_not_overridden_by_the_rule_entity_guess():
    # "Okay" is a sentence-initial capital word the grammar guess may read as a name; the model located no entity.
    provider = reply(slot()["slot_id"])
    result = route("Okay, which platform is Juniper using now?", [slot()], provider)
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm" and result["scope"]["subject"] == "Juniper"
