from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pyarrow = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: F401

from benchmarks.memoryagentbench.adapter import (
    BenchmarkQuestion,
    FactConsolidationCase,
    TARGET_SOURCE,
    parse_ordered_facts,
)
from benchmarks.memoryagentbench.g3_pilot import (
    METHOD_FLAT_RETRIEVAL,
    METHOD_TEMPORAL_MEMORY,
    MAX_GENERATION_CALLS,
    MAX_TOTAL_GENERATION_ATTEMPTS,
    MAX_TOTAL_BENCHMARK_COST_USD,
    build_generation_request,
    load_official_gold_answers,
    normalize_official_answer,
    prepare_method_context,
    provider_failure_limit_exceeded,
    score_official_substring_exact_match,
    static_worst_case_cost_usd,
    verify_frozen_output_artifact,
)
from benchmarks.memoryagentbench.memory import build_flat_index, build_temporal_memory
from scripts import run_memoryagentbench_g3_pilot as g3_runner


def _synthetic_case() -> FactConsolidationCase:
    context = (
        "Here is a list of facts:\n"
        "0. The chairperson of Example Org is Person A.\n"
        "1. The chairperson of Example Org is Person B."
    )
    return FactConsolidationCase(
        source=TARGET_SOURCE,
        case_id="synthetic:g3-case",
        workspace_id="memoryagentbench:synthetic:g3-case",
        context=context,
        facts=tuple(parse_ordered_facts(context)),
        questions=(
            BenchmarkQuestion("q0", "Who is the chairperson of Example Org?"),
        ),
    )


def test_temporal_method_uses_product_memory_and_reads_its_exact_source() -> None:
    case = _synthetic_case()
    flat_index = build_flat_index(case)
    memory = build_temporal_memory(case)
    try:
        flat = prepare_method_context(
            case,
            flat_index,
            case.questions[0],
            method=METHOD_FLAT_RETRIEVAL,
        )
        temporal = prepare_method_context(
            case,
            flat_index,
            case.questions[0],
            method=METHOD_TEMPORAL_MEMORY,
            memory=memory,
        )

        assert flat.memory_observation is None
        assert "Person A" in flat.context_bundle.rendered_text
        assert "Person B" in flat.context_bundle.rendered_text
        observation = temporal.memory_observation
        assert observation is not None
        assert observation["status"] == "CURRENT"
        assert observation["value"] == "Person B"
        assert observation["source_evidence_refs"] == ["fact:1"]
        assert observation["source_identity_verified"] is True
        assert "1. The chairperson of Example Org is Person B." in temporal.context_bundle.rendered_text
        assert "Person A" not in temporal.context_bundle.rendered_text
        assert temporal.context_bundle.workspace_id == case.workspace_id
        assert temporal.context_bundle.estimated_tokens <= 3_000
    finally:
        memory.store.close()


def test_temporal_memory_lookup_miss_is_a_scored_method_outcome_not_setup_crash() -> None:
    case = _synthetic_case()
    missed_question = BenchmarkQuestion(
        "q-memory-miss",
        "Who is the developer of SteamOS?",
    )
    case = FactConsolidationCase(
        source=case.source,
        case_id=case.case_id,
        workspace_id=case.workspace_id,
        context=case.context,
        facts=case.facts,
        questions=(missed_question,),
    )
    flat_index = build_flat_index(case)
    memory = build_temporal_memory(case)
    try:
        temporal = prepare_method_context(
            case,
            flat_index,
            missed_question,
            method=METHOD_TEMPORAL_MEMORY,
            memory=memory,
        )

        assert temporal.memory_observation is None
        assert temporal.retrieval_evidence_ids == ()
        assert not any(item.source_type.value == "evidence" for item in temporal.context_bundle.selected)
        scored = g3_runner._score_results(
            [{
                "question_id": missed_question.question_id,
                "method": METHOD_TEMPORAL_MEMORY,
                "provider_outcome": "PROVIDER_COMPLETE",
                "answer": "I don't know",
            }],
            {missed_question.question_id: ("Some Company",)},
            prepared=[temporal],
            case=case,
        )[0]
        assert scored["semantic_status"] == "FAIL"
        assert scored["failure_taxonomy"] == "MEMORY_LOOKUP_MISS"
    finally:
        memory.store.close()


def test_reader_requests_have_identical_frozen_configuration_and_no_gold() -> None:
    flat_request = build_generation_request("Question: who?\nEvidence A")
    memory_request = build_generation_request("Question: who?\nEvidence B")

    assert flat_request["model"] == memory_request["model"] == "gemini-3.8-flash"
    assert flat_request["config"] == memory_request["config"]
    assert flat_request["config"]["temperature"] == 0.0
    assert flat_request["config"]["max_output_tokens"] == 512
    assert flat_request["config"]["automatic_function_calling"] == {"disable": True}
    assert flat_request["config"]["system_instruction"] == memory_request["config"]["system_instruction"]
    assert "expected_answer" not in repr(flat_request).casefold()
    assert "gold" not in repr(flat_request).casefold()


