"""Recorded failures stay failures; product authority is tested independently."""
from dataclasses import replace
import hashlib
import json
import socket
import zipfile

import pytest

from linkloom.agents.model_adapter import ModelUsage
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.semantic_ingestion.task_observation import TaskObservation, TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticResult
from linkloom.semantic_ingestion.task_relation_semantic_proposer import _decode
from linkloom.semantic_ingestion.task_relation_semantic_validation import validate_proposals
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.tools.contracts import ToolCall
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy, _proposal_view
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from tests.m2_real_failure_support import ARCHIVE, read_json, recorded, real_context, typed_variant
from tests.unit.test_task_relation_semantic import FakeModel


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline regression attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)


def decoded(context, record, verdict, **kwargs):
    proposals = _decode(typed_variant(context, record, verdict, **kwargs), context,
                        'offline-adapted-record', None, ModelUsage())
    return SemanticResult('EXECUTED', proposals)


def test_frozen_wire_responses_references_and_79_exact_native_quotes():
    manifest = read_json('manifest.json')
    assert len(manifest['calls']) == 18 and manifest['new_provider_calls'] == 0
    quote_count = 0
    with zipfile.ZipFile(ARCHIVE) as archive:
        assert manifest['raw_streams_included'] is False
        assert not any('/stream/' in name for name in archive.namelist())
        for path, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read(path)).hexdigest() == digest, path
        for path in manifest['calls']:
            case_id = read_json(path + '/proposal.json')['case_id']
            case = read_json('inputs/' + case_id + '.json')
            wire = read_json(path + '/wire.body.json')
            assert json.loads(wire['contents'][0]['parts'][0]['text']) == case
            assert 'rationale' not in wire['contents'][0]['parts'][0]['text']
            response = read_json(path + '/assembled.response.json')
            proposal = read_json(path + '/proposal.json')
            assert json.loads(response['text']) == proposal
            utterances = {(s['source_id'], s['version'], u['utterance_id']): u['text']
                          for s in case['allowed_sources'] for u in s.get('utterances', [])}
            for citation in proposal['evidence'] + proposal['counterevidence']:
                assert citation['quote'] in utterances[(citation['source_id'], citation['version'], citation['utterance_id'])]
                quote_count += 1
    assert quote_count == 79


def test_real_failures_and_reference_are_preserved_without_rescoring():
    reference = read_json('evaluator/reference.pre_model.json')
    assert reference['status'] == 'PROVISIONAL_ANALYST_REFERENCE_NOT_GOLD'
    expected = {r['case_id']: r['acceptable_primary_types'] for r in reference['cases']}
    none = recorded('R07', 'gemini-4-argon')
    assert none['relation_type'] == 'NONE' and expected['R07'] == ['UNKNOWN']
    assert {'identity', 'owner', 'scope'} <= set(none['uncertain_fields'])
    revision = recorded('R02', 'gemini-3.8-flash')
    assert revision['relation_type'] == 'REVISES_PLAN' and revision['requires_review'] is False
    assert expected['R02'] == ['REVOKED']
    for model in ('gemini-3.8-flash', 'gemini-4-argon'):
        assert all(recorded(case_id, model)['relation_type'] == 'UNKNOWN' for case_id in ('R08', 'R09'))


@pytest.mark.parametrize('case_id,model', [('R07', 'gemini-4-argon'), ('R02', 'gemini-3.8-flash'),
                                         ('R02', 'gemini-4-argon'), ('R05', 'gemini-3.8-flash')])
def test_original_research_schema_is_not_a_product_relation_receipt(case_id, model):
    with pytest.raises(ValueError, match='INVALID_ROOT_FIELDS'):
        _decode(recorded(case_id, model), real_context(case_id), model, None, ModelUsage())


