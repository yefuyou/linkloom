"""Small single-variable offline comparison extension; no Provider or Gold loader."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from linkloom.strategy.generation import validate_sources
from linkloom.strategy.models import ComparisonSummary, ContextCostDelta, MetricDelta, MetricEvidenceReference, canonical_hash, canonical_json, require_digest, require_fields
from linkloom.strategy.policy import DIMENSIONS, POLICY_VERSION, recommendation, render_procedure


def validate_public_input(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or any(token in key.casefold() for token in ("expected", "gold", "answer_key", "case_id", "ground_truth")):
                raise ValueError("evaluator-private fields cannot enter inference")
            validate_public_input(child)
    elif isinstance(value, list):
        for child in value:
            validate_public_input(child)
    canonical_json(value)


@dataclass(frozen=True)
class FrozenCase:
    case_key: str
    public_request_json: str
    tools_applicable: bool = True

    def __post_init__(self):
        require_digest(self.case_key)
        public = json.loads(self.public_request_json)
        if not isinstance(public, dict) or type(self.tools_applicable) is not bool:
            raise ValueError("invalid public case input/applicability")
        validate_public_input(public)
        if canonical_json(public) != self.public_request_json:
            raise ValueError("public input must be canonical")


@dataclass(frozen=True)
class FrozenSuite:
    cases: tuple[FrozenCase, ...]
    configuration_json: str
    proof_kind: str

    def __post_init__(self):
        if not isinstance(self.cases, tuple) or not self.cases or any(type(c) is not FrozenCase for c in self.cases):
            raise ValueError("frozen cases required")
        if len({c.case_key for c in self.cases}) != len(self.cases):
            raise ValueError("case order must be unique")
        configuration = json.loads(self.configuration_json)
        if set(configuration) != {"runtime", "tools", "retrieval", "base_prompt", "budgets", "model_abstraction", "seed", "evaluator", "oracle"}:
            raise ValueError("all shared condition owners must be frozen")
        for value in configuration.values():
            require_digest(value)
        if canonical_json(configuration) != self.configuration_json or self.proof_kind not in {"responsive_fake", "scripted_replay"}:
            raise ValueError("invalid frozen configuration/proof kind")

    @property
    def suite_sha256(self):
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class OfflineRequest:
    case_key: str
    public_request_json: str
    configuration_json: str
    tools_applicable: bool
    strategy_context: str

    def __post_init__(self):
        FrozenSuite((FrozenCase(self.case_key, self.public_request_json, self.tools_applicable),), self.configuration_json, "scripted_replay")
        if not isinstance(self.strategy_context, str) or len(self.strategy_context) > 4000 or len(self.strategy_context.encode("utf-8")) > 16000:
            raise ValueError("offline preview exceeds final context bounds")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**require_fields(cls, value))


@dataclass(frozen=True)
class ControlledObservationInputs:
    document_observations_json: str
    tool_observations_json: str
    schema_version: str = "strategy-controlled-observations/v1"

    def __post_init__(self):
        if self.schema_version != "strategy-controlled-observations/v1":
            raise ValueError("invalid controlled observation schema")
        for text in (self.document_observations_json, self.tool_observations_json):
            value = json.loads(text)
            if not isinstance(value, list) or canonical_json(value) != text:
                raise ValueError("controlled tool/document vectors must be canonical finite JSON arrays")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**require_fields(cls, value))


@dataclass(frozen=True)
class OfflineObservation:
    request_received: OfflineRequest
    final_json: str
    trajectory_json: str
    inputs: ControlledObservationInputs

    def __post_init__(self):
        if type(self.request_received) is not OfflineRequest:
            raise ValueError("actual harness-observed request required")
        if type(self.inputs) is not ControlledObservationInputs:
            raise ValueError("typed fixed tool/document observations required")
        ControlledObservationInputs.from_dict(self.inputs.to_dict())
        for text in (self.final_json, self.trajectory_json):
            if canonical_json(json.loads(text)) != text:
                raise ValueError("observed final/trajectory must be canonical finite JSON")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        values = require_fields(cls, value)
        values["request_received"] = OfflineRequest.from_dict(values["request_received"])
        values["inputs"] = ControlledObservationInputs.from_dict(values["inputs"])
        return cls(**values)


@dataclass(frozen=True)
class EvaluationBundle:
    root: Path
    manifest_sha256: str
    summary: ComparisonSummary


def shared_condition(observations):
    # v1 permits only context and evaluated final output to differ. The complete
    # trajectory and fixed document/tool vectors are controlled inputs, not an
    # unverified channel through which a harness can change another variable.
    return canonical_hash([{
        "request": {k: v for k, v in o.request_received.to_dict().items() if k != "strategy_context"},
        "inputs": o.inputs.to_dict(), "trajectory_sha256": canonical_hash(json.loads(o.trajectory_json)),
    } for o in observations])


class StrategyEvaluationRunner:
    def __init__(self, suite, *, harness, oracle_owner, source_store):
        from .strategy_oracle import OracleOwner
        if type(suite) is not FrozenSuite:
            raise ValueError("configured frozen suite required")
        if type(oracle_owner) is not OracleOwner or oracle_owner._suite != suite:
            raise ValueError("independently configured matching oracle owner required")
        self._suite, self._harness, self._oracle_owner, self._source_store = suite, harness, oracle_owner, source_store

    def run(self, strategy, output_root):
        validate_sources(strategy, self._source_store)
        if strategy.status != "candidate":
            raise ValueError("offline comparison requires a candidate")
        suite = self._suite
        FrozenSuite(**asdict(suite) | {"cases": suite.cases})
        context = render_procedure(strategy, "Candidate strategy under offline evaluation")
        observations = []
        for strategy_context in ("", context):
            branch = []
            for case in suite.cases:
                request = OfflineRequest(case.case_key, case.public_request_json, suite.configuration_json, case.tools_applicable, strategy_context)
                observed = self._harness(request)
                if type(observed) is not OfflineObservation:
                    raise ValueError("harness must return typed actual observation")
                observed = OfflineObservation.from_dict(observed.to_dict())
                if observed.request_received != request:
                    raise ValueError("actual harness-observed request differs from frozen condition")
                branch.append(observed)
            observations.append(tuple(branch))
        baseline, candidate = observations
        if shared_condition(baseline) != shared_condition(candidate):
            raise ValueError("paired evaluation is not single-variable")
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=False)
        artifacts = {}

        def write(name, payload):
            raw = canonical_json(payload).encode("utf-8")
            path = name + ".json"
            with (root / path).open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            artifacts[name] = {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
            return artifacts[name]["sha256"]

        for name, branch in (("baseline", baseline), ("candidate", candidate)):
            digest = write(name, [o.to_dict() for o in branch])
            write(name + "_seal", {"schema_version": "offline-strategy-observation-seal/v1", "sealed": True,
                                  "gold_available": False, "observation_sha256": digest})
        # Independent oracle owner reads BOTH seals, then issues authenticated outcomes.
        outcomes = self._oracle_owner.evaluate_and_seal(strategy, root)
        for name in ("oracle_outcomes", "oracle_outcomes_seal"):
            artifacts[name] = {"path": name + ".json", "sha256": hashlib.sha256((root / (name + ".json")).read_bytes()).hexdigest()}
        rows = []
        for case, base, proposed, result in zip(suite.cases, baseline, candidate, outcomes["cases"]):
            results = [result["baseline"], result["candidate"]]
            for dimension in DIMENSIONS:
                b, c = results[0][dimension], results[1][dimension]
                if "N/A" in (b, c) and (dimension != "retrieval_tools" or case.tools_applicable or b != c):
                    raise ValueError("undeclared dimension inapplicability")
                delta = int(c == "PASS") - int(b == "PASS") if b in {"PASS", "FAIL"} and c in {"PASS", "FAIL"} else None
                rows.append(MetricDelta(case.case_key, dimension, b, c, delta, "P1" if delta == -1 else None,
                                        MetricEvidenceReference.create(base.to_dict(), artifacts["baseline"]["sha256"], dimension, b),
                                        MetricEvidenceReference.create(proposed.to_dict(), artifacts["candidate"]["sha256"], dimension, c)))
        costs = tuple(ContextCostDelta.create(b.to_dict(), c.to_dict(), artifacts["baseline"]["sha256"], artifacts["candidate"]["sha256"])
                      for b, c in zip(baseline, candidate))
        summary = ComparisonSummary.create(
            schema_version="strategy-comparison/v1", strategy_id=strategy.strategy_id,
            suite_sha256=suite.suite_sha256, condition_sha256=shared_condition(baseline), policy_version=POLICY_VERSION,
            baseline_sha256=artifacts["baseline"]["sha256"], candidate_sha256=artifacts["candidate"]["sha256"],
            context_sha256=canonical_hash(context), proof_kind=suite.proof_kind, rows=tuple(rows),
            oracle_outcome_sha256=artifacts["oracle_outcomes"]["sha256"],
            context_chars=len(context), context_bytes=len(context.encode("utf-8")),
            recommendation=recommendation(rows, len(context), len(context.encode("utf-8")), suite.proof_kind), context_costs=costs)
        digest = write("comparison", summary.to_dict())
        write("comparison_seal", {"schema_version": "strategy-comparison-seal/v1", "sealed": True,
                                  "comparison_sha256": digest, "baseline_sha256": summary.baseline_sha256,
                                  "candidate_sha256": summary.candidate_sha256, "condition_sha256": summary.condition_sha256,
                                  "suite_sha256": summary.suite_sha256})
        manifest = {"schema_version": "strategy-comparison-manifest/v1", "results": [{"result_id": summary.result_id, "artifacts": artifacts}]}
        pin = write("manifest", manifest)
        return EvaluationBundle(root, pin, summary)
