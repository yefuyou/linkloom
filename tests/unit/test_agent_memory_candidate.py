from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
import json

import pytest

from linkloom.agents.memory_candidate import (
    AgentDecisionClaimProposal,
    AgentMemoryBuildStatus,
    AgentMemoryResolutionContext,
    AgentRunEvidence,
    AgentRunEvidenceCatalog,
    MemoryCandidateBuilder,
    SubjectResolutionStatus,
    WorkspaceMismatchError,
    WorkspaceSubject,
    WorkspaceSubjectRegistry,
)
from linkloom.agents.memory_candidate_persistence import (
    candidate_payload_dict,
    deserialize_candidate,
    serialize_candidate,
)
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.decision_memory.models import ActionRecord, DecisionRecord
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.candidate_models import (
    CandidateDecisionFact,
    ClaimType,
    TemporalResolution,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text


WORKSPACE = "workspace:team-a"
RUN_ID = "run:2026-10-07:ask-17"
INGESTION_TIME = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
DECISION_SOURCE = (
    "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use Acme "
    "as supplier for Launch. Alice will confirm purchase order."
)


def _source(
    content: str = DECISION_SOURCE,
    *,
    workspace_id: str = WORKSPACE,
    artifact_id: str = "meeting-2026-06-01",
    ingestion_time: datetime = INGESTION_TIME,
) -> AgentRunEvidence:
    artifact = RawArtifact(
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        source_type="timestamped_text",
        content=content,
        ingestion_time=ingestion_time,
    )
    parsed = parse_timestamped_text(artifact)
    assert len(parsed.segments) == 1
    return AgentRunEvidence(artifact, parsed.segments[0])


def _result(
    evidence_ref: str | None,
    *,
    status: str = "approved",
    value: str | None = "Acme",
    actions: list[dict[str, object]] | None = None,
) -> TeamDecisionResult:
    refs = [evidence_ref] if evidence_ref is not None else []
    rationale = (
        [{"point": "The source records the supplier decision.", "evidence_refs": refs}]
        if refs
        else []
    )
    rejected = (
        [
            {
                "alternative": "Beta",
                "reason": "The source rejected this alternative.",
                "evidence_refs": refs,
            }
        ]
        if refs
        else []
    )
    unresolved = (
        [
            {
                "description": "Confirm the delivery address.",
                "owner": None,
                "deadline": None,
                "status": "pending",
                "evidence_refs": refs,
            }
        ]
        if refs
        else []
    )
    action_values = actions or []
    all_refs = list(dict.fromkeys(
        refs
        + [ref for item in action_values for ref in item["evidence_refs"]]
    ))
    return TeamDecisionResult(
        decision={"value": value, "status": status, "evidence_refs": refs},
        rationale=rationale,
        rejected_alternatives=rejected,
        actions=action_values,
        unresolved_items=unresolved,
        uncertainty={
            "status": "none",
            "statement": None,
            "unknown_fields": [],
            "evidence_refs": [],
        },
        evidence_refs=all_refs,
    )


def _environment(
    source: AgentRunEvidence | None = None,
    *,
    subjects: tuple[WorkspaceSubject, ...] | None = None,
    subject_phrase: str | None = "Launch",
    relation_phrase: str | None = "supplier",
    claim_type: ClaimType = ClaimType.DECISION,
    result: TeamDecisionResult | None = None,
    run_id: str = RUN_ID,
):
    source = source or _source()
    evidence_ref = source.evidence_ref
    result = result or _result(evidence_ref)
    registry = SourceReferenceRegistry(
        episodes={WORKSPACE: [source_episode_id_for(source.artifact)]},
        evidence={WORKSPACE: [evidence_ref]},
        evidence_hashes={WORKSPACE: {evidence_ref: source.artifact.content_hash}},
        evidence_versions={WORKSPACE: {evidence_ref: source.artifact.artifact_version_id}},
    )
    subject_entries = subjects
    if subject_entries is None:
        subject_entries = (WorkspaceSubject.create(WORKSPACE, "Launch"),)
    subject_registry = WorkspaceSubjectRegistry(WORKSPACE, subject_entries)
    proposal = AgentDecisionClaimProposal(
        claim_type=claim_type,
        subject_phrase=subject_phrase,
        relation_phrase=relation_phrase,
        evidence_refs=tuple(result.decision["evidence_refs"]),
    )
    context = AgentMemoryResolutionContext(WORKSPACE, subject_registry, proposal)
    relation_resolver = FrozenRelationResolver(
        ["supplier"], aliases={"vendor": "supplier"}, schema_version="test-relations/v1"
    )
    catalog = AgentRunEvidenceCatalog(run_id, [source])
    return (
        MemoryCandidateBuilder(),
        source,
        result,
        registry,
        relation_resolver,
        context,
        catalog,
        run_id,
    )


def _build(environment):
    builder, _source_record, result, registry, relations, context, catalog, run_id = environment
    return builder.build(
        result,
        workspace_id=WORKSPACE,
        run_id=run_id,
        evidence_catalog=catalog,
        source_registry=registry,
        relation_resolver=relations,
        resolution_context=context,
    )


def test_grounded_result_builds_non_authoritative_candidate_with_exact_provenance():
    environment = _environment()
    built = _build(environment)

    assert built.status is AgentMemoryBuildStatus.CANDIDATE
    candidate = built.candidate
    assert candidate is not None
    assert candidate.decision_value == "Acme"
    assert candidate.subject_resolution.status is SubjectResolutionStatus.RESOLVED_EXISTING_SUBJECT
    assert candidate.relation_resolution.canonical_relation == "supplier"
    assert candidate.effective_time == date(2026, 6, 1)
    assert candidate.temporal_resolution is TemporalResolution.EXPLICIT
    assert candidate.authority == "NON_AUTHORITATIVE"
    assert candidate.authorization_status == "NOT_AUTHORIZED"
    assert candidate.evidence[0].provenance.verify(environment[1].artifact)
    assert candidate.source_episode_ids == (source_episode_id_for(environment[1].artifact),)
    snapshot = json.loads(candidate.team_decision_result_json)
    assert snapshot["rationale"][0]["point"] == "The source records the supplier decision."
    assert snapshot["rejected_alternatives"][0]["alternative"] == "Beta"
    assert snapshot["unresolved_items"][0]["description"] == "Confirm the delivery address."


def test_legacy_candidate_payload_is_readable_but_new_writes_use_v2():
    built = _build(_environment())
    candidate = built.candidate
    assert candidate is not None
    evidence = candidate.evidence[0]
    span = evidence.provenance.semantic_span
    assert span is not None and evidence.event_time is not None

    payload = candidate_payload_dict(candidate)
    payload["record_version"] = "agent-memory-candidate/v1"
    payload["candidate"]["evidence"] = [
        {
            "evidence_ref": evidence.evidence_ref,
            "source_episode_id": evidence.source_episode_id,
            "artifact_id": evidence.artifact_id,
            "artifact_version_id": evidence.artifact_version_id,
            "source_ref": evidence.source_ref,
            "content_sha256": evidence.content_sha256,
            "provenance": {
                "workspace_id": span.workspace_id,
                "artifact_id": span.artifact_id,
                "artifact_version_id": span.artifact_version_id,
                "source_ref": span.source_ref,
                "content_hash": span.content_hash,
                "ordinal": span.ordinal,
                "message_id": span.message_id,
                "segment_id": span.segment_id,
                "evidence_ref": span.evidence_ref,
                "char_start": span.char_start,
                "char_end": span.char_end,
                "line_start": span.line_start,
                "line_end": span.line_end,
                "quote": span.quote,
                "quote_sha256": span.quote_sha256,
            },
            "source_speaker": evidence.source_speaker,
            "event_time": evidence.event_time.isoformat(),
            "claim_roles": list(evidence.claim_roles),
        }
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    row = {
        "candidate_source_kind": "AGENT_RESULT",
        "payload_version": "agent-memory-candidate/v1",
        "payload_json": encoded,
        "validation_state": "ACCEPTED",
        "validation_reasons_json": "[]",
    }

    from linkloom.semantic_ingestion.materialization import _candidate_view_from_row

    restored, view, stored_payload, stored_fingerprint = _candidate_view_from_row(row)
    assert restored.evidence[0].provenance.source_type == "legacy_semantic_ingestion"
    assert view.evidence[0].source_type == "legacy_semantic_ingestion"
    assert stored_payload == encoded
    assert stored_fingerprint == hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    new_payload, _fingerprint = serialize_candidate(restored)
    assert json.loads(new_payload)["record_version"] == "agent-memory-candidate/v2"


def test_confirmed_subject_alias_resolves_to_same_canonical_identity_and_slot():
    canonical = WorkspaceSubject.create(
        WORKSPACE,
        "Launch",
        confirmed_aliases=("Project Aurora",),
    )
    canonical_registry = WorkspaceSubjectRegistry(WORKSPACE, (canonical,))
    results = []
    for source_phrase, subject_phrase in (("Launch", "Launch"), ("Project Aurora", "Project Aurora")):
        source = _source(
            f"[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
            f"Acme as supplier for {source_phrase}."
        )
        env = _environment(source, subjects=(canonical,), subject_phrase=subject_phrase)
        results.append(_build(env).candidate)

    assert results[0] is not None and results[1] is not None
    assert results[0].subject_id == results[1].subject_id == canonical.subject_id
    assert results[0].subject_key == results[1].subject_key
    assert canonical_registry.resolve("Project Aurora").status is SubjectResolutionStatus.RESOLVED_EXISTING_SUBJECT


def test_frozen_relation_alias_is_reused_without_catalog_mutation():
    source = _source(
        "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
        "Acme as vendor for Launch."
    )
    environment = _environment(source, relation_phrase="vendor")
    resolver = environment[4]
    before = (resolver.canonical_relations, dict(resolver.aliases), resolver.fingerprint)

    built = _build(environment)

    assert built.candidate is not None
    assert built.candidate.relation_resolution.canonical_relation == "supplier"
    assert (resolver.canonical_relations, dict(resolver.aliases), resolver.fingerprint) == before


def test_unresolved_relation_stays_reviewable():
    environment = _environment(relation_phrase="supplier for")

    built = _build(environment)

    assert built.status is AgentMemoryBuildStatus.UNRESOLVED
    assert built.candidate is not None
    assert built.candidate.subject_key is None
    assert built.candidate.relation_resolution.status.value == "NEW_RELATION_CANDIDATE"
    assert "RELATION_UNRESOLVED" in {reason.value for reason in built.reason_codes}


@pytest.mark.parametrize(
    ("subjects", "phrase", "expected"),
    [
        ((), "Launch", SubjectResolutionStatus.NEW_SUBJECT_CANDIDATE),
        (
            (
                WorkspaceSubject.create(WORKSPACE, "Launch One", confirmed_aliases=("Launch",)),
                WorkspaceSubject.create(WORKSPACE, "Launch Two", confirmed_aliases=("Launch",)),
            ),
            "Launch",
            SubjectResolutionStatus.AMBIGUOUS_SUBJECT,
        ),
    ],
)
def test_unknown_or_ambiguous_subject_remains_reviewable(
    subjects: tuple[WorkspaceSubject, ...],
    phrase: str,
    expected: SubjectResolutionStatus,
):
    built = _build(_environment(subjects=subjects, subject_phrase=phrase))

    assert built.status is AgentMemoryBuildStatus.UNRESOLVED
    assert built.candidate is not None
    assert built.candidate.subject_resolution.status is expected
    assert built.candidate.subject_id is None
    assert built.candidate.subject_key is None


def test_october_run_preserves_june_effective_time_from_source_evidence():
    built = _build(_environment(run_id="run:2026-10-07:late-question"))

    assert built.candidate is not None
    assert built.candidate.effective_time == date(2026, 6, 1)
    assert built.candidate.effective_time != date(2026, 10, 7)


def test_permitted_source_event_time_rule_uses_evidence_timestamp():
    source = _source(
        "[2026-06-01T09:00:00Z] Alice: We decided to use Acme as supplier for Launch.",
        artifact_id="source-event-time",
    )

    built = _build(_environment(source))

    assert built.candidate is not None
    assert built.candidate.effective_time == datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    assert built.candidate.temporal_resolution is TemporalResolution.SOURCE_EVENT_TIME


def test_missing_source_effective_time_stays_unresolved_instead_of_using_run_time():
    source = _source(
        "[2026-10-07T09:00:00Z] Alice: Launch supplier is Acme.",
        artifact_id="no-effective-time",
    )
    built = _build(_environment(source))

    assert built.status is AgentMemoryBuildStatus.UNRESOLVED
    assert built.candidate is not None
    assert built.candidate.effective_time is None
    assert built.candidate.temporal_resolution is TemporalResolution.UNRESOLVED


def test_decision_value_not_present_in_its_source_evidence_stays_unresolved():
    source = _source()
    result = _result(source.evidence_ref, value="Globex")

    built = _build(_environment(source, result=result))

    assert built.status is AgentMemoryBuildStatus.UNRESOLVED
    assert built.candidate is not None
    assert "DECISION_VALUE_UNGROUNDED" in {reason.value for reason in built.reason_codes}


def test_unbound_evidence_ref_fails_closed():
    source = _source()
    result = _result("evidence:not-in-run-catalog")
    environment = _environment(source, result=result)

    built = _build(environment)

    assert built.status is AgentMemoryBuildStatus.BLOCKED
    assert built.candidate is None
    assert built.reason_codes[0].value == "EVIDENCE_REF_UNBOUND"


def test_source_reference_registry_hash_mismatch_fails_closed():
    environment = _environment()
    builder, source, result, _registry, relations, context, catalog, run_id = environment
    mismatched_registry = SourceReferenceRegistry(
        episodes={WORKSPACE: [source_episode_id_for(source.artifact)]},
        evidence={WORKSPACE: [source.evidence_ref]},
        evidence_hashes={WORKSPACE: {source.evidence_ref: "0" * 64}},
        evidence_versions={
            WORKSPACE: {source.evidence_ref: source.artifact.artifact_version_id}
        },
    )

    built = builder.build(
        result,
        workspace_id=WORKSPACE,
        run_id=run_id,
        evidence_catalog=catalog,
        source_registry=mismatched_registry,
        relation_resolver=relations,
        resolution_context=context,
    )

    assert built.status is AgentMemoryBuildStatus.BLOCKED
    assert built.candidate is None
    assert built.reason_codes[0].value == "SOURCE_NOT_REGISTERED"


def test_missing_action_owner_is_preserved_for_review_without_action_record_mapping():
    source = _source()
    result = _result(
        source.evidence_ref,
        actions=[
            {
                "description": "Alice will confirm purchase order.",
                "owner": None,
                "deadline": None,
                "status": "pending",
                "evidence_refs": [source.evidence_ref],
            }
        ],
    )
    built = _build(_environment(source, result=result))

    assert built.status is AgentMemoryBuildStatus.UNRESOLVED
    assert built.candidate is not None
    action = built.candidate.actions[0]
    assert action.owner is None
    assert action.requires_review
    assert not isinstance(action, ActionRecord)
    assert "ACTION_OWNER_MISSING" in {reason.value for reason in built.reason_codes}


@pytest.mark.parametrize("status", ["insufficient_evidence", "not_found"])
def test_insufficient_or_not_found_agent_result_does_not_build_decision_candidate(status: str):
    result = _result(None, status=status, value=None)
    source = _source()
    environment = _environment(source, result=result)

    built = _build(environment)

    assert built.status is AgentMemoryBuildStatus.BLOCKED
    assert built.candidate is None


def test_proposal_claim_is_not_treated_as_a_decision_candidate():
    environment = _environment(claim_type=ClaimType.PROPOSAL)

    built = _build(environment)

    assert built.status is AgentMemoryBuildStatus.BLOCKED
    assert built.candidate is None
    assert built.reason_codes[0].value == "NON_DECISION_CLAIM"


def test_workspace_mismatch_is_a_hard_rejection():
    environment = _environment()
    builder, _source_record, result, registry, relations, _context, catalog, run_id = environment
    other_registry = WorkspaceSubjectRegistry("workspace:other", ())
    other_context = AgentMemoryResolutionContext("workspace:other", other_registry)

    with pytest.raises(WorkspaceMismatchError):
        builder.build(
            result,
            workspace_id=WORKSPACE,
            run_id=run_id,
            evidence_catalog=catalog,
            source_registry=registry,
            relation_resolver=relations,
            resolution_context=other_context,
        )


def test_source_evidence_from_another_workspace_is_rejected():
    source = _source(workspace_id="workspace:other")
    env = _environment(source)
    builder, _source_record, result, registry, relations, context, catalog, run_id = env

    with pytest.raises(WorkspaceMismatchError):
        builder.build(
            result,
            workspace_id=WORKSPACE,
            run_id=run_id,
            evidence_catalog=catalog,
            source_registry=registry,
            relation_resolver=relations,
            resolution_context=context,
        )


def test_evidence_catalog_is_bound_to_the_requested_run():
    environment = _environment()
    builder, _source_record, result, registry, relations, context, catalog, _run_id = environment

    built = builder.build(
        result,
        workspace_id=WORKSPACE,
        run_id="run:other",
        evidence_catalog=catalog,
        source_registry=registry,
        relation_resolver=relations,
        resolution_context=context,
    )

    assert built.status is AgentMemoryBuildStatus.BLOCKED
    assert built.reason_codes[0].value == "RUN_EVIDENCE_SCOPE_INVALID"


def test_candidate_fingerprint_is_stable_across_replay_and_ignores_ingestion_clock():
    first = _build(_environment())
    source = _source(ingestion_time=datetime(2026, 10, 7, 19, 30, tzinfo=UTC))
    replay = _build(_environment(source))

    assert first.candidate is not None and replay.candidate is not None
    assert first.candidate.candidate_id == replay.candidate.candidate_id
    assert first.candidate.fingerprint == replay.candidate.fingerprint


def test_builder_does_not_create_authoritative_decision_or_action_records():
    environment = _environment()
    with TemporalDecisionStore(":memory:") as store:
        before = store._connection.execute("SELECT COUNT(*) FROM decision").fetchone()[0]
        built = _build(environment)
        after = store._connection.execute("SELECT COUNT(*) FROM decision").fetchone()[0]

    assert built.candidate is not None
    assert after == before == 0
    assert not isinstance(built.candidate, DecisionRecord)
    assert not isinstance(built.candidate, CandidateDecisionFact)
    assert all(not isinstance(action, ActionRecord) for action in built.candidate.actions)
    assert built.candidate.authorization_status == "NOT_AUTHORIZED"
