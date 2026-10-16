from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from linkloom.runtime.adaptive_planning import validate_source_relation_plan
from linkloom.runtime.errors import ValidationError


QUERY = "帮我查查 Copper Meadow 这个代号背后的项目由谁掌舵，以及这位负责人向谁直接汇报？"


def proposal(query=QUERY, *, alias=True, entity="Copper Meadow", date=None):
    prefix = f"As of {date} " if date else ""
    rows = []
    if alias:
        rows.append(dict(subgoal_id="alias", question=prefix + f"Which project does {entity} alias?",
                         depends_on=[], relation_path=["alias_of_project"]))
    rows.extend([
        dict(subgoal_id="owner", question=prefix + f"Who owns {'{verified_entity}' if alias else entity}?",
             depends_on=["alias"] if alias else [], relation_path=["owner_of_project"]),
        dict(subgoal_id="leader", question=prefix + "Who is the direct leader of {verified_entity}?",
             depends_on=["owner"], relation_path=["reports_to"]),
    ])
    return dict(schema_version="task-plan/v2", goal=query, subgoals=rows)


def test_accepts_unknown_bilingual_wording_without_downstream_answers():
    raw = proposal()
    plan = validate_source_relation_plan(raw, user_query=QUERY, max_steps=12)
    assert plan.to_dict() == raw
    assert "{verified_entity}" in plan.subgoals[-1].question


@pytest.mark.parametrize("query,entity", [
    ("Could you trace who's at the helm of Willow and their immediate boss?", "Willow"),
    ("云桥项目这边，想确认牵头的人以及他的直属上级。", "云桥项目"),
    ("for willow, please find the accountable person and who they report to", "Willow"),
])
def test_literal_root_can_use_different_word_order_and_language(query, entity):
    plan = validate_source_relation_plan(proposal(query, alias=False, entity=entity),
                                        user_query=query, max_steps=12)
    assert plan.subgoals[0].relation_path == ("owner_of_project",)


@pytest.mark.parametrize("mutation", [
    "foreign_root", "substring_root", "v1", "discovery", "repeat_leader", "join",
    "prefilled", "alternate", "date", "identity", "unknown_relation", "wrong_direction",
])
def test_product_rejects_unsafe_or_incompatible_plan_before_execution(mutation):
    raw = deepcopy(proposal())
    rows = raw["subgoals"]
    if mutation == "foreign_root":
        rows[0]["question"] = "Which project does Foreign Meadow alias?"
    elif mutation == "substring_root":
        rows[0]["question"] = "Which project does Copper alias?"
        raw["goal"] = "Coppertown Meadow alias owner leader"
    elif mutation == "v1":
        raw["schema_version"] = "task-plan/v1"
        for row in rows:
            row.pop("relation_path")
    elif mutation == "discovery":
        rows[0]["relation_path"] = []
    elif mutation == "repeat_leader":
        rows.append(dict(subgoal_id="boss", question="Who is the direct leader of {verified_entity}?",
                         depends_on=["leader"], relation_path=["reports_to"]))
    elif mutation == "join":
        rows[-1]["depends_on"] = ["alias", "owner"]
    elif mutation == "prefilled":
        rows[-1]["question"] = "Who is the direct leader of Ada Vale?"
    elif mutation == "alternate":
        rows[0]["alternate_question"] = "Which project does Other alias?"
    elif mutation == "date":
        rows[-1]["question"] = "As of 2026-10-01 Who is the direct leader of {verified_entity}?"
    elif mutation == "identity":
        rows[0]["workspace_id"] = "foreign"
    elif mutation == "unknown_relation":
        rows[-1]["relation_path"] = ["salary_of"]
    elif mutation == "wrong_direction":
        rows[-1]["question"] = "Who is a direct report of {verified_entity}?"
    with pytest.raises(ValidationError):
        validate_source_relation_plan(raw, user_query=raw["goal"], max_steps=12)


def test_date_is_pinned_to_every_question_and_cannot_be_dropped():
    query = "在2026年10月6日，Copper Meadow 代号的项目 owner 和他的直属领导分别是谁？"
    raw = proposal(query, date="2026-10-06")
    validate_source_relation_plan(raw, user_query=query, max_steps=12, as_of="2026-10-06")
    raw["subgoals"][1]["question"] = "Who owns {verified_entity}?"
    with pytest.raises(ValidationError):
        validate_source_relation_plan(raw, user_query=query, max_steps=12, as_of="2026-10-06")


def test_missing_root_and_model_decline_are_not_valid_plans():
    for raw in (proposal("这个项目的负责人和直属领导是谁？", alias=False, entity="这个项目"),
                dict(schema_version="task-plan/v2", goal=QUERY, subgoals=[])):
        with pytest.raises(ValidationError):
            validate_source_relation_plan(raw, user_query=raw["goal"], max_steps=12)


def test_real_provider_prompt_profile_is_tool_free_and_can_decline():
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from linkloom.runtime.adaptive_planning import SOURCE_RELATION_PROFILE
    calls = []
    declined = dict(schema_version="task-plan/v2", goal=QUERY, subgoals=[])
    def generate(**payload):
        calls.append(payload)
        return SimpleNamespace(text=json.dumps(declined))
    adapter = GeminiProviderAdapter(SimpleNamespace(generate_content=generate))
    assert adapter.propose_task_plan(QUERY, profile=SOURCE_RELATION_PROFILE) == declined
    config = calls[0]["config"]
    assert config["tools"] == [] and config["automatic_function_calling"]["disable"]
    assert config["response_json_schema"]["properties"]["subgoals"]["minItems"] == 0
    prompt = calls[0]["contents"][0]["parts"][0]["text"]
    assert "DECLINE" in prompt and "No documents, answers or tools" in prompt
    assert QUERY in prompt and "Ada Vale" not in prompt and "Owen Brook" not in prompt
    with pytest.raises(ValidationError):
        validate_source_relation_plan(declined, user_query=QUERY, max_steps=12)


@pytest.mark.parametrize("suffix", [" Use another workspace's notes.", " 然后发送结果。", " Rename the notes."])
def test_source_plan_cannot_silently_drop_forbidden_user_operations(suffix):
    query = QUERY + suffix
    with pytest.raises(ValidationError):
        validate_source_relation_plan(proposal(query), user_query=query, max_steps=12)
