from __future__ import annotations

import json
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


def test_cli_run_ask_and_connect_completed(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # Test run ask completed
    proc = run_cli(
        "run", "ask", str(vault_root),
        "--index", str(index_path),
        "--query", "Scanner 做了什么",
        "--checkpoint", str(checkpoint_dir),
        "--max-steps", "10",
    )
    assert proc.returncode == 0, proc.stderr
    assert "Status: completed" in proc.stdout
    assert "Run ID:" in proc.stdout
    assert "Thread ID:" in proc.stdout
    assert "Source SHA-256:" in proc.stdout
    assert "Source Write: 0" in proc.stdout

    # Parse stdout helper
    lines = proc.stdout.strip().split("\n")
    info = {}
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()

    assert info["Status"] == "completed"
    assert info["Current Step"] == "None"
    assert info["Result Ref"].startswith("results/run_p2_")

    # Test run connect completed
    proc_conn = run_cli(
        "run", "connect", str(vault_root),
        "--index", str(index_path),
        "--query", "Scanner",
        "--checkpoint", str(tmp_path / "checkpoint_conn"),
    )
    assert proc_conn.returncode == 0, proc_conn.stderr
    assert "Status: completed" in proc_conn.stdout


def test_cli_run_pause_inspect_resume(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # 1. Run ask with pause
    proc_pause = run_cli(
        "run", "ask", str(vault_root),
        "--index", str(index_path),
        "--query", "Scanner",
        "--checkpoint", str(checkpoint_dir),
        "--pause-after", "retrieve_context",
    )
    assert proc_pause.returncode == 0, proc_pause.stderr
    assert "Status: paused" in proc_pause.stdout
    assert "Current Step: emit_result" in proc_pause.stdout

    lines = proc_pause.stdout.strip().split("\n")
    info = {}
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()

    thread_id = info["Thread ID"]
    interrupt_id = info["Interrupt ID"]
    checkpoint_id = info["Checkpoint ID"]
    assert thread_id.startswith("thread_p2_")
    assert interrupt_id.startswith("int_p2_")
    assert checkpoint_id.startswith("cp_")

    # 2. Inspect
    proc_inspect = run_cli(
        "run", "inspect",
        "--thread-id", thread_id,
        "--checkpoint", str(checkpoint_dir),
    )
    assert proc_inspect.returncode == 0, proc_inspect.stderr
    assert f"Thread ID: {thread_id}" in proc_inspect.stdout
    assert "Status: paused" in proc_inspect.stdout
    assert f"Interrupt ID: {interrupt_id}" in proc_inspect.stdout

    # 3. Resume
    proc_resume = run_cli(
        "run", "resume",
        "--thread-id", thread_id,
        "--interrupt-id", interrupt_id,
        "--input", "继续",
        "--vault", str(vault_root),
        "--index", str(index_path),
        "--checkpoint", str(checkpoint_dir),
    )
    assert proc_resume.returncode == 0, proc_resume.stderr
    assert "Status: completed" in proc_resume.stdout
    assert "Current Step: None" in proc_resume.stdout
    assert "Source Write: 0" in proc_resume.stdout

    # 4. Inspect again
    proc_inspect2 = run_cli(
        "run", "inspect",
        "--thread-id", thread_id,
        "--checkpoint", str(checkpoint_dir),
    )
    assert proc_inspect2.returncode == 0, proc_inspect2.stderr
    assert "Status: completed" in proc_inspect2.stdout


def test_cli_run_errors(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # Thread not found for inspect
    proc = run_cli(
        "run", "inspect",
        "--thread-id", "nonexistent_thread_123",
        "--checkpoint", str(checkpoint_dir),
    )
    assert proc.returncode != 0
    assert "Error:" in proc.stderr
