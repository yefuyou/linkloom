from evals.m3_task_impact.fixtures import load_inputs
from evals.m3_task_impact.runner import execute
from evals.m3_task_impact.semantic_runtime_adapter import OfflineSemanticRuntimeAdapter


def test_public_dev_contract_runs_runtime_but_does_not_claim_business_acceptance(tmp_path):
    adapter = OfflineSemanticRuntimeAdapter(tmp_path / "dev")
    record = execute(next(c for c in load_inputs() if c["case_id"] == "D01"), adapter, adapter_kind="FAKE_RUNTIME_NOT_EVALUATED")
    assert record["status"] == "EXECUTED", record
    assert record["execution"]["target_invocations"] == 1
    assert [r["tool"] for r in record["execution"]["tool_receipts"]] == ["list_sources", "read_source"]
    assert adapter.last["status"]["status"] == "completed"
    assert adapter.last["outer_fake_calls"] == 4 and adapter.last["new_live_provider_requests"] == 0
    assert adapter.last["evaluation_status"] == "NOT_EVALUATED"
    semantic = adapter.last["result"]["result"]["task_relations"]["semantic_analysis"]
    assert semantic["analysis_status"] == "BLOCKED" and "EMPTY_QUERY_SET" in semantic["errors"]
    assert record["answer"]["answer_status"] == "ABSTAIN" and not record["answer"]["relations"]
