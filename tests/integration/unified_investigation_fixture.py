"""Offline model injection only; production imports no controller or oracle."""
from dataclasses import replace
from uuid import uuid4

from decision_discovery_fixture import DecisionDiscoveryFixture
from run_bounded_multihop import SourceOnlyController
from unified_ask_fixture import request_json, FakeTransport
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelResponse
from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.runtime.team_decision_planner import plan_relation_investigation

RELATIONS = "Relations"
TWO = "Orion 项目负责人的直属领导是谁？"
THREE = "Who owns the project behind Copper Meadow and who leads that owner?"
CONFLICT = "As of 2026-10-06, Who is the direct leader of the owner of Mariner Field?"
MISSING = "Who is the direct leader of the owner of Harbor Gap?"


class EmptyExtractionProvider:
    def complete(self, request):
        from linkloom.tools.contracts import ToolCall
        return ModelResponse(action=ModelAction.tool(ToolCall(call_id=uuid4().hex,
            tool_id=request.available_tools[0].tool_id, arguments={"claims": []})))


class UnifiedInvestigationFixture(DecisionDiscoveryFixture):
    def _seed_synthetic_data(self):
        super()._seed_synthetic_data()
        _, body = request_json(self.base_url, "/api/workspaces", method="POST", data={"name": RELATIONS})
        self.workspace_ids[RELATIONS] = body["workspace"]["workspace_id"]
        # These are ordinary source relationships, never reviewed decisions.
        self.app.extractor._provider = EmptyExtractionProvider()
        for index, text in enumerate((
            "林舟 is the accountable owner of Orion.",
            "林舟 reports directly to 陈宁.",
            "Copper Meadow is the team alias for Falcon Reach.",
            "Ada Vale is the accountable owner of Falcon Reach.",
            "Ada Vale reports directly to Owen Brook.",
            "As of 2026-10-06 Tess Avery is the accountable owner of Mariner Field.",
            "As of 2026-10-06 Tess Avery reports directly to Devon Hart.",
            "As of 2026-10-06 Tess Avery reports directly to Casey North.",
            "Riley Fox is the accountable owner of Harbor Gap.",
        )):
            self._import_and_review(name=f"relation-{index}.md",
                content="[2026-10-06T10:00:00Z] Synthetic: " + text, value="", approve=False)
        self.select_workspace("Atlas")

    def configure_agent(self, *, scenario="normal", external_provider=False, relation_max_steps=12, **kwargs):
        service = super().configure_agent(scenario=scenario, external_provider=external_provider, **kwargs)
        temporal_factory = service.model_factory
        self.relation_models = []
        service.relation_max_steps = relation_max_steps
        def factory(query):
            if plan_relation_investigation(query) is None:
                return temporal_factory(query)
            if scenario == "uncertain":
                transport = FakeTransport(scenario="uncertain")
                self.relation_models.append(transport)
                return GeminiProviderAdapter(transport)
            controller = SourceOnlyController()
            def respond(request):
                action = controller(request)
                if action.kind == "tool_call" and action.tool_call.tool_id == "search_notes":
                    action = ModelAction.tool(replace(action.tool_call,
                        arguments={**action.tool_call.arguments, "limit": 4
                            if action.tool_call.arguments["source_context"]["subgoal_id"] == "leader" else 2}))
                return action
            model = FakeModelAdapter([respond] * 12)
            self.relation_models.append(model)
            return model
        service.model_factory = factory
        return service
