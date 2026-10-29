"""Focused offline checks for the temporal HTTP acceptance runner's terminal boundary."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest
import httpx

from tests.smoke import run_temporal_product_http as runner
from tests.smoke.provider_ledger import (
    AppendOnlyProviderLedger, SmokeBlocked, UncertainProviderRequest,
)


def _ledger(path: Path, *, cap: int = 1) -> AppendOnlyProviderLedger:
    ledger = AppendOnlyProviderLedger(
        path, batch_id="offline-temporal", run_id="offline-t01",
        max_requests=cap, max_runtime_runs=1,
        max_input_tokens_per_request=None, case_id="T01",
    )
    ledger.runtime_started()
    return ledger


def _request(ledger: AppendOnlyProviderLedger, operation=None):
    return ledger.request(
        operation or (lambda: {"model_version": runner.MODEL, "usage_metadata": {
            "prompt_token_count": 3, "candidates_token_count": 2,
            "thoughts_token_count": 1,
        }}),
        request_kind="generate_content", model=runner.MODEL,
        payload={"contents": "synthetic"},
    )


@pytest.mark.parametrize("runtime_status", ["COMPLETED", "FAILED"])
@pytest.mark.parametrize("archive_succeeds", [True, False])
def test_runtime_terminal_survives_archive_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    runtime_status: str, archive_succeeds: bool,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ledger = _ledger(run_dir / "provider_ledger.jsonl")
    try:
        _request(ledger)
        ledger.finish_run(status=runtime_status, usage_summary={"input_tokens": 3})
        def archive(_source, destination):
            if not archive_succeeds:
                raise OSError("synthetic archive fault")
            destination.mkdir()
        monkeypatch.setattr(runner, "_archive_runtime", archive)
        if archive_succeeds:
            runner._archive_after_terminal(tmp_path, run_dir)
        else:
            with pytest.raises(SmokeBlocked, match="ARTIFACT_ARCHIVE_FAILED"):
                runner._archive_after_terminal(tmp_path, run_dir)
    finally:
        ledger.close()
    events = [json.loads(line) for line in (run_dir / "provider_ledger.jsonl").read_text().splitlines()]
    assert events[-2]["status"] == "SUCCESS_USAGE_REPORTED"
    assert events[-1]["event"] == "run_finished"
    assert events[-1]["status"] == runtime_status
    assert json.loads((run_dir / "archive_status.json").read_text())["status"] == (
        "ARCHIVED" if archive_succeeds else "ARCHIVE_FAILED"
    )


def test_terminal_write_failure_keeps_receipt_and_prevents_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "provider_ledger.jsonl"
    ledger = _ledger(path)
    try:
        _request(ledger)
        original = ledger._append_locked
        def fail_terminal(record):
            if record["event"] == "run_finished":
                raise SmokeBlocked("LEDGER_DURABILITY_FAILURE_STOP")
            return original(record)
        monkeypatch.setattr(ledger, "_append_locked", fail_terminal)
        with pytest.raises(SmokeBlocked, match="LEDGER_DURABILITY_FAILURE_STOP"):
            ledger.finish_run(status="COMPLETED", usage_summary={})
    finally:
        ledger.close()
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert events[-1]["status"] == "SUCCESS_USAGE_REPORTED"
    assert not any(item["event"] == "run_finished" for item in events)
    with pytest.raises(SmokeBlocked, match="ALREADY_EXISTS"):
        _ledger(path)


def test_unknown_request_stops_without_resend(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "provider_ledger.jsonl")
    calls = 0
    def unknown():
        nonlocal calls
        calls += 1
        raise TimeoutError("unknown external outcome")
    try:
        with pytest.raises(UncertainProviderRequest):
            _request(ledger, unknown)
        with pytest.raises(SmokeBlocked, match="TERMINAL_STOP"):
            _request(ledger)
    finally:
        ledger.close()
    assert calls == 1
    events = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert events[-1]["status"] == "UNCERTAIN"


@pytest.mark.parametrize(("fail_at", "failure"), [
    (1, httpx.ConnectError),
    (2, httpx.ReadError),
    (4, httpx.RemoteProtocolError),
])
def test_fake_transport_failure_at_any_turn_stops_batch(
    tmp_path: Path, fail_at: int, failure: type[Exception],
) -> None:
    ledger = _ledger(tmp_path / "provider_ledger.jsonl", cap=5)
    calls = 0

    def fake_transport():
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise failure("private transport detail")
        return {"model_version": runner.MODEL, "usage_metadata": {
            "prompt_token_count": 3, "candidates_token_count": 2,
            "thoughts_token_count": 1,
        }}

    try:
        for _ in range(fail_at - 1):
            _request(ledger, fake_transport)
        with pytest.raises(UncertainProviderRequest):
            _request(ledger, fake_transport)
        with pytest.raises(SmokeBlocked, match="TERMINAL_STOP"):
            _request(ledger, fake_transport)
    finally:
        ledger.close()
    assert calls == fail_at
    rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    finished = [x for x in rows if x["event"] == "request_finished"]
    reserved = [x for x in rows if x["event"] == "request_reserved"]
    assert len(finished) == fail_at
    assert all(x["serialized_payload_bytes"] > 0 for x in reserved)
    assert [x["status"] for x in finished] == [
        *(["SUCCESS_USAGE_REPORTED"] * (fail_at - 1)), "UNCERTAIN",
    ]
    assert finished[-1]["error_class"] == failure.__name__
    assert isinstance(finished[-1]["operation_elapsed_ms"], (int, float))
    assert all("private transport detail" not in x for x in ledger.path.read_text().splitlines())
    with pytest.raises(SmokeBlocked, match="ALREADY_EXISTS"):
        _ledger(ledger.path)


def test_returned_response_usage_fault_closes_reservation(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "provider_ledger.jsonl")

    class BadResponse:
        model_version = runner.MODEL

        @property
        def usage_metadata(self):
            raise OSError("private response read detail")

    try:
        with pytest.raises(SmokeBlocked, match="RESPONSE_USAGE_PARSE_FAILED_STOP"):
            _request(ledger, BadResponse)
        assert not ledger.in_flight
        assert ledger.halted
        with pytest.raises(SmokeBlocked, match="TERMINAL_STOP"):
            _request(ledger)
    finally:
        ledger.close()
    rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert rows[-1]["status"] == "USAGE_UNKNOWN_TERMINAL"
    assert rows[-1]["error_class"] == "OSError"
    assert rows[-1]["response_received"] is True
    assert "private response read detail" not in ledger.path.read_text()


def test_pre_send_model_rejection_has_no_request_reservation(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "provider_ledger.jsonl")
    calls = 0

    def fake_transport():
        nonlocal calls
        calls += 1

    try:
        with pytest.raises(SmokeBlocked, match="MODEL_MISMATCH_NO_FALLBACK"):
            ledger.request(
                fake_transport, request_kind="generate_content",
                model="forbidden-model", payload={"contents": "private input"},
            )
    finally:
        ledger.close()
    assert calls == 0
    assert not any(
        json.loads(x)["event"] == "request_reserved"
        for x in ledger.path.read_text().splitlines()
    )


def test_confirmed_http_400_is_known_provider_failure_not_unknown(tmp_path: Path) -> None:
    from google.genai.errors import ClientError

    ledger = _ledger(tmp_path / "provider_ledger.jsonl")
    response = httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    def rejected():
        raise ClientError(400, {"error": {"status": "INVALID_ARGUMENT"}}, response)

    try:
        with pytest.raises(ClientError):
            _request(ledger, rejected)
        assert ledger.halted and not ledger.in_flight
        with pytest.raises(SmokeBlocked, match="TERMINAL_STOP"):
            _request(ledger)
    finally:
        ledger.close()
    rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert rows[-1]["status"] == "KNOWN_PROVIDER_FAILURE_TERMINAL"
    assert rows[-1]["http_status"] == 400
    assert rows[-1]["response_received"] is True


@pytest.mark.parametrize("code", [400, 503])
def test_http_error_without_proven_client_rejection_stays_uncertain(
    tmp_path: Path, code: int,
) -> None:
    from google.genai.errors import ClientError, ServerError

    ledger = _ledger(tmp_path / "provider_ledger.jsonl")
    response = None if code == 400 else httpx.Response(code, json={"error": {}})

    def ambiguous():
        kind = ClientError if code == 400 else ServerError
        raise kind(code, {"error": {}}, response)

    try:
        with pytest.raises(UncertainProviderRequest):
            _request(ledger, ambiguous)
    finally:
        ledger.close()
    rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert rows[-1]["status"] == "UNCERTAIN"
    assert "response_received" not in rows[-1]


def test_real_archive_format_preserves_deep_models_and_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for folder in ("runtime/results", "runtime/models", "traces", "vault", "scan"):
        (source / folder).mkdir(parents=True)
    deep = source / "runtime/models/model" / ("turn_" + "x" * 75) / "tool-results"
    deep.mkdir(parents=True)
    (deep / "response.json").write_text('{"kind":"synthetic"}', encoding="utf-8")
    with sqlite3.connect(source / "runtime/checkpoints.sqlite") as connection:
        connection.execute("CREATE TABLE checkpoint (value TEXT)")
        connection.execute("INSERT INTO checkpoint VALUES ('synthetic')")
    (source / "vault/source.md").write_text("synthetic", encoding="utf-8")
    (source / "scan/index.json").write_text("{}", encoding="utf-8")
    run_dir = tmp_path / ("archive_destination_" + "z" * 60)
    run_dir.mkdir()
    assert len(str(run_dir / "runtime_artifacts" / (deep / "response.json").relative_to(source))) > 260
    runner._archive_after_terminal(source, run_dir)
    with ZipFile(run_dir / "runtime_artifacts.zip") as archive:
        names = archive.namelist()
        assert "runtime/checkpoints.sqlite" in names
        assert any(name.endswith("tool-results/response.json") for name in names)
        assert "vault/source.md" in names and "scan/index.json" in names


def test_score_survives_real_archive_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = {"kind": "failed"}
    def failed_archive(_source: Path, _destination: Path) -> None:
        raise OSError("simulated archive write failure")
    monkeypatch.setattr(runner, "_archive_runtime", failed_archive)
    with pytest.raises(SmokeBlocked, match="ARTIFACT_ARCHIVE_FAILED"):
        runner._finalize_result(snapshot, None, {"effective": "synthetic"}, tmp_path, run_dir)
    assert json.loads((run_dir / "score.json").read_text())["label"] == "NOT_EVALUATED"
    assert runner._verified_score(run_dir)["label"] == "NOT_EVALUATED"
    assert json.loads((run_dir / "archive_status.json").read_text())["status"] == "ARCHIVE_FAILED"


def test_zip_has_verified_member_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for relative in ("runtime/results", "runtime/models", "vault", "scan"):
        (source / relative).mkdir(parents=True)
    (source / "vault" / "source.md").write_text("source", encoding="utf-8")
    with sqlite3.connect(source / "runtime" / "checkpoints.sqlite") as connection:
        connection.execute("CREATE TABLE checkpoint (value TEXT)")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runner._archive_after_terminal(source, run_dir)
    assert runner._verify_archive(run_dir / "runtime_artifacts.zip")
    with ZipFile(run_dir / "runtime_artifacts.zip") as archive:
        assert "manifest.json" in archive.namelist()
    missing = run_dir / "missing_member.zip"
    with ZipFile(missing, "x") as archive:
        archive.writestr("manifest.json", json.dumps({"sha256": {
            "vault/source.md": hashlib.sha256(b"source").hexdigest(),
        }}))
    with pytest.raises(ValueError, match="member mismatch"):
        runner._verify_archive(missing)


def test_interrupted_zip_keeps_failure_separate_from_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    for relative in ("runtime/results", "runtime/models", "vault", "scan"):
        (source / relative).mkdir(parents=True)
    (source / "vault/source.md").write_text("synthetic")
    with sqlite3.connect(source / "runtime/checkpoints.sqlite") as connection:
        connection.execute("CREATE TABLE checkpoint (value TEXT)")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write = ZipFile.write
    def interrupted(self, filename, arcname=None, compress_type=None, compresslevel=None):
        if arcname == "runtime/checkpoints.sqlite":
            raise OSError("interrupted synthetic ZIP")
        return write(self, filename, arcname, compress_type, compresslevel)
    monkeypatch.setattr(ZipFile, "write", interrupted)
    with pytest.raises(SmokeBlocked, match="ARTIFACT_ARCHIVE_FAILED"):
        runner._finalize_result({"kind": "failed"}, None, {}, source, run_dir)
    assert runner._verified_score(run_dir)["label"] == "NOT_EVALUATED"
    assert not (run_dir / "runtime_artifacts.zip").exists()
    assert (run_dir / "runtime_artifacts.zip.partial").exists()


def test_score_write_failure_has_distinct_terminal_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    original = Path.open
    def denied(self, *args, **kwargs):
        if self.name == "score.json":
            raise PermissionError("synthetic score write refusal")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(SmokeBlocked, match="SCORE_PERSISTENCE_FAILED"):
        runner._finalize_result({"kind": "failed"}, None, {}, tmp_path, run_dir)
    assert json.loads((run_dir / "score_status.json").read_text())["status"] == "SCORE_PERSISTENCE_FAILED"
    assert not (run_dir / "archive_status.json").exists()


def test_scoring_contract_failure_is_not_archiving_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(SmokeBlocked, match="SCORING_FAILED"):
        runner._finalize_result(
            {"kind": "success", "temporal_decision": {"effective": {"value": "synthetic"}}},
            {"result": {"review_decision": "evidence_sufficient"}}, {}, tmp_path, run_dir,
        )
    assert json.loads((run_dir / "score_status.json").read_text())["status"] == "SCORING_FAILED"
    assert not (run_dir / "archive_status.json").exists()


def test_archive_can_be_repaired_offline_without_provider_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "BATCH", "offline-temporal")
    monkeypatch.setattr(runner, "ORDER", ("T01", "T02"))
    monkeypatch.setattr(runner, "TOTAL_REQUEST_CAP", 2)
    cases = {"cases": [{"run_id": "offline-temporal-T01"}],
             "holdout_case": {"run_id": "offline-temporal-T02"}}
    for name in ("CASES.json", "GOLD.json"):
        (tmp_path / name).write_text(json.dumps(cases if name == "CASES.json" else {}))
    (tmp_path / "PROTOCOL.json").write_text(json.dumps({
        "batch_id": "offline-temporal", "model_id": runner.MODEL,
        "sha256": {name: runner._sha(tmp_path / name) for name in ("CASES.json", "GOLD.json")},
    }))
    source = tmp_path / "source"
    for relative in ("runtime/results", "runtime/models", "vault", "scan"):
        (source / relative).mkdir(parents=True)
    (source / "vault/source.md").write_text("synthetic")
    with sqlite3.connect(source / "runtime/checkpoints.sqlite") as connection:
        connection.execute("CREATE TABLE checkpoint (value TEXT)")
    run_dir = tmp_path / "offline-temporal-T01"
    run_dir.mkdir()
    ledger = AppendOnlyProviderLedger(
        run_dir / "provider_ledger.jsonl", batch_id="offline-temporal",
        run_id="offline-temporal-T01", max_requests=2, max_runtime_runs=1,
        max_input_tokens_per_request=None, case_id="T01",
    )
    ledger.runtime_started()
    try:
        _request(ledger)
        ledger.finish_run(status="FAILED", usage_summary={})
    finally:
        ledger.close()
    (run_dir / "observed.json").write_text("{}")
    original_archive = runner._archive_runtime
    with monkeypatch.context() as patcher:
        patcher.setattr(runner, "_archive_runtime", lambda *_: (_ for _ in ()).throw(OSError("archive fault")))
        with pytest.raises(SmokeBlocked, match="ARTIFACT_ARCHIVE_FAILED"):
            runner._finalize_result({"kind": "failed"}, None, {}, source, run_dir,
                                    repair_root=tmp_path / "repair-T01")
    assert runner._archive_runtime is original_archive
    assert runner._verified_score(run_dir)["label"] == "NOT_EVALUATED"
    assert runner._repair_archive("T01") == 0
    assert json.loads((run_dir / "archive_status.json").read_text())["status"] == "ARCHIVE_FAILED"
    assert json.loads((run_dir / "archive_repair_status.json").read_text())["status"] == "ARCHIVED"
    assert runner._remaining("T02", cases) == 1
    events = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert sum(item["event"] == "request_reserved" for item in events) == 1


def test_prior_requests_cannot_be_reset_by_new_process_or_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "BATCH", "offline-temporal")
    monkeypatch.setattr(runner, "ORDER", ("T01", "T02"))
    monkeypatch.setattr(runner, "TOTAL_REQUEST_CAP", 1)
    cases = {"cases": [{"run_id": "offline-t01"}],
             "holdout_case": {"run_id": "offline-t02"}}
    first = tmp_path / "offline-t01"
    first.mkdir()
    ledger = _ledger(first / "provider_ledger.jsonl")
    try:
        _request(ledger)
        ledger.finish_run(status="COMPLETED", usage_summary={})
    finally:
        ledger.close()
    for name, data in (("observed.json", {}), ("score.json", {"label": "ANSWERED_CORRECT"}),
                       ("archive_status.json", {"status": "ARCHIVED", "archive": "runtime_artifacts.zip"})):
        (first / name).write_text(json.dumps(data), encoding="utf-8")
    (first / "score.sha256").write_text(hashlib.sha256((first / "score.json").read_bytes()).hexdigest())
    with ZipFile(first / "runtime_artifacts.zip", "x") as archive:
        archive.writestr("vault/source.md", "synthetic")
        archive.writestr("manifest.json", json.dumps({"sha256": {
            "vault/source.md": hashlib.sha256(b"synthetic").hexdigest(),
        }}))
    with pytest.raises(SmokeBlocked, match="RUN_ID_ALREADY_USED"):
        runner._remaining("T01", cases)
    with pytest.raises(SmokeBlocked, match="BATCH_REQUEST_CAP_REACHED"):
        runner._remaining("T02", cases)
    archive_path = first / "runtime_artifacts.zip"
    original_archive = archive_path.read_bytes()
    archive_path.write_bytes(b"corrupt archive")
    with pytest.raises(SmokeBlocked, match="ARCHIVE_INTEGRITY_FAILED_STOP"):
        runner._remaining("T02", cases)
    archive_path.write_bytes(original_archive)
    original_score = (first / "score.json").read_bytes()
    (first / "score.json").write_text('{"label":"FAILED"}')
    with pytest.raises(SmokeBlocked, match="SCORE_INTEGRITY_FAILED_STOP"):
        runner._remaining("T02", cases)
    (first / "score.json").write_bytes(original_score)
    (first / "archive_status.json").write_text('{"status":"ARCHIVE_FAILED"}', encoding="utf-8")
    with pytest.raises(SmokeBlocked, match="PRIOR_ARCHIVE_INCOMPLETE_STOP"):
        runner._remaining("T02", cases)
