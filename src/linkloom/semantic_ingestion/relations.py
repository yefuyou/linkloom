"""Read-only, versioned relation resolution for candidate extraction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping

from linkloom.semantic_ingestion.candidate_models import RelationResolution


def explicit_replacement(quote: str, new_value: str, old_value: str) -> bool:
    """Recognize value-bound affirmative replacement; never infer from comparison."""
    new, old = _normalize_relation(new_value), _normalize_relation(old_value)
    if not new or not old or new == old:
        return False
    for sentence in re.split(r"[。！？\n]|[.!?](?:\s+|$)", _normalize_relation(quote)):
        if re.search(r"\b(?:not|never|propose|proposes|consider|considering)\b|建议|考虑|尚未|未批准|不批准|不替代|不会", sentence):
            continue
        if any(phrase in sentence for phrase in (
            f"{new} replaces {old}", f"{new} replaced {old}",
            f"{new} supersedes {old}", f"{new} superseded {old}",
            f"{old} was replaced by {new}", f"{old} is replaced by {new}",
            f"{old} was superseded by {new}", f"{old} is superseded by {new}",
        )):
            return True
        # Both named values must be tied to selection and replacement in the
        # same sentence. Dates and selection words may qualify the old decision.
        if re.search(
            r"(?:正式改为|正式采用|正式选用|决定采用|决定改用|已改用|现采用)\s*"
            + re.escape(new) + r"[^。！？\n]*?(?:替代|取代)"
            + r"(?:\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})?"
            + r"(?:选择|采用|选用)?\s*" + re.escape(old), sentence,
        ):
            return True
        if any(re.search(pattern, sentence) for pattern in (
            r"由\s*" + re.escape(old) + r"\s*(?:正式)?改为\s*" + re.escape(new),
            re.escape(new) + r"\s*(?:正式)?替换\s*" + re.escape(old),
            re.escape(old) + r"\s*(?:已)?被\s*" + re.escape(new) + r"\s*(?:正式)?(?:取代|替代|替换)",
        )):
            return True
    return False


def _normalize_relation(value: str) -> str:
    return " ".join(value.casefold().split()).strip(" .!?\t\r\n")


@dataclass(frozen=True, slots=True)
class RelationResolutionResult:
    status: RelationResolution
    canonical_relation: str | None
    source_phrase: str | None


@dataclass(frozen=True, slots=True, init=False)
class FrozenRelationResolver:
    """Resolve only exact catalog values and explicitly supplied local aliases.

    This value owns a private immutable snapshot. It never writes back to the
    relation catalog or creates aliases as a side effect of extraction.
    """

    _canonical_relations: tuple[str, ...]
    _aliases: Mapping[str, str]
    _schema_version: str
    _fingerprint: str

    def __init__(
        self,
        canonical_relations: tuple[str, ...] | list[str],
        *,
        aliases: Mapping[str, str] | None = None,
        schema_version: str = "relation-catalog/v1",
    ) -> None:
        if not isinstance(canonical_relations, (tuple, list)):
            raise ValueError("canonical_relations must be a tuple or list")
        if any(not isinstance(item, str) or not item.strip() for item in canonical_relations):
            raise ValueError("canonical relations must be non-empty strings")
        canonical_by_key: dict[str, str] = {}
        for relation in canonical_relations:
            key = _normalize_relation(relation)
            if not key:
                raise ValueError("canonical relation is empty after normalization")
            canonical_by_key.setdefault(key, relation.strip())
        if not isinstance(schema_version, str) or not schema_version.strip():
            raise ValueError("schema_version is required")

        normalized_aliases: dict[str, str] = {}
        if aliases is not None:
            if not isinstance(aliases, Mapping):
                raise ValueError("aliases must be a mapping")
            for alias, target in aliases.items():
                if not isinstance(alias, str) or not isinstance(target, str):
                    raise ValueError("relation aliases must map text to text")
                alias_key = _normalize_relation(alias)
                target_key = _normalize_relation(target)
                canonical_target = canonical_by_key.get(target_key)
                if not alias_key or canonical_target is None:
                    raise ValueError("every relation alias must target a canonical relation")
                direct_canonical = canonical_by_key.get(alias_key)
                if direct_canonical is not None and direct_canonical != canonical_target:
                    raise ValueError("an alias cannot override a different canonical relation")
                previous = normalized_aliases.get(alias_key)
                if previous is not None and previous != canonical_target:
                    raise ValueError("one relation alias cannot target multiple relations")
                normalized_aliases[alias_key] = canonical_target

        canonical_values = tuple(
            sorted(canonical_by_key.values(), key=lambda item: _normalize_relation(item))
        )
        fingerprint_payload = {
            "schema_version": schema_version.strip(),
            "canonical_relations": canonical_values,
            "aliases": dict(sorted(normalized_aliases.items())),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "_canonical_relations", canonical_values)
        object.__setattr__(
            self,
            "_aliases",
            MappingProxyType(dict(sorted(normalized_aliases.items()))),
        )
        object.__setattr__(self, "_schema_version", schema_version.strip())
        object.__setattr__(self, "_fingerprint", fingerprint)

    @property
    def canonical_relations(self) -> tuple[str, ...]:
        return self._canonical_relations

    @property
    def aliases(self) -> Mapping[str, str]:
        return self._aliases

    @property
    def schema_version(self) -> str:
        return self._schema_version

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def resolve(self, phrase: str | None) -> RelationResolutionResult:
        if phrase is None or not isinstance(phrase, str) or not phrase.strip():
            return RelationResolutionResult(
                RelationResolution.UNRESOLVED_RELATION,
                canonical_relation=None,
                source_phrase=phrase if isinstance(phrase, str) else None,
            )
        key = _normalize_relation(phrase)
        if not key:
            return RelationResolutionResult(
                RelationResolution.UNRESOLVED_RELATION,
                canonical_relation=None,
                source_phrase=phrase,
            )
        canonical_by_key = {_normalize_relation(item): item for item in self._canonical_relations}
        canonical = canonical_by_key.get(key) or self._aliases.get(key)
        if canonical is not None:
            return RelationResolutionResult(
                RelationResolution.CANONICAL_RELATION,
                canonical_relation=canonical,
                source_phrase=phrase,
            )
        return RelationResolutionResult(
            RelationResolution.NEW_RELATION_CANDIDATE,
            canonical_relation=None,
            source_phrase=phrase,
        )
