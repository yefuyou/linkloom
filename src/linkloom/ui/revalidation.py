"""Product projection over existing source, review event and receipt contracts."""
from __future__ import annotations

from datetime import UTC, datetime, time
import hashlib
import json
from typing import TYPE_CHECKING, Any

from linkloom.decision_memory.models import DecisionMemoryState, decision_slot_key_for
from linkloom.semantic_ingestion.candidate_models import CandidateDecisionFact
from linkloom.ui.product import ProductApplicationError, _required_payload_text

if TYPE_CHECKING:
    from linkloom.ui.product import ProductApplication


class DecisionRevalidation:
    def __init__(self, app: ProductApplication) -> None:
        self.app = app

    def completed(self, workspace_id: str) -> dict[str, dict[str, Any]]:
        """A recorded approval alone never closes Attention; require its receipt."""
        return self.app.store.get_decision_revalidation_links(workspace_id)

    def inspect(self, decision_id: str) -> dict[str, Any]:
        app = self.app
        workspace_id = app._workspace_id()
        record = app.store.get_decision(workspace_id, decision_id)
        if record is None:
            raise ProductApplicationError("DECISION_NOT_FOUND", "The decision is unavailable in this workspace.", status=404)
        original = app._decision_payload(record)
        receipts = list(app.store.get_decision_receipt_rows(workspace_id, decision_id))
        sources = {}
        reasons = []
        inventory = app.repository.source_inventory().get(workspace_id, {})
        for evidence in original["evidence"]:
            ref = evidence["evidence_ref"]
            historical = app.repository.evidence_source(workspace_id, ref)
            source = (app.repository.get_source(workspace_id, str(historical["source_id"]), include_content=True,
                                               include_deleted=True) if historical else None)
            if source is not None:
                sources[source["source_id"]] = source
            code = ("ORIGINAL_EVIDENCE_UNAVAILABLE" if not evidence["verified"] else
                    "SOURCE_MISSING" if source is None else
                    "SOURCE_REMOVED" if not source["active"] else
                    "EVIDENCE_WITHDRAWN_OR_CHANGED" if ref not in inventory else None)
            if code:
                reasons.append({"code": code, "evidence_ref": ref,
                                "original_version": evidence.get("source_version"),
                                "current_version": source["source_version"] if source else None})
        if not reasons and record.memory_state in {DecisionMemoryState.STALE, DecisionMemoryState.NEEDS_REVALIDATION}:
            reasons.append({"code": "SOURCE_RECONCILIATION_REQUIRED"})
        if record.memory_state is DecisionMemoryState.INVALIDATED:
            reasons.append({"code": "DECISION_INVALIDATED"})
        candidates = []
        now = datetime.now(UTC)
        pending_candidates = app._review_candidates()
        # A crash after durable approval but before materialization can resume
        # only the exact same target-bound approval, never another old grant.
        for row in app.store.list_semantic_candidate_rows(
            workspace_id, workflow_state="APPROVED", candidate_source_kind="SOURCE_INGESTION"
        ):
            candidate_id = str(row["candidate_id"])
            if app.store.get_semantic_receipt_row(workspace_id, candidate_id) is not None:
                continue
            approvals = [event for event in app.store.get_semantic_candidate_events(workspace_id, candidate_id)
                         if event["event_type"] == "HUMAN_APPROVAL"]
            if (not approvals or json.loads(str(approvals[-1]["metadata_json"])).get(
                    "revalidation_of_decision_id") != decision_id):
                continue
            candidate = app.materializer.get_candidate(workspace_id, candidate_id)
            if isinstance(candidate, CandidateDecisionFact):
                source = app.repository.get_source(workspace_id, candidate.artifact_id)
                if source and source["source_version"] == candidate.artifact_version_id:
                    pending_candidates.append(app._candidate_payload(candidate))
        for pending in pending_candidates:
            candidate = app.materializer.get_candidate(workspace_id, pending["candidate_id"])
            if not isinstance(candidate, CandidateDecisionFact) or candidate.claim_type.value != "DECISION":
                continue
            if decision_slot_key_for(candidate.subject, candidate.relation) != record.subject_key:
                continue
            if set(app._unresolved_fields(candidate)) - {"valid_from"}:
                continue
            start = candidate.valid_from
            end = candidate.valid_to
            start = start if start is None or isinstance(start, datetime) else datetime.combine(start, time(), UTC)
            end = end if end is None or isinstance(end, datetime) else datetime.combine(end, time(), UTC)
            if (start is not None and (start > now or start < record.valid_from)) or (end is not None and end <= now):
                continue
            detail = app._candidate_detail(candidate.candidate_id)
            if pending["status"] == "APPROVED":
                event = next(event for event in reversed(app.store.get_semantic_candidate_events(
                    workspace_id, candidate.candidate_id)) if event["event_type"] == "HUMAN_APPROVAL")
                detail["recorded_approval"] = {
                    "reviewer_id": event["actor_id"], "reason": event["reason"],
                    "authorized_at": event["event_at"],
                    "resolution": json.loads(str(event["metadata_json"])).get("review_resolution"),
                }
            # Inspect verifies exact provenance; the unchanged materializer
            # remains the final source, policy and conflict gate on approval.
            candidates.append(detail)
        completion = self.completed(workspace_id).get(decision_id)
        actionable = (receipts and record.valid_to is None
                      and record.memory_state in {DecisionMemoryState.STALE, DecisionMemoryState.NEEDS_REVALIDATION}
                      and completion is None)
        view = {"workspace_id": workspace_id, "original": original,
                "original_receipts": receipts, "current_sources": list(sources.values()),
                "reasons": reasons, "candidates": candidates if actionable else [],
                "status": "RESOLVED" if completion else "UNRESOLVED" if actionable else "READ_ONLY",
                "completion": completion}
        view["review_token"] = hashlib.sha256(json.dumps(view, sort_keys=True, ensure_ascii=False,
                                                        separators=(",", ":"), default=str).encode()).hexdigest()
        return view

    def approve(self, decision_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        view = self.inspect(decision_id)
        if _required_payload_text(payload, "workspace_id", 128) != view["workspace_id"]:
            raise ProductApplicationError("WORKSPACE_MISMATCH", "The workspace changed. Reopen the decision.", status=409)
        if view["status"] != "UNRESOLVED":
            raise ProductApplicationError("REVALIDATION_NOT_OPEN", "This decision is not open for revalidation.", status=409)
        if _required_payload_text(payload, "review_token", 128) != view["review_token"]:
            raise ProductApplicationError("REVALIDATION_CHANGED", "Sources or review candidates changed. Reopen revalidation before approving.", status=409)
        candidate_id = _required_payload_text(payload, "candidate_id", 256)
        candidate = next((item for item in view["candidates"] if item["candidate_id"] == candidate_id), None)
        if candidate is None:
            raise ProductApplicationError("REVALIDATION_CANDIDATE_UNAVAILABLE", "Choose a current, resolved decision candidate for this decision slot.", status=409)
        if payload.get("candidate_fingerprint") != candidate["candidate_fingerprint"]:
            raise ProductApplicationError("APPROVAL_STALE", "The candidate changed. Review its evidence again.", status=409)
        if candidate["status"] == "APPROVED" and any(
            key in payload for key in ("reviewer_id", "reason", "subject", "relation", "valid_from", "resolution")
        ):
            raise ProductApplicationError("REVALIDATION_ALREADY_AUTHORIZED", "Finish the recorded approval without supplying new reviewer details or changing its resolution.", status=409)
        if payload.get("resolution") is not None:
            raise ProductApplicationError("REVALIDATION_RESOLUTION_UNSUPPORTED", "Resolve this candidate in Review before revalidation.", status=409)
        resolution = self.app._review_resolution(payload)
        if resolution and resolution.valid_from:
            start = resolution.valid_from
            start = start if isinstance(start, datetime) else datetime.combine(start, time(), UTC)
            original = self.app.store.get_decision(view["workspace_id"], decision_id)
            if start > datetime.now(UTC) or start < original.valid_from:
                raise ProductApplicationError("REVALIDATION_TIME_INVALID", "Choose a source-supported effective time at or after the original decision and no later than now.", status=409)
        return self.app._approve_candidate(candidate_id, payload, revalidation_of=decision_id,
                                           resume_approved=candidate["status"] == "APPROVED")
