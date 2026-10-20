from __future__ import annotations

import json

import pytest

from benchmarks.memoryagentbench.public_memory_profile import (
    CELLS,
    CLEAN_EVAL_ROOT,
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    OUTPUT_TOKEN_CAP,
    CleanEvalIntegrityError,
    canonical_sha256,
    request_cost_upper_bound_usd,
    verify_frozen_artifacts,
)


@pytest.mark.parametrize("cell_key", ("sh_32k", "mh_6k"))
def test_clean_cell_freeze_artifacts_verify_without_gold(cell_key: str) -> None:
    cell = CELLS[cell_key]

    manifest, plan = verify_frozen_artifacts(cell)

    assert manifest["selection"]["question_count"] == 100
    assert manifest["gold_isolation"]["gold_values_read"] is False
    assert manifest["prior_use_audit"]["prior_use_overlap"] == 0
    assert plan["provider_calls_at_freeze"] == 0
    assert len(plan["execution_plan"]) == 200
    assert plan["execution_plan"][0]["method"] == "Flat Retrieval"
    assert plan["execution_plan"][1]["method"] == "LinkLoom Temporal Memory"


def test_request_cost_reserve_includes_count_tokens_and_output_cap() -> None:
    bound = request_cost_upper_bound_usd(10_000)

    expected = (
        2 * 10_000 * INPUT_USD_PER_MILLION
        + OUTPUT_TOKEN_CAP * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    assert bound == expected
    assert bound * 200 < CELLS["sh_32k"].hard_cost_cap_usd


@pytest.mark.parametrize("cell_key", ("sh_32k", "mh_6k"))
def test_self_resigned_freeze_and_plan_tampering_is_rejected(
    cell_key: str,
    tmp_path,
) -> None:
    cell = CELLS[cell_key]
    manifest_path = tmp_path / cell.freeze_filename
    plan_path = tmp_path / cell.execution_plan_filename
    manifest = json.loads((CLEAN_EVAL_ROOT / cell.freeze_filename).read_text(encoding="utf-8"))
    plan = json.loads((CLEAN_EVAL_ROOT / cell.execution_plan_filename).read_text(encoding="utf-8"))

    tampered_id = f"{manifest['selection']['qa_ids_in_official_order'][0]}_tampered"
    manifest["selection"]["qa_ids_in_official_order"][0] = tampered_id
    manifest["selection"]["question_sha256_by_id"][tampered_id] = manifest["selection"]["question_sha256_by_id"].pop(
        next(iter(manifest["selection"]["question_sha256_by_id"]))
    )
    plan["execution_plan"][0]["qa_id"] = tampered_id
    manifest["hashes"]["subset_sha256"] = "0" * 64
    manifest["hashes"]["execution_plan_sha256"] = canonical_sha256(plan)
    unsigned_manifest = {key: value for key, value in manifest.items() if key != "freeze_manifest_sha256"}
    manifest["freeze_manifest_sha256"] = canonical_sha256(unsigned_manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CleanEvalIntegrityError, match="pinned"):
        verify_frozen_artifacts(cell, root=tmp_path)


def test_runtime_cost_caps_are_strict_at_equality() -> None:
    from scripts.run_public_memory_clean_eval import _cost_is_below_cap

    assert _cost_is_below_cap(5.999999, 6.0)
    assert not _cost_is_below_cap(6.0, 6.0)
    assert not _cost_is_below_cap(float("nan"), 6.0)


def test_attempt_journal_writer_appends_to_the_passed_journal_path(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import (
        _append_attempt_journal,
        _validate_attempt_journal_events,
    )

    journal_path = tmp_path / "provider_attempt_journal.jsonl"
    journal_path.write_text("", encoding="utf-8")
    events = [
        {"event": "CASE_METHOD_STARTED", "sequence": 1},
        {"event": "PROVIDER_ATTEMPT_STARTED", "sequence": 1, "attempt_no": 1},
        {"event": "PROVIDER_ATTEMPT_COMPLETED", "sequence": 1, "attempt_no": 1},
        {"event": "CASE_METHOD_COMPLETED", "sequence": 1},
        {"event": "CASE_METHOD_SCORED", "sequence": 1},
    ]

    for event in events:
        _append_attempt_journal(journal_path, event)

    rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert rows == events
    assert _validate_attempt_journal_events(rows)["method_case_lifecycles_closed"] is True
    assert _validate_attempt_journal_events(rows[:2])["integrity_valid"] is False


def test_attempt_journal_closes_case_aborted_before_provider_attempt() -> None:
    from scripts.run_public_memory_clean_eval import _validate_attempt_journal_events

    summary = _validate_attempt_journal_events(
        [
            {"event": "CASE_METHOD_STARTED", "sequence": 1},
            {
                "event": "CASE_METHOD_ABORTED",
                "sequence": 1,
                "reason": "GLOBAL_DYNAMIC_COST_GUARD_BLOCKED",
            },
        ]
    )

    assert summary["integrity_valid"] is True
    assert summary["method_case_lifecycles_closed"] is True
    assert summary["aborted_method_cases"] == 1
    assert summary["provider_attempt_starts"] == 0
    assert summary["protocol_complete"] is False


def test_attempt_journal_rejects_retry_started_after_response_acceptance() -> None:
    from scripts.run_public_memory_clean_eval import _validate_attempt_journal_events

    request_hash = "a" * 64
    events = [
        {"event": "CASE_METHOD_STARTED", "sequence": 1},
        {
            "event": "PROVIDER_ATTEMPT_STARTED",
            "sequence": 1,
            "attempt_no": 1,
            "attempt_id": "1:1",
            "generation_request_sha256": request_hash,
        },
        {
            "event": "PROVIDER_RESPONSE_ACCEPTED",
            "sequence": 1,
            "attempt_no": 1,
            "attempt_id": "1:1",
            "provider_response_accepted": True,
            "request_sha256": request_hash,
            "response_sha256": "b" * 64,
            "model": "gemini-test-model",
            "usage_present": True,
            "usage_metadata": {"input_tokens": 5, "output_tokens": 1},
        },
        {
            "event": "PROVIDER_ATTEMPT_COMPLETED",
            "sequence": 1,
            "attempt_no": 1,
            "provider_response_accepted": True,
            "provider_outcome": "LOCAL_HARNESS_BLOCK",
        },
        {
            "event": "PROVIDER_ATTEMPT_STARTED",
            "sequence": 1,
            "attempt_no": 2,
            "attempt_id": "1:2",
            "generation_request_sha256": request_hash,
        },
        {
            "event": "PROVIDER_ATTEMPT_COMPLETED",
            "sequence": 1,
            "attempt_no": 2,
            "provider_response_accepted": False,
            "provider_outcome": "PROVIDER_ERROR",
        },
        {
            "event": "CASE_METHOD_BLOCKED",
            "sequence": 1,
            "provider_outcome": "LOCAL_HARNESS_BLOCK",
        },
    ]

    summary = _validate_attempt_journal_events(events, expected_case_count=1, scoring_required=False)

    assert summary["integrity_valid"] is False
    assert "PROVIDER_ATTEMPT_STARTED_AFTER_ACCEPTED_RESPONSE" in summary["errors"]


def test_clean_eval_preflight_reader_uses_explicit_qualification_directory(tmp_path, monkeypatch) -> None:
    import scripts.run_public_memory_clean_eval as runner

    monkeypatch.setattr(runner, "CLEAN_EVAL_ROOT", tmp_path)

    payload = {
        "status": "PASS",
        "classification": "OFFLINE_QUALIFICATION_NO_GOLD_NO_PROVIDER",
        "provider_calls": {"generation": 0, "count_tokens": 0},
        "gold_values_read": False,
        "prepared_method_case_count": 200,
    }
    preflight_path = tmp_path / "sh_32k_preflight.json"
    preflight_path.write_text(json.dumps(payload), encoding="utf-8")
    runner._seal_file(preflight_path, identity="offline-preflight:sh_32k")

    assert runner._read_preflight("sh_32k", preflight_dir=tmp_path) == payload


def test_fresh_sh_run_authorization_matches_only_the_original_stopped_run() -> None:
    from scripts.run_public_memory_clean_eval import _is_original_stopped_sh_budget_row

    authorized_row = {
        "run_id": "96ab75da2ff945eb9d102a8f760e21a6",
        "cell": "sh_32k",
        "status": "PROVIDER_COMPLETION_BELOW_90_PERCENT",
    }
    assert _is_original_stopped_sh_budget_row(authorized_row)
    assert not _is_original_stopped_sh_budget_row({**authorized_row, "run_id": "another-run"})
    assert not _is_original_stopped_sh_budget_row({**authorized_row, "status": "COMPLETE"})


def test_clean_eval_records_only_allowlisted_count_tokens_fallbacks() -> None:
    from scripts.run_public_memory_clean_eval import _count_tokens_telemetry
    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

    reason = "TRANSPORT_URLError_COUNT_TOKENS_UNAVAILABLE"
    failure_class = "TRANSPORT_URLError"
    state = gemini.CountTokensFallbackState(
        unavailable=True,
        reason=reason,
        failure_class=failure_class,
    )
    record = {
        "status": "static_fallback",
        "count_tokens_status": "UNAVAILABLE",
        "token_estimation_source": "STATIC_CONSERVATIVE",
        "billing_preflight_uncertainty": True,
        "failure_class": failure_class,
        "fallback_reason": reason,
        "estimated_input_token_upper_bound": 512,
        "transport_attempts": 1,
        "failure_diagnostic": {
            "exception_class": "URLError",
            "http_status": gemini.UNAVAILABLE,
            "provider_error_code": gemini.UNAVAILABLE,
        },
    }

    telemetry = _count_tokens_telemetry(record, fallback_state=state, gemini=gemini)

    assert telemetry["count_tokens_outcome"] == "TRANSIENT_FAILURE_FALLBACK"
    assert telemetry["count_tokens_fallback_reason"] == reason
    assert telemetry["static_conservative_input_token_estimate"] == 512
    assert telemetry["billing_preflight_uncertainty"] is True


def test_count_tokens_telemetry_reprojects_untrusted_message_at_artifact_edge() -> None:
    from scripts.run_public_memory_clean_eval import _count_tokens_telemetry
    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

    state = gemini.CountTokensFallbackState(
        unavailable=True,
        reason="TRANSPORT_URLError_COUNT_TOKENS_UNAVAILABLE",
        failure_class="TRANSPORT_URLError",
    )
    private_text = "private synthetic prompt/context marker"
    record = {
        "status": "static_fallback",
        "count_tokens_status": "UNAVAILABLE",
        "token_estimation_source": "STATIC_CONSERVATIVE",
        "billing_preflight_uncertainty": True,
        "failure_class": "TRANSPORT_URLError",
        "fallback_reason": "TRANSPORT_URLError_COUNT_TOKENS_UNAVAILABLE",
        "estimated_input_token_upper_bound": 512,
        "transport_attempts": 1,
        "failure_diagnostic": {
            "exception_class": "URLError",
            "http_status": gemini.UNAVAILABLE,
            "provider_error_code": gemini.UNAVAILABLE,
            "message": private_text,
        },
    }

    telemetry = _count_tokens_telemetry(record, fallback_state=state, gemini=gemini)

    assert telemetry["count_tokens_failure_diagnostic"]["message"] == gemini.COUNT_TOKENS_FAILURE_GENERIC_MESSAGE
    assert private_text not in json.dumps(telemetry)


def test_generation_retry_classifier_accepts_httpx_connect_error() -> None:
    httpx = pytest.importorskip("httpx")
    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

    error = httpx.ConnectError("synthetic connection refusal")

    assert gemini._is_retryable_transport_failure(error) is True


@pytest.mark.parametrize(
    ("failure_kind", "expected"),
    [
        ("raw_connect", "TRANSPORT_CONNECT"),
        ("nested_connect", "TRANSPORT_CONNECT"),
        ("sdk_wrapped_connect", "TRANSPORT_CONNECT"),
        ("proxy_connect", "TRANSPORT_PROXY_CONNECT"),
        ("connect_timeout", "TRANSPORT_TIMEOUT"),
        ("read_timeout", "TRANSPORT_TIMEOUT"),
        ("tls_transient", "TRANSPORT_TLS"),
        ("dns", "TRANSPORT_DNS"),
    ],
)
def test_provider_neutral_retry_policy_classifies_transient_transport(
    failure_kind: str,
    expected: str,
) -> None:
    import httpx
    import socket
    import ssl

    from linkloom.agents.providers.retry_policy import classify_retryable_failure

    request = httpx.Request("GET", "https://example.invalid")
    if failure_kind == "raw_connect":
        error = httpx.ConnectError("synthetic refusal", request=request)
    elif failure_kind == "nested_connect":
        try:
            raise httpx.ConnectError("synthetic refusal", request=request)
        except httpx.ConnectError as inner:
            try:
                raise RuntimeError("provider wrapper") from inner
            except RuntimeError as wrapped:
                error = wrapped
    elif failure_kind == "sdk_wrapped_connect":
        api_connection_error = type("APIConnectionError", (Exception,), {})
        try:
            raise httpx.ConnectError("synthetic refusal", request=request)
        except httpx.ConnectError as inner:
            try:
                raise api_connection_error("SDK connection wrapper") from inner
            except api_connection_error as wrapped:
                error = wrapped
    elif failure_kind == "proxy_connect":
        error = httpx.ProxyError("synthetic proxy refusal", request=request)
    elif failure_kind == "connect_timeout":
        error = httpx.ConnectTimeout("synthetic connect timeout", request=request)
    elif failure_kind == "read_timeout":
        error = httpx.ReadTimeout("synthetic read timeout", request=request)
    elif failure_kind == "tls_transient":
        error = ssl.SSLError("synthetic temporary TLS handshake failure")
    else:
        error = socket.gaierror(socket.EAI_AGAIN, "synthetic temporary name lookup")

    assert classify_retryable_failure(error) == expected


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ({"http_status": 408}, "HTTP_408"),
        ({"http_status": 429}, "HTTP_429"),
        ({"http_status": 500}, "HTTP_500"),
        ({"http_status": 502}, "HTTP_502"),
        ({"http_status": 503}, "HTTP_503"),
        ({"http_status": 504}, "HTTP_504"),
        ({"provider_error_code": "RESOURCE_EXHAUSTED"}, "PROVIDER_OVERLOADED"),
        ({"provider_error_code": "UNAVAILABLE"}, "PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_neutral_retry_policy_accepts_explicit_provider_transients(
    diagnostic: dict[str, object],
    expected: str,
) -> None:
    from linkloom.agents.providers.retry_policy import classify_retryable_failure

    assert classify_retryable_failure(None, diagnostic) == expected


@pytest.mark.parametrize(
    "diagnostic",
    [
        {"http_status": 400},
        {"http_status": 401},
        {"http_status": 403},
        {"http_status": 501},
        {"provider_error_code": "INVALID_ARGUMENT"},
        {"provider_error_code": "UNAUTHENTICATED"},
        {"provider_error_code": "SAFETY"},
        {"failure_layer": "SCORING"},
    ],
)
def test_provider_neutral_retry_policy_rejects_deterministic_failures(
    diagnostic: dict[str, object],
) -> None:
    from linkloom.agents.providers.retry_policy import classify_retryable_failure

    assert classify_retryable_failure(None, diagnostic) is None


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("harness exception"),
        ValueError("scoring or parse exception"),
        TypeError("configuration exception"),
        Exception("unknown failure"),
    ],
)
def test_provider_neutral_retry_policy_does_not_retry_unknown_exceptions(
    error: Exception,
) -> None:
    from linkloom.agents.providers.retry_policy import classify_retryable_failure

    assert classify_retryable_failure(error) is None


