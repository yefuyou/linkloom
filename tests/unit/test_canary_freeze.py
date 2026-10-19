from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.timestamped_text import ParseStatus, parse_timestamped_text
from linkloom.ui.canary_spec import (
    CANARY_CAMPAIGN_ID,
    CANARY_MODEL_ID,
    EXPECTED_FREEZE_MANIFEST_SHA256,
    CanarySpecError,
    load_spec,
)


SPEC_DIR = Path(__file__).parents[2] / "docs" / "requirements" / "m3-l-live-canary" / "canary-v1"
HISTORICAL_SPEC_DIR = SPEC_DIR
SPEC_DIR = SPEC_DIR.with_name("canary-v2")


def _artifact(source_id: str, text: str) -> RawArtifact:
    return RawArtifact(
        workspace_id="m3-l-canary-test",
        artifact_id=f"canary-{source_id}",
        source_type="timestamped_text",
        content=text,
    )


def test_frozen_spec_exposes_common_question_sources_case_config_and_digest() -> None:
    spec = load_spec()

    assert spec.campaign_id == CANARY_CAMPAIGN_ID == "m3-product-canary-v1"
    assert spec.model_id == CANARY_MODEL_ID == "gemini-3.8-flash"
    assert spec["question"] == "请调查这些会议中的任务是否延续，以及截止安排发生了什么变化；有哪些结论仍需确认？"
    assert set(spec.sources) == {"A", "B", "C", "D"}
    assert set(spec.cases) == {"C1", "C2", "C3"}
    assert spec["sources"] is spec.sources
    assert spec["cases"] is spec.cases
    assert spec["digest"] == spec.digest == EXPECTED_FREEZE_MANIFEST_SHA256
    assert len(spec.digest) == 64

    assert tuple(spec.cases["C1"]["sources"]) == ("A", "B")
    assert tuple(spec.cases["C2"]["sources"]) == ("A", "C")
    assert tuple(spec.cases["C3"]["sources"]) == ("A", "C", "D")


def test_frozen_budget_is_exact_and_case_totals_sum_to_global_cap() -> None:
    spec = load_spec()

    assert spec.config["max_requests"] == 34
    assert spec.config["max_cost_usd"] == 3
    assert spec.config["provider_id"] == "gemini"
    assert spec.config["campaign_total_seconds"] == 1800
    assert spec.config["case_total_seconds"] == 600
    assert spec.config["request_timeout_seconds"] == 130
    assert spec.config["sdk_attempts"] == 1
    assert spec.config["automatic_function_calling"] is False
    assert spec.config["thinking_level"] == "LOW"
    assert spec.config["max_input_bytes"] == 49152
    assert spec.config["per_request"] == {
        "max_input_tokens": 65536,
        "max_output_tokens": 8192,
    }
    assert spec.config["stage_requests"] == {
        "F": {"C1": 2, "C2": 2, "C3": 3},
        "G": {"C1": 2, "C2": 2, "C3": 2},
        "Semantic": {"C1": 1, "C2": 1, "C3": 1},
        "Runtime": {"C1": 6, "C2": 6, "C3": 6},
    }
    assert sum(case["max_requests"] for case in spec.cases.values()) == 34


def test_match_sources_requires_exact_question_and_historical_bytes() -> None:
    spec = load_spec()

    case, source_ids = spec.match_sources(spec.question, {"A": spec.sources["A"], "C": spec.sources["C"]})
    assert case.case_id == "C2"
    assert source_ids == ("A", "C")
    assert spec.match_sources(spec.question, (spec.sources["A"], spec.sources["B"]))[0].case_id == "C1"

    with pytest.raises(CanarySpecError, match="frozen common question"):
        spec.match_sources("不同问题", ("A", "B"))
    with pytest.raises(CanarySpecError, match="changed historical bytes"):
        spec.match_sources(spec.question, {"A": spec.sources["A"].replace("2026-10-20", "2026-10-21")})


@pytest.mark.parametrize(
    ("source_id", "expected_speakers", "expected_bodies"),
    [
        ("A", ("林岚",), ("青竹项目的日志迁移由我负责，截止日期为2026-10-20。",)),
        ("B", ("林岚",), ("继续1日那项日志迁移，仍由我负责，截止日期不变。",)),
        ("C", ("林岚",), ("继续1日那项日志迁移，仍由我负责；截止改为2026-10-24，替代2026-10-20。",)),
        (
            "D",
            ("周宁", "林岚", "记录员"),
            (
                "3日提到的是陈默的另一项日志迁移，我仍按2026-10-20安排。",
                "不同意，那是我1日的任务，截止已改为2026-10-24。",
                "双方对任务归属和有效截止日期尚未达成一致。",
            ),
        ),
    ],
)
def test_sources_use_existing_timestamped_parser_and_verify_exact_offsets(
    source_id: str,
    expected_speakers: tuple[str, ...],
    expected_bodies: tuple[str, ...],
) -> None:
    spec = load_spec()
    source = _artifact(source_id, spec.sources[source_id])

    parsed = parse_timestamped_text(source)

    assert parsed.status is ParseStatus.COMPLETE
    assert tuple(segment.speaker for segment in parsed.segments) == expected_speakers
    assert tuple(segment.body_text for segment in parsed.segments) == expected_bodies
    for segment in parsed.segments:
        span = segment.evidence_span
        assert source.content[span.char_start : span.char_end] == segment.body_text
        assert span.quote == segment.body_text
        assert span.verify(source)
    if source_id == "D":
        assert tuple(segment.event_time for segment in parsed.segments) == (
            datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
            datetime(2026, 10, 5, 9, 1, tzinfo=UTC),
            datetime(2026, 10, 5, 9, 2, tzinfo=UTC),
        )


