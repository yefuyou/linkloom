"""Read-only authority checks for supplier-change relation endpoints."""

from __future__ import annotations

import json
from collections.abc import Iterable

from linkloom.decision_memory.models import DecisionMemoryState, DecisionRecord, normalize_temporal_component
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import EvidenceSpan, RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.materialization import (
    MaterializationBlockedError,
    SemanticDecisionMaterializer,
    _receipt_from_row,
)
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote
from linkloom.semantic_ingestion.task_relation_validation import verify_evidence_quote

_DECISION_STATES = {DecisionMemoryState.ACTIVE, DecisionMemoryState.SUPERSEDED}
_MATERIALIZED_STATES = {"MATERIALIZED", "DUPLICATE"}


def verify_change_authority(
    change: ChangeRef,
    store: TemporalDecisionStore,
    source_registry: SourceReferenceRegistry,
    artifacts: Iterable[RawArtifact],
) -> tuple[set[str], tuple[str, ...], tuple[EvidenceQuote, ...]]:
    """Check a ChangeRef against persisted decisions without writing state."""
    errors: set[str] = set()
    checks: list[str] = []
    extra: list[EvidenceQuote] = []
    if not isinstance(change, ChangeRef):
        return {"INVALID_CHANGE_CONTRACT"}, (), ()
    if not isinstance(artifacts, (tuple, list)) or any(not isinstance(a, RawArtifact) for a in artifacts):
        errors.add("INVALID_ARTIFACTS")
        artifacts = ()
    source_errors, _ = verify_evidence_quote(change.evidence, change.workspace_id, artifacts, source_registry)
    errors.update(source_errors)
    if change.kind == "SOURCE_CLAIM":
        errors.add("AUTHORITY_NOT_ESTABLISHED")
        return errors, ("SOURCE_CLAIM_ONLY",), ()
    checks.append("REVIEWED_DECISION_CHAIN_REQUIRED")
    if not isinstance(store, TemporalDecisionStore):
        errors.add("INVALID_STORE")
        return errors, tuple(checks), ()
    if not isinstance(source_registry, SourceReferenceRegistry):
        errors.add("INVALID_SOURCE_REGISTRY")
        return errors, tuple(checks), ()

    current = store.get_decision(change.workspace_id, change.decision_id or "")
    checks.append("CURRENT_DECISION_READ")
    if current is None:
        errors.add("DECISION_NOT_FOUND")
        return errors, tuple(checks), ()
    if current.memory_state not in _DECISION_STATES:
        errors.add("DECISION_STATE_INVALID")
    _match_fields(change, current, change.after_vendor, errors)
    if change.evidence.span.evidence_ref not in current.source_evidence_refs:
        errors.add("SOURCE_PROOF_MISMATCH")

    prior = store.get_decision(change.workspace_id, current.supersedes_id) if current.supersedes_id else None
    if current.supersedes_id is None:
        errors.add("DECISION_CHAIN_MISSING")
    else:
        checks.append("PRIOR_DECISION_READ")
        if prior is None:
            errors.add("PRIOR_DECISION_MISSING")
        else:
            if prior.memory_state not in _DECISION_STATES:
                errors.add("PRIOR_DECISION_STATE_INVALID")
            _match_fields(change, prior, change.before_vendor, errors)
            if prior.valid_from >= current.valid_from:
                errors.add("DECISION_CHAIN_ORDER_INVALID")
            if prior.subject_key != current.subject_key or prior.valid_to != current.valid_from:
                errors.add('DECISION_CHAIN_BOUNDARY_MISMATCH')

    materializer = SemanticDecisionMaterializer(store)
    current_receipt = store.get_semantic_receipt_row(change.workspace_id, change.candidate_id or "")
    if current_receipt is None or str(current_receipt.get("receipt_id")) != change.receipt_id:
        errors.add("AUTHORITY_RECEIPT_MISSING")
    else:
        _check_receipt(current_receipt, current, store, materializer, artifacts, source_registry, errors)
        if not errors:
            checks.append("HUMAN_APPROVAL_BOUND")

    if prior is not None:
        prior_receipt = next((r for r in store.get_decision_receipt_rows(change.workspace_id, prior.decision_id)
                              if str(r.get("authorization_type")) == "HUMAN_APPROVAL"), None)
        if prior_receipt is None:
            errors.add("PRIOR_AUTHORITY_RECEIPT_MISSING")
        else:
            _check_receipt(prior_receipt, prior, store, materializer, artifacts, source_registry, errors)
            checks.append("PRIOR_RECEIPT_BOUND")
        for decision in (prior, current):
            for quote in _decision_evidence_quotes(decision, artifacts, errors):
                codes, _ = verify_evidence_quote(quote, change.workspace_id, artifacts, source_registry)
                errors.update(codes)
                if not codes and quote not in extra:
                    extra.append(quote)
        checks.append("DECISION_SOURCE_EVIDENCE_VERIFIED")
    if not errors.intersection({"CHANGE_PROJECT_MISMATCH", "CHANGE_RELATION_MISMATCH",
                                "CHANGE_BEFORE_VENDOR_MISMATCH", "CHANGE_AFTER_VENDOR_MISMATCH",
                                "SOURCE_PROOF_MISMATCH"}):
        checks.append("CHANGE_FIELDS_MATCHED")
    return errors, tuple(checks), tuple(extra)


