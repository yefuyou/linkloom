from dataclasses import replace

import pytest

from linkloom.experience.store import ExperienceStore
from linkloom.strategy.generation import StrategyGenerator, validate_sources
from tests.experience_authority_support import fixture_authority, fixture_candidate, fixture_reviewed
from tests.strategy_support import strategy_root, accepted_source


def test_existing_accepted_experience_produces_separate_candidate(accepted_source):
    store, source = accepted_source
    candidate, = StrategyGenerator(store).generate()
    assert candidate.status == "candidate"
    assert candidate.source_experience_ids == (source.experience_id,)
    assert candidate.source_review_receipts[0].review_event_id == source.review_transition.review_event_id
    assert candidate.behavioral_rule != source.reusable_lesson
    validate_sources(candidate, store)


@pytest.mark.parametrize("status", ["candidate", "rejected"])
def test_unaccepted_sources_do_not_generate(strategy_root, status):
    auth = fixture_authority()
    store = ExperienceStore(strategy_root / "unaccepted.jsonl", authority=auth)
    if status == "candidate":
        store.save(fixture_candidate(auth))
    else:
        fixture_reviewed(store, auth, decision="rejected")
    assert StrategyGenerator(store).generate() == ()


def test_empty_evidence_abstains_and_orphan_id_rejects(strategy_root):
    store = ExperienceStore(strategy_root / "empty.jsonl", authority=fixture_authority())
    assert StrategyGenerator(store).generate() == ()
    with pytest.raises(ValueError):
        StrategyGenerator(store).generate(("f" * 64,))


def test_foreign_store_and_recomputed_receipt_are_not_authority(accepted_source, strategy_root):
    store, _ = accepted_source
    candidate, = StrategyGenerator(store).generate()
    foreign = ExperienceStore(strategy_root / "foreign.jsonl", authority=fixture_authority())
    with pytest.raises(ValueError):
        validate_sources(candidate, foreign)
    from linkloom.strategy.models import StrategyCandidate
    forged = StrategyCandidate.create(source_review_receipts=(replace(candidate.source_review_receipts[0], review_event_id="f" * 64),),
                                      created_at=candidate.created_at)
    with pytest.raises(ValueError):
        validate_sources(forged, store)


def test_removed_durable_review_rejects_stale_accepted_source(accepted_source):
    store, _ = accepted_source
    candidate, = StrategyGenerator(store).generate()
    lines = store.log_path.read_bytes().splitlines(keepends=True)
    store.log_path.write_bytes(lines[0])  # Test-local source, never historical artifact.
    with pytest.raises(ValueError):
        validate_sources(candidate, store)
