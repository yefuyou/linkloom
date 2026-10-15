"""Create the final diagnostic-only report from a sealed SH-32k stress run."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "src")]

import scripts.run_public_memory_clean_eval as runner  # noqa: E402


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object per line: {path}")
        rows.append(value)
    return rows


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def build_report(run_id: str) -> dict[str, Any]:
    root = runner.ARTIFACT_ROOT / "harness_stability_diagnostic" / "sh_32k" / run_id
    summary = _read_json(root / "pilot_summary.json")
    run_manifest = _read_json(root / "run_manifest.json")
    provider_outputs_payload = _read_json(root / "provider_outputs.json")
    provider_outputs = provider_outputs_payload.get("provider_outputs")
    if not isinstance(provider_outputs, list):
        raise ValueError("Provider outputs do not contain a row list")
    journal = _read_jsonl(root / "provider_attempt_journal.jsonl")
    evidence = runner._read_accepted_response_evidence(root / "accepted_response_evidence.jsonl")
    blocks = runner._reconstruct_clean_eval_blocks(root)
    artifact_manifest = runner._verify_published_artifact_manifest(root, run_id)
    current_fingerprints = runner.implementation_fingerprints()
    baseline_path = runner.DIAGNOSTIC_DIR / "DIAGNOSTIC_EXECUTION_BASELINE.json"
    baseline = _read_json(baseline_path)
    h1 = _read_json(runner.DIAGNOSTIC_DIR / "H1_guard_inventory.json")
    h2 = _read_json(runner.DIAGNOSTIC_DIR / "H2_stress_qualification.json")
    baseline_matches = baseline.get("implementation_fingerprints") == current_fingerprints
    manifest_fingerprints_match = run_manifest.get("implementation_fingerprints") == current_fingerprints

    starts_by_sequence: dict[int, int] = defaultdict(int)
    completed_attempts = [
        row for row in journal if row.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    ]
    for event in journal:
        if event.get("event") == "PROVIDER_ATTEMPT_STARTED":
            starts_by_sequence[int(event["sequence"])] += 1
    retry_count = sum(max(count - 1, 0) for count in starts_by_sequence.values())
    provider_errors = [
        row for row in provider_outputs if row.get("provider_outcome") == "PROVIDER_ERROR"
    ]
    local_blocks = [
        row for row in provider_outputs if row.get("provider_outcome") == "LOCAL_HARNESS_BLOCK"
    ]
    not_run = [row for row in provider_outputs if row.get("provider_outcome") == "NOT_RUN"]
    provider_response_case_ids = {row.get("case_id") for row in evidence["rows"]}
    provider_completed_cases = len(provider_response_case_ids)

    taxonomy: dict[str, dict[str, Any]] = {}
    for block in blocks["blocks"]:
        reason = block["reason_code"]
        row = taxonomy.setdefault(
            reason,
            {"count": 0, "phases": set(), "example_case_ids": [], "exact_guards": set()},
        )
        row["count"] += 1
        row["phases"].add(block["phase"])
        row["exact_guards"].add(
            f"{block['throw_site']['module']}.{block['throw_site']['function']}"
        )
        if block["case_id"] not in row["example_case_ids"]:
            row["example_case_ids"].append(block["case_id"])
    clean_eval_blocks = {
        reason: {
            **row,
            "phases": sorted(row["phases"]),
            "exact_guards": sorted(row["exact_guards"]),
        }
        for reason, row in sorted(taxonomy.items())
    }

    per_method: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {
            "context_tokens": [],
            "provider_elapsed_ms": [],
            "provider_input_tokens": [],
            "provider_output_tokens": [],
            "provider_reported_cost_usd": [],
            "attempt_cost_upper_bound_usd": [],
        }
    )
    for row in provider_outputs:
        method = str(row.get("method", "UNKNOWN"))
        if isinstance(row.get("context_tokens_estimated"), (int, float)):
            per_method[method]["context_tokens"].append(float(row["context_tokens_estimated"]))
        for metric in (
            "provider_elapsed_ms",
            "provider_input_tokens",
            "provider_output_tokens",
            "provider_reported_cost_usd",
            "attempt_cost_upper_bound_usd",
        ):
            value = row.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                per_method[method][metric].append(float(value))
    method_metrics_by_method = {
        method: {metric: _mean(values) for metric, values in metrics.items()}
        for method, metrics in sorted(per_method.items())
    }
    flat_metrics = method_metrics_by_method.get("Flat Retrieval", {})
    temporal_metrics = method_metrics_by_method.get("LinkLoom Temporal Memory", {})
    method_differences = {
        metric: round(temporal_metrics[metric] - flat_metrics[metric], 3)
        if isinstance(temporal_metrics.get(metric), (int, float))
        and isinstance(flat_metrics.get(metric), (int, float))
        else None
        for metric in (
            "context_tokens",
            "provider_elapsed_ms",
            "provider_input_tokens",
            "provider_output_tokens",
            "provider_reported_cost_usd",
            "attempt_cost_upper_bound_usd",
        )
    }
    method_metrics = {
        "by_method": method_metrics_by_method,
        "temporal_minus_flat": method_differences,
    }

    finalizer_pass = (
        artifact_manifest.get("status") == "PASS"
        and runner._verify_seal(root / "run_manifest.json", identity=f"run-manifest:{run_id}")
        and runner._verify_seal(root / "pilot_summary.json", identity=f"live-summary:{run_id}")
        and runner._verify_seal(
            root / "provider_outputs.json",
            identity="provider-outputs:factconsolidation_sh_32k",
        )
        and evidence["integrity_valid"]
        and blocks["integrity_valid"]
    )
    fingerprints_unchanged = baseline_matches and manifest_fingerprints_match
    if (
        not finalizer_pass
        or not fingerprints_unchanged
        or summary.get("gold_values_read") is not False
        or not summary.get("journal_protocol", {}).get("integrity_valid")
        or summary.get("harness_execution", {}).get("terminal_method_cases") != 200
        or any(reason.startswith("CLEAN_EVAL_BLOCK_OTHER_") for reason in clean_eval_blocks)
    ):
        verdict = "HARNESS_UNSTABLE"
    elif clean_eval_blocks or provider_errors:
        verdict = "HARNESS_STABLE_WITH_KNOWN_BLOCKS"
    else:
        verdict = "HARNESS_STABLE"

    return {
        "schema_version": "linkloom-harness-stability-diagnostic-report/v1",
        "diagnostic_status": summary.get("status"),
        "harness_stability_verdict": verdict,
        "clean_heldout_reuse_assessment": (
            "SUPPORTED_BY_DIAGNOSTIC_EVIDENCE" if verdict in {"HARNESS_STABLE", "HARNESS_STABLE_WITH_KNOWN_BLOCKS"}
            else "NOT_SUPPORTED"
        ),
        "sh_32k": {
            "classification": "DIAGNOSTIC_STRESS_SET",
            "clean_heldout_result": "NOT A CLEAN HELD-OUT RESULT",
            "purpose": "DIAGNOSTIC ONLY",
            "run_id": run_id,
            "scoring": summary.get("score_status"),
            "accuracy_claim": False,
            "gold_reads": 0 if summary.get("gold_values_read") is False else "INVALID",
        },
        "execution": {
            "planned_method_cases": 200,
            "terminal_method_cases": summary.get("harness_execution", {}).get("terminal_method_cases"),
            "provider_response_completed_method_cases": provider_completed_cases,
            "durable_output_method_cases": summary.get("provider_completion", {}).get("durable_output_method_cases"),
            "local_harness_block_method_cases": len(local_blocks),
            "provider_error_method_cases": len(provider_errors),
            "not_run_method_cases": len(not_run),
        },
        "clean_eval_blocks": clean_eval_blocks,
        "provider_reliability": {
            **summary.get("provider_reliability", {}),
            "completed_logical_method_cases": provider_completed_cases,
            "completion_rate_by_method_case": round(provider_completed_cases / 200, 4),
        },
        "count_tokens": summary.get("count_tokens", {}),
        "retries": {
            "attempts": retry_count,
            "max_attempts_per_logical_generation": runner.MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            "attempt_count_by_sequence": dict(sorted(starts_by_sequence.items())),
        },
        "context_tokens_latency_and_usage": method_metrics,
        "cost": {
            "estimated_spend_upper_bound_usd": summary.get("estimated_spend_upper_bound_usd"),
            "provider_reported_cost_usd": summary.get("provider_reported_cost_usd"),
            "run_hard_cap_usd": summary.get("hard_cost_cap_usd"),
            "task_hard_cap_usd": summary.get("global_cost_cap_usd"),
        },
        "artifact_durability": {
            "status": "PASS" if finalizer_pass else "FAIL",
            "artifact_manifest": artifact_manifest,
            "accepted_response_evidence_integrity": evidence["integrity_valid"],
            "clean_eval_block_integrity": blocks["integrity_valid"],
            "provider_attempt_journal_integrity": summary.get("journal_protocol", {}).get("integrity_valid"),
        },
        "finalizer": {"status": "PASS" if finalizer_pass else "FAIL"},
        "fingerprints": {
            "baseline_matches_current": baseline_matches,
            "run_manifest_matches_current": manifest_fingerprints_match,
            "sha256": current_fingerprints["files_sha256"],
            "files": current_fingerprints["files"],
        },
        "regression": {
            "status": "PASS" if h2.get("status") == "PASS" and h2.get("offline_regression", {}).get("failures") == 0 else "FAIL",
            "passed": h2.get("offline_regression", {}).get("passed"),
            "baseline": 340,
            "guard_inventory_status": h1.get("status"),
            "provider_calls": 0,
        },
        "mh_6k": {"live_run_started": False, "dataset_preparation_started": False},
        "provider_attempt_completion_events": len(completed_attempts),
        "provider_calls_during_report_generation": 0,
    }


def _markdown(report: dict[str, Any]) -> str:
    execution = report["execution"]
    provider = report["provider_reliability"]
    retry = report["retries"]
    cost = report["cost"]
    lines = [
        "# Public Memory Evaluation Harness Stability Diagnostic",
        "",
        "SH-32k is classified as a `DIAGNOSTIC_STRESS_SET`; this run is `DIAGNOSTIC ONLY` and is **NOT A CLEAN HELD-OUT RESULT**. Accuracy scoring was disabled and Gold reads remained 0.",
        "",
        "## DIAGNOSTIC STATUS",
        "",
        f"- Stress run: `{report['diagnostic_status']}`",
        f"- H4 verdict: `{report['harness_stability_verdict']}`",
        f"- Clean held-out reuse assessment: `{report['clean_heldout_reuse_assessment']}`",
        f"- Run ID: `{report['sh_32k']['run_id']}`",
        "",
        "## EXECUTION",
        "",
        f"- Planned: {execution['planned_method_cases']}",
        f"- Terminal: {execution['terminal_method_cases']}/200",
        f"- Provider response completed cases: {execution['provider_response_completed_method_cases']}",
        f"- Local harness blocks: {execution['local_harness_block_method_cases']}",
        f"- Provider errors: {execution['provider_error_method_cases']}",
        f"- Not run: {execution['not_run_method_cases']}",
        "",
        "## CLEAN_EVAL_BLOCKS",
        "",
    ]
    if report["clean_eval_blocks"]:
        lines.extend(["| Reason code | Count | Phases | Example cases | Exact guards |", "|---|---:|---|---|---|"])
        for code, block in report["clean_eval_blocks"].items():
            lines.append(
                f"| `{code}` | {block['count']} | {', '.join(block['phases'])} | "
                f"{', '.join(block['example_case_ids'])} | {', '.join(block['exact_guards'])} |"
            )
    else:
        lines.append("No CleanEvalBlocked artifacts were recorded.")
    lines.extend(
        [
            "",
            "## PROVIDER RELIABILITY",
            "",
            f"- Generation attempts: {provider.get('generation_attempts')}",
            f"- Provider completed requests: {provider.get('provider_completed_requests')}",
            f"- Provider errors: {provider.get('provider_error_attempts')}",
            f"- Local harness blocks after response: {provider.get('local_harness_block_attempts')}",
            "",
            "## COUNTTOKENS",
            "",
            "```json",
            json.dumps(report["count_tokens"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## RETRIES",
            "",
            f"- Retries: {retry['attempts']}",
            f"- Maximum attempts per logical generation: {retry['max_attempts_per_logical_generation']}",
            "",
            "## CONTEXT / LATENCY / TOKENS",
            "",
            "```json",
            json.dumps(report["context_tokens_latency_and_usage"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## ARTIFACT DURABILITY",
            "",
            f"- Status: `{report['artifact_durability']['status']}`",
            f"- Finalizer: `{report['finalizer']['status']}`",
            "",
            "## REGRESSION",
            "",
            f"- H1 guard audit: `{report['regression']['guard_inventory_status']}`",
            f"- H2 offline regression: `{report['regression']['status']}` ({report['regression']['passed']} passed; baseline {report['regression']['baseline']})",
            f"- Provider calls: {report['regression']['provider_calls']}",
            "",
            "## COST",
            "",
            f"- Estimated upper bound: ${cost['estimated_spend_upper_bound_usd']}",
            f"- Provider reported: ${cost['provider_reported_cost_usd']}",
            f"- Run cap: ${cost['run_hard_cap_usd']}; task cap: ${cost['task_hard_cap_usd']}",
            "",
            "## CLEAN DATA STATUS",
            "",
            "- MH-6k live: not started.",
            "- SH-32k accuracy scoring: disabled.",
            "- Clean held-out evaluation: not performed.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    report = build_report(args.run_id)
    json_path = REPO / "docs" / "evaluation" / "HARNESS_STABILITY_DIAGNOSTIC.json"
    markdown_path = REPO / "docs" / "evaluation" / "HARNESS_STABILITY_DIAGNOSTIC.md"
    runner._atomic_json_write_fsync(json_path, report)
    runner._atomic_json_write_fsync(markdown_path, _markdown(report))
    runner._seal_file(json_path, identity=f"harness-stability-diagnostic-report:{args.run_id}")
    runner._seal_file(markdown_path, identity=f"harness-stability-diagnostic-report-md:{args.run_id}")
    print(json.dumps({"status": report["harness_stability_verdict"], "json": str(json_path), "markdown": str(markdown_path)}, indent=2))
    return 0 if report["artifact_durability"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
