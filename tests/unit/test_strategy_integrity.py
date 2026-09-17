"""Consumption and audit integrity attacks, without changing upstream evidence."""
import json
import hashlib
from dataclasses import replace

import pytest

from linkloom.evaluation.strategy import FrozenCase
from linkloom.strategy.context import StrategyContextBuilder
from linkloom.strategy.models import ComparisonSummary, MetricDelta, StrategyReviewReceipt, canonical_hash, canonical_json
from linkloom.strategy.authority import ComparisonAuthority
from linkloom.strategy.retrieval import StrategyRetriever
from tests.strategy_support import strategy_root, accepted_source
from tests.unit.test_strategy_context import QUERY
from tests.unit.test_strategy_governance import setup_store, decision


def test_review_receipt_has_closed_versioned_contract(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    accepted = store.record_review(decision(strategy, bundle))
    payload = accepted.review_transition.receipt.to_dict()
    assert payload["schema_version"] == "strategy-review-receipt/v1"
    payload["schema_version"] = "strategy-review-receipt/v2"
    with pytest.raises(ValueError):
        StrategyReviewReceipt.from_dict(payload)


@pytest.mark.parametrize("payload", [{"gold": "private"}, {"documents": [{"expected_answer": "private"}]},
                                     {"nested": {"case_id": "private"}}])
def test_evaluator_private_input_never_enters_frozen_request(payload):
    with pytest.raises(ValueError):
        FrozenCase(canonical_hash("public-case"), canonical_json(payload))


@pytest.mark.parametrize("delta,severity", [(0, None), (True, None), (1, "P1")])
def test_fabricated_metric_delta_or_severity_rejected(delta, severity):
    with pytest.raises(ValueError):
        MetricDelta(canonical_hash("metric-case"), "decision_semantics", "FAIL", "PASS", delta, severity)


def test_partial_report_cannot_be_rehashed_into_valid_comparison(accepted_source, strategy_root):
    _, _, bundle = setup_store(strategy_root, accepted_source[0])
    payload = bundle.summary.to_dict()
    payload["rows"] = payload["rows"][:-1]
    payload["result_id"] = canonical_hash({k: v for k, v in payload.items() if k != "result_id"})
    with pytest.raises(ValueError):
        ComparisonSummary.from_dict(payload)


def test_recomputed_recommendation_or_orphan_result_is_not_authority(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    with pytest.raises(ValueError):
        store.comparison_authority.resolve("f" * 64)
    with pytest.raises(ValueError):
        replace(bundle.summary, recommendation="recommend_reject")
    with pytest.raises(ValueError):
        store.record_review(replace(decision(strategy, bundle), strategy_id="f" * 64))


@pytest.mark.parametrize("artifact", ["baseline_seal.json", "candidate_seal.json", "comparison_seal.json", "manifest.json"])
def test_modified_seal_or_bootstrap_manifest_fails_fresh_resolution(artifact, accepted_source, strategy_root):
    store, _, bundle = setup_store(strategy_root, accepted_source[0])
    (bundle.root / artifact).write_bytes(b"{}")
    with pytest.raises(ValueError):
        store.comparison_authority.resolve(bundle.summary.result_id)


@pytest.mark.parametrize("artifact,field,value", [("baseline_seal", "sealed", 1), ("candidate_seal", "gold_available", 0),
                                                 ("comparison_seal", "sealed", 1)])
def test_even_registered_seal_requires_exact_boolean_types(artifact, field, value, accepted_source, strategy_root):
    """Trusted producer registration cannot make a malformed seal well-typed."""
    store, _, bundle = setup_store(strategy_root, accepted_source[0])
    manifest = json.loads((bundle.root / "manifest.json").read_bytes())
    locator = manifest["results"][0]["artifacts"][artifact]
    path = bundle.root / locator["path"]
    seal = json.loads(path.read_bytes())
    seal[field] = value
    raw = canonical_json(seal).encode()
    path.write_bytes(raw)
    locator["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_raw = canonical_json(manifest).encode()
    path = bundle.root / "malformed-producer-manifest.json"
    path.write_bytes(manifest_raw)
    authority = ComparisonAuthority(path, manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(), oracle_authority=store.comparison_authority._oracle_authority)
    with pytest.raises(ValueError):
        authority.resolve(bundle.summary.result_id)


def test_final_serializer_rechecks_source_approval_not_cached_selection(accepted_source, strategy_root):
    source = accepted_source[0]
    store, strategy, bundle = setup_store(strategy_root, source)
    store.record_review(decision(strategy, bundle))
    context = StrategyContextBuilder(store).build(StrategyRetriever(store).retrieve(QUERY))
    source.log_path.write_bytes(source.log_path.read_bytes().splitlines(keepends=True)[0])
    with pytest.raises(ValueError):
        context.to_dict()


def test_unicode_forged_final_text_cannot_bypass_measured_bytes(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    store.record_review(decision(strategy, bundle))
    context = StrategyContextBuilder(store).build(StrategyRetriever(store).retrieve(QUERY))
    object.__setattr__(context, "model_text", "\U0001f600" * 4001)
    with pytest.raises(ValueError):
        context.to_dict()


def test_torn_tail_retained_and_cannot_accept_by_append(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    original = store.log_path.read_bytes()
    store.log_path.write_bytes(original + b'{"unfinished":')
    assert store.get(strategy.strategy_id).status == "candidate"
    with pytest.raises(ValueError):
        store.record_review(decision(strategy, bundle))
    assert store.log_path.read_bytes() == original + b'{"unfinished":'


def test_corrupt_complete_chain_rejected(accepted_source, strategy_root):
    store, strategy, _ = setup_store(strategy_root, accepted_source[0])
    payload = json.loads(store.log_path.read_bytes())
    payload["previous_event_id"] = "f" * 64
    payload["event_id"] = canonical_hash({k: v for k, v in payload.items() if k != "event_id"})
    store.log_path.write_bytes(canonical_json(payload).encode() + b"\n")
    with pytest.raises(ValueError):
        store.get(strategy.strategy_id)
