import pytest

from linkloom.ui.unified_ask import classify_task


@pytest.mark.parametrize(("query", "intent"), [
    ("当前正式采用什么方案？", "CURRENT"),
    ("What supplier are we currently using?", "CURRENT"),
    ("某日期正式采用什么方案？", "CURRENT"),
    ("What is the effective date of this reviewed decision?", "CURRENT"),
    ("查看审批收据和来源", "CURRENT"),
    ("为什么从 A 换成 B？", "INVESTIGATION"),
    ("Which original sources support this change?", "INVESTIGATION"),
    ("历史讨论和正式决策之间有什么区别？", "INVESTIGATION"),
    ("多份文档的信息是否冲突？", "INVESTIGATION"),
    ("哪些结论确定，哪些仍无法证实？", "INVESTIGATION"),
    ("Current decision and why was A replaced?", "INVESTIGATION"),
    ("Write a poem", "CURRENT"),
    ("Launch supplier", "CURRENT"),
])
# The approved router now has exactly three domain-neutral question types.
# Unsupported wording is handled by admission/fallback, not a fourth intent.
def test_bounded_capability_routing(query, intent):
    route = classify_task(query)
    assert route["intent"] == intent
    assert route["reason"]
    # The intent enum changed; unsupported commands/fragments must still fail admission.
    if query in {"Write a poem", "Launch supplier"}:
        assert route["supported"] is False


def test_rejected_final_keeps_the_actual_runtime_review_record():
    from linkloom.ui.backend import project_reviewer

    review = {"review_id": "review-test-1", "reviewer_agent_id": "reviewer_agent",
              "decision": "evidence_insufficient", "checks": [{"check": "grounding", "passed": False}],
              "reason": "Final evidence is insufficient", "created_at": "2026-10-14T12:00:00Z"}
    projected = project_reviewer({"result": None, "review": review})
    assert projected["performed"] is True
    assert projected["review_id"] == review["review_id"]
    assert projected["decision"] == "evidence_insufficient"
    assert projected["reason"] == review["reason"]
    assert project_reviewer({"result": None, "review": None})["performed"] is False


@pytest.mark.parametrize(("frozen_version", "evidence_count"), [("version-new", 1), ("version-old", 2)])
def test_same_quote_citations_only_merge_when_source_versions_match(frozen_version, evidence_count):
    from linkloom.ui.unified_ask import UnifiedAskService, _AskRun

    service = UnifiedAskService(None, lambda: None)
    run = _AskRun("Why did it change?", "workspace-a",
                  {"subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10"},
                  classify_task("Why did it change?"))
    evidence = {"source_id": "src_a", "source_version": "version-new", "line_start": 1,
                "line_end": 1, "quote": "Reviewed source passage", "evidence_ref": "formal-ref", "verified": True}
    view = {"status": "FOUND", "effective": {"value": "B", "decision_id": "decision-b",
            "valid_from": "2026-10-07", "evidence": [evidence], "authorization": {"receipt_id": "receipt-b"}},
            "transitions": []}
    snapshot = {"kind": "success", "run": {"id": "run-a"}, "temporal_decision": view,
                "reviewer": {"performed": True, "decision": "evidence_sufficient", "review_id": "review-a"},
                "evidence": [{"ref": "agent-ref", "id": "citation-1", "relative_path": "src_a.md",
                              "source_id": "src_a", "source_version": frozen_version,
                              "line_start": 1, "line_end": 1, "source": {"quote": evidence["quote"]}}]}
    result = service._from_snapshot(run, snapshot)
    assert len(result["evidence"]) == evidence_count
    assert result["evidence"][0]["evidence_ref"] == "formal-ref"
    assert result["evidence"][0]["source_version"] == "version-new"


def test_executed_semantic_candidate_remains_visible_when_one_proposal_is_unverified():
    from linkloom.ui.unified_ask import UnifiedAskService, _AskRun

    service = UnifiedAskService(None, lambda: None)
    run = _AskRun("Did the task change?", "workspace-a", {},
                  {"intent": "TASK_INVESTIGATION"})
    snapshot = {
        "kind": "insufficient", "run": {"id": "run-a"},
        "reviewer": {"decision": "evidence_sufficient"}, "evidence": [],
        "uncertainty": {}, "preparation": {"status": "PARTIAL"},
        "task_investigation": {
            "investigation_status": "BLOCKED",
            "semantic_analysis": {"analysis_status": "EXECUTED"},
        },
    }
    result = service._from_snapshot(run, snapshot)
    assert result["status"] == "PARTIAL"
