"""Slice B RED characterization for the frozen ``aer-002`` multi-note path.

This first test deliberately proves only the runtime budget boundary.  It does
not use any ``expected_*``/Gold field or claim that the final neutral result is
the user-facing Slice B answer.  Later RED tests will separately prove the
model-visible context and semantic grounding requirements.
"""

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
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.checkpoint import InMemoryCheckpointer, SQLiteCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall, ToolResult


class ProcessCrash(BaseException):
    """Test-only interruption that preserves the last durable checkpoint."""


class CrashOnDurableFinal(SQLiteCheckpointer):
    """Persist Slice B's final context-bearing request/response, then stop."""

    def save(self, state, checkpoint_id=None):
        result = super().save(state, checkpoint_id)
        latest = state.model_executions[-1] if state.model_executions else None
        if (
            latest is not None
            and latest.status == "response_durable"
            and latest.normalized_action is not None
            and latest.normalized_action.get("kind") == "final"
        ):
            raise ProcessCrash("durable_aer_002_final")
        return result


@pytest.fixture
def aer_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"aer-002-{uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _seed_inputs() -> dict[str, object]:
    """Load only the public frozen input fields used by production."""
    dataset = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "requirements"
        / "m1_team_decision_eval_seed"
        / "dataset.jsonl"
    )
    for line in dataset.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case.get("case_id") == "aer-002":
            return {
                "question": case["user_question"],
                "workspace_id": case["workspace_id"],
                "source_notes": list(case["source_notes"]),
                "distractor_notes": list(case["distractor_notes"]),
            }
    raise AssertionError("Frozen aer-002 input is unavailable.")


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(root.rglob("*.md"))
    }


def _environment(root: Path) -> dict[str, object]:
    case = _seed_inputs()
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
            call_id=f"aer-002-{request.sequence}-{tool_id}",
            tool_id=tool_id,
            arguments=arguments,
        )
    )


def _neutral_final(observation: dict[str, object]) -> str:
    """A valid non-semantic terminal contract used only to isolate the budget RED."""
    ref = observation["evidence_id"]
    relative_path = observation["relative_path"]
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": None,
                "status": "insufficient_evidence",
                "evidence_refs": [],
            },
            "rationale": [],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "insufficient_evidence",
                "statement": (
                    "The budget characterization reached verified evidence from "
                    f"{relative_path}."
                ),
                "unknown_fields": [],
                "evidence_refs": [ref],
            },
            "evidence_refs": [ref],
        },
        ensure_ascii=False,
    )


def _context_values(context: list[ToolResult]) -> list[dict[str, object]]:
    """Expose only verified values that the current model request can inspect."""
    values: list[dict[str, object]] = []
    for result in context:
        assert result.status == "ok"
        assert result.tool_id in {"search_notes", "read_verified_note"}
        raw_values = result.value if isinstance(result.value, list) else [result.value]
        for value in raw_values:
            assert isinstance(value, dict)
            assert value["status"] == "verified"
            values.append(value)
    return values


def _unique_context_values(context: list[ToolResult]) -> list[dict[str, object]]:
    """Deduplicate only repeated observations of the same visible evidence ID."""
    values: list[dict[str, object]] = []
    seen_refs: set[str] = set()
    for value in _context_values(context):
        ref = value["evidence_id"]
        assert isinstance(ref, str)
        if ref not in seen_refs:
            seen_refs.add(ref)
            values.append(value)
    return values


def _quote(value: dict[str, object]) -> str:
    quote = value["quote"]
    assert isinstance(quote, str) and quote.strip()
    return quote


def _action_status(value: dict[str, object]) -> str | None:
    """Use generic status signals in an observed quote, never a case answer map."""
    quote = _quote(value).casefold()
    if "cannot" in quote or "blocked" in quote:
        return "blocked"
    if "unassigned" in quote or "no assigned owner" in quote:
        return "unassigned"
    if "in progress" in quote or re.search(r"\b(?:is|are)\s+[a-z]+ing\b", quote):
        return "in_progress"
    return None


