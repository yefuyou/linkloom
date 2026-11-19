"""Routing safety and provider contracts independent of the 60 development cases."""
from copy import deepcopy
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.question_routing import classify_task, route_question
from linkloom.ui.scope_discovery import decision_slot_id, rank_decision_slots, resolve_query_date
from linkloom.ui import unified_ask

RESOLVER = FrozenRelationResolver(("uses platform", "has allocation"),
    aliases={"平台": "uses platform", "platform": "uses platform", "额度": "has allocation"})


def slot(subject="Juniper", *, workspace="one", reviewed=True, relation="uses platform", value="Quartz"):
    return dict(workspace_id=workspace, reviewed=reviewed, subject=subject, relation=relation,
                values=[value], current_value=value, slot_id=decision_slot_id(subject, relation))


class Selector:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        text = self.answer(request) if callable(self.answer) else self.answer
        if isinstance(text, BaseException):
            raise text
        return ModelResponse(action=ModelAction.final(text))


def ambiguous_slots():
    return [slot("Juniper"), slot("Willow", value="Jasper")]


def test_workspace_and_review_filter_run_before_indexing_and_embedding():
    current, foreign = slot(), slot("Support", workspace="other", value="PRIVATE")
    pending = slot("Pending", reviewed=False, value="UNAPPROVED")
    class Embedder:
        def __init__(self):
            self.texts = []
        def encode(self, texts):
            import numpy as np
            self.texts.extend(texts)
            return np.array([[1.0, 0.0] for _ in texts])
    model = Embedder()
    inputs = [foreign, pending, current]
    original = deepcopy(inputs)
    result = rank_decision_slots("Which platform are we using now?", inputs, RESOLVER,
                                 workspace_id="one", embedder=model)
    assert {item["subject"] for item in result} == {"Juniper"}
    assert all(item["workspace_id"] == "one" and item["reviewed"] is True for item in result)
    assert not any(word in " ".join(model.texts) for word in ("PRIVATE", "UNAPPROVED", "Support"))
    assert inputs == original


