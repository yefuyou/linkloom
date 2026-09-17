"""Registered provider-neutral procedures and closed lifecycle value policy."""
from types import MappingProxyType

from linkloom.experience.models import ExperienceApplicability
from linkloom.experience.policy import validate_gold_safe_payload

DIMENSIONS = ("contract", "grounding", "decision_semantics", "question_scope",
              "unsupported_inference", "uncertainty", "retrieval_tools")
POLICY_VERSION = "strategy-promotion-policy/v1"
STRATEGY_TEMPLATE = MappingProxyType(dict(
    generation_rule_id="explicit_rejection_procedure/v1",
    title="Require evidence of explicit rejection",
    situation="A current decision task compares alternatives with non-selection or supersession evidence.",
    behavioral_rule="Before classifying an alternative as rejected, require explicit rejection semantics in cited evidence; comparison, non-selection, lower ranking or supersession alone is insufficient. Preserve uncertainty when rejection is not established.",
    applicability=ExperienceApplicability(("team_decision",), ("current_decision", "compared_options"), ("explicit_rejection_evidence_present",)),
    exclusions=("explicit_rejection_evidence_present",),
    expected_benefit="Reduce unsupported classification while preserving uncertainty.",
    known_risks=("Added context may increase input size.", "Over-abstention must be checked against legitimate rejection evidence."),
    evaluation_requirements=(*DIMENSIONS, "context_size", "cost_proxy", "evidence_semantics", POLICY_VERSION),
))


def validate_shape(record):
    for name, value in STRATEGY_TEMPLATE.items():
        if getattr(record, name) != value:
            raise ValueError(f"{name} must equal the registered generic procedure")
    # Strategy's exact schema allows expected_benefit as a hypothesis field;
    # never weaken Gate 4's forbidden-key policy or feed it arbitrary answers.
    for value in record.to_dict().values():
        validate_gold_safe_payload(value)


def validate_record(record):
    from .models import StrategyCandidate
    if type(record) is not StrategyCandidate or StrategyCandidate.from_dict(record.to_dict()) != record:
        raise ValueError("Strategy failed closed record replay")


def render_procedure(record, heading):
    return (f"{heading}\nSituation: {record.situation}\nBehavioral rule: {record.behavioral_rule}\n"
            f"Applicability: {', '.join(record.applicability.workflows)}; {', '.join(record.applicability.task_characteristics)}\n"
            f"Exclusions: {', '.join(record.exclusions) or 'none'}")


def recommendation(rows, chars, byte_count, proof_kind):
    if chars > 4000 or byte_count > 16000 or any(r.delta == -1 for r in rows):
        return "recommend_reject"
    if any(r.dimension in {"contract", "grounding"} and r.candidate == "FAIL" for r in rows):
        return "recommend_reject"
    if proof_kind == "scripted_replay" or any(
        r.baseline not in {"PASS", "FAIL"} or r.candidate not in {"PASS", "FAIL"}
        for r in rows if not (r.dimension == "retrieval_tools" and r.baseline == r.candidate == "N/A")
    ):
        return "insufficient_evidence"
    return "recommend_accept" if any(r.dimension == "decision_semantics" and r.delta == 1 for r in rows) else "insufficient_evidence"
