from datetime import UTC, datetime
import hashlib
import sqlite3

import pytest

from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.models import DecisionRecord, DecisionStatus, DecisionTimeBasis, decision_slot_key_for
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.evolution import decision_evolution, task_evolution
from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tasks.models import EvidenceReference, TaskCandidateInput
from linkloom.tasks.store import TaskStore
from linkloom.ui.product import ProductRepository


def at(day):
    return datetime(2026, 1, day, tzinfo=UTC)


def allow(*_):
    return True


@pytest.fixture
def catalogue(tmp_path):
    path = tmp_path / 'product.sqlite'
    repo = ProductRepository(path)
    workspace = repo.create_workspace('Test workspace')['workspace_id']
    yield path, repo, workspace
    repo._connection.close()


def source(repo, workspace, name, text):
    artifact = RawArtifact(workspace, name, 'timestamped_text', text)
    span = parse_source_text(artifact).segments[0].evidence_span
    repo.create_source(workspace, name, name, artifact, (span,))
    repo.set_ingestion_result(workspace, name, artifact.artifact_version_id,
                              status='COMPLETED', candidate_count=0, review_required_count=0)
    return artifact, span


def dated(store, artifact, span, day, value, previous=None):
    registry = store.source_registry
    episode = source_episode_id_for(artifact)
    registry.register_episode(artifact.workspace_id, episode)
    registry.register_evidence(artifact.workspace_id, span.evidence_ref, content_hash=span.content_hash)
    record = DecisionRecord(
        f'decision-{day}', artifact.workspace_id, decision_slot_key_for('Vendor', 'payment'), value,
        DecisionStatus.CURRENT, at(day), None, previous, episode, (span.evidence_ref,), 'run',
        subject='Vendor', relation='payment',
    )
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(record, team_decision_contract_pass=True, grounding_pass=True)
    return materializer.materialize(candidate, approved_by='reviewer')


def decision(catalogue, **options):
    path, _, workspace = catalogue
    authorize_source = options.pop('authorize_source', allow)
    return decision_evolution(path, workspace_id=workspace, authorize_source=authorize_source,
                              subject='VENDOR', relation='payment', **options)


def test_undated_changes_current_qualification_and_retains_identity_on_promotion(catalogue):
    path, repo, workspace = catalogue
    a, first = source(repo, workspace, 'first', 'Use Birchline.')
    b, second = source(repo, workspace, 'second', 'Use Wrenwell.')
    with TemporalDecisionStore(path) as store:
        prior = dated(store, a, first, 1, 'Birchline')
        episode = source_episode_id_for(b)
        store.source_registry.register_episode(workspace, episode)
        store.source_registry.register_evidence(workspace, second.evidence_ref, content_hash=second.content_hash)
        unknown, _ = store.confirm_undated_decision(
            workspace_id=workspace, subject='Vendor', relation='payment', value='Wrenwell',
            source_episode_id=episode, source_evidence_refs=(second.evidence_ref,), provenance_run_id='run',
            authorization_id='reviewer', authorization_reason='Meeting confirmed value',
            supersedes_decision_id=prior.decision_id,
        )
        current = decision(catalogue)
        assert (current['status'], current['decision'], current['reasons']) == (
            'UNCERTAIN', None, ['EFFECTIVE_TIME_UNKNOWN'],
        )
        view = decision(catalogue, mode='AS_OF', as_of=at(5).isoformat())
        assert view['decision']['value'] == 'Birchline'
        assert view['undated'][0]['valid_from'] is None
        assert view['undated'][0]['evidence'][0]['quote'] == 'Use Wrenwell.'
        assert decision(catalogue, mode='UNDATED')['undated'][0]['decision_ref'] == unknown.decision_ref
        promoted, _, receipt = store.promote_undated_decision_to_dated(
            workspace_id=workspace, undated_decision_id=unknown.undated_decision_id, valid_from=at(8),
            time_basis=DecisionTimeBasis.USER_SELECTED, authorization_id='reviewer',
            authorization_reason='Business effective date confirmed',
        )
    at_boundary = decision(catalogue, mode='AS_OF', as_of=at(8).isoformat())['decision']
    assert at_boundary['decision_id'] == promoted.decision_id
    assert at_boundary['supersedes_id'] == prior.decision_id
    assert at_boundary['decision_ref'] == unknown.decision_ref
    assert receipt.receipt_id in {item['receipt_id'] for item in at_boundary['receipts']}
    history = decision(catalogue, mode='HISTORY')
    assert [item['value'] for item in history['dated']] == ['Birchline', 'Wrenwell']
    assert history['undated'][0]['memory_state'] == 'PROMOTED_TO_DATED'


