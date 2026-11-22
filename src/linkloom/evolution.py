"""Read-only evolution views for exact, Host-authorized decision and task queries."""
from datetime import UTC, datetime
import hashlib

from linkloom.decision_memory.models import UndatedDecisionRecord, UndatedDecisionState
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.tool import parse_decision_memory_as_of
from linkloom.semantic_ingestion.artifacts import RawArtifact, line_range_for
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tasks.review_storage import ReviewStorage
from linkloom.tasks.store import TaskStore
from linkloom.temporal_graph.projection import read_source_evidence


def decision_evolution(database_path, *, workspace_id, authorize_source, subject, relation,
                       mode="CURRENT", as_of=None):
    """Read an exact slot in a trusted workspace, with an ISO-8601 query instant.

    Only FOUND point queries have a `decision`. `dated` and `undated` carry
    supporting records separately. Interval bounds are [valid_from, valid_to).
    The Host supplies the workspace and the graph-style source authorization.
    """
    if mode not in {"CURRENT", "AS_OF", "HISTORY", "UNDATED"}:
        raise ValueError("unknown decision evolution mode")
    if mode == "AS_OF" and as_of is None:
        raise ValueError("AS_OF requires an explicit instant")
    if mode in {"CURRENT", "UNDATED"} and as_of is not None:
        raise ValueError(f"{mode} does not take an AS_OF instant")
    if not workspace_id.strip() or not subject.strip() or not relation.strip():
        raise ValueError("workspace, subject and relation are required")
    at = parse_decision_memory_as_of(as_of)
    if mode == "CURRENT":
        at = datetime.now(UTC)

    with TemporalDecisionStore(database_path, read_only=True) as store:
        db = store._connection
        db.execute("BEGIN")
        has_sources = _has_table(db, "ll_source_evidence")
        history = store.lookup_history(workspace_id, subject, relation) if mode != "UNDATED" else ()
        point_query = mode in {"CURRENT", "AS_OF"}
        if point_query:
            state = store.lookup_decision_state(workspace_id, subject, relation, as_of=at, mode=mode)
            eligible = {record.decision_id for record in state.temporal_result.records}
            history = tuple(record for record in history if record.valid_from <= at
                            and (record.valid_to is None or at < record.valid_to))
        undated_history = store.lookup_undated_history(workspace_id, subject, relation)

        promoted = {record.promoted_decision_id: record for record in undated_history if record.promoted_decision_id}
        dated = []
        corrections = {}
        revalidations = store.get_decision_revalidation_links(workspace_id) if history else {}
        for record in history:
            receipts = store.get_decision_receipt_rows(workspace_id, record.decision_id)
            for receipt in receipts:
                if original := receipt.get("correction_of_decision_id"):
                    corrections[original] = record.decision_id
            if mode == "HISTORY" and at is not None and record.valid_from > at:
                continue
            original = promoted.get(record.decision_id)
            if original is not None:
                receipts += store.get_undated_decision_receipts(workspace_id, original.undated_decision_id)
            dated.append(_decision_record(
                db, record, receipts, authorize_source, has_sources,
                revalidation=revalidations.get(record.decision_id),
                decision_ref=original.decision_ref if original else record.decision_id,
            ))

        undated = [
            _decision_record(db, record, store.get_undated_decision_receipts(
                workspace_id, record.undated_decision_id), authorize_source, has_sources)
            for record in undated_history
            if mode == "HISTORY" or record.memory_state not in {
                UndatedDecisionState.PROMOTED_TO_DATED, UndatedDecisionState.REVOKED,
            }
        ]

    result = dict(mode=mode, as_of=at.isoformat() if at else None, decision=None,
                  status="NOT_FOUND", reasons=[], dated=dated, undated=undated)
    if mode == "HISTORY":
        if dated or undated:
            result["status"] = "FOUND"
        return result
    if mode == "UNDATED":
        if undated:
            result["status"] = "FOUND" if any(item["evidence_status"] == "VERIFIED" and
                                             item["memory_state"] == "ACTIVE" for item in undated) else "UNCERTAIN"
            result["reasons"] = ["EFFECTIVE_TIME_UNKNOWN"] if result["status"] == "FOUND" else ["SOURCE_REVIEW_REQUIRED"]
        return result

    verified = [item for item in dated if item["decision_id"] in eligible and item["evidence_status"] == "VERIFIED"]
    verified_ids = {item["decision_id"] for item in verified}
    unresolved = [
        item for item in dated if item["decision_id"] not in verified_ids
        and corrections.get(item["decision_id"]) not in verified_ids
        and (item["revalidated_by"] or {}).get("decision_id") not in verified_ids
    ]
    reasons = result["reasons"]
    if len(verified) > 1:
        reasons.append("OVERLAPPING_CONFIRMED_DECISIONS")
    if unresolved:
        reasons.append("SOURCE_REVIEW_REQUIRED")
    if state.current_uncertain_due_to_undated or (undated and (mode == "CURRENT" or not verified)):
        reasons.append("EFFECTIVE_TIME_UNKNOWN")
    if len(verified) > 1:
        result["status"] = "CONFLICT"
    elif reasons:
        result["status"] = "UNCERTAIN"
    elif verified:
        result.update(status="FOUND", decision=verified[0])
    else:
        reasons.append(state.temporal_result.status.value)
    return result