def test_provider_neutral_retry_policy_bounds_and_cycles_nested_causes() -> None:
    import httpx

    from linkloom.agents.providers.retry_policy import classify_retryable_failure

    deep: BaseException = httpx.ConnectError(
        "deep synthetic refusal",
        request=httpx.Request("GET", "https://example.invalid"),
    )
    for _ in range(12):
        try:
            raise RuntimeError("nested wrapper") from deep
        except RuntimeError as wrapper:
            deep = wrapper
    assert classify_retryable_failure(deep) is None

    cyclic = RuntimeError("cycle")
    cyclic.__cause__ = cyclic
    assert classify_retryable_failure(cyclic) is None


def test_count_tokens_fallback_and_generation_share_retry_taxonomy() -> None:
    from tests.smoke import test_m12_real_provider_team_decision_smoke as gemini

    assert gemini._matches_safe_count_tokens_transport_failure(
        {"exception_class": "ConnectError", "http_status": gemini.UNAVAILABLE}
    )
    assert not gemini._matches_safe_count_tokens_transport_failure(
        {"exception_class": "ConnectError", "http_status": 401}
    )


def _run_synthetic_clean_live(
    tmp_path,
    monkeypatch,
    outcomes: dict[int, list[str]],
    *,
    global_cost_gate=None,
    diagnostic_stress: bool = False,
    diagnostic_mode: bool = False,
    dynamic_spend_upper_bound_usd: float = 0.0,
    block_hook=None,
    cell_key: str = "sh_32k",
    evaluation_plan_id: str | None = None,
    score_with_synthetic_gold: bool = False,
    rebuild_error: str | None = None,
):
    import copy
    import hashlib
    import json
    from pathlib import Path
    from types import SimpleNamespace

    import httpx

    import scripts.run_public_memory_clean_eval as runner
    from benchmarks.memoryagentbench.g32_heldout import count_tokens_request_upper_bound

    fingerprints = runner.implementation_fingerprints()
    e2_run = evaluation_plan_id == runner.E2_MH_ONLY_EVALUATION_PLAN_ID
    question_count = runner.E2_SUBSET_QA_COUNT if e2_run else 100
    prepared_rows = []
    questions = []
    plan_rows = []
    generation_hash_to_sequence = {}
    question_by_qa_id = {}
    for sequence in range(1, question_count * len(runner.METHODS) + 1):
        qa_index = (sequence - 1) // len(runner.METHODS)
        method = runner.METHODS[(sequence - 1) % len(runner.METHODS)]
        qa_id = f"synthetic_qa_{qa_index}"
        question = f"Synthetic question {qa_index}"
        context = f"synthetic context; sequence={sequence}; qa={qa_id}; method={method}"
        if qa_id not in question_by_qa_id:
            question_by_qa_id[qa_id] = SimpleNamespace(
                question_id=qa_id,
                question=question,
            )
        prepared_rows.append(
            SimpleNamespace(
                question_id=qa_id,
                method=method,
                workspace_id="synthetic-workspace",
                question=question,
                context_bundle=SimpleNamespace(rendered_text=context),
            )
        )
        request = runner.build_generation_request(context)
        generation_hash, count_hash, _ = count_tokens_request_upper_bound(request)
        generation_hash_to_sequence[generation_hash] = sequence
        plan_rows.append(
            {
                "sequence": sequence,
                "qa_id": qa_id,
                "method": method,
                "generation_request_sha256": generation_hash,
                "count_tokens_request_sha256": count_hash,
                "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
                "context_tokens_estimated": 8,
                "context_evidence_ids_selected": [f"evidence:{sequence}"],
                "retrieval_evidence_ids": [f"evidence:{sequence}"],
                "memory_lookup_status": "HIT",
                "memory_source_evidence_ids": [f"evidence:{sequence}"],
                "static_input_token_upper_bound": 100,
                "one_attempt_cost_upper_bound_usd": runner.request_cost_upper_bound_usd(100),
            }
        )
    questions = list(question_by_qa_id.values())
    case = SimpleNamespace(
        workspace_id="synthetic-workspace",
        questions=questions,
        facts=[],
    )
    preflight = {
        "execution_plan": plan_rows,
        "question_count": question_count,
        "dataset_revision": "synthetic-dataset-revision",
        "dataset_sha256": "d" * 64,
        "subset_sha256": "e" * 64,
        "freeze_manifest_sha256": "a" * 64,
        "frozen_execution_plan_sha256": "b" * 64,
        "runtime_execution_plan_sha256": "c" * 64,
    }
    if e2_run:
        preflight["subset_sha256"] = "e" * 64
    if diagnostic_stress:
        preflight.update({"purpose": "HARNESS_STABILITY", "clean_accuracy_claim": False})
    preflight_dir = tmp_path / "preflight"
    preflight_dir.mkdir()
    (preflight_dir / f"{cell_key}_preflight.json").write_text("{}\n", encoding="utf-8")
    clean_eval_root = tmp_path / "clean_eval"
    live_artifact_root = tmp_path / "live_artifacts"
    diagnostic_dir = tmp_path / "diagnostic"
    diagnostic_dir.mkdir()
    history = [
        {
            "run_id": runner.ORIGINAL_STOPPED_SH_RUN_ID,
            "cell": "sh_32k",
            "status": "PROVIDER_COMPLETION_BELOW_90_PERCENT",
            "hard_cost_cap_usd": runner.CELLS["sh_32k"].hard_cost_cap_usd,
            "reserved_usd": 0.0,
            "estimated_spend_upper_bound_usd": 0.004374,
        },
        {
            "run_id": runner.STOPPED_SH_V2_RUN_ID,
            "cell": "sh_32k",
            "status": "PROVIDER_COMPLETION_BELOW_90_PERCENT",
            "hard_cost_cap_usd": runner.CELLS["sh_32k"].hard_cost_cap_usd,
            "reserved_usd": 0.0,
            "estimated_spend_upper_bound_usd": 0.080115,
        },
    ]
    ledger = {
        "schema_version": "linkloom-public-memory-global-budget/v1",
        "hard_cap_usd": runner.GLOBAL_COST_CAP_USD,
        "runs": copy.deepcopy(history),
        "active_run_id": None,
    }
    ledger["ledger_sha256"] = runner.canonical_sha256(ledger)
    provider_boundary_calls = []
    rebuild_calls = []
    scorer_calls = []

    class FakeGuard:
        def __init__(self, sequence, attempt_no, aggregate, on_response_accepted=None):
            self.sequence = sequence
            self.attempt_no = attempt_no
            self.aggregate = aggregate
            self.on_response_accepted = on_response_accepted
            self.last_guard_state = "REQUEST_PENDING"
            self.preflight_records = [
                {
                    "status": "success",
                    "count_tokens_status": "PASS",
                    "token_estimation_source": "PROVIDER_COUNT_TOKENS",
                    "billing_preflight_uncertainty": False,
                    "counted_input_tokens": 100,
                    "transport_attempts": 0,
                    "failure_diagnostic": None,
                }
            ]

        def generate_content(self, **request):
            if runner._contains_gold_payload_field(request):
                raise AssertionError("Gold field reached the fake Provider boundary")
            signature = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
            provider_boundary_calls.append(
                {
                    "sequence": self.sequence,
                    "attempt_no": self.attempt_no,
                    "signature": signature,
                }
            )
            self.aggregate.provider_requests += 1
            attempt_outcomes = outcomes.get(self.sequence, ["success"])
            outcome = attempt_outcomes[self.attempt_no - 1] if self.attempt_no <= len(attempt_outcomes) else "success"
            if outcome == "connect":
                raise httpx.ConnectError(
                    "synthetic offline ConnectError",
                    request=httpx.Request("POST", "https://example.invalid"),
                )
            if outcome == "unknown":
                self.last_guard_state = "GENERATION_CALL_FAILED"
                raise RuntimeError("synthetic non-transient harness boundary error")
            self.last_guard_state = "RESPONSE_ACCEPTED"
            if outcome == "malformed_response":
                response = SimpleNamespace(
                    text=object(),
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=100,
                        candidates_token_count=12,
                        thoughts_token_count=0,
                    ),
                    model_dump_json=lambda: '{"candidates":[{"content":{"parts":[{}]}}]}',
                )
            elif outcome in {"missing_output", "thinking_only", "missing_usage"}:
                usage = (
                    None
                    if outcome == "missing_usage"
                    else SimpleNamespace(
                        prompt_token_count=100,
                        thoughts_token_count=157,
                    )
                )
                response = SimpleNamespace(
                    text="synthetic offline answer",
                    usage_metadata=usage,
                )
            else:
                response = SimpleNamespace(
                    text="synthetic offline answer",
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=100,
                        candidates_token_count=12,
                        thoughts_token_count=0,
                    ),
                )
            if self.on_response_accepted is not None:
                self.on_response_accepted(response)
            if outcome == "post_response_runtime":
                self.last_guard_state = "LOCAL_GUARD_FAILED"
                raise RuntimeError("synthetic private prompt after accepted response")
            if outcome == "smoke_post_response":
                self.last_guard_state = "PROVIDER_USAGE_UNAVAILABLE"
                from tests.smoke.test_m12_real_provider_team_decision_smoke import SmokeBlocked
                raise SmokeBlocked("PROVIDER_USAGE_UNAVAILABLE")
            return response

    class FakeClient:
        def close(self):
            return None

    def rebuild(cell_key, *, preflight_dir, **_options):
        rebuild_calls.append(cell_key)
        if rebuild_error is not None:
            raise runner.CleanEvalBlocked(rebuild_error)
        return (
            case,
            {"implementation_fingerprints": fingerprints},
            prepared_rows,
            SimpleNamespace(store=SimpleNamespace(close=lambda: None)),
        )

    def persist_outputs(*, root, cell_key, plan_rows, output_by_sequence):
        rows = []
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
            rows.append(row)
        payload = {
            "schema_version": "linkloom-public-memory-provider-outputs/v1",
            "cell": runner.CELLS[cell_key].display_name,
            "source": runner.CELLS[cell_key].source,
            "gold_values_read": False,
            "provider_outputs": rows,
        }
        path = root / "provider_outputs.json"
        runner._json_write(path, payload)
        runner._seal_file(path, identity=f"provider-outputs:{runner.CELLS[cell_key].source}")
        return rows, path

    monkeypatch.setattr(runner, "CLEAN_EVAL_ROOT", clean_eval_root)
    monkeypatch.setattr(runner, "ARTIFACT_ROOT", live_artifact_root)
    monkeypatch.setattr(runner, "DIAGNOSTIC_DIR", diagnostic_dir)
    monkeypatch.setattr(runner, "verify_local_provider_route", lambda: {"status": "PASS", "provider_requests": 0})
    monkeypatch.setattr(runner, "_resolve_eval_artifact_dir", lambda _: preflight_dir)
    monkeypatch.setattr(runner, "_verify_ready", lambda *args, **kwargs: (preflight, fingerprints))
    monkeypatch.setattr(runner, "_verify_diagnostic_ready", lambda **kwargs: (preflight, fingerprints))
    monkeypatch.setattr(runner, "_verify_diagnostic_qualification_gates", lambda: ({"status": "PASS"}, {"status": "PASS"}))
    monkeypatch.setattr(runner, "_verify_v3_live_authorization", lambda *args: None)
    if evaluation_plan_id == runner.MH_ONLY_EVALUATION_PLAN_ID:
        monkeypatch.setattr(
            runner,
            "_verify_authorized_mh_only_baseline",
            lambda *args: {"formal_budget_ledger_sha256": ledger.get("ledger_sha256")},
        )
    if e2_run:
        synthetic_plan = {
            "plan_id": runner.E2_MH_ONLY_EVALUATION_PLAN_ID,
            "subset_sha256": "e" * 64,
            "revision_classification": "REVISED_HELDOUT_AFTER_STOPPED_E1_1",
        }
        monkeypatch.setattr(runner, "_load_evaluation_plan", lambda _plan_id: synthetic_plan)
        monkeypatch.setattr(
            runner,
            "_verify_authorized_e2_baseline",
            lambda *_args: {
                "budget": {"formal_budget_ledger_sha256": ledger.get("ledger_sha256")}
            },
        )
    monkeypatch.setattr(runner, "_rebuild_runtime_objects", rebuild)
    monkeypatch.setattr(runner, "_persist_provider_outputs", persist_outputs)
    monkeypatch.setattr(runner, "_load_budget_ledger", lambda **kwargs: ledger)
    monkeypatch.setattr(runner, "_validate_original_stopped_sh_run", lambda row: None)
    monkeypatch.setattr(runner, "_validate_stopped_v2_sh_run", lambda row: None)
    monkeypatch.setattr(runner, "_write_budget_ledger", lambda value, **kwargs: None)
    monkeypatch.setattr(runner, "implementation_fingerprints", lambda: fingerprints)
    monkeypatch.setattr(
        runner,
        "_spent_upper_bound_usd",
        lambda aggregate: dynamic_spend_upper_bound_usd,
    )
    monkeypatch.setattr(runner, "_prepare_generation_call", lambda **kwargs: (
        FakeGuard(
            generation_hash_to_sequence[count_tokens_request_upper_bound(kwargs["request"])[0]],
            kwargs["attempt_no"],
            kwargs["aggregate"],
            kwargs.get("on_response_accepted"),
        ),
        FakeClient(),
    ))
    if block_hook is not None:
        original_persist_block = runner._persist_clean_eval_block

        def persist_block_with_hook(root, **kwargs):
            block_hook(root, kwargs)
            return original_persist_block(root, **kwargs)

        monkeypatch.setattr(runner, "_persist_clean_eval_block", persist_block_with_hook)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    monkeypatch.setattr(runner.random, "uniform", lambda *_: 0.0)
    monkeypatch.setattr(runner, "uuid4", lambda: SimpleNamespace(hex="synthetic-new-v3-run"))
    gold_calls = []
    if score_with_synthetic_gold:
        def synthetic_gold(source, question_ids, *, provider_outputs_path):
            gold_calls.append((source, tuple(question_ids), provider_outputs_path))
            return {qa_id: ["synthetic offline answer"] for qa_id in question_ids}

        monkeypatch.setattr(runner, "_load_gold_answers_after_seal", synthetic_gold)
    else:
        monkeypatch.setattr(runner, "_load_gold_answers_after_seal", lambda **_: pytest.fail("Gold read in partial run"))
        monkeypatch.setattr(runner, "_score_sealed_run", lambda **kwargs: scorer_calls.append(kwargs))
    if global_cost_gate is not None:
        monkeypatch.setattr(runner, "_global_committed_excluding", global_cost_gate)
    monkeypatch.setenv("GEMINI_API_KEY", "offline-synthetic-placeholder")

    result = runner._run_live(
        cell_key,
        preflight_dir=diagnostic_dir if diagnostic_stress else preflight_dir,
        allow_authorized_fresh_sh_v3=True,
        diagnostic_stress=diagnostic_stress,
        diagnostic_mode=diagnostic_mode,
        evaluation_plan_id=evaluation_plan_id,
    )
    return {
        "result": result,
        "calls": provider_boundary_calls,
        "rebuild_calls": rebuild_calls,
        "scorer_calls": scorer_calls,
        "gold_calls": gold_calls,
        "ledger": ledger,
        "root": Path(result["artifact_root"]),
        "plan_rows": plan_rows,
    }