@pytest.mark.parametrize("question", [
    "Which platform does Support currently use?",
    "Which platform does support use?",
    "Which platform does project 456 use?",
    "Which platform does Juniper or Stranger use?",
    "陌生项目当前采用哪个平台？",
    "Why did Juniper switch from Quartz to Unrecorded?",
    "What is Support's current platform?",
    "Which platform has Support chosen?",
    "陌生项目的平台是什么？",
    "陌生项目当前是什么平台？",
    "Support 的平台是什么？",
    "What platform is Stranger on?",
    "Which platform is Juniper using with Stranger?",
    "Which platform is Juniper using with stranger?",
])
def test_unknown_or_foreign_explicit_binding_never_borrows_a_known_slot(question):
    provider = Selector('{"intent":"CURRENT","slot_id":"forged","as_of":null}')
    result = route_question(question, [slot(), slot("Support", workspace="other")], RESOLVER,
                            workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "NO_MATCH"
    assert result["scope"] is None and result["decided_by"] == "fallback"
    assert result["discovery"]["status"] == "NO_MATCH" and result["discovery"]["scope"] is None


@pytest.mark.parametrize("question,unknown", [
    ("Which platform does Support currently use?", "Support"),
    ("Which platform does project 456 use?", "project 456"),
    ("Which platform is Juniper using with Stranger?", "Stranger"),
    ("陌生项目当前采用哪个平台？", "陌生项目"),
])
@pytest.mark.parametrize("mentions", ["unknown", "omitted", "none"])
def test_a_model_reply_never_binds_a_name_the_catalogue_does_not_know(question, unknown, mentions):
    # The model may be asked, but whatever it replies, the unknown or other-workspace name ends in NO_MATCH.
    slots = [slot(), slot("Support", workspace="other")]
    chosen = dict(intent="CURRENT", slot_id=slots[0]["slot_id"], as_of=None)
    if mentions == "unknown":
        chosen["entity_mentions"] = [unknown]
    elif mentions == "omitted":
        chosen["entity_mentions"] = ["platform"] if "platform" in question else []
    provider = Selector(json.dumps(chosen))
    result = route_question(question, slots, RESOLVER, workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "NO_MATCH" and result["scope"] is None and result["decided_by"] == "fallback"
    assert result["discovery"]["scope"] is None


def test_unrecorded_topic_qualifier_does_not_block_a_named_subject():
    # "支付" is not a recorded topic word. The named project and recorded relation still anchor the slot;
    # the answer shows the recorded slot and its sources instead of a rule-based refusal.
    result = route_question("Juniper 现在采用哪家支付平台？", [slot(), slot("Support", workspace="other")],
                            RESOLVER, workspace_id="one")
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper"


def test_same_question_resolves_only_in_its_own_workspace():
    inputs = [slot(), slot("Support", workspace="other")]
    question = "Which platform does Support currently use?"
    # Authorized migration: original-question recall precedes entity rejection.
    hits = rank_decision_slots(question, inputs, RESOLVER, workspace_id="one")
    assert hits and {h["slot_id"] for h in hits} == {inputs[0]["slot_id"]}
    assert all(h["workspace_id"] == "one" for h in hits)
    result = route_question(question, inputs, RESOLVER, workspace_id="one")
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    hits = rank_decision_slots(question, inputs, RESOLVER, workspace_id="other")
    assert [hit["subject"] for hit in hits] == ["Support"]


@pytest.mark.parametrize("metadata", [{}, {"workspace_id": "one"}, {"reviewed": True},
                                     {"workspace_id": "one", "reviewed": False}])
def test_missing_or_unreviewed_provenance_is_not_implicitly_trusted(metadata):
    item = {"subject": "Juniper", "relation": "uses platform", "values": ["Quartz"], **metadata}
    assert rank_decision_slots("Which platform now?", [item], RESOLVER, workspace_id="one") == []


def test_valid_llm_selection_is_one_bounded_choice_over_top_five():
    inputs = ambiguous_slots()
    provider = Selector(json.dumps({"intent": "CURRENT", "slot_id": inputs[1]["slot_id"], "as_of": None}))
    result = route_question("Which platform are we using now?", inputs, RESOLVER,
                            workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Willow"
    assert result["decided_by"] == "llm" and len(provider.requests) == 1
    request = provider.requests[0]
    assert request.available_tools == [] and request.generation_options.max_output_tokens == 256
    data = json.loads(request.user_input.split("\n", 1)[1])
    assert len(data["candidates"]) == 2
    assert {item["slot_id"] for item in data["candidates"]} == {item["slot_id"] for item in inputs}


@pytest.mark.parametrize("answer", [
    '{"intent":"CURRENT","slot_id":"not-a-candidate","as_of":null}',
    '{"intent":"CURRENT","slot_id":123,"as_of":null}',
    '{"intent":"OTHER","slot_id":null,"as_of":null}',
    '{"intent":"CURRENT","slot_id":null,"as_of":null,"extra":1}',
    '{"intent":"CURRENT","intent":"AS_OF","slot_id":null,"as_of":null}',
    '{"intent":"CURRENT","slot_id":null,"as_of":"2020-01-01"}',
    '{"intent":"AS_OF","slot_id":null,"as_of":null}',
    chr(96) * 3 + 'json\n{"intent":"CURRENT","slot_id":null,"as_of":null}\n' + chr(96) * 3,
    '{"intent":"CURRENT","slot_id":null,"as_of":NaN}',
    '["CURRENT",null,null]', "not JSON", TimeoutError("private transport detail"),
])
def test_invalid_or_failed_selection_falls_back_without_exposing_provider_text(answer):
    provider = Selector(answer)
    result = route_question("Which platform are we using now?", ambiguous_slots(), RESOLVER,
                            workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "CLARIFY"
    assert result["scope"] is None and result["decided_by"] == "fallback"
    assert result["llm_attempted"] is True
    assert "private transport detail" not in json.dumps(result)


def test_top_five_membership_and_top_three_fallback():
    inputs = [slot("Project" + str(number)) for number in range(7)]
    def outside(request):
        options = json.loads(request.user_input.split("\n", 1)[1])["candidates"]
        assert len(options) == 5
        excluded = next(item["slot_id"] for item in inputs
                        if item["slot_id"] not in {option["slot_id"] for option in options})
        return json.dumps({"intent": "CURRENT", "slot_id": excluded, "as_of": None})
    provider = Selector(outside)
    result = route_question("Which platform are we using now?", inputs, RESOLVER,
                            workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "CLARIFY" and result["scope"] is None
    assert len(result["discovery"]["candidates"]) == 3


def test_explicit_subject_relation_does_not_call_optional_model():
    provider = Selector(RuntimeError("must not run"))
    result = route_question("Which platform is Juniper using?", [slot()], RESOLVER, workspace_id="one",
                            provider_factory=lambda: provider, subject="Juniper", relation="uses platform")
    assert result["route"] == "CURRENT" and result["decided_by"] == "rule"
    assert provider.requests == []


def test_model_intent_overrides_the_regex_intent_clue_on_a_rule_bound_question():
    # "changed" makes the rule intent INVESTIGATION; the model reads the negation and says CURRENT.
    question = "Which platform is Juniper using now, not the one that changed?"
    assert classify_task(question)["intent"] == "INVESTIGATION"
    provider = Selector(json.dumps(dict(intent="CURRENT", slot_id=slot()["slot_id"], as_of=None)))
    result = route_question(question, [slot()], RESOLVER, workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm" and provider.requests
    # A failed model reply is never read as "the rules were right" when the wording reads as an investigation.
    broken = Selector(RuntimeError("down"))
    result = route_question(question, [slot()], RESOLVER, workspace_id="one", provider_factory=lambda: broken)
    assert result["route"] == "CLARIFY" and result["scope"] is None and result["llm_error"] == "SELECTION_UNAVAILABLE"


def test_llm_cannot_invent_a_date():
    inputs = ambiguous_slots()
    identifier = inputs[0]["slot_id"]
    for question, answer in (
        ("Which platform now?", dict(intent="AS_OF", slot_id=identifier, as_of="2021-01-01")),
        ("Which platform on 2026-10-06?", dict(intent="AS_OF", slot_id=identifier, as_of="2026-10-07")),
    ):
        provider = Selector(json.dumps(answer))
        result = route_question(question, inputs, RESOLVER, workspace_id="one", provider_factory=lambda: provider)
        assert result["route"] == "CLARIFY" and result["decided_by"] == "fallback"


def test_model_current_intent_is_honored_when_investigation_words_misfire():
    inputs = ambiguous_slots()
    identifier = inputs[0]["slot_id"]
    question = "Which platform are we using now since it changed?"
    assert classify_task(question)["intent"] == "INVESTIGATION"
    provider = Selector(json.dumps(dict(intent="CURRENT", slot_id=identifier, as_of=None)))
    result = route_question(question, inputs, RESOLVER,
                            workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "CURRENT" and result["decided_by"] == "llm"
    assert result["scope"]["subject"] == inputs[0]["subject"]


def test_model_investigation_intent_is_honored_for_why_question():
    inputs = ambiguous_slots()
    identifier = inputs[0]["slot_id"]
    provider = Selector(json.dumps(dict(intent="INVESTIGATION", slot_id=identifier, as_of=None)))
    result = route_question("Why did the platform change?", inputs, RESOLVER,
                            workspace_id="one", provider_factory=lambda: provider)
    assert result["route"] == "INVESTIGATION" and result["decided_by"] == "llm"
    assert result["scope"]["subject"] == inputs[0]["subject"]


def test_multiple_explicit_topics_and_unresolved_dates_remain_clarifications():
    inputs = [slot(), slot(relation="has allocation", value="60000")]
    for question in ("Which platform and allocation are we using now?",
                     "Juniper 上个月采用哪个平台？", "Juniper 2026-02-30 采用哪个平台？",
                     "上个月这个项目用的是哪个平台？"):
        result = route_question(question, inputs, RESOLVER, workspace_id="one")
        assert result["route"] == "CLARIFY" and result["decided_by"] == "fallback"
        assert len(result["discovery"]["candidates"]) <= 3


def test_numeric_changes_are_not_date_ranges():
    question = "为什么 Juniper 从 60000 调到 70000？"
    assert resolve_query_date(question, None) == (None, None)
    assert classify_task(question)["intent"] == "INVESTIGATION"


GUARDS = [json.loads(line) for line in Path(__file__).with_name("relation_guard.jsonl").read_text(
    encoding="utf-8-sig").splitlines()]


@pytest.mark.parametrize("case", GUARDS, ids=lambda item: item["id"])
def test_original_relation_and_task_dispatch_never_enter_slot_router(case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy relation/task dispatch entered the decision slot router")
    app = SimpleNamespace(repository=SimpleNamespace(database_path=":memory:"),
                          _store_lock=RLock(), _workspace_id=lambda: "one")
    service = unified_ask.UnifiedAskService(app, lambda: None)
    monkeypatch.setattr(service, "_trusted_slots", forbidden)
    monkeypatch.setattr(unified_ask, "route_question", forbidden)
    # Real source dispatch and date/scope checks run. Full task execution is
    # separately covered by the existing HTTP task-domain integration test.
    if case.get("domain"):
        monkeypatch.setattr(unified_ask, "validate_context", lambda *args: None)
        def task(run, payload, *, context):
            assert context["domain"] == "tasks"
            return {"route": {"intent": "TASK_INVESTIGATION"}, "error": None}, 200
        monkeypatch.setattr(service, "_task_request", task)
    payload = {key: case[key] for key in ("query", "subject", "relation", "as_of") if key in case}
    payload["workspace_id"] = "one"
    if case.get("domain"):
        payload["investigation_context"] = {"domain": case["domain"], "snapshot_hash": "a" * 64}
    result, code = service.ask(payload)
    assert code == case["expected_http"]
    assert result["route"]["intent"] == case["expected_intent"]
    assert (result.get("error") or {}).get("code") == case["expected_code"]


@pytest.mark.parametrize("question", [
    "Why was Quartz replaced by Unrecorded?",
    "Compare Quartz and unrecorded in the platform choice.",
])
def test_unknown_latin_replacement_and_comparison_values_fail_closed(question):
    item = slot()
    item["values"] = ["石英", "青玉", "Quartz"]
    result = route_question(question, [item], RESOLVER, workspace_id="one")
    assert result["route"] == "NO_MATCH" and result["scope"] is None
    assert item["slot_id"] in {c["slot_id"] for c in result["discovery"]["raw_candidates"]}


@pytest.mark.parametrize("question", [
    "为什么石英被花岗岩替换？", "石英为什么换成花岗岩？", "比较石英和花岗岩的平台方案？",
])
def test_unrecorded_chinese_value_does_not_hide_the_recorded_value_anchor(question):
    # 石英 is recorded only in this slot, so scope is not ambiguous. The unrecorded 花岗岩 is a claim for
    # the evidence stage to report as absent; the scope step must not reject or rebind it.
    item = slot()
    item["values"] = ["石英", "青玉", "Quartz"]
    result = route_question(question, [item], RESOLVER, workspace_id="one")
    assert result["route"] == "INVESTIGATION" and result["scope"]["subject"] == "Juniper"
    other = dict(slot("Willow", value="Jasper"), values=["花岗岩"])
    result = route_question(question, [item, other], RESOLVER, workspace_id="one")
    assert result["scope"] is None and result["route"] == "CLARIFY"


def test_declared_subject_alias_is_scoped_and_consistent_with_retrieval():
    item = slot()
    item["subject_aliases"] = ["Cedar"]
    result = route_question("Which platform is Cedar using?", [item], RESOLVER, workspace_id="one")
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper"
    assert rank_decision_slots("Which platform is Cedar using?", [item], RESOLVER, workspace_id="other") == []


def test_external_selector_factory_requires_per_question_authorization(monkeypatch):
    calls = []
    inputs = ambiguous_slots()
    def factory(query):
        calls.append(query)
        return Selector(json.dumps({"intent": "CURRENT", "slot_id": inputs[1]["slot_id"], "as_of": None}))
    provider = SimpleNamespace(model_factory=factory, external_provider=True, task_only=False)
    app = SimpleNamespace(repository=SimpleNamespace(database_path=":memory:"), _store_lock=RLock(),
        _workspace_id=lambda: "one", relation_resolver=RESOLVER,
        recover_decision=lambda *args, **kwargs: {"as_of": "2026-10-28", "effective": None, "status": "NOT_FOUND"})
    service = unified_ask.UnifiedAskService(app, lambda: provider)
    monkeypatch.setattr(service, "_trusted_slots", lambda workspace: inputs)
    question = {"query": "Which platform are we using now?", "workspace_id": "one"}
    denied, code = service.ask(question)
    assert code == 200 and denied["route"]["route"] == "CLARIFY" and calls == []
    allowed, code = service.ask(dict(question, allow_external_provider=True))
    assert code == 200 and allowed["route"]["decided_by"] == "llm"
    assert allowed["scope"]["subject"] == "Willow" and len(calls) == 1


def test_grammatical_value_qualifiers_and_dated_predicates_keep_known_entities():
    item = slot()
    item["values"] = ["Quartz", "Jasper"]
    for question in ("Are we on Quartz or Jasper now?",
                     "Which platform was Juniper using on 2026-10-06?",
                     "Do the Quartz and Jasper decision sources contradict each other?"):
        result = route_question(question, [item], RESOLVER, workspace_id="one")
        assert result["route"] in {"CURRENT", "AS_OF", "INVESTIGATION"}
        assert result["scope"]["subject"] == "Juniper"


def test_partial_alias_fragment_needs_selection_instead_of_assuming_the_full_topic():
    resolver = FrozenRelationResolver(("uses artifact",), aliases={"cosmic stack": "uses artifact"})
    item = slot(relation="uses artifact")
    result = route_question("Which stack now?", [item], resolver, workspace_id="one")
    assert result["route"] == "CLARIFY" and result["scope"] is None
    assert result["discovery"]["candidates"]  # Retrieval remains available for optional LLM.


def test_known_value_comparison_is_not_rejected_for_a_time_adverb():
    item = slot()
    item["values"] = ["Quartz", "Jasper"]
    result = route_question("Are we on Quartz or Jasper now?", [item], RESOLVER, workspace_id="one")
    assert result["route"] == "CURRENT" and result["scope"]["subject"] == "Juniper"
