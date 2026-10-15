"""Read-only authority over evaluator-produced, sealed local artifacts.

The manifest digest is trusted application bootstrap configuration, not request
input. Requests can select a result ID but cannot register artifacts or supply
findings/catalogs. Hashes provide integrity relative to that configured root;
this is not a sandbox against replacement of application configuration/code.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .models import EvaluatorFinding, ExperienceProvenance, ReflectionInput


REQUIRED_DIMENSIONS = frozenset({"runtime", "infrastructure", "provider_availability",
                                "contract", "grounding", "semantic"})
DIMENSION_STATES = frozenset({"PASS", "FAIL", "N/E", "BLOCKED", "PARTIAL", "REVIEW_REQUIRED"})
RESULT_SCHEMA = "complete-evaluation-result/v1"
RULE = "explicit_rejection_requires_evidence/v1"


class EvaluationAuthorityError(ValueError):
    pass


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise EvaluationAuthorityError("authority artifact must be JSON") from exc
    if not isinstance(value, dict):
        raise EvaluationAuthorityError("authority artifact must be an object")
    return value


def _fields(value: dict[str, Any], names: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != names:
        raise EvaluationAuthorityError(f"{label} must use its complete closed schema")


@dataclass(frozen=True)
class AuthoritativeEvaluationResult:
    result_id: str
    projection: ReflectionInput
    dimensions: tuple[tuple[str, str], ...]
    provenance: ExperienceProvenance | None
    terminal_run: bool
    provider_outcome_known: bool
    gold_leakage_free: bool
    supporting_observations_valid: bool
    _issuer: object = field(repr=False, compare=False)

    @property
    def generation_allowed(self) -> bool:
        states = dict(self.dimensions)
        from .policy import EXPERIENCE_TEMPLATES
        signals = set(self.projection.task_characteristics)
        return (self.terminal_run and self.provider_outcome_known
                and self.gold_leakage_free and self.supporting_observations_valid
                and states["semantic"] == "FAIL"
                and all(states[name] == "PASS" for name in REQUIRED_DIMENSIONS - {"semantic"})
                and {"team_decision", "current_decision", "compared_options"}.issubset(signals)
                and not signals.intersection(EXPERIENCE_TEMPLATES[RULE].applicability.exclude_when)
                and self.provenance is not None and len(self.projection.findings) == 1
                and self.projection.findings[0].code == RULE
                and bool(self.projection.findings[0].source_evidence_refs))


class EvaluationAuthority:
    """Resolve results against a pinned evaluator-owned manifest; never writes."""

    def __init__(self, manifest_path: str | Path, *, manifest_sha256: str):
        self._manifest_path = Path(manifest_path).resolve(strict=True)
        self._manifest_sha256 = manifest_sha256
        raw = self._manifest_path.read_bytes()
        if _hash(raw) != manifest_sha256:
            raise EvaluationAuthorityError("authority manifest identity mismatch")
        manifest = _object(raw)
        _fields(manifest, {"schema_version", "results"}, "authority manifest")
        if manifest["schema_version"] != "evaluation-authority-manifest/v1":
            raise EvaluationAuthorityError("authority manifest schema mismatch")
        entries = {}
        if not isinstance(manifest["results"], list):
            raise EvaluationAuthorityError("authority results must be an array")
        for entry in manifest["results"]:
            if not isinstance(entry, dict):
                raise EvaluationAuthorityError("authority entry must be an object")
            _fields(entry, {"result_id", "run_id", "artifacts"}, "authority entry")
            if entry["result_id"] in entries:
                raise EvaluationAuthorityError("duplicate authority result identity")
            _fields(entry["artifacts"], {"observed", "observed_seal", "evaluation", "evaluation_seal"}, "artifacts")
            artifacts = {}
            for name, artifact in entry["artifacts"].items():
                _fields(artifact, {"path", "sha256"}, "artifact locator")
                path = (self._manifest_path.parent / artifact["path"]).resolve(strict=True)
                if not path.is_relative_to(self._manifest_path.parent):
                    raise EvaluationAuthorityError("authority artifact escaped root")
                artifacts[name] = (path, artifact["sha256"])
            entries[entry["result_id"]] = (entry["run_id"], MappingProxyType(artifacts))
        self._entries = MappingProxyType(entries)
        self._issuer = object()

    def _read(self, artifacts: Any, name: str) -> tuple[dict[str, Any], str]:
        path, expected = artifacts[name]
        raw = path.read_bytes()
        if _hash(raw) != expected:
            raise EvaluationAuthorityError(f"sealed {name} artifact identity mismatch or stale artifact")
        return _object(raw), expected

    def resolve(self, result_id: str) -> AuthoritativeEvaluationResult:
        if _hash(self._manifest_path.read_bytes()) != self._manifest_sha256:
            raise EvaluationAuthorityError("authority manifest changed")
        if result_id not in self._entries:
            raise EvaluationAuthorityError("unknown authoritative result identity")
        run_id, artifacts = self._entries[result_id]
        observed, observed_hash = self._read(artifacts, "observed")
        seal, seal_hash = self._read(artifacts, "observed_seal")
        evaluation, evaluation_hash = self._read(artifacts, "evaluation")
        evaluation_seal, _ = self._read(artifacts, "evaluation_seal")
        _fields(seal, {"schema_version", "sealed", "gold_available", "observed_summary_sha256"}, "run seal")
        if (seal["schema_version"] != "deepseek-real-smoke-seal/v1"
                or seal["sealed"] is not True or seal["gold_available"] is not False
                or seal["observed_summary_sha256"] != observed_hash):
            raise EvaluationAuthorityError("run seal identity mismatch")
        run_ids = [observed.get("run_id")]
        for name in ("result", "runtime_state"):
            nested = observed.get(name)
            if isinstance(nested, dict) and "run_id" in nested:
                run_ids.append(nested["run_id"])
        run_ids = [value for value in run_ids if value is not None]
        if not run_ids or any(value != run_id for value in run_ids):
            raise EvaluationAuthorityError("source run identity mismatch")
        if observed.get("schema_version") not in {"deepseek-real-smoke-observed/v1", "deepseek-business-observed/v1"}:
            raise EvaluationAuthorityError("sealed run schema mismatch")
        refs = observed.get("visible_evidence_refs")
        if (not isinstance(refs, list)
                or any(not isinstance(ref, str) or not ref.startswith("ev_") for ref in refs)
                or len(refs) != len(set(refs))):
            raise EvaluationAuthorityError("sealed evidence catalog is invalid")
        catalog = tuple(f"{run_id}:{ref}" for ref in refs)
        from .policy import validate_source_identifiers
        validate_source_identifiers(run_id, catalog)
        # Typed observations, not same-run membership, establish lesson linkage.
        observations = observed.get("supporting_observations", [])
        if not isinstance(observations, list):
            raise EvaluationAuthorityError("supporting observations must be an array")
        observation_refs = {}
        for observation in observations:
            _fields(observation, {"observation_id", "source_evidence_refs", "model_observed"}, "supporting observation")
            identity = observation["observation_id"]
            linked = observation["source_evidence_refs"]
            if (not isinstance(identity, str) or not re.fullmatch(r"observation_[A-Za-z0-9_]+", identity)
                    or identity in observation_refs or not isinstance(linked, list)
                    or any(not isinstance(ref, str) or ref not in catalog for ref in linked)
                    or len(linked) != len(set(linked)) or not isinstance(observation["model_observed"], bool)):
                raise EvaluationAuthorityError("invalid supporting observation linkage")
            observation_refs[identity] = tuple(linked) if observation["model_observed"] else ()
        result_owner = observed.get("result")
        status_owners = [result_owner] + [observed[name] for name in ("runtime_state", "runtime") if isinstance(observed.get(name), dict)]
        terminal_run = all(isinstance(owner, dict) and isinstance(owner.get("status"), str)
                           and owner["status"] in {"completed", "failed", "cancelled"} for owner in status_owners)
        provider_outcome_known = not observed.get("provider_error")
        for owner in (observed, observed.get("result", {}), observed.get("runtime_state", {}), observed.get("runtime", {})):
            if isinstance(owner, dict) and any(isinstance(owner.get(key), str) and owner.get(key) in {"MODEL_TRANSIENT_FAILURE", "unknown_provider_outcome"}
                                               for key in ("error_code", "model_terminal_status", "failure_code")):
                provider_outcome_known = False
            error = owner.get("error") if isinstance(owner, dict) else None
            if isinstance(error, dict):
                details = error.get("details")
                if (error.get("code") == "MODEL_TRANSIENT_FAILURE"
                        or isinstance(details, dict) and details.get("outcome") == "unknown_provider_outcome"):
                    provider_outcome_known = False
        _fields(evaluation, {"schema_version", "run_id", "completed", "observed_summary_sha256",
                            "observed_seal_sha256", "source_evaluator_sha256", "dimensions",
                            "findings", "task_characteristics", "gold_leakage_detected"}, "evaluation result")
        if (evaluation["schema_version"] != RESULT_SCHEMA or evaluation["completed"] is not True
                or evaluation["run_id"] != run_id
                or evaluation["observed_summary_sha256"] != observed_hash
                or evaluation["observed_seal_sha256"] != seal_hash):
            raise EvaluationAuthorityError("complete evaluator/run identity mismatch")
        _fields(evaluation_seal, {"schema_version", "sealed", "run_id", "evaluation_sha256"}, "evaluation seal")
        if (evaluation_seal["schema_version"] != "complete-evaluation-seal/v1"
                or evaluation_seal["sealed"] is not True or evaluation_seal["run_id"] != run_id
                or evaluation_seal["evaluation_sha256"] != evaluation_hash
                or result_id != f"evaluation_{evaluation_hash}"):
            raise EvaluationAuthorityError("sealed evaluation result identity mismatch")
        dimensions = evaluation["dimensions"]
        if (not isinstance(dimensions, dict) or set(dimensions) != REQUIRED_DIMENSIONS
                or any(not isinstance(state, str) or state not in DIMENSION_STATES for state in dimensions.values())):
            raise EvaluationAuthorityError("missing required evaluation dimension or invalid state")
        if (not isinstance(evaluation["source_evaluator_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", evaluation["source_evaluator_sha256"])):
            raise EvaluationAuthorityError("source evaluator identity must be a digest")
        if not isinstance(evaluation["task_characteristics"], list):
            raise EvaluationAuthorityError("task characteristics must be an array")
        if not isinstance(evaluation["gold_leakage_detected"], bool):
            raise EvaluationAuthorityError("Gold leakage disposition must be explicit")
        gold_leakage_free = (evaluation["gold_leakage_detected"] is False
                             and "gold_leakage_detected" not in evaluation["task_characteristics"])
        findings = []
        all_links_valid = True
        if not isinstance(evaluation["findings"], list):
            raise EvaluationAuthorityError("complete findings must be an array")
        for raw_finding in evaluation["findings"]:
            _fields(raw_finding, {"finding_id", "dimension", "outcome", "code", "source_evidence_refs", "supporting_observation_ids"}, "finding")
            # BLOCKED is a complete dimension state; legacy finding contract uses N/E.
            state = dimensions.get(raw_finding["dimension"])
            if raw_finding["outcome"] != ("N/E" if state == "BLOCKED" else state):
                raise EvaluationAuthorityError("finding disagrees with complete dimension state")
            observation_ids = raw_finding["supporting_observation_ids"]
            if (not isinstance(observation_ids, list) or any(not isinstance(i, str) for i in observation_ids)
                    or len(observation_ids) != len(set(observation_ids))):
                raise EvaluationAuthorityError("invalid supporting observation identities")
            safe_finding = {key: value for key, value in raw_finding.items() if key != "supporting_observation_ids"}
            finding = EvaluatorFinding.from_dict(safe_finding | {"evaluator_artifact_sha256": evaluation_hash})
            if not set(finding.source_evidence_refs).issubset(catalog):
                raise EvaluationAuthorityError("evidence ref absent from authoritative sealed run")
            if finding.code == RULE and finding.source_evidence_refs:
                if not observations:
                    all_links_valid = False
                else:
                    if not observation_ids or any(i not in observation_refs for i in observation_ids):
                        raise EvaluationAuthorityError("supporting observation identity has no provenance link")
                    linked_refs = {ref for i in observation_ids for ref in observation_refs[i]}
                    if set(finding.source_evidence_refs) != linked_refs or not linked_refs:
                        raise EvaluationAuthorityError("finding evidence differs from model-observed supporting observation link")
            findings.append(finding)
        for dimension, state in dimensions.items():
            if state != "PASS" and not any(f.dimension == dimension for f in findings):
                raise EvaluationAuthorityError("omitted blocking dimension companion finding")
        projection = ReflectionInput(
            "reflection-input/v1", run_id, observed_hash, evaluation_hash, RESULT_SCHEMA,
            catalog, tuple(findings), tuple(evaluation["task_characteristics"]),
        )
        semantic = tuple(f for f in findings if f.code == RULE and f.dimension == "semantic" and f.outcome == "FAIL")
        provenance = ExperienceProvenance(
            source_run_id=run_id, observed_summary_sha256=observed_hash,
            evaluator_artifact_sha256=evaluation_hash, evaluator_artifact_schema_version=RESULT_SCHEMA,
            finding_ids=tuple(f.finding_id for f in semantic),
            source_evidence_refs=tuple(ref for f in semantic for ref in f.source_evidence_refs),
            available_source_evidence_refs=catalog,
            evaluation_result_id=result_id, observed_seal_sha256=seal_hash,
            source_identity=observed_hash,
        ) if semantic and all(f.source_evidence_refs for f in semantic) else None
        return AuthoritativeEvaluationResult(result_id, projection, tuple(sorted(dimensions.items())), provenance,
                                             terminal_run, provider_outcome_known, gold_leakage_free,
                                             all_links_valid, self._issuer)

    def validate_result(self, result: AuthoritativeEvaluationResult) -> AuthoritativeEvaluationResult:
        if not isinstance(result, AuthoritativeEvaluationResult) or result._issuer is not self._issuer:
            raise EvaluationAuthorityError("Reflection requires this authority's evaluated result")
        resolved = self.resolve(result.result_id)
        if resolved != result:
            raise EvaluationAuthorityError("caller-rewritten authoritative result")
        return resolved

    def validate_record(self, record: Any) -> None:
        if len(record.provenance) != 1:
            raise EvaluationAuthorityError("record requires authoritative provenance")
        provenance = record.provenance[0]
        result = self.resolve(provenance.evaluation_result_id)
        if not result.generation_allowed or len(result.projection.findings) != 1:
            raise EvaluationAuthorityError("complete evaluator does not authorize generation")
        from .policy import EXPERIENCE_TEMPLATES
        if set(result.projection.task_characteristics).intersection(EXPERIENCE_TEMPLATES[RULE].applicability.exclude_when):
            raise EvaluationAuthorityError("applicability exclusion forbids reflection")
        if (provenance != result.provenance or record.source_run_ids != (result.projection.source_run_id,)
                or record.source_evidence_refs != result.provenance.source_evidence_refs
                or record.generation_rule_id != RULE):
            raise EvaluationAuthorityError("provenance differs from authoritative sealed evidence")


@dataclass(frozen=True)
class HistoricalReviewResult(AuthoritativeEvaluationResult):
    """Distinct reviewed receipt: absent evaluator dimensions stay absent."""
    team_decision_guard_valid: bool

    @property
    def generation_allowed(self) -> bool:
        from .policy import EXPERIENCE_TEMPLATES
        signals = set(self.projection.task_characteristics)
        return (dict(self.dimensions) == {"infrastructure": "PASS", "grounding": "PASS", "semantic": "FAIL"}
                and self.terminal_run and self.provider_outcome_known
                and self.gold_leakage_free and self.supporting_observations_valid
                and self.team_decision_guard_valid and self.provenance is not None
                and "gold_leakage_detected" not in signals
                and {"team_decision", "current_decision", "compared_options"}.issubset(signals)
                and not signals.intersection(EXPERIENCE_TEMPLATES[RULE].applicability.exclude_when)
                and len(self.projection.findings) == 1
                and self.projection.findings[0].code == RULE)


class HistoricalReviewAuthority(EvaluationAuthority):
    """User-approved bootstrap-pinned historical review compatibility only.

    Not a complete evaluator and not a request registration API. It verifies
    immutable source files, their existing dispositions, and typed upstream
    guards. It never fills missing dimensions or re-scores semantic answers.
    Source locators are trusted bootstrap metadata and never enter Experience.
    """

    def __init__(self, manifest_path: str | Path, *, manifest_sha256: str, artifact_root: str | Path):
        self._manifest_path = Path(manifest_path).resolve(strict=True)
        self._manifest_sha256 = manifest_sha256
        self._artifact_root = Path(artifact_root).resolve(strict=True)
        raw = self._manifest_path.read_bytes()
        if _hash(raw) != manifest_sha256:
            raise EvaluationAuthorityError("historical authority manifest identity mismatch")
        manifest = _object(raw)
        _fields(manifest, {"schema_version", "results"}, "historical authority manifest")
        if manifest["schema_version"] != "historical-review-authority-manifest/v1" or not isinstance(manifest["results"], list):
            raise EvaluationAuthorityError("historical authority schema mismatch")
        entries = {}
        for entry in manifest["results"]:
            _fields(entry, {"result_id", "run_id", "receipt", "sources"}, "historical authority entry")
            if entry["result_id"] in entries:
                raise EvaluationAuthorityError("duplicate historical result identity")
            _fields(entry["sources"], {"observed", "observed_seal", "evaluator", "review_record"}, "historical sources")
            locators = {}
            for name, locator in (entry["sources"] | {"receipt": entry["receipt"]}).items():
                _fields(locator, {"path", "sha256"}, "historical artifact locator")
                root = self._manifest_path.parent if name == "receipt" else self._artifact_root
                path = (root / locator["path"]).resolve(strict=True)
                if not path.is_relative_to(root):
                    raise EvaluationAuthorityError("historical source escaped configured root")
                locators[name] = (path, locator["sha256"])
            entries[entry["result_id"]] = (entry["run_id"], MappingProxyType(locators))
        self._entries = MappingProxyType(entries)
        self._issuer = object()

    def resolve(self, result_id: str) -> HistoricalReviewResult:
        if _hash(self._manifest_path.read_bytes()) != self._manifest_sha256:
            raise EvaluationAuthorityError("historical authority manifest changed")
        if result_id not in self._entries:
            raise EvaluationAuthorityError("unknown authoritative historical receipt")
        run_id, locators = self._entries[result_id]
        receipt, receipt_hash = self._read(locators, "receipt")
        observed, observed_hash = self._read(locators, "observed")
        seal, seal_hash = self._read(locators, "observed_seal")
        evaluator, evaluator_hash = self._read(locators, "evaluator")
        review_path, review_hash = locators["review_record"]
        # Integrity only: do not parse or forward review prose/expected values.
        if _hash(review_path.read_bytes()) != review_hash:
            raise EvaluationAuthorityError("historical review record identity mismatch or stale artifact")
        _fields(receipt, {"schema_version", "source_run_id", "observed_summary_sha256", "observed_seal_sha256",
                          "source_evaluator_sha256", "source_evaluator_schema_version", "review_record_sha256",
                          "review_source", "receipt_created_at", "disposition", "source_disposition",
                          "finding", "task_characteristics", "gold_leakage_detected", "observation_selector"}, "historical receipt")
        if (receipt["schema_version"] != "historical-review-receipt/v1"
                or result_id != "historicalreview_" + receipt_hash
                or receipt["source_run_id"] != run_id
                or receipt["observed_summary_sha256"] != observed_hash
                or receipt["observed_seal_sha256"] != seal_hash
                or receipt["source_evaluator_sha256"] != evaluator_hash
                or receipt["source_evaluator_schema_version"] != evaluator.get("schema_version")
                or receipt["review_record_sha256"] != review_hash
                or receipt["review_source"] != "existing_independent_review"):
            raise EvaluationAuthorityError("historical review/source identity mismatch")
        from .models import _require_timestamp
        _require_timestamp(receipt["receipt_created_at"], "historical receipt_created_at")
        if receipt["gold_leakage_detected"] not in (True, False) or not isinstance(receipt["gold_leakage_detected"], bool):
            raise EvaluationAuthorityError("historical Gold leakage disposition must be explicit")
        if (seal.get("schema_version") != "deepseek-real-smoke-seal/v1" or seal.get("sealed") is not True
                or seal.get("gold_available") is not False or seal.get("observed_summary_sha256") != observed_hash):
            raise EvaluationAuthorityError("historical observed seal invalid")
        if observed.get("schema_version") not in {"deepseek-real-smoke-observed/v1", "deepseek-business-observed/v1"}:
            raise EvaluationAuthorityError("historical observed schema invalid")
        owners = (observed, observed.get("result", {}), observed.get("runtime_state", {}))
        source_ids = [owner["run_id"] for owner in owners if isinstance(owner, dict) and "run_id" in owner]
        if not source_ids or any(identity != run_id for identity in source_ids):
            raise EvaluationAuthorityError("historical run identity mismatch")
        visible = observed.get("visible_evidence_refs")
        if not isinstance(visible, list) or any(not isinstance(ref, str) for ref in visible) or len(visible) != len(set(visible)):
            raise EvaluationAuthorityError("historical evidence catalog invalid")
        catalog = tuple(f"{run_id}:{ref}" for ref in visible)
        from .policy import validate_source_identifiers
        validate_source_identifiers(run_id, catalog)
        raw_finding = receipt["finding"]
        _fields(raw_finding, {"finding_id", "dimension", "outcome", "code", "source_evidence_refs"}, "historical finding")
        finding = EvaluatorFinding.from_dict(raw_finding | {"evaluator_artifact_sha256": receipt_hash})
        disposition = receipt["disposition"]
        if disposition == "supported_causal_semantic_failure":
            source_fields = {"infrastructure", "grounding", "semantic"}
            if (evaluator.get("schema_version") != "deepseek-posthoc-evaluation/v1"
                    or evaluator.get("correctness", {}).get("rejected_alternatives") is not False
                    or finding.code != RULE or finding.dimension != "semantic" or finding.outcome != "FAIL"
                    or receipt["observation_selector"] != "final_rejected_alternatives"):
                raise EvaluationAuthorityError("historical causal finding lacks existing evaluator basis")
        elif disposition == "provider_incident":
            source_fields = {"terminal_status"}
            if (evaluator.get("schema_version") != "deepseek-business-case-run/v1"
                    or finding.code != "provider_transient_before_final/v1"
                    or finding.dimension != "provider_availability" or finding.outcome != "PARTIAL"
                    or finding.source_evidence_refs or receipt["observation_selector"] != "none"):
                raise EvaluationAuthorityError("historical provider incident disposition invalid")
        elif disposition == "unresolved_semantic_cause":
            source_fields = {"infrastructure", "contract", "grounding", "overall"}
            if (evaluator.get("schema_version") != "deepseek-business-posthoc/v1"
                    or evaluator.get("overall") != "POSTHOC_REVIEW_REQUIRED"
                    or finding.code != "semantic_mismatch_cause_unresolved/v1"
                    or finding.dimension != "semantic" or finding.outcome != "REVIEW_REQUIRED"
                    or finding.source_evidence_refs or receipt["observation_selector"] != "none"):
                raise EvaluationAuthorityError("historical unresolved disposition invalid")
        else:
            raise EvaluationAuthorityError("historical disposition not registered")
        original_states = {name: evaluator.get(name) for name in source_fields}
        if receipt["source_disposition"] != original_states or any(not isinstance(state, str) for state in original_states.values()):
            raise EvaluationAuthorityError("historical evaluator disposition changed or missing dimensions backfilled")
        runtime = observed.get("runtime", {})
        result = observed.get("result", {})
        state = observed.get("runtime_state", {})
        terminal = all(owner.get("status") in {"completed", "failed", "cancelled"} for owner in (runtime, result, state))
        provider_known = (runtime.get("error") is None and result.get("error") is None and state.get("error") is None
                          and runtime.get("status") == "completed"
                          and isinstance(runtime.get("provider_requests"), int) and runtime["provider_requests"] > 0)
        team_guard = False
        linked = False
        provenance = None
        if disposition == "supported_causal_semantic_failure":
            review = result.get("review", {})
            checks = review.get("checks", []) if isinstance(review, dict) else []
            team = observed.get("team_decision_result")
            # Reuse the upstream contract owner; this is not semantic scoring.
            from linkloom.agents.team_decision import TeamDecisionResult
            try:
                TeamDecisionResult.from_dict(team)
                team_guard = (isinstance(checks, list) and bool(checks)
                              and all(check.get("status") == "pass" for check in checks)
                              and any(check.get("result_type") == "subject" for check in checks))
            except (ValueError, TypeError):
                team_guard = False
            if team_guard:
                final_refs = {f"{run_id}:{ref}" for item in team["rejected_alternatives"] for ref in item["evidence_refs"]}
                model_call_ids = {call for turn in state.get("turns", []) if turn.get("status") == "completed" for call in turn.get("tool_call_ids", [])}
                verified = set()
                for entry in state.get("tool_ledger", []):
                    tool_result = entry.get("result") or {}
                    if entry.get("status") != "completed" or entry.get("call_id") not in model_call_ids or tool_result.get("status") != "ok":
                        continue
                    value = tool_result.get("value")
                    if isinstance(value, list):
                        verified.update(f"{run_id}:{item['evidence_id']}" for item in value
                                        if isinstance(item, dict) and item.get("status") == "verified" and isinstance(item.get("evidence_id"), str))
                reviewed_refs = {f"{run_id}:{check['ref']}" for check in checks if isinstance(check.get("ref"), str)}
                supplied = set(finding.source_evidence_refs)
                linked = bool(supplied) and supplied == final_refs and supplied.issubset(set(catalog) & verified & reviewed_refs)
                if not linked:
                    raise EvaluationAuthorityError("historical evidence lacks exact model-observed supporting observation linkage")
                provenance = ExperienceProvenance(
                    run_id, observed_hash, receipt_hash, "historical-review-receipt/v1",
                    (finding.finding_id,), finding.source_evidence_refs, catalog,
                    result_id, seal_hash, observed_hash,
                )
        elif disposition == "provider_incident":
            error = runtime.get("error") or {}
            if (error.get("code") != "MODEL_TRANSIENT_FAILURE"
                    or error.get("details", {}).get("outcome") != "unknown_provider_outcome"
                    or observed.get("team_decision_result") is not None):
                raise EvaluationAuthorityError("historical Provider transient/no-Final evidence mismatch")
        projection = ReflectionInput("reflection-input/v1", run_id, observed_hash, receipt_hash,
                                     "historical-review-receipt/v1", catalog, (finding,), tuple(receipt["task_characteristics"]))
        return HistoricalReviewResult(result_id, projection, tuple(sorted(original_states.items())), provenance,
                                      terminal, provider_known, not receipt["gold_leakage_detected"], linked,
                                      self._issuer, team_guard)
