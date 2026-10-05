from __future__ import annotations

import json
import hashlib

import pytest


def test_h3_1_reconciliation_uses_its_independent_diagnostic_ledger() -> None:
    import scripts.run_public_memory_clean_eval as runner

    ledger = json.loads(runner.H3_1_DIAGNOSTIC_LEDGER_PATH.read_text(encoding="utf-8"))
    h3_row = next(row for row in ledger["runs"] if row["run_id"] == runner.H3_1_DIAGNOSTIC_RUN_ID)
    h2_ledger = json.loads((runner.DIAGNOSTIC_DIR / "diagnostic_budget_ledger.json").read_text(encoding="utf-8"))

    assert h3_row["status"] == "HARNESS_STRESS_PASS"
    assert h3_row["estimated_spend_upper_bound_usd"] == runner.H3_1_DIAGNOSTIC_SPEND_UPPER_BOUND_USD
    assert h3_row["reserved_usd"] == 0.0
    assert all(row["run_id"] != runner.H3_1_DIAGNOSTIC_RUN_ID for row in h2_ledger["runs"])


def test_pre_protocol_formal_history_is_counted_only_with_closed_non_scoreable_evidence() -> None:
    import scripts.run_public_memory_clean_eval as runner

    run_id = runner.ORIGINAL_STOPPED_SH_RUN_ID
    manifest_path = runner.ARTIFACT_ROOT / "sh_32k" / run_id / "run_manifest.json"
    summary_path = runner.ARTIFACT_ROOT / "sh_32k" / run_id / "pilot_summary.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert runner._legacy_formal_run_is_non_scoreable(run_id, manifest, summary)

    manifest["provider_completion_rate"] = 0.90
    assert not runner._legacy_formal_run_is_non_scoreable(run_id, manifest, summary)


def test_junit_counts_supports_pytest_testsuites_wrapper() -> None:
    import xml.etree.ElementTree as ET

    import scripts.run_public_memory_clean_eval as runner

    root = ET.fromstring(
        "<testsuites><testsuite tests='20' failures='0' errors='0' />"
        "<testsuite tests='5' failures='1' errors='0' /></testsuites>"
    )

    assert runner._junit_counts(root) == (25, 1, 0)


def test_mh_only_readiness_requires_only_the_mh_preflight(tmp_path, monkeypatch) -> None:
    import scripts.run_public_memory_clean_eval as runner

    fingerprints = {"files": {}, "files_sha256": "f" * 64, "runtime_config": {}, "runtime_config_sha256": "c" * 64}
    preflight = {
        "implementation_fingerprints": fingerprints,
        "cell": "MH-6k",
        "source": "factconsolidation_mh_6k",
        "dataset_revision": "revision",
        "dataset_sha256": "d" * 64,
        "subset_sha256": "s" * 64,
        "freeze_manifest_sha256": "m" * 64,
        "frozen_execution_plan_sha256": "p" * 64,
        "runtime_execution_plan_sha256": "r" * 64,
        "case_id": "case",
        "workspace_id": "workspace",
        "question_count": 100,
        "fact_count": 1,
        "prepared_method_case_count": 200,
        "one_attempt_base_cost_upper_bound_usd": 1.0,
        "execution_plan": [{"sequence": index} for index in range(200)],
    }
    read_cells = []
    monkeypatch.setattr(runner, "implementation_fingerprints", lambda: fingerprints)
    monkeypatch.setattr(runner, "_read_preflight", lambda key, **_kwargs: read_cells.append(key) or preflight)
    monkeypatch.setattr(runner, "_prepare_cell", lambda _key: preflight)
    monkeypatch.setattr(runner, "_resolve_eval_artifact_dir", lambda path: path)

    frozen, actual_fingerprints = runner._verify_ready(
        "mh_6k",
        preflight_dir=tmp_path,
        evaluation_plan_id=runner.MH_ONLY_EVALUATION_PLAN_ID,
    )

    assert read_cells == ["mh_6k"]
    assert frozen is preflight
    assert actual_fingerprints is fingerprints

    read_cells.clear()
    runner._verify_ready(
        "mh_6k",
        preflight_dir=tmp_path,
        evaluation_plan_id=runner.LEGACY_DUAL_EVALUATION_PLAN_ID,
    )
    assert read_cells == list(runner.CELLS)


