import re
from typing import Any, Dict, Iterable

from .redaction import RedactionViolation, check_for_violations


class MemoryPolicyViolation(ValueError):
    pass


_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")
_SAFE_ACTOR_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")


def validate_candidate_value(value: Dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise MemoryPolicyViolation("memory value must be a JSON object")
    try:
        check_for_violations(value)
    except RedactionViolation as exc:
        raise MemoryPolicyViolation(str(exc)) from exc


def validate_source_refs(source_refs: Iterable[str]) -> None:
    if not isinstance(source_refs, list) or not source_refs:
        raise MemoryPolicyViolation("source_refs must be a non-empty list")
    seen = set()
    for ref in source_refs:
        if not isinstance(ref, str) or not _SAFE_REF_RE.fullmatch(ref):
            raise MemoryPolicyViolation("source_refs must be opaque safe identifiers")
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", ref):
            raise MemoryPolicyViolation("source_refs must not contain absolute paths")
        if ref in seen:
            raise MemoryPolicyViolation("source_refs must not contain duplicates")
        seen.add(ref)


def validate_memory_input(
    key: str,
    value: Dict[str, Any],
    source_refs: Iterable[str],
    metadata: Dict[str, Any] | None = None,
) -> None:
    if not isinstance(key, str) or not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$", key):
        raise MemoryPolicyViolation("key must be a short stable identifier")
    validate_candidate_value(value)
    validate_source_refs(source_refs)
    if metadata is not None:
        validate_candidate_value(metadata)


def validate_actor(actor: str) -> None:
    if not isinstance(actor, str) or not _SAFE_ACTOR_RE.fullmatch(actor):
        raise MemoryPolicyViolation("actor must be a short stable identifier")
