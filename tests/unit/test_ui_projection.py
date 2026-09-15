from __future__ import annotations

import hashlib

import pytest

from linkloom.runtime.models import RunStatus
from linkloom.schemas import NoteDocument
from linkloom.ui.projection import (
    UIProjectionError,
    WorkspacePresentationContext,
    project_run_snapshot,
)


def _evidence(
    evidence_id: str,
    *,
    path: str = "03-final-decision.md",
    line: int = 10,
    quote: str = "The Atlas Lantern team selected Aster A as the approved model provider for the",
    content_sha256: str = "a" * 64,
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "relative_path": path,
        "content_sha256": content_sha256,
        "line_start": line,
        "line_end": line,
        "quote": quote,
        "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "source_kind": "note_body",
        "reason": "matched query evidence",
        "status": "verified",
    }


def _ledger(*values: dict[str, object], tool_id: str = "search_notes") -> list[dict[str, object]]:
    return [
        {
            "call_id": f"call-{tool_id}",
            "run_id": "run-1",
            "task_id": "task-1",
            "agent_id": "retrieval_agent",
            "tool_id": tool_id,
            "sequence": 1,
            "status": "completed",
            "arguments": {},
            "result": {
                "call_id": f"call-{tool_id}",
                "tool_id": tool_id,
                "status": "ok",
                "value": list(values),
                "error": None,
                "business_status": None,
            },
            "error": None,
        }
    ]


def _team_decision(refs: list[str]) -> dict[str, object]:
    decision_ref, rationale_ref, action_ref, unresolved_ref = refs
    return {
        "schema_version": "team-decision-result/v1",
        "decision": {
            "value": "Aster A is the approved model provider for the Atlas Lantern pilot.",
            "status": "approved",
            "evidence_refs": [decision_ref],
        },
        "rationale": [
            {
                "point": "The team prioritized an auditable boundary and an owned operational path.",
                "evidence_refs": [rationale_ref],
            }
        ],
        "rejected_alternatives": [],
        "actions": [
            {
                "description": "Complete the adapter contract check.",
                "owner": None,
                "deadline": None,
                "status": "completed",
                "evidence_refs": [action_ref],
            }
        ],
        "unresolved_items": [
            {
                "description": "Regional evidence validation remains blocked.",
                "owner": "Chen Rui",
                "deadline": "2026-02-05",
                "status": "blocked",
                "evidence_refs": [unresolved_ref],
            }
        ],
        "uncertainty": {
            "status": "partial",
            "statement": "Regional readiness is not closed.",
            "unknown_fields": ["regional readiness completion"],
            "evidence_refs": [unresolved_ref],
        },
        "evidence_refs": refs,
    }


def _artifact(result: dict[str, object]) -> dict[str, object]:
    return {
        "status": "completed",
        "result": {"team_decision": result},
    }


def _status(value: str = "completed") -> RunStatus:
    return RunStatus(
        run_id="run-1",
        thread_id="thread-1",
        status=value,
        result_ref="results/run-1/result.json" if value == "completed" else None,
    )


def _workspace() -> WorkspacePresentationContext:
    return WorkspacePresentationContext(display_name="Atlas Lantern", document_count=6)


def test_projects_strict_result_into_editorial_claims_and_friendly_citations() -> None:
    refs = ["ev-decision", "ev-rationale", "ev-action", "ev-unresolved"]
    evidence = [
        _evidence(refs[0]),
        _evidence(refs[1], path="03-final-decision.md", line=23, quote="The decision prioritizes an auditable data boundary and an owned operational"),
        _evidence(refs[2], path="04-action-status.md", line=15, quote="- Lin Qiao completed the local adapter contract check on 2026-01-26."),
        _evidence(refs[3], path="06-adoption-readiness.md", line=15, quote="Regional evidence validation is blocked pending legal confirmation. The"),
    ]

    payload = project_run_snapshot(
        query="Which model provider was finally approved?",
        workspace=_workspace(),
        status=_status(),
        result_artifact=_artifact(_team_decision(refs)),
        tool_ledger=_ledger(*evidence),
    )

    assert payload["kind"] == "success"
    assert payload["decision"]["value"].startswith("Aster A")
    assert payload["decision"]["citations"] == ["citation-1"]
    assert payload["rationale"][0]["citations"] == ["citation-2"]
    assert payload["actions"][0]["owner_label"] == "Unassigned"
    assert payload["actions"][0]["deadline_label"] == "Not recorded"
    assert payload["evidence"][0]["label"] == "03-final-decision.md · line 10"
    assert payload["evidence"][0]["ref"] == "ev-decision"
    assert payload["provenance"]["expanded"] is False
    assert [step["state"] for step in payload["provenance"]["steps"]] == [
        "complete",
        "complete",
        "complete",
        "complete",
    ]


