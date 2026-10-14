"""Offline Final-contract communication through the production Agent path."""

from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy

import pytest
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


@pytest.fixture
def offline_root(tmp_path: Path) -> Path:
    return tmp_path


def _engine(
    root: Path,
    model: FakeModelAdapter,
    *,
    vault_root: Path | None = None,
    index_path: Path | None = None,
) -> RuntimeEngine:
    vault = vault_root or root / "vault"
    if vault_root is None:
        vault.mkdir()
        (vault / "decision.md").write_text(
            "# Meeting notes\n\n"
            "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B for Atlas.\n",
            encoding="utf-8",
        )
    resolved_index = index_path or scan_vault(vault, root / "scan").index_path
    return RuntimeEngine(
        vault_root=vault,
        index_path=resolved_index,
        checkpoint_dir=root / "checkpoints",
        trace_dir=root / "traces",
        checkpointer=InMemoryCheckpointer(),
        model=model,
    )


def _search(request: ModelTurnRequest) -> ModelAction:
    assert request.observation is None
    assert "team_decision_final_schema=" in request.user_input
    return ModelAction.tool(
        ToolCall(
            call_id="contract-search",
            tool_id="search_notes",
            arguments={
                "query": "Cobalt B accountable owner Atlas",
                "source_context": json.loads(
                    request.user_input.split("team_decision_request=", 1)[1]
                )["source_context"],
                "limit": 5,
            },
        )
    )


def _read(request: ModelTurnRequest) -> ModelAction:
    assert request.observation is not None and request.observation.status == "ok"
    evidence = next(
        item for item in request.observation.value if "accountable owner" in item["quote"]
    )
    return ModelAction.tool(
        ToolCall(
            call_id="contract-read",
            tool_id="read_verified_note",
            arguments={"note_ref": evidence["evidence_id"]},
        )
    )


def _final_from_read(request: ModelTurnRequest, *, omit_unknown_fields: bool) -> ModelAction:
    assert request.observation is not None and request.observation.status == "ok"
    evidence = request.observation.value
    assert "Mira Chen is the accountable owner of Cobalt B" in evidence["quote"]
    uncertainty = {
        "status": "none",
        "statement": None,
        "unknown_fields": [],
        "evidence_refs": [],
    }
    if omit_unknown_fields:
        uncertainty.pop("unknown_fields")
    ref = evidence["evidence_id"]
    return ModelAction.final(
        json.dumps(
            {
                "schema_version": "team-decision-result/v1",
                "decision": {
                    "value": "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B.",
                    "status": "approved",
                    "evidence_refs": [ref],
                },
                "rationale": [],
                "rejected_alternatives": [],
                "actions": [],
                "unresolved_items": [],
                "uncertainty": uncertainty,
                "evidence_refs": [ref],
            }
        )
    )


