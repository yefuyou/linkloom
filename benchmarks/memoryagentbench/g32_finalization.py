"""Offline packaging of already sealed G3.2 held-out results.

This module deliberately has no Provider, retrieval, memory, benchmark-loader,
or scoring dependencies. It validates existing run artifacts and aggregates
only the score fields already present in ``scored_results.json``.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .g32_heldout import EXPECTED_FROZEN, build_execution_order, heldout_qa_ids


METHODS = ("Flat Retrieval", "LinkLoom Temporal Memory")
ROOT_SOURCE_FILES = (
    "run_manifest.json",
    "preflight.json",
    "qualification.json",
    "provider_outputs.json",
    "provider_outputs.seal.json",
    "scored_results.json",
    "provider_attempt_journal.jsonl",
    "provider_progress.json",
    "subset_manifest.json",
    "subset_manifest.seal.json",
)
DERIVED_FILES = (
    "pilot_summary.offline_finalized.json",
    "offline_finalization_record.json",
    "artifact_manifest.offline_finalized.json",
)


class G32FinalizationError(ValueError):
    """The existing G3.2 artifact set is incomplete, altered, or inconsistent."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise G32FinalizationError(f"cannot read valid JSON artifact: {path.name}") from error


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _require_seal(seal: Any, *, payload_hash: str, label: str) -> None:
    if not isinstance(seal, dict) or seal.get("sealed") is not True:
        raise G32FinalizationError(f"{label} is not marked sealed")
    if seal.get("sha256") != payload_hash:
        raise G32FinalizationError(f"{label} hash does not match its source artifact")


def _check_plan(actual: Any, expected: list[dict[str, Any]], *, label: str) -> None:
    if not isinstance(actual, list) or len(actual) != len(expected):
        raise G32FinalizationError(f"{label} does not contain exactly 100 method-cases")
    for row, planned in zip(actual, expected, strict=True):
        if not isinstance(row, dict) or any(
            row.get(key) != planned[key]
            for key in ("sequence", "qa_id", "method")
        ):
            raise G32FinalizationError(f"{label} order or identity differs from frozen G3.2")


def _case_slug(method: str) -> str:
    if method == METHODS[0]:
        return "flat"
    if method == METHODS[1]:
        return "temporal"
    raise G32FinalizationError(f"unknown frozen method: {method!r}")


def _safe_journal_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise G32FinalizationError("journal seal event has no relative seal path")
    normalized = value.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise G32FinalizationError("journal contains an unsafe artifact path")
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise G32FinalizationError("journal artifact path escapes the run directory")
    return target


