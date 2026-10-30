"""Configuration failures must be actionable before any database or provider work."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from linkloom.ui import __main__ as cli


@pytest.mark.parametrize("key", [None, "", "  \t "])
def test_missing_gemini_key_exits_with_setup_and_offline_guidance(tmp_path, key):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               GOOGLE_API_KEY="synthetic-google-key", LINKLOOM_SEMANTIC_MODEL="gemini-3.8-flash")
    if key is None:
        env.pop("GEMINI_API_KEY", None)
    else:
        env["GEMINI_API_KEY"] = key
    # A reference file alone must not silently enable external service access.
    (tmp_path / ".env").write_text("GEMINI_API_KEY=synthetic-file-key\n", encoding="utf-8")
    database = tmp_path / "not-created.sqlite"
    result = subprocess.run(
        [sys.executable, "-m", "linkloom.ui", "--allow-external-provider",
         "--database", str(database)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 2
    assert "GEMINI_API_KEY" in result.stderr
    assert ".env.example" in result.stderr
    assert "PowerShell" in result.stderr and "export GEMINI_API_KEY" in result.stderr
    assert "does not load .env automatically" in result.stderr
    assert "save sources locally" in result.stderr
    assert "review existing candidates" in result.stderr
    assert "query approved decisions and their history" in result.stderr
    assert "--offline-task-demo" in result.stderr
    assert "Traceback" not in result.stderr
    assert "synthetic-google-key" not in result.stderr
    assert "synthetic-file-key" not in result.stderr
    assert not database.exists()


def test_no_key_startup_and_read_only_status_explain_local_features(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LINKLOOM_SEMANTIC_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["linkloom.ui", "--database", str(tmp_path / "product.sqlite")])
    captured = {}

    def forbidden_provider(*args, **kwargs):
        pytest.fail("Offline startup must not initialize an external provider")

    def server(_backend, **kwargs):
        captured.update(kwargs["product_app"].handle_get("/api/status"))
        return SimpleNamespace(server_port=1234, serve_forever=lambda: None, server_close=lambda: None)

    monkeypatch.setattr(cli, "build_gemini_sdk_client", forbidden_provider)
    monkeypatch.setattr(cli, "create_http_server", server)
    assert cli.main() == 0
    assert captured["ready"] is False
    for text in (capsys.readouterr().out, captured["message"]):
        assert "GEMINI_API_KEY" in text and ".env.example" in text
        assert "save sources locally" in text and "query approved decisions" in text
        assert "--offline-task-demo" in text


def test_provider_initialization_error_has_guidance_without_exception_details(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-provider-key")
    monkeypatch.delenv("LINKLOOM_SEMANTIC_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["linkloom.ui", "--allow-external-provider",
                                      "--database", str(tmp_path / "not-created.sqlite")])

    def broken_provider(*args, **kwargs):
        raise ValueError("synthetic-sensitive-exception-detail")

    monkeypatch.setattr(cli, "build_gemini_sdk_client", broken_provider)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "GEMINI_API_KEY" in message and ".env.example" in message
    assert "pip install" in message and "[smoke]" in message
    assert "Without an API key" in message
    assert "synthetic-sensitive-exception-detail" not in message
    assert "synthetic-provider-key" not in message
    assert "Traceback" not in message
    assert not (tmp_path / "not-created.sqlite").exists()
