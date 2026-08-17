import pytest
from linkloom.observability.redaction import RedactionPolicy

def test_redaction_removes_absolute_paths():
    policy = RedactionPolicy(include_absolute_paths=False)
    data = {"path": "C:/etc/passwd"}
    result = policy.apply(data)
    assert result.redacted_data["path"]["kind"] == "fingerprint"
    assert "sha256" in result.redacted_data["path"]

def test_redaction_keeps_relative_paths():
    policy = RedactionPolicy(include_relative_paths=True)
    data = {"path": "docs/readme.md"}
    result = policy.apply(data)
    assert result.redacted_data["path"] == "docs/readme.md"

def test_redaction_secrets():
    policy = RedactionPolicy(include_secret_values=False)
    data = {"api_key": "sk-12345", "normal": "value"}
    result = policy.apply(data)
    assert "api_key" not in result.redacted_data
    assert result.redacted_data["normal"] == "value"
    assert result.secrets_detected == 1

def test_redaction_quotes_and_prompts():
    policy = RedactionPolicy(include_quotes=False, include_prompt_text=False)
    data = {"quote": "This is a secret quote", "prompt": "system prompt"}
    result = policy.apply(data)
    assert "quote" not in result.redacted_data
    assert "prompt" not in result.redacted_data
    assert result.raw_content_included is False

def test_redaction_large_attributes():
    policy = RedactionPolicy(max_attribute_bytes=10)
    data = {"large_text": "this is a very long string"}
    result = policy.apply(data)
    assert result.redacted_data["large_text"] == "<truncated due to size>"
    assert "large_text" in result.truncated_fields

def test_redaction_ref():
    policy = RedactionPolicy(include_absolute_paths=False, include_quotes=False)
    ref = {"path": "C:/etc/shadow", "content": "secret text"}
    redacted_ref = policy.redact_ref(ref)
    assert redacted_ref["kind"] == "fingerprint"
    assert "sha256" in redacted_ref
    assert "content" not in redacted_ref
