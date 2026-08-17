import re
from typing import Any


class RedactionViolation(ValueError):
    pass


_FORBIDDEN_FIELD_PARTS = (
    "gold",
    "expected",
    "ground_truth",
    "label",
    "answer_key",
    "raw",
    "body",
    "content",
    "quote",
    "excerpt",
    "source_text",
)
_SECRET_FIELD_PARTS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
    "authorization",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{5,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
)


def _is_absolute_path(value: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", value))


def check_for_violations(value: Any, *, field_path: str = "value") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RedactionViolation(f"Non-string field name at {field_path}")
            key_lower = key.lower()
            if any(part in key_lower for part in _FORBIDDEN_FIELD_PARTS):
                raise RedactionViolation(f"Forbidden source/evaluation field: {field_path}.{key}")
            if any(part in key_lower for part in _SECRET_FIELD_PARTS):
                raise RedactionViolation(f"Secret-like field: {field_path}.{key}")
            check_for_violations(child, field_path=f"{field_path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            check_for_violations(child, field_path=f"{field_path}[{index}]")
        return
    if isinstance(value, str):
        if _is_absolute_path(value):
            raise RedactionViolation(f"Absolute filesystem path at {field_path}")
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            raise RedactionViolation(f"Secret-like value at {field_path}")
        if re.search(r"(?i)\bpassword\b", value):
            raise RedactionViolation(f"Secret-like value at {field_path}")
        if len(value) > 1000:
            raise RedactionViolation(f"Value too long at {field_path}; raw source text is not allowed")
        return
    if value is not None and not isinstance(value, (bool, int, float)):
        raise RedactionViolation(f"Unsupported value type at {field_path}")