def test_wrong_negative_with_exact_quotes_cannot_exclude_task_identity(tmp_path):
    context = real_context('R07')
    result = decoded(context, recorded('R07', 'gemini-4-argon'), 'REFUTED')
    report, = validate_proposals(result, context)
    assert report.stage == 'EVIDENCE_VALID' and report.semantic_status == 'NOT_EVALUATED'
    row, = _proposal_view(result, context)
    assert row['displayable_as_model_analysis'] and not row['publishable']
    assert row['release_status'] == 'PENDING_SEMANTIC_REVIEW'
    args = dict(artifacts=context.artifacts, source_registry=context.source_registry)
    identity = assess_same_task(*context.observations, **args)
    assert identity.verdict == 'UNKNOWN' and identity.missing_evidence
    with TaskRelationStore(tmp_path / 'relations.sqlite') as store:
        changes = store._connection.total_changes
        with pytest.raises(ValueError, match='expected RelationAssessment'):
            store.put_assessment(result.proposals[0])
        assert store._connection.total_changes == changes
    for verdict in ('NONE', 'REVOKED'):
        args = typed_variant(context, recorded('R07', 'gemini-4-argon'), verdict)
        with pytest.raises(ValueError, match='INVALID_TYPED_RELATION'):
            _decode(args, context, 'offline', None, ModelUsage())


def test_wrong_revision_and_review_false_cannot_change_plan_state():
    context = real_context('R02', 'REVISES_PLAN')
    record = recorded('R02', 'gemini-3.8-flash')
    original_counter = decoded(context, record, 'SUPPORTED')
    assert 'VERDICT_EVIDENCE_POLARITY_MISMATCH' in validate_proposals(original_counter, context)[1].errors
    # More permissive adversarial variation: even omission of counter-evidence
    # and a fake positive identity premise never grant semantic acceptance.
    variation = decoded(context, record, 'SUPPORTED', keep_counter=False)
    reports = validate_proposals(variation, context)
    assert all(r.stage == 'EVIDENCE_VALID' and not r.publishable for r in reports)
    rows = _proposal_view(variation, context)
    assert all(r['stage'] == 'MODEL_PROPOSED' and not r['publishable'] for r in rows)
    assert rows[1]['comparison']['delta_calendar_days'] is None
    args = dict(artifacts=context.artifacts, source_registry=context.source_registry)
    old, new = context.observations
    identity = assess_same_task(new, old, **args)
    assert assess_plan_revision(new, old, same_task=identity, **args).verdict == 'UNKNOWN'


def test_real_scope_revision_keeps_direction_identity_and_time_separate():
    context = real_context('R04', 'REVISES_PLAN')
    record = recorded('R04', 'gemini-4-argon')
    assert record['direction'] == 'B_TO_A'
    result = decoded(context, record, 'SUPPORTED', keep_counter=False)
    assert all(not row['publishable'] for row in _proposal_view(result, context))
    old, new = context.observations
    args = dict(artifacts=context.artifacts, source_registry=context.source_registry)
    identity = assess_same_task(new, old, **args)
    assert identity.verdict == 'UNKNOWN'  # finite P4 proof does not certify the English bridge
    proof = assess_plan_revision(new, old, same_task=identity, **args)
    assert proof.verdict == 'UNKNOWN' and proof.asserted_effective_at is None
    assert all(o.event_time is None and o.deadline is None for o in (old, new))
    swapped = typed_variant(context, record, 'SUPPORTED', keep_counter=False)
    swapped['proposals'][1].update(subject='A', object='B')
    with pytest.raises(ValueError, match='MISSING_REQUESTED_RELATION'):
        _decode(swapped, context, 'offline', None, ModelUsage())


def test_r06_conflict_is_not_an_authorized_restoration_or_cross_meeting_success():
    case = read_json('inputs/R06.json')
    assert len({o['source_id'] for o in case['observations']}) == 1
    for model in ('gemini-3.8-flash', 'gemini-4-argon'):
        record = recorded('R06', model)
        assert record['relation_type'] == 'CONFLICT'
        context = real_context('R06')
        # Test-only competing-claims verdict on an existing typed query; this
        # does not implement the research CONFLICT predicate or business cancel.
        result = decoded(context, record, 'CONFLICT')
        assert all(not row['publishable'] for row in _proposal_view(result, context))
        invalid = typed_variant(context, record, 'CONFLICT')
        invalid['proposals'][0]['predicate'] = 'REVOKED'
        with pytest.raises(ValueError, match='INVALID_TYPED_RELATION'):
            _decode(invalid, context, 'offline', None, ModelUsage())


