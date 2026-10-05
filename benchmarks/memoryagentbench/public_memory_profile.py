"""Immutable identity, pricing, and freeze-verification helpers for clean evals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


REPO = Path(__file__).resolve().parents[2]
CLEAN_EVAL_ROOT = REPO / "docs" / "evaluation" / "public_memory_benchmark_clean"
ARTIFACT_ROOT = REPO / ".artifacts" / "public_memory_benchmark_clean"
DATASET_PATH = REPO / "work" / "memoryagentbench-g2-20260925" / "Conflict_Resolution-00000-of-00001.parquet"
DATASET_SHA256 = "24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45"
DATASET_REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
MODEL = "gemini-3.8-flash"
SDK_VERSION = "2.22.0"
METHODS = ("Flat Retrieval", "LinkLoom Temporal Memory")
QUESTION_COUNT = 100
MAX_ATTEMPTS_PER_LOGICAL_GENERATION = 3
OUTPUT_TOKEN_CAP = 512
INPUT_TOKEN_CAP = 50_000
REQUEST_TIMEOUT_SECONDS = 60
INPUT_USD_PER_MILLION = 0.75
OUTPUT_USD_PER_MILLION = 3.75
GLOBAL_COST_CAP_USD = 30.0
PRICING_SNAPSHOT_DATE = "2026-09-26"
PRICING_SOURCE_URL = "https://ai.google.dev/gemini-api/docs/latest-model"
METRIC_SOURCE_URL = "https://github.com/HUST-AI-HYZ/MemoryAgentBench/blob/main/utils/eval_other_utils.py#L1571-L1689"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7897
MAX_INPUT_CONTEXT_TOKENS = 3_000
MAX_CONTEXT_CHARS = 12_000
PINNED_FREEZE_DIGESTS = {
    "sh_32k": {
        "manifest_sha256": "51286769d15e0070a7a09523646b37de3a78d1cf4a15130b0242efc280d5ff5a",
        "execution_plan_sha256": "0769addd255d21c215f88c96f7e646753e6fdd6d1965e7878c6dba4133ead353",
        "subset_sha256": "f321999a806288a1537a9f44b4f57345b2d6ec2cc13353f58b8b952851d7a3a5",
    },
    "mh_6k": {
        "manifest_sha256": "12b462f0dfc72c8b3c34ef57faf2d89172ecd6a5ae9782c1a095b1035b9e0629",
        "execution_plan_sha256": "8cd423ce231f1181400a606e5a88727bff1e9c10ae528e38b4cd648b2e184c5c",
        "subset_sha256": "98f2dae19635c15ed01dc1898356c75039c4e7c199fd63f451191ce08833e2b5",
    },
}


@dataclass(frozen=True, slots=True)
class EvalCell:
    slug: str
    display_name: str
    source: str
    freeze_filename: str
    execution_plan_filename: str
    hard_cost_cap_usd: float


CELLS: Mapping[str, EvalCell] = {
    "sh_32k": EvalCell(
        slug="sh_32k",
        display_name="SH-32k",
        source="factconsolidation_sh_32k",
        freeze_filename="sh_32k_freeze.json",
        execution_plan_filename="sh_32k_execution_plan.json",
        hard_cost_cap_usd=6.0,
    ),
    "mh_6k": EvalCell(
        slug="mh_6k",
        display_name="MH-6k",
        source="factconsolidation_mh_6k",
        freeze_filename="mh_6k_freeze.json",
        execution_plan_filename="mh_6k_execution_plan.json",
        hard_cost_cap_usd=10.0,
    ),
}


# The runner and profile itself are included, along with every source boundary
# that can change request construction, retrieval, memory state, context, score,
# Gemini configuration, or proxy routing.
FINGERPRINT_FILES = (
    "benchmarks/memoryagentbench/adapter.py",
    "benchmarks/memoryagentbench/memory.py",
    "benchmarks/memoryagentbench/g3_pilot.py",
    "benchmarks/memoryagentbench/public_memory_profile.py",
    "benchmarks/memoryagentbench/g32_heldout.py",
    "src/linkloom/indexing/bm25.py",
    "src/linkloom/indexing/models.py",
    "src/linkloom/indexing/__init__.py",
    "src/linkloom/context/__init__.py",
    "src/linkloom/context/models.py",
    "src/linkloom/context/manifest.py",
    "src/linkloom/context/assembler.py",
    "src/linkloom/retrieval_v2/models.py",
    "src/linkloom/agents/team_decision.py",
    "src/linkloom/agents/__init__.py",
    "src/linkloom/runtime/errors.py",
    "src/linkloom/runtime/models.py",
    "src/linkloom/experience/context.py",
    "src/linkloom/experience/models.py",
    "src/linkloom/experience/__init__.py",
    "src/linkloom/decision_memory/__init__.py",
    "src/linkloom/decision_memory/models.py",
    "src/linkloom/decision_memory/sources.py",
    "src/linkloom/decision_memory/store.py",
    "src/linkloom/decision_memory/materialization.py",
    "src/linkloom/decision_memory/tool.py",
    "src/linkloom/decision_memory/policy.py",
    "src/linkloom/decision_memory/reconciliation.py",
    "src/linkloom/agents/providers/gemini_api.py",
    "src/linkloom/agents/providers/retry_policy.py",
    "tests/smoke/test_m12_real_provider_team_decision_smoke.py",
    "tests/smoke/transport_observability.py",
    "scripts/run_public_memory_clean_eval.py",
    "docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json",
)


class CleanEvalIntegrityError(ValueError):
    """A frozen input or evaluation configuration failed verification."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "CLEAN_EVAL_INTEGRITY_VALIDATION_FAILED",
    ) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def implementation_fingerprints(repo_root: Path = REPO) -> dict[str, Any]:
    file_hashes: dict[str, str] = {}
    for relative in FINGERPRINT_FILES:
        path = repo_root / Path(relative)
        if not path.is_file():
            raise CleanEvalIntegrityError(f"implementation fingerprint input missing: {relative}")
        file_hashes[relative] = file_sha256(path)
    config = {
        "model": MODEL,
        "sdk_version": SDK_VERSION,
        "system_instruction": _reader_prompt(),
        "temperature": 0.0,
        "output_token_cap": OUTPUT_TOKEN_CAP,
        "methods": list(METHODS),
        "max_attempts_per_logical_generation": MAX_ATTEMPTS_PER_LOGICAL_GENERATION,
        "input_usd_per_million": INPUT_USD_PER_MILLION,
        "output_usd_per_million": OUTPUT_USD_PER_MILLION,
        "pricing_snapshot_date": PRICING_SNAPSHOT_DATE,
    }
    return {
        "files": file_hashes,
        "files_sha256": canonical_sha256(file_hashes),
        "runtime_config": config,
        "runtime_config_sha256": canonical_sha256(config),
    }


