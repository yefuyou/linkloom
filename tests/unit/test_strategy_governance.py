from dataclasses import replace

import pytest

from linkloom.strategy.authority import ComparisonAuthority
from linkloom.strategy.models import HumanStrategyDecision
from linkloom.strategy.store import StrategyStore
from tests.strategy_support import strategy_root, accepted_source, evaluation_fixture


def setup_store(root, source, **evaluation_options):
    strategy, bundle, oracle = evaluation_fixture(root, source, **evaluation_options)
    authority = ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256, oracle_authority=oracle)
    store = StrategyStore(root / "strategy.jsonl", source_store=source, comparison_authority=authority)
    store.save(strategy)
    return store, strategy, bundle


def decision(strategy, bundle, choice="accepted"):
    return HumanStrategyDecision("strategy-human-decision/v1", strategy.strategy_id, bundle.summary.result_id,
                                 choice, "human:test_fixture", "human", "2026-09-17T01:00:00+00:00",
                                 "Reviewed the complete offline comparison and its limits." if choice == "accepted" else
                                 "Insufficient evidence or regression prevents promotion.")


def test_explicit_human_receipt_is_durable_and_binds_evaluation(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    assert store.get(strategy.strategy_id).status == "candidate"
    accepted = store.record_review(decision(strategy, bundle))
    assert accepted.status == "accepted"
    receipt = accepted.review_transition.receipt
    assert receipt.evaluator_result == bundle.summary
    assert receipt.reviewer_decision.source == "human"
    assert receipt.candidate_event_id != accepted.review_transition.review_event_id
    replay = StrategyStore(store.log_path, source_store=accepted_source[0], comparison_authority=store.comparison_authority)
    assert replay.validate_reviewed_record(accepted) == accepted


@pytest.mark.parametrize("source,actor", [("independent_reviewer", "reviewer:test"), ("deterministic_validator", "validator:test"),
                                         ("human", "human:model"), ("human", "human:reviewer")])
def test_nonhuman_or_callback_identity_cannot_masquerade(source, actor, accepted_source, strategy_root):
    _, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    with pytest.raises(ValueError):
        replace(decision(strategy, bundle), source=source, actor=actor)


@pytest.mark.parametrize("evaluation_options", [{"proof_kind": "scripted_replay"}, {"overrides": {"candidate": {"grounding": "FAIL"}}}])
def test_unsafe_or_insufficient_result_cannot_be_human_accepted(evaluation_options, accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0], **evaluation_options)
    with pytest.raises(ValueError):
        store.record_review(decision(strategy, bundle))
    assert store.get(strategy.strategy_id).status == "candidate"
    rejected = store.record_review(decision(strategy, bundle, "rejected"))
    assert rejected.status == "rejected" and store.log_path.exists()


def test_copied_terminal_record_or_removed_review_is_not_persistence(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    accepted = store.record_review(decision(strategy, bundle))
    foreign = StrategyStore(strategy_root / "foreign.jsonl", source_store=accepted_source[0], comparison_authority=store.comparison_authority)
    with pytest.raises(ValueError):
        foreign.save(accepted)
    store.log_path.write_bytes(store.log_path.read_bytes().splitlines(keepends=True)[0])
    with pytest.raises(ValueError):
        store.validate_reviewed_record(accepted)


def test_stale_comparison_blocks_review_and_durable_reuse(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    accepted = store.record_review(decision(strategy, bundle))
    (bundle.root / "comparison.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        store.validate_reviewed_record(accepted)