@pytest.mark.parametrize('case_id', ['R01', 'R03', 'R05', 'R07'])
def test_isolated_you_cannot_supply_a_verified_owner_or_completion(case_id):
    context = real_context(case_id)
    for observation in context.observations:
        artifact = next(a for a in context.artifacts if a.artifact_version_id == observation.provenance.artifact_version_id)
        from linkloom.semantic_ingestion.document_text import parse_source_text
        segment, = parse_source_text(artifact).segments
        attempted = build_task_observation(TaskProposal(observation.raw_text, (observation.raw_text,), owner='C'), segment, artifact)
        assert attempted.owner is None and 'owner' in attempted.unknown_fields
        assert attempted.authority == 'SUPPORTING_ONLY' and attempted.relation_status == 'UNCONFIRMED'
        assert attempted.event_time is None
        with pytest.raises(ValueError, match='unsupported fields'):
            TaskObservation.from_dict(dict(attempted.to_dict(), business_status='COMPLETED'))


@pytest.mark.parametrize('change', ['permission', 'version', 'observation'])
def test_real_quote_variant_downgrades_with_current_evidence(change):
    context = real_context('R07')
    result = decoded(context, recorded('R07', 'gemini-4-argon'), 'REFUTED')
    span = context.allowed_spans[-1][1]
    if change == 'permission':
        context.source_registry.unregister_evidence(context.workspace_id, span.evidence_ref)
    elif change == 'version':
        context.source_registry.register_evidence(context.workspace_id, span.evidence_ref,
            content_hash=span.content_hash, source_version='current-version-unavailable')
    else:
        context = replace(context, revoked_observation_ids=frozenset([context.observations[-1].observation_id]))
    assert all(r.stage == 'EVIDENCE_INVALID' for r in validate_proposals(result, context))
    assert all(not r['displayable_as_model_analysis'] and not r['publishable'] for r in _proposal_view(result, context))


def test_durable_semantic_receipt_never_promotes_wrong_negative_and_cold_read_rechecks(tmp_path):
    context = real_context('R07')
    snapshot = SemanticSourceSnapshot.from_context(context)
    fake = FakeModel(typed_variant(context, recorded('R07', 'gemini-4-argon'), 'REFUTED'))
    service = SemanticAnalysis('real-failure-variant', context.workspace_id, lambda: snapshot, fake,
        model_id='gemini-3.8-flash', policy=SemanticCallPolicy('FAKE'))
    artifacts = ModelArtifactStore(tmp_path)
    bound = service.bind('offline-run', artifacts, context.question)
    call = ToolCall('call', 'analyze_task_semantics', dict(workspace_id=context.workspace_id,
        investigation_id='real-failure-variant'), run_id='offline-run')
    view = bound.execute(call)
    assert view['analysis_status'] == 'EXECUTED', view
    assert view['proposals'][0]['release_status'] == 'PENDING_SEMANTIC_REVIEW'
    assert view['publishable'] is False and view['execution']['new_live_provider_requests'] == 0
    assert bound.review(view, call_id='call')['status'] == 'pass'
    # A new service has no model; receipt validation/cold reads cannot rebill.
    cold = SemanticAnalysis('real-failure-variant', context.workspace_id, lambda: snapshot, None,
        model_id='gemini-3.8-flash', policy=SemanticCallPolicy('FAKE')).bind('offline-run', artifacts, context.question)
    cached = cold.execute(call)
    assert cached['cache_hit'] and cold.review(cached, call_id='call')['status'] == 'pass'
    assert len(fake.requests) == 1
    span = context.allowed_spans[-1][1]
    context.source_registry.unregister_evidence(context.workspace_id, span.evidence_ref)
    assert cold.review(cached, call_id='call')['status'] == 'fail'
    assert cold.execute(call)['analysis_status'] == 'BLOCKED'
    assert len(fake.requests) == 1
