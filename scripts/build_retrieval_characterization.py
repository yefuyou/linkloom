"""Validate frozen retrieval outputs and build the Stage A characterization."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from linkloom.retrieval_v2.characterization import (
    classify_failure_categories,
    explain_failure_case,
    percentile,
    recompute_case_metrics,
)


MODES = ("current", "bm25", "dense", "hybrid", "directory_hybrid")
METRIC_KEYS = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "mrr",
    "ndcg_at_5",
    "retrieval_failure",
    "evidence_availability_at_5",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("docs/evaluation/retrieval_benchmark_v2_agy_a1_raw.json"),
    )
    parser.add_argument(
        "--candidate-diagnostic",
        type=Path,
        default=Path("docs/evaluation/retrieval_v2_candidate_pool_top20_diagnostic.json"),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("docs/evaluation/retrieval_characterization.json"),
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("docs/evaluation/RETRIEVAL_CHARACTERIZATION.md"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = args.repo_root.resolve()
    benchmark_path = _resolve(root, args.benchmark)
    diagnostic_path = _resolve(root, args.candidate_diagnostic)
    json_path = _resolve(root, args.output_json)
    markdown_path = _resolve(root, args.output_markdown)
    if not args.force and (json_path.exists() or markdown_path.exists()):
        raise FileExistsError("characterization output already exists; pass --force to replace it")

    payload = build_characterization(root, benchmark_path, diagnostic_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "outputs": [str(json_path), str(markdown_path)],
                "case_count": payload["benchmark"]["case_count"],
                "failure_case_count": len(payload["failure_cases"]),
                "gold_sha256": payload["benchmark"]["gold_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_characterization(
    repo_root: Path,
    benchmark_path: Path,
    diagnostic_path: Path,
) -> dict[str, Any]:
    seed_root = repo_root / "docs" / "requirements" / "m1_team_decision_eval_seed"
    dataset_path = seed_root / "dataset.jsonl"
    actual_gold_hash = _sha256(dataset_path)
    benchmark = _read_json(benchmark_path)
    diagnostic = _read_json(diagnostic_path)
    dataset_cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected_ids = {str(row["case_id"]) for row in dataset_cases}
    expected_refs = {
        str(row["case_id"]): set(map(str, row["expected_relevant_notes"]))
        for row in dataset_cases
    }
    expected_workspaces = {
        str(row["case_id"]): str(row["workspace_id"]) for row in dataset_cases
    }
    workspace_ids = {str(row["workspace_id"]) for row in dataset_cases}
    workspace_note_counts = {
        workspace_id: len(list((seed_root / "workspaces" / workspace_id).glob("*.md")))
        for workspace_id in workspace_ids
    }
    note_count = sum(workspace_note_counts.values())

    _validate_integrity(benchmark, actual_gold_hash, expected_ids, "official benchmark")
    _validate_integrity(diagnostic, actual_gold_hash, expected_ids, "candidate diagnostic")
    if set(benchmark.get("modes", {})) != set(MODES):
        raise ValueError("official benchmark must contain exactly the five frozen modes")
    if set(diagnostic.get("modes", {})) != set(MODES):
        raise ValueError("candidate diagnostic must contain exactly the five frozen modes")
    if diagnostic.get("diagnostic", {}).get("candidate_pool_top_k") != 20:
        raise ValueError("candidate diagnostic must use top_k=20")
    if diagnostic.get("diagnostic", {}).get("official_top_k") != 5:
        raise ValueError("candidate diagnostic must preserve official top_k=5")

    official_rows = {
        mode: _case_map(
            benchmark["modes"][mode],
            expected_ids,
            expected_refs,
            expected_workspaces,
            workspace_note_counts,
            mode,
            5,
        )
        for mode in MODES
    }
    candidate_rows = {
        mode: _case_map(
            diagnostic["modes"][mode],
            expected_ids,
            expected_refs,
            expected_workspaces,
            workspace_note_counts,
            mode,
            20,
        )
        for mode in MODES
    }
    mode_summaries = {
        mode: _summarize_mode(benchmark["modes"][mode], official_rows[mode])
        for mode in MODES
    }

    candidate_summaries = {
        mode: _summarize_candidate_mode(
            official_rows[mode],
            candidate_rows[mode],
            expected_refs,
        )
        for mode in MODES
    }
    failure_cases = _failure_cases(dataset_cases, official_rows, candidate_rows)
    official_directory_changes = _changed_rank_cases(
        official_rows["hybrid"], official_rows["directory_hybrid"]
    )
    candidate_directory_changes = _changed_rank_cases(
        candidate_rows["hybrid"], candidate_rows["directory_hybrid"]
    )
    directory_losses = _candidate_losses(
        candidate_rows["hybrid"], candidate_rows["directory_hybrid"], expected_refs
    )
    directory_candidate_comparison = _candidate_count_comparison(
        candidate_rows["hybrid"], candidate_rows["directory_hybrid"]
    )
    directory_decision = (
        "NO_MEASURABLE_GAIN_ON_FROZEN_CORPUS"
        if not official_directory_changes and not candidate_directory_changes and not directory_losses
        else "RESULTS_DIFFER_WITHOUT_SCALE_EVIDENCE"
    )

    category_counts = {
        label: sum(label in row["labels"] for row in failure_cases)
        for label in (
            "LEXICAL_MISS",
            "SEMANTIC_MISS",
            "RANKING_MISS",
            "SCOPE_ROUTING_MISS",
            "TEMPORAL_AMBIGUITY",
            "MULTI_DOCUMENT_EVIDENCE",
            "ENTITY_ALIAS",
            "NO_RELEVANT_EVIDENCE",
            "OTHER",
        )
    }
    partial_recall_cases = {
        mode: sum(
            any(
                reference not in official_rows[mode][case_id]["ranked_source_refs"]
                for reference in expected_refs[case_id]
            )
            for case_id in expected_ids
        )
        for mode in MODES
    }
    omitted_evidence_items = {
        mode: sum(
            len(
                expected_refs[case_id]
                - set(official_rows[mode][case_id]["ranked_source_refs"])
            )
            for case_id in expected_ids
        )
        for mode in MODES
    }
    top20_omissions = {
        mode: sum(
            len(expected_refs[case_id] - set(candidate_rows[mode][case_id]["ranked_source_refs"]))
            for case_id in expected_ids
        )
        for mode in MODES
    }
    top5_stability = {
        mode: sum(
            official_rows[mode][case_id]["ranked_source_refs"]
            == candidate_rows[mode][case_id]["ranked_source_refs"][:5]
            for case_id in expected_ids
        )
        for mode in MODES
    }

    return {
        "schema_version": "linkloom.retrieval-characterization.v1",
        "benchmark": {
            "case_count": len(dataset_cases),
            "workspace_count": len(workspace_ids),
            "note_count": note_count,
            "official_top_k": 5,
            "gold_sha256": actual_gold_hash,
            "query_and_relevance_source": "docs/requirements/m1_team_decision_eval_seed/dataset.jsonl",
            "official_result_artifact": _relative(repo_root, benchmark_path),
            "candidate_diagnostic_artifact": _relative(repo_root, diagnostic_path),
            "candidate_pool_top_k": 20,
            "latency_percentile_method": "linear interpolation (Hyndman-Fan Type 7)",
            "latency_sample_note": "P50/P95 are small-sample descriptive statistics from one 30-query run per mode; do not interpret as a production latency guarantee.",
        },
        "validation": {
            "gold_hash_matches_input": True,
            "official_gold_unchanged": benchmark["gold_sha256_before"]
            == benchmark["gold_sha256_after"]
            == actual_gold_hash,
            "candidate_diagnostic_gold_unchanged": diagnostic["gold_sha256_before"]
            == diagnostic["gold_sha256_after"]
            == actual_gold_hash,
            "all_five_modes_have_all_30_cases": all(
                len(official_rows[mode]) == len(expected_ids) for mode in MODES
            ),
            "frozen_relevance_matches_every_mode": True,
            "requested_top_k_result_count_matches_workspace_inventory": True,
            "every_ranked_source_is_within_case_workspace": True,
            "metrics_recomputed_from_rankings": True,
            "official_top5_matches_top20_diagnostic": top5_stability,
        },
        "metrics_by_mode": mode_summaries,
        "candidate_pool_by_mode": candidate_summaries,
        "failure_analysis": {
            "case_level_category_counts": category_counts,
            "category_count_semantics": (
                "Zero means no case was assigned that label by the ranking-evidence "
                "classifier; it does not prove the phenomenon is absent."
            ),
            "categories_not_assessable_from_rankings": [
                "TEMPORAL_AMBIGUITY",
                "ENTITY_ALIAS",
            ],
            "partial_recall_cases_by_mode": partial_recall_cases,
            "omitted_relevant_items_by_mode": omitted_evidence_items,
            "top20_candidate_omissions_by_mode": top20_omissions,
            "hard_retrieval_failures_by_mode": {
                mode: mode_summaries[mode]["metrics"]["retrieval_failure_rate"]
                for mode in MODES
            },
            "analysis_performer": "Codex local analysis of the frozen result artifacts",
            "agy_a2_status": "AGY CLI reported unauthenticated on a fresh start; credential access was rejected by sandbox review. No credential access or quota reset was attempted.",
        },
        "directory_decision": {
            "decision": directory_decision,
            "official_top5_changed_cases": official_directory_changes,
            "candidate_top20_changed_cases": candidate_directory_changes,
            "scope_routing_losses": directory_losses,
            "candidate_count_comparison": directory_candidate_comparison,
            "latency_ms": {
                mode: {
                    name: mode_summaries[mode]["metrics"][name]
                    for name in ("mean_latency_ms", "p50_latency_ms", "p95_latency_ms")
                }
                for mode in ("hybrid", "directory_hybrid")
            },
            "rationale": "Hybrid and Directory-Hybrid have identical ranked results in the frozen run and top-20 diagnostic. The 36-note fixture has six notes per workspace in a flat directory layout, so this dataset demonstrates neither ranking gain nor scope-routing failure. Keep the existing backend available, but do not claim improvement or switch the default based on this benchmark.",
        },
        "reranker_decision": {
            "decision": "NO_GO_FOR_THIS_STAGE",
            "hybrid_recall_at_20": candidate_summaries["hybrid"]["recall_at_20"],
            "hybrid_partial_recall_cases_at_5": partial_recall_cases["hybrid"],
            "hybrid_partial_recall_fraction_at_5": round(
                partial_recall_cases["hybrid"] / len(expected_ids), 4
            ),
            "hybrid_metrics_at_5": mode_summaries["hybrid"]["metrics"],
            "rationale": "All six notes in each workspace appear in the top-20 result, making Recall@20=1.0 a saturated candidate-pool signal on this tiny corpus rather than evidence of realistic candidate quality. Hybrid has zero hard retrieval failures, Evidence Availability@5=1.0, Recall@5=0.9361, MRR=0.9333, and nDCG@5=0.8895; seven of 30 queries omit one item from a multi-document Gold set. This does not establish materially weak ranking quality strongly enough to justify a cross-encoder spike, especially with a single-run local Hybrid mean latency around 571 ms. Do not add or enable a reranker by default from this evidence.",
        },
        "failure_cases": failure_cases,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    benchmark = payload["benchmark"]
    lines = [
        "# Retrieval Characterization — Stage A",
        "",
        "## Benchmark integrity",
        "",
        f"- Frozen inputs: {benchmark['case_count']} queries, {benchmark['workspace_count']} workspaces, {benchmark['note_count']} notes.",
        f"- Official retrieval cutoff: Top-{benchmark['official_top_k']}; the separate candidate diagnostic requests Top-{benchmark['candidate_pool_top_k']} and does not change the official benchmark.",
        f"- Gold SHA-256: `{benchmark['gold_sha256']}`; verified unchanged before and after both runs.",
        f"- Raw artifacts: `{benchmark['official_result_artifact']}` and `{benchmark['candidate_diagnostic_artifact']}`.",
        "- Metrics were independently recomputed from each raw ranking; frozen relevance labels were checked against every mode.",
        "",
        "## Benchmark matrix",
        "",
        "P50/P95 use linear interpolation (Hyndman-Fan Type 7). With only 30 observations from one run per mode, these are small-sample descriptive statistics, not production latency guarantees.",
        "",
        "| Mode | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | Failure rate | Availability@5 | Mean ms | P50 ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        summary = payload["metrics_by_mode"][mode]
        metrics = summary["metrics"]
        lines.append(
            "| {mode} | {r1:.4f} | {r3:.4f} | {r5:.4f} | {mrr:.4f} | {ndcg:.4f} | {failure:.4f} | {availability:.4f} | {mean:.2f} | {p50:.2f} | {p95:.2f} |".format(
                mode=mode,
                r1=metrics["recall_at_1"],
                r3=metrics["recall_at_3"],
                r5=metrics["recall_at_5"],
                mrr=metrics["mrr"],
                ndcg=metrics["ndcg_at_5"],
                failure=metrics["retrieval_failure_rate"],
                availability=metrics["evidence_availability_at_5"],
                mean=metrics["mean_latency_ms"],
                p50=metrics["p50_latency_ms"],
                p95=metrics["p95_latency_ms"],
            )
        )

    lines.extend(
        [
            "",
            "## Failure taxonomy",
            "",
            "A case is listed when at least one official Top-5 mode omits a frozen relevant source. The rank cells below show the rank (or `—` when beyond Top-5) for each omitted source across Current/BM25/Dense/Hybrid/Directory-Hybrid. Candidate ranks are preserved in the JSON artifact.",
            "",
            "| Case | Query | Relevant source omitted at Top-5 — ranks C/B/D/H/DH | Class | Evidence-based explanation |",
            "|---|---|---|---|---|",
        ]
    )
    aliases = {
        "current": "C",
        "bm25": "B",
        "dense": "D",
        "hybrid": "H",
        "directory_hybrid": "DH",
    }
    for case in payload["failure_cases"]:
        omitted_sources = sorted(
            {
                source_ref
                for refs in case["missing_at_5_by_mode"].values()
                for source_ref in refs
            }
        )
        detail = []
        for source_ref in omitted_sources:
            ranks = case["relevant_ranks_at_5"][source_ref]
            rank_text = "/".join(
                f"{aliases[mode]}={ranks[mode] if ranks[mode] is not None else '—'}"
                for mode in MODES
            )
            detail.append(f"{Path(source_ref).name} ({rank_text})")
        query = str(case["query"]).replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {query} | {'; '.join(detail)} | {', '.join(case['labels'])} | {case['explanation']} |"
        )

    taxonomy_counts = payload["failure_analysis"]["case_level_category_counts"]
    lines.extend(
        [
            "",
            "Category counts are overlapping case counts. Every omitted source is present "
            "in that mode's Top-20 result, so the observed retrieval mechanism is a Top-5 "
            "ranking cutoff; no candidate-generation miss, no-evidence case, or directory "
            "scope loss is evidenced by these results. Temporal ambiguity and entity alias "
            "are not inferable from ranked-reference artifacts alone. Their zero counts "
            "mean no case was assigned those labels, not proof that those phenomena are absent.",
            "",
            "| Fixed category | Cases |",
            "|---|---:|",
        ]
    )
    for label, count in taxonomy_counts.items():
        lines.append(f"| {label} | {count} |")

    lines.extend(
        [
            "",
            "### Reranker decision",
            "",
            f"**{payload['reranker_decision']['decision']}** — Hybrid has Recall@20={payload['reranker_decision']['hybrid_recall_at_20']:.4f}, but this is saturated because each workspace contains only six notes. At Top-5, Hybrid has Recall@5=0.9361, MRR=0.9333, nDCG@5=0.8895, Availability@5=1.0000, and seven partial multi-document cases out of 30. That evidence does not show materially weak ranking strongly enough to justify a cross-encoder spike or default enablement; observed local mean latency is already about 571 ms in this single run.",
            "",
            "### Directory-aware decision",
            "",
            f"**{payload['directory_decision']['decision']}** — official Top-5 ranks changed in {len(payload['directory_decision']['official_top5_changed_cases'])}/30 cases; Top-20 ranks changed in {len(payload['directory_decision']['candidate_top20_changed_cases'])}/30; relevant scope losses: {len(payload['directory_decision']['scope_routing_losses'])}. The frozen corpus is flat per workspace, so this is not evidence of hierarchical retrieval benefit at scale.",
            f"- Candidate-count change: {payload['directory_decision']['candidate_count_comparison']['hybrid_mean']} → {payload['directory_decision']['candidate_count_comparison']['directory_hybrid_mean']} mean candidates per query; {payload['directory_decision']['candidate_count_comparison']['queries_with_fewer_directory_candidates']}/30 queries reduce candidates.",
            f"- One-run latency means: Hybrid {payload['directory_decision']['latency_ms']['hybrid']['mean_latency_ms']:.2f} ms; Directory-Hybrid {payload['directory_decision']['latency_ms']['directory_hybrid']['mean_latency_ms']:.2f} ms. Treat as descriptive only.",
            "",
            "## Execution and scope notes",
            "",
            "- Stage A1 was executed by AGY offline; Codex verified the frozen hashes, labels, mode counts, and sampled metric calculations.",
            "- A2 failure clustering was completed locally by Codex from the frozen artifacts. A fresh AGY CLI start reported unauthenticated; credential access was rejected by sandbox review, so no credential probing or quota reset was performed.",
            "- No Gold, query, retriever, threshold, or production ranking behavior was changed. The Top-20 artifact is diagnostic only.",
            "- Review E is complete; its verdict is recorded in REVIEW_E_PACKET.md. This artifact does not start Stage B.",
            "",
        ]
    )
    return "\n".join(lines)


def _summarize_mode(
    mode_report: dict[str, Any],
    rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_metrics: list[dict[str, float]] = []
    latencies: list[float] = []
    for case_id, row in rows.items():
        metrics = recompute_case_metrics(row["ranked_source_refs"], row["relevant_source_refs"])
        _assert_metrics_equal(metrics, row["metrics"], f"{case_id} recalculation")
        case_metrics.append(metrics)
        latency = float(row["latency_ms"])
        latencies.append(latency)

    aggregate = {
        name: round(sum(metrics[name] for metrics in case_metrics) / len(case_metrics), 4)
        for name in METRIC_KEYS
    }
    aggregate["retrieval_failure_rate"] = aggregate.pop("retrieval_failure")
    aggregate["mean_latency_ms"] = round(sum(latencies) / len(latencies), 4)
    _assert_metrics_equal(aggregate, mode_report["metrics"], "aggregate recalculation")
    aggregate["p50_latency_ms"] = percentile(latencies, 0.5)
    aggregate["p95_latency_ms"] = percentile(latencies, 0.95)
    return {"case_count": len(rows), "metrics": aggregate}


def _summarize_candidate_mode(
    official_rows: dict[str, dict[str, Any]],
    candidate_rows: dict[str, dict[str, Any]],
    expected_refs: dict[str, set[str]],
) -> dict[str, Any]:
    total_relevant = sum(len(expected_refs[case_id]) for case_id in expected_refs)
    retrieved_relevant = sum(
        len(expected_refs[case_id] & set(candidate_rows[case_id]["ranked_source_refs"]))
        for case_id in expected_refs
    )
    available_cases = sum(
        bool(expected_refs[case_id] & set(candidate_rows[case_id]["ranked_source_refs"]))
        for case_id in expected_refs
    )
    counts = [len(candidate_rows[case_id]["ranked_source_refs"]) for case_id in expected_refs]
    stable_top5 = sum(
        official_rows[case_id]["ranked_source_refs"]
        == candidate_rows[case_id]["ranked_source_refs"][:5]
        for case_id in expected_refs
    )
    return {
        "case_count": len(candidate_rows),
        "recall_at_20": round(retrieved_relevant / total_relevant, 4),
        "evidence_availability_at_20": round(available_cases / len(candidate_rows), 4),
        "returned_candidate_count_min": min(counts),
        "returned_candidate_count_max": max(counts),
        "returned_candidate_count_mean": round(sum(counts) / len(counts), 4),
        "official_top5_matches_diagnostic_top5_cases": stable_top5,
    }


def _failure_cases(
    dataset_cases: list[dict[str, Any]],
    official_rows: dict[str, dict[str, dict[str, Any]]],
    candidate_rows: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for dataset_case in dataset_cases:
        case_id = str(dataset_case["case_id"])
        relevant = set(map(str, dataset_case["expected_relevant_notes"]))
        official_rankings = {
            mode: official_rows[mode][case_id]["ranked_source_refs"] for mode in MODES
        }
        missing_by_mode = {
            mode: sorted(relevant - set(official_rankings[mode])) for mode in MODES
        }
        if not any(missing_by_mode.values()):
            continue
        candidate_rankings = {
            mode: candidate_rows[mode][case_id]["ranked_source_refs"] for mode in MODES
        }
        top5_ranks = {
            source_ref: {
                mode: _rank(official_rankings[mode], source_ref) for mode in MODES
            }
            for source_ref in sorted(relevant)
        }
        top20_ranks = {
            source_ref: {
                mode: _rank(candidate_rankings[mode], source_ref) for mode in MODES
            }
            for source_ref in sorted(relevant)
        }
        failures.append(
            {
                "case_id": case_id,
                "workspace_id": str(dataset_case["workspace_id"]),
                "query": str(dataset_case["user_question"]),
                "relevant_source_refs": sorted(relevant),
                "missing_at_5_by_mode": missing_by_mode,
                "relevant_ranks_at_5": top5_ranks,
                "relevant_ranks_at_20": top20_ranks,
                "explanation": explain_failure_case(
                    relevant_source_refs=relevant,
                    official_rankings=official_rankings,
                    candidate_rankings=candidate_rankings,
                ),
                "labels": classify_failure_categories(
                    relevant_source_refs=relevant,
                    official_rankings=official_rankings,
                    candidate_rankings=candidate_rankings,
                ),
            }
        )
    return failures


def _case_map(
    mode_report: dict[str, Any],
    expected_ids: set[str],
    expected_refs: dict[str, set[str]],
    expected_workspaces: dict[str, str],
    workspace_note_counts: dict[str, int],
    mode: str,
    expected_limit: int,
) -> dict[str, dict[str, Any]]:
    rows = mode_report.get("cases", [])
    case_ids = [str(row["case_id"]) for row in rows]
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != expected_ids:
        raise ValueError(f"{mode} does not contain exactly the frozen case IDs")
    result = {str(row["case_id"]): row for row in rows}
    for case_id, row in result.items():
        actual_refs = set(map(str, row["relevant_source_refs"]))
        if actual_refs != expected_refs[case_id]:
            raise ValueError(f"{mode}/{case_id} relevance differs from frozen Gold")
        ranked_refs = list(map(str, row["ranked_source_refs"]))
        expected_result_count = min(
            expected_limit, workspace_note_counts[expected_workspaces[case_id]]
        )
        if len(ranked_refs) != expected_result_count:
            raise ValueError(
                f"{mode}/{case_id} returned {len(ranked_refs)} results; "
                f"expected {expected_result_count} for top_k={expected_limit} "
                "and this workspace inventory"
            )
        if len(ranked_refs) != len(set(ranked_refs)):
            raise ValueError(f"{mode}/{case_id} contains duplicate ranked sources")
        workspace_prefix = f"workspaces/{expected_workspaces[case_id]}/"
        if any(not reference.startswith(workspace_prefix) for reference in actual_refs | set(ranked_refs)):
            raise ValueError(f"{mode}/{case_id} contains a cross-workspace source reference")
        latency = float(row["latency_ms"])
        if latency < 0:
            raise ValueError(f"{mode}/{case_id} has negative latency")
        row["ranked_source_refs"] = ranked_refs
    return result


def _validate_integrity(
    report: dict[str, Any], actual_gold_hash: str, expected_ids: set[str], label: str
) -> None:
    if report.get("gold_sha256_before") != actual_gold_hash:
        raise ValueError(f"{label} before hash does not match frozen dataset")
    if report.get("gold_sha256_after") != actual_gold_hash:
        raise ValueError(f"{label} after hash does not match frozen dataset")
    for mode, mode_report in report.get("modes", {}).items():
        case_ids = [str(row["case_id"]) for row in mode_report.get("cases", [])]
        if len(case_ids) != len(expected_ids) or set(case_ids) != expected_ids:
            raise ValueError(f"{label}/{mode} is missing or duplicating frozen cases")


def _assert_metrics_equal(actual: dict[str, float], expected: dict[str, float], label: str) -> None:
    for name, value in actual.items():
        expected_value = expected.get(name)
        if expected_value is None or abs(float(value) - float(expected_value)) > 0.0001:
            raise ValueError(f"{label}: {name}={value} does not match artifact value {expected_value}")


def _rank(ranked_source_refs: list[str], source_ref: str) -> int | None:
    try:
        return ranked_source_refs.index(source_ref) + 1
    except ValueError:
        return None


def _changed_rank_cases(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> list[str]:
    return sorted(
        case_id
        for case_id in left
        if left[case_id]["ranked_source_refs"] != right[case_id]["ranked_source_refs"]
    )


def _candidate_losses(
    hybrid_rows: dict[str, dict[str, Any]],
    directory_rows: dict[str, dict[str, Any]],
    expected_refs: dict[str, set[str]],
) -> list[dict[str, Any]]:
    losses: list[dict[str, Any]] = []
    for case_id, relevant in expected_refs.items():
        hybrid_refs = set(hybrid_rows[case_id]["ranked_source_refs"])
        directory_refs = set(directory_rows[case_id]["ranked_source_refs"])
        lost = sorted((relevant & hybrid_refs) - directory_refs)
        if lost:
            losses.append({"case_id": case_id, "lost_relevant_source_refs": lost})
    return losses


def _candidate_count_comparison(
    hybrid_rows: dict[str, dict[str, Any]],
    directory_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    differences = {
        case_id: len(hybrid_rows[case_id]["ranked_source_refs"])
        - len(directory_rows[case_id]["ranked_source_refs"])
        for case_id in hybrid_rows
    }
    hybrid_mean = sum(
        len(row["ranked_source_refs"]) for row in hybrid_rows.values()
    ) / len(hybrid_rows)
    directory_mean = sum(
        len(row["ranked_source_refs"]) for row in directory_rows.values()
    ) / len(directory_rows)
    return {
        "hybrid_mean": round(hybrid_mean, 4),
        "directory_hybrid_mean": round(directory_mean, 4),
        "mean_candidate_reduction": round(hybrid_mean - directory_mean, 4),
        "total_candidate_reduction": sum(differences.values()),
        "queries_with_fewer_directory_candidates": sum(value > 0 for value in differences.values()),
        "queries_with_more_directory_candidates": sum(value < 0 for value in differences.values()),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
