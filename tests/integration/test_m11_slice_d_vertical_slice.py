"""Frozen Slice D production-path characterizations.

The model controller receives public case inputs and verified tool outputs only.
Frozen expected fields are read after execution exclusively for evaluation.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall, ToolResult


@pytest.fixture
def slice_d_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"slice-d-{uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _dataset_case(case_id: str, *, public_only: bool) -> dict[str, object]:
    dataset = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "requirements"
        / "m1_team_decision_eval_seed"
        / "dataset.jsonl"
    )
    for line in dataset.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("case_id") != case_id:
            continue
        if not public_only:
            return record
        return {
            "case_id": record["case_id"],
            "question": record["user_question"],
            "workspace_id": record["workspace_id"],
            "source_notes": list(record["source_notes"]),
            "distractor_notes": list(record["distractor_notes"]),
        }
    raise AssertionError(f"Frozen {case_id} input is unavailable.")


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(root.rglob("*.md"))
    }


def _environment(root: Path, case_id: str) -> dict[str, object]:
    case = _dataset_case(case_id, public_only=True)
    source = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "requirements"
        / "m1_team_decision_eval_seed"
        / "workspaces"
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
    return ModelAction.tool(
        ToolCall(
            call_id=f"slice-d-{request.sequence}-{tool_id}",
            tool_id=tool_id,
            arguments=arguments,
        )
    )


def _values_from_result(result: ToolResult) -> list[dict[str, object]]:
    assert result.status == "ok"
    raw_values = result.value if isinstance(result.value, list) else [result.value]
    values: list[dict[str, object]] = []
    for value in raw_values:
        assert isinstance(value, dict)
        assert value["status"] == "verified"
        assert isinstance(value["evidence_id"], str)
        assert isinstance(value["relative_path"], str)
        assert isinstance(value["quote"], str) and value["quote"].strip()
        values.append(value)
    return values


def _context_values(context: list[ToolResult]) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    seen_refs: set[str] = set()
    for result in context:
        for value in _values_from_result(result):
            ref = str(value["evidence_id"])
            if ref not in seen_refs:
                seen_refs.add(ref)
                values.append(value)
    return values


def _quote(value: dict[str, object]) -> str:
    quote = value["quote"]
    assert isinstance(quote, str) and quote.strip()
    return quote


def _select(values: list[dict[str, object]], label: str, predicate) -> dict[str, object]:
    candidates = [value for value in values if predicate(value)]
    if not candidates:
        raise ValueError(f"missing visible {label} evidence")
    return max(candidates, key=lambda value: (len(_quote(value)), str(value["evidence_id"])))


def _assignment_intent(question: str) -> bool:
    lowered = question.casefold()
    return "assigned" in lowered or "owner" in lowered or "deadline" in lowered


def _source_quotes(value: dict[str, object], values: list[dict[str, object]]) -> list[str]:
    return [
        _quote(candidate)
        for candidate in values
        if candidate["relative_path"] == value["relative_path"]
    ]


def _owner(value: dict[str, object], values: list[dict[str, object]]) -> str | None:
    quote = _quote(value)
    match = re.search(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+owns\b",
        quote,
    )
    if match:
        return match.group(1)
    lowered = quote.casefold()
    if "unassigned" in lowered:
        return "unassigned"
    if "no named owner" in lowered or "no assignment" in lowered:
        return None
    for companion in _source_quotes(value, values):
        match = re.search(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+owns\b",
            companion,
        )
        if match:
            return match.group(1)
    return None


def _deadline(value: dict[str, object]) -> str | None:
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", _quote(value))
    return match.group(0) if match else None


def _select_blocked_status(values: list[dict[str, object]]) -> dict[str, object]:
    candidates = [value for value in values if "blocked" in _quote(value).casefold()]
    if not candidates:
        raise ValueError("missing visible blocked action evidence")
    return max(candidates, key=lambda value: (len(_quote(value)), str(value["evidence_id"])))


def _action_subject_tokens(value: dict[str, object]) -> set[str]:
    ignored = {
        "a", "an", "and", "are", "as", "at", "be", "because", "blocked",
        "by", "date", "due", "for", "has", "in", "is", "it", "of", "on",
        "or", "owner", "remains", "review", "signed", "status", "the", "to",
        "until", "with",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _quote(value).casefold())
        if token not in ignored
    }


def _select_owner_detail(
    blocked: dict[str, object],
    values: list[dict[str, object]],
) -> dict[str, object]:
    candidates = [
        value
        for value in values
        if _owner(value, values) is not None or _deadline(value) is not None
    ]
    if not candidates:
        raise ValueError("missing visible owner or deadline evidence")
    blocked_tokens = _action_subject_tokens(blocked)
    return max(
        candidates,
        key=lambda value: (
            len(blocked_tokens & _action_subject_tokens(value)),
            _owner(value, values) is not None,
            _deadline(value) is not None,
            len(_quote(value)),
            str(value["evidence_id"]),
        ),
    )


def _select_missing_owner(values: list[dict[str, object]]) -> dict[str, object]:
    candidates = [
        value
        for value in values
        if "no named owner" in _quote(value).casefold()
        or "no assignment" in _quote(value).casefold()
    ]
    if not candidates:
        raise ValueError("missing visible missing owner evidence")

    def missing_owner_key(value: dict[str, object]) -> tuple[int, int, str]:
        quote = _quote(value).casefold()
        score = 3 if "no named owner" in quote else 0
        score += 2 if "no assignment" in quote else 0
        score -= 1 if "suggested" in quote else 0
        return score, len(quote), str(value["evidence_id"])

    return max(candidates, key=missing_owner_key)


def _select_current_policy(values: list[dict[str, object]]) -> dict[str, object]:
    candidates = [
        value
        for value in values
        if any(term in _quote(value).casefold() for term in ("current", "remains", "target"))
        and any(term in _quote(value).casefold() for term in ("approved", "decision", "approach"))
    ]
    if not candidates:
        raise ValueError("missing visible current policy evidence")

    def authority_key(value: dict[str, object]) -> tuple[int, int, str]:
        quote = _quote(value).casefold()
        score = 0
        score += 4 if "current" in quote or "remains" in quote else 0
        score += 3 if "approved" in quote else 0
        score += 2 if "decision" in quote else 0
        score -= 6 if any(term in quote for term in ("draft", "discussion", "comment", "exploratory")) else 0
        return score, len(quote), str(value["evidence_id"])

    return max(candidates, key=authority_key)


def _select_approved_decision(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "approved decision",
        lambda value: "approved" in _quote(value).casefold()
        and any(term in _quote(value).casefold() for term in ("retrieval", "approach", "path"))
        and not any(term in _quote(value).casefold() for term in ("draft", "discussion", "comment")),
    )


def _ordered_claim_refs(*claims: object) -> list[str]:
    refs: list[str] = []
    for claim in claims:
        items = claim if isinstance(claim, list) else [claim]
        for item in items:
            assert isinstance(item, dict)
            for ref in item["evidence_refs"]:
                if ref not in refs:
                    refs.append(ref)
    return refs


def _temporal_result(values: list[dict[str, object]]) -> str:
    current = _select_current_policy(values)
    approved = _select_approved_decision(values)
    supersession = _select(
        values,
        "supersession",
        lambda value: "superseded" in _quote(value).casefold(),
    )
    blocked = _select_blocked_status(values)
    owner_detail = _select_owner_detail(blocked, values)
    action_refs = [blocked["evidence_id"]]
    if owner_detail["evidence_id"] not in action_refs:
        action_refs.append(owner_detail["evidence_id"])
    action = {
        "description": _quote(blocked),
        "owner": _owner(owner_detail, values),
        "deadline": _deadline(owner_detail),
        "status": "blocked",
        "evidence_refs": action_refs,
    }
    decision = {
        "value": _quote(current),
        "status": "partial",
        "evidence_refs": [current["evidence_id"], approved["evidence_id"]],
    }
    rationale = [
        {"point": _quote(supersession), "evidence_refs": [supersession["evidence_id"]]},
        {"point": _quote(approved), "evidence_refs": [approved["evidence_id"]]},
    ]
    rejected = [
        {
            "alternative": _quote(supersession),
            "reason": _quote(approved),
            "evidence_refs": [supersession["evidence_id"], approved["evidence_id"]],
        }
    ]
    uncertainty = {
        "status": "partial",
        "statement": _quote(blocked),
        "unknown_fields": [],
        "evidence_refs": action_refs,
    }
    result = {
        "schema_version": "team-decision-result/v1",
        "decision": decision,
        "rationale": rationale,
        "rejected_alternatives": rejected,
        "actions": [action],
        "unresolved_items": [dict(action)],
        "uncertainty": uncertainty,
    }
    result["evidence_refs"] = _ordered_claim_refs(
        decision,
        rationale,
        rejected,
        [action],
        result["unresolved_items"],
        uncertainty,
    )
    return json.dumps(result, ensure_ascii=False)


def _insufficient_assignment_result(values: list[dict[str, object]]) -> str:
    missing_owner = _select_missing_owner(values)
    unassigned = _select(
        values,
        "explicitly unassigned ownership",
        lambda value: "unassigned" in _quote(value).casefold(),
    )
    suggestion = _select(
        values,
        "suggestion rather than assignment",
        lambda value: "suggested" in _quote(value).casefold(),
    )
    decision = {
        "value": None,
        "status": "insufficient_evidence",
        "evidence_refs": [missing_owner["evidence_id"], unassigned["evidence_id"]],
    }
    rationale = [
        {
            "point": _quote(suggestion),
            "evidence_refs": [suggestion["evidence_id"]],
        }
    ]
    unresolved = [
        {
            "description": _quote(missing_owner),
            "owner": None,
            "deadline": None,
            "status": "pending",
            "evidence_refs": [missing_owner["evidence_id"]],
        },
        {
            "description": _quote(unassigned),
            "owner": "unassigned",
            "deadline": None,
            "status": "unassigned",
            "evidence_refs": [unassigned["evidence_id"], suggestion["evidence_id"]],
        },
    ]
    uncertainty = {
        "status": "insufficient_evidence",
        "statement": _quote(missing_owner),
        "unknown_fields": ["owner", "deadline"],
        "evidence_refs": [missing_owner["evidence_id"], unassigned["evidence_id"]],
    }
    result = {
        "schema_version": "team-decision-result/v1",
        "decision": decision,
        "rationale": rationale,
        "rejected_alternatives": [],
        "actions": [],
        "unresolved_items": unresolved,
        "uncertainty": uncertainty,
    }
    result["evidence_refs"] = _ordered_claim_refs(
        decision,
        rationale,
        [],
        [],
        unresolved,
        uncertainty,
    )
    return json.dumps(result, ensure_ascii=False)


def _planned_read_refs(values: list[dict[str, object]], question: str) -> list[str]:
    if _assignment_intent(question):
        selections = [
            _select_missing_owner(values),
            _select(
                values,
                "suggestion rather than assignment",
                lambda value: "suggested" in _quote(value).casefold(),
            ),
        ]
    else:
        selections = [
            _select(
                values,
                "supersession",
                lambda value: "superseded" in _quote(value).casefold(),
            ),
            _select_approved_decision(values),
            _select_blocked_status(values),
            _select_owner_detail(_select_blocked_status(values), values),
            _select_current_policy(values),
        ]
    refs: list[str] = []
    seen_paths: set[str] = set()
    for value in selections:
        path = str(value["relative_path"])
        if path not in seen_paths:
            seen_paths.add(path)
            refs.append(str(value["evidence_id"]))
    return refs


def _source_derived_result(values: list[dict[str, object]], question: str) -> str:
    if _assignment_intent(question):
        return _insufficient_assignment_result(values)
    return _temporal_result(values)


def _slice_d_model(case: dict[str, object]) -> FakeModelAdapter:
    """Plan reads from observed evidence; Final retains no answer or source map."""
    planned_refs: list[str] = []
    searched = False
    reads_planned = False

    def decide(request: ModelTurnRequest) -> ModelAction:
        nonlocal searched, reads_planned
        if not searched:
            assert request.observation is None
            searched = True
            return _tool(
                request,
                "search_notes",
                {
                    "query": case["question"],
                    "source_context": {"intent": "synthesize verified temporal or assignment evidence"},
                    "limit": 50,
                },
            )
        assert request.observation is not None and request.observation.status == "ok"
        if not reads_planned:
            assert isinstance(request.observation.value, list)
            planned_refs.extend(_planned_read_refs(_values_from_result(request.observation), str(case["question"])))
            reads_planned = True
        if planned_refs:
            return _tool(request, "read_verified_note", {"note_ref": planned_refs.pop(0)})
        return ModelAction.final(_source_derived_result(_context_values(request.evidence_context), str(case["question"])))

    return FakeModelAdapter([decide] * 6)


def _engine(environment: dict[str, object], model: FakeModelAdapter) -> RuntimeEngine:
    root = environment["root"]
    return RuntimeEngine(
        vault_root=environment["vault"],
        index_path=environment["index"],
        checkpoint_dir=root / "checkpoints",
        trace_dir=root / "traces",
        checkpointer=InMemoryCheckpointer(),
        model=model,
    )


def _result(root: Path, run_id: str) -> dict[str, object]:
    return json.loads((root / "checkpoints" / "results" / run_id / "result.json").read_text(encoding="utf-8"))


def _assert_source_unchanged(environment: dict[str, object]) -> None:
    assert _file_hashes(environment["source"]) == environment["source_before"]
    assert _file_hashes(environment["vault"]) == environment["copy_before"]


@pytest.mark.parametrize("case_id", ("ret-005", "iti-005"))
def test_slice_d_frozen_cases_preserve_current_authority_and_insufficient_assignment(
    slice_d_root: Path,
    case_id: str,
) -> None:
    environment = _environment(slice_d_root / case_id, case_id)
    model = _slice_d_model(environment["case"])
    engine = _engine(environment, model)
    status = engine.start_multi_agent(
        RunRequest(
            request_id=f"m11-{case_id}",
            thread_id=f"m11-{case_id}",
            workflow="team_decision",
            query=environment["case"]["question"],
            max_steps=8,
            max_provider_requests=8,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(f"m11-{case_id}")

    assert status.status == "completed", status.error
    assert state is not None
    final_request = model.requests[-1]
    visible_values = _context_values(final_request.evidence_context)
    result = _result(environment["root"], status.run_id)["result"]["team_decision"]
    gold = _dataset_case(case_id, public_only=False)

    assert result["decision"]["status"] == gold["expected_decision"]["status"]
    assert len(result["rejected_alternatives"]) == len(gold["expected_rejected_alternatives"])
    assert {
        (item["owner"], item["deadline"], item["status"])
        for item in result["actions"]
    } == {
        (item["owner"], item["deadline"], item["status"])
        for item in gold["expected_action_items"]
    }
    assert {
        (item["owner"], item["deadline"], item["status"])
        for item in result["unresolved_items"]
    } == {
        (item["owner"], item["deadline"], item["status"])
        for item in gold["expected_unresolved_items"]
    }
    if result["decision"]["value"] is None:
        assert result["actions"] == []
        assert result["uncertainty"]["status"] == "insufficient_evidence"
    else:
        assert "current" in result["decision"]["value"].casefold() or "remains" in result["decision"]["value"].casefold()
        assert "superseded" in result["rejected_alternatives"][0]["alternative"].casefold()

    visible_refs = {str(value["evidence_id"]) for value in visible_values}
    assert set(result["evidence_refs"]) <= visible_refs
    read_paths = {
        record.result["value"]["relative_path"]
        for record in state.tool_ledger
        if record.tool_id == "read_verified_note"
    }
    expected_read_paths = {Path(path).name for path in gold["expected_relevant_notes"]}
    assert read_paths == expected_read_paths
    requests = json.dumps([request.to_dict() for request in model.requests], ensure_ascii=False)
    assert "expected_" not in requests
    controller_source = inspect.getsource(_slice_d_model) + inspect.getsource(_source_derived_result)
    for forbidden in (
        "expected_",
        "ret-005",
        "iti-005",
        "Yuan Li",
        "Mira Sol",
        "February 3",
        "February 14",
    ):
        assert forbidden not in controller_source
    _assert_source_unchanged(environment)


@pytest.mark.parametrize("case_id", ("ret-005", "iti-005"))
def test_slice_d_controller_fails_when_its_required_visible_evidence_is_removed(
    slice_d_root: Path,
    case_id: str,
) -> None:
    """A current-policy or insufficient-assignment result cannot survive hidden evidence."""
    environment = _environment(slice_d_root / f"missing-{case_id}", case_id)
    model = _slice_d_model(environment["case"])
    status = _engine(environment, model).start_multi_agent(
        RunRequest(
            request_id=f"m11-{case_id}-missing-visible",
            thread_id=f"m11-{case_id}-missing-visible",
            workflow="team_decision",
            query=environment["case"]["question"],
            max_steps=8,
            max_provider_requests=8,
            dry_run=True,
        )
    )

    assert status.status == "completed", status.error
    values = _context_values(model.requests[-1].evidence_context)
    question = str(environment["case"]["question"])
    if _assignment_intent(question):
        without_unassigned = [
            value for value in values if "unassigned" not in _quote(value).casefold()
        ]
        with pytest.raises(ValueError, match="missing visible explicitly unassigned ownership evidence"):
            _source_derived_result(without_unassigned, question)
    else:
        without_supersession = [
            value for value in values if "superseded" not in _quote(value).casefold()
        ]
        with pytest.raises(ValueError, match="missing visible supersession evidence"):
            _source_derived_result(without_supersession, question)
    _assert_source_unchanged(environment)
