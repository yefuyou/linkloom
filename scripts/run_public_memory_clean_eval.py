"""Prepare and run the frozen SH-32k and MH-6k clean MemoryAgentBench cells."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import socket
import sys
import time
import traceback
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from linkloom.agents.providers.retry_policy import classify_retryable_failure  # noqa: E402
from benchmarks.memoryagentbench.adapter import (  # noqa: E402
    GOLD_FREE_INPUT_COLUMNS,
    load_factconsolidation_case,
)
from benchmarks.memoryagentbench.g3_pilot import (  # noqa: E402
    G3_METHODS,
    METHOD_FLAT_RETRIEVAL,
    METHOD_TEMPORAL_MEMORY,
    build_generation_request,
    normalize_official_answer,
    prepare_method_context,
    score_official_substring_exact_match,
)
from benchmarks.memoryagentbench.memory import build_flat_index, build_temporal_memory  # noqa: E402
from benchmarks.memoryagentbench.public_memory_profile import (  # noqa: E402
    ARTIFACT_ROOT,
    CELLS,
    CLEAN_EVAL_ROOT,
    DATASET_PATH,
    DATASET_SHA256,
    FINGERPRINT_FILES,
    GLOBAL_COST_CAP_USD,
    INPUT_TOKEN_CAP,
    INPUT_USD_PER_MILLION,
    MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
    MAX_CONTEXT_CHARS,
    MAX_INPUT_CONTEXT_TOKENS,
    METHODS,
    MODEL,
    OUTPUT_TOKEN_CAP,
    OUTPUT_USD_PER_MILLION,
    PRICING_SOURCE_URL,
    PROXY_HOST,
    PROXY_PORT,
    REQUEST_TIMEOUT_SECONDS,
    SDK_VERSION,
    CleanEvalIntegrityError,
    canonical_sha256,
    implementation_fingerprints,
    request_cost_upper_bound_usd,
    verify_frozen_artifacts,
)


def _clean_eval_reason_category(reason: Any) -> str:
    text = str(reason).upper()
    token = "".join(char if char.isascii() and (char.isalnum() or char == "_") else "_" for char in text)
    while "__" in token:
        token = token.replace("__", "_")
    token = token.strip("_").removeprefix("CLEAN_EVAL_BLOCK_")
    if any(part in token for part in ("GOLD", "OFFICIAL_GOLD", "DIAGNOSTIC_SCORING")):
        return "GOLD"
    if any(part in token for part in ("FINGERPRINT", "IMPLEMENTATION_MISMATCH", "PROFILE_MISMATCH", "REQUEST_HASH", "MODEL_DRIFT", "OUTPUT_CAP_DRIFT", "PINNED_DATASET_HASH")):
        return "FINGERPRINT"
    if any(part in token for part in ("COST", "BUDGET", "CAP", "SPEND", "RESERVATION", "RESERVE")):
        return "COST"
    if any(part in token for part in ("JOURNAL", "ARTIFACT", "SEAL", "HASH_MISMATCH", "OUTPUT_PERSIST", "OUTPUT_UNREADABLE")):
        return "ARTIFACT"
    if any(part in token for part in ("SCORE", "SCORER", "PYARROW")):
        return "SCORING"
    if any(part in token for part in ("RESPONSE", "USAGE", "COUNT_TOKENS")):
        return "RESPONSE"
    if any(part in token for part in ("METHOD_CASE", "EXECUTION", "STATE_TRANSITION", "INVALID_STATE", "INVARIANT", "PROTOCOL", "COMPLETION", "DUPLICATE", "RUNTIME_CASE", "RUNTIME_METHOD", "UNKNOWN_CLEAN_EVAL", "RUN_ARTIFACT")):
        return "PROTOCOL"
    if any(part in token for part in ("PROXY", "CLASH", "GEMINI_SDK", "GEMINI_API_KEY")):
        return "HARNESS"
    return "OTHER"


def _stable_clean_eval_reason_code(reason: Any) -> str:
    prefix = "CLEAN_EVAL_BLOCK_"
    text = str(reason).upper()
    token = "".join(char if char.isascii() and (char.isalnum() or char == "_") else "_" for char in text)
    while "__" in token:
        token = token.replace("__", "_")
    token = token.strip("_")
    if token.startswith(prefix):
        token = token[len(prefix):]
    if not token:
        token = "UNCLASSIFIED"
    category = _clean_eval_reason_category(token)
    max_reason_length = 160 - len(prefix) - len(category) - 1
    return f"{prefix}{category}_{token[:max_reason_length]}"


class CleanEvalBlocked(RuntimeError):
    """A fixed safe reason to stop the clean evaluation pipeline."""

    def __init__(self, reason: Any) -> None:
        self.reason_code = _stable_clean_eval_reason_code(reason)
        super().__init__(reason)


def _guard_criticality_level(reason_code: Any) -> str:
    token = str(reason_code).upper().removeprefix("CLEAN_EVAL_BLOCK_")
    if any(part in token for part in (
        "TRACEBACK", "DEBUG_ONLY", "OPTIONAL_METADATA", "REPORT_FORMAT",
        "SUMMARY_FORMAT", "DIAGNOSTIC_COUNTER", "AUXILIARY_LABEL",
    )):
        return "OBSERVABILITY_FORMAT_ONLY"
    if any(part in token for part in (
        "USAGE_UNAVAILABLE", "COUNT_TOKENS_ATTEMPT_NOT_JOURNALED",
        "OPTIONAL_LATENCY", "OPTIONAL_PROVIDER_REQUEST_ID", "TIMING_MISSING",
    )):
        return "COST_TELEMETRY_DEGRADED"
    if any(part in token for part in (
        "RESPONSE_TEXT_MALFORMED", "ANSWER_UNPARSABLE", "STRUCTURED_FIELD_MISSING",
        "EMPTY_RESPONSE_CONTENT",
    )):
        return "SCOREABILITY_CRITICAL"
    # Every other current throw site can change experiment identity, request
    # semantics, scoring, evidence attribution, or cost safety. New sites stay
    # critical until explicitly classified.
    return "EVALUATION_CRITICAL"


def _evaluation_guard_decision(
    guard_category: str,
    *,
    phase: str,
    provider_response_accepted: bool,
    response_valid: bool,
    evidence_valid: bool,
    cost_upper_bound_valid: bool,
    dataset_identity_valid: bool,
    scoring_identity_valid: bool,
) -> str:
    """Apply the single formal-evaluation disposition policy to a guard."""
    critical_phases = {
        "GOLD_ACCESS", "DATASET_IDENTITY", "REQUEST_IDENTITY",
        "SCORING_IDENTITY", "FINGERPRINT_VALIDATION",
    }
    category = str(guard_category).upper()
    if (
        not dataset_identity_valid
        or not scoring_identity_valid
        or str(phase).upper() in critical_phases
        or category == "EVALUATION_CRITICAL"
        or not cost_upper_bound_valid
        or (provider_response_accepted and not evidence_valid)
    ):
        return "RUN_HARD_STOP"
    if provider_response_accepted and not response_valid:
        return "CASE_NOT_EVALUATED"
    if category == "SCOREABILITY_CRITICAL":
        return "CASE_NOT_EVALUATED"
    if category == "COST_TELEMETRY_DEGRADED":
        return "CONTINUE_WITH_DEGRADED_TELEMETRY"
    if category == "OBSERVABILITY_FORMAT_ONLY":
        return "WARNING_ONLY"
    return "RUN_HARD_STOP"


def _question_ids_from_plan_rows(plan_rows: list[Mapping[str, Any]]) -> list[str]:
    question_ids = list(dict.fromkeys(
        str(row["qa_id"])
        for row in plan_rows
        if row.get("method") == METHODS[0] and isinstance(row.get("qa_id"), str)
    ))
    if not question_ids or any(not value for value in question_ids):
        raise CleanEvalBlocked("SCORING_PLAN_QUESTION_IDENTITIES_INVALID")
    return question_ids


def _score_coverage_can_still_pass(
    output_by_sequence: Mapping[int, Mapping[str, Any]],
    plan_rows: list[Mapping[str, Any]],
    *,
    minimum_scored_coverage: float = 0.95,
    minimum_paired_coverage: float = 0.95,
) -> bool:
    """Return whether remaining unstarted cases could still meet frozen coverage."""
    question_ids = _question_ids_from_plan_rows(plan_rows)
    minimum_method_count = math.ceil(len(question_ids) * minimum_scored_coverage)
    minimum_pair_count = math.ceil(len(question_ids) * minimum_paired_coverage)
    by_pair: dict[tuple[str, str], str | None] = {}
    for plan in plan_rows:
        sequence = int(plan["sequence"])
        row = output_by_sequence.get(sequence)
        by_pair[(str(plan["qa_id"]), str(plan["method"]))] = (
            str(row.get("provider_outcome")) if isinstance(row, Mapping) else None
        )
    for method in METHODS:
        possible = sum(
            by_pair[(qa_id, method)] in {None, "NOT_RUN", "PROVIDER_COMPLETE"}
            for qa_id in question_ids
        )
        if possible < minimum_method_count:
            return False
    possible_pairs = sum(
        all(
            by_pair[(qa_id, method)] in {None, "NOT_RUN", "PROVIDER_COMPLETE"}
            for method in METHODS
        )
        for qa_id in question_ids
    )
    return possible_pairs >= minimum_pair_count


def _evaluation_plan_sha256(plan: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(plan))


def _frozen_score_protocol_matches(filename: str, protocol: Any) -> bool:
    if not isinstance(protocol, dict) or protocol.get("status") != "PRE_REGISTERED_OFFLINE_ONLY":
        return False
    if filename == "E1_SCORE_PROTOCOL.json":
        return (
            protocol.get("accuracy_denominator", {}).get("per_method")
            == "all 100 preallocated MH-6k QA"
            and protocol.get("accuracy_denominator", {}).get("primary_accuracy", "").startswith("correct_count / 100;")
            and "not removed from this denominator" in protocol.get("accuracy_denominator", {}).get("primary_accuracy", "")
            and "100-QA" in protocol.get("provider_errors", {}).get("scoring", "")
            and protocol.get("paired_comparison", {}).get("valid_pair")
            == "same QA has both methods with sealed PROVIDER_COMPLETE outputs and boolean official scores"
            and "out of all 100" in protocol.get("paired_comparison", {}).get("denominator", "")
            and "do not unlock Gold" in protocol.get("usage_incomplete_or_local_block", {}).get("scoring", "")
        )
    if filename == "E1_SCORE_PROTOCOL_AMENDMENT.json":
        gate = protocol.get("formal_runner_score_gate", {})
        return (
            gate.get("completed_method_cases_required") == 200
            and gate.get("provider_completion_rate_minimum") == 0.9
            and gate.get("provider_outputs_must_be_sealed_before_gold") is True
            and "NOT_EVALUATED" in protocol.get("provider_error_resolution", {}).get("below_90_percent", "")
            and "NOT_EVALUATED" in protocol.get("usage_incomplete_or_local_block", "")
        )
    return False


def _load_evaluation_plan(plan_id: str) -> dict[str, Any]:
    try:
        registry = json.loads(EVALUATION_PLAN_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("EVALUATION_PLAN_REGISTRY_UNREADABLE") from None
    if not isinstance(registry, dict):
        raise CleanEvalBlocked("EVALUATION_PLAN_REGISTRY_INVALID")
    plans = registry.get("plans")
    plan = plans.get(plan_id) if isinstance(plans, dict) else None
    if (
        registry.get("schema_version") != "linkloom-public-memory-evaluation-plan-registry/v1"
        or not isinstance(plan, dict)
        or plan.get("plan_id") != plan_id
    ):
        raise CleanEvalBlocked("EVALUATION_PLAN_UNKNOWN_OR_INVALID")
    if plan_id == LEGACY_DUAL_EVALUATION_PLAN_ID:
        prerequisite = [{
            "cell": "sh_32k",
            "minimum_completed_runs": 1,
            "fingerprint_must_match": True,
        }]
        historical = plan.get("historical_baseline")
        relative = Path(str((historical or {}).get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise CleanEvalBlocked("LEGACY_EVALUATION_PLAN_HISTORY_MISMATCH")
        historical_path = EVALUATION_PLAN_REGISTRY_PATH.parent / relative
        if (
            plan.get("dataset_sequence") != ["sh_32k", "mh_6k"]
            or plan.get("mh_prerequisites") != prerequisite
            or not isinstance(historical, dict)
            or not historical_path.is_file()
            or historical.get("sha256") != hashlib.sha256(historical_path.read_bytes()).hexdigest()
            or not _verify_seal(
                historical_path,
                identity="authorized-execution-baseline-v3",
            )
        ):
            raise CleanEvalBlocked("LEGACY_EVALUATION_PLAN_HISTORY_MISMATCH")
    elif plan_id == MH_ONLY_EVALUATION_PLAN_ID:
        legacy = plans.get(LEGACY_DUAL_EVALUATION_PLAN_ID)
        predecessor = plan.get("historical_predecessor")
        if (
            plan.get("dataset_sequence") != ["mh_6k"]
            or plan.get("mh_prerequisites") != []
            or not isinstance(legacy, dict)
            or not isinstance(predecessor, dict)
            or predecessor.get("plan_id") != LEGACY_DUAL_EVALUATION_PLAN_ID
            or predecessor.get("baseline_sha256") != legacy.get("historical_baseline", {}).get("sha256")
            or plan.get("diagnostic_classification") != "SH_32K:DIAGNOSTIC_STRESS_SET"
        ):
            raise CleanEvalBlocked("MH_ONLY_EVALUATION_PLAN_POLICY_MISMATCH")
    elif plan_id == E2_MH_ONLY_EVALUATION_PLAN_ID:
        subset_ref = plan.get("subset_manifest")
        score_ref = plan.get("score_protocol")
        if (
            plan.get("status") != "ACTIVE"
            or plan.get("dataset_sequence") != ["mh_6k"]
            or plan.get("mh_prerequisites") != []
            or plan.get("revision_classification") != "REVISED_HELDOUT_AFTER_STOPPED_E1_1"
            or plan.get("source_plan_id") != MH_ONLY_EVALUATION_PLAN_ID
            or plan.get("diagnostic_classification") != "SH_32K:DIAGNOSTIC_STRESS_SET"
            or plan.get("question_count") != E2_SUBSET_QA_COUNT
            or plan.get("method_case_count") != E2_PLANNED_METHOD_CASES
            or plan.get("run_hard_cap_usd") != CELLS["mh_6k"].hard_cost_cap_usd
            or plan.get("global_hard_cap_usd") != GLOBAL_COST_CAP_USD
            or not isinstance(subset_ref, dict)
            or not isinstance(score_ref, dict)
        ):
            raise CleanEvalBlocked("E2_EVALUATION_PLAN_POLICY_MISMATCH")
        for reference, expected_path, expected_identity, reason in (
            (
                subset_ref,
                E2_SUBSET_MANIFEST_PATH,
                "e2-revised-heldout-subset",
                "E2_SUBSET_MANIFEST_INVALID",
            ),
            (
                score_ref,
                E2_SCORE_PROTOCOL_PATH,
                "e2-score-protocol",
                "E2_SCORE_PROTOCOL_INVALID",
            ),
        ):
            if (
                reference.get("path") != expected_path.relative_to(CLEAN_EVAL_ROOT).as_posix()
                or not _verify_seal(expected_path, identity=expected_identity)
                or hashlib.sha256(expected_path.read_bytes()).hexdigest() != reference.get("sha256")
            ):
                raise CleanEvalBlocked(reason)
        try:
            subset = json.loads(E2_SUBSET_MANIFEST_PATH.read_text(encoding="utf-8"))
            protocol = json.loads(E2_SCORE_PROTOCOL_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("E2_PLAN_ARTIFACT_UNREADABLE") from None
        if (
            subset.get("schema_version") != "linkloom-e2-revised-heldout-subset/v1"
            or subset.get("subset_sha256") != plan.get("subset_sha256")
            or subset.get("question_count") != E2_SUBSET_QA_COUNT
            or subset.get("prior_generation_overlap") != []
            or protocol.get("status") != "PRE_REGISTERED_OFFLINE_ONLY"
            or protocol.get("denominator", {}).get("per_method") != E2_SUBSET_QA_COUNT
            or protocol.get("gold_unlock_gate", {}).get("minimum_scored_coverage_per_method") != E2_MIN_SCORED_COVERAGE
            or protocol.get("gold_unlock_gate", {}).get("minimum_paired_coverage") != E2_MIN_PAIRED_COVERAGE
            or protocol.get("gold_unlock_gate", {}).get("maximum_case_local_blocks") != E2_MAX_CASE_LOCAL_BLOCKS
            or protocol.get("gold_unlock_gate", {}).get("allowed_case_local_block_reason_codes") != sorted(E2_CASE_LOCAL_REASON_CODES)
        ):
            raise CleanEvalBlocked("E2_PLAN_ARTIFACT_POLICY_MISMATCH")
    else:
        raise CleanEvalBlocked("EVALUATION_PLAN_UNKNOWN_OR_INVALID")
    return plan


def _build_e2_subset_manifest_payload(
    *,
    allow_active_run_id: str | None = None,
) -> dict[str, Any]:
    """Derive the revised MH subset from Gold-free IDs and sealed prior-run metadata."""
    cell = CELLS["mh_6k"]
    source_manifest, source_plan = verify_frozen_artifacts(cell)
    official_ids = list(source_manifest["selection"]["qa_ids_in_official_order"])
    if len(official_ids) != 100 or len(set(official_ids)) != len(official_ids):
        raise CleanEvalBlocked("E2_SOURCE_SUBSET_INVALID")

    ledger = _load_budget_ledger(
        path=FORMAL_BUDGET_LEDGER_PATH,
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    active_run_id = ledger.get("active_run_id")
    active_run_row = None
    if active_run_id is not None:
        active_run_row = next(
            (
                row for row in ledger.get("runs", [])
                if isinstance(row, dict) and row.get("run_id") == active_run_id
            ),
            None,
        )
        active_manifest_path = ARTIFACT_ROOT / "mh_6k" / str(active_run_id) / "run_manifest.json"
        if (
            allow_active_run_id != active_run_id
            or not isinstance(active_run_row, dict)
            or active_run_row.get("cell") != "mh_6k"
            or not _verify_seal(active_manifest_path, identity=f"run-manifest:{active_run_id}")
        ):
            raise CleanEvalBlocked("E2_FORMAL_HISTORY_NOT_RECONCILED")
        try:
            active_manifest = json.loads(active_manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("E2_ACTIVE_RUN_MANIFEST_INVALID") from None
        registered_plan = _load_evaluation_plan(E2_MH_ONLY_EVALUATION_PLAN_ID)
        if (
            active_manifest.get("run_id") != active_run_id
            or active_manifest.get("status") != "RUNNING"
            or active_manifest.get("evaluation_plan_id") != E2_MH_ONLY_EVALUATION_PLAN_ID
            or active_manifest.get("subset_sha256") != registered_plan.get("subset_sha256")
        ):
            raise CleanEvalBlocked("E2_ACTIVE_RUN_MANIFEST_INVALID")
    mh_rows = [
        row for row in ledger.get("runs", [])
        if isinstance(row, dict)
        and row.get("cell") == "mh_6k"
        and row.get("run_id") != active_run_id
    ]
    if {row.get("run_id") for row in mh_rows} != {
        E2_PRIOR_MH_RUN_ID,
        E2_ZERO_PROVIDER_STOPPED_RUN_ID,
    }:
        raise CleanEvalBlocked("E2_FORMAL_HISTORY_NOT_RECONCILED")

    attempted_qa_ids: set[str] = set()
    prior_run_evidence: list[dict[str, Any]] = []
    mh_artifact_root = ARTIFACT_ROOT / "mh_6k"
    discovered_run_ids = sorted(path.name for path in mh_artifact_root.iterdir() if path.is_dir()) if mh_artifact_root.exists() else []
    historical_discovered_ids = [run_id for run_id in discovered_run_ids if run_id != active_run_id]
    if set(historical_discovered_ids) != {
        E2_PRIOR_MH_RUN_ID,
        E2_ZERO_PROVIDER_STOPPED_RUN_ID,
    }:
        raise CleanEvalBlocked("E2_UNREGISTERED_MH_RUN_ARTIFACTS")

    for row in mh_rows:
        run_id = str(row["run_id"])
        if run_id == E2_ZERO_PROVIDER_STOPPED_RUN_ID:
            if not _verify_zero_provider_stopped_e2_run(run_id):
                raise CleanEvalBlocked("E2_ZERO_PROVIDER_STOPPED_RUN_EVIDENCE_INVALID")
            continue
        root = mh_artifact_root / run_id
        manifest_path = root / "run_manifest.json"
        summary_path = root / "pilot_summary.json"
        outputs_path = root / "provider_outputs.json"
        journal_path = root / "provider_attempt_journal.jsonl"
        evidence_path = root / "accepted_response_evidence.jsonl"
        artifact_manifest_path = root / "artifact_manifest.json"
        if (
            not _verify_seal(manifest_path, identity=f"run-manifest:{run_id}")
            or not _verify_seal(summary_path, identity=f"live-summary:{run_id}")
            or not _verify_seal(outputs_path, identity=f"provider-outputs:{cell.source}")
            or not _verify_seal(artifact_manifest_path, identity=f"artifact-manifest:{run_id}")
            or _verify_published_artifact_manifest(root, run_id).get("status") != "PASS"
            or not journal_path.is_file()
            or not evidence_path.is_file()
        ):
            raise CleanEvalBlocked("E2_PRIOR_MH_ARTIFACT_INTEGRITY_FAILURE")
        try:
            run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            outputs_payload = json.loads(outputs_path.read_text(encoding="utf-8"))
            events = _read_attempt_journal(journal_path)
            evidence = _read_accepted_response_evidence(evidence_path)
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("E2_PRIOR_MH_ARTIFACT_UNREADABLE") from None
        output_rows = outputs_payload.get("provider_outputs") if isinstance(outputs_payload, dict) else None
        if (
            not isinstance(run_manifest, dict)
            or not isinstance(summary, dict)
            or run_manifest.get("run_id") != run_id
            or summary.get("run_id") != run_id
            or run_manifest.get("cell_key") != "mh_6k"
            or run_manifest.get("status") != "STOPPED"
            or run_manifest.get("stop_reason") != "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE"
            or summary.get("status") != run_manifest.get("status")
            or summary.get("stop_reason") != run_manifest.get("stop_reason")
            or summary.get("gold_values_read") is not False
            or summary.get("gold_values_persisted") is not False
            or summary.get("score_status") != "SKIPPED"
            or outputs_payload.get("gold_values_read") is not False
            or not isinstance(output_rows, list)
            or len(output_rows) != 200
            or not evidence.get("integrity_valid")
            or summary.get("provider_completion", {}).get("completed_method_cases") != 15
            or summary.get("provider_completion", {}).get("provider_response_completed_method_cases") != 16
            or summary.get("provider_completion", {}).get("local_harness_block_method_cases") != 1
            or summary.get("provider_completion", {}).get("provider_error_method_cases") != 0
        ):
            raise CleanEvalBlocked("E2_PRIOR_MH_RUN_OUTCOME_INVALID")

        by_sequence = {
            item.get("sequence"): item
            for item in output_rows
            if isinstance(item, dict) and isinstance(item.get("sequence"), int)
        }
        if len(by_sequence) != 200:
            raise CleanEvalBlocked("E2_PRIOR_MH_OUTPUT_SEQUENCE_INVALID")
        started_sequences = {
            event.get("sequence")
            for event in events
            if event.get("event") == "PROVIDER_ATTEMPT_STARTED"
        }
        accepted_sequences = {item.get("sequence") for item in evidence["rows"]}
        if not started_sequences.issubset(by_sequence) or not accepted_sequences.issubset(by_sequence):
            raise CleanEvalBlocked("E2_PRIOR_MH_ATTEMPT_IDENTITY_INVALID")
        attempted_sequences = started_sequences | accepted_sequences
        for sequence, output in by_sequence.items():
            attempts = output.get("provider_attempts", 0)
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                raise CleanEvalBlocked("E2_PRIOR_MH_OUTPUT_ATTEMPT_COUNT_INVALID")
            if attempts or sequence in attempted_sequences:
                qa_id = output.get("qa_id")
                if qa_id not in official_ids:
                    raise CleanEvalBlocked("E2_PRIOR_MH_QA_ID_INVALID")
                attempted_qa_ids.add(qa_id)
        if (
            run_id == E2_PRIOR_MH_RUN_ID
            and (
                len(accepted_sequences) != 16
                or len(attempted_sequences) != 16
                or len(attempted_qa_ids) != 8
            )
        ):
            raise CleanEvalBlocked("E2_PRIOR_MH_EXECUTION_SCOPE_MISMATCH")
        prior_run_evidence.append(
            {
                "run_id": run_id,
                "status": run_manifest.get("status"),
                "attempted_method_cases": len(attempted_sequences),
                "accepted_response_method_cases": len(accepted_sequences),
                "attempted_qa_ids": [qa_id for qa_id in official_ids if qa_id in attempted_qa_ids],
                "run_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "provider_outputs_sha256": hashlib.sha256(outputs_path.read_bytes()).hexdigest(),
                "attempt_journal_sha256": hashlib.sha256(journal_path.read_bytes()).hexdigest(),
                "accepted_response_evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                "artifact_manifest_sha256": hashlib.sha256(artifact_manifest_path.read_bytes()).hexdigest(),
            }
        )

    selected_ids = [qa_id for qa_id in official_ids if qa_id not in attempted_qa_ids]
    if len(selected_ids) != E2_SUBSET_QA_COUNT or set(selected_ids) & attempted_qa_ids:
        raise CleanEvalBlocked("E2_REVISED_SUBSET_SELECTION_INVALID")
    body = {
        "schema_version": "linkloom-e2-revised-heldout-subset/v1",
        "plan_id": E2_MH_ONLY_EVALUATION_PLAN_ID,
        "classification": "REVISED_HELDOUT_AFTER_STOPPED_E1_1",
        "selection_rule": "official MH-6k order minus every QA with a prior generation attempt; no Gold access or answer-based selection",
        "source_freeze_manifest_sha256": source_manifest["freeze_manifest_sha256"],
        "source_subset_sha256": source_manifest["hashes"]["subset_sha256"],
        "source_execution_plan_sha256": source_plan.get("plan_sha256") or source_manifest["hashes"]["execution_plan_sha256"],
        "source_dataset_sha256": DATASET_SHA256,
        "question_count": len(selected_ids),
        "method_case_count": len(selected_ids) * len(METHODS),
        "selected_qa_ids_in_official_order": selected_ids,
        "prior_generation_overlap": [],
        "excluded_prior_generation_qa_ids": [qa_id for qa_id in official_ids if qa_id in attempted_qa_ids],
        "prior_run_evidence": prior_run_evidence,
        "source_columns_read": list(GOLD_FREE_INPUT_COLUMNS),
        "gold_values_read": False,
    }
    return {**body, "subset_sha256": canonical_sha256(body)}


def _verify_zero_provider_stopped_e2_run(run_id: str) -> bool:
    if run_id != E2_ZERO_PROVIDER_STOPPED_RUN_ID:
        return False
    root = ARTIFACT_ROOT / "mh_6k" / run_id
    manifest_path = root / "run_manifest.json"
    summary_path = root / "pilot_summary.json"
    outputs_path = root / "provider_outputs.json"
    journal_path = root / "provider_attempt_journal.jsonl"
    evidence_path = root / "accepted_response_evidence.jsonl"
    if (
        not _verify_seal(manifest_path, identity=f"run-manifest:{run_id}")
        or not _verify_seal(summary_path, identity=f"live-summary:{run_id}")
        or not _verify_seal(outputs_path, identity="provider-outputs:factconsolidation_mh_6k")
    ):
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        outputs = json.loads(outputs_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        return False
    rows = outputs.get("provider_outputs") if isinstance(outputs, dict) else None
    return bool(
        isinstance(manifest, dict)
        and isinstance(summary, dict)
        and isinstance(rows, list)
        and manifest.get("run_id") == run_id
        and manifest.get("evaluation_plan_id") == E2_MH_ONLY_EVALUATION_PLAN_ID
        and manifest.get("status") == "STOPPED"
        and manifest.get("stop_reason") == "CLEAN_EVAL_BLOCK_OTHER_E2_ACTIVE_RUN_MANIFEST_INVALID"
        and manifest.get("planned_questions") == E2_SUBSET_QA_COUNT
        and manifest.get("planned_method_cases") == E2_PLANNED_METHOD_CASES
        and manifest.get("subset_sha256") is None
        and summary.get("status") == "STOPPED"
        and summary.get("protocol_status") == "INVALID"
        and summary.get("stop_reason") == manifest.get("stop_reason")
        and summary.get("gold_values_read") is False
        and summary.get("provider_requests") == {
            "generation_attempts": 0,
            "count_tokens_requests": 0,
            "sdk_automatic_retries": 0,
            "harness_attempt_limit_per_logical_generation": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
        }
        and summary.get("accepted_response_evidence", {}).get("record_count") == 0
        and summary.get("provider_completion", {}).get("not_run_method_cases") == E2_PLANNED_METHOD_CASES
        and summary.get("harness_execution", {}).get("started_method_cases", 0) == 0
        and outputs.get("gold_values_read") is False
        and len(rows) == E2_PLANNED_METHOD_CASES
        and all(row.get("provider_outcome") == "NOT_RUN" for row in rows)
        and journal_path.is_file()
        and journal_path.stat().st_size == 0
        and evidence_path.is_file()
        and evidence_path.stat().st_size == 0
        and _verify_published_artifact_manifest(root, run_id).get("status") == "PASS"
    )


def _ensure_e2_subset_manifest() -> dict[str, Any]:
    expected = _build_e2_subset_manifest_payload()
    if E2_SUBSET_MANIFEST_PATH.exists():
        if not _verify_seal(E2_SUBSET_MANIFEST_PATH, identity="e2-revised-heldout-subset"):
            raise CleanEvalBlocked("E2_SUBSET_MANIFEST_ALREADY_EXISTS_INVALID")
        try:
            existing = json.loads(E2_SUBSET_MANIFEST_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("E2_SUBSET_MANIFEST_ALREADY_EXISTS_INVALID") from None
        if existing != expected:
            raise CleanEvalBlocked("E2_SUBSET_MANIFEST_ALREADY_EXISTS_DIFFERENT")
        return existing
    E2_QUALIFICATION_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_json_write_fsync(E2_SUBSET_MANIFEST_PATH, expected)
    _seal_file(E2_SUBSET_MANIFEST_PATH, identity="e2-revised-heldout-subset")
    return expected


def _read_e2_subset_manifest(*, allow_active_run_id: str | None = None) -> dict[str, Any]:
    if not _verify_seal(E2_SUBSET_MANIFEST_PATH, identity="e2-revised-heldout-subset"):
        raise CleanEvalBlocked("E2_SUBSET_MANIFEST_MISSING_OR_UNSEALED")
    try:
        actual = json.loads(E2_SUBSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("E2_SUBSET_MANIFEST_UNREADABLE") from None
    if actual != _build_e2_subset_manifest_payload(allow_active_run_id=allow_active_run_id):
        raise CleanEvalBlocked("E2_SUBSET_MANIFEST_OR_HISTORY_DRIFT")
    return actual


def _e2_score_protocol_payload(subset_manifest: Mapping[str, Any]) -> dict[str, Any]:
    predecessor_protocols: dict[str, dict[str, str]] = {}
    for filename, identity in (
        ("E1_SCORE_PROTOCOL.json", "e1-score-protocol"),
        ("E1_SCORE_PROTOCOL_AMENDMENT.json", "e1-score-protocol-amendment"),
    ):
        path = E1_SCORE_PROTOCOL_ROOT / filename
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        if not _verify_seal(path, identity=identity):
            raise CleanEvalBlocked("E2_PREDECESSOR_SCORE_PROTOCOL_INVALID")
        predecessor_protocols[filename] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seal_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
            "identity": identity,
        }
    return {
        "schema_version": "linkloom-public-memory-score-protocol/v2",
        "protocol_id": "E2_MH_ONLY_REVISED_HELDOUT_V1",
        "status": "PRE_REGISTERED_OFFLINE_ONLY",
        "created_for": E2_MH_ONLY_EVALUATION_PLAN_ID,
        "supersedes": "E1 MH_ONLY_CLEAN_V1 protocol for the revised 92-QA subset only",
        "historical_protocol_refs": predecessor_protocols,
        "selection": {
            "classification": "REVISED_HELDOUT_AFTER_STOPPED_E1_1",
            "prior_stopped_run_id": E2_PRIOR_MH_RUN_ID,
            "subset_manifest_sha256": subset_manifest["subset_sha256"],
            "selected_qa_ids_in_official_order": subset_manifest["selected_qa_ids_in_official_order"],
            "excluded_prior_generation_qa_ids": subset_manifest["excluded_prior_generation_qa_ids"],
            "prior_generation_overlap": [],
            "gold_used_for_selection": False,
        },
        "denominator": {
            "preallocated_questions": E2_SUBSET_QA_COUNT,
            "per_method": E2_SUBSET_QA_COUNT,
            "primary_accuracy": "correct_count / 92; all preallocated questions remain in the denominator",
            "required_categories": ["Correct", "Incorrect", "NOT_EVALUATED"],
            "provider_failure_and_local_block": "reported separately and counted NOT_EVALUATED; never removed from denominator",
        },
        "paired_comparison": {
            "valid_pair": "same preallocated QA has both methods with sealed PROVIDER_COMPLETE outputs and boolean official scores",
            "denominator": "paired coverage is valid pairs / all 92 preallocated QA",
            "reported_cells": [
                "both_correct",
                "flat_only_correct",
                "temporal_only_correct",
                "both_incorrect",
            ],
            "one_sided_completion": "NOT_EVALUATED for paired comparison; retained in each method's fixed denominator",
        },
        "usage_incomplete_or_local_block": {
            "valid_answer_with_safe_upper_bound": "usage_status INCOMPLETE remains PROVIDER_COMPLETE and scoreable; output_tokens stays unknown, with billing uncertainty and the reserved attempt upper bound preserved",
            "unusable_accepted_response": "only the exact preapproved local block reason below may continue; remains NOT_EVALUATED",
            "provider_failure": "counted as provider failure and NOT_EVALUATED after the fixed retry policy; never relabeled as memory failure",
            "other_local_failure": "run-level stop; no Gold read",
        },
        "gold_unlock_gate": {
            "minimum_scored_coverage_per_method": E2_MIN_SCORED_COVERAGE,
            "minimum_paired_coverage": E2_MIN_PAIRED_COVERAGE,
            "maximum_case_local_blocks": E2_MAX_CASE_LOCAL_BLOCKS,
            "allowed_case_local_block_reason_codes": sorted(E2_CASE_LOCAL_REASON_CODES),
            "all_184_method_case_lifecycles_must_be_terminal": True,
            "journal_evidence_provider_outputs_and_fingerprints_must_be_valid": True,
            "provider_outputs_must_be_sealed_before_gold": True,
            "gate_artifact_must_be_sealed_before_gold": True,
            "gate_failure": "do not unlock Gold; report protocol invalid and preserve NOT_EVALUATED outcomes",
        },
        "request_and_scoring_semantics": {
            "gemini_generation_request_unchanged": True,
            "memory_retrieval_adapter_prompt_unchanged": True,
            "official_substring_exact_match_scorer_unchanged": True,
            "scoring_implementation": "score_official_substring_exact_match",
        },
        "budget": {
            "run_hard_cap_usd": CELLS["mh_6k"].hard_cost_cap_usd,
            "formal_global_hard_cap_usd": GLOBAL_COST_CAP_USD,
            "historical_reservations_and_spend_remain_in_ledger": True,
        },
        "gold_values_read_during_preregistration": False,
        "provider_calls_during_preregistration": 0,
    }


def _ensure_e2_pre_registration() -> tuple[dict[str, Any], dict[str, Any]]:
    subset_manifest = _ensure_e2_subset_manifest()
    expected_protocol = _e2_score_protocol_payload(subset_manifest)
    if E2_SCORE_PROTOCOL_PATH.exists():
        if not _verify_seal(E2_SCORE_PROTOCOL_PATH, identity="e2-score-protocol"):
            raise CleanEvalBlocked("E2_SCORE_PROTOCOL_ALREADY_EXISTS_INVALID")
        try:
            actual_protocol = json.loads(E2_SCORE_PROTOCOL_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("E2_SCORE_PROTOCOL_ALREADY_EXISTS_INVALID") from None
        if actual_protocol != expected_protocol:
            raise CleanEvalBlocked("E2_SCORE_PROTOCOL_ALREADY_EXISTS_DIFFERENT")
    else:
        E2_QUALIFICATION_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_json_write_fsync(E2_SCORE_PROTOCOL_PATH, expected_protocol)
        _seal_file(E2_SCORE_PROTOCOL_PATH, identity="e2-score-protocol")

    registry = json.loads(EVALUATION_PLAN_REGISTRY_PATH.read_text(encoding="utf-8"))
    plans = registry.get("plans")
    if not isinstance(plans, dict):
        raise CleanEvalBlocked("EVALUATION_PLAN_REGISTRY_INVALID")
    plan = {
        "plan_id": E2_MH_ONLY_EVALUATION_PLAN_ID,
        "status": "ACTIVE",
        "dataset_sequence": ["mh_6k"],
        "mh_prerequisites": [],
        "source_plan_id": MH_ONLY_EVALUATION_PLAN_ID,
        "revision_classification": "REVISED_HELDOUT_AFTER_STOPPED_E1_1",
        "diagnostic_classification": "SH_32K:DIAGNOSTIC_STRESS_SET",
        "question_count": E2_SUBSET_QA_COUNT,
        "method_case_count": E2_PLANNED_METHOD_CASES,
        "subset_sha256": subset_manifest["subset_sha256"],
        "subset_manifest": {
            "path": E2_SUBSET_MANIFEST_PATH.relative_to(CLEAN_EVAL_ROOT).as_posix(),
            "sha256": hashlib.sha256(E2_SUBSET_MANIFEST_PATH.read_bytes()).hexdigest(),
        },
        "score_protocol": {
            "path": E2_SCORE_PROTOCOL_PATH.relative_to(CLEAN_EVAL_ROOT).as_posix(),
            "sha256": hashlib.sha256(E2_SCORE_PROTOCOL_PATH.read_bytes()).hexdigest(),
        },
        "run_hard_cap_usd": CELLS["mh_6k"].hard_cost_cap_usd,
        "global_hard_cap_usd": GLOBAL_COST_CAP_USD,
    }
    existing = plans.get(E2_MH_ONLY_EVALUATION_PLAN_ID)
    if existing is not None and existing != plan:
        raise CleanEvalBlocked("E2_EVALUATION_PLAN_ALREADY_EXISTS_DIFFERENT")
    if existing is None:
        plans[E2_MH_ONLY_EVALUATION_PLAN_ID] = plan
        _atomic_json_write_fsync(EVALUATION_PLAN_REGISTRY_PATH, registry)
    elif existing != plan:
        raise CleanEvalBlocked("E2_EVALUATION_PLAN_ALREADY_EXISTS_DIFFERENT")
    return subset_manifest, plan


def _verify_authorized_mh_only_baseline(
    qualification_dir: Path,
    fingerprints: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    baseline_path = qualification_dir / "AUTHORIZED_MH_ONLY_CLEAN_BASELINE.json"
    if not _verify_seal(baseline_path, identity="authorized-mh-only-clean-baseline"):
        raise CleanEvalBlocked("MH_ONLY_BASELINE_MISSING_OR_UNSEALED")
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("MH_ONLY_BASELINE_UNREADABLE") from None
    artifacts = baseline.get("qualification_artifacts") if isinstance(baseline, dict) else None
    registry_sha = fingerprints.get("files", {}).get(
        "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json"
    )
    if (
        not isinstance(baseline, dict)
        or baseline.get("schema_version") != "linkloom-authorized-mh-only-clean-baseline/v1"
        or baseline.get("baseline_id") != "AUTHORIZED_MH_ONLY_CLEAN_BASELINE"
        or baseline.get("status") != "AUTHORIZED_MH_ONLY_CLEAN_BASELINE"
        or baseline.get("live_execution_authorized") is not True
        or baseline.get("evaluation_plan_id") != plan.get("plan_id")
        or baseline.get("evaluation_plan_sha256") != _evaluation_plan_sha256(plan)
        or baseline.get("evaluation_plan_registry_sha256") != registry_sha
        or baseline.get("fingerprints") != fingerprints
        or not isinstance(artifacts, dict)
        or not set(MH_ONLY_REQUIRED_QUALIFICATION_ARTIFACTS).issubset(artifacts)
    ):
        raise CleanEvalBlocked("MH_ONLY_BASELINE_OR_PLAN_MISMATCH")
    for filename, expected_identity in MH_ONLY_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        reference = artifacts.get(filename)
        path = qualification_dir / filename
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        if (
            not isinstance(reference, dict)
            or reference.get("path") != filename
            or reference.get("identity") != expected_identity
            or not _verify_seal(path, identity=expected_identity)
            or hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256")
            or hashlib.sha256(seal_path.read_bytes()).hexdigest() != reference.get("seal_sha256")
        ):
            raise CleanEvalBlocked(f"MH_ONLY_QUALIFICATION_ARTIFACT_INVALID_{filename.upper()}")
    try:
        offline = json.loads((qualification_dir / "offline_qualification.json").read_text(encoding="utf-8"))
        semantic = json.loads((qualification_dir / "semantic_equivalence.json").read_text(encoding="utf-8"))
        route = json.loads((qualification_dir / "route_preflight.json").read_text(encoding="utf-8"))
        preflight = json.loads((qualification_dir / "mh_6k_preflight.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("MH_ONLY_QUALIFICATION_REPORT_UNREADABLE") from None
    if (
        offline.get("status") != "PASS"
        or offline.get("provider_calls") != 0
        or offline.get("official_gold_reads") != 0
        or offline.get("scoring_enabled") is not False
        or not isinstance(offline.get("checks"), dict)
        or set(offline["checks"]) != MH_ONLY_REQUIRED_OFFLINE_CHECKS
        or any(status != "PASS" for status in offline["checks"].values())
        or semantic.get("status") != "PASS"
        or semantic.get("generation_request_semantics_equal") is not True
        or set(semantic.get("semantic_invariants", {})) != {
            "adapter",
            "temporal_memory",
            "retrieval",
            "prompt",
            "gemini_generation_config",
            "dataset_subset",
            "scoring_rules",
        }
        or any(value is not True for value in semantic.get("semantic_invariants", {}).values())
        or route.get("status") != "PASS"
        or route.get("provider_requests") != 0
        or route.get("local_proxy", {}).get("hostname") != PROXY_HOST
        or route.get("local_proxy", {}).get("port") != PROXY_PORT
        or route.get("local_proxy", {}).get("tcp") != "PASS"
        or route.get("sdk_httpx_route", {}).get("route_inspection") != "PROXY"
        or route.get("sdk_httpx_route", {}).get("configured_proxy_port") != PROXY_PORT
        or preflight.get("status") != "PASS"
        or preflight.get("classification") != "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER"
        or preflight.get("prepared_method_case_count") != 200
        or preflight.get("question_count") != 100
        or preflight.get("gold_values_read") is not False
        or preflight.get("provider_calls") != {"generation": 0, "count_tokens": 0}
        or preflight.get("subset_sha256") != baseline.get("mh_6k_subset_sha256")
    ):
        raise CleanEvalBlocked("MH_ONLY_QUALIFICATION_REPORT_FAILED")
    budget = _load_budget_ledger(
        path=qualification_dir / "formal_budget_ledger.json",
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    if (
        budget.get("active_run_id") is not None
        or budget.get("ledger_sha256") != baseline.get("formal_budget_ledger_sha256")
        or not _cost_is_below_cap(
            _ledger_reserved_total(budget) + CELLS["mh_6k"].hard_cost_cap_usd,
            GLOBAL_COST_CAP_USD,
        )
    ):
        raise CleanEvalBlocked("MH_ONLY_FORMAL_BUDGET_SNAPSHOT_MISMATCH")
    try:
        regression_root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"])
        regression_counts = _junit_counts(
            regression_root.parse(qualification_dir / "validation_regression.xml").getroot()
        )
        focused_counts = _junit_counts(
            regression_root.parse(qualification_dir / "focused_tests.xml").getroot()
        )
    except Exception:
        raise CleanEvalBlocked("MH_ONLY_CANONICAL_REGRESSION_INVALID") from None
    if (
        regression_counts[0] < 422
        or regression_counts[1] != 0
        or regression_counts[2] != 0
        or focused_counts[0] < 7
        or focused_counts[1] != 0
        or focused_counts[2] != 0
    ):
        raise CleanEvalBlocked("MH_ONLY_CANONICAL_REGRESSION_INVALID")
    protocol_refs = baseline.get("score_protocol_artifacts")
    expected_protocols = {
        "E1_SCORE_PROTOCOL.json": "e1-score-protocol",
        "E1_SCORE_PROTOCOL_AMENDMENT.json": "e1-score-protocol-amendment",
    }
    if not isinstance(protocol_refs, dict) or not set(expected_protocols).issubset(protocol_refs):
        raise CleanEvalBlocked("MH_ONLY_SCORE_PROTOCOL_REFERENCE_MISSING")
    for filename, identity in expected_protocols.items():
        reference = protocol_refs[filename]
        expected_path = f"{E1_SCORE_PROTOCOL_ROOT.name}/{filename}"
        path = CLEAN_EVAL_ROOT / expected_path
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        if (
            not isinstance(reference, dict)
            or reference.get("path") != expected_path
            or reference.get("identity") != identity
            or not _verify_seal(path, identity=identity)
            or hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256")
            or hashlib.sha256(seal_path.read_bytes()).hexdigest() != reference.get("seal_sha256")
        ):
            raise CleanEvalBlocked("MH_ONLY_SCORE_PROTOCOL_REFERENCE_MISMATCH")
        try:
            protocol_payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("MH_ONLY_SCORE_PROTOCOL_REFERENCE_MISMATCH") from None
        if not _frozen_score_protocol_matches(filename, protocol_payload):
            raise CleanEvalBlocked("MH_ONLY_SCORE_PROTOCOL_REFERENCE_MISMATCH")
    return baseline


def _e2_qualification_artifact_path(qualification_dir: Path, filename: str) -> Path:
    frozen_assets = {
        "E2_SUBSET_MANIFEST.json": E2_SUBSET_MANIFEST_PATH,
        "E2_SCORE_PROTOCOL.json": E2_SCORE_PROTOCOL_PATH,
    }
    return frozen_assets.get(filename, qualification_dir / filename)


def _verify_h3_1_reconciliation_artifact(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        seal = json.loads(path.with_suffix(path.suffix + ".seal.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        return False
    identity = seal.get("identity") if isinstance(seal, dict) else None
    prefix = f"h3-1-evidence-reconciliation:{E2_H3_1_RECONCILIATION_RUN_ID}:"
    created_at = payload.get("created_at_utc") if isinstance(payload, dict) else None
    scope = payload.get("verification_scope") if isinstance(payload, dict) else None
    reconciliation = payload.get("ledger_reconciliation") if isinstance(payload, dict) else None
    if not isinstance(created_at, str):
        return False
    try:
        sealed_date = datetime.fromisoformat(created_at).strftime("%Y%m%d")
    except ValueError:
        return False
    if (
        not isinstance(identity, str)
        or not identity.startswith(prefix)
        or identity != f"{prefix}{sealed_date}"
        or not _verify_seal(path, identity=identity)
        or not isinstance(payload, dict)
        or payload.get("run_id") != E2_H3_1_RECONCILIATION_RUN_ID
        or payload.get("verification_status") != "PASS"
        or payload.get("overall_evidence_checks_pass") is not True
        or not isinstance(payload.get("checks"), dict)
        or any(value != "PASS" for value in payload["checks"].values())
        or not isinstance(scope, dict)
        or scope.get("provider_calls") != 0
        or scope.get("official_gold_reads") != 0
        or scope.get("raw_run_artifacts_modified") is not False
        or scope.get("ledger_files_written_by_this_audit") is not False
        or not isinstance(reconciliation, dict)
        or reconciliation.get("settlement_state")
        != "ALREADY_SETTLED_AND_VERIFIED; NO DUPLICATE EVENT APPENDED"
    ):
        return False
    return True


def _authorization_baseline_budget_hash_matches(
    baseline: Mapping[str, Any],
    budget_snapshot: Mapping[str, Any],
    *,
    e2_plan: bool,
) -> bool:
    if e2_plan:
        budget = baseline.get("budget")
        if not isinstance(budget, Mapping):
            return False
        expected_hash = budget.get("formal_budget_ledger_sha256")
    else:
        expected_hash = baseline.get("formal_budget_ledger_sha256")
    return isinstance(expected_hash, str) and expected_hash == budget_snapshot.get("ledger_sha256")


def _verify_authorized_e2_baseline(
    qualification_dir: Path,
    fingerprints: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    baseline_path = qualification_dir / "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE.json"
    if not _verify_seal(baseline_path, identity="authorized-final-evaluation-v1-baseline"):
        raise CleanEvalBlocked("E2_AUTHORIZED_BASELINE_MISSING_OR_UNSEALED")
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("E2_AUTHORIZED_BASELINE_UNREADABLE") from None
    registry_sha = fingerprints.get("files", {}).get(
        "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json"
    )
    references = baseline.get("qualification_artifacts") if isinstance(baseline, dict) else None
    if (
        not isinstance(baseline, dict)
        or baseline.get("schema_version") != "linkloom-authorized-final-evaluation-baseline/v1"
        or baseline.get("baseline_id") != "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE"
        or baseline.get("status") != "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE"
        or baseline.get("live_execution_authorized") is not True
        or baseline.get("criticality_policy_id") != FINAL_EVALUATION_POLICY_ID
        or baseline.get("criticality_policy_sha256") != hashlib.sha256(
            (qualification_dir / "FINAL_EVALUATION_POLICY_V1.json").read_bytes()
        ).hexdigest()
        or baseline.get("minimum_scored_coverage_per_method") != FINAL_EVALUATION_MIN_SCORED_COVERAGE
        or baseline.get("minimum_paired_coverage") != FINAL_EVALUATION_MIN_PAIRED_COVERAGE
        or baseline.get("case_local_block_count_hard_limit") is not None
        or baseline.get("coverage_gate_is_only_case_local_scoreability_limit") is not True
        or baseline.get("current_frozen_subset", {}).get("subset_sha256") != plan.get("subset_sha256")
        or baseline.get("current_frozen_subset", {}).get("question_count") != E2_SUBSET_QA_COUNT
        or baseline.get("current_frozen_subset", {}).get("method_case_count") != E2_PLANNED_METHOD_CASES
        or baseline.get("semantic_invariants") != {
            "gemini_generation_request_semantics_unchanged": True,
            "scoring_algorithm_and_fixed_denominator_unchanged": True,
            "memory_retrieval_adapter_prompt_unchanged": True,
            "dataset_and_subset_unchanged": True,
        }
        or baseline.get("evaluation_plan_id") != plan.get("plan_id")
        or baseline.get("evaluation_plan_sha256") != _evaluation_plan_sha256(plan)
        or baseline.get("evaluation_plan_registry_sha256") != registry_sha
        or baseline.get("fingerprints") != fingerprints
        or baseline.get("subset_sha256") != plan.get("subset_sha256")
        or not isinstance(references, dict)
        or set(references) != set(E2_REQUIRED_QUALIFICATION_ARTIFACTS)
    ):
        raise CleanEvalBlocked("E2_AUTHORIZED_BASELINE_OR_PLAN_MISMATCH")

    for filename, identity in E2_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        path = _e2_qualification_artifact_path(qualification_dir, filename)
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        reference = references.get(filename)
        if (
            not isinstance(reference, dict)
            or reference.get("path") != filename
            or reference.get("identity") != identity
            or not _verify_seal(path, identity=identity)
            or hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256")
            or hashlib.sha256(seal_path.read_bytes()).hexdigest() != reference.get("seal_sha256")
        ):
            raise CleanEvalBlocked(f"E2_QUALIFICATION_ARTIFACT_INVALID_{filename.upper()}")

    try:
        offline = json.loads((qualification_dir / "offline_qualification.json").read_text(encoding="utf-8"))
        preflight = json.loads((qualification_dir / "mh_6k_preflight.json").read_text(encoding="utf-8"))
        fake = json.loads((qualification_dir / "formal_fake_provider_e2e.json").read_text(encoding="utf-8"))
        policy = json.loads((qualification_dir / "FINAL_EVALUATION_POLICY_V1.json").read_text(encoding="utf-8"))
        guard_inventory = json.loads((qualification_dir / "guard_criticality_inventory.json").read_text(encoding="utf-8"))
        rehearsal = json.loads((qualification_dir / "formal_live_path_rehearsal.json").read_text(encoding="utf-8"))
        rehearsal_ledger = _load_budget_ledger(
            path=qualification_dir / "formal_rehearsal_budget_ledger.json",
            hard_cap_usd=GLOBAL_COST_CAP_USD,
        )
        semantic = json.loads((qualification_dir / "semantic_equivalence.json").read_text(encoding="utf-8"))
        route = json.loads((qualification_dir / "route_preflight.json").read_text(encoding="utf-8"))
        budget_snapshot = _load_budget_ledger(
            path=qualification_dir / "formal_budget_ledger.json",
            hard_cap_usd=GLOBAL_COST_CAP_USD,
        )
        reviewer = json.loads((qualification_dir / "reviewer_verdict.json").read_text(encoding="utf-8"))
        subset = json.loads(E2_SUBSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("E2_QUALIFICATION_ARTIFACT_UNREADABLE") from None

    if (
        offline.get("status") != "PASS"
        or offline.get("provider_calls") != 0
        or offline.get("official_gold_reads") != 0
        or offline.get("scoring_enabled") is not False
        or offline.get("checks") != {name: "PASS" for name in E2_REQUIRED_OFFLINE_CHECKS}
        or preflight.get("status") != "PASS"
        or preflight.get("classification") != "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER"
        or preflight.get("question_count") != E2_SUBSET_QA_COUNT
        or preflight.get("prepared_method_case_count") != E2_PLANNED_METHOD_CASES
        or preflight.get("subset_sha256") != plan.get("subset_sha256")
        or preflight.get("gold_values_read") is not False
        or preflight.get("provider_calls") != {"generation": 0, "count_tokens": 0}
        or fake.get("status") != "PASS"
        or fake.get("provider_calls") != 0
        or fake.get("official_gold_reads") != 0
        or fake.get("scoring_enabled") is not False
        or fake.get("planned_method_cases") != E2_PLANNED_METHOD_CASES
        or fake.get("usage_incomplete_case_status") != "PROVIDER_COMPLETE_AND_SCOREABLE"
        or fake.get("one_sided_case_status") != "NOT_EVALUATED"
        or fake.get("gold_unlock_gate_pass_fixture") is not True
        or fake.get("gold_unlock_gate_fail_fixture_no_gold_read") is not True
        or policy != _final_evaluation_policy_payload()
        or guard_inventory.get("status") != "PASS"
        or guard_inventory.get("production_reachable_throw_sites", 0) <= 0
        or guard_inventory.get("unclassified_criticality_sites") != 0
        or rehearsal.get("status") != "PASS"
        or rehearsal.get("provider_calls") != {"generation": 0, "count_tokens": 0}
        or rehearsal.get("gold_values_read") is not False
        or rehearsal.get("primary_stop_reason") != "FORMAL_LIVE_PATH_REHEARSAL_STOP_BEFORE_PROVIDER"
        or rehearsal_ledger.get("active_run_id") is not None
        or semantic.get("status") != "PASS"
        or semantic.get("generation_request_semantics_equal") is not True
        or semantic.get("scoring_algorithm_equal") is not True
        or any(value is not True for value in semantic.get("semantic_invariants", {}).values())
        or route.get("status") != "PASS"
        or route.get("provider_requests") != 0
        or route.get("local_proxy", {}).get("hostname") != PROXY_HOST
        or route.get("local_proxy", {}).get("port") != PROXY_PORT
        or route.get("local_proxy", {}).get("tcp") != "PASS"
        or route.get("sdk_httpx_route", {}).get("route_inspection") != "PROXY"
        or route.get("sdk_httpx_route", {}).get("configured_proxy_port") != PROXY_PORT
        or budget_snapshot.get("active_run_id") is not None
        or not _authorization_baseline_budget_hash_matches(
            baseline,
            budget_snapshot,
            e2_plan=True,
        )
        or not _cost_is_below_cap(
            _ledger_reserved_total(budget_snapshot) + CELLS["mh_6k"].hard_cost_cap_usd,
            GLOBAL_COST_CAP_USD,
        )
        or reviewer.get("verdict") != "PASS"
        or reviewer.get("read_only") is not True
        or reviewer.get("gold_values_read") is not False
        or reviewer.get("evaluation_can_run_with_safe_degradation") != "YES"
        or subset.get("prior_generation_overlap") != []
        or subset.get("question_count") != E2_SUBSET_QA_COUNT
        or subset.get("method_case_count") != E2_PLANNED_METHOD_CASES
        or subset.get("subset_sha256") != plan.get("subset_sha256")
    ):
        raise CleanEvalBlocked("E2_QUALIFICATION_REPORT_FAILED")

    try:
        element_tree = __import__("xml.etree.ElementTree", fromlist=["ElementTree"])
        regression_counts = _junit_counts(element_tree.parse(qualification_dir / "validation_regression.xml").getroot())
        focused_counts = _junit_counts(element_tree.parse(qualification_dir / "focused_tests.xml").getroot())
    except Exception:
        raise CleanEvalBlocked("E2_CANONICAL_REGRESSION_INVALID") from None
    if (
        regression_counts[0] < 427
        or regression_counts[1] != 0
        or regression_counts[2] != 0
        or focused_counts[0] < 24
        or focused_counts[1] != 0
        or focused_counts[2] != 0
    ):
        raise CleanEvalBlocked("E2_CANONICAL_REGRESSION_INVALID")
    return baseline


def _write_authorized_e2_baseline(
    *,
    qualification_dir: Path,
    fingerprints: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    if plan.get("plan_id") != E2_MH_ONLY_EVALUATION_PLAN_ID:
        raise CleanEvalBlocked("E2_BASELINE_PLAN_ID_INVALID")
    if implementation_fingerprints() != fingerprints:
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH_BEFORE_E2_AUTHORIZATION")
    if qualification_dir.resolve() != E2_QUALIFICATION_DIR.resolve():
        raise CleanEvalBlocked("E2_QUALIFICATION_PATH_MISMATCH")
    baseline_path = qualification_dir / "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE.json"
    if baseline_path.exists():
        raise CleanEvalBlocked("FINAL_V1_AUTHORIZED_BASELINE_ALREADY_EXISTS")
    current_ledger = _load_budget_ledger(
        path=FORMAL_BUDGET_LEDGER_PATH,
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    if current_ledger.get("active_run_id") is not None or not _cost_is_below_cap(
        _ledger_reserved_total(current_ledger) + CELLS["mh_6k"].hard_cost_cap_usd,
        GLOBAL_COST_CAP_USD,
    ):
        raise CleanEvalBlocked("E2_FORMAL_BUDGET_UNAVAILABLE")

    for filename, identity in E2_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        path = _e2_qualification_artifact_path(qualification_dir, filename)
        if not _verify_seal(path, identity=identity):
            raise CleanEvalBlocked(f"E2_QUALIFICATION_ARTIFACT_MISSING_OR_INVALID_{filename.upper()}")

    budget_snapshot_path = qualification_dir / "formal_budget_ledger.json"
    snapshot = _load_budget_ledger(path=budget_snapshot_path, hard_cap_usd=GLOBAL_COST_CAP_USD)
    if snapshot.get("ledger_sha256") != current_ledger.get("ledger_sha256"):
        raise CleanEvalBlocked("E2_FORMAL_BUDGET_SNAPSHOT_MISMATCH")
    if implementation_fingerprints() != fingerprints:
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH_BEFORE_E2_AUTHORIZATION")

    references = {}
    for filename, identity in E2_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        path = _e2_qualification_artifact_path(qualification_dir, filename)
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        references[filename] = {
            "path": filename,
            "identity": identity,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seal_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
        }
    prior_baseline_path = CLEAN_EVAL_ROOT / "e1_1_mh_only_clean_20261005_26f71b57" / "AUTHORIZED_MH_ONLY_CLEAN_BASELINE.json"
    e2_predecessor_baseline_path = E2_ASSET_DIR / "requalification_166f8bf8_r11" / "AUTHORIZED_E2_MH_ONLY_BASELINE.json"
    h3_reconciliation_path = CLEAN_EVAL_ROOT / "H3_1_EVIDENCE_RECONCILIATION_20261005.json"
    if (
        not _verify_seal(prior_baseline_path, identity="authorized-mh-only-clean-baseline")
        or not _verify_seal(e2_predecessor_baseline_path, identity="authorized-e2-mh-only-baseline")
        or not _verify_h3_1_reconciliation_artifact(h3_reconciliation_path)
    ):
        raise CleanEvalBlocked("FINAL_V1_HISTORICAL_EVIDENCE_REFERENCE_INVALID")
    payload = {
        "schema_version": "linkloom-authorized-final-evaluation-baseline/v1",
        "baseline_id": "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE",
        "status": "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "live_execution_authorized": True,
        "criticality_policy_id": FINAL_EVALUATION_POLICY_ID,
        "criticality_policy_sha256": hashlib.sha256(
            (qualification_dir / "FINAL_EVALUATION_POLICY_V1.json").read_bytes()
        ).hexdigest(),
        "minimum_scored_coverage_per_method": FINAL_EVALUATION_MIN_SCORED_COVERAGE,
        "minimum_paired_coverage": FINAL_EVALUATION_MIN_PAIRED_COVERAGE,
        "case_local_block_count_hard_limit": None,
        "coverage_gate_is_only_case_local_scoreability_limit": True,
        "evaluation_plan_id": plan["plan_id"],
        "evaluation_plan_sha256": _evaluation_plan_sha256(plan),
        "evaluation_plan_registry_sha256": fingerprints["files"][
            "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json"
        ],
        "fingerprints": fingerprints,
        "subset_sha256": plan["subset_sha256"],
        "question_count": E2_SUBSET_QA_COUNT,
        "method_case_count": E2_PLANNED_METHOD_CASES,
        "revision_reason": (
            "E2 failed before any generation or CountTokens request because its active manifest omitted the frozen subset hash. "
            "The zero-request stopped run is independently verified and consumes no QA; this final V1 baseline retains the existing 92-QA frozen subset."
        ),
        "predecessor": {
            "baseline_id": "AUTHORIZED_E2_MH_ONLY_BASELINE",
            "path": "requalification_166f8bf8_r11/AUTHORIZED_E2_MH_ONLY_BASELINE.json",
            "sha256": hashlib.sha256(e2_predecessor_baseline_path.read_bytes()).hexdigest(),
            "failed_live_run_id": E2_ZERO_PROVIDER_STOPPED_RUN_ID,
            "failed_live_run_provider_requests": {"generation": 0, "count_tokens": 0},
        },
        "current_frozen_subset": {
            "path": E2_SUBSET_MANIFEST_PATH.relative_to(CLEAN_EVAL_ROOT).as_posix(),
            "subset_sha256": plan["subset_sha256"],
            "question_count": E2_SUBSET_QA_COUNT,
            "method_case_count": E2_PLANNED_METHOD_CASES,
            "prior_generation_overlap": [],
        },
        "changed_files": [
            "scripts/run_public_memory_clean_eval.py",
            "scripts/audit_public_memory_clean_eval_guards.py",
            "tests/unit/test_public_memory_e2_usage_policy.py",
            "tests/unit/test_public_memory_profile.py",
        ],
        "semantic_invariants": {
            "gemini_generation_request_semantics_unchanged": True,
            "scoring_algorithm_and_fixed_denominator_unchanged": True,
            "memory_retrieval_adapter_prompt_unchanged": True,
            "dataset_and_subset_unchanged": True,
        },
        "historical_evidence": {
            "stopped_e1_1_run_id": E2_PRIOR_MH_RUN_ID,
            "zero_provider_stopped_e2_run_id": E2_ZERO_PROVIDER_STOPPED_RUN_ID,
            "e1_1_baseline_sha256": hashlib.sha256(prior_baseline_path.read_bytes()).hexdigest(),
            "e2_r11_baseline_sha256": hashlib.sha256(e2_predecessor_baseline_path.read_bytes()).hexdigest(),
            "h3_1_reconciliation_sha256": hashlib.sha256(h3_reconciliation_path.read_bytes()).hexdigest(),
            "excluded_prior_generation_qa_ids": json.loads(E2_SUBSET_MANIFEST_PATH.read_text(encoding="utf-8"))["excluded_prior_generation_qa_ids"],
            "prior_generation_overlap": [],
        },
        "historical_plan_preserved": {
            "plan_id": MH_ONLY_EVALUATION_PLAN_ID,
            "old_100_qa_selection_retained_as_history": True,
            "legacy_sh_first_plan_retained": True,
        },
        "budget": {
            "formal_budget_ledger_sha256": current_ledger["ledger_sha256"],
            "historical_formal_spend_upper_bound_usd": round(_ledger_reserved_total(current_ledger), 9),
            "global_hard_cap_usd": GLOBAL_COST_CAP_USD,
            "single_run_hard_cap_usd": CELLS["mh_6k"].hard_cost_cap_usd,
            "available_before_run_reservation_usd": round(GLOBAL_COST_CAP_USD - _ledger_reserved_total(current_ledger), 9),
        },
        "authorized_run": {
            "cell": "mh_6k",
            "qa_count": E2_SUBSET_QA_COUNT,
            "method_case_count": E2_PLANNED_METHOD_CASES,
            "method_order": ["Flat Retrieval", "LinkLoom Temporal Memory"],
            "generation_attempt_limit": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            "single_run_hard_cap_usd": CELLS["mh_6k"].hard_cost_cap_usd,
            "formal_global_hard_cap_usd": GLOBAL_COST_CAP_USD,
            "gold_unlock": "all method lifecycles terminal; at least 95% scored coverage per method and paired coverage; journal, evidence, outputs, fingerprints, subset identity, and cost pass",
            "scoring_protocol": "E2_SCORE_PROTOCOL.json exact-match denominator plus FINAL_EVALUATION_POLICY_V1.json coverage and criticality amendment",
        },
        "qualification_artifacts": references,
    }
    _atomic_json_write_fsync(baseline_path, payload)
    _seal_file(baseline_path, identity="authorized-final-evaluation-v1-baseline")
    verified = _verify_authorized_e2_baseline(qualification_dir, fingerprints, plan)
    return baseline_path, verified


def _reconcile_budget_row(
    ledger: dict[str, Any],
    *,
    run_id: str,
    status: str,
    estimated_spend_upper_bound_usd: float,
    expected_reserved_usd: float,
    hard_cap_usd: float,
) -> bool:
    if (
        ledger.get("hard_cap_usd") != hard_cap_usd
        or not _finite_nonnegative_usd(estimated_spend_upper_bound_usd)
        or not _finite_nonnegative_usd(expected_reserved_usd)
        or not _cost_is_below_cap(estimated_spend_upper_bound_usd, hard_cap_usd)
    ):
        raise CleanEvalBlocked("BUDGET_RECONCILIATION_CONFLICT")
    matches = [row for row in ledger.get("runs", []) if isinstance(row, dict) and row.get("run_id") == run_id]
    if len(matches) != 1:
        raise CleanEvalBlocked("BUDGET_RECONCILIATION_CONFLICT")
    row = matches[0]
    expected_estimate = round(estimated_spend_upper_bound_usd, 9)
    if (
        row.get("status") == status
        and row.get("reserved_usd") == 0.0
        and row.get("estimated_spend_upper_bound_usd") == expected_estimate
        and ledger.get("active_run_id") is None
    ):
        return False
    if (
        row.get("status") != "RUNNING"
        or row.get("reserved_usd") != expected_reserved_usd
        or row.get("estimated_spend_upper_bound_usd") != 0.0
        or ledger.get("active_run_id") != run_id
    ):
        raise CleanEvalBlocked("BUDGET_RECONCILIATION_CONFLICT")
    projected = _ledger_reserved_total(ledger) - expected_reserved_usd + expected_estimate
    if not _cost_is_below_cap(projected, hard_cap_usd):
        raise CleanEvalBlocked("GLOBAL_ESTIMATED_SPEND_REACHED_HARD_CAP")
    row["status"] = status
    row["reserved_usd"] = 0.0
    row["estimated_spend_upper_bound_usd"] = expected_estimate
    ledger["active_run_id"] = None
    return True


def _score_outcome_metrics(
    rows: list[Mapping[str, Any]],
    *,
    planned_denominator: int,
) -> dict[str, Any]:
    if planned_denominator <= 0 or len(rows) > planned_denominator:
        raise CleanEvalBlocked("SCORING_DENOMINATOR_INVALID")
    correct = sum(row.get("score") is True for row in rows)
    incorrect = sum(row.get("score") is False for row in rows)
    not_evaluated = planned_denominator - correct - incorrect
    return {
        "correct": correct,
        "incorrect": incorrect,
        "not_evaluated": not_evaluated,
        "provider_failure": sum(row.get("provider_outcome") == "PROVIDER_ERROR" for row in rows),
        "local_harness_block": sum(row.get("provider_outcome") == "LOCAL_HARNESS_BLOCK" for row in rows),
        "scored_coverage": (correct + incorrect) / planned_denominator,
        "primary_accuracy": correct / planned_denominator,
    }


def _score_protocol_eligibility(
    provider_outputs: list[Mapping[str, Any]],
    question_ids: list[str],
    *,
    minimum_scored_coverage: float | None = None,
    minimum_paired_coverage: float | None = None,
    maximum_case_local_blocks: int | None = None,
) -> dict[str, Any]:
    minimum_scored_coverage = (
        FINAL_EVALUATION_MIN_SCORED_COVERAGE
        if minimum_scored_coverage is None
        else minimum_scored_coverage
    )
    minimum_paired_coverage = (
        FINAL_EVALUATION_MIN_PAIRED_COVERAGE
        if minimum_paired_coverage is None
        else minimum_paired_coverage
    )
    if (
        not question_ids
        or not 0 < minimum_scored_coverage <= 1
        or not 0 < minimum_paired_coverage <= 1
        or (maximum_case_local_blocks is not None and maximum_case_local_blocks < 0)
    ):
        raise CleanEvalBlocked("SCORE_PROTOCOL_CONFIGURATION_INVALID")
    by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in provider_outputs:
        pair = (row.get("qa_id"), row.get("method"))
        if not all(isinstance(value, str) and value for value in pair) or pair in by_pair:
            raise CleanEvalBlocked("SCORE_PROTOCOL_METHOD_CASE_SET_INVALID")
        by_pair[pair] = row
    expected_pairs = {
        (qa_id, method)
        for qa_id in question_ids
        for method in METHODS
    }
    if set(by_pair) != expected_pairs:
        raise CleanEvalBlocked("SCORE_PROTOCOL_METHOD_CASE_SET_INVALID")

    reason_codes: dict[str, int] = {}
    invalid_outcomes: list[str] = []
    failure_evidence_errors: set[str] = set()
    for row in provider_outputs:
        outcome = row.get("provider_outcome")
        if outcome not in {"PROVIDER_COMPLETE", "PROVIDER_ERROR", "LOCAL_HARNESS_BLOCK"}:
            invalid_outcomes.append(str(outcome))
        if outcome in {"PROVIDER_ERROR", "LOCAL_HARNESS_BLOCK"}:
            reason = row.get("reason_code")
            stable_reason = (
                isinstance(reason, str)
                and 0 < len(reason) <= 160
                and reason.isascii()
                and reason == reason.upper()
                and all(character.isalnum() or character == "_" for character in reason)
            )
            safe_error = row.get("safe_error")
            if not stable_reason:
                failure_evidence_errors.add("FAILURE_REASON_CODE_MISSING_OR_INVALID")
            if not isinstance(safe_error, Mapping):
                failure_evidence_errors.add("FAILURE_SAFE_EVIDENCE_MISSING")
            else:
                if safe_error.get("reason_code") != reason:
                    failure_evidence_errors.add("FAILURE_REASON_CODE_EVIDENCE_MISMATCH")
                if safe_error.get("provider_outcome") != outcome:
                    failure_evidence_errors.add("FAILURE_OUTCOME_EVIDENCE_MISMATCH")
            if outcome == "PROVIDER_ERROR" and row.get("provider_response_accepted") is not False:
                failure_evidence_errors.add("ACCEPTED_RESPONSE_MISCLASSIFIED_AS_PROVIDER_ERROR")
        if outcome == "LOCAL_HARNESS_BLOCK":
            reason = row.get("reason_code")
            reason_codes[str(reason)] = reason_codes.get(str(reason), 0) + 1

    planned = len(question_ids)
    scored_by_method = {
        method: sum(
            by_pair[(qa_id, method)].get("provider_outcome") == "PROVIDER_COMPLETE"
            for qa_id in question_ids
        )
        for method in METHODS
    }
    method_coverage = {
        method: count / planned
        for method, count in scored_by_method.items()
    }
    paired_questions = [
        qa_id
        for qa_id in question_ids
        if all(
            by_pair[(qa_id, method)].get("provider_outcome") == "PROVIDER_COMPLETE"
            for method in METHODS
        )
    ]
    paired_coverage = len(paired_questions) / planned
    block_count = sum(reason_codes.values())
    errors: list[str] = []
    if invalid_outcomes:
        errors.append("NONTERMINAL_OR_UNKNOWN_METHOD_CASE_OUTCOME")
    errors.extend(sorted(failure_evidence_errors))
    if any(coverage < minimum_scored_coverage for coverage in method_coverage.values()):
        errors.append("METHOD_SCORED_COVERAGE_BELOW_MINIMUM")
    if paired_coverage < minimum_paired_coverage:
        errors.append("PAIRED_COVERAGE_BELOW_MINIMUM")
    if maximum_case_local_blocks is not None and block_count > maximum_case_local_blocks:
        errors.append("CASE_LOCAL_BLOCK_LIMIT_EXCEEDED")
    if any(reason not in E2_CASE_LOCAL_REASON_CODES for reason in reason_codes):
        errors.append("UNALLOWLISTED_CASE_LOCAL_BLOCK")
    return {
        "status": "PASS" if not errors else "FAIL",
        "eligible_for_gold_unlock": not errors,
        "planned_questions": planned,
        "planned_method_cases": len(expected_pairs),
        "scored_method_cases": sum(scored_by_method.values()),
        "scored_coverage_by_method": method_coverage,
        "minimum_scored_coverage": minimum_scored_coverage,
        "paired_questions": len(paired_questions),
        "paired_coverage": paired_coverage,
        "minimum_paired_coverage": minimum_paired_coverage,
        "case_local_blocks": block_count,
        "maximum_case_local_blocks": maximum_case_local_blocks,
        "case_local_block_reason_codes": reason_codes,
        "provider_errors": sum(
            row.get("provider_outcome") == "PROVIDER_ERROR"
            for row in provider_outputs
        ),
        "failure_evidence_errors": sorted(failure_evidence_errors),
        "not_evaluated_method_cases": len(provider_outputs) - sum(scored_by_method.values()),
        "errors": errors,
    }


def _junit_counts(root: Any) -> tuple[int, int, int]:
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    return tuple(
        sum(int(suite.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors")
    )


ORIGINAL_STOPPED_SH_RUN_ID = "96ab75da2ff945eb9d102a8f760e21a6"
ORIGINAL_STOPPED_SH_ARTIFACT_COUNT = 407
STOPPED_SH_V2_RUN_ID = "328738863a2c42b191c50224baa03ce8"
STOPPED_SH_V2_ARTIFACT_COUNT = 408
H3_1_DIAGNOSTIC_RUN_ID = "bc850e0cfe4745d79117660dd5a74367"
H3_1_DIAGNOSTIC_SPEND_UPPER_BOUND_USD = 0.8019
H3_1_DIAGNOSTIC_LEDGER_PATH = (
    CLEAN_EVAL_ROOT
    / "harness_stability_diagnostic"
    / "h3_1_20261004_089f8bf0"
    / "diagnostic_budget_ledger.json"
)
LEGACY_DUAL_EVALUATION_PLAN_ID = "SH32K_THEN_MH6K_V3"
MH_ONLY_EVALUATION_PLAN_ID = "MH_ONLY_CLEAN_V1"
E2_MH_ONLY_EVALUATION_PLAN_ID = "MH_ONLY_REVISED_HELDOUT_E2_V1"
EVALUATION_PLAN_REGISTRY_PATH = CLEAN_EVAL_ROOT / "evaluation_plan_registry.json"
E2_ASSET_DIR = CLEAN_EVAL_ROOT / "e2_revised_mh_only_20261005"
E2_QUALIFICATION_DIR = E2_ASSET_DIR / "final_evaluation_v1_20261005"
E2_SUBSET_MANIFEST_PATH = E2_ASSET_DIR / "E2_SUBSET_MANIFEST.json"
E2_SCORE_PROTOCOL_PATH = E2_ASSET_DIR / "E2_SCORE_PROTOCOL.json"
E2_PRIOR_MH_RUN_ID = "3474a8eefe3e4a48b41b8b1d503399b0"
E2_ZERO_PROVIDER_STOPPED_RUN_ID = "22c4ba5ca95a4890998aee1d68a69553"
E2_H3_1_RECONCILIATION_RUN_ID = "bc850e0cfe4745d79117660dd5a74367"
E2_SUBSET_QA_COUNT = 92
E2_PLANNED_METHOD_CASES = E2_SUBSET_QA_COUNT * len(METHODS)
FORMAL_BUDGET_LEDGER_PATH = CLEAN_EVAL_ROOT / "formal_evaluation_budget_ledger.json"
MH_ONLY_REQUIRED_QUALIFICATION_ARTIFACTS = {
    "mh_6k_preflight.json": "offline-preflight:mh_6k",
    "offline_qualification.json": "mh-only-offline-qualification",
    "semantic_equivalence.json": "mh-only-semantic-equivalence",
    "route_preflight.json": "mh-only-route-preflight",
    "ledger_reconciliation_report.json": "mh-only-ledger-reconciliation",
    "formal_budget_ledger.json": "mh-only-formal-budget-ledger",
    "focused_tests.xml": "mh-only-focused-tests",
    "validation_regression.xml": "mh-only-canonical-regression",
}
MH_ONLY_REQUIRED_OFFLINE_CHECKS = frozenset(
    {
        "mh_6k_frozen_subset",
        "gold_isolation",
        "formal_fake_provider_e2e",
        "usage_incomplete_fake_provider",
        "journal_evidence_finalizer",
        "ledger_reconciliation",
        "cost_hard_caps",
        "clash_7897_route",
        "canonical_regression",
        "score_protocol",
    }
)
E2_REQUIRED_OFFLINE_CHECKS = frozenset(
    {
        "mh_6k_revised_unrun_subset",
        "gold_isolation",
        "formal_fake_provider_e2e",
        "usage_incomplete_fake_provider",
        "journal_evidence_finalizer",
        "ledger_reconciliation",
        "cost_hard_caps",
        "clash_7897_route",
        "canonical_regression",
        "score_protocol",
        "independent_reviewer",
        "criticality_policy",
        "guard_criticality_inventory",
        "coverage_policy",
        "pre_case_finalizer",
        "formal_live_path_rehearsal",
    }
)
E2_REQUIRED_QUALIFICATION_ARTIFACTS = {
    "mh_6k_preflight.json": "offline-preflight:mh_6k",
    "E2_SUBSET_MANIFEST.json": "e2-revised-heldout-subset",
    "E2_SCORE_PROTOCOL.json": "e2-score-protocol",
    "FINAL_EVALUATION_POLICY_V1.json": "final-evaluation-policy-v1",
    "guard_criticality_inventory.json": "final-evaluation-guard-criticality-inventory",
    "offline_qualification.json": "e2-offline-qualification",
    "formal_fake_provider_e2e.json": "e2-formal-fake-provider-e2e",
    "semantic_equivalence.json": "e2-semantic-equivalence",
    "route_preflight.json": "e2-route-preflight",
    "budget_reconciliation_snapshot.json": "e2-budget-reconciliation",
    "formal_budget_ledger.json": "e2-formal-budget-ledger",
    "formal_rehearsal_budget_ledger.json": "formal-rehearsal-budget-ledger",
    "formal_live_path_rehearsal.json": "final-evaluation-formal-path-rehearsal",
    "focused_tests.xml": "e2-focused-tests",
    "validation_regression.xml": "e2-canonical-regression",
    "reviewer_verdict.json": "e2-independent-reviewer",
}
E1_SCORE_PROTOCOL_ROOT = CLEAN_EVAL_ROOT / "e1_clean_20261005_089f8bf0"
DIAGNOSTIC_RUN_CAP_USD = 6.0
DIAGNOSTIC_TASK_CAP_USD = 10.0
DIAGNOSTIC_DIR = CLEAN_EVAL_ROOT / "harness_stability_diagnostic"
DIAGNOSTIC_CASE_LOCAL_REASON_CODES = frozenset(
    {
        "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED",
        "CLEAN_EVAL_BLOCK_HARNESS_PROVIDER_USAGE_UNAVAILABLE",
        "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE",
    }
)
E2_CASE_LOCAL_REASON_CODES = frozenset(
    {"CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"}
)
E2_MIN_SCORED_COVERAGE = 0.90
E2_MIN_PAIRED_COVERAGE = 0.90
E2_MAX_CASE_LOCAL_BLOCKS = 2
FINAL_EVALUATION_MIN_SCORED_COVERAGE = 0.95
FINAL_EVALUATION_MIN_PAIRED_COVERAGE = 0.95
FINAL_EVALUATION_POLICY_ID = "FINAL_MH_ONLY_CRITICALITY_V1"


def _final_evaluation_policy_payload() -> dict[str, Any]:
    return {
        "schema_version": "linkloom-final-evaluation-criticality-policy/v1",
        "policy_id": FINAL_EVALUATION_POLICY_ID,
        "status": "FROZEN_BEFORE_PROVIDER",
        "criticality_levels": {
            "EVALUATION_CRITICAL": "Gold, dataset/subset, request/model, scoring, fingerprint, evidence identity, cross-case, or hard-cap integrity failure; RUN_HARD_STOP.",
            "SCOREABILITY_CRITICAL": "Accepted answer is malformed or cannot be parsed while receipt/evidence identity remains valid; CASE_NOT_EVALUATED.",
            "COST_TELEMETRY_DEGRADED": "Usage, CountTokens, latency, or optional provider telemetry is incomplete; continue only with valid conservative cost upper bound.",
            "OBSERVABILITY_FORMAT_ONLY": "Debug traceback, optional labels, or presentation-only metadata is missing; WARNING_ONLY.",
        },
        "decision_function": "scripts.run_public_memory_clean_eval._evaluation_guard_decision",
        "unknown_guard_default": "EVALUATION_CRITICAL",
        "case_local_block_count_hard_limit": None,
        "case_local_block_reason_codes": sorted(E2_CASE_LOCAL_REASON_CODES),
        "minimum_scored_coverage_per_method": FINAL_EVALUATION_MIN_SCORED_COVERAGE,
        "minimum_paired_coverage": FINAL_EVALUATION_MIN_PAIRED_COVERAGE,
        "coverage_denominator": "all 92 preallocated revised-heldout QA per method and for pairs",
        "below_threshold_behavior": "stop before another request once either method or paired coverage can no longer reach its frozen minimum; do not unlock Gold",
        "usage_incomplete_policy": {
            "accepted_valid_answer": "PROVIDER_COMPLETE and scoreable if request/response/evidence identity is durable and cost upper bound is valid",
            "usage_status": "INCOMPLETE",
            "missing_output_tokens": None,
            "billing_uncertainty": True,
            "cost_basis": "reserved conservative per-attempt upper bound",
        },
        "provider_failure_policy": "retry at most three times under the frozen retry policy; then NOT_EVALUATED and continue while coverage remains attainable",
        "not_evaluated_categories": ["provider failure", "case-local malformed response"],
        "gold_unlock_requirements": [
            "all 184 method-case lifecycles terminal",
            "at least 95% scored coverage per method",
            "at least 95% paired coverage",
            "sealed outputs and valid journal/evidence/fingerprints/subset identity",
            "cost reservation and actual upper bound stay within both hard caps",
        ],
        "generation_request_semantics_unchanged": True,
        "scoring_algorithm_and_denominator_unchanged": True,
        "memory_retrieval_adapter_prompt_unchanged": True,
        "dataset_and_subset_unchanged": True,
        "provider_calls_during_policy_registration": 0,
        "official_gold_reads_during_policy_registration": 0,
    }


SAFE_SMOKE_REASON_CODES = frozenset(
    {
        "CREDENTIAL_LEAK_BLOCKED",
        "PROVIDER_USAGE_UNAVAILABLE",
        "PREFLIGHT_UNDERCOUNT_MISMATCH",
        "REPORTED_COST_BUDGET_EXCEEDED",
        "AGGREGATE_REPORTED_COST_BUDGET_EXCEEDED",
        "PROJECTED_COST_BUDGET_EXCEEDED",
        "COUNT_TOKENS_FAILED",
        "COUNT_TOKENS_RESPONSE_MALFORMED",
        "COUNT_TOKENS_REQUEST_UNREPRESENTABLE",
        "STATIC_COUNT_TOKENS_BOUND_UNSAFE",
        "REQUEST_TIMEOUT_EXCEEDED",
        "CASE_PROVIDER_TIME_EXCEEDED",
        "AGGREGATE_PROVIDER_TIME_EXCEEDED",
        "REPORTED_INPUT_TOKEN_BUDGET_EXCEEDED",
        "REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED",
        "AGGREGATE_REPORTED_INPUT_TOKEN_BUDGET_EXCEEDED",
        "AGGREGATE_REPORTED_OUTPUT_TOKEN_BUDGET_EXCEEDED",
        "GENERATION_REQUEST_UNREPRESENTABLE",
        "GOLD_OR_EVALUATION_DATA_FORBIDDEN",
        "BLOCKED_PRE_SEND_GUARD",
    }
)


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _seal_file(path: Path, *, identity: str) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    seal = {
        "schema_version": "linkloom-public-memory-artifact-seal/v1",
        "identity": identity,
        "sealed": True,
        "sha256": digest,
        "sealed_at_utc": datetime.now(UTC).isoformat(),
    }
    _json_write(path.with_suffix(path.suffix + ".seal.json"), seal)
    return seal


def _verify_seal(path: Path, *, identity: str | None = None) -> bool:
    seal_path = path.with_suffix(path.suffix + ".seal.json")
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    return (
        isinstance(seal, dict)
        and seal.get("sealed") is True
        and seal.get("sha256") == digest
        and (identity is None or seal.get("identity") == identity)
    )


def _append_jsonl_fsync(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


_RESPONSE_USAGE_ALIASES = {
    "input_tokens": ("prompt_token_count", "input_tokens", "promptTokenCount"),
    "output_tokens": ("candidates_token_count", "output_tokens", "candidatesTokenCount"),
    "thinking_tokens": (
        "thoughts_token_count",
        "thinking_token_count",
        "thoughtsTokenCount",
        "thinkingTokenCount",
        "thoughts_tokens",
        "thinking_tokens",
    ),
}


def _response_usage_evidence(response: Any) -> dict[str, Any]:
    access_failures: list[str] = []

    def read_field(owner: Any, field_name: str, *, source_name: str) -> Any:
        try:
            return (
                owner.get(field_name)
                if isinstance(owner, Mapping)
                else getattr(owner, field_name, None)
            )
        except Exception:
            access_failures.append(source_name)
            return None

    usage_metadata = read_field(response, "usage_metadata", source_name="usage_metadata")
    if usage_metadata is None:
        usage_metadata = read_field(response, "usage", source_name="usage")

    normalized: dict[str, int | None] = {}
    sources: dict[str, str | None] = {}
    provider_fields: dict[str, int] = {}
    invalid_fields: list[str] = []
    for canonical_name, aliases in _RESPONSE_USAGE_ALIASES.items():
        value = None
        source = None
        for alias in aliases:
            candidate = read_field(
                usage_metadata,
                alias,
                source_name=f"usage.{alias}",
            )
            if candidate is not None:
                value = candidate
                source = alias
                break
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            invalid_fields.append(canonical_name)
            value = None
        normalized[canonical_name] = value
        sources[canonical_name] = source
        if value is not None and source is not None:
            provider_fields[source] = value

    complete = (
        not invalid_fields
        and normalized["input_tokens"] is not None
        and normalized["output_tokens"] is not None
    )
    missing_fields = [
        field
        for field, value in normalized.items()
        if value is None
    ]
    cost_value = None
    cost_source = None
    candidate_cost = read_field(usage_metadata, "cost_usd", source_name="usage.cost_usd")
    if (
        isinstance(candidate_cost, (int, float))
        and not isinstance(candidate_cost, bool)
        and math.isfinite(float(candidate_cost))
        and candidate_cost >= 0
    ):
        cost_value = float(candidate_cost)
        cost_source = "cost_usd"
    return {
        "usage_status": "INVALID" if invalid_fields else "COMPLETE" if complete else "INCOMPLETE",
        "usage_present": any(value is not None for value in normalized.values()),
        "usage_metadata": normalized,
        "usage_missing_fields": missing_fields,
        "usage_sources": sources,
        "provider_usage_object_present": usage_metadata is not None,
        "provider_usage_fields": provider_fields,
        "usage_invalid_fields": sorted(invalid_fields),
        "usage_access_failures": sorted(set(access_failures)),
        "billing_uncertainty": not complete,
        "billing_basis": "PROVIDER_REPORTED_USAGE" if complete else "RESERVED_ATTEMPT_UPPER_BOUND",
        "provider_reported_cost_usd": cost_value,
        "provider_reported_cost_source": cost_source,
    }


def _is_safe_diagnostic_label(value: Any, *, allow_spaces: bool = False) -> bool:
    allowed = "_.- " if allow_spaces else "_.-"
    return (
        isinstance(value, str)
        and 0 < len(value) <= 160
        and value.isascii()
        and all(character.isalnum() or character in allowed for character in value)
    )


def _atomic_json_write_fsync(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _persist_accepted_response_evidence(
    path: Path,
    *,
    run_id: str,
    sequence: int,
    logical_case_id: str,
    method: str,
    attempt_no: int,
    logical_attempt_id: str,
    model: str,
    request_hash: str,
    response: Any,
    on_response_received: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(run_id, str)
        or not run_id
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 1
        or not isinstance(logical_case_id, str)
        or not logical_case_id
        or not isinstance(method, str)
        or not method
        or isinstance(attempt_no, bool)
        or not isinstance(attempt_no, int)
        or attempt_no < 1
        or not isinstance(logical_attempt_id, str)
        or not logical_attempt_id
        or len(logical_attempt_id) > 128
        or not _is_sha256_hex(request_hash)
        or not isinstance(model, str)
        or not model
        or len(model) > 128
    ):
        raise CleanEvalIntegrityError("accepted response identity is invalid")

    response_hash: str | None = None
    try:
        response_text = (
            response.get("text")
            if isinstance(response, Mapping)
            else getattr(response, "text", None)
        )
        if isinstance(response_text, str):
            response_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
        else:
            dump = getattr(response, "model_dump_json", None)
            if callable(dump):
                serialized_response = dump()
                if isinstance(serialized_response, str):
                    response_hash = hashlib.sha256(serialized_response.encode("utf-8")).hexdigest()
    except Exception:
        response_hash = None

    usage = _response_usage_evidence(response)

    provider_request_id = None
    for attribute in ("response_id", "request_id", "id"):
        try:
            candidate = (
                response.get(attribute)
                if isinstance(response, Mapping)
                else getattr(response, attribute, None)
            )
        except Exception:
            continue
        if isinstance(candidate, str) and 0 < len(candidate) <= 128 and all(
            character.isascii() and (character.isalnum() or character in "-_.:")
            for character in candidate
        ):
            provider_request_id = candidate
            break

    evidence = {
        "run_id": run_id,
        "sequence": sequence,
        "case_id": logical_case_id,
        "method": method,
        "attempt_no": attempt_no,
        "logical_attempt_id": logical_attempt_id,
        "provider_response_accepted": True,
        "response_sha256": response_hash,
        "model": model,
        **usage,
        "request_sha256": request_hash,
        "provider_request_id": provider_request_id,
        "accepted_at_utc": datetime.now(UTC).isoformat(),
    }
    if on_response_received is not None:
        on_response_received(evidence)
    _append_jsonl_fsync(path, evidence)
    return evidence


def _evidence_usage_status(evidence: Mapping[str, Any]) -> str:
    explicit = evidence.get("usage_status")
    if explicit in {"COMPLETE", "INCOMPLETE", "INVALID"}:
        return explicit
    usage = evidence.get("usage_metadata")
    return (
        "COMPLETE"
        if isinstance(usage, dict)
        and all(
            isinstance(usage.get(name), int)
            and not isinstance(usage.get(name), bool)
            and usage.get(name) >= 0
            for name in ("input_tokens", "output_tokens")
        )
        else "INCOMPLETE"
    )


def _evidence_usage_is_complete(evidence: Mapping[str, Any]) -> bool:
    return _evidence_usage_status(evidence) == "COMPLETE"


def _persist_clean_eval_block(
    root: Path,
    *,
    run_id: str,
    sequence: int,
    case_id: str,
    method: str,
    attempt_no: int,
    safe_block: dict[str, Any],
    request_hash: str | None,
    response_evidence: dict[str, Any] | None,
) -> Path:
    payload = {
        "schema_version": "linkloom-clean-eval-block/v1",
        "run_id": run_id,
        "sequence": sequence,
        "case_id": case_id,
        "method": method,
        "phase": safe_block["phase"],
        "reason_code": safe_block["reason_code"],
        "outcome_domain": safe_block["outcome_domain"],
        "guard_name": safe_block["guard_name"],
        "provider_response_accepted": safe_block["provider_response_accepted"],
        "throw_site": safe_block["throw_site"],
        "exception_type": safe_block["exception_type"],
        "bounded_stack": safe_block["bounded_stack"],
        "nested_cause_type": safe_block["nested_cause_type"],
        "request_sha256": request_hash,
        "response_sha256": response_evidence.get("response_sha256") if response_evidence else None,
        "usage_present": bool(
            response_evidence and response_evidence.get("usage_present") is True
        ),
        "provider_request_id": response_evidence.get("provider_request_id") if response_evidence else None,
        "attempt_no": attempt_no,
        "timestamp_utc": datetime.now(UTC).isoformat(),
    }
    path = root / "clean_eval_blocks" / f"{sequence:03d}_attempt-{attempt_no:02d}.json"
    _atomic_json_write_fsync(path, payload)
    _seal_file(path, identity=f"clean-eval-block:{run_id}:{sequence}:{attempt_no}")
    return path


def _provider_completion_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    attempt_completions = [
        event for event in events
        if event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    ]
    accepted_attempts = {
        (event.get("sequence"), event.get("attempt_no"))
        for event in events
        if event.get("event") in {"RESPONSE_RECEIVED", "PROVIDER_RESPONSE_ACCEPTED"}
        and event.get("provider_response_accepted") is True
    }
    provider_requests = [
        event for event in attempt_completions
        if event.get("provider_request_sent") is True
        or (event.get("sequence"), event.get("attempt_no")) in accepted_attempts
    ]
    accepted_attempts.update(
        (event.get("sequence"), event.get("attempt_no"))
        for event in attempt_completions
        if event.get("provider_response_accepted") is True
        or (
            isinstance(event.get("safe_error"), dict)
            and event["safe_error"].get("provider_response_accepted") is True
        )
    )
    provider_errors = sum(
        event.get("provider_outcome") == "PROVIDER_ERROR"
        and event.get("provider_response_accepted") is not True
        for event in provider_requests
    )
    local_blocks = sum(
        event.get("provider_outcome") == "LOCAL_HARNESS_BLOCK"
        for event in provider_requests
    )
    request_attempt_count = len(
        {
            (event.get("sequence"), event.get("attempt_no"))
            for event in provider_requests
        }
        | accepted_attempts
    )
    return {
        "generation_attempts": request_attempt_count,
        "provider_completed_requests": len(accepted_attempts),
        "accepted_response_attempts": len(accepted_attempts),
        "provider_error_attempts": provider_errors,
        "local_harness_block_attempts": local_blocks,
        "completion_rate": len(accepted_attempts) / request_attempt_count if request_attempt_count else None,
    }


def _read_accepted_response_evidence(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_attempts: set[tuple[str, int, int]] = set()
    if not path.is_file():
        return {"rows": [], "integrity_valid": False, "errors": ["EVIDENCE_FILE_MISSING"]}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {"rows": [], "integrity_valid": False, "errors": ["EVIDENCE_FILE_UNREADABLE"]}
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            errors.append(f"INVALID_JSON_LINE:{index}")
            continue
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("run_id"), str)
            or not row.get("run_id")
            or isinstance(row.get("sequence"), bool)
            or not isinstance(row.get("sequence"), int)
            or row.get("sequence") < 1
            or not isinstance(row.get("case_id"), str)
            or not row.get("case_id")
            or not isinstance(row.get("method"), str)
            or not row.get("method")
            or row.get("provider_response_accepted") is not True
            or not _is_sha256_hex(row.get("request_sha256"))
            or not _is_sha256_hex(row.get("response_sha256"))
            or isinstance(row.get("attempt_no"), bool)
            or not isinstance(row.get("attempt_no"), int)
            or row.get("attempt_no") < 1
            or not isinstance(row.get("logical_attempt_id"), str)
            or not row.get("logical_attempt_id")
            or len(row["logical_attempt_id"]) > 128
            or not isinstance(row.get("model"), str)
            or not row.get("model")
            or len(row["model"]) > 128
            or not isinstance(row.get("accepted_at_utc"), str)
            or not row.get("accepted_at_utc")
            or not isinstance(row.get("usage_present"), bool)
            or (row.get("usage_metadata") is not None and not isinstance(row.get("usage_metadata"), dict))
            or (
                row.get("provider_request_id") is not None
                and (
                    not isinstance(row.get("provider_request_id"), str)
                    or not 0 < len(row["provider_request_id"]) <= 128
                    or any(
                        not character.isascii() or not (character.isalnum() or character in "-_.:")
                        for character in row["provider_request_id"]
                    )
                )
            )
        ):
            errors.append(f"INVALID_ACCEPTED_RESPONSE_EVIDENCE:{index}")
            continue
        usage = row.get("usage_metadata")
        usage = usage if isinstance(usage, dict) else {}
        usage_values_valid = all(
            value is None
            or (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            )
            for value in usage.values()
        ) and set(usage) <= {"input_tokens", "output_tokens", "thinking_tokens"}
        complete_fields_present = all(
            isinstance(usage.get(name), int)
            and not isinstance(usage.get(name), bool)
            and usage.get(name) >= 0
            for name in ("input_tokens", "output_tokens")
        )
        status = row.get("usage_status")
        explicit_status_present = status is not None
        if status is None:
            status = "COMPLETE" if complete_fields_present else "INCOMPLETE"
        usage_present_matches = row["usage_present"] is any(
            value is not None for value in usage.values()
        )
        billing_uncertainty_present = "billing_uncertainty" in row
        billing_uncertainty_matches = (
            row.get("billing_uncertainty") is (status != "COMPLETE")
            if billing_uncertainty_present
            else not explicit_status_present
        )
        sources = row.get("usage_sources")
        provider_fields = row.get("provider_usage_fields")
        usage_invalid_fields = row.get("usage_invalid_fields", [])
        usage_access_failures = row.get("usage_access_failures", [])
        usage_missing_fields = row.get("usage_missing_fields")
        expected_missing_fields = sorted(
            field for field in ("input_tokens", "output_tokens", "thinking_tokens")
            if usage.get(field) is None
        )
        cost_value = row.get("provider_reported_cost_usd")
        if (
            not usage_values_valid
            or not usage_present_matches
            or status not in {"COMPLETE", "INCOMPLETE", "INVALID"}
            or (status == "COMPLETE" and not complete_fields_present)
            or (
                status == "INCOMPLETE"
                and complete_fields_present
                and not row.get("usage_invalid_fields")
            )
            or not billing_uncertainty_matches
            or (
                sources is not None
                and (
                    not isinstance(sources, dict)
                    or set(sources) != {"input_tokens", "output_tokens", "thinking_tokens"}
                    or any(
                        source is not None
                        and (
                            not isinstance(source, str)
                            or source not in {alias for aliases in _RESPONSE_USAGE_ALIASES.values() for alias in aliases}
                        )
                        for source in sources.values()
                    )
                )
            )
            or (
                provider_fields is not None
                and (
                    not isinstance(provider_fields, dict)
                    or any(
                        key not in {alias for aliases in _RESPONSE_USAGE_ALIASES.values() for alias in aliases}
                        or isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                        for key, value in provider_fields.items()
                    )
                )
            )
            or not isinstance(usage_invalid_fields, list)
            or any(field not in _RESPONSE_USAGE_ALIASES for field in usage_invalid_fields)
            or not isinstance(usage_access_failures, list)
            or any(
                not isinstance(field, str)
                or field not in {"usage_metadata", "usage", "usage.cost_usd"}
                and not (
                    field.startswith("usage.")
                    and field.removeprefix("usage.")
                    in {alias for aliases in _RESPONSE_USAGE_ALIASES.values() for alias in aliases}
                )
                for field in usage_access_failures
            )
            or (
                usage_missing_fields is not None
                and (
                    not isinstance(usage_missing_fields, list)
                    or usage_missing_fields != expected_missing_fields
                )
            )
            or (status == "INVALID" and not usage_invalid_fields)
            or (status != "INVALID" and usage_invalid_fields)
            or (
                "provider_usage_object_present" in row
                and not isinstance(row.get("provider_usage_object_present"), bool)
            )
            or (
                "billing_uncertainty" in row
                and not isinstance(row.get("billing_uncertainty"), bool)
            )
            or (
                cost_value is not None
                and (
                    isinstance(cost_value, bool)
                    or not isinstance(cost_value, (int, float))
                    or not math.isfinite(float(cost_value))
                    or cost_value < 0
                )
            )
            or (
                row.get("provider_reported_cost_source") is not None
                and row.get("provider_reported_cost_source") != "cost_usd"
            )
        ):
            errors.append(f"INVALID_ACCEPTED_RESPONSE_EVIDENCE:{index}")
            continue
        attempt_key = (row["run_id"], row["sequence"], row["attempt_no"])
        if attempt_key in seen_attempts:
            errors.append(f"DUPLICATE_ACCEPTED_RESPONSE_EVIDENCE:{index}")
            continue
        seen_attempts.add(attempt_key)
        rows.append(row)
    return {"rows": rows, "integrity_valid": not errors, "errors": errors}


def _accepted_response_evidence_is_durable(
    path: Path,
    evidence: Mapping[str, Any] | None,
    *,
    run_id: str,
    sequence: int,
    attempt_no: int,
    request_hash: str,
) -> bool:
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("provider_response_accepted") is not True
        or evidence.get("run_id") != run_id
        or evidence.get("sequence") != sequence
        or evidence.get("attempt_no") != attempt_no
        or evidence.get("request_sha256") != request_hash
        or not _is_sha256_hex(evidence.get("response_sha256"))
    ):
        return False
    summary = _read_accepted_response_evidence(path)
    return summary["integrity_valid"] and any(
        row.get("run_id") == run_id
        and row.get("sequence") == sequence
        and row.get("attempt_no") == attempt_no
        and row.get("request_sha256") == request_hash
        and row.get("response_sha256") == evidence.get("response_sha256")
        for row in summary["rows"]
    )


def _evidence_from_response_receipt(receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    response_hash = receipt.get("response_sha256")
    usage = receipt.get("usage_metadata")
    if (
        not _is_sha256_hex(response_hash)
        or not isinstance(usage, dict)
        or not isinstance(receipt.get("run_id"), str)
        or not isinstance(receipt.get("sequence"), int)
        or not isinstance(receipt.get("qa_id"), str)
        or not isinstance(receipt.get("method"), str)
        or not isinstance(receipt.get("attempt_no"), int)
        or not isinstance(receipt.get("attempt_id"), str)
        or not isinstance(receipt.get("model"), str)
        or not isinstance(receipt.get("request_sha256"), str)
        or not isinstance(receipt.get("accepted_at_utc"), str)
    ):
        return None
    return {
        "run_id": receipt["run_id"],
        "sequence": receipt["sequence"],
        "case_id": receipt["qa_id"],
        "method": receipt["method"],
        "attempt_no": receipt["attempt_no"],
        "logical_attempt_id": receipt["attempt_id"],
        "provider_response_accepted": True,
        "response_sha256": response_hash,
        "model": receipt["model"],
        "usage_present": receipt.get("usage_present"),
        "usage_metadata": usage,
        "usage_status": receipt.get("usage_status"),
        "usage_missing_fields": receipt.get("usage_missing_fields"),
        "usage_sources": receipt.get("usage_sources"),
        "provider_usage_object_present": receipt.get("provider_usage_object_present"),
        "provider_usage_fields": receipt.get("provider_usage_fields"),
        "usage_invalid_fields": receipt.get("usage_invalid_fields", []),
        "usage_access_failures": receipt.get("usage_access_failures", []),
        "billing_uncertainty": receipt.get("billing_uncertainty"),
        "billing_basis": receipt.get("billing_basis"),
        "provider_reported_cost_usd": receipt.get("provider_reported_cost_usd"),
        "provider_reported_cost_source": receipt.get("provider_reported_cost_source"),
        "request_sha256": receipt["request_sha256"],
        "provider_request_id": receipt.get("provider_request_id"),
        "accepted_at_utc": receipt["accepted_at_utc"],
    }


def _recover_response_receipt_stages(
    journal_path: Path,
    evidence_path: Path,
    journal_events: list[dict[str, Any]],
    evidence_summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], int, list[str]]:
    """Recover only data derivable from a durable receipt, never a Provider call."""

    recovered_evidence = 0
    errors: list[str] = []
    evidence_by_attempt = {
        (row.get("sequence"), row.get("attempt_no")): row
        for row in evidence_summary.get("rows", [])
        if isinstance(row, dict)
    }
    for receipt in list(journal_events):
        if receipt.get("event") != "RESPONSE_RECEIVED":
            continue
        key = (receipt.get("sequence"), receipt.get("attempt_no"))
        evidence = evidence_by_attempt.get(key)
        if evidence is None:
            evidence = _evidence_from_response_receipt(receipt)
            if evidence is None:
                errors.append("RESPONSE_RECEIPT_NOT_RECOVERABLE")
                continue
            try:
                _append_jsonl_fsync(evidence_path, evidence)
            except OSError:
                errors.append("RESPONSE_EVIDENCE_RECOVERY_WRITE_FAILED")
                continue
            evidence_by_attempt[key] = evidence
            recovered_evidence += 1

        if (
            evidence.get("run_id") != receipt.get("run_id")
            or evidence.get("sequence") != receipt.get("sequence")
            or evidence.get("attempt_no") != receipt.get("attempt_no")
            or evidence.get("logical_attempt_id") != receipt.get("attempt_id")
            or evidence.get("request_sha256") != receipt.get("request_sha256")
            or evidence.get("response_sha256") != receipt.get("response_sha256")
            or evidence.get("usage_present") != receipt.get("usage_present")
            or evidence.get("usage_status") != receipt.get("usage_status")
            or evidence.get("usage_metadata") != receipt.get("usage_metadata")
            or evidence.get("usage_missing_fields") != receipt.get("usage_missing_fields")
            or evidence.get("usage_sources") != receipt.get("usage_sources")
            or evidence.get("provider_usage_fields")
            != receipt.get("provider_usage_fields")
            or evidence.get("usage_invalid_fields", [])
            != receipt.get("usage_invalid_fields", [])
            or evidence.get("usage_access_failures", [])
            != receipt.get("usage_access_failures", [])
            or evidence.get("billing_uncertainty") != receipt.get("billing_uncertainty")
            or evidence.get("billing_basis") != receipt.get("billing_basis")
        ):
            errors.append("RESPONSE_RECEIPT_EVIDENCE_MISMATCH")
            continue

        matching = [
            event for event in journal_events
            if (event.get("sequence"), event.get("attempt_no")) == key
        ]
        event_kinds = {event.get("event") for event in matching}
        persisted_exists = "RESPONSE_EVIDENCE_PERSISTED" in event_kinds
        usage_validated_exists = "USAGE_VALIDATED" in event_kinds
        receipt_index = journal_events.index(receipt)
        last_matching_index = max(
            index for index, event in enumerate(journal_events)
            if (event.get("sequence"), event.get("attempt_no")) == key
        )
        if not persisted_exists:
            if last_matching_index != receipt_index or journal_events[-1] is not receipt:
                errors.append("RESPONSE_EVIDENCE_PERSISTENCE_EVENT_MISSING")
                continue
            persisted = {
                **receipt,
                "event": "RESPONSE_EVIDENCE_PERSISTED",
                "evidence_sha256": canonical_sha256(evidence),
            }
            _append_attempt_journal(journal_path, persisted)
            journal_events.append(persisted)
            event_kinds.add("RESPONSE_EVIDENCE_PERSISTED")
        if not usage_validated_exists:
            if journal_events[-1].get("event") != "RESPONSE_EVIDENCE_PERSISTED":
                errors.append("RESPONSE_USAGE_VALIDATION_EVENT_MISSING")
                continue
            validated = {
                **receipt,
                "event": "USAGE_VALIDATED",
            }
            _append_attempt_journal(journal_path, validated)
            journal_events.append(validated)

    evidence_summary = _read_accepted_response_evidence(evidence_path)
    return journal_events, evidence_summary, recovered_evidence, errors


def _reconstruct_clean_eval_blocks(root: Path) -> dict[str, Any]:
    allowed_fields = frozenset(
        {
            "schema_version", "run_id", "sequence", "case_id", "method", "phase",
            "reason_code", "outcome_domain", "guard_name", "provider_response_accepted",
            "throw_site", "exception_type", "bounded_stack", "nested_cause_type",
            "request_sha256", "response_sha256", "usage_present", "provider_request_id",
            "attempt_no", "timestamp_utc",
        }
    )
    block_root = root / "clean_eval_blocks"
    files = (
        sorted(path for path in block_root.glob("*.json") if not path.name.endswith(".seal.json"))
        if block_root.is_dir()
        else []
    )
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in files:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            errors.append(f"UNREADABLE:{path.name}")
            continue
        if not isinstance(row, dict) or not _verify_seal(
            path,
            identity=f"clean-eval-block:{row.get('run_id')}:{row.get('sequence')}:{row.get('attempt_no')}",
        ):
            errors.append(f"UNSEALED:{path.name}")
            continue
        if (
            set(row) - allowed_fields
            or row.get("schema_version") != "linkloom-clean-eval-block/v1"
            or not isinstance(row.get("run_id"), str)
            or isinstance(row.get("sequence"), bool)
            or not isinstance(row.get("sequence"), int)
            or isinstance(row.get("attempt_no"), bool)
            or not isinstance(row.get("attempt_no"), int)
            or not _is_safe_diagnostic_label(row.get("case_id"))
            or not _is_safe_diagnostic_label(row.get("method"), allow_spaces=True)
            or not isinstance(row.get("phase"), str)
            or row["phase"] != row["phase"].upper()
            or not row["phase"].isascii()
            or not all(character.isalnum() or character == "_" for character in row["phase"])
            or not isinstance(row.get("reason_code"), str)
            or not (
                row["reason_code"].startswith("CLEAN_EVAL_BLOCK_")
                or (
                    row.get("outcome_domain") == "INTEGRITY"
                    and row["reason_code"].startswith("CLEAN_EVAL_INTEGRITY_")
                )
            )
            or row["reason_code"] != row["reason_code"].upper()
            or not row["reason_code"].isascii()
            or not all(character.isalnum() or character == "_" for character in row["reason_code"])
            or not isinstance(row.get("outcome_domain"), str)
            or row["outcome_domain"] not in {"PROVIDER", "LOCAL_HARNESS", "INTEGRITY", "PROTOCOL", "SCORING"}
            or not _is_safe_diagnostic_label(row.get("guard_name"))
            or not isinstance(row.get("provider_response_accepted"), bool)
            or not _is_safe_diagnostic_label(row.get("exception_type"))
            or not isinstance(row.get("bounded_stack"), list)
            or (
                row.get("nested_cause_type") is not None
                and not _is_safe_diagnostic_label(row.get("nested_cause_type"))
            )
            or (row.get("request_sha256") is not None and not _is_sha256_hex(row.get("request_sha256")))
            or (row.get("response_sha256") is not None and not _is_sha256_hex(row.get("response_sha256")))
            or not isinstance(row.get("usage_present"), bool)
            or not isinstance(row.get("timestamp_utc"), str)
        ):
            errors.append(f"SCHEMA_INVALID:{path.name}")
            continue
        if any(
            not isinstance(frame, dict)
            or set(frame) - {"module", "function", "line"}
            or not _is_safe_diagnostic_label(frame.get("module"))
            or not _is_safe_diagnostic_label(frame.get("function"))
            or isinstance(frame.get("line"), bool)
            or not isinstance(frame.get("line"), int)
            or frame["line"] <= 0
            for frame in row["bounded_stack"]
        ):
            errors.append(f"SCHEMA_INVALID:{path.name}")
            continue
        if row.get("provider_request_id") is not None and (
            not isinstance(row.get("provider_request_id"), str)
            or not 0 < len(row["provider_request_id"]) <= 128
            or any(
                not character.isascii() or not (character.isalnum() or character in "-_.:")
                for character in row["provider_request_id"]
            )
        ):
            errors.append(f"SCHEMA_INVALID:{path.name}")
            continue
        throw_site = row.get("throw_site")
        if (
            not isinstance(throw_site, dict)
            or set(throw_site) - {"module", "function", "line"}
            or not _is_safe_diagnostic_label(throw_site.get("module"))
            or not _is_safe_diagnostic_label(throw_site.get("function"))
            or (throw_site.get("line") is not None and (
                isinstance(throw_site.get("line"), bool)
                or not isinstance(throw_site.get("line"), int)
                or throw_site["line"] <= 0
            ))
        ):
            errors.append(f"SCHEMA_INVALID:{path.name}")
            continue
        rows.append({key: row.get(key) for key in allowed_fields})
    counts: dict[str, int] = {}
    for row in rows:
        code = str(row.get("reason_code", "CLEAN_EVAL_BLOCK_OTHER_UNCLASSIFIED"))
        counts[code] = counts.get(code, 0) + 1
    return {
        "block_count": len(rows),
        "reason_code_counts": counts,
        "blocks": rows,
        "integrity_errors": errors,
        "integrity_valid": not errors,
    }


def _diagnostic_guard_is_fatal(
    reason_code: str,
    *,
    response_evidence: dict[str, Any] | None = None,
    provider_response_accepted: bool = False,
    journal_integrity_valid: bool = False,
    cost_reservation_safe: bool = False,
) -> bool:
    if reason_code not in DIAGNOSTIC_CASE_LOCAL_REASON_CODES:
        return True
    if not provider_response_accepted or not isinstance(response_evidence, dict):
        return True
    request_hash = response_evidence.get("request_sha256")
    response_hash = response_evidence.get("response_sha256")
    usage = response_evidence.get("usage_metadata")
    if (
        response_evidence.get("provider_response_accepted") is not True
        or not _is_sha256_hex(request_hash)
        or not _is_sha256_hex(response_hash)
        or not isinstance(usage, dict)
        or not isinstance(response_evidence.get("logical_attempt_id"), str)
        or not isinstance(response_evidence.get("model"), str)
        or not response_evidence.get("model")
        or not journal_integrity_valid
    ):
        return True
    if (
        set(usage) - {"input_tokens", "output_tokens", "thinking_tokens"}
        or any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            )
            for value in usage.values()
        )
        or response_evidence.get("usage_present")
        is not any(value is not None for value in usage.values())
    ):
        return True
    if _evidence_usage_is_complete(response_evidence):
        return False
    if (
        reason_code in {
            "CLEAN_EVAL_BLOCK_HARNESS_PROVIDER_USAGE_UNAVAILABLE",
            "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE",
        }
        and response_evidence.get("billing_uncertainty", True) is True
        and _evidence_usage_status(response_evidence) == "INCOMPLETE"
        and cost_reservation_safe
    ):
        return False
    return True


def _formal_case_local_block_is_fatal(
    reason_code: str,
    *,
    response_evidence: dict[str, Any] | None,
    provider_response_accepted: bool,
    journal_integrity_valid: bool,
    cost_reservation_safe: bool,
) -> bool:
    evidence_valid = (
        provider_response_accepted is False
        or (
            isinstance(response_evidence, Mapping)
            and response_evidence.get("provider_response_accepted") is True
            and _is_sha256_hex(response_evidence.get("request_sha256"))
            and _is_sha256_hex(response_evidence.get("response_sha256"))
            and journal_integrity_valid
        )
    )
    decision = _evaluation_guard_decision(
        _guard_criticality_level(reason_code),
        phase="POST_RESPONSE" if provider_response_accepted else "PROVIDER_ATTEMPT",
        provider_response_accepted=provider_response_accepted,
        response_valid=True,
        evidence_valid=evidence_valid,
        cost_upper_bound_valid=cost_reservation_safe,
        dataset_identity_valid=True,
        scoring_identity_valid=True,
    )
    return decision == "RUN_HARD_STOP"


def _finite_nonnegative_usd(value: Any, *, allow_zero: bool = True) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and (value >= 0 if allow_zero else value > 0)
    )


def _diagnostic_cost_reservation_is_safe(
    attempt_start: Mapping[str, Any] | None,
    plan_row: Mapping[str, Any] | None,
    *,
    run_hard_cost_cap: Any,
    global_hard_cost_cap: Any,
) -> bool:
    if not isinstance(attempt_start, Mapping) or not isinstance(plan_row, Mapping):
        return False
    reserved = attempt_start.get("attempt_cost_upper_bound_reserved_usd")
    planned = plan_row.get("one_attempt_cost_upper_bound_usd")
    static_bound = plan_row.get("static_input_token_upper_bound")
    if (
        not _finite_nonnegative_usd(reserved, allow_zero=False)
        or not _finite_nonnegative_usd(planned, allow_zero=False)
        or not math.isclose(float(reserved), float(planned), rel_tol=0.0, abs_tol=1e-9)
        or isinstance(static_bound, bool)
        or not isinstance(static_bound, int)
        or static_bound < 0
        or attempt_start.get("static_input_token_upper_bound") != static_bound
        or not _finite_nonnegative_usd(run_hard_cost_cap, allow_zero=False)
        or not _finite_nonnegative_usd(global_hard_cost_cap, allow_zero=False)
    ):
        return False
    projected_cell = attempt_start.get("projected_cell_cost_upper_bound_usd")
    projected_global = attempt_start.get("projected_global_cost_upper_bound_usd")
    return (
        _finite_nonnegative_usd(projected_cell, allow_zero=False)
        and _finite_nonnegative_usd(projected_global, allow_zero=False)
        and float(projected_cell) <= float(run_hard_cost_cap)
        and float(projected_global) <= float(global_hard_cost_cap)
    )


def _validate_case_against_freeze(cell_key: str, cell: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    manifest, frozen_plan = verify_frozen_artifacts(cell)
    case = load_factconsolidation_case(
        DATASET_PATH,
        source=cell.source,
        question_limit=100,
    )
    dataset = manifest["dataset"]
    selection = manifest["selection"]
    if (
        case.source != cell.source
        or len(case.questions) != 100
        or [question.question_id for question in case.questions] != selection["qa_ids_in_official_order"]
        or hashlib.sha256(case.context.encode("utf-8")).hexdigest() != dataset.get("context_sha256")
        or len(case.context) != dataset.get("context_characters")
        or len(case.facts) != dataset.get("numbered_fact_count")
        or [fact.ordinal for fact in case.facts] != list(range(dataset.get("numbered_fact_count", 0)))
    ):
        raise CleanEvalIntegrityError(f"{cell.display_name} frozen source inputs drifted")
    question_hashes = selection.get("question_sha256_by_id")
    actual_hashes = {
        question.question_id: hashlib.sha256(question.question.encode("utf-8")).hexdigest()
        for question in case.questions
    }
    if question_hashes != actual_hashes:
        raise CleanEvalIntegrityError(f"{cell.display_name} question text hashes differ from freeze")
    if case.input_projection_columns != GOLD_FREE_INPUT_COLUMNS or hasattr(case, "answers"):
        raise CleanEvalIntegrityError("answer data leaked into the adapter result")
    if frozen_plan["method_case_count"] != len(case.questions) * len(METHODS):
        raise CleanEvalIntegrityError("frozen method-case count does not match loaded questions")
    return case, manifest, frozen_plan


def _prepare_cell(
    cell_key: str,
    *,
    question_ids: list[str] | None = None,
    subset_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cell = CELLS[cell_key]
    case, manifest, frozen_plan = _validate_case_against_freeze(cell_key, cell)
    source_question_ids = [question.question_id for question in case.questions]
    if question_ids is not None:
        if (
            not question_ids
            or len(question_ids) != len(set(question_ids))
            or any(qa_id not in source_question_ids for qa_id in question_ids)
            or [qa_id for qa_id in source_question_ids if qa_id in set(question_ids)] != question_ids
            or not isinstance(subset_manifest, Mapping)
            or subset_manifest.get("selected_qa_ids_in_official_order") != question_ids
        ):
            raise CleanEvalBlocked("E2_REVISED_SUBSET_SELECTION_INVALID")
        selected_by_id = {question.question_id: question for question in case.questions}
        case = replace(case, questions=tuple(selected_by_id[qa_id] for qa_id in question_ids))
        selected_index = {qa_id: index for index, qa_id in enumerate(question_ids)}
        source_rows = [
            row for row in frozen_plan["execution_plan"]
            if row.get("qa_id") in selected_index
        ]
        runtime_source_rows = [
            {
                **row,
                "source_sequence": int(row["sequence"]),
                "source_qa_index": int(row["qa_index"]),
                "sequence": index + 1,
                "qa_index": selected_index[str(row["qa_id"])],
            }
            for index, row in enumerate(source_rows)
        ]
        if len(runtime_source_rows) != len(question_ids) * len(METHODS):
            raise CleanEvalBlocked("E2_REVISED_SUBSET_PLAN_INVALID")
        runtime_source_plan = {**frozen_plan, "execution_plan": runtime_source_rows}
        active_subset_sha256 = str(subset_manifest.get("subset_sha256"))
    else:
        runtime_source_plan = frozen_plan
        active_subset_sha256 = manifest["hashes"]["subset_sha256"]
    flat_index = build_flat_index(case)
    memory = build_temporal_memory(case)
    prepared_by_pair: dict[tuple[str, str], Any] = {}
    try:
        question_by_id = {question.question_id: question for question in case.questions}
        for question in case.questions:
            for method in G3_METHODS:
                prepared_by_pair[(question.question_id, method)] = prepare_method_context(
                    case,
                    flat_index,
                    question,
                    method=method,
                    memory=memory if method == METHOD_TEMPORAL_MEMORY else None,
                )

        if len(flat_index.workspace_ids()) != 1 or flat_index.workspace_ids() != {case.workspace_id}:
            raise CleanEvalIntegrityError("retrieval workspace crossed a frozen dataset cell")
        if memory.workspace_id != case.workspace_id:
            raise CleanEvalIntegrityError("Temporal Memory workspace crossed a frozen dataset cell")

        plan_rows: list[dict[str, Any]] = []
        for frozen_row in runtime_source_plan["execution_plan"]:
            qa_id = str(frozen_row["qa_id"])
            method = str(frozen_row["method"])
            question = question_by_id[qa_id]
            prepared = prepared_by_pair[(qa_id, method)]
            request = build_generation_request(prepared.context_bundle.rendered_text)
            if request.get("model") != MODEL or request.get("config", {}).get("max_output_tokens") != OUTPUT_TOKEN_CAP:
                raise CleanEvalIntegrityError("Gemini request configuration drifted from frozen profile")
            from benchmarks.memoryagentbench.g32_heldout import count_tokens_request_upper_bound
            from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

            generation_hash, count_tokens_hash, request_bytes_bound = count_tokens_request_upper_bound(request)
            static_token_bound = gemini._static_conservative_input_token_upper_bound(request)
            if static_token_bound > INPUT_TOKEN_CAP:
                raise CleanEvalIntegrityError(f"static per-request input bound exceeds {INPUT_TOKEN_CAP}")
            if len(prepared.context_bundle.rendered_text) > MAX_CONTEXT_CHARS:
                raise CleanEvalIntegrityError("prepared request context exceeds character bound")
            if prepared.context_bundle.estimated_tokens > MAX_INPUT_CONTEXT_TOKENS:
                raise CleanEvalIntegrityError("prepared request context exceeds estimated token bound")
            memory_observation = prepared.memory_observation or {}
            plan_rows.append(
                {
                    "sequence": int(frozen_row["sequence"]),
                    "qa_index": int(frozen_row["qa_index"]),
                    **(
                        {
                            "source_sequence": int(frozen_row["source_sequence"]),
                            "source_qa_index": int(frozen_row["source_qa_index"]),
                        }
                        if "source_sequence" in frozen_row
                        else {}
                    ),
                    "qa_id": qa_id,
                    "method": method,
                    "question_sha256": hashlib.sha256(question.question.encode("utf-8")).hexdigest(),
                    "context_sha256": hashlib.sha256(prepared.context_bundle.rendered_text.encode("utf-8")).hexdigest(),
                    "generation_request_sha256": generation_hash,
                    "count_tokens_request_sha256": count_tokens_hash,
                    "request_json_bytes_upper_bound": request_bytes_bound,
                    "static_input_token_upper_bound": static_token_bound,
                    "one_attempt_cost_upper_bound_usd": request_cost_upper_bound_usd(static_token_bound),
                    "context_tokens_estimated": prepared.context_bundle.estimated_tokens,
                    "retrieval_evidence_ids": list(prepared.retrieval_evidence_ids),
                    "context_evidence_ids_selected": [
                        item.item_id
                        for item in prepared.context_bundle.selected
                        if item.source_type.value == "evidence"
                    ],
                    "memory_lookup_status": "HIT" if memory_observation else "MISS" if method == METHOD_TEMPORAL_MEMORY else "NOT_APPLICABLE",
                    "memory_source_evidence_ids": list(memory_observation.get("source_evidence_refs", [])),
                    "workspace_id": case.workspace_id,
                }
            )

        base_cost = sum(row["one_attempt_cost_upper_bound_usd"] for row in plan_rows)
        all_attempts_cost = base_cost * MAX_ATTEMPTS_PER_LOGICAL_GENERATION
        if (
            not _cost_is_below_cap(base_cost, cell.hard_cost_cap_usd)
            or not _cost_is_below_cap(base_cost, GLOBAL_COST_CAP_USD)
            or (question_ids is not None and not _cost_is_below_cap(all_attempts_cost, cell.hard_cost_cap_usd))
        ):
            raise CleanEvalIntegrityError(f"{cell.display_name} one-attempt cost bound reaches its cap")
        checks = {
            "exact_frozen_qa_order": [question.question_id for question in case.questions]
            == (question_ids if question_ids is not None else manifest["selection"]["qa_ids_in_official_order"]),
            "exact_interleaved_method_plan": len(plan_rows) == len(case.questions) * len(METHODS)
            and all(
                row["qa_id"] == runtime_source_plan["execution_plan"][index]["qa_id"]
                and row["method"] == runtime_source_plan["execution_plan"][index]["method"]
                for index, row in enumerate(plan_rows)
            ),
            "two_isolated_benchmark_views": flat_index.workspace_ids() == {case.workspace_id}
            and memory.workspace_id == case.workspace_id,
            "flat_path_has_source_evidence": all(
                row["context_evidence_ids_selected"]
                for row in plan_rows
                if row["method"] == METHOD_FLAT_RETRIEVAL
            ),
            "temporal_hits_have_provenance": all(
                row["memory_lookup_status"] != "HIT"
                or (len(row["memory_source_evidence_ids"]) == 1
                    and row["context_evidence_ids_selected"] == row["memory_source_evidence_ids"])
                for row in plan_rows
                if row["method"] == METHOD_TEMPORAL_MEMORY
            ),
            "temporal_misses_have_no_unsupported_evidence": all(
                row["memory_lookup_status"] != "MISS" or not row["context_evidence_ids_selected"]
                for row in plan_rows
                if row["method"] == METHOD_TEMPORAL_MEMORY
            ),
            "all_contexts_within_budget": all(
                row["context_tokens_estimated"] <= MAX_INPUT_CONTEXT_TOKENS
                for row in plan_rows
            ),
            "no_gold_values_read": (
                case.input_projection_columns == GOLD_FREE_INPUT_COLUMNS
                and not hasattr(case, "answers")
            ),
            "no_provider_requests_made": True,
        }
        if not all(checks.values()):
            raise CleanEvalIntegrityError(f"{cell.display_name} offline qualification failed")
        fingerprint = implementation_fingerprints()
        runtime_plan_sha256 = canonical_sha256(plan_rows)
        return {
            "schema_version": "linkloom-public-memory-clean-preflight/v1",
            "status": "PASS",
            "classification": "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "cell": cell.display_name,
            "source": cell.source,
            "dataset_revision": manifest["dataset"]["revision"],
            "dataset_sha256": DATASET_SHA256,
            "subset_sha256": active_subset_sha256,
            "source_subset_sha256": manifest["hashes"]["subset_sha256"],
            "freeze_manifest_sha256": manifest["freeze_manifest_sha256"],
            "frozen_execution_plan_sha256": manifest["hashes"]["execution_plan_sha256"],
            "runtime_execution_plan_sha256": runtime_plan_sha256,
            "case_id": case.case_id,
            "workspace_id": case.workspace_id,
            "question_count": len(case.questions),
            "fact_count": len(case.facts),
            "parsed_fact_count": sum(bool(fact.subject_key and fact.value) for fact in case.facts),
            "unmapped_fact_count": sum(not fact.subject_key or not fact.value for fact in case.facts),
            "prepared_method_case_count": len(plan_rows),
            "temporal_lookup_hit_count": sum(
                row["memory_lookup_status"] == "HIT" for row in plan_rows
            ),
            "temporal_lookup_miss_count": sum(
                row["memory_lookup_status"] == "MISS" for row in plan_rows
            ),
            "qualification_checks": checks,
            "hard_cost_cap_usd": cell.hard_cost_cap_usd,
            "global_cost_cap_usd": GLOBAL_COST_CAP_USD,
            "one_attempt_base_cost_upper_bound_usd": base_cost,
            "all_calls_at_three_attempts_cost_upper_bound_usd": all_attempts_cost,
            "cost_policy": "reserve remaining base executions; transient retries are scheduled only under the dynamic hard cap",
            "pricing": {
                "provider": "Gemini Developer API",
                "model": MODEL,
                "input_usd_per_million": INPUT_USD_PER_MILLION,
                "output_usd_per_million": OUTPUT_USD_PER_MILLION,
                "snapshot_date": "2026-09-26",
                "official_source": PRICING_SOURCE_URL,
                "count_tokens_costed_as_input_conservatively": True,
            },
            "model": MODEL,
            "sdk_version": SDK_VERSION,
            "output_token_cap": OUTPUT_TOKEN_CAP,
            "input_token_cap": INPUT_TOKEN_CAP,
            "maximum_attempts_per_logical_generation": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            "provider_calls": {"generation": 0, "count_tokens": 0},
            "gold_values_read": False,
            "implementation_fingerprints": fingerprint,
            "execution_plan": plan_rows,
        }
    finally:
        memory.store.close()


def _resolve_eval_artifact_dir(path: Path | None) -> Path:
    root = CLEAN_EVAL_ROOT.resolve()
    candidate = (path if path is not None else CLEAN_EVAL_ROOT).resolve()
    if candidate != root and root not in candidate.parents:
        raise CleanEvalBlocked("EVAL_ARTIFACT_DIR_OUTSIDE_CLEAN_EVAL_ROOT")
    return candidate


def prepare_all_cells(
    cell_keys: list[str],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    fingerprint_hashes: set[str] = set()
    output_root = _resolve_eval_artifact_dir(output_dir)
    for key in cell_keys:
        result = _prepare_cell(key)
        preflight_path = output_root / f"{key}_preflight.json"
        _json_write(preflight_path, result)
        _seal_file(preflight_path, identity=f"offline-preflight:{key}")
        fingerprint_hashes.add(result["implementation_fingerprints"]["files_sha256"])
        results[key] = {
            "status": result["status"],
            "prepared_method_case_count": result["prepared_method_case_count"],
            "temporal_lookup_hit_count": result["temporal_lookup_hit_count"],
            "temporal_lookup_miss_count": result["temporal_lookup_miss_count"],
            "one_attempt_base_cost_upper_bound_usd": result["one_attempt_base_cost_upper_bound_usd"],
            "all_calls_at_three_attempts_cost_upper_bound_usd": result["all_calls_at_three_attempts_cost_upper_bound_usd"],
            "output": str(preflight_path),
        }
    if len(fingerprint_hashes) > 1:
        raise CleanEvalIntegrityError("implementation fingerprints changed while preparing the two cells")
    return results


def _prepare_e2_preflight() -> dict[str, Any]:
    subset_manifest, plan = _ensure_e2_pre_registration()
    loaded_plan = _load_evaluation_plan(E2_MH_ONLY_EVALUATION_PLAN_ID)
    if _evaluation_plan_sha256(loaded_plan) != _evaluation_plan_sha256(plan):
        raise CleanEvalBlocked("E2_EVALUATION_PLAN_REGISTRATION_MISMATCH")
    preflight = _prepare_cell(
        "mh_6k",
        question_ids=list(subset_manifest["selected_qa_ids_in_official_order"]),
        subset_manifest=subset_manifest,
    )
    preflight.update(
        {
            "evaluation_plan_id": E2_MH_ONLY_EVALUATION_PLAN_ID,
            "evaluation_plan_sha256": _evaluation_plan_sha256(plan),
            "revision_classification": "REVISED_HELDOUT_AFTER_STOPPED_E1_1",
            "subset_manifest_sha256": hashlib.sha256(E2_SUBSET_MANIFEST_PATH.read_bytes()).hexdigest(),
            "score_protocol_sha256": hashlib.sha256(E2_SCORE_PROTOCOL_PATH.read_bytes()).hexdigest(),
            "gold_values_read": False,
            "provider_calls": {"generation": 0, "count_tokens": 0},
        }
    )
    path = E2_QUALIFICATION_DIR / "mh_6k_preflight.json"
    if path.exists():
        if not _verify_seal(path, identity="offline-preflight:mh_6k"):
            raise CleanEvalBlocked("E2_PREFLIGHT_ALREADY_EXISTS_INVALID")
        existing = json.loads(path.read_text(encoding="utf-8"))
        fields = (
            "implementation_fingerprints",
            "runtime_execution_plan_sha256",
            "subset_sha256",
            "prepared_method_case_count",
            "question_count",
            "evaluation_plan_sha256",
        )
        if any(existing.get(field) != preflight.get(field) for field in fields):
            raise CleanEvalBlocked("E2_PREFLIGHT_ALREADY_EXISTS_DIFFERENT")
        preflight = existing
    else:
        E2_QUALIFICATION_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_json_write_fsync(path, preflight)
        _seal_file(path, identity="offline-preflight:mh_6k")
    return {
        "status": preflight["status"],
        "question_count": preflight["question_count"],
        "method_case_count": preflight["prepared_method_case_count"],
        "subset_sha256": preflight["subset_sha256"],
        "source_subset_sha256": preflight["source_subset_sha256"],
        "one_attempt_base_cost_upper_bound_usd": preflight["one_attempt_base_cost_upper_bound_usd"],
        "all_calls_at_three_attempts_cost_upper_bound_usd": preflight["all_calls_at_three_attempts_cost_upper_bound_usd"],
        "gold_values_read": preflight["gold_values_read"],
        "provider_calls": preflight["provider_calls"],
        "fingerprint_sha256": preflight["implementation_fingerprints"]["files_sha256"],
        "output": str(path),
    }


def _read_preflight(
    cell_key: str,
    *,
    preflight_dir: Path | None = None,
    expected_method_case_count: int = 200,
) -> dict[str, Any]:
    path = _resolve_eval_artifact_dir(preflight_dir) / f"{cell_key}_preflight.json"
    if not _verify_seal(path, identity=f"offline-preflight:{cell_key}"):
        raise CleanEvalBlocked(f"{cell_key.upper()}_PREFLIGHT_SEAL_INVALID")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise CleanEvalBlocked(f"{cell_key.upper()}_PREFLIGHT_UNAVAILABLE") from None
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "PASS"
        or payload.get("classification") != "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER"
        or payload.get("provider_calls") != {"generation": 0, "count_tokens": 0}
        or payload.get("gold_values_read") is not False
        or payload.get("prepared_method_case_count") != expected_method_case_count
        or (
            payload.get("question_count") is not None
            and (
                isinstance(payload.get("question_count"), bool)
                or not isinstance(payload.get("question_count"), int)
                or payload.get("question_count") * len(METHODS) != expected_method_case_count
            )
        )
        or (
            "execution_plan" in payload
            and (
                not isinstance(payload.get("execution_plan"), list)
                or len(payload["execution_plan"]) != expected_method_case_count
            )
        )
    ):
        raise CleanEvalBlocked(f"{cell_key.upper()}_PREFLIGHT_INVALID")
    return payload


def _verify_ready(
    cell_key: str,
    *,
    preflight_dir: Path | None = None,
    evaluation_plan_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_fingerprints = implementation_fingerprints()
    resolved_preflight_dir = _resolve_eval_artifact_dir(preflight_dir)
    plan = _load_evaluation_plan(evaluation_plan_id) if evaluation_plan_id is not None else None
    if plan is not None and plan.get("plan_id") in {MH_ONLY_EVALUATION_PLAN_ID, E2_MH_ONLY_EVALUATION_PLAN_ID}:
        if cell_key != "mh_6k":
            raise CleanEvalBlocked("MH_ONLY_PLAN_REQUIRES_MH_6K")
        preflight_cells = (cell_key,)
    else:
        preflight_cells = tuple(CELLS)
    e2_plan = plan is not None and plan.get("plan_id") == E2_MH_ONLY_EVALUATION_PLAN_ID
    if e2_plan:
        if resolved_preflight_dir != E2_QUALIFICATION_DIR.resolve():
            raise CleanEvalBlocked("E2_QUALIFICATION_PATH_MISMATCH")
        subset_manifest = _read_e2_subset_manifest()
    else:
        subset_manifest = None
    preflights = {
        key: _read_preflight(
            key,
            preflight_dir=resolved_preflight_dir,
            expected_method_case_count=E2_PLANNED_METHOD_CASES if e2_plan else 200,
        )
        for key in preflight_cells
    }
    expected_hashes = {
        value.get("implementation_fingerprints", {}).get("files_sha256")
        for value in preflights.values()
    }
    if len(expected_hashes) != 1 or current_fingerprints["files_sha256"] not in expected_hashes:
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH")
    for key, preflight in preflights.items():
        if preflight.get("implementation_fingerprints") != current_fingerprints:
            raise CleanEvalBlocked(f"{key.upper()}_IMPLEMENTATION_FINGERPRINT_MISMATCH")

    recomputed = (
        _prepare_cell(
            cell_key,
            question_ids=list(subset_manifest["selected_qa_ids_in_official_order"]),
            subset_manifest=subset_manifest,
        )
        if e2_plan and subset_manifest is not None
        else _prepare_cell(cell_key)
    )
    frozen = preflights[cell_key]
    stable_fields = (
        "cell",
        "source",
        "dataset_revision",
        "dataset_sha256",
        "subset_sha256",
        "freeze_manifest_sha256",
        "frozen_execution_plan_sha256",
        "runtime_execution_plan_sha256",
        "case_id",
        "workspace_id",
        "question_count",
        "fact_count",
        "prepared_method_case_count",
        "one_attempt_base_cost_upper_bound_usd",
        "all_calls_at_three_attempts_cost_upper_bound_usd",
        "execution_plan",
    )
    if any(recomputed.get(field) != frozen.get(field) for field in stable_fields):
        raise CleanEvalBlocked(f"{cell_key.upper()}_RUNTIME_PREFLIGHT_MISMATCH")
    if e2_plan and (
        frozen.get("source_subset_sha256") != subset_manifest.get("source_subset_sha256")
        or frozen.get("subset_sha256") != subset_manifest.get("subset_sha256")
        or frozen.get("question_count") != E2_SUBSET_QA_COUNT
        or frozen.get("prepared_method_case_count") != E2_PLANNED_METHOD_CASES
    ):
        raise CleanEvalBlocked("E2_RUNTIME_SUBSET_MANIFEST_MISMATCH")
    return frozen, current_fingerprints


def _verify_diagnostic_ready(
    *,
    preflight_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_fingerprints = implementation_fingerprints()
    preflight = _read_preflight("sh_32k", preflight_dir=preflight_dir)
    if preflight.get("purpose") != "HARNESS_STABILITY" or preflight.get("clean_accuracy_claim") is not False:
        raise CleanEvalBlocked("DIAGNOSTIC_PREFLIGHT_PURPOSE_MISMATCH")
    if preflight.get("implementation_fingerprints") != current_fingerprints:
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH")
    recomputed = _prepare_cell("sh_32k")
    stable_fields = (
        "cell",
        "source",
        "dataset_revision",
        "dataset_sha256",
        "subset_sha256",
        "freeze_manifest_sha256",
        "frozen_execution_plan_sha256",
        "runtime_execution_plan_sha256",
        "case_id",
        "workspace_id",
        "question_count",
        "fact_count",
        "prepared_method_case_count",
        "one_attempt_base_cost_upper_bound_usd",
        "execution_plan",
    )
    if any(recomputed.get(field) != preflight.get(field) for field in stable_fields):
        raise CleanEvalBlocked("SH_32K_DIAGNOSTIC_PREFLIGHT_MISMATCH")
    return preflight, current_fingerprints


def _verify_diagnostic_qualification_gates() -> tuple[dict[str, Any], dict[str, Any]]:
    inventory_path = DIAGNOSTIC_DIR / "H1_guard_inventory.json"
    qualification_path = DIAGNOSTIC_DIR / "H2_stress_qualification.json"
    if not _verify_seal(inventory_path, identity="harness-diagnostic-h1-inventory"):
        raise CleanEvalBlocked("DIAGNOSTIC_H1_INVENTORY_UNSEALED")
    if not _verify_seal(qualification_path, identity="harness-diagnostic-h2-qualification"):
        raise CleanEvalBlocked("DIAGNOSTIC_H2_QUALIFICATION_UNSEALED")
    try:
        h1 = json.loads(inventory_path.read_text(encoding="utf-8"))
        h2 = json.loads(qualification_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("DIAGNOSTIC_QUALIFICATION_ARTIFACT_UNREADABLE") from None
    current = implementation_fingerprints()
    if (
        not isinstance(h1, dict)
        or h1.get("status") != "PASS"
        or h1.get("uninstrumented_throw_sites") != 0
        or h1.get("provider_calls") != 0
        or h1.get("gold_values_read") is not False
        or h1.get("implementation_fingerprint_sha256") != current["files_sha256"]
        or not isinstance(h2, dict)
        or h2.get("status") != "PASS"
        or h2.get("provider_calls") != {"generation": 0, "count_tokens": 0}
        or h2.get("gold_values_read") is not False
        or h2.get("mh_6k_live_or_preparation_run") is not False
        or h2.get("synthetic_lifecycle_qualification", {}).get("terminal_method_cases") != 200
        or h2.get("implementation_fingerprint_sha256") != current["files_sha256"]
    ):
        raise CleanEvalBlocked("DIAGNOSTIC_QUALIFICATION_GATE_FAILED")
    return h1, h2


def _prepare_diagnostic_stress_qualification() -> dict[str, Any]:
    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    preflight = _prepare_cell("sh_32k")
    preflight.update(
        {
            "purpose": "HARNESS_STABILITY",
            "dataset_use": "DIAGNOSTIC_STRESS_SET",
            "clean_accuracy_claim": False,
            "scoring": "DISABLED",
            "gold_values_read": False,
            "provider_calls": {"generation": 0, "count_tokens": 0},
        }
    )
    path = DIAGNOSTIC_DIR / "sh_32k_preflight.json"
    _json_write(path, preflight)
    _seal_file(path, identity="offline-preflight:sh_32k")
    return {
        "status": preflight["status"],
        "purpose": preflight["purpose"],
        "prepared_method_case_count": preflight["prepared_method_case_count"],
        "gold_values_read": preflight["gold_values_read"],
        "provider_calls": preflight["provider_calls"],
        "implementation_fingerprint_sha256": preflight["implementation_fingerprints"]["files_sha256"],
        "output": str(path),
    }


def _write_diagnostic_execution_baseline(
    *,
    preflight: dict[str, Any],
    fingerprints: dict[str, Any],
    route: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    path = DIAGNOSTIC_DIR / "DIAGNOSTIC_EXECUTION_BASELINE.json"
    payload = {
        "schema_version": "linkloom-harness-diagnostic-execution-baseline/v1",
        "purpose": "HARNESS_STABILITY",
        "dataset_classification": "DIAGNOSTIC_STRESS_SET_NOT_CLEAN_HELD_OUT",
        "accuracy_claim": False,
        "cell": "SH-32k",
        "planned_qa_count": 100,
        "planned_method_cases": 200,
        "method_order": [METHOD_FLAT_RETRIEVAL, METHOD_TEMPORAL_MEMORY],
        "alternating_execution": True,
        "scoring": "DISABLED",
        "gold_values_read": False,
        "provider_calls_during_qualification": {"generation": 0, "count_tokens": 0},
        "dataset_revision": preflight["dataset_revision"],
        "dataset_sha256": preflight["dataset_sha256"],
        "subset_sha256": preflight["subset_sha256"],
        "manifest_sha256": preflight["freeze_manifest_sha256"],
        "execution_plan_sha256": preflight["runtime_execution_plan_sha256"],
        "implementation_fingerprints": fingerprints,
        "route_preflight": route,
        "retry_policy": {
            "maximum_attempts_per_logical_generation": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            "transient_only": True,
            "sdk_automatic_retries": 0,
        },
        "artifact_policy": {
            "accepted_response_evidence_before_post_response_guards": True,
            "independent_clean_eval_block_artifacts": True,
            "journal_fsync": True,
        },
        "guard_reason_code_taxonomy": "CLEAN_EVAL_BLOCK_<CATEGORY>_<REASON>",
        "run_hard_cap_usd": DIAGNOSTIC_RUN_CAP_USD,
        "task_hard_cap_usd": DIAGNOSTIC_TASK_CAP_USD,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    if path.exists():
        if not _verify_seal(path, identity="diagnostic-execution-baseline"):
            raise CleanEvalBlocked("DIAGNOSTIC_BASELINE_EXISTS_UNSEALED")
        existing = json.loads(path.read_text(encoding="utf-8"))
        comparable_existing = {key: value for key, value in existing.items() if key != "created_at_utc"}
        comparable_payload = {key: value for key, value in payload.items() if key != "created_at_utc"}
        if comparable_existing != comparable_payload:
            raise CleanEvalBlocked("DIAGNOSTIC_BASELINE_FINGERPRINT_MISMATCH")
        payload = existing
    else:
        _atomic_json_write_fsync(path, payload)
        _seal_file(path, identity="diagnostic-execution-baseline")
    return path, payload


def _verify_v3_live_authorization(
    qualification_dir: Path,
    fingerprints: dict[str, Any],
) -> None:
    """Require the promoted V3 baseline and sealed offline gates before a live run."""
    baseline_path = qualification_dir / "AUTHORIZED_EXECUTION_BASELINE_V3.json"
    if not _verify_seal(baseline_path, identity="authorized-execution-baseline-v3"):
        raise CleanEvalBlocked("V3_BASELINE_MISSING_OR_UNSEALED")
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("V3_BASELINE_UNREADABLE") from None
    if (
        not isinstance(baseline, dict)
        or baseline.get("schema_version") != "linkloom-public-memory-authorized-execution-baseline/v3"
        or baseline.get("status") != "V3_BASELINE_APPROVED"
        or baseline.get("fingerprints") != fingerprints
    ):
        raise CleanEvalBlocked("V3_BASELINE_OR_FINGERPRINT_MISMATCH")

    qualification_artifacts = baseline.get("qualification_artifacts")
    if not isinstance(qualification_artifacts, dict):
        raise CleanEvalBlocked("V3_QUALIFICATION_EVIDENCE_MISSING")
    for cell_key in ("sh_32k", "mh_6k"):
        reference = qualification_artifacts.get(cell_key)
        path = qualification_dir / f"{cell_key}_preflight.json"
        if (
            not isinstance(reference, dict)
            or reference.get("path") != path.name
            or not _verify_seal(path, identity=f"offline-preflight:{cell_key}")
        ):
            raise CleanEvalBlocked(f"V3_{cell_key.upper()}_PREFLIGHT_MISSING_OR_UNSEALED")
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        if (
            hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256")
            or hashlib.sha256(seal_path.read_bytes()).hexdigest() != reference.get("seal_sha256")
        ):
            raise CleanEvalBlocked(f"V3_{cell_key.upper()}_PREFLIGHT_HASH_MISMATCH")
        try:
            preflight = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked(f"V3_{cell_key.upper()}_PREFLIGHT_UNREADABLE") from None
        if (
            not isinstance(preflight, dict)
            or preflight.get("status") != "PASS"
            or preflight.get("classification") != "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER"
            or preflight.get("prepared_method_case_count") != 200
            or preflight.get("gold_values_read") is not False
            or preflight.get("provider_calls") != {"generation": 0, "count_tokens": 0}
            or preflight.get("implementation_fingerprints") != fingerprints
            or not isinstance(preflight.get("execution_plan"), list)
            or len(preflight["execution_plan"]) != 200
        ):
            raise CleanEvalBlocked(f"V3_{cell_key.upper()}_PREFLIGHT_NOT_QUALIFIED")

    gate_files = (
        ("route_preflight.json", "route-preflight-v3", "V3_ROUTE_PREFLIGHT_FAILED"),
        ("journal_durability_preflight.json", "journal-durability-v3", "V3_JOURNAL_PREFLIGHT_FAILED"),
        ("retry_policy_simulation.json", "retry-simulation-v3", "V3_RETRY_SIMULATION_FAILED"),
    )
    gate_artifacts = baseline.get("gate_artifacts")
    if not isinstance(gate_artifacts, dict):
        raise CleanEvalBlocked("V3_SEALED_GATE_HASHES_MISSING")
    for filename, identity, reason in gate_files:
        path = qualification_dir / filename
        reference = gate_artifacts.get(filename)
        if (
            not isinstance(reference, dict)
            or reference.get("path") != filename
            or not _verify_seal(path, identity=identity)
        ):
            raise CleanEvalBlocked(reason)
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        if (
            hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256")
            or hashlib.sha256(seal_path.read_bytes()).hexdigest() != reference.get("seal_sha256")
        ):
            raise CleanEvalBlocked(reason)
        try:
            gate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked(reason) from None
        if not isinstance(gate, dict) or gate.get("status") != "PASS":
            raise CleanEvalBlocked(reason)
        if filename == "route_preflight.json" and gate.get("provider_requests") != 0:
            raise CleanEvalBlocked(reason)
        if filename == "journal_durability_preflight.json" and gate.get("provider_calls") != 0:
            raise CleanEvalBlocked(reason)
        if filename == "retry_policy_simulation.json" and gate.get("provider_calls") != 0:
            raise CleanEvalBlocked(reason)


def _load_budget_ledger(
    *,
    path: Path | None = None,
    hard_cap_usd: float = GLOBAL_COST_CAP_USD,
) -> dict[str, Any]:
    path = path or FORMAL_BUDGET_LEDGER_PATH
    if not path.exists():
        return {
            "schema_version": "linkloom-public-memory-global-budget/v1",
            "hard_cap_usd": hard_cap_usd,
            "runs": [],
            "active_run_id": None,
            "ledger_sha256": canonical_sha256({
                "schema_version": "linkloom-public-memory-global-budget/v1",
                "hard_cap_usd": hard_cap_usd,
                "runs": [],
                "active_run_id": None,
            }),
        }
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("GLOBAL_BUDGET_LEDGER_UNREADABLE") from None
    if not isinstance(ledger, dict):
        raise CleanEvalBlocked("GLOBAL_BUDGET_LEDGER_INVALID")
    claimed = ledger.get("ledger_sha256")
    unsigned = {key: value for key, value in ledger.items() if key != "ledger_sha256"}
    resolved_path = path.resolve()
    formal_path = FORMAL_BUDGET_LEDGER_PATH.resolve()
    if (
        claimed != canonical_sha256(unsigned)
        or unsigned.get("schema_version") != "linkloom-public-memory-global-budget/v1"
        or unsigned.get("hard_cap_usd") != hard_cap_usd
        or not isinstance(unsigned.get("runs"), list)
        or (resolved_path == formal_path and unsigned.get("budget_scope") != "FORMAL_EVALUATION")
    ):
        raise CleanEvalBlocked("GLOBAL_BUDGET_LEDGER_INTEGRITY_FAILURE")
    return ledger


def _write_budget_ledger(ledger: dict[str, Any], *, path: Path | None = None) -> None:
    payload = {key: value for key, value in ledger.items() if key != "ledger_sha256"}
    payload["ledger_sha256"] = canonical_sha256(payload)
    _json_write(path or FORMAL_BUDGET_LEDGER_PATH, payload)
    ledger.clear()
    ledger.update(payload)


def _formal_budget_ledger_from_history(
    historical_runs: list[Mapping[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for history in historical_runs:
        run_id = history.get("run_id")
        spend = history.get("estimated_spend_upper_bound_usd")
        if (
            not isinstance(run_id, str)
            or run_id in seen
            or history.get("cell") != "sh_32k"
            or history.get("reserved_usd") != 0.0
            or not _finite_nonnegative_usd(spend)
            or not _cost_is_below_cap(float(spend), float(history.get("hard_cost_cap_usd", 0.0)))
        ):
            raise CleanEvalBlocked("FORMAL_BUDGET_HISTORY_INVALID")
        seen.add(run_id)
        rows.append(dict(history))
    unsigned = {
        "schema_version": "linkloom-public-memory-global-budget/v1",
        "budget_scope": "FORMAL_EVALUATION",
        "hard_cap_usd": GLOBAL_COST_CAP_USD,
        "runs": rows,
        "active_run_id": None,
    }
    if _ledger_reserved_total(unsigned) >= GLOBAL_COST_CAP_USD:
        raise CleanEvalBlocked("GLOBAL_ESTIMATED_SPEND_REACHED_HARD_CAP")
    return {**unsigned, "ledger_sha256": canonical_sha256(unsigned)}


def _verified_formal_sh_history_rows() -> list[dict[str, Any]]:
    run_ids = (
        ORIGINAL_STOPPED_SH_RUN_ID,
        STOPPED_SH_V2_RUN_ID,
        "c3744a527b7e40148c5017c0430c3f5b",
    )
    rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        root = ARTIFACT_ROOT / "sh_32k" / run_id
        manifest_path = root / "run_manifest.json"
        summary_path = root / "pilot_summary.json"
        artifact_manifest_path = root / "artifact_manifest.json"
        if (
            not _verify_seal(manifest_path, identity=f"run-manifest:{run_id}")
            or not _verify_seal(summary_path, identity=f"live-summary:{run_id}")
            or not _verify_seal(artifact_manifest_path, identity=f"artifact-manifest:{run_id}")
        ):
            raise CleanEvalBlocked(f"FORMAL_HISTORY_ARTIFACT_SEAL_INVALID_{run_id.upper()}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked(f"FORMAL_HISTORY_ARTIFACT_UNREADABLE_{run_id.upper()}") from None
        artifact_integrity = _verify_published_artifact_manifest(root, run_id)
        spend = summary.get("estimated_spend_upper_bound_usd")
        protocol_state_valid = (
            manifest.get("protocol_status") == "INVALID"
            and summary.get("protocol_status") == "INVALID"
        ) or _legacy_formal_run_is_non_scoreable(run_id, manifest, summary)
        if (
            artifact_integrity.get("status") != "PASS"
            or manifest.get("run_id") != run_id
            or manifest.get("cell") != "SH-32k"
            or summary.get("run_id") != run_id
            or manifest.get("status") != summary.get("status")
            or manifest.get("status") not in {"STOPPED", "PROVIDER_COMPLETION_BELOW_90_PERCENT"}
            or not protocol_state_valid
            or summary.get("gold_values_read") is not False
            or summary.get("gold_values_persisted") is not False
            or summary.get("score_status") != "SKIPPED"
            or spend != manifest.get("estimated_spend_upper_bound_usd")
            or not _finite_nonnegative_usd(spend)
            or summary.get("hard_cost_cap_usd") != CELLS["sh_32k"].hard_cost_cap_usd
            or manifest.get("hard_cost_cap_usd") != CELLS["sh_32k"].hard_cost_cap_usd
        ):
            raise CleanEvalBlocked(f"FORMAL_HISTORY_EVIDENCE_MISMATCH_{run_id.upper()}")
        rows.append(
            {
                "run_id": run_id,
                "cell": "sh_32k",
                "status": str(summary["status"]),
                "hard_cost_cap_usd": CELLS["sh_32k"].hard_cost_cap_usd,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": round(float(spend), 9),
                "implementation_fingerprint_sha256": manifest.get("implementation_fingerprint_sha256"),
                "scored_results_sha256": manifest.get("final_scored_results_sha256"),
                "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "source_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "source_artifact_manifest_sha256": hashlib.sha256(artifact_manifest_path.read_bytes()).hexdigest(),
            }
        )
    return rows


def _legacy_formal_run_is_non_scoreable(
    run_id: str,
    manifest: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> bool:
    """Recognize the sealed pre-protocol V1 run only as a failed, non-scoreable history row."""
    completion = summary.get("provider_completion")
    completion_rate = manifest.get("provider_completion_rate")
    output_hash = manifest.get("provider_outputs_sha256")
    return (
        run_id == ORIGINAL_STOPPED_SH_RUN_ID
        and manifest.get("schema_version") == "linkloom-public-memory-live-run/v1"
        and summary.get("schema_version") == "linkloom-public-memory-live-summary/v1"
        and manifest.get("run_id") == run_id
        and summary.get("run_id") == run_id
        and manifest.get("protocol_status") is None
        and summary.get("protocol_status") is None
        and manifest.get("status") == "PROVIDER_COMPLETION_BELOW_90_PERCENT"
        and summary.get("status") == manifest.get("status")
        and isinstance(manifest.get("stop_reason"), str)
        and manifest["stop_reason"].startswith("HARNESS_EXCEPTION_")
        and summary.get("stop_reason") == manifest.get("stop_reason")
        and isinstance(completion_rate, (int, float))
        and math.isfinite(float(completion_rate))
        and 0 <= float(completion_rate) < 0.9
        and isinstance(completion, Mapping)
        and completion.get("planned_method_cases") == 200
        and completion.get("completed_method_cases") == 0
        and completion.get("failed_method_cases") == 0
        and completion.get("not_run_method_cases") == 200
        and completion.get("rate") == completion_rate
        and manifest.get("score_status") == summary.get("score_status") == "SKIPPED"
        and manifest.get("gold_values_read") is False
        and summary.get("gold_values_read") is False
        and summary.get("gold_values_persisted") is False
        and manifest.get("output_sealed_before_scoring") is True
        and isinstance(output_hash, str)
        and len(output_hash) == 64
        and summary.get("final_scored_results_sha256") is None
        and manifest.get("implementation_fingerprint_status") == "PASS"
        and summary.get("implementation_fingerprint_status") == "PASS"
        and summary.get("artifact_durability_status") == "PASS"
    )


def _build_formal_budget_ledger() -> dict[str, Any]:
    expected_rows = _verified_formal_sh_history_rows()
    path = FORMAL_BUDGET_LEDGER_PATH
    if path.exists():
        ledger = _load_budget_ledger(path=path, hard_cap_usd=GLOBAL_COST_CAP_USD)
        expected_by_id = {row["run_id"]: row for row in expected_rows}
        actual_by_id = {row.get("run_id"): row for row in ledger.get("runs", []) if isinstance(row, dict)}
        if (
            ledger.get("active_run_id") is not None
            or not set(expected_by_id).issubset(actual_by_id)
            or any(actual_by_id[run_id] != row for run_id, row in expected_by_id.items())
            or any(row.get("cell") == "sh_32k" and row.get("run_id") not in expected_by_id for row in actual_by_id.values())
        ):
            raise CleanEvalBlocked("FORMAL_BUDGET_LEDGER_HISTORY_MISMATCH")
        return ledger
    ledger = _formal_budget_ledger_from_history(expected_rows)
    _write_budget_ledger(ledger, path=path)
    return ledger


def _read_reconciliation_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    previous = None
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CleanEvalBlocked("BUDGET_RECONCILIATION_LOG_UNREADABLE") from None
    for index, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            raise CleanEvalBlocked("BUDGET_RECONCILIATION_LOG_INVALID") from None
        unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
        if (
            event.get("sequence") != index
            or event.get("previous_event_sha256") != previous
            or event.get("event_sha256") != canonical_sha256(unsigned)
        ):
            raise CleanEvalBlocked("BUDGET_RECONCILIATION_LOG_INTEGRITY_FAILURE")
        previous = event["event_sha256"]
        events.append(event)
    return events


def _append_reconciliation_event(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    events = _read_reconciliation_events(path)
    existing = [event for event in events if event.get("run_id") == payload.get("run_id")]
    if existing:
        candidate = {key: value for key, value in payload.items() if key not in {"event_sha256", "sequence", "previous_event_sha256", "reconciled_at_utc"}}
        recorded = {key: value for key, value in existing[0].items() if key not in {"event_sha256", "sequence", "previous_event_sha256", "reconciled_at_utc"}}
        if len(existing) != 1 or candidate != recorded:
            raise CleanEvalBlocked("BUDGET_RECONCILIATION_LOG_CONFLICT")
        return existing[0]
    event = {
        **payload,
        "sequence": len(events) + 1,
        "previous_event_sha256": events[-1]["event_sha256"] if events else None,
        "reconciled_at_utc": datetime.now(UTC).isoformat(),
    }
    event["event_sha256"] = canonical_sha256(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        raise
    return event


def _reconcile_h3_1_diagnostic_budget(qualification_dir: Path) -> dict[str, Any]:
    main_path = CLEAN_EVAL_ROOT / "global_budget_ledger.json"
    independent_path = H3_1_DIAGNOSTIC_LEDGER_PATH
    main_ledger = _load_budget_ledger(path=main_path, hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD)
    independent = _load_budget_ledger(path=independent_path, hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD)
    artifact_root = ARTIFACT_ROOT / "harness_stability_diagnostic" / "sh_32k" / H3_1_DIAGNOSTIC_RUN_ID
    manifest_path = artifact_root / "run_manifest.json"
    summary_path = artifact_root / "pilot_summary.json"
    journal_path = artifact_root / "provider_attempt_journal.jsonl"
    evidence_path = artifact_root / "accepted_response_evidence.jsonl"
    if (
        not _verify_seal(manifest_path, identity=f"run-manifest:{H3_1_DIAGNOSTIC_RUN_ID}")
        or not _verify_seal(summary_path, identity=f"live-summary:{H3_1_DIAGNOSTIC_RUN_ID}")
        or _verify_published_artifact_manifest(artifact_root, H3_1_DIAGNOSTIC_RUN_ID).get("status") != "PASS"
    ):
        raise CleanEvalBlocked("H3_1_DIAGNOSTIC_ARTIFACT_INTEGRITY_FAILURE")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        journal = _read_attempt_journal(journal_path)
        evidence = _read_accepted_response_evidence(evidence_path)
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("H3_1_DIAGNOSTIC_EVIDENCE_UNREADABLE") from None
    cost_events = [
        event for event in journal
        if event.get("event") == "PROVIDER_ATTEMPT_STARTED"
    ]
    recomputed_upper_bound = round(
        sum(float(event["attempt_cost_upper_bound_reserved_usd"]) for event in cost_events),
        9,
    )
    independent_rows = {row.get("run_id"): row for row in independent.get("runs", []) if isinstance(row, dict)}
    independent_row = independent_rows.get(H3_1_DIAGNOSTIC_RUN_ID)
    main_rows = {row.get("run_id"): row for row in main_ledger.get("runs", []) if isinstance(row, dict)}
    main_row = main_rows.get(H3_1_DIAGNOSTIC_RUN_ID)
    if (
        manifest.get("run_id") != H3_1_DIAGNOSTIC_RUN_ID
        or summary.get("run_id") != H3_1_DIAGNOSTIC_RUN_ID
        or manifest.get("status") != "HARNESS_STRESS_PASS"
        or summary.get("status") != "HARNESS_STRESS_PASS"
        or manifest.get("purpose") != "HARNESS_STABILITY"
        or summary.get("gold_values_read") is not False
        or summary.get("gold_values_persisted") is not False
        or summary.get("score_status") != "DISABLED_DIAGNOSTIC_ONLY"
        or summary.get("journal_protocol", {}).get("integrity_valid") is not True
        or summary.get("journal_protocol", {}).get("method_case_lifecycles_closed") is not True
        or summary.get("journal_protocol", {}).get("terminal_method_cases") != 200
        or not evidence.get("integrity_valid")
        or len(evidence.get("rows", [])) != 200
        or len(cost_events) != 200
        or recomputed_upper_bound != H3_1_DIAGNOSTIC_SPEND_UPPER_BOUND_USD
        or summary.get("estimated_spend_upper_bound_usd") != recomputed_upper_bound
        or manifest.get("estimated_spend_upper_bound_usd") != recomputed_upper_bound
        or not isinstance(independent_row, dict)
        or independent_row.get("status") != "HARNESS_STRESS_PASS"
        or independent_row.get("estimated_spend_upper_bound_usd") != recomputed_upper_bound
        or independent_row.get("reserved_usd") != 0.0
        or not isinstance(main_row, dict)
        or not (
            main_ledger.get("active_run_id") == H3_1_DIAGNOSTIC_RUN_ID
            and main_row.get("status") == "RUNNING"
            and main_row.get("reserved_usd") == 6.0
            and main_row.get("estimated_spend_upper_bound_usd") == 0.0
        ) and not (
            main_ledger.get("active_run_id") is None
            and main_row.get("status") == "HARNESS_STRESS_PASS"
            and main_row.get("reserved_usd") == 0.0
            and main_row.get("estimated_spend_upper_bound_usd") == H3_1_DIAGNOSTIC_SPEND_UPPER_BOUND_USD
        )
    ):
        raise CleanEvalBlocked("H3_1_DIAGNOSTIC_LEDGER_OR_COST_MISMATCH")

    snapshot_path = qualification_dir / "root_diagnostic_ledger_before_reconciliation.json"
    if snapshot_path.exists():
        if not _verify_seal(
            snapshot_path,
            identity="root-diagnostic-budget-ledger-before-reconciliation",
        ):
            raise CleanEvalBlocked("DIAGNOSTIC_LEDGER_SNAPSHOT_CONFLICT")
        original_bytes = snapshot_path.read_bytes()
    else:
        original_bytes = main_path.read_bytes()
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(original_bytes)
        _seal_file(snapshot_path, identity="root-diagnostic-budget-ledger-before-reconciliation")
    event_payload = {
        "event_type": "SETTLE_DIAGNOSTIC_RESERVATION",
        "budget_scope": "DIAGNOSTIC_TASK_10_USD",
        "run_id": H3_1_DIAGNOSTIC_RUN_ID,
        "source_main_ledger_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "source_independent_ledger_sha256": independent.get("ledger_sha256"),
        "source_run_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "source_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "source_artifact_manifest_sha256": hashlib.sha256((artifact_root / "artifact_manifest.json").read_bytes()).hexdigest(),
        "status": "HARNESS_STRESS_PASS",
        "estimated_spend_upper_bound_usd": recomputed_upper_bound,
        "provider_billed_amount_usd": None,
        "reservation_released_usd": 6.0,
    }
    reconciliation_log = CLEAN_EVAL_ROOT / "budget_reconciliation_events.jsonl"
    _append_reconciliation_event(reconciliation_log, event_payload)
    changed = _reconcile_budget_row(
        main_ledger,
        run_id=H3_1_DIAGNOSTIC_RUN_ID,
        status="HARNESS_STRESS_PASS",
        estimated_spend_upper_bound_usd=recomputed_upper_bound,
        expected_reserved_usd=6.0,
        hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD,
    )
    main_ledger["budget_scope"] = "DIAGNOSTIC_TASK"
    if changed or main_ledger.get("budget_scope") != "DIAGNOSTIC_TASK" or main_ledger.get("ledger_sha256") != canonical_sha256({key: value for key, value in main_ledger.items() if key != "ledger_sha256"}):
        _write_budget_ledger(main_ledger, path=main_path)
    final_main = _load_budget_ledger(path=main_path, hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD)
    final_rows = {row.get("run_id"): row for row in final_main.get("runs", []) if isinstance(row, dict)}
    for run_id, independent_run in independent_rows.items():
        reconciled_run = final_rows.get(run_id)
        if not isinstance(reconciled_run, dict) or any(
            reconciled_run.get(field) != independent_run.get(field)
            for field in ("status", "reserved_usd", "estimated_spend_upper_bound_usd", "hard_cost_cap_usd", "cell")
        ):
            raise CleanEvalBlocked("DIAGNOSTIC_LEDGER_RECONCILIATION_MISMATCH")
    report = {
        "schema_version": "linkloom-mh-only-budget-reconciliation/v1",
        "status": "PASS",
        "h3_1_run_id": H3_1_DIAGNOSTIC_RUN_ID,
        "h3_1_status": "HARNESS_STRESS_PASS",
        "estimated_spend_upper_bound_usd": recomputed_upper_bound,
        "provider_billed_amount_usd": None,
        "reservation_released_usd": 6.0,
        "diagnostic_h2_spend_upper_bound_usd": 0.3528855,
        "diagnostic_task_total_spend_upper_bound_usd": round(
            sum(float(row.get("estimated_spend_upper_bound_usd", 0.0)) for row in independent.get("runs", [])),
            9,
        ),
        "diagnostic_task_hard_cap_usd": DIAGNOSTIC_TASK_CAP_USD,
        "formal_evaluation_hard_cap_usd": GLOBAL_COST_CAP_USD,
        "formal_budget_ledger_is_separate": True,
        "append_only_event_log": str(reconciliation_log.relative_to(CLEAN_EVAL_ROOT)),
        "event_sha256": _read_reconciliation_events(reconciliation_log)[-1]["event_sha256"],
        "reconciled_at_utc": datetime.now(UTC).isoformat(),
    }
    report_path = qualification_dir / "ledger_reconciliation_report.json"
    if report_path.exists():
        if not _verify_seal(report_path, identity="mh-only-ledger-reconciliation"):
            raise CleanEvalBlocked("DIAGNOSTIC_LEDGER_RECONCILIATION_REPORT_CONFLICT")
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            existing.get("h3_1_run_id") != H3_1_DIAGNOSTIC_RUN_ID
            or existing.get("status") != "PASS"
            or existing.get("event_sha256") != report.get("event_sha256")
            or existing.get("estimated_spend_upper_bound_usd") != recomputed_upper_bound
            or existing.get("reservation_released_usd") != 6.0
        ):
            raise CleanEvalBlocked("DIAGNOSTIC_LEDGER_RECONCILIATION_REPORT_CONFLICT")
    else:
        _atomic_json_write_fsync(report_path, report)
        _seal_file(report_path, identity="mh-only-ledger-reconciliation")
    return report


def _write_authorized_mh_only_baseline(
    *,
    qualification_dir: Path,
    fingerprints: dict[str, Any],
    plan: dict[str, Any],
    formal_budget_ledger: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    if plan.get("plan_id") != MH_ONLY_EVALUATION_PLAN_ID:
        raise CleanEvalBlocked("MH_ONLY_BASELINE_PLAN_ID_INVALID")
    if implementation_fingerprints() != fingerprints:
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH_BEFORE_AUTHORIZATION")
    current_plan = _load_evaluation_plan(MH_ONLY_EVALUATION_PLAN_ID)
    if _evaluation_plan_sha256(current_plan) != _evaluation_plan_sha256(plan):
        raise CleanEvalBlocked("MH_ONLY_EVALUATION_PLAN_DRIFT")
    try:
        qualification_dir.resolve().relative_to(CLEAN_EVAL_ROOT.resolve())
    except ValueError:
        raise CleanEvalBlocked("MH_ONLY_QUALIFICATION_PATH_OUTSIDE_EVALUATION_ROOT") from None

    baseline_path = qualification_dir / "AUTHORIZED_MH_ONLY_CLEAN_BASELINE.json"
    if baseline_path.exists():
        raise CleanEvalBlocked("MH_ONLY_BASELINE_ALREADY_EXISTS")
    ledger_snapshot_path = qualification_dir / "formal_budget_ledger.json"
    current_ledger = _load_budget_ledger(
        path=FORMAL_BUDGET_LEDGER_PATH,
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    if (
        current_ledger.get("ledger_sha256") != formal_budget_ledger.get("ledger_sha256")
        or current_ledger.get("active_run_id") is not None
        or not _cost_is_below_cap(
            _ledger_reserved_total(current_ledger) + CELLS["mh_6k"].hard_cost_cap_usd,
            GLOBAL_COST_CAP_USD,
        )
    ):
        raise CleanEvalBlocked("MH_ONLY_FORMAL_BUDGET_UNAVAILABLE")
    qualification_dir.mkdir(parents=True, exist_ok=True)
    if ledger_snapshot_path.exists():
        if not _verify_seal(ledger_snapshot_path, identity="mh-only-formal-budget-ledger"):
            raise CleanEvalBlocked("MH_ONLY_BUDGET_SNAPSHOT_ALREADY_EXISTS")
        try:
            snapshot = json.loads(ledger_snapshot_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            raise CleanEvalBlocked("MH_ONLY_BUDGET_SNAPSHOT_ALREADY_EXISTS") from None
        if snapshot.get("ledger_sha256") != current_ledger.get("ledger_sha256"):
            raise CleanEvalBlocked("MH_ONLY_BUDGET_SNAPSHOT_ALREADY_EXISTS")
    else:
        _atomic_json_write_fsync(ledger_snapshot_path, current_ledger)
        _seal_file(ledger_snapshot_path, identity="mh-only-formal-budget-ledger")

    for filename, identity in MH_ONLY_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        path = qualification_dir / filename
        if not path.is_file():
            raise CleanEvalBlocked(f"MH_ONLY_QUALIFICATION_ARTIFACT_MISSING_{filename.upper()}")
        if not path.with_suffix(path.suffix + ".seal.json").is_file():
            _seal_file(path, identity=identity)
        if not _verify_seal(path, identity=identity):
            raise CleanEvalBlocked(f"MH_ONLY_QUALIFICATION_ARTIFACT_INVALID_{filename.upper()}")

    protocol_refs: dict[str, dict[str, Any]] = {}
    for filename, identity in (
        ("E1_SCORE_PROTOCOL.json", "e1-score-protocol"),
        ("E1_SCORE_PROTOCOL_AMENDMENT.json", "e1-score-protocol-amendment"),
    ):
        path = E1_SCORE_PROTOCOL_ROOT / filename
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        if not _verify_seal(path, identity=identity):
            raise CleanEvalBlocked("MH_ONLY_SCORE_PROTOCOL_REFERENCE_MISSING")
        protocol = json.loads(path.read_text(encoding="utf-8"))
        if not _frozen_score_protocol_matches(filename, protocol):
            raise CleanEvalBlocked("MH_ONLY_SCORE_PROTOCOL_REFERENCE_INVALID")
        protocol_refs[filename] = {
            "path": f"{E1_SCORE_PROTOCOL_ROOT.name}/{filename}",
            "identity": identity,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seal_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
        }

    artifact_refs: dict[str, dict[str, Any]] = {}
    for filename, identity in MH_ONLY_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        path = qualification_dir / filename
        seal_path = path.with_suffix(path.suffix + ".seal.json")
        artifact_refs[filename] = {
            "path": filename,
            "identity": identity,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seal_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
        }

    preflight = json.loads((qualification_dir / "mh_6k_preflight.json").read_text(encoding="utf-8"))
    prior_fingerprints = preflight.get("implementation_fingerprints", {})
    semantic = json.loads((qualification_dir / "semantic_equivalence.json").read_text(encoding="utf-8"))
    prior_e1_baseline = E1_SCORE_PROTOCOL_ROOT / "E1_CLEAN_EVALUATION_BASELINE.json"
    h3_dir = CLEAN_EVAL_ROOT / "harness_stability_diagnostic" / "h3_1_20261004_089f8bf0"
    h3_baseline = h3_dir / "DIAGNOSTIC_EXECUTION_BASELINE.json"
    legacy_v3_baseline = CLEAN_EVAL_ROOT / "authorized_execution_v3" / "AUTHORIZED_EXECUTION_BASELINE_V3.json"
    for path, identity in (
        (prior_e1_baseline, "e1-clean-evaluation-baseline"),
        (h3_baseline, "diagnostic-execution-baseline"),
        (legacy_v3_baseline, "authorized-execution-baseline-v3"),
    ):
        if not _verify_seal(path, identity=identity):
            raise CleanEvalBlocked("MH_ONLY_HISTORICAL_BASELINE_INVALID")

    diagnostic_ledger = _load_budget_ledger(
        path=H3_1_DIAGNOSTIC_LEDGER_PATH,
        hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD,
    )
    diagnostic_spend = round(_ledger_reserved_total(diagnostic_ledger), 9)
    formal_spend = round(_ledger_reserved_total(current_ledger), 9)
    payload = {
        "schema_version": "linkloom-authorized-mh-only-clean-baseline/v1",
        "baseline_id": "AUTHORIZED_MH_ONLY_CLEAN_BASELINE",
        "status": "AUTHORIZED_MH_ONLY_CLEAN_BASELINE",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "live_execution_authorized": True,
        "protocol_amendment_reason": (
            "SH-32k was used as a multi-round harness diagnostic and is classified as DIAGNOSTIC_STRESS_SET; "
            "the approved V1 clean evaluation therefore uses MH-6k only."
        ),
        "change_scope": [
            "versioned evaluation-plan selection",
            "formal-versus-diagnostic budget ledger separation and append-only diagnostic reconciliation",
            "fixed-denominator score coverage reporting",
        ],
        "historical_plan_preserved": {
            "plan_id": LEGACY_DUAL_EVALUATION_PLAN_ID,
            "baseline_path": "authorized_execution_v3/AUTHORIZED_EXECUTION_BASELINE_V3.json",
            "baseline_sha256": hashlib.sha256(legacy_v3_baseline.read_bytes()).hexdigest(),
            "old_sh_first_prerequisite_retained": True,
        },
        "historical_baselines": {
            "e1_failed_candidate_sha256": hashlib.sha256(prior_e1_baseline.read_bytes()).hexdigest(),
            "h3_1_diagnostic_baseline_sha256": hashlib.sha256(h3_baseline.read_bytes()).hexdigest(),
            "v3_dual_dataset_baseline_sha256": hashlib.sha256(legacy_v3_baseline.read_bytes()).hexdigest(),
        },
        "evaluation_plan_id": MH_ONLY_EVALUATION_PLAN_ID,
        "evaluation_plan_sha256": _evaluation_plan_sha256(plan),
        "evaluation_plan_registry_sha256": fingerprints["files"][
            "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json"
        ],
        "fingerprints": fingerprints,
        "fingerprint_change_audit": {
            "approved_policy_files": [
                "scripts/run_public_memory_clean_eval.py",
                "benchmarks/memoryagentbench/public_memory_profile.py",
                "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json",
            ],
            "prior_e1_fingerprints": prior_fingerprints,
            "semantic_invariants": semantic["semantic_invariants"],
        },
        "mh_6k_subset_sha256": preflight.get("subset_sha256"),
        "dataset_sha256": preflight.get("dataset_sha256"),
        "score_protocol_artifacts": protocol_refs,
        "qualification_artifacts": artifact_refs,
        "formal_budget_ledger_sha256": current_ledger["ledger_sha256"],
        "budget": {
            "formal_evaluation_hard_cap_usd": GLOBAL_COST_CAP_USD,
            "mh_6k_run_hard_cap_usd": CELLS["mh_6k"].hard_cost_cap_usd,
            "formal_historical_spend_upper_bound_usd": formal_spend,
            "formal_reserved_for_mh_6k_usd": CELLS["mh_6k"].hard_cost_cap_usd,
            "formal_available_after_reservation_usd": round(
                GLOBAL_COST_CAP_USD - formal_spend - CELLS["mh_6k"].hard_cost_cap_usd,
                9,
            ),
            "diagnostic_task_hard_cap_usd": DIAGNOSTIC_TASK_CAP_USD,
            "diagnostic_historical_spend_upper_bound_usd": diagnostic_spend,
            "diagnostic_reservation_active": diagnostic_ledger.get("active_run_id") is not None,
            "estimated_upper_bounds_are_not_provider_bills": True,
        },
        "authorized_run": {
            "cell": "mh_6k",
            "qa_count": 100,
            "method_case_count": 200,
            "method_order": ["Flat Retrieval", "LinkLoom Temporal Memory"],
            "generation_attempt_limit": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            "gold_unlock": "only after all 200 case lifecycles complete and sealed outputs/journal gates pass",
            "scoring_protocol": "E1_SCORE_PROTOCOL.json plus its frozen amendment",
        },
    }
    _atomic_json_write_fsync(baseline_path, payload)
    _seal_file(baseline_path, identity="authorized-mh-only-clean-baseline")
    _verify_authorized_mh_only_baseline(qualification_dir, fingerprints, plan)
    return baseline_path, payload


def _write_mh_only_semantic_equivalence(
    *,
    qualification_dir: Path,
    current_preflight: dict[str, Any],
) -> dict[str, Any]:
    prior_path = E1_SCORE_PROTOCOL_ROOT / "mh_6k_preflight.json"
    if not _verify_seal(prior_path, identity="offline-preflight:mh_6k"):
        raise CleanEvalBlocked("MH_ONLY_PRIOR_PREFLIGHT_MISSING_OR_UNSEALED")
    try:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("MH_ONLY_PRIOR_PREFLIGHT_UNREADABLE") from None
    if (
        prior.get("status") != "PASS"
        or prior.get("gold_values_read") is not False
        or prior.get("provider_calls") != {"generation": 0, "count_tokens": 0}
        or current_preflight.get("status") != "PASS"
        or current_preflight.get("gold_values_read") is not False
        or current_preflight.get("provider_calls") != {"generation": 0, "count_tokens": 0}
    ):
        raise CleanEvalBlocked("MH_ONLY_PREFLIGHT_SEMANTIC_INPUT_INVALID")
    prior_fingerprints = prior.get("implementation_fingerprints", {})
    current_fingerprints = current_preflight.get("implementation_fingerprints", {})
    prior_files = prior_fingerprints.get("files", {})
    current_files = current_fingerprints.get("files", {})
    changed_files = sorted(
        path
        for path in set(prior_files) | set(current_files)
        if prior_files.get(path) != current_files.get(path)
    )
    authorized_policy_files = {
        "scripts/run_public_memory_clean_eval.py",
        "benchmarks/memoryagentbench/public_memory_profile.py",
        "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json",
    }
    plan_projection_keys = (
        "sequence",
        "qa_id",
        "method",
        "generation_request_sha256",
        "count_tokens_request_sha256",
        "context_sha256",
        "context_tokens_estimated",
        "context_evidence_ids_selected",
        "retrieval_evidence_ids",
        "memory_lookup_status",
        "memory_source_evidence_ids",
        "static_input_token_upper_bound",
        "one_attempt_cost_upper_bound_usd",
    )
    prior_rows = prior.get("execution_plan", [])
    current_rows = current_preflight.get("execution_plan", [])
    prior_plan = [{key: row.get(key) for key in plan_projection_keys} for row in prior_rows]
    current_plan = [{key: row.get(key) for key in plan_projection_keys} for row in current_rows]
    invariant_files = {
        "adapter": ("benchmarks/memoryagentbench/adapter.py",),
        "temporal_memory": ("benchmarks/memoryagentbench/memory.py",),
        "retrieval": (
            "src/linkloom/indexing/bm25.py",
            "src/linkloom/indexing/models.py",
            "src/linkloom/indexing/__init__.py",
            "src/linkloom/retrieval_v2/models.py",
            "src/linkloom/agents/team_decision.py",
        ),
        "prompt": ("benchmarks/memoryagentbench/g3_pilot.py",),
        "gemini_generation_config": (
            "src/linkloom/agents/providers/gemini_api.py",
            "src/linkloom/agents/providers/retry_policy.py",
        ),
    }
    semantic_invariants = {
        name: all(prior_files.get(path) == current_files.get(path) for path in paths)
        for name, paths in invariant_files.items()
    }
    semantic_invariants["gemini_generation_config"] = (
        semantic_invariants["gemini_generation_config"]
        and prior_fingerprints.get("runtime_config") == current_fingerprints.get("runtime_config")
    )
    semantic_invariants["dataset_subset"] = (
        prior.get("dataset_sha256") == current_preflight.get("dataset_sha256") == DATASET_SHA256
        and prior.get("subset_sha256") == current_preflight.get("subset_sha256")
        and len(prior_plan) == len(current_plan) == 200
    )
    semantic_invariants["scoring_rules"] = (
        prior_files.get("benchmarks/memoryagentbench/g3_pilot.py")
        == current_files.get("benchmarks/memoryagentbench/g3_pilot.py")
        and _verify_seal(E1_SCORE_PROTOCOL_ROOT / "E1_SCORE_PROTOCOL.json", identity="e1-score-protocol")
        and _verify_seal(
            E1_SCORE_PROTOCOL_ROOT / "E1_SCORE_PROTOCOL_AMENDMENT.json",
            identity="e1-score-protocol-amendment",
        )
    )
    if (
        set(changed_files) != authorized_policy_files
        or prior_plan != current_plan
        or not all(semantic_invariants.values())
    ):
        raise CleanEvalBlocked("MH_ONLY_SEMANTIC_EQUIVALENCE_FAILED")
    report = {
        "schema_version": "linkloom-mh-only-semantic-equivalence/v1",
        "status": "PASS",
        "prior_preflight_sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
        "current_preflight_sha256": hashlib.sha256(
            (qualification_dir / "mh_6k_preflight.json").read_bytes()
        ).hexdigest(),
        "prior_fingerprint_sha256": prior_fingerprints.get("files_sha256"),
        "current_fingerprint_sha256": current_fingerprints.get("files_sha256"),
        "changed_fingerprint_files": changed_files,
        "authorized_policy_files": sorted(authorized_policy_files),
        "generation_request_semantics_equal": prior_plan == current_plan,
        "generation_plan_row_count": len(current_plan),
        "runtime_config_sha256_equal": (
            prior_fingerprints.get("runtime_config_sha256")
            == current_fingerprints.get("runtime_config_sha256")
        ),
        "semantic_invariants": semantic_invariants,
        "official_gold_reads": 0,
        "provider_calls": 0,
    }
    report_path = qualification_dir / "semantic_equivalence.json"
    if report_path.exists():
        raise CleanEvalBlocked("MH_ONLY_SEMANTIC_REPORT_ALREADY_EXISTS")
    _atomic_json_write_fsync(report_path, report)
    _seal_file(report_path, identity="mh-only-semantic-equivalence")
    return report


def _ledger_reserved_total(ledger: dict[str, Any]) -> float:
    total = 0.0
    for run in ledger.get("runs", []):
        if not isinstance(run, dict):
            raise CleanEvalBlocked("GLOBAL_BUDGET_LEDGER_INVALID")
        total += float(run.get("estimated_spend_upper_bound_usd", 0.0))
        total += float(run.get("reserved_usd", 0.0))
    return total


def _begin_budget_reservation(
    ledger: dict[str, Any],
    cell_key: str,
    run_id: str,
    *,
    global_cap_usd: float = GLOBAL_COST_CAP_USD,
    reservation_usd: float | None = None,
    ledger_path: Path | None = None,
) -> None:
    if ledger.get("active_run_id") is not None:
        raise CleanEvalBlocked("GLOBAL_BUDGET_LEDGER_HAS_ACTIVE_RUN")
    reserved_total = _ledger_reserved_total(ledger)
    cap = CELLS[cell_key].hard_cost_cap_usd if reservation_usd is None else reservation_usd
    if reserved_total + cap >= global_cap_usd:
        raise CleanEvalBlocked("GLOBAL_BUDGET_CAP_WOULD_BE_REACHED")
    runs = ledger["runs"]
    runs.append(
        {
            "run_id": run_id,
            "cell": cell_key,
            "status": "RUNNING",
            "hard_cost_cap_usd": cap,
            "reserved_usd": cap,
            "estimated_spend_upper_bound_usd": 0.0,
        }
    )
    ledger["active_run_id"] = run_id
    if ledger_path is None:
        _write_budget_ledger(ledger)
    else:
        _write_budget_ledger(ledger, path=ledger_path)


def _finish_budget_reservation(
    ledger: dict[str, Any],
    run_id: str,
    *,
    status: str,
    estimated_spend_upper_bound_usd: float,
    global_cap_usd: float = GLOBAL_COST_CAP_USD,
    ledger_path: Path | None = None,
) -> None:
    for row in ledger.get("runs", []):
        if isinstance(row, dict) and row.get("run_id") == run_id:
            row["status"] = status
            row["reserved_usd"] = 0.0
            row["estimated_spend_upper_bound_usd"] = round(estimated_spend_upper_bound_usd, 9)
            break
    else:
        raise CleanEvalBlocked("GLOBAL_BUDGET_RESERVATION_MISSING")
    if ledger.get("active_run_id") == run_id:
        ledger["active_run_id"] = None
    if _ledger_reserved_total(ledger) >= global_cap_usd:
        raise CleanEvalBlocked("GLOBAL_ESTIMATED_SPEND_REACHED_HARD_CAP")
    if ledger_path is None:
        _write_budget_ledger(ledger)
    else:
        _write_budget_ledger(ledger, path=ledger_path)


def _is_original_stopped_sh_budget_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("run_id") == ORIGINAL_STOPPED_SH_RUN_ID
        and row.get("cell") == "sh_32k"
        and row.get("status") == "PROVIDER_COMPLETION_BELOW_90_PERCENT"
    )


def _validate_original_stopped_sh_run(ledger_row: Mapping[str, Any]) -> None:
    if not _is_original_stopped_sh_budget_row(ledger_row):
        raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_RUN_IDENTITY_MISMATCH")
    if (
        ledger_row.get("reserved_usd") != 0.0
        or ledger_row.get("estimated_spend_upper_bound_usd") != 0.004374
        or ledger_row.get("hard_cost_cap_usd") != CELLS["sh_32k"].hard_cost_cap_usd
    ):
        raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_BUDGET_RECORD_MISMATCH")

    root = ARTIFACT_ROOT / "sh_32k" / ORIGINAL_STOPPED_SH_RUN_ID
    manifest_path = root / "run_manifest.json"
    summary_path = root / "pilot_summary.json"
    artifacts_path = root / "artifact_manifest.json"
    journal_path = root / "provider_attempt_journal.jsonl"
    if (
        not _verify_seal(manifest_path, identity=f"run-manifest:{ORIGINAL_STOPPED_SH_RUN_ID}")
        or not _verify_seal(summary_path, identity=f"live-summary:{ORIGINAL_STOPPED_SH_RUN_ID}")
        or not _verify_seal(artifacts_path, identity=f"artifact-manifest:{ORIGINAL_STOPPED_SH_RUN_ID}")
        or not journal_path.is_file()
        or journal_path.stat().st_size != 0
    ):
        raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_ARTIFACT_SEAL_OR_JOURNAL_INVALID")
    try:
        run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        artifact_manifest = json.loads(artifacts_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_ARTIFACT_UNREADABLE") from None

    completion = summary.get("provider_completion")
    requests = summary.get("provider_requests")
    if (
        run_manifest.get("run_id") != ORIGINAL_STOPPED_SH_RUN_ID
        or run_manifest.get("status") != "PROVIDER_COMPLETION_BELOW_90_PERCENT"
        or run_manifest.get("stop_reason") != "HARNESS_EXCEPTION_FileExistsError"
        or run_manifest.get("provider_completion_rate") != 0.0
        or run_manifest.get("estimated_spend_upper_bound_usd") != 0.004374
        or summary.get("run_id") != ORIGINAL_STOPPED_SH_RUN_ID
        or summary.get("status") != "PROVIDER_COMPLETION_BELOW_90_PERCENT"
        or summary.get("stop_reason") != "HARNESS_EXCEPTION_FileExistsError"
        or summary.get("gold_values_read") is not False
        or not isinstance(completion, dict)
        or completion.get("completed_method_cases") != 0
        or completion.get("planned_method_cases") != 200
        or completion.get("rate") != 0.0
        or not isinstance(requests, dict)
        or requests.get("generation_attempts") != 0
        or requests.get("count_tokens_requests") != 0
        or artifact_manifest.get("schema_version") != "linkloom-public-memory-artifact-manifest/v1"
        or artifact_manifest.get("run_id") != ORIGINAL_STOPPED_SH_RUN_ID
        or not isinstance(artifact_manifest.get("files"), dict)
        or len(artifact_manifest["files"]) != ORIGINAL_STOPPED_SH_ARTIFACT_COUNT
        or artifact_manifest.get("files_sha256") != canonical_sha256(artifact_manifest["files"])
    ):
        raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_EVIDENCE_MISMATCH")

    resolved_root = root.resolve()
    for relative_name, expected_digest in artifact_manifest["files"].items():
        if not isinstance(relative_name, str):
            raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_ARTIFACT_PATH_INVALID")
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_ARTIFACT_PATH_INVALID")
        target = (root / relative_path).resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError:
            raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_ARTIFACT_PATH_INVALID") from None
        if (
            not target.is_file()
            or not isinstance(expected_digest, str)
            or hashlib.sha256(target.read_bytes()).hexdigest() != expected_digest
        ):
            raise CleanEvalBlocked("ORIGINAL_STOPPED_SH_ARTIFACT_HASH_MISMATCH")


def _validate_stopped_v2_sh_run(ledger_row: Mapping[str, Any]) -> None:
    """Read-only verification that the failed V2 run remains sealed and stopped."""
    if (
        ledger_row.get("run_id") != STOPPED_SH_V2_RUN_ID
        or ledger_row.get("cell") != "sh_32k"
        or ledger_row.get("status") != "PROVIDER_COMPLETION_BELOW_90_PERCENT"
        or ledger_row.get("reserved_usd") != 0.0
        or ledger_row.get("estimated_spend_upper_bound_usd") != 0.080115
        or ledger_row.get("hard_cost_cap_usd") != CELLS["sh_32k"].hard_cost_cap_usd
    ):
        raise CleanEvalBlocked("STOPPED_SH_V2_BUDGET_RECORD_MISMATCH")

    root = ARTIFACT_ROOT / "sh_32k" / STOPPED_SH_V2_RUN_ID
    manifest_path = root / "run_manifest.json"
    summary_path = root / "pilot_summary.json"
    outputs_path = root / "provider_outputs.json"
    artifact_manifest_path = root / "artifact_manifest.json"
    journal_path = root / "provider_attempt_journal.jsonl"
    if (
        not _verify_seal(manifest_path, identity=f"run-manifest:{STOPPED_SH_V2_RUN_ID}")
        or not _verify_seal(summary_path, identity=f"live-summary:{STOPPED_SH_V2_RUN_ID}")
        or not _verify_seal(outputs_path, identity="provider-outputs:factconsolidation_sh_32k")
        or not _verify_seal(artifact_manifest_path, identity=f"artifact-manifest:{STOPPED_SH_V2_RUN_ID}")
        or not journal_path.is_file()
    ):
        raise CleanEvalBlocked("STOPPED_SH_V2_ARTIFACT_SEAL_INVALID")
    try:
        run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        outputs = json.loads(outputs_path.read_text(encoding="utf-8"))
        artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
        events = _read_attempt_journal(journal_path)
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("STOPPED_SH_V2_ARTIFACT_UNREADABLE") from None
    completion = summary.get("provider_completion")
    requests = summary.get("provider_requests")
    if (
        run_manifest.get("run_id") != STOPPED_SH_V2_RUN_ID
        or run_manifest.get("status") != "STOPPED"
        or run_manifest.get("stop_reason") != "NON_TRANSIENT_PROVIDER_OR_HARNESS_FAILURE"
        or run_manifest.get("protocol_status") != "INVALID"
        or summary.get("run_id") != STOPPED_SH_V2_RUN_ID
        or summary.get("status") != "STOPPED"
        or summary.get("protocol_status") != "INVALID"
        or summary.get("continuation_eligible") is not False
        or summary.get("gold_values_read") is not False
        or summary.get("gold_values_persisted") is not False
        or summary.get("score_status") != "SKIPPED"
        or not isinstance(completion, dict)
        or completion.get("completed_method_cases") != 19
        or completion.get("planned_method_cases") != 200
        or completion.get("rate") != 0.095
        or not isinstance(requests, dict)
        or requests.get("generation_attempts") != 20
        or requests.get("count_tokens_requests") != 2
        or summary.get("generation_retries") not in {None, 0}
        or outputs.get("gold_values_read") is not False
        or artifact_manifest.get("schema_version") != "linkloom-public-memory-artifact-manifest/v1"
        or artifact_manifest.get("run_id") != STOPPED_SH_V2_RUN_ID
        or not isinstance(artifact_manifest.get("files"), dict)
        or len(artifact_manifest["files"]) != STOPPED_SH_V2_ARTIFACT_COUNT
        or artifact_manifest.get("files_sha256") != canonical_sha256(artifact_manifest["files"])
    ):
        raise CleanEvalBlocked("STOPPED_SH_V2_EVIDENCE_MISMATCH")

    journal_summary = _validate_attempt_journal_events(events)
    if (
        not journal_summary["integrity_valid"]
        or journal_summary["provider_attempt_starts"] != 20
        or journal_summary["provider_attempt_completions"] != 20
        or journal_summary["last_durable_journal_event"].get("sequence") != 20
        or journal_summary["last_durable_journal_event"].get("event") != "PROVIDER_ATTEMPT_COMPLETED"
    ):
        raise CleanEvalBlocked("STOPPED_SH_V2_JOURNAL_EVIDENCE_MISMATCH")
    last_completion = next(
        (
            event
            for event in reversed(events)
            if event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
        ),
        None,
    )
    if (
        not isinstance(last_completion, dict)
        or last_completion.get("sequence") != 20
        or last_completion.get("safe_error", {}).get("exception_class") != "ConnectError"
        or last_completion.get("retryable_transient_failure") is not False
    ):
        raise CleanEvalBlocked("STOPPED_SH_V2_LAST_ATTEMPT_MISMATCH")

    resolved_root = root.resolve()
    for relative_name, expected_digest in artifact_manifest["files"].items():
        if not isinstance(relative_name, str):
            raise CleanEvalBlocked("STOPPED_SH_V2_ARTIFACT_PATH_INVALID")
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise CleanEvalBlocked("STOPPED_SH_V2_ARTIFACT_PATH_INVALID")
        target = (root / relative_path).resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError:
            raise CleanEvalBlocked("STOPPED_SH_V2_ARTIFACT_PATH_INVALID") from None
        if (
            not target.is_file()
            or not isinstance(expected_digest, str)
            or hashlib.sha256(target.read_bytes()).hexdigest() != expected_digest
        ):
            raise CleanEvalBlocked("STOPPED_SH_V2_ARTIFACT_HASH_MISMATCH")


def _safe_proxy_endpoint(
    value: str | None,
) -> tuple[str | None, int | None, bool, str | None]:
    if not value:
        return None, None, False, None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None, None, True, parsed.scheme.casefold() or None
    return (
        parsed.hostname,
        port,
        bool(parsed.username or parsed.password),
        parsed.scheme.casefold() or None,
    )


def _cost_is_below_cap(estimate: float, cap: float) -> bool:
    return math.isfinite(estimate) and math.isfinite(cap) and 0 <= estimate < cap


def _contains_gold_payload_field(value: Any) -> bool:
    gold_field_names = {
        "answer",
        "answers",
        "gold",
        "gold_answer",
        "gold_answers",
        "rationale",
        "rationales",
    }
    if isinstance(value, Mapping):
        return any(
            (isinstance(key, str) and key.casefold() in gold_field_names)
            or _contains_gold_payload_field(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_gold_payload_field(item) for item in value)
    return False


def verify_local_provider_route() -> dict[str, Any]:
    """Check only local proxy/SDK configuration; makes no API request."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise CleanEvalBlocked("GEMINI_API_KEY_MISSING")
    proxy_env: dict[str, dict[str, Any]] = {}
    for base_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        values: list[tuple[str, int]] = []
        for name in (base_name, base_name.lower()):
            raw = os.environ.get(name)
            if not raw:
                continue
            host, port, has_credentials, scheme = _safe_proxy_endpoint(raw)
            if (
                has_credentials
                or scheme != "http"
                or (host, port) != (PROXY_HOST, PROXY_PORT)
            ):
                raise CleanEvalBlocked(f"{base_name}_NOT_CLASH_7897")
            values.append((host, port))
        if not values:
            raise CleanEvalBlocked(f"{base_name}_MISSING")
        proxy_env[base_name] = {"hostname": values[0][0], "port": values[0][1]}
    if any(os.environ.get(name, "").strip() for name in ("NO_PROXY", "no_proxy")):
        raise CleanEvalBlocked("NO_PROXY_WOULD_BYPASS_CLASH")
    proxies = getproxies()
    for scheme in ("http", "https", "all"):
        host, port, has_credentials, proxy_scheme = _safe_proxy_endpoint(proxies.get(scheme))
        if (
            has_credentials
            or proxy_scheme != "http"
            or (host, port) != (PROXY_HOST, PROXY_PORT)
        ):
            raise CleanEvalBlocked(f"URLLIB_{scheme.upper()}_PROXY_MISMATCH")
    if proxies.get("no"):
        raise CleanEvalBlocked("URLLIB_NO_PROXY_WOULD_BYPASS_CLASH")
    try:
        with socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=2.0):
            local_tcp = "PASS"
    except OSError as error:
        raise CleanEvalBlocked(f"LOCAL_CLASH_UNREACHABLE_{type(error).__name__}") from None

    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini
    from tests.smoke.transport_observability import configured_route
    import httpx

    if (
        gemini.SDK_VERSION != SDK_VERSION
        or gemini.AUTOMATIC_RETRY_COUNT != 0
        or gemini.MAX_TRANSPORT_RETRIES_PER_REQUEST != 0
    ):
        raise CleanEvalBlocked("GEMINI_SDK_OR_RETRY_CONFIGURATION_MISMATCH")

    client = gemini.build_official_client(
        api_key,
        _authorization_sentinel=gemini._REAL_AUTHORIZATION_SENTINEL,
    )
    try:
        client.assert_developer_api()
        sdk_http = client._client._api_client._httpx_client
        url = httpx.URL(
            gemini.DEVELOPER_API_BASE_URL
            + "v1beta/models/"
            + MODEL
            + ":generateContent"
        )
        route = configured_route(sdk_http, url)
        if (
            route.get("route_inspection") != "PROXY"
            or route.get("configured_proxy_hostname") != PROXY_HOST
            or route.get("configured_proxy_port") != PROXY_PORT
        ):
            raise CleanEvalBlocked("GEMINI_SDK_PROXY_ROUTE_MISMATCH")
        return {
            "status": "PASS",
            "local_proxy": {"hostname": PROXY_HOST, "port": PROXY_PORT, "tcp": local_tcp},
            "environment_proxy": proxy_env,
            "urllib_route": {scheme: {"hostname": PROXY_HOST, "port": PROXY_PORT} for scheme in ("http", "https", "all")},
            "sdk_httpx_route": {
                "route_inspection": route["route_inspection"],
                "configured_proxy_hostname": route["configured_proxy_hostname"],
                "configured_proxy_port": route["configured_proxy_port"],
            },
            "model": MODEL,
            "sdk_version": gemini.SDK_VERSION,
            "provider_requests": 0,
        }
    finally:
        client.close()


def _rebuild_runtime_objects(
    cell_key: str,
    *,
    preflight_dir: Path | None = None,
    evaluation_plan_id: str | None = None,
    run_id: str | None = None,
) -> tuple[Any, dict[str, Any], list[Any], Any]:
    cell = CELLS[cell_key]
    case, manifest, frozen_plan = _validate_case_against_freeze(cell_key, cell)
    subset_manifest = (
        _read_e2_subset_manifest(allow_active_run_id=run_id)
        if evaluation_plan_id == E2_MH_ONLY_EVALUATION_PLAN_ID
        else None
    )
    if subset_manifest is not None:
        question_ids = list(subset_manifest["selected_qa_ids_in_official_order"])
        question_by_source_id = {question.question_id: question for question in case.questions}
        case = replace(case, questions=tuple(question_by_source_id[qa_id] for qa_id in question_ids))
        offline = _prepare_cell(
            cell_key,
            question_ids=question_ids,
            subset_manifest=subset_manifest,
        )
        plan_rows = offline["execution_plan"]
    else:
        offline = _prepare_cell(cell_key)
        plan_rows = frozen_plan["execution_plan"]
    flat_index = build_flat_index(case)
    memory = build_temporal_memory(case)
    prepared_by_pair: dict[tuple[str, str], Any] = {}
    try:
        question_by_id = {question.question_id: question for question in case.questions}
        for row in plan_rows:
            question = question_by_id[str(row["qa_id"])]
            method = str(row["method"])
            prepared_by_pair[(question.question_id, method)] = prepare_method_context(
                case,
                flat_index,
                question,
                method=method,
                memory=memory if method == METHOD_TEMPORAL_MEMORY else None,
            )
        expected_count = len(plan_rows)
        expected = _read_preflight(
            cell_key,
            preflight_dir=preflight_dir,
            expected_method_case_count=expected_count,
        )
        if (
            offline["runtime_execution_plan_sha256"] != expected["runtime_execution_plan_sha256"]
            or offline["implementation_fingerprints"] != expected["implementation_fingerprints"]
            or len(prepared_by_pair) != expected_count
        ):
            raise CleanEvalBlocked(f"{cell_key.upper()}_RUNTIME_REBUILD_MISMATCH")
        return case, offline, [
            prepared_by_pair[(str(row["qa_id"]), str(row["method"]))]
            for row in plan_rows
        ], memory
    except Exception:
        memory.store.close()
        raise


def _spent_upper_bound_usd(aggregate: Any) -> float:
    from decimal import Decimal

    token_estimate = float(
        Decimal(aggregate.estimated_input_tokens + aggregate.preflight_counted_input_tokens)
        * Decimal(str(INPUT_USD_PER_MILLION))
        / Decimal(1_000_000)
        + Decimal(
            aggregate.reported_billable_output_tokens
            + getattr(aggregate, "reserved_unknown_output_tokens", 0)
        )
        * Decimal(str(OUTPUT_USD_PER_MILLION))
        / Decimal(1_000_000)
    )
    return max(token_estimate, float(aggregate.reported_cost_usd or 0.0))


def _safe_exception_origin(error: BaseException) -> dict[str, Any]:
    frames = traceback.extract_tb(error.__traceback__)[-8:]
    stack: list[dict[str, Any]] = []
    for frame in frames:
        frame_path = Path(frame.filename)
        try:
            relative_path = frame_path.resolve().relative_to(REPO)
        except (OSError, ValueError):
            relative_path = Path(frame_path.name)
        module = ".".join(relative_path.with_suffix("").parts)
        stack.append(
            {
                "module": module,
                "function": frame.name,
                "line": frame.lineno,
            }
        )
    throw_site = stack[-1] if stack else {
        "module": "UNAVAILABLE",
        "function": "UNAVAILABLE",
        "line": None,
    }
    cause = error.__cause__
    if cause is None and not error.__suppress_context__:
        cause = error.__context__
    result = {
        "exception_type": type(error).__name__[:80],
        "throw_site_module": throw_site["module"],
        "throw_site_function": throw_site["function"],
        "bounded_stack_summary": stack,
        "nested_cause_type": type(cause).__name__[:80] if cause is not None else None,
    }
    if isinstance(error, CleanEvalBlocked):
        result["reason_code"] = error.reason_code
    elif isinstance(error, CleanEvalIntegrityError):
        result["reason_code"] = error.reason_code
    return result


def _safe_clean_eval_block(
    error: CleanEvalBlocked,
    guard: Any,
    *,
    logical_case_id: str | None = None,
    method: str | None = None,
    response_evidence: dict[str, Any] | None = None,
    provider_response_accepted: bool = False,
) -> dict[str, Any]:
    guard_state = _safe_guard_state(guard)
    origin = _safe_exception_origin(error)
    response_accepted = provider_response_accepted or bool(
        response_evidence and response_evidence.get("provider_response_accepted") is True
    )
    function = origin["throw_site_function"]
    if function in {"_response_text", "_persist_accepted_response_evidence"}:
        phase = "RESPONSE_VALIDATION"
    elif "persist" in function.lower() or "record_case" in function.lower():
        phase = "ARTIFACT_PERSISTENCE"
    elif function.startswith("_score") or "gold" in function.lower():
        phase = "SCORING_PRECONDITION"
    elif response_accepted:
        phase = "POST_RESPONSE"
    elif function.startswith("_verify") or "preflight" in function.lower():
        phase = "PREFLIGHT"
    else:
        phase = "PRE_RESPONSE"
    reason_code = getattr(error, "reason_code", _stable_clean_eval_reason_code(error))
    return {
        "exception_class": type(error).__name__[:80],
        "exception_type": type(error).__name__[:80],
        "guard_state": guard_state,
        "outcome_domain": _local_outcome_domain(reason_code),
        "failure_classification": "LOCAL/HARNESS BLOCK",
        "provider_outcome": "LOCAL_HARNESS_BLOCK",
        "reason_code": reason_code,
        "reason_category": reason_code.removeprefix("CLEAN_EVAL_BLOCK_").split("_", 1)[0],
        "phase": phase,
        "guard_name": origin["throw_site_function"],
        "safe_message": "A clean evaluation guard blocked this method-case.",
        "logical_case_id": logical_case_id,
        "method": method,
        "provider_response_accepted": response_accepted,
        "provider_response_already_accepted": response_accepted,
        "throw_site": {
            "module": origin["throw_site_module"],
            "function": origin["throw_site_function"],
            "line": origin["bounded_stack_summary"][-1]["line"]
            if origin["bounded_stack_summary"]
            else None,
        },
        "bounded_stack": origin["bounded_stack_summary"],
        **origin,
    }


def _failed_attempt_provider_outcome(safe_error: dict[str, Any]) -> str:
    if (
        safe_error.get("outcome_domain") == "PROVIDER"
        and safe_error.get("provider_response_accepted") is not True
    ):
        return "PROVIDER_ERROR"
    return "LOCAL_HARNESS_BLOCK"


def _safe_guard_state(guard: Any) -> str:
    candidate = getattr(guard, "last_guard_state", "CLIENT_NOT_READY") if guard is not None else "CLIENT_NOT_READY"
    if (
        isinstance(candidate, str)
        and 0 < len(candidate) <= 80
        and candidate.isascii()
        and candidate == candidate.upper()
        and all(character.isalnum() or character == "_" for character in candidate)
        and not any(marker in candidate for marker in ("SECRET", "BEARER", "API_KEY", "PASSWORD", "PROMPT", "CONTEXT"))
    ):
        return candidate
    return "UNAVAILABLE"


def _local_outcome_domain(reason_code: str) -> str:
    category = _clean_eval_reason_category(reason_code)
    if category in {"ARTIFACT", "FINGERPRINT", "GOLD"}:
        return "INTEGRITY"
    if category == "PROTOCOL":
        return "PROTOCOL"
    if category == "SCORING":
        return "SCORING"
    return "LOCAL_HARNESS"


def _is_provider_failure(error: BaseException, *, guard: Any = None) -> bool:
    if classify_retryable_failure(error, {}) is not None:
        return True
    module = type(error).__module__.casefold()
    name = type(error).__name__
    status: Any = getattr(error, "code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None) or getattr(response, "status", None)
    if (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 400 <= status <= 599
        and (module.startswith("google.") or module.startswith("httpx") or module.startswith("urllib"))
    ):
        return True
    state = _safe_guard_state(guard)
    if state == "FIRST_ATTEMPT_FAILED" and (
        module.startswith(("httpx", "httpcore", "urllib", "requests", "google."))
        or name in {"TimeoutError", "ConnectionError", "ConnectionResetError", "ConnectionAbortedError"}
    ):
        return True
    return False


def _stable_smoke_reason_code(error: BaseException) -> str:
    candidate = error.args[0] if len(error.args) == 1 else None
    if isinstance(candidate, str) and candidate in SAFE_SMOKE_REASON_CODES:
        reason = candidate
    else:
        reason = "SMOKE_BLOCKED"
    category = "RESPONSE" if reason == "PROVIDER_USAGE_UNAVAILABLE" else "HARNESS"
    return f"CLEAN_EVAL_BLOCK_{category}_{reason}"


def _exception_reason_slug(error: BaseException) -> str:
    name = type(error).__name__
    if name.endswith("Error") and len(name) > len("Error"):
        name = f"{name[:-len('Error')]}_ERROR"
    slug = "".join(
        character if character.isascii() and (character.isalnum() or character == "_") else "_"
        for character in name.upper()
    )
    return slug[:64] or "UNKNOWN_EXCEPTION"


def _safe_provider_error(
    error: BaseException,
    guard: Any,
    *,
    logical_case_id: str | None = None,
    method: str | None = None,
    response_evidence: dict[str, Any] | None = None,
    provider_response_accepted: bool = False,
) -> dict[str, Any]:
    response_accepted = provider_response_accepted or bool(
        response_evidence and response_evidence.get("provider_response_accepted") is True
    )
    if isinstance(error, CleanEvalBlocked):
        return _safe_clean_eval_block(
            error,
            guard,
            logical_case_id=logical_case_id,
            method=method,
            response_evidence=response_evidence,
            provider_response_accepted=response_accepted,
        )

    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

    origin = _safe_exception_origin(error)
    guard_state = _safe_guard_state(guard)
    exception_type = type(error).__name__[:80]
    if isinstance(error, CleanEvalIntegrityError):
        outcome_domain = "INTEGRITY"
        reason_code = error.reason_code
    elif isinstance(error, gemini.SmokeBlocked):
        outcome_domain = "LOCAL_HARNESS"
        reason_code = _stable_smoke_reason_code(error)
    elif response_accepted:
        outcome_domain = "LOCAL_HARNESS"
        reason_code = f"CLEAN_EVAL_BLOCK_LOCAL_{_exception_reason_slug(error)}"
    elif _is_provider_failure(error, guard=guard):
        outcome_domain = "PROVIDER"
        retry_classification = classify_retryable_failure(error, {})
        status_value = getattr(error, "code", None)
        if isinstance(status_value, int) and 400 <= status_value <= 599:
            reason_code = f"PROVIDER_REJECTED_HTTP_{status_value}"
        else:
            suffix = retry_classification or exception_type.upper()
            reason_code = f"PROVIDER_ERROR_{suffix[:80]}"
    else:
        outcome_domain = "LOCAL_HARNESS"
        reason_code = f"CLEAN_EVAL_BLOCK_LOCAL_{_exception_reason_slug(error)}"

    provider_outcome = "PROVIDER_ERROR" if outcome_domain == "PROVIDER" else "LOCAL_HARNESS_BLOCK"
    safe: dict[str, Any] = {
        "exception_class": exception_type,
        "exception_type": exception_type,
        "guard_state": guard_state,
        "outcome_domain": outcome_domain,
        "reason_code": reason_code,
        "reason_category": _clean_eval_reason_category(reason_code),
        "phase": "POST_RESPONSE" if response_accepted else (
            "PROVIDER_TRANSPORT" if outcome_domain == "PROVIDER" else "PRE_RESPONSE"
        ),
        "guard_name": origin["throw_site_function"],
        "failure_classification": (
            "PROVIDER_ERROR" if outcome_domain == "PROVIDER" else "LOCAL/HARNESS BLOCK"
        ),
        "provider_outcome": provider_outcome,
        "safe_message": (
            "Provider or transport failed before an accepted response."
            if outcome_domain == "PROVIDER"
            else "Local evaluation harness processing failed."
        ),
        "logical_case_id": logical_case_id,
        "method": method,
        "provider_response_accepted": response_accepted,
        "provider_response_already_accepted": response_accepted,
        "throw_site": {
            "module": origin["throw_site_module"],
            "function": origin["throw_site_function"],
            "line": origin["bounded_stack_summary"][-1]["line"]
            if origin["bounded_stack_summary"]
            else None,
        },
        "bounded_stack": origin["bounded_stack_summary"],
        **origin,
    }
    records = getattr(guard, "preflight_records", None) if guard is not None else None
    record = records[-1] if records else None
    diagnostic = record.get("failure_diagnostic") if isinstance(record, dict) else None
    if isinstance(diagnostic, dict):
        status = gemini._safe_http_status(diagnostic.get("http_status"))
        code = gemini._safe_provider_error_code(diagnostic.get("provider_error_code"))
        if status != gemini.UNAVAILABLE:
            safe["http_status"] = status
        if code != gemini.UNAVAILABLE:
            safe["provider_error_code"] = code
        failure_class = diagnostic.get("exception_class")
        if isinstance(failure_class, str) and failure_class and len(failure_class) <= 80:
            safe["count_tokens_exception_class"] = failure_class
    else:
        status = gemini._safe_http_status(getattr(error, "code", None))
        code = gemini._exception_provider_code(error)
        if status != gemini.UNAVAILABLE:
            safe["http_status"] = status
        if code != gemini.UNAVAILABLE:
            safe["provider_error_code"] = code
    return safe


def _safe_run_level_failure(error: BaseException) -> dict[str, Any]:
    safe = _safe_provider_error(error, None, provider_response_accepted=False)
    if safe.get("outcome_domain") == "PROVIDER":
        safe["outcome_domain"] = "LOCAL_HARNESS"
        safe["provider_outcome"] = "LOCAL_HARNESS_BLOCK"
        safe["failure_classification"] = "LOCAL/HARNESS BLOCK"
        safe["reason_code"] = f"CLEAN_EVAL_BLOCK_LOCAL_{_exception_reason_slug(error)}"
        safe["reason_category"] = "LOCAL_HARNESS"
        safe["safe_message"] = "Local run-level processing failed."
    safe["phase"] = "RUN_LEVEL"
    return safe


def _count_tokens_telemetry(
    record: dict[str, Any],
    *,
    fallback_state: Any,
    gemini: Any,
    allow_failure: bool = False,
) -> dict[str, Any]:
    status = record.get("count_tokens_status")
    source = record.get("token_estimation_source")
    if (
        record.get("status") == "success"
        and status == "PASS"
        and source == "PROVIDER_COUNT_TOKENS"
        and record.get("billing_preflight_uncertainty") is False
    ):
        return {
            "count_tokens_outcome": "SUCCESS",
            "count_tokens_status": status,
            "count_tokens_token_estimation_source": source,
            "count_tokens_fallback_reason": None,
            "static_conservative_input_token_estimate": None,
            "billing_preflight_uncertainty": False,
            "count_tokens_transport_attempts": record.get("transport_attempts", 0),
        }

    failure_class = record.get("failure_class") or getattr(fallback_state, "failure_class", None)
    reason = record.get("fallback_reason") or getattr(fallback_state, "reason", None)
    diagnostic = record.get("failure_diagnostic")
    safe_diagnostic = (
        gemini._persistable_count_tokens_failure_diagnostic(diagnostic)
        if isinstance(diagnostic, dict)
        else None
    )
    if record.get("status") == "static_fallback":
        exception_class = (
            failure_class.removeprefix("TRANSPORT_")
            if isinstance(failure_class, str) and failure_class.startswith("TRANSPORT_")
            else None
        )
        estimate = record.get("estimated_input_token_upper_bound")
        expected_reason = (
            f"TRANSPORT_{exception_class}_COUNT_TOKENS_UNAVAILABLE"
            if exception_class is not None
            else None
        )
        if (
            status != "UNAVAILABLE"
            or source != "STATIC_CONSERVATIVE"
            or record.get("billing_preflight_uncertainty") is not True
            or not gemini._matches_safe_count_tokens_transport_failure(
                diagnostic
                if isinstance(diagnostic, dict)
                else {
                    "exception_class": exception_class,
                    "http_status": gemini.UNAVAILABLE,
                    "provider_error_code": gemini.UNAVAILABLE,
                }
            )
            or failure_class != f"TRANSPORT_{exception_class}"
            or reason != expected_reason
            or not getattr(fallback_state, "unavailable", False)
            or getattr(fallback_state, "failure_class", None) != failure_class
            or getattr(fallback_state, "reason", None) != reason
            or isinstance(estimate, bool)
            or not isinstance(estimate, int)
            or estimate < 0
            or estimate > INPUT_TOKEN_CAP
        ):
            raise CleanEvalBlocked("COUNT_TOKENS_FALLBACK_POLICY_MISMATCH")
        outcome = "TRANSIENT_FAILURE_FALLBACK" if diagnostic is not None else "RUN_SCOPED_FALLBACK"
        return {
            "count_tokens_outcome": outcome,
            "count_tokens_status": status,
            "count_tokens_token_estimation_source": source,
            "count_tokens_fallback_reason": reason,
            "static_conservative_input_token_estimate": estimate,
            "billing_preflight_uncertainty": True,
            "count_tokens_transport_attempts": record.get("transport_attempts", 0),
            "count_tokens_failure_diagnostic": safe_diagnostic,
        }

    if allow_failure:
        return {
            "count_tokens_outcome": "FAILED",
            "count_tokens_status": status or "UNAVAILABLE",
            "count_tokens_token_estimation_source": source or "UNAVAILABLE",
            "count_tokens_fallback_reason": None,
            "static_conservative_input_token_estimate": None,
            "billing_preflight_uncertainty": False,
            "count_tokens_transport_attempts": record.get("transport_attempts", 0),
            "count_tokens_failure_diagnostic": safe_diagnostic,
        }
    raise CleanEvalBlocked("COUNT_TOKENS_PREFLIGHT_POLICY_MISMATCH")


def _record_case_result(root: Path, result: dict[str, Any]) -> None:
    sequence = result.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise CleanEvalIntegrityError("method-case sequence is invalid")
    path = root / "cases" / f"{sequence:03d}.json"
    _json_write(path, result)
    _seal_file(path, identity=f"method-case:{sequence}")


def _read_case_result(root: Path, sequence: int) -> dict[str, Any] | None:
    path = root / "cases" / f"{sequence:03d}.json"
    if not path.is_file():
        return None
    if not _verify_seal(path, identity=f"method-case:{sequence}"):
        raise CleanEvalBlocked(f"METHOD_CASE_SEAL_INVALID_{sequence}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise CleanEvalBlocked(f"METHOD_CASE_OUTPUT_UNREADABLE_{sequence}") from None
    return payload if isinstance(payload, dict) else None


def _append_attempt_journal(path: Path, attempt: dict[str, Any]) -> None:
    _append_jsonl_fsync(path, attempt)


def _read_attempt_journal(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CleanEvalBlocked("JOURNAL_PERSISTENCE_FAILURE") from None
    events: list[dict[str, Any]] = []
    try:
        for line in lines:
            if not line:
                raise ValueError
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError
            events.append(event)
    except (TypeError, ValueError):
        raise CleanEvalBlocked("JOURNAL_PERSISTENCE_FAILURE") from None
    return events


def _journal_usage_status(event: Mapping[str, Any]) -> str:
    explicit = event.get("usage_status")
    if explicit in {"COMPLETE", "INCOMPLETE", "INVALID"}:
        return explicit
    usage = event.get("usage_metadata")
    return (
        "COMPLETE"
        if isinstance(usage, dict)
        and all(
            isinstance(usage.get(name), int)
            and not isinstance(usage.get(name), bool)
            and usage.get(name) >= 0
            for name in ("input_tokens", "output_tokens")
        )
        else "INCOMPLETE"
    )


def _journal_usage_is_valid(event: Mapping[str, Any]) -> bool:
    usage = event.get("usage_metadata")
    if usage is None:
        usage = {}
    if not isinstance(usage, dict) or set(usage) - {"input_tokens", "output_tokens", "thinking_tokens"}:
        return False
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        )
        for value in usage.values()
    ):
        return False
    complete_fields_present = all(
        isinstance(usage.get(name), int)
        and not isinstance(usage.get(name), bool)
        and usage.get(name) >= 0
        for name in ("input_tokens", "output_tokens")
    )
    status = _journal_usage_status(event)
    explicit_status = event.get("usage_status")
    if explicit_status is not None and explicit_status not in {
        "COMPLETE",
        "INCOMPLETE",
        "INVALID",
    }:
        return False
    usage_present_matches = event.get("usage_present") is any(
        value is not None for value in usage.values()
    )
    billing_uncertainty_present = "billing_uncertainty" in event
    billing_uncertainty_matches = (
        event.get("billing_uncertainty") is (status != "COMPLETE")
        if billing_uncertainty_present
        else explicit_status is None
    )
    invalid_fields = event.get("usage_invalid_fields", [])
    missing_fields = event.get("usage_missing_fields")
    expected_missing_fields = sorted(
        field
        for field in ("input_tokens", "output_tokens", "thinking_tokens")
        if usage.get(field) is None
    )
    if (
        not isinstance(invalid_fields, list)
        or any(field not in _RESPONSE_USAGE_ALIASES for field in invalid_fields)
        or (
            missing_fields is not None
            and (
                not isinstance(missing_fields, list)
                or missing_fields != expected_missing_fields
            )
        )
    ):
        return False
    return (
        status in {"COMPLETE", "INCOMPLETE", "INVALID"}
        and usage_present_matches
        and (status != "COMPLETE" or complete_fields_present)
        and (
            status != "INCOMPLETE"
            or not complete_fields_present
            or bool(invalid_fields)
        )
        and (status != "INVALID" or isinstance(invalid_fields, list) and bool(invalid_fields))
        and (status == "INVALID" or not invalid_fields)
        and billing_uncertainty_matches
        and isinstance(event.get("usage_present"), bool)
    )


def _validate_attempt_journal_events(
    events: list[dict[str, Any]],
    *,
    expected_case_count: int = 200,
    scoring_required: bool = True,
) -> dict[str, Any]:
    started_cases: dict[int, dict[str, Any]] = {}
    completed_cases: set[int] = set()
    aborted_cases: set[int] = set()
    blocked_cases: set[int] = set()
    scored_cases: set[int] = set()
    attempt_starts: dict[tuple[int, int], dict[str, Any]] = {}
    attempt_completions: set[tuple[int, int]] = set()
    accepted_attempts: dict[tuple[int, int], dict[str, Any]] = {}
    response_evidence_persisted: dict[tuple[int, int], dict[str, Any]] = {}
    usage_validations: dict[tuple[int, int], dict[str, Any]] = {}
    accepted_response_cases: set[int] = set()
    active_attempts: dict[int, int] = {}
    errors: list[str] = []
    for event in events:
        kind = event.get("event")
        sequence = event.get("sequence")
        if kind in {
            "CASE_METHOD_STARTED",
            "PROVIDER_ATTEMPT_STARTED",
            "RESPONSE_RECEIVED",
            "RESPONSE_EVIDENCE_PERSISTED",
            "USAGE_VALIDATED",
            "PROVIDER_RESPONSE_ACCEPTED",
            "PROVIDER_ATTEMPT_COMPLETED",
            "CASE_METHOD_COMPLETED",
            "CASE_METHOD_BLOCKED",
            "CASE_METHOD_ABORTED",
            "CASE_METHOD_SCORED",
        }:
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                errors.append("INVALID_SEQUENCE")
                continue
        if kind == "CASE_METHOD_STARTED":
            if sequence in started_cases:
                errors.append("DUPLICATE_CASE_START")
            else:
                started_cases[sequence] = event
        elif kind in {
            "PROVIDER_ATTEMPT_STARTED",
            "RESPONSE_RECEIVED",
            "RESPONSE_EVIDENCE_PERSISTED",
            "USAGE_VALIDATED",
            "PROVIDER_RESPONSE_ACCEPTED",
            "PROVIDER_ATTEMPT_COMPLETED",
        }:
            attempt_no = event.get("attempt_no")
            if isinstance(attempt_no, bool) or not isinstance(attempt_no, int) or not 1 <= attempt_no <= MAX_ATTEMPTS_PER_LOGICAL_GENERATION:
                errors.append("INVALID_ATTEMPT_NUMBER")
                continue
            attempt_key = (sequence, attempt_no)
            if sequence not in started_cases or sequence in completed_cases or sequence in aborted_cases:
                errors.append("ATTEMPT_OUTSIDE_CASE_LIFECYCLE")
                continue
            if kind == "PROVIDER_ATTEMPT_STARTED":
                if sequence in accepted_response_cases:
                    errors.append("PROVIDER_ATTEMPT_STARTED_AFTER_ACCEPTED_RESPONSE")
                previous_attempts = sum(key[0] == sequence for key in attempt_starts)
                if attempt_key in attempt_starts or sequence in active_attempts or attempt_no != previous_attempts + 1:
                    errors.append("PROVIDER_ATTEMPT_START_ORDER_INVALID")
                else:
                    attempt_starts[attempt_key] = event
                    active_attempts[sequence] = attempt_no
            elif kind in {"RESPONSE_RECEIVED", "PROVIDER_RESPONSE_ACCEPTED"}:
                start = attempt_starts.get(attempt_key)
                request_hash = event.get("request_sha256")
                response_hash = event.get("response_sha256")
                if (
                    active_attempts.get(sequence) != attempt_no
                    or attempt_key not in attempt_starts
                    or attempt_key in accepted_attempts
                    or event.get("provider_response_accepted") is not True
                    or not isinstance(start, dict)
                    or event.get("attempt_id") != start.get("attempt_id")
                    or request_hash != start.get("generation_request_sha256")
                    or not _is_sha256_hex(request_hash)
                    or not isinstance(event.get("model"), str)
                    or not event.get("model")
                    or not _journal_usage_is_valid(event)
                ):
                    errors.append("RESPONSE_RECEIPT_INVALID")
                else:
                    if not _is_sha256_hex(response_hash):
                        errors.append("RESPONSE_RECEIPT_HASH_INVALID")
                    accepted_attempts[attempt_key] = event
                    accepted_response_cases.add(sequence)
            elif kind == "RESPONSE_EVIDENCE_PERSISTED":
                receipt = accepted_attempts.get(attempt_key)
                if (
                    active_attempts.get(sequence) != attempt_no
                    or attempt_key not in attempt_starts
                    or attempt_key in response_evidence_persisted
                    or not isinstance(receipt, dict)
                    or event.get("attempt_id") != receipt.get("attempt_id")
                    or event.get("request_sha256") != receipt.get("request_sha256")
                    or event.get("response_sha256") != receipt.get("response_sha256")
                    or not _is_sha256_hex(event.get("evidence_sha256"))
                ):
                    errors.append("RESPONSE_EVIDENCE_PERSISTENCE_EVENT_INVALID")
                else:
                    response_evidence_persisted[attempt_key] = event
            elif kind == "USAGE_VALIDATED":
                receipt = accepted_attempts.get(attempt_key)
                persisted = response_evidence_persisted.get(attempt_key)
                if (
                    active_attempts.get(sequence) != attempt_no
                    or attempt_key not in attempt_starts
                    or attempt_key in usage_validations
                    or not isinstance(receipt, dict)
                    or not isinstance(persisted, dict)
                    or event.get("attempt_id") != receipt.get("attempt_id")
                    or event.get("request_sha256") != receipt.get("request_sha256")
                    or event.get("response_sha256") != receipt.get("response_sha256")
                    or event.get("usage_status") != receipt.get("usage_status")
                    or event.get("usage_metadata") != receipt.get("usage_metadata")
                    or event.get("usage_missing_fields") != receipt.get("usage_missing_fields")
                    or event.get("usage_sources") != receipt.get("usage_sources")
                    or event.get("usage_access_failures")
                    != receipt.get("usage_access_failures")
                    or event.get("billing_uncertainty") != receipt.get("billing_uncertainty")
                    or not _journal_usage_is_valid(event)
                ):
                    errors.append("RESPONSE_USAGE_VALIDATION_EVENT_INVALID")
                else:
                    usage_validations[attempt_key] = event
            else:
                if active_attempts.get(sequence) != attempt_no or attempt_key not in attempt_starts or attempt_key in attempt_completions:
                    errors.append("PROVIDER_ATTEMPT_COMPLETION_UNPAIRED")
                else:
                    accepted = attempt_key in accepted_attempts
                    completion_acceptance = event.get("provider_response_accepted") is True
                    if accepted and not completion_acceptance:
                        errors.append("PROVIDER_RESPONSE_ACCEPTANCE_REGRESSION")
                    if completion_acceptance and not accepted:
                        errors.append("ACCEPTED_RESPONSE_EVENT_MISSING")
                    if event.get("provider_outcome") == "PROVIDER_ERROR" and (accepted or completion_acceptance):
                        errors.append("ACCEPTED_RESPONSE_CLASSIFIED_AS_PROVIDER_ERROR")
                    if event.get("provider_outcome") == "PROVIDER_COMPLETE" and not accepted:
                        errors.append("PROVIDER_COMPLETION_WITHOUT_ACCEPTED_RESPONSE")
                    if accepted and event.get("provider_outcome") == "PROVIDER_COMPLETE":
                        accepted_event = accepted_attempts[attempt_key]
                        if (
                            accepted_event.get("event") == "RESPONSE_RECEIVED"
                            and (
                                attempt_key not in response_evidence_persisted
                                or attempt_key not in usage_validations
                            )
                        ):
                            errors.append("PROVIDER_COMPLETION_BEFORE_USAGE_VALIDATION")
                        if event.get("generation_request_sha256") != accepted_event.get("request_sha256"):
                            errors.append("PROVIDER_COMPLETION_REQUEST_HASH_MISMATCH")
                        if event.get("response_sha256") != accepted_event.get("response_sha256"):
                            errors.append("PROVIDER_COMPLETION_RESPONSE_HASH_MISMATCH")
                        usage = accepted_event.get("usage_metadata")
                        if (
                            not _journal_usage_is_valid(accepted_event)
                            or _journal_usage_status(accepted_event) not in {"COMPLETE", "INCOMPLETE"}
                            or event.get("usage_status") != _journal_usage_status(accepted_event)
                            or event.get("usage_missing_fields") != accepted_event.get("usage_missing_fields")
                            or event.get("billing_uncertainty")
                            != (_journal_usage_status(accepted_event) != "COMPLETE")
                            or (
                                _journal_usage_status(accepted_event) == "INCOMPLETE"
                                and event.get("billing_basis") != "RESERVED_ATTEMPT_UPPER_BOUND"
                            )
                        ):
                            errors.append("PROVIDER_COMPLETION_USAGE_MISMATCH")
                        else:
                            for completion_field, usage_field in (
                                ("provider_input_tokens", "input_tokens"),
                                ("provider_output_tokens", "output_tokens"),
                                ("provider_thinking_tokens", "thinking_tokens"),
                            ):
                                reported = event.get(completion_field)
                                expected = usage.get(usage_field)
                                if (
                                    reported != expected
                                    or (
                                        reported is not None
                                        and (
                                            isinstance(reported, bool)
                                            or not isinstance(reported, int)
                                            or reported < 0
                                        )
                                    )
                                ):
                                    errors.append("PROVIDER_COMPLETION_USAGE_MISMATCH")
                                    break
                    attempt_completions.add(attempt_key)
                    active_attempts.pop(sequence, None)
        elif kind == "CASE_METHOD_COMPLETED":
            if (
                sequence not in started_cases
                or sequence in completed_cases
                or sequence in blocked_cases
                or sequence in aborted_cases
                or sequence in active_attempts
                or not any(key[0] == sequence for key in attempt_completions)
            ):
                errors.append("CASE_COMPLETION_UNPAIRED")
            else:
                completed_cases.add(sequence)
        elif kind == "CASE_METHOD_BLOCKED":
            if (
                sequence not in started_cases
                or sequence in completed_cases
                or sequence in blocked_cases
                or sequence in aborted_cases
                or sequence in active_attempts
            ):
                errors.append("CASE_BLOCK_UNPAIRED")
            else:
                blocked_cases.add(sequence)
        elif kind == "CASE_METHOD_ABORTED":
            if (
                sequence not in started_cases
                or sequence in completed_cases
                or sequence in blocked_cases
                or sequence in aborted_cases
                or sequence in active_attempts
            ):
                errors.append("CASE_ABORT_UNPAIRED")
            else:
                aborted_cases.add(sequence)
        elif kind == "CASE_METHOD_SCORED":
            if sequence not in (completed_cases | blocked_cases) or sequence in scored_cases:
                errors.append("CASE_SCORE_UNPAIRED")
            else:
                scored_cases.add(sequence)
        else:
            errors.append("UNKNOWN_JOURNAL_EVENT")

    for attempt_key, receipt in accepted_attempts.items():
        if receipt.get("event") == "RESPONSE_RECEIVED":
            if attempt_key not in response_evidence_persisted:
                errors.append("RESPONSE_EVIDENCE_PERSISTENCE_EVENT_MISSING")
            if attempt_key not in usage_validations:
                errors.append("RESPONSE_USAGE_VALIDATION_EVENT_MISSING")
    if active_attempts:
        errors.append("PROVIDER_ATTEMPT_LEFT_OPEN")
    if attempt_starts.keys() != attempt_completions:
        errors.append("PROVIDER_ATTEMPT_EVENT_MISMATCH")
    last_event = events[-1] if events else None
    last_event_summary = None
    if isinstance(last_event, dict):
        last_event_summary = {
            key: last_event.get(key)
            for key in ("event", "sequence", "qa_id", "method", "attempt_no", "provider_outcome")
            if key in last_event
        }
    lifecycles_closed = (
        not errors
        and not ((completed_cases | blocked_cases) & aborted_cases)
        and len(started_cases) == len(completed_cases | blocked_cases | aborted_cases)
    )
    return {
        "integrity_valid": not errors,
        "method_case_lifecycles_closed": lifecycles_closed,
        "protocol_complete": (
            lifecycles_closed
            and len(started_cases) == expected_case_count
            and len(completed_cases | blocked_cases) == expected_case_count
            and len(scored_cases) == expected_case_count
            if scoring_required
            else lifecycles_closed
            and len(started_cases) == expected_case_count
            and len(completed_cases | blocked_cases | aborted_cases) == expected_case_count
            and not scored_cases
        ),
        "started_method_cases": len(started_cases),
        "completed_method_cases": len(completed_cases),
        "blocked_method_cases": len(blocked_cases),
        "aborted_method_cases": len(aborted_cases),
        "terminal_method_cases": len(completed_cases | blocked_cases),
        "scored_method_cases": len(scored_cases),
        "provider_attempt_starts": len(attempt_starts),
        "provider_attempt_completions": len(attempt_completions),
        "provider_response_acceptances": len(accepted_attempts),
        "unmatched_method_cases": sorted(set(started_cases) - completed_cases - blocked_cases - aborted_cases),
        "errors": sorted(set(errors)),
        "last_durable_journal_event": last_event_summary,
    }


def _count_tokens_run_summary(events: list[dict[str, Any]], request_count: int) -> dict[str, Any]:
    completed_attempts = [
        event for event in events
        if event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    ]
    outcomes: dict[str, int] = {}
    fallback_reasons: dict[str, int] = {}
    static_estimates: list[int] = []
    for event in completed_attempts:
        outcome = event.get("count_tokens_outcome")
        if isinstance(outcome, str):
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        reason = event.get("count_tokens_fallback_reason")
        if isinstance(reason, str):
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
        estimate = event.get("static_conservative_input_token_estimate")
        if isinstance(estimate, int) and not isinstance(estimate, bool):
            static_estimates.append(estimate)
    return {
        "provider_count_tokens_requests": request_count,
        "provider_attempts_with_count_tokens_outcome": len(completed_attempts),
        "outcome_counts": outcomes,
        "fallback_reason_counts": fallback_reasons,
        "static_conservative_estimates_per_attempt": static_estimates,
        "billing_preflight_uncertainty_attempt_count": sum(
            event.get("billing_preflight_uncertainty") is True
            for event in completed_attempts
        ),
    }


def _safe_exception_traceback(error: BaseException) -> dict[str, Any]:
    """Return bounded frame metadata without exception text or source lines."""

    return _safe_exception_origin(error)


def _prepare_generation_call(
    *,
    run_id: str,
    workspace_id: str,
    question: str,
    request: dict[str, Any],
    attempt_no: int,
    aggregate: Any,
    aggregate_budget: Any,
    count_tokens_fallback_state: Any,
    on_response_accepted: Callable[[Any], None] | None = None,
) -> tuple[Any, Any]:
    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

    case_id = f"{run_id}:attempt:{uuid4().hex[:12]}"
    smoke_case = gemini.SmokeCase(
        case_id=case_id,
        workspace_id=workspace_id,
        user_question=question,
        budget_id=f"public-memory:{run_id}",
    )
    per_case_budget = gemini.CaseBudget(
        case_id=case_id,
        max_provider_requests=1,
        max_model_turns=1,
        max_input_tokens=INPUT_TOKEN_CAP,
        max_output_tokens=OUTPUT_TOKEN_CAP,
        max_elapsed_provider_seconds=REQUEST_TIMEOUT_SECONDS,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        per_request_output_tokens=OUTPUT_TOKEN_CAP,
        automatic_retry_count=0,
        max_preflight_requests=1,
        max_preflight_counted_input_tokens=INPUT_TOKEN_CAP,
        max_elapsed_preflight_seconds=REQUEST_TIMEOUT_SECONDS,
        per_request_input_tokens=INPUT_TOKEN_CAP,
    )
    client = gemini.build_official_client(
        os.environ["GEMINI_API_KEY"],
        _authorization_sentinel=gemini._REAL_AUTHORIZATION_SENTINEL,
    )
    guard = gemini.BudgetedGeminiClient(
        client,
        case=smoke_case,
        case_budget=per_case_budget,
        aggregate=aggregate,
        aggregate_budget=aggregate_budget,
        allow_static_count_tokens_fallback=True,
        count_tokens_fallback_state=count_tokens_fallback_state,
        count_tokens_transport_fallback_only=True,
        on_response_accepted=on_response_accepted,
    )
    from tests.smoke.transport_observability import configured_route
    import httpx

    sdk_route = configured_route(
        client._client._api_client._httpx_client,
        httpx.URL(
            gemini.DEVELOPER_API_BASE_URL
            + "v1beta/models/"
            + MODEL
            + ":generateContent"
        ),
    )
    if (
        sdk_route.get("route_inspection") != "PROXY"
        or sdk_route.get("configured_proxy_hostname") != PROXY_HOST
        or sdk_route.get("configured_proxy_port") != PROXY_PORT
    ):
        client.close()
        raise CleanEvalBlocked("GEMINI_SDK_PROXY_ROUTE_CHANGED")
    if request.get("model") != MODEL:
        client.close()
        raise CleanEvalBlocked("GEMINI_MODEL_DRIFT")
    request_config = request.get("config")
    if not isinstance(request_config, dict) or request_config.get("max_output_tokens") != OUTPUT_TOKEN_CAP:
        client.close()
        raise CleanEvalBlocked("GEMINI_OUTPUT_CAP_DRIFT")
    if attempt_no > MAX_ATTEMPTS_PER_LOGICAL_GENERATION:
        client.close()
        raise CleanEvalBlocked("LOGICAL_GENERATION_ATTEMPT_LIMIT_EXCEEDED")
    gemini._assert_gold_free(request)
    return guard, client


def _response_text(response: Any) -> str:
    value = getattr(response, "text", None)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CleanEvalBlocked("GENERATION_RESPONSE_TEXT_MALFORMED")
    return value


def _load_gold_answers_after_seal(
    source: str,
    question_ids: list[str],
    *,
    provider_outputs_path: Path,
    diagnostic_mode: bool = False,
) -> dict[str, tuple[str, ...]]:
    if diagnostic_mode:
        raise CleanEvalBlocked("DIAGNOSTIC_GOLD_ACCESS_VIOLATION")
    if not _verify_seal(provider_outputs_path, identity=f"provider-outputs:{source}"):
        raise CleanEvalBlocked("GOLD_READ_BEFORE_PROVIDER_OUTPUT_SEAL")
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise CleanEvalBlocked("GOLD_SCORER_REQUIRES_PYARROW") from error
    table = parquet.read_table(
        str(DATASET_PATH),
        columns=["answers", "metadata.qa_pair_ids", "metadata.source"],
    )
    rows = [row for row in table.to_pylist() if row.get("source") == source]
    if len(rows) != 1:
        raise CleanEvalBlocked("OFFICIAL_GOLD_ROW_MISSING_OR_AMBIGUOUS")
    row = rows[0]
    ids, answers = row.get("qa_pair_ids"), row.get("answers")
    if not isinstance(ids, list) or not isinstance(answers, list) or len(ids) != len(answers):
        raise CleanEvalBlocked("OFFICIAL_GOLD_SCHEMA_INVALID")
    by_id: dict[str, tuple[str, ...]] = {}
    for question_id, variants in zip(ids, answers, strict=True):
        if (
            not isinstance(question_id, str)
            or not isinstance(variants, list)
            or not variants
            or any(not isinstance(value, str) or not value.strip() for value in variants)
        ):
            raise CleanEvalBlocked("OFFICIAL_GOLD_VALUE_SCHEMA_INVALID")
        by_id[question_id] = tuple(variants)
    missing = [question_id for question_id in question_ids if question_id not in by_id]
    if missing:
        raise CleanEvalBlocked("OFFICIAL_GOLD_QUESTION_ID_MISSING")
    return {question_id: by_id[question_id] for question_id in question_ids}


def _fact_supports_gold(fact: Any, gold_answers: tuple[str, ...]) -> bool:
    normalized_statement = normalize_official_answer(fact.statement)
    return any(
        normalize_official_answer(answer) in normalized_statement
        for answer in gold_answers
    )


def _classify_failed_method_case(
    *,
    method: str,
    output: dict[str, Any],
    plan: dict[str, Any],
    facts_by_id: dict[str, Any],
    gold_answers: tuple[str, ...],
) -> str:
    if output.get("provider_outcome") == "LOCAL_HARNESS_BLOCK":
        return "HARNESS_GUARD_BLOCK"
    if output.get("provider_outcome") == "PROVIDER_ERROR":
        return "PROVIDER_ERROR"
    if output.get("provider_outcome") != "PROVIDER_COMPLETE":
        return "OTHER"
    selected_ids = plan.get("context_evidence_ids_selected", [])
    retrieved_ids = plan.get("retrieval_evidence_ids", [])
    selected_support = any(
        _fact_supports_gold(facts_by_id[fact_id], gold_answers)
        for fact_id in selected_ids
        if fact_id in facts_by_id
    )
    if method == METHOD_FLAT_RETRIEVAL:
        if selected_support:
            return "READER_ERROR"
        retrieved_support = any(
            _fact_supports_gold(facts_by_id[fact_id], gold_answers)
            for fact_id in retrieved_ids
            if fact_id in facts_by_id
        )
        if retrieved_support:
            return "CONTEXT_ASSEMBLY_DROP"
        if any(_fact_supports_gold(fact, gold_answers) for fact in facts_by_id.values()):
            return "RETRIEVAL_MISS"
        return "OTHER"

    source_ids = plan.get("memory_source_evidence_ids", [])
    if output.get("memory_lookup_status") == "MISS" or not source_ids:
        raw_matches = [
            fact for fact in facts_by_id.values()
            if _fact_supports_gold(fact, gold_answers)
        ]
        if any(fact.subject_key is None or fact.value is None for fact in raw_matches):
            return "MEMORY_CONSTRUCTION_MISS"
        if raw_matches:
            return "LOOKUP_ROUTING_MISS"
        return "OTHER"
    selected_fact = facts_by_id.get(source_ids[0])
    if selected_fact is None:
        return "OTHER"
    latest_fact = max(
        (
            fact for fact in facts_by_id.values()
            if fact.subject_key == selected_fact.subject_key
        ),
        key=lambda fact: fact.ordinal,
        default=selected_fact,
    )
    if selected_fact.ordinal < latest_fact.ordinal:
        return "STALE_FACT_SELECTED"
    if _fact_supports_gold(selected_fact, gold_answers):
        return "READER_ERROR"
    same_key_support = [
        fact for fact in facts_by_id.values()
        if fact.subject_key == selected_fact.subject_key and _fact_supports_gold(fact, gold_answers)
    ]
    if same_key_support:
        return "SUPERSESSION_ERROR"
    if any(_fact_supports_gold(fact, gold_answers) for fact in facts_by_id.values()):
        return "LOOKUP_ROUTING_MISS"
    return "OTHER"


def _score_sealed_run(
    *,
    root: Path,
    cell_key: str,
    case: Any,
    preflight: dict[str, Any],
    provider_outputs: list[dict[str, Any]],
    provider_outputs_path: Path,
    fingerprints: dict[str, Any],
    diagnostic_mode: bool = False,
    evaluation_plan_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if diagnostic_mode:
        raise CleanEvalBlocked("DIAGNOSTIC_SCORING_VIOLATION")
    from benchmarks.memoryagentbench.public_memory_profile import METRIC_SOURCE_URL

    current_fingerprints = implementation_fingerprints()
    if current_fingerprints != fingerprints:
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH_BEFORE_SCORING")
    successful_count = sum(row.get("provider_outcome") == "PROVIDER_COMPLETE" for row in provider_outputs)
    completion_rate = successful_count / max(1, len(provider_outputs))
    question_ids = [question.question_id for question in case.questions]
    score_unlock_gate = _score_protocol_eligibility(
        provider_outputs,
        question_ids,
        **(
            {}
            if evaluation_plan_id in {None, E2_MH_ONLY_EVALUATION_PLAN_ID}
            else {
                "minimum_scored_coverage": 0.90,
                "minimum_paired_coverage": 0.90,
                "maximum_case_local_blocks": 2,
            }
        ),
    )
    if not score_unlock_gate["eligible_for_gold_unlock"]:
        raise CleanEvalBlocked("SCORE_PROTOCOL_COVERAGE_GATE_FAILED")
    if completion_rate < 0.90:
        raise CleanEvalBlocked("PROVIDER_COMPLETION_BELOW_90_PERCENT")
    gold_by_id = _load_gold_answers_after_seal(
        CELLS[cell_key].source,
        question_ids,
        provider_outputs_path=provider_outputs_path,
    )
    facts_by_id = {fact.fact_id: fact for fact in case.facts}
    plan_by_pair = {
        (row["qa_id"], row["method"]): row
        for row in preflight["execution_plan"]
    }
    scored: list[dict[str, Any]] = []
    outputs_by_pair = {
        (row["qa_id"], row["method"]): row
        for row in provider_outputs
    }
    for qa_id in question_ids:
        for method in METHODS:
            output = outputs_by_pair[(qa_id, method)]
            gold_answers = gold_by_id[qa_id]
            score: bool | None = None
            taxonomy: str | None = None
            if output.get("provider_outcome") == "PROVIDER_COMPLETE":
                try:
                    score = score_official_substring_exact_match(
                        str(output.get("response_text", "")),
                        gold_answers,
                    )
                except (TypeError, ValueError):
                    taxonomy = "SCORING_AMBIGUITY"
                if score is False:
                    taxonomy = _classify_failed_method_case(
                        method=method,
                        output=output,
                        plan=plan_by_pair[(qa_id, method)],
                        facts_by_id=facts_by_id,
                        gold_answers=gold_answers,
                    )
            else:
                taxonomy = (
                    "HARNESS_GUARD_BLOCK"
                    if output.get("provider_outcome") == "LOCAL_HARNESS_BLOCK"
                    else "PROVIDER_ERROR"
                )
            row = {
                "qa_id": qa_id,
                "method": method,
                "provider_outcome": output.get("provider_outcome"),
                "score": score,
                "failure_taxonomy": taxonomy,
                "context_evidence_ids_selected": plan_by_pair[(qa_id, method)]["context_evidence_ids_selected"],
                "retrieval_evidence_ids": plan_by_pair[(qa_id, method)]["retrieval_evidence_ids"],
                "selected_context_tokens_estimated": output.get("context_tokens_estimated"),
                "selected_source_evidence_ids": output.get("selected_source_evidence_ids", []),
                "memory_lookup_status": output.get("memory_lookup_status"),
                "provider_attempts": output.get("provider_attempts", 0),
                "official_metric": "substring_exact_match",
            }
            scored.append(row)
            _append_attempt_journal(
                root / "provider_attempt_journal.jsonl",
                {
                    "event": "CASE_METHOD_SCORED",
                    "run_id": root.name,
                    "sequence": plan_by_pair[(qa_id, method)]["sequence"],
                    "qa_id": qa_id,
                    "method": method,
                    "score": score,
                    "failure_taxonomy": taxonomy,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                },
            )

    method_metrics: dict[str, Any] = {}
    for method in METHODS:
        rows = [row for row in scored if row["method"] == method]
        completed = [row for row in rows if row["score"] is not None]
        fixed_denominator_metrics = _score_outcome_metrics(
            rows,
            planned_denominator=len(question_ids),
        )
        correct = sum(row["score"] is True for row in completed)
        method_metrics[method] = {
            **fixed_denominator_metrics,
            "scored": len(completed),
            "completed": len(completed),
            "planned": len(rows),
            "accuracy_among_completed": correct / len(completed) if completed else None,
            "completion_rate": len(completed) / len(rows) if rows else 0.0,
            "mean_selected_context_tokens_estimated": (
                sum(int(row["selected_context_tokens_estimated"] or 0) for row in completed)
                / len(completed)
                if completed
                else None
            ),
        }
    by_pair = {(row["qa_id"], row["method"]): row for row in scored}
    paired = [
        qa_id for qa_id in question_ids
        if all(by_pair[(qa_id, method)]["score"] is not None for method in METHODS)
    ]
    paired_flat_correct = sum(by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["score"] is True for qa_id in paired)
    paired_temporal_correct = sum(by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["score"] is True for qa_id in paired)
    both_correct = sum(
        by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["score"] is True
        and by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["score"] is True
        for qa_id in paired
    )
    flat_only = sum(
        by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["score"] is True
        and by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["score"] is False
        for qa_id in paired
    )
    temporal_only = sum(
        by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["score"] is False
        and by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["score"] is True
        for qa_id in paired
    )
    both_incorrect = sum(
        by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["score"] is False
        and by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["score"] is False
        for qa_id in paired
    )
    if both_correct + flat_only + temporal_only + both_incorrect != len(paired):
        raise CleanEvalBlocked("PAIRED_SCORE_CATEGORIES_INCOMPLETE")
    unpaired = [
        {
            "qa_id": qa_id,
            "flat_provider_outcome": by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["provider_outcome"],
            "temporal_provider_outcome": by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["provider_outcome"],
            "flat_score": by_pair[(qa_id, METHOD_FLAT_RETRIEVAL)]["score"],
            "temporal_score": by_pair[(qa_id, METHOD_TEMPORAL_MEMORY)]["score"],
        }
        for qa_id in question_ids
        if qa_id not in paired
    ]
    failure_taxonomy = {
        name: sum(row.get("failure_taxonomy") == name for row in scored)
        for name in (
            "MEMORY_CONSTRUCTION_MISS",
            "RETRIEVAL_MISS",
            "STALE_FACT_SELECTED",
            "SUPERSESSION_ERROR",
            "LOOKUP_ROUTING_MISS",
            "CONTEXT_ASSEMBLY_DROP",
            "READER_ERROR",
            "SCORING_AMBIGUITY",
            "PROVIDER_ERROR",
            "HARNESS_GUARD_BLOCK",
            "OTHER",
        )
    }
    output_rows = [
        {
            "question_id": row["qa_id"],
            "method": row["method"],
            "provider_outcome": row["provider_outcome"],
            "score": row["score"],
            "failure_taxonomy": row["failure_taxonomy"],
            "selected_context_tokens_estimated": row["selected_context_tokens_estimated"],
            "selected_source_evidence_ids": row["selected_source_evidence_ids"],
            "memory_lookup_status": row["memory_lookup_status"],
            "provider_attempts": row["provider_attempts"],
        }
        for row in scored
    ]
    scored_path = root / "scored_results.json"
    _json_write(
        scored_path,
        {
            "schema_version": "linkloom-public-memory-scored-results/v1",
            "cell": CELLS[cell_key].display_name,
            "metric": "substring_exact_match",
            "gold_values_persisted": False,
            "results": output_rows,
        },
    )
    _seal_file(scored_path, identity=f"scored-results:{CELLS[cell_key].source}")
    full_score_digest = hashlib.sha256(scored_path.read_bytes()).hexdigest()
    report = {
        "schema_version": "linkloom-public-memory-clean-evaluation-summary/v1",
        "cell": CELLS[cell_key].display_name,
        "source": CELLS[cell_key].source,
        "dataset_revision": preflight["dataset_revision"],
        "dataset_sha256": DATASET_SHA256,
        "subset_sha256": preflight["subset_sha256"],
        "source_subset_sha256": preflight.get("source_subset_sha256"),
        "freeze_manifest_sha256": preflight["freeze_manifest_sha256"],
        "runtime_execution_plan_sha256": preflight["runtime_execution_plan_sha256"],
        "implementation_fingerprint_sha256": fingerprints["files_sha256"],
        "provider": "Gemini Developer API",
        "model": MODEL,
        "sdk_version": SDK_VERSION,
        "metric": "substring_exact_match",
        "metric_source": METRIC_SOURCE_URL,
        "gold_values_read_only_after_sealed_provider_outputs": True,
        "gold_values_persisted": False,
        "provider_completion": {
            "completed_method_cases": successful_count,
            "planned_method_cases": len(provider_outputs),
            "rate": completion_rate,
        },
        "score_unlock_gate": score_unlock_gate,
        "method_metrics": method_metrics,
        "paired_complete": {
            "question_count": len(paired),
            "planned_question_count": len(question_ids),
            "coverage_rate": len(paired) / len(question_ids) if question_ids else 0.0,
            "flat_correct": paired_flat_correct,
            "temporal_correct": paired_temporal_correct,
            "both_correct": both_correct,
            "flat_only_correct": flat_only,
            "temporal_only_correct": temporal_only,
            "both_incorrect": both_incorrect,
            "unpaired_questions": unpaired,
        },
        "failure_taxonomy": failure_taxonomy,
        "per_method_question_results": scored,
        "scored_results_sha256": full_score_digest,
        "accuracy_claim_scope": "descriptive evaluation of one frozen public benchmark cell; not a generalized superiority claim",
    }
    return report, report


def _global_committed_excluding(ledger: dict[str, Any], run_id: str) -> float:
    total = 0.0
    for run in ledger.get("runs", []):
        if not isinstance(run, dict) or run.get("run_id") == run_id:
            continue
        total += float(run.get("estimated_spend_upper_bound_usd", 0.0))
        total += float(run.get("reserved_usd", 0.0))
    return total


def _write_run_manifest(root: Path, payload: dict[str, Any]) -> None:
    path = root / "run_manifest.json"
    _json_write(path, payload)
    _seal_file(path, identity=f"run-manifest:{payload['run_id']}")


def _persist_provider_outputs(
    *,
    root: Path,
    cell_key: str,
    plan_rows: list[dict[str, Any]],
    output_by_sequence: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], Path]:
    cell = CELLS[cell_key]
    final_rows: list[dict[str, Any]] = []
    for plan in plan_rows:
        sequence = int(plan["sequence"])
        row = output_by_sequence.get(sequence)
        if row is None:
            row = {
                "sequence": sequence,
                "qa_id": plan["qa_id"],
                "method": plan["method"],
                "provider_outcome": "NOT_RUN",
                "provider_attempts": 0,
                "request_sha256": plan["generation_request_sha256"],
                "context_sha256": plan["context_sha256"],
                "context_tokens_estimated": plan["context_tokens_estimated"],
                "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
                "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
                "memory_lookup_status": plan["memory_lookup_status"],
                "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
            }
            output_by_sequence[sequence] = row
        case_path = root / "cases" / f"{sequence:03d}.json"
        if case_path.is_file():
            existing = _read_case_result(root, sequence)
            if existing != row:
                raise CleanEvalBlocked(f"METHOD_CASE_ARTIFACT_MISMATCH_{sequence}")
        else:
            _record_case_result(root, row)
        final_rows.append(row)
    expected_count = len(plan_rows)
    if len(final_rows) != expected_count or len({row["sequence"] for row in final_rows}) != expected_count:
        raise CleanEvalBlocked("PROVIDER_OUTPUT_METHOD_CASE_SET_INVALID")
    payload = {
        "schema_version": "linkloom-public-memory-provider-outputs/v1",
        "cell": cell.display_name,
        "source": cell.source,
        "gold_values_read": False,
        "provider_outputs": final_rows,
    }
    path = root / "provider_outputs.json"
    _json_write(path, payload)
    _seal_file(path, identity=f"provider-outputs:{cell.source}")
    return final_rows, path


def _publish_artifact_manifest(root: Path, run_id: str) -> None:
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"artifact_manifest.json", "artifact_manifest.json.seal.json"}
    }
    path = root / "artifact_manifest.json"
    _json_write(
        path,
        {
            "schema_version": "linkloom-public-memory-artifact-manifest/v1",
            "run_id": run_id,
            "files": files,
            "files_sha256": canonical_sha256(files),
        },
    )
    _seal_file(path, identity=f"artifact-manifest:{run_id}")


def _verify_published_artifact_manifest(root: Path, run_id: str) -> dict[str, Any]:
    path = root / "artifact_manifest.json"
    if not _verify_seal(path, identity=f"artifact-manifest:{run_id}"):
        return {"status": "FAIL", "errors": ["MANIFEST_SEAL_INVALID"]}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        return {"status": "FAIL", "errors": ["MANIFEST_UNREADABLE"]}
    if not isinstance(payload, dict):
        return {"status": "FAIL", "errors": ["MANIFEST_SCHEMA_INVALID"]}
    files = payload.get("files")
    if payload.get("run_id") != run_id or not isinstance(files, dict):
        return {"status": "FAIL", "errors": ["MANIFEST_IDENTITY_INVALID"]}
    errors: list[str] = []
    root_resolved = root.resolve()
    for relative_name, expected_hash in files.items():
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"INVALID_PATH:{relative_name}")
            continue
        target = (root / relative).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            errors.append(f"PATH_ESCAPE:{relative_name}")
            continue
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            errors.append(f"HASH_MISMATCH:{relative_name}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "file_count": len(files),
        "errors": errors,
    }


def _write_diagnostic_plan(
    root: Path,
    *,
    run_id: str,
    cell_key: str,
    plan_rows: list[dict[str, Any]],
) -> Path:
    allowed = (
        "sequence",
        "qa_id",
        "method",
        "generation_request_sha256",
        "count_tokens_request_sha256",
        "context_sha256",
        "context_tokens_estimated",
        "context_evidence_ids_selected",
        "retrieval_evidence_ids",
        "memory_lookup_status",
        "memory_source_evidence_ids",
        "static_input_token_upper_bound",
        "one_attempt_cost_upper_bound_usd",
    )
    safe_rows = [{key: row.get(key) for key in allowed} for row in plan_rows]
    payload = {
        "schema_version": "linkloom-public-memory-diagnostic-plan/v1",
        "run_id": run_id,
        "cell_key": cell_key,
        "plan_rows": safe_rows,
    }
    if _contains_gold_payload_field(payload):
        raise CleanEvalBlocked("DIAGNOSTIC_GOLD_ACCESS_VIOLATION")
    path = root / "diagnostic_plan.json"
    _atomic_json_write_fsync(path, payload)
    _seal_file(path, identity=f"diagnostic-plan:{run_id}")
    return path


DIAGNOSTIC_CASE_OUTPUT_FIELDS = frozenset(
    {
        "sequence",
        "qa_id",
        "method",
        "provider_outcome",
        "failure_classification",
        "reason_code",
        "provider_response_accepted",
        "provider_attempts",
        "request_sha256",
        "response_sha256",
        "count_tokens_request_sha256",
        "context_sha256",
        "context_tokens_estimated",
        "selected_source_evidence_ids",
        "retrieval_evidence_ids",
        "memory_lookup_status",
        "memory_source_evidence_ids",
        "counted_input_tokens",
        "count_tokens_outcome",
        "count_tokens_status",
        "count_tokens_token_estimation_source",
        "count_tokens_fallback_reason",
        "static_conservative_input_token_estimate",
        "billing_preflight_uncertainty",
        "count_tokens_transport_attempts",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_thinking_tokens",
        "usage_status",
        "billing_uncertainty",
        "provider_reported_cost_usd",
        "provider_elapsed_ms",
        "attempt_cost_upper_bound_usd",
        "safe_error",
    }
)


SAFE_DIAGNOSTIC_MESSAGES = frozenset(
    {
        "A clean evaluation guard blocked this method-case.",
        "Provider or transport failed before an accepted response.",
        "Local evaluation harness processing failed.",
        "Local run-level processing failed.",
        "The local diagnostic process ended before this case closed.",
    }
)
SAFE_DIAGNOSTIC_ERROR_FIELDS = frozenset(
    {
        "exception_class",
        "exception_type",
        "guard_state",
        "outcome_domain",
        "reason_code",
        "reason_category",
        "phase",
        "guard_name",
        "failure_classification",
        "provider_outcome",
        "safe_message",
        "logical_case_id",
        "method",
        "provider_response_accepted",
        "provider_response_already_accepted",
        "throw_site",
        "throw_site_module",
        "throw_site_function",
        "bounded_stack",
        "bounded_stack_summary",
        "nested_cause_type",
        "retry_classification",
        "retry_blocked_by",
        "http_status",
        "provider_error_code",
        "count_tokens_exception_class",
    }
)


def _safe_diagnostic_failure(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CleanEvalBlocked("DIAGNOSTIC_FAILURE_ARTIFACT_INVALID")
    safe: dict[str, Any] = {}
    for key in SAFE_DIAGNOSTIC_ERROR_FIELDS:
        if key not in value:
            continue
        item = value[key]
        if key in {"throw_site"}:
            if isinstance(item, dict):
                safe[key] = {
                    name: item[name]
                    for name in ("module", "function", "line")
                    if name in item and (
                        name != "line"
                        or item[name] is None
                        or (isinstance(item[name], int) and not isinstance(item[name], bool) and item[name] > 0)
                    )
                    and (
                        name == "line"
                        or _is_safe_diagnostic_label(item[name])
                    )
                }
            continue
        if key in {"bounded_stack", "bounded_stack_summary"}:
            if isinstance(item, list):
                safe[key] = [
                    {
                        name: frame[name]
                        for name in ("module", "function", "line")
                        if name in frame and (
                            name == "line"
                            and isinstance(frame[name], int)
                            and not isinstance(frame[name], bool)
                            and frame[name] > 0
                            or name != "line"
                            and _is_safe_diagnostic_label(frame[name])
                        )
                    }
                    for frame in item[-8:]
                    if isinstance(frame, dict)
                ]
            continue
        if key == "safe_message":
            if isinstance(item, str) and item in SAFE_DIAGNOSTIC_MESSAGES:
                safe[key] = item
            continue
        if key in {"provider_response_accepted", "provider_response_already_accepted"}:
            if isinstance(item, bool):
                safe[key] = item
            continue
        if key == "http_status":
            if isinstance(item, int) and not isinstance(item, bool) and 100 <= item <= 599:
                safe[key] = item
            continue
        if key in {"outcome_domain"}:
            if isinstance(item, str) and item in {"PROVIDER", "LOCAL_HARNESS", "INTEGRITY", "PROTOCOL", "SCORING"}:
                safe[key] = item
            continue
        if key in {"provider_outcome"}:
            if isinstance(item, str) and item in {"PROVIDER_ERROR", "LOCAL_HARNESS_BLOCK", "PROVIDER_COMPLETE"}:
                safe[key] = item
            continue
        if key == "failure_classification":
            if isinstance(item, str) and item in {"PROVIDER_ERROR", "PROVIDER ERROR", "LOCAL/HARNESS BLOCK"}:
                safe[key] = item
            continue
        if key == "reason_category":
            if isinstance(item, str) and item in {
                "ARTIFACT", "COST", "FINGERPRINT", "GOLD", "HARNESS", "OTHER",
                "PROTOCOL", "RESPONSE", "SCORING", "LOCAL_HARNESS",
            }:
                safe[key] = item
            continue
        if key in {"reason_code", "retry_classification", "retry_blocked_by", "phase", "guard_state"}:
            if isinstance(item, str) and 0 < len(item) <= 160 and item.isascii() and item == item.upper() and all(
                character.isalnum() or character == "_" for character in item
            ):
                safe[key] = item
            continue
        if key in {"logical_case_id"}:
            if isinstance(item, str) and 0 < len(item) <= 128 and item.isascii() and all(
                character.isalnum() or character in "_-. :" for character in item
            ):
                safe[key] = item
            continue
        if key == "method":
            if isinstance(item, str) and 0 < len(item) <= 80 and item.isascii() and all(
                character.isalnum() or character in "_-. " for character in item
            ):
                safe[key] = item
            continue
        if isinstance(item, str) and 0 < len(item) <= 160 and item.isascii() and all(
            character.isalnum() or character in "_.-" for character in item
        ):
            safe[key] = item
    return safe


def _safe_diagnostic_case_row(
    row: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    safe = {key: value for key, value in row.items() if key in DIAGNOSTIC_CASE_OUTPUT_FIELDS}
    if "safe_error" in safe:
        safe["safe_error"] = _safe_diagnostic_failure(safe["safe_error"])
    if safe.get("provider_outcome") in {"PROVIDER_ERROR", "LOCAL_HARNESS_BLOCK"}:
        safe_error = safe.get("safe_error")
        if not isinstance(safe_error, dict):
            raise CleanEvalBlocked("METHOD_CASE_FAILURE_EVIDENCE_MISSING")
        reason_code = safe.get("reason_code") or safe_error.get("reason_code")
        if (
            not isinstance(reason_code, str)
            or not reason_code
            or safe_error.get("reason_code", reason_code) != reason_code
        ):
            raise CleanEvalBlocked("METHOD_CASE_FAILURE_REASON_CODE_INVALID")
        safe["reason_code"] = reason_code
        safe_error.setdefault("reason_code", reason_code)
        safe_error.setdefault("provider_outcome", safe["provider_outcome"])
        if safe_error.get("provider_outcome") != safe["provider_outcome"]:
            raise CleanEvalBlocked("METHOD_CASE_FAILURE_OUTCOME_EVIDENCE_MISMATCH")
        safe["safe_error"] = safe_error
    safe.setdefault("sequence", plan["sequence"])
    safe.setdefault("qa_id", plan["qa_id"])
    safe.setdefault("method", plan["method"])
    safe.setdefault("request_sha256", plan["generation_request_sha256"])
    safe.setdefault("count_tokens_request_sha256", plan["count_tokens_request_sha256"])
    safe.setdefault("context_sha256", plan["context_sha256"])
    safe.setdefault("context_tokens_estimated", plan["context_tokens_estimated"])
    safe.setdefault("selected_source_evidence_ids", plan["context_evidence_ids_selected"])
    safe.setdefault("retrieval_evidence_ids", plan["retrieval_evidence_ids"])
    safe.setdefault("memory_lookup_status", plan["memory_lookup_status"])
    safe.setdefault("memory_source_evidence_ids", plan["memory_source_evidence_ids"])
    if _contains_gold_payload_field(safe):
        raise CleanEvalBlocked("DIAGNOSTIC_GOLD_ACCESS_VIOLATION")
    return safe


def _diagnostic_interruption_outcome(
    *,
    sequence: int,
    attempt_no: int,
    plan: dict[str, Any],
    accepted_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    accepted = bool(
        accepted_evidence
        and accepted_evidence.get("provider_response_accepted") is True
    )
    reason_code = "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_PROCESS_INTERRUPTED"
    safe = {
        "exception_class": "ProcessInterrupted",
        "exception_type": "ProcessInterrupted",
        "guard_state": "UNAVAILABLE",
        "outcome_domain": "LOCAL_HARNESS",
        "failure_classification": "LOCAL/HARNESS BLOCK",
        "provider_outcome": "LOCAL_HARNESS_BLOCK",
        "reason_code": reason_code,
        "reason_category": "LOCAL_HARNESS",
        "phase": "PROCESS_INTERRUPTION",
        "guard_name": "_finalize_diagnostic_run",
        "safe_message": "The local diagnostic process ended before this case closed.",
        "provider_response_accepted": accepted,
        "provider_response_already_accepted": accepted,
        "throw_site": {
            "module": "scripts.run_public_memory_clean_eval",
            "function": "_finalize_diagnostic_run",
            "line": None,
        },
        "throw_site_module": "scripts.run_public_memory_clean_eval",
        "throw_site_function": "_finalize_diagnostic_run",
        "bounded_stack": [],
        "bounded_stack_summary": [],
        "nested_cause_type": None,
        "logical_case_id": plan["qa_id"],
        "method": plan["method"],
    }
    row = {
        "sequence": sequence,
        "qa_id": plan["qa_id"],
        "method": plan["method"],
        "provider_outcome": "LOCAL_HARNESS_BLOCK",
        "failure_classification": "LOCAL/HARNESS BLOCK",
        "reason_code": reason_code,
        "provider_response_accepted": accepted,
        "provider_attempts": attempt_no,
        "request_sha256": plan["generation_request_sha256"],
        "context_sha256": plan["context_sha256"],
        "context_tokens_estimated": plan["context_tokens_estimated"],
        "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
        "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
        "memory_lookup_status": plan["memory_lookup_status"],
        "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
        "safe_error": safe,
    }
    if accepted_evidence is not None:
        row.update(
            {
                "response_sha256": accepted_evidence.get("response_sha256"),
                "provider_input_tokens": (accepted_evidence.get("usage_metadata") or {}).get("input_tokens"),
                "provider_output_tokens": (accepted_evidence.get("usage_metadata") or {}).get("output_tokens"),
                "provider_thinking_tokens": (accepted_evidence.get("usage_metadata") or {}).get("thinking_tokens"),
                "usage_status": _evidence_usage_status(accepted_evidence),
                "billing_uncertainty": accepted_evidence.get("billing_uncertainty", not _evidence_usage_is_complete(accepted_evidence)),
            }
        )
    return safe, row


def _ensure_diagnostic_block_artifact(
    root: Path,
    *,
    run_id: str,
    sequence: int,
    plan: dict[str, Any],
    attempt_no: int,
    accepted_evidence: dict[str, Any] | None,
    safe_block: dict[str, Any],
) -> None:
    block_path = root / "clean_eval_blocks" / f"{sequence:03d}_attempt-{attempt_no:02d}.json"
    identity = f"clean-eval-block:{run_id}:{sequence}:{attempt_no}"
    if block_path.is_file():
        if not _verify_seal(block_path, identity=identity):
            raise CleanEvalBlocked("ARTIFACT_CLEAN_EVAL_BLOCK_SEAL_INVALID")
        return
    _persist_clean_eval_block(
        root,
        run_id=run_id,
        sequence=sequence,
        case_id=plan["qa_id"],
        method=plan["method"],
        attempt_no=attempt_no,
        safe_block=safe_block,
        request_hash=plan["generation_request_sha256"],
        response_evidence=accepted_evidence,
    )


def _finalize_diagnostic_run(
    root: Path,
    *,
    stop_reason: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconstruct and seal a diagnostic summary using only local artifacts."""

    root = Path(root)
    manifest_path = root / "run_manifest.json"
    try:
        run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise CleanEvalBlocked("DIAGNOSTIC_RUN_MANIFEST_UNREADABLE") from None
    if not isinstance(run_manifest, dict):
        raise CleanEvalBlocked("DIAGNOSTIC_RUN_MANIFEST_INVALID")
    run_id = run_manifest.get("run_id")
    cell_key = run_manifest.get("cell_key")
    if (
        not isinstance(run_id, str)
        or run_manifest.get("diagnostic_mode") is not True
        or cell_key not in CELLS
        or not _verify_seal(manifest_path, identity=f"run-manifest:{run_id}")
    ):
        raise CleanEvalBlocked("DIAGNOSTIC_RUN_MANIFEST_SEAL_INVALID")

    plan_path = root / "diagnostic_plan.json"
    if not _verify_seal(plan_path, identity=f"diagnostic-plan:{run_id}"):
        raise CleanEvalBlocked("DIAGNOSTIC_PLAN_SEAL_INVALID")
    try:
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise CleanEvalBlocked("DIAGNOSTIC_PLAN_UNREADABLE") from None
    plan_rows = plan_payload.get("plan_rows") if isinstance(plan_payload, dict) else None
    if (
        not isinstance(plan_payload, dict)
        or plan_payload.get("run_id") != run_id
        or plan_payload.get("cell_key") != cell_key
        or not isinstance(plan_rows, list)
        or len(plan_rows) != 200
        or _contains_gold_payload_field(plan_payload)
    ):
        raise CleanEvalBlocked("DIAGNOSTIC_PLAN_INVALID_OR_GOLD_CONTAMINATED")
    plan_by_sequence = {
        row.get("sequence"): row
        for row in plan_rows
        if isinstance(row, dict) and isinstance(row.get("sequence"), int)
    }
    if len(plan_by_sequence) != len(plan_rows) or set(plan_by_sequence) != set(range(1, len(plan_rows) + 1)):
        raise CleanEvalBlocked("DIAGNOSTIC_PLAN_ORDER_INVALID")

    journal_path = root / "provider_attempt_journal.jsonl"
    accepted_path = root / "accepted_response_evidence.jsonl"
    journal_events = _read_attempt_journal(journal_path)
    evidence_summary = _read_accepted_response_evidence(accepted_path)
    journal_events, evidence_summary, recovered_receipt_evidence, receipt_recovery_errors = (
        _recover_response_receipt_stages(
            journal_path,
            accepted_path,
            journal_events,
            evidence_summary,
        )
    )
    if receipt_recovery_errors:
        stop_reason = stop_reason or "ARTIFACT_RESPONSE_RECEIPT_RECOVERY_FAILED"
    if not evidence_summary["integrity_valid"]:
        stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_EVIDENCE_INVALID"
    response_receipt_integrity_valid = (
        not receipt_recovery_errors and evidence_summary["integrity_valid"]
    )
    accepted_by_attempt = {
        (row["sequence"], row["attempt_no"]): row
        for row in evidence_summary["rows"]
    }
    start_by_attempt = {
        (event.get("sequence"), event.get("attempt_no")): event
        for event in journal_events
        if event.get("event") == "PROVIDER_ATTEMPT_STARTED"
    }
    accepted_events = {
        (event.get("sequence"), event.get("attempt_no")): event
        for event in journal_events
        if event.get("event") in {"RESPONSE_RECEIVED", "PROVIDER_RESPONSE_ACCEPTED"}
    }
    persisted_events = {
        (event.get("sequence"), event.get("attempt_no")): event
        for event in journal_events
        if event.get("event") == "RESPONSE_EVIDENCE_PERSISTED"
    }
    usage_validation_events = {
        (event.get("sequence"), event.get("attempt_no")): event
        for event in journal_events
        if event.get("event") == "USAGE_VALIDATED"
    }
    for attempt_key, evidence in accepted_by_attempt.items():
        start = start_by_attempt.get(attempt_key)
        accepted_event = accepted_events.get(attempt_key)
        if not isinstance(start, dict):
            response_receipt_integrity_valid = False
            stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_WITHOUT_ATTEMPT"
            continue
        if (
            evidence.get("run_id") != run_id
            or evidence.get("sequence") != attempt_key[0]
            or evidence.get("attempt_no") != attempt_key[1]
            or evidence.get("case_id") != start.get("qa_id")
            or evidence.get("method") != start.get("method")
            or evidence.get("logical_attempt_id") != start.get("attempt_id")
            or evidence.get("request_sha256") != start.get("generation_request_sha256")
        ):
            response_receipt_integrity_valid = False
            stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_IDENTITY_MISMATCH"
            continue
        if accepted_event is None:
            if attempt_key in {
                (event.get("sequence"), event.get("attempt_no"))
                for event in journal_events
                if event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
            }:
                response_receipt_integrity_valid = False
                stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_JOURNAL_MISMATCH"
                continue
            accepted_event = {
                **start,
                "event": "PROVIDER_RESPONSE_ACCEPTED",
                "provider_response_accepted": True,
                "request_sha256": evidence["request_sha256"],
                "response_sha256": evidence["response_sha256"],
                "usage_present": evidence["usage_present"],
                "usage_metadata": evidence["usage_metadata"],
                "provider_request_id": evidence["provider_request_id"],
                "model": evidence["model"],
                "accepted_at_utc": evidence["accepted_at_utc"],
            }
            _append_attempt_journal(journal_path, accepted_event)
            journal_events.append(accepted_event)
            accepted_events[attempt_key] = accepted_event
        elif (
            accepted_event.get("request_sha256") != evidence.get("request_sha256")
            or accepted_event.get("response_sha256") != evidence.get("response_sha256")
            or accepted_event.get("logical_attempt_id", start.get("attempt_id")) != evidence.get("logical_attempt_id")
            or accepted_event.get("model") != evidence.get("model")
            or accepted_event.get("usage_present") != evidence.get("usage_present")
            or accepted_event.get("usage_metadata") != evidence.get("usage_metadata")
            or accepted_event.get("usage_missing_fields") != evidence.get("usage_missing_fields")
            or accepted_event.get("provider_request_id") != evidence.get("provider_request_id")
            or accepted_event.get("accepted_at_utc") != evidence.get("accepted_at_utc")
            or (
                accepted_event.get("event") == "RESPONSE_RECEIVED"
                and (
                    accepted_event.get("usage_status") != evidence.get("usage_status")
                    or accepted_event.get("usage_sources") != evidence.get("usage_sources")
                    or accepted_event.get("provider_usage_fields")
                    != evidence.get("provider_usage_fields")
                    or accepted_event.get("usage_invalid_fields", [])
                    != evidence.get("usage_invalid_fields", [])
                    or accepted_event.get("usage_access_failures", [])
                    != evidence.get("usage_access_failures", [])
                    or accepted_event.get("billing_uncertainty")
                    != evidence.get("billing_uncertainty")
                    or accepted_event.get("billing_basis") != evidence.get("billing_basis")
                )
            )
        ):
            response_receipt_integrity_valid = False
            stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_IDENTITY_MISMATCH"
        if accepted_event.get("event") == "RESPONSE_RECEIVED":
            persisted = persisted_events.get(attempt_key)
            validated = usage_validation_events.get(attempt_key)
            if (
                not isinstance(persisted, dict)
                or persisted.get("evidence_sha256") != canonical_sha256(evidence)
            ):
                response_receipt_integrity_valid = False
                stop_reason = stop_reason or "ARTIFACT_RESPONSE_EVIDENCE_JOURNAL_MISMATCH"
            if (
                not isinstance(validated, dict)
                or validated.get("usage_present") != evidence.get("usage_present")
                or validated.get("usage_status") != evidence.get("usage_status")
                or validated.get("usage_metadata") != evidence.get("usage_metadata")
                or validated.get("usage_missing_fields") != evidence.get("usage_missing_fields")
                or validated.get("usage_sources") != evidence.get("usage_sources")
                or validated.get("usage_invalid_fields", [])
                != evidence.get("usage_invalid_fields", [])
                or validated.get("usage_access_failures", [])
                != evidence.get("usage_access_failures", [])
                or validated.get("billing_uncertainty")
                != evidence.get("billing_uncertainty")
                or validated.get("billing_basis") != evidence.get("billing_basis")
            ):
                response_receipt_integrity_valid = False
                stop_reason = stop_reason or "ARTIFACT_RESPONSE_USAGE_JOURNAL_MISMATCH"
    if set(accepted_events) - set(accepted_by_attempt):
        response_receipt_integrity_valid = False
        stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_EVIDENCE_MISSING"

    journal_summary = _validate_attempt_journal_events(
        journal_events,
        expected_case_count=len(plan_rows),
        scoring_required=False,
    )
    interrupt_recoverable_errors = {
        "PROVIDER_ATTEMPT_LEFT_OPEN",
        "PROVIDER_ATTEMPT_EVENT_MISMATCH",
    }
    if not journal_summary["integrity_valid"] and not set(journal_summary["errors"]) <= interrupt_recoverable_errors:
        stop_reason = stop_reason or "JOURNAL_PROTOCOL_CORRUPTION"

    started_by_sequence = {
        event.get("sequence"): event
        for event in journal_events
        if event.get("event") == "CASE_METHOD_STARTED"
    }
    terminal_sequences = {
        event.get("sequence")
        for event in journal_events
        if event.get("event") in {"CASE_METHOD_COMPLETED", "CASE_METHOD_BLOCKED", "CASE_METHOD_ABORTED"}
    }
    completions_by_attempt = {
        (event.get("sequence"), event.get("attempt_no")): event
        for event in journal_events
        if event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    }
    active_attempts = {
        key: event
        for key, event in start_by_attempt.items()
        if key not in completions_by_attempt
    }
    interrupted_sequences: set[int] = set()
    if journal_summary["integrity_valid"] or set(journal_summary["errors"]) <= interrupt_recoverable_errors:
        for sequence, case_start in sorted(started_by_sequence.items()):
            if sequence in terminal_sequences:
                continue
            plan = plan_by_sequence.get(sequence)
            if plan is None:
                stop_reason = stop_reason or "PROTOCOL_DIAGNOSTIC_CASE_OUTSIDE_PLAN"
                continue
            sequence_attempts = sorted(
                (key, event) for key, event in start_by_attempt.items() if key[0] == sequence
            )
            active_for_case = [(key, event) for key, event in sequence_attempts if key in active_attempts]
            if active_for_case:
                attempt_key, attempt_start = active_for_case[-1]
                accepted_evidence = accepted_by_attempt.get(attempt_key)
                safe_block, row = _diagnostic_interruption_outcome(
                    sequence=sequence,
                    attempt_no=attempt_key[1],
                    plan=plan,
                    accepted_evidence=accepted_evidence,
                )
                _ensure_diagnostic_block_artifact(
                    root,
                    run_id=run_id,
                    sequence=sequence,
                    plan=plan,
                    attempt_no=attempt_key[1],
                    accepted_evidence=accepted_evidence,
                    safe_block=safe_block,
                )
                completion = {
                    **attempt_start,
                    "event": "PROVIDER_ATTEMPT_COMPLETED",
                    "provider_outcome": "LOCAL_HARNESS_BLOCK",
                    "failure_classification": "LOCAL/HARNESS BLOCK",
                    "reason_code": safe_block["reason_code"],
                    "provider_response_accepted": safe_block["provider_response_accepted"],
                    "provider_request_sent": True if accepted_evidence else None,
                    "retryable_transient_failure": False,
                    "safe_error": safe_block,
                    "recovered_after_process_interruption": True,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                }
                _append_attempt_journal(journal_path, completion)
                journal_events.append(completion)
                completions_by_attempt[attempt_key] = completion
                row = _safe_diagnostic_case_row(row, plan)
                _record_case_result(root, row)
                terminal = {
                    "event": "CASE_METHOD_BLOCKED",
                    "run_id": run_id,
                    "sequence": sequence,
                    "qa_id": plan["qa_id"],
                    "method": plan["method"],
                    "provider_outcome": "LOCAL_HARNESS_BLOCK",
                    "reason_code": safe_block["reason_code"],
                    "recovered_after_process_interruption": True,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                }
                _append_attempt_journal(journal_path, terminal)
                journal_events.append(terminal)
                interrupted_sequences.add(sequence)
                terminal_sequences.add(sequence)
            elif sequence_attempts:
                last_key, last_start = sequence_attempts[-1]
                last_completion = completions_by_attempt.get(last_key)
                if isinstance(last_completion, dict) and last_completion.get("provider_outcome") == "PROVIDER_COMPLETE":
                    response_evidence = accepted_by_attempt.get(last_key)
                    case_path = root / "cases" / f"{sequence:03d}.json"
                    if case_path.is_file():
                        existing = _read_case_result(root, sequence)
                        existing_safe = (
                            _safe_diagnostic_case_row(existing, plan)
                            if isinstance(existing, dict)
                            else None
                        )
                        if (
                            existing_safe is None
                            or existing_safe.get("provider_outcome") != "PROVIDER_COMPLETE"
                            or existing_safe.get("request_sha256") != plan["generation_request_sha256"]
                            or not response_evidence
                            or existing_safe.get("response_sha256") != response_evidence.get("response_sha256")
                        ):
                            raise CleanEvalBlocked(f"DIAGNOSTIC_METHOD_CASE_ARTIFACT_MISMATCH_{sequence}")
                    else:
                        row = {
                            "sequence": sequence,
                            "qa_id": plan["qa_id"],
                            "method": plan["method"],
                            "provider_outcome": "PROVIDER_COMPLETE",
                            "provider_response_accepted": True,
                            "provider_attempts": len(sequence_attempts),
                            "request_sha256": plan["generation_request_sha256"],
                            "response_sha256": response_evidence.get("response_sha256") if response_evidence else None,
                            "context_tokens_estimated": plan["context_tokens_estimated"],
                        }
                        _record_case_result(root, _safe_diagnostic_case_row(row, plan))
                    terminal = {
                        "event": "CASE_METHOD_COMPLETED",
                        "run_id": run_id,
                        "sequence": sequence,
                        "qa_id": plan["qa_id"],
                        "method": plan["method"],
                        "provider_outcome": "PROVIDER_COMPLETE",
                        "recovered_after_process_interruption": True,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                    }
                    _append_attempt_journal(journal_path, terminal)
                    journal_events.append(terminal)
                    terminal_sequences.add(sequence)
                    interrupted_sequences.add(sequence)
                else:
                    safe_block, row = _diagnostic_interruption_outcome(
                        sequence=sequence,
                        attempt_no=last_key[1],
                        plan=plan,
                        accepted_evidence=accepted_by_attempt.get(last_key),
                    )
                    row = _safe_diagnostic_case_row(row, plan)
                    _ensure_diagnostic_block_artifact(
                        root,
                        run_id=run_id,
                        sequence=sequence,
                        plan=plan,
                        attempt_no=0,
                        accepted_evidence=accepted_by_attempt.get(last_key),
                        safe_block=safe_block,
                    )
                    _record_case_result(root, row)
                    terminal = {
                        "event": "CASE_METHOD_BLOCKED",
                        "run_id": run_id,
                        "sequence": sequence,
                        "qa_id": plan["qa_id"],
                        "method": plan["method"],
                        "provider_outcome": "LOCAL_HARNESS_BLOCK",
                        "reason_code": safe_block["reason_code"],
                        "recovered_after_process_interruption": True,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                    }
                    _append_attempt_journal(journal_path, terminal)
                    journal_events.append(terminal)
                    interrupted_sequences.add(sequence)
                    terminal_sequences.add(sequence)
            else:
                terminal = {
                    "event": "CASE_METHOD_ABORTED",
                    "run_id": run_id,
                    "sequence": sequence,
                    "qa_id": plan["qa_id"],
                    "method": plan["method"],
                    "reason_code": "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_PROCESS_INTERRUPTED",
                    "recovered_after_process_interruption": True,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                }
                _append_attempt_journal(journal_path, terminal)
                journal_events.append(terminal)
                interrupted_sequences.add(sequence)
                terminal_sequences.add(sequence)
    if interrupted_sequences:
        stop_reason = stop_reason or "PROCESS_INTERRUPTION"
    journal_summary = _validate_attempt_journal_events(
        journal_events,
        expected_case_count=len(plan_rows),
        scoring_required=False,
    )
    if not journal_summary["integrity_valid"]:
        stop_reason = stop_reason or "JOURNAL_PROTOCOL_CORRUPTION"

    block_summary = _reconstruct_clean_eval_blocks(root)
    if not block_summary["integrity_valid"]:
        stop_reason = stop_reason or "ARTIFACT_CLEAN_EVAL_BLOCK_INTEGRITY_FAILURE"
    accepted_by_key = {
        (row.get("sequence"), row.get("attempt_no")): row
        for row in evidence_summary["rows"]
    }
    case_reconciliation_ok = True
    fatal_blocks = [
        row for row in block_summary["blocks"]
        if row.get("sequence") == 0
        or _diagnostic_guard_is_fatal(
            str(row.get("reason_code", "CLEAN_EVAL_BLOCK_LOCAL_UNKNOWN")),
            response_evidence=accepted_by_key.get((row.get("sequence"), row.get("attempt_no"))),
            provider_response_accepted=row.get("provider_response_accepted") is True,
            journal_integrity_valid=journal_summary["integrity_valid"],
            cost_reservation_safe=_diagnostic_cost_reservation_is_safe(
                start_by_attempt.get((row.get("sequence"), row.get("attempt_no"))),
                plan_by_sequence.get(row.get("sequence")),
                run_hard_cost_cap=run_manifest.get("hard_cost_cap_usd"),
                global_hard_cost_cap=run_manifest.get("global_cost_cap_usd"),
            ),
        )
    ]
    if fatal_blocks:
        stop_reason = stop_reason or str(fatal_blocks[0].get("reason_code", "DIAGNOSTIC_RUN_LEVEL_BLOCK"))

    terminal_by_sequence = {
        event.get("sequence"): event
        for event in journal_events
        if event.get("event") in {"CASE_METHOD_COMPLETED", "CASE_METHOD_BLOCKED", "CASE_METHOD_ABORTED"}
    }
    output_by_sequence: dict[int, dict[str, Any]] = {}
    for sequence, plan in sorted(plan_by_sequence.items()):
        case_path = root / "cases" / f"{sequence:03d}.json"
        attempts = sorted(
            ((key, event) for key, event in start_by_attempt.items() if key[0] == sequence),
            key=lambda pair: pair[0][1],
        )
        accepted_for_case = sorted(
            (
                (key, row)
                for key, row in accepted_by_key.items()
                if key[0] == sequence
            ),
            key=lambda pair: pair[0][1],
        )
        terminal = terminal_by_sequence.get(sequence)
        if case_path.is_file():
            case_row = _read_case_result(root, sequence)
            if not isinstance(case_row, dict):
                raise CleanEvalBlocked(f"DIAGNOSTIC_METHOD_CASE_ARTIFACT_INVALID_{sequence}")
            safe_row = _safe_diagnostic_case_row(case_row, plan)
            accepted = bool(accepted_for_case)
            if (
                safe_row.get("sequence") != sequence
                or safe_row.get("qa_id") != plan["qa_id"]
                or safe_row.get("method") != plan["method"]
                or safe_row.get("request_sha256") != plan["generation_request_sha256"]
                or safe_row.get("context_sha256") != plan["context_sha256"]
                or safe_row.get("provider_response_accepted", accepted) is not accepted
                or (accepted and safe_row.get("provider_outcome") == "PROVIDER_ERROR")
                or (
                    accepted
                    and (
                        not _is_sha256_hex(safe_row.get("response_sha256"))
                        or safe_row.get("response_sha256") != accepted_for_case[-1][1].get("response_sha256")
                    )
                )
            ):
                raise CleanEvalBlocked(f"DIAGNOSTIC_METHOD_CASE_ARTIFACT_MISMATCH_{sequence}")
            safe_row.setdefault("provider_response_accepted", accepted)
            if terminal is not None:
                expected_outcome = terminal.get("provider_outcome")
                if (
                    expected_outcome in {"PROVIDER_COMPLETE", "PROVIDER_ERROR", "LOCAL_HARNESS_BLOCK"}
                    and safe_row.get("provider_outcome") != expected_outcome
                ):
                    raise CleanEvalBlocked(f"DIAGNOSTIC_METHOD_CASE_JOURNAL_MISMATCH_{sequence}")
            output_by_sequence[sequence] = safe_row
            continue

        case_events = [event for event in journal_events if event.get("sequence") == sequence]
        if not any(event.get("event") == "CASE_METHOD_STARTED" for event in case_events):
            output_by_sequence[sequence] = {
                "sequence": sequence,
                "qa_id": plan["qa_id"],
                "method": plan["method"],
                "provider_outcome": "NOT_RUN",
                "provider_attempts": 0,
                "request_sha256": plan["generation_request_sha256"],
                "context_sha256": plan["context_sha256"],
                "context_tokens_estimated": plan["context_tokens_estimated"],
                "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
                "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
                "memory_lookup_status": plan["memory_lookup_status"],
                "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
            }
            continue

        base_row = {
            "sequence": sequence,
            "qa_id": plan["qa_id"],
            "method": plan["method"],
            "provider_attempts": len(attempts),
            "request_sha256": plan["generation_request_sha256"],
            "context_sha256": plan["context_sha256"],
            "context_tokens_estimated": plan["context_tokens_estimated"],
        }
        last_completion = None
        if attempts:
            last_completion = completions_by_attempt.get(attempts[-1][0])
        if terminal is not None and terminal.get("event") == "CASE_METHOD_COMPLETED" and isinstance(last_completion, dict):
            outcome = last_completion.get("provider_outcome")
            if outcome == "PROVIDER_COMPLETE" and accepted_for_case:
                evidence = accepted_for_case[-1][1]
                if evidence.get("request_sha256") != plan["generation_request_sha256"] or last_completion.get("response_sha256") != evidence.get("response_sha256"):
                    case_reconciliation_ok = False
                    stop_reason = stop_reason or "ARTIFACT_CASE_RESPONSE_IDENTITY_MISMATCH"
                reconstructed = {
                    **base_row,
                    "provider_outcome": "PROVIDER_COMPLETE",
                    "provider_response_accepted": True,
                    "response_sha256": evidence.get("response_sha256"),
                    "provider_input_tokens": (evidence.get("usage_metadata") or {}).get("input_tokens"),
                    "provider_output_tokens": (evidence.get("usage_metadata") or {}).get("output_tokens"),
                    "provider_thinking_tokens": (evidence.get("usage_metadata") or {}).get("thinking_tokens"),
                    "usage_status": _evidence_usage_status(evidence),
                    "billing_uncertainty": evidence.get("billing_uncertainty", False),
                }
                for field in (
                    "provider_input_tokens", "provider_output_tokens", "provider_thinking_tokens",
                    "provider_reported_cost_usd", "provider_elapsed_ms", "attempt_cost_upper_bound_usd",
                ):
                    if field in last_completion:
                        reconstructed[field] = last_completion[field]
                output_by_sequence[sequence] = _safe_diagnostic_case_row(reconstructed, plan)
                continue
            if outcome == "PROVIDER_ERROR" and not accepted_for_case:
                safe_error = _safe_diagnostic_failure(last_completion.get("safe_error", {}))
                output_by_sequence[sequence] = _safe_diagnostic_case_row(
                    {
                        **base_row,
                        "provider_outcome": "PROVIDER_ERROR",
                        "provider_response_accepted": False,
                        "failure_classification": "PROVIDER_ERROR",
                        "reason_code": last_completion.get("reason_code") or safe_error.get("reason_code", "PROVIDER_ERROR_UNCLASSIFIED"),
                        "safe_error": safe_error,
                    },
                    plan,
                )
                continue

        matching_blocks = [block for block in block_summary["blocks"] if block.get("sequence") == sequence]
        block = matching_blocks[-1] if matching_blocks else None
        if terminal is not None and terminal.get("event") == "CASE_METHOD_BLOCKED":
            if block is None:
                case_reconciliation_ok = False
                stop_reason = stop_reason or "ARTIFACT_CASE_BLOCK_MISSING"
            safe_error = _safe_diagnostic_failure(
                {
                    "outcome_domain": block.get("outcome_domain") if block else "LOCAL_HARNESS",
                    "failure_classification": "LOCAL/HARNESS BLOCK",
                    "provider_outcome": "LOCAL_HARNESS_BLOCK",
                    "reason_code": block.get("reason_code") if block else terminal.get("reason_code", "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_UNKNOWN"),
                    "reason_category": "LOCAL_HARNESS",
                    "phase": block.get("phase") if block else "RUN_LEVEL",
                    "guard_name": block.get("guard_name") if block else "_finalize_diagnostic_run",
                    "exception_type": block.get("exception_type") if block else "CleanEvalBlocked",
                    "provider_response_accepted": block.get("provider_response_accepted") is True if block else bool(accepted_for_case),
                    "throw_site": block.get("throw_site") if block else {"module": "scripts.run_public_memory_clean_eval", "function": "_finalize_diagnostic_run", "line": None},
                    "bounded_stack": block.get("bounded_stack", []) if block else [],
                    "nested_cause_type": block.get("nested_cause_type") if block else None,
                    "logical_case_id": plan["qa_id"],
                    "method": plan["method"],
                }
            )
            output_by_sequence[sequence] = _safe_diagnostic_case_row(
                {
                    **base_row,
                    "provider_outcome": "LOCAL_HARNESS_BLOCK",
                    "failure_classification": "LOCAL/HARNESS BLOCK",
                    "reason_code": safe_error.get("reason_code", "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_UNKNOWN"),
                    "provider_response_accepted": bool(accepted_for_case),
                    "safe_error": safe_error,
                    **({"response_sha256": accepted_for_case[-1][1].get("response_sha256")} if accepted_for_case else {}),
                    **(
                        {
                            "provider_input_tokens": (accepted_for_case[-1][1].get("usage_metadata") or {}).get("input_tokens"),
                            "provider_output_tokens": (accepted_for_case[-1][1].get("usage_metadata") or {}).get("output_tokens"),
                            "provider_thinking_tokens": (accepted_for_case[-1][1].get("usage_metadata") or {}).get("thinking_tokens"),
                            "usage_status": _evidence_usage_status(accepted_for_case[-1][1]),
                            "billing_uncertainty": accepted_for_case[-1][1].get("billing_uncertainty", True),
                        }
                        if accepted_for_case
                        else {}
                    ),
                },
                plan,
            )
            continue

        reason_code = (
            terminal.get("reason_code")
            if terminal is not None
            else "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_PROCESS_INTERRUPTED"
        )
        output_by_sequence[sequence] = _safe_diagnostic_case_row(
            {
                **base_row,
                "provider_outcome": "LOCAL_HARNESS_BLOCK",
                "failure_classification": "LOCAL/HARNESS BLOCK",
                "reason_code": reason_code,
                "provider_response_accepted": bool(accepted_for_case),
                "safe_error": {
                    "outcome_domain": "LOCAL_HARNESS",
                    "failure_classification": "LOCAL/HARNESS BLOCK",
                    "provider_outcome": "LOCAL_HARNESS_BLOCK",
                    "reason_code": reason_code,
                    "phase": "RUN_LEVEL",
                    "guard_name": "_finalize_diagnostic_run",
                    "exception_type": "ProcessInterrupted",
                    "provider_response_accepted": bool(accepted_for_case),
                    "throw_site": {"module": "scripts.run_public_memory_clean_eval", "function": "_finalize_diagnostic_run", "line": None},
                    "bounded_stack": [],
                },
            },
            plan,
        )

    final_rows = [output_by_sequence[sequence] for sequence in sorted(output_by_sequence)]
    cell = CELLS[cell_key]
    provider_outputs_path = root / "provider_outputs.json"
    for row in final_rows:
        sequence = int(row["sequence"])
        case_path = root / "cases" / f"{sequence:03d}.json"
        if case_path.is_file():
            existing = _read_case_result(root, sequence)
            if not isinstance(existing, dict) or _safe_diagnostic_case_row(existing, plan_by_sequence[sequence]) != row:
                raise CleanEvalBlocked(f"DIAGNOSTIC_METHOD_CASE_ARTIFACT_MISMATCH_{sequence}")
        else:
            _record_case_result(root, row)
    _atomic_json_write_fsync(
        provider_outputs_path,
        {
            "schema_version": "linkloom-public-memory-diagnostic-outputs/v1",
            "run_id": run_id,
            "cell": cell.display_name,
            "source": cell.source,
            "gold_reads": 0,
            "provider_outputs": final_rows,
        },
    )
    _seal_file(provider_outputs_path, identity=f"provider-outputs:{cell.source}")

    provider_stats = _provider_completion_stats(journal_events)
    terminal_count = journal_summary["terminal_method_cases"] + journal_summary["aborted_method_cases"]
    not_run_count = max(0, len(plan_rows) - journal_summary["started_method_cases"])
    provider_errors = sum(
        event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
        and event.get("provider_outcome") == "PROVIDER_ERROR"
        and event.get("provider_response_accepted") is not True
        for event in journal_events
    )
    retry_events = [
        event for event in journal_events
        if event.get("event") == "PROVIDER_ATTEMPT_STARTED" and event.get("attempt_no", 0) > 1
    ]
    latencies = [
        float(event.get("provider_elapsed_ms", event.get("elapsed_ms")))
        for event in journal_events
        if event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
        and isinstance(event.get("provider_elapsed_ms", event.get("elapsed_ms")), (int, float))
    ]
    usage_rows = [row.get("usage_metadata") for row in evidence_summary["rows"]]
    known_token_sums: dict[str, int] = {}
    unknown_token_counts: dict[str, int] = {}
    tokens: dict[str, int | None] = {}
    for name in ("input_tokens", "output_tokens", "thinking_tokens"):
        known_values = [
            usage[name]
            for usage in usage_rows
            if isinstance(usage, dict)
            and isinstance(usage.get(name), int)
            and not isinstance(usage.get(name), bool)
        ]
        known_token_sums[name] = sum(known_values)
        unknown_token_counts[name] = len(usage_rows) - len(known_values)
        tokens[name] = known_token_sums[name] if unknown_token_counts[name] == 0 else None
    usage_status_counts = {
        status: sum(_evidence_usage_status(row) == status for row in evidence_summary["rows"])
        for status in ("COMPLETE", "INCOMPLETE", "INVALID")
    }
    contexts = [
        row.get("context_tokens_estimated")
        for row in plan_rows
        if isinstance(row.get("context_tokens_estimated"), int)
        and not isinstance(row.get("context_tokens_estimated"), bool)
    ]
    reason_codes = sorted(
        set(block_summary["reason_code_counts"])
        | ({stop_reason} if stop_reason else set())
    )
    fingerprint_status = (
        "PASS"
        if run_manifest.get("implementation_fingerprints") == implementation_fingerprints()
        else "MISMATCH"
    )
    artifact_ok = (
        journal_summary["integrity_valid"]
        and response_receipt_integrity_valid
        and evidence_summary["integrity_valid"]
        and block_summary["integrity_valid"]
        and case_reconciliation_ok
        and fingerprint_status == "PASS"
        and _verify_seal(plan_path, identity=f"diagnostic-plan:{run_id}")
        and _verify_seal(provider_outputs_path, identity=f"provider-outputs:{cell.source}")
    )
    if not artifact_ok:
        stop_reason = stop_reason or "DIAGNOSTIC_ARTIFACT_INTEGRITY_FAILURE"
    all_terminal = terminal_count == len(plan_rows) and not not_run_count
    local_blocks = journal_summary["blocked_method_cases"]
    if not artifact_ok:
        status = "BLOCKED"
    elif interrupted_sequences:
        status = "INTERRUPTED_PARTIAL"
    elif stop_reason and not all_terminal:
        status = "PARTIAL"
    elif all_terminal and local_blocks:
        status = "COMPLETE_WITH_LOCAL_BLOCKS"
    elif all_terminal and provider_errors:
        status = "COMPLETE_WITH_PROVIDER_ERRORS"
    elif all_terminal:
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    summary = {
        "schema_version": "linkloom-public-memory-diagnostic-summary/v1",
        "run_id": run_id,
        "cell": cell.display_name,
        "mode": "DIAGNOSTIC",
        "status": status,
        "stop_reason": stop_reason,
        "provider_execution": "ALLOWED",
        "benchmark_construction": "ALLOWED",
        "retrieval_temporal_memory_execution": "ALLOWED",
        "artifact_persistence": "ALLOWED",
        "retry_policy": "ALLOWED",
        "gold_loading": "FORBIDDEN",
        "gold_reads": 0,
        "scoring": "DISABLED",
        "semantic_benchmark_verdict": "DISABLED",
        "lifecycle": {
            "planned_method_cases": len(plan_rows),
            "started_method_cases": journal_summary["started_method_cases"],
            "terminal_method_cases": terminal_count,
            "completed_method_cases": journal_summary["completed_method_cases"],
            "blocked_method_cases": journal_summary["blocked_method_cases"],
            "aborted_method_cases": journal_summary["aborted_method_cases"],
            "interrupted_method_cases": len(interrupted_sequences),
            "not_run_method_cases": not_run_count,
        },
        "provider_completion": {
            "generation_attempts": provider_stats["generation_attempts"],
            "accepted_responses": provider_stats["accepted_response_attempts"],
            "provider_error_attempts": provider_errors,
            "local_harness_block_attempts": provider_stats["local_harness_block_attempts"],
            "completion_rate": provider_stats["completion_rate"],
        },
        "harness_blocks": {
            "count": block_summary["block_count"],
            "method_cases": journal_summary["blocked_method_cases"],
            "reason_code_counts": block_summary["reason_code_counts"],
        },
        "retries": {
            "retry_attempts": len(retry_events),
            "retryable_failures": sum(
                event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
                and event.get("retryable_transient_failure") is True
                for event in journal_events
            ),
        },
        "context_sizes": {
            "method_cases": len(contexts),
            "estimated_tokens_total": sum(contexts),
            "estimated_tokens_min": min(contexts) if contexts else None,
            "estimated_tokens_max": max(contexts) if contexts else None,
        },
        "latency_ms": {
            "completed_attempts": len(latencies),
            "total": round(sum(latencies), 3),
            "maximum": round(max(latencies), 3) if latencies else None,
        },
        "tokens": {
            **tokens,
            "known_partial_token_sums": known_token_sums,
            "unknown_token_counts": unknown_token_counts,
            "usage_status_counts": usage_status_counts,
            "accepted_responses_without_usage": sum(
                row.get("usage_present") is not True for row in evidence_summary["rows"]
            ),
        },
        "cost": {
            "estimated_spend_upper_bound_usd": round(
                float((metrics or {}).get("estimated_spend_upper_bound_usd", sum(
                    float(event.get("attempt_cost_upper_bound_reserved_usd", 0.0))
                    for event in journal_events
                    if event.get("event") == "PROVIDER_ATTEMPT_STARTED"
                ))),
                9,
            ),
            "provider_reported_cost_usd": (metrics or {}).get("provider_reported_cost_usd"),
            "hard_cost_cap_usd": run_manifest.get("hard_cost_cap_usd"),
            "global_cost_cap_usd": run_manifest.get("global_cost_cap_usd"),
        },
        "artifact_integrity": {
            "status": "PASS" if artifact_ok else "FAIL",
            "response_receipts": "PASS" if response_receipt_integrity_valid else "FAIL",
            "journal": "PASS" if journal_summary["integrity_valid"] else "FAIL",
            "accepted_response_evidence": "PASS" if evidence_summary["integrity_valid"] else "FAIL",
            "clean_eval_blocks": "PASS" if block_summary["integrity_valid"] else "FAIL",
            "case_artifacts": "PASS" if case_reconciliation_ok else "FAIL",
            "diagnostic_plan_sealed": _verify_seal(plan_path, identity=f"diagnostic-plan:{run_id}"),
            "provider_outputs_sealed": _verify_seal(provider_outputs_path, identity=f"provider-outputs:{cell.source}"),
            "implementation_fingerprint": fingerprint_status,
        },
        "finalizer_checks": {
            "response_receipt_integrity": "PASS" if response_receipt_integrity_valid else "FAIL",
            "evidence_integrity": "PASS" if evidence_summary["integrity_valid"] else "FAIL",
            "usage_completeness": (
                "INCOMPLETE"
                if usage_status_counts["INCOMPLETE"] or usage_status_counts["INVALID"]
                else "COMPLETE"
            ),
            "journal_consistency": "PASS" if journal_summary["integrity_valid"] else "FAIL",
            "case_completion": "PASS" if all_terminal else "PARTIAL",
            "receipt_evidence_recovered": recovered_receipt_evidence,
            "receipt_recovery_errors": receipt_recovery_errors,
        },
        "reason_codes": reason_codes,
    }
    summary_path = root / "diagnostic_summary.json"
    _atomic_json_write_fsync(summary_path, summary)
    _seal_file(summary_path, identity=f"diagnostic-summary:{run_id}")
    run_manifest.update(
        {
            "status": status,
            "stop_reason": stop_reason,
            "diagnostic_finalized_at_utc": datetime.now(UTC).isoformat(),
            "diagnostic_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            "journal_protocol": journal_summary,
            "artifact_durability_status": "PASS" if artifact_ok else "FAIL",
        }
    )
    _write_run_manifest(root, run_manifest)
    _publish_artifact_manifest(root, run_id)
    return summary


def _release_recovered_diagnostic_budget_reservation(
    root: Path,
    summary: dict[str, Any],
) -> None:
    """Release a matching interrupted diagnostic reservation after artifact recovery."""

    try:
        run_manifest = json.loads((Path(root) / "run_manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise CleanEvalBlocked("DIAGNOSTIC_RUN_MANIFEST_UNREADABLE") from None
    run_id = run_manifest.get("run_id") if isinstance(run_manifest, dict) else None
    cell_key = run_manifest.get("cell_key") if isinstance(run_manifest, dict) else None
    if not isinstance(run_id, str) or cell_key not in CELLS:
        raise CleanEvalBlocked("DIAGNOSTIC_RUN_MANIFEST_INVALID")
    diagnostic_ledger_path = DIAGNOSTIC_DIR / "diagnostic_budget_ledger.json"
    ledger = _load_budget_ledger(
        path=diagnostic_ledger_path,
        hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD,
    )
    matching = [row for row in ledger.get("runs", []) if isinstance(row, dict) and row.get("run_id") == run_id]
    if (
        len(matching) != 1
        or matching[0].get("cell") != cell_key
        or matching[0].get("status") != "RUNNING"
        or ledger.get("active_run_id") != run_id
    ):
        raise CleanEvalBlocked("DIAGNOSTIC_BUDGET_RESERVATION_NOT_RECOVERABLE")
    estimated = summary.get("cost", {}).get("estimated_spend_upper_bound_usd")
    if isinstance(estimated, bool) or not isinstance(estimated, (int, float)) or estimated < 0:
        raise CleanEvalBlocked("DIAGNOSTIC_COST_SUMMARY_INVALID")
    matching[0]["implementation_fingerprint_sha256"] = run_manifest.get(
        "implementation_fingerprint_sha256"
    )
    _finish_budget_reservation(
        ledger,
        run_id,
        status=str(summary.get("status", "BLOCKED")),
        estimated_spend_upper_bound_usd=float(estimated),
        global_cap_usd=DIAGNOSTIC_TASK_CAP_USD,
        ledger_path=diagnostic_ledger_path,
    )


def _run_live(
    cell_key: str,
    *,
    preflight_dir: Path | None = None,
    allow_authorized_fresh_sh_v3: bool = False,
    diagnostic_stress: bool = False,
    diagnostic_mode: bool = False,
    evaluation_plan_id: str | None = None,
    rehearsal_mode: bool = False,
    rehearsal_root: Path | None = None,
    rehearsal_budget_ledger_path: Path | None = None,
) -> dict[str, Any]:
    if cell_key not in CELLS:
        raise CleanEvalBlocked("UNKNOWN_CLEAN_EVAL_CELL")
    if diagnostic_stress and diagnostic_mode:
        raise CleanEvalBlocked("DIAGNOSTIC_MODES_ARE_MUTUALLY_EXCLUSIVE")
    if rehearsal_mode and (
        diagnostic_stress
        or diagnostic_mode
        or evaluation_plan_id != E2_MH_ONLY_EVALUATION_PLAN_ID
        or cell_key != "mh_6k"
        or rehearsal_root is None
        or rehearsal_budget_ledger_path is None
    ):
        raise CleanEvalBlocked("FORMAL_REHEARSAL_CONFIGURATION_INVALID")
    if diagnostic_stress and cell_key != "sh_32k":
        raise CleanEvalBlocked("DIAGNOSTIC_STRESS_ONLY_ALLOWS_SH_32K")
    if (diagnostic_stress or diagnostic_mode) and evaluation_plan_id is not None:
        raise CleanEvalBlocked("FORMAL_EVALUATION_PLAN_NOT_ALLOWED_IN_DIAGNOSTIC_MODE")
    evaluation_plan = (
        None
        if diagnostic_stress or diagnostic_mode
        else _load_evaluation_plan(evaluation_plan_id or LEGACY_DUAL_EVALUATION_PLAN_ID)
    )
    if (
        evaluation_plan is not None
        and evaluation_plan.get("plan_id") == MH_ONLY_EVALUATION_PLAN_ID
        and cell_key != "mh_6k"
    ):
        raise CleanEvalBlocked("MH_ONLY_PLAN_REQUIRES_MH_6K")
    cell = CELLS[cell_key]
    e2_plan = evaluation_plan is not None and evaluation_plan.get("plan_id") == E2_MH_ONLY_EVALUATION_PLAN_ID
    if diagnostic_stress and preflight_dir is not None and Path(preflight_dir).resolve() != DIAGNOSTIC_DIR.resolve():
        raise CleanEvalBlocked("DIAGNOSTIC_PREFLIGHT_PATH_MISMATCH")
    resolved_preflight_dir = _resolve_eval_artifact_dir(
        DIAGNOSTIC_DIR if diagnostic_stress and preflight_dir is None
        else E2_QUALIFICATION_DIR if e2_plan and preflight_dir is None
        else preflight_dir
    )
    if diagnostic_stress:
        _verify_diagnostic_qualification_gates()
    route = verify_local_provider_route()
    if diagnostic_stress:
        route_path = DIAGNOSTIC_DIR / "H3_route_preflight.json"
        _atomic_json_write_fsync(route_path, route)
        _seal_file(route_path, identity="diagnostic-route-preflight")
        preflight, fingerprints = _verify_diagnostic_ready(preflight_dir=resolved_preflight_dir)
        baseline_path, diagnostic_baseline = _write_diagnostic_execution_baseline(
            preflight=preflight,
            fingerprints=fingerprints,
            route=route,
        )
        if implementation_fingerprints() != fingerprints:
            raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH_BEFORE_LIVE")
        budget_ledger_path = DIAGNOSTIC_DIR / "diagnostic_budget_ledger.json"
        ledger = _load_budget_ledger(path=budget_ledger_path, hard_cap_usd=DIAGNOSTIC_TASK_CAP_USD)
        run_hard_cost_cap = DIAGNOSTIC_RUN_CAP_USD
        global_hard_cost_cap = DIAGNOSTIC_TASK_CAP_USD
        authorization_baseline = None
        authorization_baseline_path = None
    else:
        preflight, fingerprints = _verify_ready(
            cell_key,
            preflight_dir=resolved_preflight_dir,
            evaluation_plan_id=evaluation_plan.get("plan_id") if evaluation_plan else None,
        )
        if e2_plan:
            if rehearsal_mode:
                authorization_baseline_path = None
                authorization_baseline = {"fingerprints": fingerprints}
            else:
                authorization_baseline_path = resolved_preflight_dir / "AUTHORIZED_FINAL_V1_EVALUATION_BASELINE.json"
                authorization_baseline = _verify_authorized_e2_baseline(
                    resolved_preflight_dir,
                    fingerprints,
                    evaluation_plan,
                )
        elif evaluation_plan is not None and evaluation_plan.get("plan_id") == MH_ONLY_EVALUATION_PLAN_ID:
            authorization_baseline_path = resolved_preflight_dir / "AUTHORIZED_MH_ONLY_CLEAN_BASELINE.json"
            authorization_baseline = _verify_authorized_mh_only_baseline(
                resolved_preflight_dir,
                fingerprints,
                evaluation_plan,
            )
        else:
            authorization_baseline = None
            authorization_baseline_path = resolved_preflight_dir / "AUTHORIZED_EXECUTION_BASELINE_V3.json"
            _verify_v3_live_authorization(resolved_preflight_dir, fingerprints)
        budget_ledger_path = (
            rehearsal_budget_ledger_path
            if rehearsal_mode and rehearsal_budget_ledger_path is not None
            else FORMAL_BUDGET_LEDGER_PATH
        )
        if rehearsal_mode:
            if budget_ledger_path.exists():
                raise CleanEvalBlocked("FORMAL_REHEARSAL_BUDGET_ARTIFACT_ALREADY_EXISTS")
            rehearsal_source_ledger = _load_budget_ledger(
                path=FORMAL_BUDGET_LEDGER_PATH,
                hard_cap_usd=GLOBAL_COST_CAP_USD,
            )
            _atomic_json_write_fsync(budget_ledger_path, rehearsal_source_ledger)
            _seal_file(budget_ledger_path, identity="formal-rehearsal-budget-ledger")
        ledger = _load_budget_ledger(
            path=budget_ledger_path,
            hard_cap_usd=GLOBAL_COST_CAP_USD,
        )
        if not rehearsal_mode and authorization_baseline is not None and not _authorization_baseline_budget_hash_matches(
            authorization_baseline,
            ledger,
            e2_plan=e2_plan,
        ):
            raise CleanEvalBlocked("MH_ONLY_FORMAL_BUDGET_SNAPSHOT_MISMATCH")
        run_hard_cost_cap = cell.hard_cost_cap_usd
        global_hard_cost_cap = GLOBAL_COST_CAP_USD
    starting_budget_ledger_sha256 = ledger.get("ledger_sha256")

    history = [row for row in ledger.get("runs", []) if isinstance(row, dict)]
    completed_prior_sh = [
        row for row in history
        if row.get("cell") == "sh_32k" and row.get("status") in {"COMPLETE", "COMPLETE_WITH_PROVIDER_ERRORS"}
    ]
    prior_sh_runs = [row for row in history if row.get("cell") == cell_key]
    legacy_plan = (
        evaluation_plan is not None
        and evaluation_plan.get("plan_id") == LEGACY_DUAL_EVALUATION_PLAN_ID
    )
    if not diagnostic_stress and not diagnostic_mode and legacy_plan and cell_key == "sh_32k":
        by_id = {row.get("run_id"): row for row in prior_sh_runs}
        if (
            not allow_authorized_fresh_sh_v3
            or len(prior_sh_runs) != 2
            or set(by_id) != {ORIGINAL_STOPPED_SH_RUN_ID, STOPPED_SH_V2_RUN_ID}
            or any(row.get("cell") == "mh_6k" for row in history)
            or not _is_original_stopped_sh_budget_row(by_id.get(ORIGINAL_STOPPED_SH_RUN_ID, {}))
        ):
            raise CleanEvalBlocked("FRESH_SH_V3_AUTHORIZATION_HISTORY_MISMATCH")
        _validate_original_stopped_sh_run(by_id[ORIGINAL_STOPPED_SH_RUN_ID])
        _validate_stopped_v2_sh_run(by_id[STOPPED_SH_V2_RUN_ID])
    if (
        not diagnostic_stress
        and not diagnostic_mode
        and legacy_plan
        and cell_key == "mh_6k"
        and len(completed_prior_sh) != 1
    ):
        raise CleanEvalBlocked("SH_32K_MUST_COMPLETE_BEFORE_MH_6K")
    if (
        not diagnostic_stress
        and not diagnostic_mode
        and legacy_plan
        and cell_key == "mh_6k"
        and completed_prior_sh[0].get("implementation_fingerprint_sha256") != fingerprints["files_sha256"]
    ):
        raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_CHANGED_BETWEEN_CELLS")

    planned_method_cases = len(preflight.get("execution_plan", []))
    raw_planned_questions = preflight.get("question_count")
    if raw_planned_questions is None and not e2_plan and planned_method_cases % len(METHODS) == 0:
        # Historical and synthetic fixtures may omit this cached count; the
        # sealed standard plans are independently checked against their frozen
        # selection in _verify_ready. E2 requires its explicit 92-QA contract.
        planned_questions = planned_method_cases // len(METHODS)
    elif isinstance(raw_planned_questions, int) and not isinstance(raw_planned_questions, bool):
        planned_questions = raw_planned_questions
    else:
        planned_questions = 0
    if planned_method_cases != planned_questions * len(METHODS) or planned_method_cases <= 0:
        raise CleanEvalBlocked("PREFLIGHT_METHOD_CASE_COUNT_INVALID")
    if e2_plan and (
        planned_questions != E2_SUBSET_QA_COUNT
        or planned_method_cases != E2_PLANNED_METHOD_CASES
        or preflight.get("subset_sha256") != evaluation_plan.get("subset_sha256")
    ):
        raise CleanEvalBlocked("E2_PRE_RUN_SUBSET_PLAN_MISMATCH")

    run_id = uuid4().hex
    root = (
        Path(rehearsal_root) / run_id
        if rehearsal_mode and rehearsal_root is not None
        else ARTIFACT_ROOT / "harness_stability_diagnostic" / cell_key / run_id
        if diagnostic_stress
        else ARTIFACT_ROOT / "diagnostic" / cell_key / run_id
        if diagnostic_mode
        else ARTIFACT_ROOT / cell_key / run_id
    )
    if root.exists():
        raise CleanEvalBlocked("RUN_ARTIFACT_DIRECTORY_ALREADY_EXISTS")
    root.mkdir(parents=True, exist_ok=False)
    if diagnostic_stress:
        _begin_budget_reservation(
            ledger,
            cell_key,
            run_id,
            global_cap_usd=global_hard_cost_cap,
            reservation_usd=run_hard_cost_cap,
            ledger_path=budget_ledger_path,
        )
    else:
        _begin_budget_reservation(
            ledger,
            cell_key,
            run_id,
            global_cap_usd=global_hard_cost_cap,
            reservation_usd=run_hard_cost_cap,
            ledger_path=budget_ledger_path,
        )
    ledger["runs"][-1]["implementation_fingerprint_sha256"] = fingerprints["files_sha256"]
    preflight_sha256 = hashlib.sha256(
        (resolved_preflight_dir / f"{cell_key}_preflight.json").read_bytes()
    ).hexdigest()
    ledger["runs"][-1]["preflight_sha256"] = preflight_sha256
    _write_budget_ledger(ledger, path=budget_ledger_path)

    plan_rows = preflight["execution_plan"]
    run_manifest: dict[str, Any] = {
        "schema_version": "linkloom-public-memory-live-run/v1",
        "run_id": run_id,
        "cell_key": cell_key,
        "diagnostic_mode": diagnostic_mode,
        "offline_rehearsal": rehearsal_mode,
        "evaluation_plan_id": evaluation_plan.get("plan_id") if evaluation_plan else None,
        "evaluation_plan_sha256": _evaluation_plan_sha256(evaluation_plan) if evaluation_plan else None,
        "evaluation_plan_registry_sha256": (
            fingerprints.get("files", {}).get(
                "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json"
            )
            if evaluation_plan
            else None
        ),
        "authorization_baseline_sha256": (
            hashlib.sha256(authorization_baseline_path.read_bytes()).hexdigest()
            if authorization_baseline_path is not None and authorization_baseline_path.is_file()
            else None
        ),
        "budget_ledger_sha256_at_run_start": starting_budget_ledger_sha256,
        "cell": cell.display_name,
        "source": cell.source,
        "planned_questions": planned_questions,
        "planned_method_cases": planned_method_cases,
        "subset_sha256": preflight.get("subset_sha256"),
        "subset_manifest_sha256": preflight.get("subset_manifest_sha256"),
        "revision_classification": (
            evaluation_plan.get("revision_classification") if e2_plan else None
        ),
        "status": "RUNNING",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "dataset_revision": preflight["dataset_revision"],
        "dataset_sha256": preflight["dataset_sha256"],
        "freeze_manifest_sha256": preflight["freeze_manifest_sha256"],
        "frozen_execution_plan_sha256": preflight["frozen_execution_plan_sha256"],
        "runtime_execution_plan_sha256": preflight["runtime_execution_plan_sha256"],
        "preflight_sha256": preflight_sha256,
        "implementation_fingerprints": fingerprints,
        "implementation_fingerprint_sha256": fingerprints["files_sha256"],
        "baseline_fingerprint_sha256": (
            (authorization_baseline.get("fingerprints") or {}).get("files_sha256")
            if isinstance(authorization_baseline, Mapping)
            else fingerprints["files_sha256"]
        ),
        "runtime_config_fingerprint_sha256": preflight.get("runtime_execution_plan_sha256"),
        "final_evaluation_policy_id": FINAL_EVALUATION_POLICY_ID if e2_plan else None,
        "minimum_scored_coverage_per_method": (
            FINAL_EVALUATION_MIN_SCORED_COVERAGE if e2_plan else None
        ),
        "minimum_paired_coverage": FINAL_EVALUATION_MIN_PAIRED_COVERAGE if e2_plan else None,
        "provider": "Gemini Developer API",
        "model": MODEL,
        "sdk_version": SDK_VERSION,
        "pricing_source": PRICING_SOURCE_URL,
        "hard_cost_cap_usd": run_hard_cost_cap,
        "global_cost_cap_usd": global_hard_cost_cap,
        "budget_scope": "DIAGNOSTIC_TASK_10_USD" if diagnostic_stress else "FORMAL_EVALUATION_30_USD",
        "route_preflight": route,
        "gold_values_read": False,
        "scoring": "DISABLED" if diagnostic_stress or diagnostic_mode else "ENABLED_AFTER_SEAL",
        **({} if diagnostic_mode else {"accuracy_claim": False if diagnostic_stress else True}),
        "purpose": (
            "HARNESS_DIAGNOSTIC" if diagnostic_mode
            else "HARNESS_STABILITY" if diagnostic_stress
            else "CLEAN_EVALUATION"
        ),
        "diagnostic_execution_baseline_sha256": (
            hashlib.sha256(baseline_path.read_bytes()).hexdigest() if diagnostic_stress else None
        ),
        "output_sealed_before_scoring": not (diagnostic_stress or diagnostic_mode),
        "count_tokens_fallback_policy": "SAFE_COUNT_TOKENS_FALLBACK_TRANSPORT_ONLY",
    }
    _write_run_manifest(root, run_manifest)
    if diagnostic_mode:
        _write_diagnostic_plan(
            root,
            run_id=run_id,
            cell_key=cell_key,
            plan_rows=plan_rows,
        )

    attempt_journal_path = root / "provider_attempt_journal.jsonl"
    attempt_journal_path.write_text("", encoding="utf-8")
    accepted_response_evidence_path = root / "accepted_response_evidence.jsonl"
    with accepted_response_evidence_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    case: Any | None = None
    memory: Any | None = None
    output_by_sequence: dict[int, dict[str, Any]] = {}
    stop_reason: str | None = None
    planned_costs: list[float] = []
    if any(
        not _finite_nonnegative_usd(row.get("one_attempt_cost_upper_bound_usd"), allow_zero=False)
        for row in plan_rows
    ):
        stop_reason = "DIAGNOSTIC_COST_RESERVATION_UNAVAILABLE"
    else:
        planned_costs = [float(row["one_attempt_cost_upper_bound_usd"]) for row in plan_rows]
    spent_upper_bound = 0.0
    failure_count = 0
    aggregate: Any = None
    score_report: dict[str, Any] | None = None
    scored_summary: dict[str, Any] | None = None
    score_status = "DISABLED_DIAGNOSTIC_ONLY" if diagnostic_stress or diagnostic_mode else "NOT_RUN"
    score_error: str | None = None
    harness_traceback: dict[str, Any] | None = None
    gold_values_read = False
    accepted_response_sequences: set[int] = set()

    try:
        rebuild_options = {
            "preflight_dir": resolved_preflight_dir,
        }
        if e2_plan:
            rebuild_options.update(
                {
                    "evaluation_plan_id": evaluation_plan.get("plan_id"),
                    "run_id": run_id,
                }
            )
        case, rebuilt_preflight, prepared_rows, memory = _rebuild_runtime_objects(
            cell_key,
            **rebuild_options,
        )
        if rebuilt_preflight["implementation_fingerprints"] != fingerprints:
            raise CleanEvalBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH_BEFORE_LIVE")
        from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

        aggregate = gemini.AggregateUsage()
        count_tokens_fallback_state = gemini.CountTokensFallbackState()
        aggregate_budget = gemini.AggregateBudget(
            max_provider_requests=planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_model_turns=planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_input_tokens=INPUT_TOKEN_CAP * planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_output_tokens=OUTPUT_TOKEN_CAP * planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_elapsed_provider_seconds=REQUEST_TIMEOUT_SECONDS * planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_cost_usd=run_hard_cost_cap,
            max_preflight_requests=planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_preflight_counted_input_tokens=INPUT_TOKEN_CAP * planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
            max_elapsed_preflight_seconds=REQUEST_TIMEOUT_SECONDS * planned_method_cases * MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
        )
        questions_by_id = {question.question_id: question for question in case.questions}
        if len(prepared_rows) != planned_method_cases or len({(row.question_id, row.method) for row in prepared_rows}) != planned_method_cases:
            raise CleanEvalBlocked("RUNTIME_METHOD_CASE_SET_INVALID")

        for index, (plan, prepared) in enumerate(zip(plan_rows, prepared_rows, strict=True)):
            if stop_reason is not None:
                break
            sequence = int(plan["sequence"])
            qa_id = str(plan["qa_id"])
            method = str(plan["method"])
            if (
                prepared.question_id != qa_id
                or prepared.method != method
                or prepared.workspace_id != case.workspace_id
                or questions_by_id[qa_id].question != prepared.question
            ):
                raise CleanEvalBlocked("RUNTIME_CASE_IDENTITY_MISMATCH")
            request = build_generation_request(prepared.context_bundle.rendered_text)
            from benchmarks.memoryagentbench.g32_heldout import count_tokens_request_upper_bound

            generation_hash, count_hash, _ = count_tokens_request_upper_bound(request)
            context_hash = hashlib.sha256(prepared.context_bundle.rendered_text.encode("utf-8")).hexdigest()
            if (
                generation_hash != plan["generation_request_sha256"]
                or count_hash != plan["count_tokens_request_sha256"]
                or context_hash != plan["context_sha256"]
                or request["model"] != MODEL
            ):
                raise CleanEvalBlocked("FROZEN_REQUEST_HASH_MISMATCH")
            _safe_fact_repr = request
            if _contains_gold_payload_field(_safe_fact_repr):
                raise CleanEvalBlocked("GOLD_LEAKAGE_IN_GENERATION_REQUEST")

            _append_attempt_journal(
                attempt_journal_path,
                {
                    "event": "CASE_METHOD_STARTED",
                    "run_id": run_id,
                    "sequence": sequence,
                    "qa_id": qa_id,
                    "method": method,
                    "generation_request_sha256": generation_hash,
                    "count_tokens_request_sha256": count_hash,
                    "context_sha256": context_hash,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                },
            )

            request_cost = planned_costs[index]
            future_base_cost = sum(planned_costs[index + 1 :])
            attempt_count = 0
            case_success: dict[str, Any] | None = None
            case_local_block = False
            last_error_safe: dict[str, Any] | None = None
            last_count_tokens_telemetry: dict[str, Any] = {
                "count_tokens_outcome": "NOT_STARTED",
                "count_tokens_status": "NOT_STARTED",
                "count_tokens_token_estimation_source": "NOT_STARTED",
                "count_tokens_fallback_reason": None,
                "static_conservative_input_token_estimate": None,
                "billing_preflight_uncertainty": False,
                "count_tokens_transport_attempts": 0,
            }
            if rehearsal_mode:
                stop_reason = "FORMAL_LIVE_PATH_REHEARSAL_STOP_BEFORE_PROVIDER"
            while attempt_count < MAX_ATTEMPTS_PER_LOGICAL_GENERATION and stop_reason is None:
                next_attempt_no = attempt_count + 1
                if implementation_fingerprints() != fingerprints:
                    stop_reason = "IMPLEMENTATION_FINGERPRINT_MISMATCH_DURING_RUN"
                    break
                projected_cell_cost = spent_upper_bound + request_cost + future_base_cost
                projected_global_cost = (
                    _global_committed_excluding(ledger, run_id)
                    + spent_upper_bound
                    + request_cost
                    + future_base_cost
                )
                if not _cost_is_below_cap(projected_cell_cost, run_hard_cost_cap):
                    stop_reason = (
                        "RETRY_BLOCKED_BY_CELL_COST_GUARD"
                        if next_attempt_no > 1
                        else "CELL_DYNAMIC_COST_GUARD_BLOCKED"
                    )
                    break
                if not _cost_is_below_cap(projected_global_cost, global_hard_cost_cap):
                    stop_reason = (
                        "RETRY_BLOCKED_BY_GLOBAL_COST_GUARD"
                        if next_attempt_no > 1
                        else "GLOBAL_DYNAMIC_COST_GUARD_BLOCKED"
                    )
                    break
                reservation_candidate = {
                    "attempt_cost_upper_bound_reserved_usd": request_cost,
                    "static_input_token_upper_bound": plan.get("static_input_token_upper_bound"),
                    "projected_cell_cost_upper_bound_usd": projected_cell_cost,
                    "projected_global_cost_upper_bound_usd": projected_global_cost,
                }
                if not _diagnostic_cost_reservation_is_safe(
                    reservation_candidate,
                    plan,
                    run_hard_cost_cap=run_hard_cost_cap,
                    global_hard_cost_cap=global_hard_cost_cap,
                ):
                    stop_reason = "DIAGNOSTIC_COST_RESERVATION_UNAVAILABLE"
                    break
                attempt_count = next_attempt_no
                spent_upper_bound += request_cost
                attempt_started = time.monotonic()
                attempt_id = f"{sequence}:{attempt_count}"
                started_event = {
                    "event": "PROVIDER_ATTEMPT_STARTED",
                    "run_id": run_id,
                    "sequence": sequence,
                    "qa_id": qa_id,
                    "method": method,
                    "attempt_no": attempt_count,
                    "attempt_id": attempt_id,
                    "generation_request_sha256": generation_hash,
                    "count_tokens_request_sha256": count_hash,
                    "provider_response_accepted": False,
                    "static_input_token_upper_bound": plan["static_input_token_upper_bound"],
                    "attempt_cost_upper_bound_reserved_usd": request_cost,
                    "projected_cell_cost_upper_bound_usd": projected_cell_cost,
                    "projected_global_cost_upper_bound_usd": projected_global_cost,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                }
                _append_attempt_journal(attempt_journal_path, started_event)
                guard = None
                client = None
                response_evidence: dict[str, Any] | None = None
                response: Any | None = None
                response_text: str | None = None
                attempt_response_accepted = False
                attempt_completion_written = False
                provider_requests_before = aggregate.provider_requests
                provider_request_sent = False
                try:
                    def persist_response_acceptance(response_object: Any) -> None:
                        nonlocal attempt_response_accepted, response_evidence
                        attempt_response_accepted = True
                        accepted_response_sequences.add(sequence)

                        def record_response_received(evidence: dict[str, Any]) -> None:
                            _append_attempt_journal(
                                attempt_journal_path,
                                {
                                    **started_event,
                                    "event": "RESPONSE_RECEIVED",
                                    "provider_response_accepted": True,
                                    "request_sha256": evidence["request_sha256"],
                                    "response_sha256": evidence["response_sha256"],
                                    "model": evidence["model"],
                                    "usage_present": evidence["usage_present"],
                                    "usage_metadata": evidence["usage_metadata"],
                                    "usage_status": evidence["usage_status"],
                                    "usage_missing_fields": evidence["usage_missing_fields"],
                                    "usage_sources": evidence["usage_sources"],
                                    "provider_usage_fields": evidence["provider_usage_fields"],
                                    "provider_usage_object_present": evidence[
                                        "provider_usage_object_present"
                                    ],
                                    "usage_invalid_fields": evidence["usage_invalid_fields"],
                                    "usage_access_failures": evidence["usage_access_failures"],
                                    "billing_uncertainty": evidence["billing_uncertainty"],
                                    "billing_basis": evidence["billing_basis"],
                                    "provider_reported_cost_usd": evidence[
                                        "provider_reported_cost_usd"
                                    ],
                                    "provider_reported_cost_source": evidence[
                                        "provider_reported_cost_source"
                                    ],
                                    "provider_request_id": evidence["provider_request_id"],
                                    "accepted_at_utc": evidence["accepted_at_utc"],
                                },
                            )

                        response_evidence = _persist_accepted_response_evidence(
                            accepted_response_evidence_path,
                            run_id=run_id,
                            sequence=sequence,
                            logical_case_id=qa_id,
                            method=method,
                            attempt_no=attempt_count,
                            logical_attempt_id=attempt_id,
                            model=request["model"],
                            request_hash=generation_hash,
                            response=response_object,
                            on_response_received=record_response_received,
                        )
                        evidence_sha256 = canonical_sha256(response_evidence)
                        _append_attempt_journal(
                            attempt_journal_path,
                            {
                                **started_event,
                                "event": "RESPONSE_EVIDENCE_PERSISTED",
                                "provider_response_accepted": True,
                                "request_sha256": response_evidence["request_sha256"],
                                "response_sha256": response_evidence["response_sha256"],
                                "evidence_sha256": evidence_sha256,
                                "accepted_at_utc": response_evidence["accepted_at_utc"],
                            },
                        )
                        _append_attempt_journal(
                            attempt_journal_path,
                            {
                                **started_event,
                                "event": "USAGE_VALIDATED",
                                "provider_response_accepted": True,
                                "request_sha256": response_evidence["request_sha256"],
                                "response_sha256": response_evidence["response_sha256"],
                                "usage_present": response_evidence["usage_present"],
                                "usage_metadata": response_evidence["usage_metadata"],
                                "usage_status": response_evidence["usage_status"],
                                "usage_missing_fields": response_evidence["usage_missing_fields"],
                                "usage_sources": response_evidence["usage_sources"],
                                "provider_usage_fields": response_evidence[
                                    "provider_usage_fields"
                                ],
                                "usage_invalid_fields": response_evidence[
                                    "usage_invalid_fields"
                                ],
                                "usage_access_failures": response_evidence[
                                    "usage_access_failures"
                                ],
                                "billing_uncertainty": response_evidence[
                                    "billing_uncertainty"
                                ],
                                "billing_basis": response_evidence["billing_basis"],
                                "accepted_at_utc": response_evidence["accepted_at_utc"],
                            },
                        )
                        if response_evidence["usage_status"] == "INVALID":
                            raise CleanEvalBlocked("PROVIDER_USAGE_MALFORMED")
                        if not _is_sha256_hex(response_evidence.get("response_sha256")):
                            raise CleanEvalBlocked("ARTIFACT_RESPONSE_HASH_UNAVAILABLE")

                    guard, client = _prepare_generation_call(
                        run_id=run_id,
                        workspace_id=case.workspace_id,
                        question=prepared.question,
                        request=request,
                        attempt_no=attempt_count,
                        aggregate=aggregate,
                        aggregate_budget=aggregate_budget,
                        count_tokens_fallback_state=count_tokens_fallback_state,
                        on_response_accepted=persist_response_acceptance,
                    )
                    response = guard.generate_content(
                        model=request["model"],
                        contents=request["contents"],
                        config=request["config"],
                    )
                    provider_request_sent = True
                    if not attempt_response_accepted:
                        attempt_response_accepted = True
                        raise CleanEvalBlocked("ARTIFACT_RESPONSE_ACCEPTANCE_EVIDENCE_MISSING")
                    if response_evidence is None:
                        raise CleanEvalBlocked("ARTIFACT_RESPONSE_ACCEPTANCE_EVIDENCE_MISSING")
                    if not response_evidence.get("response_sha256"):
                        raise CleanEvalBlocked("ARTIFACT_RESPONSE_HASH_UNAVAILABLE")
                    if not guard.preflight_records:
                        raise CleanEvalBlocked("COUNT_TOKENS_ATTEMPT_NOT_JOURNALED")
                    count_record = guard.preflight_records[-1]
                    count_telemetry = _count_tokens_telemetry(
                        count_record,
                        fallback_state=count_tokens_fallback_state,
                        gemini=gemini,
                    )
                    last_count_tokens_telemetry = count_telemetry
                    response_text = _response_text(response)
                    if os.environ["GEMINI_API_KEY"] in response_text:
                        raise CleanEvalBlocked("CREDENTIAL_LEAK_BLOCKED")
                    input_tokens, output_tokens, thinking_tokens, provider_cost = gemini._reported_usage(response)
                    if response_evidence["usage_status"] == "INCOMPLETE" and not _diagnostic_cost_reservation_is_safe(
                        started_event,
                        plan,
                        run_hard_cost_cap=run_hard_cost_cap,
                        global_hard_cost_cap=global_hard_cost_cap,
                    ):
                        raise CleanEvalBlocked("DYNAMIC_COST_BOUND_UNAVAILABLE")
                    if (
                        response_evidence["usage_status"] == "INCOMPLETE"
                        and not e2_plan
                    ):
                        # Keep the already-frozen formal protocol unchanged.
                        # E2 explicitly preregisters incomplete-but-bounded
                        # usage as scoreable; earlier formal plans do not.
                        raise CleanEvalBlocked("PROVIDER_USAGE_UNAVAILABLE")
                    case_success = {
                        "sequence": sequence,
                        "qa_id": qa_id,
                        "method": method,
                        "provider_outcome": "PROVIDER_COMPLETE",
                        "provider_response_accepted": True,
                        "provider_attempts": attempt_count,
                        **({} if diagnostic_mode else {"response_text": response_text}),
                        "response_sha256": response_evidence["response_sha256"],
                        "request_sha256": generation_hash,
                        "count_tokens_request_sha256": count_hash,
                        "context_sha256": context_hash,
                        "context_tokens_estimated": plan["context_tokens_estimated"],
                        "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
                        "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
                        "memory_lookup_status": plan["memory_lookup_status"],
                        "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
                        "counted_input_tokens": count_record.get("counted_input_tokens"),
                        **count_telemetry,
                        "provider_input_tokens": input_tokens,
                        "provider_output_tokens": output_tokens,
                        "provider_thinking_tokens": thinking_tokens,
                        "usage_status": response_evidence["usage_status"],
                        "billing_uncertainty": response_evidence["billing_uncertainty"],
                        "provider_reported_cost_usd": provider_cost,
                        "provider_elapsed_ms": round((time.monotonic() - attempt_started) * 1000.0, 3),
                        "attempt_cost_upper_bound_usd": round(request_cost, 9),
                    }
                    output_by_sequence[sequence] = case_success
                    _append_attempt_journal(
                        attempt_journal_path,
                        {
                            **started_event,
                            "event": "PROVIDER_ATTEMPT_COMPLETED",
                            "provider_outcome": "PROVIDER_COMPLETE",
                            "provider_request_sent": provider_request_sent,
                            "counted_input_tokens": count_record.get("counted_input_tokens"),
                            **count_telemetry,
                            "provider_input_tokens": input_tokens,
                            "provider_output_tokens": output_tokens,
                            "provider_thinking_tokens": thinking_tokens,
                            "usage_status": response_evidence["usage_status"],
                            "usage_missing_fields": response_evidence["usage_missing_fields"],
                            "billing_uncertainty": response_evidence["billing_uncertainty"],
                            "billing_basis": response_evidence["billing_basis"],
                            "provider_response_accepted": True,
                            "provider_reported_cost_usd": provider_cost,
                            "response_sha256": case_success["response_sha256"],
                            "provider_elapsed_ms": case_success["provider_elapsed_ms"],
                        },
                    )
                    attempt_completion_written = True
                    _record_case_result(root, case_success)
                    break
                except Exception as error:
                    provider_request_sent = provider_request_sent or (
                        aggregate.provider_requests > provider_requests_before
                    )
                    safe = _safe_provider_error(
                        error,
                        guard,
                        logical_case_id=qa_id,
                        method=method,
                        response_evidence=response_evidence,
                        provider_response_accepted=attempt_response_accepted,
                    )
                    last_error_safe = safe
                    failed_provider_outcome = _failed_attempt_provider_outcome(safe)
                    local_failure = safe.get("failure_classification") == "LOCAL/HARNESS BLOCK"
                    if local_failure:
                        case_local_block = True
                        case_success = None
                        output_by_sequence.pop(sequence, None)
                    failure_classification_fields = (
                        {
                            "failure_classification": safe["failure_classification"],
                            "reason_code": safe["reason_code"],
                        }
                        if safe.get("failure_classification") == "LOCAL/HARNESS BLOCK"
                        else {}
                    )
                    count_record = (
                        guard.preflight_records[-1]
                        if guard is not None and guard.preflight_records
                        else {}
                    )
                    if count_record:
                        try:
                            last_count_tokens_telemetry = _count_tokens_telemetry(
                                count_record,
                                fallback_state=count_tokens_fallback_state,
                                gemini=gemini,
                                allow_failure=True,
                            )
                        except CleanEvalBlocked as telemetry_error:
                            last_count_tokens_telemetry = {
                                "count_tokens_outcome": "POLICY_REJECTED",
                                "count_tokens_status": count_record.get("count_tokens_status", "UNAVAILABLE"),
                                "count_tokens_token_estimation_source": count_record.get("token_estimation_source", "UNAVAILABLE"),
                                "count_tokens_fallback_reason": count_record.get("fallback_reason"),
                                "static_conservative_input_token_estimate": count_record.get("estimated_input_token_upper_bound"),
                                "billing_preflight_uncertainty": count_record.get("billing_preflight_uncertainty", False),
                                "count_tokens_transport_attempts": count_record.get("transport_attempts", 0),
                                "clean_eval_block": _safe_clean_eval_block(
                                    telemetry_error,
                                    guard,
                                    logical_case_id=qa_id,
                                    method=method,
                                ),
                            }
                    retry_classification = classify_retryable_failure(error, safe)
                    safe["retry_classification"] = retry_classification or "NON_RETRYABLE"
                    transient = (
                        retry_classification is not None
                        and not isinstance(error, CleanEvalBlocked)
                        and not attempt_response_accepted
                    )
                    if str(error) == "COUNT_TOKENS_FALLBACK_POLICY_MISMATCH":
                        transient = False
                        stop_reason = "COUNT_TOKENS_FALLBACK_POLICY_MISMATCH"
                    if (
                        local_failure
                        and attempt_response_accepted
                        and response_evidence is not None
                        and not attempt_completion_written
                        and e2_plan
                        and not (diagnostic_stress or diagnostic_mode)
                    ):
                        reason_code = safe.get("reason_code", "")
                        criticality = _guard_criticality_level(reason_code)
                        candidate_response_text = response_text
                        if candidate_response_text is None and response is not None:
                            try:
                                candidate_response_text = _response_text(response)
                            except CleanEvalBlocked:
                                candidate_response_text = None
                        usage_metadata = response_evidence.get("usage_metadata")
                        if not isinstance(usage_metadata, dict):
                            usage_metadata = {}
                        cost_safe = _diagnostic_cost_reservation_is_safe(
                            started_event,
                            plan,
                            run_hard_cost_cap=run_hard_cost_cap,
                            global_hard_cost_cap=global_hard_cost_cap,
                        )
                        evidence_valid = (
                            _accepted_response_evidence_is_durable(
                                accepted_response_evidence_path,
                                response_evidence,
                                run_id=run_id,
                                sequence=sequence,
                                attempt_no=attempt_count,
                                request_hash=generation_hash,
                            )
                            and response_evidence.get("case_id") == qa_id
                            and response_evidence.get("method") == method
                            and response_evidence.get("model") == request.get("model")
                            and _journal_usage_is_valid(response_evidence)
                            and _evidence_usage_status(response_evidence) in {"COMPLETE", "INCOMPLETE"}
                        )
                        response_valid = (
                            isinstance(candidate_response_text, str)
                            and bool(candidate_response_text.strip())
                            and os.environ.get("GEMINI_API_KEY") not in candidate_response_text
                        )
                        policy_action = _evaluation_guard_decision(
                            criticality,
                            phase="POST_RESPONSE",
                            provider_response_accepted=True,
                            response_valid=response_valid,
                            evidence_valid=evidence_valid,
                            cost_upper_bound_valid=cost_safe,
                            dataset_identity_valid=True,
                            scoring_identity_valid=True,
                        )
                        if (
                            policy_action in {"CONTINUE_WITH_DEGRADED_TELEMETRY", "WARNING_ONLY"}
                            and response_valid
                            and evidence_valid
                            and cost_safe
                        ):
                            provider_usage = usage_metadata
                            provider_elapsed_ms = round(
                                (time.monotonic() - attempt_started) * 1000.0,
                                3,
                            )
                            case_success = {
                                "sequence": sequence,
                                "qa_id": qa_id,
                                "method": method,
                                "provider_outcome": "PROVIDER_COMPLETE",
                                "provider_response_accepted": True,
                                "provider_attempts": attempt_count,
                                **({} if diagnostic_mode else {"response_text": candidate_response_text}),
                                "response_sha256": response_evidence["response_sha256"],
                                "request_sha256": generation_hash,
                                "count_tokens_request_sha256": count_hash,
                                "context_sha256": context_hash,
                                "context_tokens_estimated": plan["context_tokens_estimated"],
                                "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
                                "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
                                "memory_lookup_status": plan["memory_lookup_status"],
                                "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
                                "counted_input_tokens": count_record.get("counted_input_tokens"),
                                **last_count_tokens_telemetry,
                                "provider_input_tokens": provider_usage.get("input_tokens"),
                                "provider_output_tokens": provider_usage.get("output_tokens"),
                                "provider_thinking_tokens": provider_usage.get("thinking_tokens"),
                                "usage_status": _evidence_usage_status(response_evidence),
                                "billing_uncertainty": response_evidence.get("billing_uncertainty", True),
                                "provider_reported_cost_usd": response_evidence.get(
                                    "provider_reported_cost_usd"
                                ),
                                "provider_elapsed_ms": provider_elapsed_ms,
                                "attempt_cost_upper_bound_usd": round(request_cost, 9),
                            }
                            output_by_sequence[sequence] = case_success
                            _append_attempt_journal(
                                attempt_journal_path,
                                {
                                    **started_event,
                                    "event": "PROVIDER_ATTEMPT_COMPLETED",
                                    "provider_outcome": "PROVIDER_COMPLETE",
                                    "provider_request_sent": provider_request_sent,
                                    "counted_input_tokens": count_record.get(
                                        "counted_input_tokens", "UNAVAILABLE"
                                    ),
                                    **last_count_tokens_telemetry,
                                    "provider_input_tokens": provider_usage.get("input_tokens"),
                                    "provider_output_tokens": provider_usage.get("output_tokens"),
                                    "provider_thinking_tokens": provider_usage.get("thinking_tokens"),
                                    "usage_status": _evidence_usage_status(response_evidence),
                                    "usage_missing_fields": response_evidence.get(
                                        "usage_missing_fields", []
                                    ),
                                    "billing_uncertainty": response_evidence.get(
                                        "billing_uncertainty", True
                                    ),
                                    "billing_basis": response_evidence.get("billing_basis"),
                                    "provider_response_accepted": True,
                                    "provider_reported_cost_usd": response_evidence.get(
                                        "provider_reported_cost_usd"
                                    ),
                                    "response_sha256": response_evidence["response_sha256"],
                                    "provider_elapsed_ms": provider_elapsed_ms,
                                    "degraded_telemetry_guard_reason_code": reason_code,
                                },
                            )
                            attempt_completion_written = True
                            journal_after_degraded_success = _validate_attempt_journal_events(
                                _read_attempt_journal(attempt_journal_path),
                                expected_case_count=len(plan_rows),
                                scoring_required=True,
                            )
                            if not journal_after_degraded_success["integrity_valid"]:
                                stop_reason = "DEGRADED_TELEMETRY_ACCEPTED_RESPONSE_JOURNAL_INVALID"
                                case_success = None
                            else:
                                case_local_block = False
                                _record_case_result(root, case_success)
                            break
                    if not attempt_completion_written:
                        _append_attempt_journal(
                            attempt_journal_path,
                            {
                                **started_event,
                                "event": "PROVIDER_ATTEMPT_COMPLETED",
                                "provider_outcome": failed_provider_outcome,
                                "provider_request_sent": provider_request_sent,
                                **failure_classification_fields,
                                "response_sha256": response_evidence.get("response_sha256") if response_evidence else None,
                                "provider_response_accepted": attempt_response_accepted,
                                "counted_input_tokens": count_record.get("counted_input_tokens", "UNAVAILABLE"),
                                **last_count_tokens_telemetry,
                                "retryable_transient_failure": transient,
                                "safe_error": safe,
                                "elapsed_ms": round((time.monotonic() - attempt_started) * 1000.0, 3),
                            },
                        )
                        attempt_completion_written = True
                    if local_failure:
                        _persist_clean_eval_block(
                            root,
                            run_id=run_id,
                            sequence=sequence,
                            case_id=qa_id,
                            method=method,
                            attempt_no=attempt_count,
                            safe_block=safe,
                            request_hash=generation_hash,
                            response_evidence=response_evidence,
                        )
                    if not transient:
                        if safe.get("failure_classification") == "LOCAL/HARNESS BLOCK":
                            reason_code = safe["reason_code"]
                            journal_before_terminal = _validate_attempt_journal_events(
                                _read_attempt_journal(attempt_journal_path),
                                expected_case_count=len(plan_rows),
                                scoring_required=not (diagnostic_stress or diagnostic_mode),
                            )
                            fatal = _diagnostic_guard_is_fatal(
                                reason_code,
                                response_evidence=response_evidence,
                                provider_response_accepted=attempt_response_accepted,
                                journal_integrity_valid=journal_before_terminal["integrity_valid"],
                                cost_reservation_safe=_diagnostic_cost_reservation_is_safe(
                                    started_event,
                                    plan,
                                    run_hard_cost_cap=run_hard_cost_cap,
                                    global_hard_cost_cap=global_hard_cost_cap,
                                ),
                            )
                            if diagnostic_stress or diagnostic_mode:
                                if fatal:
                                    stop_reason = stop_reason or reason_code
                            elif not e2_plan:
                                # Keep the previously authorized MH_ONLY_CLEAN_V1
                                # protocol intact. Safe degradation applies only
                                # to the new final E2 policy frozen for this run.
                                stop_reason = stop_reason or reason_code
                            else:
                                cost_safe = _diagnostic_cost_reservation_is_safe(
                                    started_event,
                                    plan,
                                    run_hard_cost_cap=run_hard_cost_cap,
                                    global_hard_cost_cap=global_hard_cost_cap,
                                )
                                evidence_valid = (
                                    not attempt_response_accepted
                                    or (
                                        _accepted_response_evidence_is_durable(
                                            accepted_response_evidence_path,
                                            response_evidence,
                                            run_id=run_id,
                                            sequence=sequence,
                                            attempt_no=attempt_count,
                                            request_hash=generation_hash,
                                        )
                                        and journal_before_terminal["integrity_valid"]
                                    )
                                )
                                decision = _evaluation_guard_decision(
                                    _guard_criticality_level(reason_code),
                                    phase="POST_RESPONSE" if attempt_response_accepted else "PROVIDER_ATTEMPT",
                                    provider_response_accepted=attempt_response_accepted,
                                    response_valid=bool(
                                        isinstance(response_text, str) and response_text.strip()
                                    ),
                                    evidence_valid=evidence_valid,
                                    cost_upper_bound_valid=cost_safe,
                                    dataset_identity_valid=True,
                                    scoring_identity_valid=True,
                                )
                                if decision == "RUN_HARD_STOP":
                                    stop_reason = stop_reason or reason_code
                        else:
                            cost_safe = _diagnostic_cost_reservation_is_safe(
                                started_event,
                                plan,
                                run_hard_cost_cap=run_hard_cost_cap,
                                global_hard_cost_cap=global_hard_cost_cap,
                            )
                            decision = _evaluation_guard_decision(
                                "SCOREABILITY_CRITICAL",
                                phase="PROVIDER_ATTEMPT",
                                provider_response_accepted=attempt_response_accepted,
                                response_valid=False,
                                evidence_valid=not attempt_response_accepted,
                                cost_upper_bound_valid=cost_safe,
                                dataset_identity_valid=True,
                                scoring_identity_valid=True,
                            )
                            if decision == "RUN_HARD_STOP":
                                stop_reason = stop_reason or safe["reason_code"]
                        break
                    if attempt_count >= MAX_ATTEMPTS_PER_LOGICAL_GENERATION:
                        break
                    time.sleep(0.5 * (2 ** (attempt_count - 1)) + random.uniform(0.0, 0.25))
                finally:
                    if aggregate is not None:
                        try:
                            spent_upper_bound = max(
                                spent_upper_bound,
                                _spent_upper_bound_usd(aggregate),
                            )
                        except (ArithmeticError, TypeError, ValueError):
                            stop_reason = stop_reason or "DYNAMIC_COST_BOUND_UNAVAILABLE"
                        if not _cost_is_below_cap(spent_upper_bound, run_hard_cost_cap):
                            stop_reason = stop_reason or "DYNAMIC_COST_BOUND_EXCEEDED"
                        else:
                            projected_global_cost = (
                                _global_committed_excluding(ledger, run_id)
                                + spent_upper_bound
                            )
                            if not _cost_is_below_cap(projected_global_cost, global_hard_cost_cap):
                                retry_blocked = (
                                    not attempt_response_accepted
                                    and attempt_count < MAX_ATTEMPTS_PER_LOGICAL_GENERATION
                                    and last_error_safe is not None
                                    and last_error_safe.get("retry_classification")
                                    not in {None, "NON_RETRYABLE"}
                                )
                                stop_reason = stop_reason or (
                                    "RETRY_BLOCKED_BY_GLOBAL_COST_GUARD"
                                    if retry_blocked
                                    else "DYNAMIC_COST_BOUND_EXCEEDED"
                                )
                    if client is not None:
                        client.close()

            if stop_reason is not None:
                if attempt_count == 0:
                    _append_attempt_journal(
                        attempt_journal_path,
                        {
                            "event": "CASE_METHOD_ABORTED",
                            "run_id": run_id,
                            "sequence": sequence,
                            "qa_id": qa_id,
                            "method": method,
                            "reason": stop_reason,
                            "timestamp_utc": datetime.now(UTC).isoformat(),
                        },
                    )
                elif case_success is None:
                    failure_count += 1
                    failed_provider_outcome = _failed_attempt_provider_outcome(
                        last_error_safe or {}
                    )
                    failure_classification_fields = (
                        {
                            "failure_classification": last_error_safe["failure_classification"],
                            "reason_code": last_error_safe["reason_code"],
                        }
                        if last_error_safe is not None
                        and isinstance(last_error_safe.get("reason_code"), str)
                        else {}
                    )
                    case_error = {
                        "sequence": sequence,
                        "qa_id": qa_id,
                        "method": method,
                        "provider_outcome": failed_provider_outcome,
                        "provider_response_accepted": attempt_response_accepted,
                        **failure_classification_fields,
                        "provider_attempts": attempt_count,
                        "request_sha256": generation_hash,
                        "response_sha256": response_evidence.get("response_sha256") if response_evidence else None,
                        **(
                            {
                                "provider_input_tokens": (response_evidence.get("usage_metadata") or {}).get("input_tokens"),
                                "provider_output_tokens": (response_evidence.get("usage_metadata") or {}).get("output_tokens"),
                                "provider_thinking_tokens": (response_evidence.get("usage_metadata") or {}).get("thinking_tokens"),
                                "usage_status": _evidence_usage_status(response_evidence),
                                "billing_uncertainty": response_evidence.get("billing_uncertainty", True),
                            }
                            if response_evidence
                            else {}
                        ),
                        "count_tokens_request_sha256": count_hash,
                        "context_sha256": context_hash,
                        "context_tokens_estimated": plan["context_tokens_estimated"],
                        "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
                        "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
                        "memory_lookup_status": plan["memory_lookup_status"],
                        "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
                        **last_count_tokens_telemetry,
                        "safe_error": {
                            **(last_error_safe or {}),
                            **(
                                {"retry_blocked_by": stop_reason}
                                if stop_reason.startswith("RETRY_BLOCKED_BY_")
                                else {}
                            ),
                        }
                        or {"guard_state": stop_reason},
                    }
                    output_by_sequence[sequence] = case_error
                    _record_case_result(root, case_error)
                    _append_attempt_journal(
                        attempt_journal_path,
                        {
                            "event": "CASE_METHOD_BLOCKED" if case_local_block else "CASE_METHOD_COMPLETED",
                            "run_id": run_id,
                            "sequence": sequence,
                            "qa_id": qa_id,
                            "method": method,
                            "provider_outcome": failed_provider_outcome,
                            "reason_code": last_error_safe.get("reason_code") if last_error_safe else None,
                            **failure_classification_fields,
                            "retry_blocked_by": stop_reason if stop_reason.startswith("RETRY_BLOCKED_BY_") else None,
                            **last_count_tokens_telemetry,
                            "timestamp_utc": datetime.now(UTC).isoformat(),
                        },
                    )
                break
            if case_success is None:
                failure_count += 1
                failed_provider_outcome = _failed_attempt_provider_outcome(
                    last_error_safe or {}
                )
                failure_classification_fields = (
                    {
                        "failure_classification": last_error_safe["failure_classification"],
                        "reason_code": last_error_safe["reason_code"],
                    }
                    if last_error_safe is not None
                    and isinstance(last_error_safe.get("reason_code"), str)
                    else {}
                )
                output_by_sequence[sequence] = {
                    "sequence": sequence,
                    "qa_id": qa_id,
                    "method": method,
                    "provider_outcome": failed_provider_outcome,
                    "provider_response_accepted": bool(
                        last_error_safe and last_error_safe.get("provider_response_accepted") is True
                    ),
                    **failure_classification_fields,
                    "provider_attempts": attempt_count,
                    "request_sha256": generation_hash,
                    "response_sha256": response_evidence.get("response_sha256") if response_evidence else None,
                    **(
                        {
                            "provider_input_tokens": (response_evidence.get("usage_metadata") or {}).get("input_tokens"),
                            "provider_output_tokens": (response_evidence.get("usage_metadata") or {}).get("output_tokens"),
                            "provider_thinking_tokens": (response_evidence.get("usage_metadata") or {}).get("thinking_tokens"),
                            "usage_status": _evidence_usage_status(response_evidence),
                            "billing_uncertainty": response_evidence.get("billing_uncertainty", True),
                        }
                        if response_evidence
                        else {}
                    ),
                    "count_tokens_request_sha256": count_hash,
                    "context_sha256": context_hash,
                    "context_tokens_estimated": plan["context_tokens_estimated"],
                    "selected_source_evidence_ids": plan["context_evidence_ids_selected"],
                    "retrieval_evidence_ids": plan["retrieval_evidence_ids"],
                    "memory_lookup_status": plan["memory_lookup_status"],
                    "memory_source_evidence_ids": plan["memory_source_evidence_ids"],
                    **last_count_tokens_telemetry,
                    "safe_error": last_error_safe or {"guard_state": "ATTEMPTS_EXHAUSTED"},
                }
                _record_case_result(root, output_by_sequence[sequence])
                _append_attempt_journal(
                    attempt_journal_path,
                    {
                        "event": "CASE_METHOD_BLOCKED" if case_local_block else "CASE_METHOD_COMPLETED",
                        "run_id": run_id,
                        "sequence": sequence,
                        "qa_id": qa_id,
                        "method": method,
                        "provider_outcome": failed_provider_outcome,
                        "reason_code": last_error_safe.get("reason_code") if last_error_safe else None,
                        **failure_classification_fields,
                        **last_count_tokens_telemetry,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                    },
                )
                if e2_plan:
                    if not _score_coverage_can_still_pass(
                        output_by_sequence,
                        plan_rows,
                        minimum_scored_coverage=FINAL_EVALUATION_MIN_SCORED_COVERAGE,
                        minimum_paired_coverage=FINAL_EVALUATION_MIN_PAIRED_COVERAGE,
                    ):
                        stop_reason = "SCORE_PROTOCOL_COVERAGE_UNREACHABLE"
                        break
                else:
                    possible_completion = (
                        (len(accepted_response_sequences) if diagnostic_stress or diagnostic_mode else sum(
                            row.get("provider_outcome") == "PROVIDER_COMPLETE" for row in output_by_sequence.values()
                        ))
                        + (len(plan_rows) - sequence)
                    ) / len(plan_rows)
                    completion_threshold = 0.80 if diagnostic_stress else 0.90
                    if possible_completion < completion_threshold:
                        stop_reason = f"PROVIDER_COMPLETION_CANNOT_REACH_{int(completion_threshold * 100)}_PERCENT"
                        break
            if case_success is not None:
                _append_attempt_journal(
                    attempt_journal_path,
                    {
                        "event": "CASE_METHOD_COMPLETED",
                        "run_id": run_id,
                        "sequence": sequence,
                        "qa_id": qa_id,
                        "method": method,
                        "provider_outcome": "PROVIDER_COMPLETE",
                        **last_count_tokens_telemetry,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                    },
                )
            _json_write(
                root / "run_progress.json",
                {
                    "run_id": run_id,
                    "last_completed_sequence": sequence,
                    "completed_method_cases": sum(
                        row.get("provider_outcome") == "PROVIDER_COMPLETE"
                        for row in output_by_sequence.values()
                    ),
                    "failed_method_cases": failure_count,
                    "estimated_cost_upper_bound_usd": round(spent_upper_bound, 9),
                },
            )

    except Exception as error:
        safe_run_error = _safe_run_level_failure(error)
        stop_reason = stop_reason or safe_run_error["reason_code"]
        harness_traceback = _safe_exception_traceback(error)
        try:
            _persist_clean_eval_block(
                root,
                run_id=run_id,
                sequence=0,
                case_id="RUN_EXECUTION",
                method="RUN_LEVEL",
                attempt_no=0,
                safe_block=safe_run_error,
                request_hash=None,
                response_evidence=None,
            )
        except OSError:
            stop_reason = "ARTIFACT_RUN_LEVEL_GUARD_BLOCK_PERSIST_FAILED"
    finally:
        if memory is not None:
            memory.store.close()

    if diagnostic_mode:
        try:
            diagnostic_summary = _finalize_diagnostic_run(
                root,
                stop_reason=stop_reason,
                metrics={
                    "estimated_spend_upper_bound_usd": spent_upper_bound,
                    "provider_reported_cost_usd": (
                        aggregate.reported_cost_usd if aggregate is not None else None
                    ),
                },
            )
        except Exception as error:
            safe_run_error = _safe_run_level_failure(error)
            try:
                _persist_clean_eval_block(
                    root,
                    run_id=run_id,
                    sequence=0,
                    case_id="DIAGNOSTIC_FINALIZATION",
                    method="RUN_LEVEL",
                    attempt_no=0,
                    safe_block=safe_run_error,
                    request_hash=None,
                    response_evidence=None,
                )
            except OSError:
                pass
            _finish_budget_reservation(
                ledger,
                run_id,
                status="BLOCKED",
                estimated_spend_upper_bound_usd=spent_upper_bound,
                ledger_path=budget_ledger_path,
            )
            raise CleanEvalBlocked(safe_run_error["reason_code"]) from None
        _finish_budget_reservation(
            ledger,
            run_id,
            status=diagnostic_summary["status"],
            estimated_spend_upper_bound_usd=spent_upper_bound,
            ledger_path=budget_ledger_path,
        )
        ledger["runs"][-1]["implementation_fingerprint_sha256"] = fingerprints["files_sha256"]
        _write_budget_ledger(ledger, path=budget_ledger_path)
        return {
            "run_id": run_id,
            "status": diagnostic_summary["status"],
            "summary": diagnostic_summary,
            "artifact_root": str(root),
        }

    provider_outputs: list[dict[str, Any]] = []
    provider_outputs_path = root / "provider_outputs.json"
    journal_events: list[dict[str, Any]] = []
    journal_summary: dict[str, Any] = {
        "integrity_valid": False,
        "method_case_lifecycles_closed": False,
        "protocol_complete": False,
        "terminal_method_cases": 0,
        "started_method_cases": 0,
        "completed_method_cases": 0,
        "scored_method_cases": 0,
        "provider_attempt_starts": 0,
        "provider_attempt_completions": 0,
        "unmatched_method_cases": [],
        "errors": ["JOURNAL_NOT_READ"],
        "last_durable_journal_event": None,
    }
    accepted_evidence_summary: dict[str, Any] = {
        "rows": [],
        "integrity_valid": False,
        "errors": ["EVIDENCE_NOT_READ"],
    }
    score_unlock_gate: dict[str, Any] | None = None
    score_unlock_path = root / "score_unlock_gate.json"
    try:
        provider_outputs, provider_outputs_path = _persist_provider_outputs(
            root=root,
            cell_key=cell_key,
            plan_rows=plan_rows,
            output_by_sequence=output_by_sequence,
        )
        if len(provider_outputs) != planned_method_cases:
            raise CleanEvalBlocked("PROVIDER_OUTPUT_PLAN_COUNT_MISMATCH")
        journal_events = _read_attempt_journal(attempt_journal_path)
        journal_summary = _validate_attempt_journal_events(
            journal_events,
            expected_case_count=planned_method_cases,
            scoring_required=not (diagnostic_stress or diagnostic_mode),
        )
        if not journal_summary["integrity_valid"]:
            stop_reason = stop_reason or "JOURNAL_PERSISTENCE_FAILURE"
        elif (
            stop_reason is None
            and (
                journal_summary["terminal_method_cases"] != planned_method_cases
                if diagnostic_stress
                else (
                    journal_summary["terminal_method_cases"] != planned_method_cases
                    if e2_plan
                    else journal_summary["completed_method_cases"] != planned_method_cases
                )
            )
        ):
            stop_reason = "DIAGNOSTIC_LIFECYCLE_INCOMPLETE" if diagnostic_stress else "JOURNAL_PROTOCOL_INCOMPLETE"
        completion_count = sum(row.get("provider_outcome") == "PROVIDER_COMPLETE" for row in provider_outputs)
        accepted_evidence_summary = _read_accepted_response_evidence(accepted_response_evidence_path)
        if diagnostic_stress and not accepted_evidence_summary["integrity_valid"]:
            stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_EVIDENCE_INVALID"
        accepted_response_cases = {
            row["sequence"] for row in accepted_evidence_summary["rows"]
        }
        completion_rate = (
            len(accepted_response_cases) / len(plan_rows)
            if diagnostic_stress or diagnostic_mode
            else completion_count / len(provider_outputs)
        )
        from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini
        if gemini._secret_scan(root, os.environ["GEMINI_API_KEY"]):
            stop_reason = "CREDENTIAL_LEAK_BLOCKED"
        completion_threshold = 0.80 if diagnostic_stress else 0.90
        if completion_rate < completion_threshold:
            stop_reason = stop_reason or f"PROVIDER_COMPLETION_BELOW_{int(completion_threshold * 100)}_PERCENT"
        if implementation_fingerprints() != fingerprints:
            stop_reason = "IMPLEMENTATION_FINGERPRINT_MISMATCH_AFTER_LIVE"
        if (
            aggregate is not None
            and _spent_upper_bound_usd(aggregate) > spent_upper_bound + 1e-8
        ):
            # The explicit static reserve should dominate the dynamic token estimate.
            stop_reason = "COST_LEDGER_UNDERCOUNTED_DYNAMIC_PROVIDER_USAGE"

        if e2_plan:
            question_ids_for_gate = (
                [question.question_id for question in case.questions]
                if case is not None
                else _question_ids_from_plan_rows(plan_rows)
            )
            protocol_eligibility = _score_protocol_eligibility(
                provider_outputs,
                question_ids_for_gate,
            )
            output_sealed_before_gold = _verify_seal(
                provider_outputs_path,
                identity=f"provider-outputs:{cell.source}",
            )
            block_integrity_before_gold = _reconstruct_clean_eval_blocks(root)["integrity_valid"]
            score_unlock_gate = {
                "schema_version": "linkloom-public-memory-score-unlock-gate/v1",
                "run_id": run_id,
                "evaluation_plan_id": evaluation_plan.get("plan_id") if evaluation_plan else None,
                "status": "PASS" if (
                    protocol_eligibility["eligible_for_gold_unlock"]
                    and journal_summary["integrity_valid"]
                    and journal_summary["terminal_method_cases"] == planned_method_cases
                    and not journal_summary["aborted_method_cases"]
                    and accepted_evidence_summary["integrity_valid"]
                    and output_sealed_before_gold
                    and block_integrity_before_gold
                    and implementation_fingerprints() == fingerprints
                    and stop_reason is None
                ) else "FAIL",
                "eligible_for_gold_unlock": protocol_eligibility["eligible_for_gold_unlock"],
                "protocol_eligibility": protocol_eligibility,
                "planned_questions": planned_questions,
                "planned_method_cases": planned_method_cases,
                "terminal_method_cases": journal_summary["terminal_method_cases"],
                "journal_integrity_valid": journal_summary["integrity_valid"],
                "accepted_response_evidence_integrity_valid": accepted_evidence_summary["integrity_valid"],
                "provider_outputs_sealed": output_sealed_before_gold,
                "block_artifact_integrity_valid": block_integrity_before_gold,
                "fingerprints_match": implementation_fingerprints() == fingerprints,
                "stop_reason": stop_reason,
                "gold_values_read_at_gate": False,
                "created_at_utc": datetime.now(UTC).isoformat(),
            }
            _atomic_json_write_fsync(score_unlock_path, score_unlock_gate)
            _seal_file(score_unlock_path, identity=f"score-unlock-gate:{run_id}")
            if score_unlock_gate["status"] != "PASS":
                stop_reason = stop_reason or "SCORE_PROTOCOL_COVERAGE_GATE_FAILED"

        scoreable = (
            not diagnostic_stress
            and not diagnostic_mode
            and case is not None
            and stop_reason is None
            and completion_rate >= 0.90
            and journal_summary["integrity_valid"]
            and journal_summary["terminal_method_cases"] == planned_method_cases
            and (e2_plan or journal_summary["completed_method_cases"] == planned_method_cases)
            and (not e2_plan or (score_unlock_gate or {}).get("status") == "PASS")
        )
        if scoreable:
            gold_values_read = True
            score_report, scored_summary = _score_sealed_run(
                root=root,
                cell_key=cell_key,
                case=case,
                preflight=preflight,
                provider_outputs=provider_outputs,
                provider_outputs_path=provider_outputs_path,
                fingerprints=fingerprints,
                evaluation_plan_id=(evaluation_plan.get("plan_id") if evaluation_plan else None),
            )
            journal_events = _read_attempt_journal(attempt_journal_path)
            journal_summary = _validate_attempt_journal_events(
                journal_events,
                expected_case_count=planned_method_cases,
            )
            if (
                not journal_summary["integrity_valid"]
                or journal_summary["terminal_method_cases"] != planned_method_cases
                or journal_summary["scored_method_cases"] != planned_method_cases
                or not journal_summary["protocol_complete"]
            ):
                raise CleanEvalBlocked("JOURNAL_PERSISTENCE_FAILURE_AFTER_SCORING")
            score_status = "PASS"
        elif not diagnostic_stress and not diagnostic_mode:
            score_status = "SKIPPED"
    except Exception as error:
        safe_run_error = _safe_run_level_failure(error)
        stop_reason = stop_reason or safe_run_error["reason_code"]
        harness_traceback = _safe_exception_traceback(error)
        try:
            _persist_clean_eval_block(
                root,
                run_id=run_id,
                sequence=0,
                case_id="RUN_FINALIZATION",
                method="RUN_LEVEL",
                attempt_no=0,
                safe_block=safe_run_error,
                request_hash=None,
                response_evidence=None,
            )
        except OSError:
            stop_reason = "ARTIFACT_RUN_LEVEL_GUARD_BLOCK_PERSIST_FAILED"
        score_status = "BLOCKED"
        score_error = stop_reason

    try:
        journal_events = _read_attempt_journal(attempt_journal_path)
        journal_summary = _validate_attempt_journal_events(
            journal_events,
            expected_case_count=planned_method_cases,
        )
    except CleanEvalBlocked as error:
        stop_reason = stop_reason or error.reason_code
        harness_traceback = _safe_exception_origin(error)
        journal_events = []
        journal_summary = {
            "integrity_valid": False,
            "method_case_lifecycles_closed": False,
            "protocol_complete": False,
            "terminal_method_cases": 0,
            "started_method_cases": 0,
            "completed_method_cases": 0,
            "blocked_method_cases": 0,
            "scored_method_cases": 0,
            "provider_attempt_starts": 0,
            "provider_attempt_completions": 0,
            "unmatched_method_cases": [],
            "errors": [error.reason_code],
            "last_durable_journal_event": None,
        }
        stop_reason = stop_reason or "JOURNAL_PERSISTENCE_FAILURE"

    completion_count = sum(row.get("provider_outcome") == "PROVIDER_COMPLETE" for row in provider_outputs)
    accepted_evidence_summary = _read_accepted_response_evidence(accepted_response_evidence_path)
    accepted_response_cases = {row["sequence"] for row in accepted_evidence_summary["rows"]}
    provider_stats = _provider_completion_stats(journal_events)
    accepted_response_attempts = provider_stats["provider_completed_requests"]
    provider_error_attempts = provider_stats["provider_error_attempts"]
    local_harness_block_attempts = provider_stats["local_harness_block_attempts"]
    completion_rate = (
        len(accepted_response_cases) / planned_method_cases
        if diagnostic_stress or diagnostic_mode
        else completion_count / len(provider_outputs) if provider_outputs else 0.0
    )
    provider_error_method_cases = sum(
        row.get("provider_outcome") == "PROVIDER_ERROR"
        for row in provider_outputs
    )
    incomplete_usage_responses = [
        row for row in accepted_evidence_summary["rows"]
        if row.get("usage_status") == "INCOMPLETE"
    ]
    missing_output_token_occurrences = sum(
        "output_tokens" in (row.get("usage_missing_fields") or [])
        for row in incomplete_usage_responses
    )
    local_harness_block_method_cases = sum(
        row.get("provider_outcome") == "LOCAL_HARNESS_BLOCK"
        for row in provider_outputs
    )
    block_summary = _reconstruct_clean_eval_blocks(root)
    provider_outputs_sealed = _verify_seal(
        provider_outputs_path,
        identity=f"provider-outputs:{cell.source}",
    )
    score_unlock_gate_sealed = (
        _verify_seal(score_unlock_path, identity=f"score-unlock-gate:{run_id}")
        if e2_plan
        else True
    )
    fingerprint_match = implementation_fingerprints() == fingerprints
    artifact_durability_valid = (
        provider_outputs_sealed
        and journal_summary["integrity_valid"]
        and accepted_evidence_summary["integrity_valid"]
        and block_summary["integrity_valid"]
        and score_unlock_gate_sealed
        and (not gold_values_read or (score_unlock_gate or {}).get("status") == "PASS")
    )
    if diagnostic_stress:
        if not accepted_evidence_summary["integrity_valid"]:
            stop_reason = stop_reason or "ARTIFACT_ACCEPTED_RESPONSE_EVIDENCE_INVALID"
        if not block_summary["integrity_valid"]:
            stop_reason = stop_reason or "ARTIFACT_CLEAN_EVAL_BLOCK_INTEGRITY_FAILURE"
        if gold_values_read:
            stop_reason = "GOLD_DIAGNOSTIC_SCORING_VIOLATION"
        if not fingerprint_match:
            stop_reason = "FINGERPRINT_IMPLEMENTATION_DRIFT"
        if not artifact_durability_valid:
            diagnostic_invalid = True
        else:
            diagnostic_invalid = False
        if diagnostic_invalid:
            status = "HARNESS_STRESS_INVALID"
        elif (
            stop_reason is None
            and journal_summary["terminal_method_cases"] == 200
            and fingerprint_match
            and artifact_durability_valid
            and not gold_values_read
            and not any(code.startswith("CLEAN_EVAL_BLOCK_OTHER_") for code in block_summary["reason_code_counts"])
        ):
            status = "HARNESS_STRESS_PASS"
        else:
            status = "HARNESS_STRESS_PARTIAL"
    else:
        status = (
            "PROVIDER_COMPLETION_BELOW_90_PERCENT"
            if stop_reason == "PROVIDER_COMPLETION_BELOW_90_PERCENT"
            or stop_reason == "PROVIDER_COMPLETION_CANNOT_REACH_90_PERCENT"
            else "STOPPED" if stop_reason is not None
            else "COMPLETE" if completion_rate == 1.0 and score_status == "PASS"
            else "COMPLETE_WITH_NOT_EVALUATED" if e2_plan and score_status == "PASS" and completion_count < planned_method_cases
            else "COMPLETE_WITH_PROVIDER_ERRORS" if completion_rate >= 0.90 and score_status == "PASS"
            else "STOPPED"
        )
    protocol_valid = (
        status in {"COMPLETE", "COMPLETE_WITH_PROVIDER_ERRORS", "COMPLETE_WITH_NOT_EVALUATED"}
        and score_status == "PASS"
        and journal_summary["protocol_complete"]
        and fingerprint_match
    )
    diagnostic_lifecycle_valid = (
        diagnostic_stress
        and journal_summary["integrity_valid"]
        and journal_summary["method_case_lifecycles_closed"]
            and journal_summary["terminal_method_cases"] == planned_method_cases
    )
    if diagnostic_stress:
        if (
            not artifact_durability_valid
            or not fingerprint_match
            or gold_values_read
            or not diagnostic_lifecycle_valid
            or any(code.startswith("CLEAN_EVAL_BLOCK_OTHER_") for code in block_summary["reason_code_counts"])
        ):
            stability_verdict = "HARNESS_UNSTABLE"
        elif block_summary["block_count"] or provider_error_attempts:
            stability_verdict = "HARNESS_STABLE_WITH_KNOWN_BLOCKS"
        else:
            stability_verdict = "HARNESS_STABLE"
    else:
        stability_verdict = None
    if harness_traceback is not None:
        traceback_path = root / "harness_traceback.json"
        _json_write(traceback_path, {"traceback": harness_traceback})
        _seal_file(traceback_path, identity=f"harness-traceback:{run_id}")
    summary = {
        "schema_version": "linkloom-public-memory-live-summary/v1",
        "run_id": run_id,
        "evaluation_plan_id": evaluation_plan.get("plan_id") if evaluation_plan else None,
        "evaluation_plan_sha256": _evaluation_plan_sha256(evaluation_plan) if evaluation_plan else None,
        "authorization_baseline_sha256": (
            hashlib.sha256(authorization_baseline_path.read_bytes()).hexdigest()
            if authorization_baseline_path is not None and authorization_baseline_path.is_file()
            else None
        ),
        "budget_scope": "DIAGNOSTIC_TASK_10_USD" if diagnostic_stress else "FORMAL_EVALUATION_30_USD",
        "cell": cell.display_name,
        "planned_questions": planned_questions,
        "planned_method_cases": planned_method_cases,
        "purpose": "HARNESS_STABILITY" if diagnostic_stress else "CLEAN_EVALUATION",
        "dataset_classification": (
            "REVISED_HELDOUT_AFTER_STOPPED_E1_1" if e2_plan
            else "DIAGNOSTIC_STRESS_SET_NOT_CLEAN_HELD_OUT" if diagnostic_stress
            else "CLEAN_HELD_OUT"
        ),
        "accuracy_claim": False if diagnostic_stress else True,
        "status": status,
        "protocol_status": (
            "DIAGNOSTIC_LIFECYCLE_VALID" if diagnostic_lifecycle_valid
            else "VALID" if protocol_valid
            else "INVALID"
        ),
        "stop_reason": stop_reason,
        "score_status": score_status,
        "offline_rehearsal": rehearsal_mode,
        "score_error": score_error,
        "score_unlock_gate": score_unlock_gate,
        "score_unlock_gate_artifact_status": "PASS" if score_unlock_gate_sealed else "FAIL",
        "harness_stability_verdict": stability_verdict,
        "continuation_eligible": False,
        "last_durable_journal_event": journal_summary["last_durable_journal_event"],
        "journal_protocol": journal_summary,
        "harness_traceback": harness_traceback,
        "provider_completion": {
            "completed_method_cases": completion_count,
            "provider_response_completed_method_cases": len(accepted_response_cases),
            "durable_output_method_cases": completion_count,
            "planned_method_cases": planned_method_cases,
            "rate": completion_rate,
            "failed_method_cases": provider_error_method_cases + local_harness_block_method_cases,
            "provider_error_method_cases": provider_error_method_cases,
            "local_harness_block_method_cases": local_harness_block_method_cases,
            "not_run_method_cases": sum(row.get("provider_outcome") == "NOT_RUN" for row in provider_outputs),
        },
        "provider_reliability": {
            "generation_attempts": aggregate.provider_requests if aggregate is not None else 0,
            "provider_completed_requests": accepted_response_attempts,
            "accepted_response_attempts": accepted_response_attempts,
            "accepted_response_rate": provider_stats["completion_rate"],
            "provider_error_attempts": provider_error_attempts,
            "local_harness_block_attempts": local_harness_block_attempts,
        },
        "usage_completeness": {
            "accepted_response_attempts": len(accepted_evidence_summary["rows"]),
            "incomplete_usage_accepted_responses": len(incomplete_usage_responses),
            "missing_output_tokens_occurrences": missing_output_token_occurrences,
            "incomplete_usage_not_zero_filled": all(
                (row.get("usage_metadata") or {}).get("output_tokens") is None
                for row in incomplete_usage_responses
                if "output_tokens" in (row.get("usage_missing_fields") or [])
            ),
        },
        "harness_execution": {
            "planned_method_cases": planned_method_cases,
            "started_method_cases": journal_summary.get("started_method_cases", 0),
            "terminal_method_cases": journal_summary.get("terminal_method_cases", 0),
            "completed_method_cases": journal_summary.get("completed_method_cases", 0),
            "blocked_method_cases": journal_summary.get("blocked_method_cases", 0),
            "aborted_method_cases": journal_summary.get("aborted_method_cases", 0),
            "not_run_method_cases": sum(row.get("provider_outcome") == "NOT_RUN" for row in provider_outputs),
        },
        "clean_eval_blocks": {
            "count": block_summary["block_count"],
            "reason_code_counts": block_summary["reason_code_counts"],
            "blocks": block_summary["blocks"],
            "integrity_valid": block_summary["integrity_valid"],
        },
        "accepted_response_evidence": {
            "record_count": len(accepted_evidence_summary["rows"]),
            "integrity_valid": accepted_evidence_summary["integrity_valid"],
            "errors": accepted_evidence_summary["errors"],
            "path": "accepted_response_evidence.jsonl",
        },
        "diagnostic_execution_baseline_sha256": (
            hashlib.sha256(baseline_path.read_bytes()).hexdigest() if diagnostic_stress else None
        ),
        "scoring": "DISABLED" if diagnostic_stress else "ENABLED_AFTER_SEAL",
        "gold_values_read": gold_values_read,
        "finalizer_pre_summary_integrity": "PASS" if artifact_durability_valid else "FAIL",
        "provider_requests": {
            "generation_attempts": aggregate.provider_requests if aggregate is not None else 0,
            "count_tokens_requests": aggregate.preflight_count_requests if aggregate is not None else 0,
            "sdk_automatic_retries": 0,
            "harness_attempt_limit_per_logical_generation": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
        },
        "count_tokens": _count_tokens_run_summary(
            journal_events,
            aggregate.preflight_count_requests if aggregate is not None else 0,
        ),
        "estimated_spend_upper_bound_usd": round(spent_upper_bound, 9),
        "provider_reported_cost_usd": (
            aggregate.reported_cost_usd if aggregate is not None else None
        ),
        "hard_cost_cap_usd": cell.hard_cost_cap_usd,
        "global_cost_cap_usd": global_hard_cost_cap,
        "implementation_fingerprint_status": (
            "PASS" if implementation_fingerprints() == fingerprints else "MISMATCH"
        ),
        "artifact_durability_status": "PASS" if artifact_durability_valid else "FAIL",
        "gold_values_persisted": False,
        "final_score_summary": scored_summary,
        "final_scored_results_sha256": (
            hashlib.sha256((root / "scored_results.json").read_bytes()).hexdigest()
            if (root / "scored_results.json").is_file()
            else None
        ),
    }
    summary_path = root / "pilot_summary.json"
    _json_write(summary_path, summary)
    _seal_file(summary_path, identity=f"live-summary:{run_id}")

    run_manifest.update(
        {
            "status": status,
            "stop_reason": stop_reason,
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "provider_completion_rate": completion_rate,
            "estimated_spend_upper_bound_usd": round(spent_upper_bound, 9),
            "score_status": score_status,
            "provider_outputs_sha256": hashlib.sha256(provider_outputs_path.read_bytes()).hexdigest(),
            "pilot_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            "implementation_fingerprint_status": summary["implementation_fingerprint_status"],
            "protocol_status": summary["protocol_status"],
            "journal_protocol": journal_summary,
            "last_durable_journal_event": journal_summary["last_durable_journal_event"],
            "continuation_eligible": False,
            "harness_traceback_sha256": (
                hashlib.sha256((root / "harness_traceback.json").read_bytes()).hexdigest()
                if (root / "harness_traceback.json").is_file()
                else None
            ),
        }
    )
    _write_run_manifest(root, run_manifest)
    _publish_artifact_manifest(root, run_id)
    finalizer_integrity = _verify_published_artifact_manifest(root, run_id)

    ledger_status = status
    completion_threshold = 0.80 if diagnostic_stress else 0.90
    if completion_rate < completion_threshold:
        ledger_status = f"PROVIDER_COMPLETION_BELOW_{int(completion_threshold * 100)}_PERCENT"
    if diagnostic_stress or rehearsal_mode:
        _finish_budget_reservation(
            ledger,
            run_id,
            status=ledger_status,
            estimated_spend_upper_bound_usd=spent_upper_bound,
            global_cap_usd=global_hard_cost_cap,
            ledger_path=budget_ledger_path,
        )
    else:
        _finish_budget_reservation(
            ledger,
            run_id,
            status=ledger_status,
            estimated_spend_upper_bound_usd=spent_upper_bound,
        )
    ledger["runs"][-1]["implementation_fingerprint_sha256"] = fingerprints["files_sha256"]
    ledger["runs"][-1]["scored_results_sha256"] = summary["final_scored_results_sha256"]
    if diagnostic_stress or rehearsal_mode:
        _write_budget_ledger(ledger, path=budget_ledger_path)
    else:
        _write_budget_ledger(ledger)
    return {
        "run_id": run_id,
        "status": status,
        "summary": summary,
        "artifact_root": str(root),
        "finalizer_integrity": finalizer_integrity,
    }


def _rehearse_final_evaluation_live_path(
    *,
    preflight_dir: Path | None = None,
) -> dict[str, Any]:
    qualification_dir = _resolve_eval_artifact_dir(preflight_dir)
    if qualification_dir != E2_QUALIFICATION_DIR.resolve():
        raise CleanEvalBlocked("E2_QUALIFICATION_PATH_MISMATCH")
    rehearsal_dir = qualification_dir / "formal_live_path_rehearsal"
    shadow_ledger_path = qualification_dir / "formal_rehearsal_budget_ledger.json"
    report_path = qualification_dir / "formal_live_path_rehearsal.json"
    if rehearsal_dir.exists() or shadow_ledger_path.exists() or report_path.exists():
        raise CleanEvalBlocked("FORMAL_REHEARSAL_ARTIFACT_ALREADY_EXISTS")
    policy_path = qualification_dir / "FINAL_EVALUATION_POLICY_V1.json"
    try:
        policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CleanEvalBlocked("FINAL_EVALUATION_POLICY_ARTIFACT_INVALID") from None
    if (
        not _verify_seal(policy_path, identity="final-evaluation-policy-v1")
        or policy_payload != _final_evaluation_policy_payload()
    ):
        raise CleanEvalBlocked("FINAL_EVALUATION_POLICY_ARTIFACT_INVALID")
    source_ledger_before = _load_budget_ledger(
        path=FORMAL_BUDGET_LEDGER_PATH,
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    result = _run_live(
        "mh_6k",
        preflight_dir=qualification_dir,
        evaluation_plan_id=E2_MH_ONLY_EVALUATION_PLAN_ID,
        rehearsal_mode=True,
        rehearsal_root=rehearsal_dir,
        rehearsal_budget_ledger_path=shadow_ledger_path,
    )
    source_ledger_after = _load_budget_ledger(
        path=FORMAL_BUDGET_LEDGER_PATH,
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    shadow_ledger = _load_budget_ledger(
        path=shadow_ledger_path,
        hard_cap_usd=GLOBAL_COST_CAP_USD,
    )
    _seal_file(shadow_ledger_path, identity="formal-rehearsal-budget-ledger")
    run_root = Path(result["artifact_root"])
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    frozen_preflight = json.loads(
        (qualification_dir / "mh_6k_preflight.json").read_text(encoding="utf-8")
    )
    summary = result["summary"]
    manifest_requirements = {
        "dataset_revision": manifest.get("dataset_revision") == frozen_preflight.get("dataset_revision"),
        "dataset_sha256": manifest.get("dataset_sha256") == frozen_preflight.get("dataset_sha256"),
        "subset_sha256": manifest.get("subset_sha256") == frozen_preflight.get("subset_sha256"),
        "evaluation_plan_id": manifest.get("evaluation_plan_id") == E2_MH_ONLY_EVALUATION_PLAN_ID,
        "baseline_fingerprint": manifest.get("baseline_fingerprint_sha256") == manifest.get("implementation_fingerprint_sha256"),
        "runtime_config_fingerprint": manifest.get("runtime_config_fingerprint_sha256") == frozen_preflight.get("runtime_execution_plan_sha256"),
    }
    shadow_row = next(
        (row for row in shadow_ledger.get("runs", []) if row.get("run_id") == result["run_id"]),
        None,
    )
    checks = {
        "expected_pre_provider_stop": (
            summary.get("stop_reason") == "FORMAL_LIVE_PATH_REHEARSAL_STOP_BEFORE_PROVIDER"
            and summary.get("status") == "STOPPED"
        ),
        "all_manifest_identity_fields_present": all(manifest_requirements.values()),
        "first_case_initialized": summary.get("harness_execution", {}).get("started_method_cases") == 1,
        "provider_generation_requests_zero": summary.get("provider_requests", {}).get("generation_attempts") == 0,
        "count_tokens_requests_zero": summary.get("provider_requests", {}).get("count_tokens_requests") == 0,
        "gold_reads_zero": summary.get("gold_values_read") is False,
        "finalizer_returned": summary.get("score_unlock_gate_artifact_status") == "PASS",
        "score_gate_stayed_locked": summary.get("score_unlock_gate", {}).get("status") == "FAIL",
        "run_artifact_manifest_pass": result.get("finalizer_integrity", {}).get("status") == "PASS",
        "shadow_reservation_released": (
            shadow_row is not None
            and shadow_row.get("reserved_usd") == 0.0
            and shadow_ledger.get("active_run_id") is None
        ),
        "formal_ledger_unchanged": source_ledger_before.get("ledger_sha256") == source_ledger_after.get("ledger_sha256"),
    }
    payload = {
        "schema_version": "linkloom-formal-live-path-rehearsal/v1",
        "policy_id": FINAL_EVALUATION_POLICY_ID,
        "run_id": result["run_id"],
        "status": "PASS" if all(checks.values()) else "FAIL",
        "classification": "OFFLINE_FORMAL_PATH_REHEARSAL_NO_PROVIDER_NO_GOLD",
        "run_artifact_path": run_root.relative_to(qualification_dir).as_posix(),
        "manifest_fields": manifest_requirements,
        "provider_calls": {"generation": 0, "count_tokens": 0},
        "gold_values_read": False,
        "finalizer_status": summary.get("status"),
        "primary_stop_reason": summary.get("stop_reason"),
        "artifact_manifest_status": result.get("finalizer_integrity", {}).get("status"),
        "formal_budget_ledger_sha256_before": source_ledger_before.get("ledger_sha256"),
        "formal_budget_ledger_sha256_after": source_ledger_after.get("ledger_sha256"),
        "shadow_budget_ledger_sha256": shadow_ledger.get("ledger_sha256"),
        "checks": checks,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    _atomic_json_write_fsync(report_path, payload)
    _seal_file(report_path, identity="final-evaluation-formal-path-rehearsal")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", choices=("sh_32k", "mh_6k", "all"))
    group.add_argument("--prepare-e2", action="store_true")
    group.add_argument("--rehearse-final-evaluation", action="store_true")
    group.add_argument("--prepare-diagnostic", action="store_true")
    group.add_argument("--check-route", action="store_true")
    group.add_argument("--live", choices=("sh_32k", "mh_6k"))
    group.add_argument("--diagnostic", choices=("sh_32k", "mh_6k"))
    group.add_argument(
        "--finalize-diagnostic",
        type=Path,
        metavar="RUN_ARTIFACT_DIR",
        help="recover and seal an interrupted diagnostic run from its local artifacts only",
    )
    group.add_argument("--diagnostic-stress", action="store_true")
    parser.add_argument("--dataset-path", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write prepared offline qualification artifacts to this directory",
    )
    parser.add_argument(
        "--preflight-dir",
        type=Path,
        help="read sealed offline qualification artifacts from this directory for a live run",
    )
    parser.add_argument(
        "--authorize-fresh-sh-v3",
        action="store_true",
        help="allow one fresh V3 SH-32k run after verifying the sealed V1 and V2 stopped runs",
    )
    parser.add_argument(
        "--evaluation-plan",
        choices=(LEGACY_DUAL_EVALUATION_PLAN_ID, MH_ONLY_EVALUATION_PLAN_ID, E2_MH_ONLY_EVALUATION_PLAN_ID),
        help="select the fingerprinted versioned formal evaluation plan",
    )
    args = parser.parse_args()
    if args.finalize_diagnostic is not None:
        if args.output_dir is not None or args.preflight_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("qualification and live options are not valid with --finalize-diagnostic")
        summary = _finalize_diagnostic_run(args.finalize_diagnostic)
        _release_recovered_diagnostic_budget_reservation(args.finalize_diagnostic, summary)
        print(json.dumps(
            {
                "run_id": summary["run_id"],
                "status": summary["status"],
                "summary": summary,
                "artifact_root": str(args.finalize_diagnostic),
            },
            indent=2,
            ensure_ascii=False,
        ))
        return 0 if summary["artifact_integrity"]["status"] == "PASS" else 2
    if args.dataset_path.resolve() != DATASET_PATH.resolve() or hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest() != DATASET_SHA256:
        raise CleanEvalBlocked("PINNED_DATASET_HASH_MISMATCH")
    if args.prepare:
        if args.preflight_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("live-run options are only valid with --live")
        keys = list(CELLS) if args.prepare == "all" else [args.prepare]
        print(json.dumps(
            prepare_all_cells(keys, output_dir=args.output_dir),
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    if args.prepare_e2:
        if args.output_dir is not None or args.preflight_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("--prepare-e2 creates the sealed E2 preflight at its registered qualification directory")
        print(json.dumps(_prepare_e2_preflight(), indent=2, ensure_ascii=False))
        return 0
    if args.rehearse_final_evaluation:
        if (
            args.output_dir is not None
            or args.authorize_fresh_sh_v3
            or args.evaluation_plan != E2_MH_ONLY_EVALUATION_PLAN_ID
        ):
            parser.error("--rehearse-final-evaluation requires --evaluation-plan E2_MH_ONLY_REVISED_HELDOUT_E2_V1")
        print(json.dumps(
            _rehearse_final_evaluation_live_path(preflight_dir=args.preflight_dir),
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    if args.prepare_diagnostic:
        if args.output_dir is not None or args.preflight_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("live-run options are not valid with --prepare-diagnostic")
        print(json.dumps(_prepare_diagnostic_stress_qualification(), indent=2, ensure_ascii=False))
        return 0
    if args.check_route:
        if args.output_dir is not None or args.preflight_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("qualification directory options are not valid with --check-route")
        print(json.dumps(verify_local_provider_route(), indent=2, ensure_ascii=False))
        return 0
    if args.live:
        if args.output_dir is not None:
            parser.error("--output-dir is only valid with --prepare")
        if args.authorize_fresh_sh_v3 and args.live != "sh_32k":
            parser.error("--authorize-fresh-sh-v3 is only valid with --live sh_32k")
        print(json.dumps(
            _run_live(
                args.live,
                preflight_dir=args.preflight_dir,
                allow_authorized_fresh_sh_v3=args.authorize_fresh_sh_v3,
                evaluation_plan_id=args.evaluation_plan,
            ),
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    if args.diagnostic:
        if args.output_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("--diagnostic does not accept --output-dir or --authorize-fresh-sh-v3")
        print(json.dumps(
            _run_live(
                args.diagnostic,
                preflight_dir=args.preflight_dir,
                diagnostic_mode=True,
            ),
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    if args.diagnostic_stress:
        if args.output_dir is not None or args.authorize_fresh_sh_v3 or args.evaluation_plan:
            parser.error("--diagnostic-stress uses the sealed diagnostic qualification and does not accept live options")
        print(json.dumps(
            _run_live(
                "sh_32k",
                preflight_dir=args.preflight_dir,
                diagnostic_stress=True,
            ),
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    raise CleanEvalBlocked("UNKNOWN_CLEAN_EVAL_MODE")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CleanEvalBlocked as error:
        safe = _safe_clean_eval_block(error, None)
        safe.update({"status": "BLOCKED", "reason": safe["reason_code"]})
        print(json.dumps(safe, ensure_ascii=False))
        raise SystemExit(2)
    except CleanEvalIntegrityError as error:
        safe = _safe_run_level_failure(error)
        safe.update({"status": "BLOCKED", "reason": safe["reason_code"]})
        print(json.dumps(safe, ensure_ascii=False))
        raise SystemExit(2)
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED",
            "reason": f"UNEXPECTED_{type(error).__name__[:80]}",
        }, ensure_ascii=False))
        raise SystemExit(2)
