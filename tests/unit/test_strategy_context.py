from dataclasses import replace

import pytest

from linkloom.experience.models import ExperienceQuery
from linkloom.strategy.context import StrategyContextBuilder
from linkloom.strategy.retrieval import StrategyRetriever, StrategySelection
from tests.strategy_support import strategy_root, accepted_source, saturation_store
from tests.unit.test_strategy_governance import setup_store, decision

QUERY = ExperienceQuery("team_decision", ("current_decision", "compared_options"))


def test_candidate_rejected_and_missing_receipt_are_not_injectable(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    assert StrategyRetriever(store).retrieve(QUERY).strategy_ids == ()
    with pytest.raises(ValueError):
        StrategyContextBuilder(store).build(StrategySelection((strategy.strategy_id,), QUERY, 3, 2000))
    store.record_review(decision(strategy, bundle, "rejected"))
    assert StrategyRetriever(store).retrieve(QUERY).strategy_ids == ()


def test_accepted_context_is_separate_bounded_and_applicable(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    store.record_review(decision(strategy, bundle))
    selection = StrategyRetriever(store).retrieve(QUERY)
    context = StrategyContextBuilder(store).build(selection)
    payload = context.to_dict()
    assert payload["model_text"].startswith("Relevant accepted strategy")
    assert len(payload["strategy_ids"]) == 1 and len(payload["model_text"]) <= 2000
    assert strategy.strategy_id not in payload["model_text"]
    assert StrategyRetriever(store).retrieve(ExperienceQuery("search", ("unrelated",))).strategy_ids == ()
    assert StrategyRetriever(store).retrieve(ExperienceQuery("team_decision", ("current_decision", "explicit_rejection_evidence_present"))).strategy_ids == ()
    assert StrategyRetriever(store).retrieve(QUERY, max_chars=10).strategy_ids == ()
    with pytest.raises(ValueError):
        StrategyContextBuilder(store).build(object())  # Preview/type confusion fails closed.


def test_six_genuinely_persisted_strategies_cannot_bypass_final_bounds(strategy_root):
    store = saturation_store(strategy_root)
    records = store.list("accepted")
    assert len(records) == 6
    ids = tuple(r.strategy_id for r in records)
    forged = StrategySelection.__new__(StrategySelection)
    for name, value in dict(strategy_ids=ids, query=QUERY, top_k=5, max_chars=4000).items():
        object.__setattr__(forged, name, value)
    assert len(StrategyContextBuilder(store).build(forged).to_dict()["strategy_ids"]) == 5
    assert len(StrategyContextBuilder(store, hard_top_k=3).build(forged).to_dict()["strategy_ids"]) == 3
    context = StrategyContextBuilder(store).build(forged)
    object.__setattr__(context, "strategy_ids", ids)
    object.__setattr__(context, "model_text", "x" * 5000)
    with pytest.raises(ValueError):
        context.to_dict()


@pytest.mark.parametrize("field,value", [("top_k", 6), ("max_chars", 4001), ("top_k", True), ("max_chars", -1)])
def test_requested_bounds_cannot_widen_hard_limits(field, value, accepted_source, strategy_root):
    store, _, _ = setup_store(strategy_root, accepted_source[0])
    with pytest.raises(ValueError):
        StrategyRetriever(store).retrieve(QUERY, **{field: value})


def test_final_serialization_reresolves_source_and_comparison(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    store.record_review(decision(strategy, bundle))
    section = StrategyContextBuilder(store).build(StrategyRetriever(store).retrieve(QUERY))
    (bundle.root / "candidate.json").write_bytes(b"[]")
    with pytest.raises(ValueError):
        section.to_dict()
