"""B1-1 Text Source: plain notes, optional meeting date, duplicates, request_id, one revision."""

import sqlite3

from linkloom.ui.product import ProductRepository

from tests.integration.test_product_application_flow import (
    _client, _start_product_server, _stop_product_server, _wait_source,
)

PLAIN_NOTES = (
    "# 银杏项目周会\n"
    "\n"
    "讨论了支付网关供应商。\n"
    "决定：银杏项目改用 Wrenwell 作为支付网关供应商。\n"
    "- 李雷负责联调。\n"
)


def _not_stated_claim(value: str) -> dict[str, object]:
    return {
        "claim_type": "DECISION", "subject": "Launch", "relation": "uses supplier", "value": value,
        "temporal_status": "NOT_STATED", "valid_from": None, "valid_to": None,
        "confidence": {"claim_type": 0.9, "entity": 0.9, "relation": 0.9, "temporal": 0.9, "overall": 0.9},
    }


def _workspace(base: str, name: str = "Ginkgo") -> str:
    status, payload = _client(base, "/api/workspaces", method="POST", data={"name": name})
    assert status == 201
    return payload["workspace"]["workspace_id"]


def test_plain_notes_import_without_speaker_time_or_meeting_date(tmp_path):
    base, server, worker, app = _start_product_server(tmp_path, [_not_stated_claim("Wrenwell")])
    try:
        _workspace(base)
        status, payload = _client(base, "/api/sources/import", method="POST", data={
            "name": "weekly.md", "content": PLAIN_NOTES, "input_kind": "file"})
        assert status == 202, payload
        created = payload["source"]
        assert created["meeting_date"] is None and created["meeting_date_basis"] is None
        assert created["revision"] == 1 and created["input_kind"] == "file"
        source = _wait_source(base, created["source_id"])
        assert source["ingestion_status"] in {"COMPLETE", "PARTIAL"}, source
        assert source["segment_summary"]["total"] == 1
        # The immutable SourceVersion keeps the exact text and a verifiable span.
        assert source["content"] == PLAIN_NOTES
        (span,) = source["evidence"]
        assert span["quote"] == PLAIN_NOTES and span["line_start"] == 1
        assert source["evidence_jump"]["validated"] is True
        lines = [item for item in source["evidence_jump"]["spans"] if item["verification_kind"] == "RUNTIME_PASSAGE"]
        decision_line = next(item for item in lines if "Wrenwell" in item["quote"])
        assert PLAIN_NOTES[decision_line["char_start"]:decision_line["char_end"]] == decision_line["quote"]
        assert source["revisions"][0]["changed_fields"] == ["created"]
    finally:
        _stop_product_server(server, worker, app)


def test_meeting_date_is_optional_user_chosen_and_one_revision_covers_edits(tmp_path):
    base, server, worker, app = _start_product_server(
        tmp_path, [_not_stated_claim("Wrenwell"), _not_stated_claim("Halden")])
    try:
        _workspace(base)
        status, payload = _client(base, "/api/sources/import", method="POST", data={
            "name": "weekly.md", "content": "x\n", "meeting_date": "2026-02-30"})
        assert status == 400 and payload["error"]["code"] == "INVALID_MEETING_DATE"

        status, payload = _client(base, "/api/sources/import", method="POST", data={
            "name": "weekly.md", "content": PLAIN_NOTES,
            "meeting_date": "2026-11-20", "meeting_date_basis": "USER_TODAY"})
        assert status == 202
        source_id = payload["source"]["source_id"]
        source = _wait_source(base, source_id)
        assert (source["meeting_date"], source["meeting_date_basis"]) == ("2026-11-20", "USER_TODAY")
        version, attempts = source["source_version"], len(app.repository.ingestion_attempts(
            source["workspace_id"], source_id, source["source_version"]))

        def put(**data):
            return _client(base, f"/api/sources/{source_id}", method="PUT", data=data)

        # Title + date in one request: one revision, same immutable version, no extraction.
        status, result = put(name="周会（修订）.md", content=PLAIN_NOTES, meeting_date="2026-11-18",
                             expected_revision=1)
        assert status == 200 and result["changed"] is False
        assert result["changed_fields"] == ["title", "meeting_date"]
        assert result["source"]["revision"] == 2 and result["source"]["source_version"] == version
        assert result["source"]["meeting_date_basis"] == "USER_SELECTED"

        # Clearing the date is a real edit; resending identical values is not.
        status, result = put(name="周会（修订）.md", content=PLAIN_NOTES, meeting_date=None, expected_revision=2)
        assert status == 200 and result["source"]["meeting_date"] is None and result["source"]["revision"] == 3
        status, result = put(name="周会（修订）.md", content=PLAIN_NOTES, meeting_date=None, expected_revision=3)
        assert status == 200 and result["changed_fields"] == [] and result["source"]["revision"] == 3

        # A stale editor cannot overwrite a newer revision.
        status, result = put(name="other.md", content=PLAIN_NOTES + "more\n", expected_revision=1)
        assert status == 409 and result["error"]["code"] == "VERSION_CONFLICT"
        assert result["error"]["details"] == {"current_revision": 3}
        assert len(app.repository.ingestion_attempts(source["workspace_id"], source_id, version)) == attempts

        # Content edit creates a new immutable version and extracts once.
        status, result = put(name="周会（修订）.md", content=PLAIN_NOTES + "补充：下周复核。\n", expected_revision=3)
        assert status == 202 and result["changed"] is True and result["changed_fields"] == ["content"]
        updated = _wait_source(base, source_id)
        assert updated["source_version"] != version and updated["revision"] == 4
        assert updated["meeting_date"] is None
        assert [item["changed_fields"] for item in updated["revisions"]] == [
            ["created"], ["title", "meeting_date"], ["meeting_date"], ["content"]]
    finally:
        _stop_product_server(server, worker, app)


