"""Stage 2 offline scenarios on the real Runtime loop (fake model that reads observations, no scripted answers).

The fake model is bounded by the production request caps; the Host checks every proposed Typed Claim against
the Tool Ledger. No test here pins a question wording or a tool-call sequence.
"""
import json
import re

import pytest

import run_bounded_multihop as demo
from linkloom.agents.model_adapter import FakeModelAdapter
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault

WORKSPACE = "q3-workspace"
SIX_MEETINGS = [
    "Staffing sync: Harbor Gap is staffed by a rotating team.",
    "As of 2026-09-15 Cobalt A is replaced by Cobalt B.",
    "Mira Chen is the accountable owner of Cobalt B.",
    "Mira Chen reports directly to Kai Wu.",
    "Riley Fox is the accountable owner of Harbor Gap.",
    "Budget review: no change to Q3 headcount.",
]
# The fake model's own reading of one extra synthetic sentence shape (test side only).
EXTRA_READING = (re.compile(r"(?P<s>[^.;\n]+?) is replaced by (?P<o>[^.;\n]+?)$"), "replaced_by")


def documents(texts=SIX_MEETINGS):
    return [{"workspace_id": WORKSPACE, "path": f"m{i}.md", "content": f"[2026-09-0{i}T10:00:00Z] Sync: {text}"}
            for i, text in enumerate(texts, 1)]



@pytest.fixture(autouse=True)
def fake_reading(monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(demo, "_READINGS", demo._READINGS + (EXTRA_READING,))
    # The fake model's own understanding of two more phrasings (stands in for a model, not production code).
    monkeypatch.setattr(demo, "_WANTS", (
        ("reports_to", re.compile(r"lead|manager|report|直属|领导|汇报|上级|answers to", re.I)),
        ("owner_of_project", re.compile(r"\bown|负责|responsible", re.I)),
        ("alias_of_project", re.compile(r"alias|behind|别名", re.I)),
        ("replaced_by", re.compile(r"alternative|replac|替代", re.I)),
    ))


def run(tmp_path, query, notes=None, *, max_steps=20):
    root = tmp_path / "run"
    vault = root / "vault"
    vault.mkdir(parents=True)
    for document in documents(notes or SIX_MEETINGS):
        (vault / document["path"]).write_text(document["content"], encoding="utf-8")
    index = scan_vault(vault, root / "scan").index_path
    model = FakeModelAdapter([demo.SourceOnlyController()] * max_steps)
    engine = RuntimeEngine(vault_root=vault, index_path=index, checkpoint_dir=root / "runtime",
                           trace_dir=root / "traces", checkpointer=InMemoryCheckpointer(), model=model)
    status = engine.start_multi_agent(RunRequest(
        request_id="s2", thread_id="s2", workflow="team_decision", query=query, final_protocol="claim-proposal/v1",
        max_steps=max_steps, max_provider_requests=max_steps, dry_run=True, reserve_final_turn=True))
    state = engine.checkpointer.get_latest("s2")
    path = root / "runtime" / "results" / status.run_id / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    team = (payload.get("result") or {}).get("team_decision")
    return status, model, state, team, (payload.get("result") or {}).get("typed_claims") or []


def reads(state):
    return [r for r in state.tool_ledger if r.tool_id == "read_verified_note" and r.status == "completed"]


def assert_cited(team, state):
    verified = {r.result["value"]["evidence_id"] for r in reads(state)}
    assert team["evidence_refs"] and set(team["evidence_refs"]) <= verified


# A: six meetings, alternative -> owner -> direct manager -> effective date.
def test_six_meeting_q3_chain_completes_through_the_real_loop(tmp_path):
    status, model, state, team, claims = run(
        tmp_path, "After Cobalt A was replaced, who is the direct manager of the owner of its alternative, "
                  "and when did the change take effect?")
    assert status.status == "completed", status.error
    by_predicate = {claim["predicate"]: claim for claim in claims}
    assert by_predicate["replaced_by"]["object"] == "Cobalt B"
    assert by_predicate["replaced_by"]["valid_on"] == "2026-09-15"
    assert by_predicate["owner_of_project"]["object"] == "Mira Chen"
    assert by_predicate["reports_to"]["object"] == "Kai Wu"
    assert_cited(team, state)
    searches = [r.arguments["query"] for r in state.tool_ledger if r.tool_id == "search_notes"]
    assert len(searches) >= 4 and "Cobalt B" in searches and "Mira Chen" in searches


# B + C: paraphrases and wording no old template knows reach retrieval and cite real reads.
@pytest.mark.parametrize("question,expected", [
    ("who is responsible for Cobalt B?", "Mira Chen"),
    ("Cobalt B 的 owner 是谁？", "Mira Chen"),
    ("Mira Chen 向谁直接汇报？", "Kai Wu"),
    ("Who is Mira Chen's direct manager?", "Kai Wu"),
    ("Tell me whom Mira Chen answers to day to day", "Kai Wu"),
])
def test_paraphrases_enter_retrieval_and_cite_real_reads(tmp_path, question, expected):
    status, model, state, team, claims = run(tmp_path, question)
    assert status.status == "completed", status.error
    assert expected in [claim["object"] for claim in claims]
    assert reads(state), "answer must come from a real verified read"
    assert_cited(team, state)


# E: more than four evidence rounds without a budget-driven final-only turn.
def test_many_evidence_rounds_never_trigger_budget_final_only(tmp_path):
    status, model, state, team, _ = run(
        tmp_path, "Who is the direct manager of the owner of the alternative that replaced Cobalt A?")
    assert status.status == "completed", status.error
    assert len(reads(state)) > 4 or len([r for r in state.tool_ledger if r.tool_id == "search_notes"]) > 3
    flags = [request.final_only for request in model.requests]
    assert flags[-1] is False or flags.count(True) == 1
    assert not any(flags[:-1]), "only the final turn may be final-only"


def test_last_step_reserves_the_final_turn_and_publishes_partial_once(tmp_path):
    status, model, state, team, claims = run(
        tmp_path, "Who is the direct manager of the owner of the alternative that replaced Cobalt A?", max_steps=5)
    assert model.call_count == 5, "one provider request per step: no duplicate charge, no extra loop"
    assert [request.final_only for request in model.requests] == [False] * 4 + [True]
    assert len({request.sequence for request in model.requests}) == 5
    assert status.status == "completed", status.error
    assert team["uncertainty"]["status"] != "none" and team["uncertainty"]["unknown_fields"]
    assert team["decision"]["status"] != "approved" or not team["decision"]["value"]