def test_versioned_mh_only_plan_removes_only_the_mh_sh_prerequisite() -> None:
    import scripts.run_public_memory_clean_eval as runner

    mh_plan = runner._load_evaluation_plan("MH_ONLY_CLEAN_V1")
    legacy_plan = runner._load_evaluation_plan("SH32K_THEN_MH6K_V3")

    assert mh_plan["dataset_sequence"] == ["mh_6k"]
    assert mh_plan["mh_prerequisites"] == []
    assert mh_plan["diagnostic_classification"] == "SH_32K:DIAGNOSTIC_STRESS_SET"
    assert legacy_plan["dataset_sequence"] == ["sh_32k", "mh_6k"]
    assert legacy_plan["mh_prerequisites"] == [
        {"cell": "sh_32k", "minimum_completed_runs": 1, "fingerprint_must_match": True}
    ]


def test_mh_only_authorization_binds_baseline_to_plan_and_fingerprints(tmp_path) -> None:
    import scripts.run_public_memory_clean_eval as runner

    plan = runner._load_evaluation_plan("MH_ONLY_CLEAN_V1")
    registry_sha = hashlib.sha256(runner.EVALUATION_PLAN_REGISTRY_PATH.read_bytes()).hexdigest()
    formal_ledger = runner._formal_budget_ledger_from_history([])
    fingerprints = {
        "files_sha256": "f" * 64,
        "runtime_config_sha256": "c" * 64,
        "files": {"docs/evaluation/public_memory_benchmark_clean/evaluation_plan_registry.json": registry_sha},
    }
    qualification_artifacts = {}
    artifact_payloads = {
        "mh_6k_preflight.json": {
            "status": "PASS",
            "classification": "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER",
            "prepared_method_case_count": 200,
            "question_count": 100,
            "gold_values_read": False,
            "provider_calls": {"generation": 0, "count_tokens": 0},
            "subset_sha256": "s" * 64,
        },
        "offline_qualification.json": {
            "status": "PASS",
            "provider_calls": 0,
            "official_gold_reads": 0,
            "scoring_enabled": False,
            "checks": {
                "mh_6k_frozen_subset": "PASS",
                "gold_isolation": "PASS",
                "formal_fake_provider_e2e": "PASS",
                "usage_incomplete_fake_provider": "PASS",
                "journal_evidence_finalizer": "PASS",
                "ledger_reconciliation": "PASS",
                "cost_hard_caps": "PASS",
                "clash_7897_route": "PASS",
                "canonical_regression": "PASS",
                "score_protocol": "PASS",
            },
        },
        "semantic_equivalence.json": {
            "status": "PASS",
            "generation_request_semantics_equal": True,
            "semantic_invariants": {
                "adapter": True,
                "temporal_memory": True,
                "retrieval": True,
                "prompt": True,
                "gemini_generation_config": True,
                "dataset_subset": True,
                "scoring_rules": True,
            },
        },
        "route_preflight.json": {
            "status": "PASS",
            "provider_requests": 0,
            "local_proxy": {"hostname": runner.PROXY_HOST, "port": runner.PROXY_PORT, "tcp": "PASS"},
            "sdk_httpx_route": {"route_inspection": "PROXY", "configured_proxy_port": runner.PROXY_PORT},
        },
        "ledger_reconciliation_report.json": {"status": "PASS"},
        "formal_budget_ledger.json": formal_ledger,
        "focused_tests.xml": "<testsuite tests='7' failures='0' errors='0' />\n",
        "validation_regression.xml": "<testsuite tests='422' failures='0' errors='0' />\n",
    }
    for filename, identity in runner.MH_ONLY_REQUIRED_QUALIFICATION_ARTIFACTS.items():
        artifact_path = tmp_path / filename
        payload = artifact_payloads[filename]
        if isinstance(payload, str):
            artifact_path.write_text(payload, encoding="utf-8")
        else:
            artifact_path.write_text(json.dumps(payload), encoding="utf-8")
        runner._seal_file(artifact_path, identity=identity)
        qualification_artifacts[filename] = {
            "path": filename,
            "identity": identity,
            "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            "seal_sha256": hashlib.sha256(artifact_path.with_suffix(artifact_path.suffix + ".seal.json").read_bytes()).hexdigest(),
        }
    score_protocol_artifacts = {}
    for filename, identity in (
        ("E1_SCORE_PROTOCOL.json", "e1-score-protocol"),
        ("E1_SCORE_PROTOCOL_AMENDMENT.json", "e1-score-protocol-amendment"),
    ):
        score_path = runner.E1_SCORE_PROTOCOL_ROOT / filename
        seal_path = score_path.with_suffix(score_path.suffix + ".seal.json")
        score_protocol_artifacts[filename] = {
            "path": f"{runner.E1_SCORE_PROTOCOL_ROOT.name}/{filename}",
            "identity": identity,
            "sha256": hashlib.sha256(score_path.read_bytes()).hexdigest(),
            "seal_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
        }
    baseline = {
        "schema_version": "linkloom-authorized-mh-only-clean-baseline/v1",
        "baseline_id": "AUTHORIZED_MH_ONLY_CLEAN_BASELINE",
        "status": "AUTHORIZED_MH_ONLY_CLEAN_BASELINE",
        "live_execution_authorized": True,
        "evaluation_plan_id": "MH_ONLY_CLEAN_V1",
        "evaluation_plan_sha256": runner._evaluation_plan_sha256(plan),
        "evaluation_plan_registry_sha256": registry_sha,
        "fingerprints": fingerprints,
        "qualification_artifacts": qualification_artifacts,
        "formal_budget_ledger_sha256": formal_ledger["ledger_sha256"],
        "mh_6k_subset_sha256": "s" * 64,
        "score_protocol_artifacts": score_protocol_artifacts,
    }
    path = tmp_path / "AUTHORIZED_MH_ONLY_CLEAN_BASELINE.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    runner._seal_file(path, identity="authorized-mh-only-clean-baseline")

    runner._verify_authorized_mh_only_baseline(tmp_path, fingerprints, plan)

    offline = artifact_payloads["offline_qualification.json"]
    offline["checks"]["unlisted_check"] = "PASS"
    offline_path = tmp_path / "offline_qualification.json"
    offline_path.write_text(json.dumps(offline), encoding="utf-8")
    runner._seal_file(offline_path, identity="mh-only-offline-qualification")
    qualification_artifacts["offline_qualification.json"].update(
        sha256=hashlib.sha256(offline_path.read_bytes()).hexdigest(),
        seal_sha256=hashlib.sha256(offline_path.with_suffix(offline_path.suffix + ".seal.json").read_bytes()).hexdigest(),
    )
    baseline["qualification_artifacts"] = qualification_artifacts
    path.write_text(json.dumps(baseline), encoding="utf-8")
    runner._seal_file(path, identity="authorized-mh-only-clean-baseline")
    with pytest.raises(runner.CleanEvalBlocked, match="MH_ONLY_QUALIFICATION_REPORT_FAILED"):
        runner._verify_authorized_mh_only_baseline(tmp_path, fingerprints, plan)

    baseline["evaluation_plan_sha256"] = "0" * 64
    path.write_text(json.dumps(baseline), encoding="utf-8")
    runner._seal_file(path, identity="authorized-mh-only-clean-baseline")
    with pytest.raises(runner.CleanEvalBlocked, match="MH_ONLY_BASELINE_OR_PLAN_MISMATCH"):
        runner._verify_authorized_mh_only_baseline(tmp_path, fingerprints, plan)


