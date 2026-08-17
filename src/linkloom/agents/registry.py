"""Static P4 agent registry and manager-owned handoff validation."""

from typing import Dict, List, Set
from linkloom.agents.base import AgentIdentity
from linkloom.runtime.models import ValidationError
from linkloom.tools.tool_policy import KNOWN_TOOL_IDS

# Define the global allowed tools for P4
KNOWN_TOOLS: Set[str] = set(KNOWN_TOOL_IDS)

class AgentRegistry:
    def __init__(self, version: str = "p4-registry-v1"):
        self.version = version
        self._agents: Dict[str, AgentIdentity] = {}

    def register(self, agent: AgentIdentity) -> None:
        if agent.agent_id in self._agents:
            raise ValidationError(f"Duplicate agent id: {agent.agent_id}")
        
        # Validate capabilities
        for cap in agent.capabilities:
            if cap not in KNOWN_TOOLS:
                raise ValidationError(f"Unknown tool capability: {cap}")
        
        if agent.can_write_vault:
            raise ValidationError(f"Agent {agent.agent_id} cannot have write capability in P4.")
            
        if agent.can_read_gold:
            raise ValidationError(f"Agent {agent.agent_id} cannot have gold read capability in P4.")

        self._agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str) -> AgentIdentity:
        if agent_id not in self._agents:
            raise ValidationError(f"Agent not found: {agent_id}")
        return self._agents[agent_id]

    def list_agents(self) -> List[AgentIdentity]:
        """Return stable sorted list of agents."""
        return [self._agents[key] for key in sorted(self._agents.keys())]

    def validate_handoff_target(self, from_agent_id: str, to_agent_id: str) -> None:
        """Allow only explicit coordinator-to-specialist task delegation."""
        if from_agent_id not in self._agents:
            raise ValidationError(f"Agent not found: {from_agent_id}")
        if to_agent_id not in self._agents:
            raise ValidationError(f"Agent not found: {to_agent_id}")
        if from_agent_id != "coordinator" or to_agent_id == "coordinator":
            raise ValidationError(
                f"Handoff from {from_agent_id} to {to_agent_id} is not allowed; only coordinator may delegate."
            )

def create_default_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(AgentIdentity(
        agent_id="coordinator",
        role="router",
        version="p4-coordinator-v1",
        capabilities=[],
        allowed_workflows=["ask", "connect"],
        can_read_gold=False,
        can_write_vault=False,
        max_steps=12
    ))
    registry.register(AgentIdentity(
        agent_id="retrieval_agent",
        role="retrieval",
        version="p4-retrieval-v1",
        capabilities=["search_notes", "read_verified_note"],
        allowed_workflows=["ask", "connect"],
        can_read_gold=False,
        can_write_vault=False,
        max_steps=3
    ))
    registry.register(AgentIdentity(
        agent_id="curator_agent",
        role="curator",
        version="p4-curator-v1",
        capabilities=["read_verified_note", "build_pair_signals"],
        allowed_workflows=["connect"],
        can_read_gold=False,
        can_write_vault=False,
        max_steps=3
    ))
    registry.register(AgentIdentity(
        agent_id="reviewer_agent",
        role="reviewer",
        version="p4-reviewer-v1",
        capabilities=["validate_evidence", "validate_schema"],
        allowed_workflows=["ask", "connect"],
        can_read_gold=False,
        can_write_vault=False,
        max_steps=3
    ))
    return registry
