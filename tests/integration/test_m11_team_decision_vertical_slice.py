"""M1.1 Slice A acceptance on a private copy of frozen mps-001 inputs only."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.observability.reader import TraceReader
from linkloom.runtime.checkpoint import InMemoryCheckpointer, SQLiteCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


class ProcessCrash(BaseException):
    """Test-only crash that bypasses runtime finalization."""


class CrashOnDurableFinal(SQLiteCheckpointer):
    """Persist the Final response, then simulate a cold-process interruption."""

    def save(self, state, checkpoint_id=None):
        result = super().save(state, checkpoint_id)
        latest = state.model_executions[-1] if state.model_executions else None
        if (
            latest is not None
            and latest.status == "response_durable"
            and latest.normalized_action.get("kind") == "final"
        ):
            raise ProcessCrash("durable_team_decision_final")
        return result


@pytest.fixture
def m11_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"m11-{uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _seed_inputs() -> dict[str, object]:
    """Read only public case inputs; expected_* fields never leave the test oracle."""
    dataset = Path(__file__).resolve().parents[2] / "docs" / "requirements" / "m1_team_decision_eval_seed" / "dataset.jsonl"
    for line in dataset.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case.get("case_id") == "mps-001":
            return {
                "question": case["user_question"],
                "workspace_id": case["workspace_id"],
                "source_notes": list(case["source_notes"]),
                "distractor_notes": list(case["distractor_notes"]),
            }
    raise AssertionError("Frozen mps-001 input is unavailable.")


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(root.rglob("*.md"))
    }


def _environment(root: Path) -> dict[str, object]:
    case = _seed_inputs()
    source = (
        Path(__file__).resolve().parents[2]
        / "docs" / "requirements" / "m1_team_decision_eval_seed" / "workspaces"
        / str(case["workspace_id"])
    )
    vault = root / "vault"
    shutil.copytree(source, vault)
    source_before = _file_hashes(source)
    copy_before = _file_hashes(vault)
    paths = set(copy_before)
    assert {Path(path).name for path in case["source_notes"]} <= paths
    assert {Path(path).name for path in case["distractor_notes"]} <= paths
    return {
        "root": root,
        "vault": vault,
        "index": scan_vault(vault, root / "scan").index_path,
        "case": case,
        "source": source,
        "source_before": source_before,
        "copy_before": copy_before,
    }


def _tool(request: ModelTurnRequest, tool_id: str, arguments: dict[str, object]) -> ModelAction:
    return ModelAction.tool(ToolCall(
        call_id=f"m11-{request.sequence}-{tool_id}",
        tool_id=tool_id,
        arguments=arguments,
    ))


def _decision_from_observed_quote(ref: str, quote: str) -> str:
    match = re.search(r"selected (?P<provider>.+?) as the approved model provider", quote)
    assert match is not None, "The controlled model must derive the provider from observed source text."
    provider = match.group("provider")
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": f"{provider} is the approved model provider for the Atlas Lantern pilot.",
                "status": "approved",
                "evidence_refs": [ref],
            },
            "rationale": [{
                "point": "The observed decision record establishes the approval.",
                "evidence_refs": [ref],
            }],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "none",
                "statement": None,
                "unknown_fields": [],
                "evidence_refs": [],
            },
            "evidence_refs": [ref],
        },
        ensure_ascii=False,
    )


def _mps_model(*, fabricated_ref: bool = False) -> FakeModelAdapter:
    """Policy reads only ToolResult observations, never dataset expected_* fields."""

    def search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _tool(request, "search_notes", {
            "query": "Atlas Lantern approved model provider",
            "source_context": {"intent": "provider decision"},
            "limit": 5,
        })

    def read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        final_record = next(
            item for item in request.observation.value
            if item["relative_path"] == "03-final-decision.md"
        )
        return _tool(request, "read_verified_note", {"note_ref": final_record["evidence_id"]})

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        value = request.observation.value
        assert value["relative_path"] == "03-final-decision.md"
        ref = "fabricated-evidence-id" if fabricated_ref else value["evidence_id"]
        return ModelAction.final(_decision_from_observed_quote(ref, value["quote"]))

    return FakeModelAdapter([search, read, final])


def _engine(environment: dict[str, object], model, *, checkpointer=None) -> RuntimeEngine:
    root = environment["root"]
    return RuntimeEngine(
        vault_root=environment["vault"],
        index_path=environment["index"],
        checkpoint_dir=root / "checkpoints",
        trace_dir=root / "traces",
        checkpointer=checkpointer,
        model=model,
    )


def _start(engine: RuntimeEngine, environment: dict[str, object], thread_id: str):
    return engine.start_multi_agent(RunRequest(
        request_id="m11-mps-001",
        thread_id=thread_id,
        workflow="team_decision",
        query=environment["case"]["question"],
        max_steps=3,
        max_provider_requests=3,
        dry_run=True,
    ))


def _result(root: Path, run_id: str) -> dict[str, object]:
    path = root / "checkpoints" / "results" / run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_source_unchanged(environment: dict[str, object]) -> None:
    assert _file_hashes(environment["source"]) == environment["source_before"]
    assert _file_hashes(environment["vault"]) == environment["copy_before"]


def _assert_no_gold_in_model_requests(model: FakeModelAdapter) -> None:
    for request in model.requests:
        assert "expected_" not in json.dumps(request.to_dict(), ensure_ascii=False)


def test_frozen_mps_001_production_path_persists_current_decision_from_real_workspace(m11_root):
    environment = _environment(m11_root)
    model = _mps_model()
    engine = _engine(environment, model, checkpointer=InMemoryCheckpointer())

    status = _start(engine, environment, "m11-frozen-mps")
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None and status.result_ref
    result = _result(environment["root"], status.run_id)

    assert status.status == state.status == result["status"] == "completed"
    assert [record.tool_id for record in state.tool_ledger] == ["search_notes", "read_verified_note"]
    payload = result["result"]["team_decision"]
    assert payload["decision"]["value"].startswith("Aster A is the approved")
    assert "Borealis B" not in json.dumps(payload, ensure_ascii=False)
    assert payload["decision"]["evidence_refs"] == payload["evidence_refs"]
    observed_refs = {
        item["evidence_id"]
        for record in state.tool_ledger
        for item in ([record.result["value"]] if isinstance(record.result["value"], dict) else record.result["value"])
        if isinstance(item, dict) and "evidence_id" in item
    }
    assert set(payload["evidence_refs"]) <= observed_refs
    _assert_no_gold_in_model_requests(model)
    _assert_source_unchanged(environment)


def test_fabricated_team_decision_evidence_fails_the_outer_workflow(m11_root):
    environment = _environment(m11_root)
    engine = _engine(environment, _mps_model(fabricated_ref=True), checkpointer=InMemoryCheckpointer())

    status = _start(engine, environment, "m11-fabricated")
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    result = _result(environment["root"], status.run_id)
    events = TraceReader(environment["root"] / "traces").read_events(status.run_id)

    assert status.status == state.status == result["status"] == "failed"
    assert status.result_ref and state.result_ref == status.result_ref
    assert state.error.code == result["error"]["code"] == "TEAM_DECISION_CONTRACT_ERROR"
    assert result["result"] is None
    assert next(event for event in events if event.event_type == "agent.task.failed").error["code"] == "TEAM_DECISION_CONTRACT_ERROR"
    _assert_source_unchanged(environment)


def test_team_decision_evidence_review_failure_cannot_publish_completed_workflow(m11_root, monkeypatch):
    environment = _environment(m11_root)
    monkeypatch.setattr(
        RuntimeAgentAdapter,
        "_validate_evidence",
        lambda self, ref: {"status": "fail", "reason": "synthetic_review_failure", "ref": ref},
    )
    engine = _engine(environment, _mps_model(), checkpointer=InMemoryCheckpointer())

    status = _start(engine, environment, "m11-review-failure")
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    result = _result(environment["root"], status.run_id)

    assert status.status == state.status == result["status"] == "failed"
    assert state.error.code == "TEAM_DECISION_EVIDENCE_REVIEW_FAILED"
    assert result["result"] is None
    _assert_source_unchanged(environment)


def test_team_decision_cold_resume_reuses_durable_final_without_replaying_model_or_tools(m11_root):
    fresh_environment = _environment(m11_root / "fresh")
    fresh_model = _mps_model()
    fresh = _engine(fresh_environment, fresh_model, checkpointer=InMemoryCheckpointer())
    fresh_status = _start(fresh, fresh_environment, "m11-fresh")
    fresh_result = _result(fresh_environment["root"], fresh_status.run_id)["result"]["team_decision"]

    environment = _environment(m11_root / "resume")
    before = _mps_model()
    first = _engine(
        environment,
        before,
        checkpointer=CrashOnDurableFinal(environment["root"] / "checkpoints"),
    )
    with pytest.raises(ProcessCrash):
        _start(first, environment, "m11-resume")
    durable = first.checkpointer.get_latest("m11-resume")
    assert durable is not None and durable.workflow == "team_decision"
    assert durable.model_executions[-1].status == "response_durable"
    assert durable.model_executions[-1].normalized_action["kind"] == "final"
    assert before.call_count == 3
    old_ledger = [record.to_dict() for record in durable.tool_ledger]
    assert len(old_ledger) == 2

    after = FakeModelAdapter([ModelAction.final("must not replay durable Team Decision Final")])
    restarted = _engine(environment, after)
    status = restarted.resume_multi_agent("m11-resume")
    state = restarted.checkpointer.get_latest("m11-resume")
    assert state is not None and status.result_ref
    result = _result(environment["root"], status.run_id)

    assert status.status == state.status == result["status"] == "completed"
    assert state.workflow == result["workflow"] == "team_decision"
    assert not after.requests
    assert after.call_count == 0
    assert [record.to_dict() for record in state.tool_ledger] == old_ledger
    assert result["result"]["team_decision"] == fresh_result
    assert result["result"]["team_decision"]["evidence_refs"]
    _assert_no_gold_in_model_requests(before)
    _assert_source_unchanged(environment)


def test_team_decision_cold_resume_revalidates_a_durable_final(m11_root):
    environment = _environment(m11_root)
    before = _mps_model(fabricated_ref=True)
    first = _engine(
        environment,
        before,
        checkpointer=CrashOnDurableFinal(environment["root"] / "checkpoints"),
    )
    with pytest.raises(ProcessCrash):
        _start(first, environment, "m11-resume-invalid")
    durable = first.checkpointer.get_latest("m11-resume-invalid")
    assert durable is not None and durable.model_executions[-1].status == "response_durable"
    old_ledger = [record.to_dict() for record in durable.tool_ledger]

    after = FakeModelAdapter([ModelAction.final("must not replay invalid durable Final")])
    restarted = _engine(environment, after)
    status = restarted.resume_multi_agent("m11-resume-invalid")
    state = restarted.checkpointer.get_latest("m11-resume-invalid")
    assert state is not None
    result = _result(environment["root"], status.run_id)

    assert status.status == state.status == result["status"] == "failed"
    assert state.error.code == result["error"]["code"] == "TEAM_DECISION_CONTRACT_ERROR"
    assert not after.requests and after.call_count == 0
    assert [record.to_dict() for record in state.tool_ledger] == old_ledger
    assert result["result"] is None
    _assert_source_unchanged(environment)
