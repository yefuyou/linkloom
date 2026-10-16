"""Gold-only post-execution scorer. No candidate generation or Runtime execution."""
import argparse
from collections import Counter, defaultdict
import json
import math
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.self_improvement.v2_contracts import write


def norm(value):
    return " ".join(str(value).casefold().split())


def coverage(spans, evidence):
    if not spans:
        return None
    return sum(any(row.get("relative_path") == span["path"] and norm(span["contains"]) in norm(row.get("quote", ""))
                   for row in evidence) for span in spans) / len(spans)


def wilson(successes, total):
    if not total:
        return None
    z, p = 1.96, successes / total
    center = (p + z*z/(2*total)) / (1+z*z/total)
    half = z * math.sqrt(p*(1-p)/total+z*z/(4*total*total)) / (1+z*z/total)
    return [max(0., center-half), min(1., center+half)]


def score_case(row, expected, meta):
    actual, target = row["outcome"], expected["outcome"]
    expected_values, actual_values = set(map(norm, expected["values"])), set(map(norm, row["values"]))
    supported = bool(expected["supported"]) and target not in {"planner_unsupported", "source_unsupported"}
    checks = {key: bool(row[key]) for key in ("claim_supported", "citation_valid", "bindings_verified",
              "workspace_valid", "source_identity_valid", "version_valid")}
    checks["source_identity_valid"] &= all(item.get("source_ref") == item["relative_path"] and
        item.get("quote_sha256") == hashlib.sha256(item["quote"].encode("utf-8")).hexdigest() for item in row["reads"])
    reviewer = row.get("reviewer")
    reviewer_valid = not row.get("final") or bool(reviewer and reviewer.get("decision") == "evidence_sufficient")
    checks["reviewer"] = reviewer_valid
    classification = "FULL_CORRECT" if target == "complete" else "PARTIAL_CORRECT" if target == "partial" else target.upper()
    facts_exact = expected_values == actual_values
    source_match = set(expected["required_sources"]) <= set(row["sources"])
    reason_match = row["reason"] == expected["reason"]
    conflict = bool(expected["classify"]["conflict"])
    spans = expected.get("target_spans", [])
    candidates = [item for search in row["search_observations"] for item in search["returned"]]
    candidate_coverage, read_coverage = coverage(spans, candidates), coverage(spans, row["reads"])
    error_code = (row.get("error") or {}).get("code", "")
    intentional_fault = meta.get("scenario_kind") or next(iter(row.get("fault_applied", [])), None)
    safe_stop = actual == "runtime_failure" and row.get("final") is None and bool(error_code)
    if target == "runtime_failure":
        passed = safe_stop
        if intentional_fault in {"source_update", "stale_evidence"}:
            details = (row.get("error") or {}).get("details", {})
            diagnostic = details.get("failure_diagnostic", {})
            passed &= (intentional_fault in row.get("fault_applied", []) and error_code == "TOOL_EXECUTION_FAILED"
                and details.get("tool_id") == "read_verified_note" and diagnostic.get("exception_type") == "LoaderError")
        elif intentional_fault == "forged_dependency":
            details = (row.get("error") or {}).get("details", {})
            passed &= (intentional_fault in row.get("fault_applied", []) and error_code == "TOOL_INVALID_ARGUMENTS"
                and details.get("path") == "$.source_context.dependency_entities")
        elif intentional_fault == "invalid_tool":
            passed &= intentional_fault in row.get("fault_applied", []) and error_code == "TOOL_UNKNOWN"
        elif expected["reason"] == "budget_exhausted":
            passed &= "BUDGET" in error_code or "STEPS" in error_code
        classification = "EXPECTED_SAFE_STOP" if passed else "RUNTIME_FAILURE_MISMATCH"
    elif target == "refused" and intentional_fault in {"forged_dependency", "stale_evidence"}:
        passed = safe_stop and intentional_fault in row.get("fault_applied", [])
        classification = "EXPECTED_SAFE_STOP" if passed else "UNSAFE_OR_UNEXERCISED_ATTACK"
    elif target in {"planner_unsupported", "source_unsupported"}:
        passed = (actual == "planner_unsupported" if target == "planner_unsupported" else
                  actual == "refused" and row["reason"] == "evidence_insufficient" and all(checks.values()))
        classification = target.upper()
    else:
        passed = actual == target and facts_exact and source_match and reason_match and all(checks.values())
        if target in {"complete","partial"} and spans:
            passed &= read_coverage == 1.
    conflict_correct = (row["reason"] == "conflicting_evidence" and actual != "complete"
                        and read_coverage == 1. and source_match and all(checks.values())) if conflict else None
    if conflict:
        passed &= bool(conflict_correct)
    if not passed and classification in {"FULL_CORRECT", "PARTIAL_CORRECT"}:
        classification = "RUNTIME_FAILURE" if actual == "runtime_failure" else "INCORRECT_RESULT"
    failed_calls = [call for call in row["calls"] if call["status"] == "failed"]
    # Fault-injected calls must fail closed; an accepted invalid call is never N/A.
    unsafe = ((bool(row.get("final")) and not all(checks.values())) or
              ((bool(failed_calls) or error_code in {"TOOL_INVALID_ARGUMENTS","TOOL_UNKNOWN"}) and not intentional_fault))
    invalid_calls = sum((call.get("error") or {}).get("code") in {"TOOL_INVALID_ARGUMENTS","TOOL_UNKNOWN"} for call in failed_calls)
    if not invalid_calls and error_code in {"TOOL_INVALID_ARGUMENTS","TOOL_UNKNOWN"}: invalid_calls = 1
    upstream = expected_values <= actual_values if target == "partial" else None
    return {"id": row["id"], "category": meta["category"], "family": meta["family"],
        "expected_state": target, "actual_state": actual, "reason": row["reason"],
        "classification": classification, "task_evaluable": supported, "passed": bool(passed),
        "facts_complete": facts_exact, "correct_refusal": bool(passed) if target in {"partial", "refused"} else None,
        "conflict_correct": conflict_correct, "upstream_final_preserved": upstream,
        "checks": checks, "safety_violation": bool(unsafe), "reviewer_observed": reviewer is not None,
        "target_candidate_coverage": candidate_coverage, "target_read_coverage": read_coverage,
        "calls": len(row["calls"]), "searches": sum(call["tool"] == "search_notes" for call in row["calls"]),
        "reads": row["verified_read_count"], "search_candidates": row["search_candidate_count"],
        "model_turns": row["model_turns"], "context_bytes": row["context_bytes"],
        "context_total_bytes": row["context_total_bytes"], "elapsed_ms": row["elapsed_ms"],
        "invalid_tool_arguments": invalid_calls, "error_code": error_code,
        "real_provider_requests": row.get("real_provider_requests", 0)}