def _has_table(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _decision_record(db, record, receipts, authorize_source, has_sources, *, revalidation=None, decision_ref=None):
    undated = isinstance(record, UndatedDecisionRecord)
    evidence = []
    for ref in record.source_evidence_refs:
        try:
            if not has_sources:
                raise ValueError("VERIFIED_EVIDENCE_REQUIRED")
            proof = read_source_evidence(db, record.workspace_id, ref, authorize_source)
        except (PermissionError, ValueError) as error:
            evidence.append(dict(evidence_id=ref, status=str(error)))
        else:
            evidence.append(dict(proof.to_dict(), status="VERIFIED"))
    denied = any(item["status"] == "SOURCE_NOT_AUTHORIZED" for item in evidence)
    verified = bool(evidence) and all(item["status"] == "VERIFIED" for item in evidence)
    return dict(
        decision_id=record.undated_decision_id if undated else record.decision_id,
        decision_ref=record.decision_ref if undated else decision_ref,
        subject_key=record.subject_key, subject=record.subject, relation=record.relation,
        value=None if denied else record.value, memory_state=record.memory_state.value,
        valid_from=None if undated else record.valid_from.isoformat(),
        valid_to=None if undated or record.valid_to is None else record.valid_to.isoformat(),
        supersedes_id=record.supersedes_decision_id if undated else record.supersedes_id,
        promoted_decision_id=record.promoted_decision_id if undated else None,
        revalidated_by=revalidation,
        evidence=evidence,
        evidence_status="VERIFIED" if verified else "SOURCE_REVIEW_REQUIRED",
        receipts=[] if denied else [
            dict(receipt_id=receipt.receipt_id, authorization_type=receipt.authorization_type,
                 authorized_at=receipt.authorized_at.isoformat()) if not isinstance(receipt, dict) else
            {key: receipt[key] for key in ("receipt_id", "authorization_type", "authorized_at")}
            for receipt in receipts
        ],
    )


def task_evolution(database_path, *, workspace_id, authorize_source, task_id):
    """Read the TaskStore snapshot, event history and effective links in one snapshot.

    Events retain append order. `meeting_time` stays nullable even when there
    is an approval or workflow update timestamp. Review notes and quotes are
    only returned when the sources they depend on are authorized.
    """
    with TaskStore(database_path, read_only=True) as store, store._transaction():
        task = store.get_task(workspace_id, task_id)
        result = dict(status="NOT_FOUND", task=None, events=[], links=[], reviews=[],
                      unknown_fields=[], review_flags=[], status_basis=None, field_event_ids={})
        if task is None:
            return result
        db = store._connection
        has_sources = _has_table(db, "ll_source_version")
        events = store.get_events(workspace_id, task_id)
        reviews = ReviewStorage(store).history(workspace_id, task_id) if _has_table(db, "task_review_event") else ()
        if any(authorize_source(workspace_id, ref.source_id, ref.source_version_id) is not True
               for event in events for ref in event.evidence) or any(
                   authorize_source(workspace_id, ref["source_id"], ref["source_version_id"]) is not True
                   for review in reviews if review["candidate"]
                   for ref in review["candidate"]["candidate"]["evidence"]):
            result.update(status="BLOCKED", review_flags=["SOURCE_NOT_AUTHORIZED"])
            return result
        audit_by_event = {event_id: review for review in reviews for event_id in review["task_event_ids"]}
        # Source versions can repeat across multiple fields of a single event batch.
        versions = {}
        status_basis = "WORKFLOW_DEFAULT"
        field_events = {}
        for event in events:
            item = event.to_dict()
            item["evidence"] = [_task_evidence(db, ref, versions, has_sources) for ref in event.evidence]
            audit = audit_by_event.get(event.id)
            field = event.event_type.removesuffix("_changed")
            item["review_id"] = audit["id"] if audit else None
            item["field_evidence_roles"] = {
                name: origin["evidence_role"] for name, origin in audit["field_sources"].items()
                if name == field or event.event_type == "created"
            } if audit else {}
            result["events"].append(item)
            if event.event_type == "created":
                for field in ("title", "owner", "deadline"):
                    field_events[field] = item
            elif event.event_type == "status_changed":
                status_basis = "SOURCE_UPDATE" if event.evidence else "MANUAL_UPDATE"
                field_events["status"] = item
            else:
                field_events[event.event_type.removesuffix("_changed")] = item

        flags = result["review_flags"]
        if any(ref["status"] != "VERIFIED" for item in field_events.values() for ref in item["evidence"]):
            flags.append("SOURCE_REVIEW_REQUIRED")
        if reviews and reviews[-1]["review_status"] != "approved":
            flags.append(reviews[-1]["review_status"].upper())
        result.update(
            status="UNCERTAIN" if flags else "FOUND", task=task.to_dict(),
            links=[link.to_dict() for link in store.get_task_links(workspace_id, task_id)],
            unknown_fields=[field for field in ("owner", "deadline") if getattr(task, field) is None],
            status_basis=status_basis,
            field_event_ids={field: item["id"] for field, item in field_events.items()},
            reviews=[
                {key: review[key] for key in (
                    "id", "candidate_id", "action", "review_status", "task_event_ids",
                    "reviewer", "reason", "reviewed_at",
                )} | {"extraction_conflict": bool(review["candidate"] and review["candidate"]["extraction_conflict"])}
                for review in reviews
            ],
        )
        return result


def _task_evidence(db, ref, versions, has_sources):
    key = (ref.workspace_id, ref.source_id, ref.source_version_id)
    if key not in versions:
        versions[key] = db.execute(
            """SELECT version.*, source.active, source.current_version_id
               FROM ll_source_version version JOIN ll_source source USING (workspace_id, source_id)
               WHERE version.workspace_id=? AND version.source_id=? AND version.artifact_version_id=?""",
            key,
        ).fetchone() if has_sources else None

    row = versions[key]
    status = "VERIFIED_EVIDENCE_REQUIRED"
    if row is not None and (not row["active"] or row["current_version_id"] != ref.source_version_id):
        status = "SOURCE_REVALIDATION_REQUIRED"
    elif row is not None:
        content = row["content"]
        if (hashlib.sha256(content.encode("utf-8")).hexdigest() != row["content_sha256"]
                or (ref.source_sha256 is not None and ref.source_sha256 != row["content_sha256"])):
            status = "SOURCE_HASH_MISMATCH"
        elif _task_quote_matches(ref, row):
            return dict(ref.to_dict(), source_name=row["source_name"], status="VERIFIED")
        else:
            status = "EVIDENCE_POSITION_MISMATCH"
    return dict(source_id=ref.source_id, source_version_id=ref.source_version_id, status=status)


def _task_quote_matches(ref, row):
    content = row["content"]
    if ref.char_start is not None:
        return content[ref.char_start:ref.char_end] == ref.quote and (
            ref.line_start is None or line_range_for(content, ref.char_start, ref.char_end) ==
            (ref.line_start, ref.line_end))
    if ref.line_start is not None:
        return ref.quote in "".join(content.splitlines(keepends=True)[ref.line_start - 1:ref.line_end])
    artifact = RawArtifact(workspace_id=ref.workspace_id, artifact_id=ref.source_id,
                           source_type="timestamped_text", source_uri=row["source_uri"], content=content)
    return any(span.message_id == ref.message_id and span.quote == ref.quote and span.verify(artifact)
               for segment in parse_source_text(artifact).segments if (span := segment.evidence_span))
