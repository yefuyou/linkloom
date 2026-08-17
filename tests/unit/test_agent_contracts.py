import pytest
from linkloom.agents.base import AgentIdentity, AgentTask, AgentResult, HandoffRequest, ReviewDecision
from linkloom.runtime.models import ValidationError

def test_agent_identity_rejects_gold_and_write():
    with pytest.raises(ValidationError, match="cannot read gold"):
        AgentIdentity("a", "b", "c", [], [], can_read_gold=True, can_write_vault=False, max_steps=1)
        
    with pytest.raises(ValidationError, match="cannot write vault"):
        AgentIdentity("a", "b", "c", [], [], can_read_gold=False, can_write_vault=True, max_steps=1)
        
    AgentIdentity("a", "b", "c", [], [], can_read_gold=False, can_write_vault=False, max_steps=1)

    with pytest.raises(ValidationError, match="agent_id"):
        AgentIdentity("", "b", "c", [], [], can_read_gold=False, can_write_vault=False, max_steps=1)

    with pytest.raises(ValidationError, match="allowed_workflows"):
        AgentIdentity("a", "b", "c", [], ["organize"], can_read_gold=False, can_write_vault=False, max_steps=1)

def test_agent_task_valid():
    task = AgentTask(
        task_id="t1", run_id="r1", parent_task_id=None,
        parent_agent_id="coordinator", agent_id="retrieval_agent",
        workflow="ask", input_refs=["req1"], allowed_tool_ids=["search_notes"],
        max_steps=3, deadline_ms=5000, status="queued", attempt=0,
        created_at="2026"
    )
    assert task.status == "queued"

    with pytest.raises(ValidationError, match="Invalid AgentTask status"):
        AgentTask(
            task_id="t1", run_id="r1", parent_task_id=None,
            parent_agent_id="coordinator", agent_id="retrieval_agent",
            workflow="ask", input_refs=["req1"], allowed_tool_ids=["search_notes"],
            max_steps=3, deadline_ms=5000, status="unknown_status", attempt=0,
            created_at="2026"
        )

    with pytest.raises(ValidationError, match="relative reference"):
        AgentTask(
            task_id="t1", run_id="r1", parent_task_id=None,
            parent_agent_id="coordinator", agent_id="retrieval_agent",
            workflow="ask", input_refs=["C:/vault/secret.md"], allowed_tool_ids=["search_notes"],
            max_steps=3, deadline_ms=5000, status="queued", attempt=0,
            created_at="2026"
        )

    with pytest.raises(ValidationError, match="forbidden"):
        AgentTask(
            task_id="t1", run_id="r1", parent_task_id=None,
            parent_agent_id="coordinator", agent_id="retrieval_agent",
            workflow="ask", input_refs=["read_gold"], allowed_tool_ids=["search_notes"],
            max_steps=3, deadline_ms=5000, status="queued", attempt=0,
            created_at="2026"
        )

def test_agent_result_requirements():
    with pytest.raises(ValidationError, match="Completed AgentResult must have output_refs or handoff"):
        AgentResult("t1", "a1", "completed", "bundle", [], "ok", None, None, [], {}, None, "2026")
        
    with pytest.raises(ValidationError, match="Failed/rejected AgentResult must have error"):
        AgentResult("t1", "a1", "failed", "bundle", [], "fail", None, None, [], {}, None, "2026")
        
    # Valid completed
    AgentResult("t1", "a1", "completed", "bundle", ["ref1"], "ok", None, None, [], {"steps": 1, "tool_calls": 0, "provider_requests": 0}, None, "2026")
    
    # Valid failed
    AgentResult("t1", "a1", "failed", "bundle", [], "fail", None, None, [], {"steps": 0, "tool_calls": 0, "provider_requests": 0}, {"code": "X"}, "2026")

    with pytest.raises(ValidationError, match="confidence"):
        AgentResult("t1", "a1", "completed", "bundle", ["ref1"], "ok", 1.1, None, [], {"steps": 1, "tool_calls": 0, "provider_requests": 0}, None, "2026")

    with pytest.raises(ValidationError, match="usage"):
        AgentResult("t1", "a1", "completed", "bundle", ["ref1"], "ok", None, None, [], {"steps": -1, "tool_calls": 0, "provider_requests": 0}, None, "2026")

    with pytest.raises(ValidationError, match="status"):
        AgentResult("t1", "a1", "unknown", "bundle", [], "bad", None, None, [], {}, {"code": "X"}, "2026")

def test_handoff_request_valid():
    handoff = HandoffRequest(
        handoff_id="h1", from_agent_id="a1", to_agent_id="a2", reason_code="reason",
        task_id="t2", input_refs=[], requested_output_type="bundle",
        allowed_tool_ids=[], max_steps=1, status="requested", created_at="2026"
    )
    assert handoff.to_agent_id == "a2"

    with pytest.raises(ValidationError, match="status"):
        HandoffRequest(
            handoff_id="h1", from_agent_id="a1", to_agent_id="a2", reason_code="reason",
            task_id="t2", input_refs=[], requested_output_type="bundle",
            allowed_tool_ids=[], max_steps=1, status="unknown", created_at="2026"
        )

def test_review_decision_valid():
    with pytest.raises(ValidationError, match="Invalid ReviewDecision decision"):
        ReviewDecision("r1", "reviewer", ["ref1"], "approved", [], False, "reason", "2026")
        
    decision = ReviewDecision("r1", "reviewer", ["ref1"], "evidence_sufficient", [], False, "reason", "2026")
    assert decision.decision == "evidence_sufficient"
    assert not decision.human_approval_required
