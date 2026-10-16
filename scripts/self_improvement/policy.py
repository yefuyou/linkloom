"""The only executable candidate surface: query wording and bounded hit selection."""
import re

from linkloom.retrieval_v2.subgoal_query_strategy import SubgoalQueryStrategy


class ExperimentalQueryStrategy(SubgoalQueryStrategy):
    def __init__(self, mode):
        self.mode = mode

    def build_queries(self, current_subgoal, verified_entities, *, alternate_subgoal=None):
        # Let the existing binding contract reject ambiguity before any rewrite.
        original = super().build_queries(current_subgoal, verified_entities,
                                         alternate_subgoal=alternate_subgoal)
        if self.mode == "literal" or not original:
            return original
        return tuple(re.sub(r"Who is the direct leader of (.+?)\?", r"\1 reports directly",
                            re.sub(r"Who owns (.+?)\?", r"\1 accountable owner", query))
                     for query in original)


def controller_type(base, candidate):
    class BoundedController(base):
        def __call__(self, request):
            observation = request.observation
            if observation and observation.tool_id == "search_notes" and observation.status == "ok":
                import json
                packet = json.loads(request.user_input.split("team_decision_request=", 1)[1])
                relation = packet["task_plan"]["subgoals"][self.slot]["relation_path"][0]
                pattern = {"reports_to": r"reports|leader|supervisor|manager",
                           "owner_of_project": r"owner|owns|accountable",
                           "alias_of_project": r"alias"}[relation]
                self.relevant_refs = {item["evidence_id"] for item in observation.value
                                      if re.search(pattern, item["quote"], re.I)}
            action = super().__call__(request)
            if action.kind != "tool_call":
                return action
            from linkloom.agents.model_adapter import ModelAction
            from linkloom.tools.contracts import ToolCall
            call = action.tool_call
            arguments = dict(call.arguments)
            if call.tool_id == "search_notes":
                arguments["limit"] = candidate.limit
            if call.tool_id == "read_verified_note":
                if candidate.read_mode == "first_hit":
                    self.pending.clear()
                elif candidate.read_mode == "relation_candidates":
                    # Routing only. Snippets never become evidence; all retained hits
                    # still go through read_verified_note and the original validators.
                    self.pending[:] = [ref for ref in self.pending if ref in self.relevant_refs]
            return ModelAction.tool(ToolCall(call_id=call.call_id, tool_id=call.tool_id, arguments=arguments))

        def __init__(self):
            super().__init__()
            self.relevant_refs = set()

    return BoundedController
