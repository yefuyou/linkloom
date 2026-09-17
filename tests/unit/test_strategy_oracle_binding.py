"""A comparison producer cannot reseal oracle claims into a promotion."""
import json
import secrets

import pytest

from linkloom.evaluation.strategy import FrozenCase, FrozenSuite
from linkloom.strategy.models import canonical_hash
from tests.strategy_support import strategy_root, accepted_source, evaluation_fixture
from tests.unit.test_strategy_review_fix import resealed_authority


def forge_claims(payloads):
    summary = payloads["comparison"]
    for row in summary["rows"]:
        row.update(baseline="FAIL" if row["dimension"] == "decision_semantics" else "PASS",
                   candidate="PASS", delta=1 if row["dimension"] == "decision_semantics" else 0, severity=None)
        row["baseline_evidence"]["reason"] = row["candidate_evidence"]["reason"] = "sealed_observation/v1"
    summary["proof_kind"] = "responsive_fake"
    observed = payloads["baseline"][0]["request_received"]
    suite = FrozenSuite((FrozenCase(observed["case_key"], observed["public_request_json"], observed["tools_applicable"]),),
                        observed["configuration_json"], "responsive_fake")
    summary["suite_sha256"] = suite.suite_sha256
    payloads["comparison_seal"]["suite_sha256"] = suite.suite_sha256
    summary["recommendation"] = "recommend_accept"


@pytest.mark.parametrize("attack", ["outcomes", "proof_and_outcomes"])
def test_public_rehash_cannot_forge_oracle_truth(attack, accepted_source, strategy_root):
    options = ({"proof_kind": "scripted_replay"} if attack == "proof_and_outcomes" else
               {"overrides": {"candidate": {"grounding": "N/E"}}})
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0], **options)
    assert bundle.summary.recommendation == "insufficient_evidence"

    authority, result_id = resealed_authority(bundle, forge_claims, oracle)
    with pytest.raises(ValueError, match="oracle"):
        authority.resolve(result_id)


def test_original_frozen_trace_relabel_attack_fails(accepted_source, strategy_root):
    from tests.strategy_frozen_bridge import FrozenTraceBridge, ORIGINAL_PINS
    from linkloom.strategy.generation import StrategyGenerator
    bridge = FrozenTraceBridge(strategy_root)
    strategy, = StrategyGenerator(accepted_source[0]).generate()
    bundle = bridge.run(strategy, accepted_source[0])
    authority, result_id = resealed_authority(bundle, forge_claims, bridge.oracle_authority)
    with pytest.raises(ValueError, match="oracle"):
        authority.resolve(result_id)
    assert bridge.original_hashes() == ORIGINAL_PINS


@pytest.mark.parametrize("attack", ["matrix", "proof", "issuer", "boolean", "non_ascii_mac"])
def test_rehashing_signed_oracle_without_host_key_fails(attack, accepted_source, strategy_root):
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0])

    def forge(payloads):
        outcome, seal = payloads["oracle_outcomes"], payloads["oracle_outcomes_seal"]
        if attack == "matrix":
            outcome["cases"][0]["baseline"]["grounding"] = "FAIL"
        elif attack == "proof":
            outcome["proof_kind"] = "scripted_replay"
        elif attack == "issuer":
            seal["issuer_id"] = "f" * 64
        elif attack == "boolean":
            seal["sealed"] = 1
        else:
            seal["mac_sha256"] = "\u975e" * 64
        seal["outcomes_sha256"] = canonical_hash(outcome)
        payloads["comparison"]["oracle_outcome_sha256"] = canonical_hash(outcome)

    authority, result_id = resealed_authority(bundle, forge, oracle)
    with pytest.raises(ValueError):
        authority.resolve(result_id)


def test_bundle_cannot_register_foreign_key_or_replace_frozen_bootstrap(accepted_source, strategy_root):
    from dataclasses import replace
    from linkloom.evaluation.strategy_oracle import OracleAuthority, OracleVerifier
    from linkloom.strategy.authority import ComparisonAuthority
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0])
    trusted = oracle.verifiers[0]
    for verifier in (OracleVerifier(trusted._suite, secrets.token_bytes(32)),
                     OracleVerifier(replace(trusted._suite, proof_kind="scripted_replay"), trusted._key)):
        authority = ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256,
                                        oracle_authority=OracleAuthority((verifier,)))
        with pytest.raises(ValueError, match="oracle"):
            authority.resolve(bundle.summary.result_id)
    with pytest.raises(TypeError):
        ComparisonAuthority(bundle.root / "manifest.json", manifest_sha256=bundle.manifest_sha256)


def test_host_integrity_key_does_not_enter_artifacts_or_model_context(accepted_source, strategy_root):
    _, bundle, oracle = evaluation_fixture(strategy_root, accepted_source[0])
    key = oracle.verifiers[0]._key
    for path in bundle.root.iterdir():
        raw = path.read_bytes()
        assert key not in raw and key.hex().encode() not in raw
    assert key.hex() not in repr(oracle) and key.hex() not in repr(oracle.verifiers[0])
    assert not hasattr(bundle, "oracle_authority")
    assert bundle.summary.oracle_outcome_sha256 == canonical_hash(json.loads((bundle.root / "oracle_outcomes.json").read_bytes()))


@pytest.mark.parametrize("key", [b"", b"short", "x" * 32])
def test_malformed_bootstrap_key_is_rejected(key):
    from linkloom.evaluation.strategy_oracle import OracleVerifier
    from linkloom.strategy.models import canonical_json
    configuration = {k: canonical_hash(k) for k in
                     ("runtime", "tools", "retrieval", "base_prompt", "budgets", "model_abstraction", "seed", "evaluator", "oracle")}
    suite = FrozenSuite((FrozenCase(canonical_hash("case"), '{}'),), canonical_json(configuration), "responsive_fake")
    with pytest.raises(ValueError):
        OracleVerifier(suite, key)