def test_h3_1_budget_reconciliation_is_idempotent_and_releases_reservation() -> None:
    import scripts.run_public_memory_clean_eval as runner

    ledger = {
        "schema_version": "linkloom-public-memory-global-budget/v1",
        "hard_cap_usd": 10.0,
        "active_run_id": "bc850e0cfe4745d79117660dd5a74367",
        "runs": [
            {
                "run_id": "bc850e0cfe4745d79117660dd5a74367",
                "cell": "sh_32k",
                "status": "RUNNING",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 6.0,
                "estimated_spend_upper_bound_usd": 0.0,
            }
        ],
    }

    changed = runner._reconcile_budget_row(
        ledger,
        run_id="bc850e0cfe4745d79117660dd5a74367",
        status="HARNESS_STRESS_PASS",
        estimated_spend_upper_bound_usd=0.8019,
        expected_reserved_usd=6.0,
        hard_cap_usd=10.0,
    )
    again = runner._reconcile_budget_row(
        ledger,
        run_id="bc850e0cfe4745d79117660dd5a74367",
        status="HARNESS_STRESS_PASS",
        estimated_spend_upper_bound_usd=0.8019,
        expected_reserved_usd=6.0,
        hard_cap_usd=10.0,
    )

    assert changed is True
    assert again is False
    assert ledger["active_run_id"] is None
    assert ledger["runs"][0]["status"] == "HARNESS_STRESS_PASS"
    assert ledger["runs"][0]["estimated_spend_upper_bound_usd"] == 0.8019
    assert ledger["runs"][0]["reserved_usd"] == 0.0
    with pytest.raises(runner.CleanEvalBlocked, match="BUDGET_RECONCILIATION_CONFLICT"):
        runner._reconcile_budget_row(
            ledger,
            run_id="bc850e0cfe4745d79117660dd5a74367",
            status="HARNESS_STRESS_PASS",
            estimated_spend_upper_bound_usd=0.8018,
            expected_reserved_usd=6.0,
            hard_cap_usd=10.0,
        )


