"""P3-B runtime trace integration coverage."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"


def make_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault_root = tmp_path / "vault"
    shutil.copytree(FIXTURE_ROOT, vault_root)
    scan_result = scan_vault(vault_root, tmp_path / "scan")
    return vault_root, scan_result.index_path


def request(query: str = "Scanner") -> RunRequest:
    return RunRequest(request_id="req_trace_test", workflow="ask", query=query, dry_run=True)


def read_jsonl(trace_root: Path, run_id: str) -> tuple[list[dict], str]:
    path = trace_root / run_id / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines], path.read_text(encoding="utf-8")


def test_completed_runtime_emits_redacted_trace(tmp_path: Path) -> None:
    vault_root, index_path = make_vault(tmp_path)
    trace_root = tmp_path / "traces"
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=tmp_path / "checkpoint",
        trace_dir=trace_root,
    )

    raw_query = "Scanner raw-query-should-not-appear C:/secret.txt"
    status = engine.start(request(raw_query))
    assert status.status == "completed"

    events, raw_log = read_jsonl(trace_root, status.run_id)
    event_types = [event["event_type"] for event in events]
    assert event_types[0:2] == ["run.accepted", "run.started"]
    assert "step.started" in event_types
    assert "step.completed" in event_types
    assert "tool.called" in event_types
    assert "tool.completed" in event_types
    assert "checkpoint.saved" in event_types
    assert "artifact.written" in event_types
    assert event_types[-1] == "run.completed"
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["event_id"] for event in events}) == len(events)
    assert raw_query not in raw_log
    assert str(vault_root.resolve()) not in raw_log
    assert '"quote"' not in raw_log
    assert '"prompt"' not in raw_log

    manifest = json.loads((trace_root / status.run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
    assert manifest["event_count"] == len(events)
    assert manifest["first_seq"] == 1
    assert manifest["last_seq"] == len(events)
    assert manifest["redaction_policy_version"] == "trace-redaction-v1"


def test_failure_and_retry_have_separate_traces(tmp_path: Path) -> None:
    vault_root, index_path = make_vault(tmp_path)
    trace_root = tmp_path / "traces"
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=tmp_path / "checkpoint",
        trace_dir=trace_root,
        fail_at="retrieve_context",
    )

    failed = engine.start(request())
    assert failed.status == "failed"
    failed_events, _ = read_jsonl(trace_root, failed.run_id)
    failed_types = [event["event_type"] for event in failed_events]
    assert "step.started" in failed_types
    assert "step.failed" in failed_types
    assert failed_types[-1] == "run.failed"

    retried = engine.retry(failed.run_id)
    assert retried.run_id != failed.run_id
    old_events, old_log = read_jsonl(trace_root, failed.run_id)
    new_events, new_log = read_jsonl(trace_root, retried.run_id)
    assert any(event["event_type"] == "retry.scheduled" for event in old_events)
    assert all(event["run_id"] == failed.run_id for event in old_events)
    assert all(event["run_id"] == retried.run_id for event in new_events)
    assert old_log.count('"run_id":"' + failed.run_id + '"') == len(old_events)
    assert new_log.count('"run_id":"' + retried.run_id + '"') == len(new_events)
    assert any(event["attributes"].get("parent_run_id") == failed.run_id for event in new_events)


def test_trace_directory_inside_vault_is_rejected(tmp_path: Path) -> None:
    vault_root, index_path = make_vault(tmp_path)
    with pytest.raises(ValidationError, match="trace_dir"):
        RuntimeEngine(
            vault_root=vault_root,
            index_path=index_path,
            checkpoint_dir=tmp_path / "checkpoint",
            trace_dir=vault_root / ".artifacts" / "traces",
        )
