from __future__ import annotations

import json
import os
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


def test_cli_e2e_ask_and_connect(tmp_path: Path) -> None:
    # 1. Scan
    scan_dir = tmp_path / "scan"
    proc_scan = run_cli("scan", str(FIXTURE_ROOT), "--output", str(scan_dir))
    assert proc_scan.returncode == 0, proc_scan.stderr
    index_file = scan_dir / "vault_index.json"
    assert index_file.exists()

    # 2. Ask
    ask_dir = tmp_path / "ask"
    proc_ask = run_cli(
        "ask",
        str(FIXTURE_ROOT),
        "--index",
        str(index_file),
        "--query",
        "Scanner 做了什么",
        "--output",
        str(ask_dir),
    )
    assert proc_ask.returncode == 0, proc_ask.stderr
    assert "Status: completed" in proc_ask.stdout
    assert "Evidence count:" in proc_ask.stdout
    assert (ask_dir / "result.json").exists()
    assert (ask_dir / "report.md").exists()

    # 3. Connect
    connect_dir = tmp_path / "connect"
    proc_connect = run_cli(
        "connect",
        str(FIXTURE_ROOT),
        "--index",
        str(index_file),
        "--query",
        "Scanner",
        "--output",
        str(connect_dir),
    )
    assert proc_connect.returncode == 0, proc_connect.stderr
    assert "Status: completed" in proc_connect.stdout
    assert "Candidate pairs:" in proc_connect.stdout
    assert "Evidence count:" in proc_connect.stdout
    assert (connect_dir / "result.json").exists()
    assert (connect_dir / "report.md").exists()

    connect_json = json.loads((connect_dir / "result.json").read_text(encoding="utf-8"))
    assert len(connect_json["candidates"]) > 0
    assert len(connect_json["evidence"]) > 0
    assert all(len(c["evidence"]) > 0 for c in connect_json["candidates"])


def test_cli_e2e_no_evidence_query(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan"
    run_cli("scan", str(FIXTURE_ROOT), "--output", str(scan_dir))
    index_file = scan_dir / "vault_index.json"

    ask_dir = tmp_path / "ask_no_evidence"
    proc_ask = run_cli(
        "ask",
        str(FIXTURE_ROOT),
        "--index",
        str(index_file),
        "--query",
        "totally_unknown_concept_xyz_999",
        "--output",
        str(ask_dir),
    )
    assert proc_ask.returncode == 0, proc_ask.stderr
    assert "Status: no_evidence" in proc_ask.stdout
    assert "Evidence count: 0" in proc_ask.stdout

    result = json.loads((ask_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "no_evidence"
    assert result["evidence"] == []


def test_cli_e2e_error_handling(tmp_path: Path) -> None:
    # Invalid index
    proc = run_cli(
        "ask",
        str(FIXTURE_ROOT),
        "--index",
        str(tmp_path / "missing.json"),
        "--query",
        "Test",
        "--output",
        str(tmp_path / "out"),
    )
    assert proc.returncode != 0
    assert "Error:" in proc.stderr