def _select_visible_value(
    values: list[dict[str, object]],
    label: str,
    predicate,
) -> dict[str, object]:
    candidates = [value for value in values if predicate(value)]
    if not candidates:
        raise ValueError(f"missing visible {label} evidence")
    return max(
        candidates,
        key=lambda value: (len(_quote(value)), str(value["evidence_id"])),
    )


def _owner_from_quote(value: dict[str, object]) -> str:
    match = re.match(
        r"\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:is|are)\s+[a-z]+ing\b",
        _quote(value),
    )
    return match.group(1) if match else "unassigned"


def _source_derived_result_from_values(values: list[dict[str, object]]) -> str:
    """Produce a controlled Final solely from visible source quotes and refs.

    The policy is deliberately generic: it identifies a release statement, a
    diagnostic statement from a different source, and one visible action for
    each contract status class.  It contains no frozen paths, quote fragments,
    owner names, or target-answer literals.
    """
    decision = _select_visible_value(
        values,
        "release decision",
        lambda value: all(token in _quote(value).casefold() for token in ("will", "gate", "release")),
    )
    actions_by_status = {
        status: _select_visible_value(
            values,
            status,
            lambda value, expected=status: _action_status(value) == expected,
        )
        for status in ("in_progress", "unassigned", "blocked")
    }
    used_paths = {
        decision["relative_path"],
        *(value["relative_path"] for value in actions_by_status.values()),
    }
    diagnostic = _select_visible_value(
        values,
        "independent diagnostic",
        lambda value: (
            "diagnostic" in _quote(value).casefold()
            and value["relative_path"] not in used_paths
        ),
    )
    if len({value["relative_path"] for value in [decision, diagnostic, *actions_by_status.values()]}) < 4:
        raise ValueError("missing four independent visible evidence sources")

    actions = [
        {
            "description": _quote(value),
            "owner": _owner_from_quote(value) if status == "in_progress" else "unassigned",
            "deadline": None,
            "status": status,
            "evidence_refs": [value["evidence_id"]],
        }
        for status, value in actions_by_status.items()
    ]
    unresolved_items = [
        {
            "description": action["description"],
            "owner": action["owner"],
            "deadline": action["deadline"],
            "status": action["status"],
            "evidence_refs": action["evidence_refs"],
        }
        for action in actions
    ]
    evidence_refs = [
        decision["evidence_id"],
        diagnostic["evidence_id"],
        *(value["evidence_id"] for value in actions_by_status.values()),
    ]
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": _quote(decision),
                "status": "partial",
                "evidence_refs": [decision["evidence_id"]],
            },
            "rationale": [{
                "point": _quote(diagnostic),
                "evidence_refs": [diagnostic["evidence_id"]],
            }],
            "rejected_alternatives": [],
            "actions": actions,
            "unresolved_items": unresolved_items,
            "uncertainty": {
                "status": "partial",
                "statement": _quote(actions_by_status["blocked"]),
                "unknown_fields": [],
                "evidence_refs": [actions_by_status["blocked"]["evidence_id"]],
            },
            "evidence_refs": evidence_refs,
        },
        ensure_ascii=False,
    )


def _source_derived_result_from_visible_context(context: list[ToolResult]) -> str:
    return _source_derived_result_from_values(_unique_context_values(context))


def _multi_note_budget_model(case: dict[str, object]) -> FakeModelAdapter:
    """Choose one search and four reads from real observations, without Gold data.

    The controller retains only opaque evidence IDs discovered in the preceding
    successful search.  It does not retain source quotes or any business answer.
    """

    planned_refs: list[str] = []

    def search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _tool(
            request,
            "search_notes",
            {
                "query": case["question"],
                "source_context": {"intent": "current evaluation rollout boundary"},
                "limit": 20,
            },
        )

    def first_read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, list)
        first_by_path: dict[str, str] = {}
        for evidence in request.observation.value:
            first_by_path.setdefault(evidence["relative_path"], evidence["evidence_id"])
        planned_refs.extend(first_by_path.values())
        assert len(planned_refs) >= 4
        return _tool(request, "read_verified_note", {"note_ref": planned_refs.pop(0)})

    def later_read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, dict)
        return _tool(request, "read_verified_note", {"note_ref": planned_refs.pop(0)})

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, dict)
        return ModelAction.final(_neutral_final(request.observation.value))

    return FakeModelAdapter([search, first_read, later_read, later_read, later_read, final])


