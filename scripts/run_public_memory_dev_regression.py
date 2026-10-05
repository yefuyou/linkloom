"""Run the SH-6k public-memory development regression without Gold or Provider calls."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.memoryagentbench.adapter import (  # noqa: E402
    GOLD_FREE_INPUT_COLUMNS,
    load_factconsolidation_case,
)
from benchmarks.memoryagentbench.g3_pilot import (  # noqa: E402
    G3_METHODS,
    METHOD_FLAT_RETRIEVAL,
    METHOD_TEMPORAL_MEMORY,
    prepare_method_context,
)
from benchmarks.memoryagentbench.memory import build_flat_index, build_temporal_memory  # noqa: E402
from benchmarks.memoryagentbench.public_memory_profile import (  # noqa: E402
    DATASET_PATH,
    DATASET_REVISION,
    DATASET_SHA256,
)


SOURCE = "factconsolidation_sh_6k"
KNOWN_GAP_FACT_IDS = (
    "fact:5",
    "fact:152",
    "fact:196",
    "fact:244",
    "fact:248",
    "fact:266",
    "fact:299",
    "fact:320",
    "fact:356",
    "fact:374",
    "fact:412",
)


def _latest_fact_id(case: Any, subject_key: str) -> str | None:
    matching = [fact for fact in case.facts if fact.subject_key == subject_key]
    return max(matching, key=lambda fact: fact.ordinal).fact_id if matching else None


def validate_pinned_dataset(dataset_path: Path) -> str:
    if dataset_path.resolve() != DATASET_PATH.resolve():
        raise ValueError("development regression requires the pinned SH-6k dataset path")
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if dataset_sha256 != DATASET_SHA256:
        raise ValueError("development regression requires the pinned SH-6k dataset SHA-256")
    return dataset_sha256


def run(dataset_path: Path) -> dict[str, Any]:
    dataset_path = dataset_path.resolve()
    dataset_sha256 = validate_pinned_dataset(dataset_path)
    case = load_factconsolidation_case(
        dataset_path,
        source=SOURCE,
        question_limit=100,
    )
    if len(case.questions) != 100:
        raise ValueError(f"expected 100 development questions; found {len(case.questions)}")
    if case.input_projection_columns != GOLD_FREE_INPUT_COLUMNS or hasattr(case, "answers"):
        raise ValueError("Gold data leaked into the normalized development case")

    flat_index = build_flat_index(case)
    memory = build_temporal_memory(case)
    try:
        prepared_by_pair: dict[tuple[str, str], Any] = {}
        for question in case.questions:
            for method in G3_METHODS:
                prepared_by_pair[(question.question_id, method)] = prepare_method_context(
                    case,
                    flat_index,
                    question,
                    method=method,
                    memory=memory if method == METHOD_TEMPORAL_MEMORY else None,
                )

        fact_by_id = {fact.fact_id: fact for fact in case.facts}
        parser_gap_checks: dict[str, Any] = {}
        for fact_id in KNOWN_GAP_FACT_IDS:
            fact = fact_by_id[fact_id]
            parser_gap_checks[fact_id] = {
                "parsed": fact.subject is not None and fact.predicate is not None and fact.value is not None,
                "subject_key": fact.subject_key,
                "predicate": fact.predicate,
                "extraction_rule": fact.extraction_rule,
            }

        temporal_assertions = {
            "imelda_lowercase_value_is_in_current_record": False,
            "sable_versions_share_key_and_latest_source_is_fact_299": False,
            "stephen_mcneil_versions_share_key_and_latest_source_is_fact_320": False,
            "ap1000_company_clause_versions_share_key_and_latest_source_is_fact_356": False,
            "karen_armstrong_value_with_internal_of_is_preserved_in_fact_412": False,
            "pedro_pierluisi_work_location_fact_5_is_parsed": False,
        }
        by_id = {fact.fact_id: fact for fact in case.facts}
        for fact_id in KNOWN_GAP_FACT_IDS:
            fact = by_id[fact_id]
            parser_gap_checks[fact_id]["latest_fact_for_key"] = (
                _latest_fact_id(case, fact.subject_key) if fact.subject_key else None
            )
        imelda = by_id["fact:374"]
        imelda_current = memory.store.get_current(case.workspace_id, imelda.subject_key or "")
        temporal_assertions["imelda_lowercase_value_is_in_current_record"] = bool(
            imelda.value == "atheism"
            and imelda_current is not None
            and imelda_current.value == "atheism"
            and imelda_current.source_evidence_refs == ("fact:374",)
        )
        sable_first, sable_current = by_id["fact:244"], by_id["fact:299"]
        sable_state = memory.store.get_current(case.workspace_id, sable_current.subject_key or "")
        temporal_assertions["sable_versions_share_key_and_latest_source_is_fact_299"] = bool(
            sable_first.subject_key == sable_current.subject_key
            and sable_state is not None
            and sable_state.source_evidence_refs == ("fact:299",)
            and sable_current.value == "Czech Republic"
        )
        work_first, work_current = by_id["fact:152"], by_id["fact:320"]
        work_state = memory.store.get_current(case.workspace_id, work_current.subject_key or "")
        temporal_assertions["stephen_mcneil_versions_share_key_and_latest_source_is_fact_320"] = bool(
            work_first.subject_key == work_current.subject_key
            and work_state is not None
            and work_state.source_evidence_refs == ("fact:320",)
        )
        company_first, company_current = by_id["fact:196"], by_id["fact:356"]
        company_state = memory.store.get_current(case.workspace_id, company_current.subject_key or "")
        temporal_assertions["ap1000_company_clause_versions_share_key_and_latest_source_is_fact_356"] = bool(
            company_first.subject_key == company_current.subject_key
            and company_state is not None
            and company_state.source_evidence_refs == ("fact:356",)
        )
        religion_first, religion_current = by_id["fact:266"], by_id["fact:412"]
        religion_state = memory.store.get_current(case.workspace_id, religion_current.subject_key or "")
        temporal_assertions["karen_armstrong_value_with_internal_of_is_preserved_in_fact_412"] = bool(
            religion_first.subject_key == religion_current.subject_key
            and religion_current.value == "Church of Scotland"
            and religion_state is not None
            and religion_state.value == "Church of Scotland"
            and religion_state.source_evidence_refs == ("fact:412",)
        )
        temporal_assertions["pedro_pierluisi_work_location_fact_5_is_parsed"] = bool(
            by_id["fact:5"].value == "Washington, D.C."
            and by_id["fact:5"].subject_key is not None
        )

        path_rows: list[dict[str, Any]] = []
        temporal_hits = 0
        temporal_misses = 0
        flat_empty_evidence = 0
        temporal_stale_paths = 0
        for question in case.questions:
            flat = prepared_by_pair[(question.question_id, METHOD_FLAT_RETRIEVAL)]
            temporal = prepared_by_pair[(question.question_id, METHOD_TEMPORAL_MEMORY)]
            if flat.memory_observation is not None:
                raise ValueError(f"Flat path exposed a memory observation for {question.question_id}")
            if not any(
                item.source_type.value == "evidence"
                for item in flat.context_bundle.selected
            ):
                flat_empty_evidence += 1
            if temporal.memory_observation is None:
                temporal_misses += 1
                if temporal.retrieval_evidence_ids or any(
                    item.source_type.value in {"evidence", "decision_memory"}
                    for item in temporal.context_bundle.selected
                ):
                    raise ValueError(f"Temporal miss had unsupported evidence for {question.question_id}")
            else:
                temporal_hits += 1
                observation = temporal.memory_observation
                refs = observation.get("source_evidence_refs")
                if (
                    observation.get("status") != "CURRENT"
                    or observation.get("source_identity_verified") is not True
                    or not isinstance(refs, list)
                    or len(refs) != 1
                    or temporal.retrieval_evidence_ids != tuple(refs)
                ):
                    raise ValueError(f"Temporal source readback failed for {question.question_id}")
                fact = fact_by_id.get(refs[0])
                if fact is None or fact.ordinal != observation.get("latest_ordered_fact"):
                    temporal_stale_paths += 1
                    raise ValueError(f"Temporal path did not select the latest source for {question.question_id}")
            path_rows.append(
                {
                    "question_id": question.question_id,
                    "flat_selected_evidence_ids": [
                        item.item_id
                        for item in flat.context_bundle.selected
                        if item.source_type.value == "evidence"
                    ],
                    "temporal_lookup": "HIT" if temporal.memory_observation is not None else "MISS",
                    "temporal_selected_evidence_ids": list(temporal.retrieval_evidence_ids),
                    "context_budget_pass": (
                        flat.context_bundle.estimated_tokens <= 3000
                        and temporal.context_bundle.estimated_tokens <= 3000
                    ),
                }
            )

        unmapped = [fact.fact_id for fact in case.facts if not fact.subject_key or not fact.value]
        rules = Counter(fact.extraction_rule or "UNMAPPED" for fact in case.facts)
        checks = {
            "all_100_questions_prepared_for_both_methods": len(prepared_by_pair) == 200,
            "no_flat_context_lacked_source_evidence": flat_empty_evidence == 0,
            "all_temporal_hits_read_back_current_provenance": temporal_stale_paths == 0,
            "all_known_parser_gap_facts_mapped": all(row["parsed"] for row in parser_gap_checks.values()),
            "all_known_temporal_gap_assertions_pass": all(temporal_assertions.values()),
            "unmapped_raw_predicates_preserved": all(
                fact.raw_predicate for fact in case.facts if not fact.subject_key or not fact.value
            ),
            "all_contexts_within_3000_tokens": all(row["context_budget_pass"] for row in path_rows),
            "gold_not_read": case.input_projection_columns == GOLD_FREE_INPUT_COLUMNS
            and not hasattr(case, "answers"),
        }
        return {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "classification": "DEVELOPMENT_REGRESSION_ONLY_NOT_HELD_OUT",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "dataset": {
                "path": str(dataset_path.relative_to(REPO)),
                "sha256": dataset_sha256,
                "revision": DATASET_REVISION,
                "source": SOURCE,
            },
            "counts": {
                "questions": len(case.questions),
                "numbered_facts": len(case.facts),
                "parsed_facts": len(case.facts) - len(unmapped),
                "unmapped_facts": len(unmapped),
                "extraction_rules": dict(sorted(rules.items())),
                "prepared_method_question_pairs": len(prepared_by_pair),
                "temporal_lookup_hits": temporal_hits,
                "temporal_lookup_misses": temporal_misses,
            },
            "checks": checks,
            "known_gap_fact_checks": parser_gap_checks,
            "temporal_assertions": temporal_assertions,
            "unmapped_fact_ids": unmapped,
            "unmapped_raw_predicates": {
                fact.fact_id: fact.raw_predicate
                for fact in case.facts
                if not fact.subject_key or not fact.value
            },
            "question_paths": path_rows,
            "accuracy": None,
            "gold_values_read": not checks["gold_not_read"],
            "provider_calls": {"generation": 0, "count_tokens": 0},
        }
    finally:
        memory.store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=REPO / "work" / "memoryagentbench-g2-20260925" / "Conflict_Resolution-00000-of-00001.parquet",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO / "docs" / "evaluation" / "public_memory_benchmark_clean" / "sh_6k_dev_regression.json",
    )
    args = parser.parse_args()
    result = run(args.dataset.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": result["checks"], "output": str(args.output)}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