def _validate_source_artifacts(root: Path) -> dict[str, Any]:
    for filename in ROOT_SOURCE_FILES:
        if not (root / filename).is_file():
            raise G32FinalizationError(f"required source artifact is missing: {filename}")

    manifest = _read_json(root / "run_manifest.json")
    preflight = _read_json(root / "preflight.json")
    qualification = _read_json(root / "qualification.json")
    provider_document = _read_json(root / "provider_outputs.json")
    provider_seal = _read_json(root / "provider_outputs.seal.json")
    scored_document = _read_json(root / "scored_results.json")
    progress = _read_json(root / "provider_progress.json")
    subset = _read_json(root / "subset_manifest.json")
    subset_seal = _read_json(root / "subset_manifest.seal.json")

    if not isinstance(manifest, dict) or not isinstance(subset, dict):
        raise G32FinalizationError("run or subset manifest is not an object")
    if not isinstance(preflight, dict) or not isinstance(qualification, dict):
        raise G32FinalizationError("preflight or qualification artifact is not an object")
    if not isinstance(provider_document, dict) or not isinstance(scored_document, dict):
        raise G32FinalizationError("provider outputs or scored results is not an object")
    if not isinstance(progress, dict):
        raise G32FinalizationError("provider progress artifact is not an object")

    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise G32FinalizationError("run manifest has no run_id")
    if progress.get("run_id") != run_id:
        raise G32FinalizationError("provider progress run_id differs from run manifest")

    expected_fingerprints = {
        key: value for key, value in EXPECTED_FROZEN.items()
        if key.endswith("_fingerprint")
    }
    for label, candidate in (
        ("run manifest", manifest.get("implementation_fingerprints")),
        ("preflight", preflight.get("implementation_fingerprints")),
        ("subset manifest", subset.get("fingerprints")),
    ):
        if candidate != expected_fingerprints:
            raise G32FinalizationError(f"{label} frozen implementation fingerprints mismatch")
    for key in ("dataset_revision", "dataset_sha256"):
        if manifest.get(key) != EXPECTED_FROZEN[key] or subset.get(key) != EXPECTED_FROZEN[key]:
            raise G32FinalizationError(f"frozen {key} mismatch")
    if subset.get("subset_hash") != EXPECTED_FROZEN["subset_hash"]:
        raise G32FinalizationError("frozen subset_hash mismatch")
    if manifest.get("subset_hash") not in (None, EXPECTED_FROZEN["subset_hash"]):
        raise G32FinalizationError("run manifest optional subset_hash mismatch")
    if manifest.get("subset_manifest_sha256") != EXPECTED_FROZEN["subset_manifest_sha256"]:
        raise G32FinalizationError("run manifest subset fingerprint mismatch")
    if subset.get("subset_manifest_sha256") != EXPECTED_FROZEN["subset_manifest_sha256"]:
        raise G32FinalizationError("subset manifest fingerprint mismatch")
    if manifest.get("qa_ids") != heldout_qa_ids() or subset.get("qa_ids") != heldout_qa_ids():
        raise G32FinalizationError("run or subset manifest QA IDs differ from frozen QA50–QA99")
    if manifest.get("planned_method_case_executions") != 100:
        raise G32FinalizationError("run manifest does not plan exactly 100 method-cases")
    if manifest.get("subset_manifest") != subset:
        raise G32FinalizationError("embedded subset manifest differs from sealed subset artifact")

    raw_subset_hash = _sha256_file(root / "subset_manifest.json")
    _require_seal(subset_seal, payload_hash=raw_subset_hash, label="subset manifest seal")
    raw_provider_hash = _sha256_file(root / "provider_outputs.json")
    _require_seal(provider_seal, payload_hash=raw_provider_hash, label="provider outputs seal")
    if scored_document.get("provider_outputs_sha256") != raw_provider_hash:
        raise G32FinalizationError("scored results do not bind the sealed provider outputs")
    if provider_document.get("gold_loaded") is not False:
        raise G32FinalizationError("provider-output artifact reports Gold loaded")
    if scored_document.get("gold_loaded_after_each_provider_output_seal") is not True:
        raise G32FinalizationError("scoring artifact does not assert post-seal Gold loading")
    if scored_document.get("scoring_error") is not None:
        raise G32FinalizationError("scored-results artifact records a scoring error")

    expected_plan = build_execution_order(heldout_qa_ids())
    _check_plan(manifest.get("heldout_execution_plan"), expected_plan, label="run manifest plan")
    if isinstance(preflight.get("heldout_execution_plan"), list):
        _check_plan(preflight["heldout_execution_plan"], expected_plan, label="preflight plan")

    outputs = provider_document.get("results")
    scores = scored_document.get("results")
    if not isinstance(outputs, list) or not isinstance(scores, list):
        raise G32FinalizationError("provider outputs or scored rows are missing")
    if len(outputs) != 100 or len(scores) != 100:
        raise G32FinalizationError("source artifacts do not contain 100 output and score rows")

    output_by_identity: dict[tuple[int, str, str], dict[str, Any]] = {}
    score_by_identity: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row, planned in zip(outputs, expected_plan, strict=True):
        if not isinstance(row, dict):
            raise G32FinalizationError("provider output row is not an object")
        identity = (planned["sequence"], planned["qa_id"], planned["method"])
        if any(row.get(key) != value for key, value in zip(("sequence", "question_id", "method"), identity, strict=True)):
            raise G32FinalizationError("provider output rows differ from frozen execution order")
        output_by_identity[identity] = row
    for row, planned in zip(scores, expected_plan, strict=True):
        if not isinstance(row, dict):
            raise G32FinalizationError("scored result row is not an object")
        identity = (planned["sequence"], planned["qa_id"], planned["method"])
        if any(row.get(key) != value for key, value in zip(("sequence", "question_id", "method"), identity, strict=True)):
            raise G32FinalizationError("scored result rows differ from frozen execution order")
        if not (isinstance(row.get("score"), bool) or row.get("score") is None):
            raise G32FinalizationError("scored result row has an invalid stored score type")
        score_by_identity[identity] = row

    method_cases_dir = root / "method_cases"
    case_seal_paths = sorted(method_cases_dir.glob("*-case.seal.json"))
    if len(case_seal_paths) != 100:
        raise G32FinalizationError("expected exactly 100 per-case seals")
    journal_path = root / "provider_attempt_journal.jsonl"
    try:
        journal_rows = [
            json.loads(line)
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise G32FinalizationError("provider attempt journal is unreadable") from error
    seal_events = [row for row in journal_rows if row.get("event") == "CASE_METHOD_SEALED"]
    if len(seal_events) != 100:
        raise G32FinalizationError("attempt journal does not contain 100 case-seal events")
    event_by_identity: dict[tuple[int, str, str], dict[str, Any]] = {}
    for event in seal_events:
        if event.get("run_id") != run_id:
            raise G32FinalizationError("attempt journal contains a different run_id")
        identity = (event.get("sequence"), event.get("qa_id"), event.get("method"))
        if identity in event_by_identity:
            raise G32FinalizationError("attempt journal duplicates a sealed method-case")
        event_by_identity[identity] = event

    source_case_paths: list[Path] = []
    for planned in expected_plan:
        sequence = int(planned["sequence"])
        qa_id = str(planned["qa_id"])
        method = str(planned["method"])
        slug = _case_slug(method)
        identity = (sequence, qa_id, method)
        event = event_by_identity.get(identity)
        if event is None:
            raise G32FinalizationError(f"attempt journal is missing sealed case {sequence}")
        stem = f"{sequence:02}-{qa_id}-{slug}"
        output_path = method_cases_dir / f"{stem}-provider-output.json"
        output_seal_path = method_cases_dir / f"{stem}-provider-output.seal.json"
        scored_path = method_cases_dir / f"{stem}-scored.json"
        case_seal_path = method_cases_dir / f"{stem}-case.seal.json"
        for required_path in (output_path, output_seal_path, scored_path, case_seal_path):
            if not required_path.is_file():
                raise G32FinalizationError(f"per-case source artifact is missing: {required_path.name}")

        output_hash = _sha256_file(output_path)
        output_seal = _read_json(output_seal_path)
        _require_seal(output_seal, payload_hash=output_hash, label=output_seal_path.name)
        case_seal = _read_json(case_seal_path)
        if not isinstance(case_seal, dict) or case_seal.get("sealed") is not True:
            raise G32FinalizationError(f"per-case seal is invalid: {case_seal_path.name}")
        if case_seal.get("provider_output_sha256") != output_hash:
            raise G32FinalizationError(f"per-case output hash mismatch: {case_seal_path.name}")
        if case_seal.get("scored_result_sha256") != _sha256_file(scored_path):
            raise G32FinalizationError(f"per-case scored hash mismatch: {case_seal_path.name}")
        case_seal_hash = _sha256_file(case_seal_path)
        if event.get("case_seal_sha256") != case_seal_hash:
            raise G32FinalizationError(f"journal seal hash mismatch: {case_seal_path.name}")
        if _safe_journal_path(root, event.get("case_seal_path")) != case_seal_path.resolve():
            raise G32FinalizationError(f"journal points to a different case seal: {case_seal_path.name}")

        case_output_document = _read_json(output_path)
        case_scored = _read_json(scored_path)
        if (
            not isinstance(case_output_document, dict)
            or case_output_document.get("gold_loaded") is not False
            or not isinstance(case_output_document.get("results"), list)
            or len(case_output_document["results"]) != 1
            or not isinstance(case_output_document["results"][0], dict)
        ):
            raise G32FinalizationError(f"per-case output wrapper is invalid: {stem}")
        case_output = case_output_document["results"][0]
        if case_output != output_by_identity[identity]:
            raise G32FinalizationError(f"root output differs from sealed per-case output: {stem}")
        if not isinstance(case_scored, dict) or case_scored.get("provider_output_sha256") != output_hash:
            raise G32FinalizationError(f"per-case score does not bind its provider output: {stem}")
        if case_scored.get("result") != score_by_identity[identity]:
            raise G32FinalizationError(f"root score differs from sealed per-case score: {stem}")
        source_case_paths.extend((output_path, output_seal_path, scored_path, case_seal_path))

    return {
        "root": root,
        "run_id": run_id,
        "manifest": manifest,
        "preflight": preflight,
        "qualification": qualification,
        "provider_outputs": outputs,
        "scored_results": scores,
        "progress": progress,
        "subset_manifest": subset,
        "journal_rows": journal_rows,
        "expected_plan": expected_plan,
        "source_paths": [root / name for name in ROOT_SOURCE_FILES] + source_case_paths,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _build_summary(verified: dict[str, Any], original_live_status: str) -> dict[str, Any]:
    outputs = verified["provider_outputs"]
    scores = verified["scored_results"]
    score_by_identity = {
        (row["sequence"], row["question_id"], row["method"]): row
        for row in scores
    }
    by_method: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        method_outputs = [row for row in outputs if row.get("method") == method]
        method_scores = [row for row in scores if row.get("method") == method]
        completed = [
            row for row in method_scores
            if row.get("provider_outcome") == "PROVIDER_COMPLETE"
            and isinstance(row.get("score"), bool)
        ]
        correct = sum(row["score"] is True for row in completed)
        contexts = [
            float(row["context_tokens_estimated"])
            for row in method_outputs
            if isinstance(row.get("context_tokens_estimated"), (int, float))
            and not isinstance(row.get("context_tokens_estimated"), bool)
        ]
        latencies = [
            float(row["generation_latency_total_ms"])
            for row in method_outputs
            if isinstance(row.get("generation_latency_total_ms"), (int, float))
            and not isinstance(row.get("generation_latency_total_ms"), bool)
        ]
        by_method[method] = {
            "planned_method_cases": len(method_outputs),
            "provider_completed_scored_method_cases": len(completed),
            "provider_error_method_cases": sum(
                row.get("provider_outcome") == "PROVIDER_ERROR" for row in method_outputs
            ),
            "correct_from_sealed_score": correct,
            "incorrect_from_sealed_score": sum(row["score"] is False for row in completed),
            "accuracy_provider_completed_denominator": correct / len(completed) if completed else None,
            "accuracy_planned_denominator": correct / len(method_outputs) if method_outputs else None,
            "mean_context_tokens_estimated": _mean(contexts),
            "mean_generation_latency_ms_observed": _mean(latencies),
            "latency_case_count": len(latencies),
        }

    paired_by_qa: dict[str, dict[str, bool]] = defaultdict(dict)
    for planned in verified["expected_plan"]:
        identity = (planned["sequence"], planned["qa_id"], planned["method"])
        row = score_by_identity[identity]
        if row.get("provider_outcome") == "PROVIDER_COMPLETE" and isinstance(row.get("score"), bool):
            paired_by_qa[planned["qa_id"]][planned["method"]] = row["score"]
    paired = [
        methods for methods in paired_by_qa.values()
        if all(method in methods for method in METHODS)
    ]
    paired_counts = {
        "both_correct": sum(pair[METHODS[0]] and pair[METHODS[1]] for pair in paired),
        "flat_only_correct": sum(pair[METHODS[0]] and not pair[METHODS[1]] for pair in paired),
        "temporal_only_correct": sum(not pair[METHODS[0]] and pair[METHODS[1]] for pair in paired),
        "both_wrong": sum(not pair[METHODS[0]] and not pair[METHODS[1]] for pair in paired),
    }
    progress = verified["progress"]
    manifest = verified["manifest"]
    return {
        "schema_version": "memoryagentbench-g32-offline-finalized-summary/v1",
        "run_id": verified["run_id"],
        "original_live_verdict": original_live_status,
        "live_data_collection_status": "LIVE_DATA_COLLECTION_COMPLETE",
        "finalization_status": "RUN_LEVEL_PACKAGING_FINALIZED_OFFLINE",
        "method_case_executions_processed": len(outputs),
        "method_case_rows_with_sealed_scores": len(scores),
        "provider_completed_method_cases": sum(
            row.get("provider_outcome") == "PROVIDER_COMPLETE" for row in outputs
        ),
        "provider_error_method_cases": sum(
            row.get("provider_outcome") == "PROVIDER_ERROR" for row in outputs
        ),
        "methods": by_method,
        "paired_complete_qa_count": len(paired),
        "paired_correctness_matrix_from_sealed_scores": paired_counts,
        "usage_and_cost_evidence": {
            "generation_attempts_recorded_in_progress": progress.get("generation_attempts"),
            "count_tokens_attempts_recorded_in_progress": progress.get("count_tokens_attempts"),
            "retries_recorded_in_progress": progress.get("retries"),
            "hard_cost_cap_usd": manifest.get("hard_cost_cap_usd"),
            "base_planned_attempts_static_cost_upper_bound_usd": manifest.get(
                "base_planned_attempts_static_cost_upper_bound_usd"
            ),
            "all_allowed_attempts_static_cost_upper_bound_usd": manifest.get(
                "all_allowed_attempts_static_cost_upper_bound_usd"
            ),
            "actual_provider_cost": "NOT_REPORTED_BY_PROVIDER",
        },
        "frozen_fingerprints": manifest["implementation_fingerprints"],
        "aggregate_source": "scored_results.json stored score fields; no scorer or benchmark context was rerun",
        "semantic_accuracy_claim": "descriptive held-out sample only; no statistical significance or generalized superiority claim",
        "provider_calls_during_offline_finalization": 0,
    }


def _finalizer_fingerprint(repository_root: Path) -> dict[str, Any]:
    relative_paths = (
        "benchmarks/memoryagentbench/g32_finalization.py",
        "scripts/finalize_memoryagentbench_g32_offline.py",
    )
    files: dict[str, str] = {}
    for relative in relative_paths:
        path = repository_root / relative
        if path.is_file():
            files[relative] = _sha256_file(path)
    if not files:
        raise G32FinalizationError("finalizer implementation files are unavailable for fingerprinting")
    return {
        "sha256": _sha256_bytes(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "files": files,
    }


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise G32FinalizationError(f"refusing to overwrite existing artifact: {path.name}") from error


def finalize_g32_run_offline(
    run_directory: str | Path,
    *,
    original_live_status: str,
    original_finalizer_exception: str,
    finalized_at: str | None = None,
) -> dict[str, Any]:
    """Validate sealed source results and write only additive offline artifacts."""
    root = Path(run_directory).resolve()
    if not root.is_dir():
        raise G32FinalizationError(f"run directory is missing: {root}")
    if original_live_status != "HELDOUT_50_INCOMPLETE":
        raise G32FinalizationError("original live verdict must remain HELDOUT_50_INCOMPLETE")
    if not original_finalizer_exception.strip():
        raise G32FinalizationError("original finalizer exception must be preserved")
    if any((root / filename).exists() for filename in DERIVED_FILES):
        raise G32FinalizationError("offline-finalized artifact already exists; refusing overwrite")

    verified = _validate_source_artifacts(root)
    repository_root = Path(__file__).resolve().parents[2]
    summary = _build_summary(verified, original_live_status)
    finalized_timestamp = finalized_at or datetime.now(UTC).isoformat()
    source_hashes = {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(set(verified["source_paths"]))
    }
    finalizer_fingerprint = _finalizer_fingerprint(repository_root)
    record = {
        "schema_version": "memoryagentbench-g32-offline-finalization-record/v1",
        "run_id": verified["run_id"],
        "original_live_status": original_live_status,
        "finalization_status": "RUN_LEVEL_PACKAGING_FINALIZED_OFFLINE",
        "original_finalizer_exception": original_finalizer_exception,
        "source_artifact_hashes": source_hashes,
        "finalizer_implementation_fingerprint": finalizer_fingerprint,
        "finalization_timestamp": finalized_timestamp,
        "provider_calls": {"generation": 0, "count_tokens": 0},
        "provider_calls_total": 0,
        "rescoring": False,
        "rerun": False,
        "source_results_changed": False,
        "benchmark_context_reread": False,
        "sealed_result_rows_reused": len(verified["scored_results"]),
    }
    summary["original_finalizer_exception"] = original_finalizer_exception

    # Detect concurrent source changes before any additive artifact is written.
    if any(_sha256_file(path) != source_hashes[path.relative_to(root).as_posix()] for path in verified["source_paths"]):
        raise G32FinalizationError("source artifacts changed during offline finalization")

    summary_payload = _json_bytes(summary)
    record_payload = _json_bytes(record)
    summary_hash = _sha256_bytes(summary_payload)
    record_hash = _sha256_bytes(record_payload)
    artifact_manifest = {
        "schema_version": "memoryagentbench-g32-offline-artifact-manifest/v1",
        "run_id": verified["run_id"],
        "original_live_status": original_live_status,
        "source_artifact_hashes": source_hashes,
        "derived_artifact_hashes": {
            "pilot_summary.offline_finalized.json": summary_hash,
            "offline_finalization_record.json": record_hash,
        },
        "offline_finalization_record_sha256": record_hash,
        "manifest_self_hash_included": False,
    }
    manifest_payload = _json_bytes(artifact_manifest)

    _write_exclusive(root / "pilot_summary.offline_finalized.json", summary_payload)
    _write_exclusive(root / "offline_finalization_record.json", record_payload)
    _write_exclusive(root / "artifact_manifest.offline_finalized.json", manifest_payload)
    return {
        "run_id": verified["run_id"],
        "finalization_status": "RUN_LEVEL_PACKAGING_FINALIZED_OFFLINE",
        "live_data_collection_status": "LIVE_DATA_COLLECTION_COMPLETE",
        "source_artifacts_changed": False,
        "provider_calls": 0,
        "method_case_executions_processed": len(verified["provider_outputs"]),
        "summary_path": root / "pilot_summary.offline_finalized.json",
        "record_path": root / "offline_finalization_record.json",
        "manifest_path": root / "artifact_manifest.offline_finalized.json",
    }
