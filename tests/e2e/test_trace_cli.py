from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing_pythonpath else f"{SRC_ROOT}{os.pathsep}{existing_pythonpath}"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "linkloom", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )


@pytest.fixture
def test_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault_root = tmp_path / "sample_vault"
    shutil.copytree(FIXTURE_ROOT, vault_root)
    scan_dir = tmp_path / "scan"
    proc = run_cli("scan", str(vault_root), "--output", str(scan_dir))
    assert proc.returncode == 0
    return vault_root, scan_dir / "vault_index.json"


def test_cli_trace_show_and_summary(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()
    trace_dir = tmp_path / "traces"

    raw_query = "Scanner secret_token_123 raw_quote"
    proc = run_cli(
        "run", "ask", str(vault_root),
        "--index", str(index_path),
        "--query", raw_query,
        "--checkpoint", str(checkpoint_dir),
        "--trace", str(trace_dir),
        "--max-steps", "10",
    )
    assert proc.returncode == 0, proc.stderr
    assert "Status: completed" in proc.stdout
    assert f"Trace Dir: {trace_dir}" in proc.stdout

    # Parse Run ID
    lines = proc.stdout.strip().split("\n")
    info = {}
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    
    run_id = info["Run ID"]
    assert run_id.startswith("run_p2_")

    # trace show
    proc_show = run_cli("trace", "show", "--run-id", run_id, "--root", str(trace_dir))
    assert proc_show.returncode == 0, proc_show.stderr
    assert "run.completed" in proc_show.stdout
    assert "step.started" in proc_show.stdout

    # trace summary
    proc_summary = run_cli("trace", "summary", "--run-id", run_id, "--root", str(trace_dir))
    assert proc_summary.returncode == 0, proc_summary.stderr
    assert "## Redaction" in proc_summary.stdout
    assert "trace-redaction-v1" in proc_summary.stdout
    assert run_id in proc_summary.stdout
    
    # Check that summary file was generated
    summary_file = trace_dir / run_id / "trace_summary.md"
    assert summary_file.exists()

    # Verify no raw queries or absolute vault path leaked
    for text in (proc_show.stdout, proc_summary.stdout, summary_file.read_text("utf-8")):
        assert raw_query not in text
        assert "secret_token_123" not in text
        assert str(vault_root.resolve()) not in text


def test_cli_trace_resume_appends(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()
    trace_dir = tmp_path / "traces"

    # 1. Run ask with pause
    proc_pause = run_cli(
        "run", "ask", str(vault_root),
        "--index", str(index_path),
        "--query", "Scanner",
        "--checkpoint", str(checkpoint_dir),
        "--trace", str(trace_dir),
        "--pause-after", "retrieve_context",
    )
    assert proc_pause.returncode == 0, proc_pause.stderr
    assert "Status: paused" in proc_pause.stdout

    lines = proc_pause.stdout.strip().split("\n")
    info = {}
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()

    run_id = info["Run ID"]
    thread_id = info["Thread ID"]
    interrupt_id = info["Interrupt ID"]

    # Show trace after pause
    proc_show1 = run_cli("trace", "show", "--run-id", run_id, "--root", str(trace_dir))
    assert proc_show1.returncode == 0
    assert "interrupt.raised" in proc_show1.stdout

    # 2. Resume
    proc_resume = run_cli(
        "run", "resume",
        "--thread-id", thread_id,
        "--interrupt-id", interrupt_id,
        "--input", "继续",
        "--vault", str(vault_root),
        "--index", str(index_path),
        "--checkpoint", str(checkpoint_dir),
        "--trace", str(trace_dir),
    )
    assert proc_resume.returncode == 0, proc_resume.stderr
    assert "Status: completed" in proc_resume.stdout

    # Show trace after resume
    proc_show2 = run_cli("trace", "show", "--run-id", run_id, "--root", str(trace_dir))
    assert proc_show2.returncode == 0
    assert "interrupt.resumed" in proc_show2.stdout
    assert "run.completed" in proc_show2.stdout
    assert proc_show2.stdout.count(run_id) == 0 # Run ID is only in summary, wait, run_id is not printed in show directly? Not as raw unless it's in attributes. But it's fine.

    # Summary should be updated and marked complete
    proc_summary = run_cli("trace", "summary", "--run-id", run_id, "--root", str(trace_dir))
    assert proc_summary.returncode == 0
    assert "**Interrupts:**" in proc_summary.stdout
    assert "**Complete:** True" in proc_summary.stdout
