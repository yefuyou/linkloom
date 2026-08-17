"""Small, deterministic safety audits for formal evaluation boundaries.

These checks are evidence-producing guards, not an OS sandbox. They never
include the offending secret, path, or note body in their output.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Mapping

from .models import SafetyReport


_FORBIDDEN_TERMS = ("gold", "expected", "label", "ground_truth", "answer_key")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{5,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
)
_WRITE_METHODS = {"write_text", "write_bytes", "unlink", "rename", "replace", "rmdir", "mkdir"}


def _is_absolute(value: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", value)) or Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _codes(codes: set[str]) -> SafetyReport:
    return SafetyReport(is_safe=not codes, violations=sorted(codes))


def check_inference_payload(payload: dict) -> SafetyReport:
    codes: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                lowered = str(key).casefold()
                if any(term in lowered for term in _FORBIDDEN_TERMS):
                    codes.add("FORBIDDEN_EVALUATION_FIELD")
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
        elif isinstance(obj, str):
            if _is_absolute(obj):
                codes.add("ABSOLUTE_PATH")
            if any(pattern.search(obj) for pattern in _SECRET_PATTERNS):
                codes.add("SECRET_PATTERN")

    walk(payload)
    return _codes(codes)


def _mode_is_write(node: ast.Call) -> bool:
    mode_values = []
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode_values.append(str(keyword.value.value))
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        mode_values.append(str(node.args[1].value))
    return any(mode and any(flag in mode for flag in ("w", "a", "x", "+")) for mode in mode_values)


def check_inference_module(file_path: str) -> SafetyReport:
    codes: set[str] = set()
    try:
        tree = ast.parse(Path(file_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return SafetyReport(is_safe=False, violations=["MODULE_PARSE_ERROR"])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith("relation_eval") for alias in node.names):
                codes.add("Forbidden import: relation_eval")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("relation_eval"):
                codes.add("Forbidden import: relation_eval")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open" and _mode_is_write(node):
                codes.add("Unauthorized file write: open")
            if isinstance(node.func, ast.Attribute) and node.func.attr in _WRITE_METHODS:
                codes.add("Unauthorized file write: method")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "open" and _mode_is_write(node):
                codes.add("Unauthorized file write: open")
    return _codes(codes)


def snapshot_tree(root: str | Path) -> Dict[str, str]:
    root = Path(root).resolve()
    result: Dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def compare_snapshots(before: Mapping[str, str], after: Mapping[str, str]) -> SafetyReport:
    if dict(before) == dict(after):
        return SafetyReport(is_safe=True, violations=[])
    codes: set[str] = set()
    if set(before) != set(after):
        codes.add("UNAUTHORIZED_FILE_SET_CHANGE")
    if any(before.get(path) != after.get(path) for path in set(before) & set(after)):
        codes.add("SOURCE_MUTATION")
    return _codes(codes)
