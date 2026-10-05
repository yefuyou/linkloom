from __future__ import annotations

from examples.langgraph_decision_agent.app import run_demo


def test_langgraph_reference_executes_tool_review_resume_and_cross_thread_store() -> None:
    result = run_demo()

    assert result["interrupted"] is True
    assert result["review_thread_id"] == "langgraph-reference-review"
    assert result["second_thread_id"] == "langgraph-reference-recall"
    assert result["resumed_state"]["status"] == "approved"
    assert result["resumed_state"]["decision"] == "Supplier B"
    assert result["resumed_state"]["evidence_refs"] == ["ev:supplier:decision-b"]
    assert result["resumed_state"]["tool_call_count"] == 1
    assert "tool.search_notes" in result["resumed_state"]["events"]
    assert result["cross_thread_memory_hint"] == "Supplier B"
