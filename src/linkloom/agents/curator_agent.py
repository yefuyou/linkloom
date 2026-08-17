"""CuratorAgent for P4."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from linkloom.agents.base import AgentResult, AgentTask
from linkloom.tools.read_tools import read_verified_note, build_pair_signals
from linkloom.tools.tool_policy import ToolPolicyEnforcer


class CuratorAgent:
    def __init__(self, agent_id: str = "curator_agent"):
        self.agent_id = agent_id

    def execute(
        self,
        task: AgentTask,
        query: str,
        policy_enforcer: ToolPolicyEnforcer,
        reader_func: Callable[[str], Dict[str, Any]],
        curator_func: Callable[[str, str], List[Dict[str, Any]]],
    ) -> AgentResult:
        usage = {"steps": 1, "tool_calls": 0, "provider_requests": 0}
        
        try:
            note_refs = [ref for ref in task.input_refs if isinstance(ref, str)]
            if len(note_refs) < 2:
                raise ValueError("Curator requires at least two note refs")
            
            left_ref = note_refs[0]
            right_ref = note_refs[1]
            read_verified_note(left_ref, policy_enforcer, reader_func)
            usage["tool_calls"] += 1
            read_verified_note(right_ref, policy_enforcer, reader_func)
            usage["tool_calls"] += 1
            
            signals = build_pair_signals(
                left_ref=left_ref,
                right_ref=right_ref,
                policy_enforcer=policy_enforcer,
                curator_func=curator_func
            )
            usage["tool_calls"] += 1
            
            candidate_refs = [f"candidate_{i}" for i in range(len(signals))]
            
            return AgentResult(
                task_id=task.task_id,
                agent_id=self.agent_id,
                status="completed",
                output_type="candidate_bundle",
                output_refs=candidate_refs,
                summary="Generated candidate signals as system observations",
                confidence=None,
                handoff=None,
                warnings=[],
                usage=usage,
                error=None,
                completed_at=datetime.now(timezone.utc).isoformat()
            )
        except Exception as e:
            return AgentResult(
                task_id=task.task_id,
                agent_id=self.agent_id,
                status="failed",
                output_type="candidate_bundle",
                output_refs=[],
                summary="Failed to curate candidates",
                confidence=None,
                handoff=None,
                warnings=[],
                usage=usage,
                error={"message": str(e), "code": "CURATION_ERROR"},
                completed_at=datetime.now(timezone.utc).isoformat()
            )