def _run(root: Path, *, omit_unknown_fields: bool):
    model = FakeModelAdapter(
        [
            _search,
            _read,
            lambda request: _final_from_read(
                request,
                omit_unknown_fields=omit_unknown_fields,
            ),
        ]
    )
    engine = _engine(root, model)
    status = engine.start_multi_agent(
        RunRequest(
            request_id="offline-final-contract",
            thread_id="offline-final-contract-thread",
            workflow="team_decision",
            query="Who is the accountable owner of Cobalt B in Atlas?",
            max_steps=3,
            max_provider_requests=3,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    result = json.loads(
        (root / "checkpoints" / "results" / status.run_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    return status, state, model, result


def test_search_tool_result_complete_final_is_accepted_and_grounded(
    offline_root: Path,
) -> None:
    status, state, model, result = _run(offline_root, omit_unknown_fields=False)

    assert status.status == state.status == "completed"
    assert [record.tool_id for record in state.tool_ledger] == ["search_notes", "read_verified_note"]
    assert model.call_count == 3
    assert result["result"] is not None
    payload = result["result"]["team_decision"]
    assert payload["decision"]["value"] == (
        "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B."
    )
    assert payload["uncertainty"]["unknown_fields"] == []
    observed_refs = {state.tool_ledger[1].result["value"]["evidence_id"]}
    assert set(payload["evidence_refs"]) <= observed_refs


def test_search_tool_result_final_missing_required_nested_field_fails_closed(
    offline_root: Path,
) -> None:
    status, state, model, result = _run(offline_root, omit_unknown_fields=True)

    assert status.status == state.status == "failed"
    assert state.error is not None
    assert state.error.code == "TEAM_DECISION_CONTRACT_ERROR"
    assert result["result"] is None
    assert [record.tool_id for record in state.tool_ledger] == ["search_notes", "read_verified_note"]
    assert model.call_count == 3


@pytest.mark.parametrize("reverse_relation", [False, True])
def test_explicit_typed_final_protocol_runs_generic_claims_through_runtime_and_reviewer(
    tmp_path: Path, reverse_relation: bool,
) -> None:
    vault = tmp_path / "typed-vault"
    vault.mkdir()
    source_quote = (
        "As of 2025-04-15 Avery Lin is the accountable owner of Juniper Orbit. "
        "Morgan Yu is Avery Lin's direct leader."
    )
    (vault / "decision.md").write_text(source_quote, encoding="utf-8")

    def search(request: ModelTurnRequest) -> ModelAction:
        run_request = json.loads(
            request.user_input.split("team_decision_request=", 1)[1]
        )
        assert "final_protocol=claim-proposal/v1" in request.user_input
        return ModelAction.tool(ToolCall(
            call_id="typed-generic-search", tool_id="search_notes",
            arguments={
                "query": "Juniper Orbit accountable owner direct leader",
                "source_context": run_request["source_context"], "limit": 5,
            },
        ))

    def read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        evidence = next(item for item in request.observation.value if "Juniper Orbit" in item["quote"])
        return ModelAction.tool(ToolCall(
            call_id="typed-generic-read", tool_id="read_verified_note",
            arguments={"note_ref": evidence["evidence_id"]},
        ))

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        evidence = request.observation.value
        assert evidence["quote"] == source_quote
        evidence_id = evidence["evidence_id"]
        leader_subject, leader_object = (
            ("Morgan Yu", "Avery Lin") if reverse_relation
            else ("Avery Lin", "Morgan Yu")
        )
        return ModelAction.final(json.dumps({
            "schema_version": "claim-proposal/v1",
            "claims": [
                {
                    "subject": "Juniper Orbit", "predicate": "owner_of_project",
                    "object": "Avery Lin", "evidence_ids": [evidence_id],
                    "quote": "As of 2025-04-15 Avery Lin is the accountable owner of Juniper Orbit.",
                    "valid_on": "2025-04-15", "temporal_kind": "snapshot",
                },
                {
                    "subject": leader_subject, "predicate": "reports_to",
                    "object": leader_object, "evidence_ids": [evidence_id],
                    "quote": "Morgan Yu is Avery Lin's direct leader.",
                    "valid_on": None, "temporal_kind": None,
                },
            ],
        }, ensure_ascii=False))

    model = FakeModelAdapter([search, read, final])
    engine = _engine(tmp_path, model, vault_root=vault)
    status = engine.start_multi_agent(RunRequest(
        request_id=f"generic-typed-{reverse_relation}",
        thread_id=f"generic-typed-{reverse_relation}",
        workflow="team_decision",
        query="For Juniper Orbit, identify its recorded steward and that person's direct leader.",
        max_steps=4, max_provider_requests=4, dry_run=True,
        final_protocol="claim-proposal/v1",
    ))
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    result_path = engine.checkpoint_dir / "results" / status.run_id / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))

    if reverse_relation:
        assert status.status == state.status == "failed"
        assert state.error is not None and state.error.code == "TEAM_DECISION_CONTRACT_ERROR"
        assert result["result"] is None
    else:
        assert status.status == state.status == "completed", status.error
        assert result["result"]["team_decision"]["schema_version"] == "team-decision-result/v1"
        assert result["result"]["team_decision"]["decision"]["status"] == "approved"
        assert model.call_count == 3


def _phase14_inputs(run_id: str):
    base = Path(__file__).resolve().parents[2] / "work" / "phase14_gemini"
    observed = json.loads(
        (base / "runs" / run_id / "observed.json").read_text(encoding="utf-8")
    )
    shared_name = "P0_CN2" if run_id == "P14-CN2" else "P0_ALIAS"
    vault = base / "shared" / shared_name / "vault"
    index = base / "runs" / run_id / "scan" / "vault_index.json"
    return observed, vault, index


def _phase14_tool_replay(observed, final_raw: str):
    actions = []
    for index, trace_action in enumerate(observed["tool_actions"]):
        if trace_action["tool_id"] == "search_notes":
            query = trace_action["query"]

            def search(request, *, query=query, index=index):
                marker = "team_decision_request="
                request_data = json.loads(request.user_input.split(marker, 1)[1])
                return ModelAction.tool(ToolCall(
                    call_id=f"p14-replay-search-{index}",
                    tool_id="search_notes",
                    arguments={
                        "query": query,
                        "source_context": request_data["source_context"],
                        "limit": 5,
                    },
                ))

            actions.append(search)
        elif trace_action["tool_id"] == "read_verified_note":
            ref = trace_action["candidates_or_read"][0]["evidence_id"]

            def read(request, *, ref=ref, index=index):
                assert request.observation is not None
                return ModelAction.tool(ToolCall(
                    call_id=f"p14-replay-read-{index}",
                    tool_id="read_verified_note",
                    arguments={"note_ref": ref},
                ))

            actions.append(read)
        else:
            raise AssertionError(f"Unexpected Phase 14 tool action: {trace_action['tool_id']}")
    actions.append(lambda request: ModelAction.final(final_raw))
    return actions


def _phase14_cn2_claim_proposal(observed) -> dict[str, object]:
    read_refs = [
        action["candidates_or_read"][0]["evidence_id"]
        for action in observed["tool_actions"]
        if action["tool_id"] == "read_verified_note"
    ]
    assert len(read_refs) == 2
    return {
        "schema_version": "claim-proposal/v1",
        "claims": [
            {
                "subject": "青岚计划",
                "predicate": "owner_of_project",
                "object": "李明",
                "evidence_ids": [read_refs[0]],
                "quote": "截至 2025-03-01，李明是青岚计划的负责人。",
                "valid_on": "2025-03-01",
                "temporal_kind": "snapshot",
            },
            {
                "subject": "李明",
                "predicate": "reports_to",
                "object": "王芳",
                "evidence_ids": [read_refs[1]],
                "quote": "李明的直属领导是王芳。",
                "valid_on": None,
                "temporal_kind": None,
            },
        ],
    }


@pytest.mark.parametrize("run_id", ["P14-ALIAS-A", "P14-ALIAS-B"])
def test_phase14_real_final_replays_through_runtime_and_reviewer(run_id: str, tmp_path: Path):
    observed, vault, index = _phase14_inputs(run_id)
    model = FakeModelAdapter(_phase14_tool_replay(observed, observed["final_raw"]))
    engine = _engine(tmp_path, model, vault_root=vault, index_path=index)

    status = engine.start_multi_agent(RunRequest(
        request_id=f"phase14-replay-{run_id}",
        thread_id=f"phase14-replay-{run_id}",
        workflow="team_decision",
        query="Who owns Aurora Launch or Cobalt B?",
        max_steps=8,
        max_provider_requests=8,
        dry_run=True,
    ))
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    result = json.loads(
        (engine.checkpoint_dir / "results" / status.run_id / "result.json").read_text(
            encoding="utf-8"
        )
    )

    assert status.status == state.status == "completed", status.error
    assert [record.tool_id for record in state.tool_ledger].count("read_verified_note") == (
        len([item for item in observed["tool_actions"] if item["tool_id"] == "read_verified_note"])
    )
    assert result["result"]["team_decision"]["decision"]["value"] == (
        json.loads(observed["final_raw"])["decision"]["value"]
    )


def test_phase14_cn2_legacy_final_replay_remains_compatible(
    tmp_path: Path,
) -> None:
    observed, vault, index = _phase14_inputs("P14-CN2")
    model = FakeModelAdapter(_phase14_tool_replay(observed, observed["final_raw"]))
    engine = _engine(tmp_path, model, vault_root=vault, index_path=index)
    status = engine.start_multi_agent(RunRequest(
        request_id="phase14-cn2-legacy-final",
        thread_id="phase14-cn2-legacy-final",
        workflow="team_decision",
        query="谁负责青岚计划，李明的直属领导是谁？",
        max_steps=8,
        max_provider_requests=8,
        dry_run=True,
    ))
    state = engine.checkpointer.get_latest(status.thread_id)

    assert status.status == state.status == "completed", status.error
    assert state.error is None


def test_phase14_cn2_typed_claims_complete_runtime_without_reparsing_generated_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from linkloom.agents import team_decision

    observed, vault, index = _phase14_inputs("P14-CN2")
    proposal = _phase14_cn2_claim_proposal(observed)
    model = FakeModelAdapter(_phase14_tool_replay(
        observed, json.dumps(proposal, ensure_ascii=False),
    ))
    engine = _engine(tmp_path, model, vault_root=vault, index_path=index)

    def forbid_prose_reparse(*args, **kwargs):
        raise AssertionError("typed verified claims must bypass prose reparsing")

    monkeypatch.setattr(team_decision, "assess_claim_text", forbid_prose_reparse)
    status = engine.start_multi_agent(RunRequest(
        request_id="phase14-replay-cn2-typed",
        thread_id="phase14-replay-cn2-typed",
        workflow="team_decision",
        query="谁负责青岚计划，李明的直属领导是谁？",
        max_steps=8,
        max_provider_requests=8,
        dry_run=True,
        final_protocol="claim-proposal/v1",
    ))
    state = engine.checkpointer.get_latest(status.thread_id)
    result = json.loads(
        (engine.checkpoint_dir / "results" / status.run_id / "result.json").read_text(
            encoding="utf-8"
        )
    )

    assert status.status == state.status == "completed", status.error
    assert result["result"]["team_decision"]["schema_version"] == "team-decision-result/v1"
    assert result["result"]["team_decision"]["decision"]["value"] == (
        "截至 2025-03-01，青岚计划的负责人是李明；李明直接向王芳汇报"
    )
    assert [record.tool_id for record in state.tool_ledger].count("read_verified_note") == 2


@pytest.mark.parametrize(
    ("run_id", "mutation"),
    [
        ("P14-ALIAS-A", "wrong_owner"),
        ("P14-ALIAS-B", "wrong_owner"),
        ("P14-CN2", "wrong_owner"),
        ("P14-CN2", "reverse_relation"),
        ("P14-CN2", "wrong_date"),
    ],
)
def test_phase14_runtime_reviewer_rejects_wrong_owner_relation_or_date(
    run_id: str, mutation: str, tmp_path: Path,
):
    observed, vault, index = _phase14_inputs(run_id)
    if run_id == "P14-CN2":
        proposal = _phase14_cn2_claim_proposal(observed)
        if mutation == "wrong_owner":
            proposal["claims"][0]["object"] = "赵强"
        elif mutation == "reverse_relation":
            proposal["claims"][1]["subject"] = "王芳"
            proposal["claims"][1]["object"] = "李明"
        elif mutation == "wrong_date":
            proposal["claims"][0]["valid_on"] = "2025-03-02"
        final_raw = json.dumps(proposal, ensure_ascii=False)
    else:
        raw = deepcopy(json.loads(observed["final_raw"]))
        raw["decision"]["value"] = raw["decision"]["value"].replace(
            "Mira Chen", "Leo Park",
        )
        final_raw = json.dumps(raw, ensure_ascii=False)
    model = FakeModelAdapter(_phase14_tool_replay(observed, final_raw))
    engine = _engine(tmp_path, model, vault_root=vault, index_path=index)
    query = (
        "谁负责青岚计划，李明的直属领导是谁？"
        if run_id == "P14-CN2"
        else "Who owns Aurora Launch or Cobalt B?"
    )

    status = engine.start_multi_agent(RunRequest(
        request_id=f"phase14-negative-{run_id}-{mutation}",
        thread_id=f"phase14-negative-{run_id}-{mutation}",
        workflow="team_decision",
        query=query,
        max_steps=8,
        max_provider_requests=8,
        dry_run=True,
        final_protocol=(
            "claim-proposal/v1" if run_id == "P14-CN2" else "team-decision-result/v1"
        ),
    ))
    state = engine.checkpointer.get_latest(status.thread_id)

    assert status.status == "failed"
    assert state is not None and state.status == "failed"
    assert state.error is not None and state.error.code == "TEAM_DECISION_CONTRACT_ERROR"
    assert [record.tool_id for record in state.tool_ledger].count("read_verified_note") == (
        len([item for item in observed["tool_actions"] if item["tool_id"] == "read_verified_note"])
    )
