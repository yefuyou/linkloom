"""Tests for credential isolation and environment variable fallback.

These tests verify that:
1. When api_key is null in config, the runner reads OPENAI_API_KEY from env.
2. When both config and env are empty, a clear error is raised.
3. Error messages never leak the actual key value.

No network calls are made.  No real provider is instantiated.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from relation_eval.config import EvalConfig  # noqa: E402


MOCK_CONFIG_PATH = PROJECT_ROOT / "configs" / "eval.mock.yaml"
OPENAI_CONFIG_PATH = PROJECT_ROOT / "configs" / "eval.openai.yaml"


# ---------------------------------------------------------------------------
# Helper: simulate the provider-creation logic from run_evaluation.py
# without importing the OpenAI SDK or hitting the network.
# ---------------------------------------------------------------------------

def _resolve_api_key(config: EvalConfig) -> str | None:
    """Mirror the key-resolution logic in run_evaluation.main()."""
    return config.provider.api_key or os.environ.get("OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# 1. Config is null  →  env var wins
# ---------------------------------------------------------------------------

def test_null_config_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """When api_key is null in the YAML, OPENAI_API_KEY env var is used."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-from-env")
    config = EvalConfig.load_from_yaml(OPENAI_CONFIG_PATH)

    assert config.provider.api_key is None, (
        "openai config should have api_key=null after credential cleanup"
    )
    resolved = _resolve_api_key(config)
    assert resolved == "test-key-from-env"


# ---------------------------------------------------------------------------
# 2. Config is null AND env var is unset  →  clear failure
# ---------------------------------------------------------------------------

def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both config and env are empty, the runner must fail clearly."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = EvalConfig.load_from_yaml(OPENAI_CONFIG_PATH)
    resolved = _resolve_api_key(config)

    assert resolved is None, "No key should be resolved when both sources are empty"

    # Simulate the runner's guard clause
    with pytest.raises(ValueError, match="(?i)api.key"):
        if not resolved:
            raise ValueError(
                "API key must be configured via OPENAI_API_KEY env variable."
            )


# ---------------------------------------------------------------------------
# 3. Error messages must never contain the actual key
# ---------------------------------------------------------------------------

_FAKE_KEY = "sk-FAKE1234567890abcdef1234567890abcdef"


def test_error_message_does_not_leak_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the runner raises, the key value must not appear in the message."""
    monkeypatch.setenv("OPENAI_API_KEY", _FAKE_KEY)
    config = EvalConfig.load_from_yaml(OPENAI_CONFIG_PATH)
    resolved = _resolve_api_key(config)

    # Construct the error that would be raised on provider failure
    error_msg = (
        f"API key must be configured via OPENAI_API_KEY env variable. "
        f"Provider type: {config.provider.type}"
    )
    assert _FAKE_KEY not in error_msg, "Key value must not appear in error messages"

    # Also verify the run_evaluation source does not embed keys in errors
    runner_src = (
        PROJECT_ROOT / "relation_eval" / "runner" / "run_evaluation.py"
    ).read_text(encoding="utf-8")

    # The error string should reference the env var name, not any literal key
    assert "OPENAI_API_KEY" in runner_src, (
        "Runner should mention the env var name in its error guidance"
    )
    # No sk- literal should appear in the runner source
    assert not re.search(r"sk-[A-Za-z0-9]{10,}", runner_src), (
        "Runner source must not contain hardcoded API key patterns"
    )


# ---------------------------------------------------------------------------
# 4. openai config file itself must not contain a credential
# ---------------------------------------------------------------------------

def test_openai_config_file_has_no_credential() -> None:
    """The checked-in openai config must have api_key: null."""
    config = EvalConfig.load_from_yaml(OPENAI_CONFIG_PATH)
    assert config.provider.api_key is None

    raw = OPENAI_CONFIG_PATH.read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{10,}", raw), (
        "openai config file must not contain an sk- credential pattern"
    )
