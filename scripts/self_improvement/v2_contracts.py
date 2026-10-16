"""V2 data-only contracts and admission rules. Production acceptance is absent."""
import hashlib
import json
from pathlib import Path
import re

from scripts.self_improvement.contracts import Candidate, BASELINE

BASE_COMMIT = "92c0fd282f8c33dfe29471aa70b9d7cfbf6e1d75"
V1_ROUTING = Candidate("v1-routing", "V1 relation query with controller keyword routing",
                       "relation_terms", "relation_candidates", 4)
NARROW_FIRST = Candidate("narrow-first", "Known unsafe control: discard every hit after the first",
                         "relation_terms", "first_hit", 2)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def parse_candidate(value):
    try:
        candidate = Candidate.parse(value)
    except (TypeError, KeyError) as error:
        raise ValueError("Invalid candidate types") from error
    text = candidate.change.casefold()
    if re.search(r"(?:ignore|skip|bypass|disable).{0,50}(?:fail|test|ground|review|permission|toolruntime)|"
                 r"(?:modify|change|rewrite|delete|write|update).{0,50}(?:gold|permission|toolruntime|memory|reviewer)|"
                 r"gold\s*(?:path|=|:)|忽略.{0,20}(?:测试|失败)|修改.{0,20}(?:权限|gold|审批)", text):
        raise ValueError("Candidate explanation attempts to change protected policy")
    return candidate


def key(candidate):
    return candidate.query_mode, candidate.read_mode, candidate.limit


def validate_feedback(value, *, experiment_id, freeze_sha256):
    if not isinstance(value, dict) or set(value) != {
        "partition", "experiment_id", "freeze_sha256", "signals", "tested_configs"
    }:
        raise ValueError("Feedback fields outside DEV projection")
    if (value["partition"] != "development" or value["experiment_id"] != experiment_id
            or value["freeze_sha256"] != freeze_sha256):
        raise ValueError("Feedback phase or experiment identity mismatch")
    forbidden = {"gold", "expected", "answer", "gold_path", "final", "validation"}

    def check(item):
        if isinstance(item, dict):
            if forbidden & set(item):
                raise ValueError("Oracle fields in optimizer feedback")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)
    return value


def claim_final(root, experiment_id, freeze_sha256, candidates, manifest_sha256=None):
    write(Path(root) / "FINAL_CLAIM.json", {"experiment_id": experiment_id,
          "freeze_sha256": freeze_sha256, "candidates": candidates,
          "generation_closed": True,"manifest_sha256":manifest_sha256})


def admission(gates):
    reasons = []
    for field in ("validation_pass", "final_pass", "improved", "core_complete"):
        if gates.get(field) is not True:
            reasons.append(field + "_not_satisfied")
    if gates.get("protected_violations", 0):
        reasons.append("protected_boundary_violation")
    if gates.get("negative_regressions"):
        reasons.append("negative_or_correct_case_regression")
    if gates.get("core_new_failures"):
        reasons.append("candidate_differential_regression")
    if gates.get("dataset_qualified") is False:
        reasons.append("frozen_target_span_quality_defects")
    if reasons:
        level = "REJECTED"
    elif gates.get("live_qualified"):
        level = "INTEGRATION_CANDIDATE"
    else:
        level = "EXPERIMENTAL_ACCEPTED"
        reasons.append("real_agent_evidence_insufficient")
    return {"level": level, "reasons": reasons, "production_activation": False,
            "historical_failures_are_not_candidate_failures": True}
