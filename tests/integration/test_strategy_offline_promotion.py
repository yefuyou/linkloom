"""Offline mechanism proof is not live efficacy or an actual human approval."""
import io
import os
import socket
from pathlib import Path

import pytest

from linkloom.experience.models import ExperienceQuery
from linkloom.strategy.authority import ComparisonAuthority
from linkloom.strategy.context import StrategyContextBuilder
from linkloom.strategy.generation import StrategyGenerator
from linkloom.strategy.retrieval import StrategyRetriever
from linkloom.strategy.store import StrategyStore
from tests.strategy_support import strategy_root, accepted_source, evaluation_fixture
from tests.strategy_frozen_bridge import FrozenTraceBridge, historical_candidate_sources
from tests.unit.test_strategy_governance import decision


QUERY = ExperienceQuery("team_decision", ("current_decision", "compared_options"))


def test_existing_accepted_fixture_completes_explicit_offline_promotion(accepted_source, strategy_root, monkeypatch):
    """Responsive fake tests the machinery; human:test_fixture is NOT a user."""
    def forbidden(*args, **kwargs):
        raise AssertionError("offline promotion must not call a Provider or network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    original_open = io.open

    def gold_free(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and any(token in os.fspath(file).casefold() for token in
                                                       ("dataset.jsonl", "golden8", "gold.json")):
            raise AssertionError("generation/promotion/reuse opened Gold")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", gold_free)
    source_store, source = accepted_source
    assert source.status == "accepted"
    strategy, bundle, oracle = evaluation_fixture(strategy_root, source_store)
    authority = ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256, oracle_authority=oracle)
    store = StrategyStore(strategy_root / "strategy.jsonl", source_store=source_store, comparison_authority=authority)
    store.save(strategy)
    assert StrategyRetriever(store).retrieve(QUERY).strategy_ids == ()
    assert bundle.summary.recommendation == "recommend_accept"
    assert store.get(strategy.strategy_id).status == "candidate"
    accepted = store.record_review(decision(strategy, bundle))
    assert store.validate_reviewed_record(accepted).status == "accepted"
    payload = StrategyContextBuilder(store).build(StrategyRetriever(store).retrieve(QUERY)).to_dict()
    assert payload["strategy_ids"] == [strategy.strategy_id]
    assert payload["total_chars"] == len(payload["model_text"]) <= 4000
    assert payload["total_utf8_bytes"] == len(payload["model_text"].encode()) <= 16000
    print(f"mechanism proof (test-only approval): {strategy_root}; recommendation={bundle.summary.recommendation}; terminal={accepted.status}")


def test_frozen_real_trace_reuses_existing_postseal_oracle_without_claiming_gain(accepted_source, strategy_root, monkeypatch):
    bridge = FrozenTraceBridge(strategy_root)
    source_store, _ = accepted_source  # Accepted source approval remains a labeled test fixture.
    strategy, = StrategyGenerator(source_store).generate()
    originals = bridge.original_hashes()
    original_open = io.open
    gold_reads = []

    def postseal_gold_only(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).resolve() == bridge.gold_path:
            assert bridge.phase == "evaluation"
            assert all((bridge.output_root / name).is_file() for name in ("baseline_seal.json", "candidate_seal.json"))
            gold_reads.append(str(file))
        return original_open(file, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("frozen proof attempted a Provider/network call")

    monkeypatch.setattr(io, "open", postseal_gold_only)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
    monkeypatch.setattr(DeepSeekProviderAdapter, "__init__", forbidden)
    bundle = bridge.run(strategy, source_store)
    summary = bundle.summary
    # Each branch verifies the frozen Gold hash, then invokes the unchanged loader.
    assert bridge.inference_calls == 2 and len(gold_reads) == 4
    assert summary.proof_kind == "scripted_replay"
    assert summary.recommendation == "insufficient_evidence"
    assert all(row.delta in {0, None} for row in summary.rows)
    assert next(r for r in summary.rows if r.dimension == "grounding").candidate == "REVIEW_REQUIRED"
    assert all(r.candidate == "N/E" for r in summary.rows if r.dimension in
               {"question_scope", "unsupported_inference", "uncertainty", "retrieval_tools"})
    bridge.phase = "reuse"
    authority = ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256, oracle_authority=bridge.oracle_authority)
    store = StrategyStore(strategy_root / "frozen-strategy.jsonl", source_store=source_store, comparison_authority=authority)
    store.save(strategy)
    with pytest.raises(ValueError):
        store.record_review(decision(strategy, bundle))
    rejected = store.record_review(decision(strategy, bundle, "rejected"))
    assert rejected.status == "rejected" and rejected.review_transition.receipt.evaluator_result == summary
    assert StrategyRetriever(store).retrieve(QUERY).strategy_ids == ()
    assert bridge.original_hashes() == originals
    assert len(gold_reads) == 4  # No Gold access in Store/review/retrieval.
    print(f"frozen proof (test-only source/rejection): {strategy_root}; recommendation={summary.recommendation}; terminal={rejected.status}")


def test_unapproved_historical_experience_path_abstains(strategy_root):
    store, disposition = historical_candidate_sources(strategy_root)
    assert disposition == {"mps": 1, "aer": 0, "iti": 0}
    assert len(store.list("candidate")) == 1 and store.list("accepted") == ()
    assert StrategyGenerator(store).generate() == ()
    print(f"historical source abstention (no fabricated review): {strategy_root}; disposition={disposition}")
