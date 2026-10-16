"""Synthetic HTTP acceptance: model plan admission plus the real Runtime."""
from copy import deepcopy

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from run_bounded_multihop import SourceOnlyController
from unified_investigation_fixture import UnifiedInvestigationFixture, RELATIONS, TWO
from unified_ask_fixture import request_json, poll_product_ask


QUERY = "想找 Copper Meadow 这个代号对应项目的牵头人，并确认这位负责人向谁直接汇报。"


def plan_for(query):
    result = plan_relation_investigation("Who owns the project behind Copper Meadow and who leads that owner?").to_dict()
    result["goal"] = query
    return result


class PlanningModel(FakeModelAdapter):
    def __init__(self, *, invalid=False, repair_invalid=False, decline=False):
        super().__init__([SourceOnlyController()] * 12)
        self.proposals = self.repairs = 0
        self.invalid, self.repair_invalid, self.decline = invalid, repair_invalid, decline

    def propose_task_plan(self, query, **kwargs):
        self.proposals += 1
        value = plan_for(query)
        if self.decline:
            value["subgoals"] = []
        elif self.invalid:
            value["subgoals"][-1]["question"] = "Who is the direct leader of Prefilled Person?"
        return value

    def repair_task_plan(self, query, proposal, validation_error, **kwargs):
        self.repairs += 1
        assert validation_error["code"]
        return deepcopy(proposal) if self.repair_invalid else plan_for(query)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    fixture = UnifiedInvestigationFixture(tmp_path / "adaptive.sqlite")
    fixture.configure_agent()
    fixture.service.adaptive_planning = True
    fixture.select_workspace(RELATIONS)
    fixture.model = PlanningModel()
    fixture.factory_calls = []
    def factory(query):
        fixture.factory_calls.append(query)
        return fixture.model
    fixture.service.model_factory = factory
    yield fixture
    fixture.stop()


def ask(demo, query=QUERY, **kwargs):
    return request_json(demo.base_url, "/api/product/ask", method="POST", data={
        "query": query, "workspace_id": demo.app._workspace_id(), **kwargs})


def final(demo, body):
    return poll_product_ask(demo, body["result"]["agent_run_id"], demo.app._workspace_id())[1]["result"]


def test_new_wording_passes_model_plan_runtime_and_reviewer_through_http(demo):
    assert plan_relation_investigation(QUERY) is None
    status, body = ask(demo)
    assert status == 202, body
    result = final(demo, body)
    assert result["status"] == "COMPLETED", result
    assert result["source_investigation"]["answer"] == "Owen Brook"
    assert result["reviewer"]["performed"] and result["reviewer"]["accepted"]
    assert result["route"]["planning"]["origin"] == "model"
    assert result["metrics"]["planning_provider_requests"] == 1
    assert demo.model.proposals == 1 and not demo.model.repairs
    assert all(e["verified"] and e["source_id"] and e["source_version"] for e in result["evidence"])
    assert result["metrics"]["search_count"] >= 3 and result["metrics"]["read_count"] >= 3


def test_existing_rules_skip_model_planning_and_feature_defaults_off(demo):
    status, body = ask(demo, TWO)
    assert status == 202
    result = final(demo, body)
    assert result["status"] == "COMPLETED", result
    assert demo.model.proposals == 0
    demo.service.adaptive_planning = True
    demo.select_workspace("Atlas")
    status, body = ask(demo, "Which supplier is Launch using now?")
    assert status == 200 and body["result"]["path"] == "DETERMINISTIC", body
    assert demo.model.proposals == 0
    demo.service.adaptive_planning = False
    status, body = ask(demo)
    assert status == 200 and body["result"]["error"]["code"] == "UNSUPPORTED_QUERY"
    assert demo.model.proposals == 0


def test_permission_date_and_scope_rejection_precede_model_factory(demo, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setattr("linkloom.ui.unified_ask.discover_scope", lambda *args, **kwargs: {
            "status": "AMBIGUOUS", "scope": None, "candidates": [], "candidate_count": 2,
            "reason": "Conflicting reviewed identities require clarification.", "basis": []})
        status, body = ask(demo, allow_external_provider=True)
        assert status == 200 and body["result"]["error"]["code"] == "DECISION_SCOPE_AMBIGUOUS", body
        assert demo.factory_calls == []
    demo.service.external_provider = True
    assert ask(demo)[0] == 403
    for changes in ({"subject": "Launch", "relation": "uses supplier"},
                    {"as_of": "2026-10-06T12:00:00Z"}):
        assert ask(demo, allow_external_provider=True, **changes)[1]["result"]["status"] == "NEEDS_SCOPE"
    assert demo.factory_calls == []


def test_one_repair_is_revalidated_and_bad_plans_never_reach_tools(demo):
    demo.model = PlanningModel(invalid=True)
    _, body = ask(demo)
    assert final(demo, body)["status"] == "COMPLETED"
    assert demo.model.proposals == demo.model.repairs == 1
    demo.model = PlanningModel(invalid=True, repair_invalid=True)
    _, body = ask(demo)
    rejected = final(demo, body)
    assert rejected["status"] == "FAILED" and rejected["error"]["code"] == "MODEL_PLAN_REJECTED"
    assert demo.model.proposals == demo.model.repairs == 1
    assert demo.model.call_count == 0 and rejected["conclusions"] == []


def test_model_decline_cannot_count_as_valid_or_trigger_repair(demo):
    demo.model = PlanningModel(decline=True)
    _, body = ask(demo)
    result = final(demo, body)
    assert result["error"]["code"] == "MODEL_PLAN_DECLINED" and result["status"] == "NEEDS_SCOPE"
    assert demo.model.proposals == 1 and demo.model.repairs == demo.model.call_count == 0
    from linkloom.runtime.errors import ProviderOutcomeUnknownError
    def uncertain(*args, **kwargs):
        raise ProviderOutcomeUnknownError()
    demo.model = PlanningModel()
    demo.model.propose_task_plan = uncertain
    _, body = ask(demo)
    interrupted = final(demo, body)
    assert interrupted["status"] == "PROVIDER_UNCERTAIN", interrupted
    assert interrupted["error"]["code"] == "MODEL_PLAN_OUTCOME_UNKNOWN"
    assert not interrupted["reviewer"]["accepted"] and demo.model.call_count == 0
