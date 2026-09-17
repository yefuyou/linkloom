"""Trusted host oracle issuance, separate from comparison-producer hashes.

The host supplies the frozen suite and private integrity key before evaluation.
Neither is inferred from a result manifest. This is not an auth/RBAC service.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from linkloom.strategy.models import canonical_hash, canonical_json, require_digest
from linkloom.strategy.policy import DIMENSIONS, render_procedure

DOMAIN = b"linkloom/strategy-oracle-outcomes/v1\0"
OUTCOMES = {"PASS", "FAIL", "N/E", "REVIEW_REQUIRED", "N/A"}


@dataclass(frozen=True)
class OracleVerifier:
    _suite: object = field(repr=False)
    _key: bytes = field(repr=False)

    def __post_init__(self):
        from .strategy import FrozenSuite
        if type(self._suite) is not FrozenSuite or type(self._key) is not bytes or len(self._key) != 32:
            raise ValueError("oracle requires independently bootstrapped suite and 32-byte key")

    @property
    def issuer_id(self):
        return hashlib.sha256(DOMAIN + self._key + self._suite.suite_sha256.encode()).hexdigest()

    def verify(self, payload, seal):
        fields = {"schema_version", "strategy_id", "suite_sha256", "proof_kind", "evaluator_sha256", "oracle_sha256",
                  "baseline_sha256", "candidate_sha256", "condition_sha256", "context_sha256", "cases"}
        if not isinstance(payload, dict) or set(payload) != fields or payload["schema_version"] != "strategy-oracle-outcomes/v1":
            raise ValueError("invalid oracle outcome schema")
        for name in fields - {"schema_version", "proof_kind", "cases"}:
            require_digest(payload[name])
        suite = self._suite
        cfg = json.loads(suite.configuration_json)
        if (payload["suite_sha256"] != suite.suite_sha256 or payload["proof_kind"] != suite.proof_kind
                or payload["evaluator_sha256"] != cfg["evaluator"] or payload["oracle_sha256"] != cfg["oracle"]):
            raise ValueError("oracle outcomes differ from independent frozen bootstrap")
        cases = payload["cases"]
        if not isinstance(cases, list) or len(cases) != len(suite.cases):
            raise ValueError("incomplete oracle case matrix")
        for case, result in zip(suite.cases, cases):
            if not isinstance(result, dict) or set(result) != {"case_key", "baseline", "candidate"} or result["case_key"] != case.case_key:
                raise ValueError("oracle case order/identity mismatch")
            for branch in ("baseline", "candidate"):
                values = result[branch]
                if (not isinstance(values, dict) or set(values) != set(DIMENSIONS)
                        or any(type(v) is not str or v not in OUTCOMES for v in values.values())):
                    raise ValueError("invalid oracle dimension outcomes")
            for dimension in DIMENSIONS:
                b, c = result["baseline"][dimension], result["candidate"][dimension]
                if "N/A" in (b, c) and (dimension != "retrieval_tools" or case.tools_applicable or b != c):
                    raise ValueError("undeclared oracle inapplicability")
        digest = canonical_hash(payload)
        mac = hmac.new(self._key, DOMAIN + canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()
        expected = {"schema_version": "strategy-oracle-outcomes-seal/v1", "sealed": True,
                    "outcomes_sha256": digest, "issuer_id": self.issuer_id, "mac_sha256": mac}
        if isinstance(seal, dict) and "mac_sha256" in seal:
            require_digest(seal["mac_sha256"])
        if (not isinstance(seal, dict) or set(seal) != set(expected)
                or canonical_json({k: v for k, v in seal.items() if k != "mac_sha256"}) != canonical_json({k: v for k, v in expected.items() if k != "mac_sha256"})
                or type(seal["mac_sha256"]) is not str or not hmac.compare_digest(seal["mac_sha256"], mac)):
            raise ValueError("oracle integrity signature invalid")
        return payload


@dataclass(frozen=True)
class OracleAuthority:
    verifiers: tuple[OracleVerifier, ...] = field(repr=False)

    def __post_init__(self):
        if (type(self.verifiers) is not tuple or not self.verifiers or any(type(v) is not OracleVerifier for v in self.verifiers)
                or len({v.issuer_id for v in self.verifiers}) != len(self.verifiers)):
            raise ValueError("independently bootstrapped oracle verifiers required")

    def verify(self, payload, seal):
        selected = [v for v in self.verifiers if isinstance(seal, dict) and v.issuer_id == seal.get("issuer_id")]
        if len(selected) != 1:
            raise ValueError("unknown oracle issuer; result cannot register trust")
        return selected[0].verify(payload, seal)


@dataclass(frozen=True)
class OracleOwner:
    _suite: object = field(repr=False)
    _evaluator: object = field(repr=False)
    _key: bytes = field(repr=False)

    def __post_init__(self):
        OracleVerifier(self._suite, self._key)
        if not callable(self._evaluator):
            raise ValueError("configured private oracle required")

    def evaluate_and_seal(self, strategy, root):
        from .strategy import OfflineObservation, OfflineRequest, shared_condition
        root, suite = Path(root), self._suite
        context = render_procedure(strategy, "Candidate strategy under offline evaluation")
        branches, pins = [], {}
        # Read/validate BOTH actual sealed artifacts before private oracle access.
        for name, text in (("baseline", ""), ("candidate", context)):
            raw = (root / (name + ".json")).read_bytes()
            pins[name] = hashlib.sha256(raw).hexdigest()
            seal = json.loads((root / (name + "_seal.json")).read_bytes())
            expected = {"schema_version": "offline-strategy-observation-seal/v1", "sealed": True,
                        "gold_available": False, "observation_sha256": pins[name]}
            values = json.loads(raw)
            if canonical_json(seal) != canonical_json(expected) or not isinstance(values, list) or len(values) != len(suite.cases):
                raise ValueError("oracle requires complete paired observation seals")
            branch = tuple(OfflineObservation.from_dict(v) for v in values)
            for case, observed in zip(suite.cases, branch):
                request = OfflineRequest(case.case_key, case.public_request_json, suite.configuration_json, case.tools_applicable, text)
                if observed.request_received != request:
                    raise ValueError("oracle observation differs from independently frozen request")
            branches.append(branch)
        baseline, candidate = branches
        condition = shared_condition(baseline)
        if condition != shared_condition(candidate):
            raise ValueError("oracle paired shared condition mismatch")
        cfg = json.loads(suite.configuration_json)
        cases = [{"case_key": case.case_key, "baseline": self._evaluator(case, b), "candidate": self._evaluator(case, c)}
                 for case, b, c in zip(suite.cases, baseline, candidate)]
        payload = {"schema_version": "strategy-oracle-outcomes/v1", "strategy_id": strategy.strategy_id,
                   "suite_sha256": suite.suite_sha256, "proof_kind": suite.proof_kind,
                   "evaluator_sha256": cfg["evaluator"], "oracle_sha256": cfg["oracle"],
                   "baseline_sha256": pins["baseline"], "candidate_sha256": pins["candidate"],
                   "condition_sha256": condition, "context_sha256": canonical_hash(context), "cases": cases}
        verifier = OracleVerifier(suite, self._key)
        seal = {"schema_version": "strategy-oracle-outcomes-seal/v1", "sealed": True,
                "outcomes_sha256": canonical_hash(payload), "issuer_id": verifier.issuer_id,
                "mac_sha256": hmac.new(self._key, DOMAIN + canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()}
        verifier.verify(payload, seal)
        for name, value in (("oracle_outcomes", payload), ("oracle_outcomes_seal", seal)):
            with (root / (name + ".json")).open("xb") as handle:
                handle.write(canonical_json(value).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        return payload
