"""Frozen Slice C production-path characterizations.

The controlled model sees only public case inputs, opaque refs discovered by
the real search tool, and the exact verified values exposed in its Final
request.  Frozen expected fields are loaded only after execution for
assertions; they never enter the model controller.
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
def slice_c_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"slice-c-{uuid4().hex}"
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
    copied_names = set(copy_before)
    assert {Path(path).name for path in case["source_notes"]} <= copied_names
    assert {Path(path).name for path in case["distractor_notes"]} <= copied_names
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
            call_id=f"slice-c-{request.sequence}-{tool_id}",
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


def _select(
    values: list[dict[str, object]],
    label: str,
    predicate,
) -> dict[str, object]:
    candidates = [value for value in values if predicate(value)]
    if not candidates:
        raise ValueError(f"missing visible {label} evidence")
    return max(candidates, key=lambda value: (len(_quote(value)), str(value["evidence_id"])))


def _is_decision_evidence(value: dict[str, object]) -> bool:
    quote = _quote(value).casefold()
    return "selected" in quote or (
        "approved" in quote and ("policy" in quote or "mitigation" in quote)
    )


def _select_decision(values: list[dict[str, object]]) -> dict[str, object]:
    candidates = [value for value in values if _is_decision_evidence(value)]
    if not candidates:
        raise ValueError("missing visible decision evidence")

    def authority_key(value: dict[str, object]) -> tuple[int, int, str]:
        quote = _quote(value).casefold()
        score = 0
        score += 4 if "selected" in quote else 0
        score += 3 if "current" in quote else 0
        score += 2 if "approved" in quote else 0
        score -= 5 if any(term in quote for term in ("discussion", "comment", "draft", "proposed")) else 0
        return score, len(quote), str(value["evidence_id"])

    return max(candidates, key=authority_key)


def _is_independent_rejection_reason(
    value: dict[str, object],
    rejected: dict[str, object],
) -> bool:
    quote = _quote(value).casefold()
    return (
        "audit" in quote
        and "would" in quote
        and value["relative_path"] != rejected["relative_path"]
    )


def _action_status(value: dict[str, object]) -> str | None:
    quote = _quote(value).casefold()
    if "blocked" in quote:
        return "blocked"
    if "in progress" in quote:
        return "in_progress"
    if "pending" in quote:
        return "pending"
    return None


def _source_quotes(value: dict[str, object], values: list[dict[str, object]]) -> list[str]:
    path = value["relative_path"]
    return [_quote(candidate) for candidate in values if candidate["relative_path"] == path]


def _has_owner_cue(value: dict[str, object], values: list[dict[str, object]]) -> bool:
    combined = " ".join(_source_quotes(value, values)).casefold()
    return any(signal in combined for signal in (" owns ", " owner", " assigned"))


def _select_action(
    values: list[dict[str, object]],
    status: str,
) -> dict[str, object]:
    candidates = [value for value in values if _action_status(value) == status]
    if not candidates:
        raise ValueError(f"missing visible {status} action evidence")
    return max(
        candidates,
        key=lambda value: (
            _has_owner_cue(value, values),
            len(_quote(value)),
            str(value["evidence_id"]),
        ),
    )


def _action_subject_tokens(value: dict[str, object]) -> set[str]:
    """Keep only non-status terms that can identify an observed action."""
    ignored = {
        "a", "an", "and", "are", "as", "at", "be", "before", "blocked",
        "by", "can", "cannot", "do", "does", "for", "from", "has", "have",
        "in", "is", "it", "no", "not", "of", "on", "or", "pending",
        "progress", "record", "remains", "status", "still", "that", "the",
        "this", "to", "was", "were", "with", "action",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _quote(value).casefold())
        if token not in ignored
    }


def _same_action_subject(left: dict[str, object], right: dict[str, object]) -> bool:
    return len(_action_subject_tokens(left) & _action_subject_tokens(right)) >= 2


def _asks_for_rejection(question: str) -> bool:
    lowered = question.casefold()
    return "why" in lowered or "reject" in lowered


def _planned_read_refs(values: list[dict[str, object]], question: str) -> list[str]:
    """Choose source refs from observed lexical evidence, without path knowledge."""
    decision = _select_decision(values)
    action = _select_action(values, "blocked")
    selections = [decision, action]
    if _asks_for_rejection(question):
        rejected = _select(
            values,
            "rejected alternative",
            lambda value: "rejected" in _quote(value).casefold()
            and ("because" in _quote(value).casefold() or "would" in _quote(value).casefold()),
        )
        reason = _select(
            values,
            "independent rejection reason",
            lambda value: _is_independent_rejection_reason(value, rejected),
        )
        supersession = _select(
            values,
            "supersession",
            lambda value: "superseded" in _quote(value).casefold(),
        )
        selections.extend([rejected, reason, supersession])

    refs: list[str] = []
    seen_paths: set[str] = set()
    for value in selections:
        path = str(value["relative_path"])
        if path in seen_paths:
            continue
        seen_paths.add(path)
        refs.append(str(value["evidence_id"]))
    return refs


def _owner(value: dict[str, object], values: list[dict[str, object]]) -> str | None:
    quote = _quote(value)
    match = re.search(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:owns|is|are)\b",
        quote,
    )
    if match:
        return match.group(1)
    lowered = quote.casefold()
    if "no assigned owner" in lowered:
        return "unassigned"
    if "no owner was assigned" in lowered:
        return None
    companion_text = " ".join(_source_quotes(value, values)).casefold()
    if "no owner was assigned" in companion_text:
        return None
    return None


def _deadline(value: dict[str, object]) -> str | None:
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", _quote(value))
    return match.group(0) if match else None


def _action_refs(value: dict[str, object], values: list[dict[str, object]]) -> list[str]:
    refs = [str(value["evidence_id"])]
    if _owner(value, values) is None:
        for candidate in values:
            if candidate["relative_path"] == value["relative_path"] and "no owner was assigned" in _quote(candidate).casefold():
                ref = str(candidate["evidence_id"])
                if ref not in refs:
                    refs.append(ref)
                break
    return refs


def _ordered_claim_refs(
    decision: dict[str, object],
    rationale: list[dict[str, object]],
    rejected_alternatives: list[dict[str, object]],
    actions: list[dict[str, object]],
    unresolved_items: list[dict[str, object]],
    uncertainty: dict[str, object],
) -> list[str]:
    refs: list[str] = []
    for claim in [decision, *rationale, *rejected_alternatives, *actions, *unresolved_items, uncertainty]:
        for ref in claim["evidence_refs"]:
            if ref not in refs:
                refs.append(ref)
    return refs


def _source_derived_result(values: list[dict[str, object]], question: str) -> str:
    """Build a strict Final only from visible source values and public intent."""
    decision_value = _select_decision(values)
    action_values: list[dict[str, object]] = []
    for status in ("blocked", "in_progress", "pending"):
        if not any(_action_status(value) == status for value in values):
            continue
        candidate = _select_action(values, status)
        # A source may state the same action as both pending and blocked.  The
        # stronger blocked observation wins; genuinely distinct actions keep
        # their own status even when they share a source note.
        if any(_same_action_subject(candidate, earlier) for earlier in action_values):
            continue
        action_values.append(candidate)
    actions = [
        {
            "description": _quote(value),
            "owner": _owner(value, values),
            "deadline": _deadline(value),
            "status": _action_status(value),
            "evidence_refs": _action_refs(value, values),
        }
        for value in action_values
    ]
    unresolved_items = [dict(action) for action in actions]

    rejected_alternatives: list[dict[str, object]] = []
    rationale: list[dict[str, object]] = []
    if _asks_for_rejection(question):
        rejected = _select(
            values,
            "rejected alternative",
            lambda value: "rejected" in _quote(value).casefold()
            and ("because" in _quote(value).casefold() or "would" in _quote(value).casefold()),
        )
        reason = _select(
            values,
            "rejection reason",
            lambda value: _is_independent_rejection_reason(value, rejected),
        )
        supersession = _select(
            values,
            "supersession",
            lambda value: "superseded" in _quote(value).casefold(),
        )
        rejected_alternatives.append(
            {
                "alternative": _quote(rejected),
                "reason": _quote(reason),
                "evidence_refs": [rejected["evidence_id"], reason["evidence_id"]],
            }
        )
        rationale.extend(
            [
                {"point": _quote(reason), "evidence_refs": [reason["evidence_id"]]},
                {"point": _quote(supersession), "evidence_refs": [supersession["evidence_id"]]},
            ]
        )
    elif actions:
        rationale.append(
            {
                "point": _quote(action_values[0]),
                "evidence_refs": list(actions[0]["evidence_refs"]),
            }
        )

    question_lower = question.casefold()
    decision_status = "partial" if "legal" in question_lower and actions else "approved"
    uncertainty_value = action_values[0] if action_values else decision_value
    unknown_fields: list[str] = []
    if any(action["owner"] in {None, "unassigned"} for action in actions):
        unknown_fields.append("owner")
    if any(action["deadline"] is None for action in actions):
        unknown_fields.append("deadline")
    uncertainty = {
        "status": "partial" if actions else "none",
        "statement": _quote(uncertainty_value) if actions else None,
        "unknown_fields": unknown_fields,
        "evidence_refs": _action_refs(uncertainty_value, values) if actions else [],
    }
    decision = {
        "value": _quote(decision_value),
        "status": decision_status,
        "evidence_refs": [decision_value["evidence_id"]],
    }
    result = {
        "schema_version": "team-decision-result/v1",
        "decision": decision,
        "rationale": rationale,
        "rejected_alternatives": rejected_alternatives,
        "actions": actions,
        "unresolved_items": unresolved_items,
        "uncertainty": uncertainty,
    }
    result["evidence_refs"] = _ordered_claim_refs(
        decision,
        rationale,
        rejected_alternatives,
        actions,
        unresolved_items,
        uncertainty,
    )
    return json.dumps(result, ensure_ascii=False)


def _slice_c_model(case: dict[str, object]) -> FakeModelAdapter:
    """Model policy retains public question + opaque search refs, never Gold."""
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
                    "source_context": {"intent": "synthesize verified decision evidence"},
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


@pytest.mark.parametrize("case_id", ("drm-003", "inc-004"))
def test_slice_c_frozen_cases_synthesize_rejection_and_open_actions_from_visible_evidence(
    slice_c_root: Path,
    case_id: str,
) -> None:
    environment = _environment(slice_c_root / case_id, case_id)
    model = _slice_c_model(environment["case"])
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
    if result["rejected_alternatives"]:
        assert "90-day" in result["rejected_alternatives"][0]["alternative"].casefold()
        assert "audit" in result["rejected_alternatives"][0]["reason"].casefold()

    visible_refs = {str(value["evidence_id"]) for value in visible_values}
    assert set(result["evidence_refs"]) <= visible_refs
    read_paths = {
        record.result["value"]["relative_path"]
        for record in state.tool_ledger
        if record.tool_id == "read_verified_note"
    }
    expected_read_paths = {Path(path).name for path in gold["expected_relevant_notes"]}
    assert read_paths == expected_read_paths
    assert model.call_count == len(state.tool_ledger) + 1
    requests = json.dumps([request.to_dict() for request in model.requests], ensure_ascii=False)
    assert "expected_" not in requests
    assert "expected_" not in inspect.getsource(_slice_c_model)
    assert "expected_" not in inspect.getsource(_source_derived_result)
    _assert_source_unchanged(environment)


@pytest.mark.parametrize("case_id", ("drm-003", "inc-004"))
def test_slice_c_controller_changes_or_fails_when_required_visible_evidence_is_removed(
    slice_c_root: Path,
    case_id: str,
) -> None:
    """The Final policy cannot recreate a rejected reason or action from closure state."""
    environment = _environment(slice_c_root / f"missing-{case_id}", case_id)
    model = _slice_c_model(environment["case"])
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
    if case_id == "drm-003":
        rejected = _select(
            values,
            "rejected alternative",
            lambda value: "rejected" in _quote(value).casefold()
            and ("because" in _quote(value).casefold() or "would" in _quote(value).casefold()),
        )
        without_reason = [
            value
            for value in values
            if not _is_independent_rejection_reason(value, rejected)
        ]
        with pytest.raises(ValueError, match="missing visible rejection reason evidence"):
            _source_derived_result(without_reason, str(environment["case"]["question"]))
    else:
        without_in_progress = [
            value for value in values if _action_status(value) != "in_progress"
        ]
        reduced = json.loads(
            _source_derived_result(without_in_progress, str(environment["case"]["question"]))
        )
        assert "in_progress" not in {item["status"] for item in reduced["actions"]}
    _assert_source_unchanged(environment)
