"""CLI output must remain UTF-8 when the inherited stdio encoding is GBK."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_cli_outputs_utf8_with_gbk_stdio(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "meeting.md").write_text("# 项目会议\n负责人：小林。\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"}
    scan_dir = tmp_path / "scan"
    scan = subprocess.run(
        [sys.executable, "-m", "linkloom", "scan", str(vault), "--output", str(scan_dir)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, timeout=30, check=False,
    )
    assert scan.returncode == 0, scan.stderr.decode("utf-8", errors="replace")

    query = "项目😀负责人是谁？"
    ask = subprocess.run(
        [sys.executable, "-m", "linkloom", "ask", str(vault),
         "--index", str(scan_dir / "vault_index.json"), "--query", query,
         "--output", str(tmp_path / "ask")],
        cwd=PROJECT_ROOT, env=env, capture_output=True, timeout=30, check=False,
    )
    assert ask.returncode == 0, ask.stderr.decode("utf-8", errors="replace")
    assert f"Query: {query}" in ask.stdout.decode("utf-8")
