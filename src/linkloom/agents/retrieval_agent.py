"""RetrievalAgent for P4."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from linkloom.agents.base import AgentResult, AgentTask
from linkloom.tools.read_tools import search_notes, read_verified_note
from linkloom.tools.tool_policy import ToolPolicyEnforcer


class RetrievalAgent:
    def __init__(self, agent_id: str = "retrieval_agent"):
        self.agent_id = agent_id

    def execute(
        self,
        task: AgentTask,
        query: str,
        source_context: Dict[str, Any],
        policy_enforcer: ToolPolicyEnforcer,
        retrieval_func: Callable[[str, Dict[str, Any], int], List[Dict[str, Any]]],
        reader_func: Callable[[str], Dict[str, Any]],
    ) -> AgentResult:
        usage = {"steps": 1, "tool_calls": 0, "provider_requests": 0}
        
        try:
            candidates = search_notes(
                query=query,
                source_context=source_context,
                limit=10,
                policy_enforcer=policy_enforcer,
                retrieval_func=retrieval_func
            )
            usage["tool_calls"] += 1
            
            verified_evidence = []
            for candidate in candidates:
                if policy_enforcer.remaining_calls <= 0:
                    break
                ref = candidate.get("evidence_id") or candidate.get("ref") or candidate.get("note_ref")
                if not ref:
                    continue
                try:
                    doc = read_verified_note(
                        note_ref=ref,
                        policy_enforcer=policy_enforcer,
                        reader_func=reader_func
                    )
                    usage["tool_calls"] += 1
                    verified_evidence.append(doc)
                except Exception:
                    pass
            
            evidence_refs = [doc.get("evidence_id") or doc.get("ref", "") for doc in verified_evidence]
            evidence_refs = [r for r in evidence_refs if r]
            
            return AgentResult(
                task_id=task.task_id,
                agent_id=self.agent_id,
                status="completed",
                output_type="evidence_bundle",
                output_refs=evidence_refs,
                summary=f"Found {len(verified_evidence)} verified evidence",
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
                output_type="evidence_bundle",
                output_refs=[],
                summary="Failed to retrieve evidence",
                confidence=None,
                handoff=None,
                warnings=[],
                usage=usage,
                error={"message": str(e), "code": "RETRIEVAL_ERROR"},
                completed_at=datetime.now(timezone.utc).isoformat()
            )