def _multi_note_context_model(case: dict[str, object]) -> FakeModelAdapter:
    """Use only opaque observed refs; derive Final fields from visible context."""

    planned_refs: list[str] = []

    def search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _tool(
            request,
            "search_notes",
            {
                "query": case["question"],
                "source_context": {"intent": "synthesize verified evidence"},
                "limit": 50,
            },
        )

    def first_read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, list)
        seen_paths: set[str] = set()
        for evidence in request.observation.value:
            path = evidence["relative_path"]
            if path not in seen_paths:
                seen_paths.add(path)
                planned_refs.append(evidence["evidence_id"])
            if len(planned_refs) == 4:
                break
        assert len(planned_refs) == 4
        return _tool(request, "read_verified_note", {"note_ref": planned_refs.pop(0)})

    def later_read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, dict)
        return _tool(request, "read_verified_note", {"note_ref": planned_refs.pop(0)})

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, dict)
        return ModelAction.final(
            _source_derived_result_from_visible_context(request.evidence_context)
        )

    return FakeModelAdapter([search, first_read, later_read, later_read, later_read, final])


def _engine(
    environment: dict[str, object],
    model: FakeModelAdapter,
    *,
    checkpointer=None,
) -> RuntimeEngine:
    root = environment["root"]
    return RuntimeEngine(
        vault_root=environment["vault"],
        index_path=environment["index"],
        checkpoint_dir=root / "checkpoints",
        trace_dir=root / "traces",
        checkpointer=checkpointer or InMemoryCheckpointer(),
        model=model,
    )


def _start(engine: RuntimeEngine, environment: dict[str, object], thread_id: str):
    return engine.start_multi_agent(
        RunRequest(
            request_id="m11-aer-002",
            thread_id=thread_id,
            workflow="team_decision",
            query=environment["case"]["question"],
            max_steps=8,
            max_provider_requests=8,
            dry_run=True,
        )
    )


def _assert_source_unchanged(environment: dict[str, object]) -> None:
    assert _file_hashes(environment["source"]) == environment["source_before"]
    assert _file_hashes(environment["vault"]) == environment["copy_before"]


def _result(root: Path, run_id: str) -> dict[str, object]:
    path = root / "checkpoints" / "results" / run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _over_cap_environment(root: Path) -> dict[str, object]:
    """Build a private non-Gold workspace with ten independently searchable refs."""
    vault = root / "vault"
    vault.mkdir(parents=True)
    for index in range(10):
        (vault / f"probe-{index}.md").write_text(
            f"# Bounded context probe {index}\nprobe-{index}\n",
            encoding="utf-8",
        )
    return {
        "root": root,
        "vault": vault,
        "index": scan_vault(vault, root / "scan").index_path,
    }