def test_live_runner_retries_same_method_case_without_replaying_prior_case(tmp_path, monkeypatch) -> None:
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {2: ["connect", "success"], 3: ["unknown"]},
    )
    summary = replay["result"]["summary"]
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    seq1_calls = [call for call in replay["calls"] if call["sequence"] == 1]
    seq2_calls = [call for call in replay["calls"] if call["sequence"] == 2]
    seq2_events = [event for event in events if event.get("sequence") == 2]
    seq2_attempts = [event for event in seq2_events if event["event"] == "PROVIDER_ATTEMPT_STARTED"]
    seq2_completed = [event for event in seq2_events if event["event"] == "PROVIDER_ATTEMPT_COMPLETED"]
    seq2_case_starts = [event for event in seq2_events if event["event"] == "CASE_METHOD_STARTED"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))
    output_by_sequence = {row["sequence"]: row for row in outputs["provider_outputs"]}

    assert summary["status"] == "STOPPED"
    assert summary["gold_values_read"] is False
    assert outputs["gold_values_read"] is False
    assert replay["rebuild_calls"] == ["sh_32k"]
    assert replay["scorer_calls"] == []
    assert len(seq1_calls) == 1
    assert [call["attempt_no"] for call in seq2_calls] == [1, 2]
    assert seq2_calls[0]["signature"] == seq2_calls[1]["signature"]
    assert [event["attempt_no"] for event in seq2_attempts] == [1, 2]
    assert seq2_completed[0]["safe_error"]["retry_classification"] == "TRANSPORT_CONNECT"
    assert seq2_completed[1]["provider_outcome"] == "PROVIDER_COMPLETE"
    assert len({event["generation_request_sha256"] for event in seq2_attempts}) == 1
    assert len(seq2_case_starts) == 1
    assert seq2_case_starts[0]["context_sha256"] == replay["plan_rows"][1]["context_sha256"]
    assert output_by_sequence[1]["provider_attempts"] == 1
    assert output_by_sequence[2]["provider_attempts"] == 2
    assert output_by_sequence[2]["provider_outcome"] == "PROVIDER_COMPLETE"
    assert replay["result"]["run_id"] != "328738863a2c42b191c50224baa03ce8"
    assert output_by_sequence[2]["retrieval_evidence_ids"] == replay["plan_rows"][1]["retrieval_evidence_ids"]
    assert output_by_sequence[2]["memory_source_evidence_ids"] == replay["plan_rows"][1]["memory_source_evidence_ids"]
    assert runner_seal_valid(replay["root"] / "provider_outputs.json", "provider-outputs:factconsolidation_sh_32k")


def test_live_runner_exhausts_exactly_three_transient_attempts_then_continues(tmp_path, monkeypatch) -> None:
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["connect", "connect", "connect"], 2: ["success"], 3: ["unknown"]},
    )
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))
    output_by_sequence = {row["sequence"]: row for row in outputs["provider_outputs"]}
    seq1_attempts = [
        call for call in replay["calls"] if call["sequence"] == 1
    ]
    started = [
        event for event in events
        if event.get("sequence") == 1 and event["event"] == "PROVIDER_ATTEMPT_STARTED"
    ]

    assert len(seq1_attempts) == 3
    assert [call["attempt_no"] for call in seq1_attempts] == [1, 2, 3]
    assert [event["attempt_no"] for event in started] == [1, 2, 3]
    assert output_by_sequence[1]["provider_attempts"] == 3
    assert output_by_sequence[1]["provider_outcome"] == "PROVIDER_ERROR"
    assert output_by_sequence[2]["provider_outcome"] == "PROVIDER_COMPLETE"
    assert any(call["sequence"] == 2 for call in replay["calls"])


def test_live_runner_blocks_retry_when_global_cost_guard_fails(tmp_path, monkeypatch) -> None:
    global_checks = 0

    def cost_gate(_ledger, _run_id):
        nonlocal global_checks
        global_checks += 1
        return 0.0 if global_checks == 1 else 30.0

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["connect", "success"]},
        global_cost_gate=cost_gate,
    )
    summary = replay["result"]["summary"]
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))

    assert summary["stop_reason"] == "RETRY_BLOCKED_BY_GLOBAL_COST_GUARD"
    assert len([call for call in replay["calls"] if call["sequence"] == 1]) == 1
    assert [event["attempt_no"] for event in events if event.get("sequence") == 1 and event["event"] == "PROVIDER_ATTEMPT_STARTED"] == [1]
    assert outputs["provider_outputs"][0]["provider_outcome"] == "PROVIDER_ERROR"
    assert outputs["provider_outputs"][0]["provider_attempts"] == 1
    assert summary["journal_protocol"]["integrity_valid"] is True
    assert summary["artifact_durability_status"] == "PASS"


def runner_seal_valid(path, identity):
    import scripts.run_public_memory_clean_eval as runner

    return runner._verify_seal(path, identity=identity)


def test_gold_request_check_uses_structured_fields_not_text_substrings() -> None:
    from scripts.run_public_memory_clean_eval import _contains_gold_payload_field

    assert not _contains_gold_payload_field({"contents": "The word answers appears in the question."})
    assert _contains_gold_payload_field({"metadata": {"answers": ["hidden"]}})


def test_proxy_parser_preserves_scheme_for_route_validation() -> None:
    from scripts.run_public_memory_clean_eval import _safe_proxy_endpoint

    assert _safe_proxy_endpoint("http://127.0.0.1:7897") == (
        "127.0.0.1",
        7897,
        False,
        "http",
    )
    assert _safe_proxy_endpoint("socks5://127.0.0.1:7897")[3] == "socks5"


