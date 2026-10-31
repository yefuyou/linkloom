"""Configuration failures must be actionable before any database or provider work."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from linkloom.ui import __main__ as cli


@pytest.fixture(autouse=True)
def project_entry(tmp_path, monkeypatch):
    # Use a synthetic checkout so tests never load a developer's real .env.
    entry = tmp_path / "project" / "src" / "linkloom" / "ui" / "__main__.py"
    entry.parent.mkdir(parents=True)
    entry.write_text(Path(cli.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(entry))
    for name in ("GEMINI_API_KEY", "LINKLOOM_DATABASE_PATH"):
        # Record restoration even when the variable was originally absent.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("LINKLOOM_SEMANTIC_MODEL", "gemini-3.8-flash")
    return entry


@pytest.mark.parametrize("key", [None, "", "  \t "])
def test_missing_gemini_key_exits_with_setup_and_offline_guidance(tmp_path, project_entry, key):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               GOOGLE_API_KEY="synthetic-google-key", LINKLOOM_SEMANTIC_MODEL="gemini-3.8-flash")
    if key is None:
        env.pop("GEMINI_API_KEY", None)
    else:
        env["GEMINI_API_KEY"] = key
    # A .env in another working directory must not configure this checkout.
    (tmp_path / ".env").write_text("GEMINI_API_KEY=synthetic-file-key\n", encoding="utf-8")
    database = tmp_path / "not-created.sqlite"
    result = subprocess.run(
        [sys.executable, str(project_entry), "--allow-external-provider",
         "--database", str(database)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 2
    assert "GEMINI_API_KEY" in result.stderr
    assert ".env.example" in result.stderr
    assert "PowerShell" in result.stderr and "export GEMINI_API_KEY" in result.stderr
    assert "loads the project root .env" in result.stderr
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



def test_launcher_reads_project_env_before_parser_defaults_from_another_cwd(
    tmp_path, project_entry, monkeypatch, capsys, caplog,
):
    key = "synthetic-project-file-key"
    decoy = "synthetic-other-directory-key"
    database = tmp_path / "from-env.sqlite"
    (project_entry.parents[3] / ".env").write_text(
        f"GEMINI_API_KEY={key}\nLINKLOOM_DATABASE_PATH={database.as_posix()}\n", encoding="utf-8",
    )
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env").write_text(f"GEMINI_API_KEY={decoy}\n", encoding="utf-8")
    monkeypatch.chdir(other)
    monkeypatch.setattr(sys, "argv", ["linkloom.ui", "--allow-external-provider"])
    captured = []

    def unexpected_request(**kwargs):
        pytest.fail("Reading .env must never send a provider request")

    def build(key, **kwargs):
        captured.append(key)
        return SimpleNamespace(models=SimpleNamespace(generate_content=unexpected_request), close=lambda: None)

    monkeypatch.setattr(cli, "build_gemini_sdk_client", build)
    monkeypatch.setattr(cli, "create_http_server", lambda *args, **kwargs: SimpleNamespace(
        server_port=1234, serve_forever=lambda: None, server_close=lambda: None,
    ))
    assert cli.main() == 0
    assert captured == [key]
    assert database.is_file()
    output = capsys.readouterr()
    for secret in (key, decoy):
        assert secret not in output.out + output.err + caplog.text


@pytest.mark.parametrize("existing", ["synthetic-existing-key", ""])
def test_launcher_dotenv_preserves_existing_environment_including_empty_values(
    project_entry, monkeypatch, capsys, caplog, existing,
):
    file_key = "synthetic-file-key-not-used"
    (project_entry.parents[3] / ".env").write_text(f"GEMINI_API_KEY={file_key}\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", existing)
    monkeypatch.setattr(sys, "argv", ["linkloom.ui", "--help"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0
    assert os.environ["GEMINI_API_KEY"] == existing
    output = capsys.readouterr()
    assert file_key not in output.out + output.err + caplog.text
    if existing:
        assert existing not in output.out + output.err + caplog.text


def test_launcher_missing_project_env_is_silent_and_does_not_search_cwd(
    tmp_path, project_entry, monkeypatch, capsys, caplog,
):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=synthetic-cwd-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["linkloom.ui", "--help"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0
    assert "GEMINI_API_KEY" not in os.environ
    assert not (project_entry.parents[3] / ".env").exists()
    output = capsys.readouterr()
    assert "usage:" in output.out
    assert output.err == ""
    assert caplog.text == ""


def test_importing_ui_launcher_does_not_load_env(monkeypatch):
    import dotenv

    def forbidden_load(*args, **kwargs):
        pytest.fail("Only starting the UI may load .env")

    monkeypatch.setattr(dotenv, "load_dotenv", forbidden_load)
    importlib.reload(cli)
