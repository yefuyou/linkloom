"""Regression coverage for the Product workspace evidence-context boundary.

The UI-facing retrieval backend labels evidence with ``ws_*`` Product
workspace IDs.  Runtime source provenance still carries the independent
``root_*`` vault fingerprint.  These tests keep the two identities separate
while exercising the production RetrievalAgent integration and the bounded
history projection.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from linkloom.agents.base import AgentTask
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.runtime.models import (
    PolicySnapshot,
    RuntimeState,
    SourceContext,
)
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


RUN_ID = "run_product_evidence_scope"
TASK_ID = "task_product_evidence_scope"
AGENT_ID = "retrieval_agent"
PRODUCT_WORKSPACE = "ws_product_ui"
OTHER_WORKSPACE = "ws_other_ui"
VAULT_FINGERPRINT = "root_product_fixture"
OTHER_VAULT_FINGERPRINT = "root_other_fixture"


def _evidence(evidence_id: str, workspace_id: str, *, line: int = 1) -> dict:
    return {
        "status": "verified",
        "evidence_id": evidence_id,
        "relative_path": "notes/product.md",
        "content_sha256": "a" * 64,
        "quote": "Mira Chen is the accountable owner of Lantern Plan.",
        "quote_sha256": "b" * 64,
        "line_start": line,
        "line_end": line,
        "workspace_id": workspace_id,
    }


def _search_definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search verified notes.",
        input_schema={
            "type": "object",
            "required": ["query", "source_context", "limit"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "source_context": {"type": "object"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _read_definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="read_verified_note",
        version="1",
        description="Read one verified note passage.",
        input_schema={
            "type": "object",
            "required": ["note_ref", "source_context"],
            "properties": {
                "note_ref": {"type": "string", "minLength": 1},
                "source_context": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    )


def _policy(max_calls: int = 8) -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id=AGENT_ID,
            allowed_tool_ids=["search_notes", "read_verified_note"],
            denied_tool_ids=[],
            max_calls=max_calls,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _runtime(search_values: list[dict] | None = None) -> ToolRuntime:
    registry = ToolRegistry()
    search = _search_definition()
    read = _read_definition()
    registry.register(search, lambda _arguments: list(search_values or []))
    registry.register(
        read,
        lambda arguments: _evidence(
            arguments["note_ref"], PRODUCT_WORKSPACE,
        ),
    )
    return ToolRuntime(registry)


def _state() -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id=RUN_ID,
        thread_id="thread_product_evidence_scope",
        workflow="team_decision",
        status="running",
        step_seq=1,
        request_ref="request_product_evidence_scope.json",
        source=SourceContext(
            index_path=".artifacts/product-evidence-scope/index.json",
            index_sha256="c" * 64,
            vault_root_fingerprint=VAULT_FINGERPRINT,
        ),
        policy=PolicySnapshot(max_steps=4, max_provider_requests=4),
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
        max_steps=2,
        deadline_ms=30_000,
        status="running",
        attempt=0,
        created_at="2026-10-15T00:00:00+00:00",
    )


def _artifact_store() -> ModelArtifactStore:
    root = Path(".artifacts") / "product-evidence-context-scope" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return ModelArtifactStore(root / "models")


def _approved_final(evidence_id: str) -> str:
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": "Mira Chen is the accountable owner of Lantern Plan.",
                "status": "approved",
                "evidence_refs": [evidence_id],
            },
            "rationale": [],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "none",
                "statement": None,
                "unknown_fields": [],
                "evidence_refs": [],
            },
            "evidence_refs": [evidence_id],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_retrieval_agent_preserves_product_workspace_in_evidence_context() -> None:
    """The second production model turn must see the Product-labelled search result.

    This is the live defect reproduction without a Provider: the synthetic
    ToolResult has the same Product ``workspace_id`` shape emitted by the UI
    retrieval backend, while RuntimeState retains a separate vault root
    fingerprint.  The current call sites compare those two identities as if
    they were one and therefore produce an empty ``evidence_context``.
    """

    product_result = _evidence("ev_product_ui", PRODUCT_WORKSPACE)
    model = FakeModelAdapter(
        [
            ModelAction.tool(
                ToolCall(
                    call_id="search_product_ui",
                    tool_id="search_notes",
                    arguments={
                        "query": "product evidence",
                        "source_context": {
                            "workspace_id": PRODUCT_WORKSPACE,
                            "vault_root_fingerprint": VAULT_FINGERPRINT,
                        },
                        "limit": 5,
                    },
                )
            ),
            ModelAction.final(_approved_final(product_result["evidence_id"])),
        ]
    )
    agent = RetrievalAgent(
        model=model,
        initial_state=_state(),
        artifact_store=_artifact_store(),
        state_checkpoint_callback=lambda _state: None,
        expected_workspace_id=PRODUCT_WORKSPACE,
        verified_evidence_context_limit=2,
    )

    result = agent.execute(
        _task(),
        query="product evidence",
        source_context={
            "workspace_id": PRODUCT_WORKSPACE,
            "vault_root_fingerprint": VAULT_FINGERPRINT,
        },
        policy_enforcer=_policy(),
        tool_runtime=_runtime([product_result]),
    )

    assert len(model.requests) == 2
    context_values = [
        value
        for evidence_result in model.requests[1].evidence_context
        for value in (
            evidence_result.value
            if isinstance(evidence_result.value, list)
            else [evidence_result.value]
        )
    ]
    assert [value["evidence_id"] for value in context_values] == ["ev_product_ui"]
    assert {value["workspace_id"] for value in context_values} == {PRODUCT_WORKSPACE}


def _ledger_record(
    ledger: ToolExecutionLedger,
    *,
    call_id: str,
    tool_id: str,
    sequence: int,
    source_context: dict,
    value: dict,
) -> None:
    call = ToolCall(
        call_id=call_id,
        tool_id=tool_id,
        arguments={
            "source_context": source_context,
            **(
                {"query": "scope"} if tool_id == "search_notes"
                else {"note_ref": value["evidence_id"]}
            ),
            **({"limit": 5} if tool_id == "search_notes" else {}),
        },
        run_id=RUN_ID,
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        sequence=sequence,
    )
    result = ToolResult(
        call_id=call_id,
        tool_id=tool_id,
        status="ok",
        value=[value] if tool_id == "search_notes" else value,
        business_status="FOUND",
    )
    ledger.record_pending(call)
    ledger.record_completed(call, result)


def _context_from_ledger(
    ledger: ToolExecutionLedger,
    *,
    limit: int,
) -> list[ToolResult]:
    loop = SingleAgentModelLoop(
        FakeModelAdapter([ModelAction.final("unused")]),
        _runtime(),
        _policy(),
        max_steps=1,
        verified_evidence_context_limit=limit,
    )
    arguments = {
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "agent_id": AGENT_ID,
        "before_sequence": 99,
        "workspace_id": PRODUCT_WORKSPACE,
        "source_fingerprint": VAULT_FINGERPRINT,
    }
    try:
        return loop._verified_evidence_context(ledger, **arguments)
    except TypeError as error:
        # The pre-fix implementation has no separate source_fingerprint
        # parameter.  Keep the workspace-only branch executable so the
        # regression assertions below distinguish the missing fingerprint
        # check from the already-existing workspace check.
        if "source_fingerprint" not in str(error):
            raise
        arguments.pop("source_fingerprint")
        return loop._verified_evidence_context(ledger, **arguments)


def test_context_rejects_foreign_workspace_and_wrong_vault_fingerprint() -> None:
    ledger = ToolExecutionLedger()
    _ledger_record(
        ledger,
        call_id="valid_product",
        tool_id="search_notes",
        sequence=1,
        source_context={
            "workspace_id": PRODUCT_WORKSPACE,
            "vault_root_fingerprint": VAULT_FINGERPRINT,
        },
        value=_evidence("ev_product", PRODUCT_WORKSPACE, line=1),
    )
    _ledger_record(
        ledger,
        call_id="foreign_workspace",
        tool_id="search_notes",
        sequence=2,
        source_context={
            "workspace_id": OTHER_WORKSPACE,
            "vault_root_fingerprint": VAULT_FINGERPRINT,
        },
        value=_evidence("ev_foreign", OTHER_WORKSPACE, line=2),
    )
    _ledger_record(
        ledger,
        call_id="wrong_fingerprint",
        tool_id="search_notes",
        sequence=3,
        source_context={
            "workspace_id": PRODUCT_WORKSPACE,
            "vault_root_fingerprint": OTHER_VAULT_FINGERPRINT,
        },
        value=_evidence("ev_wrong_root", PRODUCT_WORKSPACE, line=3),
    )

    context = _context_from_ledger(ledger, limit=8)

    visible_ids = [
        value["evidence_id"]
        for result in context
        for value in (result.value if isinstance(result.value, list) else [result.value])
    ]
    assert visible_ids == ["ev_product"]


def test_context_keeps_early_verified_read_when_history_is_trimmed() -> None:
    ledger = ToolExecutionLedger()
    identity = {
        "workspace_id": PRODUCT_WORKSPACE,
        "vault_root_fingerprint": VAULT_FINGERPRINT,
    }
    _ledger_record(
        ledger,
        call_id="early_verified_read",
        tool_id="read_verified_note",
        sequence=1,
        source_context=identity,
        value=_evidence("ev_early_read", PRODUCT_WORKSPACE, line=10),
    )
    _ledger_record(
        ledger,
        call_id="later_search",
        tool_id="search_notes",
        sequence=2,
        source_context=identity,
        value=_evidence("ev_later_search", PRODUCT_WORKSPACE, line=20),
    )

    context = _context_from_ledger(ledger, limit=1)

    assert [result.tool_id for result in context] == ["read_verified_note"]
    assert context[0].value["evidence_id"] == "ev_early_read"