def test_accepted_response_followed_by_clean_eval_block_is_local_and_diagnostic(
    tmp_path, monkeypatch
) -> None:
    import hashlib
    import json

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["malformed_response"]},
    )
    summary = replay["result"]["summary"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))
    output = outputs["provider_outputs"][0]
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    attempt = next(
        event
        for event in events
        if event.get("sequence") == 1 and event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    )

    assert output["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert output["failure_classification"] == "LOCAL/HARNESS BLOCK"
    assert output["safe_error"]["reason_code"] == "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"
    assert output["safe_error"]["phase"] == "RESPONSE_VALIDATION"
    assert output["safe_error"]["provider_response_already_accepted"] is True
    assert output["safe_error"]["logical_case_id"] == output["qa_id"]
    assert output["safe_error"]["method"] == output["method"]
    assert output["safe_error"]["throw_site_function"] == "_response_text"
    assert output["safe_error"]["throw_site_module"] == "scripts.run_public_memory_clean_eval"
    assert output["safe_error"]["guard_name"] == "_response_text"
    assert output["safe_error"]["bounded_stack_summary"]
    assert "traceback" not in output["safe_error"]
    safe_error_json = json.dumps(output["safe_error"])
    assert "offline-synthetic-placeholder" not in safe_error_json
    assert "synthetic context" not in safe_error_json
    assert "synthetic offline answer" not in safe_error_json
    assert attempt["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert attempt["failure_classification"] == "LOCAL/HARNESS BLOCK"
    assert attempt["safe_error"]["reason_code"] == output["safe_error"]["reason_code"]
    assert summary["provider_completion"]["provider_error_method_cases"] == 0
    assert summary["provider_completion"]["local_harness_block_method_cases"] == 1
    assert summary["provider_completion"]["failed_method_cases"] == 1
    assert replay["scorer_calls"] == []
    block = json.loads(next((replay["root"] / "clean_eval_blocks").glob("*.json")).read_text(encoding="utf-8"))
    assert block["response_sha256"] == hashlib.sha256(
        b'{"candidates":[{"content":{"parts":[{}]}}]}'
    ).hexdigest()


@pytest.mark.parametrize(
    ("outcome", "reason_code"),
    [
        ("smoke_post_response", "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE"),
        ("post_response_runtime", "CLEAN_EVAL_BLOCK_LOCAL_RUNTIME_ERROR"),
    ],
)
def test_accepted_response_smoke_and_generic_failures_are_local(
    tmp_path, monkeypatch, outcome: str, reason_code: str
) -> None:
    import json

    replay = _run_synthetic_clean_live(tmp_path, monkeypatch, {1: [outcome]})
    summary = replay["result"]["summary"]
    output = json.loads((replay["root"] / "cases" / "001.json").read_text(encoding="utf-8"))
    evidence_rows = [
        json.loads(line)
        for line in (replay["root"] / "accepted_response_evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert replay["result"]["status"] == "STOPPED"
    assert output["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert output["failure_classification"] == "LOCAL/HARNESS BLOCK"
    assert output["provider_response_accepted"] is True
    assert output["safe_error"]["reason_code"] == reason_code
    assert summary["provider_completion"]["provider_response_completed_method_cases"] == 1
    assert summary["provider_completion"]["provider_error_method_cases"] == 0
    assert summary["provider_completion"]["local_harness_block_method_cases"] == 1
    assert summary["provider_completion"]["not_run_method_cases"] == 199
    assert evidence_rows[0]["response_sha256"]
    assert replay["scorer_calls"] == []


def test_clean_eval_reason_codes_include_stable_category() -> None:
    from scripts.run_public_memory_clean_eval import _stable_clean_eval_reason_code

    assert _stable_clean_eval_reason_code("GENERATION_RESPONSE_TEXT_MALFORMED") == (
        "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"
    )
    assert _stable_clean_eval_reason_code("JOURNAL_PERSISTENCE_FAILURE") == (
        "CLEAN_EVAL_BLOCK_ARTIFACT_JOURNAL_PERSISTENCE_FAILURE"
    )
    assert _stable_clean_eval_reason_code("IMPLEMENTATION_FINGERPRINT_MISMATCH") == (
        "CLEAN_EVAL_BLOCK_FINGERPRINT_IMPLEMENTATION_FINGERPRINT_MISMATCH"
    )


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("INVALID_STATE_TRANSITION", "PROTOCOL"),
        ("DUPLICATE_METHOD_CASE", "PROTOCOL"),
        ("EXECUTION_ORDER_MISMATCH", "PROTOCOL"),
        ("COMPLETION_INVARIANT", "PROTOCOL"),
        ("RESPONSE_USAGE_INVALID", "RESPONSE"),
        ("OUTPUT_PERSIST_FAILED", "ARTIFACT"),
        ("HARD_CAP_REACHED", "COST"),
        ("IMPLEMENTATION_MISMATCH", "FINGERPRINT"),
        ("GOLD_ACCESSED_TOO_EARLY", "GOLD"),
        ("SCORE_PRECONDITION_FAILED", "SCORING"),
    ],
)
def test_stable_reason_family_covers_required_categories(reason: str, category: str) -> None:
    from scripts.run_public_memory_clean_eval import _stable_clean_eval_reason_code

    assert _stable_clean_eval_reason_code(reason).startswith(f"CLEAN_EVAL_BLOCK_{category}_")


def test_every_clean_eval_block_throw_site_has_a_reason_expression() -> None:
    import ast
    from pathlib import Path

    import scripts.run_public_memory_clean_eval as runner

    source = Path(runner.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CleanEvalBlocked"
    ]
    assert calls
    assert all(node.args for node in calls)
    assert all(
        not (isinstance(node.args[0], ast.Constant) and not str(node.args[0].value).strip())
        for node in calls
    )
    assert all(runner._stable_clean_eval_reason_code(ast.unparse(node.args[0])).startswith("CLEAN_EVAL_BLOCK_") for node in calls)


def test_guard_inventory_covers_each_production_clean_eval_throw_site() -> None:
    from scripts.audit_public_memory_clean_eval_guards import build_guard_inventory

    inventory = build_guard_inventory()

    assert inventory["status"] == "PASS"
    assert inventory["production_reachable_throw_sites"] >= 90
    assert inventory["uninstrumented_throw_sites"] == 0
    assert inventory["provider_calls"] == 0
    assert inventory["gold_values_read"] is False
    assert all(
        site["reason_code_pattern"].startswith("CLEAN_EVAL_BLOCK_")
        and site["guard_condition"]
        and site["phase"]
        and site["failure_category"]
        for site in inventory["sites"]
    )


def test_accepted_response_evidence_is_durable_and_contains_no_response_text(
    tmp_path,
) -> None:
    import hashlib
    import json
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import _persist_accepted_response_evidence

    response = SimpleNamespace(
        text="private provider answer",
        usage_metadata=SimpleNamespace(
            prompt_token_count=123,
            candidates_token_count=45,
            thoughts_token_count=6,
        ),
        response_id="response-123",
    )
    path = tmp_path / "accepted_response_evidence.jsonl"
    evidence = _persist_accepted_response_evidence(
        path,
        run_id="run-1",
        sequence=7,
        logical_case_id="qa-7",
        method="LinkLoom Temporal Memory",
        attempt_no=2,
        logical_attempt_id="7:2",
        model="gemini-test-model",
        request_hash="a" * 64,
        response=response,
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [evidence]
    assert evidence["provider_response_accepted"] is True
    assert evidence["attempt_no"] == 2
    assert evidence["logical_attempt_id"] == "7:2"
    assert evidence["model"] == "gemini-test-model"
    assert evidence["response_sha256"] == hashlib.sha256(b"private provider answer").hexdigest()
    assert evidence["usage_present"] is True
    assert evidence["usage_metadata"] == {
        "input_tokens": 123,
        "output_tokens": 45,
        "thinking_tokens": 6,
    }
    assert evidence["request_sha256"] == "a" * 64
    assert evidence["provider_request_id"] == "response-123"
    assert "private provider answer" not in path.read_text(encoding="utf-8")


def test_post_response_generic_exception_is_a_local_harness_block() -> None:
    import json
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import _safe_provider_error

    accepted = {
        "provider_response_accepted": True,
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "usage_metadata": {"input_tokens": 10, "output_tokens": 2},
        "logical_attempt_id": "1:1",
    }
    guard = SimpleNamespace(last_guard_state="LOCAL_COST_GUARD_BLOCKED")
    error = RuntimeError("private prompt fragment: synthetic-context-secret")

    safe = _safe_provider_error(
        error,
        guard,
        logical_case_id="qa-1",
        method="Flat Retrieval",
        response_evidence=accepted,
        provider_response_accepted=True,
    )

    assert safe["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert safe["failure_classification"] == "LOCAL/HARNESS BLOCK"
    assert safe["outcome_domain"] == "LOCAL_HARNESS"
    assert safe["provider_response_accepted"] is True
    assert safe["reason_code"] == "CLEAN_EVAL_BLOCK_LOCAL_RUNTIME_ERROR"
    assert "synthetic-context-secret" not in json.dumps(safe)


def test_pre_response_connect_error_is_a_provider_error() -> None:
    import httpx
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import _safe_provider_error

    error = httpx.ConnectError(
        "synthetic connect failure",
        request=httpx.Request("POST", "https://example.invalid"),
    )
    safe = _safe_provider_error(
        error,
        SimpleNamespace(last_guard_state="FIRST_ATTEMPT_FAILED"),
        provider_response_accepted=False,
    )

    assert safe["provider_outcome"] == "PROVIDER_ERROR"
    assert safe["outcome_domain"] == "PROVIDER"
    assert safe["provider_response_accepted"] is False


def test_smoke_block_has_a_stable_local_reason_code() -> None:
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import _safe_provider_error
    from tests.smoke.test_m12_real_provider_team_decision_smoke import SmokeBlocked

    safe = _safe_provider_error(
        SmokeBlocked("PROVIDER_USAGE_UNAVAILABLE"),
        SimpleNamespace(last_guard_state="PROVIDER_USAGE_UNAVAILABLE"),
        provider_response_accepted=True,
    )

    assert safe["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert safe["reason_code"] == "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE"
    assert safe["outcome_domain"] == "LOCAL_HARNESS"


def test_clean_eval_integrity_error_is_structured_without_its_message() -> None:
    import json
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import _safe_provider_error

    error = CleanEvalIntegrityError("private path and prompt-like detail")
    safe = _safe_provider_error(error, SimpleNamespace(last_guard_state="PREFLIGHT"))

    assert safe["outcome_domain"] == "INTEGRITY"
    assert safe["reason_code"] == "CLEAN_EVAL_INTEGRITY_VALIDATION_FAILED"
    assert safe["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert "private path" not in json.dumps(safe)


def test_accepted_response_fact_cannot_be_erased_by_attempt_completion() -> None:
    from scripts.run_public_memory_clean_eval import _validate_attempt_journal_events

    events = [
        {"event": "CASE_METHOD_STARTED", "sequence": 1},
        {
            "event": "PROVIDER_ATTEMPT_STARTED",
            "sequence": 1,
            "attempt_no": 1,
            "attempt_id": "1:1",
            "generation_request_sha256": "a" * 64,
        },
        {
            "event": "PROVIDER_RESPONSE_ACCEPTED",
            "sequence": 1,
            "attempt_no": 1,
            "attempt_id": "1:1",
            "provider_response_accepted": True,
            "request_sha256": "a" * 64,
            "response_sha256": "b" * 64,
            "usage_present": True,
            "usage_metadata": {"input_tokens": 10, "output_tokens": 2},
            "model": "gemini-test-model",
        },
        {
            "event": "PROVIDER_ATTEMPT_COMPLETED",
            "sequence": 1,
            "attempt_no": 1,
            "provider_outcome": "PROVIDER_ERROR",
            "provider_response_accepted": False,
        },
        {"event": "CASE_METHOD_BLOCKED", "sequence": 1},
    ]

    summary = _validate_attempt_journal_events(events, expected_case_count=1, scoring_required=False)

    assert summary["integrity_valid"] is False
    assert "PROVIDER_RESPONSE_ACCEPTANCE_REGRESSION" in summary["errors"]


def test_response_accepted_fact_survives_overwritten_guard_state() -> None:
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import _safe_provider_error

    guard = SimpleNamespace(last_guard_state="REPORTED_COST_BUDGET_EXCEEDED")
    safe = _safe_provider_error(
        RuntimeError("guard failed after response"),
        guard,
        response_evidence={
            "provider_response_accepted": True,
            "request_sha256": "a" * 64,
            "response_sha256": "b" * 64,
            "usage_metadata": {"input_tokens": 10, "output_tokens": 2},
        },
    )

    assert safe["provider_response_accepted"] is True
    assert safe["provider_outcome"] == "LOCAL_HARNESS_BLOCK"


def test_diagnostic_gold_loader_is_a_hard_stop(tmp_path, monkeypatch) -> None:
    from scripts.run_public_memory_clean_eval import CleanEvalBlocked, _load_gold_answers_after_seal

    monkeypatch.setattr(
        "scripts.run_public_memory_clean_eval._verify_seal",
        lambda *args, **kwargs: pytest.fail("gold verification reached in diagnostic mode"),
    )
    with pytest.raises(CleanEvalBlocked) as error:
        _load_gold_answers_after_seal(
            "factconsolidation_sh_32k",
            ["qa-1"],
            provider_outputs_path=tmp_path / "provider_outputs.json",
            diagnostic_mode=True,
        )

    assert error.value.reason_code == "CLEAN_EVAL_BLOCK_GOLD_DIAGNOSTIC_GOLD_ACCESS_VIOLATION"


def test_safe_structural_traceback_excludes_message_secrets(monkeypatch) -> None:
    import json

    from scripts.run_public_memory_clean_eval import _safe_exception_traceback

    monkeypatch.setenv("GEMINI_API_KEY", "AIza-secret-api-key")
    error = RuntimeError(
        "http://proxy-user:proxy-pass@proxy.example.test "
        "Bearer bearer-secret prompt fragment synthetic-private-context"
    )
    try:
        raise error
    except RuntimeError as caught:
        traceback_record = _safe_exception_traceback(caught)

    serialized = json.dumps(traceback_record)
    assert isinstance(traceback_record, dict)
    assert "RuntimeError" in serialized
    for secret in (
        "proxy-user",
        "proxy-pass",
        "bearer-secret",
        "AIza-secret-api-key",
        "prompt fragment",
        "synthetic-private-context",
    ):
        assert secret not in serialized


def test_diagnostic_summary_omits_accuracy_and_score_fields(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    summary = _finalize_diagnostic_run(
        _write_diagnostic_finalizer_fixture(tmp_path / "diagnostic", case_count=200)
    )
    serialized = json.dumps(summary).casefold()

    assert summary["scoring"] == "DISABLED"
    assert "accuracy_aggregation" not in summary
    assert summary["semantic_benchmark_verdict"] == "DISABLED"
    for forbidden in ("accuracy", "correct", "incorrect", "benchmark_score"):
        assert forbidden not in serialized


def test_diagnostic_case_local_allowlist_requires_complete_accepted_evidence() -> None:
    from scripts.run_public_memory_clean_eval import (
        DIAGNOSTIC_CASE_LOCAL_REASON_CODES,
        _diagnostic_guard_is_fatal,
    )

    evidence = {
        "provider_response_accepted": True,
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "usage_present": True,
        "usage_metadata": {"input_tokens": 10, "output_tokens": 2},
        "logical_attempt_id": "1:1",
        "model": "gemini-test-model",
    }
    allowed_reason = "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"

    assert DIAGNOSTIC_CASE_LOCAL_REASON_CODES == frozenset(
        {
            allowed_reason,
            "CLEAN_EVAL_BLOCK_HARNESS_PROVIDER_USAGE_UNAVAILABLE",
            "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE",
        }
    )
    assert _diagnostic_guard_is_fatal(
        allowed_reason,
        response_evidence=evidence,
        provider_response_accepted=True,
        journal_integrity_valid=True,
    ) is False
    for reason, accepted, evidence_value, journal_valid in (
        (
            "CLEAN_EVAL_BLOCK_HARNESS_PROVIDER_USAGE_UNAVAILABLE",
            True,
            {
                **evidence,
                "usage_status": "INCOMPLETE",
                "usage_metadata": {"input_tokens": 10, "output_tokens": None},
                "billing_uncertainty": True,
            },
            True,
        ),
        (allowed_reason, False, evidence, True),
        (allowed_reason, True, {**evidence, "usage_present": False}, True),
        (allowed_reason, True, {**evidence, "response_sha256": "z" * 64}, True),
        (allowed_reason, True, evidence, False),
    ):
        assert _diagnostic_guard_is_fatal(
            reason,
            response_evidence=evidence_value,
            provider_response_accepted=accepted,
            journal_integrity_valid=journal_valid,
        ) is True


def test_persisted_run_level_failure_omits_exception_text_and_credentials(tmp_path) -> None:
    import json

    from scripts.run_public_memory_clean_eval import (
        _persist_clean_eval_block,
        _safe_run_level_failure,
    )

    secret = "https://user:pass@proxy.test Bearer private-token prompt fragment"
    safe = _safe_run_level_failure(RuntimeError(secret))
    artifact = _persist_clean_eval_block(
        tmp_path,
        run_id="synthetic-run",
        sequence=0,
        case_id="RUN_EXECUTION",
        method="RUN_LEVEL",
        attempt_no=0,
        safe_block=safe,
        request_hash=None,
        response_evidence=None,
    )
    serialized = artifact.read_text(encoding="utf-8")

    assert "RuntimeError" in serialized
    assert "throw_site" in serialized
    assert "bounded_stack" in serialized
    assert "safe_message" not in serialized
    assert secret not in serialized
    assert "private-token" not in serialized
    assert "prompt fragment" not in serialized


def test_persisted_integrity_failure_is_counted_in_clean_eval_blocks(tmp_path) -> None:
    import json

    from scripts.run_public_memory_clean_eval import (
        _persist_clean_eval_block,
        _reconstruct_clean_eval_blocks,
        _safe_run_level_failure,
    )

    reason_code = "CLEAN_EVAL_INTEGRITY_VALIDATION_FAILED"
    safe = _safe_run_level_failure(
        CleanEvalIntegrityError("synthetic private context", reason_code=reason_code)
    )
    _persist_clean_eval_block(
        tmp_path,
        run_id="integrity-run",
        sequence=0,
        case_id="RUN_EXECUTION",
        method="RUN_LEVEL",
        attempt_no=0,
        safe_block=safe,
        request_hash=None,
        response_evidence=None,
    )

    blocks = _reconstruct_clean_eval_blocks(tmp_path)

    assert blocks["integrity_valid"] is True
    assert blocks["block_count"] == 1
    assert blocks["reason_code_counts"] == {reason_code: 1}
    assert blocks["blocks"][0]["outcome_domain"] == "INTEGRITY"
    assert "synthetic private context" not in json.dumps(blocks)


def test_diagnostic_finalizer_reconstructs_partial_run_after_interruption(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    root = _write_diagnostic_finalizer_fixture(
        tmp_path / "partial",
        case_count=200,
        interrupted_sequence=1,
    )
    summary = _finalize_diagnostic_run(root)

    assert summary["status"] == "INTERRUPTED_PARTIAL"
    assert summary["lifecycle"]["not_run_method_cases"] == 199
    assert summary["provider_completion"]["accepted_responses"] == 1
    assert summary["lifecycle"]["interrupted_method_cases"] == 1
    assert summary["artifact_integrity"]["status"] == "PASS"


def test_diagnostic_finalizer_preserves_durable_case_after_terminal_event_gap(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    root = _write_diagnostic_finalizer_fixture(
        tmp_path / "terminal-gap",
        case_count=200,
        completed_without_case_terminal_sequence=1,
    )
    summary = _finalize_diagnostic_run(root)
    first = json.loads((root / "cases" / "001.json").read_text(encoding="utf-8"))

    assert summary["status"] == "INTERRUPTED_PARTIAL"
    assert summary["lifecycle"]["completed_method_cases"] == 1
    assert summary["lifecycle"]["interrupted_method_cases"] == 1
    assert summary["lifecycle"]["not_run_method_cases"] == 199
    assert first["provider_outcome"] == "PROVIDER_COMPLETE"
    assert first["provider_input_tokens"] == 10
    assert summary["artifact_integrity"]["status"] == "PASS"


def test_diagnostic_finalizer_reconstructs_case_artifact_from_terminal_journal(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    root = _write_diagnostic_finalizer_fixture(
        tmp_path / "missing-case-artifact",
        case_count=200,
        missing_case_artifact_sequence=1,
    )
    summary = _finalize_diagnostic_run(root)
    first = json.loads((root / "cases" / "001.json").read_text(encoding="utf-8"))

    assert summary["status"] == "COMPLETE"
    assert summary["lifecycle"]["completed_method_cases"] == 200
    assert summary["lifecycle"]["not_run_method_cases"] == 0
    assert first["provider_outcome"] == "PROVIDER_COMPLETE"
    assert first["provider_response_accepted"] is True
    assert summary["artifact_integrity"]["case_artifacts"] == "PASS"
    assert summary["artifact_integrity"]["status"] == "PASS"


@pytest.mark.parametrize(
    ("field", "bad_value", "expected_journal_error"),
    [
        ("response_sha256", "c" * 64, "PROVIDER_COMPLETION_RESPONSE_HASH_MISMATCH"),
        ("generation_request_sha256", "c" * 64, "PROVIDER_COMPLETION_REQUEST_HASH_MISMATCH"),
        ("provider_input_tokens", 11, "PROVIDER_COMPLETION_USAGE_MISMATCH"),
        ("provider_output_tokens", 3, "PROVIDER_COMPLETION_USAGE_MISMATCH"),
    ],
)
def test_diagnostic_finalizer_rejects_completion_mismatched_with_accepted_response(
    tmp_path, field: str, bad_value: object, expected_journal_error: str
) -> None:
    import scripts.run_public_memory_clean_eval as runner

    root = _write_diagnostic_finalizer_fixture(tmp_path / f"completion-mismatch-{field}", case_count=200)
    journal_path = root / "provider_attempt_journal.jsonl"
    events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    completion = next(
        event
        for event in events
        if event.get("sequence") == 1 and event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    )
    completion[field] = bad_value
    journal_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    summary = runner._finalize_diagnostic_run(root)

    assert summary["status"] == "BLOCKED"
    assert summary["stop_reason"] == "JOURNAL_PROTOCOL_CORRUPTION"
    assert summary["artifact_integrity"]["journal"] == "FAIL"
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    assert expected_journal_error in manifest["journal_protocol"]["errors"]


def test_diagnostic_finalizer_rejects_case_artifact_missing_accepted_response_hash(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import (
        CleanEvalBlocked,
        _finalize_diagnostic_run,
        _read_case_result,
        _record_case_result,
    )

    root = _write_diagnostic_finalizer_fixture(tmp_path / "missing-case-response-hash", case_count=200)
    case = _read_case_result(root, 1)
    assert case is not None
    case.pop("response_sha256")
    _record_case_result(root, case)

    with pytest.raises(CleanEvalBlocked, match="DIAGNOSTIC_METHOD_CASE_ARTIFACT_MISMATCH_1"):
        _finalize_diagnostic_run(root)


def test_diagnostic_finalizer_seals_run_level_block_and_marks_remaining_cases_not_run(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    root = _write_diagnostic_finalizer_fixture(
        tmp_path / "run-level-block",
        case_count=200,
        run_level_block=True,
    )
    summary = _finalize_diagnostic_run(root)
    outputs = json.loads((root / "provider_outputs.json").read_text(encoding="utf-8"))

    assert summary["status"] == "PARTIAL"
    assert summary["stop_reason"].startswith("CLEAN_EVAL_BLOCK_LOCAL_")
    assert summary["lifecycle"]["not_run_method_cases"] == 200
    assert summary["lifecycle"]["started_method_cases"] == 0
    assert summary["harness_blocks"]["count"] == 1
    assert summary["artifact_integrity"]["clean_eval_blocks"] == "PASS"
    assert summary["artifact_integrity"]["case_artifacts"] == "PASS"
    assert summary["artifact_integrity"]["status"] == "PASS"
    assert len(outputs["provider_outputs"]) == 200
    assert all(row["provider_outcome"] == "NOT_RUN" for row in outputs["provider_outputs"])


def test_diagnostic_finalizer_keeps_provider_error_separate_from_local_blocks(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    root = _write_diagnostic_finalizer_fixture(
        tmp_path / "provider-error",
        case_count=200,
        provider_error_sequence=1,
    )
    summary = _finalize_diagnostic_run(root)

    assert summary["status"] == "PARTIAL"
    assert summary["provider_completion"]["accepted_responses"] == 0
    assert summary["provider_completion"]["provider_error_attempts"] == 1
    assert summary["provider_completion"]["local_harness_block_attempts"] == 0
    assert summary["harness_blocks"]["count"] == 0
    assert summary["lifecycle"]["not_run_method_cases"] == 199
    assert summary["artifact_integrity"]["status"] == "PASS"


def test_diagnostic_finalizer_completes_two_hundred_terminal_cases(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import _finalize_diagnostic_run

    root = _write_diagnostic_finalizer_fixture(tmp_path / "complete", case_count=200)
    summary = _finalize_diagnostic_run(root)

    assert summary["status"] == "COMPLETE"
    assert summary["lifecycle"]["terminal_method_cases"] == 200
    assert summary["lifecycle"]["completed_method_cases"] == 200
    assert summary["lifecycle"]["not_run_method_cases"] == 0
    assert summary["provider_completion"]["accepted_responses"] == 200
    assert summary["artifact_integrity"]["status"] == "PASS"


def test_diagnostic_runner_never_scores_and_keeps_case_local_block_local(tmp_path, monkeypatch) -> None:
    import json

    import scripts.run_public_memory_clean_eval as runner

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["malformed_response"]},
        diagnostic_mode=True,
    )
    summary = replay["result"]["summary"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))
    first = outputs["provider_outputs"][0]

    assert replay["result"]["status"] == "COMPLETE_WITH_LOCAL_BLOCKS"
    assert summary["scoring"] == "DISABLED"
    assert summary["gold_loading"] == "FORBIDDEN"
    assert summary["gold_reads"] == 0
    assert summary["semantic_benchmark_verdict"] == "DISABLED"
    assert "accuracy_aggregation" not in summary
    assert summary["provider_completion"]["accepted_responses"] == 200
    assert summary["provider_completion"]["provider_error_attempts"] == 0
    assert summary["provider_completion"]["local_harness_block_attempts"] == 1
    assert first["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert first["reason_code"] == "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"
    assert all("response_text" not in row for row in outputs["provider_outputs"])
    assert replay["scorer_calls"] == []
    assert not (replay["root"].parents[1] / "DIAGNOSTIC_EXECUTION_BASELINE.json").exists()
    assert runner_seal_valid(replay["root"] / "diagnostic_plan.json", f"diagnostic-plan:{replay['result']['run_id']}")
    assert runner_seal_valid(replay["root"] / "diagnostic_summary.json", f"diagnostic-summary:{replay['result']['run_id']}")


def test_diagnostic_runner_stops_on_non_allowlisted_local_guard(tmp_path, monkeypatch) -> None:
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["post_response_runtime"]},
        diagnostic_mode=True,
    )
    summary = replay["result"]["summary"]
    first_case_attempts = [call for call in replay["calls"] if call["sequence"] == 1]

    assert replay["result"]["status"] == "PARTIAL"
    assert summary["stop_reason"] == "CLEAN_EVAL_BLOCK_LOCAL_RUNTIME_ERROR"
    assert summary["provider_completion"]["accepted_responses"] == 1
    assert summary["provider_completion"]["provider_error_attempts"] == 0
    assert summary["provider_completion"]["local_harness_block_attempts"] == 1
    assert summary["lifecycle"]["not_run_method_cases"] == 199
    assert [call["attempt_no"] for call in first_case_attempts] == [1]
    assert replay["scorer_calls"] == []


def test_diagnostic_scoring_entrypoint_is_a_hard_stop(tmp_path) -> None:
    from scripts.run_public_memory_clean_eval import CleanEvalBlocked, _score_sealed_run

    with pytest.raises(CleanEvalBlocked) as error:
        _score_sealed_run(
            root=tmp_path,
            cell_key="sh_32k",
            case=None,
            preflight={},
            provider_outputs=[],
            provider_outputs_path=tmp_path / "provider_outputs.json",
            fingerprints={},
            diagnostic_mode=True,
        )

    assert error.value.reason_code == "CLEAN_EVAL_BLOCK_GOLD_DIAGNOSTIC_SCORING_VIOLATION"


def test_recovery_finalizer_releases_only_the_matching_running_reservation(tmp_path, monkeypatch) -> None:
    import scripts.run_public_memory_clean_eval as runner

    root = _write_diagnostic_finalizer_fixture(tmp_path / "budget-recovery", case_count=200)
    run_id = root.name
    ledger = {
        "schema_version": "linkloom-public-memory-global-budget/v1",
        "hard_cap_usd": runner.GLOBAL_COST_CAP_USD,
        "active_run_id": run_id,
        "runs": [{
            "run_id": run_id,
            "cell": "sh_32k",
            "status": "RUNNING",
            "reserved_usd": 0.08,
            "hard_cost_cap_usd": 0.08,
            "estimated_spend_upper_bound_usd": 0.001,
        }],
    }
    writes = []
    monkeypatch.setattr(runner, "_load_budget_ledger", lambda **kwargs: ledger)
    monkeypatch.setattr(runner, "_write_budget_ledger", lambda value, **kwargs: writes.append(value))

    runner._release_recovered_diagnostic_budget_reservation(
        root,
        {"status": "INTERRUPTED_PARTIAL", "cost": {"estimated_spend_upper_bound_usd": 0.001}},
    )

    assert ledger["active_run_id"] is None
    assert ledger["runs"][0]["status"] == "INTERRUPTED_PARTIAL"
    assert ledger["runs"][0]["reserved_usd"] == 0.0
    assert ledger["runs"][0]["estimated_spend_upper_bound_usd"] == 0.001
    assert len(writes) == 1


def test_mh_only_usage_incomplete_receipt_blocks_without_gold_or_provider_error(tmp_path, monkeypatch) -> None:
    import scripts.run_public_memory_clean_eval as runner

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {200: ["missing_output"]},
        cell_key="mh_6k",
        evaluation_plan_id=runner.MH_ONLY_EVALUATION_PLAN_ID,
    )
    summary = replay["result"]["summary"]
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    evidence = runner._read_accepted_response_evidence(replay["root"] / "accepted_response_evidence.jsonl")
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))["provider_outputs"]
    blocked_output = outputs[-1]
    incomplete_pair = outputs[-2:]
    event_order = [event["event"] for event in events]

    assert summary["status"] == "STOPPED"
    assert summary["gold_values_read"] is False
    assert summary["score_status"] == "SKIPPED"
    assert replay["gold_calls"] == []
    assert replay["scorer_calls"] == []
    assert len(replay["calls"]) == 200
    assert summary["provider_completion"]["provider_response_completed_method_cases"] == 200
    assert summary["provider_completion"]["provider_error_method_cases"] == 0
    assert summary["provider_completion"]["local_harness_block_method_cases"] == 1
    assert blocked_output["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert blocked_output["reason_code"] == "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE"
    assert blocked_output["provider_response_accepted"] is True
    assert blocked_output["response_sha256"]
    assert blocked_output["provider_input_tokens"] == 100
    assert blocked_output["provider_thinking_tokens"] == 157
    assert blocked_output["provider_output_tokens"] is None
    assert blocked_output["usage_status"] == "INCOMPLETE"
    assert incomplete_pair[0]["qa_id"] == incomplete_pair[1]["qa_id"]
    assert {row["method"] for row in incomplete_pair} == {
        runner.METHOD_FLAT_RETRIEVAL,
        runner.METHOD_TEMPORAL_MEMORY,
    }
    assert {row["provider_outcome"] for row in incomplete_pair} == {
        "PROVIDER_COMPLETE",
        "LOCAL_HARNESS_BLOCK",
    }
    assert evidence["integrity_valid"] is True
    assert len(evidence["rows"]) == 200
    last_case_events = [event["event"] for event in events if event.get("sequence") == 200]
    assert last_case_events.index("RESPONSE_RECEIVED") < last_case_events.index("RESPONSE_EVIDENCE_PERSISTED")
    assert last_case_events.index("RESPONSE_EVIDENCE_PERSISTED") < last_case_events.index("USAGE_VALIDATED")
    assert events[-1]["event"] == "CASE_METHOD_BLOCKED"
    assert replay["result"]["finalizer_integrity"]["status"] == "PASS"


def test_mh_only_scoring_keeps_provider_failure_in_fixed_denominator_and_pair_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.run_public_memory_clean_eval as runner

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["connect", "connect", "connect"]},
        cell_key="mh_6k",
        evaluation_plan_id=runner.MH_ONLY_EVALUATION_PLAN_ID,
        score_with_synthetic_gold=True,
    )
    summary = replay["result"]["summary"]
    scored = summary["final_score_summary"]

    assert summary["status"] == "COMPLETE_WITH_PROVIDER_ERRORS"
    assert summary["protocol_status"] == "VALID"
    assert summary["gold_values_read"] is True
    assert summary["score_status"] == "PASS"
    assert len(replay["gold_calls"]) == 1
    flat = scored["method_metrics"][runner.METHOD_FLAT_RETRIEVAL]
    assert flat["correct"] == 99
    assert flat["incorrect"] == 0
    assert flat["not_evaluated"] == 1
    assert flat["provider_failure"] == 1
    assert flat["local_harness_block"] == 0
    assert flat["planned"] == 100
    assert flat["scored_coverage"] == 0.99
    assert flat["primary_accuracy"] == 0.99
    paired = scored["paired_complete"]
    assert paired["question_count"] == 99
    assert paired["planned_question_count"] == 100
    assert paired["coverage_rate"] == 0.99
    assert paired["unpaired_questions"] == [
        {
            "qa_id": "synthetic_qa_0",
            "flat_provider_outcome": "PROVIDER_ERROR",
            "temporal_provider_outcome": "PROVIDER_COMPLETE",
            "flat_score": None,
            "temporal_score": True,
        }
    ]


def test_historical_dual_dataset_plan_keeps_the_formal_sh_first_gate(tmp_path, monkeypatch) -> None:
    import scripts.run_public_memory_clean_eval as runner

    with pytest.raises(runner.CleanEvalBlocked) as error:
        _run_synthetic_clean_live(
            tmp_path,
            monkeypatch,
            {},
            cell_key="mh_6k",
            evaluation_plan_id=runner.LEGACY_DUAL_EVALUATION_PLAN_ID,
        )

    assert error.value.reason_code == "CLEAN_EVAL_BLOCK_OTHER_SH_32K_MUST_COMPLETE_BEFORE_MH_6K"
    assert not (tmp_path / "live_artifacts" / "mh_6k" / "synthetic-new-v3-run").exists()


def test_diagnostic_cli_dispatches_provider_run_and_local_recovery(tmp_path, monkeypatch, capsys) -> None:
    import hashlib
    import sys

    import scripts.run_public_memory_clean_eval as runner

    fake_dataset = tmp_path / "synthetic-dataset.parquet"
    fake_dataset.write_bytes(b"synthetic pinned fixture")
    monkeypatch.setattr(runner, "DATASET_PATH", fake_dataset)
    monkeypatch.setattr(runner, "DATASET_SHA256", hashlib.sha256(fake_dataset.read_bytes()).hexdigest())
    run_calls = []
    monkeypatch.setattr(
        runner,
        "_run_live",
        lambda cell, **kwargs: run_calls.append((cell, kwargs)) or {"status": "SYNTHETIC"},
    )
    monkeypatch.setattr(sys, "argv", ["runner", "--diagnostic", "mh_6k", "--preflight-dir", str(tmp_path)])

    assert runner.main() == 0
    assert run_calls == [("mh_6k", {"preflight_dir": tmp_path, "diagnostic_mode": True})]
    assert json.loads(capsys.readouterr().out)["status"] == "SYNTHETIC"

    recovery_calls = []
    monkeypatch.setattr(runner, "_finalize_diagnostic_run", lambda path: {
        "run_id": "local-recovered",
        "status": "INTERRUPTED_PARTIAL",
        "artifact_integrity": {"status": "PASS"},
    })
    monkeypatch.setattr(
        runner,
        "_release_recovered_diagnostic_budget_reservation",
        lambda path, summary: recovery_calls.append((path, summary["run_id"])),
    )
    monkeypatch.setattr(sys, "argv", ["runner", "--finalize-diagnostic", str(tmp_path / "run")])

    assert runner.main() == 0
    assert recovery_calls == [(tmp_path / "run", "local-recovered")]
    assert json.loads(capsys.readouterr().out)["run_id"] == "local-recovered"


def _write_diagnostic_finalizer_fixture(
    root,
    *,
    case_count: int,
    interrupted_sequence: int | None = None,
    completed_without_case_terminal_sequence: int | None = None,
    missing_case_artifact_sequence: int | None = None,
    provider_error_sequence: int | None = None,
    run_level_block: bool = False,
):
    import json
    from datetime import UTC, datetime

    import scripts.run_public_memory_clean_eval as runner

    root.mkdir(parents=True, exist_ok=True)
    run_id = root.name
    plan_rows = [
        {
            "sequence": sequence,
            "qa_id": f"qa-{sequence}",
            "method": "Flat Retrieval",
            "generation_request_sha256": f"{sequence:064x}",
            "count_tokens_request_sha256": f"{sequence + 500:064x}",
            "context_sha256": f"{sequence + 1000:064x}",
            "context_tokens_estimated": 25,
            "context_evidence_ids_selected": [f"fact-{sequence}"],
            "retrieval_evidence_ids": [f"fact-{sequence}"],
            "memory_lookup_status": "HIT",
            "memory_source_evidence_ids": [f"fact-{sequence}"],
            "one_attempt_cost_upper_bound_usd": 0.001,
        }
        for sequence in range(1, case_count + 1)
    ]
    plan_path = root / "diagnostic_plan.json"
    runner._atomic_json_write_fsync(
        plan_path,
        {"run_id": run_id, "cell_key": "sh_32k", "plan_rows": plan_rows},
    )
    runner._seal_file(plan_path, identity=f"diagnostic-plan:{run_id}")
    runner._write_run_manifest(root, {
        "run_id": run_id,
        "cell_key": "sh_32k",
        "cell": runner.CELLS["sh_32k"].display_name,
        "purpose": "HARNESS_DIAGNOSTIC",
        "diagnostic_mode": True,
        "implementation_fingerprints": runner.implementation_fingerprints(),
        "created_at_utc": datetime.now(UTC).isoformat(),
    })
    journal_path = root / "provider_attempt_journal.jsonl"
    accepted_path = root / "accepted_response_evidence.jsonl"
    journal_path.touch()
    accepted_path.touch()
    blocks: list[dict[str, object]] = []
    outputs: list[dict[str, object]] = []
    usage_evidence = runner._response_usage_evidence(
        {
            "text": "synthetic diagnostic answer",
            "usage_metadata": {
                "prompt_token_count": 10,
                "candidates_token_count": 2,
                "thoughts_token_count": 0,
            },
        }
    )
    for row in ([] if run_level_block else plan_rows):
        sequence = row["sequence"]
        attempt_id = f"{sequence}:1"
        accepted_at_utc = datetime.now(UTC).isoformat()
        receipt_fields = {
            name: usage_evidence[name]
            for name in (
                "usage_present",
                "usage_metadata",
                "usage_status",
                "usage_missing_fields",
                "usage_sources",
                "provider_usage_fields",
                "provider_usage_object_present",
                "usage_invalid_fields",
                "usage_access_failures",
                "billing_uncertainty",
                "billing_basis",
                "provider_reported_cost_usd",
                "provider_reported_cost_source",
            )
        }
        events = [
            {"event": "CASE_METHOD_STARTED", "run_id": run_id, "sequence": sequence, "qa_id": row["qa_id"], "method": row["method"]},
            {"event": "PROVIDER_ATTEMPT_STARTED", "run_id": run_id, "sequence": sequence, "qa_id": row["qa_id"], "method": row["method"], "attempt_no": 1, "attempt_id": attempt_id, "generation_request_sha256": row["generation_request_sha256"], "provider_response_accepted": False},
            {
                "event": "RESPONSE_RECEIVED",
                "run_id": run_id,
                "sequence": sequence,
                "qa_id": row["qa_id"],
                "method": row["method"],
                "attempt_no": 1,
                "attempt_id": attempt_id,
                "provider_response_accepted": True,
                "request_sha256": row["generation_request_sha256"],
                "response_sha256": "b" * 64,
                "model": "gemini-test-model",
                **receipt_fields,
                "provider_request_id": None,
                "accepted_at_utc": accepted_at_utc,
            },
        ]
        evidence = {
            "run_id": run_id,
            "sequence": sequence,
            "case_id": row["qa_id"],
            "method": row["method"],
            "attempt_no": 1,
            "logical_attempt_id": attempt_id,
            "provider_response_accepted": True,
            "request_sha256": row["generation_request_sha256"],
            "response_sha256": "b" * 64,
            "model": "gemini-test-model",
            **usage_evidence,
            "provider_request_id": None,
            "accepted_at_utc": accepted_at_utc,
        }
        events.extend(
            [
                {
                    "event": "RESPONSE_EVIDENCE_PERSISTED",
                    "run_id": run_id,
                    "sequence": sequence,
                    "qa_id": row["qa_id"],
                    "method": row["method"],
                    "attempt_no": 1,
                    "attempt_id": attempt_id,
                    "provider_response_accepted": True,
                    "request_sha256": row["generation_request_sha256"],
                    "response_sha256": "b" * 64,
                    "evidence_sha256": runner.canonical_sha256(evidence),
                    "accepted_at_utc": accepted_at_utc,
                },
                {
                    "event": "USAGE_VALIDATED",
                    "run_id": run_id,
                    "sequence": sequence,
                    "qa_id": row["qa_id"],
                    "method": row["method"],
                    "attempt_no": 1,
                    "attempt_id": attempt_id,
                    "provider_response_accepted": True,
                    "request_sha256": row["generation_request_sha256"],
                    "response_sha256": "b" * 64,
                    **receipt_fields,
                    "accepted_at_utc": accepted_at_utc,
                },
            ]
        )
        if sequence != provider_error_sequence:
            with accepted_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(evidence, sort_keys=True) + "\n")
        if sequence == provider_error_sequence:
            failure = {
                "event": "PROVIDER_ATTEMPT_COMPLETED",
                "run_id": run_id,
                "sequence": sequence,
                "qa_id": row["qa_id"],
                "method": row["method"],
                "attempt_no": 1,
                "provider_outcome": "PROVIDER_ERROR",
                "provider_request_sent": True,
                "provider_response_accepted": False,
                "retryable_transient_failure": False,
                "safe_error": {
                    "outcome_domain": "PROVIDER",
                    "failure_classification": "PROVIDER ERROR",
                    "provider_outcome": "PROVIDER_ERROR",
                    "reason_code": "TRANSPORT_CONNECT",
                    "provider_response_accepted": False,
                },
            }
            events = events[:2] + [failure]
            events.append({
                "event": "CASE_METHOD_COMPLETED",
                "run_id": run_id,
                "sequence": sequence,
                "qa_id": row["qa_id"],
                "method": row["method"],
                "provider_outcome": "PROVIDER_ERROR",
            })
            for event in events:
                runner._append_jsonl_fsync(journal_path, event)
            output = {
                "sequence": sequence,
                "qa_id": row["qa_id"],
                "method": row["method"],
                "provider_outcome": "PROVIDER_ERROR",
                "provider_response_accepted": False,
                "provider_attempts": 1,
                "request_sha256": row["generation_request_sha256"],
                "context_sha256": row["context_sha256"],
                "context_tokens_estimated": 25,
                "safe_error": failure["safe_error"],
            }
            outputs.append(output)
            runner._record_case_result(root, runner._safe_diagnostic_case_row(output, row))
            break
        if sequence == interrupted_sequence:
            for event in events:
                runner._append_jsonl_fsync(journal_path, event)
            outputs.append({
                "sequence": sequence,
                "qa_id": row["qa_id"],
                "method": row["method"],
                "provider_outcome": "LOCAL_HARNESS_BLOCK",
                "provider_response_accepted": True,
                "reason_code": "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_PROCESS_INTERRUPTED",
                "safe_error": {"reason_code": "CLEAN_EVAL_BLOCK_LOCAL_HARNESS_PROCESS_INTERRUPTED"},
                "provider_attempts": 1,
                "context_tokens_estimated": 25,
            })
            break
        events.append(
            {
                "event": "PROVIDER_ATTEMPT_COMPLETED",
                "run_id": run_id,
                "sequence": sequence,
                "qa_id": row["qa_id"],
                "method": row["method"],
                "attempt_no": 1,
                "provider_outcome": "PROVIDER_COMPLETE",
                "provider_response_accepted": True,
                "generation_request_sha256": row["generation_request_sha256"],
                "response_sha256": "b" * 64,
                "provider_input_tokens": 10,
                "provider_output_tokens": 2,
                "provider_thinking_tokens": 0,
                "usage_status": usage_evidence["usage_status"],
                "usage_missing_fields": usage_evidence["usage_missing_fields"],
                "billing_uncertainty": usage_evidence["billing_uncertainty"],
                "billing_basis": usage_evidence["billing_basis"],
            }
        )
        if sequence != completed_without_case_terminal_sequence:
            events.append(
                {"event": "CASE_METHOD_COMPLETED", "run_id": run_id, "sequence": sequence, "qa_id": row["qa_id"], "method": row["method"], "provider_outcome": "PROVIDER_COMPLETE"}
            )
        for event in events:
            runner._append_jsonl_fsync(journal_path, event)
        outputs.append({
            "sequence": sequence,
            "qa_id": row["qa_id"],
            "method": row["method"],
            "provider_outcome": "PROVIDER_COMPLETE",
            "provider_response_accepted": True,
            "provider_attempts": 1,
            "request_sha256": row["generation_request_sha256"],
            "response_sha256": "b" * 64,
            "context_sha256": row["context_sha256"],
            "context_tokens_estimated": 25,
            "provider_input_tokens": 10,
            "provider_output_tokens": 2,
            "provider_elapsed_ms": 2.5,
            "provider_reported_cost_usd": 0.0001,
        })
        if sequence != missing_case_artifact_sequence:
            runner._record_case_result(root, runner._safe_diagnostic_case_row(outputs[-1], row))
        if sequence == completed_without_case_terminal_sequence:
            break
    with accepted_path.open("ab") as stream:
        stream.flush()
        import os
        os.fsync(stream.fileno())
    (root / "clean_eval_blocks").mkdir(exist_ok=True)
    if run_level_block:
        safe_block = runner._safe_run_level_failure(RuntimeError("synthetic local run-level failure"))
        runner._persist_clean_eval_block(
            root,
            run_id=run_id,
            sequence=0,
            case_id="RUN_EXECUTION",
            method="RUN_LEVEL",
            attempt_no=0,
            safe_block=safe_block,
            request_hash=None,
            response_evidence=None,
        )
    return root


def test_dev_regression_rejects_an_unpinned_dataset(tmp_path) -> None:
    from scripts.run_public_memory_dev_regression import validate_pinned_dataset

    candidate = tmp_path / "different.parquet"
    candidate.write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="pinned SH-6k dataset"):
        validate_pinned_dataset(candidate)


def test_diagnostic_stress_continues_after_classified_case_local_guard(tmp_path, monkeypatch) -> None:
    import json

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["malformed_response"]},
        diagnostic_stress=True,
    )
    summary = replay["result"]["summary"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))
    rows = outputs["provider_outputs"]

    assert replay["result"]["status"] == "HARNESS_STRESS_PASS"
    assert summary["purpose"] == "HARNESS_STABILITY"
    assert summary["dataset_classification"] == "DIAGNOSTIC_STRESS_SET_NOT_CLEAN_HELD_OUT"
    assert summary["accuracy_claim"] is False
    assert summary["score_status"] == "DISABLED_DIAGNOSTIC_ONLY"
    assert summary["gold_values_read"] is False
    assert summary["harness_execution"]["terminal_method_cases"] == 200
    assert summary["provider_completion"]["provider_response_completed_method_cases"] == 200
    assert summary["provider_reliability"]["provider_error_attempts"] == 0
    assert summary["provider_reliability"]["local_harness_block_attempts"] == 1
    assert summary["harness_stability_verdict"] == "HARNESS_STABLE_WITH_KNOWN_BLOCKS"
    assert rows[0]["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert rows[1]["provider_outcome"] == "PROVIDER_COMPLETE"
    assert replay["scorer_calls"] == []


def test_diagnostic_stress_retries_only_transient_provider_failure(tmp_path, monkeypatch) -> None:
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["connect", "success"]},
        diagnostic_stress=True,
    )
    summary = replay["result"]["summary"]
    first_case_attempts = [call for call in replay["calls"] if call["sequence"] == 1]

    assert replay["result"]["status"] == "HARNESS_STRESS_PASS"
    assert [call["attempt_no"] for call in first_case_attempts] == [1, 2]
    assert summary["provider_requests"]["generation_attempts"] == 201
    assert summary["provider_reliability"]["provider_error_attempts"] == 1
    assert summary["provider_completion"]["provider_response_completed_method_cases"] == 200
    assert summary["harness_execution"]["terminal_method_cases"] == 200
    assert summary["score_status"] == "DISABLED_DIAGNOSTIC_ONLY"
    assert summary["gold_values_read"] is False


def test_accepted_response_is_fsynced_before_downstream_guard_artifact(tmp_path, monkeypatch) -> None:
    import json

    observations = []

    def assert_response_evidence_first(root, kwargs):
        evidence_path = root / "accepted_response_evidence.jsonl"
        rows = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
        matching = [row for row in rows if row["sequence"] == kwargs["sequence"]]
        observations.append(bool(matching and matching[0]["response_sha256"]))

    _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["malformed_response"]},
        diagnostic_stress=True,
        block_hook=assert_response_evidence_first,
    )

    assert observations == [True]