def _reader_prompt() -> str:
    from benchmarks.memoryagentbench.g3_pilot import READER_SYSTEM_INSTRUCTION

    return READER_SYSTEM_INSTRUCTION


def verify_frozen_artifacts(
    cell: EvalCell,
    *,
    root: Path = CLEAN_EVAL_ROOT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze_path = root / cell.freeze_filename
    plan_path = root / cell.execution_plan_filename
    try:
        manifest = json.loads(freeze_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError) as error:
        raise CleanEvalIntegrityError("frozen manifest or execution plan is unavailable") from error
    if not isinstance(manifest, dict) or not isinstance(plan, dict):
        raise CleanEvalIntegrityError("frozen inputs are not JSON objects")
    claimed_manifest_hash = manifest.get("freeze_manifest_sha256")
    unsigned_manifest = {key: value for key, value in manifest.items() if key != "freeze_manifest_sha256"}
    if claimed_manifest_hash != canonical_sha256(unsigned_manifest):
        raise CleanEvalIntegrityError("frozen manifest digest mismatch")
    expected = PINNED_FREEZE_DIGESTS.get(cell.slug)
    if expected is None or claimed_manifest_hash != expected["manifest_sha256"]:
        raise CleanEvalIntegrityError("frozen manifest digest differs from pinned digest")
    if (
        manifest.get("schema_version") != "linkloom-public-memory-heldout-freeze/v1"
        or manifest.get("classification") != "CLEAN_HELD_OUT_FROZEN_BEFORE_ADAPTER_CHANGE"
        or manifest.get("cell") != cell.display_name
    ):
        raise CleanEvalIntegrityError("frozen manifest identity mismatch")
    dataset = manifest.get("dataset")
    selection = manifest.get("selection")
    hashes = manifest.get("hashes")
    if not isinstance(dataset, dict) or not isinstance(selection, dict) or not isinstance(hashes, dict):
        raise CleanEvalIntegrityError("frozen manifest fields are missing")
    if (
        hashes.get("subset_sha256") != expected["subset_sha256"]
        or hashes.get("execution_plan_file") != cell.execution_plan_filename
        or hashes.get("execution_plan_sha256") != expected["execution_plan_sha256"]
    ):
        raise CleanEvalIntegrityError("frozen subset or plan digest differs from pinned digest")
    if (
        dataset.get("id") != "ai-hyz/MemoryAgentBench"
        or dataset.get("revision") != DATASET_REVISION
        or dataset.get("data_file_sha256") != DATASET_SHA256
        or dataset.get("source_selector") != cell.source
        or dataset.get("data_file") != DATASET_PATH.name
    ):
        raise CleanEvalIntegrityError("official frozen dataset identity mismatch")
    if file_sha256(DATASET_PATH) != DATASET_SHA256:
        raise CleanEvalIntegrityError("local pinned Parquet file hash mismatch")
    if plan.get("schema_version") != "linkloom-public-memory-execution-plan/v1":
        raise CleanEvalIntegrityError("execution plan schema mismatch")
    if (
        plan.get("cell") != cell.display_name
        or plan.get("provider_calls_at_freeze") != 0
        or plan.get("qa_order") != "official source-list order; no random sampling"
        or plan.get("method_order_per_qa") != list(METHODS)
    ):
        raise CleanEvalIntegrityError("execution plan identity or freeze call count mismatch")
    plan_rows = plan.get("execution_plan")
    qa_ids = selection.get("qa_ids_in_official_order")
    if (
        not isinstance(plan_rows, list)
        or not isinstance(qa_ids, list)
        or len(qa_ids) != QUESTION_COUNT
        or len(plan_rows) != QUESTION_COUNT * len(METHODS)
        or selection.get("question_count") != QUESTION_COUNT
        or plan.get("method_case_count") != QUESTION_COUNT * len(METHODS)
    ):
        raise CleanEvalIntegrityError("frozen question or method-case count mismatch")
    expected_rows = [
        {
            "sequence": sequence,
            "qa_index": index,
            "qa_id": qa_id,
            "method": method,
        }
        for sequence, (index, qa_id, method) in enumerate(
            ((index, qa_id, method) for index, qa_id in enumerate(qa_ids) for method in METHODS),
            start=1,
        )
    ]
    if plan_rows != expected_rows:
        raise CleanEvalIntegrityError("frozen execution order mismatch")
    if (
        hashes.get("execution_plan_sha256") != canonical_sha256(plan)
        or canonical_sha256(plan) != expected["execution_plan_sha256"]
    ):
        raise CleanEvalIntegrityError("frozen execution plan digest mismatch")
    gold = manifest.get("gold_isolation")
    prior = manifest.get("prior_use_audit")
    if (
        not isinstance(gold, dict)
        or gold.get("gold_values_read") is not False
        or gold.get("gold_rationale_read") is not False
        or not isinstance(prior, dict)
        or prior.get("prior_use_overlap") != 0
    ):
        raise CleanEvalIntegrityError("freeze gold or overlap assertion mismatch")
    return manifest, plan


def request_cost_upper_bound_usd(static_input_token_bound: int) -> float:
    if isinstance(static_input_token_bound, bool) or not isinstance(static_input_token_bound, int) or static_input_token_bound < 0:
        raise ValueError("static input token bound must be a non-negative integer")
    return (
        2 * static_input_token_bound * INPUT_USD_PER_MILLION
        + OUTPUT_TOKEN_CAP * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
