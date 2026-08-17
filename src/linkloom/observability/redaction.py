"""Allowlist-driven redaction for local LinkLoom traces.

The trace boundary is deliberately lossy: it records enough metadata to
debug a run without becoming a second copy of the Vault or the prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping


DEFAULT_REDACTION_POLICY_VERSION = "trace-redaction-v1"
_SENSITIVE_PARTS = {
    "key",
    "token",
    "password",
    "authorization",
    "secret",
    "credential",
    "cookie",
    "session",
}
_CONTENT_KEYS = {"prompt", "quote", "content", "text", "raw_text", "note_body"}
_PATH_KEYS = {
    "path",
    "relative_path",
    "file_path",
    "note_path",
    "index_path",
    "artifact_path",
    "vault_path",
    "root_path",
}


def _key_parts(key: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", key.lower()) if part}


def _is_sensitive_key(key: str) -> bool:
    parts = _key_parts(key)
    return bool(parts & _SENSITIVE_PARTS)


def _is_content_key(key: str) -> bool:
    return key.lower() in _CONTENT_KEYS


def _is_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _is_json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list):
        return all(_is_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_safe(item) for key, item in value.items())
    return False


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _sha256(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_ref(value: Any) -> dict[str, str]:
    """Return a content-free SHA-256 reference for arbitrary input."""

    if isinstance(value, (dict, list)):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        payload = value
    return {"kind": "sha256", "sha256": _sha256(payload)}


@dataclass(frozen=True)
class RedactionResult:
    redacted_data: dict[str, Any]
    policy_version: str
    secrets_detected: int = 0
    truncated_fields: list[str] = field(default_factory=list)
    raw_content_included: bool = False


class RedactionPolicy:
    """The P3 v1 trace redaction contract.

    The implementation walks structured data and emits only JSON-safe values.
    Unknown Python objects are dropped instead of stringified, which prevents
    an object's repr from becoming an accidental secret channel.
    """

    def __init__(
        self,
        policy_version: str = DEFAULT_REDACTION_POLICY_VERSION,
        include_relative_paths: bool = True,
        include_absolute_paths: bool = False,
        include_quotes: bool = False,
        quote_max_chars: int = 0,
        include_prompt_text: bool = False,
        include_secret_values: bool = False,
        hash_algorithm: str = "sha256",
        max_attribute_bytes: int = 4096,
    ) -> None:
        if policy_version != DEFAULT_REDACTION_POLICY_VERSION:
            raise ValueError(f"unsupported redaction policy: {policy_version}")
        if hash_algorithm != "sha256":
            raise ValueError("trace-redaction-v1 only permits sha256")
        if max_attribute_bytes < 1 or quote_max_chars < 0:
            raise ValueError("redaction size limits must be non-negative")
        self.policy_version = policy_version
        self.include_relative_paths = include_relative_paths
        self.include_absolute_paths = include_absolute_paths
        self.include_quotes = include_quotes
        self.quote_max_chars = quote_max_chars
        self.include_prompt_text = include_prompt_text
        self.include_secret_values = include_secret_values
        self.hash_algorithm = hash_algorithm
        self.max_attribute_bytes = max_attribute_bytes

    def _hash_only(self, value: Any, kind: str = "hash_only") -> dict[str, str]:
        return {"kind": kind, "sha256": _sha256(value)}

    def apply(self, data: Mapping[str, Any]) -> RedactionResult:
        if not isinstance(data, Mapping):
            raise TypeError("trace attributes must be a mapping")

        secrets_detected = 0
        truncated_fields: list[str] = []
        raw_content_included = False

        def redact_value(key: str, value: Any, field_path: str) -> Any:
            nonlocal secrets_detected, raw_content_included

            if _is_sensitive_key(key):
                secrets_detected += 1
                if not self.include_secret_values:
                    return None
                raw_content_included = True
                return value if _is_json_safe(value) else None

            if _is_content_key(key):
                if key.lower() == "prompt" and self.include_prompt_text:
                    raw_content_included = True
                    return str(value)[: self.quote_max_chars or None]
                if key.lower() in {"quote", "content", "text", "raw_text", "note_body"} and self.include_quotes:
                    raw_content_included = True
                    return str(value)[: self.quote_max_chars or None]
                return None

            if key.lower() == "query":
                return self._hash_only(value)

            if isinstance(value, str):
                if _is_absolute_path(value):
                    if self.include_absolute_paths:
                        raw_content_included = True
                        result: Any = value
                    else:
                        result = self._hash_only(value, "fingerprint")
                elif key.lower() in _PATH_KEYS:
                    if self.include_relative_paths:
                        result = value
                    else:
                        result = self._hash_only(value, "fingerprint")
                else:
                    result = value
            elif isinstance(value, Mapping):
                result = {}
                for child_key, child_value in value.items():
                    if not isinstance(child_key, str):
                        continue
                    child = redact_value(child_key, child_value, f"{field_path}.{child_key}")
                    if child is not None:
                        result[child_key] = child
            elif isinstance(value, list):
                result = [redact_value(key, item, f"{field_path}[{idx}]") for idx, item in enumerate(value)]
                result = [item for item in result if item is not None]
            elif _is_json_safe(value):
                result = value
            else:
                truncated_fields.append(field_path)
                return None

            if result is not None and _json_size(result) > self.max_attribute_bytes:
                truncated_fields.append(field_path)
                return "<truncated due to size>"
            return result

        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            result = redact_value(key, value, key)
            if result is not None:
                redacted[key] = result

        if truncated_fields:
            redacted["truncated_fields"] = sorted(set(truncated_fields))
        return RedactionResult(
            redacted_data=redacted,
            policy_version=self.policy_version,
            secrets_detected=secrets_detected,
            truncated_fields=sorted(set(truncated_fields)),
            raw_content_included=raw_content_included,
        )

    def redact_ref(self, ref: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Keep only safe artifact-reference fields and never raw content."""

        if ref is None:
            return None
        if not isinstance(ref, Mapping):
            return safe_ref(ref)

        result: dict[str, Any] = {}
        allowed = {"kind", "type", "artifact_id", "id", "sha256", "path", "relative_path"}
        for key, value in ref.items():
            if key not in allowed:
                continue
            if key in {"path", "relative_path"} and isinstance(value, str):
                if _is_absolute_path(value):
                    result = {"kind": "fingerprint", "sha256": _sha256(value)}
                    break
                if self.include_relative_paths:
                    result[key] = value
                continue
            if key == "sha256" and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                result[key] = value
                continue
            if key in {"kind", "type", "artifact_id", "id"} and isinstance(value, str):
                result[key] = value

        for content_key in ("content", "quote", "prompt", "query"):
            if content_key in ref:
                result[f"{content_key}_sha256"] = _sha256(ref[content_key])
        if not result:
            return safe_ref(dict(ref))
        return result
