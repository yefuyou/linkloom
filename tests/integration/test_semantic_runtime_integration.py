from dataclasses import replace

from tests.semantic_integration_support import setup, execute


def test_existing_runtime_calls_semantic_proposer_and_reviewer_without_p4_promotion(tmp_path):
    engine, outer, inner, _, context = setup(tmp_path)
    status, state, result = execute(engine, tmp_path, context.question)
    assert status.status == "completed", (status.to_dict(), result)
    assert outer.call_count == 4 and len(inner.requests) == 1
    assert [r.tool_id for r in state.tool_ledger] == ["inspect_task_context", "query_task_relations", "analyze_task_semantics"]
    payload = result["result"]["task_relations"]
    assert payload["investigation_status"] == "PENDING_SEMANTIC_REVIEW"
    analysis = payload["semantic_analysis"]
    assert analysis["analysis_status"] == "EXECUTED"
    assert any(p.get("comparison", {}).get("delta_calendar_days") == 4 for p in analysis["proposals"])
    assert all(not p["publishable"] and p["semantic_status"] == "SEMANTIC_NOT_EVALUATED" for p in analysis["proposals"])
    assert payload["relation_query"]["status"] == "NO_VERIFIED_RELATIONS"
    assert result["review"]["decision"] == "evidence_sufficient"
    assert not (tmp_path / "relations.sqlite").exists()


def test_reviewer_rechecks_semantic_revocation_after_tool_read(tmp_path):
    engine, _, inner, live, context = setup(tmp_path)
    def revoke():
        live["snapshot"] = replace(live["snapshot"],
            revoked_observation_ids=frozenset([context.observations[0].observation_id]))
    from tests.semantic_integration_support import runtime_script
    engine.model = runtime_script(context.workspace_id, before_final=revoke)
    status, _, result = execute(engine, tmp_path, context.question)
    assert len(inner.requests) == 1
    assert status.status == "failed"
    assert result["review"]["decision"] == "schema_invalid"