def test_budget_reconciliation_journal_is_append_only_and_idempotent(tmp_path) -> None:
    import scripts.run_public_memory_clean_eval as runner

    journal = tmp_path / "budget_reconciliation_events.jsonl"
    payload = {
        "event_type": "SETTLE_DIAGNOSTIC_RESERVATION",
        "run_id": "bc850e0cfe4745d79117660dd5a74367",
        "estimated_spend_upper_bound_usd": 0.8019,
        "reservation_released_usd": 6.0,
    }

    first = runner._append_reconciliation_event(journal, payload)
    second = runner._append_reconciliation_event(journal, payload)

    assert first == second
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
    assert runner._read_reconciliation_events(journal) == [first]
    journal.write_text(journal.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(runner.CleanEvalBlocked, match="BUDGET_RECONCILIATION_LOG_INTEGRITY_FAILURE"):
        runner._read_reconciliation_events(journal)


def test_formal_budget_is_separate_and_includes_all_sealed_formal_sh_history() -> None:
    import scripts.run_public_memory_clean_eval as runner

    ledger = runner._formal_budget_ledger_from_history(
        [
            {
                "run_id": "formal-sh-v1",
                "cell": "sh_32k",
                "status": "PROVIDER_COMPLETION_BELOW_90_PERCENT",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": 0.004374,
            },
            {
                "run_id": "formal-sh-v2",
                "cell": "sh_32k",
                "status": "STOPPED",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": 0.080115,
            },
            {
                "run_id": "formal-sh-v3",
                "cell": "sh_32k",
                "status": "STOPPED",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": 0.184356,
            },
        ]
    )

    assert ledger["budget_scope"] == "FORMAL_EVALUATION"
    assert ledger["hard_cap_usd"] == 30.0
    assert sum(row["estimated_spend_upper_bound_usd"] for row in ledger["runs"]) == pytest.approx(0.268845)
    assert "bc850e0cfe4745d79117660dd5a74367" not in {row["run_id"] for row in ledger["runs"]}
    assert runner._cost_is_below_cap(
        runner._ledger_reserved_total(ledger) + runner.CELLS["mh_6k"].hard_cost_cap_usd,
        30.0,
    )


def test_score_outcome_metrics_use_fixed_planned_denominator() -> None:
    import scripts.run_public_memory_clean_eval as runner

    metrics = runner._score_outcome_metrics(
        [
            {"score": True, "provider_outcome": "PROVIDER_COMPLETE"},
            {"score": False, "provider_outcome": "PROVIDER_COMPLETE"},
            {"score": None, "provider_outcome": "PROVIDER_ERROR"},
            {"score": None, "provider_outcome": "LOCAL_HARNESS_BLOCK"},
        ],
        planned_denominator=100,
    )

    assert metrics == {
        "correct": 1,
        "incorrect": 1,
        "not_evaluated": 98,
        "provider_failure": 1,
        "local_harness_block": 1,
        "scored_coverage": 0.02,
        "primary_accuracy": 0.01,
    }
