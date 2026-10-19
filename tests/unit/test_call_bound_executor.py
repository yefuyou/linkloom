from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolDefinition
from linkloom.tools.registry import CallBoundExecutor, ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


def test_call_bound_executor_sees_identity_only_after_schema_policy_and_pending_ledger():
    registry = ToolRegistry()
    runtime = ToolRuntime(registry)
    observed = []
    def execute(call):
        assert runtime.ledger.get(call.call_id).status == "pending"
        observed.append(call)
        return {"outer_call_id": call.call_id}
    registry.register(ToolDefinition("inspect_task_context", "1", "test identity binding",
        {"type": "object", "required": ["workspace_id"], "properties": {
            "workspace_id": {"type": "string", "enum": ["authorized"]}}, "additionalProperties": False},
        {"type": "object"}), CallBoundExecutor(execute))
    policy = ToolPolicyEnforcer(ToolCallPolicy("p4-readonly-tools-v1", "retrieval_agent",
        ["inspect_task_context"], [], 1, "deny", "deny", "deny"))
    bad = runtime.execute(ToolCall("bad", "inspect_task_context", {"workspace_id": "foreign"}), policy)
    assert bad.error.code == "TOOL_INVALID_ARGUMENTS" and not observed
    call = ToolCall("good", "inspect_task_context", {"workspace_id": "authorized"},
                    run_id="outer-run", task_id="retrieval-task", agent_id="retrieval_agent", sequence=3)
    assert runtime.execute(call, policy).value == {"outer_call_id": "good"}
    assert observed == [call] and policy.call_count == 1