def _over_cap_reference_model() -> FakeModelAdapter:
    """Deliberately cite an old observed ref that a bounded final prompt cannot see."""
    observed_refs: list[str] = []

    def decide(request: ModelTurnRequest) -> ModelAction:
        if request.observation is not None:
            assert request.observation.status == "ok"
            assert isinstance(request.observation.value, list)
            observed_refs.append(request.observation.value[0]["evidence_id"])
        if request.sequence <= 10:
            return _tool(
                request,
                "search_notes",
                {
                    "query": f"probe-{request.sequence - 1}",
                    "source_context": {"intent": "bounded context visibility"},
                    "limit": 1,
                },
            )
        assert len(observed_refs) == 10
        omitted_ref = observed_refs[8]
        return ModelAction.final(
            json.dumps(
                {
                    "schema_version": "team-decision-result/v1",
                    "decision": {
                        "value": "Bounded context visibility probe.",
                        "status": "partial",
                        "evidence_refs": [omitted_ref],
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
                    "evidence_refs": [omitted_ref],
                },
                ensure_ascii=False,
            )
        )

    return FakeModelAdapter([decide] * 11)


def test_team_decision_rejects_a_claim_ref_omitted_from_the_final_model_request(
    aer_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim grounding must use the Final request, not all historical ledger entries."""
    environment = _over_cap_environment(aer_root / "over-cap")
    original_search = RuntimeAgentAdapter._search_notes
    search_index = 0

    def unique_verified_search(self, query, source_context, limit):
        nonlocal search_index
        values = original_search(self, query, source_context, limit)
        assert len(values) == 1
        unique_value = dict(values[0])
        unique_value["evidence_id"] = f"{unique_value['evidence_id']}-{search_index}"
        search_index += 1
        self._evidence_by_id[unique_value["evidence_id"]] = unique_value
        return [unique_value]

    monkeypatch.setattr(RuntimeAgentAdapter, "_search_notes", unique_verified_search)
    model = _over_cap_reference_model()
    engine = _engine(environment, model)

    status = engine.start_multi_agent(
        RunRequest(
            request_id="m11-over-cap-visible-refs",
            thread_id="m11-over-cap-visible-refs",
            workflow="team_decision",
            query="bounded context visibility",
            max_steps=11,
            max_provider_requests=11,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(status.thread_id)

    assert status.status == "failed", status.error
    assert status.error["code"] == "TEAM_DECISION_CONTRACT_ERROR"
    assert model.call_count == 11
    final_request = model.requests[-1]
    final_action = state.model_executions[-1].normalized_action
    claim_ref = json.loads(final_action["final_answer"])["decision"]["evidence_refs"][0]
    final_visible_refs = {
        value["evidence_id"]
        for value in _context_values(final_request.evidence_context)
    }
    final_visible_refs.add(final_request.observation.value[0]["evidence_id"])
    assert claim_ref not in final_visible_refs


def test_aer_002_allows_a_bounded_multi_note_trajectory_through_the_production_runtime(
    aer_root: Path,
) -> None:
    """A six-turn S-4R-F trajectory must not inherit Slice A's three-turn cap."""
    environment = _environment(aer_root)
    model = _multi_note_budget_model(environment["case"])
    engine = _engine(environment, model)

    status = engine.start_multi_agent(
        RunRequest(
            request_id="m11-aer-002-budget",
            thread_id="m11-aer-002-budget",
            workflow="team_decision",
            query=environment["case"]["question"],
            max_steps=8,
            max_provider_requests=8,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(status.thread_id)

    assert status.status == "completed", status.error
    assert model.call_count == 6
    assert [record.tool_id for record in state.tool_ledger] == [
        "search_notes",
        "read_verified_note",
        "read_verified_note",
        "read_verified_note",
        "read_verified_note",
    ]
    read_paths = [
        record.result["value"]["relative_path"]
        for record in state.tool_ledger
        if record.tool_id == "read_verified_note"
    ]
    assert len(set(read_paths)) == 4
    requests = json.dumps([request.to_dict() for request in model.requests], ensure_ascii=False)
    assert "expected_" not in requests
    _assert_source_unchanged(environment)


def test_aer_002_synthesizes_multiple_verified_notes_visible_to_the_final_model_turn(
    aer_root: Path,
) -> None:
    """The model may not synthesize earlier evidence from a hidden FakeModel closure."""
    environment = _environment(aer_root)
    model = _multi_note_context_model(environment["case"])
    engine = _engine(environment, model)

    status = engine.start_multi_agent(
        RunRequest(
            request_id="m11-aer-002-context",
            thread_id="m11-aer-002-context",
            workflow="team_decision",
            query=environment["case"]["question"],
            max_steps=8,
            max_provider_requests=8,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(status.thread_id)

    assert status.status == "completed", status.error
    assert model.call_count == 6
    final_context = model.requests[-1].evidence_context
    visible_values = _context_values(final_context)
    visible_paths = {value["relative_path"] for value in visible_values}
    assert {
        "02-metric-design-review.md",
        "03-rollout-decision.md",
        "04-owner-status.md",
        "06-threshold-review.md",
    } <= visible_paths
    assert [record.tool_id for record in state.tool_ledger] == [
        "search_notes",
        "read_verified_note",
        "read_verified_note",
        "read_verified_note",
        "read_verified_note",
    ]

    result = _result(environment["root"], status.run_id)["result"]["team_decision"]
    assert result["decision"]["status"] == "partial"
    assert "deterministic metrics" in result["decision"]["value"].casefold()
    action_states = {(item["owner"], item["status"]) for item in result["actions"]}
    assert action_states == {
        ("Jae Min", "in_progress"),
        ("unassigned", "unassigned"),
        ("unassigned", "blocked"),
    }
    visible_refs = {value["evidence_id"] for value in visible_values}
    assert set(result["evidence_refs"]) <= visible_refs
    requests = json.dumps([request.to_dict() for request in model.requests], ensure_ascii=False)
    assert "expected_" not in requests
    _assert_source_unchanged(environment)


def test_aer_002_context_policy_fails_when_a_required_visible_evidence_class_is_removed(
    aer_root: Path,
) -> None:
    """The controlled Final has no closure-held answer when context is incomplete."""
    environment = _environment(aer_root / "missing-visible-class")
    model = _multi_note_context_model(environment["case"])
    status = _start(_engine(environment, model), environment, "m11-aer-002-missing-class")

    assert status.status == "completed", status.error
    final_context = model.requests[-1].evidence_context
    remaining_values = [
        value
        for value in _unique_context_values(final_context)
        if _action_status(value) != "blocked"
    ]
    with pytest.raises(ValueError, match="missing visible blocked evidence"):
        _source_derived_result_from_values(remaining_values)
    _assert_source_unchanged(environment)


def test_aer_002_cold_resume_reuses_the_durable_context_bearing_final(
    aer_root: Path,
) -> None:
    """A restart reuses the exact final request without replaying evidence or model work."""
    fresh_environment = _environment(aer_root / "fresh")
    fresh_model = _multi_note_context_model(fresh_environment["case"])
    fresh = _engine(fresh_environment, fresh_model)
    fresh_status = _start(fresh, fresh_environment, "m11-aer-002-fresh")
    fresh_result = _result(fresh_environment["root"], fresh_status.run_id)["result"]["team_decision"]

    environment = _environment(aer_root / "resume")
    before = _multi_note_context_model(environment["case"])
    first = _engine(
        environment,
        before,
        checkpointer=CrashOnDurableFinal(environment["root"] / "checkpoints"),
    )
    with pytest.raises(ProcessCrash):
        _start(first, environment, "m11-aer-002-resume")

    durable = first.checkpointer.get_latest("m11-aer-002-resume")
    assert durable is not None
    final_record = durable.model_executions[-1]
    assert final_record.status == "response_durable"
    assert final_record.normalized_action is not None
    assert final_record.normalized_action["kind"] == "final"
    assert before.call_count == 6
    old_ledger = [record.to_dict() for record in durable.tool_ledger]
    durable_request = ModelArtifactStore(environment["root"] / "checkpoints" / "models").read(
        final_record.request_ref,
        expected_sha256=final_record.request_sha256,
    )["model_request"]
    durable_context = durable_request["evidence_context"]
    assert len(durable_context) == 5
    assert all(result["status"] == "ok" for result in durable_context)
    assert {
        item["relative_path"]
        for result in durable_context
        for item in (
            result["value"] if isinstance(result["value"], list) else [result["value"]]
        )
    } >= {
        "02-metric-design-review.md",
        "03-rollout-decision.md",
        "04-owner-status.md",
        "06-threshold-review.md",
    }

    after = FakeModelAdapter([ModelAction.final("must not replay durable aer-002 final")])
    restarted = _engine(
        environment,
        after,
        checkpointer=SQLiteCheckpointer(environment["root"] / "checkpoints"),
    )
    status = restarted.resume_multi_agent("m11-aer-002-resume")
    state = restarted.checkpointer.get_latest("m11-aer-002-resume")
    assert state is not None and status.result_ref
    result = _result(environment["root"], status.run_id)

    assert status.status == state.status == result["status"] == "completed"
    assert not after.requests and after.call_count == 0
    assert [record.to_dict() for record in state.tool_ledger] == old_ledger
    assert result["result"]["team_decision"] == fresh_result
    _assert_source_unchanged(environment)