def test_provider_completion_rate_does_not_count_local_block_as_provider_error() -> None:
    from scripts.run_public_memory_clean_eval import _provider_completion_stats

    stats = _provider_completion_stats(
        [
            {
                "event": "PROVIDER_ATTEMPT_COMPLETED",
                "sequence": 1,
                "attempt_no": 1,
                "provider_outcome": "LOCAL_HARNESS_BLOCK",
                "provider_request_sent": True,
                "provider_response_accepted": True,
                "safe_error": {"provider_response_already_accepted": True},
            },
            {
                "event": "PROVIDER_ATTEMPT_COMPLETED",
                "sequence": 2,
                "attempt_no": 1,
                "provider_outcome": "PROVIDER_ERROR",
                "provider_request_sent": True,
            },
        ]
    )

    assert stats == {
        "generation_attempts": 2,
        "provider_completed_requests": 1,
        "accepted_response_attempts": 1,
        "provider_error_attempts": 1,
        "local_harness_block_attempts": 1,
        "completion_rate": 0.5,
    }


def test_pre_response_provider_failure_remains_provider_error() -> None:
    from scripts.run_public_memory_clean_eval import _failed_attempt_provider_outcome

    assert _failed_attempt_provider_outcome(
        {
            "exception_class": "ConnectError",
            "outcome_domain": "PROVIDER",
            "provider_response_accepted": False,
        }
    ) == "PROVIDER_ERROR"


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("OUTPUT_PERSIST_FAILED", "ARTIFACT"),
        ("COST_LEDGER_INCONSISTENT", "COST"),
        ("IMPLEMENTATION_FINGERPRINT_MISMATCH", "FINGERPRINT"),
        ("EXECUTION_ORDER_MISMATCH", "PROTOCOL"),
        ("SCORE_PRECONDITION_FAILED", "SCORING"),
    ],
)
def test_accepted_response_guard_categories_are_independently_sealed(
    tmp_path, reason: str, category: str
) -> None:
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import (
        CleanEvalBlocked,
        _persist_clean_eval_block,
        _reconstruct_clean_eval_blocks,
        _safe_clean_eval_block,
    )

    response_evidence = {
        "provider_response_accepted": True,
        "response_sha256": "b" * 64,
        "usage_present": True,
        "usage_metadata": {"input_tokens": 3},
        "provider_request_id": "request-1",
    }
    try:
        raise CleanEvalBlocked(reason)
    except CleanEvalBlocked as error:
        safe = _safe_clean_eval_block(
            error,
            SimpleNamespace(last_guard_state="RESPONSE_ACCEPTED"),
            logical_case_id="qa-17",
            method="LinkLoom Temporal Memory",
            response_evidence=response_evidence,
        )

    path = _persist_clean_eval_block(
        tmp_path,
        run_id="run-diagnostic",
        sequence=17,
        case_id="qa-17",
        method="LinkLoom Temporal Memory",
        attempt_no=1,
        safe_block=safe,
        request_hash="a" * 64,
        response_evidence=response_evidence,
    )
    reconstructed = _reconstruct_clean_eval_blocks(tmp_path)

    assert safe["reason_code"].startswith(f"CLEAN_EVAL_BLOCK_{category}_")
    assert path.is_file()
    assert reconstructed["integrity_valid"] is True
    assert reconstructed["block_count"] == 1
    assert reconstructed["blocks"][0]["provider_response_accepted"] is True
    assert reconstructed["blocks"][0]["response_sha256"] == "b" * 64
    assert reconstructed["blocks"][0]["usage_present"] is True
    assert reconstructed["blocks"][0]["request_sha256"] == "a" * 64


