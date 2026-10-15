"""Seal H1/H2 offline gates for the SH-32k harness diagnostic run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "src")]

from scripts.audit_public_memory_clean_eval_guards import build_guard_inventory  # noqa: E402
import scripts.run_public_memory_clean_eval as runner  # noqa: E402


REQUIRED_TESTS = {
    "test_clean_eval_reason_codes_include_stable_category",
    "test_stable_reason_family_covers_required_categories",
    "test_every_clean_eval_block_throw_site_has_a_reason_expression",
    "test_guard_inventory_covers_each_production_clean_eval_throw_site",
    "test_accepted_response_evidence_is_durable_and_contains_no_response_text",
    "test_accepted_response_followed_by_clean_eval_block_is_local_and_diagnostic",
    "test_diagnostic_stress_continues_after_classified_case_local_guard",
    "test_diagnostic_stress_retries_only_transient_provider_failure",
    "test_accepted_response_is_fsynced_before_downstream_guard_artifact",
    "test_provider_completion_rate_does_not_count_local_block_as_provider_error",
    "test_pre_response_provider_failure_remains_provider_error",
    "test_accepted_response_guard_categories_are_independently_sealed",
    "test_block_artifact_has_bounded_safe_origin_without_prompt_or_secrets",
    "test_run_level_block_artifacts_reconstruct_without_run_summary",
}


def _test_report(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failed_names: list[str] = []
    skipped_names: list[str] = []
    seen_names: set[str] = set()
    for case in cases:
        name = case.attrib.get("name", "").split("[", 1)[0]
        seen_names.add(name)
        if case.find("failure") is not None or case.find("error") is not None:
            failed_names.append(name)
        if case.find("skipped") is not None:
            skipped_names.append(name)
    total = len(cases)
    failures = len(failed_names)
    skipped = len(skipped_names)
    missing = sorted(REQUIRED_TESTS - seen_names)
    return {
        "total": total,
        "passed": total - failures - skipped,
        "failures": failures,
        "skipped": skipped,
        "failed_test_names": sorted(set(failed_names)),
        "skipped_test_names": sorted(set(skipped_names)),
        "required_tests_missing": missing,
        "required_tests": sorted(REQUIRED_TESTS),
    }


def _preflight_valid() -> tuple[bool, dict[str, Any]]:
    path = runner.DIAGNOSTIC_DIR / "sh_32k_preflight.json"
    if not runner._verify_seal(path, identity="offline-preflight:sh_32k"):
        return False, {"status": "FAIL", "reason": "PREFLIGHT_SEAL_INVALID"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError):
        return False, {"status": "FAIL", "reason": "PREFLIGHT_UNREADABLE"}
    if not isinstance(payload, dict):
        return False, {"status": "FAIL", "reason": "PREFLIGHT_SCHEMA_INVALID"}
    valid = (
        payload.get("status") == "PASS"
        and payload.get("purpose") == "HARNESS_STABILITY"
        and payload.get("dataset_use") == "DIAGNOSTIC_STRESS_SET"
        and payload.get("clean_accuracy_claim") is False
        and payload.get("scoring") == "DISABLED"
        and payload.get("gold_values_read") is False
        and payload.get("provider_calls") == {"generation": 0, "count_tokens": 0}
        and payload.get("prepared_method_case_count") == 200
        and payload.get("implementation_fingerprints") == runner.implementation_fingerprints()
    )
    return valid, {
        "status": "PASS" if valid else "FAIL",
        "prepared_method_case_count": payload.get("prepared_method_case_count"),
        "gold_values_read": payload.get("gold_values_read"),
        "provider_calls": payload.get("provider_calls"),
        "implementation_fingerprint_sha256": payload.get("implementation_fingerprints", {}).get("files_sha256"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def qualify(regression_xml: Path) -> dict[str, Any]:
    DIAGNOSTIC_DIR = runner.DIAGNOSTIC_DIR
    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    fingerprints = runner.implementation_fingerprints()
    inventory = build_guard_inventory()
    inventory["implementation_fingerprint_sha256"] = fingerprints["files_sha256"]
    inventory["reason_category_counts"] = {
        category: sum(site["failure_category"] == category for site in inventory["sites"])
        for category in sorted({site["failure_category"] for site in inventory["sites"]})
    }
    inventory["status"] = (
        "PASS"
        if inventory["status"] == "PASS"
        and inventory["production_reachable_throw_sites"] == len(inventory["sites"])
        and inventory["uninstrumented_throw_sites"] == 0
        else "FAIL"
    )
    inventory_path = DIAGNOSTIC_DIR / "H1_guard_inventory.json"
    runner._atomic_json_write_fsync(inventory_path, inventory)
    runner._seal_file(inventory_path, identity="harness-diagnostic-h1-inventory")

    tests = _test_report(regression_xml)
    preflight_ok, preflight = _preflight_valid()
    h2_pass = (
        inventory["status"] == "PASS"
        and preflight_ok
        and tests["passed"] >= 340
        and tests["failures"] == 0
        and tests["skipped"] == 0
        and not tests["required_tests_missing"]
        and fingerprints["files_sha256"] == inventory["implementation_fingerprint_sha256"]
        and fingerprints["files_sha256"] == preflight["implementation_fingerprint_sha256"]
    )
    qualification = {
        "schema_version": "linkloom-harness-diagnostic-h2-qualification/v1",
        "status": "PASS" if h2_pass else "FAIL",
        "purpose": "HARNESS_STABILITY",
        "provider_calls": {"generation": 0, "count_tokens": 0},
        "provider_boundary": "OFFLINE_FAKE_ONLY",
        "gold_values_read": False,
        "mh_6k_live_or_preparation_run": False,
        "scoring": "DISABLED_FOR_DIAGNOSTIC_REPLAY",
        "sh_32k_classification": "DIAGNOSTIC_STRESS_SET_NOT_CLEAN_HELD_OUT",
        "diagnostic_preflight": preflight,
        "static_throw_site_count": inventory["production_reachable_throw_sites"],
        "uninstrumented_throw_sites": inventory["uninstrumented_throw_sites"],
        "offline_regression": {
            "path": regression_xml.resolve().relative_to(REPO).as_posix(),
            **tests,
        },
        "synthetic_lifecycle_qualification": {
            "diagnostic_run_status": "HARNESS_STRESS_PASS" if h2_pass else "NOT_QUALIFIED",
            "terminal_method_cases": 200 if h2_pass else None,
            "classified_case_local_guard_continued": h2_pass,
            "transient_retry_bounded_and_continued": h2_pass,
            "response_hash_durable_before_guard": h2_pass,
            "block_artifacts_reconstruct_without_summary": h2_pass,
        },
        "implementation_fingerprints": fingerprints,
        "implementation_fingerprint_sha256": fingerprints["files_sha256"],
        "created_at_utc": runner.datetime.now(runner.UTC).isoformat(),
    }
    qualification_path = DIAGNOSTIC_DIR / "H2_stress_qualification.json"
    runner._atomic_json_write_fsync(qualification_path, qualification)
    runner._seal_file(qualification_path, identity="harness-diagnostic-h2-qualification")
    return {
        "h1_status": inventory["status"],
        "h2_status": qualification["status"],
        "throw_sites": inventory["production_reachable_throw_sites"],
        "regression_passed": tests["passed"],
        "required_tests_missing": tests["required_tests_missing"],
        "provider_calls": qualification["provider_calls"],
        "output": str(qualification_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regression-xml", type=Path, required=True)
    args = parser.parse_args()
    result = qualify(args.regression_xml)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["h1_status"] == "PASS" and result["h2_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
