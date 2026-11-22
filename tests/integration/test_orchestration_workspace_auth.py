"""Workspace authorization of the LangGraph orchestration: where the boundary really is.

The Supervisor name check (blocked_entity_reason) is an early convenience. These tests show that a name variant
which passes it (Orchid-Labs, Orchid Lab, the Orchid group) still cannot reach another Workspace's data, because
the Worker's real retrieval tools only see the vault materialized from the run's own Workspace sources, the
Reviewer gate only accepts evidence whose source identity the Host issued, and the published answer is built
from accepted rows only. Scripted Supervisor and Worker models, real RuntimeEngine, Reviewer and synthesis.
"""
import json
from pathlib import Path

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.orchestration.state import ACCEPTED
from linkloom.tools.contracts import ToolCall
from tests.integration import test_orchestration_langgraph as lg
from tests.integration.test_orchestration_langgraph import GOAL, World, build, delegation, events

SECRET = "ORCHID-SECRET-7"
OTHER_NOTE = "orchid.md"  # exists only in the other Workspace's vault
OTHER_TEXT = f"Orchid Labs is owned by Zed Quill, vault code {SECRET}"
VARIANTS = ["Who owns Orchid-Labs?", "Who owns Orchid Lab?", "Who owns the Orchid group?"]
BLOCKED = [["Orchid Labs", "WORKSPACE_DENIED"]]


class ProbingWorker(lg.WorkerModel):
    """Hostile scripted Worker: searches the variant, reads a note of the other Workspace, then claims its secret."""

    def complete(self, request):
        self.calls += 1
        self.requests.append(request)
        usage = ModelUsage(input_tokens=10, output_tokens=10)
        if self.calls == 1:
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            return ModelResponse(action=ModelAction.tool(ToolCall("search-1", "search_notes",
                dict(query="Orchid", limit=5, source_context=context))), usage=usage)
        if self.calls == 2:
            self.search_result = request.observation.value
            return ModelResponse(action=ModelAction.tool(ToolCall("read-2", "read_verified_note",
                dict(note_ref=OTHER_NOTE))), usage=usage)
        self.read_observation = request.observation
        refs = [f"{OTHER_NOTE}:1", *[hit["evidence_id"] for hit in self.search_result or []]]
        return ModelResponse(action=ModelAction.final(json.dumps(dict(
            schema_version="team-decision-result/v1",
            decision=dict(value=OTHER_TEXT, status="approved", evidence_refs=refs),
            rationale=[], rejected_alternatives=[], actions=[], unresolved_items=[],
            uncertainty=dict(status="none", statement=None, unknown_fields=[], evidence_refs=[]),
            evidence_refs=refs))), usage=usage)


def _every_file_text(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path, path.read_bytes().decode("utf-8", "ignore")


@pytest.mark.parametrize("text", VARIANTS)
def test_a_name_variant_that_passes_the_supervisor_check_cannot_reach_another_workspace(tmp_path, text, monkeypatch):
    monkeypatch.setattr(lg, "WorkerModel", ProbingWorker)
    world = World([delegation("delegate", [("a", text, [])]), delegation("stop")],
                  {text: (OTHER_TEXT, "Orchid", "ok")})
    orchestrator, deps = build(tmp_path, world)
    other = tmp_path / "other-workspace"  # a second Workspace on the same machine, never given to this run
    other.mkdir()
    (other / OTHER_NOTE).write_text(OTHER_TEXT, encoding="utf-8")
    scope = dict(workspace_id="ws", blocked_entities=BLOCKED)
    result = orchestrator.start(GOAL, confirmed_scope=scope)

    # the variant really passed the Supervisor check: the Supervisor's task was dispatched as written
    assert result["tasks"]["a"]["description"] == text and result["tasks"]["a"]["spawn_reason"] == "initial"
    (worker,) = world.workers

    # tool layer: the search found nothing of the other Workspace and the read of its note was refused
    assert worker.search_result == []
    assert worker.read_observation.status == "error" and worker.read_observation.value is None
    assert not (deps.vault / OTHER_NOTE).exists()

    # Reviewer layer: nothing was accepted, so no evidence, inference or answer carries the secret
    task = result["tasks"]["a"]
    assert task["status"] == "failed" and task["failure"] == "TOOL_INVALID_ARGUMENTS"
    assert result["verified_evidence"] == {} and result["inferences"] == {}
    assert result["status"] != "COMPLETE"

    # published output, trace and checkpoint: the claim of the Worker is nowhere in what the Host stores
    published = json.dumps(dict(answer=result["answer"], trace=result["trace"], tasks=result["tasks"],
                                evidence=result["verified_evidence"], inferences=result["inferences"]), default=str)
    assert SECRET not in published
    assert not (result["answer"] or {}).get("answers")
    row_file = deps.root / "tasks" / "a" / "attempt-0-1" / "row.json"
    assert not row_file.exists()
    for path, content in _every_file_text(deps.root):
        if path.suffix in (".json", ".jsonl") and "runtime" not in path.parts:
            assert SECRET not in content, path


def test_the_reviewer_gate_rejects_evidence_whose_source_identity_the_host_did_not_issue(tmp_path):
    """A source that is in the vault but unknown to the Host catalogue cannot become a verified row."""
    world = World([delegation("answer_single")], {GOAL: (lg.FACT["succ"], "Meridian A", "ok")})
    orchestrator, deps = build(tmp_path, world)
    deps.source_versions.pop("succ.md")  # the Host never issued an identity for this source
    result = orchestrator.start(GOAL)
    task = result["tasks"]["task-1"]
    assert task["status"] != ACCEPTED and task["failure"] == "WORKER_GATE_FAILED"
    assert result["verified_evidence"] == {} and not (result["answer"] or {}).get("answers")
    assert all(event["status"] != ACCEPTED for event in events(result, "worker_finished"))
