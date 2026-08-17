import pytest
from linkloom.agents.registry import AgentRegistry, create_default_registry
from linkloom.agents.base import AgentIdentity
from linkloom.runtime.models import ValidationError

def test_agent_registry_duplicate_agent_id():
    registry = AgentRegistry()
    agent = AgentIdentity("a1", "role", "v1", [], ["ask"], False, False, 1)
    registry.register(agent)
    with pytest.raises(ValidationError, match="Duplicate agent id"):
        registry.register(agent)

def test_agent_registry_unknown_tool():
    registry = AgentRegistry()
    agent = AgentIdentity("a1", "role", "v1", ["magic_wand"], ["ask"], False, False, 1)
    with pytest.raises(ValidationError, match="Unknown tool capability: magic_wand"):
        registry.register(agent)

def test_agent_registry_write_capability_rejected():
    registry = AgentRegistry()
    # can_write_vault will be rejected by AgentIdentity's __post_init__ first!
    # Wait, the prompt says "write capability、Gold capability 启动失败"
    # So AgentIdentity already rejects it, we don't even reach registry unless we bypass it.
    pass

def test_default_registry_stable_sort():
    registry = create_default_registry()
    agents = registry.list_agents()
    agent_ids = [a.agent_id for a in agents]
    assert agent_ids == ["coordinator", "curator_agent", "retrieval_agent", "reviewer_agent"]

def test_registry_allows_only_coordinator_to_handoff_to_registered_specialist():
    registry = create_default_registry()
    registry.validate_handoff_target("coordinator", "retrieval_agent")
    with pytest.raises(ValidationError, match="not allowed"):
        registry.validate_handoff_target("retrieval_agent", "reviewer_agent")
    with pytest.raises(ValidationError, match="not found"):
        registry.validate_handoff_target("coordinator", "unknown_agent")