def test_revoked_source_cannot_restore_predecessor(catalogue):
    path, repo, workspace = catalogue
    a, first = source(repo, workspace, 'first', 'Use Birchline.')
    b, second = source(repo, workspace, 'second', 'Use Wrenwell.')
    with TemporalDecisionStore(path) as store:
        prior = dated(store, a, first, 1, 'Birchline')
        dated(store, b, second, 8, 'Wrenwell', prior.decision_id)
    repo.delete_source(workspace, b.artifact_id)

    current = decision(catalogue)
    assert current['status'] == 'UNCERTAIN'
    assert current['decision'] is None
    assert [(item['value'], item['evidence'][0]['status']) for item in current['dated']] == [
        ('Wrenwell', 'SOURCE_REVALIDATION_REQUIRED'),
    ]
    assert decision(catalogue, mode='AS_OF', as_of=at(7).isoformat())['decision']['value'] == 'Birchline'


def test_overlapping_legacy_slots_return_conflict_and_deny_source_quote(catalogue):
    path, repo, workspace = catalogue
    a, first = source(repo, workspace, 'first', 'Use Birchline.')
    b, second = source(repo, workspace, 'second', 'Use Wrenwell.')
    with TemporalDecisionStore(path) as store:
        prior = dated(store, a, first, 1, 'Birchline')
        # Legacy slot identity is separate from the newer normalized exact lookup.
        with sqlite3.connect(path) as connection:
            connection.execute('UPDATE decision SET subject_key=? WHERE decision_id=?',
                               ('legacy-vendor-slot', prior.decision_id))
        dated(store, b, second, 8, 'Wrenwell')
    current = decision(catalogue)
    assert current['status'] == 'CONFLICT'
    assert {item['evidence'][0]['quote'] for item in current['dated']} == {'Use Birchline.', 'Use Wrenwell.'}
    denied = decision(catalogue, authorize_source=lambda _, source_id, version: source_id != 'second')
    assert denied['status'] == 'UNCERTAIN'
    assert denied['dated'][-1]['value'] is None
    assert 'quote' not in denied['dated'][-1]['evidence'][0]


def task_candidate(repo, workspace, name, text, **fields):
    artifact, span = source(repo, workspace, name, text)
    # MeetingTaskReview can persist an exact subspan of the registered segment.
    quote = span.quote.split(';')[0]
    evidence = EvidenceReference(
        workspace, name, artifact.artifact_version_id, quote, hashlib.sha256(quote.encode()).hexdigest(),
        char_start=span.char_start, char_end=span.char_start + len(quote),
        line_start=1, line_end=1, source_sha256=artifact.content_hash,
    )
    return TaskCandidateInput(name, workspace, 'Vendor integration', (evidence,), **fields)