def test_block_artifact_has_bounded_safe_origin_without_prompt_or_secrets(tmp_path) -> None:
    import json
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import (
        CleanEvalBlocked,
        _persist_clean_eval_block,
        _safe_clean_eval_block,
    )

    try:
        raise CleanEvalBlocked("SCORE_PRECONDITION_FAILED")
    except CleanEvalBlocked as error:
        safe = _safe_clean_eval_block(
            error,
            SimpleNamespace(last_guard_state="RESPONSE_ACCEPTED"),
            logical_case_id="qa-safe-id",
            method="Flat Retrieval",
            response_evidence={"provider_response_accepted": True},
        )
    _persist_clean_eval_block(
        tmp_path,
        run_id="run-safe",
        sequence=1,
        case_id="qa-safe-id",
        method="Flat Retrieval",
        attempt_no=1,
        safe_block=safe,
        request_hash="a" * 64,
        response_evidence={"provider_response_accepted": True},
    )
    artifact = next((tmp_path / "clean_eval_blocks").glob("*.json")).read_text(encoding="utf-8")
    payload = json.loads(artifact)

    assert len(payload["bounded_stack"]) <= 8
    assert payload["throw_site"]["function"]
    assert "private prompt text" not in artifact.casefold()
    assert "private context text" not in artifact.casefold()
    assert "secret-value" not in artifact.casefold()
    assert "api_key" not in payload
    assert "prompt" not in payload
    assert "context" not in payload