def test_frozen_g3_count_tokens_wire_shape_uses_content_system_instruction() -> None:
    request = build_generation_request("Question: synthetic?\nEvidence: fact 1")

    count_tokens_shape = g3_runner.gemini._count_endpoint_generate_request(request)

    assert count_tokens_shape["systemInstruction"] == {
        "parts": [{"text": request["config"]["system_instruction"]}],
        "role": "user",
    }
    assert count_tokens_shape["contents"] == request["contents"]
    assert count_tokens_shape["model"] == "models/gemini-3.8-flash"


def test_official_substring_metric_normalizes_case_punctuation_and_articles() -> None:
    assert normalize_official_answer("The, Final Answer!") == "final answer"
    assert score_official_substring_exact_match("Answer: The Final Answer.", ["the final answer"])
    assert not score_official_substring_exact_match("Person C", ["Person B"])


def test_static_worst_case_budget_includes_count_and_generation_input() -> None:
    assert MAX_GENERATION_CALLS == 20
    assert MAX_TOTAL_GENERATION_ATTEMPTS == 30
    assert MAX_TOTAL_BENCHMARK_COST_USD == 0.50
    worst_case = static_worst_case_cost_usd()
    assert worst_case == pytest.approx(0.3384)
    assert worst_case <= MAX_TOTAL_BENCHMARK_COST_USD
    assert static_worst_case_cost_usd(generation_attempts=30) == pytest.approx(0.5076)


@pytest.mark.parametrize(
    ("failures", "stopped"),
    ((0, False), (1, False), (2, False), (3, True)),
)
def test_provider_failure_stop_is_strictly_above_ten_percent(
    failures: int,
    stopped: bool,
) -> None:
    assert provider_failure_limit_exceeded(failures) is stopped


def test_gold_loader_requires_a_sealed_provider_output_before_reading_gold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "provider_outputs.json"
    seal_path = tmp_path / "provider_outputs.seal.json"
    gold_reads: list[list[str]] = []

    class NeverReadGold:
        @staticmethod
        def read_table(*_args: object, **_kwargs: object) -> object:
            gold_reads.append(["called"])
            raise AssertionError("Gold must not be read before outputs are sealed")

    with pytest.raises(ValueError, match="sealed provider output"):
        load_official_gold_answers(
            tmp_path / "missing.parquet",
            ["q0"],
            provider_outputs_path=output_path,
            provider_outputs_seal_path=seal_path,
            parquet_reader=NeverReadGold,
        )
    assert gold_reads == []


