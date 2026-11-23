"""Host-side deterministic checks for model-proposed Typed Claims.

The model understands the question and the sources. It proposes typed claims
(subject, predicate, object, time) bound to evidence IDs and exact quotes. The
Host never re-parses answer or source wording with sentence patterns. It only
checks what code can check deterministically:

1. the cited evidence was returned by a successful ``read_verified_note``
   (a ``search_notes`` hit alone is not evidence);
2. the EvidenceSpan is intact: verified status, recomputable evidence ID,
   quote hash, line span, and (when the Host froze sources) the source version;
3. workspace isolation and blocked entities;
4. typed-claim structure (predicate registry, ISO dates; see team_decision);
5. conflicts with Host-confirmed facts (``ConfirmedFactIndex``) and between
   the claims themselves, so conflicting sides are reported, never merged.

Unconfirmed meeting text may support a claim through a valid EvidenceSpan;
a claim is not required to exist in the decision store first.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from linkloom.evidence_identity import passage_evidence_id


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT = "INSUFFICIENT"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class _Fact:
    """One structured source fact (from a Host-confirmed receipt)."""

    relation: str
    subject: str
    value: str
    valid_on: str | None = None
    temporal_kind: str | None = None
    project_scope: str | None = None
    valid_to: str | None = None
    origin: str = "confirmed"


# Typed relation registry. Adding a relation needs one entry here and never a
# sentence pattern:
# - subject_type / object_type / direction: shown to the model;
# - fact_relation: the structured fact relation for confirmed facts;
# - graph_edge: (confirmed graph edge type, endpoint holding the claim subject);
# - single_valued: two objects for one subject and time are a conflict;
# - asymmetric: (a, b) and (b, a) for the same time are a conflict;
# - clause: the Chinese projection of a verified claim.
TYPED_CLAIM_RELATIONS: dict[str, dict[str, Any]] = {
    "alias_of_project": {
        "subject_type": "alias",
        "object_type": "project",
        "direction": "The subject name is an alias for the object project; this says nothing about ownership.",
        "fact_relation": "alias_of",
        "graph_edge": ("alias_of", "source"),
        "single_valued": True,
        "asymmetric": True,
        "clause": "{subject}是{object}的别名",
    },
    "owner_of_project": {
        "subject_type": "project",
        "object_type": "person",
        "direction": "The subject project has the object person as its owner.",
        "fact_relation": "owner_of",
        "graph_edge": ("owner_of", "target"),
        "single_valued": True,
        "asymmetric": True,
        "clause": "{subject}的负责人是{object}",
    },
    "reports_to": {
        "subject_type": "person",
        "object_type": "person",
        "direction": "The subject person reports to the object person.",
        "fact_relation": "reports_to",
        "graph_edge": ("reports_to", "source"),
        "single_valued": True,
        "asymmetric": True,
        # A confirmed reports_to edge does not say "direct", so the projection must not either (#149).
        "clause": "{subject}报告给{object}",
    },
    "replaced_by": {
        "subject_type": "project",
        "object_type": "project",
        "direction": (
            "The subject plan or project was replaced by the object plan or project. "
            "Put the date the replacement took effect in valid_on with temporal_kind=event."
        ),
        "fact_relation": "replaced_by",
        "graph_edge": ("supersedes", "target"),
        "single_valued": True,
        "asymmetric": True,
        "clause": "{subject}被{object}替代",
    },
}

_REGISTRY_FIELDS = frozenset({
    "subject_type", "object_type", "direction", "fact_relation", "graph_edge",
    "single_valued", "asymmetric", "clause",
})


def validate_typed_relation_registry(registry: Mapping[str, Mapping[str, Any]] | None = None) -> None:
    """Reject incomplete registry entries; relations are data, not patterns."""
    seen: dict[str, str] = {}
    for predicate, spec in (TYPED_CLAIM_RELATIONS if registry is None else registry).items():
        edge = spec.get("graph_edge")
        if (set(spec) != _REGISTRY_FIELDS
                or not isinstance(predicate, str) or not predicate.isidentifier()
                or not all(isinstance(spec[key], str) and spec[key]
                           for key in ("subject_type", "object_type", "direction", "fact_relation", "clause"))
                or not isinstance(spec["single_valued"], bool) or not isinstance(spec["asymmetric"], bool)
                or not (edge is None or (isinstance(edge, tuple) and len(edge) == 2
                                         and edge[1] in {"source", "target"}))):
            raise ValueError(f"Typed relation {predicate!r} is incomplete.")
        if seen.setdefault(spec["fact_relation"], predicate) != predicate:
            raise ValueError(f"Typed relation {predicate!r} reuses another fact relation.")


validate_typed_relation_registry()


def normalize_entity(value: str) -> str:
    """Identity comparison only: case and whitespace, no language parsing."""
    return " ".join(str(value).casefold().split())


_normalize_entity = normalize_entity


def _contains_entity(text: object, entity: str) -> bool:
    return isinstance(text, str) and normalize_entity(entity) in normalize_entity(text)


# --- 1-3. EvidenceSpan: real read, provenance and isolation ---------------------------------------

def evidence_identity_valid(value: Mapping[str, Any], expected_workspace_id: str) -> bool:
    """Recompute a passage evidence ID from its own immutable fields (anti-forgery)."""
    path, quote, quote_hash = value.get("relative_path"), value.get("quote"), value.get("quote_sha256")
    start, end = value.get("line_start"), value.get("line_end")
    if (not isinstance(path, str) or not path or not isinstance(quote, str) or not quote
            or value.get("workspace_id", expected_workspace_id) != expected_workspace_id
            or not isinstance(value.get("content_sha256"), str)
            or type(start) is not int or type(end) is not int or not 1 <= start <= end
            or hashlib.sha256(quote.encode("utf-8")).hexdigest() != quote_hash):
        return False
    return value.get("evidence_id") == passage_evidence_id(
        workspace_id=expected_workspace_id, resource_id=path, document_id=path,
        logical_path=f"/{path}", source_ref=path, content_hash=value["content_sha256"],
        line_start=start, line_end=end, quote_hash=quote_hash,
    )


def evidence_span_error(
    evidence_id: str,
    quote: str | None,
    evidence_by_ref: Mapping[str, Mapping[str, Any]],
    *,
    workspace_id: str,
    read_evidence_ids: Collection[str] | None,
    confirmed_facts: Any = None,
) -> str | None:
    """Return a reason code when a cited span fails a Host check, else None."""
    if read_evidence_ids is not None and evidence_id not in read_evidence_ids:
        return "EVIDENCE_NOT_READ"
    evidence = evidence_by_ref.get(evidence_id)
    if not isinstance(evidence, Mapping) or evidence.get("evidence_id") != evidence_id:
        return "EVIDENCE_UNKNOWN"
    if evidence.get("status") != "verified":
        return "EVIDENCE_UNVERIFIED"
    if evidence.get("workspace_id") != workspace_id:
        return "WORKSPACE_MISMATCH"
    source_quote = evidence.get("quote")
    if not isinstance(source_quote, str) or not source_quote.strip():
        return "EVIDENCE_UNVERIFIED"
    if quote is not None and (not quote.strip() or quote not in source_quote):
        return "QUOTE_NOT_IN_EVIDENCE"
    if "quote_sha256" in evidence and (
            hashlib.sha256(source_quote.encode("utf-8")).hexdigest() != evidence["quote_sha256"]):
        return "EVIDENCE_TAMPERED"
    if {"relative_path", "content_sha256", "line_start", "line_end", "quote_sha256"} <= set(evidence) and (
            not evidence_identity_valid(evidence, workspace_id)):
        return "EVIDENCE_TAMPERED"
    if confirmed_facts is not None and not confirmed_facts.source_current(evidence):
        return "SOURCE_VERSION_STALE"
    return None


# Transcript line header written by the importer ("[2031-06-04T09:00:00Z] Speaker: ...").
# This is the stored message format, not language: it records when a message was sent.
_MESSAGE_HEADER = re.compile(r"^\[(\d{4}-\d{2}-\d{2})[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?\]", re.M)


def message_time_only(valid_on: str, source_quote: str) -> bool:
    """True when ``valid_on`` occurs in the read only as a message send time (#94)."""
    if valid_on not in {match.group(1) for match in _MESSAGE_HEADER.finditer(source_quote)}:
        return False
    return valid_on not in _MESSAGE_HEADER.sub("", source_quote)


def blocked_entity(claim: Any, blocked_entities: Iterable[str]) -> str | None:
    """Return the first claim entity the Host never lets this run use."""
    names = {normalize_entity(name) for name in blocked_entities if str(name).strip()}
    for value in (claim.subject, claim.object_value):
        if normalize_entity(value) in names:
            return value
    return None


# --- 5. Conflicts ----------------------------------------------------------------------------------

def _claim_fact(claim: Any) -> _Fact:
    spec = TYPED_CLAIM_RELATIONS[claim.predicate]
    return _Fact(spec["fact_relation"], normalize_entity(claim.subject), normalize_entity(claim.object_value),
                 claim.valid_on, claim.temporal_kind, origin="claim")


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _interval_relation(fact: _Fact, day: str) -> str:
    """inside/overlap/outside for one ISO day against a confirmed interval."""
    from linkloom.temporal_graph.models import interval

    try:
        left, right = interval(day)
        start = interval(fact.valid_on)[0]
        end = interval(fact.valid_to)[0] if fact.valid_to else None
    except (TypeError, ValueError):
        return "overlap"  # Unparseable time never supports and never hides a conflict.
    if start >= right or (end is not None and end <= left):
        return "outside"
    if start <= left and (end is None or right <= end):
        return "inside"
    return "overlap"


def _same_time(left: _Fact, right: _Fact) -> bool:
    if left.temporal_kind == "interval" or right.temporal_kind == "interval":
        if left.temporal_kind == right.temporal_kind == "interval":
            return True if left.valid_on is None or right.valid_on is None else (
                _interval_relation(left, right.valid_on) != "outside"
                or _interval_relation(right, left.valid_on) != "outside")
        ranged, other = (left, right) if left.temporal_kind == "interval" else (right, left)
        return _interval_relation(ranged, other.valid_on or _today()) != "outside"
    if left.valid_on is None or right.valid_on is None:
        return left.valid_on is None and right.valid_on is None
    return left.valid_on == right.valid_on


def _relation_spec(relation: str) -> Mapping[str, Any] | None:
    return next((spec for spec in TYPED_CLAIM_RELATIONS.values() if spec["fact_relation"] == relation), None)


def facts_conflict(claim: _Fact, fact: _Fact) -> bool:
    """Deterministic conflict between a claimed fact and a structured fact.

    Negation (a confirmed blocker), a different object for a single-valued
    relation, and a reversed asymmetric relation conflict when their valid
    times overlap. Different times never conflict: a later change is history.
    """
    base = fact.relation.removeprefix("not_")
    spec = _relation_spec(base)
    if spec is None or base != claim.relation or not _same_time(claim, fact):
        return False
    if fact.relation.startswith("not_"):
        return fact.subject == claim.subject and fact.value == claim.value
    if spec["single_valued"] and fact.subject == claim.subject and fact.value != claim.value:
        return True
    return bool(spec["asymmetric"] and fact.subject == claim.value and fact.value == claim.subject
                and fact.subject != fact.value)


def confirmed_conflicts(
    claim: Any, evidence_values: Iterable[Mapping[str, Any]], confirmed_facts: Any,
) -> tuple[str, ...]:
    """Evidence IDs whose Host-confirmed facts contradict this claim."""
    if confirmed_facts is None:
        return ()
    claimed = _claim_fact(claim)
    refs = []
    for evidence in evidence_values:
        if any(facts_conflict(claimed, fact) for fact in confirmed_facts.facts_for(evidence)):
            refs.append(str(evidence.get("evidence_id")))
    return tuple(dict.fromkeys(refs))


def claim_conflicts(claims: Sequence[Any]) -> tuple[tuple[Any, Any], ...]:
    """Pairs of proposed claims that cannot both hold (same deterministic rules)."""
    pairs = []
    for index, left in enumerate(claims):
        for right in claims[index + 1:]:
            if left.predicate == right.predicate and facts_conflict(_claim_fact(left), _claim_fact(right)):
                pairs.append((left, right))
    return tuple(pairs)


def claim_status(
    claim: Any,
    evidence_by_ref: Mapping[str, Mapping[str, Any]],
    *,
    workspace_id: str,
    read_evidence_ids: Collection[str] | None,
    observed_evidence_by_ref: Mapping[str, Mapping[str, Any]] | None = None,
    confirmed_facts: Any = None,
    blocked_entities: Iterable[str] = (),
) -> tuple[ClaimSupportStatus, str | None]:
    """Run the five Host checks for one typed claim; return a status and reason code."""
    if claim.predicate not in TYPED_CLAIM_RELATIONS:
        return ClaimSupportStatus.UNVERIFIABLE, "PREDICATE_UNSUPPORTED"
    if blocked_entity(claim, blocked_entities) is not None:
        return ClaimSupportStatus.UNVERIFIABLE, "BLOCKED_ENTITY"
    cited = []
    for evidence_id in claim.evidence_ids:
        reason = evidence_span_error(evidence_id, claim.quote, evidence_by_ref, workspace_id=workspace_id,
                                     read_evidence_ids=read_evidence_ids, confirmed_facts=confirmed_facts)
        if reason is not None:
            return ClaimSupportStatus.UNVERIFIABLE, reason
        cited.append(evidence_by_ref[evidence_id])
    # The asserted object is new information: it must be copied from the read text.
    if not _contains_entity(claim.quote, claim.object_value):
        return ClaimSupportStatus.INSUFFICIENT, "OBJECT_NOT_IN_QUOTE"
    if claim.valid_on and any(message_time_only(claim.valid_on, value["quote"]) for value in cited):
        return ClaimSupportStatus.INSUFFICIENT, "MESSAGE_TIME_AS_VALID_ON"
    observed = [value for ref, value in (observed_evidence_by_ref or {}).items()
                if isinstance(value, Mapping) and value.get("evidence_id") == ref]
    if confirmed_conflicts(claim, [*cited, *observed], confirmed_facts):
        return ClaimSupportStatus.CONTRADICTED, "CONFIRMED_FACT_CONFLICT"
    return ClaimSupportStatus.SUPPORTED, None


def unchained_claims(claims: Sequence[Any], question: str) -> tuple[Any, ...]:
    """Claims whose subject is neither named by the user nor the object of another claim.

    A multi-hop chain must connect: a dependent hop starts from the verified
    object of its upstream claim, so a wrong upstream entity cannot be reused.
    """
    objects = {normalize_entity(claim.object_value) for claim in claims}
    return tuple(claim for claim in claims
                 if not _contains_entity(question, claim.subject)
                 and normalize_entity(claim.subject) not in objects)


__all__ = [
    "ClaimSupportStatus",
    "TYPED_CLAIM_RELATIONS",
    "blocked_entity",
    "claim_conflicts",
    "claim_status",
    "confirmed_conflicts",
    "evidence_identity_valid",
    "evidence_span_error",
    "facts_conflict",
    "normalize_entity",
    "unchained_claims",
    "validate_typed_relation_registry",
]