def test_run_level_block_artifacts_reconstruct_without_run_summary(tmp_path) -> None:
    import json

    from scripts.run_public_memory_clean_eval import (
        CleanEvalBlocked,
        _persist_clean_eval_block,
        _reconstruct_clean_eval_blocks,
        _safe_clean_eval_block,
    )

    try:
        raise CleanEvalBlocked("JOURNAL_PERSISTENCE_FAILURE")
    except CleanEvalBlocked as error:
        safe = _safe_clean_eval_block(error, None)
    _persist_clean_eval_block(
        tmp_path,
        run_id="run-interrupted",
        sequence=0,
        case_id="RUN_FINALIZATION",
        method="RUN_LEVEL",
        attempt_no=0,
        safe_block=safe,
        request_hash=None,
        response_evidence=None,
    )

    assert not (tmp_path / "pilot_summary.json").exists()
    reconstructed = _reconstruct_clean_eval_blocks(tmp_path)
    assert reconstructed["integrity_valid"] is True
    assert reconstructed["reason_code_counts"] == {
        "CLEAN_EVAL_BLOCK_ARTIFACT_JOURNAL_PERSISTENCE_FAILURE": 1
    }


@pytest.mark.parametrize(
    ("usage_metadata", "expected_usage", "expected_sources", "expected_status"),
    [
        (
            {"prompt_token_count": 109, "thoughts_token_count": 157},
            {"input_tokens": 109, "output_tokens": None, "thinking_tokens": 157},
            {"input_tokens": "prompt_token_count", "output_tokens": None, "thinking_tokens": "thoughts_token_count"},
            "INCOMPLETE",
        ),
        (
            None,
            {"input_tokens": None, "output_tokens": None, "thinking_tokens": None},
            {"input_tokens": None, "output_tokens": None, "thinking_tokens": None},
            "INCOMPLETE",
        ),
        (
            {"input_tokens": 11, "output_tokens": 4, "thinking_tokens": 2},
            {"input_tokens": 11, "output_tokens": 4, "thinking_tokens": 2},
            {"input_tokens": "input_tokens", "output_tokens": "output_tokens", "thinking_tokens": "thinking_tokens"},
            "COMPLETE",
        ),
    ],
)
def test_accepted_response_evidence_preserves_incomplete_usage_without_zero_fill(
    tmp_path, usage_metadata, expected_usage, expected_sources, expected_status
) -> None:
    import json
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import (
        _persist_accepted_response_evidence,
        _read_accepted_response_evidence,
    )

    path = tmp_path / "accepted_response_evidence.jsonl"
    response = SimpleNamespace(
        text="synthetic provider response",
        usage_metadata=usage_metadata,
        response_id="fake-response-1",
    )
    evidence = _persist_accepted_response_evidence(
        path,
        run_id="fake-run",
        sequence=1,
        logical_case_id="fake-case",
        method="Flat Retrieval",
        attempt_no=1,
        logical_attempt_id="1:1",
        model="gemini-test-model",
        request_hash="a" * 64,
        response=response,
    )

    assert evidence["usage_status"] == expected_status
    assert evidence["usage_metadata"] == expected_usage
    assert evidence["usage_sources"] == expected_sources
    assert evidence["billing_uncertainty"] is (expected_status == "INCOMPLETE")
    assert json.loads(path.read_text(encoding="utf-8")) == evidence
    summary = _read_accepted_response_evidence(path)
    assert summary["integrity_valid"] is True
    assert summary["rows"] == [evidence]


@pytest.mark.parametrize(
    "mutation",
    [
        {"usage_present": False},
        {"billing_uncertainty": True},
    ],
)
def test_evidence_reader_rejects_usage_presence_and_completeness_conflicts(
    tmp_path, mutation
) -> None:
    import json
    from types import SimpleNamespace

    from scripts.run_public_memory_clean_eval import (
        _persist_accepted_response_evidence,
        _read_accepted_response_evidence,
    )

    path = tmp_path / "accepted_response_evidence.jsonl"
    evidence = _persist_accepted_response_evidence(
        path,
        run_id="fake-run",
        sequence=1,
        logical_case_id="fake-case",
        method="Flat Retrieval",
        attempt_no=1,
        logical_attempt_id="1:1",
        model="gemini-test-model",
        request_hash="a" * 64,
        response=SimpleNamespace(
            text="synthetic provider response",
            usage_metadata={
                "prompt_token_count": 11,
                "candidates_token_count": 4,
                "thoughts_token_count": 2,
            },
        ),
    )
    path.write_text(
        json.dumps({**evidence, **mutation}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = _read_accepted_response_evidence(path)

    assert summary["integrity_valid"] is False
    assert summary["errors"] == ["INVALID_ACCEPTED_RESPONSE_EVIDENCE:1"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"usage_present": False},
        {"billing_uncertainty": False},
        {
            "usage_status": "COMPLETE",
            "usage_metadata": {
                "input_tokens": 10,
                "output_tokens": 1,
                "thinking_tokens": None,
            },
            "billing_uncertainty": True,
        },
        {"usage_status": "INCOMPLETE", "usage_metadata": {
            "input_tokens": 10, "output_tokens": 1, "thinking_tokens": None
        }},
    ],
)
def test_journal_usage_validator_rejects_usage_state_conflicts(mutation) -> None:
    from scripts.run_public_memory_clean_eval import _journal_usage_is_valid

    event = {
        "usage_present": True,
        "usage_status": "INCOMPLETE",
        "usage_metadata": {
            "input_tokens": 10,
            "output_tokens": None,
            "thinking_tokens": 2,
        },
        "usage_invalid_fields": [],
        "billing_uncertainty": True,
    }

    assert _journal_usage_is_valid({**event, **mutation}) is False


def test_journal_usage_validator_preserves_explicit_invalid_usage_state() -> None:
    from scripts.run_public_memory_clean_eval import _journal_usage_is_valid

    event = {
        "usage_present": True,
        "usage_status": "INVALID",
        "usage_metadata": {
            "input_tokens": 10,
            "output_tokens": None,
            "thinking_tokens": None,
        },
        "usage_invalid_fields": ["output_tokens"],
        "billing_uncertainty": True,
    }

    assert _journal_usage_is_valid(event) is True


def test_clean_eval_block_uses_canonical_usage_presence(tmp_path) -> None:
    import json

    from scripts.run_public_memory_clean_eval import _persist_clean_eval_block

    path = _persist_clean_eval_block(
        tmp_path,
        run_id="fake-run",
        sequence=1,
        case_id="fake-case",
        method="Flat Retrieval",
        attempt_no=1,
        safe_block={
            "phase": "RESPONSE",
            "reason_code": "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE",
            "outcome_domain": "LOCAL_HARNESS",
            "guard_name": "usage_guard",
            "provider_response_accepted": True,
            "throw_site": "fake.py:10",
            "exception_type": "CleanEvalBlocked",
            "bounded_stack": [],
            "nested_cause_type": None,
        },
        request_hash="a" * 64,
        response_evidence={
            "response_sha256": "b" * 64,
            "usage_present": False,
            "usage_metadata": {
                "input_tokens": None,
                "output_tokens": None,
                "thinking_tokens": None,
            },
            "provider_request_id": "fake-response",
        },
    )

    assert json.loads(path.read_text(encoding="utf-8"))["usage_present"] is False