def test_full_source_is_exposed_only_when_the_verified_document_still_matches() -> None:
    content = "# Final\n\nThe team selected Aster A.\n"
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    evidence = _evidence(
        "ev-decision",
        line=3,
        quote="The team selected Aster A.",
        content_sha256=content_sha,
    )
    result = {
        **_team_decision(["ev-decision"] * 4),
        "evidence_refs": ["ev-decision"],
    }
    document = NoteDocument(
        relative_path="03-final-decision.md",
        title="Final",
        content=content,
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=len(content.encode("utf-8")),
        content_sha256=content_sha,
        line_count=3,
    )

    payload = project_run_snapshot(
        query="What was decided?",
        workspace=_workspace(),
        status=_status(),
        result_artifact=_artifact(result),
        tool_ledger=_ledger(evidence),
        source_documents=[document],
    )

    source = payload["evidence"][0]["source"]
    assert source["preview_kind"] == "document"
    assert source["lines"][2] == {"number": 3, "text": "The team selected Aster A."}


def test_quote_only_fallback_never_fabricates_surrounding_context() -> None:
    evidence = _evidence("ev-decision")
    result = {
        **_team_decision(["ev-decision"] * 4),
        "evidence_refs": ["ev-decision"],
    }

    payload = project_run_snapshot(
        query="What was decided?",
        workspace=_workspace(),
        status=_status(),
        result_artifact=_artifact(result),
        tool_ledger=_ledger(evidence),
        source_documents=[],
    )

    source = payload["evidence"][0]["source"]
    assert source == {
        "preview_kind": "quote",
        "quote": evidence["quote"],
        "lines": [],
        "fallback_reason": "Full source preview is unavailable; showing the verified excerpt.",
    }


def test_unobserved_claim_evidence_fails_closed() -> None:
    refs = ["ev-decision", "ev-rationale", "ev-action", "ev-unresolved"]

    with pytest.raises(UIProjectionError, match="observed evidence") as caught:
        project_run_snapshot(
            query="What was decided?",
            workspace=_workspace(),
            status=_status(),
            result_artifact=_artifact(_team_decision(refs)),
            tool_ledger=_ledger(_evidence("ev-decision")),
        )

    assert caught.value.code == "UI_EVIDENCE_CONTRACT_ERROR"


def test_invalid_team_decision_fails_closed_as_contract_error() -> None:
    invalid = _team_decision(["ev-decision"] * 4)
    invalid["uncertainty"] = {"status": "none"}

    with pytest.raises(UIProjectionError, match="TeamDecisionResult") as caught:
        project_run_snapshot(
            query="What was decided?",
            workspace=_workspace(),
            status=_status(),
            result_artifact=_artifact(invalid),
            tool_ledger=_ledger(_evidence("ev-decision")),
        )

    assert caught.value.code == "UI_TEAM_DECISION_CONTRACT_ERROR"


@pytest.mark.parametrize(
    ("tool_id", "expected_stage", "states"),
    [
        (None, "searching", ["active", "pending", "pending", "pending"]),
        ("search_notes", "reading", ["complete", "active", "pending", "pending"]),
        ("read_verified_note", "checking", ["complete", "complete", "active", "pending"]),
    ],
)
def test_running_progress_uses_observed_runtime_work(
    tool_id: str | None,
    expected_stage: str,
    states: list[str],
) -> None:
    ledger = [] if tool_id is None else _ledger(_evidence("ev-1"), tool_id=tool_id)

    payload = project_run_snapshot(
        query="What was decided?",
        workspace=_workspace(),
        status=_status("running"),
        tool_ledger=ledger,
    )

    assert payload["kind"] == "running"
    assert payload["run"]["stage"] == expected_stage
    assert [step["state"] for step in payload["provenance"]["steps"]] == states
    if tool_id is None:
        assert payload["evidence"] == []
    else:
        assert payload["evidence"][0]["supports"] == ["Currently reviewing"]
        assert payload["evidence"][0]["source"]["preview_kind"] == "quote"


def test_runtime_failure_is_not_presented_as_insufficient_evidence() -> None:
    status = RunStatus(
        run_id="run-1",
        thread_id="thread-1",
        status="failed",
        error={
            "code": "TEAM_DECISION_CONTRACT_ERROR",
            "category": "validation",
            "message": "The structured result could not be validated.",
        },
    )

    payload = project_run_snapshot(
        query="What was decided?",
        workspace=_workspace(),
        status=status,
    )

    assert payload["kind"] == "error"
    assert payload["decision"] is None
    assert payload["error"]["code"] == "TEAM_DECISION_CONTRACT_ERROR"


def test_runtime_failure_redacts_untrusted_technical_details() -> None:
    status = RunStatus(
        run_id="run-1",
        thread_id="thread-1",
        status="failed",
        error={
            "code": "SOURCE_NOT_FOUND",
            "category": "runtime",
            "message": r"Could not read C:\Users\person\private-vault\decision.md",
        },
    )

    payload = project_run_snapshot(
        query="What was decided?",
        workspace=_workspace(),
        status=status,
    )

    assert payload["error"]["code"] == "SOURCE_NOT_FOUND"
    assert payload["error"]["technical_message"] == (
        "No additional safe details are available."
    )
    assert "private-vault" not in str(payload["error"])
