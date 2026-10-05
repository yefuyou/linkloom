"""Offline downstream comparison for Current and Hybrid retrieval modes.

The evaluator reads frozen relevance labels only after retrieval has returned.
No model or Gold payload is passed to the retrieval backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

from linkloom.agents.team_decision import SCHEMA_VERSION, TeamDecisionResult
from linkloom.indexing import EmbeddingProvider

from .benchmark import FrozenRetrievalBenchmark
from .runtime_backend import RuntimeRetrievalBackend


def run_downstream_comparison(
    repo_root: str | Path,
    *,
    embedder: EmbeddingProvider | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Compare retrieval-to-contract behavior on the frozen synthetic corpus."""
    if top_k != 5:
        raise ValueError("downstream comparison metrics are fixed at top_k=5")

    root = Path(repo_root).resolve()
    dataset_path = root / "docs" / "requirements" / "m1_team_decision_eval_seed" / "dataset.jsonl"
    gold_before = _sha256_file(dataset_path)
    benchmark = FrozenRetrievalBenchmark(root, embedder=embedder)
    gold_after_load = _sha256_file(dataset_path)
    if gold_before != gold_after_load:
        raise RuntimeError("frozen comparison Gold changed while loading")

    notes_by_workspace = benchmark.note_documents
    notes_by_path = {
        note.relative_path: note
        for notes in notes_by_workspace.values()
        for note in notes
    }
    backends: dict[tuple[str, str], RuntimeRetrievalBackend] = {}
    mode_cases: dict[str, list[dict[str, Any]]] = {"current": [], "hybrid": []}
    seen_workspaces: set[str] = set()

    for case in benchmark.cases:
        cold_start = case.workspace_id not in seen_workspaces
        workspace_notes = list(notes_by_workspace.get(case.workspace_id, ()))
        if not workspace_notes:
            raise ValueError(f"frozen case references unknown workspace: {case.workspace_id}")

        for mode in ("current", "hybrid"):
            key = (mode, case.workspace_id)
            backend = backends.get(key)
            if backend is None:
                backend = RuntimeRetrievalBackend(
                    workspace_id=case.workspace_id,
                    document_provider=lambda notes=workspace_notes: list(notes),
                    mode=mode,
                    embedder=embedder,
                    index_version=2,
                )
                backends[key] = backend

            result = backend.search(case.query, top_k=top_k)
            evidence = result.evidence
            ranked_paths = [str(item["relative_path"]) for item in evidence]
            relevant = set(case.relevant_source_refs)
            retrieved_relevant = relevant.intersection(ranked_paths[:top_k])
            model_context_bytes = len(
                json.dumps(evidence, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            contract_pass, grounding_pass = _validate_downstream_boundary(evidence, notes_by_path)

            mode_cases[mode].append(
                {
                    "case_id": case.case_id,
                    "evidence_available_at_5": bool(retrieved_relevant),
                    "expected_evidence_recall_at_5": (
                        len(retrieved_relevant) / len(relevant) if relevant else 0.0
                    ),
                    "retrieved_evidence_count": len(evidence),
                    "contract_pass": contract_pass,
                    "grounding_pass": grounding_pass,
                    "model_context_bytes": model_context_bytes,
                    "retrieval_latency_ms": result.observation.retrieval_latency_ms,
                    "index_version": result.observation.index_version,
                    "cold_start": cold_start,
                }
            )
        seen_workspaces.add(case.workspace_id)

    gold_after_run = _sha256_file(dataset_path)
    if gold_before != gold_after_run:
        raise RuntimeError("frozen comparison Gold changed during evaluation")

    return {
        "schema_version": "retrieval-v2-downstream-comparison/v1",
        "dataset": {
            "case_count": len(benchmark.cases),
            "workspace_count": len(notes_by_workspace),
            "document_count": sum(len(notes) for notes in notes_by_workspace.values()),
            "gold_sha256_before": gold_before,
            "gold_sha256_after": gold_after_run,
            "gold_unchanged": gold_before == gold_after_run,
        },
        "method": {
            "modes": ["current", "hybrid"],
            "top_k": top_k,
            "embedding": "configured local embedder; no paid provider",
            "downstream_probe": (
                "A deterministic, non-business TeamDecisionResult shape is validated against "
                "only retrieved source evidence. This checks the contract/grounding boundary, "
                "not semantic answer quality."
            ),
            "context_size": "UTF-8 bytes of the serialized retrieved evidence tool result",
        },
        "modes": {
            mode: _aggregate_cases(cases, len(benchmark.cases))
            for mode, cases in mode_cases.items()
        },
    }


def render_downstream_comparison_markdown(report: dict[str, Any]) -> str:
    """Render a concise evidence table without implying model-quality gains."""
    rows = []
    for mode, values in report["modes"].items():
        rows.append(
            "| {mode} | {availability:.3f} | {contract:.3f} | {grounding:.3f} | "
            "{context:.1f} | {latency:.2f} | N/E |".format(
                mode=mode,
                availability=values["evidence_availability_at_5"],
                contract=values["team_decision_contract_pass_rate"],
                grounding=values["grounding_pass_rate"],
                context=values["mean_context_bytes"],
                latency=values["mean_latency_ms"],
            )
        )
    dataset = report["dataset"]
    return "\n".join(
        [
            "# Retrieval V2 Downstream Comparison",
            "",
            f"Frozen cases: {dataset['case_count']} across {dataset['workspace_count']} workspaces "
            f"and {dataset['document_count']} notes. Gold unchanged: **{dataset['gold_unchanged']}**.",
            "",
            "| Mode | Evidence availability @5 | TeamDecision contract pass | Grounding pass | "
            "Mean evidence context (bytes) | Mean retrieval latency (ms) | Semantic result |",
            "|---|---:|---:|---:|---:|---:|---|",
            *rows,
            "",
            "## Interpretation",
            "",
            "Contract and grounding columns are deterministic boundary probes using a synthetic "
            "non-business result. They show that returned source evidence can pass the existing "
            "strict contract and observed-evidence check; they do not measure answer quality.",
            "",
            "Semantic result is **N/E**: this offline run invokes no real model/provider, so it "
            "does not establish semantic accuracy or a real-model improvement. Context size is "
            "the UTF-8 byte size of serialized retrieved evidence results, not provider token usage. "
            "Latency includes cold index initialization on the first query for each workspace; "
            "per-case details and hashes are in the JSON artifact.",
            "",
            f"Gold SHA-256: `{dataset['gold_sha256_after']}`.",
            "",
        ]
    )


def _validate_downstream_boundary(
    evidence: list[dict[str, Any]],
    notes_by_path: dict[str, Any],
) -> tuple[bool, bool]:
    """Validate a neutral TeamDecision shape and its observed source references."""
    raw_evidence_ids = [item.get("evidence_id") for item in evidence]
    if any(not isinstance(item, str) or not item for item in raw_evidence_ids):
        return False, False
    evidence_ids = list(dict.fromkeys(str(item) for item in raw_evidence_ids))

    if evidence:
        ref = str(evidence[0]["evidence_id"])
        final = {
            "schema_version": SCHEMA_VERSION,
            "decision": {
                "value": "Verified source evidence was retrieved.",
                "status": "approved",
                "evidence_refs": [ref],
            },
            "rationale": [],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "none",
                "statement": None,
                "unknown_fields": [],
                "evidence_refs": [],
            },
            "evidence_refs": [ref],
        }
    else:
        final = {
            "schema_version": SCHEMA_VERSION,
            "decision": {
                "value": None,
                "status": "insufficient_evidence",
                "evidence_refs": [],
            },
            "rationale": [],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "insufficient_evidence",
                "statement": "No source evidence was retrieved.",
                "unknown_fields": [],
                "evidence_refs": [],
            },
            "evidence_refs": [],
        }

    parsed = TeamDecisionResult.from_grounded_final(
        json.dumps(final, ensure_ascii=False),
        evidence_ids,
    )
    contract_pass = isinstance(parsed, TeamDecisionResult)
    grounding_pass = all(
        _is_verified_source_evidence(item, notes_by_path) for item in evidence
    ) and all(ref in evidence_ids for ref in parsed.claim_evidence_refs())
    return contract_pass, grounding_pass


