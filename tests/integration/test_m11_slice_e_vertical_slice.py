"""Frozen Slice E production-path characterizations.

The controlled model receives public case inputs and verified tool outputs only.
Frozen expected fields are read after execution exclusively for evaluation.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import inspect
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from tests.runtime_fixtures import offline_retrieval, source_context_from_request

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall, ToolResult


@pytest.fixture
def slice_e_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"slice-e-{uuid4().hex}"
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
            call_id=f"slice-e-{request.sequence}-{tool_id}",
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


def _source_text(value: dict[str, object], values: list[dict[str, object]]) -> str:
    return "\n".join(
        _quote(candidate)
        for candidate in values
        if candidate["relative_path"] == value["relative_path"]
    )


def _select(values: list[dict[str, object]], label: str, predicate) -> dict[str, object]:
    candidates = [value for value in values if predicate(value)]
    if not candidates:
        raise ValueError(f"missing visible {label} evidence")
    return max(candidates, key=lambda value: (len(_quote(value)), str(value["evidence_id"])))


def _first_rollout_intent(question: str) -> bool:
    return "first rollout" in question.casefold()


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


def _select_policy(values: list[dict[str, object]]) -> dict[str, object]:
    candidates = [
        value
        for value in values
        if (
            "active" in _quote(value).casefold()
            and "archiv" in _quote(value).casefold()
            and any(term in _quote(value).casefold() for term in ("approved", "retain", "policy"))
        )
    ]
    if not candidates:
        raise ValueError("missing visible approved retention policy evidence")
    return max(
        candidates,
        key=lambda value: (
            "approved" in _quote(value).casefold(),
            "current" in _quote(value).casefold(),
            len(_quote(value)),
            str(value["evidence_id"]),
        ),
    )


def _select_review(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "independent review rationale",
        lambda value: (
            "review" in str(value["relative_path"]).casefold()
            and any(
                term in _quote(value).casefold()
                for term in ("would", "because", "cannot", "risk")
            )
        ),
    )


def _select_policy_continuation(
    policy: dict[str, object],
    values: list[dict[str, object]],
) -> dict[str, object]:
    candidates = [
        value
        for value in values
        if (
            value["relative_path"] == policy["relative_path"]
            and value["evidence_id"] != policy["evidence_id"]
            and "policy" in _quote(value).casefold()
        )
    ]
    if not candidates:
        raise ValueError("missing visible approved policy continuation evidence")
    return max(
        candidates,
        key=lambda value: (
            bool(re.search(r"\b\d+\s+days?\b", _quote(value), flags=re.IGNORECASE)),
            "legal" not in _quote(value).casefold(),
            len(_quote(value)),
            str(value["evidence_id"]),
        ),
    )


def _select_rejected_alternative(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "explicitly rejected alternative",
        lambda value: any(
            term in _quote(value).casefold()
            for term in ("rejected", "not accepted", "declined")
        ),
    )


def _select_completed_preparation(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "completed preparation",
        lambda value: (
            "mapping" in _quote(value).casefold()
            and "dry run" in _quote(value).casefold()
            and "complet" in _quote(value).casefold()
        ),
    )


def _select_blocked_legal(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "blocked legal approval",
        lambda value: (
            "legal" in _source_text(value, values).casefold()
            and "blocked" in _quote(value).casefold()
            and "deadline" in _quote(value).casefold()
        ),
    )


def _select_pending_exception(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "pending exception work",
        lambda value: "exception" in _quote(value).casefold() and "pending" in _quote(value).casefold(),
    )


def _select_no_deletion_boundary(values: list[dict[str, object]]) -> dict[str, object]:
    candidates = [
        value
        for value in values
        if (
            "not actual deletion" in _quote(value).casefold()
            or "no deletion job is authorized" in _quote(value).casefold()
        )
    ]
    if not candidates:
        raise ValueError("missing visible preparation is not deletion boundary evidence")
    return max(
        candidates,
        key=lambda value: (
            "not actual deletion" in _quote(value).casefold(),
            len(_quote(value)),
            str(value["evidence_id"]),
        ),
    )


def _select_separate_scope(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "separate scope",
        lambda value: "tracked separately" in _quote(value).casefold(),
    )


def _select_unassigned_ownership(values: list[dict[str, object]]) -> dict[str, object]:
    return _select(
        values,
        "explicitly unassigned ownership",
        lambda value: "no assigned owner" in _quote(value).casefold(),
    )


def _completed_descriptions(value: dict[str, object]) -> list[str]:
    quote = _quote(value)
    mapping = re.search(r"([^.]*(?:mapping)[^.]*?complet(?:e|ed)[^.]*)", quote, flags=re.IGNORECASE)
    dry_run = re.search(r"([^.]*(?:dry run)[^.]*?complet(?:e|ed)[^.]*)", quote, flags=re.IGNORECASE)
    if not mapping or not dry_run:
        raise ValueError("missing visible distinct completed checkpoint evidence")
    return [mapping.group(1).strip(), dry_run.group(1).strip()]


def _migration_result(values: list[dict[str, object]]) -> str:
    policy = _select_policy(values)
    policy_continuation = _select_policy_continuation(policy, values)
    review = _select_review(values)
    alternative = _select_rejected_alternative(values)
    completed = _select_completed_preparation(values)
    legal = _select_blocked_legal(values)
    pending = _select_pending_exception(values)
    no_deletion = _select_no_deletion_boundary(values)
    completed_actions = [
        {
            "description": description,
            "owner": None,
            "deadline": None,
            "status": "completed",
            "evidence_refs": [completed["evidence_id"]],
        }
        for description in _completed_descriptions(completed)
    ]
    blocked_action = {
        "description": _quote(legal),
        "owner": None,
        "deadline": None,
        "status": "blocked",
        "evidence_refs": [legal["evidence_id"]],
    }
    pending_action = {
        "description": _quote(pending),
        "owner": None,
        "deadline": None,
        "status": "pending",
        "evidence_refs": [pending["evidence_id"]],
    }
    decision = {
        "value": f"{_quote(policy)} {_quote(policy_continuation)}",
        "status": "partial",
        "evidence_refs": [
            policy["evidence_id"],
            policy_continuation["evidence_id"],
            completed["evidence_id"],
            legal["evidence_id"],
            no_deletion["evidence_id"],
        ],
    }
    rationale = [
        {"point": _quote(review), "evidence_refs": [review["evidence_id"]]},
        {"point": _quote(completed), "evidence_refs": [completed["evidence_id"]]},
        {"point": _quote(no_deletion), "evidence_refs": [no_deletion["evidence_id"]]},
        {"point": _quote(legal), "evidence_refs": [legal["evidence_id"]]},
        {"point": _quote(pending), "evidence_refs": [pending["evidence_id"]]},
    ]
    rejected = [
        {
            "alternative": _quote(alternative),
            "reason": _quote(review),
            "evidence_refs": [alternative["evidence_id"], review["evidence_id"]],
        }
    ]
    actions = [*completed_actions, blocked_action, pending_action]
    unresolved = [dict(blocked_action), dict(pending_action)]
    uncertainty = {
        "status": "partial",
        "statement": _quote(legal),
        "unknown_fields": ["owner", "deadline"],
        "evidence_refs": [legal["evidence_id"], pending["evidence_id"]],
    }
    result = {
        "schema_version": "team-decision-result/v1",
        "decision": decision,
        "rationale": rationale,
        "rejected_alternatives": rejected,
        "actions": actions,
        "unresolved_items": unresolved,
        "uncertainty": uncertainty,
    }
    result["evidence_refs"] = _ordered_claim_refs(
        decision, rationale, rejected, actions, unresolved, uncertainty
    )
    return json.dumps(result, ensure_ascii=False)


def _rollout_result(values: list[dict[str, object]]) -> str:
    scope = _select_separate_scope(values)
    unassigned = _select_unassigned_ownership(values)
    action = {
        "description": _quote(unassigned),
        "owner": "unassigned",
        "deadline": None,
        "status": "unassigned",
        "evidence_refs": [unassigned["evidence_id"]],
    }
    decision = {
        "value": _quote(scope),
        "status": "partial",
        "evidence_refs": [scope["evidence_id"], unassigned["evidence_id"]],
    }
    rationale = [
        {"point": _quote(scope), "evidence_refs": [scope["evidence_id"]]},
        {"point": _quote(unassigned), "evidence_refs": [unassigned["evidence_id"]]},
    ]
    uncertainty = {
        "status": "partial",
        "statement": _quote(scope),
        "unknown_fields": ["completion", "owner", "deadline"],
        "evidence_refs": [scope["evidence_id"], unassigned["evidence_id"]],
    }
    result = {
        "schema_version": "team-decision-result/v1",
        "decision": decision,
        "rationale": rationale,
        "rejected_alternatives": [],
        "actions": [action],
        "unresolved_items": [dict(action)],
        "uncertainty": uncertainty,
    }
    result["evidence_refs"] = _ordered_claim_refs(
        decision, rationale, [], [action], result["unresolved_items"], uncertainty
    )
    return json.dumps(result, ensure_ascii=False)


def _planned_read_refs(values: list[dict[str, object]], question: str) -> list[str]:
    if _first_rollout_intent(question):
        selections = [_select_separate_scope(values), _select_unassigned_ownership(values)]
    else:
        selections = [
            _select_policy(values),
            _select_review(values),
            _select_completed_preparation(values),
            _select_blocked_legal(values),
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
    if _first_rollout_intent(question):
        return _rollout_result(values)
    return _migration_result(values)


def _slice_e_model(case: dict[str, object]) -> FakeModelAdapter:
    """Plan reads from observations; Final retains no answer or source map."""

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
                    "source_context": source_context_from_request(request),
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

    return FakeModelAdapter([decide] * 8)


def _controller_source() -> str:
    """Cover every helper reachable from the controlled model's decision path."""
    helpers = (
        _tool,
        _values_from_result,
        _context_values,
        _quote,
        _source_text,
        _select,
        _first_rollout_intent,
        _ordered_claim_refs,
        _select_policy,
        _select_review,
        _select_policy_continuation,
        _select_rejected_alternative,
        _select_completed_preparation,
        _select_blocked_legal,
        _select_pending_exception,
        _select_no_deletion_boundary,
        _select_separate_scope,
        _select_unassigned_ownership,
        _completed_descriptions,
        _migration_result,
        _rollout_result,
        _planned_read_refs,
        _source_derived_result,
        _slice_e_model,
    )
    return "\n".join(inspect.getsource(helper) for helper in helpers)


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
