"""Test-only legacy oracle bridge. Original evidence/Gold are strictly read-only.

This replays one frozen output; it does not execute a new production prompt,
Runtime, retrieval or Provider. Unknown oracle dimensions remain unevaluated.
"""
import hashlib
import json
from pathlib import Path

import pytest

from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.evaluation.strategy import ControlledObservationInputs, FrozenCase, FrozenSuite, OfflineObservation, StrategyEvaluationRunner
from linkloom.experience.authority import HistoricalReviewAuthority
from linkloom.experience.reflection import RunReflection
from linkloom.experience.store import ExperienceStore
from linkloom.strategy.models import canonical_hash, canonical_json
from linkloom.strategy.policy import DIMENSIONS

PROJECT = Path(__file__).parents[1]
HISTORICAL = PROJECT / "tests/fixtures/experience_reflection_v1/historical_review"
HISTORICAL_PIN = "70b8c3a4091e2ea350093edc1d1c60fe5e517c801739ae64730f9933148ad0d0"
OBSERVED_ROOT = PROJECT / ".artifacts/deepseek_real_provider_smoke/mps-001-745eb207a01449a98e39ee23b9b4ed1e/mps-001"
ORIGINAL_PINS = {
    "observed_summary.json": "40eb79e79ae170d42ce0727fce84fe443f5e73c04d8d4295586a712472dc9b6c",
    "observed_summary.seal.json": "d05fea3edd5172b478a224618a9fc6e60b6bae7a818f2a6a5c56de73984fc725",
    "posthoc_evaluation.json": "29a1aea4f2e5a6e24c70858ab7d14fd61c4891ba8afac963e0d658ee89ff297d",
}


def historical_candidate_sources(root):
    manifest = json.loads((HISTORICAL / "manifest.json").read_bytes())
    if any(not (PROJECT / source["path"]).is_file() for entry in manifest["results"] for source in entry["sources"].values()):
        pytest.skip("unchanged local historical evidence is unavailable")
    authority = HistoricalReviewAuthority(HISTORICAL / "manifest.json", manifest_sha256=HISTORICAL_PIN, artifact_root=PROJECT)
    store = ExperienceStore(root / "historical-experience.jsonl", authority=authority)
    identities = {"run_p4_961cf695f6f8": "mps", "run_p4_dd10d1b9eee7": "aer", "run_p4_b63d23ad517d": "iti"}
    disposition = {}
    for entry in manifest["results"]:
        candidates = RunReflection(authority=authority).reflect(authority.resolve(entry["result_id"]))
        disposition[identities[entry["run_id"]]] = len(candidates)
        for candidate in candidates:
            store.save(candidate)  # Do NOT manufacture an acceptance receipt.
    return store, disposition


class FrozenTraceBridge:
    def __init__(self, root):
        if any(not (OBSERVED_ROOT / name).is_file() for name in ORIGINAL_PINS):
            pytest.skip("unchanged local frozen real trace is unavailable")
        self.root = root
        self.output_root = root / "frozen-comparison"
        self.phase, self.inference_calls = "bootstrap", 0
        assert self.original_hashes() == ORIGINAL_PINS
        self.observed = json.loads((OBSERVED_ROOT / "observed_summary.json").read_bytes())
        from tests.smoke import test_deepseek_real_provider_smoke as legacy
        self.legacy = legacy
        self.gold_path = legacy.DATASET_PATH.resolve()
        documents = [{"document_id": canonical_hash(path.name), "text": path.read_text(encoding="utf-8")}
                     for path in sorted((legacy.SEED_ROOT / "workspaces" / legacy.WORKSPACE_ID).glob("*.md"))]
        public = {"question": legacy.USER_QUESTION, "documents": documents}
        code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        configuration = {
            "runtime": canonical_hash(self.observed["runtime_state"]),
            "tools": canonical_hash(self.observed["tool_calls"]),
            "retrieval": canonical_hash(documents),
            "base_prompt": canonical_hash("scripted-replay/no-prompt-execution/v1"),
            "budgets": canonical_hash(self.observed["guard"]),
            "model_abstraction": canonical_hash([code_hash, ORIGINAL_PINS["observed_summary.json"]]),
            "seed": canonical_hash("deterministic-frozen-replay/v1"),
            "evaluator": hashlib.sha256(Path(legacy.__file__).read_bytes()).hexdigest(),
            "oracle": legacy.FROZEN_DATASET_SHA256.lower(),
        }
        self.suite = FrozenSuite((FrozenCase(canonical_hash("private-frozen-trace-key/v1"), canonical_json(public)),),
                                 canonical_json(configuration), "scripted_replay")

    def original_hashes(self):
        return {name: hashlib.sha256((OBSERVED_ROOT / name).read_bytes()).hexdigest() for name in ORIGINAL_PINS}

    def harness(self, request):
        assert self.phase == "inference"
        self.inference_calls += 1
        # Full observed evidence is public historical output, not expected answers.
        # Both requests are actually received; the frozen output is unchanged.
        inputs = ControlledObservationInputs(canonical_json(json.loads(request.public_request_json)["documents"]),
                                             canonical_json(self.observed["tool_calls"]))
        return OfflineObservation(request, canonical_json(self.observed["team_decision_result"]), canonical_json(self.observed), inputs)

    def evaluator(self, case, observation):
        assert self.inference_calls == 2
        assert all((self.output_root / name).is_file() for name in ("baseline_seal.json", "candidate_seal.json"))
        self.phase = "evaluation"
        gold_raw = self.gold_path.read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(gold_raw).hexdigest() == self.legacy.FROZEN_DATASET_SHA256.lower()
        observed = json.loads(observation.trajectory_json)
        observed["team_decision_result"] = json.loads(observation.final_json)
        copy_root = self.root / ("posthoc-candidate" if observation.request_received.strategy_context else "posthoc-baseline")
        copy_root.mkdir()
        self.legacy._seal_observed(copy_root, observed)
        posthoc = self.legacy.evaluate_sealed_case(copy_root)
        dimensions = {name: "N/E" for name in DIMENSIONS}
        try:
            TeamDecisionResult.from_dict(observed["team_decision_result"])
        except ValueError:
            dimensions["contract"] = "FAIL"
        else:
            dimensions["contract"] = "PASS"
        dimensions["decision_semantics"] = posthoc["semantic"] if posthoc["semantic"] in {"PASS", "FAIL"} else "N/E"
        # Citation membership PASS is NOT proof of semantic entailment.
        dimensions["grounding"] = "REVIEW_REQUIRED" if posthoc["grounding"] == "PASS" else "FAIL"
        return dimensions

    def run(self, strategy, source_store):
        from tests.strategy_support import oracle_bootstrap
        owner, self.oracle_authority = oracle_bootstrap(self.root, self.suite, self.evaluator)
        self.phase = "inference"
        return StrategyEvaluationRunner(self.suite, harness=self.harness, oracle_owner=owner,
                                        source_store=source_store).run(strategy, self.output_root)