def _match_fields(change: ChangeRef, decision: DecisionRecord, value: str, errors: set[str]) -> None:
    if _norm(decision.subject) != _norm(change.project):
        errors.add("CHANGE_PROJECT_MISMATCH")
    if _norm(decision.relation) != "uses vendor":
        errors.add("CHANGE_RELATION_MISMATCH")
    if _norm(decision.value) != _norm(value):
        errors.add("CHANGE_AFTER_VENDOR_MISMATCH" if value == change.after_vendor else "CHANGE_BEFORE_VENDOR_MISMATCH")


def _check_receipt(receipt: dict[str, object], decision: DecisionRecord, store: TemporalDecisionStore,
                   materializer: SemanticDecisionMaterializer, artifacts: Iterable[RawArtifact],
                   source_registry: SourceReferenceRegistry, errors: set[str]) -> None:
    try:
        _receipt_from_row(receipt)
    except (TypeError, ValueError, KeyError, MaterializationBlockedError):
        errors.add("RECEIPT_INVALID")
        return
    workspace = decision.workspace_id
    candidate_id = str(receipt.get("candidate_id", ""))
    row = store.get_semantic_candidate_row(workspace, candidate_id)
    if row is None:
        errors.add("CANDIDATE_NOT_FOUND")
        return
    state = str(row.get("workflow_state"))
    if state in {"STALE", "INVALIDATED", "REJECTED"}:
        errors.add("CANDIDATE_REVOKED")
    elif state not in _MATERIALIZED_STATES:
        errors.add("CANDIDATE_NOT_MATERIALIZED")
    if str(row.get("candidate_fingerprint")) != str(receipt.get("candidate_fingerprint")):
        errors.add("RECEIPT_FINGERPRINT_MISMATCH")
    if str(receipt.get("workspace_id")) != workspace or str(receipt.get("decision_id")) != decision.decision_id:
        errors.add("RECEIPT_DECISION_MISMATCH")
    if str(receipt.get("supersedes_id")) != str(decision.supersedes_id):
        errors.add("RECEIPT_SUPERSESSION_MISMATCH")
    if str(receipt.get("authorization_type")) != "HUMAN_APPROVAL":
        errors.add("AUTHORITY_HUMAN_APPROVAL_MISSING")
    approval = next((e for e in reversed(store.get_semantic_candidate_events(workspace, candidate_id))
                     if str(e.get("event_type")) == "HUMAN_APPROVAL"
                     and str(e.get("actor_type")) == "HUMAN"
                     and str(e.get("candidate_fingerprint")) == str(receipt.get("candidate_fingerprint"))), None)
    if approval is None:
        errors.add("AUTHORITY_APPROVAL_EVENT_MISSING")
    else:
        if any(str(approval.get(a)) != str(receipt.get(b)) for a, b in (
            ("actor_id", "authorization_id"), ("reason", "authorization_reason"), ("event_at", "authorized_at"))):
            errors.add("AUTHORITY_APPROVAL_BINDING_MISMATCH")
        try:
            if not isinstance(json.loads(str(approval.get("metadata_json"))), dict):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            errors.add("AUTHORITY_APPROVAL_EVENT_MALFORMED")
    try:
        view, fingerprint = materializer._load_candidate(workspace, candidate_id)
    except (MaterializationBlockedError, ValueError, TypeError, KeyError):
        errors.add("CANDIDATE_PROVENANCE_INVALID")
        return
    if fingerprint != str(row.get("candidate_fingerprint")):
        errors.add("CANDIDATE_FINGERPRINT_MISMATCH")
    if (view.workspace_id != workspace or view.subject != decision.subject or view.relation != decision.relation
            or view.value != decision.value or tuple(view.decision_evidence_refs) != tuple(decision.source_evidence_refs)):
        errors.add("CANDIDATE_PROVENANCE_MISMATCH")
    primary = view.primary_evidence
    if (str(row.get("artifact_id")) != primary.artifact_id
            or str(row.get("artifact_version_id")) != primary.artifact_version_id
            or str(row.get("content_sha256")) != primary.content_hash):
        errors.add("CANDIDATE_PROVENANCE_MISMATCH")
    for binding in view.evidence:
        span = binding.semantic_span
        if not isinstance(span, EvidenceSpan):
            errors.add("CANDIDATE_PROVENANCE_MISSING")
            continue
        quote = EvidenceQuote(span, binding.char_start, binding.char_end, binding.quote, "CHANGE_CLAIM")
        codes, _ = verify_evidence_quote(quote, workspace, artifacts, source_registry)
        errors.update(codes)


def _decision_evidence_quotes(decision: DecisionRecord, artifacts: Iterable[RawArtifact],
                              errors: set[str]) -> tuple[EvidenceQuote, ...]:
    spans: dict[str, EvidenceSpan] = {}
    for artifact in artifacts:
        try:
            segments = parse_source_text(artifact).segments
        except (TypeError, ValueError):
            continue
        for segment in segments:
            span = segment.evidence_span
            if span.evidence_ref in decision.source_evidence_refs:
                if span.evidence_ref in spans and spans[span.evidence_ref] != span:
                    errors.add("SOURCE_EVIDENCE_SPAN_AMBIGUOUS")
                spans[span.evidence_ref] = span
    quotes = []
    for ref in decision.source_evidence_refs:
        span = spans.get(ref)
        if span is None:
            errors.add("SOURCE_EVIDENCE_SPAN_MISSING")
        else:
            quotes.append(EvidenceQuote(span, span.char_start, span.char_end, span.quote, "CHANGE_CLAIM"))
    return tuple(quotes)


def _norm(value: str | None) -> str:
    return normalize_temporal_component(value or "")