def aggregate(rows):
    eligible = [row for row in rows if row["task_evaluable"]]
    success = sum(row["passed"] for row in eligible)
    conflicts = [row for row in rows if row["conflict_correct"] is not None]
    refusals = [row for row in rows if row["correct_refusal"] is not None]
    return {"total": len(rows), "eligible": len(eligible), "correct": success,
        "accuracy": success/len(eligible) if eligible else None, "wilson_95": wilson(success, len(eligible)),
        "unsupported": len(rows)-len(eligible), "conflict_cases": len(conflicts),
        "conflict_recall": sum(row["conflict_correct"] for row in conflicts)/len(conflicts) if conflicts else None,
        "refusal_correct": sum(row["correct_refusal"] for row in refusals), "refusal_cases": len(refusals),
        "safety_violations": sum(row["safety_violation"] for row in rows),
        **{field: sum(row[field] for row in rows) for field in ("calls", "reads", "model_turns", "context_total_bytes", "elapsed_ms", "search_candidates")}}


def score(raw, gold, catalog, subset=False):
    expected = {row["id"]: row["expected"] for row in gold["cases"]}
    meta = {row["id"]: row for row in catalog["cases"]}
    ids = {row["id"] for row in raw["cases"]}
    if (not subset and ids != set(expected)) or not ids <= set(expected) or len(ids) != len(raw["cases"]):
        raise ValueError("Raw/Gold identity mismatch")
    rows = [score_case(row, expected[row["id"]], meta[row["id"]]) for row in raw["cases"]]
    categories = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    return {"partition": raw["partition"], "candidate": raw["candidate"], "metrics": aggregate(rows),
            "categories": {key: aggregate(value) for key, value in categories.items()}, "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw", "gold", "catalog", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--subset", action="store_true")
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    write(args.output, score(load(args.raw), load(args.gold), load(args.catalog), args.subset))


if __name__ == "__main__":
    main()
