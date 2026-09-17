"""Retained local offline test outputs; source approval is explicitly test-only."""
from pathlib import Path
from uuid import uuid4
import json
import secrets

import pytest

from linkloom.experience.store import ExperienceStore
from tests.experience_authority_support import fixture_authority, fixture_reviewed


@pytest.fixture
def strategy_root():
    root = Path(__file__).parents[1] / ".tmp" / ("gate5b-" + uuid4().hex)
    root.mkdir(parents=True)
    return root


@pytest.fixture
def accepted_source(strategy_root):
    authority = fixture_authority()
    store = ExperienceStore(strategy_root / "experience.jsonl", authority=authority)
    record = fixture_reviewed(store, authority)
    return store, record


def evaluation_fixture(root, source_store, *, overrides=None, proof_kind="responsive_fake", tamper_request=False, strategy=None,
                       tamper_observation=None):
    from linkloom.evaluation.strategy import ControlledObservationInputs, FrozenCase, FrozenSuite, OfflineObservation, StrategyEvaluationRunner
    from linkloom.strategy.generation import StrategyGenerator
    from linkloom.strategy.models import canonical_hash, canonical_json
    from linkloom.strategy.policy import DIMENSIONS

    if strategy is None:
        candidate, = StrategyGenerator(source_store).generate()
    else:
        candidate = strategy
    case = FrozenCase(canonical_hash("generic-private-case"), canonical_json({
        "question": "Which alternatives have explicit rejection evidence?",
        "documents": ["Options are compared; no explicit rejection is recorded."],
    }))
    identities = {key: canonical_hash("frozen-" + key) for key in
                  ("runtime", "tools", "retrieval", "base_prompt", "budgets", "model_abstraction", "seed", "evaluator", "oracle")}
    suite = FrozenSuite((case,), canonical_json(identities), proof_kind)

    def harness(request):
        from dataclasses import replace
        received = replace(request, strategy_context="tampered") if tamper_request else request
        changed = bool(request.strategy_context) and proof_kind == "responsive_fake"
        trajectory = "[]"
        inputs = ControlledObservationInputs(canonical_json(json.loads(request.public_request_json)["documents"]), "[]")
        if request.strategy_context:
            if tamper_observation == "trajectory":
                trajectory = canonical_json([{"changed": True}])
            elif tamper_observation == "tools":
                inputs = replace(inputs, tool_observations_json=canonical_json([{"changed": True}]))
            elif tamper_observation == "documents":
                inputs = replace(inputs, document_observations_json=canonical_json(["changed"]))
        return OfflineObservation(received, canonical_json({"classification": "supported" if changed else "unsupported"}), trajectory, inputs)

    def evaluator(case, observation):
        values = {dimension: "PASS" for dimension in DIMENSIONS}
        values["decision_semantics"] = "PASS" if json.loads(observation.final_json)["classification"] == "supported" else "FAIL"
        values.update((overrides or {}).get("candidate" if observation.request_received.strategy_context else "baseline", {}))
        return values

    owner, authority = oracle_bootstrap(root, suite, evaluator)
    bundle = StrategyEvaluationRunner(suite, harness=harness, oracle_owner=owner, source_store=source_store).run(candidate, root / ("comparison-" + uuid4().hex))
    return candidate, bundle, authority


def oracle_bootstrap(root, suite, evaluator):
    """Test host configures trust BEFORE producer runs; keys never enter bundles."""
    from linkloom.evaluation.strategy_oracle import OracleOwner, OracleVerifier, OracleAuthority
    key = secrets.token_bytes(32)
    private = root / "private-bootstrap"
    private.mkdir(exist_ok=True)
    # Retained synthetic restart fixture, outside all comparison/model artifacts.
    with (private / (uuid4().hex + ".bin")).open("xb") as handle:
        handle.write(key)
    verifier = OracleVerifier(suite, key)
    return OracleOwner(suite, evaluator, key), OracleAuthority((verifier,))


def saturation_store(root, count=6):
    """Synthetic saturation only: every Strategy actually completes all durable stages."""
    import hashlib
    import json
    from linkloom.experience.authority import EvaluationAuthority
    from linkloom.experience.models import ExperienceReviewDecision
    from linkloom.experience.reflection import RunReflection
    from linkloom.strategy.authority import ComparisonAuthority
    from linkloom.strategy.generation import StrategyGenerator
    from linkloom.strategy.models import HumanStrategyDecision, canonical_json
    from linkloom.strategy.store import StrategyStore
    from tests.experience_authority_support import synthetic_evaluator_bundle

    producer = root / "saturation"
    producer.mkdir()
    registrations = []
    for index in range(count):
        def edit(observed, evaluation, index=index):
            identity = f"run_saturation_{index}"
            observed["run_id"] = observed["result"]["run_id"] = evaluation["run_id"] = identity
            refs = [f"{identity}:ev_real", f"{identity}:ev_second"]
            observed["supporting_observations"][0]["source_evidence_refs"] = refs
            evaluation["findings"][0]["source_evidence_refs"] = refs
        synthetic_evaluator_bundle(producer / str(index), edit=edit)
        manifest = json.loads((producer / str(index) / "manifest.json").read_bytes())
        entry = manifest["results"][0]
        for artifact in entry["artifacts"].values():
            artifact["path"] = f"{index}/" + artifact["path"]
        registrations.append(entry)
    raw = canonical_json({"schema_version": "evaluation-authority-manifest/v1", "results": registrations}).encode()
    (producer / "source-manifest.json").write_bytes(raw)
    source_authority = EvaluationAuthority(producer / "source-manifest.json", manifest_sha256=hashlib.sha256(raw).hexdigest())
    sources = ExperienceStore(producer / "source.jsonl", authority=source_authority)
    for entry in registrations:
        candidate, = RunReflection(authority=source_authority).reflect(source_authority.resolve(entry["result_id"]))
        sources.save(candidate)
        sources.record_review(ExperienceReviewDecision("experience-review/v1", candidate.experience_id, "accepted", "reviewer:test", "independent_reviewer", "review:offline", "2026-09-17T01:00:00+00:00"))
    strategies = StrategyGenerator(sources).generate()
    comparisons, entries, verifiers = [], [], []
    for strategy in strategies:
        _, bundle, oracle = evaluation_fixture(producer, sources, strategy=strategy)
        verifiers.extend(oracle.verifiers)
        comparisons.append(bundle)
        entry = json.loads((bundle.root / "manifest.json").read_bytes())["results"][0]
        for artifact in entry["artifacts"].values():
            artifact["path"] = bundle.root.name + "/" + artifact["path"]
        entries.append(entry)
    raw = canonical_json({"schema_version": "strategy-comparison-manifest/v1", "results": entries}).encode()
    (producer / "comparison-manifest.json").write_bytes(raw)
    from linkloom.evaluation.strategy_oracle import OracleAuthority
    authority = ComparisonAuthority(producer / "comparison-manifest.json", manifest_sha256=hashlib.sha256(raw).hexdigest(), oracle_authority=OracleAuthority(tuple(verifiers)))
    store = StrategyStore(producer / "strategy.jsonl", source_store=sources, comparison_authority=authority)
    for strategy, bundle in zip(strategies, comparisons):
        store.save(strategy)
        store.record_review(HumanStrategyDecision("strategy-human-decision/v1", strategy.strategy_id, bundle.summary.result_id, "accepted", "human:test_fixture", "human", "2026-09-17T01:00:00+00:00", "Reviewed the complete offline comparison and its limits."))
    return store
