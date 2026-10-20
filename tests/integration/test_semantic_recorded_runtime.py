from pathlib import Path

import pytest

from linkloom.evaluation.task_semantic_replay import load_recorded_semantic_adapter
from linkloom.tools.semantic_analysis import SemanticCallPolicy
from tests.semantic_integration_support import setup, execute

ARTIFACTS = Path(__file__).resolve().parents[2] / "docs/requirements/m3-llm-semantics/artifacts"


@pytest.mark.parametrize("name", ["revision", "causality", "conflict"])
def test_frozen_real_proposal_replay_uses_actual_runtime_and_discloses_missing_sources(tmp_path, name):
    adapter = load_recorded_semantic_adapter(ARTIFACTS, name)
    engine, outer, _, _, context = setup(tmp_path, name, adapter=adapter, policy=SemanticCallPolicy("REPLAY"))
    status, state, result = execute(engine, tmp_path, context.question)
    assert status.status == "completed", (status.to_dict(), result)
    analysis = result["result"]["task_relations"]["semantic_analysis"]
    assert analysis["analysis_status"] == "EXECUTED", analysis
    assert len(adapter.requests) == 1 and outer.call_count == 4
    assert analysis["execution"]["kind"] == "REPLAY"
    assert analysis["execution"]["new_live_provider_requests"] == 0
    assert analysis["execution"]["imported_usage"]["input_tokens"] > 0
    assert all(p["semantic_status"] == "SEMANTIC_NOT_EVALUATED" and not p["publishable"] for p in analysis["proposals"])
    if name == "revision":
        assert any(p.get("comparison", {}).get("delta_calendar_days") == 4 for p in analysis["proposals"])
    elif name == "causality":
        assert [p["proposed_verdict"] for p in analysis["proposals"]] == ["SUPPORTED", "REFUTED", "UNKNOWN"]
        assert [p["coverage"]["status"] for p in analysis["proposals"]] == ["COMPLETE", "INSUFFICIENT", "INSUFFICIENT"]
        assert all(p["source_validation"]["stage"] == "EVIDENCE_VALID" for p in analysis["proposals"])
        assert result["result"]["task_relations"]["investigation_status"] == "BLOCKED"
    else:
        revision = next(p for p in analysis["proposals"] if p["predicate"] == "REVISES_PLAN")
        assert revision["proposed_verdict"] == "CONFLICT"
        assert revision["comparison"]["status"] == "BLOCKED"
        assert revision["comparison"]["delta_calendar_days"] is None