def test_d_keeps_independent_messages_and_distinct_unicode_evidence_offsets() -> None:
    spec = load_spec()
    source = _artifact("D", spec.sources["D"])
    segments = parse_timestamped_text(source).segments

    assert len(segments) == 3
    assert len({segment.message_id for segment in segments}) == 3
    assert len({segment.evidence_span.evidence_ref for segment in segments}) == 3
    assert [segment.evidence_span.char_start for segment in segments] == sorted(
        segment.evidence_span.char_start for segment in segments
    )
    assert all(
        first.evidence_span.char_end < second.evidence_span.char_start
        for first, second in zip(segments, segments[1:])
    )


def test_rubric_is_loaded_separately_from_inference_payload() -> None:
    spec = load_spec()

    assert set(spec.rubric["categories"]) == {
        "CORRECT_PARTIAL",
        "WRONG_ANSWER",
        "CONTRACT_FAILURE",
        "INFRA_FAILURE",
    }
    assert "BUSINESS_COMPLETE" not in spec.rubric["categories"]
    assert spec.rubric["independent_dimensions"] is True
    assert spec.rubric["refusal_alone_is_not_semantic_pass"] is True
    assert set(spec.rubric["dimensions"]) == {
        "continuity_coverage",
        "precise_quote_dates",
        "conflict",
        "unconfirmed_assertions",
        "excess_planning_missing_investigation",
    }
    assert "rubric" not in spec.inference_payload
    assert set(spec.inference_payload) == {"question", "sources"}

    assert spec.rubric["schema_version"] == "canary-rubric/v2"
    assert spec.rubric["score_scale"] == {
        "allowed_scores": (0, 1, 2),
        "unknown_score_forbidden": True,
        "empty_score_forbidden": True,
        "anchors": spec.rubric["score_scale"]["anchors"],
    }
    assert set(spec.rubric["score_scale"]["anchors"]) == {
        "continuity_coverage",
        "precise_quote_dates",
        "conflict",
        "unconfirmed_assertions",
        "excess_planning_missing_investigation",
    }
    assert all(set(anchors) == {"0", "1", "2"} for anchors in spec.rubric["score_scale"]["anchors"].values())
    hard_fail = spec.rubric["hard_fail_rules"][0]
    assert hard_fail["applies_to"] == "ALL_CASES"
    assert hard_fail["category"] == "CONTRACT_FAILURE"
    assert hard_fail["score_overrides"] == {"unconfirmed_assertions": 0}
    assert spec.rubric["category_assignment"]["exactly_one_category"] is True
    assert spec.rubric["category_assignment"]["allowed_categories"] == tuple(spec.rubric["categories"])


def test_loaded_public_views_are_immutable() -> None:
    spec = load_spec()

    with pytest.raises(TypeError):
        spec.sources["A"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        spec.cases["C1"] = spec.cases["C2"]  # type: ignore[index]
    with pytest.raises(TypeError):
        spec.rubric["categories"] = ()  # type: ignore[index]


def test_loader_rejects_changed_historical_source_bytes() -> None:
    with tempfile.TemporaryDirectory(dir=SPEC_DIR.parents[3]) as temporary_dir:
        copied = Path(temporary_dir) / "canary-v1"
        shutil.copytree(SPEC_DIR, copied)
        source_path = copied / "sources.json"
        source_path.write_bytes(source_path.read_bytes().replace(b"2026-10-20", b"2026-10-21", 1))

        with pytest.raises(CanarySpecError, match="sources.json.*SHA256"):
            load_spec(copied)


def test_loader_rejects_changed_manifest_bytes() -> None:
    with tempfile.TemporaryDirectory(dir=SPEC_DIR.parents[3]) as temporary_dir:
        copied = Path(temporary_dir) / "canary-v2"
        shutil.copytree(SPEC_DIR, copied)
        manifest_path = copied / "freeze_manifest.json"
        manifest_path.write_bytes(manifest_path.read_bytes().replace(b'"canary-v2"', b'"canary-v2-tampered"', 1))

        with pytest.raises(CanarySpecError, match="freeze manifest SHA256"):
            load_spec(copied)


def test_loader_is_read_only_and_artifact_bytes_are_stable() -> None:
    before = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in SPEC_DIR.iterdir()
        if path.is_file()
    }

    first = load_spec()
    second = load_spec()

    after = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in SPEC_DIR.iterdir()
        if path.is_file()
    }
    assert first.digest == second.digest
    assert before == after


def test_historical_v1_freeze_bytes_remain_pinned() -> None:
    manifest_path = HISTORICAL_SPEC_DIR / "freeze_manifest.json"
    assert sha256(manifest_path.read_bytes()).hexdigest() == (
        "348726410439026f54906b4268a2d4b108a48f3088ed5c3e15658f3d94c824f4"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["spec_version"] == "canary-v1"
    for relative_path, expected_hash in manifest["files"].items():
        assert sha256((HISTORICAL_SPEC_DIR / relative_path).read_bytes()).hexdigest() == expected_hash


def test_manifest_lists_every_frozen_artifact_and_uses_sha256() -> None:
    manifest = json.loads((SPEC_DIR / "freeze_manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]

    assert set(files) == {
        "README.md",
        "sources.json",
        "cases.json",
        "rubric.json",
        "runtime_config.json",
    }
    for relative_path, expected_hash in files.items():
        actual_hash = sha256((SPEC_DIR / relative_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash
        assert len(expected_hash) == 64
