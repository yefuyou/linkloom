"""P3-B pause/resume trace continuity coverage."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from linkloom.observability.reader import TraceReader
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


def test_pause_resume_appends_same_trace_and_finalizes_manifest(tmp_path: Path) -> None:
    vault_root, index_path = make_vault(tmp_path)
    checkpoint_root = tmp_path / "checkpoint"
    trace_root = tmp_path / "traces"
    req = RunRequest(request_id="req_resume_trace", workflow="ask", query="Scanner", dry_run=True)

    paused_engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_root,
        trace_dir=trace_root,
        pause_after="retrieve_context",
    )
    paused = paused_engine.start(req)
    assert paused.status == "paused"
    run_id = paused.run_id

    events_path = trace_root / run_id / "events.jsonl"
    before = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    before_last_seq = before[-1]["seq"]
    assert any(event["event_type"] == "interrupt.raised" for event in before)
    assert any(event["event_type"] == "step.completed" and event["status"] == "paused" for event in before)

    manifest_before = json.loads((trace_root / run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_before["complete"] is False
    assert manifest_before["incomplete_reason"] == "paused"

    interrupt_id = paused.interrupt["interrupt_id"] if paused.interrupt else None
    assert interrupt_id
    resumed_engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_root,
        trace_dir=trace_root,
    )
    completed = resumed_engine.resume(paused.thread_id, interrupt_id, "resume")
    assert completed.status == "completed"
    assert completed.run_id == run_id

    after = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert len(after) > len(before)
    assert [event["seq"] for event in after] == list(range(1, len(after) + 1))
    assert after[before_last_seq]["event_type"] == "interrupt.resumed"
    assert after[-1]["event_type"] == "run.completed"
    assert all(event["run_id"] == run_id for event in after)

    manifest_after = json.loads((trace_root / run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_after["complete"] is True
    assert manifest_after["event_count"] == len(after)
    assert manifest_after["last_seq"] == len(after)

    loaded = TraceReader(trace_root).read_events(run_id)
    assert len(loaded) == len(after)
