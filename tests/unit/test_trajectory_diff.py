"""Trajectory Diff: real offline runs, N/A handling, diagnostics and HTML safety."""

from __future__ import annotations

import importlib.util
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from linkloom.observability.events import TraceEvent
from linkloom.observability.trajectory_diff import (
    _clean,
    _recovery,
    diff_runs,
    load_run,
    max_concurrency,
)
from linkloom.observability.trajectory_diff_html import render_html
from linkloom.runtime.checkpoint import SQLiteCheckpointer

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_trajectory_diff.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("export_trajectory_diff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("trajectory_demo")
    runs = _load_script().run_demo(out)
    return out, runs


def test_two_real_runs_load_diff_and_render(demo):
    out, (a, b) = demo
    assert a["run_id"] != b["run_id"]
    for run in (a, b):
        assert run["provenance"]["diagnostics"] == []
        assert run["provenance"]["is_mock"] is True
        assert run["l1"]["record_complete"]["value"] is True
        assert run["l1"]["source_index_matches"]["value"] is True
    assert a["l3"]["provider_requests"]["value"] == 2 and b["l3"]["provider_requests"]["value"] == 3
    diff = diff_runs(a, b)
    assert diff["evidence"]["comparable"] is True
    assert len(diff["evidence"]["shared"]) == 1 and len(diff["evidence"]["only_a"]) == 1
    assert diff["evidence"]["only_b"] == []
    assert a["recovery"]["decision"]["decision"] == "already_terminal"
    page = render_html(diff)
    assert "<script src" not in page and "<link" not in page
    assert not re.search(r"https?://", page)
    assert a["run_id"] in page and b["run_id"] in page
    assert "Shared evidence for" not in page  # source quotes are never exported


def test_missing_data_is_na_never_zero_or_guessed(demo):
    _, (a, _b) = demo
    assert a["l2"]["gold_recall"]["value"] is None and a["l2"]["gold_recall"]["na"]
    assert a["l2"]["rubric_accuracy"]["value"] is None
    for key in ("input_tokens", "output_tokens", "estimated_cost_usd", "wall_time_ms", "summed_duration_ms", "max_concurrency"):
        assert a["l3"][key]["value"] is None and a["l3"][key]["na"], key


def test_no_checkpoint_gives_na_and_diagnostic(demo):
    out, (a, _b) = demo
    run = load_run("A", out / "traces", a["run_id"], None)
    assert run["l3"]["provider_requests"]["value"] is None
    assert run["l2"]["final_status"]["value"] is None
    assert run["recovery"] is None
    assert any(d.startswith("checkpoint:") for d in run["provenance"]["diagnostics"])


def test_corrupt_and_incomplete_traces_have_diagnostics(demo, tmp_path):
    out, (a, b) = demo
    root = tmp_path / "traces"
    shutil.copytree(out / "traces", root)
    events = root / a["run_id"] / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    events.write_text("\n".join(lines[:3] + ["{not json"]) + "\n", encoding="utf-8")
    (root / b["run_id"] / "manifest.json").write_text('{"run_id": "%s"}' % b["run_id"], encoding="utf-8")
    bad_events = load_run("A", root, a["run_id"])
    assert any(d.startswith("events:") and "Corrupted JSON" in d for d in bad_events["provenance"]["diagnostics"])
    bad_manifest = load_run("B", root, b["run_id"])
    assert any(d.startswith("manifest:") and "missing required fields" in d for d in bad_manifest["provenance"]["diagnostics"])
    missing = load_run("X", root, "run_does_not_exist")
    assert any("manifest not found" in d for d in missing["provenance"]["diagnostics"])
    assert any("events.jsonl missing" in d for d in missing["provenance"]["diagnostics"])
    render_html(diff_runs(bad_events, bad_manifest))  # degraded runs still render


def test_runs_are_never_merged_across_run_ids(demo):
    out, (a, b) = demo
    with pytest.raises(ValueError):
        diff_runs(a, a)
    # B's trace with A's checkpoint store: thread does not exist there, state is not borrowed.
    crossed = load_run("B", out / "traces", b["run_id"], out / "checkpoints_A")
    assert crossed["provenance"]["checkpoint_loaded"] is False
    assert crossed["l2"]["cited"] == []


def _event(seq, eid, parent=None, start="2026-01-01T00:00:00Z", end="2026-01-01T00:00:10Z"):
    return TraceEvent(
        event_id=eid, run_id="r", thread_id="t", seq=seq, event_type="step.completed",
        actor="runtime", status="ok", parent_event_id=parent, started_at=start, finished_at=end,
    )


def test_nested_events_are_not_concurrency():
    parent = _event(1, "p")
    child = _event(2, "c", parent="p", start="2026-01-01T00:00:01Z", end="2026-01-01T00:00:02Z")
    assert max_concurrency([parent, child])["value"] == 1
    sibling_a = _event(3, "s1", parent="p")
    sibling_b = _event(4, "s2", parent="p")
    assert max_concurrency([parent, sibling_a, sibling_b])["value"] == 2
    assert max_concurrency([_event(1, "x", start=None, end=None)])["value"] is None


def test_recovery_marks_unverified_model_outcome_as_blocked(demo):
    out, (a, _b) = demo
    state = SQLiteCheckpointer(out / "checkpoints_A").load("traj-diff-A")
    sent = replace(state.model_executions[-1], status="request_sent")
    state = replace(state, termination=None, model_executions=[*state.model_executions[:-1], sent])
    info = _recovery(state, out / "checkpoints_A")
    assert info["decision"]["decision"] == "requires_verification"
    assert "unverified" in info["model_executions"][-1]["note"]


def test_html_escapes_hostile_strings_and_scrubs_secrets(demo):
    _, (a, b) = demo
    hostile = '</script><script>alert(1)</script><img src=x onerror=alert(1)>'
    a = {**a, "label": hostile, "provenance": {**a["provenance"], "diagnostics": [hostile], "data_source": hostile}}
    page = render_html(diff_runs(a, b))
    assert "</script><script>alert(1)" not in page
    assert page.count("</script>") == 2  # data block + app script only
    assert "innerHTML" not in page
    cleaned = _clean({"note": "key sk-ABCDEF1234567890 and Bearer abcdefghijkl", "p": "/home/user/secret.md"})
    assert "sk-ABCDEF" not in str(cleaned) and "Bearer abcdefghijkl" not in str(cleaned)
    assert cleaned["p"] == "<absolute-path-redacted>"
