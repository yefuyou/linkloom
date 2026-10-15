"""Deterministic support checks for a deliberately small TeamDecision vocabulary.

This is not a general natural-language entailment judge. It recognizes a few
explicit relation forms used by LinkLoom's bounded multi-hop decision slice;
unrecognized material claims are returned as UNVERIFIABLE and must fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import re
from collections.abc import Mapping, Sequence
from typing import Any

from linkloom.semantic_ingestion.timestamped_text import timestamped_message_body


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT = "INSUFFICIENT"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class AtomicClaimAssessment:
    text: str
    relation: str
    subject: str
    value: str
    valid_on: str | None
    status: ClaimSupportStatus
    supporting_evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    status: ClaimSupportStatus
    claims: tuple[AtomicClaimAssessment, ...]


@dataclass(frozen=True, slots=True)
class _Fact:
    relation: str
    subject: str
    value: str
    valid_on: str | None = None
    temporal_kind: str | None = None
    project_scope: str | None = None


# ClaimProposal roles share the same direction as the normalized source facts.
# Keep the historical predicate names; changing their meaning would break v1.
TYPED_CLAIM_RELATIONS = {
    "alias_of_project": {
        "subject_type": "alias",
        "object_type": "project",
        "direction": "The subject name is an alias for the object project; this says nothing about ownership.",
        "source_relations": frozenset({"alias_of"}),
    },
    "owner_of_project": {
        "subject_type": "project",
        "object_type": "person",
        "direction": "The subject project has the object person as its owner.",
        "source_relations": frozenset({"owner_of"}),
    },
    "reports_to": {
        "subject_type": "person",
        "object_type": "person",
        "direction": "The subject person reports to the object person.",
        "source_relations": frozenset({"reports_to", "direct_leader_of"}),
    },
}


_LATIN_ENTITY_TOKEN = r"[A-Z][A-Za-z0-9]*(?:[.'’&/-][A-Za-z0-9]+)*"
_CJK_ENTITY_TOKEN = r"[\u3400-\u4dbf\u4e00-\u9fff]+(?:[A-Za-z0-9][A-Za-z0-9.'’&/-]*)?"
_ENTITY_TOKEN = rf"(?:{_LATIN_ENTITY_TOKEN}|{_CJK_ENTITY_TOKEN})"
_ENTITY = rf"{_ENTITY_TOKEN}(?:\s+{_ENTITY_TOKEN}){{0,5}}"
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_INVALID_DATE = "<invalid-date>"
_PREFIX_DATE = rf"(?:(?P<prefix_kind>On|As of) (?P<prefix_date>{_DATE}) )?"
_ZH_ENTITY = r"[\u3400-\u9fffA-Za-z0-9][\u3400-\u9fffA-Za-z0-9 .’'&/-]{0,80}?"
_ZH_PREFIX_DATE = rf"(?:(?P<prefix_kind>截至)\s*(?P<prefix_date>{_DATE})\s*[，,]?\s*)?"

_SUPERSESSION_ACTIVE = re.compile(
    rf"^{_PREFIX_DATE}(?P<new>{_ENTITY}) (?:replaced|superseded) "
    rf"(?P<old>{_ENTITY})(?: on (?P<suffix_date>{_DATE}))?"
    rf"(?: as the current .*)?\.?$"
)
_SUPERSESSION_PASSIVE = re.compile(
    rf"^{_PREFIX_DATE}(?P<old>{_ENTITY}) (?:was|is|has been) "
    rf"(?:replaced|superseded) by (?P<new>{_ENTITY})"
    rf"(?: on (?P<suffix_date>{_DATE}))?(?: as the current .*)?\.?$"
)
_SUPERSESSION_NEGATION_ACTIVE = re.compile(
    rf"^(?P<new>{_ENTITY}) (?:did not|does not|has not|was not) "
    rf"(?:replace|supersede) (?P<old>{_ENTITY})\.?$"
)
_SUPERSESSION_NEGATION_PASSIVE = re.compile(
    rf"^(?P<old>{_ENTITY}) (?:was not|is not|has not been) "
    rf"(?:replaced|superseded) by (?P<new>{_ENTITY})\.?$"
)
_ZH_SUPERSESSION_ACTIVE = re.compile(
    rf"^{_ZH_PREFIX_DATE}(?P<new>{_ZH_ENTITY})\s*"
    rf"(?:替代|取代|接替)(?:了)?\s*(?P<old>{_ZH_ENTITY})"
    rf"(?:\s*于\s*(?P<suffix_date>{_DATE}))?$"
)
_ZH_SUPERSESSION_PASSIVE = re.compile(
    rf"^{_ZH_PREFIX_DATE}(?P<old>{_ZH_ENTITY})\s*被\s*"
    rf"(?P<new>{_ZH_ENTITY})\s*(?:替代|取代|接替)(?:了)?"
    rf"(?:\s*于\s*(?P<suffix_date>{_DATE}))?$"
)
_ZH_SUPERSESSION_NEGATION_ACTIVE = re.compile(
    rf"^(?P<new>{_ZH_ENTITY})\s*(?:没有|未|并未|并没有)\s*"
    rf"(?:替代|取代|接替)(?:了)?\s*(?P<old>{_ZH_ENTITY})$"
)
_ZH_SUPERSESSION_NEGATION_PASSIVE = re.compile(
    rf"^(?P<old>{_ZH_ENTITY})\s*没有被\s*(?P<new>{_ZH_ENTITY})\s*"
    rf"(?:替代|取代|接替)(?:过)?$"
)
_OWNER_ASSERTION = re.compile(
    rf"^{_PREFIX_DATE}(?P<owner>{_ENTITY}) is (?:the )?(?:accountable )?"
    rf"owner of (?P<plan>{_ENTITY})(?: for (?P<project>{_ENTITY}))?\.?$"
)
_OWNER_ACTIVE = re.compile(rf"^(?P<owner>{_ENTITY}) owns (?P<plan>{_ENTITY})\.?$")
_OWNER_NEGATION = re.compile(
    rf"^(?P<owner>{_ENTITY}) is not (?:the )?(?:accountable )?owner of "
    rf"(?P<plan>{_ENTITY})\.?$"
)
_OWNER_FRAGMENT = re.compile(
    rf"^(?:the )?owner(?: is)? (?P<owner>{_ENTITY})\.?$"
)
_PROPOSED_OWNER_SOURCE = re.compile(
    rf"^A (?P<date>{_DATE}) draft listed (?P<owner>{_ENTITY}) "
    rf"as the proposed (?P<plan>{_ENTITY}) owner$"
)
_ZH_OWNER_ASSERTION = re.compile(
    rf"^{_ZH_PREFIX_DATE}(?P<owner>{_ZH_ENTITY})\s*是\s*"
    rf"(?P<plan>{_ZH_ENTITY})\s*(?:的)?(?:项目)?(?:负责人|所有者)$"
)
_ZH_OWNER_REVERSE = re.compile(
    rf"^{_ZH_PREFIX_DATE}(?P<plan>{_ZH_ENTITY})\s*(?:的)?(?:项目)?"
    rf"(?:负责人|所有者)\s*是\s*(?P<owner>{_ZH_ENTITY})$"
)
_ZH_OWNER_NEGATION = re.compile(
    rf"^(?P<owner>{_ZH_ENTITY})\s*(?:不是|并非)\s*"
    rf"(?P<plan>{_ZH_ENTITY})\s*(?:的)?(?:项目)?(?:负责人|所有者)$"
)
_LEADER_ASSERTION = re.compile(
    rf"^{_PREFIX_DATE}(?P<person>{_ENTITY}) reports directly to "
    rf"(?P<leader>{_ENTITY})(?: in the? (?P<project>{_ENTITY}) project"
    rf"| in the? .*)?\.?$"
)
_LEADER_REVERSE = re.compile(
    rf"^{_PREFIX_DATE}(?P<leader>{_ENTITY}) is (?P<person>{_ENTITY})'s "
    rf"direct leader(?: for .*)?\.?$"
)
_LEADER_FRAGMENT = re.compile(
    rf"^(?:the )?direct leader(?: is)? (?P<leader>{_ENTITY})\.?$"
)
_ZH_LEADER_ASSERTION = re.compile(
    rf"^{_ZH_PREFIX_DATE}(?P<person>{_ZH_ENTITY})\s*的?"
    rf"(?:直属领导|直接领导|上级|上司|主管|直属汇报|直接汇报)\s*是\s*(?P<leader>{_ZH_ENTITY})$"
)
_ZH_LEADER_REVERSE = re.compile(
    rf"^{_ZH_PREFIX_DATE}(?P<leader>{_ZH_ENTITY})\s*是\s*"
    rf"(?P<person>{_ZH_ENTITY})\s*的?(?:直属领导|直接领导|上级|上司|主管|直属汇报|直接汇报)$"
)
_ZH_REPORTS_TO_CLAIM = re.compile(
    rf"^(?P<person>{_ZH_ENTITY})\s*报告(?:直接)?给\s*(?P<leader>{_ZH_ENTITY})$"
)
_ZH_DIRECT_REPORTS_TO_CLAIM = re.compile(
    rf"^(?P<person>{_ZH_ENTITY})\s*直接向\s*(?P<leader>{_ZH_ENTITY})\s*汇报$"
)
_ZH_REPORTS_DIRECT_TO_CLAIM = re.compile(
    rf"^(?P<person>{_ZH_ENTITY})\s*向\s*(?P<leader>{_ZH_ENTITY})\s*直接汇报$"
)
_ALIAS_ASSERTION = re.compile(
    rf"^(?P<alias>{_ENTITY}) is (?:an? |the )?(?:internal |team )?alias for "
    rf"(?:the )?(?P<target>{_ENTITY_TOKEN}(?:\s+{_ENTITY_TOKEN}){{0,5}}?)"
    rf"(?: project| rollout plan)?\.?$"
)
_ALIAS_ACTIVE = re.compile(
    rf"^(?P<alias>{_ENTITY}) aliases (?P<target>{_ENTITY})"
    rf"(?: rollout plan)?\.?$"
)
_APPROVAL_ASSERTION = re.compile(
    rf"^{_PREFIX_DATE}(?i:the) (?P<project>{_ENTITY}) project approved "
    rf"(?P<plan>{_ENTITY}) for its .+\.?$"
)
_APPROVAL_PASSIVE = re.compile(
    rf"^{_PREFIX_DATE}(?P<plan>{_ENTITY}) was approved for "
    rf"(?P<project>{_ENTITY})(?: pilot rollout)?"
    rf"(?: on (?P<suffix_date>{_DATE}))?\.?$"
)
_SELECTED_SUPPLIER = re.compile(
    rf"^We selected (?P<supplier>{_ENTITY}?) as the supplier for the "
    rf"(?P<project>{_ENTITY}?) project\.?$"
)
_SELECTED_MODEL_PROVIDER = re.compile(
    rf"^The (?P<project>{_ENTITY}?) team selected (?P<provider>{_ENTITY}?) "
    rf"as the approved model provider(?: for the(?: .+)?)?\.?$"
)
_APPROVED_MODEL_PROVIDER_CLAIM = re.compile(
    rf"^(?P<provider>{_ENTITY}?) is the approved model provider for the "
    rf"(?P<project>{_ENTITY}?) pilot$"
)
_APPROVED_MODEL_PROVIDER_TEAM_CLAIM = re.compile(
    rf"^(?P<provider>{_ENTITY}?) is the approved model provider for the "
    rf"(?P<project>{_ENTITY}?) team$"
)
_TEAM_APPROVED_PROVIDER = re.compile(
    rf"^The team approved (?P<provider>{_ENTITY}?) as the model provider "
    rf"for the pilot\.?$"
)
_TEAM_APPROVED_PROVIDER_CLAIM = re.compile(
    rf"^(?P<provider>{_ENTITY}?) is the approved model provider$"
)
_NAMED_ENTITY_CLAIM = re.compile(rf"^The observed note names (?P<entity>{_ENTITY})$")
_APPROVAL_RATIONALE_CLAIM = re.compile(
    r"^The observed decision record establishes the approval$"
)
_SELECTED_SUPPLIER_QUESTION = re.compile(
    rf"(?i:(?:which|what)) supplier (?i:(?:was|is)) selected for the "
    rf"(?P<project>{_ENTITY}) project"
)
_CJK_RELATION_MARKER = re.compile(
    r"(?:是|替代|取代|接替|负责人|所有者|直属领导|直接领导|上级|汇报|不是|并非)"
)


def _normalize_entity(value: str) -> str:
    return " ".join(value.casefold().split())


def _valid_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return _INVALID_DATE
    return value


def _temporal_kind(match: re.Match[str], *, suffix_date: str | None = None) -> str | None:
    if suffix_date is not None:
        return "event"
    prefix_kind = match.groupdict().get("prefix_kind")
    if prefix_kind in {"As of", "截至"}:
        return "snapshot"
    if prefix_kind == "On":
        return "event"
    return None


def _snapshot_kind(match: re.Match[str]) -> str | None:
    return (
        "snapshot"
        if match.groupdict().get("prefix_date") is not None
        else None
    )


def _fact_from_clause(
    text: str,
    *,
    current_plan: str | None = None,
    current_owner: str | None = None,
) -> _Fact | None:
    statement = text.strip().rstrip(".!?。？！").strip()
    if not statement:
        return None

    match = _SUPERSESSION_NEGATION_ACTIVE.fullmatch(statement)
    if match:
        return _Fact(
            "not_superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
        )
    match = _SUPERSESSION_NEGATION_PASSIVE.fullmatch(statement)
    if match:
        return _Fact(
            "not_superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
        )
    match = _ZH_SUPERSESSION_NEGATION_ACTIVE.fullmatch(statement)
    if match:
        return _Fact(
            "not_superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
        )
    match = _ZH_SUPERSESSION_NEGATION_PASSIVE.fullmatch(statement)
    if match:
        return _Fact(
            "not_superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
        )
    match = _OWNER_NEGATION.fullmatch(statement)
    if match:
        return _Fact(
            "not_owner_of",
            _normalize_entity(match.group("plan")),
            _normalize_entity(match.group("owner")),
        )
    match = _ZH_OWNER_NEGATION.fullmatch(statement)
    if match:
        return _Fact(
            "not_owner_of",
            _normalize_entity(match.group("plan")),
            _normalize_entity(match.group("owner")),
        )

    match = _PROPOSED_OWNER_SOURCE.fullmatch(statement)
    if match:
        return _Fact(
            "proposed_owner_of",
            _normalize_entity(match.group("plan")),
            _normalize_entity(match.group("owner")),
            _valid_date(match.group("date")),
            "event",
        )

    match = _SUPERSESSION_ACTIVE.fullmatch(statement)
    if match:
        return _Fact(
            "superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
            _valid_date(match.group("suffix_date") or match.group("prefix_date")),
            _temporal_kind(match, suffix_date=match.group("suffix_date")),
        )
    match = _SUPERSESSION_PASSIVE.fullmatch(statement)
    if match:
        return _Fact(
            "superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
            _valid_date(match.group("suffix_date") or match.group("prefix_date")),
            _temporal_kind(match, suffix_date=match.group("suffix_date")),
        )
    match = _ZH_SUPERSESSION_ACTIVE.fullmatch(statement)
    if match:
        return _Fact(
            "superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
            _valid_date(match.group("suffix_date") or match.group("prefix_date")),
            _temporal_kind(match, suffix_date=match.group("suffix_date")),
        )
    match = _ZH_SUPERSESSION_PASSIVE.fullmatch(statement)
    if match:
        return _Fact(
            "superseded_by",
            _normalize_entity(match.group("old")),
            _normalize_entity(match.group("new")),
            _valid_date(match.group("suffix_date") or match.group("prefix_date")),
            _temporal_kind(match, suffix_date=match.group("suffix_date")),
        )

    match = _OWNER_ASSERTION.fullmatch(statement)
    if match:
        return _Fact(
            "owner_of",
            _normalize_entity(match.group("plan")),
            _normalize_entity(match.group("owner")),
            _valid_date(match.group("prefix_date")),
            _snapshot_kind(match),
            _normalize_entity(match.group("project")) if match.group("project") else None,
        )
    match = _ZH_OWNER_ASSERTION.fullmatch(statement) or _ZH_OWNER_REVERSE.fullmatch(statement)
    if match:
        return _Fact(
            "owner_of",
            _normalize_entity(match.group("plan")),
            _normalize_entity(match.group("owner")),
            _valid_date(match.group("prefix_date")),
            _snapshot_kind(match),
        )
    match = _OWNER_ACTIVE.fullmatch(statement)
    if match:
        return _Fact(
            "owner_of",
            _normalize_entity(match.group("plan")),
            _normalize_entity(match.group("owner")),
        )
    match = _OWNER_FRAGMENT.fullmatch(statement)
    if match and current_plan is not None:
        return _Fact(
            "owner_of", current_plan, _normalize_entity(match.group("owner"))
        )

    match = _LEADER_ASSERTION.fullmatch(statement)
    if match:
        return _Fact(
            "direct_leader_of",
            _normalize_entity(match.group("person")),
            _normalize_entity(match.group("leader")),
            _valid_date(match.group("prefix_date")),
            _snapshot_kind(match),
            _normalize_entity(match.group("project")) if match.group("project") else None,
        )
    match = _LEADER_REVERSE.fullmatch(statement)
    if match:
        return _Fact(
            "direct_leader_of",
            _normalize_entity(match.group("person")),
            _normalize_entity(match.group("leader")),
            _valid_date(match.group("prefix_date")),
            _snapshot_kind(match),
        )
    match = _ZH_LEADER_ASSERTION.fullmatch(statement) or _ZH_LEADER_REVERSE.fullmatch(statement)
    if match:
        return _Fact(
            "direct_leader_of",
            _normalize_entity(match.group("person")),
            _normalize_entity(match.group("leader")),
            _valid_date(match.group("prefix_date")),
            _snapshot_kind(match),
        )
    match = _LEADER_FRAGMENT.fullmatch(statement)
    if match and current_owner is not None:
        return _Fact(
            "direct_leader_of",
            current_owner,
            _normalize_entity(match.group("leader")),
        )

    match = _ALIAS_ASSERTION.fullmatch(statement) or _ALIAS_ACTIVE.fullmatch(statement)
    if match:
        return _Fact(
            "alias_of",
            _normalize_entity(match.group("alias")),
            _normalize_entity(match.group("target")),
        )

    match = _APPROVAL_ASSERTION.fullmatch(statement)
    if match:
        return _Fact(
            "approved_for",
            _normalize_entity(match.group("project")),
            _normalize_entity(match.group("plan")),
            _valid_date(match.group("prefix_date")),
            _temporal_kind(match),
        )
    match = _APPROVAL_PASSIVE.fullmatch(statement)
    if match:
        return _Fact(
            "approved_for",
            _normalize_entity(match.group("project")),
            _normalize_entity(match.group("plan")),
            _valid_date(match.group("suffix_date") or match.group("prefix_date")),
            _temporal_kind(match, suffix_date=match.group("suffix_date")),
        )
    match = _SELECTED_SUPPLIER.fullmatch(statement)
    if match:
        return _Fact(
            "selected_supplier_for",
            f"{_normalize_entity(match.group('project'))} project",
            _normalize_entity(match.group("supplier")),
        )
    match = _SELECTED_MODEL_PROVIDER.fullmatch(statement)
    if match:
        return _Fact(
            "approved_model_provider_for",
            _normalize_entity(match.group("project")),
            _normalize_entity(match.group("provider")),
        )
    match = _TEAM_APPROVED_PROVIDER.fullmatch(statement)
    if match:
        return _Fact(
            "approved_model_provider_for",
            "team",
            _normalize_entity(match.group("provider")),
        )
    match = _APPROVED_MODEL_PROVIDER_CLAIM.fullmatch(statement)
    if match:
        return _Fact(
            "approved_model_provider_for",
            _normalize_entity(match.group("project")),
            _normalize_entity(match.group("provider")),
        )
    match = _APPROVED_MODEL_PROVIDER_TEAM_CLAIM.fullmatch(statement)
    if match:
        return _Fact(
            "approved_model_provider_for",
            _normalize_entity(match.group("project")),
            _normalize_entity(match.group("provider")),
        )
    match = _TEAM_APPROVED_PROVIDER_CLAIM.fullmatch(statement)
    if match:
        return _Fact(
            "approved_model_provider_for",
            "team",
            _normalize_entity(match.group("provider")),
        )
    match = _NAMED_ENTITY_CLAIM.fullmatch(statement)
    if match:
        return _Fact("named_entity", "note", _normalize_entity(match.group("entity")))
    if _APPROVAL_RATIONALE_CLAIM.fullmatch(statement):
        return _Fact("approval_record", "decision record", "approval established")
    return None


def _claim_facts(
    text: str, *, question: str | None = None
) -> tuple[_Fact, ...] | None:
    clauses = [
        item.strip()
        for item in re.split(r";|；|\n|。|！|？|(?<=[.!?])\s+", text)
        if item.strip()
    ]
    if not clauses:
        return None
    if (
        len(clauses) == 1
        and re.fullmatch(_ENTITY, clauses[0])
        and _CJK_RELATION_MARKER.search(clauses[0]) is None
    ):
        if question is None:
            return None
        match = _SELECTED_SUPPLIER_QUESTION.search(question)
        if match is None:
            return None
        return (
            _Fact(
                "selected_supplier_for",
                f"{_normalize_entity(match.group('project'))} project",
                _normalize_entity(clauses[0]),
            ),
        )
    parsed: list[_Fact] = []
    current_plan: str | None = None
    current_owner: str | None = None
    for clause in clauses:
        date_qualifier = re.fullmatch(
            rf"(?:(?i:as of)\s*|截至\s*)({_DATE})\s*", clause
        )
        if date_qualifier is not None:
            if not parsed:
                return None
            valid_on = _valid_date(date_qualifier.group(1))
            if valid_on == _INVALID_DATE:
                return None
            prior = parsed[-1]
            if prior.valid_on is not None and prior.valid_on != valid_on:
                return None
            parsed[-1] = _Fact(
                prior.relation, prior.subject, prior.value, valid_on, "snapshot",
                prior.project_scope,
            )
            continue
        fact = _fact_from_clause(
            clause, current_plan=current_plan, current_owner=current_owner
        )
        if fact is None:
            # A direct reporting line supports this weaker Chinese paraphrase.
            # Parse it for final claims only: a source that merely says
            # "reports to" must not establish a *direct* leader by itself.
            # Keep the final-only reporting paraphrase on the same terminal
            # punctuation boundary as _fact_from_clause. The cited source must
            # still match the exact person and leader relation below.
            normalized_clause = clause.strip().rstrip(".!?。？！").strip()
            report = (
                _ZH_REPORTS_TO_CLAIM.fullmatch(normalized_clause)
                or _ZH_DIRECT_REPORTS_TO_CLAIM.fullmatch(normalized_clause)
                or _ZH_REPORTS_DIRECT_TO_CLAIM.fullmatch(normalized_clause)
            )
            if report:
                fact = _Fact(
                    "reports_to", _normalize_entity(report.group("person")),
                    _normalize_entity(report.group("leader")),
                )
        if fact is None or fact.valid_on == _INVALID_DATE:
            return None
        parsed.append(fact)
        if fact.relation == "superseded_by":
            current_plan = fact.value
            current_owner = None
        elif fact.relation == "alias_of":
            current_plan = fact.value
        elif fact.relation == "owner_of":
            current_plan = fact.subject
            current_owner = fact.value
    return tuple(parsed)


def _source_facts(evidence: Mapping[str, Any]) -> tuple[_Fact, ...]:
    quote = evidence.get("quote")
    if (
        evidence.get("status") != "verified"
        or not isinstance(evidence.get("evidence_id"), str)
        or not isinstance(quote, str)
        or not quote.strip()
    ):
        return ()
    facts: list[_Fact] = []
    for clause in re.split(r"(?<=[.!?。？！])\s*|\n", quote):
        fact = _fact_from_clause(timestamped_message_body(clause) or clause)
        if fact is not None and fact.valid_on != _INVALID_DATE:
            facts.append(fact)
            if fact.relation in {"selected_supplier_for", "approved_model_provider_for"}:
                facts.append(_Fact("named_entity", "note", fact.value))
            if fact.relation == "approved_model_provider_for":
                facts.append(
                    _Fact("approval_record", "decision record", "approval established")
                )
    return tuple(facts)


def evidence_supports_typed_claim(
    evidence: Mapping[str, Any],
    *,
    predicate: str,
    subject: str,
    object_value: str,
    valid_on: str | None,
    temporal_kind: str | None,
) -> bool:
    """Compare a typed proposal with an existing normalized fact in one quote.

    This intentionally adds no answer-language parser. The caller supplies an
    exact source quote copied from verified evidence; this function maps the
    two supported proposal predicates onto the existing source-fact vocabulary
    and requires exact entity and temporal-scope equality.
    """
    definition = TYPED_CLAIM_RELATIONS.get(predicate)
    relations = definition["source_relations"] if definition is not None else None
    if (
        relations is None
        or not isinstance(subject, str) or not subject.strip()
        or not isinstance(object_value, str) or not object_value.strip()
        or valid_on == _INVALID_DATE
        or temporal_kind not in {None, "snapshot", "event"}
        or (valid_on is None) != (temporal_kind is None)
    ):
        return False
    normalized_subject = _normalize_entity(subject)
    normalized_object = _normalize_entity(object_value)
    return any(
        fact.relation in relations
        and fact.subject == normalized_subject
        and fact.value == normalized_object
        and fact.valid_on == valid_on
        and fact.temporal_kind == temporal_kind
        for fact in _source_facts(evidence)
    )


def _fact_status(claim: _Fact, evidence_facts: Sequence[_Fact]) -> ClaimSupportStatus:
    same_relation_and_subject = [
        fact
        for fact in evidence_facts
        if fact.relation == claim.relation
        and fact.subject == claim.subject
        and (
            _same_temporal_scope(fact, claim)
            or (claim.valid_on is None and fact.temporal_kind == "event")
        )
    ]
    if any(fact.value != claim.value for fact in same_relation_and_subject):
        return ClaimSupportStatus.CONTRADICTED
    if any(
        fact.relation == f"not_{claim.relation}"
        and fact.subject == claim.subject
        and fact.value == claim.value
        for fact in evidence_facts
    ):
        return ClaimSupportStatus.CONTRADICTED
    if claim.temporal_kind == "event" and claim.valid_on is not None and any(
        fact.relation == claim.relation
        and fact.subject == claim.subject
        and fact.value == claim.value
        and fact.temporal_kind == "event"
        and fact.valid_on is not None
        and fact.valid_on != claim.valid_on
        for fact in evidence_facts
    ):
        return ClaimSupportStatus.CONTRADICTED
    if any(_fact_supports(fact, claim) for fact in evidence_facts):
        return ClaimSupportStatus.SUPPORTED
    return ClaimSupportStatus.INSUFFICIENT


def _fact_supports(evidence: _Fact, claim: _Fact) -> bool:
    return (
        (evidence.relation == claim.relation
         or evidence.relation == "direct_leader_of" and claim.relation == "reports_to")
        and evidence.subject == claim.subject
        and evidence.value == claim.value
        and (claim.project_scope is None
             or evidence.project_scope in {None, claim.project_scope})
        and (
            evidence.valid_on == claim.valid_on
            and evidence.temporal_kind == claim.temporal_kind
            if claim.valid_on is not None
            else evidence.valid_on is None or evidence.temporal_kind == "event"
        )
    )


def _same_temporal_scope(left: _Fact, right: _Fact) -> bool:
    if left.valid_on is None or right.valid_on is None:
        return left.valid_on is None and right.valid_on is None
    return left.valid_on == right.valid_on and left.temporal_kind == right.temporal_kind


def assess_claim_text(
    text: object,
    evidence_refs: Sequence[str],
    evidence_by_ref: Mapping[str, Mapping[str, Any]],
    *,
    question: str | None = None,
) -> ClaimAssessment:
    """Assess every clause using only exact verified passages cited by the claim."""
    if not isinstance(text, str) or not text.strip():
        return ClaimAssessment(ClaimSupportStatus.UNVERIFIABLE, ())
    parsed = _claim_facts(text, question=question)
    if parsed is None:
        return ClaimAssessment(ClaimSupportStatus.UNVERIFIABLE, ())

    source_facts: list[_Fact] = []
    evidence_refs_by_fact: dict[_Fact, list[str]] = {}
    for ref in evidence_refs:
        evidence = evidence_by_ref.get(ref)
        if not isinstance(evidence, Mapping) or evidence.get("evidence_id") != ref:
            continue
        for fact in _source_facts(evidence):
            source_facts.append(fact)
            evidence_refs_by_fact.setdefault(fact, []).append(ref)

    claims: list[AtomicClaimAssessment] = []
    for index, fact in enumerate(parsed):
        scoped_facts = source_facts
        if fact.relation in {"direct_leader_of", "reports_to"}:
            owner_scopes = {
                source_fact.project_scope
                for parent in parsed[:index]
                if parent.relation == "owner_of" and parent.value == fact.subject
                for source_fact in source_facts
                if source_fact.relation == "owner_of"
                and source_fact.project_scope is not None
                and _fact_supports(source_fact, parent)
            }
            if owner_scopes:
                # A named leader in a different project does not complete
                # this owner's chain. Unscoped direct relations remain usable;
                # they make no claim about another project's identity.
                scoped_facts = [
                    source_fact for source_fact in source_facts
                    if source_fact.relation != "direct_leader_of"
                    or source_fact.project_scope is None
                    or (len(owner_scopes) == 1
                        and source_fact.project_scope in owner_scopes)
                ]
        matching_refs = tuple(dict.fromkeys(
            ref
            for source_fact, refs in evidence_refs_by_fact.items()
            if source_fact in scoped_facts
            if _fact_supports(source_fact, fact)
            for ref in refs
        ))
        evidence_status = _fact_status(fact, scoped_facts)
        status = (
            ClaimSupportStatus.CONTRADICTED
            if evidence_status is ClaimSupportStatus.CONTRADICTED
            else ClaimSupportStatus.SUPPORTED
            if matching_refs
            else evidence_status
        )
        claims.append(
            AtomicClaimAssessment(
                text=text if len(parsed) == 1 else f"{text} [claim {index + 1}]",
                relation=fact.relation,
                subject=fact.subject,
                value=fact.value,
                valid_on=fact.valid_on,
                status=status,
                supporting_evidence_refs=matching_refs,
            )
        )
    statuses = {claim.status for claim in claims}
    if ClaimSupportStatus.CONTRADICTED in statuses:
        overall = ClaimSupportStatus.CONTRADICTED
    elif ClaimSupportStatus.UNVERIFIABLE in statuses:
        overall = ClaimSupportStatus.UNVERIFIABLE
    elif ClaimSupportStatus.INSUFFICIENT in statuses:
        overall = ClaimSupportStatus.INSUFFICIENT
    else:
        overall = ClaimSupportStatus.SUPPORTED
    return ClaimAssessment(overall, tuple(claims))


def assess_rejected_owner_alternative(
    alternative: str,
    reason: str,
    evidence_refs: Sequence[str],
    decision_text: str,
    evidence_by_ref: Mapping[str, Mapping[str, Any]],
    *,
    alias_evidence_refs: Sequence[str] = (),
) -> ClaimSupportStatus:
    """Verify a rejected proposed owner with both historical and current reads.

    The alternative is a rejected candidate, not an affirmative Final claim.
    The reason must describe the historical-proposal/current-owner contrast.
    Dates may be omitted from prose: the cited, verified facts supply them.
    If prose does state dates, they must match those facts exactly.
    """
    candidate = _fact_from_clause(alternative)
    decision_facts = _claim_facts(decision_text)
    if (candidate is None or candidate.relation != "owner_of"
            or candidate.valid_on is not None or decision_facts is None):
        return ClaimSupportStatus.UNVERIFIABLE

    alias_facts = [
        fact
        for ref in alias_evidence_refs
        if isinstance((evidence := evidence_by_ref.get(ref)), Mapping)
        and evidence.get("evidence_id") == ref
        for fact in _source_facts(evidence)
        if fact.relation == "alias_of"
    ]
    candidate_subject = candidate.subject
    alias_parts = tuple(part.strip() for part in candidate_subject.split("/"))
    if len(alias_parts) == 2:
        matches = {
            fact.value
            for fact in alias_facts
            if set(alias_parts) == {fact.subject, fact.value}
        }
        if len(matches) == 1:
            candidate_subject = next(iter(matches))

    current = [
        fact for fact in decision_facts
        if fact.relation == "owner_of" and fact.subject == candidate_subject
        and fact.value != candidate.value and fact.temporal_kind == "snapshot"
        and fact.valid_on is not None
    ]
    if len(current) != 1:
        return ClaimSupportStatus.UNVERIFIABLE
    current_fact = current[0]
    normalized_reason = " ".join(reason.strip().rstrip(".!?。？！").casefold().split())
    old = re.escape(candidate.value)
    new = re.escape(current_fact.value)
    first = re.fullmatch(
        rf"{old} was listed as a proposed owner in an? "
        rf"(?:(?P<old_modifier>older|earlier|historical) )?"
        rf"(?:(?P<old_date_before>{_DATE}) )?draft"
        rf"(?: (?:as of|dated) (?P<old_date_after>{_DATE}))?, "
        rf"but {new} is (?:stated as )?(?:the )?accountable owner as of "
        rf"(?P<new_phrase>(?:a )?(?:later|more recent) date(?:, (?P<new_date>{_DATE}))?"
        rf"|(?P<new_date_direct>{_DATE}))",
        normalized_reason,
    )
    second = re.fullmatch(
        rf"{old} was listed as a proposed owner in a draft document dated "
        rf"(?P<old_date>{_DATE}), which was superseded by a later update "
        rf"on (?P<new_date>{_DATE})",
        normalized_reason,
    )
    match = first or second
    if match is None:
        return ClaimSupportStatus.UNVERIFIABLE
    cited_facts = [
        fact
        for ref in evidence_refs
        if isinstance((evidence := evidence_by_ref.get(ref)), Mapping)
        and evidence.get("evidence_id") == ref
        for fact in _source_facts(evidence)
    ]
    historical_dates = {
        fact.valid_on for fact in cited_facts
        if fact.relation == "proposed_owner_of"
        and fact.subject == candidate_subject and fact.value == candidate.value
        and fact.temporal_kind == "event" and fact.valid_on is not None
    }
    current_dates = {
        fact.valid_on for fact in cited_facts if _fact_supports(fact, current_fact)
    }
    if len(historical_dates) != 1 or current_dates != {current_fact.valid_on}:
        return ClaimSupportStatus.INSUFFICIENT
    old_date = next(iter(historical_dates))
    new_date = current_fact.valid_on
    explicit_old_date = (
        match.groupdict().get("old_date")
        or match.groupdict().get("old_date_before")
        or match.groupdict().get("old_date_after")
    )
    explicit_new_date = match.groupdict().get("new_date") or match.groupdict().get(
        "new_date_direct"
    )
    if (old_date is None or new_date is None or old_date >= new_date
            or (explicit_old_date is not None and _valid_date(explicit_old_date) != old_date)
            or (explicit_new_date is not None and _valid_date(explicit_new_date) != new_date)):
        return ClaimSupportStatus.CONTRADICTED
    return ClaimSupportStatus.SUPPORTED


def _owner_facts_at_explicit_date(question: str, facts: Sequence[_Fact]) -> tuple[_Fact, ...]:
    """Use an exact queried snapshot date; never infer continuity from an older one."""
    dates = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", question))
    if len(dates) != 1:
        return tuple(facts)
    date = next(iter(dates))
    return tuple(fact for fact in facts
                 if fact.valid_on == date and fact.temporal_kind == "snapshot")


def _literally_named_fact_subjects(
    question: str, evidence_values: Sequence[Mapping[str, Any]], relation: str,
) -> tuple[str, ...]:
    normalized_question = _normalize_entity(question)
    return tuple(sorted({
        fact.subject
        for evidence in evidence_values for fact in _source_facts(evidence)
        if fact.relation == relation
        and re.search(
            r"(?<![a-z0-9])" + re.escape(fact.subject) + r"(?![a-z0-9])",
            normalized_question,
        )
    }))


def verified_relation_path_values(
    question: str,
    relation_path: Sequence[str],
    evidence_values: Sequence[Mapping[str, Any]],
    *,
    expected_subject: str | None = None,
    expected_project_scope: str | None = None,
    minimum_valid_on: str | None = None,
) -> tuple[str, ...]:
    """Resolve a bounded relation chain from read source facts, never model assertions.

    The caller has already paired these passages with this run's verified read
    and workspace. Each edge must have one source-supported object; ambiguity
    and missing facts both fail closed. The path itself grants no evidence.
    """
    if not relation_path or any(item not in TYPED_CLAIM_RELATIONS for item in relation_path):
        return ()
    facts = tuple(
        fact for evidence in evidence_values
        if evidence.get("status") == "verified"
        for fact in _source_facts(evidence)
    )
    first_relations = TYPED_CLAIM_RELATIONS[relation_path[0]]["source_relations"]
    if expected_subject is not None:
        subjects = {_normalize_entity(expected_subject)}
    else:
        normalized_question = _normalize_entity(question)
        subjects = {
            fact.subject for fact in facts
            if fact.relation in first_relations
            and re.search(
                r"(?<![a-z0-9])" + re.escape(fact.subject) + r"(?![a-z0-9])",
                normalized_question,
            )
        }
    if len(subjects) != 1:
        return ()
    current = next(iter(subjects))
    project_scope = _normalize_entity(expected_project_scope) if expected_project_scope else None
    for predicate in relation_path:
        matching = tuple(
            fact for fact in facts
            if fact.relation in TYPED_CLAIM_RELATIONS[predicate]["source_relations"]
            and fact.subject == current
            and (predicate != "reports_to" or fact.project_scope is None
                 or fact.project_scope == project_scope)
            and (predicate != "reports_to" or minimum_valid_on is None
                 or fact.valid_on is None or fact.valid_on >= minimum_valid_on)
        )
        if predicate in {"owner_of_project", "reports_to"}:
            matching = _owner_facts_at_explicit_date(question, matching)
        if predicate == "owner_of_project":
            project_scope = current
        values = {fact.value for fact in matching}
        if len(values) != 1:
            return ()
        current = next(iter(values))
    return (current,)


def verified_owner_source_binding(
    question: str,
    evidence_values: Sequence[Mapping[str, Any]],
    owner: str,
    *,
    expected_project: str | None = None,
) -> tuple[str, str | None] | None:
    """Recover one project and owner date from source facts, never model text."""
    normalized_question = _normalize_entity(question)
    project = _normalize_entity(expected_project) if expected_project else None
    matching = _owner_facts_at_explicit_date(question, tuple(
        fact for evidence in evidence_values for fact in _source_facts(evidence)
        if fact.relation == "owner_of" and fact.value == _normalize_entity(owner)
        and (fact.subject == project if project is not None else
             re.search(r"(?<![a-z0-9])" + re.escape(fact.subject) + r"(?![a-z0-9])",
                       normalized_question) is not None)
    ))
    bindings = {(fact.project_scope or fact.subject, fact.valid_on) for fact in matching}
    return next(iter(bindings)) if len(bindings) == 1 else None


def verified_dependency_values(
    parent_question: str,
    child_question: str,
    evidence_values: Sequence[Mapping[str, Any]],
    *,
    expected_parent_entity: str | None = None,
) -> tuple[str, ...] | None:
    """Return the one relation object a known multi-hop transition may bind.

    ``None`` means the transition is outside this small typed vocabulary and
    the existing literal-mention rule may be used. An empty tuple means the
    transition is recognized but evidence is missing or conflicting, so it
    must not fall back to a different mentioned entity.
    """
    parent = " ".join(parent_question.casefold().split())
    child = " ".join(child_question.casefold().split())
    parent_replacement = re.search(
        r"\b(replac\w*|supersed\w*|successor|take over)\b", parent
    ) is not None or re.search(r"(?:替代|取代|接替|接棒)", parent) is not None
    child_owner = (
        re.search(r"\b(owner|owns|ownership)\b", child) is not None
        or re.search(r"(?:负责人|所有者|谁负责)", child) is not None
    )
    parent_owner = (
        re.search(r"\b(owner|owns|ownership|responsible for)\b", parent) is not None
        or re.search(r"(?:负责人|所有者|谁负责)", parent) is not None
    )
    child_leader = (
        _asks_reporting_target(child)
        or re.search(r"\b(leader|leaders|leads|supervisor|supervises|supervisory|reporting line)\b", child) is not None
        or re.search(r"(?:直属领导|直接领导|上级|上司|主管|直属汇报|直接汇报)", child) is not None
    )
    child_replacement = re.search(
        r"\b(replac\w*|supersed\w*|successor|take over)\b", child
    ) is not None or re.search(r"(?:替代|取代|接替)", child) is not None
    parent_decision = re.search(r"\b(approv\w*|decision|selected|chosen)\b", parent) is not None
    parent_alias = re.search(r"\b(?:alias(?:es)?|nickname(?:d|s)?)\b", parent) is not None or "别名" in parent or "代号" in parent

    if child_leader and parent_owner:
        relation = "owner_of"
    elif child_owner and parent_alias:
        relation = "alias_of"
    elif child_owner and parent_replacement:
        relation = "superseded_by"
    elif child_replacement and parent_decision:
        relation = "approved_for"
    else:
        return None

    parent_entity = _question_relation_entity(parent_question, relation)
    if parent_entity is None and relation in {"superseded_by", "alias_of"}:
        # A handoff question can name a project and the retired plan together
        # (for example, "Atlas Meridian A 退场后由哪项方案接棒"). Resolve its
        # subject only from the cited, verified supersession facts. Multiple
        # matching subjects remain ambiguous and fail closed.
        normalized_question = _normalize_entity(parent_question)
        cited_subjects = {
            fact.subject
            for evidence in evidence_values
            for fact in _source_facts(evidence)
            if fact.relation == relation
            and re.search(
                r"(?<![a-z0-9])" + re.escape(fact.subject) + r"(?![a-z0-9])",
                normalized_question,
            )
        }
        if len(cited_subjects) == 1:
            parent_entity = next(iter(cited_subjects))
        elif cited_subjects:
            return ()
    if parent_entity is None and relation == "owner_of" and parent_alias:
        # A model may combine nickname resolution and owner lookup in one
        # subgoal. Bind the owner only through an alias fact whose subject is
        # literally in that subgoal and an owner fact for its unique project.
        normalized_question = _normalize_entity(parent_question)
        alias_projects = {
            fact.value
            for evidence in evidence_values
            for fact in _source_facts(evidence)
            if fact.relation == "alias_of"
            and re.search(
                r"(?<![a-z0-9])" + re.escape(fact.subject) + r"(?![a-z0-9])",
                normalized_question,
            )
        }
        if alias_projects:
            if len(alias_projects) != 1:
                return ()
            candidate = next(iter(alias_projects))
            if not any(
                fact.relation == "owner_of" and fact.subject == candidate
                for evidence in evidence_values for fact in _source_facts(evidence)
            ):
                return ()
            parent_entity = candidate
    if parent_entity is None and relation == "owner_of":
        # The model may ask who is responsible for *the* named task. Bind the
        # relation only when exactly one cited owner fact names a subject that
        # also occurs literally in the parent question.
        cited_subjects = _literally_named_fact_subjects(
            parent_question, evidence_values, "owner_of",
        )
        if len(cited_subjects) == 1:
            parent_entity = cited_subjects[0]
        elif cited_subjects:
            return ()
    expected_entity = (
        _normalize_entity(expected_parent_entity)
        if isinstance(expected_parent_entity, str) and expected_parent_entity.strip()
        else None
    )
    if expected_entity is not None:
        if (
            parent_entity is not None
            and parent_entity != expected_entity
            and not _is_relation_anaphor(parent_entity)
        ):
            return ()
        parent_entity = expected_entity
    if parent_entity is None:
        # A recognized relation without a resolved subject cannot safely bind
        # any same-relation fact from the cited passage.
        return ()
    relation_facts = tuple(
        fact for evidence in evidence_values for fact in _source_facts(evidence)
        if fact.relation == relation and fact.subject == parent_entity
    )
    if relation == "owner_of":
        relation_facts = _owner_facts_at_explicit_date(parent_question, relation_facts)
    targets = {fact.value for fact in relation_facts}
    return tuple(sorted(targets)) if len(targets) == 1 else ()


def verified_subgoal_values(
    question: str, evidence_values: Sequence[Mapping[str, Any]],
    *, expected_parent_entity: str | None = None,
) -> tuple[str, ...] | None:
    """Check a recognized root relation against its exact queried subject.

    Unknown question forms return None; a known relation with no single
    supported object returns an empty tuple. Search hits alone never count.
    """
    for relation in ("owner_of", "alias_of", "superseded_by", "approved_for"):
        subject = _question_relation_entity(question, relation)
        if (subject is None and relation == "owner_of"
                and isinstance(expected_parent_entity, str)
                and expected_parent_entity.strip()
                and "{verified_entity}" in question
                and (re.search(r"\b(owner|owns|ownership)\b", question, re.I)
                     or re.search(r"负责人|所有者|谁负责", question))):
            subject = _normalize_entity(expected_parent_entity)
        if (subject is None and relation == "owner_of"
                and len(set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", question))) == 1):
            subjects = _literally_named_fact_subjects(question, evidence_values, relation)
            if len(subjects) > 1:
                return ()
            if subjects:
                subject = subjects[0]
        if subject is None:
            continue
        relation_facts = tuple(
            fact for evidence in evidence_values for fact in _source_facts(evidence)
            if fact.relation == relation and fact.subject == subject
        )
        if relation == "owner_of":
            relation_facts = _owner_facts_at_explicit_date(question, relation_facts)
        targets = {fact.value for fact in relation_facts}
        return tuple(targets) if len(targets) == 1 else ()
    return None


def _asks_reporting_target(question: str) -> bool:
    """Recognize person -> leader, not the reversed 'who reports to person'."""
    return bool(
        re.search(r"\b(?:to whom does|who does)\b[^?]*\breport(?:s)?(?: directly)?(?: to)?\b", question)
        or re.search(r"\{verified_entity\}\s+reports?(?: directly)? to\b", question)
        or re.search(r"向谁(?:直接)?汇报", question)
    )


def verified_dependent_leader_values(
    parent_question: str,
    child_question: str,
    parent_evidence: Sequence[Mapping[str, Any]],
    child_evidence: Sequence[Mapping[str, Any]],
) -> tuple[str, ...] | None:
    """Resolve a direct leader only for one verified upstream entity and scope.

    An older dated reporting snapshot cannot establish a reporting line for
    a later dated upstream owner snapshot. Unknown relation wording returns
    None, preserving the existing fail-closed behavior for other predicates.
    """
    parent_text = " ".join(parent_question.casefold().split())
    child_text = " ".join(child_question.casefold().split())
    child_is_leader = (
        re.search(r"\b(leader|manager|supervisor|reporting line)\b",
                  child_text) is not None
        or _asks_reporting_target(child_text)
        or re.search(r"(?:直属领导|直接领导|上级|上司|主管|直属汇报|直接汇报)", child_text)
        is not None
    )
    if not child_is_leader:
        return None
    parent_is_owner = (
        re.search(r"\b(owner|owns|ownership|accountable|responsible for)\b", parent_text) is not None
        or re.search(r"(?:负责人|所有者|谁负责)", parent_text) is not None
    )
    parent_is_leader = (
        re.search(r"\b(leader|manager|supervisor|reporting line)\b",
                  parent_text) is not None
        or _asks_reporting_target(parent_text)
        or re.search(r"(?:直属领导|直接领导|上级|上司|主管|直属汇报|直接汇报)", parent_text)
        is not None
    )
    parent_relations = (
        {"owner_of"} if parent_is_owner else
        {"direct_leader_of", "reports_to"} if parent_is_leader else None
    )
    if parent_relations is None:
        return None
    upstream = {
        fact
        for evidence in parent_evidence
        for fact in _source_facts(evidence)
        if fact.relation in parent_relations
    }
    if len(upstream) != 1:
        return ()
    parent = next(iter(upstream))
    if parent_is_owner and parent.subject not in _normalize_entity(parent_question):
        return ()
    targets = {
        fact.value
        for evidence in child_evidence
        for fact in _source_facts(evidence)
        if fact.relation in {"direct_leader_of", "reports_to"}
        and fact.subject == parent.value
        and (not parent_is_owner or fact.project_scope in {None, parent.subject})
        and (parent.valid_on is None or fact.valid_on is None
             or fact.valid_on >= parent.valid_on)
    }
    return tuple(sorted(targets)) if len(targets) == 1 else ()


def verified_subgoal_conflict(
    question: str, evidence_values: Sequence[Mapping[str, Any]],
) -> bool:
    """Detect incompatible current facts from two distinct verified sources."""
    return bool(verified_subgoal_conflict_refs(question, evidence_values))


def verified_subgoal_conflict_refs(
    question: str, evidence_values: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    """Identify two opposing source IDs in one relation, entity and time scope."""
    for relation in ("owner_of", "alias_of", "superseded_by", "approved_for", "reports_to"):
        subject = _question_relation_entity(question, relation)
        if subject is None:
            continue
        return verified_relation_conflict_refs(relation, subject, evidence_values, question=question)
    return ()


def verified_relation_conflict_refs(
    relation: str, subject: str, evidence_values: Sequence[Mapping[str, Any]],
    *, question: str = "", expected_project_scope: str | None = None,
    minimum_valid_on: str | None = None,
) -> tuple[str, str]:
    """Check a typed relation and subject against two distinct verified sources."""
    if relation not in {"owner_of", "alias_of", "superseded_by", "approved_for", "reports_to"}:
        return ()
    normalized_subject = _normalize_entity(subject)
    groups: dict[tuple[str | None, str | None, str | None], dict[str, list[tuple[str, str]]]] = {}
    for evidence in evidence_values:
        ref = evidence.get("evidence_id")
        path = evidence.get("relative_path")
        if (evidence.get("status") != "verified" or not isinstance(ref, str)
                or not isinstance(path, str)):
            continue
        facts = _source_facts(evidence)
        if relation in {"owner_of", "reports_to"}:
            facts = _owner_facts_at_explicit_date(question, facts)
        for fact in facts:
            relations = {"reports_to", "direct_leader_of"} if relation == "reports_to" else {relation}
            if (fact.relation in relations and fact.subject == normalized_subject
                    and (relation != "reports_to" or fact.project_scope is None
                         or fact.project_scope == expected_project_scope)
                    and (relation != "reports_to" or minimum_valid_on is None
                         or fact.valid_on is None or fact.valid_on >= minimum_valid_on)):
                groups.setdefault(
                    (fact.valid_on, fact.temporal_kind, fact.project_scope), {},
                ).setdefault(fact.value, []).append((path, ref))
    for values in groups.values():
        for left_value, left_sources in values.items():
            for right_value, right_sources in values.items():
                if left_value == right_value:
                    continue
                for left_path, left_ref in left_sources:
                    for right_path, right_ref in right_sources:
                        if left_path != right_path and left_ref != right_ref:
                            return left_ref, right_ref
    return ()


def _is_relation_anaphor(entity: str) -> bool:
    """Recognize only bounded references that may inherit a verified parent entity."""
    normalized = " ".join(entity.casefold().split())
    return normalized in {
        "the plan",
        "that plan",
        "this plan",
        "the replacement",
        "that replacement",
        "this replacement",
        "the successor",
        "that successor",
        "this successor",
        "该方案",
        "这个方案",
        "此方案",
        "该计划",
        "这个计划",
        "此计划",
    }


def _question_relation_entity(question: str, relation: str) -> str | None:
    """Extract the queried relation subject without treating answer entities as input."""
    patterns: tuple[str, ...]
    if relation == "alias_of":
        patterns = (
            rf"(?i:does)\s+(?P<entity>{_ENTITY})\s+(?i:alias)\b",
            rf"(?P<entity>{_ENTITY})\s+(?i:is (?:an? |the )?(?:internal |team )?alias)\b",
            rf"(?P<entity>[^\s?？]+)\s*的(?:别名|代号)",
        )
    elif relation == "superseded_by":
        patterns = (
            rf"(?i:(?:which|what) plan (?:replaced|superseded))\s+(?P<entity>{_ENTITY})",
            rf"(?:哪个|什么)(?:方案|计划)\s*(?:替代|取代|接替)(?:了)?\s*(?P<entity>[^?？]+)",
            rf"^(?i:(?:replaced|superseded))\s+(?P<entity>{_ENTITY})",
            rf"^(?P<entity>{_ENTITY})\s+(?i:(?:replacement|successor))\b",
            rf"(?P<entity>{_ENTITY})\s+(?i:(?:replaced|superseded))\b",
        )
    elif relation == "owner_of":
        patterns = (
            rf"(?i:owns)\s+(?P<entity>{_ENTITY})",
            rf"(?i:owner of)\s+(?P<entity>{_ENTITY})",
            rf"(?i:responsible for)\s+(?P<entity>{_ENTITY})",
            rf"谁负责\s*(?P<entity>[^?？]+)",
            rf"(?:谁是|谁为)(?P<entity>[^?？]+?)(?:的)?(?:负责人|所有者)",
        )
    elif relation == "reports_to":
        patterns = (rf"(?i:direct leader of)\s+(?P<entity>{_ENTITY})",)
    elif relation == "approved_for":
        patterns = (
            rf"(?i:the)\s+(?P<entity>{_ENTITY}?)\s+(?i:project)\s+(?i:approv\w*)\b",
            rf"(?P<entity>{_ENTITY}?)\s+(?i:project)\s+(?i:approv\w*)\b",
        )
    else:
        return None
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            entity = _normalize_entity(match.group("entity"))
            if relation == "owner_of" and (
                "已验证" in entity or "verified" in entity
            ):
                return None
            return entity
    return None


__all__ = [
    "AtomicClaimAssessment",
    "ClaimAssessment",
    "ClaimSupportStatus",
    "TYPED_CLAIM_RELATIONS",
    "assess_claim_text",
    "assess_rejected_owner_alternative",
    "evidence_supports_typed_claim",
    "verified_dependency_values",
    "verified_dependent_leader_values",
    "verified_subgoal_values",
    "verified_subgoal_conflict",
    "verified_subgoal_conflict_refs",
    "verified_relation_conflict_refs",
]
