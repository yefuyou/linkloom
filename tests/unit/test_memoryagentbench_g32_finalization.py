from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.memoryagentbench.g32_heldout import (
    EXPECTED_FROZEN,
    build_execution_order,
    heldout_qa_ids,
)
from benchmarks.memoryagentbench.g32_finalization import (
    G32FinalizationError,
    finalize_g32_run_offline,
)


def _write_json(path: Path, value: object) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _create_sealed_run(root: Path) -> list[Path]:
    methods = ("Flat Retrieval", "LinkLoom Temporal Memory")
    plan = build_execution_order(heldout_qa_ids())
    fingerprints = {
        key: value for key, value in EXPECTED_FROZEN.items()
        if key.endswith("_fingerprint")
    }
    subset = {
        "qa_ids": heldout_qa_ids(),
        "dataset_revision": EXPECTED_FROZEN["dataset_revision"],
        "dataset_sha256": EXPECTED_FROZEN["dataset_sha256"],
        "subset_hash": EXPECTED_FROZEN["subset_hash"],
        "subset_manifest_sha256": EXPECTED_FROZEN["subset_manifest_sha256"],
        "fingerprints": fingerprints,
    }
    outputs: list[dict[str, object]] = []
    scored: list[dict[str, object]] = []
    journal: list[dict[str, object]] = []
    source_paths: list[Path] = []

    for item in plan:
        sequence = int(item["sequence"])
        qa_id = str(item["qa_id"])
        method = str(item["method"])
        slug = "flat" if method == methods[0] else "temporal"
        stem = f"{sequence:02}-{qa_id}-{slug}"
        provider_outcome = (
            "PROVIDER_ERROR"
            if qa_id.endswith("no99") and method == methods[1]
            else "PROVIDER_COMPLETE"
        )
        output_row: dict[str, object] = {
            "sequence": sequence,
            "question_id": qa_id,
            "method": method,
            "provider_outcome": provider_outcome,
            "generation_attempt_count": 1,
            "generation_attempts": [{
                "attempt_number": 1,
                "provider_outcome": provider_outcome,
                "reported_input_tokens": 50 if provider_outcome == "PROVIDER_COMPLETE" else None,
                "reported_output_tokens": 3 if provider_outcome == "PROVIDER_COMPLETE" else None,
            }],
            "context_tokens_estimated": 140 if method == methods[0] else 38,
            "generation_latency_total_ms": 1_000.0,
            "input_tokens": 50 if provider_outcome == "PROVIDER_COMPLETE" else None,
            "output_tokens": 3 if provider_outcome == "PROVIDER_COMPLETE" else None,
        }
        score_row = {**output_row, "score": provider_outcome == "PROVIDER_COMPLETE"}
        outputs.append(output_row)
        scored.append(score_row)

        output_path = root / "method_cases" / f"{stem}-provider-output.json"
        output_payload = _write_json(output_path, {
            "schema_version": "memoryagentbench-g3-provider-output/v1",
            "gold_loaded": False,
            "results": [output_row],
        })
        output_hash = _sha256(output_payload)
        output_seal_path = root / "method_cases" / f"{stem}-provider-output.seal.json"
        _write_json(output_seal_path, {
            "schema_version": "memoryagentbench-g3-provider-output-seal/v1",
            "sealed": True,
            "sha256": output_hash,
        })
        scored_path = root / "method_cases" / f"{stem}-scored.json"
        scored_payload = _write_json(scored_path, {
            "provider_output_sha256": output_hash,
            "result": score_row,
        })
        scored_hash = _sha256(scored_payload)
        case_seal_path = root / "method_cases" / f"{stem}-case.seal.json"
        case_seal_payload = _write_json(case_seal_path, {
            "schema_version": "memoryagentbench-g3-method-case-seal/v1",
            "sealed": True,
            "provider_output_sha256": output_hash,
            "scored_result_sha256": scored_hash,
        })
        journal.append({
            "event": "CASE_METHOD_SEALED",
            "run_id": "synthetic-g32-run",
            "sequence": sequence,
            "qa_id": qa_id,
            "method": method,
            "case_seal_path": case_seal_path.relative_to(root).as_posix(),
            "case_seal_sha256": _sha256(case_seal_payload),
        })
        source_paths.extend((output_path, output_seal_path, scored_path, case_seal_path))

    root_files = {
        "run_manifest.json": {
            "run_id": "synthetic-g32-run",
            "dataset_revision": EXPECTED_FROZEN["dataset_revision"],
            "dataset_sha256": EXPECTED_FROZEN["dataset_sha256"],
            "subset_manifest_sha256": EXPECTED_FROZEN["subset_manifest_sha256"],
            "implementation_fingerprints": fingerprints,
            "qa_ids": heldout_qa_ids(),
            "planned_method_case_executions": 100,
            "heldout_execution_plan": plan,
            "subset_manifest": subset,
        },
        "preflight.json": {
            "heldout_preflight_run_id": "synthetic-g32-run",
            "dataset_revision": EXPECTED_FROZEN["dataset_revision"],
            "implementation_fingerprints": fingerprints,
            "preflight_status": "READY",
        },
        "qualification.json": {"provider_calls": 0, "status": "READY"},
        "provider_outputs.json": {
            "schema_version": "memoryagentbench-g3-provider-outputs/v1",
            "gold_loaded": False,
            "results": outputs,
        },
        "scored_results.json": {
            "schema_version": "memoryagentbench-g3-posthoc-scores/v2",
            "provider_outputs_sha256": "",
            "gold_loaded_after_each_provider_output_seal": True,
            "scoring_error": None,
            "results": scored,
        },
        "provider_progress.json": {
            "run_id": "synthetic-g32-run",
            "generation_attempts": 99,
            "count_tokens_attempts": 99,
            "method_case_executions_completed": 100,
            "provider_completed_method_cases": 99,
            "provider_error_method_cases": 1,
            "retries": 0,
        },
    }
    output_payload = _write_json(root / "provider_outputs.json", root_files["provider_outputs.json"])
    root_files["scored_results.json"]["provider_outputs_sha256"] = _sha256(output_payload)
    for filename, value in root_files.items():
        path = root / filename
        if filename == "provider_outputs.json":
            source_paths.append(path)
            continue
        payload = _write_json(path, value)
        source_paths.append(path)
        if filename == "subset_manifest.json":
            continue
        if filename == "run_manifest.json":
            continue
    subset_payload = _write_json(root / "subset_manifest.json", subset)
    subset_seal = _write_json(root / "subset_manifest.seal.json", {
        "sealed": True,
        "sha256": _sha256(subset_payload),
    })
    provider_payload = output_payload
    _write_json(root / "provider_outputs.seal.json", {
        "schema_version": "memoryagentbench-g3-provider-output-seal/v1",
        "sealed": True,
        "sha256": _sha256(provider_payload),
    })
    journal_path = root / "provider_attempt_journal.jsonl"
    journal_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in journal),
        encoding="utf-8",
    )
    source_paths.extend((root / "subset_manifest.json", root / "subset_manifest.seal.json", root / "provider_outputs.seal.json", journal_path))
    return source_paths