def _is_verified_source_evidence(item: dict[str, Any], notes_by_path: dict[str, Any]) -> bool:
    path = item.get("relative_path")
    note = notes_by_path.get(path) if isinstance(path, str) else None
    quote = item.get("quote")
    return bool(
        note is not None
        and item.get("status") == "verified"
        and isinstance(quote, str)
        and quote
        and quote in note.content
        and item.get("content_sha256") == note.content_sha256
        and item.get("quote_sha256") == hashlib.sha256(quote.encode("utf-8")).hexdigest()
    )


def _aggregate_cases(
    cases: list[dict[str, Any]],
    total_case_count: int,
) -> dict[str, Any]:
    latencies = [case["retrieval_latency_ms"] for case in cases]
    context_sizes = [case["model_context_bytes"] for case in cases]
    cold_latencies = [case["retrieval_latency_ms"] for case in cases if case["cold_start"]]
    warm_latencies = [case["retrieval_latency_ms"] for case in cases if not case["cold_start"]]
    p95_index = max(0, math.ceil(0.95 * len(context_sizes)) - 1)
    return {
        "case_count": total_case_count,
        "evidence_availability_at_5": sum(
            case["evidence_available_at_5"] for case in cases
        ) / total_case_count,
        "expected_evidence_recall_at_5": sum(
            case["expected_evidence_recall_at_5"] for case in cases
        ) / total_case_count,
        "team_decision_contract_pass_rate": sum(case["contract_pass"] for case in cases)
        / total_case_count,
        "grounding_pass_rate": sum(case["grounding_pass"] for case in cases)
        / total_case_count,
        "mean_context_bytes": statistics.fmean(context_sizes),
        "p95_context_bytes": sorted(context_sizes)[p95_index],
        "mean_latency_ms": statistics.fmean(latencies),
        "cold_start_case_count": len(cold_latencies),
        "cold_start_mean_latency_ms": statistics.fmean(cold_latencies) if cold_latencies else 0.0,
        "warm_mean_latency_ms": statistics.fmean(warm_latencies) if warm_latencies else 0.0,
        "semantic_result": {
            "status": "N/E",
            "reason": "No real provider was invoked; the synthetic contract probe is not a semantic evaluation.",
        },
        "cases": cases,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
