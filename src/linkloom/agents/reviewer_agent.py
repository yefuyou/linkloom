"""ReviewerAgent for P4."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from linkloom.agents.base import ReviewDecision
from linkloom.tools.read_tools import validate_evidence, validate_schema
from linkloom.tools.tool_policy import ToolPolicyEnforcer


class ReviewerAgent:
    def __init__(self, agent_id: str = "reviewer_agent"):
        self.agent_id = agent_id

    def execute(
        self,
        review_id: str,
        subject_refs: List[str],
        subject_payloads: List[Dict[str, Any]],
        evidence_refs: List[str],
        policy_enforcer: ToolPolicyEnforcer,
        validate_ev_func: Callable[[str], Dict[str, Any]],
        validate_sch_func: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ) -> ReviewDecision:
        checks = []
        decision = "evidence_sufficient"
        reason = "All validation passed"
        
        try:
            for ev_ref in evidence_refs:
                ev_check = validate_evidence(
                    evidence_ref=ev_ref,
                    policy_enforcer=policy_enforcer,
                    validate_func=validate_ev_func
                )
                checks.append(ev_check)
                if ev_check.get("status") != "pass":
                    decision = "evidence_insufficient"
                    reason = "Evidence validation failed"
            
            if decision == "evidence_sufficient":
                for payload in subject_payloads:
                    sch_check = validate_schema(
                        result_type="subject",
                        payload=payload,
                        policy_enforcer=policy_enforcer,
                        validate_func=validate_sch_func
                    )
                    checks.append(sch_check)
                    if sch_check.get("status") != "pass":
                        decision = "schema_invalid"
                        reason = "Schema validation failed"
                        break
                        
            return ReviewDecision(
                review_id=review_id,
                reviewer_agent_id=self.agent_id,
                subject_refs=subject_refs,
                decision=decision,
                checks=checks,
                human_approval_required=False,
                reason=reason,
                created_at=datetime.now(timezone.utc).isoformat()
            )
        except Exception as e:
            return ReviewDecision(
                review_id=review_id,
                reviewer_agent_id=self.agent_id,
                subject_refs=subject_refs,
                decision="schema_invalid",
                checks=checks,
                human_approval_required=True,
                reason=f"Error: {str(e)}",
                created_at=datetime.now(timezone.utc).isoformat()
            )
