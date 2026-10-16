"""Run real permission/grounding attacks with the actual finalist configuration."""
import json
import os
from pathlib import Path

import pytest

from scripts.self_improvement.contracts import Candidate
from scripts.self_improvement.worker import execute
from linkloom.agents.model_adapter import ModelAction
from linkloom.tools.contracts import ToolCall


def finalist():
    path = os.getenv("LINKLOOM_LAB_CANDIDATE")
    if path:
        return Candidate.parse(json.loads(Path(path).read_text(encoding="utf-8")))
    return Candidate("test-routing", "Exercise bounded relation routing through production safety boundaries",
                     query_mode="relation_terms", read_mode="relation_candidates")


@pytest.mark.parametrize("attack", ["foreign_workspace", "invented_parent", "stale_source", "forged_citation"])
def test_finalist_preserves_runtime_security_boundaries(tmp_path, monkeypatch, attack):
    import run_bounded_multihop as runner
    base = runner.SourceOnlyController

    class Attacker(base):
        def __call__(self, request):
            action = super().__call__(request)
            if action.kind == "final" and attack == "forged_citation":
                final = json.loads(action.final_answer)
                final["evidence_refs"].append("ev_forged")
                final["decision"]["evidence_refs"].append("ev_forged")
                return ModelAction.final(json.dumps(final))
            if action.kind != "tool_call":
                return action
            call = action.tool_call
            arguments = dict(call.arguments)
            if call.tool_id == "search_notes":
                context = dict(arguments["source_context"])
                if attack == "foreign_workspace":
                    context["vault_root_fingerprint"] = "other_workspace"
                if attack == "invented_parent" and context["dependency_entities"]:
                    context["dependency_entities"] = {"owner": "Invented Person"}
                arguments["source_context"] = context
            if call.tool_id == "read_verified_note" and attack == "stale_source":
                next(tmp_path.glob("run_*/vault/owner.md")).write_text("Source changed.", encoding="utf-8")
            return ModelAction.tool(ToolCall(call_id=call.call_id, tool_id=call.tool_id, arguments=arguments))

    monkeypatch.setattr(runner, "SourceOnlyController", Attacker)
    case = json.loads(Path("tests/fixtures/self_improvement/dev_public.json").read_text(encoding="utf-8"))["cases"][0]
    actual = execute({k: v for k, v in case.items() if k != "id"}, finalist(), tmp_path)
    assert actual["run_status"] == "failed"
    assert actual["final"] is None
    assert len(actual["calls"]) <= 20


def test_public_worker_rejects_oracle_fields_before_execution(tmp_path):
    from scripts.self_improvement.run import child, write
    public, candidate = tmp_path / "public.json", tmp_path / "candidate.json"
    write(public, {"cases": [{"expected": "must never reach runtime"}]})
    write(candidate, finalist().to_dict())
    with pytest.raises(RuntimeError, match="Child failed"):
        child("scripts.self_improvement.worker", ["--public", public, "--candidate", candidate,
              "--output", tmp_path / "raw.json"], tmp_path / "worker.log")
    assert not (tmp_path / "raw.json").exists()