def test_offline_finalizer_reconstructs_sealed_run_without_provider_or_rescoring(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "g32-heldout50-synthetic-g32-run"
    source_paths = _create_sealed_run(run_root)
    original_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}

    result = finalize_g32_run_offline(
        run_root,
        original_live_status="HELDOUT_50_INCOMPLETE",
        original_finalizer_exception="NameError: name 'preflight_snapshot' is not defined",
        finalized_at="2026-09-26T00:00:00+00:00",
    )

    summary_path = run_root / "pilot_summary.offline_finalized.json"
    record_path = run_root / "offline_finalization_record.json"
    manifest_path = run_root / "artifact_manifest.offline_finalized.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result["finalization_status"] == "RUN_LEVEL_PACKAGING_FINALIZED_OFFLINE"
    assert summary["original_live_verdict"] == "HELDOUT_50_INCOMPLETE"
    assert summary["live_data_collection_status"] == "LIVE_DATA_COLLECTION_COMPLETE"
    assert summary["method_case_executions_processed"] == 100
    assert summary["provider_completed_method_cases"] == 99
    assert summary["provider_error_method_cases"] == 1
    assert record["provider_calls"] == {"generation": 0, "count_tokens": 0}
    assert record["rescoring"] is False
    assert record["rerun"] is False
    assert record["source_results_changed"] is False
    assert manifest["offline_finalization_record_sha256"] == hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths} == original_hashes
    assert not (run_root / "pilot_summary.json").exists()


def test_offline_finalizer_rejects_tampered_source_before_writing_outputs(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "g32-heldout50-tampered-synthetic-run"
    _create_sealed_run(run_root)
    provider_outputs = run_root / "provider_outputs.json"
    provider_outputs.write_bytes(provider_outputs.read_bytes() + b" ")

    with pytest.raises(G32FinalizationError, match="seal"):
        finalize_g32_run_offline(
            run_root,
            original_live_status="HELDOUT_50_INCOMPLETE",
            original_finalizer_exception="NameError: name 'preflight_snapshot' is not defined",
            finalized_at="2026-09-26T00:00:00+00:00",
        )

    assert not (run_root / "pilot_summary.offline_finalized.json").exists()
    assert not (run_root / "offline_finalization_record.json").exists()
    assert not (run_root / "artifact_manifest.offline_finalized.json").exists()