def test_formal_task_events_keep_source_review_and_unknown_dates(catalogue):
    path, repo, workspace = catalogue
    first = task_candidate(repo, workspace, 'first', 'Ada owns integration; keep notes.', owner='Ada')
    second = task_candidate(repo, workspace, 'second', 'Bo owns integration by January 20.',
                            owner='Bo', deadline='2026-01-20', meeting_time=at(8))
    third = task_candidate(repo, workspace, 'third', 'Bo remains the integration owner.', owner='Bo')
    with TaskStore(path) as store:
        task = store.create_from_candidate(workspace, first, reviewer='R1', reason='Create', now=at(5))
        store.merge_candidate(workspace, task.id, second, apply_fields=['owner', 'deadline'],
                              reviewer='R2', reason='Approved revisions', now=at(9))
        store.merge_candidate(workspace, task.id, third, apply_fields=['owner'],
                              reviewer='R3', reason='Link only', now=at(10))
        store.set_status(workspace, task.id, 'done', actor='R4', reason='Confirmed completion', now=at(11))
    before = path.read_bytes()
    view = task_evolution(path, workspace_id=workspace, authorize_source=allow, task_id=task.id)
    assert path.read_bytes() == before
    assert view['status'] == 'FOUND' and view['unknown_fields'] == []
    assert (view['task']['owner'], view['task']['deadline'], view['task']['status']) == ('Bo', '2026-01-20', 'done')
    created, owner, deadline, status = view['events']
    assert created['meeting_time'] is None and created['new_value']['deadline'] is None
    assert owner['old_value'] == 'Ada' and owner['new_value'] == 'Bo'
    assert owner['meeting_time'] == at(8).isoformat() and owner['actor'] == 'R2'
    assert deadline['evidence'][0]['source_id'] == 'second' and deadline['evidence'][0]['status'] == 'VERIFIED'
    assert status['meeting_time'] is None and status['evidence'] == [] and view['status_basis'] == 'MANUAL_UPDATE'
    assert [link['observation_id'] for link in view['links']] == ['first', 'second', 'third']
    assert task_evolution(path, workspace_id='other-workspace', authorize_source=allow, task_id=task.id)['status'] == 'NOT_FOUND'


def test_task_source_invalidation_preserves_store_state_without_citing_withdrawn_text(catalogue):
    path, repo, workspace = catalogue
    first = task_candidate(repo, workspace, 'first', 'Integration needs an owner.')
    with TaskStore(path) as store:
        task = store.create_from_candidate(workspace, first, reviewer='R1', reason='Create', now=at(5))
    repo.delete_source(workspace, 'first')
    stale = task_evolution(path, workspace_id=workspace, authorize_source=allow, task_id=task.id)
    assert (stale['status'], stale['unknown_fields'], stale['status_basis']) == (
        'UNCERTAIN', ['owner', 'deadline'], 'WORKFLOW_DEFAULT',
    )
    assert stale['events'][0]['evidence'][0]['status'] == 'SOURCE_REVALIDATION_REQUIRED'
    assert 'quote' not in stale['events'][0]['evidence'][0]
    denied = task_evolution(path, workspace_id=workspace, authorize_source=lambda *_: False, task_id=task.id)
    assert denied['status'] == 'BLOCKED' and denied['task'] is None and denied['events'] == []


def test_existing_task_review_receipt_distinguishes_human_value_from_source_context(tmp_path):
    from tests.unit.test_task_legacy_adapter import close, seed
    from tests.unit.test_task_review import confirm

    db = tmp_path / 'reviewed.sqlite'
    app = seed(db)
    try:
        reviewed = confirm(app, patch={'owner': 'Alternate owner'})
        before = db.read_bytes()
        result = task_evolution(db, workspace_id=app.workspace, authorize_source=allow,
                                task_id=reviewed['task']['id'])
        assert result['status'] == 'FOUND'
        assert result['task']['owner'] == 'Alternate owner'
        assert result['events'][0]['review_id'] == result['reviews'][0]['id']
        assert result['events'][0]['field_evidence_roles']['owner'] == 'context'
        assert result['events'][0]['field_evidence_roles']['title'] == 'source'
        assert db.read_bytes() == before
    finally:
        close(app)