def test_fake_provider_incomplete_usage_keeps_receipt_and_continues_case_locally(
    tmp_path, monkeypatch
) -> None:
    import json

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["thinking_only"]},
        diagnostic_mode=True,
    )
    summary = replay["result"]["summary"]
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("sequence") == 1
    ]
    lifecycle = [
        event["event"]
        for event in events
        if event.get("event") in {
            "RESPONSE_RECEIVED",
            "RESPONSE_EVIDENCE_PERSISTED",
            "USAGE_VALIDATED",
            "PROVIDER_ATTEMPT_COMPLETED",
            "CASE_METHOD_BLOCKED",
        }
    ]
    evidence = json.loads(
        (replay["root"] / "accepted_response_evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))
    first_case = outputs["provider_outputs"][0]

    assert lifecycle == [
        "RESPONSE_RECEIVED",
        "RESPONSE_EVIDENCE_PERSISTED",
        "USAGE_VALIDATED",
        "PROVIDER_ATTEMPT_COMPLETED",
        "CASE_METHOD_BLOCKED",
    ]
    assert evidence["usage_status"] == "INCOMPLETE"
    assert evidence["usage_metadata"] == {
        "input_tokens": 100,
        "output_tokens": None,
        "thinking_tokens": 157,
    }
    assert evidence["usage_sources"]["thinking_tokens"] == "thoughts_token_count"
    assert first_case["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert first_case["provider_response_accepted"] is True
    assert first_case["provider_output_tokens"] is None
    assert first_case["usage_status"] == "INCOMPLETE"
    assert first_case["reason_code"] == "CLEAN_EVAL_BLOCK_RESPONSE_PROVIDER_USAGE_UNAVAILABLE"
    assert summary["provider_completion"]["accepted_responses"] == 200
    assert summary["provider_completion"]["provider_error_attempts"] == 0
    assert summary["lifecycle"]["blocked_method_cases"] == 1
    assert summary["lifecycle"]["completed_method_cases"] == 199
    assert summary["finalizer_checks"]["response_receipt_integrity"] == "PASS"
    assert summary["finalizer_checks"]["evidence_integrity"] == "PASS"
    assert summary["finalizer_checks"]["usage_completeness"] == "INCOMPLETE"
    assert summary["finalizer_checks"]["case_completion"] == "PASS"
    assert summary["finalizer_checks"]["journal_consistency"] == "PASS"
    assert summary["tokens"]["output_tokens"] is None
    assert summary["tokens"]["unknown_token_counts"]["output_tokens"] == 1
    assert summary["cost"]["estimated_spend_upper_bound_usd"] <= 6.0
    assert summary["cost"]["estimated_spend_upper_bound_usd"] <= 10.0
    assert summary["gold_reads"] == 0
    assert summary["scoring"] == "DISABLED"
    assert replay["result"]["run_id"] == "synthetic-new-v3-run"
    assert len(replay["calls"]) == 200


def test_e2_fake_full_run_scores_bounded_incomplete_usage_and_stops_gold_on_gate_failure(
    tmp_path, monkeypatch
) -> None:
    import scripts.run_public_memory_clean_eval as runner

    evaluation_plan = runner.E2_MH_ONLY_EVALUATION_PLAN_ID
    passing_root = tmp_path / "passing"
    passing_root.mkdir()
    passing = _run_synthetic_clean_live(
        passing_root,
        monkeypatch,
        {2: ["missing_output"], 3: ["malformed_response"]},
        cell_key="mh_6k",
        evaluation_plan_id=evaluation_plan,
        score_with_synthetic_gold=True,
    )
    passing_summary = passing["result"]["summary"]
    passing_events = [
        json.loads(line)
        for line in (passing["root"] / "provider_attempt_journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    passing_evidence = runner._read_accepted_response_evidence(
        passing["root"] / "accepted_response_evidence.jsonl"
    )
    passing_outputs = json.loads(
        (passing["root"] / "provider_outputs.json").read_text(encoding="utf-8")
    )["provider_outputs"]
    incomplete_output = next(row for row in passing_outputs if row["sequence"] == 2)
    blocked_output = next(row for row in passing_outputs if row["sequence"] == 3)
    lifecycle_events = [
        event["event"] for event in passing_events if event.get("sequence") == 2
    ]

    assert passing_summary["planned_questions"] == 92
    assert passing_summary["planned_method_cases"] == 184
    assert passing_summary["journal_protocol"]["integrity_valid"] is True
    assert passing_summary["journal_protocol"]["terminal_method_cases"] == 184
    assert passing_summary["provider_completion"]["provider_response_completed_method_cases"] == 184
    assert passing_summary["provider_completion"]["provider_error_method_cases"] == 0
    assert passing_summary["provider_completion"]["local_harness_block_method_cases"] == 1
    assert passing_summary["usage_completeness"]["incomplete_usage_accepted_responses"] == 1
    assert passing_summary["score_unlock_gate"]["status"] == "PASS"
    assert passing_summary["status"] == "COMPLETE_WITH_NOT_EVALUATED"
    assert passing_summary["gold_values_read"] is True
    assert len(passing["gold_calls"]) == 1
    assert passing["gold_calls"][0][1] == tuple(
        row["qa_id"] for row in passing["plan_rows"] if row["method"] == runner.METHODS[0]
    )
    assert passing_evidence["integrity_valid"] is True
    assert incomplete_output["provider_outcome"] == "PROVIDER_COMPLETE"
    assert incomplete_output["usage_status"] == "INCOMPLETE"
    assert incomplete_output["provider_output_tokens"] is None
    assert incomplete_output["billing_uncertainty"] is True
    assert incomplete_output["attempt_cost_upper_bound_usd"] > 0
    assert lifecycle_events.index("RESPONSE_RECEIVED") < lifecycle_events.index(
        "RESPONSE_EVIDENCE_PERSISTED"
    )
    assert lifecycle_events.index("RESPONSE_EVIDENCE_PERSISTED") < lifecycle_events.index(
        "USAGE_VALIDATED"
    )
    assert lifecycle_events.index("USAGE_VALIDATED") < lifecycle_events.index(
        "PROVIDER_ATTEMPT_COMPLETED"
    )
    assert blocked_output["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert blocked_output["provider_response_accepted"] is True
    assert blocked_output["reason_code"] == "CLEAN_EVAL_BLOCK_RESPONSE_GENERATION_RESPONSE_TEXT_MALFORMED"
    live_manifest = json.loads((passing["root"] / "run_manifest.json").read_text(encoding="utf-8"))
    assert live_manifest["subset_sha256"] == "e" * 64
    assert live_manifest["evaluation_plan_id"] == evaluation_plan
    assert live_manifest["runtime_config_fingerprint_sha256"] == "c" * 64

    failure_root = tmp_path / "gate-failure"
    failure_root.mkdir()
    with monkeypatch.context() as gate_failure_patch:
        gate_failure = _run_synthetic_clean_live(
            failure_root,
            gate_failure_patch,
            {
                1: ["malformed_response"],
                3: ["malformed_response"],
                5: ["malformed_response"],
                7: ["malformed_response"],
                9: ["malformed_response"],
            },
            cell_key="mh_6k",
            evaluation_plan_id=evaluation_plan,
        )
    failed_summary = gate_failure["result"]["summary"]
    assert failed_summary["score_unlock_gate"]["status"] == "FAIL"
    assert failed_summary["stop_reason"] == "SCORE_PROTOCOL_COVERAGE_UNREACHABLE"
    assert failed_summary["gold_values_read"] is False
    assert gate_failure["gold_calls"] == []
    assert gate_failure["scorer_calls"] == []


def test_e2_accepted_response_survives_safe_telemetry_guard_as_degraded_success(
    tmp_path, monkeypatch
) -> None:
    import json
    import scripts.run_public_memory_clean_eval as runner

    original_count_telemetry = runner._count_tokens_telemetry
    thrown = False

    def fail_once_with_optional_latency(*args, **kwargs):
        nonlocal thrown
        if not thrown and not kwargs.get("allow_failure", False):
            thrown = True
            raise runner.CleanEvalBlocked("OPTIONAL_LATENCY_MISSING")
        return original_count_telemetry(*args, **kwargs)

    monkeypatch.setattr(runner, "_count_tokens_telemetry", fail_once_with_optional_latency)
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {},
        cell_key="mh_6k",
        evaluation_plan_id=runner.E2_MH_ONLY_EVALUATION_PLAN_ID,
        score_with_synthetic_gold=True,
    )

    summary = replay["result"]["summary"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))[
        "provider_outputs"
    ]
    events = [
        json.loads(line)
        for line in (replay["root"] / "provider_attempt_journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    first = outputs[0]
    first_completion = next(
        event for event in events
        if event.get("sequence") == 1 and event.get("event") == "PROVIDER_ATTEMPT_COMPLETED"
    )

    assert thrown is True
    assert len(replay["calls"]) == 184
    assert first["provider_outcome"] == "PROVIDER_COMPLETE"
    assert first["provider_response_accepted"] is True
    assert first["response_sha256"]
    assert first_completion["provider_outcome"] == "PROVIDER_COMPLETE"
    assert first_completion["provider_response_accepted"] is True
    assert first_completion["degraded_telemetry_guard_reason_code"] == (
        "CLEAN_EVAL_BLOCK_OTHER_OPTIONAL_LATENCY_MISSING"
    )
    assert summary["journal_protocol"]["integrity_valid"] is True
    assert summary["score_unlock_gate"]["status"] == "PASS"
    assert summary["gold_values_read"] is True
    assert len(replay["gold_calls"]) == 1


def test_e2_incomplete_usage_with_unsafe_cost_bound_hard_stops_after_receipt(
    tmp_path, monkeypatch
) -> None:
    import json
    import scripts.run_public_memory_clean_eval as runner

    original_count_telemetry = runner._count_tokens_telemetry
    original_cost_validation = runner._diagnostic_cost_reservation_is_safe
    thrown = False

    def fail_once_with_optional_latency(*args, **kwargs):
        nonlocal thrown
        if not thrown and not kwargs.get("allow_failure", False):
            thrown = True
            raise runner.CleanEvalBlocked("OPTIONAL_LATENCY_MISSING")
        return original_count_telemetry(*args, **kwargs)

    def cost_is_unsafe_after_attempt_starts(attempt_start, plan_row, **kwargs):
        if isinstance(attempt_start, dict) and "generation_request_sha256" in attempt_start:
            return False
        return original_cost_validation(attempt_start, plan_row, **kwargs)

    monkeypatch.setattr(runner, "_count_tokens_telemetry", fail_once_with_optional_latency)
    monkeypatch.setattr(
        runner,
        "_diagnostic_cost_reservation_is_safe",
        cost_is_unsafe_after_attempt_starts,
    )
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["thinking_only"]},
        cell_key="mh_6k",
        evaluation_plan_id=runner.E2_MH_ONLY_EVALUATION_PLAN_ID,
    )

    summary = replay["result"]["summary"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))[
        "provider_outputs"
    ]
    evidence = runner._read_accepted_response_evidence(
        replay["root"] / "accepted_response_evidence.jsonl"
    )

    assert thrown is True
    assert len(replay["calls"]) == 1
    assert outputs[0]["provider_outcome"] == "LOCAL_HARNESS_BLOCK"
    assert outputs[0]["provider_response_accepted"] is True
    assert outputs[0]["provider_output_tokens"] is None
    assert outputs[0]["usage_status"] == "INCOMPLETE"
    assert evidence["integrity_valid"] is True
    assert evidence["rows"][0]["response_sha256"] == outputs[0]["response_sha256"]
    assert summary["stop_reason"] == "CLEAN_EVAL_BLOCK_OTHER_OPTIONAL_LATENCY_MISSING"
    assert summary["gold_values_read"] is False
    assert replay["gold_calls"] == []


def test_pre_case_failure_finalizes_partial_run_without_secondary_exception(tmp_path, monkeypatch) -> None:
    import json
    import scripts.run_public_memory_clean_eval as runner

    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {},
        cell_key="mh_6k",
        evaluation_plan_id=runner.E2_MH_ONLY_EVALUATION_PLAN_ID,
        rebuild_error="RUNTIME_CASE_IDENTITY_MISMATCH",
    )

    summary = replay["result"]["summary"]
    outputs = json.loads((replay["root"] / "provider_outputs.json").read_text(encoding="utf-8"))[
        "provider_outputs"
    ]

    assert replay["calls"] == []
    assert summary["stop_reason"] == "CLEAN_EVAL_BLOCK_PROTOCOL_RUNTIME_CASE_IDENTITY_MISMATCH"
    assert summary["harness_traceback"]["exception_type"] != "AttributeError"
    assert summary["harness_execution"]["started_method_cases"] == 0
    assert summary["provider_completion"]["not_run_method_cases"] == 184
    assert len(outputs) == 184
    assert all(row["provider_outcome"] == "NOT_RUN" for row in outputs)
    assert summary["gold_values_read"] is False
    assert summary["score_unlock_gate"]["status"] == "FAIL"


def test_dynamic_cost_bound_flows_to_diagnostic_summary_and_ledger(
    tmp_path, monkeypatch
) -> None:
    dynamic_bound = 0.5
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {1: ["thinking_only"]},
        diagnostic_mode=True,
        dynamic_spend_upper_bound_usd=dynamic_bound,
    )
    summary = replay["result"]["summary"]
    ledger_run = replay["ledger"]["runs"][-1]

    assert summary["cost"]["estimated_spend_upper_bound_usd"] >= dynamic_bound
    assert ledger_run["estimated_spend_upper_bound_usd"] >= dynamic_bound
    assert summary["cost"]["estimated_spend_upper_bound_usd"] == pytest.approx(
        ledger_run["estimated_spend_upper_bound_usd"]
    )
    assert summary["gold_reads"] == 0


def test_unavailable_conservative_cost_reservation_stops_before_fake_provider_call(
    tmp_path, monkeypatch
) -> None:
    import scripts.run_public_memory_clean_eval as runner

    monkeypatch.setattr(runner, "request_cost_upper_bound_usd", lambda _bound: 0.0)
    replay = _run_synthetic_clean_live(
        tmp_path,
        monkeypatch,
        {},
        diagnostic_mode=True,
    )

    assert replay["calls"] == []
    assert replay["result"]["summary"]["stop_reason"] == "DIAGNOSTIC_COST_RESERVATION_UNAVAILABLE"
    assert replay["result"]["summary"].get("provider_completion", {}).get("accepted_responses", 0) == 0
    assert replay["result"]["summary"]["gold_reads"] == 0


def test_incomplete_usage_is_case_local_only_with_valid_reserved_cost_bound() -> None:
    from scripts.run_public_memory_clean_eval import (
        _diagnostic_cost_reservation_is_safe,
        _diagnostic_guard_is_fatal,
    )

    plan = {
        "static_input_token_upper_bound": 100,
        "one_attempt_cost_upper_bound_usd": 0.02,
    }
    attempt_start = {
        "static_input_token_upper_bound": 100,
        "attempt_cost_upper_bound_reserved_usd": 0.02,
        "projected_cell_cost_upper_bound_usd": 5.0,
        "projected_global_cost_upper_bound_usd": 9.0,
    }
    evidence = {
        "provider_response_accepted": True,
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "logical_attempt_id": "1:1",
        "model": "gemini-test-model",
        "usage_present": True,
        "usage_status": "INCOMPLETE",
        "usage_metadata": {"input_tokens": 100, "output_tokens": None, "thinking_tokens": 7},
        "billing_uncertainty": True,
    }
    reservation_safe = _diagnostic_cost_reservation_is_safe(
        attempt_start,
        plan,
        run_hard_cost_cap=6.0,
        global_hard_cost_cap=10.0,
    )

    assert reservation_safe is True
    assert _diagnostic_guard_is_fatal(
        "CLEAN_EVAL_BLOCK_HARNESS_PROVIDER_USAGE_UNAVAILABLE",
        response_evidence=evidence,
        provider_response_accepted=True,
        journal_integrity_valid=True,
        cost_reservation_safe=reservation_safe,
    ) is False
    assert _diagnostic_guard_is_fatal(
        "CLEAN_EVAL_BLOCK_HARNESS_PROVIDER_USAGE_UNAVAILABLE",
        response_evidence=evidence,
        provider_response_accepted=True,
        journal_integrity_valid=True,
        cost_reservation_safe=False,
    ) is True
    assert _diagnostic_guard_is_fatal(
        "CLEAN_EVAL_BLOCK_LOCAL_UNKNOWN",
        response_evidence=evidence,
        provider_response_accepted=True,
        journal_integrity_valid=True,
        cost_reservation_safe=reservation_safe,
    ) is True
    assert _diagnostic_cost_reservation_is_safe(
        {**attempt_start, "projected_cell_cost_upper_bound_usd": 6.01},
        plan,
        run_hard_cost_cap=6.0,
        global_hard_cost_cap=10.0,
    ) is False


def test_receipt_only_journal_recovers_evidence_then_usage_validation(tmp_path) -> None:
    import json

    from scripts.run_public_memory_clean_eval import (
        _append_attempt_journal,
        _recover_response_receipt_stages,
        _read_accepted_response_evidence,
    )

    journal_path = tmp_path / "provider_attempt_journal.jsonl"
    evidence_path = tmp_path / "accepted_response_evidence.jsonl"
    journal_path.write_text("", encoding="utf-8")
    evidence_path.write_text("", encoding="utf-8")
    receipt = {
        "event": "RESPONSE_RECEIVED",
        "run_id": "recovery-run",
        "sequence": 1,
        "qa_id": "synthetic-qa-1",
        "method": "Flat Retrieval",
        "attempt_no": 1,
        "attempt_id": "1:1",
        "provider_response_accepted": True,
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "model": "gemini-test-model",
        "usage_present": True,
        "usage_status": "INCOMPLETE",
        "usage_metadata": {"input_tokens": 109, "output_tokens": None, "thinking_tokens": 157},
        "usage_sources": {
            "input_tokens": "prompt_token_count",
            "output_tokens": None,
            "thinking_tokens": "thoughts_token_count",
        },
        "provider_usage_object_present": True,
        "provider_usage_fields": {"prompt_token_count": 109, "thoughts_token_count": 157},
        "usage_invalid_fields": [],
        "billing_uncertainty": True,
        "billing_basis": "RESERVED_ATTEMPT_UPPER_BOUND",
        "provider_reported_cost_usd": None,
        "provider_reported_cost_source": None,
        "provider_request_id": "fake-response-1",
        "accepted_at_utc": "2026-10-04T00:00:00+00:00",
    }
    _append_attempt_journal(journal_path, receipt)
    events, evidence_summary, recovered, errors = _recover_response_receipt_stages(
        journal_path,
        evidence_path,
        [receipt],
        _read_accepted_response_evidence(evidence_path),
    )

    assert errors == []
    assert recovered == 1
    assert [event["event"] for event in events] == [
        "RESPONSE_RECEIVED",
        "RESPONSE_EVIDENCE_PERSISTED",
        "USAGE_VALIDATED",
    ]
    assert evidence_summary["integrity_valid"] is True
    assert evidence_summary["rows"][0]["usage_metadata"]["output_tokens"] is None
    persisted_lines = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert persisted_lines == events
