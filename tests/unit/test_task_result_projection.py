from linkloom.ui.task_result import project_task_review


def test_task_review_keeps_invalid_model_proposal_visible_as_uncertain():
    proposal = {
        "predicate": "REVISES_PLAN",
        "proposed_verdict": "UNKNOWN",
        "evidence_status": "EVIDENCE_INVALID",
        "displayable_as_model_analysis": False,
        "missing_evidence": ["explicit replacement statement"],
        "supporting_evidence": [{"quote": "later date was discussed", "span": {"span_ref": "unbound"}}],
        "counter_evidence": [],
    }
    snapshot = {
        "reviewer": {"decision": "evidence_sufficient"},
        "task_investigation": {
            "context": {"observations": []},
            "relation_query": {"relations": [], "investigation_steps": []},
            "semantic_analysis": {"proposals": [proposal]},
        },
        "evidence": [],
    }
    preparation = {
        "discovery": {"valid_observations": [], "partial_observations": []},
        "planning": [],
        "source_mapping": {},
    }

    view = project_task_review(snapshot, preparation)

    assert len(view["proposals"]) == 1
    assert view["proposals"][0]["predicate"] == "REVISES_PLAN"
    assert view["proposals"][0]["evidence_status"] == "EVIDENCE_INVALID"
    assert view["proposals"][0]["missing_evidence"] == ["explicit replacement statement"]
    assert view["proposals"][0]["supporting_evidence"] == []
    assert not view["proposals"][0]["publishable"]
