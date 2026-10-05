from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from linkloom.agents.base import AgentTask
from linkloom.agents.model_adapter import (
    ModelToolCall,
    ModelToolResult,
    ModelToolTurn,
    ModelTurnProposal,
    ModelTurnRequest,
)
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.models import (
    ModelExecutionRecord,
    RuntimeState,
    SourceContext,
    ToolExecutionRecord,
)
from linkloom.tools.contracts import ToolError, ToolResult


RUN_ID = "run_runtime_v2_grounding"
TASK_ID = "task_runtime_v2_grounding"
AGENT_ID = "retrieval_agent"


def _verified_result(
    call_id: str,
    evidence_id: str,
    *,
    business_status: str | None = None,
) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        tool_id="search_notes",
        status="ok",
        value=[
            {
                "evidence_id": evidence_id,
                "relative_path": f"{evidence_id}.md",
                "content_sha256": f"content-{evidence_id}",
                "line_start": 1,
                "line_end": 1,
                "quote": f"Evidence {evidence_id}",
                "quote_sha256": f"quote-{evidence_id}",
                "status": "verified",
            }
        ],
        business_status=business_status,
    )


def _tool_turn(sequence: int, results: list[ToolResult]) -> ModelToolTurn:
    turn_id = f"{RUN_ID}:turn:{sequence}"
    proposal_id = f"{turn_id}:proposal"
    calls = [
        ModelToolCall.bind(
            provider_call_id=f"provider-{sequence}-{ordinal}",
            proposal_id=proposal_id,
            ordinal=ordinal,
            tool_id=result.tool_id,
            arguments={"query": f"query-{sequence}-{ordinal}"},
            run_id=RUN_ID,
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            sequence=sequence,
        )
        for ordinal, result in enumerate(results)
    ]
    bound_results = [
        ToolResult(
            call_id=call.runtime_call.call_id,
            tool_id=result.tool_id,
            status=result.status,
            value=result.value,
            error=result.error,
            business_status=result.business_status,
        )
        for call, result in zip(calls, results, strict=True)
    ]
    return ModelToolTurn(
        proposal_id=proposal_id,
        source_turn_id=turn_id,
        source_sequence=sequence,
        tool_calls=calls,
        tool_results=[
            ModelToolResult.for_call(
                run_id=RUN_ID,
                proposal_id=proposal_id,
                ordinal=ordinal,
                model_call=call,
                result=result,
            )
            for ordinal, (call, result) in enumerate(
                zip(calls, bound_results, strict=True)
            )
        ],
    )


def _task() -> AgentTask:
    return AgentTask(
        task_id=TASK_ID,
        run_id=RUN_ID,
        parent_task_id=None,
        parent_agent_id="supervisor",
        agent_id=AGENT_ID,
        workflow="team_decision",
        input_refs=[],
        allowed_tool_ids=["search_notes"],
        max_steps=4,
        deadline_ms=10_000,
        status="running",
        attempt=0,
        created_at="2026-09-15T00:00:00+00:00",
    )


def test_final_grounding_uses_only_verified_results_in_the_exact_v2_request():
    root = Path(".artifacts") / "runtime-v2-grounding-tests" / uuid4().hex
    store = ModelArtifactStore(root / "models")
    first_turn = _tool_turn(
        1,
        [
            _verified_result("placeholder-1", "ev_first"),
            ToolResult(
                call_id="placeholder-error",
                tool_id="search_notes",
                status="error",
                error=ToolError(
                    code="TOOL_PERMISSION_DENIED",
                    category="permission",
                    message="Denied.",
                    retryable=False,
                    safe_to_expose=True,
                ),
            ),
            _verified_result(
                "placeholder-business",
                "ev_business_failure",
                business_status="NOT_FOUND",
            ),
        ],
    )
    second_turn = _tool_turn(
        2,
        [_verified_result("placeholder-2", "ev_second")],
    )
    request = ModelTurnRequest(
        run_id=RUN_ID,
        turn_id=f"{RUN_ID}:turn:3",
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        sequence=3,
        user_input="produce one grounded final",
        observation=None,
        available_tools=[],
        evidence_context=[_verified_result("context-call", "ev_context")],
        tool_turns=[first_turn, second_turn],
    )
    request_artifact = store.write_request(
        RUN_ID,
        request.turn_id,
        {
            "runtime_identity": {
                "run_id": RUN_ID,
                "turn_id": request.turn_id,
                "task_id": TASK_ID,
                "agent_id": AGENT_ID,
                "sequence": 3,
            },
            "model_request": request.to_dict(),
        },
    )
    final_proposal = ModelTurnProposal.final(
        f"{request.turn_id}:proposal",
        "final",
    )
    final_record = ModelExecutionRecord(
        run_id=RUN_ID,
        turn_id=request.turn_id,
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        sequence=3,
        status="completed",
        request_ref=request_artifact.ref,
        request_sha256=request_artifact.sha256,
        normalized_proposal=final_proposal.to_dict(),
        proposal_id=final_proposal.proposal_id,
    )
    hidden = _verified_result("hidden-call", "ev_not_in_final_request")
    state = RuntimeState(
        schema_version=1,
        run_id=RUN_ID,
        thread_id="thread-runtime-v2-grounding",
        workflow="team_decision",
        status="running",
        step_seq=3,
        request_ref="request-runtime-v2-grounding.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="fixture-runtime-v2-grounding",
        ),
        tool_ledger=[
            ToolExecutionRecord(
                call_id=hidden.call_id,
                run_id=RUN_ID,
                task_id=TASK_ID,
                agent_id=AGENT_ID,
                tool_id=hidden.tool_id,
                sequence=2,
                status="completed",
                arguments={"query": "hidden"},
                result=hidden.to_dict(),
            )
        ],
        model_executions=[final_record],
    )

    visible = RetrievalAgent(
        model=object(),
        artifact_store=store,
    )._final_model_visible_refs(state, _task())

    assert visible == ["ev_context", "ev_first", "ev_second"]


def test_v2_final_proposal_is_recognized_as_a_durable_local_final():
    proposal = ModelTurnProposal.final(
        f"{RUN_ID}:turn:1:proposal",
        "final",
    )
    state = RuntimeState(
        schema_version=1,
        run_id=RUN_ID,
        thread_id="thread-runtime-v2-grounding",
        workflow="team_decision",
        status="running",
        step_seq=1,
        request_ref="request-runtime-v2-grounding.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="fixture-runtime-v2-grounding",
        ),
        model_executions=[
            ModelExecutionRecord(
                run_id=RUN_ID,
                turn_id=f"{RUN_ID}:turn:1",
                task_id=TASK_ID,
                agent_id=AGENT_ID,
                sequence=1,
                status="response_durable",
                normalized_proposal=proposal.to_dict(),
                proposal_id=proposal.proposal_id,
            )
        ],
    )

    assert RetrievalAgent._has_durable_final(state, _task(), AGENT_ID) is True