def test_gold_loader_reads_only_official_answers_after_output_seal(
    tmp_path: Path,
) -> None:
    import hashlib
    import json

    output_path = tmp_path / "provider_outputs.json"
    output_path.write_text('{"results":[]}', encoding="utf-8")
    seal_path = tmp_path / "provider_outputs.seal.json"
    seal_path.write_text(
        json.dumps({"sealed": True, "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    read_columns: list[list[str]] = []

    class Table:
        @staticmethod
        def to_pylist() -> list[dict[str, object]]:
            return [{
                "source": TARGET_SOURCE,
                "qa_pair_ids": ["q0", "q1"],
                "answers": [["Gold A", "Gold A variant"], ["Gold B"]],
            }]

    class Reader:
        @staticmethod
        def read_table(_path: object, *, columns: list[str]) -> Table:
            read_columns.append(columns)
            return Table()

    answers = load_official_gold_answers(
        tmp_path / "dataset.parquet",
        ["q0"],
        provider_outputs_path=output_path,
        provider_outputs_seal_path=seal_path,
        parquet_reader=Reader,
    )
    assert read_columns == [["answers", "metadata.qa_pair_ids", "metadata.source"]]
    assert answers == {"q0": ("Gold A", "Gold A variant")}


def test_frozen_output_verification_detects_post_seal_mutation(tmp_path: Path) -> None:
    import hashlib
    import json

    output_path = tmp_path / "provider_outputs.json"
    output_path.write_text('{"results":[]}', encoding="utf-8")
    seal_path = tmp_path / "provider_outputs.seal.json"
    seal_path.write_text(
        json.dumps({"sealed": True, "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    assert verify_frozen_output_artifact(output_path, seal_path)
    output_path.write_text('{"results":[1]}', encoding="utf-8")
    assert not verify_frozen_output_artifact(output_path, seal_path)


def test_summary_retry_count_comes_from_explicit_harness_counter() -> None:
    # AggregateUsage intentionally has no automatic_retry_count attribute.
    aggregate = SimpleNamespace(
        provider_requests=0,
        preflight_count_requests=0,
        inference_transport_attempts=0,
        preflight_counted_input_tokens=0,
        reported_input_tokens=0,
        reported_output_tokens=0,
        reported_billable_output_tokens=0,
        estimated_input_tokens=0,
        reported_cost_usd=None,
        preflight_records=[],
    )
    summary = g3_runner._summarize(
        [],
        aggregate,
        qualification={"offline": True},
        retry_count=0,
        execution_status="COMPLETE",
    )
    assert summary["retries"] == 0
    assert summary["pilot_status"] == "PILOT_UNSTABLE"


def test_cost_cap_preflight_blocks_before_any_provider_network_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _synthetic_case()
    prepared = prepare_method_context(
        case,
        build_flat_index(case),
        case.questions[0],
        method=METHOD_FLAT_RETRIEVAL,
    )
    aggregate = g3_runner.gemini.AggregateUsage()
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )

    def forbidden_provider_action(**_kwargs: object) -> None:
        pytest.fail("cost guard must block before countTokens or generation")

    monkeypatch.setattr(
        g3_runner,
        "_dynamic_cost_upper_bound",
        lambda *_args, **_kwargs: MAX_TOTAL_BENCHMARK_COST_USD + 0.01,
    )
    result = g3_runner._execute_generation_attempt(
        prepared,
        build_generation_request(prepared.context_bundle.rendered_text),
        sequence=1,
        attempt_number=1,
        sdk_client=SimpleNamespace(
            count_tokens=forbidden_provider_action,
            generate_content=forbidden_provider_action,
        ),
        journal=journal,
        aggregate=aggregate,
        count_tokens_fallback_state=g3_runner.gemini.CountTokensFallbackState(),
        api_key="test-secret",
        failed_count_token_input_reserve=0,
    )

    assert result["provider_outcome"] == "COST_CAP_REACHED"
    assert aggregate.preflight_count_requests == 0
    assert aggregate.provider_requests == 0
    assert journal.read_events() == []


def test_reported_provider_cost_is_also_enforced_by_dynamic_cost_bound() -> None:
    aggregate = SimpleNamespace(
        preflight_counted_input_tokens=0,
        estimated_input_tokens=0,
        reported_billable_output_tokens=0,
        reported_cost_usd=0.51,
        preflight_records=[],
    )

    assert g3_runner._dynamic_cost_upper_bound(
        aggregate,
        failed_count_token_input_reserve=0,
        generation_attempt_count=0,
        current_request_reserved=False,
    ) == pytest.approx(0.51)


def test_dynamic_cost_bound_includes_static_fallback_reserve_even_if_counter_is_zero() -> None:
    aggregate = SimpleNamespace(
        preflight_counted_input_tokens=0,
        estimated_input_tokens=0,
        reported_billable_output_tokens=0,
        reported_cost_usd=None,
        preflight_records=[{
            "status": "static_fallback",
            "token_estimation_source": "STATIC_CONSERVATIVE",
            "estimated_input_token_upper_bound": 8_000,
        }],
    )

    actual = g3_runner._dynamic_cost_upper_bound(
        aggregate,
        failed_count_token_input_reserve=0,
        generation_attempt_count=0,
        current_request_reserved=False,
    )
    expected_minimum = g3_runner.gemini._combined_cost_usd(8_000, 8_000, 0)

    assert actual >= expected_minimum


def test_dynamic_cost_bound_adds_exact_and_static_fallback_ledgers() -> None:
    aggregate = SimpleNamespace(
        preflight_counted_input_tokens=10_000,
        estimated_input_tokens=10_000,
        reported_billable_output_tokens=0,
        reported_cost_usd=None,
        preflight_records=[
            {
                "status": "success",
                "token_estimation_source": "PROVIDER_COUNT_TOKENS",
                "counted_input_tokens": 10_000,
            },
            {
                "status": "static_fallback",
                "token_estimation_source": "STATIC_CONSERVATIVE",
                "estimated_input_token_upper_bound": 8_000,
            },
        ],
    )

    actual = g3_runner._dynamic_cost_upper_bound(
        aggregate,
        failed_count_token_input_reserve=0,
        generation_attempt_count=0,
        current_request_reserved=False,
    )
    expected = g3_runner.gemini._combined_cost_usd(18_000, 18_000, 0)

    assert actual == pytest.approx(expected)


def test_dynamic_cost_bound_reserves_unresolved_count_tokens_input() -> None:
    aggregate = SimpleNamespace(
        preflight_counted_input_tokens=0,
        estimated_input_tokens=0,
        reported_billable_output_tokens=0,
        reported_cost_usd=None,
        preflight_records=[{
            "status": "failed",
            "count_tokens_status": "PENDING",
            "transport_attempts": 1,
        }],
    )

    actual = g3_runner._dynamic_cost_upper_bound(
        aggregate,
        failed_count_token_input_reserve=0,
        generation_attempt_count=0,
        current_request_reserved=False,
    )

    assert actual >= g3_runner.gemini._combined_cost_usd(
        0,
        g3_runner.PER_REQUEST_INPUT_TOKEN_CAP,
        0,
    )


def test_provider_budget_cost_guard_is_classified_as_cost_cap_not_harness_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _synthetic_case()
    prepared = prepare_method_context(
        case,
        build_flat_index(case),
        case.questions[0],
        method=METHOD_FLAT_RETRIEVAL,
    )
    aggregate = g3_runner.gemini.AggregateUsage()
    aggregate.reported_cost_usd = MAX_TOTAL_BENCHMARK_COST_USD
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )

    def forbidden_provider_action(**_kwargs: object) -> None:
        pytest.fail("provider budget must block before countTokens or generation")

    monkeypatch.setattr(g3_runner, "_dynamic_cost_upper_bound", lambda *_args, **_kwargs: 0.0)
    result = g3_runner._execute_generation_attempt(
        prepared,
        build_generation_request(prepared.context_bundle.rendered_text),
        sequence=1,
        attempt_number=1,
        sdk_client=SimpleNamespace(
            count_tokens=forbidden_provider_action,
            generate_content=forbidden_provider_action,
            assert_developer_api=lambda: None,
        ),
        journal=journal,
        aggregate=aggregate,
        count_tokens_fallback_state=g3_runner.gemini.CountTokensFallbackState(),
        api_key="test-secret",
        failed_count_token_input_reserve=0,
    )

    assert result["provider_outcome"] == "COST_CAP_REACHED"
    assert aggregate.preflight_count_requests == 0
    assert aggregate.provider_requests == 0
    assert journal.read_events() == []


def test_interrupted_run_sealer_records_unknown_calls_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    interrupted = artifact_root / "g3-first10-test"
    interrupted.mkdir(parents=True)
    monkeypatch.setattr(g3_runner, "ARTIFACTS_ROOT", artifact_root)
    g3_runner._write_json(interrupted / "run_manifest.json", {
        "stage": "G3_FIRST_TEN_PILOT",
        "gold_loaded": False,
        "qa_ids": ["factconsolidation_sh_6k_no0"],
    })
    g3_runner._write_json(interrupted / "qualification.json", {"status": "PASS"})
    g3_runner._write_json(interrupted / "prepared_context_01.json", {
        "question_id": "factconsolidation_sh_6k_no0",
        "method": METHOD_FLAT_RETRIEVAL,
    })

    sealed_root = g3_runner.seal_interrupted_run(interrupted)
    incident = json.loads((sealed_root / "execution_interruption.json").read_text(encoding="utf-8"))
    manifest = json.loads((sealed_root / "artifact_manifest.json").read_text(encoding="utf-8"))
    seal = json.loads((sealed_root / "artifact_manifest.seal.json").read_text(encoding="utf-8"))

    assert incident["request_telemetry"]["count_tokens_request_count"] == "NOT_RECOVERABLE_FROM_ARTIFACTS"
    assert incident["request_telemetry"]["generation_request_count"] == "NOT_RECOVERABLE_FROM_ARTIFACTS"
    assert incident["scope"]["gold_loaded"] is False
    interruption_entry = next(item for item in manifest["artifacts"] if item["path"] == "execution_interruption.json")
    assert interruption_entry["sha256"] == sha256((sealed_root / "execution_interruption.json").read_bytes()).hexdigest()
    assert seal["artifact_manifest_sha256"] == sha256((sealed_root / "artifact_manifest.json").read_bytes()).hexdigest()


def test_g3_execution_plan_interleaves_methods_for_each_frozen_question() -> None:
    qa_ids = [f"factconsolidation_sh_6k_no{index}" for index in range(10)]

    plan = g3_runner.build_execution_order(qa_ids)

    assert len(plan) == 20
    assert [(item["qa_id"], item["method"]) for item in plan[:4]] == [
        (qa_ids[0], METHOD_FLAT_RETRIEVAL),
        (qa_ids[0], METHOD_TEMPORAL_MEMORY),
        (qa_ids[1], METHOD_FLAT_RETRIEVAL),
        (qa_ids[1], METHOD_TEMPORAL_MEMORY),
    ]
    assert [item["sequence"] for item in plan] == list(range(1, 21))


def test_fixed50_profile_scales_only_run_bounds_and_preserves_method_order() -> None:
    try:
        g3_runner._activate_profile(g3_runner.G31_FIXED50_PROFILE)
        qa_ids = [f"factconsolidation_sh_6k_no{index}" for index in range(50)]
        plan = g3_runner.build_execution_order(qa_ids)

        assert g3_runner.MAX_QUESTIONS == 50
        assert g3_runner.MAX_GENERATION_CALLS == 100
        assert g3_runner.MAX_TOTAL_GENERATION_ATTEMPTS == 300
        assert g3_runner.MAX_TOTAL_BENCHMARK_COST_USD == 1.00
        assert [(item["qa_id"], item["method"]) for item in plan[:2]] == [
            (qa_ids[0], METHOD_FLAT_RETRIEVAL),
            (qa_ids[0], METHOD_TEMPORAL_MEMORY),
        ]
        assert g3_runner.provider_failure_limit_exceeded(10, planned_calls=100) is False
        assert g3_runner.provider_failure_limit_exceeded(11, planned_calls=100) is True
        assert g3_runner._profile_static_cost(generation_attempts=1, count_tokens_requests=1) < 1.00
    finally:
        g3_runner._activate_profile(g3_runner.G3_FIRST10_PROFILE)


@pytest.mark.parametrize(
    ("diagnostic", "retryable"),
    [
        ({"low_level_failure_class": "TRANSPORT_DNS"}, True),
        ({"low_level_failure_class": "TRANSPORT_CONNECT"}, True),
        ({"low_level_failure_class": "TRANSPORT_PROXY_CONNECT"}, True),
        ({"low_level_failure_class": "TRANSPORT_TLS"}, True),
        ({"low_level_failure_class": "TRANSPORT_TIMEOUT"}, True),
        ({"low_level_failure_class": "PROVIDER_OVERLOADED"}, True),
        ({"http_status": 408}, True),
        ({"http_status": 429}, True),
        ({"http_status": 500}, True),
        ({"http_status": 502}, True),
        ({"http_status": 503}, True),
        ({"http_status": 504}, True),
        ({"http_status": 400, "low_level_failure_class": "HTTP_4XX"}, False),
        ({"http_status": 401, "low_level_failure_class": "HTTP_4XX"}, False),
        ({"http_status": 403, "low_level_failure_class": "HTTP_4XX"}, False),
        ({"http_status": 501, "low_level_failure_class": "HTTP_5XX"}, False),
        ({"low_level_failure_class": "PROVIDER_SAFETY_BLOCK"}, False),
        ({"low_level_failure_class": "UNKNOWN_PROVIDER_FAILURE"}, False),
        (None, False),
    ],
)
def test_generation_retry_policy_retries_only_explicit_transient_failures(
    diagnostic: dict[str, object] | None,
    retryable: bool,
) -> None:
    assert g3_runner.is_retryable_generation_failure(diagnostic) is retryable


def test_retry_backoff_is_exponential_with_bounded_jitter() -> None:
    assert g3_runner.retry_delay_seconds(retry_level=1, jitter_sample=0.0) == 0.5
    assert g3_runner.retry_delay_seconds(retry_level=2, jitter_sample=0.0) == 1.0
    assert g3_runner.retry_delay_seconds(retry_level=1, jitter_sample=1.0) == 0.75


def test_bounded_generation_retry_stops_after_success_or_three_attempts() -> None:
    sent: list[int] = []
    delays: list[float] = []

    def attempt(number: int) -> dict[str, object]:
        sent.append(number)
        if number < 3:
            return {
                "provider_outcome": "PROVIDER_ERROR",
                "failure": {"low_level_failure_class": "TRANSPORT_CONNECT"},
            }
        return {"provider_outcome": "PROVIDER_COMPLETE"}

    results = g3_runner.run_bounded_generation_attempts(
        attempt,
        sleep=delays.append,
        random_sample=lambda: 0.0,
    )

    assert sent == [1, 2, 3]
    assert delays == [0.5, 1.0]
    assert [item["provider_outcome"] for item in results] == [
        "PROVIDER_ERROR",
        "PROVIDER_ERROR",
        "PROVIDER_COMPLETE",
    ]


def test_bounded_generation_retry_never_retries_non_transient_errors() -> None:
    sent: list[int] = []
    results = g3_runner.run_bounded_generation_attempts(
        lambda number: sent.append(number) or {
            "provider_outcome": "PROVIDER_ERROR",
            "failure": {"http_status": 400, "low_level_failure_class": "HTTP_4XX"},
        },
        sleep=lambda _delay: pytest.fail("HTTP 400 must not retry"),
        random_sample=lambda: 0.0,
    )

    assert sent == [1]
    assert len(results) == 1


def test_attempt_journal_marks_started_without_completed_as_unknown(
    tmp_path: Path,
) -> None:
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )
    shared = {
        "qa_id": "factconsolidation_sh_6k_no0",
        "method": METHOD_FLAT_RETRIEVAL,
        "logical_turn": 1,
        "attempt_number": 1,
        "retry_level": 0,
    }

    journal.provider_attempt_started(operation="count_tokens", **shared)
    journal.provider_attempt_completed(operation="count_tokens", status="SUCCESS", **shared)
    journal.provider_attempt_started(operation="generate_content", **{
        **shared,
        "attempt_number": 2,
        "retry_level": 1,
    })

    events = journal.read_events()
    assert events[0]["event"] == "PROVIDER_ATTEMPT_STARTED"
    assert events[0]["timestamp"]
    assert events[0]["run_id"] == "fresh-run"
    assert events[0]["provider"] == "Gemini Developer API"
    assert events[0]["model"] == "gemini-3.8-flash"
    assert [item["attempt_number"] for item in journal.unknown_provider_outcomes()] == [2]
    recorded = journal.record_unknown_provider_outcomes()
    assert [item["event"] for item in journal.read_events()][-1] == "PROVIDER_OUTCOME_UNKNOWN"
    assert len(recorded) == 1
    assert g3_runner._finalize_unknown_provider_outcome_count(journal) == 1


def test_journaled_gemini_delegate_persists_start_before_each_network_call(
    tmp_path: Path,
) -> None:
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )
    calls: list[str] = []

    class FakeDelegate:
        vertexai = False
        api_endpoint = "https://generativelanguage.googleapis.com/"

        def assert_developer_api(self) -> None:
            return None

        def count_tokens(self, **_kwargs: object) -> dict[str, int]:
            assert journal.read_events()[-1]["event"] == "PROVIDER_ATTEMPT_STARTED"
            assert journal.read_events()[-1]["operation"] == "countTokens"
            calls.append("countTokens")
            return {"total_tokens": 7}

        def generate_content(self, **_kwargs: object) -> dict[str, object]:
            assert journal.read_events()[-1]["event"] == "PROVIDER_ATTEMPT_STARTED"
            assert journal.read_events()[-1]["operation"] == "generateContent"
            calls.append("generateContent")
            return {"text": "answer", "usage_metadata": {"prompt_token_count": 7}}

    delegate = g3_runner.JournaledGeminiDelegate(
        FakeDelegate(),
        journal=journal,
        qa_id="factconsolidation_sh_6k_no0",
        method=METHOD_FLAT_RETRIEVAL,
        logical_turn=1,
        attempt_number=1,
        retry_level=0,
        secret="test-secret",
    )
    delegate.count_tokens(model="gemini-3.8-flash", request={"model": "gemini-3.8-flash"})
    delegate.generate_content(model="gemini-3.8-flash", contents=[], config={})

    assert calls == ["countTokens", "generateContent"]
    events = journal.read_events()
    assert [event["event"] for event in events] == [
        "PROVIDER_ATTEMPT_STARTED",
        "PROVIDER_ATTEMPT_COMPLETED",
        "PROVIDER_ATTEMPT_STARTED",
        "PROVIDER_ATTEMPT_COMPLETED",
    ]
    assert "answer" not in json.dumps(events)


def test_generation_exception_diagnostic_keeps_generation_failure_layer(
    tmp_path: Path,
) -> None:
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )

    class GenerationFailure(RuntimeError):
        diagnostic = {
            "high_level_outcome": "MODEL_TRANSIENT_FAILURE",
            "low_level_failure_class": "PROVIDER_OVERLOADED",
            "failure_layer": "HTTP",
            "http_status": 503,
            "provider_error_code": "UNAVAILABLE",
            "provider_error_message_safe": "temporarily unavailable",
        }

    class FakeDelegate:
        def generate_content(self, **_kwargs: object) -> object:
            raise GenerationFailure("temporarily unavailable")

    delegate = g3_runner.JournaledGeminiDelegate(
        FakeDelegate(),
        journal=journal,
        qa_id="factconsolidation_sh_6k_no0",
        method=METHOD_FLAT_RETRIEVAL,
        logical_turn=1,
        attempt_number=1,
        retry_level=0,
        secret="test-secret",
    )

    with pytest.raises(GenerationFailure):
        delegate.generate_content(model="gemini-3.8-flash", contents=[], config={})

    completed = journal.read_events()[-1]
    assert completed["event"] == "PROVIDER_ATTEMPT_COMPLETED"
    assert completed["operation"] == "generateContent"
    assert completed["high_level_outcome"] == "MODEL_TRANSIENT_FAILURE"
    assert completed["low_level_failure_class"] == "PROVIDER_OVERLOADED"
    assert completed["failure_layer"] == "HTTP"
    assert completed["http_status"] == 503


def test_post_response_guard_failure_is_harness_interruption_not_semantic_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _synthetic_case()
    prepared = prepare_method_context(
        case,
        build_flat_index(case),
        case.questions[0],
        method=METHOD_FLAT_RETRIEVAL,
    )
    response = {
        "text": "Person B",
        "response_id": "synthetic-response",
        "candidates": [{"finish_reason": "STOP"}],
    }
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )

    class FakeGuard:
        def __init__(self, delegate: object, **_kwargs: object) -> None:
            self.provider_calls = 1
            self.preflight_records: list[dict[str, object]] = []
            self.provider_elapsed_seconds = 0.01
            self.reported_input_tokens = 0
            self.reported_output_tokens = 0
            self.reported_cost_usd = None
            self.reported_count_cost_usd = None
            self.inference_transport_attempts = 1
            self.automatic_retry_count = 0
            setattr(delegate, "last_response", response)

        def generate_content(self, **_kwargs: object) -> object:
            raise g3_runner.gemini.SmokeBlocked("POST_RESPONSE_GUARD_BLOCKED")

    monkeypatch.setattr(g3_runner.gemini, "BudgetedGeminiClient", FakeGuard)
    result = g3_runner._execute_generation_attempt(
        prepared,
        build_generation_request(prepared.context_bundle.rendered_text),
        sequence=1,
        attempt_number=1,
        sdk_client=SimpleNamespace(),
        journal=journal,
        aggregate=g3_runner.gemini.AggregateUsage(),
        count_tokens_fallback_state=g3_runner.gemini.CountTokensFallbackState(),
        api_key="test-secret",
        failed_count_token_input_reserve=0,
    )

    assert result["provider_outcome"] == "HARNESS_INTERRUPTED"
    assert result["answer"] == "Person B"
    assert result["post_response_guard_failure"]["failure_layer"] == "POST_RESPONSE_GUARD"
    assert journal.read_events() == []
    scored = g3_runner._score_results(
        [{
            "question_id": case.questions[0].question_id,
            "method": METHOD_FLAT_RETRIEVAL,
            **result,
        }],
        {case.questions[0].question_id: ("Person B",)},
        prepared=[prepared],
        case=case,
    )[0]
    assert scored["semantic_status"] == "NOT_EVALUATED"
    assert scored["score"] is None
    assert scored["gold_read"] is False


