"""Issue #104: read-only visibility of pending source claims that differ from the formal state."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from linkloom.ui.investigation import ProductInvestigationService
from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)

AS_OF = "2026-10-10"


def _import(base, name, day, speaker, value, *, verb="We decided to use"):
    _, imported = _client(base, "/api/sources/import", method="POST", data={
        "name": name,
        "content": f"[2026-10-{day}T10:00:00Z] {speaker}: {verb} {value} as the supplier for launch.",
    })
    _wait_source(base, imported["source"]["source_id"])
    return imported["source"]["source_id"]


def _candidate_id(base, value):
    _, inbox = _client(base, "/api/review/candidates")
    return next(item["candidate_id"] for item in inbox["candidates"] if item["proposed_value"] == value)


def _review(base, candidate_id, action):
    status, result = _client(base, f"/api/review/candidates/{candidate_id}/{action}", method="POST",
                             data={"reviewer_id": "local-user", "reason": "Reviewed"})
    assert status == 200, result
    return result


def _recover(base, as_of=AS_OF, subject="Launch"):
    status, payload = _client(base, "/api/decisions/recover", method="POST", data={
        "subject": subject, "relation": "uses supplier", "as_of": as_of})
    assert status == 200, payload
    return payload["recovery"]


def _decisions(base):
    return _client(base, "/api/decisions")[1]["decisions"]


@pytest.fixture
def server(tmp_path):
    started = {}

    def start(claims):
        started["parts"] = _start_product_server(tmp_path, claims)
        base = started["parts"][0]
        _client(base, "/api/workspaces", method="POST", data={"name": "Launch"})
        return started["parts"]

    yield start
    if started:
        _, srv, worker, app = started["parts"]
        _stop_product_server(srv, worker, app)


def _approved_b_and_pending_c(server, claims=None, c_day="01"):
    base, *_ = server(claims or [_claim("Birchline"), _claim("Cedar")])
    _import(base, "b.md", "01", "Alice", "Birchline")
    _review(base, _candidate_id(base, "Birchline"), "approve")
    _import(base, "c.md", c_day, "Bob", "Cedar")
    return base


def test_scenario_a_approved_b_with_different_pending_c(server):
    base = _approved_b_and_pending_c(server)
    before = _decisions(base)
    recovery = _recover(base)
    assert recovery["status"] == "FOUND"
    assert recovery["effective"]["value"] == "Birchline"
    [pending] = recovery["pending_conflicts"]
    assert pending["value"] == "Cedar"
    assert pending["candidate_id"] == _candidate_id(base, "Cedar")
    assert pending["review_status"] in {"PENDING_REVIEW", "CONFLICT"}
    assert pending["authority"] == "NON_AUTHORITATIVE"
    assert pending["evidence"]["source_name"] == "c.md"
    assert pending["evidence"]["quote"] and pending["evidence"]["line_start"] >= 1
    assert pending["effective_from"].startswith("2026-10-01")
    assert _decisions(base) == before
    assert [d["value"] for d in _decisions(base)] == ["Birchline"]
    assert _client(base, f"/api/review/candidates/{pending['candidate_id']}")[1]["candidate"]["status"] == pending["review_status"]
    status, answer = _client(base, "/api/product/ask", method="POST", data={
        "query": "Which supplier is used for Launch?", "workspace_id": recovery["workspace_id"], "as_of": AS_OF})
    assert status == 200
    assert answer["result"]["conclusions"][0]["value"] == "Birchline"
    assert answer["result"]["timeline"]["pending_conflicts"][0]["candidate_id"] == pending["candidate_id"]
    assert _decisions(base) == before


def test_scenario_b_two_unreviewed_sources_expose_both_sides(server):
    base, *_ = server([_claim("Birchline"), _claim("Cedar")])
    _import(base, "a.md", "01", "Alice", "Birchline")
    _import(base, "c.md", "01", "Bob", "Cedar")
    recovery = _recover(base)
    assert recovery["status"] == "SOURCE_CONFLICT_UNREVIEWED"
    assert recovery["effective"] is None
    assert {item["value"] for item in recovery["pending_conflicts"]} == {"Birchline", "Cedar"}
    assert {item["candidate_id"] for item in recovery["pending_conflicts"]} == {
        _candidate_id(base, "Birchline"), _candidate_id(base, "Cedar")}
    assert all(item["review_status"] in {"PENDING_REVIEW", "CONFLICT"} for item in recovery["pending_conflicts"])
    assert _decisions(base) == []


def test_scenario_c_rejected_candidate_is_no_longer_a_pending_conflict(server):
    base = _approved_b_and_pending_c(server)
    candidate_id = _candidate_id(base, "Cedar")
    assert len(_recover(base)["pending_conflicts"]) == 1
    _review(base, candidate_id, "reject")
    recovery = _recover(base)
    assert recovery["status"] == "FOUND" and recovery["effective"]["value"] == "Birchline"
    assert recovery["pending_conflicts"] == []
    assert _client(base, f"/api/review/candidates/{candidate_id}")[1]["candidate"]["status"] == "REJECTED"
    assert [d["value"] for d in _decisions(base)] == ["Birchline"]


def test_scenario_b_rejecting_one_side_clears_the_conflict(server):
    base, *_ = server([_claim("Birchline"), _claim("Cedar")])
    _import(base, "a.md", "01", "Alice", "Birchline")
    _import(base, "c.md", "01", "Bob", "Cedar")
    _review(base, _candidate_id(base, "Cedar"), "reject")
    assert _recover(base)["pending_conflicts"] == []


def test_same_value_pending_claim_is_not_a_difference(server):
    base = _approved_b_and_pending_c(server, [_claim("Birchline"), _claim("birchline")])
    recovery = _recover(base)
    assert recovery["effective"]["value"] == "Birchline"
    assert recovery["pending_conflicts"] == []


def test_same_value_unreviewed_sources_are_not_a_conflict(server):
    base, *_ = server([_claim("Birchline"), _claim("birchline")])
    _import(base, "b.md", "01", "Alice", "Birchline")
    _import(base, "c.md", "01", "Bob", "birchline")
    before = _decisions(base)
    recovery = _recover(base)
    assert recovery["status"] != "SOURCE_CONFLICT_UNREVIEWED"
    assert recovery["effective"] is None
    assert recovery["pending_conflicts"] == recovery["source_conflicts"] == []
    assert _decisions(base) == before == []


def test_unreviewed_sources_at_different_effective_times_are_not_a_conflict(server):
    base, *_ = server([_claim("Birchline"), _claim("Cedar")])
    _import(base, "b.md", "01", "Alice", "Birchline")
    _import(base, "c.md", "05", "Bob", "Cedar")
    recovery = _recover(base)
    assert recovery["effective"] is None
    assert recovery["status"] != "SOURCE_CONFLICT_UNREVIEWED"
    assert recovery["pending_conflicts"] == recovery["source_conflicts"] == []
    assert _decisions(base) == []


def test_pending_claim_in_other_workspace_is_not_reported(server):
    base, *_ = server([_claim("Cedar"), _claim("Birchline")])
    _import(base, "c.md", "05", "Bob", "Cedar")
    foreign_candidate = _candidate_id(base, "Cedar")
    assert len(_recover(base)["pending_conflicts"]) == 0 and _recover(base)["status"] != "FOUND"
    _client(base, "/api/workspaces", method="POST", data={"name": "Other"})
    _import(base, "b.md", "01", "Alice", "Birchline")
    _review(base, _candidate_id(base, "Birchline"), "approve")
    recovery = _recover(base)
    assert recovery["status"] == "FOUND"
    assert recovery["pending_conflicts"] == []
    assert _client(base, f"/api/review/candidates/{foreign_candidate}")[0] == 404


def test_deleted_source_is_not_reported(server):
    base = _approved_b_and_pending_c(server)
    _, imports = _client(base, "/api/sources")
    c_source = next(s["source_id"] for s in imports["sources"] if s["name"] == "c.md")
    assert len(_recover(base)["pending_conflicts"]) == 1
    assert _client(base, f"/api/sources/{c_source}", method="DELETE")[0] == 200
    assert _recover(base)["pending_conflicts"] == []


def test_replaced_source_version_is_not_reported(server):
    base, _, _, app = server([_claim("Birchline"), _claim("Cedar"), _claim("Birchline")])
    _import(base, "b.md", "01", "Alice", "Birchline")
    _review(base, _candidate_id(base, "Birchline"), "approve")
    _import(base, "c.md", "01", "Bob", "Cedar")
    _, sources = _client(base, "/api/sources")
    c_source = next(s["source_id"] for s in sources["sources"] if s["name"] == "c.md")
    old_candidate = _recover(base)["pending_conflicts"][0]["candidate_id"]
    status, _ = _client(base, f"/api/sources/{c_source}", method="PUT", data={
        "name": "c.md",
        "content": "[2026-10-05T10:00:00Z] Bob: We decided to use Birchline as the supplier for launch.",
    })
    assert status == 202
    _wait_source(base, c_source)
    recovery = _recover(base)
    assert old_candidate not in {item["candidate_id"] for item in recovery["pending_conflicts"]}
    assert recovery["pending_conflicts"] == []
    detail = _client(base, f"/api/review/candidates/{old_candidate}")[1]["candidate"]
    assert detail["source"]["source_version"] != detail["source"]["current_source_version"]
    # An old workflow flag is not source authority. Even with a stale PENDING_REVIEW
    # flag, a captured old version must not reappear in the read-only projection.
    with app._store_lock, app.store._connection:
        app.store._connection.execute("UPDATE semantic_candidate SET workflow_state = ? WHERE candidate_id = ?",
                                      ("PENDING_REVIEW", old_candidate))
    before = _decisions(base)
    assert _recover(base)["pending_conflicts"] == []
    assert _decisions(base) == before


def test_different_slot_is_not_reported(server):
    other = {**_claim("Cedar"), "subject": "Billing"}
    base, *_ = server([_claim("Birchline"), other])
    _import(base, "b.md", "01", "Alice", "Birchline")
    _review(base, _candidate_id(base, "Birchline"), "approve")
    _import(base, "c.md", "05", "Bob", "Cedar")
    recovery = _recover(base)
    assert recovery["status"] == "FOUND" and recovery["pending_conflicts"] == []
    assert [i["value"] for i in _recover(base, subject="Billing")["pending_conflicts"]] == []


def test_pending_claim_with_later_effective_time_is_not_reported(server):
    base = _approved_b_and_pending_c(server, c_day="05")
    recovery = _recover(base)
    assert recovery["status"] == "FOUND" and recovery["pending_conflicts"] == []


def test_pending_claim_after_requested_time_is_not_reported(server):
    base = _approved_b_and_pending_c(server, c_day="20")
    assert _recover(base)["pending_conflicts"] == []
    assert _recover(base, "2026-10-25")["pending_conflicts"] == []


def test_pending_claim_older_than_current_decision_is_not_reported(server):
    base, *_ = server([_claim("Birchline"), _claim("Cedar")])
    _import(base, "b.md", "05", "Alice", "Birchline")
    _review(base, _candidate_id(base, "Birchline"), "approve")
    _import(base, "c.md", "01", "Bob", "Cedar")
    recovery = _recover(base)
    assert recovery["status"] == "FOUND" and recovery["pending_conflicts"] == []


def test_pending_conflict_does_not_open_the_agent_gate_or_write_memory(server):
    base, srv, _, app = server([_claim("Birchline"), _claim("Cedar")])
    _import(base, "a.md", "01", "Alice", "Birchline")
    _import(base, "c.md", "01", "Bob", "Cedar")
    recovery = _recover(base)
    srv.investigation_service = ProductInvestigationService(
        app, model_factory=lambda _query: (_ for _ in ()).throw(
            AssertionError("pending source conflict must not start an Agent run")),
    )
    status, payload = _client(base, "/api/agent/decisions/ask", method="POST", data={
        "query": "Which supplier is effective?", "workspace_id": recovery["workspace_id"],
        "subject": "Launch", "relation": "uses supplier", "as_of": AS_OF})
    assert status == 409
    assert payload["error"]["code"] == "TEMPORAL_SCOPE_UNVERIFIED"
    assert _decisions(base) == []


def test_pending_only_slots_stay_out_of_trusted_ask_routing(server):
    # The separate read-only entry uses the existing candidate inbox and recovery,
    # not the trusted Router catalog. Natural-language routing stays unchanged.
    base, *_ = server([_claim("Birchline"), _claim("Cedar")])
    _import(base, "a.md", "01", "Alice", "Birchline")
    _import(base, "c.md", "01", "Bob", "Cedar")
    recovery = _recover(base)
    status, payload = _client(base, "/api/product/ask", method="POST", data={
        "query": "Which supplier is used for Launch?", "workspace_id": recovery["workspace_id"]})
    assert status == 200
    assert payload["result"]["timeline"] is None and payload["result"]["conclusions"] == []
    assert len(recovery["pending_conflicts"]) == 2
    assert _client(base, "/api/product/ask/config")[1]["scopes"] == []


def test_real_recovery_payloads_render_in_the_dom(server, tmp_path):
    # Feed real recover_decision output (scenarios A and B) to the DOM test.
    import json, os, shutil, subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    base = _approved_b_and_pending_c(server)
    a = _recover(base)
    b_base, *_ = server([_claim("Birchline"), _claim("Cedar")])
    _import(b_base, "a.md", "01", "Alice", "Birchline")
    _import(b_base, "c.md", "01", "Bob", "Cedar")
    b = _recover(b_base)
    assert a["pending_conflicts"] and b["status"] == "SOURCE_CONFLICT_UNREVIEWED"
    fixture = tmp_path / "recovery.json"
    _, inbox = _client(b_base, "/api/review/candidates")
    _, ask = _client(b_base, "/api/product/ask", method="POST", data={
        "query": "Which supplier is used for Launch?", "workspace_id": b["workspace_id"]})
    details = {item["candidate_id"]: _client(b_base, f"/api/review/candidates/{item['candidate_id']}")[1]["candidate"]
               for item in inbox["candidates"]}
    fixture.write_text(json.dumps({"A": a, "B": b, "B_candidates": inbox["candidates"],
                                   "B_details": details, "B_ask": ask["result"]}), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([node, "tests/ui/pending_source_conflict_dom_test.cjs"], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, "PENDING_CONFLICT_RECOVERY_FIXTURE": str(fixture)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "real recover_decision payloads" in done.stdout
