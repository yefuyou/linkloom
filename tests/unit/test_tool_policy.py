import pytest
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer
from linkloom.tools.read_tools import read_verified_note, search_notes, validate_schema
from linkloom.runtime.models import ValidationError

def test_tool_policy_safety_limits():
    with pytest.raises(ValidationError, match="P4 ToolCallPolicy must deny network."):
        ToolCallPolicy("p4-readonly-tools-v1", "a1", [], [], 5, "allow", "deny", "deny")
    with pytest.raises(ValidationError, match="P4 ToolCallPolicy must deny vault_write."):
        ToolCallPolicy("p4-readonly-tools-v1", "a1", [], [], 5, "deny", "allow", "deny")
    with pytest.raises(ValidationError, match="P4 ToolCallPolicy must deny gold_access."):
        ToolCallPolicy("p4-readonly-tools-v1", "a1", [], [], 5, "deny", "deny", "allow")

    with pytest.raises(ValidationError, match="policy_version"):
        ToolCallPolicy("v1", "a1", [], [], 5, "deny", "deny", "deny")

    with pytest.raises(ValidationError, match="known tool"):
        ToolCallPolicy("p4-readonly-tools-v1", "a1", ["magic"], [], 5, "deny", "deny", "deny")

def test_tool_policy_enforcer():
    policy = ToolCallPolicy(
        policy_version="p4-readonly-tools-v1", agent_id="a1",
        allowed_tool_ids=["search_notes", "read_verified_note"],
        denied_tool_ids=["write_file", "read_gold"],
        max_calls=2, network="deny", vault_write="deny", gold_access="deny"
    )
    enforcer = ToolPolicyEnforcer(policy)

    # Allowed
    enforcer.authorize_call("search_notes")
    enforcer.authorize_call("read_verified_note")

    # Exceed max calls
    with pytest.raises(ValidationError, match="Exceeded max tool calls"):
        enforcer.authorize_call("search_notes")

def test_tool_policy_enforcer_denied_tools():
    policy = ToolCallPolicy(
        policy_version="p4-readonly-tools-v1", agent_id="a1",
        allowed_tool_ids=["search_notes"],
        denied_tool_ids=["read_gold"],
        max_calls=5, network="deny", vault_write="deny", gold_access="deny"
    )
    enforcer = ToolPolicyEnforcer(policy)

    with pytest.raises(ValidationError, match="core safety"):
        enforcer.authorize_call("read_gold")
        
    with pytest.raises(ValidationError, match="not in allowed list"):
        enforcer.authorize_call("unknown_tool")
        
    with pytest.raises(ValidationError, match="violates P4 core safety limits"):
        enforcer.authorize_call("write_file")


def test_typed_read_tools_validate_inputs_before_injected_callbacks():
    policy = ToolCallPolicy(
        policy_version="p4-readonly-tools-v1",
        agent_id="retrieval_agent",
        allowed_tool_ids=["search_notes", "read_verified_note", "validate_schema"],
        denied_tool_ids=["write_file", "rename_file", "read_gold", "raw_filesystem"],
        max_calls=5,
        network="deny",
        vault_write="deny",
        gold_access="deny",
    )
    enforcer = ToolPolicyEnforcer(policy)

    result = search_notes(
        "Scanner",
        {"index_sha256": "a" * 64},
        2,
        enforcer,
        lambda query, source, limit: [{"query": query, "limit": limit}],
    )
    assert result == [{"query": "Scanner", "limit": 2}]

    with pytest.raises(ValidationError, match="verified relative note reference"):
        read_verified_note("C:/vault/secret.md", enforcer, lambda ref: {"ref": ref})

    with pytest.raises(ValidationError, match="Gold or expected"):
        validate_schema(
            "review",
            {"expected": "should-not-enter-inference"},
            enforcer,
            lambda result_type, payload: {"valid": True},
        )

    assert enforcer.call_count == 1
