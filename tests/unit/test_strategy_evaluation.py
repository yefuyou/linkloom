import json

import pytest

from linkloom.evaluation.strategy import StrategyEvaluationRunner
from linkloom.strategy.authority import ComparisonAuthority
from tests.strategy_support import strategy_root, accepted_source, evaluation_fixture


def test_paired_model_observed_requests_differ_only_by_strategy(accepted_source, strategy_root):
    store, _ = accepted_source
    strategy, bundle, oracle = evaluation_fixture(strategy_root, store)
    summary = bundle.summary
    assert summary.recommendation == "recommend_accept"
    baseline = json.loads((bundle.root / "baseline.json").read_bytes())[0]["request_received"]
    candidate = json.loads((bundle.root / "candidate.json").read_bytes())[0]["request_received"]
    assert baseline.pop("strategy_context") == ""
    assert candidate.pop("strategy_context")
    assert baseline == candidate
    assert {r.dimension for r in summary.rows} >= {"grounding", "contract", "decision_semantics", "unsupported_inference"}
    assert next(r for r in summary.rows if r.dimension == "decision_semantics").delta == 1
    assert ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256, oracle_authority=oracle).resolve(summary.result_id) == summary
    assert strategy.status == "candidate"  # Recommendation is not promotion.


@pytest.mark.parametrize("dimension", ["grounding", "contract", "question_scope", "unsupported_inference", "uncertainty", "retrieval_tools"])
def test_any_new_regression_prevents_promotion(accepted_source, strategy_root, dimension):
    _, bundle, _ = evaluation_fixture(strategy_root, accepted_source[0], overrides={"candidate": {dimension: "FAIL"}})
    assert bundle.summary.recommendation == "recommend_reject"
    assert any(row.dimension == dimension and row.delta == -1 and row.severity == "P1" for row in bundle.summary.rows)


@pytest.mark.parametrize("dimension", ["grounding", "unsupported_inference", "uncertainty"])
def test_unevaluated_support_is_insufficient_not_semantic_failure(accepted_source, strategy_root, dimension):
    _, bundle, _ = evaluation_fixture(strategy_root, accepted_source[0], overrides={"candidate": {dimension: "N/E"}})
    assert bundle.summary.recommendation == "insufficient_evidence"
    assert next(r for r in bundle.summary.rows if r.dimension == dimension).delta is None


def test_unchanged_frozen_trace_does_not_claim_improvement(accepted_source, strategy_root):
    _, bundle, _ = evaluation_fixture(strategy_root, accepted_source[0], proof_kind="scripted_replay")
    assert bundle.summary.recommendation == "insufficient_evidence"


def test_harness_request_mismatch_rejected(accepted_source, strategy_root):
    with pytest.raises(ValueError):
        evaluation_fixture(strategy_root, accepted_source[0], tamper_request=True)


def test_stale_authoritative_comparison_rejects(accepted_source, strategy_root):
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0])
    authority = ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256, oracle_authority=oracle)
    (bundle.root / "comparison.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        authority.resolve(bundle.summary.result_id)
