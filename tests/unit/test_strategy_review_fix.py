"""Original 5B Reviewer P1 reproductions and durable audit guards."""
import json
import hashlib
from dataclasses import replace

import pytest

from linkloom.strategy.models import ComparisonSummary, MetricDelta, canonical_hash, canonical_json
from linkloom.strategy.authority import ComparisonAuthority
from tests.strategy_support import strategy_root, accepted_source, evaluation_fixture
from tests.unit.test_strategy_governance import setup_store, decision


def test_each_metric_has_canonical_paired_evidence_and_persisted_receipt(accepted_source, strategy_root):
    store, strategy, bundle = setup_store(strategy_root, accepted_source[0])
    baseline = json.loads((bundle.root / "baseline.json").read_bytes())[0]
    candidate = json.loads((bundle.root / "candidate.json").read_bytes())[0]
    for row in bundle.summary.rows:
        for branch, reference, observed in (("baseline", row.baseline_evidence, baseline),
                                             ("candidate", row.candidate_evidence, candidate)):
            assert reference.case_key == row.case_key and reference.dimension == row.dimension
            assert reference.artifact_sha256 == getattr(bundle.summary, branch + "_sha256")
            assert reference.observation_sha256 == canonical_hash(observed)
            assert reference.reason == "sealed_observation/v1"
    accepted = store.record_review(decision(strategy, bundle))
    assert accepted.review_transition.receipt.evaluator_result.rows == bundle.summary.rows


def test_context_cost_is_per_case_baseline_candidate_delta_and_evidence(accepted_source, strategy_root):
    _, bundle, _ = evaluation_fixture(strategy_root, accepted_source[0])
    cost, = bundle.summary.context_costs
    assert cost.baseline_chars == cost.baseline_bytes == 0
    assert cost.candidate_chars == cost.char_delta == bundle.summary.context_chars
    assert cost.candidate_bytes == cost.byte_delta == bundle.summary.context_bytes
    assert cost.baseline_evidence.dimension == cost.candidate_evidence.dimension == "context_cost"
    assert cost.token_delta is None and cost.cost_usd_delta is None


@pytest.mark.parametrize("component", ["trajectory", "tools", "documents"])
def test_non_context_observation_difference_cannot_hide_behind_gain(component, accepted_source, strategy_root):
    with pytest.raises(ValueError):
        evaluation_fixture(strategy_root, accepted_source[0], tamper_observation=component)


@pytest.mark.parametrize("outcome,reason", [("N/E", "oracle_not_evaluated/v1"), ("REVIEW_REQUIRED", "oracle_review_required/v1")])
def test_unknown_outcome_has_validated_nonfailure_reason(outcome, reason, accepted_source, strategy_root):
    _, bundle, _ = evaluation_fixture(strategy_root, accepted_source[0], overrides={"candidate": {"grounding": outcome}})
    row = next(r for r in bundle.summary.rows if r.dimension == "grounding")
    assert row.candidate_evidence.reason == reason and row.delta is None
    assert bundle.summary.recommendation == "insufficient_evidence"
    payload = row.to_dict()
    payload["candidate_evidence"]["reason"] = "sealed_observation/v1"
    with pytest.raises(ValueError):
        MetricDelta.from_dict(payload)


def test_cost_delta_and_unavailable_token_cost_cannot_be_fabricated(accepted_source, strategy_root):
    _, bundle, _ = evaluation_fixture(strategy_root, accepted_source[0])
    cost, = bundle.summary.context_costs
    with pytest.raises(ValueError):
        replace(cost, char_delta=0)
    with pytest.raises(ValueError):
        replace(cost, token_delta=0)
    payload = bundle.summary.to_dict()
    payload["context_costs"] = []
    payload["result_id"] = canonical_hash({k: v for k, v in payload.items() if k != "result_id"})
    with pytest.raises(ValueError):
        ComparisonSummary.from_dict(payload)


def resealed_authority(bundle, edit, oracle_authority, *, rebind_observations=False):
    """Malicious test producer rewrites its own private bundle, not source artifacts."""
    manifest = json.loads((bundle.root / "manifest.json").read_bytes())
    locators = manifest["results"][0]["artifacts"]
    payloads = {name: json.loads((bundle.root / locator["path"]).read_bytes()) for name, locator in locators.items()}
    edit(payloads)

    def write(name):
        raw = canonical_json(payloads[name]).encode()
        (bundle.root / locators[name]["path"]).write_bytes(raw)
        locators[name]["sha256"] = hashlib.sha256(raw).hexdigest()
        return locators[name]["sha256"]

    comparison = payloads["comparison"]
    for name in ("oracle_outcomes", "oracle_outcomes_seal"):
        write(name)
    for name in ("baseline", "candidate"):
        digest = write(name)
        comparison[name + "_sha256"] = digest
        payloads[name + "_seal"]["observation_sha256"] = digest
        write(name + "_seal")
        if rebind_observations:
            observations = {o["request_received"]["case_key"]: o for o in payloads[name]}
            for row in (*comparison["rows"], *comparison["context_costs"]):
                ref = row[name + "_evidence"]
                ref["artifact_sha256"] = digest
                ref["observation_sha256"] = canonical_hash(observations[row["case_key"]])
    comparison["result_id"] = canonical_hash({k: v for k, v in comparison.items() if k != "result_id"})
    digest = write("comparison")
    payloads["comparison_seal"].update(comparison_sha256=digest, baseline_sha256=comparison["baseline_sha256"],
                                       candidate_sha256=comparison["candidate_sha256"])
    write("comparison_seal")
    manifest["results"][0]["result_id"] = comparison["result_id"]
    raw = canonical_json(manifest).encode()
    path = bundle.root / "malicious-producer-manifest.json"
    path.write_bytes(raw)
    return ComparisonAuthority(path, manifest_sha256=hashlib.sha256(raw).hexdigest(), oracle_authority=oracle_authority), comparison["result_id"]


@pytest.mark.parametrize("component", ["trajectory", "tools", "documents"])
def test_authority_rejects_resealed_noncontext_difference(component, accepted_source, strategy_root):
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0])
    def edit(payloads):
        observed = payloads["candidate"][0]
        if component == "trajectory":
            observed["trajectory_json"] = '["changed"]'
        else:
            key = "tool_observations_json" if component == "tools" else "document_observations_json"
            observed["inputs"][key] = '["changed"]'
    authority, result_id = resealed_authority(bundle, edit, oracle, rebind_observations=True)
    with pytest.raises(ValueError, match="oracle|shared condition"):
        authority.resolve(result_id)


def test_authority_rejects_rehashed_orphan_metric_locator(accepted_source, strategy_root):
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0])
    def edit(payloads):
        payloads["comparison"]["rows"][0]["candidate_evidence"]["observation_sha256"] = "f" * 64
    authority, result_id = resealed_authority(bundle, edit, oracle)
    with pytest.raises(ValueError, match="per-metric evidence"):
        authority.resolve(result_id)


def test_typed_controlled_observations_reject_unknown_or_noncanonical_payload():
    from linkloom.evaluation.strategy import ControlledObservationInputs
    with pytest.raises(ValueError):
        ControlledObservationInputs('[ "document" ]', '[]')
    payload = ControlledObservationInputs('[]', '[]').to_dict()
    payload["unverified_extra"] = "private"
    with pytest.raises(ValueError):
        ControlledObservationInputs.from_dict(payload)
