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
    env["PYTHONPATH"] = str(SRC_ROOT)
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
    assert proc.returncode == 0, proc.stderr
    return vault_root, scan_dir / "vault_index.json"


def test_cli_agent_ask_and_trace_are_read_only_and_explainable(
    tmp_path: Path, test_vault: tuple[Path, Path]
) -> None:
    vault_root, index_path = test_vault
    checkpoint = tmp_path / "p4-runtime"
    trace = tmp_path / "p4-traces"
    query = "Scanner secret_token_123"

    proc = run_cli(
        "agent", "ask", str(vault_root),
        "--index", str(index_path), "--query", query,
        "--checkpoint", str(checkpoint), "--trace", str(trace),
    )
    assert proc.returncode == 0, proc.stderr
    assert "Coordination Pattern: manager-as-tools" in proc.stdout
    assert "Retrieval Agent" in proc.stdout
    assert "Reviewer Decision:" in proc.stdout
    assert "Writer Capability: DENY" in proc.stdout
    assert "Gold Access: DENY" in proc.stdout
    assert "Network: DENY" in proc.stdout
    assert query not in proc.stdout
    assert str(vault_root.resolve()) not in proc.stdout

    info = {
        line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
        for line in proc.stdout.splitlines()
        if ":" in line
    }
    run_id = info["Run ID"]
    trace_proc = run_cli("agent", "trace", "--run-id", run_id, "--root", str(trace))
    assert trace_proc.returncode == 0, trace_proc.stderr
    assert "agent.task.created" in trace_proc.stdout
    assert "agent.task.completed" in trace_proc.stdout
    assert query not in trace_proc.stdout
    assert str(vault_root.resolve()) not in trace_proc.stdout


def test_cli_agent_connect_exposes_handoff(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    proc = run_cli(
        "agent", "connect", str(vault_root),
        "--index", str(index_path), "--query", "Scanner",
        "--checkpoint", str(tmp_path / "runtime"),
        "--trace", str(tmp_path / "traces"),
    )
    assert proc.returncode == 0, proc.stderr
    assert "Handoffs:" in proc.stdout
    assert "Curator Agent" in proc.stdout
