"""Read-only bootstrap-pinned comparison resolution; request metadata is not proof."""
import hashlib
import json
from pathlib import Path

from .models import ComparisonSummary, ContextCostDelta, MetricEvidenceReference, canonical_hash, canonical_json, require_digest
from .policy import DIMENSIONS, render_procedure


class ComparisonAuthority:
    def __init__(self, manifest_path, *, manifest_sha256, oracle_authority):
        from linkloom.evaluation.strategy_oracle import OracleAuthority
        if type(oracle_authority) is not OracleAuthority:
            raise ValueError("independent oracle authority required; producer pins are insufficient")
        require_digest(manifest_sha256)
        self._oracle_authority = oracle_authority
        self._path = Path(manifest_path)
        self._pin = manifest_sha256
        self._manifest()

    def _manifest(self):
        raw = self._path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self._pin:
            raise ValueError("comparison authority manifest pin changed")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"schema_version", "results"} or value["schema_version"] != "strategy-comparison-manifest/v1":
            raise ValueError("invalid comparison manifest")
        if not isinstance(value["results"], list) or not value["results"]:
            raise ValueError("comparison registrations required")
        seen = set()
        for entry in value["results"]:
            if not isinstance(entry, dict) or set(entry) != {"result_id", "artifacts"} or entry["result_id"] in seen:
                raise ValueError("invalid or duplicate comparison registration")
            require_digest(entry["result_id"])
            seen.add(entry["result_id"])
        return value

    def _read(self, artifact):
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise ValueError("invalid comparison artifact locator")
        require_digest(artifact["sha256"])
        if not isinstance(artifact["path"], str):
            raise ValueError("invalid artifact path")
        root = self._path.resolve().parent
        path = (root / artifact["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("artifact escaped configured root")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise ValueError("stale or rewritten comparison artifact")
        return json.loads(raw)

    def resolve(self, result_id):
        from linkloom.evaluation.strategy import FrozenCase, FrozenSuite, OfflineObservation, shared_condition
        entries = [e for e in self._manifest()["results"] if e["result_id"] == result_id]
        if len(entries) != 1:
            raise ValueError("unregistered evaluator comparison")
        artifacts = entries[0]["artifacts"]
        if not isinstance(artifacts, dict) or set(artifacts) != {"baseline", "candidate", "baseline_seal", "candidate_seal", "comparison", "comparison_seal", "oracle_outcomes", "oracle_outcomes_seal"}:
            raise ValueError("complete paired evidence and seals required")
        payloads = {name: self._read(locator) for name, locator in artifacts.items()}
        summary = ComparisonSummary.from_dict(payloads["comparison"])
        if summary.result_id != result_id:
            raise ValueError("comparison registration identity mismatch")
        oracle = self._oracle_authority.verify(payloads["oracle_outcomes"], payloads["oracle_outcomes_seal"])
        if summary.oracle_outcome_sha256 != artifacts["oracle_outcomes"]["sha256"]:
            raise ValueError("comparison references a different oracle outcome receipt")
        for field in ("strategy_id", "suite_sha256", "proof_kind", "baseline_sha256", "candidate_sha256", "condition_sha256", "context_sha256"):
            if getattr(summary, field) != oracle[field]:
                raise ValueError("comparison differs from independently issued oracle identity/proof")
        matrix = {r["case_key"]: r for r in oracle["cases"]}
        for row in summary.rows:
            if row.case_key not in matrix or any(getattr(row, name) != matrix[row.case_key][name][row.dimension] for name in ("baseline", "candidate")):
                raise ValueError("comparison outcomes differ from independently issued oracle truth")
        observations = []
        for name in ("baseline", "candidate"):
            raw = payloads[name]
            if not isinstance(raw, list) or not raw:
                raise ValueError("missing paired observations")
            seal = payloads[name + "_seal"]
            expected_seal = {"schema_version": "offline-strategy-observation-seal/v1", "sealed": True,
                             "gold_available": False, "observation_sha256": artifacts[name]["sha256"]}
            if canonical_json(seal) != canonical_json(expected_seal):
                raise ValueError("observed seal does not bind original result")
            if getattr(summary, name + "_sha256") != artifacts[name]["sha256"]:
                raise ValueError("metrics bind a different observed result")
            observations.append(tuple(OfflineObservation.from_dict(o) for o in raw))
        base, candidate = observations
        if len(base) != len(candidate) or shared_condition(base) != shared_condition(candidate) or shared_condition(base) != summary.condition_sha256:
            raise ValueError("paired shared condition identity mismatch")
        cases = tuple(FrozenCase(o.request_received.case_key, o.request_received.public_request_json, o.request_received.tools_applicable) for o in base)
        configuration = base[0].request_received.configuration_json
        suite = FrozenSuite(cases, configuration, summary.proof_kind)
        if suite.suite_sha256 != summary.suite_sha256 or any(o.request_received.configuration_json != configuration for o in base):
            raise ValueError("suite/evaluator/configuration identity mismatch")
        for b, c in zip(base, candidate):
            if b.request_received.strategy_context != "" or canonical_hash(c.request_received.strategy_context) != summary.context_sha256:
                raise ValueError("baseline or candidate context is not the declared sole variable")
            text = c.request_received.strategy_context
            if len(text) != summary.context_chars or len(text.encode("utf-8")) != summary.context_bytes:
                raise ValueError("context cost proxy does not match model-observed text")
        if {r.case_key for r in summary.rows} != {c.case_key for c in cases}:
            raise ValueError("omitted or foreign case results")
        for index, case in enumerate(cases):
            for row in (r for r in summary.rows if r.case_key == case.case_key):
                for name, reference, observed, outcome in (("baseline", row.baseline_evidence, base[index], row.baseline),
                                                            ("candidate", row.candidate_evidence, candidate[index], row.candidate)):
                    if reference != MetricEvidenceReference.create(observed.to_dict(), artifacts[name]["sha256"], row.dimension, outcome):
                        raise ValueError("per-metric evidence is not the exact sealed case observation")
            cost, = (r for r in summary.context_costs if r.case_key == case.case_key)
            if cost != ContextCostDelta.create(base[index].to_dict(), candidate[index].to_dict(), summary.baseline_sha256, summary.candidate_sha256):
                raise ValueError("per-case cost/evidence differs from actual model-observed context")
            for row in (r for r in summary.rows if r.case_key == case.case_key):
                if "N/A" in (row.baseline, row.candidate) and (row.dimension != "retrieval_tools" or case.tools_applicable or row.baseline != row.candidate):
                    raise ValueError("undeclared evaluator dimension omission")
        expected_seal = {
            "schema_version": "strategy-comparison-seal/v1", "sealed": True,
            "comparison_sha256": artifacts["comparison"]["sha256"], "baseline_sha256": summary.baseline_sha256,
            "candidate_sha256": summary.candidate_sha256, "condition_sha256": summary.condition_sha256,
            "suite_sha256": summary.suite_sha256,
        }
        if canonical_json(payloads["comparison_seal"]) != canonical_json(expected_seal):
            raise ValueError("comparison seal mismatch")
        return summary

    def validate(self, strategy, summary):
        if type(summary) is not ComparisonSummary or self.resolve(summary.result_id) != summary:
            raise ValueError("comparison differs from configured evaluator authority")
        if summary.strategy_id != strategy.strategy_id or summary.context_sha256 != canonical_hash(render_procedure(strategy, "Candidate strategy under offline evaluation")):
            raise ValueError("comparison does not evaluate this exact Strategy context")
        return summary