def test_duplicate_prompt_and_request_id_idempotency(tmp_path):
    base, server, worker, app = _start_product_server(
        tmp_path, [_not_stated_claim("Wrenwell"), _not_stated_claim("Wrenwell")])
    try:
        _workspace(base)
        body = {"name": "weekly.md", "content": PLAIN_NOTES, "request_id": "req-import-0001"}
        status, first = _client(base, "/api/sources/import", method="POST", data=body)
        assert status == 202
        source_id = first["source"]["source_id"]
        _wait_source(base, source_id)

        # Network replay of the same request returns the same Source, no new prompt.
        status, replay = _client(base, "/api/sources/import", method="POST", data=body)
        assert status == 200 and replay["replayed"] is True and replay["source"]["source_id"] == source_id
        status, conflict = _client(base, "/api/sources/import", method="POST",
                                   data={**body, "content": PLAIN_NOTES + "changed\n"})
        assert status == 409 and conflict["error"]["code"] == "REQUEST_ID_CONFLICT"

        # A new create with exactly the same text prompts; the prompt does not consume its request_id.
        second = {"name": "copy.md", "content": PLAIN_NOTES, "request_id": "req-import-0002"}
        status, prompt = _client(base, "/api/sources/import", method="POST", data=second)
        assert status == 409 and prompt["error"]["code"] == "SOURCE_DUPLICATE"
        assert [item["source_id"] for item in prompt["error"]["details"]["duplicates"]] == [source_id]
        assert len(_client(base, "/api/sources")[1]["sources"]) == 1

        status, independent = _client(base, "/api/sources/import", method="POST",
                                      data={**second, "allow_duplicate": True})
        assert status == 202 and independent["source"]["source_id"] != source_id
        _wait_source(base, independent["source"]["source_id"])
        assert len(_client(base, "/api/sources")[1]["sources"]) == 2

        # Another workspace never sees these sources as duplicates.
        _workspace(base, "Other")
        status, other = _client(base, "/api/sources/import", method="POST",
                                data={"name": "weekly.md", "content": PLAIN_NOTES, "request_id": "req-import-0001"})
        assert status == 202 and other["source"]["source_id"] not in {source_id, independent["source"]["source_id"]}
        _wait_source(base, other["source"]["source_id"])
    finally:
        _stop_product_server(server, worker, app)


def test_existing_database_gains_source_metadata_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE ll_workspace (workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE ll_source (
                workspace_id TEXT NOT NULL, source_id TEXT NOT NULL, name TEXT NOT NULL,
                current_version_id TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0, 1)),
                ingestion_status TEXT NOT NULL, candidate_count INTEGER NOT NULL DEFAULT 0,
                review_required_count INTEGER NOT NULL DEFAULT 0, error_message TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (workspace_id, source_id));
            INSERT INTO ll_workspace VALUES ('ws_old', 'Old', '2026-01-01T00:00:00+00:00');
            INSERT INTO ll_source VALUES ('ws_old', 'src_old', 'old.txt', 'v1', 1, 'COMPLETE', 0, 0, NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """)
    repository = ProductRepository(path)
    try:
        row = repository._connection.execute(
            "SELECT meeting_date, meeting_date_basis, revision FROM ll_source WHERE source_id = 'src_old'").fetchone()
        assert tuple(row) == (None, None, 1)
    finally:
        repository.close()


def test_plain_notes_decision_can_be_approved_with_reviewer_chosen_effective_date(tmp_path):
    """The reviewer supplies the effective date; it need not be written in plain notes."""
    base, server, worker, app = _start_product_server(tmp_path, [_not_stated_claim("Wrenwell")])
    try:
        _workspace(base)
        status, payload = _client(base, "/api/sources/import", method="POST",
                                  data={"name": "weekly.md", "content": PLAIN_NOTES})
        assert status == 202
        _wait_source(base, payload["source"]["source_id"])
        (candidate,) = _client(base, "/api/review/candidates")[1]["candidates"]
        path = f"/api/review/candidates/{candidate['candidate_id']}/approve"
        status, result = _client(base, path, method="POST", data={
            "reviewer_id": "local-user", "reason": "Confirmed in review", "subject": "银杏项目"})
        assert status == 409, "content approval still needs an effective date in the dated path"
        status, result = _client(base, path, method="POST", data={
            "reviewer_id": "local-user", "reason": "Confirmed in review", "subject": "银杏项目",
            "valid_from": "2026-11-01"})
        assert status == 200 and result["workflow_state"] == "MATERIALIZED", result
        (decision,) = _client(base, "/api/decisions")[1]["decisions"]
        assert (decision["subject"], decision["value"]) == ("银杏项目", "Wrenwell")
        assert decision["valid_from"].startswith("2026-11-01")
        assert decision["evidence"][0]["verified"] is True
        assert decision["evidence"][0]["quote"] == PLAIN_NOTES
    finally:
        _stop_product_server(server, worker, app)