@pytest.mark.parametrize("interrupt_after_start", [False, True], ids=["scoring-error", "provider-interrupted"])
def test_run_live_persists_failure_and_stops_before_next_method_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_start: bool,
) -> None:
    qa_ids = [f"factconsolidation_sh_6k_no{index}" for index in range(10)]
    case = SimpleNamespace(
        case_id="synthetic:g3-live",
        workspace_id="memoryagentbench:synthetic:g3-live",
        questions=tuple(BenchmarkQuestion(qa_id, f"Question {index}") for index, qa_id in enumerate(qa_ids)),
        facts=(),
    )
    prepared = [
        SimpleNamespace(
            question_id=str(item["qa_id"]),
            method=str(item["method"]),
            context_bundle=SimpleNamespace(rendered_text=f"Question {item['qa_id']} evidence"),
            to_artifact=lambda qa_id=item["qa_id"], method=item["method"]: {
                "question_id": qa_id,
                "method": method,
                "gold_read": False,
            },
        )
        for item in g3_runner.build_execution_order(qa_ids)
    ]

    class FakeStore:
        def close(self) -> None:
            return None

    qualification = {"offline_qualification": True}
    monkeypatch.setattr(
        g3_runner,
        "prepare_all_contexts",
        lambda _dataset: (case, object(), prepared, qualification, SimpleNamespace(store=FakeStore())),
    )
    monkeypatch.setattr(g3_runner, "ARTIFACTS_ROOT", tmp_path.parent / "a")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")

    class FakeSDKClient:
        @staticmethod
        def close() -> None:
            return None

    class FakeGuard:
        def __init__(self) -> None:
            self.provider_elapsed_seconds = 0.01
            self.preflight_elapsed_seconds = 0.0
            self.preflight_records: list[dict[str, object]] = []
            self.preflight_transport_attempts = 0
            self.reported_input_tokens = 10
            self.reported_output_tokens = 5
            self.inference_transport_attempts = 1
            self.automatic_retry_count = 0
            self.reported_cost_usd = None
            self.reported_count_cost_usd = None

    monkeypatch.setattr(g3_runner.gemini, "BudgetedGeminiClient", FakeGuard)
    monkeypatch.setattr(g3_runner.gemini, "build_official_client", lambda *_args, **_kwargs: FakeSDKClient())
    generation_sequences: list[int] = []

    def fake_execute(
        _prepared: object,
        _request: dict[str, object],
        *,
        sequence: int,
        attempt_number: int,
        aggregate: object,
        journal: g3_runner.DurableAttemptJournal,
        **_kwargs: object,
    ) -> dict[str, object]:
        generation_sequences.append(sequence)
        if interrupt_after_start:
            journal.provider_attempt_started(
                qa_id=prepared[0].question_id,
                method=prepared[0].method,
                operation="generateContent",
                logical_turn=1,
                attempt_number=attempt_number,
                retry_level=attempt_number - 1,
            )
            raise KeyboardInterrupt()
        aggregate.provider_requests += 1
        aggregate.estimated_input_tokens += 10
        aggregate.reported_input_tokens = (aggregate.reported_input_tokens or 0) + 10
        aggregate.reported_output_tokens = (aggregate.reported_output_tokens or 0) + 5
        return {
            "provider_outcome": "PROVIDER_COMPLETE",
            "answer": "A synthetic answer",
            "guard": FakeGuard(),
            "generation_attempted": True,
            "attempt_number": attempt_number,
            "response_id": "synthetic-response",
            "finish_reason": "STOP",
            "failure": None,
            "post_response_guard_failure": None,
        }

    monkeypatch.setattr(g3_runner, "_execute_generation_attempt", fake_execute)
    if interrupt_after_start:
        monkeypatch.setattr(
            g3_runner,
            "load_official_gold_answers",
            lambda *_args, **_kwargs: pytest.fail("Gold must not be loaded after an interrupted Provider attempt"),
        )
    else:
        monkeypatch.setattr(
            g3_runner,
            "load_official_gold_answers",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic scoring corruption")),
        )

    root = g3_runner.run_live(Path("synthetic-dataset"), {
        "preflight_status": "READY",
        "subset_manifest": {
            "question_count": 10,
            "qa_ids": qa_ids,
            "selection": "official QA order; first 10 only",
        },
        "qa_ids": qa_ids,
        "subset_manifest_sha256": "synthetic-manifest-hash",
    })

    summary = json.loads((root / "pilot_summary.json").read_text(encoding="utf-8"))
    scored = json.loads((root / "scored_results.json").read_text(encoding="utf-8"))
    assert generation_sequences == [1]
    assert summary["pilot_execution_status"] == "HARNESS_INTERRUPTED"
    assert summary["scoring_verified"] is False
    if interrupt_after_start:
        journal_events = [
            json.loads(line)
            for line in (root / "provider_attempt_journal.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert summary["execution_blocker"] == "HARNESS_INTERRUPTED_BY_USER"
        assert summary["unknown_provider_outcomes"] == 1
        assert any(event["event"] == "PROVIDER_OUTCOME_UNKNOWN" for event in journal_events)
        assert (root / "provider_outputs.seal.json").exists()
        assert scored["results"] == []
    else:
        assert summary["execution_blocker"] == "POSTHOC_SCORING_FAILED"
        assert scored["results"][0]["semantic_status"] == "NOT_EVALUATED"
        assert scored["results"][0]["gold_read"] is False


def test_case_method_lifecycle_is_persisted_as_individual_events(tmp_path: Path) -> None:
    journal = g3_runner.DurableAttemptJournal(
        tmp_path / "attempt_journal.jsonl",
        run_id="fresh-run",
    )

    journal.case_method_event("CASE_METHOD_STARTED", sequence=1, qa_id="q0", method=METHOD_FLAT_RETRIEVAL)
    journal.case_method_event("CASE_METHOD_COMPLETED", sequence=1, qa_id="q0", method=METHOD_FLAT_RETRIEVAL)
    journal.case_method_event("CASE_METHOD_SEALED", sequence=1, qa_id="q0", method=METHOD_FLAT_RETRIEVAL)

    assert [event["event"] for event in journal.read_events()] == [
        "CASE_METHOD_STARTED",
        "CASE_METHOD_COMPLETED",
        "CASE_METHOD_SEALED",
    ]


def test_each_method_case_output_is_persisted_and_sealed_before_scoring(
    tmp_path: Path,
) -> None:
    output = {
        "question_id": "factconsolidation_sh_6k_no0",
        "method": METHOD_FLAT_RETRIEVAL,
        "provider_outcome": "PROVIDER_COMPLETE",
        "answer": "Person B",
        "gold_read": False,
    }

    output_path, seal_path = g3_runner.persist_method_case_output(
        tmp_path,
        sequence=1,
        result=output,
    )

    assert verify_frozen_output_artifact(output_path, seal_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["gold_loaded"] is False
    assert payload["results"] == [output]
    assert "expected_answers" not in output_path.read_text(encoding="utf-8")
