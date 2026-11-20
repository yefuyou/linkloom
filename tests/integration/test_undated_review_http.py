"""Product wiring of #132: confirm content with unknown time, list it, later fill the date, and Ask."""

from tests.integration.test_product_application_flow import (
    _client, _start_product_server, _stop_product_server, _wait_source,
)
from tests.integration.test_text_source_import import _quoted_claim

MEETING_A = "# 周会 A\n决定：银杏项目使用 Halden 作为支付网关供应商。\n"
MEETING_B = "# 周会 B\n决定：银杏项目改用 Wrenwell 作为支付网关供应商，启用时间待定。\n"
REVIEW = {"reviewer_id": "local-user", "reason": "Confirmed in review"}


def _import_candidate(base, name, content):
    status, payload = _client(base, "/api/sources/import", method="POST", data={"name": name, "content": content})
    assert status == 202, payload
    source = _wait_source(base, payload["source"]["source_id"])
    candidates = [item for item in _client(base, "/api/review/candidates")[1]["candidates"]
                  if item["source_id"] == source["source_id"]]
    assert len(candidates) == 1, candidates
    return candidates[0]


def _ask(base, query):
    workspace_id = _client(base, "/api/workspaces/current")[1]["workspace"]["workspace_id"]
    status, payload = _client(base, "/api/product/ask", method="POST", data={
        "workspace_id": workspace_id, "query": query, "subject": "银杏项目", "relation": "uses supplier"})
    assert status == 200, payload
    return payload["result"]


def test_confirm_unknown_time_then_fill_date_through_product_api(tmp_path):
    base, server, worker, app = _start_product_server(
        tmp_path, [_quoted_claim("Halden", "决定：银杏项目使用 Halden 作为支付网关供应商。"),
                   _quoted_claim("Wrenwell", "决定：银杏项目改用 Wrenwell 作为支付网关供应商，启用时间待定。")])
    try:
        assert _client(base, "/api/workspaces", method="POST", data={"name": "Ginkgo"})[0] == 201
        first = _import_candidate(base, "a.md", MEETING_A)
        status, dated = _client(base, f"/api/review/candidates/{first['candidate_id']}/approve", method="POST",
                                data={**REVIEW, "valid_from": "2026-10-01"})
        assert status == 200 and dated["workflow_state"] == "MATERIALIZED", dated

        second = _import_candidate(base, "b.md", MEETING_B)
        path = f"/api/review/candidates/{second['candidate_id']}/approve"
        # A date together with UNKNOWN is contradictory and rejected.
        status, error = _client(base, path, method="POST",
                                data={**REVIEW, "time_basis": "UNKNOWN", "valid_from": "2026-11-01"})
        assert status == 400 and error["error"]["code"] == "INVALID_VALID_TIME"
        status, undated = _client(base, path, method="POST", data={**REVIEW, "time_basis": "UNKNOWN"})
        assert status == 200 and undated["time_basis"] == "UNKNOWN", undated
        assert undated["workflow_state"] == "MATERIALIZED" and undated["undated_decision_id"].startswith("udec_")
        # The candidate left the review queue; a replayed approval is idempotent.
        assert all(item["candidate_id"] != second["candidate_id"]
                   for item in _client(base, "/api/review/candidates")[1]["candidates"])
        status, replay = _client(base, path, method="POST", data={**REVIEW, "time_basis": "UNKNOWN"})
        assert status == 200 and replay["undated_decision_id"] == undated["undated_decision_id"], replay

        (listed,) = _client(base, "/api/decisions/undated")[1]["undated"]
        assert (listed["value"], listed["valid_from"], listed["evidence_status"]) == ("Wrenwell", None, "VERIFIED")

        current = _ask(base, "银杏项目现在用哪个支付网关？")
        assert current["status"] == "INSUFFICIENT" and current["conclusions"] == []
        assert [item["value"] for item in current["evolution"]["undated"]] == ["Wrenwell"]

        # Filling the date is a separate reviewed act with its own basis.
        promote = f"/api/decisions/undated/{undated['undated_decision_id']}/promote"
        status, error = _client(base, promote, method="POST", data={**REVIEW, "valid_from": "2026-11-05"})
        assert status == 400 and error["error"]["code"] == "INVALID_VALID_TIME"
        status, promoted = _client(base, promote, method="POST", data={
            **REVIEW, "valid_from": "2026-11-05", "time_basis": "USER_TODAY",
            "supersedes_decision_id": dated["decision_id"]})
        assert status == 200, promoted
        assert promoted["decision_ref"] == undated["decision_ref"] and promoted["memory_state"] == "PROMOTED_TO_DATED"
        assert _client(base, "/api/decisions/undated")[1]["undated"] == []

        current = _ask(base, "银杏项目现在用哪个支付网关？")
        assert current["status"] == "COMPLETED" and current["conclusions"][0]["value"] == "Wrenwell", current
        status, again = _client(base, promote, method="POST", data={
            **REVIEW, "valid_from": "2026-11-06", "time_basis": "USER_SELECTED"})
        assert status == 409 and again["error"]["code"] == "UNDATED_NOT_ACTIVE"
    finally:
        _stop_product_server(server, worker, app)
